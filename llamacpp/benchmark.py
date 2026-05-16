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


# A varied corpus — opening sentences from many different public-domain works,
# plus technical/news/code-ish snippets. The point is to keep the n-gram
# distribution wide so speculative draft models (ngram, draft-LM) don't get
# free acceptance the way they do on a single repeated paragraph.
_SEED_SENTENCES: tuple[str, ...] = (
    "The quick brown fox jumps over the lazy dog.",
    "Far out in the uncharted backwaters of the unfashionable end of the western spiral arm of the galaxy lies a small unregarded yellow sun.",
    "It was a bright cold day in April, and the clocks were striking thirteen.",
    "All happy families are alike; each unhappy family is unhappy in its own way.",
    "In the beginning the Universe was created; this has made a lot of people very angry and been widely regarded as a bad move.",
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.",
    "Call me Ishmael. Some years ago, never mind how long precisely, having little or no money in my purse, I thought I would sail about.",
    "It is a truth universally acknowledged, that a single man in possession of a good fortune, must be in want of a wife.",
    "Mr and Mrs Dursley, of number four, Privet Drive, were proud to say that they were perfectly normal, thank you very much.",
    "Many years later, as he faced the firing squad, Colonel Aureliano Buendia was to remember that distant afternoon when his father took him to discover ice.",
    "It was the best of times, it was the worst of times, it was the age of wisdom, it was the age of foolishness.",
    "The sky above the port was the color of television, tuned to a dead channel.",
    "In a hole in the ground there lived a hobbit; not a nasty, dirty, wet hole filled with the ends of worms and an oozy smell.",
    "Once upon a time, there was a woman who discovered she had turned into the wrong person.",
    "You don't know about me without you have read a book by the name of The Adventures of Tom Sawyer, but that ain't no matter.",
    "The Pacific Ocean covers more than thirty percent of the Earth's surface and contains more than half its free water.",
    "Photosynthesis converts carbon dioxide and water into glucose and oxygen, powered by photons in the visible range.",
    "The mitochondrion is a double-membrane-bound organelle found in most eukaryotic cells, generating most of the cell's supply of ATP.",
    "Quantum entanglement is a physical phenomenon that occurs when groups of particles share a quantum state, even when separated by large distances.",
    "Black holes form when a massive star collapses under its own gravity, leaving behind a region from which not even light can escape.",
    "Reinforcement learning trains agents to maximize cumulative reward through trial and error in an environment with delayed feedback signals.",
    "Transformers use self-attention to weigh the relative importance of every token in a sequence, allowing parallel computation across positions.",
    "The Linux kernel scheduler decides which runnable process gets the CPU next, balancing throughput, latency, fairness, and power consumption.",
    "A cache coherence protocol ensures that local copies of shared data across multiple processor caches remain consistent under concurrent writes.",
    "TCP guarantees in-order, reliable delivery on top of an unreliable IP network by sequencing bytes, retransmitting losses, and pacing flow.",
    "Public-key cryptography uses a pair of mathematically linked keys so that anyone can encrypt for a recipient but only the recipient can decrypt.",
    "Garbage collection automates memory reclamation by tracing reachable objects from a root set and freeing whatever is no longer accessible.",
    "Database indexes trade write amplification and storage for dramatically faster lookups, especially on selective columns in large tables.",
    "The federal reserve raised interest rates by twenty-five basis points on Wednesday, signaling a more cautious stance amid persistent inflation.",
    "Markets opened lower on news of slowing manufacturing output in Asia, though tech shares rallied on a stronger-than-expected earnings report.",
    "Researchers at MIT announced a new battery chemistry that could roughly double the energy density of commercial lithium-ion cells.",
    "Drought conditions in the southwest deepened over the summer, prompting emergency water-use restrictions across three counties.",
    "The orchestra concluded with a stirring rendition of Beethoven's seventh symphony, the audience rising before the final chord had faded.",
    "She tightened the bowline, leaned back into the harness, and stepped off the ledge, trusting the rope and the anchor and very little else.",
    "Coffee, when grown in volcanic soil at altitude and slow-dried in the husk, develops a winey acidity that pairs unexpectedly with dark chocolate.",
    "The recipe called for browning the butter until it smelled like toasted hazelnuts, then folding it gently into the egg yolks before adding the flour.",
    "On long flights I find that a thin merino layer, broken-in shoes, and a paperback you do not mind losing make the indignities of travel bearable.",
    "She paused at the threshold, listening, and for a moment everything in the house seemed to be holding its breath along with her.",
    "Function composition, currying, and immutable data structures together form the practical core of most functional programming styles.",
    "When debugging a heisenbug, instrument first, hypothesize second, and never trust a fix you cannot explain mechanism by mechanism.",
)


def _build_prompt(target_tokens: int, seed: int = 0xC0FFEE) -> str:
    """Build a varied, deterministic prompt by sampling without replacement until we
    overshoot, then trimming. Different from _build_exact_prompt (which uses
    /tokenize for precision) — this is the cheap, sync version.
    """
    if target_tokens <= 0:
        return " "
    rng = random.Random(seed)
    pool = list(_SEED_SENTENCES)
    rng.shuffle(pool)
    char_target = int(target_tokens * 5)
    out: list[str] = []
    cur = 0
    i = 0
    while cur < char_target:
        s = pool[i % len(pool)]
        if i and i % len(pool) == 0:
            rng.shuffle(pool)
        out.append(s)
        cur += len(s) + 1
        i += 1
    return (" ".join(out))[:char_target]


def _varied_seed_text(seed: int = 0xC0FFEE) -> str:
    """Long shuffled seed used by the exact builder. Stable across invocations."""
    rng = random.Random(seed)
    pool = list(_SEED_SENTENCES)
    rng.shuffle(pool)
    # Pre-build enough text that callers rarely need a second shuffle.
    return " ".join(pool * 6)


_SEED_TEXT = _varied_seed_text()


async def _tokenize(sess: aiohttp.ClientSession, base: str, text: str) -> int:
    async with sess.post(f"{base}/tokenize", json={"content": text}) as r:
        data = await r.json()
    return len(data.get("tokens", []) or [])


async def _build_exact_prompt(
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
