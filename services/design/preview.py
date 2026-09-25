"""The preview origin: a second aiohttp site on 127.0.0.1:<design.preview_port>.

Generated pages run here, not on the proxy's origin, so a page — whatever
the model wrote into it — cannot read or write `/api/design/*`: it is
cross-origin to :1235, the CSP pins `connect-src 'self'`, and the mutating
routes reject a foreign `Origin` (docs/teledesign-contract.md §5).

    GET /p/{pid}/{path}      project files; HTML gets the bridge scripts injected
    GET /_td/{name}          runtime scripts (services/design/runtime/, W3)
    GET /starters/{name}     starter scaffolds (services/design/starters/, W3)
    GET /ds/{pid}/{path}     the project's staged `_ds/` (read-only)
    GET /dsys/{sid}/{path}   a design system's package (specimen cards, W5)

The coordinator calls `start_background()` from proxy startup and `stop()` on
shutdown.
"""

from __future__ import annotations

import logging
import mimetypes
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from aiohttp import web

import config
from services.design import store

logger = logging.getLogger("telecode.services.design.preview")

RUNTIME_DIR = Path(__file__).parent / "runtime"
STARTERS_DIR = Path(__file__).parent / "starters"
# Files the page may request but that are host state, never page content.
_HIDDEN_TOP = {".versions", ".td", "chats", "agents"}
_HIDDEN_FILES = {"comments.json", "assets.json", "boards.json"}

_MIME = {
    ".jsx": "application/javascript", ".tsx": "application/javascript", ".mjs": "application/javascript",
    ".js": "application/javascript", ".css": "text/css", ".html": "text/html", ".htm": "text/html",
    ".json": "application/json", ".svg": "image/svg+xml", ".webp": "image/webp", ".wasm": "application/wasm",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
    ".md": "text/plain", ".napkin": "application/json", ".pen": "application/json",
}

_runner: Optional[web.AppRunner] = None


def preview_port() -> int:
    return int(config.get_nested("design.preview_port", 1237))


def preview_origin() -> str:
    return f"http://127.0.0.1:{preview_port()}"


def host_origins() -> list:
    port = config.proxy_port()
    return [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]


def preview_url(pid: str, rel: str, *, host_origin: Optional[str] = None) -> str:
    """URL of a project file on the preview origin, carrying `td_host` for the bridge."""
    if not store.valid_id(pid) or not store.safe_relpath(rel):
        raise ValueError("invalid project id or path")
    host = host_origin or host_origins()[0]
    return f"{preview_origin()}/p/{pid}/{quote(rel, safe='/')}?td_host={quote(host, safe='')}"


def csp() -> str:
    ancestors = " ".join(host_origins())
    return ("default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://cdn.jsdelivr.net "
            "https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' data: https://fonts.gstatic.com; "
            "img-src 'self' data: blob: https:; "
            "media-src 'self' data: blob: https:; "
            "connect-src 'self'; "
            f"frame-ancestors {ancestors}")


@web.middleware
async def _headers_middleware(request: web.Request, handler):
    try:
        resp = await handler(request)
    except web.HTTPException as exc:
        resp = exc
    resp.headers["Content-Security-Policy"] = csp()
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if isinstance(resp, web.HTTPException):
        raise resp
    return resp


def _mime(p: Path) -> str:
    return _MIME.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or "application/octet-stream"


def _file(root: Path, rel: str) -> Path:
    p = store.resolve_in(root, rel)
    if not p or not p.is_file():
        raise web.HTTPNotFound()
    return p


def _flat(directory: Path, name: str) -> Path:
    # Runtime / starter names are flat file names.
    if not re.match(r"^[A-Za-z0-9_.-]{1,120}$", name or "") or name.startswith("."):
        raise web.HTTPNotFound()
    return _file(directory, name)


_HEAD_CLOSE_RE = re.compile(r"</head\s*>", re.I)
_HEAD_OPEN_RE = re.compile(r"<head(\s[^>]*)?>", re.I)
_BABEL_SCRIPT_RE = re.compile(r"""<script\b[^>]*\btype\s*=\s*["']?text/babel""", re.I)


