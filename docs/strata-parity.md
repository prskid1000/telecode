# Strata parity: every Strata parameter against telecode + llama.cpp

Date: 2026-10-14 (updated). Inputs: `docs/strata-params.md` (738 rows; the task text said 725, the file header and the row count say 738, all are classified), `llamacpp/argv.py` tables, `llamacpp/config.py`, `tray/qt_sections.py`, the stock `llama/llama-server.exe --help` (741 lines), our patched build `build/llama.cpp/build-telecode/bin/llama-server.exe --help`, and greps of `build/llama.cpp` (base `fcac19079`, upstream b11609 plus our working-tree patches). Nothing was run beyond `--help`.

Status meaning: **IMPLEMENTED** = we built an equivalent (our patch series, `--moe-cache-*`); **COVERED** = stock llama.cpp or telecode already has an equivalent knob; **FEASIBLE** = not built, effort S (hours) / M (a day or two) / L (a week+); **N/A** = tied to Strata's engine, a hardware / quantization / kernel variant, a Strata-only API / UI / installer feature, or a diagnostic. "Unverified" marks things I could not confirm by grep. Settings keys: `model X` = `llamacpp.models.<m>.X`, `global X` = `llamacpp.X`; both can also be set through `extra_args`.

Notes on the mapping: upstream ships the GPU expert cache itself (`--moe-cache-mib`, `src/llama-moe-cache.cpp`, multi-GPU split like `--tensor-split`); our patches (`0002-ggml-skip-negative-expert-ids-and-cpu-split-overlap`, `0003-moe-cache-cpu-miss-prewarm-and-adaptive-draft`, plus later source changes in `build/llama.cpp`) add CPU compute on miss, promotion control, stats file and prewarm, route bias, foresight, tail skip, PCIe share, async upload, round swaps and adaptive draft length. After the latest work almost every llama flag has a telecode setting; the few that do not are listed under "COVERED but missing a telecode knob".

**Update 2 (same day, later).** Re-checked against `common/arg.cpp` in the source tree, `llamacpp/argv.py`, `tray/qt_sections.py`, `process.py`, `llamacpp/config.py`, `llamacpp/gguf_info.py`, `llamacpp/cpu_topology.py`, `tools/moe_cache_bench.py`, and corrected with `docs/strata-default-behaviours.md`. Strata's **tail skip is on by default** with a window-wide rank rule (rank 7, auto-off at 90 % cached); its **swap margin is additive** (cand >= victim + 1.5, ours `--moe-cache-promote-gain` is a ratio); its **CPU share of prefill (chunks under 1024 tokens) and its PCIe share (auto-refined at run time) are on by default**. Ours are all off by default, and our CPU path is capped at 31 tokens (`--moe-cache-cpu-miss-max-tokens` is 1..31), so the prefill share is not built. Caveats: patch file `0003` lists only the flags up to `--moe-cache-tail-skip`; `-tail-skip-rank`, `-tail-skip-auto-off`, `-evict-window`, `-pcie-share`, `-pcie-max`, `-async-upload`, `-swap-every`, `-swap-max`, `-route-top`, `-route-scale` exist in `common/arg.cpp` of the source tree but not in the patch files (grep), and not in the `--help` of the current `build-telecode` binary. So the patch files need regenerating and the build repeating (whether a newer build exists is unverified). The scheduler "hit poke" is `ggml_backend_event_query` after the GPU split in `ggml-backend.cpp` (grep); `cpu_overlap` is in patch 0002.


## Summary

| Status | Count |
|---|---:|
| IMPLEMENTED | 24 |
| COVERED | 200 |
| FEASIBLE | 61 |
| N/A | 453 |
| **Total** | **738** |

| # | Category | IMPLEMENTED | COVERED | FEASIBLE | N/A | Total |
|---:|---|---:|---:|---:|---:|---:|
| 1 | MoE expert cache and placement | 13 | 15 | 21 | 31 | 80 |
| 2 | CPU compute and threading | 0 | 4 | 0 | 8 | 12 |
| 3 | PCIe, DMA, transfer | 3 | 1 | 1 | 6 | 11 |
| 4 | Prompt processing | 1 | 6 | 4 | 15 | 26 |
| 5 | KV cache | 0 | 11 | 5 | 16 | 32 |
| 6 | Speculative decoding | 3 | 9 | 2 | 8 | 22 |
| 7 | Sampling, reasoning, response | 0 | 37 | 5 | 10 | 52 |
| 8 | Batching, slots, replicas | 1 | 6 | 5 | 5 | 17 |
| 9 | Multi-GPU | 0 | 16 | 0 | 26 | 42 |
| 10 | Quantization, kernels, hardware | 1 | 34 | 13 | 160 | 208 |
| 11 | Server, API, security, setup | 0 | 45 | 0 | 51 | 96 |
| 12 | Model management | 0 | 13 | 5 | 15 | 33 |
| 13 | Diagnostics, tests, benchmarks | 2 | 3 | 0 | 102 | 107 |

## 1. MoE expert cache and placement (VRAM / RAM / SSD tiers, pinning, adaptive cache, helper GPU caches, elastic VRAM)

