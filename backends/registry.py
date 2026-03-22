"""
Backend registry — auto-built from settings.json tool keys.

PTY-based backends are GenericCLIBackend instances created from config.
Screen and Video are special non-PTY backends with their own classes.
"""
from .base import CLIBackend
from .implementations import GenericCLIBackend, ScreenBackend, VideoBackend, NON_PTY_KEYS
import config

# Special non-PTY backends
_SPECIAL: dict[str, CLIBackend] = {
    "screen": ScreenBackend(),
    "video":  VideoBackend(),
}


def _build_registry() -> dict[str, CLIBackend]:
    registry: dict[str, CLIBackend] = {}
    for key in config.all_tool_keys():
        if key in _SPECIAL:
            registry[key] = _SPECIAL[key]
        elif key not in NON_PTY_KEYS:
            registry[key] = GenericCLIBackend(key)
    # Ensure special backends are always present
    for key, backend in _SPECIAL.items():
        registry.setdefault(key, backend)
    return registry


_REGISTRY: dict[str, CLIBackend] = _build_registry()


def refresh() -> None:
    """Rebuild registry after settings reload."""
    global _REGISTRY
    _REGISTRY = _build_registry()


def get_backend(key: str) -> CLIBackend | None:
    return _REGISTRY.get(key)


def all_backends() -> list[CLIBackend]:
    return list(_REGISTRY.values())
