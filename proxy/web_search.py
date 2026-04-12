"""Web search backend via Brave Search API.

Direct API calls — no self-hosted engine, no Docker, no venv. Just an
API key from https://brave.com/search/api/ ($5/month for ~1000 queries).

The managed tools framework handles interception, visibility, and
round-tripping. This module just does the search + formats results.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

import aiohttp

from proxy import config as proxy_config

log = logging.getLogger("telecode.proxy.web_search")

_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

_REMINDER = (
    "REMINDER: You MUST cite the sources above in your response to the user "
    "using markdown hyperlinks."
)


def _format_results(query: str, results: list[dict[str, Any]]) -> str:
    lines = [f'Web search results for query: "{query}"', ""]
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "(no title)").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("description") or r.get("content") or "").strip()
        lines.append(f"[{i}] {title}")
        if url:
            lines.append(f"URL: {url}")
        if snippet:
            lines.append(f"Snippet: {snippet}")
        lines.append("")
    lines.append(_REMINDER)
    return "\n".join(lines)


def _format_error(query: str, message: str) -> str:
    return (
        f'Web search results for query: "{query}"\n\n'
        f"ERROR: {message}\n\n"
        f"Tell the user the web search failed and continue without these results."
    )


# ── Cache ──────────────────────────────────────────────────────────────────

_CACHE: "OrderedDict[str, tuple[str, int]]" = OrderedDict()
_CACHE_MAX = 256


def _cache_get(key: str) -> tuple[str, int] | None:
    val = _CACHE.get(key)
    if val is not None:
        _CACHE.move_to_end(key)
    return val


def _cache_put(key: str, value: tuple[str, int]) -> None:
    _CACHE[key] = value
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)


# ── Search ─────────────────────────────────────────────────────────────────

async def search(
    query: str,
    max_results: int | None = None,
    **_kwargs: Any,
) -> tuple[str, int]:
    """Search via Brave API. Returns (formatted_result_string, count).

    Always returns a string (never raises) — errors become visible
    ERROR strings so the model can keep going.
    """
    query = (query or "").strip()
    if not query:
        return _format_error("", "empty query"), 0
    n = max_results or 5

    cache_key = f"{n}:{query}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    api_key = proxy_config.web_search_api_key()
    if not api_key:
        return _format_error(query, "Brave API key missing — set proxy.web_search.api_key in settings.json"), 0

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        headers = {
            "X-Subscription-Token": api_key,
            "Accept": "application/json",
        }
        params = {"q": query, "count": str(n)}
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(_BRAVE_URL, headers=headers, params=params) as resp:
                if resp.status == 401:
                    return _format_error(query, "Brave API key invalid (401)"), 0
                if resp.status == 429:
                    return _format_error(query, "Brave API rate limited (429) — try again later"), 0
                if resp.status != 200:
                    body = await resp.text()
                    return _format_error(query, f"Brave HTTP {resp.status}: {body[:200]}"), 0
                data = await resp.json()
    except Exception as exc:
        log.warning("Brave search failed: %s", exc)
        return _format_error(query, str(exc)), 0

    results = (data.get("web") or {}).get("results") or []
    if not results:
        return _format_error(query, "no results"), 0

    out = _format_results(query, results[:n])
    count = len(results[:n])
    _cache_put(cache_key, (out, count))
    return out, count
