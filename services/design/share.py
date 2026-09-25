"""Share tokens for a project: `data/design/shares.json` (only when `design.share.enabled`).

    {"<token>": {token, project_id, role: view|comment|edit, created_at}}

A token is 32 random url-safe bytes; it is the whole credential, so it is
compared in constant time and never listed outside its own project.
"""

from __future__ import annotations

import hmac
import re
import secrets
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import config
from services.design import store

ROLES = ("view", "comment", "edit")
MAX_SHARES_PER_PROJECT = 50
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
_lock = threading.RLock()


def enabled() -> bool:
    return bool(config.get_nested("design.share.enabled", False))


def _path():
    return store.base_dir() / "shares.json"


def _load() -> Dict[str, Dict[str, Any]]:
    data = store._read_json(_path())
    return data if isinstance(data, dict) else {}


def list_shares(pid: str) -> List[Dict[str, Any]]:
    return [s for s in _load().values() if s.get("project_id") == pid]


def create_share(pid: str, role: str) -> Dict[str, Any]:
    if role not in ROLES:
        raise ValueError("role must be view, comment or edit")
    if not store.get_project(pid):
        raise LookupError("project not found")
    with _lock:
        data = _load()
        if sum(1 for s in data.values() if s.get("project_id") == pid) >= MAX_SHARES_PER_PROJECT:
            raise ValueError("too many share links for this project")
        token = secrets.token_urlsafe(32)
        rec = {"token": token, "project_id": pid, "role": role,
               "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        data[token] = rec
        store._write_json(_path(), data)
        return rec


def delete_share(pid: str, token: str) -> bool:
    with _lock:
        data = _load()
        rec = data.get(token)
        if not rec or rec.get("project_id") != pid:
            return False
        del data[token]
        store._write_json(_path(), data)
        return True


def resolve(token: str) -> Optional[Dict[str, Any]]:
    """The share record for a token, or None (also None while sharing is disabled)."""
    if not enabled() or not isinstance(token, str) or not _TOKEN_RE.match(token):
        return None
    for key, rec in _load().items():
        if hmac.compare_digest(key, token):
            return rec if store.get_project(rec.get("project_id", "")) else None
    return None
