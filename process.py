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
import threading
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


_JOB_CREATE_LOCK = threading.Lock()


def _create_kill_on_close_job():
    """Idempotent: create the Job Object on first call. No-op off Windows.

    Under a lock: two first-time callers racing (a parallel pipeline phase
    spawning its CLIs at once) used to create two Jobs; the loser's handle was
    garbage-collected — closing a KILL_ON_JOB_CLOSE Job — which killed the
    child just assigned to it (NtResumeProcess → STATUS_PROCESS_IS_TERMINATING)."""
    global _JOB_HANDLE
    if sys.platform != "win32" or _JOB_HANDLE is not None:
        return _JOB_HANDLE
    with _JOB_CREATE_LOCK:
        if _JOB_HANDLE is not None:
            return _JOB_HANDLE
        return _create_job_locked()


def _create_job_locked():
    global _JOB_HANDLE
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

# ES_CONTINUOUS | ES_SYSTEM_REQUIRED: the thread keeps the system awake until it
# clears the state or exits. Thread-bound, so set and cleared on the event loop
# thread, which is where every begin_request / end_request / stop runs.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_AWAKE_HELD = False


def _keep_awake(on: bool) -> None:
    """Hold (`on`) or release the Windows keep-awake request. No-op off Windows
    and when the state is already as asked."""
    global _AWAKE_HELD
    if sys.platform != "win32" or on == _AWAKE_HELD:
        return
    try:
        import ctypes
        flags = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED if on else _ES_CONTINUOUS
        if ctypes.windll.kernel32.SetThreadExecutionState(flags):
            _AWAKE_HELD = on
            _LLAMA_LOG.info("llama.cpp: %s", "keeping Windows awake while requests run"
                            if on else "released keep-awake request")
    except Exception as exc:
        _LLAMA_LOG.debug("SetThreadExecutionState failed: %s", exc)


_VRAM_CHECK_WARNED = False


def _free_vram_short(minimum: int) -> Optional[str]:
    """None when the first NVIDIA GPU has at least `minimum` MiB free (or cannot be
    read — logged once, check skipped); else a message saying how short it is.
    Blocking (runs nvidia-smi): call it off the event loop."""
    global _VRAM_CHECK_WARNED
    from llamacpp import vram
    free = vram.free_vram_mib()
    for _ in range(2):            # a model that was just stopped may still be releasing VRAM
        if free is None or free >= minimum:
            break
        time.sleep(1.5)
        free = vram.free_vram_mib()
    if free is None:
        if not _VRAM_CHECK_WARNED:
            _VRAM_CHECK_WARNED = True
            _LLAMA_LOG.warning("llama.cpp: min_free_vram_mib is set but nvidia-smi could not be read "
                               "— skipping the free-VRAM check")
        return None
    if free >= minimum:
        return None
    return f"{free} MiB free < min_free_vram_mib {minimum} MiB"


