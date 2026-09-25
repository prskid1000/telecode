"""Truly parallel forked map workers (deferred-P3).

A ``worker_session: fork`` map worker continues the planner's CLI conversation
(so it knows everything the planner learned) but works in **its own copy of the
planner's workspace**, so N workers run at once instead of queueing on the
planner's staging lock. Two things make that work:

Workspace copy — :func:`populate_workspace`
  A fast file copy of the planner's workspace (``.git``, ``node_modules``,
  virtualenvs, caches, ``.telecode/`` and the session store's own files are
  skipped) — the state the planner left, including files its ``.gitignore``
  hides such as ``.env``. When the workspace has been snapshotted *since* the
  planner's after-snapshot (it moved on — e.g. a map retried later), or the
  planner ran in a throwaway copy that is gone, the planner's post-step
  snapshot is exported instead (``services.snapshots.export``), which is exact
  but lacks ignored / oversized files.

Conversation fork — :func:`prepare_fork`
  * **Claude Code** keeps sessions per project folder:
    ``<CLAUDE_CONFIG_DIR or ~/.claude>/projects/<encoded cwd>/<session>.jsonl``,
    the encoding replacing every non-alphanumeric character of the absolute cwd
    with ``-`` (``C:\\Users\\me\\.x`` → ``C--Users-me--x``), and ``--resume``
    only finds a session in the folder of the cwd it runs in. So the planner's
    ``<id>.jsonl`` (and its ``<id>/`` side folder, when present) is copied into
    the worker folder's project dir; ``--resume <id> --fork-session`` then works
    there and writes the fork under a new id. The copy is removed after the
    worker (:func:`cleanup_fork`); Claude's own fork file stays.
    Encoded names over 200 characters are hashed by Claude itself — those fall
    back (below) rather than guess the hash.
  * **Codex** stores rollouts globally (``~/.codex/sessions/YYYY/MM/DD/``) and
    ``codex exec fork <id>`` looks them up by id whatever the cwd (``--last`` is
    the only cwd-filtered lookup), so nothing is staged; ``-C`` = the worker dir.
  * **Antigravity** has no fork.

When a fork cannot work for an engine (agy, no planner conversation, the Claude
session file not found, a path too long) that worker alone falls back to a
fresh conversation seeded with the planner's handoff (it is always in the
worker prompt) and the reason is logged and recorded on the worker
(``fork_fallback``). A forked worker whose CLI then reports that the session
does not exist is re-run once the same way.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from services import snapshots

logger = logging.getLogger("telecode.services.run.fork_workspace")

COPY_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", ".mypy_cache", ".pytest_cache",
                  ".ruff_cache", ".tox", ".telecode"}
COPY_SKIP_FILES = {"session.json"}
CLAUDE_MAX_DIRNAME = 200

# What a CLI prints when --resume / fork names a conversation it cannot find.
MISSING_SESSION_RE = re.compile(
    r"no conversation found|conversation[^\n]{0,80}not found|session[^\n]{0,80}(not found|does not exist)|"
    r"no (such )?(session|thread|rollout)[^\n]{0,40}found|thread[^\n]{0,60}not found", re.I)


# ── workspace ───────────────────────────────────────────────────────────────

def fast_copy(src: Path, dst: Path) -> int:
    """Copy every regular file of ``src`` into ``dst`` except the skipped
    folders / files. Returns files copied."""
    src, dst = Path(src), Path(dst)
    n = 0
    if not src.is_dir():
        return 0
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in COPY_SKIP_DIRS and not (Path(dirpath) / d).is_symlink()]
        rel_dir = Path(dirpath).relative_to(src)
        for fn in filenames:
            if fn in COPY_SKIP_FILES or fn.startswith(".session."):
                continue
            s = Path(dirpath) / fn
            if s.is_symlink():
                continue
            d = dst / rel_dir / fn
            try:
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)
                n += 1
            except OSError:
                continue
    return n


def populate_workspace(dst: Path, *, src_dir: Optional[Path], snapshot_key: Optional[str],
                       snapshot_after: Optional[str]) -> Dict[str, Any]:
    """Fill a worker's (empty) folder with the planner's post-step workspace.
    Returns {"from": "copy" | "snapshot" | "none", "files": n, "sha"?}."""
    have_snap = bool(snapshot_key and snapshot_after and snapshots.exists(snapshot_key, snapshot_after))
    moved = False
    if have_snap:
        try:
            newest = snapshots.latest(snapshot_key)
            moved = bool(newest) and not newest.startswith(snapshot_after) and not snapshot_after.startswith(newest)
        except Exception:
            moved = False
    src_ok = bool(src_dir) and Path(src_dir).is_dir()
    if have_snap and (moved or not src_ok):
        try:
            n = snapshots.export(snapshot_key, snapshot_after, dst)
            return {"from": "snapshot", "files": n, "sha": snapshot_after}
        except Exception as exc:
            logger.warning(f"worker workspace: snapshot export {snapshot_after[:8]} failed ({exc}) — copying")
    if src_ok:
        return {"from": "copy", "files": fast_copy(Path(src_dir), dst)}
    return {"from": "none", "files": 0}


# ── Claude session files ────────────────────────────────────────────────────

def claude_projects_root() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "projects"


def claude_project_dirname(cwd: Path) -> str:
    """Claude Code's folder name for a cwd: every non-alphanumeric → ``-``."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(cwd).absolute()))


