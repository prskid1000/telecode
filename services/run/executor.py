"""Pipeline run executor — Run → Step → Attempt (P2).

A Run executes a Job's ``pipeline.steps[]`` grouped by ``phase``: phases run
in order, the steps of one phase concurrently. Everything a run needs is
copied into the run record at creation (``job_snapshot``, per-step ``spec``),
so a retry hours later does not depend on the job being unchanged.

Engine / model / local per step (B3):
  step override > run override (POST body) > agent default > claude_code / CLI default / cloud.

Session policy per step (``spec.session_policy``; blank = by position)
  resume         continue the scope's conversation (workspace, agent, engine) in
                 the job workspace — the default for a single-step phase
  fork           branch a conversation instead of continuing it: the previous
                 step's (same engine, same workspace) else the scope's own —
                 Claude ``--resume <id> --fork-session``, Codex ``exec fork``;
                 Antigravity has no fork → fresh + handoff
  fresh          new conversation in the job workspace
  fresh_handoff  new conversation, always seeded with the previous handoff
  ephemeral      new conversation in a throwaway copy of the workspace
                 (``run-parallel`` namespace) — forced for every step of a
                 parallel phase (the steps would otherwise contend for one
                 folder and Claude resumes only in the cwd a session began in)

Handoffs (``services.run.handoff``): every step ends with a structured
handoff (Claude ``--json-schema`` / Codex ``--output-schema`` / agy
``.telecode/handoff.json``; derived from the final text when missing). The
next step's prompt gets ``<handoff>`` blocks — summary, decisions, open
questions, next steps, verdict, artifact paths — when it has
``depends_on_text``, policy ``fresh_handoff``, or is an agy fork. Artifacts are
copied to ``data/runs/<run>/artifacts/<step>/``; for an ephemeral step every
added/modified file is kept there too.

Budgets (``services.run.budget``): run budget = POST body over ``job.budget``,
split over the remaining steps (seconds over the remaining phases), per-step
overrides win. Enforced per attempt by the Engine Runner (tokens, wall clock)
and Claude (``--max-budget-usd``); a hit ends the step ``budget_exceeded``. A
phase whose run budget is used up is not started.

Snapshots (``services.snapshots``): the step's work tree is committed to its
shadow repo before and after every attempt → per-step diff and revert.

Retries: ``retry_step(run, step, mode)`` — ``retry`` resumes the attempt's CLI
conversation ("continue from where you stopped"; fresh if it has none),
``retry_clean`` restores the pre-step snapshot and starts fresh. The step and
every later phase are reset to pending and a new driver continues from that
phase. ``spec.auto_retry`` (0..5) re-runs a step automatically after a
transient failure (overloaded / rate limit / network, detected from the error
and the attempt's events) with exponential backoff
(``tasks.retry_backoff_sec``).

Failure handling: the first phase with a non-completed step halts the run;
later pending steps are "skipped". ``cancel_run`` cancels in-flight tasks
through the shared queue cancel (CLI tree-kill) — live or orphaned.
``reconcile_orphaned_runs`` (startup) marks anything left running as
"interrupted" (retryable).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services import snapshots
from services.agent.agent_manager import get_agent_manager
from services.run import budget as budget_mod
from services.run import handoff as handoff_mod
from services.run.run_store import TERMINAL_RUN_STATUSES, get_run_store, usage_from_result
from services.session import session_store
from services.task.task_manager import POOL_BACKGROUND, TaskStatus, cancel_task, get_task_queue

logger = logging.getLogger("telecode.services.run.executor")

EPHEMERAL_NS = "run-parallel"

HANDOFF_CAP = 16 * 1024          # chars of reply kept in a derived handoff summary
PREVIEW_CAP = 400                # chars kept in step.result_preview for list views
RESULT_TEXT_CAP = 256 * 1024     # the full reply kept on the step (the task row has it all)
MAX_FILES_LISTED = 200
MAX_FILES_SCANNED = 20000
_SCAN_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
                   ".telecode"}

POLICIES = ("resume", "fork", "fresh", "fresh_handoff", "ephemeral")
RETRY_MODES = ("retry", "retry_clean")
RETRYABLE = ("failed", "cancelled", "interrupted", "budget_exceeded")

_TRANSIENT_RE = re.compile(
    r"overloaded|rate[ _-]?limit|too many requests|\b(429|500|502|503|504|529)\b|temporarily unavailable|"
    r"service unavailable|econnreset|etimedout|enotfound|eai_again|socket hang up|network|"
    r"connection (reset|refused|error|closed|aborted)|stream (disconnected|error)|api_error|internal server error",
    re.I)

RETRY_PROMPT = (
    "Your previous attempt at this step stopped before it finished ({reason}). Continue from where you "
    "stopped — do not redo work that is already done — and finish the step.\n\n"
    "The step, for reference:\n<step_prompt>\n{prompt}\n</step_prompt>")

_drivers_lock = threading.Lock()
_drivers: Dict[str, "_RunDriver"] = {}


class _RunDriver:
    """Owns the lifecycle of a single Run (one driver thread at a time)."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.cancel_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.active_task_ids: List[str] = []   # for cancel propagation
        self.lock = threading.Lock()


