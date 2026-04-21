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
    task_id = current_task_id()
    if not task_id: return False
    task = get_task_queue().get_task(task_id)
    return bool(task and task.status == TaskStatus.CANCELLED)

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
