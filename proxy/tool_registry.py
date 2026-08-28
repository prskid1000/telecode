"""Split tools into core (always forwarded) and deferred (searchable).

Stores deferred tools per-request and injects the ToolSearch meta-tool.
Builds dynamic system instruction catalog from deferred tools.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from config import get_nested as _settings_get
from proxy.config import core_tools, strip_reminders

# ── ToolSearch meta-tool definition ──────────────────────────────────────────

TOOL_SEARCH_TOOL: dict[str, Any] = {
    "name": "ToolSearch",
    "description": (
        "Fetches full schema definitions for unloaded tools so they can be called.\n\n"
        "Unloaded tools appear by name in <system-reminder> messages. Until fetched, "
        "only the name is known \u2014 there is no parameter schema, so the tool cannot "
        "be invoked. This tool takes a query, matches it against the unloaded tool list, "
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
                    'Query to find unloaded tools. Use "select:<tool_name>" '
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
    core_names: set[str],
    strip_names: set[str],
    inject_schemas: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split into (core_tools_list, deferred_tools_list).

    - core_names: tool names always forwarded as-is
    - strip_names: tool names dropped entirely (e.g. CC versions of managed tools)
    - inject_schemas: schemas injected into the core list (managed tools)

    ToolSearch is injected when any tools end up deferred.
    """
    core: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []

    for tool in tools:
        name = tool.get("name", "")
        if name in strip_names:
            continue
        if name == "ToolSearch":
            # The meta-tool is always injected by us — drop any incoming copy
            # so it can't leak into the deferred listing.
            continue
        if name in core_names:
            core.append(tool)
        else:
            deferred.append(tool)

    if deferred:
        core.insert(0, TOOL_SEARCH_TOOL)
    for schema in inject_schemas:
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


def proxy_system_instruction(filename: str = "system.md") -> str:
    """Load a proxy system instruction markdown file and resolve `<if>` conditionals.

    Re-read each call so settings hot-reload and doc edits both take effect.
    `filename` is relative to `proxy/instructions/` (e.g. "system.md", "office.md").
    """
    md_path = Path(__file__).resolve().parent / "instructions" / filename
    try:
        text = md_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _FALLBACK_INSTRUCTION
    return _preprocess_conditionals(text).strip()


def build_deferred_listing(deferred: list[dict[str, Any]]) -> str:
    """Build an unloaded tool name list for injection into messages."""
    names = [t["name"] for t in deferred]
    lines = [
        "<system-reminder>",
        "Unloaded tools (call ToolSearch to load schema before use):",
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
    r"Unloaded tools \(call ToolSearch.*?"
    r"</system-reminder>",
    re.DOTALL,
)

# Per-turn token-budget bookkeeping the client re-emits after EVERY turn
# (Claude Code: `<total_tokens>15000000 tokens left</total_tokens>`). Pure
# noise for a local model with a fixed context, and one more duplicate copy
# accumulates per turn. Covered by the same `strip_reminders` switch.
# Eating the adjacent whitespace stops the surrounding text from growing
# blank lines, so stripping does not itself perturb the prompt prefix.
_TOKEN_BUDGET_RE = re.compile(
    r"[ \t]*\n{0,2}<total_tokens>.*?</total_tokens>[ \t]*",
    re.DOTALL,
)


# ── Client system-prompt noise (always removed, no setting) ────────────────
#
# Three blocks Claude Code puts in its system prompt that are pure cost in
# front of a local model. Offsets below are measured on a real `say hi` from
# Claude Code 2.1.250 in this repo, into the 59,105-char rendered prompt; a
# change at offset N invalidates every token after N.
#
#   x-anthropic-billing-header:  offset 28. Telemetry for Anthropic's billing,
#                                meaningless to llama.cpp. Carries the CLI
#                                version, so every `claude update` invalidates
#                                100% of the prefix cache.
#   # Environment                offset 5,022. cwd / platform / shell / OS.
#   gitStatus:                   offset 5,955. A snapshot taken at session
#                                start, so it is stale by definition, and it
#                                changes on every commit, branch switch and
#                                dirty-file edit — invalidating ~90% of the
#                                prompt, CLAUDE.md included, each time. The
#                                model can run `git status` for live state.
#
# All three live in the LEADING system message and nowhere else, so the
# stripper is only ever applied there. A user pasting `# Environment` into a
# prompt, or a tool result containing a `gitStatus:` line, is out of scope by
# construction rather than by luck of the regex.

# Anchored on Claude Code's exact adjacent wording, not just the heading.
# These three strips are unconditional — no flag gates them — and the same
# `_prepare_internal_body` serves every client, plain OpenAI apps included.
# `# Environment` is a heading anyone might legitimately write, so matching the
# bare heading would silently eat a third party's system prompt. The
# lookaheads make that essentially impossible while still matching every
# Claude Code request.
_BILLING_HEADER_RE = re.compile(r"^x-anthropic-billing-header:.*$\n?", re.M)
_ENV_BLOCK_RE = re.compile(
    r"^# Environment$(?=\nYou have been invoked in the following environment)", re.M)
