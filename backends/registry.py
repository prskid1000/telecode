from .base import CLIBackend
from .implementations import (
    ClaudeCodeBackend, CodexBackend, ShellBackend, PowerShellBackend, ScreenBackend,
)

_REGISTRY: dict[str, CLIBackend] = {
    b.info.key: b
    for b in [
        ClaudeCodeBackend(),
        CodexBackend(),
        ShellBackend(),
        PowerShellBackend(),
        ScreenBackend(),
    ]
}

def get_backend(key: str) -> CLIBackend | None:
    return _REGISTRY.get(key)

def all_backends() -> list[CLIBackend]:
    return list(_REGISTRY.values())
