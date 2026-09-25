"""Approvals inbox (P3) — one table for every "a person must decide" pause.

``approvals`` rows in ``data/telecode.db``::

    {id, kind: gate|tool|memory, run_id, step_id, trigger_id, title, body,
     payload, status: pending|approved|rejected|cancelled, created_at,
     decided_at, decided_by, decision_note, edited_text, telegram}

* :func:`create` inserts a pending row and publishes ``approval.created`` on the
  global live feed (kind ``approval``) — the web inbox badge and the Telegram
  notifier both listen there.
* :func:`decide` flips a *pending* row atomically (``UPDATE … WHERE
  status='pending'``), so a click in the web UI and one on the Telegram button
  can never both win; publishes ``approval.decided`` and then calls the handler
  registered for the row's kind (a pipeline gate resumes its run).
* A pending row survives a restart — it is the gate's durable state; the run
  stays ``awaiting_input`` until someone decides.

Only ``gate`` has a producer today (the run executor). ``tool`` (a CLI's
permission prompt routed to a person) and ``memory`` (a proposed memory diff)
are reserved kinds with the same inbox, for P4/P5.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional

from services.db.core import connect, now_iso

logger = logging.getLogger("telecode.services.approvals")

KINDS = ("gate", "tool", "memory")
STATUSES = ("pending", "approved", "rejected", "cancelled")
DECISIONS = {"approve": "approved", "reject": "rejected"}
BODY_CAP = 64 * 1024
NOTE_CAP = 16 * 1024

_lock = threading.RLock()
_handlers: Dict[str, Callable[[Dict[str, Any]], None]] = {}


class ApprovalError(ValueError):
    """Bad request (unknown id, bad decision)."""


class AlreadyDecided(RuntimeError):
    """The approval is no longer pending; ``.approval`` is its current row."""

    def __init__(self, approval: Dict[str, Any]):
        super().__init__(f"approval already {approval.get('status')}")
        self.approval = approval


def register_handler(kind: str, fn: Callable[[Dict[str, Any]], None]) -> None:
    """``fn(approval)`` runs after a row of ``kind`` is decided (any decision)."""
    _handlers[kind] = fn


def _row(r) -> Dict[str, Any]:
    d = dict(r)
    for k in ("payload", "telegram"):
        try:
            d[k] = json.loads(d[k]) if d.get(k) else None
        except ValueError:
            d[k] = None
    return d


def _publish(etype: str, ap: Dict[str, Any]) -> None:
    try:
        from services import bus
        bus.publish_global("approval", etype, ap)
    except Exception:
        logger.exception("approval bus publish failed")


def create(kind: str, *, title: str, body: str = "", payload: Optional[Dict[str, Any]] = None,
           run_id: Optional[str] = None, step_id: Optional[str] = None,
           trigger_id: Optional[str] = None) -> Dict[str, Any]:
    if kind not in KINDS:
        raise ApprovalError(f"kind must be one of {KINDS}")
    aid = str(uuid.uuid4())
    with _lock:
        conn = connect()
        conn.execute(
            "INSERT INTO approvals (id, kind, run_id, step_id, trigger_id, title, body, payload, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?, 'pending', ?)",
            (aid, kind, run_id, step_id, trigger_id, (title or "")[:500], (body or "")[:BODY_CAP],
             json.dumps(payload or {}, ensure_ascii=False, default=str), now_iso()))
        ap = get(aid)
    _publish("approval.created", ap)
    return ap


def get(approval_id: str) -> Optional[Dict[str, Any]]:
    r = connect().execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
    return _row(r) if r else None


def find_pending(run_id: str, step_id: str) -> Optional[Dict[str, Any]]:
    r = connect().execute("SELECT * FROM approvals WHERE run_id=? AND step_id=? AND status='pending' "
                          "ORDER BY created_at DESC LIMIT 1", (run_id, step_id)).fetchone()
    return _row(r) if r else None


def for_step(run_id: str, step_id: str) -> List[Dict[str, Any]]:
    return [_row(r) for r in connect().execute(
        "SELECT * FROM approvals WHERE run_id=? AND step_id=? ORDER BY created_at", (run_id, step_id))]


def list_approvals(status: Optional[str] = "pending", limit: int = 200,
                   run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    q, args = "SELECT * FROM approvals WHERE 1=1", []
    if status:
        q += " AND status=?"
        args.append(status)
    if run_id:
        q += " AND run_id=?"
        args.append(run_id)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(int(limit))
    return [_row(r) for r in connect().execute(q, args)]


def pending_count() -> int:
    return int(connect().execute("SELECT COUNT(*) FROM approvals WHERE status='pending'").fetchone()[0])


def decide(approval_id: str, decision: str, *, by: str = "web", note: Optional[str] = None,
           edited_text: Optional[str] = None) -> Dict[str, Any]:
    """approve | reject a pending approval. Raises ApprovalError (unknown /
    bad decision) or AlreadyDecided. ``edited_text`` (approve only) replaces
    what the approval proposed — a gate hands it to the next step."""
    status = DECISIONS.get((decision or "").lower())
    if not status:
        raise ApprovalError("decision must be approve or reject")
    if edited_text is not None and status != "approved":
        raise ApprovalError("edited text is only accepted with approve")
    with _lock:
        conn = connect()
        cur = conn.execute(
            "UPDATE approvals SET status=?, decided_at=?, decided_by=?, decision_note=?, edited_text=? "
            "WHERE id=? AND status='pending'",
            (status, now_iso(), (by or "web")[:200], (note or "")[:NOTE_CAP] or None,
             edited_text[:BODY_CAP] if isinstance(edited_text, str) else None, approval_id))
        ap = get(approval_id)
        if ap is None:
            raise ApprovalError("approval not found")
        if cur.rowcount == 0:
            raise AlreadyDecided(ap)
    _publish("approval.decided", ap)
    if ap["kind"] == "gate" and "gate" not in _handlers:
        import services.run.executor  # noqa: F401 — registers the gate handler
    fn = _handlers.get(ap["kind"])
    if fn:
        try:
            fn(ap)
        except Exception:
            logger.exception(f"approval handler for {ap['kind']} failed ({approval_id})")
    return ap


def cancel_for(run_id: str, step_id: Optional[str] = None, reason: str = "run cancelled") -> int:
    """Pending approvals of a run (or one step) → cancelled. No handler runs."""
    with _lock:
        conn = connect()
        rows = conn.execute("SELECT id FROM approvals WHERE run_id=? AND status='pending'"
                            + (" AND step_id=?" if step_id else ""),
                            (run_id, step_id) if step_id else (run_id,)).fetchall()
        for r in rows:
            conn.execute("UPDATE approvals SET status='cancelled', decided_at=?, decided_by='system', "
                         "decision_note=? WHERE id=? AND status='pending'", (now_iso(), reason, r["id"]))
    for r in rows:
        ap = get(r["id"])
        if ap:
            _publish("approval.decided", ap)
    return len(rows)


def cancel(approval_id: str, reason: str = "cancelled") -> Optional[Dict[str, Any]]:
    """One pending approval → cancelled (P5: a tool approval that timed out, or
    whose task stopped). No handler runs. Returns the row (None if unknown)."""
    with _lock:
        cur = connect().execute("UPDATE approvals SET status='cancelled', decided_at=?, decided_by='system', "
                                "decision_note=? WHERE id=? AND status='pending'",
                                (now_iso(), (reason or "")[:NOTE_CAP], approval_id))
        ap = get(approval_id)
    if ap and cur.rowcount:
        _publish("approval.decided", ap)
    return ap


def set_telegram(approval_id: str, info: Optional[Dict[str, Any]]) -> None:
    """Remember where the Telegram notifier posted this approval (to edit it later)."""
    connect().execute("UPDATE approvals SET telegram=? WHERE id=?",
                      (json.dumps(info) if info else None, approval_id))
