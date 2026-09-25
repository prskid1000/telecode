"""Bind an :class:`EngineRequest` to the task-queue task running on this thread.

* events → ``task_utils.append_event`` (task metadata + ``data/telecode.db`` +
  the live bus). ``delta`` events are streamed to the bus only — except for
  agy, whose only text channel they are, where they are persisted under the
  legacy ``narrative_delta`` kind the UIs and TeleDesign already read.
* progress → ``update_progress``; cancel → ``is_cancelled`` (the queue's
  cancel/timeout flips the status, and the runner's watcher sees it);
* the CLI pid is registered with the queue together with the runner's
  non-blocking ``stop`` so ``TaskQueue.cancel`` and the timeout watchdog take
  the graceful → tree-kill path;
* the task's ``timeout_seconds`` is also handed to the runner;
* after a successful run, one row of session lineage (``sessions_index``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from services.engine.adapters import get_adapter
from services.engine.types import EngineRequest

logger = logging.getLogger("telecode.services.engine.task_bridge")


def legacy_resume_writer(sid: str, ns: Optional[str], key: str) -> Callable[[str], None]:
    """Resume-id writer for callers with no scoped store (TeleDesign's flat keys)."""
    def store(resume_id: str) -> None:
        from services.session import session_store
        session_store.patch_data(sid, {key: resume_id}, namespace=ns)
    return store


def task_request(engine: str, *, prompt: str, cwd: Path, sid: Optional[str], model: Optional[str] = None,
                 is_local: bool = False, resume_id: Optional[str] = None,
                 on_resume_id: Optional[Callable[[str], None]] = None, log_path: Optional[Path] = None,
                 last_msg_path: Optional[Path] = None, system_append_file: Optional[Path] = None,
                 schema: Optional[Dict[str, Any]] = None) -> EngineRequest:
    from services.task import task_utils
    from services.task.task_manager import get_task_queue

    adapter = get_adapter(engine)
    task_id = task_utils.get_task_id()
    queue = get_task_queue()
    task = queue.get_task(task_id) if task_id else None

    def on_event(evt: Dict[str, Any]) -> None:
        if evt.get("kind") == "delta":
            if adapter.persist_deltas:
                task_utils.append_event({**evt, "kind": "narrative_delta"})
            else:
                task_utils.publish_live(evt)
            return
        task_utils.append_event(evt)

    def on_spawn(pid: int, stop: Callable[[str], None]) -> None:
        queue.register_pid(task_id, pid, killer=stop)

    def on_exit(pid: int) -> None:
        queue.unregister_pid(task_id, pid)

    import config as app_config
    return EngineRequest(
        engine=engine, prompt=prompt, cwd=Path(cwd), model=model, is_local=bool(is_local),
        resume_id=resume_id, on_resume_id=on_resume_id, log_path=log_path, last_msg_path=last_msg_path,
        system_append_file=system_append_file, schema=schema,
        timeout_sec=(task.timeout_seconds if task else None),
        on_event=on_event, on_progress=lambda p, m: task_utils.update_progress(p, m),
        cancel_check=task_utils.is_cancelled, on_spawn=on_spawn, on_exit=on_exit,
        session_id=sid, kill_grace_sec=app_config.tasks_kill_grace_seconds(),
    )


def _lineage_kind(md: Dict[str, Any]) -> str:
    if md.get("source") == "design":
        return "design"
    if md.get("routine_id"):
        return "routine"
    if md.get("source") == "heartbeat":
        return "heartbeat"
    if md.get("run_id"):
        return "run"
    return "task"


def run_in_task(req: EngineRequest, *, sid: Optional[str], ns: Optional[str],
                agent_id: Optional[str] = None) -> Dict[str, Any]:
    """Run the engine and return the handler result dict (shape unchanged)."""
    from services.engine.runner import run_engine
    from services.task import task_utils
    from services.task.task_manager import get_task_queue

    result = run_engine(req)
    try:
        from services.db import sessions_repo
        tid = task_utils.get_task_id()
        task = get_task_queue().get_task(tid) if tid else None
        md = (task.metadata if task else {}) or {}
        sessions_repo.record_run(
            namespace=ns, workspace_id=sid, agent_id=agent_id, engine=req.engine, is_local=req.is_local,
            engine_session_id=result.engine_session_id, kind=_lineage_kind(md),
            created_by=str(md.get("source") or "user"), task_id=tid, tokens=result.tokens,
            cost_usd=result.cost_usd)
    except Exception:
        logger.exception("session lineage write failed")
    return result.to_dict(sid, with_schema=bool(req.schema))
