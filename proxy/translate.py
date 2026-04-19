"""Dual-protocol translator: Anthropic ↔ OpenAI ↔ llama.cpp.

Internal canonical format is OpenAI-shape (llama.cpp speaks it natively).
Every incoming request is translated to the internal shape; the intercept
loop operates on that shape; on the way out we translate back to whatever
protocol the client used.

Public functions:

    ## Incoming (client → internal)
    anthropic_request_to_internal(body, *, inference_defaults) -> dict
    openai_request_to_internal(body, *, inference_defaults) -> dict

    ## Outgoing (internal → client, streaming)
    async for chunk in stream_internal_to_anthropic(openai_sse, *, state): ...
    async for chunk in stream_internal_to_openai(openai_sse): ...

    ## Outgoing (internal → client, non-streaming)
    openai_response_to_anthropic(body, *, reasoning_cfg) -> dict
    openai_response_to_internal(body) -> dict   # identity + normalization

    ## Helpers
    drop_cache_control(obj)            # recursively removes cache_control
    tool_result_to_openai(tool_result) # handles image lifting done right
"""
from __future__ import annotations

import copy
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable


# ── Utilities ────────────────────────────────────────────────────────────

def drop_cache_control(obj: Any) -> Any:
    """Recursively strip `cache_control` keys from dicts/lists.

    `cache_control` is Anthropic prompt-caching metadata. llama.cpp has its
    own slot-based KV cache and doesn't want this key. We drop it during
    every translation so it can never reach upstream.
    """
    if isinstance(obj, dict):
        return {k: drop_cache_control(v) for k, v in obj.items() if k != "cache_control"}
    if isinstance(obj, list):
        return [drop_cache_control(x) for x in obj]
    return obj


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


# ── Anthropic request → internal (OpenAI-shape) ──────────────────────────

def _anthropic_content_to_openai(content: Any) -> Any:
    """Convert an Anthropic `content` field (string | list of blocks) to an
    OpenAI content field (string | list of text/image_url parts).

    Tool-use and tool-result blocks are NOT handled here — they become
    separate messages in OpenAI's shape, so the caller decomposes them.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return content

    parts: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append({"type": "text", "text": block.get("text", "")})
        elif btype == "image":
            src = block.get("source", {}) or {}
            src_type = src.get("type", "base64")
            if src_type == "base64":
                mime = src.get("media_type", "image/png")
                data = src.get("data", "")
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{data}"},
                })
            elif src_type == "url":
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": src.get("url", "")},
                })
        elif btype == "document":
            # llama.cpp doesn't have native PDF — surface as text if provided
            src = block.get("source", {}) or {}
            if src.get("type") == "text":
                parts.append({"type": "text", "text": src.get("data", "")})
        # tool_use / tool_result handled separately (see _decompose_anthropic_message)

    # If the result is a single text part, collapse to a string — llama.cpp
    # prefers string content for simple messages.
    if len(parts) == 1 and parts[0].get("type") == "text":
        return parts[0]["text"]
    return parts


def _decompose_anthropic_message(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Split one Anthropic message into one or more OpenAI messages.

    Anthropic packs tool_use + text into a single assistant message, and
    tool_result + text into a single user message. OpenAI requires separate
    messages: assistant(content + tool_calls) vs tool(one per tool_use_id).
    """
    role = msg.get("role", "user")
    content = msg.get("content", "")

    # Plain string content → single OpenAI message
    if isinstance(content, str):
        return [{"role": role, "content": content}]

    if not isinstance(content, list):
        return [{"role": role, "content": str(content)}]

    # ── Split assistant messages ────────────────────────────────────────
    if role == "assistant":
        text_parts: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text_parts.append({"type": "text", "text": block.get("text", "")})
            elif btype == "thinking":
                # We don't round-trip thinking blocks back to the model —
                # llama.cpp regenerates them via <think> tags each turn.
                continue
            elif btype == "tool_use":
                args = block.get("input", {})
                tool_calls.append({
                    "id": block.get("id", _gen_id("call")),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(args),
                    },
                })
        out_msg: dict[str, Any] = {"role": "assistant"}
        # Flatten single-text content to string (llama.cpp prefers it)
        if len(text_parts) == 1:
            out_msg["content"] = text_parts[0]["text"]
        elif text_parts:
            out_msg["content"] = text_parts
        else:
            out_msg["content"] = None if tool_calls else ""
        if tool_calls:
            out_msg["tool_calls"] = tool_calls
        return [out_msg]

    # ── Split user messages (possibly containing tool_results) ──────────
    if role == "user":
        out: list[dict[str, Any]] = []
        plain_parts: list[dict[str, Any]] = []  # text/image for the "user" message
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_result":
                # Emit one OpenAI `tool` message per tool_result. Content may
                # be string OR an array with text + image blocks. llama.cpp
                # (being OpenAI-compatible) accepts string content for `tool`
                # role; arrays only go to `user` messages. So we lift images
                # out AFTER flushing the tool message.
                tr_content = block.get("content", "")
                tool_text, lifted_parts = _split_tool_result_content(tr_content)
                out.append({
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", ""),
                    "content": tool_text,
                })
                # Append lifted images as a separate user message so the
                # model can still see them contextually linked to this tool.
                if lifted_parts:
                    out.append({"role": "user", "content": lifted_parts})
            elif btype == "text":
                plain_parts.append({"type": "text", "text": block.get("text", "")})
            elif btype == "image":
                src = block.get("source", {}) or {}
                src_type = src.get("type", "base64")
                if src_type == "base64":
                    mime = src.get("media_type", "image/png")
                    data = src.get("data", "")
                    plain_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{data}"},
                    })
                elif src_type == "url":
                    plain_parts.append({
                        "type": "image_url",
                        "image_url": {"url": src.get("url", "")},
                    })

        if plain_parts:
            if len(plain_parts) == 1 and plain_parts[0].get("type") == "text":
                out.append({"role": "user", "content": plain_parts[0]["text"]})
            else:
                out.append({"role": "user", "content": plain_parts})
        return out

    # Unknown role — pass through with best-effort content conversion
    return [{"role": role, "content": _anthropic_content_to_openai(content)}]


