# Test plan for the Strata port (run after all features are written)

Patches under test: `patches/llama.cpp/0002-ggml-skip-negative-expert-ids-and-cpu-split-overlap.patch` and
`0003-moe-cache-cpu-miss-prewarm-and-adaptive-draft.patch` (0003 needs 0002), plus the telecode side (`llamacpp/argv.py`,
`config.py`, `patcher.py`, `process.py`, `tray/qt_sections.py`, `llamacpp/gguf_info.py`, `llamacpp/cpu_topology.py`,
`tools/moe_cache_bench.py`). Parameter coverage: `docs/strata-parity.md`.

## Safety protocol (this laptop hard-crashed twice on 2026-10-14, bugcheck 0x20001 HYPERVISOR_ERROR)

- One `llama-server` at a time, never two. Kill it (`taskkill /F /IM llama-server.exe`) before the next.
- Test runs use **normal priority, no mlock, <= 8 threads, ctx <= 8192** unless the test is about the real config
  (`tools/moe_cache_bench.py` does this by default; `--no-safe` is for the few real-config runs).
- **Hard VRAM ceiling 11.6 GB (11600 MiB, set by the user):** `tools/moe_cache_bench.py --vram-limit` (default 11600) kills the server above it. Third crash, 2026-10-15 07:29, was the 27B with `--spec-type draft-mtp` at ~11.9 GB: **do not run the 27B with MTP in tests** (see memory note 27b-mtp-crashes-laptop); use the 35B and the 27B without spec.
- (old rule) Watch VRAM: abort a run above 11 GB used. The 27B + MTP real config needs ctx_size_spec (60000) to leave room for the draft context.
- No benchmark while compiling. Timing runs: close other GPU apps, 3 repeats, report mean and spread, note GPU temperature.
- Stop and report on the first crash, hang over 3 minutes, `CUDA error`, `GGML_ASSERT`, or a sluggish system. No retry loops.
- Test runs use a frozen copy of the binaries (`build/test-bin-*`), not the build directory that is being compiled.

## A. Regression: nothing changes without flags

| # | Test | Pass criterion |
|---|---|---|
| A1 | stock vs patched, 35B MoE `--n-cpu-moe 26`, 4 prompts, greedy, 192 tokens | byte-identical outputs (done once on the first build: pass) |
| A2 | same for the dense 27B with the real overrides | byte-identical |
| A3 | upstream cache path: `--n-cpu-moe 99 --moe-cache-mib 3000`, no new flag | byte-identical to stock |
| A4 | telecode argv for every registered model with current settings.json | identical to the argv before the work (done by the knob agent: pass) |
| A5 | `llama-server --help` of the patched build vs stock | only the new flags added |
| A6 | `build_moe_ffn` refactor: a different MoE arch (any small MoE GGUF if available) | byte-identical to stock |

## B. Correctness of each new mode (compare to the upstream-cache path as reference)

Exact equality is not required between CPU and GPU kernels (different quantised dot products). Criterion: identical text, or
the first divergence is a near-tie (top-2 probability gap < 0.02 via `/completion` `n_probs`) and agreement stays high.

| # | Flags | Check |
|---|---|---|
| B1 | `--moe-cache-cpu-miss` | short prompts identical to static layout (passed once); a long prompt (above the batch cap) equal or near-tie |
| B2 | promote-after 0 / 1 / 1e6, promote-max 0, decay 0, promote-gain 0.5 | starts, no crash, same text as B1 |
| B3 | `--moe-cache-cpu-miss-max-tokens` 1, 8, 16, 31 and prompts of 1, 8, 16, 17, 31, 32, 33, 200 tokens | no crash around the cap, output equal to B1 |
| B4 | `--moe-cache-stats-file F` twice in a row, then `--moe-cache-prewarm` | file written (size = 16 + 4*layers*experts), second run logs the prewarm, output equal; a file from another model is ignored with a warning; a truncated file is ignored |
| B5 | `--moe-cache-foresight 1/2/4` | no crash, log shows uploads ahead, output equal to B1 |
| B6 | `--moe-cache-route-bias 0.01/0.05/0.2` | lossy: measure agreement with B1 and hit rate; default 0 must equal B1 |
| B7 | `--moe-cache-tail-skip 1/2` with MTP verify windows | lossy: acceptance and speed vs no skip |
| B8 | CUDA `mul_mat_id` skip-negative: unit check through B3 (batch 2..31 with all ids hit / all miss / mixed) | rows of skipped ids are zero, no illegal access (run once under `compute-sanitizer --tool memcheck` with a tiny prompt) |
| B9 | `--spec-draft-adaptive` on the 27B MTP | same text as fixed n_max (greedy), log prints the policy; acceptance by position plausible |
| B10 | parallel 2 slots, a cancelled request mid-stream, a model swap through telecode | no hang, no leak (VRAM back to baseline) |

