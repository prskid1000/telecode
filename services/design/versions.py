"""Content-addressed project versions (docs/teledesign-contract.md §3, §4.1).

    .versions/manifest.json      {"versions": [{v, at, origin, turn_id?, prompt?, parent,
                                                files: {rel_path: sha256}}]}
    .versions/objects/<sha256>   one blob per distinct content (doc.fig included)

Every version records the *whole* tracked tree, not a delta, so restore and
compare are a map lookup; blobs are shared, so an unchanged file costs nothing.
A snapshot whose tree equals the latest version's creates nothing.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.design import files as dfiles
from services.design import store

logger = logging.getLogger("telecode.services.design.versions")

VALID_ORIGINS = ("agent", "user", "tweak", "restore")
# Derived or host-owned state that must not flip back on restore.
_UNTRACKED = {"comments.json", "assets.json", "thumbnail.webp"}
MAX_TRACKED_BYTES = 64 * 1024 * 1024
MAX_DIFF_BYTES = 2 * 1024 * 1024

_locks_guard = threading.Lock()
_locks: Dict[str, threading.RLock] = {}


def lock_for(pid: str) -> threading.RLock:
    with _locks_guard:
        lk = _locks.get(pid)
        if lk is None:
            lk = _locks[pid] = threading.RLock()
        return lk


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _vdir(root: Path) -> Path:
    return root / ".versions"


def _read_manifest(root: Path) -> Dict[str, Any]:
    try:
        data = json.loads((_vdir(root) / "manifest.json").read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("versions"), list):
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("design versions: unreadable manifest in %s: %s", root, exc)
    return {"versions": []}


def _write_manifest(root: Path, data: Dict[str, Any]) -> None:
    store._write_json(_vdir(root) / "manifest.json", data)


def _hash_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def tracked_tree(root: Path) -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    for rel, p in dfiles.iter_project_files(root, include_readonly=False):
        if rel in _UNTRACKED:
            continue
        try:
            if p.stat().st_size > MAX_TRACKED_BYTES:
                continue
        except OSError:
            continue
        out[rel] = p
    return out


def _store_blob(root: Path, p: Path, sha: str) -> None:
    obj = _vdir(root) / "objects" / sha
    if obj.exists():
        return
    obj.parent.mkdir(parents=True, exist_ok=True)
    tmp = obj.with_name(obj.name + ".tmp")
    with p.open("rb") as src, tmp.open("wb") as dst:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            dst.write(chunk)
    os.replace(tmp, obj)


def list_versions(pid: str) -> Optional[List[Dict[str, Any]]]:
    root = store.project_dir(pid)
    if not root:
        return None
    return _read_manifest(root)["versions"]


def get_version(pid: str, v: int) -> Optional[Dict[str, Any]]:
    for rec in list_versions(pid) or []:
        if rec.get("v") == v:
            return rec
    return None


def latest(pid: str) -> Optional[Dict[str, Any]]:
    vs = list_versions(pid) or []
    return vs[-1] if vs else None


def changed_between(a: Optional[Dict[str, str]], b: Dict[str, str]) -> List[str]:
    a = a or {}
    return sorted({k for k in set(a) | set(b) if a.get(k) != b.get(k)})


def snapshot(pid: str, origin: str, *, turn_id: Optional[str] = None,
             prompt: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Record the current tree. Returns (version record or None, changed paths).

    No new version when nothing changed since the latest one.
    """
    if origin not in VALID_ORIGINS:
        raise ValueError(f"bad origin {origin!r}")
    root = store.project_dir(pid)
    if not root:
        return None, []
    with lock_for(pid):
        manifest = _read_manifest(root)
        prev = manifest["versions"][-1] if manifest["versions"] else None
        tree: Dict[str, str] = {}
        for rel, p in tracked_tree(root).items():
            try:
                sha = _hash_file(p)
                _store_blob(root, p, sha)
            except OSError as exc:
                logger.warning("design versions: skip %s: %s", rel, exc)
                continue
            tree[rel] = sha
        changed = changed_between(prev["files"] if prev else None, tree)
        if prev is not None and not changed:
            return None, []
        if prev is None and not tree:
            return None, []
        rec: Dict[str, Any] = {
            "v": (prev["v"] + 1) if prev else 1,
            "at": _now_iso(),
            "origin": origin,
            "parent": prev["v"] if prev else None,
            "files": tree,
            "changed": changed,
        }
        if turn_id:
            rec["turn_id"] = turn_id
        if prompt:
            rec["prompt"] = prompt[:500]
        manifest["versions"].append(rec)
        _write_manifest(root, manifest)
        store.set_project_fields(pid, current_version=rec["v"])
        return rec, changed


