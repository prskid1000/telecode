"""Web search via Brave Search scraping.

Scrapes https://search.brave.com/search?q=QUERY — no API key, no
self-hosted engine. Results are in the raw HTML (Svelte SSR, no JS
execution needed). URLs are direct (no redirect wrapper).

Stable CSS selectors used (not svelte-XXXXX build hashes):
  - div.snippet[data-type="web"]  → result container
  - div.title[title="..."]        → full title in `title` attr
  - a[href="https://..."]         → direct destination URL
  - div.content                   → snippet text

Improvements layered on top (for AI consumers):
  - Strip <script>/<style>/<noscript> before regex (faster, more resilient
    to class-name churn on unrelated page chrome).
  - Normalize + dedup URLs (strip utm_*, fbclid, fragments, lowercase host)
    so tabbed / repeated Brave listings collapse.
  - Optional `fetch_pages`: concurrently fetch top-N destination URLs, run a
    readability pass (strip nav/header/footer/aside/form/iframe/script/style,
    prefer <main>/<article>), trim to ~3 KB each, and inline the text after
    each snippet. Turns the tool from "here are 5 links" into grounded
    content the model can cite directly.
"""
from __future__ import annotations

import asyncio
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

# Per-page fetch budget for fetch_pages=N. Total time ~ max per-page since concurrent.
_PAGE_FETCH_TIMEOUT = 8.0
_PAGE_TEXT_MAX = 3000     # characters of extracted text per page
_PAGE_BYTES_MAX = 1_500_000  # 1.5 MB hard cap; skip larger / binary pages

# ── Pre-filter: remove boilerplate tags that confuse regex + bloat HTML ──────

_BOILERPLATE_RE = re.compile(
    r"<(?:script|style|noscript|svg|template)\b[^>]*>.*?</(?:script|style|noscript|svg|template)>",
    re.IGNORECASE | re.DOTALL,
)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_boilerplate(html: str) -> str:
    """Remove <script>/<style>/<noscript>/<svg>/<template> blocks + comments."""
    html = _COMMENT_RE.sub("", html)
    return _BOILERPLATE_RE.sub("", html)


# ── HTML parsing ─────────────────────────────────────────────────────────────
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
    # Overshoot — some blocks might not parse cleanly, so dedup later trims.
    for part in parts[1 : max_results * 2 + 1]:
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


# ── URL normalization + dedup ────────────────────────────────────────────────

_TRACKING_PREFIXES = ("utm_",)
_TRACKING_EXACT = {"fbclid", "gclid", "mc_cid", "mc_eid", "_ga", "ref", "ref_src",
                   "igshid", "si", "spm", "vero_conv", "vero_id", "yclid"}


