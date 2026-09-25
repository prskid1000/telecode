"""CODEX task handler — a thin wrapper over the Engine Runner.

Same signature as CLAUDE_CODE; the agent's AGENT.md is staged as AGENTS.md.
argv (``exec``-only flags before ``resume``, prompt ``-`` on stdin, local-mode
``-c`` provider) and the JSONL event mapping: ``services/engine/adapters/codex.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from services.engine.adapters.codex import (  # noqa: F401 - re-exported for callers/tests
    LOCAL_PROVIDER_ID,
    add_usage as _add_usage,
    build_argv as _build_codex_argv,
    local_env as _local_env,
    local_provider_overrides as _local_provider_overrides,
    normalize_usage as _normalize_usage,
)
from services.engine.task_bridge import legacy_resume_writer, run_in_task, task_request
from services.task.handlers._common import prepare, run as run_step
from services.task.staging import stage_for_run

ENGINE = "codex"


def codex_task(
    prompt: Optional[str] = None,
    is_local: bool = False,
    *,
    agent_id: Optional[str] = None,
    agent: Optional[Dict[str, Any]] = None,
    job: Optional[Dict[str, Any]] = None,
    agent_files: Optional[List[Any]] = None,
    job_files: Optional[List[Any]] = None,
    model: Optional[str] = None,
    schema: Optional[Dict[str, Any]] = None,
    step_ctl: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run Codex (``codex exec --json``) in the session folder. ``schema`` →
    ``--output-schema`` (the reply is parsed into ``structured_output``)."""
    ctx = prepare(ENGINE, prompt=prompt, is_local=is_local, agent_id=agent_id, agent=agent, job=job,
                  agent_files=agent_files, job_files=job_files, step_ctl=step_ctl)
    with stage_for_run(ctx.agent_id, ctx.sid, ctx.work_dir, engine="codex"):
        def run_fn(*, prompt, resume_id, fork, schema, budget, add_dirs, lineage, log_suffix=""):
            return _run_codex_subprocess(
                prompt=prompt, work_dir=ctx.work_dir, sid=ctx.sid, ns=ctx.ns, resume_id=resume_id,
                is_local=is_local, log_path=ctx.log_dir / f"{ctx.task_id}{log_suffix}.jsonl",
                last_msg_path=ctx.log_dir / f"{ctx.task_id}{log_suffix}.codex_last_message.txt", model=model,
                resume_store=ctx.resume_store, agent_id=ctx.agent_id, schema=schema, fork=fork,
                budget=budget, add_dirs=add_dirs, lineage=lineage)
        return run_step(ctx, run_fn, schema=schema)


def _run_codex_subprocess(
    *,
    prompt: str,
    work_dir: Path,
    sid: str,
    ns: Optional[str],
    resume_id: Optional[str],
    is_local: bool,
    log_path: Path,
    last_msg_path: Path,
    model: Optional[str] = None,
    resume_store: Optional[Callable[[str], None]] = None,
    schema: Optional[Dict[str, Any]] = None,
    agent_id: Optional[str] = None,
    fork: bool = False,
    budget: Optional[Dict[str, Any]] = None,
    add_dirs=(),
    lineage: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run ``codex exec`` for the current task. Without ``resume_store`` the
    legacy flat ``last_codex_session_id`` key is written."""
    req = task_request(
        ENGINE, prompt=prompt, cwd=work_dir, sid=sid, model=model, is_local=is_local,
        resume_id=resume_id,
        on_resume_id=resume_store or legacy_resume_writer(sid, ns, "last_codex_session_id"),
        log_path=log_path, last_msg_path=last_msg_path, schema=schema, fork=fork, budget=budget,
        add_dirs=add_dirs)
    return run_in_task(req, sid=sid, ns=ns, agent_id=agent_id, lineage=lineage)
