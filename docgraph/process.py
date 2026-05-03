"""DocGraph subprocess supervision — single host model.

After the docgraph 2.2.0 rewrite, telecode supervises **one** docgraph
host process. The host registers every configured root, exposes the web
UI + JSON API + MCP HTTP on one port, and accepts a `root=<slug>` enum
argument on every tool call to scope queries.

Public surface (used by main.py + the tray UI):
    autostart_all() / shutdown_all()
    get_index() / get_host()
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Optional

import aiohttp

from process import bind_to_lifetime_job, kill_process_tree, sweep_port

import config as app_config
from . import config as dg_cfg
from . import bridge as dg_bridge
from . import index_state
from . import wiki_state

log = logging.getLogger("telecode.docgraph")


# ── Helpers ────────────────────────────────────────────────────────────────

def _binary_or_raise() -> str:
    binary = dg_cfg.resolve_binary()
    if not binary:
        raise RuntimeError(
            "docgraph binary not found. Set `docgraph.binary` in settings.json or "
            "install docgraph on PATH (`pipx install docgraph`)."
        )
    return binary


def _open_log(role: str, slug: str | None = None):
    """Open a fresh log file for `role` (truncating any previous content).

    Append mode used to make every reindex attempt accumulate into one
    file across telecode sessions, which made debugging unreadable —
    you'd see five layers of historical errors in the Logs viewer.
    Truncating per spawn means the live log only shows the current run.
    """
    path = dg_cfg.log_path(role, slug)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fp = open(path, "wb", buffering=0)
    fp.write(f"===== telecode: spawning docgraph {role}".encode()
             + (f" ({slug})".encode() if slug else b"")
             + b" =====\n")
    fp.flush()
    return fp


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _ensure_high_perf_gpu(exe_path: str) -> None:
    """Mark `exe_path` as preferring the discrete GPU on Windows hybrid
    graphics. Without this, processes spawned with CREATE_NO_WINDOW get
    handed the iGPU by DXGI's default adapter enumeration, and ONNX
    Runtime / DirectML lands on Intel Graphics — slow at best, device-hung
    under sustained load at worst.

    Writes `HKCU\\Software\\Microsoft\\DirectX\\UserGpuPreferences` with
    the absolute exe path → `GpuPreference=2;` (2 = High Performance).
    Idempotent: skip if the value already matches. No-op off Windows or
    if the path is unresolvable."""
    if sys.platform != "win32" or not exe_path:
        return
    try:
        path = os.path.abspath(exe_path)
        if not os.path.exists(path):
            return
        import winreg  # stdlib on Windows
        key_path = r"Software\Microsoft\DirectX\UserGpuPreferences"
        wanted = "GpuPreference=2;"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_READ | winreg.KEY_WRITE) as k:
            try:
                cur, _ = winreg.QueryValueEx(k, path)
                if cur == wanted:
                    return
            except FileNotFoundError:
                pass
            winreg.SetValueEx(k, path, 0, winreg.REG_SZ, wanted)
            log.info("docgraph: wrote GpuPreference=2 (high-perf) for %s", path)
    except Exception as exc:
        # Not fatal — host still runs, just may end up on iGPU under
        # CREATE_NO_WINDOW. Log and move on.
        log.warning("could not set GpuPreference for %s: %s", exe_path, exc)


def _alive(proc: subprocess.Popen | None) -> bool:
    return bool(proc and proc.poll() is None)


def _ascii_bar(pct: float, width: int = 24) -> str:
    """Tiny block-character bar — `[#####.....]`-style, but with the
    Unicode block ramp so the bar reads cleanly in any monospace font."""
    pct = max(0.0, min(1.0, pct))
    filled = int(width * pct)
    return "█" * filled + "░" * (width - filled)


def _format_progress_line(event: str, payload: dict) -> str:
    """Format an SSE progress event payload into one human-readable log
    line. Covers index_progress + wiki_progress shapes. Renders an ASCII
    progress bar so the log reads like the CLI's Rich bar."""
    import datetime as _dt
    phase = str(payload.get("phase") or "?")
    cur = int(payload.get("current") or 0)
    tot = int(payload.get("total") or 0)
    mod = str(payload.get("module") or "")
    ts = float(payload.get("ts") or 0.0)
    ts_str = _dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "--:--:--"
    if tot > 0:
        pct = cur / tot
        bar = _ascii_bar(pct)
        head = f"{phase}: {mod}" if (event == "wiki_progress" and mod) else phase
        return f"[{ts_str}] {head:<28} {bar} {pct*100:5.1f}% ({cur:>7}/{tot:<7})"
    if event == "wiki_progress" and mod:
        return f"[{ts_str}] {phase}: {mod}"
    return f"[{ts_str}] {phase}"


async def _sse_progress_tee(slug: str, port: int, event_names: tuple[str, ...],
                             log_fp, session: aiohttp.ClientSession,
                             path_for_slug: str = "") -> None:
    """Subscribe to /api/events on the host and write matching events to
    `log_fp`. Filters by `slug` (events from other roots are ignored) and
    by event name (`index_progress` / `wiki_progress`). Best-effort — any
    failure (host went down, SSE timed out, log_fp closed) is swallowed
    silently; the parent task handles the actual operation result."""
    if log_fp is None:
        return
    base = f"http://{dg_cfg.host_host()}:{port}"
    try:
        async with session.get(f"{base}/api/events") as resp:
            if resp.status != 200:
                return
            current_event = "message"
            async for raw in resp.content:
                try:
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                except Exception:
                    continue
                if not line:
                    continue
                if line.startswith("event:"):
                    current_event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                if current_event not in event_names:
                    continue
                payload_text = line[5:].strip()
                try:
                    payload = json.loads(payload_text)
                except Exception:
                    continue
                if payload.get("repo_slug") != slug:
                    continue
                # Push to the in-memory progress state so the tray UI
                # can paint a live bar on the row.
                try:
                    from docgraph import progress_state
                    kind = "wiki" if current_event == "wiki_progress" else "index"
                    progress_state.set(
                        path_for_slug or "",
                        kind,
                        phase=str(payload.get("phase") or "?"),
                        current=int(payload.get("current") or 0),
                        total=int(payload.get("total") or 0),
                        module=str(payload.get("module") or ""),
                    )
                except Exception:
                    pass
                line_out = _format_progress_line(current_event, payload)
                try:
                    log_fp.write((line_out + "\n").encode("utf-8", errors="replace"))
                    log_fp.flush()
                except Exception:
                    return
    except (asyncio.CancelledError, aiohttp.ClientError, Exception):
        return


