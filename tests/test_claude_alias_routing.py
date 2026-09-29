"""Agent-roster strip toggle + claude-* names routed to the loaded model."""
import asyncio

from proxy import server
from proxy import config as proxy_config
from proxy.tool_registry import strip_turn_context

ROSTER = ("Available agent types for the Agent tool:\n"
          "- general-purpose: does things\n"
          "</system-reminder>")


def test_roster_stripped_by_default():
    assert "Available agent types" not in strip_turn_context(ROSTER)


def test_roster_kept_when_toggled_off():
    assert "general-purpose" in strip_turn_context(ROSTER, agents=False)


def _patch(monkeypatch, *, loaded="qwen-a", on=True, registered=("qwen-a", "qwen-b")):
    async def _loaded():
        return loaded
    monkeypatch.setattr(server, "_loaded_model", _loaded)
    monkeypatch.setattr(proxy_config, "claude_alias_to_loaded", lambda: on)
    monkeypatch.setattr(server.llama_cfg, "models",
                        lambda: {k: {} for k in registered})


def test_claude_alias_goes_to_loaded(monkeypatch):
    _patch(monkeypatch)
    for name in ("claude-haiku-4-5-20251001", "claude-sonnet-5", "Claude-opus-5-5"):
        assert asyncio.run(server._claude_alias_to_loaded(name)) == "qwen-a"


def test_non_claude_and_registered_names_untouched(monkeypatch):
    _patch(monkeypatch)
    assert asyncio.run(server._claude_alias_to_loaded("qwen-b")) == ""
    assert asyncio.run(server._claude_alias_to_loaded("gpt-5")) == ""


def test_setting_off(monkeypatch):
    _patch(monkeypatch, on=False)
    assert asyncio.run(server._claude_alias_to_loaded("claude-sonnet-5")) == ""


def test_nothing_loaded_falls_back(monkeypatch):
    _patch(monkeypatch, loaded="")
    assert asyncio.run(server._claude_alias_to_loaded("claude-sonnet-5")) == ""
