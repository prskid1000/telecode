"""Build llama-server argv from config.

Flag mapping:
  top-level `llamacpp.*`           → server-wide flags (host, port, api-key)
  per-model `llamacpp.models.<m>`  → model-specific flags (-m, --ctx-size,
                                     -ngl, --mmproj, speculative, cache)
  `extra_args`                     → generic [["--flag", "value"], ...]
                                     appended verbatim (escape hatch)

Anything in `inference_defaults` is NOT passed as a CLI flag — those are
per-request knobs handled by the translator when constructing
/v1/chat/completions payloads. Only values that must be set at process
launch (slot count, context size, KV cache dtype, multimodal projector,
draft model, etc.) belong in the model config.
"""
from __future__ import annotations

import config as app_config
from llamacpp import config as cfg


# Per-model fields that map to CLI flags. Entries: (config_key, flag_or_None,
# kind) where kind ∈ {"value", "flag", "path"}. "flag" is boolean (no value).
_MODEL_FLAG_SPECS: list[tuple[str, str, str]] = [
    ("ctx_size",      "--ctx-size",        "value"),
    ("n_gpu_layers",  "--n-gpu-layers",    "value"),
    ("threads",       "--threads",         "value"),
    ("batch_size",    "--batch-size",      "value"),
    ("ubatch_size",   "--ubatch-size",     "value"),
    ("parallel",      "--parallel",        "value"),
    ("cont_batching", "--cont-batching",   "flag"),
    ("flash_attn",    "--flash-attn",      "onoff"),
    ("mlock",         "--mlock",           "flag"),
    ("no_mmap",       "--no-mmap",         "flag"),
    ("no_kv_offload", "--no-kv-offload",   "flag"),
    ("kv_unified",    "--kv-unified",      "flag"),
    ("n_cpu_moe",     "--n-cpu-moe",       "value"),
    ("cpu_moe",       "--cpu-moe",         "flag"),
    ("cache_type_k",  "--cache-type-k",    "value"),
    ("cache_type_v",  "--cache-type-v",    "value"),
    ("cache_reuse",   "--cache-reuse",     "value"),
    ("rope_scaling",  "--rope-scaling",    "value"),
    ("rope_freq_base","--rope-freq-base",  "value"),
    ("rope_freq_scale","--rope-freq-scale","value"),
    ("yarn_orig_ctx", "--yarn-orig-ctx",   "value"),
    ("keep",          "--keep",            "value"),
    ("seed",          "--seed",            "value"),
    ("numa",          "--numa",            "value"),
    ("main_gpu",      "--main-gpu",        "value"),
    ("tensor_split",  "--tensor-split",    "value"),
    ("split_mode",    "--split-mode",      "value"),
    ("slot_save_path","--slot-save-path",  "path"),
    ("chat_template", "--chat-template",   "value"),
    ("jinja",         "--jinja",           "flag"),
    ("mmproj",        "--mmproj",          "path"),
    ("draft_model",   "--model-draft",     "path"),
    ("draft_n",       "--draft-max",       "value"),
    ("draft_n_min",   "--draft-min",       "value"),
    ("draft_p_min",   "--draft-p-min",     "value"),
    ("lookup_cache_static",  "--lookup-cache-static",  "path"),
    ("lookup_cache_dynamic", "--lookup-cache-dynamic", "path"),
    ("lora",          "--lora",            "path"),
    ("lora_scale",    "--lora-scaled",     "value"),
    ("grammar",       "--grammar",         "value"),
    ("grammar_file",  "--grammar-file",    "path"),
    ("reasoning_budget",         "--reasoning-budget",         "value"),
    ("reasoning_budget_message", "--reasoning-budget-message", "value"),
    ("reasoning_format",         "--reasoning-format",         "value"),
    ("fit",                      "--fit",                      "onoff"),
    ("fit_ctx",                  "--fit-ctx",                  "value"),
    ("fit_target",               "--fit-target",               "value"),
]


def build_argv(model_name: str) -> list[str]:
    """Produce the full llama-server argv for a given registered model.

    Raises KeyError if the model isn't registered and doesn't resolve to one.
    """
    resolved = cfg.resolve_model(model_name)
    if not resolved:
        raise KeyError(f"No model registered that matches '{model_name}'")

    mcfg = cfg.model_cfg(resolved)
    model_path = mcfg.get("path")
    if not model_path:
        raise KeyError(f"llamacpp.models.{resolved}.path is required")

    argv: list[str] = [cfg.binary()]

    # Server-wide binding
    argv += ["--host", cfg.host()]
    argv += ["--port", str(cfg.port())]

    # API key (optional)
    key = cfg.api_key()
    if key:
        argv += ["--api-key", key]

    # Disable the built-in web UI — we don't want it exposed on the same
    # port the proxy talks to.
    argv.append("--no-webui")

    # Main model
    argv += ["--model", cfg.resolve_path(model_path)]

    # Per-model flags
    for key, flag, kind in _MODEL_FLAG_SPECS:
        if key not in mcfg:
            continue
        val = mcfg[key]
        if val is None:
            continue
        if kind == "flag":
            if bool(val):
                argv.append(flag)
        elif kind == "onoff":
            # Tri-state flag — llama.cpp newer style expects "on"/"off"/"auto"
            argv += [flag, "on" if bool(val) else "off"]
        elif kind == "path":
            argv += [flag, cfg.resolve_path(str(val))]
        else:  # "value"
            argv += [flag, str(val)]

    # Generic escape hatch — [["--flag","value"], ["--bare-flag"], ...]
    for entry in mcfg.get("extra_args", []) or []:
        if isinstance(entry, (list, tuple)):
            argv.extend(str(x) for x in entry)
        elif isinstance(entry, str):
            argv.append(entry)

    # Top-level extra_args apply to every model
    for entry in (app_config.get_nested("llamacpp.extra_args", []) or []):
        if isinstance(entry, (list, tuple)):
            argv.extend(str(x) for x in entry)
        elif isinstance(entry, str):
            argv.append(entry)

    return argv


def describe(model_name: str) -> str:
    """Human-readable argv preview for logs."""
    try:
        argv = build_argv(model_name)
    except KeyError as exc:
        return f"<{exc}>"
    # Quote args containing spaces for copy-paste
    return " ".join(
        f'"{a}"' if " " in a else a for a in argv
    )
