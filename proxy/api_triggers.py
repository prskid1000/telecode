"""AIOHTTP routes for Triggers (the one scheduler) — replaces /api/routines.

| Method | Path                                   | Purpose                                         |
|--------|----------------------------------------|-------------------------------------------------|
| GET    | /api/triggers                          | list (?target_kind, ?target_id, ?agent_id, ?status, ?source) — secrets masked |
| POST   | /api/triggers                          | create                                          |
| POST   | /api/triggers/preview                  | {schedule, active_hours} → next fire times       |
| GET    | /api/triggers/{id}                     | one trigger (webhook token / GitHub secret shown) |
| PATCH  | /api/triggers/{id}                     | edit (HEARTBEAT.md triggers: status only)       |
| DELETE | /api/triggers/{id}                     | delete (?delete_session=true)                   |
| POST   | /api/triggers/{id}/pause, /resume      | pause / resume                                  |
| POST   | /api/triggers/{id}/run-now             | fire now (manual)                               |
| POST   | /api/triggers/{id}/token               | rotate the webhook token                        |
| GET    | /api/triggers/{id}/fires               | history (?limit)                                |
| POST   | /api/triggers/{id}/fire                | **webhook** — ``Authorization: Bearer <token>``  |
| POST   | /api/triggers/{id}/github              | **GitHub webhook** — ``X-Hub-Signature-256``      |

The two webhook routes are the only ones meant to be reachable from outside;
both authenticate per trigger and answer 401 for any auth problem (including
an unknown trigger), so they do not reveal which ids exist.
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from services.task.safe_paths import validate_id
from services.triggers import service, store, webhook
from services.triggers import schedule as sched

logger = logging.getLogger("telecode.proxy.api_triggers")


def _err(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"success": False, "error": msg}, status=status)


def _tid(request: web.Request):
    tid = request.match_info["trigger_id"]
    try:
        validate_id(tid, "trigger_id")
    except ValueError as exc:
        return None, _err(str(exc))
    return tid, None


async def _json(request: web.Request) -> dict:
    try:
        b = await request.json()
    except Exception:
        b = {}
    return b if isinstance(b, dict) else {}


async def _call(fn, *a, **kw):
    """Run a service call off the event loop; map errors to HTTP."""
    try:
        return await asyncio.to_thread(fn, *a, **kw), None
    except service.NotFound as exc:
        return None, _err(str(exc), 404)
    except LookupError as exc:
        return None, _err(str(exc), 404)
    except ValueError as exc:
        return None, _err(str(exc), 400)


async def list_triggers(request: web.Request) -> web.Response:
    q = request.query
    out, err = await _call(service.list_triggers, target_kind=q.get("target_kind"), target_id=q.get("target_id"),
                           agent_id=q.get("agent_id"), status=q.get("status"), source=q.get("source"))
    if err is not None:
        return err
    import config
    return web.json_response({"success": True, "triggers": out, "heartbeat_enabled": bool(config.heartbeat_enabled())})


async def create_trigger(request: web.Request) -> web.Response:
    out, err = await _call(service.create, await _json(request))
    return err if err is not None else web.json_response({"success": True, "trigger": out})


async def preview(request: web.Request) -> web.Response:
    body = await _json(request)
    try:
        s = sched.normalize_schedule(body.get("schedule"))
        ah = sched.normalize_active_hours(body.get("active_hours"))
    except ValueError as exc:
        return _err(str(exc))
    return web.json_response({"success": True, "schedule": s, "active_hours": ah,
                              "upcoming": sched.upcoming(s, int(body.get("count") or 5), active_hours=ah)})


async def get_trigger(request: web.Request) -> web.Response:
    tid, bad = _tid(request)
    if bad is not None:
        return bad
    out, err = await _call(service.get, tid)
    if err is not None:
        return err
    fires, _ = await _call(store.list_fires, tid, 10)
    return web.json_response({"success": True, "trigger": out, "recent_fires": fires or []})


async def patch_trigger(request: web.Request) -> web.Response:
    tid, bad = _tid(request)
    if bad is not None:
        return bad
    out, err = await _call(service.patch, tid, await _json(request))
    return err if err is not None else web.json_response({"success": True, "trigger": out})


async def delete_trigger(request: web.Request) -> web.Response:
    tid, bad = _tid(request)
    if bad is not None:
        return bad
    drop = (request.query.get("delete_session") or "").lower() in ("1", "true", "yes")
    ok, err = await _call(service.delete, tid, drop)
    if err is not None:
        return err
    if not ok:
        return _err("trigger not found", 404)
    return web.json_response({"success": True, "deleted_session": drop})


def _status_route(status: str):
    async def handler(request: web.Request) -> web.Response:
        tid, bad = _tid(request)
        if bad is not None:
            return bad
        out, err = await _call(service.set_status, tid, status)
        return err if err is not None else web.json_response({"success": True, "trigger": out})
    return handler


async def run_now(request: web.Request) -> web.Response:
    tid, bad = _tid(request)
    if bad is not None:
        return bad
    out, err = await _call(service.run_now, tid)
    return err if err is not None else web.json_response({"success": True, **out})


async def rotate_token(request: web.Request) -> web.Response:
    tid, bad = _tid(request)
    if bad is not None:
        return bad
    out, err = await _call(service.rotate_token, tid)
    return err if err is not None else web.json_response({"success": True, "trigger": out})


async def list_fires(request: web.Request) -> web.Response:
    tid, bad = _tid(request)
    if bad is not None:
        return bad
    try:
        limit = max(1, min(500, int(request.query.get("limit", "100"))))
    except ValueError:
        limit = 100
    out, err = await _call(service.fires, tid, limit)
    return err if err is not None else web.json_response({"success": True, "fires": out})


# ── webhooks (authenticated per trigger) ────────────────────────────────────

_UNAUTH = {"success": False, "error": "unauthorized"}


async def _read_body(request: web.Request):
    """The whole body, or None past MAX_BODY (StreamReader.read(n) returns *up to*
    n bytes, so read in a loop). An oversized body is drained (bounded) so the
    client gets the 413 instead of a connection reset."""
    buf = bytearray()
    while True:
        chunk = await request.content.read(64 * 1024)
        if not chunk:
            return bytes(buf)
        buf += chunk
        if len(buf) > webhook.MAX_BODY:
            drained = 0
            while drained < 16 * 1024 * 1024:
                more = await request.content.read(64 * 1024)
                if not more:
                    break
                drained += len(more)
            return None


def _fire_response(res: dict) -> web.Response:
    status = res.get("status")
    if status == "fired":
        return web.json_response({"success": True, **res}, status=202)
    code = 423 if "trigger is" in (res.get("reason") or "") else 409
    return web.json_response({"success": False, **res}, status=code)


async def webhook_fire(request: web.Request) -> web.Response:
    tid, bad = _tid(request)
    if bad is not None:
        return bad
    raw = await _read_body(request)
    if raw is None:
        return _err(f"payload larger than {webhook.MAX_BODY} bytes", 413)
    rec = await asyncio.to_thread(store.get, tid)
    if rec is None or not webhook.check_bearer(rec, request.headers.get("Authorization")):
        return web.json_response(_UNAUTH, status=401)
    payload = webhook.parse_body(raw, request.headers.get("Content-Type", "")) if raw else None
    from services.triggers import fire as fire_mod
    res = await asyncio.to_thread(fire_mod.fire, tid, source="webhook", payload=payload if payload != "" else None)
    return _fire_response(res)


async def github_fire(request: web.Request) -> web.Response:
    tid, bad = _tid(request)
    if bad is not None:
        return bad
    raw = await _read_body(request)
    if raw is None:
        return _err(f"payload larger than {webhook.MAX_BODY} bytes", 413)
    rec = await asyncio.to_thread(store.get, tid)
    if rec is None or not webhook.check_github_signature(rec, raw, request.headers.get("X-Hub-Signature-256")):
        return web.json_response(_UNAUTH, status=401)
    event = request.headers.get("X-GitHub-Event", "")
    if event == "ping":
        return web.json_response({"success": True, "pong": True})
    payload = webhook.parse_body(raw, "application/json")
    ok, why = webhook.github_filter(rec, event, payload)
    if not ok:
        return web.json_response({"success": True, "ignored": why})
    from services.triggers import fire as fire_mod
    res = await asyncio.to_thread(fire_mod.fire, tid, source="github",
                                  payload=webhook.github_payload(event, payload,
                                                                 request.headers.get("X-GitHub-Delivery")))
    return _fire_response(res)


def register_routes(app: web.Application) -> None:
    app.router.add_get("/api/triggers", list_triggers)
    app.router.add_post("/api/triggers", create_trigger)
    app.router.add_post("/api/triggers/preview", preview)
    app.router.add_get("/api/triggers/{trigger_id}", get_trigger)
    app.router.add_patch("/api/triggers/{trigger_id}", patch_trigger)
    app.router.add_delete("/api/triggers/{trigger_id}", delete_trigger)
    app.router.add_post("/api/triggers/{trigger_id}/pause", _status_route("paused"))
    app.router.add_post("/api/triggers/{trigger_id}/resume", _status_route("active"))
    app.router.add_post("/api/triggers/{trigger_id}/run-now", run_now)
    app.router.add_post("/api/triggers/{trigger_id}/token", rotate_token)
    app.router.add_get("/api/triggers/{trigger_id}/fires", list_fires)
    app.router.add_post("/api/triggers/{trigger_id}/fire", webhook_fire)
    app.router.add_post("/api/triggers/{trigger_id}/github", github_fire)
