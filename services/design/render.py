"""Headless Edge over CDP — the renderer behind TeleDesign's checks and exports.

One shared browser per telecode process, started lazily on first use and closed
after `design.render.idle_close_sec` (default 300) with no page open:

    msedge --headless=new --remote-debugging-port=0 --user-data-dir=data/design/.edge-profile

Port 0 lets Edge pick a free port; it writes the port and the browser target's
path to `<profile>/DevToolsActivePort`, which is how we find it. CDP is spoken
over one aiohttp WebSocket per event loop in *flattened* session mode (every
page command carries a `sessionId`), so any number of pages share the socket.

The process is bound to telecode's kill-on-close Job Object through
`process.bind_to_lifetime_job` (Edge's helper processes inherit the Job), and
`stop()` / atexit / the idle watcher take the whole tree down with
`process.kill_process_tree`. A crash-orphaned Edge would still hold the profile
lock — a new launch then hands off to it and exits at once — so a launch that
dies without writing DevToolsActivePort sweeps `msedge` processes whose command
line names *our* profile directory and retries once.

Every page this module loads is a §5 preview-origin URL
(`http://127.0.0.1:<design.preview_port>/p/<pid>/<file>`) or a `file://` URL of
an export we wrote — with one exception, `browse()` (the `design_browser` tool),
which opens a caller-supplied URL in a throwaway browser context where Edge makes
no network request of its own: CDP `Fetch` hands every request to the host, which
fetches it under `proxy/media_fetch.py`'s rules (see the section above `browse`).
`screenshot` / `eval_js` / `dom_snapshot` / `print_pdf` accept a URL for
in-process callers only — REST callers must go through `preview_url()`.

Public API (async unless noted):
    screenshot(url, width, height, full_page=False, selector=None, steps=None, scale=1, fmt="png", quality=None)
    console_errors(pid, file) -> list[str]
    thumbnail(pid, file=None) -> Path
    print_pdf(url, landscape=False, width_px=None, height_px=None) -> bytes
    eval_js(url, code, width=1280, height=800) -> any
    dom_snapshot(url, width=1280, height=800) -> dict
    verify(pid, files, screenshots=True, layers=None) -> {"status": "pass"|"issues", "issues": [...],
                                                          "pen_problems": str, ...}
    browse(url, width, height, full_page=False, steps=None) -> {screenshot, outline, blocked, …}
    preview_url(pid, rel) / preview_origin()          (sync)
    stop()                                             (sync)
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import hashlib
import io
import json
import logging
import shutil
import subprocess
import sys
import threading
import time
import weakref
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

import aiohttp

import config
from services.design import store

log = logging.getLogger("telecode.services.design.render")

_EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class RenderError(RuntimeError):
    """Rendering failed (browser missing, navigation failed, page threw in eval, …)."""


# ── Settings (read every time — hot reload) ──────────────────────────────

def _idle_close_sec() -> float:
    return float(config.get_nested("design.render.idle_close_sec", 300))


def _max_pages() -> int:
    return max(1, int(config.get_nested("design.render.max_pages", 4)))


def _settle_timeout() -> float:
    return float(config.get_nested("design.render.settle_timeout_sec", 8))


def preview_origin() -> str:
    return f"http://127.0.0.1:{int(config.get_nested('design.preview_port', 1237))}"


def preview_url(pid: str, rel: str) -> str:
    """The §5 preview-origin URL of a project file. Validates both parts."""
    if not store.valid_id(pid) or not store.safe_relpath(rel):
        raise RenderError("invalid project id or path")
    return f"{preview_origin()}/p/{pid}/{quote(rel, safe='/')}"


def cache_dir(pid: Optional[str] = None) -> Path:
    d = store.base_dir() / ".render-cache"
    return d / pid if pid else d


def _edge_binary() -> str:
    override = config.get_nested("design.render.browser", "") or ""
    for c in ([override] if override else []) + list(_EDGE_CANDIDATES):
        if c and Path(c).is_file():
            return c
    for name in ("msedge", "microsoft-edge", "microsoft-edge-stable", "chromium", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise RenderError("Microsoft Edge not found (set design.render.browser to a Chromium binary)")


# ── The browser process (thread-safe, loop-independent) ──────────────────

class _BrowserProcess:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.proc: Optional[subprocess.Popen] = None
        self.ws_url: Optional[str] = None
        self.active = 0
        self.last_used = time.monotonic()
        self._watcher: Optional[threading.Thread] = None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def ensure(self) -> str:
        with self._lock:
            if self.alive() and self.ws_url:
                return self.ws_url
            self._close_locked()
            try:
                self._launch_locked()
            except RenderError as exc:
                if "exited" not in str(exc):
                    raise
                log.warning("render: Edge launch failed (%s) — sweeping orphans on our profile", exc)
                _sweep_profile_orphans(self._profile())
                self._launch_locked()
            return self.ws_url  # type: ignore[return-value]

    @staticmethod
    def _profile() -> Path:
        return store.base_dir() / ".edge-profile"

    def _launch_locked(self) -> None:
        profile = self._profile()
        profile.mkdir(parents=True, exist_ok=True)
        port_file = profile / "DevToolsActivePort"
        try:
            port_file.unlink()
        except FileNotFoundError:
            pass
        args = [
            _edge_binary(), "--headless=new", "--remote-debugging-port=0",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check", "--disable-extensions",
            "--disable-background-networking", "--disable-sync", "--disable-component-update",
            "--disable-default-apps", "--disable-popup-blocking", "--hide-scrollbars",
            "--mute-audio", "--force-color-profile=srgb", "--font-render-hinting=none",
            "--disable-features=msEdgeSidebarV2,msHubApps,msUndersideButton,Translate,"
            "EdgeCollections,msImplicitSignin",
            "about:blank",
        ]
        self.proc = subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
        try:
            from process import bind_to_lifetime_job
            bind_to_lifetime_job(self.proc.pid, self.proc)
        except Exception as exc:  # pragma: no cover — process.py always importable in telecode
            log.debug("render: Job binding unavailable: %s", exc)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if port_file.exists():
                lines = port_file.read_text(encoding="utf-8", errors="replace").split()
                if len(lines) >= 2:
                    self.ws_url = f"ws://127.0.0.1:{int(lines[0])}{lines[1]}"
                    log.info("render: headless Edge pid %d on %s", self.proc.pid, self.ws_url)
                    self.last_used = time.monotonic()
                    self._start_watcher()
                    return
            if self.proc.poll() is not None:
                code = self.proc.returncode
                self.proc = None
                raise RenderError(f"Edge exited with code {code} before exposing CDP")
            time.sleep(0.05)
        self._close_locked()
        raise RenderError("Edge did not expose CDP within 20 s")

    def _start_watcher(self) -> None:
        if self._watcher and self._watcher.is_alive():
            return
        self._watcher = threading.Thread(target=self._watch, name="td-render-idle", daemon=True)
        self._watcher.start()

    def _watch(self) -> None:
        while True:
            time.sleep(15)
            with self._lock:
                if not self.alive():
                    return
                if self.active <= 0 and time.monotonic() - self.last_used > _idle_close_sec():
                    log.info("render: closing idle headless Edge")
                    self._close_locked()
                    return

    def touch(self, delta: int) -> None:
        with self._lock:
            self.active += delta
            self.last_used = time.monotonic()

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        proc, self.proc, self.ws_url = self.proc, None, None
        if proc is None or proc.poll() is not None:
            return
        try:
            from process import kill_process_tree
            kill_process_tree(proc.pid, force=True, timeout=5.0)
        except Exception:
            pass
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass


def _sweep_profile_orphans(profile: Path) -> None:
    """Kill msedge processes whose command line names our profile dir (never the user's Edge)."""
    if sys.platform != "win32":
        return
    needle = str(profile).replace("'", "''")
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" -ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.CommandLine -and $_.CommandLine.Contains('{needle}') }} | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=15, creationflags=_CREATE_NO_WINDOW,
        ).stdout
    except Exception:
        return
    from process import kill_process_tree
    for tok in out.split():
        if tok.isdigit():
            log.warning("render: killing orphaned headless Edge pid %s", tok)
            kill_process_tree(int(tok), force=True)
    time.sleep(0.5)


_BROWSER = _BrowserProcess()
atexit.register(_BROWSER.close)


def stop() -> None:
    """Close the shared browser now (telecode shutdown)."""
    _BROWSER.close()


# ── CDP connection (one per event loop) ──────────────────────────────────

