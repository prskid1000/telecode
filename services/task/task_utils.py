"""Helpers for task handlers to report progress, check cancellation, and log events."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from pathlib import Path

from services.session import session_store
from services.task.task_manager import (
    TaskStatus,
    _local,
    get_task_queue,
)

logger = logging.getLogger("telecode.services.task.utils")

class StreamDrain:
    """Read a subprocess pipe to EOF on a daemon thread.

    The CLI handlers only iterate stdout; a stderr=PIPE that nobody reads
    fills the OS pipe buffer (~4 KB on Windows) and the child blocks on its
    next stderr write — forever, since we are blocked reading its stdout.
    Codex hits this on an expired ChatGPT login (it logs a token-refresh
    error per request). `text()` joins what was read, after the child exits.
    """

    def __init__(self, stream: Any, limit: int = 256 * 1024) -> None:
        import threading
        self._chunks: list = []
        self._size = 0
        self._limit = limit
        self._thread = threading.Thread(target=self._run, args=(stream,), daemon=True)
        self._thread.start()

    def _run(self, stream: Any) -> None:
        try:
            for line in stream:
                if self._size < self._limit:
                    self._chunks.append(line)
                    self._size += len(line)
        except Exception:
            pass

    def text(self, timeout: float = 5.0) -> str:
        self._thread.join(timeout)
        return "".join(self._chunks)


def current_task_id() -> Optional[str]:
    return getattr(_local, "task_id", None)

def current_session_id() -> Optional[str]:
    tid = current_task_id()
    if not tid: return None
    task = get_task_queue().get_task(tid)
    return task.session_id if task else None

def current_session_namespace() -> Optional[str]:
    tid = current_task_id()
    if not tid: return None
    task = get_task_queue().get_task(tid)
    return task.session_namespace if task else None

def get_task_id() -> Optional[str]:
    return current_task_id()

def get_session_id() -> Optional[str]:
    return current_session_id()

def get_session_namespace() -> Optional[str]:
    return current_session_namespace()

def get_session_folder() -> Optional[Path]:
    sid = current_session_id()
    if not sid: return None
    from services.session import session_store
    return session_store._session_dir(sid, namespace=current_session_namespace())

def is_cancelled() -> bool:
    """True once the current task stopped being active — cancelled, or failed
    by the queue's timeout watchdog — so the handler should stop reading."""
    task_id = current_task_id()
    if not task_id: return False
    task = get_task_queue().get_task(task_id)
    return bool(task and task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING))

def update_progress(progress: float, message: Optional[str] = None) -> bool:
    task_id = current_task_id()
    if not task_id: return False
    queue = get_task_queue()
    task = queue.get_task(task_id)
    if not task or task.status == TaskStatus.CANCELLED:
        return False
    with queue.lock:
        task.progress = progress
        if message:
            task.metadata["progress_message"] = message
    if message:
        logger.info(f"Task {task_id}: {progress * 100:.1f}% - {message}")
    return True

def append_event(event: Dict[str, Any]) -> None:
    task_id = current_task_id()
    if not task_id: return
    queue = get_task_queue()
    task = queue.get_task(task_id)
    if not task: return
    with queue.lock:
        events = task.metadata.setdefault("events", [])
        events.append({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            **event
        })


# ── CLI process tracking (B10) ─────────────────────────────────────────────

def track_process(proc: Any) -> Optional[int]:
    """Register a spawned CLI with the current task so cancel/timeout can kill
    its whole tree, and bind it to the process-wide kill-on-close Job so it
    cannot outlive telecode. With ``shell=True`` ``proc.pid`` is the shell
    (cmd.exe on Windows); ``process.kill_process_tree`` walks down to the CLI.
    Returns the pid, for :func:`untrack_process`."""
    pid = getattr(proc, "pid", None)
    if not pid:
        return None
    try:
        import process as tc_process
        tc_process.bind_to_lifetime_job(pid)
    except Exception:
        pass
    get_task_queue().register_pid(current_task_id(), pid)
    return pid


def kill_proc_tree(proc: Any) -> None:
    """Kill the shell AND the CLI under it — with shell=True a plain
    terminate()/kill() only reaches cmd.exe and the CLI keeps running."""
    try:
        import process as tc_process
        tc_process.kill_process_tree(proc.pid, force=True)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def untrack_process(pid: Optional[int]) -> None:
    if pid:
        get_task_queue().unregister_pid(current_task_id(), pid)


# ── Start-event prompt digest (B6) ─────────────────────────────────────────

PROMPT_HEAD_CHARS = 2048


def prompt_digest(prompt: str) -> Dict[str, Any]:
    """What the `start` event keeps of a prompt: the first 2 KB (under the
    legacy `prompt` key), its length and sha256. Design prompts run to 56 KB
    and every event is held in memory for the task's lifetime."""
    import hashlib
    text = prompt or ""
    return {
        "prompt": text[:PROMPT_HEAD_CHARS],
        "prompt_len": len(text),
        "prompt_sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest(),
        "prompt_truncated": len(text) > PROMPT_HEAD_CHARS,
    }


# ── Resume ids scoped per (workspace, agent, engine) (B7) ──────────────────

# Pre-B7 flat keys in session.data. Still read for runs without an agent (so
# existing Task-mode sessions keep resuming) and still written for them; the
# TeleDesign path calls the _run_*_subprocess functions directly and keeps
# using these keys itself.
LEGACY_RESUME_KEYS = {
    ("claude_code", False): "last_claude_session_id",
    ("claude_code", True): "last_claude_session_id",
    ("codex", False): "last_codex_session_id",
    ("codex", True): "last_codex_session_id",
    ("antigravity", False): "last_antigravity_conversation_id",
    ("antigravity", True): "last_antigravity_local_conversation_id",
}


def resume_scope_key(agent_id: Optional[str], engine: str, is_local: bool) -> str:
    """Key into session.data["resume"]: ``<agent_id|_>:<engine>[:local]``."""
    return f"{agent_id or '_'}:{engine}" + (":local" if is_local else "")


def read_resume_id(data: Optional[Dict[str, Any]], agent_id: Optional[str],
                   engine: str, is_local: bool) -> Optional[str]:
    data = data or {}
    scoped = (data.get("resume") or {}).get(resume_scope_key(agent_id, engine, is_local))
    if scoped:
        return scoped
    if not agent_id:
        return data.get(LEGACY_RESUME_KEYS.get((engine, bool(is_local)), "")) or None
    return None


def make_resume_store(sid: str, ns: Optional[str], agent_id: Optional[str],
                      engine: str, is_local: bool):
    """Callable the _run_*_subprocess functions use to persist a new resume id."""
    key = resume_scope_key(agent_id, engine, is_local)
    legacy = None if agent_id else LEGACY_RESUME_KEYS.get((engine, bool(is_local)))

    def store(resume_id: str) -> None:
        patch: Dict[str, Any] = {"resume": {key: resume_id}}
        if legacy:
            patch[legacy] = resume_id
        session_store.patch_data(sid, patch, namespace=ns)

    return store
