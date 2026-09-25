"""Per-run staging of agent internal files into a workspace.

Stages SOUL.md / USER.md / MEMORY.md verbatim, plus the agent's AGENT.md —
either into the workspace under the engine's bridge name (AGENTS.md for
Codex / Antigravity, CLAUDE.md for the legacy Claude path) or, when the caller
passes ``agent_md_file``, into that file outside the workspace (the Claude
handler hands it to ``claude --append-system-prompt-file``, so the
workspace's own CLAUDE.md is never touched).

The workspace's own files are never destroyed (B8): any pre-existing file
with a staged name is backed up to ``data/staging_backups/<key>/`` before
staging and restored on exit. The backup is on disk, so a crash mid-run is
repaired by the next stage of the same workspace.

Write-back is a three-way merge (base = what was staged, ours = what the run
left, theirs = what agent storage holds now — another run on another
workspace may have written it meanwhile). Non-overlapping edits merge
cleanly, and so do two appends at the same point (theirs then ours); a real
overlap keeps both sides between conflict markers and is logged.

HEARTBEAT.md is intentionally NOT staged — it is read by the heartbeat
scheduler directly from agent storage and never touches the workspace.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from services.agent.agent_manager import get_agent_manager

logger = logging.getLogger("telecode.services.task.staging")

# Files copied verbatim (workspace name == storage name)
PASSTHROUGH_FILES = ("SOUL.md", "USER.md", "MEMORY.md")

# Engine → workspace-side filename for the agent's AGENT.md
# Claude reads CLAUDE.md; Codex / Antigravity both read AGENTS.md by convention.
AGENT_BRIDGE = {
    "claude": "CLAUDE.md",
    "codex": "AGENTS.md",
    "antigravity": "AGENTS.md",
}

# Per-workspace lock so two concurrent runs don't clobber each other's staged files.
_workspace_locks_guard = threading.Lock()
_workspace_locks: Dict[str, threading.Lock] = {}

_MANIFEST = "manifest.json"


def _get_workspace_lock(session_id: str) -> threading.Lock:
    with _workspace_locks_guard:
        lock = _workspace_locks.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _workspace_locks[session_id] = lock
        return lock


def _bridge_filename(engine: str) -> str:
    name = AGENT_BRIDGE.get(engine)
    if not name:
        raise ValueError(f"Unknown engine '{engine}' for staging (expected one of {list(AGENT_BRIDGE)})")
    return name


def _staged_filenames(engine: str, external_agent_md: bool = False) -> tuple:
    """The full set of filenames written into the workspace for a run."""
    if external_agent_md:
        return PASSTHROUGH_FILES
    return PASSTHROUGH_FILES + (_bridge_filename(engine),)


# ── Three-way merge ─────────────────────────────────────────────────────────

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


# ── Backups of the workspace's own files ────────────────────────────────────

def _backup_dir(work_dir: Path) -> Path:
    import config
    key = hashlib.sha1(str(work_dir.resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(config._settings_dir()) / "data" / "staging_backups" / key


def _restore_backups(work_dir: Path, bdir: Path) -> None:
    """Put the workspace's own files back; remove files that did not exist."""
    manifest_path = bdir / _MANIFEST
    if not manifest_path.exists():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error(f"Unreadable staging manifest {manifest_path}: {exc}")
        return
    for fname, had in (manifest.get("files") or {}).items():
        target = work_dir / fname
        try:
            if had:
                shutil.copy2(bdir / fname, target)
            elif target.exists():
                target.unlink()
        except Exception as exc:
            logger.warning(f"Could not restore {target}: {exc}")
            return  # keep the backup dir for the next attempt
    shutil.rmtree(bdir, ignore_errors=True)


def _backup_existing(work_dir: Path, fnames, bdir: Path) -> None:
    if (bdir / _MANIFEST).exists():
        # A previous run crashed between stage and unstage: repair first.
        logger.warning(f"Restoring workspace files left staged by an interrupted run in {work_dir}")
        _restore_backups(work_dir, bdir)
    bdir.mkdir(parents=True, exist_ok=True)
    files: Dict[str, bool] = {}
    for fname in fnames:
        src = work_dir / fname
        if src.is_file():
            shutil.copy2(src, bdir / fname)
            files[fname] = True
        else:
            files[fname] = False
    (bdir / _MANIFEST).write_text(json.dumps({"work_dir": str(work_dir), "files": files}),
                                  encoding="utf-8")


# ── Stage / write back ──────────────────────────────────────────────────────

