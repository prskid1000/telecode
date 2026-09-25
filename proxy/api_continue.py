"""Cross-engine continue (P5) — ``services/engine/handover.py``.

  POST /api/sessions/{session_id}/continue
       {engine, model?, is_local?, namespace?, prompt?, run_id?, step_id?}
       → 202 {task_id, from_engine, to_engine, switched_from, package: {json, md}, …}

Builds the neutral context package (latest handoff — of ``run_id``/``step_id``
when given — + workspace diff since the first snapshot + PROGRESS.md) and
starts a fresh conversation on ``engine`` in the same workspace; its lineage row
is ``engine_switch``. 409 while a task runs in the workspace.
"""

from __future__ import annotations

import asyncio

from aiohttp import web

from services.engine import handover
from services.task.safe_paths import validate_id


def _err(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"success": False, "error": msg}, status=status)


async def continue_session(request: web.Request) -> web.Response:
    sid = request.match_info["session_id"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return _err("body must be a JSON object")
    ns = body.get("namespace") or request.query.get("namespace") or None
    try:
        validate_id(sid, "session_id")
        for k in ("run_id", "step_id"):
            if body.get(k):
                validate_id(str(body[k]), k)
        if ns:
            validate_id(str(ns), "namespace")
    except ValueError as exc:
        return _err(str(exc))
    for k in ("model", "prompt"):
        if body.get(k) is not None and not isinstance(body.get(k), str):
            return _err(f"{k} must be a string")
    try:
        res = await asyncio.to_thread(
            handover.continue_session, sid, engine=str(body.get("engine") or ""), model=(body.get("model") or None),
            is_local=bool(body.get("is_local")), ns=ns, prompt=body.get("prompt") or None,
            run_id=body.get("run_id") or None, step_id=body.get("step_id") or None)
    except handover.SessionBusy as exc:
        return _err(str(exc), 409)
    except handover.ContinueError as exc:
        return _err(str(exc), 404 if "not found" in str(exc) else 400)
    return web.json_response({"success": True, **res}, status=202)


def register_routes(app: web.Application) -> None:
    app.router.add_post("/api/sessions/{session_id}/continue", continue_session)
