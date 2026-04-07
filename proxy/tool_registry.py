"""Split tools into core (always forwarded) and deferred (searchable).

Stores deferred tools per-request and injects the ToolSearch meta-tool.
Builds dynamic system instruction catalog from deferred tools.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from proxy.config import core_tools

# ── ToolSearch meta-tool definition ──────────────────────────────────────────

TOOL_SEARCH_TOOL: dict[str, Any] = {
    "name": "ToolSearch",
    "description": (
        "Fetch full schemas for deferred tools so you can call them. "
        "You must call this before using any tool not in your core set."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query or regex prefixed with 're:'",
            },
            "max_results": {
                "type": "integer",
                "description": "Max results (default 5)",
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


# ── Dynamic catalog from deferred tools ──────────────────────────────────────

# Known MCP prefix -> friendly category name
_PREFIX_MAP = {
    "mcp__plugin_chrome-devtools-mcp_chrome-devtools__": "chrome-devtools",
    "mcp__plugin_context-mode_context-mode__": "context-mode",
    "mcp__plugin_claude-historian_historian__": "claude-historian",
    "mcp__code-review-graph__": "code-review-graph",
    "mcp__claude_ai_Hauliermagic__hauliermagic-": "Hauliermagic",
    "mcp__claude_ai_Routemagic__routemagic-": "Routemagic",
    "mcp__mcp_server_mysql__": "mysql",
}


def _categorize(name: str) -> tuple[str, str]:
    """Return (category, short_name) for a tool name."""
    for prefix, category in _PREFIX_MAP.items():
        if name.startswith(prefix):
            return category, name[len(prefix):]
    return "other", name


def build_tool_catalog(
    core: list[dict[str, Any]],
    deferred: list[dict[str, Any]],
) -> str:
    """Build a dynamic system instruction describing available tools."""
    core_names = [t["name"] for t in core if t["name"] != "ToolSearch"]

    lines = [
        "Always call ToolSearch before using a tool not listed here: "
        + ", ".join(core_names) + ".",
        "",
    ]

    if not deferred:
        return "\n".join(lines)

    # Group deferred tools by category
    groups: dict[str, list[str]] = defaultdict(list)
    for tool in deferred:
        cat, short = _categorize(tool["name"])
        groups[cat].append(short)

    # Sort: named categories first, "other" last
    sorted_cats = sorted(groups.keys(), key=lambda c: (c == "other", c))

    lines.append(f"Deferred tools ({len(deferred)}):")
    for cat in sorted_cats:
        names = sorted(groups[cat])
        preview = ", ".join(names[:10])
        if len(names) > 10:
            preview += "..."
        lines.append(f"  {cat} ({len(names)}): {preview}")

    return "\n".join(lines)


# ── Strip deferred-tool system-reminders from messages ───────────────────────

# Matches <system-reminder> blocks that contain deferred tool listings
_DEFERRED_REMINDER_RE = re.compile(
    r"<system-reminder>\s*\n?"
    r"The following deferred tools are now available via ToolSearch:.*?"
    r"</system-reminder>",
    re.DOTALL,
)


def strip_deferred_reminders(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove Claude Code's deferred-tool system-reminder blocks from messages."""
    cleaned = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str) and _DEFERRED_REMINDER_RE.search(content):
            new_content = _DEFERRED_REMINDER_RE.sub("", content).strip()
            if new_content:
                cleaned.append({**msg, "content": new_content})
            # Drop message entirely if it was only the reminder
            continue
        if isinstance(content, list):
            new_blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if _DEFERRED_REMINDER_RE.search(text):
                        new_text = _DEFERRED_REMINDER_RE.sub("", text).strip()
                        if new_text:
                            new_blocks.append({**block, "text": new_text})
                        continue
                new_blocks.append(block)
            if new_blocks:
                cleaned.append({**msg, "content": new_blocks})
            continue
        cleaned.append(msg)
    return cleaned
