"""AIOHTTP routes for pipeline Runs.

Endpoints:
  POST   /api/jobs/:job_id/runs            start a new run (executes the job's pipeline)
                                           body (all optional): {engine, model, is_local, source,
                                           budget: {max_usd, max_tokens, max_seconds}}
                                           — run-level overrides; a step's own
                                           engine/model/is_local/budget wins, blank = inherit
  POST   /api/jobs/:job_id/runs/:run_id/cancel  cancel an active run
  GET    /api/jobs/:job_id/runs            list runs for a job
  GET    /api/runs/:run_id                 get a single run
  GET    /api/runs                         list runs (recent)
  POST   /api/runs/:run_id/steps/:step_id/retry   {mode: retry|retry_clean, budget?} — re-run one
                                           step, then the downstream phases (409 while the run is live)
  GET    /api/runs/:run_id/steps/:step_id/diff    ?attempt=N&path=P&worker=W — unified diff before→after
                                           of an attempt (default the latest) or of map worker W + its file list
  POST   /api/runs/:run_id/steps/:step_id/revert  {attempt?} — restore the job workspace to the
                                           attempt's pre-step snapshot (a safety snapshot is taken first)
"""

from __future__ import annotations

import asyncio
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
                               model=body.get("model") or None,
                               budget=body.get("budget") or None)
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


async def _body(request: web.Request) -> dict:
    try:
        b = await request.json()
    except Exception:
        b = {}
    return b if isinstance(b, dict) else {}


def _int_q(request: web.Request, key: str):
    v = request.query.get(key)
    if v in (None, ""):
        return None
    try:
        return int(v)
    except ValueError:
        raise ValueError(f"{key} must be an integer") from None


async def retry_step(request: web.Request) -> web.Response:
    if (bad := _bad_id(request, "run_id", "step_id")) is not None:
        return bad
    body = await _body(request)
    from services.run import executor
    try:
        run = executor.retry_step(request.match_info["run_id"], request.match_info["step_id"],
                                  mode=str(body.get("mode") or "retry"), budget=body.get("budget") or None)
    except LookupError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except executor.RunBusy as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"run": run})


async def step_diff(request: web.Request) -> web.Response:
    if (bad := _bad_id(request, "run_id", "step_id")) is not None:
        return bad
    from services.run import executor
    try:
        out = await asyncio.get_running_loop().run_in_executor(None, lambda: executor.step_diff(
            request.match_info["run_id"], request.match_info["step_id"], attempt=_int_q(request, "attempt"),
            path=request.query.get("path") or None, worker=_int_q(request, "worker")))
    except LookupError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response(out)


async def revert_step(request: web.Request) -> web.Response:
    if (bad := _bad_id(request, "run_id", "step_id")) is not None:
        return bad
    body = await _body(request)
    from services.run import executor
    try:
        attempt = body.get("attempt")
        out = await asyncio.get_running_loop().run_in_executor(None, lambda: executor.revert_step(
            request.match_info["run_id"], request.match_info["step_id"],
            attempt=int(attempt) if attempt not in (None, "") else None))
    except LookupError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except executor.RunBusy as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response(out)


async def get_artifact(request: web.Request) -> web.Response:
    """A file a step kept under data/runs/<run>/artifacts/<step>/ (download)."""
    if (bad := _bad_id(request, "run_id", "step_id")) is not None:
        return bad
    from services.run.handoff import artifacts_dir
    from services.task.safe_paths import resolve_in
    try:
        root = artifacts_dir(request.match_info["run_id"], request.match_info["step_id"])
        p = resolve_in(root, request.match_info["rel_path"])
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not p.is_file():
        return web.json_response({"error": "Artifact not found"}, status=404)
    return web.FileResponse(p, headers={"Content-Disposition": f'attachment; filename="{p.name}"'})


def _reconcile_on_startup() -> None:
    """Runs / trigger fires left "running" by a previous process → interrupted (B6).

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
        from services.triggers import fire as trigger_fire
        trigger_fire.reconcile_all()
    except Exception:
        logger.exception("startup reconcile of trigger fires failed")


def register_routes(app: web.Application):
    _reconcile_on_startup()
    app.router.add_get("/api/jobs/{job_id}/runs", list_job_runs)
    app.router.add_post("/api/jobs/{job_id}/runs", start_run)
    app.router.add_post("/api/jobs/{job_id}/runs/{run_id}/cancel", cancel_run)
    app.router.add_get("/api/runs", list_runs)
    app.router.add_get("/api/runs/{run_id}", get_run)
    app.router.add_post("/api/runs/{run_id}/steps/{step_id}/retry", retry_step)
    app.router.add_get("/api/runs/{run_id}/steps/{step_id}/diff", step_diff)
    app.router.add_post("/api/runs/{run_id}/steps/{step_id}/revert", revert_step)
    app.router.add_get("/api/runs/{run_id}/steps/{step_id}/artifacts/{rel_path:.+}", get_artifact)