def _split_tool_result_content(content: Any) -> tuple[str, list[dict[str, Any]]]:
    """Return (text_for_tool_message, lifted_image_parts).

    For string content: returns (content, []).
    For array content: joins text blocks; image blocks are returned
    separately so the caller can attach them to a subsequent user message
    (OpenAI's `tool` role requires string content).
    """
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return str(content), []

    text_parts: list[str] = []
    images: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "image":
            src = block.get("source", {}) or {}
            src_type = src.get("type", "base64")
            if src_type == "base64":
                mime = src.get("media_type", "image/png")
                data = src.get("data", "")
                images.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{data}"},
                })
            elif src_type == "url":
                images.append({
                    "type": "image_url",
                    "image_url": {"url": src.get("url", "")},
                })

    text_out = "\n".join(text_parts)
    if images and not text_out:
        text_out = f"[{len(images)} image(s) from this tool attached in next message]"
    return text_out, images


def _merge_chat_template_kwargs(out_body: dict[str, Any], patch: dict[str, Any]) -> None:
    """Merge a dict into body.chat_template_kwargs (creating it if needed)."""
    if not patch:
        return
    kwargs = dict(out_body.get("chat_template_kwargs") or {})
    kwargs.update(patch)
    out_body["chat_template_kwargs"] = kwargs


def _apply_effort_entry(
    entry: dict[str, Any],
    out_body: dict[str, Any],
) -> str:
    """Apply one reasoning_effort_map entry to the outgoing body.

    Recognized keys (everything else ignored — forward-compatible):
      enable_thinking        bool  → chat_template_kwargs.enable_thinking
      thinking_budget_tokens int   → body.thinking_budget_tokens (llama.cpp
                                      enforces this server-side; 0 = unlimited)
      reasoning_effort       str   → chat_template_kwargs.reasoning_effort
                                      (for models whose chat template uses it)
      max_tokens             int   → body.max_tokens (hard cap on total output)
      system_nudge           str   → returned — caller prepends to system
                                      (legacy fallback; prefer the numeric knobs)

    Returns the system_nudge (may be "") so the caller can handle prompt-level
    injection in its own flow.
    """
    # chat_template_kwargs layer
    ct_patch: dict[str, Any] = {}
    if "enable_thinking" in entry:
        ct_patch["enable_thinking"] = bool(entry["enable_thinking"])
    if "reasoning_effort" in entry and entry["reasoning_effort"]:
        ct_patch["reasoning_effort"] = str(entry["reasoning_effort"])
    _merge_chat_template_kwargs(out_body, ct_patch)

    # Native llama.cpp thinking budget (hard server-side cap)
    if "thinking_budget_tokens" in entry:
        out_body["thinking_budget_tokens"] = int(entry["thinking_budget_tokens"])

    # Optional overall token cap
    if "max_tokens" in entry and entry["max_tokens"] is not None:
        # Respect a tighter cap — but don't grow a caller-supplied cap.
        cur = out_body.get("max_tokens")
        new = int(entry["max_tokens"])
        if cur is None or new < cur:
            out_body["max_tokens"] = new

    return str(entry.get("system_nudge", "") or "")


