"""Agent memory (P4): git-versioned ``internal/``, index + typed topic files,
pinned constraints, per-engine memory wiring, reflection with approval.

  repo        git plumbing for ``internal/.git`` (commit / per-run branch merge / history / revert)
  store       layout, index + topic files, the one-time MEMORY.md split, pinned constraints
  merge       merge3 — the fallback for files git cannot merge
  reflection  the reflection trigger, its input digest, proposals → ``memory`` approvals

Public entry points used outside this package:

* :func:`engine_extras` — what the Engine Runner adds to a CLI launch so the
  engine sees the agent's one memory (the runner calls it; see its docstring).
* :func:`pinned_constraints` — the ``## Pinned constraints`` section of AGENT.md
  (triggers append it at the tail of every fire; rotation carries it over).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("telecode.services.memory")

CLAUDE_SETTINGS_FILE = "claude-memory-settings.json"
_EMPTY = {"args": [], "env": {}, "add_dirs": []}


def _engine_key(engine: str) -> str:
    e = (engine or "").strip().lower()
    return {"claude_code": "claude", "claude": "claude", "codex": "codex",
            "antigravity": "antigravity", "agy": "antigravity"}.get(e, e)


def engine_extras(agent_id: Optional[str], engine: str, workspace: Optional[Path] = None) -> Dict[str, Any]:
    """Launch additions that point an engine at the agent's memory.

    Returns ``{"args": [str], "env": {str: str}, "add_dirs": [str]}`` — extra
    argv (append after the adapter's own flags, before the prompt marker), extra
    environment, and directories to pass as ``--add-dir`` (all three engines
    accept ``--add-dir``; Codex since 0.157 on ``codex exec``).

    * no agent / unknown agent → all empty.
    * ``claude_code``: ``args = ["--settings", "<data/agents/<id>/claude-memory-settings.json>"]``,
      a file holding ``{"autoMemoryDirectory": "<internal/memory>"}`` — Claude
      Code's auto memory then reads (index at session start) and writes that
      directory instead of ``~/.claude/projects/<cwd>/memory`` (flag settings are
      honoured for this key; project settings are not), so there is one memory
      per agent, not one per folder; ``add_dirs = ["<internal/memory>"]``; plus
      ``settings = {"autoMemoryDirectory": …}`` — the file's content, for a runner
      that already passes its own ``--settings`` and must merge into one (verified
      with claude 2.1.282: ``--settings <file>`` makes auto memory load that index).
    * ``codex`` / ``antigravity``: ``add_dirs = ["<internal/memory>"]`` (the staged
      MEMORY.md index names that directory in its first line).
    * a memory-reflection fire (:mod:`.reflection`): Claude gets
      ``env = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}`` and nothing else, the
      others nothing — the reflection reads a digest and proposes a diff for
      approval; it must not write memory directly.

    ``workspace`` is accepted for future per-workspace rules and currently unused.
    """
    if not agent_id:
        return {k: (list(v) if isinstance(v, list) else dict(v)) for k, v in _EMPTY.items()}
    from services.agent.agent_manager import get_agent_manager
    try:
        if not get_agent_manager().get_agent(agent_id):
            return {"args": [], "env": {}, "add_dirs": []}
    except ValueError:
        return {"args": [], "env": {}, "add_dirs": []}
    from services.memory import reflection, store
    eng = _engine_key(engine)
    if reflection.current_task_is_reflection(agent_id):
        return {"args": [], "env": {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"} if eng == "claude" else {},
                "add_dirs": []}
    mdir = store.ensure(agent_id) / store.MEMORY_DIR
    mdir.mkdir(parents=True, exist_ok=True)
    if eng == "claude":
        sp = store.agent_state_dir(agent_id) / CLAUDE_SETTINGS_FILE
        want = json.dumps({"autoMemoryDirectory": str(mdir)}, indent=2)
        try:
            if not sp.exists() or sp.read_text(encoding="utf-8") != want:
                sp.write_text(want, encoding="utf-8")
        except OSError:
            logger.exception(f"could not write {sp}")
            return {"args": [], "env": {}, "add_dirs": [str(mdir)]}
        return {"args": ["--settings", str(sp)], "env": {}, "add_dirs": [str(mdir)],
                "settings": {"autoMemoryDirectory": str(mdir)}}
    if eng in ("codex", "antigravity"):
        return {"args": [], "env": {}, "add_dirs": [str(mdir)]}
    return {"args": [], "env": {}, "add_dirs": []}


def pinned_constraints(agent_id: Optional[str]) -> str:
    """The agent's pinned constraints: the ``## Pinned constraints`` section of
    its AGENT.md (to the next heading), stripped; "" when none."""
    from services.memory import reflection
    return reflection.pinned_constraints(agent_id)


def set_pinned_constraints(agent_id: str, text: str) -> str:
    """Replace (or add / with empty text remove) the section; commits. Returns the new AGENT.md."""
    from services.agent.agent_manager import get_agent_manager
    from services.memory import repo, store
    mgr = get_agent_manager()
    cur = mgr.get_internal_files(agent_id).get("AGENT.md", "") or ""
    new = store.replace_pinned(cur, text)
    if new != cur:
        mgr.set_internal_files(agent_id, {"AGENT.md": new},
                               message=repo.message("ui: edit pinned constraints", kind="ui"))
    return new
