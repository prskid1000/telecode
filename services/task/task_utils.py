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
