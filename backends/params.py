"""Tool params loaded entirely from settings.json via config module."""
from __future__ import annotations
from html import escape as _esc
import config
from backends.base import BackendParams


def load_params(backend_key: str) -> BackendParams:
    """Build BackendParams for a tool from settings.json."""
    return BackendParams(
        extra_flags=config.tool_flags(backend_key),
        env=config.tool_env(backend_key),
        session_args=config.tool_session_args(backend_key),
        idle_sec=config.tool_stream_idle_sec(backend_key),
        max_wait_sec=config.tool_stream_max_wait_sec(backend_key),
    )


def save_tool_setting(backend_key: str, field: str, value) -> None:
    config.set_nested(f"tools.{backend_key}.{field}", value)


def set_tool_env_var(backend_key: str, var: str, value: str) -> None:
    config.set_nested(f"tools.{backend_key}.env.{var}", value)


def set_tool_flag(backend_key: str, flags: list[str]) -> None:
    config.set_nested(f"tools.{backend_key}.flags", flags)


def render_all_tools() -> str:
    """Render all tool configs as HTML for Telegram."""
    lines = ["<b>⚙️ Tool Configuration</b>\n"]
    for key in config.all_tool_keys():
        cmd_str   = " ".join(config.tool_startup_cmd(key))
        flags_str = " ".join(config.tool_flags(key)) or "(none)"
        lines.append(f"🔧 <b>{_esc(key)}</b>")
        lines.append(f"  Cmd:   <code>{_esc(cmd_str)}</code>")
        lines.append(f"  Flags: <code>{_esc(flags_str)}</code>")
        for k, v in config.tool_env(key).items():
            masked = (v[:4] + "…") if len(v) > 8 else "***"
            lines.append(f"  <code>{_esc(k)}</code> = <code>{_esc(masked)}</code>")
        for k, v in config.tool_session_args(key).items():
            lines.append(f"  Session <code>{_esc(k)}</code>: <code>{_esc(v or '(unset)')}</code>")
        lines.append("")
    return "\n".join(lines)
