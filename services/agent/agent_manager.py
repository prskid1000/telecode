"""Persistent Agent storage for Telecode."""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger("telecode.services.agent")

INTERNAL_FILES = ("SOUL.md", "USER.md", "AGENT.md", "MEMORY.md", "HEARTBEAT.md")

# Per-agent lock — protects writeback to data/agents/<id>/internal/ when
# multiple parallel runs across different workspaces target the same agent.
_agent_locks_guard = threading.Lock()
_agent_locks: Dict[str, threading.Lock] = {}


def _agent_lock(agent_id: str) -> threading.Lock:
    with _agent_locks_guard:
        lock = _agent_locks.get(agent_id)
        if lock is None:
            lock = threading.Lock()
            _agent_locks[agent_id] = lock
        return lock

def get_agents_base_dir() -> Path:
    return Path(config._settings_dir()) / "data" / "agents"

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

class AgentManager:
    def __init__(self):
        self.base_dir = get_agents_base_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_agent_path(self, agent_id: str) -> Path:
        return self.base_dir / f"{agent_id}.json"

    def _get_agent_files_dir(self, agent_id: str) -> Path:
        return self.base_dir / agent_id / "files"

    def _get_agent_internal_dir(self, agent_id: str) -> Path:
        return self.base_dir / agent_id / "internal"

    def list_agents(self) -> List[Dict[str, Any]]:
        agents = []
        for p in self.base_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                agents.append(data)
            except Exception as e:
                logger.error(f"Failed to load agent from {p}: {e}")
        return sorted(agents, key=lambda x: x.get("updated_at", ""), reverse=True)

    def create_agent(self, name: str, instructions: str = "", soul: str = "") -> Dict[str, Any]:
        agent_id = str(uuid.uuid4())
        now = _now_iso()
        agent_data = {
            "id": agent_id,
            "name": name,
            "instructions": instructions,
            "created_at": now,
            "updated_at": now
        }
        self._get_agent_path(agent_id).write_text(json.dumps(agent_data, indent=2), encoding="utf-8")
        self._get_agent_files_dir(agent_id).mkdir(parents=True, exist_ok=True)

        internal_dir = self._get_agent_internal_dir(agent_id)
        internal_dir.mkdir(parents=True, exist_ok=True)
        for fname in INTERNAL_FILES:
            content = soul if fname == "SOUL.md" else ""
            (internal_dir / fname).write_text(content, encoding="utf-8")
        return agent_data

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        p = self._get_agent_path(agent_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def update_agent(self, agent_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        agent = self.get_agent(agent_id)
        if not agent:
            return None
        
        if "name" in data:
            agent["name"] = data["name"]
        if "instructions" in data:
            agent["instructions"] = data["instructions"]
        
        agent["updated_at"] = _now_iso()
        self._get_agent_path(agent_id).write_text(json.dumps(agent, indent=2), encoding="utf-8")
        return agent

    def delete_agent(self, agent_id: str) -> bool:
        p = self._get_agent_path(agent_id)
        if p.exists():
            p.unlink()
            files_dir = self.base_dir / agent_id
            if files_dir.exists():
                shutil.rmtree(files_dir)
            return True
        return False

    def list_files(self, agent_id: str) -> List[Dict[str, Any]]:
        files_dir = self._get_agent_files_dir(agent_id)
        if not files_dir.exists():
            return []
        
        result = []
        for p in files_dir.glob("**/*"):
            if p.is_file():
                rel = p.relative_to(files_dir).as_posix()
                result.append({
                    "path": rel,
                    "bytes": p.stat().st_size,
                    "modified_at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                })
        return result

    def save_file(self, agent_id: str, filename: str, content: bytes):
        files_dir = self._get_agent_files_dir(agent_id)
        files_dir.mkdir(parents=True, exist_ok=True)
        dest = files_dir / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)

    def get_file_path(self, agent_id: str, filename: str) -> Optional[Path]:
        p = self._get_agent_files_dir(agent_id) / filename
        if p.exists() and p.is_file():
            return p
        return None

    def delete_file(self, agent_id: str, filename: str) -> bool:
        p = self._get_agent_files_dir(agent_id) / filename
        if p.exists() and p.is_file():
            p.unlink()
            return True
        return False

    def get_internal_files(self, agent_id: str) -> Dict[str, str]:
        d = self._get_agent_internal_dir(agent_id)
        out: Dict[str, str] = {}
        for fname in INTERNAL_FILES:
            p = d / fname
            out[fname] = p.read_text(encoding="utf-8") if p.exists() else ""
        return out

    def set_internal_files(self, agent_id: str, files: Dict[str, str]) -> bool:
        if not self.get_agent(agent_id):
            return False
        with _agent_lock(agent_id):
            d = self._get_agent_internal_dir(agent_id)
            d.mkdir(parents=True, exist_ok=True)
            for fname, content in (files or {}).items():
                if fname not in INTERNAL_FILES:
                    continue
                (d / fname).write_text(content or "", encoding="utf-8")
        return True

_manager = AgentManager()
def get_agent_manager() -> AgentManager:
    return _manager