def _stage(agent_id: str, work_dir: Path, engine: str,
           agent_md_file: Optional[Path] = None) -> Dict[str, str]:
    """Copy agent internal files into work_dir; return snapshot of staged contents."""
    mgr = get_agent_manager()
    internal = mgr.get_internal_files(agent_id)  # dict, includes all 5 internal names
    snapshot: Dict[str, str] = {}
    work_dir.mkdir(parents=True, exist_ok=True)
    for fname in PASSTHROUGH_FILES:
        content = internal.get(fname, "") or ""
        (work_dir / fname).write_text(content, encoding="utf-8")
        snapshot[fname] = content
    agent_md = internal.get("AGENT.md", "") or ""
    if agent_md_file is not None:
        agent_md_file.parent.mkdir(parents=True, exist_ok=True)
        agent_md_file.write_text(agent_md, encoding="utf-8")
    else:
        (work_dir / _bridge_filename(engine)).write_text(agent_md, encoding="utf-8")
    snapshot["AGENT.md"] = agent_md
    logger.info(f"Staged agent {agent_id} into {work_dir} (engine={engine}, "
                f"agent_md={'external' if agent_md_file else _bridge_filename(engine)})")
    return snapshot


def _read(p: Path) -> Optional[str]:
    try:
        return p.read_text(encoding="utf-8") if p.exists() else None
    except Exception as exc:
        logger.warning(f"Could not read {p} during writeback: {exc}")
        return None


def _writeback(agent_id: str, work_dir: Path, engine: str, snapshot: Dict[str, str],
               agent_md_file: Optional[Path] = None) -> Dict[str, str]:
    """Three-way merge every changed staged file into agent storage."""
    mgr = get_agent_manager()
    sources = {fname: work_dir / fname for fname in PASSTHROUGH_FILES}
    sources["AGENT.md"] = agent_md_file if agent_md_file is not None else work_dir / _bridge_filename(engine)

    changed: Dict[str, str] = {}
    for fname, path in sources.items():
        if fname not in snapshot:
            continue
        current = _read(path)
        if current is not None and current != snapshot[fname]:
            changed[fname] = current
    if not changed:
        return {}

    stored = mgr.get_internal_files(agent_id)
    merged: Dict[str, str] = {}
    for fname, ours in changed.items():
        text, conflict = merge3(snapshot[fname], ours, stored.get(fname, "") or "")
        if conflict:
            logger.warning(f"Write-back conflict in {fname} for agent {agent_id}: another run changed it "
                           f"concurrently; kept both versions between conflict markers")
        if text != (stored.get(fname, "") or ""):
            merged[fname] = text
    if merged:
        try:
            mgr.set_internal_files(agent_id, merged)
            logger.info(f"Wrote back {list(merged)} for agent {agent_id}")
        except Exception as exc:
            logger.error(f"Writeback failed for agent {agent_id}: {exc}")
    return merged


def _writeback_and_unstage(
    agent_id: str,
    work_dir: Path,
    engine: str,
    snapshot: Dict[str, str],
    agent_md_file: Optional[Path] = None,
) -> None:
    """Merge changes back to agent storage, then restore the workspace's own files."""
    try:
        if snapshot:
            _writeback(agent_id, work_dir, engine, snapshot, agent_md_file)
    finally:
        bdir = _backup_dir(work_dir)
        if (bdir / _MANIFEST).exists():
            _restore_backups(work_dir, bdir)
        else:
            # No manifest (staging failed before backing up): remove only what we wrote.
            for fname in _staged_filenames(engine, agent_md_file is not None):
                p = work_dir / fname
                try:
                    if p.exists() and fname in snapshot:
                        p.unlink()
                except Exception as exc:
                    logger.warning(f"Could not unstage {p}: {exc}")
        if agent_md_file is not None:
            try:
                agent_md_file.unlink(missing_ok=True)
            except Exception:
                pass


@contextmanager
def stage_for_run(
    agent_id: Optional[str],
    workspace_id: str,
    work_dir: Path,
    engine: str,
    *,
    agent_md_file: Optional[Path] = None,
) -> Iterator[Dict[str, str]]:
    """Acquire workspace lock, stage agent files, yield snapshot. On exit:
    merge-writeback + restore the workspace's own files.

    If agent_id is falsy, yields an empty snapshot and does no staging — useful
    for legacy code paths or tasks not bound to an agent.
    """
    if not agent_id:
        yield {}
        return

    lock = _get_workspace_lock(workspace_id)
    if not lock.acquire(blocking=False):
        try:
            from services.task.task_utils import update_progress
            update_progress(0.02, "waiting for workspace (another run is staged)")
        except Exception:
            pass
        lock.acquire()
    snapshot: Dict[str, str] = {}
    try:
        _backup_existing(work_dir, _staged_filenames(engine, agent_md_file is not None),
                         _backup_dir(work_dir))
        snapshot = _stage(agent_id, work_dir, engine, agent_md_file)
        yield snapshot
    finally:
        try:
            _writeback_and_unstage(agent_id, work_dir, engine, snapshot, agent_md_file)
        finally:
            lock.release()