class _Conn:
    def __init__(self, loop: asyncio.AbstractEventLoop, ws_url: str) -> None:
        self.loop = loop
        self.ws_url = ws_url
        self.http: Optional[aiohttp.ClientSession] = None
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.reader: Optional[asyncio.Task] = None
        self.pending: Dict[int, asyncio.Future] = {}
        self.handlers: Dict[str, Callable[[str, dict], None]] = {}
        self.closed = False
        self._id = 0
        self.sem = asyncio.Semaphore(_max_pages())

    async def open(self) -> None:
        self.http = aiohttp.ClientSession()
        try:
            self.ws = await self.http.ws_connect(self.ws_url, max_msg_size=0, autoping=True)
        except Exception:
            await self.http.close()
            raise
        self.reader = self.loop.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for msg in self.ws:  # type: ignore[union-attr]
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                    continue
                data = json.loads(msg.data)
                if "id" in data:
                    fut = self.pending.pop(data["id"], None)
                    if fut and not fut.done():
                        if "error" in data:
                            err = data["error"]
                            fut.set_exception(RenderError(f"CDP {err.get('message')} ({err.get('code')})"))
                        else:
                            fut.set_result(data.get("result") or {})
                    continue
                handler = self.handlers.get(data.get("sessionId") or "")
                if handler:
                    try:
                        handler(data.get("method", ""), data.get("params") or {})
                    except Exception:  # never let a page handler kill the reader
                        log.exception("render: event handler failed")
        except Exception as exc:
            log.debug("render: CDP reader ended: %s", exc)
        finally:
            self.closed = True
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(RenderError("browser connection closed"))
            self.pending.clear()
            if self.http:
                await self.http.close()

    async def send(self, method: str, params: Optional[dict] = None,
                   session_id: Optional[str] = None, timeout: float = 30.0) -> dict:
        if self.closed or self.ws is None:
            raise RenderError("browser connection closed")
        self._id += 1
        mid = self._id
        msg: Dict[str, Any] = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        fut = self.loop.create_future()
        self.pending[mid] = fut
        await self.ws.send_str(json.dumps(msg))
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self.pending.pop(mid, None)
            raise RenderError(f"CDP {method} timed out after {timeout:.0f}s")


_conns: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Conn]" = weakref.WeakKeyDictionary()
_conn_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()
_conns_guard = threading.Lock()


async def _get_conn() -> _Conn:
    loop = asyncio.get_running_loop()
    with _conns_guard:
        lock = _conn_locks.get(loop)
        if lock is None:
            lock = _conn_locks[loop] = asyncio.Lock()
    async with lock:
        ws_url = await asyncio.to_thread(_BROWSER.ensure)
        with _conns_guard:
            c = _conns.get(loop)
        if c and not c.closed and c.ws_url == ws_url:
            return c
        c = _Conn(loop, ws_url)
        try:
            await c.open()
        except Exception:
            # Stale endpoint (browser died between ensure() and connect): relaunch once.
            _BROWSER.close()
            ws_url = await asyncio.to_thread(_BROWSER.ensure)
            c = _Conn(loop, ws_url)
            await c.open()
        with _conns_guard:
            _conns[loop] = c
        return c


# ── Page ─────────────────────────────────────────────────────────────────

# Runs before any page script: records the time of the last DOM mutation so
# settle() can tell when in-browser Babel / React have finished rendering.
_PRELUDE_JS = r"""
(() => {
  if (window.__tdRender) return; window.__tdRender = true;
  window.__tdLastMut = performance.now();
  try {
    new MutationObserver(() => { window.__tdLastMut = performance.now(); })
      .observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
  } catch (e) {}
})();
"""

_STATE_JS = r"""
(() => ({rs: document.readyState, idle: performance.now() - (window.__tdLastMut || 0),
  fonts: document.fonts ? document.fonts.status : 'loaded',
  timeline: !!(window.tdTimeline && typeof window.tdTimeline.seek === 'function')}))()
"""

_IGNORED_ERROR_SUBSTRINGS = ("favicon.ico", "/_td/")
_TRACKED_RESOURCE_TYPES = ("Image", "Font", "Stylesheet", "Script", "Media")


def _remote_object_text(obj: dict) -> str:
    if "value" in obj:
        v = obj["value"]
        return v if isinstance(v, str) else json.dumps(v)
    return obj.get("description") or obj.get("unserializableValue") or obj.get("type", "")


def _exception_text(details: dict) -> str:
    exc = details.get("exception") or {}
    text = exc.get("description") or details.get("text") or "exception"
    url = details.get("url")
    if url and details.get("lineNumber") is not None:
        text += f" ({url.rsplit('/', 1)[-1]}:{details['lineNumber'] + 1})"
    return text


