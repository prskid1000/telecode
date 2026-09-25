"""The guarantees the pre-P3 heartbeat / routine tests pinned, re-pinned on
triggers (fake task handlers — no CLI): HEARTBEAT.md validation, B3 engine /
model / local per entry, B4 first fire of a new entry, B6 interrupted fires
after a restart, B13 per-fire timeout, and the REST glue (agents / jobs)."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services.agent.agent_manager import get_agent_manager
from services.job.job_manager import get_job_manager
from services.session import session_store
from services.task.task_manager import TaskStatus, get_task_queue
from services.triggers import fire as fire_mod
from services.triggers import heartbeat, scheduler, service, store
from services.triggers import schedule as sched


@pytest.fixture
def hb_on(monkeypatch):
    import config
    monkeypatch.setattr(config, "heartbeat_enabled", lambda: True)


def _wait(tid, deadline=10):
    q = get_task_queue()
    end = time.time() + deadline
    while time.time() < end:
        t = q.get_task(tid)
        if t and t.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return t
        time.sleep(0.02)
    return q.get_task(tid)


def _agent(md="", **kw):
    a = get_agent_manager().create_agent("g-agent", **kw)
    if md:
        get_agent_manager().set_internal_files(a["id"], {"HEARTBEAT.md": md})
    return a


# ── HEARTBEAT.md validation ─────────────────────────────────────────────────

@pytest.mark.parametrize("entry,msg", [
    ("- name: a\n  cron: '0 9 * * *'", "missing 'prompt'"),
    ("- cron: '0 9 * * *'\n  prompt: p", "missing or empty 'name'"),
    ("- name: a\n  cron: '0 9 * * *'\n  prompt: p\n  workspace: persistent", "workspace_id required"),
    ("- name: a\n  cron: '0 9 * * *'\n  prompt: p\n  workspace: persistent\n  workspace_id: nope", "does not exist"),
    ("- name: a\n  cron: '0 9 * * *'\n  prompt: p\n  workspace: sometimes", "workspace must be one of"),
    ("- name: a\n  cron: '0 9 * * *'\n  prompt: p\n  engine: gpt", "engine must be one of"),
    ("- name: a\n  cron: '0 9 * * *'\n  prompt: p\n  is_local: maybe", "is_local must be true or false"),
    ("- name: a\n  cron: '0 9 * * *'\n  prompt: p\n  model: 5", "model must be a string"),
    ("- name: a\n  cron: '0 9 * * *'\n  every: 1h\n  prompt: p", "only one of cron / every / at"),
    ("- name: a\n  at: someday\n  prompt: p", "ISO date-time"),
    ("- name: a\n  cron: '0 9 * * *'\n  tz: Nowhere/City\n  prompt: p", "unknown time zone"),
    ("- name: a\n  every: 1h\n  active_hours: 'late-night'\n  prompt: p", "HH:MM"),
    ("- name: a\n  every: 1h\n  prompt: p\n  permission_mode: yolo", "permission_mode"),
    ("- oops", "entry must be a mapping"),
])
def test_heartbeat_entry_errors(tmp_data_root, entry, msg):
    a = _agent()
    out = heartbeat.parse(f"notes\n```yaml\n{entry}\n```\n", a["id"])
    assert not out["ok"] and msg in " | ".join(e["msg"] for e in out["errors"]), out["errors"]


def test_heartbeat_blocks_notes_and_duplicates(tmp_data_root):
    a = _agent()
    out = heartbeat.parse("free text is ignored\n```yaml\n- name: x\n  every: 1h\n  prompt: p\n```\n"
                          "more notes\n```yaml\nnot: a list\n```\n```yaml\n- name: x\n  every: 2h\n  prompt: q\n```\n"
                          "```yaml\n- name: y\n  cron: [broken\n```", a["id"])
    msgs = [e["msg"] for e in out["errors"]]
    assert [e["name"] for e in out["entries"]] == ["x"]
    assert any("must be a list" in m for m in msgs) and any("duplicate name" in m for m in msgs)
    assert any("yaml parse error" in m for m in msgs)
    assert len(out["entries"][0]["next_fires"]) == 3


def test_validate_route_and_compile_on_save(tmp_data_root, hb_on):
    from proxy import api_agents
    a = _agent()

    async def go():
        app = web.Application()
        api_agents.register_routes(app)
        c = TestClient(TestServer(app))
        await c.start_server()
        try:
            v = await (await c.post(f"/api/agents/{a['id']}/heartbeat/validate",
                                    json={"text": "```yaml\n- name: t\n  every: 30m\n  prompt: P\n```"})).json()
            put = await (await c.put(f"/api/agents/{a['id']}/internal", json={"files": {
                "HEARTBEAT.md": "```yaml\n- name: t\n  every: 30m\n  prompt: P\n```"}})).json()
            rec = await (await c.post(f"/api/agents/{a['id']}/heartbeat/reconcile")).json()
            dl = await c.delete(f"/api/agents/{a['id']}")
            return v, put, rec, dl.status
        finally:
            await c.close()
    v, put, rec, dl = asyncio.run(go())
    assert v["ok"] and v["entries"][0]["schedule"] == {"every_seconds": 1800} and "body" not in v["entries"][0]
    assert put["reconcile"]["created"] == 1 and rec["created"] == 0 and rec["updated"] == 0
    assert dl == 200 and store.list_all(source="heartbeat") == []              # its triggers went with it


# ── B3: engine / model / local reach the fire ───────────────────────────────

def test_b3_entry_engine_model_and_local_reach_the_task(tmp_data_root, fake_task_queue, hb_on):
    a = _agent("```yaml\n- name: cx\n  every: 1h\n  prompt: CX\n  engine: codex\n  model: gpt-5-mini\n  is_local: true\n"
               "- name: default-model\n  every: 1h\n  prompt: DM\n```", engine="claude_code", model="sonnet")
    heartbeat.compile_agent(a["id"])
    by = {r["name"]: r for r in store.list_all(source="heartbeat")}
    t1 = _wait(fire_mod.fire(by["cx"]["id"], source="manual")["task_id"])
    t2 = _wait(fire_mod.fire(by["default-model"]["id"], source="manual")["task_id"])
    assert t1.task_type == "CODEX" and t2.task_type == "CLAUDE_CODE"
    c1, c2 = fake_task_queue.last_calls[-2:]
    assert c1["model"] == "gpt-5-mini" and c1["is_local"] is True and c1["agent_id"] == a["id"]
    assert c2["model"] == "sonnet" and c2["is_local"] is False                 # the agent's default model


# ── B4: a new entry fires at its first slot ─────────────────────────────────

def test_b4_new_cron_entry_fires_at_the_first_slot(tmp_data_root, fake_task_queue, hb_on):
    a = _agent("```yaml\n- name: hourly\n  cron: '0 * * * *'\n  prompt: H\n```")
    tid = heartbeat.compile_agent(a["id"])["triggers"][0]
    nfa = sched.parse_iso(store.get(tid)["state"]["next_fire_at"])
    assert nfa > sched.utcnow() and nfa.minute == 0
    assert scheduler.tick(nfa - timedelta(seconds=1))["fired"] == 0
    assert scheduler.tick(nfa + timedelta(seconds=1))["fired"] == 1
    assert sched.parse_iso(store.get(tid)["state"]["next_fire_at"]) == nfa + timedelta(hours=1)


# ── B6: a fire left running by a previous process ───────────────────────────

def test_b6_fire_whose_task_vanished_becomes_interrupted(tmp_data_root, fake_task_queue):
    t = service.create({"name": "i", "target": {"kind": "task", "prompt": "p"}, "auto_pause_after_failures": 1})
    row = store.add_fire(t["id"], source="schedule", status="running", task_id="gone-task")
    from services.db import task_repo
    task_repo.save_task({"task_id": "gone-task", "task_type": "CLAUDE_CODE", "status": "running", "metadata": {}})
    get_task_queue().reconcile_persisted()                                   # restart: DB task → failed "interrupted…"
    assert fire_mod.reconcile_all() == 1
    f, rec = store.get_fire(row["id"]), store.get(t["id"])
    assert f["status"] == "interrupted" and rec["status"] == "paused"         # counts as a failure
    assert rec["state"]["consecutive_failures"] == 1


# ── B13: per-fire timeout + skip-if-running under the lock ──────────────────

def test_b13_timeout_reaches_the_queue_and_outputs_only_is_prompt_text(tmp_data_root, fake_task_queue):
    t = service.create({"name": "to", "target": {"kind": "task", "prompt": "p"}, "task_timeout_seconds": 90,
                        "outputs_only": True})
    task = _wait(service.run_now(t["id"])["task_id"])
    assert task.timeout_seconds == 90 and "outputs_only" not in fake_task_queue.last_calls[-1]
    assert "Output rule for this trigger" in fake_task_queue.last_calls[-1]["prompt"]


def test_concurrent_fires_submit_once(tmp_data_root, fake_task_queue):
    import threading
    fake_task_queue.block()
    t = service.create({"name": "race", "target": {"kind": "task", "prompt": "p"}})
    results = []
    ths = [threading.Thread(target=lambda: results.append(fire_mod.fire(t["id"], source="manual"))) for _ in range(5)]
    [x.start() for x in ths]
    [x.join() for x in ths]
    assert sum(1 for r in results if r["status"] == "fired") == 1
    fake_task_queue.release()


# ── REST glue ───────────────────────────────────────────────────────────────

def test_deleting_a_job_deletes_its_triggers_and_webhook_size_cap(tmp_data_root):
    from proxy import api_jobs, api_triggers
    session_store.create(session_id="ws-g", data={})
    j = get_job_manager().create_job({"title": "j", "workspace_id": "ws-g", "agent_id": "a"})
    t = service.create({"name": "jt", "target": {"kind": "job", "id": j["id"]}, "events": {"webhook": {"enabled": True}}})
    tok = service.get(t["id"])["events"]["webhook"]["token"]

    async def go():
        app = web.Application()
        api_jobs.register_routes(app)
        api_triggers.register_routes(app)
        c = TestClient(TestServer(app))
        await c.start_server()
        try:
            big = await c.post(f"/api/triggers/{t['id']}/fire", data=b"x" * (300 * 1024),
                               headers={"Authorization": f"Bearer {tok}"})
            dl = await (await c.delete(f"/api/jobs/{j['id']}")).json()
            return big.status, dl
        finally:
            await c.close()
    big, dl = asyncio.run(go())
    assert big == 413 and dl["triggers_deleted"] == 1 and store.get(t["id"]) is None


def test_webhook_reads_a_large_body_whole(tmp_data_root, fake_task_queue):
    from proxy import api_triggers
    t = service.create({"name": "big", "target": {"kind": "task", "prompt": "p"}, "events": {"webhook": {"enabled": True}}})
    tok = service.get(t["id"])["events"]["webhook"]["token"]

    async def go():
        app = web.Application()
        api_triggers.register_routes(app)
        c = TestClient(TestServer(app))
        await c.start_server()
        try:
            r = await c.post(f"/api/triggers/{t['id']}/fire", json={"big": "x" * 200_000, "tail": "END"},
                             headers={"Authorization": f"Bearer {tok}"})
            return r.status, await r.json()
        finally:
            await c.close()
    st, body = asyncio.run(go())
    assert st == 202
    _wait(body["task_id"])
    p = fake_task_queue.last_calls[-1]["prompt"]
    assert '{\n  "big": "xxx' in p and "truncated" in p                    # parsed as JSON (whole body), then capped
