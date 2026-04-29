"""AIOHTTP routes for task queue. Mimics pythonmagic API."""

from __future__ import annotations

import logging
from aiohttp import web

from services.task.task_manager import get_task_queue, task_to_dict
from services.task.task_registry import register_default_tasks

from proxy import request_log

logger = logging.getLogger("telecode.proxy.api_tasks")

def _log_req(request: web.Request):
    return request_log.new_request(request.method, request.path, inbound_protocol="task-api")

# Ensure default tasks are registered
register_default_tasks()

async def list_types(request: web.Request) -> web.Response:
    rid = _log_req(request)
    types = get_task_queue().get_available_task_types()
    request_log.set_response_preview(rid, types)
    request_log.finish(rid, 200)
    return web.json_response(types)

async def list_all(request: web.Request) -> web.Response:
    """All tasks grouped by status."""
    rid = _log_req(request)
    queue = get_task_queue()
    grouped: dict = {"pending": [], "running": [], "completed": [], "failed": [], "cancelled": []}
    for task in queue.list_tasks():
        grouped.setdefault(task.status.value, []).append(task_to_dict(task))
    request_log.set_response_preview(rid, grouped)
    request_log.finish(rid, 200)
    return web.json_response({"success": True, **grouped})

async def submit_task(request: web.Request) -> web.Response:
    rid = _log_req(request)
    try: data = await request.json()
    except Exception: data = {}
    request_log.set_request_preview(rid, data)
    
    task_type = data.get("task_type")
    params = data.get("params") or {}
    
    if not task_type:
        request_log.finish(rid, 400, "task_type is required")
        return web.json_response({"success": False, "error": "task_type is required"}, status=400)
    
    queue = get_task_queue()
    try:
        task_id = queue.submit_task(
            task_type=task_type,
            params=params,
            metadata=data.get("metadata") or {},
            task_timeout_seconds=data.get("task_timeout_seconds"),
            session_id=data.get("session_id"),
            session_namespace=data.get("session_namespace") or data.get("namespace"),
            absolute_ttl_seconds=data.get("absolute_ttl_seconds"),
        )
        task = queue.get_task(task_id)
        out = {"success": True, "task_id": task_id, "task": task_to_dict(task) if task else None}
        request_log.set_response_preview(rid, out)
        request_log.finish(rid, 200)
        return web.json_response(out)
    except Exception as exc:
        request_log.finish(rid, 400, str(exc))
        return web.json_response({"success": False, "error": str(exc)}, status=400)

async def get_task_status(request: web.Request) -> web.Response:
    rid = _log_req(request)
    task_id = request.match_info["task_id"]
    task = get_task_queue().get_task(task_id)
    if not task:
        request_log.finish(rid, 404, "Task not found")
        return web.json_response({"success": False, "error": "Task not found"}, status=404)
    out = {"success": True, **task_to_dict(task)}
    request_log.set_response_preview(rid, out)
    request_log.finish(rid, 200)
    return web.json_response(out)

async def cancel_task(request: web.Request) -> web.Response:
    rid = _log_req(request)
    task_id = request.match_info["task_id"]
    queue = get_task_queue()
    task = queue.get_task(task_id)
    if not task:
        request_log.finish(rid, 404, "Task not found")
        return web.json_response({"success": False, "error": "Task not found"}, status=404)
    
    with queue.lock:
        if task.future and not task.future.done():
            task.future.cancel()
        from services.task.task_manager import TaskStatus
        task.status = TaskStatus.CANCELLED
    
    out = {"success": True, "task": task_to_dict(task)}
    request_log.set_response_preview(rid, out)
    request_log.finish(rid, 200)
    return web.json_response(out)

def register_routes(app: web.Application):
    app.router.add_get("/api/tasks", list_all)
    app.router.add_get("/api/tasks/types", list_types)
    app.router.add_post("/api/tasks", submit_task)
    app.router.add_get("/api/tasks/{task_id}", get_task_status)
    app.router.add_post("/api/tasks/{task_id}/cancel", cancel_task)
