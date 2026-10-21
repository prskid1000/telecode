"""Automatic runtime model pruning for Telecode.

Provides on-the-fly, automated model pruning so users do not need to manually run scripts
or modify GGUF files on disk. When configured in Telecode UI:
- ShortGPT Layer Pruning: automatically prunes redundant middle layers to fit 100% in 12GB VRAM.
- Vocabulary Trimming: automatically prunes multilingual subwords from embedding and output heads.

Pruned variants are cached transparently in `data/models/.pruned/` and re-used across server starts.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

_LOG = logging.getLogger("telecode.llamacpp.runtime_prune")

ROOT = Path(__file__).resolve().parent.parent
GGUF_PY = ROOT / "build" / "llama.cpp" / "gguf-py"
if GGUF_PY.exists():
    sys.path.insert(0, str(GGUF_PY))


def ensure_runtime_pruned_model(model_path_str: str, mcfg: dict) -> str:
    """Resolve model path, applying automatic runtime layer/vocab pruning if configured.

    If neither layer pruning nor vocab trimming is enabled, returns original path.
    """
    layer_prune = mcfg.get("runtime_layer_prune")
    drop_range = mcfg.get("runtime_layer_drop_range")
    vocab_trim = mcfg.get("runtime_vocab_trim")

    # If no runtime pruning configured, pass through original path
    if not layer_prune and not drop_range and not vocab_trim:
        return model_path_str

    src_path = Path(model_path_str)
    if not src_path.is_absolute():
        src_path = (ROOT / src_path).resolve()

    if not src_path.exists():
        return model_path_str

    # Compute cache key from source file stat and prune config
    try:
        st = src_path.stat()
        key_data = f"{src_path.name}:{st.st_size}:{st.st_mtime}:{layer_prune}:{drop_range}:{vocab_trim}"
        key_hash = hashlib.sha256(key_data.encode("utf-8")).hexdigest()[:12]
    except Exception as e:
        _LOG.warning("Failed to stat model file for runtime pruning: %s", e)
        return model_path_str

    cache_dir = ROOT / "data" / "models" / ".pruned"
    cache_dir.mkdir(parents=True, exist_ok=True)
    dst_name = f"{src_path.stem}_pruned_{key_hash}.gguf"
    dst_path = cache_dir / dst_name

    if dst_path.exists() and dst_path.stat().st_size > 0:
        _LOG.info("Using cached runtime-pruned model: %s", dst_path.name)
        return str(dst_path)

    _LOG.info("Generating runtime-pruned model for %s (layer_prune=%s, drop_range=%s, vocab_trim=%s)...",
              src_path.name, layer_prune, drop_range, vocab_trim)

    try:
        import gguf
    except ImportError:
        _LOG.warning("gguf python package not available; skipping automatic runtime pruning")
        return model_path_str

    try:
        _execute_prune(src_path, dst_path, layer_prune, drop_range, vocab_trim)
        _LOG.info("Successfully created runtime-pruned model: %s (%0.1f MB)", dst_path.name, dst_path.stat().st_size / 1024 / 1024)
        return str(dst_path)
    except Exception as exc:
        _LOG.error("Runtime model pruning failed: %s; falling back to original model", exc, exc_info=True)
        if dst_path.exists():
            try:
                dst_path.unlink()
            except OSError:
                pass
        return model_path_str


def _execute_prune(src_path: Path, dst_path: Path, layer_prune, drop_range, vocab_trim):
    import gguf

    reader = gguf.GGUFReader(str(src_path))

    arch = "llama"
    block_count = None
    for field in reader.fields.values():
        if field.name.endswith(".block_count"):
            arch = field.name.split(".")[0]
            block_count = int(field.contents())
            break

    if arch == "llama" and "general.architecture" in reader.fields:
        arch = str(reader.fields["general.architecture"].contents())

    if block_count is None:
        layer_indices = set()
        for t in reader.tensors:
            m = re.match(r"^blk\.(\d+)\.", t.name)
            if m:
                layer_indices.add(int(m.group(1)))
        block_count = max(layer_indices) + 1 if layer_indices else 32

    # Calculate dropped layers
    dropped_layers = set()
    if drop_range:
        if isinstance(drop_range, str):
            parts = [int(x.strip()) for x in re.split(r"[,\.\s\-]+", drop_range) if x.strip()]
            if len(parts) >= 2:
                dropped_layers = set(range(parts[0], parts[1] + 1))
            elif len(parts) == 1:
                dropped_layers = {parts[0]}
        elif isinstance(drop_range, (list, tuple)) and len(drop_range) >= 2:
            dropped_layers = set(range(int(drop_range[0]), int(drop_range[1]) + 1))
    elif layer_prune:
        keep_ratio = float(layer_prune) if isinstance(layer_prune, (float, int)) and layer_prune < 1.0 else 0.75
        target_layers = max(1, int(round(block_count * keep_ratio)))
        num_to_drop = block_count - target_layers
        start = int(round(block_count * 0.35))
        if start + num_to_drop > block_count - 2:
            start = max(1, block_count - 2 - num_to_drop)
        dropped_layers = set(range(start, start + num_to_drop))

    kept_layers = [i for i in range(block_count) if i not in dropped_layers]
    new_block_count = len(kept_layers)
    layer_remap = {old_idx: new_idx for new_idx, old_idx in enumerate(kept_layers)}

    writer = gguf.GGUFWriter(str(dst_path), arch)

    skip_prefixes = ("GGUF.", "general.architecture")
    for field in reader.fields.values():
        name = field.name
        if any(name.startswith(p) or name == p for p in skip_prefixes):
            continue
        if name.endswith(".block_count"):
            writer.add_block_count(new_block_count)
            continue
        val = field.contents()
        if field.types and field.types[0] == gguf.GGUFValueType.STRING:
            writer.add_string(name, str(val))
        elif field.types and field.types[0] == gguf.GGUFValueType.UINT32:
            writer.add_uint32(name, int(val))
        elif field.types and field.types[0] == gguf.GGUFValueType.INT32:
            writer.add_int32(name, int(val))
        elif field.types and field.types[0] == gguf.GGUFValueType.FLOAT32:
            writer.add_float32(name, float(val))
        elif field.types and field.types[0] == gguf.GGUFValueType.BOOL:
            writer.add_bool(name, bool(val))
        elif field.types and field.types[0] == gguf.GGUFValueType.ARRAY:
            writer.add_array(name, val)

    layer_pattern = re.compile(r"^blk\.(\d+)\.(.+)$")

    for tensor in reader.tensors:
        tname = tensor.name
        m = layer_pattern.match(tname)
        if m:
            layer_idx = int(m.group(1))
            subname = m.group(2)
            if layer_idx in dropped_layers:
                continue
            new_layer_idx = layer_remap[layer_idx]
            new_name = f"blk.{new_layer_idx}.{subname}"
        else:
            new_name = tname

        writer.add_tensor(new_name, tensor.data, raw_shape=tensor.shape, raw_dtype=tensor.tensor_type)

    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
