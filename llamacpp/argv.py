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

import json
import logging
import re
from typing import Optional

from pathlib import Path

import config as app_config
from llamacpp import config as cfg

_LOG = logging.getLogger("telecode.llamacpp.argv")


# Kinds:
#   "value"     → --flag <str(val)> ; skip if empty string
#   "value_nz"  → --flag <str(val)> ; skip if empty string OR numeric zero.
#                 Use for knobs where 0 means "server default / disabled".
#   "path"      → --flag <resolved_path> ; skip if empty string
#   "flag"      → --flag (only if truthy); no value
#   "onoff"     → --flag on|off (tri-state; always emits if present)
#   "bool_pair" → flag is a tuple (positive, negative); emits one of the two
#                 based on truthiness. Used for --X / --no-X style pairs.
#   "pathlist"  → --flag a,b,c ; every comma-separated item resolved as a path
#   "pathscaled"→ --flag a:1.0,b:0.5 ; the path part of each item resolved, the
#                 scale (after the LAST colon) kept
#   "multi"     → --flag v1 --flag v2 ; one flag per comma/whitespace/newline item
#   "json_obj"  → --flag '<compact json>' ; skipped (warning) unless a JSON object
#   "range2"    → --flag START END ; two args from "10 20" / "10-20" / "10,20"

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
    # cpu_mask / cpu_range select which logical CPUs the thread pools may run
    # on. Hybrid Intel parts (P + E cores) enumerate the two classes
    # interleaved, so a mask is usually required where a range won't do.
    # NOTE: llama-server defaults --cpu-mask-batch to --cpu-mask. If cpu_mask
    # restricts generation to the P-cores, set cpu_mask_batch explicitly (e.g.
    # all-cores) or prompt processing inherits the same restriction.
    ("cpu_mask",         "--cpu-mask",         "value"),
    ("cpu_range",        "--cpu-range",        "value"),
    ("cpu_mask_batch",   "--cpu-mask-batch",   "value"),
    ("cpu_range_batch",  "--cpu-range-batch",  "value"),
    ("cpu_strict",       "--cpu-strict",       "value"),
    ("cpu_strict_batch", "--cpu-strict-batch", "value"),
    ("prio",             "--prio",             "value"),
    ("prio_batch",       "--prio-batch",       "value"),
    ("poll",             "--poll",             "value"),
    ("poll_batch",       "--poll-batch",       "value"),
    ("threads_http",     "--threads-http",     "value_nz"),

    # Draft-model scheduling (stock b10733+). Only meaningful with a draft model /
    # MTP head, harmless otherwise. The tray greys them unless a draft spec type is on.
    ("spec_draft_cpu_mask",         "--spec-draft-cpu-mask",         "value"),
    ("spec_draft_cpu_range",        "--spec-draft-cpu-range",        "value"),
    ("spec_draft_cpu_strict",       "--spec-draft-cpu-strict",       "value"),
    ("spec_draft_prio",             "--spec-draft-prio",             "value"),
    ("spec_draft_poll",             "--spec-draft-poll",             "value"),
    ("spec_draft_cpu_mask_batch",   "--spec-draft-cpu-mask-batch",   "value"),
    ("spec_draft_cpu_strict_batch", "--spec-draft-cpu-strict-batch", "value"),
    ("spec_draft_prio_batch",       "--spec-draft-prio-batch",       "value"),
    ("spec_draft_poll_batch",       "--spec-draft-poll-batch",       "value"),

    # Memory policy.
    # load_mode replaces --mlock / --no-mmap / --direct-io, all three of which
    # upstream deprecated in favour of a single --load-mode. The accepted values
    # are a closed set (probed against b10733): auto, none, mmap, mlock,
    # mmap+mlock, dio. Arbitrary combinations are REJECTED -- "mlock+dio" and
    # "mmap+mlock+dio" both fail to parse -- so this is a choice, not a bitmask.
    # Emitted via _emit_load_mode().
    ("no_host",          "--no-host",         "flag"),
    ("lazy_mode",        "--lazy-mode",       "value"),
    ("repack",           ("--repack", "--no-repack"),           "bool_pair"),
    ("op_offload",       ("--op-offload", "--no-op-offload"),   "bool_pair"),
    ("check_tensors",    "--check-tensors",   "flag"),

    # Hardware layout
    ("main_gpu",         "--main-gpu",        "value"),
    ("mmproj_device",    "--mmproj-device",   "value"),
    ("tensor_split",     "--tensor-split",    "value"),
    ("split_mode",       "--split-mode",      "value"),
    ("numa",             "--numa",            "value"),

    # Sampling/context defaults that apply at launch
    ("seed",             "--seed",            "value"),
    ("keep",             "--keep",            "value"),

    # Caching policy
    ("kv_offload",       ("--kv-offload", "--no-kv-offload"),   "bool_pair"),
    ("kv_unified",       ("--kv-unified", "--no-kv-unified"),   "bool_pair"),
    ("kv_unified_per_slot", "--kv-unified-per-slot",           "value_nz"),
    ("cache_prompt",     ("--cache-prompt", "--no-cache-prompt"), "bool_pair"),
    # renamed from --clear-idle/--no-clear-idle in upstream commit 9d49acb
    ("cache_idle_slots", ("--cache-idle-slots", "--no-cache-idle-slots"), "bool_pair"),
    ("context_shift",    ("--context-shift", "--no-context-shift"), "bool_pair"),
    ("warmup",           ("--warmup", "--no-warmup"),             "bool_pair"),
    # cache_ram / ctx_checkpoints / checkpoint_min_step carry meaningful zero
    # semantics, so emit literally `--flag 0` (kind "value", not "value_nz"):
    #   --cache-ram N         default 8192 MiB; -1 = no limit, 0 = disable.
    #   --ctx-checkpoints N   default 32; max context checkpoints per slot.
    #   --checkpoint-min-step N  default 256; min token spacing between
    #                            checkpoints, 0 = no minimum. (Renamed from the
    #                            removed --checkpoint-every-n-tokens.)
    # Keeping explicit emission makes intent visible in `describe()` output.
    # defrag_thold was removed in v9243 (DEPRECATED warning + no-op upstream);
    # it is not emitted.
    ("cache_ram",                  "--cache-ram",                 "value"),
    ("ctx_checkpoints",            "--ctx-checkpoints",           "value"),
    ("checkpoint_min_step",        "--checkpoint-min-step",       "value"),
    ("swa_full",                   "--swa-full",                  "flag"),
    ("slot_save_path",             "--slot-save-path",            "path"),
    ("sleep_idle_seconds",         "--sleep-idle-seconds",        "value_nz"),

    # LMCache tiered KV cache (patched build)
    ("lmcache",                     "--lmcache",                   "flag"),
    ("lmcache_chunk_size",          "--lmcache-chunk-size",        "value_nz"),
    ("lmcache_ram_mb",              "--lmcache-ram-mb",            "value"),
    ("lmcache_checkpoints",         "--lmcache-checkpoints",       "value"),
    ("lmcache_disk",                "--lmcache-disk",              "flag"),
    ("lmcache_disk_dir",            "--lmcache-disk-dir",          "path"),
    ("lmcache_disk_max_mb",         "--lmcache-disk-max-mb",       "value_nz"),
    ("lmcache_disk_idle_sec",       "--lmcache-disk-idle-sec",     "value"),

    # Speculative decoding:
    # - spec_type is dispatched in _emit_spec_type() so the "none" sentinel
    #   used by the tray (when spec_default is the active toggle) doesn't leak
    #   to llama-server as `--spec-type none`, which would activate the spec
    #   machinery without picking an implementation. Also dispatches the
    #   per-mode ngram knobs.
    # - spec_default is emitted here, but build_argv() defensively suppresses
    #   it when spec_type is meaningfully set (the two are mutually
    #   exclusive — set one or the other, not both).
    ("spec_default",             "--spec-default",         "flag"),
    ("spec_draft_n_ubatch",           "--spec-draft-n-ubatch",           "value_nz"),  # newer upstream builds only
    ("spec_draft_head_subset",        "--spec-draft-head-subset",        "value_nz"),
    ("spec_draft_head_subset_ids",    "--spec-draft-head-subset-ids",    "path"),
    ("spec_synth_len",           "--spec-synth-len",       "value_nz"),
    ("spec_synth_rates",         "--spec-synth-rates",     "value"),
    # How the draft is sampled (greedy argmax vs probabilistic + rejection sampling) and
    # whether that sampling runs on the backend (stock default: enabled).
    ("spec_draft_sampling",         "--spec-draft-sampling",   "value"),
    ("spec_draft_backend_sampling",
     ("--spec-draft-backend-sampling", "--no-spec-draft-backend-sampling"), "bool_pair"),
    ("threads_draft",            "--threads-draft",        "value_nz"),
    ("threads_batch_draft",      "--threads-batch-draft",  "value_nz"),

    # Server-wide generation cap
    ("n_predict",                "--n-predict",            "value_nz"),

    # HTTP / endpoints
    ("timeout",                  "--timeout",              "value_nz"),
    ("api_prefix",               "--api-prefix",           "value"),
    ("media_path",               "--media-path",           "path"),
    ("metrics",                  "--metrics",              "flag"),
    ("props",                    "--props",                "flag"),
    ("slots",                    ("--slots", "--no-slots"), "bool_pair"),

    # Server-mode selectors (mutually exclusive with chat mode — usually leave off)
    ("embedding",                "--embedding",            "flag"),
    ("rerank",                   "--rerank",               "flag"),
    ("pooling",                  "--pooling",              "value"),

    # Video input (b10733+). llama-server shells out to ffmpeg/ffprobe to decode
    # a video into frames at video_fps and runs them through the vision
    # projector, interleaving text timestamps so the model can reason about
    # when something happened. Needs a vision model + mmproj, and ffmpeg on
    # PATH (or video_ffmpeg_dir).
    #
    # The proxy routes video too: proxy/translate.py maps an Anthropic `video`
    # block and OpenAI's `video_url` part onto llama.cpp's `input_video`.
    ("video_fps",                "--video-fps",                "value_nz"),
    ("video_timestamp_interval", "--video-timestamp-interval", "value_nz"),
    ("video_ffmpeg_dir",         "--video-ffmpeg-dir",         "path"),

    # Diagnostic / debug
    ("skip_chat_parsing",        "--skip-chat-parsing",    "flag"),
    ("perf",                     ("--perf", "--no-perf"),  "bool_pair"),

    # Misc server behaviour
    ("prefill_assistant",        ("--prefill-assistant", "--no-prefill-assistant"), "bool_pair"),
    ("lora_init_without_apply",  "--lora-init-without-apply", "flag"),
    ("rpc",                      "--rpc",                  "value"),
    ("offline",                  "--offline",              "flag"),

    # Sampler defaults. These are llama-server's own launch defaults: a request
    # that carries a value wins; the proxy fills only the fields it knows
    # (inference_defaults). Absent key = nothing emitted = the server's default.
    ("samplers",                 "--samplers",             "value"),
    ("sampler_seq",              "--sampler-seq",          "value"),
    ("top_nsigma",               "--top-nsigma",           "value"),
    ("xtc_probability",          "--xtc-probability",      "value"),
    ("xtc_threshold",            "--xtc-threshold",        "value"),
    ("typical",                  "--typical",              "value"),
    ("repeat_last_n",            "--repeat-last-n",        "value"),
    ("dry_multiplier",           "--dry-multiplier",       "value"),
    ("dry_base",                 "--dry-base",             "value"),
    ("dry_allowed_length",       "--dry-allowed-length",   "value"),
    ("dry_penalty_last_n",       "--dry-penalty-last-n",   "value"),
    ("dry_sequence_breaker",     "--dry-sequence-breaker", "value"),
    ("dynatemp_range",           "--dynatemp-range",       "value"),
    ("dynatemp_exp",             "--dynatemp-exp",         "value"),
    ("mirostat",                 "--mirostat",             "value"),
    ("mirostat_lr",              "--mirostat-lr",          "value"),
    ("mirostat_ent",             "--mirostat-ent",         "value"),
    ("adaptive_target",          "--adaptive-target",      "value"),
    ("adaptive_decay",           "--adaptive-decay",       "value"),
]