class Page:
    def __init__(self, conn: _Conn, width: int, height: int, scale: float, offline: bool,
                 context_id: Optional[str] = None) -> None:
        self.conn = conn
        self.context_id = context_id
        self.extra_handler: Optional[Callable[[str, dict], None]] = None
        self.width, self.height, self.scale, self.offline = int(width), int(height), float(scale), offline
        self.sid: Optional[str] = None
        self.target_id: Optional[str] = None
        self.console: List[Dict[str, Any]] = []
        self.failed_resources: List[Dict[str, Any]] = []
        self._requests: Dict[str, Dict[str, str]] = {}
        self._inflight: set = set()
        self._last_net = time.monotonic()
        self._load = asyncio.Event()

    async def open(self) -> None:
        params: Dict[str, Any] = {"url": "about:blank"}
        if self.context_id:
            params["browserContextId"] = self.context_id
        r = await self.conn.send("Target.createTarget", params)
        self.target_id = r["targetId"]
        r = await self.conn.send("Target.attachToTarget", {"targetId": self.target_id, "flatten": True})
        self.sid = r["sessionId"]
        self.conn.handlers[self.sid] = self._on_event
        for m in ("Page.enable", "Runtime.enable", "Network.enable", "Log.enable"):
            await self.cmd(m)
        await self.set_viewport(self.width, self.height, self.scale)
        await self.cmd("Page.addScriptToEvaluateOnNewDocument", {"source": _PRELUDE_JS})
        if self.offline:
            await self.cmd("Network.emulateNetworkConditions", {
                "offline": True, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1})

    async def close(self) -> None:
        if self.sid:
            self.conn.handlers.pop(self.sid, None)
        if self.target_id and not self.conn.closed:
            try:
                await self.conn.send("Target.closeTarget", {"targetId": self.target_id}, timeout=10)
            except Exception:
                pass

    async def cmd(self, method: str, params: Optional[dict] = None, timeout: float = 30.0) -> dict:
        return await self.conn.send(method, params, self.sid, timeout)

    async def set_viewport(self, width: int, height: int, scale: float = 1.0) -> None:
        self.width, self.height, self.scale = int(width), int(height), float(scale)
        await self.cmd("Emulation.setDeviceMetricsOverride", {
            "width": self.width, "height": self.height, "deviceScaleFactor": self.scale, "mobile": False})

    # events
    def _on_event(self, method: str, p: dict) -> None:
        if self.extra_handler is not None and method.startswith("Fetch."):
            self.extra_handler(method, p)
            return
        if method == "Page.loadEventFired":
            self._load.set()
        elif method == "Runtime.consoleAPICalled":
            level = {"assert": "error", "warning": "warn"}.get(p.get("type"), p.get("type"))
            if level in ("error", "warn"):
                text = " ".join(_remote_object_text(a) for a in p.get("args") or [])
                self.console.append({"level": level, "text": text, "source": "console"})
        elif method == "Runtime.exceptionThrown":
            self.console.append({"level": "error", "text": _exception_text(p.get("exceptionDetails") or {}),
                                 "source": "exception"})
        elif method == "Log.entryAdded":
            e = p.get("entry") or {}
            if e.get("level") == "error":
                text = e.get("text", "")
                if e.get("url"):
                    text += f" ({e['url']})"
                self.console.append({"level": "error", "text": text, "source": e.get("source", "log")})
        elif method == "Network.requestWillBeSent":
            rid = p.get("requestId")
            rtype = p.get("type", "")
            self._requests[rid] = {"url": (p.get("request") or {}).get("url", ""), "type": rtype}
            if rtype not in ("EventSource", "WebSocket"):
                self._inflight.add(rid)
            self._last_net = time.monotonic()
        elif method == "Network.responseReceived":
            resp = p.get("response") or {}
            if int(resp.get("status") or 0) >= 400 and p.get("type") in _TRACKED_RESOURCE_TYPES:
                self.failed_resources.append({"url": resp.get("url", ""), "type": p.get("type"),
                                              "error": f"HTTP {resp.get('status')}"})
        elif method in ("Network.loadingFinished", "Network.loadingFailed"):
            rid = p.get("requestId")
            self._inflight.discard(rid)
            self._last_net = time.monotonic()
            if method == "Network.loadingFailed" and not p.get("canceled"):
                req = self._requests.get(rid) or {}
                if p.get("type", req.get("type")) in _TRACKED_RESOURCE_TYPES:
                    self.failed_resources.append({"url": req.get("url", ""), "type": p.get("type", req.get("type")),
                                                  "error": p.get("errorText", "failed")})

    @property
    def errors(self) -> List[str]:
        out = []
        for c in self.console:
            if c["level"] != "error":
                continue
            if any(s in c["text"] for s in _IGNORED_ERROR_SUBSTRINGS):
                continue
            out.append(c["text"])
        return out

    # navigation
    async def goto(self, url: str, timeout: float = 30.0, settle: bool = True) -> None:
        self._load.clear()
        r = await self.cmd("Page.navigate", {"url": url}, timeout=timeout)
        if r.get("errorText"):
            raise RenderError(f"navigation to {url} failed: {r['errorText']}")
        try:
            await asyncio.wait_for(self._load.wait(), timeout)
        except asyncio.TimeoutError:
            log.warning("render: load event timed out for %s — continuing", url)
        if settle:
            await self.settle()

    async def settle(self, quiet_ms: float = 400, timeout: Optional[float] = None) -> None:
        deadline = time.monotonic() + (timeout if timeout is not None else _settle_timeout())
        while True:
            try:
                st = await self.eval(_STATE_JS, await_promise=False, timeout=10) or {}
            except RenderError:
                st = {}
            net_quiet = not self._inflight and time.monotonic() - self._last_net > 0.3
            dom_quiet = st.get("idle", 0) > quiet_ms or st.get("timeline")
            if st.get("rs") == "complete" and st.get("fonts") == "loaded" and net_quiet and dom_quiet:
                return
            if time.monotonic() > deadline:
                return
            await asyncio.sleep(0.1)

    async def eval(self, expr: str, await_promise: bool = True, timeout: float = 30.0) -> Any:
        r = await self.cmd("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": await_promise, "userGesture": True,
        }, timeout=timeout)
        if r.get("exceptionDetails"):
            raise RenderError("page script failed: " + _exception_text(r["exceptionDetails"]))
        return (r.get("result") or {}).get("value")

    async def call(self, fn_src: str, *args: Any, timeout: float = 30.0) -> Any:
        """Evaluate `(fn_src)(...args)` with JSON-serialisable args."""
        return await self.eval(f"({fn_src})(...{json.dumps(list(args))})", timeout=timeout)

    async def add_style(self, css: str, style_id: str = "__td_rt_style") -> None:
        await self.call(
            "(id, css) => { let s = document.getElementById(id); if (!s) { s = document.createElement('style');"
            " s.id = id; s.setAttribute('data-td-rt', ''); (document.head || document.documentElement).appendChild(s); }"
            " s.textContent = css; }", style_id, css)

    async def remove_style(self, style_id: str = "__td_rt_style") -> None:
        await self.call("(id) => { const s = document.getElementById(id); if (s) s.remove(); }", style_id)

    async def hide(self, selectors: List[str], style_id: str = "__td_rt_hide") -> None:
        sels = [s for s in (selectors or []) if isinstance(s, str) and s.strip() and "{" not in s and "}" not in s]
        if sels:
            await self.add_style(",".join(sels) + "{visibility:hidden !important}", style_id)

    async def frame(self) -> None:
        await self.eval("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))", timeout=10)

    async def capture(self, fmt: str = "png", quality: Optional[int] = None, clip: Optional[dict] = None,
                      full_page: bool = False) -> bytes:
        params: Dict[str, Any] = {"format": fmt if fmt in ("png", "jpeg", "webp") else "png",
                                  "fromSurface": True}
        if quality is not None and params["format"] != "png":
            params["quality"] = max(1, min(100, int(quality)))
        if full_page and clip is None:
            size = await self.eval(
                "(() => { const d = document.documentElement, b = document.body || d;"
                " return [Math.max(d.scrollWidth, b.scrollWidth, innerWidth),"
                " Math.min(Math.max(d.scrollHeight, b.scrollHeight, innerHeight), 16384)]; })()")
            clip = {"x": 0, "y": 0, "width": size[0], "height": size[1], "scale": 1}
        if clip is not None:
            params["clip"] = {**clip, "scale": clip.get("scale", 1)}
            params["captureBeyondViewport"] = True
        r = await self.cmd("Page.captureScreenshot", params, timeout=60)
        return base64.b64decode(r["data"])

    async def run_steps(self, steps: Optional[List[dict]]) -> None:
        """Pre-capture interaction: [{"eval": js} | {"click": selector} | {"wait": ms} | {"key": name}
        | {"slide": n} | {"scroll": y}]."""
        for step in (steps or [])[:100]:
            if not isinstance(step, dict):
                continue
            if "code" in step and "eval" not in step:  # W7 MCP spelling: {"code", "delay"}
                step = {**step, "eval": step["code"]}
            if "delay" in step and "wait" not in step:
                step = {**step, "wait": step["delay"]}
            if "eval" in step:
                code = str(step["eval"])
                if "return" in code:
                    code = "(async () => {\n" + code + "\n})()"
                await self.eval(code)
            elif "click" in step:
                box = await self.call(
                    "(sel) => { const el = document.querySelector(sel); if (!el) return null;"
                    " el.scrollIntoView({block: 'center'}); const r = el.getBoundingClientRect();"
                    " return [r.x + r.width / 2, r.y + r.height / 2]; }", str(step["click"]))
                if box:
                    for t in ("mousePressed", "mouseReleased"):
                        await self.cmd("Input.dispatchMouseEvent", {"type": t, "x": box[0], "y": box[1],
                                                                   "button": "left", "clickCount": 1})
            elif "key" in step:
                key = str(step["key"])
                for t in ("keyDown", "keyUp"):
                    await self.cmd("Input.dispatchKeyEvent", {"type": t, "key": key, "code": key,
                                                             "windowsVirtualKeyCode": _VK.get(key, 0)})
            elif "slide" in step:
                await deck_go(self, int(step["slide"]))
            elif "scroll" in step:
                await self.eval(f"window.scrollTo(0, {float(step['scroll'])})")
            if "wait" in step:
                await asyncio.sleep(min(float(step["wait"]), 10000) / 1000.0)
            await self.frame()


_VK = {"ArrowRight": 39, "ArrowLeft": 37, "ArrowUp": 38, "ArrowDown": 40, "Enter": 13, "Escape": 27,
       " ": 32, "Space": 32, "Tab": 9, "PageDown": 34, "PageUp": 33, "Home": 36, "End": 35}


@asynccontextmanager
async def open_page(width: int = 1280, height: int = 800, scale: float = 1.0, offline: bool = False):
    conn = await _get_conn()
    _BROWSER.touch(+1)
    try:
        async with conn.sem:
            page = Page(conn, width, height, scale, offline)
            try:
                await page.open()
                yield page
            finally:
                await page.close()
    finally:
        _BROWSER.touch(-1)


# ── Deck helpers (prompts/deck.md contract; also W3's <deck-stage>) ──────

