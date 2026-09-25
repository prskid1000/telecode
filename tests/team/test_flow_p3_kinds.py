"""P3 step kinds end to end — real task handlers, run executor and Engine
Runner; only the CLI is a fake (fixtures/engine/fake_agent_cli.py):
map fan-out → reduce, loop (command / grader / schema checks), gate
(approve with edited text over REST, reject, survives a restart, cancel)."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services import approvals
from services.agent.agent_manager import get_agent_manager
from services.engine.adapters import get_adapter
from services.job.job_manager import get_job_manager
from services.run import executor
from services.run.run_store import get_run_store
from services.session import session_store
from services.task.task_manager import get_task_queue

FAKE = Path(__file__).parent / "fixtures" / "engine" / "fake_agent_cli.py"
TERMINAL = ("completed", "failed", "partial", "cancelled", "interrupted", "budget_exceeded", "rejected")
HO = lambda **kw: json.dumps({"status": "done", "summary": "ok", "decisions": [], "artifacts": [],  # noqa: E731
                              "open_questions": [], "next_steps": [], "items": [], "verdict": "pass", **kw})


@pytest.fixture
def env(tmp_data_root, monkeypatch):
    import config
    from services.task.task_registry import register_default_tasks
    q = get_task_queue()
    saved = dict(q.task_handlers)
    register_default_tasks()
    monkeypatch.setattr(config, "tasks_retry_backoff_seconds", lambda: 0.05)
    monkeypatch.setattr(config, "tasks_kill_grace_seconds", lambda: 0.5)
    calls = {"argv": tmp_data_root / "argv.jsonl", "prompts": tmp_data_root / "prompts.jsonl"}
    yield {"root": tmp_data_root, "calls": calls, "monkeypatch": monkeypatch}
    q.task_handlers.clear()
    q.task_handlers.update(saved)


def fake(env, engine, script):
    ad = get_adapter(engine)
    orig = type(ad).build

    def build(req):
        launch = orig(ad, req)
        launch.argv = [sys.executable, str(FAKE), "--argv-out", str(env["calls"]["argv"]),
                       "--prompt-out", str(env["calls"]["prompts"]),
                       *[str(a) for a in script(req)], "--", *launch.argv[1:]]
        return launch
    env["monkeypatch"].setattr(ad, "build", build)


def prompts(env):
    p = env["calls"]["prompts"]
    return [json.loads(l).replace("\r\n", "\n") for l in p.read_text(encoding="utf-8").splitlines()] \
        if p.exists() else []


def argvs(env):
    p = env["calls"]["argv"]
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def wait_run(run_id, pred=None, deadline=40):
    pred = pred or (lambda r: r["status"] in TERMINAL and not executor.is_active(r["run_id"]))
    end = time.time() + deadline
    rs = get_run_store()
    while time.time() < end:
        r = rs.get_run(run_id)
        if r and pred(r):
            return r
        time.sleep(0.05)
    return rs.get_run(run_id)


def setup_job(steps, ws="ws-p3", mode="custom", **job):
    a = get_agent_manager().create_agent("p3-bot", soul="s")
    if not session_store.exists(ws):
        session_store.create(session_id=ws, data={"name": ws})
    for i, s in enumerate(steps):
        s.setdefault("agent_id", a["id"] if s.get("kind") != "gate" else None)
        s.setdefault("phase", i)
    j = get_job_manager().create_job({"title": "p3", "workspace_id": ws, "task_description": "go",
                                      "pipeline": {"mode": mode, "steps": steps}, **job})
    return j, session_store._session_dir(ws)


def start(j, **kw):
    return asyncio.run(executor.start_run(job=j, source="user", **kw))


def _app():
    from proxy import api_approvals, api_runs
    app = web.Application()
    api_runs.register_routes(app)
    api_approvals.register_routes(app)
    return app


async def _with_client(fn):
    c = TestClient(TestServer(_app()))
    await c.start_server()
    try:
        return await fn(c)
    finally:
        await c.close()


# ── normalisation ───────────────────────────────────────────────────────────

def test_kind_steps_must_own_their_phase(tmp_data_root):
    m = get_job_manager()
    with pytest.raises(ValueError, match="alone in its phase"):
        m.create_job({"title": "x", "pipeline": {"mode": "parallel", "steps": [
            {"agent_id": "a"}, {"kind": "gate", "gate": {"title": "ok?"}}]}})
    with pytest.raises(ValueError, match="needs a previous phase"):
        m.create_job({"title": "x", "pipeline": {"mode": "single", "steps": [{"agent_id": "a", "kind": "map"}]}})
    with pytest.raises(ValueError, match="command is required"):
        m.create_job({"title": "x", "pipeline": {"mode": "single", "steps": [
            {"agent_id": "a", "kind": "loop", "loop": {"check": {"type": "command"}}}]}})
    j = m.create_job({"title": "x", "pipeline": {"mode": "sequential", "steps": [
        {"agent_id": "a"}, {"kind": "gate", "gate": {"title": "Ship?", "instructions": "look"}},
        {"agent_id": "a", "kind": "map", "map": {"items_from": "items", "max_parallel": 2}},
        {"agent_id": "a", "kind": "reduce"},
        {"agent_id": "a", "kind": "loop", "loop": {"max_iterations": 2,
                                                  "check": {"type": "schema", "schema": '{"type":"object"}'}}}]}})
    kinds = [s["kind"] for s in j["pipeline"]["steps"]]
    assert kinds == ["agent", "gate", "map", "reduce", "loop"]
    assert j["pipeline"]["steps"][1]["agent_id"] is None
    assert j["pipeline"]["steps"][2]["map"] == {"items_from": "items", "max_parallel": 2, "max_items": 20,
                                                "worker_session": "ephemeral"}
    assert j["pipeline"]["steps"][4]["loop"]["check"]["schema"] == {"type": "object"}


# ── map → reduce ────────────────────────────────────────────────────────────

def test_map_fans_out_over_planner_items_then_reduce_merges(env):
    def script(req):
        p = req.prompt
        if "PLAN" in p and "<map_item" not in p and "reduce_instructions" not in p:
            assert "<fanout_instructions>" in p
            return ["--session", "S-PLAN", "--handoff", HO(summary="planned", items=["alpha", "beta"])]
        if "<map_item" in p:
            item = "alpha" if "alpha\n</map_item>" in p else "beta"
            return ["--write", f"{item}.txt={item} done", "--handoff",
                    HO(summary=f"did {item}", artifacts=[{"path": f"{item}.txt", "kind": "doc", "description": item}],
                       next_steps=[f"check {item}"])]
        assert "<reduce_instructions>" in p
        return ["--handoff", HO(summary="merged alpha+beta")]
    fake(env, "claude_code", script)
    j, wd = setup_job([{"name": "Planner", "prompt_override": "PLAN"},
                       {"name": "Worker", "kind": "map", "map": {"items_from": "items", "max_parallel": 2},
                        "prompt_override": "DO", "budget": {"max_usd": 1.0}},
                       {"name": "Reducer", "kind": "reduce", "prompt_override": "MERGE"}])
    r = wait_run(start(j)["run_id"])
    assert r["status"] == "completed", r
    plan, mp, red = r["steps"]
    assert [w["item"] for w in mp["workers"]] == ["alpha", "beta"]
    assert all(w["status"] == "completed" for w in mp["workers"])
    assert mp["budget"]["per"] == "worker" and mp["budget"]["max_usd"] == pytest.approx(0.5)
    arts = {a["path"] for a in mp["handoff"]["artifacts"]}
    assert arts == {"w1/alpha.txt", "w2/beta.txt"}
    stored = next(a for a in mp["handoff"]["artifacts"] if a["path"] == "w1/alpha.txt")["stored_path"]
    assert Path(stored).read_text() == "alpha done"
    assert not (wd / "alpha.txt").exists()                                   # workers ran in copies
    rp = next(p for p in prompts(env) if "<reduce_instructions>" in p)
    assert 'from="Worker #1"' in rp and 'from="Worker #2"' in rp and "did alpha" in rp and "did beta" in rp
    assert red["handoff"]["summary"] == "merged alpha+beta" and red["session_policy"] == "fresh"
    # worker diff route
    d = executor.step_diff(r["run_id"], mp["step_id"], worker=1)
    assert [f["path"] for f in d["files"]] == ["alpha.txt"]

    async def art(c):
        resp = await c.get(f"/api/runs/{r['run_id']}/steps/{mp['step_id']}/artifacts/w2/beta.txt")
        return resp.status, await resp.read()
    assert asyncio.run(_with_client(art)) == (200, b"beta done")


def test_map_with_no_items_completes_with_a_note(env):
    fake(env, "claude_code", lambda req: ["--handoff", HO(summary="nothing")])
    j, _ = setup_job([{"prompt_override": "PLAN"}, {"kind": "map", "map": {"items_from": "next_steps"}}])
    r = wait_run(start(j)["run_id"])
    assert r["status"] == "completed" and r["steps"][1]["workers"] == []
    assert "nothing to fan out" in r["steps"][1]["handoff"]["summary"]


def test_map_retry_reruns_only_failed_workers(env):
    counter = env["root"] / "beta.cnt"

    def script(req):
        p = req.prompt
        if "<map_item" not in p:
            return ["--handoff", HO(next_steps=["alpha", "beta"])]
        if "beta\n</map_item>" in p:
            return ["--fail-times", "1", "--counter", str(counter), "--handoff", HO(summary="beta ok")]
        return ["--handoff", HO(summary="alpha ok")]
    fake(env, "claude_code", script)
    j, _ = setup_job([{"prompt_override": "PLAN"}, {"kind": "map", "prompt_override": "W"}])
    r = wait_run(start(j)["run_id"])
    assert r["status"] == "partial" and r["steps"][1]["status"] == "failed"
    assert [w["status"] for w in r["steps"][1]["workers"]] == ["completed", "failed"]
    n_before = len(prompts(env))
    executor.retry_step(r["run_id"], r["steps"][1]["step_id"], "retry")
    r = wait_run(r["run_id"])
    assert r["status"] == "completed", r
    new = prompts(env)[n_before:]
    assert len(new) == 1 and "beta\n</map_item>" in new[0]                  # alpha was not re-run


# ── loop ───────────────────────────────────────────────────────────────────

def test_loop_command_check_feeds_findings_back_until_it_passes(env):
    def script(req):
        if "did not pass" in req.prompt:
            return ["--session", "S-BODY", "--write", "fixed.txt=1", "--handoff", HO(summary="fixed it")]
        return ["--session", "S-BODY", "--handoff", HO(summary="first try")]
    fake(env, "claude_code", script)
    cmd = f'"{sys.executable}" -c "import os,sys; print(\'missing fixed.txt\'); sys.exit(0 if os.path.exists(\'fixed.txt\') else 3)"'
    j, wd = setup_job([{"name": "Body", "kind": "loop", "prompt_override": "BUILD",
                        "loop": {"max_iterations": 3, "check": {"type": "command", "command": cmd}}}], mode="single")
    r = wait_run(start(j)["run_id"])
    assert r["status"] == "completed", r
    s = r["steps"][0]
    its = s["iterations"]
    assert [i["check"]["passed"] for i in its] == [False, True]
    assert its[0]["check"]["exit_code"] == 3 and "missing fixed.txt" in its[0]["check"]["findings"]
    a = argvs(env)
    assert a[1][a[1].index("--resume") + 1] == "S-BODY"                     # the body resumed its conversation
    assert "exited with 3" in prompts(env)[1]
    assert s["handoff"]["loop"] == {"iterations": 2, "passed": True, "check": "command"}
    assert [x["mode"] for x in s["attempts"]] == ["run", "iteration"]


def test_loop_gives_up_after_max_iterations(env):
    fake(env, "claude_code", lambda req: ["--session", "S", "--handoff", HO()])
    j, _ = setup_job([{"kind": "loop", "prompt_override": "X",
                       "loop": {"max_iterations": 2, "check": {"type": "command", "command": "exit 1"}}}],
                     mode="single")
    r = wait_run(start(j)["run_id"])
    s = r["steps"][0]
    assert r["status"] == "failed" and len(s["iterations"]) == 2
    assert "did not pass after 2 iteration" in s["error"]


def test_loop_grader_runs_in_a_fresh_session_with_the_rubric(env):
    n = {"grades": 0}

    def script(req):
        p = req.prompt
        if "<grading_task>" in p:
            n["grades"] += 1
            assert "<rubric>\nmust mention tests\n</rubric>" in p
            v = {"verdict": "fail", "summary": "no tests", "findings": ["mention tests"]} if n["grades"] == 1 \
                else {"verdict": "pass", "summary": "good", "findings": []}
            return ["--session", f"G{n['grades']}", "--handoff", json.dumps(v)]
        return ["--session", "S-WORK", "--handoff", HO(summary="wrote it" + (" with tests" if "mention tests" in p else ""))]
    fake(env, "claude_code", script)
    j, _ = setup_job([{"kind": "loop", "prompt_override": "WRITE",
                       "loop": {"max_iterations": 3, "check": {"type": "grader", "rubric": "must mention tests"}}}],
                     mode="single")
    r = wait_run(start(j)["run_id"])
    assert r["status"] == "completed", r
    its = r["steps"][0]["iterations"]
    assert [i["check"]["verdict"] for i in its] == ["fail", "pass"]
    assert "- mention tests" in its[0]["check"]["findings"]
    grader_argv = [a for a, p in zip(argvs(env), prompts(env)) if "<grading_task>" in p]
    assert len(grader_argv) == 2 and all("--resume" not in a for a in grader_argv)
    assert all("verdict" in json.loads(a[a.index("--json-schema") + 1])["properties"] for a in grader_argv)
    body_second = [p for p in prompts(env) if "<grading_task>" not in p][1]
    assert "mention tests" in body_second


def test_loop_schema_check_on_a_workspace_json_file(env):
    def script(req):
        if "did not pass" in req.prompt:
            return ["--write", 'out.json={"score": 9}', "--handoff", HO()]
        return ["--write", 'out.json={"score": "high"}', "--handoff", HO()]
    fake(env, "claude_code", script)
    schema = {"type": "object", "required": ["score"], "properties": {"score": {"type": "integer"}}}
    j, _ = setup_job([{"kind": "loop", "prompt_override": "SCORE",
                       "loop": {"check": {"type": "schema", "schema": schema, "path": "out.json"}}}], mode="single")
    r = wait_run(start(j)["run_id"])
    its = r["steps"][0]["iterations"]
    assert r["status"] == "completed" and "$.score: expected integer" in its[0]["check"]["findings"]


# ── gate ───────────────────────────────────────────────────────────────────

def test_gate_waits_then_rest_approve_with_edited_text_feeds_next_step(env):
    fake(env, "claude_code", lambda req: ["--handoff", HO(summary="draft ready")] if "DRAFT" in req.prompt
         else ["--handoff", HO(summary="shipped")])
    j, _ = setup_job([{"name": "Drafter", "prompt_override": "DRAFT"},
                      {"kind": "gate", "gate": {"title": "Ship it?", "instructions": "Read the draft first."}},
                      {"name": "Shipper", "prompt_override": "SHIP", "depends_on_text": True}])
    r = wait_run(start(j)["run_id"], lambda r: r["status"] == "awaiting_input" and not executor.is_active(r["run_id"]))
    assert r["status"] == "awaiting_input", r
    gate = r["steps"][1]
    assert gate["status"] == "awaiting_input" and r["steps"][2]["status"] == "pending"
    ap = approvals.get(gate["approval_id"])
    assert ap["status"] == "pending" and ap["title"] == "Ship it?" and "draft ready" in ap["body"]
    assert "Read the draft first." in ap["body"]

    async def go(c):
        lst = await (await c.get("/api/approvals")).json()
        bad = await c.post(f"/api/approvals/{ap['id']}/approve", json={"edited_text": 5})
        ok = await c.post(f"/api/approvals/{ap['id']}/approve",
                          json={"edited_text": "Ship only the EU build.", "note": "careful"})
        again = await c.post(f"/api/approvals/{ap['id']}/reject", json={})
        return lst, bad.status, ok.status, again.status, await again.json()
    lst, bad, ok, again, again_body = asyncio.run(_with_client(go))
    assert lst["pending_count"] == 1 and lst["approvals"][0]["id"] == ap["id"]
    assert bad == 400 and ok == 200 and again == 409 and again_body["approval"]["status"] == "approved"
    r = wait_run(r["run_id"])
    assert r["status"] == "completed", r
    assert r["steps"][1]["handoff"]["summary"] == "Ship only the EU build."
    ship_prompt = [p for p in prompts(env) if "SHIP" in p][0]
    assert "Ship only the EU build." in ship_prompt and "draft ready" in ship_prompt   # gate note + passthrough


def test_gate_reject_ends_run_rejected(env):
    fake(env, "claude_code", lambda req: ["--handoff", HO()])
    j, _ = setup_job([{"prompt_override": "A"}, {"kind": "gate"}, {"prompt_override": "B"}])
    r = wait_run(start(j)["run_id"], lambda r: r["status"] == "awaiting_input" and not executor.is_active(r["run_id"]))
    approvals.decide(r["steps"][1]["approval_id"], "reject", by="tester", note="not now")
    r = wait_run(r["run_id"])
    assert r["status"] == "rejected" and [s["status"] for s in r["steps"]] == ["completed", "rejected", "skipped"]
    assert "not now" in r["steps"][1]["error"]
    # a rejected gate can be asked again
    executor.retry_step(r["run_id"], r["steps"][1]["step_id"], "retry")
    r = wait_run(r["run_id"], lambda r: r["status"] == "awaiting_input" and not executor.is_active(r["run_id"]))
    assert approvals.get(r["steps"][1]["approval_id"])["status"] == "pending"


def test_gate_survives_restart_and_cancel_cancels_the_approval(env):
    fake(env, "claude_code", lambda req: ["--handoff", HO()])
    j, _ = setup_job([{"kind": "gate", "gate": {"title": "go?"}}, {"prompt_override": "AFTER"}])
    r = wait_run(start(j)["run_id"], lambda r: r["status"] == "awaiting_input" and not executor.is_active(r["run_id"]))
    assert executor.reconcile_orphaned_runs() == 0                             # "restart": still waiting
    assert get_run_store().get_run(r["run_id"])["status"] == "awaiting_input"
    with pytest.raises(executor.RunBusy):
        executor.retry_step(r["run_id"], r["steps"][1]["step_id"], "retry_clean")
    aid = r["steps"][0]["approval_id"]
    approvals.decide(aid, "approve", by="later")                              # resumes from the next phase
    r = wait_run(r["run_id"])
    assert r["status"] == "completed" and "AFTER" in prompts(env)[0]
    # a second run: cancel while waiting
    r2 = wait_run(start(j)["run_id"], lambda r: r["status"] == "awaiting_input" and not executor.is_active(r["run_id"]))
    assert executor.cancel_run(r2["run_id"])
    r2 = get_run_store().get_run(r2["run_id"])
    assert r2["status"] == "cancelled" and approvals.get(r2["steps"][0]["approval_id"])["status"] == "cancelled"
    with pytest.raises(approvals.AlreadyDecided):
        approvals.decide(r2["steps"][0]["approval_id"], "approve")
