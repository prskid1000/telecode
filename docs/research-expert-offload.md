# Research: fast MoE inference when experts do not fit in GPU memory

Date: 2026-10-14. Scope: what the 2024-2026 literature offers beyond our llama.cpp patch series (GPU expert cache with LRU/frequency promotion and hysteresis, CPU computes misses, async miss-count promotion, foresight prefetch, stats + prewarm, lossy router bias, lossy tail skip, MTP draft with adaptive length). Target box: RTX 5070 Ti Laptop 12 GB + 64 GB RAM, Qwen3.6-35B-A3B Q4_K_XL (256 experts, top-8).

Method and honesty notes. I opened every arXiv abstract/HTML page cited below through a page-reader tool that summarises the page; numbers are the authors' own claims, not reproduced by me. Where a page did not show a number, the entry says "not verified". Two PDFs (ICLR 2026 offloading paper, SOSP KTransformers) could not be read directly; those entries rely on abstracts and search snippets and are flagged. I did not read `docs/strata-port.md`; "ours" below follows the feature list in the task. Section 5 contains my own analysis (marked as hypothesis), not literature.

Important context: upstream llama.cpp now ships a GPU expert cache (`--moe-cache-mib`, PR #29887, multi-GPU #30112) per a secondary source I could not check against GitHub: https://www.developersdigest.tech/blog/llama-cpp-moe-offload-n-cpu-moe-vs-moe-cache-2026 . Our parity doc already says the same.

---

## 1. Entry per technique

### 1.1 Expert prefetching / prediction

**Speculating Experts (arXiv 2603.19289, Mar 2026).** Predicts layer l+1 experts by applying the next router to a "quasi-hidden state" = LN(offline per-expert average activation ("default vector") of the selected experts + the post-attention residual). Same family as our foresight (next router on current state). Prefetch overlaps the copy with compute. Reported: up to 14% lower TPOT vs on-demand CPU loading (A6000, Qwen3-30B-A3B 9-14%; only 5-8% on A100/GH200); on-demand copy is 84-88% of TPOT on A6000. Recall@k about 90% beyond early layers on Qwen3-30B-A3B; early layers much worse, and a trained neural estimator (17M params, 4M tokens) lifts early layers by up to ~25%. Quality: they *execute the speculated experts instead of the true ones*; that costs accuracy on Qwen3-30B-A3B (6-benchmark average 0.817 -> 0.641 router-only, 0.766 with hybrid estimator) but is fine on GPT-OSS. Our design keeps true routing (prefetch only), so is lossless. Code: https://github.com/axonn-ai/yalis (branch `offload_prefetch`, Apache-2.0 repo; paper CC BY-NC-SA). Sources: https://arxiv.org/abs/2603.19289 , https://arxiv.org/html/2603.19289v1 , licence https://github.com/axonn-ai/yalis . Port: default-vector table is an offline pass (stats file machinery exists) plus a hook in the foresight graph node; effort S-M; gain for us small (see 5) since a miss costs CPU compute, not a 84% copy stall. The paper's own text/table disagree on one recovery figure (flagged by the reader tool).

**ST-MoE, Spatio-Temporal Expert Prefetching (arXiv 2606.15453, Jun 2026).** Two profiled 2-bit-confidence tables: cross-layer correlation table (next-layer experts) + history table (previous token). 85% prediction accuracy; 2.5x vs A100 GPU, 2.2x vs Adap-gating, 1.5x vs Pre-gated MoE. But it needs a custom reconfigurable accelerator, evaluated only in a cycle-accurate simulator (TSMC 40 nm); no code mentioned. Not portable; the only transferable idea is "cross-layer co-occurrence table + previous-token table" as a cheap predictor, which is S-M and unproven on a GPU. Source: https://arxiv.org/html/2606.15453 .

**ProMoE (arXiv 2410.22134).** Per-layer 2-layer MLP predictor (~2M params), top-k predicted experts prefetched; copies are split into per-linear-layer chunks so a speculative low-priority copy delays a real miss by at most one chunk; "stride prefetching" predicts one more layer ahead at ~5% accuracy cost; 84.7% predictor accuracy (vs 58.3% token-based). Reported 2.20x prefill / 2.07x decode average (up to 3.21x / 5.02x) vs "existing offloading solutions" (HF Transformers offload, llama.cpp layer offload, unified memory); 1.78x / 1.34x vs static and LRU caches. Hardware RTX 4090 + 128 GB. Quality: stated equivalent to the original model. Code: https://github.com/promoe-opensource/promoe (link from the paper HTML; I did not open the repo; licence not verified). Sources: https://arxiv.org/html/2410.22134v3 , https://arxiv.org/abs/2410.22134 . Port: needs a trained predictor per model (M-L) plus chunk-preemptible copies on a low-priority stream (S-M). Useful part for us: the preemptible copy discipline, which prevents our foresight uploads from delaying promotion/misses.

**Fate (arXiv 2502.12224).** Prefetches via adjacent-layer gate inputs (no extra model), shallow-layer-favouring cache and quantised transfer. Up to 4.5x prefill / 4.1x decode vs load-on-demand, 1.9x / 2.2x vs an expert-activation-path method; claims 99% hit rate with the shallow-favouring cache (setup not verified). Code not found. Source: https://arxiv.org/abs/2502.12224 . Port: the idea "give more cache to shallow layers" is S (see per-layer budgets, 1.2).

**AdapMoE (arXiv 2408.10284).** Sensitivity-based adaptive number of experts (25% fewer activated), adaptive prefetch and cache. 1.35x speedup, "without accuracy degradation" (abstract only; baselines unnamed). Code: https://github.com/PKU-SEC-Lab/AdapMoE (no licence file). Source: https://arxiv.org/abs/2408.10284 . Port: Mixtral-class only, needs per-layer sensitivity calibration; M, low gain for top-8-of-256.

**MoE-Infinity (arXiv 2401.14361).** Request-level activation traces select "representative" traces to drive cache replacement and prefetch; 3.1-16.7x per-token latency vs vLLM, Ollama, DeepSpeed, BrainStorm. Code Apache-2.0: https://github.com/EfficientMoE/MoE-Infinity (active). Sources: https://arxiv.org/abs/2401.14361 . Port: trace matching is a smarter version of our persisted stats + prewarm (L, speculative gain).

**Pre-gated MoE (arXiv 2308.12066, ISCA'24).** Changes the model: layer l's gate picks layer l+1's experts, so prefetch is exact. Claims same quality and good performance, no numbers in abstract; requires fine-tuning of the router, so not applicable to an already-trained Qwen3.6. No code link on the page. Source: https://arxiv.org/abs/2308.12066 .

**Efficient CPU-GPU Collaborative Inference for MoE on Memory-Limited Systems (arXiv 2512.16473, ASP-DAC 2026).** This is essentially our architecture: GPU holds attention, router, KV and a set-associative LRU expert cache; miss -> activations go to the CPU which computes the expert multi-threaded; missed weights are copied asynchronously to refresh the cache; two CUDA streams. No prefetching, argued to be limited by transfer cost. RTX 4090 + 24-core Threadripper: up to 4.8 tok/s Mixtral-8x7B and 10.4 tok/s Phi-3.5-MoE; up to 4.4x vs on-demand baselines (DeepSpeed-Inference, Accelerate, Pre-gated estimate), ~1.6x vs Fiddler; cache adds only +15-35% over CPU-only on Mixtral (+50-250% on Phi-3.5-MoE); LRU beats random by 5-15% hit rate. No quality loss (exact). Code MIT: https://github.com/elsa-lab/MoE-CPU-GPU-Collaborative-Inference . Sources: https://arxiv.org/html/2512.16473v1 . Port: nothing new for us; it validates the design and the prefetch scepticism.

**In-depth analysis of caching and prefetching (arXiv 2511.05814).** Small study on Mixtral-8x7B: LFU faster than LRU on 3 of 4 GPUs (A6000 2.34 -> 4.32 tok/s, RTX 3090 +0.7%), hit precision/recall only 29.1% -> 29.9% / 58.2% -> 59.8%; next-layer-gate speculation 84.6% accuracy on one prompt but no end-to-end speed measured. Weak evidence (one prompt, 57-question MMLU subset) but a reminder that policy choice is noisy. Source: https://arxiv.org/html/2511.05814 .

### 1.2 Cache replacement, per-layer budgets, placement

**MoE-CORE (arXiv 2610.01950, Oct 2026).** Decode-side: nonuniform per-layer cache allocation (more slots to low-hit layers), domain-based hot-expert initialisation (like our prewarm), routing-history-aware replacement (recent frequency + long-term average + router score + age), cross-layer prefetch (next router on current hidden state, default 1 expert/layer), optional approximate expert substitution (AES) for misses whose score is within 0.1 of the top-k boundary, replaced by resident experts. 1.39-1.75x lower TPOT than static load-on-demand on DeepSeek-V4-Flash; effective availability 92.43% -> 99.972% with AES, +4.7% TPOT overhead at batch 1. Hardware is an Ascend NPU (not CUDA). No accuracy/perplexity reported for AES (the authors say score proximity does not establish output equivalence). Code Apache-2.0: https://github.com/LookAround0301/vllm-ascend (branch moe_offload_v5.0, `expert_offload`). Source: https://arxiv.org/html/2610.01950 . Port: per-layer budget and multi-signal replacement are S-M and fit our stats file. Ablation per component not verified.

**HOBBIT cache policy (arXiv 2411.01433).** Weighted priority of LRU, LFU, a "least high-precision frequently used" and layer-distance, weights tuned on a calibration set; separate high/low-precision pools. Source: https://arxiv.org/html/2411.01433 .

**DALI (arXiv 2602.03495, Feb 2026).** Built on KTransformers for local PCs (RTX 3090 + EPYC): greedy per-layer CPU/GPU expert assignment by workload, residual-based prefetch for high-workload experts, workload-aware cache replacement. Average decode speedup 3.97x over llama.cpp, 2.16x over KTransformers, 1.48x over MoE-Lightning, 1.32x over HybriMoE; prefill 7.62x / 3.80x / 2.45x / 2.00x. No quality metrics, no code link found. Source: https://arxiv.org/html/2602.03495v1 . The llama.cpp baseline is plain layer offload, so 3.97x is not comparable to a cache-equipped llama.cpp.

**HybriMoE (arXiv 2504.05897, DAC'25).** Dynamic intra-layer CPU/GPU scheduler balancing uneven expert workloads, impact-driven inter-layer prefetch, score-based cache. 1.33x prefill / 1.70x decode vs the best prior hybrid framework (KTransformers). Code Apache-2.0: https://github.com/PKU-SEC-Lab/HybriMoE . Sources: https://huggingface.co/papers/2504.05897 (baseline identity and hardware not verified by me).

**ExpertFlow (arXiv 2410.17954).** Trained routing-path predictor (7 MB), per-layer cache allocation by predicted demand (PLEC), token regrouping by predicted route. Hit ratio 91.96% (up to +61% over LRU); up to 1.99-2.21x over a fixed per-layer LRU cache on Mixtral/Qwen1.5/DeepSeek-MoE. No quality numbers, no code on the page. Source: https://arxiv.org/html/2410.17954v2 .

**Local routing consistency (ICLR 2026, arXiv 2505.16056).** Across 20 MoE LLMs, local routing consistency varies a lot; consistency trades off against local load balance; shared experts lower consistency; most models balance cache effectiveness and cost at a cache of ~2x the active experts. This is the right yardstick for "will a cache work for model X". Source (abstract via search; PDF unreadable): https://arxiv.org/abs/2505.16056 .

### 1.3 Mixed/dynamic precision

**HOBBIT (arXiv 2411.01433).** Built on llama.cpp (~8k lines). For a missed expert, load a low-precision copy depending on its gate weight: rank experts, "unimportance" = summed weight of better-ranked experts; < 0.6 high precision, 0.6-0.9 low precision, > 0.9 skipped (Mixtral split ~67% / 30% / 3%). Accuracy: GSM8K 0.52 -> 0.51 on Mixtral and about -0.01 on Phi-MoE, TruthfulQA unchanged or up. Speed: 1.31x / 1.42x vs llama.cpp on 4090+CPU, ~parity (0.99x) to 1.46x vs Fiddler, 2.3-3.9x vs MoE-Infinity, 9.9x vs MoE-Infinity on Jetson. Code: none found on the page. Sources: https://arxiv.org/html/2411.01433v2 . Port to us: keep a second, lower-bit copy of all experts in RAM (needs ~extra 8-10 GB at IQ2-class on 64 GB RAM), and on a miss upload that copy instead of computing the Q4 miss on CPU; needs a second tensor set in the loader, a cache with two slot sizes, and a `mul_mat_id` path that accepts it. Effort L. Gain uncertain: it replaces CPU compute with a 2-4x smaller PCIe transfer; wins only if our CPU miss path is the bottleneck (see 5). Note HOBBIT's own best result vs a CPU-compute baseline (Fiddler) is only 0.99-1.46x.

**FloE (arXiv 2505.05950, ICML'25).** Up-projection INT2 (HQQ) always loaded; its activation magnitude sparsifies gate/down so only needed channels are transferred. 9.3x per-expert compression, 11 GB VRAM Mixtral-8x7B; 48.7x vs DeepSpeed-MII, 2.6x vs Mixtral-Offloading, 3.14x vs Fiddler; 4.4-7.6% average accuracy loss. No code on the page. Source: https://arxiv.org/html/2505.05950v1 . Port: needs custom sparse expert format and kernels; L; accuracy loss too high for us.

### 1.4 Expert skipping and pruning at inference

**ACE (arXiv 2609.05228).** Training-free, calibration-free per-token expert skipping; always keeps top-1; skips a slot only when two proxies (global spectral capacity and router-conditioned response) agree it is low-contribution; offline tables, lookup at runtime. On Qwen3.6-35B-A3B (our model) at 50% skipping: perplexity on WikiText-2 -7.96% and average accuracy +4.15 points *relative to the strongest competing method* (absolute degradation vs the unskipped model not shown on the page). No latency numbers, no code link. Source: https://arxiv.org/abs/2609.05228 .

**Elbow-based routing (arXiv 2608.04401, ICLR 2026).** Pick k per token at the elbow of sorted router probabilities; 5.3% average latency reduction at maintained accuracy on OLMoE. Source: via search listing https://arxiv.org/pdf/2608.04401 (not opened).
**ASET (ACL Findings 2026), Ban&Pick (arXiv 2509.06346), MoDES.** Router entropy/confidence adaptive activation, static skip policies 10-78% throughput; Ban&Pick 1.25x on Qwen3-30B-A3B in vLLM; MoDES for multimodal. Only search snippets seen: https://arxiv.org/pdf/2509.06346v1 , https://preview.aclanthology.org/ingest-acl/2026.findings-acl.2140/ . Not verified.

**SERE (arXiv 2602.07616).** Re-routes batch-secondary experts to the most similar primary expert (precomputed similarity, threshold rho). Up to 2.0x (1.2-1.6x on Qwen3/DeepSeek-V2-Lite), >97% of accuracy; Qwen3-30B-A3B Top-2 + SERE averages 80.37 vs 82.24 for Top-8; Top-1 loses far more. H20 + vLLM, batched decoding; batch-1 and verify windows not evaluated. Code MIT: https://github.com/JL-Cheng/SERE . Source: https://arxiv.org/html/2602.07616v1 . For us only relevant in multi-slot serving or large verify windows.

### 1.5 CPU-side kernels and scheduling

**Fiddler (arXiv 2402.07033).** Compute missed experts on the CPU instead of moving weights; 1.26x single-batch, 1.30x long prefill, 11.57x beam search vs unnamed baselines. Code Apache-2.0: https://github.com/efeslab/fiddler (last push Nov 2024). Source: https://arxiv.org/abs/2402.07033 . Our CPU-miss path already is this; FloE and HOBBIT both beat or tie it only modestly.

**KTransformers (SOSP'25).** AMX-specialised and AVX512/AVX2 INT4/INT8 kernels, NUMA-aware memory, async CPU-GPU scheduling: 4.62-19.74x prefill, 1.25-4.09x decode vs existing systems; **Expert Deferral** (defer some selected experts' contribution to later layers to raise CPU utilisation from <75% to ~100%) up to 1.45x extra throughput with <=0.5% average accuracy drop. Numbers come from the abstract as surfaced by search, I did not open the paper; the Expert Deferral mechanism description is my paraphrase of the snippet and should be verified before porting. Code Apache-2.0, very active: https://github.com/kvcache-ai/ktransformers . Sources: https://madsys.cs.tsinghua.edu.cn/publication/ktransformers-unleashing-the-full-potential-of-cpu/gpu-hybrid-inference-for-moe-models/ . Port: AMX needs Intel Sapphire Rapids+ (a laptop CPU almost surely lacks it; CPU model not checked); AVX512 path may exist upstream already. Expert Deferral is a graph change (M-L), lossy.

**CoX-MoE (arXiv 2605.17889).** Batch/throughput oriented: attention micro-batched, expert compute over the whole batch as one unit, AMX co-execution, static "expert-aware stratification" pre-placing hot experts. 1.7-2.4x vs MoE-Lightning, 3.4-7.1x vs FlexGen on Xeon + 48-80 GB GPUs; no quality metric, no code. Not relevant to batch-1 on a 12 GB laptop. Source: https://arxiv.org/html/2605.17889v1 .

### 1.6 Router-aware batching by expert

Covered by CoX-MoE above, SERE (1.4) and ExpertFlow token regrouping (1.2). Relevant to us only for prefill and verify windows (we already compute grouped by expert in `mul_mat_id`); no batch-1 gain. The "cacheable by design" study also reports llama.cpp request batching stopped helping past about batch 8 and collapsed at 32 from paging thrash on its setup: https://arxiv.org/html/2608.18261 .

### 1.7 Offloading plus speculative decoding

All verified from abstracts/HTML on the pages cited.

- **SpecFit (arXiv 2610.15417).** Chooses the verified draft subtree and the draft depth by reward / expert-union cost, target unmodified. Qwen3-30B-A3B, batch 1: +21.6% vs EAGLE-3 with the same expert cache on CNN/DM, +7.0% on GSM8K, +10.8% on RTX 4090; 2.51x vs autoregressive on the 4090. Quality: throughput only, greedy verification keeps outputs. Code Apache-2.0: https://github.com/Chlience/SpecFit . Source: https://arxiv.org/html/2610.15417 . Closest to our "acceptance-adaptive length", which ignores expert-union cost.
- **AcceptMoE (arXiv 2608.02989).** Verifier expert set sized per block from tree commitment and conditioned on cache residency; SGLang batch 1: 2.06x vs EAGLE-3 natural routing under physical offloading, 73.6-77.1% less host-to-device traffic; lossy, mean accuracy -0.27 points over 12 model-task pairs. No code on the page. Source: https://arxiv.org/abs/2608.02989 . This is a published validation of our "tail skip in verify windows" idea, with a measured quality loss.
- **MoE-Spec (arXiv 2602.16052).** Fixed per-layer expert capacity at verification, drop the tail; +10-30% over EAGLE-3 at comparable quality (no figures on page), trade accuracy for latency by tightening. Source: https://arxiv.org/abs/2602.16052 .
- **EVICT (arXiv 2605.00342).** Lossless, training-free truncation of the draft tree by benefit/cost; up to 2.35x vs autoregressive, 1.21x avg vs EAGLE-3; offloading not mentioned. Source: https://arxiv.org/abs/2605.00342 .
- **SP-MoE (arXiv 2510.10302).** Drafter's per-layer attention output goes through the *target's* router to predict verification experts; cutoff layer so prefetch finishes during drafting; own CUDA stream. 1.07-3.5x TPOT vs Mixtral-Offloading+SD, MoE-Infinity+SD, AdapMoE+SD (about 34/19/12% lower on average); lossless. No code on the page. Source: https://arxiv.org/html/2510.10302v2 .
- **MoE-SpAc (arXiv 2603.09983).** Treats SD as a lookahead sensor: utility estimator for expert demand, online integer program splitting experts between GPU and CPU, async prefetch/evict. Qwen3-30B-A3B + Qwen3-4B draft on RTX 4090: ~42% TPS over llama.cpp with SD, 4.04x average over all baselines; no quality metrics. Code MIT: https://github.com/lshAlgorithm/MoE-SpAc . Source: https://arxiv.org/html/2603.09983v1 .
- **MoE-SpeQ (arXiv 2511.14102), DraftExpert (arXiv 2607.24434), The Limits of Speculation (arXiv 2609.22156).** MoE-SpeQ: quantised drafter predicts multi-step experts for prefetch (not opened). DraftExpert: trained per-layer draft expert, 1.45x decode, 84-87% acceptance, requires training: https://arxiv.org/abs/2607.24434 . Limits: SSP-based budget selection with an Oracle: https://arxiv.org/abs/2609.22156 . Not applicable without training.

Verify-window effect, in short: a window of n tokens activates up to the union of n x 8 experts per layer, so with a 27% cache the miss count per layer rises roughly linearly in n while the accepted tokens do not; this is why SpecFit/AcceptMoE/MoE-Spec all price drafts by expert-union cost. (Inference from their abstracts, not a measurement of ours.)

### 1.8 Quality-preserving / cache-aware routing

**Cache-conditional experts (Skliar et al., arXiv 2412.00099, TMLR).** Training-free: z' = z + lambda x running-average-logit-range x cache-mask, used only to rank; original logits still weight outputs; guaranteed top-J (J=1 for k=2 models, J=2 for k=4/6) experts kept by true rank. With cache = half the experts and lambda = 0.5, miss rate falls from 35% to 16% (Qwen1.5-MoE), 28% -> 7% (DeepSeek-V2), 22% -> 9% (Phi-3.5-MoE), 40% -> 21% (Mixtral). Perplexity +0.1% to +3% (Qwen ~0.5%, DeepSeek ~0.1%, Mixtral ~2.9%); MMLU/GSM8K drop <0.1% (no per-model figures). On-device llama.cpp Snapdragon, Qwen1.5-MoE-A2.7B: >2x token throughput vs LRU. No code link found. Sources: https://arxiv.org/html/2412.00099 , https://arxiv.org/abs/2412.00099 . Port: S; same hook as our bias, add the top-J guarantee and the logit-range scaling.

**ReMoE (arXiv 2605.27081).** Router-only fine-tune with KL anchor + temporal-reuse loss. Only ~7% fewer unique misses in simulation (LRU capacity 6), quality about flat (MMLU +0.09, GSM8K about -0.7, IFEval -1.1); real speed: 1.77-1.99x decode in llama.cpp on a Jetson Orin NX with NVMe, 8.4% throughput on vLLM RTX 3090. Needs training; code https://github.com/BUAA-OSCAR/ReMoE (no licence file). Source: https://arxiv.org/html/2605.27081 .

**Cacheable by Design (arXiv 2608.18261).** Small-scale (137M/340M) negative result, but has the most useful *inference-time rerouting* curve: ~30% miss reduction costs +0.8-2.0% perplexity, 50% costs +4.0-5.8%, 74% costs +25%; training for locality alone costs +0.3% at 15% miss reduction up to +3.1% at 60%; stacking both gives ~80% miss reduction at +2-3%. Authors estimate ~95% hit rate is needed for 10 tok/s on their hardware and recommend LRU over static pinning. MIT code: https://github.com/Shriniwas410/cacheable-by-design . Source: https://arxiv.org/html/2608.18261 . Caveat: tiny models, so the absolute numbers do not transfer to a 35B-A3B model with 256 experts.

**MoE-CORE AES** (1.2) and **AcceptMoE / MoE-Spec** (1.7) are the other lossy cache-conditioned approximations; only AcceptMoE gives a quality number (-0.27 accuracy points).

---

## 2. Summary table

| Technique | Type | Speedup (baseline) | Quality | Code / licence | Port effort |
|---|---|---|---|---|---|
| Speculating Experts | prefetch via quasi-hidden state | <=14% TPOT (on-demand) | lossy if speculated experts are executed | yalis Apache-2.0 | S-M |
| ProMoE | learned predictor + chunk preemption | 2.07x decode (offload systems), 1.34x vs LRU | exact | promoe-opensource, licence unverified | M-L |
| 2512.16473 | CPU-miss + async cache | 4.4x (on-demand), 1.6x (Fiddler) | exact | MIT | already ours |
| MoE-CORE | per-layer budget, RHAC, AES | 1.39-1.75x (static LoD, NPU) | AES unmeasured | Apache-2.0 (vllm-ascend fork) | S-M |
| HOBBIT | low-bit miss experts | 1.31-1.42x vs llama.cpp, ~1x vs Fiddler | GSM8K -0.01 | none found | L |
| FloE | INT2 + sparse experts | 3.14x vs Fiddler | -4.4 to -7.6% | none found | L |
| Cache-conditional routing | cache-aware router bias | >2x on phone vs LRU | PPL +0.1-3%, MMLU/GSM8K <0.1% | none found | S |
| ACE / elbow / ASET | expert skipping | no latency number (ACE) | ACE on our model, numbers vs peers only | none verified | M |
| SpecFit / EVICT | spec-decoding cost-aware | +7-22% vs cached EAGLE-3 | lossless | SpecFit Apache-2.0 | S-M |
| AcceptMoE / MoE-Spec | verify-window expert limit | 2.06x (offloaded EAGLE-3) | -0.27 pt | none found | M |
| SP-MoE / MoE-SpAc | draft-guided prefetch / placement | 12-34% vs offload+SD | lossless | MoE-SpAc MIT | M-L |
| KTransformers (+Expert Deferral) | CPU kernels + deferral | 1.25-4.09x decode; +1.45x deferral | <=0.5% | Apache-2.0 | L |
| DALI / HybriMoE | dynamic CPU/GPU assignment | 1.32x / 1.70x over prior hybrids | exact | HybriMoE Apache-2.0; DALI none | M |

---

## 3. Which of our features the literature supports, or doubts

- CPU computes misses, async promotion, LRU/frequency cache: same design as 2512.16473 and Fiddler (https://arxiv.org/html/2512.16473v1). Supported. Its own cache gain over CPU-only is modest (+15-35% on Mixtral), which matches our cache ~ static split result.
- Foresight prefetch: **weakest feature**. The 14% best case was measured where the copy is 84-88% of TPOT (https://arxiv.org/html/2603.19289v1); on faster-link machines 5-8%. In a CPU-miss design a mispredicted expert costs a CPU compute, not a copy stall, so the ceiling is lower than their 14%. 2512.16473 deliberately omits prefetch for the transfer-cost reason; 2511.05814 never measured speed. Keep only if it measurably wins in our A/B; cheap insurance is a preemptible low-priority copy (ProMoE chunks).
- Prewarm + persisted stats: supported (MoE-CORE domain hot-expert init, CoX-MoE static stratification); "cacheable by design" says pinning alone is weaker than LRU, so keeping prewarm as an *initial state only* is right.
- Router bias (lossy): supported, but ours should be checked against the published recipe (top-J guarantee, scale by running logit range, outputs weighted by original logits). Published loss is 0.1-3% PPL at ~half cache and ~50% miss reduction; our cache is 27%, harder than any of those settings, and the small-model study shows the cost rising steeply past ~30-50% miss reduction.
- Tail skip in verify windows (lossy): supported by AcceptMoE (-0.27 points, 2.06x) and MoE-Spec; not redundant.
- Acceptance-adaptive draft length: incomplete. SpecFit/Limits-of-Speculation show the right objective includes expert-union cost under a cache.
- Hysteresis/frequency promotion: literature policies (LRU vs LFU) swing between +0.7% and +85% depending on GPU (https://arxiv.org/html/2511.05814); weak evidence either way, tune empirically.

---

## 4. Top 8 ideas we have not implemented (benefit / effort)

1. **Expert-union-aware verify window** (SpecFit/EVICT/AcceptMoE style): choose MTP draft length (and, if chains branch later, subtree) by predicted accepted-tokens gain minus misses added to the cache. S-M, server/spec code only, no graph change; published +7-22% over cached EAGLE-3 on Qwen3-30B-A3B. https://arxiv.org/html/2610.15417
2. **Cache-conditional routing recipe** (top-J guaranteed, bias scaled by running logit range, true logits for weights): S, same hook as our bias; aims to cut quality loss per miss avoided, enabling a larger lambda. https://arxiv.org/html/2412.00099
3. **Per-layer cache budgets from the stats file** (MoE-CORE nonuniform allocation, Fate shallow-favouring, ExpertFlow PLEC): S-M; marginal-hit-rate allocation of the fixed MiB budget; exact, no quality risk. https://arxiv.org/html/2610.01950 , https://arxiv.org/abs/2502.12224
4. **Dynamic per-layer CPU-vs-upload assignment** (HybriMoE/DALI; our `pcie_frac`): M; compute a miss on CPU or upload once to GPU depending on layer miss count and measured CPU vs PCIe rate; matters for prefill and verify windows. https://huggingface.co/papers/2504.05897 , https://arxiv.org/html/2602.03495v1
5. **Cache-residency-aware verification expert limit** (AcceptMoE/MoE-Spec generalised): M; tighten our tail skip with a per-layer budget and commitment weights; published -0.27 points. https://arxiv.org/abs/2608.02989
6. **Multi-signal replacement score** (recent frequency + long-term average + router score + age; MoE-CORE RHAC, HOBBIT): S; replaces LRU/frequency+hysteresis; gain unknown, noisy per 2511.05814. https://arxiv.org/html/2610.01950
7. **Calibrated confidence-based skipping of missed experts** (ACE on Qwen3.6-35B-A3B, elbow routing): M; skip only low-contribution *missed* experts per token in normal decoding; needs an offline table; absolute loss not verified. https://arxiv.org/abs/2609.05228
8. **KTransformers-style Expert Deferral** (or AVX512/AMX miss kernels if the CPU has them): M-L; claimed up to 1.45x with <=0.5% drop, abstract-level evidence only; also try `ik_llama.cpp`-class kernels first. https://madsys.cs.tsinghua.edu.cn/publication/ktransformers-unleashing-the-full-potential-of-cpu/gpu-hybrid-inference-for-moe-models/

Considered and ranked below: HOBBIT low-bit miss experts (L, ~1x vs CPU-compute baseline), FloE (L, 4-8% accuracy loss), ProMoE learned predictors and the quasi-hidden-state estimator (M, foresight is already our weakest feature), SP-MoE/MoE-SpAc draft-driven prefetch (M-L; MTP head only gives a token-level hint), ReMoE/training approaches (needs training, ~7% fewer misses), SERE/CoX-MoE (batch serving only), ST-MoE (needs custom hardware).

---

## 5. Hypotheses about our own numbers (not from the literature)

- Hit rate 75% at 8 active experts means about 2 CPU-computed experts per layer per token. At Q4, 3 B active parameters are roughly 1.7 GB per token, so 25% on CPU is roughly 0.4 GB, which should take only a few ms at DDR5 bandwidth; if our token time is ~21-25 ms the floor may be per-layer GPU<->CPU synchronisation and hidden-state transfers, not bandwidth. That is the cost HybriMoE/DALI scheduling and Expert Deferral attack; profile before choosing between ideas 4 and 8.
- Static layer split at 44-55 tok/s beating our cache (40-47) is plausible: 2512.16473 itself found the cache adds only 15-35% over CPU-only on Mixtral. Cache gains should be larger with MTP verify windows, which is where ideas 1 and 5 pay.

## 6. Could not verify

ICLR 2026 offloading paper body, SOSP KTransformers body (Expert Deferral mechanics), ST-MoE accuracy impact, ACE absolute accuracy and speed, ProMoE and HOBBIT code existence/licence, DALI/Fate/ExpertFlow/Cache-conditional code, MoE-SpeQ details, ASET/Ban&Pick/Elbow numbers beyond search snippets, llama.cpp PR #29887 details, and all speedups (author-reported only).