_DECK_JS = r"""
(() => {
  if (window.__tdDeck) return true;
  const find = () => {
    const stage = document.querySelector('#deck-stage, [data-td-deck], deck-stage');
    let slides = [...document.querySelectorAll('[data-td-slide]')];
    if (!slides.length && stage) {
      const root = stage.querySelector('.deck-canvas') || stage;
      slides = [...root.children].filter(e => e.tagName === 'SECTION');
    }
    return {stage, slides};
  };
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity < 0.05) return false;
    const r = el.getBoundingClientRect(); return r.width > 1 && r.height > 1;
  };
  const notes = () => {
    const n = document.getElementById('td-speaker-notes') || document.getElementById('speaker-notes');
    if (!n) return null;
    try {
      const v = JSON.parse(n.textContent);
      return Array.isArray(v) ? v.map(x => typeof x === 'string' ? x : ((x && (x.notes || x.text)) || '')) : null;
    } catch (e) { return null; }
  };
  const counter = (stage) => {
    const c = (stage || document).querySelector('.deck-count, [data-td-deck-count]');
    return c ? c.textContent.replace(/\s+/g, ' ').trim() : null;
  };
  window.__tdDeck = {
    slides() { return find().slides; },
    info() {
      const {stage, slides} = find();
      if (!slides.length) return {is_deck: false, count: 0};
      const num = (v) => +v || 0;
      const W = num(stage && (stage.dataset.tdW || stage.getAttribute('width'))) || 1920;
      const H = num(stage && (stage.dataset.tdH || stage.getAttribute('height'))) || 1080;
      return {is_deck: true, count: slides.length, w: W, h: H,
        labels: slides.map(s => s.getAttribute('data-td-screen') || s.getAttribute('data-label') || ''),
        notes: notes(), counter: counter(stage)};
    },
    async go(n) {
      const {stage, slides} = find();
      if (!slides.length) return {ok: false, forced: false, counter: null};
      document.querySelectorAll('[data-td-rt-show],[data-td-rt-hide]').forEach(e => {
        e.removeAttribute('data-td-rt-show'); e.removeAttribute('data-td-rt-hide'); });
      window.postMessage({type: 'td:slide', action: 'go', index: n}, '*');
      await new Promise(r => setTimeout(r, 80));
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      const target = slides[n - 1];
      const others = slides.map((s, i) => i).filter(i => i !== n - 1 && visible(slides[i])).map(i => i + 1);
      const shown = visible(target);
      const ok = shown && !others.length;
      if (!ok) {
        if (!document.getElementById('__td_rt_force')) {
          const st = document.createElement('style'); st.id = '__td_rt_force'; st.setAttribute('data-td-rt', '');
          st.textContent = '[data-td-rt-hide]{display:none !important}' +
            '[data-td-rt-show]{display:block !important;visibility:visible !important;opacity:1 !important}';
          document.head.appendChild(st);
        }
        slides.forEach((s, i) => s.setAttribute(i === n - 1 ? 'data-td-rt-show' : 'data-td-rt-hide', ''));
        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      }
      const r = target.getBoundingClientRect();
      return {ok, forced: !ok, shown, others, counter: counter(stage), rect: [r.x, r.y, r.width, r.height],
              size: [target.offsetWidth, target.offsetHeight]};
    },
  };
  return true;
})()
"""


async def deck_info(page: Page) -> Dict[str, Any]:
    await page.eval(_DECK_JS, await_promise=False)
    return await page.eval("window.__tdDeck.info()", await_promise=False) or {"is_deck": False, "count": 0}


async def deck_go(page: Page, n: int, settle_ms: int = 350) -> Dict[str, Any]:
    await page.eval(_DECK_JS, await_promise=False)
    res = await page.eval(f"window.__tdDeck.go({int(n)})") or {}
    if settle_ms:
        await asyncio.sleep(settle_ms / 1000.0)  # entry transitions
    return res


async def load_for_render(page: Page, url: str) -> Dict[str, Any]:
    """Navigate, settle, and resize the viewport to the deck canvas when the page is a deck."""
    await page.goto(url)
    info = await deck_info(page)
    if info.get("is_deck") and (page.width, page.height) != (info["w"], info["h"]):
        await page.set_viewport(info["w"], info["h"], page.scale)
        await page.settle(timeout=3)
    return info


# ── Public API ───────────────────────────────────────────────────────────

async def screenshot(url: str, width: int = 1280, height: int = 800, full_page: bool = False,
                     selector: Optional[str] = None, steps: Optional[List[dict]] = None, scale: float = 1,
                     fmt: str = "png", quality: Optional[int] = None,
                     hide_selectors: Optional[List[str]] = None) -> bytes:
    scale = max(0.25, min(3.0, float(scale or 1)))
    async with open_page(width, height, scale) as page:
        await page.goto(url)
        await page.hide(hide_selectors or [])
        await page.run_steps(steps)
        clip = None
        if selector:
            box = await page.call(
                "(sel) => { const el = document.querySelector(sel); if (!el) return null;"
                " const r = el.getBoundingClientRect(); return {x: r.x + scrollX, y: r.y + scrollY,"
                " width: Math.max(1, r.width), height: Math.max(1, r.height)}; }", selector)
            if not box:
                raise RenderError(f"selector not found: {selector}")
            clip = box
        return await page.capture(fmt, quality, clip=clip, full_page=full_page)


async def console_errors(pid: str, file: str) -> List[str]:
    """Errors from a headless load of one project file: exceptions, console.error, failed loads."""
    url = preview_url(pid, file)
    async with open_page(1280, 800) as page:
        await page.goto(url)
        errs = list(page.errors)
    seen, out = set(), []
    for e in errs:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


async def print_pdf(url: str, landscape: bool = False, width_px: Optional[float] = None,
                    height_px: Optional[float] = None, prefer_css_page_size: bool = True,
                    extra_css: Optional[str] = None, media: str = "print") -> bytes:
    async with open_page(int(width_px or 1280), int(height_px or 800)) as page:
        await page.goto(url)
        return await pdf_from_page(page, landscape, width_px, height_px, prefer_css_page_size, extra_css, media)


async def pdf_from_page(page: Page, landscape: bool = False, width_px: Optional[float] = None,
                        height_px: Optional[float] = None, prefer_css_page_size: bool = True,
                        extra_css: Optional[str] = None, media: str = "print") -> bytes:
    if extra_css:
        await page.add_style(extra_css, "__td_rt_print")
    await page.cmd("Emulation.setEmulatedMedia", {"media": media})
    params: Dict[str, Any] = {
        "landscape": bool(landscape), "printBackground": True, "preferCSSPageSize": prefer_css_page_size,
        "marginTop": 0, "marginBottom": 0, "marginLeft": 0, "marginRight": 0,
        "transferMode": "ReturnAsBase64",
    }
    if width_px and height_px:
        params["paperWidth"] = float(width_px) / 96.0
        params["paperHeight"] = float(height_px) / 96.0
    r = await page.cmd("Page.printToPDF", params, timeout=120)
    return base64.b64decode(r["data"])


async def eval_js(url: str, code: str, width: int = 1280, height: int = 800) -> Any:
    """Load `url` and evaluate `code` (an expression; promises are awaited)."""
    async with open_page(width, height) as page:
        await page.goto(url)
        return await page.eval(code)


_DOM_SNAPSHOT_JS = r"""
(maxNodes) => {
  let count = 0;
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'HEAD', 'META', 'LINK', 'TITLE']);
  const pick = (cs) => ({
    display: cs.display, position: cs.position, color: cs.color, background_color: cs.backgroundColor,
    background_image: cs.backgroundImage === 'none' ? null : cs.backgroundImage.slice(0, 500),
    font_family: cs.fontFamily, font_size: parseFloat(cs.fontSize), font_weight: cs.fontWeight,
    font_style: cs.fontStyle, line_height: cs.lineHeight, letter_spacing: cs.letterSpacing,
    text_align: cs.textAlign, text_transform: cs.textTransform, border_radius: cs.borderRadius,
    border_width: cs.borderTopWidth, border_color: cs.borderTopColor, border_style: cs.borderTopStyle,
    opacity: +cs.opacity, box_shadow: cs.boxShadow === 'none' ? null : cs.boxShadow,
    padding: [cs.paddingTop, cs.paddingRight, cs.paddingBottom, cs.paddingLeft].map(parseFloat),
    flex_direction: cs.flexDirection, justify_content: cs.justifyContent, align_items: cs.alignItems,
    gap: cs.gap, flex_wrap: cs.flexWrap, overflow: cs.overflow, z_index: cs.zIndex,
    grid_template_columns: cs.display.includes('grid') ? cs.gridTemplateColumns : undefined,
  });
  const walk = (el) => {
    if (count >= maxNodes || SKIP.has(el.tagName)) return null;
    const cs = getComputedStyle(el);
    if (cs.display === 'none') return null;
    const r = el.getBoundingClientRect();
    count++;
    const node = {tag: el.tagName.toLowerCase(), rect: {x: r.x + scrollX, y: r.y + scrollY, w: r.width, h: r.height},
                  style: pick(cs)};
    if (el.id) node.id = el.id;
    const tid = el.getAttribute('data-td-id'); if (tid) node.td_id = tid;
    const scr = el.getAttribute('data-td-screen'); if (scr) node.screen = scr;
    const src = el.getAttribute('data-td-src'); if (src) node.source_loc = src;
    if (el.classList.length) node.classes = [...el.classList].slice(0, 20);
    if (el.tagName === 'IMG') { node.src = el.currentSrc || el.src; node.alt = el.alt || ''; }
    if (el.tagName === 'svg' || el instanceof SVGSVGElement) { node.svg = el.outerHTML.slice(0, 20000); return node; }
    const text = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').replace(/\s+/g, ' ').trim();
    if (text) node.text = text;
    if (cs.visibility === 'hidden') node.hidden = true;
    const kids = [];
    for (const c of el.children) { const k = walk(c); if (k) kids.push(k); }
    if (kids.length) node.children = kids;
    return node;
  };
  const root = walk(document.body || document.documentElement);
  return {title: document.title, viewport: {width: innerWidth, height: innerHeight},
          document: {width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight},
          truncated: count >= maxNodes, node_count: count, root};
}
"""


