import json
import os
import uuid
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional

class TeamStore:
    def __init__(self, storage_path: str = "data/team_state.json"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.state = {
            "agents": {}, # agent_id -> {name, task_type, is_local, instructions, avatar}
            "tickets": {}, # ticket_id -> {title, prompt, agent_id, session_id, task_id, status, created_at}
            "history": [], # [{timestamp, session_id, type, message, metadata}]
        }
        self.load()

    def load(self):
        if self.storage_path.exists():
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    self.state.update(json.load(f))
            except Exception:
                pass

    def save(self):
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    # --- Agent (Specialist) Management ---
    def add_agent(self, name: str, task_type: str, instructions: str, is_local: bool = True, avatar: str = "👤", equipment: List[Dict] = None) -> str:
        aid = str(uuid.uuid4())[:8]
        self.state["agents"][aid] = {
            "id": aid,
            "name": name,
            "task_type": task_type,
            "is_local": is_local,
            "instructions": instructions,
            "avatar": avatar,
            "equipment": equipment or [] # [{path, content}]
        }
        self.save()
        return aid

    def delete_agent(self, agent_id: str):
        if agent_id in self.state["agents"]:
            del self.state["agents"][agent_id]
            self.save()

    def get_agents(self) -> List[Dict]:
        return list(self.state["agents"].values())

    # --- Ticket Management ---
    def create_ticket(self, title: str, prompt: str, agent_id: str = None, session_id: str = None, depends_on: str = None) -> str:
        tid = str(uuid.uuid4())[:8]
        self.state["tickets"][tid] = {
            "id": tid,
            "title": title,
            "prompt": prompt,
            "agent_id": agent_id,
            "session_id": session_id,
            "depends_on": depends_on,
            "task_id": None,
            "task_ids": [], # Track all shifts for this ticket
            "status": "blocked" if depends_on else "todo",
            "created_at": datetime.now().isoformat()
        }
        agent_name = self.state["agents"].get(agent_id, {}).get("name", "Unassigned")
        self.add_event(session_id, "ticket_created", f"Created assignment '{title}' (Assigned: {agent_name})", {"ticket_id": tid})
        self.save()
        return tid

    def update_ticket(self, ticket_id: str, **kwargs):
        if ticket_id in self.state["tickets"]:
            self.state["tickets"][ticket_id].update(kwargs)
            self.save()

    def delete_ticket(self, ticket_id: str):
        if ticket_id in self.state["tickets"]:
            t = self.state["tickets"][ticket_id]
            self.add_event(t["session_id"], "ticket_deleted", f"Deleted assignment '{t['title']}'")
            del self.state["tickets"][ticket_id]
            self.save()

    def get_tickets(self) -> List[Dict]:
        return list(self.state["tickets"].values())

    # --- History / Audit Log ---
    def add_event(self, session_id: str, event_type: str, message: str, metadata: Dict = None):
        if not session_id: return
        self.state["history"].append({
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id,
            "type": event_type,
            "message": message,
            "metadata": metadata or {}
        })
        if len(self.state["history"]) > 1000:
            self.state["history"] = self.state["history"][-1000:]
        self.save()

    def get_history(self, session_id: str) -> List[Dict]:
        return [h for h in self.state["history"] if h["session_id"] == session_id]

_team_store = None
def get_team_store() -> TeamStore:
    global _team_store
    if _team_store is None:
        _team_store = TeamStore()
    return _team_store
