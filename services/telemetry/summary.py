"""Dashboard queries over the telemetry tables + the run store.

* :func:`summary` — cost / tokens by day and by agent | job | trigger | engine |
  model, failure rates, cache-read ratio, top tools, run outcomes (process ok vs
  verdict), what the CLIs exported over OTLP.
* :func:`timeline` — one run's phases → steps → attempts / workers / iterations
  on a time axis with cost per step (the Gantt), plus the OTLP cost per step as
  a cross-check of the stream-parsed usage.
* :func:`trigger_passk` — pass^k over a trigger's last k fires.

The unit of work is the ``invoke_agent`` span (one per engine run: step
attempt, map worker, loop iteration / grader, Task-mode task, design turn), so
Task mode and Team mode are counted the same way. Tokens: ``input_tokens``
includes cache reads and writes (the prompt as the model saw it), so
``cache_read_ratio = cache_read_tokens / input_tokens``.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.db.core import connect
from services.telemetry import store
from services.telemetry.spans import iso_ms

GROUPS = {"agent": "agent_id", "job": "job_id", "trigger": "trigger_id", "engine": "engine", "model": "model"}
_REL = re.compile(r"^(\d+)\s*([mhdw])$")
DEFAULT_WINDOW_DAYS = 14


def parse_time(value: Optional[str], default_ms: Optional[int] = None) -> Optional[int]:
    """``7d`` / ``24h`` / ``90m`` / ``2w`` (relative to now), epoch ms, or ISO-8601."""
    if value in (None, ""):
        return default_ms
    v = str(value).strip()
    m = _REL.match(v)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        secs = n * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
        return int((time.time() - secs) * 1000)
    if v.isdigit():
        return int(v)
    ms = iso_ms(v)
    if ms is None:
        raise ValueError(f"cannot parse time {value!r} (use 7d, 24h, epoch ms or ISO-8601)")
    return ms


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ratio(a: float, b: float) -> Optional[float]:
    return round(a / b, 4) if b else None


def _labels(group: str, keys: List[Optional[str]]) -> Dict[Optional[str], str]:
    out: Dict[Optional[str], str] = {None: {"agent": "(no agent)", "job": "(no job — Task mode)",
                                            "trigger": "(not triggered)", "engine": "(unknown)",
                                            "model": "(default model)"}[group]}
    try:
        if group == "agent":
            from services.agent.agent_manager import get_agent_manager
            m = get_agent_manager()
            for k in keys:
                if k:
                    a = m.get_agent(k)
                    out[k] = (a or {}).get("name") or f"(deleted agent {k[:8]})"
        elif group == "job":
            from services.job.job_manager import get_job_manager
            m = get_job_manager()
            for k in keys:
                if k:
                    j = m.get_job(k)
                    out[k] = (j or {}).get("title") or f"(deleted job {k[:8]})"
        elif group == "trigger":
            for k in keys:
                if k:
                    r = connect().execute("SELECT name FROM triggers WHERE id=?", (k,)).fetchone()
                    out[k] = (r[0] if r else None) or f"(deleted trigger {k[:8]})"
        elif group == "engine":
            names = {"claude_code": "Claude Code", "codex": "Codex", "antigravity": "Antigravity"}
            for k in keys:
                if k:
                    out[k] = names.get(k, k)
    except Exception:
        pass
    for k in keys:
        out.setdefault(k, k or out[None])
    return out


_AGG = ("COUNT(*) AS attempts, SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors, "
        "SUM(CASE WHEN end_ms IS NULL THEN 1 ELSE 0 END) AS running, "
        "SUM(cost_usd) AS cost_usd, SUM(CASE WHEN cost_usd IS NULL AND end_ms IS NOT NULL THEN 1 ELSE 0 END) AS no_cost, "
        "SUM(COALESCE(input_tokens,0)) AS input_tokens, SUM(COALESCE(output_tokens,0)) AS output_tokens, "
        "SUM(COALESCE(cache_read_tokens,0)) AS cache_read_tokens, "
        "SUM(COALESCE(cache_write_tokens,0)) AS cache_write_tokens, AVG(duration_ms) AS avg_duration_ms")


def _agg_row(r) -> Dict[str, Any]:
    d = dict(r)
    attempts = int(d.get("attempts") or 0)
    done = attempts - int(d.get("running") or 0)
    inp = int(d.get("input_tokens") or 0)
    return {
        "attempts": attempts, "errors": int(d.get("errors") or 0), "running": int(d.get("running") or 0),
        "failure_rate": _ratio(int(d.get("errors") or 0), done),
        "cost_usd": round(float(d.get("cost_usd") or 0), 6), "cost_complete": int(d.get("no_cost") or 0) == 0,
        "input_tokens": inp, "output_tokens": int(d.get("output_tokens") or 0),
        "cache_read_tokens": int(d.get("cache_read_tokens") or 0),
        "cache_write_tokens": int(d.get("cache_write_tokens") or 0),
        "cache_read_ratio": _ratio(int(d.get("cache_read_tokens") or 0), inp),
        "avg_duration_ms": int(d["avg_duration_ms"]) if d.get("avg_duration_ms") is not None else None,
    }


def _run_outcomes(since_ms: int, until_ms: int, group_col: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[Any, Dict[str, Any]]]:
    """Runs started in the window: process ok vs verdict (overall, and per job / trigger)."""
    rows = connect().execute("SELECT data FROM runs WHERE started_at >= ? AND started_at <= ?",
                             (_iso(since_ms), _iso(until_ms))).fetchall()
    tot = {"runs": 0, "active": 0, "process_ok": 0, "pass": 0, "fail": 0, "unknown": 0, "ok_but_fail": 0,
           "failed_but_pass": 0}
    per: Dict[Any, Dict[str, Any]] = {}
    for (data,) in rows:
        try:
            run = json.loads(data)
        except ValueError:
            continue
        buckets = [tot]
        if group_col in ("job_id", "trigger_id"):
            k = run.get(group_col)
            buckets.append(per.setdefault(k, {k2: 0 for k2 in tot}))
        for b in buckets:
            b["runs"] += 1
            if run.get("status") in ("pending", "running", "awaiting_input"):
                b["active"] += 1
                continue
            ok = run.get("status") == "completed"
            v = run.get("verdict") or "unknown"
            b["process_ok"] += int(ok)
            b[v if v in ("pass", "fail") else "unknown"] += 1
            b["ok_but_fail"] += int(ok and v == "fail")
            b["failed_but_pass"] += int((not ok) and v == "pass")
    for b in [tot, *per.values()]:
        fin = b["runs"] - b["active"]
        b["process_ok_rate"] = _ratio(b["process_ok"], fin)
        b["success_rate"] = _ratio(b["pass"], b["pass"] + b["fail"])
    return tot, per


def summary(group: str = "agent", since: Optional[str] = None, until: Optional[str] = None,
            limit: int = 25) -> Dict[str, Any]:
    if group not in GROUPS:
        raise ValueError(f"group must be one of {tuple(GROUPS)}")
    try:
        store.prune()
    except Exception:
        pass
    now = int(time.time() * 1000)
    since_ms = parse_time(since, now - DEFAULT_WINDOW_DAYS * 86400 * 1000)
    until_ms = parse_time(until, now)
    col = GROUPS[group]
    c = connect()
    where = "source='telecode' AND operation='invoke_agent' AND start_ms >= ? AND start_ms <= ?"
    args = (since_ms, until_ms)
    totals = _agg_row(c.execute(f"SELECT {_AGG} FROM spans WHERE {where}", args).fetchone())

    days: Dict[str, Dict[str, Any]] = {}
    for r in c.execute(f"SELECT date(start_ms/1000, 'unixepoch', 'localtime') AS day, {_AGG} FROM spans "
                       f"WHERE {where} GROUP BY day ORDER BY day", args):
        days[r["day"]] = {"day": r["day"], **_agg_row(r)}
    # A continuous axis: every day in the window, zero-filled.
    by_day = []
    d0 = datetime.fromtimestamp(since_ms / 1000).date()
    d1 = datetime.fromtimestamp(until_ms / 1000).date()
    if (d1 - d0).days <= 400:
        d = d0
        while d <= d1:
            k = d.isoformat()
            by_day.append(days.get(k) or {"day": k, **_agg_row({"attempts": 0})})
            d += timedelta(days=1)
    else:
        by_day = list(days.values())

    groups = []
    rows = c.execute(f"SELECT {col} AS k, {_AGG} FROM spans WHERE {where} GROUP BY {col} "
                     f"ORDER BY SUM(COALESCE(cost_usd,0)) DESC, COUNT(*) DESC LIMIT ?", (*args, int(limit))).fetchall()
    labels = _labels(group, [r["k"] for r in rows])
    run_tot, run_per = _run_outcomes(since_ms, until_ms, col)
    for r in rows:
        g = {"key": r["k"], "label": labels.get(r["k"]), **_agg_row(r)}
        if col in ("job_id", "trigger_id"):
            g["runs"] = run_per.get(r["k"])
        if group == "trigger" and r["k"]:
            try:
                g["pass_k"] = trigger_passk(r["k"], 5)
            except Exception:
                g["pass_k"] = None
        groups.append(g)

    tools = [{"name": r["tool_name"], "count": int(r["n"]), "errors": int(r["e"] or 0),
              "error_rate": _ratio(int(r["e"] or 0), int(r["n"]))}
             for r in c.execute(
                 "SELECT tool_name, COUNT(*) AS n, SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS e FROM spans "
                 "WHERE source='telecode' AND operation='execute_tool' AND start_ms >= ? AND start_ms <= ? "
                 "GROUP BY tool_name ORDER BY n DESC LIMIT 12", args)]

    otlp = {
        "metric_points": int(c.execute("SELECT COUNT(*) FROM metric_points WHERE ts_ms >= ? AND ts_ms <= ?",
                                       args).fetchone()[0]),
        "log_events": int(c.execute("SELECT COUNT(*) FROM log_events WHERE ts_ms >= ? AND ts_ms <= ?",
                                    args).fetchone()[0]),
        "cost_by_model": [{"model": r["model"], "cost_usd": round(float(r["cost"] or 0), 6), "requests": int(r["n"]),
                           "input_tokens": int(r["i"] or 0), "output_tokens": int(r["o"] or 0),
                           "cache_read_tokens": int(r["cr"] or 0)}
                          for r in c.execute(
                              "SELECT model, SUM(cost_usd) AS cost, COUNT(*) AS n, SUM(input_tokens) AS i, "
                              "SUM(output_tokens) AS o, SUM(cache_read_tokens) AS cr FROM log_events "
                              "WHERE name LIKE '%api_request' AND ts_ms >= ? AND ts_ms <= ? GROUP BY model "
                              "ORDER BY cost DESC", args)],
        "metrics": [{"name": r["name"], "type": r["type"], "value": round(float(r["v"] or 0), 6)}
                    for r in c.execute(
                        "SELECT name, type, SUM(value) AS v FROM metric_points WHERE ts_ms >= ? AND ts_ms <= ? "
                        "AND (temporality IS NULL OR temporality='delta') GROUP BY name, type ORDER BY name, type",
                        args)],
    }
    return {"group": group, "since": _iso(since_ms), "until": _iso(until_ms), "totals": totals,
            "by_day": by_day, "groups": groups, "top_tools": tools, "runs": run_tot, "otlp": otlp}


# ── run timeline ────────────────────────────────────────────────────────────

def _span_stats(task_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not task_ids:
        return out
    c = connect()
    q = ",".join("?" * len(task_ids))
    for r in c.execute(f"SELECT task_id, model, status, start_ms, end_ms FROM spans WHERE source='telecode' "
                       f"AND operation='invoke_agent' AND task_id IN ({q})", task_ids):
        out.setdefault(r["task_id"], {}).update({"model": r["model"], "span_status": r["status"],
                                                 "span_start_ms": r["start_ms"], "span_end_ms": r["end_ms"]})
    for r in c.execute(f"SELECT task_id, COUNT(*) AS n FROM spans WHERE source='telecode' AND operation='execute_tool' "
                       f"AND task_id IN ({q}) GROUP BY task_id", task_ids):
        out.setdefault(r["task_id"], {})["tools"] = int(r["n"])
    return out


def _bar(rec: Dict[str, Any], now: int) -> Dict[str, Any]:
    s, e = iso_ms(rec.get("started_at")), iso_ms(rec.get("completed_at"))
    live = rec.get("status") in ("running", "pending", "awaiting_input")
    return {"start_ms": s, "end_ms": e if e else (now if (s and live) else None)}


def timeline(run_id: str) -> Optional[Dict[str, Any]]:
    from services.run.run_store import get_run_store
    run = get_run_store().get_run(run_id)
    if not run:
        return None
    now = int(time.time() * 1000)
    task_ids: List[str] = []
    for s in run.get("steps") or []:
        for a in s.get("attempts") or []:
            if a.get("task_id"):
                task_ids.append(a["task_id"])
        for w in s.get("workers") or []:
            if w.get("task_id"):
                task_ids.append(w["task_id"])
        for it in s.get("iterations") or []:
            g = (it.get("check") or {}).get("grader_task_id")
            if g:
                task_ids.append(g)
    stats = _span_stats(task_ids)
    otlp_cost: Dict[str, float] = {}
    for r in connect().execute("SELECT step_id, SUM(cost_usd) AS c FROM log_events WHERE run_id=? AND "
                               "name LIKE '%api_request' GROUP BY step_id", (run_id,)):
        otlp_cost[r["step_id"] or ""] = round(float(r["c"] or 0), 6)

    phases: Dict[int, List[Dict[str, Any]]] = {}
    for s in run.get("steps") or []:
        u = s.get("usage") or {}
        kind = s.get("kind") or (s.get("spec") or {}).get("kind") or "agent"
        attempts = []
        for a in s.get("attempts") or []:
            au = a.get("usage") or {}
            attempts.append({"n": a.get("n"), "mode": a.get("mode"), "status": a.get("status"),
                             "task_id": a.get("task_id"), "error": a.get("error"), "verdict": a.get("verdict"),
                             "cost_usd": au.get("cost_usd"), "input_tokens": au.get("input_tokens"),
                             "output_tokens": au.get("output_tokens"), **_bar(a, now),
                             **stats.get(a.get("task_id") or "", {})})
        workers = [{"n": w.get("n"), "item": w.get("item"), "status": w.get("status"), "task_id": w.get("task_id"),
                    "cost_usd": (w.get("usage") or {}).get("cost_usd"), **_bar(w, now),
                    **stats.get(w.get("task_id") or "", {})} for w in s.get("workers") or []]
        phases.setdefault(int((s.get("spec") or {}).get("phase") or 0), []).append({
            "step_id": s["step_id"], "name": s.get("name") or s.get("agent_name") or kind, "kind": kind,
            "status": s.get("status"), "engine": s.get("engine"), "model": s.get("model"),
            "agent_id": s.get("agent_id"), "session_id": s.get("session_id"),
            "session_policy": s.get("session_policy"),
            "verdict": (s.get("handoff") or {}).get("verdict"), "cost_usd": u.get("cost_usd"),
            "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
            "cache_read_tokens": u.get("cache_read_tokens"), "otlp_cost_usd": otlp_cost.get(s["step_id"]),
            **_bar(s, now), "attempts": attempts, "workers": workers,
            "iterations": [{"n": it.get("n"), "status": it.get("status"),
                            "passed": (it.get("check") or {}).get("passed")} for it in s.get("iterations") or []],
        })
    rs, re_ = iso_ms(run.get("started_at") or run.get("created_at")), iso_ms(run.get("completed_at"))
    return {
        "run_id": run_id, "job_id": run.get("job_id"), "status": run.get("status"),
        "title": (run.get("job_snapshot") or {}).get("title"),
        "start_ms": rs, "end_ms": re_ or (now if run.get("status") in ("running", "pending") else None),
        "now_ms": now, "usage": run.get("usage"), "budget": run.get("budget"),
        "process_ok": run.get("process_ok"), "verdict": run.get("verdict"),
        "verdict_source": run.get("verdict_source"), "verdict_detail": run.get("verdict_detail"),
        "outcome_check": run.get("outcome_check"), "trigger_id": run.get("trigger_id"),
        "otlp_cost_usd": round(sum(otlp_cost.values()), 6) if otlp_cost else None,
        "phases": [{"phase": p, "steps": phases[p]} for p in sorted(phases)],
    }


# ── pass^k ──────────────────────────────────────────────────────────────────

def _fire_outcome(f: Dict[str, Any]) -> Tuple[str, str]:
    st = f.get("status")
    if f.get("run_id"):
        r = connect().execute("SELECT data FROM runs WHERE run_id=?", (f["run_id"],)).fetchone()
        if r:
            try:
                run = json.loads(r[0])
            except ValueError:
                run = {}
            if run.get("verdict") in ("pass", "fail"):
                return run["verdict"], "verdict"
            if run.get("status") in ("pending", "running", "awaiting_input"):
                return "running", "process"
    if st in ("completed", "ok"):
        return "pass", "process"
    if st in ("failed", "interrupted"):
        return "fail", "process"
    return "unknown", "process"


def trigger_passk(trigger_id: str, k: int = 5) -> Dict[str, Any]:
    """pass^k over the last k decided fires (skipped / running fires don't count):
    ``pass_k`` = 1 when all k passed (the strict reading), ``p_hat`` = pass rate
    over them and ``p_hat_k`` = p_hat^k (the estimate of k independent passes).
    A run fire counts its verdict; a task fire (no verdict) its process status."""
    k = max(1, min(50, int(k)))
    rows = connect().execute(
        "SELECT id, seq, status, run_id, task_id, fired_at, completed_at, cost_usd FROM trigger_fires "
        "WHERE trigger_id=? AND status NOT IN ('skipped', 'running') ORDER BY seq DESC LIMIT ?",
        (trigger_id, k * 3)).fetchall()
    fires = []
    for r in rows:
        f = dict(r)
        outcome, basis = _fire_outcome(f)
        if outcome in ("running",):
            continue
        fires.append({**f, "outcome": outcome, "basis": basis})
        if len(fires) >= k:
            break
    decided = [f for f in fires if f["outcome"] in ("pass", "fail")]
    passes = sum(1 for f in decided if f["outcome"] == "pass")
    p = _ratio(passes, len(decided))
    return {"trigger_id": trigger_id, "k": k, "fires": fires, "decided": len(decided), "passes": passes,
            "p_hat": p, "p_hat_k": round(p ** k, 4) if p is not None else None,
            "pass_k": (1 if passes == k else 0) if len(decided) >= k else None}
