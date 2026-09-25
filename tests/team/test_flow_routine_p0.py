"""B5 (outputs_only never reaches handler kwargs), B13 (skip-if-running
under the routine lock, task_type validation), B6 (interrupted fires)."""

from __future__ import annotations

import threading
import time

import pytest

from services.routine import routine_manager, routine_service, routine_store
from services.task.task_manager import TaskStatus, get_task_queue


@pytest.fixture(autouse=True)
def _restore_handlers():
    q = get_task_queue()
    saved = dict(q.task_handlers)
    yield
    q.task_handlers.clear()
    q.task_handlers.update(saved)


def _mk(tmp_data_root, **kw):
    body = {"name": "r", "prompt": "do it", "schedule": {"every_seconds": 60}, **kw}
    return routine_service.create_routine(body)


def _wait_status(tid, deadline=5.0):
    q = get_task_queue()
    end = time.time() + deadline
    while time.time() < end:
        t = q.get_task(tid)
        if t and t.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return t
        time.sleep(0.02)
    return q.get_task(tid)


def test_b5_outputs_only_is_prompt_instruction_not_kwarg(tmp_data_root):
    """With the REAL strict signature (no **kw) an extra kwarg is a TypeError."""
    seen = {}

    def strict_handler(prompt=None, is_local=False, *, agent_id=None, agent=None, job=None,
                       agent_files=None, job_files=None, model=None):
        seen["prompt"] = prompt
        return {"result": "ok"}

    get_task_queue().register_handler("CLAUDE_CODE", strict_handler)
    rec = _mk(tmp_data_root, outputs_only=True)
    tid = routine_manager.fire_routine(rec, source="manual")
    t = _wait_status(tid)
    assert t.status == TaskStatus.COMPLETED, t.error
    assert "reply with ONLY this tick's deliverable" in seen["prompt"]
    assert seen["prompt"].rstrip().endswith("saying so.")


def test_b13_skip_if_running_under_lock(tmp_data_root):
    release = threading.Event()
    get_task_queue().register_handler("CLAUDE_CODE", lambda **kw: (release.wait(5), {"result": "x"})[1])
    rec = _mk(tmp_data_root)
    results = []
    barrier = threading.Barrier(4)

    def fire():
        barrier.wait()
        results.append(routine_manager.fire_routine(rec, source="manual"))

    threads = [threading.Thread(target=fire) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join(5)
    release.set()
    fired = [r for r in results if r]
    assert len(fired) == 1, results          # the other three saw it running and skipped
    after = routine_store.get(rec["routine_id"])
    assert after["total_runs"] == 1 and after["skipped_runs"] == 3


def test_b13_manager_tick_rechecks_due_under_lock(tmp_data_root):
    get_task_queue().register_handler("CLAUDE_CODE", lambda **kw: {"result": "x"})
    rec = _mk(tmp_data_root)
    # not due yet (next_fire_at is 60s ahead) → a stale "due" decision is ignored
    assert routine_manager.fire_routine(rec, source="manager") is None


def test_b13_task_type_validated(tmp_data_root):
    with pytest.raises(ValueError):
        _mk(tmp_data_root, task_type="ECHO")
    with pytest.raises(ValueError):
        _mk(tmp_data_root, task_type="NOPE")
    rec = _mk(tmp_data_root, task_type="codex")          # engine name normalised
    assert rec["task_type"] == "CODEX"
    with pytest.raises(ValueError):
        routine_store.patch(rec["routine_id"], {"task_type": "bogus"})


def test_b13_routine_timeout_enforced(tmp_data_root):
    stop = threading.Event()

    def slow(**kw):
        stop.wait(10)
        return {"result": "late"}

    get_task_queue().register_handler("CLAUDE_CODE", slow)
    rec = _mk(tmp_data_root)
    routine_store.patch(rec["routine_id"], {"task_timeout_seconds": 30})
    # 30 s is the store minimum; drive the queue's watchdog directly instead of waiting.
    tid = routine_manager.fire_routine(routine_store.get(rec["routine_id"]), source="manual")
    t = get_task_queue().get_task(tid)
    assert t.timeout_seconds == 30 and t.pool == "background"
    deadline = time.time() + 5
    while t.status == TaskStatus.PENDING and time.time() < deadline:
        time.sleep(0.02)
    get_task_queue()._on_timeout(tid)
    stop.set()
    t = _wait_status(tid)
    assert t.status == TaskStatus.FAILED and t.error == "timeout"


def test_b6_routine_fire_lost_on_restart_marked_interrupted(tmp_data_root):
    rec = _mk(tmp_data_root)
    routine_store.record_fire(rec["routine_id"], task_id="task-from-previous-process")
    assert routine_manager.reconcile_interrupted() == 1
    after = routine_store.get(rec["routine_id"])
    assert after["last_completion_status"] == "interrupted"
    assert routine_manager.reconcile_interrupted() == 0                 # idempotent
