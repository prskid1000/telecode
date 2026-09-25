"""P4 reflection end to end with the real task handlers + Engine Runner and the
fake agent CLI: the reflection trigger, its staged input digest, the JSON
reply → proposal commit → ``memory`` approval with the diff → approve (merged
into memory) / reject (dropped), supersede, after-K-runs, and the REST routes
(memory, topics, history, diff, revert, pinned, reflection, per-agent skills)."""

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
from services.memory import reflection, repo, store
from services.task.task_manager import TaskStatus, get_task_queue
from services.triggers import store as tstore

FAKE = Path(__file__).parent / "fixtures" / "engine" / "fake_agent_cli.py"


@pytest.fixture
def env(tmp_data_root, monkeypatch):
    import config
    from services.task.task_registry import register_default_tasks
    q = get_task_queue()
    saved = dict(q.task_handlers)
    register_default_tasks()
    monkeypatch.setattr(config, "tasks_kill_grace_seconds", lambda: 0.5)
    monkeypatch.setenv("CLAUDE_SKILLS_DIR", str(tmp_data_root / "global-skills"))
    calls = {"argv": tmp_data_root / "argv.jsonl", "seen": []}
    yield {"root": tmp_data_root, "calls": calls, "monkeypatch": monkeypatch}
    q.task_handlers.clear()
    q.task_handlers.update(saved)


def fake(env, script, engine="claude_code"):
    ad = get_adapter(engine)
    orig = type(ad).build

    def build(req):
        launch = orig(ad, req)
        launch.argv = [sys.executable, str(FAKE), "--argv-out", str(env["calls"]["argv"]),
                       *[str(a) for a in script(req)], "--", *launch.argv[1:]]
        return launch
    env["monkeypatch"].setattr(ad, "build", build)


def wait_task(tid, deadline=30):
    q = get_task_queue()
    end = time.time() + deadline
    while time.time() < end:
        t = q.get_task(tid)
        if t and t.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return t
        time.sleep(0.05)
    return q.get_task(tid)


def _agent(name="reflector", **files):
    am = get_agent_manager()
    a = am.create_agent(name, engine="claude_code", model="haiku")
    am.set_internal_files(a["id"], {"AGENT.md": "# Rules\n\n## Pinned constraints\nNever push to main.\n", **files})
    store.write_topic(a["id"], "feedback_tests.md", meta={"name": "Run tests first", "type": "feedback",
                                                          "description": "before every commit"},
                      body="Run the test suite before committing.")
    store.write_topic(a["id"], "project_old.md", meta={"name": "Old project", "type": "project"}, body="obsolete")
    return a["id"]


PROPOSAL = {"summary": "Consolidated.", "operations": [
    {"op": "ADD", "name": "Deploy via staging", "type": "feedback", "description": "staging first",
     "body": "Always deploy to staging first.", "reason": "run 2 broke prod"},
    {"op": "UPDATE", "file": "feedback_tests.md", "helpful": 2, "reason": "followed twice"},
    {"op": "DELETE", "file": "project_old.md", "reason": "obsolete"},
    {"op": "NOOP", "file": "feedback_tests.md", "reason": "fine"},
    {"op": "UPDATE", "file": "nope.md"}]}


def _reflect(env, proposal=PROPOSAL, text=None, write=None):
    seen = env["calls"]["seen"]

    def script(req):
        p = Path(req.cwd) / reflection.INPUT_REL
        seen.append({"input": p.read_text(encoding="utf-8") if p.exists() else None, "env": dict(req.env_extra or {}),
                     "add_dirs": [str(d) for d in req.add_dirs]})
        args = ["--text", text if text is not None else json.dumps(proposal)]
        for w in write or ():
            args += ["--write", w]
        return args
    fake(env, script)