class RunBusy(RuntimeError):
    """The run (or its workspace) is active — the request would race it."""


# ── Text helpers ────────────────────────────────────────────────────────────

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


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Resolution ──────────────────────────────────────────────────────────────

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


def _resolve_policy(requested: str, parallel: bool) -> str:
    requested = (requested or "").strip().lower()
    if parallel:
        return "ephemeral"
    return requested if requested in POLICIES else "resume"


def _session_mode(policy: str) -> str:
    return {"resume": "resume", "fork": "fork"}.get(policy, "fresh")


def _include_handoff(spec: Dict[str, Any], policy: str, engine: str) -> bool:
    return bool(spec.get("depends_on_text")) or spec.get("session_policy") == "fresh_handoff" \
        or (policy == "fork" and engine == "antigravity")


def _build_step_prompt(job: Dict[str, Any], step: Dict[str, Any], prev_outputs: Optional[List[Dict[str, Any]]],
                       include_handoff: Optional[bool] = None, engine: Optional[str] = None) -> str:
    """Step prompt = prompt override or the job prompt, + the previous phase's
    ``<handoff>`` block(s) when it depends on them, + (in a run) the
    ``<handoff_instructions>`` for ``engine``.

    ``prev_outputs``: [{step_id, name, status, text, handoff?, files_changed?}]
    — an output without a handoff gets one derived from its text."""
    base = (step.get("prompt_override") or job.get("task_description") or "").strip()
    base = base or "(no prompt provided)"
    want = bool(step.get("depends_on_text")) if include_handoff is None else include_handoff
    if want and prev_outputs:
        outs = []
        for o in prev_outputs:
            if not o.get("handoff") and (o.get("text") or "").strip():
                o = {**o, "handoff": handoff_mod.derive(o.get("text") or "", step_status=o.get("status") or "completed")}
            if o.get("handoff") or o.get("files_changed"):
                outs.append(o)
        block = handoff_mod.render_prev(outs)
        if block:
            base = f"{base}\n\n{block}"
    if engine:
        base = f"{base}\n\n{handoff_mod.instructions(engine)}"
    return base


