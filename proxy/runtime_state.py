"""Persisted runtime overrides — survives restarts.

What lives here:
  - per-managed-tool enable/disable (UI toggles in the panel)
  - per-MCP-tool enable/disable
  - any other "I toggled this off, it should stay off" runtime preferences

Stored in `<settings_dir>/data/runtime-overrides.json` next to the bot's
existing data dir. Best-effort — the in-memory dicts are the source of
truth during a run; the file is just a snapshot.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("telecode.runtime_state")
_LOCK = threading.Lock()
_DEFAULT: dict[str, Any] = {
    "managed_tools": {},   # name → bool (True/False; missing = True)
    "mcp_tools":     {},
}


def _path() -> Path:
    settings_path = Path(os.environ.get("TELECODE_SETTINGS", "settings.json")).resolve()
    return settings_path.parent / "data" / "runtime-overrides.json"


def load() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return dict(_DEFAULT)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        out = dict(_DEFAULT)
        for k in _DEFAULT:
            if k in data and isinstance(data[k], dict):
                out[k] = {str(kk): bool(vv) for kk, vv in data[k].items()}
        return out
    except Exception as exc:
        log.warning("could not read runtime-overrides.json: %s", exc)
        return dict(_DEFAULT)


def _save(data: dict[str, Any]) -> None:
    p = _path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:
        log.debug("could not write runtime-overrides.json: %s", exc)


def set_tool(category: str, name: str, enabled: bool) -> None:
    """Persist one tool toggle. category ∈ {'managed_tools', 'mcp_tools'}."""
    with _LOCK:
        data = load()
        data.setdefault(category, {})[name] = bool(enabled)
        _save(data)


def get_all() -> dict[str, dict[str, bool]]:
    return load()


def is_managed_enabled(name: str) -> bool:
    """Consulted by proxy/server.py before injecting a managed tool — the
    user's runtime toggle takes precedence over the profile's static list.
    Default True if no override has ever been set."""
    return load().get("managed_tools", {}).get(name, True)


def is_mcp_tool_enabled(name: str) -> bool:
    return load().get("mcp_tools", {}).get(name, True)
