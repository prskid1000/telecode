"""TeleDesign export jobs: html · zip · pdf · pptx · png · mp4 · handoff.

`start(pid, kind, body)` validates, records a job and runs it as a background
task on the caller's event loop; the UI polls `get_job()`. Results live under
`data/design/exports/<job_id>/` (`job.json` + the result file) and are removed
24 h after the job was created (`cleanup()`, run on every start and hourly).

Standalone HTML (`build_standalone`) inlines every stylesheet, script, image,
font and media file the page references — project files straight from disk,
remote CDN resources fetched **once** through `proxy/media_fetch.py` (the SSRF
boundary: page sources are agent-written, i.e. untrusted) and cached under
`data/design/.cdn-cache/`. SRI hashes are checked before a remote script is
inlined. A `<template id="__bundler_thumbnail">` splash (a small screenshot of
the page) shows while the inlined bundle parses and fades out on load.

Everything that renders goes through `render.py` against the §5 preview origin.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html as html_mod
import io
import json
import logging
import mimetypes
import posixpath
import re
import shutil
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urljoin, urlparse

import config
from services.design import render, store

log = logging.getLogger("telecode.services.design.export")

KINDS = ("html", "zip", "pdf", "pptx", "png", "mp4", "handoff")
MAX_AGE_SEC = 24 * 3600
_EXCLUDE_DIRS = {".versions", ".td", "chats"}
_RUNTIME_DIR = Path(__file__).parent / "runtime"
_STARTERS_DIR = Path(__file__).parent / "starters"
_VIRTUAL_ORIGIN = "http://td.invalid"
_REMOTE_MAX_BYTES = 32 * 1024 * 1024
_INLINE_MAX_BYTES = 48 * 1024 * 1024


def exports_dir() -> Path:
    return store.base_dir() / "exports"


def cdn_cache_dir() -> Path:
    return store.base_dir() / ".cdn-cache"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Jobs ─────────────────────────────────────────────────────────────────

_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_tasks: set = set()
_sems: "Dict[int, asyncio.Semaphore]" = {}
_janitor_loops: set = set()


class ExportError(ValueError):
    """Bad request for an export (unknown kind, bad file, …) — maps to HTTP 400."""


def _valid_job_id(job_id: str) -> bool:
    return store.valid_id(job_id)


def _job_dir(job_id: str) -> Path:
    return exports_dir() / job_id


def _save_job(job: Dict[str, Any]) -> None:
    d = _job_dir(job["id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "job.json.tmp"
    tmp.write_text(json.dumps(job, indent=2), encoding="utf-8")
    tmp.replace(d / "job.json")


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    if not _valid_job_id(job_id):
        return None
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job:
        return job
    try:
        return json.loads((_job_dir(job_id) / "job.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def list_jobs(pid: str) -> List[Dict[str, Any]]:
    out = []
    d = exports_dir()
    if d.exists():
        for jd in d.iterdir():
            job = get_job(jd.name) if jd.is_dir() else None
            if job and job.get("pid") == pid:
                out.append(public_job(job))
    out.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return out


def result_path(job: Dict[str, Any]) -> Optional[Path]:
    if job.get("status") != "done" or not job.get("result"):
        return None
    p = _job_dir(job["id"]) / job["result"]
    return p if p.is_file() else None


def public_job(job: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: job.get(k) for k in ("id", "pid", "kind", "file", "status", "progress", "message", "error",
                                   "flags", "stats", "filename", "size", "created_at", "finished_at")}
    out["job_id"] = job["id"]
    if job.get("status") == "done" and job.get("result"):
        out["download_url"] = f"/api/design/projects/{job['pid']}/export/download/{job['id']}"
    for k in ("bundle_path", "prompt"):
        if job.get(k):
            out[k] = job[k]
    return out


def cleanup(max_age_sec: float = MAX_AGE_SEC) -> int:
    """Remove export job dirs older than `max_age_sec` (by creation time in job.json, else mtime)."""
    d = exports_dir()
    if not d.exists():
        return 0
    removed = 0
    now = time.time()
    for jd in d.iterdir():
        if not jd.is_dir():
            continue
        created = None
        try:
            job = json.loads((jd / "job.json").read_text(encoding="utf-8"))
            if job.get("status") in ("queued", "running") and jd.name in _jobs:
                continue
            created = job.get("created_ts")
        except Exception:
            pass
        if created is None:
            try:
                created = jd.stat().st_mtime
            except OSError:
                continue
        if now - float(created) > max_age_sec:
            shutil.rmtree(jd, ignore_errors=True)
            with _jobs_lock:
                _jobs.pop(jd.name, None)
            removed += 1
    return removed


async def _janitor() -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            await asyncio.to_thread(cleanup)
        except Exception:
            log.exception("export: cleanup failed")


def _sem() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    s = _sems.get(id(loop))
    if s is None:
        s = _sems[id(loop)] = asyncio.Semaphore(max(1, int(config.get_nested("design.export.max_jobs", 2))))
    return s


class JobCtx:
    """What a kind handler gets: the job, validated inputs and progress/flag helpers."""

    def __init__(self, job: Dict[str, Any], pdir: Path, files: List[str], options: Dict[str, Any]) -> None:
        self.job = job
        self.pid: str = job["pid"]
        self.pdir = pdir
        self.files = files
        self.file = files[0] if files else None
        self.options = options
        self.out_dir = _job_dir(job["id"])
        self.project = store.get_project(self.pid) or {}

    def progress(self, frac: float, message: Optional[str] = None) -> None:
        self.job["progress"] = round(max(0.0, min(1.0, float(frac))), 3)
        if message is not None:
            self.job["message"] = message
        _save_job(self.job)

    def flag(self, code: str, message: str, **extra: Any) -> None:
        self.job.setdefault("flags", []).append({"code": code, "message": message, **extra})

    def name(self, ext: str, suffix: str = "") -> str:
        base = _slug(self.project.get("title") or "design")
        if self.file and self.file != "index.html" and len(self.files) == 1:
            base += "-" + _slug(self.file.rsplit(".", 1)[0])
        return f"{base}{suffix}.{ext}"

    def url(self, rel: Optional[str] = None) -> str:
        return render.preview_url(self.pid, rel or self.file)


def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s or "").strip("-").lower()
    return (s or "design")[:60]


def _opt_int(options: Dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(options.get(key, default))
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _opt_float(options: Dict[str, Any], key: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(options.get(key, default))
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _resolve_files(pid: str, pdir: Path, body: Dict[str, Any]) -> List[str]:
    files: List[str] = []
    f = body.get("file")
    if f is not None:
        if not isinstance(f, str) or not store.safe_relpath(f) or not f.endswith(".html") or not (pdir / f).is_file():
            raise ExportError("file must be an existing project .html file")
        files.append(f)
    ids = body.get("board_ids")
    if ids:
        if not isinstance(ids, list):
            raise ExportError("board_ids must be a list")
        boards = store.get_boards(pid) or {}
        for bid in ids:
            b = boards.get(bid) if isinstance(bid, str) else None
            if not b:
                raise ExportError(f"unknown board: {str(bid)[:64]}")
            src = b.get("src")
            if src and (pdir / src).is_file() and src not in files:
                files.append(src)
    if not files:
        prim = render.primary_file(pid)
        if prim:
            files.append(prim)
    return files


def start(pid: str, kind: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Validate and launch an export job on the running loop. Raises ExportError."""
    body = body or {}
    if kind not in KINDS:
        raise ExportError(f"unknown export kind: {kind}")
    pdir = store.project_dir(pid)
    if not pdir:
        raise ExportError("project not found")
    options = body.get("options") or {}
    if not isinstance(options, dict):
        raise ExportError("options must be an object")
    files = _resolve_files(pid, pdir, body)
    if kind not in ("zip", "handoff") and not files:
        raise ExportError("project has no HTML file to export")

    try:
        cleanup()
    except Exception:
        log.exception("export: cleanup failed")

    job = {
        "id": uuid.uuid4().hex, "pid": pid, "kind": kind, "file": files[0] if files else None, "files": files,
        "options": options, "status": "queued", "progress": 0.0, "message": "Queued", "error": None,
        "flags": [], "result": None, "filename": None, "size": None,
        "created_at": _now_iso(), "created_ts": time.time(), "finished_at": None,
    }
    with _jobs_lock:
        _jobs[job["id"]] = job
    _save_job(job)
    ctx = JobCtx(job, pdir, files, options)
    loop = asyncio.get_running_loop()
    task = loop.create_task(_run(ctx))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    if id(loop) not in _janitor_loops:
        _janitor_loops.add(id(loop))
        jt = loop.create_task(_janitor())
        _tasks.add(jt)
    return job


