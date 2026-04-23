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
    agent = get_agent_manager().create_agent(name, instructions)
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

def register_routes(app: web.Application):
    app.router.add_get("/api/agents", list_agents)
    app.router.add_post("/api/agents", create_agent)
    app.router.add_get("/api/agents/{agent_id}", get_agent)
    app.router.add_put("/api/agents/{agent_id}", update_agent)
    app.router.add_delete("/api/agents/{agent_id}", delete_agent)
    app.router.add_get("/api/agents/{agent_id}/files", list_agent_files)
    app.router.add_post("/api/agents/{agent_id}/files", upload_agent_files)
    app.router.add_get("/api/agents/{agent_id}/files/{rel_path:.*}", get_agent_file)
    app.router.add_delete("/api/agents/{agent_id}/files/{rel_path:.*}", delete_agent_file)
