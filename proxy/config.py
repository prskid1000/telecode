"""Proxy configuration — reads from main settings.json."""
from __future__ import annotations

import config as app_config

# BM25 tuning
BM25_K1 = 0.9
BM25_B = 0.4
MAX_SEARCH_RESULTS = 5


def core_tools() -> list[str]:
    """Global default core tool names. Profiles may override via profile.core_tools.
    Empty = no core tools (everything is deferred when tool_search is on)."""
    return app_config.get_nested("proxy.core_tools", []) or []


def proxy_host() -> str:
    return app_config.get_nested("proxy.host", "127.0.0.1")


def proxy_port() -> int:
    return int(app_config.get_nested("proxy.port", 1235))


def upstream_url() -> str:
    """LM Studio (or other backend) base URL."""
    return app_config.get_nested("proxy.upstream_url", "http://localhost:1234")


def upstream_model() -> str:
    """Model name for lightweight proxy-internal LLM calls (query classifier etc.)."""
    return app_config.get_nested("proxy.upstream_model", app_config.get_nested(
        "tools.claude-local.env.ANTHROPIC_MODEL", "qwen3.5-35b-a3b"
    ))


def enabled() -> bool:
    return bool(app_config.get_nested("proxy.enabled", False))


def debug() -> bool:
    return bool(app_config.get_nested("proxy.debug", False))


def max_roundtrips() -> int:
    """Maximum number of intercept round-trips for a single request before
    giving up. One round-trip = one upstream call. ToolSearch, managed tools,
    auto-load schema injection, and unloaded-tool guards each consume one.
    """
    return int(app_config.get_nested("proxy.max_roundtrips", 15))


def ping_interval() -> float:
    """Seconds between `event: ping` heartbeats sent to the client during
    long-running streams. Anthropic-aware clients (CC, pivot.claude.ai,
    Office add-ins) treat these as live-progress signals and won't time out.
    The faster `: keepalive` SSE comments still go out every 2s.
    """
    return float(app_config.get_nested("proxy.ping_interval", 10))


def tool_search() -> bool:
    """Enable tool splitting, ToolSearch injection, and deferred tool handling."""
    return bool(app_config.get_nested("proxy.tool_search", False))


def strip_reminders() -> bool:
    """Strip all system-reminder blocks from messages. Works independently or with tool_search."""
    return bool(app_config.get_nested("proxy.strip_reminders", False))


def auto_load_tools() -> bool:
    """Auto-load deferred tool schemas when model calls them without loading first.
    Only takes effect for requests where tool_search produced deferred tools."""
    return bool(app_config.get_nested("proxy.auto_load_tools", False))


def lift_tool_result_images() -> bool:
    """Rewrite array-form tool_result.content so LM Studio accepts it, lifting
    image blocks out as siblings in the same user message."""
    return bool(app_config.get_nested("proxy.lift_tool_result_images", False))


def location() -> str:
    """User's location for context injection (e.g. 'Kolkata, India'). Empty = omit."""
    return str(app_config.get_nested("proxy.location", "") or "")


# ── Web search (Brave Search scraper) ──────────────────────────────────────

def cors_origins() -> list[str]:
    """CORS allowed origins. Empty list = CORS disabled."""
    return app_config.get_nested("proxy.cors_origins", [])


def client_profiles() -> list[dict]:
    """Header-based client profiles for per-client request handling.

    Example (settings.json):
        "proxy": {
          "client_profiles": [
            {
              "name": "office",
              "match": {"header": "Referer", "contains": "pivot.claude.ai"},
              "system_instruction": "proxy_office.md",
              "tool_search": false,
              "intercept": false,
              "inject_date_location": false
            }
          ]
        }

    First profile whose `match` matches wins. If no match, default behavior applies.
    """
    return app_config.get_nested("proxy.client_profiles", []) or []


def model_mapping() -> dict[str, str]:
    """Map client-facing model names to upstream model names.

    e.g. {"claude-opus-4-6": "qwen3.5-35b-a3b"}

    - /v1/models response lists the keys (client-facing names) alongside real models
    - /v1/messages rewrites request's `model` field from key → value before forwarding
    """
    return app_config.get_nested("proxy.model_mapping", {}) or {}


