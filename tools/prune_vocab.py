#!/usr/bin/env python3
"""Offline Vocabulary Pruner for GGUF models.

Trims unused vocabulary rows out of token_embd.weight, output.weight, and tokenizer metadata
(Strategy 2: Offline Vocabulary Pruning). Modern LLMs (Qwen: 152k, Llama 3: 128k) waste 1.0–1.8 GB
of VRAM storing multilingual subwords that are never used during coding/English tasks.

Trimming vocabulary to the top 32k-64k tokens + special tokens permanently shrinks the model weights,
allowing dense 27B/32B models to fit entirely inside 12GB VRAM without CPU offloading.

Usage:
  # Keep top 32768 most frequent tokens + special tokens:
  python tools/prune_vocab.py --in models/qwen2.5-32b.gguf --out models/qwen2.5-32b-vocab32k.gguf --keep-tokens 32768

  # Keep tokens ranked from your own code/text (generated via build_head_subset_ids.py):
  python tools/prune_vocab.py --in models/model.gguf --out models/trimmed.gguf --token-ids data/head-ids.txt
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
import numpy as np

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
    parser = argparse.ArgumentParser(description="Offline Vocabulary Pruner for GGUF models")
    parser.add_argument("--in", "-i", dest="input_path", required=True, help="Path to input GGUF file")
    parser.add_argument("--out", "-o", dest="output_path", required=True, help="Path to output trimmed GGUF file")
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--keep-tokens", "-k", type=int, help="Number of most frequent tokens to retain (e.g. 32768 or 65536)")
    group.add_argument("--token-ids", type=str, help="Path to text file containing ranked token IDs (one per line)")

    parser.add_argument("--special-tokens", type=int, default=1024, help="Number of trailing special tokens to preserve (default: 1024)")
    return parser.parse_args()


def load_token_selection(total_vocab: int, keep_n: int | None, ids_file: str | None, n_special: int) -> list[int]:
    n_special = min(n_special, total_vocab // 4)
    selected_set = set()
    selected_list = []

    if ids_file:
        with open(ids_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    tid = int(line)
                    if 0 <= tid < total_vocab - n_special and tid not in selected_set:
                        selected_set.add(tid)
                        selected_list.append(tid)
                except ValueError:
                    continue
        print(f"Loaded {len(selected_list)} token IDs from {ids_file}")
    elif keep_n:
        target = min(keep_n, total_vocab - n_special)
        selected_list = list(range(target))
        selected_set = set(selected_list)

    # Always append the last n_special tokens (special tokens like BOS, EOS, PAD, thinking tags)
    for tid in range(total_vocab - n_special, total_vocab):
        if tid not in selected_set:
            selected_set.add(tid)
            selected_list.append(tid)

    return selected_list


def main():
    args = parse_args()
    input_file = Path(args.input_path)
    output_file = Path(args.output_path)

    if not input_file.exists():
        print(f"Error: input file {input_file} not found.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading GGUF: {input_file} ...")
    reader = gguf.GGUFReader(str(input_file))

    # Detect vocabulary size and architecture
    arch = "llama"
    if "general.architecture" in reader.fields:
        arch = str(reader.fields["general.architecture"].contents())

    vocab_tokens_field = reader.fields.get("tokenizer.ggml.tokens")
    if not vocab_tokens_field:
        print("Error: Could not find tokenizer.ggml.tokens in GGUF metadata.", file=sys.stderr)
        sys.exit(1)

    all_tokens = vocab_tokens_field.contents()
    total_vocab = len(all_tokens)
    print(f"Detected total vocabulary size: {total_vocab} tokens")

    selected_tokens = load_token_selection(total_vocab, args.keep_tokens, args.token_ids, args.special_tokens)
    new_vocab_size = len(selected_tokens)
    print(f"Pruned vocabulary size: {new_vocab_size} tokens (reduced by {total_vocab - new_vocab_size} tokens, {(1 - new_vocab_size/total_vocab)*100:.1f}%)")

    writer = gguf.GGUFWriter(str(output_file), arch)

    # 1. Copy metadata fields, updating tokenizer arrays
    skip_prefixes = ("GGUF.", "general.architecture")
    for field in reader.fields.values():
        name = field.name
        if any(name.startswith(p) or name == p for p in skip_prefixes):
            continue

        if name == "tokenizer.ggml.tokens":
            trimmed_tokens = [all_tokens[i] for i in selected_tokens]
            writer.add_token_list(trimmed_tokens)
            continue
        elif name == "tokenizer.ggml.scores":
            scores = field.contents()
            trimmed_scores = [scores[i] for i in selected_tokens]
            writer.add_token_scores(trimmed_scores)
            continue
        elif name == "tokenizer.ggml.token_type":
            ttypes = field.contents()
            trimmed_ttypes = [ttypes[i] for i in selected_tokens]
            writer.add_token_types(trimmed_ttypes)
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

    # 2. Copy tensors, slicing vocab-dimension tensors
    total_bytes_in = 0
    total_bytes_out = 0
    tensors_written = 0

    vocab_tensors = {"token_embd.weight", "output.weight"}

    for tensor in reader.tensors:
        tname = tensor.name
        tbytes = tensor.n_bytes
        total_bytes_in += tbytes

        if tname in vocab_tensors and tensor.shape[-1] == total_vocab:
            # Slicing along vocabulary dimension
            # If tensor is raw float32/float16:
            data = tensor.data
            if hasattr(data, "shape") and len(data.shape) >= 2:
                # Sliced array
                sliced_data = data[selected_tokens, :] if data.shape[0] == total_vocab else data[:, selected_tokens]
                writer.add_tensor(tname, sliced_data)
                total_bytes_out += sliced_data.nbytes
                tensors_written += 1
                continue

        # Non-vocab tensor or quantized block tensor: written directly
        writer.add_tensor(tname, tensor.data, raw_shape=tensor.shape, raw_dtype=tensor.tensor_type)
        total_bytes_out += tbytes
        tensors_written += 1

    print(f"Writing trimmed GGUF: {output_file} ...")
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()

    mb_in = total_bytes_in / (1024 * 1024)
    mb_out = total_bytes_out / (1024 * 1024)
    print(f"\nVocabulary Pruning Complete!")
    print(f"  Input size:   {mb_in:.1f} MiB")
    print(f"  Output size:  {mb_out:.1f} MiB")
    print(f"  Tensors processed: {tensors_written}")


if __name__ == "__main__":
    main()
