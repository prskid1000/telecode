"""Persistent Job storage for Telecode."""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger("telecode.services.job")

def get_jobs_base_dir() -> Path:
    return Path(config._settings_dir()) / "data" / "jobs"

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

class JobManager:
    def __init__(self):
        self.base_dir = get_jobs_base_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_job_path(self, job_id: str) -> Path:
        return self.base_dir / f"{job_id}.json"

    def _get_job_files_dir(self, job_id: str) -> Path:
        return self.base_dir / job_id / "files"

    def list_jobs(self) -> List[Dict[str, Any]]:
        jobs = []
        for p in self.base_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                jobs.append(data)
            except Exception as e:
                logger.error(f"Failed to load job from {p}: {e}")
        return sorted(jobs, key=lambda x: x.get("updated_at", ""), reverse=True)

    def create_job(self, data: Dict[str, Any]) -> Dict[str, Any]:
        job_id = str(uuid.uuid4())
        now = _now_iso()
        job_data = {
            "id": job_id,
            "title": data.get("title", "Untitled Job"),
            "agent_id": data.get("agent_id"),
            "workspace_id": data.get("workspace_id"),
            "actions": data.get("actions", []),
            "tasks": data.get("tasks", []),
            "created_at": now,
            "updated_at": now
        }
        self._get_job_path(job_id).write_text(json.dumps(job_data, indent=2), encoding="utf-8")
        self._get_job_files_dir(job_id).mkdir(parents=True, exist_ok=True)
        return job_data

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        p = self._get_job_path(job_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def update_job(self, job_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        job = self.get_job(job_id)
        if not job:
            return None
        
        for key in ["title", "actions", "tasks"]:
            if key in data:
                job[key] = data[key]
        
        job["updated_at"] = _now_iso()
        self._get_job_path(job_id).write_text(json.dumps(job, indent=2), encoding="utf-8")
        return job

    def delete_job(self, job_id: str) -> bool:
        p = self._get_job_path(job_id)
        if p.exists():
            p.unlink()
            files_dir = self.base_dir / job_id
            if files_dir.exists():
                shutil.rmtree(files_dir)
            return True
        return False

    def list_files(self, job_id: str) -> List[Dict[str, Any]]:
        files_dir = self._get_job_files_dir(job_id)
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

    def save_file(self, job_id: str, filename: str, content: bytes):
        files_dir = self._get_job_files_dir(job_id)
        files_dir.mkdir(parents=True, exist_ok=True)
        dest = files_dir / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)

    def get_file_path(self, job_id: str, filename: str) -> Optional[Path]:
        p = self._get_job_files_dir(job_id) / filename
        if p.exists() and p.is_file():
            return p
        return None

    def delete_file(self, job_id: str, filename: str) -> bool:
        p = self._get_job_files_dir(job_id) / filename
        if p.exists() and p.is_file():
            p.unlink()
            return True
        return False

_manager = JobManager()
def get_job_manager() -> JobManager:
    return _manager