async def run_sync(pid: str, kind: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Start a job and wait for it (MCP tools / tests)."""
    job = start(pid, kind, body)
    while job["status"] in ("queued", "running"):
        await asyncio.sleep(0.2)
    return job


async def _run(ctx: JobCtx) -> None:
    job = ctx.job
    async with _sem():
        job["status"] = "running"
        ctx.progress(0.01, "Starting")
        try:
            path = await _HANDLERS[job["kind"]](ctx)
            job["result"] = path.name
            job["filename"] = path.name
            job["size"] = path.stat().st_size
            job["status"] = "done"
            job["progress"] = 1.0
            job["message"] = "Done"
        except Exception as exc:
            log.warning("export %s %s failed: %s", job["kind"], job["id"], exc, exc_info=not isinstance(
                exc, (ExportError, render.RenderError)))
            job["status"] = "failed"
            job["error"] = str(exc) or exc.__class__.__name__
            job["message"] = "Failed"
        job["finished_at"] = _now_iso()
        _save_job(job)


# ── Remote resources (CDN cache, via media_fetch) ────────────────────────

_cdn_locks: Dict[str, asyncio.Lock] = {}


async def cdn_get(url: str) -> bytes:
    """Fetch a remote resource once; afterwards served from data/design/.cdn-cache."""
    from proxy import media_fetch  # SSRF-guarded fetch: page sources are untrusted
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    d = cdn_cache_dir()
    f = d / key
    if f.is_file():
        return f.read_bytes()
    lock = _cdn_locks.setdefault(key, asyncio.Lock())
    async with lock:
        if f.is_file():
            return f.read_bytes()
        data = await media_fetch.fetch_media_bytes(url, max_bytes=_REMOTE_MAX_BYTES)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{key}.tmp"
        tmp.write_bytes(data)
        tmp.replace(f)
        (d / f"{key}.url").write_text(url, encoding="utf-8")
        return data


# ── Standalone HTML ──────────────────────────────────────────────────────

_EXTRA_MIME = {
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf",
    ".svg": "image/svg+xml", ".webp": "image/webp", ".avif": "image/avif", ".jsx": "text/babel",
    ".mjs": "text/javascript", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".ico": "image/x-icon", ".gif": "image/gif", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
}


def _sniff(data: bytes, path: str) -> str:
    ext = posixpath.splitext(urlparse(path).path)[1].lower()
    if ext in _EXTRA_MIME:
        return _EXTRA_MIME[ext]
    head = data[:16]
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head[:4] == b"GIF8":
        return "image/gif"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if head[:4] == b"wOF2":
        return "font/woff2"
    if head[:4] == b"wOFF":
        return "font/woff"
    if head[:4] in (b"\x00\x01\x00\x00", b"true"):
        return "font/ttf"
    if head[:4] == b"OTTO":
        return "font/otf"
    if b"<svg" in data[:512]:
        return "image/svg+xml"
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def _data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


_ATTR_RE = re.compile(r"""([^\s=/>"']+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s"'>]+))?""")


def _parse_attrs(s: str) -> List[List[Optional[str]]]:
    out = []
    for m in _ATTR_RE.finditer(s):
        name, raw = m.group(1), m.group(2)
        if raw is not None and raw[:1] in ("'", '"'):
            raw = raw[1:-1]
        out.append([name, html_mod.unescape(raw) if raw is not None else None])
    return out


def _attr(attrs: List[List[Optional[str]]], name: str) -> Optional[str]:
    for a in attrs:
        if a[0].lower() == name:
            return a[1]
    return None


def _set_attr(attrs: List[List[Optional[str]]], name: str, value: Optional[str]) -> None:
    for a in attrs:
        if a[0].lower() == name:
            a[1] = value
            return
    attrs.append([name, value])


def _drop_attrs(attrs: List[List[Optional[str]]], *names: str) -> List[List[Optional[str]]]:
    return [a for a in attrs if a[0].lower() not in names]


def _render_attrs(attrs: List[List[Optional[str]]]) -> str:
    parts = []
    for name, val in attrs:
        if name == "/":
            continue
        parts.append(name if val is None else f'{name}="{html_mod.escape(val, quote=True)}"')
    return (" " + " ".join(parts)) if parts else ""


class _Inliner:
    def __init__(self, pid: str, pdir: Path) -> None:
        self.pid = pid
        self.pdir = pdir.resolve()
        self.unresolved: List[str] = []
        self.remote: List[str] = []
        self.integrity_failed: List[str] = []
        self.js_refs = 0
        self.total = 0

    # URL model: every reference is resolved against a virtual preview-origin URL,
    # so `../`, root-relative `/p/<pid>/…`, `/_td/…`, `/starters/…` and `/ds/<pid>/…`
    # behave exactly as they do in the live preview, then map back to disk.
    def base_url(self, rel: str) -> str:
        return f"{_VIRTUAL_ORIGIN}/p/{self.pid}/{rel}"

    def _local_path(self, url: str) -> Optional[Path]:
        path = unquote(urlparse(url).path)
        roots = [(f"/p/{self.pid}/", self.pdir), (f"/ds/{self.pid}/", self.pdir / "_ds"),
                 ("/_td/", _RUNTIME_DIR), ("/starters/", _STARTERS_DIR)]
        for prefix, root in roots:
            if path.startswith(prefix):
                rel = path[len(prefix):]
                if not store.safe_relpath(rel):
                    return None
                p = (root / rel).resolve()
                try:
                    p.relative_to(root.resolve())
                except ValueError:
                    return None
                return p if p.is_file() else None
        return None

    async def fetch(self, ref: str, base: str) -> Optional[Tuple[bytes, str, str]]:
        """(bytes, mime, absolute url) or None (left as-is and recorded)."""
        ref = (ref or "").strip()
        if not ref or ref.startswith(("data:", "blob:", "#", "about:", "javascript:", "mailto:", "tel:")):
            return None
        if ref.startswith("//"):
            ref = "https:" + ref
        url = urljoin(base, ref).split("#", 1)[0]
        if url.startswith(_VIRTUAL_ORIGIN + "/"):
            p = self._local_path(url.split("?", 1)[0])
            if p is None:
                self._unresolved(ref)
                return None
            data = p.read_bytes()
        elif urlparse(url).scheme in ("http", "https"):
            try:
                data = await cdn_get(url)
            except Exception as exc:
                log.info("export: remote %s not inlined: %s", url, exc)
                self._unresolved(url)
                return None
            self.remote.append(url)
        else:
            self._unresolved(ref)
            return None
        self.total += len(data)
        if self.total > _INLINE_MAX_BYTES:
            raise ExportError(f"standalone file would exceed {_INLINE_MAX_BYTES // (1024 * 1024)} MB")
        return data, _sniff(data, url), url

    def _unresolved(self, ref: str) -> None:
        if ref not in self.unresolved:
            self.unresolved.append(ref)

    async def data_uri(self, ref: str, base: str) -> Optional[str]:
        got = await self.fetch(ref, base)
        return _data_uri(got[0], got[1]) if got else None

    # CSS
    _URL_RE = re.compile(r"""url\(\s*(['"]?)([^'")]+?)\1\s*\)""", re.I)
    _IMPORT_RE = re.compile(
        r"""@import\s+(?:url\(\s*(['"]?)([^'")]+?)\1\s*\)|(['"])([^'"]+)\3)\s*([^;]*);""", re.I)

    async def css(self, text: str, base: str, depth: int = 0) -> str:
        if depth < 6:
            out, pos = [], 0
            for m in self._IMPORT_RE.finditer(text):
                out.append(text[pos:m.start()])
                got = await self.fetch(m.group(2) or m.group(4), base)
                if got:
                    inner = await self.css(got[0].decode("utf-8", "replace"), got[2], depth + 1)
                    media = m.group(5).strip()
                    out.append(f"@media {media} {{\n{inner}\n}}" if media else inner)
                else:
                    out.append(m.group(0))
                pos = m.end()
            out.append(text[pos:])
            text = "".join(out)
        out, pos = [], 0
        for m in self._URL_RE.finditer(text):
            out.append(text[pos:m.start()])
            ref = m.group(2).strip()
            uri = None if ref.startswith("#") else await self.data_uri(ref, base)
            out.append(f'url("{uri}")' if uri else m.group(0))
            pos = m.end()
        out.append(text[pos:])
        return "".join(out)

    # Asset paths written as string literals in JS/JSX ("assets/logo.png").
    _JS_REF_RE = re.compile(
        r"""(["'`])((?:\.{0,2}/)?[A-Za-z0-9_\-./ ]+\.(?:png|jpe?g|gif|svg|webp|avif|mp4|webm|mp3|wav|woff2?|ttf|otf))\1""",
        re.I)

    async def js_refs_inline(self, code: str, base: str) -> str:
        out, pos = [], 0
        for m in self._JS_REF_RE.finditer(code):
            ref = m.group(2)
            url = urljoin(base, ref)
            if not url.startswith(_VIRTUAL_ORIGIN + "/"):
                continue
            p = self._local_path(url)
            if p is None or p.stat().st_size > 8 * 1024 * 1024:
                continue
            data = p.read_bytes()
            self.total += len(data)
            out.append(code[pos:m.start()])
            out.append(m.group(1) + _data_uri(data, _sniff(data, url)) + m.group(1))
            pos = m.end()
            self.js_refs += 1
        out.append(code[pos:])
        return "".join(out)


def _script_safe(code: str) -> str:
    return re.sub(r"</(script)", r"<\\/\1", code, flags=re.I)


_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.I | re.S)
_STYLE_RE = re.compile(r"<style\b([^>]*)>(.*?)</style\s*>", re.I | re.S)
_LINK_RE = re.compile(r"<link\b([^>]*?)/?>", re.I)
_MEDIA_TAG_RE = re.compile(r"<(img|source|video|audio|input|image|track|embed)\b([^>]*?)(/?)>", re.I)
_STYLE_ATTR_RE = re.compile(r"""\sstyle\s*=\s*("[^"]*url\([^"]*"|'[^']*url\([^']*')""", re.I)
_BODY_OPEN_RE = re.compile(r"<body\b[^>]*>", re.I)


async def _sub_async(pattern: re.Pattern, text: str, fn: Callable[[re.Match], Awaitable[str]]) -> str:
    out, pos = [], 0
    for m in pattern.finditer(text):
        out.append(text[pos:m.start()])
        out.append(await fn(m))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def _integrity_ok(data: bytes, integrity: Optional[str]) -> bool:
    if not integrity:
        return True
    for token in integrity.split():
        algo, _, want = token.partition("-")
        if algo in ("sha256", "sha384", "sha512"):
            got = base64.b64encode(hashlib.new(algo, data).digest()).decode("ascii")
            if got == want:
                return True
    return False


_SPLASH_TEMPLATE = (
    '<template id="__bundler_thumbnail"><div id="__bundler_splash" style="position:fixed;inset:0;'
    'z-index:2147483647;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;'
    'background:#111114;color:#e8e8ec;font:500 14px/1.4 system-ui,sans-serif;transition:opacity .3s ease">'
    '{img}<div style="opacity:.7">{title}</div></div></template>'
    "<script>(function(){{var t=document.getElementById('__bundler_thumbnail');if(!t||!t.content)return;"
    "var n=t.content.firstElementChild.cloneNode(true);(document.body||document.documentElement).appendChild(n);"
    "function done(){{setTimeout(function(){{n.style.opacity='0';setTimeout(function(){{n.remove()}},320)}},150)}}"
    "if(document.readyState==='complete')done();else addEventListener('load',done)}})();</script>"
)


async def build_standalone(pid: str, rel: str, *, splash: bool = True,
                           progress: Optional[Callable[[float, str], None]] = None) -> Tuple[str, Dict[str, Any]]:
    """Return (html, report) for a single self-contained file of project page `rel`."""
    pdir = store.project_dir(pid)
    if not pdir or not store.safe_relpath(rel) or not (pdir / rel).is_file():
        raise ExportError("file not found")
    src = (pdir / rel).read_text(encoding="utf-8", errors="replace")
    inl = _Inliner(pid, pdir)
    base = inl.base_url(rel)
    deferred: List[str] = []

    async def on_script(m: re.Match) -> str:
        attrs = _parse_attrs(m.group(1))
        body = m.group(2)
        src_ref = _attr(attrs, "src")
        typ = (_attr(attrs, "type") or "").lower()
        if src_ref is None:
            if typ in ("text/babel", "text/jsx") or typ in ("", "text/javascript", "application/javascript", "module"):
                body = await inl.js_refs_inline(body, base)
            return f"<script{_render_attrs(attrs)}>{body}</script>"
        if typ == "module":
            inl._unresolved(src_ref)  # module graphs are not inlined
            return m.group(0)
        got = await inl.fetch(src_ref, base)
        if not got:
            return m.group(0)
        data, _, url = got
        if not _integrity_ok(data, _attr(attrs, "integrity")):
            inl.integrity_failed.append(url)
            return m.group(0)
        code = data.decode("utf-8", "replace")
        if url.startswith(_VIRTUAL_ORIGIN) and (typ in ("text/babel", "text/jsx") or url.endswith((".jsx", ".js"))):
            code = await inl.js_refs_inline(code, url)
        is_defer = _attr(attrs, "defer") is not None or any(a[0].lower() == "defer" for a in attrs)
        attrs = _drop_attrs(attrs, "src", "integrity", "crossorigin", "defer", "async", "referrerpolicy")
        tag = f"<script{_render_attrs(attrs)} data-td-inlined=\"{html_mod.escape(src_ref)}\">{_script_safe(code)}</script>"
        if is_defer and typ not in ("text/babel", "text/jsx"):
            deferred.append(tag)
            return ""
        return tag

    async def on_style(m: re.Match) -> str:
        return f"<style{m.group(1)}>{await inl.css(m.group(2), base)}</style>"

    async def on_link(m: re.Match) -> str:
        attrs = _parse_attrs(m.group(1))
        rels = (_attr(attrs, "rel") or "").lower().split()
        href = _attr(attrs, "href")
        if not href:
            return m.group(0)
        if "stylesheet" in rels:
            got = await inl.fetch(href, base)
            if not got:
                return m.group(0)
            if not _integrity_ok(got[0], _attr(attrs, "integrity")):
                inl.integrity_failed.append(got[2])
                return m.group(0)
            css = await inl.css(got[0].decode("utf-8", "replace"), got[2])
            media = _attr(attrs, "media")
            return f"<style{' media=' + chr(34) + html_mod.escape(media) + chr(34) if media else ''}>{css}</style>"
        if any(r in rels for r in ("preconnect", "dns-prefetch", "preload", "prefetch", "modulepreload")):
            return ""
        if any(r in rels for r in ("icon", "apple-touch-icon", "shortcut")):
            uri = await inl.data_uri(href, base)
            if uri:
                _set_attr(attrs, "href", uri)
                return f"<link{_render_attrs(attrs)}>"
        return m.group(0)

    async def on_media(m: re.Match) -> str:
        tag, attrs = m.group(1), _parse_attrs(m.group(2))
        changed = False
        for name in ("src", "poster", "href", "xlink:href", "data-src"):
            v = _attr(attrs, name)
            if v and not v.startswith("#"):
                uri = await inl.data_uri(v, base)
                if uri:
                    _set_attr(attrs, name, uri)
                    changed = True
        srcset = _attr(attrs, "srcset")
        if srcset:
            parts = []
            for cand in srcset.split(","):
                bits = cand.strip().split()
                if bits:
                    uri = await inl.data_uri(bits[0], base)
                    parts.append(" ".join([uri or bits[0]] + bits[1:]))
            _set_attr(attrs, "srcset", ", ".join(parts))
            changed = True
        if not changed:
            return m.group(0)
        return f"<{tag}{_render_attrs(attrs)}{' /' if m.group(3) else ''}>"

    async def on_style_attr(m: re.Match) -> str:
        raw = m.group(1)
        q = raw[0]
        css = html_mod.unescape(raw[1:-1])
        new = await inl.css(css, base)
        return f" style={q}{new.replace(q, '&quot;' if q == chr(34) else '&#39;')}{q}"

    if progress:
        progress(0.1, "Inlining scripts")
    # Scripts first, and nothing else touches their bodies afterwards.
    placeholders: Dict[str, str] = {}

    async def stash_script(m: re.Match) -> str:
        out = await on_script(m)
        key = f"\x00TDSCRIPT{len(placeholders)}\x00"
        placeholders[key] = out
        return key

    doc = await _sub_async(_SCRIPT_RE, src, stash_script)
    if progress:
        progress(0.4, "Inlining styles and fonts")
    doc = await _sub_async(_STYLE_RE, doc, on_style)
    doc = await _sub_async(_LINK_RE, doc, on_link)
    if progress:
        progress(0.6, "Inlining images")
    doc = await _sub_async(_MEDIA_TAG_RE, doc, on_media)
    doc = await _sub_async(_STYLE_ATTR_RE, doc, on_style_attr)
    for key, val in placeholders.items():
        doc = doc.replace(key, val)
    if deferred:
        closing = re.search(r"</body\s*>", doc, re.I)
        blob = "\n".join(deferred)
        doc = doc[:closing.start()] + blob + doc[closing.start():] if closing else doc + blob

    if splash:
        if progress:
            progress(0.8, "Rendering splash thumbnail")
        img = ""
        try:
            png = await render.screenshot(render.preview_url(pid, rel), 1280, 800, fmt="jpeg", quality=70,
                                          hide_selectors=[".deck-controls"])
            from PIL import Image
            im = Image.open(io.BytesIO(png)).convert("RGB")
            im = im.resize((480, max(1, round(im.height * 480 / im.width))), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "WEBP", quality=70)
            img = (f'<img alt="" src="{_data_uri(buf.getvalue(), "image/webp")}" style="width:min(480px,70vw);'
                   f'height:auto;border-radius:10px;box-shadow:0 12px 40px rgba(0,0,0,.45)">')
        except Exception as exc:
            log.info("export: splash thumbnail skipped: %s", exc)
        title = html_mod.escape((store.get_project(pid) or {}).get("title") or rel)
        splash_html = _SPLASH_TEMPLATE.format(img=img, title=title)
        m = _BODY_OPEN_RE.search(doc)
        if m:
            doc = doc[:m.end()] + splash_html + doc[m.end():]
        else:
            doc = splash_html + doc

    report = {"remote_inlined": inl.remote, "unresolved": inl.unresolved,
              "integrity_failed": inl.integrity_failed, "js_asset_refs": inl.js_refs, "bytes": len(doc)}
    return doc, report


def _standalone_flags(ctx: JobCtx, report: Dict[str, Any]) -> None:
    if report["unresolved"]:
        ctx.flag("unresolved_refs", f"{len(report['unresolved'])} reference(s) could not be inlined and still "
                 "need the network or the project folder", refs=report["unresolved"][:20])
    if report["integrity_failed"]:
        ctx.flag("integrity_mismatch", "A remote script failed its SRI check and was left as a remote reference",
                 refs=report["integrity_failed"][:10])


async def _export_html(ctx: JobCtx) -> Path:
    doc, report = await build_standalone(ctx.pid, ctx.file, splash=ctx.options.get("splash", True) is not False,
                                         progress=ctx.progress)
    _standalone_flags(ctx, report)
    out = ctx.out_dir / ctx.name("html")
    out.write_text(doc, encoding="utf-8")
    return out


# ── ZIP ──────────────────────────────────────────────────────────────────

def iter_project_files(pdir: Path, exclude_dirs=_EXCLUDE_DIRS) -> List[Path]:
    out = []
    for p in sorted(pdir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(pdir)
        if rel.parts[0] in exclude_dirs or p.name.endswith(".tmp"):
            continue
        out.append(p)
    return out


async def _export_zip(ctx: JobCtx) -> Path:
    files = iter_project_files(ctx.pdir)
    out = ctx.out_dir / ctx.name("zip")
    prefix = _slug(ctx.project.get("title") or "design")

    def write() -> None:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for i, p in enumerate(files):
                z.write(p, f"{prefix}/{p.relative_to(ctx.pdir).as_posix()}")
                if i % 25 == 0:
                    ctx.progress(0.1 + 0.6 * i / max(1, len(files)), f"Adding {p.name}")

    await asyncio.to_thread(write)
    if ctx.options.get("include_standalone") and ctx.file:
        doc, report = await build_standalone(ctx.pid, ctx.file)
        _standalone_flags(ctx, report)
        with zipfile.ZipFile(out, "a", zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"{prefix}/{ctx.file.rsplit('.', 1)[0]}.standalone.html", doc)
    return out


# ── PDF ──────────────────────────────────────────────────────────────────

def pdf_page_count(data: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", data))


def _deck_print_css(w: int, h: int) -> str:
    return f"""@media print {{
@page {{ size: {w}px {h}px; margin: 0 }}
html, body {{ width: {w}px !important; height: auto !important; overflow: visible !important;
  margin: 0 !important; background: none !important; }}
#deck-stage, [data-td-deck], deck-stage, .deck-canvas {{ position: static !important; transform: none !important;
  width: {w}px !important; height: auto !important; inset: auto !important; overflow: visible !important; }}
[data-td-slide] {{ display: block !important; position: relative !important; inset: auto !important;
  width: {w}px !important; height: {h}px !important; overflow: hidden !important; visibility: visible !important;
  opacity: 1 !important; transform: none !important; page-break-after: always; break-after: page;
  page-break-inside: avoid; break-inside: avoid; }}
[data-td-slide]:last-of-type {{ page-break-after: auto; break-after: auto; }}
.deck-controls, [data-td-deck-controls], #__bundler_splash {{ display: none !important; }}
}}"""


async def _pdf_one(ctx: JobCtx, rel: str, frac0: float, frac1: float) -> bytes:
    mode = ctx.options.get("mode")
    if mode not in ("print", "screen"):
        mode = "print" if ctx.project.get("kind") == "one_pager" else "screen"
    async with render.open_page(1280, 800) as page:
        info = await render.load_for_render(page, ctx.url(rel))
        await page.hide(ctx.options.get("hideSelectors") or [])
        if info.get("is_deck"):
            w, h, count = int(info["w"]), int(info["h"]), int(info["count"])
            ctx.progress(frac0 + (frac1 - frac0) * 0.2, f"Printing {count} slides")
            data = await render.pdf_from_page(page, width_px=w, height_px=h, prefer_css_page_size=False,
                                              extra_css=_deck_print_css(w, h))
            pages = pdf_page_count(data)
            if pages == count:
                return data
            # The deck's layout fought the print CSS (e.g. a shadow-DOM stage): fall back to one
            # raster page per slide so the page count is still exactly one per slide.
            ctx.flag("pdf_raster_fallback", f"Print layout produced {pages} pages for {count} slides; "
                     "exported as one image per slide instead (text is not selectable)", file=rel)
            await page.remove_style("__td_rt_print")
            await page.cmd("Emulation.setEmulatedMedia", {"media": "screen"})
            await page.hide([".deck-controls"] + list(ctx.options.get("hideSelectors") or []))
            from PIL import Image
            imgs = []
            for i in range(1, count + 1):
                await render.deck_go(page, i)
                imgs.append(Image.open(io.BytesIO(await page.capture("png"))).convert("RGB"))
                ctx.progress(frac0 + (frac1 - frac0) * (0.3 + 0.6 * i / count), f"Slide {i}/{count}")
            buf = io.BytesIO()
            imgs[0].save(buf, "PDF", save_all=True, append_images=imgs[1:], resolution=96.0)
            return buf.getvalue()
        if mode == "print":
            size = str(ctx.options.get("page_size") or "Letter").lower()
            dims = {"a4": (793.7, 1122.5), "letter": (816, 1056), "legal": (816, 1344), "a3": (1122.5, 1587.4)}
            w, h = dims.get(size, dims["letter"])
            return await render.pdf_from_page(page, landscape=bool(ctx.options.get("landscape")), width_px=w,
                                              height_px=h, prefer_css_page_size=True,
                                              extra_css="#__bundler_splash{display:none!important}")
        # screen: one tall page exactly as the page looks on screen
        width = _opt_int(ctx.options, "width", 1280, 320, 3840)
        await page.set_viewport(width, 800)
        await page.settle(timeout=3)
        height = await page.eval("Math.max(document.documentElement.scrollHeight, document.body ? "
                                 "document.body.scrollHeight : 0)") or 800
        height = min(int(height), 19000)
        return await render.pdf_from_page(page, width_px=width, height_px=height, prefer_css_page_size=False,
                                          extra_css="@page{margin:0} html,body{margin:0}", media="screen")


async def _export_pdf(ctx: JobCtx) -> Path:
    results = []
    n = len(ctx.files)
    for i, rel in enumerate(ctx.files):
        results.append((rel, await _pdf_one(ctx, rel, i / n, (i + 1) / n)))
    if n == 1:
        out = ctx.out_dir / ctx.name("pdf")
        out.write_bytes(results[0][1])
        return out
    out = ctx.out_dir / ctx.name("zip", "-pdf")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in results:
            z.writestr(rel.rsplit(".", 1)[0] + ".pdf", data)
    return out


# ── PNG / JPEG / WEBP ────────────────────────────────────────────────────

def _board_size(pid: str, rel: str) -> Tuple[int, int]:
    for b in (store.get_boards(pid) or {}).values():
        if isinstance(b, dict) and b.get("src") == rel:
            try:
                return max(64, min(7680, int(b.get("width")))), max(64, min(7680, int(b.get("height"))))
            except (TypeError, ValueError):
                break
    return 1280, 800


async def _export_png(ctx: JobCtx) -> Path:
    fmt = str(ctx.options.get("format") or "png").lower()
    fmt = "jpeg" if fmt == "jpg" else fmt
    if fmt not in ("png", "jpeg", "webp"):
        raise ExportError("format must be png, jpeg or webp")
    ext = "jpg" if fmt == "jpeg" else fmt
    scale = _opt_float(ctx.options, "scale", 1, 1, 3)
    quality = _opt_int(ctx.options, "quality", 90, 1, 100) if fmt != "png" else None
    full_page = bool(ctx.options.get("full_page", False))
    hide = [".deck-controls"] + [s for s in (ctx.options.get("hideSelectors") or []) if isinstance(s, str)]
    images: List[Tuple[str, bytes]] = []
    for fi, rel in enumerate(ctx.files):
        w, h = _board_size(ctx.pid, rel)
        w = _opt_int(ctx.options, "width", w, 64, 7680)
        h = _opt_int(ctx.options, "height", h, 64, 7680)
        async with render.open_page(w, h, scale) as page:
            info = await render.load_for_render(page, ctx.url(rel))
            stem = rel.rsplit(".", 1)[0].replace("/", "_")
            if info.get("is_deck"):
                count = int(info["count"])
                sel = ctx.options.get("slides", "all")
                if isinstance(sel, int):
                    idxs = [sel]
                elif isinstance(sel, list):
                    idxs = [int(x) for x in sel if str(x).isdigit()]
                else:
                    idxs = list(range(1, count + 1))
                idxs = [i for i in idxs if 1 <= i <= count] or [1]
                await page.hide(hide)
                for k, i in enumerate(idxs):
                    await render.deck_go(page, i)
                    images.append((f"{stem}-{i:02d}.{ext}", await page.capture(fmt, quality)))
                    ctx.progress((fi + (k + 1) / len(idxs)) / len(ctx.files), f"Slide {i}")
            else:
                await page.hide(hide[1:])
                await page.run_steps(ctx.options.get("steps"))
                images.append((f"{stem}.{ext}", await page.capture(fmt, quality, full_page=full_page)))
                ctx.progress((fi + 1) / len(ctx.files), rel)
    if len(images) == 1:
        out = ctx.out_dir / ctx.name(ext)
        out.write_bytes(images[0][1])
        return out
    out = ctx.out_dir / ctx.name("zip", f"-{ext}")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for name, data in images:
            z.writestr(name, data)
    return out


# ── Dispatch ─────────────────────────────────────────────────────────────

async def _export_pptx(ctx: JobCtx) -> Path:
    from services.design import pptx_export
    return await pptx_export.export(ctx)


async def _export_mp4(ctx: JobCtx) -> Path:
    from services.design import video_export
    return await video_export.export(ctx)


async def _export_handoff(ctx: JobCtx) -> Path:
    from services.design import handoff
    return await handoff.export(ctx)


_HANDLERS: Dict[str, Callable[[JobCtx], Awaitable[Path]]] = {
    "html": _export_html, "zip": _export_zip, "pdf": _export_pdf, "png": _export_png,
    "pptx": _export_pptx, "mp4": _export_mp4, "handoff": _export_handoff,
}
