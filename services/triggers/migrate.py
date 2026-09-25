"""One-time migration of the P0–P2 schedulers into triggers (per database).

* ``data/routines/*.json`` → ``task`` triggers (same prompt, engine, interval,
  permanent session, counters, timeout, outputs_only; ``cancelled`` →
  ``disabled``). The last fire's task becomes the first history row. The folder
  is renamed ``data/routines.migrated`` so nothing reads it again.
* HEARTBEAT.md entries → ``agent_prompt`` triggers (:func:`heartbeat.compile_all`),
  then ``data/heartbeat-state.json`` supplies each one's last fire time (so an
  entry that fired an hour before the upgrade does not fire again at once);
  the file is renamed ``heartbeat-state.json.migrated``.
* ``kind: heartbeat`` jobs (the old HEARTBEAT.md mirror) are moved to
  ``data/jobs/_migrated_heartbeat/``.

Guarded by ``meta.triggers_migrated``; everything moved is kept on disk.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict

from services.db.core import get_meta, now_iso, set_meta

logger = logging.getLogger("telecode.services.triggers.migrate")

FLAG = "triggers_migrated"


def _data() -> Path:
    import config
    return Path(config._settings_dir()) / "data"


def _routine_to_trigger(r: Dict[str, Any]) -> Dict[str, Any]:
    from services.triggers import model, schedule as sched, store
    status = {"active": "active", "paused": "paused"}.get(r.get("status"), "disabled")
    body = {
        "name": r.get("name") or f"routine {str(r.get('routine_id'))[:8]}",
        "description": r.get("description"),
        "status": status,
        "target": {"kind": "task", "prompt": r.get("prompt") or "(empty)", "task_type": r.get("task_type"),
                   "is_local": bool(r.get("is_local"))},
        "schedule": {"every_seconds": max(sched.MIN_INTERVAL_SECONDS,
                                          int((r.get("schedule") or {}).get("every_seconds") or 3600))},
        "session": "shared",
        "task_timeout_seconds": int(r.get("task_timeout_seconds") or 1800),
        "outputs_only": bool(r.get("outputs_only")),
        "ok_suppression": False,
    }
    rec = model.normalize(body)
    rec.update({"source": "routine", "session_id": r.get("session_id"),
                "session_namespace": r.get("session_namespace"),
                "migrated_from": {"routine_id": r.get("routine_id")}})
    rec["state"] = {
        "total_fires": int(r.get("total_runs") or 0), "skipped_fires": int(r.get("skipped_runs") or 0),
        "last_fire_at": r.get("last_fire_at"),
        "next_fire_at": r.get("next_fire_at") if status == "active" else None,
        "last_status": r.get("last_completion_status"), "last_error": r.get("last_error"),
    }
    rec = store.save(rec)
    if r.get("last_task_id"):
        st = r.get("last_completion_status") or "running"
        st = st if st in store.FIRE_STATUSES else ("interrupted" if st != "running" else "running")
        store.add_fire(rec["id"], source="schedule", status=st, task_id=r["last_task_id"],
                       reason="imported from the routine", collapse_skips=False)
    return rec


def migrate_once() -> Dict[str, int]:
    if get_meta(FLAG):
        return {}
    out = {"routines": 0, "heartbeat_jobs": 0, "heartbeat_state": 0}
    data = _data()

    rdir = data / "routines"
    if rdir.is_dir():
        for p in sorted(rdir.glob("*.json")):
            try:
                _routine_to_trigger(json.loads(p.read_text(encoding="utf-8")))
                out["routines"] += 1
            except Exception:
                logger.exception(f"routine migration failed for {p.name} (file kept)")
        try:
            dest = data / "routines.migrated"
            if dest.exists():
                dest = data / f"routines.migrated-{now_iso().replace(':', '')}"
            rdir.rename(dest)
        except OSError:
            logger.exception("could not rename data/routines after migrating it")

    jobs = data / "jobs"
    if jobs.is_dir():
        moved = jobs / "_migrated_heartbeat"
        for p in sorted(jobs.glob("*.json")):
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if j.get("kind") != "heartbeat":
                continue
            moved.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(p), str(moved / p.name))
                d = jobs / p.stem
                if d.is_dir():
                    shutil.move(str(d), str(moved / p.stem))
                out["heartbeat_jobs"] += 1
            except OSError:
                logger.exception(f"could not move heartbeat job {p.name}")

    from services.triggers import heartbeat, schedule as sched, store
    heartbeat.compile_all()
    hb_state = data / "heartbeat-state.json"
    if hb_state.is_file():
        try:
            state = json.loads(hb_state.read_text(encoding="utf-8"))
        except Exception:
            state = {}
        for key, cur in (state or {}).items():
            if not isinstance(cur, dict) or ":" not in key or not cur.get("last_run"):
                continue
            agent_id, name = key.split(":", 1)
            rec = store.get_by_source_key(heartbeat.source_key(agent_id, name))
            if not rec:
                continue

            def apply(r, _cur=cur):
                st = r.setdefault("state", {})
                st["last_fire_at"] = _cur["last_run"]
                st["last_status"] = _cur.get("last_status")
                if r.get("status") == "active":
                    st["next_fire_at"] = sched.to_iso(sched.next_fire(
                        r.get("schedule"), sched.utcnow(), last_fire=sched.parse_iso(_cur["last_run"])))
            store.mutate(rec["id"], apply)
            out["heartbeat_state"] += 1
        try:
            hb_state.rename(hb_state.with_name("heartbeat-state.json.migrated"))
        except OSError:
            pass

    set_meta(FLAG, now_iso())
    if any(out.values()):
        logger.info(f"migrated to triggers: {out}")
    return out
