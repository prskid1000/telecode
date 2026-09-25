"""Portable skills per agent — procedural memory every engine can read.

An agent's skills live in its internal repo: ``internal/skills/<name>/SKILL.md``
(+ reference files), versioned with the rest of its memory. For a run they are
staged into the workspace as ``.agents/skills/<name>/`` (Codex, Antigravity and
other AGENTS.md-style engines) **and** ``.claude/skills/<name>/`` (Claude Code
project skills), then removed again after the run.

Staging never clobbers: a skill folder the workspace already has under either
root is left alone (the workspace's own skill wins for that run), and only the
folders — and empty parent folders — staging created are removed afterwards.
What was created is recorded in the staging manifest, so a crashed run's
skills are cleaned up by the next stage of the same workspace.

The global skills (``~/.claude/skills``, :mod:`services.skills.skill_store`)
are unchanged; :func:`promote_to_agent` copies a global skill into an agent and
:func:`copy_to_global` copies an agent's skill out to every global root.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.skills import skill_store
from services.skills.skill_store import MAX_FILE_BYTES, SKILL_MD, SKILL_NAME_RE

logger = logging.getLogger("telecode.services.skills.agent")

STAGE_ROOTS = (".agents/skills", ".claude/skills")
SKILLS_DIR = "skills"


class SkillExists(ValueError):
    pass


def skills_dir(agent_id: str) -> Path:
    from services.memory import store
    return store.ensure(agent_id) / SKILLS_DIR


def _skill_dir(agent_id: str, name: str) -> Path:
    skill_store._validate_name(name)
    return skills_dir(agent_id) / name


def _commit(agent_id: str, subject: str) -> Optional[str]:
    from services.memory import repo, store
    d = store.internal_dir(agent_id)
    if not repo.available() or not repo.is_repo(d):
        return None
    return repo.commit_all(d, repo.message(subject, kind="ui"))


def _lock(agent_id: str):
    from services.memory import repo, store
    return repo.lock_for(store.internal_dir(agent_id))


def _mtime(p: Path) -> str:
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def list_skills(agent_id: str) -> List[Dict[str, Any]]:
    root = skills_dir(agent_id)
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not SKILL_NAME_RE.match(d.name):
            continue
        md = d / SKILL_MD
        content = md.read_text(encoding="utf-8") if md.exists() else ""
        out.append({"name": d.name, "description": skill_store._extract_description(content),
                    "has_skill_md": md.exists(), "file_count": sum(1 for p in d.rglob("*") if p.is_file()),
                    "modified_at": _mtime(d)})
    return out


def get_skill(agent_id: str, name: str) -> Optional[Dict[str, Any]]:
    d = _skill_dir(agent_id, name)
    if not d.is_dir():
        return None
    md = d / SKILL_MD
    content = md.read_text(encoding="utf-8") if md.exists() else ""
    return {"name": name, "description": skill_store._extract_description(content), "content": content,
            "files": [skill_store._file_summary(p, d) for p in sorted(d.rglob("*")) if p.is_file()],
            "modified_at": _mtime(d)}


def upsert_skill(agent_id: str, name: str, content: str) -> Dict[str, Any]:
    if len((content or "").encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError(f"SKILL.md too large (limit {MAX_FILE_BYTES} bytes)")
    if not (content or "").strip():
        raise ValueError("content is empty")
    from services.memory import repo
    d = _skill_dir(agent_id, name)
    with _lock(agent_id):
        existed = d.exists()
        repo.write_text(d / SKILL_MD, content)
        _commit(agent_id, f"ui: {'edit' if existed else 'add'} skill {name}")
    return get_skill(agent_id, name) or {}


def delete_skill(agent_id: str, name: str) -> bool:
    d = _skill_dir(agent_id, name)
    if not d.is_dir():
        return False
    with _lock(agent_id):
        shutil.rmtree(d)
        _commit(agent_id, f"ui: delete skill {name}")
    return True


def _file_target(agent_id: str, name: str, rel: str) -> Path:
    skill_store._validate_rel_path(rel)
    d = _skill_dir(agent_id, name)
    target = (d / rel).resolve()
    if not str(target).startswith(str(d.resolve())):
        raise ValueError("path escapes skill folder")
    return target


def read_skill_file(agent_id: str, name: str, rel: str) -> bytes:
    t = _file_target(agent_id, name, rel)
    if not t.is_file():
        raise FileNotFoundError(f"{name}/{rel}")
    return t.read_bytes()


def write_skill_file(agent_id: str, name: str, rel: str, data: bytes) -> Dict[str, Any]:
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"file too large ({len(data)} bytes). Limit is {MAX_FILE_BYTES}.")
    t = _file_target(agent_id, name, rel)
    with _lock(agent_id):
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_bytes(data)
        _commit(agent_id, f"ui: skill {name}: write {rel}")
    return skill_store._file_summary(t, _skill_dir(agent_id, name))


def delete_skill_file(agent_id: str, name: str, rel: str) -> bool:
    t = _file_target(agent_id, name, rel)
    if not t.is_file():
        return False
    with _lock(agent_id):
        t.unlink()
        _commit(agent_id, f"ui: skill {name}: delete {rel}")
    return True


# ── global ↔ agent ─────────────────────────────────────────────────────────

def promote_to_agent(global_name: str, agent_id: str, *, overwrite: bool = False) -> Dict[str, Any]:
    """Copy a global skill (first root that has it) into the agent."""
    src_dirs = skill_store._existing_skill_dirs(global_name)
    if not src_dirs:
        raise FileNotFoundError(f"global skill '{global_name}' not found")
    dst = _skill_dir(agent_id, global_name)
    with _lock(agent_id):
        if dst.exists():
            if not overwrite:
                raise SkillExists(f"agent already has a skill '{global_name}' (pass overwrite)")
            shutil.rmtree(dst)
        shutil.copytree(src_dirs[0][1], dst)
        _commit(agent_id, f"ui: promote global skill {global_name} to this agent")
    return get_skill(agent_id, global_name) or {}


def copy_to_global(agent_id: str, name: str, *, overwrite: bool = False) -> Dict[str, Any]:
    """Copy the agent's skill to every enabled global root."""
    src = _skill_dir(agent_id, name)
    if not src.is_dir():
        raise FileNotFoundError(f"agent skill '{name}' not found")
    if skill_store._existing_skill_dirs(name) and not overwrite:
        raise SkillExists(f"a global skill '{name}' already exists (pass overwrite)")
    for _label, d in skill_store._all_skill_dirs(name):
        if d.exists():
            shutil.rmtree(d)
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, d)
    return skill_store.get_skill(name) or {}


