"""Split tools into core (always forwarded) and deferred (searchable).

Stores deferred tools per-request and injects the ToolSearch meta-tool.
Builds dynamic system instruction catalog from deferred tools.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from config import get_nested as _settings_get
from proxy.config import core_tools, strip_reminders, web_search_enabled

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


# ── WebSearch tool definition (replaces CC's built-in WebSearch) ───────────

WEB_SEARCH_TOOL: dict[str, Any] = {
    "name": "WebSearch",
    "description": (
        "Search the web via Brave Search. Use this whenever you need information "
        "that might be outdated in your training data, or when the user explicitly "
        "asks you to search/look up something. Returns titles, URLs, and snippets "
        "— always cite the URLs as markdown links in your response.\n\n"
        "Just provide the query. If your first search doesn't find what you need, "
        "try different keywords before giving up. You can use WebFetch on any "
        "returned URL to read the full page content if the snippet isn't enough."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query.",
                "minLength": 2,
            },
            "max_results": {
                "type": "integer",
                "description": (
                    "Number of results to return (1-20, default 5). "
                    "Start with 3-5 for quick lookups. "
                    "Use 10-15 for broad research. "
                    "If initial results are insufficient, refine the query "
                    "rather than requesting 20 upfront."
                ),
                "default": 5,
                "minimum": 1,
                "maximum": 20,
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
    Managed tools (WebSearch, speak, transcribe, etc.) are stripped from
    CC's tool list and replaced with our proxy-handled versions — the
    model calls them, the proxy intercepts and executes locally.
    """
    from proxy.managed_tools import get_schemas, get_strip_names

    core_names = set(core_tools())
    strip_names = get_strip_names() | {"ToolSearch"}
    core: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []

    for tool in tools:
        name = tool.get("name", "")
        if name in strip_names:
            continue
        if name in core_names:
            core.append(tool)
        else:
            deferred.append(tool)

    if deferred:
        core.insert(0, TOOL_SEARCH_TOOL)
    for schema in get_schemas():
        core.insert(0, schema)

    return core, deferred


# ── System instruction loading + conditional preprocessing ────────────────

# `<if dotted.settings.path="value">...</if>` blocks let proxy_system.md gate
# paragraphs by current settings. Tags must live on their own lines. Flat only —
# no nesting.
_IF_TAG_RE = re.compile(
    r'<if\s+([\w.]+)="([^"]*)">[ \t]*\n(.*?)\n[ \t]*</if>[ \t]*\n?',
    re.DOTALL,
)


def _preprocess_conditionals(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        path, expected, content = m.group(1), m.group(2), m.group(3)
        actual = _settings_get(path, None)
        if isinstance(actual, bool):
            actual_str = "true" if actual else "false"
        elif actual is None:
            actual_str = ""
        else:
            actual_str = str(actual)
        return content + "\n" if actual_str == expected else ""
    return _IF_TAG_RE.sub(repl, text)


_FALLBACK_INSTRUCTION = (
    "If a tool is not in your available tools list, you cannot call it. "
    "You must call ToolSearch first — it will return the tool's schema. "
    "Only after receiving the schema can you call that tool. "
    "Calling an unloaded tool without its schema will always fail."
)


def proxy_system_instruction() -> str:
    """Load proxy_system.md and resolve `<if>` conditionals against current settings.

    Re-read each call so settings hot-reload and doc edits both take effect.
    """
    md_path = Path(__file__).resolve().parent.parent / "proxy_system.md"
    try:
        text = md_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _FALLBACK_INSTRUCTION
    return _preprocess_conditionals(text).strip()


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

# Patterns for reminders we want to KEEP even when strip_reminders is on
_SKILLS_REMINDER_RE = re.compile(
    r"<system-reminder>\s*\n?"
    r"The following skills are available.*?"
    r"</system-reminder>",
    re.DOTALL,
)
_DEFERRED_KEEP_RE = re.compile(
    r"<system-reminder>\s*\n?"
    r"Deferred tools \(call ToolSearch.*?"
    r"</system-reminder>",
    re.DOTALL,
)


def _strip_reminders_except_preserved(text: str) -> str:
    """Strip all system-reminder blocks EXCEPT skills listings and our deferred listing."""
    # Extract blocks we want to keep
    preserved = _SKILLS_REMINDER_RE.findall(text) + _DEFERRED_KEEP_RE.findall(text)
    # Strip all reminders
    text = _ALL_REMINDERS_RE.sub("", text)
    # Re-append preserved blocks
    if preserved:
        text = text.rstrip() + "\n\n" + "\n\n".join(preserved)
    return text


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
    """Strip system-reminder blocks from messages, preserving skills listings."""
    return _apply_to_messages(
        messages, lambda t: _strip_reminders_except_preserved(t).strip()
    )


def lift_tool_result_images(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Work around LM Studio rejecting array-form `tool_result.content`.

    Anthropic's spec allows `tool_result.content` to be either a string or an
    array of text/image blocks. LM Studio's /v1/messages parser only accepts
    strings, AND it enforces that all tool_result blocks come first in the
    user message (before any text/image blocks). Together these mean an image
    returned by Read or a screenshot tool can't ride inside the tool_result.

    Fix: for each user message containing a tool_result with a list `content`,
    rewrite the tool_result content as a plain string and lift image/text
    blocks out to the END of the message. Because tool_results must stay
    contiguous at the start, position alone can no longer encode provenance, so
    each lifted image is preceded by a text label like `tool_use_id=abc.1:`
    matching a label inside the tool_result's placeholder string. This gives
    the model both a count and a name-matching reference for every image.
    """
    rewritten: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "user":
            rewritten.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            rewritten.append(msg)
            continue

        head: list[Any] = []  # tool_result + other non-lifted blocks in order
        lifted: list[dict[str, Any]] = []  # labeled images to append at end
        changed = False

        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                head.append(block)
                continue
            tr_content = block.get("content")
            if not isinstance(tr_content, list):
                head.append(block)
                continue

            changed = True
            text_parts: list[str] = []
            image_blocks: list[dict[str, Any]] = []
            for sub in tr_content:
                if not isinstance(sub, dict):
                    continue
                stype = sub.get("type")
                if stype == "text":
                    text_parts.append(sub.get("text", ""))
                elif stype == "image":
                    image_blocks.append(sub)

            tool_use_id = block.get("tool_use_id", "")
            joined_text = "\n".join(p for p in text_parts if p)

            if image_blocks:
                labels = [
                    f"tool_use_id={tool_use_id}.{i + 1}"
                    for i in range(len(image_blocks))
                ]
                n = len(image_blocks)
                noun = "image" if n == 1 else "images"
                placeholder = (
                    f"[{n} {noun} from tool_use_id={tool_use_id} appended at "
                    f"the end of this user message, labeled: {', '.join(labels)}]"
                )
                if joined_text:
                    placeholder = f"{joined_text}\n\n{placeholder}"
                head.append({**block, "content": placeholder})
                for label, img in zip(labels, image_blocks):
                    lifted.append({"type": "text", "text": f"{label}:"})
                    lifted.append(img)
            else:
                head.append({**block, "content": joined_text or "[empty tool result]"})

        if changed:
            rewritten.append({**msg, "content": head + lifted})
        else:
            rewritten.append(msg)
    return rewritten


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
            text = _strip_reminders_except_preserved(text)
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