_GIT_STATUS_RE = re.compile(r"^gitStatus:(?=[ \t]*This is the git status)", re.M)
_TOP_HEADING_RE = re.compile(r"^# \S", re.M)


def _cut_to_next_heading(text: str, start_re: re.Pattern[str]) -> str:
    """Remove each `start_re` match through to the next top-level heading."""
    out = text
    for _ in range(8):
        m = start_re.search(out)
        if not m:
            break
        nxt = _TOP_HEADING_RE.search(out, m.end())
        end = nxt.start() if nxt else len(out)
        head, tail = out[:m.start()], out[end:]
        if head.strip() and tail.strip():
            out = head.rstrip() + "\n\n" + tail.lstrip()
        else:
            out = (head + tail).strip()
    return out


def strip_client_system_noise(text: str) -> str:
    """Drop the billing header, `# Environment` and `gitStatus:` blocks."""
    text = _BILLING_HEADER_RE.sub("", text)
    text = _cut_to_next_heading(text, _ENV_BLOCK_RE)
    text = _cut_to_next_heading(text, _GIT_STATUS_RE)
    return text.strip()


# ── Per-turn context blocks ────────────────────────────────────────────────
#
# Claude Code sends the agent-type roster, the skills catalogue and the MCP
# server instructions as mid-conversation `role:"system"` messages (an
# official Messages API feature, appended at the tail so they do not
# invalidate the cached prefix before them). They are NOT <system-reminder>
# blocks, so `strip_reminders` never reaches them. Measured at 1,546 / 8,518 /
# 523 chars.
#
#   agent types  removed unconditionally — the Agent tool's subagent menu is
#                dead weight for a local model driving one CLI.
#   skills       toggle. Strip it and the model can no longer pick a skill by
#                name.
#   mcp          toggle. This one carries real operating instructions the MCP
#                servers themselves supplied, so it is off by default.
#
# `mid_system_messages: "strip"` is the whole-message lever that subsumes all
# three; these are the surgical version, and unlike `strip` they leave
# anything unrecognised alone.
#
# Scoped to messages AFTER the leading system message. With the default
# `mid_system_messages: "demote"` these arrive re-roled to `user` by the time
# we see them, so position — not role — is what identifies them.

_AGENT_TYPES_RE = re.compile(r"^Available agent types for the Agent tool:", re.M)
_SKILLS_LISTING_RE = re.compile(r"^The following skills are available", re.M)
_MCP_INSTRUCTIONS_RE = re.compile(
    r"^(?:# MCP Server Instructions$"
    r"|The following MCP servers are configured but failed to connect)", re.M)

# A block runs to the next of these. Generous on purpose: over-shooting a
# boundary would delete a sibling block, which is the failure mode worth
# engineering against.
_TURN_BOUNDARY_RE = re.compile(
    r"^Available agent types for the Agent tool:"
    r"|^The following skills are available"
    r"|^The following MCP servers are configured but failed to connect"
    r"|^# MCP Server Instructions$"
    r"|^The following deferred tools are now available"
    r"|^Unloaded tools \(call ToolSearch"
    r"|^While bypass permissions mode is active:"
    r"|</system-reminder>",
    re.M,
)


def _cut_to_boundary(text: str, start_re: "re.Pattern[str]") -> str:
    out = text
    for _ in range(8):
        m = start_re.search(out)
        if not m:
            break
        nxt = _TURN_BOUNDARY_RE.search(out, m.end())
        end = nxt.start() if nxt else len(out)
        head, tail = out[:m.start()], out[end:]
        if head.strip() and tail.strip():
            out = head.rstrip() + "\n\n" + tail.lstrip()
        else:
            out = (head + tail).strip()
    return out


def strip_turn_context(text: str, *, skills: bool = False,
                       mcp: bool = False) -> str:
    """Remove the agent-type roster (always) plus any toggled-on listings."""
    text = _cut_to_boundary(text, _AGENT_TYPES_RE)
    if skills:
        text = _cut_to_boundary(text, _SKILLS_LISTING_RE)
    if mcp:
        text = _cut_to_boundary(text, _MCP_INSTRUCTIONS_RE)
    return text.strip()


# ── CLAUDE.md document limiter ─────────────────────────────────────────────
#
# `# claudeMd` is not one document. It is every CLAUDE.md on the path
# concatenated — managed policy, then `~/.claude/CLAUDE.md`, then the project
# file, then nested ones, then MEMORY.md — each headed by a
# `Contents of <path> (<why>):` line, in that load order. Measured on a bare
# `say hi` in this repo: 7,182 + 33,102 + 733 chars.
#
# It rides INSIDE the <system-reminder> on the first user turn (Claude Code
# delivers CLAUDE.md as a user message, not as part of the system prompt), so
# `strip_reminders` takes the whole thing with it. `keep_claude_md` is the
# exclusion: keep the first N documents and drop the rest, whether or not
# reminders are being stripped.
#
#   -1  leave it alone (default)
#    0  drop the block outright
#    N  keep the first N documents

