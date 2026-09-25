"""Pipeline run executor — orchestrates Job.pipeline.steps[] under a Run record.

Sequential mode (Phase 9): steps run in order against the job's workspace_id.
A step's task is submitted via the queue; we poll its status, capture the
final reply, and feed it forward when step.depends_on_text is true.

Parallel mode (Phase 12): each step gets its own ephemeral session;
all submitted concurrently; aggregated when all finish.

Engine / model / local are resolved per step (B3):
  step override (job.pipeline.steps[].engine/model/is_local, blank = inherit)
  > run override (POST /api/jobs/{id}/runs body)
  > agent default (agent.engine / agent.model)
  > claude_code / CLI default model / cloud.

Handoff (B2): a step with depends_on_text gets the previous step's full reply
(capped at HANDOFF_CAP chars, head + tail around an omission marker) plus the
list of files that step changed in its workspace.

Failure handling: sequential — first failure stops; remaining steps marked
"skipped". Parallel — independent; final run.status reflects mixed outcomes.

Cancellation: cancel_run(run_id) cancels in-flight tasks through the shared
queue cancel (which kills the CLI process tree), marks running steps
cancelled and pending ones skipped. It also works on orphaned runs (no live
driver, e.g. after a restart). reconcile_orphaned_runs() runs at startup and
marks anything left "running" by a previous process as "interrupted".
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.agent.agent_manager import get_agent_manager
from services.run.run_store import get_run_store, usage_from_result
from services.session import session_store
from services.task.task_manager import POOL_BACKGROUND, TaskStatus, cancel_task, get_task_queue

logger = logging.getLogger("telecode.services.run.executor")

EPHEMERAL_NS = "run-parallel"

HANDOFF_CAP = 16 * 1024          # chars of previous output passed to the next step
PREVIEW_CAP = 400                # chars kept in step.result_preview for list views
MAX_FILES_LISTED = 200
MAX_FILES_SCANNED = 20000
_SCAN_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}


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


def _result_text(result: Any) -> str:
    if isinstance(result, dict):
        v = result.get("result")
        if isinstance(v, str) and v.strip():
            return v.strip()
        text = result.get("output_text") or ""
        if isinstance(text, str) and text.strip():
            return text.strip()
        return ""
    if isinstance(result, str):
        return result
    return ""


def _result_preview(result: Any, limit: int = PREVIEW_CAP) -> str:
    """Pull a short text preview out of a task result dict (list views)."""
    return _result_text(result)[:limit]


def _cap_head_tail(text: str, cap: int = HANDOFF_CAP) -> str:
    """Keep the head and the tail of an over-long text around a marker.

    The tail matters as much as the head — CLIs put the conclusion last."""
    if len(text) <= cap:
        return text
    omitted = len(text) - cap
    marker = f"\n\n[... {omitted} characters omitted from the middle of this output ...]\n\n"
    keep = max(0, cap - len(marker))
    head = keep // 2
    tail = keep - head
    return text[:head] + marker + (text[-tail:] if tail else "")


def _handoff_text(result: Any, cap: int = HANDOFF_CAP) -> str:
    return _cap_head_tail(_result_text(result), cap)


def _engine_to_task_type(engine: str) -> str:
    from services.task.engine_map import engine_to_task_type
    return engine_to_task_type(engine or "claude_code")


def _resolve_step_config(step: Dict[str, Any], overrides: Dict[str, Any],
                         agent: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """step override > run override > agent default > built-in default."""
    agent = agent or {}

    def pick(key: str, default: Any) -> Any:
        for src in (step, overrides, agent):
            v = src.get(key)
            if v is not None and v != "":
                return v
        return default

    engine = str(pick("engine", "claude_code")).strip().lower()
    from services.task.engine_map import supported_engines
    if engine not in supported_engines():
        engine = "claude_code"
    is_local = step.get("is_local")
    if is_local is None:
        is_local = overrides.get("is_local")
    return {
        "engine": engine,
        "model": str(pick("model", "") or "").strip(),
        "is_local": bool(is_local) if is_local is not None else False,
    }


def _build_step_prompt(job: Dict[str, Any], step: Dict[str, Any], prev_outputs: Optional[List[Dict[str, Any]]]) -> str:
    """Compose the prompt for a step.

    `prev_outputs` is a list of {step_id, name, text, status, files_changed?}
    from the previous phase. When step.depends_on_text is true:
      - 1 prior output → wrapped in <previous_output>
      - >1 prior outputs → wrapped in <previous_outputs> with per-output tags
    Each output carries a <files_changed> list when the step touched files.
    """
    base = step.get("prompt_override") or job.get("task_description") or ""
    if step.get("depends_on_text") and prev_outputs:
        non_empty = [o for o in prev_outputs if o.get("text") or o.get("files_changed")]
        if non_empty:
            def body(o: Dict[str, Any]) -> str:
                parts = [o.get("text") or ""]
                files = o.get("files_changed") or []
                if files:
                    parts.append("<files_changed>\n" + "\n".join(
                        f"{f.get('change', 'modified')}: {f.get('path')}" for f in files
                    ) + "\n</files_changed>")
                return "\n".join(p for p in parts if p)

            if len(non_empty) == 1:
                base = f"{base}\n\n<previous_output>\n{body(non_empty[0])}\n</previous_output>"
            else:
                blocks = "\n".join(
                    f'<output step="{(o.get("name") or o.get("step_id", "")[:8])}">\n{body(o)}\n</output>'
                    for o in non_empty
                )
                base = f"{base}\n\n<previous_outputs>\n{blocks}\n</previous_outputs>"
    return base.strip() or "(no prompt provided)"


# ── Workspace change detection (for the handoff's file list) ────────────────

def _scan_files(root: Path) -> Dict[str, Tuple[int, int]]:
    out: Dict[str, Tuple[int, int]] = {}
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SCAN_SKIP_DIRS]
        for fn in filenames:
            if fn == "session.json" or fn.startswith(".session."):
                continue
            p = Path(dirpath) / fn
            try:
                st = p.stat()
            except OSError:
                continue
            out[p.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
            if len(out) >= MAX_FILES_SCANNED:
                return out
    return out


def _diff_files(before: Dict[str, Tuple[int, int]], after: Dict[str, Tuple[int, int]]) -> List[Dict[str, str]]:
    changes: List[Dict[str, str]] = []
    for path in sorted(set(before) | set(after)):
        if path not in before:
            changes.append({"path": path, "change": "added"})
        elif path not in after:
            changes.append({"path": path, "change": "deleted"})
        elif before[path] != after[path]:
            changes.append({"path": path, "change": "modified"})
        if len(changes) >= MAX_FILES_LISTED:
            break
    return changes


# ── Public entry points ─────────────────────────────────────────────────────

async def start_run(
    job: Dict[str, Any],
    is_local: Optional[bool] = None,
    source: str = "user",
    engine: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a Run record and launch the driver thread. Returns the new run dict.

    `engine` / `model` / `is_local` are run-level overrides (None / "" = not
    set) — see the module docstring for precedence.
    """
    pipeline = job.get("pipeline") or {"mode": "single", "steps": []}
    steps_in = pipeline.get("steps") or []
    if not steps_in:
        raise ValueError("Job has no pipeline steps to run")

    from services.task.engine_map import supported_engines
    eng = (engine or "").strip().lower()
    if eng and eng not in supported_engines():
        raise ValueError(f"engine must be one of {supported_engines()}, got {engine!r}")
    overrides = {"engine": eng, "model": (model or "").strip(),
                 "is_local": None if is_local is None else bool(is_local)}

    agent_mgr = get_agent_manager()
    decorated_steps = []
    resolved_steps = []
    for s in steps_in:
        agent = agent_mgr.get_agent(s.get("agent_id")) or {}
        cfg = _resolve_step_config(s, overrides, agent)
        step_id = s.get("step_id") or str(uuid.uuid4())
        decorated_steps.append({
            "step_id": step_id,
            "agent_id": s.get("agent_id"),
            "agent_name": agent.get("name", ""),
            "name": s.get("name", ""),
            **cfg,
        })
        resolved_steps.append({**s, "step_id": step_id, "_cfg": cfg})

    run = get_run_store().create_run(
        job_id=job["id"],
        mode=pipeline.get("mode", "single"),
        source=source,
        steps=decorated_steps,
        overrides=overrides,
    )

    driver = _RunDriver(run["run_id"])
    with _drivers_lock:
        _drivers[run["run_id"]] = driver

    driver.thread = threading.Thread(
        target=_drive_run,
        args=(run["run_id"], job, {**pipeline, "steps": resolved_steps}, source, driver),
        name=f"run-{run['run_id'][:8]}",
        daemon=True,
    )
    driver.thread.start()
    return run


