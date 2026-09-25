"""CRUD + actions for the REST routes (``proxy/api_triggers.py``)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from services.triggers import fire as fire_mod
from services.triggers import model, schedule as sched, store

logger = logging.getLogger("telecode.services.triggers.service")

HEARTBEAT_EDITABLE = {"status"}


class NotFound(LookupError):
    pass


def _decorate(rec: Dict[str, Any], reveal: bool = False) -> Dict[str, Any]:
    out = model.public(rec, reveal=reveal)
    try:
        out["upcoming"] = []
        if rec.get("status") == "active" and rec.get("schedule"):
            first = sched.parse_iso((rec.get("state") or {}).get("next_fire_at"))
            ah = rec.get("active_hours")
            if first is not None:
                out["upcoming"] = ([sched.to_iso(first)] if sched.in_active_hours(ah, first) else []) \
                    + sched.upcoming(rec["schedule"], 3, after=first, active_hours=ah)
                out["upcoming"] = out["upcoming"][:3]
            else:
                out["upcoming"] = sched.upcoming(rec["schedule"], 3, active_hours=ah)
    except Exception:
        out["upcoming"] = []
    if rec.get("source") == "heartbeat":
        import config
        out["heartbeat_enabled"] = bool(config.heartbeat_enabled())
    return out


def create(body: Dict[str, Any]) -> Dict[str, Any]:
    rec = model.normalize(body)
    rec["source"] = "user"
    rec.pop("source_key", None)
    rec = store.save(rec)                      # id assigned
    fire_mod.ensure_session(rec)
    if rec["status"] == "active":
        fire_mod.schedule_next(rec)
    return _decorate(store.save(rec), reveal=True)


def get(trigger_id: str, reveal: bool = True) -> Dict[str, Any]:
    rec = store.get(trigger_id)
    if rec is None:
        raise NotFound("trigger not found")
    last = store.last_fire(trigger_id)
    if last and last.get("status") == "running" and fire_mod.reconcile_fire(last):
        rec = store.get(trigger_id) or rec
    return _decorate(rec, reveal=reveal)


def list_triggers(**filters: Any) -> List[Dict[str, Any]]:
    try:
        fire_mod.reconcile_all()
    except Exception:
        logger.exception("inline fire reconcile failed")
    return [_decorate(r) for r in store.list_all(**{k: v for k, v in filters.items() if v})]


def patch(trigger_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    with store.lock_for(trigger_id):
        cur = store.get(trigger_id)
        if cur is None:
            raise NotFound("trigger not found")
        if cur.get("source") == "heartbeat" and set(body) - HEARTBEAT_EDITABLE:
            raise ValueError("this trigger is compiled from the agent's HEARTBEAT.md — edit it there "
                             "(only pause / resume work here)")
        rec = model.normalize(body, existing=cur)
        if rec["status"] != "active":
            rec.setdefault("state", {})["next_fire_at"] = None
            if rec["status"] != cur.get("status"):
                rec["state"]["paused_reason"] = f"{rec['status']} by user"
        elif rec.get("schedule") != cur.get("schedule") or cur.get("status") != "active":
            rec["state"].pop("paused_reason", None)
            if cur.get("status") != "active":
                rec["state"]["consecutive_failures"] = 0
            fire_mod.schedule_next(rec)
        fire_mod.ensure_session(rec)
        return _decorate(store.save(rec), reveal=True)


def set_status(trigger_id: str, status: str) -> Dict[str, Any]:
    return patch(trigger_id, {"status": status})


def delete(trigger_id: str, delete_session: bool = False) -> bool:
    rec = store.get(trigger_id)
    if rec is None:
        return False
    if rec.get("source") == "heartbeat":
        raise ValueError("this trigger is compiled from the agent's HEARTBEAT.md — remove the entry there")
    if delete_session and rec.get("session_id") and not (rec.get("target") or {}).get("workspace_id"):
        try:
            from services.session import session_store
            session_store.delete(rec["session_id"], namespace=rec.get("session_namespace"))
        except Exception:
            logger.exception(f"could not delete the session of trigger {trigger_id}")
    return store.delete(trigger_id)


def rotate_token(trigger_id: str) -> Dict[str, Any]:
    def fn(rec):
        rec.setdefault("events", {}).setdefault("webhook", {})["token"] = model.new_token()
    rec = store.mutate(trigger_id, fn)
    if rec is None:
        raise NotFound("trigger not found")
    return _decorate(rec, reveal=True)


def fires(trigger_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    if store.get(trigger_id) is None:
        raise NotFound("trigger not found")
    last = store.last_fire(trigger_id)
    if last and last.get("status") == "running":
        fire_mod.reconcile_fire(last)
    return store.list_fires(trigger_id, limit=limit)


def run_now(trigger_id: str) -> Dict[str, Any]:
    if store.get(trigger_id) is None:
        raise NotFound("trigger not found")
    return dict(fire_mod.fire(trigger_id, source="manual"))
