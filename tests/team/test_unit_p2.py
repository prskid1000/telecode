"""P2 units: handoff validation/derivation/rendering, budgets, shadow-git
snapshots, adapter argv for fork / --max-budget-usd, Claude live usage and
budget results, runner token/wall-clock caps."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from services import snapshots
from services.engine import EngineBudgetExceeded, EngineRequest
from services.engine.adapters import get_adapter
from services.engine.adapters.base import ParseState
from services.engine.runner import run_engine
from services.run import budget, handoff

FIX = Path(__file__).parent / "fixtures" / "engine"
FAKE = FIX / "fake_agent_cli.py"

GOOD = {"status": "done", "summary": "wrote the plan", "decisions": ["use sqlite"],
        "artifacts": [{"path": "out/plan.md", "kind": "doc", "description": "the plan"}],
        "open_questions": [], "next_steps": ["review"], "verdict": "pass"}


# ── handoff ────────────────────────────────────────────────────────────────

def test_handoff_schema_is_strict_for_codex():
    s = handoff.HANDOFF_SCHEMA
    assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
    art = s["properties"]["artifacts"]["items"]
    assert art["additionalProperties"] is False and set(art["required"]) == set(art["properties"])


def test_handoff_validate_normalises_and_rejects(tmp_path):
    ho, problems = handoff.validate(GOOD, tmp_path)
    assert ho["status"] == "done" and ho["artifacts"][0]["path"] == "out/plan.md" and not problems
    # lenient shape: string list items, absolute path inside cwd, JSON string input
    raw = {**GOOD, "decisions": "one decision", "artifacts": [{"path": str(tmp_path / "a" / "b.txt")},
                                                              {"path": "../escape.txt"}]}
    ho, problems = handoff.validate(json.dumps(raw), tmp_path)
    assert ho["decisions"] == ["one decision"]
    assert [a["path"] for a in ho["artifacts"]] == ["a/b.txt"]
    assert any("outside" in p for p in problems)
    for bad in ("nope", {"summary": "x", "status": "finished", "verdict": "pass"}, {**GOOD, "summary": ""}, 3):
        assert handoff.validate(bad, tmp_path)[0] is None


def test_handoff_resolve_derives_when_missing_or_invalid(tmp_path):
    d = handoff.resolve(None, "final words", step_status="completed", error=None, work_dir=tmp_path)
    assert d["derived"] and d["status"] == "unknown" and d["summary"] == "final words"
    d = handoff.resolve({"status": "bogus"}, "txt", step_status="completed", error=None, work_dir=tmp_path)
    assert d["derived"] and "invalid structured handoff" in d["derive_reason"]
    d = handoff.resolve(None, "", step_status="failed", error="boom", work_dir=tmp_path)
    assert d["status"] == "failed" and d["verdict"] == "fail" and "boom" in d["summary"]


def test_handoff_block_renders_artifacts_and_files():
    ho = {**GOOD, "artifacts": [{"path": "out/plan.md", "kind": "doc", "description": "the plan",
                                 "stored_path": "C:/x/out/plan.md"}]}
    block = handoff.render_prev([{"name": "Planner", "handoff": ho,
                                  "files_changed": [{"path": "out/plan.md", "change": "added"}]}])
    assert block.startswith('<handoff from="Planner" status="done" verdict="pass">')
    assert "- C:/x/out/plan.md (doc) — the plan [workspace path: out/plan.md]" in block
    assert "<decisions>\n- use sqlite\n</decisions>" in block and "added: out/plan.md" in block
    two = handoff.render_prev([{"name": "a", "handoff": ho}, {"name": "b", "handoff": ho}])
    assert two.startswith("<handoffs>") and two.count("<handoff from=") == 2


def test_collect_artifacts_copies_declared_and_ephemeral_changes(tmp_data_root, tmp_path):
    wd = tmp_path / "wd"
    (wd / "out").mkdir(parents=True)
    (wd / "out" / "plan.md").write_text("P", encoding="utf-8")
    (wd / "extra.txt").write_text("E", encoding="utf-8")
    ho = {**GOOD, "artifacts": [dict(GOOD["artifacts"][0]), {"path": "gone.md", "kind": "doc", "description": ""}]}
    arts = handoff.collect_artifacts("run1", "step1", wd, ho,
                                     [{"path": "extra.txt", "change": "added"}], include_changed=True)
    by = {a["path"]: a for a in arts}
    assert Path(by["out/plan.md"]["stored_path"]).read_text() == "P"
    assert by["gone.md"]["missing"] is True
    assert by["extra.txt"]["undeclared"] and Path(by["extra.txt"]["stored_path"]).read_text() == "E"
    assert handoff.artifacts_dir("run1", "step1") == tmp_data_root / "data" / "runs" / "run1" / "artifacts" / "step1"


def test_agy_handoff_file_is_read_and_removed(tmp_path):
    (tmp_path / ".telecode").mkdir()
    (tmp_path / ".telecode" / "handoff.json").write_text(json.dumps(GOOD), encoding="utf-8")
    assert handoff.read_agy_file(tmp_path)["summary"] == "wrote the plan"
    assert not (tmp_path / ".telecode" / "handoff.json").exists()
    assert handoff.read_agy_file(tmp_path) is None
    assert "handoff.json" in handoff.instructions("antigravity") and "structured" in handoff.instructions("codex")


# ── budget ────────────────────────────────────────────────────────────────

def test_budget_normalise_merge_and_split():
    assert budget.normalize({"max_usd": "1.5", "max_tokens": 1000.7, "max_seconds": 0}) == \
        {"max_usd": 1.5, "max_tokens": 1000, "max_seconds": None}
    with pytest.raises(ValueError):
        budget.normalize({"max_usd": -1})
    assert budget.merge({"max_usd": 2}, {"max_usd": 5, "max_tokens": 9}) == \
        {"max_usd": 2.0, "max_tokens": 9, "max_seconds": None}
    run = {"budget": {"max_usd": 1.0, "max_tokens": 10000, "max_seconds": 600}, "active_seconds": 100,
           "steps": [{"usage": {"cost_usd": 0.4, "input_tokens": 3000, "cache_read_tokens": 2000,
                                "output_tokens": 1000}}]}
    sp = budget.spent(run)
    assert sp == {"usd": 0.4, "usd_complete": True, "tokens": 2000, "seconds": 100.0}
    eff = budget.for_step(run, {"spec": {"budget": {"max_tokens": 500}}}, steps_left=3, phases_left=2)
    assert eff["max_usd"] == pytest.approx(0.2) and eff["max_seconds"] == pytest.approx(250)
    assert eff["max_tokens"] == 500 and eff["source"] == {"max_usd": "run", "max_tokens": "step", "max_seconds": "run"}
    run["active_seconds"] = 700
    assert budget.exhausted(run) == "max_seconds"
    assert budget.usage_add({"cost_usd": 1, "input_tokens": 2}, {"cost_usd": None, "input_tokens": 3}) == \
        {"cost_usd": None, "input_tokens": 5}


# ── snapshots ─────────────────────────────────────────────────────────────

def _git_ok():
    import shutil
    return shutil.which("git") is not None


@pytest.mark.skipif(not _git_ok(), reason="git not on PATH")
def test_snapshots_diff_restore_excludes_and_user_git(tmp_data_root, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "snapshots_max_file_mb", lambda: 1)
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    user_head = (ws / ".git" / "HEAD").read_bytes()
    (ws / "a.txt").write_text("one\n", encoding="utf-8")
    (ws / ".telecode").mkdir()
    (ws / ".telecode" / "scratch.json").write_text("{}", encoding="utf-8")
    (ws / "big.bin").write_bytes(b"x" * (1024 * 1024 + 10))
    key = snapshots.key_for("ws", None)
    before = snapshots.take(key, ws, "before", {"phase": "before", "run": "r1"})
    (ws / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    (ws / "sub").mkdir()
    (ws / "sub" / "b.md").write_text("new\n", encoding="utf-8")
    after = snapshots.take(key, ws, "after", {"phase": "after", "run": "r1"})
    files = {f["path"]: f for f in snapshots.changed_files(key, before, after)}
    assert set(files) == {"a.txt", "sub/b.md"}                          # big.bin / .telecode never tracked
    assert files["a.txt"]["additions"] == 1 and files["sub/b.md"]["change"] == "added"
    d = snapshots.diff(key, before, after, "a.txt")
    assert "+two" in d["diff"] and "sub/b.md" not in d["diff"]
    log = snapshots.log(key)
    assert [e["kind"] for e in log] == ["after", "before"] and log[0]["run"] == "r1"
    assert "big.bin" in log[0]["skipped_large"]
    res = snapshots.restore(key, ws, before)
    assert (ws / "a.txt").read_text() == "one\n" and not (ws / "sub" / "b.md").exists()
    assert (ws / "big.bin").exists() and (ws / ".telecode" / "scratch.json").exists()
    assert (ws / ".git" / "HEAD").read_bytes() == user_head               # the user's repo untouched
    assert snapshots.exists(key, res["safety"])                            # the restore is undoable
    snapshots.restore(key, ws, res["safety"])
    assert (ws / "sub" / "b.md").read_text() == "new\n"
    assert snapshots.git_dir(key).parent == tmp_data_root / "data" / "snapshots"


@pytest.mark.skipif(not _git_ok(), reason="git not on PATH")
def test_snapshots_prune_keeps_newest(tmp_data_root, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "snapshots_keep", lambda: 3)
    ws = tmp_path / "ws2"
    ws.mkdir()
    key = snapshots.key_for("ws2", "ns")
    shas = []
    for i in range(5):
        (ws / "f.txt").write_text(str(i), encoding="utf-8")
        shas.append(snapshots.take(key, ws, f"s{i}"))
    labels = [e["label"] for e in snapshots.log(key)]
    assert labels == ["s4", "s3", "s2"]
    assert snapshots.previous(key, shas[4]) == shas[3]


def test_snapshots_disabled_is_a_noop(tmp_data_root, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "snapshots_enabled", lambda: False)
    assert snapshots.take("k", tmp_path, "x") is None and snapshots.log("k") == []


# ── adapters ──────────────────────────────────────────────────────────────

def test_adapter_argv_fork_and_budget(tmp_path):
    req = EngineRequest(engine="claude_code", prompt="p", cwd=tmp_path, resume_id="S1", fork=True, max_usd=0.5)
    argv = get_adapter("claude_code").build(req).argv
    assert argv[argv.index("--resume") + 1] == "S1" and "--fork-session" in argv
    assert argv[argv.index("--max-budget-usd") + 1] == "0.5000"
    argv = get_adapter("claude_code").build(EngineRequest(engine="claude_code", prompt="p", cwd=tmp_path)).argv
    assert "--fork-session" not in argv and "--max-budget-usd" not in argv
    req = EngineRequest(engine="codex", prompt="p", cwd=tmp_path, resume_id="T1", fork=True,
                        last_msg_path=tmp_path / "m.txt")
    argv = get_adapter("codex").build(req).argv
    assert argv[argv.index("fork") + 1] == "T1" and "resume" not in argv
    assert argv.index("-C") < argv.index("fork") and argv[-1] == "-"
    req = EngineRequest(engine="antigravity", prompt="p", cwd=tmp_path, resume_id="C1", fork=True)
    assert "--conversation" not in get_adapter("antigravity").build(req).argv


def test_claude_live_usage_and_budget_result(tmp_path):
    ad = get_adapter("claude_code")
    st = ParseState()
    for out in (10, 30):
        evs = ad.parse({"type": "stream_event", "event": {"type": "message_delta", "usage": {
            "input_tokens": 2, "output_tokens": out, "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 5}}}, st)
    assert evs == [{"kind": "usage", "tokens": {"input": 4, "output": 40, "cache_read": 200, "cache_write": 10,
                                                "total_input_incl_cache": 214}, "cost_usd": None, "partial": True}]
    ad.parse({"type": "result", "subtype": "error_max_budget_usd", "total_cost_usd": 0.51}, st)
    with pytest.raises(EngineBudgetExceeded, match="budget_exceeded: cost \\$0.5100"):
        ad.finish(EngineRequest(engine="claude_code", prompt="", cwd=tmp_path, max_usd=0.5), st, 0, "", 5)


def _fake(monkeypatch, engine, *args):
    ad = get_adapter(engine)
    orig = type(ad).build

    def build(req):
        launch = orig(ad, req)
        launch.argv = [sys.executable, str(FAKE), *[str(a) for a in args]]
        return launch
    monkeypatch.setattr(ad, "build", build)


def test_runner_token_cap_stops_run(tmp_path, monkeypatch):
    _fake(monkeypatch, "claude_code", "--tokens", "5000", "--hang-sec", "30")
    events = []
    req = EngineRequest(engine="claude_code", prompt="p", cwd=tmp_path, max_tokens=1000,
                        on_event=events.append, kill_grace_sec=0.5)
    with pytest.raises(EngineBudgetExceeded, match="tokens 5010 > max_tokens 1000"):
        run_engine(req)
    assert events[-1]["kind"] == "error" and events[-1]["budget_exceeded"] is True
    assert events[0]["budget"] == {"max_tokens": 1000}


def test_runner_wall_clock_budget(tmp_path, monkeypatch):
    _fake(monkeypatch, "claude_code", "--hang-sec", "30")
    with pytest.raises(EngineBudgetExceeded, match="max_seconds 1"):
        run_engine(EngineRequest(engine="claude_code", prompt="p", cwd=tmp_path, max_seconds=1, kill_grace_sec=0.5))
