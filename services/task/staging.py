"""Per-run staging of agent internal files into a workspace.

Stages SOUL.md / USER.md / MEMORY.md verbatim, plus AGENT.md renamed to
CLAUDE.md or GEMINI.md depending on the engine. On exit, diffs each staged
file against its initial snapshot; any change is written back to the agent's
internal storage. Then the staged files are deleted from the workspace.

HEARTBEAT.md is intentionally NOT staged — it is read by the heartbeat
scheduler directly from agent storage and never touches the workspace.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional

from services.agent.agent_manager import get_agent_manager

logger = logging.getLogger("telecode.services.task.staging")

# Files copied verbatim (workspace name == storage name)
PASSTHROUGH_FILES = ("SOUL.md", "USER.md", "MEMORY.md")

# Engine → workspace-side filename for the agent's AGENT.md
AGENT_BRIDGE = {
    "claude": "CLAUDE.md",
    "gemini": "GEMINI.md",
}

# Per-workspace lock so two concurrent runs don't clobber each other's staged files.
_workspace_locks_guard = threading.Lock()
_workspace_locks: Dict[str, threading.Lock] = {}


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


def _staged_filenames(engine: str) -> tuple:
    """The full set of filenames written into the workspace for a run."""
    return PASSTHROUGH_FILES + (_bridge_filename(engine),)


def _stage(agent_id: str, work_dir: Path, engine: str) -> Dict[str, str]:
    """Copy agent internal files into work_dir; return snapshot of staged contents."""
    mgr = get_agent_manager()
    internal = mgr.get_internal_files(agent_id)  # dict, includes all 5 internal names
    bridge = _bridge_filename(engine)
    snapshot: Dict[str, str] = {}
    work_dir.mkdir(parents=True, exist_ok=True)
    for fname in PASSTHROUGH_FILES:
        content = internal.get(fname, "") or ""
        (work_dir / fname).write_text(content, encoding="utf-8")
        snapshot[fname] = content
    agent_md = internal.get("AGENT.md", "") or ""
    (work_dir / bridge).write_text(agent_md, encoding="utf-8")
    snapshot[bridge] = agent_md
    logger.info(f"Staged agent {agent_id} into {work_dir} (engine={engine}, files={list(snapshot)})")
    return snapshot


def _writeback_and_unstage(
    agent_id: str,
    work_dir: Path,
    engine: str,
    snapshot: Dict[str, str],
) -> None:
    """Diff staged files against snapshot, write changes back to agent storage, then delete."""
    mgr = get_agent_manager()
    bridge = _bridge_filename(engine)

    changes: Dict[str, str] = {}
    for fname in PASSTHROUGH_FILES:
        p = work_dir / fname
        if not p.exists():
            continue
        try:
            current = p.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning(f"Could not read {p} during writeback: {exc}")
            continue
        if current != snapshot.get(fname):
            changes[fname] = current

    bridge_path = work_dir / bridge
    if bridge_path.exists():
        try:
            current_bridge = bridge_path.read_text(encoding="utf-8")
            if current_bridge != snapshot.get(bridge):
                # Bridge file maps back to AGENT.md in agent storage
                changes["AGENT.md"] = current_bridge
        except Exception as exc:
            logger.warning(f"Could not read {bridge_path} during writeback: {exc}")

    if changes:
        try:
            mgr.set_internal_files(agent_id, changes)
            logger.info(f"Wrote back {list(changes)} for agent {agent_id}")
        except Exception as exc:
            logger.error(f"Writeback failed for agent {agent_id}: {exc}")

    # Always unstage — delete every file we wrote in _stage, by name. Never delete
    # anything else.
    for fname in _staged_filenames(engine):
        p = work_dir / fname
        try:
            if p.exists():
                p.unlink()
        except Exception as exc:
            logger.warning(f"Could not unstage {p}: {exc}")


@contextmanager
def stage_for_run(
    agent_id: Optional[str],
    workspace_id: str,
    work_dir: Path,
    engine: str,
) -> Iterator[Dict[str, str]]:
    """Acquire workspace lock, stage agent files, yield snapshot. On exit: writeback + unstage.

    If agent_id is falsy, yields an empty snapshot and does no staging — useful
    for legacy code paths or tasks not bound to an agent.
    """
    if not agent_id:
        yield {}
        return

    lock = _get_workspace_lock(workspace_id)
    lock.acquire()
    snapshot: Dict[str, str] = {}
    try:
        snapshot = _stage(agent_id, work_dir, engine)
        yield snapshot
    finally:
        try:
            _writeback_and_unstage(agent_id, work_dir, engine, snapshot)
        finally:
            lock.release()
