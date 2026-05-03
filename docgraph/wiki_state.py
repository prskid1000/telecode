"""Per-path wiki run state. Persisted to data/docgraph-wiki-state.json.

Schema mirrors `index_state.py`:
    { "<path>": {"last_run": <epoch>, "last_status": "ok|failed|cancelled|running",
                  "last_was_full": bool} }

`last_was_full` corresponds to `docgraph wiki --force` (rebuild every page
from scratch) vs the resumable default that skips already-rendered pages.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

import config as app_config

_LOCK = threading.Lock()


def _path() -> str:
    return os.path.join(os.path.dirname(app_config.logs_dir()), "docgraph-wiki-state.json")


def load() -> dict[str, dict[str, Any]]:
    p = _path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    p = _path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, p)
    except OSError:
        pass


def update(path: str, *, status: str, was_full: bool, ts: float | None = None) -> None:
    with _LOCK:
        data = load()
        data[path] = {
            "last_run":      ts if ts is not None else time.time(),
            "last_status":   status,
            "last_was_full": was_full,
        }
        _save(data)


def mark_running(path: str, *, was_full: bool) -> None:
    with _LOCK:
        data = load()
        prev = data.get(path, {})
        data[path] = {
            "last_run":      time.time(),
            "last_status":   "running",
            "last_was_full": was_full,
            "_prev_status":  prev.get("last_status", "idle"),
        }
        _save(data)


def get(path: str) -> dict[str, Any] | None:
    return load().get(path)
