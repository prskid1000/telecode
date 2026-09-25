"""Permission modes for Codex and Antigravity (argv per engine per mode).

Mapping verified against codex-cli 0.157 and agy 1.2.11 (see CLAUDE.md "Task
engines" → permission modes). The agy stream lines below are trimmed from a
real 1.2.11 ``--mode accept-edits`` run: the edit went through, the command was
auto-denied without hanging.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.engine.adapters import antigravity as agy
from services.engine.adapters import codex
from services.engine.adapters.base import ParseState

W = Path("W")
L = Path("L")


def _codex(mode, resume_id=None, fork=False):
    return codex.build_argv(work_dir=W, resume_id=resume_id, last_msg_path=L, model=None,
                            fork=fork, permission_mode=mode)


def _before_sub(argv, flag):
    """``flag`` occurs, and before the resume/fork subcommand when there is one."""
    i = argv.index(flag)
    for sub in ("resume", "fork"):
        if sub in argv:
            assert i < argv.index(sub), (flag, argv)
    return i


# ---------------------------------------------------------------- codex

@pytest.mark.parametrize("mode", [None, "", "skip", "bypassPermissions"])
@pytest.mark.parametrize("resume", [None, "SID"])
def test_codex_skip_modes_keep_the_bypass(mode, resume):
    a = _codex(mode, resume)
    assert codex.BYPASS_FLAG in a
    assert a[_before_sub(a, "--sandbox") + 1] == "danger-full-access"
    assert "--approve-for-me" not in a and "approval_policy=never" not in a


@pytest.mark.parametrize("resume", [None, "SID"])
def test_codex_auto_is_approve_for_me_without_sandbox_flag(resume):
    a = _codex("auto", resume)
    _before_sub(a, "--approve-for-me")
    # clap: "the argument '--sandbox <SANDBOX_MODE>' cannot be used with '--approve-for-me'"
    assert "--sandbox" not in a
    assert codex.BYPASS_FLAG not in a and "approval_policy=never" not in a


@pytest.mark.parametrize("mode", ["acceptEdits", "dontAsk", "ask", "manual", "somethingNew"])
@pytest.mark.parametrize("resume", [None, "SID"])
def test_codex_workspace_write_never(mode, resume):
    a = _codex(mode, resume)
    assert a[_before_sub(a, "--sandbox") + 1] == "workspace-write"
    i = _before_sub(a, "approval_policy=never")
    assert a[i - 1] == "-c"
    assert codex.BYPASS_FLAG not in a and "--approve-for-me" not in a


def test_codex_plan_is_read_only():
    a = _codex("plan", "SID", fork=True)
    assert a[_before_sub(a, "--sandbox") + 1] == "read-only"
    assert "approval_policy=never" in a and codex.BYPASS_FLAG not in a
    assert a[a.index("fork") + 1] == "SID" and a[-1] == "-"


def test_codex_plan_warnings():
    assert codex.permission_plan("auto").warning is None
    assert codex.permission_plan("acceptEdits").warning is None
    assert codex.permission_plan(None).warning is None
    assert "cannot ask a person" in codex.permission_plan("ask").warning
    assert "cannot ask a person" in codex.permission_plan("manual").warning
    assert "no Codex mapping" in codex.permission_plan("weird").warning


def test_codex_argv_keeps_its_shape_otherwise():
    a = _codex("acceptEdits", "SID")
    assert a[:2] == ["codex", "exec"] and a[-1] == "-"
    assert a[a.index("-C") + 1] == "W" and a.index("-C") < a.index("resume")
    assert a[a.index("resume") + 1] == "SID"
    for f in ("--json", "--skip-git-repo-check", "--output-last-message"):
        assert a.index(f) > a.index("resume")


def test_codex_adapter_carries_mode_and_warning(tmp_path):
    from services.engine import EngineRequest
    from services.engine.adapters import get_adapter
    ad = get_adapter("codex")
    ask = ad.build(EngineRequest(engine="codex", prompt="p", cwd=tmp_path, permission_mode="ask",
                                 last_msg_path=tmp_path / "m.txt"))
    assert "workspace-write" in ask.argv and codex.BYPASS_FLAG not in ask.argv
    assert ask.warnings and "cannot ask a person" in ask.warnings[0]
    auto = ad.build(EngineRequest(engine="codex", prompt="p", cwd=tmp_path, permission_mode="auto",
                                  last_msg_path=tmp_path / "m.txt"))
    assert "--approve-for-me" in auto.argv and auto.warnings == []
    plain = ad.build(EngineRequest(engine="codex", prompt="p", cwd=tmp_path, last_msg_path=tmp_path / "m.txt"))
    assert codex.BYPASS_FLAG in plain.argv and plain.warnings == []
    # engine_extras args still land just before the trailing "-"
    assert plain.extras_at == len(plain.argv) - 1 and plain.argv[-1] == "-"


# ---------------------------------------------------------------- antigravity

def _agy(mode, resume=None):
    return agy.build_argv(work_dir=W, resume_id=resume, permission_mode=mode)


@pytest.mark.parametrize("mode,flags", [
    (None, ["--dangerously-skip-permissions"]),
    ("", ["--dangerously-skip-permissions"]),
    ("skip", ["--dangerously-skip-permissions"]),
    ("bypassPermissions", ["--dangerously-skip-permissions"]),
    ("auto", ["--mode", "accept-edits"]),
    ("acceptEdits", ["--mode", "accept-edits"]),
    ("plan", ["--mode", "plan"]),
    ("dontAsk", []),
    ("ask", ["--mode", "accept-edits"]),
    ("manual", ["--mode", "accept-edits"]),
])
def test_agy_modes(mode, flags):
    a = _agy(mode, "CONV")
    head = a[:a.index("--add-dir")]
    assert head == ["agy", "--input-format", "stream-json", "--output-format", "stream-json", *flags]
    assert a[-1] == "-p=" and a[a.index("--conversation") + 1] == "CONV"
    if mode not in (None, "", "skip", "bypassPermissions"):
        assert "--dangerously-skip-permissions" not in a


def test_agy_warnings():
    assert agy.permission_args("auto")[1] is None and agy.permission_args("plan")[1] is None
    assert agy.permission_args("dontAsk")[1] is None and agy.permission_args(None)[1] is None
    assert "cannot ask a person" in agy.permission_args("ask")[1]
    assert "no agy mapping" in agy.permission_args("xyz")[1]


def test_agy_adapter_carries_mode_and_warning(tmp_path):
    from services.engine import EngineRequest
    from services.engine.adapters import get_adapter
    ad = get_adapter("antigravity")
    ask = ad.build(EngineRequest(engine="antigravity", prompt="p", cwd=tmp_path, permission_mode="ask"))
    assert ask.argv[ask.argv.index("--mode") + 1] == "accept-edits" and ask.warnings
    plain = ad.build(EngineRequest(engine="antigravity", prompt="p", cwd=tmp_path))
    assert "--dangerously-skip-permissions" in plain.argv and plain.warnings == []
    assert plain.argv[plain.extras_at] == "-p="


_DENIED_TOOL = {"event": "step_update", "step_update": {
    "conversation_id": "c1", "step_index": 4, "state": "ERROR", "step_type": "tool", "tool_name": "run_command",
    "tool_info": {"name": "run_command", "parameters": {"CommandLine": "echo shell-ok"},
                  "error": {"type": "TOOL_ERROR", "message": "permission check failed for command \"echo shell-ok\": "
                            "user denied permission to run command:\necho shell-ok\nDo not attempt to circumvent"}}}}
_RESULT = {"event": "result", "result": {
    "conversation_id": "c1", "status": "SUCCESS", "response": "", "duration_seconds": 8.49, "num_turns": 1,
    "usage": {"input_tokens": 27003, "output_tokens": 558, "cache_read_tokens": 0},
    "denied_actions": [{"action": "command", "display_name": "RunCommand"}]}}


def test_agy_headless_denials_become_warnings():
    ad = agy.AntigravityAdapter()
    st = ParseState()
    ev = ad.parse(json.loads(json.dumps(_DENIED_TOOL)), st)
    assert ev == [{"kind": "warning", "text": 'run_command: permission check failed for command "echo shell-ok": '
                                              'user denied permission to run command:'}]
    ev = ad.parse(_RESULT, st)
    assert ev[0]["kind"] == "warning" and "RunCommand" in ev[0]["text"]
    assert ev[0]["denied_actions"] == [{"action": "command", "display_name": "RunCommand"}]
    assert st.saw_completion and st.session_id == "c1"
    res = ad.finish(None, st, 0, "", 9000)       # a denial is not a failed run
    assert res.engine_session_id == "c1" and res.tokens["input"] == 27003
