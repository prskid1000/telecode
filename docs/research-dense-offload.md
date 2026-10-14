# Strata-class gains for dense models and models that do not fit in 12 GB: research + port assessment

Date: 2026-10-14. Scope: web research plus a read-only audit of the llama.cpp checkout
(`D:\Projects\Telecode\build\llama.cpp`, upstream b11609 `fcac19079` **plus our patches 0001-0003**, which are applied in that tree).
Nothing was run on a GPU or a model; the only local measurements are file/header reads and a CPU/RAM inventory.

Conventions: **[V]** = verified by opening the source (page or code) in this session. **[S]** = seen only in a search
snippet or secondary summary (not opened). **[E]** = my own estimate/arithmetic, not a measurement. **[U]** = unverified recollection.
"vendor" = numbers the authors of the method report; "indep" = measured by someone else; "PR" = a contributor's table in a llama.cpp PR
(self-reported, but public and re-testable). No independent replication was found for any activation-sparsity method.

---

## 0. Answer in one screen

The dense analogue of an expert cache does **not** exist as a *cache*: a dense model reads every weight on every token, so there is no
hit-rate to win. Decode of a model that is partly CPU-resident is a **DRAM-bandwidth roofline**, and only three levers move it:

1. **Read fewer bytes per token**: activation sparsity (TEAL/CATS/Prox class; <= ~1.3-1.8x on the sparsified part, quality cost, new CPU kernels).
2. **Amortise each weight read over several tokens**: speculative decoding (MTP / EAGLE-3 / DFlash / ngram). Already in llama.cpp; the work is making it *offload-aware*.
3. **Put the right bytes on the fast side** (placement) and **hide transfers** (prefetch overlap) - these cut waste, they do not beat the roofline.

For the actual 27B config on this laptop the model is not really "non-fitting" (see section 1), and the largest visible waste is the **CPU-resident
output head (675 MB read per token, and again for every MTP draft step)**.

### Ranked list (detail in section 8)

