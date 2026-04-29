"""Pipeline run executor — orchestrates Job.pipeline.steps[] under a Run record.

Sequential mode (Phase 9): steps run in order against the job's workspace_id.
A step's task is submitted via the queue; we poll its status, capture the
final reply, and feed it forward when step.depends_on_text is true.

Parallel mode (Phase 12): each step gets its own ephemeral session;
all submitted concurrently; aggregated when all finish.

Failure handling: sequential — first failure stops; remaining steps marked
"skipped". Parallel — independent; final run.status reflects mixed outcomes.

Cancellation: cancel_run(run_id) marks the run cancelled, cancels in-flight
queue tasks for active steps, marks pending steps "skipped".
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.agent.agent_manager import get_agent_manager
from services.run.run_store import get_run_store
from services.session import session_store
from services.task.task_manager import TaskStatus, get_task_queue

logger = logging.getLogger("telecode.services.run.executor")

EPHEMERAL_NS = "run-parallel"


# In-process tracking of running drivers, so cancel_run() can signal them.
_drivers_lock = threading.Lock()
_drivers: Dict[str, "_RunDriver"] = {}


class _RunDriver:
    """Owns the lifecycle of a single Run."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.cancel_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.active_task_ids: List[str] = []  # for cancel propagation


def _result_preview(result: Any, limit: int = 400) -> str:
    """Pull a short text preview out of a task result dict."""
    if isinstance(result, dict):
        for key in ("result",):
            v = result.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()[:limit]
        text = (result.get("output_text") or "")
        if isinstance(text, str) and text.strip():
            return text.strip()[:limit]
    if isinstance(result, str):
        return result[:limit]
    return ""


def _engine_to_task_type(engine: str) -> str:
    return {"claude_code": "CLAUDE_CODE", "gemini": "GEMINI"}.get(engine or "claude_code", "CLAUDE_CODE")


def _agent_default_engine(agent_id: str) -> str:
    # Today there's no per-agent engine field; default to claude_code.
    # When pipelines store per-step engine override, executor will read that.
    return "claude_code"


def _build_step_prompt(job: Dict[str, Any], step: Dict[str, Any], prev_text: Optional[str]) -> str:
    base = step.get("prompt_override") or job.get("task_description") or ""
    if step.get("depends_on_text") and prev_text:
        base = f"{base}\n\n<previous_output>\n{prev_text}\n</previous_output>"
    return base.strip() or "(no prompt provided)"


# ── Public entry points ─────────────────────────────────────────────────────

async def start_run(job: Dict[str, Any], is_local: bool, source: str) -> Dict[str, Any]:
    """Create a Run record and launch the driver thread. Returns the new run dict."""
    pipeline = job.get("pipeline") or {"mode": "single", "steps": []}
    steps_in = pipeline.get("steps") or []
    if not steps_in:
        raise ValueError("Job has no pipeline steps to run")

    agent_mgr = get_agent_manager()
    decorated_steps = []
    for s in steps_in:
        agent = agent_mgr.get_agent(s.get("agent_id")) or {}
        decorated_steps.append({
            "step_id": s.get("step_id") or str(uuid.uuid4()),
            "agent_id": s.get("agent_id"),
            "agent_name": agent.get("name", ""),
            "name": s.get("name", ""),
        })

    run = get_run_store().create_run(
        job_id=job["id"],
        mode=pipeline.get("mode", "single"),
        source=source,
        steps=decorated_steps,
    )

    driver = _RunDriver(run["run_id"])
    with _drivers_lock:
        _drivers[run["run_id"]] = driver

    driver.thread = threading.Thread(
        target=_drive_run,
        args=(run["run_id"], job, pipeline, is_local, source, driver),
        name=f"run-{run['run_id'][:8]}",
        daemon=True,
    )
    driver.thread.start()
    return run


