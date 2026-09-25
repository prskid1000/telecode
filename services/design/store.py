"""Persistent storage for TeleDesign projects and design systems.

Layout (under <settings_dir>/data/design/):

    projects/<id>.json                 project record
    projects/<id>/doc.fig              the canvas, saved by the open-pencil editor (.fig bytes:
                                       Kiwi + Zstd + ZIP). Absent until the first save.
    projects/<id>/boards.json          HTML boards: {board_key: {src, width, height}}. .fig has
                                       no node for a live page, so a frame marks the spot and the
                                       host overlays the sandboxed iframe on it. Keyed by a board
                                       key stored in the frame itself, because open-pencil renumbers
                                       node ids on every reopen (resolve via telecode_board_list).
    projects/<id>/imports/*.pen        imported pen.dev files (open-pencil reads .pen, not writes)
    projects/<id>/*.html, *.jsx, ...   files behind HTML boards (the agent's cwd)
    projects/<id>/.versions/           snapshots of touched files per turn
    projects/<id>/comments.json        pinned comments on any board
    systems/<id>.json                  design system record
    systems/<id>/                      DESIGN.md, tokens.json, components/, assets/

One tab, one canvas: Claude-Design-style generated prototypes and pen.dev-style
structured layers live side by side as boards, and either can be converted to
the other (HTML → layers, layers → code).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger("telecode.services.design")

VALID_KINDS = (
    "prototype", "slides", "wireframe", "one_pager", "animation", "landing_page",
    "mobile_app", "web_app", "dashboard_table", "design_system", "other",
)

# The REST surface has no auth, so every id that reaches a path is checked.
_ID_RE = re.compile(r"^[0-9a-f]{32}$")

_lock = threading.RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def base_dir() -> Path:
    return Path(config._settings_dir()) / "data" / "design"


def _projects_dir() -> Path:
    return base_dir() / "projects"


def _systems_dir() -> Path:
    return base_dir() / "systems"


def valid_id(value: str) -> bool:
    return bool(value) and bool(_ID_RE.match(value))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("design: unreadable %s: %s", path, exc)
        return None


# open-pencil's own cap on a document it will open.
MAX_CANVAS_BYTES = 64 * 1024 * 1024
MAX_IMPORT_BYTES = 64 * 1024 * 1024

# A .fig is a ZIP container; refuse anything else rather than store junk the
# editor will fail to open later.
_ZIP_MAGIC = b"PK"


# ── Projects ─────────────────────────────────────────────────────────────

def list_projects(include_archived: bool = False) -> List[Dict[str, Any]]:
    d = _projects_dir()
    if not d.exists():
        return []
    out = []
    for f in d.glob("*.json"):
        rec = _read_json(f)
        if rec and (include_archived or not rec.get("archived")):
            out.append(rec)
    out.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return out


def get_project(pid: str) -> Optional[Dict[str, Any]]:
    if not valid_id(pid):
        return None
    return _read_json(_projects_dir() / f"{pid}.json")


def create_project(data: Dict[str, Any]) -> Dict[str, Any]:
    kind = data.get("kind") if data.get("kind") in VALID_KINDS else "prototype"
    system_id = data.get("design_system_id")
    now = _now_iso()
    rec = {
        "id": uuid.uuid4().hex,
        "title": (data.get("title") or "Untitled design").strip()[:200],
        "kind": kind,
        "design_system_id": system_id if system_id and valid_id(system_id) else None,
        # Direction picker choice (seeds/styles/styles.json id) for projects with no system.
        "style_id": _clean_style_id(data.get("style_id")),
        # The built-in agent: optional agent persona (AGENT.md) plus the direct
        # task session it resumes, so iterations keep context. No Team-Mode jobs.
        "agent_id": data.get("agent_id") or None,
        "session_id": None,
        "current_version": 0,
        "active_chat_id": None,
        "title_locked": bool((data.get("title") or "").strip()) and (data.get("title") or "").strip().lower() not in ("untitled", "untitled design"),
        "thumbnail": False,
        "archived": False,
        "created_at": now,
        "updated_at": now,
    }
    with _lock:
        pdir = _projects_dir() / rec["id"]
        pdir.mkdir(parents=True, exist_ok=True)
        _write_json(pdir / "boards.json", {})
        _write_json(pdir / "comments.json", [])
        _write_json(_projects_dir() / f"{rec['id']}.json", rec)
    return rec


_STYLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _clean_style_id(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and _STYLE_ID_RE.match(value) else None


_PROJECT_MUTABLE = ("title", "kind", "design_system_id", "style_id", "agent_id", "archived",
                    "active_chat_id", "title_locked")


def update_project(pid: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Caller-facing PATCH: only the user-editable fields."""
    with _lock:
        rec = get_project(pid)
        if not rec:
            return None
        for k in _PROJECT_MUTABLE:
            if k in patch:
                rec[k] = patch[k]
        if "style_id" in patch:
            rec["style_id"] = _clean_style_id(patch["style_id"])
        if rec.get("kind") not in VALID_KINDS:
            rec["kind"] = "prototype"
        if isinstance(rec.get("title"), str):
            rec["title"] = rec["title"].strip()[:200] or "Untitled design"
        # A title the user typed is theirs: auto-title never overwrites it.
        if "title" in patch and "title_locked" not in patch:
            rec["title_locked"] = True
        for k in ("design_system_id", "active_chat_id"):
            if rec.get(k) is not None and not valid_id(str(rec.get(k))):
                rec[k] = None
        rec["title_locked"] = bool(rec.get("title_locked"))
        rec["archived"] = bool(rec.get("archived"))
        rec["updated_at"] = _now_iso()
        _write_json(_projects_dir() / f"{pid}.json", rec)
        return rec


