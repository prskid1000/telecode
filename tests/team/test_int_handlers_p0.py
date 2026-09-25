"""Handler-level regressions with the CLI subprocess mocked out:
B3 (model forwarded), B7 (resume id scoped per workspace/agent/engine, legacy
fallback), B8 (Claude gets AGENT.md via --append-system-prompt-file and the
workspace CLAUDE.md is untouched), B6 (start event keeps a prompt digest),
B11 (codex usage from 0.157 turn.completed shapes).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from services.agent.agent_manager import get_agent_manager
from services.session import session_store
from services.task.task_manager import TaskStatus, get_task_queue
from services.task.task_utils import (
    make_resume_store,
    prompt_digest,
    read_resume_id,
    resume_scope_key,
)


@pytest.fixture(autouse=True)
def _restore_handlers():
    q = get_task_queue()
    saved = dict(q.task_handlers)
    yield
    q.task_handlers.clear()
    q.task_handlers.update(saved)


def _run_task(task_type, params, sid, ns=None, deadline=5.0):
    q = get_task_queue()
    tid = q.submit_task(task_type, params, session_id=sid, session_namespace=ns)
    end = time.time() + deadline
    while time.time() < end:
        t = q.get_task(tid)
        if t.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return t
        time.sleep(0.02)
    return q.get_task(tid)


# ── B7 unit ─────────────────────────────────────────────────────────────────

def test_b7_scope_key_shape():
    assert resume_scope_key("A1", "codex", False) == "A1:codex"
    assert resume_scope_key(None, "claude_code", True) == "_:claude_code:local"


def test_b7_read_resume_legacy_fallback_only_without_agent():
    data = {"last_claude_session_id": "legacy-1",
            "last_antigravity_local_conversation_id": "agy-local"}
    assert read_resume_id(data, None, "claude_code", False) == "legacy-1"
    assert read_resume_id(data, None, "antigravity", True) == "agy-local"
    assert read_resume_id(data, None, "antigravity", False) is None
    # an agent never inherits the workspace-wide legacy conversation
    assert read_resume_id(data, "agent-a", "claude_code", False) is None
    data["resume"] = {"agent-a:claude_code": "scoped"}
    assert read_resume_id(data, "agent-a", "claude_code", False) == "scoped"


def test_b7_store_writes_scoped_and_legacy_for_no_agent(tmp_data_root):
    session_store.create(session_id="ws-r", data={})
    make_resume_store("ws-r", None, None, "codex", False)("th-1")
    make_resume_store("ws-r", None, "agent-b", "codex", False)("th-2")
    d = session_store.get("ws-r")["data"]
    assert d["resume"] == {"_:codex": "th-1", "agent-b:codex": "th-2"}
    assert d["last_codex_session_id"] == "th-1"                        # legacy kept for no-agent


# ── B7/B3/B8 through claude_code_task with the subprocess mocked ────────────

def _fake_claude(calls):
    def fake(**kw):
        calls.append(kw)
        f = kw.get("append_system_prompt_file")
        kw["_append_text"] = Path(f).read_text(encoding="utf-8") if f else None
        # what the workspace CLAUDE.md looks like while the CLI runs
        cm = kw["work_dir"] / "CLAUDE.md"
        kw["_claude_md_during"] = cm.read_text(encoding="utf-8") if cm.exists() else None
        kw["resume_store"](f"cli-session-{len(calls)}")
        return {"result": "ok"}
    return fake


def test_b7_b3_b8_claude_task(tmp_data_root, monkeypatch):
    from services.task.handlers import claude_code
    calls = []
    monkeypatch.setattr(claude_code, "_run_claude_subprocess", _fake_claude(calls))
    get_task_queue().register_handler("P0_CLAUDE", claude_code.claude_code_task)

    am = get_agent_manager()
    a = am.create_agent("A")
    b = am.create_agent("B")
    am.set_internal_files(a["id"], {"AGENT.md": "agent A rules"})
    am.set_internal_files(b["id"], {"AGENT.md": "agent B rules"})

    session_store.create(session_id="ws-claude", data={"name": "ws"})
    ws_dir = session_store._session_dir("ws-claude")
    (ws_dir / "CLAUDE.md").write_text("the project's own CLAUDE.md", encoding="utf-8")

    t = _run_task("P0_CLAUDE", {"prompt": "p1", "agent_id": a["id"], "model": "sonnet"}, "ws-claude")
    assert t.status == TaskStatus.COMPLETED, t.error
    t = _run_task("P0_CLAUDE", {"prompt": "p2", "agent_id": b["id"]}, "ws-claude")
    assert t.status == TaskStatus.COMPLETED, t.error
    t = _run_task("P0_CLAUDE", {"prompt": "p3", "agent_id": a["id"]}, "ws-claude")
    assert t.status == TaskStatus.COMPLETED, t.error

    # B3: model forwarded
    assert calls[0]["model"] == "sonnet" and calls[1]["model"] is None
    # B7: agent B does not resume agent A's conversation; A resumes its own
    assert calls[0]["resume_id"] is None
    assert calls[1]["resume_id"] is None
    assert calls[2]["resume_id"] == "cli-session-1"
    data = session_store.get("ws-claude")["data"]
    assert data["resume"] == {f"{a['id']}:claude_code": "cli-session-3",
                              f"{b['id']}:claude_code": "cli-session-2"}
    assert "last_claude_session_id" not in data
    # B8: AGENT.md passed explicitly; the workspace CLAUDE.md never replaced
    assert calls[0]["_append_text"] == "agent A rules"
    assert calls[1]["_append_text"] == "agent B rules"
    assert all(c["_claude_md_during"] == "the project's own CLAUDE.md" for c in calls)
    assert (ws_dir / "CLAUDE.md").read_text(encoding="utf-8") == "the project's own CLAUDE.md"
    assert not Path(calls[0]["append_system_prompt_file"]).exists()        # cleaned up


def test_b7_no_agent_task_still_resumes_legacy_session(tmp_data_root, monkeypatch):
    from services.task.handlers import claude_code
    calls = []
    monkeypatch.setattr(claude_code, "_run_claude_subprocess", _fake_claude(calls))
    get_task_queue().register_handler("P0_CLAUDE", claude_code.claude_code_task)
    session_store.create(session_id="ws-old", data={"last_claude_session_id": "pre-p0-session"})
    t = _run_task("P0_CLAUDE", {"prompt": "hi"}, "ws-old")
    assert t.status == TaskStatus.COMPLETED, t.error
    assert calls[0]["resume_id"] == "pre-p0-session"
    assert calls[0]["append_system_prompt_file"] is None


@pytest.mark.parametrize("mod_name,fn_name,sub_name,engine", [
    ("codex", "codex_task", "_run_codex_subprocess", "codex"),
    ("antigravity", "antigravity_task", "_run_antigravity_subprocess", "antigravity"),
])
def test_b3_b7_other_engines_forward_model_and_scope(tmp_data_root, monkeypatch, mod_name, fn_name, sub_name, engine):
    import importlib
    mod = importlib.import_module(f"services.task.handlers.{mod_name}")
    calls = []

    def fake(**kw):
        calls.append(kw)
        kw["resume_store"]("conv-1")
        return {"result": "ok"}

    monkeypatch.setattr(mod, sub_name, fake)
    get_task_queue().register_handler("P0_ENG", getattr(mod, fn_name))
    a = get_agent_manager().create_agent("E")
    session_store.create(session_id=f"ws-{engine}", data={})
    t = _run_task("P0_ENG", {"prompt": "x", "agent_id": a["id"], "model": "m-1"}, f"ws-{engine}")
    assert t.status == TaskStatus.COMPLETED, t.error
    assert calls[0]["model"] == "m-1"
    assert session_store.get(f"ws-{engine}")["data"]["resume"] == {f"{a['id']}:{engine}": "conv-1"}


def test_b7_teledesign_signature_still_works_without_new_kwargs():
    """generate.py calls _run_*_subprocess with only the old kwargs + model."""
    import inspect
    from services.task.handlers import antigravity, claude_code, codex
    for fn in (claude_code._run_claude_subprocess, codex._run_codex_subprocess,
               antigravity._run_antigravity_subprocess):
        params = inspect.signature(fn).parameters
        assert params["model"].default is None
        assert params["resume_store"].default is None


# ── B6: prompt digest ───────────────────────────────────────────────────────

def test_b6_prompt_digest_bounds_start_event():
    big = "x" * 56_000
    d = prompt_digest(big)
    assert len(d["prompt"]) == 2048 and d["prompt_len"] == 56_000 and d["prompt_truncated"]
    assert len(d["prompt_sha256"]) == 64
    assert prompt_digest("hi")["prompt_truncated"] is False


# ── B11: codex usage ────────────────────────────────────────────────────────

def test_b11_codex_usage_from_turn_completed_events():
    from services.task.handlers.codex import _add_usage, _normalize_usage
    totals = {}
    # codex-cli 0.157 turn.completed.usage (TokenUsage): cached is a subset of input
    _add_usage(totals, {"input_tokens": 29453, "cached_input_tokens": 29009,
                        "output_tokens": 168, "reasoning_output_tokens": 12})
    _add_usage(totals, {"input_tokens": 1000, "cached_input_tokens": 0, "output_tokens": 10})
    _add_usage(totals, None)
    tok = _normalize_usage(totals)
    assert tok["total_input_incl_cache"] == 30453                       # not input + cached
    assert tok["cache_read"] == 29009
    assert tok["input"] == 30453 - 29009
    assert tok["output"] == 178 and tok["reasoning_output"] == 12


class _FakeProc:
    def __init__(self, lines):
        self.stdout = iter(lines)
        self.stderr = iter(())
        self.pid = 0
        self.returncode = 0

        class _In:
            def write(self, _s): pass
            def close(self): pass
        self.stdin = _In()

    def wait(self, timeout=None): return 0
    def poll(self): return 0
    def kill(self): pass


def test_b11_codex_subprocess_reports_turns_duration_and_unknown_cost(tmp_data_root, monkeypatch):
    import json as _json
    from services.task.handlers import codex
    lines = [_json.dumps(e) + "\n" for e in (
        {"type": "thread.started", "thread_id": "th-9"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}},
        {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 60,
                                             "output_tokens": 7}},
    )]
    monkeypatch.setattr(codex.subprocess, "Popen", lambda *a, **k: _FakeProc(lines))
    stored = []
    session_store.create(session_id="ws-cx", data={})
    out = codex._run_codex_subprocess(
        prompt="p", work_dir=session_store._session_dir("ws-cx"), sid="ws-cx", ns=None,
        resume_id=None, is_local=False, log_path=tmp_data_root / "cx.jsonl",
        last_msg_path=tmp_data_root / "cx_last.txt", resume_store=stored.append)
    assert stored == ["th-9"]
    assert out["num_turns"] == 1 and out["cost_usd"] is None and out["duration_ms"] >= 0
    assert out["tokens"]["total_input_incl_cache"] == 100 and out["tokens"]["cache_read"] == 60
