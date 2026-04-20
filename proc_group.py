"""Windows Job Object: bind a child PID to this Python process's lifetime.

On Windows, child processes survive parent termination by default. This
module creates a single Job Object flagged `KILL_ON_JOB_CLOSE` at first
call and assigns every subsequent PID to it. When the Python interpreter
exits — clean shutdown, Ctrl+C, Task Manager End Process, pythonw crash,
OS logout — Windows releases the Job handle, the job closes, and every
assigned process is terminated by the OS.

No-op on non-Windows platforms (Unix processes don't need this; we can
add PR_SET_PDEATHSIG there later if we want symmetry).

Public surface:
    bind_to_lifetime_job(pid) -> bool      # True if binding succeeded

Module-level side effects:
    - atexit handler registered at import (best-effort kill fallback for
      platforms / pywin32 installs where the Job Object can't be created)

All internal globals are module-private.
"""
from __future__ import annotations

import atexit
import logging
import socket
import subprocess
import sys
import time

log = logging.getLogger("telecode.proc_group")


_JOB_HANDLE = None
_TRACKED_PIDS: set[int] = set()
# Popen / asyncio.subprocess.Process objects we've registered for fallback kill.
_TRACKED_PROCS: list = []


def _create_kill_on_close_job():
    """Create the process-wide kill-on-close Job Object (Windows only).

    Returns the job handle (int on Windows, None otherwise). Stashed in a
    module global so the Job lives as long as the Python interpreter.
    """
    global _JOB_HANDLE
    if sys.platform != "win32":
        return None
    if _JOB_HANDLE is not None:
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
        log.info("proc_group: created kill-on-close Job Object")
        return job
    except Exception as exc:
        log.warning("proc_group: could not create Job Object: %s", exc)
        return None


def bind_to_lifetime_job(pid: int, proc=None) -> bool:
    """Assign `pid` to the kill-on-close Job Object.

    Pass `proc` (the Popen-like object) too — we keep a weak reference and
    fall back to `proc.kill()` on atexit if the Job Object path failed.

    Returns True on success, False otherwise. Idempotent: binding the same
    PID twice is harmless (Windows allows re-assignment of members already
    in the job).
    """
    if proc is not None and proc not in _TRACKED_PROCS:
        _TRACKED_PROCS.append(proc)

    if sys.platform != "win32":
        return False
    if pid <= 0 or pid in _TRACKED_PIDS:
        return True  # already handled

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
        log.warning("proc_group: could not assign pid %d: %s", pid, exc)
        return False


def kill_process_tree(pid: int, force: bool = False, timeout: float = 5.0) -> bool:
    """Kill a process AND all its descendants.

    On Windows uses `taskkill /PID <pid> /T` (`/F` if force=True) which walks
    the process tree via the parent-PID map. Better than `proc.terminate()`
    when the child spawns workers that need to go down with it (uvicorn,
    node, python subprocesses, etc.). On Unix falls back to killing the
    group via `os.killpg`.

    Returns True if the tree appears terminated, False on timeout / error.
    The Job Object approach (bind_to_lifetime_job) is strictly better for
    crash safety — this helper is for GRACEFUL shutdown paths where we
    want descendants gone before we move on.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import subprocess
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
    # Unix
    try:
        import os
        import signal as _sig
        os.killpg(os.getpgid(pid), _sig.SIGKILL if force else _sig.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError, OSError) as exc:
        log.debug("killpg failed for pid %d: %s", pid, exc)
        return False


def _port_in_use(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((host, port)) == 0
    except OSError:
        return False


def _pids_listening_on(port: int) -> list[int]:
    """Windows-only. Returns PIDs listening on 127.0.0.1:<port>."""
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
    """Return (exe_path, command_line) for a PID. Both "" if unknown.

    Command line matters: a .exe under our sidecar tree is typically
    launched by pyenv's python.exe, so the owning process `Path` points
    at pyenv while the real identity is in argv.
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
    """Kill processes holding `host:port` whose executable path OR command
    line contains any of `expected_image_substrings` (case-insensitive).
    Safe: foreign listeners on the same port are logged, not killed.

    Use before spawning a sidecar whose previous instance may have been
    orphaned (Job Object binding failed, parent SIGKILLed, etc.) so the new
    child doesn't hit a bind-in-use error and die silently.
    """
    if not _port_in_use(host, port):
        return
    wanted = tuple(s.lower() for s in expected_image_substrings if s)
    for pid in _pids_listening_on(port):
        exe, cmd = _process_image(pid)
        exe_lc, cmd_lc = exe.lower(), cmd.lower()
        hit = any((w in exe_lc) or (w in cmd_lc) for w in wanted)
        if hit:
            log.warning("proc_group: port %d held by orphan PID %d (%s) — killing",
                        port, pid, exe or cmd or "unknown")
            kill_process_tree(pid, force=True)
        else:
            log.error("proc_group: port %d in use by foreign PID %d "
                      "(exe=%s cmd=%s) — not killing; sidecar spawn will fail",
                      port, pid, exe or "?", cmd or "?")
    # Give the OS a moment to release the socket.
    for _ in range(20):
        if not _port_in_use(host, port):
            return
        time.sleep(0.1)


def _atexit_kill_all() -> None:
    """Best-effort fallback: try to kill every tracked subprocess.

    Only fires on clean interpreter exit — not on SIGKILL / Task Manager
    End Process / OS shutdown. Those are covered by the Job Object itself.
    """
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
                # asyncio.subprocess.Process
                try:
                    proc.kill()
                except Exception:
                    pass
        except Exception:
            pass


atexit.register(_atexit_kill_all)
