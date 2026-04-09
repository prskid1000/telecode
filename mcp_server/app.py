"""FastMCP application instance — tools register by importing this module."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp_app = FastMCP("telecode-audio", stateless_http=True)
