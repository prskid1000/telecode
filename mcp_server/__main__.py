"""Standalone entry point: python -m mcp_server"""
from __future__ import annotations

import os
import sys

# Ensure project root is on sys.path so 'config' and 'mcp_server' resolve
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server.app import mcp_app, register_all
register_all()

if __name__ == "__main__":
    print(f"Starting telecode-audio MCP server on http://{mcp_app.settings.host}:{mcp_app.settings.port}/mcp")
    mcp_app.run(transport="streamable-http")
