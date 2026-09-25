"""Generic filesystem-backed task sessions. Ported from pythonmagic.

Expiry (B1). A session is *ephemeral* when ``data.ephemeral`` is true or it
lives in one of EPHEMERAL_NAMESPACES (run fan-out, fresh trigger fires; "heartbeat"
is the pre-P3 name); everything else (Task-mode sessions, Team workspaces,
shared trigger sessions and design sessions) is a
*workspace*.

- Absolute TTL applies to ephemeral sessions only; workspaces expire on idle
  time alone.
- A session with a PENDING/RUNNING task is never expired.
- An expired ephemeral session is deleted. An expired workspace is
  **archived** — moved to ``data/task_sessions/_archived/<ns|~root>/<id>`` —
  never removed, and ``ensure()`` (every task submit) restores it, so a job
  that runs on an archived workspace gets its files back rather than an
  empty folder.
- ``ensure()`` refreshes ``last_used_at``: submitting a task is use.

Older session.json files need no migration; the rules above read the same
fields.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import config

logger = logging.getLogger("telecode.services.session")

def get_sessions_dir() -> Path:
    base = Path(config._settings_dir()) / "data" / "task_sessions"
    return base

_NAMESPACES_DIR_NAME = "_ns"
_ARCHIVE_DIR_NAME = "_archived"
_ARCHIVE_ROOT_NS = "~root"
EPHEMERAL_NAMESPACES = frozenset({"run-parallel", "heartbeat", "trigger"})
_ARCHIVE_STAMP_RE = re.compile(r"\.\d{8}T\d{6}Z$")
DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS = 86400

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,128}$")
_NAMESPACE_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_MAX_SESSION_JSON_BYTES = 5 * 1024 * 1024

_locks_guard = threading.Lock()
_locks: Dict[str, threading.RLock] = {}

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _validate_id(session_id: str) -> None:
    if not _SESSION_ID_RE.match(session_id):
        raise ValueError(f"Invalid session_id '{session_id}'.")
    if session_id in (_NAMESPACES_DIR_NAME, _ARCHIVE_DIR_NAME):
        raise ValueError("session_id is reserved")

def _validate_namespace(namespace: Optional[str]) -> None:
    if namespace is None: return
    if not _NAMESPACE_RE.match(namespace):
        raise ValueError(f"Invalid namespace '{namespace}'.")

def _session_dir(session_id: str, namespace: Optional[str] = None) -> Path:
    base = get_sessions_dir()
    if namespace is None:
        return base / session_id
    return base / _NAMESPACES_DIR_NAME / namespace / session_id

def _session_json_path(session_id: str, namespace: Optional[str] = None) -> Path:
    return _session_dir(session_id, namespace) / "session.json"

def _lock_for(session_id: str, namespace: Optional[str] = None) -> threading.RLock:
    key = f"{namespace or ''}::{session_id}"
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _locks[key] = lock
        return lock

def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, default=str).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".session.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encoded)
        os.replace(tmp_name, path)
    except Exception:
        try: os.unlink(tmp_name)
        except OSError: pass
        raise

def _deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        else:
            dst[key] = value
    return dst

def _read_session(session_id: str, namespace: Optional[str] = None) -> Optional[Dict[str, Any]]:
    path = _session_json_path(session_id, namespace)
    if not path.exists(): return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return None

def is_ephemeral(meta: Dict[str, Any], namespace: Optional[str] = None) -> bool:
    ns = namespace if namespace is not None else meta.get("namespace")
    return bool((meta.get("data") or {}).get("ephemeral")) or ns in EPHEMERAL_NAMESPACES


def _in_use(session_id: str, namespace: Optional[str]) -> bool:
    """True while any queued task (pending or running) is bound to the session."""
    try:
        from services.task.task_manager import get_task_queue
        return get_task_queue().session_has_active_task(session_id, namespace)
    except Exception:
        return False


def _is_expired(meta: Dict[str, Any]) -> bool:
    now = datetime.now(timezone.utc)
    abs_ttl = meta.get("absolute_ttl_seconds")
    if abs_ttl and is_ephemeral(meta):
        try:
            created = datetime.fromisoformat(meta.get("created_at", "").replace("Z", "+00:00"))
            if (now - created).total_seconds() > abs_ttl: return True
        except Exception: pass
    idle = meta.get("session_idle_timeout_seconds")
    if idle:
        try:
            last = datetime.fromisoformat(meta.get("last_used_at", "").replace("Z", "+00:00"))
            if (now - last).total_seconds() > idle: return True
        except Exception: pass
    return False

def _archive_dir(session_id: str, namespace: Optional[str]) -> Path:
    return get_sessions_dir() / _ARCHIVE_DIR_NAME / (namespace or _ARCHIVE_ROOT_NS) / session_id


def archive(session_id: str, namespace: Optional[str] = None) -> bool:
    """Move a session folder into the archive (never deletes files)."""
    with _lock_for(session_id, namespace):
        folder = _session_dir(session_id, namespace)
        if not folder.exists():
            return False
        meta = _read_session(session_id, namespace)
        if meta is not None:
            meta["archived_at"] = _now_iso()
            try:
                _atomic_write_json(_session_json_path(session_id, namespace), meta)
            except Exception:
                pass
        dest = _archive_dir(session_id, namespace)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            dest.rename(dest.with_name(f"{session_id}.{stamp}"))
        shutil.move(str(folder), str(dest))
        logger.info(f"Archived idle session {namespace or ''}/{session_id} -> {dest}")
        return True


def restore(session_id: str, namespace: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Move an archived session back. Returns its meta, or None if not archived
    (or a live session of that id already exists)."""
    _validate_namespace(namespace)
    _validate_id(session_id)
    with _lock_for(session_id, namespace):
        src = _archive_dir(session_id, namespace)
        if not (src / "session.json").exists() or _session_json_path(session_id, namespace).exists():
            return None
        folder = _session_dir(session_id, namespace)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)  # stray folder without session.json
        folder.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(folder))
        meta = _read_session(session_id, namespace) or {}
        meta.pop("archived_at", None)
        meta["last_used_at"] = _now_iso()
        _atomic_write_json(_session_json_path(session_id, namespace), meta)
        logger.info(f"Restored archived session {namespace or ''}/{session_id}")
        return meta


