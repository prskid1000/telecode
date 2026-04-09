"""Standalone entry point: python -m mcp_server"""
from __future__ import annotations

import os
import sys

# Ensure project root is on sys.path so 'config' and 'mcp_server' resolve
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server.app import mcp_app
import mcp_server.tools  # noqa: F401 — triggers auto-discovery

try:
    import config as cfg
    host = cfg.get_nested("mcp_server.host", "127.0.0.1")
    port = int(cfg.get_nested("mcp_server.port", 1236))
except Exception:
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "1236"))

if __name__ == "__main__":
    print(f"Starting telecode-audio MCP server on http://{host}:{port}/mcp")
    mcp_app.run(transport="streamable-http", host=host, port=port)
