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
from collections import OrderedDict
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
from proxy import api_routines
from proxy import api_sessions
from proxy import api_tasks
from proxy import api_agents
from proxy import api_jobs
from proxy import api_skills
from proxy import api_runs
from proxy.tool_registry import (
    proxy_system_instruction,
    strip_all_reminders,
    limit_claude_md,
    strip_client_system_noise,
    strip_turn_context,
)
from proxy.tool_search import BM25Index
from llamacpp import config as llama_cfg
from process import get_supervisor

log = logging.getLogger("telecode.proxy")

_HEARTBEAT_INTERVAL = 2.0

# Upstream (llama.cpp) request timeout. aiohttp's default is total=300, which
# silently kills any generation running longer than 5 minutes — the slot is
# cancelled mid-stream and the client gets a 500 with an empty message
# (asyncio.TimeoutError stringifies to ""). Cap the wall clock at 1 hour and
# rely on sock_read to catch a genuinely hung upstream: during streaming,
# tokens arrive continuously so the read timer never trips.
_UPSTREAM_TIMEOUT = aiohttp.ClientTimeout(total=3600, sock_connect=10, sock_read=300)


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

    def close_open_block(self) -> bytes:
        """Close whatever content block the round left open. Only protocols
        with explicit block framing need this; the rest return nothing."""
        return b""


class AnthropicAdapter(ClientAdapter):
    protocol = "anthropic"

    def __init__(self, client_model: str) -> None:
        super().__init__(client_model)
        # Content-block indices are message-scoped, not round-scoped: a round
        # that ends in an intercept can still have streamed a thinking block,
        # so the counter has to survive into the next round's stream state or
        # the two would collide on the same index.
        self._next_index = 0
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
        """Status = synthetic text content block, taking the next index in
        the same sequence as real content blocks."""
        frame = xlate.emit_anthropic_status_block(text, self._next_index)
        self._next_index += 1
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
        state._next_index = self._next_index
        state._message_started = True  # initial_frame already sent it
        state._message_id = self._message_id
        self.state = state

    def translate_openai_chunk(self, chunk: dict[str, Any]) -> bytes:
        assert self.state is not None
        out = self.state.step(chunk)
        # Pull the counter back so the next round (and any status block
        # emitted between the two) continues where this one stopped.
        self._next_index = self.state._next_index
        return out

    def close_open_block(self) -> bytes:
        """Close the block this round left open.

        Needed only when a round ends in an intercept: the finishing chunk is
        consumed by the intercept path and never reaches the stream state, so
        its `content_block_stop` would otherwise never be sent.
        """
        if self.state is None:
            return b""
        return self.state._close_current()

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
    system_mode: str | None = None,
) -> dict[str, Any]:
    """Add the profile system_instruction and/or date+location to body.

    body is in INTERNAL (OpenAI) shape — we extend the first system message,
    or create one if there is none.

    `system_instruction` goes at the HEAD (it is the system prompt).
    Date/location goes at the TAIL, as plain text, for two reasons:

      * Plain text, not a <system-reminder> block. `strip_reminders` removes
        every reminder block it does not explicitly preserve, so a wrapped
        injection was deleted again a couple of steps later — the flag was a
        silent no-op for any profile with strip_reminders on.
      * At the tail, because the date rolls over once a day. Anything after
        the injection point has to be re-prefilled when it changes; putting it
        last means that is only the conversation, not the whole system prompt
        and tool listing ahead of it.
    """
    head_parts: list[str] = []
    tail_parts: list[str] = []

    system_md = (profile.get("system_instruction") if profile and "system_instruction" in profile
                 else proxy_config.system_instruction())
    if system_md:
        instruction = proxy_system_instruction(system_md)
        if instruction:
            head_parts.append(instruction)

    if inject_date_location:
        from datetime import datetime
        date_str = datetime.now().strftime("%Y-%m-%d (%A)")
        location = await _get_location()
        segs = [f"Current date: {date_str}."]
        if location:
            segs.append(f"User location: {location}.")
        tail_parts.append(" ".join(segs))

    if not head_parts and not tail_parts:
        return body

    head = "\n\n".join(head_parts)
    tail = "\n\n".join(tail_parts)

    messages = xlate._normalize_system_messages(body.get("messages", []), system_mode)
    if messages and messages[0].get("role") == "system":
        existing = messages[0].get("content", "")
        if isinstance(existing, list):
            # Re-emit as string (llama.cpp handles both but string is cheaper)
            existing = "\n".join(p.get("text", "") for p in existing
                                 if isinstance(p, dict) and p.get("type") == "text")
        elif not isinstance(existing, str):
            existing = ""
        merged = "\n\n".join(part for part in (head, existing, tail) if part)
        messages[0] = {**messages[0], "content": merged}
    else:
        merged = "\n\n".join(part for part in (head, tail) if part)
        messages = [{"role": "system", "content": merged}] + list(messages)

    body["messages"] = messages
    return body


