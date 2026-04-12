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

# Patch run_streamable_http_async to inject CORS middleware when configured.

async def _run_with_cors() -> None:  # pragma: no cover
    """Wrap the Starlette app with CORSMiddleware before starting uvicorn."""
    import uvicorn

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
    server = uvicorn.Server(config)
    await server.serve()


if _cors_origins:
    mcp_app.run_streamable_http_async = _run_with_cors  # type: ignore[assignment]


def register_all() -> None:
    """Import all drop-in modules (tools, resources, prompts)."""
    import mcp_server.tools      # noqa: F401
    import mcp_server.resources  # noqa: F401
    import mcp_server.prompts    # noqa: F401
