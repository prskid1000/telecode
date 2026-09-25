"""Persistent storage for pipeline Runs.

A Run is one execution of a Job's pipeline. It owns N step entries; each step
has a corresponding queue Task (linked via step.task_id).

Layout:
  data/runs/<run_id>.json
    {
      "run_id": ..., "job_id": ..., "mode": "single|sequential|parallel",
      "source": "user|manual_run|heartbeat",
      "status": "pending|running|completed|failed|partial|cancelled|interrupted",
      "started_at": ..., "completed_at": ...,
      "overrides": {"engine", "model", "is_local"},     # run-level, from POST body
      "usage": {cost_usd, cost_complete, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens, num_turns, duration_ms},
      "steps": [
        {
          "step_id", "agent_id", "agent_name", "name",
          "engine", "model", "is_local",               # resolved for this step
          "task_id" | null,
          "session_id" | null,
          "status": "pending|running|completed|failed|cancelled|skipped|interrupted",
          "started_at" | null, "completed_at" | null,
          "result_preview": "..." | null,   # first 400 chars (list views)
          "result_text": "..." | null,      # handoff text, capped 16 KB head+tail
          "files_changed": [{path, change}] | null,
          "usage": {...} | null,
          "error": "..." | null,
        }, ...
      ]
    }

"interrupted" = the step's task was not alive when telecode (re)started —
see services.run.executor.reconcile_orphaned_runs.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from services.task.safe_paths import validate_id

logger = logging.getLogger("telecode.services.run")

VALID_RUN_STATUSES = ("pending", "running", "completed", "failed", "partial", "cancelled", "interrupted")
VALID_STEP_STATUSES = ("pending", "running", "completed", "failed", "cancelled", "skipped", "interrupted")

_USAGE_INT_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens",
                     "cache_write_tokens", "num_turns", "duration_ms")


def usage_from_result(result: Any) -> Optional[Dict[str, Any]]:
    """Normalise a handler result into a step usage record (None if absent).

    `cost_usd` stays None when the engine reports no cost (Codex, agy) so the
    roll-up can say the total is incomplete instead of pretending it is 0.
    """
    if not isinstance(result, dict):
        return None
    tokens = result.get("tokens") or {}
    cost = result.get("cost_usd")
    return {
        "cost_usd": float(cost) if isinstance(cost, (int, float)) else None,
        "input_tokens": int(tokens.get("total_input_incl_cache") or tokens.get("input") or 0),
        "output_tokens": int(tokens.get("output") or 0),
        "cache_read_tokens": int(tokens.get("cache_read") or 0),
        "cache_write_tokens": int(tokens.get("cache_write") or 0),
        "num_turns": int(result.get("num_turns") or 0),
        "duration_ms": int(result.get("duration_ms") or 0),
    }


def rollup_usage(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    total: Dict[str, Any] = {k: 0 for k in _USAGE_INT_FIELDS}
    cost = 0.0
    complete = True
    for st in steps:
        u = st.get("usage")
        if not u:
            continue
        for k in _USAGE_INT_FIELDS:
            total[k] += int(u.get(k) or 0)
        if u.get("cost_usd") is None:
            complete = False
        else:
            cost += float(u["cost_usd"])
    total["cost_usd"] = round(cost, 6)
    total["cost_complete"] = complete
    return total


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
        # Windows refuses to replace a file another handle has open (a reader
        # in another process, an AV scanner); retry briefly before failing.
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.025)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _path(run_id: str) -> Path:
    return get_runs_base_dir() / f"{validate_id(run_id, 'run_id')}.json"


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
        overrides: Optional[Dict[str, Any]] = None,
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
            "overrides": dict(overrides or {}),
            "usage": None,
            "steps": [
                {
                    "step_id": s.get("step_id") or str(uuid.uuid4()),
                    "agent_id": s.get("agent_id"),
                    "agent_name": s.get("agent_name", ""),
                    "name": s.get("name", ""),
                    "engine": s.get("engine") or "claude_code",
                    "model": s.get("model") or "",
                    "is_local": bool(s.get("is_local", False)),
                    "task_id": None,
                    "session_id": None,
                    "status": "pending",
                    "started_at": None,
                    "completed_at": None,
                    "result_preview": None,
                    "result_text": None,
                    "files_changed": None,
                    "usage": None,
                    "error": None,
                }
                for s in steps
            ],
        }
        with _lock:
            _atomic_write(_path(run_id), run)
        return run

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        try:
            p = _path(run_id)
        except ValueError:
            return None
        # Reads share the writers' lock: on Windows an open read handle makes
        # the writer's os.replace fail with "Access is denied".
        with _lock:
            if not p.exists():
                return None
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return None

    def list_runs(self, job_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        runs = []
        with _lock:
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
                elif run.get("cancel_requested"):
                    run["status"] = "cancelled"
                elif "interrupted" in statuses:
                    run["status"] = "interrupted"
                elif "cancelled" in statuses and not (statuses & {"running", "pending"}):
                    run["status"] = "cancelled" if statuses == {"cancelled"} else "partial"
                elif "failed" in statuses or "skipped" in statuses:
                    run["status"] = "partial" if "completed" in statuses else "failed"
                else:
                    run["status"] = "completed"
            if run["status"] not in ("running", "pending"):
                run["completed_at"] = run.get("completed_at") or _now_iso()
            run["usage"] = rollup_usage(steps)
            _atomic_write(_path(run_id), run)
            return run

    def delete_run(self, run_id: str) -> bool:
        try:
            p = _path(run_id)
        except ValueError:
            return False
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
