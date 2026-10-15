# Porting Strata's MoE features to llama.cpp as a patch series

> **2026-10-15:** patch 0004 (draft head subset) was folded into `0003-moe-cache-cpu-miss-prewarm-and-adaptive-draft.patch`. The series is now 0001 (defer_loading), 0002 (ggml), 0003 (cache, adaptive draft, head subset). Earlier mentions of 0004 below mean the head-subset part of 0003.

Source: [Niko1221/Strata](https://github.com/Niko1221/Strata) (MIT, main as of 2026-10-14, ~110k lines C++/CUDA under
`src/`, own runtime: `core/` 24k, `kernels/` 53k, `prefill/` 13k, `program/` 15k, `qwen35/` 3k, `spec/` 0.7k).
Strata is **not** a llama.cpp fork (`third_party/ggml` holds only `ggml-common.h`), so there is nothing to rebase:
this series is **our own re-implementation of its ideas** against upstream llama.cpp, as add-on patches under
`patches/llama.cpp/` (each toggleable in the tray's Patched Build card; `Requires:` lines in a patch header are honoured).

## Current state (2026-10-15)

Written and compiling: patches **0002** (ggml: `mul_mat_id` skips ids < 0 on CPU and CUDA, scheduler CPU overlap with an
event query, batched host-to-device copies), **0003** (the MoE cache modes and the adaptive draft length) and **0004** (GPU
subset of the output head for MTP draft steps). Flags, all off by default: `--moe-cache-cpu-miss` with `-promote-after/-max/-gain`,
`-decay`, `-cpu-miss-max-tokens`, `-evict-window`, `-pcie-share/-pcie-max/-pcie-auto(-window)`, `-async-upload`,
`-upload-batch`, `-swap-every/-swap-max`, `-foresight`, `-tail-skip`, `-tail-skip-rank/-auto-off`, `-route-bias/-top/-scale`,
`-stats-file`, `-prewarm`; `--spec-draft-adaptive` (+ `-min-gain/-ema/-probe`); `--spec-draft-head-subset`. Every flag has a
tray knob and an argv row. Two independent code reviews found 10 problems in the first version; all were fixed (a second
review of the fixes and the later additions is in progress). Measured so far (35B MoE, 12 GB laptop): hybrid ~40-47 tok/s
against 44-55 for a static layer split; the 125B model, where this should win, is not downloaded. **Not yet measured:**
everything added after the first benchmark, because the laptop crashed twice under load and tests are on hold until it is
on a proper charger.

Not built, and why: KV streaming, VRAM-elastic cache, custom prefill/small-batch kernels, replicas (each a week or more,
gain unmeasured here); CPU share of prefill batches above 31 tokens (design known, see `strata-parity.md`); the ~450
Strata switches that tune its own engine, hardware ports, installer or diagnostics (`strata-parity.md`, N/A rows).
Documents: `strata-params.md` (738 parameters), `strata-parity.md` (status of each), `strata-default-behaviours.md`
(always-on techniques), `research-*.md` (alternatives and papers), `strata-test-plan.md` (what to run when testing resumes).

The phase table below is the original plan, kept for reference; the revision section after it records what changed.

## Ground rules

- One patch = one feature, off by default behind a flag, byte-identical output when off.
- Each patch is verified against stock llama.cpp on the same GGUF (token-identical greedy, then speed).
- A patch that upstream already covers is dropped, not ported (check first, per phase).
- Numbers quoted by Strata (RTX 5070 12 GB, 64 GB RAM, 80-93 tok/s Q2_0) are its own; we re-measure.

## Phases (build order = dependency order)

| # | Patch | Strata source | What it is | Size / risk |
|---|---|---|---|---|
| 0 | baseline check | — | Our base (b11609) has the qwen3.8 arch (#27742). Verify QSA indexer + PLE n-gram embedding are wired; if not, that is patch 0 | unknown |
| 1 | `--moe-pin-by-need` | `expert_source.cpp` (5.2k), `STRATA_PIN_BY_NEED` | Choose which experts live on GPU by measured routing counts instead of whole layers (`--n-cpu-moe` is per layer) | small-medium |
| 2 | **adaptive expert cache** | `expert_cache.cpp` (778), `expert_source.cpp`, `remote_experts.cpp` (456), `vmm.cpp` | Per-layer VRAM slot pool; router ids remapped to slots; miss experts computed **on CPU at the same time as the GPU**; hot-miss promoted asynchronously, coldest demoted (LRU/frequency). Core of the speedup | **large, highest risk**: touches ggml scheduler + `mul_mat_id` split |
| 3 | async promote + batched DMA + exchange rotation | `docs/BATCHED_DMA.md`, `EXCHANGE_ROTATION.md`, `pinned.cu` | Pinned host experts, batched H2D copies, rotating exchange buffers | medium, needs 2 |
| 4 | prefill expert streaming | `prefill/` (13k), `moe_fused*.cu`, `moe_mmq.cu` | Stream next layer's experts over PCIe while current attention runs; 8,192-token prefill chunks; fused int8 tensor-core MoE kernels | large (kernels) |
| 5 | KV streaming (`--kv-resident N`) | `docs/KV_PREFETCH.md` | Cold KV in RAM, hot part in VRAM, so more experts fit | medium |
| 6 | K8V4 + Hadamard q4_0 KV types | DETAILS.md "K8V4", PR #21/#120 | New cache types; check upstream first (rotated KV may exist) | small-medium |
| 7 | MTP drafting + controller | `mtp.cpp` (1.8k), `spec/controller.cpp`, `draft_policy.cpp` | Upstream has `draft-mtp`; port only the acceptance-driven draft policy and the 3-token verify pass | small, partly upstream |
| 8 | prompt-lookup / suffix drafter | `spec/suffix_drafter.cpp` (98) | Upstream has ngram spec types; port only if its acceptance gate adds anything | small |
| 9 | helper / peer GPU expert caches + disjoint ownership | `peer_experts.cpp`, `docs/DISJOINT_EXPERT_CACHE.md`, `MULTI_GPU.md` | Second GPU holds extra experts; `holds()` check keeps caches disjoint | medium, needs 2 |
| 10 | VRAM-elastic cache | `vmm.cpp`, `docs/VRAM_ELASTIC.md` | CUDA VMM segments so the cache can shrink/grow; `POST /v1/vram` | medium, NVIDIA only |
| 11 | n-gram PLE table on SSD | `ngram/ple_reader.cpp` (548) | 28.8 GB table read a few rows per token via OS cache; model-specific, overlaps 0 | medium |
| 12 | conversation cache | `core/conversation_*`, `MESSAGE_BOUNDARY_CACHE.md`, `PROMPT_CACHE_TAIL.md` | Keep + reload conversation state; mostly overlaps llama-server slots / `--cache-ram` — likely dropped | likely drop |
| 13 | logit bias, llama-swap glue | `LOGIT_BIAS.md`, `LLAMA_SWAP.md` | Already supported by llama-server / our supervisor — drop | drop |
| 14 | HIP / Metal / SYCL | `AMD_HIP.md`, `metal/`, `sycl/` | ggml already has these backends; port only the tuned hipBLASLt table if wanted | optional |

## Revision after reading our base (b11609): upstream already has a GPU expert cache

`src/llama-moe-cache.{h,cpp}` (#29887, multi-GPU #30112, `--moe-cache-mib`) keeps host-resident experts in a GPU
buffer. Per layer a slot map is copied by the scheduler, and a copy callback **uploads the missing experts to the
GPU** (LRU). It never computes a miss on the CPU. Consequences for the plan:

- Patch 2 shrinks from "build a cache" to **"hybrid miss handling on top of `llama_moe_cache`"**: hits on the GPU, misses
  computed on the CPU at the same time (no upload stall), plus need/frequency-based async promotion instead of
  upload-on-miss. Patch 9 (multi-GPU caches) is **already upstream**; only the disjoint-ownership guard remains.
- The mechanism: `mul_mat_id` skips ids < 0 and zeroes those rows (done for the CPU backend, see below), so one layer
  runs two `mul_mat_id` ops, GPU over the cache with misses = -1 and CPU over the host tensor with hits = -1, summed.
  No CUDA kernel change: the GPU side can point misses at slot 0 and zero their routing weight.
- Measured on this laptop (RTX 5070 Ti Laptop 12 GB, 63 GB RAM, `qwen3.6-35b-a3b` UD-Q4_K_XL, ctx 8192, 4 prompts x
  256 tokens, greedy): stock `-ncmoe 26` = **54.6 tok/s** decode, 85.9 prompt; all experts on CPU + upstream
  `--moe-cache-mib 5000` = **36.2 tok/s** decode, 31.7 prompt. The upstream cache loses to a static layer split here, which is
  exactly the gap CPU-compute-on-miss and adaptive promotion should close. This is the baseline every patch must beat.
- Status of code (2026-10-14): **patch `0002-moe-cache-compute-misses-on-the-cpu.patch` is written, builds and runs.**
  CPU `mul_mat_id` (`ggml-cpu.c`, `repack.cpp`) skips ids < 0 and zeroes those rows. `llama-moe-cache` gets a CPU-miss
  mode (`plan_cpu_miss`: miss counts per expert, promotion after N misses, max uploads per layer/token, count decay;
  the uploaded expert is used from the layer's next call). `build_moe_ffn` runs the FFN twice (cache hits on the GPU,
  misses on the CPU) and sums. Flags: `--moe-cache-cpu-miss`, `--moe-cache-promote-after`, `--moe-cache-promote-max`,
  `--moe-cache-decay`, `--moe-cache-cpu-miss-max-tokens`; all exposed in the tray model page and `argv.py`.
- **Limit found:** CUDA's MMQ/MMF `mul_mat_id` assumes an expert appears once per token, so repeated "dummy" ids
  corrupt `expert_bounds` and crash (illegal memory access, batches > 8). Hybrid is therefore capped at 4 tokens by
  default (max 8, the MMVQ small-batch kernels tolerate repeats). Proper fix = skip ids < 0 in `mm_ids_helper`, MMVQ, MMVF
  and MMF and zero dst (patch 0003, not started); that lifts the cap and drops the weight masks.
- **Measured, same bench, ctx 8192, 4 prompts x 256 tok:** decode 35.6 tok/s (defaults), 31.4 (promote at 1, max 4),
  **38.2** (promote after 4, max 1) vs upstream cache 36.2 and static `-ncmoe 26` **54.6**. Prompt processing 66 vs 31.7
  (upstream cache) vs 85.9 (static). So correct and a little better than upstream's cache, but **not yet better than a
  static layer split**. Why: (1) the stock scheduler synchronizes the whole GPU stream on every GPU->CPU input copy
  (`ggml_backend_sched_copy_input`), so the GPU branch and the CPU branch run one after the other; real overlap needs
  per-split events (patch 0004). (2) A 5 GB cache holds ~27% of this model's experts, so hit rate decides the gain;
  Strata's headline numbers come from a 125B model where the hot set is a smaller fraction. Next: hit-rate stats at
  shutdown, scheduler events, then re-measure on the 125B once it is downloaded.

## Generality: nothing here may be Qwen-specific

Strata hard-codes one model (`src/qwen35`, 24,576 experts, 10 active, MTP depth 3). We port the **mechanisms** and
discover every number from the GGUF (expert count, top-k, shared experts, layer kinds). Each patch is gated on a model
class, detected at load from GGUF metadata (`expert_count > 0`, hybrid/recurrent layers, MTP tensors, sparse-attn
indexer), never from the arch name; a dense model loads untouched.

| Gate | Patches |
|---|---|
| MoE (`n_expert > 0`) | 1, 2, 3, 4, 9, 10, 15, 16, 19, 20 |
| MoE and `n_expert_used` small vs `n_expert` | the cache auto-sizes; it self-disables when everything fits in VRAM |
| any model with a KV cache | 5, 6, 12 (generic parts) |
| model with MTP / draft tensors | 7 |
| any model | 8, 17, 18, 21 |
| hybrid recurrent (GDN / SSM) state | 22 (state-aware checkpointing; upstream already has the layers) |

## Missed in the first pass (found in the deep check of `docs/*` and `src/*`)

| # | Patch | Strata source | What it is |
|---|---|---|---|
| 15 | resident plan from routing stats | `route_resident_parity`, `resident_plan_parity`, `route_window_parity`, `docs/MODELS.md` low-RAM | Routing-window statistics decide the VRAM-resident set; "low-RAM mode" keeps in RAM only what the GPU does not hold (a complement, not a copy) |
| 16 | CPU expert pool + affinity | `kernels/cpu/`, `serve/cpu_affinity.py`, "17 expert-pool workers" | Dedicated worker pool computing miss experts in place (AVX2/AVX-512/AMX), pinned to cores; the CPU half of patch 2 |
| 17 | batch slots with grouped verify | `docs/BATCHING.md`, `batch_groups_test` | N concurrent requests share one verify pass; check against llama-server `--parallel` first |
| 18 | small-batch interleaved matvec | `docs/MMVQ_IL_TABLE.md`, `mmvq_il_parity` | Decode/verify windows of 2-4 tokens use an interleaved quant layout kernel with a per-card measured table |
| 19 | NVFP4 routed experts + w4a4/w4a8 prefill | `docs/NVFP4.md`, `mmq_nvfp4_*` | Blackwell/Ada FP4 expert path; needs the quant type in the GGUF, check upstream NVFP4 first |
| 20 | prefill head alias | `docs/PREFILL_HEAD_ALIAS.md` | Output head's VRAM lent to the prompt path (they never run together) |
| 21 | decode-kernel tricks | `pdl_parity`, `decode_cluster_parity` | Programmatic dependent launch, thread-block-cluster argmax/select on sm_90+ |
| 22 | research runs | `docs/RESEARCH_RUNS.md` | Read a long document once, fork many questions from the saved state |
| 23 | sparse-attention indexer (QSA) | `qsa_*_parity`, `qsa_oracle_vectors` | Only if the model has one; part of baseline patch 0 |
| 24 | PCIe mode `dma` vs `mmap`, SSD expert source | `--pcie-mode`, `--mmap-experts`, `expert_source.cpp` | Third tier: experts read from NVMe when RAM is too small |

Dropped as out of scope (server / UI / other hardware, not engine features): image generation, MCP server, monitor,
prometheus, llama-swap glue, replicas, installer scripts, Metal / SYCL / Intel Arc / Strix Halo / V100 ports.

## Where it plugs into telecode

`llamacpp/patcher.py` applies top-level patches and each vendor subdirectory (`patch_key()` already keys by relative
path). `strata/` would be a third kind: **a re-implementation series, not a vendored fork**, so it gets no
`VENDOR.json` drift tooling (nothing to drift against) and no `claims` — only a `NOTICE` and per-patch flag docs.
Each patch's flags then go into `llamacpp/argv.py` tables and the tray page, following the existing pattern.

## Decisions and risks

- **Patch 2 decides whether the project is worth it.** Do a spike first: read how ggml schedules `mul_mat_id` and
  what Strata's `expert_source.cpp` does on a miss, then prove a single-GPU hit/miss split on one layer and measure.
  If the spike shows the scheduler change is not rebase-survivable, stop and use the alternative below.
- **Rebase cost:** patches 2-4 touch hot upstream files. Expect to carry them every upstream bump, the cost the
  `patcher.py` notes warn about. Keep them small, and keep drift checks (`git apply --check`) in the tray card.
- **Alternative:** supervise Strata itself as a second engine (OpenAI + Anthropic API on localhost), like the DocGraph
  host. Zero llama.cpp maintenance, one model family only, and no gain for other MoE models.
- **Test bench (this laptop):** RTX 5070 Ti Laptop 12 GB, 63 GB RAM — the same class as Strata's own RTX 5070 12 GB /
  64 GB box. Configured models: `qwen3.6-35b-a3b` (UD-Q4_K_XL, MoE, `n_cpu_moe: 26`; the **baseline for patches 1-3**,
  it is already expert-offloaded, so before/after is a direct A/B) and `qwen3.8-27b` (dense + MTP; only useful for
  patches 5-8). The 125B Qwen3.8-Flash-Next is not downloaded (35-50 GB at Q2-IQ3), so the headline numbers need that
  GGUF. Build: `custom_build.source_dir = ./build/llama.cpp`, CUDA on; that checkout does not exist yet.