# Per-spec-type ngram flag dispatch.
#
# Upstream replaced the single --spec-ngram-* flags with per-mode variants in
# b9145, and v9243 added n-min/n-max to ngram-mod (previously only n-match).
# Three spec types share a (lookup-n, draft-m, min-hits) shape; ngram-mod has
# its own (n-min, n-max, n-match) shape so it's handled separately.
_NGRAM_SIZE_FLAG_MAP: dict[str, tuple[str, str, str]] = {
    "ngram-simple":  ("--spec-ngram-simple-size-n",  "--spec-ngram-simple-size-m",  "--spec-ngram-simple-min-hits"),
    "ngram-map-k":   ("--spec-ngram-map-k-size-n",   "--spec-ngram-map-k-size-m",   "--spec-ngram-map-k-min-hits"),
    "ngram-map-k4v": ("--spec-ngram-map-k4v-size-n", "--spec-ngram-map-k4v-size-m", "--spec-ngram-map-k4v-min-hits"),
}


def _spec_types(gcfg: dict) -> list[str]:
    """spec_type may be a comma-separated list (v9243+); split + strip.

    Drops the "none" sentinel — the tray writes spec_type="none" when the
    user picks spec_default as the active toggle, and llama-server would
    otherwise interpret it as "enable spec decoding with no implementation".
    Empty/"none"-only values return [].
    """
    raw = str(gcfg.get("spec_type", "") or "").strip()
    if not raw:
        return []
    out = [s.strip() for s in raw.split(",") if s.strip()]
    return [s for s in out if s.lower() != "none"]


