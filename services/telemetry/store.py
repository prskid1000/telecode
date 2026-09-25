"""Telemetry rows in ``data/telecode.db`` (migration 4): ``spans``,
``metric_points``, ``log_events``.

* ``spans`` holds both telecode's own GenAI spans (``source='telecode'``:
  ``invoke_workflow`` per run, ``invoke_agent`` per engine run = step attempt /
  task, ``execute_tool`` per normalised tool event) and spans a CLI exported
  over OTLP (``source='otlp'``). Keyed (trace_id, span_id) — an own span is
  written once when it starts and replaced when it ends.
* ``metric_points`` / ``log_events`` are what the CLIs export (Claude Code's
  ``claude_code.*`` metrics and events, Codex's ``codex.*`` events).
* Every row carries the ``telecode.*`` correlation ids (task / run / step /
  agent / trigger) lifted from the OTLP resource, so dashboards join by id.

Retention: :func:`prune` deletes rows older than ``telemetry.retention_days``;
it runs at most once an hour, from the receiver and the dashboard queries.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

from services.db.core import connect

logger = logging.getLogger("telecode.services.telemetry.store")

SPAN_COLS = ("trace_id", "span_id", "parent_span_id", "name", "operation", "kind", "source", "start_ms",
             "end_ms", "duration_ms", "status", "status_message", "task_id", "run_id", "step_id", "agent_id",
             "job_id", "trigger_id", "engine", "model", "tool_name", "input_tokens", "output_tokens",
             "cache_read_tokens", "cache_write_tokens", "cost_usd", "attributes", "resource", "received_ms")
POINT_COLS = ("ts_ms", "name", "value", "unit", "kind", "temporality", "type", "model", "session_id", "task_id",
              "run_id", "step_id", "agent_id", "trigger_id", "attributes", "resource", "received_ms")
LOG_COLS = ("ts_ms", "name", "severity", "body", "model", "tool_name", "success", "duration_ms", "cost_usd",
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "session_id", "task_id",
            "run_id", "step_id", "agent_id", "trigger_id", "attributes", "resource", "received_ms")

PRUNE_EVERY_SEC = 3600
_prune_lock = threading.Lock()
_last_prune: Dict[str, float] = {}


def now_ms() -> int:
    return int(time.time() * 1000)


def _dumps(v: Any) -> Optional[str]:
    if v is None or v == {}:
        return None
    return json.dumps(v, ensure_ascii=False, default=str)


def _vals(row: Dict[str, Any], cols: Iterable[str]) -> tuple:
    out = []
    for c in cols:
        v = row.get(c)
        if c in ("attributes", "resource") and not isinstance(v, (str, type(None))):
            v = _dumps(v)
        out.append(v)
    return tuple(out)


def _insert(conn, table: str, cols: tuple, rows: List[Dict[str, Any]], replace: bool = False) -> int:
    if not rows:
        return 0
    verb = "INSERT OR REPLACE" if replace else "INSERT"
    sql = f"{verb} INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    conn.execute("BEGIN")
    try:
        conn.executemany(sql, [_vals(r, cols) for r in rows])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(rows)


def insert_spans(rows: List[Dict[str, Any]], conn=None) -> int:
    return _insert(conn or connect(), "spans", SPAN_COLS, rows, replace=True)


def insert_points(rows: List[Dict[str, Any]], conn=None) -> int:
    return _insert(conn or connect(), "metric_points", POINT_COLS, rows)


def insert_logs(rows: List[Dict[str, Any]], conn=None) -> int:
    return _insert(conn or connect(), "log_events", LOG_COLS, rows)


def prune(retention_days: Optional[int] = None, *, force: bool = False, conn=None) -> int:
    """Delete telemetry older than the retention window (throttled to once an hour per db)."""
    from services.db.core import db_path
    from services.telemetry import settings
    key = str(db_path())
    with _prune_lock:
        if not force and time.time() - _last_prune.get(key, 0) < PRUNE_EVERY_SEC:
            return 0
        _last_prune[key] = time.time()
    days = retention_days if retention_days is not None else settings.retention_days()
    cutoff = now_ms() - int(days) * 86400 * 1000
    c = conn or connect()
    n = 0
    for table, col in (("spans", "start_ms"), ("metric_points", "ts_ms"), ("log_events", "ts_ms")):
        cur = c.execute(f"DELETE FROM {table} WHERE {col} < ?", (cutoff,))
        n += cur.rowcount or 0
    if n:
        logger.info("telemetry: pruned %d rows older than %d days", n, days)
    return n


def counts(conn=None) -> Dict[str, Any]:
    c = conn or connect()
    out: Dict[str, Any] = {}
    for table, col in (("spans", "received_ms"), ("metric_points", "received_ms"), ("log_events", "received_ms")):
        r = c.execute(f"SELECT COUNT(*), MAX({col}) FROM {table}").fetchone()
        out[table] = {"rows": int(r[0] or 0), "last_received_ms": r[1]}
    r = c.execute("SELECT COUNT(*) FROM spans WHERE source='otlp'").fetchone()
    out["spans"]["otlp"] = int(r[0] or 0)
    return out