def cancel_run(run_id: str) -> bool:
    store = get_run_store()
    run = store.get_run(run_id)
    if not run:
        return False
    if run.get("status") in ("completed", "failed", "cancelled"):
        return False

    with _drivers_lock:
        driver = _drivers.get(run_id)
    if driver:
        driver.cancel_event.set()
        # Best-effort cancel of in-flight tasks
        queue = get_task_queue()
        for tid in list(driver.active_task_ids):
            t = queue.get_task(tid)
            if t and t.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                with queue.lock:
                    if t.future and not t.future.done():
                        t.future.cancel()
                    t.status = TaskStatus.CANCELLED

    # Mark any pending step as skipped, set run status
    for s in run.get("steps", []):
        if s.get("status") == "pending":
            store.update_step(run_id, s["step_id"], {"status": "skipped"})
    store.update_run(run_id, {"status": "cancelled"})
    store.finalise(run_id)
    return True


# ── Driver thread ───────────────────────────────────────────────────────────

def _drive_run(
    run_id: str,
    job: Dict[str, Any],
    pipeline: Dict[str, Any],
    is_local: bool,
    source: str,
    driver: _RunDriver,
):
    store = get_run_store()
    store.update_run(run_id, {"status": "running"})
    mode = pipeline.get("mode", "single")
    try:
        if mode == "parallel":
            _run_parallel(run_id, job, pipeline, is_local, source, driver)
        else:
            _run_sequential(run_id, job, pipeline, is_local, source, driver)
    except Exception as exc:
        logger.exception(f"Run {run_id} crashed: {exc}")
        store.update_run(run_id, {"status": "failed"})
    finally:
        store.finalise(run_id)
        with _drivers_lock:
            _drivers.pop(run_id, None)


def _run_sequential(run_id, job, pipeline, is_local, source, driver: _RunDriver):
    store = get_run_store()
    queue = get_task_queue()

    workspace_id = job.get("workspace_id")
    if not workspace_id:
        # All sequential steps need a shared workspace
        for s in pipeline.get("steps") or []:
            store.update_step(run_id, s["step_id"], {"status": "failed", "error": "Job has no workspace_id"})
        store.update_run(run_id, {"status": "failed"})
        return

    prev_text: Optional[str] = None
    halt = False

    for step in pipeline.get("steps") or []:
        if driver.cancel_event.is_set():
            store.update_step(run_id, step["step_id"], {"status": "skipped"})
            continue
        if halt:
            store.update_step(run_id, step["step_id"], {"status": "skipped"})
            continue

        engine = _agent_default_engine(step["agent_id"])
        task_type = _engine_to_task_type(engine)
        prompt = _build_step_prompt(job, step, prev_text)

        store.update_step(run_id, step["step_id"], {
            "status": "running",
            "started_at": _now_iso(),
            "session_id": workspace_id,
        })

        task_id = queue.submit_task(
            task_type=task_type,
            params={"prompt": prompt, "is_local": is_local, "agent_id": step["agent_id"]},
            metadata={
                "source": source,
                "job_id": job["id"],
                "run_id": run_id,
                "step_id": step["step_id"],
                "agent_id": step["agent_id"],
            },
            session_id=workspace_id,
        )
        driver.active_task_ids.append(task_id)
        store.update_step(run_id, step["step_id"], {"task_id": task_id})

        # Wait for completion
        result_obj, status, error = _wait_for_task(task_id, driver)

        prev_text = _result_preview(result_obj) if status == "completed" else None
        store.update_step(run_id, step["step_id"], {
            "status": status,
            "completed_at": _now_iso(),
            "result_preview": prev_text,
            "error": error,
        })

        try:
            driver.active_task_ids.remove(task_id)
        except ValueError:
            pass

        if status != "completed":
            halt = True