def cancel_run(run_id: str) -> bool:
    """Cancel a run — live or orphaned. Returns False if unknown or finished."""
    store = get_run_store()
    run = store.get_run(run_id)
    if not run:
        return False
    if run.get("status") in ("completed", "failed", "cancelled", "partial", "interrupted"):
        return False

    store.update_run(run_id, {"cancel_requested": True})
    with _drivers_lock:
        driver = _drivers.get(run_id)
    if driver:
        driver.cancel_event.set()
        for tid in list(driver.active_task_ids):
            cancel_task(tid, "cancelled by user")

    now = _now_iso()
    for s in run.get("steps", []):
        if s.get("status") == "pending":
            store.update_step(run_id, s["step_id"], {"status": "skipped"})
        elif s.get("status") == "running":
            if s.get("task_id"):
                cancel_task(s["task_id"], "cancelled by user")
            store.update_step(run_id, s["step_id"], {
                "status": "cancelled", "completed_at": now, "error": "cancelled by user",
            })
    store.update_run(run_id, {"status": "cancelled"})
    store.finalise(run_id)
    return True


def reconcile_orphaned_runs() -> int:
    """Startup pass: runs left pending/running by a previous process.

    A run with a live driver in this process is left alone. Otherwise every
    running step whose task is not alive in the queue becomes "interrupted"
    (pending ones "skipped") and the run is finalised. Returns runs touched.
    """
    store = get_run_store()
    queue = get_task_queue()
    touched = 0
    for run in store.list_runs(limit=100000):
        if run.get("status") not in ("pending", "running"):
            continue
        rid = run.get("run_id")
        with _drivers_lock:
            if rid in _drivers:
                continue
        now = _now_iso()
        for s in run.get("steps") or []:
            st = s.get("status")
            if st == "running" and not queue.is_active(s.get("task_id")):
                store.update_step(rid, s["step_id"], {
                    "status": "interrupted", "completed_at": now,
                    "error": "interrupted: telecode restarted while this step was running",
                })
            elif st == "pending":
                store.update_step(rid, s["step_id"], {"status": "skipped"})
        store.finalise(rid)
        touched += 1
    if touched:
        logger.warning(f"Marked {touched} orphaned run(s) from a previous process as interrupted")
    return touched


