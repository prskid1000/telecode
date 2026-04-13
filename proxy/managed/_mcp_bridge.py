"""Auto-bridge every MCP tool into the proxy's managed-tool registry.

Single source of truth: `mcp_server/tools/` files. Each `@mcp_app.tool()` is
registered as a managed tool here too, with the same schema and a handler
that dispatches through MCP's tool manager.

Result: drop a new .py file in `mcp_server/tools/` and it's exposed via
both transports (MCP streamable-HTTP and the proxy intercept loop) with
zero duplicated code.
"""
from __future__ import annotations

import logging
from typing import Any

from proxy.managed_tools import register

log = logging.getLogger("telecode.proxy.managed.mcp_bridge")

# Primary-arg guess for the visibility line (falls back to first required field).
_PRIMARY_HINTS = {"query", "text", "audio_path", "code", "url", "path"}


def _pick_primary_arg(params: dict[str, Any]) -> str:
    props = (params or {}).get("properties", {})
    required = (params or {}).get("required", [])
    for k in required:
        if k in _PRIMARY_HINTS:
            return k
    return required[0] if required else next(iter(props), "")


def _bridge_all() -> None:
    from mcp_server.app import mcp_app, register_all as _register_mcp
    _register_mcp()

    mgr = mcp_app._tool_manager

    for info in mgr.list_tools():
        name = info.name
        schema = {
            "name": name,
            "description": (info.description or "").strip(),
            "input_schema": info.parameters or {"type": "object", "properties": {}},
        }
        primary = _pick_primary_arg(info.parameters or {})

        def _make_handler(tool_name: str):
            async def _handle(args: dict[str, Any]) -> tuple[str, str]:
                result = await mgr.call_tool(tool_name, args)
                # FastMCP may return a list of content blocks, a str, or an object.
                if isinstance(result, list):
                    parts = []
                    for block in result:
                        text = getattr(block, "text", None)
                        parts.append(text if text is not None else str(block))
                    text_out = "\n".join(parts)
                elif isinstance(result, str):
                    text_out = result
                else:
                    text_out = str(result)
                preview = text_out.splitlines()[0][:80] if text_out else "(no output)"
                return preview, text_out
            return _handle

        # CC's same-name tool (if any) should be replaced by ours
        register(name, schema, _make_handler(name),
                 strip=[name], primary_arg=primary)
        log.info("Bridged MCP tool → managed: %s (primary=%s)", name, primary)


_bridge_all()
