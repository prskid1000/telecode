"""Web search via Brave Search scraping.

Scrapes https://search.brave.com/search?q=QUERY — no API key, no
self-hosted engine. Results are in the raw HTML (Svelte SSR, no JS
execution needed). URLs are direct (no redirect wrapper).

Stable CSS selectors used (not svelte-XXXXX build hashes):
  - div.snippet[data-type="web"]  → result container
  - div.title[title="..."]        → full title in `title` attr
  - a[href="https://..."]         → direct destination URL
  - div.content                   → snippet text
"""
from __future__ import annotations

import html as _html
import re
from typing import Any

import aiohttp

_BRAVE_URL = "https://search.brave.com/search"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "DNT": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "keep-alive",
}

_REMINDER = (
    "REMINDER: You MUST cite the sources above in your response to the user "
    "using markdown hyperlinks."
)

# ── HTML parsing ───────────────────────────────────────────────────────────
# Split on snippet boundaries, extract title/url/snippet via regex.
# Avoids svelte-XXXXX class hashes — uses only stable semantic classes.

_SNIPPET_SPLIT = re.compile(
    r'<div\s+[^>]*class="snippet\b[^"]*"[^>]*data-type="web"',
)
_TITLE_RE = re.compile(
    r'class="title[^"]*"\s+title="([^"]*)"',
)
_URL_RE = re.compile(
    r'<a\s+href="(https?://[^"]+)"',
)
_CONTENT_RE = re.compile(
    r'<div\s+class="content\s+desktop-default-regular[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _parse_brave_html(html: str, max_results: int) -> list[dict[str, str]]:
    parts = _SNIPPET_SPLIT.split(html)
    results: list[dict[str, str]] = []
    for part in parts[1 : max_results + 1]:
        m_title = _TITLE_RE.search(part)
        m_url = _URL_RE.search(part)
        if not m_title or not m_url:
            continue
        m_snip = _CONTENT_RE.search(part)
        snippet = ""
        if m_snip:
            snippet = _html.unescape(_TAG_RE.sub("", m_snip.group(1))).strip()
        results.append({
            "title": _html.unescape(m_title.group(1)).strip(),
            "url": _html.unescape(m_url.group(1)).strip(),
            "description": snippet,
        })
    return results


# ── Formatting ─────────────────────────────────────────────────────────────

def _format_results(query: str, results: list[dict[str, Any]]) -> str:
    lines = [f'Web search results for query: "{query}"', ""]
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "(no title)").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("description") or "").strip()
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


# ── Search ─────────────────────────────────────────────────────────────────

async def search(
    query: str,
    max_results: int | None = None,
    **_kwargs: Any,
) -> tuple[str, int]:
    """Scrape Brave Search. Returns (formatted_result_string, count)."""
    query = (query or "").strip()
    if not query:
        return _format_error("", "empty query"), 0
    n = max_results or 5

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        params = {"q": query, "source": "web"}
        async with aiohttp.ClientSession(timeout=timeout, headers=_HEADERS) as session:
            async with session.get(_BRAVE_URL, params=params) as resp:
                if resp.status == 429:
                    unlock_url = f"https://search.brave.com/search?q={query.replace(' ', '+')}"
                    return _format_error(
                        query,
                        f"Brave Search rate limited (HTTP 429). "
                        f"Ask the user to open this link in their browser to unlock: {unlock_url} "
                        f"— opening it in a browser often resets the rate limit for this IP. "
                        f"Then try searching again."
                    ), 0
                if resp.status != 200:
                    body = await resp.text()
                    return _format_error(query, f"Brave HTTP {resp.status}: {body[:200]}"), 0
                html = await resp.text()
    except Exception as exc:
        return _format_error(query, str(exc)), 0

    results = _parse_brave_html(html, n)
    if not results:
        return _format_error(query, "no results (HTML parse returned empty)"), 0

    return _format_results(query, results), len(results)
