"""P1 Engine Runner: per-adapter argv + event normalisation, driven by real
recorded CLI output (tests/team/fixtures/engine/*.jsonl — claude 2.1 stream-json,
codex-cli 0.157 `exec --json`, agy 1.2 stream-json)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from services.engine.adapters import get_adapter
from services.engine.adapters.base import ParseState
from services.engine.types import EVENT_KINDS, EngineError, EngineRequest

FIX = Path(__file__).parent / "fixtures" / "engine"


def _replay(engine, fixture):
    ad = get_adapter(engine)
    st = ParseState()
    events = []
    for line in (FIX / fixture).read_text(encoding="utf-8").splitlines():
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            evt = None
        if not isinstance(evt, dict):
            if line.strip():
                ad.raw_line(line.strip(), st)
            continue
        events += ad.parse(evt, st)
    events += ad.trailing_events(st)
    return ad, st, events


def _req(engine, tmp_path, **kw):
    return EngineRequest(engine=engine, prompt="p", cwd=tmp_path, **kw)


# ── Claude ──────────────────────────────────────────────────────────────────

def test_claude_recorded_stream_normalises(tmp_path):
    ad, st, events = _replay("claude_code", "claude_stream.jsonl")
    kinds = Counter(e["kind"] for e in events)
    assert set(kinds) <= set(EVENT_KINDS)
    assert kinds["delta"] > 10                                     # partial-message text deltas
    tools = [e for e in events if e["kind"] == "tool"]
    assert len(tools) == 1 and tools[0]["tool"] == "Edit" == tools[0]["name"]
    assert tools[0]["summary"].startswith("Edit: C:\\") and tools[0]["summary"].endswith("claude-cloud.html")
    assert any(e["kind"] == "narrative" and "Edited by claude-cloud" in e["text"] for e in events)
    assert st.session_id == "b91e2083-0212-41b0-97c7-7717c48820af"
    res = ad.finish(_req("claude_code", tmp_path), st, 0, "", 100)
    assert res.cost_usd == pytest.approx(0.731356)
    assert res.tokens["cache_read"] == 181419 and res.tokens["cache_write"] == 2672
    assert res.tokens["total_input_incl_cache"] == 4 + 181419 + 2672
    assert res.engine_session_id == st.session_id and res.tool_calls == ["Edit"]
    d = res.to_dict("ws-1")
    assert d["claude_session_id"] == st.session_id and d["session_id"] == "ws-1"
    assert ad.usage_event(st)["tokens"]["output"] == res.tokens["output"]


def test_claude_todo_retry_and_raw_fallback(tmp_path):
    ad = get_adapter("claude_code")
    st = ParseState()
    ev = ad.parse({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "TodoWrite", "input": {"todos": [
            {"content": "a", "status": "completed"}, {"content": "b", "status": "in_progress"}]}}]}}, st)
    assert [e["kind"] for e in ev] == ["tool", "todo"]
    assert ev[1]["todos"] == [{"text": "a", "status": "completed"}, {"text": "b", "status": "in_progress"}]
    ev = ad.parse({"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 3, "error": "x"}, st)
    assert ev == [{"kind": "retry", "attempt": 1, "max_retries": 3, "error": "x"}]
    # non-JSON output, no result event → text + narrative events, no failure
    ad.raw_line("plain text", st)
    assert ad.trailing_events(st) == [{"kind": "narrative", "text": "plain text"}]
    assert ad.finish(_req("claude_code", tmp_path), st, 1, "", 1).text == "plain text"
    with pytest.raises(EngineError, match="claude exited with code 2: boom"):
        ad.finish(_req("claude_code", tmp_path), ParseState(), 2, "boom\n", 1)


def test_claude_argv_flags(tmp_path):
    from services.engine.adapters.claude import build_argv
    argv = build_argv(resume_id="r1", model="sonnet", is_local=False,
                      append_system_prompt_file=tmp_path / "a.md", schema={"type": "object"})
    assert argv[:2] == ["claude", "-p"] and "--dangerously-skip-permissions" in argv
    assert argv[argv.index("--resume") + 1] == "r1" and argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--append-system-prompt-file") + 1] == str(tmp_path / "a.md")
    assert json.loads(argv[argv.index("--json-schema") + 1]) == {"type": "object"}
    # local: the model travels as ANTHROPIC_MODEL, never --model
    assert "--model" not in build_argv(resume_id=None, model="qwen", is_local=True)


def test_claude_local_env_shape():
    from services.engine.adapters.claude import local_env
    env = local_env("qwen", 1235, 16384, base={"PATH": "x"})
    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:1235"             # no /v1
    assert env["ANTHROPIC_MODEL"] == "qwen" and env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "16384"
    assert env["PATH"] == "x"


def test_claude_structured_output_passthrough(tmp_path):
    ad = get_adapter("claude_code")
    st = ParseState()
    ad.parse({"type": "result", "result": "{}", "structured_output": {"ok": True}, "session_id": "s"}, st)
    res = ad.finish(_req("claude_code", tmp_path, schema={"type": "object"}), st, 0, "", 1)
    assert res.structured_output == {"ok": True}
    assert res.to_dict("w", with_schema=True)["structured_output"] == {"ok": True}


# ── Codex ───────────────────────────────────────────────────────────────────

def test_codex_recorded_tools_stream(tmp_path):
    ad, st, events = _replay("codex", "codex_tools.jsonl")
    kinds = [e["kind"] for e in events]
    assert set(kinds) <= set(EVENT_KINDS)
    assert kinds.count("tool") == 2                                # item.completed only, not item.started
    assert all(e["tool"] == "command_execution" and "pwsh.exe" in e["summary"]
               for e in events if e["kind"] == "tool")
    assert kinds[0] == "warning" and "Model metadata" in events[0]["text"]
    assert {"kind": "narrative", "text": "DONE"} in events
    assert kinds[-1] == "usage" and events[-1]["tokens"]["total_input_incl_cache"] == 21961
    assert st.session_id == "01a0d6a4-9409-7aa3-a792-9a31063c0cd0" and st.turns == 1
    last = tmp_path / "last.txt"
    last.write_text("DONE\n", encoding="utf-8")
    res = ad.finish(_req("codex", tmp_path, last_msg_path=last), st, 0, "", 1234)
    assert res.text == "DONE" and res.cost_usd is None and res.duration_ms == 1234
    assert res.tokens == {"input": 21961 - 21553, "output": 142, "cache_read": 21553, "cache_write": 0,
                          "reasoning_output": 0, "total_input_incl_cache": 21961}
    assert res.to_dict("w")["codex_session_id"] == st.session_id


def test_codex_recorded_turn_failed_raises(tmp_path):
    ad, st, events = _replay("codex", "codex_turn_failed.jsonl")
    retries = [e for e in events if e["kind"] == "retry"]
    assert len(retries) == 11                                     # 10 reconnects + 1 final error
    assert retries[-1]["error"] == "workspace routing discovery unauthorized (401)"
    assert any(e["kind"] == "warning" and "Falling back" in e["text"] for e in events)
    assert not st.saw_completion
    with pytest.raises(EngineError, match="codex exited with code 1: 401"):
        ad.finish(_req("codex", tmp_path, last_msg_path=tmp_path / "none.txt"), st, 1, "401", 5)


def test_codex_todo_list_and_schema_reply(tmp_path):
    ad = get_adapter("codex")
    st = ParseState()
    ev = ad.parse({"type": "item.updated", "item": {"type": "todo_list", "items": [
        {"text": "x", "completed": True}, {"text": "y", "completed": False}]}}, st)
    assert ev == [{"kind": "todo", "todos": [{"text": "x", "status": "completed"},
                                             {"text": "y", "status": "pending"}]}]
    ad.parse({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 1}}, st)
    last = tmp_path / "last.txt"
    last.write_text('{"verdict": "pass"}', encoding="utf-8")
    res = ad.finish(_req("codex", tmp_path, last_msg_path=last, schema={"type": "object"}), st, 0, "", 1)
    assert res.structured_output == {"verdict": "pass"}


def test_codex_argv_resume_order_and_schema_file(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "_settings_dir", lambda: tmp_path)
    ad = get_adapter("codex")
    launch = ad.build(_req("codex", tmp_path, resume_id="T1", model="gpt-5.5",
                           last_msg_path=tmp_path / "l.txt", schema={"type": "object"}))
    argv = launch.argv
    assert argv[:2] == ["codex", "exec"] and argv[-1] == "-"
    # exec-only flags must precede `resume`
    assert argv.index("--sandbox") < argv.index("resume") and argv.index("-C") < argv.index("resume")
    assert argv[argv.index("resume") + 1] == "T1"
    schema_file = Path(argv[argv.index("--output-schema") + 1])
    assert json.loads(schema_file.read_text()) == {"type": "object"} and schema_file in launch.cleanup
    assert launch.stdin == "p"


def test_codex_local_provider_is_c_overrides_and_strips_keys():
    from services.engine.adapters.codex import local_env, local_provider_overrides
    ov = local_provider_overrides(1235)
    assert "model_provider=telecode" in ov and "model_providers.telecode.wire_api=responses" in ov
    assert "model_providers.telecode.base_url=http://localhost:1235/v1" in ov
    env = local_env({"OPENAI_API_KEY": "k", "CODEX_API_KEY": "c", "CODEX_HOME": "h"})
    assert "OPENAI_API_KEY" not in env and "CODEX_API_KEY" not in env and env["CODEX_HOME"] == "h"


# ── Antigravity ─────────────────────────────────────────────────────────────

def test_agy_recorded_stream_normalises(tmp_path):
    ad, st, events = _replay("antigravity", "agy_stream.jsonl")
    kinds = Counter(e["kind"] for e in events)
    assert set(kinds) <= set(EVENT_KINDS)
    assert kinds["tool"] == 7 and kinds["delta"] == 6
    t0 = next(e for e in events if e["kind"] == "tool")
    assert t0["name"] == t0["tool"] and isinstance(t0["input"], dict)
    assert json.loads(t0["summary"]) == t0["input"]               # TeleDesign previews the input JSON
    assert st.session_id == "bf4855a3-637f-4f95-ba85-53cfee7d0dfb"
    res = ad.finish(_req("antigravity", tmp_path), st, 0, "", 1)
    assert res.text.startswith("I have created the requested page")
    assert res.tokens["input"] == 87802 and res.tokens["cache_read"] == 175447
    assert res.duration_ms == 39029 and res.num_turns == 1 and res.cost_usd is None
    assert res.to_dict("w")["antigravity_conversation_id"] == st.session_id


def test_agy_failed_result_raises(tmp_path):
    ad = get_adapter("antigravity")
    st = ParseState()
    ad.parse({"event": "result", "result": {"status": "ERROR", "error": "quota"}}, st)
    with pytest.raises(EngineError, match="agy failed: quota"):
        ad.finish(_req("antigravity", tmp_path), st, 0, "", 1)


def test_agy_stdin_argv_and_local_home(tmp_path):
    from services.engine.adapters.antigravity import (build_argv, ensure_local_home, local_env,
                                                      local_model_arg, stdin_message)
    argv = build_argv(work_dir=tmp_path, resume_id="c1", model="gemini-x")
    assert argv[-1] == "-p=" and argv[argv.index("--conversation") + 1] == "c1"
    assert argv[argv.index("--add-dir") + 1] == str(tmp_path)
    assert json.loads(stdin_message("hi")) == {"event": "user", "message": {"content": "hi"}}
    home = ensure_local_home(tmp_path / "home")
    cfg = json.loads((home / ".gemini" / "antigravity-cli" / "settings.json").read_text())
    assert cfg["modelProvider"] == "gemini"
    env = local_env(1235, home, base={"GOOGLE_API_KEY": "g"})
    assert "GOOGLE_API_KEY" not in env and env["USERPROFILE"] == str(home)
    assert env["GOOGLE_GEMINI_BASE_URL"] == "http://localhost:1235"
    assert local_model_arg("q") == "gemini-api://local/models/q"
    launch = get_adapter("antigravity").build(_req("antigravity", tmp_path))
    assert json.loads(launch.stdin)["message"]["content"] == "p"


# ── Handler modules keep their public names ─────────────────────────────────

def test_handler_reexports():
    from services.task.handlers import antigravity, codex
    assert codex._add_usage and codex._normalize_usage and codex._build_codex_argv
    assert antigravity.ensure_local_home and antigravity.local_model_arg
