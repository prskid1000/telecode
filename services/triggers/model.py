"""The trigger record: validation / normalisation (create, patch, HEARTBEAT.md).

One record type for everything that starts work on its own::

    {id, name, description, status: active|paused|disabled,
     source: user|heartbeat|routine, source_key, owner_agent_id,
     target: {kind: task|agent_prompt|job, id (job), agent_id, prompt,
              engine, model, is_local, workspace_id},
     schedule: {cron, tz} | {every_seconds} | {at, tz} | null,
     events: {webhook: {enabled, token},
              github: {enabled, secret, events[], branches[], authors[], labels[]},
              file: {enabled, workspace_id, glob, debounce_seconds}},
     session: shared|fresh, session_id, session_namespace,
     active_hours: {start, end, tz, days[]} | null,
     skip_if_empty: {path, heartbeat_section} | null,
     ok_suppression, ok_tokens[], notify, model_override,
     goal: {check_command, features_file, max_fires, max_cost_usd} | null,
     auto_pause_after_failures, catch_up: skip|once, pinned,
     permission_mode, effort, task_timeout_seconds, preface, outputs_only,
     state: {...counters and scheduling state, owned by the scheduler...},
     created_at, updated_at}
"""

from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional

from services.triggers import schedule as sched

TARGET_KINDS = ("task", "agent_prompt", "job")
SESSIONS = ("shared", "fresh")
STATUSES = ("active", "paused", "disabled")
SOURCES = ("user", "heartbeat", "routine")
CATCH_UP = ("skip", "once")
# Claude --permission-mode values, plus "skip" (= --dangerously-skip-permissions).
# "ask" (P5): Claude's permission prompts go to telecode's approve_tool → approvals inbox + Telegram.
PERMISSION_MODES = ("auto", "ask", "acceptEdits", "dontAsk", "plan", "manual", "bypassPermissions", "skip")
DEFAULT_PERMISSION_MODE = "auto"
DEFAULT_OK_TOKENS = ["HEARTBEAT_OK", "NO_REPLY"]
NAME_MAX = 200
PROMPT_MAX = 256 * 1024

EDITABLE = {"name", "description", "status", "target", "schedule", "events", "session", "active_hours",
            "skip_if_empty", "ok_suppression", "ok_tokens", "notify", "model_override", "goal",
            "auto_pause_after_failures", "catch_up", "pinned", "permission_mode", "task_timeout_seconds",
            "preface", "outputs_only", "effort"}


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _str_list(v: Any, name: str, cap: int = 50) -> List[str]:
    if v in (None, ""):
        return []
    if isinstance(v, str):
        v = [x.strip() for x in v.split(",")]
    if not isinstance(v, list):
        raise ValueError(f"{name} must be a list of strings")
    return [str(x).strip() for x in v if str(x).strip()][:cap]


def _engine(v: Any, task_type: Any = None) -> str:
    from services.task.engine_map import ENGINE_TO_TASK_TYPE, supported_engines
    e = str(v or "").strip().lower()
    if not e and task_type:
        tt = str(task_type).strip().upper()
        e = next((k for k, t in ENGINE_TO_TASK_TYPE.items() if t == tt), str(task_type).strip().lower())
    e = e or "claude_code"
    if e not in supported_engines():
        raise ValueError(f"engine must be one of {supported_engines()}, got {v or task_type!r}")
    return e


def _int(v: Any, default: int, lo: int, hi: int, name: str) -> int:
    if v is None or v == "":
        return default
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer") from None
    if n < lo or n > hi:
        raise ValueError(f"{name} must be {lo}..{hi}")
    return n


def normalize_target(t: Any) -> Dict[str, Any]:
    if not isinstance(t, dict):
        raise ValueError("target must be an object {kind: task|agent_prompt|job, ...}")
    kind = str(t.get("kind") or "").strip().lower()
    if kind not in TARGET_KINDS:
        raise ValueError(f"target.kind must be one of {TARGET_KINDS}")
    out: Dict[str, Any] = {"kind": kind}
    if kind == "job":
        jid = str(t.get("id") or t.get("job_id") or "").strip()
        from services.job.job_manager import get_job_manager
        if not jid or not get_job_manager().get_job(jid):
            raise ValueError("target.id must be an existing job id")
        out["id"] = jid
        out["engine"] = _engine(t.get("engine")) if t.get("engine") else ""
    else:
        prompt = t.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("target.prompt is required")
        out["prompt"] = prompt[:PROMPT_MAX]
        out["engine"] = _engine(t.get("engine"), t.get("task_type"))
        if kind == "agent_prompt":
            aid = str(t.get("agent_id") or "").strip()
            from services.agent.agent_manager import get_agent_manager
            if not aid or not get_agent_manager().get_agent(aid):
                raise ValueError("target.agent_id must be an existing agent id")
            out["agent_id"] = aid
            ws = str(t.get("workspace_id") or "").strip()
            out["workspace_id"] = ws or None
    out["model"] = str(t.get("model") or "").strip()
    il = t.get("is_local")
    out["is_local"] = None if (kind == "job" and il in (None, "")) else bool(il)
    return out


