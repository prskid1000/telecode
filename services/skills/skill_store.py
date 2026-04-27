"""Filesystem-backed CRUD for Claude Code + Gemini CLI skills.

Skills live as directories under each enabled root (default
``~/.claude/skills/`` and ``~/.gemini/skills/``). Each skill folder has a
``SKILL.md`` describing the skill in the standard skills frontmatter format,
plus optional reference files alongside it.

Both Claude Code (`claude -p`) and Gemini CLI (`gemini -p`) auto-discover
skills from the runtime user's home on every invocation — so writes here
are picked up by the next task without restarting telecode.

This module mirrors writes to **all enabled roots** so the same skill is
available to both CLIs. Reads pick the first root that has the file; lists
union by name.

Layout per root::

    <root>/
        hm-batching/
            SKILL.md
            references/
                spec.md
        rm-poc/
            SKILL.md

API:
- list_skills() → summary records (unioned across roots)
- get_skill(name) → full record incl. SKILL.md text + file list
- upsert_skill(name, content) → write SKILL.md to every root
- delete_skill(name) → remove the skill folder from every root
- read_skill_file(name, rel_path) → bytes
- write_skill_file(name, rel_path, data) → bytes (mirrored)
- delete_skill_file(name, rel_path) (mirrored)
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
SKILL_FILE_PATH_RE = re.compile(r"^(?!\.)(?!.*\.\.)([A-Za-z0-9_./\-]{1,200})$")
SKILL_MD = "SKILL.md"
MAX_FILE_BYTES = 256 * 1024


_ROOT_DEFINITIONS = (
    ("claude", "CLAUDE_SKILLS_DIR", "~/.claude/skills"),
    ("gemini", "GEMINI_SKILLS_DIR", "~/.gemini/skills"),
)


def _enabled_roots() -> List[tuple[str, Path]]:
    """Return enabled (label, path) pairs.

    Per-root override via ``CLAUDE_SKILLS_DIR`` / ``GEMINI_SKILLS_DIR`` env vars.
    Set either to the literal string ``"off"`` or ``""`` to disable that root.
    """
    out: List[tuple[str, Path]] = []
    for label, env_key, default in _ROOT_DEFINITIONS:
        raw = os.getenv(env_key, default)
        if not raw or raw.strip().lower() == "off":
            continue
        out.append((label, Path(os.path.expanduser(raw))))
    if not out:
        raise RuntimeError(
            "No skill roots enabled. Set CLAUDE_SKILLS_DIR or GEMINI_SKILLS_DIR."
        )
    return out


def _ensure_roots() -> List[tuple[str, Path]]:
    roots = _enabled_roots()
    for _, r in roots:
        r.mkdir(parents=True, exist_ok=True)
    return roots


def _validate_name(name: str) -> None:
    if not SKILL_NAME_RE.match(name or ""):
        raise ValueError(
            f"Invalid skill name '{name}'. Must match [a-z0-9][a-z0-9_-]{{0,40}}."
        )


def _validate_rel_path(rel: str) -> None:
    if rel == SKILL_MD:
        raise ValueError(
            "Use upsert_skill / get_skill for SKILL.md (not the file API)."
        )
    if not SKILL_FILE_PATH_RE.match(rel or ""):
        raise ValueError(
            f"Invalid skill file path '{rel}'. Must be a relative POSIX path "
            "without '..' segments."
        )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except Exception:
        logger.exception("Failed to read %s", path)
        return ""


def _extract_description(skill_md: str) -> str:
    """One-line description: YAML frontmatter `description:` first, else first non-heading line."""
    if not skill_md:
        return ""
    if skill_md.startswith("---"):
        end = skill_md.find("---", 3)
        if end != -1:
            for line in skill_md[3:end].splitlines():
                m = re.match(r"^\s*description\s*:\s*(.+?)\s*$", line)
                if m:
                    return m.group(1).strip().strip('"\'')
    for line in skill_md.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("---"):
            continue
        return s[:200]
    return ""


def _file_summary(p: Path, root: Path) -> Dict[str, Any]:
    stat = p.stat()
    return {
        "path": p.relative_to(root).as_posix(),
        "bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _existing_skill_dirs(name: str) -> List[tuple[str, Path]]:
    _validate_name(name)
    return [(label, r / name) for label, r in _enabled_roots() if (r / name).is_dir()]


def _all_skill_dirs(name: str) -> List[tuple[str, Path]]:
    """All enabled roots' (label, folder) for `name` (existing or not)."""
    _validate_name(name)
    return [(label, r / name) for label, r in _enabled_roots()]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_skills() -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for label, root in _ensure_roots():
        if not root.exists():
            continue
        for d in sorted(root.iterdir()):
            if not d.is_dir() or not SKILL_NAME_RE.match(d.name):
                continue
            if d.name in seen:
                if label not in seen[d.name]["roots"]:
                    seen[d.name]["roots"].append(label)
                continue
            skill_md = d / SKILL_MD
            content = _read_text(skill_md) if skill_md.exists() else ""
            all_files = [p for p in d.rglob("*") if p.is_file()]
            seen[d.name] = {
                "name": d.name,
                "description": _extract_description(content),
                "has_skill_md": skill_md.exists(),
                "file_count": len(all_files),
                "modified_at": datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%SZ"),
                "roots": [label],
            }
    return sorted(seen.values(), key=lambda s: s["name"])


