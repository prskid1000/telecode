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
def host_debounce() -> int:       return int(host_cfg().get("debounce", 500) or 500)


# ── LLM augmentation ────────────────────────────────────────────────────────

def llm_cfg() -> dict:           return _section("llm")
def llm_model() -> str:          return str(llm_cfg().get("model", "") or "")
def llm_host() -> str:           return str(llm_cfg().get("host", "localhost") or "localhost")
def llm_port() -> int:           return int(llm_cfg().get("port", 1235) or 1235)
def llm_format() -> str:         return str(llm_cfg().get("format", "openai") or "openai")
def llm_max_tokens() -> int:     return int(llm_cfg().get("max_tokens", 150) or 150)
def llm_docstrings() -> bool:
    """Whether LLM docstring augmentation runs during indexing. Default
    False — even if a model is configured, the user must explicitly opt in
    to docstring generation (it's slow + costs token budget)."""
    return bool(llm_cfg().get("docstrings", False))
def llm_wiki() -> bool:
    """Whether the wiki builder uses the LLM. Default False — wiki falls
    back to the fact-sheet renderer until the user explicitly enables it."""
    return bool(llm_cfg().get("wiki", False))
def llm_max_tokens_wiki() -> int:
    """Wiki generation needs a much bigger budget than docstring augmentation
    (the docgraph CLI defaults wiki to 4096, index to 150). Stored at
    `docgraph.llm.max_tokens_wiki` so the two can be tuned independently."""
    return int(llm_cfg().get("max_tokens_wiki", 4096) or 4096)


def llm_prompt_docstring() -> str:
    """User-supplied override for the docstring template. Forwarded to
    docgraph as DOCGRAPH_LLM_PROMPT_DOCSTRING. Empty = use built-in."""
    prompts = llm_cfg().get("prompts") or {}
    return str(prompts.get("docstring", "") or "")


def llm_prompt_wiki() -> str:
    """User-supplied override for the wiki output-format tail."""
    prompts = llm_cfg().get("prompts") or {}
    return str(prompts.get("wiki", "") or "")


# ── Embeddings ──────────────────────────────────────────────────────────────

def embeddings_cfg() -> dict:    return _section("embeddings")
def embeddings_model() -> str:   return str(embeddings_cfg().get("model", "") or "")
def embeddings_gpu() -> bool:    return bool(embeddings_cfg().get("gpu", False))
def embeddings_directml_device_id() -> int:
    """DirectML adapter index for the embedder. -1 = let DirectML pick.
    Set to the dGPU index (often 1) on hybrid-graphics laptops where
    Windows otherwise routes the windowless host process to the iGPU."""
    raw = embeddings_cfg().get("directml_device_id", -1)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


# ── Reranker (cross-encoder over top-K search candidates) ──────────────────

def rerank_cfg() -> dict:        return _section("rerank")
def rerank_model() -> str:       return str(rerank_cfg().get("model", "") or "")
def rerank_default() -> bool:    return bool(rerank_cfg().get("default", False))
def rerank_gpu() -> bool:        return bool(rerank_cfg().get("gpu", False))


# ── Index (CLI subprocess flags) ───────────────────────────────────────────

def index_cfg() -> dict:             return _section("index")
def index_workers() -> int:          return int(index_cfg().get("workers", 0) or 0)
def index_embed_batch_size() -> int:
    """`docgraph index --embed-batch-size`. 0 = use docgraph's default
    (256 on CPU / 32 on GPU). Lower it if `--gpu` saturates VRAM."""
    return int(index_cfg().get("embed_batch_size", 0) or 0)


# ── Wiki ────────────────────────────────────────────────────────────────────

def wiki_cfg() -> dict:          return _section("wiki")
def wiki_depth() -> int:
    """`docgraph wiki --depth`. Max directory levels to bucket files by;
    1 = one page per top-level module, 12 = one page per leaf folder."""
    return int(wiki_cfg().get("depth", 12) or 12)


# ── Document indexing (tier 2 + tier 3 — opt-in) ──────────────────────────

_DEFAULT_TEXT_EXTS = ("md", "markdown", "txt", "rst", "csv")
_DEFAULT_ASSET_EXTS = (
    "pdf", "xlsx", "xls", "docx", "doc", "ppt", "pptx",
    "png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "bmp", "tiff",
    "mp4", "mov", "webm", "avi", "mkv", "mp3", "wav", "flac", "ogg", "m4a",
    "zip", "tar", "gz", "tgz", "7z", "rar", "bz2", "xz",
    "parquet", "feather", "arrow", "h5", "hdf5", "pkl", "pickle", "npz", "npy",
    "ttf", "woff", "woff2", "otf", "eot",
    "gltf", "glb", "fbx", "obj", "stl", "blend",
)


def documents_cfg() -> dict:     return index_cfg().get("documents") or {}


def documents_enabled() -> bool:
    """Master toggle for the document + asset pass."""
    return bool(documents_cfg().get("enabled", False))


def text_extensions() -> tuple[str, ...]:
    raw = documents_cfg().get("text_extensions")
    if isinstance(raw, list) and raw:
        return tuple(str(e).strip().lstrip(".").lower() for e in raw if str(e).strip())
    return _DEFAULT_TEXT_EXTS


def asset_extensions() -> tuple[str, ...]:
    raw = documents_cfg().get("asset_extensions")
    if isinstance(raw, list) and raw:
        return tuple(str(e).strip().lstrip(".").lower() for e in raw if str(e).strip())
    return _DEFAULT_ASSET_EXTS


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
