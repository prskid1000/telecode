"""Telecode-side DocGraph integration.

Supervises `docgraph` subprocesses (index, watch, serve, daemon, mcp) and
bridges MCP tools into the proxy's managed-tools registry. See CLAUDE.md
"DocGraph integration" for the full design.
"""
from __future__ import annotations
