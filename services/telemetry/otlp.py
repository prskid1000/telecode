"""OTLP → telemetry rows (both encodings share this: protobuf is decoded into
the OTLP/JSON shape first, see :mod:`services.telemetry.protobuf`).

``ingest(signal, payload)`` normalises one export request and writes it:

* metrics → one ``metric_points`` row per data point (sum / gauge value, a
  histogram's sum). Claude Code's ``claude_code.token.usage`` carries its
  token kind in the ``type`` attribute, ``claude_code.cost.usage`` is USD.
* logs → one ``log_events`` row per record; the event name comes from
  ``eventName`` / a dotted body (``claude_code.api_request``) / ``event.name``.
  Cost, tokens, duration, model, tool name and success are lifted into typed
  columns from Claude's (``cost_usd``, ``input_tokens``, ``cache_read_tokens``,
  ``cache_creation_tokens``…) and Codex's (``input_token_count``,
  ``cached_token_count``…) attribute names.
* traces → ``spans`` rows with ``source='otlp'``.

Account identity attributes (``user.email``, ``user.account_uuid``,
``organization.id``…) are dropped at ingest.

Correlation: the ``telecode.*`` resource attributes the Engine Runner puts in
``OTEL_RESOURCE_ATTRIBUTES`` (task / run / step / agent / trigger / job ids)
become columns; ``session.id`` / ``conversation.id`` become ``session_id``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from services.telemetry import store

logger = logging.getLogger("telecode.services.telemetry.otlp")

SIGNALS = ("metrics", "logs", "traces")
ID_KEYS = ("task_id", "run_id", "step_id", "agent_id", "trigger_id", "job_id")
_TEMPORALITY = {0: None, 1: "delta", 2: "cumulative",
                "AGGREGATION_TEMPORALITY_DELTA": "delta", "AGGREGATION_TEMPORALITY_CUMULATIVE": "cumulative"}
_SPAN_KIND = {0: None, 1: "internal", 2: "server", 3: "client", 4: "producer", 5: "consumer"}
_SEVERITY = {1: "TRACE", 5: "DEBUG", 9: "INFO", 13: "WARN", 17: "ERROR", 21: "FATAL"}
_ATTR_CAP = 4096
# Account identity Claude Code attaches to every record — not needed to attribute cost
# to a run (the telecode.* ids do that), so it is never stored.
PII_KEYS = frozenset({"user.email", "user.id", "user.account_id", "user.account_uuid", "organization.id"})


def any_value(v: Any) -> Any:
    if not isinstance(v, dict):
        return v
    if "stringValue" in v:
        return v["stringValue"]
    if "boolValue" in v:
        return bool(v["boolValue"])
    if "intValue" in v:
        try:
            return int(v["intValue"])
        except (TypeError, ValueError):
            return v["intValue"]
    if "doubleValue" in v:
        try:
            return float(v["doubleValue"])
        except (TypeError, ValueError):
            return v["doubleValue"]
    if "arrayValue" in v:
        return [any_value(x) for x in (v["arrayValue"] or {}).get("values") or []]
    if "kvlistValue" in v:
        return attrs((v["kvlistValue"] or {}).get("values"))
    if "bytesValue" in v:
        return v["bytesValue"]
    return None


def attrs(kvs: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for kv in kvs or []:
        if isinstance(kv, dict) and kv.get("key") is not None:
            if kv["key"] in PII_KEYS:
                continue
            val = any_value(kv.get("value"))
            if isinstance(val, str) and len(val) > _ATTR_CAP:
                val = val[:_ATTR_CAP] + "…"
            out[str(kv["key"])] = val
    return out


def _ms(nano: Any) -> Optional[int]:
    try:
        n = int(nano)
    except (TypeError, ValueError):
        return None
    return n // 1_000_000 if n else None


def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> Optional[int]:
    f = _num(v)
    return int(f) if f is not None else None


def _bool(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, str) and v.lower() in ("true", "false"):
        return int(v.lower() == "true")
    if isinstance(v, (int, float)):
        return int(bool(v))
    return None


def correlation(res: Dict[str, Any], a: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``telecode.*`` ids (resource first, point/record attributes as fallback),
    the CLI session id and the model."""
    a = a or {}
    out: Dict[str, Any] = {}
    for k in ID_KEYS:
        v = res.get(f"telecode.{k}") or a.get(f"telecode.{k}")
        out[k] = str(v) if v not in (None, "") else None
    sid = a.get("session.id") or res.get("session.id") or a.get("conversation.id") or res.get("conversation.id")
    out["session_id"] = str(sid) if sid else None
    model = a.get("model") or a.get("gen_ai.request.model") or a.get("gen_ai.response.model") or res.get("model")
    out["model"] = str(model) if model else None
    return out


def _resource_blocks(payload: Dict[str, Any], key: str, scope_key: str, items_key: str):
    for rb in payload.get(key) or []:
        if not isinstance(rb, dict):
            continue
        res = attrs((rb.get("resource") or {}).get("attributes"))
        for sb in rb.get(scope_key) or []:
            if not isinstance(sb, dict):
                continue
            scope = (sb.get("scope") or {}).get("name")
            for item in sb.get(items_key) or []:
                if isinstance(item, dict):
                    yield res, scope, item


