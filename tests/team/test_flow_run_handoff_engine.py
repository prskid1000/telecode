"""B2 (full handoff + files changed), B3 (engine/model/is_local per run and
per step, agent defaults), B6 (orphaned runs), B10 (cancel), B11 (usage
roll-up) — through the real run executor with the fake handler."""

from __future__ import annotations

import asyncio
import time

import pytest

from services.agent.agent_manager import get_agent_manager
from services.job.job_manager import get_job_manager
from services.run.executor import (
    HANDOFF_CAP,
    _cap_head_tail,
    _handoff_text,
    cancel_run,
    reconcile_orphaned_runs,
    start_run,
)
from services.run.run_store import get_run_store
from services.session import session_store

TERMINAL = ("completed", "failed", "partial", "cancelled", "interrupted")


def _wait(run_id, pred, deadline=10.0):
    rs = get_run_store()
    end = time.time() + deadline
    while time.time() < end:
        r = rs.get_run(run_id)
        if r and pred(r):
            return r
        time.sleep(0.05)
    return rs.get_run(run_id)


def _agent(**kw):
    am = get_agent_manager()
    a = am.create_agent("p0-bot", soul="s", **kw)
    return a["id"]


def _ws(sid="ws-p0"):
    if not session_store.exists(sid):
        session_store.create(session_id=sid, data={"name": sid})
    return sid


def _run(job, **kw):
    return asyncio.run(start_run(job=job, source="user", **kw))


# ── B2 ──────────────────────────────────────────────────────────────────────

def test_b2_cap_head_tail_keeps_both_ends():
    text = "HEAD" + ("x" * 50_000) + "TAIL"
    out = _cap_head_tail(text, 16 * 1024)
    assert len(out) <= 16 * 1024
    assert out.startswith("HEAD") and out.endswith("TAIL")
    assert "characters omitted" in out
    assert _cap_head_tail("short", 100) == "short"


def test_b2_step_handoff_uses_full_text_not_400_preview(tmp_data_root, fake_task_queue):
    long_reply = "BEGIN " + ("word " * 2000) + " END-MARKER"          # ~10 KB
    fake_task_queue.set_result(long_reply)
    aid, ws = _agent(), _ws()
    j = get_job_manager().create_job({
        "title": "handoff", "workspace_id": ws, "task_description": "go",
        "pipeline": {"mode": "sequential", "steps": [
            {"agent_id": aid, "name": "one"},
            {"agent_id": aid, "name": "two", "depends_on_text": True},
        ]},
    })
    final = _wait(_run(j)["run_id"], lambda r: r["status"] in TERMINAL)
    assert final["status"] == "completed"
    second = fake_task_queue.last_calls[1]["prompt"]
    assert "END-MARKER" in second and "BEGIN" in second            # whole reply, not 400 chars
    step1 = final["steps"][0]
    assert len(step1["result_preview"]) == 400
    assert step1["result_text"] == long_reply.strip()
    assert len(_handoff_text({"result": "y" * 100_000})) <= HANDOFF_CAP


def test_b2_handoff_lists_files_changed_by_step(tmp_data_root, fake_task_queue, monkeypatch):
    """The first step writes a file in the workspace; the second step's prompt names it."""
    aid, ws = _agent(), _ws("ws-files")
    from services.task.task_manager import get_task_queue
    from services.task.task_utils import get_session_folder
    q = get_task_queue()
    orig = q.task_handlers["CLAUDE_CODE"]
    calls = []

    def writer(prompt=None, **kw):
        calls.append(prompt)
        if len(calls) == 1:
            (get_session_folder() / "out" / "summary.md").parent.mkdir(parents=True, exist_ok=True)
            (get_session_folder() / "out" / "summary.md").write_text("done", encoding="utf-8")
        return {"result": "wrote summary"}

    q.register_handler("CLAUDE_CODE", writer)
    try:
        j = get_job_manager().create_job({
            "title": "files", "workspace_id": ws, "task_description": "go",
            "pipeline": {"mode": "sequential", "steps": [
                {"agent_id": aid, "name": "w"},
                {"agent_id": aid, "name": "r", "depends_on_text": True},
            ]},
        })
        final = _wait(_run(j)["run_id"], lambda r: r["status"] in TERMINAL)
    finally:
        q.register_handler("CLAUDE_CODE", orig)
    assert [{k: f[k] for k in ("path", "change")} for f in final["steps"][0]["files_changed"]] == \
        [{"path": "out/summary.md", "change": "added"}]
    assert "<files_changed>" in calls[1] and "added: out/summary.md" in calls[1]


