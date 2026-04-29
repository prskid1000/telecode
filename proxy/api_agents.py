"""AIOHTTP routes for Agent management."""

from __future__ import annotations

import logging
from aiohttp import web
from pathlib import Path

from services.agent.agent_manager import get_agent_manager

logger = logging.getLogger("telecode.proxy.api_agents")

async def list_agents(request: web.Request) -> web.Response:
    agents = get_agent_manager().list_agents()
    return web.json_response({"agents": agents})

async def create_agent(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    
    name = data.get("name")
    if not name:
        return web.json_response({"error": "Name is required"}, status=400)
    
    instructions = data.get("instructions", "")
    soul = data.get("soul", "")
    agent = get_agent_manager().create_agent(name, instructions, soul=soul)
    return web.json_response({"agent": agent})

async def get_agent(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    agent = get_agent_manager().get_agent(agent_id)
    if not agent:
        return web.json_response({"error": "Agent not found"}, status=404)
    return web.json_response({"agent": agent})

async def update_agent(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    
    agent = get_agent_manager().update_agent(agent_id, data)
    if not agent:
        return web.json_response({"error": "Agent not found"}, status=404)
    return web.json_response({"agent": agent})

async def delete_agent(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    success = get_agent_manager().delete_agent(agent_id)
    if not success:
        return web.json_response({"error": "Agent not found"}, status=404)
    return web.json_response({"success": True})

async def list_agent_files(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    files = get_agent_manager().list_files(agent_id)
    return web.json_response({"files": files})

async def upload_agent_files(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    reader = await request.multipart()
    count = 0
    while True:
        part = await reader.next()
        if part is None: break
        if part.name != "files": continue
        
        filename = part.filename
        if not filename: continue
        
        content = await part.read()
        get_agent_manager().save_file(agent_id, filename, content)
        count += 1
    
    return web.json_response({"success": True, "count": count})

async def get_agent_file(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    rel_path = request.match_info["rel_path"]
    p = get_agent_manager().get_file_path(agent_id, rel_path)
    if not p:
        return web.json_response({"error": "File not found"}, status=404)
    return web.FileResponse(p)

async def delete_agent_file(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    rel_path = request.match_info["rel_path"]
    success = get_agent_manager().delete_file(agent_id, rel_path)
    if not success:
        return web.json_response({"error": "File not found"}, status=404)
    return web.json_response({"success": True})

async def get_agent_internal(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    if not get_agent_manager().get_agent(agent_id):
        return web.json_response({"error": "Agent not found"}, status=404)
    files = get_agent_manager().get_internal_files(agent_id)
    return web.json_response({"files": files})

async def update_agent_internal(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    files = data.get("files") or {}
    ok = get_agent_manager().set_internal_files(agent_id, files)
    if not ok:
        return web.json_response({"error": "Agent not found"}, status=404)

    # If HEARTBEAT.md changed, reconcile HB jobs synchronously so the sidebar
    # picks up changes on its next refresh.
    reconcile_summary = None
    if "HEARTBEAT.md" in files:
        try:
            from services.heartbeat.reconcile import reconcile_agent
            reconcile_summary = reconcile_agent(agent_id)
        except Exception as exc:
            logger.exception(f"reconcile_agent failed for {agent_id}: {exc}")
            reconcile_summary = {"errors": [{"msg": str(exc)}]}

    return web.json_response({"success": True, "reconcile": reconcile_summary})


async def validate_agent_heartbeat(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    if not get_agent_manager().get_agent(agent_id):
        return web.json_response({"error": "Agent not found"}, status=404)

    try:
        data = await request.json()
    except Exception:
        data = {}

    text = data.get("text")
    if text is None:
        # default: validate the file currently on disk
        files = get_agent_manager().get_internal_files(agent_id)
        text = files.get("HEARTBEAT.md", "") or ""

    from services.heartbeat.parser import parse, next_fires
    parsed = parse(text)
    out = parsed.to_dict()
    # decorate each entry with next 3 fire times
    for e_obj, e_dict in zip(parsed.entries, out["entries"]):
        try:
            e_dict["next_fires"] = next_fires(e_obj, count=3)
        except Exception as exc:
            e_dict["next_fires_error"] = str(exc)
    return web.json_response(out)


async def reconcile_agent_heartbeat(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    if not get_agent_manager().get_agent(agent_id):
        return web.json_response({"error": "Agent not found"}, status=404)
    from services.heartbeat.reconcile import reconcile_agent
    summary = reconcile_agent(agent_id)
    return web.json_response(summary)

def register_routes(app: web.Application):
    app.router.add_get("/api/agents", list_agents)
    app.router.add_post("/api/agents", create_agent)
    app.router.add_get("/api/agents/{agent_id}", get_agent)
    app.router.add_put("/api/agents/{agent_id}", update_agent)
    app.router.add_delete("/api/agents/{agent_id}", delete_agent)
    app.router.add_get("/api/agents/{agent_id}/internal", get_agent_internal)
    app.router.add_put("/api/agents/{agent_id}/internal", update_agent_internal)
    app.router.add_post("/api/agents/{agent_id}/heartbeat/validate", validate_agent_heartbeat)
    app.router.add_post("/api/agents/{agent_id}/heartbeat/reconcile", reconcile_agent_heartbeat)
    app.router.add_get("/api/agents/{agent_id}/files", list_agent_files)
    app.router.add_post("/api/agents/{agent_id}/files", upload_agent_files)
    app.router.add_get("/api/agents/{agent_id}/files/{rel_path:.*}", get_agent_file)
    app.router.add_delete("/api/agents/{agent_id}/files/{rel_path:.*}", delete_agent_file)
