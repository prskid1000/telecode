"""P1 store + SSE: task write-through survives a "restart" (a fresh TaskQueue
with nothing in memory), startup reconcile of tasks left running, capped event
retention, the REST fallbacks, SSE replay-then-live for tasks and runs, the
global feed, the one-time data/runs/*.json import, session lineage, and
TeleDesign's turn handler delegating to the Engine Runner."""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services.db import sessions_repo, task_repo
from services.session import session_store
from services.task import task_manager as tm
from services.task.task_manager import TaskQueue, TaskStatus, get_task_queue


@pytest.fixture(autouse=True)
def _restore_handlers():
    q = get_task_queue()
    saved = dict(q.task_handlers)
    yield
    q.task_handlers.clear()
    q.task_handlers.update(saved)


def _wait(pred, deadline=10.0):
    end = time.time() + deadline
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def _emitting_handler(n=3, gate=None):
    from services.task.task_utils import append_event

    def handler(**_kw):
        append_event({"kind": "start", "prompt": "hello world"})
        for i in range(n - 2):
            append_event({"kind": "narrative", "text": f"step {i}"})
        if gate is not None:
            gate.wait(10)
            append_event({"kind": "narrative", "text": "after gate"})
        append_event({"kind": "done", "tool_count": 0})
        return {"result": "ok"}
    return handler


def _finished_task(n=4):
    q = get_task_queue()
    q.register_handler("P1_EMIT", _emitting_handler(n))
    tid = q.submit_task("P1_EMIT", {}, session_id="ws-sse")
    assert _wait(lambda: q.get_task(tid).status == TaskStatus.COMPLETED)
    task_repo.flush()
    return tid


# ── Restart persistence ─────────────────────────────────────────────────────

def test_finished_task_survives_restart(tmp_data_root):
    tid = _finished_task(4)
    fresh = TaskQueue(max_workers=1, background_workers=1)       # "after restart": nothing in memory
    assert fresh.get_task(tid) is None
    rec = fresh.get_task_record(tid)
    assert rec["status"] == "completed" and rec["result"] == {"result": "ok"}
    assert [e["kind"] for e in rec["metadata"]["events"]] == ["start", "narrative", "narrative", "done"]
    listed = {d["task_id"]: d for d in fresh.list_task_records()}
    assert listed[tid]["metadata"]["events"] == [rec["metadata"]["events"][0]]   # list carries the start event


def test_reconcile_marks_tasks_left_running_as_interrupted(tmp_data_root):
    task_repo.save_task({"task_id": "t-orphan", "task_type": "CLAUDE_CODE", "status": "running",
                         "created_at": "2026-09-25T00:00:00", "metadata": {}})
    task_repo.flush()
    fresh = TaskQueue(max_workers=1, background_workers=1)
    assert fresh.reconcile_persisted() == 1
    rec = task_repo.load_task("t-orphan")
    assert rec["status"] == "failed" and rec["error"].startswith("interrupted")
    assert fresh.reconcile_persisted() == 0


def test_reconcile_leaves_live_tasks_alone(tmp_data_root):
    gate = threading.Event()
    q = get_task_queue()
    q.register_handler("P1_EMIT", _emitting_handler(3, gate))
    tid = q.submit_task("P1_EMIT", {}, session_id="ws-live")
    assert _wait(lambda: q.get_task(tid).status == TaskStatus.RUNNING)
    task_repo.flush()
    assert q.reconcile_persisted() == 0
    gate.set()
    assert _wait(lambda: q.get_task(tid).status == TaskStatus.COMPLETED)


def test_event_retention_keeps_head_and_tail(tmp_data_root, monkeypatch):
    monkeypatch.setattr(task_repo, "KEEP_HEAD", 5)
    monkeypatch.setattr(task_repo, "KEEP_TAIL", 20)
    monkeypatch.setattr(task_repo, "_TRIM_EVERY", 10)
    for seq in range(1, 101):
        task_repo.append_event("t-cap", seq, {"kind": "narrative", "text": str(seq)})
    task_repo.flush()
    seqs = [e["seq"] for e in task_repo.events("t-cap")]
    assert seqs[:5] == [1, 2, 3, 4, 5] and seqs[-20:] == list(range(81, 101)) and len(seqs) == 25


