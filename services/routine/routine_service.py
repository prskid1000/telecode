"""High-level CRUD for routines.

Composes :mod:`routine_store` (persistence), :mod:`routine_manager`
(heartbeat-driven firing), and :mod:`services.session.session_store`
(the bound session folder). Pause / resume / edit / delete are pure
file operations -- the heartbeat picks up changes on its next tick.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from services.routine import routine_manager, routine_store
from services.session import session_store
from services.task.task_manager import get_task_queue, task_to_dict

logger = logging.getLogger("telecode.services.routine.service")

# Sessions bound to a routine are long-lived. Default idle TTL = 1 year;
# every fire refreshes ``last_used_at`` via ``append_task_id`` so an
# active routine never lets its session expire.
_DEFAULT_SESSION_IDLE = 365 * 86400


def create_routine(body: Dict[str, Any]) -> Dict[str, Any]:
    """Create routine + bound session."""
    schedule = body.get("schedule") or {}
    session_namespace = body.get("session_namespace") or None
    session_idle_timeout = body.get("session_idle_timeout_seconds")
    if session_idle_timeout is None:
        session_idle_timeout = _DEFAULT_SESSION_IDLE

    session_id = body.get("session_id") or str(uuid.uuid4())
    pre_existed = session_store.exists(session_id, namespace=session_namespace)
    meta = session_store.ensure(
        session_id=session_id,
        session_idle_timeout_seconds=int(session_idle_timeout),
        namespace=session_namespace,
    )
    session_id = meta["session_id"]

    try:
        rec = routine_store.build_record(
            name=body.get("name", ""),
            prompt=body.get("prompt", ""),
            schedule=schedule,
            session_id=session_id,
            session_namespace=session_namespace,
            description=body.get("description"),
            outputs_only=bool(body.get("outputs_only", False)),
            task_timeout_seconds=int(body.get("task_timeout_seconds") or 1800),
            session_idle_timeout_seconds=int(session_idle_timeout),
            task_type=body.get("task_type") or "CLAUDE_CODE",
            is_local=bool(body.get("is_local", False)),
            routine_id=body.get("routine_id"),
        )
    except Exception:
        # Only roll back the session if WE created it on this call.
        if not pre_existed:
            try:
                session_store.delete(session_id, namespace=session_namespace)
            except Exception:
                logger.exception(f"Failed to rollback session {session_id}")
        raise

    return routine_store.save(rec)


def get_routine(routine_id: str) -> Optional[Dict[str, Any]]:
    rec = routine_store.get(routine_id)
    if rec:
        # Cheap inline reconcile so the UI poller picks up completion within
        # its 5s cadence instead of waiting up to 60s for the next manager tick.
        routine_manager._reconcile_completion(rec)
        rec = routine_store.get(routine_id) or rec
    return rec


def list_routines(
    status: Optional[str] = None,
    session_namespace: Optional[str] = None,
) -> List[Dict[str, Any]]:
    recs = routine_store.list_all(status=status, session_namespace=session_namespace)
    for rec in recs:
        try:
            routine_manager._reconcile_completion(rec)
        except Exception:
            pass
    # Re-read after reconcile so callers see fresh last_completed_at.
    return routine_store.list_all(status=status, session_namespace=session_namespace)


def patch_routine(routine_id: str, body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return routine_store.patch(routine_id, body)


def pause_routine(routine_id: str) -> Optional[Dict[str, Any]]:
    rec = routine_store.get(routine_id)
    if not rec:
        return None
    if rec.get("status") == "cancelled":
        return rec
    return routine_store.set_status(routine_id, "paused")


def resume_routine(routine_id: str) -> Optional[Dict[str, Any]]:
    rec = routine_store.get(routine_id)
    if not rec:
        return None
    if rec.get("status") == "cancelled":
        raise ValueError("Cannot resume a cancelled routine -- create a new one")
    return routine_store.set_status(routine_id, "active")


def run_now(routine_id: str) -> Dict[str, Any]:
    rec = routine_store.get(routine_id)
    if not rec:
        return {"task_id": None, "skipped": False, "error": "Routine not found"}
    if rec.get("status") != "active":
        return {"task_id": None, "skipped": True, "error": f"status={rec.get('status')}"}
    # Reconcile completion of the previous fire first so the skip-if-running
    # gate sees fresh terminal status instead of stale RUNNING.
    routine_manager._reconcile_completion(rec)
    rec = routine_store.get(routine_id) or rec
    task_id = routine_manager.fire_routine(rec, source="manual")
    return {"task_id": task_id, "skipped": task_id is None}


def delete_routine(routine_id: str, delete_session: bool = False) -> bool:
    rec = routine_store.get(routine_id)
    if not rec:
        return False
    if delete_session:
        try:
            session_store.delete(
                rec["session_id"], namespace=rec.get("session_namespace")
            )
        except Exception:
            logger.exception(f"Failed to delete session for routine {routine_id}")
    return routine_store.delete(routine_id)


def list_runs(routine_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Return tasks fired by this routine, newest first."""
    queue = get_task_queue()
    tasks = [
        t for t in queue.list_tasks()
        if (t.metadata or {}).get("routine_id") == routine_id
    ]
    tasks.sort(key=lambda t: t.created_at or datetime.min, reverse=True)
    return [task_to_dict(t) for t in tasks[:limit]]
