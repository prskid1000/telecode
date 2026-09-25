"""Line-based three-way merge — the fallback for a file git could not merge.

Staging write-back merges a run's changes to an agent's internal files with
``git merge`` of a per-run branch (see :mod:`services.memory.repo`). Only the
files git reports as conflicted come here, where two cases git calls a
conflict are resolved: both sides only inserted at the same point (two runs
appending to MEMORY.md — both kept, the concurrent write first), and a last
line without a trailing newline. A real overlap keeps both sides between
``<<<<<<< this run`` / ``>>>>>>> concurrent write`` markers.
"""

from __future__ import annotations

import difflib
from typing import List, Tuple

CONFLICT_OURS = "<<<<<<< this run"
CONFLICT_SEP = "======="
CONFLICT_THEIRS = ">>>>>>> concurrent write"


def _hunks(base: List[str], other: List[str], side: str) -> List[Tuple[int, int, List[str], str]]:
    sm = difflib.SequenceMatcher(None, base, other, autojunk=False)
    return [(i1, i2, other[j1:j2], side)
            for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]


def _apply(base: List[str], lo: int, hi: int, hunks) -> List[str]:
    out: List[str] = []
    pos = lo
    for i1, i2, lines, _ in sorted(hunks, key=lambda h: (h[0], h[1])):
        out += base[pos:i1] + lines
        pos = i2
    return out + base[pos:hi]


def _nl(lines: List[str]) -> List[str]:
    if lines and not lines[-1].endswith("\n"):
        return lines[:-1] + [lines[-1] + "\n"]
    return lines


def merge3(base: str, ours: str, theirs: str) -> Tuple[str, bool]:
    """Line-based three-way merge. Returns (text, had_conflict)."""
    if ours == theirs or theirs == base:
        return ours, False
    if ours == base:
        return theirs, False
    # A last line without "\n" differs from the same line with one, which
    # would turn two appends to a newline-less file into a conflict on the
    # last line. Merge on newline-terminated text; drop the added final
    # newline again when neither side had one.
    had_nl = ours.endswith("\n") or theirs.endswith("\n")
    base, ours, theirs = (t if (not t or t.endswith("\n")) else t + "\n" for t in (base, ours, theirs))
    text, conflict = _merge3_lines(base, ours, theirs)
    if not had_nl and text.endswith("\n") and not conflict:
        text = text[:-1]
    return text, conflict


def _merge3_lines(base: str, ours: str, theirs: str) -> Tuple[str, bool]:
    if ours == theirs or theirs == base:
        return ours, False
    if ours == base:
        return theirs, False
    b = base.splitlines(keepends=True)
    hunks = sorted(_hunks(b, ours.splitlines(keepends=True), "ours")
                   + _hunks(b, theirs.splitlines(keepends=True), "theirs"),
                   key=lambda h: (h[0], h[1]))
    out: List[str] = []
    pos = 0
    conflict = False
    i = 0
    while i < len(hunks):
        group = [hunks[i]]
        lo, hi = hunks[i][0], hunks[i][1]
        i += 1
        while i < len(hunks) and hunks[i][0] <= hi:
            group.append(hunks[i])
            hi = max(hi, hunks[i][1])
            i += 1
        out += b[pos:lo]
        pos = hi
        mine = [h for h in group if h[3] == "ours"]
        other = [h for h in group if h[3] == "theirs"]
        if not mine or not other:
            out += _apply(b, lo, hi, group)
            continue
        o_text = _apply(b, lo, hi, mine)
        t_text = _apply(b, lo, hi, other)
        if o_text == t_text:
            out += o_text
        elif lo == hi and all(h[0] == h[1] for h in group):
            # Both sides only inserted at the same point (typically two runs
            # appending to MEMORY.md): keep both, concurrent write first.
            out += _nl(t_text) + o_text
        else:
            conflict = True
            out += ([CONFLICT_OURS + "\n"] + _nl(o_text) + [CONFLICT_SEP + "\n"]
                    + _nl(t_text) + [CONFLICT_THEIRS + "\n"])
    out += b[pos:]
    return "".join(out), conflict