# ── staging into a workspace ───────────────────────────────────────────────

def stage(agent_id: str, work_dir: Path) -> Dict[str, List[str]]:
    """Copy every agent skill into ``.agents/skills`` and ``.claude/skills``.
    Returns ``{"dirs": [created skill dirs], "parents": [created parent dirs],
    "skipped": [dirs the workspace already had]}`` (paths relative to work_dir)."""
    created: Dict[str, List[str]] = {"dirs": [], "parents": [], "skipped": []}
    try:
        skills = [s["name"] for s in list_skills(agent_id)]
    except Exception:
        logger.exception(f"listing skills of agent {agent_id} failed")
        return created
    if not skills:
        return created
    src_root = skills_dir(agent_id)
    for root in STAGE_ROOTS:
        # record parents we are about to create, outermost first
        acc = Path()
        for part in Path(root).parts:
            acc = acc / part
            if not (work_dir / acc).exists():
                created["parents"].append(acc.as_posix())
        for name in skills:
            rel = f"{root}/{name}"
            dst = work_dir / rel
            if dst.exists():
                created["skipped"].append(rel)
                continue
            try:
                shutil.copytree(src_root / name, dst)
                created["dirs"].append(rel)
            except Exception as exc:
                logger.warning(f"could not stage skill {name} into {dst}: {exc}")
    if created["skipped"]:
        logger.info(f"skills already in {work_dir} kept (not staged over): {created['skipped']}")
    return created


def unstage(work_dir: Path, created: Optional[Dict[str, List[str]]]) -> None:
    if not created:
        return
    for rel in created.get("dirs") or ():
        p = work_dir / rel
        try:
            if p.is_dir():
                shutil.rmtree(p)
        except Exception as exc:
            logger.warning(f"could not remove staged skill {p}: {exc}")
    for rel in reversed(created.get("parents") or ()):
        p = work_dir / rel
        try:
            if p.is_dir() and not any(p.iterdir()):
                p.rmdir()
        except Exception:
            pass
