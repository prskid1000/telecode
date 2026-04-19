"""End-to-end test harness for the llama.cpp-backed dual-protocol proxy.

Run directly (not via pytest — it spawns llama-server which takes time and
we want verbose progress output):

    python tests/test_e2e_proxy.py            # full matrix (~1-2 min)
    python tests/test_e2e_proxy.py --quick    # skip vision cases (~30s)

What it covers:
    Routing
      - GET /v1/models in both shapes (header sniff)
      - response model reverse-mapped to client alias

    Text
      - Anthropic and OpenAI, streaming and non-streaming

    Token counting
      - /v1/messages/count_tokens via llama.cpp /tokenize

    Thinking / reasoning effort
      - thinking.type=enabled with budget_tokens bucket → system nudge
      - thinking.display=omitted → ReasoningState strips <think> blocks
      - /no_think path exercised indirectly by thinking.type=disabled
        mapping to chat_template_kwargs.enable_thinking=false

    Tool calls
      - Anthropic streaming tool_use
      - OpenAI streaming tool_calls
      - Anthropic non-stream roundtrip (assistant tool_use → user tool_result → final text)

    Intercept loop
      - claude-code profile: ToolSearch + auto_load, deferred tool loaded
        on blind call, model retries with schema, tool_use emitted

    Vision (multimodal)
      - Anthropic image block in user message
      - OpenAI image_url in user message
      - Anthropic tool_result containing an image (lifted to follow-on user message)

Requires a matching `e2e-test` profile in settings.json so managed tools
aren't auto-injected; see settings.example.json.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import sys
import time
import traceback
from typing import Any

import aiohttp
from PIL import Image, ImageDraw

# Make the project root importable when this file is run directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp import web
from llamacpp.supervisor import get_supervisor, shutdown_supervisor
from llamacpp import config as llama_cfg
from proxy.server import create_app
from proxy import config as proxy_config

log = logging.getLogger("e2e")
PROXY_URL = f"http://127.0.0.1:{proxy_config.proxy_port()}"
ACTIVE_MODEL = "claude-opus-4-6"  # goes through proxy.model_mapping


def _make_png(w: int = 128, h: int = 128, color=(220, 30, 30), label: str = "RED") -> str:
    """Return base64-encoded PNG. Minimum 8192 pixels for Qwen-VL."""
    img = Image.new("RGB", (w, h), color)
    d = ImageDraw.Draw(img)
    d.text((max(5, w // 8), max(5, h // 3)), label, fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ══════════════════════════════════════════════════════════════════════
# Harness
# ══════════════════════════════════════════════════════════════════════

class Results:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def ok(self, name: str) -> None:
        self.passed.append(name)
        print(f"  PASS  {name}")

    def fail(self, name: str, reason: str) -> None:
        self.failed.append((name, reason))
        print(f"  FAIL  {name}: {reason[:300]}")

    def summary(self) -> int:
        print("\n" + "=" * 70)
        print(f"PASS: {len(self.passed)}  |  FAIL: {len(self.failed)}")
        if self.failed:
            print("\nFailures:")
            for name, reason in self.failed:
                print(f"  - {name}")
                print(f"      {reason[:500]}")
        print("=" * 70)
        return 0 if not self.failed else 1


RES = Results()


async def _post_json(session: aiohttp.ClientSession, path: str, body: dict,
                     headers: dict | None = None) -> tuple[int, Any]:
    async with session.post(f"{PROXY_URL}{path}", json=body, headers=headers or {}) as r:
        ct = r.headers.get("Content-Type", "")
        if "json" in ct:
            return r.status, await r.json()
        return r.status, await r.text()


async def _stream_events(session: aiohttp.ClientSession, path: str, body: dict,
                         headers: dict | None = None, max_seconds: float = 180) -> dict:
    """Collect all SSE events. Returns {events, raw, status}."""
    deadline = time.time() + max_seconds
    events: list[dict] = []
    raw_chunks: list[str] = []
    async with session.post(f"{PROXY_URL}{path}", json=body, headers=headers or {}) as r:
        status = r.status
        if status != 200:
            body_text = await r.text()
            return {"status": status, "events": [], "raw": body_text}
        buf = ""
        async for chunk in r.content.iter_any():
            if time.time() > deadline:
                break
            text = chunk.decode("utf-8", errors="replace")
            raw_chunks.append(text)
            buf += text
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                data = None
                for line in block.split("\n"):
                    if line.startswith("data: "):
                        data = line[6:]
                        break
                if data is None or data.strip() == "[DONE]":
                    continue
                try:
                    events.append(json.loads(data))
                except json.JSONDecodeError:
                    pass
    return {"status": status, "events": events, "raw": "".join(raw_chunks)}


# ── Response extractors ────────────────────────────────────────────────

def _anth_text(resp: dict) -> str:
    return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")


def _oa_text(resp: dict) -> str:
    try:
        return resp["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError):
        return ""


def _anth_stream_reduce(events: list[dict]) -> dict:
    """Collapse Anthropic SSE to {text, thinking, tool_uses, stop_reason}."""
    text_parts: list[str] = []
    think_parts: list[str] = []
    tool_uses: list[dict] = []
    current_tool: dict | None = None
    stop_reason = None
    block_types: dict[int, str] = {}
    for ev in events:
        t = ev.get("type")
        if t == "content_block_start":
            idx = ev.get("index", 0)
            cb = ev.get("content_block", {})
            block_types[idx] = cb.get("type", "")
            if cb.get("type") == "tool_use":
                current_tool = {"id": cb.get("id"), "name": cb.get("name"), "input": ""}
        elif t == "content_block_delta":
            d = ev.get("delta", {})
            dt = d.get("type")
            if dt == "text_delta":
                text_parts.append(d.get("text", ""))
            elif dt == "thinking_delta":
                think_parts.append(d.get("thinking", ""))
            elif dt == "input_json_delta":
                if current_tool is not None:
                    current_tool["input"] += d.get("partial_json", "")
        elif t == "content_block_stop":
            idx = ev.get("index", 0)
            if block_types.get(idx) == "tool_use" and current_tool is not None:
                try:
                    current_tool["input"] = json.loads(current_tool["input"] or "{}")
                except json.JSONDecodeError:
                    pass
                tool_uses.append(current_tool)
                current_tool = None
        elif t == "message_delta":
            stop_reason = ev.get("delta", {}).get("stop_reason") or stop_reason
    return {
        "text": "".join(text_parts),
        "thinking": "".join(think_parts),
        "tool_uses": tool_uses,
        "stop_reason": stop_reason,
    }


def _oa_stream_reduce(chunks: list[dict]) -> dict:
    text_parts: list[str] = []
    tool_calls: dict[int, dict] = {}
    finish = None
    for c in chunks:
        ch = (c.get("choices") or [{}])[0]
        d = ch.get("delta", {}) or {}
        if d.get("content"):
            text_parts.append(d["content"])
        for tc in d.get("tool_calls", []) or []:
            idx = tc.get("index", 0)
            e = tool_calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            if tc.get("id"):
                e["id"] = tc["id"]
            fn = tc.get("function", {}) or {}
            e["name"] += fn.get("name", "") or ""
            e["arguments"] += fn.get("arguments", "") or ""
        if ch.get("finish_reason"):
            finish = ch["finish_reason"]
    return {
        "text": "".join(text_parts),
        "tool_calls": list(tool_calls.values()),
        "finish_reason": finish,
    }


# ══════════════════════════════════════════════════════════════════════
# Test cases
# ══════════════════════════════════════════════════════════════════════

WEATHER_TOOL_ANTH = {
    "name": "get_weather",
    "description": "Get the current weather for a city.",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name"}},
        "required": ["city"],
    },
}

WEATHER_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "City name"}},
            "required": ["city"],
        },
    },
}


async def t_models_anthropic(session: aiohttp.ClientSession) -> None:
    name = "GET /v1/models (anthropic shape)"
    async with session.get(f"{PROXY_URL}/v1/models",
                           headers={"anthropic-version": "2023-06-01"}) as r:
        if r.status != 200:
            RES.fail(name, f"status {r.status}"); return
        body = await r.json()
    if not isinstance(body.get("data"), list) or not body["data"]:
        RES.fail(name, f"empty data: {body}"); return
    first = body["data"][0]
    if first.get("type") != "model" or "display_name" not in first:
        RES.fail(name, f"wrong shape: {first}"); return
    RES.ok(name)


async def t_models_openai(session: aiohttp.ClientSession) -> None:
    name = "GET /v1/models (openai shape)"
    async with session.get(f"{PROXY_URL}/v1/models") as r:
        if r.status != 200:
            RES.fail(name, f"status {r.status}"); return
        body = await r.json()
    if body.get("object") != "list" or not body.get("data"):
        RES.fail(name, f"wrong shape: {body}"); return
    RES.ok(name)


async def t_count_tokens(session: aiohttp.ClientSession) -> None:
    name = "POST /v1/messages/count_tokens"
    status, data = await _post_json(session, "/v1/messages/count_tokens", {
        "model": ACTIVE_MODEL,
        "messages": [{"role": "user", "content": "Count my tokens, please."}],
    })
    if status != 200:
        RES.fail(name, f"status {status}: {data}"); return
    if not isinstance(data.get("input_tokens"), int) or data["input_tokens"] <= 0:
        RES.fail(name, f"no/invalid token count: {data}"); return
    RES.ok(name + f"  ({data['input_tokens']} toks)")


async def t_anth_text_nonstream(session: aiohttp.ClientSession) -> None:
    name = "anthropic text (non-stream)"
    status, data = await _post_json(session, "/v1/messages", {
        "model": ACTIVE_MODEL,
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
        "thinking": {"type": "disabled"},
    })
    if status != 200:
        RES.fail(name, f"status {status}: {data}"); return
    if data.get("type") != "message":
        RES.fail(name, f"wrong type: {data}"); return
    if not _anth_text(data).strip():
        RES.fail(name, f"empty text: {data}"); return
    RES.ok(name + f"  (got {_anth_text(data)!r})")


async def t_anth_text_stream(session: aiohttp.ClientSession) -> None:
    name = "anthropic text (streaming)"
    result = await _stream_events(session, "/v1/messages", {
        "model": ACTIVE_MODEL,
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
        "thinking": {"type": "disabled"},
        "stream": True,
    })
    if result["status"] != 200:
        RES.fail(name, f"status {result['status']}: {result['raw'][:300]}"); return
    reduced = _anth_stream_reduce(result["events"])
    if not reduced["text"].strip():
        RES.fail(name, f"empty text; events={len(result['events'])} raw={result['raw'][:300]}"); return
    if reduced["stop_reason"] is None:
        RES.fail(name, "no stop_reason emitted"); return
    RES.ok(name + f"  ({reduced['text'][:40]!r}, stop={reduced['stop_reason']})")


async def t_openai_text_nonstream(session: aiohttp.ClientSession) -> None:
    name = "openai text (non-stream)"
    status, data = await _post_json(session, "/v1/chat/completions", {
        "model": ACTIVE_MODEL,
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
        "reasoning_effort": "minimal",
    })
    if status != 200:
        RES.fail(name, f"status {status}: {data}"); return
    if not _oa_text(data).strip():
        RES.fail(name, f"empty text: {data}"); return
    RES.ok(name + f"  ({_oa_text(data)[:40]!r})")


async def t_openai_text_stream(session: aiohttp.ClientSession) -> None:
    name = "openai text (streaming)"
    result = await _stream_events(session, "/v1/chat/completions", {
        "model": ACTIVE_MODEL,
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
        "reasoning_effort": "minimal",
        "stream": True,
    })
    if result["status"] != 200:
        RES.fail(name, f"status {result['status']}: {result['raw'][:300]}"); return
    reduced = _oa_stream_reduce(result["events"])
    if not reduced["text"].strip():
        RES.fail(name, f"empty text; raw={result['raw'][:300]}"); return
    RES.ok(name + f"  ({reduced['text'][:40]!r}, finish={reduced['finish_reason']})")


async def t_anth_thinking(session: aiohttp.ClientSession) -> None:
    name = "anthropic thinking (stream, emit_thinking_blocks=true)"
    result = await _stream_events(session, "/v1/messages", {
        "model": ACTIVE_MODEL,
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": "Reply with just the digit: what is 2+3?"}],
        "thinking": {"type": "enabled", "budget_tokens": 3000},
        "stream": True,
    })
    if result["status"] != 200:
        RES.fail(name, f"status {result['status']}: {result['raw'][:300]}"); return
    reduced = _anth_stream_reduce(result["events"])
    if "<think>" in reduced["text"] or "</think>" in reduced["text"]:
        RES.fail(name, f"think tags leaked into text: {reduced['text'][:200]}"); return
    if not reduced["text"].strip():
        RES.fail(name, f"empty text: {reduced}"); return
    RES.ok(name + f"  (text={len(reduced['text'])}c, think={len(reduced['thinking'])}c)")


async def t_anth_thinking_omitted(session: aiohttp.ClientSession) -> None:
    name = "anthropic thinking display=omitted (no thinking blocks)"
    result = await _stream_events(session, "/v1/messages", {
        "model": ACTIVE_MODEL,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": "What is 5+7? Explain briefly."}],
        "thinking": {"type": "enabled", "budget_tokens": 5000, "display": "omitted"},
        "stream": True,
    })
    if result["status"] != 200:
        RES.fail(name, f"status {result['status']}: {result['raw'][:300]}"); return
    reduced = _anth_stream_reduce(result["events"])
    if reduced["thinking"]:
        RES.fail(name, f"thinking blocks leaked despite display=omitted: {reduced['thinking'][:120]}"); return
    if not reduced["text"].strip():
        RES.fail(name, f"empty text: {reduced}"); return
    RES.ok(name + f"  (text={len(reduced['text'])}c, thinking suppressed)")


async def t_anth_tool_call_stream(session: aiohttp.ClientSession) -> None:
    name = "anthropic tool_call (streaming)"
    result = await _stream_events(session, "/v1/messages", {
        "model": ACTIVE_MODEL,
        "max_tokens": 256,
        "messages": [{"role": "user", "content": "Use the get_weather tool for city=Paris."}],
        "tools": [WEATHER_TOOL_ANTH],
        "thinking": {"type": "disabled"},
        "stream": True,
    })
    if result["status"] != 200:
        RES.fail(name, f"status {result['status']}: {result['raw'][:300]}"); return
    reduced = _anth_stream_reduce(result["events"])
    if not reduced["tool_uses"]:
        RES.fail(name, f"no tool_use emitted; text={reduced['text'][:200]}"); return
    tu = reduced["tool_uses"][0]
    if tu["name"] != "get_weather":
        RES.fail(name, f"wrong tool: {tu}"); return
    inp = tu["input"] if isinstance(tu["input"], dict) else {}
    if "paris" not in str(inp.get("city", "")).lower():
        RES.fail(name, f"wrong city: {tu}"); return
    if reduced["stop_reason"] != "tool_use":
        RES.fail(name, f"stop_reason={reduced['stop_reason']}"); return
    RES.ok(name + f"  ({tu['name']}({inp}))")


async def t_openai_tool_call_stream(session: aiohttp.ClientSession) -> None:
    name = "openai tool_call (streaming)"
    result = await _stream_events(session, "/v1/chat/completions", {
        "model": ACTIVE_MODEL,
        "max_tokens": 256,
        "messages": [{"role": "user", "content": "Use the get_weather tool for city=Tokyo."}],
        "tools": [WEATHER_TOOL_OPENAI],
        "reasoning_effort": "minimal",
        "stream": True,
    })
    if result["status"] != 200:
        RES.fail(name, f"status {result['status']}: {result['raw'][:300]}"); return
    reduced = _oa_stream_reduce(result["events"])
    if not reduced["tool_calls"]:
        RES.fail(name, f"no tool_calls emitted; text={reduced['text'][:200]}"); return
    tc = reduced["tool_calls"][0]
    if tc["name"] != "get_weather":
        RES.fail(name, f"wrong tool: {tc}"); return
    try:
        args = json.loads(tc["arguments"] or "{}")
    except json.JSONDecodeError:
        args = {}
    if "tokyo" not in str(args.get("city", "")).lower():
        RES.fail(name, f"wrong args: {tc}"); return
    RES.ok(name + f"  ({tc['name']}({args}))")


async def t_anth_tool_roundtrip(session: aiohttp.ClientSession) -> None:
    name = "anthropic tool roundtrip (non-stream)"
    body = {
        "model": ACTIVE_MODEL,
        "max_tokens": 512,
        "messages": [
            {"role": "user", "content": "What's the weather in Berlin?"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"city": "Berlin"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "Berlin: 15C, sunny."},
            ]},
        ],
        "tools": [WEATHER_TOOL_ANTH],
        "thinking": {"type": "disabled"},
    }
    status, data = await _post_json(session, "/v1/messages", body)
    if status != 200:
        RES.fail(name, f"status {status}: {data}"); return
    text = _anth_text(data)
    if not text.strip():
        RES.fail(name, f"empty text: {data}"); return
    if "15" not in text and "berlin" not in text.lower():
        RES.fail(name, f"response doesn't mention weather data: {text[:200]}"); return
    RES.ok(name + f"  ({text[:60]!r})")


async def t_tool_search_via_profile(session: aiohttp.ClientSession) -> None:
    name = "claude-code profile: ToolSearch + auto_load roundtrip"
    tools = [
        {"name": "Bash", "description": "Run a bash command.",
         "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}},
        {"name": "get_weather", "description": "Get weather for a city.",
         "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}},
        {"name": "get_stock_price", "description": "Get stock price by ticker.",
         "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
    ]
    result = await _stream_events(session, "/v1/messages", {
        "model": ACTIVE_MODEL,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": "Use the get_weather tool for city=Oslo. Do not use Bash."}],
        "tools": tools,
        "thinking": {"type": "disabled"},
        "stream": True,
    }, headers={"User-Agent": "claude-cli/1.0", "anthropic-version": "2023-06-01"})
    if result["status"] != 200:
        RES.fail(name, f"status {result['status']}: {result['raw'][:300]}"); return
    reduced = _anth_stream_reduce(result["events"])
    raw_text = reduced["text"]
    if "ToolSearch" not in raw_text and "Loaded" not in raw_text:
        RES.fail(name, f"no intercept status line visible; text={raw_text[:300]}"); return
    if not reduced["tool_uses"]:
        RES.fail(name, f"no tool_use emitted after auto_load; text={raw_text[:300]}"); return
    got = reduced["tool_uses"][-1]["name"]
    if got != "get_weather":
        RES.fail(name, f"final tool_use was {got}, not get_weather"); return
    RES.ok(name + f"  (status lines + {got} call)")


async def t_anth_vision_text(session: aiohttp.ClientSession) -> None:
    name = "anthropic vision (image in user message)"
    body = {
        "model": ACTIVE_MODEL,
        "max_tokens": 128,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _make_png()}},
                {"type": "text", "text": "In one word, what color is this tiny image?"},
            ],
        }],
        "thinking": {"type": "disabled"},
    }
    status, data = await _post_json(session, "/v1/messages", body)
    if status != 200:
        RES.fail(name, f"status {status}: {str(data)[:300]}"); return
    text = _anth_text(data)
    if not text.strip():
        RES.fail(name, f"empty text: {data}"); return
    RES.ok(name + f"  (got {text[:80]!r})")


async def t_anth_tool_result_with_image(session: aiohttp.ClientSession) -> None:
    name = "anthropic tool_result with image"
    body = {
        "model": ACTIVE_MODEL,
        "max_tokens": 128,
        "messages": [
            {"role": "user", "content": "Take a screenshot."},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "call_1", "name": "screenshot", "input": {}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": [
                    {"type": "text", "text": "Screenshot captured."},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _make_png()}},
                ]},
            ]},
        ],
        "tools": [{
            "name": "screenshot",
            "description": "Take a screenshot.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        }],
        "thinking": {"type": "disabled"},
    }
    status, data = await _post_json(session, "/v1/messages", body)
    if status != 200:
        RES.fail(name, f"status {status}: {str(data)[:300]}"); return
    text = _anth_text(data)
    if not text.strip():
        RES.fail(name, f"empty text: {data}"); return
    RES.ok(name + f"  ({text[:80]!r})")


async def t_openai_vision(session: aiohttp.ClientSession) -> None:
    name = "openai vision (image_url)"
    body = {
        "model": ACTIVE_MODEL,
        "max_tokens": 128,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "In one word, what color is this tiny image?"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_make_png()}"}},
            ],
        }],
        "reasoning_effort": "minimal",
    }
    status, data = await _post_json(session, "/v1/chat/completions", body)
    if status != 200:
        RES.fail(name, f"status {status}: {str(data)[:300]}"); return
    text = _oa_text(data)
    if not text.strip():
        RES.fail(name, f"empty text: {data}"); return
    RES.ok(name + f"  (got {text[:80]!r})")


async def t_anth_model_alias(session: aiohttp.ClientSession) -> None:
    name = "anthropic: response model field reverse-mapped"
    body = {
        "model": ACTIVE_MODEL,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": {"type": "disabled"},
    }
    status, data = await _post_json(session, "/v1/messages", body)
    if status != 200:
        RES.fail(name, f"status {status}"); return
    got = data.get("model", "")
    if got != ACTIVE_MODEL:
        RES.fail(name, f"got model={got!r}, expected {ACTIVE_MODEL!r}"); return
    RES.ok(name + f"  (model={got})")


# ══════════════════════════════════════════════════════════════════════
# Tray-UI action tests (settings patch, add/remove, lifecycle)
# ══════════════════════════════════════════════════════════════════════

def _enumerate_leaves(d, prefix=""):
    if isinstance(d, dict) and d:
        for k, v in d.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict) and v:
                yield from _enumerate_leaves(v, p)
            else:
                yield p, v
    else:
        yield prefix, d


def _mutate(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1 if value < 999999 else value - 1
    if isinstance(value, float):
        return round(value + 0.013, 4)
    if isinstance(value, str):
        return value + "_zz"
    if isinstance(value, list):
        return list(value) + ["__test__"]
    if isinstance(value, dict):
        out = dict(value); out["__test_key__"] = "__test__"; return out
    return value


async def t_all_settings_round_trip() -> None:
    """Every leaf in settings.json patches + reads back + restores cleanly."""
    name = "settings: 118-leaf round-trip (tray patch path)"
    from tray.qt_helpers import read_settings, get_path, patch_settings, settings_path
    import config as app_config

    original = json.loads(settings_path().read_text(encoding="utf-8"))
    leaves = list(_enumerate_leaves(original))
    failed: list[tuple[str, str]] = []
    tested = 0
    skipped = 0

    try:
        for path, orig in leaves:
            new = _mutate(orig)
            if new == orig:
                skipped += 1
                continue
            try:
                patch_settings(path, new)
                got = get_path(read_settings(), path)
                if got != new:
                    failed.append((path, f"read back {got!r} != {new!r}"))
                    continue
                got_live = app_config.get_nested(path)
                if got_live != new:
                    failed.append((path, f"config.reload() stale: {got_live!r}"))
                    continue
                tested += 1
            except Exception as e:
                failed.append((path, f"{type(e).__name__}: {e}"))
            finally:
                try:
                    patch_settings(path, orig)
                except Exception:
                    pass
    finally:
        # Belt-and-braces structural restore
        settings_path().write_text(
            json.dumps(original, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        app_config.reload()

    if failed:
        RES.fail(name, f"{len(failed)} failures: " + ", ".join(p for p, _ in failed[:3]))
    else:
        RES.ok(f"{name}  ({tested} tested, {skipped} null-skipped)")


async def t_add_remove_model_flow() -> None:
    name = "add/remove:  llamacpp.models.<key>"
    from tray.qt_helpers import read_settings, get_path, patch_settings, remove_path
    import copy

    key = "__e2e_test_model__"
    seed = {"path": "/tmp/x.gguf", "ctx_size": 2048, "n_gpu_layers": 0}
    try:
        remove_path(f"llamacpp.models.{key}")
        patch_settings(f"llamacpp.models.{key}", copy.deepcopy(seed))
        if get_path(read_settings(), f"llamacpp.models.{key}.ctx_size") != 2048:
            RES.fail(name, "after add, key not present"); return
        remove_path(f"llamacpp.models.{key}")
        if get_path(read_settings(), f"llamacpp.models.{key}") is not None:
            RES.fail(name, "after remove, key still present"); return
        RES.ok(name)
    finally:
        remove_path(f"llamacpp.models.{key}")


async def t_add_remove_tool_flow() -> None:
    name = "add/remove:  tools.<key>"
    from tray.qt_helpers import read_settings, get_path, patch_settings, remove_path
    import copy

    key = "__e2e_test_tool__"
    seed = {"name": "Test", "startup_cmd": ["echo"], "flags": [], "env": {}, "session": {}}
    try:
        remove_path(f"tools.{key}")
        patch_settings(f"tools.{key}", copy.deepcopy(seed))
        if get_path(read_settings(), f"tools.{key}.startup_cmd") != ["echo"]:
            RES.fail(name, "after add, key not present"); return
        remove_path(f"tools.{key}")
        if get_path(read_settings(), f"tools.{key}") is not None:
            RES.fail(name, "after remove, key still present"); return
        RES.ok(name)
    finally:
        remove_path(f"tools.{key}")


async def t_valid_key_regex() -> None:
    name = "add/remove:  _valid_key regex"
    from tray.qt_sections import _valid_key
    for bad in ("", " ", "a b", "a:b", "a.b", "1abc", "x/y", "Ω"):
        ok, _ = _valid_key(bad)
        if ok:
            RES.fail(name, f"should reject {bad!r}"); return
    for good in ("a", "ab_cd", "ABC-123", "x9", "Qwen-30b"):
        ok, _ = _valid_key(good)
        if not ok:
            RES.fail(name, f"should accept {good!r}"); return
    RES.ok(name)


async def t_managed_tool_runtime_toggle() -> None:
    name = "managed:    runtime toggle persists & reads"
    from proxy.runtime_state import is_managed_enabled, set_tool
    tool = "web_search"
    prev = is_managed_enabled(tool)
    try:
        set_tool("managed_tools", tool, not prev)
        if is_managed_enabled(tool) != (not prev):
            RES.fail(name, "toggle did not take effect"); return
        set_tool("managed_tools", tool, prev)
        if is_managed_enabled(tool) != prev:
            RES.fail(name, "restore did not take effect"); return
        RES.ok(name)
    finally:
        set_tool("managed_tools", tool, prev)


async def t_request_log_populated() -> None:
    """After the routing/text tests have run, the in-process request_log ring
    buffer should have entries. Uses snapshot() — same call tray's Requests
    section makes."""
    name = "requests:   in-process ring buffer populated"
    from proxy import request_log
    snap = request_log.snapshot()
    if len(snap) == 0:
        RES.fail(name, "buffer empty — route handlers aren't calling request_log.finish()"); return
    # Every entry should have required fields
    e = snap[0]
    for field in ("rid", "method", "path", "started_at", "status"):
        if field not in e:
            RES.fail(name, f"entry missing field {field!r}"); return
    RES.ok(f"{name}  ({len(snap)} entries)")


async def t_supervisor_unload_load(supervisor) -> None:
    """Actually call Unload → verify alive=False → call Load → verify alive=True.
    This is the live Load Now / Unload button code path."""
    name = "llama:      Unload + Load round-trip"
    if not supervisor.alive():
        RES.fail(name, "supervisor not alive before test — can't verify unload"); return
    try:
        await supervisor.stop()
        if supervisor.alive():
            RES.fail(name, "after stop(), alive=True"); return
        t0 = time.time()
        active = await supervisor.start_default()
        dt = time.time() - t0
        if not supervisor.alive():
            RES.fail(name, "after start_default(), alive=False"); return
        if not active:
            RES.fail(name, "start_default() returned empty model name"); return
        RES.ok(f"{name}  (reloaded '{active}' in {dt:.1f}s)")
    except Exception as e:
        RES.fail(name, f"{type(e).__name__}: {e}")


async def t_supervisor_restart(supervisor) -> None:
    """Actually call Restart → stop + start_default. Verify active_model still set."""
    name = "llama:      Restart"
    if not supervisor.alive():
        RES.fail(name, "supervisor not alive before test"); return
    try:
        await supervisor.stop()
        active = await supervisor.start_default()
        if not supervisor.alive() or not active:
            RES.fail(name, f"restart failed: alive={supervisor.alive()}, active={active!r}"); return
        RES.ok(f"{name}  (active={active})")
    except Exception as e:
        RES.fail(name, f"{type(e).__name__}: {e}")


async def t_supervisor_ensure_same_model_fast(supervisor) -> None:
    """ensure_model(same) must be a no-op — no respawn."""
    name = "llama:      ensure_model(same) is a no-op"
    if not supervisor.alive():
        RES.fail(name, "supervisor not alive"); return
    active = supervisor.active_model()
    t0 = time.time()
    await supervisor.ensure_model(active)
    dt = time.time() - t0
    if not supervisor.alive() or supervisor.active_model() != active:
        RES.fail(name, "state changed unexpectedly"); return
    if dt > 2.0:
        RES.fail(name, f"took {dt:.1f}s — suggests respawn"); return
    RES.ok(f"{name}  ({dt*1000:.0f}ms)")


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

async def main() -> int:
    quick = "--quick" in sys.argv
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    print(f"Spawning llama-server (binary={llama_cfg.binary()})")
    supervisor = await get_supervisor()
    t0 = time.time()
    try:
        active = await supervisor.start_default()
    except Exception as exc:
        print(f"FATAL: supervisor failed to start: {exc}")
        traceback.print_exc()
        return 2
    print(f"llama-server ready as '{active}' in {time.time()-t0:.1f}s")

    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", proxy_config.proxy_port())
    await site.start()
    print(f"Proxy listening on {PROXY_URL}")
    await asyncio.sleep(0.5)

    timeout = aiohttp.ClientTimeout(total=300)
    # User-Agent matches the `e2e-test` profile in settings.json → clean
    # baseline (no managed tools injected, no date/location context).
    default_headers = {"User-Agent": "telecode-e2e/1.0"}
    async with aiohttp.ClientSession(timeout=timeout, headers=default_headers) as session:
        print("\n── Routing ──")
        await t_models_anthropic(session)
        await t_models_openai(session)
        await t_anth_model_alias(session)

        print("\n── Text ──")
        await t_anth_text_nonstream(session)
        await t_anth_text_stream(session)
        await t_openai_text_nonstream(session)
        await t_openai_text_stream(session)

        print("\n── Count tokens ──")
        await t_count_tokens(session)

        print("\n── Thinking ──")
        await t_anth_thinking(session)
        await t_anth_thinking_omitted(session)

        print("\n── Tool calls ──")
        await t_anth_tool_call_stream(session)
        await t_openai_tool_call_stream(session)
        await t_anth_tool_roundtrip(session)

        print("\n── Intercept loop (ToolSearch / auto_load) ──")
        await t_tool_search_via_profile(session)

        if not quick:
            print("\n── Vision ──")
            await t_anth_vision_text(session)
            await t_openai_vision(session)
            await t_anth_tool_result_with_image(session)

        print("\n── Tray: settings patch path ──")
        await t_all_settings_round_trip()
        await t_add_remove_model_flow()
        await t_add_remove_tool_flow()
        await t_valid_key_regex()
        await t_managed_tool_runtime_toggle()

        print("\n── Tray: request log ──")
        await t_request_log_populated()

        # Lifecycle tests go LAST — they stop/restart llama-server.
        print("\n── Tray: llama.cpp lifecycle actions ──")
        await t_supervisor_ensure_same_model_fast(supervisor)
        await t_supervisor_unload_load(supervisor)
        await t_supervisor_restart(supervisor)

    code = RES.summary()

    print("\nShutting down...")
    await runner.cleanup()
    await shutdown_supervisor()
    return code


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
