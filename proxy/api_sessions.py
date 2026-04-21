"""AIOHTTP routes for task sessions. Mimics pythonmagic API."""

from __future__ import annotations

import json
import logging
import mimetypes
from aiohttp import web
from pathlib import Path

from services.session import session_store

from proxy import request_log

logger = logging.getLogger("telecode.proxy.api_sessions")

def _log_req(request: web.Request):
    return request_log.new_request(request.method, request.path, inbound_protocol="session-api")

async def list_sessions(request: web.Request) -> web.Response:
    rid = _log_req(request)
    namespace = request.query.get("namespace")
    try:
        sessions = session_store.list_all(namespace=namespace)
        request_log.finish(rid, 200)
        return web.json_response({"success": True, "namespace": namespace, "sessions": sessions})
    except Exception as exc:
        request_log.finish(rid, 400, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=400)

async def create_session(request: web.Request) -> web.Response:
    rid = _log_req(request)
    try: body = await request.json()
    except Exception: body = {}
    request_log.set_request_preview(rid, body)
    try:
        meta = session_store.create(
            session_id=body.get("session_id"),
            data=body.get("data"),
            session_idle_timeout_seconds=body.get("session_idle_timeout_seconds"),
            absolute_ttl_seconds=body.get("absolute_ttl_seconds"),
            files=body.get("files"),
            namespace=body.get("namespace"),
        )
        request_log.set_response_preview(rid, meta)
        request_log.finish(rid, 200)
        return web.json_response({"success": True, "session": meta})
    except Exception as exc:
        request_log.finish(rid, 400, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=400)

async def get_session(request: web.Request) -> web.Response:
    rid = _log_req(request)
    session_id = request.match_info["session_id"]
    namespace = request.query.get("namespace")
    try:
        meta = session_store.get(session_id, namespace=namespace)
        if not meta:
            request_log.finish(rid, 404, "Not found")
            return web.json_response({"success": False, "error": "Session not found"}, status=404)
        request_log.set_response_preview(rid, meta)
        request_log.finish(rid, 200)
        return web.json_response({"success": True, "session": meta})
    except Exception as exc:
        request_log.finish(rid, 400, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=400)

async def delete_session(request: web.Request) -> web.Response:
    rid = _log_req(request)
    session_id = request.match_info["session_id"]
    namespace = request.query.get("namespace")
    try:
        removed = session_store.delete(session_id, namespace=namespace)
        if not removed:
            request_log.finish(rid, 404, "Not found")
            return web.json_response({"success": False, "error": "Session not found"}, status=404)
        request_log.finish(rid, 200)
        return web.json_response({"success": True})
    except Exception as exc:
        request_log.finish(rid, 400, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=400)

async def list_files(request: web.Request) -> web.Response:
    rid = _log_req(request)
    session_id = request.match_info["session_id"]
    namespace = request.query.get("namespace")
    try:
        files = session_store.list_files(session_id, namespace=namespace)
        request_log.set_response_preview(rid, {"files": files})
        request_log.finish(rid, 200)
        return web.json_response({"success": True, "files": files})
    except FileNotFoundError as exc:
        request_log.finish(rid, 404, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=404)
    except Exception as exc:
        request_log.finish(rid, 400, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=400)

async def upload_files(request: web.Request) -> web.Response:
    rid = _log_req(request)
    session_id = request.match_info["session_id"]
    namespace = request.query.get("namespace")
    if not session_store.exists(session_id, namespace=namespace):
        request_log.finish(rid, 404, "Not found")
        return web.json_response({"success": False, "error": "Session not found"}, status=404)

    reader = await request.multipart()
    written = []
    while True:
        part = await reader.next()
        if part is None: break
        if part.name == "files":
            filename = part.filename
            content = await part.read()
            info = session_store.write_file(session_id, filename, content, namespace=namespace)
            written.append(info)
            
    request_log.set_response_preview(rid, {"written": written})
    request_log.finish(rid, 200)
    return web.json_response({"success": True, "written": written})

async def get_file(request: web.Request) -> web.Response:
    rid = _log_req(request)
    session_id = request.match_info["session_id"]
    rel_path = request.match_info["rel_path"]
    namespace = request.query.get("namespace")
    try:
        dest = session_store.resolve_file(session_id, rel_path, namespace=namespace)
        mime = mimetypes.guess_type(dest.name)[0] or "application/octet-stream"
        request_log.finish(rid, 200)
        return web.FileResponse(dest, headers={"Content-Type": mime})
    except FileNotFoundError as exc:
        request_log.finish(rid, 404, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=404)
    except Exception as exc:
        request_log.finish(rid, 400, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=400)

async def delete_file(request: web.Request) -> web.Response:
    rid = _log_req(request)
    session_id = request.match_info["session_id"]
    rel_path = request.match_info["rel_path"]
    namespace = request.query.get("namespace")
    try:
        removed = session_store.delete_file(session_id, rel_path, namespace=namespace)
        if not removed:
            request_log.finish(rid, 404, "Not found")
            return web.json_response({"success": False, "error": "File not found"}, status=404)
        request_log.finish(rid, 200)
        return web.json_response({"success": True})
    except FileNotFoundError as exc:
        request_log.finish(rid, 404, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=404)
    except Exception as exc:
        request_log.finish(rid, 400, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=400)

def register_routes(app: web.Application):
    app.router.add_get("/api/sessions", list_sessions)
    app.router.add_post("/api/sessions", create_session)
    app.router.add_get("/api/sessions/{session_id}", get_session)
    app.router.add_delete("/api/sessions/{session_id}", delete_session)
    
    app.router.add_get("/api/sessions/{session_id}/files", list_files)
    app.router.add_post("/api/sessions/{session_id}/files", upload_files)
    app.router.add_get("/api/sessions/{session_id}/files/{rel_path:.*}", get_file)
    app.router.add_delete("/api/sessions/{session_id}/files/{rel_path:.*}", delete_file)
