"""Split the PrismML fork diff into a telecode patch series.

The fork is 93 commits on top of upstream 5ea87dd (= tag b10615 + one unrelated
webgpu commit). Splitting by FILE GROUP rather than by commit range is
deliberate: every group owns a disjoint set of files, so the patches have no
apply-time ordering dependency on each other and any one of them can be dropped
without breaking `git apply` on the rest. (They do have COMPILE-time
dependencies - dropping the ggml core patch breaks every backend patch.)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SRC = Path(sys.argv[1])
OUT = Path(sys.argv[2])

# (number, slug, subject, [path predicates], body)
GROUPS: list[tuple[str, str, str, list[str], str]] = [
    ("0002", "prism-ggml-core-low-bit-types",
     "ggml : add the Prism low-bit types (PQ2_0, PTQ1_0) and the FWHT op",
     ["ggml/include/", "ggml/src/ggml.c", "ggml/src/ggml-common.h",
      "ggml/src/ggml-quants.c", "ggml/src/ggml-quants.h", "ggml/src/gguf.cpp",
      "ggml/src/CMakeLists.txt", "ggml/CMakeLists.txt", "ggml/src/ggml-impl.h",
      "ggml/src/ggml-backend.cpp", "ggml/src/ggml-alloc.c"],
     "Type-table entries, block layouts and reference quant/dequant for the two\n"
     "group-128 formats the Bonsai models ship in, plus the Fast Walsh-Hadamard\n"
     "transform op the Hadamard weight-fold runtime dispatches to.\n\n"
     "PQ2_0 is ggml type 142: ternary {-1,0,+1} with an FP16 scale per 128\n"
     "weights, 1.71 bpw. It coexists with upstream's Q2_0 (type 42, group 64)\n"
     "rather than replacing it - the published PQ2_0 GGUFs and the upstream\n"
     "group-64 GGUFs are different files. PTQ1_0 is the 1.75 bpw sibling."),

    ("0003", "prism-ggml-cpu-backend",
     "ggml-cpu : PQ2_0 / PTQ1_0 CPU backend, repack and SIMD dots",
     ["ggml/src/ggml-cpu/"],
     "Scalar q8_0 vec-dot and CPU traits for both types, the get_rows and ops.cpp\n"
     "switch coverage, and the SIMD fast paths: AVX-512-VNNI and AVX-VNNI on x86,\n"
     "NEON and NEON+DP on ARM, with repack GEMV/GEMM for Q1_0 and PQ2_0.\n\n"
     "The ARM NEON dot is marked UNVERIFIED upstream - the fork's own commit\n"
     "message says it needs ARM hardware it was never run on."),

    ("0004", "prism-ggml-cuda-backend",
     "ggml-cuda : PQ2_0 / PTQ1_0 CUDA kernels, FWHT and the Hopper prefill path",
     ["ggml/src/ggml-cuda/"],
     "MMQ tile loaders, MMVQ, dequantize and get_rows for both low-bit types;\n"
     "the FWHT kernels including the block-per-row path for widths >= 512; and\n"
     "the opt-in sm_90a wgmma prefill path for Q1_0/PQ2_0, which the fork turns\n"
     "on by default late in the series.\n\n"
     "Also carries the gated-delta-net rows-indexed state read that the qwen35\n"
     "ring decode path needs, and the GB10 tuning."),

    ("0005", "prism-ggml-metal-backend",
     "ggml-metal : PQ2_0 / PTQ1_0 Metal backend and FWHT kernels",
     ["ggml/src/ggml-metal/"],
     "Mat-vec and mat-mul in exact float for both types, the dequant-copy\n"
     "kernels, multi-column Q1_0 mat-vec for small verify batches, and the\n"
     "threadgroup FWHT.\n\n"
     "Dead weight on a Windows/CUDA build - kept because dropping it would make\n"
     "the series stop reproducing the fork, and it costs nothing at build time\n"
     "(the Metal backend is not compiled here)."),

    ("0006", "prism-ggml-other-backends",
     "ggml : PQ2_0 / PTQ1_0 support in the Vulkan, HIP and remaining backends",
     ["ggml/src/ggml-vulkan", "ggml/src/ggml-sycl", "ggml/src/ggml-opencl",
      "ggml/src/ggml-hexagon", "ggml/src/ggml-webgpu", "ggml/src/ggml-musa",
      "ggml/src/ggml-rpc", "ggml/src/ggml-blas", "ggml/src/ggml-cann"],
     "Vulkan dequant shaders and supports_op wiring, the HIP decode path for\n"
     "Q1_0/Q2_0/PQ2_0 on AMD, and the supports_op / type-table updates the other\n"
     "backends need so they correctly refuse the new types instead of crashing."),

    ("0007", "prism-llama-runtime",
     "llama : Prism runtime - ftype wiring, KV mean-centering, DSpark/DFlash",
     ["src/", "include/"],
     "The llama-level half of the fork:\n\n"
     "  - PQ2_0/PTQ1_0 ftype ids, names and row-data validation, plus detection\n"
     "    of legacy group-128 Q2_0 files so they fail with a pointer to the right\n"
     "    download instead of garbage output.\n"
     "  - Per-channel K-cache mean-centering for Q4_0, with the hybrid-model and\n"
     "    calibration-basis guards.\n"
     "  - The Hadamard weight-fold runtime and its shared activation transform.\n"
     "  - The DSpark / DFlash / DFly speculative-decoding drafters and the six\n"
     "    hybrid-attention decode graph fusions.\n"
     "  - The qwen35 ring decode path.\n\n"
     "This is the patch most exposed to upstream churn: llama-arch.cpp alone is\n"
     "479 added / 413 removed lines against a file upstream edits constantly."),

    ("0008", "prism-common-and-tools",
     "common, tools : DSpark drafter runtime, kv-mean-center tool, speculative",
     ["common/", "tools/", "examples/", "CMakeLists.txt"],
     "The DSpark Markov drafter (CUDA and Metal), the speculative-decoding\n"
     "changes that size the draft context by n_embd_out and take the block layout\n"
     "from the draft model, the kv-mean-center calibration tool, and the\n"
     "rs-rollback examples.\n\n"
     "Note: telecode builds with LLAMA_BUILD_EXAMPLES=OFF, so the examples/ files\n"
     "are carried but never compiled."),

    ("0009", "prism-conversion-and-gguf-py",
     "gguf-py, conversion : Prism quant types and DSpark/DFlash exporters",
     ["gguf-py/", "conversion/"],
     "Python-side constants for the new ggml types and the conversion scripts\n"
     "for the DSpark drafter and its DFlash re-export.\n\n"
     "Not needed to RUN a published GGUF - only to produce one. Safe to drop if\n"
     "you are only consuming prism-ml's published weights."),

    ("0010", "prism-tests",
     "tests : Prism low-bit and drafter test coverage",
     ["tests/"],
     "Element-map and CUDA-dot tests for PTQ1_0, the kv-mean-center test, the\n"
     "DSpark/DFly forward and fusion tests, and the backend-ops and quantize-fns\n"
     "entries for the new types.\n\n"
     "telecode builds with LLAMA_BUILD_TESTS=OFF, so these are carried but never\n"
     "compiled. Kept because they are the only executable specification of what\n"
     "the kernels are supposed to produce."),

    ("0011", "prism-docs-and-ci",
     "docs, ci : Prism README notes and the release-prism workflow",
     [".github/", "docs/", "README.md", "AGENTS.md", "CONTRIBUTING.md",
      "LICENSE", "NOTICE"],
     "Documentation and the fork's own GitHub Actions release pipeline.\n\n"
     "PURE NOISE FOR A LOCAL BUILD. The workflow (927 lines) builds and uploads\n"
     "release archives from PrismML-Eng's repo and will never run here. This\n"
     "patch is last and touches nothing any other patch touches, so deleting the\n"
     "file is the whole cost of dropping it.")
]

HEADER = """From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001
From: PrismML-Eng <contact@prismml.com>
Date: Mon, 21 Sep 2026 00:00:00 +0000
Subject: [PATCH] {subject}

