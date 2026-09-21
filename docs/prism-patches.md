# Vendoring the PrismML llama.cpp fork as a patch series

`patches/llama.cpp/0002..0011-prism-*.patch` is the entire
[PrismML-Eng/llama.cpp](https://github.com/PrismML-Eng/llama.cpp) `prism` branch,
re-expressed as a series `llamacpp/patcher.py` can apply to a clean upstream
checkout. This document is how to regenerate it, and what it costs.

## What it is for

The prism-ml Bonsai GGUFs (`prism-ml/Ternary-Bonsai-27B-gguf`,
`prism-ml/Bonsai-27B-gguf`) ship in **PQ2_0** — ternary `{−1, 0, +1}` with an
FP16 scale per **128** weights, 1.71 bpw, ggml type **142**. Upstream llama.cpp
has `Q1_0` (41) and `Q2_0` (42), but those are the **group-64** formats. The
group-128 pack is the fork's, and nothing upstream can read it.

A published GGUF is not portable between the two. The HF repo ships both:

| file | size | runs on |
| --- | --- | --- |
| `Ternary-Bonsai-27B-PQ2_0.gguf` | 6.83 GB | this series (or the fork) |
| `Ternary-Bonsai-27B-Q2_0.gguf` | 6.83 GB | **nothing** — legacy group-128 pack; the fork detects it and errors with a pointer to the other two |
| `Ternary-Bonsai-27B-Q2_g64.gguf` | 7.23 GB | stock upstream llama.cpp, no patches |

The architecture (`qwen35`, Qwen3.6-27B hybrid attention) is **already upstream** and
needs nothing from this series. Only the weight format does.

So: if you only want to run a Bonsai model and can spare 400 MB, the `Q2_g64`
file on a stock build is the cheap path and this series is unnecessary. The
series exists for PQ2_0/PTQ1_0 plus the fork's kernels, drafters and KV
mean-centering.

## The pin, and why it is not optional

`llamacpp.custom_build.tag` is set to **`b10615`**.

The fork branched from upstream `5ea87dd`, which is `b10615` plus one unrelated
WebGPU argsort fix. The series is generated as the fork's own work only — it does
**not** carry that WebGPU commit — so it applies exactly at `b10615`.

Leave the tag empty and `fetch_source()` resolves the latest release instead;
every prism patch then fails `git apply --check` in one go, and `apply_patches()`
reports ten failures that look like bit-rot but are just the wrong base.

Upstream was ~450 commits past `b10615` when this was generated. That gap only
grows. **This is the fork-maintenance cost `patcher.py` was explicitly shaped to
avoid**, accepted here on purpose; the module docstring says so plainly. Refresh
by regenerating, never by hand-editing a `.patch`.

## Regenerating

Needs `git` and ~2 GB of scratch. Nothing here touches the real build checkout.

```bash
# 1. Blobless clone of the fork; add upstream so tags resolve.
git clone --filter=blob:none --no-checkout https://github.com/PrismML-Eng/llama.cpp.git fork
cd fork
git remote add upstream https://github.com/ggml-org/llama.cpp.git
git fetch --filter=blob:none --tags upstream

# 2. Find the base and the tag under it. If `git describe` no longer says
#    "b10615-1-g5ea87ddad", the fork rebased -- update the pin in settings.json
#    AND the Base tag line in tools/split_prism.py's HEADER.
BASE=$(git merge-base upstream/master origin/prism)
git describe --tags "$BASE"

# 3. Confirm the base-offset commits do not touch anything the fork touches.
#    Empty output = the series will apply cleanly at the tag.
comm -12 <(git diff --name-only b10615.."$BASE" | sort) \
         <(git diff --name-only "$BASE"..origin/prism | sort)

# 4. The fork's own diff, and nothing else.
git diff --binary --no-renames "$BASE"..origin/prism > ../prism-full.diff

# 5. Split into the series.
python ../split_prism.py ../prism-full.diff ../series
```

`split_prism.py` **refuses to write** if any changed file falls outside every
group's path prefixes, rather than silently dropping it. A new top-level
directory in the fork therefore shows up as a hard failure — add it to `GROUPS`
and rerun.

## Verifying a regenerated series

Reconstruct the tree and compare it to the fork. This is the only check that
matters; per-patch `--check` passing does not prove the result is the fork.

```bash
git worktree add ../base b10615
cd ../base
for p in ../series/*.patch; do git apply "$p" || echo "FAILED $p"; done
git add -A -f
git write-tree                      # vs: git rev-parse origin/prism^{tree}
```

Two residual differences are **expected** and were present when this series was
generated:

1. `ggml/src/ggml-webgpu/wgsl-shaders/argsort.wgsl` — the upstream commit
   deliberately excluded in step 4. Not built here.
2. `tools/kv-mean-center/make-calib-corpus.sh` — mode only (`100644` vs
   `100755`), a Windows `core.filemode` artifact.

Anything else means the split lost content. Do not install it.

## Layout of the series

Split by **file group**, not by commit. Each group owns a disjoint set of files,
so there is no apply-time ordering dependency and any single patch can be deleted
without breaking `git apply` on the rest.

| patch | files | what |
| --- | --- | --- |
| `0002` ggml core | 7 | PQ2_0 / PTQ1_0 type table, block layouts, reference quant/dequant, FWHT op |
| `0003` CPU | 12 | scalar + AVX-512-VNNI / AVX-VNNI / NEON dots, repack GEMV/GEMM |
| `0004` CUDA | 34 | MMQ / MMVQ / dequant / get_rows, FWHT, sm_90a wgmma prefill, GDN rows |
| `0005` Metal | 15 | mat-vec / mat-mul in exact float, dequant-copy, threadgroup FWHT |
| `0006` other backends | 13 | Vulkan shaders, HIP decode, `supports_op` on the rest |
| `0007` llama runtime | 22 | ftype wiring, KV mean-centering, Hadamard fold, DSpark/DFlash/DFly, graph fusions |
| `0008` common + tools | 25 | DSpark Markov drafter, speculative changes, kv-mean-center tool |
| `0009` conversion | 9 | gguf-py constants, DSpark/DFlash exporters |
| `0010` tests | 13 | the only executable spec of what the kernels should produce |
| `0011` docs + CI | 7 | README notes and the fork's 927-line release workflow |

Compile-time they are **not** independent: `0002` underpins every backend patch.

Inert in a telecode build — safe to delete if the series gets unwieldy:

- `0009` — only needed to *produce* a GGUF, not to run one.
- `0010` — `LLAMA_BUILD_TESTS=OFF`.
- `0011` — `.github/workflows/release-prism.yml` builds release archives from
  PrismML-Eng's repo and will never run here.

`0008` also carries `examples/rs-rollback/`, which `LLAMA_BUILD_EXAMPLES=OFF`
never compiles.

## Known weak spots, from the fork's own commit messages

- **ARM NEON dot for PQ2_0** — landed marked `[UNVERIFIED - needs ARM HW]`.
- **Hopper wgmma prefill (sm_90a)** — landed `[UNVERIFIED - needs Hopper HW]`,
  then enabled by default later in the series without a verification commit.
- **Metal PQ2_0 backend** — landed `[UNVERIFIED - needs Apple HW]`.

None of these affect an x86 + CUDA build below Hopper.

## The standing trap

Unchanged from `patcher.py`'s docstring, and now worse: the **Version Manager →
Update Now** path overlays a release zip onto the same install dir and silently
replaces a patched build. With ten prism patches in the series that means
silently losing PQ2_0 support — the server starts fine and then refuses to load
the model. `status()`'s `build_is_installed` is what reports it; check it before
concluding a GGUF is corrupt.
