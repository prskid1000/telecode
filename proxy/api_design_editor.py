"""AIOHTTP routes for the TeleDesign canvas editor (patched open-pencil build).

    GET  /design/editor/{path}                 the vendored static build (proxy/static/design/editor/)
    GET  /api/design/editor-bridge?project=    WebSocket: the editor page registers here (editor_bridge)
    GET  /api/design/editor-bridge/health      what the page's MCP runtime polls ({status, version, tools})
    GET  /api/design/editor/tools              tool descriptors with JSON input schemas
    GET  /api/design/editor/build-info         BUILD_INFO.json of the vendored build
    GET  /api/design/projects/{pid}/editor     bridge status for one project
    POST /api/design/projects/{pid}/editor/call   {tool, args?, timeout?, format?: "raw"|"mcp"}
    POST /api/design/projects/{pid}/editor/boards {node_id, src, width?, height?, key?}
    DELETE /api/design/projects/{pid}/editor/boards/{key}
    POST /api/design/projects/{pid}/editor/convert        {direction:"to-layers", key?|src?} | {direction:"to-html", node_id, path?}
    POST /api/design/projects/{pid}/editor/layer-preview  {node_id} → {path, url} (.layers/<slug>.html)
    GET  /api/design/projects/{pid}/editor/tokens         where the tokens are + what would be mirrored
    POST /api/design/projects/{pid}/editor/tokens/push    tokens.json → canvas variable collections
    POST /api/design/projects/{pid}/editor/tokens/pull    canvas variables → tokens.json (+ tokens.css), diff
    GET  /api/design/projects/{pid}/editor/slides         top-level frames of the current page, in order
    POST /api/design/projects/{pid}/editor/slides/order   {ids, arrange?}
    POST /api/design/projects/{pid}/editor/slides/pdf     {ids?} → application/pdf (vector, one page per slide)

The build is produced by tools/build_open_pencil.py and is never edited by hand.
Serving rules: `.wasm` is `application/wasm` (streaming compile refuses anything
else), content-hashed `assets/*` are immutable, everything else revalidates, and
any extension-less path falls back to index.html (Vue router, history mode).
index.html gets the bridge token injected as a meta tag — the only place the
page learns it, and one a cross-origin page cannot read.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from aiohttp import web

import config
from services.design import canvas_convert, canvas_tokens, editor_bridge, store

logger = logging.getLogger("telecode.proxy.api_design_editor")

EDITOR_DIR = Path(__file__).parent / "static" / "design" / "editor"

_MIME = {
    ".wasm": "application/wasm",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".json": "application/json",
    ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain",
    "": "text/plain",  # NOTICE
}

_IMMUTABLE = "public, max-age=31536000, immutable"
_REVALIDATE = "no-cache"
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._@+-]{1,200}$")


def _resolve(rel: str) -> Optional[Path]:
    """A file inside the editor build, or None. No `..`, no absolute paths."""
    parts = [p for p in rel.split("/") if p]
    if any(p in (".", "..") or not _SAFE_SEGMENT.match(p) for p in parts):
        return None
    path = EDITOR_DIR.joinpath(*parts) if parts else EDITOR_DIR / "index.html"
    try:
        path.resolve().relative_to(EDITOR_DIR.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def _index_response() -> web.StreamResponse:
    index = EDITOR_DIR / "index.html"
    if not index.is_file():
        return web.json_response(
            {"error": "Canvas editor is not built", "hint": "python tools/build_open_pencil.py"},
            status=404)
    html = index.read_text(encoding="utf-8")
    meta = f'<meta name="td-bridge-token" content="{editor_bridge.token()}" />'
    html = html.replace("<head>", f"<head>\n    {meta}", 1) if "<head>" in html else meta + html
    return web.Response(text=html, content_type="text/html", headers={
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
    })


async def serve_editor(request: web.Request) -> web.StreamResponse:
    rel = request.match_info.get("path", "")
    if rel in ("", "index.html"):
        return _index_response()
    path = _resolve(rel)
    if path is None:
        last = rel.rsplit("/", 1)[-1]
        # Extension-less → a client-side route (e.g. /design/editor/demo): SPA fallback.
        # A missing file with an extension is a real 404, never index.html (a
        # wasm/js request answered with HTML fails confusingly far away).
        if "." not in last and not rel.startswith("assets/"):
            return _index_response()
        raise web.HTTPNotFound()
    if path.name == "index.html":
        return _index_response()
    ctype = _MIME.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] \
        or "application/octet-stream"
    cache = _IMMUTABLE if rel.startswith("assets/") else _REVALIDATE
    return web.FileResponse(path, headers={
        "Content-Type": ctype,
        "Cache-Control": cache,
        "X-Content-Type-Options": "nosniff",
    })


async def redirect_editor(request: web.Request) -> web.StreamResponse:
    qs = request.query_string
    raise web.HTTPFound("/design/editor/" + (f"?{qs}" if qs else ""))


# ── Origin guard (same rule as the rest of /api/design) ──────────────

def _own_origin(request: web.Request, origin: str) -> bool:
    try:
        u = urlsplit(origin)
    except ValueError:
        return False
    if u.scheme not in ("http", "https"):
        return False
    port = int(config.get_nested("proxy.port", 1235))
    if u.hostname in ("127.0.0.1", "localhost") and (u.port or 80) == port:
        return True
    # Same host the request was addressed to (e.g. a LAN name for this machine).
    return bool(request.host) and u.netloc == request.host


def _origin_ok(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    return origin is None or _own_origin(request, origin)


# ── Bridge ───────────────────────────────────────────────────────────

async def bridge_ws(request: web.Request) -> web.StreamResponse:
    pid = request.query.get("project", "")
    if not store.valid_id(pid) or not store.get_project(pid):
        return web.json_response({"error": "Project not found"}, status=404)
    # Browsers always send Origin on a WebSocket handshake, and WebSockets are not
    # covered by CORS — so this is what stops a preview page (another port) from
    # posing as the editor.
    if not _origin_ok(request):
        logger.warning("editor bridge: refused WebSocket from origin %s", request.headers.get("Origin"))
        return web.json_response({"error": "Forbidden origin"}, status=403)
    if request.headers.get("Upgrade", "").lower() != "websocket":
        return web.json_response({"error": "Expected a WebSocket upgrade"}, status=400)
    return await editor_bridge.handle_ws(request, pid)


async def bridge_health(request: web.Request) -> web.Response:
    pid = request.query.get("project", "")
    connected = store.valid_id(pid) and editor_bridge.status(pid)["connected"]
    return web.json_response({
        "status": "ok",
        "version": editor_bridge.open_pencil_version() or "0.0.0",
        "authRequired": True,
        "tools": editor_bridge.tools(include_schema=False),
        # Not part of upstream's health shape; the page ignores unknown keys.
        "connected": bool(connected),
    })


async def list_tools(request: web.Request) -> web.Response:
    return web.json_response({
        "open_pencil_version": editor_bridge.open_pencil_version(),
        "tools": editor_bridge.tools(include_schema=True),
    })


async def build_info(request: web.Request) -> web.Response:
    f = EDITOR_DIR / "BUILD_INFO.json"
    if not f.is_file():
        return web.json_response({"error": "Canvas editor is not built"}, status=404)
    return web.json_response(json.loads(f.read_text(encoding="utf-8")))


async def editor_status(request: web.Request) -> web.Response:
    pid = request.match_info["project_id"]
    pdir = store.project_dir(pid) if store.valid_id(pid) else None
    if not pdir:
        return web.json_response({"error": "Project not found"}, status=404)
    # has_canvas lets the editor skip a GET …/canvas that would 404 (and log a
    # console error) for a project that has never been saved.
    return web.json_response({"editor": {**editor_bridge.status(pid),
                                         "has_canvas": (pdir / "doc.fig").is_file()}})


async def editor_call(request: web.Request) -> web.Response:
    pid = request.match_info["project_id"]
    if not _origin_ok(request):
        return web.json_response({"error": "Forbidden origin"}, status=403)
    if request.content_type != "application/json":
        return web.json_response({"error": "Expected application/json"}, status=415)
    if not store.get_project(pid):
        return web.json_response({"error": "Project not found"}, status=404)
    try:
        data = await request.json()
    except Exception:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("tool"), str):
        return web.json_response({"error": "Expected {tool, args?}"}, status=400)
    args = data.get("args") or {}
    if not isinstance(args, dict):
        return web.json_response({"error": "args must be an object"}, status=400)
    try:
        timeout = min(max(float(data.get("timeout") or editor_bridge.RPC_TIMEOUT), 1.0), 120.0)
    except (TypeError, ValueError):
        timeout = editor_bridge.RPC_TIMEOUT
    if data.get("format") == "mcp":
        return web.json_response(await editor_bridge.call_mcp(pid, data["tool"], args, timeout))
    try:
        body = await editor_bridge.call(pid, data["tool"], args, timeout)
    except editor_bridge.EditorBridgeError as exc:
        msg = str(exc)
        code = 409 if msg == editor_bridge.APP_NOT_CONNECTED else 400
        return web.json_response({"ok": False, "error": msg}, status=code)
    return web.json_response(body)


async def _guarded_json(request: web.Request):
    """(pid, body) for a mutating editor route, or an error response."""
    pid = request.match_info["project_id"]
    if not _origin_ok(request):
        return None, web.json_response({"error": "Forbidden origin"}, status=403)
    if request.content_type != "application/json":
        return None, web.json_response({"error": "Expected application/json"}, status=415)
    if not store.get_project(pid):
        return None, web.json_response({"error": "Project not found"}, status=404)
    try:
        data = await request.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        return None, web.json_response({"error": "Expected a JSON object"}, status=400)
    return (pid, data), None


async def register_board(request: web.Request) -> web.Response:
    """Make a frame an HTML board: stamp a stable key on it and add it to boards.json.

    Body: {node_id, src, width?, height?, key?}. boards.json is keyed by the board
    key, not the node id — node ids are session-local in the editor.
    """
    parsed, err = await _guarded_json(request)
    if err:
        return err
    pid, data = parsed
    try:
        board = await editor_bridge.register_board(
            pid, data.get("node_id"), data.get("src"), data.get("width"), data.get("height"),
            data.get("key"))
    except editor_bridge.EditorBridgeError as exc:
        msg = str(exc)
        return web.json_response({"ok": False, "error": msg},
                                 status=409 if msg == editor_bridge.APP_NOT_CONNECTED else 400)
    return web.json_response({"ok": True, "board": board})


async def unregister_board(request: web.Request) -> web.Response:
    pid = request.match_info["project_id"]
    if not _origin_ok(request):
        return web.json_response({"error": "Forbidden origin"}, status=403)
    if not store.get_project(pid):
        return web.json_response({"error": "Project not found"}, status=404)
    try:
        result = await editor_bridge.unregister_board(pid, request.match_info["key"])
    except editor_bridge.EditorBridgeError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    return web.json_response({"ok": True, **result})


# ── Canvas parity: convert, layer preview, tokens, slides (gaps #1 #3 #4 #14) ──

def _bridge_error(exc: Exception) -> web.Response:
    msg = str(exc)
    if isinstance(exc, editor_bridge.EditorBridgeError) and msg == editor_bridge.APP_NOT_CONNECTED:
        return web.json_response({"ok": False, "error": msg}, status=409)
    if isinstance(exc, (editor_bridge.EditorBridgeError, canvas_convert.ConvertError, canvas_tokens.TokensError)):
        return web.json_response({"ok": False, "error": msg}, status=400)
    logger.exception("canvas route failed")
    return web.json_response({"ok": False, "error": msg or exc.__class__.__name__}, status=502)


async def convert(request: web.Request) -> web.Response:
    """{direction: "to-layers", key?|src?, width?, height?} | {direction: "to-html", node_id, path?}"""
    parsed, err = await _guarded_json(request)
    if err:
        return err
    pid, data = parsed
    try:
        if data.get("direction") == "to-layers":
            result = await canvas_convert.to_layers(pid, key=data.get("key"), src=data.get("src"),
                                                    width=data.get("width"), height=data.get("height"),
                                                    name=data.get("name"))
        elif data.get("direction") == "to-html":
            result = await canvas_convert.to_html(pid, data.get("node_id"), data.get("path"))
        else:
            return web.json_response({"ok": False, "error": 'direction must be "to-layers" or "to-html"'},
                                     status=400)
    except Exception as exc:
        return _bridge_error(exc)
    return web.json_response({"ok": True, **result})


async def layer_preview(request: web.Request) -> web.Response:
    parsed, err = await _guarded_json(request)
    if err:
        return err
    pid, data = parsed
    try:
        result = await canvas_convert.layer_preview(pid, data.get("node_id"))
    except editor_bridge.EditorBridgeError as exc:
        # The previewed frame was deleted (or the canvas reloaded, renumbering ids):
        # an expected outcome of a refresh, answered in-band so the pane just closes.
        if str(exc).startswith("Node not found"):
            return web.json_response({"ok": False, "missing": True, "error": str(exc)})
        return _bridge_error(exc)
    except Exception as exc:
        return _bridge_error(exc)
    return web.json_response({"ok": True, **result})


async def tokens_info(request: web.Request) -> web.Response:
    pid = request.match_info["project_id"]
    pdir = store.project_dir(pid) if store.valid_id(pid) else None
    if not pdir:
        return web.json_response({"error": "Project not found"}, status=404)
    try:
        where, tokens = canvas_tokens.load(pdir)
        payload = canvas_tokens.to_collections(tokens)
    except canvas_tokens.TokensError as exc:
        return web.json_response({"ok": False, "available": False, "error": str(exc)})
    return web.json_response({"ok": True, "available": True, **where,
                              "collections": [{"name": c["name"], "modes": c["modes"],
                                               "variables": len(c["variables"])} for c in payload["collections"]],
                              "count": payload["count"], "skipped": payload["skipped"]})


async def tokens_push(request: web.Request) -> web.Response:
    parsed, err = await _guarded_json(request)
    if err:
        return err
    pid, _data = parsed
    try:
        where, tokens = canvas_tokens.load(store.project_dir(pid))
        payload = canvas_tokens.to_collections(tokens)
        body = await editor_bridge.call(pid, "telecode_variables_apply",
                                        {"collections": payload["collections"]}, 60)
    except Exception as exc:
        return _bridge_error(exc)
    return web.json_response({"ok": True, "source": where["json"], "count": payload["count"],
                              "skipped": payload["skipped"], "report": body.get("result") or {}})


async def tokens_pull(request: web.Request) -> web.Response:
    """Write the canvas's variables back into the project's tokens (user-initiated: no approval)."""
    parsed, err = await _guarded_json(request)
    if err:
        return err
    pid, _data = parsed
    try:
        canvas_tokens.load(store.project_dir(pid))   # fail fast before asking the editor
        body = await editor_bridge.call(pid, "telecode_variables_read", {}, 30)
        result = canvas_tokens.pull(store.project_dir(pid), body.get("result") or {})
    except Exception as exc:
        return _bridge_error(exc)
    if result["written"]:
        try:
            from services.design import events
            events.publish(pid, "files", {"changed": result["written"], "origin": "canvas-tokens"})
        except Exception:
            logger.debug("tokens pull: files event not published", exc_info=True)
    return web.json_response({"ok": True, **result})


async def slides_list(request: web.Request) -> web.Response:
    pid = request.match_info["project_id"]
    if not store.valid_id(pid) or not store.get_project(pid):
        return web.json_response({"error": "Project not found"}, status=404)
    args = {"page_id": request.query["page_id"]} if request.query.get("page_id") else {}
    try:
        body = await editor_bridge.call(pid, "telecode_slides_list", args, 20)
    except Exception as exc:
        return _bridge_error(exc)
    return web.json_response({"ok": True, **(body.get("result") or {})})


async def slides_order(request: web.Request) -> web.Response:
    parsed, err = await _guarded_json(request)
    if err:
        return err
    pid, data = parsed
    ids = data.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(ids) > 500:
        return web.json_response({"ok": False, "error": "ids must be a list of node ids"}, status=400)
    try:
        body = await editor_bridge.call(pid, "telecode_slides_reorder",
                                        {"ids": ids, "arrange": bool(data.get("arrange", True))}, 30)
    except Exception as exc:
        return _bridge_error(exc)
    return web.json_response({"ok": True, **(body.get("result") or {})})


async def slides_pdf(request: web.Request) -> web.StreamResponse:
    """{ids?: [...]} → one vector PDF of those slides in order (all slides when omitted)."""
    parsed, err = await _guarded_json(request)
    if err:
        return err
    pid, data = parsed
    ids = data.get("ids")
    try:
        if not ids:
            listing = await editor_bridge.call(pid, "telecode_slides_list", {}, 20)
            ids = [s["id"] for s in (listing.get("result") or {}).get("slides") or [] if isinstance(s, dict)]
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids) or len(ids) > 500:
            return web.json_response({"ok": False, "error": "No slides to export"}, status=400)
        body = await editor_bridge.call(pid, "export_pdf", {"ids": ids}, 120)
    except Exception as exc:
        return _bridge_error(exc)
    result = body.get("result") or {}
    b64 = result.get("base64") if isinstance(result, dict) else None
    if not isinstance(b64, str):
        return web.json_response({"ok": False, "error": "The editor returned no PDF"}, status=502)
    import base64
    data_bytes = base64.b64decode(b64)
    title = re.sub(r"[^A-Za-z0-9._ -]+", "", (store.get_project(pid) or {}).get("title") or "slides").strip() or "slides"
    return web.Response(body=data_bytes, content_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{title[:60]} - slides.pdf"',
        "Cache-Control": "no-store",
    })


def register_routes(app: web.Application):
    app.router.add_get("/design/editor", redirect_editor)
    app.router.add_get("/design/editor/{path:.*}", serve_editor)
    app.router.add_get("/api/design/editor-bridge", bridge_ws)
    app.router.add_get("/api/design/editor-bridge/health", bridge_health)
    app.router.add_get("/api/design/editor/tools", list_tools)
    app.router.add_get("/api/design/editor/build-info", build_info)
    app.router.add_get("/api/design/projects/{project_id}/editor", editor_status)
    app.router.add_post("/api/design/projects/{project_id}/editor/call", editor_call)
    # Aliases used by mcp_server/tools/design.py (design_canvas_call).
    app.router.add_get("/api/design/canvas/tools", list_tools)
    app.router.add_post("/api/design/projects/{project_id}/canvas/call", editor_call)
    app.router.add_post("/api/design/projects/{project_id}/editor/boards", register_board)
    app.router.add_delete("/api/design/projects/{project_id}/editor/boards/{key}", unregister_board)
    P = "/api/design/projects/{project_id}/editor"
    app.router.add_post(P + "/convert", convert)
    app.router.add_post(P + "/layer-preview", layer_preview)
    app.router.add_get(P + "/tokens", tokens_info)
    app.router.add_post(P + "/tokens/push", tokens_push)
    app.router.add_post(P + "/tokens/pull", tokens_pull)
    app.router.add_get(P + "/slides", slides_list)
    app.router.add_post(P + "/slides/order", slides_order)
    app.router.add_post(P + "/slides/pdf", slides_pdf)
