"""Pipeline run executor — Run → Step → Attempt, with step kinds (P3).

A Run executes a Job's ``pipeline.steps[]`` grouped by ``phase``: phases run
in order, the steps of one phase concurrently. Everything a run needs is
copied into the run record at creation (``job_snapshot``, per-step ``spec``),
so a retry hours later does not depend on the job being unchanged.

Step kinds (``spec.kind``; map / loop / gate / reduce each own their phase)
  agent   one agent run (the P2 step)
  map     fan-out: one worker per item of the previous phase's handoff field
          (``map.items_from``: next_steps | items | open_questions | decisions |
          artifacts), ``map.max_parallel`` at a time, each with an even share of
          the step budget, each in an ephemeral copy of the workspace — or, with
          ``worker_session: fork``, in its own copy of the *planner's* post-step
          workspace with a fork of the planner's conversation staged for that
          folder (``services.run.fork_workspace``), so forked workers run in
          parallel too; an engine / worker that cannot fork falls back to a fresh
          conversation seeded with the planner's handoff (``fork_fallback``).
          Workers are ``step.workers[]``; the step's handoff merges theirs, the
          next phase gets one ``<handoff>`` per worker, and nothing a worker does
          reaches the planner's workspace (only its artifacts / changed files).
  gate    … may carry ``timeout_sec`` + ``on_timeout`` (reject | approve |
          skip): the deadline is stored on the approval, and
          ``services.approvals``' checker resolves it (decided_by ``timeout``).
  loop    evaluator-optimizer: the body runs (an agent attempt), then a check —
          ``command`` (run in the workspace, exit 0 = pass), ``grader`` (another
          agent in a FRESH session, in a copy of the workspace, with a rubric →
          verdict + findings) or ``schema`` (the body's handoff, or a JSON file,
          against a JSON Schema). A failing check's findings go back to the body,
          which resumes its conversation; up to ``loop.max_iterations``.
          Iterations are ``step.iterations[]``.
  gate    human approval: an ``approvals`` row (``services.approvals``) is
          created, the step and the run become ``awaiting_input`` and the driver
          exits. The row survives a restart; approving (optionally with edited
          text, which becomes the gate's handoff note to the next step) launches a
          new driver from the next phase; rejecting ends the run ``rejected``.
  reduce  an agent given every handoff of the previous phase with instructions
          to merge them into one (fresh conversation by default).

Engine / model / local per step (B3):
  step override > run override (POST body) > agent default > claude_code / CLI default / cloud.

Session policy per step (``spec.session_policy``; blank = by position)
  resume | fork | fresh | fresh_handoff | ephemeral — see P2 notes; ephemeral is
  forced for every step of a parallel phase.

Handoffs (``services.run.handoff``), budgets (``services.run.budget``),
snapshots (``services.snapshots``), retries (``retry_step``: ``retry`` resumes /
``retry_clean`` restores + fresh) are as in P2. For a map step ``retry`` re-runs
only the workers that did not complete; for a loop it runs another round of
iterations resuming the body's conversation; for a rejected / cancelled gate it
asks again.

Trigger-fired runs (``services.triggers``) carry ``overrides.permission_mode``
(passed to every step task → Claude ``--permission-mode``), an optional
``overrides.session_policy`` (``fresh``) and ``job_snapshot.context`` /
``job_snapshot.pinned`` (the trigger preface / untrusted payload, and pinned
constraints appended at the tail of every step prompt).

Failure handling: the first phase with a non-completed step halts the run;
later pending steps are "skipped". ``cancel_run`` cancels in-flight tasks
through the shared queue cancel (CLI tree-kill) — live or orphaned — and any
pending gate approval. ``reconcile_orphaned_runs`` (startup) marks anything left
running as "interrupted" (retryable); a run waiting on a gate keeps waiting.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from services import approvals
from services import snapshots
from services.agent.agent_manager import get_agent_manager
from services.run import budget as budget_mod
from services.run import fork_workspace
from services.run import handoff as handoff_mod
from services.run.run_store import TERMINAL_RUN_STATUSES, get_run_store, usage_from_result
from services.session import session_store
from services.task.task_manager import POOL_BACKGROUND, TaskStatus, cancel_task, get_task_queue

logger = logging.getLogger("telecode.services.run.executor")

EPHEMERAL_NS = "run-parallel"

HANDOFF_CAP = 16 * 1024          # chars of reply kept in a derived handoff summary
PREVIEW_CAP = 400                # chars kept in step.result_preview for list views
RESULT_TEXT_CAP = 256 * 1024     # the full reply kept on the step (the task row has it all)
CHECK_OUTPUT_CAP = 8 * 1024      # chars of a command check's output kept as findings
MAX_FILES_LISTED = 200
MAX_FILES_SCANNED = 20000
_SCAN_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
                   ".telecode"}

POLICIES = ("resume", "fork", "fresh", "fresh_handoff", "ephemeral")
RETRY_MODES = ("retry", "retry_clean")
RETRYABLE = ("failed", "cancelled", "interrupted", "budget_exceeded", "rejected")

_TRANSIENT_RE = re.compile(
    r"overloaded|rate[ _-]?limit|too many requests|\b(429|500|502|503|504|529)\b|temporarily unavailable|"
    r"service unavailable|econnreset|etimedout|enotfound|eai_again|socket hang up|network|"
    r"connection (reset|refused|error|closed|aborted)|stream (disconnected|error)|api_error|internal server error",
    re.I)

RETRY_PROMPT = (
    "Your previous attempt at this step stopped before it finished ({reason}). Continue from where you "
    "stopped — do not redo work that is already done — and finish the step.\n\n"
    "The step, for reference:\n<step_prompt>\n{prompt}\n</step_prompt>")

LOOP_FEEDBACK_PROMPT = (
    "A check ran on the result of your previous iteration (iteration {n} of at most {max}) and it did not pass.\n"
    "<check type=\"{ctype}\">\n{findings}\n</check>\n"
    "Fix what the check found — keep the work that is already right — and finish the step again.\n\n"
    "The step, for reference:\n<step_prompt>\n{prompt}\n</step_prompt>")

FANOUT_INSTRUCTIONS = (
    "<fanout_instructions>\nThe next step fans out: one worker per entry of your handoff's `items` array. "
    "List each independent unit of work as one self-contained item (what to do, and where), and leave "
    "`items` empty if there is nothing to fan out.\n</fanout_instructions>")

REDUCE_INSTRUCTIONS = (
    "<reduce_instructions>\nYou are the reduce step of this pipeline: the handoffs above come from "
    "{n} step(s)/worker(s) of the previous phase. Combine them into one result — merge the summaries, "
    "de-duplicate the decisions, next steps and open questions, resolve conflicts between them (and say how) — "
    "and report ONE handoff for the whole phase.\n</reduce_instructions>")

GRADER_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "summary", "findings"],
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"],
                    "description": "pass = the work meets the rubric; fail = it does not"},
        "summary": {"type": "string", "description": "One or two sentences on the overall result"},
        "findings": {"type": "array", "items": {"type": "string"},
                     "description": "Specific problems to fix (empty when it passes)"},
    },
}

GRADER_PROMPT = (
    "<grading_task>\nYou are a grader. Another agent just did the work described below, in a workspace; your "
    "working directory is a copy of that workspace after the work. Inspect the files you need and judge the "
    "work strictly against the rubric. Do not fix anything yourself.\n\n"
    "<rubric>\n{rubric}\n</rubric>\n\n<task>\n{task}\n</task>\n\n{work}\n\n"
    "Reply with a verdict (pass | fail), a short summary and the findings — the specific problems the agent "
    "must fix (empty when it passes).{agy}\n</grading_task>")

_VERDICT_RE = re.compile(r"\bverdict\s*[:=]\s*\**\s*(pass|fail)\b", re.I)

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


def _kind(step: Dict[str, Any]) -> str:
    return str((step.get("spec") or {}).get("kind") or step.get("kind") or "agent")


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
        # step > run override (a trigger's effort); "" = the CLI's default
        "effort": str(step.get("effort") or overrides.get("effort") or ""),
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
        or (policy == "fork" and engine == "antigravity") or spec.get("kind") == "reduce"


def _build_step_prompt(job: Dict[str, Any], step: Dict[str, Any], prev_outputs: Optional[List[Dict[str, Any]]],
                       include_handoff: Optional[bool] = None, engine: Optional[str] = None,
                       lead: str = "", extra: str = "") -> str:
    """Step prompt = prompt override or the job prompt, + ``lead`` (a map item),
    + the run's trigger context, + the previous phase's ``<handoff>`` block(s)
    when it depends on them, + ``extra`` (fan-out / reduce instructions),
    + (in a run) the ``<handoff_instructions>`` for ``engine``, + pinned
    constraints at the very tail.

    ``prev_outputs``: [{step_id, name, status, text, handoff?, files_changed?}]
    — an output without a handoff gets one derived from its text."""
    base = (step.get("prompt_override") or job.get("task_description") or "").strip()
    base = base or "(no prompt provided)"
    if lead:
        base = f"{base}\n\n{lead}"
    if (job.get("context") or "").strip():
        base = f"{base}\n\n{job['context'].strip()}"
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
    if extra:
        base = f"{base}\n\n{extra}"
    if engine:
        base = f"{base}\n\n{handoff_mod.instructions(engine)}"
    if (job.get("pinned") or "").strip():
        base = f"{base}\n\n<pinned_constraints>\n{job['pinned'].strip()}\n</pinned_constraints>"
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

def _step_spec(s: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(s.get("kind") or "agent")
    spec = {
        "phase": int(s.get("phase") or 0),
        "kind": kind,
        "prompt_override": s.get("prompt_override") or "",
        "depends_on_text": bool(s.get("depends_on_text")) or kind == "reduce",
        "session_policy": (s.get("session_policy") or "").strip().lower(),
        "budget": {k: v for k, v in budget_mod.normalize(s.get("budget")).items() if v is not None},
        "auto_retry": max(0, min(5, int(s.get("auto_retry") or 0))),
    }
    for k in ("map", "loop", "gate"):
        if isinstance(s.get(k), dict):
            spec[k] = dict(s[k])
    if kind == "reduce" and not spec["session_policy"]:
        spec["session_policy"] = "fresh"
    return spec


async def start_run(
    job: Dict[str, Any],
    is_local: Optional[bool] = None,
    source: str = "user",
    engine: Optional[str] = None,
    model: Optional[str] = None,
    budget: Optional[Dict[str, Any]] = None,
    trigger: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a Run record and launch the driver thread. Returns the new run dict.

    ``engine`` / ``model`` / ``is_local`` / ``budget`` are run-level overrides
    (None / "" = not set). ``trigger`` (set by services.triggers):
    {id, fire_id, context, pinned, permission_mode, session_policy}."""
    return create_and_launch(job, is_local=is_local, source=source, engine=engine, model=model,
                             budget=budget, trigger=trigger)


