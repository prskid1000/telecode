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

## 6. Not done yet (and why)

- Hard crash #3 (bugcheck 0x20001 HYPERVISOR_ERROR, 2026-10-15 07:29) happened while the 27B ran with MTP + adaptive + head subset at ~11.9 GB VRAM. **No more 27B + MTP runs** (memory note `27b-mtp-crashes-laptop`). MTP is therefore covered by the CPU-only policy simulation (`tools/spec_policy_sim.cpp`) and by the lossless-output checks above, not by further GPU runs.
- Runtime smoke of the real 35B config (200k context, mmproj, parallel 2, mlock) with and without the cache; B10 (parallel requests, cancel, VRAM back to baseline); proxy end to end (model swap, idle unload, tool search); Telegram / tray start and stop; Speed Test. These load the GPU while the user runs an emulator, so they wait.
- Performance (group C and D speed): needs the high-power charger.
- Prompt share is slower than plain misses on this machine (prompt 292 vs 707 tok/s in the one low-power run): needs a proper measurement and a lower default share before it is recommended. It is off by default.
- Tray audit: every page builds offscreen with all new labels (502 labels llama page, 397 models page), no settings key has two editable rows. Read/write of each row was not exercised.