_CLAUDE_MD_START_RE = re.compile(r"^# claudeMd$", re.M)
_DOC_HEADER_RE = re.compile(r"^Contents of .+:$", re.M)

# What ends the `# claudeMd` section. Claude Code's sibling context headers are
# a single camelCase token (`# currentDate`, `# userEmail`, `# gitStatus`);
# headings inside a user's own CLAUDE.md do not collide because they either
# contain a space (`# Memory Index`) or start with a capital.
_CTX_BOUNDARY_RE = re.compile(
    r"^# [a-z][A-Za-z0-9]*$"
    r"|^[ \t]*IMPORTANT: this context may or may not be relevant"
    r"|</system-reminder>",
    re.M,
)


def _claude_md_span(text: str) -> tuple[int, int] | None:
    m = _CLAUDE_MD_START_RE.search(text)
    if not m:
        return None
    nxt = _CTX_BOUNDARY_RE.search(text, m.end())
    return m.start(), (nxt.start() if nxt else len(text))


def _claude_md_trimmed(section: str, keep: int) -> str:
    """`section` cut down to its first `keep` `Contents of …:` documents."""
    docs = list(_DOC_HEADER_RE.finditer(section))
    if len(docs) <= keep:
        return section
    return section[:docs[keep].start()]


def extract_claude_md(text: str, keep: int) -> str:
    """The first `keep` CLAUDE.md documents, re-wrapped as a reminder.

    Used to carry the block through `strip_reminders`, which would otherwise
    delete the wrapper it lives in.
    """
    if keep <= 0:
        return ""
    span = _claude_md_span(text)
    if not span:
        return ""
    section = _claude_md_trimmed(text[span[0]:span[1]], keep).rstrip()
    if not section:
        return ""
    return f"<system-reminder>\n{section}\n</system-reminder>"


def limit_claude_md_text(text: str, keep: int) -> str:
    """Trim the block in place — for when reminders are NOT being stripped.

    The surviving head stays byte-identical, so llama.cpp's prefix cache only
    has to refill from the cut.
    """
    if keep < 0:
        return text
    span = _claude_md_span(text)
    if not span:
        return text
    start, end = span
    if keep == 0:
        cut_at = start
    else:
        docs = list(_DOC_HEADER_RE.finditer(text[start:end]))
        if len(docs) <= keep:
            return text
        cut_at = start + docs[keep].start()
    head, tail = text[:cut_at], text[end:]
    if head.strip() and tail.strip():
        return head.rstrip() + "\n\n" + tail.lstrip()
    return (head + tail).strip()


def _strip_reminders_except_preserved(text: str, keep_claude_md: int = -1) -> str:
    """Strip client bookkeeping from `text`.

    Removed: every <system-reminder> block, and every <total_tokens> budget
    line. Kept: the first `keep_claude_md` CLAUDE.md documents, skills
    listings and our deferred-tools listing — re-appended at the end in a
    fixed order, so the result stays byte-identical turn over turn.
    (Proxy-authored context that must survive is emitted as plain text rather
    than added here — see server.py::_inject_system_prompt.)
    """
    # Extract blocks we want to keep. CLAUDE.md goes first: it is the
    # highest-priority instruction in the request, and a fixed order is what
    # keeps the result byte-stable.
    preserved: list[str] = []
    kept_md = extract_claude_md(text, keep_claude_md)
    if kept_md:
        preserved.append(kept_md)
    preserved += (_SKILLS_REMINDER_RE.findall(text)
                  + _DEFERRED_KEEP_RE.findall(text))
    # Strip all reminders + per-turn token-budget noise
    text = _ALL_REMINDERS_RE.sub("", text)
    text = _TOKEN_BUDGET_RE.sub("", text)
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


def strip_all_reminders(messages: list[dict[str, Any]],
                        keep_claude_md: int = -1) -> list[dict[str, Any]]:
    """Strip system-reminder blocks, preserving skills + `keep_claude_md` docs."""
    return _apply_to_messages(
        messages,
        lambda t: _strip_reminders_except_preserved(t, keep_claude_md).strip(),
    )


def limit_claude_md(messages: list[dict[str, Any]],
                    keep: int) -> list[dict[str, Any]]:
    """Trim the CLAUDE.md block in place, reminders left intact.

    The other half of `keep_claude_md`: used when `strip_reminders` is off, so
    the dial still limits how many documents get through.
    """
    if keep < 0:
        return messages
    return _apply_to_messages(
        messages, lambda t: limit_claude_md_text(t, keep).strip()
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
