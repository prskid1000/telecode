"""The Engine Runner — one way to run Claude Code / Codex / Antigravity.

``run_engine(EngineRequest) -> EngineResult`` (``runner.py``) is used by the
three task handlers and by TeleDesign's turn handler; ``task_bridge.py`` binds
a request to the current task-queue task (events, progress, cancel, pids,
resume ids, session lineage). Engine specifics are thin adapters under
``adapters/``; spawning and tree-kill live in ``spawn.py``.
"""

from services.engine.types import (  # noqa: F401
    ENGINES, EVENT_KINDS, EngineBudgetExceeded, EngineCancelled, EngineError, EngineRequest, EngineResult,
    EngineTimeout,
)


def run_engine(req):  # noqa: D401 - thin re-export, imported lazily
    from services.engine.runner import run_engine as _run
    return _run(req)
