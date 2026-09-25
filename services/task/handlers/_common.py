"""Prologue + run step shared by the three task handlers.

prompt (pre-rendered or agent/job XML) → task + session binding → raw-log path
→ which CLI conversation to use (resume scope per (workspace, agent,
engine[, local]), or the step's session policy) → rotation check. The handler
stages the agent's files and calls :func:`run`, which drives its
``_run_*_subprocess`` (a thin wrapper over the Engine Runner).

``step_ctl`` (from the run executor; absent for Task-mode submits = resume)::

    {"session": "resume" | "fork" | "fresh",   # how to pick the conversation
     "resume_id": "<cli id>",                  # explicit (a step retry)
     "fork_from": "<cli id>",                  # fork source (default: the scope's)
     "policy": "<pipeline policy name>",       # recorded in sessions_index
     "budget": {max_usd, max_tokens, max_seconds},
     "add_dirs": ["<dir>", …],                 # e.g. previous steps' artifacts
     "handoff": true,                          # agy: read .telecode/handoff.json
     "rotate": true,                           # force a rotation
     "pinned": "<constraints kept across a rotation>"}

Rotation: when the conversation about to be resumed has passed
``tasks.rotate_after_tokens`` budget tokens, or (routines) its row has run
``tasks.rotate_after_fires`` times, or ``step_ctl.rotate`` — the old
conversation is asked for a handoff (structured, like a pipeline step) and the
task continues in a **fresh** conversation whose prompt starts with that
handoff plus the pinned constraints (the agent's ``## Pinned constraints``
section of AGENT.md + ``step_ctl.pinned``). The new ``sessions_index`` row
records ``lineage=rotation`` and ``rotated_from``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from services.session import session_store
from services.task.agent_prompt import resolve_prompt
from services.task.task_utils import (
    get_session_folder,
    get_session_id,
    get_session_namespace,
    get_task_id,
    make_resume_store,
    read_resume_id,
)

logger = logging.getLogger("telecode.services.task.handlers")


@dataclass
class TaskContext:
    prompt: str
    agent_id: Optional[str]
    task_id: str
    sid: str
    ns: Optional[str]
    work_dir: Path
    log_dir: Path
    resume_id: Optional[str]
    resume_store: Callable[[str], None]
    engine: str = ""
    fork: bool = False
    ctl: Dict[str, Any] = field(default_factory=dict)
    lineage: Dict[str, Any] = field(default_factory=dict)
    rotate_row: Optional[Dict[str, Any]] = None
    rotate_reason: str = ""


def _current_task_metadata() -> Dict[str, Any]:
    try:
        from services.task.task_manager import get_task_queue
        tid = get_task_id()
        t = get_task_queue().get_task(tid) if tid else None
        return (t.metadata if t else {}) or {}
    except Exception:
        return {}


def _rotation_check(ctx: TaskContext) -> None:
    """Decide whether the conversation about to be resumed must rotate."""
    if not ctx.resume_id or ctx.fork:
        return
    import config as app_config
    row = None
    try:
        from services.db import sessions_repo
        row = sessions_repo.find_by_engine_session(ctx.resume_id)
    except Exception:
        logger.exception("rotation check: lineage lookup failed")
    if ctx.ctl.get("rotate"):
        ctx.rotate_row, ctx.rotate_reason = row or {"id": None}, "requested"
        return
    if not row:
        return
    max_tok = app_config.tasks_rotate_after_tokens()
    if max_tok and int(row.get("cumulative_tokens") or 0) >= max_tok:
        ctx.rotate_row = row
        ctx.rotate_reason = f"{int(row.get('cumulative_tokens') or 0)} tokens >= rotate_after_tokens {max_tok}"
        return
    max_fires = app_config.tasks_rotate_after_fires()
    if max_fires and _current_task_metadata().get("routine_id") and int(row.get("runs_count") or 0) >= max_fires:
        ctx.rotate_row = row
        ctx.rotate_reason = f"{int(row.get('runs_count') or 0)} fires >= rotate_after_fires {max_fires}"


def prepare(engine: str, *, prompt: Optional[str], is_local: bool, agent_id: Optional[str],
            agent: Optional[Dict[str, Any]], job: Optional[Dict[str, Any]],
            agent_files: Optional[List[Any]], job_files: Optional[List[Any]],
            step_ctl: Optional[Dict[str, Any]] = None) -> TaskContext:
    text = resolve_prompt({"prompt": prompt, "agent": agent, "job": job,
                           "agent_files": agent_files, "job_files": job_files})
    if not agent_id and isinstance(agent, dict):
        agent_id = agent.get("id")
    import config as app_config
    log_dir = Path(app_config._settings_dir()) / "data" / "task_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    sid = get_session_id()
    ns = get_session_namespace()
    work_dir = get_session_folder()
    if not sid or not work_dir:
        raise RuntimeError("No session bound to this task")
    meta = session_store.get(sid, namespace=ns) or {}
    ctl = dict(step_ctl or {})
    scoped = read_resume_id(meta.get("data"), agent_id, engine, is_local)
    session = (ctl.get("session") or "resume").lower()
    policy = ctl.get("policy") or session
    resume_id: Optional[str] = scoped
    fork = False
    lineage: Dict[str, Any] = {"policy": policy}
    if ctl.get("resume_id"):
        resume_id = ctl["resume_id"]
    elif session == "fresh":
        resume_id = None
        lineage["lineage"] = "ephemeral" if policy == "ephemeral" else "fresh"
    elif session == "fork":
        src = ctl.get("fork_from") or scoped
        if src and engine != "antigravity":
            resume_id, fork = src, True
            lineage.update({"lineage": "fork", "forked_from_session": src})
        else:
            resume_id = None   # agy has no fork (or nothing to fork): fresh, seeded by the handoff
            lineage["lineage"] = "fresh"
    ctx = TaskContext(
        prompt=text, agent_id=agent_id, task_id=get_task_id() or "no-task", sid=sid, ns=ns,
        work_dir=work_dir, log_dir=log_dir, resume_id=resume_id,
        resume_store=make_resume_store(sid, ns, agent_id, engine, is_local),
        engine=engine, fork=fork, ctl=ctl, lineage=lineage,
    )
    _rotation_check(ctx)
    return ctx


# ── Rotation ────────────────────────────────────────────────────────────────

_PINNED_RE = re.compile(r"^#{1,6}\s*pinned constraints\s*$", re.I | re.M)


def pinned_constraints(agent_id: Optional[str]) -> str:
    """The ``## Pinned constraints`` section of the agent's AGENT.md (to the next heading)."""
    if not agent_id:
        return ""
    try:
        from services.agent.agent_manager import get_agent_manager
        text = get_agent_manager().get_internal_files(agent_id).get("AGENT.md", "") or ""
    except Exception:
        return ""
    m = _PINNED_RE.search(text)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^#{1,6}\s", rest, re.M)
    return (rest[: nxt.start()] if nxt else rest).strip()


