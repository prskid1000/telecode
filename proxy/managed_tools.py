"""Proxy-managed tools — injected into the model's tool list and intercepted
on tool_use, so the model can call them without a separate MCP connection.

Each tool has an Anthropic-format schema (injected into core tools via
`split_tools`) and an async handler (dispatched in `_handle_streaming`).
CC's versions of the same tools are stripped from the request so the model
only sees ours.

Adding a new managed tool:
  1. Define the schema dict (Anthropic tool format)
  2. Write an async handler: (input_dict) -> str
  3. Call `register(name, schema, handler, strip=["CCToolName"])`
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger("telecode.proxy.managed_tools")

Handler = Callable[[dict[str, Any]], Awaitable[str]]


@dataclass
class ManagedTool:
    name: str
    schema: dict[str, Any]
    handler: Handler
    strip_from_cc: list[str] = field(default_factory=list)


_REGISTRY: dict[str, ManagedTool] = {}


def register(
    name: str,
    schema: dict[str, Any],
    handler: Handler,
    strip: list[str] | None = None,
) -> None:
    _REGISTRY[name] = ManagedTool(
        name=name, schema=schema, handler=handler,
        strip_from_cc=strip or [],
    )
    log.info("Registered managed tool: %s (strips CC tools: %s)", name, strip or [])


def get_schemas() -> list[dict[str, Any]]:
    return [t.schema for t in _REGISTRY.values()]


def get_strip_names() -> set[str]:
    names: set[str] = set()
    for t in _REGISTRY.values():
        names.update(t.strip_from_cc)
        names.add(t.name)
    return names


def get_handler(name: str) -> Handler | None:
    t = _REGISTRY.get(name)
    return t.handler if t else None


def is_managed(name: str) -> bool:
    return name in _REGISTRY


# ── Tool registrations ────────────────────────────────────────────────────
# Lazy-imported at the bottom so handlers can reference heavy modules
# without slowing proxy boot.


def _register_all() -> None:
    """Register all proxy-managed tools. Called once on module load."""

    from proxy.tool_registry import WEB_SEARCH_TOOL
    from proxy import config as proxy_config

    # ── WebSearch ──────────────────────────────────────────────────────
    if proxy_config.web_search_enabled():
        async def _handle_web_search(args: dict[str, Any]) -> str:
            from proxy.web_search import search as ws_search
            query = (args.get("query") or "").strip()
            categories = args.get("categories") or ["general"]
            if isinstance(categories, str):
                categories = [categories]
            result_str, count = await ws_search(query, categories=categories)
            summary = f'\U0001f50d WebSearch("{query}", categories={categories}) \u2192 {count} results'
            log.info("%s", summary)
            return f"{summary}\n\n{result_str}"

        register("WebSearch", WEB_SEARCH_TOOL, _handle_web_search, strip=["WebSearch"])

    # ── speak (TTS) ───────────────────────────────────────────────────
    speak_schema: dict[str, Any] = {
        "name": "speak",
        "description": (
            "Generate speech audio from text using Kokoro TTS. "
            "Returns the absolute path to the generated audio file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to speak."},
                "voice": {
                    "type": "string",
                    "description": "Kokoro voice ID (e.g. af_heart, am_adam). Default: af_heart.",
                    "default": "af_heart",
                },
                "output_path": {
                    "type": "string",
                    "description": "Optional file path for the output WAV. If empty, saves to a temp file.",
                    "default": "",
                },
            },
            "required": ["text"],
        },
    }

    async def _handle_speak(args: dict[str, Any]) -> str:
        from mcp_server.tools.tts import speak
        return await speak(
            text=args.get("text", ""),
            voice=args.get("voice", "af_heart"),
            output_path=args.get("output_path", ""),
        )

    register("speak", speak_schema, _handle_speak)

    # ── transcribe (STT) ──────────────────────────────────────────────
    transcribe_schema: dict[str, Any] = {
        "name": "transcribe",
        "description": (
            "Transcribe audio to text using Whisper STT. "
            "Accepts local file paths or remote URLs (http/https). "
            "Returns the transcribed text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "audio_path": {
                    "type": "string",
                    "description": "Path to the audio file, or a remote URL.",
                },
                "language": {
                    "type": "string",
                    "description": "Two-letter language code (e.g. 'en'). Optional.",
                    "default": "",
                },
            },
            "required": ["audio_path"],
        },
    }

    async def _handle_transcribe(args: dict[str, Any]) -> str:
        from mcp_server.tools.stt import transcribe
        return await transcribe(
            audio_path=args.get("audio_path", ""),
            language=args.get("language", ""),
        )

    register("transcribe", transcribe_schema, _handle_transcribe)


_register_all()
