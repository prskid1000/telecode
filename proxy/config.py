"""Proxy configuration — reads from main settings.json.

Upstream is always llama-server (spawned by the supervisor in `llamacpp/`).
The proxy presents both Anthropic (`/v1/messages`) and OpenAI
(`/v1/chat/completions`) surfaces to clients and translates to llama.cpp's
OpenAI-compatible endpoints internally.
"""
from __future__ import annotations

import config as app_config
from llamacpp import config as llama_cfg

# BM25 tuning (kept — proxy-internal ToolSearch engine)
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
    """llama-server base URL.

    Sourced from `llamacpp.host`/`llamacpp.port` (the supervisor owns the
    process). Falls back to `proxy.upstream_url` only if `llamacpp.enabled`
    is false — i.e. someone is running a stand-alone llama-server and
    pointed the proxy at it manually.
    """
    if llama_cfg.enabled():
        return llama_cfg.upstream_url()
    override = app_config.get_nested("proxy.upstream_url", "") or ""
    return override or llama_cfg.upstream_url()


def protocols() -> list[str]:
    """Which client-facing protocols to expose.

    Values: "anthropic", "openai". Default both. Disabling one just
    unregisters the corresponding routes; there is no per-request toggle.
    """
    configured = app_config.get_nested("proxy.protocols", ["anthropic", "openai"])
    if not configured:
        return ["anthropic", "openai"]
    # Normalize + filter
    known = {"anthropic", "openai"}
    return [p for p in configured if p in known] or ["anthropic", "openai"]


def enabled() -> bool:
    return bool(app_config.get_nested("proxy.enabled", False))


def debug() -> bool:
    return bool(app_config.get_nested("proxy.debug", False))


def max_roundtrips() -> int:
    """Max intercept round-trips per request (ToolSearch, managed tools,
    auto-load, unloaded-guard each consume one)."""
    return int(app_config.get_nested("proxy.max_roundtrips", 15))


def ping_interval() -> float:
    """Seconds between `event: ping` heartbeats."""
    return float(app_config.get_nested("proxy.ping_interval", 10))


def tool_search() -> bool:
    return bool(app_config.get_nested("proxy.tool_search", False))


def strip_reminders() -> bool:
    return bool(app_config.get_nested("proxy.strip_reminders", False))


def auto_load_tools() -> bool:
    return bool(app_config.get_nested("proxy.auto_load_tools", False))


def sort_tools() -> bool:
    """When true, sort body.tools alphabetically by name before sending
    to llama.cpp. Stabilises the prompt prefix across turns when a client
    reorders its tool list, at the cost of overriding any deliberate
    primacy ordering. Off by default — enable per-profile for known-bad
    clients."""
    return bool(app_config.get_nested("proxy.sort_tools", False))


def location() -> str:
    """User's location for context injection. Empty = omit."""
    return str(app_config.get_nested("proxy.location", "") or "")


def cors_origins() -> list[str]:
    """CORS allowed origins. Empty list = CORS disabled."""
    return app_config.get_nested("proxy.cors_origins", [])


def client_profiles() -> list[dict]:
    """Header-based client profiles for per-client request handling.

    First profile whose `match` matches wins. See README for full shape.
    Each profile can override any proxy feature flag.
    """
    return app_config.get_nested("proxy.client_profiles", []) or []


def model_mapping() -> dict[str, str]:
    """Map client-facing model names to llama.cpp registry keys.

    e.g. {"claude-opus-4-6": "qwen3.5-35b"}

    - /v1/models response lists the keys (client-facing names) alongside
      registered models
    - /v1/messages rewrites request's `model` field from key → value before
      the supervisor picks which model to run
    """
    return app_config.get_nested("proxy.model_mapping", {}) or {}