def test_prompt_is_not_persisted_beyond_digest(tmp_data_root):
    q = get_task_queue()
    q.register_handler("P1_EMIT", _emitting_handler(2))
    tid = q.submit_task("P1_EMIT", {"prompt": "x" * 60000}, session_id="ws-dig")
    assert _wait(lambda: q.get_task(tid).status in (TaskStatus.COMPLETED, TaskStatus.FAILED))
    task_repo.flush()
    rec = task_repo.load_task(tid)
    assert "x" * 1000 not in json.dumps(rec)


# ── REST + SSE ──────────────────────────────────────────────────────────────

def _app():
    from proxy import api_events, api_runs, api_tasks
    app = web.Application()
    api_tasks.register_routes(app)
    api_runs.register_routes(app)
    api_events.register_routes(app)
    return app


def _frames(body: str):
    out = []
    for block in body.split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln and not ln.startswith(":")]
        if not lines:
            continue
        f = {"id": None, "event": None, "data": ""}
        for ln in lines:
            k, _, v = ln.partition(": ")
            if k == "data":
                f["data"] += v
            else:
                f[k] = v
        f["data"] = json.loads(f["data"]) if f["data"] else None
        out.append(f)
    return out


def _run(coro):
    return asyncio.run(coro)


async def _client():
    client = TestClient(TestServer(_app()))
    await client.start_server()
    return client


def test_rest_falls_back_to_db_after_restart(tmp_data_root, monkeypatch):
    tid = _finished_task(3)
    monkeypatch.setattr(tm, "_task_queue", TaskQueue(max_workers=1, background_workers=1))

    async def go():
        c = await _client()
        try:
            r = await c.get(f"/api/tasks/{tid}")
            one = await r.json()
            r2 = await c.get("/api/tasks")
            allt = await r2.json()
            r3 = await c.get("/api/tasks/..%2F..%2Fx")
            return one, allt, r3.status
        finally:
            await c.close()
    one, allt, bad = _run(go())
    assert one["success"] and one["status"] == "completed" and len(one["metadata"]["events"]) == 3
    assert tid in {t["task_id"] for t in allt["completed"]}
    assert bad == 404


def test_task_sse_replays_finished_task_and_honours_after(tmp_data_root):
    tid = _finished_task(4)

    async def go():
        c = await _client()
        try:
            full = await (await c.get(f"/api/tasks/{tid}/events")).text()
            part = await (await c.get(f"/api/tasks/{tid}/events", headers={"Last-Event-ID": "2"})).text()
            missing = (await c.get("/api/tasks/nope-nope/events")).status
            invalid = (await c.get("/api/tasks/a.b/events")).status
            return full, part, missing, invalid
        finally:
            await c.close()
    full, part, missing, invalid = _run(go())
    f = _frames(full)
    assert [x["event"] for x in f] == ["event"] * 4 + ["status", "end"]
    assert [x["id"] for x in f[:4]] == ["1", "2", "3", "4"]
    assert f[0]["data"]["kind"] == "start" and f[4]["data"]["status"] == "completed"
    p = _frames(part)
    assert [x["id"] for x in p if x["event"] == "event"] == ["3", "4"]
    assert missing == 404 and invalid == 400


def test_task_sse_replay_then_live(tmp_data_root):
    gate = threading.Event()
    q = get_task_queue()
    q.register_handler("P1_EMIT", _emitting_handler(3, gate))

    async def go():
        c = await _client()
        try:
            tid = q.submit_task("P1_EMIT", {}, session_id="ws-live2")
            await asyncio.to_thread(_wait, lambda: len(q.get_task(tid).metadata.get("events") or []) >= 2)
            resp = await c.get(f"/api/tasks/{tid}/events")
            await asyncio.sleep(0.2)
            gate.set()
            body = await asyncio.wait_for(resp.text(), 15)
            return body
        finally:
            await c.close()
    f = _frames(_run(go()))
    events = [x for x in f if x["event"] == "event"]
    assert [x["data"]["text"] for x in events if x["data"]["kind"] == "narrative"] == ["step 0", "after gate"]
    assert [int(x["id"]) for x in events] == [1, 2, 3, 4]                # no duplicates across replay/live
    assert f[-1]["event"] == "end" and f[-2]["data"]["status"] == "completed"


