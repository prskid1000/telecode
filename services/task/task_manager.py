"""In-process task queue for telecode. Ported from pythonmagic."""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("telecode.services.task")

_local = threading.local()

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class Task:
    task_id: str
    task_type: str
    session_id: Optional[str] = None
    session_namespace: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    progress: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    future: Optional[Future] = None

class TaskQueue:
    def __init__(self, max_workers: int = 5):
        self.tasks: Dict[str, Task] = {}
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="task")
        self.task_handlers: Dict[str, Callable] = {}
        self.task_metadata: Dict[str, Dict[str, Any]] = {}

    def register_handler(
        self,
        task_type: str,
        handler: Callable,
        description: Optional[str] = None,
        params_schema: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.task_handlers[task_type] = handler
        self.task_metadata[task_type] = {
            "description": description or f"Task type: {task_type}",
            "params_schema": params_schema or {},
        }

    def get_available_task_types(self) -> Dict[str, Any]:
        return self.task_metadata

    def submit_task(
        self,
        task_type: str,
        params: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        task_timeout_seconds: Optional[int] = None,
        session_id: Optional[str] = None,
        session_idle_timeout_seconds: Optional[int] = None,
        session_namespace: Optional[str] = None,
        absolute_ttl_seconds: Optional[int] = None,
    ) -> str:
        task_id = str(uuid.uuid4())
        from services.session import session_store
        
        sid = session_id or str(uuid.uuid4())
        session_store.ensure(sid, session_idle_timeout_seconds=session_idle_timeout_seconds, 
                             absolute_ttl_seconds=absolute_ttl_seconds, namespace=session_namespace)
        session_store.append_task_id(sid, task_id, namespace=session_namespace)

        task = Task(task_id=task_id, task_type=task_type, session_id=sid, 
                    session_namespace=session_namespace, metadata=metadata or {})
        with self.lock:
            self.tasks[task_id] = task

        handler = self.task_handlers.get(task_type)
        if not handler:
            task.status = TaskStatus.FAILED
            task.error = f"No handler for {task_type}"
            task.completed_at = datetime.now()
            return task_id

        future = self.executor.submit(self._execute_task, task_id, handler, params)
        with self.lock:
            task.future = future

        return task_id

    def _execute_task(self, task_id: str, handler: Callable, params: Dict[str, Any]) -> None:
        _local.task_id = task_id
        with self.lock:
            task = self.tasks.get(task_id)
            if not task or task.status == TaskStatus.CANCELLED:
                _local.task_id = None
                return
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now()

        try:
            result = handler(**params)
            with self.lock:
                cur = self.tasks.get(task_id)
                if not cur or cur.status == TaskStatus.CANCELLED: return
                cur.status = TaskStatus.COMPLETED
                cur.completed_at = datetime.now()
                cur.result = result
                cur.progress = 1.0
        except Exception as exc:
            with self.lock:
                cur = self.tasks.get(task_id)
                if not cur or cur.status == TaskStatus.CANCELLED: return
                cur.status = TaskStatus.FAILED
                cur.completed_at = datetime.now()
                cur.error = str(exc)
        finally:
            _local.task_id = None

    def get_task(self, task_id: str) -> Optional[Task]:
        with self.lock: return self.tasks.get(task_id)

    def list_tasks(self) -> List[Task]:
        with self.lock: return list(self.tasks.values())

_task_queue: Optional[TaskQueue] = None
def get_task_queue() -> TaskQueue:
    global _task_queue
    if _task_queue is None: _task_queue = TaskQueue()
    return _task_queue

def task_to_dict(task: Task) -> Dict[str, Any]:
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "session_id": task.session_id,
        "session_namespace": task.session_namespace,
        "status": task.status.value,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "progress": task.progress,
        "metadata": task.metadata,
        "result": task.result,
        "error": task.error,
    }