# ── Workspace change detection (fallback when snapshots are off) ────────────

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
    budget: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a Run record and launch the driver thread. Returns the new run dict.

    ``engine`` / ``model`` / ``is_local`` / ``budget`` are run-level overrides
    (None / "" = not set)."""
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
    run_budget = {k: v for k, v in budget_mod.merge(budget, job.get("budget")).items() if v is not None}

    phase_sizes: Dict[int, int] = {}
    for s in steps_in:
        p = int(s.get("phase") or 0)
        phase_sizes[p] = phase_sizes.get(p, 0) + 1

    agent_mgr = get_agent_manager()
    decorated = []
    for s in steps_in:
        agent = agent_mgr.get_agent(s.get("agent_id")) or {}
        cfg = _resolve_step_config(s, overrides, agent)
        phase = int(s.get("phase") or 0)
        spec = {
            "phase": phase,
            "prompt_override": s.get("prompt_override") or "",
            "depends_on_text": bool(s.get("depends_on_text")),
            "session_policy": (s.get("session_policy") or "").strip().lower(),
            "budget": {k: v for k, v in budget_mod.normalize(s.get("budget")).items() if v is not None},
            "auto_retry": max(0, min(5, int(s.get("auto_retry") or 0))),
        }
        decorated.append({
            "step_id": s.get("step_id") or str(uuid.uuid4()),
            "agent_id": s.get("agent_id"),
            "agent_name": agent.get("name", ""),
            "name": s.get("name", ""),
            "spec": spec,
            "session_policy": _resolve_policy(spec["session_policy"], phase_sizes[phase] > 1),
            **cfg,
        })

    run = get_run_store().create_run(
        job_id=job["id"], mode=pipeline.get("mode", "single"), source=source, steps=decorated,
        overrides=overrides, budget=run_budget,
        job_snapshot={"title": job.get("title", ""), "task_description": job.get("task_description", ""),
                      "workspace_id": job.get("workspace_id")},
    )
    _launch(run["run_id"], source, from_phase=None, retry=None)
    return run


def _launch(run_id: str, source: str, *, from_phase: Optional[int], retry: Optional[Dict[str, Any]]) -> _RunDriver:
    with _drivers_lock:
        if run_id in _drivers:
            raise RunBusy("run is already active")
        driver = _RunDriver(run_id)
        _drivers[run_id] = driver
    driver.thread = threading.Thread(target=_drive_run, args=(run_id, source, driver, from_phase, retry),
                                     name=f"run-{run_id[:8]}", daemon=True)
    driver.thread.start()
    return driver


def is_active(run_id: str) -> bool:
    with _drivers_lock:
        return run_id in _drivers


def cancel_run(run_id: str) -> bool:
    """Cancel a run — live or orphaned. Returns False if unknown or finished."""
    store = get_run_store()
    run = store.get_run(run_id)
    if not run:
        return False
    if run.get("status") in TERMINAL_RUN_STATUSES and not is_active(run_id):
        return False

    store.update_run(run_id, {"cancel_requested": True})
    with _drivers_lock:
        driver = _drivers.get(run_id)
    if driver:
        driver.cancel_event.set()
        for tid in list(driver.active_task_ids):
            cancel_task(tid, "cancelled by user")

    now = _now_iso()

    def fn(r):
        for s in r.get("steps", []):
            if s.get("status") == "pending":
                s["status"] = "skipped"
            elif s.get("status") == "running":
                if s.get("task_id"):
                    cancel_task(s["task_id"], "cancelled by user")
                s.update({"status": "cancelled", "completed_at": now, "error": "cancelled by user"})
                if s.get("attempts") and s["attempts"][-1].get("status") == "running":
                    s["attempts"][-1].update({"status": "cancelled", "completed_at": now})
        r["status"] = "cancelled"
    store.mutate(run_id, fn)
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
        if is_active(rid):
            continue
        now = _now_iso()

        def fn(r):
            for s in r.get("steps") or []:
                st = s.get("status")
                if st == "running" and not queue.is_active(s.get("task_id")):
                    s.update({"status": "interrupted", "completed_at": now,
                              "error": "interrupted: telecode restarted while this step was running"})
                    if s.get("attempts") and s["attempts"][-1].get("status") == "running":
                        s["attempts"][-1].update({"status": "interrupted", "completed_at": now})
                elif st == "pending":
                    s["status"] = "skipped"
        store.mutate(rid, fn)
        store.finalise(rid)
        touched += 1
    if touched:
        logger.warning(f"Marked {touched} orphaned run(s) from a previous process as interrupted")
    return touched


def _find_step(run: Dict[str, Any], step_id: str) -> Optional[Dict[str, Any]]:
    return next((s for s in run.get("steps") or [] if s.get("step_id") == step_id), None)


def retry_step(run_id: str, step_id: str, mode: str = "retry",
               budget: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Re-run one step (``retry`` | ``retry_clean``) and continue downstream.

    Raises LookupError (unknown run/step), ValueError (bad mode / step state /
    budget), RunBusy (the run has a live driver)."""
    if mode not in RETRY_MODES:
        raise ValueError(f"mode must be one of {RETRY_MODES}")
    override = {k: v for k, v in budget_mod.normalize(budget).items() if v is not None}
    store = get_run_store()
    run = store.get_run(run_id)
    if not run:
        raise LookupError("Run not found")
    step = _find_step(run, step_id)
    if not step:
        raise LookupError("Step not found in this run")
    if is_active(run_id):
        raise RunBusy("run is active — cancel it or wait for it to finish")
    st = step.get("status")
    if st == "completed" and mode == "retry":
        raise ValueError("this step completed — use retry_clean to redo it from its pre-step snapshot")
    if st not in RETRYABLE + ("completed",):
        raise ValueError(f"cannot retry a {st} step")
    phase = int((step.get("spec") or {}).get("phase") or 0)

    def reset(r):
        for s in r.get("steps") or []:
            ph = int((s.get("spec") or {}).get("phase") or 0)
            if s.get("step_id") == step_id or ph > phase:
                s.update({"status": "pending", "error": None, "completed_at": None})
        r.update({"status": "running", "completed_at": None, "cancel_requested": False,
                  "budget_exhausted": False})
    store.mutate(run_id, reset)
    _launch(run_id, run.get("source") or "user", from_phase=phase,
            retry={"step_id": step_id, "mode": mode, "budget": override or None})
    return store.get_run(run_id)


