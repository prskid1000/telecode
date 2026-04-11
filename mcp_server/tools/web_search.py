"""Web search tool — same schema as Claude's official WebSearch.

Exposes the SearXNG-backed search that the proxy WebSearch rewriter uses,
so MCP clients (Claude Code with the `telecode` MCP attached, or any other
MCP client) can call it directly without going through the
intercept-empty-tool-result path.

Schema mirrors `WebSearch` from Anthropic's tool use docs:
  - query (string, required, min 2 chars)
  - allowed_domains (string list, optional) — only return results from these
  - blocked_domains (string list, optional) — drop results from these

Result format also mirrors Anthropic's tool: a single string with `[N] Title
/ URL / Snippet` blocks plus a closing REMINDER about citing sources, so a
local model answering from this tool produces the same shape of output as
one talking to the real Anthropic backend.
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Optional

from mcp_server.app import mcp_app

# Reuse the SearXNG client + formatter from the proxy rewriter so the two
# entry points share one cache, one config, and one provider abstraction.
from proxy.rewriters import web_search as _ws

log = logging.getLogger("telecode.mcp_server.web_search")


def _domain_of(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return ""
    return host.lower().lstrip("www.")


def _domain_matches(host: str, patterns: list[str]) -> bool:
    """Match a hostname against a list of allow/block patterns.

    A pattern matches if the host equals it or ends with `.<pattern>` —
    so `nytimes.com` matches `www.nytimes.com` and `cooking.nytimes.com`.
    """
    host = host.lower().lstrip(".")
    for p in patterns:
        p = p.lower().lstrip(".").strip()
        if not p:
            continue
        if host == p or host.endswith("." + p):
            return True
    return False


@mcp_app.tool()
async def web_search(
    query: str,
    allowed_domains: Optional[list[str]] = None,
    blocked_domains: Optional[list[str]] = None,
) -> str:
    """Search the web and return ranked results with titles, URLs, and snippets.

    Use this for current events, factual lookups, recent docs, or anything
    that needs information from the internet. Always cite the URLs returned
    as markdown links in your reply to the user.

    Args:
        query: The search query (2+ characters).
        allowed_domains: If set, only return results whose hostname matches
            one of these domains (e.g. ["nytimes.com", "github.com"]).
            Subdomain matches are included.
        blocked_domains: If set, drop results whose hostname matches one of
            these domains. Applied after `allowed_domains`.

    Returns:
        A formatted multi-line string. Each result is `[N] Title / URL /
        Snippet`. A `Summary:` line may appear at the top if the search
        backend produced one. Errors are returned as a string starting
        `Web search results for query: "..."` followed by `ERROR: ...` so
        the model can detect failure without raising.
    """
    query = (query or "").strip()
    if len(query) < 2:
        return _ws._format_error(query, "query must be at least 2 characters")

    # Pull a few extras up front so we still have enough left after filtering.
    n = _ws.proxy_config.web_search_max_results()
    fetch_n = max(n * 3, n + 5) if (allowed_domains or blocked_domains) else n

    provider_name = _ws.proxy_config.web_search_provider()
    fn = _ws._PROVIDERS.get(provider_name)
    if fn is None:
        return _ws._format_error(query, f"unknown provider {provider_name!r}")

    try:
        results, answer = await fn(query, fetch_n)
    except Exception as exc:
        log.warning("MCP web_search %s failed: %s", provider_name, exc)
        return _ws._format_error(query, str(exc))

    # Apply allow/block lists. Empty lists mean "no filter".
    allow = list(allowed_domains or [])
    block = list(blocked_domains or [])
    if allow or block:
        filtered = []
        for r in results:
            host = _domain_of(r.get("url", ""))
            if not host:
                continue
            if allow and not _domain_matches(host, allow):
                continue
            if block and _domain_matches(host, block):
                continue
            filtered.append(r)
            if len(filtered) >= n:
                break
        results = filtered
    else:
        results = results[:n]

    if not results:
        return _ws._format_error(query, "no results")

    return _ws._format_results(query, results, answer)
