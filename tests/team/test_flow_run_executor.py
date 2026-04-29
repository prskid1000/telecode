"""Flow tests for the run executor — mocks CLAUDE_CODE/GEMINI handler so no
real subprocess is spawned. Verifies phase grouping, workspace strategy,
output threading, failure halt, and cancellation.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from services.agent.agent_manager import get_agent_manager
from services.job.job_manager import get_job_manager
from services.run.run_store import get_run_store
from services.session import session_store
from services.run.executor import start_run, cancel_run


def _wait_for_run(run_id, predicate, deadline=10.0):
    rs = get_run_store()
    end = time.time() + deadline
    while time.time() < end:
        r = rs.get_run(run_id)
        if r and predicate(r):
            return r
        time.sleep(0.05)
    return rs.get_run(run_id)


def _setup_agent_and_workspace():
    am = get_agent_manager()
    a = am.create_agent("flow-bot", soul="soul")
    am.set_internal_files(a["id"], {"AGENT.md": "rules", "MEMORY.md": ""})
    sid = "ws-flow"
    if not session_store.exists(sid):
        session_store.create(session_id=sid, data={"name": "flow-ws"})
    return a["id"], sid


def _start(job, **kw):
    return asyncio.run(start_run(job=job, is_local=False, source="user", **kw))


def test_single_step_completes_in_workspace(tmp_data_root, fake_task_queue):
    fake_task_queue.set_result("hello world")
    agent_id, ws = _setup_agent_and_workspace()
    j = get_job_manager().create_job({
        "title": "single-test", "workspace_id": ws,
        "pipeline": {"mode": "single", "steps": [{"agent_id": agent_id, "name": "only"}]},
    })

    run = _start(j)
    final = _wait_for_run(run["run_id"], lambda r: r["status"] in ("completed", "failed", "partial", "cancelled"))
    assert final["status"] == "completed"
    assert final["steps"][0]["status"] == "completed"
    assert final["steps"][0]["session_id"] == ws
    assert final["steps"][0]["result_preview"] == "hello world"


def test_sequential_threads_previous_output_when_depends_on_text(tmp_data_root, fake_task_queue):
    fake_task_queue.set_result("STEP1-RESULT")
    agent_id, ws = _setup_agent_and_workspace()
    j = get_job_manager().create_job({
        "title": "seq-test", "workspace_id": ws,
        "task_description": "go",
        "pipeline": {"mode": "sequential", "steps": [
            {"agent_id": agent_id, "name": "first"},
            {"agent_id": agent_id, "name": "second", "depends_on_text": True},
        ]},
    })

    run = _start(j)
    final = _wait_for_run(run["run_id"], lambda r: r["status"] in ("completed", "failed", "partial", "cancelled"))
    assert final["status"] == "completed"

    # The 2nd step's prompt must contain the 1st step's output
    second_call = fake_task_queue.last_calls[1]
    assert "<previous_output>" in second_call["prompt"]
    assert "STEP1-RESULT" in second_call["prompt"]


def test_sequential_does_not_thread_output_when_disabled(tmp_data_root, fake_task_queue):
    fake_task_queue.set_result("ANYTHING")
    agent_id, ws = _setup_agent_and_workspace()
    j = get_job_manager().create_job({
        "title": "seq-no-feed", "workspace_id": ws,
        "pipeline": {"mode": "sequential", "steps": [
            {"agent_id": agent_id, "name": "a"},
            {"agent_id": agent_id, "name": "b", "depends_on_text": False},
        ]},
    })

    run = _start(j)
    _wait_for_run(run["run_id"], lambda r: r["status"] in ("completed", "failed", "partial"))
    second_call = fake_task_queue.last_calls[1]
    assert "previous_output" not in second_call["prompt"]


def test_first_failure_halts_remaining_marked_skipped(tmp_data_root, fake_task_queue):
    fake_task_queue.fail()
    agent_id, ws = _setup_agent_and_workspace()
    j = get_job_manager().create_job({
        "title": "halt-test", "workspace_id": ws,
        "pipeline": {"mode": "sequential", "steps": [
            {"agent_id": agent_id, "name": "a"},
            {"agent_id": agent_id, "name": "b"},
            {"agent_id": agent_id, "name": "c"},
        ]},
    })
    run = _start(j)
    final = _wait_for_run(run["run_id"], lambda r: r["status"] in ("completed", "failed", "partial", "cancelled"))
    statuses = [s["status"] for s in final["steps"]]
    assert statuses[0] == "failed"
    assert statuses[1] == "skipped"
    assert statuses[2] == "skipped"
    assert final["status"] in ("failed", "partial")


def test_parallel_uses_ephemeral_sessions_per_step(tmp_data_root, fake_task_queue):
    fake_task_queue.set_result("ok")
    agent_id, ws = _setup_agent_and_workspace()
    j = get_job_manager().create_job({
        "title": "par-test", "workspace_id": ws,
        "pipeline": {"mode": "parallel", "steps": [
            {"agent_id": agent_id, "name": "a"},
            {"agent_id": agent_id, "name": "b"},
        ]},
    })
    run = _start(j)
    final = _wait_for_run(run["run_id"], lambda r: r["status"] in ("completed", "failed", "partial"))
    assert final["status"] == "completed"

    sids = [s["session_id"] for s in final["steps"]]
    assert len(set(sids)) == 2          # unique
    assert all(sid != ws for sid in sids)  # not the job's workspace
    # And ephemeral sessions are deleted after the run
    for sid in sids:
        assert not session_store.exists(sid, namespace="run-parallel")


def test_custom_phases_run_in_order(tmp_data_root, fake_task_queue):
    """Phase 0 (single, in workspace) → phase 1 (parallel, ephemeral) → phase 2."""
    fake_task_queue.set_result("phase-out")
    agent_id, ws = _setup_agent_and_workspace()
    j = get_job_manager().create_job({
        "title": "custom-test", "workspace_id": ws,
        "pipeline": {"mode": "custom", "steps": [
            {"agent_id": agent_id, "name": "A", "phase": 0},
            {"agent_id": agent_id, "name": "B", "phase": 1},
            {"agent_id": agent_id, "name": "C", "phase": 1},
            {"agent_id": agent_id, "name": "D", "phase": 2, "depends_on_text": True},
        ]},
    })
    run = _start(j)
    final = _wait_for_run(run["run_id"], lambda r: r["status"] in ("completed", "failed", "partial"))
    assert final["status"] == "completed"

    by_name = {s["name"]: s for s in final["steps"]}
    # A and D run on the job workspace (single-step phases)
    assert by_name["A"]["session_id"] == ws
    assert by_name["D"]["session_id"] == ws
    # B and C ran on distinct ephemeral sessions
    assert by_name["B"]["session_id"] != by_name["C"]["session_id"]
    assert by_name["B"]["session_id"] != ws
    assert by_name["C"]["session_id"] != ws

    # D should have received B+C outputs as <previous_outputs>
    d_call = next(c for c in fake_task_queue.last_calls if "phase-out" in (c.get("prompt") or "") and "<previous_outputs>" in (c.get("prompt") or ""))
    assert "<previous_outputs>" in d_call["prompt"]
    assert d_call["prompt"].count("<output ") == 2


def test_cancel_run_marks_remaining_skipped(tmp_data_root, fake_task_queue):
    fake_task_queue.block()
    agent_id, ws = _setup_agent_and_workspace()
    j = get_job_manager().create_job({
        "title": "cancel-test", "workspace_id": ws,
        "pipeline": {"mode": "sequential", "steps": [
            {"agent_id": agent_id, "name": "a"},
            {"agent_id": agent_id, "name": "b"},
            {"agent_id": agent_id, "name": "c"},
        ]},
    })
    run = _start(j)
    # Wait until the first step is running, then cancel
    _wait_for_run(run["run_id"], lambda r: r["steps"][0]["status"] == "running", deadline=5)
    assert cancel_run(run["run_id"]) is True
    fake_task_queue.release()
    final = _wait_for_run(run["run_id"], lambda r: r["status"] in ("cancelled", "failed", "partial"))
    assert final["status"] in ("cancelled", "failed", "partial")
    # Remaining steps marked skipped (or cancelled), not running
    for s in final["steps"][1:]:
        assert s["status"] in ("skipped", "cancelled", "pending")


def test_passes_agent_id_into_handler_params(tmp_data_root, fake_task_queue):
    agent_id, ws = _setup_agent_and_workspace()
    j = get_job_manager().create_job({
        "title": "agent-id-test", "workspace_id": ws,
        "pipeline": {"mode": "single", "steps": [{"agent_id": agent_id}]},
    })
    run = _start(j)
    _wait_for_run(run["run_id"], lambda r: r["status"] in ("completed", "failed", "partial"))
    assert fake_task_queue.last_calls[0]["agent_id"] == agent_id
