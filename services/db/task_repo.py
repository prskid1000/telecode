"""Task + task-event persistence (write-through from the in-memory TaskQueue).

The queue stays the source of truth for *live* tasks (handlers read their own
Task object); this table is what survives a restart: ``GET /api/tasks`` and
``GET /api/tasks/{id}`` fall back to it for tasks no longer in memory, and the
SSE route replays events from it.

Events are capped per task: the first ``KEEP_HEAD`` (the ``start`` event and
early context) plus the last ``KEEP_TAIL``. ``seq`` is the 1-based position in
the task's event stream and never reused, so a client can resume after any
``seq`` it has seen.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Optional

from services.db import writer
from services.db.core import connect, now_iso

logger = logging.getLogger("telecode.services.db.tasks")

KEEP_HEAD = 50
KEEP_TAIL = 2000
_TRIM_EVERY = 200

ACTIVE = ("pending", "running")


def _dumps(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False, default=str)


def _loads(s: Optional[str]) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return None


def save_task(d: Dict[str, Any]) -> None:
    """Upsert a task from its ``task_to_dict`` shape (metadata.events excluded).
    Serialised now, written by the background writer (``services.db.writer``)."""
    md = dict(d.get("metadata") or {})
    md.pop("events", None)
    row = (d["task_id"], d.get("task_type") or "", d.get("session_id"), d.get("session_namespace"),
           d.get("status") or "pending", d.get("pool"), d.get("timeout_seconds"),
           float(d.get("progress") or 0), d.get("created_at"), d.get("started_at"),
           d.get("completed_at"), d.get("error"), _dumps(md), _dumps(d.get("result")), now_iso())
    writer.submit(_save_row, row)


def _save_row(conn, row: tuple) -> None:
    conn.execute(
        """INSERT INTO tasks (task_id, task_type, session_id, session_namespace, status, pool,
                              timeout_seconds, progress, created_at, started_at, completed_at,
                              error, metadata, result, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(task_id) DO UPDATE SET
             status=excluded.status, progress=excluded.progress, started_at=excluded.started_at,
             completed_at=excluded.completed_at, error=excluded.error, metadata=excluded.metadata,
             result=excluded.result, updated_at=excluded.updated_at,
             session_id=excluded.session_id, session_namespace=excluded.session_namespace""", row)


def append_event(task_id: str, seq: int, event: Dict[str, Any]) -> None:
    writer.submit(_append_row, task_id, int(seq), event.get("kind"), event.get("ts"), _dumps(event))


def _append_row(conn, task_id: str, seq: int, kind: Optional[str], ts: Optional[str], data: str) -> None:
    conn.execute("INSERT OR REPLACE INTO task_events (task_id, seq, kind, ts, data) VALUES (?,?,?,?,?)",
                 (task_id, seq, kind, ts, data))
    if seq > KEEP_HEAD + KEEP_TAIL and seq % _TRIM_EVERY == 0:
        conn.execute("DELETE FROM task_events WHERE task_id=? AND seq>? AND seq<=?",
                     (task_id, KEEP_HEAD, seq - KEEP_TAIL))


def flush(timeout: float = 10.0) -> bool:
    return writer.flush(timeout)


def events(task_id: str, after: int = 0, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    sql = "SELECT seq, data FROM task_events WHERE task_id=? AND seq>? ORDER BY seq"
    args: list = [task_id, int(after or 0)]
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))
    out = []
    for row in connect().execute(sql, args):
        ev = _loads(row["data"]) or {}
        ev["seq"] = row["seq"]
        out.append(ev)
    return out


def _row_to_dict(row: Any) -> Dict[str, Any]:
    md = _loads(row["metadata"]) or {}
    return {
        "task_id": row["task_id"],
        "task_type": row["task_type"],
        "session_id": row["session_id"],
        "session_namespace": row["session_namespace"],
        "status": row["status"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "progress": row["progress"] or 0.0,
        "metadata": md,
        "result": _loads(row["result"]),
        "error": row["error"],
        "pool": row["pool"],
        "timeout_seconds": row["timeout_seconds"],
    }


def load_task(task_id: str, with_events: bool = True) -> Optional[Dict[str, Any]]:
    row = connect().execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        return None
    d = _row_to_dict(row)
    if with_events:
        d["metadata"]["events"] = [{k: v for k, v in e.items() if k != "seq"} for e in events(task_id)]
    return d


def list_tasks(limit: int = 500, exclude: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """Most recent tasks (newest first) not in ``exclude``, each carrying only
    its first event (the ``start`` event the task lists read the prompt from)."""
    skip = set(exclude)
    rows = connect().execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                             (int(limit) + len(skip),)).fetchall()
    out = [_row_to_dict(r) for r in rows if r["task_id"] not in skip][:limit]
    if out:
        ids = [d["task_id"] for d in out]
        firsts: Dict[str, Dict[str, Any]] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = f"SELECT task_id, data FROM task_events WHERE seq=1 AND task_id IN ({','.join('?' * len(chunk))})"
            for row in connect().execute(q, chunk):
                firsts[row["task_id"]] = _loads(row["data"]) or {}
        for d in out:
            first = firsts.get(d["task_id"])
            d["metadata"]["events"] = [first] if first else []
    return out


def reconcile_interrupted(is_active) -> int:
    """Tasks the DB says are pending/running but this process is not running
    (telecode restarted mid-task) become failed/interrupted."""
    writer.flush(5.0)
    conn = connect()
    rows = conn.execute("SELECT task_id FROM tasks WHERE status IN ('pending','running')").fetchall()
    n = 0
    for row in rows:
        if is_active(row["task_id"]):
            continue
        conn.execute("UPDATE tasks SET status='failed', completed_at=COALESCE(completed_at, ?), "
                     "error=?, updated_at=? WHERE task_id=? AND status IN ('pending','running')",
                     (now_iso(), "interrupted: telecode restarted while this task was running",
                      now_iso(), row["task_id"]))
        n += 1
    if n:
        logger.warning("Marked %d task(s) from a previous process as interrupted", n)
    return n
