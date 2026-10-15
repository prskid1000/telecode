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

import logging
import re
import sys
from pathlib import Path
from typing import Any

import config as app_config

_LOG = logging.getLogger("telecode.llamacpp.config")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


# ── Top-level server knobs ───────────────────────────────────────────────

def enabled() -> bool:
    return bool(app_config.get_nested("llamacpp.enabled", False))


# where the llama.cpp install lives when llamacpp.binary is empty (relative to the settings.json directory)
DEFAULT_BINARY = "./llama/llama-server.exe" if sys.platform == "win32" else "llama-server"


def as_int(value: Any, default: int = 0) -> int:
    """int() that survives a hand-edited settings value ("abc", "32768.0", None)."""
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if v == v else default  # NaN


def binary() -> str:
    """Path to llama-server executable.

    Default is `./llama/llama-server.exe` — the install lives inside the
    telecode dir so the updater overlays releases there, not in a stray
    `~/.llama`. A bare name ("llama-server") is still resolved on PATH; a
    relative path is anchored to the settings.json directory like
    `resolve_path`, so it does not depend on the process cwd."""
    raw = str(app_config.get_nested("llamacpp.binary", "") or "").strip() or DEFAULT_BINARY
    if ("/" in raw or "\\" in raw) and not Path(raw).is_absolute():
        return resolve_path(raw)
    return raw


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


def watchdog_sec() -> float:
    """Restart llama-server when its /health has not answered for this many seconds (0 = off).

    A server that is busy loading or generating still answers /health; one that hangs in the driver or a deadlock does not."""
    try:
        return max(0.0, float(app_config.get_nested("llamacpp.watchdog_sec", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def win_power_throttle_off() -> bool:
    """Opt out of Windows power throttling (EcoQoS) for llama-server.

    On hybrid CPUs Windows may park a background-looking process on the E-cores; Strata measured
    +18% decode with it off. Off by default so the spawn is unchanged."""
    return bool(app_config.get_nested("llamacpp.win_power_throttle_off", False))


def win_process_priority() -> str:
    """Priority class set on llama-server after the spawn: '' (leave it), idle, below_normal, normal,
    above_normal or high. Not the same as the llama.cpp --prio thread priority."""
    v = str(app_config.get_nested("llamacpp.win_process_priority", "") or "").strip().lower()
    return v if v in ("idle", "below_normal", "normal", "above_normal", "high") else ""


def prevent_sleep() -> bool:
    """Keep Windows awake while llama-server has a request in flight.

    Default False. Held only while the supervisor's inflight count is above zero
    and released the moment it returns to zero or the model stops; no effect off
    Windows. Not a llama-server flag, so it never reaches argv."""
    return bool(app_config.get_nested("llamacpp.prevent_sleep", False))


def min_free_vram_mib() -> int:
    """Refuse to spawn llama-server when the first NVIDIA GPU has less free VRAM (MiB).

    0 (default) = no check. Guards against the driver spilling a model into shared
    system memory, which runs an order of magnitude slower than failing early.
    Not a llama-server flag, so it never reaches argv."""
    return as_int(app_config.get_nested("llamacpp.min_free_vram_mib", 0))


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


def spec_active() -> bool:
    """True when speculative decoding is on: a real --spec-type strategy or --spec-default."""
    g = app_config.get_nested("llamacpp", {}) or {}
    types = [t.strip().lower() for t in str(g.get("spec_type", "") or "").split(",")]
    return any(t and t != "none" for t in types) or bool(g.get("spec_default"))


def effective_ctx_size(name: str) -> int:
    """Context the server is started with for `name`.

    `ctx_size_spec` (0 = off) caps it while speculative decoding is on: the draft / MTP
    context and its KV take VRAM, so a model that just fits without them needs a smaller window.
    """
    m = model_cfg(name)
    ctx = as_int(m.get("ctx_size"))
    cap = as_int(m.get("ctx_size_spec"))
    if cap > 0 and ctx > 0 and spec_active():
        return min(ctx, cap)
    return ctx


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
    # Off by default: llama.cpp's own default is unrestricted thinking, and a
    # cap that bites is a deliberate choice, not something to inherit silently.
    "thinking_budget": {"enabled": False},
    # Keys are Claude Code's effort vocabulary (what the proxy is built for);
    # values are the MODEL's chat-template vocabulary. The default targets
    # low/medium/high, the one trio valid across both families we care about:
    # GPT-OSS takes it verbatim, and Qwen 3.8's template aliases high -> xhigh
    # before its `not in ('xhigh','medium','low')` raise, so nothing here can
    # trip it. Tighten `allowed` per model when the template is stricter.
    "reasoning_effort": {
        "template_key": "reasoning_effort",
        "map": {
            "none": "low", "minimal": "low", "low": "low",
            "medium": "medium", "adaptive": "medium",
            "high": "high", "xhigh": "high", "max": "high",
        },
    },
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


# ── Environment for the spawned llama-server ─────────────────────────────

def parse_env_text(text: Any, source: str = "env") -> dict[str, str]:
    """Parse a multi-line `KEY=VALUE` block into a dict.

    Blank lines and lines starting with `#` are ignored. A name that is not
    [A-Za-z_][A-Za-z0-9_]* or a line without `=` is dropped with a warning (the
    rest still applies). The value is everything after the first `=`, stripped,
    with one pair of matching surrounding quotes removed. Later lines win.
    """
    out: dict[str, str] = {}
    if not isinstance(text, str):
        if text not in (None, ""):
            _LOG.warning("llamacpp: %s must be text (one KEY=VALUE per line); ignored", source)
        return out
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, eq, value = line.partition("=")
        name = name.strip()
        if not eq or not _ENV_NAME.match(name):
            _LOG.warning("llamacpp: %s line %d ignored (expected NAME=VALUE with a valid name): %r",
                         source, n, raw[:80])
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[name] = value
    return out


def extra_env(model_name: str = "") -> dict[str, str]:
    """Environment variables to add for a llama-server spawn: global `llamacpp.env`,
    then the model's own `llamacpp.models.<m>.env` on top (it overrides)."""
    env = parse_env_text(app_config.get_nested("llamacpp.env", "") or "", "llamacpp.env")
    if model_name:
        resolved = resolve_model(model_name) or model_name
        env.update(parse_env_text(model_cfg(resolved).get("env", "") or "",
                                  f"llamacpp.models.{resolved}.env"))
    return env


def spawn_env(model_name: str = "") -> dict[str, str] | None:
    """Full child environment (os.environ + extras), or None when there are no
    extras so the child inherits unchanged. Rebuilt on every spawn."""
    extra = extra_env(model_name)
    if not extra:
        return None
    import os
    env = dict(os.environ)
    env.update(extra)
    return env