def read_blob(pid: str, sha: str) -> Optional[bytes]:
    root = store.project_dir(pid)
    if not root or not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        return None
    try:
        return (_vdir(root) / "objects" / sha).read_bytes()
    except FileNotFoundError:
        return None


def file_at(pid: str, v: int, rel: str) -> Optional[bytes]:
    rec = get_version(pid, v)
    if not rec or not store.safe_relpath(rel):
        return None
    sha = rec["files"].get(rel)
    return read_blob(pid, sha) if sha else None


def restore(pid: str, v: int, paths: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Bring files back to version `v` and record the result as a `restore` version.

    With `paths`, only those files; a path absent from `v` is deleted (it did
    not exist then). Without, the whole tracked tree: files added since are removed.
    Returns the new version (or the current latest if the tree already matched).
    """
    root = store.project_dir(pid)
    target = get_version(pid, v)
    if not root or not target:
        return None
    with lock_for(pid):
        want: Dict[str, str] = target["files"]
        current = tracked_tree(root)
        if paths:
            scope = [p for p in paths if store.safe_relpath(p)]
        else:
            scope = sorted(set(want) | set(current))
        for rel in scope:
            p = store.resolve_in(root, rel)
            if not p:
                continue
            sha = want.get(rel)
            if sha:
                blob = read_blob(pid, sha)
                if blob is None:
                    logger.warning("design versions: missing blob %s for %s", sha, rel)
                    continue
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_name(f".td-restore.tmp")
                tmp.write_bytes(blob)
                os.replace(tmp, p)
            elif p.is_file() and rel in current and (paths or not rel.startswith("uploads/")):
                # A whole-tree restore keeps later uploads: they are the user's inputs,
                # not design state. Naming one in `paths` still removes it.
                p.unlink()
        rec, _ = snapshot(pid, "restore", prompt=f"Restored v{v}" + (f" ({len(scope)} file(s))" if paths else ""))
        return rec or latest(pid)


def _text(data: Optional[bytes]) -> Optional[List[str]]:
    if data is None:
        return []
    if len(data) > MAX_DIFF_BYTES or b"\x00" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace").splitlines(keepends=True)


def diff(pid: str, a: int, b: Optional[int], rel: Optional[str] = None) -> Optional[str]:
    """Unified diff between versions `a` and `b` (`b` None = working tree)."""
    root = store.project_dir(pid)
    va = get_version(pid, a)
    if not root or not va:
        return None
    if b is None:
        fb = {r: p for r, p in tracked_tree(root).items()}
        get_b = lambda r: fb[r].read_bytes() if r in fb else None  # noqa: E731
        names_b = set(fb)
        label_b = "working"
    else:
        vb = get_version(pid, b)
        if not vb:
            return None
        get_b = lambda r: read_blob(pid, vb["files"][r]) if r in vb["files"] else None  # noqa: E731
        names_b = set(vb["files"])
        label_b = f"v{b}"
    names = sorted(set(va["files"]) | names_b)
    if rel:
        if not store.safe_relpath(rel):
            return None
        names = [rel]
    out: List[str] = []
    for name in names:
        da = read_blob(pid, va["files"][name]) if name in va["files"] else None
        db = get_b(name)
        if da == db:
            continue
        la, lb = _text(da), _text(db)
        if la is None or lb is None:
            out.append(f"Binary file {name} differs\n")
            continue
        out.extend(difflib.unified_diff(la, lb, fromfile=f"v{a}/{name}", tofile=f"{label_b}/{name}"))
        if out and not out[-1].endswith("\n"):
            out.append("\n")
    return "".join(out)
