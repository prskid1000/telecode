"""Per-run staging of agent internal files into a workspace.

Stages SOUL.md / USER.md / MEMORY.md (the memory **index** — topic files stay
in the agent's ``internal/memory/`` and are reached through ``--add-dir``, see
:func:`services.memory.engine_extras`), plus the agent's AGENT.md — either
into the workspace under the engine's bridge name (AGENTS.md for Codex /
Antigravity, CLAUDE.md for the legacy Claude path) or, when the caller passes
``agent_md_file``, into that file outside the workspace (the Claude handler
hands it to ``claude --append-system-prompt-file``, so the workspace's own
CLAUDE.md is never touched). The agent's portable skills
(``internal/skills/<name>/``) are staged into ``.agents/skills/`` and
``.claude/skills/`` (:mod:`services.skills.agent_skills`).

The workspace's own files are never destroyed (B8): any pre-existing file
with a staged name is backed up to ``data/staging_backups/<key>/`` before
staging and restored on exit; a skill folder the workspace already has is not
staged over. The backup manifest is on disk, so a crash mid-run is repaired
by the next stage of the same workspace.

Write-back (P4) is a git commit in the agent's internal repo
(:mod:`services.memory.repo`): ``run:<run_id> step:<step_id>`` (or
``task:<task_id>``) built on the commit staged from, then fast-forwarded — or,
when another run / an edit committed meanwhile, merged as a per-run branch.
Files git cannot merge go through :func:`merge3` (same-point appends keep both
sides; a true overlap keeps both between conflict markers and is logged).
Files an engine wrote straight into the memory dir during the run (Claude's
auto memory, topic files via ``--add-dir``) are committed with it.

A memory-reflection fire (:mod:`services.memory.reflection`) is staged with its
input digest and never writes back — its proposal goes through an approval.

HEARTBEAT.md is intentionally NOT staged — the trigger scheduler reads it
directly from agent storage and it never touches the workspace.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from services.agent.agent_manager import get_agent_manager, internal_rel
from services.memory.merge import CONFLICT_OURS, CONFLICT_SEP, CONFLICT_THEIRS, merge3  # noqa: F401 (re-exported)

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
# First line of a staged MEMORY.md when the agent has topic files: where they are.
_TOPICS_HEADER_RE = re.compile(r"\A<!-- memory topic files: .* -->\n")


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


# ── Backups of the workspace's own files ────────────────────────────────────

def _backup_dir(work_dir: Path) -> Path:
    import config
    key = hashlib.sha1(str(work_dir.resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(config._settings_dir()) / "data" / "staging_backups" / key


def _read_manifest(bdir: Path) -> Optional[Dict[str, Any]]:
    p = bdir / _MANIFEST
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error(f"Unreadable staging manifest {p}: {exc}")
        return None


def _write_manifest(bdir: Path, manifest: Dict[str, Any]) -> None:
    (bdir / _MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")


def _restore_backups(work_dir: Path, bdir: Path) -> None:
    """Put the workspace's own files back; remove files (and staged skill
    folders / extras) that did not exist."""
    manifest = _read_manifest(bdir)
    if manifest is None:
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
    for rel in manifest.get("extras") or ():
        try:
            (work_dir / rel).unlink(missing_ok=True)
        except Exception:
            pass
    from services.skills import agent_skills
    agent_skills.unstage(work_dir, manifest.get("skills"))
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
    _write_manifest(bdir, {"work_dir": str(work_dir), "files": files})


def _manifest_add(work_dir: Path, key: str, value: Any) -> None:
    bdir = _backup_dir(work_dir)
    m = _read_manifest(bdir)
    if m is None:
        return
    m[key] = value
    _write_manifest(bdir, m)


# ── Task context ────────────────────────────────────────────────────────────

def _task_info() -> Dict[str, Any]:
    """{task_id, run_id, step_id, trigger_id, engine} of the task running this stage."""
    try:
        from services.task.task_manager import get_task_queue
        from services.task.task_utils import get_task_id
        tid = get_task_id()
        t = get_task_queue().get_task(tid) if tid else None
        md = (t.metadata if t else {}) or {}
    except Exception:
        tid, md = None, {}
    return {"task_id": tid, "run_id": md.get("run_id"), "step_id": md.get("step_id"),
            "trigger_id": md.get("trigger_id"), "engine": md.get("engine")}


def _label(info: Dict[str, Any]) -> tuple:
    if info.get("run_id"):
        return (f"run:{info['run_id']} step:{info.get('step_id') or '-'}",
                {"kind": "run", "run": info["run_id"], "step": info.get("step_id"), "task": info.get("task_id"),
                 "engine": info.get("engine")})
    if info.get("task_id"):
        return (f"task:{info['task_id']}", {"kind": "task", "task": info["task_id"],
                                            "trigger": info.get("trigger_id"), "engine": info.get("engine")})
    return "writeback: staged outside a task", {"kind": "task"}


# ── Stage / write back ──────────────────────────────────────────────────────

def _topics_header(agent_id: str) -> str:
    from services.memory import store
    mdir = store.memory_dir(agent_id)
    if any(p.name != store.INDEX for p in mdir.glob("*.md")):
        return (f"<!-- memory topic files: {mdir} — the index below links to them; read one when it is "
                f"relevant, edit or add topic files there -->\n")
    return ""


def _stage(agent_id: str, work_dir: Path, engine: str,
           agent_md_file: Optional[Path] = None) -> Dict[str, str]:
    """Copy agent internal files into work_dir; return snapshot of staged contents."""
    mgr = get_agent_manager()
    internal = mgr.get_internal_files(agent_id)  # dict, includes all 5 internal names (MEMORY.md = index)
    snapshot: Dict[str, str] = {}
    work_dir.mkdir(parents=True, exist_ok=True)
    for fname in PASSTHROUGH_FILES:
        content = internal.get(fname, "") or ""
        staged = (_topics_header(agent_id) + content) if fname == "MEMORY.md" else content
        (work_dir / fname).write_text(staged, encoding="utf-8")
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


def _changed(work_dir: Path, engine: str, snapshot: Dict[str, str],
             agent_md_file: Optional[Path]) -> Dict[str, str]:
    sources = {fname: work_dir / fname for fname in PASSTHROUGH_FILES}
    sources["AGENT.md"] = agent_md_file if agent_md_file is not None else work_dir / _bridge_filename(engine)
    changed: Dict[str, str] = {}
    for fname, path in sources.items():
        if fname not in snapshot:
            continue
        current = _read(path)
        if current is not None and fname == "MEMORY.md":
            current = _TOPICS_HEADER_RE.sub("", current, count=1)
        if current is not None and current != snapshot[fname]:
            changed[fname] = current
    return changed


def _writeback(agent_id: str, work_dir: Path, engine: str, snapshot: Dict[str, str],
               agent_md_file: Optional[Path] = None, *, base: Optional[str] = None,
               info: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Commit the run's changes to the agent's internal repo (a fast-forward,
    or a merge of the run's branch when something else committed meanwhile).
    Returns {fname: new stored text} for the staged files that changed."""
    from services.memory import repo, store
    mgr = get_agent_manager()
    changed = _changed(work_dir, engine, snapshot, agent_md_file)
    d = store.internal_dir(agent_id)
    label, trailers = _label(info or _task_info())
    use_git = repo.available() and repo.is_repo(d)
    if not use_git:
        return _writeback_plain(agent_id, snapshot, changed)
    try:
        with repo.lock_for(d):
            if not changed:
                # Direct writes into the memory dir (auto memory, topic files) still get their commit.
                repo.commit_all(d, repo.message(f"{label} (memory files)", **trailers))
                return {}
            base = base or repo.head(d)
            files = {internal_rel(f): text for f, text in changed.items()}
            if repo.head(d) == base:
                # Nothing else committed since staging: one commit on main carrying the staged
                # files and whatever the engine wrote straight into the memory dir.
                for rel, text in files.items():
                    repo.write_text(d / rel, text)
                res = {"head": repo.commit_all(d, repo.message(label, **trailers)), "fast_forward": True,
                       "conflicts": [], "resolved": []}
            else:
                sha = repo.commit_tree_on(d, base, files, repo.message(label, **trailers))
                res = repo.integrate(d, base, sha, label,
                                     direct_msg=repo.message(f"{label} (memory files)", **trailers),
                                     trailers=trailers)
        for p in res["conflicts"]:
            logger.warning(f"Write-back conflict in {p} for agent {agent_id}: another run changed it "
                           f"concurrently; kept both versions between conflict markers")
        if res["resolved"]:
            logger.info(f"Write-back for agent {agent_id} merged concurrent edits in {res['resolved']}")
        stored = mgr.get_internal_files(agent_id)
        logger.info(f"Wrote back {list(changed)} for agent {agent_id} ({label}, "
                    f"{'fast-forward' if res['fast_forward'] else 'merge'} → {str(res['head'])[:8]})")
        return {f: stored.get(f, "") for f in changed}
    except Exception as exc:
        logger.error(f"Writeback failed for agent {agent_id}: {exc}")
        return {}