| # | Item | Effort | Expected gain on the 12 GB laptop | Needs llama.cpp patch |
|---|---|---|---|---|
| 1 | GPU-resident draft-vocab subset for the MTP/EAGLE/DFlash draft head (+ draft cost fed to our adaptive policy) | S-M | 27B config: [E] 1.3-1.8x decode with MTP; token-identical output | yes (small) |
| 2 | Placement autotuner in telecode (per-tensor + ubatch + env knobs, benchmark-driven, contiguous layer ranges) | S | [E] 5-30% decode on 32B-70B partial offload vs naive `-ngl`; free for 27B | no (argv/`-ot` only) |
| 3 | Async next-layer weight prefetch (carry PR #21067) built on per-split scheduler events shared with the Strata MoE patch 0004 | M | prefill of CPU-resident layers +10-25% [E]; 0 on decode; negative on slow PCIe (PR data) | yes (scheduler) |
| 4 | Wide/tree verification + EAGLE-3/DFlash drafts for true non-fitting 32B-70B, policy calibrated with `--spec-synth-len` | M | 32B-70B CPU-bound: [E] 1.5-2.5x; SpecExec paper reports far more with huge trees | partly |
| 5 | CPU head/embedding path: requantise `output.weight` to a repack-capable type or add an IQ4_XS decode GEMV | S-M | cuts the 8-11 ms [E] CPU head time by 10-30% [E] | maybe |
| 6 | Activation sparsity (CATS/TEAL/Prox-style gate-row skip + transposed down) for CPU-resident FFN | L | CPU-resident part x1.15-1.3 at <= 40% sparsity [E]; quality cost; zero for the 27B config | yes (kernels) |
| 7 | KV hot/cold with query-aware page selection (Quest/ShadowKV/DuoAttention class), only for full-attention dense models whose KV exceeds VRAM | L | none for Qwen3.8-27B (hybrid, 1.4 GB KV at 80k); matters for 32B-class at >= 64k | yes |
| 8 | PowerInfer-style hot/cold neuron cache for natively sparse (ReLU-family) models | L | only if a >= 30B natively sparse GGUF appears; watch-list | yes |

Item 1 is also the one designed in section 9.

---

## 1. Ground truth about this machine and this model (read here, not assumed)

| Fact | Value | How obtained |
|---|---|---|
| CPU | Intel Core Ultra 9 275HX, 24 cores / 24 threads (8 P + 16 E, no SMT) | `Win32_Processor` [V]; P/E split from [Notebookcheck](https://www.notebookcheck.net/Intel-Core-Ultra-9-275HX-Processor-Benchmarks-and-Specs.943094.0.html) [S]. AVX-512/AMX absence on Arrow Lake: [U] (not confirmed by the page) |
| RAM | 2 x 32 GiB DDR5-6400 -> 102.4 GB/s theoretical dual channel | `Win32_PhysicalMemory` [V] + arithmetic [E]; real read bandwidth unmeasured (expect roughly 60-90 GB/s [E]) |
| GPU | RTX 5070 Ti Laptop, 12 GB GDDR7, 192-bit, ~672 GB/s, listed PCIe 5.0 x16 | [Notebookcheck](https://www.notebookcheck.net/Nvidia-GeForce-RTX-5070-Ti-Laptop-Benchmarks-and-Specs.934945.0.html) [S]; the negotiated link on this laptop is unmeasured |
| PCIe 5.0 x16 | ~63 GB/s per direction theoretical | spec arithmetic [E] |
| telecode `qwen3.8-27b` | `Qwen3.8-27B-GSQ-RCO-IQ3_XXS-mtp.gguf`, ctx 80000, ngl 99, `token_embd` + `output` forced to CPU, q4_0/q4_0 KV, flash-attn, `--parallel 2`, ubatch 2048, `--load-mode mlock`, `--threads 8 --cpu-mask 0xc03c03 --cpu-strict 1`, `--prio 3` | `settings.json` + argv in `data/logs/llama.log.prev` [V] |

GGUF header of that file (read with `llamacpp/gguf_info.py`, header only) [V]:

- arch `qwen35`, **65 blocks (64 + 1 NextN/MTP)**, embedding 5120, n_ff 17408, 24 heads / **4 KV heads**, head dim 256.
- `full_attention_interval = 4` -> **only 16 of 64 layers have a KV cache**; the other 48 are GDN/SSM recurrent layers (constant state).
  KV at q4_0 (0.5625 B/element) = 16 x 2 x 1024 x 0.5625 = **18.4 KB/token -> 1.47 GB at 80k** (f16: 5.2 GB) [E from header]. +1 layer for the MTP block.
- file 9.73 GiB. `output.weight` = [5120 x 248320] **IQ4_XS = 675,450,880 B (0.63 GiB)**, `token_embd.weight` IQ2_S (~0.38 GiB, a lookup: free on CPU).
- The MTP block has **no `nextn.shared_head_head`** tensor, so its logits come from `model.output`, i.e. the CPU-resident head
  (`src/models/qwen35.cpp:655-660`: `head_w = shared_head_head ? ... : model.output`) [V].

Consequences (all [E]):

- GPU-resident weights ~ 8.7 GiB, + KV 1.4 GiB + recurrent state + compute buffers (ubatch 2048) -> the card is genuinely full, which is why the head sits on the CPU.
  This 27B config is therefore *"fits except the head"*, not a deep-offload case. KV is not the problem (hybrid architecture).
- Decode per token ~ GPU 9.4 GB / 672 GB/s = 14 ms roofline (~18 ms real) **+ CPU head 0.675 GB / 60-80 GB/s = 8-11 ms** -> the head is ~30-35% of a token.
- With MTP n=3 the head is read ~4 times per round (3 draft steps + 1 verify) -> it can dominate the round. This is what item 1 attacks.

### What the b11609 checkout already has (grep evidence)

| Capability | In checkout? | Evidence |
|---|---|---|
| Op offload of CPU-resident weights for batch >= 32 | yes, tunable | `GGML_OP_OFFLOAD_MIN_BATCH` env (default 32) `ggml-cuda.cu:182`; `--op-offload/--no-op-offload` `common/arg.cpp:3106`; scheduler rule `ggml-backend.cpp:1119` |
| Async weight prefetch / double buffering of CPU layers | **no** | no `PREFETCH` symbol in `ggml/`, `src/`, `common/`; upstream PR #21067 still open (below) |
| Page-locking of mmap'd host weights | opt-in | `GGML_CUDA_REGISTER_HOST=1` -> `cudaHostRegister(Portable\|ReadOnly)` `ggml-cuda.cu:161,5226`; `GGML_CUDA_NO_PINNED` |
| `--fit` for dense models (partial layers: attention first, then up, gate, down) | yes | `common/fit.cpp:19-23, 499-520, 821-849` |
| `-ot`, `--cpu-moe`, `--n-cpu-moe`, `--no-host`, `-nkvo`, `--fit-*` | yes | `common/arg.cpp:2418-2433, 2757-2770, 3026-3080` |
| dense `--n-cpu-ffn` | no (ours, planned) | `docs/strata-parity.md:878` |
| MoE GPU expert cache | yes (#29887) | `src/llama-moe-cache.*` |
| KV Hadamard rotation (makes q4_0 KV usable) | yes (#21038) | `src/llama-kv-cache.cpp:21-383`, env `LLAMA_ATTN_ROT_DISABLE` |
| KV streaming / hot-cold KV | no | only `-nkvo` (all KV on host) |
| Native sparse-attention KV (DSA/DSV4/MSA) | yes, for those archs only | `src/llama-kv-cache-dsa*.cpp`, `-dsv4`, `-msa` |
| Activation-sparse compute | **no** | only Gemma-3n masks activations (gaussian top-k) but still computes densely `src/models/gemma3n.cpp:215,393` |
| MTP, EAGLE-3, DFlash, DSpark, ngram-* speculation | yes | `docs/speculative.md`, `src/models/{eagle3,dflash}.cpp`, `--spec-type` list |
| Reduced draft vocabulary (`d2t`) for EAGLE-3 / DFlash | yes | `src/models/eagle3.cpp:44-51`, `dflash.cpp:115-121, 826-` (scatters draft logits to full vocab) |
| Adaptive draft length by tokens/ms | **ours** (patch 0003) | `--spec-draft-adaptive*` in `patches/llama.cpp/0003-...patch`, `common/common.h:381-385` |
| Tiled CPU matmul (prefill) | yes (#27851); this checkout's gate also lists IQ4_XS, IQ2_*, IQ3_*, IQ1_* | `ggml-cpu/tiled/tiled.cpp:617-629` |
| x86 repack for IQ types | only `IQ4_NL` | `ggml-cpu/repack.cpp` (only IQ4_NL asserts) -> an `IQ4_XS` head is not repacked |
| AMX | code present, needs AMX hardware | `ggml-cpu/amx/` |
| CPU affinity/priority/poll for gen and batch separately, NUMA | yes | `common/arg.cpp:1540-1625, 2726` |
| `llama_prefetch_rows`, `--lazy-mode` | yes (#29599) | row-level mmap prefetch for lazily read tensors (embeddings); **not** layer streaming |

---

## 2. The roofline that frames everything [E]

Decode time per token ~ `B_gpu / BW_gpu + B_cpu / BW_ram + n_splits x t_sync`, where `B_cpu` is the bytes of CPU-resident weights read per token.

- Moving **one byte** from CPU to GPU saves `1/BW_ram - 1/BW_gpu` regardless of which tensor it belongs to. So for decode, "which tensor gives most speed per VRAM byte"
  is answered by *exceptions*, not by tensor type: (a) `token_embd` is a row lookup -> always free on CPU; (b) the output head is read in full every decode token but only for the last
  row(s) in prefill; (c) every GPU/CPU boundary costs a synchronisation, so contiguous layer ranges beat scattered per-tensor placement; (d) attention tensors must sit with their KV.
- **Streaming instead of computing in place does not beat the roofline for decode.** Streaming CPU-resident bytes over PCIe (~63 GB/s theoretical, 25-50 real [E]) and computing
  them on the CPU in place (~60-90 GB/s) both draw on the same DRAM (102 GB/s theoretical). Streaming only wins in **prefill**, where GPU compute per byte is high
  (ubatch 2048 x 27B ~ 110 TFLOP per ubatch [E]) and the copy can hide behind it.
- Calibration against an independent dense run: Llama-3.3-70B UD-Q4_K_XL on an RTX 3090 + Xeon E5-2673 v4 (DDR4) decodes at **2.0-2.3 tok/s** with llama.cpp
  and ik_llama.cpp alike ([Hardware Corner](https://www.hardware-corner.net/guides/llama-cpp-vs-ik_llama-cpp/) [V], indep): exactly the CPU-RAM roofline. No engine escapes it.

---

## 3. Topic 1 - activation sparsity (hot/cold neurons, contextual sparsity)

### 3.1 Survey table

| Method | Mechanism | Reported speedup (baseline, hardware) | Source type | Quality | Retraining? | Code / licence | In llama.cpp / ik_llama / GGUF engine? |
|---|---|---|---|---|---|---|---|
| **PowerInfer** | ReLU-family models; offline profile -> hot neurons on GPU, cold on CPU; online predictors; neuron-aware sparse operators | up to **11.69x** vs llama.cpp, RTX 4090 (11x Falcon-40B FP16; 8x INT4 on 2080 Ti; 3x Llama-2-70B); avg 8.32 tok/s non-quant, 13.20 tok/s quantized [V abstract + README via [arXiv](https://arxiv.org/abs/2312.12456), [repo](https://github.com/Tiiny-AI/PowerInfer)] | vendor | "negligible"; 70B ReLU model showed loss (fine-tuned on ~5B tokens, per repo FAQ) | **yes** (ReLU-fied models) | [MIT](https://github.com/Tiiny-AI/PowerInfer); llama.cpp-derived, own GGUFs that "may not work" in stock llama.cpp | own fork only; **ReLU/ReGLU/squared-ReLU only** (Mistral, Qwen, plain Llama unsupported) |
| **PowerInfer-2** | neuron clusters, NPU for dense (hot) / CPU for sparse (cold), segmented cache, I/O pipeline from flash | up to **27.8x** vs SOTA frameworks; 47B model at 11.68 tok/s on a phone [V [arXiv](https://arxiv.org/abs/2406.06282)]; avg 3.94x vs LLMFlash / 25.4x vs llama.cpp on OnePlus 12 [S [summary](https://rss.boorghani.com/?p=341312)] | vendor | "negligible" | uses sparse (TurboSparse) models | closed/mobile | no |
| **LLM in a Flash** | windowing (reuse recently active neurons) + row-column bundling (up row i + down column i adjacent) | 4-5x (CPU), 20-25x (GPU) vs naive flash loading; models up to 2x DRAM [V [arXiv](https://arxiv.org/abs/2312.11514)] | vendor | relies on ReLU sparsity (OPT/Falcon) | no (uses sparse models) | none | idea only (bundling is reusable for any sparse scheme) |
| **Deja Vu** | per-layer trained predictors choose heads/MLP neurons (contextual sparsity) | >2x vs FasterTransformer, >6x vs HF, OPT-175B [V [arXiv](https://arxiv.org/abs/2310.17157)] | vendor | "no loss" (OPT, ReLU) | trains small predictors, not the model | research code | no |
| **CATS** | threshold on the SwiGLU **gate** activation magnitude (calibrated cutoff) -> skip up/down work for masked neurons | ~**15%** token latency on Llama-2-7B/Mistral-7B with a custom GPU kernel; 50% sparsity within 1-2 points of base, no fine-tuning [V [arXiv](https://arxiv.org/abs/2404.08763)] | vendor | within 1-2% at 50% | **no** | [repo](https://github.com/scalingintelligence/cats) (licence not checked) | no |
| **TEAL** | magnitude-prune **inputs** of every linear layer (qkv, o, gate, up, down) per-tensor thresholds | 1.53x @40% / 1.8x @50%; table: Llama-3-8B A6000 1.63x @50%, Llama-3-70B A6000 1.49x @50%, A100 1.33x / 1.37x [V [arXiv](https://arxiv.org/abs/2408.14690), [HTML](https://arxiv.org/html/2408.14690v1)] | vendor | WikiText ppl Llama-3-8B 5.87 -> 6.21 (40%) -> 6.67 (50%); 70B 2.93 -> 3.52 -> 4.30; six-task avg 8B 68.07 -> 63.42 @50%, 70B 80.41 -> 78.26 @50% [V] | **no** | [repo](https://github.com/FasterDecoding/TEAL) (FP16 Llama-2/3 only; licence not checked) | no |
| **R-Sparse** | input-channel sparsity + rank-aware (SVD) recovery of the dropped part, no predictor | **43%** end-to-end at 50% model-level sparsity with custom kernels [S [ICLR page](https://iclr.cc/virtual/2025/poster/30686), [arXiv](https://arxiv.org/abs/2504.19449)] | vendor | comparable at 50% | no | not checked | no |
| **ProSparse / ReLU-fication** | replace SiLU by ReLU + progressive sparsity regulariser, continued pre-training | 89.3% sparsity Llama-2-7B, up to 4.52x [V [arXiv](https://arxiv.org/abs/2402.13516)]; TurboSparse dReLU 90% (Mistral-7B) / 97% per expert (Mixtral) [S [arXiv](https://arxiv.org/abs/2406.05955)] | vendor | "comparable" | **yes, 10B+ tokens-class** | HF checkpoints | needs PowerInfer-style runtime |
| **SparseInfer** | sign-bit predictor for ReLU-fied models | ~21% over prior SOTA, <= 1 pt loss [S [arXiv](https://arxiv.org/abs/2411.12692)] | vendor | ReLU-fied only | no | not checked | no |
| **Prox (Jul 2026)** | INT4 proxy up/gate weights + input sparsity build one channel mask; selected channels computed exactly in FP16 | **1.51-1.99x** single-batch at 60-70% FFN sparsity, A6000 (Mistral-7B 50.3 -> 100.3 tok/s @70%), vs dense [V [arXiv](https://arxiv.org/abs/2607.27591)] | vendor | Qwen3-8B avg acc 76.1 dense -> 74.6 (50%) / 73.3 (60%) / 68.6 (70%); ppl 8.2 -> 8.4 / 8.6 / 9.4; beats TEAL at 60-70% on all 10 models [V]; +~12% weight storage for proxies | **no** | code/licence not stated in the part read (last 9 k chars unread) | no |
| La RoSA, WINA, Amber-Pruner | rotated / weight-aware sparse activation | not opened | - | - | - | - | **[S/U]** names only |

Common caveats [V from the TEAL paper]: gains are **single-batch decode** only ("does not scale as well to higher batch sizes"), prefill must stay dense, TEAL at 0% sparsity
is *slower* than dense with its kernels, combined sparse+quantised **kernels were left to future work** (they only show perplexity under 8-bit RTN / 4-bit AWQ / 2-3 bit QuIP#,
where "the point of sharp degradation is similar across bitwidths"). Prox: "combined low-bit and sparse kernels are future work", INT4 proxies cost ~12% weight memory.
All hardware is FP16 on A6000/A100/3090. **No result on a GGUF k-/i-quant kernel, none on CPU, none independently replicated.**

### 3.2 What works on an already-trained SwiGLU model without retraining, and at what cost

CATS, TEAL, R-Sparse, Prox. Usable range ~25-40% sparsity at ~0-1.5 ppl-point/1-2 task-point loss for 8B; 70B tolerates more (TEAL 70B @50%: -2.2 task points). ReLU-fication
(ProSparse/TurboSparse) and Deja-Vu/PowerInfer predictors need training or ReLU models and cannot be applied to an arbitrary GGUF such as Qwen3.8-27B.
For our IQ3_XXS 27B, stacking input sparsity on 3-bit weights is **unmeasured** (TEAL's evidence is that degradation onset is similar across bit-widths, on Llama-2-7B).

### 3.3 Port assessment for a llama.cpp patch

The blocker is **memory layout, not the idea** [E]:

- ggml stores a weight as `[ne0 = in][ne1 = out]`, rows contiguous over the *input* dimension, quantised in blocks of 32/256 along that dimension (`ffn_gate/up` 5120 -> 17408; `ffn_down` is `[17408 in][5120 out]`).
- **Skipping an output neuron = skipping a contiguous row** -> cheap (CATS-style gate -> skip *up* rows). **Skipping an input channel** (TEAL for every matrix, and the *down* projection for any scheme)
  means skipping one element of every row, i.e. strided reads inside a quant block. At 50% unstructured sparsity almost no 256-wide block is fully zero, so **zero bytes are saved**.
  PowerInfer/LLM-in-a-Flash solve this with a transposed, neuron-major copy of `down` (bundled with `up`) built at conversion time; that copy must be quantised along the *output* dimension,
  i.e. re-quantised from FP16 sources (or dequantise -> requantise from the IQ3 file, adding noise).
- Bytes saved on a SwiGLU layer when only gate-derived masking is used: read = gate (1/3) + up x density + down x density (needs transposed copy). At 50% density with the transposed copy: 0.67 of FFN bytes;
  FFN is ~72% of a 27B layer's bytes (3 x 5120 x 17408 vs ~105 M attention params) [E] -> **~24% fewer layer bytes, ~1.3x on the sparsified part** at best. Without the transposed copy: 1/3 saved on up only -> 0.83 -> ~12%.
  This matches CATS's reported ~15% (GPU) [V].
- TEAL-style whole-model input sparsity could reach ~1.5-1.8x only with a column-major block layout for *all* matrices (a new quant layout) - a new GGUF type, not a patch.
- Effort **L**: new GGUF tensors, calibration tool (per-layer thresholds file), CPU kernel (AVX2/VNNI on Arrow Lake; the repacked path is bypassed), CUDA kernel if the part runs on GPU, graph change with a threshold op.
  Gate: only profitable on the CPU-resident share of a 32B-70B model and at batch 1-4 (spec verification rows would densify - the union of 4-8 token masks is much larger than one mask [E]).
- Expected gain on the 12 GB laptop: **0 for the 27B config** (nothing CPU-resident except a non-sparse head); [E] 1.1-1.25x end-to-end on a 70B Q4 with ~30 GB on CPU at 40% sparsity, minus a quality loss. Rank 6.
- Risks: quality compounding with 3-4-bit quants; interaction with speculation (verification batches densify the mask); kernel maintenance across upstream churn.

Natively sparse models: Gemma-3n applies activation sparsity in early layers (in-checkout, `gemma3n.cpp:215`) but llama.cpp still computes densely; making that computation skip rows is a smaller, model-specific variant (and Gemma-3n is small, so low value).

---

## 4. Topic 2 - weight/layer streaming, placement, partial offload

| Idea | Mechanism | Numbers | Source type | In checkout? | Port assessment |
|---|---|---|---|---|---|
| **Stock partial offload / `--fit`** | contiguous layers on GPU; overflow layer split attention->up->gate->down | n/a | code [V] | yes (`fit.cpp`) | baseline; telecode's `vram.py`/`gguf_info.py` already estimate |
| **`-ot` per-tensor placement** | regex on tensor names -> buffer type | community guidance: attention + shared experts on GPU, big FFN on CPU ([HF guide](https://huggingface.co/blog/Doctor-Shotgun/llamacpp-moe-offload-guide), MoE) [S]; ik_llama thread: "dense ffn layers get offloaded to CPU first as they are just larger" ([#532](https://github.com/ikawrakow/ik_llama.cpp/discussions/532)) [S] | anecdotal | yes | **no published dense speed-per-VRAM-byte study found**; by the roofline all bytes are equal for decode (section 2) |
| **Prefill streaming of CPU weights (op-offload, batch >= 32)** | scheduler copies CPU weights to GPU per ubatch and runs the op there | HF guide: 300 GB over PCIe 4.0 x16 ~ 10 s per batch; recommends `-b/-ub 4096` for CPU+GPU [S]; a user claim of ubatch 64 giving 59 -> 582 tok/s on Qwen3.5-27B is a LinkedIn aggregator [S, unreliable] | anecdotal | yes; knob `GGML_OP_OFFLOAD_MIN_BATCH`, `-ub` | tune, do not patch; sweep `-ub` x min-batch per model |
| **Page-lock host weights** | `cudaHostRegister` on the mmap'd tensor | issue #25859 (MoE, RTX 3060 12 GB, Qwen3.6-35B-A3B Q4_K_M `-ncmoe 26`, pp2048): page-locking alone **+21%**, plus second-stream upload overlap -> 1143 -> 1880 tok/s (+64%), GPU idle 42% -> few % [V [issue](https://github.com/ggml-org/llama.cpp/issues/25859)] | indep (one user) | opt-in env `GGML_CUDA_REGISTER_HOST=1` | **free experiment**; also needs `--no-mmap` per PR #21067 note ("unpinned weights serialize") |
| **Weight prefetch, double buffer (PR #21067)** | scheduler prefetches layer n+1's override tensors on a copy stream (events) while layer n computes; `--prefetch-weights`, requires `--no-mmap` | author: Qwen3.5-27B Q4_K_M, FFN on CPU (`-ot "ffn_(gate\|up\|down).*=CPU"`), gains at all ubatch sizes, shrinking with ubatch (graphs only, GPU not named in text); noonghunna 2x RTX 3090 MoE run (table below); crusaderky RTX 3090 PCIe 3.0: **150 -> 95 tok/s (worse)** at ubatch 2048 [V [PR](https://github.com/ggml-org/llama.cpp/pull/21067), [blog](https://am17an.bearblog.dev/prefetching-weights-in-llamacpp/)] | PR (self-reported + 3rd-party comments) | **no** (open since 2026-03-27, 1 approval) | **M**; needs scheduler events - the *same* mechanism as our Strata patch 0004 ("per-split events"); reserves ~15% of VRAM for the double buffer in one report [V]; rebase-risky |
| **FlexGen** | LP search over weight/KV/activation placement, 4-bit compression, large batch for throughput | 1 tok/s on OPT-175B with one 16 GB GPU at effective batch 144; "100x" claim [U, not on the abstract] [V [arXiv](https://arxiv.org/abs/2303.06865)] | vendor | no | throughput (batch) oriented; irrelevant for single-stream latency |
| **vLLM prefetch offloader** | groups layers, async H2D prefetch N layers ahead, static buffers for CUDA-graph | docs only, **no numbers**; backend `prefetch` if `offload_group_size > 0` else UVA [S [docs](https://docs.vllm.ai/en/v0.22.1/api/vllm/model_executor/offloader/prefetch/)] | docs | n/a | confirms the design in #21067 is the industry pattern |
| **prima.cpp** (llama.cpp fork) | pipelined-ring parallelism overlapping disk I/O with compute; Halda scheduler; mmap prefetch-release conflict fix | 70B at 674 ms/token with < 6% memory pressure; "5-17x lower TPOT" vs llama.cpp/exo/dllama on 4 home devices [V [arXiv](https://arxiv.org/abs/2504.08791)] | vendor (multi-device cluster) | no | relevant only when RAM < model (SSD streaming); not our 63 GB case |

PR #21067 third-party table (Laguna-S-2.1 117.6B IQ4_NL, 2x RTX 3090, 28 expert layers on CPU, 262k ctx; MoE, so only an upper-bound on dense relevance) [V]:
prefill 10K 902.5 -> 1035.3 tok/s (+14.7%), prefill 90K 734.2 -> 835.7 (+13.8%), TTFT short prompt 1085 -> 1604 ms (+48%), decode 38.40 -> 36.55 (-4.8%).
Author's own expectation: **dense models benefit most** because the transfer hides behind per-layer compute when `C_n > T_{n+1}`; benefit shrinks as compute grows relative to transfer and on slow links.

Partial-offload tuning rules distilled [E, from section 2 plus the sources above]: (1) `token_embd` on CPU always; (2) keep layer ranges contiguous; (3) put the output head on GPU
before any FFN tensor if VRAM allows (per-byte equal in decode, but the head is also read per MTP draft step); (4) tune `-ub`/min-batch per model since prefill transfer vs compute flips with link speed
(PCIe 3.0 loses with prefetch, PR data); (5) reclaiming compute-buffer VRAM (smaller ubatch) to move the 0.63 GiB head onto the GPU is a trade between prefill speed and decode speed that must be measured.

---

## 5. Topic 3 - KV cache

| Idea | Mechanism | Numbers | Source type | In checkout? | Port assessment |
|---|---|---|---|---|---|
| **Rotated quantised KV** (Hadamard on Q/K/V, PR #21038) | outlier-spreading rotation lets q4_0 KV be accurate | Qwen3-8B wikitext ppl (f16 7.3203): q4_0 7.6451 -> **7.5012**, q8_0 7.3172 -> 7.3195; Qwen3.5-9B KLD q4_0/q4_0 0.00798 -> 0.00506 (later 0.00478); gpt-oss-20b AIME25 q4_0 2.0% -> 21.7%; TG 0.88x at ctx 0, 0.93x at 32K on an early commit [V [PR](https://github.com/ggml-org/llama.cpp/pull/21038)] | PR | **yes** (merged 2026-04-01) | already in use by telecode (q4_0/q4_0); nothing to port. Independent check on a 5090: no measurable loss at f16/q8_0/q4_0 KV at 32K on Qwen3.8-27B ([KGP Talkie](https://kgptalkie.com/tutorials/llm-benchmarking/qwen-3-8-27b-llama-cpp-speed-settings) [S], single author) |
| **TurboQuant-style sub-4-bit KV** | rotation + vector quantisation, forks | tests on Qwen3.6-27B/32B show path-preservation drop for turbo3, crashes with turbo4 on some configs ([disc. #20969](https://github.com/ggml-org/llama.cpp/discussions/20969)) [S] | community | not merged | skip |
| **KV on host (`-nkvo`)** | all KV in RAM, attention on CPU | n/a | code | yes | all-or-nothing; PCIe not involved but CPU attention bandwidth-bound |
| **StreamingLLM** | attention sinks + sliding window | up to 22.2x vs sliding-window recompute; 4M-token streams; **does not extend usable context** (no recall of evicted text) [V [arXiv](https://arxiv.org/abs/2309.17453)] | vendor | no | lossy for retrieval; not suitable for coding agents |
| **H2O** | keep 20% heavy-hitter + recent tokens | up to 29x vs DeepSpeed Zero-Inference/Accelerate, 3x vs FlexGen (OPT-6.7B/30B), 1.9x latency [V [arXiv](https://arxiv.org/abs/2306.14048)] | vendor | no | lossy eviction |
| **SnapKV** | per-head clustering of important prompt positions from an observation window | small accuracy drop at up to 380K tokens (HF impl.) [S [arXiv](https://arxiv.org/abs/2404.14469)]; Llama-3.1-8B LongBench avg: full 42.70, SnapKV 34.57, H2O 32.72, StreamingLLM 27.12 [S [DASH-KV table](https://arxiv.org/pdf/2604.19351)] | vendor + 3rd-party table | no | eviction loses ~8 LongBench points in that table; a prompt-compression mode, not lossless |
| **Quest** | query-aware page selection: load only top-k KV pages per head | abstract: "up to 2.23x self-attention speedup, which reduces inference latency by 7.03x", negligible loss [V [arXiv](https://arxiv.org/abs/2406.10774)] (the abstract's wording is odd; read the paper before quoting) | vendor | no | needs per-page min/max metadata + selective gather kernel |
| **ShadowKV** | low-rank K on GPU, V offloaded to CPU, sparse reconstruct | up to 3.04x throughput, 6x batch (A100) [S [ICML page](https://icml.cc/virtual/2025/poster/44053)] | vendor | no | L |
| **InfiniGen / RetrievalAttention / PQCache** | KV in CPU memory, speculative prefetch of critical entries | no decode speed numbers surfaced in this search [S [survey](https://arxiv.org/html/2603.20397v1)] | - | no | L |
| **DuoAttention** | retrieval heads keep full KV, streaming heads keep sink+window | memory 2.55x (MHA) / **1.67x (GQA)**, decode 2.18x / **1.50x (GQA)**, prefill 1.73x / 1.63x; 3.3M ctx on one A100 (Llama-3-8B + quant) [V [arXiv](https://arxiv.org/abs/2410.10819)] | vendor | no | needs a calibration pass to classify heads (optimisation on synthetic data) and a per-head KV length in the cache layer; M-L |
| **Chunked prefill** | ubatch-sized slices | already how llama.cpp prefills | code | yes | n/a |

Port view [E]: for **Qwen3.8-27B (hybrid)** KV is 1.4 GB at 80k, so none of this matters. For a classic GQA dense 32B (e.g. 64 layers, 8 KV heads, head dim 128 [U config from memory]) KV is ~74 KB/token at q4_0 ->
**~5.9 GB at 80k**, which *is* a VRAM problem; there the only lossless-ish lever is DuoAttention/Quest-class selection or putting part of the layers' KV on the CPU with their attention (needs per-layer `-nkvo`, absent).
Expected gain only for that model class; rank 7.

---

## 6. Topic 4 - speculative decoding for dense models (2026)

| Method | Mechanism | Measured speedups (baseline, hardware) | Source type | In checkout? | Notes for offload |
|---|---|---|---|---|---|
| **MTP** (PR #22673) | model's own NextN block drafts | Qwen3.6-27B Q6_K RTX 3090+3060: 22.97 -> **42.45** (n=3, acc 76%); Q4_K_M 3x3060: 18.51 -> 32.24; RTX 5090 q4_0: 63.72 -> 105.47; DGX Spark Q8_0 ~7 -> 14-22; 35B-A3B MoE on a 6 GB laptop only 22.92 -> 29.39 (n=2 best); prefill 1315 -> 665 tok/s; +2.49 GiB VRAM in one run; needs `--parallel 1` [V [PR](https://github.com/ggml-org/llama.cpp/pull/22673)] | PR (3rd-party posts) | **yes** | dense gains 1.7-2.4x; our model's MTP head reads the **CPU** output head every draft step (section 1) |
| **EAGLE-3** (#18039, merged 2026-06-12) | 1-layer draft on target hidden states, optional reduced vocab | RTX A6000: Llama-3.1-8B BF16 44.5 -> 146 (3.28x), Q4_K_M 121.5 -> 274 (2.26x); **Llama-3.3-70B Q4_K_M 15.6 -> 33-38 (2.14-2.41x)**; Qwen3-8B 2.17x; Qwen3-14B 1.46x; **Qwen3-32B Q4_K_M 32.0 -> 40-41.5 (1.24-1.30x, acceptance ~40%)**; MoE ~1.0-1.4x [V [PR](https://github.com/ggml-org/llama.cpp/pull/18039)] | PR (author) | yes (`docs/speculative.md` lists 70B, Qwen3-32B, gemma-4-31B drafts) | speedup tracks acceptance; the 70B fits in 48 GB here, so these are *not* offload numbers |
| **DFlash** (#22105, merged 2026-06-28) | block-diffusion draft emits a whole block per forward | Qwen3.6-27B Q4_K_M DGX Spark: 12.57 -> **33.76 (2.69x)**, acc 0.25; Qwen3-8B BF16 51.9 -> 419 (8.08x, quicksort prompt); trip-planning prompt only 0.93-1.49x; MoE gpt-oss-20b 0.7-1.3x [V [PR](https://github.com/ggml-org/llama.cpp/pull/22105)]. Paper: "over 6x lossless", "up to 2.5x over EAGLE-3" (hardware/models not on the abstract page) [V [arXiv](https://arxiv.org/abs/2602.06036)] | PR + vendor | yes (`draft-dflash`; hybrid targets need recurrent-state checkpoints, extra target pass per rejection) | prompt-dependent; code/repetitive text best |
| **DSpark** (#25173) | DFlash + low-rank Markov head | llama.cpp lists `draft-dspark`; only Qwen3-backbone drafts supported; no speed table opened [S [PR](https://github.com/ggml-org/llama.cpp/pull/25173)] | - | yes (docs) | unverified |
| **ngram-mod / map** | history lookup, no model | docs: good for code rewrite, reasoning that repeats, summarisation | docs [V] | yes | free; stack with a model-based drafter |
| **SpecExec** (NeurIPS 2024) | huge draft *trees* (hundreds of tokens) verified in one pass, built for offloaded targets | Llama-2-70B on consumer GPU + RAM offload: **4-6 tok/s at 4-bit, 2-3 tok/s at 16-bit**, i.e. up to **10.6x / 18.7x** over autoregressive offloaded decoding; ~20 accepted tokens per iteration [S [arXiv](https://arxiv.org/abs/2406.02532), summaries] | vendor | no tree verification in llama.cpp (only `p_split` param seen; its use not verified) | the paper's regime is exactly "70B weights mostly in RAM"; numbers are paper-only |
| **FR-Spec** (ACL 2025) | draft restricted to a frequency-ranked vocabulary subset; LM head is the draft bottleneck | LM-head compute -75%, avg **1.12x over EAGLE-2**, 1.08x on Medusa; verification unchanged (distribution preserved); static ranking; [repo](https://github.com/thunlp/FR-Spec) [S [arXiv](https://arxiv.org/abs/2502.14856)] | vendor | partial: reduced `d2t` vocab exists for EAGLE-3/DFlash *checkpoints*, not as a runtime subset for MTP | the idea behind rank 1 |
| **Adaptive draft length** | choose length maximising expected tokens/ms from measured acceptance and round time | ours, patch 0003 (`--spec-draft-adaptive`) | code [V] | yes (ours) | already cost-based; does not know the draft head sits on the CPU, but measures its time implicitly |

Why speculation is the strongest lever for *non-fitting* models [E]: the verify pass reads each weight once for k tokens. For the CPU-resident part, rows 1..k of a small batch are still memory-bound on a CPU
until compute catches up (~tens of tokens), so tokens/s scales with accepted length almost linearly - which is also why the 70B Hardware-Corner baseline (2 tok/s) is the case where EAGLE-3/DFlash/ngram pay most.
Independent confirmation on *offloaded* dense models in llama.cpp: **none found** (all PR tables are fully-resident models); this must be measured (use `--spec-synth-len`, which replaces verification by synthetic acceptance, to isolate cost from acceptance: `docs/speculative.md` [V]).

---

## 7. Topic 5 - CPU kernels and cores

- **Decode GEMV is DRAM-bound**; kernel work only helps if the kernel is below the roofline. Prefill (batch >= 64 rows) is compute-bound: the **tiled** k-quant matmul (#27851, AMD 9950X3D 8 threads, Qwen3.5-27B Q5_K_XL pp2048)
  gave **16.34 -> 67.10 tok/s (4.1x) on VNNI, 14.98 -> 25.36 (1.7x) on AVX2**, decode unchanged (2.89 -> 2.90), GEMV at 0.78-0.84x so it falls back below 64 rows [V [PR](https://github.com/ggml-org/llama.cpp/pull/27851)]. Our checkout's gate also accepts IQ types
  (`tiled.cpp:617-629`) -> relevant to IQ3_XXS CPU prefill, **unbenchmarked**. Arrow Lake's VNNI situation: the repo builds AVX-VNNI (`CMakeLists.txt:338`) but whether this CPU exposes AVX-VNNI is [U].
- **Repack:** x86 repack covers only `IQ4_NL` among IQ types (`repack.cpp`). The telecode head is `IQ4_XS`, so its decode GEMV is the generic AVX2 `ggml_vec_dot_iq4_xs_q8_K` path [E from grep]. A repack-capable type
  (IQ4_NL, Q4_0) for the head is a quick experiment (item 5).
- **ik_llama.cpp** ships CPU-oriented interleaved quants (`_R4`, `_R16`), "much faster CPU prompt processing" (no numbers on the README), run-time repack `-rtr` (warned against for hybrid CPU/GPU), Hadamard K/V cache,
  auto-fit, offload-policy control (MIT) [V [README](https://github.com/ikawrakow/ik_llama.cpp)]. Independent measurements are **inconsistent**: 70B Q4 on RTX 3090: IQ4_NL pp 138 vs 133 tok/s (equal), UD-Q4_K_XL pp **14.6 vs 123** (ik slower), TG ~equal (2.1-2.3) [V Hardware Corner]; MoE reports 1.5-2x slower in ik ([#1699](https://github.com/ikawrakow/ik_llama.cpp/issues/1699) [S]); a laptop-2060 user found it slower for a Gemma-3 12B partial offload ([#258](https://github.com/ikawrakow/ik_llama.cpp/discussions/258) [S]).
  Verdict: not a drop-in win for dense partial offload.
- **AMX** (`ggml-cpu/amx/`): no AMX on this laptop CPU [U]; skip.
- **Hybrid P/E cores:** telecode already pins decode to a hand-picked 8-core mask (`0xc03c03` = logical CPUs 0,1,10,11,12,13,22,23) and batch to all 24 - an unusual mask whose origin is not documented in the repo; I could not verify
  which logical CPUs are P-cores on this Windows install. General evidence is weak: one non-hybrid i7-8700 peaked at 8 of 12 threads ([InventiveHQ](https://inventivehq.com/blog/cpu-thread-scaling-llm-inference) [S]); no Arrow-Lake benchmark found.
  Action: a per-thread-count sweep (4/6/8/12/16) with the head as the only CPU work, plus `GetSystemCpuSetInformation` EfficiencyClass to confirm the mask.

---

## 8. Topic 6 - competitor engines for a dense model that does not fit

| Engine | Dense non-fitting story | Independent evidence | Licence |
|---|---|---|---|
| llama.cpp (stock) | layer split + op-offload; speculation | Hardware Corner 70B: 2.0-2.3 tok/s (3090 + DDR4) [V] | MIT |
| ik_llama.cpp | same architecture + IQK quants | inconsistent (section 7) | MIT |
| PowerInfer | hot/cold neurons, ReLU models only | vendor numbers only; stale model list (Falcon-40B, Llama-2, ProSparse, Bamboo) [V repo]; 2026 news is SmallThinker/Tiiny hardware, "20 tok/s gpt-oss-120B" is a vendor claim | MIT |
| KTransformers | MoE-only design: AMX/AVX512 expert kernels, expert deferral; "dense layers" run on GPU (SGLang integration) | no dense offload path found ([repo](https://github.com/kvcache-ai/ktransformers), [paper page](https://madsys.cs.tsinghua.edu.cn/publication/ktransformers-unleashing-the-full-potential-of-cpu/gpu-hybrid-inference-for-moe-models/), [SGLang #11425](https://github.com/sgl-project/sglang/issues/11425)) [S] | Apache-2.0 [U] |
| prima.cpp | multi-device, disk-streaming pipelines | vendor 5-17x vs llama.cpp on clusters | not checked |
| vLLM prefetch offloader / UVA | GPU-server oriented, async layer prefetch | docs only | Apache-2.0 [U] |
| FlexGen / DeepSpeed ZeRO-Inference / HF Accelerate | throughput-oriented offload | H2O paper: these are 3-29x slower than a KV-eviction variant (OPT) [V] | various |
| mistral.rs, MLC-LLM, exllamav3 | nothing relevant found for CPU-offloaded dense benchmarks; exllamav3 offload support [U] | none | - |
| Q-Infer (ACM 2025) | GPU-CPU sparsity-aware scheduling following PowerInfer | abstract-level only ([ACM](https://dl.acm.org/doi/full/10.1145/3764589)) [S] | - |

Conclusion: **no engine found beats stock llama.cpp for a dense model with a large CPU-resident share** at batch 1 without changing bytes-per-token (sparsity) or tokens-per-read (speculation).

---

## 9. Ranked list of the 8 best things to implement (with reasoning)

1. **Offload-aware speculative drafting: GPU draft-vocab subset for the MTP / EAGLE-3 / DFlash head.**
   Why first: it removes the dominant cost in the exact config we run (draft passes read 675 MB from DRAM each), output stays exact (verification uses the full head), effort is small, it is off by default, and it composes with our adaptive policy.
   Gain [E]: model below. Risk: low. Rebase surface: ~200 lines (new tensor member, one graph helper, one loader hook, 3 flags).
2. **Placement autotuner in telecode (no llama patch).** A benchmark-driven search over: head on GPU vs CPU (reclaiming compute-buffer VRAM via a smaller ubatch), contiguous GPU layer range, `-ot` fractions, `-ub`, `GGML_OP_OFFLOAD_MIN_BATCH`, `GGML_CUDA_REGISTER_HOST`, thread count/mask. Uses existing `gguf_info.py` / `vram.py` for sizing and `llama-bench` per candidate; saves the winner in `settings.json`. Effort S, pure Python + tray widget.
3. **Async weight prefetch + per-split events** (carry PR #21067, reimplemented on the scheduler events we need for Strata patch 0004). One scheduler change serves both MoE (CPU/GPU overlap) and dense (prefill streaming). Do after measuring that prefill is actually PCIe-limited here (the PR is negative on PCIe 3.0).
4. **Wide / tree verification with EAGLE-3 or DFlash drafts for 32B-70B partial offload**, calibrated by `--spec-synth-len`. This is where the roofline can be beaten by a large factor; requires measuring where CPU compute stops being free (batch ~16-64 rows on 8 P-cores [E]).
5. **CPU head kernel path:** requantise `output.weight` from the BF16/original to IQ4_NL/Q4_0 (repackable) or add an IQ4_XS repack/GEMV; verify against ppl (the head is quality-sensitive). Cheap offline experiment first.
6. **Activation sparsity for the CPU-resident FFN** (gate-derived mask, up-row skip, transposed neuron-major `down` built by a converter), thresholds calibrated per layer; only for 32B-70B and batch 1-4 (section 3.3).
7. **Query-aware KV selection / DuoAttention-style head split** for full-attention dense models whose KV exceeds VRAM.
8. **Hot/cold neuron cache for natively sparse models** - only if a >= 30B ReLU-family GGUF becomes interesting; otherwise do not build.

Not recommended: ReLU-fication of existing models (10B+ token training), TurboQuant-class sub-4-bit KV, eviction methods (StreamingLLM/H2O/SnapKV) for agentic coding, switching engines.

---

## 10. Design sketch of item 1: `draft-vocab` subset head

### Problem
`graph_mtp` (`src/models/qwen35.cpp:655-661`) multiplies the draft hidden state by `model.output` (IQ4_XS, 248,320 x 5,120, **0.63 GiB on the CPU** in telecode's config). Every draft step pays a DRAM-bound CPU mat-vec of ~8-11 ms [E].

### Model-based estimate [E; assumptions: GPU weights 9.4 GB at ~75% of 672 GB/s = 18.6 ms/verify, CPU head 9.5 ms per pass (any batch <= 8), MTP block 1 ms, acceptance 0.76/0.58/0.44 for drafts 1/2/3]
| Variant | Round time | Tokens/round | tok/s |
|---|---|---|---|
| no speculation | 18.6 + 9.5 = 28 ms | 1 | ~35 |
| MTP n=3, CPU head for drafts (today) | verify 28 + 3 x (1 + 9.5) = 60 ms | 2.78 | ~46 |
| MTP n=3, GPU subset head (N=32768, 87 MB, ~0.15 ms) | 28 + 3 x 1.2 = 32 ms | 2.78 | ~87 |
The two outer rows are consistent with the PR's reported ~1.7-2.4x for fully-resident cards; the middle row (only ~1.3x over baseline) is the prediction that the CPU head erodes MTP here. **Everything above is a prediction to be replaced by a profile** (first measurement: tg with and without `--spec-type draft-mtp` plus `--spec-synth-len 2.5`).

### Changes
1. **Model/loader** (`src/llama-model.h`, `llama-model.cpp`, hook after tensors are loaded): if `spec_draft_vocab > 0`, the arch has MTP (`nextn_predict_layers > 0`) and `model.output` lives in a host buffer, create
   `model.output_draft` ([n_embd, N], **same ggml type**, allocated in the MTP block's device buffer type) and `model.draft_ids` (I32 [N]). Fill by **row memcpy**: all ggml quant types are row-wise (a row of n_embd = 5120 elements is 20 whole IQ4_XS blocks), so
   `dst[i] = src[ids[i]]` is bit-exact, no dequantisation. Skip silently if `output` is already on the GPU (log why).
2. **Graph** (`graph_mtp::build`, factored into one helper `build_mtp_head()` in `llama-graph.cpp` so the other MTP archs - qwen35moe, GLM5Next, DSV4 - reuse it): `logits_sub = build_lora_mm(output_draft, cur)` [N, n_out]; scatter into a full-vocab tensor
   pre-filled with -inf using `draft_ids` (the same pattern as `dflash.cpp:826`), assign to `res->t_logits`. Samplers and `common/speculative.cpp` see an ordinary n_vocab logits row, so no change to draft-token handling. **Only the MTP graph type uses it; the target (verify) pass keeps the full head -> output is identical** under greedy sampling and distribution-preserving under probabilistic sampling (draft probabilities q are the subset softmax, which is what the draft actually sampled from; cf. `1fb7ef3e3`).
3. **Ranking source**: `--spec-draft-vocab-mode first|file|observed`:
   - `first` (default when N set): ids 0..N-1 (byte-level BPE merge order ~ frequency [U; validate on the Qwen vocab by measuring subset coverage on telecode chat logs]).
   - `file`: `--spec-draft-vocab-file ranked_ids.i32` produced by a telecode tool that tokenises the user's own transcripts via the proxy's `/tokenize` and counts tokens.
   - `observed` (phase 2): count tokens actually emitted by the target during the session and re-copy rows every K rounds (an 87 MB device upload, off the critical path).
   Always union special/EOS tokens.
4. **Same for EAGLE-3/DFlash** drafts that ship a full-vocab head: reuse `d2t` machinery - no new work beyond allowing `output_draft` for models whose draft head is large and on the CPU.
5. **Interaction with patch 0003**: `--spec-draft-adaptive` already measures the time of rounds of each length; with a cheaper draft step the optimum shifts to longer drafts automatically. No change needed, but add `n_draft_vocab` to its debug line.

### Flags (llama.cpp)
`--spec-draft-vocab N` (0 = off, env `LLAMA_ARG_SPEC_DRAFT_VOCAB`), `--spec-draft-vocab-mode`, `--spec-draft-vocab-file`. Off by default; byte-identical behaviour when off (rule 1 in `docs/strata-port.md`).

### telecode knobs
- `llamacpp/argv.py` `_MODEL_FLAG_SPECS`: `spec_draft_vocab` (`value_nz`), `spec_draft_vocab_mode`, `spec_draft_vocab_file` (`path`) per model.
- Tray model page: a number box + mode combo placed under `spec_type`, greyed by `_dependent(row, ["…spec_type"], lambda v: any(t in v for t in ("draft-mtp","draft-eagle3","draft-dflash")))`; read-only hint showing the extra VRAM (`N x n_embd x bpw / 8`, e.g. 87 MB at N=32768), computed from `gguf_info.py` and fed into `vram.py` so the fit estimate accounts for it.
- A "build draft vocabulary" action in the tray (tokenise transcripts from `data/` -> ranked ids file). Persist the path in settings.
- Patcher: new top-level patch `0004-...` (numbering to be reconciled with the Strata plan's 0004 scheduler-events patch); `patcher.py` needs no change.

### Test plan
1. Baseline profile (no patch): tg for `none`, `draft-mtp n=1..4`, `--spec-synth-len 2.0/2.5` to separate acceptance from cost; capture CPU vs GPU time per round (Nsight or `GGML_SCHED_DEBUG`).
2. Patch on, N in {8192, 16384, 32768, 65536}: greedy token-identity vs flag off (must be exact), draft-token coverage (fraction of target tokens inside the subset), acceptance delta, tokens/s on three prompt classes (code, chat, long reasoning), VRAM delta.
3. Failure modes: output already on GPU (feature must no-op), unified-KV/`--parallel 2` (MTP currently needs `--parallel 1` per the PR comments [V]), ctx-checkpoint restore paths.
4. Success criterion: >= 1.25x over today's MTP tokens/s at identical outputs; otherwise drop (the model above says it should be ~1.8x, but it is a model).

---

## 11. Open measurements before anything is built (none was run here)

| Question | Why it matters | Cheapest measurement |
|---|---|---|
| Real DRAM read bandwidth on the 8-core decode mask | sets the CPU head cost (8 vs 11 ms) and every [E] above | `llama-bench -ngl 0` on a small Q4_0 model with thread sweep |
| Negotiated PCIe link and H2D bandwidth | decides whether prefetch (rank 3) can win | pinned `cudaMemcpy` microbenchmark / `nvidia-smi -q` link fields |
| Fraction of token time in the CPU head, with and without MTP | validates rank 1 | per-op timing (`GGML_SCHED_DEBUG`, `--spec-synth-len`) |
| Compute-buffer size at ubatch 2048 vs 1024 vs 512 | whether the head can move to the GPU for free | `--fit-print` / load log |
| CPU instruction set (AVX-VNNI? AVX-512?) | tiled prefill and kernel choice | `llama-server --list-devices` / system_info line |
| P/E-core identity of mask `0xc03c03` | thread-pinning correctness | `GetSystemCpuSetInformation` |
| Quality of 3-bit weights + 25-40% input sparsity | rank 6 go/no-go | ppl on a held-out transcript set with a TEAL-style mask in a Python harness |

---

## 12. Sources

Local code and data (verified): `D:\Projects\Telecode\build\llama.cpp` (`common/fit.cpp`, `common/arg.cpp`, `common/common.h`, `docs/speculative.md`, `ggml/src/ggml-cuda/ggml-cuda.cu`, `ggml/src/ggml-backend.cpp`, `ggml/src/ggml-cpu/{tiled,repack.cpp}`, `src/models/{qwen35,gemma3n,eagle3,dflash}.cpp`, `src/llama-kv-cache.cpp`), `D:\Projects\Telecode\settings.json`, `data\logs\llama.log.prev`, `patches\llama.cpp\0002-*.patch`, `0003-*.patch`, `docs\strata-port.md`, `docs\strata-parity.md`, GGUF header via `llamacpp/gguf_info.py`.

External (URL per claim above): Strata <https://github.com/Niko1221/Strata>; PowerInfer <https://arxiv.org/abs/2312.12456>, <https://github.com/Tiiny-AI/PowerInfer>; PowerInfer-2 <https://arxiv.org/abs/2406.06282>;
LLM in a Flash <https://arxiv.org/abs/2312.11514>; Deja Vu <https://arxiv.org/abs/2310.17157>; CATS <https://arxiv.org/abs/2404.08763>; TEAL <https://arxiv.org/abs/2408.14690>, <https://arxiv.org/html/2408.14690v1>;
R-Sparse <https://arxiv.org/abs/2504.19449>; ProSparse <https://arxiv.org/abs/2402.13516>; TurboSparse <https://arxiv.org/abs/2406.05955>; SparseInfer <https://arxiv.org/abs/2411.12692>; Prox <https://arxiv.org/abs/2607.27591>;
Sparsing Law (states llama.cpp computes FFNs densely) <https://arxiv.org/abs/2411.02335> [S]; llama.cpp PRs #21038 rotation <https://github.com/ggml-org/llama.cpp/pull/21038>, #21067 prefetch <https://github.com/ggml-org/llama.cpp/pull/21067>,
#26563 expert cache (closed) <https://github.com/ggml-org/llama.cpp/pull/26563>, #29887 MoE cache <https://github.com/ggml-org/llama.cpp/pull/29887>, #27851 tiled matmul <https://github.com/ggml-org/llama.cpp/pull/27851>, #22105 DFlash <https://github.com/ggml-org/llama.cpp/pull/22105>,
#18039 EAGLE-3 <https://github.com/ggml-org/llama.cpp/pull/18039>, #22673 MTP <https://github.com/ggml-org/llama.cpp/pull/22673>, #25173 DSpark <https://github.com/ggml-org/llama.cpp/pull/25173>, issue #25859 <https://github.com/ggml-org/llama.cpp/issues/25859>;
prefetch blog <https://am17an.bearblog.dev/prefetching-weights-in-llamacpp/>; HF MoE offload guide <https://huggingface.co/blog/Doctor-Shotgun/llamacpp-moe-offload-guide>; Hardware Corner <https://www.hardware-corner.net/guides/llama-cpp-vs-ik_llama-cpp/>;
ik_llama.cpp <https://github.com/ikawrakow/ik_llama.cpp>; DFlash <https://arxiv.org/abs/2602.06036>; SpecExec <https://arxiv.org/abs/2406.02532>; FR-Spec <https://arxiv.org/abs/2502.14856>; prima.cpp <https://arxiv.org/abs/2504.08791>; FlexGen <https://arxiv.org/abs/2303.06865>;
vLLM offloader <https://docs.vllm.ai/en/v0.22.1/api/vllm/model_executor/offloader/prefetch/>; Quest <https://arxiv.org/abs/2406.10774>; H2O <https://arxiv.org/abs/2306.14048>; StreamingLLM <https://arxiv.org/abs/2309.17453>; SnapKV <https://arxiv.org/abs/2404.14469>;
DuoAttention <https://arxiv.org/abs/2410.10819>; ShadowKV <https://icml.cc/virtual/2025/poster/44053>; DASH-KV table <https://arxiv.org/pdf/2604.19351>; KV survey <https://arxiv.org/html/2603.20397v1>; KTransformers <https://github.com/kvcache-ai/ktransformers>;
SGLang #11425 <https://github.com/sgl-project/sglang/issues/11425>; Q-Infer <https://dl.acm.org/doi/full/10.1145/3764589>; Notebookcheck RTX 5070 Ti Laptop <https://www.notebookcheck.net/Nvidia-GeForce-RTX-5070-Ti-Laptop-Benchmarks-and-Specs.934945.0.html>,
Core Ultra 9 275HX <https://www.notebookcheck.net/Intel-Core-Ultra-9-275HX-Processor-Benchmarks-and-Specs.943094.0.html>; KGP Talkie <https://kgptalkie.com/tutorials/llm-benchmarking/qwen-3-8-27b-llama-cpp-speed-settings>; TurboQuant discussion <https://github.com/ggml-org/llama.cpp/discussions/20969>;
InventiveHQ <https://inventivehq.com/blog/cpu-thread-scaling-llm-inference>.

Discrepancies noticed: PR #29887 merge date appears as Oct 3 in one fetch and Oct 7 in another (both 2026; our checkout contains it). PowerInfer-2's "29x" appears in secondary posts; the arXiv abstract says 27.8x (used here).
The Quest abstract's 2.23x / 7.03x wording is as quoted by the page and looks inverted relative to usual reporting; confirm in the PDF before citing.