def _candidate_dirnames(cwd: Path) -> List[str]:
    names = [claude_project_dirname(cwd)]
    try:
        real = re.sub(r"[^A-Za-z0-9]", "-", str(Path(cwd).resolve()))
        if real not in names:
            names.append(real)
    except OSError:
        pass
    return names


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def find_claude_session(session_id: str, hint_cwd: Optional[Path] = None) -> Optional[Path]:
    """The ``<id>.jsonl`` of a Claude conversation: the hint cwd's project dir
    first, else any project dir."""
    if not session_id or not _SESSION_ID_RE.match(session_id):
        return None
    root = claude_projects_root()
    if hint_cwd is not None:
        for name in _candidate_dirnames(hint_cwd):
            p = root / name / f"{session_id}.jsonl"
            if p.is_file():
                return p
    try:
        for p in root.glob(f"*/{session_id}.jsonl"):
            if p.is_file():
                return p
    except OSError:
        pass
    return None


def stage_claude_fork(session_id: str, src_cwd: Optional[Path], dst_cwd: Path) -> Dict[str, Any]:
    """Copy the planner's conversation into the worker folder's project dir.
    Returns {"ok": bool, "reason"?: str, "staged": [paths we created]}."""
    names = _candidate_dirnames(dst_cwd)
    if any(len(n) > CLAUDE_MAX_DIRNAME for n in names):
        return {"ok": False, "reason": f"worker path too long for Claude's project folder name "
                                       f"({max(len(n) for n in names)} > {CLAUDE_MAX_DIRNAME} chars)",
                "staged": []}
    src = find_claude_session(session_id, src_cwd)
    if src is None:
        return {"ok": False, "reason": f"Claude session file {session_id}.jsonl not found under "
                                       f"{claude_projects_root()}", "staged": []}
    staged: List[str] = []
    root = claude_projects_root()
    try:
        for name in names:
            d = root / name
            d.mkdir(parents=True, exist_ok=True)
            tgt = d / src.name
            if tgt.resolve() != src.resolve() and not tgt.exists():
                shutil.copy2(src, tgt)
                staged.append(str(tgt))
            side = src.with_suffix("")                      # <id>/ (subagents, tool results), when present
            if side.is_dir() and not (d / side.name).exists():
                shutil.copytree(side, d / side.name)
                staged.append(str(d / side.name))
    except OSError as exc:
        cleanup_fork({"staged": staged})
        return {"ok": False, "reason": f"copying the Claude session failed: {exc}", "staged": []}
    return {"ok": True, "staged": staged, "source": str(src)}


def cleanup_fork(info: Optional[Dict[str, Any]]) -> None:
    """Remove what :func:`stage_claude_fork` copied (Claude's fork file stays)."""
    for p in reversed((info or {}).get("staged") or []):
        try:
            path = Path(p)
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def prepare_fork(engine: str, session_id: Optional[str], *, src_cwd: Optional[Path],
                 dst_cwd: Path) -> Dict[str, Any]:
    """Can a worker in ``dst_cwd`` fork conversation ``session_id``? Stages
    what the engine needs. Returns {"ok": bool, "reason"?, "staged": [...]}."""
    if not session_id:
        return {"ok": False, "reason": "the planner has no CLI conversation to fork", "staged": []}
    if engine == "claude_code":
        return stage_claude_fork(session_id, src_cwd, dst_cwd)
    if engine == "codex":
        return {"ok": True, "staged": [], "note": "codex rollouts are global; exec fork is not cwd-bound"}
    return {"ok": False, "reason": f"{engine} has no conversation fork", "staged": []}


def missing_session(error: Optional[str], events: Optional[List[Dict[str, Any]]] = None) -> bool:
    texts = [error or ""]
    for e in (events or [])[-20:]:
        texts.append(str(e.get("message") or e.get("text") or e.get("error") or ""))
    return bool(MISSING_SESSION_RE.search("\n".join(texts)))