# ── Driver thread ───────────────────────────────────────────────────────────

def _drive_run(
    run_id: str,
    job: Dict[str, Any],
    pipeline: Dict[str, Any],
    source: str,
    driver: _RunDriver,
):
    """Phase-driven executor.

    Group steps by `phase` index. For each phase in order:
      - 1 step  → run inside the job's workspace (sequential semantics)
      - >1 step → ephemeral session per step, run concurrently (parallel)
    Outputs from a phase are passed forward to the next phase's steps that
    have `depends_on_text`. First failure halts; remaining steps marked skipped.
    """
    store = get_run_store()
    store.update_run(run_id, {"status": "running"})
    try:
        _run_phased(run_id, job, pipeline, source, driver)
    except Exception as exc:
        logger.exception(f"Run {run_id} crashed: {exc}")
        store.update_run(run_id, {"status": "failed"})
    finally:
        store.finalise(run_id)
        with _drivers_lock:
            _drivers.pop(run_id, None)


def _run_phased(run_id, job, pipeline, source, driver: _RunDriver):
    store = get_run_store()
    queue = get_task_queue()
    workspace_id = job.get("workspace_id")
    steps = pipeline.get("steps") or []
    if not steps:
        return

    # Group by phase index (preserve order of first appearance)
    phases: Dict[int, List[Dict[str, Any]]] = {}
    phase_order: List[int] = []
    for s in steps:
        p = int(s.get("phase") or 0)
        if p not in phases:
            phases[p] = []
            phase_order.append(p)
        phases[p].append(s)
    phase_order.sort()

    prev_outputs: List[Dict[str, Any]] = []
    halt = False

    for phase_idx in phase_order:
        phase_steps = phases[phase_idx]

        if halt or driver.cancel_event.is_set():
            for s in phase_steps:
                store.update_step(run_id, s["step_id"], {"status": "skipped"})
            continue

        if len(phase_steps) == 1:
            step = phase_steps[0]
            if not workspace_id:
                store.update_step(run_id, step["step_id"], {
                    "status": "failed", "error": "Job has no workspace_id",
                })
                halt = True
                continue
            outputs = _run_one_in_workspace(
                run_id, step, job, workspace_id, prev_outputs,
                source, driver, queue, store,
            )
        else:
            outputs = _run_phase_parallel(
                run_id, phase_steps, job, prev_outputs,
                source, driver, queue, store,
            )

        prev_outputs = outputs
        if any(o.get("status") != "completed" for o in outputs):
            halt = True


def _step_cfg(step: Dict[str, Any]) -> Dict[str, Any]:
    return step.get("_cfg") or _resolve_step_config(step, {}, None)


