"""OpenTelemetry wiring for the CLIs the Engine Runner spawns (P5).

Every export goes to telecode's own receiver on loopback
(``http://127.0.0.1:<proxy.port>/otlp``) — never off-box. The child env is
scrubbed of any inherited ``OTEL_*`` exporter/endpoint/header settings first,
so a stray global ``OTEL_EXPORTER_OTLP_ENDPOINT`` can't redirect it.

Claude Code (``monitoring-usage`` docs; verified on 2.1.282)::

    CLAUDE_CODE_ENABLE_TELEMETRY=1  OTEL_METRICS_EXPORTER=otlp  OTEL_LOGS_EXPORTER=otlp
    OTEL_EXPORTER_OTLP_PROTOCOL=http/json  OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:<p>/otlp
    OTEL_RESOURCE_ATTRIBUTES=telecode.task_id=…,telecode.run_id=…,telecode.step_id=…,…
    OTEL_METRIC_EXPORT_INTERVAL / OTEL_LOGS_EXPORT_INTERVAL (short, so short runs export)
    OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta (points sum cleanly)

(the SDK appends ``/v1/metrics`` / ``/v1/logs`` / ``/v1/traces`` to the base
endpoint). ``telemetry.cli_traces`` adds the beta traces
(``CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`` + ``OTEL_TRACES_EXPORTER=otlp``).

Codex (``[otel]`` in config.toml) — set with ``-c`` dotted keys, unquoted
values (``-c`` falls back to the literal string, so no quote characters reach
argv)::

    -c otel.environment=telecode -c otel.log_user_prompt=false
    -c otel.exporter.otlp-http.endpoint=http://127.0.0.1:<p>/otlp/v1/logs
    -c otel.exporter.otlp-http.protocol=json
    (same for otel.metrics_exporter → /v1/metrics; otel.trace_exporter → /v1/traces
    only with telemetry.cli_traces, else otel.trace_exporter=none)

(verified on codex-cli 0.157 with ``codex -c … mcp list``, which loads and
validates the config without a model call: bad variants are rejected by name.)

plus ``OTEL_RESOURCE_ATTRIBUTES`` in the env — **honoured** (verified with a
real 0.157 ``codex exec`` through the task handler, 2026-09-25): every
``resourceLogs`` / ``resourceSpans`` block carried ``service.name=codex_exec``
and all the ``telecode.*`` ids, so no other correlation is needed (the records
also carry ``conversation.id``). Log events seen: ``codex.conversation_starts``
(with ``approval_policy`` / ``sandbox_policy`` / ``model``),
``codex.user_prompt`` (prompt ``[REDACTED]``), ``codex.api_request``,
``codex.startup_phase``. Records also carry ``user.email`` /
``user.account_id``, dropped at ingest (``otlp.PII_KEYS``).
Antigravity has no OTel export — own spans only.
"""

from __future__ import annotations

import os
from typing import Dict, List, Mapping, Optional
from urllib.parse import quote

CORRELATION_KEYS = ("task_id", "run_id", "step_id", "agent_id", "job_id", "trigger_id", "attempt", "source",
                    "workspace_id")

# Inherited settings that could send telemetry somewhere else (or change its shape).
_SCRUB_PREFIXES = ("OTEL_EXPORTER_", "OTEL_TRACES_", "OTEL_METRICS_", "OTEL_LOGS_", "OTEL_METRIC_", "OTEL_BSP_",
                   "OTEL_BLRP_")
_SCRUB_KEYS = ("OTEL_RESOURCE_ATTRIBUTES", "OTEL_SERVICE_NAME", "OTEL_SDK_DISABLED", "CLAUDE_CODE_ENABLE_TELEMETRY",
               "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA", "OTEL_LOG_USER_PROMPTS", "OTEL_LOG_TOOL_DETAILS",
               "OTEL_LOG_TOOL_CONTENT")


def active(correlation: Optional[Mapping[str, object]]) -> bool:
    if correlation is None:
        return False
    try:
        from services.telemetry import settings
        return settings.enabled()
    except Exception:
        return False


def resource_attributes(correlation: Mapping[str, object]) -> str:
    """``telecode.<key>=<value>`` pairs, percent-encoded (the W3C baggage rules
    OTEL_RESOURCE_ATTRIBUTES uses: ``,`` ``=`` and spaces must be escaped)."""
    parts = ["service.namespace=telecode"]
    for k in CORRELATION_KEYS:
        v = correlation.get(k)
        if v in (None, ""):
            continue
        parts.append(f"telecode.{k}={quote(str(v), safe='-_.:~')}")
    return ",".join(parts)


def scrub(env: Mapping[str, str]) -> Dict[str, str]:
    out = {}
    for k, v in env.items():
        ku = k.upper()
        if ku in _SCRUB_KEYS or ku.startswith(_SCRUB_PREFIXES):
            continue
        out[k] = v
    return out


def claude_env(correlation: Mapping[str, object]) -> Dict[str, str]:
    from services.telemetry import settings
    env = {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_EXPORTER_OTLP_ENDPOINT": settings.receiver_endpoint(),
        "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "delta",
        "OTEL_METRIC_EXPORT_INTERVAL": str(settings.metric_interval_ms()),
        "OTEL_LOGS_EXPORT_INTERVAL": str(settings.logs_interval_ms()),
        "OTEL_RESOURCE_ATTRIBUTES": resource_attributes(correlation),
    }
    if settings.cli_traces():
        env["CLAUDE_CODE_ENHANCED_TELEMETRY_BETA"] = "1"
        env["OTEL_TRACES_EXPORTER"] = "otlp"
    else:
        env["OTEL_TRACES_EXPORTER"] = "none"
    return env


def codex_overrides() -> List[str]:
    """``-c otel.*`` for Codex. Logs and metrics always go to the receiver
    (metrics_exporter's variants include ``statsig`` — pointing it here keeps
    it on the box). Traces only with ``telemetry.cli_traces``: a real 0.157 run
    exported ~2,000 internal spans (``fs.get_metadata`` ×1,433, ``auth``,
    ``append_items``…) ≈ 4.7 MB of OTLP/JSON for one tiny prompt, so otherwise
    ``otel.trace_exporter=none`` (a valid variant: none|statsig|otlp-http|otlp-grpc)."""
    from services.telemetry import settings
    base = settings.receiver_endpoint()
    out = ["-c", "otel.environment=telecode", "-c", "otel.log_user_prompt=false"]
    signals = [("exporter", "logs"), ("metrics_exporter", "metrics")]
    if settings.cli_traces():
        signals.append(("trace_exporter", "traces"))
    for key, path in signals:
        out += ["-c", f"otel.{key}.otlp-http.endpoint={base}/v1/{path}",
                "-c", f"otel.{key}.otlp-http.protocol=json"]
    if not settings.cli_traces():
        out += ["-c", "otel.trace_exporter=none"]
    return out


def codex_env(correlation: Mapping[str, object]) -> Dict[str, str]:
    return {"OTEL_RESOURCE_ATTRIBUTES": resource_attributes(correlation)}


def with_env(base: Optional[Mapping[str, str]], extra: Mapping[str, str]) -> Dict[str, str]:
    """``base`` (default os.environ) scrubbed of inherited OTel settings, plus ``extra``."""
    return {**scrub(os.environ if base is None else base), **extra}