## C. Performance (3 repeats each, same prompts, real 200k config for the 35B)

Rows: static `--n-cpu-moe 26` (baseline, ~44-55 tok/s measured) vs upstream cache vs cpu-miss vs cpu-miss + foresight vs + route
bias vs + prewarm; env `GGML_CUDA_REGISTER_HOST=1` on/off; cache sizes 2500/4000/6000 MiB; threads 6/8/12; with and without the
real core mask. Record decode and prompt tok/s, hit rate from the log, uploaded MiB per token. Goal: find the settings where
hybrid beats static; the 35B fits too well for it to win by much, so the headline case needs the 125B Qwen3.8-Flash-Next (smallest
quant 67.6 GB, needs a download decision).

## D. Speculative decoding

27B MTP: `--spec-type draft-mtp` with `ctx_size_spec` 60000: VRAM headroom first (nvidia-smi), then fixed n_max 2/3/4 vs adaptive;
tokens/s and acceptance per position. Check telecode applies the smaller context only when spec is active.

## E. Telecode UI and settings

Every new knob: change in the tray -> `settings.json` updates -> `argv.build_argv` shows exactly that flag (and nothing when unset) ->
start from the tray -> the flag is accepted by the binary (`--help` lists it). Greyed rows follow their dependencies.
Patched Build card: per-patch toggles, `Requires:` handling (disable 0002 -> 0003 is skipped with a message), Build / Install /
Version Manager restore. `Detect CPU cores`, `Inspect GGUF`, `prevent_sleep`, `min_free_vram_mib` (set above current free VRAM -> spawn refused with a clear message).

## F. Stability

20-minute decode loop with hybrid + foresight + prewarm: no VRAM growth, no log errors, steady tok/s. A 100k-token prompt (above the
cap, offload path). Shutdown writes the stats file. Repeat after a telecode restart.

## G. Settings audit (last step, after every feature is built)

Goal: every existing llama.cpp setting in telecode still works exactly as before, alone and together with the new features.

1. **Static audit, no model loaded.** For every key in `_GLOBAL_FLAG_SPECS`, `_MODEL_FLAG_SPECS`, the ngram / load-mode / spec-type emitters and the new rows: build the argv with only that key set, and check the flag (a) is emitted exactly once with the right value, (b) is listed by `--help` of the stock binary, or of the patched binary if it is a patched-only flag (use `llamacpp/flag_audit.py` `audit_config`), (c) is NOT emitted when the key is unset, 0 or empty where that is the documented meaning. Report any flag telecode emits that the binary rejects.
2. **Argv snapshot diff.** Argv for the current `settings.json` (both models) before the work vs now: identical (done once by the knob agent). Re-run on the final tree; also for the example settings file `settings.example.json` if present.
3. **Combination matrix (argv only).** New features on top of every existing mode that could clash: `fit` (the fitter ignores the cache size?), `cpu_moe`, `n_cpu_moe` 0/26/99, `parallel` 1/2 with `kv_unified`, `kv_offload` off, `cache_ram` 0 / non-zero, `cache_idle_slots`, `load_mode` mlock / mmap / none, `spec_type` none / draft-mtp / ngram-mod / chain, `spec_default`, `mmproj` with `mmproj_offload`, `override_tensor`, `lora`, `rope_*` / yarn, `tensor_split` / multi GPU, `cpu_mask*` / `cpu_strict*` / `prio*` / `poll*`, `threads*`, `ctx_size_spec`, `env`. For each pair: argv is valid (no duplicate flags, no unknown flags) and no setting is silently dropped.
4. **Runtime smoke for the combinations that matter** (safe protocol, one server at a time): the real 35B config (200k, mmproj, parallel 2, mlock, core mask) with and without `moe_cache_mib` + `moe_cache_cpu_miss`; the real 27B config with and without `spec_type: draft-mtp` + adaptive + `ctx_size_spec`; the proxy end to end (chat request through :1235, model swap between the two models, idle unload, `context_overflow`, `inference_defaults`, tool search), the Telegram/tray start-stop path, Speed Test in the tray.
5. **Tray audit.** Build every page offscreen; every row reads and writes its key; greyed rows follow their dependencies; no key is edited in two places (one editable control per setting).
6. Findings go to `docs/strata-audit-report.md` with a table: setting | alone | with new features | evidence.
