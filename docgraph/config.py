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

Per-root data (stored in <root>/.docgraph/, NOT in settings.json):

    repos.json  — extra local paths indexed into the same graph
                  [ "/path/to/extra/repo" ]

    links.json  — external URLs crawled and indexed (TTL-based refresh)
                  [ { "url": "https://…", "depth": 2, "ttl_hours": 24,
                      "last_fetched": null, "page_count": null } ]
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
    `{"path": str, "watch": bool, "pinned": bool}`. Filters empty paths.
    `pinned` rows can't be removed/reordered/edited in the tray UI."""
    out: list[dict] = []
    for entry in _root().get("roots") or []:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path", "") or "").strip()
        if not path:
            continue
        out.append({
            "path": path,
            "watch": bool(entry.get("watch", False)),
            "pinned": bool(entry.get("pinned", False)),
        })
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


def llm_max_tokens_chat() -> int:
    """Per-call cap for the right-panel Chat tab on the docgraph host.
    Default 0 = unlimited (the OpenAI-compatible server decides; Anthropic
    falls back to its 8192 default). Stored at
    `docgraph.llm.max_tokens_chat` so chat doesn't share the docstring or
    wiki budget."""
    raw = llm_cfg().get("max_tokens_chat", 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def llm_api_key() -> str:
    """Optional API key forwarded to the LLM server (Bearer / x-api-key
    depending on `llm.format`). Empty string = no auth header."""
    return str(llm_cfg().get("api_key", "") or "")


def llm_timeout() -> int:
    """Per-request HTTP timeout (s). 0 / negative = use docgraph's default
    of 60. Wiki page generation on big modules can take 30s+ on local LLM
    servers, so this is exposed."""
    raw = llm_cfg().get("timeout", 60)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 60


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
def embeddings_torch_compile() -> bool:
    """Whether the docgraph embedder applies `torch.compile`. Costs
    ~10-30 s extra cold-start; ~1.3-1.6× steady-state speedup on GPU.
    Forwarded as `--embed-torch-compile`. Off by default."""
    return bool(embeddings_cfg().get("torch_compile", False))
def embeddings_idle_unload_sec() -> float:
    """Seconds of embedder inactivity before the docgraph host evicts
    the pooled torch SentenceTransformer. 0 = never. Forwarded as
    `--embed-idle-unload-sec`; takes effect on next host spawn. Setting
    a positive value also opts the host into telecode's VRAM reaper —
    once every pooled model has idle-unloaded, telecode restarts the
    host to release the CUDA context (~300 MB) that
    `torch.cuda.empty_cache()` cannot."""
    raw = embeddings_cfg().get("idle_unload_sec", 0)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


# ── Reranker (cross-encoder over top-K search candidates) ──────────────────

def rerank_cfg() -> dict:        return _section("rerank")
def rerank_model() -> str:       return str(rerank_cfg().get("model", "") or "")
def rerank_default() -> bool:    return bool(rerank_cfg().get("default", False))
def rerank_gpu() -> bool:        return bool(rerank_cfg().get("gpu", False))
def rerank_torch_compile() -> bool:
    """Same trade-off as `embeddings_torch_compile` but for the cross-encoder.
    Independent of the embedder flag. Forwarded as `--rerank-torch-compile`."""
    return bool(rerank_cfg().get("torch_compile", False))
def rerank_idle_unload_sec() -> float:
    """Seconds of reranker inactivity before the docgraph host evicts
    the cross-encoder. 0 = never. Forwarded as `--rerank-idle-unload-sec`;
    takes effect on next host spawn."""
    raw = rerank_cfg().get("idle_unload_sec", 0)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


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
    """Max directory levels to bucket files by; 1 = one page per top-level
    module, 12 = one page per leaf folder. Forwarded to the host spawn as
    `--wiki-depth` so /api/wiki/build picks it up when the request payload
    omits `depth`. Also forwarded to `docgraph wiki --depth` on subprocess
    fallback (when the host route is unavailable)."""
    return int(wiki_cfg().get("depth", 12) or 12)


# ── External links (per-root .docgraph/links.json) ────────────────────────────

def root_links(path: str) -> list[dict]:
    """Return the external links configured for `path`. Reads .docgraph/links.json.
    Each entry: {url, depth, ttl_hours, last_fetched, page_count}."""
    from pathlib import Path as _Path
    import json as _json
    p = _Path(path).expanduser() / ".docgraph" / "links.json"
    if not p.exists():
        return []
    try:
        raw = _json.loads(p.read_text(encoding="utf-8"))
        return [e for e in raw if isinstance(e, dict) and e.get("url")]
    except Exception:
        return []


def save_root_links(path: str, links: list[dict]) -> None:
    """Write external links for `path` to .docgraph/links.json."""
    from pathlib import Path as _Path
    import json as _json
    data_dir = _Path(path).expanduser() / ".docgraph"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "links.json").write_text(
        _json.dumps(links, indent=2),
        encoding="utf-8",
    )


# ── Extra local paths (per-root .docgraph/repos.json) ────────────────────────

def root_extra_paths(path: str) -> list[str]:
    """Return the extra local paths configured for `path`. Reads .docgraph/repos.json."""
    from pathlib import Path as _Path
    import json as _json
    p = _Path(path).expanduser() / ".docgraph" / "repos.json"
    if not p.exists():
        return []
    try:
        raw = _json.loads(p.read_text(encoding="utf-8"))
        return [e for e in raw if isinstance(e, str) and e.strip()]
    except Exception:
        return []


def save_root_extra_paths(path: str, paths: list[str]) -> None:
    """Write extra local paths for `path` to .docgraph/repos.json."""
    from pathlib import Path as _Path
    import json as _json
    data_dir = _Path(path).expanduser() / ".docgraph"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "repos.json").write_text(
        _json.dumps(paths, indent=2),
        encoding="utf-8",
    )


# ── Logs ─────────────────────────────────────────────────────────────────────

def log_path(role: str = "host", slug: str | None = None) -> str:
    # Host stdout/stderr shares `docgraph.log` with the telecode-side wrapper
    # logger so users see both streams interleaved in one viewer entry. Index
    # and wiki are short-lived subprocesses with their own per-run files.
    if role == "host":
        return os.path.join(app_config.logs_dir(), "docgraph.log")
    base = f"docgraph_{role}"
    if slug:
        base = f"{base}_{slug}"
    return os.path.join(app_config.logs_dir(), f"{base}.log")


def slug_for_path(path: str) -> str:
    """Mirror docgraph.workspace.slug_for_root."""
    name = os.path.basename(os.path.normpath(path)) or "root"
    safe = "".join(c if (c.isalnum() or c in "_-") else "_" for c in name)
    return safe.lower() or "root"
