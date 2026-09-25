"""Trigger persistence: ``triggers`` + ``trigger_fires`` in data/telecode.db.

The full record lives in ``triggers.data`` (JSON); the columns mirror what the
scheduler queries by. Every write publishes a ``trigger.update`` summary on the
global live feed (kind ``trigger``). Mutations of one trigger go through
:func:`lock_for` (re-entrant, per id) so a scheduler fire, a webhook and a
"fire now" click can never race each other.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional

from services.db.core import connect, now_iso

logger = logging.getLogger("telecode.services.triggers.store")

_guard = threading.Lock()
_locks: Dict[str, threading.RLock] = {}
_db_lock = threading.RLock()

FIRE_STATUSES = ("running", "completed", "ok", "skipped", "failed", "cancelled", "interrupted")
FIRE_TERMINAL = ("completed", "ok", "skipped", "failed", "cancelled", "interrupted")


def lock_for(trigger_id: str) -> threading.RLock:
    with _guard:
        lk = _locks.get(trigger_id)
        if lk is None:
            lk = _locks[trigger_id] = threading.RLock()
        return lk


def _load(row) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    try:
        return json.loads(row["data"])
    except ValueError:
        return None


def _summary(rec: Dict[str, Any]) -> Dict[str, Any]:
    st = rec.get("state") or {}
    return {"id": rec["id"], "name": rec.get("name"), "status": rec.get("status"),
            "target": {"kind": (rec.get("target") or {}).get("kind"), "id": (rec.get("target") or {}).get("id"),
                       "agent_id": (rec.get("target") or {}).get("agent_id")},
            "next_fire_at": st.get("next_fire_at"), "last_fire_at": st.get("last_fire_at"),
            "last_status": st.get("last_status"), "paused_reason": st.get("paused_reason")}


def _publish(etype: str, data: Dict[str, Any]) -> None:
    try:
        from services import bus
        bus.publish_global("trigger", etype, data)
    except Exception:
        logger.exception("trigger bus publish failed")


def save(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Insert or replace the record (``id`` assigned when missing)."""
    rec = dict(rec)
    rec.setdefault("id", str(uuid.uuid4()))
    now = now_iso()
    rec.setdefault("created_at", now)
    rec["updated_at"] = now
    t = rec.get("target") or {}
    with _db_lock:
        connect().execute(
            "INSERT INTO triggers (id, name, source, source_key, status, target_kind, target_id, agent_id, "
            "next_fire_at, data, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, source=excluded.source, "
            "source_key=excluded.source_key, status=excluded.status, target_kind=excluded.target_kind, "
            "target_id=excluded.target_id, agent_id=excluded.agent_id, next_fire_at=excluded.next_fire_at, "
            "data=excluded.data, updated_at=excluded.updated_at",
            (rec["id"], rec.get("name"), rec.get("source") or "user", rec.get("source_key"), rec.get("status"),
             t.get("kind"), t.get("id"), t.get("agent_id") or rec.get("owner_agent_id"),
             (rec.get("state") or {}).get("next_fire_at"),
             json.dumps(rec, ensure_ascii=False, default=str), rec["created_at"], rec["updated_at"]))
    _publish("trigger.update", _summary(rec))
    return rec


def get(trigger_id: str) -> Optional[Dict[str, Any]]:
    return _load(connect().execute("SELECT data FROM triggers WHERE id=?", (trigger_id,)).fetchone())


def get_by_source_key(key: str) -> Optional[Dict[str, Any]]:
    return _load(connect().execute("SELECT data FROM triggers WHERE source_key=?", (key,)).fetchone())


def list_all(*, status: Optional[str] = None, target_kind: Optional[str] = None, target_id: Optional[str] = None,
             agent_id: Optional[str] = None, source: Optional[str] = None) -> List[Dict[str, Any]]:
    q, args = "SELECT data FROM triggers WHERE 1=1", []
    for col, val in (("status", status), ("target_kind", target_kind), ("target_id", target_id),
                     ("agent_id", agent_id), ("source", source)):
        if val:
            q += f" AND {col}=?"
            args.append(val)
    q += " ORDER BY created_at"
    out = []
    for r in connect().execute(q, args):
        rec = _load(r)
        if rec:
            out.append(rec)
    return out


def mutate(trigger_id: str, fn: Callable[[Dict[str, Any]], None]) -> Optional[Dict[str, Any]]:
    """Read-modify-write one record under its lock; returns the new record."""
    with lock_for(trigger_id):
        rec = get(trigger_id)
        if rec is None:
            return None
        fn(rec)
        return save(rec)


def delete(trigger_id: str) -> bool:
    with lock_for(trigger_id), _db_lock:
        cur = connect().execute("DELETE FROM triggers WHERE id=?", (trigger_id,))
    if cur.rowcount:
        _publish("trigger.deleted", {"id": trigger_id})
    return cur.rowcount > 0


# ── fires (history) ─────────────────────────────────────────────────────────

