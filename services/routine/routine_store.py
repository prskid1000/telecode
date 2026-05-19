"""Filesystem-backed routine storage.

Each routine lives at ``<settings_dir>/data/routines/<routine_id>.json``
(atomic tmp+rename writes). Schedule is interval-only: ``every_seconds``.

The manager (:mod:`routine_manager`) reads these records on every
heartbeat tick to decide what to fire. ``next_fire_at`` on the record
is the authoritative scheduling state -- recomputed on create, resume,
schedule-patch, and after each fire.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger("telecode.services.routine.store")


def _routines_dir() -> Path:
    return Path(config._settings_dir()) / "data" / "routines"


_ROUTINE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_NAME_MAX = 200

# Lower bound on a routine's interval. The manager itself ticks once a
# minute (see routine_manager.MANAGER_INTERVAL_SECONDS); anything below
# the manager interval would only fire once per manager tick anyway.
MIN_ROUTINE_INTERVAL_SECONDS = 60

_locks_guard = threading.Lock()
_locks: Dict[str, threading.RLock] = {}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value[:-1]).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except ValueError:
        return None


def compute_next_fire(every_seconds: int, *, from_dt: Optional[datetime] = None) -> str:
    base = from_dt or _now_utc()
    return _to_iso(base + timedelta(seconds=int(every_seconds)))


def is_due(rec: Dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    if rec.get("status") != "active":
        return False
    nfa = _parse_iso(rec.get("next_fire_at"))
    if nfa is None:
        return True
    return (now or _now_utc()) >= nfa


def _lock_for(routine_id: str) -> threading.RLock:
    with _locks_guard:
        lock = _locks.get(routine_id)
        if lock is None:
            lock = threading.RLock()
            _locks[routine_id] = lock
        return lock


def _validate_id(routine_id: str) -> None:
    if not _ROUTINE_ID_RE.match(routine_id):
        raise ValueError(
            f"Invalid routine_id '{routine_id}'. Must match [a-zA-Z0-9_-]{{1,128}}."
        )


def _path(routine_id: str) -> Path:
    return _routines_dir() / f"{routine_id}.json"


def _atomic_write(routine_id: str, data: Dict[str, Any]) -> None:
    d = _routines_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{routine_id}.tmp"
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(_path(routine_id))


def _validate_schedule(sched: Any) -> Dict[str, Any]:
    if not isinstance(sched, dict):
        raise ValueError("schedule must be an object")
    every = sched.get("every_seconds")
    if not isinstance(every, int) or every < MIN_ROUTINE_INTERVAL_SECONDS:
        raise ValueError(
            f"schedule.every_seconds must be an integer >= {MIN_ROUTINE_INTERVAL_SECONDS}"
        )
    return {"every_seconds": every}


def build_record(
    *,
    name: str,
    prompt: str,
    schedule: Dict[str, Any],
    session_id: str,
    session_namespace: Optional[str] = None,
    description: Optional[str] = None,
    outputs_only: bool = False,
    task_timeout_seconds: int = 1800,
    session_idle_timeout_seconds: Optional[int] = None,
    task_type: str = "CLAUDE_CODE",
    is_local: bool = False,
    routine_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required")
    if len(name) > _NAME_MAX:
        raise ValueError(f"name too long (>{_NAME_MAX} chars)")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required")
    if not isinstance(task_timeout_seconds, int) or task_timeout_seconds < 30:
        raise ValueError("task_timeout_seconds must be an integer >= 30")

    rid = routine_id or str(uuid.uuid4())
    _validate_id(rid)
    validated = _validate_schedule(schedule)
    now = _now_utc()
    return {
        "routine_id": rid,
        "name": name.strip(),
        "description": (description or "").strip() or None,
        "prompt": prompt,
        "outputs_only": bool(outputs_only),
        "task_type": task_type or "CLAUDE_CODE",
        "is_local": bool(is_local),
        "task_timeout_seconds": int(task_timeout_seconds),
        "schedule": validated,
        "session_id": session_id,
        "session_namespace": session_namespace,
        "session_idle_timeout_seconds": session_idle_timeout_seconds,
        "status": "active",
        "created_at": _to_iso(now),
        "updated_at": _to_iso(now),
        "last_fire_at": None,
        "last_task_id": None,
        "last_completed_at": None,
        "last_completed_task_id": None,
        "last_completion_status": None,
        "next_fire_at": compute_next_fire(validated["every_seconds"], from_dt=now),
        "total_runs": 0,
        "skipped_runs": 0,
        "last_error": None,
    }


def save(record: Dict[str, Any]) -> Dict[str, Any]:
    rid = record["routine_id"]
    _validate_id(rid)
    with _lock_for(rid):
        record["updated_at"] = _to_iso(_now_utc())
        _atomic_write(rid, record)
        return record


def get(routine_id: str) -> Optional[Dict[str, Any]]:
    _validate_id(routine_id)
    p = _path(routine_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.exception(f"Failed to read routine {routine_id}")
        return None


def patch(routine_id: str, patch_body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    _validate_id(routine_id)
    if not isinstance(patch_body, dict):
        raise ValueError("patch body must be an object")

    updatable = {
        "name", "description", "prompt", "outputs_only",
        "task_timeout_seconds", "schedule", "task_type", "is_local",
    }
    unknown = set(patch_body) - updatable
    if unknown:
        raise ValueError(f"Cannot patch fields: {sorted(unknown)}")

    with _lock_for(routine_id):
        rec = get(routine_id)
        if not rec:
            return None
        if "name" in patch_body:
            name = patch_body["name"]
            if not isinstance(name, str) or not name.strip():
                raise ValueError("name must be a non-empty string")
            if len(name) > _NAME_MAX:
                raise ValueError(f"name too long (>{_NAME_MAX} chars)")
            rec["name"] = name.strip()
        if "description" in patch_body:
            desc = patch_body["description"]
            rec["description"] = (desc or "").strip() or None
        if "prompt" in patch_body:
            prompt = patch_body["prompt"]
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError("prompt must be a non-empty string")
            rec["prompt"] = prompt
        if "outputs_only" in patch_body:
            rec["outputs_only"] = bool(patch_body["outputs_only"])
        if "is_local" in patch_body:
            rec["is_local"] = bool(patch_body["is_local"])
        if "task_type" in patch_body:
            tt = patch_body["task_type"]
            if not isinstance(tt, str) or not tt.strip():
                raise ValueError("task_type must be a non-empty string")
            rec["task_type"] = tt.strip()
        if "task_timeout_seconds" in patch_body:
            tts = patch_body["task_timeout_seconds"]
            if not isinstance(tts, int) or tts < 30:
                raise ValueError("task_timeout_seconds must be an integer >= 30")
            rec["task_timeout_seconds"] = tts
        if "schedule" in patch_body:
            rec["schedule"] = _validate_schedule(patch_body["schedule"])
            rec["next_fire_at"] = compute_next_fire(rec["schedule"]["every_seconds"])
        rec["updated_at"] = _to_iso(_now_utc())
        _atomic_write(routine_id, rec)
        return rec


def set_status(routine_id: str, status: str) -> Optional[Dict[str, Any]]:
    if status not in ("active", "paused", "cancelled"):
        raise ValueError(f"Unknown status '{status}'")
    with _lock_for(routine_id):
        rec = get(routine_id)
        if not rec:
            return None
        rec["status"] = status
        rec["updated_at"] = _to_iso(_now_utc())
        if status == "active":
            rec["next_fire_at"] = compute_next_fire(rec["schedule"]["every_seconds"])
        elif status == "paused":
            rec["next_fire_at"] = None
        _atomic_write(routine_id, rec)
        return rec


def record_fire(
    routine_id: str,
    *,
    task_id: Optional[str],
    skipped: bool = False,
    error: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    with _lock_for(routine_id):
        rec = get(routine_id)
        if not rec:
            return None
        now = _now_utc()
        if skipped:
            rec["skipped_runs"] = rec.get("skipped_runs", 0) + 1
        else:
            rec["total_runs"] = rec.get("total_runs", 0) + 1
            rec["last_fire_at"] = _to_iso(now)
            if task_id:
                rec["last_task_id"] = task_id
        rec["next_fire_at"] = compute_next_fire(
            rec["schedule"]["every_seconds"], from_dt=now
        )
        rec["last_error"] = error
        rec["updated_at"] = _to_iso(now)
        _atomic_write(routine_id, rec)
        return rec


def record_completion(
    routine_id: str,
    *,
    task_id: str,
    status: str,
    completed_at_iso: Optional[str],
    error: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Mark a fired task as completed/failed/cancelled. Idempotent per task_id.

    The manager polls terminal status of ``last_task_id`` each tick; this
    function dedups via ``last_completed_task_id`` so re-polling the same
    task is a no-op. ``last_error`` is only overwritten when the task
    failed (success leaves any prior error in place is fine -- caller
    can pass ``error=None`` to clear).
    """
    with _lock_for(routine_id):
        rec = get(routine_id)
        if not rec:
            return None
        if rec.get("last_completed_task_id") == task_id:
            return rec
        rec["last_completed_task_id"] = task_id
        rec["last_completed_at"] = completed_at_iso
        rec["last_completion_status"] = status
        if error is not None:
            rec["last_error"] = error
        elif status == "completed":
            rec["last_error"] = None
        rec["updated_at"] = _to_iso(_now_utc())
        _atomic_write(routine_id, rec)
        return rec


def delete(routine_id: str) -> bool:
    _validate_id(routine_id)
    with _lock_for(routine_id):
        p = _path(routine_id)
        if not p.exists():
            return False
        p.unlink()
        return True


def list_all(
    status: Optional[str] = None,
    session_namespace: Optional[str] = None,
) -> List[Dict[str, Any]]:
    d = _routines_dir()
    if not d.exists():
        return []
    out: List[Dict[str, Any]] = []
    for path in sorted(d.glob("*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception(f"Skipping unreadable routine file {path}")
            continue
        if status and rec.get("status") != status:
            continue
        if session_namespace is not None and rec.get("session_namespace") != session_namespace:
            continue
        out.append(rec)
    return out
