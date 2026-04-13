"""Web search tool for MCP clients.

Same backend as the proxy's managed WebSearch — Brave Search API.
"""
from __future__ import annotations

import logging

from mcp_server.app import mcp_app
from proxy import web_search as _ws

log = logging.getLogger("telecode.mcp_server.web_search")


@mcp_app.tool()
async def web_search(query: str) -> str:
    """Search the public web via Brave Search. Returns titles, URLs, snippets.

    USE when the user needs information that lives on external websites:
    current events, third-party API docs, package versions, tutorials for
    unfamiliar tech, facts newer than your training cutoff.

    DO NOT USE for:
      - tasks a dedicated tool can do (databases, files, git, local commands)
      - things you already know confidently
      - "how do I do X" when X is something the existing tool set handles

    Before calling, check if a deferred or core tool matches the domain. If
    one does, call `ToolSearch` or that tool directly — not web_search.

    Cite returned URLs as markdown links in your reply.

    Args:
        query: The search query (2+ characters).
    """
    query = (query or "").strip()
    if len(query) < 2:
        return _ws._format_error(query, "query must be at least 2 characters")

    result_str, count = await _ws.search(query, categories=["general"])
    return result_str
