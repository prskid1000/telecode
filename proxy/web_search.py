"""Web search via Brave Search, with DuckDuckGo HTML fallback.

Primary:   https://search.brave.com/search?q=QUERY
Fallback:  https://html.duckduckgo.com/html/?q=QUERY   (when Brave 429s)

No API key, no self-hosted engine. Both endpoints return SSR HTML with
stable semantic classes; parsing uses only those (not build-hash classes).
"""
from __future__ import annotations

import html as _html
import logging
import re
import urllib.parse
from typing import Any

import aiohttp

log = logging.getLogger("telecode.web_search")

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

# ── DuckDuckGo HTML fallback ───────────────────────────────────────────────
# Used when Brave returns 429 or parses to zero results. DDG's /html/ endpoint
# is a no-JS fallback intended for crawlers; it wraps destination URLs behind
# /l/?uddg=ENCODED which we unwrap.

_DDG_URL = "https://html.duckduckgo.com/html/"
_DDG_RESULT_SPLIT = re.compile(r'<div\s+class="[^"]*\bresult(?:\s|")')
_DDG_TITLE_RE = re.compile(
    r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL,
)
_DDG_SNIPPET_RE = re.compile(
    r'class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL,
)


def _unwrap_ddg(href: str) -> str:
    """DDG wraps URLs as //duckduckgo.com/l/?uddg=ENCODED (or similar).
    Pull out the real destination."""
    if "uddg=" in href:
        try:
            q = urllib.parse.urlparse(href).query
            for k, v in urllib.parse.parse_qsl(q):
                if k == "uddg" and v:
                    return urllib.parse.unquote(v)
        except Exception:
            pass
    if href.startswith("//"):
        return "https:" + href
    return href


def _parse_ddg_html(html: str, max_results: int) -> list[dict[str, str]]:
    parts = _DDG_RESULT_SPLIT.split(html)
    results: list[dict[str, str]] = []
    for part in parts[1 : max_results * 3]:  # DDG mixes sponsored in; overshoot then trim
        m_title = _DDG_TITLE_RE.search(part)
        if not m_title:
            continue
        href = _unwrap_ddg(_html.unescape(m_title.group(1).strip()))
        title = _html.unescape(_TAG_RE.sub("", m_title.group(2))).strip()
        if not href.startswith(("http://", "https://")):
            continue
        snippet = ""
        m_snip = _DDG_SNIPPET_RE.search(part)
        if m_snip:
            snippet = _html.unescape(_TAG_RE.sub("", m_snip.group(1))).strip()
        results.append({"title": title, "url": href, "description": snippet})
        if len(results) >= max_results:
            break
    return results


async def _fetch(session: aiohttp.ClientSession, url: str,
                 params: dict[str, str]) -> tuple[int, str]:
    async with session.get(url, params=params) as resp:
        return resp.status, await resp.text()


async def search(
    query: str,
    max_results: int | None = None,
    **_kwargs: Any,
) -> tuple[str, int]:
    """Search with Brave first, fall back to DuckDuckGo on 429 or empty parse.
    Returns (formatted_result_string, count)."""
    query = (query or "").strip()
    if not query:
        return _format_error("", "empty query"), 0
    n = max_results or 5

    brave_note = ""
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout, headers=_HEADERS) as session:
            # ── Primary: Brave ────────────────────────────────────────
            try:
                status, body = await _fetch(session, _BRAVE_URL, {"q": query, "source": "web"})
                if status == 200:
                    results = _parse_brave_html(body, n)
                    if results:
                        return _format_results(query, results), len(results)
                    brave_note = "brave: empty parse"
                elif status == 429:
                    brave_note = "brave: 429 rate-limited"
                else:
                    brave_note = f"brave: HTTP {status}"
                log.info("web_search: brave fallback — %s", brave_note)
            except Exception as e:
                brave_note = f"brave: {type(e).__name__}: {e}"
                log.info("web_search: brave exception — %s", brave_note)

            # ── Fallback: DuckDuckGo HTML ─────────────────────────────
            try:
                status, body = await _fetch(session, _DDG_URL, {"q": query})
                if status != 200:
                    return _format_error(
                        query, f"{brave_note}; ddg: HTTP {status}"
                    ), 0
                results = _parse_ddg_html(body, n)
                if not results:
                    return _format_error(
                        query, f"{brave_note}; ddg: empty parse"
                    ), 0
                return _format_results(query, results), len(results)
            except Exception as e:
                return _format_error(
                    query, f"{brave_note}; ddg: {type(e).__name__}: {e}"
                ), 0
    except Exception as exc:
        return _format_error(query, str(exc)), 0