def inject(html_text: str) -> str:
    """Add the host bridge (and the Babel source-stamping plugin when the page
    uses Babel). Only runtime files that exist are injected — a 404ing script
    would itself fail the done gate."""
    tags = []
    # td-bridge.js already carries window.telecode.complete; td-telecode.js is
    # the standalone copy for exported pages, so it is not injected here.
    for name in ("td-bridge.js",):
        if (RUNTIME_DIR / name).is_file():
            tags.append(f'<script src="/_td/{name}"></script>')
    out = html_text
    if tags:
        block = "".join(tags)
        m = _HEAD_OPEN_RE.search(out)
        if m:
            # Early in <head>, so console capture sees the page's own scripts.
            out = out[:m.end()] + block + out[m.end():]
        elif _HEAD_CLOSE_RE.search(out):
            out = _HEAD_CLOSE_RE.sub(lambda mm: block + mm.group(0), out, count=1)
        else:
            out = block + out
    babel = _BABEL_SCRIPT_RE.search(out)
    if babel and (RUNTIME_DIR / "td-babel-source.js").is_file():
        out = out[:babel.start()] + '<script src="/_td/td-babel-source.js"></script>' + out[babel.start():]
    return out


def _serve(p: Path) -> web.StreamResponse:
    if p.suffix.lower() in (".html", ".htm"):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            raise web.HTTPNotFound()
        return web.Response(text=inject(text), content_type="text/html", charset="utf-8")
    resp = web.FileResponse(p)
    resp.content_type = _mime(p)
    return resp


async def serve_project(request: web.Request) -> web.StreamResponse:
    pid = request.match_info["pid"]
    rel = request.match_info.get("path") or "index.html"
    if rel.endswith("/"):
        rel += "index.html"
    root = store.project_dir(pid) if store.valid_id(pid) else None
    if not root:
        raise web.HTTPNotFound()
    top = rel.split("/", 1)[0]
    if top in _HIDDEN_TOP or rel in _HIDDEN_FILES:
        raise web.HTTPNotFound()
    return _serve(_file(root, rel))


async def serve_runtime(request: web.Request) -> web.StreamResponse:
    p = _flat(RUNTIME_DIR, request.match_info["name"])
    resp = web.FileResponse(p)
    resp.content_type = _mime(p)
    return resp


async def serve_starter(request: web.Request) -> web.StreamResponse:
    p = _flat(STARTERS_DIR, request.match_info["name"])
    resp = web.FileResponse(p)
    resp.content_type = _mime(p)
    return resp


async def serve_ds(request: web.Request) -> web.StreamResponse:
    pid = request.match_info["pid"]
    root = store.project_dir(pid) if store.valid_id(pid) else None
    if not root or not (root / "_ds").is_dir():
        raise web.HTTPNotFound()
    return _serve(_file(root / "_ds", request.match_info["path"]))


async def serve_system(request: web.Request) -> web.StreamResponse:
    sid = request.match_info["sid"]
    if not store.get_system(sid):
        raise web.HTTPNotFound()
    return _serve(_file(store.base_dir() / "systems" / sid, request.match_info["path"]))


async def _project_root_redirect(request: web.Request) -> web.StreamResponse:
    pid = request.match_info["pid"]
    if not store.valid_id(pid):
        raise web.HTTPNotFound()
    raise web.HTTPFound(f"/p/{pid}/")


async def _favicon(_request: web.Request) -> web.Response:
    # Browsers ask for it unprompted; a 404 would land in the console and trip
    # the done gate on every page.
    return web.Response(status=204)


def create_app() -> web.Application:
    app = web.Application(middlewares=[_headers_middleware])
    app.router.add_get("/favicon.ico", _favicon)
    app.router.add_get("/p/{pid}/{path:.*}", serve_project)
    app.router.add_get("/p/{pid}", _project_root_redirect)
    app.router.add_get("/_td/{name}", serve_runtime)
    app.router.add_get("/starters/{name}", serve_starter)
    app.router.add_get("/ds/{pid}/{path:.*}", serve_ds)
    app.router.add_get("/dsys/{sid}/{path:.*}", serve_system)
    return app


async def start_background() -> Optional[web.AppRunner]:
    """Start the preview site on 127.0.0.1 only (never exposed on the LAN)."""
    global _runner
    if _runner is not None:
        return _runner
    port = preview_port()
    runner = web.AppRunner(create_app(), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", port).start()
    except OSError as exc:
        logger.error("design preview: cannot listen on 127.0.0.1:%d: %s", port, exc)
        await runner.cleanup()
        return None
    _runner = runner
    logger.info("design preview origin listening on 127.0.0.1:%d", port)
    return runner


async def stop() -> None:
    global _runner
    if _runner is not None:
        await _runner.cleanup()
        _runner = None
