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
    queue.persist(task_id, throttle=True)
    return True

def append_event(event: Dict[str, Any]) -> None:
    """Append an event to the current task: in memory (``metadata.events``,
    which TeleDesign's driver reads by index), in ``data/telecode.db`` (capped
    per task) and on the live bus (SSE), with a 1-based ``seq``."""
    task_id = current_task_id()
    if not task_id: return
    queue = get_task_queue()
    task = queue.get_task(task_id)
    if not task: return
    evt = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), **event}
    with queue.lock:
        events = task.metadata.setdefault("events", [])
        events.append(evt)
        seq = len(events)
    queue.record_event(task, seq, evt)


def publish_live(event: Dict[str, Any]) -> None:
    """Stream an event of the current task to SSE subscribers without keeping
    it (Claude's per-token text deltas — the raw log already has them)."""
    task_id = current_task_id()
    if not task_id: return
    task = get_task_queue().get_task(task_id)
    if not task: return
    from services import bus
    evt = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), **event}
    bus.publish("task", task_id, "delta", evt)
    run_id = (task.metadata or {}).get("run_id")
    if run_id:
        bus.publish("run", run_id, "delta", {**evt, "task_id": task_id})


# ── CLI process tracking (B10) ─────────────────────────────────────────────

def track_process(proc: Any) -> Optional[int]:
    """For a handler that spawns its own process (the engine CLIs go through
    services.engine, which registers itself): record it with the current task
    so cancel/timeout tree-kill it, and bind it to the kill-on-close Job.
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


def untrack_process(pid: Optional[int]) -> None:
    if pid:
        get_task_queue().unregister_pid(current_task_id(), pid)


# ── Start-event prompt digest (B6) — lives with the runner ────────────────

from services.engine.runner import PROMPT_HEAD_CHARS, prompt_digest  # noqa: E402,F401


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
