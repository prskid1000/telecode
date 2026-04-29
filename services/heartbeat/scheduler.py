"""Heartbeat scheduler — async tick loop that fires HB jobs on cron schedule.

Per tick (default 60s):
  for each agent:
    parse HEARTBEAT.md
    reconcile HB Jobs (so the sidebar reflects the latest YAML)
    for each due, enabled entry:
      fire(agent, entry) — submit task on resolved workspace; ephemeral if applicable
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from croniter import croniter

import config
from services.agent.agent_manager import get_agent_manager
from services.job.job_manager import get_job_manager
from services.session import session_store
from services.task.task_manager import TaskStatus, get_task_queue
from services.heartbeat import state as hb_state
from services.heartbeat.parser import ScheduleEntry, parse
from services.heartbeat.reconcile import reconcile_agent

logger = logging.getLogger("telecode.services.heartbeat.scheduler")

EPHEMERAL_NS = "heartbeat"


class HeartbeatScheduler:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._tracked_ephemeral: Dict[str, Dict[str, Any]] = {}
        # task_id -> {"session_id": str, "agent_id": str, "entry_name": str}

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running():
            return
        if not config.heartbeat_enabled():
            logger.info("heartbeat.enabled=false — scheduler not started")
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="heartbeat-scheduler")
        logger.info(f"Heartbeat scheduler started (tick={config.heartbeat_tick_seconds()}s)")

    async def stop(self) -> None:
        if not self.is_running():
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            self._task.cancel()
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.get_running_loop().run_in_executor(None, self._tick)
            except Exception as exc:
                logger.exception(f"Heartbeat tick failed: {exc}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=config.heartbeat_tick_seconds())
            except asyncio.TimeoutError:
                pass

    # ── Tick logic (sync) ────────────────────────────────────────────────────

    def _tick(self) -> None:
        agents = get_agent_manager().list_agents()
        if not agents:
            return

        max_fires = config.heartbeat_max_concurrent_fires()
        fires_this_tick = 0
        all_known_keys = set()

        for agent in agents:
            agent_id = agent["id"]

            # Reconcile YAML → HB Jobs (sidebar parity)
            try:
                reconcile_agent(agent_id)
            except Exception as exc:
                logger.exception(f"reconcile_agent({agent_id}): {exc}")

            files = get_agent_manager().get_internal_files(agent_id)
            text = files.get("HEARTBEAT.md", "") or ""
            parsed = parse(text)

            for entry in parsed.entries:
                all_known_keys.add(f"{agent_id}:{entry.name}")
                if not entry.enabled:
                    continue
                if fires_this_tick >= max_fires:
                    continue
                if self._is_due(agent_id, entry):
                    try:
                        self._fire(agent_id, entry)
                        fires_this_tick += 1
                    except Exception as exc:
                        logger.exception(f"fire({agent_id}, {entry.name}) failed: {exc}")

        # Sweep stale state keys (entries deleted from YAML)
        try:
            removed = hb_state.prune_orphans(all_known_keys)
            if removed:
                logger.info(f"Pruned {removed} orphan heartbeat state entries")
        except Exception:
            pass

        # Cleanup completed ephemeral sessions
        self._sweep_ephemeral()

    def _is_due(self, agent_id: str, entry: ScheduleEntry) -> bool:
        st = hb_state.get(agent_id, entry.name)
        last_run = st.get("last_run")

        now = datetime.now(timezone.utc)
        if last_run:
            try:
                last_dt = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
            except Exception:
                last_dt = now  # malformed; treat as just-fired
        else:
            # No prior run — anchor to now so first fire is at next scheduled time,
            # not immediately. Backfill is intentionally skipped.
            last_dt = now

        try:
            it = croniter(entry.cron, last_dt)
            next_fire = it.get_next(datetime).astimezone(timezone.utc)
        except Exception:
            return False

        # Don't fire twice within min_fire_gap
        if last_run:
            gap = (now - last_dt).total_seconds()
            if gap < config.heartbeat_min_fire_gap_seconds():
                return False

        return next_fire <= now

    def _fire(self, agent_id: str, entry: ScheduleEntry) -> None:
        agent = get_agent_manager().get_agent(agent_id)
        if not agent:
            return

        # Resolve workspace
        if entry.workspace == "ephemeral":
            ws_id = f"hb-{agent_id[:8]}-{entry.name}-{uuid.uuid4().hex[:6]}"
            session_store.create(
                session_id=ws_id,
                namespace=EPHEMERAL_NS,
                data={
                    "name": f"hb-{agent.get('name','')}-{entry.name}",
                    "ephemeral": True,
                    "owner_agent": agent_id,
                    "heartbeat_entry": entry.name,
                },
                session_idle_timeout_seconds=config.heartbeat_ephemeral_ttl_seconds(),
                absolute_ttl_seconds=config.heartbeat_ephemeral_ttl_seconds(),
            )
            session_namespace = EPHEMERAL_NS
        else:
            ws_id = entry.workspace_id
            session_namespace = None
            if not ws_id or not session_store.exists(ws_id, namespace=session_namespace):
                logger.warning(
                    f"Heartbeat {agent_id}/{entry.name}: persistent workspace_id "
                    f"'{ws_id}' missing — skipping fire"
                )
                hb_state.mark_fired(agent_id, entry.name, task_id=None)
                hb_state.mark_finished(agent_id, entry.name, "failed", task_id=None)
                return

        # Resolve job_id (the HB Job created by reconciliation)
        hb_job = get_job_manager().find_heartbeat_job(agent_id, entry.name)
        job_id = hb_job["id"] if hb_job else None

        task_type_map = {"claude_code": "CLAUDE_CODE", "gemini": "GEMINI"}
        task_type = task_type_map.get(entry.engine, "CLAUDE_CODE")

        params = {
            "prompt": entry.prompt,
            "is_local": False,
            "agent_id": agent_id,
        }
        meta = {
            "source": "heartbeat",
            "agent_id": agent_id,
            "job_id": job_id,
            "heartbeat_entry": entry.name,
            "ephemeral_session": entry.workspace == "ephemeral",
        }

        queue = get_task_queue()
        task_id = queue.submit_task(
            task_type=task_type,
            params=params,
            metadata=meta,
            session_id=ws_id,
            session_namespace=session_namespace,
        )
        hb_state.mark_fired(agent_id, entry.name, task_id=task_id)

        if entry.workspace == "ephemeral":
            self._tracked_ephemeral[task_id] = {
                "session_id": ws_id,
                "session_namespace": session_namespace,
                "agent_id": agent_id,
                "entry_name": entry.name,
            }
        logger.info(f"Fired heartbeat {agent_id}/{entry.name} task={task_id} ws={ws_id}")

    def _sweep_ephemeral(self) -> None:
        if not self._tracked_ephemeral:
            return
        queue = get_task_queue()
        done_keys = []
        for task_id, info in list(self._tracked_ephemeral.items()):
            t = queue.get_task(task_id)
            if not t or t.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                continue
            try:
                session_store.delete(info["session_id"], namespace=info.get("session_namespace"))
            except Exception as exc:
                logger.warning(f"could not delete ephemeral session {info['session_id']}: {exc}")
            try:
                hb_state.mark_finished(info["agent_id"], info["entry_name"], t.status.value, task_id=task_id)
            except Exception:
                pass
            done_keys.append(task_id)
        for k in done_keys:
            self._tracked_ephemeral.pop(k, None)


_scheduler: Optional[HeartbeatScheduler] = None


def get_scheduler() -> HeartbeatScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = HeartbeatScheduler()
    return _scheduler


async def start_scheduler() -> None:
    await get_scheduler().start()


async def stop_scheduler() -> None:
    await get_scheduler().stop()