def list_archived(namespace: Optional[str] = None) -> List[Dict[str, Any]]:
    root = get_sessions_dir() / _ARCHIVE_DIR_NAME / (namespace or _ARCHIVE_ROOT_NS)
    out: List[Dict[str, Any]] = []
    if not root.exists():
        return out
    for entry in root.iterdir():
        p = entry / "session.json"
        # Older archives of the same id carry a ".<timestamp>" suffix; list the latest only.
        if entry.is_dir() and p.exists() and not _ARCHIVE_STAMP_RE.search(entry.name):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
    return out


def _expire(session_id: str, namespace: Optional[str], meta: Dict[str, Any]) -> bool:
    """Apply expiry to one session. Returns True if it left the live set."""
    if _in_use(session_id, namespace):
        return False
    if is_ephemeral(meta, namespace):
        return delete(session_id, namespace)
    return archive(session_id, namespace)


def exists(session_id: str, namespace: Optional[str] = None) -> bool:
    _validate_id(session_id)
    return _session_json_path(session_id, namespace).exists()

def create(
    session_id: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    session_idle_timeout_seconds: Optional[int] = DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS,
    absolute_ttl_seconds: Optional[int] = None,
    files: Optional[Dict[str, str]] = None,
    namespace: Optional[str] = None,
) -> Dict[str, Any]:
    _validate_namespace(namespace)
    sid = session_id or str(uuid.uuid4())
    _validate_id(sid)
    with _lock_for(sid, namespace):
        if _session_json_path(sid, namespace).exists():
            raise FileExistsError(f"Session '{sid}' already exists")
        folder = _session_dir(sid, namespace)
        folder.mkdir(parents=True, exist_ok=True)
        if files:
            for rel, content in files.items():
                fpath = _safe_rel_path(folder, rel)
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_text(content, encoding="utf-8")
        now = _now_iso()
        meta = {
            "session_id": sid,
            "namespace": namespace,
            "created_at": now,
            "last_used_at": now,
            # None = the 1-day default; 0 = never expire (a workspace the user
            # created without a TTL, or with expiry turned off).
            "session_idle_timeout_seconds": (DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS
                                             if session_idle_timeout_seconds is None
                                             else max(0, int(session_idle_timeout_seconds))),
            "task_ids": [],
            "data": data or {},
        }
        if absolute_ttl_seconds: meta["absolute_ttl_seconds"] = int(absolute_ttl_seconds)
        _atomic_write_json(_session_json_path(sid, namespace), meta)
        return meta

