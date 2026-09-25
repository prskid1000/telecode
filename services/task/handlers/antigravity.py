"""ANTIGRAVITY task handler — a thin wrapper over the Engine Runner.

Same signature as CLAUDE_CODE; the agent's AGENT.md is staged as AGENTS.md.
stdin stream-json transport, ``--conversation`` resume, the isolated local-mode
home and the ``gemini-api://local/models/<m>`` model form:
``services/engine/adapters/antigravity.py``. Local conversations live in the
isolated home, so they are stored under a separate resume key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from services.engine.adapters.antigravity import (  # noqa: F401 - re-exported for callers/tests
    build_argv as _build_antigravity_argv,
    ensure_local_home,
    local_env,
    local_home,
    local_model_arg,
    stdin_message as _stdin_message,
)
from services.engine.task_bridge import legacy_resume_writer, run_in_task, task_request
from services.task.handlers._common import prepare
from services.task.staging import stage_for_run

ENGINE = "antigravity"
_RESUME_KEY = "last_antigravity_conversation_id"
_RESUME_KEY_LOCAL = "last_antigravity_local_conversation_id"


def antigravity_task(
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
) -> Dict[str, Any]:
    """Run Antigravity (``agy``) in the session folder. ``model``: an ``agy
    models`` id in cloud mode, the llama model in local mode. ``schema`` is
    accepted for signature parity; agy has no structured-output flag yet."""
    ctx = prepare(ENGINE, prompt=prompt, is_local=is_local, agent_id=agent_id, agent=agent, job=job,
                  agent_files=agent_files, job_files=job_files)
    with stage_for_run(ctx.agent_id, ctx.sid, ctx.work_dir, engine="antigravity"):
        kw: Dict[str, Any] = {}
        if schema:
            kw["schema"] = schema
        return _run_antigravity_subprocess(
            prompt=ctx.prompt, work_dir=ctx.work_dir, sid=ctx.sid, ns=ctx.ns, resume_id=ctx.resume_id,
            log_path=ctx.log_dir / f"{ctx.task_id}.txt", is_local=is_local, model=model,
            resume_store=ctx.resume_store, agent_id=ctx.agent_id, **kw)


def _run_antigravity_subprocess(
    *,
    prompt: str,
    work_dir: Path,
    sid: str,
    ns: Optional[str],
    resume_id: Optional[str],
    log_path: Path,
    is_local: bool = False,
    model: Optional[str] = None,
    resume_store: Optional[Callable[[str], None]] = None,
    schema: Optional[Dict[str, Any]] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run ``agy`` for the current task. Without ``resume_store`` the legacy
    flat key (cloud or local) is written."""
    key = _RESUME_KEY_LOCAL if is_local else _RESUME_KEY
    req = task_request(
        ENGINE, prompt=prompt, cwd=work_dir, sid=sid, model=model, is_local=is_local,
        resume_id=resume_id, on_resume_id=resume_store or legacy_resume_writer(sid, ns, key),
        log_path=log_path, schema=schema)
    return run_in_task(req, sid=sid, ns=ns, agent_id=agent_id)