def set_project_fields(pid: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """Internal write of host-owned fields (current_version, thumbnail, auto title…).

    Not reachable from PATCH, so a caller cannot forge `current_version`.
    """
    with _lock:
        rec = get_project(pid)
        if not rec:
            return None
        rec.update(fields)
        rec["updated_at"] = _now_iso()
        _write_json(_projects_dir() / f"{pid}.json", rec)
        return rec


def delete_project(pid: str) -> bool:
    if not valid_id(pid):
        return False
    with _lock:
        f = _projects_dir() / f"{pid}.json"
        if not f.exists():
            return False
        f.unlink()
        shutil.rmtree(_projects_dir() / pid, ignore_errors=True)
        return True


def project_dir(pid: str) -> Optional[Path]:
    return _projects_dir() / pid if get_project(pid) else None


# ── Canvas document ──────────────────────────────────────────────────────

def get_canvas(pid: str) -> Optional[bytes]:
    d = project_dir(pid)
    if not d:
        return None
    try:
        return (d / "doc.fig").read_bytes()
    except FileNotFoundError:
        return None


def save_canvas(pid: str, data: bytes) -> bool:
    if not data or len(data) > MAX_CANVAS_BYTES or not data.startswith(_ZIP_MAGIC):
        return False
    with _lock:
        d = project_dir(pid)
        if not d:
            return False
        tmp = d / "doc.fig.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, d / "doc.fig")
        update_project(pid, {})
        return True


def get_boards(pid: str) -> Optional[Dict[str, Any]]:
    d = project_dir(pid)
    return (_read_json(d / "boards.json") or {}) if d else None


def save_boards(pid: str, boards: Dict[str, Any]) -> bool:
    if not isinstance(boards, dict):
        return False
    clean: Dict[str, Any] = {}
    for node_id, b in boards.items():
        if not isinstance(b, dict) or not isinstance(node_id, str) or len(node_id) > 64:
            return False
        src = b.get("src")
        # Project-relative .html only: the host serves the board from here.
        if not isinstance(src, str) or not safe_relpath(src) or not src.endswith(".html"):
            return False
        clean[node_id] = {"src": src, "width": b.get("width"), "height": b.get("height")}
    with _lock:
        d = project_dir(pid)
        if not d:
            return False
        _write_json(d / "boards.json", clean)
        return True


_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9 ._-]{1,120}$")
# Path segments the agent may create: anything Windows can store that is also
# URL-safe enough to round-trip through /p/<pid>/<path> — no reserved
# characters, no control characters, no `#`/`%` (fragment / escape confusion).
_SAFE_SEGMENT_RE = re.compile(r"^[^<>:\"/\\|?*#%\x00-\x1f]{1,120}$")
_WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                 *(f"lpt{i}" for i in range(1, 10))}
MAX_RELPATH_LEN = 400


