"""Read-only GGUF header inspector — sizes the pieces of a model without loading it.

Parses the header only (magic, version 2/3, tensor count, KV metadata, tensor
infos); tensor data is never read. Every length and count taken from the file is
bounded before it is used (strings 1 MiB, arrays 10 million elements, tensors
200000, KV pairs 100000) and checked against the file length, so a corrupt or
hostile file raises ValueError instead of allocating or looping.

`summarize(path)` is what the tray's "Inspect GGUF" button shows. For MoE models
it also suggests a starting `n_cpu_moe` and `moe_cache_mib` (patched build).

    python -m llamacpp.gguf_info <path> [--ctx N] [--free-mib N]
"""
from __future__ import annotations

import json
import math
import os
import re
import struct
import sys
from typing import Any, BinaryIO, Optional

MAX_STRING = 1 << 20
MAX_ARRAY = 10_000_000
MAX_TENSORS = 200_000
MAX_KV = 100_000
MAX_DIMS = 8
KEEP_ARRAY = 64            # arrays up to this long keep their values; longer ones keep only the length
KEEP_STRING = 4096         # longer strings (chat templates) are kept truncated

_MAGIC = b"GGUF"
_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_T_STRING, _T_ARRAY = 8, 9

# ggml type id → (name, elements per block, bytes per block). Types missing here
# (or added by a fork) are sized from the gaps between tensor offsets instead.
GGML_TYPES: dict[int, tuple[str, int, int]] = {
    0: ("F32", 1, 4), 1: ("F16", 1, 2), 2: ("Q4_0", 32, 18), 3: ("Q4_1", 32, 20),
    6: ("Q5_0", 32, 22), 7: ("Q5_1", 32, 24), 8: ("Q8_0", 32, 34), 9: ("Q8_1", 32, 36),
    10: ("Q2_K", 256, 84), 11: ("Q3_K", 256, 110), 12: ("Q4_K", 256, 144),
    13: ("Q5_K", 256, 176), 14: ("Q6_K", 256, 210), 15: ("Q8_K", 256, 292),
    16: ("IQ2_XXS", 256, 66), 17: ("IQ2_XS", 256, 74), 18: ("IQ3_XXS", 256, 98),
    19: ("IQ1_S", 256, 50), 20: ("IQ4_NL", 32, 18), 21: ("IQ3_S", 256, 110),
    22: ("IQ2_S", 256, 82), 23: ("IQ4_XS", 256, 136), 24: ("I8", 1, 1), 25: ("I16", 1, 2),
    26: ("I32", 1, 4), 27: ("I64", 1, 8), 28: ("F64", 1, 8), 29: ("IQ1_M", 256, 56),
    30: ("BF16", 1, 2), 34: ("TQ1_0", 256, 54), 35: ("TQ2_0", 256, 66), 39: ("MXFP4", 32, 17),
}

_EXPERT_RE = re.compile(r"ffn_(gate|up|down|gate_up)_exps")
_BLK_RE = re.compile(r"^blk\.(\d+)\.")
_MIB = 1024 * 1024


class _Reader:
    """Bounded sequential reader: no read or skip may run past the file's end."""

    def __init__(self, f: BinaryIO, size: int) -> None:
        self.f, self.size = f, size

    def read(self, n: int) -> bytes:
        if n < 0 or self.f.tell() + n > self.size:
            raise ValueError("GGUF truncated or length field impossible")
        b = self.f.read(n)
        if len(b) != n:
            raise ValueError("GGUF truncated")
        return b

    def skip(self, n: int) -> None:
        if n < 0 or self.f.tell() + n > self.size:
            raise ValueError("GGUF truncated or length field impossible")
        self.f.seek(n, 1)

    def u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.read(8))[0]

    def string(self, keep: bool = True) -> Optional[str]:
        n = self.u64()
        if n > MAX_STRING:
            raise ValueError(f"string of {n} bytes exceeds the {MAX_STRING} cap")
        if not keep:
            self.skip(n)
            return None
        s = self.read(n).decode("utf-8", "replace")
        return s if len(s) <= KEEP_STRING else s[:KEEP_STRING] + "…"


