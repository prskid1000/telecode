"""Lightweight LLM utility for proxy-internal structured calls.

Calls llama-server directly (NOT through our proxy — avoids recursion)
via its OpenAI-compatible /v1/chat/completions endpoint. Used by managed
tool handlers for pre-processing (query classification, intent detection,
entity extraction, etc.). Typically <1s with ~100 tokens in, ~20 out.
"""
from __future__ import annotations

import json
from typing import Any

import aiohttp

from llamacpp import config as llama_cfg


async def structured_call(
    prompt: str,
    schema: dict[str, Any],
    *,
    max_tokens: int = 100,
    temperature: float = 0,
    schema_name: str = "response",
) -> dict[str, Any]:
    """Call llama-server and get a structured JSON response.

    Returns {} on any error — callers must have a fallback default.
    """
    upstream = llama_cfg.upstream_url()
    model = llama_cfg.default_model()

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        },
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{upstream}/v1/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as resp:
                data = await resp.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content) if isinstance(content, str) else content
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}
