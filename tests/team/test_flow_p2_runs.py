"""P2 end to end with the real task handlers, run executor and Engine Runner;
only the CLI is a fake (tests/team/fixtures/engine/fake_agent_cli.py):
structured handoffs + artifacts, snapshots/diff/revert (+ REST), retries
(resume / clean / auto), budgets, fork policy, parallel ephemeral artifacts,
agy's handoff file and session rotation."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services.agent.agent_manager import get_agent_manager
from services.engine.adapters import get_adapter
from services.job.job_manager import get_job_manager
from services.run import executor
from services.run.run_store import get_run_store
from services.session import session_store
from services.task.task_manager import TaskStatus, get_task_queue

FAKE = Path(__file__).parent / "fixtures" / "engine" / "fake_agent_cli.py"
TERMINAL = ("completed", "failed", "partial", "cancelled", "interrupted", "budget_exceeded")
HO = lambda **kw: json.dumps({"status": "done", "summary": "ok", "decisions": [], "artifacts": [],  # noqa: E731
                              "open_questions": [], "next_steps": [], "verdict": "pass", **kw})


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
    """script(req) -> extra fake-cli args, chosen per call from the request."""
    ad = get_adapter(engine)
    orig = type(ad).build

    def build(req):
        launch = orig(ad, req)
        launch.argv = [sys.executable, str(FAKE), "--argv-out", str(env["calls"]["argv"]),
                       "--prompt-out", str(env["calls"]["prompts"]),
                       *[str(a) for a in script(req)], "--", *launch.argv[1:]]
        return launch
    env["monkeypatch"].setattr(ad, "build", build)


def argvs(env):
    p = env["calls"]["argv"]
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def prompts(env):
    p = env["calls"]["prompts"]
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def wait_run(run_id, pred=lambda r: r["status"] in TERMINAL and not executor.is_active(r["run_id"]), deadline=30):
    end = time.time() + deadline
    rs = get_run_store()
    while time.time() < end:
        r = rs.get_run(run_id)
        if r and pred(r):
            return r
        time.sleep(0.05)
    return rs.get_run(run_id)


def setup_job(steps, ws="ws-p2", **job):
    a = get_agent_manager().create_agent("p2-bot", soul="s")
    if not session_store.exists(ws):
        session_store.create(session_id=ws, data={"name": ws})
    for s in steps:
        s.setdefault("agent_id", a["id"])
    j = get_job_manager().create_job({"title": "p2", "workspace_id": ws, "task_description": "go",
                                      "pipeline": {"mode": job.pop("mode", "sequential"), "steps": steps}, **job})
    return j, session_store._session_dir(ws)


def start(j, **kw):
    return asyncio.run(executor.start_run(job=j, source="user", **kw))


def _app():
    from proxy import api_runs, api_sessions
    app = web.Application()
    api_runs.register_routes(app)
    api_sessions.register_routes(app)
    return app


async def _with_client(fn):
    c = TestClient(TestServer(_app()))
    await c.start_server()
    try:
        return await fn(c)
    finally:
        await c.close()


# ── handoffs + artifacts + snapshots ────────────────────────────────────────

def test_two_step_handoff_artifacts_diff_revert_and_rest(env):
    def script(req):
        if "STEP-ONE" in req.prompt:
            return ["--session", "S-ONE", "--write", "out/plan.md=the plan\n",
                    "--handoff", HO(summary="planned it", decisions=["keep it short"],
                                    artifacts=[{"path": "out/plan.md", "kind": "doc", "description": "the plan"}])]
        return ["--session", "S-TWO", "--handoff", HO(summary="read the plan")]
    fake(env, "claude_code", script)
    j, wd = setup_job([{"name": "Planner", "prompt_override": "STEP-ONE"},
                       {"name": "Reader", "prompt_override": "STEP-TWO", "depends_on_text": True}])
    r = wait_run(start(j)["run_id"])
    assert r["status"] == "completed", r
    s1, s2 = r["steps"]
    assert s1["handoff"]["status"] == "done" and s1["handoff"]["decisions"] == ["keep it short"]
    art = s1["handoff"]["artifacts"][0]
    assert Path(art["stored_path"]).read_text() == "the plan\n"
    assert s1["engine_session_id"] == "S-ONE" and s1["session_policy"] == "resume"
    p1, p2 = prompts(env)
    assert "<handoff_instructions>" in p1 and "<handoff from" not in p1
    assert '<handoff from="Planner" status="done" verdict="pass">' in p2
    assert art["stored_path"] in p2 and "keep it short" in p2 and "added: out/plan.md" in p2
    a1 = argvs(env)[0]
    assert "--json-schema" in a1 and json.loads(a1[a1.index("--json-schema") + 1])["required"]
    assert "--add-dir" in argvs(env)[1]                                        # step 2 can read step 1's artifacts
    # handoff stored in its own run_steps column too
    from services.db.core import connect
    row = connect().execute("SELECT handoff FROM run_steps WHERE step_id=?", (s1["step_id"],)).fetchone()
    assert json.loads(row["handoff"])["summary"] == "planned it"
    assert [f["path"] for f in s1["files_changed"]] == ["out/plan.md"]

    async def go(c):
        d = await (await c.get(f"/api/runs/{r['run_id']}/steps/{s1['step_id']}/diff")).json()
        one = await (await c.get(f"/api/runs/{r['run_id']}/steps/{s1['step_id']}/diff",
                                 params={"path": "out/plan.md"})).json()
        art_r = await c.get(f"/api/runs/{r['run_id']}/steps/{s1['step_id']}/artifacts/out/plan.md")
        bad = await c.get(f"/api/runs/{r['run_id']}/steps/{s1['step_id']}/artifacts/..%2F..%2Fx")
        snaps = await (await c.get("/api/sessions/ws-p2/snapshots")).json()
        rev = await c.post(f"/api/runs/{r['run_id']}/steps/{s1['step_id']}/revert", json={})
        return d, one, art_r.status, await art_r.read(), bad.status, snaps, rev.status, await rev.json()
    d, one, art_status, art_body, bad_status, snaps, rev_status, rev = asyncio.run(_with_client(go))
    assert [f["path"] for f in d["files"]] == ["out/plan.md"] and "+the plan" in d["diff"]
    assert one["path"] == "out/plan.md" and "+the plan" in one["diff"]
    assert art_status == 200 and art_body == b"the plan\n" and bad_status in (400, 404)
    kinds = [s["kind"] for s in snaps["snapshots"]]
    assert kinds.count("before") == 2 and kinds.count("after") == 2
    assert rev_status == 200 and rev["restored_to"] == s1["attempts"][0]["snapshot_before"]
    assert not (wd / "out" / "plan.md").exists()
    assert get_run_store().get_run(r["run_id"])["steps"][0]["reverted"]["attempt"] == 1

    async def undo(c):
        snaps = (await (await c.get("/api/sessions/ws-p2/snapshots")).json())["snapshots"]
        safety = next(s for s in snaps if s["kind"] == "safety")
        diff = await (await c.get(f"/api/sessions/ws-p2/snapshots/{snaps[0]['sha']}/diff")).json()
        res = await c.post(f"/api/sessions/ws-p2/snapshots/{safety['sha']}/restore", json={})
        return diff, res.status
    diff, st = asyncio.run(_with_client(undo))
    assert diff["success"] and st == 200 and (wd / "out" / "plan.md").read_text() == "the plan\n"


def test_step_without_structured_output_gets_derived_handoff(env):
    fake(env, "claude_code", lambda req: ["--text", "plain reply only"])
    j, _ = setup_job([{"prompt_override": "A"}], mode="single")
    r = wait_run(start(j)["run_id"])
    ho = r["steps"][0]["handoff"]
    assert r["status"] == "completed" and ho["derived"] and ho["summary"] == "plain reply only"


# ── retries ──────────────────────────────────────────────────────────────

def test_retry_resumes_same_session_then_runs_downstream(env):
    counter = env["root"] / "fail.cnt"

    def script(req):
        if "STEP-ONE" in req.prompt or "Continue from where you stopped" in req.prompt:
            return ["--session", "S-RETRY", "--fail-times", "1", "--counter", str(counter), "--handoff", HO()]
        return ["--handoff", HO(summary="two")]
    fake(env, "claude_code", script)
    j, _ = setup_job([{"prompt_override": "STEP-ONE"}, {"prompt_override": "STEP-TWO", "depends_on_text": True}])
    r = wait_run(start(j)["run_id"])
    assert r["status"] == "failed" and [s["status"] for s in r["steps"]] == ["failed", "skipped"]
    assert r["steps"][0]["attempts"][0]["engine_session_id"] == "S-RETRY"
    with pytest.raises(ValueError):
        executor.retry_step(r["run_id"], r["steps"][1]["step_id"], "retry")      # skipped: not retryable
    executor.retry_step(r["run_id"], r["steps"][0]["step_id"], "retry")
    r = wait_run(r["run_id"])
    assert r["status"] == "completed", r
    a = argvs(env)
    assert a[1][a[1].index("--resume") + 1] == "S-RETRY"
    assert "Continue from where you stopped" in prompts(env)[1]
    s1 = r["steps"][0]
    assert [x["mode"] for x in s1["attempts"]] == ["run", "retry"] and s1["attempts"][1]["status"] == "completed"
    assert r["steps"][1]["status"] == "completed"


def test_retry_clean_restores_pre_step_snapshot_and_starts_fresh(env):
    n = {"i": 0}

    def script(req):
        n["i"] += 1
        return ["--session", f"S{n['i']}", "--write", "junk.txt=1"] if n["i"] == 1 else ["--session", "S2"]
    fake(env, "claude_code", script)
    j, wd = setup_job([{"prompt_override": "A"}], mode="single")
    r = wait_run(start(j)["run_id"])
    assert (wd / "junk.txt").exists()
    async def go(c):
        resp = await c.post(f"/api/runs/{r['run_id']}/steps/{r['steps'][0]['step_id']}/retry",
                            json={"mode": "retry_clean"})
        return resp.status
    assert asyncio.run(_with_client(go)) == 200
    r = wait_run(r["run_id"])
    assert r["status"] == "completed" and not (wd / "junk.txt").exists()
    assert "--resume" not in argvs(env)[1]
    assert r["steps"][0]["attempts"][1]["mode"] == "retry_clean" and r["steps"][0]["attempts"][1]["restored"]


def test_auto_retry_on_transient_failure(env):
    counter = env["root"] / "c.cnt"
    fake(env, "claude_code", lambda req: ["--session", "S-A", "--fail-times", "1", "--counter", str(counter)])
    j, _ = setup_job([{"prompt_override": "A", "auto_retry": 2}], mode="single")
    r = wait_run(start(j)["run_id"])
    assert r["status"] == "completed", r
    assert [a["status"] for a in r["steps"][0]["attempts"]] == ["failed", "completed"]
    assert r["steps"][0]["attempts"][1]["mode"] == "retry"


def test_no_auto_retry_for_non_transient(env):
    fake(env, "claude_code", lambda req: ["--budget-hit"])
    j, _ = setup_job([{"prompt_override": "A", "auto_retry": 3, "budget": {"max_usd": 0.01}}], mode="single")
    r = wait_run(start(j)["run_id"])
    assert r["steps"][0]["status"] == "budget_exceeded" and len(r["steps"][0]["attempts"]) == 1
    a = argvs(env)[0]
    assert a[a.index("--max-budget-usd") + 1] == "0.0100"


def test_retry_refused_while_run_active(env):
    fake(env, "claude_code", lambda req: ["--hang-sec", "20"])
    j, _ = setup_job([{"prompt_override": "A"}], mode="single")
    run = start(j)
    r = wait_run(run["run_id"], lambda r: r["steps"][0]["status"] == "running")
    with pytest.raises(executor.RunBusy):
        executor.retry_step(r["run_id"], r["steps"][0]["step_id"], "retry_clean")
    executor.cancel_run(r["run_id"])
    assert wait_run(r["run_id"])["status"] == "cancelled"


# ── budgets ──────────────────────────────────────────────────────────────

def test_step_token_cap_ends_budget_exceeded(env):
    fake(env, "claude_code", lambda req: ["--tokens", "5000", "--hang-sec", "30"])
    j, _ = setup_job([{"prompt_override": "A", "budget": {"max_tokens": 100}}, {"prompt_override": "B"}])
    r = wait_run(start(j)["run_id"])
    assert r["status"] == "budget_exceeded"
    s1, s2 = r["steps"]
    assert s1["status"] == "budget_exceeded" and "max_tokens 100" in s1["error"] and s2["status"] == "skipped"
    assert s1["usage"]["output_tokens"] == 5000                                # spent is kept for a failed attempt


def test_run_budget_splits_and_exhausts(env):
    fake(env, "claude_code", lambda req: ["--handoff", HO()])
    j, _ = setup_job([{"prompt_override": "A"}, {"prompt_override": "B"}], budget={"max_usd": 0.02})
    r = wait_run(start(j)["run_id"])
    s1, s2 = r["steps"]
    assert s1["budget"]["max_usd"] == pytest.approx(0.01) and s1["budget"]["source"]["max_usd"] == "run"
    assert s2["status"] == "budget_exceeded" or s2["budget"]["max_usd"] == pytest.approx(0.0077, abs=1e-4)
    assert r["budget"] == {"max_usd": 0.02} and r["spent"]["usd"] >= 0.0123
    # the run POST body overrides the job's budget field-wise
    r2 = wait_run(start(j, budget={"max_usd": 0.005})["run_id"])
    assert r2["status"] == "budget_exceeded"
    assert "run budget used up" in r2["steps"][1]["error"]


# ── session policy ──────────────────────────────────────────────────────

def test_fork_policy_forks_previous_step_session(env):
    def script(req):
        return ["--session", "S-BASE"] if "STEP-ONE" in req.prompt else ["--session", "S-FORK"]
    fake(env, "claude_code", script)
    j, _ = setup_job([{"prompt_override": "STEP-ONE"}, {"prompt_override": "STEP-TWO", "session_policy": "fork"}])
    r = wait_run(start(j)["run_id"])
    assert r["status"] == "completed" and r["steps"][1]["session_policy"] == "fork"
    a2 = argvs(env)[1]
    assert a2[a2.index("--resume") + 1] == "S-BASE" and "--fork-session" in a2
    from services.db import sessions_repo
    row = sessions_repo.find_by_engine_session("S-FORK")
    base = sessions_repo.find_by_engine_session("S-BASE")
    assert row["lineage"] == "fork" and row["forked_from"] == base["id"]


def test_fresh_policy_never_resumes(env):
    fake(env, "claude_code", lambda req: ["--session", "S-X"])
    j, _ = setup_job([{"prompt_override": "A", "session_policy": "fresh"}], mode="single")
    wait_run(start(j)["run_id"])
    wait_run(start(j)["run_id"])
    assert all("--resume" not in a for a in argvs(env))
    # default policy resumes the scope's conversation on the next run
    j2 = get_job_manager().update_job(j["id"], {"pipeline": {"mode": "single", "steps": [
        {"agent_id": j["pipeline"]["steps"][0]["agent_id"], "prompt_override": "A"}]}})
    wait_run(start(j2)["run_id"])
    assert "--resume" in argvs(env)[2]


def test_parallel_steps_are_ephemeral_and_keep_their_files(env):
    def script(req):
        return ["--write", "a.txt=A"] if "PAR-A" in req.prompt else ["--write", "b.txt=B"]
    fake(env, "claude_code", script)
    j, wd = setup_job([{"name": "a", "prompt_override": "PAR-A", "session_policy": "resume"},
                       {"name": "b", "prompt_override": "PAR-B"}], mode="parallel")
    r = wait_run(start(j)["run_id"])
    assert r["status"] == "completed"
    for s, name in zip(r["steps"], ("a.txt", "b.txt")):
        assert s["session_policy"] == "ephemeral"
        stored = {a["path"]: a for a in s["handoff"]["artifacts"]}
        assert Path(stored[name]["stored_path"]).exists() and stored[name]["undeclared"]
        assert not session_store.exists(s["session_id"], namespace=executor.EPHEMERAL_NS)
        assert executor.step_diff(r["run_id"], s["step_id"])["files"][0]["path"] == name
        with pytest.raises(ValueError):
            executor.revert_step(r["run_id"], s["step_id"])
    assert not (wd / "a.txt").exists()


def test_antigravity_handoff_file_fallback(env):
    # agy gave no structured_output (e.g. an older build) but wrote the file: the file is the fallback.
    fake(env, "antigravity", lambda req: ["--agy", "--agy-file", "--handoff", HO(summary="agy did it"),
                                          "--text", "hi"])
    j, wd = setup_job([{"prompt_override": "A", "engine": "antigravity"}], mode="single")
    r = wait_run(start(j)["run_id"])
    ho = r["steps"][0]["handoff"]
    assert r["status"] == "completed" and ho["summary"] == "agy did it" and not ho["derived"]
    assert ".telecode/handoff.json" not in prompts(env)[0]      # no longer asked for
    assert not (wd / ".telecode" / "handoff.json").exists()


# ── rotation ──────────────────────────────────────────────────────────────

def test_rotation_after_token_threshold(env):
    import config
    env["monkeypatch"].setattr(config, "tasks_rotate_after_tokens", lambda: 100)
    seq = iter(["S-OLD", "S-OLD", "S-NEW"])
    fake(env, "claude_code", lambda req: ["--session", next(seq), "--handoff", HO(summary="state so far")]
         if "rotated into a fresh session" in req.prompt else ["--session", next(seq)])
    q = get_task_queue()
    session_store.create(session_id="ws-rot", data={})

    def run(prompt):
        tid = q.submit_task("CLAUDE_CODE", {"prompt": prompt}, session_id="ws-rot")
        end = time.time() + 20
        while time.time() < end and q.get_task(tid).status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            time.sleep(0.05)
        return q.get_task(tid)
    assert run("first").status == TaskStatus.COMPLETED               # 1000+50+20 budget tokens > 100
    t = run("second")
    assert t.status == TaskStatus.COMPLETED, t.error
    a = argvs(env)
    assert a[1][a[1].index("--resume") + 1] == "S-OLD"               # the old conversation is asked for a handoff
    assert "--resume" not in a[2]                                     # then a fresh one continues
    p = prompts(env)[2]
    assert p.startswith("<rotated_session>") and "state so far" in p and p.rstrip().endswith("second")
    assert t.result["rotation"]["from_session"] == "S-OLD"
    from services.db import sessions_repo
    new, old = sessions_repo.find_by_engine_session("S-NEW"), sessions_repo.find_by_engine_session("S-OLD")
    assert new["lineage"] == "rotation" and new["rotated_from"] == old["id"] and old["status"] == "superseded"
    assert session_store.get("ws-rot")["data"]["last_claude_session_id"] == "S-NEW"
