"""Share links for a project: `data/design/shares.json` (only when `design.share.enabled`).

    {"<token>": {token, project_id, role: view|comment|edit, created_at, updated_at,
                 expires_at: iso|null, snapshot: {at, files: {rel: sha256}} | null}}

A link shows a **frozen snapshot** of the project taken when it was created (or last
updated): the files' contents are stored as content-addressed blobs in the project's
own `.versions/objects/` (shared with the version history, so an unchanged file
costs nothing). The owner sees "changes since last shared" and can update the
snapshot in place — the URL stays the same. Records written before snapshots
existed have no `snapshot` and keep showing the live project.

A token is 32 random url-safe bytes; it is the whole credential, so it is compared
in constant time and never listed outside its own project. An expired link resolves
to nothing, exactly like a revoked one.
"""

from __future__ import annotations

import hmac
import io
import re
import secrets
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import config
from services.design import files as dfiles
from services.design import store, versions

ROLES = ("view", "comment", "edit")
MAX_SHARES_PER_PROJECT = 50
MAX_EXPIRY_SEC = 365 * 86400
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
MAX_ZIP_BYTES = 256 * 1024 * 1024
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
# Host bookkeeping: never part of what a recipient sees.
_EXCLUDE = {"comments.json", "assets.json", "boards.json", "thumbnail.webp", "doc.fig", "docs/canvases.json"}
_lock = threading.RLock()


def _excluded(rel: str) -> bool:
    """Host bookkeeping plus every canvas document and its JSON mirror."""
    return rel in _EXCLUDE or store.is_canvas_path(rel) or (
        rel.endswith(".fig.json") and store.is_canvas_path(rel[:-5]))


def enabled() -> bool:
    return bool(config.get_nested("design.share.enabled", False))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(iso: Any) -> Optional[datetime]:
    if not isinstance(iso, str):
        return None
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _path():
    return store.base_dir() / "shares.json"


def _load() -> Dict[str, Dict[str, Any]]:
    data = store._read_json(_path())
    return data if isinstance(data, dict) else {}


def _save(data: Dict[str, Dict[str, Any]]) -> None:
    store._write_json(_path(), data)


def expired(rec: Dict[str, Any]) -> bool:
    exp = _parse(rec.get("expires_at"))
    return bool(exp and exp <= _now())


def _expiry(expires_in: Any) -> Optional[str]:
    """`expires_in` seconds (int) → ISO timestamp; None/0 = never."""
    if expires_in in (None, "", 0, "0", "never"):
        return None
    try:
        sec = int(expires_in)
    except (TypeError, ValueError):
        raise ValueError("expires_in must be a number of seconds (or null for never)")
    if sec < 60 or sec > MAX_EXPIRY_SEC:
        raise ValueError("expires_in must be between 60 seconds and 365 days")
    return _iso(_now() + timedelta(seconds=sec))


# ── Snapshot ─────────────────────────────────────────────────────────────

def _share_tree(pid: str) -> Dict[str, Any]:
    """Every file a recipient's pages may load: sources, uploads and the staged
    `_ds/` (which the version history leaves out as read-only host state)."""
    root = store.project_dir(pid)
    out = {}
    if not root:
        return out
    for rel, p in dfiles.iter_project_files(root, include_readonly=True):
        if _excluded(rel) or rel.split("/", 1)[0] in dfiles.HIDDEN_TOP:
            continue
        out[rel] = p
    return out


def current_hashes(pid: str) -> Dict[str, str]:
    """rel → sha256 of the working tree (no blobs stored)."""
    out: Dict[str, str] = {}
    for rel, p in _share_tree(pid).items():
        try:
            out[rel] = versions._hash_file(p)
        except OSError:
            continue
    return out


def take_snapshot(pid: str) -> Dict[str, Any]:
    root = store.project_dir(pid)
    if not root:
        raise LookupError("project not found")
    files: Dict[str, str] = {}
    total = 0
    with versions.lock_for(pid):
        for rel, p in _share_tree(pid).items():
            try:
                size = p.stat().st_size
                total += size
                if total > MAX_SNAPSHOT_BYTES:
                    raise ValueError("project too large to snapshot for sharing")
                sha = versions._hash_file(p)
                versions._store_blob(root, p, sha)
            except OSError:
                continue
            files[rel] = sha
    latest = versions.latest(pid) or {}
    return {"at": _iso(_now()), "files": files, "version": latest.get("v")}


