"""Throughput benchmark — talks to llama-server directly, bypassing the proxy.

Builds a synthetic prompt of approximately N tokens, sends it to
`/completion` with `cache_prompt:false` (cold prompt-eval), generates a
fixed number of tokens, and returns the timings llama-server reports.

Hits `cfg.upstream_url()` directly so proxy transforms (system-prompt
injection, tool injection, model-mapping) don't skew the numbers.
"""
from __future__ import annotations

import random
import time
import aiohttp

from llamacpp import config as cfg

# Expanded, extremely rich, highly meaningful paragraphs across diverse fields
# designed to scale up to 1M+ tokens beautifully with realistic n-gram distributions.
_SEED_PARAGRAPHS: tuple[str, ...] = (
    "In the deep reaches of the cosmos, supermassive black holes anchor the centers of most massive galaxies, exerting gravitational forces so intense that even light cannot escape their event horizons. As surrounding matter spirally falls toward the singularity, it forms a superheated accretion disk that emits brilliant radiation across the electromagnetic spectrum, occasionally launching powerful relativistic jets that span thousands of light-years.",
    "The cosmic microwave background radiation represents the relic thermal signature of the early universe, dating back to approximately 380,000 years after the Big Bang when protons and electrons recombined into neutral hydrogen. This recombination decoupled photons from baryonic matter, allowing light to travel freely through space for the first time, leaving behind a highly isotropic 2.7 Kelvin glow perturbed only by micro-Kelvin temperature fluctuations.",
    "A lock-free concurrent hash map utilizes atomic compare-and-swap operations rather than mutual exclusion locks to resolve write contentions across multiple threads. By avoiding traditional locks, the data structure prevents thread preemptions, priority inversions, and deadlocks, ensuring that at least one thread makes progress within a finite number of steps, which dramatically improves throughput in high-concurrency systems.",
    "Compilers translate high-level source code into optimized machine instructions through a series of structured compiler phases, beginning with lexical analysis and parsing to construct an abstract syntax tree. This intermediate representation undergoes semantic analysis, target-independent optimizations like common subexpression elimination, loop unrolling, and dead code removal, before final code generation and register allocation occur.",
    "Database indexing architectures generally choose between B+ trees and Log-Structured Merge trees to optimize for specific workload profiles. B+ trees maintain a balanced, sorted hierarchy that provides highly predictable, logarithmic search latency and is ideal for read-heavy workloads, whereas Log-Structured Merge trees buffer writes in memory before flushing them to sequential disk tables, making them exceptionally fast for write-intensive tasks.",
    "The eukaryotic mitochondrion operates as the primary energetic powerhouse of the cell, orchestrating the complex biochemical cascade of cellular respiration through the citric acid cycle and oxidative phosphorylation. High-energy electrons derived from nutrient breakdown are passed along the inner mitochondrial membrane's transport chain, pumping protons into the intermembrane space to drive ATP synthase.",
    "CRISPR-Cas9 gene editing represents a revolutionary molecular technology derived from a bacterial adaptive immune system, utilizing a guide RNA sequence to target specific genomic loci with exceptional precision. Upon binding, the Cas9 endonuclease introduces a double-stranded break in the DNA helix, triggering the cell's natural repair pathways to either disrupt the gene or integrate custom donor sequences.",
    "The transition from agrarian economies to industrial manufacturing during the late eighteenth century sparked the Industrial Revolution, profoundly reshaping global demographics, labor structures, and urban centers. The development of steam-powered engines, mechanized textile looms, and advanced metallurgy accelerated production rates and established centralized factory systems, permanently altering sociological landscapes.",
    "Ancient Greek philosophy, championed by Socrates, Plato, and Aristotle, laid the fundamental intellectual groundwork for Western rational inquiry and scientific methodology. Socrates introduced dialectical questioning to dissect ethical concepts, Plato formulated the theory of Forms to explore objective reality, and Aristotle pioneered formal logic and empirical observation of the natural world.",
    "Quantum superposition dictates that a physical system remains simultaneously in multiple possible states until a measurement occurs, collapsing its wave function into a single, definite outcome. This non-classical phenomenon allows quantum computers to process complex information using qubits that represent both zero and one concurrently, enabling parallel computational capabilities far exceeding classical limits.",
    "Albert Einstein's theory of special relativity revolutionized our understanding of spacetime by establishing that the laws of physics are identical for all non-accelerating observers and that the speed of light in a vacuum remains constant regardless of the source's motion. This leads to profound physical consequences, including time dilation and length contraction at relativistic velocities.",
    "Skyscraper structural engineering must account for both static gravity loads and highly dynamic lateral wind and seismic forces to ensure structural integrity and occupant comfort. Modern high-rise designs often employ a rigid central shear-wall core coupled with perimeter outrigger trusses and tuned mass dampers, which act as massive internal pendulums to counteract wind-induced sway.",
    "Plate tectonics explains the dynamic movements of Earth's lithospheric plates as they drift across the semi-fluid asthenosphere, driven by mantle convection currents and slab-pull forces. The interactions along plate boundaries—convergent, divergent, and transform—give rise to mountain ranges, deep oceanic trenches, active volcanic chains, and powerful seismic events.",
    "The global carbon cycle regulates the exchange of carbon among Earth's atmosphere, oceans, biosphere, and geosphere, acting as a crucial planetary thermostat. Anthropogenic emissions from fossil fuel combustion and widespread deforestation have disrupted this delicate equilibrium, increasing atmospheric carbon dioxide concentrations and driving global climate changes.",
    "Deep ocean hydrothermal vents host unique ecosystems thriving in complete darkness under crushing hydrostatic pressure, powered entirely by chemosynthesis rather than photosynthesis. Extremophilic bacteria oxidize toxic hydrogen sulfide spewing from geothermal chimneys, forming the primary nutritional foundation for complex organisms like giant tube worms, blind shrimp, and vent crabs.",
    "Classical orchestral composition relies on a balanced distribution of instrumental families—strings, woodwinds, brass, and percussion—to achieve rich harmonic textures and dynamic contrasts. Composers exploit distinct timbre qualities, contrapuntal voice-leading, and complex orchestrational voicings to express nuanced emotional themes and maintain structural coherence across expansive symphonic movements.",
    "Gothic cathedral architecture characterized the medieval European landscape, featuring innovative structural elements like pointed arches, ribbed vaults, and flying buttresses. These engineering advancements allowed builders to construct soaring, light-filled sanctuaries with massive stained-glass windows, shifting gravity loads outward and creating a sense of divine height and celestial illumination.",
    "Photosynthesis converts carbon dioxide and water into glucose and oxygen, powered by solar photons absorbed by chlorophyll molecules in plant chloroplasts. This complex biological process consists of light-dependent reactions that generate ATP and NADPH, followed by the light-independent Calvin cycle which fixes gaseous carbon into stable, energy-rich organic compounds.",
    "The historical scientific method emerged through the pioneering efforts of thinkers like Galileo Galilei, who insisted on empirical experimentation and mathematical description over pure scholastic speculation. By constructing advanced telescopes to observe celestial bodies and conducting precise experiments on falling objects, Galileo established systematic observation as the primary path to physical truth.",
    "Johannes Gutenberg's development of the movable-type printing press in the fifteenth century catalyzed a rapid communications revolution, dramatically increasing literacy rates and accelerating the dissemination of ideas. This technology democratized access to knowledge, fueled the Protestant Reformation, and laid the cultural and intellectual foundations for the European Scientific Revolution.",
    "Renaissance painters revolutionized fine art by developing sophisticated mathematical perspective systems and realistic lighting techniques like chiaroscuro and sfumato. By studying human anatomy and optics, artists like Leonardo da Vinci and Michelangelo captured three-dimensional form, emotional depth, and atmospheric realism on flat canvases with unprecedented accuracy.",
    "Existentialist philosophy, popularized by Jean-Paul Sartre and Albert Camus, posits that existence precedes essence, meaning humans are not born with a predefined purpose but must actively construct meaning through free choice. This freedom brings profound personal responsibility and existential dread, as individuals navigate an indifferent universe devoid of objective moral absolutes.",
    "Mitochondrial DNA is maternally inherited and encodes essential proteins involved in the electron transport chain, mutating at a significantly faster rate than nuclear DNA due to proximity to damaging reactive oxygen species. This rapid evolutionary rate makes mitochondrial genetics a powerful molecular tool for tracing maternal lineages and investigating evolutionary history.",
    "TCP congestion control algorithms like Reno and BBR optimize network throughput by dynamically adjusting the congestion window size based on packet loss or round-trip time variations. By pacing transmission rates to match the bottleneck bandwidth, these protocols prevent network congestion collapse while maximizing link utilization across heterogeneous and volatile network paths.",
    "The replication of DNA is an exceptionally high-fidelity biological process orchestrated by a complex machinery of specialized enzymes, led by DNA polymerase. Helicase unwinds the double helix, single-strand binding proteins stabilize the exposed templates, and polymerase synthesizes the new strands, utilizing built-in proofreading capabilities to correct mismatched base pairs.",
    "The global oceanic thermohaline circulation, often called the great ocean conveyor belt, is driven by differences in seawater temperature and salinity, playing a vital role in global heat distribution. Deep water forms in polar regions where cold, salty water sinks, slowly flowing through deep ocean basins before upwelling in warmer regions, modulating continental climates.",
    "Microservice architectures structure software applications as collections of loosely coupled, independently deployable services that communicate via lightweight network protocols. This decentralized approach improves fault isolation, horizontal scalability, and development velocity, though it introduces substantial complexities in distributed state management, data consistency, and network latency.",
    "Artificial neural networks learn complex representations by propagating inputs through layers of interconnected nodes, adjusting weights via backpropagation and gradient descent. During training, the system calculates the loss function's gradient relative to each parameter, propagating error backward through the network to iteratively minimize prediction errors.",
    "Optical fiber communication utilizes total internal reflection to transmit high-speed digital signals over vast distances as light pulses through pure glass cores. By cladding the core in a material with a lower refractive index, light remains tightly confined within the fiber, enabling massive data bandwidths with exceptionally low signal attenuation.",
    "Photovoltaic solar cells harness the photoelectric effect to convert solar radiation directly into electrical energy within semiconductor materials. When photons strike the p-n junction of a silicon wafer, they excite valence electrons into the conduction band, creating free electron-hole pairs that are swept by an internal electric field to generate direct current.",
    "Public key infrastructure secures modern digital communications by managing cryptographic key pairs, digital certificates, and certificate authorities. By utilizing asymmetric encryption algorithms, it enables secure identity verification, encrypted data transmission, and non-repudiable digital signatures across untrusted public networks like the global internet.",
    "Archaeological radiocarbon dating determines the age of organic materials by measuring the residual activity of the carbon-14 isotope. Because living organisms maintain a constant ratio of carbon-12 to carbon-14 which decays exponentially with a half-life of 5,730 years upon death, measuring this ratio reveals the precise time elapsed since the organism ceased exchange.",
    "Epigenetic mechanisms modulate gene expression without altering the underlying DNA sequence, utilizing DNA methylation and histone modifications to regulate chromatin accessibility. These molecular tags act as dynamic switches, allowing environmental factors like diet, stress, and toxins to exert long-term influences on cellular behavior and phenotypic traits.",
    "The Great Barrier Reef represents the largest biogenic structure on Earth, constructed by billions of tiny coral polyps secreting calcium carbonate skeletons over thousands of years. Rising sea temperatures disrupt the symbiotic relationship between corals and their photosynthetic zooxanthellae algae, triggering widespread coral bleaching events that threaten marine biodiversity.",
    "Game theory analyzes strategic interactions among rational decision-makers, modeling scenarios where each player's payoff depends on the choices of all participants. The concept of Nash Equilibrium defines a stable state where no player can unilaterally improve their outcome by changing strategies, providing profound insights into economics, biology, and international relations."
)

