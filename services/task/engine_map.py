"""Single source of truth for engine -> task_type mapping.

Used by services.run.executor, services.heartbeat.scheduler, and
services.routine.routine_manager so adding a new engine never drifts across
call sites.

Engine string is the lowercase form stored on agent / pipeline step / HB entry
(e.g. "claude_code", "codex", "antigravity"); task_type is the upper-case key
registered with the task queue.
"""

from __future__ import annotations

from typing import Dict

ENGINE_TO_TASK_TYPE: Dict[str, str] = {
    "claude_code": "CLAUDE_CODE",
    "codex":       "CODEX",
    "antigravity": "ANTIGRAVITY",
}

DEFAULT_TASK_TYPE = "CLAUDE_CODE"


def engine_to_task_type(engine: str) -> str:
    """Map an engine string to its task_type, falling back to CLAUDE_CODE."""
    return ENGINE_TO_TASK_TYPE.get((engine or "").strip().lower(), DEFAULT_TASK_TYPE)


def supported_engines() -> list:
    """List of engine keys we know how to dispatch."""
    return list(ENGINE_TO_TASK_TYPE.keys())
