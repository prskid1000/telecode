"""FastMCP application instance — tools register by importing this module."""
from __future__ import annotations

import os
from mcp.server.fastmcp import FastMCP

try:
    import config as _cfg
    _host = _cfg.get_nested("mcp_server.host", "127.0.0.1")
    _port = int(_cfg.get_nested("mcp_server.port", 1236))
    _cors_origins: list[str] = _cfg.get_nested("mcp_server.cors_origins", [])
except Exception:
    _host = os.environ.get("MCP_HOST", "127.0.0.1")
    _port = int(os.environ.get("MCP_PORT", "1236"))
    _cors_origins = []

mcp_app = FastMCP("telecode", stateless_http=True, host=_host, port=_port)

# Our own uvicorn runner (FastMCP's builds the same server but keeps no handle):
# it adds CORS when configured, and `request_stop()` lets the tray switch the
# server off live instead of waiting for a telecode restart.
_server = None


async def serve() -> None:  # pragma: no cover
    """Build the Starlette app (+ CORS) and serve until `request_stop()`."""
    global _server
    import uvicorn
    import sys

    # Fix for pythonw.exe where sys.stdout/stderr are None, causing uvicorn's
    # default logging formatter (which checks isatty()) to crash.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    # A session manager can only run once and FastMCP caches it, so drop the
    # previous one — otherwise a second start (tray off → on) fails.
    mcp_app._session_manager = None
    starlette_app = mcp_app.streamable_http_app()

    if _cors_origins:
        from starlette.middleware.cors import CORSMiddleware
        starlette_app.add_middleware(
            CORSMiddleware,
            allow_origins=_cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials=True,
            # Required for Office add-ins running in browser iframes
            expose_headers=["*"],
        )

    config = uvicorn.Config(
        starlette_app,
        host=mcp_app.settings.host,
        port=mcp_app.settings.port,
        log_level=mcp_app.settings.log_level.lower(),
    )
    _server = uvicorn.Server(config)
    try:
        await _server.serve()
    finally:
        _server = None


def request_stop() -> None:
    """Ask the running server to shut down (uvicorn checks this flag)."""
    if _server is not None:
        _server.should_exit = True


def register_all() -> None:
    """Import all drop-in modules (tools, resources, prompts)."""
    import mcp_server.tools      # noqa: F401
    import mcp_server.resources  # noqa: F401
    import mcp_server.prompts    # noqa: F401
