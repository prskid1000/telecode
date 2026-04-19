"""Wrappers around llama-server's native `/tokenize` and `/apply-template`.

Used for accurate `/v1/messages/count_tokens` (no need for the old
`max_tokens=1` round-trip hack) and anywhere else we need to know exact
token counts before sending a request.
"""
from __future__ import annotations

from typing import Any

import aiohttp

from llamacpp import config as llama_cfg


async def apply_template(messages: list[dict[str, Any]]) -> str:
    """Render a chat-style message list through the active model's chat
    template. Returns the flattened string that will be tokenized.

    Falls back to a naive concatenation if llama-server is unreachable or
    the endpoint isn't available.
    """
    url = f"{llama_cfg.upstream_url()}/apply-template"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"messages": messages},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return _naive_flatten(messages)
                data = await resp.json(content_type=None)
                return str(data.get("prompt", "") or _naive_flatten(messages))
    except (aiohttp.ClientError, TimeoutError):
        return _naive_flatten(messages)


def _naive_flatten(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            text = "\n".join(
                c.get("text", "") for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            )
        else:
            text = str(content or "")
        parts.append(f"{role}: {text}")
    return "\n".join(parts)


async def count_tokens(text_or_messages: str | list[dict[str, Any]]) -> int:
    """Return the exact token count for a prompt string or message list.

    If a message list is passed, we first render it through the model's
    chat template, then tokenize the resulting string.
    """
    if isinstance(text_or_messages, list):
        prompt = await apply_template(text_or_messages)
    else:
        prompt = text_or_messages

    url = f"{llama_cfg.upstream_url()}/tokenize"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json={"content": prompt},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return 0
                data = await resp.json(content_type=None)
                tokens = data.get("tokens", [])
                return len(tokens) if isinstance(tokens, list) else 0
    except (aiohttp.ClientError, TimeoutError):
        return 0
