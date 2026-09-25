"""Persistent last-fire tracking for heartbeat entries.

State file: data/heartbeat-state.json — atomic writes via tempfile + os.replace.

Key format: "<agent_id>:<entry_name>" → {last_run, last_status, last_task_id,
last_finished, first_seen}. `first_seen` anchors a never-fired entry's first
cron slot (B4); it is dropped once the entry has fired (last_run takes over).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import config

logger = logging.getLogger("telecode.services.heartbeat.state")

_lock = threading.Lock()


def _state_path() -> Path:
    return Path(config._settings_dir()) / "data" / "heartbeat-state.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read() -> Dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"Could not read {p}: {exc}; starting fresh")
        return {}


def _write(state: Dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(state, indent=2, default=str).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".hb-state.", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encoded)
        os.replace(tmp, p)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _key(agent_id: str, entry_name: str) -> str:
    return f"{agent_id}:{entry_name}"


def get(agent_id: str, entry_name: str) -> Dict[str, Any]:
    with _lock:
        return dict(_read().get(_key(agent_id, entry_name), {}))


def mark_fired(agent_id: str, entry_name: str, task_id: Optional[str] = None) -> None:
    with _lock:
        state = _read()
        state[_key(agent_id, entry_name)] = {
            "last_run": _now_iso(),
            "last_status": "running",
            "last_task_id": task_id,
        }
        _write(state)


def mark_seen(agent_id: str, entry_name: str) -> str:
    """Record when the scheduler first saw a never-fired entry. Idempotent;
    returns the stored first_seen."""
    with _lock:
        state = _read()
        cur = state.get(_key(agent_id, entry_name), {})
        if not cur.get("first_seen"):
            cur["first_seen"] = _now_iso()
            state[_key(agent_id, entry_name)] = cur
            _write(state)
        return cur["first_seen"]


def reconcile_interrupted(is_live) -> int:
    """Startup pass (B6): entries left "running" whose task is not alive
    (`is_live(task_id)` False) become "interrupted". Returns count."""
    with _lock:
        state = _read()
        n = 0
        for cur in state.values():
            if isinstance(cur, dict) and cur.get("last_status") == "running" \
                    and not is_live(cur.get("last_task_id")):
                cur["last_status"] = "interrupted"
                cur["last_finished"] = _now_iso()
                n += 1
        if n:
            _write(state)
        return n


def mark_finished(agent_id: str, entry_name: str, status: str, task_id: Optional[str] = None) -> None:
    with _lock:
        state = _read()
        cur = state.get(_key(agent_id, entry_name), {})
        cur.update({
            "last_status": status,
            "last_finished": _now_iso(),
        })
        if task_id:
            cur["last_task_id"] = task_id
        state[_key(agent_id, entry_name)] = cur
        _write(state)


def prune_orphans(known_keys: set) -> int:
    """Remove state entries whose key isn't in known_keys. Returns count removed."""
    with _lock:
        state = _read()
        removed = [k for k in state if k not in known_keys]
        if not removed:
            return 0
        for k in removed:
            del state[k]
        _write(state)
        return len(removed)
