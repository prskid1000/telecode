"""Reflection — sleep-time memory consolidation with an approval gate.

Each agent gets one **reflection trigger** (created through the triggers
service, visible on the agent's Triggers card): target ``agent_prompt`` (the
agent itself, its default engine / model, **cloud** unless
``tasks.memory.reflect_local`` is set), a fresh session per fire, a nightly
cron (``tasks.memory.reflect_cron``, UTC) that is **paused** unless nightly
reflection is switched on for the agent. It fires:

* nightly (when on), from the trigger scheduler;
* after ``tasks.memory.reflect_after_runs`` runs of the agent (default 10;
  0 = off; a pipeline run counts once however many of its steps the agent
  ran) — :func:`note_run`, called by staging after each write-back;
* on demand — "Reflect now" (:func:`reflect_now`).

A reflection fire is staged like any agent run, plus
``.telecode/memory_reflection_input.md`` (the digest :func:`build_input`
writes: the current index and topic files with their helpful / harmful
counters, the pinned constraints, and what the agent's runs since the last
reflection did — handoffs, replies, errors). It must not edit files (staging
discards any write-back of a reflection fire, and :func:`services.memory.
engine_extras` gives it no memory dir / auto memory); it replies with JSON::

    {"summary": "...",
     "operations": [{"op": "ADD|UPDATE|DELETE|NOOP", "file": "feedback_x.md",
                     "name": "...", "description": "...", "type": "user|feedback|project|reference",
                     "body": "...", "helpful": +1, "harmful": 0, "reason": "..."}]}

(Mem0's ADD / UPDATE / DELETE / NOOP per candidate memory; ACE's helpful /
harmful counters on feedback entries, given as deltas.) :func:`process`
turns a finished fire's reply into a **proposal commit** built on the current
memory (a temporary index; kept alive by ``refs/proposals/<task>``; the work
tree is untouched) and an approval of kind ``memory`` whose body is the diff.
Approve → the commit is merged into the agent's memory (a fast-forward, or a
merge when memory moved meanwhile); reject → the ref is dropped. A newer
proposal supersedes a pending one.

State per agent: ``data/agents/<id>/reflection.json`` (outside the repo).
The ticker (:func:`start`, 30 s, from the proxy) processes finished fires and
runs the one-time memory migration of every agent at startup.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.memory import repo, store

logger = logging.getLogger("telecode.services.memory.reflection")

STATE_FILE = "reflection.json"
INPUT_REL = ".telecode/memory_reflection_input.md"
MAX_OPS = 50
TOPIC_CAP = 8 * 1024
TOPICS_TOTAL_CAP = 96 * 1024
RESULT_CAP = 3 * 1024
MAX_RUNS_IN_INPUT = 15
TICK_SECONDS = 30.0

REFLECT_PROMPT = (
    "Memory reflection (sleep-time consolidation). Read the file .telecode/memory_reflection_input.md in the "
    "current directory: it holds your current long-term memory (the index and every topic file, with helpful / "
    "harmful counters on feedback entries), your pinned constraints, and what your recent runs did.\n\n"
    "Decide, candidate by candidate, what your memory should keep:\n"
    "- ADD a new topic file for a durable fact, preference, lesson or pointer the runs revealed that memory lacks;\n"
    "- UPDATE a topic that is stale, wrong, vague or duplicated (merge duplicates into one, DELETE the rest), and "
    "bump a feedback entry's counters: helpful +1 when a run followed it and it helped, harmful +1 when following "
    "it hurt;\n"
    "- DELETE what is obsolete, contradicted, or has harmful clearly above helpful;\n"
    "- NOOP for a candidate you considered and chose to leave (say why).\n"
    "Keep memories short and specific; one idea per topic file. Never contradict the pinned constraints. Only "
    "record what will matter in future runs — not a log of what happened.\n\n"
    "Do not edit, create or delete any file. Reply with ONLY one JSON object, no prose around it:\n"
    '{"summary": "<one paragraph>", "operations": [{"op": "ADD|UPDATE|DELETE|NOOP", "file": "<topic file, for '
    'UPDATE/DELETE/NOOP>", "name": "<title>", "description": "<one line for the index>", "type": '
    '"user|feedback|project|reference", "body": "<full new body, for ADD / an UPDATE that changes it>", '
    '"helpful": <delta>, "harmful": <delta>, "reason": "<why>"}]}\n'
    "If nothing should change, reply with an empty operations list.")

_state_locks_guard = threading.Lock()
_state_locks: Dict[str, threading.RLock] = {}
_ticker: Optional[threading.Thread] = None
_stop: Optional[threading.Event] = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _lock(agent_id: str) -> threading.RLock:
    with _state_locks_guard:
        lk = _state_locks.get(agent_id)
        if lk is None:
            lk = _state_locks[agent_id] = threading.RLock()
        return lk


def _state_path(agent_id: str) -> Path:
    return store.agent_state_dir(agent_id) / STATE_FILE


def _load(agent_id: str) -> Dict[str, Any]:
    p = _state_path(agent_id)
    with _lock(agent_id):            # never read while _save swaps the file (Windows: access denied)
        for attempt in range(5):
            try:
                return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))   # an antivirus / indexer holding it briefly
            except Exception:
                logger.exception(f"unreadable reflection state {p}")
                return {}
        logger.error(f"reflection state {p} stayed locked")
        return {}


def _save(agent_id: str, st: Dict[str, Any]) -> None:
    p = _state_path(agent_id)
    if not p.parent.is_dir():
        return                       # agent deleted
    with _lock(agent_id):
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
        for attempt in range(5):
            try:
                tmp.replace(p)
                return
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))
        tmp.replace(p)


def _after_runs(st: Dict[str, Any]) -> int:
    import config
    v = st.get("after_runs")
    if v is None:
        return config.tasks_memory_reflect_after_runs()
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return config.tasks_memory_reflect_after_runs()


def _agent(agent_id: str) -> Optional[Dict[str, Any]]:
    from services.agent.agent_manager import get_agent_manager
    return get_agent_manager().get_agent(agent_id)


# ── the trigger ────────────────────────────────────────────────────────────

def _trigger_body(agent: Dict[str, Any], nightly: bool) -> Dict[str, Any]:
    import config
    engine = agent.get("engine") or "claude_code"
    model = config.tasks_memory_reflect_model() or agent.get("model") or ""
    return {
        "name": f"Memory reflection · {agent.get('name') or agent['id'][:8]}",
        "description": "Consolidates this agent's memory from its recent runs and proposes a memory diff for "
                       "approval (Approvals inbox). Created by the agent's Memory panel.",
        "status": "active" if nightly else "paused",
        "target": {"kind": "agent_prompt", "agent_id": agent["id"], "prompt": REFLECT_PROMPT,
                   "engine": engine, "model": model, "is_local": bool(config.tasks_memory_reflect_local())},
        "schedule": {"cron": config.tasks_memory_reflect_cron(), "tz": config.tasks_memory_reflect_tz()},
        "session": "fresh", "preface": False, "outputs_only": True, "notify": False,
        "ok_suppression": False, "task_timeout_seconds": 900, "auto_pause_after_failures": 3,
    }


def ensure_trigger(agent_id: str) -> str:
    """The agent's reflection trigger id — created, or brought in line with the
    agent's current engine / model and the nightly switch."""
    from services.triggers import service, store as tstore
    agent = _agent(agent_id)
    if not agent:
        raise LookupError("agent not found")
    with _lock(agent_id):
        st = _load(agent_id)
        body = _trigger_body(agent, bool(st.get("nightly")))
        tid = st.get("trigger_id")
        cur = tstore.get(tid) if tid else None
        if cur is None:
            rec = service.create(body)
            st["trigger_id"] = rec["id"]
            _save(agent_id, st)
            logger.info(f"agent {agent_id}: reflection trigger {rec['id'][:8]} created")
            return rec["id"]
        want = {k: body[k] for k in ("target", "name")}
        if (cur.get("target") or {}).get("engine") != body["target"]["engine"] or \
                (cur.get("target") or {}).get("model") != body["target"]["model"] or \
                (cur.get("target") or {}).get("prompt") != REFLECT_PROMPT or cur.get("name") != body["name"] or \
                bool((cur.get("target") or {}).get("is_local")) != body["target"]["is_local"]:
            service.patch(tid, want)
        return tid


