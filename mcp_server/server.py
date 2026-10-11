"""MCP server lifecycle — background start/stop for telecode integration."""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("telecode.mcp_server")

_thread: threading.Thread | None = None


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def start_mcp_background(host: str, port: int) -> threading.Thread | None:
    """Start MCP streamable-HTTP server in a daemon thread.

    Mirrors the proxy's start_proxy_background() pattern but uses a thread
    because FastMCP.run() manages its own uvicorn event loop.

    Note: host/port args are accepted for interface consistency with main.py
    but the actual values come from mcp_server.app (reads settings.json).
    """
    global _thread

    if is_running():                     # tray toggles call this live; never bind twice
        return _thread

    try:
        import config as cfg
        if not cfg.get_nested("mcp_server.enabled", False):
            log.info("MCP server disabled in settings")
            return None
    except ImportError:
        pass  # standalone mode, always start

    def _run() -> None:
        import sys
        import os
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w")

        import asyncio
        from mcp_server.app import mcp_app, register_all, serve
        register_all()

        log.info("MCP server starting on http://%s:%d/mcp", mcp_app.settings.host, mcp_app.settings.port)
        asyncio.run(serve())
        log.info("MCP server stopped")

    _thread = threading.Thread(target=_run, daemon=True, name="mcp-server")
    _thread.start()
    log.info("MCP server thread launched (port %d)", port)
    return _thread


def stop_mcp_server(timeout: float = 5.0) -> None:
    """Shut the server down and wait for the port to be released. Blocking —
    call off the Qt thread."""
    global _thread
    from mcp_server.app import request_stop
    request_stop()
    t, _thread = _thread, None
    if t is not None and t.is_alive():
        t.join(timeout)