def _resolve_reasoning_effort(
    effort: str | None,
    defaults: dict[str, Any],
) -> dict[str, Any]:
    """Look up a `reasoning_effort` string in the per-model map.

    Returns the matched entry dict (empty dict if not found). Caller passes it
    to `_apply_effort_entry` to apply the knobs to the outgoing body.
    """
    if not effort:
        return {}
    mapping = (defaults.get("reasoning_effort_map") or {})
    entry = mapping.get(str(effort).lower())
    return dict(entry) if isinstance(entry, dict) else {}


def _anthropic_system_to_openai(system: Any) -> str:
    """Flatten Anthropic system (string | list of text blocks) to a single string."""
    if not system:
        return ""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n\n".join(p for p in parts if p)
    return str(system)


def _anthropic_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic tool schema → OpenAI function-calling schema."""
    out = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        out.append({
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object"}),
            },
        })
    return out


def _anthropic_tool_choice_to_openai(tc: Any) -> Any:
    """Anthropic tool_choice → OpenAI tool_choice."""
    if not tc:
        return None
    if isinstance(tc, str):
        # "auto" / "any" / "none"
        if tc in ("auto", "none"):
            return tc
        if tc == "any":
            return "required"
    if isinstance(tc, dict):
        ttype = tc.get("type", "")
        if ttype == "auto":
            return "auto"
        if ttype == "any":
            return "required"
        if ttype == "none":
            return "none"
        if ttype == "tool":
            return {"type": "function", "function": {"name": tc.get("name", "")}}
    return None


def anthropic_request_to_internal(
    body: dict[str, Any],
    *,
    inference_defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Translate an Anthropic /v1/messages body to an internal (OpenAI-shape) body.

    The result is directly POST-able to llama-server's /v1/chat/completions.
    """
    body = drop_cache_control(body)
    defaults = inference_defaults or {}

    # ── Anthropic reasoning controls ─────────────────────────────────────
    # Two parameters, can combine:
    #
    #   thinking:
    #     {"type": "disabled"}               → Qwen /no_think injected
    #     {"type": "enabled",                  → older models (pre-4.6)
    #      "budget_tokens": N,                  bucketed via effort_map
    #      "display": "summarized"|"omitted"}   omitted → strip <think> output
    #     {"type": "adaptive",               → Opus 4.6+: model decides
    #      "display": "summarized"|"omitted"}
    #
    #   output_config:
    #     {"effort": "low"|"medium"|"high"|  → maps via reasoning_effort_map
    #               "xhigh"|"max"}
    #
    # If BOTH `output_config.effort` and `thinking.budget_tokens` are set,
    # effort wins (Anthropic's newer control model).
    thinking_param = body.get("thinking") or {}
    output_config = body.get("output_config") or {}
    explicit_effort = output_config.get("effort")

    tt = thinking_param.get("type", "")

    # Output display — if omitted, don't forward thinking deltas.
    # Hints ride on the returned body's `_telecode_hints` key; the server
    # pops them via `pop_hints()` before forwarding upstream.
    display = thinking_param.get("display")
    hints_to_propagate: dict[str, Any] = {}
    if display == "omitted":
        hints_to_propagate["emit_thinking_blocks"] = False

    # Resolve effort with precedence:
    #   output_config.effort > budget_tokens bucket > thinking.type
    effort: str | None = None
    direct_budget: int | None = None
    if explicit_effort:
        effort = str(explicit_effort).lower()
    elif tt == "enabled":
        # Pass the explicit budget through verbatim — llama.cpp enforces it.
        budget = int(thinking_param.get("budget_tokens", 0) or 0)
        if budget:
            direct_budget = budget
        # Also bucket to an effort string so nudges (if configured) still apply.
        if not budget:
            effort = "medium"
        elif budget < 1000:
            effort = "low"
        elif budget > 20000:
            effort = "max"
        elif budget > 10000:
            effort = "high"
        else:
            effort = "medium"
    elif tt == "adaptive":
        effort = "high"
    elif tt == "disabled":
        effort = "none"

    # Strip Anthropic-specific fields so llama.cpp doesn't reject them
    body.pop("thinking", None)
    body.pop("output_config", None)

    messages: list[dict[str, Any]] = []

    # Resolve effort → entry dict, then apply to `out` (chat_template_kwargs,
    # thinking_budget_tokens, max_tokens, optional system_nudge). The direct
    # Anthropic budget_tokens wins over whatever the map says for budget.
    entry = _resolve_reasoning_effort(effort, defaults)

    system_text = _anthropic_system_to_openai(body.get("system"))

    if system_text:
        messages.append({"role": "system", "content": system_text})

    for msg in body.get("messages", []):
        messages.extend(_decompose_anthropic_message(msg))

    out: dict[str, Any] = {
        "model": body.get("model", ""),
        "messages": messages,
        "stream": bool(body.get("stream", False)),
    }

    nudge = _apply_effort_entry(entry, out)
    # Master "Use reasoning" switch (settings.json llamacpp.inference.disable_thinking)
    # — only applies if the client didn't explicitly send `thinking` already.
    if not thinking_param and bool(defaults.get("disable_thinking", False)):
        _apply_thinking_off(out)
    # direct budget from client overrides whatever the map said
    if direct_budget is not None:
        out["thinking_budget_tokens"] = direct_budget
    if nudge:
        # Prepend nudge to leading system message (or create one)
        if out["messages"] and out["messages"][0].get("role") == "system":
            existing = out["messages"][0].get("content", "")
            out["messages"][0] = {
                **out["messages"][0],
                "content": f"{nudge}\n\n{existing}" if existing else nudge,
            }
        else:
            out["messages"] = [{"role": "system", "content": nudge}, *out["messages"]]

    # Inference params — body wins over defaults
    def _pick(key: str, fallback_key: str | None = None) -> Any:
        if key in body:
            return body[key]
        if fallback_key and fallback_key in body:
            return body[fallback_key]
        if key in defaults:
            return defaults[key]
        return None

    temperature = _pick("temperature")
    if temperature is not None:
        out["temperature"] = temperature
    top_p = _pick("top_p")
    if top_p is not None:
        out["top_p"] = top_p
    top_k = _pick("top_k")
    if top_k is not None:
        out["top_k"] = top_k
    min_p = defaults.get("min_p")
    if min_p is not None:
        out["min_p"] = min_p
    rep = defaults.get("repeat_penalty")
    if rep is not None:
        out["repeat_penalty"] = rep
    pres = _pick("presence_penalty")
    if pres is not None:
        out["presence_penalty"] = pres
    freq = _pick("frequency_penalty")
    if freq is not None:
        out["frequency_penalty"] = freq

    max_tokens = _pick("max_tokens")
    if max_tokens is not None:
        out["max_tokens"] = max_tokens

    stop_seqs = body.get("stop_sequences") or defaults.get("stop") or []
    if stop_seqs:
        out["stop"] = list(stop_seqs)

    tools = _anthropic_tools_to_openai(body.get("tools", []))
    if tools:
        out["tools"] = tools

    tc = _anthropic_tool_choice_to_openai(body.get("tool_choice"))
    if tc is not None:
        out["tool_choice"] = tc

    # Structured output — if the client passed response_format pass it
    # through verbatim (llama.cpp accepts it directly).
    if "response_format" in body:
        out["response_format"] = body["response_format"]
    elif defaults.get("structured_output", {}).get("enabled"):
        so = defaults["structured_output"]
        if so.get("schema"):
            out["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "strict": True, "schema": so["schema"]},
            }
        elif so.get("grammar"):
            out["grammar"] = so["grammar"]

    # Ask llama.cpp to include usage on the final streaming chunk
    if out["stream"]:
        out["stream_options"] = {"include_usage": True}

    # Slot-based prompt cache is on by default in llama.cpp; make it explicit
    out["cache_prompt"] = True

    if hints_to_propagate:
        out["_telecode_hints"] = hints_to_propagate

    return out


