"""MCP tools for session management."""

from __future__ import annotations

from typing import Any, Dict, Optional
from mcp_server.app import mcp_app
import config
from services.session import session_store

def _check_enabled():
    if not config.enable_session_tools():
        raise RuntimeError("Session tools are disabled in settings.json (proxy.enable_session_tools)")

@mcp_app.tool()
async def session_create(
    session_id: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    namespace: Optional[str] = None
) -> str:
    """Create a new session to carry state across tasks."""
    _check_enabled()
    meta = session_store.create(session_id=session_id, data=data, namespace=namespace)
    return f"Session created: {meta['session_id']}"

@mcp_app.tool()
async def session_get(
    session_id: str,
    namespace: Optional[str] = None
) -> Dict[str, Any]:
    """Retrieve session metadata and data."""
    _check_enabled()
    meta = session_store.get(session_id, namespace=namespace)
    if not meta:
        raise ValueError(f"Session {session_id} not found")
    return meta

@mcp_app.tool()
async def session_list(namespace: Optional[str] = None) -> list[Dict[str, Any]]:
    """List all active sessions."""
    _check_enabled()
    return session_store.list_all(namespace=namespace)

@mcp_app.tool()
async def session_delete(session_id: str, namespace: Optional[str] = None) -> str:
    """Delete a session and all its files."""
    _check_enabled()
    if session_store.delete(session_id, namespace=namespace):
        return f"Session {session_id} deleted"
    return f"Session {session_id} not found"

@mcp_app.tool()
async def session_upload_file(
    session_id: str,
    path: str,
    content: str,
    namespace: Optional[str] = None
) -> Dict[str, Any]:
    """Upload/write a file to the session workspace."""
    _check_enabled()
    # Content is assumed to be UTF-8 string for this tool
    info = session_store.write_file(session_id, path, content.encode("utf-8"), namespace=namespace)
    return info

@mcp_app.tool()
async def session_read_file(
    session_id: str,
    path: str,
    namespace: Optional[str] = None
) -> str:
    """Read a file's content from the session workspace."""
    _check_enabled()
    dest = session_store.resolve_file(session_id, path, namespace=namespace)
    return dest.read_text(encoding="utf-8", errors="replace")

@mcp_app.tool()
async def session_files_list(
    session_id: str,
    namespace: Optional[str] = None
) -> list[Dict[str, Any]]:
    """List all files in the session workspace."""
    _check_enabled()
    return session_store.list_files(session_id, namespace=namespace)
