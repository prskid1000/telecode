"""Heartbeat-driven scheduler for routines.

A single daemon thread wakes every ``MANAGER_INTERVAL_SECONDS`` and
walks every routine on disk via :func:`tick`. For routines whose
``next_fire_at`` has been reached, :func:`fire_routine` submits a task
against the routine's permanent session.

Skip-if-running is enforced on every fire, under the routine's lock
(the record is re-read inside it), so a manager tick and a manual
run-now can never both submit -- a routine never has two of its own
tasks in flight at once. ``next_fire_at`` advances regardless of whether
we fired or skipped, so missed windows are dropped rather than caught up.

Routine tasks run on the queue's background pool with
``task_timeout_seconds`` enforced by the queue (the CLI tree is killed and
the task fails with error "timeout").
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from services.routine import routine_store
from services.task.task_manager import POOL_BACKGROUND, TaskStatus, get_task_queue

logger = logging.getLogger("telecode.services.routine.manager")

MANAGER_INTERVAL_SECONDS = 60

_thread: Optional[threading.Thread] = None
_stop_event: Optional[threading.Event] = None
_lock = threading.Lock()


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _format_since(iso_ts: Optional[str]) -> str:
    if not iso_ts:
        return "unknown"
    parsed = routine_store._parse_iso(iso_ts)
    if not parsed:
        return "unknown"
    delta = (datetime.now(timezone.utc) - parsed).total_seconds()
    return _format_duration(max(0, int(delta)))


def start(*, run_now: bool = True) -> None:
    """Spawn the heartbeat thread. Idempotent."""
    global _thread, _stop_event
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop_event = threading.Event()
        _thread = threading.Thread(
            target=_loop,
            name="routine-manager",
            args=(_stop_event,),
            daemon=True,
        )
        _thread.start()
        logger.info(
            f"routine_manager: heartbeat started (every {MANAGER_INTERVAL_SECONDS}s)"
        )
    try:
        reconcile_interrupted()
    except Exception:
        logger.exception("routine_manager: interrupted-fire reconcile failed")
    if run_now:
        # Catch up any routines that became due while we were down. The loop
        # itself sleeps first, so without this the first tick wouldn't run
        # until MANAGER_INTERVAL_SECONDS from now.
        try:
            tick()
        except Exception:
            logger.exception("routine_manager: bootstrap-tick failed")


def stop() -> None:
    global _thread, _stop_event
    with _lock:
        if _stop_event is not None:
            _stop_event.set()
        _thread = None
        _stop_event = None


def _loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        # Wait first, then tick. The bootstrap-tick in start() covers t=0.
        if stop_event.wait(MANAGER_INTERVAL_SECONDS):
            return
        try:
            tick()
        except Exception:
            logger.exception("routine_manager: tick crashed (continuing)")


_TERMINAL_STATUSES = (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)


def _reconcile_completion(rec: dict) -> None:
    """If this routine's last_task_id finished, write last_completed_at.

    Idempotent via routine_store.record_completion's dedup. Cheap: one
    in-memory lookup per routine per tick.
    """
    rid = rec.get("routine_id")
    last_task_id = rec.get("last_task_id")
    if not rid or not last_task_id:
        return
    if rec.get("last_completed_task_id") == last_task_id:
        return  # already reconciled
    task = get_task_queue().get_task(last_task_id)
    if not task or task.status not in _TERMINAL_STATUSES:
        return
    completed_at = task.completed_at.isoformat() if task.completed_at else None
    routine_store.record_completion(
        rid,
        task_id=last_task_id,
        status=task.status.value,
        completed_at_iso=completed_at,
        error=task.error,
    )


def tick() -> None:
    started = time.perf_counter()
    # Reconcile completion across ALL routines first (active + paused) -- a
    # routine paused while its last fire is still running should still get
    # its last_completed_at written when that task finishes.
    try:
        all_routines = routine_store.list_all()
    except Exception:
        logger.exception("routine_manager.tick: list_all failed")
        return
    for rec in all_routines:
        try:
            _reconcile_completion(rec)
        except Exception:
            logger.exception(
                f"routine_manager.tick: completion reconcile failed for "
                f"{rec.get('routine_id')}"
            )

    # Only active routines participate in the fire decision.
    routines = [r for r in all_routines if r.get("status") == "active"]

    fired = 0
    skipped = 0
    errored = 0
    for rec in routines:
        rid = rec.get("routine_id")
        if not rid:
            continue
        try:
            if not routine_store.is_due(rec):
                continue
            task_id = fire_routine(rec, source="manager")
            if task_id is None:
                skipped += 1
            else:
                fired += 1
        except Exception:
            errored += 1
            logger.exception(f"routine_manager.tick: routine {rid} fire failed")

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if fired or skipped or errored:
        logger.info(
            f"routine_manager.tick: scanned={len(routines)} fired={fired} "
            f"skipped={skipped} errored={errored} elapsed_ms={elapsed_ms}"
        )


OUTPUTS_ONLY_INSTRUCTION = (
    "\n---\n"
    "Output rule for this routine: reply with ONLY this tick's deliverable -- "
    "no narration of the steps you took, no preamble, no sign-off. If there "
    "is nothing new, reply with one short line saying so.\n"
)


def reconcile_interrupted() -> int:
    """Startup pass (B6): a routine whose last task is not in the queue (the
    process restarted) and was never reconciled gets completion status
    "interrupted", so the UI stops showing it as running."""
    queue = get_task_queue()
    n = 0
    for rec in routine_store.list_all():
        tid = rec.get("last_task_id")
        if not tid or rec.get("last_completed_task_id") == tid or queue.get_task(tid):
            continue
        routine_store.record_completion(
            rec["routine_id"], task_id=tid, status="interrupted",
            completed_at_iso=None, error="interrupted: telecode restarted while this fire was running",
        )
        n += 1
    if n:
        logger.warning(f"routine_manager: marked {n} routine fire(s) from a previous process as interrupted")
    return n


def fire_routine(rec: dict, *, source: str = "manager") -> Optional[str]:
    """Submit a task for this routine.

    Honours skip-if-running under the routine's lock. Always advances
    ``next_fire_at`` (no catch-up). Returns the new task_id, or None if
    skipped/failed.
    """
    rid = rec["routine_id"]
    with routine_store._lock_for(rid):
        return _fire_locked(rid, rec, source)


def _fire_locked(rid: str, rec: dict, source: str) -> Optional[str]:
    queue = get_task_queue()
    rec = routine_store.get(rid) or rec  # fresh read inside the lock
    if source == "manager" and not routine_store.is_due(rec):
        return None  # another caller fired it since the tick decided

    # Skip-if-running: previous task still in flight?
    last_task_id = rec.get("last_task_id")
    if last_task_id:
        prev = queue.get_task(last_task_id)
        if prev and prev.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            logger.info(
                f"routine {rid} ({rec.get('name')}): previous task "
                f"{last_task_id} still {prev.status.value} -- skip"
            )
            routine_store.record_fire(rid, task_id=None, skipped=True)
            return None

    try:
        task_type = routine_store.normalize_task_type(rec.get("task_type") or "CLAUDE_CODE")
    except ValueError as exc:
        logger.error(f"routine {rid}: {exc} -- not firing")
        routine_store.record_fire(rid, task_id=None, error=str(exc))
        return None

    # Heartbeat preface -- frames this fire as cycle N of an ongoing
    # assignment so the resumed CLI continues progress instead of
    # restarting each tick.
    wake_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    every_s = int((rec.get("schedule") or {}).get("every_seconds") or 0)
    cadence = _format_duration(every_s) if every_s else "unknown cadence"
    tick_num = int(rec.get("total_runs") or 0) + 1
    last_fired_at = rec.get("last_fire_at")
    since_last = _format_since(last_fired_at)
    last_line = (
        f"Last successful tick: {since_last} ago (at {last_fired_at})."
        if last_fired_at else
        "Last successful tick: (none yet -- this is the first fire)."
    )
    rname = rec.get("name") or rid[:8]
    heartbeat_preface = (
        f"[Routine heartbeat -- \"{rname}\" -- tick #{tick_num} -- "
        f"fired by {source} at {wake_ts}]\n"
        f"Cadence: every {cadence}. {last_line}\n"
        "\n"
        "This is a RECURRING scheduled wake-up, not a one-shot conversation. "
        "Your job is to advance an ongoing assignment, not start over each tick.\n"
        "\n"
        "Discipline for every tick:\n"
        "1. Check your session memory first -- prior-tick outputs are already "
        "in the conversation history (the session is resumed). DO NOT redo "
        "work that's already done; build on it.\n"
        "2. Treat the standing directive below as a continuing assignment. "
        "Each tick is one cycle of carrying it out.\n"
        "3. If nothing new is required this cycle (no new inputs, no changes "
        "to act on), say so in one short line and stop -- do not fabricate "
        "work to fill the cycle.\n"
        "4. Keep output concise. Replies stack up over many ticks; be terse "
        "and high-signal.\n"
        "\n"
        "Standing directive (re-issued each tick so you don't drift):\n"
        "---\n"
    )

    prompt = heartbeat_preface + rec["prompt"]
    # outputs_only is a prompt instruction, never a handler kwarg: the
    # handlers share one signature and an unknown kwarg is a TypeError (B5).
    if rec.get("outputs_only"):
        prompt += OUTPUTS_ONLY_INSTRUCTION
    params: dict = {
        "prompt": prompt,
        # All three engines can run against the local llama.cpp proxy: Claude
        # via ANTHROPIC_BASE_URL, Codex via a `-c model_provider=telecode`
        # override pointing at /v1/responses, Antigravity via its Gemini-API
        # route (GOOGLE_GEMINI_BASE_URL + an isolated home).
        "is_local": bool(rec.get("is_local", False)),
    }

    try:
        task_id = queue.submit_task(
            task_type=task_type,
            params=params,
            metadata={
                "routine_id": rid,
                "routine_name": rec.get("name"),
                "fire_source": source,
            },
            task_timeout_seconds=int(rec.get("task_timeout_seconds") or 1800),
            session_id=rec["session_id"],
            session_namespace=rec.get("session_namespace"),
            pool=POOL_BACKGROUND,
        )
        routine_store.record_fire(rid, task_id=task_id)
        logger.info(
            f"routine {rid} ({rec.get('name')}) fired via {source} -> task {task_id}"
        )
        return task_id
    except Exception as exc:
        logger.exception(f"routine {rid} failed to submit task")
        routine_store.record_fire(rid, task_id=None, error=str(exc))
        return None