def _attempt_rec(step: Dict[str, Any], attempt: Optional[int]) -> Optional[Dict[str, Any]]:
    atts = step.get("attempts") or []
    if attempt is None:
        return atts[-1] if atts else None
    return next((a for a in atts if int(a.get("n") or 0) == int(attempt)), None)


def step_diff(run_id: str, step_id: str, attempt: Optional[int] = None,
              path: Optional[str] = None) -> Dict[str, Any]:
    """Unified diff of one attempt (default: the latest) — before → after."""
    run = get_run_store().get_run(run_id)
    if not run:
        raise LookupError("Run not found")
    step = _find_step(run, step_id)
    if not step:
        raise LookupError("Step not found in this run")
    a = _attempt_rec(step, attempt)
    if not a:
        raise LookupError("This step has not run yet")
    key, before, after = a.get("snapshot_key") or step.get("snapshot_key"), a.get("snapshot_before"), a.get("snapshot_after")
    if not (key and before):
        raise LookupError("No snapshots for this attempt (snapshots disabled or git unavailable)")
    if not after:
        raise LookupError("The attempt has no after-snapshot yet (still running?)")
    try:
        d = snapshots.diff(key, before, after, path)
    except snapshots.SnapshotError as exc:
        raise LookupError(str(exc)) from None
    return {"run_id": run_id, "step_id": step_id, "attempt": a.get("n"), "snapshot_key": key,
            "before": before, "after": after, "files": snapshots.changed_files(key, before, after),
            "path": path, **d}


def revert_step(run_id: str, step_id: str, attempt: Optional[int] = None) -> Dict[str, Any]:
    """Restore the job workspace to the attempt's pre-step snapshot."""
    store = get_run_store()
    run = store.get_run(run_id)
    if not run:
        raise LookupError("Run not found")
    step = _find_step(run, step_id)
    if not step:
        raise LookupError("Step not found in this run")
    if step.get("session_policy") == "ephemeral":
        raise ValueError("this step ran in a throwaway copy of the workspace — there is nothing to revert "
                         "in the job workspace (its files are kept as artifacts)")
    a = _attempt_rec(step, attempt)
    if not a or not a.get("snapshot_before"):
        raise LookupError("No pre-step snapshot for this attempt")
    if is_active(run_id):
        raise RunBusy("run is active — cancel it or wait for it to finish")
    sid, ns = step.get("session_id"), step.get("session_namespace")
    if not sid:
        raise LookupError("The step has no workspace")
    if get_task_queue().session_has_active_task(sid, ns):
        raise RunBusy("a task is running in this workspace")
    key = a.get("snapshot_key") or step.get("snapshot_key")
    try:
        res = snapshots.restore(key, session_store._session_dir(sid, namespace=ns), a["snapshot_before"],
                                label=f"revert step {step.get('name') or step_id[:8]} (run {run_id[:8]})",
                                meta={"run": run_id, "step": step_id, "attempt": a.get("n")})
    except snapshots.SnapshotError as exc:
        raise LookupError(str(exc)) from None
    store.update_step(run_id, step_id, {"reverted": {"at": _now_iso(), "attempt": a.get("n"),
                                                     "to": a["snapshot_before"], **res}})
    return {"run_id": run_id, "step_id": step_id, "attempt": a.get("n"), "restored_to": a["snapshot_before"], **res}


# ── Driver ──────────────────────────────────────────────────────────────────

def _drive_run(run_id: str, source: str, driver: _RunDriver, from_phase: Optional[int],
               retry: Optional[Dict[str, Any]]) -> None:
    store = get_run_store()
    store.update_run(run_id, {"status": "running"})
    try:
        _run_phased(run_id, source, driver, from_phase or 0, retry)
    except Exception as exc:
        logger.exception(f"Run {run_id} crashed: {exc}")
        store.update_run(run_id, {"status": "failed"})
    finally:
        with _drivers_lock:
            _drivers.pop(run_id, None)
        store.finalise(run_id)


def _phase_of(step: Dict[str, Any]) -> int:
    return int((step.get("spec") or {}).get("phase") or 0)


def _stored_output(step: Dict[str, Any]) -> Dict[str, Any]:
    return {"step_id": step["step_id"], "name": step.get("name") or "", "status": step.get("status"),
            "text": step.get("result_text"), "handoff": step.get("handoff"),
            "files_changed": step.get("files_changed") or [], "engine": step.get("engine"),
            "engine_session_id": step.get("engine_session_id"), "session_policy": step.get("session_policy"),
            "session_id": step.get("session_id")}


