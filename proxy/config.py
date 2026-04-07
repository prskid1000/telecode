"""Proxy configuration — reads from main settings.json."""
from __future__ import annotations

import config as app_config

# Tools always forwarded to the model (never deferred).
# Matches Opus's core set — only 9 tools (~6.9k tokens).
DEFAULT_CORE_TOOLS = [
    "Bash", "Edit", "Read", "Write", "Glob", "Grep",
    "Agent", "Skill",
]

# BM25 tuning
BM25_K1 = 0.9
BM25_B = 0.4
MAX_SEARCH_RESULTS = 5


def core_tools() -> list[str]:
    """Core tool names — from settings or defaults."""
    return app_config.get_nested("proxy.core_tools", DEFAULT_CORE_TOOLS)


def proxy_port() -> int:
    return int(app_config.get_nested("proxy.port", 1235))


def upstream_url() -> str:
    """LM Studio (or other backend) base URL."""
    return app_config.get_nested("proxy.upstream_url", "http://localhost:1234")


def enabled() -> bool:
    return bool(app_config.get_nested("proxy.enabled", False))


def debug() -> bool:
    return bool(app_config.get_nested("proxy.debug", False))
