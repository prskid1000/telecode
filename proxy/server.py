"""
Anthropic-compatible streaming proxy with ToolSearch interception.

Sits between Claude Code and LM Studio (or any OpenAI-compatible backend).
Intercepts tool lists, defers non-core tools, and handles ToolSearch round-trips.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
from aiohttp import web

# How often to send `: keepalive` SSE comments to the client while waiting
# on upstream. Resets the client's HTTP read timeout. SSE comment lines are
# ignored by parsers — purely a wire-level keep-alive.
_HEARTBEAT_INTERVAL = 2.0

from proxy import config as proxy_config
from proxy import managed_tools as _managed_tools  # noqa: F401  registers tools
from proxy.tool_registry import (
    split_tools, rewrite_messages, strip_all_reminders, proxy_system_instruction,
    lift_tool_result_images as _lift_tool_result_images,
)
from proxy.tool_search import BM25Index


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



def _status_block_lines(text: str, index: int) -> list[str]:
    """Build SSE lines for one synthetic text content block carrying status.

    Renders in CC as a normal text block with the `●` / `└` formatted text.
    Always rendered, even when the model goes straight to tool_use with no
    text of its own — so visibility never gets dropped.
    """
    start = {
        "type": "content_block_start",
        "index": index,
        "content_block": {"type": "text", "text": ""},
    }
    delta = {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "text_delta", "text": text + "\n\n"},
    }
    stop = {"type": "content_block_stop", "index": index}
    return [
        "event: content_block_start\n",
        f"data: {json.dumps(start)}\n\n",
        "event: content_block_delta\n",
        f"data: {json.dumps(delta)}\n\n",
        "event: content_block_stop\n",
        f"data: {json.dumps(stop)}\n\n",
    ]


def _shift_event_index(line: str, offset: int) -> str:
    """Bump the `index` field of a content_block_* event by `offset`.

    Used when synthetic status blocks have been emitted at indices [0..N-1]:
    every upstream content block index N must shift to N+offset on the wire
    so the wire stream stays self-consistent.
    """
    if offset == 0 or not line.startswith("data: "):
        return line
    try:
        event = json.loads(line[6:].rstrip("\n"))
    except json.JSONDecodeError:
        return line
    if event.get("type", "").startswith("content_block_") and "index" in event:
        event["index"] = event["index"] + offset
        return f"data: {json.dumps(event)}\n\n"
    return line


async def _ensure_prepared(resp: web.StreamResponse, request: web.Request) -> None:
    """Idempotent prepare(): set SSE headers + CORS once."""
    if resp.prepared:
        return
    resp.content_type = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Connection"] = "keep-alive"
    _apply_cors_to_stream(resp, request)
    await resp.prepare(request)


async def _start_heartbeat(
    resp: web.StreamResponse,
    request: web.Request,
    write_lock: asyncio.Lock,
) -> asyncio.Task:
    """Spawn a heartbeat task that runs for the entire request lifetime.

    Two cadences:
      - `: keepalive\\n\\n` SSE comment every `_HEARTBEAT_INTERVAL` seconds
        (default 2s). Wire-level keep-alive — clients ignore comment lines
        but their HTTP read timer resets on byte activity.
      - `event: ping\\n\\ndata: {"type":"ping"}\\n\\n` every
        `proxy.ping_interval` seconds (default 20s). Anthropic's official
        live-progress signal — CC / pivot / Office add-ins recognize it
        and won't time out even on long generations.

    All writes go through `write_lock` to serialize with the main loop's
    chunk writes during passthrough mode.
    """
    await _ensure_prepared(resp, request)
    ping_every = max(_HEARTBEAT_INTERVAL, proxy_config.ping_interval())
    _PING_LINE = b"event: ping\ndata: {\"type\":\"ping\"}\n\n"

    async def _beat() -> None:
        elapsed = 0.0
        last_ping = 0.0
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
                elapsed += _HEARTBEAT_INTERVAL
                async with write_lock:
                    try:
                        if elapsed - last_ping >= ping_every:
                            await resp.write(_PING_LINE)
                            last_ping = elapsed
                        else:
                            await resp.write(b": keepalive\n\n")
                    except (ConnectionResetError, ConnectionError):
                        return
        except asyncio.CancelledError:
            return

    return asyncio.create_task(_beat())


async def _stop_heartbeat(task: asyncio.Task | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


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
    request: web.Request,
    intercept_names: set[str] | None = None,
    *,
    known_names: set[str] | None = None,
    status_blocks: list[str] | None = None,
    base_index_offset: int = 0,
) -> dict[str, Any] | None:
    """Stream from upstream with early branch on first content_block_start.

    Returns:
      dict — response began with a tool_use whose name is in `intercept_names`.
             Nothing was written to the client; rest of upstream is consumed
             only enough to capture the full tool_use input. Caller handles
             the tool, optionally accumulates a status string, and re-calls.
      None — response is final (text, or non-intercepted tool_use). Has been
             fully streamed to the client. Status blocks (if any) were emitted
             as synthetic text blocks at indices [0..N-1] and upstream block
             indices were shifted by N + base_index_offset.

    A heartbeat task emits `: keepalive\\n\\n` every ~2s during the buffer
    window so the client's HTTP read timeout doesn't fire while we wait.
    """
    intercept_names = intercept_names or set()
    # Hallucination guard: when `known_names` is provided, any tool_use whose
    # name is neither in `intercept_names` nor in `known_names` is treated as
    # intercepted (returned to caller with _hallucinated=True) so the proxy
    # can return BM25 suggestions instead of letting the bogus call leak to CC.
    known_names = known_names or set()
    status_blocks = status_blocks or []
    n_status = len(status_blocks)
    index_offset = base_index_offset + n_status

    # write_lock may be provided by the caller (preferred — lets heartbeat
    # stay alive across _forward_stream calls). Fallback for standalone use.
    write_lock = getattr(resp, "_write_lock", None)
    own_heartbeat = write_lock is None
    if write_lock is None:
        write_lock = asyncio.Lock()

    # Buffered events held during the decision window (up to first
    # content_block_start). On passthrough we flush these; on intercept we
    # keep accumulating to capture the full tool_use input then discard.
    buffered: list[str] = []
    decided: str | None = None  # None | "intercept" | "passthrough"
    heartbeat: asyncio.Task | None = None

    # Tool tracking (only the FIRST tool_use in a response can be intercepted)
    cur_name = ""
    cur_id = ""
    cur_json = ""
    tool_use: dict[str, Any] | None = None

    async def _flush_buffered_with_status() -> None:
        """Called when we decide PASSTHROUGH: emit status blocks (at indices
        starting from base_index_offset), then flush buffered events with
        their indices shifted by index_offset, then continue streaming live.
        """
        await _ensure_prepared(resp, request)
        async with write_lock:
            for i, text in enumerate(status_blocks):
                for line in _status_block_lines(text, base_index_offset + i):
                    await resp.write(line.encode())
            for line in buffered:
                await resp.write(_shift_event_index(line, index_offset).encode())
        buffered.clear()

    try:
        if own_heartbeat:
            heartbeat = await _start_heartbeat(resp, request, write_lock)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{upstream}/v1/messages",
                json=payload,
                headers=headers,
            ) as upstream_resp:
                if upstream_resp.status != 200:
                    body = await upstream_resp.text()
                    await _stop_heartbeat(heartbeat)
                    heartbeat = None
                    # We already prepared with 200 — surface the error as an
                    # SSE error event so CC sees it.
                    err = {"type": "error", "error": {"type": "upstream_error",
                                                       "status": upstream_resp.status,
                                                       "body": body[:500]}}
                    async with write_lock:
                        await resp.write(b"event: error\n")
                        await resp.write(f"data: {json.dumps(err)}\n\n".encode())
                    return None

                buf = ""
                async for chunk in upstream_resp.content.iter_any():
                    text = chunk.decode("utf-8", errors="replace")
                    buf += text

                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.rstrip("\r")

                        # Reconstruct canonical SSE form
                        if not line.startswith("data: "):
                            sse_line = f"{line}\n"
                        else:
                            data_str = line[6:]
                            sse_line = f"data: {data_str}\n\n"

                        # Parse event for state-machine decisions
                        event: dict[str, Any] | None = None
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str.strip() != "[DONE]":
                                try:
                                    event = json.loads(data_str)
                                except json.JSONDecodeError:
                                    event = None

                        # Forward the FIRST message_start to the client
                        # immediately, regardless of intercept/passthrough.
                        # Clients (CC, pivot) buffer their SSE parser until they
                        # see message_start — without this they hold back any
                        # status blocks we emit between rounds and flush them
                        # together with the next round's text. Subsequent
                        # rounds' message_start events are dropped (one
                        # request = one message).
                        etype = event.get("type") if event else ""
                        if etype == "message_start":
                            if not getattr(resp, "_message_start_sent", False):
                                resp._message_start_sent = True
                                # Synthesize the SSE event header too (clients
                                # may rely on it; original header was appended
                                # to `buffered` on the preceding line but would
                                # be discarded with the rest of an intercept
                                # round's buffer).
                                async with write_lock:
                                    await resp.write(b"event: message_start\n")
                                    await resp.write(sse_line.encode())
                                    writer = getattr(resp, "_payload_writer", None)
                                    if writer is not None:
                                        try:
                                            await writer.drain()
                                        except (ConnectionResetError, ConnectionError):
                                            pass
                            # Remove any `event: message_start` header we may
                            # have already appended to buffered on the previous
                            # line (passthrough rounds mustn't send a duplicate).
                            if buffered and buffered[-1].strip() == "event: message_start":
                                buffered.pop()
                            continue

                        # Drop message_stop on intercept rounds (we're looping;
                        # client must see exactly one message_stop at the end).
                        if (decided == "intercept" and etype in ("message_delta", "message_stop")):
                            continue

                        # First content_block_start = decision point
                        if (decided is None and event
                                and event.get("type") == "content_block_start"):
                            block = event.get("content_block", {})
                            btype = block.get("type", "")
                            if btype == "tool_use":
                                cur_name = block.get("name", "")
                                cur_id = block.get("id", "")
                                cur_json = ""
                                if cur_name in intercept_names:
                                    decided = "intercept"
                                elif known_names and cur_name not in known_names:
                                    # Hallucinated — unknown tool name.
                                    # Intercept so caller returns BM25 suggestions.
                                    decided = "intercept"
                                else:
                                    decided = "passthrough"
                            else:
                                # text or other → passthrough
                                decided = "passthrough"

                            if decided == "passthrough":
                                buffered.append(sse_line)
                                await _flush_buffered_with_status()
                                continue
                            # else INTERCEPT: fall through, keep buffering
                            # Heartbeat keeps running through both modes — pings
                            # are emitted every `proxy.ping_interval` seconds even
                            # during long upstream silence mid-passthrough.

                        # Accumulate tool_use input json (intercept path only —
                        # in passthrough it's already streamed to client below)
                        if (decided == "intercept" and event
                                and event.get("type") == "content_block_delta"):
                            delta = event.get("delta", {})
                            if delta.get("type") == "input_json_delta":
                                cur_json += delta.get("partial_json", "")

                        if (decided == "intercept" and event
                                and event.get("type") == "content_block_stop"
                                and tool_use is None):
                            try:
                                args = json.loads(cur_json) if cur_json else {}
                            except json.JSONDecodeError:
                                args = {}
                            tool_use = {"id": cur_id, "name": cur_name, "input": args}
                            # Tool input fully captured; we can stop early.
                            # Continue draining the stream cleanly so the
                            # connection closes (some servers don't like aborts).

                        if decided == "passthrough":
                            # Live streaming: shift index and write immediately
                            # under the lock so heartbeat pings don't interleave
                            # mid-event.
                            shifted = _shift_event_index(sse_line, index_offset).encode()
                            async with write_lock:
                                await resp.write(shifted)
                        else:
                            # INTERCEPT or pre-decision → buffer
                            buffered.append(sse_line)

        return tool_use
    finally:
        await _stop_heartbeat(heartbeat)


def _canonicalize_body(body: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the upstream body with a cache-friendly key order and
    normalized message content.

    Why: LM Studio's prefix cache keys on the exact serialized token stream.
    CC puts `messages` near the start of the JSON body, so a new user turn
    breaks the prefix after ~100 chars. By placing stable fields (system,
    tools) first, the cache hits cover the entire system prompt and tool
    definitions — only the growing messages tail needs re-processing.

    Also normalizes message content: CC sometimes emits `content: "text"`
    (plain string), sometimes `content: [{"type":"text","text":"..."}]`.
    Each flip between forms costs a cache miss. We always use list form.
    """
    # 1) Normalize message content
    msgs = body.get("messages", [])
    normalized: list[dict[str, Any]] = []
    for m in msgs:
        if not isinstance(m, dict):
            normalized.append(m)
            continue
        content = m.get("content")
        if isinstance(content, str):
            new = dict(m)
            new["content"] = [{"type": "text", "text": content}]
            normalized.append(new)
        else:
            normalized.append(m)

    # 2) Rebuild body with stable-first key order
    preferred_order = ("model", "system", "tools", "max_tokens", "temperature",
                       "top_p", "top_k", "stop_sequences", "stream", "metadata",
                       "tool_choice", "messages")
    out: dict[str, Any] = {}
    for key in preferred_order:
        if key == "messages":
            out[key] = normalized
        elif key in body:
            out[key] = body[key]
    # Preserve any fields we didn't anticipate, placed AFTER messages (harmless)
    for key, val in body.items():
        if key not in out:
            out[key] = val
    return out


