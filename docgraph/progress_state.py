"""In-memory live progress state for in-flight docgraph index/wiki runs.

Populated by `_sse_progress_tee` on each SSE `index_progress` /
`wiki_progress` event. Read by the tray UI to draw a live progress bar
on the per-root row. Cleared by the runners once the operation finishes.

Not persisted — this is purely "what's happening right now". The
`index_state` / `wiki_state` modules cover the recorded last-run history.
"""
from __future__ import annotations

import threading
import time
from typing import Any

_LOCK = threading.Lock()
_STATE: dict[tuple[str, str], dict[str, Any]] = {}


def set(path: str, kind: str, *, phase: str, current: int, total: int,
        module: str = "") -> None:
    with _LOCK:
        _STATE[(path, kind)] = {
            "phase":   phase,
            "current": int(current),
            "total":   int(total),
            "module":  module,
            "ts":      time.time(),
        }


def get(path: str, kind: str) -> dict[str, Any] | None:
    with _LOCK:
        v = _STATE.get((path, kind))
        return dict(v) if v is not None else None


def clear(path: str, kind: str) -> None:
    with _LOCK:
        _STATE.pop((path, kind), None)
