"""Subprocess lifecycle — everything process-related for telecode.

Two layers in one file:

1. **Generic primitives** (top half)
   - Windows kill-on-close Job Object: spawned children go down with the
     interpreter even on hard kill (Task Manager End Process, pythonw
     crash, OS logoff).
   - `kill_process_tree(pid, force=True)` — taskkill /T on Windows,
     killpg on Unix.
   - `sweep_port(port, whitelist)` — command-line-aware orphan killer.
     A previous telecode that died before its Job Object tore down
     llama-server leaves an orphan holding our port; the new spawn
     fails to bind. This sweeps only processes whose exe **or** command
     line matches a whitelist (so a foreign app on the same port is
     logged, not murdered).
   - `atexit` fallback: best-effort `proc.kill()` on clean exit when
     the Job Object path wasn't available (pywin32 missing).

2. **Sidecar supervisor** (bottom half)
   - `LlamaSupervisor` — one-active-model llama-server lifecycle with
     readiness probe (poll-death guard + post-/health stability check),
     idle-unload watcher, inflight request gating, graceful stop.
   - Module singleton via `get_supervisor()` / `shutdown_supervisor()`.

Everything here used to live in `proc_group.py` and
`llamacpp/supervisor.py`; consolidated so there's exactly one place
to look for process-lifecycle behaviour.
"""
from __future__ import annotations

import asyncio
import atexit
import logging
import os
import shutil
import signal as _sig
import socket
import subprocess
import sys
import time
from typing import Optional

import aiohttp

from llamacpp import config as cfg
from llamacpp import state as llama_state
from llamacpp.argv import build_argv, describe


log = logging.getLogger("telecode.process")


# ═══════════════════════════════════════════════════════════════════════
# Section 1 — Generic process primitives
# ═══════════════════════════════════════════════════════════════════════


# ── Kill-on-close Job Object (Windows crash safety) ──────────────────
#
# One process-wide Job Object flagged KILL_ON_JOB_CLOSE. Every spawned
# sidecar is assigned to it. When this Python interpreter exits for ANY
# reason (clean shutdown, Ctrl+C, Task Manager End Process, pythonw
# crash, OS logout) Windows releases the handle, the Job closes, and
# every assigned process dies. This is what prevents orphans from
# stealing our port across restarts.

_JOB_HANDLE = None
_TRACKED_PIDS: set[int] = set()
# Popen / asyncio.subprocess.Process objects for the atexit fallback
_TRACKED_PROCS: list = []


def _create_kill_on_close_job():
    """Idempotent: create the Job Object on first call. No-op off Windows."""
    global _JOB_HANDLE
    if sys.platform != "win32" or _JOB_HANDLE is not None:
        return _JOB_HANDLE
    try:
        import win32job
        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation
        )
        info["BasicLimitInformation"]["LimitFlags"] |= (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, info
        )
        _JOB_HANDLE = job
        log.info("created kill-on-close Job Object")
    except Exception as exc:
        log.warning("could not create Job Object (orphans possible on "
                    "hard-kill of telecode): %s", exc)
    return _JOB_HANDLE


def bind_to_lifetime_job(pid: int, proc=None) -> bool:
    """Assign `pid` to the kill-on-close Job Object.

    Pass `proc` too — we stash it for the atexit fallback even when the
    Job binding itself is available. Idempotent: re-binding the same PID
    is harmless. Returns True when the Job assignment succeeded (or the
    PID was already tracked), False otherwise.
    """
    if proc is not None and proc not in _TRACKED_PROCS:
        _TRACKED_PROCS.append(proc)

    if sys.platform != "win32":
        return False
    if pid <= 0 or pid in _TRACKED_PIDS:
        return True

    job = _create_kill_on_close_job()
    if job is None:
        return False
    try:
        import win32api
        import win32con
        import win32job
        ph = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, pid)
        try:
            win32job.AssignProcessToJobObject(job, ph)
        finally:
            win32api.CloseHandle(ph)
        _TRACKED_PIDS.add(pid)
        return True
    except Exception as exc:
        log.warning("could not assign PID %d to Job Object: %s", pid, exc)
        return False


# ── Tree kill ────────────────────────────────────────────────────────

