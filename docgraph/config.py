"""Accessors over `settings.docgraph.*` plus binary auto-detection."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import config as app_config


def _root() -> dict:
    return app_config.get_nested("docgraph", {}) or {}


def _section(name: str) -> dict:
    return _root().get(name, {}) or {}


def binary_setting() -> str:
    return str(_root().get("binary", "") or "")


def resolve_binary() -> str | None:
    """Return absolute path to the `docgraph` binary, or None if not found.

    Setting empty/blank → autodetect:
      1. `shutil.which("docgraph")` (handles Windows `.cmd`/`.bat`/`.exe` shims)
      2. `<settings_dir>/.venv/Scripts/docgraph.exe`
      3. `~/.local/bin/docgraph.bat`
      4. `~/.docgraph/.venv/Scripts/docgraph.exe`
    Setting non-empty → resolved via `shutil.which` first (so `docgraph` works
    if it's on PATH), then used verbatim if absolute.
    """
    raw = binary_setting()
    if raw:
        hit = shutil.which(raw)
        if hit:
            return hit
        if os.path.isabs(raw) and os.path.exists(raw):
            return raw
        return None
    hit = shutil.which("docgraph")
    if hit:
        return hit
    home = Path.home()
    settings_dir = Path(os.environ.get("TELECODE_SETTINGS", "settings.json")).resolve().parent
    for candidate in (
        settings_dir / ".venv" / "Scripts" / "docgraph.exe",
        home / ".local" / "bin" / "docgraph.bat",
        home / ".local" / "bin" / "docgraph",
        home / ".docgraph" / ".venv" / "Scripts" / "docgraph.exe",
    ):
        if candidate.exists():
            return str(candidate)
    return None


def default_path() -> str:
    return str(_root().get("default_path", "") or "")


# ── Index ────────────────────────────────────────────────────────────────────
def index_cfg() -> dict:           return _section("index")
def index_paths() -> list[str]:    return [str(p) for p in (index_cfg().get("paths") or []) if p]
def index_full() -> bool:          return bool(index_cfg().get("full", False))
def index_workers() -> int:        return int(index_cfg().get("workers", 0) or 0)
def index_gpu() -> bool:           return bool(index_cfg().get("gpu", False))
def index_llm_model() -> str:      return str(index_cfg().get("llm_model", "") or "")
def index_llm_host() -> str:       return str(index_cfg().get("llm_host", "localhost") or "localhost")
def index_llm_port() -> int:       return int(index_cfg().get("llm_port", 1235) or 1235)
def index_llm_format() -> str:     return str(index_cfg().get("llm_format", "openai") or "openai")
def index_llm_max_tokens() -> int: return int(index_cfg().get("llm_max_tokens", 150) or 150)
def index_embedding_model() -> str: return str(index_cfg().get("embedding_model", "") or "")


# ── Watch ────────────────────────────────────────────────────────────────────
def watch_cfg() -> dict:           return _section("watch")
def watch_enabled() -> bool:       return bool(watch_cfg().get("enabled", False))
def watch_auto_start() -> bool:    return bool(watch_cfg().get("auto_start", False))
def watch_auto_restart() -> bool:  return bool(watch_cfg().get("auto_restart", True))
def watch_path() -> str:           return str(watch_cfg().get("path", "") or default_path())
def watch_serve_too() -> bool:     return bool(watch_cfg().get("serve_too", False))
def watch_host() -> str:           return str(watch_cfg().get("host", "127.0.0.1") or "127.0.0.1")
def watch_port() -> int:           return int(watch_cfg().get("port", 5500) or 5500)


# ── Serve ────────────────────────────────────────────────────────────────────
def serve_cfg() -> dict:           return _section("serve")
def serve_enabled() -> bool:       return bool(serve_cfg().get("enabled", False))
def serve_auto_start() -> bool:    return bool(serve_cfg().get("auto_start", False))
def serve_auto_restart() -> bool:  return bool(serve_cfg().get("auto_restart", True))
def serve_path() -> str:           return str(serve_cfg().get("path", "") or default_path())
def serve_host() -> str:           return str(serve_cfg().get("host", "127.0.0.1") or "127.0.0.1")
def serve_port() -> int:           return int(serve_cfg().get("port", 5500) or 5500)
def serve_gpu() -> bool:           return bool(serve_cfg().get("gpu", False))


# ── Daemon ───────────────────────────────────────────────────────────────────
def daemon_cfg() -> dict:          return _section("daemon")
def daemon_enabled() -> bool:      return bool(daemon_cfg().get("enabled", False))
def daemon_auto_start() -> bool:   return bool(daemon_cfg().get("auto_start", False))
def daemon_auto_restart() -> bool: return bool(daemon_cfg().get("auto_restart", True))
def daemon_port() -> int:          return int(daemon_cfg().get("port", 5577) or 5577)
def daemon_model() -> str:         return str(daemon_cfg().get("model", "BAAI/bge-small-en-v1.5") or "BAAI/bge-small-en-v1.5")
def daemon_gpu() -> bool:          return bool(daemon_cfg().get("gpu", False))


# ── MCP ──────────────────────────────────────────────────────────────────────
def mcp_cfg() -> dict:             return _section("mcp")
def mcp_enabled() -> bool:         return bool(mcp_cfg().get("enabled", False))
def mcp_auto_start() -> bool:      return bool(mcp_cfg().get("auto_start", False))
def mcp_auto_restart() -> bool:    return bool(mcp_cfg().get("auto_restart", True))
def mcp_paths() -> list[str]:      return [str(p) for p in (mcp_cfg().get("paths") or []) if p]
def mcp_base_port() -> int:        return int(mcp_cfg().get("base_port", 5600) or 5600)
def mcp_host() -> str:             return str(mcp_cfg().get("host", "127.0.0.1") or "127.0.0.1")
def mcp_gpu() -> bool:             return bool(mcp_cfg().get("gpu", False))
def mcp_ready_timeout_sec() -> int: return int(mcp_cfg().get("ready_timeout_sec", 30) or 30)


# ── Logs ─────────────────────────────────────────────────────────────────────
def log_path(role: str, slug: str | None = None) -> str:
    """Centralised log path so supervisors and UI tail the same file."""
    base = f"docgraph_{role}"
    if slug:
        base = f"{base}_{slug}"
    return os.path.join(app_config.logs_dir(), f"{base}.log")


def slug_for_path(path: str) -> str:
    """Stable short slug from a repo path — used for log names + tool prefixes."""
    name = os.path.basename(os.path.normpath(path)) or "repo"
    safe = "".join(c if (c.isalnum() or c in "_-") else "_" for c in name)
    return safe.lower() or "repo"
