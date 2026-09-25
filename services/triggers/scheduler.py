"""The one scheduler: a daemon thread inside the proxy process.

Every ``triggers.tick_seconds`` (default 15): reconcile running fires, then
fire every active trigger whose ``next_fire_at`` has passed (at most
``triggers.max_fires_per_tick``). Every 2 s: poll file-watch triggers. Every
60 s (and at start): compile every agent's HEARTBEAT.md.

Catch-up: a due time more than ``max(3 × tick, 120 s)`` in the past was missed
(telecode was not running). ``catch_up: skip`` (default) records one skipped
fire and moves on to the next slot; ``once`` fires once now.

HEARTBEAT.md triggers fire on schedule / files only while ``heartbeat.enabled``
is on (their "fire now" always works) — the setting that gated the old
heartbeat loop keeps its meaning.

File watch: the glob is scanned (≤ 5000 files) in the trigger's workspace; the
first scan is a baseline; a change starts a debounce window, and the fire goes
out ``debounce_seconds`` after the last change with the changed paths as its
payload. While a fire of the trigger runs, changes are absorbed into the
baseline, so a fire that edits watched files does not re-trigger itself.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from services.triggers import fire as fire_mod
from services.triggers import schedule as sched
from services.triggers import store

logger = logging.getLogger("telecode.services.triggers.scheduler")

FILE_POLL_SECONDS = 2.0
COMPILE_SECONDS = 60.0
MAX_WATCHED_FILES = 5000

_thread: Optional[threading.Thread] = None
_stop: Optional[threading.Event] = None
_guard = threading.Lock()
# trigger_id → {sig, changed: set, last_change: float}
_file_state: Dict[str, Dict[str, Any]] = {}


def tick_seconds() -> float:
    import config
    return max(2.0, float(config.triggers_tick_seconds()))


def max_fires_per_tick() -> int:
    import config
    return config.triggers_max_fires_per_tick()


def _heartbeat_on() -> bool:
    import config
    return bool(config.heartbeat_enabled())


def _may_fire_unattended(rec: Dict[str, Any]) -> bool:
    return rec.get("source") != "heartbeat" or _heartbeat_on()


def start() -> None:
    """Start the scheduler thread (idempotent): one-time migration, compile
    HEARTBEAT.md, reconcile fires left running by a previous process."""
    global _thread, _stop
    with _guard:
        if _thread is not None and _thread.is_alive():
            return
        try:
            from services.triggers import migrate
            migrate.migrate_once()
        except Exception:
            logger.exception("trigger migration failed")
        try:
            from services.triggers import heartbeat
            heartbeat.compile_all()
        except Exception:
            logger.exception("HEARTBEAT.md compile failed at start")
        _stop = threading.Event()
        _thread = threading.Thread(target=_loop, args=(_stop,), name="trigger-scheduler", daemon=True)
        _thread.start()
    logger.info(f"trigger scheduler started (tick {tick_seconds():.0f}s)")


def stop() -> None:
    global _thread, _stop
    with _guard:
        if _stop is not None:
            _stop.set()
        _thread, _stop = None, None


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def _loop(stop_ev: threading.Event) -> None:
    last_tick = last_compile = 0.0
    first = True
    while not stop_ev.is_set():
        now = time.monotonic()
        try:
            if first or now - last_tick >= tick_seconds():
                last_tick = now
                tick()
            if now - last_compile >= COMPILE_SECONDS:
                last_compile = now
                if not first:
                    from services.triggers import heartbeat
                    heartbeat.compile_all()
            file_tick()
        except Exception:
            logger.exception("trigger scheduler iteration failed (continuing)")
        first = False
        stop_ev.wait(FILE_POLL_SECONDS)


def tick(now=None) -> Dict[str, int]:
    """One scheduling pass (also called by tests)."""
    now = now or sched.utcnow()
    counts = {"reconciled": fire_mod.reconcile_all(), "fired": 0, "skipped": 0}
    budget = max_fires_per_tick()
    missed_after = max(3 * tick_seconds(), 120.0)
    for rec in store.list_all(status="active"):
        if not rec.get("schedule") or not _may_fire_unattended(rec):
            continue
        st = rec.get("state") or {}
        nfa = sched.parse_iso(st.get("next_fire_at"))
        if nfa is None:
            if rec.get("schedule", {}).get("at"):
                continue
            store.mutate(rec["id"], fire_mod.schedule_next)
            continue
        if nfa > now:
            continue
        if (now - nfa).total_seconds() > missed_after and rec.get("catch_up", "skip") == "skip":
            def skip(r, _nfa=nfa):
                fire_mod._skip(r, "schedule", f"missed while telecode was not running (due {sched.to_iso(_nfa)})")
                r.setdefault("state", {})["next_fire_at"] = sched.to_iso(
                    sched.next_fire(r.get("schedule"), now, last_fire=sched.parse_iso(
                        (r.get("state") or {}).get("last_fire_at"))))
                if (r.get("schedule") or {}).get("at"):
                    r["status"] = "disabled"
                    r["state"]["paused_reason"] = "one-off missed"
            store.mutate(rec["id"], skip)
            counts["skipped"] += 1
            continue
        if budget <= 0:
            break
        catch = (now - nfa).total_seconds() > missed_after
        res = fire_mod.fire(rec["id"], source="schedule", now=now, reason="catch-up" if catch else None)
        if res.fired:
            budget -= 1
            counts["fired"] += 1
        elif res.get("fire_id"):
            counts["skipped"] += 1
    return counts


# ── file watch ─────────────────────────────────────────────────────────────

def _watch_dir(rec: Dict[str, Any]) -> Optional[Path]:
    fi = (rec.get("events") or {}).get("file") or {}
    if fi.get("workspace_id"):
        from services.session import session_store
        return session_store._session_dir(fi["workspace_id"])
    # No ensure() here: it rewrites session.json (last_used_at) and this runs every 2 s.
    return fire_mod.workspace_dir(rec if rec.get("session_id") or (rec.get("target") or {}).get("kind") == "job"
                                  else fire_mod.ensure_session(dict(rec)))


def _signature(root: Path, pattern: str) -> Dict[str, Tuple[int, int]]:
    out: Dict[str, Tuple[int, int]] = {}
    if not root.is_dir():
        return out
    for p in root.glob(pattern):
        if len(out) >= MAX_WATCHED_FILES:
            break
        try:
            if p.is_file():
                rel = p.relative_to(root).as_posix()
                if rel.startswith((".telecode/", ".git/")) or rel == "session.json":
                    continue
                s = p.stat()
                out[rel] = (s.st_mtime_ns, s.st_size)
        except OSError:
            continue
    return out


def file_tick(now_mono: Optional[float] = None) -> int:
    """Poll file-watch triggers; returns fires started."""
    now_mono = now_mono if now_mono is not None else time.monotonic()
    fired = 0
    live = set()
    for rec in store.list_all(status="active"):
        fi = (rec.get("events") or {}).get("file") or {}
        if not fi.get("enabled") or not fi.get("glob") or not _may_fire_unattended(rec):
            continue
        tid = rec["id"]
        live.add(tid)
        root = _watch_dir(rec)
        if root is None:
            continue
        sig = _signature(root, fi["glob"])
        stt = _file_state.get(tid)
        if stt is None or stt.get("glob") != fi["glob"] or stt.get("root") != str(root):
            _file_state[tid] = {"sig": sig, "changed": {}, "last_change": None, "glob": fi["glob"], "root": str(root)}
            continue
        running = fire_mod._fire_active(store.last_fire(tid))
        if running:
            stt.update({"sig": sig, "changed": {}, "last_change": None})
            continue
        old = stt["sig"]
        if sig != old:
            for path in set(old) | set(sig):
                if path not in sig:
                    stt["changed"][path] = "deleted"
                elif path not in old:
                    stt["changed"][path] = "added"
                elif old[path] != sig[path]:
                    stt["changed"].setdefault(path, "modified")
            stt["sig"] = sig
            stt["last_change"] = now_mono
            continue
        if stt["changed"] and stt["last_change"] is not None \
                and now_mono - stt["last_change"] >= float(fi.get("debounce_seconds") or 10):
            changed = [{"path": p, "change": c} for p, c in sorted(stt["changed"].items())][:500]
            stt.update({"changed": {}, "last_change": None})
            res = fire_mod.fire(tid, source="file", payload={"glob": fi["glob"], "changed": changed})
            if res.fired:
                fired += 1
    for tid in list(_file_state):
        if tid not in live:
            _file_state.pop(tid, None)
    return fired