ROTATION_ASK = (
    "This conversation is being rotated into a fresh session to keep its context small. Do not start new "
    "work. Report a handoff of the state of the work so far for the fresh session that continues it: what is "
    "done, what is in progress, the decisions and constraints that must be kept, the files that matter, open "
    "questions and next steps.")


def rotation_preamble(ho: Dict[str, Any], reason: str, pinned: str) -> str:
    from services.run.handoff import render_block
    parts = ["<rotated_session>",
             f"This task continues work from an earlier conversation that was rotated out ({reason}). "
             "Its handoff follows; treat it as your memory of that conversation.",
             render_block({"name": "previous conversation", "handoff": ho})]
    if pinned:
        parts += ["<pinned_constraints>", pinned, "</pinned_constraints>"]
    parts.append("</rotated_session>")
    return "\n".join(parts)


def _ask_rotation_handoff(ctx: TaskContext, run_fn: Callable[..., Dict[str, Any]]) -> Dict[str, Any]:
    from services.engine.types import EngineCancelled
    from services.run import handoff as ho_mod
    from services.task import task_utils
    agy = ctx.engine == "antigravity"
    task_utils.append_event({"kind": "rotation", "stage": "ask", "reason": ctx.rotate_reason,
                             "rotated_session": ctx.resume_id})
    if agy:
        ho_mod.clear_agy_file(ctx.work_dir)
    try:
        res = run_fn(prompt=ROTATION_ASK + "\n\n" + ho_mod.instructions(ctx.engine), resume_id=ctx.resume_id,
                     fork=False, schema=None if agy else ho_mod.HANDOFF_SCHEMA, budget=None,
                     add_dirs=ctx.ctl.get("add_dirs") or (), lineage=None, log_suffix=".rotate")
    except EngineCancelled:
        raise
    except Exception as exc:
        logger.warning(f"rotation handoff failed ({exc}) — continuing fresh without one")
        return ho_mod.derive("", step_status="failed", error=f"rotation handoff failed: {exc}",
                             reason="rotation handoff failed")
    structured = ho_mod.read_agy_file(ctx.work_dir) if agy else res.get("structured_output")
    return ho_mod.resolve(structured, res.get("result") or "", step_status="completed", error=None,
                          work_dir=ctx.work_dir)


def run(ctx: TaskContext, run_fn: Callable[..., Dict[str, Any]], *,
        schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Rotation (if due) → the real run → agy handoff file. ``run_fn`` is the
    handler's closure over its ``_run_*_subprocess``:
    ``run_fn(prompt, resume_id, fork, schema, budget, add_dirs, lineage, log_suffix)``."""
    from services.run import handoff as ho_mod
    from services.task import task_utils
    prompt, resume_id, fork, lineage = ctx.prompt, ctx.resume_id, ctx.fork, dict(ctx.lineage)
    rotation: Optional[Dict[str, Any]] = None
    if ctx.rotate_row is not None:
        ho = _ask_rotation_handoff(ctx, run_fn)
        pinned = "\n".join(x for x in (pinned_constraints(ctx.agent_id), ctx.ctl.get("pinned") or "") if x).strip()
        prompt = rotation_preamble(ho, ctx.rotate_reason, pinned) + "\n\n" + prompt
        rotation = {"from_session": ctx.resume_id, "from_row": ctx.rotate_row.get("id"),
                    "reason": ctx.rotate_reason, "handoff": ho}
        lineage = {"lineage": "rotation", "rotated_from": ctx.rotate_row.get("id"),
                   "policy": lineage.get("policy")}
        resume_id, fork = None, False
        task_utils.append_event({"kind": "rotation", "stage": "continue", "reason": ctx.rotate_reason,
                                 "rotated_session": rotation["from_session"]})
    agy_file = ctx.engine == "antigravity" and bool(ctx.ctl.get("handoff"))
    if agy_file:
        ho_mod.clear_agy_file(ctx.work_dir)
    res = run_fn(prompt=prompt, resume_id=resume_id, fork=fork, schema=schema,
                 budget=ctx.ctl.get("budget") or None, add_dirs=ctx.ctl.get("add_dirs") or (),
                 lineage=lineage, log_suffix="")
    if agy_file:
        res["structured_output"] = ho_mod.read_agy_file(ctx.work_dir)
    if rotation:
        res["rotation"] = rotation
    return res