async def _index_via_host(path: str, full: bool, port: int, log_fp=None) -> tuple[bool, str]:
    """POST /api/admin/index?root=<slug>&full=<bool> on the running host.

    Returns (ok, detail_text). Resolves `path` to a slug by hitting
    /api/roots first — if the host doesn't know about `path`, returns
    a clear failure (don't silently fall back to the subprocess: that
    would create the writer-lock conflict we're trying to avoid).
    """
    base = f"http://{dg_cfg.host_host()}:{port}"
    norm = os.path.normpath(path)
    target = norm.casefold() if sys.platform == "win32" else norm
    timeout = aiohttp.ClientTimeout(total=600)  # full reindex can be slow
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # 1) Resolve path -> slug via the host's roots listing.
        try:
            async with session.get(f"{base}/api/roots") as resp:
                if resp.status != 200:
                    return False, f"GET /api/roots returned HTTP {resp.status}"
                roots = await resp.json()
        except Exception as exc:
            return False, f"could not reach host at {base}: {exc}"
        slug: str | None = None
        for r in roots:
            r_norm = os.path.normpath(str(r.get("path") or ""))
            r_target = r_norm.casefold() if sys.platform == "win32" else r_norm
            if r_target == target:
                slug = r.get("slug")
                break
        if slug is None:
            return False, (
                f"path {path} is not a registered root on the host "
                f"(roots: {[r.get('slug') for r in roots]})"
            )
        # 2) Trigger the index pass. Tee SSE phase events into the log
        # in parallel so docgraph_index.log shows the same per-stage status
        # the CLI prints (rather than just the spawning marker + the final
        # captured Rich transcript at the end).
        sse_task = asyncio.create_task(
            _sse_progress_tee(slug, port, ("index_progress",), log_fp, session,
                               path_for_slug=path)
        )
        url = f"{base}/api/admin/index?root={slug}"
        try:
            try:
                async with session.post(url, json={"full": full}) as resp:
                    body = await resp.text()
                    if resp.status != 200:
                        return False, f"POST /api/admin/index -> HTTP {resp.status}: {body[:500]}"
                    import json
                    payload = json.loads(body)
                    job_id = payload.get("job_id")
                    if not job_id:
                        return False, "No job_id returned"
                
                while True:
                    await asyncio.sleep(2.0)
                    async with session.get(f"{base}/api/jobs/{job_id}") as resp:
                        if resp.status != 200:
                            continue
                        job = await resp.json()
                        status = job.get("status")
                        if status == "completed":
                            stats = job.get("result") or {}
                            cap = job.get("log") or ""
                            lines: list[str] = []
                            if cap:
                                lines.append(cap.rstrip())
                            summary = (
                                f"\n--- done: {stats.get('files', '?')} files, "
                                f"{stats.get('changed', '?')} changed, "
                                f"{stats.get('deleted', '?')} deleted, "
                                f"{stats.get('entities', '?')} entities, "
                                f"{stats.get('errors', 0)} errors, "
                                f"{stats.get('elapsed', 0):.2f}s ---"
                            )
                            lines.append(summary)
                            return True, "\n".join(lines)
                        elif status == "cancelled":
                            raise asyncio.CancelledError()
                        elif status == "failed":
                            return False, f"Index job failed: {job.get('error')}"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return False, f"POST /api/admin/index failed: {exc}"
        finally:
            sse_task.cancel()
            try:
                await sse_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                from docgraph import progress_state
                progress_state.clear(path, "index")
            except Exception:
                pass


async def _request_host_cancel(path: str) -> None:
    """Tell the host to flip its per-root cancel token. Best-effort —
    the local asyncio cancel still happens regardless. Without this
    step, the host keeps indexing/building-wikis in the background
    even after the client aborts the HTTP request."""
    if _HOST is None or not _HOST.alive() or not _HOST.port():
        return
    port = _HOST.port()
    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            slug = await _resolve_slug_from_host(path, port, session)
            if slug is None:
                return
            base = f"http://{dg_cfg.host_host()}:{port}"
            async with session.post(f"{base}/api/admin/cancel?root={slug}") as resp:
                await resp.read()
    except Exception as exc:
        log.warning("docgraph cancel request failed for %s: %s", path, exc)


async def _resolve_slug_from_host(path: str, port: int,
                                   session: aiohttp.ClientSession) -> str | None:
    """Look up the registered slug for `path` via /api/roots. Returns None
    on miss. Used by every host-route helper (index, wiki, stats, clear,
    docs)."""
    base = f"http://{dg_cfg.host_host()}:{port}"
    norm = os.path.normpath(path)
    target = norm.casefold() if sys.platform == "win32" else norm
    try:
        async with session.get(f"{base}/api/roots") as resp:
            if resp.status != 200:
                return None
            roots = await resp.json()
    except Exception:
        return None
    for r in roots:
        r_norm = os.path.normpath(str(r.get("path") or ""))
        r_target = r_norm.casefold() if sys.platform == "win32" else r_norm
        if r_target == target:
            return r.get("slug")
    return None


async def fetch_stats_dict(path: str) -> Optional[dict]:
    """Lightweight host-only stats fetch. Returns the parsed /api/stats
    payload (entity + edge counts) or None if the host isn't alive / the
    path isn't registered / the call fails. Used by the tray to paint
    a live stats badge per row — never spawns a subprocess."""
    if _HOST is None or not _HOST.alive() or not _HOST.port():
        return None
    port = _HOST.port()
    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            slug = await _resolve_slug_from_host(path, port, session)
            if slug is None:
                return None
            base = f"http://{dg_cfg.host_host()}:{port}"
            async with session.get(f"{base}/api/stats?root={slug}") as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
    except Exception:
        return None