def _apply_tool_transforms(
    body: dict[str, Any],
    profile: dict | None,
    use_tool_search: bool,
    managed_inject_names: list[str],
    sort_tools: bool = False,
    sticky: set[str] | None = None,
    defer_supported: bool = False,
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
        # Tools this conversation has already loaded stay core for its lifetime.
        # Without this the array reverts on the next request and the whole
        # prefix re-prefills a second time — see the _sticky_tools note above.
        #
        # Inert when the server supports defer_loading: everything is declared
        # already, so promoting a tool would only make it *rendered* — moving
        # the prefix, which is the very thing this whole path exists to avoid.
        # (Entries can survive from a pre-patch session, hence the guard rather
        # than trusting the intercept loop not to have written any.)
        if not defer_supported:
            core_names |= (sticky or set())

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

        # Managed tools go through the same core/deferred split as client tools.
        # If a managed tool's name is in core_names it stays core; otherwise it
        # lands in deferred so ToolSearch can load it on demand.
        for oa, anth in zip(managed_oa, inject_schemas):
            if _fn_name(oa) in core_names:
                core_tools_out.append(oa)
            else:
                deferred.append(anth)  # already Anthropic shape, ready for BM25

        # Inject ToolSearch meta-tool whenever we have deferred tools
        if deferred:
            from proxy.tool_registry import TOOL_SEARCH_TOOL
            core_tools_out.insert(0, _anth_to_openai_tool(TOOL_SEARCH_TOOL))

        if defer_supported and deferred:
            # llama.cpp carries our defer_loading patch: declare EVERY tool so
            # the sampling grammar accepts it, and let the server leave the
            # deferred schemas out of the rendered prompt. The declared set is
            # then constant for the whole conversation, so revealing a schema
            # later costs nothing — no position-0 change, no re-prefill.
            deferred_names_local = {d["name"] for d in deferred}
            all_tools = list(core_tools_out)
            for t in tools:
                if _fn_name(t) in deferred_names_local:
                    t.setdefault("function", {})["defer_loading"] = True
                    all_tools.append(t)
            for oa, anth in zip(managed_oa, inject_schemas):
                if _fn_name(oa) in deferred_names_local:
                    oa.setdefault("function", {})["defer_loading"] = True
                    all_tools.append(oa)
            tools = all_tools
        else:
            tools = core_tools_out
    else:
        tools = managed_oa + tools

    if sort_tools and tools:
        tools.sort(key=_fn_name)

    if tools:
        body["tools"] = tools
    elif "tools" in body:
        del body["tools"]

    return body, deferred


# ── Sticky loaded tools ──────────────────────────────────────────────────────
# Tools render at position 0 of the prompt, so ANY change to the array shifts
# the whole prefix and llama.cpp re-prefills the entire conversation (measured:
# 68K tokens / 51s). A ToolSearch load used to pay that twice — once on the
# retry round, when the schema is appended to body["tools"], and again on the
# NEXT request, because _apply_tool_transforms recomputes the core/deferred
# split from the static `core_tools` list and the loaded tool falls back to
# deferred, reverting the array.
#
# Remembering per conversation what has been loaded removes the second break.
# The first is unavoidable without declaring every deferred schema up front,
# which measures at ~10,400 tokens of permanent context — a worse trade.
_STICKY_MAX = 64
_sticky_tools: OrderedDict[str, set[str]] = OrderedDict()


def _conversation_key(client_body: dict[str, Any]) -> str | None:
    """Stable per-conversation id, or None when the client doesn't supply one.

    Claude Code puts a JSON blob in metadata.user_id carrying a session_id that
    stays constant for the life of the session — a far better key than hashing
    message content, which carries per-turn reminders that change every turn.
    """
    meta = client_body.get("metadata")
    raw = meta.get("user_id") if isinstance(meta, dict) else None
    if not isinstance(raw, str) or not raw:
        return None
    try:
        sid = json.loads(raw).get("session_id")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return raw[:128]  # opaque, but stable for this client
    return str(sid) if sid else None


def _sticky_get(key: str | None) -> set[str]:
    if not key:
        return set()
    names = _sticky_tools.get(key)
    if names is None:
        return set()
    _sticky_tools.move_to_end(key)
    return set(names)


def _sticky_add(key: str | None, names: set[str]) -> None:
    if not key or not names:
        return
    cur = _sticky_tools.setdefault(key, set())
    cur |= set(names)
    _sticky_tools.move_to_end(key)
    while len(_sticky_tools) > _STICKY_MAX:
        _sticky_tools.popitem(last=False)


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


def _drop_empty_turns(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove turns that the strippers above emptied out.

    A message whose text is now blank contributes nothing but chat-template
    scaffolding (`<|im_start|>user
<|im_end|>`) — tokens spent on an empty
    turn, and a shape some templates handle badly.

    Two exemptions. Tool-protocol turns are never dropped: losing an assistant
    `tool_calls` turn or its `role:"tool"` reply breaks the pairing the
    template requires. And a turn carrying non-text content (an image) is kept
    with its remaining blocks, since only the text went away.

    Dropping an emptied LEADING system message is safe and deliberate: this
    runs before `_inject_system_prompt`, which prepends a fresh system message
    when there is none to extend.
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        if (msg.get("tool_calls") or msg.get("tool_call_id")
                or msg.get("role") == "tool"):
            out.append(msg)
            continue
        content = msg.get("content")
        if isinstance(content, str):
            if content.strip():
                out.append(msg)
            continue
        if isinstance(content, list):
            kept = [b for b in content
                    if not (isinstance(b, dict) and b.get("type") == "text"
                            and not (b.get("text") or "").strip())]
            if kept:
                out.append({**msg, "content": kept})
            continue
        out.append(msg)
    # Never hand upstream an empty conversation — if everything emptied, the
    # request is malformed and the original is the more useful error.
    return out or messages


# Content blocks that can carry a remote URL, by inbound protocol. `image` is
# Anthropic's own block; `video` and `audio` are telecode extensions (the
# Messages API has neither). The OpenAI side is llama.cpp's `input_*` plus the
# `*_url` variants clients mirror from `image_url`.
#
# All three kinds are inlined here rather than handed to llama.cpp. Images used
# to be the exception — llama.cpp fetched those itself — but that is the same
# SSRF primitive as the other two: llama.cpp runs on this machine, so a URL the
# caller cannot reach is one it can, and its fetch caps at 10 MB / 10 s besides.
# Consequence worth knowing: an image URL on localhost or the LAN is now
# REFUSED where it previously worked. Drop the "image" entries to restore that.
_URL_MEDIA_ANTHROPIC = {
    "image": "image/png",
    "video": "video/mp4",
    "audio": "audio/mpeg",
}
_URL_MEDIA_OPENAI = {
    "image_url": ("image_url", "image/png"),
    "video_url": ("video_url", "video/mp4"),
    "audio_url": ("audio_url", "audio/mpeg"),
    "input_video": ("input_video", "video/mp4"),
    "input_audio": ("input_audio", "audio/mpeg"),
}


async def _inline_media_urls(body: dict[str, Any], inbound_protocol: str) -> None:
    """Replace video/audio-by-URL with inline base64, in place.

    Anthropic shape:  {"type": "audio",       "source": {"type": "url", "url": ...}}
    OpenAI shape:     {"type": "input_audio", "input_audio": {"url": ...}}
                      {"type": "audio_url",   "audio_url": {"url": ...}}

    A data: URI is left alone — translate.py decodes those — as is raw base64,
    which is what OpenAI's own `input_audio.data` carries. Only a real remote
    URL is fetched, and we fetch it rather than letting llama.cpp do it: its
    own fetch caps at 10 MB / 10 s, and it runs on this machine, so a URL the
    caller cannot reach is one llama.cpp can. media_fetch's guards apply here.

    The declared `media_type` is cosmetic — llama.cpp sniffs the container —
    so a fetched clip is labelled by kind rather than by probing the bytes.
    """
    from proxy.media_fetch import MediaFetchError, fetch_media_b64

    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = str(block.get("type"))
            url, key, mime = None, "", ""
            if inbound_protocol == "anthropic" and btype in _URL_MEDIA_ANTHROPIC:
                mime = _URL_MEDIA_ANTHROPIC[btype]
                src = block.get("source") or {}
                if src.get("type") == "url":
                    url = src.get("url", "")
            elif btype in _URL_MEDIA_OPENAI:
                key, mime = _URL_MEDIA_OPENAI[btype]
                inner = block.get(key) or {}
                if isinstance(inner, dict):
                    url = inner.get("url") or inner.get("data") or ""

            # Only an http(s) URL needs resolving. A data: URI is already
            # inline, and anything else is raw base64.
            if not url or not url.startswith(("http://", "https://")):
                continue
            try:
                data = await fetch_media_b64(url)
            except MediaFetchError as exc:
                # A refused or unreachable URL is the client's problem, and a
                # silent drop would look like the model ignoring the clip.
                raise web.HTTPBadRequest(
                    reason=f"{btype} URL rejected: {exc}") from exc

            if key:
                block[key] = {"url": f"data:{mime};base64,{data}"}
            else:
                block["source"] = {"type": "base64", "media_type": mime,
                                   "data": data}


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

    # 2. Profile-driven feature flags (resolved first — translation needs the
    #    mid-conversation system-message policy).
    def _pget(key: str, default):
        if profile and key in profile:
            return profile[key]
        return default

    system_mode = _pget("mid_system_messages", proxy_config.mid_system_messages())

    # 2b. Inline any video supplied by URL. llama.cpp's input_video takes
    #     base64 only, so a URL has to be fetched by someone — images get away
    #     with passing the URL through because llama.cpp fetches those itself.
    #     Done here rather than in translate.py because it needs to await, and
    #     translate.py is deliberately synchronous and pure.
    await _inline_media_urls(body, inbound_protocol)

    # 3. Client body → internal (OpenAI) body
    if inbound_protocol == "anthropic":
        internal = xlate.anthropic_request_to_internal(
            body, inference_defaults=inference, system_mode=system_mode)
    else:
        internal = xlate.openai_request_to_internal(
            body, inference_defaults=inference, system_mode=system_mode)

    internal["model"] = active_model

    # 3b. Always-on system-prompt cleanup — billing header, `# Environment`
    #     and the `gitStatus:` snapshot. No setting: none of the three is
    #     useful to a local model, and gitStatus in particular sits early in
    #     the prompt and changes on every commit / dirty-file edit, so it
    #     invalidates ~90% of the prefix cache (CLAUDE.md included) every time
    #     the working tree moves. Scoped to the LEADING system message, which
    #     is the only place they occur, so nothing in the conversation can be
    #     caught by it. Runs before every injection of ours.
    #     `strip_client_system_prompt` is the all-or-nothing version: drop the
    #     leading block entirely and let `system_instruction` stand in its
    #     place. Emptied here rather than removed, so `_drop_empty_turns`
    #     below does the removal and `_inject_system_prompt` prepends a fresh
    #     system message.
    _drop_client_sys = bool(_pget("strip_client_system_prompt",
                                  proxy_config.strip_client_system_prompt()))
    if _drop_client_sys and not (profile or {}).get("system_instruction"):
        log.warning(
            "strip_client_system_prompt is on for profile %r but it has no "
            "system_instruction — this request goes upstream with no system "
            "prompt at all.",
            (profile or {}).get("name", "<none>"))

    _sys_msgs = internal.get("messages", [])
    if _sys_msgs and _sys_msgs[0].get("role") == "system":
        if _drop_client_sys:
            _sys_msgs[0] = {**_sys_msgs[0], "content": ""}
        else:
            _c = _sys_msgs[0].get("content")
            if isinstance(_c, str):
                _sys_msgs[0] = {**_sys_msgs[0], "content": strip_client_system_noise(_c)}
            elif isinstance(_c, list):
                _sys_msgs[0] = {**_sys_msgs[0], "content": [
                    {**b, "text": strip_client_system_noise(b.get("text", ""))}
                    if isinstance(b, dict) and b.get("type") == "text" else b
                    for b in _c
                ]}

    # 3c. Per-turn context blocks. The agent-type roster goes unconditionally;
    #     skills and MCP instructions are toggles. These arrive in
    #     mid-conversation system messages which `strip_reminders` cannot
    #     reach, and with the default `mid_system_messages: "demote"` they are
    #     already re-roled to `user` by now — so they are identified by
    #     POSITION (anything after the leading system block), never by role.
    _strip_skills = bool(_pget("strip_skills", proxy_config.strip_skills()))
    _strip_mcp = bool(_pget("strip_mcp_instructions",
                            proxy_config.strip_mcp_instructions()))
    _msgs = internal.get("messages", [])
    _first = 1 if (_msgs and _msgs[0].get("role") == "system") else 0
    for _i in range(_first, len(_msgs)):
        _m = _msgs[_i]
        if _m.get("tool_calls") or _m.get("tool_call_id") or _m.get("role") == "tool":
            continue  # never touch tool-protocol turns
        _c = _m.get("content")
        if isinstance(_c, str):
            _msgs[_i] = {**_m, "content": strip_turn_context(
                _c, skills=_strip_skills, mcp=_strip_mcp)}
        elif isinstance(_c, list):
            _msgs[_i] = {**_m, "content": [
                {**b, "text": strip_turn_context(
                    b.get("text", ""), skills=_strip_skills, mcp=_strip_mcp)}
                if isinstance(b, dict) and b.get("type") == "text" else b
                for b in _c
            ]}

    internal["messages"] = _drop_empty_turns(internal.get("messages", []))

    use_tool_search = _pget("tool_search", proxy_config.tool_search())
    inject_date_loc = _pget("inject_date_location", proxy_config.inject_date_location())
    use_strip_reminders = _pget("strip_reminders", proxy_config.strip_reminders())
    use_auto_load = _pget("auto_load_tools", proxy_config.auto_load_tools())
    use_sort_tools = _pget("sort_tools", proxy_config.sort_tools())

    # 4. System-prompt injection
    internal = await _inject_system_prompt(internal, profile, inject_date_loc, system_mode)

    # 5. Tool transforms (split into core/deferred, inject managed)
    from proxy.managed_tools import _REGISTRY as _MGR
    from proxy.runtime_state import is_managed_enabled as _is_enabled
    # Profile > global > whole registry. The global returns None (not []) when
    # unset, so "inject nothing" stays expressible as an explicit empty list.
    _mi_global = proxy_config.inject_managed()
    managed_inject_raw: list[str] = (
        profile.get("inject_managed") if profile and "inject_managed" in profile
        else (_mi_global if _mi_global is not None else list(_MGR.keys()))
    ) or []
    # Honor live runtime toggles set via the control panel
    managed_inject: list[str] = [n for n in managed_inject_raw if _is_enabled(n)]

    conv_key = _conversation_key(body)
    # Cached after the first call per (server, model); any failure answers
    # False, which just means we keep doing the split ourselves.
    defer_supported = False
    if use_tool_search:
        from proxy import llama_caps
        defer_supported = await llama_caps.supports_defer_loading(active_model)
    internal, deferred = _apply_tool_transforms(
        internal, profile, use_tool_search, managed_inject,
        sort_tools=use_sort_tools,
        sticky=_sticky_get(conv_key),
        defer_supported=defer_supported,
    )

    # 6. Inject deferred-listing reminder into first user message
    if deferred:
        internal = _inject_deferred_reminder(internal, deferred)

    # 7. Strip reminders (after our own injection, so keep ours).
    #    `keep_claude_md` is the exclusion: the CLAUDE.md block lives inside
    #    the reminder, so stripping would take it regardless. Any value >= 0
    #    is honoured in both modes — as an exclusion here, as a plain limit
    #    below.
    try:
        keep_md = int(_pget("keep_claude_md", proxy_config.keep_claude_md()))
    except (TypeError, ValueError):
        keep_md = -1
    try:
        keep_mem = int(_pget("keep_memory", proxy_config.keep_memory()))
    except (TypeError, ValueError):
        keep_mem = -1
    try:
        keep_rules = int(_pget("keep_rules", proxy_config.keep_rules()))
    except (TypeError, ValueError):
        keep_rules = -1
    if use_strip_reminders:
        internal["messages"] = _strip_reminders_from_internal(
            internal.get("messages", []), keep_md, keep_mem, keep_rules)
    elif keep_md >= 0 or keep_mem >= 0 or keep_rules >= 0:
        internal["messages"] = limit_claude_md(
            internal.get("messages", []), keep_md, keep_mem, keep_rules)

    # 8. Context overflow — last, so it sees the final prepared body.
    internal = await _apply_context_overflow(
        internal, active_model,
        str(_pget("context_overflow", inference.get("context_overflow", "error")) or "error"),
    )

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
        "conv_key": conv_key,
        "defer_supported": defer_supported,
    }


# ── Context overflow ─────────────────────────────────────────────────────
#
# llama.cpp has NO server-side prompt truncation. `--context-shift` covers a
# different case entirely (generation running past the context, mid-stream) and
# is disabled outright for many models. An oversized *prompt* is rejected at
# admission with `exceed_context_size_error`, deliberately, so the client picks
# what history to lose. That client is us.

_MIN_TAIL_MESSAGES = 2      # always keep the final exchange intact
_OVERFLOW_SAFETY = 64       # slack for template scaffolding we can't predict


def _is_tool_protocol(msg: dict[str, Any]) -> bool:
    return bool(msg.get("tool_calls") or msg.get("tool_call_id")
                or msg.get("role") == "tool")


def _drop_indices(messages: list[dict[str, Any]], policy: str) -> list[int]:
    """Indices eligible for dropping, in the order we should drop them.

    Never the leading system block, and never the last `_MIN_TAIL_MESSAGES`
    (the current question would be lost). Order encodes the policy:

      truncate_middle  outward from the centre — keeps both the oldest turns
                       (which the model treats as standing context) and the
                       most recent ones. Also the only cache-friendly choice:
                       the head stays byte-identical, so llama.cpp's prefix
                       cache survives everything before the cut.
      truncate_left    oldest first
      truncate_right   newest-eligible first
    """
    first = 1 if (messages and messages[0].get("role") == "system") else 0
    last = max(first, len(messages) - _MIN_TAIL_MESSAGES)
    eligible = list(range(first, last))
    if not eligible:
        return []
    if policy == "truncate_left":
        return eligible
    if policy == "truncate_right":
        return list(reversed(eligible))
    mid = len(eligible) // 2
    out: list[int] = []
    for off in range(len(eligible)):
        lo, hi = mid - off - 1, mid + off
        if hi < len(eligible):
            out.append(eligible[hi])
        if lo >= 0:
            out.append(eligible[lo])
    return out


async def _apply_context_overflow(
    internal: dict[str, Any],
    active_model: str,
    policy: str,
) -> dict[str, Any]:
    """Trim the prepared body so it fits the model's context window.

    Implements `llamacpp.inference.context_overflow`. Returns the body
    unchanged when it already fits, when the policy is "error" (let llama.cpp
    reject it, which is the pre-existing behaviour), or when we cannot
    establish a budget.

    Whole messages are dropped, never fragments: a partial message would
    orphan a `tool_calls` from its `role:"tool"` result and break the chat
    template. Tool-protocol messages are dropped together with the assistant
    turn that owns them.
    """
    if policy not in ("truncate_middle", "truncate_left", "truncate_right"):
        return internal

    ctx = int((llama_cfg.model_cfg(active_model) or {}).get("ctx_size") or 0)
    if ctx <= 0:
        return internal

    reserve = internal.get("max_tokens")
    try:
        reserve = int(reserve)
    except (TypeError, ValueError):
        reserve = 0
    budget = ctx - max(reserve, 0) - _OVERFLOW_SAFETY
    if budget <= 0:
        budget = ctx - _OVERFLOW_SAFETY

    messages = list(internal.get("messages") or [])
    if not messages:
        return internal

    try:
        used = await toks.count_tokens(messages)
    except Exception as exc:
        log.warning("context_overflow: token count failed (%s) — forwarding as-is", exc)
        return internal
    if used <= 0 or used <= budget:
        return internal

    order = _drop_indices(messages, policy)
    dropped: set[int] = set()
    for idx in order:
        if used <= budget:
            break
        if idx in dropped:
            continue
        # Drop the message plus any tool-protocol run bound to it, so a
        # tool_calls turn never loses its results (or vice versa).
        group = {idx}
        if messages[idx].get("tool_calls"):
            j = idx + 1
            while j < len(messages) and messages[j].get("role") == "tool":
                group.add(j)
                j += 1
        elif messages[idx].get("role") == "tool":
            j = idx - 1
            while j >= 0 and messages[j].get("role") == "tool":
                j -= 1
            if j >= 0 and messages[j].get("tool_calls"):
                group.add(j)
                k = j + 1
                while k < len(messages) and messages[k].get("role") == "tool":
                    group.add(k)
                    k += 1
        dropped |= group
        kept = [m for i, m in enumerate(messages) if i not in dropped]
        try:
            used = await toks.count_tokens(kept)
        except Exception as exc:
            log.warning("context_overflow: token count failed mid-trim (%s)", exc)
            return internal

    if not dropped:
        return internal

    kept = [m for i, m in enumerate(messages) if i not in dropped]
    log.warning(
        "context_overflow(%s): dropped %d/%d messages to fit %s "
        "(ctx=%d reserve=%d budget=%d, now %d tokens)",
        policy, len(dropped), len(messages), active_model,
        ctx, reserve, budget, used,
    )
    internal["messages"] = kept
    return internal


def _strip_reminders_from_internal(messages: list[dict[str, Any]],
                                   keep_claude_md: int = -1,
                                   keep_memory: int = -1,
                                   keep_rules: int = -1) -> list[dict[str, Any]]:
    """Re-use the Anthropic-shape reminder stripper on the internal history.

    The content we care about is just the text inside messages — role labels
    are irrelevant, and `strip_all_reminders` already carries every other key
    through via `{**msg, ...}`.

    Stripped one message at a time on purpose: the stripper DROPS messages
    whose text empties out (common now that `<total_tokens>` bookkeeping is
    stripped — those messages are nothing else), and a batch call would then
    return a shorter list that no longer lines up index-for-index with the
    input. Tool-protocol messages are never dropped: losing an assistant
    `tool_calls` turn or its `role:"tool"` result would break the pairing the
    chat template requires, so they survive with empty content instead.
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        is_protocol = bool(
            msg.get("tool_calls")
            or msg.get("tool_call_id")
            or msg.get("role") == "tool"
        )
        cleaned = strip_all_reminders([msg], keep_claude_md, keep_memory,
                                      keep_rules)
        if cleaned:
            out.append(cleaned[0])
        elif is_protocol:
            out.append({**msg, "content": ""})
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


def _toolsearch_select_guidance(
    query: str,
    matched: list[dict[str, Any]],
    available_names: set[str],
) -> str | None:
    """For `select:` queries, rescue the model when it asks to load tools that
    are ALREADY available (core/visible) — a common stall for small models that
    ToolSearch a tool they could just call. Returns a guidance string, or None
    to fall back to the default functions-block formatting.

    Only fires when at least one requested name is already callable; genuinely
    unknown selects still get the plain 'No matching tools' message.
    """
    if not query.startswith("select:"):
        return None
    requested = [n.strip() for n in query[len("select:"):].split(",") if n.strip()]
    if not requested:
        return None
    matched_names = {m.get("name", "") for m in matched}
    already = [n for n in requested if n in available_names and n not in matched_names]
    if not already:
        return None

    parts: list[str] = []
    if matched:
        parts.append(_format_functions_block(matched))
    lst = ", ".join(f"`{n}`" for n in already)
    is_one = len(already) == 1
    parts.append(
        f"NOTE: {lst} {'is' if is_one else 'are'} ALREADY available in this "
        f"conversation — you do NOT need ToolSearch for {'it' if is_one else 'them'}. "
        f"Call {'it' if is_one else 'them'} directly by name."
    )
    return "\n\n".join(parts)


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

    # Thinking is NOT a decision signal. llama.cpp's `--reasoning-format none`
    # inlines <think>…</think> into `delta.content`, so a rule of "any content
    # ⇒ passthrough" fires on the model's first THOUGHT token — before any
    # tool call exists — and every call in that turn then goes straight to the
    # client, ToolSearch and the managed tools included, which the client has
    # never heard of. This mirror of the adapter's own ReasoningState tells
    # real output apart from thinking; it is a separate instance so the two
    # tag buffers cannot disturb each other. (`reasoning_content` needs no
    # such care — it is thinking by construction and never reaches here.)
    decide_think = xlate.ReasoningState(
        start_tag=reasoning_cfg.get("start", "<think>"),
        end_tag=reasoning_cfg.get("end", "</think>"),
        emit_thinking=True,
        enabled=reasoning_cfg.get("enabled", True),
    )

    # Mark this round as in-flight so the supervisor's idle-unload watcher
    # never tears down llama-server mid-stream. We end-request just before
    # each return path below (there are 4: 502 from upstream, [DONE],
    # captured-tool-call, stream-without-DONE).
    supervisor = await get_supervisor()
    await supervisor.begin_request()

    async with aiohttp.ClientSession(timeout=_UPSTREAM_TIMEOUT) as session:
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
                            elif choices[0].get("finish_reason"):
                                decided = "passthrough"
                                just_decided = True
                            elif content:
                                # Only NON-BLANK text outside a think block
                                # settles the round. llama.cpp emits a bare
                                # "\n\n" between </think> and the first
                                # tool_call delta; counting that as an answer
                                # committed the round to passthrough, so every
                                # tool call after it escaped to the client
                                # ("No such tool available: ToolSearch").
                                # Whitespace is not an answer — keep watching.
                                if any(k == "text" and t.strip()
                                       for k, t in decide_think.push(content)):
                                    decided = "passthrough"
                                    just_decided = True

                    # ── Post-decision handling ──────────────────────────
                    # If decision flipped THIS event, we already consumed the
                    # tool-call fragment in the pre-decision branch; don't
                    # re-append name/arguments below.
                    if decided is None:
                        # Still undecided ⇒ what we have so far is thinking.
                        # Write it now rather than buffering: on a long think
                        # it is the only thing the user has to look at, and it
                        # belongs to the message whichever way the round goes.
                        # If this turns out to be an intercept, the block is
                        # closed below and the next round appends to the same
                        # message.
                        #
                        # Except a tool-call fragment: undecided here means the
                        # name is still arriving in pieces, and a tool_use block
                        # opened on half a name cannot be taken back.
                        _d = (event.get("choices") or [{}])[0].get("delta", {}) or {}
                        if not _d.get("tool_calls"):
                            async with write_lock:
                                await _ensure_prepared(resp, request)
                                await resp.write(adapter.translate_openai_chunk(event))
                    elif decided == "passthrough":
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
                                # This chunk never reaches the stream state, so
                                # any thinking block opened above would be left
                                # without its content_block_stop.
                                tail = adapter.close_open_block()
                                if tail:
                                    async with write_lock:
                                        await _ensure_prepared(resp, request)
                                        await resp.write(tail)
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
                q = str(tool_input.get("query", ""))
                guidance = _toolsearch_select_guidance(q, matched, core_visible_names)
                result_content = guidance or _format_functions_block(matched)
                if matched:
                    names = ", ".join(m.get("name", "") for m in matched[:5])
                    status_line = f'● ToolSearch("{q[:80]}")\n└  {len(matched)} schemas loaded: {names}'
                elif guidance:
                    status_line = f'● ToolSearch("{q[:80]}")\n└  Already available · told to call directly'
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

            # Make the loaded tools callable from here on.
            #
            # With defer_loading support the tool is ALREADY declared — it was
            # simply not rendered — so there is nothing to append and the prefix
            # never moves. We only stop intercepting it; the model gets the
            # schema from the tool_result, which lands at the tail.
            #
            # Without it we must append to body["tools"], which shifts position 0
            # and costs one full re-prefill. _sticky_add at least keeps that to
            # once per tool per conversation rather than again on the next
            # request, when the core/deferred split is recomputed.
            if matched:
                names = {m["name"] for m in matched}
                if not prep.get("defer_supported"):
                    body.setdefault("tools", []).extend(
                        _anth_tool_to_openai_tool(m) for m in matched)
                    _sticky_add(prep.get("conv_key"), names)
                core_visible_names |= names

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
        async with aiohttp.ClientSession(timeout=_UPSTREAM_TIMEOUT) as session:
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
                q = str(tool_input.get("query", ""))
                guidance = _toolsearch_select_guidance(q, matched, core_visible_names)
                result_text = guidance or _format_functions_block(matched)
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

def _header_log_path() -> Path:
    """Resolve data/logs/request_headers.log next to telecode.log."""
    import os
    try:
        from config import _settings_dir  # type: ignore[attr-defined]
        base = _settings_dir()
    except Exception:
        base = Path(os.getcwd())
    d = base / "data" / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d / "request_headers.log"


# Headers worth redacting so we never write credentials to disk.
_REDACT_HEADERS = {"authorization", "x-api-key"}


@web.middleware
async def header_log_middleware(request: web.Request, handler):
    """Diagnostic: append every request's method/path/headers as one JSON line
    to data/logs/request_headers.log. Captures the full `anthropic-beta` set
    (and the `?beta=true` query) that the body dumps in request_log don't show.
    Secrets are redacted. Best-effort — never blocks the request on failure."""
    try:
        hdrs = {}
        for k, v in request.headers.items():
            lk = k.lower()
            hdrs[lk] = "<redacted>" if lk in _REDACT_HEADERS else v
        rec = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "method": request.method,
            "path": request.path_qs,  # includes ?beta=true
            "headers": hdrs,
        }
        with open(_header_log_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return await handler(request)


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
    app = web.Application(middlewares=[header_log_middleware, cors_middleware])

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
    api_runs.register_routes(app)
    api_routines.register_routes(app)

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

    # Start the routine heartbeat thread so saved routines fire on their interval.
    try:
        from services.routine import routine_manager
        routine_manager.start()
    except Exception:
        log.exception("routine_manager: failed to start")

    return runner