def test_run_sse_snapshot_live_and_end(tmp_data_root):
    from services.run.run_store import get_run_store
    rs = get_run_store()

    async def go():
        c = await _client()          # builds the app first: its startup reconcile runs here
        try:
            run = rs.create_run(job_id="job-1", mode="single", source="user",
                                steps=[{"step_id": "s1", "agent_id": "a", "name": "one"}])
            rid = run["run_id"]
            resp = await c.get(f"/api/runs/{rid}/events")

            def advance():
                time.sleep(0.2)
                rs.update_run(rid, {"status": "running"})
                rs.update_step(rid, "s1", {"status": "completed"})
                rs.finalise(rid)
            threading.Thread(target=advance, daemon=True).start()
            body = await asyncio.wait_for(resp.text(), 15)
            done = await (await c.get(f"/api/runs/{rid}/events")).text()
            missing = (await c.get("/api/runs/nope/events")).status
            return rid, body, done, missing
        finally:
            await c.close()
    rid, body, done, missing = _run(go())
    f = _frames(body)
    assert f[0]["event"] == "run" and f[0]["data"]["status"] == "pending"
    assert any(x["event"] == "run" and x["data"]["steps"][0]["status"] == "completed" for x in f)
    assert f[-1] == {"id": None, "event": "end", "data": {"run_id": rid, "status": "completed"}}
    assert [x["event"] for x in _frames(done)] == ["run", "end"]              # already final: snapshot + end
    assert missing == 404


def test_global_feed_gets_task_status_summaries(tmp_data_root):
    q = get_task_queue()
    q.register_handler("P1_EMIT", _emitting_handler(2))

    async def go():
        c = await _client()
        try:
            bad = (await c.get("/api/events?kinds=bogus")).status
            resp = await c.get("/api/events?kinds=task")
            await asyncio.sleep(0.1)
            tid = q.submit_task("P1_EMIT", {}, session_id="ws-glob")
            seen = []
            buf = ""
            end = time.time() + 10
            while time.time() < end and not any(s.get("status") == "completed" for s in seen):
                chunk = await asyncio.wait_for(resp.content.readany(), 10)
                buf += chunk.decode()
                *blocks, buf = buf.split("\n\n")
                for fr in _frames("\n\n".join(blocks)):
                    if fr["event"] == "task.status" and fr["data"]["task_id"] == tid:
                        seen.append(fr["data"])
            resp.close()
            return bad, seen
        finally:
            await c.close()
    bad, seen = _run(go())
    assert bad == 400
    statuses = [s["status"] for s in seen]
    assert "completed" in statuses and "events" not in seen[0]                 # summaries only


# ── Run store ───────────────────────────────────────────────────────────────

def test_run_store_imports_json_once_and_keeps_files(tmp_data_root):
    from services.run import run_store as rsm
    runs_dir = tmp_data_root / "data" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    legacy = {"run_id": "legacy-run-1", "job_id": "job-L", "mode": "single", "source": "user",
              "status": "completed", "started_at": "2026-09-01T00:00:00Z", "completed_at": "2026-09-01T00:01:00Z",
              "overrides": {}, "usage": None,
              "steps": [{"step_id": "s1", "status": "completed", "task_id": "t1", "result_text": "hi"}]}
    path = runs_dir / "legacy-run-1.json"
    raw = json.dumps(legacy)
    path.write_text(raw, encoding="utf-8")
    (runs_dir / "broken.json").write_text("{nope", encoding="utf-8")
    from services.db import core
    core.connect().execute("DELETE FROM meta")                               # fixture's store already ran
    store = rsm.RunStore()
    assert store.get_run("legacy-run-1") == legacy
    assert path.read_text(encoding="utf-8") == raw                            # JSON left untouched
    assert store.delete_run("legacy-run-1")
    rsm.RunStore()                                                           # second init: no re-import
    assert store.get_run("legacy-run-1") is None
    assert [r["run_id"] for r in store.list_runs(job_id="job-L")] == []


def test_run_store_roundtrip_order_and_bad_ids(tmp_data_root):
    from services.run.run_store import get_run_store
    rs = get_run_store()
    run = rs.create_run(job_id="j", mode="sequential", source="user",
                        steps=[{"step_id": f"s{i}", "name": str(i)} for i in range(5)])
    rs.update_step(run["run_id"], "s3", {"status": "failed", "error": "x"})
    got = rs.get_run(run["run_id"])
    assert [s["step_id"] for s in got["steps"]] == ["s0", "s1", "s2", "s3", "s4"]
    assert got["steps"][3]["error"] == "x"
    assert rs.get_run("../etc") is None and rs.update_run("a b", {}) is None and rs.delete_run("..") is False


# ── Session lineage ─────────────────────────────────────────────────────────

