"""Persistent storage for pipeline Runs.

A Run is one execution of a Job's pipeline. It owns N step entries; each step
has a corresponding queue Task (linked via step.task_id).

Layout:
  data/runs/<run_id>.json
    {
      "run_id": ..., "job_id": ..., "mode": "single|sequential|parallel",
      "source": "user|manual_run|heartbeat",
      "status": "pending|running|completed|failed|partial|cancelled",
      "started_at": ..., "completed_at": ...,
      "steps": [
        {
          "step_id", "agent_id", "agent_name", "name",
          "task_id" | null,
          "session_id" | null,
          "status": "pending|running|completed|failed|cancelled|skipped",
          "started_at" | null, "completed_at" | null,
          "result_preview": "..." | null,
          "error": "..." | null,
        }, ...
      ]
    }
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger("telecode.services.run")

VALID_RUN_STATUSES = ("pending", "running", "completed", "failed", "partial", "cancelled")
VALID_STEP_STATUSES = ("pending", "running", "completed", "failed", "cancelled", "skipped")


def get_runs_base_dir() -> Path:
    return Path(config._settings_dir()) / "data" / "runs"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_lock = threading.RLock()


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, default=str).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".run.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encoded)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _path(run_id: str) -> Path:
    return get_runs_base_dir() / f"{run_id}.json"


class RunStore:
    def __init__(self):
        self.base_dir = get_runs_base_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def create_run(
        self,
        *,
        job_id: str,
        mode: str,
        source: str,
        steps: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        run_id = str(uuid.uuid4())
        now = _now_iso()
        run = {
            "run_id": run_id,
            "job_id": job_id,
            "mode": mode,
            "source": source,  # "user" | "manual_run" | "heartbeat"
            "status": "pending",
            "started_at": now,
            "completed_at": None,
            "steps": [
                {
                    "step_id": s.get("step_id") or str(uuid.uuid4()),
                    "agent_id": s.get("agent_id"),
                    "agent_name": s.get("agent_name", ""),
                    "name": s.get("name", ""),
                    "task_id": None,
                    "session_id": None,
                    "status": "pending",
                    "started_at": None,
                    "completed_at": None,
                    "result_preview": None,
                    "error": None,
                }
                for s in steps
            ],
        }
        with _lock:
            _atomic_write(_path(run_id), run)
        return run

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        p = _path(run_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def list_runs(self, job_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        runs = []
        for p in self.base_dir.glob("*.json"):
            try:
                runs.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
        if job_id:
            runs = [r for r in runs if r.get("job_id") == job_id]
        runs.sort(key=lambda r: r.get("started_at", ""), reverse=True)
        return runs[:limit]

    def update_run(self, run_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with _lock:
            run = self.get_run(run_id)
            if not run:
                return None
            for k, v in (patch or {}).items():
                run[k] = v
            _atomic_write(_path(run_id), run)
            return run

    def update_step(self, run_id: str, step_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with _lock:
            run = self.get_run(run_id)
            if not run:
                return None
            for s in run.get("steps", []):
                if s.get("step_id") == step_id:
                    for k, v in (patch or {}).items():
                        s[k] = v
                    _atomic_write(_path(run_id), run)
                    return run
            return None

    def finalise(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Compute aggregate run status from step statuses."""
        with _lock:
            run = self.get_run(run_id)
            if not run:
                return None
            steps = run.get("steps") or []
            if not steps:
                run["status"] = "completed"
            else:
                statuses = {s.get("status") for s in steps}
                if statuses == {"completed"}:
                    run["status"] = "completed"
                elif "running" in statuses or "pending" in statuses:
                    run["status"] = "running"
                elif "cancelled" in statuses and not (statuses & {"running", "pending"}):
                    run["status"] = "cancelled" if statuses == {"cancelled"} else "partial"
                elif "failed" in statuses or "skipped" in statuses:
                    run["status"] = "partial" if "completed" in statuses else "failed"
                else:
                    run["status"] = "completed"
            if run["status"] not in ("running", "pending"):
                run["completed_at"] = run.get("completed_at") or _now_iso()
            _atomic_write(_path(run_id), run)
            return run

    def delete_run(self, run_id: str) -> bool:
        p = _path(run_id)
        if p.exists():
            p.unlink()
            return True
        return False


_store: Optional[RunStore] = None


def get_run_store() -> RunStore:
    global _store
    if _store is None:
        _store = RunStore()
    return _store