def set_options(agent_id: str, *, nightly: Optional[bool] = None,
                after_runs: Optional[int] = None) -> Dict[str, Any]:
    from services.triggers import service
    with _lock(agent_id):
        st = _load(agent_id)
        if nightly is not None:
            st["nightly"] = bool(nightly)
        if after_runs is not None:
            if after_runs == -1:
                st.pop("after_runs", None)          # back to the setting's default
            else:
                st["after_runs"] = max(0, min(int(after_runs), 10_000))
        _save(agent_id, st)
    if nightly is not None:
        tid = ensure_trigger(agent_id)
        service.set_status(tid, "active" if nightly else "paused")
    return status(agent_id)


def forget(agent_id: str) -> None:
    """Agent deleted: its reflection trigger and pending proposals go too."""
    st = _load(agent_id)
    if st.get("trigger_id"):
        try:
            from services.triggers import store as tstore
            tstore.delete(st["trigger_id"])
        except Exception:
            logger.exception("deleting the reflection trigger failed")
    for ap in _pending_approvals(agent_id):
        try:
            from services import approvals
            approvals.decide(ap["id"], "reject", by="system", note="agent deleted")
        except Exception:
            pass


def is_reflection_task(agent_id: Optional[str], info: Dict[str, Any]) -> bool:
    if not agent_id or not info.get("trigger_id"):
        return False
    try:
        return _load(agent_id).get("trigger_id") == info["trigger_id"]
    except Exception:
        return False


