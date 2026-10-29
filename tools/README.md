# tools/

Scripts for testing and maintaining the llama.cpp side of telecode. Everything runs from the repo root; the generic ones take their inputs as
arguments and know nothing about a particular model. Python: `telecode-venv\Scripts\python.exe` (it has aiohttp / PySide6 that telecode itself needs).

## Safety first (12 GB laptop GPU)

A GPU whose dedicated memory reaches **about 11.7 GB (Task Manager GiB, = 11981 MiB in nvidia-smi)** hard-crashes this machine (bugcheck 0x20001,
three times). Rules for every test that loads a model:

- one llama-server at a time, never two; close the Android emulator first (it takes ~3.4 GB of VRAM)
- stay at or below **11.5 GiB = 11776 MiB** in use including the desktop; `moe_cache_bench.py` and `bench_matrix.py` kill the server above `vram_limit`
  (default 11600 MiB); `vram_guard.py` is a second, independent watcher
- look at the plan first: `vram_plan.py` prints what llama-server will use **without loading the weights**
- no power cut or battery during a test, and compare speeds only inside one session: a laptop varies 2x between runs

## Generic tools

| tool | what it does |
|---|---|
| `vram_plan.py MODEL [--ctx N] [--set key=v1,v2] [-- extra args]` | llama-server's own memory plan (model + KV of every slot + compute buffers + draft context) for a matrix of settings, no weights loaded, a few seconds each. Add ~450 MiB for the CUDA runtime. |
| `vram_guard.py --limit-mib N` | logs the GPU memory every second and kills llama-server above the limit. |
| `moe_cache_bench.py` | one configuration: telecode's own command line for a model (+ `--set`, `--extra`), fixed greedy prompts, tokens/s, an output hash, the peak VRAM. `--pad` long prompts, `--probs` token probabilities, `--vram-limit` kill level, `--exe` binary under test. |
| `bench_matrix.py CASES.json [--baseline RESULTS.jsonl]` | a list of configurations through the bench, one at a time, with the VRAM kill, stopping at the first failure; compares output hashes with an earlier run = a regression check. See `bench_matrix.example.json`. |
| `ocr_bench.py --model M [--thinking] [--levels ...] [--set k=v]` | OCR accuracy of a vision model on generated images with known text (clean to nearly illegible, plus full pages), same seed = same images. `--thinking` with the `page` level is what exposes lossy MoE settings: short answers do not. |
| `compare_runs.py texts|probs` | how far two runs agree: shared prefix of the texts, or the first different token with the two best probabilities (a near-tie is harmless). |
| `patch_series.py regen|verify|upgrade-check TAG` | maintain `patches/llama.cpp` (see below). |
| `build_llama.ps1 [-Target ...] [-Jobs 2]` | build a llama.cpp checkout with Visual Studio, gently and until nothing is left to do. |
| `sanitize_server.py` | llama-server under compute-sanitizer memcheck with one small request. |
| `parallel_download.py URL OUT` | resumable download over many parallel ranged connections (a model file over a slow single connection). |
| `settings_audit_static.py`, `settings_audit_combo.py`, `settings_audit_coverage.py` | no model, no GPU: every settings key emits exactly its flag, 400 random combinations have no duplicate or unknown flags, every flag that only the patched build has a setting and a tray row. |

## Tools for the patched llama.cpp features

| tool | what it does |
|---|---|
| `moe_cache_lru_fuzz.bat` / `.cpp` | the expert-cache planner (`src/llama-moe-cache.cpp`) on random input, no GPU: invariants over 160000 steps. `FUZZ_TRACE=1` prints a fingerprint of the whole state per seed: build it against two versions and the traces must be equal for a refactor to be safe. |
| `spec_policy_sim.cpp` | simulation of the adaptive draft-length policy in five worlds (copy of `common_spec_policy`). |
| `build_head_subset_ids.py` | ranks the tokens of your own text for `--head-subset-ids` and `--spec-draft-head-subset-ids`. |
| `prune_layers.py` | ShortGPT structural layer pruner: removes redundant middle layers from GGUF models so 27B-32B dense models fit 100% in 12GB VRAM at 40+ tok/s. |
| `prune_vocab.py` | Offline vocabulary trimmer: prunes unused multilingual tokens from `token_embd` and `output.weight`, saving 1.0–1.8 GB VRAM. |
| `build_open_pencil.py`, `vendor_drift.py` | TeleDesign editor build, vendored llama.cpp forks (see CLAUDE.md). |

## Typical workflows

**Is a new build / flag safe?** `bench_matrix.py cases.json --baseline <old>/results.jsonl` after `vram_plan.py` shows the memory fits: equal hashes = same
output. Differences: `compare_runs.py probs` tells a near-tie from a real change.

**Which settings fit the GPU?** `python tools/vram_plan.py qwen3.8-27b --ctx 80000 --set ub=512,2048 -- -np 1` (the table of context, micro-batch, slots,
draft depth against memory). Measured on this machine: the plan plus ~450 MiB is the real peak.

**Changing the llama.cpp patches.** Work in `build/llama.cpp` (it is disposable; the patches are the artifact), build with `build_llama.ps1`, test, then
`python tools/patch_series.py regen` (rewrites the patches from the working tree and proves the series applies to a clean `git archive HEAD`).
`patch_series.py upgrade-check b11641` says which patch fails on a newer upstream tag. Exact diffs (somebody else's patch, an upstream PR) are marked
`"exact": true` in `patches/llama.cpp/series.json` and are never rewritten.