def _read_value(r: _Reader, t: int, depth: int = 0) -> Any:
    if t in _SCALARS:
        fmt = _SCALARS[t]
        return struct.unpack(fmt, r.read(struct.calcsize(fmt)))[0]
    if t == _T_STRING:
        return r.string()
    if t == _T_ARRAY:
        if depth >= 3:
            raise ValueError("arrays nested too deep")
        et, n = r.u32(), r.u64()
        if n > MAX_ARRAY:
            raise ValueError(f"array of {n} elements exceeds the {MAX_ARRAY} cap")
        keep = n <= KEEP_ARRAY
        if et in _SCALARS:
            if keep:
                return [_read_value(r, et, depth + 1) for _ in range(n)]
            r.skip(n * struct.calcsize(_SCALARS[et]))
        elif et == _T_STRING:
            out = []
            for _ in range(n):
                s = r.string(keep)
                if keep:
                    out.append(s)
            if keep:
                return out
        elif et == _T_ARRAY:
            for _ in range(n):
                _read_value(r, et, depth + 1)
        else:
            raise ValueError(f"unknown array element type {et}")
        return {"array_len": n}
    raise ValueError(f"unknown value type {t}")


def read_header(path: str) -> dict[str, Any]:
    """{version, kv, tensors:[{name, dims, type, offset}], file_size, data_start}.
    Header only. ValueError on anything that is not a sane GGUF v2/v3 file."""
    size = os.path.getsize(path)
    if size < 24:
        raise ValueError("file too small to be a GGUF")
    with open(path, "rb", buffering=1 << 20) as f:
        r = _Reader(f, size)
        if r.read(4) != _MAGIC:
            raise ValueError("not a GGUF file (bad magic)")
        version = r.u32()
        if version not in (2, 3):
            raise ValueError(f"unsupported GGUF version {version}")
        n_tensors, n_kv = r.u64(), r.u64()
        if n_tensors > MAX_TENSORS:
            raise ValueError(f"{n_tensors} tensors exceeds the {MAX_TENSORS} cap")
        if n_kv > MAX_KV:
            raise ValueError(f"{n_kv} metadata entries exceeds the {MAX_KV} cap")
        # Each KV is at least 12 bytes, each tensor info at least 24: a count the
        # file cannot hold is corruption, caught before any loop runs.
        if n_kv * 12 + n_tensors * 24 > size:
            raise ValueError("metadata / tensor counts larger than the file")
        kv: dict[str, Any] = {}
        for _ in range(n_kv):
            key = r.string()
            kv[key] = _read_value(r, r.u32())
        tensors = []
        for _ in range(n_tensors):
            name = r.string()
            n_dims = r.u32()
            if not 1 <= n_dims <= MAX_DIMS:
                raise ValueError(f"tensor {name!r} has {n_dims} dimensions")
            dims = [r.u64() for _ in range(n_dims)]
            ttype, offset = r.u32(), r.u64()
            if any(d == 0 or d > 1 << 40 for d in dims) or math.prod(dims) > 1 << 48:
                raise ValueError(f"tensor {name!r} has impossible dimensions {dims}")
            tensors.append({"name": name, "dims": dims, "type": ttype, "offset": offset})
        align = kv.get("general.alignment")
        align = align if isinstance(align, int) and 0 < align <= 1 << 16 else 32
        data_start = -(-f.tell() // align) * align
    return {"version": version, "kv": kv, "tensors": tensors, "file_size": size, "data_start": data_start}


def _tensor_bytes(tensors: list[dict[str, Any]], file_size: int, data_start: int) -> list[int]:
    """Byte size of each tensor: from its type's block layout, else from the gap to
    the next tensor's offset (the last one runs to the end of the file)."""
    order = sorted(range(len(tensors)), key=lambda i: tensors[i]["offset"])
    gap = {}
    for k, i in enumerate(order):
        end = tensors[order[k + 1]]["offset"] if k + 1 < len(order) else file_size - data_start
        gap[i] = max(0, end - tensors[i]["offset"])
    out = []
    for i, t in enumerate(tensors):
        info = GGML_TYPES.get(t["type"])
        if info:
            _, blck, tsize = info
            out.append(-(-math.prod(t["dims"]) // blck) * tsize)
        else:
            out.append(gap[i])
    return out


def _num(v: Any) -> Optional[int]:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _kv_bytes_per_token(kv: dict[str, Any], arch: str, n_layers: int) -> tuple[int, str]:
    """(bytes per token of f16 KV cache, how it was derived)."""
    heads = kv.get(f"{arch}.attention.head_count")
    kv_heads = kv.get(f"{arch}.attention.head_count_kv", heads)
    emb = _num(kv.get(f"{arch}.embedding_length"))
    klen = _num(kv.get(f"{arch}.attention.key_length"))
    vlen = _num(kv.get(f"{arch}.attention.value_length"))
    if isinstance(heads, list):
        heads = max((h for h in heads if isinstance(h, (int, float))), default=None)
    if kv_heads is None or (not klen and not (emb and heads)):
        return 64 * 1024, "no head counts in the file — assumed 64 KiB/token"
    head_dim_k = klen or emb // int(heads)
    head_dim_v = vlen or head_dim_k
    if isinstance(kv_heads, list):
        per_layer = [int(h) for h in kv_heads if isinstance(h, (int, float))]
        total = sum(h * (head_dim_k + head_dim_v) for h in per_layer) * 2
        return int(total), f"per-layer KV heads, head dim {head_dim_k}, f16"
    layers = n_layers
    interval = _num(kv.get(f"{arch}.full_attention_interval"))
    if interval and interval > 1:      # hybrid linear-attention: only every Nth layer keeps a KV cache
        layers = -(-n_layers // interval)
    return int(layers * int(kv_heads) * (head_dim_k + head_dim_v) * 2), \
        f"{layers} attention layers x {int(kv_heads)} KV heads x dim {head_dim_k}, f16"


def summarize(path: str, ctx_size: Optional[int] = None, free_vram_mib: Optional[int] = None,
              kv_factor: float = 1.0) -> dict[str, Any]:
    """Facts and a starting config for a GGUF; header only.

    ctx_size    context the KV estimate is for (default min(model context, 8192)).
    free_vram_mib  VRAM to plan against (default: nvidia-smi now, else 12000).
    kv_factor   size of the KV cache relative to f16 (q8_0 is about 0.53, q4_0 about 0.28)."""
    h = read_header(path)
    kv, tensors = h["kv"], h["tensors"]
    arch = str(kv.get("general.architecture") or "")
    sizes = _tensor_bytes(tensors, h["file_size"], h["data_start"])

    n_layers = _num(kv.get(f"{arch}.block_count"))
    ctx_len = _num(kv.get(f"{arch}.context_length"))
    n_exp = _num(kv.get(f"{arch}.expert_count"))
    experts_per_layer: dict[int, int] = {}
    expert_total = dense_total = 0
    quants: dict[str, int] = {}
    for t, b in zip(tensors, sizes):
        qname = GGML_TYPES.get(t["type"], (f"type{t['type']}",))[0]
        quants[qname] = quants.get(qname, 0) + b
        if _EXPERT_RE.search(t["name"]):
            expert_total += b
            m = _BLK_RE.match(t["name"])
            if m:
                experts_per_layer[int(m.group(1))] = experts_per_layer.get(int(m.group(1)), 0) + b
        else:
            dense_total += b

    out: dict[str, Any] = {
        "path": path,
        "gguf_version": h["version"],
        "architecture": arch,
        "name": kv.get("general.name"),
        "n_layers": n_layers,
        "context_length": ctx_len,
        "embedding_length": _num(kv.get(f"{arch}.embedding_length")),
        "expert_count": n_exp,
        "expert_used_count": _num(kv.get(f"{arch}.expert_used_count")),
        "expert_shared_count": _num(kv.get(f"{arch}.expert_shared_count")),
        "has_mtp": any("nextn" in t["name"] or "mtp" in t["name"] for t in tensors),
        "tensor_count": len(tensors),
        "file_size_bytes": h["file_size"],
        "expert_bytes_total": expert_total,
        "expert_bytes_per_layer": {str(k): v for k, v in sorted(experts_per_layer.items())},
        "dense_bytes": dense_total,
        "quant_bytes": dict(sorted(quants.items(), key=lambda kvp: -kvp[1])),
    }

    if expert_total and (n_exp or 0) > 1:
        ctx = ctx_size or min(ctx_len or 8192, 8192)
        if free_vram_mib is None:
            from llamacpp import vram
            free_vram_mib = vram.free_vram_mib()
            free_src = "nvidia-smi" if free_vram_mib is not None else "assumed"
            free_vram_mib = free_vram_mib if free_vram_mib is not None else 12000
        else:
            free_src = "given"
        per_tok, kv_how = _kv_bytes_per_token(kv, arch, n_layers or len(experts_per_layer))
        dense_mib = dense_total / _MIB
        kv_mib = ctx * per_tok * max(0.05, kv_factor) / _MIB
        headroom = 1500
        budget = free_vram_mib - dense_mib - kv_mib - headroom
        n_moe_layers = len(experts_per_layer) or (n_layers or 0)
        avg_layer = expert_total / max(1, n_moe_layers) / _MIB
        fit = max(0, min(n_moe_layers, int(budget // avg_layer))) if avg_layer > 0 else 0
        cache = max(0, int(budget * 0.7))
        out["suggested"] = {
            "n_cpu_moe": n_moe_layers - fit,
            "moe_cache_mib": cache,
            "ctx_size": ctx,
            "free_vram_mib": int(free_vram_mib),
            "explanation": (
                f"{free_vram_mib} MiB free ({free_src}) - dense {dense_mib:.0f} - KV {kv_mib:.0f} "
                f"(ctx {ctx}; {kv_how}) - {headroom} headroom = {budget:.0f} MiB for experts. "
                f"At ~{avg_layer:.0f} MiB of experts per layer {fit} of {n_moe_layers} layers fit "
                f"(n_cpu_moe {n_moe_layers - fit}); with all experts on the CPU, cache 70% = {cache} MiB."),
        }
    return out


def format_summary(s: dict[str, Any]) -> str:
    """Plain-text rendering of summarize() for the tray."""
    g = lambda b: f"{b / (1 << 30):.2f} GiB"
    lines = [
        f"{s.get('name') or os.path.basename(s['path'])}  ({s['architecture'] or 'unknown arch'}, GGUF v{s['gguf_version']})",
        f"layers {s['n_layers']}   context {s['context_length']}   embedding {s['embedding_length']}   "
        f"tensors {s['tensor_count']}   MTP/nextn: {'yes' if s['has_mtp'] else 'no'}",
        f"file {g(s['file_size_bytes'])}   dense {g(s['dense_bytes'])}   routed experts {g(s['expert_bytes_total'])}",
    ]
    if s.get("expert_count"):
        lines.append(f"experts {s['expert_count']}  used/token {s['expert_used_count']}  shared {s['expert_shared_count'] or 0}")
    lines.append("quant (by bytes): " + ", ".join(f"{k} {g(v)}" for k, v in s["quant_bytes"].items()))
    sg = s.get("suggested")
    if sg:
        lines += ["", f"suggested: n_cpu_moe {sg['n_cpu_moe']}   moe_cache_mib {sg['moe_cache_mib']}", sg["explanation"]]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m llamacpp.gguf_info", description="Summarise a GGUF header (read-only).")
    ap.add_argument("path")
    ap.add_argument("--ctx", type=int, default=None, help="context size for the KV estimate")
    ap.add_argument("--free-mib", type=int, default=None, help="free VRAM to plan against (default: nvidia-smi)")
    a = ap.parse_args(argv)
    try:
        print(json.dumps(summarize(a.path, a.ctx, a.free_mib), indent=2))
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
