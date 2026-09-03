"""Runtime capability probes for the llama-server we're talking to.

Telecode has to work against a stock release *and* against a build carrying
`patches/llama.cpp/0001-common-add-defer_loading-to-tool-definitions.patch`.
Rather than gate on a build number — which says nothing about whether a local
build carries our patch — each capability is probed functionally, once per
server, and cached.

`defer_loading` is probed through `/apply-template`, which renders a prompt and
returns it *without running inference*. That matters: the obvious alternative
(a tiny completion) would take the single slot and evict a long conversation's
prefix cache, which is the exact failure this whole feature exists to avoid.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import aiohttp

from llamacpp import config as llama_cfg

log = logging.getLogger("telecode.proxy.llama_caps")

# (base_url, model) -> {capability: bool}. Cleared on model swap by the caller.
_cache: dict[tuple[str, str], dict[str, bool]] = {}

_PROBE_TIMEOUT = 20.0

# A name that will never collide with a real tool, and is distinctive enough to
# search for in the rendered prompt.
_PROBE_DEFERRED = "telecode__probe_deferred_tool"
_PROBE_VISIBLE = "telecode__probe_visible_tool"


def _tool(name: str, *, defer: bool) -> dict[str, Any]:
    fn: dict[str, Any] = {
        "name": name,
        "description": "probe",
        "parameters": {"type": "object", "properties": {}},
    }
    if defer:
        fn["defer_loading"] = True
    return {"type": "function", "function": fn}


def invalidate(model: str = "") -> None:
    """Drop cached capabilities. Call on model swap or server restart —
    a different binary may answer differently."""
    if not model:
        _cache.clear()
        return
    for key in [k for k in _cache if k[1] == model]:
        _cache.pop(key, None)


async def supports_defer_loading(model: str) -> bool:
    """True when the server honours `defer_loading` on a tool definition.

    Probe: render a two-tool prompt where one tool is deferred. A server that
    supports the flag omits that tool from the prompt while keeping it callable;
    a stock server ignores the unknown field and renders both.

    Any failure answers False — falling back to telecode's own core/deferred
    split is always correct, just slower.
    """
    key = (llama_cfg.upstream_url(), model)
    cached = _cache.get(key)
    if cached is not None and "defer_loading" in cached:
        return cached["defer_loading"]

    ok = False
    try:
        body = {
            "messages": [{"role": "user", "content": "probe"}],
            "tools": [
                _tool(_PROBE_VISIBLE, defer=False),
                _tool(_PROBE_DEFERRED, defer=True),
            ],
        }
        timeout = aiohttp.ClientTimeout(total=_PROBE_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{llama_cfg.upstream_url()}/apply-template",
                                    json=body) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    prompt = str(data.get("prompt", "") or "")
                    # Both names must be reasoned about: if the VISIBLE one is
                    # missing too, the template simply doesn't render tools and
                    # the probe says nothing about defer_loading.
                    if _PROBE_VISIBLE in prompt:
                        ok = _PROBE_DEFERRED not in prompt
                    else:
                        log.debug("defer_loading probe inconclusive: template "
                                  "rendered no tools")
                else:
                    log.debug("defer_loading probe: HTTP %s", resp.status)
    except Exception as exc:
        log.debug("defer_loading probe failed (%s) — assuming unsupported", exc)

    _cache.setdefault(key, {})["defer_loading"] = ok
    log.info("llama-server %s: defer_loading %s", model,
             "SUPPORTED — declaring all tools, prefix stays stable"
             if ok else "not supported — using the proxy-side core/deferred split")
    return ok
