"""Lightweight LLM utility for proxy-internal structured calls.

Calls the upstream model (LM Studio) via /v1/chat/completions with
JSON structured output. Used by managed tool handlers for pre-processing
(query classification, intent detection, entity extraction, etc.).

Calls go directly to the upstream (NOT through our proxy — avoids
recursion). Typically <1s with ~100 tokens in, ~20 out.
"""
from __future__ import annotations

import json
from typing import Any

import aiohttp

from proxy import config as proxy_config


async def structured_call(
    prompt: str,
    schema: dict[str, Any],
    *,
    max_tokens: int = 100,
    temperature: float = 0,
    schema_name: str = "response",
) -> dict[str, Any]:
    """Call the upstream LLM and get a structured JSON response.

    Args:
        prompt: The user message to send.
        schema: JSON Schema for the response (the `schema` field inside
            `response_format.json_schema`). Must have `type: "object"`.
        max_tokens: Cap on output tokens (keep small for speed).
        temperature: 0 for deterministic classification.
        schema_name: Name for the json_schema wrapper.

    Returns:
        Parsed JSON dict from the model's response.
        Returns {} on any error (timeout, parse failure, etc.) — callers
        should always have a fallback default.
    """
    upstream = proxy_config.upstream_url()
    model = proxy_config.upstream_model()

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