| Name | Status | Our knob / how | Notes |
|---|---|---|---|
| `--adapt-async` | IMPLEMENTED | `--moe-cache-async-upload` / model `moe_cache_async_upload` | promoted and predicted experts upload on a second CUDA stream; best with `GGML_CUDA_REGISTER_HOST=1` via `env` |
| `--adapt-decay` | IMPLEMENTED | `--moe-cache-decay` / model `moe_cache_decay` | halves miss counts every N tokens; not Strata's 0.7 factor |
| `--adapt-every` | IMPLEMENTED | `--moe-cache-swap-every` + `--moe-cache-swap-max` / model `moe_cache_swap_every`, `moe_cache_swap_max` | round-based swaps choose the most missed experts of all layers together; per-call promotion (`promote_after`, `promote_max`) remains the default; ours is off by default (Strata: on) |
| `--adapt-swaps` | IMPLEMENTED | `--moe-cache-swap-max` (per round) and `--moe-cache-promote-max` (per call) / model `moe_cache_swap_max`, `moe_cache_promote_max` | 0 promote_max = never promote |
| `--adapt-tuned` | N/A | Intel-port policy variant |  |
| `--expert-cache` | COVERED | `--moe-cache-mib` (upstream) / model `moe_cache_mib` | MiB, not a multiple of the largest blob; no "auto" sizing (see --vram-reserve-mib) |
| `--expert-cache-cpu-order` | N/A | exactness experiment |  |
| `--expert-cache-device1` | COVERED | `--moe-cache-mib` is split over GPUs like `--tensor-split` (upstream #30112) / model `moe_cache_mib` + `tensor_split` | one total, no per-GPU size; this laptop has one GPU |
| `--expert-cache-device2` | COVERED | `--moe-cache-mib` is split over GPUs like `--tensor-split` (upstream #30112) / model `moe_cache_mib` + `tensor_split` | one total, no per-GPU size; this laptop has one GPU |
| `--expert-cache-device3` | COVERED | `--moe-cache-mib` is split over GPUs like `--tensor-split` (upstream #30112) / model `moe_cache_mib` + `tensor_split` | one total, no per-GPU size; this laptop has one GPU |
| `--expert-cache-per-layer` | COVERED | upstream `llama_moe_cache` keeps a slot map per layer | always on, no switch |
| `--expert-cache-remote` | N/A | Strata helper-GPU design; upstream multi-GPU cache replaces it |  |
| `--expert-cache-remote-placement` | N/A | Strata helper-GPU design; upstream multi-GPU cache replaces it |  |
| `--expert-profile` | IMPLEMENTED | `--moe-cache-stats-file` + `--moe-cache-prewarm` / model `moe_cache_stats_file`, `moe_cache_prewarm` | learned counts from earlier runs fill the cache at the first token; no shipped profile |
| `--expert-profile-save` | IMPLEMENTED | `--moe-cache-stats-file` / model `moe_cache_stats_file` | same file is read and written |
| `--expert-profile-save-every` | IMPLEMENTED | `--moe-cache-stats-file` | saved on exit and every 4000 cache calls; interval is fixed, no knob |
| `--gpu-placement` | N/A | Strata setup wrapper for helper-GPU caches |  |
| `--gpu1-experts` | N/A | Strata setup wrapper for helper-GPU caches |  |
| `--gpu2-experts` | N/A | Strata setup wrapper for helper-GPU caches |  |
| `--gpu3-experts` | N/A | Strata setup wrapper for helper-GPU caches |  |
| `--kv-grow` | FEASIBLE | L: allocate KV cells lazily so the freed VRAM goes to `--moe-cache-mib` | llama allocates the whole KV at start; short-term workaround is a smaller `ctx_size` / `ctx_size_spec` |
| `--low-ram` | COVERED | `--load-mode mmap` (default auto = mmap) / global `load_mode` | experts stay in the OS file cache; no separate pack file needed |
| `--mmap-experts` | COVERED | `--load-mode mmap` (default auto = mmap) / global `load_mode` | experts stay in the OS file cache; no separate pack file needed |
| `--no-kv-grow` | FEASIBLE | L: allocate KV cells lazily so the freed VRAM goes to `--moe-cache-mib` | llama allocates the whole KV at start; short-term workaround is a smaller `ctx_size` / `ctx_size_spec` |
| `--no-remote-expert-opt` | N/A | Strata setup wrapper for helper-GPU caches |  |
| `--reorder` | N/A | profile-file tooling |  |
| `--resident-budget-gib` | FEASIBLE | M: lock only the hottest experts (by the stats file) in RAM, leave the rest mmap-only | today `load_mode: mlock` is all-or-nothing |
| `--resident-cpu-experts` | COVERED | `--load-mode mlock` or `mmap+mlock` / global `load_mode` | locks the whole model, not just the non-GPU experts |
| `--resident-experts` | COVERED | `--load-mode mlock` or `mmap+mlock` / global `load_mode` | locks the whole model, not just the non-GPU experts |
| `--shared-expert-arena` | N/A | Linux shared-memory / THP arena |  |
| `--stream-experts` | N/A | Intel SYCL engine |  |
| `--trim-stage-weights` | N/A | Strata stage weights; llama loads only its layers |  |
| `--vram-elastic` | FEASIBLE | L: CUDA VMM-backed cache segments so `moe_cache_mib` can shrink and grow while loaded | meanwhile: restart with another `moe_cache_mib`; plan patch 10, NVIDIA only |
| `--vram-reserve-mib` | COVERED | `--fit` + `--fit-target` / global `fit`, `fit_target` | fit does not size `--moe-cache-mib` (unverified); we set it by hand |
| `--vram-segment-mib` | FEASIBLE | L: CUDA VMM-backed cache segments so `moe_cache_mib` can shrink and grow while loaded | meanwhile: restart with another `moe_cache_mib`; plan patch 10, NVIDIA only |
| `expert_cache` | COVERED | `--moe-cache-mib` / model `moe_cache_mib` | same as --expert-cache |
| `expert_profile_save` | IMPLEMENTED | `--moe-cache-stats-file` / model `moe_cache_stats_file` | saved on exit and periodically |
| `expert_profile_save_every` | IMPLEMENTED | `--moe-cache-stats-file` | saved on exit and every 4000 cache calls; interval is fixed, no knob |
| `LOW_RAM` | N/A | Docker variable |  |
| `POST /v1/vram reserve_mib` | FEASIBLE | L: CUDA VMM-backed cache segments so `moe_cache_mib` can shrink and grow while loaded | meanwhile: restart with another `moe_cache_mib`; plan patch 10, NVIDIA only |
| `remote_expert_opt` | N/A | Strata setup wrapper for helper-GPU caches |  |
| `RESIDENT_BUDGET_GIB` | N/A | Docker variable |  |
| `STRATA_ADAPT_ASYNC_BATCH` | N/A | internal tuning of Strata async swap | our async upload has no batch / wait knobs |
| `STRATA_ADAPT_ASYNC_WAIT` | N/A | internal tuning of Strata async swap | our async upload has no batch / wait knobs |
| `STRATA_ADAPT_CALLBACK_WARMUP` | N/A | internal tuning of Strata async swap |  |
| `STRATA_ADAPT_LAG` | N/A | internal tuning of Strata async swap |  |
| `STRATA_ADAPT_NOWAIT` | N/A | internal tuning of Strata async swap |  |
| `STRATA_ADAPT_OVERLAP` | IMPLEMENTED | `--moe-cache-async-upload` / model `moe_cache_async_upload` | copies overlap with compute; per-layer landed signals are not built |
| `STRATA_ADAPT_PICK_INLINE` | N/A | internal tuning of Strata async swap |  |
| `STRATA_ADAPT_SIGNAL` | N/A | internal tuning of Strata async swap |  |
| `STRATA_ARENA_MMAP` | COVERED | mmap + `PrefetchVirtualMemory` / `POSIX_MADV_WILLNEED` at load (llama-mmap.cpp) / global `load_mode` | automatic, no switch |
| `STRATA_ARENA_PIN_GIB` | FEASIBLE | M: register only the hottest CPU experts by the stats file, capped in GiB; whole-buffer `GGML_CUDA_REGISTER_HOST` is now settable through `env` | first step (env) done; benefit on Windows unverified |
| `STRATA_DISJOINT_ADAPT` | N/A | upstream per-GPU cache groups are disjoint by layer split |  |
| `STRATA_EXCHANGE_ROTATE` | FEASIBLE | M: swap buffer ownership instead of copying on promotion | small gain; plan patch 3 |
| `STRATA_FETCH_THREADS` | N/A | Strata GGUF-in-place stager; llama uses mmap page faults |  |
| `STRATA_FILE_RELEASE` | FEASIBLE | M: drop file-backed pages of uploaded experts from the working set (Windows) | only matters when the model is bigger than RAM |
| `STRATA_FILL_AHEAD` | N/A | prewarm fills the whole cache in one pass |  |
| `STRATA_FS_AHEAD` | IMPLEMENTED | `--moe-cache-foresight N` / model `moe_cache_foresight` | look-ahead part only |
| `STRATA_FS_SLOTS` | FEASIBLE | M: separate per-layer swap slots with a copy budget (admit after 1 miss in 4 windows, 32 copies per window) instead of uploading into the main cache | foresight itself is built |
| `STRATA_IO_PF_AHEAD` | FEASIBLE | M: I/O-thread read-ahead of predicted / missed experts from the SSD tier | only for models larger than RAM; needs look-ahead for the predicted part |
| `STRATA_IO_PF_STAGE` | FEASIBLE | M: I/O-thread read-ahead of predicted / missed experts from the SSD tier | only for models larger than RAM; needs look-ahead for the predicted part |
| `STRATA_IO_PF_THREADS` | FEASIBLE | M: I/O-thread read-ahead of predicted / missed experts from the SSD tier | only for models larger than RAM; needs look-ahead for the predicted part |
| `STRATA_IO_PREFETCH` | FEASIBLE | M: I/O-thread read-ahead of predicted / missed experts from the SSD tier | only for models larger than RAM; needs look-ahead for the predicted part |
| `STRATA_IO_PREFETCH_DEPTH` | FEASIBLE | M: I/O-thread read-ahead of predicted / missed experts from the SSD tier | only for models larger than RAM; needs look-ahead for the predicted part |
| `STRATA_LEND_MIRROR` | N/A | Intel SYCL engine |  |
| `STRATA_LOOKAHEAD` | IMPLEMENTED | `--moe-cache-foresight N` / model `moe_cache_foresight` | predicts the next layer's experts and uploads up to N into the main cache; no page warming for the SSD tier |
| `STRATA_LOOKAHEAD_K` | IMPLEMENTED | `--moe-cache-foresight N` / model `moe_cache_foresight` | predicts the next layer's experts and uploads up to N into the main cache; no page warming for the SSD tier |
| `STRATA_NO_ARENA_THP` | N/A | Linux shared-memory / THP arena |  |
| `STRATA_OWNED_PRICE` | N/A | Strata memory planner / Strix Halo |  |
| `STRATA_PIN_BY_NEED` | FEASIBLE | M: register only the hottest CPU experts by the stats file, capped in GiB; whole-buffer `GGML_CUDA_REGISTER_HOST` is now settable through `env` | first step (env) done; benefit on Windows unverified |
| `STRATA_PIN_GUARD` | FEASIBLE | S: free-RAM guard around pinning / locking (with the partial-pin patch) |  |
| `STRATA_PIN_RESERVE_GIB` | FEASIBLE | S: free-RAM guard around pinning / locking (with the partial-pin patch) |  |
| `STRATA_PIPELINE_ADAPT_ASYNC` | N/A | internal tuning of Strata async swap |  |
| `STRATA_READ_AHEAD` | COVERED | mmap + `PrefetchVirtualMemory` / `POSIX_MADV_WILLNEED` at load (llama-mmap.cpp) / global `load_mode` | automatic, no switch |
| `STRATA_RESIDENT_HEADROOM_GIB` | FEASIBLE | S: free-RAM guard around pinning / locking (with the partial-pin patch) |  |
| `STRATA_RESIDENT_PIN` | COVERED | `--load-mode mlock` or `mmap+mlock` / global `load_mode` | locks the whole model, not just the non-GPU experts |
| `STRATA_UMA_HEADROOM_GIB` | N/A | Strata memory planner / Strix Halo |  |
| `STRATA_UNBUFFERED_LOAD` | COVERED | `--load-mode dio` / global `load_mode` | DirectIO; Windows behaviour unverified |
| `vram_elastic` | FEASIBLE | L: CUDA VMM-backed cache segments so `moe_cache_mib` can shrink and grow while loaded | meanwhile: restart with another `moe_cache_mib`; plan patch 10, NVIDIA only |
| `vram_segment_mib` | FEASIBLE | L: CUDA VMM-backed cache segments so `moe_cache_mib` can shrink and grow while loaded | meanwhile: restart with another `moe_cache_mib`; plan patch 10, NVIDIA only |

## 2. CPU compute of experts, threading and affinity

| Name | Status | Our knob / how | Notes |
|---|---|---|---|
| `--aux-cpus` | N/A | Strata helper-thread placement |  |
| `--host-core` | COVERED | `--cpu-mask` / `--cpu-range` / `--cpu-strict` / global `cpu_mask`, `cpu_range`, `cpu_strict`; core detection buttons in the tray | pin by hand |
| `--no-host-worker` | N/A | A/B switch for Strata CPU pool |  |
| `--no-pool` | N/A | A/B switch for Strata CPU pool |  |
| `--pool-affinity` | COVERED | `--cpu-mask` / `--cpu-range` (+ `_batch`) / global `cpu_mask`, `cpu_range`; tray CPU core detection buttons (`llamacpp/cpu_topology.py`) fill P-core / all-core masks | detection is a button, not applied automatically |
| `--pool-tasks` | N/A | Strata pool task chunking; ggml threadpool |  |
| `--pool-workers` | COVERED | `--threads` / `--threads-batch` / global `threads`, `threads_batch` |  |
| `STRATA_ADAPT_JOB_CPU` | N/A | Strata helper-thread placement |  |
| `STRATA_AUX_CPUS` | N/A | Strata helper-thread placement |  |
| `STRATA_AUX_STAGER` | N/A | Strata helper-thread placement |  |
| `STRATA_HOST_CORE` | COVERED | `--cpu-mask` / `--cpu-range` / `--cpu-strict` / global `cpu_mask`, `cpu_range`, `cpu_strict`; core detection buttons in the tray | pin by hand |
| `strata_tune.aux_cpus` | N/A | Strata helper-thread placement |  |

## 3. PCIe, DMA and transfer

| Name | Status | Our knob / how | Notes |
|---|---|---|---|
| `--pcie-frac` | IMPLEMENTED | `--moe-cache-pcie-share` + `--moe-cache-pcie-max` / model `moe_cache_pcie_share`, `moe_cache_pcie_max` | a fraction of the misses (most used first) is uploaded and computed on the GPU in that call; ours is off by default (Strata: on) and Strata refines it at run time (`PcieAuto`), ours is fixed |
| `--pcie-mode` | N/A | Strata copy path; ggml uses cudaMemcpyAsync |  |
| `--spec-min-p` | COVERED | `--spec-draft-p-min` / model `draft_p_min` |  |
| `pcie_frac` | IMPLEMENTED | `--moe-cache-pcie-share` + `--moe-cache-pcie-max` / model `moe_cache_pcie_share`, `moe_cache_pcie_max` | a fraction of the misses (most used first) is uploaded and computed on the GPU in that call; ours is off by default (Strata: on) and Strata refines it at run time (`PcieAuto`), ours is fixed |
| `STRATA_DMA_BATCH` | FEASIBLE | M: batch promotion copies of one layer into one H2D transfer | plan patch 3; today copies per expert (unverified) |
| `STRATA_IO_THREADS` | N/A | Strata PLE row reader |  |
| `STRATA_PCIE_AUTO_CANON` | N/A | Strata auto-PCIe / stage-buffer tuning |  |
| `STRATA_PCIE_AUTO_SPLIT` | N/A | Strata auto-PCIe / stage-buffer tuning |  |
| `STRATA_PCIE_FRAC_DEFAULT` | N/A | Strata auto-PCIe / stage-buffer tuning |  |
| `STRATA_PCIE_MIN1` | IMPLEMENTED | `--moe-cache-pcie-max 1` with `--moe-cache-pcie-share` / model `moe_cache_pcie_max` | approximation: at most N experts per layer and call go over PCIe |
| `STRATA_STAGE_PIN` | N/A | Strata auto-PCIe / stage-buffer tuning |  |

## 4. Prompt processing (prefill chunking, fused kernels, CPU share, head alias)

| Name | Status | Our knob / how | Notes |
|---|---|---|---|
| `--no-prefill-borrow` | FEASIBLE | L: lend expert-cache slots to the prompt compute buffer, give back after prefill | small win on 12 GB; llama computes prompt in its own buffers |
| `--prefill` | COVERED | `--batch-size` / `--ubatch-size` / global `batch_size`, `ubatch_size` | set by hand, no auto chunk sizing |
| `--prefill-borrow` | FEASIBLE | L: lend expert-cache slots to the prompt compute buffer, give back after prefill | small win on 12 GB; llama computes prompt in its own buffers |
| `--prefill-head-alias` | FEASIBLE | L: reuse the output-head buffer for the prompt compute buffer | plan item 20 |
| `STRATA_ARENA_REGISTER` | COVERED | env `GGML_CUDA_REGISTER_HOST=1` through `env` (global / model) | upstream default off; effect on Windows unverified |
| `STRATA_EMB_REUSE_ACCOUNT` | N/A | Strata prompt-path tuning / AMD / Intel |  |
| `STRATA_GFX12_CHUNK` | N/A | Strata prompt-path tuning / AMD / Intel |  |
| `STRATA_GFX12_DEFAULTS` | N/A | Strata prompt-path tuning / AMD / Intel |  |
| `STRATA_METAL_PROMPT` | N/A | Apple Metal engine |  |
| `STRATA_METAL_PROMPT_CHUNK` | N/A | Apple Metal engine |  |
| `STRATA_PF_SWITCH_MIN_T` | N/A | Strata prompt-path tuning / AMD / Intel |  |
| `STRATA_PREFILL_AUTO_MAX` | COVERED | `--batch-size` / `--ubatch-size` / global `batch_size`, `ubatch_size` | set by hand, no auto chunk sizing |
| `STRATA_PREFILL_CPU_SHARE` | FEASIBLE | M: raise `moe_cache_cpu_miss_max_tokens` (hard limit 31) to chunks under 1024 tokens with a balanced CPU/GPU share and the A/B gate | skipping ids < 0 now works in CPU and CUDA `mul_mat_id` (patch 0002) but the flag stays 1..31; Strata has the prefill share ON by default |
| `STRATA_PREFILL_CPU_SHARE_MAX` | IMPLEMENTED | `--moe-cache-cpu-miss-max-tokens` (1..31) / model `moe_cache_cpu_miss_max_tokens` | decode / verify windows only; prompt chunks up to 1024 tokens are not covered (see STRATA_PREFILL_CPU_SHARE) |
| `STRATA_PREFILL_EQUAL` | N/A | Strata prompt-path tuning / AMD / Intel |  |
| `STRATA_PREFILL_FIRST` | N/A | Strata prompt-path tuning / AMD / Intel |  |
| `STRATA_PREFILL_MMQ` | N/A | Strata prompt-path tuning / AMD / Intel |  |
| `STRATA_PREFILL_RING` | N/A | Strata prompt-path tuning / AMD / Intel |  |
| `STRATA_PREFILL_STREAM_ALL` | COVERED | `--op-offload` / global `op_offload` | batch weights streamed to the GPU for big batches |
| `STRATA_PREFILL_STREAM_MIN` | COVERED | env `GGML_OP_OFFLOAD_MIN_BATCH` through `env` (global / model) | default 32 |
| `STRATA_PROMPT_DIRECT` | COVERED | `--load-mode dio` / global `load_mode` | applies to load, not to later expert reads |
| `STRATA_RING_BYTES` | N/A | Strata prompt-path tuning / AMD / Intel |  |
| `STRATA_STAGER_RING` | N/A | Strata GGUF-in-place stager; llama uses mmap page faults |  |
| `STRATA_STAGER_SLEEP` | N/A | Strata GGUF-in-place stager; llama uses mmap page faults |  |
| `STRATA_STAGER_SSD` | N/A | Strata GGUF-in-place stager; llama uses mmap page faults |  |
| `STRATA_STAGER_THREADS` | N/A | Strata GGUF-in-place stager; llama uses mmap page faults |  |

## 5. KV cache (quantization, streaming, prefetch, prompt cache, checkpoints, conversation cache, session files)

| Name | Status | Our knob / how | Notes |
|---|---|---|---|
| `--conversation-cache-mib` | COVERED | `--cache-ram` + `--cache-idle-slots` / global `cache_ram`, `cache_idle_slots` | host-RAM prompt cache in MiB; keep non-zero (CLAUDE.md) |
| `--conversation-cache-min-free-mib` | N/A | Strata cache admission / session-file details; cache_ram is size-based |  |
| `--conversation-cache-min-tokens` | N/A | Strata cache admission / session-file details; cache_ram is size-based |  |
| `--conversation-cache-slots` | N/A | Strata cache admission / session-file details; cache_ram is size-based |  |
| `--kv` | COVERED | `--cache-type-k` / `--cache-type-v` / model `cache_type_k`, `cache_type_v` | q8_0, q4_0, k8v4 = k q8_0 + v q4_0; rotation of quantized KV is automatic upstream (env `LLAMA_ATTN_ROT_DISABLE` turns it off) |
| `--kv-resident` | FEASIBLE | L: hot KV cells in VRAM, cold ones in RAM, prefetch next layer on a side stream | only `--no-kv-offload` (all KV in RAM) exists; plan patch 5 |
| `--kv-streaming` | COVERED | `--no-kv-offload` / global `kv_offload` | all-or-nothing, no resident window |
| `--memory-limit-mib` | N/A | container limit / troubleshooting |  |
| `--prompt-cache` | COVERED | `--cache-prompt` / global `cache_prompt` |  |
| `--prompt-cache-every` | COVERED | `--checkpoint-min-step` + `--ctx-checkpoints` / global `checkpoint_min_step`, `ctx_checkpoints` |  |
| `--prompt-cache-root` | COVERED | `--cache-reuse` / model `cache_reuse` | KV-shift reuse of a shared prefix, not a root checkpoint |
| `--prompt-cache-tail` | FEASIBLE | M: checkpoint at the last message boundary (the proxy knows it) so a conversation resumes without re-prefill | llama checkpoints on a token step, not at turn ends |
| `--session-min-free-mib` | N/A | Strata cache admission / session-file details; cache_ram is size-based |  |
| `--session-save-reclaim` | N/A | Strata cache admission / session-file details; cache_ram is size-based |  |
| `--slot-save-path` | COVERED | `--slot-save-path` / global `slot_save_path` |  |
| `--tail-role-token` | N/A | Strata conversation-checkpoint internals / Docker variable |  |
| `--turn-token` | N/A | Strata conversation-checkpoint internals / Docker variable |  |
| `filename` | COVERED | upstream `POST /slots/:id?action=save\|restore` (needs `slot_save_path`) |  |
| `KV` | N/A | Strata conversation-checkpoint internals / Docker variable |  |
| `KV_STREAMING` | N/A | Strata conversation-checkpoint internals / Docker variable |  |
| `pin=N` | N/A | Strata conversation-checkpoint internals / Docker variable |  |
| `POST /slots/0?action=save or restore` | COVERED | upstream `POST /slots/:id?action=save\|restore` (needs `slot_save_path`) |  |
| `slot_save_path` | COVERED | `--slot-save-path` / global `slot_save_path` |  |
| `STRATA_CACHE_MESSAGE_BOUNDARY` | FEASIBLE | M: checkpoint at the last message boundary (the proxy knows it) so a conversation resumes without re-prefill | llama checkpoints on a token step, not at turn ends |
| `strata_checkpoint` | N/A | Strata conversation-checkpoint internals / Docker variable |  |
| `STRATA_KV_GROW` | FEASIBLE | L: allocate KV cells lazily so the freed VRAM goes to `--moe-cache-mib` | llama allocates the whole KV at start; short-term workaround is a smaller `ctx_size` / `ctx_size_spec` |
| `STRATA_KV_HOST_DMA` | N/A | container limit / troubleshooting |  |
| `STRATA_KV_PREFETCH` | FEASIBLE | L: hot KV cells in VRAM, cold ones in RAM, prefetch next layer on a side stream | only `--no-kv-offload` (all KV in RAM) exists; plan patch 5 |
| `STRATA_KV_ROT` | COVERED | upstream attn-rotation, on by default for quantized KV | switch off with env `LLAMA_ATTN_ROT_DISABLE` through `env` |
| `STRATA_MEMORY_LIMIT_MIB` | N/A | container limit / troubleshooting |  |
| `strata_prefix` | N/A | Strata conversation-checkpoint internals / Docker variable |  |
| `STRATA_SESSION_BUFFERED` | N/A | Strata cache admission / session-file details; cache_ram is size-based |  |

## 6. Speculative decoding (MTP, prompt lookup, draft policy, verify window, tail skip)

| Name | Status | Our knob / how | Notes |
|---|---|---|---|
| `--coupled-draft` | COVERED | `--spec-draft-sampling {greedy,probabilistic}` / global `spec_draft_sampling` | knob now exists |
| `--draft-vocab` | FEASIBLE | L: restrict the MTP head's output projection to a token subset | model-specific; speeds the draft step only |
| `--lookup-chain` | COVERED | `--spec-type draft-mtp,ngram-mod` (comma list) / global `spec_type` | chaining semantics of the combined list unverified |
| `--lookup-chain-min` | COVERED | `--spec-ngram-mod-n-min` / global `spec_ngram_mod_n_min` |  |
| `--mtp` | COVERED | `--spec-type draft-mtp` / global `spec_type` |  |
| `--mtp-draft-vocab` | FEASIBLE | L: restrict the MTP head's output projection to a token subset | model-specific; speeds the draft step only |
| `--mtp-hnorm` | N/A | Strata MTP graph variant |  |
| `--mtp-max-t` | COVERED | `--spec-draft-n-max` / model `draft_n`; `--spec-draft-adaptive` chooses the length by measured tokens/ms |  |
| `--mtp-q4` | N/A | Strata MTP graph variant |  |
| `--no-coupled-draft` | COVERED | `--spec-draft-sampling greedy` / global `spec_draft_sampling` |  |
| `--no-spec-split` | N/A | Strata MTP graph variant |  |
| `--spec` | COVERED | `--spec-draft-n-max` / model `draft_n`, plus adaptive length `--spec-draft-adaptive` (+ `-min-gain`, `-ema`, `-probe`) / global `spec_draft_adaptive`, `spec_draft_adaptive_min_gain`, `spec_draft_adaptive_ema`, `spec_draft_adaptive_probe` | adaptive draft length is Strata's controller idea; patch 0003 |
| `--spec-split` | N/A | Strata MTP graph variant |  |
| `STRATA_ROUTE_PRIOR` | IMPLEMENTED | `--moe-cache-route-bias` (+ `--moe-cache-route-top`, `--moe-cache-route-scale`) / model `moe_cache_route_bias`, `moe_cache_route_top`, `moe_cache_route_scale` | adds F to the router score of cached experts; changes the output; off by default (Strata: opt-in too) |
| `STRATA_ROUTE_RESIDENT` | IMPLEMENTED | `--moe-cache-route-bias` (+ `--moe-cache-route-top`, `--moe-cache-route-scale`) / model `moe_cache_route_bias`, `moe_cache_route_top`, `moe_cache_route_scale` | adds F to the router score of cached experts; changes the output; off by default (Strata: opt-in too) |
| `STRATA_ROUTE_RESIDENT_MTP` | N/A | Strata MTP graph variant |  |
| `STRATA_SPEC_COUPLED` | COVERED | `--spec-draft-sampling {greedy,probabilistic}` / global `spec_draft_sampling` | knob now exists |
| `STRATA_SPEC_DEPTH` | N/A | diagnostic |  |
| `STRATA_SPEC_GUMBEL` | N/A | Strata sampled-draft internals |  |
| `STRATA_SPEC_MIN_TEMP` | N/A | Strata sampled-draft internals |  |
| `STRATA_SPEC_PROB` | COVERED | `--spec-draft-sampling {greedy,probabilistic}` / global `spec_draft_sampling` | knob now exists |
| `STRATA_TAIL_SKIP_MAX_CACHED` | IMPLEMENTED | `--moe-cache-tail-skip-auto-off` / model `moe_cache_tail_skip_auto_off` | no skipping when >= PCT % of the batch's experts are cached (default 90, same as Strata) |

## 7. Sampling, reasoning and response behaviour

| Name | Status | Our knob / how | Notes |
|---|---|---|---|
| `--eos-ids` | N/A | Strata engine token handling; llama stops on EOG |  |
| `--fit-max-tokens` | FEASIBLE | S: proxy clamps max_tokens to the remaining context instead of answering 400 |  |
| `--greedy` | COVERED | inference_defaults `temperature: 0` / model `inference_defaults.temperature` |  |
| `--reasoning-effort` | COVERED | proxy `inference_defaults.reasoning_effort` map (+ `thinking_budget`) | llama `--reasoning-effort` / `-rea` server default has no knob; the proxy map covers requests |
| `--seed` | COVERED | `--seed` / global `seed`; request `seed` passes through |  |
| `--stop-eos` | N/A | Strata engine token handling; llama stops on EOG |  |
| `--temperature` | COVERED | inference_defaults `temperature` (proxy-applied) |  |
| `--top-k` | COVERED | inference_defaults `top_k` |  |
| `--top-p` | COVERED | inference_defaults `top_p` |  |
| `/think` | COVERED | proxy Anthropic translation + per-model `inference_defaults.thinking` (template key) and `reasoning` |  |
| `anthropic_thinking` | COVERED | proxy Anthropic translation + per-model `inference_defaults.thinking` (template key) and `reasoning` |  |
| `chat_template_kwargs` | COVERED | inference_defaults `chat_template_kwargs` | llama `--chat-template-kwargs` server default has no knob; proxy merges per request |
| `client_metadata` | N/A | Strata client-quirk workaround / stateful API |  |
| `codex_compaction_cache` | N/A | Strata client-quirk workaround / stateful API |  |
| `effort_position` | N/A | Strata client-quirk workaround / stateful API |  |
| `env_to_user` | N/A | Strata client-quirk workaround / stateful API |  |
| `fit_max_tokens` | FEASIBLE | S: proxy clamps max_tokens to the remaining context instead of answering 400 |  |
| `frequency_penalty` | COVERED | inference_defaults `frequency_penalty` |  |
| `include` | N/A | Strata client-quirk workaround / stateful API |  |
| `instructions` | COVERED | proxy pipeline (Anthropic / OpenAI / Gemini adapters, `/v1/responses` passthrough); llama handles them natively |  |
| `logit_bias` | COVERED | llama request field `logit_bias`; `--logit-bias` flag | proxy passthrough unverified; GAP: llama flag exists, NO telecode knob for the flag |
| `logprobs / top_logprobs` | COVERED | llama request fields `logprobs` / `n_probs` | proxy passthrough unverified |
| `max_output_tokens` | COVERED | inference_defaults `max_tokens`; `/v1/responses` passthrough |  |
| `max_tokens` | COVERED | inference_defaults `max_tokens`; `/v1/responses` passthrough |  |
| `min_p` | COVERED | inference_defaults `min_p` |  |
| `output_config.effort` | COVERED | proxy `inference_defaults.reasoning_effort` map (+ `thinking_budget`) | llama `--reasoning-effort` / `-rea` server default has no knob; the proxy map covers requests |
| `parallel_tool_calls` | COVERED | proxy pipeline (Anthropic / OpenAI / Gemini adapters, `/v1/responses` passthrough); llama handles them natively |  |
| `penalty_last_n` | COVERED | llama `--repeat-last-n` / global `repeat_last_n`; request field `repeat_last_n` |  |
| `presence_penalty` | COVERED | inference_defaults `presence_penalty` |  |
| `previous_response_id / conversation / background` | N/A | Strata client-quirk workaround / stateful API |  |
| `reasoning_budget_tokens` | COVERED | `--reasoning-budget` / model `reasoning_budget`; proxy `thinking_budget` + `reasoning_effort_map` |  |
| `reasoning_close_retry` | FEASIBLE | M: proxy detects a reply ended inside <think> (or repeating passages) and re-asks with a forced close |  |
| `reasoning_effort` | COVERED | proxy `inference_defaults.reasoning_effort` map (+ `thinking_budget`) | llama `--reasoning-effort` / `-rea` server default has no knob; the proxy map covers requests |
| `reasoning_loop_recovery` | FEASIBLE | M: proxy detects a reply ended inside <think> (or repeating passages) and re-asks with a forced close |  |
| `repeat_stop_tokens` | FEASIBLE | S: proxy stream watcher ends a reply after N identical tokens (llama DRY sampler is the other lever) |  |
| `repetition_penalty` | COVERED | inference_defaults `repeat_penalty` |  |
| `response_format` | COVERED | proxy `structured_output` + llama `response_format` / json_schema grammar |  |
| `return_progress` | COVERED | llama request field `return_progress` | proxy passthrough unverified |
| `sampling` | COVERED | inference_defaults block (global and per model) |  |
| `seed` | COVERED | `--seed` / global `seed`; request `seed` passes through |  |
| `stop / stop_sequences` | COVERED | inference_defaults `stop` |  |
| `STRATA_KEEP_EMPTY_TURNS` | N/A | Strata client-quirk workaround / stateful API |  |
| `STRATA_TOPK_STREAM` | N/A | Strata client-quirk workaround / stateful API |  |
| `stream` | COVERED | proxy pipeline (Anthropic / OpenAI / Gemini adapters, `/v1/responses` passthrough); llama handles them natively |  |
| `system / messages / tools` | COVERED | proxy pipeline (Anthropic / OpenAI / Gemini adapters, `/v1/responses` passthrough); llama handles them natively |  |
| `temperature` | COVERED | inference_defaults `temperature` (proxy-applied) |  |
| `text.format` | COVERED | proxy pipeline (Anthropic / OpenAI / Gemini adapters, `/v1/responses` passthrough); llama handles them natively |  |
| `thinking` | COVERED | proxy Anthropic translation + per-model `inference_defaults.thinking` (template key) and `reasoning` |  |
| `tool_call_recovery` | COVERED | llama chat auto-parser (`common/chat-auto-parser`) + proxy tool translation | no extra "recovery" pass; unverified |
| `tool_choice` | COVERED | proxy pipeline (Anthropic / OpenAI / Gemini adapters, `/v1/responses` passthrough); llama handles them natively |  |
| `top_k` | COVERED | inference_defaults `top_k` |  |
| `top_p` | COVERED | inference_defaults `top_p` |  |

## 8. Batching, parallel slots and replicas

| Name | Status | Our knob / how | Notes |
|---|---|---|---|
| `--adapt-min-gain` | IMPLEMENTED | `--moe-cache-promote-gain` / model `moe_cache_promote_gain` | RATIO (used F times more than the victim); Strata's margin is ADDITIVE (cand >= victim + 1.5, candidate floor 2.0); `--moe-cache-evict-window` / `moe_cache_evict_window` picks the least used of the N least recent slots |
| `--batch` | COVERED | `--parallel` + `--cont-batching` / global `parallel`, `cont_batching`; `--ctx-size` / model `ctx_size` | setup-only / Docker variants are the same knobs |
| `--batch-mtp` | N/A | Strata batch-slot scheduler; llama-server batches itself |  |
| `--context` | COVERED | `--parallel` + `--cont-batching` / global `parallel`, `cont_batching`; `--ctx-size` / model `ctx_size` | setup-only / Docker variants are the same knobs |
| `--parallel` | COVERED | `--parallel` + `--cont-batching` / global `parallel`, `cont_batching`; `--ctx-size` / model `ctx_size` | setup-only / Docker variants are the same knobs |
| `--replicas` | FEASIBLE | L: LlamaSupervisor runs one active server; N replicas need a router in the proxy | no gain on one 12 GB GPU |
| `--slots` | COVERED | `--parallel` + `--cont-batching` / global `parallel`, `cont_batching`; `--ctx-size` / model `ctx_size` | setup-only / Docker variants are the same knobs |
| `CONTEXT` | COVERED | `--parallel` + `--cont-batching` / global `parallel`, `cont_batching`; `--ctx-size` / model `ctx_size` | setup-only / Docker variants are the same knobs |
| `parallel` | COVERED | `--parallel` + `--cont-batching` / global `parallel`, `cont_batching`; `--ctx-size` / model `ctx_size` | setup-only / Docker variants are the same knobs |
| `replica_cpus` | FEASIBLE | L: LlamaSupervisor runs one active server; N replicas need a router in the proxy | no gain on one 12 GB GPU |
| `replica_load_tokens` | FEASIBLE | L: LlamaSupervisor runs one active server; N replicas need a router in the proxy | no gain on one 12 GB GPU |
| `replicas` | FEASIBLE | L: LlamaSupervisor runs one active server; N replicas need a router in the proxy | no gain on one 12 GB GPU |
| `STRATA_BATCH_DECODE_SHARE` | N/A | Strata batch-slot scheduler; llama-server batches itself |  |
| `STRATA_BATCH_MTP` | N/A | Strata batch-slot scheduler; llama-server batches itself |  |
| `STRATA_BATCH_MTP_SERIAL` | N/A | Strata batch-slot scheduler; llama-server batches itself |  |
| `STRATA_PARALLEL_SOLO` | N/A | Strata batch-slot scheduler; llama-server batches itself |  |
| `STRATA_REPLICA_START` | FEASIBLE | L: LlamaSupervisor runs one active server; N replicas need a router in the proxy | no gain on one 12 GB GPU |

## 9. Multi-GPU (layer split, helper and peer GPUs, pipeline)

| Name | Status | Our knob / how | Notes |
|---|---|---|---|
| `--batch-groups` | N/A | Strata multi-GPU pipeline / engine variant |  |
| `--cuda` | N/A | Strata multi-GPU pipeline / engine variant |  |
| `--gpu` | COVERED | `--device` / `--main-gpu` / global `main_gpu`, model `device` | CUDA_VISIBLE_DEVICES is an env of the launching shell |
| `--gpus` | COVERED | `--device` / `--main-gpu` / global `main_gpu`, model `device` | CUDA_VISIBLE_DEVICES is an env of the launching shell |
| `--gpus all` | COVERED | `--device` / `--main-gpu` / global `main_gpu`, model `device` | CUDA_VISIBLE_DEVICES is an env of the launching shell |
| `--layer-split` | COVERED | `--tensor-split` + `--split-mode layer` / global `tensor_split`, `split_mode` |  |
| `--layer-split auto` | COVERED | `--fit` / global `fit`, `fit_target` |  |
| `--peer-adapt-swaps` | N/A | Strata multi-GPU pipeline / engine variant |  |
| `--peer-device` | COVERED | multi-GPU `--moe-cache-mib` over `--tensor-split` (upstream) | no separate peer tier |
| `--peer-prefill-rows` | N/A | Strata multi-GPU pipeline / engine variant |  |
| `--peer-reserve-mib` | COVERED | `--fit-target MiB0,MiB1,...` / global `fit_target` |  |
| `--peer-slots` | N/A | Strata multi-GPU pipeline / engine variant |  |
| `--pipeline-windows` | N/A | Strata multi-GPU pipeline / engine variant |  |
| `--remote-expert-opt` | N/A | Strata multi-GPU pipeline / engine variant |  |
| `--short-read` | N/A | Strata multi-GPU pipeline / engine variant |  |
| `--split-device` | COVERED | `--device` / `--main-gpu` / global `main_gpu`, model `device` | CUDA_VISIBLE_DEVICES is an env of the launching shell |
| `--split-skip-if-fits` | COVERED | `--fit` / global `fit`, `fit_target` |  |
| `--suffix-draft` | COVERED | `--spec-type ngram-mod` / `ngram-map-k` / `ngram-simple` / global `spec_type`, `spec_ngram_*` |  |
| `--vram-reserve-later-mib` | COVERED | `--fit-target MiB0,MiB1,...` / global `fit_target` |  |
| `CUDA_VISIBLE_DEVICES` | COVERED | `--device` / `--main-gpu` / global `main_gpu`, model `device` | CUDA_VISIBLE_DEVICES is an env of the launching shell |
| `gpu` | COVERED | `--device` / `--main-gpu` / global `main_gpu`, model `device` | CUDA_VISIBLE_DEVICES is an env of the launching shell |
| `GPU` | N/A | Docker variable |  |
| `gpu_order` | COVERED | `--fit` / global `fit`, `fit_target` |  |
| `GPUS` | N/A | Docker variable |  |
| `HIP_VISIBLE_DEVICES` | N/A | AMD ROCm selector |  |
| `layer_split` | COVERED | `--tensor-split` + `--split-mode layer` / global `tensor_split`, `split_mode` |  |
| `LAYER_SPLIT` | N/A | Docker variable |  |
| `ONEAPI_DEVICE_SELECTOR` | N/A | Intel selector |  |
| `ROCR_VISIBLE_DEVICES` | N/A | AMD ROCm selector |  |
| `split_skip_if_fits` | COVERED | `--fit` / global `fit`, `fit_target` |  |
| `STRATA_MTP_BATCH_RING` | N/A | Strata multi-GPU split tuning |  |
| `STRATA_MTP_STAGE0` | N/A | Strata multi-GPU split tuning |  |
| `STRATA_PIPELINE_AUTO` | N/A | Strata multi-GPU pipeline / engine variant |  |
| `STRATA_PREFILL_HELP` | N/A | Strata multi-GPU split tuning |  |
| `STRATA_PREFILL_HELP_FRAC` | N/A | Strata multi-GPU split tuning |  |
| `STRATA_SPLIT_CHUNK16K` | N/A | Strata multi-GPU split tuning |  |
| `STRATA_SPLIT_HEADROOM` | N/A | Strata multi-GPU split tuning |  |
| `STRATA_SPLIT_HEADROOM_TOL` | N/A | Strata multi-GPU split tuning |  |
| `STRATA_SPLIT_MTP_BATCH` | N/A | Strata multi-GPU split tuning |  |
| `STRATA_SPLIT_OWN` | N/A | Strata multi-GPU split tuning |  |
| `STRATA_SPLIT_RING` | N/A | Strata multi-GPU split tuning |  |
| `STRATA_STAGE_TRIM` | N/A | Strata multi-GPU split tuning |  |

## 10. Quantization, kernels, models and hardware-specific switches

| Name | Status | Our knob / how | Notes |
|---|---|---|---|
| `--backend` | COVERED | Patched Build card builds with CUDA (`custom_build`, `GGML_CUDA=ON`) | llama.cpp selects backends at build time |
| `--build` | COVERED | tray Patched Build (`llamacpp/patcher.py`, `custom_build.source_dir`) |  |
| `--calibrate` | FEASIBLE | M: search `moe_cache_*` / threads automatically; building blocks exist: `tools/moe_cache_bench.py` (greedy bench, tok/s, output hash, peak VRAM) and the GGUF inspector's suggested cache size | no automatic grid search or per-PC store |
| `--compat-bf16` | N/A | Strata build / pack option |  |
| `--control-vector` | COVERED | `--control-vector`, `--control-vector-scaled`, `--control-vector-layer-range` / model `control_vector`, `control_vector_scaled`, `control_vector_layer_range` |  |
| `--control-vector-layer-range` | COVERED | `--control-vector`, `--control-vector-scaled`, `--control-vector-layer-range` / model `control_vector`, `control_vector_scaled`, `control_vector_layer_range` |  |
| `--control-vector-scaled` | COVERED | `--control-vector`, `--control-vector-scaled`, `--control-vector-layer-range` / model `control_vector`, `control_vector_scaled`, `control_vector_layer_range` |  |
| `--cvec-dir` | N/A | Strata control-vector extras (llama has plain control vectors) |  |
| `--cvec-mode` | N/A | Strata control-vector extras (llama has plain control vectors) |  |
| `--diffusion-fa` | N/A | Strata image generation |  |
| `--embd-gguf` | N/A | Strata own model pack / installer |  |
| `--experimental-speed-projection` | N/A | Strata control-vector extras (llama has plain control vectors) |  |
| `--experts-bin` | N/A | Strata own model pack / installer |  |
| `--flash-attn` | N/A | Strata vision-encoder option; we use --mmproj |  |
| `--image-backend` | N/A | Strata image generation |  |
| `--image-gpu-policy` | N/A | Strata image generation |  |
| `--image-native` | N/A | Strata image generation |  |
| `--image-quant` | N/A | Strata image generation |  |
| `--images` | N/A | Strata image generation |  |
| `--max-context` | N/A | Strata session-fingerprint field |  |
| `--min-tokens` | COVERED | `--image-min-tokens` / model `image_min_tokens` |  |
| `--mtp-window` | N/A | Strata session-fingerprint field |  |
| `--n-gpu-layers` | COVERED | `--n-gpu-layers` / model `n_gpu_layers` |  |
| `--native` | N/A | Strata own model pack / installer |  |
| `--native-dense-gguf` | N/A | Strata own model pack / installer |  |
| `--native-head-gguf` | N/A | Strata own model pack / installer |  |
| `--no-ple` | N/A | Strata own model pack / installer |  |
| `--override-tensor` | COVERED | `--override-tensor` / model `override_tensor` |  |
| `--pack` | N/A | Strata own model pack / installer |  |
| `--ple-gguf` | COVERED | `--lazy-mode` / global `lazy_mode` | per-layer embeddings read on demand from the mmap; no I/O queue knobs |
| `--ple-inflight` | COVERED | `--lazy-mode` / global `lazy_mode` | per-layer embeddings read on demand from the mmap; no I/O queue knobs |
| `--ple-io` | COVERED | `--lazy-mode` / global `lazy_mode` | per-layer embeddings read on demand from the mmap; no I/O queue knobs |
| `--ple-row-cache` | N/A | Strata PLE row cache |  |
| `--prebuilt` | N/A | Strata own model pack / installer |  |
| `--rope-freq-base` | COVERED | `--rope-freq-base` / model `rope_freq_base` |  |
| `--rope-freq-scale` | COVERED | `--rope-freq-scale` / model `rope_freq_scale` |  |
| `--rope-scale` | COVERED | `--rope-scale` / model `rope_scale` | argv drops it when `rope_freq_scale` is set |
| `--rope-scaling` | COVERED | `--rope-scaling` / model `rope_scaling` |  |
| `--vae-tiling` | N/A | Strata image generation |  |
| `--vision` | COVERED | `--mmproj` / model `mmproj`, `mmproj_offload` |  |
| `--vision-tokens` | COVERED | `--image-max-tokens` / model `image_max_tokens` |  |
| `--yarn-attn-factor` | COVERED | `--yarn-attn-factor` / model `yarn_attn_factor` |  |
| `--yarn-beta-fast` | COVERED | `--yarn-beta-fast` / model `yarn_beta_fast` |  |
| `--yarn-beta-slow` | COVERED | `--yarn-beta-slow` / model `yarn_beta_slow` |  |
| `--yarn-ext-factor` | COVERED | `--yarn-ext-factor` / model `yarn_ext_factor` |  |
| `--yarn-orig-ctx` | COVERED | `--yarn-orig-ctx` / model `yarn_orig_ctx` |  |
| `/image` | N/A | Strata image generation |  |
| `BUILD_VISION` | N/A | Strata vision-encoder option; we use --mmproj |  |
| `CC / CXX / CUDAHOSTCXX` | COVERED | Patched Build picks the compiler itself (`llamacpp/patcher.py`) |  |
| `CUDA_ARCHITECTURES` | N/A | patcher builds `CMAKE_CUDA_ARCHITECTURES=native` |  |
| `experimental_speed_projection` | N/A | Strata control-vector extras (llama has plain control vectors) |  |
| `GPU_PINNED_MIN_XFER_SIZE` | N/A | AMD / HIP-specific |  |
| `HF_ENDPOINT` | N/A | Hugging Face mirror for Strata downloads |  |
| `HSA_OVERRIDE_GFX_VERSION` | N/A | AMD / HIP-specific |  |
| `HSA_USERPTR_FOR_PAGED_MEM` | N/A | AMD / HIP-specific |  |
| `IGC_EnableDPEmulation` | N/A | Intel / SYCL-specific |  |
| `STRATA_ARENA_ALIAS_CHECK` | N/A | Intel / SYCL-specific |  |
| `STRATA_ARGMAX_MULTI` | FEASIBLE | L: thread-block-cluster argmax / block-select kernels for sm_90+ | plan item 21; small decode gain |
| `STRATA_ATTN_LANECELL` | N/A | AMD / HIP-specific |  |
| `STRATA_ATTN_PRE75` | N/A | Strata kernel variant; ggml chooses its own |  |
| `STRATA_BF16_TC` | N/A | Strata kernel variant; ggml chooses its own |  |
| `STRATA_BUILD_TESTS` | N/A | Strata build / pack option |  |
| `STRATA_COPY_ENGINE` | N/A | Intel / SYCL-specific |  |
| `STRATA_CUDA` | N/A | Strata multi-GPU pipeline / engine variant |  |
| `STRATA_CVEC_FUSE` | N/A | Strata control-vector extras (llama has plain control vectors) |  |
| `STRATA_DENSE_F32` | N/A | Strata kernel variant; ggml chooses its own |  |
| `STRATA_DENSE_MMQ` | N/A | AMD / HIP-specific |  |
| `STRATA_DEQUANT_WIDE` | N/A | Strata engine internal / kernel variant |  |
| `STRATA_DOWN_LANES` | N/A | Intel / SYCL-specific |  |
| `STRATA_ENABLE_CUDA` | COVERED | Patched Build card builds with CUDA (`custom_build`, `GGML_CUDA=ON`) | llama.cpp selects backends at build time |
| `STRATA_ENABLE_HIP` | N/A | AMD / HIP-specific |  |
| `STRATA_ENABLE_SYCL` | N/A | Intel / SYCL-specific |  |
| `STRATA_EXP_MODE` | N/A | AMD / HIP-specific |  |
| `STRATA_EXPERIMENTAL_GFX1103` | N/A | AMD / HIP-specific |  |
| `STRATA_EXPERIMENTAL_SM60` | N/A | Strata kernel variant; ggml chooses its own |  |
| `STRATA_EXPERT_LANES` | N/A | Intel / SYCL-specific |  |
| `STRATA_EXPERT_SPLIT` | N/A | Intel / SYCL-specific |  |
| `STRATA_EXPERT_V2` | N/A | AMD / HIP-specific |  |
| `STRATA_EXPERT_V2K` | N/A | Strata engine internal / kernel variant |  |
| `STRATA_EXPERT_XMX` | N/A | Intel / SYCL-specific |  |
| `STRATA_FILL_SERIAL` | N/A | Intel / SYCL-specific |  |
| `STRATA_FORCE_ISA` | N/A | CPU ISA selection; ggml picks its CPU variant |  |
| `STRATA_GDN_CHUNKED` | N/A | Strata kernel variant; ggml chooses its own |  |
| `STRATA_GDN_CONVL2` | N/A | AMD / HIP-specific |  |
| `STRATA_GDN_HEAD` | N/A | AMD / HIP-specific |  |
| `STRATA_GDN_KEYHEAD` | N/A | Intel / SYCL-specific |  |
| `STRATA_GDN_NOY` | N/A | AMD / HIP-specific |  |
| `STRATA_GDN_PP` | N/A | AMD / HIP-specific |  |
| `STRATA_GDN_SPLIT` | N/A | AMD / HIP-specific |  |
| `STRATA_GDN_XE` | N/A | Intel / SYCL-specific |  |
| `STRATA_GFX1012_PORTABLE_DOT` | N/A | AMD / HIP-specific |  |
| `STRATA_GFX1151_DEFAULTS` | N/A | AMD / HIP-specific |  |
| `STRATA_GFX906_ATTN_QUERY_SWIZZLE` | N/A | AMD / HIP-specific |  |
| `STRATA_GFX906_ATTN_REDUCE12` | N/A | AMD / HIP-specific |  |
| `STRATA_GGML_DIR` | COVERED | tray Patched Build (`llamacpp/patcher.py`, `custom_build.source_dir`) |  |
| `STRATA_GPU_METAL` | N/A | Apple Metal-specific |  |
| `STRATA_GR_DOWN_DIRECT` | N/A | Intel / SYCL-specific |  |
| `STRATA_GR_DOWN_MAX4` | N/A | Intel / SYCL-specific |  |
| `STRATA_GR_DOWN_SLICED` | N/A | Strata engine internal / kernel variant |  |
| `STRATA_GR_FAST` | N/A | Strata kernel variant; ggml chooses its own |  |
| `STRATA_GR_SPLIT` | N/A | Strata kernel variant; ggml chooses its own |  |
| `STRATA_GR_V3` | N/A | Intel / SYCL-specific |  |
| `STRATA_GU_LANES` | N/A | Intel / SYCL-specific |  |
| `STRATA_HC_CHECK` | N/A | Intel / SYCL-specific |  |
| `STRATA_HC_Q8` | N/A | Strata kernel variant; ggml chooses its own |  |
| `STRATA_HC_UPMIX` | N/A | AMD / HIP-specific |  |
| `STRATA_HCD_EXACT` | N/A | AMD / HIP-specific |  |
| `STRATA_HIP_ADAPT_KERNEL_COPY` | N/A | AMD / HIP-specific |  |
| `STRATA_HIP_ARCHS` | N/A | AMD / HIP-specific |  |
| `STRATA_HIP_GFX906` | N/A | AMD / HIP-specific |  |
| `STRATA_HIP_PROMPT_F16` | N/A | AMD / HIP-specific |  |
| `STRATA_HIP_ROUTER_OLD` | N/A | AMD / HIP-specific |  |
| `STRATA_HIP_WMMA` | N/A | AMD / HIP-specific |  |
| `STRATA_HIPBLASLT_TUNING` | N/A | AMD / HIP-specific |  |
| `STRATA_IQ3S_MT1` | N/A | Strata CPU kernel variant |  |
| `STRATA_IQ_MT_MIN` | N/A | Strata CPU kernel variant |  |
| `STRATA_ISA_FLOOR` | N/A | CPU ISA selection; ggml picks its CPU variant |  |
| `STRATA_KQ256` | N/A | Strata CPU kernel variant |  |
| `STRATA_LFUSE` | N/A | Strata engine internal / kernel variant |  |
| `STRATA_METAL_ARENA_GB` | N/A | Apple Metal-specific |  |
| `STRATA_METAL_CONTIG` | N/A | Apple Metal-specific |  |
| `STRATA_METAL_FUSE` | N/A | Apple Metal-specific |  |
| `STRATA_METAL_IGNORE_REPACK` | N/A | Apple Metal-specific |  |
| `STRATA_METAL_KEEP_FREE_GB` | N/A | Apple Metal-specific |  |
| `STRATA_METAL_KEEP_HOST_WEIGHTS` | N/A | Apple Metal-specific |  |
| `STRATA_METAL_LAZY_FOLD` | N/A | Apple Metal-specific |  |
| `STRATA_METAL_LEAVE_SHARE` | N/A | Apple Metal-specific |  |
| `STRATA_METAL_MTP_CHAIN` | N/A | Apple Metal-specific |  |
| `STRATA_METAL_MTP_Q8` | N/A | Apple Metal-specific |  |
| `STRATA_METAL_PARTIAL` | N/A | Apple Metal-specific |  |
| `STRATA_METAL_ROUTE` | N/A | Apple Metal-specific |  |
| `STRATA_METAL_STREAM` | N/A | Apple Metal-specific |  |
| `STRATA_METAL_WIRE_ARENA` | N/A | Apple Metal-specific |  |
| `STRATA_MIRROR_MIB` | N/A | Intel / SYCL-specific |  |
| `STRATA_MMQ_KQ` | N/A | Strata build / kernel option; ggml has MMQ natively |  |
| `STRATA_MMQ_KQUANTS` | N/A | Strata build / kernel option; ggml has MMQ natively |  |
| `STRATA_MMVF_ROWS` | N/A | Strata kernel variant; ggml chooses its own |  |
| `STRATA_MMVQ_A2` | N/A | Strata engine internal / kernel variant |  |
| `STRATA_MMVQ_IL` | FEASIBLE | L: interleaved small-batch matvec kernel with a per-card row table | plan item 18; helps 2-4 token verify windows |
| `STRATA_MMVQ_IL_ROWS` | FEASIBLE | L: interleaved small-batch matvec kernel with a per-card row table | plan item 18; helps 2-4 token verify windows |
| `STRATA_MMVQ_SG` | N/A | Strata engine internal / kernel variant |  |
| `STRATA_MMVQ_WAVE` | N/A | Strata engine internal / kernel variant |  |
| `STRATA_MMVQ_WIDE_32` | N/A | Strata engine internal / kernel variant |  |
| `STRATA_MMVQ_WIDE_K` | N/A | Strata engine internal / kernel variant |  |
| `STRATA_MTP_SIDE_STREAM` | N/A | AMD / HIP-specific |  |
| `STRATA_NATIVE_EXPERTS` | N/A | Strata build / kernel option; ggml has MMQ natively |  |
| `STRATA_NO_AVXVNNI` | N/A | Strata CPU kernel variant |  |
| `STRATA_NO_NVFP4_512` | N/A | NVFP4 is a ggml quant type; kernels chosen by ggml |  |
| `STRATA_NVCC` | COVERED | tray Patched Build (`llamacpp/patcher.py`, `custom_build.source_dir`) |  |
| `STRATA_ORCA_Q4KS_MMQ` | N/A | Strata build / kernel option; ggml has MMQ natively |  |
| `STRATA_PA_FAST` | N/A | AMD / HIP-specific |  |
| `STRATA_PARTIAL_PIN` | FEASIBLE | M: register only the hottest CPU experts by the stats file, capped in GiB; whole-buffer `GGML_CUDA_REGISTER_HOST` is now settable through `env` | first step (env) done; benefit on Windows unverified |
| `STRATA_PARTIAL_PIN_GIB` | FEASIBLE | M: register only the hottest CPU experts by the stats file, capped in GiB; whole-buffer `GGML_CUDA_REGISTER_HOST` is now settable through `env` | first step (env) done; benefit on Windows unverified |
| `STRATA_PF_FUSED` | FEASIBLE | L: fused int8 tensor-core MoE prompt kernels | plan patch 4; ggml MMQ is the baseline; HIP variants N/A |
| `STRATA_PF_FUSED_KQ` | FEASIBLE | L: fused int8 tensor-core MoE prompt kernels | plan patch 4; ggml MMQ is the baseline; HIP variants N/A |
| `STRATA_PF_FUSED_NATIVE` | FEASIBLE | L: fused int8 tensor-core MoE prompt kernels | plan patch 4; ggml MMQ is the baseline; HIP variants N/A |
| `STRATA_PF_GEMM` | N/A | AMD / HIP-specific |  |
| `STRATA_PF_PAD` | N/A | AMD / HIP-specific |  |
| `STRATA_PF_XMX` | N/A | Intel / SYCL-specific |  |
| `STRATA_PLAN_PARALLEL` | N/A | Strata engine internal / kernel variant |  |
| `STRATA_PLE_AUTO_MMAP` | COVERED | `--lazy-mode` / global `lazy_mode` | per-layer embeddings read on demand from the mmap; no I/O queue knobs |
| `STRATA_PLE_BATCH` | COVERED | `--lazy-mode` / global `lazy_mode` | per-layer embeddings read on demand from the mmap; no I/O queue knobs |
| `STRATA_PREBUILT_URL` | N/A | Strata own model pack / installer |  |
| `STRATA_PREFILL_NVFP4` | N/A | NVFP4 is a ggml quant type; kernels chosen by ggml |  |
| `STRATA_PREFILL_X2_MIN_K` | N/A | NVFP4 is a ggml quant type; kernels chosen by ggml |  |
| `STRATA_PROMPT_ATTN_OLD` | N/A | Strata engine internal / kernel variant |  |
| `STRATA_PROMPT_ATTN_XMX` | N/A | Intel / SYCL-specific |  |
| `STRATA_Q2_BITPLANE` | N/A | Strata CPU kernel variant |  |
| `STRATA_Q6_PACKED` | N/A | AMD / HIP-specific |  |
| `STRATA_Q6K_EXPERTS` | N/A | Strata build / kernel option; ggml has MMQ natively |  |
| `STRATA_Q8_PACKED` | N/A | AMD / HIP-specific |  |
| `STRATA_QFUSE` | N/A | AMD / HIP-specific |  |
| `STRATA_QSA_CLUSTER` | FEASIBLE | L: thread-block-cluster argmax / block-select kernels for sm_90+ | plan item 21; small decode gain |
| `STRATA_ROCM_INDEX` | N/A | AMD / HIP-specific |  |
| `STRATA_ROCM_VERSION` | N/A | AMD / HIP-specific |  |
| `STRATA_ROUTE_PER_TOKEN` | N/A | AMD / HIP-specific |  |
| `STRATA_ROUTE_TAIL_SKIP` | IMPLEMENTED | `--moe-cache-tail-skip-rank` (Strata window rule: skip a miss when EVERY token ranks it N-th or lower) and `--moe-cache-tail-skip N` (per-token keep N) / model `moe_cache_tail_skip_rank`, `moe_cache_tail_skip` | Strata has it ON by default (rank 7); ours defaults to 0 = off, so set rank 7 to match; lossy |
| `STRATA_SELECT_SIMT` | N/A | Strata engine internal / kernel variant |  |
| `STRATA_SELECT_WMMA` | N/A | Strata engine internal / kernel variant |  |
| `STRATA_SH_STREAM` | COVERED | ggml CUDA graph stream optimisation, env `GGML_CUDA_GRAPH_OPT` | GAP: llama env exists, NO telecode knob; equivalence to a shared-expert second stream unverified |
| `STRATA_SM61_TABLE` | N/A | Strata kernel variant; ggml chooses its own |  |
| `STRATA_SM70_TABLE` | N/A | Strata kernel variant; ggml chooses its own |  |
| `STRATA_SYCL_AOT` | N/A | Intel / SYCL-specific |  |
| `STRATA_SYCL_BF16_GRF` | N/A | Intel / SYCL-specific |  |
| `STRATA_SYCL_BIN` | N/A | Intel / SYCL-specific |  |
| `STRATA_SYCL_EXPERT_LANES` | N/A | Intel / SYCL-specific |  |
| `STRATA_SYCL_GEMM_STAGE` | N/A | Intel / SYCL-specific |  |
| `STRATA_SYCL_PARITY` | N/A | Intel / SYCL-specific |  |
| `STRATA_SYCL_ROOT` | N/A | Intel / SYCL-specific |  |
| `STRATA_SYCL_SAMPLER_FP64` | N/A | Intel / SYCL-specific |  |
| `STRATA_SYCL_SPIN_MAX` | N/A | Intel / SYCL-specific |  |
| `STRATA_TSUM` | N/A | AMD / HIP-specific |  |
| `STRATA_VCVARS` | COVERED | tray Patched Build (`llamacpp/patcher.py`, `custom_build.source_dir`) |  |
| `STRATA_VERIFY_DEVICE_PLAN` | N/A | Intel / SYCL-specific |  |
| `STRATA_VERIFY_LEAN` | N/A | Strata engine internal / kernel variant |  |
| `STRATA_VERIFY_NO_HOST` | N/A | Intel / SYCL-specific |  |
| `STRATA_WARM_GRAPHS` | N/A | Intel graph capture |  |
| `STRATA_WDDM_BUDGET` | N/A | AMD / HIP-specific |  |
| `SYCL_CACHE_PERSISTENT` | N/A | Intel / SYCL-specific |  |
| `VISION` | N/A | Strata vision-encoder option; we use --mmproj |  |
| `vision.cuda_device` | COVERED | `--mmproj-device` / global `mmproj_device` |  |
| `vision.elastic` | FEASIBLE | M: load the projector only while images arrive | today `--sleep-idle-seconds` (global `sleep_idle_seconds`) unloads the whole model |
| `vision.elastic_idle_s` | FEASIBLE | M: load the projector only while images arrive | today `--sleep-idle-seconds` (global `sleep_idle_seconds`) unloads the whole model |
| `vision.elastic_mib` | FEASIBLE | M: load the projector only while images arrive | today `--sleep-idle-seconds` (global `sleep_idle_seconds`) unloads the whole model |
| `vision.max_tokens` | COVERED | `--image-max-tokens` / model `image_max_tokens` |  |
| `vision.min_tokens` | COVERED | `--image-min-tokens` / model `image_min_tokens` |  |
| `vision.mmproj` | COVERED | `--mmproj` / model `mmproj`, `mmproj_offload` |  |
| `ZES_ENABLE_SYSMAN` | N/A | Intel / SYCL-specific |  |

## 11. Server, API, network, security, monitoring and setup

| Name | Status | Our knob / how | Notes |
|---|---|---|---|
| `--api-key` | COVERED | `--api-key` / global `api_key` |  |
| `--api-monitor` | COVERED | proxy `proxy.debug` request dumps in `data/logs/requests/`; llama log in `data/logs/llama.log` | no live monitor page |
| `--check` | N/A | Strata installer / launcher option |  |
| `--config` | N/A | Strata installer / launcher option |  |
| `--engine` | N/A | Strata installer / launcher option |  |
| `--help` | N/A | Strata installer / launcher option |  |
| `--host` | COVERED | `--host` / global `host` |  |
| `--mcp-config` | COVERED | telecode MCP server (port 1236) and proxy managed-tool bridge (`proxy.max_roundtrips`) | llama `--mcp-servers-config` / `--tools` have no knob; unused behind the proxy |
| `--no-start` | N/A | Strata installer / launcher option |  |
| `--port` | COVERED | `--port` / global `port` |  |
| `--rollback-engine` | COVERED | Version Manager restore (`llamacpp/updater.py`) |  |
| `--root` | N/A | Strata installer / launcher option |  |
| `--script` | N/A | Strata installer / launcher option |  |
| `--serve` | N/A | Strata installer / launcher option |  |
| `--setup` | N/A | Strata installer / launcher option |  |
| `--skip-build` | N/A | Strata installer / launcher option |  |
| `--tokenizer` | N/A | Strata installer / launcher option |  |
| `--update` | COVERED | tray "Update Now" (`llamacpp/updater.py`) |  |
| `--yes` | N/A | Strata installer / launcher option |  |
| `allowed_hosts` | N/A | Strata server hardening; telecode proxy has its own |  |
| `api_key` | COVERED | `--api-key` / global `api_key` |  |
| `API_KEY` | COVERED | `--api-key` / global `api_key` |  |
| `api_monitor` | COVERED | proxy `proxy.debug` request dumps in `data/logs/requests/`; llama log in `data/logs/llama.log` | no live monitor page |
| `arch` | N/A | Strata installer / launcher option |  |
| `args` | COVERED | `extra_args` (global and per model) |  |
| `backend` | N/A | Strata installer / launcher option |  |
| `CLAUDE_CODE_EFFORT_LEVEL` | COVERED | proxy `inference_defaults.reasoning_effort` map (+ `thinking_budget`) | llama `--reasoning-effort` / `-rea` server default has no knob; the proxy map covers requests |
| `codex_thread_titles` | N/A | Strata client-quirk workaround |  |
| `CONFIG` | N/A | Strata installer / launcher option |  |
| `cors_origins` | COVERED | `proxy.cors_origins` | llama `--cors-*` flags unused behind the proxy |
| `cwd` | N/A | Strata installer / launcher option |  |
| `engine_silence_s` | COVERED | `--timeout` / global `timeout`; `ready_timeout_sec` | no stall-with-no-CPU watchdog |
| `env` | COVERED | `llamacpp.env` (global) and per-model `env` (KEY=value lines), passed by LlamaSupervisor to the spawn (`process.py`, `llamacpp/config.py parse_env_text`) | unlocks upstream env-only knobs |
| `exe` | COVERED | global `binary` |  |
| `FAKE_MCP_LOG` | N/A | Strata installer / launcher option |  |
| `GET /api-monitor, /api/requests` | COVERED | proxy `proxy.debug` request dumps in `data/logs/requests/`; llama log in `data/logs/llama.log` | no live monitor page |
| `GET /mcp` | N/A | Strata-only UI / management API; telecode has tray and MCP server |  |
| `GET /metrics` | COVERED | `--metrics` / global `metrics` |  |
| `GET /metrics/prometheus` | COVERED | `--metrics` / global `metrics` |  |
| `GET /props` | COVERED | `--props` / global `props` |  |
| `GET /status, GET /slots` | COVERED | `--slots` / global `slots` |  |
| `GET /v1/models, /models, /health` | COVERED | upstream `/health`, `/v1/models`; proxy serves the model list |  |
| `GET /v1/status, /api/health` | COVERED | upstream `/health`, `/v1/models`; proxy serves the model list |  |
| `GET/POST /config` | N/A | Strata-only UI / management API; telecode has tray and MCP server |  |
| `GET/POST /settings` | N/A | Strata-only UI / management API; telecode has tray and MCP server |  |
| `hip_ordinal` | N/A | AMD device ordinal |  |
| `host` | COVERED | `--host` / global `host` |  |
| `HOST` | COVERED | `--host` / global `host` |  |
| `images` | N/A | Strata image generation |  |
| `images.gpu_policy` | N/A | Strata image generation |  |
| `images.max_n` | N/A | Strata image generation |  |
| `images.max_side` | N/A | Strata image generation |  |
| `images.reload` | N/A | Strata image generation |  |
| `lib_dirs` | N/A | Strata installer / launcher option |  |
| `log` | COVERED | LlamaSupervisor writes `data/logs/llama.log` |  |
| `mcp.max_result_chars` | COVERED | telecode MCP server (port 1236) and proxy managed-tool bridge (`proxy.max_roundtrips`) | llama `--mcp-servers-config` / `--tools` have no knob; unused behind the proxy |
| `mcp.max_rounds` | COVERED | telecode MCP server (port 1236) and proxy managed-tool bridge (`proxy.max_roundtrips`) | llama `--mcp-servers-config` / `--tools` have no knob; unused behind the proxy |
| `mcp.timeout_s` | COVERED | telecode MCP server (port 1236) and proxy managed-tool bridge (`proxy.max_roundtrips`) | llama `--mcp-servers-config` / `--tools` have no knob; unused behind the proxy |
| `mcp_servers` | COVERED | telecode MCP server (port 1236) and proxy managed-tool bridge (`proxy.max_roundtrips`) | llama `--mcp-servers-config` / `--tools` have no knob; unused behind the proxy |
| `model_name` | COVERED | proxy `model_mapping` / client aliases |  |
| `n` | N/A | Strata image generation |  |
| `port` | COVERED | `--port` / global `port` |  |
| `PORT` | COVERED | `--port` / global `port` |  |
| `POST /v1/chat/completions` | COVERED | proxy adapters + llama native routes (`/v1/messages`, `/v1/responses`, count_tokens) |  |
| `POST /v1/images/generations` | N/A | Strata image generation |  |
| `POST /v1/messages` | COVERED | proxy adapters + llama native routes (`/v1/messages`, `/v1/responses`, count_tokens) |  |
| `POST /v1/messages/count_tokens` | COVERED | proxy adapters + llama native routes (`/v1/messages`, `/v1/responses`, count_tokens) |  |
| `POST /v1/responses` | COVERED | proxy adapters + llama native routes (`/v1/messages`, `/v1/responses`, count_tokens) |  |
| `prevent_sleep` | COVERED | global `prevent_sleep` (supervisor keeps Windows awake while llama-server runs) |  |
| `REINSTALL` | N/A | Strata installer / launcher option |  |
| `size` | N/A | Strata image generation |  |
| `steps` | N/A | Strata image generation |  |
| `STRATA_ALLOWED_HOSTS` | N/A | Strata server hardening; telecode proxy has its own |  |
| `STRATA_API_KEY` | COVERED | `--api-key` / global `api_key` |  |
| `strata_benchmark` | N/A | Strata-only UI / management API; telecode has tray and MCP server |  |
| `strata_connect_info` | N/A | Strata-only UI / management API; telecode has tray and MCP server |  |
| `STRATA_DEBUG` | COVERED | proxy `proxy.debug` request dumps in `data/logs/requests/`; llama log in `data/logs/llama.log` | no live monitor page |
| `STRATA_ENGINE_STALL_S` | COVERED | `--timeout` / global `timeout`; `ready_timeout_sec` | no stall-with-no-CPU watchdog |
| `STRATA_EXECV` | N/A | Strata installer / launcher option |  |
| `STRATA_HTTP_BACKLOG` | N/A | Strata server hardening; telecode proxy has its own |  |
| `strata_install` | N/A | Strata-only UI / management API; telecode has tray and MCP server |  |
| `strata_logs` | N/A | Strata-only UI / management API; telecode has tray and MCP server |  |
| `STRATA_MAX_BODY_MIB` | N/A | Strata server hardening; telecode proxy has its own |  |
| `strata_mcp` | COVERED | telecode MCP server (port 1236) and proxy managed-tool bridge (`proxy.max_roundtrips`) | llama `--mcp-servers-config` / `--tools` have no knob; unused behind the proxy |
| `strata_models` | N/A | Strata-only UI / management API; telecode has tray and MCP server |  |
| `STRATA_REQUEST_LINES` | COVERED | proxy `proxy.debug` request dumps in `data/logs/requests/`; llama log in `data/logs/llama.log` | no live monitor page |
| `STRATA_SKIP_SHA256` | N/A | Strata installer / launcher option |  |
| `strata_start` | N/A | Strata-only UI / management API; telecode has tray and MCP server |  |
| `STRATA_START_RETRIES` | N/A | Strata installer / launcher option |  |
| `strata_status` | N/A | Strata-only UI / management API; telecode has tray and MCP server |  |
| `strata_stop` | N/A | Strata-only UI / management API; telecode has tray and MCP server |  |
| `STRATA_WATCHDOG_S` | COVERED | `--timeout` / global `timeout`; `ready_timeout_sec` | no stall-with-no-CPU watchdog |
| `threads` | N/A | Strata image-encoder thread count |  |
| `tokenizer` | N/A | Strata installer / launcher option |  |
| `trusted_origins` | N/A | Strata server hardening; telecode proxy has its own |  |
| `vision` | COVERED | `--mmproj` / model `mmproj`, `mmproj_offload` |  |

## 12. Model management (install, load, lazy load, idle unload, aliases)

| Name | Status | Our knob / how | Notes |
|---|---|---|---|
| `--before-load` | FEASIBLE | S: optional shell hook run by the supervisor before spawn | low value here |
| `--browser` | N/A | Strata installer / model catalogue |  |
| `--data-dir` | N/A | Strata installer / model catalogue |  |
| `--family` | N/A | Strata installer / model catalogue |  |
| `--gguf-dir` | COVERED | model `path` per entry in `llamacpp.models` | llama `--models-dir` (router mode) has no knob; telecode swaps models itself |
| `--idle-unload` | COVERED | global `idle_unload_sec` (supervisor, inflight-gated) and `--sleep-idle-seconds` / `sleep_idle_seconds` |  |
| `--inspect` | COVERED | GGUF inspector (`llamacpp/gguf_info.py`, tray model card): arch, experts, quant, KV size, suggested `moe_cache_mib` from free VRAM ("Apply suggested cache size") |  |
| `--lazy` | COVERED | global `auto_start: false`; `ensure_model()` loads on first request |  |
| `--min-free-vram-mib` | COVERED | global `min_free_vram_mib` (nvidia-smi check before load, `process.py`) | refuses a load that would spill; unreadable nvidia-smi only logs a warning |
| `--model` | N/A | Strata installer / model catalogue |  |
| `--models-dir` | COVERED | model `path` per entry in `llamacpp.models` | llama `--models-dir` (router mode) has no knob; telecode swaps models itself |
| `--no-browser` | N/A | Strata installer / model catalogue |  |
| `--open` | N/A | Strata installer / model catalogue |  |
| `--source` | N/A | Strata installer / model catalogue |  |
| `aliases` | COVERED | proxy `model_mapping` / client aliases |  |
| `before_load` | FEASIBLE | S: optional shell hook run by the supervisor before spawn | low value here |
| `before_load_ignore_failure` | FEASIBLE | S: optional shell hook run by the supervisor before spawn | low value here |
| `before_load_strict` | FEASIBLE | S: optional shell hook run by the supervisor before spawn | low value here |
| `before_load_timeout_s` | FEASIBLE | S: optional shell hook run by the supervisor before spawn | low value here |
| `FAMILY` | N/A | Strata installer / model catalogue |  |
| `GGUF_DIR` | N/A | Docker variable |  |
| `idle_unload_s` | COVERED | global `idle_unload_sec` (supervisor, inflight-gated) and `--sleep-idle-seconds` / `sleep_idle_seconds` |  |
| `lazy_load` | COVERED | global `auto_start: false`; `ensure_model()` loads on first request |  |
| `min_free_vram_mib` | COVERED | global `min_free_vram_mib` (nvidia-smi check before load, `process.py`) | refuses a load that would spill; unreadable nvidia-smi only logs a warning |
| `MODEL` | N/A | Strata installer / model catalogue |  |
| `MODELSCOPE_ENDPOINT` | N/A | Strata installer / model catalogue |  |
| `open_browser` | N/A | Strata installer / model catalogue |  |
| `POST /load` | COVERED | supervisor `ensure_model` / idle unload, tray controls; llama router `/models/load`, `/models/unload` |  |
| `POST /unload` | COVERED | supervisor `ensure_model` / idle unload, tray controls; llama router `/models/load`, `/models/unload` |  |
| `POST /v1/load, /v1/unload` | COVERED | supervisor `ensure_model` / idle unload, tray controls; llama router `/models/load`, `/models/unload` |  |
| `STRATA_DROP_CACHE_ON_EXIT` | N/A | Strata installer / model catalogue |  |
| `STRATA_GGUF_PY` | N/A | Strata installer / model catalogue |  |
| `STRATA_SOURCE` | N/A | Strata installer / model catalogue |  |

## 13. Diagnostics, tests and benchmarks

| Name | Status | Our knob / how | Notes |
|---|---|---|---|
| `--check-logits` | N/A | diagnostic / debug / test switch |  |
| `--clients` | N/A | Strata bench.sh option | telecode has its own Speed Test (`llamacpp/benchmark.py`) |
| `--compare` | N/A | Strata bench.sh option | telecode has its own Speed Test (`llamacpp/benchmark.py`) |
| `--compare-only` | N/A | Strata bench.sh option | telecode has its own Speed Test (`llamacpp/benchmark.py`) |
| `--cpu-oracle-q8-0` | N/A | diagnostic / debug / test switch |  |
| `--cpu-threads` | N/A | diagnostic / debug / test switch |  |
| `--dump-final-r` | N/A | diagnostic / debug / test switch |  |
| `--dump-halves` | N/A | diagnostic / debug / test switch |  |
| `--dump-layers` | N/A | diagnostic / debug / test switch |  |
| `--dump-logits` | N/A | diagnostic / debug / test switch |  |
| `--dump-mixed` | N/A | diagnostic / debug / test switch |  |
| `--dump-residual` | N/A | diagnostic / debug / test switch |  |
| `--dump-routing` | N/A | diagnostic | the stats file keeps counts, not a trace |
| `--families` | N/A | diagnostic / debug / test switch |  |
| `--gpu-only-full` | N/A | diagnostic / debug / test switch |  |
| `--gpu-roles` | N/A | diagnostic / debug / test switch |  |
| `--gpu-stages` | N/A | diagnostic / debug / test switch |  |
| `--gr-fp32-activations` | N/A | diagnostic / debug / test switch |  |
| `--gr-native-mmvf` | N/A | diagnostic / debug / test switch |  |
| `--graph-only` | N/A | diagnostic / debug / test switch |  |
| `--keep-canonical` | N/A | diagnostic / debug / test switch |  |
| `--logits-stride` | N/A | diagnostic / debug / test switch |  |
| `--long` | N/A | Strata bench.sh option | telecode has its own Speed Test (`llamacpp/benchmark.py`) |
| `--max-new` | N/A | diagnostic / debug / test switch |  |
| `--native-bf16` | N/A | diagnostic / debug / test switch |  |
| `--native-bf16-extra` | N/A | diagnostic / debug / test switch |  |
| `--native-flash-attn-short` | N/A | diagnostic / debug / test switch |  |
| `--native-gdn` | N/A | diagnostic / debug / test switch |  |
| `--native-moe-combine` | N/A | diagnostic / debug / test switch |  |
| `--native-ple-key` | N/A | diagnostic / debug / test switch |  |
| `--native-ple-postops` | N/A | diagnostic / debug / test switch |  |
| `--native-qsa` | N/A | diagnostic / debug / test switch |  |
| `--native-qsa-indexer` | N/A | diagnostic / debug / test switch |  |
| `--native-rope` | N/A | diagnostic / debug / test switch |  |
| `--native-router` | N/A | diagnostic / debug / test switch |  |
| `--no-capture` | N/A | diagnostic / debug / test switch |  |
| `--no-fast-attn` | N/A | diagnostic / debug / test switch |  |
| `--no-fast-select` | N/A | diagnostic / debug / test switch |  |
| `--no-fused-gdn` | N/A | diagnostic / debug / test switch |  |
| `--no-fused-gr` | N/A | diagnostic / debug / test switch |  |
| `--no-hit-poke` | IMPLEMENTED | scheduler `cpu_overlap` + `ggml_backend_event_query` after the GPU split (patch 0002), always on | no switch to turn it off |
| `--no-ple-prefetch` | N/A | diagnostic / debug / test switch |  |
| `--no-publish-kernel` | N/A | diagnostic / debug / test switch |  |
| `--no-split-rows` | N/A | diagnostic / debug / test switch |  |
| `--no-token-graph` | N/A | diagnostic / debug / test switch |  |
| `--out` | N/A | Strata bench.sh option | telecode has its own Speed Test (`llamacpp/benchmark.py`) |
| `--ple-delay-us` | N/A | diagnostic / debug / test switch |  |
| `--ple-sync-submit` | N/A | diagnostic / debug / test switch |  |
| `--prefill-until` | N/A | diagnostic / debug / test switch |  |
| `--quiet` | N/A | Strata bench.sh option | telecode has its own Speed Test (`llamacpp/benchmark.py`) |
| `--ready-timeout` | N/A | Strata bench.sh option | telecode has its own Speed Test (`llamacpp/benchmark.py`) |
| `--runs` | N/A | Strata bench.sh option | telecode has its own Speed Test (`llamacpp/benchmark.py`) |
| `--shared-late` | N/A | diagnostic / debug / test switch |  |
| `--spec-corrupt` | N/A | diagnostic / debug / test switch |  |
| `--spec-follow` | N/A | diagnostic / debug / test switch |  |
| `--spec-oracle` | N/A | diagnostic / debug / test switch |  |
| `--stage-timing` | N/A | diagnostic / debug / test switch |  |
| `--stats` | IMPLEMENTED | `--moe-cache-stats-file` / model `moe_cache_stats_file` | persists per-expert use counts; hit-rate print at shutdown not verified |
| `--stream-token` | N/A | diagnostic / debug / test switch |  |
| `--sync-every-layer` | N/A | diagnostic / debug / test switch |  |
| `--think` | N/A | Strata bench.sh option | telecode has its own Speed Test (`llamacpp/benchmark.py`) |
| `--tokens` | N/A | diagnostic / debug / test switch |  |
| `--tokens-file` | N/A | diagnostic / debug / test switch |  |
| `--url` | N/A | Strata bench.sh option | telecode has its own Speed Test (`llamacpp/benchmark.py`) |
| `--version` | COVERED | `--version`; tray Version Manager shows the build |  |
| `--window-hashes` | N/A | diagnostic / debug / test switch |  |
| `STRATA_BATCH_TRACE` | N/A | diagnostic / debug / test switch |  |
| `STRATA_DBG_NAN` | N/A | diagnostic / debug / test switch |  |
| `STRATA_DECODE_TIMING` | COVERED | `--perf` / global `perf`; llama prints timings per request |  |
| `STRATA_DOORBELL_NOCHECK` | N/A | diagnostic / debug / test switch |  |
| `STRATA_DUMP_MOE_INPUT` | N/A | diagnostic / debug / test switch |  |
| `STRATA_DUMP_SEL` | N/A | diagnostic / debug / test switch |  |
| `STRATA_FS_VERIFY` | N/A | diagnostic / debug / test switch |  |
| `STRATA_HIPBLASLT_VERBOSE` | N/A | diagnostic / debug / test switch |  |
| `STRATA_IO_STATS` | N/A | diagnostic / debug / test switch |  |
| `STRATA_KEY` | N/A | diagnostic / debug / test switch |  |
| `STRATA_LOGPOS` | N/A | diagnostic / debug / test switch |  |
| `STRATA_LOGPOS_TOPK` | N/A | diagnostic / debug / test switch |  |
| `STRATA_LOOKAHEAD_STATS` | N/A | diagnostic / debug / test switch |  |
| `STRATA_METAL_KPROF` | N/A | diagnostic / debug / test switch |  |
| `STRATA_METAL_PROFILE` | N/A | diagnostic / debug / test switch |  |
| `STRATA_METAL_TRACE` | N/A | diagnostic / debug / test switch |  |
| `STRATA_NAN_GUARD` | N/A | diagnostic / debug / test switch |  |
| `STRATA_NAN_INJECT` | N/A | diagnostic / debug / test switch |  |
| `STRATA_PARITY_PROMPT_ATTN` | N/A | Strata test build option |  |
| `STRATA_PF_STEP_SYNC` | N/A | diagnostic / debug / test switch |  |
| `STRATA_PIPELINE_DEBUG` | N/A | diagnostic / debug / test switch |  |
| `STRATA_PIPELINE_FORCE_MISS` | N/A | diagnostic / debug / test switch |  |
| `STRATA_PIPELINE_LOG` | N/A | diagnostic / debug / test switch |  |
| `STRATA_PIPELINE_SPEC_DEPTH` | N/A | diagnostic / debug / test switch |  |
| `STRATA_PIPELINE_SWITCH` | N/A | diagnostic / debug / test switch |  |
| `STRATA_PIPELINE_THETA` | N/A | diagnostic / debug / test switch |  |
| `STRATA_PIPELINE_TRACE` | N/A | diagnostic / debug / test switch |  |
| `STRATA_PLE_FIXTURE_DIR` | N/A | Strata test build option |  |
| `STRATA_PLE_GGUF` | N/A | Strata test build option |  |
| `STRATA_PLE_TRACE` | N/A | diagnostic / debug / test switch |  |
| `STRATA_PREFILL_SYNC` | N/A | diagnostic / debug / test switch |  |
| `STRATA_PREFILL_TIMING` | COVERED | `--perf` / global `perf`; llama prints timings per request |  |
| `STRATA_SNAPSHOT_FULL_CAPTURE` | N/A | diagnostic / debug / test switch |  |
| `STRATA_USAGE_DUMP` | N/A | Intel profile-building diagnostic | our stats file serves the same purpose |
| `STRATA_VERIFY_ALL_SLOTS` | N/A | diagnostic / debug / test switch |  |
| `STRATA_VERIFY_ASYNC_LAUNCH` | N/A | diagnostic / debug / test switch |  |
| `STRATA_VERIFY_DEBUG` | N/A | diagnostic / debug / test switch |  |
| `STRATA_VERIFY_DUMP` | N/A | diagnostic / debug / test switch |  |
| `STRATA_VERIFY_EAGER` | N/A | diagnostic / debug / test switch |  |
| `STRATA_VERIFY_FIND` | N/A | diagnostic / debug / test switch |  |
| `STRATA_VERIFY_PROFILE` | N/A | diagnostic / debug / test switch |  |

## Gaps worth doing next

Ranked by expected benefit for big MoE models on a 12 GB GPU with 64 GB RAM. Items from the first version that are now built (cache-aware routing, async upload, promotion gain, round swaps, PCIe share, foresight, tail skip, `llamacpp.env`, spec draft sampling, adaptive draft length, min free VRAM, prevent sleep, GGUF inspector) are no longer listed; what is left:

| Rank | Item (Strata rows) | Effort | Why / how |
|---:|---|---|---|
| 1 | Make the new behaviours the default and measure: tail skip rank 7 with auto-off 90 %, PCIe share, round swaps (Strata has all three on; ours are off, see `docs/strata-default-behaviours.md`) | S | Pure settings / recipe work with `tools/moe_cache_bench.py`; output-changing items (tail skip, route bias) need the KL / identical-output check. Regenerate patch files and rebuild first (see the caveat at the top). |
| 2 | CPU share of prefill for chunks under 1024 tokens (`STRATA_PREFILL_CPU_SHARE`) | M | Lift the 31-token cap of `--moe-cache-cpu-miss-max-tokens` now that `mul_mat_id` skips ids < 0 on CPU and CUDA; add a balanced share and an A/B gate. Strata reports -20..-49 % prompt time at 512-3000 tokens. |
| 3 | PcieAuto-style refinement of the PCIe share and an automatic `--calibrate` (`--calibrate`, `--pcie-frac` auto) | M | `tools/moe_cache_bench.py` and the GGUF inspector are the building blocks; add a grid search over `moe_cache_*` and a per-PC store. |
| 4 | Additive promotion margin and landed-signal gating (`--adapt-min-gain` exact form, `STRATA_ADAPT_LAG`) | S | `cand >= victim + 1.5` with candidate floor 2.0 next to the existing ratio gain. |
| 5 | Hot-expert pinning and mlock by need (`STRATA_PIN_BY_NEED`, `STRATA_PARTIAL_PIN*`, `--resident-budget-gib`, `STRATA_PIN_GUARD`, `STRATA_PIN_RESERVE_GIB`) | M | `GGML_CUDA_REGISTER_HOST` is settable now through `env`; register only the hottest experts from the stats file, capped, with a free-RAM guard. |
| 6 | Foresight swap slots (`STRATA_FS_SLOTS`) | M | Separate per-layer slots and a copy budget so predictions never evict hot experts. |
| 7 | KV streaming / lazy KV (`--kv-resident`, `STRATA_KV_PREFETCH`, `--kv-grow`) | L | Frees VRAM for the expert cache on long contexts. Short-term: `ctx_size`, `ctx_size_spec`, or `kv_offload: false`. |
| 8 | Message-boundary checkpoints (`--prompt-cache-tail`, `STRATA_CACHE_MESSAGE_BOUNDARY`) | M | Agent turns re-send a long prefix; checkpoint at the last turn end. Partly covered by `ctx_checkpoints` + `cache_ram`. |
| 9 | Proxy-side reply guards (`reasoning_loop_recovery`, `reasoning_close_retry`, `repeat_stop_tokens`, `fit_max_tokens`) | M / S | Stream watchers and a max_tokens clamp in the proxy. |
| 10 | Batched promotion copies (`STRATA_DMA_BATCH`), exchange rotation (`STRATA_EXCHANGE_ROTATE`) | M | One H2D transfer per layer's promotions; small gain. |
| 11 | VRAM-elastic cache (`--vram-elastic`, `POST /v1/vram`) | L | CUDA VMM segments; only worth it when other GPU apps compete for the 12 GB. |
| 12 | Kernel work (`STRATA_PF_FUSED*`, `STRATA_MMVQ_IL*`, `STRATA_ARGMAX_MULTI`, `STRATA_QSA_CLUSTER`) | L each | Prefill and small-batch decode kernels; ggml MMQ / MMVQ is the baseline; the gain on Blackwell is unmeasured. |
| 13 | Replicas, vision elastic, SSD-tier read-ahead (`STRATA_IO_PF_*`, `STRATA_FILE_RELEASE`), `before_load` hook | L / M / S | Low value on one 12 GB card with 64 GB RAM. |

## COVERED but missing a telecode knob

Diff of the long options of the stock `llama-server.exe --help` against `llamacpp/argv.py` after the latest additions. Roughly 45 flags were added (spec-draft CPU placement, samplers xtc / typical / dry, control vectors, `rope_scale`, `n_cpu_ffn`, `reasoning_effort`, `chat_template_kwargs`, `logit_bias`, `ignore_eos`, `mmproj_auto`, `mtmd_batch_max_tokens`, `perf`, `prefill_assistant`, `lora_init_without_apply`, `rpc`, `offline`, `spec_draft_sampling`, ...). Everything below can still be passed through `llamacpp.extra_args` or `llamacpp.models.<m>.extra_args`.

| llama flag | Suggested settings key | Scope | Note |
|---|---|---|---|
| `--mirostat`, `--mirostat-lr`, `--mirostat-ent` | `mirostat`, `mirostat_lr`, `mirostat_ent` | model (server default) or proxy `inference_defaults.*` | not exposed |
| `--dynatemp-range`, `--dynatemp-exp` | `dynatemp_range`, `dynatemp_exp` | model / `inference_defaults.*` | not exposed |
| `--adaptive-target`, `--adaptive-decay` | `adaptive_target`, `adaptive_decay` | model / `inference_defaults.*` | not exposed |
| `--temp`, `--top-k`, `--top-p`, `--min-p`, `--repeat-penalty`, `--presence-penalty`, `--frequency-penalty` | `inference_defaults.*` (exists) | model / global | by design the proxy applies these per request, not the server CLI; direct clients of llama-server on port 1234 do not get them |
| `--spec-draft-hf` | `hf_repo_draft` | model | telecode uses local paths |
| `--tags`, `--embd-normalize` | `tags`, `embd_normalize` | global | embedding servers only |

Server / UI flags telecode does not need behind its proxy (low priority): `--cors-origins`, `--cors-methods`, `--cors-headers`, `--cors-credentials` (proxy has `proxy.cors_origins`), `--ssl-key-file`, `--ssl-cert-file`, `--api-key-file`, `--sse-ping-interval` (proxy has `proxy.ping_interval`), `--reuse-port`, `--path`, `--ui`, `--ui-config`, `--ui-config-file`, `--ui-mcp-proxy`, `--tools`, `--tools-runtime`, `--mcp-servers-config`, `--mcp-servers-json`, `--models-dir`, `--models-preset`, `--models-max`, `--models-autoload` (router mode; LlamaSupervisor swaps models itself), `--log-disable`, `--log-file`, `--log-jsonl`, `--log-colors`, `--log-prefix`, `--log-timestamps`, `--log-prompts-dir` (supervisor redirects stdout to `data/logs/llama.log`), `--spm-infill`, `--list-devices`, `--version`, `--completion-bash`, and the preset shortcuts (`--fim-qwen-*`, `--gpt-oss-*`, `--vision-gemma-*`, `--embd-gemma-default`). Alias-only differences (`--draft`, `--draft-min`, `--spec-ngram-size-*`) are covered by `draft_n`, `draft_n_min` and `spec_ngram_*`.

Environment-only upstream knobs are no longer a gap: `llamacpp.env` (global) and per-model `env` pass any variable to the spawn (`GGML_CUDA_REGISTER_HOST`, `GGML_OP_OFFLOAD_MIN_BATCH`, `LLAMA_ATTN_ROT_DISABLE`, `GGML_CUDA_DISABLE_GRAPHS`, `GGML_CUDA_GRAPH_OPT`, `GGML_CUDA_PDL`, `GGML_CUDA_MMQ_PREC`, `LLAMA_ARG_*`, ...). Windows extras from the same work: `win_power_throttle_off`, `win_process_priority`, `prevent_sleep`, `min_free_vram_mib` (all `llamacpp.*`, tray "Spawn" card).

## Still not built

**FEASIBLE items remaining (61 rows, grouped)**

| Group | Rows | Effort | One line |
|---|---|---|---|
| KV hot/cold streaming and lazy KV | `--kv-resident`, `STRATA_KV_PREFETCH`, `--kv-grow`, `--no-kv-grow`, `STRATA_KV_GROW` | L | KV in VRAM only where requests reach, rest in RAM, prefetch on a side stream. |
| VRAM-elastic cache | `--vram-elastic`, `--vram-segment-mib`, `vram_elastic`, `vram_segment_mib`, `POST /v1/vram reserve_mib` | L | CUDA VMM segments so `moe_cache_mib` can shrink and grow while loaded. |
| Hot-expert pinning / residency | `--resident-budget-gib`, `STRATA_PIN_BY_NEED`, `STRATA_ARENA_PIN_GIB`, `STRATA_PARTIAL_PIN`, `STRATA_PARTIAL_PIN_GIB` | M | Register / lock only the hottest CPU experts by the stats file, capped. |
| Pinning guards | `STRATA_PIN_GUARD`, `STRATA_PIN_RESERVE_GIB`, `STRATA_RESIDENT_HEADROOM_GIB` | S | Free-RAM guard around pinning and locking. |
| Foresight slots | `STRATA_FS_SLOTS` | M | Separate swap slots with a copy budget. |
| Copy path refinements | `STRATA_DMA_BATCH`, `STRATA_EXCHANGE_ROTATE` | M | Batch promotion copies; rotate buffer ownership. |
| SSD tier | `STRATA_IO_PF_AHEAD`, `STRATA_IO_PF_STAGE`, `STRATA_IO_PF_THREADS`, `STRATA_IO_PREFETCH`, `STRATA_IO_PREFETCH_DEPTH`, `STRATA_FILE_RELEASE` | M | Read-ahead of predicted experts and working-set release; only for models larger than RAM. |
| Prompt path | `STRATA_PREFILL_CPU_SHARE` (M), `--prefill-borrow`, `--no-prefill-borrow`, `--prefill-head-alias` (L) | M / L | CPU share above 31 tokens; lend cache slots or the output head to the prompt buffers. |
| Prompt cache | `--prompt-cache-tail`, `STRATA_CACHE_MESSAGE_BOUNDARY` | M | Checkpoint at the last turn boundary. |
| Speculative decoding | `--draft-vocab`, `--mtp-draft-vocab` | L | Restrict the MTP head output to a token subset. |
| Reply guards in the proxy | `reasoning_close_retry`, `reasoning_loop_recovery` (M), `repeat_stop_tokens`, `--fit-max-tokens`, `fit_max_tokens` (S) | M / S | Watchers and a max_tokens clamp. |
| Replicas | `--replicas`, `replicas`, `replica_cpus`, `replica_load_tokens`, `STRATA_REPLICA_START` | L | Several llama-server instances behind a router; no gain on one GPU. |
| Calibration | `--calibrate` | M | Automatic search over `moe_cache_*` and threads. |
| Kernels | `STRATA_ARGMAX_MULTI`, `STRATA_QSA_CLUSTER`, `STRATA_MMVQ_IL`, `STRATA_MMVQ_IL_ROWS`, `STRATA_PF_FUSED`, `STRATA_PF_FUSED_KQ`, `STRATA_PF_FUSED_NATIVE` | L | CUDA kernel ports; ggml MMQ / MMVQ is the baseline. |
| Vision on demand | `vision.elastic`, `vision.elastic_idle_s`, `vision.elastic_mib` | M | Load the projector only while images arrive. |
| Before-load hook | `--before-load`, `before_load`, `before_load_ignore_failure`, `before_load_strict`, `before_load_timeout_s` | S | Optional shell command before spawn; low value here. |

**N/A groups (453 rows) and why**

| Group | Reason |
|---|---|
| AMD / HIP / ROCm switches (about 40) | Strata kernels for AMD cards; we run CUDA. |
| Intel SYCL / Arc switches (about 35) | Strata's Intel engine. |
| Apple Metal switches (about 10) | Strata's Mac engine. |
| CPU kernel variants (IQ, Q2_0 bit-plane, AVX-VNNI, ISA floor) | ggml-cpu selects its own kernels. |
| Strata kernel tuning (hyper-connection, GDN, GR, QSA scorers, attention variants) | Strata's own model-specific kernels; ggml has its own. |
| NVFP4 and quantization variants | NVFP4 / MXFP4 / Q2_0 are ggml types handled by the GGUF and ggml. |
| Strata model pack, PLE reader, native GGUF paths | Strata's own pack format; llama loads GGUF directly. |
| Image generation (`--image-*`, `/v1/images/generations`) | Not an llama.cpp feature. |
| Installer, launcher, Docker variables, model catalogue, setup options | Strata setup flow; telecode has its tray and updater. |
| Strata server hardening, monitor and management MCP tools | telecode proxy / tray / MCP server cover these differently. |
| Batch-slot scheduler, pipeline windows, multi-GPU split tuning | llama-server schedules slots and splits layers itself. |
| Conversation-checkpoint internals (`--turn-token`, `--tail-role-token`, session-file reserves) | Strata session format; `cache_ram` and slot save cover it. |
| Client-quirk workarounds (Codex titles / compaction, opencode env, stateful Responses fields) | Strata API shims; the proxy has its own. |
| Diagnostics, dumps, A/B arms, fault injection, bench.sh options (about 105) | Strata debugging; telecode has the Speed Test and `tools/moe_cache_bench.py`. |

## Verification notes

- Verified by grep in `build/llama.cpp`: the flag list in `common/arg.cpp` for `--moe-cache-*` and `--spec-draft-adaptive*`, the 1..31 range of `--moe-cache-cpu-miss-max-tokens`, `cpu_overlap` and `ggml_backend_event_query` in `ggml-backend.cpp`, `GGML_CUDA_REGISTER_HOST`, `GGML_OP_OFFLOAD_MIN_BATCH`, `LLAMA_ATTN_ROT_DISABLE`, mmap read-ahead, `--load-mode` values, `--lazy-mode`, `--spec-type` incl. `draft-mtp`, stats file saved on destruction and every 4000 calls. In telecode: the argv rows listed above, `llamacpp.env` parsing (`config.parse_env_text`, `process.py` spawn), `prevent_sleep`, `win_power_throttle_off`, `win_process_priority`, `min_free_vram_mib`, `gguf_info.py` summary with suggested cache size, `cpu_topology.py` detection, the tray rows for `moe_cache_pcie_share`, `moe_cache_swap_every`, `moe_cache_tail_skip_rank`.
- Not verified: whether the current `build-telecode` binary carries the newest flags (its `--help` does not list them); whether `--fit` accounts for `--moe-cache-mib`; whether the proxy passes `logit_bias` / `logprobs` / `return_progress` through; Windows behaviour of `--load-mode dio` and `GGML_CUDA_REGISTER_HOST`; the chaining semantics of `draft-mtp,ngram-mod`; all Strata-side behaviour (from `strata-params.md` and `strata-default-behaviours.md`, not from Strata's source).
- Telecode MoE settings (model scope): `moe_cache_mib`, `moe_cache_cpu_miss`, `moe_cache_promote_after`, `moe_cache_promote_max`, `moe_cache_promote_gain`, `moe_cache_decay`, `moe_cache_cpu_miss_max_tokens`, `moe_cache_stats_file`, `moe_cache_prewarm`, `moe_cache_route_bias`, `moe_cache_route_top`, `moe_cache_route_scale`, `moe_cache_foresight`, `moe_cache_tail_skip`, `moe_cache_tail_skip_rank`, `moe_cache_tail_skip_auto_off`, `moe_cache_evict_window`, `moe_cache_pcie_share`, `moe_cache_pcie_max`, `moe_cache_async_upload`, `moe_cache_swap_every`, `moe_cache_swap_max`, `ctx_size_spec`; global: `spec_draft_adaptive`, `spec_draft_adaptive_min_gain`, `spec_draft_adaptive_ema`, `spec_draft_adaptive_probe`. `argv.py` emits the tuning flags only with `moe_cache_cpu_miss` on and `moe_cache_mib > 0`, so a stock binary never sees them.