def _fire_row(r) -> Dict[str, Any]:
    d = dict(r)
    try:
        d["data"] = json.loads(d["data"]) if d.get("data") else {}
    except ValueError:
        d["data"] = {}
    return d


def add_fire(trigger_id: str, *, source: str, status: str, reason: Optional[str] = None,
             task_id: Optional[str] = None, run_id: Optional[str] = None,
             data: Optional[Dict[str, Any]] = None, collapse_skips: bool = True,
             fire_id: Optional[str] = None) -> Dict[str, Any]:
    """Append a fire row. Consecutive skips for the same reason collapse into
    one row (``data.count`` + the latest ``fired_at``) so an every-minute
    trigger outside its active hours does not write a row a minute all night."""
    now = now_iso()
    with _db_lock:
        conn = connect()
        if status == "skipped" and collapse_skips:
            last = conn.execute("SELECT * FROM trigger_fires WHERE trigger_id=? ORDER BY seq DESC LIMIT 1",
                                (trigger_id,)).fetchone()
            if last and last["status"] == "skipped" and (last["reason"] or "") == (reason or ""):
                d = _fire_row(last)["data"]
                d["count"] = int(d.get("count") or 1) + 1
                d["first_at"] = d.get("first_at") or last["fired_at"]
                conn.execute("UPDATE trigger_fires SET fired_at=?, completed_at=?, data=? WHERE id=?",
                             (now, now, json.dumps(d), last["id"]))
                row = _fire_row(conn.execute("SELECT * FROM trigger_fires WHERE id=?", (last["id"],)).fetchone())
                _publish("trigger.fire", {"trigger_id": trigger_id, **{k: row[k] for k in ("id", "status", "reason")}})
                return row
        seq = int(conn.execute("SELECT COALESCE(MAX(seq),0) FROM trigger_fires WHERE trigger_id=?",
                               (trigger_id,)).fetchone()[0]) + 1
        fid = fire_id or str(uuid.uuid4())
        conn.execute(
            "INSERT INTO trigger_fires (id, trigger_id, seq, source, status, reason, task_id, run_id, fired_at, "
            "completed_at, data) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (fid, trigger_id, seq, source, status, reason, task_id, run_id, now,
             now if status in FIRE_TERMINAL else None, json.dumps(data or {}, ensure_ascii=False, default=str)))
        row = _fire_row(conn.execute("SELECT * FROM trigger_fires WHERE id=?", (fid,)).fetchone())
    _publish("trigger.fire", {"trigger_id": trigger_id, "id": fid, "status": status, "reason": reason,
                              "task_id": task_id, "run_id": run_id})
    return row


def update_fire(fire_id: str, **patch: Any) -> Optional[Dict[str, Any]]:
    cols = {k: v for k, v in patch.items() if k in ("status", "reason", "task_id", "run_id", "completed_at",
                                                      "cost_usd")}
    with _db_lock:
        conn = connect()
        row = conn.execute("SELECT * FROM trigger_fires WHERE id=?", (fire_id,)).fetchone()
        if not row:
            return None
        data = _fire_row(row)["data"]
        if isinstance(patch.get("data"), dict):
            data.update(patch["data"])
        if cols.get("status") in FIRE_TERMINAL and "completed_at" not in cols:
            cols["completed_at"] = now_iso()
        sets = ", ".join(f"{k}=?" for k in cols) + (", " if cols else "") + "data=?"
        conn.execute(f"UPDATE trigger_fires SET {sets} WHERE id=?", (*cols.values(), json.dumps(data), fire_id))
        out = _fire_row(conn.execute("SELECT * FROM trigger_fires WHERE id=?", (fire_id,)).fetchone())
    _publish("trigger.fire", {"trigger_id": out["trigger_id"], "id": fire_id, "status": out["status"],
                              "reason": out.get("reason"), "task_id": out.get("task_id"), "run_id": out.get("run_id")})
    return out


def get_fire(fire_id: str) -> Optional[Dict[str, Any]]:
    r = connect().execute("SELECT * FROM trigger_fires WHERE id=?", (fire_id,)).fetchone()
    return _fire_row(r) if r else None


def list_fires(trigger_id: str, limit: int = 100, status: Optional[str] = None) -> List[Dict[str, Any]]:
    q, args = "SELECT * FROM trigger_fires WHERE trigger_id=?", [trigger_id]
    if status:
        q += " AND status=?"
        args.append(status)
    q += " ORDER BY seq DESC LIMIT ?"
    args.append(int(limit))
    return [_fire_row(r) for r in connect().execute(q, args)]


def running_fires() -> List[Dict[str, Any]]:
    return [_fire_row(r) for r in connect().execute("SELECT * FROM trigger_fires WHERE status='running'")]


def last_fire(trigger_id: str) -> Optional[Dict[str, Any]]:
    r = connect().execute("SELECT * FROM trigger_fires WHERE trigger_id=? AND status!='skipped' "
                          "ORDER BY seq DESC LIMIT 1", (trigger_id,)).fetchone()
    return _fire_row(r) if r else None