def _submit_step(queue, run_id, step, job, prompt, source, session_id, namespace=None, extra_meta=None) -> str:
    cfg = _step_cfg(step)
    params: Dict[str, Any] = {"prompt": prompt, "is_local": cfg["is_local"], "agent_id": step["agent_id"]}
    if cfg["model"]:
        params["model"] = cfg["model"]
    return queue.submit_task(
        task_type=_engine_to_task_type(cfg["engine"]),
        params=params,
        metadata={
            "source": source,
            "job_id": job["id"],
            "run_id": run_id,
            "step_id": step["step_id"],
            "agent_id": step["agent_id"],
            "engine": cfg["engine"],
            **(extra_meta or {}),
        },
        session_id=session_id,
        session_namespace=namespace,
        pool=POOL_BACKGROUND,
    )


def _finish_step(store, run_id, step, result_obj, status, error, before, work_dir) -> Dict[str, Any]:
    files = _diff_files(before, _scan_files(work_dir)) if work_dir is not None else []
    text = _handoff_text(result_obj) if status == "completed" else None
    store.update_step(run_id, step["step_id"], {
        "status": status,
        "completed_at": _now_iso(),
        "result_preview": text[:PREVIEW_CAP] if text is not None else None,
        "result_text": text,
        "files_changed": files,
        "usage": usage_from_result(result_obj),
        "error": error,
    })
    return {
        "step_id": step["step_id"],
        "name": step.get("name") or "",
        "text": text,
        "files_changed": files,
        "status": status,
    }


def _run_one_in_workspace(
    run_id, step, job, workspace_id, prev_outputs,
    source, driver: _RunDriver, queue, store,
) -> List[Dict[str, Any]]:
    prompt = _build_step_prompt(job, step, prev_outputs)
    work_dir = session_store._session_dir(workspace_id)
    before = _scan_files(work_dir)

    store.update_step(run_id, step["step_id"], {
        "status": "running",
        "started_at": _now_iso(),
        "session_id": workspace_id,
    })

    task_id = _submit_step(queue, run_id, step, job, prompt, source, workspace_id)
    driver.active_task_ids.append(task_id)
    store.update_step(run_id, step["step_id"], {"task_id": task_id})

    result_obj, status, error = _wait_for_task(task_id, driver)
    out = _finish_step(store, run_id, step, result_obj, status, error, before, work_dir)
    try:
        driver.active_task_ids.remove(task_id)
    except ValueError:
        pass
    return [out]


def _run_phase_parallel(
    run_id, phase_steps, job, prev_outputs,
    source, driver: _RunDriver, queue, store,
) -> List[Dict[str, Any]]:
    fan_sessions: Dict[str, str] = {}  # step_id -> ws_id
    snapshots: Dict[str, Dict[str, Tuple[int, int]]] = {}
    job_ws_id = job.get("workspace_id")

    # Submit all steps' tasks against ephemeral sessions
    for step in phase_steps:
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

        if job_ws_id:
            try:
                _copy_workspace_files(job_ws_id, ws_id)
            except Exception as exc:
                logger.warning(f"fan-out copy {job_ws_id} -> {ws_id} failed: {exc}")

        prompt = _build_step_prompt(job, step, prev_outputs)
        snapshots[step["step_id"]] = _scan_files(session_store._session_dir(ws_id, namespace=EPHEMERAL_NS))

        store.update_step(run_id, step["step_id"], {
            "status": "running",
            "started_at": _now_iso(),
            "session_id": ws_id,
        })

        task_id = _submit_step(queue, run_id, step, job, prompt, source, ws_id,
                               namespace=EPHEMERAL_NS, extra_meta={"ephemeral_session": True})
        driver.active_task_ids.append(task_id)
        store.update_step(run_id, step["step_id"], {"task_id": task_id})

    outputs: List[Dict[str, Any]] = []
    for step in phase_steps:
        cur = next(
            (s for s in (store.get_run(run_id) or {}).get("steps", []) if s["step_id"] == step["step_id"]),
            None,
        )
        if not cur or not cur.get("task_id"):
            continue
        result_obj, status, error = _wait_for_task(cur["task_id"], driver)
        ws_dir = session_store._session_dir(fan_sessions[step["step_id"]], namespace=EPHEMERAL_NS)
        outputs.append(_finish_step(store, run_id, step, result_obj, status, error,
                                    snapshots.get(step["step_id"], {}), ws_dir))
        try:
            driver.active_task_ids.remove(cur["task_id"])
        except ValueError:
            pass

    for _sid, ws_id in fan_sessions.items():
        try:
            session_store.delete(ws_id, namespace=EPHEMERAL_NS)
        except Exception as exc:
            logger.warning(f"could not delete ephemeral run session {ws_id}: {exc}")

    return outputs


# ── Helpers ────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _wait_for_task(task_id: str, driver: _RunDriver, poll_seconds: float = 0.5):
    """Block until the queue task resolves. Returns (result, status, error)."""
    queue = get_task_queue()
    while True:
        if driver.cancel_event.is_set():
            cancel_task(task_id, "cancelled by user")
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
