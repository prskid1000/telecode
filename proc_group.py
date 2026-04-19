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
import sys

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
