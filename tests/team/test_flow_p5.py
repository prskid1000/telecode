"""P5 end to end with the real task handlers, run executor and Engine Runner;
only the CLIs are fakes (fixtures/engine/fake_agent_cli.py, fake_cli.py):

* OTLP receiver over HTTP (JSON, protobuf, gzip, loopback guard, disabled);
* own GenAI spans (invoke_workflow → invoke_agent → execute_tool) joined by
  run / step ids, the dashboards (summary / timeline / pass^k REST);
* verdicts: outcome check pass / "process ok but task failed";
* cross-engine continue (Claude step → Codex) with its lineage edge;
* approve_tool ↔ the approvals REST inbox (allow with edited input, deny,
  timeout), and the MCP tool registration.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import sys
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services.agent.agent_manager import get_agent_manager
from services.db import writer
from services.db.core import connect
from services.engine.adapters import get_adapter
from services.job.job_manager import get_job_manager
from services.run import executor
from services.run.run_store import get_run_store
from services.session import session_store
from services.task.task_manager import get_task_queue
from services.telemetry import protobuf

ENG = Path(__file__).parent / "fixtures" / "engine"
FAKE = ENG / "fake_agent_cli.py"
FAKE_CLI = ENG / "fake_cli.py"
OTLP = Path(__file__).parent / "fixtures" / "otlp"
TERMINAL = ("completed", "failed", "partial", "cancelled", "interrupted", "budget_exceeded", "rejected")
HO = lambda **kw: json.dumps({"status": "done", "summary": "built the thing", "decisions": ["use sqlite"],  # noqa: E731
                              "artifacts": [], "open_questions": [], "next_steps": ["ship it"], "verdict": "pass", **kw})


@pytest.fixture
def env(tmp_data_root, monkeypatch):
    import config
    from services.task.task_registry import register_default_tasks
    q = get_task_queue()
    saved = dict(q.task_handlers)
    register_default_tasks()
    monkeypatch.setattr(config, "tasks_kill_grace_seconds", lambda: 0.5)
    calls = {"argv": tmp_data_root / "argv.jsonl", "prompts": tmp_data_root / "prompts.jsonl"}
    yield {"root": tmp_data_root, "calls": calls, "monkeypatch": monkeypatch}
    q.task_handlers.clear()
    q.task_handlers.update(saved)


def fake_claude(env, script):
    ad = get_adapter("claude_code")
    orig = type(ad).build

    def build(req):
        launch = orig(ad, req)
        env.setdefault("envs", []).append(dict(launch.env or {}))
        launch.argv = [sys.executable, str(FAKE), "--argv-out", str(env["calls"]["argv"]),
                       "--prompt-out", str(env["calls"]["prompts"]), *[str(a) for a in script(req)], "--",
                       *launch.argv[1:]]
        return launch
    env["monkeypatch"].setattr(ad, "build", build)


def fake_codex(env, stdin_out: Path):
    ad = get_adapter("codex")
    orig = type(ad).build

    def build(req):
        launch = orig(ad, req)
        env["codex_argv"] = list(launch.argv)
        launch.argv = [sys.executable, str(FAKE_CLI), "--replay", str(ENG / "codex_tools.jsonl"),
                       "--stdin-out", str(stdin_out)]
        return launch
    env["monkeypatch"].setattr(ad, "build", build)


def wait_run(run_id, deadline=30):
    end = time.time() + deadline
    rs = get_run_store()
    while time.time() < end:
        r = rs.get_run(run_id)
        if r and r["status"] in TERMINAL and not executor.is_active(run_id) and r.get("verdict"):
            return r
        time.sleep(0.05)
    return rs.get_run(run_id)


def wait_task(tid, deadline=30):
    q = get_task_queue()
    end = time.time() + deadline
    while time.time() < end:
        t = q.get_task(tid)
        if t and t.status.value in ("completed", "failed", "cancelled"):
            return t
        time.sleep(0.05)
    return q.get_task(tid)


def setup_job(ws, **job):
    a = get_agent_manager().create_agent("p5-bot", soul="s")
    if not session_store.exists(ws):
        session_store.create(session_id=ws, data={"name": ws})
    j = get_job_manager().create_job({"title": job.pop("title", "p5 job"), "workspace_id": ws, "task_description": "go",
                                      "pipeline": {"mode": "single", "steps": [{"agent_id": a["id"]}]}, **job})
    return j, a, session_store._session_dir(ws)


def _app():
    from proxy import api_approvals, api_continue, api_runs, api_sessions, api_telemetry
    app = web.Application()
    for m in (api_runs, api_sessions, api_telemetry, api_continue, api_approvals):
        m.register_routes(app)
    return app


async def _with_client(fn):
    c = TestClient(TestServer(_app()))
    await c.start_server()
    try:
        return await fn(c)
    finally:
        await c.close()


def rows(sql, *args):
    writer.flush()
    return [dict(r) for r in connect().execute(sql, args)]


# ── OTLP receiver over HTTP ─────────────────────────────────────────────────

def test_receiver_json_protobuf_gzip_and_guards(tmp_data_root, monkeypatch):
    logs = json.loads((OTLP / "claude_logs.json").read_text(encoding="utf-8"))
    mets = json.loads((OTLP / "claude_metrics.json").read_text(encoding="utf-8"))
    from proxy import api_telemetry

    async def go(c):
        r = await c.post("/otlp/v1/logs", json=logs)
        assert r.status == 200 and (await r.json()) == {"partialSuccess": {}}
        pb = protobuf.encode_request("metrics", mets)
        r = await c.post("/otlp/v1/metrics", data=pb, headers={"Content-Type": "application/x-protobuf"})
        assert r.status == 200 and r.content_type == "application/x-protobuf" and (await r.read()) == b""
        r = await c.post("/otlp/v1/metrics", data=gzip.compress(json.dumps(mets).encode()),
                         headers={"Content-Type": "application/json", "Content-Encoding": "gzip"})
        assert r.status == 200
        assert (await c.post("/otlp/v1/logs", data=b"hi", headers={"Content-Type": "text/plain"})).status == 415
        assert (await c.post("/otlp/v1/logs", data=b"{nope", headers={"Content-Type": "application/json"})).status == 400
        assert (await c.post("/otlp/v1/bogus", json={})).status == 404
        real = api_telemetry.is_loopback
        monkeypatch.setattr(api_telemetry, "is_loopback", lambda remote: False)
        assert (await c.post("/otlp/v1/logs", json=logs)).status == 403
        monkeypatch.setattr(api_telemetry, "is_loopback", real)
        st = await (await c.get("/api/telemetry/status")).json()
        return st
    st = asyncio.run(_with_client(go))
    assert st["counts"]["log_events"]["rows"] == len(rows("SELECT id FROM log_events"))
    pts = rows("SELECT value FROM metric_points WHERE name='claude_code.cost.usage'")
    assert round(sum(p["value"] for p in pts), 7) == round(2 * 0.0577632, 7)      # protobuf + gzip copies


def test_is_loopback():
    from proxy.api_telemetry import is_loopback
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("::ffff:127.0.0.1")
    assert not is_loopback("192.168.1.5") and not is_loopback(None) and not is_loopback("example.com")


def test_receiver_drops_when_disabled(tmp_data_root, monkeypatch):
    import config
    real = config.get_nested
    monkeypatch.setattr(config, "get_nested", lambda k, d=None: False if k == "telemetry.enabled" else real(k, d))
    logs = json.loads((OTLP / "claude_logs.json").read_text(encoding="utf-8"))

    async def go(c):
        return (await c.post("/otlp/v1/logs", json=logs)).status
    assert asyncio.run(_with_client(go)) == 200
    assert rows("SELECT COUNT(*) AS n FROM log_events")[0]["n"] == 0


# ── spans + verdicts + dashboards (a real run through the executor) ─────────

def test_run_spans_verdict_outcome_and_dashboards(env):
    fake_claude(env, lambda req: ["--session", "S-P5", "--tool", "Bash=ls", "--tool", "Read", "--model", "claude-haiku-9",
                                  "--write", "PROGRESS.md=half done\\n", "--handoff", HO()])
    ok_cmd = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
    j, agent, ws = setup_job("ws-p5", outcome_check={"command": ok_cmd})
    run = asyncio.run(executor.start_run(job=j, source="user"))
    r = wait_run(run["run_id"])
    assert r["status"] == "completed" and r["process_ok"] is True
    assert (r["verdict"], r["verdict_source"]) == ("pass", "outcome_check")
    assert r["outcome_check"]["exit_code"] == 0 and r["outcome_check"]["passed"] is True
    step = r["steps"][0]

    # the CLI got OTel env tagged with this run/step
    ra = env["envs"][0]["OTEL_RESOURCE_ATTRIBUTES"]
    assert f"telecode.run_id={r['run_id']}" in ra and f"telecode.step_id={step['step_id']}" in ra
    assert f"telecode.agent_id={agent['id']}" in ra and env["envs"][0]["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"

    sp = rows("SELECT * FROM spans WHERE run_id=? ORDER BY start_ms", r["run_id"])
    wf = [s for s in sp if s["operation"] == "invoke_workflow"]
    ag = [s for s in sp if s["operation"] == "invoke_agent"]
    tools = [s for s in sp if s["operation"] == "execute_tool"]
    assert len(wf) == 1 and wf[0]["status"] == "ok" and wf[0]["job_id"] == j["id"]
    assert len(ag) == 1 and ag[0]["parent_span_id"] == wf[0]["span_id"] and ag[0]["trace_id"] == wf[0]["trace_id"]
    assert ag[0]["step_id"] == step["step_id"] and ag[0]["agent_id"] == agent["id"] and ag[0]["status"] == "ok"
    assert ag[0]["model"] == "claude-haiku-9" and ag[0]["cost_usd"] == 0.0123 and ag[0]["cache_read_tokens"] == 1000
    assert json.loads(ag[0]["attributes"])["gen_ai.operation.name"] == "invoke_agent"
    assert sorted(t["tool_name"] for t in tools) == ["Bash", "Read"]
    assert all(t["parent_span_id"] == ag[0]["span_id"] and t["end_ms"] is not None for t in tools)

    # a second job: the process completes but the outcome check fails
    fail_cmd = f'"{sys.executable}" -c "import sys; print(\'tests failed\'); sys.exit(3)"'
    j2, _, _ = setup_job("ws-p5b", title="p5 failing", outcome_check=fail_cmd)
    r2 = wait_run(asyncio.run(executor.start_run(job=j2, source="user"))["run_id"])
    assert r2["status"] == "completed" and r2["process_ok"] is True and r2["verdict"] == "fail"
    assert r2["outcome_check"]["exit_code"] == 3 and "tests failed" in r2["outcome_check"]["output"]

    async def go(c):
        s_job = await (await c.get("/api/telemetry/summary?group=job&since=1d")).json()
        s_model = await (await c.get("/api/telemetry/summary?group=model&since=1d")).json()
        s_eng = await (await c.get("/api/telemetry/summary?group=engine")).json()
        bad = await c.get("/api/telemetry/summary?group=planet")
        tl = await (await c.get(f"/api/telemetry/runs/{r['run_id']}/timeline")).json()
        missing = await c.get("/api/telemetry/runs/nope/timeline")
        tt = await (await c.get(f"/api/telemetry/tasks/{step['task_id']}")).json()
        return s_job, s_model, s_eng, bad.status, tl, missing.status, tt
    s_job, s_model, s_eng, bad, tl, missing, tt = asyncio.run(_with_client(go))
    assert bad == 400 and missing == 404
    assert s_job["totals"]["attempts"] == 2 and round(s_job["totals"]["cost_usd"], 4) == 0.0246
    assert s_job["runs"]["process_ok"] == 2 and s_job["runs"]["pass"] == 1 and s_job["runs"]["ok_but_fail"] == 1
    by_job = {g["label"]: g for g in s_job["groups"]}
    assert set(by_job) == {"p5 job", "p5 failing"}
    assert by_job["p5 failing"]["runs"]["success_rate"] == 0.0 and by_job["p5 job"]["runs"]["success_rate"] == 1.0
    assert s_model["groups"][0]["key"] == "claude-haiku-9"
    assert s_eng["groups"][0]["label"] == "Claude Code" and s_eng["groups"][0]["failure_rate"] == 0.0
    assert {t["name"] for t in s_job["top_tools"]} == {"Bash", "Read"}
    assert s_job["totals"]["cache_read_ratio"] == round(2000 / (2 * 1150), 4)
    assert s_job["by_day"] and s_job["by_day"][-1]["attempts"] == 2
    t = tl["timeline"]
    assert t["verdict"] == "pass" and t["verdict_source"] == "outcome_check" and t["phases"][0]["steps"][0]["attempts"]
    a0 = t["phases"][0]["steps"][0]["attempts"][0]
    assert a0["start_ms"] and a0["end_ms"] >= a0["start_ms"] and a0["tools"] == 2 and a0["model"] == "claude-haiku-9"
    assert t["phases"][0]["steps"][0]["cost_usd"] == 0.0123
    assert {s["operation"] for s in tt["spans"]} == {"invoke_agent", "execute_tool"}
    env["run"] = r


def test_cancelled_run_verdict_unknown(env):
    fake_claude(env, lambda req: ["--hang-sec", "20"])
    j, _, _ = setup_job("ws-p5c", outcome_check="exit 0")
    run = asyncio.run(executor.start_run(job=j, source="user"))
    end = time.time() + 15
    while time.time() < end and get_run_store().get_run(run["run_id"])["steps"][0].get("status") != "running":
        time.sleep(0.05)
    time.sleep(0.5)
    executor.cancel_run(run["run_id"])
    r = wait_run(run["run_id"])
    assert r["status"] == "cancelled" and r["verdict"] == "unknown" and "outcome_check" not in r
    end = time.time() + 15      # the worker thread closes the span once the CLI tree is gone
    while time.time() < end:
        ag = rows("SELECT status, status_message FROM spans WHERE run_id=? AND operation='invoke_agent'", r["run_id"])
        if ag and ag[0]["status"] != "unset":
            break
        time.sleep(0.1)
    assert ag and ag[0]["status"] == "error" and "cancel" in (ag[0]["status_message"] or "").lower()


# ── cross-engine continue ───────────────────────────────────────────────────

def test_continue_claude_step_on_codex_with_lineage(env):
    fake_claude(env, lambda req: ["--session", "S-CL", "--write", "app.py=print(1)\\n", "--write",
                                  "PROGRESS.md=- [x] scaffold\\n- [ ] tests\\n", "--handoff", HO(summary="scaffolded app.py")])
    j, agent, ws = setup_job("ws-p5x")
    r = wait_run(asyncio.run(executor.start_run(job=j, source="user"))["run_id"])
    assert r["status"] == "completed"
    step = r["steps"][0]
    stdin_out = env["root"] / "codex_stdin.txt"
    fake_codex(env, stdin_out)

    async def go(c):
        busy_before = await c.post("/api/sessions/nope-ws/continue", json={"engine": "codex"})
        bad = await c.post("/api/sessions/ws-p5x/continue", json={"engine": "cobol"})
        res = await c.post("/api/sessions/ws-p5x/continue",
                           json={"engine": "codex", "run_id": r["run_id"], "step_id": step["step_id"],
                                 "prompt": "Now write the tests."})
        return busy_before.status, bad.status, res.status, await res.json()
    missing, bad, status, body = asyncio.run(_with_client(go))
    assert missing == 404 and bad == 400 and status == 202, body
    assert body["from_engine"] == "claude_code" and body["to_engine"] == "codex" and body["handoff_source"] == "run_step"
    assert body["progress_md"] is True and body["files_changed"] >= 2
    t = wait_task(body["task_id"])
    assert t.status.value == "completed", t.error
    prompt = stdin_out.read_text(encoding="utf-8")
    assert prompt.startswith("<engine_switch>") and "scaffolded app.py" in prompt and "Now write the tests." in prompt
    assert "added: app.py" in prompt and "- [ ] tests" in prompt and "<workspace_diff>" in prompt
    assert (ws / body["package"]["md"]).is_file() and (ws / body["package"]["json"]).is_file()
    assert "resume" not in env["codex_argv"]                             # a fresh conversation
    lin = rows("SELECT * FROM sessions_index WHERE workspace_id='ws-p5x' ORDER BY created_at")
    cl = next(x for x in lin if x["engine"] == "claude_code")
    cx = next(x for x in lin if x["engine"] == "codex")
    assert cx["lineage"] == "engine_switch" and cx["switched_from"] == cl["id"] and body["switched_from"] == cl["id"]
    assert t.metadata["continued_from"]["engine"] == "claude_code"
    # the Task-mode continue task is an invoke_agent span too (no run)
    sp = rows("SELECT * FROM spans WHERE task_id=? AND operation='invoke_agent'", body["task_id"])
    assert sp and sp[0]["run_id"] is None and sp[0]["engine"] == "codex" and sp[0]["parent_span_id"] is None


def test_continue_refuses_while_a_task_runs(env, monkeypatch):
    from services.engine import handover
    session_store.create(session_id="ws-busy", data={})
    monkeypatch.setattr(get_task_queue(), "session_has_active_task", lambda sid, ns=None: True)
    with pytest.raises(handover.SessionBusy):
        handover.continue_session("ws-busy", engine="codex")


def test_continue_from_task_mode_uses_last_reply(env, tmp_data_root):
    from services.engine import handover
    session_store.create(session_id="ws-t", data={})
    connect().execute("INSERT INTO tasks (task_id, task_type, session_id, status, completed_at, result) VALUES (?,?,?,?,?,?)",
                      ("tk1", "CODEX", "ws-t", "completed", "2026-09-25T10:00:00Z",
                       json.dumps({"result": "I refactored db.py; next: add migrations."})))
    ho = handover.latest_handoff("ws-t")
    assert ho["source"] == "task" and "refactored db.py" in ho["last_reply"]
    pkg = handover.build_package("ws-t", None, target_engine="claude_code")
    assert pkg["from_engine"] == "codex"
    text = handover.render_prompt(pkg)
    assert "<last_reply>" in text and handover.DEFAULT_ASK in text


# ── approve_tool ↔ approvals inbox ──────────────────────────────────────────

def test_approve_tool_roundtrip_via_rest(tmp_data_root, monkeypatch):
    from mcp_server.tools import approvals as tool
    monkeypatch.setattr(tool, "POLL_SEC", 0.05)
    hdr = {"x-telecode-task": "T-1", "x-telecode-run": "R-1", "x-telecode-step": "S-1"}

    async def go(c):
        async def decide_when_pending(action, body):
            for _ in range(200):
                lst = await (await c.get("/api/approvals")).json()
                if lst["approvals"]:
                    ap = lst["approvals"][0]
                    r = await c.post(f"/api/approvals/{ap['id']}/{action}", json=body)
                    return ap, r.status
                await asyncio.sleep(0.02)
            raise AssertionError("no approval appeared")
        waiter = asyncio.create_task(tool.request_approval("Bash", {"command": "rm -rf build"}, "tu1", hdr, timeout=10))
        ap, st = await decide_when_pending("approve", {"edited_text": '{"command": "rm -rf build/tmp"}', "by": "tester"})
        allow = await waiter
        waiter = asyncio.create_task(tool.request_approval("Write", {"file_path": "x"}, "", {}, timeout=10))
        ap2, st2 = await decide_when_pending("reject", {"note": "not today", "by": "tester"})
        deny = await waiter
        timed = await tool.request_approval("Edit", {"file_path": "y"}, "", {}, timeout=0.3)
        hist = await (await c.get("/api/approvals?status=all")).json()
        return ap, st, allow, st2, deny, timed, hist
    ap, st, allow, st2, deny, timed, hist = asyncio.run(_with_client(go))
    assert st == 200 and ap["kind"] == "tool" and ap["run_id"] == "R-1" and ap["step_id"] == "S-1"
    assert ap["payload"]["task_id"] == "T-1" and "rm -rf build" in ap["body"] and ap["title"] == "Allow Bash?"
    assert allow == {"behavior": "allow", "updatedInput": {"command": "rm -rf build/tmp"}}
    assert st2 == 200 and deny == {"behavior": "deny", "message": "Denied by tester: not today"}
    assert timed["behavior"] == "deny" and "within" in timed["message"]
    statuses = sorted(a["status"] for a in hist["approvals"])
    assert statuses == ["approved", "cancelled", "rejected"]


def test_approve_tool_is_a_registered_mcp_tool(tmp_data_root, monkeypatch):
    from mcp_server.app import mcp_app
    from mcp_server.tools import approvals as tool
    monkeypatch.setattr(tool, "_timeout", lambda: 0.2)
    monkeypatch.setattr(tool, "POLL_SEC", 0.05)

    async def go():
        names = [t.name for t in await mcp_app.list_tools()]
        out = await mcp_app.call_tool("approve_tool", {"tool_name": "Bash", "input": {"command": "ls"}, "tool_use_id": "x"})
        return names, out
    names, out = asyncio.run(go())
    assert "approve_tool" in names
    blocks = out[0] if isinstance(out, tuple) else out
    text = blocks[0].text
    assert json.loads(text)["behavior"] == "deny"


# ── lifetime Job creation race (found while seeding a parallel phase) ───────

@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Objects")
def test_concurrent_first_spawns_share_one_lifetime_job(monkeypatch):
    import threading
    import process as tc_process
    from services.engine import spawn as sp
    keep = tc_process._JOB_HANDLE                 # stays referenced: closing it would kill its members
    monkeypatch.setattr(tc_process, "_JOB_HANDLE", None)
    monkeypatch.setattr(tc_process, "_TRACKED_PIDS", set())
    created = []
    real = tc_process._create_job_locked

    def counting():
        created.append(1)
        return real()
    monkeypatch.setattr(tc_process, "_create_job_locked", counting)
    errs = []

    def one():
        try:
            s = sp.spawn([sys.executable, "-c", "print(1)"], cwd=Path("."))
            s.proc.communicate(timeout=30)
            s.close()
        except Exception as exc:     # "could not resume … after binding it to the Job" before the fix
            errs.append(str(exc))
    ts = [threading.Thread(target=one) for _ in range(12)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    new_job = tc_process._JOB_HANDLE
    assert errs == [] and len(created) == 1 and new_job is not None
    assert tc_process._JOB_HANDLE is new_job and keep is not new_job