def _writeback_plain(agent_id: str, snapshot: Dict[str, str], changed: Dict[str, str]) -> Dict[str, str]:
    """No git on PATH: three-way merge each file against what storage holds now."""
    mgr = get_agent_manager()
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
        mgr.set_internal_files(agent_id, merged)
    return merged


def _writeback_and_unstage(
    agent_id: str,
    work_dir: Path,
    engine: str,
    snapshot: Dict[str, str],
    agent_md_file: Optional[Path] = None,
    *,
    base: Optional[str] = None,
    info: Optional[Dict[str, Any]] = None,
    discard: bool = False,
) -> None:
    """Commit changes to the agent's repo, then restore the workspace's own files."""
    try:
        if snapshot and not discard:
            _writeback(agent_id, work_dir, engine, snapshot, agent_md_file, base=base, info=info)
        elif snapshot and discard:
            dropped = _changed(work_dir, engine, snapshot, agent_md_file)
            if dropped:
                logger.warning(f"memory reflection for agent {agent_id} edited {list(dropped)} directly — "
                               f"discarded (reflection proposes changes through an approval)")
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
    """Acquire workspace lock, stage agent files + skills, yield snapshot. On
    exit: commit the write-back + restore the workspace's own files.

    If agent_id is falsy, yields an empty snapshot and does no staging — useful
    for legacy code paths or tasks not bound to an agent.
    """
    if not agent_id:
        yield {}
        return

    from services.memory import reflection, repo, store
    lock = _get_workspace_lock(workspace_id)
    if not lock.acquire(blocking=False):
        try:
            from services.task.task_utils import update_progress
            update_progress(0.02, "waiting for workspace (another run is staged)")
        except Exception:
            pass
        lock.acquire()
    snapshot: Dict[str, str] = {}
    info = _task_info()
    is_reflection = reflection.is_reflection_task(agent_id, info)
    base: Optional[str] = None
    try:
        d = store.ensure(agent_id)
        if repo.available() and repo.is_repo(d):
            with repo.lock_for(d):
                # Anything uncommitted (e.g. auto memory written outside a run) gets its own commit
                # first, so the run's commit only carries the run's changes.
                repo.commit_all(d, repo.message("sync: uncommitted memory changes", kind="direct"))
                base = repo.head(d)
        _backup_existing(work_dir, _staged_filenames(engine, agent_md_file is not None),
                         _backup_dir(work_dir))
        snapshot = _stage(agent_id, work_dir, engine, agent_md_file)
        from services.skills import agent_skills
        _manifest_add(work_dir, "skills", agent_skills.stage(agent_id, work_dir))
        if is_reflection:
            _manifest_add(work_dir, "extras", reflection.stage_input(agent_id, work_dir))
        yield snapshot
    finally:
        try:
            _writeback_and_unstage(agent_id, work_dir, engine, snapshot, agent_md_file,
                                   base=base, info=info, discard=is_reflection)
        finally:
            lock.release()
    if not is_reflection and info.get("task_id"):
        try:
            reflection.note_run(agent_id, info)
        except Exception:
            logger.exception(f"reflection bookkeeping failed for agent {agent_id}")
