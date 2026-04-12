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


# ── Web search ─────────────────────────────────────────────────────────────

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

# Engines tested directly against the mbaozi/SearXNGforWindows fork from a
# residential IP (April 2026). The default set picks ONE best engine per
# distinct purpose — no duplicates:
#
#   startpage        general web search       (~9 results/q, diverse domains;
#                                              chosen over bing because bing
#                                              serves decoy spam to scrapers —
#                                              tested side-by-side: for
#                                              "2026 midterm elections governor"
#                                              bing returned 1 unique domain
#                                              of crypto-blog spam, startpage
#                                              returned 10 diverse real results
#                                              including Wikipedia's exact
#                                              gubernatorial-election article)
#   bing news        current news             (dedicated bing-news module;
#                                              the news variant doesn't suffer
#                                              the same decoy issue as base bing)
#   wikipedia        encyclopedic facts       (mediawiki API; results + infoboxes)
#   wiktionary       word definitions         (mediawiki API)
#   reddit           discussion / forums      (Reddit API, 25 results/q)
#   stackoverflow    programming Q&A          (stackexchange API, 10 results/q)
#   github           code repos               (GitHub search API, 30 results/q)
#   mdn              web/JS API docs          (Mozilla MDN JSON)
#   semantic scholar academic papers          (Semantic Scholar API; indexes
#                                              arxiv + biomed + general)
#
# Excluded as duplicates of the picks above:
#   - wikidata: wikipedia covers same entity facts in prose
#   - wikinews: bing news is broader and more current
#   - wikiquote: niche
#   - gitlab: github is the dominant code host
#   - arxiv: semantic scholar indexes arxiv plus more sources
#   - hackernews: reddit covers tech discussion broader
#
# Excluded as niche / category-specific (only fire on specialized queries):
#   - npm, crates.io, docker hub: package registry searches; useful only for
#     specific package metadata lookups, not general info
#   - currency: only fires on specific phrasings; the model rarely needs
#     currency conversion via search anyway
#
# Excluded as broken from a residential IP (verified empirically):
#   - bing: serves query-specific decoy content (cryptocurrency blogs,
#     Fiverr ads, Hotels.com generic pages) to SearXNG-IP scrapers
#   - google, duckduckgo, mojeek, qwant: bot defenses (CAPTCHA / 403 /
#     HTML instead of JSON / 24h ban); only fix is paid residential proxy
#   - brave, yahoo: silent 0-result returns; HTML parsers out of sync
#     with current brave.com / search.yahoo.com markup
#   - pypi: returns 0 results across multiple queries on this fork build
#   - pubmed, openstreetmap: SearxEngineResponseException(timeout)
#   - openlibrary, mwmbl: SearxEngineUnexpectedException(crash)
#   - reuters: HTTP error
#
# Users can override by setting `proxy.web_search.searxng.engines` in
# settings.json with any combination of engine names from the upstream
# template (254 total engines available). NOTE: startpage may get
# suspended after several hours of heavy use ("flaky" per the upstream
# issue tracker); if that happens, add `bing` back as a fallback.
DEFAULT_SEARXNG_ENGINES = [
    "startpage", "bing news",
    "wikipedia", "wiktionary",
    "reddit", "stackoverflow", "askubuntu",
    "github", "mdn",
    "semantic scholar",
    "photon",
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
