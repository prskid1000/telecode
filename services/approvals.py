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

Producers: ``gate`` (the run executor), ``tool`` (P5, a CLI's permission prompt
routed to a person) and ``memory`` (P4, a proposed memory diff).

Deadlines (deferred-P3): :func:`create` takes ``deadline_at`` (UTC ISO) and
``on_timeout`` (``reject`` | ``approve`` | ``skip``); both are persisted in the
row's ``payload`` and surfaced as top-level ``deadline_at`` / ``on_timeout``.
:func:`expire_due` resolves every pending row whose deadline has passed with
``decided_by = "timeout"`` — reject → ``rejected``, approve → ``approved``,
skip → ``skipped`` — publishes ``approval.decided`` and calls the kind's handler
(the gate handler continues or ends the run). A daemon thread
(:func:`start_timeout_checker`, started by the run executor at startup and
whenever a gate with a deadline opens) calls it every
``TIMEOUT_CHECK_SECONDS``; because the deadline lives in the table, a restart
simply catches up on the next tick.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from services.db.core import connect, now_iso

logger = logging.getLogger("telecode.services.approvals")

KINDS = ("gate", "tool", "memory")
STATUSES = ("pending", "approved", "rejected", "cancelled", "skipped")
DECISIONS = {"approve": "approved", "reject": "rejected"}
TIMEOUT_POLICIES = {"reject": "rejected", "approve": "approved", "skip": "skipped"}
TIMEOUT_BY = "timeout"
TIMEOUT_CHECK_SECONDS = 5.0
BODY_CAP = 64 * 1024
NOTE_CAP = 16 * 1024

_lock = threading.RLock()
_handlers: Dict[str, Callable[[Dict[str, Any]], None]] = {}
_checker: Optional[threading.Thread] = None
_checker_stop = threading.Event()


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
    p = d.get("payload") or {}
    d["deadline_at"] = p.get("deadline_at") if isinstance(p, dict) else None
    d["on_timeout"] = (p.get("on_timeout") or "reject") if d["deadline_at"] else None
    return d


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def deadline_in(seconds: float) -> str:
    """UTC ISO timestamp ``seconds`` from now (a gate's deadline)."""
    return (datetime.now(timezone.utc) + timedelta(seconds=float(seconds))).strftime("%Y-%m-%dT%H:%M:%SZ")


def _publish(etype: str, ap: Dict[str, Any]) -> None:
    try:
        from services import bus
        bus.publish_global("approval", etype, ap)
    except Exception:
        logger.exception("approval bus publish failed")


def create(kind: str, *, title: str, body: str = "", payload: Optional[Dict[str, Any]] = None,
           run_id: Optional[str] = None, step_id: Optional[str] = None,
           trigger_id: Optional[str] = None, deadline_at: Optional[str] = None,
           on_timeout: Optional[str] = None) -> Dict[str, Any]:
    if kind not in KINDS:
        raise ApprovalError(f"kind must be one of {KINDS}")
    payload = dict(payload or {})
    if deadline_at:
        if _parse_iso(deadline_at) is None:
            raise ApprovalError("deadline_at must be an ISO timestamp")
        pol = (on_timeout or "reject").lower()
        if pol not in TIMEOUT_POLICIES:
            raise ApprovalError(f"on_timeout must be one of {tuple(TIMEOUT_POLICIES)}")
        payload.update({"deadline_at": deadline_at, "on_timeout": pol})
    aid = str(uuid.uuid4())
    with _lock:
        conn = connect()
        conn.execute(
            "INSERT INTO approvals (id, kind, run_id, step_id, trigger_id, title, body, payload, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?, 'pending', ?)",
            (aid, kind, run_id, step_id, trigger_id, (title or "")[:500], (body or "")[:BODY_CAP],
             json.dumps(payload, ensure_ascii=False, default=str), now_iso()))
        ap = get(aid)
    _publish("approval.created", ap)
    if deadline_at:
        start_timeout_checker()
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
    if (by or "").strip().lower() == TIMEOUT_BY:
        raise ApprovalError("'timeout' is reserved for expired approvals")
    return _flip(approval_id, status, by=by or "web", note=note, edited_text=edited_text)


def _flip(approval_id: str, status: str, *, by: str, note: Optional[str] = None,
          edited_text: Optional[str] = None) -> Dict[str, Any]:
    """pending → ``status`` atomically, publish, then the kind's handler."""
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


# ── Deadlines ───────────────────────────────────────────────────────────────

def expire(approval_id: str) -> Optional[Dict[str, Any]]:
    """Resolve one pending approval by its ``on_timeout`` policy, whatever its
    deadline (the checker only calls it for overdue rows). Returns the decided
    row, or None when it was no longer pending / has no deadline."""
    ap = get(approval_id)
    if not ap or ap.get("status") != "pending" or not ap.get("deadline_at"):
        return None
    pol = ap.get("on_timeout") or "reject"
    note = f"no decision by {ap['deadline_at']} — on_timeout: {pol}"
    try:
        return _flip(approval_id, TIMEOUT_POLICIES.get(pol, "rejected"), by=TIMEOUT_BY, note=note)
    except AlreadyDecided:
        return None


def expire_due(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Resolve every pending approval whose deadline has passed."""
    now = now or datetime.now(timezone.utc)
    out: List[Dict[str, Any]] = []
    for r in connect().execute("SELECT id, payload FROM approvals WHERE status='pending' "
                               "AND payload LIKE '%deadline_at%'").fetchall():
        try:
            dl = _parse_iso((json.loads(r["payload"] or "{}") or {}).get("deadline_at"))
        except ValueError:
            continue
        if dl is not None and dl <= now:
            try:
                ap = expire(r["id"])
            except Exception:
                logger.exception(f"expiring approval {r['id']} failed")
                continue
            if ap:
                logger.info(f"approval {r['id'][:8]} timed out → {ap['status']}")
                out.append(ap)
    return out


def has_deadlines() -> bool:
    return connect().execute("SELECT 1 FROM approvals WHERE status='pending' AND payload LIKE '%deadline_at%' "
                             "LIMIT 1").fetchone() is not None


def _checker_loop(interval: float) -> None:
    while True:
        try:
            expire_due()
        except Exception:
            logger.exception("approval timeout check failed")
        if _checker_stop.wait(interval):
            return


def start_timeout_checker(interval: Optional[float] = None) -> None:
    """Idempotent: one daemon thread per process. Its first pass runs at once,
    so a deadline that passed while telecode was down resolves on startup."""
    global _checker
    with _lock:
        if _checker is not None and _checker.is_alive():
            return
        _checker_stop.clear()
        _checker = threading.Thread(target=_checker_loop, args=(interval or TIMEOUT_CHECK_SECONDS,),
                                    name="approval-timeouts", daemon=True)
        _checker.start()


def stop_timeout_checker(timeout: float = 5.0) -> None:
    global _checker
    _checker_stop.set()
    t = _checker
    if t is not None:
        t.join(timeout)
    _checker = None


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
