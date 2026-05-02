"""Accessors over `settings.docgraph.*` for the unified-host era.

New shape (settings.example.json):

    "docgraph": {
      "binary": "",
      "host": {
        "enabled": false, "auto_start": false, "auto_restart": true,
        "host": "127.0.0.1", "port": 5500,
        "gpu": false
      },
      "roots": [
        { "path": "/path/to/repo", "watch": false }
      ],
      "llm":        { "model": "", "host": "localhost", "port": 1235,
                      "format": "openai", "max_tokens": 150 },
      "embeddings": { "model": "", "gpu": false },
      "index":      { "workers": 0 }
    }
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import config as app_config


def _root() -> dict:
    return app_config.get_nested("docgraph", {}) or {}


def _section(name: str) -> dict:
    return _root().get(name, {}) or {}


# ── Binary autodetect (unchanged) ──────────────────────────────────────────

def binary_setting() -> str:
    return str(_root().get("binary", "") or "")


def resolve_binary() -> str | None:
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


# ── Roots ──────────────────────────────────────────────────────────────────

def roots() -> list[dict]:
    """Return the configured roots verbatim. Each entry is
    `{"path": str, "watch": bool}`. Filters empty paths."""
    out: list[dict] = []
    for entry in _root().get("roots") or []:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path", "") or "").strip()
        if not path:
            continue
        out.append({"path": path, "watch": bool(entry.get("watch", False))})
    return out


def root_paths() -> list[str]:
    return [r["path"] for r in roots()]


def root_paths_to_watch() -> list[str]:
    return [r["path"] for r in roots() if r.get("watch")]


def default_path() -> str:
    """First configured root, or filesystem autodetect."""
    paths = root_paths()
    if paths:
        return paths[0]
    home = Path.home()
    settings_dir = Path(os.environ.get("TELECODE_SETTINGS", "settings.json")).resolve().parent
    for candidate in (
        Path.cwd(),
        home / ".docgraph",
        settings_dir,
    ):
        try:
            if (candidate / ".docgraph" / "graph.kuzu").exists():
                return str(candidate)
        except OSError:
            pass
    return ""


# ── Host ───────────────────────────────────────────────────────────────────

def host_cfg() -> dict:           return _section("host")
def host_enabled() -> bool:       return bool(host_cfg().get("enabled", False))
def host_auto_start() -> bool:    return bool(host_cfg().get("auto_start", False))
def host_auto_restart() -> bool:  return bool(host_cfg().get("auto_restart", True))
def host_host() -> str:           return str(host_cfg().get("host", "127.0.0.1") or "127.0.0.1")
def host_port() -> int:           return int(host_cfg().get("port", 5500) or 5500)
def host_gpu() -> bool:           return bool(host_cfg().get("gpu", False))


# ── LLM augmentation ────────────────────────────────────────────────────────

def llm_cfg() -> dict:           return _section("llm")
def llm_model() -> str:          return str(llm_cfg().get("model", "") or "")
def llm_host() -> str:           return str(llm_cfg().get("host", "localhost") or "localhost")
def llm_port() -> int:           return int(llm_cfg().get("port", 1235) or 1235)
def llm_format() -> str:         return str(llm_cfg().get("format", "openai") or "openai")
def llm_max_tokens() -> int:     return int(llm_cfg().get("max_tokens", 150) or 150)


# ── Embeddings ──────────────────────────────────────────────────────────────

def embeddings_cfg() -> dict:    return _section("embeddings")
def embeddings_model() -> str:   return str(embeddings_cfg().get("model", "") or "")
def embeddings_gpu() -> bool:    return bool(embeddings_cfg().get("gpu", False))


# ── Index (CLI subprocess flags) ───────────────────────────────────────────

def index_cfg() -> dict:         return _section("index")
def index_workers() -> int:      return int(index_cfg().get("workers", 0) or 0)


# ── Logs ─────────────────────────────────────────────────────────────────────

def log_path(role: str = "host", slug: str | None = None) -> str:
    base = f"docgraph_{role}"
    if slug:
        base = f"{base}_{slug}"
    return os.path.join(app_config.logs_dir(), f"{base}.log")


def slug_for_path(path: str) -> str:
    """Mirror docgraph.workspace.slug_for_root."""
    name = os.path.basename(os.path.normpath(path)) or "root"
    safe = "".join(c if (c.isalnum() or c in "_-") else "_" for c in name)
    return safe.lower() or "root"
