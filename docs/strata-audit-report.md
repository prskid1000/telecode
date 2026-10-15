# Strata port: settings audit and test report

Date: 2026-10-15. Build: patches 0001-0004 on llama.cpp b11609 (frozen binaries `build/test-bin-0006`, `-0007`), stock binary `llama/llama-server.exe`.
Safety protocol in force: one server at a time, ctx 8192, no mlock, normal priority, hard VRAM ceiling 11.6 GB (bench kills the server above it).
Machine state during testing: low-power charger (speeds are not meaningful, only correctness is judged).

## 1. Static audit of telecode settings (plan G.1, no model loaded)

`audit_static.py` (scratchpad): every key of `_GLOBAL_FLAG_SPECS` and `_MODEL_FLAG_SPECS` set alone, argv built, flag looked up in the `--help` of both binaries.

| Result | Value |
|---|---|
| keys audited | 192 |
| flags in stock / patched `--help` | 257 / 289 (32 patched-only: the `--moe-cache-*` series and `--spec-draft-*` additions) |
| flag emitted exactly once with its key set | all |
| flag unknown to both binaries | 0 |
| false positive | `moe_cache_cpu_miss` alone emits nothing: by design, it needs `moe_cache_mib` |

## 2. Argv snapshot diff (plan G.2)

`build_argv()` of the code before the work (commit e4c2d21) against the current code, same `settings.json`:

| Model | Result |
|---|---|
| qwen3.6-35b-a3b | identical (85 args) |
| qwen3.8-27b | identical (83 args) |

## 3. Combination matrix, argv only (plan G.3)

400 random combinations of 8-25 settings at once (cache on/off, CPU miss, spec type on/off, all kinds of flags): **0** duplicate flags, **0** flags unknown to the patched binary.

## 4. Runtime results (35B MoE unless noted)

| Test | Result |
|---|---|
| A1 stock vs patched, `--n-cpu-moe 26`, greedy | identical output hashes (3 runs, both orders) |
| A2 same on the dense 27B (no spec) | identical |
| A3 upstream cache path (`--n-cpu-moe 99 --moe-cache-mib 3000`) stock vs patched | identical |
| B (18 flag sets, ~500-token prompts, 64 tokens) | no crash, no assert, no hang. Outputs differ from the static layout inside the first ~100 characters in most sets, **including the stock upstream cache path**: CPU and GPU kernels round differently |
| B accuracy check with token probabilities (static / upstream / cpu-miss / cpu-miss + prompt share) | the first differing token is a true tie: 0.504 vs 0.496 (ref gap 0.009). Prompts without such a tie are identical for 24 tokens. Top-token probability differs by up to 0.11 between static and cpu-miss, 0.085 between static and the stock upstream cache |
| B4 stats file / prewarm | file is 40976 bytes (16 + 4*40 layers*256 experts), prewarmed run changes placement, truncated file is ignored without a crash |
| B8 `compute-sanitizer --tool memcheck`, 37-token prompt with prompt share, foresight, round swaps | no error reported |
| D 27B with draft-mtp: fixed n_max 3 vs no spec (48 tokens, greedy) | identical text; with adaptive length and the draft head subset the 96-token output equals the fixed-length output |

Lossy modes (route bias, tail skip) change the text by design and were only run for stability.

## 5. Bugs found by testing and fixed

| # | Found by | Fix |
|---|---|---|
| 1 | third independent review (9 findings): staging slots read by foresight (heap corruption), a slot filled twice per round (wrong expert weights), empty-group dirty loop, route bias after prompt batches, env clamp changing behaviour with flags off, staging reserved without CPU miss, foresight router built for prompt batches | commit fd2542c; the LRU fuzzer now covers the first two and crashed on the old code |
| 2 | testing the draft head subset: server aborts at start with `CUDA error: invalid argument` because the memory-fit pass loads the model without weights and the subset copied into a placeholder buffer | commit eb310b7 |
| 3 | argv: `moe_cache_staging_slots` 0 could not be passed (`value_nz`) | commit fd2542c |

## 5b. Deep review (four independent read-only reviewers, 2026-10-15)

Areas: cache core, ggml/CUDA patch 0002, graph + speculative + head subset + plumbing, telecode Python side. Every finding was checked against the code before a change.

Fixed:

