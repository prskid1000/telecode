"""llama-server subprocess supervisor.

One active process at a time. Model-swap restarts the subprocess with a
new argv and waits for /health to report "ok" before returning. All stdout
and stderr stream into data/logs/llama.log.

Public surface:
    sup = await get_supervisor()        # idempotent, spawns on first call
    await sup.ensure_model("qwen-35b")  # swap if active model differs
    await sup.stop()                    # graceful shutdown
    await shutdown_supervisor()         # module-level shutdown

Thread-safety: all async; caller must await from the event loop.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import signal
import subprocess
import sys
from typing import Optional

import aiohttp

from llamacpp import config as cfg
from llamacpp import state as llama_state
from llamacpp.argv import build_argv, describe
from proc_group import bind_to_lifetime_job, kill_process_tree


log = logging.getLogger("telecode.llamacpp")


class LlamaSupervisor:
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._active_model: str = ""
        self._lock = asyncio.Lock()
        self._log_fp = None  # file object for llama.log
        self._swap_token = 0  # increments on every swap; cancels readers
        # ── Lazy load + idle unload bookkeeping ─────────────────────
        self._last_used: float = 0.0           # epoch seconds; 0 = never
        self._inflight: int = 0                # active upstream calls
        self._inflight_lock = asyncio.Lock()
        self._idle_task: Optional[asyncio.Task] = None
        self._loaded_at: float = 0.0           # for "uptime" display

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def ensure_model(self, model_name: str) -> str:
        """Make sure `model_name` (or a resolved alias) is the active model.

        Lazy load: spawns llama-server on demand if nothing is running.
        Records last-used timestamp so the idle-unload watcher knows when
        the model went cold.

        Raises RuntimeError on spawn failure / readiness timeout.
        """
        import time as _t
        resolved = cfg.resolve_model(model_name)
        if not resolved:
            raise RuntimeError(f"No llama.cpp model registered for '{model_name}'")

        self._last_used = _t.time()

        async with self._lock:
            if self._proc and self._proc.poll() is None and self._active_model == resolved:
                self._ensure_idle_watcher()
                return resolved
            await self._stop_locked()
            await self._spawn_locked(resolved)
            self._loaded_at = _t.time()
            self._ensure_idle_watcher()
            # Persist the currently-active model so the next telecode launch
            # can preload it (see main.py startup).
            try:
                llama_state.save(resolved)
            except Exception:
                pass
            return resolved

    # ── Activity tracking (for the idle watcher) ─────────────────────

    async def begin_request(self) -> None:
        """Caller acquires this around an upstream call so the idle
        watcher won't unload mid-stream. Pair with `end_request`."""
        import time as _t
        async with self._inflight_lock:
            self._inflight += 1
            self._last_used = _t.time()

    async def end_request(self) -> None:
        import time as _t
        async with self._inflight_lock:
            self._inflight = max(0, self._inflight - 1)
            self._last_used = _t.time()

    def inflight_count(self) -> int:
        return self._inflight

    def last_used(self) -> float:
        return self._last_used

    def loaded_at(self) -> float:
        return self._loaded_at

    # ── Idle unload watcher ──────────────────────────────────────────

    def _ensure_idle_watcher(self) -> None:
        """Start the background idle-unload task if not already running."""
        if self._idle_task is not None and not self._idle_task.done():
            return
        if cfg.idle_unload_sec() <= 0:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._idle_task = loop.create_task(self._idle_watcher_loop())

    async def _idle_watcher_loop(self) -> None:
        import time as _t
        idle_limit = cfg.idle_unload_sec()
        while True:
            await asyncio.sleep(min(30.0, max(5.0, idle_limit / 6)))
            if not self.alive():
                return  # already unloaded
            if self._inflight > 0:
                continue
            if self._last_used == 0:
                continue
            idle_for = _t.time() - self._last_used
            if idle_for >= idle_limit:
                log.info("llama.cpp: idle for %.0fs ≥ %.0fs limit — unloading", idle_for, idle_limit)
                try:
                    await self.stop()
                except Exception as exc:
                    log.warning("idle unload failed: %s", exc)
                return  # next request will re-spawn

    async def start_default(self) -> str:
        """Spawn the default model on startup. Returns the active model key."""
        default = cfg.default_model()
        if not default:
            raise RuntimeError("llamacpp.models is empty — register at least one model")
        return await self.ensure_model(default)

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    def active_model(self) -> str:
        return self._active_model

    def alive(self) -> bool:
        return bool(self._proc and self._proc.poll() is None)

    # ── Internal ─────────────────────────────────────────────────────────

    async def _spawn_locked(self, model_name: str) -> None:
        argv = build_argv(model_name)
        binary = argv[0]
        resolved_binary = shutil.which(binary) or binary
        argv[0] = resolved_binary

        # Open/reopen log file in append mode
        log_path = cfg.log_file()
        self._log_fp = open(log_path, "ab", buffering=0)  # unbuffered bytes

        self._log_fp.write(
            f"\n\n===== telecode: spawning llama-server for '{model_name}' =====\n"
            .encode()
        )
        self._log_fp.write(f"argv: {describe(model_name)}\n".encode())
        self._log_fp.flush()

        log.info("llama.cpp: spawning '%s' on %s:%d", model_name, cfg.host(), cfg.port())

        creation = 0
        if sys.platform == "win32":
            creation = subprocess.CREATE_NO_WINDOW  # no console popup under pythonw

        try:
            self._proc = subprocess.Popen(
                argv,
                stdout=self._log_fp,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=creation,
            )
        except FileNotFoundError as exc:
            self._log_fp.close()
            self._log_fp = None
            raise RuntimeError(
                f"llama.cpp binary not found: {binary}. Set llamacpp.binary "
                f"in settings.json to the absolute path."
            ) from exc

        # Bind to the shared kill-on-close Job Object so the OS kills
        # llama-server if THIS Python process dies for any reason (Ctrl+C
        # bypassing _post_shutdown, Task Manager End Process, pythonw crash).
        # No-op on non-Windows.
        if bind_to_lifetime_job(self._proc.pid, proc=self._proc):
            log.info("llama.cpp: pid %d bound to lifetime job", self._proc.pid)

        self._active_model = model_name

        # Wait for /health to return ok (or the process to die)
        try:
            await self._wait_ready()
        except Exception:
            await self._stop_locked()
            raise

        log.info("llama.cpp: '%s' ready (pid %d)", model_name, self._proc.pid)

    async def _wait_ready(self) -> None:
        deadline = asyncio.get_event_loop().time() + cfg.ready_timeout_sec()
        url = f"{cfg.upstream_url()}/health"
        last_err: Exception | None = None

        async with aiohttp.ClientSession() as session:
            while asyncio.get_event_loop().time() < deadline:
                if self._proc is None or self._proc.poll() is not None:
                    raise RuntimeError(
                        f"llama-server exited during startup "
                        f"(code {self._proc.returncode if self._proc else '?'}); "
                        f"see {cfg.log_file()}"
                    )
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=2)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json(content_type=None)
                            status = (data or {}).get("status", "")
                            # "ok" = loaded + ready; "loading model" = still spinning up
                            if status == "ok":
                                return
                        elif resp.status == 503:
                            # llama-server returns 503 while still loading
                            pass
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    last_err = exc

                await asyncio.sleep(1.0)

        raise RuntimeError(
            f"llama-server did not become ready within {cfg.ready_timeout_sec()}s "
            f"(last error: {last_err}); see {cfg.log_file()}"
        )

    async def _stop_locked(self) -> None:
        proc = self._proc
        self._proc = None
        self._active_model = ""

        if proc is None or proc.poll() is not None:
            if self._log_fp:
                try:
                    self._log_fp.close()
                except OSError:
                    pass
                self._log_fp = None
            return

        # Graceful tree-kill first (catches any worker processes llama-server
        # might have spawned). `kill_process_tree` uses taskkill /T on
        # Windows, os.killpg on Unix — both walk the process tree.
        kill_process_tree(proc.pid, force=False)

        # Give the tree a few seconds to exit
        for _ in range(40):  # 40 * 0.1s = 4s
            if proc.poll() is not None:
                break
            await asyncio.sleep(0.1)

        if proc.poll() is None:
            # Force the tree down
            kill_process_tree(proc.pid, force=True)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

        if self._log_fp:
            try:
                self._log_fp.write(b"===== llama-server stopped =====\n")
                self._log_fp.close()
            except OSError:
                pass
            self._log_fp = None


# ── Module-level singleton ──────────────────────────────────────────────

_SUPERVISOR: LlamaSupervisor | None = None


async def get_supervisor() -> LlamaSupervisor:
    """Get or create the global supervisor. Does not start a model yet —
    call `ensure_model` or `start_default` after."""
    global _SUPERVISOR
    if _SUPERVISOR is None:
        _SUPERVISOR = LlamaSupervisor()
    return _SUPERVISOR


async def shutdown_supervisor() -> None:
    global _SUPERVISOR
    if _SUPERVISOR is not None:
        try:
            await _SUPERVISOR.stop()
        finally:
            _SUPERVISOR = None