def create_and_launch(job: Dict[str, Any], *, is_local: Optional[bool] = None, source: str = "user",
                      engine: Optional[str] = None, model: Optional[str] = None,
                      budget: Optional[Dict[str, Any]] = None,
                      trigger: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Synchronous body of :func:`start_run` (the trigger scheduler calls it
    from its own thread)."""
    pipeline = job.get("pipeline") or {"mode": "single", "steps": []}
    steps_in = pipeline.get("steps") or []
    if not steps_in:
        raise ValueError("Job has no pipeline steps to run")

    from services.task.engine_map import supported_engines
    eng = (engine or "").strip().lower()
    if eng and eng not in supported_engines():
        raise ValueError(f"engine must be one of {supported_engines()}, got {engine!r}")
    trigger = trigger or {}
    overrides = {"engine": eng, "model": (model or "").strip(),
                 "is_local": None if is_local is None else bool(is_local)}
    if trigger.get("permission_mode"):
        overrides["permission_mode"] = trigger["permission_mode"]
    elif job.get("permission_mode") and job.get("permission_mode") not in ("skip", "bypassPermissions"):
        overrides["permission_mode"] = job["permission_mode"]      # P5: a job can ask for "ask" / "auto"
    if trigger.get("session_policy"):
        overrides["session_policy"] = trigger["session_policy"]
    if trigger.get("effort"):
        overrides["effort"] = trigger["effort"]
    run_budget = {k: v for k, v in budget_mod.merge(budget, job.get("budget")).items() if v is not None}

    phase_sizes: Dict[int, int] = {}
    for s in steps_in:
        p = int(s.get("phase") or 0)
        phase_sizes[p] = phase_sizes.get(p, 0) + 1

    agent_mgr = get_agent_manager()
    decorated = []
    for s in steps_in:
        agent = (agent_mgr.get_agent(s.get("agent_id")) if s.get("agent_id") else None) or {}
        cfg = _resolve_step_config(s, overrides, agent)
        spec = _step_spec(s)
        if overrides.get("session_policy") and spec["kind"] != "gate" and not spec["session_policy"]:
            spec["session_policy"] = overrides["session_policy"]
        decorated.append({
            "step_id": s.get("step_id") or str(uuid.uuid4()),
            "agent_id": s.get("agent_id"),
            "agent_name": agent.get("name", ""),
            "name": s.get("name", "") or ((spec.get("gate") or {}).get("title") if spec["kind"] == "gate" else ""),
            "kind": spec["kind"],
            "spec": spec,
            "session_policy": _resolve_policy(spec["session_policy"], phase_sizes[spec["phase"]] > 1),
            **cfg,
        })

    snap = {"title": job.get("title", ""), "task_description": job.get("task_description", ""),
            "workspace_id": job.get("workspace_id")}
    if job.get("outcome_check"):
        snap["outcome_check"] = job["outcome_check"]              # P5: run after the run, exit 0 = pass
    if trigger.get("context"):
        snap["context"] = trigger["context"]
    if trigger.get("pinned"):
        snap["pinned"] = trigger["pinned"]
    run = get_run_store().create_run(
        job_id=job["id"], mode=pipeline.get("mode", "single"), source=source, steps=decorated,
        overrides=overrides, budget=run_budget, job_snapshot=snap,
        extra={"trigger_id": trigger.get("id"), "trigger_fire_id": trigger.get("fire_id")} if trigger else None,
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
    """Cancel a run — live, orphaned or waiting on a gate. Returns False if
    unknown or finished."""
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
    try:
        approvals.cancel_for(run_id, reason="run cancelled")
    except Exception:
        logger.exception("cancelling the run's pending approvals failed")

    now = _now_iso()

    def fn(r):
        for s in r.get("steps", []):
            if s.get("status") == "pending":
                s["status"] = "skipped"
            elif s.get("status") in ("running", "awaiting_input"):
                if s.get("task_id"):
                    cancel_task(s["task_id"], "cancelled by user")
                for w in s.get("workers") or []:
                    if w.get("status") in ("running", "pending"):
                        if w.get("task_id"):
                            cancel_task(w["task_id"], "cancelled by user")
                        w.update({"status": "cancelled", "completed_at": now})
                s.update({"status": "cancelled", "completed_at": now, "error": "cancelled by user"})
                if s.get("attempts") and s["attempts"][-1].get("status") == "running":
                    s["attempts"][-1].update({"status": "cancelled", "completed_at": now})
        r["status"] = "cancelled"
    store.mutate(run_id, fn)
    store.finalise(run_id)
    if not driver:
        _verdict(run_id)
    return True


def reconcile_orphaned_runs() -> int:
    """Startup pass: runs left pending/running by a previous process.

    A run with a live driver in this process is left alone, and so is a run
    waiting on a gate (``awaiting_input`` — its approval row is its state).
    Otherwise every running step (and map worker) whose task is not alive in
    the queue becomes "interrupted" (pending ones "skipped") and the run is
    finalised. Returns runs touched.
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
                for w in s.get("workers") or []:
                    if w.get("status") in ("running", "pending") and not queue.is_active(w.get("task_id")):
                        w.update({"status": "interrupted", "completed_at": now})
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
    # Gate deadlines live in the approvals table: the checker catches up on any
    # that passed while telecode was down.
    try:
        if approvals.has_deadlines():
            approvals.start_timeout_checker()
    except Exception:
        logger.exception("starting the approval timeout checker failed")
    return touched


def _find_step(run: Dict[str, Any], step_id: str) -> Optional[Dict[str, Any]]:
    return next((s for s in run.get("steps") or [] if s.get("step_id") == step_id), None)


def retry_step(run_id: str, step_id: str, mode: str = "retry",
               budget: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Re-run one step (``retry`` | ``retry_clean``) and continue downstream.

    Raises LookupError (unknown run/step), ValueError (bad mode / step state /
    budget), RunBusy (the run has a live driver or waits on a gate)."""
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
    if run.get("status") == "awaiting_input":
        raise RunBusy("run is waiting for an approval — decide it or cancel the run")
    st = step.get("status")
    kind = _kind(step)
    if kind == "gate":
        if st not in ("rejected", "cancelled", "interrupted", "failed"):
            raise ValueError(f"cannot retry a {st} gate — only a rejected or cancelled one can be asked again")
    elif st == "completed" and mode == "retry":
        raise ValueError("this step completed — use retry_clean to redo it from its pre-step snapshot")
    if st not in RETRYABLE + ("completed",):
        raise ValueError(f"cannot retry a {st} step")
    phase = int((step.get("spec") or {}).get("phase") or 0)

    def reset(r):
        for s in r.get("steps") or []:
            ph = int((s.get("spec") or {}).get("phase") or 0)
            if s.get("step_id") == step_id or ph > phase:
                s.update({"status": "pending", "error": None, "completed_at": None})
                if _kind(s) == "gate":
                    for k in ("approval_id", "gate_decision", "deadline_at", "on_timeout"):
                        s.pop(k, None)
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
              path: Optional[str] = None, worker: Optional[int] = None) -> Dict[str, Any]:
    """Unified diff of one attempt (default: the latest) — before → after — or
    of one map worker (``worker=n``)."""
    run = get_run_store().get_run(run_id)
    if not run:
        raise LookupError("Run not found")
    step = _find_step(run, step_id)
    if not step:
        raise LookupError("Step not found in this run")
    if worker is not None:
        a = next((w for w in step.get("workers") or [] if int(w.get("n") or 0) == int(worker)), None)
        if not a:
            raise LookupError("No such worker in this step")
    else:
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
    return {"run_id": run_id, "step_id": step_id, "attempt": a.get("n"), "worker": worker, "snapshot_key": key,
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
    if step.get("session_policy") == "ephemeral" or _kind(step) == "map":
        raise ValueError("this step ran in throwaway copies of the workspace — there is nothing to revert "
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


# ── Gate decisions (approvals handler) ─────────────────────────────────────

def _on_gate_decided(ap: Dict[str, Any]) -> None:
    """Approve → the gate completes (edited text = its handoff note) and a new
    driver continues from the next phase. Reject → the run ends ``rejected``."""
    run_id, step_id = ap.get("run_id"), ap.get("step_id")
    if not run_id or not step_id or ap.get("status") not in ("approved", "rejected", "skipped"):
        return
    store = get_run_store()
    run = store.get_run(run_id)
    step = _find_step(run, step_id) if run else None
    if not step or step.get("status") != "awaiting_input" or step.get("approval_id") != ap.get("id"):
        logger.info(f"gate decision for {run_id[:8]}/{step_id[:8]} ignored (step not waiting on it)")
        return
    now = _now_iso()
    who = ap.get("decided_by") or "someone"
    note = (ap.get("decision_note") or "").strip()
    phase = int((step.get("spec") or {}).get("phase") or 0)
    if ap["status"] == "rejected":
        def rej(r):
            for s in r.get("steps") or []:
                if s.get("step_id") == step_id:
                    s.update({"status": "rejected", "completed_at": now,
                              "error": f"rejected by {who}" + (f": {note}" if note else ""),
                              "gate_decision": {"status": "rejected", "by": who, "note": note, "at": now}})
                elif int((s.get("spec") or {}).get("phase") or 0) > phase and s.get("status") == "pending":
                    s["status"] = "skipped"
        store.mutate(run_id, rej)
        store.finalise(run_id)
        _verdict(run_id)
        return
    if ap["status"] == "skipped":
        # on_timeout: skip — the gate is passed over without a decision; the next
        # phase still gets what the gate was shown (gate_input), but no gate note.
        store.update_step(run_id, step_id, {
            "status": "skipped", "completed_at": now, "error": None,
            "gate_decision": {"status": "skipped", "by": who, "note": note, "at": now}})
        store.update_run(run_id, {"status": "running", "completed_at": None})
        try:
            _launch(run_id, run.get("source") or "user", from_phase=phase + 1, retry=None)
        except RunBusy:
            logger.warning(f"run {run_id[:8]}: gate skipped while a driver is active")
        return
    edited = ap.get("edited_text")
    summary = (edited or "").strip() or (f"Approved by {who}" + (f": {note}" if note else "."))
    ho = {"status": "done", "summary": summary,
          "decisions": [f"Approved by {who}" + (f" — {note}" if note and edited else "")],
          "artifacts": [], "open_questions": [], "next_steps": [], "items": [], "verdict": "pass", "derived": False,
          "gate": {"approval_id": ap["id"], "decided_by": who, "edited": bool((edited or "").strip()), "note": note}}
    store.update_step(run_id, step_id, {
        "status": "completed", "completed_at": now, "handoff": ho, "result_text": summary,
        "result_preview": summary[:PREVIEW_CAP],
        "gate_decision": {"status": "approved", "by": who, "note": note, "edited": bool(edited), "at": now}})
    store.update_run(run_id, {"status": "running", "completed_at": None})
    try:
        _launch(run_id, run.get("source") or "user", from_phase=phase + 1, retry=None)
    except RunBusy:
        logger.warning(f"run {run_id[:8]}: gate approved while a driver is active")


approvals.register_handler("gate", _on_gate_decided)


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
        _verdict(run_id)


def _verdict(run_id: str) -> None:
    """P5: verdict + outcome check + the run's invoke_workflow span (never raises)."""
    try:
        from services.telemetry import verdict
        verdict.apply(run_id)
    except Exception:
        logger.exception(f"run {run_id[:8]}: verdict failed")


def _gate_passed(step: Dict[str, Any]) -> bool:
    """A finished gate phase lets the run go on: approved, or skipped by its
    on_timeout policy (not a gate skipped because the run halted earlier)."""
    return step.get("status") == "completed" or (
        step.get("status") == "skipped" and (step.get("gate_decision") or {}).get("status") == "skipped")


def _phase_of(step: Dict[str, Any]) -> int:
    return int((step.get("spec") or {}).get("phase") or 0)


def _stored_output(step: Dict[str, Any]) -> Dict[str, Any]:
    return {"step_id": step["step_id"], "name": step.get("name") or "", "status": step.get("status"),
            "text": step.get("result_text"), "handoff": step.get("handoff"),
            "files_changed": step.get("files_changed") or [], "engine": step.get("engine"),
            "engine_session_id": step.get("engine_session_id"), "session_policy": step.get("session_policy"),
            "session_id": step.get("session_id"), "kind": _kind(step)}


def _phase_outputs(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """What the next phase sees of a finished phase: a map step contributes one
    output per worker, a gate passes on what it was shown plus its own note."""
    out: List[Dict[str, Any]] = []
    for s in steps:
        k = _kind(s)
        if k == "map" and s.get("workers"):
            label = s.get("name") or s.get("agent_name") or "map"
            for w in s["workers"]:
                out.append({"step_id": s["step_id"], "name": f"{label} #{w.get('n')}", "status": w.get("status"),
                            "text": w.get("result_text"), "handoff": w.get("handoff"), "files_changed": [],
                            "engine": s.get("engine"), "engine_session_id": w.get("engine_session_id"),
                            "session_policy": "ephemeral", "session_id": w.get("session_id"), "kind": "map",
                            "worker": w.get("n")})
        elif k == "gate":
            out.extend(dict(o) for o in (s.get("gate_input") or []))
            if s.get("handoff"):
                out.append(_stored_output(s))
        else:
            out.append(_stored_output(s))
    return out


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
        prev_outputs = _phase_outputs([by_id[i] for i in phases[earlier[-1]]])
    halt = False
    for p in [x for x in order if x >= from_phase]:
        run = store.get_run(run_id)
        by_id = {s["step_id"]: s for s in run["steps"]}
        pending = [i for i in phases[p] if by_id[i].get("status") == "pending"]
        if halt or driver.cancel_event.is_set():
            for i in pending:
                store.update_step(run_id, i, {"status": "skipped"})
            continue
        kind = _kind(by_id[phases[p][0]]) if len(phases[p]) == 1 else "agent"
        if kind == "gate":
            if pending:
                _open_gate(run_id, pending[0], prev_outputs)
                return                                  # the driver ends; the run waits for a decision
            prev_outputs = _phase_outputs([by_id[i] for i in phases[p]])
            if any(not _gate_passed(by_id[i]) for i in phases[p]):
                halt = True
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
        nxt = next((x for x in order if x > p), None)
        fanout = False
        if nxt is not None and len(phases[nxt]) == 1:
            ns_ = by_id[phases[nxt][0]]
            fanout = _kind(ns_) == "map" and ((ns_.get("spec") or {}).get("map") or {}).get("items_from") == "items"
        t0 = time.monotonic()

        def one(i: str) -> None:
            mode = retry["mode"] if retry and retry.get("step_id") == i else "run"
            ovr = retry.get("budget") if retry and retry.get("step_id") == i else None
            k = _kind(by_id[i])
            try:
                if k == "map":
                    _execute_map(run_id, i, prev_outputs, driver, mode=mode, budget_override=ovr,
                                 steps_left=steps_left, phases_left=phases_left, source=source)
                elif k == "loop":
                    _execute_loop(run_id, i, prev_outputs, driver, mode=mode, budget_override=ovr,
                                  steps_left=steps_left, phases_left=phases_left, source=source,
                                  extra=FANOUT_INSTRUCTIONS if fanout else "")
                else:
                    extra = FANOUT_INSTRUCTIONS if fanout else ""
                    if k == "reduce":
                        extra = (REDUCE_INSTRUCTIONS.format(n=len(prev_outputs)) + ("\n\n" + extra if extra else ""))
                    _execute_step(run_id, i, prev_outputs, driver, mode=mode, budget_override=ovr,
                                  steps_left=steps_left, phases_left=phases_left, source=source, extra=extra)
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
        prev_outputs = _phase_outputs([by_id[i] for i in phases[p]])
        if any(by_id[i].get("status") != "completed" for i in phases[p]):
            halt = True


def _open_gate(run_id: str, step_id: str, prev_outputs: List[Dict[str, Any]]) -> None:
    """A gate step: persist an approval (reusing a pending one), mark the step
    and the run ``awaiting_input``."""
    store = get_run_store()
    run = store.get_run(run_id)
    step = _find_step(run, step_id) or {}
    gate = (step.get("spec") or {}).get("gate") or {}
    passthrough = [{k: v for k, v in o.items() if k != "events"} for o in prev_outputs]
    lines = []
    if gate.get("instructions"):
        lines.append(gate["instructions"])
    for o in passthrough:
        ho = o.get("handoff") or {}
        if ho or o.get("text"):
            lines.append(f"── {o.get('name') or 'previous step'} ({ho.get('status', o.get('status'))}, "
                         f"verdict {ho.get('verdict', 'unknown')}) ──\n"
                         + (ho.get("summary") or _cap_head_tail(o.get("text") or "", 4000)))
    ap = approvals.find_pending(run_id, step_id)
    if ap is None:
        tmo = int(gate.get("timeout_sec") or 0)
        ap = approvals.create(
            "gate", run_id=run_id, step_id=step_id,
            title=gate.get("title") or step.get("name") or "Approval",
            body="\n\n".join(lines)[:approvals.BODY_CAP],
            payload={"job_id": run.get("job_id"), "job_title": (run.get("job_snapshot") or {}).get("title"),
                     "instructions": gate.get("instructions") or "",
                     "previous": [{"name": o.get("name"), "status": o.get("status"),
                                   "summary": ((o.get("handoff") or {}).get("summary") or "")[:4000],
                                   "verdict": (o.get("handoff") or {}).get("verdict")} for o in passthrough]},
            deadline_at=approvals.deadline_in(tmo) if tmo > 0 else None,
            on_timeout=(gate.get("on_timeout") or "reject") if tmo > 0 else None)
    store.update_step(run_id, step_id, {"status": "awaiting_input", "started_at": step.get("started_at") or _now_iso(),
                                        "completed_at": None, "error": None, "approval_id": ap["id"],
                                        "gate_input": passthrough, "deadline_at": ap.get("deadline_at"),
                                        "on_timeout": ap.get("on_timeout")})
    store.update_run(run_id, {"status": "awaiting_input"})
    logger.info(f"run {run_id[:8]}: gate {step_id[:8]} waiting for approval {ap['id'][:8]}")


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
                  source: str, extra: str = "") -> Dict[str, Any]:
    store = get_run_store()
    auto = int((((_find_step(store.get_run(run_id), step_id) or {}).get("spec")) or {}).get("auto_retry") or 0)
    n_auto = 0
    while True:
        out = _attempt(run_id, step_id, prev_outputs, driver, mode=mode, budget_override=budget_override,
                       steps_left=steps_left, phases_left=phases_left, source=source, extra=extra)
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


def _ephemeral_session(run_id: str, step_id: str, ws_id: Optional[str], keep: bool,
                       suffix: str = "") -> Tuple[str, Path]:
    sid = f"run-{run_id[:8]}-{step_id[:8]}{suffix}"
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


def _run_unit(run: Dict[str, Any], step: Dict[str, Any], *, driver: _RunDriver, source: str, label: str,
              sid: str, ns: Optional[str], work_dir: Path, key: str, snap_meta: Dict[str, Any], prompt: str,
              ctl: Dict[str, Any], engine: str, model: Optional[str], is_local: bool, agent_id: Optional[str],
              schema: Optional[Dict[str, Any]], meta: Dict[str, Any],
              on_submitted: Optional[Callable[[str, Optional[str]], None]] = None) -> Dict[str, Any]:
    """One CLI run for a step (or a map worker / loop grader): before-snapshot,
    submit to the background pool, wait, after-snapshot, changed files, usage.
    Returns the raw outcome; the caller resolves the handoff and records it."""
    queue = get_task_queue()
    before = snapshots.take(key, work_dir, f"before {label}", {**snap_meta, "phase": "before"})
    before_scan = None if before else _scan_files(work_dir)
    params: Dict[str, Any] = {"prompt": prompt, "is_local": bool(is_local), "agent_id": agent_id, "step_ctl": ctl}
    if model:
        params["model"] = model
    if schema and engine in ("claude_code", "codex"):
        params["schema"] = schema
    overrides = run.get("overrides") or {}
    md = {"source": source, "job_id": run.get("job_id"), "run_id": run["run_id"], "step_id": step["step_id"],
          "agent_id": agent_id, "engine": engine, **meta}
    if overrides.get("permission_mode"):
        md["permission_mode"] = overrides["permission_mode"]
    if step.get("effort") or overrides.get("effort"):
        md["effort"] = step.get("effort") or overrides.get("effort")   # → EngineRequest.effort
    if run.get("trigger_id"):
        md["trigger_id"] = run["trigger_id"]
    task_id = queue.submit_task(task_type=_engine_to_task_type(engine), params=params, metadata=md,
                                session_id=sid, session_namespace=ns, pool=POOL_BACKGROUND)
    with driver.lock:
        driver.active_task_ids.append(task_id)
    if on_submitted:
        on_submitted(task_id, before)
    result_obj, status, error = _wait_for_task(task_id, driver)
    with driver.lock:
        if task_id in driver.active_task_ids:
            driver.active_task_ids.remove(task_id)
    task = queue.get_task(task_id)
    tmd = dict(task.metadata) if task else {}
    if status == "failed" and (error or "").startswith("budget_exceeded"):
        status = "budget_exceeded"
    after = snapshots.take(key, work_dir, f"after {label} ({status})", {**snap_meta, "phase": "after"}) \
        if before else None
    if before and after:
        files = [{k: f[k] for k in ("path", "change", "additions", "deletions")}
                 for f in snapshots.changed_files(key, before, after)][:MAX_FILES_LISTED]
    else:
        files = _diff_files(before_scan or {}, _scan_files(work_dir))
    return {
        "task_id": task_id, "status": status, "error": error, "result": result_obj,
        "text": _result_text(result_obj),
        "structured": result_obj.get("structured_output") if isinstance(result_obj, dict) else None,
        "files": files, "usage": usage_from_result(result_obj) or _usage_from_live(tmd),
        "esid": tmd.get("engine_session_id") or _engine_session_from_result(result_obj),
        "before": before, "after": after, "key": key if before else None, "events": tmd.get("events") or [],
    }


def _base_ctl(run: Dict[str, Any], run_id: str, prev_outputs: List[Dict[str, Any]]) -> Dict[str, Any]:
    ctl: Dict[str, Any] = {"handoff": True}
    add_dirs: List[str] = []
    for o in prev_outputs:
        d = handoff_mod.artifacts_dir(run_id, o["step_id"])
        if d.is_dir() and str(d) not in add_dirs:
            add_dirs.append(str(d))
    if add_dirs:
        ctl["add_dirs"] = add_dirs
    pinned = (run.get("job_snapshot") or {}).get("pinned")
    if pinned:
        ctl["pinned"] = pinned
    return ctl


def _attempt(run_id: str, step_id: str, prev_outputs: List[Dict[str, Any]], driver: _RunDriver, *,
             mode: str, budget_override: Optional[Dict[str, Any]], steps_left: int, phases_left: int,
             source: str, extra: str = "", feedback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One attempt of an agent / reduce step (also a loop iteration's body:
    ``feedback`` = {findings, ctype, n, max} resumes the previous attempt's
    conversation with the check's findings)."""
    store = get_run_store()
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
    rec_mode = "iteration" if feedback else mode

    def fail_now(status: str, error: str) -> Dict[str, Any]:
        store.update_step(run_id, step_id, {"status": status, "completed_at": _now_iso(), "error": error})
        return {**_stored_output({**step, "status": status}), "error": error}

    if driver.cancel_event.is_set():
        return fail_now("skipped", "run cancelled")

    # Where the attempt runs.
    if policy == "ephemeral":
        keep = (mode == "retry" or bool(feedback)) and bool(last and last.get("engine_session_id"))
        sid, work_dir = _ephemeral_session(run_id, step_id, ws_id, keep)
        ns: Optional[str] = EPHEMERAL_NS
    else:
        if not ws_id:
            return fail_now("failed", "Job has no workspace_id")
        sid, ns = ws_id, None
        session_store.ensure(sid)
        work_dir = session_store._session_dir(sid)
    key = snapshots.key_for(sid, ns)
    meta = {"run": run_id, "step": step_id, "attempt": n, "mode": rec_mode}

    restored = None
    if mode == "retry_clean" and policy != "ephemeral" and not feedback:
        target = next((a.get("snapshot_before") for a in attempts if a.get("snapshot_before")), None)
        if target and snapshots.exists(key, target):
            try:
                restored = snapshots.restore(key, work_dir, target, label=f"retry clean: restore before {label}",
                                             meta=meta)
            except snapshots.SnapshotError as exc:
                logger.warning(f"retry_clean restore failed: {exc}")
        else:
            logger.warning(f"run {run_id[:8]} step {step_id[:8]}: no pre-step snapshot to restore for retry_clean")

    eff = budget_mod.for_step(run, step, steps_left=steps_left, phases_left=phases_left, override=budget_override)
    caps = {k: eff[k] for k in budget_mod.DIMS if eff.get(k) is not None}
    attempt_rec: Dict[str, Any] = {
        "n": n, "mode": rec_mode, "task_id": None, "status": "running", "error": None,
        "started_at": _now_iso(), "completed_at": None, "engine_session_id": None,
        "snapshot_key": None, "snapshot_before": None, "snapshot_after": None,
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
    ctl = {**_base_ctl(run, run_id, prev_outputs), "policy": policy, "session": _session_mode(policy)}
    if caps:
        ctl["budget"] = caps
    base_prompt = _build_step_prompt(snap, spec, prev_outputs, _include_handoff(spec, policy, engine), engine,
                                     extra=extra)
    if feedback and last and last.get("engine_session_id"):
        ctl["resume_id"] = last["engine_session_id"]
        prompt = LOOP_FEEDBACK_PROMPT.format(n=feedback["n"], max=feedback["max"], ctype=feedback["ctype"],
                                             findings=_cap_head_tail(feedback["findings"] or "(no details)", 8000),
                                             prompt=_cap_head_tail(base_prompt, HANDOFF_CAP))
        prompt += "\n\n" + handoff_mod.instructions(engine)
    elif feedback:
        ctl["session"] = "fresh"
        prompt = base_prompt + "\n\n" + LOOP_FEEDBACK_PROMPT.split("\n\nThe step, for reference")[0].format(
            n=feedback["n"], max=feedback["max"], ctype=feedback["ctype"],
            findings=_cap_head_tail(feedback["findings"] or "(no details)", 8000))
    elif mode == "retry" and last and last.get("engine_session_id"):
        ctl["resume_id"] = last["engine_session_id"]
        reason = (last.get("error") or step.get("error") or last.get("status") or "stopped")
        prompt = RETRY_PROMPT.format(reason=str(reason)[:300], prompt=_cap_head_tail(base_prompt, HANDOFF_CAP))
        prompt += "\n\n" + handoff_mod.instructions(engine)
    else:
        if mode in ("retry", "retry_clean"):
            ctl["session"] = "fresh"
        prompt = base_prompt
    if ctl["session"] == "fork" and len(prev_outputs) == 1 and not ctl.get("resume_id"):
        o = prev_outputs[0]
        if o.get("engine") == engine and o.get("engine_session_id") and o.get("session_policy") != "ephemeral":
            ctl["fork_from"] = o["engine_session_id"]

    def submitted(task_id: str, before: Optional[str]) -> None:
        attempt_rec.update({"task_id": task_id, "snapshot_key": key if before else None, "snapshot_before": before})
        store.update_step(run_id, step_id, {
            "status": "running", "started_at": attempt_rec["started_at"], "completed_at": None, "error": None,
            "task_id": task_id, "session_id": sid, "session_namespace": ns,
            "snapshot_key": key if before else None, "snapshot_before": before, "snapshot_after": None,
            "budget": attempt_rec["budget"], "attempts": attempts + [attempt_rec]})

    out = _run_unit(run, step, driver=driver, source=source, label=f"{label} · attempt {n}", sid=sid, ns=ns,
                    work_dir=work_dir, key=key, snap_meta=meta, prompt=prompt, ctl=ctl, engine=engine,
                    model=step.get("model"), is_local=bool(step.get("is_local")), agent_id=step.get("agent_id"),
                    schema=handoff_mod.HANDOFF_SCHEMA,
                    meta={"attempt": n, "attempt_mode": rec_mode, "session_policy": policy,
                          **({"ephemeral_session": True} if policy == "ephemeral" else {})},
                    on_submitted=submitted)
    status, error, text, files = out["status"], out["error"], out["text"], out["files"]
    ho = handoff_mod.resolve(out["structured"], text, step_status=status, error=error, work_dir=work_dir)
    handoff_mod.collect_artifacts(run_id, step_id, work_dir, ho, files, include_changed=(policy == "ephemeral"))
    if not text and not ho.get("derived"):
        text = ho.get("summary") or ""

    attempt_rec.update({"status": status, "error": error, "completed_at": _now_iso(), "engine_session_id": out["esid"],
                        "snapshot_after": out["after"], "usage": out["usage"], "files_changed": len(files),
                        "handoff_status": ho.get("status"), "verdict": ho.get("verdict")})
    result_obj = out["result"]
    if isinstance(result_obj, dict) and result_obj.get("rotation"):
        rot = result_obj["rotation"]
        attempt_rec["rotated_from"] = {"session": rot.get("from_session"), "reason": rot.get("reason")}
    cur = _find_step(store.get_run(run_id), step_id) or step
    store.update_step(run_id, step_id, {
        "status": status, "completed_at": attempt_rec["completed_at"], "error": error,
        "result_text": text[:RESULT_TEXT_CAP] if text else None,
        "result_preview": text[:PREVIEW_CAP] if text else None,
        "files_changed": files, "handoff": ho, "engine_session_id": out["esid"], "snapshot_after": out["after"],
        "usage": budget_mod.usage_add(cur.get("usage"), out["usage"]),
        "attempts": (list(cur.get("attempts") or [])[:len(attempts)]) + [attempt_rec]})

    if policy == "ephemeral" and status == "completed" and not feedback and _kind(step) != "loop":
        try:
            session_store.delete(sid, namespace=EPHEMERAL_NS)   # the shadow repo stays, for the diff
        except Exception as exc:
            logger.warning(f"could not delete ephemeral run session {sid}: {exc}")

    return {"step_id": step_id, "name": step.get("name") or "", "status": status, "error": error,
            "text": text, "handoff": ho, "files_changed": files, "engine": engine,
            "engine_session_id": out["esid"], "session_policy": policy, "events": out["events"],
            "work_dir": str(work_dir), "session_id": sid, "session_namespace": ns}


# ── map ────────────────────────────────────────────────────────────────────

def _map_items(prev_outputs: List[Dict[str, Any]], source: str, limit: int) -> List[str]:
    out: List[str] = []
    for o in prev_outputs:
        ho = o.get("handoff") or (handoff_mod.derive(o.get("text") or "", step_status=o.get("status") or "completed")
                                  if (o.get("text") or "").strip() else {})
        if source == "artifacts":
            vals = [(a.get("path") or "") + (f" — {a['description']}" if a.get("description") else "")
                    for a in ho.get("artifacts") or [] if not a.get("missing")]
        else:
            vals = ho.get(source) or []
        for v in vals:
            v = str(v).strip()
            if v and v not in out:
                out.append(v)
    return out[:limit]


def _update_worker(run_id: str, step_id: str, n: int, patch: Dict[str, Any],
                   usage: Optional[Dict[str, Any]] = None) -> None:
    def fn(r):
        for s in r.get("steps") or []:
            if s.get("step_id") != step_id:
                continue
            for w in s.get("workers") or []:
                if int(w.get("n") or 0) == n:
                    w.update(patch)
            if usage:
                s["usage"] = budget_mod.usage_add(s.get("usage"), usage)
    get_run_store().mutate(run_id, fn)


def _execute_map(run_id: str, step_id: str, prev_outputs: List[Dict[str, Any]], driver: _RunDriver, *,
                 mode: str, budget_override: Optional[Dict[str, Any]], steps_left: int, phases_left: int,
                 source: str) -> None:
    store = get_run_store()
    run = store.get_run(run_id)
    step = _find_step(run, step_id)
    spec = step.get("spec") or {}
    mcfg = spec.get("map") or {}
    items = _map_items(prev_outputs, mcfg.get("items_from") or "next_steps", int(mcfg.get("max_items") or 20))
    old = {w.get("item"): w for w in step.get("workers") or []} if mode == "retry" else {}
    workers = []
    for i, it in enumerate(items, start=1):
        prev_w = old.get(it)
        if prev_w and prev_w.get("status") == "completed":
            workers.append({**prev_w, "n": i})
        else:
            workers.append({"n": i, "item": it, "status": "pending", "task_id": None, "error": None,
                            "started_at": None, "completed_at": None, "handoff": None, "usage": None})
    now = _now_iso()
    if not items:
        src = mcfg.get("items_from") or "next_steps"
        msg = f"map: the previous phase's handoff had no {src} — nothing to fan out"
        store.update_step(run_id, step_id, {
            "status": "completed", "started_at": now, "completed_at": now, "workers": [], "error": None,
            "handoff": {**handoff_mod.derive(msg, step_status="completed"), "derived": False, "status": "done",
                        "summary": msg}, "result_text": msg, "result_preview": msg})
        return
    todo = [w for w in workers if w.get("status") != "completed"]
    eff = budget_mod.for_step(run, step, steps_left=steps_left, phases_left=phases_left, override=budget_override)
    par = max(1, int(mcfg.get("max_parallel") or 3))
    waves = max(1, math.ceil(len(todo) / par))
    share: Dict[str, Any] = {}
    for k in budget_mod.DIMS:
        v = eff.get(k)
        if v is None:
            continue
        v = v / waves if k == "max_seconds" else v / max(1, len(todo))
        share[k] = int(v) if k == "max_tokens" else round(v, 6)
    store.update_step(run_id, step_id, {"status": "running", "started_at": now, "completed_at": None, "error": None,
                                        "workers": workers, "budget": {**share, "source": eff.get("source") or {},
                                                                       "per": "worker"}})
    exhausted_dim = next((k for k, v in share.items() if v is not None and v <= 0), None)
    if exhausted_dim:
        store.update_step(run_id, step_id, {"status": "budget_exceeded", "completed_at": _now_iso(),
                                            "error": f"budget_exceeded: no {exhausted_dim} left for the workers"})
        return
    agents_prev = [o for o in prev_outputs if o.get("kind") != "gate"]     # a gate in between passes the planner on
    planner = agents_prev[0] if len(agents_prev) == 1 else None
    with ThreadPoolExecutor(max_workers=par, thread_name_prefix=f"map-{step_id[:6]}") as pool:
        futs = [pool.submit(_run_map_worker, run_id, step_id, w, len(workers), prev_outputs, planner, share,
                            driver, source) for w in todo]
        for f in futs:
            try:
                f.result()
            except Exception:
                logger.exception(f"map worker crashed in run {run_id[:8]}")
    _finish_map(run_id, step_id, driver)


def _run_map_worker(run_id: str, step_id: str, w: Dict[str, Any], total: int, prev_outputs: List[Dict[str, Any]],
                    planner: Optional[Dict[str, Any]], share: Dict[str, Any], driver: _RunDriver,
                    source: str) -> None:
    store = get_run_store()
    n = int(w["n"])
    if driver.cancel_event.is_set():
        _update_worker(run_id, step_id, n, {"status": "cancelled", "completed_at": _now_iso()})
        return
    run = store.get_run(run_id)
    step = _find_step(run, step_id)
    spec = step.get("spec") or {}
    snap = run.get("job_snapshot") or {}
    engine = step.get("engine") or "claude_code"
    mcfg = spec.get("map") or {}
    ctl = {**_base_ctl(run, run_id, prev_outputs), "budget": {k: v for k, v in share.items()} or None}
    if not ctl.get("budget"):
        ctl.pop("budget", None)
    ns = EPHEMERAL_NS
    fork_info: Optional[Dict[str, Any]] = None
    fallback: Optional[str] = None
    ws_from: Optional[Dict[str, Any]] = None
    if mcfg.get("worker_session") == "fork":
        # Its own copy of the planner's workspace + a fork of the planner's
        # conversation staged for that folder (services/run/fork_workspace.py),
        # so forked workers run in parallel instead of queueing on one cwd.
        sid, work_dir = _ephemeral_session(run_id, step_id, None, False, suffix=f"-w{n}")
        pstep = _find_step(run, planner["step_id"]) if planner else None
        pdir = None
        if planner and planner.get("session_id"):
            pns = (pstep or {}).get("session_namespace") or (EPHEMERAL_NS if planner.get("session_policy")
                                                               == "ephemeral" else None)
            pdir = session_store._session_dir(planner["session_id"], namespace=pns)
        elif snap.get("workspace_id"):
            pdir = session_store._session_dir(snap["workspace_id"])
        ws_from = fork_workspace.populate_workspace(
            work_dir, src_dir=pdir, snapshot_key=(pstep or {}).get("snapshot_key"),
            snapshot_after=(pstep or {}).get("snapshot_after"))
        if ws_from["from"] == "none" and snap.get("workspace_id"):
            ws_from = {"from": "copy", "files": fork_workspace.fast_copy(
                session_store._session_dir(snap["workspace_id"]), work_dir)}
        if not planner:
            fallback = "fork needs exactly one step in the previous phase (the planner)"
        elif planner.get("engine") != engine:
            fallback = f"the planner ran on {planner.get('engine')}, the workers on {engine}"
        else:
            fork_info = fork_workspace.prepare_fork(engine, planner.get("engine_session_id"), src_cwd=pdir,
                                                    dst_cwd=work_dir)
            if not fork_info.get("ok"):
                fallback = fork_info.get("reason") or "fork not possible"
        if fallback:
            logger.info(f"run {run_id[:8]} map worker #{n}: cannot fork ({fallback}) — fresh conversation "
                        f"seeded with the planner's handoff")
            ctl.update({"policy": "fresh_handoff", "session": "fresh"})
        else:
            ctl.update({"policy": "fork", "session": "fork", "fork_from": planner["engine_session_id"]})
    else:
        sid, work_dir = _ephemeral_session(run_id, step_id, snap.get("workspace_id"), False, suffix=f"-w{n}")
        ctl.update({"policy": "ephemeral", "session": "fresh"})
    fork = ctl["session"] == "fork"
    key = snapshots.key_for(sid, ns)
    lead = (f'<map_item index="{n}" of="{total}">\n{w["item"]}\n</map_item>\n'
            f"You are worker {n} of {total} of a fan-out step: do this one item only — other workers handle "
            "the rest. Report your handoff for this item.")
    prompt = _build_step_prompt(snap, spec, prev_outputs, True, engine, lead=lead)
    label = f"{step.get('name') or step.get('agent_name') or 'map'} #{n}"
    started = _now_iso()

    def submitted(task_id: str, before: Optional[str]) -> None:
        _update_worker(run_id, step_id, n, {"status": "running", "task_id": task_id, "started_at": started,
                                            "session_id": sid, "session_namespace": ns,
                                            "snapshot_key": key if before else None, "snapshot_before": before,
                                            "fork": bool(fork), "fork_fallback": fallback,
                                            **({"workspace_from": ws_from} if ws_from else {})})

    def unit() -> Dict[str, Any]:
        return _run_unit(run, step, driver=driver, source=source, label=label, sid=sid, ns=ns, work_dir=work_dir,
                         key=key, snap_meta={"run": run_id, "step": step_id, "worker": n}, prompt=prompt, ctl=ctl,
                         engine=engine, model=step.get("model"), is_local=bool(step.get("is_local")),
                         agent_id=step.get("agent_id"), schema=handoff_mod.HANDOFF_SCHEMA,
                         meta={"worker": n, "session_policy": "fork" if fork else "ephemeral",
                               "ephemeral_session": True},
                         on_submitted=submitted)

    try:
        out = unit()
        if fork and out["status"] == "failed" and not driver.cancel_event.is_set() \
                and fork_workspace.missing_session(out.get("error"), out.get("events")):
            fallback = f"the CLI could not find the planner's conversation to fork: {str(out.get('error'))[:200]}"
            logger.warning(f"run {run_id[:8]} map worker #{n}: {fallback} — re-running fresh with the handoff")
            fork = False
            ctl = {**{k: v for k, v in ctl.items() if k != "fork_from"}, "policy": "fresh_handoff",
                   "session": "fresh"}
            usage0 = out["usage"]
            out = unit()
            out["usage"] = budget_mod.usage_add(usage0, out["usage"]) if usage0 else out["usage"]
    finally:
        fork_workspace.cleanup_fork(fork_info)
    ho = handoff_mod.resolve(out["structured"], out["text"], step_status=out["status"], error=out["error"],
                             work_dir=work_dir)
    handoff_mod.collect_artifacts(run_id, step_id, work_dir, ho, out["files"], include_changed=True,
                                  dest_root=handoff_mod.artifacts_dir(run_id, step_id) / f"w{n}")
    text = out["text"] or ("" if ho.get("derived") else ho.get("summary") or "")
    _update_worker(run_id, step_id, n, {
        "status": out["status"], "error": out["error"], "completed_at": _now_iso(), "handoff": ho,
        "result_text": text[:RESULT_TEXT_CAP] if text else None, "engine_session_id": out["esid"],
        "snapshot_after": out["after"], "usage": out["usage"], "files_changed": len(out["files"]),
        "fork": bool(fork), "fork_fallback": fallback},
        usage=out["usage"])
    if out["status"] == "completed":
        try:
            session_store.delete(sid, namespace=EPHEMERAL_NS)
        except Exception as exc:
            logger.warning(f"could not delete map worker session {sid}: {exc}")


def _finish_map(run_id: str, step_id: str, driver: _RunDriver) -> None:
    store = get_run_store()
    step = _find_step(store.get_run(run_id), step_id)
    workers = step.get("workers") or []
    ok = [w for w in workers if w.get("status") == "completed"]
    lines, merged = [], {"decisions": [], "open_questions": [], "next_steps": [], "artifacts": []}
    for w in workers:
        ho = w.get("handoff") or {}
        lines.append(f"#{w['n']} [{w.get('status')}] {w.get('item')}: {(ho.get('summary') or w.get('error') or '')[:2000]}")
        for k in ("decisions", "open_questions", "next_steps"):
            for x in ho.get(k) or []:
                if x not in merged[k]:
                    merged[k].append(x)
        for a in ho.get("artifacts") or []:
            merged["artifacts"].append({**a, "path": f"w{w['n']}/{a.get('path')}", "worker": w["n"]})
    all_ok = len(ok) == len(workers)
    ho = {"status": "done" if all_ok else ("partial" if ok else "failed"),
          "summary": f"map over {len(workers)} item(s): {len(ok)} completed.\n" + "\n".join(lines),
          **merged, "items": [], "verdict": "pass" if all_ok else "fail", "derived": False}
    ho["summary"] = _cap_head_tail(ho["summary"], HANDOFF_CAP)
    if driver.cancel_event.is_set() and not all_ok:
        status, error = "cancelled", "cancelled by user"
    elif all_ok:
        status, error = "completed", None
    elif any(w.get("status") == "budget_exceeded" for w in workers):
        status, error = "budget_exceeded", f"{len(workers) - len(ok)} of {len(workers)} workers did not complete"
    else:
        status, error = "failed", f"{len(workers) - len(ok)} of {len(workers)} workers did not complete"
    store.update_step(run_id, step_id, {"status": status, "error": error, "completed_at": _now_iso(),
                                        "handoff": ho, "result_text": ho["summary"],
                                        "result_preview": ho["summary"][:PREVIEW_CAP]})


# ── loop ───────────────────────────────────────────────────────────────────

def _execute_loop(run_id: str, step_id: str, prev_outputs: List[Dict[str, Any]], driver: _RunDriver, *,
                  mode: str, budget_override: Optional[Dict[str, Any]], steps_left: int, phases_left: int,
                  source: str, extra: str = "") -> None:
    store = get_run_store()
    run = store.get_run(run_id)
    step = _find_step(run, step_id)
    spec = step.get("spec") or {}
    lp = spec.get("loop") or {}
    check = lp.get("check") or {"type": "command", "command": "exit 1"}
    maxi = int(lp.get("max_iterations") or 3)
    iterations = list(step.get("iterations") or []) if mode == "retry" else []
    if mode != "retry":
        store.update_step(run_id, step_id, {"iterations": []})
    feedback: Optional[Dict[str, Any]] = None
    if mode == "retry" and iterations and iterations[-1].get("check"):
        last = iterations[-1]
        feedback = {"findings": last["check"].get("findings") or "", "ctype": check.get("type"),
                    "n": last.get("n"), "max": len(iterations) + maxi}
    first = True
    limit = len(iterations) + maxi
    while len(iterations) < limit:
        if driver.cancel_event.is_set():
            store.update_step(run_id, step_id, {"status": "cancelled", "completed_at": _now_iso(),
                                                "error": "cancelled by user"})
            return
        n = len(iterations) + 1
        att_mode = mode if first and not feedback else "run"
        out = _attempt(run_id, step_id, prev_outputs, driver, mode=att_mode,
                       budget_override=_loop_budget(store.get_run(run_id), step_id, budget_override),
                       steps_left=steps_left, phases_left=phases_left, source=source, extra=extra,
                       feedback=feedback)
        first = False
        it = {"n": n, "task_id": (_find_step(store.get_run(run_id), step_id) or {}).get("task_id"),
              "status": out["status"], "started_at": None, "completed_at": _now_iso(),
              "summary": ((out.get("handoff") or {}).get("summary") or "")[:2000], "check": None}
        if out["status"] != "completed":
            iterations.append(it)
            store.update_step(run_id, step_id, {"iterations": iterations})
            return                                   # the attempt already recorded the failure
        store.update_step(run_id, step_id, {"status": "running", "completed_at": None})
        res = _run_check(run_id, step_id, check, out, driver, source, n)
        it["check"] = res
        iterations.append(it)
        ho = dict(out.get("handoff") or {})
        ho["loop"] = {"iterations": n, "passed": bool(res.get("passed")), "check": check.get("type")}
        if res.get("passed"):
            store.update_step(run_id, step_id, {"iterations": iterations, "status": "completed",
                                                "completed_at": _now_iso(), "handoff": ho, "error": None})
            return
        if driver.cancel_event.is_set():
            store.update_step(run_id, step_id, {"iterations": iterations, "status": "cancelled",
                                                "completed_at": _now_iso(), "error": "cancelled by user"})
            return
        store.update_step(run_id, step_id, {"iterations": iterations, "handoff": ho})
        feedback = {"findings": res.get("findings") or "", "ctype": check.get("type"), "n": n, "max": limit}
    last = iterations[-1]["check"] if iterations and iterations[-1].get("check") else {}
    store.update_step(run_id, step_id, {
        "status": "failed", "completed_at": _now_iso(),
        "error": f"loop: the {check.get('type')} check did not pass after {len(iterations)} iteration(s): "
                 + str((last or {}).get("findings") or "")[:500]})


def _loop_budget(run: Dict[str, Any], step_id: str, override: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A loop's own step budget covers all its iterations: what is left of it."""
    step = _find_step(run, step_id) or {}
    own = budget_mod.merge(override, (step.get("spec") or {}).get("budget"))
    if not budget_mod.is_set(own):
        return override
    u = step.get("usage") or {}
    left: Dict[str, Any] = {}
    if own.get("max_usd") is not None:
        left["max_usd"] = max(0.000001, float(own["max_usd"]) - float(u.get("cost_usd") or 0))
    if own.get("max_tokens") is not None:
        left["max_tokens"] = max(1, int(own["max_tokens"]) - budget_mod.usage_tokens(u))
    if own.get("max_seconds") is not None:
        left["max_seconds"] = own["max_seconds"]
    return left


def _run_check(run_id: str, step_id: str, check: Dict[str, Any], body: Dict[str, Any], driver: _RunDriver,
               source: str, n: int) -> Dict[str, Any]:
    ctype = check.get("type")
    started = _now_iso()
    try:
        if ctype == "command":
            res = _command_check(run_id, step_id, check, Path(body["work_dir"]), driver, n)
        elif ctype == "schema":
            res = _schema_check(check, body)
        else:
            res = _grader_check(run_id, step_id, check, body, driver, source, n)
    except Exception as exc:
        logger.exception(f"loop check crashed ({run_id[:8]}/{step_id[:8]})")
        res = {"passed": False, "findings": f"the check itself failed: {exc}"}
    return {"type": ctype, "started_at": started, "completed_at": _now_iso(), **res}


def _schema_check(check: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    from services.run.jsonschema_lite import validate
    rel = (check.get("path") or "").strip()
    if rel:
        from services.task.safe_paths import resolve_in
        try:
            p = resolve_in(Path(body["work_dir"]), rel)
            instance = json.loads(p.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return {"passed": False, "findings": f"{rel} does not exist in the workspace"}
        except (ValueError, OSError) as exc:
            return {"passed": False, "findings": f"{rel} is not readable JSON: {exc}"}
    else:
        instance = {k: v for k, v in (body.get("handoff") or {}).items()
                    if k not in ("derived", "derive_reason", "notes")}
    problems = validate(instance, check.get("schema") or {})
    return {"passed": not problems, "findings": "\n".join(problems) if problems else "",
            "target": rel or "handoff"}


def _command_check(run_id: str, step_id: str, check: Dict[str, Any], work_dir: Path, driver: _RunDriver,
                   n: int) -> Dict[str, Any]:
    import config
    log_dir = Path(config._settings_dir()) / "data" / "runs" / run_id / "checks"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{step_id}-{n}.log"
    timeout = float(check.get("timeout_seconds") or 300)
    kwargs: Dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    t0 = time.monotonic()
    with open(log_path, "wb") as fh:
        proc = subprocess.Popen(check["command"], shell=True, cwd=str(work_dir), stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, **kwargs)
        try:
            import process as tc_process
            tc_process.bind_to_lifetime_job(proc.pid, proc=proc)
        except Exception:
            pass
        timed_out = cancelled = False
        while proc.poll() is None:
            if driver.cancel_event.is_set():
                cancelled = True
            elif time.monotonic() - t0 > timeout:
                timed_out = True
            if cancelled or timed_out:
                try:
                    import process as tc_process
                    tc_process.kill_process_tree(proc.pid, force=True)
                except Exception:
                    proc.kill()
                break
            time.sleep(0.2)
        try:
            code = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            code = None
    raw = log_path.read_bytes()[-CHECK_OUTPUT_CAP:].decode("utf-8", "replace")
    if cancelled:
        return {"passed": False, "exit_code": code, "findings": "cancelled", "output_path": str(log_path)}
    if timed_out:
        return {"passed": False, "exit_code": code, "output_path": str(log_path),
                "findings": f"the check command timed out after {int(timeout)}s\n{raw}"}
    return {"passed": code == 0, "exit_code": code, "output_path": str(log_path),
            "findings": "" if code == 0 else f"`{check['command']}` exited with {code}:\n{raw}".strip(),
            "output": raw[-2000:]}


def _grader_check(run_id: str, step_id: str, check: Dict[str, Any], body: Dict[str, Any], driver: _RunDriver,
                  source: str, n: int) -> Dict[str, Any]:
    store = get_run_store()
    run = store.get_run(run_id)
    step = _find_step(run, step_id)
    engine = check.get("grader_engine") or step.get("engine") or "claude_code"
    model = check.get("grader_model") or (step.get("model") if engine == step.get("engine") else "")
    # FRESH session in a throwaway copy of the workspace the body just worked in.
    sid = f"run-{run_id[:8]}-{step_id[:8]}-g{n}"
    if session_store.exists(sid, namespace=EPHEMERAL_NS):
        session_store.delete(sid, namespace=EPHEMERAL_NS)
    session_store.create(session_id=sid, namespace=EPHEMERAL_NS,
                         data={"name": f"grader-{run_id[:8]}", "ephemeral": True, "owner_run": run_id},
                         session_idle_timeout_seconds=3600, absolute_ttl_seconds=3600)
    work_dir = session_store._session_dir(sid, namespace=EPHEMERAL_NS)
    _copy_dir(Path(body["work_dir"]), work_dir)
    snap = run.get("job_snapshot") or {}
    task = (step.get("spec") or {}).get("prompt_override") or snap.get("task_description") or ""
    work = handoff_mod.render_block({"name": step.get("name") or "the work", "handoff": body.get("handoff") or {},
                                     "files_changed": body.get("files_changed") or []})
    agy = ("\nEnd your reply with a line `VERDICT: PASS` or `VERDICT: FAIL`." if engine == "antigravity" else "")
    prompt = GRADER_PROMPT.format(rubric=check.get("rubric") or "", task=_cap_head_tail(task, 8000), work=work, agy=agy)
    grader_rec: Dict[str, Any] = {}

    def submitted(task_id: str, before: Optional[str]) -> None:
        grader_rec["task_id"] = task_id

    out = _run_unit(run, step, driver=driver, source=source, label=f"grader #{n}", sid=sid, ns=EPHEMERAL_NS,
                    work_dir=work_dir, key=snapshots.key_for(sid, EPHEMERAL_NS),
                    snap_meta={"run": run_id, "step": step_id, "grader": n}, prompt=prompt,
                    ctl={"policy": "ephemeral", "session": "fresh", "handoff": False}, engine=engine, model=model,
                    is_local=bool(step.get("is_local")), agent_id=check.get("grader_agent_id") or None,
                    schema=GRADER_SCHEMA, meta={"role": "grader", "iteration": n, "ephemeral_session": True},
                    on_submitted=submitted)
    try:
        session_store.delete(sid, namespace=EPHEMERAL_NS)
    except Exception:
        pass
    if out["usage"]:
        def add(r):
            s = _find_step(r, step_id)
            if s is not None:
                s["usage"] = budget_mod.usage_add(s.get("usage"), out["usage"])
        store.mutate(run_id, add)
    if out["status"] != "completed":
        return {"passed": False, "grader_task_id": out["task_id"], "grader_engine": engine,
                "findings": f"the grader did not finish ({out['status']}): {out.get('error') or ''}".strip()}
    verdict, summary, findings = _parse_verdict(out["structured"], out["text"])
    return {"passed": verdict == "pass", "verdict": verdict or "unknown", "summary": summary,
            "findings": "\n".join(f"- {f}" for f in findings) if findings else (summary if verdict != "pass" else ""),
            "grader_task_id": out["task_id"], "grader_engine": engine, "usage": out["usage"]}


def _parse_verdict(structured: Any, text: str) -> Tuple[Optional[str], str, List[str]]:
    obj = structured
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except ValueError:
            obj = None
    if isinstance(obj, dict) and str(obj.get("verdict") or "").lower() in ("pass", "fail"):
        fs = obj.get("findings") or []
        return (str(obj["verdict"]).lower(), str(obj.get("summary") or "")[:4000],
                [str(x)[:2000] for x in (fs if isinstance(fs, list) else [fs])][:50])
    m = _VERDICT_RE.search(text or "")
    return (m.group(1).lower() if m else None, (text or "")[:4000], [] if m and m.group(1).lower() == "pass"
            else [(text or "no verdict given")[:4000]])


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


def _copy_dir(src_dir: Path, dst_dir: Path) -> None:
    """Best-effort copy of every regular file of a session folder (not its
    session.json, .git or .telecode)."""
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


def _copy_workspace_files(src_session_id: str, dst_session_id: str) -> None:
    """Best-effort copy of every regular file from src session to dst (same names)."""
    _copy_dir(session_store._session_dir(src_session_id),
              session_store._session_dir(dst_session_id, namespace=EPHEMERAL_NS))


def run_async(coro):  # pragma: no cover - convenience for sync callers
    return asyncio.run(coro)
