"""DocGraph subprocess supervisors.

Five roles, one supervisor class each, modeled on `process.LlamaSupervisor`:
    IndexRunner, WatchSupervisor, ServeSupervisor, DaemonSupervisor, McpSupervisor

Public surface used by main.py + the tray UI:
    autostart_all(), shutdown_all()
    get_watch() / get_serve() / get_daemon() / get_mcp() / get_index()

Lock coordination (DocGraph's writer lock blocks readers — see its CLAUDE.md):
    Watch / Index for path P  ->  stop Serve/MCP/Daemon for P first
    Serve / MCP for path P     ->  reject if Watch holds P
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Optional

import aiohttp

from process import bind_to_lifetime_job, kill_process_tree, sweep_port

from . import config as dg_cfg
from . import bridge as dg_bridge
from . import index_state

log = logging.getLogger("telecode.docgraph")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _binary_or_raise() -> str:
    binary = dg_cfg.resolve_binary()
    if not binary:
        raise RuntimeError(
            "docgraph binary not found. Set `docgraph.binary` in settings.json or "
            "install docgraph on PATH (`pipx install docgraph`)."
        )
    return binary


def _open_log(role: str, slug: str | None = None):
    path = dg_cfg.log_path(role, slug)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fp = open(path, "ab", buffering=0)
    fp.write(f"\n\n===== telecode: spawning docgraph {role}".encode()
             + (f" ({slug})".encode() if slug else b"")
             + b" =====\n")
    fp.flush()
    return fp


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _alive(proc: subprocess.Popen | None) -> bool:
    return bool(proc and proc.poll() is None)


# ── Base supervisor (long-running) ───────────────────────────────────────────

class _BaseSupervisor:
    """Shared lifecycle for Watch / Serve / Daemon / one MCP child."""
    role: str = "base"

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._log_fp = None
        self._lock = asyncio.Lock()
        self._started_at: float = 0.0
        self._restart_task: Optional[asyncio.Task] = None
        self._slug: str | None = None
        self._port: int | None = None

    def alive(self) -> bool:
        return _alive(self._proc)

    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def port(self) -> int | None:
        return self._port

    def started_at(self) -> float:
        return self._started_at

    def log_path(self) -> str:
        return dg_cfg.log_path(self.role, self._slug)

    async def start(self) -> None:
        async with self._lock:
            if self.alive():
                return
            await self._start_locked()

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def _start_locked(self) -> None:
        raise NotImplementedError

    async def _stop_locked(self) -> None:
        proc = self._proc
        self._proc = None
        if self._restart_task and not self._restart_task.done():
            self._restart_task.cancel()
        self._restart_task = None
        if proc and proc.poll() is None:
            try:
                kill_process_tree(proc.pid)
            except Exception as exc:
                log.warning("docgraph %s: graceful kill failed: %s", self.role, exc)
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

    def _spawn(self, argv: list[str], extra_env: dict[str, str] | None = None) -> subprocess.Popen:
        env = dict(os.environ)
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
            log.info("docgraph %s: pid %d bound to lifetime job", self.role, proc.pid)
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
            log.warning("docgraph %s: subprocess exited (code %s) — restarting",
                        self.role, proc.returncode)
            try:
                async with self._lock:
                    await self._stop_locked()
                    await self._start_locked()
            except Exception as exc:
                log.error("docgraph %s: auto-restart failed: %s", self.role, exc)
                return


# ── Index (one-shot) ─────────────────────────────────────────────────────────

class IndexRunner:
    """Per-path index runs. One at a time (lock). Cancellable.

    `run(path, force)` triggers a single repo's reindex. `run_all(force)`
    is a convenience that walks `docgraph.index.paths` in sequence.
    Per-path outcomes are persisted via `index_state.update()` so the UI
    can show "last: <when> · ok|failed" pills.
    """

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._proc: Optional[subprocess.Popen] = None
        self._log_fp = None
        self._lock = asyncio.Lock()
        self._current_path: str | None = None
        self._current_force: bool = False
        self._last_status: str = "idle"  # idle | running | done | failed | cancelled
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
            paths = [p for p in (dg_cfg.index_paths() or []) if p]
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
        try:
            binary = _binary_or_raise()
            argv = [binary, "index", path]
            if force:
                argv.append("--full")
            if dg_cfg.index_workers() > 0:
                argv += ["--workers", str(dg_cfg.index_workers())]
            if dg_cfg.index_gpu():
                argv.append("--gpu")
            if dg_cfg.index_llm_model():
                argv += ["--llm-model", dg_cfg.index_llm_model(),
                         "--llm-host", dg_cfg.index_llm_host(),
                         "--llm-port", str(dg_cfg.index_llm_port()),
                         "--llm-format", dg_cfg.index_llm_format(),
                         "--llm-max-tokens", str(dg_cfg.index_llm_max_tokens())]
            env = {}
            if dg_cfg.index_embedding_model():
                env["DOCGRAPH_EMBED_MODEL"] = dg_cfg.index_embedding_model()
            # Coordinate: stop readers for this path while index runs.
            await _stop_readers_for_path(path)
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
            await _autostart_readers()


# ── Watch ────────────────────────────────────────────────────────────────────

class WatchSupervisor(_BaseSupervisor):
    role = "watch"

    async def _start_locked(self) -> None:
        path = dg_cfg.watch_path()
        if not path:
            raise RuntimeError("docgraph.watch.path is empty")
        binary = _binary_or_raise()
        argv = [binary, "watch", path]
        if dg_cfg.watch_serve_too():
            argv += ["--serve",
                     "--host", dg_cfg.watch_host(),
                     "--port", str(dg_cfg.watch_port())]
            self._port = dg_cfg.watch_port()
        # Watch holds writer lock — stop readers (Serve / MCP / Daemon) for this path.
        await _stop_readers_for_path(path)
        self._slug = dg_cfg.slug_for_path(path)
        self._log_fp = _open_log(self.role)
        if self._port:
            try:
                sweep_port(self._port, ("docgraph",))
            except Exception:
                pass
        self._proc = self._spawn(argv)
        self._started_at = time.time()
        self._arm_auto_restart(dg_cfg.watch_auto_restart())


# ── Serve ────────────────────────────────────────────────────────────────────

class ServeSupervisor(_BaseSupervisor):
    role = "serve"

    async def _start_locked(self) -> None:
        path = dg_cfg.serve_path()
        if not path:
            raise RuntimeError("docgraph.serve.path is empty")
        if _watch_holds(path):
            raise RuntimeError(
                f"docgraph watch is running for {path} — stop it before starting serve"
            )
        binary = _binary_or_raise()
        argv = [binary, "serve", path,
                "--host", dg_cfg.serve_host(),
                "--port", str(dg_cfg.serve_port())]
        env = {}
        if dg_cfg.serve_gpu():
            env["DOCGRAPH_GPU"] = "1"
        self._port = dg_cfg.serve_port()
        self._slug = dg_cfg.slug_for_path(path)
        self._log_fp = _open_log(self.role)
        try:
            sweep_port(self._port, ("docgraph",))
        except Exception:
            pass
        self._proc = self._spawn(argv, extra_env=env)
        self._started_at = time.time()
        self._arm_auto_restart(dg_cfg.serve_auto_restart())


# ── Daemon ───────────────────────────────────────────────────────────────────

class DaemonSupervisor(_BaseSupervisor):
    role = "daemon"

    async def _start_locked(self) -> None:
        binary = _binary_or_raise()
        argv = [binary, "daemon", "start",
                "--port", str(dg_cfg.daemon_port()),
                "--model", dg_cfg.daemon_model()]
        if dg_cfg.daemon_gpu():
            argv.append("--gpu")
        self._port = dg_cfg.daemon_port()
        self._log_fp = _open_log(self.role)
        try:
            sweep_port(self._port, ("docgraph",))
        except Exception:
            pass
        self._proc = self._spawn(argv)
        self._started_at = time.time()
        self._arm_auto_restart(dg_cfg.daemon_auto_restart())


# ── MCP (multiple children, one per repo) ────────────────────────────────────

class _McpChild(_BaseSupervisor):
    role = "mcp"

    def __init__(self, path: str, port: int) -> None:
        super().__init__()
        self._path = path
        self._port = port
        self._slug = dg_cfg.slug_for_path(path)
        self._bridged_count = 0

    @property
    def path(self) -> str:
        return self._path

    @property
    def slug(self) -> str:
        return self._slug or ""

    def bridged_count(self) -> int:
        return self._bridged_count

    async def _start_locked(self) -> None:
        if _watch_holds(self._path):
            raise RuntimeError(
                f"docgraph watch is running for {self._path} — stop it before starting mcp"
            )
        binary = _binary_or_raise()
        argv = [binary, "mcp", self._path, "--transport", "http"]
        env = {"DOCGRAPH_PORT": str(self._port), "DOCGRAPH_HOST": dg_cfg.mcp_host()}
        if dg_cfg.mcp_gpu():
            env["DOCGRAPH_GPU"] = "1"
        self._log_fp = _open_log(self.role, self._slug)
        try:
            sweep_port(self._port, ("docgraph",))
        except Exception:
            pass
        self._proc = self._spawn(argv, extra_env=env)
        self._started_at = time.time()
        await self._wait_ready()
        # Bridge MCP tools into proxy.managed_tools._REGISTRY.
        try:
            self._bridged_count = await dg_bridge.bridge_child(
                slug=self._slug, port=self._port, host=dg_cfg.mcp_host(),
                multi=len(dg_cfg.mcp_paths()) > 1,
            )
        except Exception as exc:
            log.error("docgraph mcp %s: bridge failed: %s", self._slug, exc)
        self._arm_auto_restart(dg_cfg.mcp_auto_restart())

    async def _stop_locked(self) -> None:
        # Unregister bridged tools first, then kill the process.
        try:
            dg_bridge.unbridge_child(slug=self._slug or "")
        except Exception:
            pass
        self._bridged_count = 0
        await super()._stop_locked()

    async def _wait_ready(self) -> None:
        deadline = asyncio.get_event_loop().time() + dg_cfg.mcp_ready_timeout_sec()
        url = f"http://{dg_cfg.mcp_host()}:{self._port}/mcp"
        async with aiohttp.ClientSession() as session:
            while asyncio.get_event_loop().time() < deadline:
                if not _alive(self._proc):
                    raise RuntimeError(
                        f"docgraph mcp {self._slug} exited during startup; see {self.log_path()}"
                    )
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                        # The MCP streamable-HTTP endpoint responds to GET with
                        # 405/406 once the server is up — any HTTP response is
                        # a "ready" signal.
                        if resp.status < 500:
                            return
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass
                await asyncio.sleep(0.5)
        raise RuntimeError(
            f"docgraph mcp {self._slug} not ready within {dg_cfg.mcp_ready_timeout_sec()}s"
        )


class McpSupervisor:
    """Owns N `_McpChild` instances, one per `docgraph.mcp.paths` entry."""

    def __init__(self) -> None:
        self._children: dict[str, _McpChild] = {}  # path -> child
        self._lock = asyncio.Lock()

    def children(self) -> list[_McpChild]:
        return list(self._children.values())

    def alive(self) -> bool:
        return any(c.alive() for c in self._children.values())

    def status(self) -> list[dict]:
        return [
            {
                "path":     c.path,
                "slug":     c.slug,
                "port":     c.port(),
                "pid":      c.pid(),
                "alive":    c.alive(),
                "bridged":  c.bridged_count(),
                "log_path": c.log_path(),
            }
            for c in self._children.values()
        ]

    async def start(self) -> None:
        async with self._lock:
            paths = dg_cfg.mcp_paths()
            base = dg_cfg.mcp_base_port()
            # Drop children whose path is no longer configured.
            for stale in list(self._children.keys()):
                if stale not in paths:
                    await self._children[stale].stop()
                    self._children.pop(stale, None)
            for i, path in enumerate(paths):
                if path in self._children:
                    if not self._children[path].alive():
                        try:
                            await self._children[path].start()
                        except Exception as exc:
                            log.error("docgraph mcp %s: %s", path, exc)
                    continue
                child = _McpChild(path, base + i)
                self._children[path] = child
                try:
                    await child.start()
                except Exception as exc:
                    log.error("docgraph mcp %s: start failed: %s", path, exc)

    async def stop(self) -> None:
        async with self._lock:
            for child in list(self._children.values()):
                try:
                    await child.stop()
                except Exception:
                    pass
            self._children.clear()

    async def stop_path(self, path: str) -> None:
        async with self._lock:
            child = self._children.get(path)
            if child:
                await child.stop()
                self._children.pop(path, None)


# ── Module singletons ────────────────────────────────────────────────────────

_INDEX: IndexRunner | None = None
_WATCH: WatchSupervisor | None = None
_SERVE: ServeSupervisor | None = None
_DAEMON: DaemonSupervisor | None = None
_MCP: McpSupervisor | None = None


def get_index() -> IndexRunner:
    global _INDEX
    if _INDEX is None:
        _INDEX = IndexRunner()
    return _INDEX


def get_watch() -> WatchSupervisor:
    global _WATCH
    if _WATCH is None:
        _WATCH = WatchSupervisor()
    return _WATCH


def get_serve() -> ServeSupervisor:
    global _SERVE
    if _SERVE is None:
        _SERVE = ServeSupervisor()
    return _SERVE


def get_daemon() -> DaemonSupervisor:
    global _DAEMON
    if _DAEMON is None:
        _DAEMON = DaemonSupervisor()
    return _DAEMON


def get_mcp() -> McpSupervisor:
    global _MCP
    if _MCP is None:
        _MCP = McpSupervisor()
    return _MCP


# ── Lock-coordination helpers ────────────────────────────────────────────────

def _watch_holds(path: str) -> bool:
    if _WATCH is None or not _WATCH.alive():
        return False
    try:
        return os.path.normpath(dg_cfg.watch_path()) == os.path.normpath(path)
    except Exception:
        return False


async def _stop_readers_for_path(path: str) -> None:
    """Stop Serve / MCP children for `path` so writer-lock holders can run."""
    norm = os.path.normpath(path)
    if _SERVE is not None and _SERVE.alive():
        try:
            if os.path.normpath(dg_cfg.serve_path()) == norm:
                await _SERVE.stop()
        except Exception:
            pass
    if _MCP is not None:
        for child in list(_MCP.children()):
            if os.path.normpath(child.path) == norm:
                await _MCP.stop_path(child.path)


async def _autostart_readers() -> None:
    if dg_cfg.serve_enabled() and dg_cfg.serve_auto_start():
        try:
            await get_serve().start()
        except Exception as exc:
            log.warning("docgraph serve auto-restart: %s", exc)
    if dg_cfg.mcp_enabled() and dg_cfg.mcp_auto_start():
        try:
            await get_mcp().start()
        except Exception as exc:
            log.warning("docgraph mcp auto-restart: %s", exc)


# ── Boot / shutdown hooks ────────────────────────────────────────────────────

async def autostart_all() -> None:
    """Called from main.py:_post_init."""
    if dg_cfg.daemon_enabled() and dg_cfg.daemon_auto_start():
        try:
            await get_daemon().start()
        except Exception as exc:
            log.error("docgraph daemon auto-start: %s", exc)
    if dg_cfg.serve_enabled() and dg_cfg.serve_auto_start():
        try:
            await get_serve().start()
        except Exception as exc:
            log.error("docgraph serve auto-start: %s", exc)
    if dg_cfg.watch_enabled() and dg_cfg.watch_auto_start():
        try:
            await get_watch().start()
        except Exception as exc:
            log.error("docgraph watch auto-start: %s", exc)
    if dg_cfg.mcp_enabled() and dg_cfg.mcp_auto_start():
        try:
            await get_mcp().start()
        except Exception as exc:
            log.error("docgraph mcp auto-start: %s", exc)


async def shutdown_all() -> None:
    """Called from main.py:_post_shutdown. Reverse order: bridge -> mcp -> daemon -> serve -> watch."""
    if _MCP is not None:
        try:
            await _MCP.stop()
        except Exception:
            pass
    if _DAEMON is not None:
        try:
            await _DAEMON.stop()
        except Exception:
            pass
    if _SERVE is not None:
        try:
            await _SERVE.stop()
        except Exception:
            pass
    if _WATCH is not None:
        try:
            await _WATCH.stop()
        except Exception:
            pass
    if _INDEX is not None:
        try:
            await _INDEX.cancel()
        except Exception:
            pass


def status_snapshot() -> dict:
    """Used by tray/qt_helpers.build_status()."""
    return {
        "binary":   dg_cfg.resolve_binary(),
        "binary_ok": dg_cfg.resolve_binary() is not None,
        "index":    (_INDEX.status() if _INDEX else {"alive": False, "last_status": "idle"}),
        "watch":    {
            "enabled": dg_cfg.watch_enabled(),
            "alive":   bool(_WATCH and _WATCH.alive()),
            "pid":     (_WATCH.pid() if _WATCH else None),
            "port":    (_WATCH.port() if _WATCH else None),
            "log_path": dg_cfg.log_path("watch"),
        },
        "serve":    {
            "enabled": dg_cfg.serve_enabled(),
            "alive":   bool(_SERVE and _SERVE.alive()),
            "pid":     (_SERVE.pid() if _SERVE else None),
            "port":    (_SERVE.port() if _SERVE else None),
            "log_path": dg_cfg.log_path("serve"),
        },
        "daemon":   {
            "enabled": dg_cfg.daemon_enabled(),
            "alive":   bool(_DAEMON and _DAEMON.alive()),
            "pid":     (_DAEMON.pid() if _DAEMON else None),
            "port":    (_DAEMON.port() if _DAEMON else None),
            "log_path": dg_cfg.log_path("daemon"),
        },
        "mcp":      {
            "enabled":  dg_cfg.mcp_enabled(),
            "children": (_MCP.status() if _MCP else []),
        },
    }
