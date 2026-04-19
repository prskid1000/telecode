"""Single-instance guard using a Win32 named mutex (Unix: fcntl flock).

Telecode already has implicit protection from port binding (proxy :1235,
llama-server :1234 — a second copy dies when ports collide) but those
collisions happen late in startup and leak noise into the logs. This
guard rejects the duplicate process at main() entry, before anything
heavy spins up.

Qt-based guards (QLocalServer) don't work here because telecode's Qt
app lives on a daemon thread and isn't available when main() is deciding
whether to proceed. A raw OS handle is the right tool.

Contract:
  - `acquire()` returns True on first call per user, False on duplicates.
  - The underlying handle is held by the module for the interpreter's
    lifetime; no explicit release needed. The OS drops it on exit.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sys
import tempfile

log = logging.getLogger("telecode.single_instance")

# Module-level hold: don't let the fd / handle get GC'd mid-run.
_held_handle = None


def _key() -> str:
    """Stable per-user, per-machine key."""
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    digest = hashlib.sha1(home.encode("utf-8")).hexdigest()[:10]
    return f"telecode-{digest}"


def acquire() -> bool:
    """True → we're the only instance. False → another process holds it."""
    global _held_handle
    if sys.platform == "win32":
        return _acquire_win32()
    return _acquire_posix()


def _acquire_win32() -> bool:
    global _held_handle
    try:
        import win32event  # type: ignore
        import win32api    # type: ignore
        import winerror    # type: ignore
    except ImportError:
        log.debug("pywin32 missing — single-instance guard disabled")
        return True  # fail open rather than break the user's launch

    # Global\ prefix would cover all sessions; we scope per-user instead.
    mutex_name = _key()
    handle = win32event.CreateMutex(None, False, mutex_name)
    last_error = win32api.GetLastError()
    if last_error == winerror.ERROR_ALREADY_EXISTS:
        # Another instance holds it. Close our reference cleanly.
        win32api.CloseHandle(handle)
        return False
    _held_handle = handle  # keep alive for process lifetime
    return True


def _acquire_posix() -> bool:
    global _held_handle
    try:
        import fcntl  # type: ignore
    except ImportError:
        return True
    lock_path = os.path.join(tempfile.gettempdir(), f"{_key()}.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    _held_handle = fd
    return True
