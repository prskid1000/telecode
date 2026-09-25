"""CLAUDE_CODE task handler — a thin wrapper over the Engine Runner.

``claude_code_task`` resolves the prompt/session/resume scope, stages the
agent's files (AGENT.md goes to ``--append-system-prompt-file``, never over the
workspace's own CLAUDE.md) and calls :func:`_run_claude_subprocess`, which
builds an :class:`services.engine.EngineRequest` for the current task and runs
it. argv / event parsing: ``services/engine/adapters/claude.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from services.engine.task_bridge import legacy_resume_writer, run_in_task, task_request
from services.task.handlers._common import prepare
from services.task.staging import stage_for_run

ENGINE = "claude_code"


def claude_code_task(
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
    """Run Claude Code in the session folder assigned by the queue.

    ``model``: a Claude alias/full name in cloud mode, the llama model in local
    mode. ``schema``: JSON Schema for structured output (``--json-schema``);
    the parsed object comes back as ``structured_output``.
    """
    ctx = prepare(ENGINE, prompt=prompt, is_local=is_local, agent_id=agent_id, agent=agent, job=job,
                  agent_files=agent_files, job_files=job_files)
    import config as app_config
    agent_md_file = None
    if ctx.agent_id:
        agent_md_file = (Path(app_config._settings_dir()) / "data" / "runtime"
                         / "agent_prompts" / f"{ctx.task_id}.md")
    with stage_for_run(ctx.agent_id, ctx.sid, ctx.work_dir, engine="claude", agent_md_file=agent_md_file):
        append_file = agent_md_file if (agent_md_file and agent_md_file.exists()
                                        and agent_md_file.stat().st_size > 0) else None
        kw: Dict[str, Any] = {}
        if schema:
            kw["schema"] = schema
        return _run_claude_subprocess(
            prompt=ctx.prompt, work_dir=ctx.work_dir, sid=ctx.sid, ns=ctx.ns, resume_id=ctx.resume_id,
            is_local=is_local, log_path=ctx.log_dir / f"{ctx.task_id}.jsonl", model=model,
            resume_store=ctx.resume_store, append_system_prompt_file=append_file,
            agent_id=ctx.agent_id, **kw)


def _run_claude_subprocess(
    *,
    prompt: str,
    work_dir: Path,
    sid: str,
    ns: Optional[str],
    resume_id: Optional[str],
    is_local: bool,
    log_path: Path,
    model: Optional[str] = None,
    resume_store: Optional[Callable[[str], None]] = None,
    append_system_prompt_file: Optional[Path] = None,
    schema: Optional[Dict[str, Any]] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run ``claude -p`` for the current task. Without ``resume_store`` the
    legacy flat ``last_claude_session_id`` key is written."""
    req = task_request(
        ENGINE, prompt=prompt, cwd=work_dir, sid=sid, model=model, is_local=is_local,
        resume_id=resume_id,
        on_resume_id=resume_store or legacy_resume_writer(sid, ns, "last_claude_session_id"),
        log_path=log_path, system_append_file=append_system_prompt_file, schema=schema)
    return run_in_task(req, sid=sid, ns=ns, agent_id=agent_id)
