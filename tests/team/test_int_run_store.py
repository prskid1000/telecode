"""Integration tests for RunStore + finalise() status aggregation."""

from __future__ import annotations

from services.run.run_store import get_run_store


def _make_run(steps=None):
    rs = get_run_store()
    return rs.create_run(
        job_id="job-1",
        mode="sequential",
        source="user",
        steps=steps or [
            {"step_id": "s1", "agent_id": "a", "agent_name": "A", "name": "first"},
            {"step_id": "s2", "agent_id": "b", "agent_name": "B", "name": "second"},
        ],
    )


def test_create_run_starts_pending_with_pending_steps(tmp_data_root):
    r = _make_run()
    assert r["status"] == "pending"
    for s in r["steps"]:
        assert s["status"] == "pending"
        assert s["task_id"] is None


def test_finalise_all_completed_marks_run_completed(tmp_data_root):
    r = _make_run()
    rs = get_run_store()
    for s in r["steps"]:
        rs.update_step(r["run_id"], s["step_id"], {"status": "completed"})
    final = rs.finalise(r["run_id"])
    assert final["status"] == "completed"
    assert final["completed_at"] is not None


def test_finalise_partial_when_some_completed_some_failed(tmp_data_root):
    r = _make_run()
    rs = get_run_store()
    rs.update_step(r["run_id"], "s1", {"status": "completed"})
    rs.update_step(r["run_id"], "s2", {"status": "failed"})
    final = rs.finalise(r["run_id"])
    assert final["status"] == "partial"


def test_finalise_failed_when_no_completed(tmp_data_root):
    r = _make_run()
    rs = get_run_store()
    rs.update_step(r["run_id"], "s1", {"status": "failed"})
    rs.update_step(r["run_id"], "s2", {"status": "skipped"})
    final = rs.finalise(r["run_id"])
    assert final["status"] == "failed"


def test_finalise_running_when_step_still_running(tmp_data_root):
    r = _make_run()
    rs = get_run_store()
    rs.update_step(r["run_id"], "s1", {"status": "completed"})
    rs.update_step(r["run_id"], "s2", {"status": "running"})
    final = rs.finalise(r["run_id"])
    assert final["status"] == "running"
    assert final["completed_at"] is None    # not yet finished


def test_finalise_cancelled_when_all_cancelled(tmp_data_root):
    r = _make_run()
    rs = get_run_store()
    rs.update_step(r["run_id"], "s1", {"status": "cancelled"})
    rs.update_step(r["run_id"], "s2", {"status": "cancelled"})
    final = rs.finalise(r["run_id"])
    assert final["status"] == "cancelled"


def test_list_runs_filters_by_job(tmp_data_root):
    rs = get_run_store()
    r1 = rs.create_run(job_id="job-A", mode="single", source="user", steps=[
        {"step_id": "s1", "agent_id": "x"},
    ])
    r2 = rs.create_run(job_id="job-B", mode="single", source="user", steps=[
        {"step_id": "s1", "agent_id": "y"},
    ])
    listing = rs.list_runs(job_id="job-A")
    assert {x["run_id"] for x in listing} == {r1["run_id"]}
    listing_b = rs.list_runs(job_id="job-B")
    assert {x["run_id"] for x in listing_b} == {r2["run_id"]}


def test_update_step_only_modifies_target(tmp_data_root):
    r = _make_run()
    rs = get_run_store()
    rs.update_step(r["run_id"], "s1", {"status": "completed", "result_preview": "ok"})
    after = rs.get_run(r["run_id"])
    assert after["steps"][0]["status"] == "completed"
    assert after["steps"][0]["result_preview"] == "ok"
    assert after["steps"][1]["status"] == "pending"


def test_delete_run_removes_file(tmp_data_root):
    r = _make_run()
    rs = get_run_store()
    assert rs.delete_run(r["run_id"]) is True
    assert rs.get_run(r["run_id"]) is None
    assert rs.delete_run(r["run_id"]) is False    # gone now