def _safe_segment(part: str) -> bool:
    if part in ("", ".", "..") or not _SAFE_SEGMENT_RE.match(part):
        return False
    if part != part.strip() or part.endswith("."):
        return False
    return part.split(".")[0].lower() not in _WIN_RESERVED


def safe_relpath(rel: str) -> bool:
    """A relative path that stays inside the project: no drive, no root, no `..`."""
    if not rel or not isinstance(rel, str) or len(rel) > MAX_RELPATH_LEN:
        return False
    if "\\" in rel or rel.startswith("/") or ":" in rel:
        return False
    return all(_safe_segment(part) for part in rel.split("/"))


def resolve_in(root: Path, rel: str) -> Optional[Path]:
    """`root/rel` when `rel` is a safe relpath that also resolves inside `root`.

    The resolve() check catches what the syntax check cannot: a symlink or
    junction the agent created inside the project that points elsewhere.
    """
    if not safe_relpath(rel):
        return None
    try:
        base = root.resolve()
        p = (root / rel).resolve()
    except (OSError, RuntimeError):
        return None
    if p != base and base not in p.parents:
        return None
    return p


def import_pen(pid: str, filename: str, data: bytes) -> Optional[str]:
    """Store an uploaded .pen for the editor to open; returns its relative path."""
    name = os.path.basename(filename or "")
    if not name.endswith(".pen") or not _SAFE_NAME_RE.match(name):
        return None
    if not data or len(data) > MAX_IMPORT_BYTES:
        return None
    try:
        json.loads(data.decode("utf-8"))  # .pen is JSON; reject anything else
    except Exception:
        return None
    with _lock:
        d = project_dir(pid)
        if not d:
            return None
        (d / "imports").mkdir(exist_ok=True)
        (d / "imports" / name).write_bytes(data)
        return f"imports/{name}"


# ── Design systems ───────────────────────────────────────────────────────

_SEEDS_DIR = Path(__file__).parent / "seeds" / "systems"
_SEED_NS = uuid.UUID("5d1e6c0a-7e1e-4d3a-9c2b-74656c656465")
_SYSTEM_RECORD_KEYS = ("name", "slug", "description", "status", "is_default", "version",
                       "default_theme", "tags", "license", "credits", "source")


def seed_id(slug: str) -> str:
    """Stable id per bundled system, so re-seeding updates in place instead of duplicating."""
    return uuid.uuid5(_SEED_NS, f"teledesign-seed:{slug}").hex


