#!/usr/bin/env python3
"""ShortGPT Structural Layer Pruner for GGUF models.

Prunes redundant middle transformer layers from dense LLMs based on ShortGPT (Men et al., ACL 2025).
Deep dense models (27B, 32B, 70B) exhibit high redundancy in their middle layers. Removing 20-25%
of middle layers shrinks a 27B model to ~21B or a 32B model to ~24B, allowing it to fit 100% inside
12GB VRAM without CPU offload and run at full GPU decoding speeds (35-45+ tok/s).

Usage:
  # Drop middle layers automatically by keep-ratio (e.g. 0.75 keeps 75% of layers):
  python tools/prune_layers.py --in models/qwen2.5-32b-q4.gguf --out models/qwen2.5-24b-pruned.gguf --keep-ratio 0.75

  # Drop explicit layer range (e.g. layers 16 to 23 inclusive):
  python tools/prune_layers.py --in models/qwen2.5-32b-q4.gguf --out models/qwen2.5-24b-pruned.gguf --drop-range 16 23

  # Drop explicit comma-separated layers:
  python tools/prune_layers.py --in models/model.gguf --out models/pruned.gguf --drop-layers 14,15,16,17,18,19,20,21
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Add gguf-py to path if available in local checkout
ROOT = Path(__file__).resolve().parent.parent
GGUF_PY = ROOT / "build" / "llama.cpp" / "gguf-py"
if GGUF_PY.exists():
    sys.path.insert(0, str(GGUF_PY))

try:
    import gguf
except ImportError:
    print("Error: gguf python package not found. Ensure build/llama.cpp/gguf-py exists or install via pip install gguf.", file=sys.stderr)
    sys.exit(1)


def parse_args():
    parser = argparse.ArgumentParser(description="ShortGPT Structural Layer Pruner for GGUF models")
    parser.add_argument("--in", "-i", dest="input_path", required=True, help="Path to input GGUF file")
    parser.add_argument("--out", "-o", dest="output_path", required=True, help="Path to output pruned GGUF file")
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--keep-ratio", type=float, help="Ratio of layers to keep (e.g. 0.75 or 0.80). Middle layers are dropped.")
    group.add_argument("--drop-range", type=int, nargs=2, metavar=("START", "END"), help="Start and end layer index to drop inclusive (e.g. 16 23)")
    group.add_argument("--drop-layers", type=str, help="Comma-separated list of layer indices to drop (e.g. '14,15,16,17')")

    return parser.parse_args()


def calculate_drop_layers(total_layers: int, args) -> set[int]:
    if args.drop_layers:
        dropped = {int(x.strip()) for x in args.drop_layers.split(",") if x.strip()}
    elif args.drop_range:
        start, end = args.drop_range
        dropped = set(range(start, end + 1))
    elif args.keep_ratio:
        if not (0.1 <= args.keep_ratio < 1.0):
            raise ValueError("--keep-ratio must be between 0.1 and 1.0")
        target_layers = max(1, int(round(total_layers * args.keep_ratio)))
        num_to_drop = total_layers - target_layers
        # ShortGPT heuristic: early layers (first ~25%) and final layers (last ~15%) are critical.
        # Middle layers (~30% to 70% depth) have the lowest block influence.
        start = int(round(total_layers * 0.35))
        if start + num_to_drop > total_layers - 2:
            start = max(1, total_layers - 2 - num_to_drop)
        dropped = set(range(start, start + num_to_drop))
    else:
        raise ValueError("No layer pruning strategy specified")

    for idx in dropped:
        if idx < 0 or idx >= total_layers:
            raise ValueError(f"Layer index {idx} out of range [0, {total_layers - 1}]")
    
    return dropped


def main():
    args = parse_args()
    input_file = Path(args.input_path)
    output_file = Path(args.output_path)

    if not input_file.exists():
        print(f"Error: input file {input_file} not found.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading GGUF: {input_file} ...")
    reader = gguf.GGUFReader(str(input_file))

    # Detect architecture and block count
    arch = None
    block_count = None
    for field in reader.fields.values():
        if field.name.endswith(".block_count"):
            arch = field.name.split(".")[0]
            block_count = int(field.contents())
            break

    if arch is None and "general.architecture" in reader.fields:
        arch = str(reader.fields["general.architecture"].contents())

    if block_count is None:
        # Fallback to inspecting blk.<N> tensors
        layer_indices = set()
        for t in reader.tensors:
            m = re.match(r"^blk\.(\d+)\.", t.name)
            if m:
                layer_indices.add(int(m.group(1)))
        if layer_indices:
            block_count = max(layer_indices) + 1
            if arch is None:
                arch = "llama"
        else:
            print("Error: Could not determine block_count from GGUF metadata.", file=sys.stderr)
            sys.exit(1)

    print(f"Detected architecture: {arch}, Total layers: {block_count}")

    dropped_layers = calculate_drop_layers(block_count, args)
    kept_layers = [i for i in range(block_count) if i not in dropped_layers]
    new_block_count = len(kept_layers)

    print(f"Pruning plan: Dropping {len(dropped_layers)} layers: {sorted(dropped_layers)}")
    print(f"Remaining layers: {new_block_count} of {block_count} ({new_block_count / block_count * 100:.1f}%)")

    # Mapping: old layer index -> new sequential layer index
    layer_remap = {old_idx: new_idx for new_idx, old_idx in enumerate(kept_layers)}

    writer = gguf.GGUFWriter(str(output_file), arch)

    # 1. Copy metadata fields, updating block_count and skipping auto-generated fields
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

    # 2. Copy and re-index tensors
    total_bytes_in = 0
    total_bytes_out = 0
    tensors_written = 0
    tensors_dropped = 0

    layer_pattern = re.compile(r"^blk\.(\d+)\.(.+)$")

    for tensor in reader.tensors:
        tname = tensor.name
        tbytes = tensor.n_bytes
        total_bytes_in += tbytes

        m = layer_pattern.match(tname)
        if m:
            layer_idx = int(m.group(1))
            subname = m.group(2)
            if layer_idx in dropped_layers:
                tensors_dropped += 1
                continue
            new_layer_idx = layer_remap[layer_idx]
            new_name = f"blk.{new_layer_idx}.{subname}"
        else:
            new_name = tname

        writer.add_tensor(new_name, tensor.data, raw_shape=tensor.shape, raw_dtype=tensor.tensor_type)
        total_bytes_out += tbytes
        tensors_written += 1

    print(f"Writing output GGUF: {output_file} ...")
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()

    mb_in = total_bytes_in / (1024 * 1024)
    mb_out = total_bytes_out / (1024 * 1024)
    mb_saved = mb_in - mb_out

    print(f"\nPruning Complete!")
    print(f"  Input size:   {mb_in:.1f} MiB ({mb_in / 1024:.2f} GiB)")
    print(f"  Output size:  {mb_out:.1f} MiB ({mb_out / 1024:.2f} GiB)")
    print(f"  Memory saved: {mb_saved:.1f} MiB ({mb_saved / 1024:.2f} GiB)")
    print(f"  Tensors kept: {tensors_written}, dropped: {tensors_dropped}")


if __name__ == "__main__":
    main()
