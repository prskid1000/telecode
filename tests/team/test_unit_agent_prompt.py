"""Unit tests for services.task.agent_prompt (no I/O)."""

from __future__ import annotations

import pytest
from services.task.agent_prompt import render_agent_prompt_xml, resolve_prompt


def test_xml_envelope_has_required_blocks():
    out = render_agent_prompt_xml(
        agent={"id": "a-1", "name": "Alice"},
        job={"workspace_id": "ws-1", "task_description": "do stuff"},
    )
    assert "<agent_task" in out
    assert 'id="a-1"' in out
    assert 'name="Alice"' in out
    assert 'id="ws-1"' in out
    assert "<task_description>" in out
    assert "do stuff" in out
    assert "<context_files/>" in out


def test_xml_does_not_include_instructions_block():
    """Instructions are auto-loaded by the CLI from CLAUDE.md/GEMINI.md now;
    they must NOT be embedded in the XML."""
    out = render_agent_prompt_xml(
        agent={"id": "a-1", "name": "Alice", "instructions": "should not appear"},
        job={"workspace_id": "ws-1", "task_description": "x"},
    )
    assert "<instructions>" not in out
    assert "should not appear" not in out


def test_xml_files_block_with_paths():
    out = render_agent_prompt_xml(
        agent={"id": "a", "name": "n"},
        job={"workspace_id": "ws", "task_description": "x"},
        agent_files=[{"path": "a.txt"}],
        job_files=[{"path": "b.csv"}],
    )
    assert "<context_files>" in out
    assert "<file>a.txt</file>" in out
    assert "<file>b.csv</file>" in out


def test_xml_escapes_special_chars():
    out = render_agent_prompt_xml(
        agent={"id": "a&b", "name": "<x>"},
        job={"workspace_id": "ws", "task_description": "5 < 7"},
    )
    assert "a&amp;b" in out
    assert "&lt;x&gt;" in out
    assert "5 &lt; 7" in out


def test_resolve_prompt_passes_through_string():
    assert resolve_prompt({"prompt": "hello"}) == "hello"


def test_resolve_prompt_renders_xml_when_structured():
    out = resolve_prompt({
        "agent": {"id": "a", "name": "Alice"},
        "job": {"workspace_id": "ws", "task_description": "task"},
    })
    assert "<agent_task" in out
    assert "Alice" in out


def test_resolve_prompt_structured_wins_over_string():
    out = resolve_prompt({
        "prompt": "ignored",
        "agent": {"id": "a", "name": "Alice"},
        "job": {"workspace_id": "ws", "task_description": "task"},
    })
    assert "<agent_task" in out
    assert "ignored" not in out


def test_resolve_prompt_raises_when_neither():
    with pytest.raises(ValueError):
        resolve_prompt({})
