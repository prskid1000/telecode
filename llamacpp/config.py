"""llama.cpp configuration accessors.

Every knob comes from settings.json under `llamacpp.*`. Models live in a
registry under `llamacpp.models`; each model can override any of the
top-level knobs (ctx_size, n_gpu_layers, etc.) and the inference defaults
(temperature, top_p, …).

The argv builder in `llamacpp.argv` walks this config to produce the
command line passed to llama-server. Anything not special-cased can be
set via `extra_args: [["--flag", "value"], ...]`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import config as app_config


# ── Top-level server knobs ───────────────────────────────────────────────

def enabled() -> bool:
    return bool(app_config.get_nested("llamacpp.enabled", False))


def binary() -> str:
    """Path to llama-server executable."""
    return str(app_config.get_nested("llamacpp.binary", "llama-server"))


def host() -> str:
    return str(app_config.get_nested("llamacpp.host", "127.0.0.1"))


def port() -> int:
    return int(app_config.get_nested("llamacpp.port", 1234))


def auto_start() -> bool:
    """Eagerly load the default model when telecode starts.

    Default False — the model loads on the first `/v1/messages` or
    `/v1/chat/completions` request. Saves VRAM if you don't always need it.
    Per-model `preload: true` overrides this for specific entries.
    """
    return bool(app_config.get_nested("llamacpp.auto_start", False))


def idle_unload_sec() -> float:
    """Seconds of idleness before the supervisor stops llama-server.

    "Idle" = no in-flight requests AND last upstream call was > N seconds
    ago. The model is then unloaded; the next request reloads it. Default
    300 (5 minutes). Set 0 to disable auto-unload.
    """
    return float(app_config.get_nested("llamacpp.idle_unload_sec", 300))


# Note: telecode always remembers the last-active model (no toggle).
# `data/llama-state.json` is written on every ensure_model() call.


# `restart_on_exit` was a placeholder that was never wired — removed.


def preload_models() -> list[str]:
    """Models flagged with `preload: true` — loaded at startup regardless
    of `auto_start`. The DEFAULT model is also loaded if `auto_start` is true."""
    out: list[str] = []
    for name, cfg in models().items():
        if isinstance(cfg, dict) and cfg.get("preload"):
            out.append(name)
    return out


def ready_timeout_sec() -> float:
    return float(app_config.get_nested("llamacpp.ready_timeout_sec", 120))


def api_key() -> str:
    """Optional auth token expected by llama-server's --api-key."""
    return str(app_config.get_nested("llamacpp.api_key", "") or "")


def log_file() -> str:
    """Absolute path to the llama-server log (stdout+stderr merged)."""
    import os
    logs_dir = app_config.logs_dir()
    os.makedirs(logs_dir, exist_ok=True)
    return os.path.join(logs_dir, "llama.log")


# ── Model registry ───────────────────────────────────────────────────────

def models() -> dict[str, dict[str, Any]]:
    """Registered models: name → config dict."""
    return dict(app_config.get_nested("llamacpp.models", {}) or {})


def default_model() -> str:
    """Fallback model name when a request doesn't specify one (or maps to nothing)."""
    cfg = app_config.get_nested("llamacpp.default_model", "") or ""
    if cfg:
        return str(cfg)
    # Fallback: first key in registry
    reg = models()
    return next(iter(reg), "")


def model_cfg(name: str) -> dict[str, Any]:
    """Raw per-model config dict (may be empty)."""
    return models().get(name, {}) or {}


def resolve_model(name: str) -> str:
    """Resolve a requested model name to a registered model key.

    Lookup order:
      1. exact match in `llamacpp.models`
      2. match in `proxy.model_mapping` (then recurse — aliases may point at
         a registered model)
      3. fall back to `default_model()`

    Returns the chosen key or "" if nothing is registered at all.
    """
    if not name:
        return default_model()
    reg = models()
    if name in reg:
        return name
    mapping = app_config.get_nested("proxy.model_mapping", {}) or {}
    aliased = mapping.get(name)
    if aliased and aliased in reg:
        return aliased
    return default_model()


# ── Inference defaults ───────────────────────────────────────────────────

_INFERENCE_DEFAULTS: dict[str, Any] = {
    "temperature": 0.6,
    "top_p": 0.95,
    "top_k": 20,
    "min_p": 0.0,
    "repeat_penalty": 1.0,
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
    "max_tokens": None,
    "stop": [],
    "context_overflow": "truncate_middle",
    "reasoning": {
        "enabled": True,
        "start": "<think>",
        "end": "</think>",
        "emit_thinking_blocks": True,
    },
    "structured_output": {
        "enabled": False,
        "schema": None,
        "grammar": None,
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def inference_defaults() -> dict[str, Any]:
    """Global inference defaults from settings.json (merged over hard-coded defaults)."""
    override = app_config.get_nested("llamacpp.inference", {}) or {}
    return _deep_merge(_INFERENCE_DEFAULTS, override)


def inference_for(model_name: str) -> dict[str, Any]:
    """Resolved inference config for a specific model (per-model overrides global)."""
    global_cfg = inference_defaults()
    model_override = (model_cfg(model_name).get("inference_defaults") or {})
    return _deep_merge(global_cfg, model_override)


# ── Utilities ────────────────────────────────────────────────────────────

def upstream_url() -> str:
    """Base URL where llama-server is reachable (internal callers use this)."""
    h = host()
    # llama-server binds 0.0.0.0 by default for external access, but internal
    # callers should hit 127.0.0.1 to skip loopback round-trips.
    internal_host = "127.0.0.1" if h in ("0.0.0.0", "") else h
    return f"http://{internal_host}:{port()}"


def resolve_path(path_str: str) -> str:
    """Absolute path as-is; relative anchored to settings.json directory."""
    if not path_str:
        return ""
    p = Path(path_str)
    if p.is_absolute():
        return str(p)
    settings_path = Path(__import__("os").environ.get("TELECODE_SETTINGS", "settings.json"))
    return str((settings_path.resolve().parent / p).resolve())
