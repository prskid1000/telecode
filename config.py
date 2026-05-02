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

_SETTINGS_PATH = Path(os.environ.get("TELECODE_SETTINGS", "settings.json")).resolve()


def _settings_dir() -> Path:
    """Directory containing the active settings.json (for resolving relative paths)."""
    return _SETTINGS_PATH.resolve().parent


def _resolve_path(path_str: str) -> str:
    """Resolve `store_path` / `logs_dir`: absolute paths unchanged; relative paths
    are anchored to the directory containing settings.json (not process cwd).

    This matches how `proxy/server.py` resolves `data/logs` for debug dumps, so
    `telecode.log` and `proxy_full_*.json` stay in the same folder when cwd differs
    (e.g. Scheduled Task, `pythonw` from another working directory).
    """
    p = Path(path_str)
    if p.is_absolute():
        return str(p)
    return str((_settings_dir() / p).resolve())


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
    keys = _split_path(dotpath)
    node = _raw
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value
    save()


def get_nested(dotpath: str, default: Any = None) -> Any:
    node = _raw
    for key in _split_path(dotpath):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def _split_path(path: str) -> list[str]:
    """Split dotpath by '.' but allow escaping dots with '\\.'."""
    parts = []
    current = []
    escaped = False
    for char in path:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ".":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


# ── Telegram ──────────────────────────────────────────────────────────────────
def telegram_token()    -> str:      return _raw["telegram"]["bot_token"]
def telegram_group_id() -> int:      return int(_raw["telegram"]["group_id"])
def allowed_user_ids()  -> set[int]:
    ids = _raw["telegram"].get("allowed_user_ids", [])
    return set(ids) if ids else set()


# ── Paths ─────────────────────────────────────────────────────────────────────
def store_path() -> str:
    return _resolve_path(_raw["paths"]["store_path"])


def logs_dir() -> str:
    return _resolve_path(_raw["paths"].get("logs_dir", "./data/logs"))


def pty_cwd() -> str:
    """PTY working directory — always home directory."""
    return str(Path.home())


# ── Streaming ─────────────────────────────────────────────────────────────────
def stream_interval() -> float: return float(_raw["streaming"]["interval_sec"])
def max_msg_length()  -> int:   return int(_raw["streaming"]["max_message_length"])
def idle_timeout()    -> int:   return int(_raw["streaming"]["idle_timeout_sec"])


def _streaming_cfg() -> dict[str, Any]:
    return _raw.get("streaming", {}) or {}


def stream_idle_sec() -> float:
    """Seconds of PTY silence before treating a streaming burst as complete."""
    return float(_streaming_cfg().get("idle_sec", 2.0))


def stream_max_wait_sec() -> float:
    """Upper bound on how long to buffer a continuous stream before forcing a flush."""
    return float(_streaming_cfg().get("max_wait_sec", 5.0))


def tool_stream_idle_sec(key: str) -> float:
    """Per-tool override for PTY idle flush; falls back to the global default."""
    override = tool_cfg(key).get("streaming", {}).get("idle_sec")
    return float(override) if override is not None else stream_idle_sec()


def tool_stream_max_wait_sec(key: str) -> float:
    """Per-tool override for PTY max-wait flush; falls back to the global default."""
    override = tool_cfg(key).get("streaming", {}).get("max_wait_sec")
    return float(override) if override is not None else stream_max_wait_sec()


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


# ── Computer control ────────────────────────────────────────────────────────
def _computer_cfg() -> dict[str, Any]:
    return tool_cfg("computer")

def computer_api_base_url() -> str:
    return _computer_cfg().get("api", {}).get("base_url", "http://localhost:1234/v1")

def computer_api_key() -> str:
    return _computer_cfg().get("api", {}).get("api_key", "")

def computer_model() -> str:
    return _computer_cfg().get("api", {}).get("model", "")

def computer_api_format() -> str:
    fmt = _computer_cfg().get("api", {}).get("format", "openai").lower()
    return fmt if fmt in ("openai", "anthropic") else "openai"

def computer_capture_interval() -> float:
    return float(_computer_cfg().get("capture_interval", 3))

def computer_system_prompt() -> str:
    return _computer_cfg().get("system_prompt", "")

def computer_max_history() -> int:
    return int(_computer_cfg().get("max_history", 20))


# ── MCP server ────────────────────────────────────────────────────────────────
def mcp_server_enabled() -> bool: return bool(get_nested("mcp_server.enabled", False))
def mcp_server_host()    -> str:  return get_nested("mcp_server.host", "127.0.0.1")
def mcp_server_port()    -> int:  return int(get_nested("mcp_server.port", 1236))
def mcp_server_tts_url() -> str:  return get_nested("mcp_server.tts_url", "http://127.0.0.1:6500")
def mcp_server_stt_url() -> str:  return get_nested("mcp_server.stt_url", "http://127.0.0.1:6600")


# ── Heartbeat scheduler ──────────────────────────────────────────────────────
def heartbeat_enabled()                  -> bool: return bool(get_nested("heartbeat.enabled", False))
def heartbeat_tick_seconds()             -> int:  return int(get_nested("heartbeat.tick_seconds", 60))
def heartbeat_ephemeral_ttl_seconds()    -> int:  return int(get_nested("heartbeat.ephemeral_ttl_seconds", 3600))
def heartbeat_max_concurrent_fires()     -> int:  return int(get_nested("heartbeat.max_concurrent_fires", 2))
def heartbeat_min_fire_gap_seconds()     -> int:  return int(get_nested("heartbeat.min_fire_gap_seconds", 60))


# ── Proxy ─────────────────────────────────────────────────────────────────────
def proxy_enabled()      -> bool: return bool(get_nested("proxy.enabled", False))
def proxy_port()         -> int:  return int(get_nested("proxy.port", 1235))
def proxy_upstream_url() -> str:  return get_nested("proxy.upstream_url", "http://localhost:1234")


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