def get_skill(name: str) -> Optional[Dict[str, Any]]:
    dirs = _existing_skill_dirs(name)
    if not dirs:
        return None
    _, primary = dirs[0]
    skill_md = primary / SKILL_MD
    content = _read_text(skill_md) if skill_md.exists() else ""
    files = [_file_summary(p, primary) for p in sorted(primary.rglob("*")) if p.is_file()]
    return {
        "name": name,
        "description": _extract_description(content),
        "content": content,
        "files": files,
        "modified_at": datetime.fromtimestamp(primary.stat().st_mtime, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "roots": [label for label, _ in dirs],
    }


def upsert_skill(name: str, content: str) -> Dict[str, Any]:
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError(
            f"SKILL.md too large ({len(content)} chars). Limit is {MAX_FILE_BYTES} bytes."
        )
    written: List[str] = []
    for label, d in _all_skill_dirs(name):
        d.mkdir(parents=True, exist_ok=True)
        (d / SKILL_MD).write_text(content, encoding="utf-8")
        written.append(label)
    logger.info("Skill upserted: %s -> %s (%d bytes)", name, written, len(content))
    return get_skill(name) or {}


def delete_skill(name: str) -> bool:
    dirs = _existing_skill_dirs(name)
    if not dirs:
        return False
    for _, d in dirs:
        shutil.rmtree(d)
    logger.info("Skill deleted: %s (%d roots)", name, len(dirs))
    return True


def read_skill_file(name: str, rel_path: str) -> bytes:
    _validate_rel_path(rel_path)
    for _, d in _existing_skill_dirs(name):
        target = (d / rel_path).resolve()
        if not str(target).startswith(str(d.resolve())):
            raise ValueError("path escapes skill folder")
        if target.is_file():
            return target.read_bytes()
    raise FileNotFoundError(f"{name}/{rel_path}")


def write_skill_file(name: str, rel_path: str, data: bytes) -> Dict[str, Any]:
    _validate_rel_path(rel_path)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(
            f"file too large ({len(data)} bytes). Limit is {MAX_FILE_BYTES}."
        )
    last_summary: Optional[Dict[str, Any]] = None
    for _, d in _all_skill_dirs(name):
        d.mkdir(parents=True, exist_ok=True)
        target = (d / rel_path).resolve()
        if not str(target).startswith(str(d.resolve())):
            raise ValueError("path escapes skill folder")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        last_summary = _file_summary(target, d)
    assert last_summary is not None
    return last_summary


def delete_skill_file(name: str, rel_path: str) -> bool:
    _validate_rel_path(rel_path)
    removed = False
    for _, d in _existing_skill_dirs(name):
        target = (d / rel_path).resolve()
        if not str(target).startswith(str(d.resolve())):
            raise ValueError("path escapes skill folder")
        if target.is_file():
            target.unlink()
            removed = True
    return removed


def roots_info() -> List[Dict[str, Any]]:
    """Diagnostic: list enabled roots and whether each exists."""
    return [
        {"label": label, "path": str(r), "exists": r.exists()}
        for label, r in _enabled_roots()
    ]
