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
        "Search and load tool schemas that are not yet available to you. "
        "If a tool call fails or a tool schema is missing, use this to find "
        "and load the right tool for your task."
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

    # Inject ToolSearch as FIRST tool if we have deferred tools
    if deferred:
        core.insert(0, TOOL_SEARCH_TOOL)

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

    if not deferred:
        return ""

    # Group deferred tools by category
    groups: dict[str, list[str]] = defaultdict(list)
    for tool in deferred:
        cat, short = _categorize(tool["name"])
        groups[cat].append(short)

    # Sort: named categories first, "other" last
    sorted_cats = sorted(groups.keys(), key=lambda c: (c == "other", c))

    lines = [
        "=== TOOL SCHEMAS (loaded via ToolSearch) ===",
        f"Core tools (always available): {', '.join(core_names)}",
        f"Deferred tools ({len(deferred)}) — call ToolSearch(query) to load schema before use:",
    ]
    for cat in sorted_cats:
        names = sorted(groups[cat])
        preview = ", ".join(names[:10])
        if len(names) > 10:
            preview += "..."
        lines.append(f"  [{cat}] ({len(names)}): {preview}")

    return "\n".join(lines)


SKILLS_VS_TOOLS_REMINDER = (
    "<system-reminder>\n"
    "Understanding Skills vs Tools:\n"
    "\n"
    "Skills are instructions, hints, guides, and references. When you invoke a Skill, "
    "it loads domain-specific knowledge that tells you how to approach a task — which "
    "tools to use, what patterns to follow, and how to behave in that context. "
    "Skills steer your behavior. They do not execute actions themselves.\n"
    "\n"
    "Tools are executable actions. They read files, run queries, take screenshots, "
    "search code, and interact with external systems. When a tool schema is not "
    "available to you, call ToolSearch to find and load it.\n"
    "\n"
    "Skills = knowledge about how to work. Tools = actions that do the work.\n"
    "\n"
    "Skill names are exact identifiers from the skills list above. "
    "Do not guess skill names from keywords. "
    "Each skill entry starts with '- name: description'. "
    "The name may contain colons (e.g. plugin:skill). "
    "Use the full identifier before the description text.\n"
    "</system-reminder>"
)


# ── Strip deferred-tool system-reminders from messages ───────────────────────

# Patterns to strip from messages
_STRIP_PATTERNS = [
    # Deferred tool listings (replaced by our catalog)
    re.compile(
        r"<system-reminder>\s*\n?"
        r"The following deferred tools are now available via ToolSearch:.*?"
        r"</system-reminder>",
        re.DOTALL,
    ),
    # context-mode hooks — references deferred tools by full name, confuses model
    re.compile(
        r"<system-reminder>\s*\n?"
        r"SessionStart hook additional context:.*?"
        r"</system-reminder>",
        re.DOTALL,
    ),
    # Companion — wastes tokens
    re.compile(
        r"<system-reminder>\s*\n?"
        r"# Companion.*?"
        r"</system-reminder>",
        re.DOTALL,
    ),
]


def strip_noisy_reminders(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove noisy system-reminders and inject skills vs tools distinction."""

    def _strip_text(text: str) -> str:
        for pat in _STRIP_PATTERNS:
            text = pat.sub("", text)
        return text.strip()

    cleaned = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            new_content = _strip_text(content)
            if new_content:
                cleaned.append({**msg, "content": new_content})
            continue
        if isinstance(content, list):
            new_blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    new_text = _strip_text(text)
                    if new_text:
                        new_blocks.append({**block, "text": new_text})
                        # Inject distinction reminder right after skills listing
                        if "skills are available for use with the Skill tool" in text:
                            new_blocks.append({"type": "text", "text": SKILLS_VS_TOOLS_REMINDER})
                    continue
                new_blocks.append(block)
            if new_blocks:
                cleaned.append({**msg, "content": new_blocks})
            continue
        cleaned.append(msg)
    return cleaned
