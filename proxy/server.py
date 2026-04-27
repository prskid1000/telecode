"""Dual-protocol proxy in front of llama-server.

Exposes both Anthropic `/v1/messages` and OpenAI `/v1/chat/completions`
to clients. Internally everything is translated to OpenAI shape (llama.cpp
native) before hitting the upstream; the response stream is translated
back to whatever protocol the client used.

The intercept loop (ToolSearch / managed tools / auto-load / hallucination
guard) runs on the internal OpenAI shape. Per-protocol differences are
confined to the `ClientAdapter` subclasses at the top of this file.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import aiohttp
from aiohttp import web

from proxy import config as proxy_config
import config
from proxy import managed_tools  # noqa: F401  side-effect: registers tools
from proxy import request_log
from proxy import translate as xlate
from proxy import tokenizer as toks
from proxy import api_sessions
from proxy import api_tasks
from proxy import api_agents
from proxy import api_jobs
from proxy import api_skills
from proxy.tool_registry import (
    proxy_system_instruction,
    strip_all_reminders,
)
from proxy.tool_search import BM25Index
from llamacpp import config as llama_cfg
from process import get_supervisor

log = logging.getLogger("telecode.proxy")

_HEARTBEAT_INTERVAL = 2.0


# ═══════════════════════════════════════════════════════════════════════
# SSE utilities
# ═══════════════════════════════════════════════════════════════════════

async def _ensure_prepared(resp: web.StreamResponse, request: web.Request) -> None:
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
    *,
    protocol: str,
) -> asyncio.Task:
    """Send wire-level keep-alives every 2s and protocol pings every N seconds.

    Anthropic protocol: `event: ping` frames (CC / pivot recognize these).
    OpenAI protocol: SSE comment lines only (OpenAI SSE has no ping event).
    """
    await _ensure_prepared(resp, request)
    ping_every = max(_HEARTBEAT_INTERVAL, proxy_config.ping_interval())
    anthropic_ping = b"event: ping\ndata: {\"type\":\"ping\"}\n\n"

    async def _beat() -> None:
        elapsed = 0.0
        last_ping = 0.0
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
                elapsed += _HEARTBEAT_INTERVAL
                async with write_lock:
                    try:
                        if elapsed - last_ping >= ping_every and protocol == "anthropic":
                            await resp.write(anthropic_ping)
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


def _apply_cors_to_stream(resp: web.StreamResponse, request: web.Request) -> None:
    origins = proxy_config.cors_origins()
    if not origins:
        return
    origin = request.headers.get("Origin", "")
    allowed = "*" in origins or origin in origins
    if allowed:
        resp.headers["Access-Control-Allow-Origin"] = origin or "*"
        resp.headers["Access-Control-Allow-Private-Network"] = "true"


# ═══════════════════════════════════════════════════════════════════════
# Location detection (for date/location injection)
# ═══════════════════════════════════════════════════════════════════════

_location_cache: str | None = None


async def _get_location() -> str:
    global _location_cache
    configured = proxy_config.location()
    if configured:
        return configured
    if _location_cache is not None:
        return _location_cache
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("http://ip-api.com/json/?fields=city,country") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    city = data.get("city", "")
                    country = data.get("country", "")
                    _location_cache = f"{city}, {country}" if (city and country) else (country or "")
                else:
                    _location_cache = ""
    except Exception:
        _location_cache = ""
    return _location_cache or ""


# ═══════════════════════════════════════════════════════════════════════
# Profile matching
# ═══════════════════════════════════════════════════════════════════════

def _match_profile(headers) -> dict | None:
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


# ═══════════════════════════════════════════════════════════════════════
# Client adapters — per-protocol status emission
# ═══════════════════════════════════════════════════════════════════════

class ClientAdapter:
    """Per-protocol helpers. Each adapter knows how to:
      - emit message_start/initial-frame ONCE at request start (so status
        blocks that follow aren't buffered by the client SSE parser)
      - emit a status line (tool-call visibility) between rounds
      - translate upstream OpenAI chunks to the client's protocol
    """

    protocol = "anthropic"

    def __init__(self, client_model: str) -> None:
        self.client_model = client_model
        self.initial_emitted = False

    def initial_frame(self) -> bytes:
        """Frame the client must see before any status/content. Override per protocol."""
        raise NotImplementedError

    def emit_status(self, text: str) -> bytes:
        raise NotImplementedError

    def reset_state(self, reasoning_cfg: dict[str, Any]) -> None:
        raise NotImplementedError

    def translate_openai_chunk(self, chunk: dict[str, Any]) -> bytes:
        raise NotImplementedError

    def end_stream(self) -> bytes:
        raise NotImplementedError


class AnthropicAdapter(ClientAdapter):
    protocol = "anthropic"

    def __init__(self, client_model: str) -> None:
        super().__init__(client_model)
        self.status_emitted = 0
        self.state: xlate.AnthropicStreamState | None = None
        # Shared across rounds so start_message/end_stream fire exactly once.
        self._message_id = f"msg_{uuid.uuid4().hex[:24]}"

    def initial_frame(self) -> bytes:
        """Emit Anthropic `message_start` once. Clients buffer ALL subsequent
        events until they see this — critical for status-block visibility."""
        if self.initial_emitted:
            return b""
        self.initial_emitted = True
        ev = {
            "type": "message_start",
            "message": {
                "id": self._message_id,
                "type": "message",
                "role": "assistant",
                "model": self.client_model or "unknown",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        }
        return (
            f"event: message_start\n"
            f"data: {json.dumps(ev)}\n\n"
        ).encode()

    def emit_status(self, text: str) -> bytes:
        """Status = synthetic text content block. Indices start at 0 and
        increment per status, before any real content blocks (which start
        at `status_emitted` and go up from there)."""
        frame = xlate.emit_anthropic_status_block(text, self.status_emitted)
        self.status_emitted += 1
        return frame

    def reset_state(self, reasoning_cfg: dict[str, Any]) -> None:
        """Create a fresh AnthropicStreamState for a new upstream round.
        Stream state's next_index is offset past already-emitted status
        blocks. message_start was already emitted by `initial_frame` at
        request start, so we mark the state as 'started' to suppress its
        own message_start emission in step()."""
        state = xlate.AnthropicStreamState(
            reasoning=xlate.ReasoningState(
                start_tag=reasoning_cfg.get("start", "<think>"),
                end_tag=reasoning_cfg.get("end", "</think>"),
                emit_thinking=reasoning_cfg.get("emit_thinking_blocks", True),
                enabled=reasoning_cfg.get("enabled", True),
            ),
            client_model=self.client_model,
        )
        state._next_index = self.status_emitted
        state._message_started = True  # initial_frame already sent it
        state._message_id = self._message_id
        self.state = state

    def translate_openai_chunk(self, chunk: dict[str, Any]) -> bytes:
        assert self.state is not None
        return self.state.step(chunk)

    def end_stream(self) -> bytes:
        return b""  # message_stop emitted by state on finish_reason


class OpenAIAdapter(ClientAdapter):
    protocol = "openai"

    def __init__(self, client_model: str) -> None:
        super().__init__(client_model)
        self.completion_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"

    def initial_frame(self) -> bytes:
        """Emit a `role: "assistant"` opener chunk once. OpenAI clients
        expect the first chunk's delta to carry the role — putting it
        before status/content keeps strict parsers happy."""
        if self.initial_emitted:
            return b""
        self.initial_emitted = True
        chunk = {
            "id": self.completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": self.client_model or "unknown",
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant", "content": ""},
                "finish_reason": None,
            }],
        }
        return f"data: {json.dumps(chunk)}\n\n".encode()

    def emit_status(self, text: str) -> bytes:
        return xlate.emit_openai_status_chunk(text, self.client_model, self.completion_id)

    def reset_state(self, reasoning_cfg: dict[str, Any]) -> None:
        pass  # OpenAI clients get raw upstream chunks (identity translation)

    def translate_openai_chunk(self, chunk: dict[str, Any]) -> bytes:
        """Rewrite only the `model` field (so the client sees the alias it
        sent) and forward the rest verbatim. Strip any `role` from deltas
        after the opener (we already emitted it in initial_frame)."""
        if self.client_model:
            chunk = {**chunk, "model": self.client_model}
        chunk["id"] = self.completion_id  # unify the id across round-trips
        # Drop redundant role emissions — we emitted role in initial_frame.
        for ch in chunk.get("choices", []) or []:
            d = ch.get("delta")
            if isinstance(d, dict) and "role" in d:
                d.pop("role", None)
        return f"data: {json.dumps(chunk)}\n\n".encode()

    def end_stream(self) -> bytes:
        return b"data: [DONE]\n\n"


# ═══════════════════════════════════════════════════════════════════════
# Request preparation (shared by Anthropic + OpenAI paths)
# ═══════════════════════════════════════════════════════════════════════

async def _inject_system_prompt(
    body: dict[str, Any],
    profile: dict | None,
    inject_date_location: bool,
) -> dict[str, Any]:
    """Prepend profile system_instruction and/or date+location to body.

    body is in INTERNAL (OpenAI) shape — we modify the first system message
    or prepend a new one.
    """
    parts: list[str] = []

    system_md = profile.get("system_instruction") if profile else None
    if system_md:
        instruction = proxy_system_instruction(system_md)
        if instruction:
            parts.append(instruction)

    if inject_date_location:
        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d (%A)")
        location = await _get_location()
        segs = [f"Current date: {date_str}."]
        if location:
            segs.append(f"User location: {location}.")
        parts.append("<system-reminder>\n" + " ".join(segs) + "\n</system-reminder>")

    if not parts:
        return body

    injection = "\n\n".join(parts)
    messages = body.get("messages", [])
    if messages and messages[0].get("role") == "system":
        existing = messages[0].get("content", "")
        if isinstance(existing, str):
            messages[0] = {**messages[0], "content": f"{injection}\n\n{existing}" if existing else injection}
        elif isinstance(existing, list):
            # Re-emit as string (llama.cpp handles both but string is cheaper)
            flat = "\n".join(p.get("text", "") for p in existing if isinstance(p, dict) and p.get("type") == "text")
            messages[0] = {**messages[0], "content": f"{injection}\n\n{flat}" if flat else injection}
    else:
        body["messages"] = [{"role": "system", "content": injection}] + list(messages)

    return body


def _apply_tool_transforms(
    body: dict[str, Any],
    profile: dict | None,
    use_tool_search: bool,
    managed_inject_names: list[str],
    sort_tools: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Split tools into core + deferred for the internal body.

    The internal body uses OpenAI-shape `tools: [{type:"function", function:{...}}]`.
    We work with those directly.

    Returns (body, deferred_anthropic_shape_for_ToolSearch_BM25).
    """
    # Strip cache_control (defensive — translate.py already does this, but
    # clients sometimes mirror it on tool definitions too).
    tools_raw = body.get("tools", []) or []
    tools: list[dict[str, Any]] = []
    for t in tools_raw:
        if isinstance(t, dict):
            tools.append({k: v for k, v in t.items() if k != "cache_control"})

    # Resolve managed tools to inject
    from proxy.managed_tools import _REGISTRY as _MGR
    inject_schemas: list[dict[str, Any]] = []
    managed_strip: set[str] = set()
    for name in managed_inject_names:
        mt = _MGR.get(name)
        if not mt:
            continue
        managed_strip.add(mt.name)
        managed_strip.update(mt.strip_from_cc)
        inject_schemas.append(mt.schema)

    # Strip managed-tool-equivalents the client sent
    def _fn_name(tool: dict[str, Any]) -> str:
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else None
        return fn.get("name", "") if fn else tool.get("name", "")

    tools = [t for t in tools if _fn_name(t) not in managed_strip]

    # Convert Anthropic-shape managed schemas to OpenAI-shape tools for injection
    def _anth_to_openai_tool(s: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": s.get("name", ""),
                "description": s.get("description", ""),
                "parameters": s.get("input_schema", {"type": "object"}),
            },
        }

    managed_oa = [_anth_to_openai_tool(s) for s in inject_schemas]

    deferred: list[dict[str, Any]] = []
    if use_tool_search:
        core_names = set(
            (profile.get("core_tools") if profile and "core_tools" in profile else proxy_config.core_tools())
            or []
        )

        core_tools_out: list[dict[str, Any]] = []
        for t in tools:
            name = _fn_name(t)
            if name == "ToolSearch":
                # Never defer the meta-tool itself — we always re-inject it below
                # if there's anything deferred. Drop incoming copies so it can't
                # leak into the deferred listing.
                continue
            if name in core_names:
                core_tools_out.append(t)
            else:
                # Convert to Anthropic shape for BM25 / ToolSearch results
                fn = t.get("function") or {}
                deferred.append({
                    "name": fn.get("name", name),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object"}),
                })

        # Inject ToolSearch meta-tool whenever we have deferred tools
        if deferred:
            from proxy.tool_registry import TOOL_SEARCH_TOOL
            core_tools_out.insert(0, _anth_to_openai_tool(TOOL_SEARCH_TOOL))

        # Managed tools are always core-visible
        tools = managed_oa + core_tools_out
    else:
        tools = managed_oa + tools

    if sort_tools and tools:
        tools.sort(key=_fn_name)

    if tools:
        body["tools"] = tools
    elif "tools" in body:
        del body["tools"]

    return body, deferred


