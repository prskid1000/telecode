"""
Concrete CLI backends — startup_cmd and flags come from settings.json.

Backend     Icon  Tool
──────────  ────  ───────────────────────────────────
claude      🟣    Anthropic Claude Code
codex       🟢    OpenAI Codex CLI
shell       🐚    Bash terminal
powershell  🔷    PowerShell terminal
"""
from .base import CLIBackend, BackendInfo, BackendParams
import config

BACKEND_ICONS: dict[str, str] = {
    "claude":     "🟣",
    "codex":      "🟢",
    "shell":      "🐚",
    "powershell": "🔷",
    "screen":     "📸",
}


class ClaudeCodeBackend(CLIBackend):
    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            key="claude", name="Claude Code",
            description="Anthropic's AI coding agent",
            base_cmd=config.tool_startup_cmd("claude"),
            default_flags=config.tool_flags("claude"),
        )

    def build_launch_cmd(self, params: BackendParams) -> list[str]:
        cmd = config.tool_startup_cmd("claude") + params.extra_flags
        resume_id = params.session_args.get("resume_id", "")
        if resume_id:
            cmd += ["--resume", resume_id]
        return cmd

    def startup_message(self) -> str:
        return (
            "🟣 <b>Claude Code</b> — Ready\n\n"
            "Just type your message below.\n\n"
            "⚙️ <code>/settings tool claude</code> to configure"
        )


class CodexBackend(CLIBackend):
    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            key="codex", name="Codex CLI",
            description="OpenAI code generation &amp; editing",
            base_cmd=config.tool_startup_cmd("codex"),
            default_flags=config.tool_flags("codex"),
        )

    def startup_message(self) -> str:
        return (
            "🟢 <b>Codex CLI</b> — Ready\n\n"
            "Just type your message below.\n\n"
            "⚙️ <code>/settings tool codex</code> to configure"
        )


class ShellBackend(CLIBackend):
    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            key="shell", name="Bash",
            description="Bash terminal",
            base_cmd=config.tool_startup_cmd("shell"),
            default_flags=[],
        )

    def startup_message(self) -> str:
        return (
            "🐚 <b>Bash</b> — Ready\n\n"
            "Type any command to run it."
        )


class PowerShellBackend(CLIBackend):
    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            key="powershell", name="PowerShell",
            description="PowerShell terminal",
            base_cmd=config.tool_startup_cmd("powershell"),
            default_flags=[],
        )

    def startup_message(self) -> str:
        return (
            "🔷 <b>PowerShell</b> — Ready\n\n"
            "Type any command to run it."
        )


class ScreenBackend(CLIBackend):
    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            key="screen", name="Screen Capture",
            description="Capture and stream a window",
            base_cmd=[],
            default_flags=[],
        )

    def build_launch_cmd(self, params: BackendParams) -> list[str]:
        return []  # not used — ScreenCapture replaces PTYProcess

    def startup_message(self) -> str:
        return "📸 <b>Screen Capture</b> — Streaming"