def current_task_is_reflection(agent_id: Optional[str]) -> bool:
    if not agent_id:
        return False
    try:
        from services.task.staging import _task_info
        return is_reflection_task(agent_id, _task_info())
    except Exception:
        return False


# ── counting runs, firing ──────────────────────────────────────────────────

class Busy(RuntimeError):
    pass


def _pending_approvals(agent_id: str) -> List[Dict[str, Any]]:
    try:
        from services import approvals
        return [a for a in approvals.list_approvals(status="pending", limit=500)
                if a.get("kind") == "memory" and (a.get("payload") or {}).get("agent_id") == agent_id]
    except Exception:
        return []


def _running(agent_id: str, st: Dict[str, Any]) -> Optional[str]:
    tid = st.get("trigger_id")
    if not tid:
        return None
    try:
        from services.triggers import store as tstore
        from services.task.task_manager import get_task_queue
        last = tstore.last_fire(tid)
        if last and last.get("task_id"):
            rec = get_task_queue().get_task_record(last["task_id"])
            if rec and rec.get("status") in ("pending", "running"):
                return last["task_id"]
    except Exception:
        logger.exception("reflection: running check failed")
    return None


def note_run(agent_id: str, info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Count one run of the agent (a pipeline run once); fire a reflection when
    the count reaches ``reflect_after_runs``."""
    key = info.get("run_id") or info.get("task_id")
    if not key or not _agent(agent_id):
        return None
    with _lock(agent_id):
        st = _load(agent_id)
        counted = list(st.get("counted") or [])
        if key in counted:
            return None
        counted = (counted + [key])[-100:]
        st["counted"] = counted
        st["runs_since"] = int(st.get("runs_since") or 0) + 1
        k = _after_runs(st)
        due = k > 0 and st["runs_since"] >= k
        _save(agent_id, st)
    if not due:
        return None
    try:
        return reflect_now(agent_id, reason=f"after {k} runs")
    except Busy as exc:
        logger.info(f"agent {agent_id}: reflection due but {exc}")
    except Exception:
        logger.exception(f"agent {agent_id}: automatic reflection failed to start")
    return None


def reflect_now(agent_id: str, reason: str = "reflect now") -> Dict[str, Any]:
    from services.triggers import fire as fire_mod
    process(agent_id)
    st = _load(agent_id)
    if _running(agent_id, st):
        raise Busy("a reflection is already running")
    tid = ensure_trigger(agent_id)
    res = dict(fire_mod.fire(tid, source="manual", reason=reason))
    with _lock(agent_id):
        st = _load(agent_id)
        st["last_fire"] = {"at": _now(), "reason": reason, "status": res.get("status"),
                           "task_id": res.get("task_id"), "detail": res.get("reason")}
        _save(agent_id, st)
    return res


# ── the input digest ───────────────────────────────────────────────────────

def _cap(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"\n… [{len(s) - n} more characters]"


def _recent_tasks(agent_id: str, since: Optional[str], exclude_trigger: Optional[str]) -> List[Dict[str, Any]]:
    from services.task.task_manager import get_task_queue
    out = []
    for t in get_task_queue().list_task_records(limit=500):
        md = t.get("metadata") or {}
        if md.get("agent_id") != agent_id or (exclude_trigger and md.get("trigger_id") == exclude_trigger):
            continue
        if t.get("status") in ("pending", "running"):
            continue
        done = t.get("completed_at") or t.get("created_at") or ""
        if since and done and done < since:
            continue
        out.append(t)
    out.sort(key=lambda t: t.get("completed_at") or t.get("created_at") or "", reverse=True)
    return out[:MAX_RUNS_IN_INPUT]


def _handoff_of(md: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not md.get("run_id") or not md.get("step_id"):
        return None
    try:
        from services.run.run_store import get_run_store
        run = get_run_store().get_run(md["run_id"]) or {}
        step = next((s for s in run.get("steps") or [] if s.get("step_id") == md["step_id"]), None)
        return {"job": (run.get("job_snapshot") or {}).get("title"), "step": (step or {}).get("name"),
                "handoff": (step or {}).get("handoff")}
    except Exception:
        return None


def build_input(agent_id: str) -> str:
    agent = _agent(agent_id) or {"id": agent_id}
    st = _load(agent_id)
    since = st.get("last_reflection_at")
    parts = [f"# Memory reflection input — agent {agent.get('name') or agent_id}",
             f"Generated {_now()}. Last reflection: {since or 'never'}.", ""]
    parts += ["## Current memory index (memory/MEMORY.md)", "```markdown", store.read_index(agent_id) or "(empty)",
              "```", ""]
    parts.append("## Topic files")
    total = 0
    topics = store.list_topics(agent_id)
    if not topics:
        parts.append("(none yet)")
    for t in topics:
        full = store.read_topic(agent_id, t["file"]) or {}
        body = _cap(full.get("content") or "", TOPIC_CAP)
        total += len(body)
        if total > TOPICS_TOTAL_CAP:
            parts.append(f"### {t['file']} — omitted (input size cap); name: {t['name']}; {t['description']}")
            continue
        counters = f" — helpful {t['helpful']}, harmful {t['harmful']}" if t["type"] == "feedback" else ""
        parts += [f"### {t['file']} ({t['type']}{counters})", "```markdown", body.rstrip("\n"), "```"]
    parts.append("")
    pinned = pinned_constraints(agent_id)
    parts += ["## Pinned constraints (never contradict)", pinned or "(none)", ""]
    runs = _recent_tasks(agent_id, since, st.get("trigger_id"))
    parts.append(f"## Recent runs ({len(runs)}{' since the last reflection' if since else ''})")
    if not runs:
        parts.append("No runs since the last reflection — consolidate what is there (merge duplicates, sharpen, "
                     "prune), or change nothing.")
    for t in runs:
        md = t.get("metadata") or {}
        res = t.get("result") if isinstance(t.get("result"), dict) else {}
        where = (f"run {md.get('run_id')} step {md.get('step_id')}" if md.get("run_id")
                 else f"trigger {md.get('trigger_name') or md.get('trigger_id')}" if md.get("trigger_id")
                 else "task")
        parts.append(f"### {t.get('completed_at') or t.get('created_at')} · {t.get('status')} · {where} · "
                     f"{md.get('engine') or ''}")
        ho = _handoff_of(md)
        if ho and ho.get("handoff"):
            h = ho["handoff"]
            parts.append(f"Job: {ho.get('job') or '-'}, step: {ho.get('step') or '-'}")
            parts.append(f"Handoff: status {h.get('status')}, verdict {h.get('verdict')} — {h.get('summary') or ''}")
            for key in ("decisions", "open_questions", "next_steps"):
                vals = h.get(key) or []
                if vals:
                    parts.append(f"{key.replace('_', ' ').capitalize()}: " + "; ".join(str(v) for v in vals[:10]))
        text = res.get("result") if isinstance(res, dict) else None
        if text:
            parts += ["Reply:", "```text", _cap(str(text), RESULT_CAP), "```"]
        if t.get("error"):
            parts.append(f"Error: {_cap(str(t['error']), 1000)}")
        parts.append("")
    return "\n".join(parts) + "\n"


def stage_input(agent_id: str, work_dir: Path) -> List[str]:
    p = work_dir / INPUT_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build_input(agent_id), encoding="utf-8")
    return [INPUT_REL]


def pinned_constraints(agent_id: Optional[str]) -> str:
    if not agent_id:
        return ""
    try:
        from services.agent.agent_manager import get_agent_manager
        return store.pinned_from_text(get_agent_manager().get_internal_files(agent_id).get("AGENT.md", "") or "")
    except Exception:
        return ""


# ── reply → proposal → approval ────────────────────────────────────────────

def parse_proposal(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    cands = [text]
    cands += [m.group(1) for m in re.finditer(r"```(?:json)?\s*\n(.*?)```", text, re.S)]
    if "{" in text:
        cands.append(text[text.find("{"): text.rfind("}") + 1])
    for c in cands:
        try:
            obj = json.loads(c)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and isinstance(obj.get("operations"), list):
            return obj
    return None


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def plan(agent_id: str, proposal: Dict[str, Any]) -> Tuple[Dict[str, Optional[str]], List[Dict[str, Any]]]:
    """Operations → ({repo path: new text | None}, [op records with status])."""
    mdir = store.memory_dir(agent_id)
    existing = {p.name for p in mdir.glob("*.md") if p.name != store.INDEX}
    index = store.read_index(agent_id)
    files: Dict[str, Optional[str]] = {}
    ops_out: List[Dict[str, Any]] = []
    for raw in (proposal.get("operations") or [])[:MAX_OPS]:
        if not isinstance(raw, dict):
            continue
        op = str(raw.get("op") or "").upper()
        rec = {"op": op, "file": raw.get("file"), "name": raw.get("name"), "reason": raw.get("reason")}
        try:
            if op == "NOOP":
                rec["status"] = "noop"
            elif op == "ADD":
                name = str(raw.get("name") or "").strip()
                body = str(raw.get("body") or "").strip()
                if not name or not body:
                    raise ValueError("ADD needs a name and a body")
                t = raw.get("type") if raw.get("type") in store.TYPES else "project"
                fname = raw.get("file")
                if not (isinstance(fname, str) and store.TOPIC_RE.match(fname) and fname != store.INDEX
                        and fname not in existing):
                    fname = store.unique_topic_name(sorted(existing), t, name)
                meta = {"name": name, "description": str(raw.get("description") or store.describe(body)), "type": t}
                if t == "feedback":
                    meta.update(helpful=max(0, _int(raw.get("helpful"))), harmful=max(0, _int(raw.get("harmful"))))
                files[f"{store.MEMORY_DIR}/{fname}"] = store.render_topic(meta, body)
                index = store.upsert_index_line(index, fname, meta)
                existing.add(fname)
                rec.update(file=fname, status="applied")
            elif op in ("UPDATE", "DELETE"):
                fname = store.check_topic_name(str(raw.get("file") or ""))
                if fname not in existing:
                    raise ValueError(f"{fname} does not exist")
                if op == "DELETE":
                    files[f"{store.MEMORY_DIR}/{fname}"] = None
                    index = store.drop_index_line(index, fname)
                    existing.discard(fname)
                    rec["status"] = "applied"
                else:
                    cur = files.get(f"{store.MEMORY_DIR}/{fname}") or (mdir / fname).read_text(encoding="utf-8")
                    meta, body = store.parse_topic(cur)
                    new_meta, new_body = dict(meta), body
                    for k in ("name", "description"):
                        if raw.get(k):
                            new_meta[k] = str(raw[k]).strip()
                    if raw.get("type") in store.TYPES:
                        new_meta["type"] = raw["type"]
                    if raw.get("body"):
                        new_body = str(raw["body"]).strip()
                    if new_meta.get("type") == "feedback":
                        new_meta["helpful"] = max(0, _int(meta.get("helpful")) + _int(raw.get("helpful")))
                        new_meta["harmful"] = max(0, _int(meta.get("harmful")) + _int(raw.get("harmful")))
                    text = store.render_topic(new_meta, new_body)
                    if text == store.render_topic(meta, body) and text.strip() == cur.strip():
                        rec["status"] = "noop"
                    else:
                        files[f"{store.MEMORY_DIR}/{fname}"] = text
                        index = store.upsert_index_line(index, fname, new_meta)
                        rec["status"] = "applied"
            else:
                raise ValueError(f"unknown op {op!r}")
        except ValueError as exc:
            rec.update(status="skipped", error=str(exc))
        ops_out.append(rec)
    if index != store.read_index(agent_id):
        files[store.INDEX_REL] = index
    return files, ops_out


def propose(agent_id: str, proposal: Dict[str, Any], *, task_id: Optional[str] = None,
            fire_id: Optional[str] = None) -> Dict[str, Any]:
    """Build the proposal commit + a ``memory`` approval. Returns the outcome record."""
    from services import approvals
    agent = _agent(agent_id) or {}
    d = store.ensure(agent_id)
    files, ops = plan(agent_id, proposal)
    summary = str(proposal.get("summary") or "").strip()[:4000]
    counts = {k: sum(1 for o in ops if o["op"] == k and o.get("status") == "applied") for k in ("ADD", "UPDATE", "DELETE")}
    if not files:
        return {"status": "nothing_to_change", "summary": summary, "operations": ops, "at": _now(), "task_id": task_id}
    with repo.lock_for(d):
        repo.commit_all(d, repo.message("sync: uncommitted memory changes", kind="direct"))
        base = repo.head(d)
        sha = repo.commit_tree_on(d, base, files, repo.message(
            f"reflection: +{counts['ADD']} ~{counts['UPDATE']} -{counts['DELETE']} memories",
            kind="reflection", task=task_id))
        ref = f"refs/proposals/{re.sub(r'[^A-Za-z0-9_.-]+', '_', task_id or sha[:12])}"
        repo.set_ref(d, ref, sha)
        diff = repo.diff_between(d, base, sha)
    for old in _pending_approvals(agent_id):
        try:
            approvals.decide(old["id"], "reject", by="system", note="superseded by a newer reflection")
        except Exception:
            pass
    warn = store.index_warnings(files.get(store.INDEX_REL) or "")
    ap = approvals.create(
        "memory", title=f"Memory update for {agent.get('name') or agent_id}: "
                        f"+{counts['ADD']} ~{counts['UPDATE']} -{counts['DELETE']}",
        body=diff["diff"],
        payload={"agent_id": agent_id, "agent_name": agent.get("name"), "base": base, "commit": sha, "ref": ref,
                 "summary": summary, "operations": ops, "files": diff["files"], "task_id": task_id,
                 "fire_id": fire_id, "warnings": warn, "trigger_id": _load(agent_id).get("trigger_id")},
        trigger_id=_load(agent_id).get("trigger_id"))
    return {"status": "proposed", "approval_id": ap["id"], "summary": summary, "operations": ops,
            "counts": counts, "at": _now(), "task_id": task_id, "commit": sha}


def process(agent_id: str) -> int:
    """Turn finished reflection fires into proposals. Returns how many were handled."""
    st = _load(agent_id)
    tid = st.get("trigger_id")
    if not tid:
        return 0
    from services.task.task_manager import get_task_queue
    from services.triggers import store as tstore
    n = 0
    with _lock(agent_id):
        st = _load(agent_id)
        done = set(st.get("processed") or [])
        for f in reversed(tstore.list_fires(tid, limit=10)):
            task_id = f.get("task_id")
            if not task_id or task_id in done:
                continue
            rec = get_task_queue().get_task_record(task_id)
            if rec is None or rec.get("status") in ("pending", "running"):
                continue
            res = rec.get("result") if isinstance(rec.get("result"), dict) else {}
            if rec.get("status") != "completed":
                out = {"status": "failed", "error": rec.get("error") or rec.get("status"), "at": _now(),
                       "task_id": task_id}
            else:
                prop = parse_proposal(res.get("result") or "")
                if prop is None:
                    out = {"status": "unparsed", "error": "the reply was not the JSON proposal",
                           "excerpt": (res.get("result") or "")[:1000], "at": _now(), "task_id": task_id}
                else:
                    try:
                        out = propose(agent_id, prop, task_id=task_id, fire_id=f.get("id"))
                    except Exception as exc:
                        logger.exception(f"agent {agent_id}: building the memory proposal failed")
                        out = {"status": "failed", "error": str(exc), "at": _now(), "task_id": task_id}
                st["last_reflection_at"] = rec.get("started_at") or rec.get("created_at") or _now()
                st["runs_since"] = 0
            done.add(task_id)
            st["processed"] = (list(st.get("processed") or []) + [task_id])[-50:]
            st["last"] = out
            st["history"] = ([{k: out.get(k) for k in ("status", "at", "task_id", "approval_id", "error", "counts")}]
                             + list(st.get("history") or []))[:20]
            n += 1
        if n:
            _save(agent_id, st)
    return n


def process_all() -> int:
    from services.agent.agent_manager import get_agent_manager
    n = 0
    for a in get_agent_manager().list_agents():
        try:
            n += process(a["id"])
        except Exception:
            logger.exception(f"reflection processing failed for agent {a.get('id')}")
    return n


# ── approval decisions ─────────────────────────────────────────────────────

def _on_decided(ap: Dict[str, Any]) -> None:
    p = ap.get("payload") or {}
    agent_id = p.get("agent_id")
    if not agent_id or not _agent(agent_id):
        return
    d = store.internal_dir(agent_id)
    outcome: Dict[str, Any] = {"approval_id": ap["id"], "at": _now()}
    try:
        if ap.get("status") == "approved" and p.get("commit"):
            res = repo.integrate(d, p.get("base"), p["commit"], f"reflection:{ap['id']}",
                                 trailers={"kind": "reflection", "approval": ap["id"]})
            outcome.update(status="applied", head=res["head"], conflicts=res["conflicts"])
            if res["conflicts"]:
                logger.warning(f"agent {agent_id}: approved memory proposal conflicted with later edits in "
                               f"{res['conflicts']} — kept both versions between conflict markers")
        else:
            outcome["status"] = ap.get("status")
    except Exception as exc:
        logger.exception(f"agent {agent_id}: applying memory proposal {ap['id']} failed")
        outcome.update(status="apply_failed", error=str(exc))
    finally:
        if p.get("ref"):
            repo.delete_ref(d, p["ref"])
    with _lock(agent_id):
        st = _load(agent_id)
        if (st.get("last") or {}).get("approval_id") == ap["id"]:
            st["last"] = {**st["last"], "decision": outcome}
        st["history"] = [{**h, "decision": outcome.get("status")} if h.get("approval_id") == ap["id"] else h
                         for h in st.get("history") or []]
        _save(agent_id, st)


def _register() -> None:
    try:
        from services import approvals
        approvals.register_handler("memory", _on_decided)
    except Exception:
        logger.exception("could not register the memory approval handler")


_register()


# ── status ─────────────────────────────────────────────────────────────────

def status(agent_id: str) -> Dict[str, Any]:
    import config
    try:
        process(agent_id)
    except Exception:
        logger.exception("inline reflection processing failed")
    st = _load(agent_id)
    trig = None
    if st.get("trigger_id"):
        try:
            from services.triggers import service
            t = service.get(st["trigger_id"], reveal=False)
            trig = {"id": t["id"], "status": t.get("status"), "schedule": t.get("schedule"),
                    "next_fire_at": (t.get("state") or {}).get("next_fire_at"),
                    "engine": (t.get("target") or {}).get("engine"), "model": (t.get("target") or {}).get("model"),
                    "is_local": (t.get("target") or {}).get("is_local")}
        except Exception:
            trig = None
    pend = _pending_approvals(agent_id)
    return {"nightly": bool(st.get("nightly")), "after_runs": _after_runs(st),
            "after_runs_default": config.tasks_memory_reflect_after_runs(), "after_runs_custom": "after_runs" in st,
            "runs_since": int(st.get("runs_since") or 0), "last_reflection_at": st.get("last_reflection_at"),
            "running_task_id": _running(agent_id, st), "trigger": trig, "last": st.get("last"),
            "last_fire": st.get("last_fire"), "history": st.get("history") or [],
            "pending_approval_ids": [a["id"] for a in pend], "cron": config.tasks_memory_reflect_cron(),
            "local": bool(config.tasks_memory_reflect_local())}


# ── ticker ─────────────────────────────────────────────────────────────────

def start() -> None:
    """Proxy startup: migrate every agent's memory once, then a 30 s loop that
    turns finished reflection fires into approvals."""
    global _ticker, _stop
    if _ticker is not None and _ticker.is_alive():
        return
    _stop = threading.Event()

    def loop(stop_ev: threading.Event) -> None:
        try:
            n = store.migrate_all()
            logger.info(f"memory: {n} agent(s) checked for the P4 layout")
        except Exception:
            logger.exception("memory migration sweep failed")
        while not stop_ev.wait(TICK_SECONDS):
            try:
                process_all()
            except Exception:
                logger.exception("reflection tick failed (continuing)")

    _ticker = threading.Thread(target=loop, args=(_stop,), name="memory-reflection", daemon=True)
    _ticker.start()


def stop() -> None:
    global _ticker, _stop
    if _stop is not None:
        _stop.set()
    _ticker, _stop = None, None
