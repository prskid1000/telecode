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
from proxy.tool_registry import split_tools, rewrite_messages
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
) -> dict[str, Any] | None:
    """Stream response from upstream to client. Returns parsed result if tool_use detected."""
    collected_events: list[dict[str, Any]] = []
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
                resp.set_status(upstream_resp.status)
                await resp.prepare(upstream_resp._request if hasattr(upstream_resp, '_request') else None)
                await resp.write(body.encode())
                return None

            # Stream SSE events
            buffer = ""
            async for chunk in upstream_resp.content.iter_any():
                text = chunk.decode("utf-8", errors="replace")
                buffer += text

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.rstrip("\r")

                    if not line.startswith("data: "):
                        # Forward non-data lines (event:, empty lines)
                        if resp.prepared:
                            await resp.write(f"{line}\n".encode())
                        continue

                    data_str = line[6:]  # strip "data: "
                    if data_str.strip() == "[DONE]":
                        if resp.prepared:
                            await resp.write(b"data: [DONE]\n\n")
                        continue

                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        if resp.prepared:
                            await resp.write(f"{line}\n".encode())
                        continue

                    collected_events.append(event)
                    etype = event.get("type", "")

                    # Track tool_use blocks
                    if etype == "content_block_start":
                        block = event.get("content_block", {})
                        if block.get("type") == "tool_use":
                            current_tool_name = block.get("name", "")
                            current_tool_id = block.get("id", "")
                            current_tool_json = ""
                            if current_tool_name == "ToolSearch":
                                # Don't stream ToolSearch blocks to client
                                continue

                    if etype == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "input_json_delta":
                            current_tool_json += delta.get("partial_json", "")
                            if current_tool_name == "ToolSearch":
                                continue

                    if etype == "content_block_stop" and current_tool_name == "ToolSearch":
                        try:
                            args = json.loads(current_tool_json) if current_tool_json else {}
                        except json.JSONDecodeError:
                            args = {}
                        tool_use_block = {
                            "id": current_tool_id,
                            "name": "ToolSearch",
                            "input": args,
                        }
                        current_tool_name = ""
                        continue

                    if etype == "message_stop" and tool_use_block:
                        # Don't forward message_stop — we'll do a round-trip
                        continue

                    # Forward everything else to client
                    if current_tool_name != "ToolSearch":
                        if not resp.prepared:
                            resp.content_type = "text/event-stream"
                            resp.headers["Cache-Control"] = "no-cache"
                            resp.headers["Connection"] = "keep-alive"
                            await resp.prepare(
                                # aiohttp needs the request object
                                resp._req if hasattr(resp, '_req') else None
                            )
                        await resp.write(f"data: {data_str}\n\n".encode())

    return tool_use_block


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
    tools = body.get("tools", [])

    # Split tools
    core, deferred = split_tools(tools)
    body["tools"] = core

    log.info(
        "Proxy: %d tools -> %d core + %d deferred",
        len(tools), len(core), len(deferred),
    )

    # Replace deferred-tool listings in messages with our actual deferred list
    if deferred:
        body["messages"] = rewrite_messages(body.get("messages", []), deferred)

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
    """Handle streaming request with ToolSearch interception."""
    resp = web.StreamResponse()
    resp.content_type = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Connection"] = "keep-alive"
    # Store request ref for prepare()
    resp._req = request
    await resp.prepare(request)

    tool_use = await _forward_stream(upstream, body, headers, resp)

    if tool_use and tool_use["name"] == "ToolSearch":
        # ToolSearch intercepted — do round-trip
        matched = await _do_tool_search(deferred, tool_use["input"])
        log.info("ToolSearch matched %d tools for query=%r", len(matched), tool_use["input"].get("query"))

        result_content = _format_functions_block(matched)

        # Inject matched tools into the tool list and re-send
        body["tools"] = body["tools"] + matched

        # Add tool_result to messages
        body["messages"] = body.get("messages", []) + [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": tool_use["id"],
                 "name": "ToolSearch", "input": tool_use["input"]},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_use["id"],
                 "content": result_content},
            ]},
        ]

        # Second round-trip — stream directly to client
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{upstream}/v1/messages",
                json=body,
                headers=headers,
            ) as upstream_resp:
                async for chunk in upstream_resp.content.iter_any():
                    await resp.write(chunk)

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

    # Check if response contains ToolSearch call
    stop_reason = result.get("stop_reason", "")
    if stop_reason == "tool_use":
        for block in result.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == "ToolSearch":
                matched = await _do_tool_search(deferred, block.get("input", {}))
                log.info("ToolSearch matched %d tools", len(matched))

                result_text = _format_functions_block(matched)

                # Re-send with matched tools injected
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
