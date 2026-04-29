"""Unit tests for executor helper functions (no I/O)."""

from __future__ import annotations

from services.run.executor import (
    _build_step_prompt,
    _result_preview,
    _engine_to_task_type,
)


def test_engine_mapping():
    assert _engine_to_task_type("claude_code") == "CLAUDE_CODE"
    assert _engine_to_task_type("gemini") == "GEMINI"
    assert _engine_to_task_type("") == "CLAUDE_CODE"   # safe default
    assert _engine_to_task_type("unknown") == "CLAUDE_CODE"


def test_result_preview_string():
    assert _result_preview("hello") == "hello"


def test_result_preview_dict_with_result_field():
    assert _result_preview({"result": "the reply"}) == "the reply"


def test_result_preview_truncates_long_text():
    long = "x" * 1000
    assert len(_result_preview({"result": long})) == 400


def test_result_preview_empty_dict_yields_empty():
    assert _result_preview({}) == ""


def test_build_step_prompt_uses_override_when_set():
    job = {"task_description": "default"}
    step = {"prompt_override": "specific"}
    assert _build_step_prompt(job, step, prev_outputs=None) == "specific"


def test_build_step_prompt_falls_back_to_job_default():
    job = {"task_description": "default plan"}
    step = {"prompt_override": ""}
    assert _build_step_prompt(job, step, prev_outputs=None) == "default plan"


def test_build_step_prompt_handles_no_prompt():
    job = {}
    step = {}
    assert _build_step_prompt(job, step, prev_outputs=None) == "(no prompt provided)"


def test_build_step_prompt_threads_single_previous_output():
    job = {"task_description": "do the thing"}
    step = {"depends_on_text": True}
    prev = [{"step_id": "s1", "name": "first", "text": "PRIOR REPLY", "status": "completed"}]
    out = _build_step_prompt(job, step, prev)
    assert "<previous_output>" in out
    assert "PRIOR REPLY" in out
    assert "</previous_output>" in out


def test_build_step_prompt_wraps_multiple_previous_outputs():
    job = {"task_description": "merge results"}
    step = {"depends_on_text": True}
    prev = [
        {"step_id": "s1", "name": "branchA", "text": "A says hi", "status": "completed"},
        {"step_id": "s2", "name": "branchB", "text": "B says hello", "status": "completed"},
    ]
    out = _build_step_prompt(job, step, prev)
    assert "<previous_outputs>" in out
    assert '<output step="branchA">' in out
    assert "A says hi" in out
    assert '<output step="branchB">' in out
    assert "B says hello" in out


def test_build_step_prompt_skips_threading_when_disabled():
    job = {"task_description": "go"}
    step = {"depends_on_text": False}
    prev = [{"step_id": "s1", "text": "ignored"}]
    out = _build_step_prompt(job, step, prev)
    assert "previous_output" not in out


def test_build_step_prompt_skips_empty_prev():
    job = {"task_description": "go"}
    step = {"depends_on_text": True}
    prev = [{"step_id": "s1", "text": ""}]    # text empty → skipped
    out = _build_step_prompt(job, step, prev)
    assert "previous_output" not in out