| Finding | Fix |
|---|---|
| `copy_experts` could extend a run of experts into a staging slot (wrong weights after a prompt batch larger than the staging window) | bound by `lru.n_slots` |
| head subset VRAM was invisible to `--fit` and the memory breakdown, and the fit dry run never created it | counted in `memory_breakdown()`, dry run no longer needs a host head |
| patch series did not apply to a clean checkout (0003 already held half of 0004) | 0004 folded into 0003; the series is now checked in order against `git archive HEAD` and equals the working tree on all 34 files (`regen_all.py` logic) |
| adaptive draft length never saved MTP / Eagle3 draft steps (only truncated the result) | those drafters stop at the length the policy chose |
| NaN passed the float range checks of the new flags | NaN-safe checks |
| `pcie_auto_window` 0 gave 0/0 | clamped to >= 1 |
| `cpu_miss()` true without any cached layer (turned scheduler overlap on for nothing) | false when no group is cached |
| host inputs of prompt batches sized by `prefill_max_tokens` instead of `n_ubatch` (GBs of host RAM on large models) | bounded by `n_ubatch` |
| CUDA event query could leave `cudaErrorNotReady` in the last-error slot | cleared |
| "Apply suggested cache size" wrote only the cache size (computed for all experts on the CPU) | also sets `n_cpu_moe` to all MoE layers |
| `auto_cache.plan`: `n_cpu_moe` 0 treated as "all on CPU"; unset context planned for 8192 instead of the trained one | llama.cpp semantics, trained context |
| OOM retry repeated spawns that could not help (no cache planned, same size, failure before the spawn marker, non-memory failure) | retry only when a strictly smaller cache is planned; generic "failed to load model" no longer counts as OOM |
| watchdog could kill a freshly started server after a swap | re-checks pid and state under the lock |
| patched-only flags emitted for a stock binary (server refuses to start) | flags the binary does not list in `--help` are left out (cached per binary) |
| `DEFAULT_BINARY` NameError when `llamacpp.binary` is empty | constant defined |
| non-numeric settings values raised ValueError at spawn | tolerant `as_int` / `as_float` |
| turning a required patch off failed the whole patched build | dependents are skipped, not failed |
| `spec_type: "None"` handled differently in config and argv | case-insensitive |
| bench: relative paths broke in the temp settings copy; the VRAM ceiling killed every llama-server | absolute paths; only the bench's own server is killed |
| tray subscriptions piled up | dropped when the widget is destroyed |

Known and left (documented, not fixed):

- Skipped (negative id) experts still cost a full GPU matmul and are zeroed afterwards; an early exit inside the MMVQ kernel and dropping those pairs in the MMQ id helper would shorten the GPU branch of CPU-miss layers. Needs a full CUDA rebuild and measurement: speed phase.
- Other backends (Vulkan, Metal, SYCL...) do not refuse nodes flagged skip-negative; unreachable today because CPU-miss mode only starts on CUDA.
- `GGML_BACKEND_API_VERSION` is not bumped although the interface structs grew; `llamacpp/patcher.py` moves stale backend DLLs out of the install dir instead.
- Adaptive draft acceptance accounting after a checkpoint restore (recurrent / hybrid targets) counts the target's correction token; with several slots another slot's round can reset a slot's `round_k`; round time includes other slots' prompt chunks; the policy also caps n-gram drafters when both are on. These make the adaptive policy's estimates noisy, not wrong output: verification is exact. Measure before trusting adaptive length on the 27B.
- Async uploads from pageable (mmapped) weights block the host thread; the gain of `--moe-cache-async-upload` needs pinned host weights and a measurement.
- Stats file shared by two contexts is overwritten by the last writer.

## 5c. Patch simplification (2026-10-15)

Goal: smaller patch, same features. Done (plan from a read-only review):

| Change | Effect |
|---|---|
| `build_moe_ffn`: upstream's expert block is back byte for byte (not re-indented, not in a lambda); the CPU miss form runs it twice in a loop | `llama-graph.cpp` +245/-139 -> +114/-3 |
| the 27 cache options travel as ONE `llama_moe_cache_params` pointer in `llama_context_params` / `common_params` instead of 27 copied fields in 5 files | `llama-cparams.h` untouched, `llama.h` +28 -> +4, `common.h` +35 -> +12, `common.cpp` +28 -> +2; a new option touches 2 places instead of 7 |
| `arg.cpp`: the 31 add-on options as one macro call each | +309 -> +84 |
| head subset reports its memory (see 5b) | -- |

Result: patch 0003 +2140/-175 -> +1747/-39 (3112 -> 2449 lines); whole series 4289 -> 3627 lines; 15 files in 0003 and 10 in 0002 (unchanged; merging head-subset files and de-duplicating `llama-moe-cache.cpp` need a differential fuzz first and were not done).

Checked without a GPU: `llama-server --help` of the new build is byte-identical to the old (865 lines, defaults and env names included); LRU fuzzer 160000 steps OK; the patch series applies in order to `git archive HEAD` and equals the working tree on all 34 files. **Not yet checked on the GPU (waiting for the emulator to be closed):** the graph change must give the same outputs as before (A1/A3/B hashes and the `compute-sanitizer` run).

## 6. Not done yet (and why)

- Hard crash #3 (bugcheck 0x20001 HYPERVISOR_ERROR, 2026-10-15 07:29) happened while the 27B ran with MTP + adaptive + head subset at ~11.9 GB VRAM. **No more 27B + MTP runs** (memory note `27b-mtp-crashes-laptop`). MTP is therefore covered by the CPU-only policy simulation (`tools/spec_policy_sim.cpp`) and by the lossless-output checks above, not by further GPU runs.
- Runtime smoke of the real 35B config (200k context, mmproj, parallel 2, mlock) with and without the cache; B10 (parallel requests, cancel, VRAM back to baseline); proxy end to end (model swap, idle unload, tool search); Telegram / tray start and stop; Speed Test. These load the GPU while the user runs an emulator, so they wait.
- Performance (group C and D speed): needs the high-power charger.
- Prompt share is slower than plain misses on this machine (prompt 292 vs 707 tok/s in the one low-power run): needs a proper measurement and a lower default share before it is recommended. It is off by default.
- Tray audit: every page builds offscreen with all new labels (502 labels llama page, 397 models page), no settings key has two editable rows. Read/write of each row was not exercised.
