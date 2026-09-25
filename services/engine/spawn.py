"""Spawning a CLI so it can never escape telecode, and stopping its whole tree.

**No shell.** The binary is resolved with ``shutil.which`` and spawned
directly, so ``proc.pid`` *is* the CLI (not a ``cmd.exe`` wrapper). A Windows
``.cmd``/``.bat`` shim is handled two ways: an npm shim is unwrapped to
``node <script.js>``; any other shim runs under ``cmd.exe /d /s /c`` after its
arguments are checked for cmd metacharacters (the prompt is never on argv — it
goes on stdin — so only flags and paths are).

**Bound before it runs (Windows).** The child is created ``CREATE_SUSPENDED``,
assigned to telecode's kill-on-close Job (``process.bind_to_lifetime_job``) and
to a per-run Job nested inside it, then resumed. Nothing the CLI spawns can
start outside the Jobs, so a hard kill of telecode takes the whole tree down,
and :meth:`Spawned.kill_tree` (``TerminateJobObject``) reaches grandchildren
even after their parent exited — ``taskkill /T`` cannot, it walks live
parent links. The per-run Job has no kill-on-close flag: processes the agent
deliberately left running survive the run, exactly as before, until telecode
exits.

**Graceful first.** ``CREATE_NEW_PROCESS_GROUP`` makes the CLI a group
leader; :meth:`Spawned.graceful_stop` delivers CTRL_BREAK to that group from a
tiny helper process attached to the CLI's own (hidden) console — telecode's
console, if any, is never signalled. Then, after the grace period, the tree
kill.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional, Union

from services.engine.types import EngineError

logger = logging.getLogger("telecode.services.engine.spawn")

IS_WIN = sys.platform == "win32"
CREATE_SUSPENDED = 0x00000004
_CMD_META = re.compile(r'[&|<>^%!"\r\n]')
# npm/cmd-shim: "%_prog%"  "%dp0%\node_modules\...\cli.js" %*
_NPM_SHIM_RE = re.compile(r'"%(?:~)?dp0%?\\?([^"%]+?\.(?:js|cjs|mjs))"', re.I)


def resolve_argv(argv: List[str]) -> Union[List[str], str]:
    """Resolve ``argv[0]`` on PATH and unwrap Windows shims. Unresolvable names
    are passed through (the spawn then raises a clear EngineError). A batch shim
    that is not an npm shim comes back as a ``cmd.exe /d /s /c`` command-line string."""
    name = argv[0]
    found = shutil.which(name)
    if not found:
        return list(argv)
    path = Path(found)
    if IS_WIN and path.suffix.lower() in (".cmd", ".bat"):
        return _unwrap_shim(path, list(argv[1:]))
    return [str(path), *argv[1:]]


def _unwrap_shim(shim: Path, args: List[str]) -> Union[List[str], str]:
    try:
        text = shim.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    m = _NPM_SHIM_RE.search(text)
    if m:
        script = (shim.parent / m.group(1).replace("\\", os.sep)).resolve()
        node = shim.parent / "node.exe"
        node_bin = str(node) if node.exists() else shutil.which("node")
        if script.exists() and node_bin:
            return [node_bin, str(script), *args]
    bad = [a for a in args if _CMD_META.search(a)]
    if bad:
        raise EngineError(f"{shim.name} is a batch shim and an argument contains cmd metacharacters "
                          f"({bad[0][:60]!r}); install the native binary or pass simpler arguments")
    # A command-line *string*: Popen would re-quote a list element holding
    # quotes with backslashes, which cmd does not understand. With /s, cmd strips
    # the outer quote pair and runs the line inside verbatim.
    line = subprocess.list2cmdline([str(shim), *args])
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    return f'"{comspec}" /d /s /c "{line}"'  # type: ignore[return-value]


class _RunJob:
    """A per-run Job Object nested in the lifetime Job (Windows, pywin32)."""

    def __init__(self) -> None:
        self.handle = None
        if not IS_WIN:
            return
        try:
            import win32job
            self.handle = win32job.CreateJobObject(None, "")
        except Exception as exc:  # pywin32 missing
            logger.debug("per-run Job unavailable: %s", exc)

    def assign(self, proc_handle: int) -> bool:
        if self.handle is None:
            return False
        try:
            import win32job
            win32job.AssignProcessToJobObject(self.handle, proc_handle)
            return True
        except Exception as exc:
            logger.debug("assign to per-run Job failed: %s", exc)
            return False

    def terminate(self) -> bool:
        if self.handle is None:
            return False
        try:
            import win32job
            win32job.TerminateJobObject(self.handle, 1)
            return True
        except Exception as exc:
            logger.debug("TerminateJobObject failed: %s", exc)
            return False

    def close(self) -> None:
        if self.handle is None:
            return
        try:
            import win32api
            win32api.CloseHandle(self.handle)
        except Exception:
            pass
        self.handle = None


def _resume_process(proc_handle: int) -> bool:
    import ctypes
    try:
        status = ctypes.windll.ntdll.NtResumeProcess(ctypes.c_void_p(proc_handle))
        return status == 0
    except Exception as exc:
        logger.warning("NtResumeProcess failed: %s", exc)
        return False


class Spawned:
    """A running CLI plus the handles needed to stop its tree."""

    def __init__(self, proc: subprocess.Popen, job: Optional[_RunJob]):
        self.proc = proc
        self.pid = int(getattr(proc, "pid", 0) or 0)
        self._job = job
        self._killed = threading.Event()

    def alive(self) -> bool:
        try:
            return self.proc.poll() is None
        except Exception:
            return False

    def graceful_stop(self) -> bool:
        """Ask the CLI to exit (CTRL_BREAK to its process group / SIGTERM)."""
        if not self.pid or not self.alive():
            return False
        if not IS_WIN:
            try:
                import signal
                os.killpg(os.getpgid(self.pid), signal.SIGTERM)
                return True
            except Exception:
                return False
        # From a helper attached to the child's console: never our own console.
        code = ("import ctypes,sys;k=ctypes.windll.kernel32;p=int(sys.argv[1]);k.FreeConsole();"
                "sys.exit(0 if (k.AttachConsole(p) and k.SetConsoleCtrlHandler(None,1) "
                "and k.GenerateConsoleCtrlEvent(1,p)) else 1)")
        try:
            r = subprocess.run([sys.executable, "-c", code, str(self.pid)], timeout=5,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return r.returncode == 0
        except Exception:
            return False

    def kill_tree(self) -> None:
        """Kill the CLI and every descendant, even orphaned ones."""
        self._killed.set()
        if self._job is not None:
            self._job.terminate()
        if self.pid:
            try:
                import process as tc_process
                if self.alive():
                    tc_process.kill_process_tree(self.pid, force=True)
            except Exception:
                pass
        try:
            self.proc.kill()
        except Exception:
            pass

    def stop(self, grace_sec: float) -> None:
        """Graceful stop, wait up to ``grace_sec``, then the tree kill (always —
        grandchildren may ignore the break)."""
        if self._killed.is_set():
            return
        if grace_sec > 0 and self.graceful_stop():
            try:
                self.proc.wait(timeout=grace_sec)
            except Exception:
                pass
        self.kill_tree()

    def close(self) -> None:
        if self._job is not None:
            self._job.close()


def spawn(argv: List[str], *, cwd: Path, env: Optional[Dict[str, str]] = None) -> Spawned:
    """Spawn ``argv`` with stdin/stdout/stderr pipes (text, utf-8), bound to the
    Jobs before its first instruction runs (Windows)."""
    cmd = resolve_argv(argv)
    flags = 0
    kwargs: Dict[str, object] = {}
    if IS_WIN:
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED)
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=flags, **kwargs)
    except FileNotFoundError as exc:
        raise EngineError(f"{argv[0]} not found on PATH ({exc})") from exc
    except OSError as exc:
        raise EngineError(f"could not start {argv[0]}: {exc}") from exc

    job: Optional[_RunJob] = None
    pid = int(getattr(proc, "pid", 0) or 0)
    handle = getattr(proc, "_handle", None)
    if IS_WIN and handle is not None:
        try:
            import process as tc_process
            tc_process.bind_to_lifetime_job(pid, proc)
        except Exception as exc:
            logger.warning("could not bind pid %d to the lifetime Job: %s", pid, exc)
        job = _RunJob()
        job.assign(int(handle))
        if not _resume_process(int(handle)):
            try:
                proc.kill()
            except Exception:
                pass
            job.close()
            raise EngineError(f"could not resume {argv[0]} after binding it to the Job")
    elif pid:
        try:
            import process as tc_process
            tc_process.bind_to_lifetime_job(pid, proc)
        except Exception:
            pass
    return Spawned(proc, job)