_BENCHMARK_SUFFIX = "\n\nWrite a highly detailed, creative, and coherent continuation of the above text:\n"

def _build_prompt(target_tokens: int, seed: int = 0xC0FFEE) -> str:
    """Build a varied, deterministic prompt by sampling without replacement until we
    overshoot, then trimming. Different from _build_exact_prompt (which uses
    /tokenize for precision) — this is the cheap, sync version.
    """
    if target_tokens <= 0:
        return " "
    rng = random.Random(seed)
    pool = list(_SEED_PARAGRAPHS)
    rng.shuffle(pool)
    char_target = int(target_tokens * 5)
    out: list[str] = []
    cur = 0
    i = 0
    while cur < char_target:
        paragraph = pool[i % len(pool)]
        sec_str = f"\n\n### CHAPTER {i // len(pool) + 1}. SECTION {i % len(pool) + 1}\n\n"
        s = sec_str + paragraph
        if i and i % len(pool) == 0:
            rng.shuffle(pool)
        out.append(s)
        cur += len(s) + 1
        i += 1
    return (" ".join(out))[:char_target]


def _varied_seed_text(seed: int = 0xC0FFEE) -> str:
    """Long shuffled seed used by the exact builder. Stable across invocations."""
    rng = random.Random(seed)
    pool = list(_SEED_PARAGRAPHS)
    rng.shuffle(pool)
    
    # We want a massive, highly varied text that can easily cover 1.5 million characters
    # so we don't hit limits or repeat identically. Let's do 120 repeats, giving ~3,600 paragraphs.
    out = []
    for i in range(len(pool) * 120):
        paragraph = pool[i % len(pool)]
        sec_str = f"\n\n### MODULE {i // len(pool) + 1} - SECTION {i % len(pool) + 1}\n\n"
        out.append(sec_str + paragraph)
        if i and i % len(pool) == 0:
            rng.shuffle(pool)
            
    return " \n\n ".join(out)