def _apply_cors_to_stream(resp: web.StreamResponse, request: web.Request) -> None:
    """Set CORS headers on a StreamResponse before prepare() — middleware can't
    reach headers after prepare() has committed them."""
    origins = proxy_config.cors_origins()
    if not origins:
        return
    origin = request.headers.get("Origin", "")
    allowed = "*" in origins or origin in origins
    if allowed:
        resp.headers["Access-Control-Allow-Origin"] = origin or "*"
        resp.headers["Access-Control-Allow-Private-Network"] = "true"


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
        _apply_cors_to_stream(resp, request)
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


async def _dump_request(body: dict[str, Any], label: str, meta: dict[str, Any] | None = None) -> None:
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
        payload: dict[str, Any] = {"label": label, "body": body}
        if meta:
            payload["meta"] = meta
        await f.write(json.dumps(payload, indent=2, ensure_ascii=False))

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

    # Dump the raw incoming body plus minimal client metadata for cache debugging.
    # (Do NOT mutate the real request body with debug-only fields.)
    await _dump_request(
        body,
        "INCOMING",
        meta={
            "user_agent": (request.headers.get("User-Agent", "") or "")[:200],
            "referer": (request.headers.get("Referer", "") or "")[:200],
        },
    )

    # Match request against configured client profiles (first match wins).
    profile = _match_profile(request.headers)

    # Apply model mapping (e.g. claude-opus-4-6 -> qwen3.5-35b-a3b)
    mapping = proxy_config.model_mapping()
    requested_model = body.get("model", "")
    if mapping:
        if requested_model in mapping:
            body["model"] = mapping[requested_model]

    # Profile-driven tool filtering:
    #  - strip_tool_names: drop tools whose name matches any entry (hosted or custom)
    #  - strip_cache_control: remove `cache_control` key from each tool (LM Studio rejects it)
    strip_names = set(profile.get("strip_tool_names", [])) if profile else set()
    strip_cc = profile.get("strip_cache_control", True) if profile else True

    tools = body.get("tools", [])
    if tools and (strip_names or strip_cc):
        filtered = []
        for t in tools:
            name = t.get("name", "")
            if name in strip_names:
                continue
            if strip_cc:
                t = {k: v for k, v in t.items() if k != "cache_control"}
            filtered.append(t)
        body["tools"] = filtered

    upstream = proxy_config.upstream_url()
    deferred: list[dict[str, Any]] = []

    # Profile settings (fall back to global proxy settings) — every feature independently togglable
    def _pget(key: str, default):
        if profile and key in profile:
            return profile[key]
        return default

    use_tool_search = _pget("tool_search", proxy_config.tool_search())
    inject_date_loc = _pget("inject_date_location", True)
    use_strip_reminders = _pget("strip_reminders", proxy_config.strip_reminders())
    use_lift_images = _pget("lift_tool_result_images", proxy_config.lift_tool_result_images())
    use_auto_load = _pget("auto_load_tools", proxy_config.auto_load_tools())
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

    # Resolve managed tool injection (works with or without tool_search)
    from proxy.managed_tools import _REGISTRY as _MANAGED_REG
    inject_managed: list[str] = (
        profile.get("inject_managed") if profile and "inject_managed" in profile
        else list(_MANAGED_REG.keys())
    ) or []

    managed_strip_names: set[str] = set()
    inject_schemas: list[dict[str, Any]] = []
    for mname in inject_managed:
        mt = _MANAGED_REG.get(mname)
        if not mt:
            continue
        managed_strip_names.add(mt.name)
        managed_strip_names.update(mt.strip_from_cc)
        inject_schemas.append(mt.schema)

    if use_tool_search:
        _core_list = profile.get("core_tools") if profile and "core_tools" in profile else proxy_config.core_tools()
        core_names: set[str] = set(_core_list or [])
        extra_strip = {"ToolSearch"} | managed_strip_names

        tools = body.get("tools", [])
        core, deferred = split_tools(tools, core_names, extra_strip, inject_schemas)
        body["tools"] = core

        # Inject deferred tools instruction into system, tool names into messages
        if deferred:
            body["messages"] = rewrite_messages(body.get("messages", []), deferred)

    elif inject_schemas or managed_strip_names:
        # Managed-tool injection without splitting (Office profile path)
        tools = body.get("tools", [])
        kept = [t for t in tools if t.get("name", "") not in managed_strip_names]
        body["tools"] = inject_schemas + kept

    if use_strip_reminders:
        body["messages"] = strip_all_reminders(body.get("messages", []))

    if use_lift_images:
        body["messages"] = _lift_tool_result_images(body.get("messages", []))

    # Forward auth headers
    headers = {}
    for h in ("x-api-key", "anthropic-version", "authorization", "content-type"):
        if h in request.headers:
            headers[h] = request.headers[h]
    headers.setdefault("content-type", "application/json")

    # Which managed tools to intercept for THIS request = whatever the profile injected.
    # (Injecting without intercepting is broken; intercepting without injecting is a no-op.)
    managed_intercept: set[str] = {mt.name for mt in (_MANAGED_REG.get(n) for n in inject_managed) if mt}

    # Prefix-cache optimization for LM Studio:
    #  1. Reorder keys so stable fields (system, tools) come before the growing
    #     `messages` list — keeps the serialized prefix identical across turns.
    #  2. Normalize message content to list-of-blocks form so CC's string↔list
    #     flip-flopping doesn't bust cache.
    body = _canonicalize_body(body)

    await _dump_request(
        body,
        "OUTGOING",
        meta={
            "profile": (profile.get("name") if profile else None) or "-",
            "model_before": requested_model,
            "model_after": body.get("model", "") or "",
            "user_agent": (request.headers.get("User-Agent", "") or "")[:200],
            "referer": (request.headers.get("Referer", "") or "")[:200],
        },
    )

    if body.get("stream", False):
        return await _handle_streaming(upstream, body, headers, deferred, request,
                                       managed_intercept=managed_intercept,
                                       auto_load=use_auto_load)
    else:
        return await _handle_non_streaming(upstream, body, headers, deferred,
                                           managed_intercept=managed_intercept,
                                           auto_load=use_auto_load)


