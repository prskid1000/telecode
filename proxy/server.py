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
from proxy import managed_tools as _managed_tools  # noqa: F401  registers tools
from proxy.tool_registry import (
    split_tools, rewrite_messages, strip_all_reminders, proxy_system_instruction,
    lift_tool_result_images as _lift_tool_result_images,
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



def _prepend_text_to_stream(buffered: list[str], text: str) -> list[str]:
    """Prepend `text` to the model's first text_delta in the SSE stream.

    No new blocks, no re-indexing — just injects a text_delta event
    right before the model's first one, carrying the summary text.
    Preserves all indices and ordering so CC's cache stays valid.
    """
    prefix = json.dumps(text + "\n\n")
    output: list[str] = []
    injected = False
    for line in buffered:
        if (
            not injected
            and line.startswith("data: ")
            and '"text_delta"' in line
        ):
            # Parse to get the index, then emit our prefix delta first
            try:
                event = json.loads(line[6:].rstrip("\n"))
                idx = event.get("index", 0)
                output.append(
                    f'data: {{"type":"content_block_delta","index":{idx},'
                    f'"delta":{{"type":"text_delta","text":{prefix}}}}}\n\n'
                )
            except json.JSONDecodeError:
                pass
            injected = True
        output.append(line)
    return output


# ── Location detection ───────────────────────────────────────────────────────

_location_cache: str | None = None


async def _get_location() -> str:
    """Get user's location. Uses settings override if set, otherwise
    auto-detects once via ip-api.com (free, no key, cached for session)."""
    global _location_cache

    # Settings override
    configured = proxy_config.location()
    if configured:
        return configured

    # Return cached result
    if _location_cache is not None:
        return _location_cache

    # Auto-detect via IP geolocation (one-time)
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("http://ip-api.com/json/?fields=city,country") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    city = data.get("city", "")
                    country = data.get("country", "")
                    if city and country:
                        _location_cache = f"{city}, {country}"
                    elif country:
                        _location_cache = country
                    else:
                        _location_cache = ""
                    log.info("Auto-detected location: %s", _location_cache)
                else:
                    _location_cache = ""
    except Exception:
        _location_cache = ""

    return _location_cache


# ── Request handling ─────────────────────────────────────────────────────────

async def _forward_stream(
    upstream: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    resp: web.StreamResponse,
    intercept_names: set[str] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Buffer response from upstream, check for interceptable tool calls.

    Returns (tool_use_block, buffered_lines). NEVER flushes to the client —
    the caller decides when and how to flush (possibly prepending a
    synthetic text block for visibility).
    """
    buffered_lines: list[str] = []
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
                return None, []

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

    return tool_use_block, buffered_lines


async def _flush_sse(
    resp: web.StreamResponse,
    request: web.Request,
    lines: list[str],
) -> None:
    """Flush buffered SSE lines to the client."""
    if proxy_config.debug():
        await _dump_request({"sse_lines": lines, "total_bytes": sum(len(l) for l in lines)}, "RESPONSE")
    if not resp.prepared:
        resp.content_type = "text/event-stream"
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["Connection"] = "keep-alive"
        await resp.prepare(request)
    for line in lines:
        await resp.write(line.encode())


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


def _match_profile(headers) -> dict | None:
    """Return the first client profile whose match condition is satisfied.

    Match spec: {"header": "<Name>", "contains": "<substring>"} (case-insensitive).
    """
    for profile in proxy_config.client_profiles():
        match = profile.get("match", {})
        hdr = match.get("header", "")
        needle = match.get("contains", "")
        if not hdr or not needle:
            continue
        value = headers.get(hdr, "") or ""
        if needle.lower() in value.lower():
            return profile
    return None


async def handle_messages(request: web.Request) -> web.StreamResponse:
    """Main proxy endpoint: POST /v1/messages"""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    await _dump_request(body, "INCOMING")

    # Match request against configured client profiles (first match wins).
    profile = _match_profile(request.headers)
    if profile:
        log.info("Proxy: matched client profile %r", profile.get("name", "?"))

    # Apply model mapping (e.g. claude-opus-4-6 -> qwen3.5-35b-a3b)
    mapping = proxy_config.model_mapping()
    if mapping:
        requested_model = body.get("model", "")
        if requested_model in mapping:
            body["model"] = mapping[requested_model]
            log.info("Proxy: mapped model %s -> %s", requested_model, body["model"])

    upstream = proxy_config.upstream_url()
    deferred: list[dict[str, Any]] = []

    # Profile settings (fall back to global proxy settings)
    use_tool_splitting = profile.get("tool_splitting", proxy_config.tool_splitting()) if profile else proxy_config.tool_splitting()
    use_intercept = profile.get("intercept", True) if profile else True
    inject_date_loc = profile.get("inject_date_location", True) if profile else True
    system_md = profile.get("system_instruction") if profile else None

    # Inject profile-specific system instruction (prepended to client's system)
    if system_md:
        instruction = proxy_system_instruction(system_md)
        if instruction:
            system = body.get("system", "")
            if isinstance(system, str):
                body["system"] = f"{instruction}\n\n{system}" if system else instruction
            elif isinstance(system, list):
                system.insert(0, {"type": "text", "text": instruction})

    # Inject current date + location as a system-reminder (unless disabled)
    if inject_date_loc:
        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d (%A)")
        location = await _get_location()
        parts = [f"Current date: {date_str}."]
        if location:
            parts.append(f"User location: {location}.")
        context = "<system-reminder>\n" + " ".join(parts) + "\n</system-reminder>"
        system = body.get("system", "")
        if isinstance(system, str):
            body["system"] = f"{system}\n\n{context}" if system else context
        elif isinstance(system, list):
            system.append({"type": "text", "text": context})

    if use_tool_splitting:
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

    if proxy_config.lift_tool_result_images():
        body["messages"] = _lift_tool_result_images(body.get("messages", []))

    await _dump_request(body, "OUTGOING")

    # Forward auth headers
    headers = {}
    for h in ("x-api-key", "anthropic-version", "authorization", "content-type"):
        if h in request.headers:
            headers[h] = request.headers[h]
    headers.setdefault("content-type", "application/json")

    if body.get("stream", False):
        return await _handle_streaming(upstream, body, headers, deferred, request, skip_intercept=not use_intercept)
    else:
        return await _handle_non_streaming(upstream, body, headers, deferred, skip_intercept=not use_intercept)


async def _handle_streaming(
    upstream: str,
    body: dict[str, Any],
    headers: dict[str, str],
    deferred: list[dict[str, Any]],
    request: web.Request,
    skip_intercept: bool = False,
) -> web.StreamResponse:
    """Handle streaming request with managed tools, ToolSearch, and auto-load interception."""
    from proxy.managed_tools import is_managed, get_handler, get_tool, format_visibility, run_pre_llm, run_post_llm

    # Build set of tool names to intercept (Office add-ins skip all intercept)
    intercept: set[str] | None = None
    deferred_names = {t["name"] for t in deferred}
    if not skip_intercept:
        if deferred:
            intercept = {"ToolSearch"}
            if proxy_config.auto_load_tools():
                intercept |= deferred_names

        # Add all managed tools (WebSearch, speak, transcribe, etc.) to intercept set
        from proxy.managed_tools import _REGISTRY
        managed_names = set(_REGISTRY.keys())
        if managed_names:
            intercept = (intercept or set()) | managed_names

    resp = web.StreamResponse()
    resp._req = request

    # ── Intercept loop ───────────────────────────────────────────
    # The model may call intercepted tools across multiple round-trips
    # (e.g. WebSearch → reads result → WebSearch again → ToolSearch → text).
    # We loop: each iteration calls upstream, checks for intercepted tools,
    # handles them, appends messages, and retries. When the model finally
    # produces a response with NO intercepted tools, we flush it to CC
    # (optionally prepending visibility summaries).
    summaries: list[str] = []
    max_roundtrips = 15

    for _rt in range(max_roundtrips):
        tool_use, buffered = await _forward_stream(
            upstream, body, headers, resp, intercept,
        )

        if not tool_use:
            # Clean response — no more intercepted tools.
            # Prepend summaries (if any) then flush to CC.
            if summaries:
                buffered = _prepend_text_to_stream(
                    buffered, "\n".join(summaries),
                )
            await _flush_sse(resp, request, buffered)
            break

        # Handle the intercepted tool
        tool_name = tool_use["name"]
        matched: list[dict[str, Any]] = []
        result_content: str | None = None

        if tool_name == "ToolSearch":
            matched = await _do_tool_search(deferred, tool_use["input"])
            log.info("ToolSearch matched %d tools for query=%r", len(matched), tool_use["input"].get("query"))
            result_content = _format_functions_block(matched)

        elif is_managed(tool_name):
            tool_entry = get_tool(tool_name)
            handler = tool_entry.handler if tool_entry else None
            if handler and tool_entry:
                try:
                    # pre_llm: model input → LLM → enrich args
                    enriched = await run_pre_llm(tool_entry, tool_use["input"])
                    # handler: enriched args → (summary, result)
                    summary, result_content = await handler(enriched)
                    # post_llm: result → LLM → processed result
                    result_content = await run_post_llm(tool_entry, result_content)
                    log.info("Managed tool %s: %s", tool_name, summary)
                except Exception as exc:
                    log.warning("Managed tool %s failed: %s", tool_name, exc)
                    summary = f"Failed: {exc}"
                    result_content = f"ERROR: {tool_name} failed: {exc}"
                summaries.append(format_visibility(tool_name, tool_use["input"], summary))

        elif proxy_config.auto_load_tools() and tool_name in deferred_names:
            matched = [t for t in deferred if t["name"] == tool_name]
            log.info("Auto-loading schema for deferred tool: %s", tool_name)
            result_content = (
                f"This tool's schema was not loaded. Here is the schema:\n\n"
                f"{_format_functions_block(matched)}\n\n"
                f"Call the tool again with the correct parameter names from the schema above."
            )

        if not result_content:
            # Unhandled tool — flush as-is and break
            await _flush_sse(resp, request, buffered)
            break

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
        # Continue loop — next iteration calls upstream again

    if not resp.prepared:
        await resp.prepare(request)
    await resp.write_eof()
    return resp


async def _handle_non_streaming(
    upstream: str,
    body: dict[str, Any],
    headers: dict[str, str],
    deferred: list[dict[str, Any]],
    skip_intercept: bool = False,
) -> web.Response:
    """Handle non-streaming request with the same intercept loop as streaming."""
    from proxy.managed_tools import get_handler, get_tool, format_visibility, run_pre_llm, run_post_llm
    from proxy.managed_tools import _REGISTRY

    deferred_names = set() if skip_intercept else {t["name"] for t in deferred}
    managed_names = set() if skip_intercept else set(_REGISTRY.keys())
    summaries: list[str] = []

    max_roundtrips = 15
    for _rt in range(max_roundtrips):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{upstream}/v1/messages",
                json=body,
                headers=headers,
            ) as upstream_resp:
                result = await upstream_resp.json()

        if result.get("stop_reason") != "tool_use":
            # Prepend summaries into the model's first text block (no new blocks)
            if summaries:
                prefix = "\n".join(summaries) + "\n\n"
                for block in result.get("content", []):
                    if block.get("type") == "text":
                        block["text"] = prefix + block.get("text", "")
                        break
            return web.json_response(result, status=200)

        handled = False
        for block in result.get("content", []):
            if block.get("type") != "tool_use":
                continue
            tool_name = block.get("name", "")
            matched: list[dict[str, Any]] = []
            result_text: str | None = None

            if tool_name == "ToolSearch":
                matched = await _do_tool_search(deferred, block.get("input", {}))
                log.info("ToolSearch matched %d tools", len(matched))
                result_text = _format_functions_block(matched)

            elif tool_name in managed_names:
                tool_entry = get_tool(tool_name)
                handler = tool_entry.handler if tool_entry else None
                if handler and tool_entry:
                    try:
                        enriched = await run_pre_llm(tool_entry, block.get("input", {}))
                        summary, result_text = await handler(enriched)
                        result_text = await run_post_llm(tool_entry, result_text)
                        log.info("Managed tool %s (non-streaming): %s", tool_name, summary)
                    except Exception as exc:
                        log.warning("Managed tool %s failed: %s", tool_name, exc)
                        summary = f"Failed: {exc}"
                        result_text = f"ERROR: {tool_name} failed: {exc}"
                    summaries.append(format_visibility(tool_name, block.get("input", {}), summary))

            elif proxy_config.auto_load_tools() and tool_name in deferred_names:
                matched = [t for t in deferred if t["name"] == tool_name]
                log.info("Auto-loading schema for deferred tool: %s", tool_name)
                result_text = (
                    f"This tool's schema was not loaded. Here is the schema:\n\n"
                    f"{_format_functions_block(matched)}\n\n"
                    f"Call the tool again with the correct parameter names."
                )

            if not result_text:
                continue

            if matched:
                body["tools"] = body["tools"] + matched
            body["messages"] = body.get("messages", []) + [
                {"role": "assistant", "content": result.get("content", [])},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": block["id"],
                     "content": result_text},
                ]},
            ]
            handled = True
            break

        if not handled:
            return web.json_response(result, status=200)

    return web.json_response(result, status=200)


# ── /v1/models — convert OpenAI format from LM Studio to Anthropic format ──

def _openai_models_to_anthropic(openai_data: dict) -> dict:
    """Convert OpenAI /v1/models response to Anthropic format.

    Prepends any client-facing aliases from proxy.model_mapping so clients
    (e.g. Office add-ins) see familiar Claude model names.
    """
    from datetime import datetime, timezone

    models = []

    # Prepend mapped aliases (claude-opus-4-6, etc.) so they appear first
    mapping = proxy_config.model_mapping()
    for alias, real in mapping.items():
        display = alias.replace("-", " ").replace("_", " ").title()
        models.append({
            "id": alias,
            "type": "model",
            "display_name": display,
            "created_at": "2024-01-01T00:00:00Z",
        })

    for m in openai_data.get("data", []):
        created_ts = m.get("created", 0)
        try:
            created_at = datetime.fromtimestamp(created_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OSError, ValueError):
            created_at = "2024-01-01T00:00:00Z"

        model_id = m.get("id", "unknown")
        display = model_id.replace("-", " ").replace("_", " ").title()
        models.append({
            "id": model_id,
            "type": "model",
            "display_name": display,
            "created_at": created_at,
        })

    return {
        "data": models,
        "has_more": False,
        "first_id": models[0]["id"] if models else "",
        "last_id": models[-1]["id"] if models else "",
    }


async def handle_models(request: web.Request) -> web.Response:
    """GET /v1/models — fetch from LM Studio (OpenAI format), return Anthropic format."""
    upstream = proxy_config.upstream_url()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "transfer-encoding")}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{upstream}/v1/models", headers=headers) as resp:
                data = await resp.json()
                return web.json_response(_openai_models_to_anthropic(data))
    except Exception as exc:
        log.warning("Failed to fetch models from upstream: %s", exc)
        return web.json_response({"data": [], "has_more": False, "first_id": "", "last_id": ""})


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

@web.middleware
async def cors_middleware(request: web.Request, handler):
    """Add CORS headers to all responses when proxy.cors_origins is set."""
    origins = proxy_config.cors_origins()
    origin = request.headers.get("Origin", "")
    allowed = origins and ("*" in origins or origin in origins)

    # All OPTIONS requests get a direct response (never forward to handler)
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
        if allowed:
            resp.headers["Access-Control-Allow-Origin"] = origin or "*"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = request.headers.get(
                "Access-Control-Request-Headers", "*"
            )
            resp.headers["Access-Control-Allow-Private-Network"] = "true"
            resp.headers["Access-Control-Max-Age"] = "86400"
        return resp

    resp = await handler(request)
    if allowed:
        resp.headers["Access-Control-Allow-Origin"] = origin or "*"
        resp.headers["Access-Control-Allow-Private-Network"] = "true"
    return resp


def create_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_post("/v1/messages", handle_messages)
    app.router.add_get("/v1/models", handle_models)
    # Passthrough everything else (health, etc.)
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
    host = proxy_config.proxy_host()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("Tool-search proxy listening on http://%s:%d -> %s", host, port, proxy_config.upstream_url())
    return runner
