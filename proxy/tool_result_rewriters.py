"""Generic framework for rewriting tool_result content in conversation history.

When the local model is proxied to LM Studio, several Claude Code built-in
tools (WebSearch most prominently) return empty placeholder strings because
their real backends aren't reachable. This module walks the messages array on
each /v1/messages request and lets registered rewriters substitute real
content for those placeholders, so the local model sees usable data.

Each rewriter targets one tool by name and decides per-block whether it wants
to replace the result. Rewriters are async because they typically call out to
external APIs (search, embeddings, etc).

Rewriters live as drop-in modules under `proxy/rewriters/`. The package is
auto-imported here, so just creating a new file is enough — no edits needed
to this module or to `server.py`. Each module should look like:

    from proxy.tool_result_rewriters import register

    class MyRewriter:
        tool_name = "MyTool"

        def should_replace(self, original):
            return isinstance(original, str) and "fail" in original

        async def replace(self, tool_input, original):
            return await my_backend(tool_input["query"])

    register(MyRewriter())
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

log = logging.getLogger("telecode.proxy.rewriters")


class ToolResultRewriter(Protocol):
    """Interface for tool_result rewriters.

    Implementations declare which tool they target via `tool_name`, decide
    per-block whether they want to replace the result, and produce the new
    content. `should_replace` must be cheap and synchronous; `replace` may do
    network I/O.
    """

    tool_name: str

    def should_replace(self, original: Any) -> bool:
        ...

    async def replace(self, tool_input: dict[str, Any], original: Any) -> Any:
        ...


_REGISTRY: dict[str, ToolResultRewriter] = {}


def register(rewriter: ToolResultRewriter) -> None:
    """Register a rewriter. Last write wins for a given tool name."""
    _REGISTRY[rewriter.tool_name] = rewriter
    log.info("Registered tool_result rewriter for %s", rewriter.tool_name)


def registered_tools() -> list[str]:
    return list(_REGISTRY.keys())


# ── Convenience factory ────────────────────────────────────────────────────

ShouldReplaceFn = Callable[[Any], bool]
ReplaceFn = Callable[[dict[str, Any], Any], Awaitable[Any]]


@dataclass
class _FunctionRewriter:
    tool_name: str
    _should: ShouldReplaceFn
    _replace: ReplaceFn

    def should_replace(self, original: Any) -> bool:
        return self._should(original)

    async def replace(self, tool_input: dict[str, Any], original: Any) -> Any:
        return await self._replace(tool_input, original)


def make_rewriter(
    tool_name: str,
    should_replace: ShouldReplaceFn,
    replace: ReplaceFn,
) -> ToolResultRewriter:
    """Build a rewriter from two plain functions and register it.

    Returned for callers that want a handle, but the registration side-effect
    is what actually plugs it into the framework. Use this when you don't need
    state beyond what the closure captures.
    """
    rewriter = _FunctionRewriter(tool_name, should_replace, replace)
    register(rewriter)
    return rewriter


def _build_tool_use_index(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map tool_use_id -> tool_use block (so we can look up name + input from a tool_result)."""
    idx: dict[str, dict[str, Any]] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tu_id = block.get("id", "")
                if tu_id:
                    idx[tu_id] = block
    return idx


async def rewrite_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Walk messages and apply registered rewriters to matching tool_results.

    Returns a new messages list (does not mutate input). All rewriters that
    fire on a single pass run concurrently to amortize network latency.
    """
    if not _REGISTRY:
        return messages

    tool_use_idx = _build_tool_use_index(messages)

    # First pass: collect rewrite jobs without doing any I/O so we can run
    # them concurrently and then splice the results back in deterministically.
    @dataclass
    class _Job:
        msg_index: int
        block_index: int
        rewriter: ToolResultRewriter
        tool_input: dict[str, Any]
        original: Any

    jobs: list[_Job] = []
    for mi, msg in enumerate(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for bi, block in enumerate(content):
            if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                continue
            tu_id = block.get("tool_use_id", "")
            tu = tool_use_idx.get(tu_id)
            if not tu:
                continue
            rewriter = _REGISTRY.get(tu.get("name", ""))
            if rewriter is None:
                continue
            original = block.get("content")
            try:
                if not rewriter.should_replace(original):
                    continue
            except Exception as exc:
                log.warning("%s.should_replace raised %s", rewriter.tool_name, exc)
                continue
            jobs.append(_Job(mi, bi, rewriter, tu.get("input", {}) or {}, original))

    if not jobs:
        return messages

    async def _run_one(job: _Job):
        try:
            return await job.rewriter.replace(job.tool_input, job.original)
        except Exception as exc:
            log.warning("%s.replace raised %s", job.rewriter.tool_name, exc)
            return None

    results = await asyncio.gather(*(_run_one(j) for j in jobs))

    # Build a new messages list with rewritten blocks spliced in.
    out = [dict(m) for m in messages]
    for job, new_content in zip(jobs, results):
        if new_content is None:
            continue
        msg = out[job.msg_index]
        new_blocks = list(msg.get("content", []))
        old_block = new_blocks[job.block_index]
        if isinstance(old_block, dict):
            new_blocks[job.block_index] = {**old_block, "content": new_content}
            msg["content"] = new_blocks
            log.info(
                "Rewrote %s tool_result (msg=%d block=%d)",
                job.rewriter.tool_name, job.msg_index, job.block_index,
            )

    return out


# Auto-import the rewriters package so each drop-in module's `register()` call
# fires. Done at the bottom so `register` is defined before any rewriter loads.
# Wrapped in try/except so a broken rewriter file can't break the framework.
try:
    from proxy import rewriters as _rewriters_pkg  # noqa: F401
except Exception as _exc:  # pragma: no cover
    log.warning("Failed to auto-load proxy.rewriters package: %s", _exc)
