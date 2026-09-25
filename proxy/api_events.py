"""SSE for Task and Team mode (additions — every polling route still works).

  GET /api/tasks/{task_id}/events   replay the task's events (``?after=<seq>`` or
                                    ``Last-Event-ID``), then live. Frames:
                                    ``event`` (id = seq), ``delta`` (streamed text,
                                    not persisted), ``status``; ``end`` once the
                                    task is terminal, then the stream closes.
  GET /api/runs/{run_id}/events     ``run`` (the record now, then after every
                                    write) + the step tasks' ``event``/``status``/
                                    ``delta`` frames; ``end`` once the run is final.
  GET /api/events?kinds=task,run    global feed: ``task.status`` and ``run.update``
                                    summaries only (sidebars, boards).

``: ping`` comments every 15 s keep proxies from timing the stream out. Ids are
validated (the REST surface has no auth). Built on :mod:`services.bus`, the
same thread-safe bounded-queue pattern as TeleDesign's events.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from aiohttp import web

from services import bus
from services.task.safe_paths import validate_id

logger = logging.getLogger("telecode.proxy.api_events")

TERMINAL_TASK = ("completed", "failed", "cancelled")
ACTIVE_RUN = ("pending", "running")
_CLOSED = (ConnectionResetError, asyncio.CancelledError, RuntimeError)


def _bad(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"success": False, "error": msg}, status=status)


def _ok_id(value: str, kind: str) -> bool:
    try:
        validate_id(value, kind)
        return True
    except ValueError:
        return False


async def _open(request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(headers={
        "Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    await resp.prepare(request)
    await resp.write(b": connected\n\n")
    return resp


def _after(request: web.Request) -> int:
    raw = request.query.get("after") or request.headers.get("Last-Event-ID") or "0"
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _replay_task(task_id: str, after: int):
    """(events with seq > after, current record) — memory for a live task,
    else data/telecode.db."""
    from services.task.task_manager import get_task_queue, task_to_dict
    q = get_task_queue()
    task = q.get_task(task_id)
    if task is not None:
        with q.lock:
            evs = list(task.metadata.get("events") or [])
            rec = task_to_dict(task)
        return [{**e, "seq": i} for i, e in enumerate(evs, start=1) if i > after], rec
    from services.db import task_repo
    rec = task_repo.load_task(task_id, with_events=False)
    if rec is None:
        return None, None
    return task_repo.events(task_id, after=after), rec


async def task_events(request: web.Request) -> web.StreamResponse:
    from services.task.task_manager import task_status_summary
    task_id = request.match_info["task_id"]
    if not _ok_id(task_id, "task_id"):
        return _bad("invalid task_id")
    after = _after(request)
    evs, rec = await asyncio.to_thread(_replay_task, task_id, after)
    if rec is None:
        return _bad("Task not found", 404)
    resp = await _open(request)
    sub = bus.Subscription("task", task_id)
    try:
        # Subscribed before re-reading, so nothing published in between is lost;
        # frames already replayed are skipped by seq.
        evs, rec = await asyncio.to_thread(_replay_task, task_id, after)
        last = after
        for e in evs or []:
            await resp.write(bus.format_sse("event", {**e, "task_id": task_id}, e["seq"]))
            last = e["seq"]
        await resp.write(bus.format_sse("status", task_status_summary(rec)))
        if rec["status"] in TERMINAL_TASK:
            await resp.write(bus.format_sse("end", {"task_id": task_id, "status": rec["status"]}))
            return resp
        while True:
            item = await sub.get(bus.HEARTBEAT_SEC)
            if item is None:
                await resp.write(b": ping\n\n")
                continue
            etype, data = item
            if etype == "event":
                seq = int(data.get("seq") or 0)
                if seq <= last:
                    continue
                last = seq
                await resp.write(bus.format_sse("event", data, seq))
                continue
            await resp.write(bus.format_sse(etype, data))
            if etype == "status" and data.get("status") in TERMINAL_TASK:
                await resp.write(bus.format_sse("end", {"task_id": task_id, "status": data["status"]}))
                break
    except _CLOSED:
        pass
    finally:
        sub.close()
    return resp


async def run_events(request: web.Request) -> web.StreamResponse:
    from services.run.run_store import get_run_store
    run_id = request.match_info["run_id"]
    if not _ok_id(run_id, "run_id"):
        return _bad("invalid run_id")
    run = await asyncio.to_thread(get_run_store().get_run, run_id)
    if not run:
        return _bad("Run not found", 404)
    resp = await _open(request)
    sub = bus.Subscription("run", run_id)
    try:
        run = await asyncio.to_thread(get_run_store().get_run, run_id) or run
        await resp.write(bus.format_sse("run", run))
        if run.get("status") not in ACTIVE_RUN:
            await resp.write(bus.format_sse("end", {"run_id": run_id, "status": run.get("status")}))
            return resp
        while True:
            item = await sub.get(bus.HEARTBEAT_SEC)
            if item is None:
                await resp.write(b": ping\n\n")
                continue
            etype, data = item
            await resp.write(bus.format_sse(etype, data, data.get("seq") if etype == "event" else None))
            if etype == "run" and data.get("status") not in ACTIVE_RUN:
                await resp.write(bus.format_sse("end", {"run_id": run_id, "status": data.get("status")}))
                break
    except _CLOSED:
        pass
    finally:
        sub.close()
    return resp


async def global_events(request: web.Request) -> web.StreamResponse:
    kinds = {k.strip() for k in (request.query.get("kinds") or "task,run").split(",") if k.strip()}
    if not kinds or not kinds <= {"task", "run"}:
        return _bad("kinds must be a comma list of: task, run")
    resp = await _open(request)
    sub = bus.Subscription(kinds=kinds)
    try:
        while True:
            item = await sub.get(bus.HEARTBEAT_SEC)
            if item is None:
                await resp.write(b": ping\n\n")
                continue
            await resp.write(bus.format_sse(*item))
    except _CLOSED:
        pass
    finally:
        sub.close()
    return resp


def register_routes(app: web.Application) -> None:
    app.router.add_get("/api/tasks/{task_id}/events", task_events)
    app.router.add_get("/api/runs/{run_id}/events", run_events)
    app.router.add_get("/api/events", global_events)
