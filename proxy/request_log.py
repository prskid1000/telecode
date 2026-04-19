"""In-memory ring buffer of recent proxy requests, plus optional disk dumps.

Public API used by server.py:
    new_request(method, path, client_model="", inbound_protocol="") -> rid
    set_request_preview(rid, body_dict)
    finish(rid, status, error="")

The tray log viewer reads via `snapshot()` — returns a list of RequestEntry
dicts sorted newest-first. Entries are capped at MAX_ENTRIES (oldest evicted).

When `proxy.debug` is true, each finished request is also written to
`data/logs/requests/req_<timestamp>_<rid>.json` for post-hoc inspection.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

log = logging.getLogger("telecode.proxy.request_log")

MAX_ENTRIES = 200

_entries: deque[dict[str, Any]] = deque(maxlen=MAX_ENTRIES)
_by_rid: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _dumps_dir() -> Path:
    # resolve relative to settings.json directory to match telecode.log behaviour
    import os
    from config import _settings_dir  # type: ignore[attr-defined]
    try:
        base = _settings_dir()
    except Exception:
        base = Path(os.getcwd())
    d = base / "data" / "logs" / "requests"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def _debug_enabled() -> bool:
    try:
        from proxy import config as pc
        return bool(pc.debug())
    except Exception:
        return False


def new_request(method: str, path: str, client_model: str = "",
                inbound_protocol: str = "") -> str:
    rid = uuid.uuid4().hex[:12]
    entry = {
        "rid":               rid,
        "method":            method,
        "path":              path,
        "client_model":      client_model,
        "inbound_protocol":  inbound_protocol,
        "started_at":        time.time(),
        "finished_at":       None,
        "duration_ms":       None,
        "status":            None,
        "error":             "",
        "request_preview":   None,
    }
    with _lock:
        _entries.appendleft(entry)
        _by_rid[rid] = entry
        # trim the by_rid map in sync with the deque
        if len(_by_rid) > MAX_ENTRIES * 2:
            live = {e["rid"] for e in _entries}
            for k in list(_by_rid.keys()):
                if k not in live:
                    _by_rid.pop(k, None)
    return rid


def set_request_preview(rid: str, body: Any) -> None:
    with _lock:
        e = _by_rid.get(rid)
        if e is not None:
            e["request_preview"] = body


def finish(rid: str, status: int, error: str = "") -> None:
    with _lock:
        e = _by_rid.get(rid)
        if e is None:
            return
        now = time.time()
        e["finished_at"] = now
        e["duration_ms"] = int((now - e["started_at"]) * 1000)
        e["status"] = status
        e["error"] = error or ""
        snapshot_entry = dict(e)

    if _debug_enabled():
        try:
            ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(snapshot_entry["started_at"]))
            path = _dumps_dir() / f"req_{ts}_{rid}.json"
            path.write_text(json.dumps(snapshot_entry, indent=2, default=str),
                            encoding="utf-8")
        except Exception as exc:
            log.debug("dump write failed: %s", exc)


def snapshot() -> list[dict[str, Any]]:
    """Return a newest-first list copy of the ring buffer."""
    with _lock:
        return [dict(e) for e in _entries]


def get(rid: str) -> dict[str, Any] | None:
    with _lock:
        e = _by_rid.get(rid)
        return dict(e) if e else None


def clear() -> None:
    with _lock:
        _entries.clear()
        _by_rid.clear()