# ── B3 ──────────────────────────────────────────────────────────────────────

def test_b3_run_override_engine_model_local(tmp_data_root, fake_task_queue):
    from services.task.task_manager import get_task_queue
    aid, ws = _agent(), _ws()
    j = get_job_manager().create_job({
        "title": "ovr", "workspace_id": ws,
        "pipeline": {"mode": "single", "steps": [{"agent_id": aid}]},
    })
    run = _run(j, engine="codex", model="gpt-5.5", is_local=False)
    final = _wait(run["run_id"], lambda r: r["status"] in TERMINAL)
    step = final["steps"][0]
    assert (step["engine"], step["model"], step["is_local"]) == ("codex", "gpt-5.5", False)
    assert get_task_queue().get_task(step["task_id"]).task_type == "CODEX"
    assert fake_task_queue.last_calls[0]["model"] == "gpt-5.5"
    assert final["overrides"]["engine"] == "codex"


def test_b3_step_override_beats_run_and_blank_inherits(tmp_data_root, fake_task_queue):
    from services.task.task_manager import get_task_queue
    aid, ws = _agent(engine="antigravity", model="agent-model"), _ws()
    j = get_job_manager().create_job({
        "title": "steps", "workspace_id": ws,
        "pipeline": {"mode": "sequential", "steps": [
            {"agent_id": aid, "name": "pinned", "engine": "codex", "model": "m-step", "is_local": True},
            {"agent_id": aid, "name": "inherit", "engine": "", "model": ""},
        ]},
    })
    stored = get_job_manager().get_job(j["id"])["pipeline"]["steps"]
    assert stored[0]["engine"] == "codex" and stored[0]["is_local"] is True
    assert stored[1]["engine"] == "" and stored[1]["is_local"] is None      # blank = inherit

    final = _wait(_run(j, engine="claude_code")["run_id"], lambda r: r["status"] in TERMINAL)
    s0, s1 = final["steps"]
    assert (s0["engine"], s0["model"], s0["is_local"]) == ("codex", "m-step", True)
    # run override (claude_code) beats the agent's default engine; the model
    # still falls through to the agent default.
    assert (s1["engine"], s1["model"]) == ("claude_code", "agent-model")
    q = get_task_queue()
    assert q.get_task(s0["task_id"]).task_type == "CODEX"
    assert q.get_task(s1["task_id"]).task_type == "CLAUDE_CODE"


def test_b3_agent_default_engine_used_without_overrides(tmp_data_root, fake_task_queue):
    from services.task.task_manager import get_task_queue
    aid, ws = _agent(engine="codex"), _ws()
    j = get_job_manager().create_job({
        "title": "agentdef", "workspace_id": ws,
        "pipeline": {"mode": "single", "steps": [{"agent_id": aid}]},
    })
    final = _wait(_run(j)["run_id"], lambda r: r["status"] in TERMINAL)
    assert final["steps"][0]["engine"] == "codex"
    assert get_task_queue().get_task(final["steps"][0]["task_id"]).task_type == "CODEX"
    assert "model" not in fake_task_queue.last_calls[0]                  # blank → not passed


def test_b3_invalid_engines_rejected(tmp_data_root):
    with pytest.raises(ValueError):
        get_job_manager().create_job({"title": "x", "pipeline": {
            "mode": "single", "steps": [{"agent_id": "a", "engine": "gpt5"}]}})
    with pytest.raises(ValueError):
        get_agent_manager().create_agent("bad", engine="nope")


# ── B6 / B10 / B11 ──────────────────────────────────────────────────────────

