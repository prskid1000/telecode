"""Persisted supervisor state — survives restarts.

Stores which model was most recently loaded so the next telecode launch
can pick up where the last one left off (eagerly preload it, or use it
as the implicit default if a request omits the `model` field).

Stored at `<settings_dir>/data/llama-state.json` so it travels with the
project rather than the user home dir.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import config as app_config

log = logging.getLogger("telecode.llamacpp.state")


def _path() -> Path:
    settings_path = Path(os.environ.get("TELECODE_SETTINGS", "settings.json")).resolve()
    return settings_path.parent / "data" / "llama-state.json"


def load() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        log.warning("could not read llama-state.json: %s", exc)
        return {}


def save(active_model: str) -> None:
    """Persist the currently-active model. Best-effort — logs and moves on."""
    p = _path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {"active_model": active_model, "ts": time.time()}
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:
        log.debug("could not write llama-state.json: %s", exc)


def last_active_model() -> str:
    return str(load().get("active_model", "") or "")