async def dom_snapshot(url: str, width: int = 1280, height: int = 800, max_nodes: int = 5000) -> Dict[str, Any]:
    """Layout tree (rects + computed styles + text) for HTML → layers conversion."""
    async with open_page(width, height) as page:
        await page.goto(url)
        snap = await page.call(_DOM_SNAPSHOT_JS, int(max_nodes), timeout=60)
        snap["url"] = url
        return snap


# ── design_browser: open a caller-supplied URL, guarded ───────────────────
#
# The one place this module navigates to a URL a caller chose. The page runs in its
# own throwaway browser context (no cookies/storage shared with preview renders),
# and Edge itself never touches the network: CDP `Fetch` pauses every request and
# the host fetches it with proxy/media_fetch.py's rules — http/https only, every
# resolved address public, each redirect hop re-validated, byte caps while
# streaming — then fulfils the request with the bytes. A request that fails the
# rules is failed with `BlockedByClient` and listed in `blocked`. WebSockets are not
# covered by `Fetch`, so they are refused outright with `Network.setBlockedURLs`.

BROWSE_MAX_RESOURCE_BYTES = 16 * 1024 * 1024
BROWSE_MAX_TOTAL_BYTES = 64 * 1024 * 1024
BROWSE_MAX_REQUESTS = 400
BROWSE_FETCH_TIMEOUT = 20.0
_HOP_HEADERS = {"set-cookie", "set-cookie2", "content-encoding", "content-length", "transfer-encoding",
                "connection", "keep-alive", "alt-svc", "strict-transport-security"}
_FWD_REQUEST_HEADERS = {"accept", "accept-language", "user-agent", "content-type", "range", "referer"}


class _GuardedFetcher:
    def __init__(self) -> None:
        self.allowed_hosts: Dict[str, bool] = {}
        self.blocked: List[Dict[str, str]] = []
        self.total = 0
        self.count = 0
        self.http: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "_GuardedFetcher":
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=BROWSE_FETCH_TIMEOUT),
                                          cookie_jar=aiohttp.DummyCookieJar())
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self.http:
            await self.http.close()

    async def check(self, url: str) -> None:
        from proxy import media_fetch
        from urllib.parse import urlsplit
        u = urlsplit(url)
        key = f"{u.scheme}://{(u.hostname or '').lower()}"
        ok = self.allowed_hosts.get(key)
        if ok is None:
            try:
                await asyncio.to_thread(media_fetch._check_url, url)
                ok = True
            except media_fetch.MediaFetchError as exc:
                self.allowed_hosts[key] = False
                raise RenderError(str(exc))
            self.allowed_hosts[key] = ok
        if not ok:
            raise RenderError(f"refused host {u.hostname}")

    async def fetch(self, url: str, method: str, headers: Dict[str, str],
                    body: Optional[bytes]) -> Dict[str, Any]:
        """One hop → {status, headers: [(k, v)], body: bytes}.

        Redirects are *not* followed here: the 3xx goes back to Edge, whose next
        request for the Location is paused and validated like any other — so every
        hop passes the rules and the page keeps the right base URL.
        """
        if self.count >= BROWSE_MAX_REQUESTS:
            raise RenderError("request budget exhausted")
        self.count += 1
        fwd = {k: v for k, v in (headers or {}).items() if k.lower() in _FWD_REQUEST_HEADERS}
        await self.check(url)
        assert self.http is not None
        async with self.http.request(method, url, headers=fwd, data=body, allow_redirects=False) as resp:
            chunks, size = [], 0
            if not (300 <= resp.status < 400):
                async for chunk in resp.content.iter_chunked(1 << 16):
                    size += len(chunk)
                    if size > BROWSE_MAX_RESOURCE_BYTES or self.total + size > BROWSE_MAX_TOTAL_BYTES:
                        raise RenderError("resource exceeds the byte cap")
                    chunks.append(chunk)
            self.total += size
            hdrs = [(k, v) for k, v in resp.headers.items() if k.lower() not in _HOP_HEADERS]
            return {"status": resp.status, "headers": hdrs, "body": b"".join(chunks)}


_OUTLINE_JS = r"""
(maxLinks) => {
  const txt = (el) => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden'; };
  const count = (m, k) => m.set(k, (m.get(k) || 0) + 1);
  const top = (m, n) => [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, n).map(([k, v]) => ({value: k, count: v}));
  const fonts = new Map(), colors = new Map(), bgs = new Map();
  let n = 0;
  for (const el of document.querySelectorAll('body *')) {
    if (++n > 3000) break;
    const cs = getComputedStyle(el);
    if (cs.backgroundColor && cs.backgroundColor !== 'rgba(0, 0, 0, 0)') count(bgs, cs.backgroundColor);
    if ([...el.childNodes].some(c => c.nodeType === 3 && c.textContent.trim())) {
      count(fonts, cs.fontFamily.split(',')[0].replace(/["']/g, '').trim()); count(colors, cs.color);
    }
  }
  const heads = [...document.querySelectorAll('h1,h2,h3,h4')].filter(vis).slice(0, 60)
    .map(h => ({level: +h.tagName[1], text: txt(h).slice(0, 140)}));
  const land = [...document.querySelectorAll('header,nav,main,aside,footer,section,form,[role=banner],[role=navigation],[role=main],[role=contentinfo]')]
    .filter(vis).slice(0, 40).map(e => { const r = e.getBoundingClientRect();
      return {tag: e.tagName.toLowerCase(), role: e.getAttribute('role') || undefined, id: e.id || undefined,
              label: (e.getAttribute('aria-label') || (e.querySelector('h1,h2,h3') ? txt(e.querySelector('h1,h2,h3')) : '')).slice(0, 80),
              rect: [Math.round(r.x + scrollX), Math.round(r.y + scrollY), Math.round(r.width), Math.round(r.height)]}; });
  const links = [...document.querySelectorAll('a[href]')].filter(vis).slice(0, maxLinks)
    .map(a => ({text: txt(a).slice(0, 80), href: a.href.slice(0, 300)}));
  const buttons = [...document.querySelectorAll('button,[role=button],input[type=submit],input[type=button]')].filter(vis)
    .slice(0, 40).map(b => (txt(b) || b.value || b.getAttribute('aria-label') || '').slice(0, 60));
  const images = [...document.querySelectorAll('img')].filter(vis).slice(0, 30)
    .map(i => ({alt: (i.alt || '').slice(0, 100), src: (i.currentSrc || i.src || '').slice(0, 300),
                size: [i.naturalWidth, i.naturalHeight]}));
  const meta = (name) => { const m = document.querySelector(`meta[name="${name}"],meta[property="${name}"]`); return m ? m.content.slice(0, 300) : undefined; };
  return {title: document.title, url: location.href, lang: document.documentElement.lang || undefined,
          description: meta('description') || meta('og:description'),
          size: {width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight},
          headings: heads, landmarks: land, links, buttons, images,
          inputs: document.querySelectorAll('input:not([type=hidden]),select,textarea').length,
          fonts: top(fonts, 6), text_colors: top(colors, 6), background_colors: top(bgs, 6),
          text: txt(document.body || document.documentElement).slice(0, 4000)};
}
"""