async def fetch_stats(path: str) -> str:
    """Return a human-readable stats blob for `path`. Tries the host's
    /api/stats first; falls back to running `docgraph stats <path>` as
    a one-shot subprocess if the host isn't available."""
    if _HOST is not None and _HOST.alive() and _HOST.port():
        port = _HOST.port()
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                slug = await _resolve_slug_from_host(path, port, session)
                if slug is None:
                    return f"path {path} is not a registered root on the host."
                base = f"http://{dg_cfg.host_host()}:{port}"
                async with session.get(f"{base}/api/stats?root={slug}") as resp:
                    if resp.status != 200:
                        return f"GET /api/stats → HTTP {resp.status}"
                    data = await resp.json()
        except Exception as exc:
            return f"host route failed: {exc}"
        lines = [f"Repo: {data.get('repo', path)}", ""]
        for label in ("File", "Module", "Class", "Function", "Variable"):
            lines.append(f"  {label:<10} {data.get(label, 0)}")
        tables = data.get("tables") or []
        if tables:
            lines.append("")
            lines.append(f"Kuzu tables: {len(tables)}")
        return "\n".join(lines)
    # Subprocess fallback
    try:
        binary = _binary_or_raise()
    except Exception as exc:
        return str(exc)
    proc = await asyncio.to_thread(
        subprocess.run,
        [binary, "stats", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
        creationflags=_creation_flags(),
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
    return proc.stdout or "(no output)"


async def clear_index(path: str) -> tuple[bool, str]:
    """Wipe the index for `path`. Tries POST /api/admin/clear first; falls
    back to `docgraph clear <path> --yes` only if the host isn't alive."""
    if _HOST is not None and _HOST.alive() and _HOST.port():
        port = _HOST.port()
        timeout = aiohttp.ClientTimeout(total=120)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                slug = await _resolve_slug_from_host(path, port, session)
                if slug is None:
                    return False, f"path {path} is not a registered root on the host."
                base = f"http://{dg_cfg.host_host()}:{port}"
                async with session.post(f"{base}/api/admin/clear?root={slug}") as resp:
                    body = await resp.text()
                    if resp.status != 200:
                        return False, f"POST /api/admin/clear → HTTP {resp.status}: {body[:500]}"
                    return True, f"Index for {path} cleared."
        except Exception as exc:
            return False, f"host route failed: {exc}"
    # Subprocess fallback
    try:
        binary = _binary_or_raise()
    except Exception as exc:
        return False, str(exc)
    proc = await asyncio.to_thread(
        subprocess.run,
        [binary, "clear", path, "--yes"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
        creationflags=_creation_flags(),
    )
    if proc.returncode == 0:
        return True, f"Index for {path} cleared (subprocess)."
    return False, proc.stdout or f"docgraph clear exited with rc={proc.returncode}"


async def list_docs_for(path: str) -> tuple[bool, list[dict] | str]:
    """GET /api/docs/list?root=<slug>. Returns (True, [rows]) or (False, msg).
    No subprocess fallback — listing requires the host to be alive (so we
    don't pay a Kuzu-open cost for a read-only call)."""
    if _HOST is None or not _HOST.alive() or not _HOST.port():
        return False, "Host is not running. Start it from the Host card."
    port = _HOST.port()
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            slug = await _resolve_slug_from_host(path, port, session)
            if slug is None:
                return False, f"path {path} is not a registered root on the host."
            base = f"http://{dg_cfg.host_host()}:{port}"
            async with session.get(f"{base}/api/docs/list?root={slug}") as resp:
                if resp.status != 200:
                    body = await resp.text()
                    return False, f"GET /api/docs/list → HTTP {resp.status}: {body[:200]}"
                return True, await resp.json()
    except Exception as exc:
        return False, f"host route failed: {exc}"


async def add_doc_for(path: str, url: str) -> tuple[bool, dict | str]:
    """POST /api/docs/add?root=<slug>. Returns (True, payload) or (False, msg)."""
    if _HOST is None or not _HOST.alive() or not _HOST.port():
        return False, "Host is not running. Start it from the Host card."
    port = _HOST.port()
    # URL fetch + chunk + embed can take a while for large pages.
    timeout = aiohttp.ClientTimeout(total=300)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            slug = await _resolve_slug_from_host(path, port, session)
            if slug is None:
                return False, f"path {path} is not a registered root on the host."
            base = f"http://{dg_cfg.host_host()}:{port}"
            async with session.post(f"{base}/api/docs/add?root={slug}",
                                     json={"url": url}) as resp:
                body = await resp.text()
                if resp.status != 200:
                    return False, f"POST /api/docs/add -> HTTP {resp.status}: {body[:300]}"
                import json
                payload = json.loads(body)
                job_id = payload.get("job_id")
                if not job_id:
                    return True, payload
            
            while True:
                import asyncio
                await asyncio.sleep(2.0)
                async with session.get(f"{base}/api/jobs/{job_id}") as resp:
                    if resp.status != 200:
                        continue
                    job = await resp.json()
                    status = job.get("status")
                    if status == "completed":
                        return True, job.get("result") or {}
                    elif status == "cancelled":
                        return False, "docs add cancelled"
                    elif status == "failed":
                        return False, f"Docs add failed: {job.get('error')}"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return False, f"host route failed: {exc}"


async def remove_doc_for(path: str, url: str) -> tuple[bool, dict | str]:
    """POST /api/docs/remove?root=<slug>."""
    if _HOST is None or not _HOST.alive() or not _HOST.port():
        return False, "Host is not running."
    port = _HOST.port()
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            slug = await _resolve_slug_from_host(path, port, session)
            if slug is None:
                return False, f"path {path} is not a registered root."
            base = f"http://{dg_cfg.host_host()}:{port}"
            async with session.post(f"{base}/api/docs/remove?root={slug}",
                                     json={"url": url}) as resp:
                body = await resp.text()
                if resp.status != 200:
                    return False, f"POST /api/docs/remove → HTTP {resp.status}: {body[:300]}"
                try:
                    return True, json.loads(body)
                except Exception:
                    return True, {"raw": body[:1000]}
    except Exception as exc:
        return False, f"host route failed: {exc}"


async def _wiki_via_host(path: str, force: bool, port: int, log_fp=None) -> tuple[bool, str]:
    """POST /api/wiki/build?root=<slug> on the running host.

    Mirrors `_index_via_host`: resolves the path → slug via /api/roots,
    POSTs the build request, and returns (ok, summary). The host's wiki
    builder uses the slot's `cfg.llm_*` (set when the host was started),
    so the LLM endpoint isn't tunable via this route — only the
    subprocess fallback can override per-call.
    """
    base = f"http://{dg_cfg.host_host()}:{port}"
    norm = os.path.normpath(path)
    target = norm.casefold() if sys.platform == "win32" else norm
    timeout = aiohttp.ClientTimeout(total=1800)  # wiki can be slow per module
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.get(f"{base}/api/roots") as resp:
                if resp.status != 200:
                    return False, f"GET /api/roots returned HTTP {resp.status}"
                roots = await resp.json()
        except Exception as exc:
            return False, f"could not reach host at {base}: {exc}"
        slug: str | None = None
        for r in roots:
            r_norm = os.path.normpath(str(r.get("path") or ""))
            r_target = r_norm.casefold() if sys.platform == "win32" else r_norm
            if r_target == target:
                slug = r.get("slug")
                break
        if slug is None:
            return False, (
                f"path {path} is not a registered root on the host "
                f"(roots: {[r.get('slug') for r in roots]})"
            )
        # Tee per-module SSE progress so docgraph_wiki.log mirrors CLI status.
        sse_task = asyncio.create_task(
            _sse_progress_tee(slug, port, ("wiki_progress",), log_fp, session,
                               path_for_slug=path)
        )
        url = f"{base}/api/wiki/build?root={slug}"
        try:
            try:
                async with session.post(url, json={"force": force}) as resp:
                    body = await resp.text()
                    if resp.status != 200:
                        return False, f"POST /api/wiki/build -> HTTP {resp.status}: {body[:500]}"
                    import json
                    payload = json.loads(body)
                    job_id = payload.get("job_id")
                    if not job_id:
                        return False, "No job_id returned"
                
                while True:
                    await asyncio.sleep(2.0)
                    async with session.get(f"{base}/api/jobs/{job_id}") as resp:
                        if resp.status != 200:
                            continue
                        job = await resp.json()
                        status = job.get("status")
                        if status == "completed":
                            res = job.get("result") or {}
                            built = res.get("built", "?")
                            modules = res.get("modules") or []
                            status_str = 'rebuilt' if force else 'resumable'
                            summary = (
                                f"\n--- wiki: {built} module page(s) "
                                f"({status_str}) ---\n"
                                + "\n".join(f"  * {m}" for m in modules[:50])
                                + ("\n  * ..." if len(modules) > 50 else "")
                            )
                            return True, summary
                        elif status == "cancelled":
                            raise asyncio.CancelledError()
                        elif status == "failed":
                            return False, f"Wiki job failed: {job.get('error')}"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return False, f"POST /api/wiki/build failed: {exc}"
        finally:
            sse_task.cancel()
            try:
                await sse_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                from docgraph import progress_state
                progress_state.clear(path, "wiki")
            except Exception:
                pass


# ── Index runner (one-shot subprocess per root) ────────────────────────────

class IndexRunner:
    """Runs `docgraph index <path>` as a one-shot subprocess. The host
    process can stay running during the index — both tolerate concurrent
    access through Kuzu's per-file lock model.
    """

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._proc: Optional[subprocess.Popen] = None
        self._log_fp = None
        self._lock = asyncio.Lock()
        self._current_path: str | None = None
        self._current_force: bool = False
        self._last_status: str = "idle"
        self._last_finished_at: float = 0.0

    def alive(self) -> bool:
        return bool(self._task and not self._task.done())

    def current_path(self) -> str | None:
        return self._current_path

    def status(self) -> dict:
        return {
            "alive":             self.alive(),
            "current_path":      self._current_path,
            "current_force":     self._current_force,
            "last_status":       self._last_status,
            "last_finished_at":  self._last_finished_at,
            "log_path":          dg_cfg.log_path("index"),
            "per_path":          index_state.load(),
        }

    async def run(self, path: str, force: bool = False) -> None:
        if not path:
            raise RuntimeError("path is required")
        async with self._lock:
            if self.alive():
                log.warning("docgraph index: already running %s — ignoring %s",
                            self._current_path, path)
                return
            self._task = asyncio.create_task(self._run_one(path, force))

    async def run_all(self, force: bool = False) -> None:
        async with self._lock:
            if self.alive():
                return
            paths = dg_cfg.root_paths()
            if not paths:
                return
            self._task = asyncio.create_task(self._run_sequence(paths, force))

    async def cancel(self) -> None:
        # Capture path/proc under the lock, then drop it before doing any
        # network IO. Otherwise _request_host_cancel would deadlock if
        # any other coroutine awaiting the lock is also fired by cancel.
        async with self._lock:
            t = self._task
            current = self._current_path
            proc = self._proc
        # Tell the host to flip its cancel token *before* cancelling the
        # local task, so the in-flight HTTP response sees the cancel and
        # raises OperationCancelled cleanly. (Cancelling the asyncio task
        # first would abort the connection and the server would never see
        # the cancel signal.)
        if current:
            await _request_host_cancel(current)
        async with self._lock:
            if t and not t.done():
                t.cancel()
            if proc and proc.poll() is None:
                try:
                    kill_process_tree(proc.pid)
                except Exception:
                    pass

    async def _run_sequence(self, paths: list[str], force: bool) -> None:
        for path in paths:
            await self._run_one_inline(path, force)
            if self._last_status in ("failed", "cancelled"):
                return

    async def _run_one(self, path: str, force: bool) -> None:
        await self._run_one_inline(path, force)

    async def _run_one_inline(self, path: str, force: bool) -> None:
        self._last_status = "running"
        self._current_path = path
        self._current_force = force
        index_state.mark_running(path, was_full=force)
        self._log_fp = _open_log("index")
        rc_or_status = "failed"

        # Two routes:
        # 1. If the host is alive, POST `/api/admin/index` and let it run
        #    in-process (it owns the workspace + writer-lock dance).
        # 2. Otherwise spawn a `docgraph index` subprocess.
        # Spawning the subprocess in route 2 only when the host isn't
        # alive avoids the "Could not set lock on file" crash you'd get
        # if both held connections to the same Kuzu file at once.
        # Cancellation: cancel() POSTs /api/admin/cancel?root=<slug> first
        # (so the host's cooperative-cancel token fires), then cancels
        # the local task. The host returns HTTP 499 when the token trips
        # at a checkpoint; _index_via_host re-raises CancelledError on
        # 499 and the finally-block clears local state.
        try:
            host_route = False
            try:
                if _HOST is not None and _HOST.alive() and _HOST.port():
                    host_route = True
                    self._log_fp.write(
                        f"\n--- index {path} via host /api/admin/index "
                        f"({'--full' if force else 'incremental'}) ---\n".encode()
                    )
                    self._log_fp.flush()
                    ok, detail = await _index_via_host(path, force, _HOST.port(),
                                                        log_fp=self._log_fp)
                    self._log_fp.write(detail.encode("utf-8", errors="replace") + b"\n")
                    self._log_fp.flush()
                    rc_or_status = "ok" if ok else "failed"
                    self._last_status = "done" if ok else "failed"
            except asyncio.CancelledError:
                self._last_status = "cancelled"
                rc_or_status = "cancelled"
                host_route = True  # don't fall through to subprocess
                raise
            except Exception as exc:
                log.warning("docgraph index %s: host route failed: %s — falling back to subprocess", path, exc)
                self._log_fp.write(f"\n--- host route failed: {exc} — falling back to subprocess ---\n".encode())
                self._log_fp.flush()
                host_route = False

            if not host_route:
                try:
                    binary = _binary_or_raise()
                    argv = [binary, "index", path]
                    if force:
                        argv.append("--full")
                    if dg_cfg.index_workers() > 0:
                        argv += ["--workers", str(dg_cfg.index_workers())]
                    if dg_cfg.index_embed_batch_size() > 0:
                        argv += ["--embed-batch-size", str(dg_cfg.index_embed_batch_size())]
                    if dg_cfg.embeddings_gpu():
                        argv.append("--gpu")
                    if dg_cfg.embeddings_model():
                        argv += ["--embed-model", dg_cfg.embeddings_model()]
                    if dg_cfg.llm_model():
                        argv += ["--llm-model", dg_cfg.llm_model(),
                                 "--llm-host", dg_cfg.llm_host(),
                                 "--llm-port", str(dg_cfg.llm_port()),
                                 "--llm-format", dg_cfg.llm_format(),
                                 "--llm-max-tokens", str(dg_cfg.llm_max_tokens())]
                    # Long-form prompt overrides go through temp files so we
                    # avoid argv-length / quoting hazards. Files live next
                    # to the host's prompt files for consistency.
                    runtime_dir = os.path.join(
                        os.path.dirname(app_config.logs_dir()) or ".", "runtime",
                    )
                    docstring_text = (dg_cfg.llm_prompt_docstring() or "").strip()
                    if docstring_text:
                        try:
                            os.makedirs(runtime_dir, exist_ok=True)
                            tmp_path = os.path.join(runtime_dir, "docgraph_llm_prompt_docstring.txt")
                            with open(tmp_path, "w", encoding="utf-8") as f:
                                f.write(docstring_text)
                            argv += ["--llm-prompt-docstring-file", tmp_path]
                        except Exception as exc:
                            log.warning("docgraph index: failed to materialize docstring prompt: %s", exc)
                    if dg_cfg.documents_enabled():
                        argv.append("--documents")
                    text_exts = dg_cfg.text_extensions()
                    if text_exts and tuple(text_exts) != dg_cfg._DEFAULT_TEXT_EXTS:
                        argv += ["--text-exts", ",".join(text_exts)]
                    asset_exts = dg_cfg.asset_extensions()
                    if asset_exts and tuple(asset_exts) != dg_cfg._DEFAULT_ASSET_EXTS:
                        argv += ["--asset-exts", ",".join(asset_exts)]
                    # Only PYTHONIOENCODING / PYTHONUTF8 stay as env — they
                    # govern Python stdio encoding and have no CLI equivalent.
                    env = {
                        "PYTHONIOENCODING": "utf-8",
                        "PYTHONUTF8": "1",
                    }
                    self._log_fp.write(
                        f"\n--- index {path} {'(--full)' if force else '(incremental)'} ---\n".encode()
                    )
                    self._log_fp.flush()
                    self._proc = subprocess.Popen(
                        argv,
                        stdout=self._log_fp,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                        creationflags=_creation_flags(),
                        env={**os.environ, **env},
                    )
                    bind_to_lifetime_job(self._proc.pid, proc=self._proc)
                    rc = await asyncio.to_thread(self._proc.wait)
                    rc_or_status = "ok" if rc == 0 else "failed"
                    self._last_status = "done" if rc == 0 else "failed"
                    if rc != 0:
                        log.warning("docgraph index %s: rc=%s", path, rc)
                except asyncio.CancelledError:
                    self._last_status = "cancelled"
                    rc_or_status = "cancelled"
                    raise
                except Exception as exc:
                    log.error("docgraph index %s: %s", path, exc)
                    self._last_status = "failed"
                    rc_or_status = "failed"
        finally:
            # Always finalize even on CancelledError, otherwise current_path
            # never clears and the per-row pill stays stuck on "running…".
            index_state.update(path, status=rc_or_status, was_full=force)
            self._current_path = None
            self._current_force = False
            self._proc = None
            self._last_finished_at = time.time()
            if self._log_fp:
                try:
                    self._log_fp.close()
                except Exception:
                    pass
                self._log_fp = None


# ── Wiki runner (one-shot per root) ────────────────────────────────────────

class WikiRunner:
    """Runs `docgraph wiki <path>` (or `POST /api/wiki/build` against the
    host). Same two-route pattern as `IndexRunner` — host route avoids the
    Kuzu lock dance, subprocess route lets us pass tunable `--llm-*` flags.
    """

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._proc: Optional[subprocess.Popen] = None
        self._log_fp = None
        self._lock = asyncio.Lock()
        self._current_path: str | None = None
        self._current_force: bool = False
        self._last_status: str = "idle"
        self._last_finished_at: float = 0.0

    def alive(self) -> bool:
        return bool(self._task and not self._task.done())

    def current_path(self) -> str | None:
        return self._current_path

    def status(self) -> dict:
        return {
            "alive":             self.alive(),
            "current_path":      self._current_path,
            "current_force":     self._current_force,
            "last_status":       self._last_status,
            "last_finished_at":  self._last_finished_at,
            "log_path":          dg_cfg.log_path("wiki"),
            "per_path":          wiki_state.load(),
        }

    async def run(self, path: str, force: bool = False) -> None:
        if not path:
            raise RuntimeError("path is required")
        async with self._lock:
            if self.alive():
                log.warning("docgraph wiki: already running %s — ignoring %s",
                            self._current_path, path)
                return
            self._task = asyncio.create_task(self._run_one(path, force))

    async def run_all(self, force: bool = False) -> None:
        async with self._lock:
            if self.alive():
                return
            paths = dg_cfg.root_paths()
            if not paths:
                return
            self._task = asyncio.create_task(self._run_sequence(paths, force))

    async def cancel(self) -> None:
        async with self._lock:
            t = self._task
            current = self._current_path
            proc = self._proc
        if current:
            await _request_host_cancel(current)
        async with self._lock:
            if t and not t.done():
                t.cancel()
            if proc and proc.poll() is None:
                try:
                    kill_process_tree(proc.pid)
                except Exception:
                    pass

    async def _run_sequence(self, paths: list[str], force: bool) -> None:
        for path in paths:
            await self._run_one_inline(path, force)
            if self._last_status in ("failed", "cancelled"):
                return

    async def _run_one(self, path: str, force: bool) -> None:
        await self._run_one_inline(path, force)

    async def _run_one_inline(self, path: str, force: bool) -> None:
        self._last_status = "running"
        self._current_path = path
        self._current_force = force
        wiki_state.mark_running(path, was_full=force)
        self._log_fp = _open_log("wiki")
        rc_or_status = "failed"

        # Cancellation mirrors IndexRunner: cancel() POSTs /api/admin/cancel
        # first so the host's wiki loop sees the token at the next module
        # boundary; the HTTP 499 reply is mapped to CancelledError.
        try:
            host_route = False
            try:
                if _HOST is not None and _HOST.alive() and _HOST.port():
                    host_route = True
                    self._log_fp.write(
                        f"\n--- wiki {path} via host /api/wiki/build "
                        f"({'--force' if force else 'resumable'}) ---\n".encode()
                    )
                    self._log_fp.flush()
                    ok, detail = await _wiki_via_host(path, force, _HOST.port(),
                                                       log_fp=self._log_fp)
                    self._log_fp.write(detail.encode("utf-8", errors="replace") + b"\n")
                    self._log_fp.flush()
                    rc_or_status = "ok" if ok else "failed"
                    self._last_status = "done" if ok else "failed"
            except asyncio.CancelledError:
                self._last_status = "cancelled"
                rc_or_status = "cancelled"
                host_route = True
                raise
            except Exception as exc:
                log.warning("docgraph wiki %s: host route failed: %s — falling back to subprocess", path, exc)
                self._log_fp.write(f"\n--- host route failed: {exc} — falling back to subprocess ---\n".encode())
                self._log_fp.flush()
                host_route = False

            if not host_route:
                try:
                    binary = _binary_or_raise()
                    argv = [binary, "wiki", path]
                    if force:
                        argv.append("--force")
                    if dg_cfg.wiki_depth() and dg_cfg.wiki_depth() != 12:
                        argv += ["--depth", str(dg_cfg.wiki_depth())]
                    if dg_cfg.llm_model():
                        argv += ["--llm-model", dg_cfg.llm_model(),
                                 "--llm-host", dg_cfg.llm_host(),
                                 "--llm-port", str(dg_cfg.llm_port()),
                                 "--llm-format", dg_cfg.llm_format(),
                                 "--llm-max-tokens", str(dg_cfg.llm_max_tokens_wiki())]
                    runtime_dir = os.path.join(
                        os.path.dirname(app_config.logs_dir()) or ".", "runtime",
                    )
                    wiki_text = (dg_cfg.llm_prompt_wiki() or "").strip()
                    if wiki_text:
                        try:
                            os.makedirs(runtime_dir, exist_ok=True)
                            tmp_path = os.path.join(runtime_dir, "docgraph_llm_prompt_wiki.txt")
                            with open(tmp_path, "w", encoding="utf-8") as f:
                                f.write(wiki_text)
                            argv += ["--llm-prompt-wiki-file", tmp_path]
                        except Exception as exc:
                            log.warning("docgraph wiki: failed to materialize wiki prompt: %s", exc)
                    env = {
                        "PYTHONIOENCODING": "utf-8",
                        "PYTHONUTF8": "1",
                    }
                    self._log_fp.write(
                        f"\n--- wiki {path} {'(--force)' if force else '(resumable)'} ---\n".encode()
                    )
                    self._log_fp.flush()
                    self._proc = subprocess.Popen(
                        argv,
                        stdout=self._log_fp,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                        creationflags=_creation_flags(),
                        env={**os.environ, **env},
                    )
                    bind_to_lifetime_job(self._proc.pid, proc=self._proc)
                    rc = await asyncio.to_thread(self._proc.wait)
                    rc_or_status = "ok" if rc == 0 else "failed"
                    self._last_status = "done" if rc == 0 else "failed"
                    if rc != 0:
                        log.warning("docgraph wiki %s: rc=%s", path, rc)
                except asyncio.CancelledError:
                    self._last_status = "cancelled"
                    rc_or_status = "cancelled"
                    raise
                except Exception as exc:
                    log.error("docgraph wiki %s: %s", path, exc)
                    self._last_status = "failed"
                    rc_or_status = "failed"
        finally:
            wiki_state.update(path, status=rc_or_status, was_full=force)
            self._current_path = None
            self._current_force = False
            self._proc = None
            self._last_finished_at = time.time()
            if self._log_fp:
                try:
                    self._log_fp.close()
                except Exception:
                    pass
                self._log_fp = None


# ── Host (the unified docgraph server) ─────────────────────────────────────

class HostSupervisor:
    """Owns one `docgraph host --root … --root …` subprocess."""
    role = "host"

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._log_fp = None
        self._lock = asyncio.Lock()
        self._started_at: float = 0.0
        self._port: int | None = None
        self._restart_task: Optional[asyncio.Task] = None
        self._last_error: str | None = None
        self._bridged_count = 0

    def alive(self) -> bool:
        return _alive(self._proc)

    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def port(self) -> int | None:
        return self._port

    def started_at(self) -> float:
        return self._started_at

    def last_error(self) -> str | None:
        return self._last_error

    def bridged_count(self) -> int:
        return self._bridged_count

    def log_path(self) -> str:
        return dg_cfg.log_path(self.role)

    async def start(self) -> None:
        async with self._lock:
            if self.alive():
                return
            try:
                await self._start_locked()
                self._last_error = None
            except Exception as exc:
                self._last_error = str(exc)
                log.error("docgraph host start failed: %s", exc)
            raise

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def _start_locked(self) -> None:
        roots = dg_cfg.root_paths()
        if not roots:
            raise RuntimeError(
                "docgraph.roots is empty — add at least one root in settings."
            )
        binary = _binary_or_raise()
        port = dg_cfg.host_port()
        host_addr = dg_cfg.host_host()
        argv = [binary, "host", "--host", host_addr, "--port", str(port)]
        for r in roots:
            argv += ["--root", r]
        watched = [w for w in dg_cfg.root_paths_to_watch() if w in roots]
        for w in watched:
            argv += ["--watch", w]
        if watched and dg_cfg.host_debounce() and dg_cfg.host_debounce() != 500:
            argv += ["--debounce", str(dg_cfg.host_debounce())]
        # Configuration goes through `docgraph host` CLI flags rather than
        # env vars. Keeps the spawn introspectable from `ps` / Process Hacker
        # and removes the historical drift between settings.json keys and
        # DOCGRAPH_* names. The flags below mirror Config.from_env one-for-one.
        # Single source of truth for GPU embeddings — `embeddings.gpu` —
        # used by both the host spawn and the index-subprocess fallback.
        if dg_cfg.embeddings_gpu():
            argv.append("--gpu")
        if dg_cfg.embeddings_model():
            argv += ["--embed-model", dg_cfg.embeddings_model()]
        if dg_cfg.rerank_model():
            argv += ["--rerank-model", dg_cfg.rerank_model()]
        if dg_cfg.rerank_default():
            argv.append("--rerank-default")
        if dg_cfg.rerank_gpu():
            argv.append("--rerank-gpu")
        # Indexer + wiki tunables that affect /api/admin/index and
        # /api/wiki/build inside the host. These must travel with the host
        # spawn, not just the fallback subprocess, since the host route is
        # the primary path now.
        if dg_cfg.index_workers() > 0:
            argv += ["--workers", str(dg_cfg.index_workers())]
        if dg_cfg.index_embed_batch_size() > 0:
            argv += ["--embed-batch-size", str(dg_cfg.index_embed_batch_size())]
        if dg_cfg.wiki_depth() and dg_cfg.wiki_depth() != 12:
            argv += ["--wiki-depth", str(dg_cfg.wiki_depth())]
        # LLM augmentation knobs — only forwarded when a model is configured
        # (setting just the model implies enable, mirroring docgraph's CLI).
        if dg_cfg.llm_model():
            argv += ["--llm-model", dg_cfg.llm_model()]
            if dg_cfg.llm_host():
                argv += ["--llm-host", dg_cfg.llm_host()]
            if dg_cfg.llm_port():
                argv += ["--llm-port", str(dg_cfg.llm_port())]
            if dg_cfg.llm_format():
                argv += ["--llm-format", dg_cfg.llm_format()]
            if dg_cfg.llm_max_tokens():
                argv += ["--llm-max-tokens", str(dg_cfg.llm_max_tokens())]
            if dg_cfg.llm_max_tokens_wiki():
                argv += ["--llm-max-tokens-wiki", str(dg_cfg.llm_max_tokens_wiki())]
            if dg_cfg.llm_api_key():
                argv += ["--llm-api-key", dg_cfg.llm_api_key()]
            if dg_cfg.llm_timeout() > 0:
                argv += ["--llm-timeout", str(dg_cfg.llm_timeout())]
            argv.append("--llm-docstrings" if dg_cfg.llm_docstrings() else "--no-llm-docstrings")
        argv.append("--llm-wiki" if dg_cfg.llm_wiki() else "--no-llm-wiki")
        # Long-form prompt overrides — write the text to a temp file so we
        # can pass --llm-prompt-*-file rather than smuggling multi-line
        # content through argv (Windows command-line length limits, escaping
        # hassle). Files live under data/runtime/ and are overwritten each
        # spawn so they stay in sync with settings.json.
        for kind, text in (("docstring", dg_cfg.llm_prompt_docstring()),
                           ("wiki",      dg_cfg.llm_prompt_wiki())):
            text = (text or "").strip()
            if not text:
                continue
            try:
                runtime_dir = os.path.join(
                    os.path.dirname(app_config.logs_dir()) or ".", "runtime",
                )
                os.makedirs(runtime_dir, exist_ok=True)
                tmp_path = os.path.join(runtime_dir, f"docgraph_llm_prompt_{kind}.txt")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(text)
                argv += [f"--llm-prompt-{kind}-file", tmp_path]
            except Exception as exc:
                log.warning("docgraph host: failed to materialize %s prompt: %s", kind, exc)
        # Document + asset indexing — the master toggle plus optional
        # extension overrides. Empty strings would still flip --documents
        # on via the implies-rule, so only emit when they actually differ.
        if dg_cfg.documents_enabled():
            argv.append("--documents")
        text_exts = dg_cfg.text_extensions()
        if text_exts and tuple(text_exts) != dg_cfg._DEFAULT_TEXT_EXTS:
            argv += ["--text-exts", ",".join(text_exts)]
        asset_exts = dg_cfg.asset_extensions()
        if asset_exts and tuple(asset_exts) != dg_cfg._DEFAULT_ASSET_EXTS:
            argv += ["--asset-exts", ",".join(asset_exts)]

        self._port = port
        self._log_fp = _open_log(self.role)
        try:
            sweep_port(self._port, ("docgraph",))
        except Exception:
            pass
        self._proc = self._spawn(argv, extra_env=None)
        self._started_at = time.time()
        try:
            await self._wait_ready()
        except Exception:
            await self._stop_locked()
            raise
        try:
            self._bridged_count = await dg_bridge.bridge_host(
                host=host_addr, port=port,
            )
        except Exception as exc:
            log.error("docgraph host: bridge failed: %s", exc)
            self._bridged_count = 0
        self._arm_auto_restart(dg_cfg.host_auto_restart())

    async def _stop_locked(self) -> None:
        try:
            dg_bridge.unbridge_host()
        except Exception:
            pass
        self._bridged_count = 0
        proc = self._proc
        self._proc = None
        if self._restart_task and not self._restart_task.done():
            self._restart_task.cancel()
        self._restart_task = None
        if proc and proc.poll() is None:
            try:
                kill_process_tree(proc.pid)
            except Exception as exc:
                log.warning("docgraph host: graceful kill failed: %s", exc)
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass
        if self._log_fp:
            try:
                self._log_fp.close()
            except Exception:
                pass
            self._log_fp = None
        if self._port:
            try:
                sweep_port(self._port, ("docgraph",))
            except Exception:
                pass

    async def _wait_ready(self) -> None:
        deadline = asyncio.get_event_loop().time() + 30
        url = f"http://{dg_cfg.host_host()}:{self._port}/api/roots"
        async with aiohttp.ClientSession() as session:
            while asyncio.get_event_loop().time() < deadline:
                if not _alive(self._proc):
                    raise RuntimeError(
                        f"docgraph host exited during startup; see {self.log_path()}"
                    )
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                        if resp.status == 200:
                            return
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass
                await asyncio.sleep(0.5)
        raise RuntimeError(f"docgraph host not ready within 30s")

    def _spawn(self, argv: list[str], extra_env: dict[str, str] | None = None) -> subprocess.Popen:
        env = dict(os.environ)
        # Force UTF-8 for the child's stdio. Otherwise on Windows the
        # subprocess inherits cp1252, and Rich's braille spinners
        # (⠋ etc.) crash any line that hits stdout/stderr → log
        # file. Affects host AND index runs.
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        if extra_env:
            env.update(extra_env)
        binary = argv[0]
        argv[0] = shutil.which(binary) or binary
        # On Windows hybrid graphics, processes spawned with CREATE_NO_WINDOW
        # default to the power-saving GPU (Intel iGPU) for DXGI / DirectML
        # adapter enumeration. ONNX Runtime then lands on the iGPU and either
        # runs slow or device-hangs under sustained inference load. Setting
        # the per-app GpuPreference=2 (High Performance) override in
        # HKCU\Software\Microsoft\DirectX\UserGpuPreferences forces Windows
        # to expose the discrete GPU to that exe regardless of windowed
        # state. Idempotent — only writes when the value is missing or
        # different. No-op off Windows.
        _ensure_high_perf_gpu(argv[0])
        proc = subprocess.Popen(
            argv,
            stdout=self._log_fp,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=_creation_flags(),
            env=env,
        )
        if bind_to_lifetime_job(proc.pid, proc=proc):
            log.info("docgraph host: pid %d bound to lifetime job", proc.pid)
        return proc

    def _arm_auto_restart(self, enabled: bool) -> None:
        if not enabled:
            return
        if self._restart_task and not self._restart_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._restart_task = loop.create_task(self._auto_restart_loop())

#     async def _auto_restart_loop(self) -> None:
#         while True:
#             await asyncio.sleep(2.0)
#             proc = self._proc
#             if proc is None:
#                 return
#             if proc.poll() is None:
#                 continue
#             log.warning("docgraph host: subprocess exited (code %s) — restarting",
#                         proc.returncode)
#             try:
#                 async with self._lock:
#                     await self._stop_locked()
#                     await self._start_locked()
#             except Exception as exc:
#                 log.error("docgraph host: auto-restart failed: %s", exc)
#                 return


# ── Module singletons ──────────────────────────────────────────────────────

_INDEX: IndexRunner | None = None
_WIKI: WikiRunner | None = None
_HOST: HostSupervisor | None = None


def get_index() -> IndexRunner:
    global _INDEX
    if _INDEX is None:
        _INDEX = IndexRunner()
    return _INDEX


def get_wiki() -> WikiRunner:
    global _WIKI
    if _WIKI is None:
        _WIKI = WikiRunner()
    return _WIKI


def get_host() -> HostSupervisor:
    global _HOST
    if _HOST is None:
        _HOST = HostSupervisor()
    return _HOST


# ── Boot / shutdown hooks ──────────────────────────────────────────────────

async def autostart_all() -> None:
    """Called from main.py:_post_init.

    Auto-start fires on `host.auto_start` alone — the older two-flag gate
    (`enabled AND auto_start`) made the UI confusing because users would
    flip Auto-start on, expect it to start, and find Enabled silently
    blocking it. Now Auto-start is the single switch for boot behavior.
    `enabled` remains the live-state flag (Stop / Start / Restart in the
    tray), independent of boot persistence.
    """
    if dg_cfg.host_auto_start():
        try:
            await get_host().start()
        except Exception as exc:
            log.error("docgraph host auto-start: %s", exc)


async def shutdown_all() -> None:
    """Called from main.py:_post_shutdown."""
    if _HOST is not None:
        try:
            await _HOST.stop()
        except Exception:
            pass
    if _INDEX is not None:
        try:
            await _INDEX.cancel()
        except Exception:
            pass
    if _WIKI is not None:
        try:
            await _WIKI.cancel()
        except Exception:
            pass


def status_snapshot() -> dict:
    """Used by tray/qt_helpers.build_status()."""
    binary = dg_cfg.resolve_binary()
    return {
        "binary":       binary,
        "binary_ok":    binary is not None,
        "default_path": dg_cfg.default_path(),
        "index":        (_INDEX.status() if _INDEX else {"alive": False, "last_status": "idle"}),
        "host": {
            "enabled":    dg_cfg.host_enabled(),
            "alive":      bool(_HOST and _HOST.alive()),
            "pid":        (_HOST.pid() if _HOST else None),
            "port":       (_HOST.port() if _HOST else None),
            "started_at": (_HOST.started_at() if _HOST else 0.0),
            "bridged":    (_HOST.bridged_count() if _HOST else 0),
            "last_error": (_HOST.last_error() if _HOST else None),
            "log_path":   dg_cfg.log_path("host"),
        },
    }
