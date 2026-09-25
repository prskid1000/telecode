"""Persistent storage for pipeline Runs.

A Run is one execution of a Job's pipeline. It owns N step entries; each step
has a corresponding queue Task (linked via step.task_id).

Storage: SQLite, ``data/telecode.db`` — table ``runs`` (one row per run; the
record minus its steps in ``data``) and ``run_steps`` (one row per step, in
pipeline order). Pre-SQLite ``data/runs/*.json`` files are imported once per
database (flag ``meta.runs_json_imported``) and then left alone as a manual
backup; nothing reads them afterwards.

Record shape (unchanged, what every API returns):
    {
      "run_id": ..., "job_id": ..., "mode": "single|sequential|parallel",
      "source": "user|manual_run|heartbeat",
      "status": "pending|running|completed|failed|partial|cancelled|interrupted|budget_exceeded",
      "started_at": ..., "completed_at": ...,
      "overrides": {"engine", "model", "is_local"},     # run-level, from POST body
      "job_snapshot": {title, task_description, workspace_id},  # what the steps were built from
      "budget": {max_usd, max_tokens, max_seconds},     # resolved run budget (null = unlimited)
      "spent": {usd, usd_complete, tokens, seconds},    # vs budget (tokens = budget tokens)
      "active_seconds": float,                          # driver wall time, summed per phase
      "usage": {cost_usd, cost_complete, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens, num_turns, duration_ms},
      "steps": [
        {
          "step_id", "agent_id", "agent_name", "name",
          "engine", "model", "is_local",               # resolved for this step
          "spec": {phase, prompt_override, depends_on_text, session_policy, budget, auto_retry},
          "session_policy": "resume|fork|fresh|fresh_handoff|ephemeral",   # resolved
          "task_id" | null,                 # latest attempt's task
          "session_id" | null, "session_namespace" | null,
          "engine_session_id" | null,       # latest attempt's CLI conversation
          "status": "pending|running|completed|failed|cancelled|skipped|interrupted|budget_exceeded",
          "started_at" | null, "completed_at" | null,
          "result_preview": "..." | null,   # first 400 chars (list views)
          "result_text": "..." | null,      # the full final reply (capped 256 KB)
          "handoff": {status, summary, decisions, artifacts, open_questions,
                      next_steps, verdict, derived} | null,
          "files_changed": [{path, change, additions, deletions}] | null,
          "snapshot_before" | "snapshot_after": sha | null, "snapshot_key": str | null,
          "budget": {...} | null,           # effective budget of the latest attempt
          "usage": {...} | null,            # summed over all attempts (what was spent)
          "attempts": [{n, mode, task_id, status, error, started_at, completed_at,
                        engine_session_id, snapshot_before, snapshot_after, usage, budget}],
          "error": "..." | null,
        }, ...
      ]
    }

"interrupted" = the step's task was not alive when telecode (re)started —
see services.run.executor.reconcile_orphaned_runs. "budget_exceeded" = an
attempt hit its budget, or the run's budget was used up before it ran.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from services.db.core import connect, get_meta, set_meta
from services.task.safe_paths import validate_id

logger = logging.getLogger("telecode.services.run")

VALID_RUN_STATUSES = ("pending", "running", "awaiting_input", "completed", "failed", "partial", "cancelled",
                      "interrupted", "budget_exceeded", "rejected")
VALID_STEP_STATUSES = ("pending", "running", "awaiting_input", "completed", "failed", "cancelled", "skipped",
                       "interrupted", "budget_exceeded", "rejected")
TERMINAL_RUN_STATUSES = ("completed", "failed", "partial", "cancelled", "interrupted", "budget_exceeded",
                         "rejected")

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
    """Where pre-SQLite runs lived (``data/runs/*.json``). Read once, by the
    one-time import; the files are left on disk as a manual backup."""
    return Path(config._settings_dir()) / "data" / "runs"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_lock = threading.RLock()
_IMPORT_FLAG = "runs_json_imported"


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _check_id(run_id: str) -> Optional[str]:
    try:
        return validate_id(run_id, "run_id")
    except ValueError:
        return None


def _write_run(conn, run: Dict[str, Any]) -> None:
    """Upsert the run header and every step row (one transaction)."""
    head = {k: v for k, v in run.items() if k != "steps"}
    now = _now_iso()
    conn.execute(
        "INSERT INTO runs (run_id, job_id, status, mode, source, started_at, completed_at, data, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET job_id=excluded.job_id, "
        "status=excluded.status, mode=excluded.mode, source=excluded.source, started_at=excluded.started_at, "
        "completed_at=excluded.completed_at, data=excluded.data, updated_at=excluded.updated_at",
        (run["run_id"], run.get("job_id"), run.get("status"), run.get("mode"), run.get("source"),
         run.get("started_at"), run.get("completed_at"), _dumps(head), now))
    steps = run.get("steps") or []
    conn.execute("DELETE FROM run_steps WHERE run_id=? AND step_id NOT IN (%s)"
                 % ",".join("?" * len(steps)) if steps else "DELETE FROM run_steps WHERE run_id=?",
                 (run["run_id"], *[s.get("step_id") for s in steps]))
    for idx, st in enumerate(steps):
        _write_step(conn, run["run_id"], idx, st)


def _write_step(conn, run_id: str, idx: int, step: Dict[str, Any]) -> None:
    ho = step.get("handoff")
    conn.execute(
        "INSERT INTO run_steps (run_id, idx, step_id, status, task_id, data, handoff) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(run_id, step_id) DO UPDATE SET idx=excluded.idx, status=excluded.status, "
        "task_id=excluded.task_id, data=excluded.data, handoff=excluded.handoff",
        (run_id, idx, step.get("step_id"), step.get("status"), step.get("task_id"), _dumps(step),
         _dumps(ho) if ho else None))


def _read_run(conn, run_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT data FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        return None
    try:
        run = json.loads(row["data"])
    except ValueError:
        return None
    steps = []
    for r in conn.execute("SELECT data FROM run_steps WHERE run_id=? ORDER BY idx", (run_id,)):
        try:
            steps.append(json.loads(r["data"]))
        except ValueError:
            continue
    run["steps"] = steps
    return run


def _publish(run: Optional[Dict[str, Any]]) -> None:
    if not run:
        return
    try:
        from services import bus
        bus.publish("run", run["run_id"], "run", run)
        bus.publish_global("run", "run.update", {
            "run_id": run["run_id"], "job_id": run.get("job_id"), "status": run.get("status"),
            "source": run.get("source"), "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
            "steps": [{"step_id": s.get("step_id"), "status": s.get("status"), "task_id": s.get("task_id"),
                       "agent_name": s.get("agent_name"), "name": s.get("name")} for s in run.get("steps") or []],
        })
    except Exception:
        logger.exception("run bus publish failed")


def import_json_runs(conn=None) -> int:
    """One-time import of ``data/runs/*.json`` into SQLite (per database).
    Existing rows win (INSERT only if absent); the JSON files are not touched."""
    conn = conn or connect()
    if get_meta(_IMPORT_FLAG):
        return 0
    base = get_runs_base_dir()
    n = 0
    if base.is_dir():
        for p in sorted(base.glob("*.json")):
            try:
                run = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                logger.warning(f"run import: skipping unreadable {p.name}")
                continue
            rid = run.get("run_id") if isinstance(run, dict) else None
            if not rid or not _check_id(rid):
                continue
            if conn.execute("SELECT 1 FROM runs WHERE run_id=?", (rid,)).fetchone():
                continue
            conn.execute("BEGIN")
            try:
                _write_run(conn, run)
                conn.execute("COMMIT")
                n += 1
            except Exception:
                conn.execute("ROLLBACK")
                logger.exception(f"run import failed for {p.name}")
    set_meta(_IMPORT_FLAG, _now_iso())
    if n:
        logger.info(f"Imported {n} run(s) from {base} into telecode.db (JSON files kept as backup)")
    return n


class RunStore:
    """Runs in ``data/telecode.db`` (tables ``runs`` + ``run_steps``).

    Each mutation is a read-modify-write under one process-wide lock inside a
    single SQLite transaction, and publishes the new record on the live bus
    (``run`` frames on the run's topic, ``run.update`` on the global feed).
    """

    def __init__(self):
        self.base_dir = get_runs_base_dir()
        with _lock:
            try:
                import_json_runs()
            except Exception:
                logger.exception("one-time import of data/runs/*.json failed")

    def _mutate(self, run_id: str, fn) -> Optional[Dict[str, Any]]:
        if not _check_id(run_id):
            return None
        with _lock:
            conn = connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                run = _read_run(conn, run_id)
                if run is None:
                    conn.execute("ROLLBACK")
                    return None
                out = fn(conn, run)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        _publish(out)
        return out

    def create_run(
        self,
        *,
        job_id: str,
        mode: str,
        source: str,
        steps: List[Dict[str, Any]],
        overrides: Optional[Dict[str, Any]] = None,
        budget: Optional[Dict[str, Any]] = None,
        job_snapshot: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        run_id = str(uuid.uuid4())
        now = _now_iso()
        run = {
            **{k: v for k, v in (extra or {}).items() if v is not None},
            "run_id": run_id,
            "job_id": job_id,
            "mode": mode,
            "source": source,  # "user" | "manual_run" | "heartbeat"
            "status": "pending",
            "started_at": now,
            "completed_at": None,
            "overrides": dict(overrides or {}),
            "job_snapshot": dict(job_snapshot or {}),
            "budget": dict(budget or {}),
            "spent": {"usd": 0.0, "usd_complete": True, "tokens": 0, "seconds": 0.0},
            "active_seconds": 0.0,
            "usage": None,
            "steps": [
                {
                    "step_id": s.get("step_id") or str(uuid.uuid4()),
                    "agent_id": s.get("agent_id"),
                    "agent_name": s.get("agent_name", ""),
                    "name": s.get("name", ""),
                    "kind": s.get("kind") or (s.get("spec") or {}).get("kind") or "agent",
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
                    "spec": dict(s.get("spec") or {}),
                    "session_policy": s.get("session_policy") or "resume",
                    "session_namespace": None,
                    "engine_session_id": None,
                    "handoff": None,
                    "snapshot_key": None,
                    "snapshot_before": None,
                    "snapshot_after": None,
                    "budget": None,
                    "attempts": [],
                }
                for s in steps
            ],
        }
        with _lock:
            conn = connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                _write_run(conn, run)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        _publish(run)
        return run

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        if not _check_id(run_id):
            return None
        with _lock:
            return _read_run(connect(), run_id)

    def list_runs(self, job_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        with _lock:
            conn = connect()
            if job_id:
                rows = conn.execute("SELECT run_id FROM runs WHERE job_id=? ORDER BY started_at DESC LIMIT ?",
                                    (job_id, int(limit))).fetchall()
            else:
                rows = conn.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT ?",
                                    (int(limit),)).fetchall()
            out = []
            for r in rows:
                run = _read_run(conn, r["run_id"])
                if run:
                    out.append(run)
        return out

    def update_run(self, run_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        def fn(conn, run):
            for k, v in (patch or {}).items():
                run[k] = v
            _write_run(conn, run)
            return run
        return self._mutate(run_id, fn)

    def update_step(self, run_id: str, step_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        def fn(conn, run):
            for idx, s in enumerate(run.get("steps", [])):
                if s.get("step_id") == step_id:
                    for k, v in (patch or {}).items():
                        s[k] = v
                    _write_step(conn, run_id, idx, s)
                    if "usage" in (patch or {}):
                        from services.run.budget import spent
                        run["spent"] = spent(run)
                        _write_run(conn, run)
                    return run
            return None
        return self._mutate(run_id, fn)

    def finalise(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Compute aggregate run status from step statuses."""
        def fn(conn, run):
            steps = run.get("steps") or []
            if not steps:
                run["status"] = "completed"
            else:
                # A gate passed over by its on_timeout "skip" policy counts as done.
                statuses = {"completed" if s.get("status") == "skipped"
                            and (s.get("gate_decision") or {}).get("status") == "skipped" else s.get("status")
                            for s in steps}
                if statuses == {"completed"}:
                    run["status"] = "completed"
                elif "awaiting_input" in statuses and not run.get("cancel_requested"):
                    run["status"] = "awaiting_input"
                elif "running" in statuses or "pending" in statuses:
                    run["status"] = "running"
                elif run.get("cancel_requested"):
                    run["status"] = "cancelled"
                elif "rejected" in statuses:
                    run["status"] = "rejected"
                elif "budget_exceeded" in statuses or run.get("budget_exhausted"):
                    run["status"] = "budget_exceeded"
                elif "interrupted" in statuses:
                    run["status"] = "interrupted"
                elif "cancelled" in statuses and not (statuses & {"running", "pending"}):
                    run["status"] = "cancelled" if statuses == {"cancelled"} else "partial"
                elif "failed" in statuses or "skipped" in statuses:
                    run["status"] = "partial" if "completed" in statuses else "failed"
                else:
                    run["status"] = "completed"
            if run["status"] not in ("running", "pending", "awaiting_input"):
                run["completed_at"] = run.get("completed_at") or _now_iso()
            else:
                run["completed_at"] = None
            run["usage"] = rollup_usage(steps)
            from services.run.budget import spent
            run["spent"] = spent(run)
            _write_run(conn, run)
            return run
        return self._mutate(run_id, fn)

    def mutate(self, run_id: str, fn) -> Optional[Dict[str, Any]]:
        """``fn(run) -> None`` edits the record in place inside one
        transaction (the whole run is rewritten); returns the new record."""
        def wrap(conn, run):
            fn(run)
            from services.run.budget import spent
            run["spent"] = spent(run)
            _write_run(conn, run)
            return run
        return self._mutate(run_id, wrap)

    def delete_run(self, run_id: str) -> bool:
        if not _check_id(run_id):
            return False
        with _lock:
            conn = connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
                conn.execute("DELETE FROM run_steps WHERE run_id=?", (run_id,))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return cur.rowcount > 0


_store: Optional[RunStore] = None


def get_run_store() -> RunStore:
    global _store
    if _store is None:
        _store = RunStore()
    return _store