def test_reflect_now_proposes_a_diff_and_approve_merges_it(env):
    aid = _agent()
    _reflect(env)
    res = reflection.reflect_now(aid)
    assert res["status"] == "fired" and res["task_id"]
    t = wait_task(res["task_id"])
    assert t.status == TaskStatus.COMPLETED, t.error
    # the trigger: the agent's own, paused (nightly off), agent engine + model, cloud
    tr = tstore.get(reflection._load(aid)["trigger_id"])
    assert tr["status"] == "paused" and tr["target"]["kind"] == "agent_prompt" and tr["target"]["agent_id"] == aid
    assert tr["target"]["engine"] == "claude_code" and tr["target"]["model"] == "haiku"
    assert tr["target"]["is_local"] is False and tr["session"] == "fresh"
    # the staged digest: memory, counters, pinned constraints
    inp = env["calls"]["seen"][0]["input"]
    assert inp and "feedback_tests.md (feedback — helpful 0, harmful 0)" in inp and "Never push to main." in inp
    before = repo.head(store.internal_dir(aid))
    assert reflection.process(aid) == 1
    st = reflection.status(aid)
    assert st["last"]["status"] == "proposed" and st["runs_since"] == 0 and st["last_reflection_at"]
    ops = {(o["op"], o.get("file")): o["status"] for o in st["last"]["operations"]}
    assert ops[("DELETE", "project_old.md")] == "applied" and ops[("UPDATE", "nope.md")] == "skipped"
    ap = approvals.get(st["last"]["approval_id"])
    assert ap["kind"] == "memory" and ap["status"] == "pending" and "+1 ~1 -1" in ap["title"]
    assert "+Always deploy to staging first." in ap["body"] and "-obsolete" in ap["body"]
    assert "+helpful: 2" in ap["body"]
    # nothing applied yet: memory untouched until approved
    assert repo.head(store.internal_dir(aid)) == before
    assert (store.memory_dir(aid) / "project_old.md").exists()
    approvals.decide(ap["id"], "approve", by="test")
    assert not (store.memory_dir(aid) / "project_old.md").exists()
    assert (store.memory_dir(aid) / "feedback_deploy_via_staging.md").exists()
    meta, _ = store.parse_topic((store.memory_dir(aid) / "feedback_tests.md").read_text(encoding="utf-8"))
    assert meta["helpful"] == 2
    idx = store.read_index(aid)
    assert "feedback_deploy_via_staging.md" in idx and "project_old.md" not in idx
    top = repo.history(store.internal_dir(aid), limit=2)[0]
    assert top["subject"].startswith("reflection: +1 ~1 -1") and top["kind"] == "reflection"
    assert not repo.ref_exists(store.internal_dir(aid), ap["payload"]["ref"])
    assert reflection.status(aid)["last"]["decision"]["status"] == "applied"


def test_reflection_run_never_writes_back_and_gets_no_memory_access(env):
    aid = _agent()
    _reflect(env, write=["MEMORY.md=hijacked"])
    t = wait_task(reflection.reflect_now(aid)["task_id"])
    assert t.status == TaskStatus.COMPLETED
    assert "hijacked" not in store.read_index(aid)
    # engine_extras (called by the runner) gives a reflection fire no memory dir and no --settings
    assert str(store.memory_dir(aid)) not in env["calls"]["seen"][0]["add_dirs"]
    assert all("--settings" not in json.loads(l) for l in env["calls"]["argv"].read_text(encoding="utf-8").splitlines())
    assert not any(c["subject"].startswith("task:") for c in repo.history(store.internal_dir(aid), limit=50))


def test_reject_drops_the_proposal_and_memory_moves_meanwhile_merges(env):
    aid = _agent()
    _reflect(env)
    wait_task(reflection.reflect_now(aid)["task_id"])
    reflection.process(aid)
    ap = approvals.get(reflection.status(aid)["last"]["approval_id"])
    approvals.decide(ap["id"], "reject", by="test")
    assert (store.memory_dir(aid) / "project_old.md").exists()
    assert not repo.ref_exists(store.internal_dir(aid), ap["payload"]["ref"])
    # second proposal; memory changes before approval → merge, not fast-forward
    wait_task(reflection.reflect_now(aid)["task_id"])
    reflection.process(aid)
    ap2 = approvals.get(reflection.status(aid)["last"]["approval_id"])
    store.write_topic(aid, "user_me.md", meta={"name": "Me", "type": "user"}, body="hi")
    approvals.decide(ap2["id"], "approve", by="test")
    assert (store.memory_dir(aid) / "user_me.md").exists() and not (store.memory_dir(aid) / "project_old.md").exists()
    assert repo.history(store.internal_dir(aid), limit=1)[0]["subject"] == f"merge reflection:{ap2['id']}"


