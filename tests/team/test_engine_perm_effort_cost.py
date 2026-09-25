"""Permission mode + effort for every engine (incl. TeleDesign turns) and the
shared per-run cost correction (services/engine/cost.py).

Claude reports ``total_cost_usd`` cumulatively across a resumed session; the
runner subtracts the conversation's last recorded total, so Task / run / trigger
/ design consumers all see per-run cost. The fake CLI replays a recorded stream
with the result's ``total_cost_usd`` rewritten to a growing cumulative figure.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

import config
from services.engine import EngineRequest
from services.engine.adapters import get_adapter
from services.engine.adapters import antigravity as agy_ad
from services.engine.adapters import claude as claude_ad
from services.engine.adapters import codex as codex_ad
from services.engine.cost import per_run_cost
from services.engine.runner import run_engine

FIX = Path(__file__).parent / "fixtures" / "engine"
FAKE = FIX / "fake_cli.py"
SID = "b91e2083-0212-41b0-97c7-7717c48820af"      # the session id in claude_stream.jsonl


def _replay(tmp_path: Path, cost: float, sid: str = SID) -> Path:
    out = tmp_path / f"replay-{sid[:4]}-{cost}.jsonl"
    lines = []
    for ln in (FIX / "claude_stream.jsonl").read_text(encoding="utf-8").splitlines():
        evt = json.loads(ln)
        if "session_id" in evt:
            evt["session_id"] = sid
        if evt.get("type") == "result":
            evt["total_cost_usd"] = cost
        lines.append(json.dumps(evt))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


class _Fake:
    """Swap argv for the fake CLI replaying ``replays`` in order; keep the argv."""

    def __init__(self, monkeypatch, engine, replays):
        self.replays, self.launches = list(replays), []
        ad = get_adapter(engine)
        orig = type(ad).build

        def build(req):
            launch = orig(ad, req)
            self.launches.append(list(launch.argv))
            launch.argv = [sys.executable, str(FAKE), "--replay", str(self.replays[len(self.launches) - 1])]
            return launch
        monkeypatch.setattr(ad, "build", build)


# ── per-run cost ────────────────────────────────────────────────────────────

def test_per_run_cost():
    assert per_run_cost(1.0392322, 0.9161136) == pytest.approx(0.1231186, abs=1e-6)
    assert per_run_cost(0.5, None) == 0.5
    assert per_run_cost(0.5, 0.0) == 0.5
    assert per_run_cost(0.5, 0.9) == 0.5          # below the base: already the run's own figure
    assert per_run_cost(None, 0.3) is None        # Codex / agy report no cost


def test_runner_records_per_run_cost_across_resumes(tmp_data_root, tmp_path, monkeypatch):
    fake = _Fake(monkeypatch, "claude_code",
                 [_replay(tmp_path, 0.36), _replay(tmp_path, 0.39), _replay(tmp_path, 0.05, sid="new-sid")])
    events = []

    def req(resume_id=None, **kw):
        return EngineRequest(engine="claude_code", prompt="hi", cwd=tmp_path, resume_id=resume_id,
                             on_event=events.append, **kw)

    r1 = run_engine(req())
    assert r1.cost_usd == pytest.approx(0.36) and r1.cost_total_usd == pytest.approx(0.36)

    events.clear()
    r2 = run_engine(req(SID))
    assert r2.cost_usd == pytest.approx(0.03)                 # 0.39 cumulative − 0.36 before
    assert r2.cost_total_usd == pytest.approx(0.39)
    usage = [e for e in events if e["kind"] == "usage" and not e.get("partial")][-1]
    done = [e for e in events if e["kind"] == "done"][-1]
    assert usage["cost_usd"] == pytest.approx(0.03) and done["cost_usd"] == pytest.approx(0.03)
    assert r2.to_dict("ws")["cost_usd"] == pytest.approx(0.03)

    # The resume could not continue and the CLI started over (new id): its own figure,
    # even when it is above the old conversation's base.
    fake.replays[2] = _replay(tmp_path, 0.50, sid="new-sid")
    r3 = run_engine(req(SID))
    assert r3.cost_usd == pytest.approx(0.50)
    from services.db import sessions_repo
    assert sessions_repo.cost_total("claude_code", SID) == pytest.approx(0.39)
    assert sessions_repo.cost_total("claude_code", "new-sid") == pytest.approx(0.50)
    assert len(fake.launches) == 3


def test_caller_hint_is_only_a_fallback(tmp_data_root, tmp_path, monkeypatch):
    _Fake(monkeypatch, "claude_code", [_replay(tmp_path, 0.50)])
    r = run_engine(EngineRequest(engine="claude_code", prompt="hi", cwd=tmp_path, resume_id=SID,
                                 cost_base_usd=0.45))
    assert r.cost_usd == pytest.approx(0.05)


def test_task_mode_lineage_sums_per_run_cost(tmp_data_root, tmp_path, monkeypatch):
    """Two Task-mode CLAUDE_CODE submits in one workspace: the task results and
    the sessions_index row carry per-run cost (0.36, then 0.03 → 0.39 total)."""
    from services.task.handlers.claude_code import claude_code_task
    from services.task.task_manager import TaskStatus, get_task_queue

    _Fake(monkeypatch, "claude_code", [_replay(tmp_path, 0.36), _replay(tmp_path, 0.39)])
    q = get_task_queue()
    q.register_handler("CLAUDE_CODE", claude_code_task)

    def submit(md=None):
        tid = q.submit_task("CLAUDE_CODE", {"prompt": "hi"}, metadata=md or {}, session_id="ws-cost")
        for _ in range(400):
            t = q.get_task(tid)
            if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                return t
            import time
            time.sleep(0.05)
        raise AssertionError("task did not finish")

    t1, t2 = submit(), submit({"effort": "high", "permission_mode": "plan"})
    assert t1.status == TaskStatus.COMPLETED, t1.error
    assert t2.status == TaskStatus.COMPLETED, t2.error
    assert t1.result["cost_usd"] == pytest.approx(0.36)
    assert t2.result["cost_usd"] == pytest.approx(0.03)
    start = [e for e in t2.metadata["events"] if e.get("kind") == "start"][-1]
    assert start["effort"] == "high" and start["permission_mode"] == "plan"
    from services.db import sessions_repo
    row = sessions_repo.find_by_engine_session(SID)
    assert row["cumulative_cost_usd"] == pytest.approx(0.39) and row["runs_count"] == 2


# ── effort → CLI flags ──────────────────────────────────────────────────────

def test_effort_flags_per_engine(tmp_path):
    assert claude_ad.effort_args("xhigh") == ["--effort", "xhigh"]
    assert claude_ad.effort_args(None) == [] and claude_ad.effort_args("bogus") == []
    argv = claude_ad.build_argv(resume_id="S", model="haiku", is_local=False, effort="max")
    assert argv[argv.index("--effort") + 1] == "max"

    argv = codex_ad.build_argv(work_dir=tmp_path, resume_id="T", last_msg_path=tmp_path / "m.txt",
                               model=None, effort="high")
    i = argv.index("model_reasoning_effort=high")
    assert argv[i - 1] == "-c" and i < argv.index("resume")   # -c overrides precede the subcommand
    assert "model_reasoning_effort" not in " ".join(
        codex_ad.build_argv(work_dir=tmp_path, resume_id=None, last_msg_path=tmp_path / "m.txt", model=None))

    assert agy_ad.effort_args("xhigh") == ["--effort", "high"]     # agy has no xhigh
    argv = agy_ad.build_argv(work_dir=tmp_path, resume_id=None, effort="low")
    assert argv[argv.index("--effort") + 1] == "low" and argv[-1] == "-p="


def test_normalize_effort_and_job_step_and_trigger():
    from services.engine.types import normalize_effort
    assert normalize_effort("") == "" and normalize_effort("xhigh") == "xhigh"
    with pytest.raises(ValueError):
        normalize_effort("extreme")
    from services.job.job_manager import _normalize_pipeline
    pipe = _normalize_pipeline({"mode": "single", "steps": [{"agent_id": "a1", "effort": "low"}]})
    assert pipe["steps"][0]["effort"] == "low"
    from services.run.executor import _resolve_step_config
    assert _resolve_step_config({"effort": ""}, {"effort": "high"}, {})["effort"] == "high"
    assert _resolve_step_config({"effort": "low"}, {"effort": "high"}, {})["effort"] == "low"


# ── permission modes: the Claude ask path next to TeleDesign's own --mcp-config ─

def test_ask_mode_merges_into_callers_mcp_config(tmp_data_root, tmp_path, monkeypatch):
    from services.telemetry import settings as p5
    monkeypatch.setattr(p5, "mcp_enabled", lambda: True)
    monkeypatch.setattr(p5, "mcp_url", lambda: "http://127.0.0.1:18766/mcp")
    monkeypatch.setattr(p5, "approval_timeout_sec", lambda: 60)
    design_cfg = tmp_path / "claude-mcp.json"
    req = EngineRequest(engine="claude_code", prompt="p", cwd=tmp_path, permission_mode="ask",
                        extra_args=["--strict-mcp-config", "--mcp-config", str(design_cfg), "--tools", "Read"])
    launch = get_adapter("claude_code").build(req)
    argv = launch.argv
    assert argv.count("--mcp-config") == 1
    i = argv.index("--mcp-config")
    assert argv[i + 1] == str(design_cfg) and "mcp-approve-" in argv[i + 2]
    assert argv[argv.index("--permission-prompt-tool") + 1] == claude_ad.APPROVE_TOOL
    for p in launch.cleanup:
        p.unlink(missing_ok=True)


# ── TeleDesign: permission mode + effort reach the CLI; per-turn cost is shared ─

@pytest.fixture
def design_settings(monkeypatch):
    raw = {"design": {"local_helpers": False, "verifier": {"enabled": False}},
           "mcp_server": {"enabled": False}, "proxy": {"enabled": False},
           "llamacpp": {"enabled": False}, "telemetry": {"enabled": False}}
    monkeypatch.setattr(config, "_raw", raw)
    return raw


def test_design_turns_permission_effort_and_cost(design_settings, tmp_data_root, tmp_path, monkeypatch):
    from services.design import chats as dchats, generate, store

    fake = _Fake(monkeypatch, "claude_code", [_replay(tmp_path, 0.36), _replay(tmp_path, 0.39),
                                              _replay(tmp_path, 0.40)])
    monkeypatch.setattr(generate, "engine_available", lambda e: True)

    async def no_post(*a, **k):
        return None
    monkeypatch.setattr(generate, "_post_turn", no_post)
    generate.register_task_type()

    project = store.create_project({"title": "Perm", "kind": "prototype"})
    chat = dchats.create_chat(project["id"], {})
    pid, cid = project["id"], chat["id"]
    assert chat["permission_mode"] is None

    async def turn(body):
        out = await generate.start_turn(pid, cid, body)
        for _ in range(400):
            if (pid, cid) not in generate._chat_running:
                break
            await asyncio.sleep(0.05)
        return [t for t in dchats.read_turns(pid, cid) if t["id"] == out["turn"]["id"]][-1]

    t1 = asyncio.run(turn({"text": "Build a tiny page."}))
    assert t1["status"] == "done", t1.get("error")
    a1 = fake.launches[0]
    assert a1[a1.index("--permission-mode") + 1] == "acceptEdits"          # the design default
    assert "--dangerously-skip-permissions" not in a1 and "--effort" not in a1
    assert t1["permission_mode"] == "acceptEdits" and t1["usage"]["cost_usd"] == pytest.approx(0.36)

    dchats.update_chat(pid, cid, {"permission_mode": "plan", "effort": "high"})
    t2 = asyncio.run(turn({"text": "Change the heading."}))
    a2 = fake.launches[1]
    assert a2[a2.index("--permission-mode") + 1] == "plan" and a2[a2.index("--effort") + 1] == "high"
    assert "--resume" in a2
    assert t2["usage"]["cost_usd"] == pytest.approx(0.03)                    # shared correction

    # A turn body overrides the chat; "skip" = --dangerously-skip-permissions.
    t3 = asyncio.run(turn({"text": "Again.", "permission_mode": "skip", "effort": "low"}))
    a3 = fake.launches[2]
    assert "--dangerously-skip-permissions" in a3 and a3[a3.index("--effort") + 1] == "low"
    assert t3["usage"]["cost_usd"] == pytest.approx(0.01)

    with pytest.raises(ValueError):
        dchats.update_chat(pid, cid, {"permission_mode": "bogus"})
    with pytest.raises(ValueError):
        asyncio.run(generate.start_turn(pid, cid, {"text": "x", "permission_mode": "bogus"}))


def test_design_default_setting(design_settings):
    from services.design import chats as dchats
    assert dchats.default_permission_mode() == "acceptEdits"
    design_settings["design"]["permission_mode"] = "auto"
    assert dchats.default_permission_mode() == "auto"
    design_settings["design"]["permission_mode"] = "nonsense"
    assert dchats.default_permission_mode() == "acceptEdits"
