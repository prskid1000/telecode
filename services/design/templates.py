"""Saved project templates: `data/design/templates/<id>/`.

    template.json   {id, name, intro_text, kind, design_system_id, source_project_id, cover, created_at}
    project/        the project's tracked files at save time (no versions, chats or comments)
    cover.<ext>     optional cover image

Instantiate = new project + copy of `project/` + a first version (origin `user`).
"""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.design import store, versions

logger = logging.getLogger("telecode.services.design.templates")

_COVER_EXTS = (".webp", ".png", ".jpg", ".jpeg")
MAX_TEMPLATE_BYTES = 512 * 1024 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tdir() -> Path:
    return store.base_dir() / "templates"


def list_templates() -> List[Dict[str, Any]]:
    d = _tdir()
    if not d.exists():
        return []
    out = [r for r in (store._read_json(p / "template.json") for p in d.iterdir() if p.is_dir()) if r]
    out.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return out


def get_template(tid: str) -> Optional[Dict[str, Any]]:
    if not store.valid_id(tid):
        return None
    return store._read_json(_tdir() / tid / "template.json")


def cover_path(tid: str) -> Optional[Path]:
    rec = get_template(tid)
    if not rec or not rec.get("cover"):
        return None
    p = _tdir() / tid / rec["cover"]
    return p if p.is_file() else None


def create_template(data: Dict[str, Any]) -> Dict[str, Any]:
    pid = data.get("project_id")
    project = store.get_project(pid) if isinstance(pid, str) else None
    root = store.project_dir(pid) if project else None
    if not project or not root:
        raise LookupError("project not found")
    name = str(data.get("name") or project.get("title") or "Template").strip()[:120]
    tid = uuid.uuid4().hex
    tdir = _tdir() / tid
    total = 0
    tree = versions.tracked_tree(root)
    for rel, p in tree.items():
        total += p.stat().st_size
        if total > MAX_TEMPLATE_BYTES:
            shutil.rmtree(tdir, ignore_errors=True)
            raise ValueError("project too large for a template")
        dst = tdir / "project" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
    cover = None
    src_cover = data.get("cover")
    if isinstance(src_cover, str) and src_cover:
        cp = store.resolve_in(root, src_cover)
        if not cp or not cp.is_file() or cp.suffix.lower() not in _COVER_EXTS:
            shutil.rmtree(tdir, ignore_errors=True)
            raise ValueError("cover must be a project image (.webp/.png/.jpg)")
    elif (root / "thumbnail.webp").is_file():
        cp = root / "thumbnail.webp"
    else:
        cp = None
    tdir.mkdir(parents=True, exist_ok=True)
    if cp:
        cover = "cover" + cp.suffix.lower()
        shutil.copy2(cp, tdir / cover)
    rec = {
        "id": tid,
        "name": name,
        "intro_text": str(data.get("intro_text") or "")[:4000],
        "kind": project.get("kind"),
        "design_system_id": project.get("design_system_id"),
        "source_project_id": pid,
        "cover": cover,
        "file_count": len(tree),
        "created_at": _now_iso(),
    }
    store._write_json(tdir / "template.json", rec)
    return rec


def delete_template(tid: str) -> bool:
    if not get_template(tid):
        return False
    shutil.rmtree(_tdir() / tid, ignore_errors=True)
    return True


def instantiate(tid: str, data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    rec = get_template(tid)
    if not rec:
        return None
    data = data or {}
    proj = store.create_project({
        "title": data.get("title") or rec["name"],
        "kind": rec.get("kind"),
        "design_system_id": data.get("design_system_id", rec.get("design_system_id")),
    })
    src = _tdir() / tid / "project"
    dst = store.project_dir(proj["id"])
    if src.is_dir() and dst:
        for p in src.rglob("*"):
            if p.is_file():
                rel = p.relative_to(src).as_posix()
                if not store.safe_relpath(rel):
                    continue
                out = dst / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, out)
    versions.snapshot(proj["id"], "user", prompt=f"From template “{rec['name']}”")
    store.set_project_fields(proj["id"], template_id=tid, intro_text=rec.get("intro_text") or "")
    return store.get_project(proj["id"])
