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
If a tool is not in your available tools list, you cannot call it. \
You must call ToolSearch first — it will return the tool's schema. \
Only after receiving the schema can you call that tool. \
Calling an unloaded tool without its schema will always fail."""


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


# ── Message rewriting ──────────────────────────────────────────────────────

_DEFERRED_LISTING_RE = re.compile(
    r"<system-reminder>\s*\n?"
    r"The following deferred tools are now available via ToolSearch:.*?"
    r"</system-reminder>",
    re.DOTALL,
)

_ALL_REMINDERS_RE = re.compile(
    r"<system-reminder>.*?</system-reminder>",
    re.DOTALL,
)


def _apply_to_messages(
    messages: list[dict[str, Any]],
    text_fn,
) -> list[dict[str, Any]]:
    """Apply text_fn to all text content in messages, dropping empty results."""
    cleaned = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            new_content = text_fn(content)
            if new_content:
                cleaned.append({**msg, "content": new_content})
            continue
        if isinstance(content, list):
            new_blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    new_text = text_fn(block.get("text", ""))
                    if new_text:
                        new_blocks.append({**block, "text": new_text})
                    continue
                new_blocks.append(block)
            if new_blocks:
                cleaned.append({**msg, "content": new_blocks})
            continue
        cleaned.append(msg)
    return cleaned


def strip_all_reminders(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip all <system-reminder> blocks from messages."""
    return _apply_to_messages(
        messages, lambda t: _ALL_REMINDERS_RE.sub("", t).strip()
    )


def rewrite_messages(
    messages: list[dict[str, Any]],
    deferred: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace deferred-tool listings with ours. Inject if none found."""
    replacement = build_deferred_listing(deferred) if deferred else ""
    found = False

    def _rewrite(text: str) -> str:
        nonlocal found
        if strip_reminders():
            text = _ALL_REMINDERS_RE.sub("", text)
        elif _DEFERRED_LISTING_RE.search(text):
            found = True
            text = _DEFERRED_LISTING_RE.sub(replacement, text)
        return text.strip()

    cleaned = _apply_to_messages(messages, _rewrite)

    # Inject into first user message if no existing listing was replaced
    if not found and replacement:
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
