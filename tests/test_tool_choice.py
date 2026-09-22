"""Tests for tool_choice normalization and llama-server compatibility."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proxy.translate import (  # noqa: E402
    _anthropic_tool_choice_to_openai,
    _normalize_tool_choice,
    anthropic_request_to_internal,
    openai_request_to_internal,
)


def test_normalize_tool_choice_strings():
    assert _normalize_tool_choice("auto", None) == ("auto", None)
    assert _normalize_tool_choice("none", None) == ("none", None)
    assert _normalize_tool_choice("any", None) == ("required", None)
    assert _normalize_tool_choice("required", None) == ("required", None)
    assert _normalize_tool_choice(None, None) == (None, None)


def test_normalize_tool_choice_anthropic_named_tool():
    tools = [
        {"type": "function", "function": {"name": "web_search"}},
        {"type": "function", "function": {"name": "bash"}},
    ]
    tc, filtered = _normalize_tool_choice({"type": "tool", "name": "web_search"}, tools)
    assert tc == "required"
    assert len(filtered) == 1
    assert filtered[0]["function"]["name"] == "web_search"


def test_normalize_tool_choice_openai_named_tool():
    tools = [
        {"type": "function", "function": {"name": "web_search"}},
        {"type": "function", "function": {"name": "bash"}},
    ]
    tc, filtered = _normalize_tool_choice(
        {"type": "function", "function": {"name": "web_search"}}, tools
    )
    assert tc == "required"
    assert len(filtered) == 1
    assert filtered[0]["function"]["name"] == "web_search"


def test_anthropic_request_to_internal_named_tool_choice():
    req = {
        "model": "claude-3-7-sonnet",
        "messages": [{"role": "user", "content": "Search for news"}],
        "tools": [
            {
                "name": "web_search",
                "description": "Search web",
                "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
            {
                "name": "bash",
                "description": "Run bash",
                "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}},
            },
        ],
        "tool_choice": {"type": "tool", "name": "web_search"},
    }
    internal = anthropic_request_to_internal(req)
    # Must be string "required" for llama-server
    assert internal["tool_choice"] == "required"
    # Tools must be filtered to only the named tool
    assert len(internal["tools"]) == 1
    assert internal["tools"][0]["function"]["name"] == "web_search"


def test_openai_request_to_internal_named_tool_choice():
    req = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Search for news"}],
        "tools": [
            {
                "type": "function",
                "function": {"name": "web_search", "parameters": {"type": "object"}},
            },
            {
                "type": "function",
                "function": {"name": "bash", "parameters": {"type": "object"}},
            },
        ],
        "tool_choice": {"type": "function", "function": {"name": "web_search"}},
    }
    internal = openai_request_to_internal(req)
    # Must be string "required" for llama-server
    assert internal["tool_choice"] == "required"
    # Tools must be filtered to only the named tool
    assert len(internal["tools"]) == 1
    assert internal["tools"][0]["function"]["name"] == "web_search"


def test_anthropic_tool_choice_to_openai_standalone():
    assert _anthropic_tool_choice_to_openai({"type": "tool", "name": "foo"}) == "required"
    assert _anthropic_tool_choice_to_openai("auto") == "auto"
    assert _anthropic_tool_choice_to_openai("any") == "required"
    assert _anthropic_tool_choice_to_openai("none") == "none"
    assert _anthropic_tool_choice_to_openai(None) is None
