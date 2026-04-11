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


def tool_splitting() -> bool:
    """Enable tool splitting, ToolSearch injection, and deferred tool handling."""
    return bool(app_config.get_nested("proxy.tool_splitting", False))


def strip_reminders() -> bool:
    """Strip all system-reminder blocks from messages. Works independently or with tool_splitting."""
    return bool(app_config.get_nested("proxy.strip_reminders", False))


def auto_load_tools() -> bool:
    """Auto-load deferred tool schemas when model calls them without loading first.
    Only effective when tool_splitting is enabled."""
    return tool_splitting() and bool(app_config.get_nested("proxy.auto_load_tools", False))


def lift_tool_result_images() -> bool:
    """Rewrite array-form tool_result.content so LM Studio accepts it, lifting
    image blocks out as siblings in the same user message."""
    return bool(app_config.get_nested("proxy.lift_tool_result_images", False))


# ── Tool-result rewriters (generic framework) ──────────────────────────────

def tool_result_rewriting() -> bool:
    """Master switch for the tool_result rewriter framework."""
    return bool(app_config.get_nested("proxy.tool_result_rewriting", False))


# ── Web search (one specific rewriter) ─────────────────────────────────────

def web_search_enabled() -> bool:
    """Replace empty WebSearch tool_results with real search results."""
    return bool(app_config.get_nested("proxy.web_search.enabled", False))


def web_search_provider() -> str:
    return str(app_config.get_nested("proxy.web_search.provider", "searxng"))


def web_search_url() -> str:
    """Base URL of the local SearXNG instance (no trailing slash)."""
    return str(app_config.get_nested("proxy.web_search.url", "http://localhost:8888")).rstrip("/")


def web_search_max_results() -> int:
    return int(app_config.get_nested("proxy.web_search.max_results", 5))


# ── SearXNG-specific overlays applied to data/searxng/settings.yml ─────────

# Engines verified against the official searxng/searxng issue tracker AND
# tested directly against the mbaozi/SearXNGforWindows fork from a
# residential IP (April 2026). Bing is the only one that consistently
# returns results without bot-defense interference:
#
#   - bing: ✅ 10 results per query, no rate-limiting
#
# All others fail in one of these ways and are excluded by default:
#
#   - google: HTTP 302 -> google.com/sorry/index (CAPTCHA wall) ->
#     SearxEngineTooManyRequestsException. Issues #531/#1405/#4435/#2498.
#     Only fix is rotating egress IP via paid residential proxy.
#   - duckduckgo: SearxEngineCaptchaException. PR #3955 fix landed after
#     2025.05 but the mbaozi fork is from 2025.05 so it's missing the patch.
#   - mojeek: HTTP 403 -> SearxEngineAccessDeniedException, 24h ban from
#     SearXNG's circuit breaker after a single failure.
#   - qwant: JSONDecodeError — Qwant returns an HTML CAPTCHA page instead
#     of JSON when it suspects a bot.
#   - brave / yahoo: silent 0-result returns. HTML parsers in the fork are
#     out of sync with current brave.com / search.yahoo.com markup;
#     nothing logged because parser succeeds — it just finds no results.
#   - startpage: works (~9 results/query) but suspended after a few hours
#     of use; flaky.
#
# Users can override by setting `proxy.web_search.searxng.engines` in
# settings.json with any combination of engine names from the upstream
# template (254 total engines available).
DEFAULT_SEARXNG_ENGINES = [
    "bing",
]


def web_search_searxng_engines() -> list[str]:
    """Names of search engines to enable in SearXNG. Anything not in this
    list is disabled by overlay onto the upstream settings template."""
    val = app_config.get_nested("proxy.web_search.searxng.engines", DEFAULT_SEARXNG_ENGINES)
    return list(val) if val else DEFAULT_SEARXNG_ENGINES


def web_search_searxng_safesearch() -> int:
    """0 = off, 1 = moderate, 2 = strict."""
    return int(app_config.get_nested("proxy.web_search.searxng.safesearch", 0))


def web_search_searxng_language() -> str:
    """Two-letter language code passed to `search.default_lang`."""
    return str(app_config.get_nested("proxy.web_search.searxng.language", "en"))
