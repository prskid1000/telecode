"""MCP-client bridge — registers the host's tools as managed.

Single host model: telecode supervises one `docgraph host` process; we
discover its tool surface once on `bridge_host()` and register every
tool as `docgraph_<tool>`. Each tool's `root` argument (a closed enum
emitted by the host) lets agents pick which registered repo per call —
no per-root namespacing needed in the bridged tool name.

Handlers re-open a transient session per call. Cold start is bounded
(loopback HTTP + cached process) so this stays simple — no long-lived
client task to plumb.
"""
from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

from proxy import managed_tools

log = logging.getLogger("telecode.docgraph.bridge")


# Track names we registered so we can pop them on stop.
_BRIDGED: list[str] = []


def _import_mcp_client():
    try:
        from mcp import ClientSession  # type: ignore
        from mcp.client.streamable_http import streamablehttp_client  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"mcp client SDK not available: {exc}") from exc
    return ClientSession, streamablehttp_client


async def _open_session(host: str, port: int):
    ClientSession, streamablehttp_client = _import_mcp_client()
    url = f"http://{host}:{port}/mcp"
    stack = AsyncExitStack()
    transport = await stack.enter_async_context(streamablehttp_client(url))
    read, write, *_ = transport
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return session, stack


async def bridge_host(*, host: str, port: int) -> int:
    """Discover tools on the host and register them. Returns count bridged."""
    session, stack = await _open_session(host, port)
    try:
        listed = await session.list_tools()
        tools = getattr(listed, "tools", listed)
        registered: list[str] = []
        for t in tools:
            tool_name = getattr(t, "name", None) or t["name"]
            description = getattr(t, "description", None) or t.get("description", "") or ""
            schema = (
                getattr(t, "inputSchema", None)
                or t.get("inputSchema")
                or {"type": "object", "properties": {}}
            )
            full_name = f"docgraph_{tool_name}"
            mt_schema = {
                "name": full_name,
                "description": (description or "").strip(),
                "input_schema": schema,
            }
            handler = _make_handler(host=host, port=port, tool_name=tool_name)
            primary = _default_primary_arg(schema)
            managed_tools.register(
                full_name, mt_schema, handler,
                strip=[full_name], primary_arg=primary,
            )
            registered.append(full_name)
        _BRIDGED.clear()
        _BRIDGED.extend(registered)
        log.info("docgraph bridge: registered %d tools (port %d)",
                 len(registered), port)
        return len(registered)
    finally:
        await stack.aclose()


def unbridge_host() -> None:
    """Pop all tools we registered from the managed_tools registry."""
    names = list(_BRIDGED)
    _BRIDGED.clear()
    reg = managed_tools._REGISTRY
    for name in names:
        reg.pop(name, None)
    if names:
        log.info("docgraph bridge: unregistered %d tools", len(names))


def _make_handler(*, host: str, port: int, tool_name: str):
    async def _handle(args: dict[str, Any]) -> tuple[str, str]:
        session, stack = await _open_session(host, port)
        try:
            result = await session.call_tool(tool_name, args or {})
            blocks = getattr(result, "content", None) or []
            parts: list[str] = []
            for b in blocks:
                text = getattr(b, "text", None)
                if text is not None:
                    parts.append(text)
                else:
                    parts.append(str(b))
            text_out = "\n".join(parts) if parts else str(result)
            preview = text_out.splitlines()[0][:80] if text_out else "(no output)"
            return preview, text_out
        finally:
            await stack.aclose()
    return _handle


_PRIMARY_HINTS = {"query", "name", "file", "target", "path", "url"}


def _default_primary_arg(schema: dict[str, Any]) -> str:
    props = (schema or {}).get("properties", {}) or {}
    required = (schema or {}).get("required", []) or []
    for k in required:
        if k in _PRIMARY_HINTS:
            return k
    return required[0] if required else (next(iter(props), "") if props else "")
