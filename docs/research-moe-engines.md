# Research: MoE engines for one consumer GPU with experts in system RAM

Date of research: 2026-10-14. Method: web search + page fetches (not run, not benchmarked by us). Every claim has a URL; where a page could not be read in full (PDF dumps, truncated threads) that is said. Items marked **[memory]** come from prior knowledge, with an arXiv URL I did not re-open in this session.

## 0. Executive summary

- **Nothing I found is verifiably better than Strata on a 12 GB GPU + 64 GB RAM for Qwen3.8-Flash-Next.** The only head-to-head numbers are mostly Strata's own, or single-user reports. Mainline llama.cpp (with the newly merged expert cache) is the closest functioning alternative, and it is far behind Strata's headline decode numbers on the same class of hardware: 25 -> 39-41 tok/s on an RTX 4090 vs Strata's claimed 100-165 tok/s (different quants, though: Q4_0 vs 2-3 bit).
- **Correction to the brief:** Strata is not purely "its own C++/CUDA engine". Its README credits "parts of llama.cpp / ggml" and ships `third_party/ggml` (https://github.com/Niko1221/Strata). It is a ggml-derived engine with its own scheduler, cache, quants and server. That makes porting its ideas into llama.cpp more plausible than if it were fully from scratch.
- **The decode gap is mostly quantization + lossy routing, not only engineering.** Strata's headline numbers use Q2_0 / IQ2_XS (2-bit) weights and, since v0.1.42, `STRATA_ROUTE_TAIL_SKIP=7` (skip low-importance non-resident experts, changes answers, KL 0.0058, top-1 match 97.7% on an RTX 3060) (https://smartscope.blog/en/blog/strata-local-llm-engine-qwen3-8-flash-next-2026/). Compare like for like before concluding llama.cpp is "slow".
- Strata was made public 2026-09-24 (same SmartScope article), has 24.2k stars, 216 open issues (https://github.com/Niko1221/Strata). Quality complaints exist (see 1.3).

## 1. Strata (baseline)

### 1.1 Facts
| Item | Value | Source |
|---|---|---|
| License | MIT (models have their own licenses; Qwen Community License 1.0) | https://github.com/Niko1221/Strata , https://www.developersdigest.tech/blog/qwen-3-8-flash-next-strata-run-locally-2026 |
| Stack | C/C++ engine, CMake, backends for CUDA/HIP (not named on page), Metal, SYCL; Python tooling; uses parts of llama.cpp/ggml | https://github.com/Niko1221/Strata |
| Maturity | 24.2k stars, 2.4k forks, 216 open issues, 2,174 commits; public since 2026-09-24; users report crashes/reboots on some AMD setups (#872), illegal memory access on long prompts (#1468), loops (#728) | repo page; SmartScope |
| Hardware | NVIDIA RTX 20-50, AMD RX 6800+/9060 XT+/R9700; >=12 GB VRAM; 32 GB RAM minimum, 64 GB for all sizes (one aggregator says 64 GB is required: https://aiweekly.co/alerts/open-source-strata-engine-runs-125b-qwen38-flash-next-on-a-single-12gb-consumer) | repo page |
| Model | Qwen3.8-Flash-Next 125B MoE: 24,576 experts, 10 active per token | repo page |
| Quants | Q2_0, IQ2_XS, IQ3_XXS, IQ3_S (Strata builds), expert-halved "Coder", plus Unsloth UD-IQ4_XS..Q8_0 GGUF | repo page |

### 1.2 Techniques (as documented)
- Auto-sized GPU expert cache (about 3,700 experts on RTX 5070 Q2_0); RAM holds all experts; SSD for overflow (README; https://newreleases.io/project/github/Niko1221/Strata/release/v0.1.41).
- CPU computes non-resident experts in parallel with GPU (README).
- MTP speculative decoding, 4/8-bit KV (https://smartscope.blog/en/blog/strata-local-llm-engine-qwen3-8-flash-next-2026/). README claims 1.6-1.8x from speculation.
- Prompt in chunks up to 8,192 tokens with expert streaming; short-prompt CPU share in expert streaming (<1,024 tok) (v0.1.41 notes).
- `STRATA_ROUTE_TAIL_SKIP=7` (default on CUDA since 0.1.42): skips transfer+compute of low-importance non-resident experts; +12.9..15.6% on single GPUs; lossy (SmartScope). Opt-in `STRATA_ROUTE_RESIDENT`: prefers cached experts, KL 0.07 at margin 0.5; +4.1% on RTX 5070 (v0.1.41 notes).
- Multi-group batching, multi-GPU layer split, page-locked staging (off by default: corrupted IQ3_S answers after a 4,096-token prompt) (v0.1.41 notes).
- I could not read DETAILS.md (404 at the guessed path); KV streaming is not described on any page I could read.

### 1.3 Reported numbers (author vs independent)
| Hardware / model | Result | Type | Source |
|---|---|---|---|
| RTX 5070 12 GB, Ryzen 5 7600, 64 GB, Q2_0 | 94 tok/s decode, 2,650 tok/s prompt (4K answers, 32K prompts, engine 0.1.36) | author | README |
| same, IQ3_S | 53 / 1,620 | author | README |
| RX 9070 XT 16 GB, 47 GB RAM, Q2_0 | 60 / 1,160 | author | README |
| RTX 5070: DETAILS.md vs README disagree (93.0 vs 94 decode; 1,299 vs 2,650 prompt) | internal inconsistency | analysis | SmartScope |
| RTX 4090, IQ3_S, v0.1.42 | 164 tok/s decode, 5.2K prefill @16K | single gist, no Strata config shown | https://gist.github.com/ryan4yin/48617bbddacc7067f10799770b7cc33f |
| RTX 4090 + 128 GB DDR5-?, 7950X3D | 124 tok/s | one HN user | https://news.ycombinator.com/item?id=49953495 |
| RTX 5090 | 125 tok/s | one HN user | HN thread |
| RTX 3090, 64 GB DDR4, PCIe x8 | 40-60 tok/s vs ~20 for llama.cpp "standard" | one HN user | HN thread |
| RTX 3080 10 GB, 48 GB RAM, Coder | ~30 tok/s | one HN user | HN thread |
| R9700 32 GB + 96 GB DDR4, PCIe 3 | ~60 tok/s | one HN user | HN thread |
| RTX 3090 ~100-140 tok/s, 5060 Ti ~87 | estimates, explicitly not measured | author | README / SmartScope |
| llama.cpp tuned, RTX 4090 + 96 GB DDR5-5600, IQ3_XXS 82 GB: decode ~30 @6K, ~22 @90K, 19.5 @110K; prefill ~860-1360 | measured, careful methodology, but pre-merge (no expert cache) | independent | the ryan4yin gist above |

Skepticism notes: most Strata speed claims use 2-3 bit quants and a lossy expert-skipping default; independent reporters call IQ3_XXS "significantly degraded" and the Coder build lossy; one user measured vision grounding error 154.8 px vs 46.5 px for llama.cpp on the same GGUF; reasoning reported as very long (all in the HN thread). No independent like-for-like (same quant, same prompt, same output quality) Strata vs llama.cpp comparison exists as of today. The "4-day-old, largely LLM-generated" criticism is in the same thread.

## 2. Ranked candidates (detail)

### 2.1 llama.cpp mainline: `--moe-cache-mib` (PR #29887, #30112) - closest practical alternative
- URLs: https://github.com/ggml-org/llama.cpp/pull/29887 , https://github.com/ggml-org/llama.cpp/pull/30112 . License MIT. C/C++, CUDA/HIP.
- Merged 2026-10-07 (#29887, am17an, approved by ggerganov; port of Tether qvac-fabric cache) and 2026-10-08 (#30112 multi-GPU, global size split by `--tensor-split`).
- Technique: `MUL_MAT_ID` for host-resident experts runs on the GPU from a per-layer-group LRU cache in VRAM; only misses are uploaded over PCIe; applies to batches <= 32 tokens (decode / small draft batches); larger batches use the old offload path. Single device in #29887, multi-GPU in #30112.
- Numbers (author, Qwen3.8-Flash-Next Q4_0, 65.4 GiB experts, EPYC 7742, PCIe 4.0 x16): RTX 4090 25.0 -> 39.4 (1.57x, 72% hit) -> 40.7 with `-cmoe` + 11 GB cache; RTX 5090 30.8 -> 54.5 -> 67.8 (2.2x, 89% hit). 2x4090: 34.78 -> 64.39 decode, prompt 440.7 -> 321.5 (0.73x). Source: the PR threads above; summarized in https://www.developersdigest.tech/blog/llama-cpp-moe-offload-n-cpu-moe-vs-moe-cache-2026 (secondary, did not rerun).
- Independent-ish reports in the thread: R9700 HIP 11.0 -> 27.6 tok/s at 16 GB cache; RTX 5060 Ti 16 GB + 62 GB RAM IQ3_XXS 14.95 -> 19.26 (fork); RTX 3090 only 1-5% over tuned `--n-cpu-moe`; RTX 2080 Ti PCIe3 slower than whole-layer offload; a GLM-5.3-Flash regression 15.1 -> 3.6 when slots < experts per token.
- Weak points: LRU only (reviewers report frequency-admission beats it), no CPU compute of misses in parallel (misses are PCIe-uploaded on the critical path), prefill unaided and can get slower because the cache steals VRAM from `--fit`, possible repetition issue with high top-k (unconfirmed).
- GGUF: yes, native.
- **Verdict: worse than Strata at decode on a 12 GB card with 2-bit weights as claimed; comparable in design philosophy; vastly better on correctness, maturity and maintenance.** Different quant makes direct comparison impossible; it is the only candidate that is lossless and upstream.

### 2.2 llama.cpp unmerged work (ideas, not products)
| Item | What | Numbers / status | URL |
|---|---|---|---|
| Issue #29949 in-graph LRU (`LRU_DECIDE` / `LRU_FILL` graph ops) | cache decisions + copies inside the compute graph, no host readback, works with MTP and dual GPU | author: 7900 XTX 24 GB IQ3_XXS 9.7 -> 37.2 (160 slots, 89.4% hit), 44.8 with MTP; claims 1.5x over #29887 at same budget (20.1 vs 31.2); PPL 1.4290 vs 1.4292; no PR opened, no maintainer reply | https://github.com/ggml-org/llama.cpp/issues/29949 |
| RFC #24528 CPU-compute-misses design | `MUL_MAT_ID` stays on CPU; hits run batched on GPU while CPU threads do misses (the same structure as Strata's "CPU computes misses in parallel") | 4x3090 EPYC: GLM-5.1 754B IQ2_M 13.96 -> 17.49 (+25%), Qwen3.5-397B +7%; prefill drops 14-66% in some setups; author does not expect merge; v2 branch exists; ablation: cache 8%, fusion 9% | https://github.com/ggml-org/llama.cpp/discussions/24528 |
| PR #28414 `--prefetch-experts-slots` | prefill: upload each expert tensor one split ahead on a second stream, rotating staging buffers | -11% to -22% TTFT on author's fork; draft; maintainers sceptical unless it beats mmap page cache; silent wrong output on multi-GPU | https://github.com/ggml-org/llama.cpp/pull/28414 |
| PR #25294 SSD streaming of experts | device slot cache, async I/O, O_DIRECT, wave-partitioned prefill | GB10 128 GB GLM-5.2 254 GB: decode 2.08 vs 0.87 tok/s (2.4x), prefill 5.3x vs mmap; open, flagged as too large | https://github.com/ggml-org/llama.cpp/pull/25294 |
| PR #27861 earlier GPU LRU | superseded by #29887 | - | https://github.com/ggml-org/llama.cpp/pull/27861 |
| PR #26563 `-ehs` hot-expert cache | CUDA-only, open (per a blog, not verified) | - | referenced by https://dev.to/breachprotocol/a-llamacpp-patch-learns-which-experts-to-keep-in-vram-while-you-type-374n |
| Issue #20757 two-tier cache, pluggable eviction | closed as completed; confirmed strong temporal locality | - | https://github.com/ggml-org/llama.cpp/issues/20757 |
| Issue #25859 serial H2D copies in offloaded prefill | documents the prefill bottleneck | - | https://github.com/ggml-org/llama.cpp/issues/25859 |

### 2.3 ik_llama.cpp
- https://github.com/ikawrakow/ik_llama.cpp ; MIT; ~3.3k stars, 489 forks; llama.cpp fork (C/C++/CUDA).
- Relevant: Qwen3.8-Flash-Next support (PR 2365) and MTP for it (PR 2369, standalone MTP head via `-md`); auto-fit of offloaded tensors to VRAM (PRs 1501/1504/1872); hybrid CPU/GPU MoE offload strategy (PR 520); `-ot` tensor overrides (PR 232); fused delta-net for Qwen3-Next/3.5 (PRs 1315+); IQK / trellis quants (IQ2_K..IQ4_KT, `_R4` row-interleaved CPU layouts), Q8_KV; graph split mode for multi-GPU (some users see incoherent output); ngram/suffix self-speculation. No expert VRAM cache listed on the README.
- Hardware: AVX2/AVX512 CPU kernels (this is its CPU strength: faster CPU-side quantized matmul for the experts in RAM), CUDA.
- Numbers: I found no 2026 ik_llama.cpp benchmark on Qwen3.8-Flash-Next with a single consumer GPU. Older guidance: `-fmoe`, `-ot exps=CPU`, `-ser` (https://github.com/ikawrakow/ik_llama.cpp/discussions/258, about 1.5 years old).
- **Verdict: unknown / not shown to beat Strata; potentially complementary** because its better CPU kernels and sub-4-bit quants (IQ2_K, IQ3_K, trellis) raise the RAM-bound baseline that Strata's CPU-compute-of-misses depends on. Needs our own benchmark.

### 2.4 KTransformers (kvcache-ai)
- https://github.com/kvcache-ai/ktransformers ; Apache-2.0; 19.6k stars, 1.6k forks; Python + C++ (kt-kernel) with SGLang integration; PyPI 0.7.0.post4 (2026-09-13) per the search summary of https://pypi.org/project/ktransformers/0.7.0.post4/ .
- Techniques: Intel AMX / AVX512 / AVX2+AVX-VNNI INT4/INT8 CPU kernels (AVX2 backend added March 2026), NUMA-aware expert placement, hot-experts-on-GPU / cold-on-CPU placement, FP8 GPU kernels, 3-tier prefix cache, LoRA fine-tuning. SOSP'25 paper: Expert Deferral (overlap CPU/GPU by deferring some experts' contribution to the next layer; up to 1.45x extra throughput, <= 0.5% avg accuracy drop), 4.62-19.74x prefill and 1.25-4.09x decode over baselines (https://madsys.cs.tsinghua.edu.cn/publication/ktransformers-unleashing-the-full-potential-of-cpu/gpu-hybrid-inference-for-moe-models/).
- Models: DeepSeek V3/R1/V4-Flash, Kimi K2.x, GLM-4/5.x, MiniMax M2.x, Qwen3MoE, Qwen3-Next, Mixtral. **Qwen3.8-Flash-Next not listed** on the repo page (I could not confirm support).
- Numbers on offer are server-class: DeepSeek-R1-0528 FP8 on 8xL20 + Xeon 6454S 227.85 tok/s total (repo). Consumer: DeepSeek-R1 on 24 GB + 382 GB DRAM (Feb 2025). No 12 GB + 64 GB figure found. A 347 tok/s prefill on Qwen3-235B/AMX/4090 appears in an unverified blog.
- GGUF: not first-class (own INT4/INT8 formats, llamafile backend mentioned).
- **Verdict: not comparable for this target.** It is built for big-RAM servers and AMX Xeons (hundreds of GB); a 64 GB desktop is outside its design point. Worse than Strata for "125B on a 12 GB card"; better at large-model, high-RAM, multi-concurrency, and CPU kernels (AMX). Techniques worth borrowing: Expert Deferral, AMX/VNNI kernels.

### 2.5 FreeToken (FlashML-org, UC Berkeley/MIT)
- https://github.com/FlashML-org/FreeToken ; Apache-2.0; 14.5k stars; paper arXiv 2608.16157 (https://arxiv.org/abs/2608.16157); coverage https://www.infoq.com/news/2026/08/freetoken-local-inference/ .
- Techniques: q* policy (split work between CPU cores and GPU by measured interconnect throughput; startup benchmark of PCIe and CPU bandwidth), FTW fast weight format with full-layer double buffering, elastic VRAM manager (KV vs resident experts at runtime, resizable via `ft ctl`), "semantic anchor checkpointing" for agent prompt edits.
- Formats: MXFP4, NVFP4, FP8, BF16 only; models DeepSeek-V4-Flash, Qwen3.6-35B-A3B, GLM-5.2. **No Qwen3.8-Flash-Next, no GGUF, no 2-3 bit quants** -> cannot run the Strata target on 12 GB/64 GB.
- Numbers: 39.3 tok/s Qwen3.6-35B on an 8 GB RTX 4060 laptop (secondary: https://ai-engineering-trend.medium.com/freetoken-open-source-run-35b-moe-models-on-8gb-gpus-at-39-tokens-s-is-local-llm-freedom-finally-29a27541ccb0 ); "3-4x decode, 6-30x prefill vs Ollama/llama.cpp" (InfoQ, authors' paper; commenters dispute baselines). Independent-looking test (https://betterstack.com/community/guides/ai/freetoken-moe-inference/ ): RTX 5090 + 64 GB, Qwen3.6-35B 8-bit (38 GB): Ollama 58.8 vs FreeToken 132.5 tok/s (2.25x); when the 4-bit model fits in VRAM Ollama is faster (239.6 vs 225.3). Only one task, no llama.cpp `--n-cpu-moe` baseline.
- Abstract contains no numbers (https://arxiv.org/abs/2608.16157). Language and last-commit date not visible.
- **Verdict: comparable architecture, narrower model/quant support; unproven against tuned llama.cpp; not able to replace Strata for this model.** Best source of ideas for bandwidth-adaptive scheduling.

### 2.6 Colibrì (JustVugg/colibri)
- https://github.com/JustVugg/colibri ; Apache-2.0; ~41.9k stars; pure C, CUDA/Metal/Vulkan optional; disk/RAM/VRAM tiers; per-layer LRU, learned pinned hot set (`.coli_usage`), router look-ahead prefetch (GLM-5.2 routing 71.6% predictable one layer ahead), O_DIRECT, mirrored SSDs (+37.5% decode).
- Safetensors / own int4/int8, no GGUF. Numbers are small-model or huge-model-on-slow-storage: Qwen3.6-35B-A3B 25.4-30.0 tok/s on RTX 3070 8 GB; GLM-5.2 744B 1.07 tok/s on RTX 5070 Ti laptop. Author states they have not measured a discrete GPU themselves; MTP lost 32% at ~85% hit rate.
- **Verdict: worse for this use case** (no Qwen3.8-Flash-Next claim, slow); relevant as proof that look-ahead prefetch + pinned hot set works and that MTP can hurt at mid hit rates (same warning in PR #29887: n-gram speculation can double expert transfers).

### 2.7 FATE / llama-moe-cache (ongunm)
- https://github.com/ongunm/llama-moe-cache ; AGPL-3.0 (commercial dual license); 9 stars, 7 commits; ~500-line patch to ggml scheduler/CUDA: GPU-resident LRU pool, cross-layer + temporal prediction prefetch on a separate CUDA stream.
- RTX 4070 Ti 12 GB, Qwen3-30B-A3B Q4_K_M: 33.74 -> 64.45 tok/s (1.91x), 99.5% hit rate. Prefill collapses (4 vs 56 tok/s). Scaling claims ("1T on one workstation") are projections.
- **Verdict: worse (immature, AGPL, prefill broken), but the cross-layer + temporal predictor is a cheap idea to evaluate.** Note the 99.5% hit rate is on a 30B model whose experts nearly fit in 12 GB, not representative of 125B.

### 2.8 vLLM / SGLang
- vLLM RFC #38256 (https://github.com/vllm-project/vllm/issues/38256): pinned CPU store + GPU slot cache with LFRU eviction, deduped batched prefill loads; PR #37190 open (needs `--enforce-eager`, single GPU, no CPU fallback); independent `tinyserve`: GPT-OSS-20B MXFP4 on an 8 GB laptop GPU 30 tok/s. Issue #41447 `--moe-gpu-prefetch` (https://github.com/vllm-project/vllm/issues/41447). Not usable for GGUF/12 GB + 125B today.
- SGLang + KTransformers hybrid CPU/GPU integration: https://github.com/sgl-project/sglang/issues/11425 (server-class numbers: CPU MoE kernel 21.3 TFLOPS on dual Xeon 8452Y).
- **Verdict: not applicable now; datacenter-oriented.**

### 2.9 Academic systems (mostly no usable code)
| System | Idea | Result / state | URL |
|---|---|---|---|
| DALI (2602.03495) | dynamic CPU/GPU expert assignment (0-1 integer program, greedy), residual-based prefetch, workload-aware cache replacement; compared with llama.cpp, kTransformers, Fiddler, HybriMoE | speedups claimed, no numbers in abstract; no code link | https://arxiv.org/abs/2602.03495 |
| ATSInfer (2607.10183) | tensor-granularity static placement + load-aware dynamic transfer | up to 1.94x prefill, 3.29x decode over "existing systems"; no code link | https://arxiv.org/abs/2607.10183 |
| CoX-MoE (2605.17889, DAC'26) | AMX CPU-GPU coalesced expert execution, static expert stratification | up to 7.1x vs FlexGen, 2.4x vs MoE-Lightning (throughput batch, not latency); no code | https://arxiv.org/abs/2605.17889 |
| Edge0 / EDGEO (2609.18063) | trained per-layer "prerouter" predicts next layer's routing one token ahead, SSD streaming, recovery LoRA | 35B MoE at 20 tok/s, 3 GiB active memory on a 24 GB machine; needs training; code link not on abstract | https://arxiv.org/abs/2609.18063 |
| FineMoE (EuroSys'26) | semantic + trajectory-guided fine-grained prefetch/eviction | serving-oriented | https://arxiv.org/pdf/2502.05370 |
| "Efficient CPU-GPU collaborative inference for MoE" | run uncached experts on CPU while async-fetching for next token | Mixtral 8x7B, Phi-3.5-MoE | https://arxiv.org/pdf/2512.16473 |
| Importance-driven expert scheduling (edge) | CPU-load balancer decides move-vs-compute; substitutes less important experts | - | https://arxiv.org/html/2508.18983v1 |
| ADEPT two-stage offloading | domain-aware preload during prefill; >83% hit at K=6 | - | https://www.researchgate.net/publication/400888358_Two-Stage_Expert_Offloading_for_Domain-Aware_MoE_Inference |
| FlashMoE (2601.17063) | ML-based cache replacement for SSD I/O | edge | https://arxiv.org/html/2601.17063v1 |
| MoE-Infinity | activation-aware (sequence-level) expert tracing, prefetch | 2401.14361 | https://arxiv.org/html/2401.14361v2 |

### 2.10 Classic systems, from memory (URLs not re-opened this session) **[memory]**
Fiddler (CPU computes misses instead of copying; arXiv 2402.07033), Mixtral-offloading (LRU + speculative prefetch + HQQ mixed quant; 2312.17238), HOBBIT (mixed-precision expert loading, 2411.01433), AdapMoE (adaptive gating + prefetch, 2408.10284), ProMoE (proactive caching, 2410.22134), Fate (cross-layer gate prediction, 2502.12224), SwapMoE (2308.15030), DAOP (data-aware offloading with predictive pre-calculation, 2501.10375), FloE (compressed experts, 2505.05950), eMoE (2503.06823), MoE-Lightning (batch throughput, 2411.11217), Klotski (2502.17003), HybriMoE (CPU-GPU hybrid scheduling, 2504.05897), DeepSpeed-MoE inference (datacenter). All are research prototypes on Mixtral/DeepSeek-V2-Lite scale, mostly PyTorch, none exposes GGUF; superseded in practice by the llama.cpp/Strata/KTransformers line. Treat as technique sources only.

### 2.11 Other runtimes
- LM Studio 0.3.23 exposes "force MoE experts on CPU" (= `--n-cpu-moe` mechanism) (https://www.lmstudio.ai/blog/lmstudio-v0.3.23); no dedicated expert cache found. Ollama: no 2026 expert-cache source found; it was beaten 2.25x by FreeToken on one CPU-offload test (betterstack link above).
- benni-inference-engine (https://github.com/benni-os/benni-inference-engine): marketing-style claims, unexplained "Skill 81"; not evaluated, treat with caution.
- Intel / AMD-specific stacks: Strata itself lists SYCL and HIP backends (repo); no other Intel/AMD MoE-offload stack found. ik_llama.cpp and KT target x86 CPU features (AVX2/AVX512/AMX).

## 3. Ranking: five most relevant alternatives to Strata for 12 GB GPU + 64 GB RAM

1. **llama.cpp mainline `--moe-cache-mib` (+ `-cmoe`, MTP)** - lossless, GGUF, merged, same hardware class; slower than Strata's lossy 2-bit headline but the only maintained path we can build on.
2. **llama.cpp issue #29949 in-graph LRU / RFC #24528 CPU-computes-misses** - together they reproduce Strata's two core ideas (GPU cache + parallel CPU misses) inside ggml; unmerged, author-reported 1.5x over #29887.
3. **ik_llama.cpp** - Qwen3.8-Flash-Next + MTP support, IQK/trellis quants, `_R4` CPU kernels; no expert cache; best raw CPU-side speed candidate, unmeasured here.
4. **FreeToken** - bandwidth-adaptive CPU/GPU scheduler and elastic cache/KV memory; Apache-2.0, but no GGUF / Qwen3.8 / low-bit support.
5. **KTransformers** - best CPU kernels (AMX/VNNI) and Expert Deferral; wrong scale (large-RAM servers), no Qwen3.8 listed.

## 4. Techniques Strata lacks that others have (and porting into llama.cpp)

Strata-has list for context: GPU expert cache, CPU compute of misses, MTP, 8K-chunk streaming prefill, lossy tail skip.

| Technique | Source | Porting to llama.cpp | Size |
|---|---|---|---|
| Frequency-aware admission / TinyLFU-style or LFRU eviction instead of LRU | reviewer measurements in PR #29887 (60% hit vs LRU), vLLM RFC (LFRU) https://github.com/vllm-project/vllm/issues/38256 , DALI https://arxiv.org/abs/2602.03495 | Replace eviction in `ggml-cuda` MoE cache; keep per-expert counters with decay; admission = skip upload if freq below victim | patch-sized (<300 lines) |
| In-graph cache decision + fill (no host readback / sync) | #29949 https://github.com/ggml-org/llama.cpp/issues/29949 | two new ggml ops; branch `lru-upstream-v3` exists; compatible with MTP and multi-GPU | patch-sized but touches ggml op set (review friction) |
| Cache hits on GPU while CPU threads compute misses in the same op | RFC #24528 https://github.com/ggml-org/llama.cpp/discussions/24528 ; Fiddler **[memory]** | hybrid `MUL_MAT_ID` split by hit mask; one CPU thread dispatches GPU hits | ~1,700 lines CUDA per author; large |
| Expert Deferral (defer some experts' output one layer to overlap CPU/GPU) | KTransformers SOSP'25 https://madsys.cs.tsinghua.edu.cn/publication/ktransformers-unleashing-the-full-potential-of-cpu/gpu-hybrid-inference-for-moe-models/ | changes graph semantics (accuracy cost <=0.5% avg) - needs a flag and model-specific validation | medium, lossy |
| AMX / AVX-VNNI INT4/INT8 expert kernels | KTransformers; ik_llama.cpp `_R4` layouts https://github.com/ikawrakow/ik_llama.cpp | upstream ggml-cpu already has AMX paths; port ik-style row-interleaved repack for IQ-quants; license MIT both | medium to large |
| Look-ahead prefetch (one layer ahead, cross-layer router prediction) + separate copy stream | Colibrì https://github.com/JustVugg/colibri ; FATE https://github.com/ongunm/llama-moe-cache ; Fate, ProMoE **[memory]** | needs next-layer gate input approximated from current hidden state; staging in the cache's slot pool; #28414 shows prefill-only version and its multi-GPU hazards | patch-sized for decode, risky |
| Bandwidth-adaptive scheduling: benchmark PCIe + RAM bandwidth at startup, choose move-vs-compute per expert | FreeToken q* https://www.infoq.com/news/2026/08/freetoken-local-inference/ ; "Importance-driven" https://arxiv.org/html/2508.18983v1 | add a calibration step in `--fit`; policy = if miss cost on CPU < copy cost, compute on CPU | patch-sized policy, depends on #24528 for CPU path |
| Elastic VRAM split between KV cache and expert cache at runtime | FreeToken | resize cache between ubatches; requires allocator changes; #29887 already shrinks `--fit` layers | medium |
| Wave-partitioned prefill (each expert loaded once per ubatch) | PR #25294 https://github.com/ggml-org/llama.cpp/pull/25294 ; vLLM dedup load | group tokens by expert across the ubatch before upload; fixes issue #25859 | medium |
| Dedup + bigger prefill chunks with expert streaming | Strata already has; vLLM RFC too | llama.cpp batch >32 bypasses the cache; extend cache to prefill with dedup | medium |
| SSD tier with O_DIRECT, async I/O pool, mirrored disks | PR #25294; Colibrì | only matters when RAM < model; for 64 GB + 80 GB model this is Strata's SSD overflow | large (PR #25294 is already 'too big') |
| Pinned hot set learned from usage, persisted across sessions | Colibrì `.coli_usage`; RFC #24528 hot-set file; imatrix-seeded cache (vLLM RFC PR 3) | write routing histogram to file, preload on startup; combine with `--moe-cache-mib` | patch-sized |
| Routing-aware lossy shortcuts (skip tail experts not resident; prefer resident) | Strata itself (so not "lacking"), plus "Smart Expert Reduction" in ik_llama.cpp (PR 239) https://github.com/ikawrakow/ik_llama.cpp | make it an opt-in `--moe-skip-tail N`; must report KL; breaks lossless guarantee | patch-sized, policy risk |
| Trained routing predictor ("prerouter") | Edge0 https://arxiv.org/abs/2609.18063 | needs fine-tuned checkpoints per model; not portable | not feasible without training |
| Sequence-level activation tracing for prefetch | MoE-Infinity https://arxiv.org/html/2401.14361v2 | complex; low value for single-user decode | large |
| Semantic-anchor / agent-state checkpointing for prompt edits | FreeToken | llama.cpp already has prompt cache + `--cache-ram` (see our CLAUDE.md); recurrent-state checkpoints for DeltaNet are the new part | medium |

Warnings carried from the threads: n-gram speculation can double expert transfers (PR #29887, sanmai); MTP lost 32% at ~85% hit rate in Colibrì; a cache with slots < experts-per-token thrashes (GLM-5.3-Flash 15.1 -> 3.6 tok/s); the cache hurts on PCIe 3 and when the layout already fits (RTX 2080 Ti, 3090 results above).

## 5. What I could not verify
- Strata's KV streaming and exact cache policy (DETAILS.md not reachable); whether MTP is Qwen's own 4B head (the developersdigest article says it does not say).
- Any Strata vs llama.cpp comparison on the same quant with output-quality control.
- KTransformers support for Qwen3.8-Flash-Next; ik_llama.cpp numbers on this model and a single consumer GPU; FreeToken source language and last-commit date.
- PDF contents of DALI, ATSInfer, CoX-MoE, FreeToken (fetches returned binary); only abstracts were read.
- The model name "Qwen3.8-Flash-Next" itself is only seen in the llama.cpp PR threads and Strata material; consistent across them.
- Repo star counts are as shown on fetch day and come from page text summaries, not API calls.

## 6. Suggested next steps for Telecode (not done here)
1. Build current llama.cpp master (post-#30112) and measure `--cpu-moe --moe-cache-mib N` on the real 12 GB card with the same Strata quant (Unsloth IQ3_XXS / IQ4_XS GGUF) for an apples-to-apples baseline; record hit rate and prefill.
2. Test 0003 patch series of ours against #29949 (in-graph LRU) as a competing cache.
3. Build ik_llama.cpp (PR 2365/2369) on the same machine and compare decode and prefill, since its CPU kernels govern the miss path.
4. Run Strata itself with `STRATA_ROUTE_TAIL_SKIP=0` for a lossless comparison.