def _run_phased(run_id: str, source: str, driver: _RunDriver, from_phase: int,
                retry: Optional[Dict[str, Any]]) -> None:
    store = get_run_store()
    run = store.get_run(run_id)
    phases: Dict[int, List[str]] = {}
    for s in run.get("steps") or []:
        phases.setdefault(_phase_of(s), []).append(s["step_id"])
    order = sorted(phases)
    earlier = [p for p in order if p < from_phase]
    prev_outputs: List[Dict[str, Any]] = []
    if earlier:
        by_id = {s["step_id"]: s for s in run["steps"]}
        prev_outputs = [_stored_output(by_id[i]) for i in phases[earlier[-1]]]
    halt = False
    for p in [x for x in order if x >= from_phase]:
        run = store.get_run(run_id)
        by_id = {s["step_id"]: s for s in run["steps"]}
        pending = [i for i in phases[p] if by_id[i].get("status") == "pending"]
        if halt or driver.cancel_event.is_set():
            for i in pending:
                store.update_step(run_id, i, {"status": "skipped"})
            continue
        dim = budget_mod.exhausted(run)
        if dim and pending:
            for i in pending:
                store.update_step(run_id, i, {"status": "budget_exceeded", "completed_at": _now_iso(),
                                              "error": f"run budget used up ({dim}) before this step ran"})
            store.update_run(run_id, {"budget_exhausted": True})
            halt = True
            continue
        later = [x for x in order if x >= p]
        steps_left = sum(1 for x in later for i in phases[x] if by_id[i].get("status") == "pending")
        phases_left = sum(1 for x in later if any(by_id[i].get("status") == "pending" for i in phases[x]))
        t0 = time.monotonic()

        def one(i: str) -> None:
            mode = retry["mode"] if retry and retry.get("step_id") == i else "run"
            ovr = retry.get("budget") if retry and retry.get("step_id") == i else None
            try:
                _execute_step(run_id, i, prev_outputs, driver, mode=mode, budget_override=ovr,
                              steps_left=steps_left, phases_left=phases_left, source=source)
            except Exception as exc:
                logger.exception(f"run {run_id} step {i} crashed: {exc}")
                store.update_step(run_id, i, {"status": "failed", "completed_at": _now_iso(),
                                              "error": f"executor error: {exc}"})

        if len(pending) == 1:
            one(pending[0])
        elif pending:
            threads = [threading.Thread(target=one, args=(i,), daemon=True, name=f"run-{run_id[:8]}-{i[:6]}")
                       for i in pending]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        elapsed = time.monotonic() - t0
        store.mutate(run_id, lambda r: r.__setitem__("active_seconds",
                                                     round(float(r.get("active_seconds") or 0) + elapsed, 1)))
        run = store.get_run(run_id)
        by_id = {s["step_id"]: s for s in run["steps"]}
        prev_outputs = [_stored_output(by_id[i]) for i in phases[p]]
        if any(o["status"] != "completed" for o in prev_outputs):
            halt = True


# ── One step: attempts + auto-retry ─────────────────────────────────────────

def _is_transient(error: Optional[str], events: List[Dict[str, Any]]) -> bool:
    texts = [error or ""]
    for e in (events or [])[-30:]:
        if e.get("kind") in ("error", "warning", "retry"):
            texts.append(str(e.get("message") or e.get("text") or e.get("error") or ""))
    blob = "\n".join(texts)
    if "budget_exceeded" in blob or "cancelled" in (error or ""):
        return False
    return bool(_TRANSIENT_RE.search(blob))


def _execute_step(run_id: str, step_id: str, prev_outputs: List[Dict[str, Any]], driver: _RunDriver, *,
                  mode: str, budget_override: Optional[Dict[str, Any]], steps_left: int, phases_left: int,
                  source: str) -> Dict[str, Any]:
    store = get_run_store()
    auto = int((((_find_step(store.get_run(run_id), step_id) or {}).get("spec")) or {}).get("auto_retry") or 0)
    n_auto = 0
    while True:
        out = _attempt(run_id, step_id, prev_outputs, driver, mode=mode, budget_override=budget_override,
                       steps_left=steps_left, phases_left=phases_left, source=source)
        if out["status"] != "failed" or n_auto >= auto or driver.cancel_event.is_set() \
                or not _is_transient(out.get("error"), out.get("events") or []):
            return out
        n_auto += 1
        delay = _backoff(n_auto)
        logger.info(f"run {run_id[:8]} step {step_id[:8]}: transient failure, auto-retry {n_auto}/{auto} "
                    f"in {delay:.0f}s")
        store.update_step(run_id, step_id, {"status": "pending", "auto_retry_in": delay,
                                            "error": f"{out.get('error')} — auto-retry {n_auto}/{auto} in {delay:.0f}s"})
        if driver.cancel_event.wait(delay):
            store.update_step(run_id, step_id, {"status": "cancelled", "completed_at": _now_iso(),
                                                "error": "cancelled by user", "auto_retry_in": None})
            return {**out, "status": "cancelled"}
        store.update_step(run_id, step_id, {"auto_retry_in": None})
        mode = "retry"