def ensure(
    session_id: str,
    data: Optional[Dict[str, Any]] = None,
    session_idle_timeout_seconds: Optional[int] = DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS,
    absolute_ttl_seconds: Optional[int] = None,
    namespace: Optional[str] = None,
) -> Dict[str, Any]:
    _validate_namespace(namespace)
    _validate_id(session_id)
    with _lock_for(session_id, namespace):
        meta = _read_session(session_id, namespace)
        if not meta:
            meta = restore(session_id, namespace)
        if meta:
            # Submitting a task is use: refresh last_used_at so the session
            # cannot be swept between here and the handler's first read.
            meta["last_used_at"] = _now_iso()
            _atomic_write_json(_session_json_path(session_id, namespace), meta)
            return meta
        return create(session_id, data, session_idle_timeout_seconds, absolute_ttl_seconds, namespace=namespace)

def get(session_id: str, namespace: Optional[str] = None) -> Optional[Dict[str, Any]]:
    meta = _read_session(session_id, namespace)
    if not meta: return None
    if _is_expired(meta) and _expire(session_id, namespace, meta):
        return None
    return meta

def list_all(namespace: Optional[str] = None) -> List[Dict[str, Any]]:
    sweep_expired()
    out = []
    base = get_sessions_dir()
    if namespace is None:
        if not base.exists(): return []
        for entry in base.iterdir():
            if entry.is_dir() and entry.name not in (_NAMESPACES_DIR_NAME, _ARCHIVE_DIR_NAME):
                m = _read_session(entry.name)
                if m: out.append(m)
    else:
        ns_dir = base / _NAMESPACES_DIR_NAME / namespace
        if not ns_dir.exists(): return []
        for entry in ns_dir.iterdir():
            if entry.is_dir():
                m = _read_session(entry.name, namespace)
                if m: out.append(m)
    return out