async def _handle_streaming(
    upstream: str,
    body: dict[str, Any],
    headers: dict[str, str],
    deferred: list[dict[str, Any]],
    request: web.Request,
    managed_intercept: set[str] | None = None,
    auto_load: bool = False,
) -> web.StreamResponse:
    """Handle streaming request. Two independent intercept behaviors:

    - ToolSearch + deferred tools: always intercepted when `deferred` is non-empty
      (tool_search produced them — ToolSearch is how the model loads them).
    - Managed tools (WebSearch, code_execution, speak, ...): intercepted when the
      request's profile injected them (`managed_intercept` names).
    """
    from proxy.managed_tools import is_managed, get_handler, get_tool, format_visibility, run_pre_llm, run_post_llm

    intercept: set[str] = set()
    deferred_names = {t["name"] for t in deferred}
    if deferred:
        # ToolSearch is bundled with tool_search — always intercepted when deferred exist
        intercept.add("ToolSearch")
        if auto_load:
            intercept |= deferred_names
        else:
            # Unloaded-tool guard: also intercept blind deferred calls
            intercept |= deferred_names
    if managed_intercept:
        intercept |= managed_intercept

    resp = web.StreamResponse()
    resp._req = request
    # Request-scoped write lock + heartbeat so pings keep flowing across
    # intercept round-trips AND during local tool-handler execution (e.g.
    # code_execution can take 30s — without this, no bytes would hit the
    # client for that whole window).
    write_lock: asyncio.Lock = asyncio.Lock()
    resp._write_lock = write_lock
    heartbeat: asyncio.Task | None = None

    # ── Intercept loop ───────────────────────────────────────────
    # Each iteration calls upstream. _forward_stream branches on the first
    # content_block_start: if intercepted tool_use → returns the tool_use dict
    # (nothing written to client yet); otherwise streams the response live
    # (with upstream indices shifted past any already-emitted status blocks).
    max_roundtrips = proxy_config.max_roundtrips()
    # Names currently visible to the model as core tools (for hallucination guard)
    core_visible_names: set[str] = {t.get("name", "") for t in body.get("tools", [])}
    # Status blocks already written to the wire. The NEXT _forward_stream call
    # must shift upstream block indices by this count so the wire stays
    # self-consistent.
    status_emitted = 0

    async def _emit_live_status(text: str) -> None:
        """Write a status block to the wire immediately, under the request's
        write lock (heartbeat may be writing concurrently). Drains the
        underlying HTTP writer so the client sees the block now, not
        buffered with the next round's bytes."""
        nonlocal status_emitted
        await _ensure_prepared(resp, request)
        async with write_lock:
            for line in _status_block_lines(text, status_emitted):
                await resp.write(line.encode())
            # Force the payload writer to push buffered bytes to the
            # transport. Without this, small writes can sit in the
            # aiohttp chunked-encoding buffer until the next write comes.
            writer = getattr(resp, "_payload_writer", None)
            if writer is not None:
                try:
                    await writer.drain()
                except (ConnectionResetError, ConnectionError):
                    pass
        status_emitted += 1

    try:
        heartbeat = await _start_heartbeat(resp, request, write_lock)

        for _rt in range(max_roundtrips):
            round_intercept = set(intercept)
            # Hallucination guard: everything the model is allowed to call
            # without interception. Anything outside this set AND outside
            # `intercept_names` is treated as a made-up name and caught.
            known_names = (
                core_visible_names
                | deferred_names
                | round_intercept
            )

            tool_use = await _forward_stream(
                upstream, body, headers, resp, request,
                intercept_names=round_intercept,
                known_names=known_names,
                status_blocks=[],  # status now emitted live, not batched
                base_index_offset=status_emitted,
            )

            if tool_use is None:
                # Final response fully streamed.
                break

            # Handle the intercepted tool — build status_line + tool_result content
            tool_name = tool_use["name"]
            tool_input = tool_use.get("input") or {}
            matched: list[dict[str, Any]] = []
            result_content: str | None = None
            status_line: str | None = None

            if tool_name == "ToolSearch":
                matched = await _do_tool_search(deferred, tool_input)
                result_content = _format_functions_block(matched)
                q = str(tool_input.get("query", ""))
                if matched:
                    names = ", ".join(m.get("name", "") for m in matched[:5])
                    status_line = f'● ToolSearch("{q[:80]}")\n└  {len(matched)} schemas loaded: {names}'
                else:
                    status_line = f'● ToolSearch("{q[:80]}")\n└  No matches'

            elif is_managed(tool_name):
                tool_entry = get_tool(tool_name)
                handler = tool_entry.handler if tool_entry else None
                if handler and tool_entry:
                    try:
                        enriched = await run_pre_llm(tool_entry, tool_input)
                        summary, result_content = await handler(enriched)
                        result_content = await run_post_llm(tool_entry, result_content)
                    except Exception as exc:
                        summary = f"Failed: {exc}"
                        result_content = f"ERROR: {tool_name} failed: {exc}"
                    status_line = format_visibility(tool_name, tool_input, summary)

            elif auto_load and tool_name in deferred_names and tool_name not in core_visible_names:
                matched = [t for t in deferred if t["name"] == tool_name]
                result_content = (
                    f"The schema for `{tool_name}` has now been loaded:\n\n"
                    f"{_format_functions_block(matched)}\n\n"
                    f"Call the tool again using the parameter names from this schema."
                )
                status_line = f'● Loaded {tool_name}\n└  Schema delivered · awaiting retry'

            elif (not auto_load) and tool_name in deferred_names and tool_name not in core_visible_names:
                result_content = (
                    f"`{tool_name}` is currently UNLOADED in this conversation.\n\n"
                    f"Call `ToolSearch(query=\"select:{tool_name}\", max_results=5)` to load its schema, "
                    f"then call `{tool_name}` again using the parameter names from that schema."
                )
                status_line = (
                    f'● Blocked: {tool_name} (unloaded)\n'
                    f'└  Model instructed to ToolSearch first'
                )

            else:
                # Hallucination guard: unknown tool name (not core, not deferred,
                # not managed, not ToolSearch). Run BM25 over everything known
                # with the bogus name as query and return the top matches as
                # suggestions — no schemas injected (that would bloat context).
                # Auto-load / ToolSearch handle the next call for the real name.
                haystack = list(body.get("tools", [])) + deferred
                search_matches = await _do_tool_search(
                    haystack, {"query": tool_name, "max_results": 5}
                )
                if search_matches:
                    suggestion_names = ", ".join(
                        m.get("name", "") for m in search_matches[:5]
                    )
                    result_content = (
                        f"The tool `{tool_name}` does not exist. Did you mean one of these?\n\n"
                        f"{_format_functions_block(search_matches)}\n\n"
                        f"Call the correct tool with its exact name from the schema above."
                    )
                    status_line = (
                        f'● Unknown tool: {tool_name}\n'
                        f'└  Suggested: {suggestion_names}'
                    )
                else:
                    result_content = (
                        f"The tool `{tool_name}` does not exist and no close matches were found. "
                        f"Call `ToolSearch(query=\"<keywords>\")` with keywords from the task "
                        f"(not a guessed tool name) to discover the right tool."
                    )
                    status_line = (
                        f'● Unknown tool: {tool_name}\n'
                        f'└  No close matches · model told to ToolSearch with keywords'
                    )

            if result_content is None:
                # Defensive: should be unreachable now. Stop the loop.
                break

            # Write the status block to the wire NOW so the user sees the tool
            # call immediately, not bundled with the final model response.
            if status_line:
                await _emit_live_status(status_line)

            if matched:
                body["tools"] = body["tools"] + matched
                core_visible_names |= {t.get("name", "") for t in matched}

            body["messages"] = body.get("messages", []) + [
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": tool_use["id"],
                     "name": tool_name, "input": tool_input},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tool_use["id"],
                     "content": result_content},
                ]},
            ]
            # Next iteration: base_index_offset=status_emitted shifts upstream
    finally:
        await _stop_heartbeat(heartbeat)

    if not resp.prepared:
        _apply_cors_to_stream(resp, request)
        await resp.prepare(request)
    await resp.write_eof()
    return resp


