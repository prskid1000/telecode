"""Reconcile HEARTBEAT.md ↔ kind=="heartbeat" Job records.

For a given agent: parse its HEARTBEAT.md, then for each parsed entry
ensure a Job exists with kind="heartbeat", agent_id=agent.id, heartbeat_entry={...}.
Entries removed from YAML mark their Job as archived (history preserved).

Called on:
  - HEARTBEAT.md save in the UI
  - heartbeat scheduler tick (every settings.heartbeat.tick_seconds)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from services.agent.agent_manager import get_agent_manager
from services.job.job_manager import get_job_manager
from services.heartbeat.parser import ScheduleEntry, parse

logger = logging.getLogger("telecode.services.heartbeat.reconcile")


def _entry_dict(entry: ScheduleEntry) -> Dict[str, Any]:
    return entry.to_dict()


def reconcile_agent(agent_id: str) -> Dict[str, Any]:
    """Sync HB jobs for a single agent. Returns {created, updated, archived, errors}."""
    agent_mgr = get_agent_manager()
    job_mgr = get_job_manager()

    if not agent_mgr.get_agent(agent_id):
        return {"created": 0, "updated": 0, "archived": 0, "errors": [{"msg": "agent not found"}]}

    files = agent_mgr.get_internal_files(agent_id)
    text = files.get("HEARTBEAT.md", "") or ""
    parsed = parse(text)

    created = updated = archived = 0
    seen_names = set()

    for entry in parsed.entries:
        seen_names.add(entry.name)
        existing = job_mgr.find_heartbeat_job(agent_id, entry.name)
        new_entry = _entry_dict(entry)

        if existing is None:
            job_mgr.create_job({
                "title": entry.name,
                "agent_id": agent_id,
                "workspace_id": entry.workspace_id,  # null for ephemeral
                "task_description": entry.prompt,
                "kind": "heartbeat",
                "heartbeat_entry": new_entry,
            })
            created += 1
            continue

        # Update mutable fields if drift detected
        prev_entry = existing.get("heartbeat_entry") or {}
        drift = (prev_entry != new_entry
                 or existing.get("task_description") != entry.prompt
                 or existing.get("workspace_id") != entry.workspace_id
                 or existing.get("archived"))
        if drift:
            job_mgr.update_job(existing["id"], {
                "title": entry.name,
                "task_description": entry.prompt,
                "workspace_id": entry.workspace_id,
                "heartbeat_entry": new_entry,
                "archived": False,
            })
            updated += 1

    # Archive jobs whose entry vanished from YAML
    for j in job_mgr.list_jobs(kind="heartbeat", include_archived=False):
        if j.get("agent_id") != agent_id:
            continue
        entry = j.get("heartbeat_entry") or {}
        nm = entry.get("name")
        if nm and nm not in seen_names:
            job_mgr.update_job(j["id"], {"archived": True})
            archived += 1

    return {
        "created": created,
        "updated": updated,
        "archived": archived,
        "errors": parsed.errors,
    }


def reconcile_all() -> List[Dict[str, Any]]:
    """Reconcile every agent. Returns per-agent summary."""
    agent_mgr = get_agent_manager()
    out = []
    for a in agent_mgr.list_agents():
        try:
            summary = reconcile_agent(a["id"])
            out.append({"agent_id": a["id"], "name": a.get("name"), **summary})
        except Exception as exc:
            logger.error(f"Reconcile failed for agent {a.get('id')}: {exc}")
            out.append({"agent_id": a.get("id"), "name": a.get("name"),
                        "errors": [{"msg": str(exc)}]})
    return out