{body}

Provenance
----------
Source:   https://github.com/PrismML-Eng/llama.cpp
Branch:   prism
Range:    5ea87ddad22541a37053c7ba92b02ec1923617c6..9a9394a895b96003ca842a6041cb28ac49a108f7
Base tag: b10615 (the fork's base is b10615 plus one unrelated webgpu commit,
          which is NOT included here - this diff is the fork's own work only)
Part {part} of {total}, split by file group from the full 14,759-line fork diff.
llama.cpp is MIT licensed; the fork carries the same terms.
---
"""


def split_chunks(text: str) -> list[tuple[str, str]]:
    """Split a unified diff into (path, chunk) pairs, preserving bytes."""
    idx = [m.start() for m in re.finditer(r"^diff --git ", text, re.M)]
    out = []
    for i, start in enumerate(idx):
        end = idx[i + 1] if i + 1 < len(idx) else len(text)
        chunk = text[start:end]
        m = re.match(r'diff --git "?a/(.+?)"? "?b/', chunk)
        if not m:
            raise SystemExit(f"cannot parse path from: {chunk[:120]!r}")
        out.append((m.group(1), chunk))
    return out


def main() -> None:
    text = SRC.read_text(encoding="utf-8", errors="surrogateescape")
    chunks = split_chunks(text)
    print(f"parsed {len(chunks)} file chunks")

    assigned: dict[str, list[str]] = {g[0]: [] for g in GROUPS}
    seen: set[str] = set()
    orphans: list[str] = []

    for path, chunk in chunks:
        for num, _slug, _subj, prefixes, _body in GROUPS:
            if any(path == p or path.startswith(p) for p in prefixes):
                assigned[num].append(chunk)
                seen.add(path)
                break
        else:
            orphans.append(path)

    if orphans:
        print("!! UNASSIGNED FILES (would be silently dropped):")
        for o in orphans:
            print("   ", o)
        raise SystemExit("refusing to write an incomplete series")

    OUT.mkdir(parents=True, exist_ok=True)
    total = len(GROUPS)
    written = 0
    for i, (num, slug, subject, _prefixes, body) in enumerate(GROUPS, 1):
        parts = assigned[num]
        if not parts:
            print(f"-- {num}-{slug}: no files matched, skipping")
            continue
        head = HEADER.format(subject=subject, body=body, part=i, total=total)
        payload = head + "".join(parts)
        if not payload.endswith("\n"):
            payload += "\n"
        dest = OUT / f"{num}-{slug}.patch"
        dest.write_text(payload, encoding="utf-8", errors="surrogateescape",
                        newline="\n")
        nfiles = len(parts)
        print(f"-- wrote {dest.name}  ({nfiles} files, {len(payload)//1024} KB)")
        written += nfiles

    print(f"total file chunks written: {written} / {len(chunks)}")
    if written != len(chunks):
        raise SystemExit("chunk count mismatch - series would not reproduce the fork")


if __name__ == "__main__":
    main()
