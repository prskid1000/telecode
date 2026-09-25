"""AIOHTTP routes for TeleDesign — export (docs/teledesign-contract.md §8).

    POST /api/design/projects/{pid}/export/{kind}         {file?, board_ids?, options} → {"job_id", "job"}
    GET  /api/design/projects/{pid}/export/jobs            → {"jobs": [...]}
    GET  /api/design/projects/{pid}/export/jobs/{job_id}   → {status, progress, download_url?, error?, flags?, …}
    GET  /api/design/projects/{pid}/export/download/{job_id}   streams the result (attachment)
    GET  /api/design/projects/{pid}/export/handoff/prompt  ?file=  → {"prompt", "path", "job_id", "download_url"}

kind ∈ html · zip · pdf · pptx · png · mp4 · handoff.

The REST surface has no auth: ids are validated (`store.valid_id`), `file` / `board_ids`
are resolved inside the project by `export.start`, the POST requires
`Content-Type: application/json` and refuses a foreign `Origin` (a preview page on the
preview port must never be able to start work here).
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

import config
from services.design import export, store

log = logging.getLogger("telecode.proxy.api_design_export")

_PREFIX = "/api/design/projects/{project_id}/export"


def _allowed_origin(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    if not origin:
        return True
    port = int(config.get_nested("proxy.port", 1235))
    return origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")


def _err(msg: str, status: int) -> web.Response:
    return web.json_response({"error": msg}, status=status)


def _pid(request: web.Request) -> str:
    return request.match_info["project_id"]


async def start_export(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not store.valid_id(pid):
        return _err("Invalid project id", 400)
    if not _allowed_origin(request):
        return _err("Forbidden origin", 403)
    if request.content_type != "application/json":
        return _err("Expected application/json", 415)
    try:
        body = await request.json()
    except Exception:
        return _err("Invalid JSON", 400)
    if not isinstance(body, dict):
        return _err("Invalid JSON", 400)
    kind = request.match_info["kind"]
    try:
        job = export.start(pid, kind, body)
    except export.ExportError as exc:
        status = 404 if "not found" in str(exc) and "project" in str(exc) else 400
        return _err(str(exc), status)
    return web.json_response({"job_id": job["id"], "job": export.public_job(job)})


def _own_job(request: web.Request):
    pid = _pid(request)
    job = export.get_job(request.match_info["job_id"]) if store.valid_id(pid) else None
    return job if job and job.get("pid") == pid else None


async def get_job(request: web.Request) -> web.Response:
    job = _own_job(request)
    if not job:
        return _err("Export job not found", 404)
    return web.json_response(export.public_job(job))


async def list_jobs(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not store.get_project(pid):
        return _err("Project not found", 404)
    return web.json_response({"jobs": await asyncio.to_thread(export.list_jobs, pid)})


async def download(request: web.Request) -> web.StreamResponse:
    job = _own_job(request)
    path = export.result_path(job) if job else None
    if not path:
        return _err("Export not ready", 404)
    name = job.get("filename") or path.name
    resp = web.FileResponse(path)
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


async def handoff_prompt(request: web.Request) -> web.Response:
    pid = _pid(request)
    if not store.get_project(pid):
        return _err("Project not found", 404)
    body = {}
    if request.query.get("file"):
        body["file"] = request.query["file"]
    try:
        job = await export.run_sync(pid, "handoff", body)
    except export.ExportError as exc:
        return _err(str(exc), 400)
    if job["status"] != "done":
        return _err(job.get("error") or "Handoff failed", 500)
    pj = export.public_job(job)
    return web.json_response({"prompt": job.get("prompt"), "path": job.get("bundle_path"),
                              "job_id": job["id"], "download_url": pj.get("download_url")})


def register_routes(app: web.Application):
    app.router.add_get(_PREFIX + "/jobs", list_jobs)
    app.router.add_get(_PREFIX + "/jobs/{job_id}", get_job)
    app.router.add_get(_PREFIX + "/download/{job_id}", download)
    app.router.add_get(_PREFIX + "/handoff/prompt", handoff_prompt)
    app.router.add_post(_PREFIX + "/{kind}", start_export)
