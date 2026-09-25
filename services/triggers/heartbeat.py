"""HEARTBEAT.md — an authoring format that compiles to triggers.

HEARTBEAT.md is markdown with one or more ```yaml fenced blocks, each a YAML
list of entries. Text outside the fences is notes for the agent (and the
place ``skip_if_empty.heartbeat_section`` looks at). Each entry::

    name        str, unique in the file                      (required)
    prompt      str                                          (required)
    cron | every | at                                        (one required)
                cron: 5-field cron · every: 900 / 15m / 2h / 1d · at: ISO time (one-off)
    tz          IANA zone for cron / at / active_hours (default UTC)
    workspace   ephemeral (default: a fresh copy per fire) | persistent (+ workspace_id)
    engine      claude_code (default) | codex | antigravity
    model       engine model id / llama model; blank = the agent's default
    is_local    bool
    enabled     bool (default true) — false compiles to a disabled trigger
    active_hours   "09:00-18:00" or {start, end, days, tz}
    skip_if_empty  a workspace path, or {section: "<HEARTBEAT.md heading>"}
    ok_suppression bool (default true) · notify bool · catch_up skip|once
    goal        {check, features, max_fires, max_cost}
    auto_pause_after_failures int · pinned str · permission_mode · timeout (s)

:func:`compile_agent` upserts one trigger per entry (``source: heartbeat``,
``source_key: hb:<agent_id>:<name>``), keeping each trigger's state and a
pause set from the UI; entries removed from the file delete their trigger
(fire history stays). It runs on save, on the reconcile route, and every
minute from the scheduler (so edits made on disk are picked up).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import yaml

from services.triggers import model, schedule as sched, store

logger = logging.getLogger("telecode.services.triggers.heartbeat")

VALID_WORKSPACE_MODES = ("ephemeral", "persistent")
_FENCE_RE = re.compile(r"```yaml\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
_KNOWN = {"name", "prompt", "cron", "every", "at", "tz", "workspace", "workspace_id", "engine", "model",
          "is_local", "enabled", "active_hours", "skip_if_empty", "ok_suppression", "notify", "catch_up",
          "goal", "auto_pause_after_failures", "pinned", "permission_mode", "timeout", "outputs_only"}


def _entry_to_body(agent_id: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    """HEARTBEAT.md entry → trigger create body (validated by model.normalize)."""
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("missing or empty 'name'")
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("missing 'prompt'")
    unknown = set(raw) - _KNOWN
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(sorted(unknown))}")
    given = [k for k in ("cron", "every", "at") if raw.get(k) not in (None, "")]
    if not given:
        raise ValueError("needs one of cron, every, at")
    if len(given) > 1:
        raise ValueError(f"use only one of cron / every / at (got {', '.join(given)})")
    tz = str(raw.get("tz") or "UTC")
    schedule: Dict[str, Any] = {"tz": tz}
    if raw.get("cron") not in (None, ""):
        schedule["cron"] = str(raw["cron"]).strip()
    elif raw.get("every") not in (None, ""):
        schedule = {"every_seconds": sched.parse_every(raw["every"])}
    else:
        at = raw["at"]
        schedule["at"] = at.isoformat() if hasattr(at, "isoformat") else str(at)
    ws = str(raw.get("workspace") or "ephemeral").strip()
    if ws not in VALID_WORKSPACE_MODES:
        raise ValueError(f"workspace must be one of {VALID_WORKSPACE_MODES}, got '{ws}'")
    wsid = raw.get("workspace_id")
    if ws == "persistent":
        if not wsid or not isinstance(wsid, str):
            raise ValueError("workspace_id required when workspace == 'persistent'")
        from services.session import session_store
        if not session_store.exists(wsid):
            raise ValueError(f"workspace_id {wsid!r} does not exist")
    for key, typ in (("is_local", bool), ("enabled", bool), ("ok_suppression", bool), ("notify", bool),
                     ("outputs_only", bool)):
        if key in raw and not isinstance(raw[key], typ):
            raise ValueError(f"{key} must be true or false")
    model_v = raw.get("model")
    if model_v is not None and not isinstance(model_v, str):
        raise ValueError("model must be a string")
    skip = raw.get("skip_if_empty")
    if isinstance(skip, dict) and "section" in skip:
        skip = {"heartbeat_section": skip.get("section"), "path": skip.get("path")}
    goal = raw.get("goal")
    if isinstance(goal, dict):
        goal = {"check_command": goal.get("check") or goal.get("check_command"),
                "features_file": goal.get("features") or goal.get("features_file"),
                "max_fires": goal.get("max_fires"), "max_cost_usd": goal.get("max_cost") or goal.get("max_cost_usd")}
    ah = raw.get("active_hours")
    if isinstance(ah, dict) and "tz" not in ah:
        ah = {**ah, "tz": tz}
    elif isinstance(ah, str):
        ah = {**(sched.normalize_active_hours(ah) or {}), "tz": tz}
    return {
        "name": name,
        "target": {"kind": "agent_prompt", "agent_id": agent_id, "prompt": prompt.rstrip(),
                   "engine": str(raw.get("engine") or "claude_code").strip().lower(),
                   "model": (model_v or "").strip(), "is_local": bool(raw.get("is_local", False)),
                   "workspace_id": wsid if ws == "persistent" else None},
        "schedule": schedule,
        "session": "shared" if ws == "persistent" else "fresh",
        "active_hours": ah,
        "skip_if_empty": skip,
        "ok_suppression": raw.get("ok_suppression", True),
        "notify": raw.get("notify", False),
        "catch_up": raw.get("catch_up") or "skip",
        "goal": goal,
        "auto_pause_after_failures": raw.get("auto_pause_after_failures"),
        "pinned": raw.get("pinned") or "",
        "permission_mode": raw.get("permission_mode") or model.DEFAULT_PERMISSION_MODE,
        "task_timeout_seconds": raw.get("timeout") or 1800,
        "outputs_only": raw.get("outputs_only", False),
        "_enabled": raw.get("enabled", True),
    }


def parse(text: str, agent_id: str = "_") -> Dict[str, Any]:
    """{ok, entries: [{name, body, next_fires}], errors: [{block, index, name?, msg}]}."""
    entries: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for bidx, m in enumerate(_FENCE_RE.finditer(text or "")):
        try:
            data = yaml.safe_load(m.group(1))
        except yaml.YAMLError as exc:
            errors.append({"block": bidx, "msg": f"yaml parse error: {exc}"})
            continue
        if data is None:
            continue
        if not isinstance(data, list):
            errors.append({"block": bidx, "msg": "yaml block must be a list of entries"})
            continue
        for iidx, raw in enumerate(data):
            if not isinstance(raw, dict):
                errors.append({"block": bidx, "index": iidx, "msg": "entry must be a mapping"})
                continue
            nm = str(raw.get("name") or "").strip() or None
            try:
                body = _entry_to_body(agent_id, raw)
                check = {k: v for k, v in body.items() if not k.startswith("_")}
                if agent_id != "_":
                    model.normalize(check)
                else:
                    sched.normalize_schedule(check["schedule"])
                    sched.normalize_active_hours(check.get("active_hours"))
            except (ValueError, TypeError) as exc:
                errors.append({"block": bidx, "index": iidx, **({"name": nm} if nm else {}), "msg": str(exc)})
                continue
            if nm in seen:
                errors.append({"block": bidx, "index": iidx, "name": nm,
                               "msg": f"duplicate name (already defined in block {seen[nm]})"})
                continue
            seen[nm] = bidx
            try:
                nxt = sched.upcoming(sched.normalize_schedule(body["schedule"]), 3,
                                     active_hours=sched.normalize_active_hours(body.get("active_hours")))
            except Exception:
                nxt = []
            entries.append({"name": nm, "enabled": bool(body["_enabled"]), "body": body, "next_fires": nxt,
                            "schedule": body["schedule"], "engine": body["target"]["engine"],
                            "session": body["session"]})
    return {"ok": not errors, "entries": entries, "errors": errors}


def source_key(agent_id: str, name: str) -> str:
    return f"hb:{agent_id}:{name}"


def compile_agent(agent_id: str, text: Optional[str] = None) -> Dict[str, Any]:
    """Upsert the agent's HEARTBEAT.md entries as triggers. Returns
    {created, updated, deleted, errors, triggers: [ids]}."""
    from services.agent.agent_manager import get_agent_manager
    from services.triggers import fire as fire_mod
    mgr = get_agent_manager()
    if not mgr.get_agent(agent_id):
        return {"created": 0, "updated": 0, "deleted": 0, "errors": [{"msg": "agent not found"}], "triggers": []}
    if text is None:
        text = mgr.get_internal_files(agent_id).get("HEARTBEAT.md", "") or ""
    parsed = parse(text, agent_id)
    created = updated = deleted = 0
    keep = set()
    ids = []
    for e in parsed["entries"]:
        key = source_key(agent_id, e["name"])
        keep.add(key)
        body = {k: v for k, v in e["body"].items() if not k.startswith("_")}
        with store.lock_for(key):
            cur = store.get_by_source_key(key)
            try:
                if cur is None:
                    rec = model.normalize({**body, "status": "active" if e["enabled"] else "disabled"})
                    rec.update({"source": "heartbeat", "source_key": key, "owner_agent_id": agent_id})
                    fire_mod.schedule_next(rec)
                    rec = store.save(rec)
                    created += 1
                    ids.append(rec["id"])
                    continue
                status = cur.get("status")
                if not e["enabled"]:
                    status = "disabled"
                elif status == "disabled":
                    status = "active"
                rec = model.normalize({**body, "status": status}, existing=cur)
                spec_changed = any(rec.get(k) != cur.get(k) for k in model.EDITABLE if k != "status") \
                    or status != cur.get("status")
                if not spec_changed:
                    ids.append(cur["id"])
                    continue
                if rec.get("schedule") != cur.get("schedule") or (status == "active" and cur.get("status") != "active"):
                    fire_mod.schedule_next(rec)
                if status != "active":
                    rec.setdefault("state", {})["next_fire_at"] = None
                store.save(rec)
                updated += 1
                ids.append(rec["id"])
            except ValueError as exc:
                parsed["errors"].append({"name": e["name"], "msg": str(exc)})
    for rec in store.list_all(source="heartbeat"):
        if rec.get("owner_agent_id") == agent_id and rec.get("source_key") not in keep:
            store.delete(rec["id"])
            deleted += 1
    return {"created": created, "updated": updated, "deleted": deleted, "errors": parsed["errors"], "triggers": ids}


def compile_all() -> List[Dict[str, Any]]:
    """Every agent's HEARTBEAT.md; heartbeat triggers of deleted agents go."""
    from services.agent.agent_manager import get_agent_manager
    agents = {a["id"]: a for a in get_agent_manager().list_agents()}
    out = []
    for aid, a in agents.items():
        try:
            out.append({"agent_id": aid, "name": a.get("name"), **compile_agent(aid)})
        except Exception as exc:
            logger.exception(f"HEARTBEAT.md compile failed for {aid}")
            out.append({"agent_id": aid, "errors": [{"msg": str(exc)}]})
    for rec in store.list_all(source="heartbeat"):
        if rec.get("owner_agent_id") not in agents:
            store.delete(rec["id"])
    return out