async def browse(url: str, width: int = 1280, height: int = 800, full_page: bool = False,
                 steps: Optional[List[dict]] = None, fmt: str = "jpeg", quality: int = 80,
                 guarded: bool = True, max_links: int = 60) -> Dict[str, Any]:
    """Load `url` headless and return {screenshot: bytes, mime, outline, blocked, console, final_url}.

    `guarded=True` (every caller-supplied URL) routes all traffic through the
    media_fetch rules described above. `guarded=False` is only for the §5 preview
    origin, which is ours.
    """
    if guarded:
        from urllib.parse import urlsplit
        if urlsplit(url).scheme not in ("http", "https"):
            raise RenderError("only http(s) URLs can be browsed")
    width = max(320, min(int(width or 1280), 2560))
    height = max(240, min(int(height or 800), 2560))
    conn = await _get_conn()
    _BROWSER.touch(+1)
    context_id: Optional[str] = None
    try:
        async with conn.sem:
            if guarded:
                context_id = (await conn.send("Target.createBrowserContext", {"disposeOnDetach": True}))[
                    "browserContextId"]
            page = Page(conn, width, height, 1.0, False, context_id=context_id)
            fetcher = _GuardedFetcher()
            try:
                await page.open()
                async with fetcher:
                    if guarded:
                        loop = asyncio.get_running_loop()

                        async def _serve(p: dict) -> None:
                            rid = p.get("requestId")
                            req = p.get("request") or {}
                            rurl = req.get("url", "")
                            method = (req.get("method") or "GET").upper()
                            try:
                                if method not in ("GET", "HEAD", "POST"):
                                    raise RenderError(f"method {method} not allowed")
                                body = None
                                if method == "POST" and req.get("postData"):
                                    body = str(req["postData"]).encode("utf-8")
                                res = await fetcher.fetch(rurl, method, req.get("headers") or {}, body)
                                await page.cmd("Fetch.fulfillRequest", {
                                    "requestId": rid, "responseCode": int(res["status"]),
                                    "responseHeaders": [{"name": k, "value": v} for k, v in res["headers"]],
                                    "body": base64.b64encode(res["body"]).decode("ascii")}, timeout=30)
                            except Exception as exc:
                                if len(fetcher.blocked) < 100:
                                    fetcher.blocked.append({"url": rurl[:300], "reason": str(exc)[:200]})
                                try:
                                    await page.cmd("Fetch.failRequest", {"requestId": rid,
                                                                         "errorReason": "BlockedByClient"}, timeout=10)
                                except Exception:
                                    pass

                        def _on_fetch(method: str, p: dict) -> None:
                            if method == "Fetch.requestPaused":
                                loop.create_task(_serve(p))
                        page.extra_handler = _on_fetch
                        await page.cmd("Network.setBlockedURLs", {"urls": ["ws://*", "wss://*"]})
                        await page.cmd("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
                    await page.goto(url)
                    await page.run_steps(steps)
                    outline = await page.call(_OUTLINE_JS, int(max_links), timeout=30)
                    shot = await page.capture("jpeg" if fmt == "jpeg" else "png",
                                              quality if fmt == "jpeg" else None, full_page=full_page)
                    return {"screenshot": shot, "mime": "image/jpeg" if fmt == "jpeg" else "image/png",
                            "outline": outline, "blocked": list(fetcher.blocked),
                            "console": list(page.errors)[:30], "final_url": (outline or {}).get("url", url),
                            "bytes_fetched": fetcher.total, "requests": fetcher.count}
            finally:
                page.extra_handler = None
                await page.close()
                if context_id and not conn.closed:
                    try:
                        await conn.send("Target.disposeBrowserContext", {"browserContextId": context_id},
                                        timeout=10)
                    except Exception:
                        pass
    finally:
        _BROWSER.touch(-1)


# ── Thumbnails ───────────────────────────────────────────────────────────

def primary_file(pid: str, preferred: Optional[str] = None) -> Optional[str]:
    """The file a project 'is': explicit > project active_file > index.html > first asset > first .html."""
    d = store.project_dir(pid)
    if not d:
        return None
    cands: List[str] = []
    if preferred:
        cands.append(preferred)
    rec = store.get_project(pid) or {}
    for key in ("active_file", "primary_file"):
        if isinstance(rec.get(key), str):
            cands.append(rec[key])
    cands.append("index.html")
    try:
        assets = json.loads((d / "assets.json").read_text(encoding="utf-8")).get("assets") or []
        cands += [a.get("path") for a in assets if isinstance(a, dict) and isinstance(a.get("path"), str)]
    except Exception:
        pass
    try:
        boards = json.loads((d / "boards.json").read_text(encoding="utf-8")) or {}
        cands += [b.get("src") for b in boards.values() if isinstance(b, dict)]
    except Exception:
        pass
    for c in cands:
        if c and store.safe_relpath(c) and c.endswith(".html") and (d / c).is_file():
            return c
    htmls = sorted(p.relative_to(d).as_posix() for p in d.glob("*.html"))
    return htmls[0] if htmls else None


async def thumbnail(pid: str, file: Optional[str] = None, width: int = 640) -> Path:
    """Render the project's primary board to `<project>/thumbnail.webp`; returns the path."""
    d = store.project_dir(pid)
    if not d:
        raise RenderError("project not found")
    rel = primary_file(pid, file)
    if not rel:
        raise RenderError("project has no HTML file to thumbnail")
    async with open_page(1280, 800) as page:
        info = await load_for_render(page, preview_url(pid, rel))
        if info.get("is_deck"):
            await deck_go(page, 1)
            await page.hide([".deck-controls"])
        png = await page.capture("png")
    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGB")
    h = max(1, round(img.height * width / img.width))
    img = img.resize((width, h), Image.LANCZOS)
    out = d / "thumbnail.webp"
    tmp = d / "thumbnail.webp.tmp"
    img.save(tmp, "WEBP", quality=82, method=4)
    tmp.replace(out)
    return out


# ── Verifier render checks ───────────────────────────────────────────────

_CHECKS_JS = r"""
(() => {
  if (window.__tdChecks) return true;
  const path = (el) => {
    const parts = []; let e = el;
    while (e && e.nodeType === 1 && parts.length < 5) {
      let s = e.tagName.toLowerCase();
      const tid = e.getAttribute('data-td-id');
      if (tid) { parts.unshift(`[data-td-id="${tid}"]`); break; }
      if (e.id) { parts.unshift(`${s}#${e.id}`); break; }
      const scr = e.getAttribute('data-td-screen');
      if (scr) { parts.unshift(`${s}[data-td-screen="${scr}"]`); break; }
      const p = e.parentElement;
      if (p) { const same = [...p.children].filter(c => c.tagName === e.tagName);
               if (same.length > 1) s += `:nth-of-type(${same.indexOf(e) + 1})`; }
      parts.unshift(s); e = p;
    }
    return parts.join(' > ');
  };
  const vis = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity < 0.05) return false;
    const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0;
  };
  const textEls = (root) => {
    const out = [], seen = new Set();
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); let n;
    while ((n = w.nextNode())) {
      if (!n.textContent.trim()) continue;
      const el = n.parentElement;
      if (!el || seen.has(el) || el.closest('script,style,noscript,template,[data-td-rt]')) continue;
      seen.add(el); if (vis(el)) out.push(el);
    }
    return out;
  };
  const targets = (root, factor) => [...root.querySelectorAll(
      'button,[role=button],input:not([type=hidden]),select,textarea,a[href]')]
    .filter(el => vis(el) && !(el.tagName === 'A' && getComputedStyle(el).display === 'inline'))
    .map(el => { const r = el.getBoundingClientRect(); return {el, w: r.width * factor, h: r.height * factor}; })
    .filter(t => t.w < 44 || t.h < 44)
    .map(t => ({where: path(t.el), w: Math.round(t.w), h: Math.round(t.h),
                text: (t.el.textContent || t.el.value || t.el.getAttribute('aria-label') || '').trim().slice(0, 30)}));
  const small = (root, minPx) => textEls(root)
    .map(el => ({el, size: parseFloat(getComputedStyle(el).fontSize)}))
    .filter(x => x.size < minPx - 0.01)
    .map(x => ({where: path(x.el), size: x.size, text: x.el.textContent.trim().slice(0, 40)}));
  const images = (root) => [...root.querySelectorAll('img')]
    .filter(i => i.complete && i.naturalWidth === 0 && (i.currentSrc || i.getAttribute('src')))
    .map(i => ({where: path(i), src: (i.currentSrc || i.getAttribute('src')).slice(0, 200)}));
  const deepest = (els) => els.filter(el => !els.some(o => o !== el && el.contains(o)));
  // WCAG 2.x contrast: text colour vs the colour actually behind it. The background
  // is composited from the element's own and its ancestors' background-color up to
  // the first opaque one (then white). When anything that is not a flat colour sits
  // behind the text — a background-image/gradient, or an img/video/canvas/svg under
  // it — the pair is "unknown" and skipped, never guessed: a false contrast failure
  // would send the designer off to "fix" white text on a hero photo.
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const cx = cv.getContext('2d', {willReadFrequently: true});
  const rgba = (c) => {
    if (!c || c === 'transparent') return [0, 0, 0, 0];
    const m = c.match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$/);
    if (m) {
      let a = m[4] === undefined ? 1 : (m[4].endsWith('%') ? parseFloat(m[4]) / 100 : +m[4]);
      return [+m[1], +m[2], +m[3], a];
    }
    try {  // oklch(), color(), lab() … — let the canvas convert to sRGB
      cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = c; cx.fillRect(0, 0, 1, 1);
      const d = cx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255];
    } catch (e) { return null; }
  };
  const over = (top, under) => {  // top (rgba) composited over an opaque colour
    const a = top[3]; return [0, 1, 2].map(i => top[i] * a + under[i] * (1 - a));
  };
  const lum = (c) => {
    const ch = c.slice(0, 3).map(v => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
  };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const MEDIA = 'img,video,canvas,svg,picture,iframe,object,embed';
  const behind = (el) => {
    const r = el.getBoundingClientRect(); const layers = [];
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;
      if (e !== el) {
        for (const m of e.querySelectorAll(MEDIA)) {
          if (m === el || el.contains(m) || m.contains(el)) continue;
          const mr = m.getBoundingClientRect();
          if (mr.width && mr.height && mr.left < r.right && mr.right > r.left && mr.top < r.bottom && mr.bottom > r.top) return null;
        }
      }
      const bg = rgba(cs.backgroundColor); if (!bg) return null;
      if (bg[3] > 0) layers.push(bg);
      if (bg[3] >= 0.999) break;
    }
    let col = [255, 255, 255];
    for (let i = layers.length - 1; i >= 0; i--) col = over(layers[i], col);
    return col;
  };
  const contrast = (root) => {
    const out = [];
    for (const el of textEls(root).slice(0, 600)) {
      if (el.closest('button:disabled,input:disabled,select:disabled,textarea:disabled,[aria-disabled=true],[aria-hidden=true]')) continue;
      const cs = getComputedStyle(el);
      if (cs.textShadow && cs.textShadow !== 'none') continue;
      if ((cs.webkitBackgroundClip || cs.backgroundClip) === 'text') continue;
      const fg = rgba(cs.color); if (!fg || fg[3] < 0.1) continue;
      const bg = behind(el); if (!bg) continue;
      const ink = over(fg, bg);
      const size = parseFloat(cs.fontSize), weight = parseInt(cs.fontWeight, 10) || 400;
      const large = size >= 24 || (size >= 18.66 && weight >= 700);
      const need = large ? 3 : 4.5, got = ratio(ink, bg);
      if (got + 1e-6 < need) {
        const hex = (c) => '#' + c.slice(0, 3).map(v => Math.round(v).toString(16).padStart(2, '0')).join('');
        out.push({where: path(el), ratio: Math.round(got * 100) / 100, need, large, size,
                  fg: hex(ink), bg: hex(bg), text: el.textContent.trim().slice(0, 40)});
      }
    }
    return out.sort((a, b) => a.ratio - b.ratio);
  };
  window.__tdChecks = {
    contrast,
    page(minPx) {
      const de = document.documentElement, vw = de.clientWidth;
      const overflow = de.scrollWidth > vw + 1;
      let culprits = [];
      if (overflow) {
        const c = [];
        for (const el of document.body.querySelectorAll('*')) {
          if (c.length > 300) break;
          const r = el.getBoundingClientRect();
          if (r.right > vw + 1 && r.width > 0 && vis(el)) c.push(el);
        }
        culprits = deepest(c).slice(0, 5).map(el => ({where: path(el), right: Math.round(el.getBoundingClientRect().right)}));
      }
      const b = document.body;
      const blank = !b || (b.innerText.trim() === '' && !b.querySelector('img,svg,canvas,video,picture,iframe'));
      return {overflow_x: overflow, scroll_width: de.scrollWidth, viewport: vw, culprits,
              small: small(b, minPx), targets: targets(b, 1), images: images(b), blank,
              contrast: contrast(b)};
    },
    slide(index, W, minPx) {
      const s = window.__tdDeck.slides()[index - 1];
      const sr = s.getBoundingClientRect();
      const factor = sr.width ? W / sr.width : 1;
      const off = [];
      for (const el of textEls(s)) {
        const r = el.getBoundingClientRect();
        if (r.right > sr.right + 1 || r.bottom > sr.bottom + 1 || r.left < sr.left - 1 || r.top < sr.top - 1)
          off.push(el);
      }
      const blank = s.innerText.trim() === '' && !s.querySelector('img,svg,canvas,video,picture');
      return {label: s.getAttribute('data-td-screen') || '',
              offslide: deepest(off).slice(0, 5).map(el => ({where: path(el), text: el.textContent.trim().slice(0, 40)})),
              small: small(s, minPx), targets: targets(s, factor), images: images(s), blank,
              contrast: contrast(s)};
    },
  };
  return true;
})()
"""


def _issue(severity: str, file: str, where: str, what: str, evidence: str, fix: str, check: str) -> Dict[str, str]:
    return {"severity": severity, "file": file, "where": where, "what": what, "evidence": evidence,
            "fix": fix, "check": check}


def _examples(items: List[dict], key: str = "where", n: int = 3) -> str:
    return "; ".join(str(i.get(key)) for i in items[:n])


def contrast_issue(rel: str, items: List[dict]) -> Optional[Dict[str, str]]:
    """One verifier issue for the text/background pairs below WCAG AA.

    `major` when any pair is under 3:1 (unreadable for many people, and below even
    the large-text bar), else `minor` — verifier.md's "contrast below 4.5:1 (3:1
    large) on primary content" is judged by the model pass, which sees these too.
    """
    if not items:
        return None
    items = sorted(items, key=lambda c: c.get("ratio", 99))
    worst = items[0]
    severity = "major" if worst.get("ratio", 99) < 3 else "minor"
    slides = sorted({c["slide"] for c in items if c.get("slide")})
    evidence = "; ".join(f"{c.get('fg')} on {c.get('bg')} = {c.get('ratio')}:1 (needs {c.get('need'):g}:1, "
                         f"{c.get('size'):g}px) \"{c.get('text', '')}\"" for c in items[:3])
    return _issue(severity, rel, _examples(items),
                  f"{len(items)} text element(s) below WCAG AA contrast (worst {worst.get('ratio')}:1"
                  + (f", slides {slides[:6]}" if slides else "") + ")",
                  evidence, "Darken the text or lighten its background (or the reverse) until normal text reaches "
                            "4.5:1 and large text 3:1; use the design system's text/surface token pairs.",
                  "contrast")


async def _verify_file(pid: str, rel: str, shots: bool, max_slides: int = 40) -> Dict[str, Any]:
    issues: List[Dict[str, str]] = []
    shot_paths: List[str] = []
    out_dir = cache_dir(pid) / "verify"
    if shots:
        out_dir.mkdir(parents=True, exist_ok=True)
    stem = rel.replace("/", "__").rsplit(".", 1)[0]

    async with open_page(1280, 800) as page:
        info = await load_for_render(page, preview_url(pid, rel))
        await page.eval(_CHECKS_JS, await_promise=False)
        is_deck = bool(info.get("is_deck"))
        if not is_deck:
            res = await page.eval("window.__tdChecks.page(12)", await_promise=False) or {}
            if res.get("blank"):
                issues.append(_issue("blocker", rel, "body", "Board renders blank (no visible text or media)",
                                     "headless render", "Check the console errors and the React mount point.",
                                     "blank"))
            if res.get("overflow_x"):
                issues.append(_issue(
                    "major", rel, _examples(res.get("culprits") or []) or "body",
                    f"Horizontal overflow: page is {res.get('scroll_width')}px wide in a {res.get('viewport')}px viewport",
                    "document.documentElement.scrollWidth", "Constrain the widest element (max-width: 100%, "
                    "flex-wrap, min-width: 0) so nothing extends past the viewport.", "overflow"))
            if res.get("small"):
                sm = res["small"]
                issues.append(_issue("minor", rel, _examples(sm),
                                     f"{len(sm)} text element(s) under 12px (smallest {min(s['size'] for s in sm):g}px)",
                                     "computed font-size", "Raise body and caption text to at least 12px.",
                                     "small_text"))
            tg = [t for t in res.get("targets") or []]
            if tg:
                issues.append(_issue("minor", rel, _examples(tg),
                                     f"{len(tg)} interactive element(s) smaller than 44×44px",
                                     ", ".join(f"{t['w']}×{t['h']}" for t in tg[:3]),
                                     "Give buttons and controls a hit area of at least 44×44px (padding or min-width/min-height).",
                                     "hit_target"))
            ci = contrast_issue(rel, res.get("contrast") or [])
            if ci:
                issues.append(ci)
            imgs = res.get("images") or []
            if shots:
                p = out_dir / f"{stem}.png"
                p.write_bytes(await page.capture("png"))
                shot_paths.append(str(p))
        else:
            count = min(int(info.get("count") or 0), max_slides)
            W = int(info.get("w") or 1920)
            imgs = []
            labels = info.get("labels") or []
            bad_labels = []
            for i, lab in enumerate(labels[:count], start=1):
                num = lab[:2]
                if not lab or not num.isdigit() or int(num) != i:
                    bad_labels.append(f"slide {i}: {lab or '(none)'}")
            if bad_labels:
                issues.append(_issue("minor", rel, "[data-td-slide]",
                                     "Slide labels do not follow data-td-screen=\"NN Name\" (1-indexed)",
                                     "; ".join(bad_labels[:4]), "Label every slide \"NN Name\" in order, starting at 01.",
                                     "slide_label"))
            counter_bad, forced, small_all, off_all, tg_all, con_all = [], [], [], [], [], []
            sticky: set = set()
            for i in range(1, count + 1):
                nav = await deck_go(page, i, settle_ms=250)
                if not nav.get("shown", True):
                    forced.append(i)
                sticky.update(nav.get("others") or [])
                ctr = nav.get("counter")
                if ctr is not None and ctr.replace(" ", "") != f"{i}/{info['count']}":
                    counter_bad.append(f"slide {i} shows \"{ctr}\"")
                res = await page.eval(f"window.__tdChecks.slide({i}, {W}, 24)", await_promise=False) or {}
                where = f"section[data-td-screen='{res.get('label') or i}']"
                if res.get("blank"):
                    issues.append(_issue("blocker", rel, where, f"Slide {i} is blank", "headless render",
                                         "Give the slide content or remove it.", "blank"))
                small_all += [dict(s, slide=i) for s in res.get("small") or []]
                off_all += [dict(o, slide=i) for o in res.get("offslide") or []]
                tg_all += [dict(t, slide=i) for t in res.get("targets") or []]
                con_all += [dict(c, slide=i) for c in res.get("contrast") or []]
                imgs += res.get("images") or []
                if shots and i <= 12:
                    await page.hide([".deck-controls"])
                    p = out_dir / f"{stem}-{i:02d}.png"
                    p.write_bytes(await page.capture("png"))
                    shot_paths.append(str(p))
                    await page.remove_style("__td_rt_hide")
            if forced:
                issues.append(_issue("blocker" if len(forced) == count else "major", rel, "#deck-stage",
                                     f"Deck navigation did not show slide(s) {', '.join(map(str, forced[:8]))} "
                                     "in response to {type:'td:slide', action:'go'}",
                                     "slide visibility after td:slide", "Implement the td:slide message handler "
                                     "from the deck contract (prompts/deck.md §2).", "deck_nav"))
            if sticky:
                lst = sorted(sticky)
                issues.append(_issue("major", rel, ", ".join(f"[data-td-slide]:nth-of-type({s})" for s in lst[:4]),
                                     f"Slide(s) {', '.join(map(str, lst[:8]))} stay visible while another slide is "
                                     "active — slides overlap", "slide visibility during navigation",
                                     "Don't set `display` on the <section data-td-slide> itself (it overrides the "
                                     "deck's hide rule); put flex/grid on an inner wrapper.", "deck_overlap"))
            if counter_bad:
                issues.append(_issue("major", rel, ".deck-count", "Slide counter does not match the current slide",
                                     "; ".join(counter_bad[:4]), "Update the counter to `index / total` "
                                     "(1-indexed) on every slide change.", "slide_counter"))
            if small_all:
                issues.append(_issue("major", rel, _examples(small_all),
                                     f"{len(small_all)} text element(s) under 24px on the 1920×1080 canvas "
                                     f"(smallest {min(s['size'] for s in small_all):g}px, slides "
                                     f"{sorted({s['slide'] for s in small_all})[:6]})",
                                     "computed font-size", "Deck text is never below 24px — split the slide or cut words.",
                                     "small_text"))
            if off_all:
                issues.append(_issue("major", rel, _examples(off_all),
                                     f"{len(off_all)} text element(s) extend past the slide edge "
                                     f"(slides {sorted({o['slide'] for o in off_all})[:6]})",
                                     "element rect vs slide rect", "Keep content at least 96px inside the slide edges.",
                                     "overflow"))
            if tg_all:
                issues.append(_issue("minor", rel, _examples(tg_all),
                                     f"{len(tg_all)} interactive element(s) on slides smaller than 44×44px",
                                     ", ".join(f"{t['w']}×{t['h']}" for t in tg_all[:3]),
                                     "Give controls a hit area of at least 44×44px.", "hit_target"))
            ci = contrast_issue(rel, con_all)
            if ci:
                issues.append(ci)
            if count and not info.get("notes"):
                pass  # notes are optional (only when the user asked) — reported by PPTX export, not here

        errors = list(page.errors)
        failed = [f for f in page.failed_resources if f.get("type") in ("Image", "Font", "Media")]

    if errors:
        issues.insert(0, _issue("blocker", rel, "console", f"{len(errors)} console error(s) on load: {errors[0][:300]}",
                                "; ".join(e[:200] for e in errors[:5]), "Fix the error at its source; the page must load "
                                "with a clean console.", "console"))
    bad_imgs = {i["src"]: i for i in imgs}
    for f in failed:
        bad_imgs.setdefault(f["url"], {"where": f["type"].lower(), "src": f["url"]})
    if bad_imgs:
        lst = list(bad_imgs.values())
        issues.append(_issue("major", rel, _examples(lst), f"{len(lst)} image/font/media resource(s) failed to load",
                             "; ".join(str(i["src"])[:120] for i in lst[:4]),
                             "Fix the path (project-relative) or replace the asset.", "broken_resource"))
    return {"issues": issues, "screenshots": shot_paths, "console": errors, "deck": info}


_SEV_ORDER = {"blocker": 0, "major": 1, "minor": 2, "info": 3}


def _layer_checks_enabled(layers: Optional[bool]) -> bool:
    if layers is not None:
        return bool(layers)
    return bool(config.get_nested("design.verifier.layer_boards", True))


async def verify(pid: str, files: List[str], screenshots: bool = True,
                 layers: Optional[bool] = None) -> Dict[str, Any]:
    """Render checks for the verifier / done gate.

    Returns {"status": "pass"|"issues", "issues": [verifier.md items + "check"],
             "screenshots": [paths], "console": {file: [errors]}, "decks": {file: info},
             "pen_problems": str, "layers": {ran, skipped, problems, …}}.
    Non-HTML or missing files are skipped. Never raises for a page problem — a
    render failure becomes a blocker issue on that file.

    Layer boards (`layers`, default `design.verifier.layer_boards` = true) are
    checked with open-pencil's own analysis in the live editor
    (`editor_bridge.inspect_layers`); with no editor attached the check is skipped
    and the reason logged and returned — never guessed.
    """
    d = store.project_dir(pid)
    if not d:
        raise RenderError("project not found")
    issues: List[Dict[str, str]] = []
    shots: List[str] = []
    console: Dict[str, List[str]] = {}
    decks: Dict[str, Any] = {}
    for rel in files or []:
        if not isinstance(rel, str) or not store.safe_relpath(rel) or not rel.endswith(".html"):
            continue
        if not (d / rel).is_file():
            continue
        try:
            r = await _verify_file(pid, rel, screenshots)
        except Exception as exc:
            log.warning("render: verify %s/%s failed: %s", pid, rel, exc)
            issues.append(_issue("blocker", rel, "page", f"Headless render failed: {exc}", "render.verify",
                                 "Make sure the page loads.", "render"))
            continue
        issues += r["issues"]
        shots += r["screenshots"]
        console[rel] = r["console"]
        if r["deck"].get("is_deck"):
            decks[rel] = r["deck"]
    layer_report: Dict[str, Any] = {"ran": False, "skipped": "disabled", "issues": [], "problems": [],
                                    "text": "", "screenshots": []}
    if _layer_checks_enabled(layers) and store.has_canvas(d):
        try:
            from services.design import editor_bridge
            layer_report = await editor_bridge.inspect_layers(
                pid, shots_dir=(cache_dir(pid) / "verify") if screenshots else None)
        except Exception as exc:
            layer_report = {**layer_report, "skipped": f"layer inspection failed: {exc}"}
        if layer_report.get("skipped"):
            log.info("render: verify %s — layer boards not checked: %s", pid, layer_report["skipped"])
        issues += layer_report.get("issues") or []
        shots += layer_report.get("screenshots") or []
    elif _layer_checks_enabled(layers):
        layer_report["skipped"] = "project has no canvas document (docs/*.fig)"
    issues.sort(key=lambda i: _SEV_ORDER.get(i["severity"], 9))
    pen = layer_report.get("text") or (f"(not checked: {layer_report['skipped']})"
                                       if layer_report.get("skipped") else "")
    return {"status": "issues" if issues else "pass", "issues": issues, "screenshots": shots,
            "console": console, "decks": decks, "pen_problems": pen,
            "layers": {k: layer_report.get(k) for k in ("ran", "skipped", "problems", "typography")}}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