def test_newer_proposal_supersedes_pending_and_unparsed_reply(env):
    aid = _agent()
    _reflect(env)
    wait_task(reflection.reflect_now(aid)["task_id"])
    reflection.process(aid)
    first = reflection.status(aid)["last"]["approval_id"]
    wait_task(reflection.reflect_now(aid)["task_id"])
    reflection.process(aid)
    assert approvals.get(first)["status"] == "rejected" and approvals.get(first)["decided_by"] == "system"
    _reflect(env, text="I could not decide.")
    wait_task(reflection.reflect_now(aid)["task_id"])
    reflection.process(aid)
    assert reflection.status(aid)["last"]["status"] == "unparsed"
    _reflect(env, proposal={"summary": "nothing", "operations": [{"op": "NOOP", "file": "feedback_tests.md"}]})
    wait_task(reflection.reflect_now(aid)["task_id"])
    reflection.process(aid)
    assert reflection.status(aid)["last"]["status"] == "nothing_to_change"


def test_after_k_runs_fires_a_reflection_once_per_pipeline_run(env):
    aid = _agent()
    reflection.set_options(aid, after_runs=2)
    fired = []
    env["monkeypatch"].setattr(reflection, "reflect_now", lambda a, reason="": fired.append(reason) or {})
    reflection.note_run(aid, {"run_id": "R1", "task_id": "t1"})
    reflection.note_run(aid, {"run_id": "R1", "task_id": "t2"})       # same pipeline run: counted once
    assert not fired and reflection._load(aid)["runs_since"] == 1
    reflection.note_run(aid, {"run_id": None, "task_id": "t3"})
    assert fired == ["after 2 runs"]
    reflection.set_options(aid, after_runs=0)
    for i in range(5):
        reflection.note_run(aid, {"task_id": f"x{i}"})
    assert fired == ["after 2 runs"]


def test_agent_task_counts_and_nightly_switch(env):
    aid = _agent()
    fake(env, lambda req: ["--text", "done"])
    q = get_task_queue()
    from services.session import session_store
    session_store.ensure("ws-k", session_idle_timeout_seconds=0)
    tid = q.submit_task("CLAUDE_CODE", {"prompt": "hi", "agent_id": aid}, metadata={"agent_id": aid},
                        session_id="ws-k")
    assert wait_task(tid).status == TaskStatus.COMPLETED
    assert reflection._load(aid)["runs_since"] == 1
    argv = json.loads(env["calls"]["argv"].read_text(encoding="utf-8").splitlines()[-1])
    from services.engine import runner
    if hasattr(runner, "engine_extras"):      # the runner applies services.memory.engine_extras (P5 wiring)
        assert "--settings" in argv, argv
        sp = Path(argv[argv.index("--settings") + 1])
        assert json.loads(sp.read_text(encoding="utf-8")) == {"autoMemoryDirectory": str(store.memory_dir(aid))}
    st = reflection.set_options(aid, nightly=True)
    assert st["nightly"] and st["trigger"]["status"] == "active" and st["trigger"]["next_fire_at"]
    st = reflection.set_options(aid, nightly=False)
    assert st["trigger"]["status"] == "paused"
    # deleting the agent deletes its reflection trigger
    trig = reflection._load(aid)["trigger_id"]
    get_agent_manager().delete_agent(aid)
    assert tstore.get(trig) is None


# ── REST ───────────────────────────────────────────────────────────────────

def _app():
    from proxy import api_agents, api_approvals, api_skills
    app = web.Application()
    api_agents.register_routes(app)
    api_skills.register_routes(app)
    api_approvals.register_routes(app)
    return app


def _run(coro_fn):
    async def go():
        c = TestClient(TestServer(_app()))
        await c.start_server()
        try:
            return await coro_fn(c)
        finally:
            await c.close()
    return asyncio.new_event_loop().run_until_complete(go())


