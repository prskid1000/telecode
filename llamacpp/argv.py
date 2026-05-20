"""Build llama-server argv from config.

Flag layout:
  top-level `llamacpp.*`           → server-wide flags (host, port, threads,
                                     batch/ubatch, parallel, caching policy,
                                     speculative algorithm, hardware layout)
  per-model `llamacpp.models.<m>`  → model-specific flags (path, ctx_size,
                                     -ngl, mmproj, cache_type_k/v, flash_attn,
                                     draft model pair, rope, lora, grammar)
  `extra_args`                     → generic [["--flag", "value"], ...]
                                     appended verbatim (escape hatch)

Flags are resolved from exactly one level. No cross-level fallback: a key
in the wrong section is silently ignored.

Anything in `inference_defaults` is NOT passed as a CLI flag — those are
per-request knobs handled by the translator when constructing
/v1/chat/completions payloads.
"""
from __future__ import annotations

from typing import Optional

import config as app_config
from llamacpp import config as cfg


# Kinds:
#   "value"     → --flag <str(val)> ; skip if empty string
#   "value_nz"  → --flag <str(val)> ; skip if empty string OR numeric zero.
#                 Use for knobs where 0 means "server default / disabled".
#   "path"      → --flag <resolved_path> ; skip if empty string
#   "flag"      → --flag (only if truthy); no value
#   "onoff"     → --flag on|off (tri-state; always emits if present)
#   "bool_pair" → flag is a tuple (positive, negative); emits one of the two
#                 based on truthiness. Used for --X / --no-X style pairs.

# Server-wide flags read from top-level llamacpp.*
_GLOBAL_FLAG_SPECS: list[tuple[str, object, str]] = [
    # CPU / batching
    ("threads",          "--threads",         "value"),
    ("threads_batch",    "--threads-batch",   "value_nz"),
    ("batch_size",       "--batch-size",      "value"),
    ("ubatch_size",      "--ubatch-size",     "value"),
    ("parallel",         "--parallel",        "value"),
    ("cont_batching",    "--cont-batching",   "flag"),

    # Scheduler / OS scheduling
    ("cpu_strict",       "--cpu-strict",       "value"),
    ("cpu_strict_batch", "--cpu-strict-batch", "value"),
    ("prio",             "--prio",             "value"),
    ("prio_batch",       "--prio-batch",       "value"),
    ("poll",             "--poll",             "value"),
    ("poll_batch",       "--poll-batch",       "value"),
    ("threads_http",     "--threads-http",     "value_nz"),

    # Memory policy
    ("mlock",            "--mlock",           "flag"),
    ("no_mmap",          "--no-mmap",         "flag"),
    ("no_host",          "--no-host",         "flag"),
    ("repack",           ("--repack", "--no-repack"),           "bool_pair"),
    ("op_offload",       ("--op-offload", "--no-op-offload"),   "bool_pair"),

    # Hardware layout
    ("main_gpu",         "--main-gpu",        "value"),
    ("tensor_split",     "--tensor-split",    "value"),
    ("split_mode",       "--split-mode",      "value"),
    ("numa",             "--numa",            "value"),

    # Sampling/context defaults that apply at launch
    ("seed",             "--seed",            "value"),
    ("keep",             "--keep",            "value"),

    # Caching policy
    ("kv_offload",       ("--kv-offload", "--no-kv-offload"),   "bool_pair"),
    ("kv_unified",       ("--kv-unified", "--no-kv-unified"),   "bool_pair"),
    ("cache_prompt",     ("--cache-prompt", "--no-cache-prompt"), "bool_pair"),
    # renamed from --clear-idle/--no-clear-idle in upstream commit 9d49acb
    ("cache_idle_slots", ("--cache-idle-slots", "--no-cache-idle-slots"), "bool_pair"),
    ("context_shift",    ("--context-shift", "--no-context-shift"), "bool_pair"),
    ("warmup",           ("--warmup", "--no-warmup"),             "bool_pair"),
    # cache_ram / ctx_checkpoints / checkpoint_every_n_tokens use server-side
    # "0 = disable" semantics — must emit literally `--flag 0`, not skip. Hence
    # "value" (not "value_nz") so explicit zero passes through. Skipping zero
    # would silently apply the server defaults (8192 MiB cache, 32 checkpoints,
    # 8192-token checkpoint interval), wasting RAM/VRAM.
    ("cache_ram",                  "--cache-ram",                 "value"),
    ("defrag_thold",               "--defrag-thold",              "value_nz"),
    ("ctx_checkpoints",            "--ctx-checkpoints",           "value"),
    ("checkpoint_every_n_tokens",  "--checkpoint-every-n-tokens", "value"),
    ("swa_full",                   "--swa-full",                  "flag"),
    ("slot_save_path",             "--slot-save-path",            "path"),
    ("sleep_idle_seconds",         "--sleep-idle-seconds",        "value_nz"),

    # Speculative decoding algorithm (server-wide choice).
    # The per-ngram size/min-hits knobs are dispatched in _emit_spec_ngram()
    # because each spec_type takes its own per-mode flag now.
    ("spec_type",                "--spec-type",            "value"),
    ("threads_draft",            "--threads-draft",        "value_nz"),
    ("threads_batch_draft",      "--threads-batch-draft",  "value_nz"),
]