def install_seeds() -> int:
    """Copy bundled design systems into data/design/systems on first use.

    Only installs a seed whose id is absent: once installed it belongs to the
    user (Remix edits it, delete removes it for good). Returns how many were added.
    A delete is remembered in `.deleted_seeds` so the seed is not resurrected.
    """
    if not _SEEDS_DIR.is_dir():
        return 0
    added = 0
    with _lock:
        sysdir = _systems_dir()
        sysdir.mkdir(parents=True, exist_ok=True)
        deleted = set((_read_json(sysdir / ".deleted_seeds.json") or {}).get("ids", []))
        has_default = any(r.get("is_default") for r in _list_system_records())
        for src in sorted(p for p in _SEEDS_DIR.iterdir() if p.is_dir()):
            meta = _read_json(src / "system.json")
            if not meta or not meta.get("slug"):
                continue
            sid = seed_id(meta["slug"])
            if sid in deleted or (sysdir / f"{sid}.json").exists():
                continue
            shutil.copytree(src, sysdir / sid, dirs_exist_ok=True)
            now = _now_iso()
            rec = {k: meta[k] for k in _SYSTEM_RECORD_KEYS if k in meta}
            rec.update({"id": sid, "seed": True, "sources": [{"type": "bundled", "ref": meta["slug"]}],
                        "created_at": now, "updated_at": now})
            # Never take the default away from a system the user already chose.
            rec["is_default"] = bool(meta.get("is_default")) and not has_default
            has_default = has_default or rec["is_default"]
            _write_json(sysdir / f"{sid}.json", rec)
            added += 1
    if added:
        logger.info("design: installed %d bundled design system(s)", added)
    return added


def _list_system_records() -> List[Dict[str, Any]]:
    d = _systems_dir()
    if not d.exists():
        return []
    return [r for r in (_read_json(f) for f in d.glob("*.json") if not f.name.startswith(".")) if r]


def list_systems() -> List[Dict[str, Any]]:
    install_seeds()
    d = _systems_dir()
    if not d.exists():
        return []
    out = _list_system_records()
    out.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return out


def get_system(sid: str) -> Optional[Dict[str, Any]]:
    if not valid_id(sid):
        return None
    return _read_json(_systems_dir() / f"{sid}.json")


def create_system(data: Dict[str, Any]) -> Dict[str, Any]:
    now = _now_iso()
    rec = {
        "id": uuid.uuid4().hex,
        "name": (data.get("name") or "Untitled system").strip()[:200],
        # Where it was extracted from: [{type: "codebase"|"url"|"file"|"figma", ref}]
        "sources": data.get("sources") or [],
        "status": "draft",          # draft → extracting → published | failed
        "is_default": False,
        "created_at": now,
        "updated_at": now,
    }
    with _lock:
        sdir = _systems_dir() / rec["id"]
        (sdir / "components").mkdir(parents=True, exist_ok=True)
        (sdir / "assets").mkdir(parents=True, exist_ok=True)
        _write_json(sdir / "tokens.json", {"color": {}, "typography": {}, "spacing": {}, "radius": {}, "shadow": {}})
        (sdir / "DESIGN.md").write_text(f"# {rec['name']}\n", encoding="utf-8")
        _write_json(_systems_dir() / f"{rec['id']}.json", rec)
    return rec


_SYSTEM_MUTABLE = ("name", "sources", "status", "is_default")


def update_system(sid: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    with _lock:
        rec = get_system(sid)
        if not rec:
            return None
        for k in _SYSTEM_MUTABLE:
            if k in patch:
                rec[k] = patch[k]
        if patch.get("is_default"):
            # Exactly one default: clear it everywhere else.
            for other in list_systems():
                if other["id"] != sid and other.get("is_default"):
                    other["is_default"] = False
                    _write_json(_systems_dir() / f"{other['id']}.json", other)
        rec["updated_at"] = _now_iso()
        _write_json(_systems_dir() / f"{sid}.json", rec)
        return rec


def delete_system(sid: str) -> bool:
    if not valid_id(sid):
        return False
    with _lock:
        f = _systems_dir() / f"{sid}.json"
        if not f.exists():
            return False
        rec = _read_json(f) or {}
        f.unlink()
        shutil.rmtree(_systems_dir() / sid, ignore_errors=True)
        if rec.get("seed"):
            marker = _systems_dir() / ".deleted_seeds.json"
            ids = set((_read_json(marker) or {}).get("ids", [])) | {sid}
            _write_json(marker, {"ids": sorted(ids)})
        return True
