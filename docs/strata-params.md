# Strata parameter inventory

Source: a read-only clone of the Strata inference engine (engine 0.1.44.x era), mined from README.md, docs/*.md, serve/*.py, setup.py and src/program/generate.cpp (the engine's `usage()` text and argument parser). Nothing was run.

Kinds used: `engine flag` (command line of the `strata` engine, usually placed in the run config's `args` list), `run-config key` (key of `strata-<model>.json`), `env switch` (`STRATA_*` and a few external variables, set in the config's `env` block or the environment), `API request field` (HTTP request body or query), plus `setup option` (setup.py / START-HERE.bat / setup.sh), `server option` (serve/server.py), `bench option`, `build option` (CMake / Docker build), `API endpoint` and `MCP tool` for entries that are not one of the first four.

A name that exists on two surfaces (for example `reasoning_effort` as a request field and as a run-config key) is listed once with both kinds. Defaults are the documented ones; `-` means the docs give none, `off` means opt-in.

## Summary

| # | Category | Parameters |
|---:|---|---:|
| 1 | MoE expert cache and placement (VRAM / RAM / SSD tiers, pinning, adaptive cache, helper GPU caches, elastic VRAM) | 80 |
| 2 | CPU compute of experts, threading and affinity | 12 |
| 3 | PCIe, DMA and transfer | 11 |
| 4 | Prompt processing (prefill chunking, fused kernels, CPU share, head alias) | 26 |
| 5 | KV cache (quantization, streaming, prefetch, prompt cache, checkpoints, conversation cache, session files) | 32 |
| 6 | Speculative decoding (MTP, prompt lookup, draft policy, verify window, tail skip) | 22 |
| 7 | Sampling, reasoning and response behaviour | 52 |
| 8 | Batching, parallel slots and replicas | 17 |
| 9 | Multi-GPU (layer split, helper and peer GPUs, pipeline) | 42 |
| 10 | Quantization, kernels, models and hardware-specific switches | 208 |
| 11 | Server, API, network, security, monitoring and setup | 96 |
| 12 | Model management (install, load, lazy load, idle unload, aliases) | 33 |
| 13 | Diagnostics, tests and benchmarks | 107 |
| | **Total** | **738** |

## 1. MoE expert cache and placement (VRAM / RAM / SSD tiers, pinning, adaptive cache, helper GPU caches, elastic VRAM)

| Name | Kind | Default | What it does | Source file |
|---|---|---|---|---|
| `--adapt-async` | engine flag | 0 | 1 advances adaptive-tier swaps between decode windows on a helper thread. | DETAILS.md |
| `--adapt-decay` | engine flag | 0.7 | How fast the adaptive expert cache's routing counts decay. | DETAILS.md, generate.cpp |
| `--adapt-every` | engine flag | 4 | Makes the GPU expert cache follow the conversation by swapping experts every N windows. | DETAILS.md, generate.cpp |
| `--adapt-swaps` | engine flag | 96 | 0 disables adaptive swaps (for byte-identical repeats). | DETAILS.md, generate.cpp |
| `--adapt-tuned` | engine flag | off | Opt-in tuned adaptive-tier policy on the Intel port (neutral on the test card). | INTEL.md |
| `--expert-cache` | engine flag | auto | Expert cache size as N times the largest expert blob, or auto. | DETAILS.md, generate.cpp |
| `--expert-cache-cpu-order` | engine flag | off | Experimental GPU expert reduction matching CPU order. | generate.cpp |
| `--expert-cache-device1` | engine flag | off | Expert cache slots (number or auto) on helper GPU CUDA1 (others ranked-pair experts computed there). | SECOND_GPU.md |
| `--expert-cache-device2` | engine flag | off | Expert cache slots (number or auto) on helper GPU CUDA2. | SECOND_GPU.md |
| `--expert-cache-device3` | engine flag | off | Expert cache slots (number or auto) on helper GPU CUDA3. | SECOND_GPU.md |
| `--expert-cache-per-layer` | engine flag | on via setup | Gives each layer its own cache slots instead of one shared arrival-order counter. | generate.cpp |
| `--expert-cache-remote` | engine flag | off | Older helper-GPU caches that take visible GPUs no stage runs on. | SECOND_GPU.md |
| `--expert-cache-remote-placement` | engine flag | stripe | Engine form of --gpu-placement (layer or stripe). | SECOND_GPU.md |
| `--expert-profile` | engine flag | shipped data/expert-profile*.bin | Expert ranking used to fill the GPU expert cache at start. | DETAILS.md |
| `--expert-profile-save` | engine flag | unset | Path the learned expert profile is saved to. | generate.cpp |
| `--expert-profile-save-every` | engine flag | 10 (minutes) | Minutes between learned-profile saves (0 = on QUIT only). | generate.cpp |
| `--gpu-placement` | setup option | stripe | layer gives each layer to exactly one helper GPU, stripe spreads experts across them. | SECOND_GPU.md |
| `--gpu1-experts` | setup option | off | Setup option giving CUDA1 N helper expert slots. | SECOND_GPU.md |
| `--gpu2-experts` | setup option | off | Setup option giving CUDA2 N helper expert slots. | SECOND_GPU.md |
| `--gpu3-experts` | setup option | off | Setup option giving CUDA3 N helper expert slots. | SECOND_GPU.md |
| `--kv-grow` | engine flag | off | The K/V takes VRAM only for cells requests reach and the expert cache holds the rest (--no-kv-grow turns it off). | DETAILS.md |
| `--low-ram` | setup option | auto by setup | on/off in setup, resident/mmap forces a low-RAM variant. | DETAILS.md |
| `--mmap-experts` | engine flag | chosen by setup (low-RAM) | Maps the experts from experts.bin (or the GGUFs) so the OS file cache holds what the GPU does not. | DETAILS.md |
| `--no-kv-grow` | engine flag | - | Turns off --kv-grow (also STRATA_KV_GROW=0). | generate.cpp |
| `--no-remote-expert-opt` | setup option | off | Leaves --remote-expert-opt out of the written config. | SECOND_GPU.md |
| `--reorder` | tool option (tools/make_profile.py) | off | Puts your trace's expert pairs first, then the base profile's, then the rest. | DETAILS.md |
| `--resident-budget-gib` | engine flag | unset | GiB of the profile's hottest experts kept locked in RAM at start, the rest read from files; implies --mmap-experts. | DETAILS.md |
| `--resident-cpu-experts` | engine flag | off | With mmap and a static profile, keeps the experts the GPU cache does not hold resident in ordinary RAM. | generate.cpp |
| `--resident-experts` | engine flag | chosen by setup | Copies exactly the experts the GPU does not hold from experts.bin into RAM at start so nothing is read from the SSD while answering. | DETAILS.md |
| `--shared-expert-arena` | engine flag | off (Linux) | Backs the resident arena with one MAP_SHARED file (put it on /dev/shm). | generate.cpp |
| `--stream-experts` | engine flag | on for Intel Arc via setup | Intel SYCL engine: keeps every expert in VRAM streamed from the GGUF with no host copy. | INTEL.md |
| `--trim-stage-weights` | engine flag | off | Trims stage weights to cut RAM use with mapped experts. | DETAILS.md |
| `--vram-elastic` | engine flag | off | Enables the segmented elastic expert cache. | DETAILS.md |
| `--vram-reserve-mib` | engine flag | 700 | VRAM in MiB the engine leaves free, deducted before the expert cache is sized. | DETAILS.md |
| `--vram-segment-mib` | engine flag | 512 | Segment size of the elastic expert cache (at least 64). | generate.cpp |
| `expert_cache` | run-config key | auto | Same as --expert-cache. | DETAILS.md |
| `expert_profile_save` | run-config key | unset | File the engine saves the learned expert profile to on clean exit and periodically. | DETAILS.md |
| `expert_profile_save_every` | run-config key | 10 (minutes) | Minutes between learned-profile saves (0 = exit only). | DETAILS.md |
| `LOW_RAM` | env switch | auto | Docker: on maps the experts from the pack (needed under a memory limit). | INSTALL.md |
| `POST /v1/vram reserve_mib` | API request field | - | Shrinks the expert cache until that much VRAM is free (null grows back, 0 takes all back). | DETAILS.md |
| `remote_expert_opt` | run-config key | true on 2+ GPUs from setup | false leaves --remote-expert-opt out of the config (setup --no-remote-expert-opt). | SECOND_GPU.md |
| `RESIDENT_BUDGET_GIB` | env switch | unset | Docker: passes setup's --resident-budget-gib. | INSTALL.md |
| `STRATA_ADAPT_ASYNC_BATCH` | env switch | 0 (all) | Posts at most N async copies per tick. | DETAILS.md |
| `STRATA_ADAPT_ASYNC_WAIT` | env switch | 1 (arena) | Waits for the in-flight async round at the next tick so the window an expert becomes GPU-resident is timing independent. | DETAILS.md |
| `STRATA_ADAPT_CALLBACK_WARMUP` | env switch | on (Linux) | 0 turns off starting the GPU host-callback thread before the CPU pool pins the host thread. | DETAILS.md |
| `STRATA_ADAPT_LAG` | env switch | 1 | A window takes the adaptive swaps once they are N windows old. | DETAILS.md |
| `STRATA_ADAPT_NOWAIT` | env switch | off | Adaptive-tier no-wait switch excluded when the overlap runs. | DETAILS.md |
| `STRATA_ADAPT_OVERLAP` | env switch | on CUDA Linux 0.1.43 / opt-in elsewhere | 1 makes the adaptive tier's copies land while the next window runs. | DETAILS.md |
| `STRATA_ADAPT_PICK_INLINE` | env switch | 1 | Runs the async swap pick on the host thread, one round per window. | DETAILS.md |
| `STRATA_ADAPT_SIGNAL` | env switch | copy (host on Windows) | host uses a host function, copy a stream-ordered 4-byte copy for the overlap's per-layer landed signal. | DETAILS.md |
| `STRATA_ARENA_MMAP` | env switch | off (Linux) | 1 maps a native pack's expert arena read-only from experts.bin instead of reading it into locked RAM. | DETAILS.md |
| `STRATA_ARENA_PIN_GIB` | env switch | 29 (Windows) | Cap in GiB of arena RAM registered (page-locked) with STRATA_PIN_BY_NEED. | DETAILS.md |
| `STRATA_DISJOINT_ADAPT` | env switch | off | 1 keeps helper-GPU-owned experts out of the primary cache's promotion candidates so no expert occupies two cards. | DISJOINT_EXPERT_CACHE.md |
| `STRATA_EXCHANGE_ROTATE` | env switch | off | 1 hands buffer ownership over on an adaptive swap instead of copying the evicted blob into the RAM copy. | DETAILS.md |
| `STRATA_FETCH_THREADS` | env switch | 8 | Threads fetching a layer's missing experts when the GGUF is read in place. | DETAILS.md |
| `STRATA_FILE_RELEASE` | env switch | 0 (Windows) | 1 trims file-backed pages of experts after their GPU uploads complete. | DETAILS.md |
| `STRATA_FILL_AHEAD` | env switch | 256 | How many GPU cache fill pairs are asked for ahead. | DETAILS.md |
| `STRATA_FS_AHEAD` | env switch | on | 0 keeps only the misses for the Foresight slots (no look-ahead). | DETAILS.md |
| `STRATA_FS_SLOTS` | env switch | 0 | Foresight swap space: N VRAM slots per layer refilled with just-missed and predicted experts. | DETAILS.md |
| `STRATA_IO_PF_AHEAD` | env switch | on | 0 keeps only the demand reads. | DETAILS.md |
| `STRATA_IO_PF_STAGE` | env switch | off | 1 stages every uncached expert in a buffer and makes the layer wait for them. | DETAILS.md |
| `STRATA_IO_PF_THREADS` | env switch | 8 | I/O threads for file-tier prefetch. | DETAILS.md |
| `STRATA_IO_PREFETCH` | env switch | off (Linux) | 1 reads uncached and next-layer predicted experts by I/O threads with whole-blob preads. | DETAILS.md |
| `STRATA_IO_PREFETCH_DEPTH` | env switch | 2 when on | Prefetch depth in layers for file-tier I/O. | DETAILS.md |
| `STRATA_LEND_MIRROR` | env switch | on (Intel) | 0 turns off mirroring of experts in the last 10% of cache slots on Intel. | INTEL.md |
| `STRATA_LOOKAHEAD` | env switch | on | 0 turns off the next-layer router look-ahead page warming. | DETAILS.md |
| `STRATA_LOOKAHEAD_K` | env switch | 10 | Number of next-layer experts the router look-ahead predicts for prefetch. | DETAILS.md |
| `STRATA_NO_ARENA_THP` | env switch | off | 1 skips MADV_HUGEPAGE on the expert arena. | TROUBLESHOOTING.md |
| `STRATA_OWNED_PRICE` | env switch | 0.1.39 rule | exact prices the prompt path's own buffers by their real allocation in cache sizing. | DETAILS.md |
| `STRATA_PIN_BY_NEED` | env switch | off | 1 page-aligns blobs and registers the profile's non-GPU-resident experts by rank up to STRATA_ARENA_PIN_GIB. | DETAILS.md |
| `STRATA_PIN_GUARD` | env switch | on | 0 turns off page-locking the expert copy in steps guarded by free RAM. | TROUBLESHOOTING.md |
| `STRATA_PIN_RESERVE_GIB` | env switch | 4 | RAM in GiB that must stay free while pinning the expert copy. | TROUBLESHOOTING.md |
| `STRATA_PIPELINE_ADAPT_ASYNC` | env switch | on | 0 uses the blocking adaptive tier with --pipeline-windows 2. | DETAILS.md |
| `STRATA_READ_AHEAD` | env switch | on (Linux) | 0 turns off madvise/fadvise read-ahead of weights at start. | DETAILS.md |
| `STRATA_RESIDENT_HEADROOM_GIB` | env switch | 4 | RAM in GiB the resident variant leaves free. | DETAILS.md |
| `STRATA_RESIDENT_PIN` | env switch | 1 | 0 disables page-locking of the resident RAM copy (gives mapped mode's exact tokens). | DETAILS.md |
| `STRATA_UMA_HEADROOM_GIB` | env switch | 6 | GiB left to the system on a unified-memory (Strix Halo) memory plan. | STRIX_HALO.md |
| `STRATA_UNBUFFERED_LOAD` | env switch | auto | 1 forces and 0 disables unbuffered reads at model load. | TROUBLESHOOTING.md |
| `vram_elastic` | run-config key | false | Allocates the expert cache in segments so POST /v1/vram can shrink or grow it (engine --vram-elastic). | DETAILS.md |
| `vram_segment_mib` | run-config key | 512 | Size of one elastic expert cache segment in MiB. | DETAILS.md |

## 2. CPU compute of experts, threading and affinity

| Name | Kind | Default | What it does | Source file |
|---|---|---|---|---|
| `--aux-cpus` | engine flag | off | auto or a CPU list places non-pool, non-host threads on spare CPUs (Linux). | DETAILS.md |
| `--host-core` | engine flag | first | last runs the host thread on the last physical core and the workers on the first (Windows). | DETAILS.md |
| `--no-host-worker` | engine flag | off | A/B: host thread does not join the CPU expert drain. | generate.cpp |
| `--no-pool` | engine flag | off | Skips the CPU expert pool (GPU-only floor). | generate.cpp |
| `--pool-affinity` | engine flag | all | Worker CPU affinity: all, auto (P-cores first on hybrid CPUs) or p-cores. | generate.cpp |
| `--pool-tasks` | engine flag | 0 (3 tasks per thread) | Target total row tasks in each batched CPU expert Gate/Up and Down phase. | DETAILS.md |
| `--pool-workers` | engine flag | calibrated | How many CPU threads compute experts. | DETAILS.md, generate.cpp |
| `STRATA_ADAPT_JOB_CPU` | env switch | unset | Explicit CPU for the adaptive tier's job thread (wins over --aux-cpus). | DETAILS.md |
| `STRATA_AUX_CPUS` | env switch | off | Same as --aux-cpus. | DETAILS.md |
| `STRATA_AUX_STAGER` | env switch | on | 0 leaves the prefill copy threads where they were started. | DETAILS.md |
| `STRATA_HOST_CORE` | env switch | first | Same as --host-core. | DETAILS.md |
| `strata_tune.aux_cpus` | API request field | start-up setting | Per request: 1/0/true/false moves threads to the aux CPU set or back. | DETAILS.md |

## 3. PCIe, DMA and transfer

| Name | Kind | Default | What it does | Source file |
|---|---|---|---|---|
| `--pcie-frac` | engine flag | auto (set from CPU speed) | Fraction of missed experts the GPU computes over PCIe instead of the CPU; 0 gives CPU-only exact tokens. | DETAILS.md |
| `--pcie-mode` | engine flag | kernel copy | dma restores the old DMA expert copy way. | DETAILS.md, generate.cpp |
| `--spec-min-p` | engine flag | calibrated | How sure the draft layer must be to add another guess to a check. | DETAILS.md, generate.cpp |
| `pcie_frac` | API request field | engine default | Per-request PCIe share (a request that sets it is left alone by the auto refinement). | DETAILS.md |
| `STRATA_DMA_BATCH` | env switch | off | Batched DMA expert copy switch (see BATCHED_DMA.md). | DETAILS.md, BATCHED_DMA.md |
| `STRATA_IO_THREADS` | env switch | 16 (PLE reader queue depth) | Blocking O_DIRECT threads of the PLE row reader (for example 32 or 64). | AMD_HIP_PERFORMANCE.md |
| `STRATA_PCIE_AUTO_CANON` | env switch | off on Windows (on Linux) | 1 enables the auto PCIe share for the canonical Q2_0 pack on Windows. | DETAILS.md |
| `STRATA_PCIE_AUTO_SPLIT` | env switch | off (HIP) | 1 refines a two-stage layer split's PCIe share per stage from its probed link. | DETAILS.md |
| `STRATA_PCIE_FRAC_DEFAULT` | env switch | auto-refine (new rule) | old keeps the 0.1.41 PCIe-share rule instead of refining it from CPU pool speed. | DETAILS.md |
| `STRATA_PCIE_MIN1` | env switch | off | 1 sends one missed expert over PCIe as soon as a layer has two misses. | DETAILS.md |
| `STRATA_STAGE_PIN` | env switch | off | 1 page-locks the stage buffers used when experts are served from GGUF in place. | DETAILS.md |

## 4. Prompt processing (prefill chunking, fused kernels, CPU share, head alias)

| Name | Kind | Default | What it does | Source file |
|---|---|---|---|---|
| `--no-prefill-borrow` | engine flag | off | Stops the prompt path borrowing expert-cache slots (for exact batch comparisons). | BATCHING.md |
| `--prefill` | engine flag | auto | Prompt chunk size; auto = largest chunk whose buffers fit in the borrowed cache slots (up to 8192). | DETAILS.md, generate.cpp |
| `--prefill-borrow` | engine flag | auto (Intel) | Lends expert-cache slots to the prompt path (--no-prefill-borrow decides explicitly). | INTEL.md |
| `--prefill-head-alias` | engine flag | off | Lets the output head's VRAM hold the prompt buffers while a prompt is read (one GPU, --serve). | DETAILS.md |
| `STRATA_ARENA_REGISTER` | env switch | on | 0 skips cudaHostRegister of the expert arena (local bench switch mentioned in PREFILL_HEAD_ALIAS.md). | PREFILL_HEAD_ALIAS.md |
| `STRATA_EMB_REUSE_ACCOUNT` | env switch | off | 1 lets the planner use the VRAM saved by bo sharing emb, growing the chunk. | DETAILS.md |
| `STRATA_GFX12_CHUNK` | env switch | on (gfx12 Linux) | 0 keeps 8192-token chunks for a bare --prefill auto on gfx12. | DETAILS.md |
| `STRATA_GFX12_DEFAULTS` | env switch | on (gfx12 Linux) | 0 keeps gfx12 defaults off (8192 chunk). | DETAILS.md |
| `STRATA_METAL_PROMPT` | env switch | batched | window reads the prompt through decode windows (slow exact fallback). | MAC.md |
| `STRATA_METAL_PROMPT_CHUNK` | env switch | 2048 (4096 streaming 96 GB+, 1024 smaller streaming) | Prompt chunk size on the Metal engine. | MAC.md |
| `STRATA_PF_SWITCH_MIN_T` | env switch | - | Minimum chunk length (for example 4096) for the fused/GEMM prompt switch (HIP). | STRIX_HALO.md |
| `STRATA_PREFILL_AUTO_MAX` | env switch | 8192 | Upper bound of the chunk --prefill auto may pick. | DETAILS.md |
| `STRATA_PREFILL_CPU_SHARE` | env switch | auto on one CUDA GPU without --batch (chunks under 1024 tokens) | auto or a fixed fraction hands the CPU pool the experts few of the chunk's tokens route to; 0 turns it off. | DETAILS.md |
| `STRATA_PREFILL_CPU_SHARE_MAX` | env switch | 1024 | Largest prompt chunk (tokens) the CPU share applies to (3072 opt-in extension). | DETAILS.md |
| `STRATA_PREFILL_EQUAL` | env switch | off | 1 reads a prompt segment in chunks of equal size (changes rounding). | DETAILS.md |
| `STRATA_PREFILL_FIRST` | env switch | 256 (Intel) | Size of the short first prompt chunk so the GPU starts early. | INTEL.md |
| `STRATA_PREFILL_MMQ` | env switch | on when built | 1 uses ggml MMQ prompt kernels on HIP (also a build option). | AMD_HIP_PERFORMANCE.md |
| `STRATA_PREFILL_RING` | env switch | auto | Streamed ring slots for the prompt path. | MULTI_GPU.md |
| `STRATA_PREFILL_STREAM_ALL` | env switch | auto | 1/0 forces streaming of every expert for prompts on or off. | INTEL.md |
| `STRATA_PREFILL_STREAM_MIN` | env switch | 1024 | Smallest prompt chunk that uses the fused experts / streaming (for example 128). | STRIX_HALO.md |
| `STRATA_PROMPT_DIRECT` | env switch | off (Linux) | 1 reads the prompt's experts directly (unbuffered) from the file. | QUANTS.md |
| `STRATA_RING_BYTES` | env switch | auto | 0 restores 0.1.39's prompt streaming ring, loan and chunk choice. | DETAILS.md |
| `STRATA_STAGER_RING` | env switch | 128 | Depth of the prompt stager ring when experts are read from the GGUF in place. | UNSLOTH_Q4.md |
| `STRATA_STAGER_SLEEP` | env switch | spin on Windows, sleep on Linux | 1 makes the prompt-staging threads sleep while waiting on Windows (less CPU, 5-6% slower); 0 forces spinning on Linux. | DETAILS.md |
| `STRATA_STAGER_SSD` | env switch | auto (mincore check) | 1/0 forces the SSD or RAM stager profile for a GGUF read in place. | DETAILS.md |
| `STRATA_STAGER_THREADS` | env switch | auto | Number of prompt-staging copy threads (up to 32); wins over STRATA_STAGER_SSD. | DETAILS.md |

## 5. KV cache (quantization, streaming, prefetch, prompt cache, checkpoints, conversation cache, session files)

| Name | Kind | Default | What it does | Source file |
|---|---|---|---|---|
| `--conversation-cache-mib` | engine flag | 0 (off) | MiB of parked-conversation cache. | DETAILS.md, generate.cpp |
| `--conversation-cache-min-free-mib` | engine flag | 2560 | RAM headroom that must remain for parking or session-file save/restore. | DETAILS.md |
| `--conversation-cache-min-tokens` | engine flag | 0 | Parks only conversations of at least N tokens. | DETAILS.md |
| `--conversation-cache-slots` | engine flag | 0 | Number of parked conversations the host-RAM conversation cache keeps. | DETAILS.md, generate.cpp |
| `--kv` | engine flag / setup option | 8-bit (q4_0 / k8v4 optional) | KV cache format: 8-bit default, q4_0 Hadamard 4-bit, k8v4 hybrid. | DETAILS.md, generate.cpp, setup.py |
| `--kv-resident` | engine flag | 32768 when streaming | Number of KV cells kept in VRAM while the rest streams from RAM. | DETAILS.md, generate.cpp |
| `--kv-streaming` | setup option | auto | on/off overrides setup's KV-streaming decision (never under WSL). | DETAILS.md |
| `--memory-limit-mib` | engine flag | 0 (none) | Total MiB the engine's container/cgroup may use, for the RAM headroom figure. | DETAILS.md |
| `--prompt-cache` | engine flag | on | 0 disables the prompt cache that resumes repeated prompts. | DETAILS.md, generate.cpp |
| `--prompt-cache-every` | engine flag | 16384 | Takes a conversation checkpoint every N prompt tokens. | DETAILS.md, generate.cpp |
| `--prompt-cache-root` | engine flag | 2048 | Minimum system-prompt length that gets a root checkpoint (0 = none). | DETAILS.md, generate.cpp |
| `--prompt-cache-tail` | engine flag | off | Saves one extra mid-prompt checkpoint at the first chunk boundary within one chunk of the prompt's end (single-GPU serve). | PROMPT_CACHE_TAIL.md |
| `--session-min-free-mib` | engine flag | 4096 | Disk space reserve a session-file save requires beyond the new file (0 = no check). | DETAILS.md, generate.cpp |
| `--session-save-reclaim` | engine flag | off | Lets a SAVE that fails its RAM preflight first release reconstructible host caches. | DETAILS.md |
| `--slot-save-path` | engine flag | unset (save/restore off) | Directory for session files enabling POST /slots/0?action=save/restore (also slot_save_path in the config). | DETAILS.md |
| `--tail-role-token` | engine flag | -1 (off) | Role token of a trailing short turn left out of the conversation checkpoint (effort_position end). | generate.cpp |
| `--turn-token` | engine flag | model default | Token id marking the start of each new assistant turn for checkpoints. | DETAILS.md, generate.cpp |
| `filename` | API request field | - | Session file name (plain name inside the slot-save dir) for slots save/restore. | DETAILS.md |
| `KV` | env switch | int8 | Docker: KV precision (int8, q4_0, k8v4). | INSTALL.md |
| `KV_STREAMING` | env switch | auto | Docker: passes setup's --kv-streaming (on/off/auto). | INSTALL.md |
| `pin=N` | engine flag | none | Engine key on the GEN line that pins the checkpoint at the shared-prefix boundary (what strata_prefix sets). | RESEARCH_RUNS.md |
| `POST /slots/0?action=save or restore` | API endpoint | - | Saves or restores the conversation to a session file. | DETAILS.md |
| `slot_save_path` | run-config key | unset | Same as --slot-save-path, relative to the config's cwd. | DETAILS.md |
| `STRATA_CACHE_MESSAGE_BOUNDARY` | env switch | off | 1 saves a checkpoint at the previous turn boundary (prefix at least 8192 tokens, tail at most 1024) so an edited last message reuses history. | MESSAGE_BOUNDARY_CACHE.md |
| `strata_checkpoint` | API request field | true | false makes a one-shot request save no checkpoints and keep no session. | DETAILS.md |
| `STRATA_KV_GROW` | env switch | off | 1 equals --kv-grow. | DETAILS.md |
| `STRATA_KV_HOST_DMA` | env switch | off | 1 stops GPU kernels writing the RAM copy of the K/V during a window (troubleshooting hang). | TROUBLESHOOTING.md |
| `STRATA_KV_PREFETCH` | env switch | off | 1 uploads the next attention layer's streamed KV on a separate stream while the current layer computes. | KV_PREFETCH.md |
| `STRATA_KV_ROT` | env switch | - | KV rotation setting that is part of the session-file config fingerprint. | DETAILS.md |
| `STRATA_MEMORY_LIMIT_MIB` | env switch | 0 | Same as --memory-limit-mib (the flag wins). | DETAILS.md |
| `strata_prefix` | API request field | none | Marks where a shared document ends ({messages:N}, {message,chars} or {tokens}) so the boundary checkpoint is pinned. | DETAILS.md |
| `STRATA_SESSION_BUFFERED` | env switch | off | 1 forces buffered instead of O_DIRECT I/O for session files on Linux. | DETAILS.md |

## 6. Speculative decoding (MTP, prompt lookup, draft policy, verify window, tail skip)

| Name | Kind | Default | What it does | Source file |
|---|---|---|---|---|
| `--coupled-draft` | engine flag | off | Same as STRATA_SPEC_COUPLED=1 (drafts sampled with the request's own settings and random number). | DETAILS.md |
| `--draft-vocab` | engine flag | CJK-inclusive subset (0.1.27+) | Chooses the MTP draft head's token subset (en, cyrillic, fr, or the default with all CJK tokens); setup takes it too. | DETAILS.md |
| `--lookup-chain` | engine flag | 0 (off) | Adds up to K prompt-lookup drafts that continue the MTP's drafts (window up to 8). | generate.cpp |
| `--lookup-chain-min` | engine flag | 3 | Shortest context match --lookup-chain extends. | DETAILS.md |
| `--mtp` | engine flag | on via setup | Runs MTP speculative decoding with the draft layer; without it drafts come from the prompt-lookup drafter and the draft VRAM goes to the expert cache. | DETAILS.md, NVFP4.md, generate.cpp |
| `--mtp-draft-vocab` | engine flag | <mtp>/draft_vocab.bin | Uses a token subset file of your own for the draft head. | DETAILS.md |
| `--mtp-hnorm` | engine flag | default | stream uses one norm per stream as llama.cpp's MTP graph. | DETAILS.md |
| `--mtp-max-t` | engine flag | 0 (= --spec) | Caps the MTP's windows at M tokens; longer windows come from suffixes. | generate.cpp |
| `--mtp-q4` | engine flag | off | all/proj/head: the draft layer's projections and/or draft head as 4-bit copies. | generate.cpp |
| `--no-coupled-draft` | engine flag | - | Disables coupled draft sampling (propose argmax drafts). | generate.cpp |
| `--no-spec-split` | engine flag | - | Turns the split verify window off. | generate.cpp |
| `--spec` | engine flag | 0 (setup writes 4) | The MTP drafter's verify window: tokens proposed per check. | generate.cpp |
| `--spec-split` | engine flag | off | Opt-in split verify window (the overlap study: exact, ~7% slower); --no-spec-split turns it off. | generate.cpp |
| `STRATA_ROUTE_PRIOR` | env switch | off | Opt-in route prior (adds speed, doubles the KL). | DETAILS.md |
| `STRATA_ROUTE_RESIDENT` | env switch | off | Opt-in route-resident switch. | DETAILS.md |
| `STRATA_ROUTE_RESIDENT_MTP` | env switch | off | Opt-in route-resident switch for the MTP path (not faster than STRATA_ROUTE_RESIDENT). | DETAILS.md |
| `STRATA_SPEC_COUPLED` | env switch | off | 1 lets the draft layer sample its guesses with the target's own chain and random draw for sampled requests. | DETAILS.md |
| `STRATA_SPEC_DEPTH` | env switch | off | 1 prints draft acceptance by depth per request. | DETAILS.md |
| `STRATA_SPEC_GUMBEL` | env switch | off | 1 picks tokens by Gumbel-max with noise keyed by seed/position/token so draft and target agree on shared tokens. | DETAILS.md |
| `STRATA_SPEC_MIN_TEMP` | env switch | 0 | Limits sampled drafting to requests at or above this temperature. | DETAILS.md |
| `STRATA_SPEC_PROB` | env switch | off | 1 enables speculative rejection sampling (accept with min(1,p/q), resample from the leftover). | DETAILS.md |
| `STRATA_TAIL_SKIP_MAX_CACHED` | env switch | 90 (60 on AMD layer split) | Percent of experts in the GPU cache at or above which the route tail skip is not armed (101 = never). | DETAILS.md |

## 7. Sampling, reasoning and response behaviour

| Name | Kind | Default | What it does | Source file |
|---|---|---|---|---|
| `--eos-ids` | engine flag | 248044,248046 | End-of-turn token ids used by --stop-eos. | generate.cpp |
| `--fit-max-tokens` | server option | off | Same as fit_max_tokens. | DETAILS.md |
| `--greedy` | engine flag | on | Argmax decoding (the default). | generate.cpp |
| `--reasoning-effort` | engine flag (moved) | - | Moved out of args into the config's reasoning_effort key with a note at startup. | DETAILS.md |
| `--seed` | engine flag | unset | Enables sampling with this Philox seed. | generate.cpp |
| `--stop-eos` | engine flag | off | Stops when the model emits an end-of-turn token. | generate.cpp |
| `--temperature` | engine flag | - | One-shot sampling temperature. | generate.cpp |
| `--top-k` | engine flag | - | One-shot sampling top-k. | generate.cpp |
| `--top-p` | engine flag | - | One-shot sampling top-p. | generate.cpp |
| `/think` | chat.py command | model default | chat.py command setting the thinking level (for example /think low). | DETAILS.md |
| `anthropic_thinking` | run-config key | model | on_request renders an Anthropic request that does not ask for thinking without thinking. | DETAILS.md |
| `chat_template_kwargs` | API request field | - | Template variables such as enable_thinking. | server.py |
| `client_metadata` | API request field | - | Codex's turn metadata (session_id, thread_id, request_kind) used by thread-title and compaction options. | responses.py |
| `codex_compaction_cache` | run-config key | false | Renders Codex's compaction request with the previous prompt's tools so the cache is reused. | DETAILS.md |
| `effort_position` | run-config key | start | end renders every prompt start as the default effort's and puts low/medium effort in a short system turn before the answer, keeping the cache across effort changes. | DETAILS.md |
| `env_to_user` | run-config key | false | Moves opencode's <env> block to the front of the first user message so the system turn is session-independent. | DETAILS.md |
| `fit_max_tokens` | run-config key | false | Shortens a max_tokens that would run past the context instead of answering 400 (--fit-max-tokens on serve/server.py). | DETAILS.md |
| `frequency_penalty` | API request field | none | Frequency penalty (also in the sampling block). | DETAILS.md |
| `include` | API request field | - | Adds an opaque encrypted_content string to reasoning items on /v1/responses. | DETAILS.md |
| `instructions` | API request field | - | System message of a Responses API request. | responses.py |
| `logit_bias` | API request field | none | Request-scoped token biases (-100 or false excludes a token); CUDA/HIP engines without continuous batching. | DETAILS.md, LOGIT_BIAS.md |
| `logprobs / top_logprobs` | API request field | off | Token log-probabilities (Intel SYCL server: logprobs true, top_logprobs 0-20). | INTEL.md |
| `max_output_tokens` | API request field | - | Responses API output cap (thinking included); running out ends the response incomplete. | DETAILS.md |
| `max_tokens` | API request field | unlimited (rest of context) | Output token cap (max_completion_tokens also accepted); 400 if it overruns the context unless fit_max_tokens. | server.py |
| `min_p` | API request field | none | Min-p honoured per request. | DETAILS.md |
| `output_config.effort` | API request field | model default (high) | Anthropic-style low/medium/high effort. | DETAILS.md |
| `parallel_tool_calls` | API request field | - | Accepted on Responses requests. | responses.py |
| `penalty_last_n` | API request field | 64 when any penalty is set | How many recent tokens the penalties count over; repeat_last_n also works. | DETAILS.md |
| `presence_penalty` | API request field | none | Presence penalty (also in the sampling block). | DETAILS.md |
| `previous_response_id / conversation / background` | API request field | refused | Stateful Responses API fields that are refused on /v1/responses. | DETAILS.md |
| `reasoning_budget_tokens` | API request field / run-config key | 0 (no cap) | Caps the thinking at N tokens, then ends it with a wrap-up line and </think>. | DETAILS.md |
| `reasoning_close_retry` | run-config key | false | Closes the thinking once and continues when a reply ended inside its thinking with no answer or tool call. | DETAILS.md |
| `reasoning_effort` | API request field / run-config key | model default (high) | none/low/medium/high thinking level (also reasoning.effort or chat_template_kwargs.enable_thinking). | DETAILS.md |
| `reasoning_loop_recovery` | run-config key | false | false/stop/recover: ends or recovers a reply whose reasoning repeats whole passages. | DETAILS.md |
| `repeat_stop_tokens` | run-config key | 256 | Ends a reply (finish_reason length) that repeats the same token N times in a row; 0 turns it off. | DETAILS.md |
| `repetition_penalty` | API request field | none | Repetition penalty; llama.cpp's repeat_penalty also works. | DETAILS.md |
| `response_format` | API request field | text | json_object or json_schema (schema prompting then server validation, 502 structured_output_failed on failure). | DETAILS.md |
| `return_progress` | API request field | false | Puts prompt_progress (total, cache, processed, time_ms) on the stream as llama.cpp does. | DETAILS.md |
| `sampling` | run-config key | none (greedy) | Block of default sampling keys (temperature, top_p, top_k, min_p, penalties) for requests that omit them. | DETAILS.md |
| `seed` | API request field | none | Per-request sampling seed. | DETAILS.md |
| `stop / stop_sequences` | API request field | none | Up to 4 stop strings; the answer ends before the first (finish_reason stop). | DETAILS.md |
| `STRATA_KEEP_EMPTY_TURNS` | env switch | off | Keeps empty chat turns (see TROUBLESHOOTING.md). | DETAILS.md |
| `STRATA_TOPK_STREAM` | env switch | on | 0 disables streamed top-k (see TROUBLESHOOTING.md). | DETAILS.md |
| `stream` | API request field | false | Streams thinking, answer and tool calls as they are made, with keep-alives during long prompts. | DETAILS.md |
| `system / messages / tools` | API request field | - | Standard OpenAI/Anthropic conversation, system prompt and tool declarations. | server.py |
| `temperature` | API request field | 0 (greedy) unless sampling block | Sampling temperature honoured per request. | DETAILS.md |
| `text.format` | API request field | - | Responses API json_schema / json_object response format (checked, not constrained). | DETAILS.md |
| `thinking` | API request field | model default | thinking.type disabled/enabled with budget_tokens (under 2K = low, under 8K = medium, more = high). | DETAILS.md |
| `tool_call_recovery` | run-config key | false | Reads tool calls written as <parameter=NAME>, JSON inside <tool_call>, bare <function=> blocks or a second call in one <tool_call>. | DETAILS.md |
| `tool_choice` | API request field | auto | none hides the tools; anything else lets the model choose. | DETAILS.md |
| `top_k` | API request field | none (max 64) | Top-k honoured per request; 0 or above 64 uses 64. | DETAILS.md |
| `top_p` | API request field | none | Nucleus sampling honoured per request. | DETAILS.md |

## 8. Batching, parallel slots and replicas

| Name | Kind | Default | What it does | Source file |
|---|---|---|---|---|
| `--adapt-min-gain` | engine flag | 1.5 | Bar a swap must clear (routing count of incoming vs outgoing expert) so fewer experts move per round. | BATCHING.md |
| `--batch` | engine flag | 1 (one at a time) | Up to N conversations get batch slots decoded together (also --slots N, config parallel). | BATCHING.md |
| `--batch-mtp` | engine flag | off | Batch MTP on a layer split; pays only when experts do not all fit in VRAM. | DETAILS.md |
| `--context` | setup option | recommended for VRAM | Context size in tokens (8K to 256K+). | DETAILS.md, setup.py |
| `--parallel` | setup option | recommended only when experts mostly fit VRAM | Sets the config's parallel batch-slot count in setup. | BATCHING.md |
| `--replicas` | setup option | off | Runs N independent engines each on an equal group of the chosen cards, requests spread over them. | setup.py |
| `--slots` | engine flag | 1 | Alias of --batch N. | BATCHING.md |
| `CONTEXT` | env switch | recommended | Docker: context length. | INSTALL.md |
| `parallel` | run-config key | 1 | Number of batch slots decoded together (opt-in; maps to --batch). | DETAILS.md |
| `replica_cpus` | run-config key | true (Linux) | false turns off giving each replica disjoint physical CPU cores. | replicas.py |
| `replica_load_tokens` | run-config key | 3000 | Routing cost in prompt tokens of one request already running on a replica. | server.py |
| `replicas` | run-config key | off | Number, or list of {"gpus": [...]}, of data-parallel engines each on its own card group. | replicas.py |
| `STRATA_BATCH_DECODE_SHARE` | env switch | 0.5 | How long slots decode between a prompt's chunks relative to the chunk time. | BATCHING.md |
| `STRATA_BATCH_MTP` | env switch | off | 1 equals --batch-mtp (each batch slot verifies one MTP proposal per window). | BATCHING.md |
| `STRATA_BATCH_MTP_SERIAL` | env switch | off | 1 keeps --batch-mtp with serial windows instead of the pipelined default on a layer split. | BATCHING.md |
| `STRATA_PARALLEL_SOLO` | env switch | on | 0 stops a request left alone in a slot from returning to the solo path. | BATCHING.md |
| `STRATA_REPLICA_START` | env switch | parallel | serial starts the replica engines one after another. | server.py |

## 9. Multi-GPU (layer split, helper and peer GPUs, pipeline)

| Name | Kind | Default | What it does | Source file |
|---|---|---|---|---|
| `--batch-groups` | engine flag | auto on a layer split | Splits the slots into G groups pipelined through the GPUs (G must divide N; 1 = one window, auto = one group per stage). | BATCHING.md |
| `--cuda` | setup option | auto | Selects the CUDA engine build (--cuda 12 for the experimental Pascal/Volta engine). | MULTI_GPU.md |
| `--gpu` | setup option / engine flag / server option | GPU with most VRAM | Picks one GPU (numbered as nvidia-smi). | DETAILS.md, generate.cpp, server.py |
| `--gpus` | engine flag | one GPU | Shares the model across several GPUs (for example 0,1). | DETAILS.md |
| `--gpus all` | setup option | - | Uses every card that can share the model. | MULTI_GPU.md |
| `--layer-split` | engine flag | off | Splits the layers across GPUs at layer N. | DETAILS.md |
| `--layer-split auto` | engine flag | auto | auto places layers by each card's free VRAM and profiled expert coverage, or give the first layer of each later card (18 or 16,32). | MULTI_GPU.md |
| `--peer-adapt-swaps` | engine flag | -1 (primary's) | Swaps per adaptive round on the peer. | SECOND_GPU.md |
| `--peer-device` | engine flag | off | Puts a second adaptive expert cache on CUDA device N that computes its experts' rows over P2P/NVLink. | SECOND_GPU.md |
| `--peer-prefill-rows` | engine flag | -1 (half of chunk x top-k) | Share of each prompt chunk's rows the peer computes (0 keeps them on the primary). | SECOND_GPU.md |
| `--peer-reserve-mib` | engine flag | 600 | MiB left free on the peer card. | SECOND_GPU.md |
| `--peer-slots` | engine flag | 0 (all that fit) | Caps the peer tier at N experts. | SECOND_GPU.md |
| `--pipeline-windows` | engine flag | 2 on AMD gfx12 splits, 0 on NVIDIA | 2 lets the first card start the next verify window while the last card verifies this one (0 off, 1 only short reads). | MULTI_GPU.md |
| `--remote-expert-opt` | engine flag | off | Optimises helper-GPU expert caches (inert on a plain layer split). | MULTI_GPU.md |
| `--short-read` | engine flag | 64 | Number of tail prompt tokens read through the verify windows. | MULTI_GPU.md, generate.cpp |
| `--split-device` | engine flag | next visible devices | Devices of the later stages (--layer-split K --split-device 0 runs both stages on one card). | MULTI_GPU.md |
| `--split-skip-if-fits` | engine flag | off | Same as split_skip_if_fits with --layer-split auto. | MULTI_GPU.md |
| `--suffix-draft` | engine flag | on | Drafts from copied earlier context; 0 turns it off. | MULTI_GPU.md, generate.cpp |
| `--vram-reserve-later-mib` | engine flag | --vram-reserve-mib value | VRAM reserve for the later cards of a split (for example the one driving the monitors). | MULTI_GPU.md |
| `CUDA_VISIBLE_DEVICES` | env switch | all | Selects NVIDIA cards (the engine sets it from --gpu). | generate.cpp |
| `gpu` | run-config key | one GPU (most VRAM) | List of GPU indexes the engine uses (for example [0, 2]); first is the main card. | MULTI_GPU.md |
| `GPU` | env switch | all | Docker: pin one card. | INSTALL.md |
| `gpu_order` | run-config key | auto order (faster card last) | as_given keeps the written card order under layer_split auto. | MULTI_GPU.md |
| `GPUS` | env switch | all usable | Docker: card list (for example 0,2). | INSTALL.md |
| `HIP_VISIBLE_DEVICES` | env switch | all | Selects AMD cards (also ROCR_VISIBLE_DEVICES). | AMD_HIP.md |
| `layer_split` | run-config key | unset | Layer ranges per GPU (for example "24" or "12,24,36"); engine --layer-split. | BATCHING.md |
| `LAYER_SPLIT` | env switch | auto | Docker: where the later card's layers start. | INSTALL.md |
| `ONEAPI_DEVICE_SELECTOR` | env switch | level_zero:0 | Selects Intel devices (level_zero:* for two cards). | INTEL_ARC.md |
| `ROCR_VISIBLE_DEVICES` | env switch | all | Selects AMD cards for the ROCm runtime. | AMD_HIP.md |
| `split_skip_if_fits` | run-config key | false | Runs on the first card alone when it holds every profiled expert plus KV, draft layer and reserve (--split-skip-if-fits). | MULTI_GPU.md |
| `STRATA_MTP_BATCH_RING` | env switch | on | 0 sends a ring draft K/V back to the drafter's old pass. | MULTI_GPU.md |
| `STRATA_MTP_STAGE0` | env switch | on with pipeline | 0 keeps the draft layer on the last card. | MULTI_GPU.md |
| `STRATA_PIPELINE_AUTO` | env switch | on | 0 turns the default pipeline-windows off. | MULTI_GPU.md |
| `STRATA_PREFILL_HELP` | env switch | off | 1 lets the idle card compute a share of the streamed experts for one-chunk prompts on a split. | MULTI_GPU.md |
| `STRATA_PREFILL_HELP_FRAC` | env switch | auto | Fixes the share of experts the idle card helps with. | MULTI_GPU.md |
| `STRATA_SPLIT_CHUNK16K` | env switch | off (gfx12 split) | 1 keeps 16384-token auto chunks on an AMD gfx12 split instead of 8192. | MULTI_GPU.md |
| `STRATA_SPLIT_HEADROOM` | env switch | on | 0 keeps the old Intel layer-split pick. | INTEL_ARC.md |
| `STRATA_SPLIT_HEADROOM_TOL` | env switch | - | Tolerance of the predicted-time layer-split pick on Intel. | INTEL_ARC.md |
| `STRATA_SPLIT_MTP_BATCH` | env switch | off | 1 fills the draft layer's prompt K/V in batches on the last card of a split. | MULTI_GPU.md |
| `STRATA_SPLIT_OWN` | env switch | off | 1 gives every card its own prompt buffers (auto: only where buffers are at most 12% of VRAM). | MULTI_GPU.md |
| `STRATA_SPLIT_RING` | env switch | 96 slots when 75% of pairs cached | Number of streamed ring slots for the prompt path on a split (0 = pinned-share rule). | MULTI_GPU.md |
| `STRATA_STAGE_TRIM` | env switch | off | 1 equals --trim-stage-weights with an explicit layer split (each GPU loads only its layers' dense weights). | BATCHING.md |

## 10. Quantization, kernels, models and hardware-specific switches

| Name | Kind | Default | What it does | Source file |
|---|---|---|---|---|
| `--backend` | setup option | cuda (hip on AMD-only PCs) | cuda (NVIDIA), hip (AMD) or sycl (Intel Arc, experimental). | setup.py |
| `--build` | setup option | off | Compiles the engine instead of using the ready-made one. | setup.py |
| `--calibrate` | setup option | off | Measures and tunes pcie-frac, spec-min-p, pool-workers and adapt settings for this PC (15-30 min). | DETAILS.md |
| `--compat-bf16` | pack tool option (tools/iq_pack.py) | off | Expands the small projections an ordinary GGUF quantized (hyper-connection, attention, PLE key) to BF16 so Strata's kernels can read them (Orca packs). | ORCA.md |
| `--control-vector` | engine flag | - | Control vector GGUF at scale 1 (see --control-vector-scaled). | generate.cpp |
| `--control-vector-layer-range` | engine flag | unset | First and last layer the control vector steers. | DETAILS.md |
| `--control-vector-scaled` | engine flag | unset | Loads a control vector GGUF with a scale (file:scale). | DETAILS.md |
| `--cvec-dir` | engine flag | - | per-layer control-vector direction layout. | DETAILS.md |
| `--cvec-mode` | engine flag | add | add (additive) or project (remove one direction) control-vector mode. | DETAILS.md |
| `--diffusion-fa` | image encoder option | on | stable-diffusion.cpp flash attention used for Qwen-Image. | IMAGES.md |
| `--embd-gguf` | engine flag | --native's | Takes the token embedding from this GGUF (BF16 mapped host memory, no VRAM). | generate.cpp |
| `--experimental-speed-projection` | setup option | off | on/off/path of a control-vector GGUF: turns on the experimental refusal-direction projection. | DETAILS.md |
| `--experts-bin` | pack tool option (tools/iq_pack.py) | off | Also writes the pack's experts.bin (needed for --mmap-experts on AMD / Orca packs). | AMD_HIP.md |
| `--flash-attn` | vision encoder option | auto (off on AVX-512 CPU builds) | Overrides strata-vision's CPU flash attention on/off/auto. | DETAILS.md |
| `--image-backend` | setup option | auto | Image generation backend: cuda, hip, vulkan or cpu. | setup.py |
| `--image-gpu-policy` | setup option | auto | What the text model does during an image job: auto, keep, shrink or unload. | setup.py |
| `--image-native` | setup option | - | on/off for the native image path (preview). | setup.py |
| `--image-quant` | setup option | by GPU memory | Qwen-Image denoiser quant Q4_K_M, Q6_K or Q8_0. | setup.py |
| `--images` | setup option | no | qwen-image installs image generation (preview, hidden from --help); no removes it. | setup.py |
| `--max-context` | engine flag | chosen context | Maximum context of the engine (part of the session fingerprint). | DETAILS.md, generate.cpp |
| `--min-tokens` | vision encoder option | - | strata-vision minimum image tokens (set by vision.min_tokens). | DETAILS.md |
| `--mtp-window` | engine flag | - | MTP verify window size (part of the session fingerprint). | DETAILS.md |
| `--n-gpu-layers` | llama.cpp option (Intel doc) | - | Whole model on the card (999) in the llama.cpp comparison runs. | INTEL.md |
| `--native` | engine flag | set by setup for native packs | Path to the native GGUF the engine reads dense weights from (with --pack). | NVFP4.md, generate.cpp |
| `--native-dense-gguf` | engine flag | - | GGUF used as the dense-weight source. | NVFP4.md, generate.cpp |
| `--native-head-gguf` | engine flag | - | Native Q5_K head from model shard 1; requires --stream-token. | generate.cpp |
| `--no-ple` | engine flag | off | Diagnostic ablation of the PLE layer. | generate.cpp |
| `--override-tensor` | llama.cpp option (Intel doc) | - | Keeps per_layer_token_embd in host RAM in the llama.cpp comparison runs. | INTEL.md |
| `--pack` | engine flag | - | Model pack directory (experts.bin, dense, embeddings). | NVFP4.md |
| `--ple-gguf` | engine flag | - | GGUF shard with the per-layer token embedding (PLE) table. | NVFP4.md |
| `--ple-inflight` | engine flag | 256 | Outstanding SSD reads for the n-gram table. | generate.cpp |
| `--ple-io` | engine flag | direct | How the n-gram (PLE) table is read: direct, ram or mmap. | DETAILS.md |
| `--ple-row-cache` | engine flag | 1048576 | Bounded cache of fetched PLE rows (90 B each; 0 = off). | generate.cpp |
| `--prebuilt` | setup option | PREBUILT_URL (env STRATA_PREBUILT_URL) | Where the ready-made engine comes from (URL folder or local folder). | setup.py |
| `--rope-freq-base` | engine flag | model's | Raw ggml rope base frequency. | DETAILS.md |
| `--rope-freq-scale` | engine flag | model's | Raw ggml rope frequency scale. | DETAILS.md |
| `--rope-scale` | engine flag | derived from context | Rope scaling factor (final context / 262,144, at least 1). | DETAILS.md |
| `--rope-scaling` | engine flag | none | Rope scaling type none/linear/yarn for contexts past 262,144 (setup picks yarn). | DETAILS.md |
| `--vae-tiling` | image encoder option | on for Vulkan | stable-diffusion.cpp VAE tiling setup adds on Vulkan (with --vae-tile-overlap 0.25). | IMAGES.md |
| `--vision` | setup option / engine flag | asked | yes/no: read pictures with the image encoder. | DETAILS.md, generate.cpp |
| `--vision-tokens` | setup option | 1024 GPU / 300 CPU | Most tokens one picture becomes (vision.max_tokens). | DETAILS.md |
| `--yarn-attn-factor` | engine flag | model's | YaRN attention magnitude factor. | DETAILS.md |
| `--yarn-beta-fast` | engine flag | 32 | YaRN high-correction dimension. | DETAILS.md |
| `--yarn-beta-slow` | engine flag | 1 | YaRN low-correction dimension. | DETAILS.md |
| `--yarn-ext-factor` | engine flag | model's | YaRN extrapolation mix factor. | DETAILS.md |
| `--yarn-orig-ctx` | engine flag | 262144 | Original context for YaRN. | DETAILS.md |
| `/image` | chat.py command | - | chat.py command attaching a picture to the next question. | DETAILS.md |
| `BUILD_VISION` | build option | 1 | Docker build-arg: 0 skips the image encoder. | INSTALL.md |
| `CC / CXX / CUDAHOSTCXX` | env switch | detected | Compilers setup passes to CMake (for example CXX=g++-14). | TROUBLESHOOTING.md |
| `CUDA_ARCHITECTURES` | build option | 86;89;120;80 | Docker build-arg: CUDA architectures to compile for. | INSTALL.md |
| `experimental_speed_projection` | API request field | true when loaded | false turns the loaded control vector off for that request (also in sampling block, Sampling drawer). | DETAILS.md |
| `GPU_PINNED_MIN_XFER_SIZE` | env switch | - | 1048576 in the config's env makes HIP pin the pageable source pages of large copies (fixes mmap-experts stalls on RX 6800). | AMD_HIP.md |
| `HF_ENDPOINT` | env switch | huggingface.co | Hugging Face mirror setup downloads from (for example https://hf-mirror.com). | INSTALL.md |
| `HSA_OVERRIDE_GFX_VERSION` | env switch | unset | Overrides the GFX version ROCm reports (9.0.6 for gfx906 libraries). | OLDER_GPUS.md |
| `HSA_USERPTR_FOR_PAGED_MEM` | env switch | 1 (ROCm default) | 0 in the config's env avoids KFD userptr queue suspension timeouts on AMD Linux. | AMD_HIP.md |
| `IGC_EnableDPEmulation` | env switch | - | Intel compiler double-precision emulation key (passed to the engine). | INTEL_ARC.md |
| `STRATA_ARENA_ALIAS_CHECK` | env switch | on (Intel SYCL) | 0 skips the arena alias check on Intel GPUs. | INTEL.md |
| `STRATA_ARGMAX_MULTI` | env switch | on (sm_90+) | 0 turns off the cluster greedy-argmax decode kernel. | DETAILS.md |
| `STRATA_ATTN_LANECELL` | env switch | off | One-cell-per-thread attention scores kernel (Strix Halo / HIP decode layout switch). | STRIX_HALO.md |
| `STRATA_ATTN_PRE75` | env switch | on (V100, gfx103x) | 0 runs the standard attention kernel instead of the pre-sm_75 / RDNA2 shuffle-light kernel (#540). | NVIDIA_V100.md |
| `STRATA_BF16_TC` | env switch | off on Turing, on on V100 | 1/0 runs the BF16 projections on the FP16 tensor cores instead of cuBLAS BF16. | OLDER_GPUS.md |
| `STRATA_BUILD_TESTS` | build option | OFF | CMake switch building the parity and test programs. | DETAILS.md |
| `STRATA_COPY_ENGINE` | env switch | on for Battlemage | 1 forces the Intel copy-engine path elsewhere, 0 turns it off. | INTEL.md |
| `STRATA_CUDA` | env switch | auto | 12 selects the CUDA 12 engine (same as --cuda 12). | OLDER_GPUS.md |
| `STRATA_CVEC_FUSE` | env switch | off (HIP gfx1151) | Fuses a steered layer's write, control vector and next norm in one pass. | STRIX_HALO.md |
| `STRATA_DENSE_F32` | env switch | off | 1 runs the large dense GEMMs as cublasSgemm on FP32 copies (Pascal, opt-in). | OLDER_GPUS.md |
| `STRATA_DENSE_MMQ` | env switch | off (HIP) | HIP dense MMQ switch (see AMD_HIP.md). | DETAILS.md |
| `STRATA_DEQUANT_WIDE` | env switch | on | 0 turns off the wide dequantization kernels for Q4_K/Q5_K/Q6_K/IQ4_XS. | OLDER_GPUS.md |
| `STRATA_DOWN_LANES` | env switch | 8 | Lanes per row of the SYCL port's down expert kernel (4/8/16/32). | INTEL.md |
| `STRATA_ENABLE_CUDA` | build option | ON for CUDA builds | CMake switch enabling the CUDA engine. | AMD_HIP.md |
| `STRATA_ENABLE_HIP` | build option | OFF | CMake switch enabling the HIP engine. | AMD_HIP.md |
| `STRATA_ENABLE_SYCL` | build option | OFF | CMake switch enabling the SYCL engine. | INTEL_ARC.md |
| `STRATA_EXP_MODE` | env switch | per card | Selects the VRAM expert decode kernel mode (0 = CUDA layout, 2 = previous AMD layout, 7/8 card-specific modes). | AMD_HIP.md |
| `STRATA_EXPERIMENTAL_GFX1103` | env switch | off | Treats the gfx1103 iGPU as usable (experimental). | setup.py |
| `STRATA_EXPERIMENTAL_SM60` | env switch | off | 1 lists pre-sm_75 cards as usable (also a CMake option for the CUDA 12 engine). | OLDER_GPUS.md |
| `STRATA_EXPERT_LANES` | env switch | 8 | Grouped-expert lane count (SYCL; null result). | A770_SPEED_SPEC.md |
| `STRATA_EXPERT_SPLIT` | env switch | off | 1 runs upstream's expert grid on the Intel SYCL port instead of the port's own. | INTEL.md |
| `STRATA_EXPERT_V2` | env switch | off | Grouped decode experts (IQ3_S / IQ4_NL) for HIP gfx1151. | STRIX_HALO.md |
| `STRATA_EXPERT_V2K` | env switch | off | 1 makes the K-quant experts use the CUDA layout bitwise (Unsloth files). | QUANTS.md |
| `STRATA_EXPERT_XMX` | env switch | off | Parked opt-in XMX expert matrix kernels on Intel (slower). | INTEL.md |
| `STRATA_FILL_SERIAL` | env switch | off | 1 uses the old serial GPU cache fill on Intel. | INTEL.md |
| `STRATA_FORCE_ISA` | env switch | auto | avx or sse forces an older CPU instruction level in the engine build/run. | INSTALL.md |
| `STRATA_GDN_CHUNKED` | env switch | 0 | 1 runs the DeltaNet prompt recurrence in 32-token chunks (needs 128+ SMs; =2 forces it on smaller cards). | DETAILS.md |
| `STRATA_GDN_CONVL2` | env switch | off | Prompt gated-delta-net convolution/norm fusion (HIP gfx1151). | STRIX_HALO.md |
| `STRATA_GDN_HEAD` | env switch | off | Prompt gated-delta-net recurrence variant with four lanes per column (HIP gfx1151). | STRIX_HALO.md |
| `STRATA_GDN_KEYHEAD` | env switch | off | 1 runs the DeltaNet recurrence per key head on Intel (8% slower prompts, bit-identical). | INTEL.md |
| `STRATA_GDN_NOY` | env switch | off | Prompt GDN variant (HIP gfx1151). | STRIX_HALO.md |
| `STRATA_GDN_PP` | env switch | off | 2 runs two columns per lane in the prompt GDN recurrence (HIP gfx1151). | STRIX_HALO.md |
| `STRATA_GDN_SPLIT` | env switch | off | GDN step over four blocks per head (HIP gfx1151). | STRIX_HALO.md |
| `STRATA_GDN_XE` | env switch | on (Alchemist) | 1/0 turns the Xe prompt GDN kernels on or off (any 16-lane Intel GPU). | INTEL.md |
| `STRATA_GFX1012_PORTABLE_DOT` | build option | OFF | Portable dot kernel for gfx1012 (RX 5500 XT). | OLDER_GPUS.md |
| `STRATA_GFX1151_DEFAULTS` | env switch | on | 0 turns off the whole table of gfx1151 (Strix Halo) defaults. | STRIX_HALO.md |
| `STRATA_GFX906_ATTN_QUERY_SWIZZLE` | env switch | off | 1 enables the gfx906 (MI50) attention query-swizzle variant. | AMD_HIP.md |
| `STRATA_GFX906_ATTN_REDUCE12` | env switch | off | 1 enables the gfx906 attention reduce variant. | AMD_HIP.md |
| `STRATA_GGML_DIR` | build option | pinned checkout | Points the build at a llama.cpp checkout for offline builds. | AMD_HIP.md |
| `STRATA_GPU_METAL` | build option | off | CMake switch guarding the Metal engine code (PC builds byte-identical without it). | MAC.md |
| `STRATA_GR_DOWN_DIRECT` | env switch | on | 0 restores the old direct down-projection path (Intel). | INTEL.md |
| `STRATA_GR_DOWN_MAX4` | env switch | off | 1 opt-in max-4 down-projection variant (Intel). | INTEL.md |
| `STRATA_GR_DOWN_SLICED` | env switch | on | 0 restores the direct down kernel instead of the sliced one. | INTEL.md |
| `STRATA_GR_FAST` | env switch | on (gfx906) | 0 uses the plain hyper-connection up kernel instead of the latency-hidden one. | AMD_HIP.md |
| `STRATA_GR_SPLIT` | env switch | off | 1 enables the gr_down_multi split along K (about 3% per window, different summation order). | AMD_HIP.md |
| `STRATA_GR_V3` | env switch | off | Opt-in gr v3 kernel on Intel (slower). | INTEL.md |
| `STRATA_GU_LANES` | env switch | 8 | Lanes per row of the SYCL port's gate/up expert kernel (4/8/16/32). | INTEL.md |
| `STRATA_HC_CHECK` | env switch | off | Enables the hyper-connection split-norm check on SYCL. | INTEL.md |
| `STRATA_HC_Q8` | env switch | on for Mac and AMD gfx12 | 1/0 reads the hyper-connection matrices from a Q8_0 copy instead of BF16 (0 gives the exact BF16 read). | MAC.md |
| `STRATA_HC_UPMIX` | env switch | off | Hyper-connection up projection with its epilogue (HIP gfx1151). | STRIX_HALO.md |
| `STRATA_HCD_EXACT` | env switch | off | Runs the hyper-connection down projection in a WMMA kernel reproducing hipBLASLt's summation order. | STRIX_HALO.md |
| `STRATA_HIP_ADAPT_KERNEL_COPY` | env switch | off (HIP) | HIP adaptive-tier kernel copy (see AMD_HIP.md). | DETAILS.md |
| `STRATA_HIP_ARCHS` | env switch | detected | Selects the HIP architectures setup builds for (for example gfx1031). | AMD_HIP.md |
| `STRATA_HIP_GFX906` | build option | OFF | Separate opt-in HIP build for gfx906 (MI50/MI60/Radeon VII). | AMD_HIP.md |
| `STRATA_HIP_PROMPT_F16` | env switch | off | 1 runs the 16-bit prompt GEMMs FP16 in and out on gfx103x. | AMD_HIP.md |
| `STRATA_HIP_ROUTER_OLD` | env switch | off | 1 runs the portable router kernel on HIP. | AMD_HIP.md |
| `STRATA_HIP_WMMA` | env switch | off (gfx12, int8 KV) | 1 runs the prompt QSA attention on RDNA4 matrix cores; any value silences the start hint. | AMD_HIP.md |
| `STRATA_HIPBLASLT_TUNING` | env switch | per-arch table by setup | Path of a hipBLASLt tuning table (tools/hip/<arch>-hipblaslt-<ver>.txt). | AMD_HIP.md |
| `STRATA_IQ3S_MT1` | env switch | off | 1 gives IQ3_S the multi-token kernel for a single token on Alder Lake+ CPUs without AVX-512 (faster there, changes a lone token's rounding). | DETAILS.md |
| `STRATA_IQ_MT_MIN` | env switch | off | 1 uses the multi-token IQ kernels for every expert group so greedy answers no longer depend on drafting. | DETAILS.md |
| `STRATA_ISA_FLOOR` | build option | - | avx or none builds the engine for CPUs without AVX2 (also env at setup). | INSTALL.md |
| `STRATA_KQ256` | env switch | off | 1 uses multi-token AVX2 kernels for Q4_K/Q5_1/Q8_0 experts (bit-exact, no faster). | UNSLOTH_Q4.md |
| `STRATA_LFUSE` | env switch | off | Fewer launches around the shared expert and KV append. | STRIX_HALO.md |
| `STRATA_METAL_ARENA_GB` | env switch | auto | Mac streaming: expert arena size in GB. | MAC.md |
| `STRATA_METAL_CONTIG` | env switch | on | 0 reads the model file even when the experts' copy is there (Mac streaming). | MAC.md |
| `STRATA_METAL_FUSE` | env switch | on | 0 turns off a Metal decode fusion (same tokens, slower). | MAC.md |
| `STRATA_METAL_IGNORE_REPACK` | env switch | off | 1 starts the engine although a model repack or setup preparation is running. | MAC.md |
| `STRATA_METAL_KEEP_FREE_GB` | env switch | auto | Mac streaming: memory left to macOS when the arena is sized. | MAC.md |
| `STRATA_METAL_KEEP_HOST_WEIGHTS` | env switch | off | 1 keeps the host code's own 2-3 GB copies of weights (Mac streaming). | MAC.md |
| `STRATA_METAL_LAZY_FOLD` | env switch | on | 0 turns off the second Metal decode fusion. | MAC.md |
| `STRATA_METAL_LEAVE_SHARE` | env switch | 0.4 (64 GB+ Macs) | Share of RAM left to macOS and its file cache when the footprint cap is set. | MAC.md |
| `STRATA_METAL_MTP_CHAIN` | env switch | on | 0 uses one GPU submission per draft step instead of one per round. | MAC.md |
| `STRATA_METAL_MTP_Q8` | env switch | on | 0 keeps the draft layer's matrices in BF16 instead of Q8_0. | MAC.md |
| `STRATA_METAL_PARTIAL` | env switch | off | 1 enables a decode variant that measured no faster. | MAC.md |
| `STRATA_METAL_ROUTE` | env switch | off | 1 enables a decode variant that measured no faster. | MAC.md |
| `STRATA_METAL_STREAM` | env switch | auto by size | 1 streams experts from the SSD even when the pack fits, 0 never. | MAC.md |
| `STRATA_METAL_WIRE_ARENA` | env switch | on | 0 leaves the filled arena pageable instead of wired with mlock (Macs 64 GB+). | MAC.md |
| `STRATA_MIRROR_MIB` | env switch | auto | MiB of experts mirrored in a pinned host mirror at start on Intel (0 = none). | INTEL.md |
| `STRATA_MMQ_KQ` | env switch | off | 1 multiplies Q4_K/Q5_K/Q5_1/Q6_K prompt experts with llama.cpp MMQ kernels. | UNSLOTH_Q4.md |
| `STRATA_MMQ_KQUANTS` | build option | off | Builds the K-quant MMQ instances (costs 1-3 expert slots per model). | UNSLOTH_Q4.md |
| `STRATA_MMVF_ROWS` | env switch | off | 4-row BF16 GEMV decode kernel. | STRIX_HALO.md |
| `STRATA_MMVQ_A2` | env switch | on (Intel) | 0 restores the old mmvq path. | INTEL.md |
| `STRATA_MMVQ_IL` | env switch | on (sm_80+) | 0 turns off the interleaved small-batch matvec path for decode/verify dense projections. | MMVQ_IL_TABLE.md |
| `STRATA_MMVQ_IL_ROWS` | env switch | built-in table | Overrides the rows-per-warp table as "type:ncols:r0,r1,r2,r3,r4;...". | MMVQ_IL_TABLE.md |
| `STRATA_MMVQ_SG` | env switch | - | SIMD16 builds of multi-column mmvq and wide Q6_K (tried, no gain). | INTEL.md |
| `STRATA_MMVQ_WAVE` | env switch | on (gfx906) | 0 uses the CUDA layout MMVQ instead of one wavefront per row. | AMD_HIP.md |
| `STRATA_MMVQ_WIDE_32` | env switch | on (Intel) | 0 turns off the wide 32 mmvq path. | INTEL.md |
| `STRATA_MMVQ_WIDE_K` | env switch | on (Intel) | 0 turns off the wide-K mmvq path. | INTEL.md |
| `STRATA_MTP_SIDE_STREAM` | env switch | off (on gfx103x) | 0/1 disables or creates the MTP drafter's side stream on HIP. | AMD_HIP.md |
| `STRATA_NATIVE_EXPERTS` | build option | ON | CMake switch compiling the native expert kernels. | ORCA_Q4_K_S.md |
| `STRATA_NO_AVXVNNI` | env switch | off | 1 turns off AVX-VNNI forms of the i-quant, IQ4_NL and Q2_0 rows. | DETAILS.md |
| `STRATA_NO_NVFP4_512` | env switch | off | 1 falls back from the AVX-512 NVFP4 CPU rows to ggml-cpu. | NVFP4.md |
| `STRATA_NVCC` | env switch | newest found | Path of the nvcc setup compiles with. | TROUBLESHOOTING.md |
| `STRATA_ORCA_Q4KS_MMQ` | build option | OFF | Compiles the Q5_0 GGML MMQ instance for Orca Q4_K_S. | ORCA_Q4_K_S.md |
| `STRATA_PA_FAST` | env switch | off | Prompt attention with single FP16 q and p (HIP gfx1151). | STRIX_HALO.md |
| `STRATA_PARTIAL_PIN` | env switch | off | 1 registers the hottest part of the RAM budget with the GPU driver so the GPU computes a share of misses over PCIe. | UNSLOTH_Q4.md |
| `STRATA_PARTIAL_PIN_GIB` | env switch | 24 | GiB registered by STRATA_PARTIAL_PIN. | UNSLOTH_Q4.md |
| `STRATA_PF_FUSED` | env switch | on (RTX 30+) | 0 keeps the previous prompt kernels (byte-identical to 0.1.35); 1 also runs the native IQ packs' fused kernels (opt-in; HIP gfx11, gfx1200/1201). | DETAILS.md |
| `STRATA_PF_FUSED_KQ` | env switch | off | 1 also runs fused prompt kernels for K-quant packs (gfx11). | QUANTS.md |
| `STRATA_PF_FUSED_NATIVE` | env switch | on | 0 keeps MMQ when the fused flag is set (HIP). | AMD_HIP.md |
| `STRATA_PF_GEMM` | env switch | off | 1 runs the prompt's FP16 projections on the 128x256 WMMA GEMM (HIP). | STRIX_HALO.md |
| `STRATA_PF_PAD` | env switch | off | Padded GEMM row strides (HIP gfx1151). | STRIX_HALO.md |
| `STRATA_PF_XMX` | env switch | off | 1/2 enables grouped XMX expert GEMM on Battlemage (slower). | INTEL.md |
| `STRATA_PLAN_PARALLEL` | env switch | on (Intel) | 0 restores the old serial plan path. | INTEL.md |
| `STRATA_PLE_AUTO_MMAP` | env switch | off | 1 brings back the 0.1.42 automatic switch to --ple-io mmap when direct reads measure slow. | DETAILS.md |
| `STRATA_PLE_BATCH` | env switch | off | Computes a verify window's PLE key/value projections at once. | STRIX_HALO.md |
| `STRATA_PREBUILT_URL` | env switch | release URL | Default location of the ready-made engine. | setup.py |
| `STRATA_PREFILL_NVFP4` | env switch | w4a8 | Prompt-path NVFP4 expert precision: w4a8 (default), w4a4 (Blackwell FP4 MMA), w4a4x2, fp16 (exact). | NVFP4.md |
| `STRATA_PREFILL_X2_MIN_K` | env switch | - | With w4a4x2, minimum K (for example 640) from which the down projection also uses x2. | NVFP4.md |
| `STRATA_PROMPT_ATTN_OLD` | env switch | off | 1 runs the decode attention kernel for prompts. | OLDER_GPUS.md |
| `STRATA_PROMPT_ATTN_XMX` | env switch | off | 1 runs prompt attention on Intel XMX matrix units. | INTEL.md |
| `STRATA_Q2_BITPLANE` | env switch | off | 1 uses a bit-plane row kernel for Q2_0 on AVX2-only CPUs. | DETAILS.md |
| `STRATA_Q6_PACKED` | env switch | off | Packed Q6_K weight decode layout (HIP gfx1151). | STRIX_HALO.md |
| `STRATA_Q6K_EXPERTS` | build option | on | Compiles Q6_K expert support (now default). | QUANTS.md |
| `STRATA_Q8_PACKED` | env switch | off | Packed Q8_0 weight decode layout (HIP gfx1151). | STRIX_HALO.md |
| `STRATA_QFUSE` | env switch | off | Activation images written by their producers (HIP gfx1151). | STRIX_HALO.md |
| `STRATA_QSA_CLUSTER` | env switch | on (sm_90+) | 0 turns off the thread-block-cluster decode block-selection kernels. | DETAILS.md |
| `STRATA_ROCM_INDEX` | env switch | detected | Overrides the ROCm package index setup installs from. | AMD_HIP.md |
| `STRATA_ROCM_VERSION` | env switch | detected | Overrides the ROCm version setup installs. | AMD_HIP.md |
| `STRATA_ROUTE_PER_TOKEN` | env switch | off | 1 routes per token instead of one multi-column projection on HIP. | AMD_HIP.md |
| `STRATA_ROUTE_TAIL_SKIP` | env switch | on (CUDA) | 0 disables skipping the tail of missed experts in a verify window. | DETAILS.md |
| `STRATA_SELECT_SIMT` | env switch | off | 1 uses the tiled FP32 QSA block scorer on the pre-sm_80 CUDA path. | OLDER_GPUS.md |
| `STRATA_SELECT_WMMA` | env switch | off (gfx12) | 1 runs the QSA block scorer on matrix cores. | AMD_HIP.md |
| `STRATA_SH_STREAM` | env switch | on CUDA; off on HIP except gfx1151 | 1/0 runs the shared expert on a second stream beside the routed experts (output identical). | DETAILS.md |
| `STRATA_SM61_TABLE` | env switch | on (sm_61) | 0 restores the kernels 0.1.43.1 ran on Pascal. | OLDER_GPUS.md |
| `STRATA_SM70_TABLE` | env switch | off | 1 enables the measured V100 decode kernel table. | NVIDIA_V100.md |
| `STRATA_SYCL_AOT` | build option | JIT | Compiles SYCL GPU code ahead of time (bmg-g31, bmg-g21). | INTEL_ARC.md |
| `STRATA_SYCL_BF16_GRF` | build option | 0 | Register file size mode for the SYCL BF16 kernels (0/128/256). | A770_SPEED_SPEC.md |
| `STRATA_SYCL_BIN` | env switch | build-sycl/sycl/strata | Path of the SYCL engine binary. | INTEL_ARC.md |
| `STRATA_SYCL_EXPERT_LANES` | build option | 8 | Lanes of the grouped SYCL expert kernel (1..32). | A770_SPEED_SPEC.md |
| `STRATA_SYCL_GEMM_STAGE` | env switch | off on Battlemage | 1 stages GEMM inputs for the A770 oneMKL bug. | INTEL.md |
| `STRATA_SYCL_PARITY` | build option | ON | OFF skips the SYCL kernel parity tests. | INTEL_ARC.md |
| `STRATA_SYCL_ROOT` | env switch | - | Path of the SYCL checkout/install root. | INTEL.md |
| `STRATA_SYCL_SAMPLER_FP64` | build option | 0 | 1 keeps double accumulation in the SYCL sampler. | INTEL.md |
| `STRATA_SYCL_SPIN_MAX` | build option | 2000000 | Reads before the SYCL doorbell spin gives up. | INTEL.md |
| `STRATA_TSUM` | env switch | off | Single-butterfly warp sums (HIP gfx1151). | STRIX_HALO.md |
| `STRATA_VCVARS` | env switch | auto | Path of the Visual Studio vcvars used for the build on Windows. | setup.py |
| `STRATA_VERIFY_DEVICE_PLAN` | env switch | off | 1 plans the verify window on the device (Intel SYCL). | INTEL.md |
| `STRATA_VERIFY_LEAN` | env switch | on | 0 keeps decode graph nodes nobody answers under STRATA_VERIFY_NO_HOST. | INTEL.md |
| `STRATA_VERIFY_NO_HOST` | env switch | SYCL default on Battlemage | Intel Arc: skips the host verify path (no adaptive tier); the profile is then ranked from device routing counts. | DETAILS.md |
| `STRATA_WARM_GRAPHS` | env switch | on | 0 stops capturing every window graph at load (Intel). | INTEL.md |
| `STRATA_WDDM_BUDGET` | env switch | on | 0 turns off the WDDM budget logic on HIP Windows. | AMD_HIP.md |
| `SYCL_CACHE_PERSISTENT` | env switch | 0 | 0 avoids the persistent JIT cache crash on Xe2. | INTEL_ARC.md |
| `VISION` | env switch | no | Docker: image encoder (gpu, cpu, no). | INSTALL.md |
| `vision.cuda_device` | run-config key | unset | Runs the GPU image encoder alone on that card (numbered like nvidia-smi). | DETAILS.md |
| `vision.elastic` | run-config key | false | Starts the GPU image encoder only while there are pictures (needs vram_elastic). | DETAILS.md |
| `vision.elastic_idle_s` | run-config key | 120 | Seconds without a picture before the on-demand encoder stops. | DETAILS.md |
| `vision.elastic_mib` | run-config key | 2048 | VRAM kept free for the on-demand image encoder. | DETAILS.md |
| `vision.max_tokens` | run-config key | 1024 GPU / 300 CPU | Most image tokens a picture becomes. | DETAILS.md |
| `vision.min_tokens` | run-config key | none | Minimum image tokens, a small picture is scaled up to it (encoder --min-tokens). | DETAILS.md |
| `vision.mmproj` | run-config key | BF16 mmproj | Another mmproj file of this model (for example Q8_0, recommended for CPU encoding). | DETAILS.md |
| `ZES_ENABLE_SYSMAN` | env switch | 1 | Enables Level Zero sysman for Intel telemetry. | INTEL_ARC.md |

## 11. Server, API, network, security, monitoring and setup

| Name | Kind | Default | What it does | Source file |
|---|---|---|---|---|
| `--api-key` | setup option / bench option | none | API key(s), comma-separated. | DETAILS.md, strata_bench.py |
| `--api-monitor` | server option | off | Same as api_monitor. | DETAILS.md |
| `--check` | setup option | off | Only checks this PC and exits. | setup.py |
| `--config` | server option / bench option | - | Path of the strata-<model>.json run config. | server.py, strata_bench.py |
| `--engine` | server option | mock | Engine kind: strata or the scripted mock engine. | server.py |
| `--help` | engine flag | - | Prints the engine's usage text. | generate.cpp |
| `--host` | setup option | 127.0.0.1 | Listen address, for example 0.0.0.0 for other devices. | DETAILS.md |
| `--mcp-config` | server option | none | Path of a claude_desktop_config.json-style file with an mcpServers block. | DETAILS.md |
| `--no-start` | setup option | off | Installs only, does not start the model. | setup.py |
| `--port` | setup option / server option | 8080 | Port the server listens on. | DETAILS.md, server.py |
| `--rollback-engine` | setup option | off | Puts back the engine an update replaced (kept in engine/.previous). | setup.py |
| `--root` | MCP server option | script folder | strata_mcp.py: Strata folder to manage. | MCP_SERVER.md |
| `--script` | server option | greeting | Mock engine's answer (repeatable). | server.py |
| `--serve` | engine flag | off (one-shot) | Keeps the engine resident and takes requests on stdin (what the server starts it with). | generate.cpp |
| `--setup` | setup option | off | Re-runs setup to install another model or change context/images. | DETAILS.md |
| `--skip-build` | setup option | - | Hidden: skip the build step. | setup.py |
| `--tokenizer` | server option | pack/full/tokenizer | Tokenizer directory. | server.py |
| `--update` | setup option | off | Updates engine, Python packages and model settings without starting (UPDATE.bat / update.sh). | setup.py |
| `--yes` | setup option | off | Runs setup/start without questions. | DETAILS.md |
| `allowed_hosts` | run-config key | none | Extra Host names the server answers (".example.com" suffix, ["*"] turns the DNS-rebinding check off). | DETAILS.md |
| `api_key` | run-config key | none | API key (string, comma-separated, or list); also --api-key and STRATA_API_KEY. | DETAILS.md |
| `API_KEY` | env switch | none | Docker: API key (set it before exposing the port). | INSTALL.md |
| `api_monitor` | run-config key | false | Keeps the last 100 requests' prompts and answers in memory for /api-monitor, /api/requests (also --api-monitor). | DETAILS.md |
| `arch` | run-config key | qwen4exp | Model architecture key used to pick the reported model name. | server.py |
| `args` | run-config key | written by setup | List of engine arguments; engine options added by hand go here. | server.py |
| `backend` | run-config key | cuda | cuda/hip/sycl backend (hip selects AMD ordinals and env). | server.py |
| `CLAUDE_CODE_EFFORT_LEVEL` | env switch | high (client side) | Claude Code client variable setting the thinking level sent as output_config.effort. | LLAMA_SWAP.md |
| `codex_thread_titles` | run-config key | false | Answers Codex's thread-title request without the engine as the user's line cut to 36 characters. | DETAILS.md |
| `CONFIG` | env switch | derived from MODEL | Docker: any config file path overriding strata-<model>.json. | INSTALL.md |
| `cors_origins` | run-config key | none (off) | Browser origins allowed to call /v1/* (["*"] for any, only with an API key). | DETAILS.md |
| `cwd` | run-config key | Strata folder | Working directory of the engine and base for relative paths. | server.py |
| `engine_silence_s` | run-config key | 300 | Seconds without engine output before the server ends the engine (0 = wait forever). | DETAILS.md |
| `env` | run-config key | {} | Block of environment variables set for the engine (for example STRATA_PIN_BY_NEED). | server.py |
| `exe` | run-config key | engine path | Engine executable the server starts. | server.py |
| `FAKE_MCP_LOG` | env switch | unset | Test: log file of the fake MCP server. | mcp_fake_server.py |
| `GET /api-monitor, /api/requests` | API endpoint | - | API request monitor page and JSON (id= for one request); needs api_monitor. | DETAILS.md |
| `GET /mcp` | API endpoint | - | The MCP servers, their state and tools. | DETAILS.md |
| `GET /metrics` | API endpoint | - | JSON for the Monitor tab, or Prometheus text with vLLM names when asked with Accept: text/plain or ?format=prometheus. | DETAILS.md |
| `GET /metrics/prometheus` | API endpoint | - | Request totals and running state under llama-server's metric names. | DETAILS.md |
| `GET /props` | API endpoint | - | Model properties: chat template, chat_template_caps, context limit, generation defaults, total_slots, is_sleeping (accepts ?model=). | DETAILS.md |
| `GET /status, GET /slots` | API endpoint | - | What the model is doing now; one slot entry per --batch slot with n_ctx and n_prompt_tokens. | DETAILS.md |
| `GET /v1/models, /models, /health` | API endpoint | - | Model list (only the loaded model, with context limit and modalities) and health. | DETAILS.md |
| `GET /v1/status, /api/health` | API endpoint | - | v1/status exposes loaded and auto_load; /api/health aliases /health. | DETAILS.md |
| `GET/POST /config` | API endpoint | - | The run config's Settings view (editable keys); only Strata's own page can save. | runconfig.py |
| `GET/POST /settings` | API endpoint | - | Shared Chat settings ("Use for other apps too"; keys such as reasoning_effort, temperature, top_p, top_k, seed, max_tokens). | server.py |
| `hip_ordinal` | run-config key | auto | HIP device ordinal used for AMD cards. | server.py |
| `host` | run-config key | 127.0.0.1 | Address the server listens on (setup --host). | DETAILS.md |
| `HOST` | env switch | 0.0.0.0 | Docker: listen address. | docker-entrypoint.sh |
| `images` | run-config key | absent | Section enabling POST /v1/images/generations (written by setup --images). | IMAGES.md |
| `images.gpu_policy` | run-config key | auto | What the text model does during an image job (auto/keep/shrink/unload). | IMAGES.md |
| `images.max_n` | run-config key | 4 | Most images per request. | IMAGES.md |
| `images.max_side` | run-config key | 2048 | Largest image side. | IMAGES.md |
| `images.reload` | run-config key | lazy | eager restarts the unloaded text model right after the image. | IMAGES.md |
| `lib_dirs` | run-config key | none | Extra library directories added to the engine's library path. | server.py |
| `log` | run-config key | strata-<model>.log | Engine log file. | server.py |
| `mcp.max_result_chars` | run-config key | 20000 | Longest MCP tool result passed to the model. | DETAILS.md |
| `mcp.max_rounds` | run-config key | 8 | Most MCP tool calls in a row per answer. | DETAILS.md |
| `mcp.timeout_s` | run-config key | 60 | Seconds an MCP tool call may take before the model gets an error result. | DETAILS.md |
| `mcp_servers` | run-config key | none | MCP servers (program or url) whose tools the chat page offers to the model (mcpServers also accepted). | DETAILS.md |
| `model_name` | run-config key | family model name | Name the server reports for the model. | server.py |
| `n` | API request field | 1 | Number of images (1-4) for /v1/images/generations. | IMAGES.md |
| `port` | run-config key | 8080 | Port the server listens on (also server --port, default 8095 for a bare server). | server.py |
| `PORT` | env switch | 8080 | Docker: server port. | docker-entrypoint.sh |
| `POST /v1/chat/completions` | API endpoint | - | OpenAI Chat Completions (stream and non-stream, tools). | DETAILS.md |
| `POST /v1/images/generations` | API endpoint | - | OpenAI-style image generation (prompt, size, n, response_format, seed, steps) when images are set up. | IMAGES.md |
| `POST /v1/messages` | API endpoint | - | Anthropic Messages (stream and non-stream, tools). | DETAILS.md |
| `POST /v1/messages/count_tokens` | API endpoint | - | Anthropic token counting for a request. | server.py |
| `POST /v1/responses` | API endpoint | - | OpenAI Responses API (stateless, stream and non-stream, tools) for Codex CLI. | DETAILS.md |
| `prevent_sleep` | run-config key | false | Keeps Windows from sleeping while a request runs or waits. | DETAILS.md |
| `REINSTALL` | env switch | off | Docker: 1 re-runs setup to change settings for a model already set up. | INSTALL.md |
| `size` | API request field | 1024x1024 | WIDTHxHEIGHT of a generated image (multiples of 32, 256-2048). | IMAGES.md |
| `steps` | API request field | 8 | Sampling steps of a generated image. | IMAGES.md |
| `STRATA_ALLOWED_HOSTS` | env switch | unset | Comma-separated allowed Host names. | DETAILS.md |
| `STRATA_API_KEY` | env switch | unset | API key for the server. | DETAILS.md |
| `strata_benchmark` | MCP tool | - | Strata MCP server tool: short speed test (port, max_tokens 16-512). | MCP_SERVER.md |
| `strata_connect_info` | MCP tool | - | Strata MCP server tool: base URLs and client settings (port). | MCP_SERVER.md |
| `STRATA_DEBUG` | env switch | off | 1 dumps raw model text and a compact line per request while diagnosing blank turns. | server.py |
| `STRATA_ENGINE_STALL_S` | env switch | 90 | Seconds of silence plus no CPU/disk use before the engine is ended as frozen; 0 turns the check off. | DETAILS.md |
| `STRATA_EXECV` | env switch | - | Internal switch setup uses when it re-executes itself. | setup.py |
| `STRATA_HTTP_BACKLOG` | env switch | 256 | HTTP listen backlog (requests wait in the server, not the kernel). | server.py |
| `strata_install` | MCP tool | plan only | Strata MCP server tool: runs setup in the background (family, model, context, vision, data_dir, gpus; confirm true to run, cancel true to stop). | MCP_SERVER.md |
| `strata_logs` | MCP tool | - | Strata MCP server tool: last log lines (source server/engine/setup, model, lines 1-500). | MCP_SERVER.md |
| `STRATA_MAX_BODY_MIB` | env switch | 256 | Largest request body in MiB (413 above it). | server.py |
| `strata_mcp` | API request field | false | Opt-in for a /v1/chat/completions request to use MCP tools (emits strata_mcp events). | DETAILS.md |
| `strata_models` | MCP tool | - | Strata MCP server tool: every family and size, download size, RAM, fit. | MCP_SERVER.md |
| `STRATA_REQUEST_LINES` | env switch | off | 1 echoes one stdout line per finished request from the engine's own summary. | server.py |
| `STRATA_SKIP_SHA256` | env switch | off | 1 skips setup's SHA-256 check of the downloaded engine. | TROUBLESHOOTING.md |
| `strata_start` | MCP tool | - | Strata MCP server tool: starts an installed model (model, port, gpu, wait_seconds). | MCP_SERVER.md |
| `STRATA_START_RETRIES` | env switch | 3 | How many times a transient engine start failure (HSA exceptions) is retried (0 = never). | server.py |
| `strata_status` | MCP tool | - | Strata MCP server tool: what is running, installed, and this PC's hardware. | MCP_SERVER.md |
| `strata_stop` | MCP tool | - | Strata MCP server tool: unloads and ends the server it started (port, force). | MCP_SERVER.md |
| `STRATA_WATCHDOG_S` | env switch | 120 (60 from 0.1.13) | Seconds without progress before the engine watchdog ends a request; 0 turns it off. | DETAILS.md |
| `threads` | run-config key | - | Thread count passed to strata-vision (image encoder). | server.py |
| `tokenizer` | run-config key | pack tokenizer | Directory of the model's tokenizer (vocab.json, merges.txt, token_type.json). | server.py |
| `trusted_origins` | run-config key | none | Origins (proxies, tunnels) allowed to reach the settings and MCP tools. | DETAILS.md |
| `vision` | run-config key | set by setup | Section configuring the image encoder (exe, mmproj, model, max_tokens, min_tokens, cuda_device, elastic ...). | server.py |

## 12. Model management (install, load, lazy load, idle unload, aliases)

| Name | Kind | Default | What it does | Source file |
|---|---|---|---|---|
| `--before-load` | server option | none | Same as before_load. | DETAILS.md |
| `--browser` | setup option | on | Opens the chat page again when the model is ready. | setup.py |
| `--data-dir` | setup option | Strata-data next to the folder | Where model files, packs and mtp are kept. | DETAILS.md |
| `--family` | setup option | asked | Chooses the model family (original, coder, swift, unsloth). | DETAILS.md, setup.py |
| `--gguf-dir` | setup option | - | Uses GGUF files you already have. | DETAILS.md |
| `--idle-unload` | server option | 0 (never) | Same as idle_unload_s. | DETAILS.md |
| `--inspect` | setup option | - | Shows what a GGUF is and whether Strata runs it, from headers only (file, folder, URL, ms:owner/repo, hf:owner/repo). | setup.py |
| `--lazy` | server option | off | Same as lazy_load. | DETAILS.md |
| `--min-free-vram-mib` | server option | 0 | Same as min_free_vram_mib. | DETAILS.md |
| `--model` | setup option | recommended for RAM | Chooses the model size (Q2_0, IQ2_XS, IQ3_XXS, IQ3_S, UD-Q4_K_XL ...). | DETAILS.md, setup.py |
| `--models-dir` | setup option | - | Folder for the GGUF files only. | DETAILS.md |
| `--no-browser` | setup option | browser opens | Do not open the chat page when the model is ready (remembered; --browser undoes it). | setup.py |
| `--open` | server option | off | Opens the local page in the browser once the model is ready. | server.py |
| `--source` | setup option | huggingface | Download source (modelscope). | INSTALL.md |
| `aliases` | run-config key | none | Other model names listed in /v1/models and answered to. | DETAILS.md |
| `before_load` | run-config key | none | Command (string or list) run before the model is loaded again. | DETAILS.md |
| `before_load_ignore_failure` | run-config key | false | Always load even when before_load fails. | DETAILS.md |
| `before_load_strict` | run-config key | false | Always refuse the load when before_load fails. | DETAILS.md |
| `before_load_timeout_s` | run-config key | 120 | How long the before_load command may run. | DETAILS.md |
| `FAMILY` | env switch | qwen | Docker: model family (qwen, swift, coder, unsloth). | INSTALL.md |
| `GGUF_DIR` | env switch | unset | Docker: folder with existing GGUF files (setup --gguf-dir). | INSTALL.md |
| `idle_unload_s` | run-config key | 0 (never) | Unloads the model after N seconds without requests; the next request loads it again. | DETAILS.md |
| `lazy_load` | run-config key | false | Starts the API without loading the engine; the first request or POST /load loads it (also --lazy). | DETAILS.md |
| `min_free_vram_mib` | run-config key | 0 | Loads an unloaded model only when that much VRAM is free, else answers 503. | DETAILS.md |
| `MODEL` | env switch | recommended | Docker: model size to start (selects strata-<model>.json). | INSTALL.md |
| `MODELSCOPE_ENDPOINT` | env switch | modelscope.cn | ModelScope endpoint for --source modelscope. | setup.py |
| `open_browser` | run-config key | true | false stops the server opening the chat page when the model is ready (also --open flag). | server.py |
| `POST /load` | API request field | - | Loads the model ahead of a request. | DETAILS.md |
| `POST /unload` | API request field | - | Unloads the model now (409 while running; ?wait=1 or ?wait=<seconds> waits for the running turn). | DETAILS.md |
| `POST /v1/load, /v1/unload` | API endpoint | - | JSON control aliases of /load and /unload (API key required; 409 while busy). | DETAILS.md |
| `STRATA_DROP_CACHE_ON_EXIT` | env switch | off (Linux) | 1 returns the model files' page cache to the OS when the server stops. | cache_drop.py |
| `STRATA_GGUF_PY` | env switch | auto | Path to the gguf-py directory used by tools/iq_pack.py. | ORCA.md |
| `STRATA_SOURCE` | env switch | huggingface | modelscope downloads the model from ModelScope (also --source modelscope). | INSTALL.md |

## 13. Diagnostics, tests and benchmarks

| Name | Kind | Default | What it does | Source file |
|---|---|---|---|---|
| `--check-logits` | engine flag | off | Copy and check all logits in the stream-token path. | generate.cpp |
| `--clients` | bench option | 0 | bench.sh: also sends N requests at once. | strata_bench.py |
| `--compare` | bench option | - | bench.sh: puts an older results folder beside this run. | strata_bench.py |
| `--compare-only` | bench option | - | bench.sh: prints the comparison of two existing results folders. | strata_bench.py |
| `--cpu-oracle-q8-0` | engine flag | off | Experimental pinned CPU expert quantization and dot reduction. | generate.cpp |
| `--cpu-threads` | engine flag | - | concurrent_main test: CPU threads. | concurrent_main.cpp |
| `--dump-final-r` | engine flag | off | Writes the final R to a file. | generate.cpp |
| `--dump-halves` | engine flag | off | Writes both halves' block_out and inject per layer. | generate.cpp |
| `--dump-layers` | engine flag | off | Writes R after every layer per position (bisection ladder). | generate.cpp |
| `--dump-logits` | engine flag | off | Writes one line of raw logits per position to PATH. | generate.cpp |
| `--dump-mixed` | engine flag | off | Writes the post-attention residual. | generate.cpp |
| `--dump-residual` | engine flag | off | Writes the final R (hc x n_embd, f32) for head bisection. | generate.cpp |
| `--dump-routing` | engine flag | off | Dumps a routing trace replayable by tools/replay_cache_policy.py. | DETAILS.md |
| `--families` | engine flag | - | Prints the model families the engine supports. | generate.cpp |
| `--gpu-only-full` | engine flag | off | Measure: replay pre+post for all layers plus head, no pool (true per-token GPU floor). | generate.cpp |
| `--gpu-roles` | engine flag | - | concurrent_main test: GPU role split. | concurrent_main.cpp |
| `--gpu-stages` | engine flag | off | One-token replay of per-layer graphs (refuses native IQ packs). | DETAILS.md |
| `--gr-fp32-activations` | engine flag | off | Experimental CUDA-oracle GR activation precision. | generate.cpp |
| `--gr-native-mmvf` | engine flag | off | Experimental pinned GR norm/projections; implies FP32 activations. | generate.cpp |
| `--graph-only` | engine flag | off | Measure: replay the 48 pre graphs only. | generate.cpp |
| `--keep-canonical` | engine flag | off | A/B: also load canonical copies of natively served tensors (more VRAM). | generate.cpp |
| `--logits-stride` | engine flag | 1 | Stores every Nth logits row plus the final input (N>1 requires --max-new 1). | generate.cpp |
| `--long` | bench option | off | bench.sh: also a prompt of about 32,768 tokens. | strata_bench.py |
| `--max-new` | engine flag | 16 | Tokens the one-shot engine generates. | generate.cpp |
| `--native-bf16` | engine flag | off | Experimental CUDA-oracle SSM/router/indexer BF16 projections. | generate.cpp |
| `--native-bf16-extra` | engine flag | off | Experimental CUDA-oracle PLE/shared gate BF16 projections. | generate.cpp |
| `--native-flash-attn-short` | engine flag | off | Diagnostic pinned vector attention (--max-context 256 or less). | generate.cpp |
| `--native-gdn` | engine flag | off | Experimental pinned CUDA GDN norms/gates/recurrence. | generate.cpp |
| `--native-moe-combine` | engine flag | off | Experimental pinned CUDA routed/shared combination. | generate.cpp |
| `--native-ple-key` | engine flag | off | Experimental native PLE key; requires --native-dense-gguf. | generate.cpp |
| `--native-ple-postops` | engine flag | off | Experimental pinned PLE postprojection arithmetic. | generate.cpp |
| `--native-qsa` | engine flag | off | Experimental pinned QSA normalization and output gate. | generate.cpp |
| `--native-qsa-indexer` | engine flag | off | Experimental pinned indexer key cache and pooling. | generate.cpp |
| `--native-rope` | engine flag | off | Experimental pinned text-only CUDA rotary arithmetic. | generate.cpp |
| `--native-router` | engine flag | off | Experimental pinned CUDA 512-expert top-10 routing. | generate.cpp |
| `--no-capture` | engine flag | off | Runs layers directly instead of replaying CUDA graphs. | generate.cpp |
| `--no-fast-attn` | engine flag | off | A/B: disables the fast attention kernel. | generate.cpp |
| `--no-fast-select` | engine flag | off | A/B: disables the fast block-selection kernel. | generate.cpp |
| `--no-fused-gdn` | engine flag | off | A/B: disables fused GDN kernels. | generate.cpp |
| `--no-fused-gr` | engine flag | off | A/B: the six-kernel hyper-connection read and a separate write. | generate.cpp |
| `--no-hit-poke` | engine flag | off | A/B: skip the driver poke after the hit path launch. | generate.cpp |
| `--no-ple-prefetch` | engine flag | off | A/B arm: read the PLE table's sixteen rows one at a time. | generate.cpp |
| `--no-publish-kernel` | engine flag | off | A/B: disables the publish kernel. | generate.cpp |
| `--no-split-rows` | engine flag | off | A/B: disables splitting expert rows. | generate.cpp |
| `--no-token-graph` | engine flag | off | A/B: two graphs per layer instead of one per token. | generate.cpp |
| `--out` | bench option | bench/results | bench.sh: where the results folder is written. | strata_bench.py |
| `--ple-delay-us` | engine flag | 0 | Fault injection: each PLE row read completes no earlier than U microseconds. | generate.cpp |
| `--ple-sync-submit` | engine flag | off | A/B arm: submit table reads on the token thread instead of an I/O thread. | generate.cpp |
| `--prefill-until` | engine flag | - | Limits prompt prefill to this many tokens (diagnostic). | generate.cpp |
| `--quiet` | bench option | off | bench.sh: less output. | strata_bench.py |
| `--ready-timeout` | bench option | 1800 | bench.sh: seconds to wait for the model to load. | strata_bench.py |
| `--runs` | bench option | 3 | bench.sh: runs per test. | strata_bench.py |
| `--shared-late` | engine flag | off | A/B: shared expert after the CPU pool instead of overlapped. | generate.cpp |
| `--spec-corrupt` | engine flag | 0 | Test: replaces every Nth draft with a wrong token. | generate.cpp |
| `--spec-follow` | engine flag | off | Benchmarks: emit a given continuation and accept matching drafts. | generate.cpp |
| `--spec-oracle` | engine flag | off | Test: draft oracle path file. | generate.cpp |
| `--stage-timing` | engine flag | off | Per-stage kernel-count shares (not a time profile). | generate.cpp |
| `--stats` | engine flag | off | Prints the expert tier statistics of decoding. | DETAILS.md |
| `--stream-token` | engine flag | off | Enqueue token work on the session stream (experimental). | generate.cpp |
| `--sync-every-layer` | engine flag | off | Debug: synchronise after every layer. | generate.cpp |
| `--think` | bench option | off | bench.sh: leaves thinking on (the default suite turns it off). | strata_bench.py |
| `--tokens` | engine flag | required (one-shot) | Prompt as comma-separated token IDs for the one-shot engine (diagnostics). | generate.cpp |
| `--tokens-file` | engine flag | - | Pretokenized prompt file, commas or whitespace. | generate.cpp |
| `--url` | bench option | - | bench.sh: use the server already running at this address. | strata_bench.py |
| `--version` | engine flag | - | Prints the engine version. | generate.cpp |
| `--window-hashes` | engine flag | off | Per verify window, writes a hash line of its final residual rows and argmaxes. | generate.cpp |
| `STRATA_BATCH_TRACE` | env switch | off | Traces batch slot activity in the server log. | server.py |
| `STRATA_DBG_NAN` | env switch | off | 1 counts and reports non-finite FP16 outputs and GEMM inputs (Intel/HIP debugging). | AMD_HIP.md |
| `STRATA_DECODE_TIMING` | env switch | off | 1 prints a per-request `strata decode timing:` profile line. | DETAILS.md |
| `STRATA_DOORBELL_NOCHECK` | env switch | off | 1 makes doorbell_wait_payload skip its check (SYCL debugging). | A770_SPEED_SPEC.md |
| `STRATA_DUMP_MOE_INPUT` | env switch | off | Dumps real MoE input rows for mmq_nvfp4_parity --real. | NVFP4.md |
| `STRATA_DUMP_SEL` | env switch | off | Dumps the QSA block selection to a file (measurement). | INTEL.md |
| `STRATA_FS_VERIFY` | env switch | off | 1 reads back and compares every landed Foresight slot. | DETAILS.md |
| `STRATA_HIPBLASLT_VERBOSE` | env switch | off | 1 prints hipBLASLt fallback counts in a summary line. | AMD_HIP.md |
| `STRATA_IO_STATS` | env switch | off | 1 adds page-cache hit/miss and read-ahead used/unused counts to the file tier I/O log line. | DETAILS.md |
| `STRATA_KEY` | env switch | unset | API key used by tools/early_close_test.py. | BATCHING.md |
| `STRATA_LOGPOS` | env switch | unset | File path to dump each position's next token and top-K logprobs (for quant quality comparison). | QUANTS.md |
| `STRATA_LOGPOS_TOPK` | env switch | - | Number of top tokens STRATA_LOGPOS records (20 or 64). | QUANTS.md |
| `STRATA_LOOKAHEAD_STATS` | env switch | off | 1 scores next-layer expert predictions and logs recall/precision. | DETAILS.md |
| `STRATA_METAL_KPROF` | env switch | off | Per-kernel GPU times (value T). | MAC.md |
| `STRATA_METAL_PROFILE` | env switch | off | 1 prints a time breakdown per window. | MAC.md |
| `STRATA_METAL_TRACE` | env switch | off | 1 prints per-window traces. | MAC.md |
| `STRATA_NAN_GUARD` | env switch | on | 0 disables the non-finite-logits guard kernel (#879). | DETAILS.md |
| `STRATA_NAN_INJECT` | env switch | off | Debug switch reporting the n-th checked window as non-finite to exercise the guard path. | DETAILS.md |
| `STRATA_PARITY_PROMPT_ATTN` | build option | OFF | Builds the qsa_prompt_attn_parity test. | NVIDIA_V100.md |
| `STRATA_PF_STEP_SYNC` | env switch | off | 1 syncs after every prompt step to find which step hangs. | TROUBLESHOOTING.md |
| `STRATA_PIPELINE_DEBUG` | env switch | off | 1 enables the STRATA_PIPELINE_* tuning/test variables (THETA, FORCE_MISS, SWITCH, LOG, TRACE, SPEC_DEPTH). | MULTI_GPU.md |
| `STRATA_PIPELINE_FORCE_MISS` | env switch | off | With STRATA_PIPELINE_DEBUG=1 forces every pipeline guess wrong (test). | MULTI_GPU.md |
| `STRATA_PIPELINE_LOG` | env switch | - | With STRATA_PIPELINE_DEBUG=1 pipeline logging. | MULTI_GPU.md |
| `STRATA_PIPELINE_SPEC_DEPTH` | env switch | - | With STRATA_PIPELINE_DEBUG=1 pipeline spec depth. | MULTI_GPU.md |
| `STRATA_PIPELINE_SWITCH` | env switch | - | With STRATA_PIPELINE_DEBUG=1 pipeline switch. | MULTI_GPU.md |
| `STRATA_PIPELINE_THETA` | env switch | - | With STRATA_PIPELINE_DEBUG=1 tunes the guess-likelihood threshold. | MULTI_GPU.md |
| `STRATA_PIPELINE_TRACE` | env switch | - | With STRATA_PIPELINE_DEBUG=1 pipeline tracing. | MULTI_GPU.md |
| `STRATA_PLE_FIXTURE_DIR` | build option | unset | Directory with ple_in.bin/ple_out.bin for the ple_parity test. | INTEL.md |
| `STRATA_PLE_GGUF` | build option | unset | Path of the Q2_0 shard 2 GGUF for the ple_parity test. | INTEL.md |
| `STRATA_PLE_TRACE` | env switch | off | 1 traces the PLE reader. | INTEL.md |
| `STRATA_PREFILL_SYNC` | env switch | off | 1 syncs after every phase (Intel debug). | INTEL.md |
| `STRATA_PREFILL_TIMING` | env switch | off | 1 prints per-chunk prompt timing. | AMD_HIP.md |
| `STRATA_SNAPSHOT_FULL_CAPTURE` | env switch | off | 1 disables K/V retention in conversation-cache snapshots for diagnostic comparisons. | DETAILS.md |
| `STRATA_USAGE_DUMP` | env switch | off | PATH to dump device-side routing counts (Intel profile building). | INTEL.md |
| `STRATA_VERIFY_ALL_SLOTS` | env switch | off | 1 verifies all expert slots on Intel. | INTEL.md |
| `STRATA_VERIFY_ASYNC_LAUNCH` | env switch | off | 1 runs the verifier's CUDA graph launch on a separate host thread (for Compute Sanitizer). | DETAILS.md |
| `STRATA_VERIFY_DEBUG` | env switch | off | 1 prints the residual after every layer. | INTEL.md |
| `STRATA_VERIFY_DUMP` | env switch | off | Directory to dump filled slots compared with the GGUF. | INTEL.md |
| `STRATA_VERIFY_EAGER` | env switch | off | 1 runs the verify window without a graph (profiling). | A770_SPEED_SPEC.md |
| `STRATA_VERIFY_FIND` | env switch | off | 1 reads every filled slot back and compares it with the GGUF. | INTEL.md |
| `STRATA_VERIFY_PROFILE` | env switch | off | 1 adds per-stage GPU event timing (`decode GPU stages`). | DETAILS.md |

---

Total parameters listed: **738**.

Files read: README.md; docs/DETAILS.md, BATCHING.md, MULTI_GPU.md, SECOND_GPU.md, VRAM_ELASTIC.md, KV_PREFETCH.md, BATCHED_DMA.md, EXCHANGE_ROTATION.md, DISJOINT_EXPERT_CACHE.md, PREFILL_HEAD_ALIAS.md, PROMPT_CACHE_TAIL.md, MESSAGE_BOUNDARY_CACHE.md, RESEARCH_RUNS.md, MMVQ_IL_TABLE.md, LOGIT_BIAS.md, NVFP4.md, MODELS.md, HOW_IT_WORKS.md, INSTALL.md, TROUBLESHOOTING.md (fully); LLAMA_SWAP.md, MCP_SERVER.md, IMAGES.md, ORCA.md, MAC.md, AMD_HIP.md, AMD_HIP_PERFORMANCE.md, STRIX_HALO.md, INTEL_ARC.md (the sections that carry settings); INTEL.md, A770_SPEED_SPEC.md, OLDER_GPUS.md, NVIDIA_V100.md, QUANTS.md, UNSLOTH_Q4.md, UNSLOTH_Q6.md, ORCA_Q4_K_S.md, COMMUNITY_BENCHMARKS.md, AI_SETUP.md, TEST_REQUESTS.md (only by targeted search: every `STRATA_*` mention, every flag and config key by targeted search with surrounding text, not line by line); serve/runconfig.py (fully), serve/server.py (argument parser, config keys, request fields, environment reads), serve/replicas.py, keepawake.py, cache_drop.py, cpu_affinity.py, telemetry.py; setup.py (argument parser and environment reads); src/program/generate.cpp (the `usage()` text and the argument parser); docker-entrypoint.sh and tools/strata_bench.py (option lists); sycl/ and src/core/concurrent_main.cpp (flag literals).
