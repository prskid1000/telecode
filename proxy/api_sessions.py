"""AIOHTTP routes for task sessions. Mimics pythonmagic API."""

from __future__ import annotations

import json
import logging
import mimetypes
from aiohttp import web
from pathlib import Path

from services.session import session_store

logger = logging.getLogger("telecode.proxy.api_sessions")

async def list_sessions(request: web.Request) -> web.Response:
    namespace = request.query.get("namespace")
    try:
        sessions = session_store.list_all(namespace=namespace)
        return web.json_response({"success": True, "namespace": namespace, "sessions": sessions})
    except Exception as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=400)

async def create_session(request: web.Request) -> web.Response:
    try: body = await request.json()
    except Exception: body = {}
    try:
        meta = session_store.create(
            session_id=body.get("session_id"),
            data=body.get("data"),
            session_idle_timeout_seconds=body.get("session_idle_timeout_seconds"),
            absolute_ttl_seconds=body.get("absolute_ttl_seconds"),
            files=body.get("files"),
            namespace=body.get("namespace"),
        )
        return web.json_response({"success": True, "session": meta})
    except Exception as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=400)

async def get_session(request: web.Request) -> web.Response:
    session_id = request.match_info["session_id"]
    namespace = request.query.get("namespace")
    try:
        meta = session_store.get(session_id, namespace=namespace)
        if not meta: return web.json_response({"success": False, "error": "Session not found"}, status=404)
        return web.json_response({"success": True, "session": meta})
    except Exception as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=400)

async def delete_session(request: web.Request) -> web.Response:
    session_id = request.match_info["session_id"]
    namespace = request.query.get("namespace")
    try:
        removed = session_store.delete(session_id, namespace=namespace)
        if not removed: return web.json_response({"success": False, "error": "Session not found"}, status=404)
        return web.json_response({"success": True})
    except Exception as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=400)

async def list_files(request: web.Request) -> web.Response:
    session_id = request.match_info["session_id"]
    namespace = request.query.get("namespace")
    try:
        files = session_store.list_files(session_id, namespace=namespace)
        return web.json_response({"success": True, "files": files})
    except FileNotFoundError as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=404)
    except Exception as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=400)

async def upload_files(request: web.Request) -> web.Response:
    session_id = request.match_info["session_id"]
    namespace = request.query.get("namespace")
    if not session_store.exists(session_id, namespace=namespace):
        return web.json_response({"success": False, "error": "Session not found"}, status=404)

    reader = await request.multipart()
    written = []
    
    # AIOHTTP multipart is a bit different from Flask's request.files
    while True:
        part = await reader.next()
        if part is None: break
        if part.name == "files":
            filename = part.filename
            content = await part.read()
            # We could use 'paths' form field too if needed, but keeping it simple for now
            info = session_store.write_file(session_id, filename, content, namespace=namespace)
            written.append(info)
            
    return web.json_response({"success": True, "written": written})

async def get_file(request: web.Request) -> web.Response:
    session_id = request.match_info["session_id"]
    rel_path = request.match_info["rel_path"]
    namespace = request.query.get("namespace")
    try:
        dest = session_store.resolve_file(session_id, rel_path, namespace=namespace)
        mime = mimetypes.guess_type(dest.name)[0] or "application/octet-stream"
        return web.FileResponse(dest, headers={"Content-Type": mime})
    except FileNotFoundError as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=404)
    except Exception as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=400)

async def delete_file(request: web.Request) -> web.Response:
    session_id = request.match_info["session_id"]
    rel_path = request.match_info["rel_path"]
    namespace = request.query.get("namespace")
    try:
        removed = session_store.delete_file(session_id, rel_path, namespace=namespace)
        if not removed: return web.json_response({"success": False, "error": "File not found"}, status=404)
        return web.json_response({"success": True})
    except FileNotFoundError as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=404)
    except Exception as exc:
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
