"""MCP server lifecycle — background start/stop for telecode integration."""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("telecode.mcp_server")

_thread: threading.Thread | None = None


def start_mcp_background(host: str, port: int) -> threading.Thread | None:
    """Start MCP streamable-HTTP server in a daemon thread.

    Mirrors the proxy's start_proxy_background() pattern but uses a thread
    because FastMCP.run() manages its own uvicorn event loop.

    Note: host/port args are accepted for interface consistency with main.py
    but the actual values come from mcp_server.app (reads settings.json).
    """
    global _thread

    try:
        import config as cfg
        if not cfg.get_nested("mcp_server.enabled", False):
            log.info("MCP server disabled in settings")
            return None
    except ImportError:
        pass  # standalone mode, always start

    def _run() -> None:
        from mcp_server.app import mcp_app, register_all
        register_all()

        log.info("MCP server starting on http://%s:%d/mcp", mcp_app.settings.host, mcp_app.settings.port)
        mcp_app.run(transport="streamable-http")

    _thread = threading.Thread(target=_run, daemon=True, name="mcp-server")
    _thread.start()
    log.info("MCP server thread launched (port %d)", port)
    return _thread


def stop_mcp_server() -> None:
    """Best-effort shutdown (daemon thread dies with process)."""
    global _thread
    _thread = None
