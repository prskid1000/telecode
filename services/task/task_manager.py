"""In-process task queue for telecode. Ported from pythonmagic.

Two worker pools (B12): ``interactive`` (Task-mode submits, TeleDesign turns)
and ``background`` (routines, heartbeat fires, pipeline-run steps), sized by
``config.tasks_interactive_workers()`` / ``tasks_background_workers()``, so
background work can never occupy every worker an interactive request needs.

Cancellation and timeouts go through one path, :meth:`TaskQueue.cancel` /
:meth:`TaskQueue._on_timeout`: flip the status (never over a finished task),
stamp ``completed_at``, then kill every CLI process tree the handler
registered with :meth:`TaskQueue.register_pid` — through the Engine Runner's
non-blocking stop (graceful CTRL_BREAK, then Job/tree kill) when it registered
one, else a direct tree kill.

Finished tasks are evicted after ``FINISHED_RETENTION_SECONDS`` (the newest
``FINISHED_KEEP_MIN`` finished tasks are always kept) so the queue does not
grow for the life of the process.

Every state change is written through to ``data/telecode.db``
(``services.db.task_repo``) and published on the live bus (``services.bus``),
so ``GET /api/tasks[/{id}]`` survives a restart (:meth:`get_task_record`,
:meth:`list_task_records`) and the SSE routes can replay. A DB failure is
logged, never raised into a task.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("telecode.services.task")

_local = threading.local()

POOL_INTERACTIVE = "interactive"
POOL_BACKGROUND = "background"

# Finished tasks older than this are evicted from memory, except that the
# newest FINISHED_KEEP_MIN finished tasks are always kept (UI history).
FINISHED_RETENTION_SECONDS = 3600
FINISHED_KEEP_MIN = 500
_EVICT_EVERY_SECONDS = 60


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
ACTIVE_STATUSES = (TaskStatus.PENDING, TaskStatus.RUNNING)


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
    pool: str = POOL_INTERACTIVE
    timeout_seconds: Optional[int] = None
    # PIDs of CLI process trees the handler spawned.
    pids: List[int] = field(default_factory=list)
    # pid -> non-blocking stop(reason) from the Engine Runner (graceful, then
    # tree-kill). A pid without one is tree-killed directly.
    killers: Dict[int, Callable[[str], None]] = field(default_factory=dict)
    last_persist: float = 0.0


def _classify_pool(metadata: Dict[str, Any]) -> str:
    """Default pool from submit metadata: scheduled/pipeline work is background."""
    md = metadata or {}
    if md.get("routine_id") or md.get("run_id") or md.get("source") == "heartbeat":
        return POOL_BACKGROUND
    return POOL_INTERACTIVE


def _kill_pid_tree(pid: int) -> None:
    try:
        import process as tc_process
        tc_process.kill_process_tree(pid, force=True)
    except Exception as exc:  # noqa: BLE001 - best effort
        logger.warning(f"could not kill process tree {pid}: {exc}")


class TaskQueue:
    def __init__(self, max_workers: Optional[int] = None, background_workers: Optional[int] = None):
        self.tasks: Dict[str, Task] = {}
        self.lock = threading.RLock()
        if max_workers is None or background_workers is None:
            import config
            if max_workers is None:
                max_workers = config.tasks_interactive_workers()
            if background_workers is None:
                background_workers = config.tasks_background_workers()
        self.pools: Dict[str, ThreadPoolExecutor] = {
            POOL_INTERACTIVE: ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="task"),
            POOL_BACKGROUND: ThreadPoolExecutor(max_workers=background_workers, thread_name_prefix="task-bg"),
        }
        # Kept for callers that referenced the single pool.
        self.executor = self.pools[POOL_INTERACTIVE]
        self.task_handlers: Dict[str, Callable] = {}
        self.task_metadata: Dict[str, Dict[str, Any]] = {}
        self._timers: Dict[str, threading.Timer] = {}
        self._last_evict = 0.0

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
        pool: Optional[str] = None,
    ) -> str:
        self.evict_finished()
        task_id = str(uuid.uuid4())
        from services.session import session_store

        sid = session_id or str(uuid.uuid4())
        session_store.ensure(sid, session_idle_timeout_seconds=session_idle_timeout_seconds,
                             absolute_ttl_seconds=absolute_ttl_seconds, namespace=session_namespace)
        session_store.append_task_id(sid, task_id, namespace=session_namespace)

        md = metadata or {}
        pool = pool if pool in self.pools else _classify_pool(md)
        timeout = None
        if task_timeout_seconds:
            try:
                timeout = int(task_timeout_seconds)
            except (TypeError, ValueError):
                timeout = None
            if timeout is not None and timeout <= 0:
                timeout = None
        task = Task(task_id=task_id, task_type=task_type, session_id=sid,
                    session_namespace=session_namespace, metadata=md,
                    pool=pool, timeout_seconds=timeout)
        with self.lock:
            self.tasks[task_id] = task

        handler = self.task_handlers.get(task_type)
        if not handler:
            task.status = TaskStatus.FAILED
            task.error = f"No handler for {task_type}"
            task.completed_at = datetime.now()
            self.persist(task_id)
            return task_id
        self.persist(task_id)

        future = self.pools[pool].submit(self._execute_task, task_id, handler, params)
        with self.lock:
            task.future = future

        return task_id

    def _execute_task(self, task_id: str, handler: Callable, params: Dict[str, Any]) -> None:
        _local.task_id = task_id
        with self.lock:
            task = self.tasks.get(task_id)
            if not task or task.status != TaskStatus.PENDING:
                _local.task_id = None
                return
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now()
            if task.timeout_seconds:
                timer = threading.Timer(task.timeout_seconds, self._on_timeout, args=(task_id,))
                timer.daemon = True
                self._timers[task_id] = timer
                timer.start()
        self.persist(task_id)

        try:
            result = handler(**params)
            with self.lock:
                cur = self.tasks.get(task_id)
                # Cancelled / timed out while running: that outcome stands.
                if not cur or cur.status != TaskStatus.RUNNING:
                    return
                cur.status = TaskStatus.COMPLETED
                cur.completed_at = datetime.now()
                cur.result = result
                cur.progress = 1.0
            self.persist(task_id)
        except Exception as exc:
            with self.lock:
                cur = self.tasks.get(task_id)
                if not cur or cur.status != TaskStatus.RUNNING:
                    return
                cur.status = TaskStatus.FAILED
                cur.completed_at = datetime.now()
                cur.error = str(exc)
            self.persist(task_id)
        finally:
            _local.task_id = None
            with self.lock:
                timer = self._timers.pop(task_id, None)
            if timer:
                timer.cancel()

    # ── Process registry / cancel / timeout ─────────────────────────────

    def register_pid(self, task_id: Optional[str], pid: int,
                     killer: Optional[Callable[[str], None]] = None) -> None:
        """Record a CLI process tree for this task; stop it at once if the
        task was already cancelled/timed out before the spawn finished.
        ``killer(reason)`` (the Engine Runner's graceful stop) is used instead
        of a direct tree-kill when given; it must not block."""
        if not task_id or not pid:
            return
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            task.pids.append(int(pid))
            if killer is not None:
                task.killers[int(pid)] = killer
            late = task.status in TERMINAL_STATUSES
        if late:
            self._kill_one(int(pid), killer, "cancelled")

    def unregister_pid(self, task_id: Optional[str], pid: int) -> None:
        if not task_id:
            return
        with self.lock:
            task = self.tasks.get(task_id)
            if task and pid in task.pids:
                task.pids.remove(pid)
            if task:
                task.killers.pop(pid, None)

    @staticmethod
    def _kill_one(pid: int, killer: Optional[Callable[[str], None]], reason: str) -> None:
        if killer is not None:
            try:
                killer(reason)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"engine stop for pid {pid} failed ({exc}) — tree-killing")
        _kill_pid_tree(pid)

    def kill_processes(self, task_id: str, reason: str = "cancelled") -> int:
        with self.lock:
            task = self.tasks.get(task_id)
            pids = list(task.pids) if task else []
            killers = dict(task.killers) if task else {}
        for pid in pids:
            self._kill_one(pid, killers.get(pid), reason)
        return len(pids)

    def cancel(self, task_id: str, reason: str = "cancelled") -> bool:
        """Cancel a pending/running task and kill its CLI process tree.

        Returns False (and changes nothing) when the task is unknown or has
        already finished — a finished task's outcome is never overwritten.
        """
        return self._terminate(task_id, TaskStatus.CANCELLED, reason)

    def _on_timeout(self, task_id: str) -> None:
        with self.lock:
            task = self.tasks.get(task_id)
            limit = task.timeout_seconds if task else None
        if self._terminate(task_id, TaskStatus.FAILED, "timeout"):
            logger.warning(f"Task {task_id} exceeded task_timeout_seconds={limit} — killed")

    def _terminate(self, task_id: str, status: TaskStatus, reason: str) -> bool:
        with self.lock:
            task = self.tasks.get(task_id)
            if not task or task.status in TERMINAL_STATUSES:
                return False
            if task.future and not task.future.done():
                task.future.cancel()
            task.status = status
            task.completed_at = datetime.now()
            task.error = reason
            timer = self._timers.pop(task_id, None)
        if timer:
            timer.cancel()
        self.persist(task_id)
        self.kill_processes(task_id, reason)
        return True

    # ── Persistence + live bus ──────────────────────────────────────────

    def persist(self, task_id: str, throttle: bool = False) -> None:
        """Write the task through to data/telecode.db and publish its status.
        ``throttle`` (progress updates) skips writes closer than 0.5 s apart."""
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            now = time.monotonic()
            if throttle and now - task.last_persist < 0.5:
                return
            task.last_persist = now
            d = task_to_dict(task)
        try:
            from services.db import task_repo
            task_repo.save_task(d)
        except Exception:
            logger.exception(f"task {task_id}: DB write failed")
        _publish_status(d)

    def record_event(self, task: Task, seq: int, evt: Dict[str, Any]) -> None:
        try:
            from services.db import task_repo
            task_repo.append_event(task.task_id, seq, evt)
        except Exception:
            logger.exception(f"task {task.task_id}: DB event write failed")
        try:
            from services import bus
            framed = {**evt, "seq": seq, "task_id": task.task_id}
            bus.publish("task", task.task_id, "event", framed)
            run_id = (task.metadata or {}).get("run_id")
            if run_id:
                bus.publish("run", run_id, "event", framed)
        except Exception:
            logger.exception("bus publish failed")

    def get_task_record(self, task_id: str) -> Optional[Dict[str, Any]]:
        """``task_to_dict`` of a live task, else the persisted record (with its
        events) of one from before a restart or evicted from memory."""
        task = self.get_task(task_id)
        if task:
            with self.lock:
                return task_to_dict(task)
        try:
            from services.db import task_repo
            return task_repo.load_task(task_id)
        except Exception:
            logger.exception("task DB read failed")
            return None

    def list_task_records(self, limit: int = FINISHED_KEEP_MIN) -> List[Dict[str, Any]]:
        """Live tasks plus up to ``limit`` persisted ones not in memory."""
        tasks = self.list_tasks()
        with self.lock:
            live = [task_to_dict(t) for t in tasks]
        try:
            from services.db import task_repo
            live += task_repo.list_tasks(limit=limit, exclude={d["task_id"] for d in live})
        except Exception:
            logger.exception("task DB list failed")
        return live

    def reconcile_persisted(self) -> int:
        """Startup: persisted pending/running tasks this process is not running
        → failed ("interrupted: telecode restarted …")."""
        try:
            from services.db import task_repo
            return task_repo.reconcile_interrupted(self.is_active)
        except Exception:
            logger.exception("task reconcile failed")
            return 0

    # ── Queries / housekeeping ──────────────────────────────────────────

    def get_task(self, task_id: str) -> Optional[Task]:
        with self.lock: return self.tasks.get(task_id)

    def list_tasks(self) -> List[Task]:
        self.evict_finished()
        with self.lock: return list(self.tasks.values())

    def is_active(self, task_id: Optional[str]) -> bool:
        if not task_id:
            return False
        with self.lock:
            t = self.tasks.get(task_id)
            return bool(t and t.status in ACTIVE_STATUSES)

    def session_has_active_task(self, session_id: str, namespace: Optional[str] = None) -> bool:
        with self.lock:
            return any(
                t.session_id == session_id and t.session_namespace == namespace
                and t.status in ACTIVE_STATUSES
                for t in self.tasks.values()
            )

    def evict_finished(self, *, force: bool = False, now: Optional[datetime] = None) -> int:
        """Drop finished tasks older than FINISHED_RETENTION_SECONDS, always
        keeping the newest FINISHED_KEEP_MIN finished ones. Rate-limited."""
        import time as _time
        mono = _time.monotonic()
        if not force and mono - self._last_evict < _EVICT_EVERY_SECONDS:
            return 0
        self._last_evict = mono
        now = now or datetime.now()
        with self.lock:
            finished = [t for t in self.tasks.values() if t.status in TERMINAL_STATUSES]
            finished.sort(key=lambda t: t.completed_at or t.created_at, reverse=True)
            removed = 0
            for t in finished[FINISHED_KEEP_MIN:]:
                ts = t.completed_at or t.created_at
                if (now - ts).total_seconds() > FINISHED_RETENTION_SECONDS:
                    self.tasks.pop(t.task_id, None)
                    removed += 1
        if removed:
            logger.info(f"Evicted {removed} finished tasks from the queue")
        return removed


_task_queue: Optional[TaskQueue] = None
_queue_guard = threading.Lock()


def get_task_queue() -> TaskQueue:
    global _task_queue
    if _task_queue is None:
        with _queue_guard:
            if _task_queue is None:
                _task_queue = TaskQueue()
    return _task_queue


def cancel_task(task_id: str, reason: str = "cancelled") -> bool:
    """Shared cancel entry point (API, run executor, …). See TaskQueue.cancel."""
    return get_task_queue().cancel(task_id, reason)


def task_status_summary(d: Dict[str, Any]) -> Dict[str, Any]:
    """The ``status`` / ``task.status`` SSE payload for a ``task_to_dict`` record."""
    md = d.get("metadata") or {}
    return {
        "task_id": d["task_id"], "task_type": d["task_type"], "status": d["status"],
        "progress": d.get("progress"), "progress_message": md.get("progress_message"),
        "session_id": d.get("session_id"), "session_namespace": d.get("session_namespace"),
        "error": d.get("error"), "created_at": d.get("created_at"),
        "completed_at": d.get("completed_at"), "run_id": md.get("run_id"),
        "step_id": md.get("step_id"), "source": md.get("source"),
    }


def _publish_status(d: Dict[str, Any]) -> None:
    try:
        from services import bus
        summary = task_status_summary(d)
        bus.publish("task", d["task_id"], "status", summary)
        if summary["run_id"]:
            bus.publish("run", summary["run_id"], "status", summary)
        bus.publish_global("task", "task.status", summary)
    except Exception:
        logger.exception("bus publish failed")


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
        "pool": task.pool,
        "timeout_seconds": task.timeout_seconds,
    }
