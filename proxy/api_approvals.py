"""AIOHTTP routes for the approvals inbox (P3).

  GET  /api/approvals                    ?status=pending (default) | approved | rejected | cancelled | all,
                                         ?run_id=… → {approvals, pending_count}
  GET  /api/approvals/{id}               one approval
  POST /api/approvals/{id}/approve       {note?, edited_text?} — edited text replaces what the
                                         approval proposed (a gate hands it to the next step)
  POST /api/approvals/{id}/reject        {note?}

Deciding an approval that is no longer pending answers 409 with its current
row. ``approval.created`` / ``approval.decided`` go out on the global live feed
(``GET /api/events?kinds=approval``).
"""

from __future__ import annotations

import asyncio

from aiohttp import web

from services import approvals
from services.task.safe_paths import validate_id


def _err(msg: str, status: int = 400, **extra) -> web.Response:
    return web.json_response({"success": False, "error": msg, **extra}, status=status)


async def list_approvals(request: web.Request) -> web.Response:
    status = request.query.get("status", "pending")
    if status not in approvals.STATUSES + ("all",):
        return _err(f"status must be one of {approvals.STATUSES + ('all',)}")
    run_id = request.query.get("run_id") or None
    if run_id:
        try:
            validate_id(run_id, "run_id")
        except ValueError as exc:
            return _err(str(exc))
    rows = await asyncio.to_thread(approvals.list_approvals, None if status == "all" else status, 200, run_id)
    n = await asyncio.to_thread(approvals.pending_count)
    return web.json_response({"success": True, "approvals": rows, "pending_count": n})


async def get_approval(request: web.Request) -> web.Response:
    aid = request.match_info["approval_id"]
    try:
        validate_id(aid, "approval_id")
    except ValueError as exc:
        return _err(str(exc))
    ap = await asyncio.to_thread(approvals.get, aid)
    if not ap:
        return _err("approval not found", 404)
    return web.json_response({"success": True, "approval": ap})


def _decide(decision: str):
    async def handler(request: web.Request) -> web.Response:
        aid = request.match_info["approval_id"]
        try:
            validate_id(aid, "approval_id")
        except ValueError as exc:
            return _err(str(exc))
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
        edited = body.get("edited_text")
        if edited is not None and not isinstance(edited, str):
            return _err("edited_text must be a string")
        try:
            ap = await asyncio.to_thread(approvals.decide, aid, decision, by=str(body.get("by") or "web")[:100],
                                         note=body.get("note") if isinstance(body.get("note"), str) else None,
                                         edited_text=edited if decision == "approve" and edited else None)
        except approvals.AlreadyDecided as exc:
            return _err(str(exc), 409, approval=exc.approval)
        except approvals.ApprovalError as exc:
            return _err(str(exc), 404 if "not found" in str(exc) else 400)
        return web.json_response({"success": True, "approval": ap})
    return handler


def register_routes(app: web.Application) -> None:
    app.router.add_get("/api/approvals", list_approvals)
    app.router.add_get("/api/approvals/{approval_id}", get_approval)
    app.router.add_post("/api/approvals/{approval_id}/approve", _decide("approve"))
    app.router.add_post("/api/approvals/{approval_id}/reject", _decide("reject"))