def patch_data(session_id: str, patch: Dict[str, Any], namespace: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with _lock_for(session_id, namespace):
        meta = _read_session(session_id, namespace)
        if not meta: return None
        _deep_merge(meta.setdefault("data", {}), patch or {})
        meta["last_used_at"] = _now_iso()
        _atomic_write_json(_session_json_path(session_id, namespace), meta)
        return meta

def update_session(
    sid: str,
    namespace: Optional[str] = None,
    session_idle_timeout_seconds: Optional[int] = None,
    absolute_ttl_seconds: Optional[int] = None,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    with _lock_for(sid, namespace):
        meta = _read_session(sid, namespace)
        if not meta:
            raise FileNotFoundError(f"Session {sid} not found")
        
        if session_idle_timeout_seconds is not None:
            meta["session_idle_timeout_seconds"] = int(session_idle_timeout_seconds)
        if absolute_ttl_seconds is not None:
            meta["absolute_ttl_seconds"] = int(absolute_ttl_seconds)
        if data is not None:
            meta.setdefault("data", {}).update(data)
            
        meta["last_used_at"] = _now_iso()
        _atomic_write_json(_session_json_path(sid, namespace), meta)
        return meta

def append_task_id(session_id: str, task_id: str, namespace: Optional[str] = None) -> None:
    with _lock_for(session_id, namespace):
        meta = _read_session(session_id, namespace)
        if not meta: return
        ids = meta.setdefault("task_ids", [])
        if task_id not in ids: ids.append(task_id)
        meta["last_used_at"] = _now_iso()
        _atomic_write_json(_session_json_path(session_id, namespace), meta)

def delete(session_id: str, namespace: Optional[str] = None) -> bool:
    with _lock_for(session_id, namespace):
        folder = _session_dir(session_id, namespace)
        if not folder.exists(): return False
        shutil.rmtree(folder, ignore_errors=True)
        return True

def _safe_rel_path(folder: Path, rel: str) -> Path:
    if ".." in rel.replace("\\", "/"): raise ValueError("Invalid path")
    dest = (folder / rel).resolve()
    if folder.resolve() not in dest.parents and folder.resolve() != dest:
        raise ValueError("Path escapes folder")
    return dest

def write_file(session_id: str, rel_path: str, content: bytes, namespace: Optional[str] = None) -> Dict[str, Any]:
    with _lock_for(session_id, namespace):
        meta = _read_session(session_id, namespace)
        if not meta: raise FileNotFoundError("Session not found")
        folder = _session_dir(session_id, namespace)
        dest = _safe_rel_path(folder, rel_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        meta["last_used_at"] = _now_iso()
        _atomic_write_json(_session_json_path(session_id, namespace), meta)
        return {"path": rel_path, "bytes": len(content)}

def list_files(session_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
    folder = _session_dir(session_id, namespace)
    if not folder.exists(): raise FileNotFoundError("Session not found")
    out = []
    for p in folder.rglob("*"):
        if not p.is_file() or p.name == "session.json": continue
        out.append({"path": p.relative_to(folder).as_posix(), "bytes": p.stat().st_size})
    return out

def resolve_file(session_id: str, rel_path: str, namespace: Optional[str] = None) -> Path:
    folder = _session_dir(session_id, namespace)
    if not folder.exists(): raise FileNotFoundError("Session not found")
    dest = _safe_rel_path(folder, rel_path)
    if not dest.exists() or not dest.is_file():
        raise FileNotFoundError(f"File '{rel_path}' not found in session")
    return dest

def delete_file(session_id: str, rel_path: str, namespace: Optional[str] = None) -> bool:
    with _lock_for(session_id, namespace):
        folder = _session_dir(session_id, namespace)
        if not folder.exists(): raise FileNotFoundError("Session not found")
        dest = _safe_rel_path(folder, rel_path)
        if dest.exists() and dest.is_file():
            dest.unlink()
            return True
        return False

def _sweep_dir(root: Path, namespace: Optional[str]) -> int:
    removed = 0
    if not root.exists(): return 0
    for entry in root.iterdir():
        if not entry.is_dir() or (namespace is None and entry.name in (_NAMESPACES_DIR_NAME, _ARCHIVE_DIR_NAME)):
            continue
        meta = _read_session(entry.name, namespace)
        if meta and _is_expired(meta) and _expire(entry.name, namespace, meta):
            removed += 1
    return removed

_last_sweep = 0.0
def sweep_expired() -> int:
    global _last_sweep
    now = time.time()
    if now - _last_sweep < 60: return 0
    _last_sweep = now
    base = get_sessions_dir()
    removed = _sweep_dir(base, None)
    ns_root = base / _NAMESPACES_DIR_NAME
    if ns_root.exists():
        for ns_dir in ns_root.iterdir():
            if ns_dir.is_dir():
                removed += _sweep_dir(ns_dir, ns_dir.name)
    if removed: logger.info(f"Swept {removed} expired sessions (ephemeral deleted, workspaces archived)")
    return removed