def _backoff(n: int) -> float:
    import config
    return float(config.tasks_retry_backoff_seconds()) * (2 ** (n - 1))


def _ephemeral_session(run_id: str, step_id: str, ws_id: Optional[str], keep: bool) -> Tuple[str, Path]:
    sid = f"run-{run_id[:8]}-{step_id[:8]}"
    if keep and session_store.exists(sid, namespace=EPHEMERAL_NS):
        return sid, session_store._session_dir(sid, namespace=EPHEMERAL_NS)
    if session_store.exists(sid, namespace=EPHEMERAL_NS):
        session_store.delete(sid, namespace=EPHEMERAL_NS)
    session_store.create(session_id=sid, namespace=EPHEMERAL_NS,
                         data={"name": f"run-{run_id[:8]}", "ephemeral": True, "owner_run": run_id},
                         session_idle_timeout_seconds=3600, absolute_ttl_seconds=3600)
    if ws_id:
        try:
            _copy_workspace_files(ws_id, sid)
        except Exception as exc:
            logger.warning(f"fan-out copy {ws_id} -> {sid} failed: {exc}")
    return sid, session_store._session_dir(sid, namespace=EPHEMERAL_NS)


def _usage_from_live(md: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    live = (md or {}).get("usage_live")
    if not live:
        return None
    return usage_from_result({"tokens": live.get("tokens") or {}, "cost_usd": live.get("cost_usd")})


def _engine_session_from_result(result: Any) -> Optional[str]:
    if not isinstance(result, dict):
        return None
    for k in ("claude_session_id", "codex_session_id", "antigravity_conversation_id", "engine_session_id"):
        if result.get(k):
            return result[k]
    return None


def _attempt(run_id: str, step_id: str, prev_outputs: List[Dict[str, Any]], driver: _RunDriver, *,
             mode: str, budget_override: Optional[Dict[str, Any]], steps_left: int, phases_left: int,
             source: str) -> Dict[str, Any]:
    store = get_run_store()
    queue = get_task_queue()
    run = store.get_run(run_id)
    step = _find_step(run, step_id)
    spec = step.get("spec") or {}
    snap = run.get("job_snapshot") or {}
    ws_id = snap.get("workspace_id")
    policy = step.get("session_policy") or "resume"
    engine = step.get("engine") or "claude_code"
    attempts = list(step.get("attempts") or [])
    last = attempts[-1] if attempts else None
    n = len(attempts) + 1
    label = step.get("name") or step.get("agent_name") or step_id[:8]

    def fail_now(status: str, error: str) -> Dict[str, Any]:
        store.update_step(run_id, step_id, {"status": status, "completed_at": _now_iso(), "error": error})
        return {**_stored_output({**step, "status": status}), "error": error}

    if driver.cancel_event.is_set():
        return fail_now("skipped", "run cancelled")

    # Where the attempt runs.
    if policy == "ephemeral":
        keep = mode == "retry" and bool(last and last.get("engine_session_id"))
        sid, work_dir = _ephemeral_session(run_id, step_id, ws_id, keep)
        ns: Optional[str] = EPHEMERAL_NS
    else:
        if not ws_id:
            return fail_now("failed", "Job has no workspace_id")
        sid, ns = ws_id, None
        session_store.ensure(sid)
        work_dir = session_store._session_dir(sid)
    key = snapshots.key_for(sid, ns)
    meta = {"run": run_id, "step": step_id, "attempt": n, "mode": mode}

    restored = None
    if mode == "retry_clean" and policy != "ephemeral":
        target = next((a.get("snapshot_before") for a in attempts if a.get("snapshot_before")), None)
        if target and snapshots.exists(key, target):
            try:
                restored = snapshots.restore(key, work_dir, target, label=f"retry clean: restore before {label}",
                                             meta=meta)
            except snapshots.SnapshotError as exc:
                logger.warning(f"retry_clean restore failed: {exc}")
        else:
            logger.warning(f"run {run_id[:8]} step {step_id[:8]}: no pre-step snapshot to restore for retry_clean")

    before = snapshots.take(key, work_dir, f"before {label} · attempt {n}", {**meta, "phase": "before"})
    before_scan = None if before else _scan_files(work_dir)

    eff = budget_mod.for_step(run, step, steps_left=steps_left, phases_left=phases_left, override=budget_override)
    caps = {k: eff[k] for k in budget_mod.DIMS if eff.get(k) is not None}
    attempt_rec: Dict[str, Any] = {
        "n": n, "mode": mode, "task_id": None, "status": "running", "error": None,
        "started_at": _now_iso(), "completed_at": None, "engine_session_id": None,
        "snapshot_key": key if before else None, "snapshot_before": before, "snapshot_after": None,
        "usage": None, "budget": {**caps, "source": eff.get("source") or {}},
        **({"restored": restored} if restored else {}),
    }
    exhausted_dim = next((k for k, v in caps.items() if v is not None and v <= 0), None)
    if exhausted_dim:
        attempt_rec.update({"status": "budget_exceeded", "completed_at": _now_iso(),
                            "error": f"budget_exceeded: no {exhausted_dim} left for this step"})
        store.update_step(run_id, step_id, {
            "status": "budget_exceeded", "completed_at": _now_iso(), "error": attempt_rec["error"],
            "attempts": attempts + [attempt_rec], "budget": attempt_rec["budget"]})
        return {**_stored_output({**step, "status": "budget_exceeded"}), "error": attempt_rec["error"]}

    # Prompt + session control.
    ctl: Dict[str, Any] = {"policy": policy, "session": _session_mode(policy), "handoff": True}
    if caps:
        ctl["budget"] = caps
    add_dirs = []
    for o in prev_outputs:
        d = handoff_mod.artifacts_dir(run_id, o["step_id"])
        if d.is_dir():
            add_dirs.append(str(d))
    if add_dirs:
        ctl["add_dirs"] = add_dirs
    base_prompt = _build_step_prompt(snap, spec, prev_outputs, _include_handoff(spec, policy, engine), engine)
    if mode == "retry" and last and last.get("engine_session_id"):
        ctl["resume_id"] = last["engine_session_id"]
        reason = (last.get("error") or step.get("error") or last.get("status") or "stopped")
        prompt = RETRY_PROMPT.format(reason=str(reason)[:300], prompt=_cap_head_tail(base_prompt, HANDOFF_CAP))
        prompt += "\n\n" + handoff_mod.instructions(engine)
    else:
        if mode in ("retry", "retry_clean"):
            ctl["session"] = "fresh"
        prompt = base_prompt
    if ctl["session"] == "fork" and len(prev_outputs) == 1:
        o = prev_outputs[0]
        if o.get("engine") == engine and o.get("engine_session_id") and o.get("session_policy") != "ephemeral":
            ctl["fork_from"] = o["engine_session_id"]

    params: Dict[str, Any] = {"prompt": prompt, "is_local": bool(step.get("is_local")),
                              "agent_id": step.get("agent_id"), "step_ctl": ctl}
    if step.get("model"):
        params["model"] = step["model"]
    if engine in ("claude_code", "codex"):
        params["schema"] = handoff_mod.HANDOFF_SCHEMA

    task_id = queue.submit_task(
        task_type=_engine_to_task_type(engine), params=params,
        metadata={"source": source, "job_id": run.get("job_id"), "run_id": run_id, "step_id": step_id,
                  "agent_id": step.get("agent_id"), "engine": engine, "attempt": n, "attempt_mode": mode,
                  "session_policy": policy, **({"ephemeral_session": True} if policy == "ephemeral" else {})},
        session_id=sid, session_namespace=ns, pool=POOL_BACKGROUND)
    attempt_rec["task_id"] = task_id
    with driver.lock:
        driver.active_task_ids.append(task_id)
    store.update_step(run_id, step_id, {
        "status": "running", "started_at": attempt_rec["started_at"], "completed_at": None, "error": None, "task_id": task_id, "session_id": sid, "session_namespace": ns,
        "snapshot_key": key if before else None, "snapshot_before": before, "snapshot_after": None,
        "budget": attempt_rec["budget"], "attempts": attempts + [attempt_rec]})

    result_obj, status, error = _wait_for_task(task_id, driver)
    with driver.lock:
        if task_id in driver.active_task_ids:
            driver.active_task_ids.remove(task_id)
    task = queue.get_task(task_id)
    md = dict(task.metadata) if task else {}
    if status == "failed" and (error or "").startswith("budget_exceeded"):
        status = "budget_exceeded"

    after = snapshots.take(key, work_dir, f"after {label} · attempt {n} ({status})", {**meta, "phase": "after"}) \
        if before else None
    if before and after:
        files = [{k: f[k] for k in ("path", "change", "additions", "deletions")}
                 for f in snapshots.changed_files(key, before, after)][:MAX_FILES_LISTED]
    else:
        files = _diff_files(before_scan or {}, _scan_files(work_dir))

    usage = usage_from_result(result_obj) or _usage_from_live(md)
    esid = md.get("engine_session_id") or _engine_session_from_result(result_obj)
    text = _result_text(result_obj)
    structured = result_obj.get("structured_output") if isinstance(result_obj, dict) else None
    ho = handoff_mod.resolve(structured, text, step_status=status, error=error, work_dir=work_dir)
    handoff_mod.collect_artifacts(run_id, step_id, work_dir, ho, files, include_changed=(policy == "ephemeral"))
    if not text and not ho.get("derived"):
        text = ho.get("summary") or ""

    attempt_rec.update({"status": status, "error": error, "completed_at": _now_iso(), "engine_session_id": esid,
                        "snapshot_after": after, "usage": usage, "files_changed": len(files),
                        "handoff_status": ho.get("status"), "verdict": ho.get("verdict")})
    if isinstance(result_obj, dict) and result_obj.get("rotation"):
        rot = result_obj["rotation"]
        attempt_rec["rotated_from"] = {"session": rot.get("from_session"), "reason": rot.get("reason")}
    cur = _find_step(store.get_run(run_id), step_id) or step
    store.update_step(run_id, step_id, {
        "status": status, "completed_at": attempt_rec["completed_at"], "error": error,
        "result_text": text[:RESULT_TEXT_CAP] if text else None,
        "result_preview": text[:PREVIEW_CAP] if text else None,
        "files_changed": files, "handoff": ho, "engine_session_id": esid, "snapshot_after": after,
        "usage": budget_mod.usage_add(cur.get("usage"), usage),
        "attempts": attempts + [attempt_rec]})

    if policy == "ephemeral" and status == "completed":
        try:
            session_store.delete(sid, namespace=EPHEMERAL_NS)   # the shadow repo stays, for the diff
        except Exception as exc:
            logger.warning(f"could not delete ephemeral run session {sid}: {exc}")

    return {"step_id": step_id, "name": step.get("name") or "", "status": status, "error": error,
            "text": text, "handoff": ho, "files_changed": files, "engine": engine,
            "engine_session_id": esid, "session_policy": policy, "events": md.get("events") or []}


# ── Helpers ────────────────────────────────────────────────────────────────

def _wait_for_task(task_id: str, driver: _RunDriver, poll_seconds: float = 0.5):
    """Block until the queue task resolves. Returns (result, status, error)."""
    queue = get_task_queue()
    while True:
        if driver.cancel_event.is_set():
            cancel_task(task_id, "cancelled by user")
            t = queue.get_task(task_id)
            return (t.result if t else None), "cancelled", "cancelled by user"
        t = queue.get_task(task_id)
        if not t:
            return None, "failed", "task disappeared from queue"
        if t.status == TaskStatus.COMPLETED:
            return t.result, "completed", None
        if t.status == TaskStatus.FAILED:
            return t.result, "failed", t.error
        if t.status == TaskStatus.CANCELLED:
            return t.result, "cancelled", t.error or "cancelled"
        driver.cancel_event.wait(poll_seconds)


def _copy_workspace_files(src_session_id: str, dst_session_id: str) -> None:
    """Best-effort copy of every regular file from src session to dst (same names)."""
    src_dir = session_store._session_dir(src_session_id)
    dst_dir = session_store._session_dir(dst_session_id, namespace=EPHEMERAL_NS)
    if not src_dir.exists():
        return
    for src_file in src_dir.rglob("*"):
        if not src_file.is_file() or src_file.name == "session.json":
            continue
        rel = src_file.relative_to(src_dir)
        if rel.parts and rel.parts[0] in (".git", ".telecode"):
            continue
        dst_file = dst_dir / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src_file, dst_file)
        except Exception:
            continue


def run_async(coro):  # pragma: no cover - convenience for sync callers
    return asyncio.run(coro)
