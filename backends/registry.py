from .base import CLIBackend
from .implementations import ClaudeCodeBackend, CodexBackend, ShellBackend

_REGISTRY: dict[str, CLIBackend] = {
    b.info.key: b
    for b in [
        ClaudeCodeBackend(),
        CodexBackend(),
        ShellBackend(),
    ]
}

def get_backend(key: str) -> CLIBackend | None:
    return _REGISTRY.get(key)

def all_backends() -> list[CLIBackend]:
    return list(_REGISTRY.values())
