"""
Concrete CLI backends — most are data-driven from settings.json.

Special backends (screen, video) that don't use a PTY still have their own classes.
Everything else is built automatically by GenericCLIBackend from the tools config.
"""
from .base import CLIBackend, BackendInfo, BackendParams
import config

BACKEND_ICONS: dict[str, str] = {
    "claude":       "🟣",
    "claude-local": "🟤",
    "codex":        "🟢",
    "codex-local":  "🟠",
    "shell":        "🐚",
    "powershell":   "🔷",
    "screen":       "📸",
    "video":        "🎬",
}

# Display names for known backends; unknown keys get title-cased automatically.
BACKEND_NAMES: dict[str, str] = {
    "claude":       "Claude Code",
    "claude-local": "Claude Code (Local)",
    "codex":        "Codex CLI",
    "codex-local":  "Codex CLI (Local)",
    "shell":        "Bash",
    "powershell":   "PowerShell",
    "screen":       "Screen Image Capture",
    "video":        "Screen Video Capture",
}

# session_args keys that map to CLI flags (e.g. resume_id -> --resume <value>)
SESSION_ARG_FLAGS: dict[str, str] = {
    "resume_id": "--resume",
}

# Keys that are non-PTY (screen capture, video) — handled separately.
NON_PTY_KEYS = {"screen", "video"}


def _icon(key: str) -> str:
    return BACKEND_ICONS.get(key, "🔧")


def _name(key: str) -> str:
    return BACKEND_NAMES.get(key, key.replace("-", " ").title())


class GenericCLIBackend(CLIBackend):
    """Data-driven backend — everything comes from settings.json."""

    def __init__(self, key: str):
        self._key = key

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            key=self._key,
            name=_name(self._key),
            description=_name(self._key),
            base_cmd=config.tool_startup_cmd(self._key),
            default_flags=config.tool_flags(self._key),
        )

    def build_launch_cmd(self, params: BackendParams) -> list[str]:
        cmd = config.tool_startup_cmd(self._key) + params.extra_flags
        # Map session_args to CLI flags (e.g. resume_id -> --resume <val>)
        for arg_key, flag in SESSION_ARG_FLAGS.items():
            val = params.session_args.get(arg_key, "")
            if val:
                cmd += [flag, val]
        return cmd

    def startup_message(self) -> str:
        icon = _icon(self._key)
        name = _name(self._key)
        return (
            f"{icon} <b>{name}</b> — Ready\n\n"
            "Just type your message below.\n\n"
            f"⚙️ <code>/settings tool {self._key}</code> to configure"
        )


class ScreenBackend(CLIBackend):
    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            key="screen", name="Screen Image Capture",
            description="Capture and stream window images",
            base_cmd=[], default_flags=[],
        )

    def build_launch_cmd(self, params: BackendParams) -> list[str]:
        return []

    def startup_message(self) -> str:
        return "📸 <b>Screen Image Capture</b> — Streaming"


class VideoBackend(CLIBackend):
    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            key="video", name="Screen Video Capture",
            description="Record a 1-minute video of a window",
            base_cmd=[], default_flags=[],
        )

    def build_launch_cmd(self, params: BackendParams) -> list[str]:
        return []

    def startup_message(self) -> str:
        return "🎬 <b>Screen Video Capture</b> — Recording"
