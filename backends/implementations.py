"""
Concrete CLI backends — all data-driven from settings.json.

Special backends (screen, video) that don't use a PTY still have their own classes.
Everything else is built automatically by GenericCLIBackend from the tools config.
"""
from .base import CLIBackend, BackendInfo, BackendParams
import config

# session_args keys that map to CLI flags (e.g. resume_id -> --resume <value>)
SESSION_ARG_FLAGS: dict[str, str] = {
    "resume_id": "--resume",
}

# Keys that are non-PTY (screen capture, video, computer control) — handled separately.
NON_PTY_KEYS = {"screen", "video", "computer"}


class GenericCLIBackend(CLIBackend):
    """Data-driven backend — everything comes from settings.json."""

    def __init__(self, key: str):
        self._key = key

    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            key=self._key,
            name=config.tool_name(self._key),
            description=config.tool_name(self._key),
            base_cmd=config.tool_startup_cmd(self._key),
            default_flags=config.tool_flags(self._key),
        )

    def build_launch_cmd(self, params: BackendParams) -> list[str]:
        cmd = config.tool_startup_cmd(self._key) + params.extra_flags
        for arg_key, flag in SESSION_ARG_FLAGS.items():
            val = params.session_args.get(arg_key, "")
            if val:
                cmd += [flag, val]
        return cmd

    def startup_message(self) -> str:
        icon = config.tool_icon(self._key)
        name = config.tool_name(self._key)
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


class ComputerBackend(CLIBackend):
    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            key="computer", name="Computer Control",
            description="Control a window via vision LLM",
            base_cmd=[], default_flags=[],
        )

    def build_launch_cmd(self, params: BackendParams) -> list[str]:
        return []

    def startup_message(self) -> str:
        return "🖥️ <b>Computer Control</b> — Ready"