_SEED_TEXT = _varied_seed_text()


async def _tokenize(sess: aiohttp.ClientSession, base: str, text: str) -> int:
    async with sess.post(f"{base}/tokenize", json={"content": text}) as r:
        data = await r.json()
    return len(data.get("tokens", []) or [])


async def _build_exact_prompt(
    sess: aiohttp.ClientSession, base: str, target_tokens: int,
) -> tuple[str, int]:
    """Build a prompt that tokenizes to <= target_tokens, as close as possible,
    and appends a benchmark continuation suffix to prevent immediate EOS generation.
    """
    if target_tokens <= 0:
        return " ", 0

    try:
        suffix_tok = await _tokenize(sess, base, _BENCHMARK_SUFFIX)
    except Exception:
        suffix_tok = 15  # safe estimation

    adjusted_target = target_tokens - suffix_tok
    if adjusted_target < 256:
        # Small prompts: do not adjust, do not append suffix to avoid small-value instability
        adjusted_target = target_tokens
        suffix_to_append = ""
        suffix_tok = 0
    else:
        suffix_to_append = _BENCHMARK_SUFFIX

    prompt, actual = await _build_exact_prompt_body(sess, base, adjusted_target)
    
    final_prompt = prompt + suffix_to_append
    final_actual = actual + suffix_tok
    return final_prompt, final_actual


