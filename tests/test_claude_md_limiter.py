"""`keep_claude_md` / `keep_memory` — the CLAUDE.md document limiter.

The block Claude Code sends is not one document: `# claudeMd` is every
CLAUDE.md on the path concatenated, each under a `Contents of <path> (<why>):`
header, with the auto-memory index last. These tests pin the split between the
two counts, because getting it wrong silently discards the user's memories
with nothing in the log to say so.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proxy.tool_registry import (  # noqa: E402
    extract_claude_md,
    limit_claude_md_text,
)

ALL, NONE = -1, 0

# Shaped exactly like a real request: heading, intro, three documents in load
# order, then the sibling context header that ends the block. The labels are
# the ones Claude Code actually emits, captured from a live request.
PAYLOAD = """\
<system-reminder>
As you answer the user's questions, you can use the following context:
# claudeMd
Codebase and user instructions are shown below.

Contents of C:\\Users\\prith\\.claude\\CLAUDE.md (user's private global instructions for all projects):

GLOBAL_BODY

Contents of C:\\Users\\prith\\Projects\\cursor\\CLAUDE.md (project instructions, checked into the codebase):

PROJECT_BODY

Contents of C:\\Users\\prith\\.claude\\projects\\C--Users-prith\\memory\\MEMORY.md (user's auto-memory, persists across conversations):

MEMORY_BODY
# userEmail
The user's email address is rahul@datainsights.ae.
</system-reminder>"""

GLOBAL, PROJECT, MEMORY = "GLOBAL_BODY", "PROJECT_BODY", "MEMORY_BODY"

# Rules land between the project CLAUDE.md and the memory index, under the
# SAME label as a project CLAUDE.md — the path is the only thing separating
# them. Both facts measured against a live request, not assumed.
RULES_DOCS = (
    "Contents of C:\\Users\\prith\\Projects\\cursor\\.claude\\rules\\nested\\deep.md "
    "(project instructions, checked into the codebase):\n\nRULE_NESTED\n\n"
    "Contents of C:\\Users\\prith\\Projects\\cursor\\.claude\\rules\\style.md "
    "(project instructions, checked into the codebase):\n\nRULE_STYLE\n\n")
PAYLOAD_RULES = PAYLOAD.replace(
    "Contents of C:\\Users\\prith\\.claude\\projects",
    RULES_DOCS + "Contents of C:\\Users\\prith\\.claude\\projects")
RULE_A, RULE_B = "RULE_NESTED", "RULE_STYLE"
EVERY = (GLOBAL, PROJECT, RULE_A, RULE_B, MEMORY)

# A second auto-memory document. Only the MEMORY.md index loads at session
# start today, so a real request carries one — but the dial is a count, and
# a count that cannot count to two is a switch wearing a costume.
SECOND_MEMORY = (
    "Contents of C:\\Users\\prith\\.claude\\projects\\other\\memory\\MEMORY.md "
    "(user's auto-memory, persists across conversations):\n\nMEMORY_TWO\n\n")
PAYLOAD2 = PAYLOAD.replace("# userEmail", SECOND_MEMORY + "# userEmail")


def _present(text: str, *names: str) -> set[str]:
    return {n for n in (names or (GLOBAL, PROJECT, MEMORY)) if n in text}


# ── keep_claude_md counts only real CLAUDE.md files ────────────────────────

def test_all_kept_by_default():
    assert limit_claude_md_text(PAYLOAD, ALL, ALL) == PAYLOAD


def test_memory_is_not_counted_against_the_claude_md_limit():
    """The regression this split exists for.

    Two CLAUDE.md documents plus a memory index is three documents. Under one
    shared count `keep=2` kept the 13KB project file and dropped the memory
    index; the counts are independent now, so all three survive.
    """
    assert _present(limit_claude_md_text(PAYLOAD, 2, ALL)) == {
        GLOBAL, PROJECT, MEMORY}


def test_count_drops_later_claude_md_files_only():
    assert _present(limit_claude_md_text(PAYLOAD, 1, ALL)) == {GLOBAL, MEMORY}


def test_zero_keeps_memory_alone():
    out = limit_claude_md_text(PAYLOAD, NONE, ALL)
    assert _present(out) == {MEMORY}
    assert "# claudeMd" in out


# ── keep_memory is a count of its own ──────────────────────────────────────

def test_memory_can_be_dropped_while_claude_md_stays():
    assert _present(limit_claude_md_text(PAYLOAD, ALL, NONE)) == {
        GLOBAL, PROJECT}


def test_memory_count_limits_memory_documents_only():
    """Two memory documents, `keep_memory: 1` — the first survives, and the
    CLAUDE.md files are untouched by a limit that isn't theirs."""
    out = limit_claude_md_text(PAYLOAD2, ALL, 1)
    assert _present(out, GLOBAL, PROJECT, MEMORY, "MEMORY_TWO") == {
        GLOBAL, PROJECT, MEMORY}


def test_the_two_counts_are_independent():
    out = limit_claude_md_text(PAYLOAD2, 1, 2)
    assert _present(out, GLOBAL, PROJECT, MEMORY, "MEMORY_TWO") == {
        GLOBAL, MEMORY, "MEMORY_TWO"}


def test_block_disappears_when_both_counts_are_zero():
    out = limit_claude_md_text(PAYLOAD, NONE, NONE)
    assert _present(out) == set()
    # No orphaned heading left behind for the model to puzzle over.
    assert "# claudeMd" not in out


# ── the block's neighbours survive either way ──────────────────────────────

def test_surrounding_context_is_preserved():
    for keep, mem in ((NONE, NONE), (NONE, ALL), (1, ALL), (ALL, NONE)):
        out = limit_claude_md_text(PAYLOAD, keep, mem)
        assert "rahul@datainsights.ae" in out, (keep, mem)
        assert "# userEmail" in out, (keep, mem)


def test_result_is_byte_stable():
    """llama.cpp's prefix cache needs the same bytes turn over turn."""
    once = limit_claude_md_text(PAYLOAD, 1, ALL)
    assert limit_claude_md_text(once, 1, ALL) == once


def test_document_order_is_load_order():
    out = limit_claude_md_text(PAYLOAD, 2, ALL)
    assert out.index(GLOBAL) < out.index(PROJECT) < out.index(MEMORY)


# ── extract_claude_md: the strip_reminders path ────────────────────────────

def test_extract_rewraps_kept_documents():
    out = extract_claude_md(PAYLOAD, 1, ALL)
    assert out.startswith("<system-reminder>")
    assert out.endswith("</system-reminder>")
    assert _present(out) == {GLOBAL, MEMORY}


def test_extract_returns_nothing_when_both_counts_are_zero():
    assert extract_claude_md(PAYLOAD, NONE, NONE) == ""


def test_extract_keeps_memory_with_a_zero_claude_md_count():
    """`keep_claude_md: 0` used to mean "drop the block"; it now means "drop
    the CLAUDE.md files", and memory answers to its own count."""
    assert _present(extract_claude_md(PAYLOAD, NONE, ALL)) == {MEMORY}


def test_no_claude_md_block_is_left_alone():
    plain = "<system-reminder>\nnothing to see\n</system-reminder>"
    assert limit_claude_md_text(plain, NONE, NONE) == plain
    assert extract_claude_md(plain, 2, ALL) == ""


# ── keep_rules: the third kind ─────────────────────────────────────────────

def test_rules_are_not_counted_as_claude_md():
    """A rules file wears the same label as a project CLAUDE.md, so only the
    path separates them. `keep_claude_md: 2` must not eat the rules."""
    out = limit_claude_md_text(PAYLOAD_RULES, 2, ALL, ALL)
    assert _present(out, *EVERY) == set(EVERY)


def test_rules_have_their_own_limit():
    out = limit_claude_md_text(PAYLOAD_RULES, ALL, ALL, 1)
    assert _present(out, *EVERY) == {GLOBAL, PROJECT, RULE_A, MEMORY}


def test_rules_can_be_dropped_without_touching_the_others():
    out = limit_claude_md_text(PAYLOAD_RULES, ALL, ALL, NONE)
    assert _present(out, *EVERY) == {GLOBAL, PROJECT, MEMORY}


def test_the_shared_count_regression():
    """What one dial did before the split: `keep=2` over a project with two
    rules kept the two big CLAUDE.md files and dropped everything else."""
    shared = limit_claude_md_text(PAYLOAD_RULES, 2, 2, 2)
    assert _present(shared, *EVERY) == set(EVERY), (
        "three independent counts of 2 must keep all five documents")


def test_all_three_counts_are_independent():
    out = limit_claude_md_text(PAYLOAD_RULES, 1, NONE, 1)
    assert _present(out, *EVERY) == {GLOBAL, RULE_A}


def test_user_level_rules_are_rules_too():
    """`~/.claude/rules/` and `<project>/.claude/rules/` are the same kind."""
    user_rule = ("Contents of C:\\Users\\prith\\.claude\\rules\\prefs.md "
                 "(user's private global instructions for all projects):"
                 "\n\nUSER_RULE\n\n")
    src = PAYLOAD.replace("Contents of C:\\Users\\prith\\Projects",
                          user_rule + "Contents of C:\\Users\\prith\\Projects")
    out = limit_claude_md_text(src, ALL, ALL, NONE)
    assert "USER_RULE" not in out
    assert _present(out) == {GLOBAL, PROJECT, MEMORY}


def test_rules_block_vanishes_when_all_three_are_zero():
    out = limit_claude_md_text(PAYLOAD_RULES, NONE, NONE, NONE)
    assert _present(out, *EVERY) == set()
    assert "# claudeMd" not in out
