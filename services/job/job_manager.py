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


VALID_PIPELINE_MODES = ("single", "sequential", "parallel", "custom")


def _normalize_pipeline(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise / validate a pipeline dict in-place; return it.

    Each step ends up with a `phase: int`. Phases run sequentially; steps
    in the same phase run in parallel.
      - single     → 1 step, phase 0
      - sequential → step i has phase i
      - parallel   → all steps share phase 0
      - custom     → respects per-step phase; fills missing values from index;
                     renumbers to contiguous 0..N-1 preserving relative order.
    """
    pipe = dict(data or {})
    mode = pipe.get("mode", "single")
    if mode not in VALID_PIPELINE_MODES:
        mode = "single"
    pipe["mode"] = mode

    raw_steps = pipe.get("steps") or []
    out_steps: List[Dict[str, Any]] = []
    for s in raw_steps:
        if not isinstance(s, dict):
            continue
        step = {
            "step_id": s.get("step_id") or str(uuid.uuid4()),
            "agent_id": s.get("agent_id"),
            "name": s.get("name") or "",
            "prompt_override": s.get("prompt_override") or "",
            "depends_on_text": bool(s.get("depends_on_text", False)),
            "phase": s.get("phase"),
        }
        if not step["agent_id"]:
            continue  # drop malformed steps
        out_steps.append(step)

    if mode == "single":
        out_steps = out_steps[:1]
        for s in out_steps:
            s["phase"] = 0
    elif mode == "sequential":
        for i, s in enumerate(out_steps):
            s["phase"] = i
    elif mode == "parallel":
        for s in out_steps:
            s["phase"] = 0
    elif mode == "custom":
        # Fill missing phase values (fall back to the step's index so adding
        # a step without setting phase puts it on its own phase by default),
        # then renumber to contiguous 0..N-1 preserving relative order.
        for i, s in enumerate(out_steps):
            if s["phase"] is None:
                s["phase"] = i
            else:
                try:
                    s["phase"] = int(s["phase"])
                except (TypeError, ValueError):
                    s["phase"] = i
        unique_phases = sorted({s["phase"] for s in out_steps})
        renumber = {p: i for i, p in enumerate(unique_phases)}
        for s in out_steps:
            s["phase"] = renumber[s["phase"]]

    pipe["steps"] = out_steps
    return pipe



class JobManager:
    def __init__(self):
        self.base_dir = get_jobs_base_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_job_path(self, job_id: str) -> Path:
        return self.base_dir / f"{job_id}.json"

    def _get_job_files_dir(self, job_id: str) -> Path:
        return self.base_dir / job_id / "files"

    def list_jobs(self, kind: Optional[str] = None, include_archived: bool = False) -> List[Dict[str, Any]]:
        jobs = []
        for p in self.base_dir.glob("*.json"):
            try:
                jobs.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception as e:
                logger.error(f"Failed to load job from {p}: {e}")
        if kind:
            jobs = [j for j in jobs if j.get("kind") == kind]
        if not include_archived:
            jobs = [j for j in jobs if not j.get("archived")]
        return sorted(jobs, key=lambda x: x.get("updated_at", ""), reverse=True)

    def create_job(self, data: Dict[str, Any]) -> Dict[str, Any]:
        job_id = str(uuid.uuid4())
        now = _now_iso()
        kind = data.get("kind", "user")
        if kind not in ("user", "heartbeat"):
            kind = "user"

        # Build pipeline from the explicit `pipeline` dict, or from a single
        # `agent_id` (HB jobs and the simple create-job modal use this shape).
        if "pipeline" in data:
            pipeline = _normalize_pipeline(data["pipeline"])
        elif data.get("agent_id"):
            pipeline = _normalize_pipeline({
                "mode": "single",
                "steps": [{"agent_id": data["agent_id"]}],
            })
        else:
            pipeline = {"mode": "single", "steps": []}

        job_data = {
            "id": job_id,
            "title": data.get("title", "Untitled Job"),
            "agent_id": data.get("agent_id"),  # kept for legacy reads
            "workspace_id": data.get("workspace_id"),
            "actions": data.get("actions", []),
            "tasks": data.get("tasks", []),
            "task_description": data.get("task_description", ""),
            "pipeline": pipeline,
            "kind": kind,
            "heartbeat_entry": data.get("heartbeat_entry"),  # dict or None; only for kind=="heartbeat"
            "archived": bool(data.get("archived", False)),
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

        # Mutable fields. heartbeat_entry/archived are mainly written by the
        # reconciliation pass for kind=="heartbeat" jobs but accepted via API too.
        for key in ["title", "actions", "tasks", "task_description",
                    "agent_id", "workspace_id",
                    "heartbeat_entry", "archived"]:
            if key in data:
                job[key] = data[key]
        if "pipeline" in data:
            job["pipeline"] = _normalize_pipeline(data["pipeline"])

        job["updated_at"] = _now_iso()
        self._get_job_path(job_id).write_text(json.dumps(job, indent=2), encoding="utf-8")
        return job

    def find_heartbeat_job(self, agent_id: str, entry_name: str) -> Optional[Dict[str, Any]]:
        for j in self.list_jobs(kind="heartbeat", include_archived=True):
            if j.get("agent_id") != agent_id:
                continue
            entry = j.get("heartbeat_entry") or {}
            if entry.get("name") == entry_name:
                return j
        return None

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