# spec_type → (size-n flag, size-m flag, min-hits flag). None entries are no-op.
# Upstream replaced the single --spec-ngram-* flags with per-mode variants;
# ngram-mod has only n-match (the "lookup length" knob), no size-m / min-hits.
_NGRAM_FLAG_MAP: dict[str, tuple[Optional[str], Optional[str], Optional[str]]] = {
    "ngram-simple":  ("--spec-ngram-simple-size-n",  "--spec-ngram-simple-size-m",  "--spec-ngram-simple-min-hits"),
    "ngram-map-k":   ("--spec-ngram-map-k-size-n",   "--spec-ngram-map-k-size-m",   "--spec-ngram-map-k-min-hits"),
    "ngram-map-k4v": ("--spec-ngram-map-k4v-size-n", "--spec-ngram-map-k4v-size-m", "--spec-ngram-map-k4v-min-hits"),
    "ngram-mod":     ("--spec-ngram-mod-n-match",    None,                          None),
}


def _emit_spec_ngram(argv: list[str], gcfg: dict) -> None:
    """Translate the historical `spec_ngram_size_n/_m/_min_hits` keys to the
    per-spec-type flags llama-server now requires (b9145+)."""
    st = str(gcfg.get("spec_type", "") or "").strip()
    mapping = _NGRAM_FLAG_MAP.get(st)
    if not mapping:
        return
    n_flag, m_flag, h_flag = mapping
    for key, flag in (("spec_ngram_size_n", n_flag),
                      ("spec_ngram_size_m", m_flag),
                      ("spec_ngram_min_hits", h_flag)):
        if flag is None:
            continue
        val = gcfg.get(key)
        if val in (None, "", 0, "0"):
            continue
        argv += [flag, str(val)]


