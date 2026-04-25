"""Throughput benchmark — talks to llama-server directly, bypassing the proxy.

Builds a synthetic prompt of approximately N tokens, sends it to
`/completion` with `cache_prompt:false` (cold prompt-eval), generates a
fixed number of tokens, and returns the timings llama-server reports.

Hits `cfg.upstream_url()` directly so proxy transforms (system-prompt
injection, tool injection, model-mapping) don't skew the numbers.
"""
from __future__ import annotations

import time
import aiohttp

from llamacpp import config as cfg


_SEED_TEXT = (
    "The quick brown fox jumps over the lazy dog. "
    "Far out in the uncharted backwaters of the unfashionable end of the "
    "western spiral arm of the galaxy lies a small unregarded yellow sun. "
    "It was a bright cold day in April, and the clocks were striking thirteen. "
    "All happy families are alike; each unhappy family is unhappy in its own way. "
    "In the beginning the Universe was created. This has made a lot of people "
    "very angry and been widely regarded as a bad move. "
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua. "
)


def _build_prompt(target_tokens: int) -> str:
    if target_tokens <= 0:
        return " "
    # English averages ~4 chars/token on llama tokenizers; 5 gives a safe upper bound.
    char_target = int(target_tokens * 5)
    repeats = (char_target // len(_SEED_TEXT)) + 1
    return (_SEED_TEXT * repeats)[:char_target]


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

        payload = {
            "prompt": prompt,
            "n_predict": int(n_predict),
            "cache_prompt": False,
            "stream": False,
            "temperature": 0.0,
            "top_k": 1,
        }

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
