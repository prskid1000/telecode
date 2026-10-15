# Strata: behaviours with no switch, on by default, or described only in prose

Date: 2026-10-14. Companion to [strata-params.md](strata-params.md) (the 738 documented parameters),
[strata-parity.md](strata-parity.md) (their mapping) and [strata-port.md](strata-port.md) (the port plan). A parameter
inventory misses what Strata does **without being asked**: techniques that are always on, chosen automatically,
calibrated at startup, adaptive at runtime, or only described in prose. This file lists those.

**Sources read.** These were read by eight parallel passes, one per group:
- the docs: `README.md`, `docs/HOW_IT_WORKS.md`, `docs/DETAILS.md` in full, and the paper `docs/paper/Strata-Paper.pdf` (all 9 pages)
- the smaller docs: `BATCHED_DMA`, `EXCHANGE_ROTATION`, `KV_PREFETCH`, `VRAM_ELASTIC`, `DISJOINT_EXPERT_CACHE`, `MESSAGE_BOUNDARY_CACHE`, `PROMPT_CACHE_TAIL`, `PREFILL_HEAD_ALIAS`, `MODELS`, `BATCHING`, `SECOND_GPU`, `QUANTS`, `MMVQ_IL_TABLE`, `TROUBLESHOOTING`, `OLDER_GPUS`
- the engine source, function bodies read:
  - `src/core/{expert_cache,expert_source,remote_experts,remote_expert_opt,peer_experts,vmm,weights,pinned,layer,graph,verify,mtp,session,native_head,native_dense,conversation_*}`
  - `src/spec/*`, `src/kernels/ngram.cpp`
  - the sampler, kv and route-prior kernels
  - `src/kernels/cpu/{pool,expert,expert_layout,native_expert,pool_affinity_*}`
  - `src/prefill/prefill.cpp` and its dispatch files
  - `src/program/generate.cpp`: option defaults, startup, the serve loop, the adaptive tier
  - `src/plan/plan_main.cpp`
  - `src/platform/*`
  - `serve/{server,responses,structured,winjob,keepawake,cpu_affinity,cache_drop,session_save_retry,runconfig}.py`

Strata clone: engine ~0.1.44. Nothing was run. Line numbers are approximate. Numbers are Strata's own measurements on
its hardware, not ours.

**Our side** = patches `0002-ggml-skip-negative-expert-ids-and-cpu-split-overlap` and
`0003-moe-cache-modes-expert-reduction-adaptive-draft-head-subset`, telecode (proxy, `LlamaSupervisor`, `llamacpp/{argv,gguf_info,
vram,cpu_topology}.py`) and stock llama.cpp b11609.

The 0003 flags are:
- `--moe-cache-cpu-miss`, `-promote-after`, `-promote-max`, `-promote-gain`, `-decay`, `-cpu-miss-max-tokens`
- `-stats-file`, `-prewarm`, `-route-bias`, `-foresight`, `-tail-skip`
- `--spec-draft-adaptive*`

Stock features we count as equivalents include `--moe-cache-mib` (LRU upload-on-miss), `--fit`, `--load-mode`,
`--cpu-mask`, `--cpu-strict`, `--poll`, `--cache-ram`, ctx checkpoints, `draft-mtp`, the `ngram-*` spec types,
`--spec-draft-sampling` and `GGML_OP_OFFLOAD_MIN_BATCH`.

**Status:**
- **COVERED**: we or stock llama.cpp do the equivalent. The cell names the flag or patch.
- **PARTIAL**: something exists but differs or is missing a piece. The cell says what is missing.
- **MISSING-FEASIBLE**: portable. The cell gives the effort (S = hours, M = a day or two, L = a week or more) and how.
- **MISSING-NOT-PORTABLE**: tied to Strata's runtime, model, file format or other hardware. The cell gives the reason.

Rows marked "→ §n" are cross-references and are counted only once.

## Summary

| Status | Count |
|---|---:|
| COVERED | 66 |
| PARTIAL | 38 |
| MISSING-FEASIBLE | 75 |
| MISSING-NOT-PORTABLE | 27 |
| **Total** | **206** |

The eight reading passes produced about 380 raw rows. Merging duplicates across docs and code leaves the 206 above.

**Corrections to `strata-parity.md` found by reading code instead of parameter descriptions:**
- **Route tail skip is ON by default** (rank ≥ 7, CUDA, since 0.1.42) and switches itself off when ≥ 90 % of experts are
  cached. Ours is off by default and uses a different rule.
- **The adaptive swap margin is additive** (`cand ≥ victim + 1.5`, with a candidate floor of 2.0 and ×0.7 decay per
  round). Our `--moe-cache-promote-gain` is a ratio.
- **The prompt-time CPU share is on by default** for chunks under 1,024 tokens (0.1.41+). The PCIe share of decode
  misses and its runtime refinement (`PcieAuto`) are also on.
- **`spec_min_p` defaults to 0 in the engine.** The paper's p ≥ 0.5 is a configuration choice.
- **Strata keeps a cache hit and a miss bit-identical.** The GPU reuses the CPU's activation scales, so cache residency
  never changes a token. Our hybrid path quantises activations differently on CPU and CUDA, so in our port **cache
  contents can change greedy output**. This does not break the "identical when off" rule, but tests that compare
  cache-on runs against each other must allow for it.

---

## 1. Startup admission, VRAM/RAM split and placement

