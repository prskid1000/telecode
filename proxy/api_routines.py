"""AIOHTTP routes for recurring CLAUDE_CODE / GEMINI routines.

A routine is a saved recipe (prompt + interval + permanent session)
that the routine manager's heartbeat fires until the user cancels.

Endpoints (all under ``/api/routines``):

| Method  | Path                       | Purpose                                  |
|---------|----------------------------|------------------------------------------|
| GET     | ``/``                      | List routines (?status, ?namespace)      |
| POST    | ``/``                      | Create routine + bound session           |
| GET     | ``/<id>``                  | One routine                              |
| PATCH   | ``/<id>``                  | Update prompt/schedule/options           |
| DELETE  | ``/<id>``                  | Cancel + remove (?delete_session=true)   |
| POST    | ``/<id>/pause``            | Pause scheduling                         |
| POST    | ``/<id>/resume``           | Resume scheduling                        |
| POST    | ``/<id>/run-now``          | Fire one tick immediately                |
| GET     | ``/<id>/runs``             | History of tasks fired by this routine   |
"""

from __future__ import annotations

import logging
from aiohttp import web

from services.routine import routine_service

from proxy import request_log

logger = logging.getLogger("telecode.proxy.api_routines")


def _log_req(request: web.Request):
    return request_log.new_request(request.method, request.path, inbound_protocol="routine-api")


def _truthy(v):
    return (v or "").lower() in ("1", "true", "yes", "on")


async def list_routines(request: web.Request) -> web.Response:
    rid = _log_req(request)
    status = request.query.get("status") or None
    namespace = request.query.get("namespace") or None
    try:
        routines = routine_service.list_routines(
            status=status, session_namespace=namespace
        )
        out = {"success": True, "routines": routines}
        request_log.set_response_preview(rid, out)
        request_log.finish(rid, 200)
        return web.json_response(out)
    except Exception as exc:
        request_log.finish(rid, 400, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=400)


async def create_routine(request: web.Request) -> web.Response:
    rid = _log_req(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    request_log.set_request_preview(rid, body)
    try:
        rec = routine_service.create_routine(body or {})
        out = {"success": True, "routine": rec}
        request_log.set_response_preview(rid, out)
        request_log.finish(rid, 200)
        return web.json_response(out)
    except (ValueError, FileExistsError) as exc:
        request_log.finish(rid, 400, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=400)


async def get_routine(request: web.Request) -> web.Response:
    rid = _log_req(request)
    routine_id = request.match_info["routine_id"]
    try:
        rec = routine_service.get_routine(routine_id)
    except ValueError as exc:
        request_log.finish(rid, 400, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=400)
    if not rec:
        request_log.finish(rid, 404, "Routine not found")
        return web.json_response({"success": False, "error": "Routine not found"}, status=404)
    runs = routine_service.list_runs(routine_id, limit=5)
    out = {"success": True, "routine": rec, "recent_runs": runs}
    request_log.set_response_preview(rid, out)
    request_log.finish(rid, 200)
    return web.json_response(out)


async def patch_routine(request: web.Request) -> web.Response:
    rid = _log_req(request)
    routine_id = request.match_info["routine_id"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    request_log.set_request_preview(rid, body)
    try:
        rec = routine_service.patch_routine(routine_id, body or {})
    except ValueError as exc:
        request_log.finish(rid, 400, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=400)
    if not rec:
        request_log.finish(rid, 404, "Routine not found")
        return web.json_response({"success": False, "error": "Routine not found"}, status=404)
    out = {"success": True, "routine": rec}
    request_log.set_response_preview(rid, out)
    request_log.finish(rid, 200)
    return web.json_response(out)


async def delete_routine(request: web.Request) -> web.Response:
    rid = _log_req(request)
    routine_id = request.match_info["routine_id"]
    delete_session = _truthy(request.query.get("delete_session"))
    try:
        removed = routine_service.delete_routine(
            routine_id, delete_session=delete_session
        )
    except ValueError as exc:
        request_log.finish(rid, 400, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=400)
    if not removed:
        request_log.finish(rid, 404, "Routine not found")
        return web.json_response({"success": False, "error": "Routine not found"}, status=404)
    out = {"success": True, "deleted_session": delete_session}
    request_log.set_response_preview(rid, out)
    request_log.finish(rid, 200)
    return web.json_response(out)


async def pause_routine(request: web.Request) -> web.Response:
    rid = _log_req(request)
    routine_id = request.match_info["routine_id"]
    rec = routine_service.pause_routine(routine_id)
    if not rec:
        request_log.finish(rid, 404, "Routine not found")
        return web.json_response({"success": False, "error": "Routine not found"}, status=404)
    out = {"success": True, "routine": rec}
    request_log.set_response_preview(rid, out)
    request_log.finish(rid, 200)
    return web.json_response(out)


async def resume_routine(request: web.Request) -> web.Response:
    rid = _log_req(request)
    routine_id = request.match_info["routine_id"]
    try:
        rec = routine_service.resume_routine(routine_id)
    except ValueError as exc:
        request_log.finish(rid, 400, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=400)
    if not rec:
        request_log.finish(rid, 404, "Routine not found")
        return web.json_response({"success": False, "error": "Routine not found"}, status=404)
    out = {"success": True, "routine": rec}
    request_log.set_response_preview(rid, out)
    request_log.finish(rid, 200)
    return web.json_response(out)


async def run_now(request: web.Request) -> web.Response:
    rid = _log_req(request)
    routine_id = request.match_info["routine_id"]
    result = routine_service.run_now(routine_id)
    out = {"success": True, **result}
    request_log.set_response_preview(rid, out)
    request_log.finish(rid, 200)
    return web.json_response(out)


async def list_runs(request: web.Request) -> web.Response:
    rid = _log_req(request)
    routine_id = request.match_info["routine_id"]
    try:
        limit = int(request.query.get("limit", "50"))
    except ValueError:
        limit = 50
    runs = routine_service.list_runs(routine_id, limit=max(1, min(limit, 500)))
    out = {"success": True, "runs": runs}
    request_log.set_response_preview(rid, out)
    request_log.finish(rid, 200)
    return web.json_response(out)


def register_routes(app: web.Application) -> None:
    app.router.add_get("/api/routines", list_routines)
    app.router.add_post("/api/routines", create_routine)
    app.router.add_get("/api/routines/{routine_id}", get_routine)
    app.router.add_patch("/api/routines/{routine_id}", patch_routine)
    app.router.add_delete("/api/routines/{routine_id}", delete_routine)
    app.router.add_post("/api/routines/{routine_id}/pause", pause_routine)
    app.router.add_post("/api/routines/{routine_id}/resume", resume_routine)
    app.router.add_post("/api/routines/{routine_id}/run-now", run_now)
    app.router.add_get("/api/routines/{routine_id}/runs", list_runs)
