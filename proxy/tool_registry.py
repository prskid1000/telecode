"""Split tools into core (always forwarded) and deferred (searchable).

Stores deferred tools per-request and injects the ToolSearch meta-tool.
Builds dynamic system instruction catalog from deferred tools.
"""
from __future__ import annotations

import re
from typing import Any

from proxy.config import core_tools, strip_reminders

# ── ToolSearch meta-tool definition ──────────────────────────────────────────

TOOL_SEARCH_TOOL: dict[str, Any] = {
    "name": "ToolSearch",
    "description": (
        "Fetches full schema definitions for deferred tools so they can be called.\n\n"
        "Deferred tools appear by name in <system-reminder> messages. Until fetched, "
        "only the name is known \u2014 there is no parameter schema, so the tool cannot "
        "be invoked. This tool takes a query, matches it against the deferred tool list, "
        "and returns the matched tools' complete JSONSchema definitions inside a "
        "<functions> block. Once a tool's schema appears in that result, it is callable "
        "exactly like any tool defined at the top of the prompt.\n\n"
        "Result format: each matched tool appears as one "
        '<function>{"description": "...", "name": "...", "parameters": {...}}</function> '
        "line inside the <functions> block \u2014 the same encoding as the tool list at "
        "the top of this prompt.\n\n"
        "Query forms:\n"
        '- "select:Read,Edit,Grep" \u2014 fetch these exact tools by name\n'
        '- "notebook jupyter" \u2014 keyword search, up to max_results best matches\n'
        '- "+slack send" \u2014 require "slack" in the name, rank by remaining terms'
    ),
    "input_schema": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    'Query to find deferred tools. Use "select:<tool_name>" '
                    "for direct selection, or keywords to search."
                ),
            },
            "max_results": {
                "default": 5,
                "description": "Maximum number of results to return (default: 5)",
                "type": "number",
            },
        },
        "required": ["query", "max_results"],
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


# ── Deferred tool listing for in-place replacement ─────────────────────────

PROXY_SYSTEM_INSTRUCTION = """\
Prefer dedicated tools over shell commands. \
Skills load domain instructions — tools execute actions. \
Some tools have unloaded schemas — use ToolSearch to load them before calling. \
Always load a tool's schema before calling it for the first time."""


def build_deferred_listing(deferred: list[dict[str, Any]]) -> str:
    """Build a deferred tool name list for injection into messages."""
    names = [t["name"] for t in deferred]
    lines = [
        "<system-reminder>",
        "Deferred tools (call ToolSearch to load schema before use):",
    ]
    for name in names:
        lines.append(name)
    lines.append("</system-reminder>")
    return "\n".join(lines)


# ── Rewrite deferred-tool reminders in messages ───────────────────────────

_DEFERRED_LISTING_RE = re.compile(
    r"<system-reminder>\s*\n?"
    r"The following deferred tools are now available via ToolSearch:.*?"
    r"</system-reminder>",
    re.DOTALL,
)

# Matches any <system-reminder>...</system-reminder> block
_ALL_REMINDERS_RE = re.compile(
    r"<system-reminder>.*?</system-reminder>",
    re.DOTALL,
)


def rewrite_messages(
    messages: list[dict[str, Any]],
    deferred: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace deferred-tool listings in messages with our actual deferred list.

    If no existing listing found, inject one into the first user message.
    If strip_reminders() is true, strips ALL system-reminder blocks first.
    """
    replacement = build_deferred_listing(deferred) if deferred else ""
    found_listing = False
    strip_all = strip_reminders()

    def _rewrite_text(text: str) -> str:
        nonlocal found_listing
        if strip_all:
            # Strip all system-reminder blocks
            text = _ALL_REMINDERS_RE.sub("", text)
        else:
            # Only replace deferred listings
            if _DEFERRED_LISTING_RE.search(text):
                found_listing = True
                text = _DEFERRED_LISTING_RE.sub(replacement, text)
        return text.strip()

    cleaned = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            new_content = _rewrite_text(content)
            if new_content:
                cleaned.append({**msg, "content": new_content})
            continue
        if isinstance(content, list):
            new_blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    new_text = _rewrite_text(text)
                    if new_text:
                        new_blocks.append({**block, "text": new_text})
                    continue
                new_blocks.append(block)
            if new_blocks:
                cleaned.append({**msg, "content": new_blocks})
            continue
        cleaned.append(msg)

    # No existing listing — inject into first user message
    if not found_listing and replacement:
        for msg in cleaned:
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                msg["content"] = f"{replacement}\n{content}"
            elif isinstance(content, list):
                content.insert(0, {"type": "text", "text": replacement})
            break

    return cleaned
