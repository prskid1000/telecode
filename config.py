"""
Single source of truth — everything loaded from settings.json.

No .env file. No os.getenv() calls anywhere else in the project.
All modules import accessors from here only.

Hot-reload: call config.reload() at runtime (e.g. from /settings reload).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_SETTINGS_PATH = Path(os.environ.get("TELECODE_SETTINGS", "settings.json"))


def _load() -> dict[str, Any]:
    if not _SETTINGS_PATH.exists():
        raise FileNotFoundError(
            f"\n\nsettings.json not found at: {_SETTINGS_PATH.resolve()}\n"
            "→ Edit settings.json and fill in your values.\n"
        )
    with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict[str, Any]) -> None:
    with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


_raw: dict[str, Any] = _load()


def reload() -> None:
    global _raw
    _raw = _load()


def save() -> None:
    _save(_raw)


def raw() -> dict[str, Any]:
    return _raw


def set_nested(dotpath: str, value: Any) -> None:
    keys = dotpath.split(".")
    node = _raw
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value
    save()


def get_nested(dotpath: str, default: Any = None) -> Any:
    node = _raw
    for key in dotpath.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


# ── Telegram ──────────────────────────────────────────────────────────────────
def telegram_token()    -> str:      return _raw["telegram"]["bot_token"]
def telegram_group_id() -> int:      return int(_raw["telegram"]["group_id"])
def allowed_user_ids()  -> set[int]:
    ids = _raw["telegram"].get("allowed_user_ids", [])
    return set(ids) if ids else set()


# ── Paths ─────────────────────────────────────────────────────────────────────
def sessions_dir() -> str: return _raw["paths"]["sessions_dir"]
def store_path() -> str: return _raw["paths"]["store_path"]
def logs_dir()     -> str: return _raw["paths"].get("logs_dir", "./data/logs")


def pty_cwd() -> str:
    """PTY working directory — always home directory."""
    return str(Path.home())


# ── Streaming ─────────────────────────────────────────────────────────────────
def stream_interval() -> float: return float(_raw["streaming"]["interval_sec"])
def max_msg_length()  -> int:   return int(_raw["streaming"]["max_message_length"])
def idle_timeout()    -> int:   return int(_raw["streaming"]["idle_timeout_sec"])


# ── STT ───────────────────────────────────────────────────────────────────────
def stt_enabled()  -> bool: return bool(_raw["voice"]["stt"]["enabled"])
def stt_base_url() -> str:  return _raw["voice"]["stt"]["base_url"]
def stt_model()    -> str:  return _raw["voice"]["stt"]["model"]


# ── Capture intervals ────────────────────────────────────────────────────────
def image_interval() -> float:
    """Seconds between image capture sends."""
    return float(_raw.get("capture", {}).get("image_interval", 15))

def video_interval() -> int:
    """Seconds per video chunk (= recording length per segment)."""
    return int(_raw.get("capture", {}).get("video_interval", 60))


# ── Tools ─────────────────────────────────────────────────────────────────────
def tool_cfg(key: str) -> dict[str, Any]:
    return _raw.get("tools", {}).get(key, {})

def tool_name(key: str) -> str:
    return tool_cfg(key).get("name", "") or key.replace("-", " ").title()

def tool_icon(key: str) -> str:
    return tool_cfg(key).get("icon", "") or "🔧"

def tool_startup_cmd(key: str) -> list[str]:
    return list(tool_cfg(key).get("startup_cmd", [key]))

def tool_flags(key: str) -> list[str]:
    return list(tool_cfg(key).get("flags", []))

def tool_env(key: str) -> dict[str, str]:
    return {k: v for k, v in tool_cfg(key).get("env", {}).items() if v}

def tool_session_args(key: str) -> dict[str, str]:
    return dict(tool_cfg(key).get("session", {}))

def all_tool_keys() -> list[str]:
    return list(_raw.get("tools", {}).keys())


# ── Validation ────────────────────────────────────────────────────────────────
def validate() -> list[str]:
    w: list[str] = []
    if not telegram_token() or "YOUR_BOT_TOKEN" in telegram_token():
        w.append("⚠️  telegram.bot_token is not set")
    if telegram_group_id() == -1001234567890:
        w.append("⚠️  telegram.group_id is still the placeholder — set your actual group ID")
    if not allowed_user_ids():
        w.append("⚠️  telegram.allowed_user_ids is empty — anyone can use this bot!")
    for key in all_tool_keys():
        if not tool_startup_cmd(key):
            w.append(f"⚠️  tools.{key}.startup_cmd is empty")
    return w