def test_session_lineage_accumulates_and_supersedes(tmp_data_root):
    kw = dict(namespace=None, workspace_id="ws", agent_id="A", engine="codex", is_local=False,
              kind="run", created_by="user")
    r1 = sessions_repo.record_run(engine_session_id="th-1", task_id="t1",
                                  tokens={"total_input_incl_cache": 100, "output": 5}, cost_usd=None, **kw)
    assert sessions_repo.record_run(engine_session_id="th-1", task_id="t2",
                                    tokens={"total_input_incl_cache": 50, "output": 1}, cost_usd=None, **kw) == r1
    r2 = sessions_repo.record_run(engine_session_id="th-2", task_id="t3", tokens={}, cost_usd=0.5, **kw)
    other = sessions_repo.record_run(engine_session_id="c-9", task_id="t4", tokens={}, cost_usd=0.1,
                                     **{**kw, "agent_id": "B", "engine": "claude_code"})
    rows = {r["id"]: r for r in sessions_repo.list_sessions(workspace_id="ws")}
    assert rows[r1]["status"] == "superseded" and rows[r1]["runs_count"] == 2
    assert rows[r1]["cumulative_input_tokens"] == 150 and rows[r1]["cost_complete"] is False
    assert rows[r2]["parent_id"] == r1 and rows[r2]["status"] == "active"
    assert rows[other]["parent_id"] is None                                   # scoped per agent+engine
    assert sessions_repo.record_run(engine_session_id=None, task_id="t5", tokens={}, cost_usd=None, **kw) is None


def test_engine_task_writes_lineage_row(tmp_data_root, monkeypatch):
    from services.engine import runner
    from services.engine.types import EngineResult
    from services.task.handlers import codex

    def fake_run_engine(req):
        req.on_resume_id("thread-xyz")
        return EngineResult(engine="codex", text="ok", engine_session_id="thread-xyz", num_turns=1,
                            tokens={"total_input_incl_cache": 10, "output": 2})
    monkeypatch.setattr(runner, "run_engine", fake_run_engine)
    q = get_task_queue()
    q.register_handler("P1_CX", codex.codex_task)
    session_store.create(session_id="ws-lin", data={})
    tid = q.submit_task("P1_CX", {"prompt": "x"}, session_id="ws-lin", metadata={"source": "user"})
    assert _wait(lambda: q.get_task(tid).status == TaskStatus.COMPLETED), q.get_task(tid).error
    res = q.get_task(tid).result
    assert res["codex_session_id"] == "thread-xyz" and res["cost_usd"] is None
    rows = sessions_repo.list_sessions(workspace_id="ws-lin")
    assert len(rows) == 1 and rows[0]["engine_session_id"] == "thread-xyz" and rows[0]["kind"] == "task"
    assert session_store.get("ws-lin")["data"]["last_codex_session_id"] == "thread-xyz"


# ── TeleDesign delegates to the runner ──────────────────────────────────────

@pytest.mark.parametrize("engine,is_local,slot_key", [
    ("claude_code", False, "last_claude_session_id"),
    ("codex", False, "last_codex_session_id"),
    ("antigravity", True, "last_antigravity_local_conversation_id"),
])
def test_design_turn_handler_uses_engine_runner(tmp_data_root, tmp_path, monkeypatch, engine, is_local, slot_key):
    from services.design import generate
    from services.engine import runner
    from services.engine.types import EngineResult
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.setattr(generate.store, "project_dir", lambda pid: project)
    seen = {}

    def fake_run_engine(req):
        seen["req"] = req
        req.on_resume_id("new-conv")
        return EngineResult(engine=engine, text="done", engine_session_id="new-conv")
    monkeypatch.setattr(runner, "run_engine", fake_run_engine)
    generate.register_task_type()
    q = get_task_queue()
    session_store.create(session_id="design-chat", namespace="design", data={slot_key: "old-conv"})
    tid = q.submit_task(generate.TASK_TYPE, {"pid": "p1", "chat_id": "c1", "turn_id": "t1", "engine": engine,
                                             "prompt": "make a page", "is_local": is_local, "model": "m"},
                        session_id="design-chat", session_namespace="design", metadata={"source": "design"})
    assert _wait(lambda: q.get_task(tid).status == TaskStatus.COMPLETED), q.get_task(tid).error
    req = seen["req"]
    assert req.engine == engine and req.cwd == project and req.resume_id == "old-conv"
    assert req.is_local is is_local and req.model == "m" and req.prompt == "make a page"
    assert req.log_path.name == f"{tid}.jsonl"                    # the file _drive tails
    assert (req.last_msg_path is not None) == (engine == "codex")
    assert session_store.get("design-chat", namespace="design")["data"][slot_key] == "new-conv"
    assert q.get_task(tid).result["result"] == "done"