def test_rest_memory_topics_history_diff_revert_pinned(env):
    aid = _agent()

    async def go(c):
        base = f"/api/agents/{aid}"
        r = await c.get(f"{base}/memory")
        m = await r.json()
        assert r.status == 200 and {t["file"] for t in m["topics"]} == {"feedback_tests.md", "project_old.md"}
        assert m["git"] and m["head"]
        r = await c.put(f"{base}/memory/topics/user_me.md",
                        json={"meta": {"name": "Me", "type": "user", "description": "the owner"}, "body": "hi"})
        assert r.status == 200 and "user_me.md" in store.read_index(aid)
        assert (await c.put(f"{base}/memory/topics/..%2Fx.md", json={"content": "x"})).status in (400, 404)
        assert (await c.put(f"{base}/memory/topics/a.md", json={"meta": {"type": "bad"}})).status == 400
        r = await c.get(f"{base}/memory/topics/user_me.md")
        assert (await r.json())["meta"]["name"] == "Me"
        r = await c.get(f"{base}/memory/history?limit=5")
        commits = (await r.json())["commits"]
        assert commits[0]["subject"] == "ui: add memory user_me.md"
        r = await c.get(f"{base}/memory/diff?commit={commits[0]['sha']}")
        d = await r.json()
        assert "+hi" in d["diff"] and {f["path"] for f in d["files"]} == {"memory/user_me.md", "memory/MEMORY.md"}
        assert (await c.get(f"{base}/memory/diff?commit=zzz")).status == 400
        assert (await c.get(f"{base}/memory/diff?commit=abcdef12")).status == 404
        assert (await c.get(f"{base}/memory/history?path=../x")).status == 400
        r = await c.post(f"{base}/memory/revert", json={"commit": commits[0]["sha"]})
        assert r.status == 200 and "user_me.md" not in store.read_index(aid)
        r = await c.put(f"{base}/memory/index", json={"content": "# Memory Index\n"})
        assert r.status == 200 and store.read_index(aid) == "# Memory Index\n"
        r = await c.post(f"{base}/memory/index/rebuild")
        assert "feedback_tests.md" in (await r.json())["index"]
        r = await c.put(f"{base}/pinned", json={"text": "Stay in the workspace."})
        assert (await r.json())["text"] == "Stay in the workspace."
        assert (await (await c.get(f"{base}/pinned")).json())["text"] == "Stay in the workspace."
        r = await c.delete(f"{base}/memory/topics/project_old.md")
        assert r.status == 200 and (await c.delete(f"{base}/memory/topics/project_old.md")).status == 404
        r = await c.get(f"{base}/memory/engine-extras?engine=codex")
        assert (await r.json())["add_dirs"] == [str(store.memory_dir(aid))]
        assert (await c.get("/api/agents/..bad/memory")).status == 400
        assert (await c.get("/api/agents/nope/memory")).status == 404
    _run(go)


def test_rest_reflection_and_approval_inbox(env):
    aid = _agent()
    _reflect(env)

    async def go(c):
        base = f"/api/agents/{aid}/memory"
        r = await c.put(f"{base}/reflection", json={"after_runs": 3})
        assert (await r.json())["after_runs"] == 3
        assert (await c.put(f"{base}/reflection", json={"nightly": "yes"})).status == 400
        r = await c.post(f"{base}/reflect")
        body = await r.json()
        assert r.status == 200 and body["fire"]["status"] == "fired"
        wait_task(body["fire"]["task_id"])
        r = await c.get(f"{base}/reflection")
        st = await r.json()
        assert st["last"]["status"] == "proposed" and st["pending_approval_ids"]
        r = await c.get("/api/approvals?status=pending")
        aps = [a for a in (await r.json())["approvals"] if a["kind"] == "memory"]
        assert aps and aps[0]["payload"]["agent_id"] == aid
        r = await c.post(f"/api/approvals/{aps[0]['id']}/approve", json={})
        assert r.status == 200
        assert (store.memory_dir(aid) / "feedback_deploy_via_staging.md").exists()
    _run(go)


def test_rest_agent_skills_crud_promote_copy(env):
    aid = _agent()
    from services.skills import skill_store
    skill_store.upsert_skill("global-a", "global A")

    async def go(c):
        base = f"/api/agents/{aid}/skills"
        assert (await (await c.get(base)).json())["skills"] == []
        r = await c.put(f"{base}/lint", json={"content": "---\ndescription: lint it\n---\nrun ruff"})
        assert r.status == 200 and (await r.json())["description"] == "lint it"
        r = await c.put(f"{base}/lint/files/ref/a.md", data=b"ref")
        assert r.status == 200
        assert await (await c.get(f"{base}/lint/files/ref/a.md")).read() == b"ref"
        assert (await c.put(f"{base}/Bad Name", json={"content": "x"})).status == 400
        r = await c.post("/api/skills/global-a/promote", json={"agent_id": aid})
        assert r.status == 200 and (await r.json())["content"] == "global A"
        assert (await c.post("/api/skills/global-a/promote", json={"agent_id": aid})).status == 409
        r = await c.post(f"{base}/lint/copy-to-global", json={})
        assert r.status == 200 and skill_store.get_skill("lint")["content"].endswith("run ruff")
        assert (await c.post(f"{base}/lint/copy-to-global", json={})).status == 409
        names = [s["name"] for s in (await (await c.get(base)).json())["skills"]]
        assert names == ["global-a", "lint"]
        assert (await c.delete(f"{base}/lint")).status == 200
        assert (await c.get(f"{base}/lint")).status == 404
    _run(go)
