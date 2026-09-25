"""AIOHTTP routes for pipeline Runs.

Endpoints:
  POST   /api/jobs/:job_id/runs            start a new run (executes the job's pipeline)
                                           body (all optional): {engine, model, is_local, source}
                                           — run-level overrides; a step's own
                                           engine/model/is_local wins, blank = inherit
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
from services.task.safe_paths import validate_id

logger = logging.getLogger("telecode.proxy.api_runs")


def _bad_id(request: web.Request, *keys: str):
    for k in keys:
        try:
            validate_id(request.match_info[k], k)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
    return None


async def list_job_runs(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    if (bad := _bad_id(request, "job_id")) is not None:
        return bad
    runs = get_run_store().list_runs(job_id=job_id)
    return web.json_response({"runs": runs})


async def list_runs(request: web.Request) -> web.Response:
    runs = get_run_store().list_runs()
    return web.json_response({"runs": runs})


async def get_run(request: web.Request) -> web.Response:
    run_id = request.match_info["run_id"]
    if (bad := _bad_id(request, "run_id")) is not None:
        return bad
    run = get_run_store().get_run(run_id)
    if not run:
        return web.json_response({"error": "Run not found"}, status=404)
    return web.json_response({"run": run})


async def start_run(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    if (bad := _bad_id(request, "job_id")) is not None:
        return bad
    job = get_job_manager().get_job(job_id)
    if not job:
        return web.json_response({"error": "Job not found"}, status=404)

    try:
        body = await request.json()
    except Exception:
        body = {}

    if not isinstance(body, dict):
        body = {}
    # Absent / null / "" = not overridden at run level (steps and the agent decide).
    raw_local = body.get("is_local")
    is_local = None if raw_local in (None, "") else bool(raw_local)
    source = body.get("source") or "user"

    from services.run.executor import start_run as exec_start
    try:
        run = await exec_start(job=job, is_local=is_local, source=source,
                               engine=body.get("engine") or None,
                               model=body.get("model") or None)
    except Exception as exc:
        logger.exception(f"start_run failed for job {job_id}: {exc}")
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"run": run})


async def cancel_run(request: web.Request) -> web.Response:
    run_id = request.match_info["run_id"]
    if (bad := _bad_id(request, "run_id")) is not None:
        return bad
    from services.run.executor import cancel_run as exec_cancel
    ok = exec_cancel(run_id)
    if not ok:
        return web.json_response({"error": "Run not found or already finished"}, status=404)
    return web.json_response({"success": True})


def _reconcile_on_startup() -> None:
    """Runs/heartbeat state left "running" by a previous process → interrupted (B6).

    Called once when the proxy app is built. Only records whose task is not
    alive in this process's queue (and runs without a live driver) are
    touched, so a proxy restart inside a running telecode is safe. Tasks
    first: data/telecode.db rows left pending/running become failed
    ("interrupted: telecode restarted …")."""
    try:
        from services.task.task_manager import get_task_queue
        get_task_queue().reconcile_persisted()
    except Exception:
        logger.exception("startup reconcile of tasks failed")
    try:
        from services.run.executor import reconcile_orphaned_runs
        reconcile_orphaned_runs()
    except Exception:
        logger.exception("startup reconcile of runs failed")
    try:
        from services.heartbeat import state as hb_state
        from services.task.task_manager import get_task_queue
        hb_state.reconcile_interrupted(get_task_queue().is_active)
    except Exception:
        logger.exception("startup reconcile of heartbeat state failed")


def register_routes(app: web.Application):
    _reconcile_on_startup()
    app.router.add_get("/api/jobs/{job_id}/runs", list_job_runs)
    app.router.add_post("/api/jobs/{job_id}/runs", start_run)
    app.router.add_post("/api/jobs/{job_id}/runs/{run_id}/cancel", cancel_run)
    app.router.add_get("/api/runs", list_runs)
    app.router.add_get("/api/runs/{run_id}", get_run)