def kill_process_tree(pid: int, force: bool = False, timeout: float = 5.0) -> bool:
    """Kill `pid` AND every descendant (child workers, etc.).

    Windows: `taskkill /T` walks the parent-PID map.
    Unix:    `killpg` on the process group (assumes setsid on spawn).

    Returns True on apparent success. The Job Object binding is strictly
    better for CRASH safety; this helper is for GRACEFUL shutdown paths
    where we need descendants gone before we move on.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        args = ["taskkill.exe", "/PID", str(pid), "/T"]
        if force:
            args.append("/F")
        try:
            subprocess.run(
                args,
                timeout=timeout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.warning("taskkill failed for pid %d: %s", pid, exc)
            return False
    try:
        os.killpg(os.getpgid(pid), _sig.SIGKILL if force else _sig.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError, OSError) as exc:
        log.debug("killpg failed for pid %d: %s", pid, exc)
        return False


# ── Port probe + orphan sweep ────────────────────────────────────────

def _port_in_use(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((host, port)) == 0
    except OSError:
        return False


def _pids_listening_on(port: int) -> list[int]:
    """PIDs holding 127.0.0.1:<port> in LISTEN state. Windows-only."""
    if sys.platform != "win32":
        return []
    ps = (
        "$c = Get-NetTCPConnection -LocalAddress 127.0.0.1 "
        f"-LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue; "
        "if ($c) { $c.OwningProcess | Sort-Object -Unique }"
    )
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=5.0, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (subprocess.TimeoutExpired, OSError):
        return []
    pids: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def _process_image(pid: int) -> tuple[str, str]:
    """Return (exe_path, command_line) for `pid`. Both "" if unknown.

    Both matter: a .exe under our sidecar tree is typically launched by
    pyenv's python.exe, so the owning process `Path` points at pyenv
    while the real identity is in argv — check both.
    """
    if sys.platform != "win32":
        return "", ""
    ps = (
        f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' "
        "-ErrorAction SilentlyContinue; "
        "if ($p) { $p.ExecutablePath; '---'; $p.CommandLine }"
    )
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=5.0, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (subprocess.TimeoutExpired, OSError):
        return "", ""
    parts = out.split("---", 1)
    exe = parts[0].strip() if parts else ""
    cmd = parts[1].strip() if len(parts) > 1 else ""
    return exe, cmd


def sweep_port(port: int, expected_image_substrings: tuple[str, ...],
               host: str = "127.0.0.1") -> None:
    """Kill processes holding `host:port` whose exe OR command line
    contains any `expected_image_substrings` (case-insensitive). Foreign
    listeners are logged, never killed.

    Call before spawning a sidecar whose previous instance may have been
    orphaned (Job binding failed, parent SIGKILLed, etc.).
    """
    if not _port_in_use(host, port):
        return
    wanted = tuple(s.lower() for s in expected_image_substrings if s)
    for pid in _pids_listening_on(port):
        exe, cmd = _process_image(pid)
        exe_lc, cmd_lc = exe.lower(), cmd.lower()
        if any((w in exe_lc) or (w in cmd_lc) for w in wanted):
            log.warning("port %d held by orphan PID %d (%s) — killing",
                        port, pid, exe or cmd or "unknown")
            kill_process_tree(pid, force=True)
        else:
            log.error("port %d in use by foreign PID %d (exe=%s cmd=%s) — "
                      "not killing; sidecar spawn will fail",
                      port, pid, exe or "?", cmd or "?")
    for _ in range(20):
        if not _port_in_use(host, port):
            return
        time.sleep(0.1)


# ── atexit fallback ──────────────────────────────────────────────────

def _atexit_kill_tracked() -> None:
    """Clean-exit fallback when the Job Object path wasn't available.
    Hard-kill paths (SIGKILL, OS shutdown) are covered by the Job itself."""
    for proc in list(_TRACKED_PROCS):
        try:
            if hasattr(proc, "poll") and proc.poll() is None:
                proc.kill()
                if hasattr(proc, "wait"):
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        pass
            elif hasattr(proc, "returncode") and proc.returncode is None:
                try:
                    proc.kill()
                except Exception:
                    pass
        except Exception:
            pass


atexit.register(_atexit_kill_tracked)


# ═══════════════════════════════════════════════════════════════════════
# Section 2 — llama-server supervisor
# ═══════════════════════════════════════════════════════════════════════

_LLAMA_LOG = logging.getLogger("telecode.llamacpp")


class LlamaSupervisor:
    """One active llama-server process at a time.

    Public async surface:
        await sup.ensure_model("qwen-35b")   # swap if active model differs
        await sup.stop()                     # graceful shutdown
        sup.alive() / active_model() / last_used() / loaded_at()

    Thread-safety: everything async; caller must await from the loop.
    """

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._active_model: str = ""
        self._lock = asyncio.Lock()
        self._log_fp = None  # file object for llama.log
        self._swap_token = 0  # increments on every swap; cancels readers
        # ── Lazy load + idle unload bookkeeping ─────────────────────
        self._last_used: float = 0.0
        self._inflight: int = 0
        self._inflight_lock = asyncio.Lock()
        self._idle_task: Optional[asyncio.Task] = None
        self._loaded_at: float = 0.0

    # ── Lifecycle ────────────────────────────────────────────────────

    async def ensure_model(self, model_name: str) -> str:
        """Make `model_name` (or a resolved alias) the active model.

        Lazy load: spawns llama-server on demand if nothing is running.
        Records last-used timestamp so the idle watcher knows when the
        model went cold.

        Raises RuntimeError on spawn failure or readiness timeout.
        """
        resolved = cfg.resolve_model(model_name)
        if not resolved:
            raise RuntimeError(f"No llama.cpp model registered for '{model_name}'")

        self._last_used = time.time()

        async with self._lock:
            if self._proc and self._proc.poll() is None and self._active_model == resolved:
                self._ensure_idle_watcher()
                return resolved
            await self._stop_locked()
            await self._spawn_locked(resolved)
            self._loaded_at = time.time()
            self._ensure_idle_watcher()
            try:
                llama_state.save(resolved)
            except Exception:
                pass
            return resolved

    async def start_default(self) -> str:
        """Spawn the default model on startup."""
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

    # ── Activity tracking (for the idle watcher) ─────────────────────

    async def begin_request(self) -> None:
        """Caller acquires this around an upstream call so the idle
        watcher won't unload mid-stream. Pair with `end_request`."""
        async with self._inflight_lock:
            self._inflight += 1
            self._last_used = time.time()

    async def end_request(self) -> None:
        async with self._inflight_lock:
            self._inflight = max(0, self._inflight - 1)
            self._last_used = time.time()

    def inflight_count(self) -> int:
        return self._inflight

    def last_used(self) -> float:
        return self._last_used

    def loaded_at(self) -> float:
        return self._loaded_at

    # ── Idle unload watcher ──────────────────────────────────────────

    def _ensure_idle_watcher(self) -> None:
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
        idle_limit = cfg.idle_unload_sec()
        while True:
            await asyncio.sleep(min(30.0, max(5.0, idle_limit / 6)))
            if not self.alive():
                return
            if self._inflight > 0:
                continue
            if self._last_used == 0:
                continue
            idle_for = time.time() - self._last_used
            if idle_for >= idle_limit:
                _LLAMA_LOG.info("llama.cpp: idle for %.0fs ≥ %.0fs limit — unloading",
                                idle_for, idle_limit)
                try:
                    await self.stop()
                except Exception as exc:
                    _LLAMA_LOG.warning("idle unload failed: %s", exc)
                return  # next request will re-spawn

    # ── Internal spawn / readiness / stop ────────────────────────────

    async def _spawn_locked(self, model_name: str) -> None:
        argv = build_argv(model_name)
        binary = argv[0]
        resolved_binary = shutil.which(binary) or binary
        argv[0] = resolved_binary

        log_path = cfg.log_file()
        self._log_fp = open(log_path, "ab", buffering=0)
        self._log_fp.write(
            f"\n\n===== telecode: spawning llama-server for '{model_name}' =====\n"
            .encode()
        )
        self._log_fp.write(f"argv: {describe(model_name)}\n".encode())
        self._log_fp.flush()

        _LLAMA_LOG.info("llama.cpp: spawning '%s' on %s:%d",
                        model_name, cfg.host(), cfg.port())

        # If a previous telecode crashed before its Job Object could tear
        # down llama-server (or the Job binding failed), an orphan may still
        # hold our port — llama-server would then die on bind. Sweep it.
        try:
            sweep_port(cfg.port(), ("llama-server", "llama_server", "llama.cpp"))
        except Exception as exc:
            _LLAMA_LOG.debug("port sweep skipped: %s", exc)

        creation = 0
        if sys.platform == "win32":
            creation = subprocess.CREATE_NO_WINDOW

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

        # Bind to the shared kill-on-close Job so the OS kills llama-server
        # if THIS Python process dies unexpectedly. No-op on non-Windows.
        if bind_to_lifetime_job(self._proc.pid, proc=self._proc):
            _LLAMA_LOG.info("llama.cpp: pid %d bound to lifetime job", self._proc.pid)

        self._active_model = model_name

        try:
            await self._wait_ready()
        except Exception:
            await self._stop_locked()
            raise

        _LLAMA_LOG.info("llama.cpp: '%s' ready (pid %d)", model_name, self._proc.pid)

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
                            if status == "ok":
                                # Guard against an orphan answering on our
                                # port: re-poll after a short stabilization
                                # window; if our child died, the probe was
                                # hitting someone else.
                                await asyncio.sleep(1.0)
                                if self._proc is None or self._proc.poll() is not None:
                                    raise RuntimeError(
                                        f"llama-server died right after /health=ok "
                                        f"(code {self._proc.returncode if self._proc else '?'}); "
                                        f"another process on port {cfg.port()}? "
                                        f"see {cfg.log_file()}"
                                    )
                                return
                        elif resp.status == 503:
                            pass  # still loading
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

        kill_process_tree(proc.pid, force=False)
        for _ in range(40):  # 40 * 0.1s = 4s
            if proc.poll() is not None:
                break
            await asyncio.sleep(0.1)

        if proc.poll() is None:
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


# ── Module-level singleton ───────────────────────────────────────────

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