async def _build_exact_prompt_body(
    sess: aiohttp.ClientSession, base: str, target_tokens: int,
) -> tuple[str, int]:
    """Build a prompt that tokenizes to <= target_tokens, as close as possible.

    We never exceed target — overshooting can blow the model's ctx_size.
    """
    if target_tokens <= 0:
        return " ", 0

    seed_tok = await _tokenize(sess, base, _SEED_TEXT)
    if seed_tok <= 0:
        prompt = _build_prompt(target_tokens)
        return prompt, await _tokenize(sess, base, prompt)

    chars_per_tok = len(_SEED_TEXT) / seed_tok
    # Start slightly under target; grow if there is headroom.
    char_len = max(1, int(target_tokens * chars_per_tok * 0.97))
    repeats = (char_len // len(_SEED_TEXT)) + 1
    prompt = (_SEED_TEXT * repeats)[:char_len]
    actual = await _tokenize(sess, base, prompt)

    for _ in range(6):
        if actual == target_tokens:
            return prompt, actual
        if actual > target_tokens:
            # Trim by the observed ratio to land at-or-under target.
            ratio = target_tokens / max(1, actual)
            new_len = max(1, int(len(prompt) * ratio))
            if new_len >= len(prompt):
                new_len = len(prompt) - 1
            prompt = prompt[:new_len]
        else:
            need = target_tokens - actual
            add_chars = max(1, int(need * chars_per_tok * 0.97))
            extra_repeats = (add_chars // len(_SEED_TEXT)) + 1
            prompt = prompt + (_SEED_TEXT * extra_repeats)[:add_chars]
        actual = await _tokenize(sess, base, prompt)
        if actual <= target_tokens and target_tokens - actual <= max(2, target_tokens // 1000):
            return prompt, actual

    # Final safety: if still over target, hard-trim by ratio until under.
    while actual > target_tokens and len(prompt) > 1:
        ratio = target_tokens / max(1, actual)
        new_len = max(1, int(len(prompt) * ratio * 0.98))
        if new_len >= len(prompt):
            new_len = len(prompt) - 1
        prompt = prompt[:new_len]
        actual = await _tokenize(sess, base, prompt)
    return prompt, actual


async def run_speed_test(
    target_prompt_tokens: int,
    n_predict: int = 128,
    timeout_sec: float = 600.0,
) -> dict:
    """Run a single benchmark pass against llama-server.

    Returns:
        ok, error, actual_prompt_tokens, prompt_n, prompt_ms, prompt_per_second,
        predicted_n, predicted_ms, predicted_per_second, wall_ms, model
    """
    base = cfg.upstream_url()

    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    out: dict = {
        "ok": False,
        "error": "",
        "actual_prompt_tokens": 0,
        "prompt_n": 0,
        "prompt_ms": 0.0,
        "prompt_per_second": 0.0,
        "predicted_n": 0,
        "predicted_ms": 0.0,
        "predicted_per_second": 0.0,
        "wall_ms": 0.0,
        "model": "",
    }

    sup = None
    try:
        from process import _SUPERVISOR as sup  # type: ignore[assignment]
    except Exception:
        sup = None

    async with aiohttp.ClientSession(timeout=timeout) as sess:
        try:
            prompt, actual = await _build_exact_prompt(sess, base, target_prompt_tokens)
            out["actual_prompt_tokens"] = actual
        except Exception as exc:
            out["error"] = f"tokenize failed: {exc}"
            return out

        # Use the user's configured sampling so the bench matches real
        # request behavior. Greedy decoding (temp=0, top_k=1) hands
        # speculative draft models near-100% acceptance on this kind of
        # synthetic prompt and inflates reported tok/s.
        infer = cfg.inference_for(cfg.default_model())
        payload: dict = {
            "prompt": prompt,
            "n_predict": int(n_predict),
            "cache_prompt": False,
            "stream": False,
            "seed": 0xC0FFEE,
        }
        for src, dst in (
            ("temperature", "temperature"),
            ("top_k", "top_k"),
            ("top_p", "top_p"),
            ("min_p", "min_p"),
            ("repeat_penalty", "repeat_penalty"),
            ("presence_penalty", "presence_penalty"),
            ("frequency_penalty", "frequency_penalty"),
        ):
            if src in infer and infer[src] is not None:
                payload[dst] = infer[src]

        if sup is not None:
            try:
                await sup.begin_request()
            except Exception:
                pass
        try:
            t0 = time.monotonic()
            try:
                async with sess.post(f"{base}/completion", json=payload) as r:
                    if r.status >= 400:
                        body = await r.text()
                        out["error"] = f"HTTP {r.status}: {body[:200]}"
                        return out
                    data = await r.json()
            except Exception as exc:
                out["error"] = f"completion failed: {exc}"
                return out
            out["wall_ms"] = (time.monotonic() - t0) * 1000.0
        except Exception as exc:
            out["error"] = f"completion post-processing failed: {exc}"
            return out
        finally:
            if sup is not None:
                try:
                    await sup.end_request()
                except Exception:
                    pass

    if isinstance(data, dict) and data.get("error"):
        out["error"] = str(data["error"])
        return out

    timings = (data.get("timings") or {}) if isinstance(data, dict) else {}
    out["prompt_n"] = int(timings.get("prompt_n", 0) or 0)
    out["prompt_ms"] = float(timings.get("prompt_ms", 0) or 0)
    out["prompt_per_second"] = float(timings.get("prompt_per_second", 0) or 0)
    out["predicted_n"] = int(timings.get("predicted_n", 0) or 0)
    out["predicted_ms"] = float(timings.get("predicted_ms", 0) or 0)
    out["predicted_per_second"] = float(timings.get("predicted_per_second", 0) or 0)
    out["model"] = str(data.get("model", "") or "") if isinstance(data, dict) else ""
    out["ok"] = True
    return out
