"""AIOHTTP routes for TeleDesign core (docs/teledesign-contract.md §4).

Projects, canvas, boards, chats + turns, SSE events, files, uploads, versions,
comments, assets, tweaks / deterministic edits, templates, share snapshots, engines,
undo/redo version steps, download cards, handoff-to-session, Google Slides (gws), Figma import.

The REST surface has no auth, so:
- every id is checked with `store.valid_id`, every path with `store.safe_relpath`
  (and resolved inside the project);
- `design_guard_middleware` (installed by `register_routes`) rejects a mutating
  `/api/design/*` request whose `Origin` is not the proxy's own, or whose body
  is a CORS-simple type — a generated page on the preview origin can never write;
- raw project files are served as inert text on this origin (the preview origin
  is where pages execute).
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from aiohttp import web

import config
from services.design import assets as dassets
from services.design import chats as dchats
from services.design import comments as dcomments
from services.design import edits as dedits
from services.design import events, generate, preview, share, store, templates, versions
from services.design import files as dfiles

logger = logging.getLogger("telecode.proxy.api_design")

STATIC_DIR = Path(__file__).parent / "static"
APP_DIR = STATIC_DIR / "design" / "app"

_MUTATING = ("POST", "PUT", "PATCH", "DELETE")
_BODY_TYPES = ("application/json", "application/octet-stream", "multipart/form-data")
# Served inert on the API origin: never let an agent-written page execute here.
_INERT_TYPES = {".html", ".htm", ".svg", ".xml", ".js", ".mjs", ".jsx", ".tsx", ".ts", ".xhtml"}


# ── Guard ────────────────────────────────────────────────────────────────

def allowed_origins() -> set:
    extra = config.get_nested("design.allowed_origins", []) or []
    return set(preview.host_origins()) | {str(o).rstrip("/") for o in extra if isinstance(o, str)}


@web.middleware
async def design_guard_middleware(request: web.Request, handler):
    if request.method in _MUTATING and request.path.startswith("/api/design/"):
        origin = request.headers.get("Origin")
        # No Origin = not a browser page (CLI, MCP tools, tests): allowed.
        # "null" (sandboxed iframe) and any other origin — the preview origin included — are not.
        if origin is not None and origin.rstrip("/") not in allowed_origins():
            return web.json_response({"error": "Cross-origin write refused"}, status=403)
        if request.method != "DELETE" and request.content_type not in _BODY_TYPES:
            return web.json_response(
                {"error": "Mutating design routes take application/json, application/octet-stream "
                          "or multipart/form-data"}, status=415)
    return await handler(request)


# ── Helpers ──────────────────────────────────────────────────────────────

def _err(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"error": msg}, status=status)


async def _json_body(request: web.Request):
    try:
        data = await request.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _pid(request: web.Request) -> Optional[str]:
    pid = request.match_info["project_id"]
    return pid if store.valid_id(pid) and store.get_project(pid) else None


def _inert_file_response(p: Path) -> web.StreamResponse:
    resp = web.FileResponse(p)
    ext = p.suffix.lower()
    if ext in _INERT_TYPES:
        resp.content_type = "text/plain"
        resp.charset = "utf-8"
    else:
        resp.content_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _inert_bytes(name: str, data: bytes) -> web.Response:
    ext = os.path.splitext(name)[1].lower()
    ctype = "text/plain" if ext in _INERT_TYPES else (mimetypes.guess_type(name)[0] or "application/octet-stream")
    resp = web.Response(body=data, content_type=ctype)
    if ctype == "text/plain":
        resp.charset = "utf-8"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return resp


# ── Pages ────────────────────────────────────────────────────────────────

async def handle_design_ui(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "teledesign.html")


async def handle_design_app(request: web.Request) -> web.StreamResponse:
    rel = request.match_info["path"]
    p = store.resolve_in(APP_DIR, rel) if APP_DIR.is_dir() else None
    if not p or not p.is_file():
        raise web.HTTPNotFound()
    resp = web.FileResponse(p)
    ctype = {".js": "application/javascript", ".mjs": "application/javascript", ".css": "text/css",
             ".html": "text/html", ".json": "application/json", ".svg": "image/svg+xml"}.get(p.suffix.lower())
    if ctype:
        resp.content_type = ctype
    resp.headers["Cache-Control"] = "no-cache"
    return resp


async def get_config(request: web.Request) -> web.Response:
    return web.json_response({
        "preview_origin": preview.preview_origin(),
        "share_enabled": share.enabled(),
        "default_engine": dchats.default_engine(),
        "default_is_local": bool(config.get_nested("design.default_is_local", False)),
        "max_parallel_agents": int(config.get_nested("design.max_parallel_agents", 6)),
        "kinds": list(store.VALID_KINDS),
        "features": {"share_snapshots": True, "undo_steps": True, "downloads": True, "handoff_session": True,
                     "google_slides": True, "figma_import": True},
    })


async def get_engines(request: web.Request) -> web.Response:
    return web.json_response(await generate.engines_status())


# ── Projects ─────────────────────────────────────────────────────────────

async def list_projects(request: web.Request) -> web.Response:
    include_archived = request.query.get("include_archived") in ("1", "true", "yes")
    return web.json_response({"projects": store.list_projects(include_archived)})


async def ensure_welcome(request: web.Request) -> web.Response:
    """The gallery calls this when it finds no projects: creates the sample project
    once per install (a marker keeps a deleted sample from coming back)."""
    try:
        proj = await asyncio.to_thread(templates.ensure_welcome)
    except Exception as exc:
        logger.exception("design: welcome sample project failed")
        return _err(f"Couldn't create the sample project: {exc}", 500)
    return web.json_response({"project": proj})


async def create_project(request: web.Request) -> web.Response:
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    return web.json_response({"project": store.create_project(data)})


async def get_project(request: web.Request) -> web.Response:
    rec = store.get_project(request.match_info["project_id"])
    if not rec:
        return _err("Project not found", 404)
    return web.json_response({"project": rec})


async def update_project(request: web.Request) -> web.Response:
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    rec = store.update_project(request.match_info["project_id"], data)
    if not rec:
        return _err("Project not found", 404)
    return web.json_response({"project": rec})


async def delete_project(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    for c in dchats.list_chats(pid) or []:
        generate.stop_turn(pid, c["id"])
    dchats.drop_project_sessions(pid)
    store.delete_project(pid)
    return web.json_response({"ok": True})


async def get_thumbnail(request: web.Request) -> web.StreamResponse:
    pid = _pid(request)
    p = store.project_dir(pid) / "thumbnail.webp" if pid else None
    if not p or not p.is_file():
        return _err("No thumbnail yet", 404)
    resp = web.FileResponse(p)
    resp.content_type = "image/webp"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


async def get_canvas(request: web.Request) -> web.Response:
    data = store.get_canvas(request.match_info["project_id"])
    if data is None:
        return _err("No canvas saved yet", 404)
    return web.Response(body=data, content_type="application/octet-stream")


async def put_canvas(request: web.Request) -> web.Response:
    # Octet-stream is not a CORS-simple type, so a page in the preview sandbox
    # cannot forge this write without a preflight the proxy never answers.
    if request.content_type != "application/octet-stream":
        return _err("Expected application/octet-stream", 415)
    if (request.content_length or 0) > store.MAX_CANVAS_BYTES:
        return _err("Canvas too large", 413)
    data = await request.read()
    if not store.save_canvas(request.match_info["project_id"], data):
        return _err("Invalid canvas or project")
    return web.json_response({"ok": True})


async def get_boards(request: web.Request) -> web.Response:
    boards = store.get_boards(request.match_info["project_id"])
    if boards is None:
        return _err("Project not found", 404)
    return web.json_response({"boards": boards})


async def put_boards(request: web.Request) -> web.Response:
    data = await _json_body(request)
    if data is None or not store.save_boards(request.match_info["project_id"], data.get("boards")):
        return _err("Invalid boards or project")
    return web.json_response({"ok": True})


async def import_pen(request: web.Request) -> web.Response:
    if (request.content_length or 0) > store.MAX_IMPORT_BYTES:
        return _err("File too large", 413)
    reader = await request.multipart()
    part = await reader.next() if reader else None
    if part is None or not part.filename:
        return _err("Expected a .pen file")
    data = await part.read(decode=False)
    rel = store.import_pen(request.match_info["project_id"], part.filename, bytes(data))
    if not rel:
        return _err("Invalid .pen file or project")
    return web.json_response({"path": rel})


# ── Chats + turns ────────────────────────────────────────────────────────

async def list_chats(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    chats = dchats.list_chats(pid) or []
    out = []
    for c in chats:
        running = generate.running_turn(pid, c["id"])
        out.append({**c, "running_turn_id": running["id"] if running else None})
    return web.json_response({"chats": out})


async def create_chat(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    try:
        chat = dchats.create_chat(pid, data)
    except ValueError as exc:
        return _err(str(exc))
    return web.json_response({"chat": chat})


def _chat_ids(request: web.Request):
    pid = _pid(request)
    cid = request.match_info["chat_id"]
    if not pid or not store.valid_id(cid) or not dchats.get_chat(pid, cid):
        return None, None
    return pid, cid


async def update_chat(request: web.Request) -> web.Response:
    pid, cid = _chat_ids(request)
    if not pid:
        return _err("Chat not found", 404)
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    try:
        chat = dchats.update_chat(pid, cid, data)
    except ValueError as exc:
        return _err(str(exc))
    return web.json_response({"chat": chat})


async def delete_chat(request: web.Request) -> web.Response:
    pid, cid = _chat_ids(request)
    if not pid:
        return _err("Chat not found", 404)
    if generate.running_turn(pid, cid):
        return _err("A turn is running in this chat — stop it first", 409)
    dchats.delete_chat(pid, cid)
    return web.json_response({"ok": True})


async def list_turns(request: web.Request) -> web.Response:
    pid, cid = _chat_ids(request)
    if not pid:
        return _err("Chat not found", 404)
    after = request.query.get("after")
    if after and not store.valid_id(after):
        return _err("Invalid turn id")
    return web.json_response({"turns": generate.list_turns(pid, cid, after)})


async def post_turn(request: web.Request) -> web.Response:
    pid, cid = _chat_ids(request)
    if not pid:
        return _err("Chat not found", 404)
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    try:
        res = await generate.start_turn(pid, cid, data)
    except generate.BusyError as exc:
        return _err(str(exc), 409)
    except generate.NotFound as exc:
        return _err(str(exc), 404)
    except ValueError as exc:
        return _err(str(exc))
    return web.json_response(res)


async def stop_turn(request: web.Request) -> web.Response:
    pid, cid = _chat_ids(request)
    if not pid:
        return _err("Chat not found", 404)
    return web.json_response({"ok": generate.stop_turn(pid, cid)})


# ── Events (SSE) ─────────────────────────────────────────────────────────

async def sse_events(request: web.Request) -> web.StreamResponse:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    chat_id = request.query.get("chat_id")
    if chat_id and not store.valid_id(chat_id):
        return _err("Invalid chat id")
    resp = web.StreamResponse(headers={
        "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    await resp.prepare(request)
    sub = events.Subscription(pid, chat_id)
    try:
        await resp.write(b": connected\n\n")
        while True:
            item = await sub.get(events.HEARTBEAT_SEC)
            if item is None:
                await resp.write(b": ping\n\n")
                continue
            await resp.write(events.format_sse(*item))
    except (ConnectionResetError, asyncio.CancelledError, RuntimeError):
        pass
    finally:
        sub.close()
    return resp


# ── Files + uploads ──────────────────────────────────────────────────────

async def list_files(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    prefix = request.query.get("prefix", "")
    if prefix and not store.safe_relpath(prefix.strip("/")):
        return _err("Invalid prefix")
    return web.json_response({"files": dfiles.list_files(pid, prefix)})


async def get_file(request: web.Request) -> web.StreamResponse:
    pid = _pid(request)
    rel = request.match_info["path"]
    p = dfiles.read_path(pid, rel) if pid else None
    if not p:
        return _err("File not found", 404)
    return _inert_file_response(p)


def _snapshot_and_publish(pid: str, origin: str, changed_hint, prompt: str) -> Optional[int]:
    rec, changed = versions.snapshot(pid, origin, prompt=prompt)
    v = rec["v"] if rec else (store.get_project(pid) or {}).get("current_version")
    events.publish(pid, "files", {"changed": changed or list(changed_hint), "version": v})
    return v


async def put_file(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    rel = request.match_info["path"]
    if not dfiles.writable(rel):
        return _err("Invalid or read-only path")
    if request.content_type != "application/octet-stream":
        return _err("Expected application/octet-stream", 415)
    if (request.content_length or 0) > dfiles.MAX_FILE_BYTES:
        return _err("File too large", 413)
    data = await request.read()
    if len(data) > dfiles.MAX_FILE_BYTES:
        return _err("File too large", 413)
    if not await asyncio.to_thread(dfiles.write_file, pid, rel, data):
        return _err("Could not write file")
    v = await asyncio.to_thread(_snapshot_and_publish, pid, "user", [rel], f"Edited {rel}")
    return web.json_response({"ok": True, "version": v})


async def delete_file(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    rel = request.match_info["path"]
    if not dfiles.delete_file(pid, rel):
        return _err("File not found or read-only", 404)
    v = await asyncio.to_thread(_snapshot_and_publish, pid, "user", [rel], f"Deleted {rel}")
    return web.json_response({"ok": True, "version": v})


async def upload_files(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    if request.content_type != "multipart/form-data":
        return _err("Expected multipart/form-data", 415)
    root = store.project_dir(pid)
    reader = await request.multipart()
    paths = []
    while True:
        part = await reader.next()
        if part is None:
            break
        if not part.filename:
            continue
        if len(paths) >= dfiles.MAX_UPLOADS_PER_REQUEST:
            return _err("Too many files", 413)
        rel = dfiles.upload_name(pid, part.filename)
        if not rel:
            return _err("Invalid file name")
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".td-{uuid.uuid4().hex[:8]}.tmp")
        size = 0
        try:
            with tmp.open("wb") as fh:
                while True:
                    chunk = await part.read_chunk(256 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > dfiles.MAX_UPLOAD_BYTES:
                        raise ValueError("too large")
                    fh.write(chunk)
            os.replace(tmp, dest)
        except ValueError:
            tmp.unlink(missing_ok=True)
            return _err(f"{part.filename}: file larger than {dfiles.MAX_UPLOAD_BYTES // (1024 * 1024)} MB", 413)
        paths.append(rel)
    if paths:
        events.publish(pid, "files", {"changed": paths, "version": None})
    return web.json_response({"paths": paths})


# ── Versions ─────────────────────────────────────────────────────────────

def _int(value: Any) -> Optional[int]:
    try:
        v = int(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


async def list_versions(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    vs = versions.list_versions(pid) or []
    # The list view needs no per-file hashes; one version's detail has them.
    slim = [{k: v for k, v in rec.items() if k != "files"} | {"file_count": len(rec.get("files") or {})}
            for rec in vs]
    return web.json_response({"versions": slim, "current_version": (store.get_project(pid) or {}).get("current_version")})


async def get_version(request: web.Request) -> web.Response:
    pid = _pid(request)
    v = _int(request.match_info["v"])
    rec = versions.get_version(pid, v) if pid and v else None
    if not rec:
        return _err("Version not found", 404)
    return web.json_response({"version": rec,
                              "files": [{"path": p, "sha256": s} for p, s in sorted(rec["files"].items())]})


async def get_version_file(request: web.Request) -> web.Response:
    pid = _pid(request)
    v = _int(request.match_info["v"])
    rel = request.match_info["path"]
    data = versions.file_at(pid, v, rel) if pid and v else None
    if data is None:
        return _err("File not found in that version", 404)
    return _inert_bytes(rel, data)


async def restore_version(request: web.Request) -> web.Response:
    pid = _pid(request)
    v = _int(request.match_info["v"])
    if not pid or not v or not versions.get_version(pid, v):
        return _err("Version not found", 404)
    data = await _json_body(request) or {}
    paths = data.get("paths")
    if paths is not None and (not isinstance(paths, list) or not all(isinstance(p, str) and store.safe_relpath(p)
                                                                      for p in paths)):
        return _err("paths must be a list of project paths")
    rec = await asyncio.to_thread(versions.restore, pid, v, paths)
    if rec:
        events.publish(pid, "files", {"changed": rec.get("changed") or [], "version": rec["v"]})
    return web.json_response({"version": rec})


async def diff_versions(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    a = _int(request.query.get("a"))
    b_raw = request.query.get("b")
    b = None if b_raw in (None, "", "working", "current") else _int(b_raw)
    rel = request.query.get("path") or None
    if not a or (b_raw not in (None, "", "working", "current") and not b):
        return _err("a (and optionally b) must be version numbers")
    out = await asyncio.to_thread(versions.diff, pid, a, b, rel)
    if out is None:
        return _err("Version not found", 404)
    return web.json_response({"diff": out})


# ── Comments ─────────────────────────────────────────────────────────────

async def list_comments(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    items = dcomments.list_comments(pid) or []
    status = request.query.get("status")
    if status:
        items = [c for c in items if c.get("status") == status]
    return web.json_response({"comments": items})


async def create_comment(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    try:
        rec = dcomments.create_comment(pid, data)
    except ValueError as exc:
        return _err(str(exc))
    events.publish(pid, "comments", {"changed": [rec["id"]]})
    return web.json_response({"comment": rec})


async def update_comment(request: web.Request) -> web.Response:
    pid = _pid(request)
    cid = request.match_info["comment_id"]
    if not pid or not store.valid_id(cid):
        return _err("Comment not found", 404)
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    try:
        rec = dcomments.update_comment(pid, cid, data)
    except ValueError as exc:
        return _err(str(exc))
    if not rec:
        return _err("Comment not found", 404)
    events.publish(pid, "comments", {"changed": [cid]})
    return web.json_response({"comment": rec})


async def delete_comment(request: web.Request) -> web.Response:
    pid = _pid(request)
    cid = request.match_info["comment_id"]
    if not pid or not store.valid_id(cid) or not dcomments.delete_comment(pid, cid):
        return _err("Comment not found", 404)
    events.publish(pid, "comments", {"changed": [cid]})
    return web.json_response({"ok": True})


async def send_comments(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    ids, chat_id = data.get("ids"), data.get("chat_id")
    if not isinstance(ids, list) or not ids or not all(store.valid_id(i) for i in ids):
        return _err("ids must be a non-empty list of comment ids")
    if not store.valid_id(chat_id or "") or not dchats.get_chat(pid, chat_id):
        return _err("Chat not found", 404)
    body = {"text": data.get("text") or "Address the attached comments.", "comment_ids": ids}
    for k in ("engine", "is_local", "effort", "model"):
        if k in data:
            body[k] = data[k]
    try:
        res = await generate.start_turn(pid, chat_id, body)
    except generate.BusyError as exc:
        return _err(str(exc), 409)
    except ValueError as exc:
        return _err(str(exc))
    return web.json_response(res)


# ── Assets (review manifest) ─────────────────────────────────────────────

async def get_assets(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    return web.json_response({"assets": dassets.get_assets(pid)})


async def put_assets(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    data = await _json_body(request)
    if data is None or not isinstance(data.get("assets"), list):
        return _err('Expected {"assets": [...]}')
    items = dassets.put_assets(pid, data["assets"])
    events.publish(pid, "assets", {})
    return web.json_response({"assets": items})


async def patch_asset(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    try:
        rec = dassets.patch_asset(pid, request.match_info["asset_id"], data)
    except ValueError as exc:
        return _err(str(exc))
    if not rec:
        return _err("Asset not found", 404)
    events.publish(pid, "assets", {})
    return web.json_response({"asset": rec})


# ── Tweaks + deterministic edits ─────────────────────────────────────────

_EDITABLE_EXT = (".html", ".htm", ".jsx", ".tsx", ".js")


def _read_text(pid: str, rel: Any) -> Optional[str]:
    if not isinstance(rel, str) or not rel.lower().endswith(_EDITABLE_EXT) or not dfiles.writable(rel):
        return None
    p = dfiles.read_path(pid, rel)
    if not p or p.stat().st_size > 8 * 1024 * 1024:
        return None
    return p.read_text(encoding="utf-8")


async def post_tweaks(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    rel = data.get("file")
    src = _read_text(pid, rel)
    if src is None:
        return _err("File not found or not editable", 404)
    try:
        out = dedits.apply_tweaks(src, data.get("edits"))
    except dedits.EditError as exc:
        return _err(str(exc), 422)
    if out != src:
        dfiles.write_file(pid, rel, out.encode("utf-8"))
    v = await asyncio.to_thread(_snapshot_and_publish, pid, "tweak", [rel], f"Tweaks in {rel}")
    return web.json_response({"ok": True, "version": v})


async def post_edits(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    rel = data.get("file")
    src = _read_text(pid, rel)
    if src is None:
        return _err("File not found or not editable", 404)
    try:
        out = dedits.apply_edit(src, rel, data.get("op"), data.get("value"),
                                source_loc=data.get("source_loc"), td_id=data.get("td_id"))
    except dedits.Ambiguous as exc:
        return web.json_response({"ok": False, "route": "agent", "reason": str(exc)})
    except dedits.EditError as exc:
        return _err(str(exc), 422)
    if out != src:
        dfiles.write_file(pid, rel, out.encode("utf-8"))
    v = await asyncio.to_thread(_snapshot_and_publish, pid, "user", [rel], f"Inline {data.get('op')} edit in {rel}")
    return web.json_response({"ok": True, "version": v})


# ── Templates ────────────────────────────────────────────────────────────

async def list_templates(request: web.Request) -> web.Response:
    return web.json_response({"templates": templates.list_templates()})


async def create_template(request: web.Request) -> web.Response:
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    if not store.valid_id(str(data.get("project_id") or "")):
        return _err("project_id required")
    try:
        rec = await asyncio.to_thread(templates.create_template, data)
    except LookupError as exc:
        return _err(str(exc), 404)
    except ValueError as exc:
        return _err(str(exc))
    return web.json_response({"template": rec})


async def delete_template(request: web.Request) -> web.Response:
    if not templates.delete_template(request.match_info["template_id"]):
        return _err("Template not found", 404)
    return web.json_response({"ok": True})


async def template_cover(request: web.Request) -> web.StreamResponse:
    p = templates.cover_path(request.match_info["template_id"])
    if not p:
        return _err("No cover", 404)
    return web.FileResponse(p)


async def instantiate_template(request: web.Request) -> web.Response:
    data = await _json_body(request) or {}
    proj = await asyncio.to_thread(templates.instantiate, request.match_info["template_id"], data)
    if not proj:
        return _err("Template not found", 404)
    return web.json_response({"project": proj})


# ── Share ────────────────────────────────────────────────────────────────

def _share_disabled() -> web.Response:
    return _err("Sharing is disabled (design.share.enabled)", 403)


async def list_shares(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    return web.json_response({"enabled": share.enabled(), "shares": share.list_shares(pid)})


async def create_share(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    if not share.enabled():
        return _share_disabled()
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    try:
        rec = await asyncio.to_thread(share.create_share, pid, data.get("role") or "view",
                                      data.get("expires_in"), data.get("snapshot", True) is not False)
    except ValueError as exc:
        return _err(str(exc))
    full = share._load().get(rec["token"]) or {}
    deps = await asyncio.to_thread(share.dependency_report, full) if full else []
    return web.json_response({"share": rec, "url": f"/design/s/{rec['token']}", "missing_dependencies": deps})


async def update_share(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    if not share.enabled():
        return _share_disabled()
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    try:
        rec = await asyncio.to_thread(share.update_share, pid, request.match_info["token"],
                                      resnapshot=bool(data.get("resnapshot")), role=data.get("role"),
                                      expires_in=data["expires_in"] if "expires_in" in data else "keep")
    except ValueError as exc:
        return _err(str(exc))
    if not rec:
        return _err("Share not found", 404)
    full = share._load().get(rec["token"]) or {}
    deps = await asyncio.to_thread(share.dependency_report, full) if full else []
    return web.json_response({"share": rec, "missing_dependencies": deps})


async def check_share(request: web.Request) -> web.Response:
    pid = _pid(request)
    full = share._load().get(request.match_info["token"]) if pid else None
    if not full or full.get("project_id") != pid:
        return _err("Share not found", 404)
    deps = await asyncio.to_thread(share.dependency_report, full)
    return web.json_response({"share": share.public(full), "missing_dependencies": deps})


async def delete_share(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid or not share.delete_share(pid, request.match_info["token"]):
        return _err("Share not found", 404)
    return web.json_response({"ok": True})


async def share_page(request: web.Request) -> web.StreamResponse:
    page = APP_DIR / "share.html"
    if not share.resolve(request.match_info["token"]) or not page.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(page)


async def share_meta(request: web.Request) -> web.Response:
    rec = share.resolve(request.match_info["token"])
    if not rec:
        return _err("Share not found", 404)
    pid = rec["project_id"]
    snap = share.snapshot_files(rec)
    token = request.match_info["token"]
    return web.json_response({
        "role": rec["role"],
        "project": {k: v for k, v in (store.get_project(pid) or {}).items()
                    if k in ("id", "title", "kind", "created_at", "updated_at")},
        "boards": store.get_boards(pid),
        "files": share.list_recipient_files(rec),
        "comments": dcomments.list_comments(pid) if rec["role"] in ("comment", "edit") else [],
        "preview_origin": preview.preview_origin(),
        "snapshot": None if snap is None else {"at": (rec.get("snapshot") or {}).get("at"), "file_count": len(snap)},
        # Where the recipient's frames load: the frozen snapshot on the preview origin, or
        # (links made before snapshots) the live project.
        "pages_base": f"/s/{token}/" if snap is not None else f"/p/{pid}/",
        "expires_at": rec.get("expires_at"),
        "download_url": f"/api/design/s/{token}/download",
    })


async def share_file(request: web.Request) -> web.StreamResponse:
    rec = share.resolve(request.match_info["token"])
    rel = request.match_info["path"]
    data = await asyncio.to_thread(share.read_file, rec, rel) if rec else None
    if data is None:
        return _err("File not found", 404)
    return _inert_bytes(rel, data)


async def share_download(request: web.Request) -> web.StreamResponse:
    rec = share.resolve(request.match_info["token"])
    if not rec:
        return _err("Share not found", 404)
    try:
        name, data = await asyncio.to_thread(share.recipient_zip, rec)
    except ValueError as exc:
        return _err(str(exc), 413)
    return web.Response(body=data, content_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff"})


async def share_comment(request: web.Request) -> web.Response:
    rec = share.resolve(request.match_info["token"])
    if not rec:
        return _err("Share not found", 404)
    if rec["role"] not in ("comment", "edit"):
        return _err("This link is view-only", 403)
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    data.setdefault("author", "Guest")
    try:
        c = dcomments.create_comment(rec["project_id"], data)
    except ValueError as exc:
        return _err(str(exc))
    events.publish(rec["project_id"], "comments", {"changed": [c["id"]]})
    return web.json_response({"comment": c})


# ── Undo / redo on HTML boards (version steps) ───────────────────────────

async def get_undo_state(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    rel = request.query.get("path") or ""
    if not store.safe_relpath(rel):
        return _err("path required")
    return web.json_response(await asyncio.to_thread(versions.undo_state, pid, rel))


async def post_undo(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    rel = data.get("path")
    if not isinstance(rel, str) or not dfiles.writable(rel):
        return _err("path must be a writable project file")
    try:
        res = await asyncio.to_thread(versions.step, pid, rel, data.get("direction") or "undo")
    except ValueError as exc:
        return _err(str(exc))
    if not res:
        return web.json_response({"ok": False, "reason": "nothing to " + (data.get("direction") or "undo")})
    v = res["version"]["v"] if res.get("version") else None
    events.publish(pid, "files", {"changed": [rel], "version": v})
    return web.json_response({"ok": True, **res})


# ── Downloads (chat download cards) ──────────────────────────────────────

MAX_DOWNLOAD_ZIP_BYTES = 512 * 1024 * 1024


def _zip_project_paths(pid: str, prefix: str) -> tuple:
    import io
    import zipfile
    root = store.project_dir(pid)
    title = (store.get_project(pid) or {}).get("title") or "design"
    slug = "".join(c if c.isalnum() else "-" for c in title).strip("-").lower()[:60] or "design"
    name = slug if not prefix else f"{slug}-{prefix.rstrip('/').replace('/', '-')}"
    buf = io.BytesIO()
    total = 0
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, p in dfiles.iter_project_files(root, include_readonly=True):
            if rel in ("comments.json", "assets.json"):
                continue
            if prefix and not rel.startswith(prefix):
                continue
            total += p.stat().st_size
            if total > MAX_DOWNLOAD_ZIP_BYTES:
                raise ValueError("too large to download as one ZIP — use Export → Project ZIP")
            z.write(p, f"{name}/{rel[len(prefix):] if prefix else rel}")
            count += 1
    return f"{name}.zip", buf.getvalue(), count


async def download(request: web.Request) -> web.StreamResponse:
    """`?path=<file>` → the file as an attachment; `?path=<folder>/` or `?kind=folder`
    → that folder zipped; `?kind=project` (no path) → the whole project zipped."""
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    rel = (request.query.get("path") or "").strip()
    kind = request.query.get("kind") or ("project" if not rel else "file")
    if kind == "file":
        p = dfiles.read_path(pid, rel)
        if p:
            resp = web.FileResponse(p)
            resp.content_type = "application/octet-stream"
            resp.headers["Content-Disposition"] = f'attachment; filename="{p.name}"'
            resp.headers["X-Content-Type-Options"] = "nosniff"
            resp.headers["Cache-Control"] = "no-store"
            return resp
        kind = "folder"
    prefix = ""
    if kind == "folder":
        prefix = rel.strip("/")
        if not store.safe_relpath(prefix) or prefix.split("/", 1)[0] in dfiles.HIDDEN_TOP:
            return _err("File or folder not found", 404)
        d = dfiles.resolve(pid, prefix)
        if not d or not d.is_dir():
            return _err("File or folder not found", 404)
        prefix += "/"
    elif kind != "project":
        return _err("kind must be file, folder or project")
    try:
        name, data, count = await asyncio.to_thread(_zip_project_paths, pid, prefix)
    except ValueError as exc:
        return _err(str(exc), 413)
    if not count:
        return _err("Nothing to download there", 404)
    return web.Response(body=data, content_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff"})


# ── Handoff into a coding session ────────────────────────────────────────

async def list_dirs(request: web.Request) -> web.Response:
    from services.design import handoff
    try:
        return web.json_response(await asyncio.to_thread(handoff.list_dirs, request.query.get("path") or None))
    except ValueError as exc:
        return _err(str(exc))


async def handoff_session(request: web.Request) -> web.Response:
    from services.design import handoff
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    try:
        res = await asyncio.to_thread(handoff.start_session, pid, data)
    except LookupError as exc:
        return _err(str(exc), 404)
    except (ValueError, FileExistsError) as exc:
        return _err(str(exc))
    return web.json_response(res)


# ── Send to Google Slides (gws) ──────────────────────────────────────────

async def gslides_status(request: web.Request) -> web.Response:
    from services.design import gslides
    refresh = request.query.get("refresh") in ("1", "true")
    return web.json_response(await asyncio.to_thread(gslides.status, refresh))


async def gslides_send(request: web.Request) -> web.Response:
    from services.design import gslides
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    f = data.get("file")
    if f is not None and (not isinstance(f, str) or not store.safe_relpath(f)):
        return _err("invalid file")
    try:
        st = await asyncio.to_thread(gslides.status, True)
        if not st.get("installed") or not st.get("authed") or not st.get("can_upload", True):
            return web.json_response({"error": st.get("instructions"), "status": st}, status=412)
        job = gslides.start(pid, data)
    except PermissionError as exc:
        return web.json_response({"error": str(exc)}, status=412)
    return web.json_response({"job_id": job["id"], "job": job})


async def gslides_job(request: web.Request) -> web.Response:
    from services.design import gslides
    pid = _pid(request)
    job = gslides.get_job(request.match_info["job_id"]) if pid else None
    if not job or job.get("pid") != pid:
        return _err("Job not found", 404)
    return web.json_response(job)


# ── Figma link import ────────────────────────────────────────────────────

async def figma_status(request: web.Request) -> web.Response:
    from services.design import figma_import
    return web.json_response({"has_token": bool(figma_import.token())})


async def figma_set_token(request: web.Request) -> web.Response:
    from services.design import figma_import
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    try:
        await asyncio.to_thread(figma_import.set_token, data.get("token"))
    except ValueError as exc:
        return _err(str(exc))
    return web.json_response({"has_token": bool(figma_import.token())})


async def figma_frames(request: web.Request) -> web.Response:
    from services.design import figma_import
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    try:
        return web.json_response(await figma_import.list_frames(data.get("url")))
    except ValueError as exc:
        return _err(str(exc))
    except figma_import.FigmaError as exc:
        return _err(str(exc), 502)


async def figma_import_route(request: web.Request) -> web.Response:
    from services.design import figma_import
    pid = _pid(request)
    if not pid:
        return _err("Project not found", 404)
    data = await _json_body(request)
    if data is None:
        return _err("Invalid JSON")
    ids = data.get("ids")
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
        return _err("ids must be a list of node ids")
    try:
        res = await figma_import.import_frames(pid, data.get("url"), ids)
    except ValueError as exc:
        return _err(str(exc))
    except figma_import.FigmaError as exc:
        return _err(str(exc), 502)
    except Exception as exc:  # media_fetch refusal etc.
        return _err(f"Figma import failed: {exc}", 502)
    v = await asyncio.to_thread(_snapshot_and_publish, pid, "user", res["files"],
                                f"Imported {len(res['boards'])} frame(s) from Figma")
    return web.json_response({**res, "version": v})


# ── Registration ─────────────────────────────────────────────────────────

def register_routes(app: web.Application):
    # Applies to every mutating /api/design/* route, whoever owns it. Added
    # here (before the app freezes) so the guard ships with the routes.
    if design_guard_middleware not in app.middlewares:
        app.middlewares.append(design_guard_middleware)

    r = app.router
    r.add_get("/design", handle_design_ui)
    r.add_get("/design/app/{path:.*}", handle_design_app)
    r.add_get("/design/s/{token}", share_page)
    r.add_get("/api/design/config", get_config)
    r.add_get("/api/design/engines", get_engines)

    P = "/api/design/projects/{project_id}"
    r.add_get("/api/design/projects", list_projects)
    r.add_post("/api/design/projects", create_project)
    r.add_post("/api/design/welcome", ensure_welcome)
    r.add_get(P, get_project)
    r.add_patch(P, update_project)
    r.add_delete(P, delete_project)
    r.add_get(P + "/thumbnail", get_thumbnail)
    r.add_get(P + "/canvas", get_canvas)
    r.add_put(P + "/canvas", put_canvas)
    r.add_get(P + "/boards", get_boards)
    r.add_put(P + "/boards", put_boards)
    r.add_post(P + "/import/pen", import_pen)

    r.add_get(P + "/chats", list_chats)
    r.add_post(P + "/chats", create_chat)
    r.add_patch(P + "/chats/{chat_id}", update_chat)
    r.add_delete(P + "/chats/{chat_id}", delete_chat)
    r.add_get(P + "/chats/{chat_id}/turns", list_turns)
    r.add_post(P + "/chats/{chat_id}/turns", post_turn)
    r.add_post(P + "/chats/{chat_id}/stop", stop_turn)
    r.add_get(P + "/events", sse_events)

    r.add_get(P + "/files", list_files)
    r.add_get(P + "/files/{path:.+}", get_file)
    r.add_put(P + "/files/{path:.+}", put_file)
    r.add_delete(P + "/files/{path:.+}", delete_file)
    r.add_post(P + "/uploads", upload_files)

    r.add_get(P + "/versions", list_versions)
    r.add_get(P + "/versions/diff", diff_versions)
    r.add_get(P + "/versions/{v:\\d+}", get_version)
    r.add_get(P + "/versions/{v:\\d+}/files/{path:.+}", get_version_file)
    r.add_post(P + "/versions/{v:\\d+}/restore", restore_version)

    r.add_get(P + "/comments", list_comments)
    r.add_post(P + "/comments", create_comment)
    r.add_post(P + "/comments/send", send_comments)
    r.add_patch(P + "/comments/{comment_id}", update_comment)
    r.add_delete(P + "/comments/{comment_id}", delete_comment)

    r.add_get(P + "/assets", get_assets)
    r.add_put(P + "/assets", put_assets)
    r.add_patch(P + "/assets/{asset_id}", patch_asset)

    r.add_post(P + "/tweaks", post_tweaks)
    r.add_post(P + "/edits", post_edits)

    r.add_get("/api/design/templates", list_templates)
    r.add_post("/api/design/templates", create_template)
    r.add_delete("/api/design/templates/{template_id}", delete_template)
    r.add_get("/api/design/templates/{template_id}/cover", template_cover)
    r.add_post("/api/design/templates/{template_id}/instantiate", instantiate_template)

    r.add_get(P + "/share", list_shares)
    r.add_post(P + "/share", create_share)
    r.add_patch(P + "/share/{token}", update_share)
    r.add_get(P + "/share/{token}/check", check_share)
    r.add_delete(P + "/share/{token}", delete_share)
    r.add_get("/api/design/s/{token}", share_meta)
    r.add_get("/api/design/s/{token}/download", share_download)
    r.add_get("/api/design/s/{token}/files/{path:.+}", share_file)
    r.add_post("/api/design/s/{token}/comments", share_comment)

    r.add_get(P + "/versions/undo", get_undo_state)
    r.add_post(P + "/versions/undo", post_undo)
    r.add_get(P + "/download", download)
    r.add_get("/api/design/fs/dirs", list_dirs)
    r.add_post(P + "/handoff/session", handoff_session)
    r.add_get("/api/design/gslides/status", gslides_status)
    r.add_post(P + "/send/google-slides", gslides_send)
    r.add_get(P + "/send/jobs/{job_id}", gslides_job)
    r.add_get("/api/design/figma", figma_status)
    r.add_put("/api/design/figma/token", figma_set_token)
    r.add_post(P + "/import/figma/frames", figma_frames)
    r.add_post(P + "/import/figma", figma_import_route)
