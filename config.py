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


# Standard effort presets. Auto-seeded into reasoning_effort_map on load so
# every value a client can send hits a real entry. Users edit values freely
# but the tray blocks deletion of these keys.
#
# The list is the union of three sources, not one vocabulary:
#   Claude Code's effort slider  low | medium | high | xhigh | max
#   OpenAI Responses API         minimal | low | medium | high | xhigh | none
#   Anthropic `thinking.type`    disabled -> none, adaptive -> adaptive
#                                (resolved in translate.py, never sent as an
#                                 effort string by the client itself)
# So `minimal` is OpenAI-only and `adaptive`/`none` are proxy-internal —
# none of them are droppable just because Claude Code's slider lacks them.
#
# Order is the tray's render + sort order (`_EFF_ORDER`), so it must read as
# ascending effort. `adaptive` is last because it is not a level at all: it
# hands the decision to the model.
STANDARD_EFFORT_KEYS: tuple[str, ...] = (
    "none", "minimal", "low", "medium", "high", "xhigh", "max", "adaptive",
)

# Ladder is capped at 4096 at `max`. Note this makes `max` a real cap rather
# than the "key absent -> unlimited" case it used to be; nothing here is
# unlimited any more. Remove a key entirely to get unlimited back.
_STANDARD_EFFORT_DEFAULTS: dict[str, dict[str, Any]] = {
    "none":     {"thinking_budget_tokens": 1},
    "minimal":  {"thinking_budget_tokens": 256},
    "low":      {"thinking_budget_tokens": 512},
    "medium":   {"thinking_budget_tokens": 1024},
    "high":     {"thinking_budget_tokens": 2048},
    "xhigh":    {"thinking_budget_tokens": 3072},
    "max":      {"thinking_budget_tokens": 4096},
    "adaptive": {"thinking_budget_tokens": 2048},
}


# Per-model twin of _STANDARD_EFFORT_DEFAULTS, for the *template* string
# rather than the token budget. Keys are the effort levels above; values are
# what the model's chat template expects. low/medium/high is the one trio
# valid across both families we care about — GPT-OSS takes it verbatim, and
# Qwen 3.8 aliases high -> xhigh before its raise check, which is why `xhigh`
# and `max` map to "high" rather than to "xhigh": the alias is portable, the
# literal is not (GPT-OSS rejects "xhigh").
#
# Consequence worth knowing when tuning a Qwen 3.8 model: high/xhigh/max all
# land on the template's top tier, so the top three levels are one level in
# practice. Override per model if you want them to differ. Qwen 3.8's tiers
# are prompt text, not budgets — `medium` injects no instruction at all.
_STANDARD_EFFORT_TEMPLATE_DEFAULTS: dict[str, str] = {
    "none":     "low",
    "minimal":  "low",
    "low":      "low",
    "medium":   "medium",
    "high":     "high",
    "xhigh":    "high",
    "max":      "high",
    "adaptive": "medium",
}


def _ensure_model_effort_maps(data: dict[str, Any]) -> bool:
    """Seed each model's inference_defaults.reasoning_effort.

    Same contract as _ensure_standard_effort_map: fill only what is missing,
    never overwrite. Without this the tray renders empty boxes for a mapping
    that is actually in force via _INFERENCE_DEFAULTS — the "looks unset but
    isn't" trap that makes a settings UI untrustworthy.
    """
    models = data.get("llamacpp", {}).get("models")
    if not isinstance(models, dict):
        return False
    changed = False
    for _name, model in models.items():
        if not isinstance(model, dict):
            continue
        inf = model.setdefault("inference_defaults", {})
        if not isinstance(inf, dict):
            continue
        eff = inf.get("reasoning_effort")
        if not isinstance(eff, dict):
            eff = {}
            inf["reasoning_effort"] = eff
            changed = True
        if "template_key" not in eff:
            eff["template_key"] = "reasoning_effort"
            changed = True
        if eff.pop("allowed", None) is not None:
            changed = True   # superseded: values are per-model and visible now
        mapping = eff.get("map")
        if not isinstance(mapping, dict):
            mapping = {}
            eff["map"] = mapping
            changed = True
        for k, v in _STANDARD_EFFORT_TEMPLATE_DEFAULTS.items():
            if k not in mapping:
                mapping[k] = v
                changed = True
    return changed


def _ensure_standard_effort_map(data: dict[str, Any]) -> bool:
    """Seed any missing STANDARD_EFFORT_KEYS into the reasoning_effort_map.

    Returns True if the settings dict was mutated (caller should persist).
    Existing entries are never overwritten — only missing keys are filled.
    """
    inf = data.get("llamacpp", {}).get("inference")
    if not isinstance(inf, dict):
        return False
    mapping = inf.get("reasoning_effort_map")
    if mapping is None:
        mapping = {}
        inf["reasoning_effort_map"] = mapping
    if not isinstance(mapping, dict):
        return False
    changed = False
    for k in STANDARD_EFFORT_KEYS:
        if k not in mapping:
            mapping[k] = dict(_STANDARD_EFFORT_DEFAULTS[k])
            changed = True
    return changed


_raw: dict[str, Any] = _load()
if _ensure_standard_effort_map(_raw) | _ensure_model_effort_maps(_raw):
    _save(_raw)


def reload() -> None:
    global _raw
    _raw = _load()
    if _ensure_standard_effort_map(_raw) | _ensure_model_effort_maps(_raw):
        _save(_raw)


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
def telegram_auto_start()   -> bool: return bool(_raw["telegram"].get("auto_start",   True))
def telegram_auto_restart() -> bool: return bool(_raw["telegram"].get("auto_restart", True))


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
# Only the connection target. VoxType controls which STT model is loaded.
def stt_enabled()  -> bool: return bool(_raw["voice"]["stt"]["enabled"])
def stt_base_url() -> str:  return _raw["voice"]["stt"]["base_url"]


# ── TTS ───────────────────────────────────────────────────────────────────────
# Only the connection target. VoxType controls which TTS model + voice is used.
def tts_enabled()  -> bool:
    return bool(((_raw.get("voice") or {}).get("tts") or {}).get("enabled", False))
def tts_base_url() -> str:
    return str(((_raw.get("voice") or {}).get("tts") or {}).get(
        "base_url", "http://127.0.0.1:6600/v1"))


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
def mcp_server_tts_url() -> str:  return get_nested("mcp_server.tts_url", "http://127.0.0.1:6600")
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
