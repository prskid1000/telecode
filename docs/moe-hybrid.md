# MoE hybrid mode (Strata's algorithm) and LMCache for llama.cpp

The series is now three patches:

| Patch | Replaces | What |
|---|---|---|
| `0001-common-add-defer_loading-to-tool-definitions.patch` | — (unchanged) | tool `defer_loading`, used by `proxy/llama_caps.py` |
| `0002-moe-hybrid-strata.patch` | old 0002, 0003, 0004 | Strata's way of running a MoE model whose experts do not fit in VRAM; only the parts that make decoding faster |
| `0003-lmcache-prefix-restore.patch` | old 0005 | LMCache rewritten: no bundled zstd, server-only, correct restores, more hits (partial matches through checkpoints), 2 entries per conversation, idle-only disk writes |

- Base: llama.cpp **b11731** (latest release on 19 Oct 2026). Checked: the three apply in order to a clean b11731, and 0003 also applies
  without 0002.
- Checked here: every changed C/C++ file compiles (CPU build, no warnings).
- **Not checked:** the CUDA files (no CUDA compiler in the sandbox; the CUDA parts are old 0002's kernels, which you already built),
  and nothing has run on a GPU. First run: follow "How to test" below.
- Old 0004 (MTP draft cache) is dropped: you don't use MTP.

---

## Integration steps (do these in order)

Commands are for PowerShell on your Windows machine, run from the telecode repo root. Only the settings of removed features go; every
setting that still does something stays in the UI, with a sensible default so it works without tuning.

### Step 1: put the files in place

```powershell
git checkout -b moe-hybrid
Expand-Archive -Force telecode-moe-hybrid-patches.zip -DestinationPath .
git rm patches/llama.cpp/0002-ggml-skip-negative-expert-ids-and-cpu-split-overlap.patch `
       patches/llama.cpp/0003-moe-cache-modes-expert-reduction-adaptive-draft-head-subset.patch `
       patches/llama.cpp/0004-upstream-draft-kv-out-of-checkpoints-and-pinned-host-buffers.patch `
       patches/llama.cpp/0005-lmcache-fast-kv-blend.patch
git add patches/llama.cpp docs/moe-hybrid.md
Get-ChildItem patches/llama.cpp   # expect: 0001, 0002-moe-hybrid-strata, 0003-lmcache-prefix-restore, series.json
```

The zip's 0001 is byte-for-byte your current one; `series.json` is replaced.

### Step 2: `llamacpp/argv.py`

**2a. MoE hybrid rows.** In `_MODEL_FLAG_SPECS`, right after the `("moe_cache_mib", ...)` row:

```python
    # MoE hybrid mode (patches/llama.cpp/0002-moe-hybrid-strata.patch)
    ("moe_hybrid_mib",                  "--moe-hybrid-mib",                  "value_nz"),
    ("moe_hybrid_max_tokens",           "--moe-hybrid-max-tokens",           "value_nz"),
    ("moe_hybrid_swap_every",           "--moe-hybrid-swap-every",           "value_nz"),
    ("moe_hybrid_swap_max",             "--moe-hybrid-swap-max",             "value"),
```

Delete the rows whose patch is gone: every `moe_cache_*` row **except** `moe_cache_mib`, plus `moe_reduce_min`, `moe_reduce_thresh`,
`spec_draft_adaptive*`, `spec_draft_head_subset*`; delete `_MOE_CACHE_TUNING`, `_MOE_CACHE_ROUTE_RECIPE` and the suppression lines that
use them or the deleted keys. The upstream MoE-cache keys (`moe_cache_mib`, `moe_cache_auto`, `moe_cache_auto_headroom_mib`) stay.

**2b. The hybrid switch.** In `build_argv`, right after `mcfg = {**mcfg, **overrides}`:

```python
    # MoE hybrid mode: every layer's experts stay in host memory, the GPU keeps the most used ones
    hybrid = bool(mcfg.get("moe_hybrid"))
    if hybrid:
        mcfg = {**mcfg, "cpu_moe": True}
```

Next to the other `suppressed_model` rules:

```python
    if hybrid:
        suppressed_model.update({"n_cpu_moe", "moe_cache_mib"})   # all layers on the CPU; the MoE cache excludes the hybrid mode
    else:
        suppressed_model.update({"moe_hybrid_mib", "moe_hybrid_max_tokens", "moe_hybrid_swap_every", "moe_hybrid_swap_max"})
```

**2c. LMCache rows.** In `_GLOBAL_FLAG_SPECS`, LMCache block: keep `lmcache`, `lmcache_chunk_size`, `lmcache_ram_mb`, `lmcache_disk`,
`lmcache_disk_dir`, `lmcache_disk_max_mb`; delete `lmcache_blend`, `lmcache_blend_recompute`, `lmcache_blend_min_tokens`,
`lmcache_compress`, `lmcache_async_save` (the new patch ignores them); add:

```python
    ("lmcache_checkpoints",         "--lmcache-checkpoints",       "value"),
    ("lmcache_disk_idle_sec",       "--lmcache-disk-idle-sec",     "value"),
```

Replace the LMCache suppression block with:

```python
    if not gcfg.get("lmcache"):
        suppressed_global.update({"lmcache", "lmcache_chunk_size", "lmcache_ram_mb", "lmcache_checkpoints", "lmcache_disk",
                                  "lmcache_disk_dir", "lmcache_disk_max_mb", "lmcache_disk_idle_sec"})
    elif not gcfg.get("lmcache_disk"):
        suppressed_global.update({"lmcache_disk_dir", "lmcache_disk_max_mb", "lmcache_disk_idle_sec"})
```

**2d.** Replace the `_PATCHED_PREFIXES` line:

```python
_PATCHED_PREFIXES = ("--moe-cache-", "--moe-hybrid-", "--lmcache", "--moe-expert-reduce", "--spec-draft-n-ubatch", "--spec-draft-adaptive", "--spec-draft-head-subset")
```

A stock binary still starts (unknown add-on flags are left out with a warning).

### Step 3: automatic slot size (`llamacpp/auto_cache.py`, `process.py`, `llamacpp/config.py`)

`auto_cache.py` already sizes `moe_cache_mib` from free VRAM at every start and retries smaller after an out-of-memory start. Give the
hybrid mode the same: with **Auto Slot Memory** on (`moe_hybrid_auto`), `moe_hybrid_mib` becomes a ceiling (0 = no ceiling).

**3a.** `auto_cache.py`, replace `enabled()` and add a helper:

```python
def enabled(model_name: str) -> bool:
    m = cfg.model_cfg(model_name)
    return bool(m.get("moe_hybrid_auto") if m.get("moe_hybrid") else m.get("moe_cache_auto"))


def target_key(model_name: str) -> str:
    """The setting the plan sizes: the hybrid mode's slots or the MoE cache."""
    return "moe_hybrid_mib" if cfg.model_cfg(model_name).get("moe_hybrid") else "moe_cache_mib"
```

**3b.** In `plan()`:
- `if not mcfg.get("moe_cache_auto"):` → `if not enabled(model_name):`
- after it: `key = target_key(model_name)`
- `n_cpu = n_moe if mcfg.get("cpu_moe") else ...` → `n_cpu = n_moe if (mcfg.get("cpu_moe") or mcfg.get("moe_hybrid")) else ...`
- `cap = cfg.as_int(mcfg.get("moe_cache_mib"))` → `cap = cfg.as_int(mcfg.get(key))`
- the log line `-> moe_cache_mib %d%s` → `-> %s %d%s` with `key` as an extra argument
- `return {"moe_cache_mib": mib}` → `return {key: mib}`

The headroom stays `moe_cache_auto_headroom_mib` (default 1500), shared by both modes.

**3c.** `process.py`, the two places that read the planned size (about lines 648 and 673):

```python
                key = auto_cache.target_key(model_name)
                if nxt is None or int(nxt.get(key) or 0) >= self._planned_mib:
```

```python
        key = auto_cache.target_key(model_name)
        self._planned_mib = int(overrides[key]) if overrides and overrides.get(key) else None
```

**3d.** `llamacpp/config.py`: delete the unused `lmcache_*` getter functions (they read `llamacpp.lmcache.*`, a key nothing writes).
Nothing is added: pinned memory and `poll` stay your own settings (see 5c).

### Step 4: tray UI (`tray/qt_sections.py`)

The **Patched Build** card needs no change: it lists whatever `.patch` files are in `patches/llama.cpp/` with their `Title:` lines, and
the on/off keys follow the new names (`llamacpp.custom_build.patches.0002-moe-hybrid-strata`, `...0003-lmcache-prefix-restore`).

The settings cards are shown or hidden by **patch file name prefix** (`patch="0003"`, `patch="0005"`), and those numbers now mean other
patches, so:

**4a.** In `_bind_patch_toggle_visibility`, hide a card whose patch is not in the series:

```python
        matching = [p for p in patcher.patches() if p.name.startswith(patch_prefix)]
        if not matching:
            widget.setVisible(False)  # the patch is not in the series: its settings do nothing
            return
```

**4b.** Delete the cards of removed features (line numbers from telecode `main`, 19 Oct 2026):

| Page | Card | Lines (about) | Feature (gone with old 0003) |
|---|---|---|---|
| llama.cpp | Adaptive Draft Length & Head Subset (`adp_card`) | 2643–2699, plus `layout.addWidget(adp_card)` at 2816 | adaptive draft length, draft head subset |
| model | Fewer Experts per Token | 5762–5774 | `--moe-expert-reduce-*` |
| model | CPU-Miss Mode & Promotion (`_sl_cm`) | declared 5777, rows in 5795–5945 | CPU-miss cache mode, promotion, stats file, prewarm |
| model | Prefetch, Routing & PCIe (`_sl_pf`) | declared 5779, rows in 5795–5945 | foresight, route bias, PCIe share, swap rounds, tail skip |
| model | Prompt Batches (`_sl_pb`) | declared 5781, rows in 5795–5945 | prompt share, staging slots |

For the model page: delete the three `_sl_cm/_sl_pf/_sl_pb = _sec(...)` lines, every statement starting with `_sl_cm.addWidget(`,
`_sl_pf.addWidget(` or `_sl_pb.addWidget(` (each runs to its closing `))`), and the `_mc_on` / `_mc_cache` lambdas. For the llama.cpp
page: from `adp_card, adp_body = _card(` through the last `adp_body.addWidget(...)`, the `_adaptive_*` helpers, and
`layout.addWidget(adp_card)`. If you cannot delete one yet, set its `patch=` to `"0003-moe-cache-modes"` (matches no file, so 4a hides it).

Keep unchanged: **Patched Build**, **MoE Expert Cache** (upstream `--moe-cache-mib`, auto size, headroom), **Speculative Decoding**
(incl. Draft Micro-Batch).

**4c.** LMCache card: set `patch="0003-lmcache"`, retitle, keep its rows except the five dead ones, and add two rows:

| Row | Key | Change |
|---|---|---|
| Enable LMCache | `lmcache` | keep; help: "keep finished conversations and continue new prompts from them, across slots and restarts" |
| Chunk Size | `lmcache_chunk_size` | keep (default 256) |
| Host RAM Cache | `lmcache_ram_mb` | keep; default **8192**; help: "RAM for the saved conversations" |
| **Checkpoints per Entry** | `lmcache_checkpoints` | **add**, 0–32, default 4 |
| Enable Disk Tier | `lmcache_disk` | keep; help: "also keep them on disk, they survive restarts" |
| Disk Directory | `lmcache_disk_dir` | keep (default `./data/lmcache`) |
| Disk Limit | `lmcache_disk_max_mb` | keep (default 65536) |
| **Disk Write Delay** | `lmcache_disk_idle_sec` | **add**, 0–3600 s, default 30 |
| CacheBlend, Boundary Recompute, Min Blend Size, Transfer Compression, Async Background Staging | `lmcache_blend*`, `lmcache_compress`, `lmcache_async_save` | **delete** (not implemented) |

```python
    lmcache_card, lmcache_body = _card("LMCache: Saved Conversations",
                                       "--lmcache: keep finished conversations and continue new prompts from them, across slots and restarts",
                                       patch="0003-lmcache")
    ...
    lmcache_body.addWidget(_dependent(
        _number_row("llamacpp.lmcache_checkpoints", "Checkpoints per Entry", 0, 32, 1, 0, "",
                    help_text="--lmcache-checkpoints: context checkpoints kept with each saved conversation, half the earliest and half "
                              "the latest. A prompt that changes inside a saved conversation (compacted, edited) resumes at the last one "
                              "before the change. ~70 MB each on Qwen3.6 (default 4).", default=4),
        ["llamacpp.lmcache"], _lmc_on))
    lmcache_body.addWidget(_dependent(
        _number_row("llamacpp.lmcache_disk_idle_sec", "Disk Write Delay", 0, 3600, 5, 0, "s",
                    help_text="--lmcache-disk-idle-sec: new conversations are written to disk only after this many seconds without "
                              "requests, and at shutdown; earlier turns of a conversation are not written. Saves SSD wear (default 30).",
                    default=30),
        ["llamacpp.lmcache", "llamacpp.lmcache_disk"], _lmc_disk))
```

**4d.** New card on the model page, right before `_sl = _sec("MoE Expert Cache", ...)`:

```python
        _sl_hy = _sec("MoE Hybrid Mode",
                      "--moe-hybrid-* — each layer keeps its most used experts on the GPU; the CPU computes the others at the same time",
                      patch="0002-moe-hybrid")
        _hy_on = lambda on: bool(on)
        _sl_hy.addWidget(_toggle_row(f"{p}.moe_hybrid", "MoE Hybrid Mode",
                                     "For MoE models that do not fit in VRAM. Keeps every expert in RAM and the most used ones of each layer "
                                     "on the GPU. Replaces N CPU MoE and the MoE Expert Cache for this model while on."))
        _sl_hy.addWidget(_dependent(
            _toggle_row(f"{p}.moe_hybrid_auto", "Auto Slot Memory",
                        "Size the slots at every start from the free VRAM (minus the Auto Cache Headroom below) and retry smaller after an "
                        "out-of-memory start. Slot Memory, when set, becomes a ceiling. Needs nvidia-smi.", default=True),
            [f"{p}.moe_hybrid"], _hy_on))
        _sl_hy.addWidget(_dependent(
            _number_row(f"{p}.moe_hybrid_mib", "Slot Memory", 0, 262144, 256, 0, "MiB",
                        "--moe-hybrid-mib: VRAM for expert slots (~78 MiB per slot per layer on Qwen3.6-35B-A3B Q4). With Auto Slot "
                        "Memory on: the ceiling, 0 = none.", default=0),
            [f"{p}.moe_hybrid"], _hy_on))
        _sl_hy.addWidget(_dependent(
            _number_row(f"{p}.moe_hybrid_max_tokens", "Max Batch", 1, 31, 1, 0, "tok",
                        "--moe-hybrid-max-tokens: the largest batch that uses the hybrid form (decoding is 1 token, speculative "
                        "verification a few). Larger batches (prompts) copy the experts to the GPU (default 16).", default=16),
            [f"{p}.moe_hybrid"], _hy_on))
        _sl_hy.addWidget(_dependent(
            _number_row(f"{p}.moe_hybrid_swap_every", "Rebalance Every", 1, 4096, 8, 0, "tok",
                        "--moe-hybrid-swap-every: generated tokens between two rebalances of the slots (default 32).", default=32),
            [f"{p}.moe_hybrid"], _hy_on))
        _sl_hy.addWidget(_dependent(
            _number_row(f"{p}.moe_hybrid_swap_max", "Moves per Rebalance", 0, 4096, 1, 0, "experts",
                        "--moe-hybrid-swap-max: experts moved per rebalance while generating, over all layers (default 8). After a prompt "
                        "up to 2048 move at once.", default=8),
            [f"{p}.moe_hybrid"], _hy_on))
```

If `_toggle_row` has no `default=` parameter in your version, drop it and set `moe_hybrid_auto: true` in the settings instead.

### Step 5: settings (`settings.json`, or your profile)

**5a.** Build source: pin the tag the patches were made for, and delete any `llamacpp.custom_build.patches` keys of the old files:

```json
"custom_build": { "source_dir": "", "tag": "b11731", "cuda": true }
```

**5b.** The `qwen3.6-35b-a3b` model:

```json
"moe_hybrid": true,
"moe_hybrid_auto": true,
"moe_hybrid_mib": 0
```

Delete every old `moe_cache_*` key except `moe_cache_mib` / `moe_cache_auto` / `moe_cache_auto_headroom_mib` (unused while the hybrid
mode is on). `n_cpu_moe` can stay: it is ignored while the switch is on and used again when you turn it off.

**5c.** Left to you, off / blank by default; the patches do not depend on them:

| Setting | Default | What it changes | Try |
|---|---|---|---|
| model `env`: `GGML_CUDA_REGISTER_HOST=1` | not set (off) | upstream llama.cpp option: page-locks the host model buffers, so prompt copies and rebalance copies run truly async. Costs ~19 GB of locked RAM (your profile already uses `load_mode: mlock`) | when measuring prompt speed |
| `poll` | blank = server default (50) | how long CPU threads spin before sleeping between CPU steps. The hybrid mode has 40 CPU steps per token (the static split 26), so 0 means a wake-up each time | blank or 100; your profile's 0 is the slowest for both modes |

Keep `ctx_checkpoints` at 32: when the list is full the server drops the **oldest** checkpoints, which are the ones a compacted
conversation needs.

**5d.** LMCache (optional): `"lmcache": true`, optionally `"lmcache_disk": true`; the other keys have working defaults (RAM 8192,
checkpoints 4, chunk 256, disk 65536 MiB, write delay 30 s). Delete `lmcache_blend`, `lmcache_blend_recompute`,
`lmcache_blend_min_tokens`, `lmcache_compress`, `lmcache_async_save`.

**5e.** `settings.example.json`: the same keys with a short comment each; update `_comment_lmcache`.

### Step 6: build the patched llama.cpp

Tray → llama.cpp → **Patched Build**. The build log must list the three patches as applied. Then:

```powershell
.\llama\llama-server.exe --help | Select-String "moe-hybrid|lmcache"
```

A later **Update Now** overwrites the patched build (as before); build again after updating.

### Step 7: first start, check the log

```
auto cache: ... -> moe_hybrid_mib 6400                       (telecode log)
llama_moe_hybrid: 80 of 256 experts per layer in CUDA0 for 40 layers, ... MiB, batches up to 16 tokens
server_lmcache: LMCache on: chunk 256 tokens, RAM 8192 MiB, 4 checkpoints per entry, disk ... (N entries found, ...)
```

- `MoE hybrid mode is off: <reason>` → read the reason (usually no layer has its experts in host memory).
- Out of memory at start: telecode retries with a smaller size (up to 3 times).
- At exit: `llama_moe_hybrid: N rebalances moved M experts`.

### Step 8: check answers and speed

```powershell
python tools/moe_cache_bench.py --model qwen3.6-35b-a3b --label static --set moe_hybrid=false --set n_cpu_moe=26
python tools/moe_cache_bench.py --model qwen3.6-35b-a3b --label hybrid --set moe_hybrid=true
```

Compare `tg_tok_s` (decode; old baseline 54.6) and `pp_tok_s` (prompt). Equal `outputs_sha256` means identical text; a different hash
is fine if the texts first differ at a near-tie. If the bench does not run auto_cache, add `--set moe_hybrid_mib=6400` (the value from
the log in Step 7).

### Step 9: if something goes wrong

- Turn **MoE Hybrid Mode** off: the model runs exactly as before (`n_cpu_moe` split), same binary.
- Back to the release binary: Tray → Version Manager → Restore.
- Send the llama log lines around `llama_moe_hybrid` / `LMCache` and any `GGML_ASSERT`.

### Step 10: cleanup

- `tools/bench_matrix.example.json`, `tools/settings_audit_*.py`: replace the old cache knobs with `moe_hybrid`.
- Commit: `git commit -m "llama.cpp: MoE hybrid mode and LMCache patches, two-switch UI"`.

---

## 1. Strata's algorithm (read from `src/qwen35/q36_engine.cu`, `src/kernels/cpu/pool.cpp`, `docs/QWEN36.md`)

Strata's Qwen3.6 engine has a "hybrid mode" for cards that cannot hold every expert (12 and 16 GB).

### 1.1 Placement

- One budget for experts = free VRAM after weights, cache and buffers.
- **Every MoE layer gets the same number of slots** `S` = budget ÷ (bytes of one expert from every layer). RTX 3060 12 GB, UD-Q4_K_XL:
  37 of 256 experts per layer.
- RAM holds every expert (pinned when the driver allows).
- At start the slots hold experts `0..S-1`. The first prompt's rebalance fixes that.

### 1.2 One MoE layer, small batch (decode, MTP verify, ≤ 32 rows)

| Step | GPU stream | Host thread / CPU pool |
|---|---|---|
| 1 | attention, router, top-k | |
| 2 | group the selected experts **with the slot table that lives on the GPU** (`slot_of_d`); copy ids and activations to pinned host memory; record an event | |
| 3 | run the resident experts | busy-wait on the event (`cudaEventQuery` loop) |
| 4 | run the shared expert | compute the missed experts (per expert, its rows split across all pool threads) |
| 5 | idle if the CPU is still busy | copy the CPU results to the GPU, launch one merge kernel |
| 6 | combine + shared-expert gate + residual + next norm (one kernel) | |

The GPU never waits for the host to *decide* anything. The host only waits for the ids (step 3), and the GPU only waits for the CPU's
results (step 5).

### 1.3 Moving experts (`rebalance`)

- Score of an expert = how often the router picked it (prompt rows counted on the GPU, decode rows counted on commit, rejected MTP
  drafts not counted).
- After a prompt: one rebalance with up to **2048** swaps.
- While generating: every **32** positions, up to **8** swaps over all layers.
- A swap happens only if the newcomer's score is higher than the score of the resident expert it replaces. The weakest resident goes first.
- After each rebalance every score is **halved** (recent use counts most).
- Copies happen between forwards, on the compute stream, then one sync. Never inside a layer.

### 1.4 CPU pool

- Persistent worker threads, pinned to physical cores, spinning between jobs (no OS wake-up per layer).
- The host thread does a share of the work too.
- Each token's activation is quantized once; each expert's gate/up rows, then down rows, are split across all threads.

### 1.5 Prompts (more than 32 rows)

Each layer's experts are copied whole to the GPU (double-buffered on a second stream) and run there. Resident experts are copied GPU to GPU.

### 1.6 Other facts

- CUDA graphs are **off** in hybrid mode (the host takes part in every layer).
- Lossless for Qwen3.6 (the "tail skip" that drops experts is a Flash-Next default, not used here).
- Measured by Strata on the RTX 3060 12 GB: 59.7 tok/s (llama.cpp `-ncmoe 26`: 48–52), 84.7 tok/s with `--mtp 2`.

---

## 2. What the patch does, piece by piece

| Strata piece | In the patch | Where |
|---|---|---|
| Same slot count per layer | `S = min(n_expert, mib ÷ bytes of one expert per tracked layer) − 1` (one spare slot: CUDA MMQ may read past the last expert) | `src/llama-moe-hybrid.cpp` (constructor) |
| Slot table on the GPU | `slot_tab[il]`: device I32 `[1, n_expert]`; the graph looks up `get_rows(slot_tab, ids)` on the GPU | `llama-moe-hybrid.cpp`, `llama-graph.cpp` `build_moe_ffn` |
| GPU runs resident experts, CPU the rest | `build_moe_ffn` runs upstream's expert block twice: pass 0 on the GPU over the slot banks (ids = slots, −1 for misses), pass 1 pinned to the CPU over the host experts (ids = misses, −1 for hits). Both skip ids < 0 and leave zero rows; the sum is done on the GPU | `llama-graph.cpp` |
| Miss ids for the CPU | a CPU op (`build_route`) reads the selected ids, writes `e` or −1 from the host mirror of the slot table, and counts every id | `llama-moe-hybrid.cpp` `impl::route` |
| Ids reach the host early, event, busy-wait | `ggml_backend_sched_set_early_cpu_inputs`: when a GPU split is followed by a CPU split, the scheduler computes the GPU split up to the last node the CPU split reads, copies those tensors to the CPU split's (pinned) input buffers, records an event, then launches the rest of the GPU split. The CPU split busy-waits on the event (`ggml_backend_event_query`) instead of syncing the whole GPU stream | `ggml/src/ggml-backend.cpp` |
| Shared expert during the CPU work | Qwen3.5/3.6 MoE builds the shared expert first and hands it to `build_moe_ffn` (`moe_overlap`), which adds it to the graph between the GPU pass and the CPU pass | `src/models/qwen35moe.cpp`, `llama-graph.cpp` |
| Rebalance | `before_compute(n_tokens)` runs before every graph compute: a batch > `max_tokens` marks "prompt seen"; the next small batch triggers `rebalance(2048)`; then every 32 generated tokens `rebalance(8)`. Swaps are chosen by largest score gain over all layers, copies are queued async on the device stream with one sync at the end, scores are halved | `llama-moe-hybrid.cpp`, `llama-context.cpp` `process_ubatch` |
| Prompt counts | prompt batches are counted in upstream's expert copy callback (`sched_copy_experts`), which already reads the selected ids to copy only the used experts; no extra graph step | `llama-graph.cpp` |
| Prompts streamed to the GPU | upstream path (`op_offload`, batches ≥ 32): the scheduler copies the host experts a batch uses to the GPU. **The experts that have a slot are copied from GPU memory** (the hybrid mode plugs into upstream's copy callback, like the MoE cache does), so only the others cross PCIe. With `GGML_CUDA_REGISTER_HOST=1` (optional, off by default) the PCIe copies come from pinned memory | `llama-moe-hybrid.cpp` `copy_experts`, `llama-context.cpp` `sched_copy_experts` |
| Skip ids < 0 | from old 0002: CPU and CPU-repack `mul_mat_id` write zero rows; CUDA single-token vector kernel skips the expert; CUDA multi-token path remaps −1 to an unused slot and zeroes the rows after | `ggml-cpu.c`, `repack.cpp`, `ggml-cuda.cu`, `mmid.cu`, `mmvq.cu`, `ggml.c` |
| CUDA graphs off | follows from the CPU splits (as in Strata) | — |

### Per decode step (what happens, in order)

1. `process_ubatch` → `moe_hybrid->before_compute(n_tokens)` (maybe a rebalance, between computes).
2. Per MoE layer: GPU split runs attention, router, top-k, `cont(ids)`, slot lookup → **early copy** of ids, activations, weights + event → GPU continues with the resident experts and the shared expert.
3. CPU split: busy-wait on the event → route op (miss ids + counts) → host-expert `mul_mat_id` (skips resident) → weights.
4. Next GPU split: CPU result copied up → `experts_gpu + experts_cpu` → sum over experts → `+ shared expert` → residual → next layer.

### Flags

| Flag | Default | Meaning |
|---|---|---|
| `--moe-hybrid-mib N` | 0 (off) | VRAM for slots. Needs the experts in host memory: `--cpu-moe` (all layers) or `--n-cpu-moe K` (first K layers). |
| `--moe-hybrid-max-tokens N` | 16 | Largest batch that uses the hybrid form, 1..31. Decoding (1 row) and speculative verify windows use it; prompts don't. |
| `--moe-hybrid-swap-every N` | 32 | Generated tokens between two rebalances. |
| `--moe-hybrid-swap-max N` | 8 | Experts moved per rebalance while generating (over all layers). After a prompt (a batch of 32+ tokens) up to 2048 move once. |

The MoE cache (`--moe-cache-mib`) and the hybrid mode exclude each other; with both set, the hybrid mode wins. The MTP draft context never
gets the hybrid mode (its MTP block runs on the CPU, about 8 experts per draft step).

### Log lines to look for

```
llama_moe_hybrid: 76 of 256 experts per layer in CUDA0 for 40 layers, 5830.00 MiB, batches up to 16 tokens
...
llama_moe_hybrid: 37 rebalances moved 2410 experts          (at exit)
```

If you see `MoE hybrid mode is off: ...`, the reason follows (for example no layer has its experts in host memory).

---

## 2.1 Independent review of 0002 and what changed

A separate review read the whole 0002 diff against upstream b11731 (code reading, no GPU). Findings and what was done:

| # | Finding | Severity | Done |
|---|---|---|---|
| 1 | The hybrid mode was created on any GPU, but only CPU and CUDA/ROCm honour skipped experts (−1 ids); on Vulkan/SYCL/Metal it would read out of bounds | wrong output / crash | **fixed**: CUDA / ROCm / MUSA only, a warning otherwise |
| 2 | The count-only CPU step after each layer in prompts moved split boundaries, so upstream's copy callback stopped seeing the expert matmul: all 256 experts uploaded, no GPU-to-GPU copy of resident experts | big prompt slowdown | **fixed**: the step is gone; prompt routing is counted inside the copy callback, which reads the ids anyway |
| 3 | With fewer than 7 slots per layer, the CUDA multi-token path could give a token the same slot twice | wrong output | **fixed**: refuses a size below `n_expert_used − 1` slots with a clear message |
| 4 | A device fault made the CPU's wait loop spin forever | hang | **fixed**: `event_query` aborts on any error other than "not ready" |
| 5 | More than 30 early inputs would overflow a fixed array | memory corruption (rare) | **fixed**: falls back to the normal copy |
| 6 | For 2–16 tokens, CUDA computes a dummy expert for every skipped entry and zeroes it | moderate slowdown in verify windows | **not done** (needs a CUDA kernel change that cannot be compiled here); decoding (1 token) is not affected |
| 7 | Cutting a split into two views lost the CUDA-graph fast path (uid 0 = full compare every token) | small slowdown | **fixed**: stable uids per half |
| 8 | Hazards: `GGML_CUDA_GRAPH_OPT`, backends without async reads, inputs in other buffer types, fused kernels reading −1 ids, the repack message, a 17–31 token batch triggering the 2048-move rebalance | low | **fixed** all six (early copy skipped in the first three cases; fusions disabled for skipped ids; message mentions `--no-repack`; only batches of 32+ count as a prompt) |
| 9 | Differences from Strata: MTP block not hybrid, rejected drafts counted, one spare slot per layer, rebalance copies synchronous, CPU branch sends 8 rows per token | speed, small | rebalance copies now **asynchronous**; the rest kept (you don't use MTP; the spare slot is one expert per layer) |

The review also confirmed as correct: the scheduler's early copy (no stale reads, buffer lifetimes, events), the backend placement of
both passes, the shapes, the Qwen3.6 reorder being node-for-node identical when the mode is off, the CPU/CUDA skipped-id kernels, the
rebalance bookkeeping, `copy_experts`, and the construction order.

## 3. What was dropped and why

| From | Dropped | Why |
|---|---|---|
| 0002 | CPU-overlap flag, Windows "poke" event, batched uploads | replaced by early CPU inputs (one general mechanism); uploads are now few and between computes |
| 0003 | global LRU + promote-on-miss, PCIe share / auto, swap rounds, foresight, route bias, tail skip, prompt share + staging slots, stats file + prewarm, fewer experts per token | Strata does none of these on Qwen3.6, they sit on the per-layer critical path, and several change answers; none was measured faster |
| 0003 | adaptive draft length, draft head subset | not MoE; the head subset helped the dense 27B (+48%) — keep that file as its own patch if you use the 27B with MTP |
| 0004 | draft KV out of checkpoints | only matters with MTP / a draft model; you don't use MTP. Upstream PR #29509 is still open if you ever need it |

### 3.1 LMCache, old 0005 → new 0003

| Old 0005 | New 0003 |
|---|---|
| 2.5 MB, of which ~2.4 MB bundled zstd (`vendor/zstd/zstd.c`, 53k lines) | 40 KB; no compression, no vendored code (states are stored as they are; a compressed KV cache is the KV type you choose) |
| class in libllama, exported, plus a `llama.h` field nobody read | lives in the server only: `tools/server/server-lmcache.{h,cpp}`; options in `common_params::lmcache` |
| restored a state of *L* tokens but set `n_past` to the last full chunk (< *L*), and turned off the server's "reprocess" check: wrong text on recurrent models (Qwen3.6) | restoring puts the slot **exactly where it would be had it processed the entry itself** (state, tokens, checkpoints); the server's own prompt-reuse logic then keeps the shared tokens and, on Qwen3.6, goes back to a checkpoint before the first difference. Correct by construction, and the server's checks stay on |
| hits only on chunk-aligned prefixes (and those were wrong) | **more hits**: an entry is used when it is a whole prefix of the prompt *and* when the prompt changes inside it, as long as the point the server can resume from (the whole entry, or its latest checkpoint before the change) is further than what the slot already has. Compacted conversations, edited or regenerated messages, and branches from an older turn now hit, from any slot and after restarts. Entries are indexed at every chunk boundary |
| saved after every prompt **and** at the end of every request, on the server thread; every save queued for disk | saved once, at the end of a request (`release()`), to RAM. Disk writes happen on a background thread **only after the server has been quiet for `--lmcache-disk-idle-sec` (default 30 s) and at shutdown**, and an earlier turn that a longer entry of the same conversation continues is never written. A long agent session costs one disk write per quiet period instead of one per request (at 200k context ≈ 1.2 GB per write) |
| `--lmcache-blend`, `-compress`, `-async-save` parsed, never used | still accepted (so telecode's argv keeps working) but documented as ignored |
| no checkpoints saved; on restore it kept the slot's checkpoints, which belonged to other tokens | each entry keeps up to `--lmcache-checkpoints` (default 4) of the slot's checkpoints, the 2 earliest and the 2 latest, and brings them back on restore. The slot's own checkpoints are kept only inside the tokens it shares with the restored ones. So a compacted conversation (same system prompt, new history) or an edited message can still restart near the change instead of from token 0. On Qwen3.6 a checkpoint is the recurrent state, ~70 MB, so 4 add ~0.28 GB per entry |
| no limit per conversation: a new full-size entry roughly every 256 tokens of growth | **at most 2 entries per conversation**: a new entry drops every earlier turn of the same conversation except the previous one, which serves a regenerated answer; their disk files go too |

How it works: when a request ends, the slot's whole sequence state (KV cache + recurrent state) is copied to RAM together with its exact
tokens and up to 4 checkpoints (with `--lmcache-disk`, later written to `DIR/<hash>.bin`: header, tokens, state, checkpoints). Entries
are indexed by a hash of every chunk-aligned prefix (256 tokens per chunk). For a new prompt the server first takes the slot's own
common prefix as before; then LMCache hashes the prompt chunk by chunk, starting from the longest shared prefix, measures each
candidate's exact common prefix, works out where the server could resume (the whole entry if it is a prefix, else its latest checkpoint
before the difference), and restores the best one if that beats the slot. On restart, the disk index (tokens and checkpoint positions)
is rebuilt from the files. RAM over the limit (8 GB): the oldest states leave RAM (they stay restorable from disk if already written;
unwritten ones are dropped). Disk over the limit (64 GB): the oldest files are deleted.
It is skipped with a draft model / MTP and with images.

Tested here with a small test program that fakes the llama state functions (built with address and undefined-behaviour checkers):
a continuing prompt (whole entry, 4 checkpoints 2 earliest + 2 latest), a compacted conversation (same 150-token start, new history:
resumes at the checkpoint at 128 instead of 0), the slot already having more (no restore), a regenerated answer (previous turn), a branch
from a dropped older turn (resumes at a checkpoint), a prompt equal to an entry (resumes at its last checkpoint), a model without
recurrent layers (resumes at the full shared prefix), the RAM limit keeping the newest entries, idle disk writes (only the latest turn of
each conversation), and a restart restoring from disk with its checkpoints, including the compaction case.

It overlaps with upstream `--cache-ram` (same idea, per slot, RAM only). What LMCache adds: sharing across slots and the disk tier
that survives restarts.

---

## 4. Changes in telecode (summary)

All of it is in the Integration steps above. In short:

| Area | Change |
|---|---|
| `patches/llama.cpp/` | three patches + new `series.json` (0001 and 0003 `exact`, 0002 rebuilt from its paths; 0002 and 0003 both touch `common/arg.cpp` and `common/common.h`) |
| `argv.py` | 4 hybrid rows, `moe_hybrid` switch → `--cpu-moe` and the hybrid flags; LMCache: 2 rows added, 5 dead rows removed; old cache-mode rows removed; `--moe-hybrid-` / `--lmcache` in `_PATCHED_PREFIXES` |
| `auto_cache.py`, `process.py` | Auto Slot Memory: size `moe_hybrid_mib` from free VRAM at every start, retry smaller after out-of-memory |
| `config.py` | unused `lmcache_*` getters deleted (nothing added) |
| tray | 5 cards of removed features deleted; LMCache card keeps its live rows, gains 2, loses 5 dead ones; new MoE Hybrid Mode card (6 rows); cards of missing patches hidden |
| settings | `moe_hybrid`, `moe_hybrid_auto` on the model; dead `moe_cache_*` / `lmcache_*` keys deleted; `poll` and pinned memory stay yours (blank / off) |

**Settings and their defaults** (all tunable in the UI):

| Setting | Default | Where |
|---|---|---|
| MoE Hybrid Mode (`moe_hybrid`) | off | model card |
| Auto Slot Memory (`moe_hybrid_auto`) | on | model card; headroom = Auto Cache Headroom (1500 MiB) |
| Slot Memory (`moe_hybrid_mib`) | 0 (= no ceiling with auto) | model card |
| Max Batch (`moe_hybrid_max_tokens`) | 16 | model card |
| Rebalance Every (`moe_hybrid_swap_every`) | 32 tokens | model card |
| Moves per Rebalance (`moe_hybrid_swap_max`) | 8 | model card |
| moves after a prompt | 2048 | fixed in the patch |
| LMCache, Chunk Size, RAM | off, 256, 8192 MiB | LMCache card |
| Checkpoints per Entry | 4 (2 earliest + 2 latest) | LMCache card |
| Disk Tier, Directory, Limit, Write Delay | off, `./data/lmcache`, 65536 MiB, 30 s | LMCache card |
| entries per conversation | 2 (latest and previous turn) | fixed in the patch |
| `poll` | blank (server default 50) | llama.cpp page |
| pinned memory (`GGML_CUDA_REGISTER_HOST`) | off | model `env` |

## 5. How to test

See Steps 6–8 of the Integration steps.

## 6. Known limits

- One GPU: slots go to the first GPU of the context; layers on other GPUs are not tracked.
- MTP verify rows that get rejected are still counted (Strata counts only kept rows). Small effect at 32-token rebalances.
- Prompts: the resident experts are copied GPU to GPU, the rest cross PCIe for every prompt batch. With the same VRAM for experts, the
  bytes copied per batch are the same as the static split's (all experts minus the resident ones, however they are arranged), so prompt
  speed should be about the same as today. Faster would need copies that overlap compute (a scheduler change, not done).
- The shared-expert overlap is wired for `qwen35moe` only; other MoE models get the hybrid mode without it.
- For 2–16 tokens, CUDA still computes a dummy expert for each −1 entry and zeroes it (review item 6). Correct, wasteful in speculative
  verify windows; decoding (1 token) is not affected.
- CUDA / ROCm only (other GPU backends do not honour skipped experts).
- LMCache: a partial match resumes at a checkpoint, so how much it saves depends on where the 4 kept checkpoints are (2 earliest, 2
  latest). No CacheBlend (reusing text from the middle of other prompts). A state is as large as the slot's KV cache for that conversation
  (q4_0 KV on Qwen3.6: ~6 KB per token, ~1.15 GB at 200k, plus ~70 MB per checkpoint).
- Untested on a GPU so far (see the top of this file).

## 7. Final audit: old patches (0002–0005) vs new (0002, 0003)

"Measured" = numbers from your own notes (`docs/strata-port.md`, laptop RTX 5070 Ti 12 GB, Qwen3.6-35B-A3B UD-Q4_K_XL). "Estimate" = not
measured; nothing new has run on a GPU yet.

### MoE (old 0002 + 0003 + 0004 → new 0002)

| # | Area | Old behaviour | New behaviour | Result |
|---|---|---|---|---|
| 1 | Who decides hit/miss per layer | the CPU, after stopping the whole GPU stream in every layer | the GPU, from a slot table in GPU memory; the CPU starts when the ids arrive (event) | **Improved** |
| 2 | Moving experts | up to 2 uploads per layer per token, inside the layer, from pageable memory | only between computes: after a prompt up to 2048, then 8 per 32 tokens, async (fully async with pinned memory, optional) | **Improved** |
| 3 | Placement policy | one LRU for all layers, every hit reshuffles it | same slot count per layer, decayed routing counts, swaps chosen by gain over all layers | **Improved** |
| 4 | GPU/CPU overlap | GPU and CPU took turns (scheduler sync) | GPU runs resident experts + shared expert while the CPU runs the misses | **Improved** |
| 5 | Decode speed | 36.2–38.2 tok/s with the cache (measured) vs 54.6 static | ~60–68 tok/s (estimate; Strata's own 12 GB gain is +15–20%) | **Improved** (estimate) |
| 6 | Prompt speed | 31.7–66 tok/s with the cache vs 85.9 static (measured, short test) | resident experts copied GPU→GPU, used experts only: about the same bytes as the static split | **Improved** vs old; about equal to static (estimate) |
| 7 | Correctness | lossless modes exact; lossy modes optional | always lossless | **Same** |
| 8 | Lossy speed options (route bias, tail skip, fewer experts per token) | available | removed | **Loss** (tuning options that change answers) |
| 9 | Foresight prefetch, PCIe share/auto, swap rounds, prompt share, staging slots | available, none measured faster | removed (the design no longer needs them) | **Neutral** (fewer knobs, nothing proven lost) |
| 10 | Stats file + prewarm across restarts | available | removed; placement learns from the first prompt | **Loss** (small: the first prompt after a start) |
| 11 | Adaptive draft length, draft head subset | available (+48% on the dense 27B with MTP, measured) | removed | **Loss** only with MTP / speculative decoding |
| 12 | Draft KV out of checkpoints (0004) | applied | removed | **Loss** only with MTP |
| 13 | Speculative verify windows (2–16 tokens) | dummy experts computed for skipped ids | same | **Same** (review item 6, not done) |
| 14 | GPU backends | CUDA only for CPU-miss mode | CUDA / ROCm / MUSA, clear warning otherwise | **Same** / safer |
| 15 | Robustness | event query could hang on a device fault; no input bound | abort on device fault; bounded; skipped where unsafe | **Improved** |
| 16 | CUDA graph reuse | not affected (no split cut) | split cut, but stable uids keep the fast path | **Same** |
| 17 | Stock behaviour with flags off | unchanged | unchanged (Qwen3.6 graph node-for-node identical) | **Same** |
| 18 | telecode settings | ~30 cache-mode knobs | 6 hybrid settings (switch, auto size, size, max batch, rebalance every, moves) | **Improved** usability; **Loss** of the removed knobs (rows 8–9) |
| 19 | Patch size / upkeep | 0002 24 KB + 0003 121 KB + 0004 1 KB | 76 KB | **Improved** |
| 20 | Testing | 0003 modes measured on the laptop | compiled (CPU) and reviewed; not run on a GPU | **Loss** (risk until Step 8 is done) |

### LMCache (old 0005 → new 0003)

| # | Area | Old behaviour | New behaviour | Result |
|---|---|---|---|---|
| 21 | Correctness on Qwen3.6 | restored a longer state than `n_past` and skipped the server's safety: wrong text possible | the slot becomes exactly the saved conversation; the server's own logic resumes; tests pass | **Improved** |
| 22 | Hits | chunk-aligned prefix of the newest save only (and those could be wrong) | whole prefix **and** partial matches through checkpoints (compacted, edited, regenerated, branched), any slot, after restarts | **Improved** |
| 23 | Latency per request | saved twice (after the prompt and at the end) with zstd on the server thread | saved once at the end, no compression | **Improved** |
| 24 | RAM / disk per saved conversation | zstd level 1 (~5–15% smaller on q4_0 KV, estimate) | uncompressed (~1.15 GB at 200k, + ~70 MB per checkpoint) | **Loss** (small) |
| 25 | Saved conversations per chat | unlimited: a new full copy roughly every 256 tokens of growth | 2 (latest + previous turn) | **Improved** RAM; older turns reachable only through checkpoints (**small loss**) |
| 26 | SSD writes | every save queued | only after 30 s idle (and at shutdown), earlier turns never written | **Improved** (much less SSD wear) |
| 27 | Checkpoints | none saved; old ones kept for the wrong tokens | 4 per saved conversation (2 earliest + 2 latest), restored with it | **Improved** (+~0.28 GB per entry) |
| 28 | Blend / compress / async flags | parsed, never implemented | parsed, ignored (documented) | **Same** |
| 29 | Code footprint | 2.5 MB incl. vendored zstd, class exported from libllama, `llama.h` field | 40 KB, server only, no `llama.h` change | **Improved** |
| 30 | telecode settings | 11 rows (5 did nothing) | 8 rows (2 new: checkpoints, disk write delay) | **Improved** |
| 31 | Testing | none found | logic tested with a fake-model test program (ASan/UBSan), not on a GPU | **Improved** |

**Net:** for your use (Qwen3.6, no MTP) the new series is expected to be faster at decoding, about as fast at prompts, correct where the
old LMCache was not, gentler on the SSD, and much smaller to maintain. What you give up: the lossy speed options, prewarm across
restarts, the MTP-only tools, a little RAM/SSD per saved conversation, and (until Step 8 is done) measured proof.
