"""AIOHTTP routes for pipeline Runs.

Endpoints:
  POST   /api/jobs/:job_id/runs            start a new run (executes the job's pipeline)
  POST   /api/jobs/:job_id/runs/:run_id/cancel  cancel an active run
  GET    /api/jobs/:job_id/runs            list runs for a job
  GET    /api/runs/:run_id                 get a single run
  GET    /api/runs                         list runs (recent)
"""

from __future__ import annotations

import logging
from aiohttp import web

from services.run.run_store import get_run_store
from services.job.job_manager import get_job_manager

logger = logging.getLogger("telecode.proxy.api_runs")


async def list_job_runs(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    runs = get_run_store().list_runs(job_id=job_id)
    return web.json_response({"runs": runs})


async def list_runs(request: web.Request) -> web.Response:
    runs = get_run_store().list_runs()
    return web.json_response({"runs": runs})


async def get_run(request: web.Request) -> web.Response:
    run_id = request.match_info["run_id"]
    run = get_run_store().get_run(run_id)
    if not run:
        return web.json_response({"error": "Run not found"}, status=404)
    return web.json_response({"run": run})


async def start_run(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    job = get_job_manager().get_job(job_id)
    if not job:
        return web.json_response({"error": "Job not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        body = {}

    is_local = bool(body.get("is_local", False))
    source = body.get("source") or "user"

    from services.run.executor import start_run as exec_start
    try:
        run = await exec_start(job=job, is_local=is_local, source=source)
    except Exception as exc:
        logger.exception(f"start_run failed for job {job_id}: {exc}")
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"run": run})


async def cancel_run(request: web.Request) -> web.Response:
    run_id = request.match_info["run_id"]
    from services.run.executor import cancel_run as exec_cancel
    ok = exec_cancel(run_id)
    if not ok:
        return web.json_response({"error": "Run not found or already finished"}, status=404)
    return web.json_response({"success": True})


def register_routes(app: web.Application):
    app.router.add_get("/api/jobs/{job_id}/runs", list_job_runs)
    app.router.add_post("/api/jobs/{job_id}/runs", start_run)
    app.router.add_post("/api/jobs/{job_id}/runs/{run_id}/cancel", cancel_run)
    app.router.add_get("/api/runs", list_runs)
    app.router.add_get("/api/runs/{run_id}", get_run)