def _emit_load_mode(argv: list[str], gcfg: dict) -> None:
    """Emit --load-mode (empty = omit, llama-server's own default).

    Upstream deprecated --mlock / --no-mmap / --direct-io in favour of one
    --load-mode taking a closed set of values (probed against b10733): auto,
    none, mmap, mlock, mmap+mlock, dio. Combinations beyond mmap+mlock are
    rejected by the parser.
    """
    mode = str(gcfg.get("load_mode", "") or "").strip()
    if mode:
        argv += ["--load-mode", mode]


def _emit_spec_type(argv: list[str], gcfg: dict) -> None:
    """Emit --spec-type only when at least one real strategy is selected.

    Filtering happens via _spec_types(), which strips empty + "none" entries.
    The value is rejoined so the upstream server sees the canonical
    comma-separated form.
    """
    active = _spec_types(gcfg)
    if not active:
        return
    argv += ["--spec-type", ",".join(active)]


def _emit_spec_ngram(argv: list[str], gcfg: dict) -> None:
    """Emit the per-spec-type ngram knobs.

    For ngram-simple / ngram-map-k / ngram-map-k4v: dispatch from
    `spec_ngram_size_n/_m/_min_hits` (server-wide).

    For ngram-mod: read `spec_ngram_mod_n_min/_max/_match` (0 = llama-server's
    default).
    """
    active = _spec_types(gcfg)

    for st in active:
        mapping = _NGRAM_SIZE_FLAG_MAP.get(st)
        if not mapping:
            continue
        n_flag, m_flag, h_flag = mapping
        for key, flag in (("spec_ngram_size_n",   n_flag),
                          ("spec_ngram_size_m",   m_flag),
                          ("spec_ngram_min_hits", h_flag)):
            val = gcfg.get(key)
            if val in (None, "", 0, "0"):
                continue
            argv += [flag, str(val)]

    if "ngram-mod" in active:
        for key_or_val, flag in (
            (gcfg.get("spec_ngram_mod_n_min"),   "--spec-ngram-mod-n-min"),
            (gcfg.get("spec_ngram_mod_n_max"),   "--spec-ngram-mod-n-max"),
            (gcfg.get("spec_ngram_mod_n_match"), "--spec-ngram-mod-n-match"),
        ):
            if key_or_val in (None, "", 0, "0"):
                continue
            argv += [flag, str(key_or_val)]