def changes_since(pid: str, rec: Dict[str, Any], current: Optional[Dict[str, str]] = None) -> Dict[str, List[str]]:
    snap = (rec.get("snapshot") or {}).get("files")
    if not isinstance(snap, dict):
        return {"added": [], "modified": [], "removed": []}
    cur = current if current is not None else current_hashes(pid)
    return {
        "added": sorted(r for r in cur if r not in snap),
        "modified": sorted(r for r in cur if r in snap and snap[r] != cur[r]),
        "removed": sorted(r for r in snap if r not in cur),
    }


_REF_RE = re.compile(r"""(?:\b(?:src|href|poster)\s*=\s*["']([^"'#?]+)|url\(\s*["']?([^"')#?]+))""", re.I)


def missing_dependencies(files: Dict[str, str], reader) -> List[Dict[str, str]]:
    """Relative references in the snapshot's HTML/CSS/JSX that point at files the
    snapshot does not contain (they would 404 for a recipient)."""
    out: List[Dict[str, str]] = []
    seen = set()
    for rel in sorted(files):
        if not rel.lower().endswith((".html", ".htm", ".css", ".jsx", ".js")):
            continue
        data = reader(files[rel])
        if not data or len(data) > 4 * 1024 * 1024:
            continue
        text = data.decode("utf-8", errors="replace")
        base = rel.rsplit("/", 1)[0] + "/" if "/" in rel else ""
        for m in _REF_RE.finditer(text):
            ref = (m.group(1) or m.group(2) or "").strip()
            if not ref or re.match(r"^(?:[a-z][a-z0-9+.-]*:|//|/|\$|\{)", ref, re.I) or "${" in ref:
                continue
            parts: List[str] = []
            for seg in (base + ref).split("/"):
                if seg in ("", "."):
                    continue
                if seg == "..":
                    if parts:
                        parts.pop()
                    continue
                parts.append(seg)
            target = "/".join(parts)
            if not target or target in files or (rel, target) in seen:
                continue
            if target.split("/", 1)[0] in ("_td", "starters", "ds"):   # served by the preview origin itself
                continue
            seen.add((rel, target))
            out.append({"file": rel, "ref": ref, "missing": target})
            if len(out) >= 50:
                return out
    return out


# ── Records ──────────────────────────────────────────────────────────────

