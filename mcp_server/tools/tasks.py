"""MCP tools for task management."""

from __future__ import annotations

from typing import Any, Dict, Optional
from mcp_server.app import mcp_app
import config
from services.task.task_manager import get_task_queue, task_to_dict

def _check_enabled():
    if not config.enable_session_tools():
        raise RuntimeError("Session tools are disabled in settings.json (proxy.enable_session_tools)")

@mcp_app.tool()
async def task_submit(
    task_type: str,
    params: Dict[str, Any],
    session_id: Optional[str] = None,
    namespace: Optional[str] = None
) -> Dict[str, Any]:
    """Submit a background task."""
    _check_enabled()
    queue = get_task_queue()
    task_id = queue.submit_task(
        task_type=task_type,
        params=params,
        session_id=session_id,
        session_namespace=namespace
    )
    task = queue.get_task(task_id)
    return task_to_dict(task) if task else {"task_id": task_id}

@mcp_app.tool()
async def task_status(task_id: str) -> Dict[str, Any]:
    """Check the status of a submitted task."""
    _check_enabled()
    task = get_task_queue().get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    return task_to_dict(task)

@mcp_app.tool()
async def task_list_types() -> Dict[str, Any]:
    """List all registered task types and their schemas."""
    _check_enabled()
    return get_task_queue().get_available_task_types()
