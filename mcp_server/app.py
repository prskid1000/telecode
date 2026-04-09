"""FastMCP application instance — tools register by importing this module."""
from __future__ import annotations

import os
from mcp.server.fastmcp import FastMCP

try:
    import config as _cfg
    _host = _cfg.get_nested("mcp_server.host", "127.0.0.1")
    _port = int(_cfg.get_nested("mcp_server.port", 1236))
except Exception:
    _host = os.environ.get("MCP_HOST", "127.0.0.1")
    _port = int(os.environ.get("MCP_PORT", "1236"))

mcp_app = FastMCP("telecode-audio", stateless_http=True, host=_host, port=_port)


def register_all() -> None:
    """Import all drop-in modules (tools, resources, prompts)."""
    import mcp_server.tools      # noqa: F401
    import mcp_server.resources  # noqa: F401
    import mcp_server.prompts    # noqa: F401
