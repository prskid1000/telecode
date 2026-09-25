"""P5 telemetry HTTP surface.

OTLP/HTTP receiver (what the CLIs export to — see services/engine/otel.py)::

  POST /otlp/v1/metrics | /otlp/v1/logs | /otlp/v1/traces
       Content-Type application/json (OTLP/JSON) or application/x-protobuf;
       Content-Encoding gzip accepted. **Loopback peers only** (403 otherwise),
       whatever address the proxy is bound to. Answers an empty
       ``Export*ServiceResponse`` in the request's encoding. With
       ``telemetry.enabled`` off the payload is accepted and dropped.

Dashboards (read-only)::

  GET /api/telemetry/status                      enabled, retention, row counts, receiver endpoint
  GET /api/telemetry/summary?group=agent|job|trigger|engine|model&since=7d&until=…
  GET /api/telemetry/runs/{run_id}/timeline      phases → steps → attempts Gantt data, verdict
  GET /api/telemetry/triggers/{trigger_id}/passk?k=5
  GET /api/telemetry/tasks/{task_id}             one engine run: its spans + OTLP events
"""

from __future__ import annotations

import asyncio
import gzip
import ipaddress
import json
import logging

from aiohttp import web

from services.task.safe_paths import validate_id
from services.telemetry import otlp, protobuf, settings, store, summary

logger = logging.getLogger("telecode.proxy.api_telemetry")
GZIP_MAGIC = bytes((0x1F, 0x8B))


def _err(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"success": False, "error": msg}, status=status)


def is_loopback(remote) -> bool:
    if not remote:
        return False
    try:
        ip = ipaddress.ip_address(str(remote).split("%", 1)[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_loopback


# ── receiver ────────────────────────────────────────────────────────────────

async def otlp_receive(request: web.Request) -> web.Response:
    signal = request.match_info["signal"]
    if signal not in otlp.SIGNALS:
        return _err("unknown OTLP signal", 404)
    if not is_loopback(request.remote):
        logger.warning("otlp: refused %s from non-loopback peer %s", signal, request.remote)
        return _err("the OTLP receiver accepts loopback clients only", 403)
    cap = settings.max_body_bytes()
    if request.content_length and request.content_length > cap:
        return _err("payload too large", 413)
    try:
        body = await request.read()
    except web.HTTPRequestEntityTooLarge:
        return _err("payload too large", 413)
    except Exception as exc:
        return _err(f"could not read body: {exc}")
    if len(body) > cap:
        return _err("payload too large", 413)
    # aiohttp usually inflates Content-Encoding: gzip itself; inflate only what still is gzip.
    if (request.headers.get("Content-Encoding") or "").lower() == "gzip" and body[:2] == GZIP_MAGIC:
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError) as exc:
            return _err(f"bad gzip body: {exc}")
        if len(body) > cap * 4:
            return _err("payload too large", 413)
    ctype = (request.content_type or "").lower()
    is_pb = "protobuf" in ctype
    if not is_pb and "json" not in ctype:
        return _err("Content-Type must be application/json or application/x-protobuf", 415)
    try:
        payload = protobuf.decode_request(signal, body) if is_pb else json.loads(body.decode("utf-8") or "{}")
    except (protobuf.DecodeError, ValueError, UnicodeDecodeError) as exc:
        return _err(f"bad OTLP payload: {exc}")
    if settings.enabled():
        try:
            res = await asyncio.to_thread(otlp.ingest, signal, payload)
            logger.debug("otlp %s: %d rows", signal, res["rows"])
        except ValueError as exc:
            return _err(str(exc))
        except Exception:
            logger.exception("otlp ingest failed")
            return _err("ingest failed", 500)
    if is_pb:
        return web.Response(body=b"", content_type="application/x-protobuf")
    return web.json_response({"partialSuccess": {}})


# ── dashboards ──────────────────────────────────────────────────────────────

async def status(request: web.Request) -> web.Response:
    counts = await asyncio.to_thread(store.counts)
    return web.json_response({"success": True, "enabled": settings.enabled(),
                              "retention_days": settings.retention_days(),
                              "receiver": settings.receiver_endpoint(), "counts": counts,
                              "approval_timeout_sec": settings.approval_timeout_sec(),
                              "mcp_enabled": settings.mcp_enabled()})


async def get_summary(request: web.Request) -> web.Response:
    q = request.query
    try:
        data = await asyncio.to_thread(summary.summary, q.get("group") or "agent", q.get("since") or None,
                                       q.get("until") or None, int(q.get("limit") or 25))
    except ValueError as exc:
        return _err(str(exc))
    return web.json_response({"success": True, **data})


async def run_timeline(request: web.Request) -> web.Response:
    rid = request.match_info["run_id"]
    try:
        validate_id(rid, "run_id")
    except ValueError as exc:
        return _err(str(exc))
    data = await asyncio.to_thread(summary.timeline, rid)
    if data is None:
        return _err("run not found", 404)
    return web.json_response({"success": True, "timeline": data})


async def trigger_passk(request: web.Request) -> web.Response:
    tid = request.match_info["trigger_id"]
    try:
        validate_id(tid, "trigger_id")
        k = int(request.query.get("k") or 5)
    except ValueError as exc:
        return _err(str(exc))
    data = await asyncio.to_thread(summary.trigger_passk, tid, k)
    return web.json_response({"success": True, **data})


def _task_telemetry(task_id: str):
    from services.db.core import connect
    c = connect()
    spans = [dict(r) for r in c.execute(
        "SELECT name, operation, source, start_ms, end_ms, duration_ms, status, status_message, tool_name, model, "
        "engine, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd FROM spans "
        "WHERE task_id=? ORDER BY start_ms LIMIT 2000", (task_id,))]
    events = [dict(r) for r in c.execute(
        "SELECT ts_ms, name, model, tool_name, success, duration_ms, cost_usd, input_tokens, output_tokens, "
        "cache_read_tokens, cache_write_tokens FROM log_events WHERE task_id=? ORDER BY ts_ms LIMIT 2000",
        (task_id,))]
    metrics = [dict(r) for r in c.execute(
        "SELECT name, type, model, SUM(value) AS value FROM metric_points WHERE task_id=? "
        "GROUP BY name, type, model ORDER BY name", (task_id,))]
    return {"task_id": task_id, "spans": spans, "log_events": events, "metrics": metrics,
            "otlp_cost_usd": round(sum(e["cost_usd"] or 0 for e in events if (e["name"] or "").endswith("api_request")), 6)}


async def task_telemetry(request: web.Request) -> web.Response:
    tid = request.match_info["task_id"]
    try:
        validate_id(tid, "task_id")
    except ValueError as exc:
        return _err(str(exc))
    return web.json_response({"success": True, **(await asyncio.to_thread(_task_telemetry, tid))})


def register_routes(app: web.Application) -> None:
    app.router.add_post("/otlp/v1/{signal}", otlp_receive)
    app.router.add_get("/api/telemetry/status", status)
    app.router.add_get("/api/telemetry/summary", get_summary)
    app.router.add_get("/api/telemetry/runs/{run_id}/timeline", run_timeline)
    app.router.add_get("/api/telemetry/triggers/{trigger_id}/passk", trigger_passk)
    app.router.add_get("/api/telemetry/tasks/{task_id}", task_telemetry)
