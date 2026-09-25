"""Thin per-engine adapters: argv + env + stdin + stream parser."""

from __future__ import annotations

from services.engine.adapters.antigravity import AntigravityAdapter
from services.engine.adapters.base import Adapter
from services.engine.adapters.claude import ClaudeAdapter
from services.engine.adapters.codex import CodexAdapter

_ADAPTERS = {
    "claude_code": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "antigravity": AntigravityAdapter(),
}


def get_adapter(engine: str) -> Adapter:
    try:
        return _ADAPTERS[engine]
    except KeyError:
        raise ValueError(f"Unknown engine {engine!r} (expected one of {sorted(_ADAPTERS)})") from None
