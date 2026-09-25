"""Review manifest `assets.json` (docs/teledesign-contract.md §3).

Canonical on-disk shape:

    {"assets": [{id, name, group, path, board_id?, viewport?, subtitle?, status, versions: [v…]}]}

The agent is taught (prompts/charter.md) to write a bare list with `asset` /
`board` keys; both shapes are accepted on read and normalised to the
canonical one on the next host write, so neither side has to be exact.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Any, Dict, List, Optional

from services.design import store

VALID_STATUS = ("needs-review", "approved", "changes-requested")
MAX_ASSETS = 500
_lock = threading.RLock()

# Deliverables are HTML pages outside the support/staging folders.
_NOT_DELIVERABLE_TOP = {"_ds", "uploads", "scraps", "assets", "imports"}


def _asset_id(path: str, name: str) -> str:
    return hashlib.sha1(f"{path}|{name}".encode("utf-8")).hexdigest()[:16]


def _s(v: Any, cap: int = 300) -> Optional[str]:
    return str(v)[:cap] if isinstance(v, (str, int, float)) and str(v).strip() else None


def _viewport(v: Any) -> Optional[Dict[str, int]]:
    if not isinstance(v, dict):
        return None
    try:
        w, h = int(v.get("width")), int(v.get("height"))
    except (TypeError, ValueError):
        return None
    if 0 < w <= 20000 and 0 < h <= 20000:
        return {"width": w, "height": h}
    return None


def normalise_one(a: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(a, dict):
        return None
    path = _s(a.get("path"), 400)
    if not path or not store.safe_relpath(path):
        return None
    name = _s(a.get("name")) or _s(a.get("asset")) or path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    status = a.get("status") if a.get("status") in VALID_STATUS else "needs-review"
    versions = [int(x) for x in (a.get("versions") or []) if isinstance(x, int) and x > 0][-50:]
    rec = {
        "id": _s(a.get("id"), 64) or _asset_id(path, name),
        "name": name,
        "group": _s(a.get("group"), 120) or "Screens",
        "path": path,
        "status": status,
        "versions": versions,
    }
    board = _s(a.get("board_id")) or _s(a.get("board"))
    if board:
        rec["board_id"] = board
    vp = _viewport(a.get("viewport"))
    if vp:
        rec["viewport"] = vp
    sub = _s(a.get("subtitle"), 400)
    if sub:
        rec["subtitle"] = sub
    return rec


def _parse(raw: Any) -> List[Dict[str, Any]]:
    items = raw.get("assets") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    out, seen = [], set()
    for a in items[:MAX_ASSETS]:
        rec = normalise_one(a)
        if rec and rec["id"] not in seen:
            seen.add(rec["id"])
            out.append(rec)
    return out


def get_assets(pid: str) -> Optional[List[Dict[str, Any]]]:
    d = store.project_dir(pid)
    if not d:
        return None
    return _parse(store._read_json(d / "assets.json"))


def put_assets(pid: str, items: Any) -> Optional[List[Dict[str, Any]]]:
    d = store.project_dir(pid)
    if not d:
        return None
    clean = _parse({"assets": items} if isinstance(items, list) else items)
    with _lock:
        store._write_json(d / "assets.json", {"assets": clean})
    return clean


def patch_asset(pid: str, aid: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    with _lock:
        items = get_assets(pid)
        for a in items or []:
            if a["id"] != aid:
                continue
            if "status" in patch:
                if patch["status"] not in VALID_STATUS:
                    raise ValueError("invalid status")
                a["status"] = patch["status"]
            for k in ("name", "group", "subtitle"):
                if k in patch and _s(patch[k]):
                    a[k] = _s(patch[k], 400)
            put_assets(pid, items)
            return a
    return None


def _deliverable(rel: str) -> bool:
    return rel.lower().endswith((".html", ".htm")) and rel.split("/", 1)[0] not in _NOT_DELIVERABLE_TOP


def register_changed(pid: str, changed: List[str], version: Optional[int]) -> bool:
    """Post-turn: auto-register new HTML deliverables; a changed one goes back
    to needs-review and gains the version. Returns True if the manifest changed.
    """
    with _lock:
        items = get_assets(pid)
        if items is None:
            return False
        d = store.project_dir(pid)
        by_path: Dict[str, List[Dict[str, Any]]] = {}
        for a in items:
            by_path.setdefault(a["path"], []).append(a)
        dirty = False
        for rel in changed:
            exists = bool(d and (d / rel).is_file())
            if rel in by_path:
                if not exists:
                    continue
                for a in by_path[rel]:
                    a["status"] = "needs-review"
                    if version and version not in a["versions"]:
                        a["versions"].append(version)
                    dirty = True
            elif exists and _deliverable(rel):
                rec = normalise_one({"path": rel, "group": "Screens",
                                     "versions": [version] if version else []})
                if rec:
                    items.append(rec)
                    dirty = True
        if dirty:
            store._write_json(d / "assets.json", {"assets": items[:MAX_ASSETS]})
        return dirty