def _run_parallel(run_id, job, pipeline, is_local, source, driver: _RunDriver):
    store = get_run_store()
    queue = get_task_queue()
    steps = pipeline.get("steps") or []

    # Each parallel step gets its own ephemeral session, fanned-out from the
    # job's workspace contents (read-only-ish: only the staged agent files
    # matter for identity; CLI may write artifacts in the ephemeral cwd).
    fan_sessions: Dict[str, str] = {}  # step_id -> ws_id

    job_ws_id = job.get("workspace_id")

    for step in steps:
        ws_id = f"run-{run_id[:8]}-{step['step_id'][:8]}"
        try:
            session_store.create(
                session_id=ws_id,
                namespace=EPHEMERAL_NS,
                data={"name": f"run-{run_id[:8]}", "ephemeral": True, "owner_run": run_id},
                session_idle_timeout_seconds=3600,
                absolute_ttl_seconds=3600,
            )
        except FileExistsError:
            pass
        fan_sessions[step["step_id"]] = ws_id

        # Best-effort: copy job workspace files into ephemeral cwd
        if job_ws_id:
            try:
                _copy_workspace_files(job_ws_id, ws_id)
            except Exception as exc:
                logger.warning(f"fan-out copy {job_ws_id} -> {ws_id} failed: {exc}")

        engine = _agent_default_engine(step["agent_id"])
        task_type = _engine_to_task_type(engine)
        prompt = _build_step_prompt(job, step, prev_text=None)

        store.update_step(run_id, step["step_id"], {
            "status": "running",
            "started_at": _now_iso(),
            "session_id": ws_id,
        })

        task_id = queue.submit_task(
            task_type=task_type,
            params={"prompt": prompt, "is_local": is_local, "agent_id": step["agent_id"]},
            metadata={
                "source": source,
                "job_id": job["id"],
                "run_id": run_id,
                "step_id": step["step_id"],
                "agent_id": step["agent_id"],
                "ephemeral_session": True,
            },
            session_id=ws_id,
            session_namespace=EPHEMERAL_NS,
        )
        driver.active_task_ids.append(task_id)
        store.update_step(run_id, step["step_id"], {"task_id": task_id})

    # Now wait for every task in parallel
    for step in steps:
        sid = step["step_id"]
        cur = next((s for s in (store.get_run(run_id) or {}).get("steps", []) if s["step_id"] == sid), None)
        if not cur or not cur.get("task_id"):
            continue
        result_obj, status, error = _wait_for_task(cur["task_id"], driver)
        store.update_step(run_id, sid, {
            "status": status,
            "completed_at": _now_iso(),
            "result_preview": _result_preview(result_obj) if status == "completed" else None,
            "error": error,
        })
        try:
            driver.active_task_ids.remove(cur["task_id"])
        except ValueError:
            pass

    # Cleanup ephemeral sessions
    for sid, ws_id in fan_sessions.items():
        try:
            session_store.delete(ws_id, namespace=EPHEMERAL_NS)
        except Exception as exc:
            logger.warning(f"could not delete ephemeral run session {ws_id}: {exc}")


# ── Helpers ────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _wait_for_task(task_id: str, driver: _RunDriver, poll_seconds: float = 0.5):
    """Block until the queue task resolves. Returns (result, status, error)."""
    queue = get_task_queue()
    while True:
        if driver.cancel_event.is_set():
            t = queue.get_task(task_id)
            if t:
                with queue.lock:
                    if t.future and not t.future.done():
                        t.future.cancel()
                    t.status = TaskStatus.CANCELLED
            return None, "cancelled", "cancelled by user"
        t = queue.get_task(task_id)
        if not t:
            return None, "failed", "task disappeared from queue"
        if t.status == TaskStatus.COMPLETED:
            return t.result, "completed", None
        if t.status == TaskStatus.FAILED:
            return t.result, "failed", t.error
        if t.status == TaskStatus.CANCELLED:
            return t.result, "cancelled", t.error or "cancelled"
        # PENDING or RUNNING — poll
        driver.cancel_event.wait(poll_seconds)


def _copy_workspace_files(src_session_id: str, dst_session_id: str) -> None:
    """Best-effort copy of every regular file from src session to dst (same names)."""
    import shutil
    src_dir = session_store._session_dir(src_session_id)
    dst_dir = session_store._session_dir(dst_session_id, namespace=EPHEMERAL_NS)
    if not src_dir.exists():
        return
    for src_file in src_dir.rglob("*"):
        if not src_file.is_file() or src_file.name == "session.json":
            continue
        rel = src_file.relative_to(src_dir)
        dst_file = dst_dir / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src_file, dst_file)
        except Exception:
            continue
