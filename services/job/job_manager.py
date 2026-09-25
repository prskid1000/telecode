"""Persistent Job storage for Telecode."""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from services.task.safe_paths import resolve_in, validate_id

logger = logging.getLogger("telecode.services.job")

def get_jobs_base_dir() -> Path:
    return Path(config._settings_dir()) / "data" / "jobs"

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


VALID_PIPELINE_MODES = ("single", "sequential", "parallel", "custom")
# Per-step session policy (P2). "" = the default for the step's position:
# resume for a single-step phase (the job workspace), ephemeral in a parallel phase.
SESSION_POLICIES = ("resume", "fork", "fresh", "fresh_handoff", "ephemeral")
MAX_AUTO_RETRY = 5


def _normalize_pipeline(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise / validate a pipeline dict in-place; return it.

    Each step ends up with a `phase: int`. Phases run sequentially; steps
    in the same phase run in parallel.
      - single     → 1 step, phase 0
      - sequential → step i has phase i
      - parallel   → all steps share phase 0
      - custom     → respects per-step phase; fills missing values from index;
                     renumbers to contiguous 0..N-1 preserving relative order.
    """
    pipe = dict(data or {})
    mode = pipe.get("mode", "single")
    if mode not in VALID_PIPELINE_MODES:
        mode = "single"
    pipe["mode"] = mode

    raw_steps = pipe.get("steps") or []
    out_steps: List[Dict[str, Any]] = []
    for s in raw_steps:
        if not isinstance(s, dict):
            continue
        step = {
            "step_id": s.get("step_id") or str(uuid.uuid4()),
            "agent_id": s.get("agent_id"),
            "name": s.get("name") or "",
            "prompt_override": s.get("prompt_override") or "",
            "depends_on_text": bool(s.get("depends_on_text", False)),
            "phase": s.get("phase"),
            # Per-step overrides; blank / None = inherit (run body → agent default).
            "engine": _normalize_step_engine(s.get("engine")),
            "model": (s.get("model") or "").strip() if isinstance(s.get("model"), str) else "",
            "effort": _normalize_effort(s.get("effort")),
            "is_local": _normalize_tristate(s.get("is_local")),
            "session_policy": _normalize_policy(s.get("session_policy")),
            "budget": _normalize_budget(s.get("budget")),
            "auto_retry": _normalize_auto_retry(s.get("auto_retry")),
        }
        step.update(_normalize_kind(s))
        if not step["agent_id"] and step["kind"] != "gate":
            continue  # drop malformed steps (every kind but a gate runs an agent)
        if step["kind"] == "gate":
            step["agent_id"] = None
        out_steps.append(step)

    if mode == "single":
        out_steps = out_steps[:1]
        for s in out_steps:
            s["phase"] = 0
    elif mode == "sequential":
        for i, s in enumerate(out_steps):
            s["phase"] = i
    elif mode == "parallel":
        for s in out_steps:
            s["phase"] = 0
    elif mode == "custom":
        # Fill missing phase values (fall back to the step's index so adding
        # a step without setting phase puts it on its own phase by default),
        # then renumber to contiguous 0..N-1 preserving relative order.
        for i, s in enumerate(out_steps):
            if s["phase"] is None:
                s["phase"] = i
            else:
                try:
                    s["phase"] = int(s["phase"])
                except (TypeError, ValueError):
                    s["phase"] = i
        unique_phases = sorted({s["phase"] for s in out_steps})
        renumber = {p: i for i, p in enumerate(unique_phases)}
        for s in out_steps:
            s["phase"] = renumber[s["phase"]]

    _check_kind_phases(out_steps)
    pipe["steps"] = out_steps
    return pipe


# ── Step kinds (P3) ─────────────────────────────────────────────────────────
# agent   one agent run (default)
# map     fan-out: one worker per item of the previous phase's handoff field
# loop    evaluator-optimizer: body → check (command | grader | schema) → feedback
# gate    human approval (run status awaiting_input) — no agent
# reduce  an agent that merges the previous phase's handoffs into one
STEP_KINDS = ("agent", "map", "loop", "gate", "reduce")
MAP_SOURCES = ("next_steps", "items", "open_questions", "decisions", "artifacts")
MAP_WORKER_SESSIONS = ("ephemeral", "fork")
CHECK_TYPES = ("command", "grader", "schema")
MAX_MAP_PARALLEL = 16
MAX_MAP_ITEMS = 100
MAX_LOOP_ITERATIONS = 10
GATE_TIMEOUT_POLICIES = ("reject", "approve", "skip")
MIN_GATE_TIMEOUT = 10
MAX_GATE_TIMEOUT = 30 * 24 * 3600


def _int_in(value: Any, default: int, lo: int, hi: int, name: str) -> int:
    if value is None or value == "":
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer {lo}..{hi}, got {value!r}") from None
    if n < lo or n > hi:
        raise ValueError(f"{name} must be {lo}..{hi}, got {n}")
    return n


def _normalize_kind(s: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(s.get("kind") or "agent").strip().lower()
    if kind not in STEP_KINDS:
        raise ValueError(f"step kind must be one of {STEP_KINDS}, got {s.get('kind')!r}")
    out: Dict[str, Any] = {"kind": kind}
    if kind == "map":
        m = s.get("map") if isinstance(s.get("map"), dict) else {}
        src = str(m.get("items_from") or "next_steps").strip().lower()
        if src not in MAP_SOURCES:
            raise ValueError(f"map.items_from must be one of {MAP_SOURCES}, got {src!r}")
        ws = str(m.get("worker_session") or "ephemeral").strip().lower()
        if ws not in MAP_WORKER_SESSIONS:
            raise ValueError(f"map.worker_session must be one of {MAP_WORKER_SESSIONS}")
        out["map"] = {
            "items_from": src,
            "max_parallel": _int_in(m.get("max_parallel"), 3, 1, MAX_MAP_PARALLEL, "map.max_parallel"),
            "max_items": _int_in(m.get("max_items"), 20, 1, MAX_MAP_ITEMS, "map.max_items"),
            "worker_session": ws,
        }
    elif kind == "loop":
        lp = s.get("loop") if isinstance(s.get("loop"), dict) else {}
        chk = lp.get("check") if isinstance(lp.get("check"), dict) else {}
        ctype = str(chk.get("type") or "command").strip().lower()
        if ctype not in CHECK_TYPES:
            raise ValueError(f"loop.check.type must be one of {CHECK_TYPES}, got {ctype!r}")
        check: Dict[str, Any] = {"type": ctype}
        if ctype == "command":
            cmd = str(chk.get("command") or "").strip()
            if not cmd:
                raise ValueError("loop.check.command is required for a command check")
            check["command"] = cmd[:4000]
            check["timeout_seconds"] = _int_in(chk.get("timeout_seconds"), 300, 5, 3600, "loop.check.timeout_seconds")
        elif ctype == "grader":
            check["rubric"] = str(chk.get("rubric") or "").strip()[:16000]
            if not check["rubric"]:
                raise ValueError("loop.check.rubric is required for a grader check")
            check["grader_agent_id"] = (str(chk.get("grader_agent_id") or "").strip() or None)
            check["grader_engine"] = _normalize_step_engine(chk.get("grader_engine"))
            check["grader_model"] = str(chk.get("grader_model") or "").strip()
        else:
            schema = chk.get("schema")
            if isinstance(schema, str):
                try:
                    schema = json.loads(schema) if schema.strip() else None
                except ValueError as exc:
                    raise ValueError(f"loop.check.schema is not valid JSON: {exc}") from None
            if not isinstance(schema, dict):
                raise ValueError("loop.check.schema must be a JSON Schema object")
            check["schema"] = schema
            # blank = validate the body's handoff; else a JSON file in the workspace
            check["path"] = str(chk.get("path") or "").strip()[:500]
        out["loop"] = {"max_iterations": _int_in(lp.get("max_iterations"), 3, 1, MAX_LOOP_ITERATIONS,
                                                 "loop.max_iterations"), "check": check}
    elif kind == "gate":
        g = s.get("gate") if isinstance(s.get("gate"), dict) else {}
        out["gate"] = {"title": str(g.get("title") or s.get("name") or "Approval").strip()[:200],
                       "instructions": str(g.get("instructions") or "").strip()[:8000]}
        # Deferred-P3: a gate may time out. timeout_sec blank/0 = wait forever.
        tmo = g.get("timeout_sec")
        if tmo not in (None, "", 0, "0"):
            out["gate"]["timeout_sec"] = _int_in(tmo, 0, MIN_GATE_TIMEOUT, MAX_GATE_TIMEOUT, "gate.timeout_sec")
            pol = str(g.get("on_timeout") or "reject").strip().lower()
            if pol not in GATE_TIMEOUT_POLICIES:
                raise ValueError(f"gate.on_timeout must be one of {GATE_TIMEOUT_POLICIES}, got {pol!r}")
            out["gate"]["on_timeout"] = pol
    return out


def _check_kind_phases(steps: List[Dict[str, Any]]) -> None:
    """map / loop / gate / reduce steps own their phase; map and reduce need a
    previous phase to read from."""
    by_phase: Dict[int, List[Dict[str, Any]]] = {}
    for s in steps:
        by_phase.setdefault(int(s.get("phase") or 0), []).append(s)
    first = min(by_phase) if by_phase else 0
    for p, group in by_phase.items():
        special = [s for s in group if s.get("kind", "agent") != "agent"]
        if special and len(group) > 1:
            raise ValueError(f"a {special[0]['kind']} step must be alone in its phase (phase {p + 1} has "
                             f"{len(group)} steps) — use the custom mode to give it its own phase")
        for s in special:
            if s["kind"] in ("map", "reduce") and p == first:
                raise ValueError(f"a {s['kind']} step needs a previous phase to read handoffs from")



def _normalize_step_engine(engine: Any) -> str:
    """'' = inherit; else one of the supported engine keys. Unknown → ValueError."""
    from services.task.engine_map import supported_engines
    if engine is None:
        return ""
    e = str(engine).strip().lower()
    if e and e not in supported_engines():
        raise ValueError(f"step engine must be one of {supported_engines()} or blank, got {engine!r}")
    return e


def _normalize_policy(value: Any) -> str:
    """'' = default for the step's position; else one of SESSION_POLICIES."""
    v = str(value or "").strip().lower().replace("+", "_").replace("-", "_")
    if v and v not in SESSION_POLICIES:
        raise ValueError(f"session_policy must be one of {SESSION_POLICIES} or blank, got {value!r}")
    return v


def _normalize_budget(value: Any) -> Dict[str, Any]:
    """{max_usd, max_tokens, max_seconds}; blank / 0 = inherit (unlimited)."""
    from services.run.budget import normalize
    return {k: v for k, v in normalize(value).items() if v is not None}


def _normalize_outcome_check(value: Any) -> Optional[Dict[str, Any]]:
    from services.telemetry.verdict import normalize_outcome_check
    return normalize_outcome_check(value)


JOB_PERMISSION_MODES = ("", "skip", "ask", "auto", "acceptEdits", "dontAsk", "plan", "manual")


def _normalize_effort(value: Any) -> str:
    from services.engine.types import normalize_effort
    return normalize_effort(value)


def _normalize_job_permission_mode(value: Any) -> str:
    v = str(value or "").strip()
    if v not in JOB_PERMISSION_MODES:
        raise ValueError(f"permission_mode must be one of {JOB_PERMISSION_MODES}")
    return v


def _normalize_auto_retry(value: Any) -> int:
    try:
        n = int(value or 0)
    except (TypeError, ValueError):
        raise ValueError(f"auto_retry must be an integer 0..{MAX_AUTO_RETRY}, got {value!r}") from None
    if n < 0 or n > MAX_AUTO_RETRY:
        raise ValueError(f"auto_retry must be 0..{MAX_AUTO_RETRY}")
    return n


def _normalize_tristate(value: Any) -> Optional[bool]:
    """None / '' = inherit; otherwise a bool ("true"/"false" strings accepted)."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("", "inherit", "default"):
            return None
        return v in ("1", "true", "yes", "on", "local")
    return bool(value)


class JobManager:
    def __init__(self):
        self.base_dir = get_jobs_base_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_job_path(self, job_id: str) -> Path:
        return self.base_dir / f"{validate_id(job_id, 'job_id')}.json"

    def _get_job_files_dir(self, job_id: str) -> Path:
        return self.base_dir / validate_id(job_id, "job_id") / "files"

    def list_jobs(self, kind: Optional[str] = None, include_archived: bool = False) -> List[Dict[str, Any]]:
        jobs = []
        for p in self.base_dir.glob("*.json"):
            try:
                jobs.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception as e:
                logger.error(f"Failed to load job from {p}: {e}")
        if kind:
            jobs = [j for j in jobs if j.get("kind") == kind]
        if not include_archived:
            jobs = [j for j in jobs if not j.get("archived")]
        return sorted(jobs, key=lambda x: x.get("updated_at", ""), reverse=True)

    def create_job(self, data: Dict[str, Any]) -> Dict[str, Any]:
        job_id = str(uuid.uuid4())
        now = _now_iso()
        # Build pipeline from the explicit `pipeline` dict, or from a single
        # `agent_id` (the simple create-job modal uses this shape).
        if "pipeline" in data:
            pipeline = _normalize_pipeline(data["pipeline"])
        elif data.get("agent_id"):
            pipeline = _normalize_pipeline({
                "mode": "single",
                "steps": [{"agent_id": data["agent_id"]}],
            })
        else:
            pipeline = {"mode": "single", "steps": []}

        job_data = {
            "id": job_id,
            "title": data.get("title", "Untitled Job"),
            "agent_id": data.get("agent_id"),  # kept for legacy reads
            "workspace_id": data.get("workspace_id"),
            "actions": data.get("actions", []),
            "tasks": data.get("tasks", []),
            "task_description": data.get("task_description", ""),
            "pipeline": pipeline,
            # Run-level budget {max_usd, max_tokens, max_seconds} split across the steps.
            "budget": _normalize_budget(data.get("budget")),
            # P5: shell command run in the workspace after each run (exit 0 = pass) and
            # the Claude permission mode for its runs ("" = skip, "ask" = approve_tool).
            "outcome_check": _normalize_outcome_check(data.get("outcome_check")),
            "permission_mode": _normalize_job_permission_mode(data.get("permission_mode")),
            "kind": "user",   # the only kind since P3 (heartbeat jobs became triggers)
            "archived": bool(data.get("archived", False)),
            "created_at": now,
            "updated_at": now
        }
        self._get_job_path(job_id).write_text(json.dumps(job_data, indent=2), encoding="utf-8")
        self._get_job_files_dir(job_id).mkdir(parents=True, exist_ok=True)
        return job_data

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        try:
            p = self._get_job_path(job_id)
        except ValueError:
            return None
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def update_job(self, job_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        job = self.get_job(job_id)
        if not job:
            return None

        for key in ["title", "actions", "tasks", "task_description",
                    "agent_id", "workspace_id", "archived"]:
            if key in data:
                job[key] = data[key]
        if "pipeline" in data:
            job["pipeline"] = _normalize_pipeline(data["pipeline"])
        if "budget" in data:
            job["budget"] = _normalize_budget(data["budget"])
        if "outcome_check" in data:
            job["outcome_check"] = _normalize_outcome_check(data["outcome_check"])
        if "permission_mode" in data:
            job["permission_mode"] = _normalize_job_permission_mode(data["permission_mode"])

        job["updated_at"] = _now_iso()
        self._get_job_path(job_id).write_text(json.dumps(job, indent=2), encoding="utf-8")
        return job

    def delete_job(self, job_id: str) -> bool:
        p = self._get_job_path(job_id)
        if p.exists():
            p.unlink()
            files_dir = self.base_dir / validate_id(job_id, "job_id")
            if files_dir.exists():
                shutil.rmtree(files_dir)
            return True
        return False

    def list_files(self, job_id: str) -> List[Dict[str, Any]]:
        files_dir = self._get_job_files_dir(job_id)
        if not files_dir.exists():
            return []
        
        result = []
        for p in files_dir.glob("**/*"):
            if p.is_file():
                rel = p.relative_to(files_dir).as_posix()
                result.append({
                    "path": rel,
                    "bytes": p.stat().st_size,
                    "modified_at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                })
        return result

    @staticmethod
    def _resolve_in(files_dir: Path, filename: str) -> Path:
        """Resolve `filename` inside `files_dir`, refusing to escape it.

        Callers pass names straight from HTTP -- an upload part's filename, or
        a JSON field -- so `files_dir / filename` alone is an arbitrary-write
        primitive: "../../../x" walks out of the job directory, and save_file
        even creates the parents on the way. Containment is enforced here, at
        the one place every caller goes through, rather than in each handler.
        Shared with the agent store via services.task.safe_paths.
        """
        return resolve_in(files_dir, filename)

    def save_file(self, job_id: str, filename: str, content: bytes):
        files_dir = self._get_job_files_dir(job_id)
        files_dir.mkdir(parents=True, exist_ok=True)
        dest = self._resolve_in(files_dir, filename)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)

    def get_file_path(self, job_id: str, filename: str) -> Optional[Path]:
        try:
            p = self._resolve_in(self._get_job_files_dir(job_id), filename)
        except ValueError:
            return None
        if p.exists() and p.is_file():
            return p
        return None

    def delete_file(self, job_id: str, filename: str) -> bool:
        try:
            p = self._resolve_in(self._get_job_files_dir(job_id), filename)
        except ValueError:
            return False
        if p.exists() and p.is_file():
            p.unlink()
            return True
        return False

_manager = JobManager()
def get_job_manager() -> JobManager:
    return _manager