def metric_rows(payload: Dict[str, Any], received_ms: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for res, _scope, m in _resource_blocks(payload, "resourceMetrics", "scopeMetrics", "metrics"):
        name = m.get("name")
        for kind in ("sum", "gauge", "histogram"):
            body = m.get(kind)
            if not isinstance(body, dict):
                continue
            temp = _TEMPORALITY.get(body.get("aggregationTemporality"))
            for dp in body.get("dataPoints") or []:
                if not isinstance(dp, dict):
                    continue
                a = attrs(dp.get("attributes"))
                if kind == "histogram":
                    value = _num(dp.get("sum"))
                else:
                    value = _num(dp.get("asDouble")) if dp.get("asDouble") is not None else _num(dp.get("asInt"))
                c = correlation(res, a)
                rows.append({"ts_ms": _ms(dp.get("timeUnixNano")) or received_ms, "name": name, "value": value,
                             "unit": m.get("unit"), "kind": kind, "temporality": temp,
                             "type": a.get("type"), "attributes": a, "resource": res,
                             "received_ms": received_ms, **c})
    return rows


def _event_name(rec: Dict[str, Any], a: Dict[str, Any]) -> Optional[str]:
    if rec.get("eventName"):
        return str(rec["eventName"])
    body = any_value(rec.get("body"))
    if isinstance(body, str) and "." in body and " " not in body and len(body) < 120:
        return body
    ev = a.get("event.name")
    return str(ev) if ev else (body if isinstance(body, str) and len(body) < 120 else None)


def log_rows(payload: Dict[str, Any], received_ms: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for res, _scope, rec in _resource_blocks(payload, "resourceLogs", "scopeLogs", "logRecords"):
        a = attrs(rec.get("attributes"))
        body = any_value(rec.get("body"))
        c = correlation(res, a)
        ts = _ms(rec.get("timeUnixNano")) or _ms(rec.get("observedTimeUnixNano")) or received_ms
        sev = rec.get("severityText") or _SEVERITY.get(rec.get("severityNumber"))
        rows.append({
            "ts_ms": ts, "name": _event_name(rec, a), "severity": sev,
            "body": None if body is None else (body if isinstance(body, str) else str(body))[:_ATTR_CAP],
            "tool_name": a.get("tool_name") or a.get("tool.name") or a.get("gen_ai.tool.name"),
            "success": _bool(a.get("success")),
            "duration_ms": _int(a.get("duration_ms")),
            "cost_usd": _num(a.get("cost_usd")),
            "input_tokens": _int(a.get("input_tokens") if a.get("input_tokens") is not None else a.get("input_token_count")),
            "output_tokens": _int(a.get("output_tokens") if a.get("output_tokens") is not None else a.get("output_token_count")),
            "cache_read_tokens": _int(a.get("cache_read_tokens") if a.get("cache_read_tokens") is not None
                                      else a.get("cached_token_count")),
            "cache_write_tokens": _int(a.get("cache_creation_tokens")),
            "attributes": a, "resource": res, "received_ms": received_ms, **c,
        })
    return rows


def span_rows(payload: Dict[str, Any], received_ms: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for res, _scope, sp in _resource_blocks(payload, "resourceSpans", "scopeSpans", "spans"):
        if not sp.get("traceId") or not sp.get("spanId"):
            continue
        a = attrs(sp.get("attributes"))
        c = correlation(res, a)
        start, end = _ms(sp.get("startTimeUnixNano")), _ms(sp.get("endTimeUnixNano"))
        st = sp.get("status") or {}
        code = st.get("code")
        status = "error" if code in (2, "STATUS_CODE_ERROR") else "ok" if code in (1, "STATUS_CODE_OK") else "unset"
        rows.append({
            "trace_id": str(sp["traceId"]).lower(), "span_id": str(sp["spanId"]).lower(),
            "parent_span_id": (str(sp.get("parentSpanId")).lower() or None) if sp.get("parentSpanId") else None,
            "name": sp.get("name"), "operation": a.get("gen_ai.operation.name"),
            "kind": _SPAN_KIND.get(sp.get("kind")) if isinstance(sp.get("kind"), int) else sp.get("kind"),
            "source": "otlp", "start_ms": start or received_ms, "end_ms": end,
            "duration_ms": (end - start) if (start and end) else None, "status": status,
            "status_message": st.get("message"), "engine": None,
            "tool_name": a.get("gen_ai.tool.name") or a.get("tool_name"),
            "input_tokens": _int(a.get("gen_ai.usage.input_tokens")),
            "output_tokens": _int(a.get("gen_ai.usage.output_tokens")),
            "cache_read_tokens": _int(a.get("gen_ai.usage.cache_read.input_tokens")),
            "cache_write_tokens": _int(a.get("gen_ai.usage.cache_creation.input_tokens")),
            "cost_usd": _num(a.get("cost_usd")),
            "attributes": a, "resource": res, "received_ms": received_ms,
            **{k: v for k, v in c.items() if k != "session_id"},
        })
    return rows


def normalise(signal: str, payload: Dict[str, Any], received_ms: Optional[int] = None) -> Tuple[str, List[Dict[str, Any]]]:
    if signal not in SIGNALS:
        raise ValueError(f"signal must be one of {SIGNALS}")
    if not isinstance(payload, dict):
        raise ValueError("OTLP payload must be a JSON object")
    now = received_ms or store.now_ms()
    if signal == "metrics":
        return "metric_points", metric_rows(payload, now)
    if signal == "logs":
        return "log_events", log_rows(payload, now)
    return "spans", span_rows(payload, now)


def ingest(signal: str, payload: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Normalise + write one export request; returns {table, rows}."""
    table, rows = normalise(signal, payload)
    writer = {"metric_points": store.insert_points, "log_events": store.insert_logs,
              "spans": store.insert_spans}[table]
    n = writer(rows, conn=conn)
    try:
        store.prune(conn=conn)
    except Exception:
        logger.exception("telemetry prune failed")
    return {"table": table, "rows": n}