# Per-model flags read from llamacpp.models.<m>.*
_MODEL_FLAG_SPECS: list[tuple[str, object, str]] = [
    # Capacity
    ("ctx_size",      "--ctx-size",        "value"),
    ("n_gpu_layers",  "--n-gpu-layers",    "value"),

    # Attention
    ("flash_attn",    "--flash-attn",      "onoff"),

    # KV cache dtypes (model-sensitive)
    ("cache_type_k",  "--cache-type-k",    "value"),
    ("cache_type_v",  "--cache-type-v",    "value"),
    ("cache_reuse",   "--cache-reuse",     "value"),

    # MoE
    ("n_cpu_moe",     "--n-cpu-moe",       "value"),
    ("cpu_moe",       "--cpu-moe",         "flag"),

    # RoPE (0 = model default for all numeric RoPE knobs)
    ("rope_scaling",   "--rope-scaling",     "value"),
    ("rope_freq_base", "--rope-freq-base",   "value_nz"),
    ("rope_freq_scale","--rope-freq-scale",  "value_nz"),
    ("yarn_orig_ctx",  "--yarn-orig-ctx",    "value_nz"),

    # Chat template
    ("chat_template", "--chat-template",   "value"),
    ("jinja",         "--jinja",           "flag"),

    # Vision
    ("mmproj",        "--mmproj",          "path"),

    # Draft model (paired with main model). Upstream b9145 dropped
    # --draft-max/--draft-min/--ctx-size-draft; the replacements are the
    # --spec-draft-* family. --draft-p-min is still accepted as an alias of
    # --spec-draft-p-min.
    ("draft_model",         "--model-draft",        "path"),
    ("n_gpu_layers_draft",  "--n-gpu-layers-draft", "value_nz"),
    ("cache_type_k_draft",  "--cache-type-k-draft", "value"),
    ("cache_type_v_draft",  "--cache-type-v-draft", "value"),
    ("device_draft",        "--device-draft",       "value"),
    ("draft_n",       "--spec-draft-n-max", "value"),
    ("draft_n_min",   "--spec-draft-n-min", "value"),
    ("draft_p_min",   "--spec-draft-p-min", "value"),

    # N-gram lookup cache (only active when spec_type=ngram-cache; server
    # does not implement save, so dynamic file is never written — load only)
    ("lookup_cache_static",  "--lookup-cache-static",  "path"),
    ("lookup_cache_dynamic", "--lookup-cache-dynamic", "path"),

    # LoRA
    ("lora",          "--lora",            "path"),
    ("lora_scale",    "--lora-scaled",     "value"),

    # Grammar
    ("grammar",       "--grammar",         "value"),
    ("grammar_file",  "--grammar-file",    "path"),

    # Reasoning
    ("reasoning_budget",         "--reasoning-budget",         "value"),
    ("reasoning_budget_message", "--reasoning-budget-message", "value"),
    ("reasoning_format",         "--reasoning-format",         "value"),

    # Context fitter
    ("fit",                      "--fit",                      "onoff"),
    ("fit_ctx",                  "--fit-ctx",                  "value"),
    ("fit_target",               "--fit-target",               "value"),
]


def _emit_flag(argv: list[str], cfg_dict: dict, key: str, flag: object, kind: str) -> None:
    """Append one flag (and its argument) to argv if the key is present and non-empty."""
    if key not in cfg_dict:
        return
    val = cfg_dict[key]
    if val is None:
        return

    if kind == "flag":
        if bool(val):
            argv.append(str(flag))
        return

    if kind == "onoff":
        argv += [str(flag), "on" if bool(val) else "off"]
        return

    if kind == "bool_pair":
        pos, neg = flag  # type: ignore[misc]
        argv.append(str(pos if bool(val) else neg))
        return

    sval = str(val).strip()
    if not sval:
        return
    if kind == "path":
        argv += [str(flag), cfg.resolve_path(sval)]
        return
    if kind == "value_nz":
        try:
            if float(sval) == 0.0:
                return
        except ValueError:
            pass
    argv += [str(flag), sval]


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

    # Top-level llamacpp.* block, for server-wide flags
    gcfg = app_config.get_nested("llamacpp", {}) or {}

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

    # Server-wide flags (top-level llamacpp.*)
    for k, flag, kind in _GLOBAL_FLAG_SPECS:
        _emit_flag(argv, gcfg, k, flag, kind)
    _emit_spec_ngram(argv, gcfg)

    # Per-model flags
    for k, flag, kind in _MODEL_FLAG_SPECS:
        _emit_flag(argv, mcfg, k, flag, kind)

    # Generic escape hatch — [["--flag","value"], ["--bare-flag"], ...]
    for entry in mcfg.get("extra_args", []) or []:
        if isinstance(entry, (list, tuple)):
            argv.extend(str(x) for x in entry)
        elif isinstance(entry, str):
            argv.append(entry)

    # Top-level extra_args apply to every model
    for entry in (gcfg.get("extra_args", []) or []):
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