def _apply_win_process_tuning(pid: int) -> None:
    """Windows only: optional EcoQoS opt-out and process priority class for a freshly spawned llama-server.

    Both are off unless llamacpp.win_power_throttle_off / win_process_priority are set; failures are logged
    and ignored, the server runs either way."""
    if sys.platform != "win32":
        return
    throttle_off = cfg.win_power_throttle_off()
    prio = cfg.win_process_priority()
    if not throttle_off and not prio:
        return
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        handle = k32.OpenProcess(0x0200 | 0x0400, False, pid)  # PROCESS_SET_INFORMATION | PROCESS_QUERY_INFORMATION
        if not handle:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        try:
            if throttle_off:
                class _PowerThrottling(ctypes.Structure):
                    _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                                ("StateMask", wintypes.ULONG)]

                st = _PowerThrottling(1, 0x1, 0)  # version 1, control EXECUTION_SPEED, state 0 = never throttle
                k32.SetProcessInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
                ok = k32.SetProcessInformation(handle, 4, ctypes.byref(st), ctypes.sizeof(st))  # ProcessPowerThrottling
                _LLAMA_LOG.info("llama.cpp: power throttling off for pid %d: %s", pid, "ok" if ok else "failed")
            if prio:
                classes = {"idle": 0x40, "below_normal": 0x4000, "normal": 0x20, "above_normal": 0x8000, "high": 0x80}
                ok = k32.SetPriorityClass(handle, classes[prio])
                _LLAMA_LOG.info("llama.cpp: priority class %s for pid %d: %s", prio, pid, "ok" if ok else "failed")
        finally:
            k32.CloseHandle(handle)
    except Exception as exc:
        _LLAMA_LOG.warning("llama.cpp: process tuning skipped: %s", exc)


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
        self._watchdog_task: Optional[asyncio.Task] = None
        self._loaded_at: float = 0.0
        self._vram_task: Optional[asyncio.Task] = None
        self._spawn_logged = False  # the current spawn attempt wrote its marker to llama.log
        self._planned_mib: Optional[int] = None  # the automatic cache size of the current spawn attempt

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
                self._ensure_watchdog()
                self._ensure_vram_guard()
                return resolved
            await self._stop_locked()
            await self._spawn_with_retry(resolved)
            self._loaded_at = time.time()
            self._ensure_idle_watcher()
            self._ensure_watchdog()
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
            if cfg.prevent_sleep():
                _keep_awake(True)

    async def end_request(self) -> None:
        async with self._inflight_lock:
            self._inflight = max(0, self._inflight - 1)
            self._last_used = time.time()
            if self._inflight == 0:
                _keep_awake(False)

    def inflight_count(self) -> int:
        return self._inflight

    def last_used(self) -> float:
        return self._last_used

    def loaded_at(self) -> float:
        return self._loaded_at

    # ── Idle unload watcher ──────────────────────────────────────────

    def _ensure_vram_guard(self) -> None:
        if self._vram_task is not None and not self._vram_task.done():
            return
        if cfg.vram_ceiling_mib() <= 0:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._vram_task = loop.create_task(self._vram_guard_loop())

    async def _vram_guard_loop(self) -> None:
        """Kill llama-server when the GPU memory in use passes llamacpp.vram_ceiling_mib.

        Started at the spawn, so the model load is covered. It does not take the supervisor lock (a spawn holds it until the
        server is ready): it kills the process, the spawn then fails with 'server exited' and the next request starts it again."""
        from llamacpp import vram
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(0.5)
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return
            ceiling = cfg.vram_ceiling_mib()
            if ceiling <= 0:
                return
            used = await loop.run_in_executor(None, vram.used_vram_mib)
            if used is not None and used > ceiling:
                _LLAMA_LOG.error("llama.cpp: GPU memory in use %d MiB passed llamacpp.vram_ceiling_mib %d MiB, killing llama-server (pid %d)",
                                 used, ceiling, proc.pid)
                kill_process_tree(proc.pid, force=True)
                return

    def _ensure_watchdog(self) -> None:
        if self._watchdog_task is not None and not self._watchdog_task.done():
            return
        if cfg.watchdog_sec() <= 0:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._watchdog_task = loop.create_task(self._watchdog_loop())

    async def _watchdog_loop(self) -> None:
        """Stop a llama-server whose /health stops answering; the next request starts a fresh one."""
        url = f"{cfg.upstream_url()}/health"
        failing_since: Optional[float] = None
        watched_pid = 0
        async with aiohttp.ClientSession() as session:
            while True:
                await asyncio.sleep(5.0)
                if not self.alive():
                    return
                pid = self._proc.pid if self._proc else 0
                if pid != watched_pid:
                    # a new server (swap, restart): its start-up is not a failure of the old one
                    watched_pid = pid
                    failing_since = None
                limit = cfg.watchdog_sec()
                if limit <= 0:
                    return
                ok = False
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                        ok = resp.status in (200, 503)  # 503 = still loading a model
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    ok = False
                now = time.time()
                if ok:
                    failing_since = None
                    continue
                failing_since = failing_since or now
                if now - failing_since >= limit:
                    _LLAMA_LOG.error("llama.cpp: /health has not answered for %.0fs, restarting llama-server", now - failing_since)
                    try:
                        async with self._lock:
                            # a swap or spawn may have held the lock meanwhile: only stop the very server that stopped answering
                            if self._proc is not None and self._proc.pid == watched_pid and self._proc.poll() is None:
                                await self._stop_locked()
                    except Exception as exc:
                        _LLAMA_LOG.warning("watchdog stop failed: %s", exc)
                    return

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

    async def _spawn_with_retry(self, model_name: str) -> None:
        """Spawn; with an automatic MoE cache size, start again with a smaller cache when the server ran out of GPU memory."""
        from llamacpp import auto_cache
        scale = 1.0
        for attempt in range(4):
            self._spawn_logged = False
            self._planned_mib = None
            try:
                await self._spawn_locked(model_name, scale)
                return
            except RuntimeError as exc:
                # a retry only helps when this attempt really started a server with a planned cache that a smaller one would change;
                # every extra near-OOM load is a risk on a machine that crashes when the GPU memory runs full
                if (attempt >= 3 or not auto_cache.enabled(model_name) or not self._spawn_logged
                        or not self._planned_mib or not auto_cache.log_says_oom(cfg.log_file())):
                    raise
                nxt = await asyncio.get_running_loop().run_in_executor(None, auto_cache.plan, model_name, scale*0.8)
                if nxt is None or int(nxt.get("moe_cache_mib") or 0) >= self._planned_mib:
                    raise
                scale *= 0.8
                _LLAMA_LOG.warning("llama.cpp: '%s' ran out of GPU memory (%s), trying again with a cache %d%% of the planned size",
                                   model_name, exc, int(scale*100))
                await self._stop_locked()

    async def _spawn_locked(self, model_name: str, cache_scale: float = 1.0) -> None:
        # Refuse to start into too little VRAM: the driver would spill the model
        # into shared system memory and run an order of magnitude slower.
        min_free = cfg.min_free_vram_mib()
        if min_free > 0:
            short = await asyncio.get_running_loop().run_in_executor(
                None, _free_vram_short, min_free)
            if short:
                _LLAMA_LOG.error("llama.cpp: not spawning '%s': %s", model_name, short)
                raise RuntimeError(
                    f"llama-server not started for '{model_name}': {short}. Free GPU memory "
                    f"or lower llamacpp.min_free_vram_mib."
                )

        overrides = None
        from llamacpp import auto_cache
        if auto_cache.enabled(model_name):
            overrides = await asyncio.get_running_loop().run_in_executor(None, auto_cache.plan, model_name, cache_scale)
        self._planned_mib = int(overrides["moe_cache_mib"]) if overrides and overrides.get("moe_cache_mib") else None
        argv = build_argv(model_name, overrides)
        binary = argv[0]
        resolved_binary = shutil.which(binary) or binary
        argv[0] = resolved_binary

        log_path = cfg.log_file()
        self._log_fp = open(log_path, "ab", buffering=0)
        self._log_fp.write(
            f"\n\n===== telecode: spawning llama-server for '{model_name}' =====\n"
            .encode()
        )
        self._log_fp.write(f"argv: {describe(model_name, overrides)}\n".encode())
        # Extra environment (llamacpp.env, then the model's own env on top), rebuilt
        # for the model being started. Names only in the log; values may be paths.
        child_env = cfg.spawn_env(model_name)
        if child_env is not None:
            self._log_fp.write(
                f"env: {', '.join(sorted(cfg.extra_env(model_name)))}\n".encode())
        self._log_fp.flush()
        self._spawn_logged = True

        _LLAMA_LOG.info("llama.cpp: spawning '%s' on %s:%d",
                        model_name, cfg.host(), cfg.port())
        _LLAMA_LOG.info("llama.cpp: argv: %s", describe(model_name, overrides))

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
                env=child_env,
            )
        except FileNotFoundError as exc:
            self._log_fp.close()
            self._log_fp = None
            raise RuntimeError(
                f"llama.cpp binary not found: {binary}. Set llamacpp.binary "
                f"in settings.json to the absolute path."
            ) from exc

        _apply_win_process_tuning(self._proc.pid)
        self._ensure_vram_guard()

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

        # A fresh process may be a different binary — a patched build installed
        # over the release, or a release update that replaced one. Capability
        # answers are probed from the running server, so they must not survive
        # a respawn; a stale "unsupported" would silently keep the proxy on the
        # slow tool path until telecode restarted.
        try:
            from proxy import llama_caps
            llama_caps.invalidate()
        except Exception as exc:
            _LLAMA_LOG.debug("llama_caps.invalidate failed: %s", exc)

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
        _keep_awake(False)

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
