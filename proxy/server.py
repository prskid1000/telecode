"""
Anthropic-compatible streaming proxy with ToolSearch interception.

Sits between Claude Code and LM Studio (or any OpenAI-compatible backend).
Intercepts tool lists, defers non-core tools, and handles ToolSearch round-trips.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp
from aiohttp import web

from proxy import config as proxy_config
from proxy.tool_registry import (
    split_tools, rewrite_messages, strip_all_reminders, proxy_system_instruction,
)
from proxy.tool_search import BM25Index

log = logging.getLogger("telecode.proxy")


def _format_functions_block(matched: list[dict[str, Any]]) -> str:
    """Format matched tools as a <functions> block matching real Claude Code ToolSearch."""
    if not matched:
        return "No matching tools found. Try a different query."
    lines = ["<functions>"]
    for t in matched:
        # Match Claude Code format: {"description": ..., "name": ..., "parameters": ...}
        entry = {
            "description": t.get("description", ""),
            "name": t.get("name", ""),
            "parameters": t.get("input_schema", {}),
        }
        lines.append(f"<function>{json.dumps(entry)}</function>")
    lines.append("</functions>")
    return "\n".join(lines)


# ── Request handling ─────────────────────────────────────────────────────────

async def _forward_stream(
    upstream: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    resp: web.StreamResponse,
    intercept_names: set[str] | None = None,
) -> dict[str, Any] | None:
    """Buffer response from upstream, check for interceptable tool calls.

    If no intercept needed, flushes entire buffer to client.
    If intercept found, returns tool_use block without flushing.
    """
    buffered_lines: list[str] = []  # raw SSE lines to flush if clean
    tool_use_block: dict[str, Any] | None = None
    current_tool_json = ""
    current_tool_id = ""
    current_tool_name = ""

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{upstream}/v1/messages",
            json=payload,
            headers=headers,
        ) as upstream_resp:
            if upstream_resp.status != 200:
                body = await upstream_resp.text()
                if not resp.prepared:
                    resp.set_status(upstream_resp.status)
                    await resp.prepare(resp._req if hasattr(resp, '_req') else None)
                await resp.write(body.encode())
                return None

            # Buffer all SSE events
            buf = ""
            async for chunk in upstream_resp.content.iter_any():
                text = chunk.decode("utf-8", errors="replace")
                buf += text

                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.rstrip("\r")

                    if not line.startswith("data: "):
                        buffered_lines.append(f"{line}\n")
                        continue

                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        buffered_lines.append("data: [DONE]\n\n")
                        continue

                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        buffered_lines.append(f"data: {data_str}\n\n")
                        continue

                    buffered_lines.append(f"data: {data_str}\n\n")
                    etype = event.get("type", "")

                    # Track tool_use blocks
                    if etype == "content_block_start":
                        block = event.get("content_block", {})
                        if block.get("type") == "tool_use":
                            current_tool_name = block.get("name", "")
                            current_tool_id = block.get("id", "")
                            current_tool_json = ""

                    if etype == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "input_json_delta":
                            current_tool_json += delta.get("partial_json", "")

                    if etype == "content_block_stop" and intercept_names and current_tool_name in intercept_names:
                        try:
                            args = json.loads(current_tool_json) if current_tool_json else {}
                        except json.JSONDecodeError:
                            args = {}
                        tool_use_block = {
                            "id": current_tool_id,
                            "name": current_tool_name,
                            "input": args,
                        }
                        current_tool_name = ""

    # If intercepted, don't flush — caller handles round-trip
    if tool_use_block:
        return tool_use_block

    # Clean response — flush entire buffer to client
    if not resp.prepared:
        resp.content_type = "text/event-stream"
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["Connection"] = "keep-alive"
        await resp.prepare(resp._req if hasattr(resp, '_req') else None)
    for line in buffered_lines:
        await resp.write(line.encode())

    return None


async def _do_tool_search(
    deferred: list[dict[str, Any]],
    args: dict[str, Any],
) -> list[dict[str, Any]]:
    """Execute ToolSearch and return matching tool definitions.

    Query forms (matching Claude Code's real ToolSearch):
      select:Read,Edit,Grep  — exact name match, comma-separated
      +slack send             — require "slack" in name, rank by remaining terms
      notebook jupyter        — keyword search (BM25)
    """
    query = args.get("query", "")
    max_results = args.get("max_results", 5)

    # select:Name1,Name2 — exact name lookup
    if query.startswith("select:"):
        names = {n.strip() for n in query[7:].split(",") if n.strip()}
        return [t for t in deferred if t.get("name", "") in names]

    # +required_in_name rest of keywords — filter by name, rank remainder
    if query.startswith("+"):
        parts = query.split(None, 1)
        required = parts[0][1:].lower()  # strip the +
        filtered = [t for t in deferred if required in t.get("name", "").lower()]
        if len(parts) > 1 and filtered:
            index = BM25Index(filtered)
            return index.search(parts[1], max_results)
        return filtered[:max_results]

    # Default: BM25 keyword search
    index = BM25Index(deferred)
    return index.search(query, max_results)


# ── Debug dump (disabled — set _DEBUG = True to enable) ──────────────────────

_dump_counter = 0
_MAX_DUMP_FILES = 50


async def _dump_request(body: dict[str, Any], label: str) -> None:
    """Dump request to log file. Enable via proxy.debug in settings.json.

    Rotates to keep only the last _MAX_DUMP_FILES files.
    """
    if not proxy_config.debug():
        return
    import os, glob as globmod, aiofiles
    global _dump_counter
    _dump_counter += 1
    dump_dir = os.path.join(os.path.dirname(__file__), "..", "data", "logs")
    os.makedirs(dump_dir, exist_ok=True)
    full_path = os.path.join(dump_dir, f"proxy_full_{_dump_counter}.json")
    async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
        await f.write(json.dumps({"label": label, "body": body}, indent=2, ensure_ascii=False))
    log.info("Proxy debug #%d: %s -> %s", _dump_counter, label, full_path)

    # Rotate: keep only last _MAX_DUMP_FILES
    files = sorted(globmod.glob(os.path.join(dump_dir, "proxy_full_*.json")))
    for old in files[:-_MAX_DUMP_FILES]:
        try:
            os.remove(old)
        except OSError:
            pass


async def handle_messages(request: web.Request) -> web.StreamResponse:
    """Main proxy endpoint: POST /v1/messages"""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    await _dump_request(body, "INCOMING")

    upstream = proxy_config.upstream_url()
    deferred: list[dict[str, Any]] = []

    if proxy_config.tool_splitting():
        tools = body.get("tools", [])
        core, deferred = split_tools(tools)
        body["tools"] = core

        log.info(
            "Proxy: %d tools -> %d core + %d deferred",
            len(tools), len(core), len(deferred),
        )

        # Inject deferred tools instruction into system, tool names into messages
        if deferred:
            instruction = proxy_system_instruction()
            system = body.get("system", "")
            if isinstance(system, str):
                body["system"] = f"{instruction}\n\n{system}" if system else instruction
            elif isinstance(system, list):
                system.insert(0, {"type": "text", "text": instruction})
            body["messages"] = rewrite_messages(body.get("messages", []), deferred)

    elif proxy_config.strip_reminders():
        # Strip reminders even without tool splitting
        body["messages"] = strip_all_reminders(body.get("messages", []))

    await _dump_request(body, "OUTGOING")

    # Forward auth headers
    headers = {}
    for h in ("x-api-key", "anthropic-version", "authorization", "content-type"):
        if h in request.headers:
            headers[h] = request.headers[h]
    headers.setdefault("content-type", "application/json")

    if body.get("stream", False):
        return await _handle_streaming(upstream, body, headers, deferred, request)
    else:
        return await _handle_non_streaming(upstream, body, headers, deferred)


async def _handle_streaming(
    upstream: str,
    body: dict[str, Any],
    headers: dict[str, str],
    deferred: list[dict[str, Any]],
    request: web.Request,
) -> web.StreamResponse:
    """Handle streaming request with ToolSearch and auto-load interception."""
    # Build set of tool names to intercept
    intercept: set[str] | None = None
    deferred_names = {t["name"] for t in deferred}
    if deferred:
        intercept = {"ToolSearch"}
        if proxy_config.auto_load_tools():
            intercept |= deferred_names

    resp = web.StreamResponse()
    resp._req = request

    tool_use = await _forward_stream(upstream, body, headers, resp, intercept)

    if tool_use:
        tool_name = tool_use["name"]

        if tool_name == "ToolSearch":
            matched = await _do_tool_search(deferred, tool_use["input"])
            log.info("ToolSearch matched %d tools for query=%r", len(matched), tool_use["input"].get("query"))
            result_content = _format_functions_block(matched)

        elif proxy_config.auto_load_tools() and tool_name in deferred_names:
            matched = [t for t in deferred if t["name"] == tool_name]
            log.info("Auto-loading schema for deferred tool: %s", tool_name)
            schema_info = _format_functions_block(matched)
            result_content = (
                f"This tool's schema was not loaded. Here is the schema:\n\n"
                f"{schema_info}\n\n"
                f"Call the tool again with the correct parameter names from the schema above."
            )
        else:
            matched = []
            result_content = None

        if result_content:
            if matched:
                body["tools"] = body["tools"] + matched

            body["messages"] = body.get("messages", []) + [
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": tool_use["id"],
                     "name": tool_name, "input": tool_use["input"]},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tool_use["id"],
                     "content": result_content},
                ]},
            ]

            # Round-trip — stream retry to client
            if not resp.prepared:
                resp.content_type = "text/event-stream"
                resp.headers["Cache-Control"] = "no-cache"
                resp.headers["Connection"] = "keep-alive"
                await resp.prepare(request)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{upstream}/v1/messages",
                    json=body,
                    headers=headers,
                ) as upstream_resp:
                    async for chunk in upstream_resp.content.iter_any():
                        await resp.write(chunk)

    if not resp.prepared:
        await resp.prepare(request)
    await resp.write_eof()
    return resp


async def _handle_non_streaming(
    upstream: str,
    body: dict[str, Any],
    headers: dict[str, str],
    deferred: list[dict[str, Any]],
) -> web.Response:
    """Handle non-streaming request."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{upstream}/v1/messages",
            json=body,
            headers=headers,
        ) as upstream_resp:
            result = await upstream_resp.json()

    # Check if response contains ToolSearch or deferred tool call
    deferred_names = {t["name"] for t in deferred}
    stop_reason = result.get("stop_reason", "")
    if stop_reason == "tool_use":
        for block in result.get("content", []):
            if block.get("type") != "tool_use":
                continue
            tool_name = block.get("name", "")

            if tool_name == "ToolSearch":
                matched = await _do_tool_search(deferred, block.get("input", {}))
                log.info("ToolSearch matched %d tools", len(matched))
                result_text = _format_functions_block(matched)

            elif proxy_config.auto_load_tools() and tool_name in deferred_names:
                matched = [t for t in deferred if t["name"] == tool_name]
                log.info("Auto-loading schema for deferred tool: %s", tool_name)
                schema_info = _format_functions_block(matched)
                result_text = (
                    f"This tool's schema was not loaded. Here is the schema:\n\n"
                    f"{schema_info}\n\n"
                    f"Call the tool again with the correct parameter names from the schema above."
                )
            else:
                continue

            # Re-send with matched tools injected
            if matched:
                body["tools"] = body["tools"] + matched
            body["messages"] = body.get("messages", []) + [
                {"role": "assistant", "content": result.get("content", [])},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": block["id"],
                     "content": result_text},
                ]},
            ]

            async with aiohttp.ClientSession() as session2:
                async with session2.post(
                    f"{upstream}/v1/messages",
                    json=body,
                    headers=headers,
                ) as resp2:
                    return web.json_response(await resp2.json(), status=resp2.status)

    return web.json_response(result, status=200)


