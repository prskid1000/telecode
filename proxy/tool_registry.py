"""Split tools into core (always forwarded) and deferred (searchable).

Stores deferred tools per-request and injects the ToolSearch meta-tool.
"""
from __future__ import annotations

from typing import Any

from proxy.config import core_tools

# ── ToolSearch meta-tool definition ──────────────────────────────────────────

TOOL_SEARCH_TOOL: dict[str, Any] = {
    "name": "ToolSearch",
    "description": (
        "Search for available tools by keyword or regex. Returns full tool "
        "definitions so you can call them.\n"
        "Available categories: chrome-devtools (browser automation, screenshots, "
        "network), claude-historian (conversation history), context-mode "
        "(index/search large outputs), cron (scheduling), worktree (git), "
        "notebook, LSP, task management, MCP resources, Hauliermagic, Routemagic, "
        "code-review-graph."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Natural language search query, or regex prefixed with 're:'. "
                    'Examples: "chrome screenshot", "re:mcp__plugin.*screenshot"'
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "Max tools to return (default 5).",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}


def split_tools(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split into (core_tools_list, deferred_tools_list).

    Core tools are forwarded as-is. ToolSearch is injected into core.
    """
    core_names = set(core_tools())
    core: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []

    for tool in tools:
        name = tool.get("name", "")
        if name in core_names:
            core.append(tool)
        elif name == "ToolSearch":
            # Already injected — skip duplicate
            continue
        else:
            deferred.append(tool)

    # Inject ToolSearch if we have deferred tools
    if deferred:
        core.append(TOOL_SEARCH_TOOL)

    return core, deferred