# ── OpenAI request → internal (almost identity) ─────────────────────────

def pop_hints(body: dict[str, Any]) -> dict[str, Any]:
    """Pop proxy-internal hints the translator left in the body.

    Called by the server layer BEFORE forwarding the body upstream so
    llama.cpp never sees these keys. Returns the hint dict (may be empty).
    """
    return body.pop("_telecode_hints", {}) or {}


def openai_request_to_internal(
    body: dict[str, Any],
    *,
    inference_defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize an OpenAI /v1/chat/completions body for llama.cpp.

    - strips cache_control anywhere it appears (defensive; some clients mirror it)
    - maps `reasoning_effort` through `inference.reasoning_effort_map`
    - applies missing inference defaults
    - ensures stream_options.include_usage when streaming
    - ensures cache_prompt
    """
    body = drop_cache_control(copy.deepcopy(body))
    defaults = inference_defaults or {}

    # Resolve reasoning effort from either of OpenAI's two shapes:
    #   flat:   body.reasoning_effort: "minimal"|"low"|"medium"|"high"|"xhigh"|"none"
    #   nested: body.reasoning.effort (Responses API / newer Chat Completions)
    # Nested wins if both present. Strip both from the body either way.
    effort = body.pop("reasoning_effort", None)
    nested = body.pop("reasoning", None)
    if isinstance(nested, dict) and nested.get("effort"):
        effort = nested["effort"]

    entry = _resolve_reasoning_effort(effort, defaults)
    sys_nudge = _apply_effort_entry(entry, body)
    # Master "Use reasoning" switch from settings.json — only fires when the
    # client didn't explicitly send a reasoning_effort.
    if effort is None and bool(defaults.get("disable_thinking", False)):
        _apply_thinking_off(body)

    messages = body.get("messages") or []
    if sys_nudge:
        if messages and messages[0].get("role") == "system":
            existing = messages[0].get("content", "")
            if isinstance(existing, str):
                messages[0] = {**messages[0], "content": f"{sys_nudge}\n\n{existing}" if existing else sys_nudge}
        else:
            messages = [{"role": "system", "content": sys_nudge}, *messages]
        body["messages"] = messages

    def _fill(key: str, val: Any) -> None:
        if key not in body and val is not None:
            body[key] = val

    _fill("temperature", defaults.get("temperature"))
    _fill("top_p", defaults.get("top_p"))
    _fill("top_k", defaults.get("top_k"))
    _fill("min_p", defaults.get("min_p"))
    _fill("repeat_penalty", defaults.get("repeat_penalty"))
    _fill("presence_penalty", defaults.get("presence_penalty"))
    _fill("frequency_penalty", defaults.get("frequency_penalty"))

    if "max_tokens" not in body and defaults.get("max_tokens") is not None:
        body["max_tokens"] = defaults["max_tokens"]

    if "stop" not in body and defaults.get("stop"):
        body["stop"] = list(defaults["stop"])

    if body.get("stream"):
        opts = body.get("stream_options") or {}
        opts.setdefault("include_usage", True)
        body["stream_options"] = opts

    body.setdefault("cache_prompt", True)

    return body


# ── Reasoning state machine ──────────────────────────────────────────────

@dataclass
class ReasoningState:
    """Tracks whether the current text stream is inside a `<think>` section.

    The model may emit the opener or closer tag split across multiple deltas
    (e.g. "<thi" then "nk>"). We buffer up to `len(longest_tag)` characters
    of pending output so we can recognize a tag that lands across a boundary.
    """
    start_tag: str = "<think>"
    end_tag: str = "</think>"
    emit_thinking: bool = True
    enabled: bool = True

    # Mutable state
    in_think: bool = False
    pending: str = ""  # text held back while we watch for partial tag matches

    def _max_tag_len(self) -> int:
        return max(len(self.start_tag), len(self.end_tag))

    def _could_extend_to_tag(self, s: str) -> bool:
        """True if s is a (strict) prefix of either start_tag or end_tag."""
        return (
            (self.start_tag.startswith(s) and s != self.start_tag)
            or (self.end_tag.startswith(s) and s != self.end_tag)
        )

    def push(self, text: str) -> list[tuple[str, str]]:
        """Feed a chunk of model text. Returns a list of (kind, emit) pairs.

        kind ∈ {"text", "thinking"} — destination Anthropic block type.
        emit is the substring to emit to that block. Empty strings are
        never returned.

        Tags themselves are consumed, not emitted. If `enabled=False`, the
        whole stream is flushed as "text" verbatim.
        """
        if not self.enabled:
            return [("text", text)] if text else []

        out: list[tuple[str, str]] = []
        self.pending += text

        while True:
            if not self.pending:
                break

            target_tag = self.end_tag if self.in_think else self.start_tag

            # Look for a full tag hit
            idx = self.pending.find(target_tag)
            if idx >= 0:
                # Emit anything before the tag to the current kind
                before = self.pending[:idx]
                if before:
                    kind = "thinking" if self.in_think else "text"
                    if kind == "thinking" and not self.emit_thinking:
                        pass  # drop
                    else:
                        out.append((kind, before))
                # Consume the tag, flip state
                self.pending = self.pending[idx + len(target_tag):]
                self.in_think = not self.in_think
                continue

            # No full hit. Might a tail of `pending` be a prefix of a tag?
            max_tail = min(len(self.pending), self._max_tag_len() - 1)
            keep_tail = 0
            for k in range(max_tail, 0, -1):
                if self._could_extend_to_tag(self.pending[-k:]):
                    keep_tail = k
                    break

            if keep_tail:
                emit = self.pending[:-keep_tail]
                self.pending = self.pending[-keep_tail:]
            else:
                emit = self.pending
                self.pending = ""

            if emit:
                kind = "thinking" if self.in_think else "text"
                if kind == "thinking" and not self.emit_thinking:
                    pass  # drop
                else:
                    out.append((kind, emit))

            if not self.pending:
                break
            # Otherwise loop: pending is a partial tag; wait for more input.
            # But if we didn't shrink pending this iteration, break.
            break

        return out

    def flush(self) -> list[tuple[str, str]]:
        """Emit any buffered trailing text at stream end. Partial tag buffers
        get flushed as their current kind (assume tag was garbage)."""
        if not self.pending:
            return []
        kind = "thinking" if self.in_think else "text"
        out = []
        if not (kind == "thinking" and not self.emit_thinking):
            out.append((kind, self.pending))
        self.pending = ""
        return out


# ── OpenAI stream → Anthropic SSE ────────────────────────────────────────

def _sse_event(event_type: str, data: dict[str, Any]) -> bytes:
    """Build one `event: X\\n` + `data: {...}\\n\\n` SSE frame."""
    return (
        f"event: {event_type}\n"
        f"data: {json.dumps(data)}\n\n"
    ).encode()


@dataclass
class AnthropicStreamState:
    """Assembles Anthropic SSE output from OpenAI chat.completion.chunk events.

    One instance per request. Caller feeds each parsed `data: {...}` object
    to `step()` and writes the returned bytes (if any) to the wire.
    """
    reasoning: ReasoningState = field(default_factory=ReasoningState)
    client_model: str = ""

    # Per-stream state
    _message_id: str = ""
    _message_started: bool = False
    _stop_reason: str = "end_turn"
    _usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})

    # Content block bookkeeping
    _next_index: int = 0
    _current_kind: str = ""   # "" | "text" | "thinking" | "tool_use"
    _current_index: int = -1

    # Tool-call assembly (OpenAI sends tool_calls with `index` field)
    _tool_calls: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Map OpenAI tool_call.index → Anthropic content block index
    _tool_index_map: dict[int, int] = field(default_factory=dict)
    # Closed/open tracking for tool_use blocks
    _tool_closed: set = field(default_factory=set)

    def start_message(self) -> bytes:
        """Return the message_start frame. Call exactly once per stream."""
        if self._message_started:
            return b""
        self._message_started = True
        self._message_id = _gen_id("msg")
        msg = {
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
        return _sse_event("message_start", msg)

    def _open_block(self, kind: str) -> tuple[int, bytes]:
        """Open a new content block of `kind`. Returns (index, sse_bytes)."""
        idx = self._next_index
        self._next_index += 1
        self._current_kind = kind
        self._current_index = idx
        if kind == "thinking":
            block = {"type": "thinking", "thinking": ""}
        elif kind == "text":
            block = {"type": "text", "text": ""}
        else:
            # tool_use is opened via a separate path (_open_tool_block)
            raise ValueError(f"use _open_tool_block for {kind}")
        return idx, _sse_event("content_block_start", {
            "type": "content_block_start",
            "index": idx,
            "content_block": block,
        })

    def _open_tool_block(self, tool_id: str, tool_name: str) -> tuple[int, bytes]:
        idx = self._next_index
        self._next_index += 1
        self._current_kind = "tool_use"
        self._current_index = idx
        return idx, _sse_event("content_block_start", {
            "type": "content_block_start",
            "index": idx,
            "content_block": {
                "type": "tool_use",
                "id": tool_id,
                "name": tool_name,
                "input": {},
            },
        })

    def _close_current(self) -> bytes:
        """Close the currently open content block (if any)."""
        if self._current_index < 0:
            return b""
        out = _sse_event("content_block_stop", {
            "type": "content_block_stop",
            "index": self._current_index,
        })
        # If a tool block, also emit an empty signature / input_json completion
        self._current_kind = ""
        self._current_index = -1
        return out

    def step(self, chunk: dict[str, Any]) -> bytes:
        """Consume one OpenAI chat.completion.chunk and return SSE bytes (may be empty)."""
        out = bytearray()

        if not self._message_started:
            out += self.start_message()

        choices = chunk.get("choices", [])
        if not choices:
            # Usage-only final chunk
            usage = chunk.get("usage")
            if usage:
                self._usage["input_tokens"] = int(usage.get("prompt_tokens", 0) or 0)
                self._usage["output_tokens"] = int(usage.get("completion_tokens", 0) or 0)
            return bytes(out)

        choice = choices[0]
        delta = choice.get("delta", {}) or {}
        finish = choice.get("finish_reason")

        # ── Handle tool_calls delta ─────────────────────────────────────
        for tc in delta.get("tool_calls", []) or []:
            ti = tc.get("index", 0)
            entry = self._tool_calls.setdefault(ti, {
                "id": tc.get("id", _gen_id("call")),
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

            # First time we see this call — close prior block, open tool block
            if ti not in self._tool_index_map:
                # Flush any pending reasoning buffer before switching blocks
                for kind, text in self.reasoning.flush():
                    out += self._emit_text_like(kind, text)

                if self._current_index >= 0:
                    out += self._close_current()

                idx, b = self._open_tool_block(entry["id"], entry["name"] or "")
                self._tool_index_map[ti] = idx
                out += b
            else:
                # Name may still be arriving piecemeal — skip; we don't need
                # to re-emit content_block_start. (OpenAI usually sends the
                # full name in the first chunk.)
                pass

            # Stream arguments as input_json_delta on the mapped index
            if "arguments" in fn and fn["arguments"]:
                anth_idx = self._tool_index_map[ti]
                out += _sse_event("content_block_delta", {
                    "type": "content_block_delta",
                    "index": anth_idx,
                    "delta": {"type": "input_json_delta", "partial_json": fn["arguments"]},
                })

        # ── Handle text content delta ───────────────────────────────────
        content = delta.get("content")
        if content is not None and content != "":
            # If we're currently in a tool block, close it; text resumes
            if self._current_kind == "tool_use":
                out += self._close_current()

            for kind, text in self.reasoning.push(content):
                out += self._emit_text_like(kind, text)

        # ── Handle finish_reason ────────────────────────────────────────
        if finish:
            # Flush reasoning buffer
            for kind, text in self.reasoning.flush():
                out += self._emit_text_like(kind, text)

            if self._current_index >= 0:
                out += self._close_current()

            # Close any still-open tool blocks (paranoid — usually already closed)
            # (None should remain since we always close on kind-switch.)

            self._stop_reason = _finish_reason_to_anthropic(finish)

            # message_delta with stop info + usage. Both input_tokens and
            # output_tokens MUST be present — claude-cli reads input_tokens
            # to compute the /context "current usage", and a missing field
            # surfaces as 0/200k (looks like the entire context is empty).
            out += _sse_event("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": self._stop_reason, "stop_sequence": None},
                "usage": {
                    "input_tokens":  self._usage["input_tokens"],
                    "output_tokens": self._usage["output_tokens"],
                },
            })
            out += _sse_event("message_stop", {"type": "message_stop"})

        return bytes(out)

    def _emit_text_like(self, kind: str, text: str) -> bytes:
        """Emit a text or thinking delta, opening a new block if needed."""
        out = bytearray()
        # If current block is wrong kind, close it first
        if self._current_kind != kind:
            if self._current_index >= 0:
                out += self._close_current()
            _, b = self._open_block(kind)
            out += b

        delta_type = "thinking_delta" if kind == "thinking" else "text_delta"
        delta_field = "thinking" if kind == "thinking" else "text"
        out += _sse_event("content_block_delta", {
            "type": "content_block_delta",
            "index": self._current_index,
            "delta": {"type": delta_type, delta_field: text},
        })
        return bytes(out)


def _finish_reason_to_anthropic(reason: str) -> str:
    return {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "stop_sequence",
        "function_call": "tool_use",
    }.get(reason, "end_turn")


def emit_anthropic_status_block(text: str, index: int) -> bytes:
    """Synthesize a short text content block carrying a status line.

    Used by the proxy to show ● / └ tool-call visibility inline between
    intercept rounds. The block is fully self-contained (start + delta +
    stop) so clients' content arrays stay well-formed.
    """
    return (
        _sse_event("content_block_start", {
            "type": "content_block_start", "index": index,
            "content_block": {"type": "text", "text": ""},
        })
        + _sse_event("content_block_delta", {
            "type": "content_block_delta", "index": index,
            "delta": {"type": "text_delta", "text": text + "\n\n"},
        })
        + _sse_event("content_block_stop", {
            "type": "content_block_stop", "index": index,
        })
    )


def emit_openai_status_chunk(text: str, model: str, completion_id: str) -> bytes:
    """Synthesize an OpenAI streaming chunk carrying a status line.

    OpenAI clients display role:"assistant" content deltas in sequence —
    prepending the status text before the real model output gives the same
    visual effect as Anthropic's synthetic content blocks.
    """
    chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {"content": text + "\n\n"},
            "finish_reason": None,
        }],
    }
    return f"data: {json.dumps(chunk)}\n\n".encode()


# ── OpenAI response → Anthropic (non-stream) ─────────────────────────────

def openai_response_to_anthropic(
    body: dict[str, Any],
    *,
    reasoning_cfg: dict[str, Any] | None = None,
    client_model: str = "",
) -> dict[str, Any]:
    """Convert a complete OpenAI chat.completion response into an Anthropic
    /v1/messages response.
    """
    reasoning_cfg = reasoning_cfg or {}
    reasoning = ReasoningState(
        start_tag=reasoning_cfg.get("start", "<think>"),
        end_tag=reasoning_cfg.get("end", "</think>"),
        emit_thinking=reasoning_cfg.get("emit_thinking_blocks", True),
        enabled=reasoning_cfg.get("enabled", True),
    )

    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message", {}) or {}

    content_blocks: list[dict[str, Any]] = []

    # Text content — run through reasoning state machine to split thinking/text
    text = message.get("content") or ""
    if isinstance(text, list):
        # Rare: content arrays in non-stream response — flatten
        text = "".join(
            p.get("text", "") for p in text if isinstance(p, dict) and p.get("type") == "text"
        )

    if text:
        text_buf = ""
        think_buf = ""
        for kind, chunk in (*reasoning.push(text), *reasoning.flush()):
            if kind == "thinking":
                think_buf += chunk
            else:
                text_buf += chunk
        if think_buf:
            content_blocks.append({"type": "thinking", "thinking": think_buf, "signature": ""})
        if text_buf:
            content_blocks.append({"type": "text", "text": text_buf})

    # Tool calls
    for tc in message.get("tool_calls", []) or []:
        fn = tc.get("function", {}) or {}
        try:
            args = json.loads(fn.get("arguments", "{}") or "{}")
        except json.JSONDecodeError:
            args = {}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id", _gen_id("call")),
            "name": fn.get("name", ""),
            "input": args,
        })

    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    usage = body.get("usage") or {}
    return {
        "id": body.get("id") or _gen_id("msg"),
        "type": "message",
        "role": "assistant",
        "model": client_model or body.get("model", "unknown"),
        "content": content_blocks,
        "stop_reason": _finish_reason_to_anthropic(choice.get("finish_reason") or "stop"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "output_tokens": int(usage.get("completion_tokens", 0) or 0),
        },
    }


# ── OpenAI response → Anthropic (count_tokens) ───────────────────────────

def openai_tokenize_to_count_tokens(token_count: int) -> dict[str, Any]:
    return {"input_tokens": token_count}


# ── Model list conversions ───────────────────────────────────────────────

def openai_models_to_anthropic(openai_data: dict, aliases: dict[str, str]) -> dict:
    """Convert llama.cpp `/v1/models` (OpenAI shape) to Anthropic shape.

    Prepends every alias key from `proxy.model_mapping` so clients see
    familiar Claude-ish names.
    """
    from datetime import datetime, timezone

    models: list[dict[str, Any]] = []

    for alias in aliases.keys():
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
            created_at = datetime.fromtimestamp(
                int(created_ts), tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OSError, ValueError, TypeError):
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


def build_openai_models(
    registered: Iterable[str],
    aliases: dict[str, str],
) -> dict[str, Any]:
    """Build an OpenAI-shape /v1/models response from our registry + aliases."""
    seen: set[str] = set()
    data: list[dict[str, Any]] = []
    for name in list(aliases.keys()) + list(registered):
        if name in seen:
            continue
        seen.add(name)
        data.append({
            "id": name,
            "object": "model",
            "created": 0,
            "owned_by": "telecode",
        })
    return {"object": "list", "data": data}