# ── Passthrough for non-messages endpoints ───────────────────────────────────

async def handle_passthrough(request: web.Request) -> web.Response:
    """Forward any non-/v1/messages request unchanged."""
    upstream = proxy_config.upstream_url()
    path = request.path
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "transfer-encoding")}

    body = await request.read()

    async with aiohttp.ClientSession() as session:
        async with session.request(
            request.method,
            f"{upstream}{path}",
            headers=headers,
            data=body if body else None,
        ) as upstream_resp:
            resp_body = await upstream_resp.read()
            return web.Response(
                body=resp_body,
                status=upstream_resp.status,
                content_type=upstream_resp.content_type,
            )


# ── App factory ──────────────────────────────────────────────────────────────

def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/v1/messages", handle_messages)
    # Passthrough everything else (models, health, etc.)
    app.router.add_route("*", "/{path:.*}", handle_passthrough)
    return app


async def start_proxy_background() -> aiohttp.web.AppRunner | None:
    """Start proxy as a background task (non-blocking). Returns runner for cleanup."""
    if not proxy_config.enabled():
        log.info("Proxy disabled in settings")
        return None

    port = proxy_config.proxy_port()
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    log.info("Tool-search proxy listening on http://127.0.0.1:%d -> %s", port, proxy_config.upstream_url())
    return runner
