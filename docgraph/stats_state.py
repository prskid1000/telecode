"""In-memory entity/edge count cache for the per-root stats badge.

The tray UI ticks once per second; we don't want every tick to slap
the host with a /api/stats call (it's cheap but not free — one Kuzu
COUNT per node + edge table). Each path gets a TTL'd cached snapshot
plus an in-flight flag so the refresher can dedupe overlapping fetches.

  set(path, payload)        store latest counts + timestamp
  get(path)                 return cached snapshot or None
  age(path)                 seconds since last set (inf if never)
  mark_in_flight(path)      best-effort lock; returns False if already
                            being fetched
  clear_in_flight(path)     release the lock
  drop(path)                forget everything for path

Not persisted — purely a UI smoothing layer.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Any

_LOCK = threading.Lock()
_STATE: dict[str, dict[str, Any]] = {}
_INFLIGHT: set[str] = set()


def set(path: str, payload: dict[str, Any]) -> None:
    with _LOCK:
        _STATE[path] = {"payload": dict(payload), "ts": time.time()}


def get(path: str) -> dict[str, Any] | None:
    with _LOCK:
        v = _STATE.get(path)
        return dict(v["payload"]) if v else None


def age(path: str) -> float:
    with _LOCK:
        v = _STATE.get(path)
        return time.time() - v["ts"] if v else math.inf


def mark_in_flight(path: str) -> bool:
    with _LOCK:
        if path in _INFLIGHT:
            return False
        _INFLIGHT.add(path)
        return True


def clear_in_flight(path: str) -> None:
    with _LOCK:
        _INFLIGHT.discard(path)


def drop(path: str) -> None:
    with _LOCK:
        _STATE.pop(path, None)
        _INFLIGHT.discard(path)
