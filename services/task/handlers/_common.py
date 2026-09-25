"""Prologue shared by the three task handlers (everything before the CLI runs).

prompt (pre-rendered or agent/job XML) → task + session binding → raw-log path
→ resume id scoped per (workspace, agent, engine[, local]). The handler then
stages the agent's files and calls its ``_run_*_subprocess``, a thin wrapper
over the Engine Runner (``services.engine``).
"""

from __future__ import annotations

from dataclasses import dataclass
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


def prepare(engine: str, *, prompt: Optional[str], is_local: bool, agent_id: Optional[str],
            agent: Optional[Dict[str, Any]], job: Optional[Dict[str, Any]],
            agent_files: Optional[List[Any]], job_files: Optional[List[Any]]) -> TaskContext:
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
    return TaskContext(
        prompt=text, agent_id=agent_id, task_id=get_task_id() or "no-task", sid=sid, ns=ns,
        work_dir=work_dir, log_dir=log_dir,
        resume_id=read_resume_id(meta.get("data"), agent_id, engine, is_local),
        resume_store=make_resume_store(sid, ns, agent_id, engine, is_local),
    )
