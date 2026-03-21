"""
Concrete CLI backends — startup_cmd and flags come from settings.json.

Backend  Icon  Tool
───────  ────  ───────────────────────────────────
claude   🟣    Anthropic Claude Code
codex    🟢    OpenAI Codex CLI
shell    🐚    Raw terminal
"""
from .base import CLIBackend, BackendInfo, BackendParams
import config

BACKEND_ICONS: dict[str, str] = {
    "claude": "🟣",
    "codex":  "🟢",
    "shell":  "🐚",
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
            key="shell", name="Terminal",
            description="Direct shell access",
            base_cmd=config.tool_startup_cmd("shell"),
            default_flags=[],
        )

    def startup_message(self) -> str:
        return (
            "🐚 <b>Terminal</b> — Ready\n\n"
            "Type any command to run it."
        )