# Per-model flags read from llamacpp.models.<m>.*
_MODEL_FLAG_SPECS: list[tuple[str, object, str]] = [
    # Capacity
    ("ctx_size",      "--ctx-size",        "value"),
    ("n_gpu_layers",  "--n-gpu-layers",    "value"),
    ("device",        "--device",          "value"),
    ("batch_size",    "--batch-size",      "value_nz"),

    # Attention
    ("flash_attn",    "--flash-attn",      "value"),

    # KV cache dtypes (model-sensitive)
    ("cache_type_k",  "--cache-type-k",    "value"),
    ("cache_type_v",  "--cache-type-v",    "value"),
    ("cache_reuse",   "--cache-reuse",     "value"),

    # MoE
    ("n_cpu_moe",     "--n-cpu-moe",       "value"),
    ("n_cpu_ffn",     "--n-cpu-ffn",       "value_nz"),
    ("cpu_moe",       "--cpu-moe",         "flag"),

    ("moe_cache_mib",                   "--moe-cache-mib",                   "value_nz"),
    # MoE hybrid mode (patches/llama.cpp/0002-moe-hybrid-strata.patch)
    ("moe_hybrid_mib",                  "--moe-hybrid-mib",                  "value_nz"),
    ("moe_hybrid_max_tokens",           "--moe-hybrid-max-tokens",           "value_nz"),
    ("moe_hybrid_swap_every",           "--moe-hybrid-swap-every",           "value_nz"),
    ("moe_hybrid_swap_max",             "--moe-hybrid-swap-max",             "value"),
    ("moe_hybrid_skip_weight",          "--moe-hybrid-skip-weight",          "value"),
    ("moe_hybrid_tail_skip",            "--moe-hybrid-tail-skip",            "value"),
    ("spec_draft_head_subset",          "--spec-draft-head-subset",          "value_nz"),
    ("spec_draft_head_subset_ids",      "--spec-draft-head-subset-ids",      "path"),
    ("ubatch_size",                     "--ubatch-size",                     "value_nz"),
    ("spec_draft_n_ubatch",             "--spec-draft-n-ubatch",             "value_nz"),

    # RoPE / YaRN (0 = model default; YaRN factors default to -1.00 = auto,
    # so emit only when explicitly set to a non-zero override).
    ("rope_scaling",     "--rope-scaling",      "value"),
    ("rope_freq_base",   "--rope-freq-base",    "value_nz"),
    ("rope_freq_scale",  "--rope-freq-scale",   "value_nz"),
    # rope_scale is the reciprocal spelling of rope_freq_scale; build_argv drops it
    # when rope_freq_scale is also set so the two never contradict each other.
    ("rope_scale",       "--rope-scale",        "value_nz"),
    ("yarn_orig_ctx",    "--yarn-orig-ctx",     "value_nz"),
    ("yarn_ext_factor",  "--yarn-ext-factor",   "value_nz"),
    ("yarn_attn_factor", "--yarn-attn-factor",  "value_nz"),
    ("yarn_beta_slow",   "--yarn-beta-slow",    "value_nz"),
    ("yarn_beta_fast",   "--yarn-beta-fast",    "value_nz"),

    # Chat template
    ("chat_template",      "--chat-template",       "value"),
    ("chat_template_file", "--chat-template-file",  "path"),
    ("jinja",              "--jinja",               "flag"),

    # Vision
    ("mmproj",            "--mmproj",            "path"),
    ("mmproj_auto",       ("--mmproj-auto", "--no-mmproj-auto"), "bool_pair"),
    ("mtmd_batch_max_tokens", "--mtmd-batch-max-tokens", "value_nz"),
    ("mmproj_offload",    ("--mmproj-offload", "--no-mmproj-offload"), "bool_pair"),
    ("image_min_tokens",  "--image-min-tokens",  "value_nz"),
    ("image_max_tokens",  "--image-max-tokens",  "value_nz"),

    # Draft model (paired with main model). Upstream b9145 dropped
    # --draft-max/--draft-min/--ctx-size-draft; the replacements are the
    # --spec-draft-* family. v9243 added cpu-moe-draft / n-cpu-moe-draft for
    # offloading MoE experts of the draft model, and exposed --spec-draft-p-split.
    # v9243 also changed defaults: --spec-draft-n-max 16→3, --spec-draft-p-min 0.75→0.
    ("draft_model",         "--model-draft",        "path"),
    ("n_gpu_layers_draft",  "--n-gpu-layers-draft", "value_nz"),
    ("cache_type_k_draft",  "--cache-type-k-draft", "value"),
    ("cache_type_v_draft",  "--cache-type-v-draft", "value"),
    ("device_draft",                "--device-draft",                 "value"),
    ("cpu_moe_draft",               "--cpu-moe-draft",                "flag"),
    ("n_cpu_moe_draft",             "--n-cpu-moe-draft",              "value_nz"),
    ("draft_n",                     "--spec-draft-n-max",             "value"),
    ("draft_n_min",                 "--spec-draft-n-min",             "value"),
    ("draft_p_min",                 "--spec-draft-p-min",             "value"),
    ("draft_p_split",               "--spec-draft-p-split",           "value_nz"),
    ("spec_draft_override_tensor",  "--spec-draft-override-tensor",   "value"),
    ("spec_draft_sampling",         "--spec-draft-sampling",          "value"),
    ("spec_draft_backend_sampling",
     ("--spec-draft-backend-sampling", "--no-spec-draft-backend-sampling"), "bool_pair"),
    ("threads_draft",               "--threads-draft",                "value_nz"),
    ("threads_batch_draft",         "--threads-batch-draft",          "value_nz"),
    ("spec_draft_cpu_mask",         "--spec-draft-cpu-mask",          "value"),
    ("spec_draft_cpu_range",        "--spec-draft-cpu-range",         "value"),
    ("spec_draft_cpu_strict",       "--spec-draft-cpu-strict",        "value"),
    ("spec_draft_prio",             "--spec-draft-prio",              "value"),
    ("spec_draft_poll",             "--spec-draft-poll",              "value"),
    ("spec_draft_cpu_mask_batch",   "--spec-draft-cpu-mask-batch",    "value"),
    ("spec_draft_cpu_strict_batch", "--spec-draft-cpu-strict-batch",  "value"),
    ("spec_draft_prio_batch",       "--spec-draft-prio-batch",        "value"),
    ("spec_draft_poll_batch",       "--spec-draft-poll-batch",        "value"),

    # N-gram lookup cache (only active when spec_type=ngram-cache; server
    # does not implement save, so dynamic file is never written — load only)
    ("lookup_cache_static",  "--lookup-cache-static",  "path"),
    ("lookup_cache_dynamic", "--lookup-cache-dynamic", "path"),

    # LoRA
    ("lora",          "--lora",            "path"),
    ("lora_scale",    "--lora-scaled",     "value"),

    # Control vectors (steering). control_vector takes comma-separated paths;
    # control_vector_scaled takes path:scale,...; the layer range is "START END".
    ("control_vector",              "--control-vector",              "pathlist"),
    ("control_vector_scaled",       "--control-vector-scaled",       "pathscaled"),
    ("control_vector_layer_range",  "--control-vector-layer-range",  "range2"),

    # Generation defaults of the server (a request's own value wins).
    ("logit_bias",    "--logit-bias",      "multi"),
    ("ignore_eos",    "--ignore-eos",      "flag"),

    # Grammar
    ("grammar",       "--grammar",         "value"),
    ("grammar_file",  "--grammar-file",    "path"),

    # Reasoning (--reasoning is the on/off/auto master toggle; budget knobs cap it)
    ("reasoning",                "--reasoning",                "value"),
    ("reasoning_budget",         "--reasoning-budget",         "value"),
    ("reasoning_budget_message", "--reasoning-budget-message", "value"),
    ("reasoning_format",         "--reasoning-format",         "value"),
    ("reasoning_effort",         "--reasoning-effort",         "value"),
    ("chat_template_kwargs",     "--chat-template-kwargs",     "json_obj"),
    # llama-server nags for this one at every startup when the template
    # supports it: "chat template supports preserving reasoning, consider
    # enabling it via --reasoning-preserve". Server-side twin of Qwen's
    # `preserve_thinking` template var and of proxy drop_prior_thinking.
    ("reasoning_preserve", ("--reasoning-preserve", "--no-reasoning-preserve"), "bool_pair"),

    # Advanced placement
    ("override_tensor",  "--override-tensor",  "value"),
    ("override_kv",      "--override-kv",      "value"),

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

    if key == "flash_attn":
        if isinstance(val, bool):
            sval = "on" if val else "off"
        elif str(val).lower() == "true":
            sval = "on"
        elif str(val).lower() == "false":
            sval = "off"
        else:
            sval = str(val).strip().lower()
        if sval not in ("on", "off", "auto"):
            sval = "auto"
        argv += [str(flag), sval]
        return

    if key in ("cpu_strict", "cpu_strict_batch", "spec_draft_cpu_strict",
               "spec_draft_cpu_strict_batch", "spec_draft_poll", "spec_draft_poll_batch"):
        sval = "1" if bool(val) else "0"
        argv += [str(flag), sval]
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
    if kind == "pathlist":
        items = [cfg.resolve_path(x.strip()) for x in sval.split(",") if x.strip()]
        if items:
            argv += [str(flag), ",".join(items)]
        return
    if kind == "pathscaled":
        items = []
        for x in sval.split(","):
            x = x.strip()
            if not x:
                continue
            name, sep, scale = x.rpartition(":")
            # "D:/v.gguf" has a colon but no scale: the tail is not a number
            try:
                float(scale)
            except ValueError:
                name, sep, scale = x, "", ""
            items.append(cfg.resolve_path(name.strip()) + (f":{scale.strip()}" if sep else ""))
        if items:
            argv += [str(flag), ",".join(items)]
        return
    if kind == "multi":
        for item in re.split(r"[,\s]+", sval):
            if item:
                argv += [str(flag), item]
        return
    if kind == "json_obj":
        try:
            obj = json.loads(sval)
        except ValueError as exc:
            _LOG.warning("llamacpp: %s is not valid JSON (%s); %s not emitted", key, exc, flag)
            return
        if not isinstance(obj, dict):
            _LOG.warning("llamacpp: %s must be a JSON object; %s not emitted", key, flag)
            return
        argv += [str(flag), json.dumps(obj, separators=(",", ":"))]
        return
    if kind == "range2":
        parts = [x for x in re.split(r"[\s,]+|(?<=\d)-(?=\d)", sval) if x]
        if len(parts) != 2 or not all(x.lstrip("-").isdigit() for x in parts):
            _LOG.warning("llamacpp: %s needs 'START END' (got %r); %s not emitted", key, sval, flag)
            return
        argv += [str(flag), parts[0], parts[1]]
        return
    if kind == "value_nz":
        try:
            if float(sval) == 0.0:
                return
        except ValueError:
            pass
    argv += [str(flag), sval]


def build_argv(model_name: str, overrides: Optional[dict] = None) -> list[str]:
    """Produce the full llama-server argv for a given registered model.

    `overrides` replaces model keys for this call only (the supervisor uses it for the automatic cache size).
    Raises KeyError if the model isn't registered and doesn't resolve to one.
    """
    resolved = cfg.resolve_model(model_name)
    if not resolved:
        raise KeyError(f"No model registered that matches '{model_name}'")

    mcfg = cfg.model_cfg(resolved)
    if overrides:
        mcfg = {**mcfg, **overrides}

    # MoE hybrid mode: every layer's experts stay in host memory, the GPU keeps the most used ones
    hybrid = bool(mcfg.get("moe_hybrid"))
    if hybrid:
        mcfg = {**mcfg, "cpu_moe": True}
    model_path = mcfg.get("path")
    if not model_path:
        raise KeyError(f"llamacpp.models.{resolved}.path is required")

    # Top-level llamacpp.* block, for server-wide flags
    gcfg = app_config.get_nested("llamacpp", {}) or {}
    if "spec_type" in mcfg:
        gcfg = {**gcfg, "spec_type": mcfg["spec_type"]}
    if "spec_default" in mcfg:
        gcfg = {**gcfg, "spec_default": mcfg["spec_default"]}

    argv: list[str] = [cfg.binary()]

    # Server-wide binding
    argv += ["--host", cfg.host()]
    argv += ["--port", str(cfg.port())]

    # API key (optional)
    key = cfg.api_key()
    if key:
        argv += ["--api-key", key]

    # Disable the built-in web UI — we don't want it exposed on the same
    # port the proxy talks to. v9243 renamed --no-webui to --no-ui (the old
    # name still works with a deprecation warning).
    argv.append("--no-ui")

    # Main model
    argv += ["--model", cfg.resolve_path(model_path)]

    # Server-wide flags (top-level llamacpp.*)
    spec_active = bool(_spec_types(gcfg))
    suppressed_global: set[str] = set()
    # spec_default and spec_type are mutually exclusive — if spec_type names
    # a real strategy, drop spec_default even if it was left truthy in
    # settings.json (defensive mirror of _mutex_spec_default_vs_type in the
    # tray, in case settings.json was hand-edited).
    if spec_active:
        suppressed_global.add("spec_default")
    if not (spec_active or gcfg.get("spec_default")):
        suppressed_global.add("spec_draft_n_ubatch")
    if not gcfg.get("lmcache"):
        suppressed_global.update({"lmcache", "lmcache_chunk_size", "lmcache_ram_mb", "lmcache_checkpoints", "lmcache_disk",
                                  "lmcache_disk_dir", "lmcache_disk_max_mb", "lmcache_disk_idle_sec"})
    elif not gcfg.get("lmcache_disk"):
        suppressed_global.update({"lmcache_disk_dir", "lmcache_disk_max_mb", "lmcache_disk_idle_sec"})
    for k, flag, kind in _GLOBAL_FLAG_SPECS:
        if k in mcfg:
            suppressed_global.add(k)
        if k in suppressed_global:
            continue
        _emit_flag(argv, gcfg, k, flag, kind)
    _emit_load_mode(argv, gcfg)
    _emit_spec_type(argv, gcfg)
    _emit_spec_ngram(argv, gcfg)

    # Context sizing
    mcfg = dict(mcfg)
    if overrides and "ctx_size" in overrides:
        mcfg["ctx_size"] = overrides["ctx_size"]
    else:
        eff_ctx = cfg.effective_ctx_size(resolved)
        if eff_ctx > 0:
            mcfg["ctx_size"] = eff_ctx

    # Per-model flags. When fit is on, suppress n_gpu_layers / n_gpu_layers_draft —
    # llama-server's auto-fitter aborts the moment it sees a user-pinned ngl
    # ("n_gpu_layers already set by user to N, abort"), leaving the model
    # in a degraded layout. fit and explicit ngl are mutually exclusive.
    suppressed_model: set[str] = set()
    if bool(mcfg.get("fit")):
        suppressed_model.update({"n_gpu_layers", "n_gpu_layers_draft"})
        # a VRAM budget instead of a margin: the fitter may use at most this much of the card (the weights, the KV cache, the draft / MTP context
        # and the head subset all count, the stock fitter measures them); --fit-target is what it leaves free
        budget = cfg.as_int(mcfg.get("vram_budget_mib"))
        if budget > 0 and not cfg.as_int(mcfg.get("fit_target")):
            from llamacpp import vram
            total = vram.total_vram_mib()
            if total:
                mcfg["fit_target"] = max(0, total - budget)
    if hybrid:
        suppressed_model.update({"n_cpu_moe", "moe_cache_mib"})   # all layers on the CPU; the MoE cache excludes the hybrid mode
    else:
        suppressed_model.update({"moe_hybrid_mib", "moe_hybrid_max_tokens", "moe_hybrid_swap_every", "moe_hybrid_swap_max",
                                 "moe_hybrid_skip_weight", "moe_hybrid_tail_skip"})
    # rope_scale and rope_freq_scale are the same lever; the older key wins.
    if cfg.as_float(mcfg.get("rope_freq_scale")) != 0.0:
        suppressed_model.add("rope_scale")
    for k, flag, kind in _MODEL_FLAG_SPECS:
        if k in suppressed_model:
            continue
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

    return _drop_unsupported(argv)


# flags that only a patched llama.cpp (llamacpp/patcher.py) or a recent build has: a binary that does not list them in --help refuses to start
_PATCHED_PREFIXES = ("--moe-cache-", "--moe-hybrid-", "--lmcache", "--moe-expert-reduce", "--spec-draft-n-ubatch", "--spec-draft-adaptive", "--spec-draft-head-subset")
_HELP_FLAGS: dict[tuple, Optional[set]] = {}


def _binary_flags(binary: str) -> Optional[set]:
    """The long flags `binary --help` lists, cached per file (path, size, mtime); None when it cannot be asked."""
    import os
    import shutil
    import subprocess
    exe = shutil.which(binary) or binary
    try:
        st = os.stat(exe)
    except OSError:
        return None
    key = (exe, st.st_size, int(st.st_mtime))
    if key not in _HELP_FLAGS:
        flags = None
        try:
            out = subprocess.run([exe, "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            found = set(re.findall(r"(?<![\w-])(--[a-z0-9][a-z0-9-]*)", out))
            flags = found if len(found) > 50 else None  # a real help text lists hundreds
        except Exception as exc:  # no binary yet, timeout: do not filter
            _LOG.debug("cannot read --help of %s: %s", exe, exc)
        _HELP_FLAGS[key] = flags
    return _HELP_FLAGS[key]


def _drop_unsupported(argv: list[str]) -> list[str]:
    """Remove the add-on flags (and their values) that the binary does not know, so a stock binary still starts."""
    if not any(a.startswith(_PATCHED_PREFIXES) for a in argv[1:]):
        return argv
    have = _binary_flags(argv[0])
    if not have:
        return argv
    out = [argv[0]]
    i = 1
    while i < len(argv):
        a = argv[i]
        if a.startswith(_PATCHED_PREFIXES) and a not in have:
            _LOG.warning("llamacpp: %s is not known to %s (stock build?), leaving it out", a, argv[0])
            i += 1
            if i < len(argv) and not argv[i].startswith("-"):
                i += 1
            continue
        out.append(a)
        i += 1
    return out


def describe(model_name: str, overrides: Optional[dict] = None) -> str:
    """Human-readable argv preview for logs."""
    try:
        argv = build_argv(model_name, overrides)
    except KeyError as exc:
        return f"<{exc}>"
    # Quote args containing spaces for copy-paste
    return " ".join(
        f'"{a}"' if " " in a else a for a in argv
    )
