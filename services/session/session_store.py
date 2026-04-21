"""Generic filesystem-backed task sessions. Ported from pythonmagic."""

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
    if session_id == _NAMESPACES_DIR_NAME:
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

def _is_expired(meta: Dict[str, Any]) -> bool:
    now = datetime.now(timezone.utc)
    abs_ttl = meta.get("absolute_ttl_seconds")
    if abs_ttl:
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
            "session_idle_timeout_seconds": session_idle_timeout_seconds or DEFAULT_SESSION_IDLE_TIMEOUT_SECONDS,
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
        if meta: return meta
        return create(session_id, data, session_idle_timeout_seconds, absolute_ttl_seconds, namespace=namespace)

def get(session_id: str, namespace: Optional[str] = None) -> Optional[Dict[str, Any]]:
    meta = _read_session(session_id, namespace)
    if not meta: return None
    if _is_expired(meta):
        delete(session_id, namespace)
        return None
    return meta

def list_all(namespace: Optional[str] = None) -> List[Dict[str, Any]]:
    sweep_expired()
    out = []
    base = get_sessions_dir()
    if namespace is None:
        if not base.exists(): return []
        for entry in base.iterdir():
            if entry.is_dir() and entry.name != _NAMESPACES_DIR_NAME:
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
        if not entry.is_dir() or (namespace is None and entry.name == _NAMESPACES_DIR_NAME):
            continue
        meta = _read_session(entry.name, namespace)
        if meta and _is_expired(meta):
            delete(entry.name, namespace)
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
    if removed: logger.info(f"Swept {removed} expired sessions")
    return removed
