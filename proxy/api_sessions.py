"""AIOHTTP routes for task sessions. Mimics pythonmagic API."""

from __future__ import annotations

import asyncio
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
        out = {"success": True, "namespace": namespace, "sessions": sessions}
        # Expired workspaces are archived, not deleted (B1); ?include_archived=1
        # lists them separately so the UI can offer a restore.
        if (request.query.get("include_archived") or "").lower() in ("1", "true", "yes"):
            out["archived"] = session_store.list_archived(namespace=namespace)
        request_log.finish(rid, 200)
        return web.json_response(out)
    except Exception as exc:
        request_log.finish(rid, 400, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=400)

async def create_session(request: web.Request) -> web.Response:
    rid = _log_req(request)
    try: body = await request.json()
    except Exception: body = {}
    request_log.set_request_preview(rid, body)
    try:
        # A session created here is a workspace: with no TTL given it never
        # expires (0). 0 from the UI's "expiry off" also means never.
        idle = body.get("session_idle_timeout_seconds")
        meta = session_store.create(
            session_id=body.get("session_id"),
            data=body.get("data"),
            session_idle_timeout_seconds=0 if idle in (None, "") else idle,
            absolute_ttl_seconds=body.get("absolute_ttl_seconds") or None,
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

async def update_session(request: web.Request) -> web.Response:
    rid = _log_req(request)
    session_id = request.match_info["session_id"]
    try:
        data = await request.json()
    except Exception:
        data = {}

    namespace = request.query.get("namespace")
    try:
        meta = session_store.update_session(
            sid=session_id,
            namespace=namespace,
            session_idle_timeout_seconds=data.get("session_idle_timeout_seconds"),
            absolute_ttl_seconds=data.get("absolute_ttl_seconds"),
            data=data.get("data")
        )
        request_log.finish(rid, 200)
        return web.json_response({"success": True, "session": meta})
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

async def restore_session(request: web.Request) -> web.Response:
    rid = _log_req(request)
    session_id = request.match_info["session_id"]
    namespace = request.query.get("namespace")
    try:
        meta = session_store.restore(session_id, namespace=namespace)
    except ValueError as exc:
        request_log.finish(rid, 400, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=400)
    if not meta:
        request_log.finish(rid, 404, "Not archived")
        return web.json_response({"success": False, "error": "No archived session with that id"}, status=404)
    request_log.finish(rid, 200)
    return web.json_response({"success": True, "session": meta})

# ── Snapshots (P2: shadow git per workspace) + session lineage ─────────────

def _snap_target(request: web.Request):
    """(key, work_dir) for the session in the URL; raises FileNotFoundError."""
    from services import snapshots
    session_id = request.match_info["session_id"]
    namespace = request.query.get("namespace") or None
    if not session_store.exists(session_id, namespace=namespace):
        raise FileNotFoundError("Session not found")
    return snapshots.key_for(session_id, namespace), session_store._session_dir(session_id, namespace=namespace)


def _busy(request: web.Request) -> bool:
    from services.task.task_manager import get_task_queue
    return get_task_queue().session_has_active_task(request.match_info["session_id"],
                                                    request.query.get("namespace") or None)


async def list_snapshots(request: web.Request) -> web.Response:
    from services import snapshots
    try:
        key, _wd = _snap_target(request)
    except (FileNotFoundError, ValueError) as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=404)
    limit = min(1000, max(1, int(request.query.get("limit") or 200)))
    snaps = await asyncio.get_running_loop().run_in_executor(None, lambda: snapshots.log(key, limit=limit))
    return web.json_response({"success": True, "enabled": snapshots.available(), "snapshot_key": key,
                              "snapshots": snaps})


async def take_snapshot(request: web.Request) -> web.Response:
    from services import snapshots
    try:
        key, wd = _snap_target(request)
    except (FileNotFoundError, ValueError) as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=404)
    try:
        body = await request.json()
    except Exception:
        body = {}
    label = str((body or {}).get("label") or "manual snapshot")[:200]
    sha = await asyncio.get_running_loop().run_in_executor(
        None, lambda: snapshots.take(key, wd, label, {"phase": "manual"}))
    if not sha:
        return web.json_response({"success": False, "error": "snapshot failed (disabled, or git unavailable)"},
                                 status=503)
    return web.json_response({"success": True, "sha": sha})


async def snapshot_diff(request: web.Request) -> web.Response:
    from services import snapshots
    try:
        key, _wd = _snap_target(request)
    except (FileNotFoundError, ValueError) as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=404)
    sha = request.match_info["sha"]
    base = request.query.get("base") or None
    path = request.query.get("path") or None

    def work():
        b = base or snapshots.previous(key, sha)
        if not b:
            raise snapshots.SnapshotError("this is the oldest snapshot — nothing to compare with")
        return {"before": b, "after": sha, "files": snapshots.changed_files(key, b, sha),
                **snapshots.diff(key, b, sha, path)}
    try:
        out = await asyncio.get_running_loop().run_in_executor(None, work)
    except snapshots.SnapshotError as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=404)
    return web.json_response({"success": True, **out})


async def restore_snapshot(request: web.Request) -> web.Response:
    from services import snapshots
    try:
        key, wd = _snap_target(request)
    except (FileNotFoundError, ValueError) as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=404)
    if _busy(request):
        return web.json_response({"success": False, "error": "a task is running in this session"}, status=409)
    sha = request.match_info["sha"]
    try:
        res = await asyncio.get_running_loop().run_in_executor(
            None, lambda: snapshots.restore(key, wd, sha, meta={"requested_by": "api"}))
    except snapshots.SnapshotError as exc:
        return web.json_response({"success": False, "error": str(exc)}, status=404)
    return web.json_response({"success": True, "restored_to": sha, **res})


async def session_lineage(request: web.Request) -> web.Response:
    from services.db import sessions_repo
    sid = request.match_info["session_id"]
    ns = request.query.get("namespace")
    rows = sessions_repo.list_sessions(workspace_id=sid, namespace=ns if ns is not None else None)
    return web.json_response({"success": True, "sessions": rows})


def register_routes(app: web.Application):
    app.router.add_get("/api/sessions", list_sessions)
    app.router.add_post("/api/sessions", create_session)
    app.router.add_get("/api/sessions/{session_id}", get_session)
    app.router.add_put("/api/sessions/{session_id}", update_session)
    app.router.add_delete("/api/sessions/{session_id}", delete_session)
    app.router.add_post("/api/sessions/{session_id}/restore", restore_session)
    app.router.add_get("/api/sessions/{session_id}/snapshots", list_snapshots)
    app.router.add_post("/api/sessions/{session_id}/snapshots", take_snapshot)
    app.router.add_get("/api/sessions/{session_id}/snapshots/{sha}/diff", snapshot_diff)
    app.router.add_post("/api/sessions/{session_id}/snapshots/{sha}/restore", restore_snapshot)
    app.router.add_get("/api/sessions/{session_id}/lineage", session_lineage)
    
    app.router.add_get("/api/sessions/{session_id}/files", list_files)
    app.router.add_post("/api/sessions/{session_id}/files", upload_files)
    app.router.add_get("/api/sessions/{session_id}/files/{rel_path:.*}", get_file)
    app.router.add_delete("/api/sessions/{session_id}/files/{rel_path:.*}", delete_file)