def _inject_deferred_reminder(
    body: dict[str, Any],
    deferred: list[dict[str, Any]],
) -> dict[str, Any]:
    """Tell the model which tool NAMES are unloaded (schemas retrievable via ToolSearch)."""
    if not deferred:
        return body
    names = ", ".join(t["name"] for t in deferred)
    reminder = (
        "<system-reminder>\n"
        f"Unloaded tools (call ToolSearch to load schema before use): {names}\n"
        "</system-reminder>"
    )
    messages = body.get("messages", [])
    # Append to the first user message so the model sees it in context
    for i, msg in enumerate(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            messages[i] = {**msg, "content": f"{reminder}\n\n{content}" if content else reminder}
        elif isinstance(content, list):
            messages[i] = {**msg, "content": [{"type": "text", "text": reminder}] + list(content)}
        break
    return body


async def _prepare_internal_body(
    body: dict[str, Any],
    request: web.Request,
    inbound_protocol: str,
) -> dict[str, Any]:
    """Translate client body → internal body, apply all proxy transforms,
    return a dict with keys:
      body: internal-shape body (ready for llama.cpp /v1/chat/completions)
      deferred: list of deferred tools (Anthropic-shape, for BM25)
      managed_intercept: set of managed tool names to intercept
      auto_load: bool
      client_model: str (original requested model; for reverse mapping)
      active_model: str (resolved llama.cpp registry key)
      reasoning_cfg: dict (inference_for(active_model).reasoning)
      profile: matched profile or None
    """
    profile = _match_profile(request.headers)
    requested_model = body.get("model", "") or ""

    # 1. Resolve model via registry / mapping (so we can apply its inference defaults)
    active_model = llama_cfg.resolve_model(requested_model)
    if not active_model:
        raise web.HTTPBadRequest(reason=f"Unknown model: {requested_model}. Register in llamacpp.models.")

    inference = llama_cfg.inference_for(active_model)

    # 2. Client body → internal (OpenAI) body
    if inbound_protocol == "anthropic":
        internal = xlate.anthropic_request_to_internal(body, inference_defaults=inference)
    else:
        internal = xlate.openai_request_to_internal(body, inference_defaults=inference)

    internal["model"] = active_model

    # 3. Profile-driven feature flags
    def _pget(key: str, default):
        if profile and key in profile:
            return profile[key]
        return default

    use_tool_search = _pget("tool_search", proxy_config.tool_search())
    inject_date_loc = _pget("inject_date_location", True)
    use_strip_reminders = _pget("strip_reminders", proxy_config.strip_reminders())
    use_auto_load = _pget("auto_load_tools", proxy_config.auto_load_tools())
    use_sort_tools = _pget("sort_tools", proxy_config.sort_tools())

    # 4. System-prompt injection
    internal = await _inject_system_prompt(internal, profile, inject_date_loc)

    # 5. Tool transforms (split into core/deferred, inject managed)
    from proxy.managed_tools import _REGISTRY as _MGR
    from proxy.runtime_state import is_managed_enabled as _is_enabled
    managed_inject_raw: list[str] = (
        profile.get("inject_managed") if profile and "inject_managed" in profile
        else list(_MGR.keys())
    ) or []
    # Honor live runtime toggles set via the control panel
    managed_inject: list[str] = [n for n in managed_inject_raw if _is_enabled(n)]
    
    # Filter session tools if disabled in config
    if not config.enable_session_tools():
        session_tools = {"session_create", "session_get", "session_list", "session_delete",
                         "task_submit", "task_status", "task_list_types"}
        managed_inject = [n for n in managed_inject if n not in session_tools]

    internal, deferred = _apply_tool_transforms(
        internal, profile, use_tool_search, managed_inject,
        sort_tools=use_sort_tools,
    )

    # 6. Inject deferred-listing reminder into first user message
    if deferred:
        internal = _inject_deferred_reminder(internal, deferred)

    # 7. Strip reminders (after our own injection, so keep ours)
    if use_strip_reminders:
        internal["messages"] = _strip_reminders_from_internal(internal.get("messages", []))

    managed_intercept = {
        _MGR[n].name for n in managed_inject if n in _MGR
    }

    # Translator may have embedded per-request reasoning overrides
    # (thinking.display=omitted, adaptive, etc.) in `_telecode_hints`.
    hints = xlate.pop_hints(internal)
    reasoning_cfg = dict(inference.get("reasoning", {}))
    if "emit_thinking_blocks" in hints:
        reasoning_cfg["emit_thinking_blocks"] = bool(hints["emit_thinking_blocks"])

    return {
        "body": internal,
        "deferred": deferred,
        "managed_intercept": managed_intercept,
        "auto_load": use_auto_load,
        "client_model": requested_model,
        "active_model": active_model,
        "reasoning_cfg": reasoning_cfg,
        "profile": profile,
    }


def _strip_reminders_from_internal(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-use the Anthropic-shape reminder stripper. The content we care
    about is just the text inside messages — role labels are irrelevant."""
    # Wrap each message's content into Anthropic-shape, strip, unwrap
    adapted = []
    for msg in messages:
        c = msg.get("content")
        if isinstance(c, str):
            adapted.append({"role": msg.get("role", "user"), "content": c})
        elif isinstance(c, list):
            adapted.append({"role": msg.get("role", "user"), "content": c})
        else:
            adapted.append(msg)
    stripped = strip_all_reminders(adapted)
    # Merge back any metadata keys we might have dropped (tool_calls, tool_call_id, name)
    out = []
    by_role: dict[int, dict[str, Any]] = {}
    for i, s in enumerate(stripped):
        original = messages[i] if i < len(messages) else {}
        merged = {**original, **s}
        out.append(merged)
        by_role[i] = merged
    return out


# ═══════════════════════════════════════════════════════════════════════
# ToolSearch / status helpers
# ═══════════════════════════════════════════════════════════════════════

def _format_functions_block(matched: list[dict[str, Any]]) -> str:
    if not matched:
        return "No matching tools found. Try a different query."
    lines = ["<functions>"]
    for t in matched:
        entry = {
            "description": t.get("description", ""),
            "name": t.get("name", ""),
            "parameters": t.get("input_schema", {}),
        }
        lines.append(f"<function>{json.dumps(entry)}</function>")
    lines.append("</functions>")
    return "\n".join(lines)


async def _do_tool_search(
    deferred: list[dict[str, Any]],
    args: dict[str, Any],
) -> list[dict[str, Any]]:
    query = args.get("query", "")
    max_results = args.get("max_results", 5)

    if query.startswith("select:"):
        names = {n.strip() for n in query[7:].split(",") if n.strip()}
        return [t for t in deferred if t.get("name", "") in names]

    if query.startswith("+"):
        parts = query.split(None, 1)
        required = parts[0][1:].lower()
        filtered = [t for t in deferred if required in t.get("name", "").lower()]
        if len(parts) > 1 and filtered:
            return BM25Index(filtered).search(parts[1], max_results)
        return filtered[:max_results]

    return BM25Index(deferred).search(query, max_results)


def _anth_tool_to_openai_tool(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": s.get("name", ""),
            "description": s.get("description", ""),
            "parameters": s.get("input_schema", {"type": "object"}),
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# OpenAI SSE stream reader with first-tool-call decision
# ═══════════════════════════════════════════════════════════════════════

class InterceptedToolCall:
    """Signal object: upstream started with a tool_call that matched our
    intercept set. Caller handles the call and re-invokes upstream."""
    def __init__(self, id: str, name: str, arguments: str, hallucinated: bool = False) -> None:
        self.id = id
        self.name = name
        self.arguments = arguments
        self.hallucinated = hallucinated


async def _run_upstream_round(
    internal_body: dict[str, Any],
    headers: dict[str, str],
    resp: web.StreamResponse,
    request: web.Request,
    adapter: ClientAdapter,
    reasoning_cfg: dict[str, Any],
    *,
    intercept_names: set[str],
    known_names: set[str],
    write_lock: asyncio.Lock,
) -> Optional[InterceptedToolCall]:
    """One upstream round-trip. Returns InterceptedToolCall if the first
    content block is an intercepted tool_call (nothing written to client);
    otherwise streams the response through to the client and returns None.
    """
    upstream = llama_cfg.upstream_url()
    url = f"{upstream}/v1/chat/completions"

    # OpenAI stream SSE parser state
    buf = ""
    decided: str | None = None  # None | "intercept" | "passthrough"
    # Tool-call assembly (per-call-index)
    tool_parts: dict[int, dict[str, Any]] = {}
    tool_order: list[int] = []

    adapter.reset_state(reasoning_cfg)

    # Mark this round as in-flight so the supervisor's idle-unload watcher
    # never tears down llama-server mid-stream. We end-request just before
    # each return path below (there are 4: 502 from upstream, [DONE],
    # captured-tool-call, stream-without-DONE).
    supervisor = await get_supervisor()
    await supervisor.begin_request()

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=internal_body, headers=headers) as upstream_resp:
            if upstream_resp.status != 200:
                errtext = await upstream_resp.text()
                log.warning("upstream %d: %s", upstream_resp.status, errtext[:500])
                async with write_lock:
                    await _ensure_prepared(resp, request)
                    err = {
                        "type": "error",
                        "error": {
                            "type": "upstream_error",
                            "status": upstream_resp.status,
                            "body": errtext[:500],
                        },
                    }
                    if adapter.protocol == "anthropic":
                        await resp.write(b"event: error\n")
                        await resp.write(f"data: {json.dumps(err)}\n\n".encode())
                    else:
                        await resp.write(f"data: {json.dumps(err)}\n\n".encode())
                        await resp.write(b"data: [DONE]\n\n")
                await supervisor.end_request()
                return None

            async for chunk in upstream_resp.content.iter_any():
                text = chunk.decode("utf-8", errors="replace")
                buf += text

                while "\n\n" in buf:
                    event_block, buf = buf.split("\n\n", 1)
                    data_line = None
                    for line in event_block.split("\n"):
                        if line.startswith("data: "):
                            data_line = line[6:]
                            break
                    if data_line is None:
                        continue
                    if data_line.strip() == "[DONE]":
                        # End of stream
                        if decided == "passthrough":
                            async with write_lock:
                                await resp.write(adapter.end_stream())
                        await supervisor.end_request()
                        return None

                    try:
                        event = json.loads(data_line)
                    except json.JSONDecodeError:
                        continue

                    # ── Pre-decision: watch first content signal ────────
                    just_decided = False
                    if decided is None:
                        choices = event.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {}) or {}
                            tcs = delta.get("tool_calls", []) or []
                            content = delta.get("content")

                            if tcs:
                                # First tool_call — assemble until we know the name,
                                # then decide intercept/passthrough.
                                for tc in tcs:
                                    idx = tc.get("index", 0)
                                    entry = tool_parts.setdefault(idx, {
                                        "id": tc.get("id", ""),
                                        "name": "",
                                        "arguments": "",
                                    })
                                    if tc.get("id"):
                                        entry["id"] = tc["id"]
                                    fn = tc.get("function", {}) or {}
                                    if fn.get("name"):
                                        entry["name"] += fn["name"]
                                    if "arguments" in fn:
                                        entry["arguments"] += fn["arguments"] or ""
                                    if idx not in tool_order:
                                        tool_order.append(idx)

                                first_idx = tool_order[0]
                                first_name = tool_parts[first_idx]["name"]
                                if first_name:
                                    if first_name in intercept_names:
                                        decided = "intercept"
                                    elif known_names and first_name not in known_names:
                                        decided = "intercept"  # hallucinated
                                    else:
                                        decided = "passthrough"
                                    just_decided = True
                                # else still waiting for full name
                            elif content or choices[0].get("finish_reason"):
                                decided = "passthrough"
                                just_decided = True

                    # ── Post-decision handling ──────────────────────────
                    # If decision flipped THIS event, we already consumed the
                    # tool-call fragment in the pre-decision branch; don't
                    # re-append name/arguments below.
                    if decided == "passthrough":
                        async with write_lock:
                            await _ensure_prepared(resp, request)
                            await resp.write(adapter.translate_openai_chunk(event))
                    elif decided == "intercept":
                        # Keep assembling the first tool_call's arguments.
                        # Skip assembly on the decision tick — already done.
                        choices = event.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {}) or {}
                            if not just_decided:
                                for tc in delta.get("tool_calls", []) or []:
                                    idx = tc.get("index", 0)
                                    if idx not in tool_parts:
                                        continue
                                    fn = tc.get("function", {}) or {}
                                    if fn.get("name"):
                                        tool_parts[idx]["name"] += fn["name"]
                                    if "arguments" in fn:
                                        tool_parts[idx]["arguments"] += fn["arguments"] or ""
                            if choices[0].get("finish_reason"):
                                # Stream ended — return the captured call
                                first_idx = tool_order[0]
                                entry = tool_parts[first_idx]
                                await supervisor.end_request()
                                return InterceptedToolCall(
                                    id=entry["id"] or f"call_{uuid.uuid4().hex[:12]}",
                                    name=entry["name"],
                                    arguments=entry["arguments"],
                                    hallucinated=(entry["name"] not in intercept_names),
                                )
                    # else decided is None: still pre-decision, keep reading

    # Stream ended without [DONE] — treat as passthrough complete
    try:
        await supervisor.end_request()
    except Exception:
        pass
    return None


async def _emit_status(
    adapter: ClientAdapter,
    resp: web.StreamResponse,
    request: web.Request,
    write_lock: asyncio.Lock,
    text: str,
) -> None:
    """Write a status line (tool-call visibility) to the wire immediately."""
    await _ensure_prepared(resp, request)
    async with write_lock:
        await resp.write(adapter.emit_status(text))
        writer = getattr(resp, "_payload_writer", None)
        if writer is not None:
            try:
                await writer.drain()
            except (ConnectionResetError, ConnectionError):
                pass


# ═══════════════════════════════════════════════════════════════════════
# Streaming intercept loop
# ═══════════════════════════════════════════════════════════════════════

async def _run_streaming(
    prep: dict[str, Any],
    request: web.Request,
    adapter: ClientAdapter,
) -> web.StreamResponse:
    """Execute the streaming intercept loop for one request."""
    from proxy.managed_tools import is_managed, get_tool, format_visibility, run_pre_llm, run_post_llm

    body = prep["body"]
    deferred = prep["deferred"]
    managed_intercept = prep["managed_intercept"]
    auto_load = prep["auto_load"]
    reasoning_cfg = prep["reasoning_cfg"]
    active_model = prep["active_model"]

    deferred_names = {t["name"] for t in deferred}

    resp = web.StreamResponse()
    write_lock = asyncio.Lock()
    resp._write_lock = write_lock
    heartbeat: asyncio.Task | None = None

    # Names currently exposed as callable tools
    def _fn_name(tool: dict[str, Any]) -> str:
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else None
        return fn.get("name", "") if fn else tool.get("name", "")

    core_visible_names: set[str] = {_fn_name(t) for t in body.get("tools", [])}

    headers = {"Content-Type": "application/json"}
    api_key = llama_cfg.api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Ensure the correct model is loaded
    try:
        supervisor = await get_supervisor()
        await supervisor.ensure_model(active_model)
    except Exception as exc:
        log.error("model swap failed: %s", exc, exc_info=True)
        # Return a minimal error through the adapter's protocol
        err_msg = f"Failed to load model '{active_model}': {exc}"
        return web.json_response(
            {"type": "error", "error": {"type": "model_load_error", "message": err_msg}},
            status=503,
        )

    try:
        heartbeat = await _start_heartbeat(resp, request, write_lock, protocol=adapter.protocol)

        # Emit the protocol's initial frame IMMEDIATELY so that any status
        # block we push between rounds isn't buffered by the client's SSE
        # parser. Anthropic: message_start. OpenAI: role:"assistant" opener.
        initial = adapter.initial_frame()
        if initial:
            await _ensure_prepared(resp, request)
            async with write_lock:
                await resp.write(initial)
                writer = getattr(resp, "_payload_writer", None)
                if writer is not None:
                    try:
                        await writer.drain()
                    except (ConnectionResetError, ConnectionError):
                        pass

        max_roundtrips = proxy_config.max_roundtrips()
        rounds_completed = 0
        for _rt in range(max_roundtrips):
            rounds_completed = _rt + 1
            # Rebuild intercept set each round (tools joined core_visible_names
            # via auto_load should stop being intercepted).
            intercept_names: set[str] = set()
            if deferred:
                intercept_names.add("ToolSearch")
                intercept_names |= (deferred_names - core_visible_names)
            intercept_names |= managed_intercept

            known_names = core_visible_names | deferred_names | intercept_names

            tool_call = await _run_upstream_round(
                body, headers, resp, request, adapter, reasoning_cfg,
                intercept_names=intercept_names,
                known_names=known_names,
                write_lock=write_lock,
            )

            if tool_call is None:
                break  # passthrough complete

            # ── Handle intercepted tool call ────────────────────────────
            try:
                tool_input = json.loads(tool_call.arguments) if tool_call.arguments else {}
            except json.JSONDecodeError:
                tool_input = {}

            tool_name = tool_call.name
            matched: list[dict[str, Any]] = []
            result_content: str | None = None
            status_line: str | None = None

            _rid = request.get("_rid")

            if tool_name == "ToolSearch":
                matched = await _do_tool_search(deferred, tool_input)
                result_content = _format_functions_block(matched)
                q = str(tool_input.get("query", ""))
                if matched:
                    names = ", ".join(m.get("name", "") for m in matched[:5])
                    status_line = f'● ToolSearch("{q[:80]}")\n└  {len(matched)} schemas loaded: {names}'
                else:
                    status_line = f'● ToolSearch("{q[:80]}")\n└  No matches'
                if _rid:
                    request_log.append_intercept(_rid, {
                        "type": "tool_search",
                        "query": q,
                        "matched": [m.get("name", "") for m in matched],
                    })

            elif is_managed(tool_name):
                tool_entry = get_tool(tool_name)
                if tool_entry and tool_entry.handler:
                    try:
                        enriched = await run_pre_llm(tool_entry, tool_input)
                        summary, result_content = await tool_entry.handler(enriched)
                        result_content = await run_post_llm(tool_entry, result_content)
                    except Exception as exc:
                        summary = f"Failed: {exc}"
                        result_content = f"ERROR: {tool_name} failed: {exc}"
                    status_line = format_visibility(tool_name, tool_input, summary)
                    if _rid:
                        # Truncate bulky result bodies (e.g. web-search output)
                        # so the log entry stays compact; full body still goes
                        # back to the model.
                        preview = (result_content or "")
                        if len(preview) > 2000:
                            preview = preview[:2000] + f"…(+{len(result_content or '') - 2000} chars)"
                        request_log.append_intercept(_rid, {
                            "type": "managed_tool",
                            "name": tool_name,
                            "input": tool_input,
                            "summary": summary,
                            "result_preview": preview,
                        })

            elif auto_load and tool_name in deferred_names and tool_name not in core_visible_names:
                matched = [t for t in deferred if t["name"] == tool_name]
                result_content = (
                    f"The schema for `{tool_name}` has now been loaded:\n\n"
                    f"{_format_functions_block(matched)}\n\n"
                    f"Call the tool again using the parameter names from this schema."
                )
                status_line = f'● Loaded {tool_name}\n└  Schema delivered · awaiting retry'
                if _rid:
                    request_log.append_intercept(_rid, {
                        "type": "auto_load", "name": tool_name,
                    })

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
                if _rid:
                    request_log.append_intercept(_rid, {
                        "type": "blocked", "name": tool_name,
                        "reason": "unloaded",
                    })

            else:
                # Hallucination guard
                haystack_tools = [
                    {
                        "name": _fn_name(t),
                        "description": (t.get("function") or {}).get("description", ""),
                        "input_schema": (t.get("function") or {}).get("parameters", {}),
                    }
                    for t in body.get("tools", [])
                ]
                haystack = haystack_tools + deferred
                search_matches = await _do_tool_search(
                    haystack, {"query": tool_name, "max_results": 5}
                )
                if search_matches:
                    sugg = ", ".join(m.get("name", "") for m in search_matches[:5])
                    result_content = (
                        f"The tool `{tool_name}` does not exist. Did you mean one of these?\n\n"
                        f"{_format_functions_block(search_matches)}\n\n"
                        f"Call the correct tool with its exact name from the schema above."
                    )
                    status_line = f'● Unknown tool: {tool_name}\n└  Suggested: {sugg}'
                else:
                    result_content = (
                        f"The tool `{tool_name}` does not exist and no close matches were found. "
                        f"Call `ToolSearch(query=\"<keywords>\")` with keywords from the task."
                    )
                    status_line = (
                        f'● Unknown tool: {tool_name}\n'
                        f'└  No close matches · model told to ToolSearch with keywords'
                    )
                if _rid:
                    request_log.append_intercept(_rid, {
                        "type": "hallucination", "name": tool_name,
                        "suggestions": [m.get("name", "") for m in search_matches],
                    })

            if result_content is None:
                break

            if status_line:
                await _emit_status(adapter, resp, request, write_lock, status_line)

            # Append matched schemas to body.tools (core-visible going forward)
            if matched:
                body.setdefault("tools", []).extend(_anth_tool_to_openai_tool(m) for m in matched)
                core_visible_names |= {m["name"] for m in matched}

            # Append [assistant-tool_call, tool-result] to messages (OpenAI shape)
            body.setdefault("messages", []).extend([
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": tool_call.arguments or "{}",
                        },
                    }],
                },
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_content,
                },
            ])
    finally:
        await _stop_heartbeat(heartbeat)

    # Streaming summary — we don't re-assemble the full text here (the
    # chunks went straight through to the client), but the intercept
    # list + round count already tells the "why was this slow / what did
    # the model do" story. The tray viewer's JSON tree is happy with a
    # tiny summary dict.
    _rid = request.get("_rid")
    if _rid:
        request_log.set_response_preview(_rid, {
            "mode": "stream",
            "rounds_completed": rounds_completed,
            "note": "streamed to client — content not re-captured here",
        })

    if not resp.prepared:
        _apply_cors_to_stream(resp, request)
        await resp.prepare(request)
    await resp.write_eof()
    return resp


# ═══════════════════════════════════════════════════════════════════════
# Non-streaming intercept loop
# ═══════════════════════════════════════════════════════════════════════

async def _run_non_streaming(
    prep: dict[str, Any],
    request: web.Request,
    inbound_protocol: str,
) -> web.Response:
    from proxy.managed_tools import is_managed, get_tool, format_visibility, run_pre_llm, run_post_llm

    body = prep["body"]
    deferred = prep["deferred"]
    managed_intercept = prep["managed_intercept"]
    auto_load = prep["auto_load"]
    reasoning_cfg = prep["reasoning_cfg"]
    active_model = prep["active_model"]
    client_model = prep["client_model"]

    body["stream"] = False

    deferred_names = {t["name"] for t in deferred}

    def _fn_name(tool: dict[str, Any]) -> str:
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else None
        return fn.get("name", "") if fn else tool.get("name", "")

    core_visible_names: set[str] = {_fn_name(t) for t in body.get("tools", [])}

    headers = {"Content-Type": "application/json"}
    api_key = llama_cfg.api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Ensure correct model loaded
    try:
        supervisor = await get_supervisor()
        await supervisor.ensure_model(active_model)
    except Exception as exc:
        return web.json_response(
            {"type": "error", "error": {"type": "model_load_error", "message": str(exc)}},
            status=503,
        )

    upstream = llama_cfg.upstream_url()
    url = f"{upstream}/v1/chat/completions"

    max_roundtrips = proxy_config.max_roundtrips()
    result: dict[str, Any] = {}
    summaries: list[str] = []

    for _rt in range(max_roundtrips):
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=headers) as upstream_resp:
                if upstream_resp.status != 200:
                    errtext = await upstream_resp.text()
                    return web.json_response(
                        {"type": "error", "error": {"type": "upstream_error", "status": upstream_resp.status, "body": errtext[:500]}},
                        status=502,
                    )
                result = await upstream_resp.json()

        choice = (result.get("choices") or [{}])[0]
        msg = choice.get("message", {}) or {}
        tool_calls = msg.get("tool_calls") or []
        finish = choice.get("finish_reason")

        if finish != "tool_calls" and not tool_calls:
            break  # done

        # Handle the first intercepted tool call (if any)
        handled = False
        for tc in tool_calls:
            fn = tc.get("function", {}) or {}
            tool_name = fn.get("name", "")
            try:
                tool_input = json.loads(fn.get("arguments", "{}") or "{}")
            except json.JSONDecodeError:
                tool_input = {}

            # Decide if we should intercept this call
            should_intercept = (
                tool_name == "ToolSearch"
                or tool_name in managed_intercept
                or tool_name in deferred_names
                or (tool_name and tool_name not in core_visible_names)  # hallucinated
            )
            if not should_intercept:
                continue

            matched: list[dict[str, Any]] = []
            result_text: str | None = None
            _rid = request.get("_rid")

            if tool_name == "ToolSearch":
                matched = await _do_tool_search(deferred, tool_input)
                result_text = _format_functions_block(matched)
                if _rid:
                    request_log.append_intercept(_rid, {
                        "type": "tool_search",
                        "query": str(tool_input.get("query", "")),
                        "matched": [m.get("name", "") for m in matched],
                    })

            elif is_managed(tool_name):
                tool_entry = get_tool(tool_name)
                if tool_entry and tool_entry.handler:
                    try:
                        enriched = await run_pre_llm(tool_entry, tool_input)
                        summary, result_text = await tool_entry.handler(enriched)
                        result_text = await run_post_llm(tool_entry, result_text)
                    except Exception as exc:
                        summary = f"Failed: {exc}"
                        result_text = f"ERROR: {tool_name} failed: {exc}"
                    summaries.append(format_visibility(tool_name, tool_input, summary))
                    if _rid:
                        preview = (result_text or "")
                        if len(preview) > 2000:
                            preview = preview[:2000] + f"…(+{len(result_text or '') - 2000} chars)"
                        request_log.append_intercept(_rid, {
                            "type": "managed_tool",
                            "name": tool_name,
                            "input": tool_input,
                            "summary": summary,
                            "result_preview": preview,
                        })

            elif auto_load and tool_name in deferred_names and tool_name not in core_visible_names:
                matched = [t for t in deferred if t["name"] == tool_name]
                result_text = (
                    f"The schema for `{tool_name}` has now been loaded:\n\n"
                    f"{_format_functions_block(matched)}\n\n"
                    f"Call the tool again with the correct parameter names."
                )
                if _rid:
                    request_log.append_intercept(_rid, {
                        "type": "auto_load", "name": tool_name,
                    })

            elif (not auto_load) and tool_name in deferred_names and tool_name not in core_visible_names:
                result_text = (
                    f"`{tool_name}` is currently UNLOADED. Call ToolSearch to load it."
                )
                if _rid:
                    request_log.append_intercept(_rid, {
                        "type": "blocked", "name": tool_name,
                        "reason": "unloaded",
                    })

            else:
                # Hallucination guard
                haystack_tools = [
                    {
                        "name": _fn_name(t),
                        "description": (t.get("function") or {}).get("description", ""),
                        "input_schema": (t.get("function") or {}).get("parameters", {}),
                    }
                    for t in body.get("tools", [])
                ]
                haystack = haystack_tools + deferred
                search_matches = await _do_tool_search(haystack, {"query": tool_name, "max_results": 5})
                if search_matches:
                    result_text = (
                        f"The tool `{tool_name}` does not exist. Did you mean:\n\n"
                        f"{_format_functions_block(search_matches)}"
                    )
                else:
                    result_text = f"The tool `{tool_name}` does not exist."
                if _rid:
                    request_log.append_intercept(_rid, {
                        "type": "hallucination", "name": tool_name,
                        "suggestions": [m.get("name", "") for m in search_matches],
                    })

            if not result_text:
                continue

            if matched:
                body.setdefault("tools", []).extend(_anth_tool_to_openai_tool(m) for m in matched)
                core_visible_names |= {m["name"] for m in matched}

            body.setdefault("messages", []).extend([
                {
                    "role": "assistant",
                    "content": msg.get("content"),
                    "tool_calls": [{
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {"name": tool_name, "arguments": fn.get("arguments", "{}")},
                    }],
                },
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result_text,
                },
            ])
            handled = True
            break

        if not handled:
            break

    # Prepend summaries to first text block (managed-tool visibility)
    if summaries:
        prefix = "\n".join(summaries) + "\n\n"
        choices = result.get("choices") or []
        if choices:
            mmsg = choices[0].get("message", {}) or {}
            content = mmsg.get("content") or ""
            if isinstance(content, str):
                mmsg["content"] = prefix + content
            choices[0]["message"] = mmsg
            result["choices"] = choices

    # Convert to client protocol
    _rid = request.get("_rid")
    if inbound_protocol == "anthropic":
        anth = xlate.openai_response_to_anthropic(
            result, reasoning_cfg=reasoning_cfg, client_model=client_model,
        )
        if _rid:
            request_log.set_response_preview(_rid, anth)
        return web.json_response(anth)
    else:
        # OpenAI identity — just rewrite `model`
        if client_model:
            result["model"] = client_model
        if _rid:
            request_log.set_response_preview(_rid, result)
        return web.json_response(result)


# ═══════════════════════════════════════════════════════════════════════
# Route handlers
# ═══════════════════════════════════════════════════════════════════════

async def handle_anthropic_messages(request: web.Request) -> web.StreamResponse:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    rid = request_log.new_request("POST", "/v1/messages",
                                  client_model=body.get("model", ""),
                                  inbound_protocol="anthropic")
    request_log.set_request_preview(rid, body)
    request["_rid"] = rid
    try:
        prep = await _prepare_internal_body(body, request, "anthropic")
        if prep["body"].get("stream", False):
            adapter = AnthropicAdapter(client_model=prep["client_model"])
            resp = await _run_streaming(prep, request, adapter)
        else:
            resp = await _run_non_streaming(prep, request, "anthropic")
        request_log.finish(rid, resp.status)
        return resp
    except Exception as exc:
        request_log.finish(rid, 500, error=str(exc))
        raise


async def handle_openai_chat_completions(request: web.Request) -> web.StreamResponse:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    rid = request_log.new_request("POST", "/v1/chat/completions",
                                  client_model=body.get("model", ""),
                                  inbound_protocol="openai")
    request_log.set_request_preview(rid, body)
    request["_rid"] = rid
    try:
        prep = await _prepare_internal_body(body, request, "openai")
        if prep["body"].get("stream", False):
            adapter = OpenAIAdapter(client_model=prep["client_model"])
            resp = await _run_streaming(prep, request, adapter)
        else:
            resp = await _run_non_streaming(prep, request, "openai")
        request_log.finish(rid, resp.status)
        return resp
    except Exception as exc:
        request_log.finish(rid, 500, error=str(exc))
        raise


async def handle_count_tokens(request: web.Request) -> web.Response:
    """POST /v1/messages/count_tokens — accurate via llama.cpp /tokenize."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    inbound_protocol = "anthropic" if _is_anthropic_request(request) else "openai"
    prep = await _prepare_internal_body(body, request, inbound_protocol)
    internal = prep["body"]
    active_model = prep["active_model"]

    # Ensure model is loaded so /apply-template + /tokenize use the right tokenizer
    try:
        supervisor = await get_supervisor()
        await supervisor.ensure_model(active_model)
    except Exception as exc:
        return web.json_response(
            {"error": {"type": "model_load_error", "message": str(exc)}},
            status=503,
        )

    messages = internal.get("messages", [])
    count = await toks.count_tokens(messages)

    if inbound_protocol == "openai":
        return web.json_response({
            "object": "list",
            "data": [{"object": "token_count", "token_count": count}],
            "model": active_model,
            "usage": {
                "prompt_tokens": count,
                "completion_tokens": 0,
                "total_tokens": count,
                "prompt_tokens_details": {
                    "cached_tokens": 0,
                    "audio_tokens": 0
                },
                "completion_tokens_details": {
                    "reasoning_tokens": 0,
                    "audio_tokens": 0,
                    "accepted_prediction_tokens": 0,
                    "rejected_prediction_tokens": 0
                }
            }
        })
    else:
        return web.json_response({
            "input_tokens": count,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "thinking_tokens": 0,
            "audio_tokens": 0,
            "id_slot": 0,
            "generation_settings": {},
            "timings": {
                "prompt_n": count,
                "prompt_ms": 0,
                "prompt_per_token_ms": 0,
                "prompt_per_second": 0,
                "predicted_n": 0,
                "predicted_ms": 0,
                "predicted_per_token_ms": 0,
                "predicted_per_second": 0
            }
        })


def _is_anthropic_request(request: web.Request) -> bool:
    """Detect whether a request wants Anthropic-shape output by header sniff."""
    headers = request.headers
    if "anthropic-version" in headers:
        return True
    if "x-api-key" in headers:
        return True
    return False


async def handle_models(request: web.Request) -> web.Response:
    """Dual-protocol /v1/models — shape chosen by header sniff."""
    # Fetch upstream models
    registered = list(llama_cfg.models().keys())
    aliases = proxy_config.model_mapping()

    if _is_anthropic_request(request):
        # Anthropic shape — registered models + aliases
        openai_data = xlate.build_openai_models(registered, aliases)
        return web.json_response(xlate.openai_models_to_anthropic(openai_data, aliases))

    # OpenAI shape
    return web.json_response(xlate.build_openai_models(registered, aliases))


async def handle_model_by_id(request: web.Request) -> web.Response:
    model_id = request.match_info["model_id"]
    registered = list(llama_cfg.models().keys())
    aliases = proxy_config.model_mapping()

    if _is_anthropic_request(request):
        openai_data = xlate.build_openai_models(registered, aliases)
        anth = xlate.openai_models_to_anthropic(openai_data, aliases)
        for m in anth.get("data", []):
            if m["id"] == model_id:
                return web.json_response(m)
        return web.json_response(
            {"type": "error", "error": {"type": "not_found_error", "message": f"model: {model_id}"}},
            status=404,
        )

    # OpenAI shape
    data = xlate.build_openai_models(registered, aliases).get("data", [])
    for m in data:
        if m["id"] == model_id:
            return web.json_response(m)
    return web.json_response({"error": {"message": f"Model {model_id} not found", "type": "not_found"}}, status=404)


async def handle_model_load(request: web.Request) -> web.Response:
    """POST /v1/models/{model_id}/load — load (or swap to) a model."""
    model_id = request.match_info["model_id"]
    try:
        supervisor = await get_supervisor()
        resolved = await supervisor.ensure_model(model_id)
    except Exception as exc:
        return web.json_response(
            {"error": {"type": "model_load_error", "message": str(exc)}},
            status=503,
        )
    return web.json_response({
        "status": "loaded",
        "requested": model_id,
        "active_model": resolved,
        "loaded_at": supervisor.loaded_at(),
    })


async def handle_model_load_default(request: web.Request) -> web.Response:
    """POST /v1/models/load — load the configured default model."""
    try:
        supervisor = await get_supervisor()
        resolved = await supervisor.start_default()
    except Exception as exc:
        return web.json_response(
            {"error": {"type": "model_load_error", "message": str(exc)}},
            status=503,
        )
    return web.json_response({
        "status": "loaded",
        "active_model": resolved,
        "loaded_at": supervisor.loaded_at(),
    })


async def handle_model_unload(request: web.Request) -> web.Response:
    """POST /v1/models/unload — stop the active llama-server."""
    try:
        supervisor = await get_supervisor()
        was_active = supervisor.active_model()
        await supervisor.stop()
    except Exception as exc:
        return web.json_response(
            {"error": {"type": "model_unload_error", "message": str(exc)}},
            status=500,
        )
    return web.json_response({"status": "unloaded", "previous_model": was_active})


async def handle_model_status(request: web.Request) -> web.Response:
    """GET /v1/models/active — current supervisor state."""
    supervisor = await get_supervisor()
    return web.json_response({
        "alive": supervisor.alive(),
        "active_model": supervisor.active_model(),
        "inflight": supervisor.inflight_count(),
        "loaded_at": supervisor.loaded_at(),
        "last_used": supervisor.last_used(),
    })


async def handle_embeddings(request: web.Request) -> web.Response:
    """POST /v1/embeddings — forward to llama.cpp unchanged."""
    body = await request.read()
    upstream = llama_cfg.upstream_url()

    headers = {}
    for h in ("content-type", "authorization"):
        if h in request.headers:
            headers[h] = request.headers[h]
    headers.setdefault("content-type", "application/json")

    async with aiohttp.ClientSession() as session:
        async with session.post(f"{upstream}/v1/embeddings", data=body, headers=headers) as up:
            out = await up.read()
            return web.Response(body=out, status=up.status, content_type=up.content_type)


async def handle_health(request: web.Request) -> web.Response:
    """Forward /health to llama.cpp for clients that probe it."""
    upstream = llama_cfg.upstream_url()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{upstream}/health", timeout=aiohttp.ClientTimeout(total=5)) as up:
                out = await up.read()
                return web.Response(body=out, status=up.status, content_type=up.content_type)
    except Exception as exc:
        return web.json_response({"status": "error", "message": str(exc)}, status=503)


async def handle_ui(request: web.Request) -> web.FileResponse:
    """Serve the new Telecode UI."""
    path = Path(__file__).parent / "static" / "telecode.html"
    if not path.exists():
        # Fallback to legacy if new UI doesn't exist yet
        path = Path(__file__).parent / "static" / "index.html"
    return web.FileResponse(path)


async def handle_legacy_ui(request: web.Request) -> web.FileResponse:
    """Serve the legacy session management UI."""
    path = Path(__file__).parent / "static" / "index.html"
    return web.FileResponse(path)


# ═══════════════════════════════════════════════════════════════════════
# App factory
# ═══════════════════════════════════════════════════════════════════════

@web.middleware
async def cors_middleware(request: web.Request, handler):
    origins = proxy_config.cors_origins()
    origin = request.headers.get("Origin", "")
    allowed = origins and ("*" in origins or origin in origins)

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

    protocols = set(proxy_config.protocols())

    if "anthropic" in protocols:
        app.router.add_post("/v1/messages/count_tokens", handle_count_tokens)
        app.router.add_post("/v1/messages", handle_anthropic_messages)

    if "openai" in protocols:
        app.router.add_post("/v1/chat/completions", handle_openai_chat_completions)

    # /v1/models routes are shared — shape chosen by header sniff
    app.router.add_get("/v1/models", handle_models)
    app.router.add_get("/v1/models/active", handle_model_status)
    app.router.add_post("/v1/models/load", handle_model_load_default)
    app.router.add_post("/v1/models/unload", handle_model_unload)
    app.router.add_post("/v1/models/{model_id}/load", handle_model_load)
    app.router.add_get("/v1/models/{model_id}", handle_model_by_id)

    # Embeddings + health forwarded to llama.cpp
    app.router.add_post("/v1/embeddings", handle_embeddings)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/ui", handle_ui)

    # Session and Task Management (pythonmagic-style)
    api_sessions.register_routes(app)
    api_tasks.register_routes(app)
    api_agents.register_routes(app)
    api_jobs.register_routes(app)
    api_skills.register_routes(app)

    app.router.add_get("/ui/legacy", handle_legacy_ui)

    return app


async def start_proxy_background() -> web.AppRunner | None:
    """Start proxy as a background task (non-blocking)."""
    if not proxy_config.enabled():
        return None

    request_log.clear()
    removed = request_log.clear_disk_dumps()
    if removed:
        log.info("cleared %d previous request dump(s) on startup", removed)

    port = proxy_config.proxy_port()
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    host = proxy_config.proxy_host()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("proxy listening on %s:%d — protocols=%s", host, port, proxy_config.protocols())
    return runner
