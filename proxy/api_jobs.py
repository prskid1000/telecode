"""AIOHTTP routes for Job management."""

from __future__ import annotations

import logging
import aiohttp
from aiohttp import web
from pathlib import Path
from proxy.media_fetch import MediaFetchError, fetch_media_bytes
from services.job.job_manager import get_job_manager

# Job attachments are documents, not media — a far smaller cap than video.
MAX_JOB_FILE_BYTES = 64 * 1024 * 1024

logger = logging.getLogger("telecode.proxy.api_jobs")

async def list_jobs(request: web.Request) -> web.Response:
    kind = request.query.get("kind")  # "user" | "heartbeat" | None
    include_archived = request.query.get("include_archived") in ("1", "true", "yes")
    jobs = get_job_manager().list_jobs(kind=kind, include_archived=include_archived)
    return web.json_response({"jobs": jobs})

async def create_job(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    
    job = get_job_manager().create_job(data)
    return web.json_response({"job": job})

async def get_job(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    job = get_job_manager().get_job(job_id)
    if not job:
        return web.json_response({"error": "Job not found"}, status=404)
    return web.json_response({"job": job})

async def update_job(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    
    job = get_job_manager().update_job(job_id, data)
    if not job:
        return web.json_response({"error": "Job not found"}, status=404)
    return web.json_response({"job": job})

async def delete_job(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    success = get_job_manager().delete_job(job_id)
    if not success:
        return web.json_response({"error": "Job not found"}, status=404)
    return web.json_response({"success": True})

async def list_job_files(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    files = get_job_manager().list_files(job_id)
    return web.json_response({"files": files})

async def upload_job_files(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    reader = await request.multipart()
    count = 0
    while True:
        part = await reader.next()
        if part is None: break
        if part.name != "files": continue
        
        filename = part.filename
        if not filename: continue
        
        content = await part.read()
        get_job_manager().save_file(job_id, filename, content)
        count += 1
    
    return web.json_response({"success": True, "count": count})

async def fetch_job_file(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    
    url = data.get("url")
    name = data.get("name")
    if not url or not name:
        return web.json_response({"error": "URL and Name are required"}, status=400)
    
    # Fetched through media_fetch, not a bare session.get: this endpoint takes a
    # URL from an unauthenticated caller and the proxy runs on the user's
    # machine, so an unguarded fetch reaches localhost, the LAN and cloud
    # metadata on their behalf. media_fetch enforces the scheme, rejects
    # non-public addresses on every DNS answer and every redirect hop, and caps
    # the body while streaming.
    try:
        content = await fetch_media_bytes(url, max_bytes=MAX_JOB_FILE_BYTES)
    except MediaFetchError as e:
        return web.json_response({"error": f"URL rejected: {e}"}, status=400)
    except Exception as e:  # noqa: BLE001 - surface anything unexpected as 500
        return web.json_response({"error": f"Error fetching URL: {e}"}, status=500)

    try:
        get_job_manager().save_file(job_id, name, content)
    except ValueError as e:
        # Path containment — `name` is caller-supplied.
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response({"success": True})

async def get_job_file(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    rel_path = request.match_info["rel_path"]
    p = get_job_manager().get_file_path(job_id, rel_path)
    if not p:
        return web.json_response({"error": "File not found"}, status=404)
    return web.FileResponse(p)

async def delete_job_file(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    rel_path = request.match_info["rel_path"]
    success = get_job_manager().delete_file(job_id, rel_path)
    if not success:
        return web.json_response({"error": "File not found"}, status=404)
    return web.json_response({"success": True})

def register_routes(app: web.Application):
    app.router.add_get("/api/jobs", list_jobs)
    app.router.add_post("/api/jobs", create_job)
    app.router.add_get("/api/jobs/{job_id}", get_job)
    app.router.add_put("/api/jobs/{job_id}", update_job)
    app.router.add_delete("/api/jobs/{job_id}", delete_job)
    app.router.add_get("/api/jobs/{job_id}/files", list_job_files)
    app.router.add_post("/api/jobs/{job_id}/files", upload_job_files)
    app.router.add_post("/api/jobs/{job_id}/files/fetch", fetch_job_file)
    app.router.add_get("/api/jobs/{job_id}/files/{rel_path:.*}", get_job_file)
    app.router.add_delete("/api/jobs/{job_id}/files/{rel_path:.*}", delete_job_file)
