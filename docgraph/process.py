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
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Optional

import aiohttp

from process import bind_to_lifetime_job, kill_process_tree, sweep_port

from . import config as dg_cfg
from . import bridge as dg_bridge
from . import index_state

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


def _alive(proc: subprocess.Popen | None) -> bool:
    return bool(proc and proc.poll() is None)


async def _index_via_host(path: str, full: bool, port: int) -> tuple[bool, str]:
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
        # 2) Trigger the index pass.
        url = f"{base}/api/admin/index?root={slug}"
        try:
            async with session.post(url, json={"full": full}) as resp:
                body = await resp.text()
                if resp.status != 200:
                    return False, f"POST /api/admin/index → HTTP {resp.status}: {body[:200]}"
                return True, body[:2000]
        except Exception as exc:
            return False, f"POST /api/admin/index failed: {exc}"


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
        async with self._lock:
            t = self._task
            if t and not t.done():
                t.cancel()
            if self._proc and self._proc.poll() is None:
                try:
                    kill_process_tree(self._proc.pid)
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
        host_route = False
        try:
            if _HOST is not None and _HOST.alive() and _HOST.port():
                host_route = True
                self._log_fp.write(
                    f"\n--- index {path} via host /api/admin/index "
                    f"({'--full' if force else 'incremental'}) ---\n".encode()
                )
                self._log_fp.flush()
                ok, detail = await _index_via_host(path, force, _HOST.port())
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
                if dg_cfg.embeddings_gpu():
                    argv.append("--gpu")
                if dg_cfg.llm_model():
                    argv += ["--llm-model", dg_cfg.llm_model(),
                             "--llm-host", dg_cfg.llm_host(),
                             "--llm-port", str(dg_cfg.llm_port()),
                             "--llm-format", dg_cfg.llm_format(),
                             "--llm-max-tokens", str(dg_cfg.llm_max_tokens())]
                env = {
                    # Force UTF-8 stdio so Rich progress glyphs don't crash
                    # the log writer on Windows (cp1252 default).
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONUTF8": "1",
                }
                if dg_cfg.embeddings_model():
                    env["DOCGRAPH_EMBED_MODEL"] = dg_cfg.embeddings_model()
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

        # Always finalize: persist per-path state, clear current, close log.
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
        for w in dg_cfg.root_paths_to_watch():
            if w in roots:
                argv += ["--watch", w]
        env = {}
        if dg_cfg.host_gpu():
            env["DOCGRAPH_GPU"] = "1"
        if dg_cfg.embeddings_model():
            env["DOCGRAPH_EMBED_MODEL"] = dg_cfg.embeddings_model()

        self._port = port
        self._log_fp = _open_log(self.role)
        try:
            sweep_port(self._port, ("docgraph",))
        except Exception:
            pass
        self._proc = self._spawn(argv, extra_env=env)
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

    async def _auto_restart_loop(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            proc = self._proc
            if proc is None:
                return
            if proc.poll() is None:
                continue
            log.warning("docgraph host: subprocess exited (code %s) — restarting",
                        proc.returncode)
            try:
                async with self._lock:
                    await self._stop_locked()
                    await self._start_locked()
            except Exception as exc:
                log.error("docgraph host: auto-restart failed: %s", exc)
                return


# ── Module singletons ──────────────────────────────────────────────────────

_INDEX: IndexRunner | None = None
_HOST: HostSupervisor | None = None


def get_index() -> IndexRunner:
    global _INDEX
    if _INDEX is None:
        _INDEX = IndexRunner()
    return _INDEX


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
