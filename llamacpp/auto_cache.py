"""Automatic size of the MoE expert cache (`llamacpp.models.<m>.moe_cache_auto`).

The cache is VRAM that must fit next to the dense weights, the KV cache and the compute buffers; guessing it wrong costs
a failed start (out of memory) or, on Windows, a model that spills into shared memory and runs several times slower.
So the size is worked out at every spawn from what is free right now, and the supervisor retries smaller when the
server runs out of memory anyway.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Optional

import config as app_config
from llamacpp import config as cfg
from llamacpp import gguf_info, vram

_LOG = logging.getLogger("telecode.llamacpp.auto_cache")

# what llama-server / ggml print when an allocation on the GPU fails
_OOM = re.compile(r"out of memory|failed to allocate|cudamalloc failed|unable to allocate|cuda error: .*memory|"
                  r"alloc_tensor_range", re.I)


def enabled(model_name: str) -> bool:
    m = cfg.model_cfg(model_name)
    return bool(m.get("moe_hybrid_auto") if m.get("moe_hybrid") else m.get("moe_cache_auto"))


def target_key(model_name: str) -> str:
    """The setting the plan sizes: the hybrid mode's slots or the MoE cache."""
    return "moe_hybrid_mib" if cfg.model_cfg(model_name).get("moe_hybrid") else "moe_cache_mib" 


def headroom_mib(model_name: str) -> int:
    try:
        return max(0, int(cfg.model_cfg(model_name).get("moe_cache_auto_headroom_mib", 1500) or 0))
    except (TypeError, ValueError):
        return 1500


# bytes per element of the KV cache types relative to f16
_KV_TYPE = {"f32": 2.0, "f16": 1.0, "bf16": 1.0, "q8_0": 0.53, "q5_1": 0.38, "q5_0": 0.34, "q4_1": 0.31, "q4_0": 0.28, "iq4_nl": 0.28}


def kv_factor(model_name: str) -> float:
    m = cfg.model_cfg(model_name)
    k = _KV_TYPE.get(str(m.get("cache_type_k") or "f16").lower(), 1.0)
    v = _KV_TYPE.get(str(m.get("cache_type_v") or "f16").lower(), 1.0)
    return (k + v)/2


def _free_mib() -> Optional[int]:
    """Free VRAM now; the previous model may still be releasing it, so take the best of a few readings."""
    best: Optional[int] = None
    for i in range(3):
        v = vram.free_vram_mib()
        if v is None:
            return best
        best = v if best is None else max(best, v)
        if i < 2:
            time.sleep(1.0)
    return best


def plan(model_name: str, scale: float = 1.0) -> Optional[dict]:
    """The settings to override for this spawn, e.g. {"moe_cache_mib": 4200}, or None to leave the config alone.

    scale < 1 is applied after an out-of-memory start. A configured moe_cache_mib above 0 is a ceiling."""
    mcfg = cfg.model_cfg(model_name)
    if not enabled(model_name):
        return None
    key = target_key(model_name)
    free = _free_mib()
    if free is None:
        _LOG.info("auto cache: free VRAM unknown (no nvidia-smi), using moe_cache_mib as configured")
        return None
    path = mcfg.get("path")
    if not path:
        return None
    # Respect vram_budget_mib and vram_ceiling_mib
    overhead = cfg.preflight_overhead_mib()
    ceiling = cfg.vram_ceiling_mib()
    budget = cfg.as_int(mcfg.get("vram_budget_mib"))
    limit = budget if budget > 0 else ceiling
    if limit > 0:
        free = min(free, max(0, limit - overhead))

    headroom = headroom_mib(model_name)
    ubatch = cfg.as_int(mcfg.get("ubatch_size")) or cfg.as_int(app_config.get_nested("llamacpp.ubatch_size")) or 512
    parallel = cfg.as_int(mcfg.get("parallel")) or cfg.as_int(app_config.get_nested("llamacpp.parallel")) or 1
    extra_headroom = 0
    if ubatch >= 2048:
        extra_headroom += 1000 if ubatch >= 4096 else 500
    if parallel > 1:
        extra_headroom += (parallel - 1) * 350
    headroom += extra_headroom
    try:
        # the suggestion plans with 1500 MiB of headroom: shift the free memory so that it means the configured headroom
        # ctx_size 0 means the trained context for llama-server: plan the KV cache for that, not for 8192
        s = gguf_info.summarize(str(cfg.resolve_path(path)), ctx_size=cfg.effective_ctx_size(model_name),
                                free_vram_mib=free + 1500 - headroom, kv_factor=kv_factor(model_name), unset_ctx_is_trained=True)
    except Exception as exc:
        _LOG.warning("auto cache: cannot read %s: %s", path, exc)
        return None
    sg = s.get("suggested")
    if not sg:
        return None  # not a MoE model

    # memory left after the dense weights, the KV cache and the headroom (the suggestion keeps 70% of it for the cache)
    avail = float(sg["moe_cache_mib"]) / 0.7
    layers = s.get("expert_bytes_per_layer") or {}
    n_moe = len(layers) or int(s.get("n_layers") or 0)
    if n_moe:
        # experts of layers that are not offloaded sit on the GPU too
        # n_cpu_moe 0 or absent = no expert layer on the CPU (llama.cpp's meaning), so every layer's experts sit on the GPU
        try:
            n_cpu = n_moe if (mcfg.get("cpu_moe") or mcfg.get("moe_hybrid")) else max(0, min(n_moe, cfg.as_int(mcfg.get("n_cpu_moe"))))
        except (TypeError, ValueError):
            n_cpu = 0
        avail -= max(0, n_moe - n_cpu) * float(s["expert_bytes_total"]) / n_moe / (1 << 20)
    factor = 0.88 if mcfg.get("moe_hybrid") else 0.7
    mib = int(max(0.0, avail) * factor * max(0.05, min(1.0, scale)))
    mib = (mib // 64) * 64
    cap = cfg.as_int(mcfg.get(key))
    if cap > 0:
        mib = min(mib, cap)
    if mib < 256:
        mib = 0
    _LOG.info("auto cache: %d MiB free, ctx %d, headroom %d, scale %.2f -> %s %d%s",
              free, sg["ctx_size"], headroom, scale, key, mib, f" (ceiling {cap})" if cap > 0 else "")
    return {key: mib}


def log_says_oom(log_path: str, tail_bytes: int = 96 * 1024) -> bool:
    """Whether the end of llama.log looks like a failed GPU allocation."""
    try:
        p = Path(log_path)
        with p.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - tail_bytes))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    # only the last spawn: the log is appended to across spawns
    marker = text.rfind("===== telecode: spawning llama-server")
    if marker >= 0:
        text = text[marker:]
    return bool(_OOM.search(text))