def normalize_events(e: Any, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    e = e if isinstance(e, dict) else {}
    old = existing or {}
    wh_in = e.get("webhook") if isinstance(e.get("webhook"), dict) else {}
    wh_old = old.get("webhook") or {}
    wh = {"enabled": bool(wh_in.get("enabled", wh_old.get("enabled", False))),
          "token": wh_old.get("token") or new_token()}
    if wh_in.get("rotate"):
        wh["token"] = new_token()
    gh_in = e.get("github") if isinstance(e.get("github"), dict) else {}
    gh_old = old.get("github") or {}
    gh = {"enabled": bool(gh_in.get("enabled", gh_old.get("enabled", False))),
          "secret": str(gh_in["secret"]).strip() if gh_in.get("secret") not in (None, "") else gh_old.get("secret") or "",
          "events": _str_list(gh_in.get("events", gh_old.get("events")), "github.events"),
          "branches": _str_list(gh_in.get("branches", gh_old.get("branches")), "github.branches"),
          "authors": _str_list(gh_in.get("authors", gh_old.get("authors")), "github.authors"),
          "labels": _str_list(gh_in.get("labels", gh_old.get("labels")), "github.labels")}
    if gh["enabled"] and len(gh["secret"]) < 8:
        raise ValueError("github.secret (at least 8 characters) is required for a GitHub trigger — "
                         "the same secret goes in the repository's webhook settings")
    fi_in = e.get("file") if isinstance(e.get("file"), dict) else {}
    fi_old = old.get("file") or {}
    fi = {"enabled": bool(fi_in.get("enabled", fi_old.get("enabled", False))),
          "workspace_id": str(fi_in.get("workspace_id", fi_old.get("workspace_id")) or "").strip() or None,
          "glob": str(fi_in.get("glob", fi_old.get("glob")) or "").strip()[:500],
          "debounce_seconds": _int(fi_in.get("debounce_seconds", fi_old.get("debounce_seconds")), 10, 2, 3600,
                                   "file.debounce_seconds")}
    if fi["enabled"]:
        g = fi["glob"].replace("\\", "/")
        if not g or g.startswith("/") or ".." in g.split("/") or ":" in g:
            raise ValueError("file.glob must be a relative pattern inside the workspace, e.g. inbox/*.md")
        fi["glob"] = g
    return {"webhook": wh, "github": gh, "file": fi}


def normalize_goal(g: Any) -> Optional[Dict[str, Any]]:
    if not g:
        return None
    if not isinstance(g, dict):
        raise ValueError("goal must be an object {check_command, features_file, max_fires, max_cost_usd}")
    out = {"check_command": str(g.get("check_command") or "").strip()[:4000],
           "features_file": str(g.get("features_file") or "").strip()[:500],
           "max_fires": _int(g.get("max_fires"), 0, 0, 1_000_000, "goal.max_fires"),
           "max_cost_usd": None}
    if g.get("max_cost_usd") not in (None, "", 0):
        try:
            out["max_cost_usd"] = round(float(g["max_cost_usd"]), 6)
        except (TypeError, ValueError):
            raise ValueError("goal.max_cost_usd must be a number") from None
        if out["max_cost_usd"] <= 0:
            out["max_cost_usd"] = None
    ff = out["features_file"].replace("\\", "/")
    if ff and (ff.startswith("/") or ".." in ff.split("/") or ":" in ff):
        raise ValueError("goal.features_file must be a relative path in the workspace")
    out["features_file"] = ff
    if not any([out["check_command"], out["features_file"], out["max_fires"], out["max_cost_usd"]]):
        return None
    return out


def normalize_skip(s: Any) -> Optional[Dict[str, Any]]:
    if not s:
        return None
    if isinstance(s, str):
        s = {"path": s}
    if not isinstance(s, dict):
        raise ValueError("skip_if_empty must be {path} or {heartbeat_section}")
    path = str(s.get("path") or "").strip().replace("\\", "/")
    sec = str(s.get("heartbeat_section") or s.get("section") or "").strip()
    if path and (path.startswith("/") or ".." in path.split("/") or ":" in path):
        raise ValueError("skip_if_empty.path must be a relative path in the workspace")
    if not path and not sec:
        return None
    return {"path": path, "heartbeat_section": sec}


def normalize(body: Dict[str, Any], existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Full record from a create body, or ``existing`` patched with ``body``.
    Raises ValueError with a message fit for the API caller."""
    if not isinstance(body, dict):
        raise ValueError("body must be an object")
    if existing is not None:
        unknown = set(body) - EDITABLE - {"id", "state", "source", "source_key", "owner_agent_id", "created_at",
                                          "updated_at", "session_id", "session_namespace"}
        if unknown:
            raise ValueError(f"unknown trigger fields: {sorted(unknown)}")
    cur = dict(existing or {})
    get = lambda k, d=None: body[k] if k in body else cur.get(k, d)  # noqa: E731

    name = get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required")
    rec: Dict[str, Any] = {k: v for k, v in cur.items()}
    rec["name"] = name.strip()[:NAME_MAX]
    rec["description"] = (get("description") or "").strip()[:2000] or None
    status = str(get("status") or "active")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    rec["status"] = status
    rec["target"] = normalize_target(get("target"))
    rec["schedule"] = sched.normalize_schedule(get("schedule"))
    rec["events"] = normalize_events(body.get("events") if "events" in body else cur.get("events"),
                                     cur.get("events"))
    session = str(get("session") or ("fresh" if rec["target"]["kind"] == "agent_prompt" and
                                     not rec["target"].get("workspace_id") and not existing else "shared"))
    if session not in SESSIONS:
        raise ValueError(f"session must be one of {SESSIONS}")
    rec["session"] = session
    rec["active_hours"] = sched.normalize_active_hours(get("active_hours"))
    rec["skip_if_empty"] = normalize_skip(get("skip_if_empty"))
    rec["ok_suppression"] = bool(get("ok_suppression", True))
    toks = _str_list(get("ok_tokens"), "ok_tokens", 10) or list(DEFAULT_OK_TOKENS)
    rec["ok_tokens"] = [t[:64] for t in toks]
    rec["notify"] = bool(get("notify", False))
    rec["model_override"] = str(get("model_override") or "").strip()[:200]
    rec["goal"] = normalize_goal(get("goal"))
    rec["auto_pause_after_failures"] = _int(get("auto_pause_after_failures"), 3, 0, 1000,
                                            "auto_pause_after_failures")
    catch = str(get("catch_up") or "skip")
    if catch not in CATCH_UP:
        raise ValueError(f"catch_up must be one of {CATCH_UP}")
    rec["catch_up"] = catch
    rec["pinned"] = (get("pinned") or "").strip()[:16000]
    pm = str(get("permission_mode") or DEFAULT_PERMISSION_MODE)
    if pm not in PERMISSION_MODES:
        raise ValueError(f"permission_mode must be one of {PERMISSION_MODES}")
    rec["permission_mode"] = pm
    from services.engine.types import normalize_effort
    rec["effort"] = normalize_effort(get("effort")) or None
    rec["task_timeout_seconds"] = _int(get("task_timeout_seconds"), 1800, 30, 7 * 86400, "task_timeout_seconds")
    rec["preface"] = bool(get("preface", True))
    rec["outputs_only"] = bool(get("outputs_only", False))
    if not rec["schedule"] and not any(rec["events"][k]["enabled"] for k in ("webhook", "github", "file")):
        # Manual-only triggers are fine (fire now), but say so in the record.
        rec["manual_only"] = True
    else:
        rec.pop("manual_only", None)
    rec.setdefault("source", body.get("source") or "user")
    rec.setdefault("state", {})
    return rec


def public(rec: Dict[str, Any], reveal: bool = False) -> Dict[str, Any]:
    """API view: secrets masked unless ``reveal`` (the detail route)."""
    out = dict(rec)
    ev = {k: dict(v) for k, v in (rec.get("events") or {}).items()}
    if not reveal:
        if ev.get("webhook", {}).get("token"):
            ev["webhook"]["token"] = "…" + ev["webhook"]["token"][-4:]
        if ev.get("github", {}).get("secret"):
            ev["github"]["secret"] = "…" + ev["github"]["secret"][-2:]
    out["events"] = ev
    return out