def test_b11_run_usage_rollup(tmp_data_root, fake_task_queue):
    from services.task.task_manager import get_task_queue
    aid, ws = _agent(), _ws()
    q = get_task_queue()
    orig = q.task_handlers["CLAUDE_CODE"]
    q.register_handler("CLAUDE_CODE", lambda **kw: {
        "result": "ok", "cost_usd": 0.25, "num_turns": 2, "duration_ms": 10,
        "tokens": {"input": 10, "output": 5, "cache_read": 90, "cache_write": 1,
                   "total_input_incl_cache": 101}})
    q.register_handler("CODEX", lambda **kw: {
        "result": "ok", "cost_usd": None, "num_turns": 1, "duration_ms": 5,
        "tokens": {"input": 3, "output": 4, "cache_read": 7, "cache_write": 0,
                   "total_input_incl_cache": 10}})
    try:
        j = get_job_manager().create_job({
            "title": "usage", "workspace_id": ws,
            "pipeline": {"mode": "sequential", "steps": [
                {"agent_id": aid}, {"agent_id": aid, "engine": "codex"}]},
        })
        final = _wait(_run(j)["run_id"], lambda r: r["status"] in TERMINAL)
    finally:
        q.register_handler("CLAUDE_CODE", orig)
        q.register_handler("CODEX", orig)
    assert final["steps"][0]["usage"]["cost_usd"] == 0.25
    assert final["steps"][1]["usage"]["cost_usd"] is None
    u = final["usage"]
    assert u["input_tokens"] == 111 and u["output_tokens"] == 9 and u["num_turns"] == 3
    assert u["cost_usd"] == 0.25 and u["cost_complete"] is False


def test_b6_orphaned_run_marked_interrupted_and_cancel_works(tmp_data_root):
    rs = get_run_store()
    run = rs.create_run(job_id="j", mode="sequential", source="user",
                        steps=[{"agent_id": "a"}, {"agent_id": "a"}])
    rid = run["run_id"]
    rs.update_run(rid, {"status": "running"})
    rs.update_step(rid, run["steps"][0]["step_id"], {"status": "running", "task_id": "gone-task"})

    # cancel on an orphan (no driver) must finish it, not leave it "running"
    assert cancel_run(rid) is True
    r = rs.get_run(rid)
    assert r["status"] == "cancelled"
    assert r["steps"][0]["status"] == "cancelled" and r["steps"][0]["completed_at"]
    assert r["steps"][1]["status"] == "skipped"
    assert cancel_run(rid) is False                                      # already finished

    run2 = rs.create_run(job_id="j", mode="single", source="user", steps=[{"agent_id": "a"}])
    rs.update_run(run2["run_id"], {"status": "running"})
    rs.update_step(run2["run_id"], run2["steps"][0]["step_id"], {"status": "running", "task_id": "dead"})
    assert reconcile_orphaned_runs() == 1
    r2 = rs.get_run(run2["run_id"])
    assert r2["status"] == "interrupted" and r2["steps"][0]["status"] == "interrupted"


def test_b6_reconcile_leaves_live_runs_alone(tmp_data_root, fake_task_queue):
    fake_task_queue.block()
    aid, ws = _agent(), _ws()
    j = get_job_manager().create_job({
        "title": "live", "workspace_id": ws,
        "pipeline": {"mode": "single", "steps": [{"agent_id": aid}]},
    })
    run = _run(j)
    _wait(run["run_id"], lambda r: r["steps"][0]["status"] == "running")
    assert reconcile_orphaned_runs() == 0
    assert get_run_store().get_run(run["run_id"])["steps"][0]["status"] == "running"
    fake_task_queue.release()
    _wait(run["run_id"], lambda r: r["status"] in TERMINAL)


def test_b10_cancel_run_does_not_overwrite_finished_task(tmp_data_root, fake_task_queue):
    from services.task.task_manager import TaskStatus, get_task_queue
    aid, ws = _agent(), _ws()
    j = get_job_manager().create_job({
        "title": "fin", "workspace_id": ws,
        "pipeline": {"mode": "single", "steps": [{"agent_id": aid}]},
    })
    final = _wait(_run(j)["run_id"], lambda r: r["status"] in TERMINAL)
    tid = final["steps"][0]["task_id"]
    assert cancel_run(final["run_id"]) is False
    assert get_task_queue().get_task(tid).status == TaskStatus.COMPLETED