async def _handle_non_streaming(
    upstream: str,
    body: dict[str, Any],
    headers: dict[str, str],
    deferred: list[dict[str, Any]],
    managed_intercept: set[str] | None = None,
    auto_load: bool = False,
) -> web.Response:
    """Handle non-streaming request. Same intercept model as streaming:
    ToolSearch+deferred always intercepted when deferred exist;
    managed tools intercepted only when `managed_intercept` names are set.
    """
    from proxy.managed_tools import get_handler, get_tool, format_visibility, run_pre_llm, run_post_llm

    deferred_names = {t["name"] for t in deferred}
    managed_names = managed_intercept or set()
    # Names currently visible to the model as core tools (for hallucination guard)
    core_visible_names: set[str] = {t.get("name", "") for t in body.get("tools", [])}
    summaries: list[str] = []

    max_roundtrips = proxy_config.max_roundtrips()
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
                result_text = _format_functions_block(matched)

            elif (not auto_load) and tool_name in deferred_names and tool_name not in core_visible_names:
                result_text = (
                    f"`{tool_name}` is currently UNLOADED in this conversation.\n\n"
                    f"Call `ToolSearch(query=\"select:{tool_name}\", max_results=5)` to load its schema, "
                    f"then call `{tool_name}` again using the parameter names from that schema."
                )

            elif tool_name in managed_names:
                tool_entry = get_tool(tool_name)
                handler = tool_entry.handler if tool_entry else None
                if handler and tool_entry:
                    try:
                        enriched = await run_pre_llm(tool_entry, block.get("input", {}))
                        summary, result_text = await handler(enriched)
                        result_text = await run_post_llm(tool_entry, result_text)
                    except Exception as exc:
                        summary = f"Failed: {exc}"
                        result_text = f"ERROR: {tool_name} failed: {exc}"
                    summaries.append(format_visibility(tool_name, block.get("input", {}), summary))

            elif auto_load and tool_name in deferred_names and tool_name not in core_visible_names:
                # Only fire on the FIRST call — after injection the tool joins
                # core_visible_names and subsequent calls pass through to CC.
                matched = [t for t in deferred if t["name"] == tool_name]
                result_text = (
                    f"This tool's schema was not loaded. Here is the schema:\n\n"
                    f"{_format_functions_block(matched)}\n\n"
                    f"Call the tool again with the correct parameter names."
                )

            elif tool_name not in core_visible_names and (deferred or core_visible_names):
                # Hallucination guard — show top matches, don't inject schemas.
                haystack = list(body.get("tools", [])) + deferred
                search_matches = await _do_tool_search(
                    haystack, {"query": tool_name, "max_results": 5}
                )
                matched = []
                if search_matches:
                    result_text = (
                        f"The tool `{tool_name}` does not exist. Did you mean one of these?\n\n"
                        f"{_format_functions_block(search_matches)}\n\n"
                        f"Call the correct tool with its exact name from the schema above."
                    )
                else:
                    result_text = (
                        f"The tool `{tool_name}` does not exist and no close matches were found. "
                        f"Call `ToolSearch(query=\"<keywords>\")` with task-related keywords "
                        f"to discover the right tool."
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
    except Exception:
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
        return None

    port = proxy_config.proxy_port()
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    host = proxy_config.proxy_host()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner
