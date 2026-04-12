"""Web search tool — same schema as the proxy-intercepted WebSearch.

Exposes the SearXNG-backed search that the proxy uses, so MCP clients
(Claude Code with the `telecode` MCP attached, or any other MCP client)
can call it directly.

Schema matches the proxy's `WebSearch` tool: `query` (required) +
`categories` (optional enum array). Result is a formatted string with
`[N] Title / URL / Snippet` blocks.
"""
from __future__ import annotations

import logging
from typing import Optional

from mcp_server.app import mcp_app
from proxy import web_search as _ws

log = logging.getLogger("telecode.mcp_server.web_search")


@mcp_app.tool()
async def web_search(
    query: str,
    categories: Optional[list[str]] = None,
    max_results: int = 5,
) -> str:
    """Search the web for current information.

    Returns ranked results with titles, URLs, and snippets. Always cite
    the URLs returned as markdown links in your reply.

    Categories control which sources are searched:
      - general: Web search (startpage, wikipedia, wiktionary)
      - news: Current news (bing news)
      - code: Code repos, Q&A, docs (github, stackoverflow, askubuntu, mdn)
      - science: Academic papers (semantic scholar)
      - discussion: Forums and discussions (reddit)
      - map: Locations and geocoding (photon/OpenStreetMap)

    Multiple categories can be combined. Default is ["general"].

    Args:
        query: The search query (2+ characters).
        categories: Which source types to search. Default: ["general"].
        max_results: Number of results to return (1-20). Default: 5.

    Returns:
        Formatted multi-line string with search results and a reminder
        to cite sources.
    """
    query = (query or "").strip()
    if len(query) < 2:
        return _ws._format_error(query, "query must be at least 2 characters")

    cats = list(categories or ["general"])
    result_str, count = await _ws.search(query, categories=cats, max_results=max_results)
    return result_str
