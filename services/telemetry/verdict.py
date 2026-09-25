"""Run verdicts (P5): *did the task succeed*, kept apart from *did the process finish*.

Every finished run gets::

    process_ok      the run completed (every step completed)
    verdict         pass | fail | unknown
    verdict_source  outcome_check | handoff | process
    verdict_detail  {handoffs: [{step_id, name, verdict}], reason}
    outcome_check   {command, exit_code, passed, output, output_path, duration_ms, ran_at}  (if the job has one)

Rules, in order:

1. The run was cancelled → ``unknown`` (``process``): nobody learned anything.
2. The job has an ``outcome_check`` ({command, timeout_seconds}) → it runs in
   the job workspace after the run (also after a failed run — the outcome can
   still be right, and knowing that is the point); exit 0 = ``pass``.
   It decides the verdict (``outcome_check``): an objective check outranks the
   agents' own opinion.
3. The process did not complete (failed / partial / budget_exceeded / rejected /
   interrupted) → ``fail`` (``process``).
4. Otherwise the final phase's handoff verdicts (``handoff``): any ``fail`` →
   fail, all ``pass`` → pass, else unknown (a derived handoff says unknown).

Called by the run executor when a driver ends (``apply``); a run still waiting
on a gate is left alone. Also writes the run's ``invoke_workflow`` span.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

logger = logging.getLogger("telecode.services.telemetry.verdict")

VERDICTS = ("pass", "fail", "unknown")
ACTIVE = ("pending", "running", "awaiting_input")
OUTCOME_CAP = 8 * 1024


def normalize_outcome_check(value: Any) -> Optional[Dict[str, Any]]:
    """A job's ``outcome_check``: None / "" → None; a string → {command};
    {command, timeout_seconds (1–3600, default 300)}. Raises ValueError."""
    if value in (None, "", {}):
        return None
    if isinstance(value, str):
        value = {"command": value}
    if not isinstance(value, dict):
        raise ValueError("outcome_check must be an object {command, timeout_seconds} or a command string")
    cmd = str(value.get("command") or "").strip()
    if not cmd:
        return None
    if len(cmd) > 4000:
        raise ValueError("outcome_check.command is too long (4000 chars max)")
    try:
        timeout = int(value.get("timeout_seconds") or 300)
    except (TypeError, ValueError):
        raise ValueError("outcome_check.timeout_seconds must be a number") from None
    return {"command": cmd, "timeout_seconds": max(1, min(3600, timeout))}


def _final_phase_steps(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps = [s for s in run.get("steps") or [] if s.get("status") != "skipped"]
    if not steps:
        return []
    last = max(int((s.get("spec") or {}).get("phase") or 0) for s in steps)
    return [s for s in steps if int((s.get("spec") or {}).get("phase") or 0) == last]


def handoff_verdicts(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for s in _final_phase_steps(run):
        if (s.get("kind") or (s.get("spec") or {}).get("kind")) == "map" and s.get("workers"):
            for w in s["workers"]:
                out.append({"step_id": s["step_id"], "name": f"{s.get('name') or 'map'} #{w.get('n')}",
                            "verdict": ((w.get("handoff") or {}).get("verdict") or "unknown")})
            continue
        ho = s.get("handoff") or {}
        out.append({"step_id": s["step_id"], "name": s.get("name") or s.get("agent_name") or "",
                    "verdict": ho.get("verdict") if ho.get("verdict") in VERDICTS else "unknown"})
    return out


def compute(run: Dict[str, Any], outcome: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    status = run.get("status")
    process_ok = status == "completed"
    hv = handoff_verdicts(run)
    detail: Dict[str, Any] = {"handoffs": hv}
    if status == "cancelled":
        verdict, source, detail["reason"] = "unknown", "process", "run cancelled"
    elif outcome is not None and outcome.get("passed") is not None:
        verdict, source = ("pass" if outcome["passed"] else "fail"), "outcome_check"
        detail["reason"] = f"outcome check exit {outcome.get('exit_code')}"
    elif not process_ok:
        verdict, source, detail["reason"] = "fail", "process", f"run {status}"
    else:
        vs = [h["verdict"] for h in hv]
        source = "handoff"
        if any(v == "fail" for v in vs):
            verdict, detail["reason"] = "fail", "a final step's handoff says fail"
        elif vs and all(v == "pass" for v in vs):
            verdict, detail["reason"] = "pass", "every final step's handoff says pass"
        else:
            verdict, detail["reason"] = "unknown", "no final handoff verdict"
    return {"process_ok": process_ok, "verdict": verdict, "verdict_source": source, "verdict_detail": detail}


def run_outcome_check(run: Dict[str, Any], check: Dict[str, Any]) -> Dict[str, Any]:
    """Run the job's outcome check in the job workspace (same runner as a loop
    step's command check: shell, Job-bound, tree-killed on timeout)."""
    from services.run.executor import _command_check
    from services.session import session_store
    ws = (run.get("job_snapshot") or {}).get("workspace_id")
    if not ws or not session_store.exists(ws):
        return {"command": check["command"], "passed": None, "exit_code": None,
                "output": "the job's workspace no longer exists — outcome check not run",
                "ran_at": _now_iso(), "duration_ms": 0}
    work_dir = Path(session_store._session_dir(ws))
    t0 = time.monotonic()
    n = int(time.time())
    res = _command_check(run["run_id"], "outcome", check, work_dir, SimpleNamespace(cancel_event=threading.Event()), n)
    out = str(res.get("output") or res.get("findings") or "")
    return {"command": check["command"], "passed": bool(res.get("passed")), "exit_code": res.get("exit_code"),
            "output": out[-OUTCOME_CAP:], "output_path": res.get("output_path"),
            "duration_ms": int((time.monotonic() - t0) * 1000), "ran_at": _now_iso()}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def apply(run_id: str) -> Optional[Dict[str, Any]]:
    """Verdict + outcome check for a run whose driver just ended; writes the
    workflow span either way. Never raises."""
    try:
        from services.run.run_store import get_run_store
        store = get_run_store()
        run = store.get_run(run_id)
        if not run:
            return None
        if run.get("status") in ACTIVE:
            _span(run)
            return run
        check = normalize_outcome_check((run.get("job_snapshot") or {}).get("outcome_check"))
        outcome = None
        if check and run.get("status") != "cancelled":
            try:
                outcome = run_outcome_check(run, check)
            except Exception as exc:
                logger.exception(f"outcome check for run {run_id[:8]} crashed")
                outcome = {"command": check["command"], "passed": False, "exit_code": None,
                           "output": f"the outcome check itself failed: {exc}", "ran_at": _now_iso()}
        v = compute(run, outcome)
        patch = dict(v)
        if outcome is not None:
            patch["outcome_check"] = outcome
        run = store.update_run(run_id, patch) or run
        _span(run)
        return run
    except Exception:
        logger.exception(f"verdict for run {run_id} failed")
        return None


def _span(run: Dict[str, Any]) -> None:
    try:
        from services.telemetry.spans import record_workflow
        record_workflow(run)
    except Exception:
        logger.exception("workflow span failed")
