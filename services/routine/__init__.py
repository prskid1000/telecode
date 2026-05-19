"""Recurring task jobs bound to a permanent task-mode session.

Heartbeat-driven: a single worker thread (the manager) ticks once per
``MANAGER_INTERVAL_SECONDS`` and walks every routine on disk. For
routines whose ``next_fire_at`` has been reached, the manager submits a
CLAUDE_CODE (or other configured) task against the routine's permanent
session via ``services.task.task_manager``.

Pause / resume / edit / delete are pure file ops on the routine record
-- no per-routine scheduler state. The manager picks up changes on its
next heartbeat.

Public surface: :mod:`services.routine.routine_service`.
"""

from services.routine import routine_manager, routine_service, routine_store

__all__ = ["routine_manager", "routine_service", "routine_store"]