| Technique | Where | What it does | Default | Impact | Our status |
|---|---|---|---|---|---|
| Three-tier byte placement | paper §3.1 Table 1; HOW_IT_WORKS | Everything read for every token (attention, mixers, routers, shared experts, head, MTP, KV and state) lives in VRAM, routed experts in RAM, and the n-gram table on SSD. The expert cache gets whatever VRAM is left. | always | ~3.5 GB dense on the GPU, ~6 GB cache ≈ 4,500 of 24,576 experts | **COVERED**: `-ot` / `--n-cpu-moe` + `--moe-cache-mib` give the same tiers. Sizing is manual, see the next row |
| Expert cache auto-sized from VRAM that is free after load | generate.cpp:main ~5107-5205; DETAILS "expert cache size is a budget"; paper §3.7 Table 4 | Slots = free VRAM after weights, KV, drafter and head, minus a reserve (700 MiB + prompt buffers 160 + chunk·680/1024 MiB + MTP head bind + ~180 MiB draft head), divided by the blob size and capped at the profile length. Small cards shrink the reserve down to 300 MiB to keep a minimum slot count, and long contexts shrink the cache automatically (4,517 slots at 1K → 1,588 at 262K). | on (setup writes `auto`) | A cache 0.86 GiB too large: 102 → 13.7 tok/s (WDDM paging) | **PARTIAL**: `--moe-cache-mib` is manual and `gguf_info` only suggests a value. Add `--moe-cache-mib auto` in `llama-moe-cache`, sized after the KV and compute buffers are allocated, minus a reserve. S-M |
| Write-then-recheck of free VRAM | generate.cpp:main ~5361-5428 | After zeroing every slot it re-reads free VRAM. If free is below reserve − 64 MiB it reopens the cache smaller (up to 6 tries, 1 GiB steps while free reads ~0), because WDDM overstates free VRAM by 0.7-1 GB before pages are touched. | on | Prevents the paging cliff above | **MISSING-FEASIBLE** S: touch-and-recheck inside `llama-moe-cache`, or a telecode post-ready nvidia-smi check that respawns with a smaller `moe_cache_mib` |
| Smaller retry when cudaMalloc fails | generate.cpp ~5380-5397; DETAILS Troubleshooting #60 | When the single large cache allocation fails because Windows charges VRAM to commit, it retries at 3/4 of the size up to 8 times, then prints a page-file hint. | on | Start survives | **MISSING-FEASIBLE** S: retry loop in `llama_moe_cache` |
| VRAM-left warning after load, and per request | generate.cpp ~9508-9568; DETAILS "free figure moves after startup" | Warns when less than 256 MiB is free after load (512 MiB at later stages), and re-reads free VRAM on each request, warning below 1 GiB on Windows. It names the `--vram-reserve-mib` value that would fix it. | on | 91 → 3 tok/s, then a watchdog kill at 70K | **PARTIAL**: telecode's `min_free_vram_mib` checks before load only. Add a post-ready and per-request check via `vram.py` plus a tray warning. S |
| Windows commit and page-file check | generate.cpp ~7264-7280; DETAILS #60 | Compares the commit left (RAM + page file) with planned host allocations plus a 4 GiB margin. Setup warns about a page file under 4 GB or one that is system-managed. | on | Engine died silently otherwise (#1607) | **MISSING-FEASIBLE** S: tray/supervisor preflight with `GlobalMemoryStatusEx` |
| RAM fit rule and RAM check before the arena | expert_source.cpp:ArenaExpertSource::open (#633); server.py:warn_tight_ram; MODELS "Will it fit" | Refuses when the cgroup limit is below arena + one blob, and warns when experts leave under 6 GB for everything else. Setup keeps every expert in RAM when experts + ~10-16 GB fit, otherwise it picks a low-RAM mode. | on | All-in-RAM vs budget: decode ×1.34-1.40, 4K prompt ×2.07 | **PARTIAL**: `gguf_info` sizes the experts but nothing compares them with RAM. Add a warning in the supervisor or in Inspect GGUF. S |
| Low-RAM "resident complement" mode | DETAILS "Low-RAM mode, resident"; MODELS; expert_source.cpp `make_cache_complement_plan` | When RAM cannot hold every expert, RAM holds only the experts no GPU holds (4-8 GiB headroom). Each swap copies the evicted VRAM bytes back into the RAM slot freed by the incoming expert. | setup picks it | Coder commit 36 → 13 GB | **MISSING-FEASIBLE** L (planned patch 15). Low value at 64 GB |
| RAM budget by rank | expert_source.cpp:pin_cache_complement, clamp_resident_budget | Keeps the hottest non-VRAM experts in RAM within available − headroom − 256 MiB (Windows: min of RAM and commit). The rest stay on file. | opt-in, setup on low RAM | Protects the page cache near the limit | **MISSING-FEASIBLE** M (parity #7) |
| Cache slots sized per layer in bytes | paper Finding 8; expert_cache.cpp:open_sized | Each layer's slots are sized to that layer's blob, 256-B aligned, not to the largest expert. | on (native packs) | 3,556 vs 2,673 experts, +13 % decode at 1K | **PARTIAL**: unverified whether `llama_moe_cache` uses one slot size for all layers. Check first; fixing it is S-M |
| Cache capped at the ranked hot set | generate.cpp ~5153, ~5291 | Slots never exceed the number of ranked profile pairs. | on | VRAM not wasted | **MISSING-FEASIBLE** S: cap `moe_cache_mib` at the stats-file hot set in telecode |
| Setup picks context, KV type and KV streaming | DETAILS "Speed", "IQ3 at 262K"; paper §3.7 | Picks a context from RAM and VRAM, 8-bit KV above 4-8K, and KV streaming from 64K when RAM allows. | setup auto | 262K: 95 → 56 tok/s (bounded) | **PARTIAL**: stock `--fit` picks ctx and layers. Add a KV-type recommendation to `gguf_info`. S |
| `CUDA_MODULE_LOADING=EAGER` | generate.cpp ~1829-1838; QUANTS "Formats cost nothing" | Loads every kernel at context creation, before the cache takes the free VRAM, so a kernel first used mid-prompt cannot OOM on its own code. | on unless the env is set | ~30 MB; prevents `cudaFuncSetAttribute` OOM mid-prompt | **MISSING-FEASIBLE** S: needs a `llamacpp.env` setting (parity gap #4), set when `moe_cache_mib > 0` |
| Recurrent state priced in the planner | plan_main.cpp / plan.hpp `make_plan` | The memory plan subtracts GDN state and KV (int8 + fp16 scales) before sizing the pool; pricing the GDN state at 0 was worth 85 slots of error. | dev tool | Planning accuracy | **PARTIAL**: check that `gguf_info` counts recurrent-state bytes. S |
| VRAM priced at 2 MiB allocation granules | native_dense.cpp `kAllocGranule` | Every matrix of 1 MiB or more is priced at its granule-rounded size when planning the fit. | on | +0.8 % accuracy | **MISSING-FEASIBLE** S in `gguf_info` |
| All-resident shortcut | generate.cpp ~8616-8667; verify.cpp `refresh_ar` | When every expert is resident it turns off usage counting, the adaptive tier, PcieAuto, tail skip and the prefill CPU share, and runs a graph with no host service. | on | -1..2 % decode avoided | **PARTIAL**: upstream's cache disables itself. Unverified whether our cpu-miss graph drops the CPU branch when every expert hits. S |
| Dense tensors not loaded twice | generate.cpp ~3044-3066 | Tensors served in native form skip their canonical copy. | on | ~2.7 GB VRAM | **MISSING-NOT-PORTABLE**: Strata pack format |
| Windows-HIP 2,560 MiB reserve floor | generate.cpp ~5127 | Larger reserve on ≥ 15 GiB AMD cards under Windows. | on (Win HIP) | 52 → 21 tok/s without it | **MISSING-NOT-PORTABLE**: AMD only |
| Admission checks (ISA, rope, ctx + max_new) | generate.cpp ~2631-2756 | Refuses impossible configurations before loading anything. | on | - | **COVERED**: llama.cpp arg/ctx validation |
| Non-blocking main stream | generate.cpp ~3233 | All work goes on a `cudaStreamNonBlocking` stream. | on | ~8× launch cost on WDDM otherwise | **COVERED**: ggml-cuda streams + graphs |

## 2. Hot set before the first token and calibration

| Technique | Where | What it does | Default | Impact | Our status |
|---|---|---|---|---|---|
| Cache filled from a ranked profile before the first token | expert_cache.cpp:read_expert_profile; generate.cpp ~5455-5516; paper §3.4 | A shipped `profile.bin` ranks every expert; slots are filled hottest-first and blocking, 64-256 pairs ahead, and slot 0 is read back and compared. | on (setup always passes one) | Hit rate 0.645 vs 0.486 first-come; 50 % at 4,500 slots | **PARTIAL**: `--moe-cache-prewarm` + `--moe-cache-stats-file` need an earlier run and are off by default. There is no offline profile builder (routing trace → stats file) and no readback. S |
| Learned profile saved | expert_cache.cpp:rank_learned_profile | Ranks resident experts, then accumulated heat, then the prior rank, and writes atomically every 10 min and on exit. | opt-in key | +13-19 % on matching work | **COVERED**: `--moe-cache-stats-file` (every 4,000 calls + exit; atomic write unverified) |
| No-profile admission | expert_cache.cpp:admit | Without a profile it fills compulsory misses first-come. A single global counter gave a 2.97 % hit rate, against 21-70 % with per-layer quotas. | fallback | 2.97 % vs 21-70 % | **COVERED**: upstream LRU upload-on-miss. Verify that its pool is per layer. S |
| Startup PCIe probe | generate.cpp:probe_pcie_h2d_gbps ~1662 | Times four 256 MiB pinned H2D bursts after a warm-up copy and keeps the fastest. | on | x8 links get half the share | **MISSING-FEASIBLE** S, needed for §4 PCIe share |
| PCIe share start value per quant | generate.cpp:pcie_frac_for_gbps ~2858 | Base share of missed experts sent over PCIe: Q2_0 0.2, i-quant 0.55, NVFP4 0.25, scaled by min(1, GB/s ÷ 20). | on | NVFP4 0.25: 103 → 117 tok/s | → §4 |
| `PcieAuto` runtime refinement | generate.cpp `struct PcieAuto` ~1736, ~8668 | After 48, then 96, then 96 windows (≥ 300 misses) it measures the CPU pool's rate r. It sets share = link ÷ (link + 1.85 r), rounded to 0.05 and clamped to 0.10-0.60, at most three times. | on (CUDA, 1 GPU, no `--pcie-frac`) | +30 % decode on RTX 3060; within 0.05 of the measured best | **MISSING-FEASIBLE** M (needs the §4 PCIe share) |
| `--calibrate` | DETAILS "Tuning for your PC"; MODELS | Grid-measures pcie-frac, spec-min-p, pool workers and adapt knobs, keeps a setting only if it is > 3 % faster, and stores the result per PC and model. | offered after install | +7-7.9 % | **MISSING-FEASIBLE** M: drive telecode's Speed Test over a `moe_cache_*` grid |
| Router look-ahead page warming | generate.cpp ~5802-5840 `RouterLookahead`; expert_source.cpp | An aux thread applies layer l+1's BF16 router to layer l's MoE input (≤ 8 tokens, k = 10) and warms the predicted non-resident experts' pages (`PrefetchVirtualMemory` / `WILLNEED`). | on for the mmap/file tier | UD-Q4_K_XL 3 → 7-8.5 tok/s in budget mode | **PARTIAL**: `--moe-cache-foresight N` predicts and uploads to VRAM, but does no page warming. M, low value at 64 GB |
| Unbuffered vs cached load chosen automatically | pinned.cu:experts_unbuffered | Sixteen random 64 KiB probe reads decide whether the file is cached. If cold and too big for the file cache beside the arena, it loads unbuffered with 16 readers. | on | - | **PARTIAL**: stock `--load-mode dio` has no automatic choice. Telecode could pick `load_mode` from RAM vs size. S |
| Slow-load hint under Task Scheduler | generate.cpp ~5017-5031; DETAILS "Running it at startup" | Below 0.2 GiB/s load rate it explains that Task Scheduler's Below-Normal priority and limited token slow the load about 24×. | on (Windows) | 0.05 vs 1.42 GiB/s; 35 s vs 13-14 min | **MISSING-FEASIBLE** S, **relevant to us**: telecode auto-starts via a Scheduled Task under `pythonw`, so spawn llama-server with `NORMAL_PRIORITY_CLASS` |
| PLE direct-read probe and SSD keep-awake | generate.cpp ~3296-3340 | Probes random 4 KiB read rate and reads every 100 ms for 60 s after a request so the SSD does not sleep. | on | 50-150 ms first-read stalls | **MISSING-NOT-PORTABLE**: PLE n-gram table is model-specific (patch 11) |

## 3. Expert cache and adaptive swap policy

| Technique | Where | What it does | Default | Impact | Our status |
|---|---|---|---|---|---|
| Adaptive swap rounds | generate.cpp adapt lambda ~8833-8989; Options `adapt_every=4, adapt_swaps=96, adapt_min_gain=1.5, adapt_decay=0.7`; paper Finding 4 | Every routed (token, expert) is counted. Every 4 windows, per layer, missing experts with decayed count ≥ 2.0 are paired hottest-first against the coldest residents of the same layer. A swap happens when cand ≥ victim + 1.5; swaps are ranked globally by gain, capped at 96 per round, and then all counts are multiplied by 0.7. | on | Hit rate 0.50 → 0.72; drain 19.1 → 10.3 ms/token; batch 45.2 → 74.7 tok/s | **PARTIAL**: ours promotes per token after N misses, max per layer, gain as a ratio, halving decay, all off by default. Add a round mode with Strata's constants to `plan_cpu_miss` and make it the default. S |
| Admit on landing, lag 1 | generate.cpp `apply_pending` ~12609-12629; paper Finding 10 | The evicted expert leaves the residency table at once (the CPU computes it). The incoming expert counts as resident only after its copy event lands, checked at the start of the next window, so output does not depend on timing. | on | 91.7 → 94.4 tok/s | **PARTIAL**: our promoted expert is uploaded synchronously inside the layer's copy callback. → next row |
| Swap copies on a refill stream, overlapped with drafting | generate.cpp `adapt_stream`, serve loop ~12687-12737; `--adapt-async` | Swap selection runs on a thread while the window commits and MTP drafts. Copies go on a non-blocking stream with an event, so decode never waits on PCIe. | lag/overlap on; helper-thread copies opt-in | ×1.07-1.11 decode; batch 74.7 → 89.2 | **MISSING-FEASIBLE** M (parity #2): side-stream copy + event, slot marked valid when the copy completes |
| Per-layer landed signals (`ADAPT_OVERLAP`) | DETAILS "More opt-in switches"; verify.cpp `adapt_gate_wait` | Copies go in layer order and each layer waits only for its own copies, not the whole round. The signal is a 4-byte copy on Linux and a host function on Windows, because WDDM batching kept the copy from landing. Wait limit 20 s. | opt-in (was Linux CUDA default in 0.1.43) | +4-8 % A4000/P100, −21 % 5090 Laptop | **MISSING-FEASIBLE** M, after the row above |
| Swap-copy thread on a spare SMT sibling | generate.cpp `JobThread::smt_spare_cpu` ~1432 | Pins the promotion memcpy thread to a CPU no pool or host thread uses. | on (Windows) | - | **MISSING-FEASIBLE** S, once copies are async |
| Bit-identical hit and miss | expert_source.cpp:expert_hit_run, quantize_q8_0_scaled | The GPU hit uses the CPU's fp32 activation scales, so whether an expert is cached never changes a token. | on | KL 9.7e-2 → 1.03 when wrong | **MISSING-NOT-PORTABLE**: ggml CPU and CUDA quantise activations independently (see the corrections above) |
| Foresight swap space | expert_source.cpp `ForesightSwap` | Separate per-layer VRAM slots filled from misses and predictions: admitted after 1 miss in 4 windows, 32 copies per card per window, slots reused after 4 windows. | off (`STRATA_FS_SLOTS`) | - | **PARTIAL**: `--moe-cache-foresight` uploads into the main cache. No separate slots or budget |
| Batched swap DMA | BATCHED_DMA; dma_batch.hpp | `cudaMemcpyBatchAsync` groups of 2-128 copies, so there is one WDDM interrupt per group. | off | −83 % submit time, no tok/s gain shown | **MISSING-FEASIBLE** S, low value |
| Exchange-buffer rotation | EXCHANGE_ROTATION | In resident-complement mode the temporary buffer becomes the resident slot, removing a host memcpy. | off | +8.1 % (one pair) | **MISSING-NOT-PORTABLE**: needs the complement exchange design |
| Zeroed arena and startup slot readback | expert_cache.cpp:open, `verify_slot` | `cudaMemset` 0 makes an unfilled slot give a deterministic wrong answer. The first filled slot is compared with the host bytes. | on | Safety | **MISSING-FEASIBLE** S |
| Grow-back refill (VRAM elastic) | VRAM_ELASTIC | When segments return, slots get the same experts back or the layer's most-routed missing one. | on with elastic | - | **MISSING-FEASIBLE** L (patch 10) |
| Disjoint primary/helper ownership | DISJOINT_EXPERT_CACHE; expert_source.cpp | Primary promotion skips experts a helper or peer already holds; the helper's victim must be colder than the primary's coldest. | off / on for helpers | +15.5 % generation | **PARTIAL**: upstream multi-GPU split, no `holds()` guard. S-M, multi-GPU only |

## 4. Miss handling per window: CPU compute vs PCIe

| Technique | Where | What it does | Default | Impact | Our status |
|---|---|---|---|---|---|
| CPU computes misses in place while the GPU computes hits | paper §3.2 Fig. 2; hit_hook.hpp | Missed experts are computed by the CPU pool straight from RAM while the GPU runs the cached ones, and the layer waits for the slower side. | always | GPU ≈ CPU ≈ half of every round | **COVERED**: `--moe-cache-cpu-miss` + 0002 `ggml_backend_sched_set_cpu_overlap`; overlap is still limited by scheduler syncs |
| "Hit poke": force GPU submission before the CPU drain | Options `no_hit_poke=false`; generate.cpp R4.2d | After launching the GPU hit work it queries the stream so WDDM submits it now, instead of batching it until the CPU pool returns. | on | Overlapped layers 17 → 9,190 of 10,562 | **MISSING-FEASIBLE** S, **high value on Windows**: in 0002's scheduler path, call `cudaStreamQuery` / an event query after launching a GPU split that precedes an overlapped CPU split |
| Shared expert early or on a side stream | generate.cpp `layer_set_shared_early`; verify.cpp `sh_fork_late` | The shared expert runs before (or forked beside) the routed experts, so it overlaps the CPU pool. CUDA default on; HIP off (62.9 vs 43.4 tok/s). | on (CUDA) | - | **PARTIAL**: depends on graph order in `build_moe_ffn`. Place the shared expert in the GPU split that runs during the CPU miss split. S |
| In-graph doorbell | paper §3.2; verify.cpp `doorbell_publish_res`, `wait_flag_ge`; session.cpp | The GPU writes router ids and hidden state to mapped pinned memory, and a host thread spinning on them computes the misses and raises flags. GPU kernels spin on the flags while VRAM hits run, with a `cudaStreamQuery` every 2 ms; there is no driver sync per layer. | always | Removes per-layer sync latency | **PARTIAL**: 0002 overlaps through scheduler splits. A device-side wait on a host flag needs a new ggml-cuda op and scheduler surgery. L |
| Mapped pinned zero-copy exchange | session.cpp `SessionLoopScratch`; verify.cpp `mapped()` | Every host↔device exchange uses `cudaHostAllocMapped` buffers read by a kernel rather than memcpy nodes, which split WDDM submission. | on | 67 flushes per token measured otherwise | **MISSING-FEASIBLE** M: mapped pinned buffers for CPU-split inputs and outputs in the ggml scheduler |
| One plan for the whole verify window | expert_source.cpp:expert_pool_dispatch_multi | Distinct experts across all window tokens are classified (VRAM / peer / helper / PCIe / CPU / skip). Each CPU expert is computed once for all its tokens, and the GPU zeroes its own rows. | on | - | **PARTIAL**: our hybrid is capped at 4 tokens (max 8) because CUDA MMQ/MMF `mul_mat_id` breaks on repeated dummy ids. Skip ids < 0 in `mm_ids_helper`/MMQ/MMF ("0003-next"). M |
| PCIe share of misses | expert_source.cpp dispatch_multi `pcie_num`; paper Finding 9; DETAILS "The PCIe share" | Each window, the last ⌊n_miss·share⌋ distinct misses are computed by the GPU from pinned host memory (copy-engine staging or kernel reads of mapped memory) while the CPU computes the rest. | on (0.2 / 0.55 / 0.25 by quant) | IQ2_XS 70 → 78, IQ3_XXS 56 → 65 tok/s; NVFP4 103 → 117 | **MISSING-FEASIBLE** M-L: a third branch in `build_moe_ffn` that uploads a share of the misses into scratch slots (or zero-copy `mul_mat_id` from host-registered memory) |
| GPU copy kernel instead of driver memcpy | DETAILS Troubleshooting #31 | In verify windows experts are copied by a GPU kernel from mapped memory, because driver memcpy hung inside the NVIDIA driver. | on (0.1.14) | Fixes a hang | **MISSING-FEASIBLE** M, part of the PCIe share |
| Route tail skip | generate.cpp ~8618-8665; expert_source.cpp dispatch_multi; QUANTS; DETAILS | A missed expert that **every** token of a verify window routes at rank 7 or worse (of 10) is neither copied nor computed, and weights are not renormalised. It switches itself off at ≥ 90 % cached (60 % on HIP splits), with batch slots, a peer or helper GPU, or NVFP4 experts. | **on** (CUDA 0.1.42+, RDNA4 0.1.43) | +10-20 % decode on 12-16 GB cards (+26-67 % in budget mode); KL 0.003-0.006 | **PARTIAL**: `--moe-cache-tail-skip N` keeps the top-N misses per token by weight and is off by default. Add Strata's window-wide rank rule plus the auto-off gates. S |
| Split-window intra-layer overlap | paper Finding 5 | Two token groups so one group's CPU work overlaps the other's GPU work. It is exact but 7 % slower. | not used | −7 % | **COVERED** (in the sense that Strata tells us not to build it) |
| Next-layer expert prediction | paper §7 | Prose future work: predict the next layer's experts and start their copies early. | not built | - | **COVERED**: `--moe-cache-foresight` |

## 5. CPU expert pool

| Technique | Where | What it does | Default | Impact | Our status |
|---|---|---|---|---|---|
| One worker per physical core, host core reserved, P-cores first | pool.cpp:`detect_cpu_topology_impl`, `ExpertPool` ctor; pool_affinity_win.hpp | Uses physical cores − 1 workers, never SMT siblings (they share load/store bandwidth), each hard-pinned with group affinity. Hybrid CPUs use P-cores first and E-cores only in Auto mode. | on | SMT pairs halve per-expert bandwidth | **PARTIAL**: `-t`, `--cpu-mask`, `--cpu-strict` exist and `cpu_topology.py` builds P-core masks, but only as tray buttons. Auto-apply "first logical CPU of each P-core, strict" for MoE cpu-miss models. S |
| Power-throttling (EcoQoS) opt-out | serve/winjob.py:no_throttle (#691) | `SetProcessInformation(ProcessPowerThrottling)` disables execution-speed and timer throttling, so Windows 11 does not move a background or minimised engine's threads to E-cores. | on (Windows) | i9-13980HX 36.6 → 43.2 tok/s (+18 %) | **MISSING-FEASIBLE** S, **high value**: telecode spawns llama-server windowless from `pythonw`. Apply after spawn in `process.py` |
| Host thread drains jobs too | pool.cpp `run_phase` | The calling thread claims jobs instead of spinning on "done". | on | 33.7 → ~44 GB/s effective | **COVERED**: ggml thread 0 computes |
| Spin 20 ms then sleep | pool.cpp:worker `kSpinBeforeSleep` | Workers spin on `_mm_pause`, then wait on a condition variable, with lost-wakeup-safe ordering. | on | - | **COVERED**: `--poll` |
| Epoch-tagged lock-free claim | pool.cpp claim/begin_batch | A CAS on a packed (epoch, n, i) means a late thread can never take the next batch's jobs. | on | Correctness | **COVERED**: ggml threadpool chunk counter |
| Two-phase row split across all experts | pool.cpp `run_split_multi`; tasks = 3 × threads | Gate/up rows of all the layer's CPU experts are split into equal ranges, then the down rows after quantising h, so the tail is short whatever the expert count. | on | - | **COVERED**: ggml `mul_mat_id` chunking (Strata's balance across experts is finer; low value) |
| int8 (q8_K) activations for CPU rows | DETAILS "Short prompts"; native_expert.cpp | CPU expert rows use quantised activations. | on | KL ~0.006 | **COVERED**: ggml vec_dot |
| AVX2 Q8_K activation quantiser | native_expert.cpp `q8k_avx2` | Byte-identical AVX2 replacement for ggml-cpu's scalar x86 `quantize_row_q8_K`. | on | ~3 µs per token per layer before the pool starts | **MISSING-FEASIBLE** S, upstreamable |
| Multi-token "decode once" IQ kernels | native_expert.cpp `native_gu_rows` (MAXT 8, mt_min 2); paper Finding 7 | When an expert has ≥ 2 tokens in a window, each weight block is decoded once for all of them (AVX-512 / AVX2 / AVX1). | on | 2.0-2.4× ggml-cpu at 3 tokens | **MISSING-FEASIBLE** M: x86 multi-column (`nrc > 1`) IQ `vec_dot` in ggml-cpu, which also helps spec-verify on CPU layers of any model |
| Skip activation quantisation for rows with no CPU work | SECOND_GPU "remote-expert-opt" | Tokens whose experts are all on a GPU skip CPU activation quantisation. | on with that option | Small | **MISSING-FEASIBLE** S: skip `from_float` for rows whose ids are all < 0 |
| Q2_0 repack + AVX-512 VNNI kernels | paper §3.6, Findings 6-7; expert.cpp `s2_expert_vnni_q` | Q2_0 experts are repacked at load; one unpack and one VNNI dot per 64 weights. | on for Q2_0 | 39 → 52 GB/s (≈ RAM bound); down-proj 7× over scalar | **MISSING-FEASIBLE** M: ggml-cpu x86 Q2_0 `vec_dot`, only if we run Q2_0 GGUFs |
| AVX-VNNI paths chosen automatically | DETAILS "Switches added in 0.1.40" | i-quant / IQ4_NL / Q2_0 rows use AVX-VNNI where the CPU has it. | on | 4-25 % faster rows | **COVERED**: ggml-cpu variants (build-dependent) |
| ISA dispatch with OS-state checks | expert_layout.cpp `cpu_avx512_ok` | Picks the AVX-512 → AVX2 → AVX1 → ggml path at runtime, with XCR0 and per-core hybrid checks. | on | - | **COVERED**: `GGML_CPU_ALL_VARIANTS` |
| One contiguous blob per expert | expert_layout.cpp `expert_layout_load` | Gate, up and down of one expert live in one blob, so one DMA, pin or read covers the expert. | on (pack format) | 1 DMA per expert vs 3 | **MISSING-NOT-PORTABLE**: file format (llama keeps 3 tensors) |
| NVFP4 CPU kernels | native_expert.cpp | CPU rows for NVFP4 packs. | on for NVFP4 | 2× under DRAM load | **MISSING-NOT-PORTABLE** for us (no NVFP4 packs planned) |
| Reserved, pinned host core | session.cpp `SessionLoopScratch::init`; generate.cpp ~5062 | The spinning host loop gets the first physical core (or `last`, because Windows routes GPU interrupts to CPU 0). | on | CPU path 26.9 → 36.3 GB/s | **MISSING-NOT-PORTABLE**: llama.cpp has no spinning host-service thread |
| CUDA callback-thread warm-up before pinning | generate.cpp ~5034 | Creates the driver's callback thread before the host pins itself. | on (Linux) | 985 → 4 µs per callback | **MISSING-NOT-PORTABLE**: Linux, Strata's host-callback design |
| NUMA | pool.cpp | None; respects the allowed CPU set only. | - | - | **COVERED**: llama `--numa` does more |

## 6. Prefill: chunking and overlap with expert streaming

| Technique | Where | What it does | Default | Impact | Our status |
|---|---|---|---|---|---|
| Streamed walk through a VRAM ring | prefill.cpp `stream_all_min`, `run_impl`; paper §3.5 | From 1,024-token chunks up, every non-resident expert is copied in fixed layer/id order through a ring, before routing is known. The copy engine works through attention, and layer l+1's experts arrive during layer l's attention. | on (threshold 1,024) | 1.5K 621 → 785, 2K 727 → 934 tok/s | **PARTIAL**: stock op offload (batch ≥ 32) copies a layer's whole tensor when the op runs, with no look-ahead. Add a double-buffered pre-upload of layer l+1. M-L (patch 4) |
| Resident experts skipped by the stream | prefill.cpp seq build `host_res[...] >= 0` | Experts already in the VRAM cache are read in place and only the rest are streamed. | on | - | **MISSING-FEASIBLE** M: make the offload aware of `llama_moe_cache` contents |
| Routed-only staging below 1,024 tokens | prefill.cpp `stage_one`, `STAGE=8` | Smaller chunks read the router ids back and upload only routed non-resident experts through an 8-slot ring kept 8 transfers ahead. The shared expert is queued after the router. | on | - | **MISSING-FEASIBLE** M (ids readback or a GPU gather) |
| Ring sized in bytes | prefill.cpp `ring_bytes`, `ring_budget_slots` | The ring is 384 Q2_0 blobs (≈ 506 MiB) or 1,024 with fused kernels, turned into slots (16-1,024). It drops to 96 when less than 90 % of streamed experts are pinned. | on | Q2_0 8K: 1,153 → 1,294 tok/s; Q8_0 rig 87 → 402 | **MISSING-FEASIBLE** S, on top of the ring |
| Prompt buffers borrowed from the cache tail | generate.cpp `plan_lend`, `lend`/`refill` ~11409-11553; prefill.cpp `carve`/`relayout`; paper §3.5 | Prompt scratch, ring and GEMM workspace are carved out of the coldest cache slots (≤ 85 %, or 90 % when pinned, always keeping 128), then refilled with queued copies after the prompt. Adaptation pauses during the loan. | on with a profile | No permanent VRAM for prefill; 8,192 vs 4,096 chunk = 973 vs 791 tok/s | **MISSING-FEASIBLE** L: alias the compute buffer onto `llama_moe_cache` memory for large ubatches, then re-upload |
| `--prefill auto` chunk size | generate.cpp `biggest_chunk` ~6612-6760; prefill.cpp `ring_cap_for` (keep ring at ≥ 6,144) | Takes the largest chunk on a 256 grid (≤ 8,192; 16,384 on gfx12) whose buffers fit the lend budget, trading ring slots for chunk size below 6,144. | on (default since 0.1.39b) | 256 → 2,048 chunk: 185 → 667 tok/s; 32K prompt +14-26 % | **PARTIAL**: stock `-b`/`-ub` are static. Have telecode choose `ubatch_size` from free VRAM and model size. S |
| Short segments through decode windows | generate.cpp `short_read=64` ~11217 | Prompt parts of 64 tokens or fewer (a new message, an assistant header) go through verify windows with misses on the CPU instead of the batched streaming path. | on | A batched run costs ~300 ms + ~180 ms refill vs ~16 ms/token | **PARTIAL**: stock `GGML_OP_OFFLOAD_MIN_BATCH` (32) is env-only, and telecode cannot set env (parity #4). S |
| CPU share of prefill | prefill.cpp `cpu_share_*`, `arm_cpu_share`; generate.cpp ~7202-7289; DETAILS "Short prompts" | For chunks under 1,024 tokens (1 GPU, no batch slots, enough RAM and commit), the CPU pool computes the least-routed non-resident experts (≤ 8 tokens each) while the GPU streams the rest. Share = g/(c+g) from EMAs (α 0.25), clamped to 0.05-0.9. | **on** since 0.1.41 | −20..−49 % prompt time at 512-3,000 tokens; KL ≈ 0.004 | **MISSING-FEASIBLE** M: needs the skip-negative-ids MMQ/MMF fix, then `--moe-cache-cpu-miss` for prefill ubatches with a balanced share |
| CPU-share A/B gate | prefill.cpp `cpu_gate` (5 ratios, 3 minimum, probe every 29 layers) | Compares wall time per expert on adjacent layers with and without the share; the median decides, and the other arm is re-probed periodically. | on with the share | Stops losses (#1282) | **MISSING-FEASIBLE** S, after the share |
| Issuer thread for ring copies | prefill.cpp `issuer` | A pinned host thread issues the copies, so the launch thread never waits behind a host memcpy. | on | Removed GPU idle in i-quant prompts | **MISSING-FEASIBLE** M, with the ring |
| Stager threads for unpinned blobs | prefill.cpp `Stager` (clamp(hw/4, 2, 4) threads, 16-slot pinned ring, per-layer generation CAS) | Unpinned blobs are memcpy'd in parallel into a pinned ring, then DMA'd. | on | ~15 s of a 32K IQ3_S prompt | **MISSING-FEASIBLE** M: ggml copies pageable memory through one staging path |
| SSD stager profile from page residency | prefill.cpp `init` (`cached_share(256)` < 0.9) | When the GGUF's pages are mostly not resident it switches to 32 threads, a 128-deep ring and 8-blob runs. | on | NVMe 4K chunk 71 → 29 s | **MISSING-FEASIBLE** M, only matters when the model exceeds RAM |
| Stager wait style per OS | prefill.cpp `stager_sleep` | Spins on Windows, sleeps on Linux. | per OS | DGX Spark 91 → 1,222; Windows −1.2 % with sleep | **MISSING-NOT-PORTABLE**: only exists with the stager design |
| One gather and one wait per 16 experts | prefill.cpp `group_gather`, `MMQ_GROUP=16` | Inside the ring, 16 experts are gathered in one launch after one wait. | on | 226 ms of a 32K NVFP4 prompt | **MISSING-NOT-PORTABLE**: ggml MMQ `mul_mat_id` is already one launch |
| Grouping tables in mapped pinned memory | prefill.cpp `grp_host`/`grp_dev` | Routing tables never queue behind expert copies. | on | 128K prompt 4.2 → 0.7 s | **COVERED**: `mm_ids_helper` builds them on the GPU |
| Fused int8 tensor-core prompt experts | moe_fused.cu `enabled()`; paper Finding 11 | `mma.sync` int8 kernels quantise activations once, with no host sync; default only for the Q2_0 pack on sm80+. | on (Q2_0) | +13-23 % prompt | **COVERED** in substance by stock MMQ (Q2_0-specific kernel not portable) |
| Fused tile chosen from rows per expert | moe_fused_iq.cu `pick_ww` | A 128-row work item at 56-112 rows per expert, otherwise 64. | on | ±10-20 % per layer | **COVERED**: MMQ `mmq_x` selection |
| GPU activation quantisation bit-matched to ggml | quantize_act.cu | GPU int8 conversion reproduces ggml's exactly, so CPU and GPU experts agree. | on | KL | **COVERED**: we use ggml on both sides |
| One scratch for attention and MoE | prefill.cpp `*_set_bytes` | Attention and MoE scratch are never live together, so they share one region. | on | ~260 KB of ~680 KB per token | **COVERED**: ggml graph allocator |
| Pageable fallback for every pinned allocation | prefill.cpp `force_pageable` | Falls back to pageable memory instead of failing. | on | Robustness | **COVERED**: ggml host buffer fallback |
| Pinned upload ring for small inputs | prefill.cpp `PinnedUpload` | Token ids, tables and image rows (≤ 4 MB batches) go through event-guarded pinned buffers. | on | Fixed stale reads | **COVERED**: ggml pinned input staging |
| Batched embedding gather; MTP draft KV filled in the prompt pass | prefill.cpp C-4, `draft_kv` | One kernel per chunk; draft-layer KV appended in the same pass. | on | - | **COVERED**: `get_rows`; draft-mtp processes the prompt |
| PLE rows read ahead and batched as GEMMs | prefill.cpp `ple_gather`, `ple_batch` | Double-buffered SSD reads on a thread; projections over the chunk. | on (PLE models) | ~0.4 s of a 32K prompt | **MISSING-NOT-PORTABLE**: model feature (patch 11) |
| Read-ahead of model files at start | DETAILS "Read-ahead at start"; TROUBLESHOOTING | WILLNEED in 128 KiB steps, 256 fill pairs ahead. | on | Cold start 920 → 70 s | **COVERED**: stock `PrefetchVirtualMemory` / `MADV_WILLNEED` |
| Head alias (head VRAM lent to prefill) | PREFILL_HEAD_ALIAS | Unloads the output head during prompt reads and reloads it before decode. | off | 8 GB card +125 % prefill, +0.6 s on short reads | **MISSING-FEASIBLE** L, low value on 12 GB |
| Prefill progress beat per layer and chunk | prefill.cpp `progress_beat` | Each layer reports progress so watchdogs do not kill a long chunk, and a stall names its layer. | on | - | **PARTIAL**: llama-server `return_progress` exists; there is no watchdog using it. → §13 |

## 7. KV cache layout and streaming

| Technique | Where | What it does | Default | Impact | Our status |
|---|---|---|---|---|---|
| 8-bit KV chosen automatically at longer contexts | paper §3.7, §4; DETAILS "Speed" | Above ~4-8K tokens KV is int8, freeing VRAM for experts. | setup auto | Part of 262K 95 → 56 tok/s staying bounded | **PARTIAL**: `cache_type_k/v` are manual. Add a telecode rule (q8_0 above a ctx threshold for MoE-offload models). S |
| KV streaming from 64K | HOW_IT_WORKS; DETAILS "KV streaming"; layer.cpp `kv_plan`; kv_stream.cu | Only the read part of KV stays in VRAM (≥ 20,480-cell pool, clock-sweep eviction, pinned mapped host copy). It works because the sparse-attention indexer names the blocks it will read; setup turns it on from 64K, never under WSL. | setup auto ≥ 64K | 262K: 50.9 → 62.6 tok/s | **MISSING-FEASIBLE** L for models with a sparse-attention indexer (patch 5/23); not portable to dense attention |
| KV prefetch on a side stream | KV_PREFETCH; prefill.cpp `kv_prefetch_after` | Uploads the next layer's streamed KV during the current layer. | off | −4-5 % prefill time at 32-64K | **MISSING-FEASIBLE** M, after KV streaming |
| MTP layer attends only to the last 32,768 positions | paper §3.7; Options `mtp_window`; layer.cpp ring cells | The draft layer's KV is a sliding window (a ring over a pinned host copy when streaming), which bounds draft cost at long context. Verification keeps the output exact. | on | Keeps drafting cheap at 262K | **MISSING-FEASIBLE** M: SWA mask/KV for the nextn layer only |
| Draft-layer KV never 4-bit | mtp.cpp `load` | The draft layer keeps int8 or full-precision KV whatever the main format is. | on | - | **MISSING-NOT-PORTABLE**: llama.cpp has one KV type per context |
| Hadamard-rotated q4 KV; K8V4 | kv_q4.cu; DETAILS "4-bit KV", "Hybrid K8V4" | Q4 values are always rotated (FWHT-256); K8V4 = 8-bit keys and rotated 4-bit values. | on with q4 | +4 % at 128K; 85 → 99 tok/s | **COVERED**: upstream automatic rotation + `-ctk q8_0 -ctv q4_0` |
| RoPE tables in float64 on the host | layer.cpp `qsa_state_init` | Built once and shared by every layer and the drafter. | on | Accuracy | **MISSING-NOT-PORTABLE**: ggml computes RoPE in-kernel; low value |

## 8. Speculative decoding controller

| Technique | Where | What it does | Default | Impact | Our status |
|---|---|---|---|---|---|
| MTP drafts up to 3, one verify pass | paper §3.3; HOW_IT_WORKS | The MTP head drafts and one pass over all layers verifies; the first window after a prompt has T = 1, and windows are capped at 8. | on | 1.6-1.8×, 2.4-3.2 tokens per window | **COVERED**: `--spec-type draft-mtp`, `--spec-draft-n-max` |
| Draft probability gate | paper §3.3; mtp.cpp `draft` | The chain stops when a draft's probability falls below `min_p`; without it every step launches back to back. The engine default is 0 and the paper used 0.5. | 0 (paper 0.5) | - | **COVERED**: `--spec-draft-p-min` |
| Cost-model draft policy | spec/draft_policy.cpp `choose`/`observe`; generate.cpp ~9681, 12750 | Each round it chooses MTP or prompt lookup, and the window size, by expected tokens per measured ms. Round cost per size is an EMA (α 0.1, prior {1, 1.35, …, 3.6}); MTP yield is an EMA (α 0.05, prior 1 + 0.7(t−1)); lookup acceptance is bucketed by match length (< 6/12/24/≥ 24, priors 0.75-0.96, decay 0.97). | on | - | **PARTIAL**: `--spec-draft-adaptive` (off by default) learns length only; it does not arbitrate MTP against lookup per round. M |
| Probe and staleness re-measure | draft_policy.cpp (`kProbes 3`, `kStaleRounds 300`) | A size measured fewer than 3 times, or not for 300 rounds, is retried when confidence is high, so a slow stretch (cache refill) cannot price a size out forever. | on | - | **PARTIAL**: `--spec-draft-adaptive-probe` probes a neighbour, with no staleness rule. S |
| Prompt lookup gated on MTP agreement | generate.cpp ~2564-2570, 12571 | Lookup (match ≥ 3 tokens) may fill the window only if MTP's first draft equals the lookup's first token and the policy expects a gain; the window grows to spec + 2 (≤ 8). | on (`suffix_draft=3`) | Code edits +6-11 %, plain text unchanged | **PARTIAL**: stock `ngram-*` spec types, with no agreement gate or cost choice. S-M in `common/speculative.cpp` |
| Lookup chained after MTP drafts | draft_policy.cpp `chain`; generate.cpp ~12595 | After t MTP drafts a lookup continuation of those drafts is appended when it pays (chain prior per bucket {0.45, 0.65, 0.8, 0.9}, learned only from rounds that reached the chain). | on | - | **MISSING-FEASIBLE** M: unverified whether `draft-mtp,ngram-mod` chains this way; build it in `common/speculative.cpp` |
| Suffix drafter (trigram hash, longest match) and multiple sources | spec/suffix_drafter.cpp, draft_source.cpp | Keeps the 4 newest positions per trigram; the longest match wins and may run into the current suffix. | on | - | **COVERED**: stock ngram spec types |
| MTP catch-up only to the accepted row | mtp.cpp `draft` (`T = a + 1`) | Draft-layer KV is refreshed only for accepted rows; rejected rows are overwritten next round. | on | - | **MISSING-FEASIBLE** S: check whether upstream draft-mtp recomputes rejected rows |
| Draft head over a token subset | mtp.cpp `bind` (`draft_vocab.bin`); DETAILS "draft layer's tokens"; paper §3.3 (40,525 tokens) | The MTP head scores only a shipped subset of the ~248K vocabulary and maps ids back, which cuts the head GEMV per draft step. | on when the file exists | Head ~110-180 MiB; CJK subset +15-38 % | **MISSING-FEASIBLE** M: gather rows of `output.weight` into a draft-only head + remap, plus a corpus frequency tool. Works for any model with MTP or a draft model |
| Draft layer's experts fully in VRAM | mtp.cpp `load` | The MTP layer's 512 experts are uploaded whole, so drafting never misses. | on | - | **COVERED**: `-ncmoe` counts from layer 0, so the nextn layer stays on the GPU. Verify with `--moe-cache-mib` |
| Batched commit replay; T = 1 self-commit | verify.cpp `capture_commit`/`commit` | Recurrent state is recomputed only for the kept steps, batched over all GDN and sparse-attention layers; a 1-token window commits itself. | on | ~70 graph nodes fewer per commit | **PARTIAL**: upstream rolls back hybrid memory for draft-mtp, without the batching or T = 1 skip |
| Async commit overlapped with the next draft | verify.cpp `set_commit_async` | The commit graph is not waited on; the drafter, on its own stream, reads only the final rows. | on | - | **MISSING-FEASIBLE** M: draft context on a second stream |
| Sampled drafts judged correctly | DETAILS "Sampled drafting"; `coupled_draft_env` | At temperature > 0 a draft is kept only if it equals the sampled token (rejection sampling via `STRATA_SPEC_PROB`). | on | Higher acceptance at T > 0 | **COVERED**: stock behaviour + `--spec-draft-sampling probabilistic` (no telecode knob, parity gap #5) |
| Penalties on every verified token | DETAILS "Current limits" | Repetition penalties apply per verified position. | on | Penalised requests 1-11 % slower | **COVERED**: stock |
| Alternate controller with expert-union cost model | spec/controller.cpp | Prices a window as dense(n) + (1 − hit)·cpu_miss·U(n) + sync + draft (U = {1, 1.70, … 5.2}). Built but not used by the main loop. | not used | - | **COVERED** in effect: `--spec-draft-adaptive` measures time per length directly |

## 9. Sampling and output head

| Technique | Where | What it does | Default | Impact | Our status |
|---|---|---|---|---|---|
| Greedy argmax inside the captured window graph | verify.cpp `record_window` | Greedy picks are recorded in the graph with no extra launch or sync; sampled or penalised rows are re-sampled after replay. | on | - | **COVERED**: stock backend (GPU) sampling |
| Multi-CTA / thread-block-cluster argmax | sampler.cu `sampler_greedy_cluster_kernel`; DETAILS 0.1.36 | An 8-CTA cluster on sm_90+ (multi-block on sm_80-89) shares one 248K-logit row; a single block per row is latency-bound. | on | 40 → 6 µs per call; 128K 64.5 → 76.4 tok/s (with cluster select) | **MISSING-FEASIBLE** S: ggml-cuda argmax for wide rows; helps every large-vocab model |
| Split top-k sampler (top_k capped at 64) | sampler.cu split/merge | Blocks of 4,096 logits keep top-k each, and one warp merges them in llama.cpp sampler order. | on | Sampled path ~30× faster than its old kernel | **COVERED**: stock GPU top-k (do not copy the 64 cap) |
| Penalty membership bitmap | sampler.cu | O(1) history membership test. | on | Fixed a 45 → 31 tok/s regression | **COVERED**: not a cost in stock |
| Position-keyed Philox seed | verify.cpp `sp.counter = pos0`; BATCHING "Exactness" | A sampled row's draw is Philox(seed, position), so a seed replays the same text whatever the drafts or batching. | on | Reproducibility | **MISSING-FEASIBLE** S, low value |
| Non-finite logits guard | verify.cpp `nan_guard_on`; server.py `_run` (#879); DETAILS | A per-window flag for NaN/Inf. The request ends, every cached state is dropped, and the server retries once invisibly if nothing was streamed yet. | on | 4.8 µs per window | **MISSING-FEASIBLE** M: llama-server check + slot/cache erase; proxy retries once when nothing was streamed |
| Fused final hyper-connection read | verify.cpp head | One launch for the final mixer of all window tokens. | on | - | **MISSING-NOT-PORTABLE**: model-specific graph |
| Interleaved MMVQ for the head with 2-4 rows | verify.cpp head (`native_mmvq_il`) | → §15 | | | → §15 |

## 10. Session and conversation reuse

| Technique | Where | What it does | Default | Impact | Our status |
|---|---|---|---|---|---|
| Live-session continuation from the longest prefix | generate.cpp ~10919; HOW_IT_WORKS; README | A request resumes from the longest of live session, checkpoint, parked conversation or idle slot that prefixes it. | on | 57,745-token restore in 0.3 s | **COVERED**: slot prefix reuse + `--cache-ram` |
| Checkpoint at the turn boundary | generate.cpp ~11702-11718; BATCHING; DETAILS "Conversation cache" | The prompt is read in two parts with a checkpoint at the last turn-start token, because the next turn re-renders history without this turn's thinking. A finished slot keeps this checkpoint. | on | Avoids minutes of re-read per turn on long chats | **MISSING-FEASIBLE** M (parity #11): a ctx checkpoint at the last turn-start token in llama-server, taking the token from the chat template |
| Root checkpoint at the end of the system prompt, pinned | generate.cpp ~11719-11732 (`prompt_cache_root=2048`) | Any read from token 0 also checkpoints at the first turn boundary if it is ≥ 2,048 tokens; the checkpoint is never evicted, so new chats with the same system prompt and tools skip it. | on | - | **MISSING-FEASIBLE** M (same patch). Matters most for hybrid or recurrent models, which cannot rewind |
| Up to 6 checkpoints, LRU, plus one every 16,384 tokens | Options `prompt_cache=6`; generate.cpp ~8545 | Bounded checkpoint set during long reads. | on | - | **COVERED**: `--ctx-checkpoints`, `--checkpoint-min-step` |
| Checkpoint = recurrent state only | conversation_snapshot.cpp; generate.cpp | Copies the GDN recurrences, indexer tails and PLE history (~118 MB); positional KV stays in place. | on | Cheap checkpoints | **PARTIAL**: upstream hybrid checkpoints; size per checkpoint unverified |
| Retained KV pages between parks | DETAILS "Snapshots" | Unchanged pages are reused on the next park and growth is appended; oversize falls back to normal prompt processing. | on with the conversation cache | - | **PARTIAL**: `--cache-ram` stores whole states (delta capture not worth porting) |
| Snapshot includes draft KV and partial page | conversation_snapshot.cpp, conversation_state.cpp | Restores leave the drafter consistent. | on | - | **PARTIAL**: unverified for stock draft-mtp + `--cache-ram` |
| Format-tagged snapshots; session-file preflights | conversation_snapshot.cpp; DETAILS "Session files" | A snapshot never restores into a different KV format; saves check disk (+4 GB), RAM and fingerprints and write atomically. | on | - | **COVERED**: state files check type; `--slot-save-path` (fewer guards) |
| Host-memory guard for snapshot storage | conversation_memory.cpp `sample_host_memory` | Room = min(MemAvailable or cgroup room, cap); on Windows min(AvailPhys, AvailPageFile), because commit can refuse while RAM is free. | on | - | **MISSING-FEASIBLE** S: telecode sizes `cache_ram` from this at spawn instead of a fixed MiB |
| Slot chosen by longest held prefix, else empty, else LRU | server.py `pick_slot` | Keeps other conversations cached for their next turns. | on with `parallel` | - | **COVERED**: `--slot-prompt-similarity` + LRU |
| Per-request shared-prefix pin (`strata_prefix`) | server.py `resolve_prefix`; RESEARCH_RUNS | Marks the first K messages or N tokens as a shared prefix whose checkpoint is never evicted, so N questions cost one prefix read. | per request | - | **PARTIAL**: no pinned cache entry in llama-server. M (port #22) |
| Cancelled request keeps its state | generate.cpp ~12798 | A request stopped between windows keeps the exact state it reached. | on | - | **MISSING-FEASIBLE** S, low value |
| Image content keys | server.py `prepare` (#2228) | The cache matches a re-encoded image by a hash of its normalised bytes. | on | - | **COVERED** (unverified): mtmd image chunk hash |
| Stable Claude Code prefix | DETAILS "Conversation cache" | The `cch=` billing header and `cc_version` are pinned so the prefix stays byte-stable. | on | - | **COVERED**: proxy `strip_client_system_noise` |
| Budget continuation reuses the held prefix | server.py `_run` | Wrap-up and forced-call continuations extend the cached prompt instead of re-reading it. | on | - | **COVERED**: in-sequence reasoning budget |

## 11. Memory pinning, registration and IO

| Technique | Where | What it does | Default | Impact | Our status |
|---|---|---|---|---|---|
| Experts copied into an anonymous arena instead of mmap | expert_source.cpp `ArenaExpertSource::open`; generate.cpp ~4722-5033 | All experts are read once by 6 loader threads into engine-owned RAM, so the OS cannot evict them like file pages. | on (`--mmap-experts` opts out) | Pool 1.79× faster than warm mmap, 3.7× than cold; 19 → 42.8 GB/s | **PARTIAL**: `--load-mode none` exists, but telecode does not default MoE-offload models to it. S, measure first |
| Whole arena page-locked, registered before touch, slice fallback | pinned.cu `PinnedArena`; paper §3.1, Finding 13 | `cudaHostRegister(Portable\|Mapped)` before any page is touched. On Windows, if a 34-43 GB range is refused it pins per-layer slices until refused and `VirtualLock`s the rest (working set + 512 MiB in 1 GiB chunks); PCIe reads never span ranges. | on | Enables DMA and the PCIe share | **PARTIAL**: stock `GGML_CUDA_REGISTER_HOST` pins whole buffers, is env-only (no telecode knob, parity #4) and has no slice fallback. M |
| WDDM pin cap | pinned.cu `sliced_pin_limit`, ByNeed 29 GiB; generate.cpp ~4903 (8 GiB with 2 contexts) | Caps page-locked memory under the WDDM budget (optionally DXGI budget − usage − 4 GiB). | on | A full pin left 0 GiB for the cache (#243) | **MISSING-FEASIBLE** S: needed as soon as we enable `GGML_CUDA_REGISTER_HOST` on 63 GB Windows |
| Pin by need | README "Optional"; PinnedArena ByNeed | Locks the experts the card needs first, in profile order. | off | +4.4 % prompt on Windows | **MISSING-FEASIBLE** M (parity #7), order from the stats file |
| Large pages for the arena | pinned.cu `reserve` | Windows enables SeLockMemoryPrivilege and tries `MEM_LARGE_PAGES` (needs a new logon); Linux tries hugetlb, then THP with safety checks. | on | ~750 TLB misses per 3 MB expert avoided | **MISSING-FEASIBLE** S-M: large pages in the ggml CPU buffer. Also helps dense models with CPU layers |
| Working-set trim after startup | expert_source.cpp (`SetProcessWorkingSetSize(-1,-1)`, `VirtualUnlock`) | Mapped pages touched during startup move to standby. | on (resident mode) | 0.44 GiB → real availability on 32 GB | **MISSING-FEASIBLE** S |
| Release RAM pages of VRAM-resident experts | expert_source.cpp `STRATA_FILE_RELEASE` | `VirtualUnlock` / `MADV_DONTNEED` for experts now in VRAM. | opt-in | - | **MISSING-FEASIBLE** S |
| Parallel loader with large freads | pinned.cu `load_experts_ranges`; mtp.cpp `read_file` | N threads, 8 MiB chunks; avoids MSVC's 4 KiB ifstream reads. | on | Minutes → seconds cold start | **COVERED**: stock loader |
| No `FILE_FLAG_RANDOM_ACCESS` | expert_source.cpp `FileExpertSource::open` | A plain mapping keeps read-ahead and retention. | on | 14× | **COVERED**: llama-mmap |
| Read-ahead hints | memory.cpp `advise_willneed` | `MADV_WILLNEED` in 128 KiB steps; `PrefetchVirtualMemory` batches. | on | - | **COVERED**: stock prefetch |
| SSD tier: direct reads, merged windows, batched fetch | expert_source.cpp `read_direct`, `fill_many`; platform/direct_file.cpp | 4 KiB-aligned reads merged into ≤ 32 MiB windows, overlapped I/O, 8-thread fetch of a layer's misses; the mapping is closed because NTFS serialises unbuffered reads of a mapped file. | on (SSD tier) | 2.35 → 3.57 GB/s | **MISSING-NOT-PORTABLE**: no SSD expert tier in llama.cpp (patch 24 would be a new subsystem) |
| Pinned bounce ring and stage buffers | expert_source.cpp `claim_stage`; pinned_ring.hpp | Buffers are reused after 3 layer changes and event-fenced. | mixed | - | **MISSING-NOT-PORTABLE**: Strata's source design |
| Linux pin guard | TROUBLESHOOTING #1250; `pin_cache_complement` | Drops its own file cache, page-locks in 1 GiB steps, keeps ≥ 2-4 GiB free. | on (Linux) | OOM-kill fix | **MISSING-NOT-PORTABLE** for us: Linux only |
| n-gram rows prefetched a step ahead | paper §3.1 Fig. 1; verify.cpp | 16 rows per token prefetched one step ahead. | always | ≤ 23 KB per token | **MISSING-NOT-PORTABLE**: model-specific PLE table |

## 12. Server and request handling (including reasoning)

| Technique | Where | What it does | Default | Impact | Our status |
|---|---|---|---|---|---|
| One request at a time, queue keepalive every 10 s | server.py `Service._run` (`QUEUE_BEAT_S`); README | Others wait in a FIFO with keepalives, and a client leaving while queued gives up its place. Parallel is opt-in and costs 11-24 % per request on 12 GB. | 1 | - | **COVERED**: `--parallel 1` + proxy heartbeat |
| Keepalive during prefill and held-back tool arguments | server.py `generate`, `_run` (`KEEPALIVE_S=10`) | Keepalives continue during long prompt reads and while a whole tool argument is held back. | on | - | **COVERED**: proxy `_start_heartbeat` |
| Responses keepalive as a real event | responses.py `Assembler.in_progress` | Sends `response.in_progress` instead of an SSE comment, because Codex's idle timer ignores comments. | on | - | **PARTIAL**: our `/v1/responses` passthrough sends comments. Emit `response.in_progress`. S |
| Context check, never truncate | server.py `prepare` (`CTX_SLACK=8`) | Rejects prompt + max_new + 8 > ctx with a 400 that says how much room is left; an unset max_tokens means the rest of the context. | on | - | **COVERED**: `exceed_context_size_error` + proxy `context_overflow` |
| 503 "starting" while the engine (re)starts | server.py `prepare` | No 0-context error during a restart. | on | - | **COVERED**: proxy `ensure_model` waits on `/health` |
| Stop strings, forced tool calls, structured output | server.py `StopMatcher`; structured.py | Stops are matched only in answer text; forced calls write the opening tokens; JSON is validated after the fact. | on | - | **COVERED**: llama-server stop + grammar (stronger) |
| Tool calls inside thinking delivered as calls | frontend.py `OutputParser._release` | A declared tool's call at line start inside reasoning, outside code fences, becomes a real call if the turn ends on a stop token. | on | - | **MISSING-FEASIBLE** S-M: proxy post-pass on `reasoning_content` when finish = stop and there are no tool_calls |
| Quoted `</think>` not treated as the end | frontend.py `_quoted_think_end` (#537) | A closing tag right after a quote or backtick and not followed by a newline is the model quoting it. | on | - | **MISSING-FEASIBLE** S: rule in the proxy `ReasoningState` |
| Control-token text inside messages stays text | server.py `encode_prompt` (`mark_think_literals`) | `<think>`, `</think>` and end-of-turn markers written inside a message are tokenised as plain text, which blocks prompt-structure injection. | on | - | **MISSING-FEASIBLE** M: llama.cpp patch to tokenise message content with `parse_special=false` |
| Unreadable tool images become a note | server.py `_note_unreadable_tool_images` | A tool image the model cannot encode becomes `[image omitted: why]` instead of a 400 that would break every later turn. | on | - | **MISSING-FEASIBLE** S: proxy, when the loaded model has no mmproj |
| Tool results reordered to call order | responses.py `_order_tool_results` | Each run of tool outputs is sorted into the order of the assistant's calls. | on | - | **MISSING-FEASIBLE** S: `normalize_responses_request` and the chat path |
| Malformed function-call arguments kept | responses.py `_arguments` | Non-JSON arguments become `{"arguments": raw}` instead of rejecting the conversation. | on | - | **MISSING-FEASIBLE** S: proxy translate |
| Empty assistant turns dropped from history | TROUBLESHOOTING #843 | Finished assistant turns with no text are left out. | on | - | **MISSING-FEASIBLE** S: proxy filter (our `_drop_empty_turns` handles stripper output only) |
| Single-token repeat stop | server.py `_run` (`REPEAT_STOP_TOKENS=256`); DETAILS | Ends a reply after 256 identical tokens in a row, as "length". | **on** (256) | Saves runaway tokens (one report: 36,689 `!`) | **MISSING-FEASIBLE** S: proxy stream watcher that cancels upstream |
| Thinking budget keeps room for the answer | server.py `_run` (`ANSWER_RESERVE_MIN=512`) | Budget is capped at max_new − min(max(512, max_new/4), max_new/2), so thinking closes early enough to leave room for an answer. | on with a budget | Avoids empty answers | **MISSING-FEASIBLE** S: clamp `thinking_budget_tokens` against `max_tokens` in `_prepare_internal_body` |
| Budget wrap-up text at a clean boundary | server.py `REASONING_WRAP_UP` | Injects a closing sentence and `</think>` without splitting a tag or character. | on with a budget | - | **COVERED**: `--reasoning-budget` |
| Hint when max_tokens is hit inside thinking | server.py `_run` (#530) | Logs a suggestion to set a budget. | on | - | **MISSING-FEASIBLE** S, diagnostic only |
| Anthropic requests think by default; default effort filled in | server.py `main`, `effort_default` | Default thinking and effort for requests that do not specify them. | model | - | **COVERED**: per-model thinking key + `reasoning_effort_map` |
| Reasoning replay through `encrypted_content` | responses.py `_reasoning_text` | A stateless server can put the thinking back into the prompt. | on | - | **COVERED**: per-request `reasoning_format: deepseek` |
| Template capability probes at load | DETAILS "chat_template_caps" | A failed probe turns off only that feature. | on | - | **COVERED**: `proxy/llama_caps.py` |
| Body and image-fetch caps | server.py (`CHUNKED_BODY_MAX` 256 MiB, `IMAGE_URL_MAX` 32 MiB) | Limits enforced before reading. | on | - | **COVERED**: aiohttp limit + `media_fetch` cap |
| Vision encode serialised with decode | server.py `prepare` | Image encoding takes the same turn as generation because concurrent GPU use hung. | on | - | **COVERED**: mtmd runs on llama-server's thread |
| Solo path ↔ batch-slot migration | BATCHING; server.py `_may_go_solo` (`SOLO_AGAIN_MAX=2`) | A lone request uses the solo MTP path, moves into a slot when a second request arrives, and returns when alone again. | on with `parallel` | - | **MISSING-NOT-PORTABLE**: llama-server slots always batch |
| Prompt chunks interleaved with batch decode; yield to a shorter prompt | generate.cpp `read_part`; server.py `_take_control` (`YIELDS_MAX=2`) | Slots decode for 0.5× a chunk's time between chunks, and a long read yields at a chunk boundary to a prompt at most half its length. | on with `parallel` | - | **COVERED**: continuous batching (no preemption; low value) |
| Port check before load | server.py `main` | Binds and frees the port before the minutes-long load. | on | - | **MISSING-FEASIBLE** S: supervisor pre-spawn bind check |
| `CUDA_DEVICE_ORDER=PCI_BUS_ID` | server.py `child_env` | CUDA numbering follows nvidia-smi. | on | - | **MISSING-FEASIBLE** S, needs `llamacpp.env` |
| CPU flash-attention off for a CPU vision encoder on AVX-512 | DETAILS Images (#660) | - | auto | 43.5 → 13.3 s encode | **MISSING-NOT-PORTABLE** here: our build is AVX2 and mmproj runs on the GPU |

## 13. Watchdogs and recovery

| Technique | Where | What it does | Default | Impact | Our status |
|---|---|---|---|---|---|
| Engine no-progress watchdog, 60 s | generate.cpp ~9612-9669 (`STRATA_WATCHDOG_S`); pool.cpp `kStall`; DETAILS #29 | If no step finishes for 60 s it prints a stall report (stage, VRAM, pool and verifier state, memory, disk waits, Windows minidump) and aborts, and the next request restarts the engine. An active file tier gets up to 10× the limit. | on | - | **MISSING-FEASIBLE** S: supervisor or proxy restart when an in-flight request emits nothing for N s, logging nvidia-smi and process memory |
| Server silence watchdog, adaptive during prefill | server.py `generate` (`ENGINE_SILENCE_S=300`, `PP_FLOOR_TOK_S=50`, `PP_SLACK=3`) | The allowed silence grows with prompt length before the first chunk and becomes 3× the previous chunk afterwards. | on | - | **MISSING-FEASIBLE** S-M: same mechanism, timed from llama-server `return_progress` |
| Frozen-engine detector | server.py `_stall_check` (90 s, CPU < 0.5 s, I/O < 1 MiB, GPU idle) | Kills only if the engine is truly idle; otherwise warns once. | on | - | **MISSING-FEASIBLE** S: psutil + nvidia-smi in `LlamaSupervisor` |
| Transient start retry; restart retries | server.py `StrataEngine.__init__` (3 tries, 2/4/8 s), `restart` (3 × 15 s) | A start that dies before READY, or with an empty log, is retried while the previous process's VRAM is released. | on | - | **PARTIAL**: `_free_vram_short` re-polls VRAM; there is no spawn retry. S |
| Restart on the next request | server.py `ensure_loaded` (#27); TROUBLESHOOTING | A dead engine is restarted transparently. | on | - | **COVERED**: `LlamaSupervisor.ensure_model` |
| READY timeout | server.py `ENGINE_READY_S=900` | Kills a start that never becomes ready. | on | - | **COVERED**: `llamacpp.ready_timeout_sec` |
| STOP drain bounded | server.py `generate` `finally` | An engine that never answers STOP after a hang-up is killed. | on | - | **COVERED**: llama-server cancel + proxy `finally` |
| Death note diagnostics | server.py `death_note`, `_WIN_CRASH` | Explains an exit from the log tail and Windows NTSTATUS codes (illegal instruction → missing ISA, OOM). | on | - | **PARTIAL**: `llama.log` only. Decode NTSTATUS in the supervisor log. S |
| Kill-on-close job; SIGTERM as a clean quit | winjob.py `contain`; server.py `on_sigterm` | Children die with the server, and docker stop is graceful. | on | - | **COVERED**: `process.py` Job + supervisor shutdown |
| Engine rollback | TROUBLESHOOTING #670 | Keeps the previous engine build. | on | - | **COVERED**: Version Manager `.bak-<ts>` restore |
| Bounded GPU waits and spin release | verify.cpp `release_gpu_waits`; graph.cpp | 20 s per layer; mapped flags are forced so no spinning kernel outlives the process (otherwise Windows loses the GPU). | on | - | **MISSING-NOT-PORTABLE**: llama.cpp has no spin kernels |
| Fatal verifier lines end the engine; orphan slot reaper; failure latch | server.py `_pump`, `_reap_orphan_slots`; expert_source.cpp `failed` | Bookkeeping for Strata's own pipe protocol and dispatch. | on | - | **MISSING-NOT-PORTABLE** |
| Copy-failure fallbacks | expert_source.cpp `dma_batch`, ForesightSwap, `RemotePrefillSource` | A refused batch DMA falls back to per-blob copies; a failed foresight copy turns foresight off. | on | Robustness | **MISSING-NOT-PORTABLE**: tied to those Strata paths (add equivalents with our own async copies) |
| HIP environment repair | TROUBLESHOOTING #654 | Clears a stale `HIP_PATH` and fixes an unwritable TEMP. | on | - | **MISSING-NOT-PORTABLE**: AMD only |

## 14. Multi-GPU (low priority on a single 12 GB card)

| Technique | Where | What it does | Default | Impact | Our status |
|---|---|---|---|---|---|
| `--layer-split auto` cost model | generate.cpp ~3704-3960 | Minimises the predicted window time from SMs × clock, uncached routed mass and per-stage link probes, with an exhaustive search up to 4 GPUs. | on when `auto` | 2× 4090 on an x1 link: 350 → 1,400 tok/s prompt | **MISSING-FEASIBLE** M: hit-mass-aware `--tensor-split` planner in telecode |
| Faster card last | server.py `ordered_gpus` | Puts the fastest GPU on the last stage (head, draft, verify) unless scores are within 5 %. | on | 6K prompt 50 → 15 s | **MISSING-FEASIBLE** S |
| Peer tier, helper caches, helper adaptation | peer_experts.cpp; remote_experts.cpp; remote_expert_opt.cu; SECOND_GPU | A second GPU holds the next-ranked experts and adapts with the same thresholds; helpers reduce outputs on the GPU with spin scheduling and zero-copy. | on with `--peer-device` / 2+ GPUs | +28-132 % decode on 2 GPUs | **PARTIAL**: upstream multi-GPU cache split, without adaptation or the disjoint guard |
| Per-stage reserves, pin cap with 2 contexts, pipeline windows on gfx12, split skip | generate.cpp ~3445-3813 | Placement guards for layer splits. | mixed | - | **MISSING-NOT-PORTABLE**: other hardware |

## 15. Optimisations for SMALL or non-MoE models

Everything here helps a dense model, or any model running speculative decoding. Rows that are counted elsewhere carry
their section.

| Technique | Where | What it does | Default | Impact | Our status |
|---|---|---|---|---|---|
| Interleaved small-batch MMVQ (2-4 tokens) with a per-arch table | MMVQ_IL_TABLE; native_mmvq.cu; verify.cpp head | Decode and verify windows of 2-4 tokens read an interleaved q8_1 activation copy; rows per warp come from a measured table per architecture. | on sm_80+ (and its own sm_61 table) | P40 112 → 65 µs; head 2,545 → 1,766 µs | **MISSING-FEASIBLE** L (port #18): ggml MMVQ change for ncols 2-4 |
| Dense Q8_0 GEMV, one row per block for 2-4 columns | QUANTS | - | on | +1-2.7 % decode | **MISSING-FEASIBLE** M: MMVQ tuning |
| Multi-CTA argmax | → §9 | Every large-vocab model | | | → §9 |
| Draft-vocab subset head | → §8 | Any MTP or draft model | | | → §8 |
| Cost-model draft policy, lookup gate and chaining | → §8 | Any model with spec decoding (+6-11 % on code) | | | → §8 |
| Turn-boundary and root checkpoints | → §10 | Any hybrid or recurrent model | | | → §10 |
| AVX2 Q8_K quantiser; x86 multi-token IQ dot | → §5 | Any model with CPU layers; spec-verify on CPU | | | → §5 |
| Power-throttling opt-out; Normal priority | → §5 / §2 | Any model on a hybrid Intel laptop | | | → §5, §2 |
| Large pages for CPU weights | → §11 | Dense models with CPU layers | | | → §11 |
| One CUDA graph per window size | paper §3.2; verify.cpp `capture` | Each window size 1-8 is captured lazily; graph pairs are LRU-evicted when VRAM is short. | on | Host never syncs | **COVERED**: stock CUDA graphs (they break at our CPU splits, which is a MoE-only issue) |
| Serving without MTP gives its VRAM to the cache | DETAILS "Serving without the draft layer" | - | config | 1,000 → 1,678 slots | **COVERED**: omit draft-mtp |
| Wide dequant on sm_61; FP16 tensor-core path below sm_75 | OLDER_GPUS | Pascal and Turing paths. | on there | 5-22× dequant | **MISSING-NOT-PORTABLE** for us: old GPUs |
| GDN O(1) decode and chunked GDN prefill | DETAILS | Model layers. | model | - | **COVERED**: upstream has the layers |

---

## Prioritised: what to add next

Ranked by expected gain for a big MoE model on a 12 GB GPU with 64 GB RAM under Windows, per unit of effort. The current
state from `strata-port.md`: 38.2 tok/s with our cache vs 54.6 tok/s for a static `-ncmoe 26`. Hit rate and CPU/GPU
overlap are therefore the open problems, and items 1-6 attack exactly those.

| # | Item | Section | Effort | Why |
|---:|---|---|---|---|
| 1 | **Hit poke**: query the stream after launching the GPU split that precedes an overlapped CPU split | §4 | S | Strata measured 17 → 9,190 of 10,562 layers overlapped once it did this. Our 0002 overlap probably suffers the same WDDM batching; measure before and after |
| 2 | **Route tail skip as Strata ships it** (window-wide rank ≥ 7, auto-off at ≥ 90 % cached, with batch slots or NVFP4) | §4 | S | Default-on upstream, +10-20 % decode, KL 0.003-0.006. Our flag has a different rule and is off |
| 3 | **Power-throttling opt-out + Normal priority for llama-server** | §5, §2 | S | +18 % measured on a hybrid Intel CPU. Telecode spawns windowless from `pythonw` / a Scheduled Task, which is the exact case where Strata saw throttling and 24× slower loads |
| 4 | **Round-based adaptive swap mode** (every 4 windows, count ≥ 2.0, +1.5 additive, ×0.7 decay, ≤ 96 global swaps) on by default with cpu-miss | §3 | S | Strata's hit rate 0.50 → 0.72 comes from this exact policy; ours is per-miss and off |
| 5 | **Async promotion with admit-on-landing** (side stream + event, evicted expert to CPU at once, lag 1) | §3 | M | ×1.07-1.11 decode; removes PCIe waits from the decode path |
| 6 | **Lift the 4-token hybrid cap** (skip ids < 0 in `mm_ids_helper`, MMQ, MMF, MMVF) | §4 | M | Prerequisite for whole-window miss plans, longer verify windows and the prefill CPU share (item 9) |
| 7 | **Auto `moe_cache_mib`** after load, with touch-and-recheck, a smaller retry on OOM, and post-load and per-request VRAM warnings | §1 | S-M | A slightly oversized cache is a 7× WDDM cliff; long contexts need a smaller cache automatically |
| 8 | **`llamacpp.env` setting** used for `CUDA_MODULE_LOADING=EAGER`, `GGML_CUDA_REGISTER_HOST` (with a WDDM pin cap), `GGML_OP_OFFLOAD_MIN_BATCH` and `CUDA_DEVICE_ORDER` | §1, §6, §11 | S | Unlocks four default-on Strata behaviours at once |
| 9 | **CPU share of prefill** for ubatches under 1,024 tokens, with the A/B gate | §6 | M (after 6) | −20..−49 % prompt time at 512-3,000 tokens, which is the agent-turn regime |
| 10 | **PCIe share of misses + startup probe + PcieAuto** | §4, §2 | M-L | +11-30 % decode where the CPU is slower than the link |
| 11 | **Supervisor watchdogs**: no-progress / frozen detection with restart, a transient start retry, NTSTATUS decoding | §13 | S | Strata ships three layers of these; telecode has none mid-request |
| 12 | **Default `load_mode: none` for MoE-offload models** (arena instead of mmap) | §11 | S (measure) | Strata: CPU expert rate ×1.8 over warm mmap |
| 13 | **Turn-boundary + pinned root checkpoints** | §10 | M | Long agent chats on hybrid models stop re-reading minutes of history after each turn |
| 14 | **Prefill look-ahead ring** that skips cache-resident experts and streams layer l+1 during layer l's attention (routed-only below 1,024) | §6 | M-L | +15-28 % prompt at 1.5-4K tokens |
| 15 | **Proxy items**: 256-token repeat stop, thinking-budget answer reserve, tool calls rescued from reasoning, quoted `</think>`, empty assistant turns, tool-image note, Responses `in_progress` keepalive, tool-result order | §12 | S each | All default-on in Strata; pure telecode work |
| 16 | **Spec controller**: MTP-vs-lookup arbitration by match-length bucket, MTP-agreement gate, lookup chained after MTP, staleness re-probe | §8 | M | +6-11 % on code edits; helps every model |
| 17 | **Draft-vocab subset head** for MTP | §8 | M | Cuts the draft head GEMV; frees VRAM for the cache |
| 18 | **Multi-CTA argmax** in ggml-cuda | §9 | S | 40 → 6 µs per call, every window and draft step, any model |
| 19 | **CPU kernels**: AVX2 Q8_K quantiser (S), x86 multi-token IQ `vec_dot` (M) | §5 | S / M | Both upstreamable; 2-2.4× for experts shared within a window |
| 20 | **Mapped pinned zero-copy for CPU-split inputs and outputs** | §4 | M | Strata measured 67 WDDM flushes per token without it |
| 21 | **Smaller items**: auto q8 KV above a ctx threshold, `cache_ram` sized from available RAM and commit, P-core strict mask applied automatically, cache cap at the hot set, zeroed arena + slot readback, prefill `ubatch` from free VRAM | §1, §5, §7, §10 | S each | Defaults Strata applies that we leave to the user |
| 22 | **Larger items, later**: prompt buffers borrowed from the cache tail (L), KV streaming for indexer models (L), interleaved MMVQ (L), MTP sliding window (M), resident complement for low-RAM (L) | §6, §7, §15 | M-L | Real gains, but either large or of low value on this machine |
