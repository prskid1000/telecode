"""Web search tool for MCP clients.

Same backend as the proxy's managed WebSearch — SearXNG with automatic
category routing via LLM classifier. Just pass a query.
"""
from __future__ import annotations

import logging

from mcp_server.app import mcp_app
from proxy import web_search as _ws

log = logging.getLogger("telecode.mcp_server.web_search")


@mcp_app.tool()
async def web_search(query: str) -> str:
    """Search the web for current information.

    Returns ranked results with titles, URLs, and snippets. Always cite
    the URLs returned as markdown links in your reply.

    The search automatically routes to the best sources (web, news, code,
    academic papers, forums, maps) based on query intent.

    Args:
        query: The search query (2+ characters).

    Returns:
        Formatted multi-line string with search results.
    """
    query = (query or "").strip()
    if len(query) < 2:
        return _ws._format_error(query, "query must be at least 2 characters")

    result_str, count = await _ws.search(query, categories=["general"])
    return result_str