def public(rec: Dict[str, Any], current: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """The owner's view of a link (token included — it's their project)."""
    snap = rec.get("snapshot")
    out = {k: rec.get(k) for k in ("token", "project_id", "role", "created_at", "updated_at", "expires_at")}
    out["expired"] = expired(rec)
    out["live"] = not isinstance(snap, dict)
    if isinstance(snap, dict):
        ch = changes_since(rec["project_id"], rec, current)
        out["snapshot"] = {"at": snap.get("at"), "version": snap.get("version"),
                           "file_count": len(snap.get("files") or {})}
        out["changes"] = ch
        out["changed_count"] = sum(len(v) for v in ch.values())
    return out


def list_shares(pid: str) -> List[Dict[str, Any]]:
    recs = [s for s in _load().values() if s.get("project_id") == pid]
    cur = current_hashes(pid) if any(isinstance(r.get("snapshot"), dict) for r in recs) else {}
    out = [public(r, cur) for r in recs]
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out


def create_share(pid: str, role: str, expires_in: Any = None, snapshot: bool = True) -> Dict[str, Any]:
    if role not in ROLES:
        raise ValueError("role must be view, comment or edit")
    if not store.get_project(pid):
        raise LookupError("project not found")
    exp = _expiry(expires_in)
    snap = take_snapshot(pid) if snapshot else None
    with _lock:
        data = _load()
        if sum(1 for s in data.values() if s.get("project_id") == pid) >= MAX_SHARES_PER_PROJECT:
            raise ValueError("too many share links for this project")
        token = secrets.token_urlsafe(32)
        now = _iso(_now())
        rec = {"token": token, "project_id": pid, "role": role, "created_at": now, "updated_at": now,
               "expires_at": exp, "snapshot": snap}
        data[token] = rec
        _save(data)
    return public(rec)


def _own(data: Dict[str, Dict[str, Any]], pid: str, token: str) -> Optional[Dict[str, Any]]:
    if not isinstance(token, str) or not _TOKEN_RE.match(token):
        return None
    rec = data.get(token)
    return rec if rec and rec.get("project_id") == pid else None


def update_share(pid: str, token: str, *, resnapshot: bool = False, role: Optional[str] = None,
                 expires_in: Any = "keep") -> Optional[Dict[str, Any]]:
    """Refresh the snapshot to the current files and/or change role / expiry."""
    if role is not None and role not in ROLES:
        raise ValueError("role must be view, comment or edit")
    exp = None if expires_in == "keep" else _expiry(expires_in)
    snap = take_snapshot(pid) if resnapshot else None
    with _lock:
        data = _load()
        rec = _own(data, pid, token)
        if not rec:
            return None
        if resnapshot:
            rec["snapshot"] = snap
        if role is not None:
            rec["role"] = role
        if expires_in != "keep":
            rec["expires_at"] = exp
        rec["updated_at"] = _iso(_now())
        _save(data)
    return public(rec)


def delete_share(pid: str, token: str) -> bool:
    with _lock:
        data = _load()
        if not _own(data, pid, token):
            return False
        del data[token]
        _save(data)
        return True


def resolve(token: str) -> Optional[Dict[str, Any]]:
    """The share record for a token, or None (also None while sharing is
    disabled, once the link expired, or when its project is gone)."""
    if not enabled() or not isinstance(token, str) or not _TOKEN_RE.match(token):
        return None
    for key, rec in _load().items():
        if hmac.compare_digest(key, token):
            if expired(rec):
                return None
            return rec if store.get_project(rec.get("project_id", "")) else None
    return None


# ── Recipient access ─────────────────────────────────────────────────────

def snapshot_files(rec: Dict[str, Any]) -> Optional[Dict[str, str]]:
    snap = rec.get("snapshot")
    return snap.get("files") if isinstance(snap, dict) and isinstance(snap.get("files"), dict) else None


def list_recipient_files(rec: Dict[str, Any]) -> List[Dict[str, Any]]:
    snap = snapshot_files(rec)
    if snap is None:
        return dfiles.list_files(rec["project_id"]) or []
    out = []
    for rel in sorted(snap):
        out.append({"path": rel, "kind": dfiles.kind_of(rel), "sha256": snap[rel]})
    return out


def read_file(rec: Dict[str, Any], rel: str) -> Optional[bytes]:
    """Bytes of `rel` as the recipient sees it (snapshot, or live for old links)."""
    if not store.safe_relpath(rel) or rel.split("/", 1)[0] in dfiles.HIDDEN_TOP or _excluded(rel):
        return None
    snap = snapshot_files(rec)
    if snap is None:
        p = dfiles.read_path(rec["project_id"], rel)
        try:
            return p.read_bytes() if p else None
        except OSError:
            return None
    sha = snap.get(rel)
    return versions.read_blob(rec["project_id"], sha) if sha else None


def recipient_zip(rec: Dict[str, Any]) -> Tuple[str, bytes]:
    """(filename, zip bytes) of what the recipient can see."""
    title = (store.get_project(rec["project_id"]) or {}).get("title") or "design"
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()[:60] or "design"
    buf = io.BytesIO()
    total = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in list_recipient_files(rec):
            data = read_file(rec, f["path"])
            if data is None:
                continue
            total += len(data)
            if total > MAX_ZIP_BYTES:
                raise ValueError("snapshot too large to download as one ZIP")
            z.writestr(f"{slug}/{f['path']}", data)
    return f"{slug}.zip", buf.getvalue()


def dependency_report(rec: Dict[str, Any]) -> List[Dict[str, str]]:
    snap = snapshot_files(rec)
    if snap is None:
        return []
    return missing_dependencies(snap, lambda sha: versions.read_blob(rec["project_id"], sha))