def _normalize_url(url: str) -> str:
    """Lowercase scheme+host, drop tracking params, strip fragment, strip
    trailing slash when no query/path remains. Used only for dedup — the
    displayed URL is the original."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    scheme = (p.scheme or "https").lower()
    host = (p.hostname or "").lower()
    # Strip leading www. for dedup — many sites redirect both ways
    if host.startswith("www."):
        host = host[4:]
    port = f":{p.port}" if p.port and not ((scheme == "http" and p.port == 80) or (scheme == "https" and p.port == 443)) else ""
    path = p.path.rstrip("/") or "/"
    # Filter query params
    kept = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
            if not (any(k.lower().startswith(pfx) for pfx in _TRACKING_PREFIXES)
                    or k.lower() in _TRACKING_EXACT)]
    query = urllib.parse.urlencode(kept)
    return f"{scheme}://{host}{port}{path}{('?' + query) if query else ''}"


def _dedup(results: list[dict[str, str]], limit: int) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for r in results:
        key = _normalize_url(r.get("url", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= limit:
            break
    return out


# ── Readable text extraction ─────────────────────────────────────────────────

_CHROME_TAGS_RE = re.compile(
    r"<(?:nav|header|footer|aside|form|iframe|noscript|script|style|svg|template|picture|figure|dialog)\b[^>]*>.*?</(?:nav|header|footer|aside|form|iframe|noscript|script|style|svg|template|picture|figure|dialog)>",
    re.IGNORECASE | re.DOTALL,
)
_MAIN_RE = re.compile(
    r"<(?:main|article)\b[^>]*>(.*?)</(?:main|article)>",
    re.IGNORECASE | re.DOTALL,
)
_WS_RE = re.compile(r"[ \t]+")
_MANY_NL_RE = re.compile(r"\n{3,}")


def _extract_readable_text(html: str) -> str:
    """Strip chrome, prefer <main>/<article>, convert remaining tags to
    text + newlines. Returns a cleaned, trimmed plain-text blob."""
    html = _COMMENT_RE.sub("", html)
    html = _CHROME_TAGS_RE.sub("", html)
    # Prefer the largest <main> or <article> block if present
    blocks = _MAIN_RE.findall(html)
    if blocks:
        html = max(blocks, key=len)
    # Block-level tags → newline
    html = re.sub(r"</?(?:p|br|div|li|tr|h[1-6]|section|ul|ol|blockquote|pre)\b[^>]*>",
                  "\n", html, flags=re.IGNORECASE)
    # All remaining tags → empty
    text = _TAG_RE.sub("", html)
    text = _html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = _MANY_NL_RE.sub("\n\n", text).strip()
    return text


async def _fetch_page_text(session: aiohttp.ClientSession, url: str) -> str:
    """Fetch one URL, extract readable text. Returns '' on any failure."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=_PAGE_FETCH_TIMEOUT),
                                allow_redirects=True) as resp:
            if resp.status != 200:
                return ""
            ctype = (resp.headers.get("content-type") or "").lower()
            if "html" not in ctype and "xml" not in ctype:
                return ""  # skip PDFs, images, JSON, etc.
            # Read up to the byte cap
            body = await resp.content.read(_PAGE_BYTES_MAX + 1)
            if len(body) > _PAGE_BYTES_MAX:
                return ""  # too large — probably not an article
            try:
                html = body.decode(resp.charset or "utf-8", errors="replace")
            except (LookupError, UnicodeDecodeError):
                html = body.decode("utf-8", errors="replace")
    except Exception as exc:
        log.debug("web_search page fetch failed: %s — %s", url, exc)
        return ""
    text = _extract_readable_text(html)
    if len(text) > _PAGE_TEXT_MAX:
        text = text[:_PAGE_TEXT_MAX].rstrip() + "\n…(truncated)"
    return text


# ── Formatting ───────────────────────────────────────────────────────────────

def _format_results(query: str, results: list[dict[str, Any]],
                    page_texts: list[str] | None = None) -> str:
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
        if page_texts and i - 1 < len(page_texts) and page_texts[i - 1]:
            lines.append("Content:")
            lines.append(page_texts[i - 1])
        lines.append("")
    lines.append(_REMINDER)
    return "\n".join(lines)


def _format_error(query: str, message: str) -> str:
    return (
        f'Web search results for query: "{query}"\n\n'
        f"ERROR: {message}\n\n"
        f"Tell the user the web search failed and continue without these results."
    )


# ── Search ───────────────────────────────────────────────────────────────────

async def search(
    query: str,
    max_results: int | None = None,
    fetch_pages: int = 0,
    **_kwargs: Any,
) -> tuple[str, int]:
    """Scrape Brave Search. Returns (formatted_result_string, count).

    Args:
        query: search string
        max_results: number of result rows (default 5)
        fetch_pages: when > 0, concurrently fetch + extract readable text
                     from the top-N URLs and inline it after each snippet.
                     Capped at max_results. Default 0 (snippet-only).
    """
    query = (query or "").strip()
    if not query:
        return _format_error("", "empty query"), 0
    n = max_results or 5
    fetch_n = max(0, min(int(fetch_pages or 0), n))

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout, headers=_HEADERS) as session:
            async with session.get(_BRAVE_URL, params={"q": query, "source": "web"}) as resp:
                if resp.status == 429:
                    unlock_url = f"https://search.brave.com/search?q={urllib.parse.quote_plus(query)}"
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

            html = _strip_boilerplate(html)
            results = _parse_brave_html(html, n)
            results = _dedup(results, n)
            if not results:
                return _format_error(query, "no results (HTML parse returned empty)"), 0

            page_texts: list[str] | None = None
            if fetch_n > 0:
                urls = [r["url"] for r in results[:fetch_n]]
                page_texts = await asyncio.gather(
                    *(_fetch_page_text(session, u) for u in urls),
                    return_exceptions=False,
                )
                # Pad with empty so indexing is simple
                page_texts += [""] * (len(results) - len(page_texts))

            return _format_results(query, results, page_texts), len(results)
    except Exception as exc:
        return _format_error(query, str(exc)), 0
