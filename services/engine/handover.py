"""Cross-engine continue (P5): carry a workspace's work over to another engine.

A CLI conversation cannot move between engines, so the state travels as a
**neutral context package** built from what telecode already keeps:

* the latest **handoff** of that workspace (a run step's structured handoff —
  the one named by ``run_id``/``step_id``, else the newest — or a Task-mode
  task's rotation handoff / structured output); with none, the last reply
  (capped) stands in;
* the **workspace diff** since the session's first snapshot (a fresh snapshot
  is taken now, so it is exact), plus the changed-file list;
* ``PROGRESS.md`` from the workspace root, if present.

The package is written in full to ``<workspace>/.telecode/continue/<ts>-<engine>.{json,md}``
(``.telecode/`` is excluded from snapshots) and a capped rendering is prepended
to the prompt of a new task on the target engine in the **same workspace**, in
a fresh conversation. Its ``sessions_index`` row gets ``lineage=engine_switch``
with ``switched_from`` = the row of the conversation it continues.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("telecode.services.engine.handover")

DIFF_PROMPT_CAP = 24 * 1024
DIFF_FILE_CAP = 512 * 1024
PROGRESS_CAP = 16 * 1024
REPLY_CAP = 8 * 1024
ENGINE_LABELS = {"claude_code": "Claude Code", "codex": "Codex", "antigravity": "Antigravity"}

DEFAULT_ASK = ("Continue this work from where it stopped. Check the workspace against the package above first "
               "(the files are the source of truth), keep the decisions already made, and finish what is left.")


class ContinueError(ValueError):
    """Bad request (unknown session, bad engine)."""


class SessionBusy(RuntimeError):
    """A task is already running in the workspace."""


def _cap(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    head = text[: n * 3 // 4]
    tail = text[-(n // 4):]
    return f"{head}\n… [{len(text) - len(head) - len(tail)} chars omitted — full text in the package file] …\n{tail}"


def _step_handoff(run_id: str, step_id: Optional[str]) -> Optional[Dict[str, Any]]:
    from services.run.run_store import get_run_store
    run = get_run_store().get_run(run_id)
    if not run:
        return None
    steps = [s for s in run.get("steps") or [] if s.get("handoff")]
    if step_id:
        steps = [s for s in steps if s.get("step_id") == step_id]
    if not steps:
        return None
    s = steps[-1]
    return {"source": "run_step", "run_id": run_id, "step_id": s["step_id"], "name": s.get("name") or s.get("agent_name"),
            "engine": s.get("engine"), "engine_session_id": s.get("engine_session_id"),
            "handoff": s["handoff"], "files_changed": s.get("files_changed") or [], "at": s.get("completed_at")}


def latest_handoff(sid: str, *, run_id: Optional[str] = None, step_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The newest handoff produced in this workspace (see module doc)."""
    if run_id:
        return _step_handoff(run_id, step_id)
    from services.db.core import connect
    c = connect()
    best: Optional[Dict[str, Any]] = None
    try:
        r = c.execute("SELECT run_id, step_id FROM run_steps WHERE json_extract(data, '$.session_id') = ? "
                      "AND handoff IS NOT NULL ORDER BY json_extract(data, '$.completed_at') DESC LIMIT 1",
                      (sid,)).fetchone()
        if r:
            best = _step_handoff(r["run_id"], r["step_id"])
    except Exception:
        logger.exception("handoff lookup in run_steps failed")
    # A newer Task-mode task in the same workspace wins over an old run step.
    try:
        from services.db import task_repo  # noqa: F401 — the table is written by it
        rows = c.execute("SELECT task_id, task_type, completed_at, result, metadata FROM tasks WHERE session_id=? "
                         "AND status='completed' ORDER BY completed_at DESC LIMIT 5", (sid,)).fetchall()
    except Exception:
        rows = []
    for r in rows:
        if best and (best.get("at") or "") >= (r["completed_at"] or ""):
            break
        try:
            res = json.loads(r["result"]) if r["result"] else {}
        except ValueError:
            res = {}
        so = res.get("structured_output") if isinstance(res, dict) else None
        ho = (res.get("rotation") or {}).get("handoff") if isinstance(res, dict) else None
        if isinstance(so, dict) and so.get("summary"):
            ho = so
        text = (res.get("result") or "") if isinstance(res, dict) else ""
        if ho or text:
            best = {"source": "task", "task_id": r["task_id"], "task_type": r["task_type"], "at": r["completed_at"],
                    "handoff": ho, "last_reply": None if ho else _cap(str(text), REPLY_CAP)}
            break
    return best


def source_conversation(sid: str, ns: Optional[str], exclude_engine: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The workspace's most recent active CLI conversation (sessions_index row)."""
    from services.db.core import connect
    q = "SELECT * FROM sessions_index WHERE workspace_id=? AND namespace=? AND status='active'"
    args: list = [sid, ns or ""]
    if exclude_engine:
        q += " AND engine != ?"
        args.append(exclude_engine)
    r = connect().execute(q + " ORDER BY updated_at DESC LIMIT 1", args).fetchone()
    return dict(r) if r else None


def workspace_diff(sid: str, ns: Optional[str], work_dir: Path, label: str) -> Dict[str, Any]:
    from services import snapshots
    key = snapshots.key_for(sid, ns)
    now_sha = snapshots.take(key, work_dir, label, {"phase": "manual", "reason": "engine_switch"})
    entries = snapshots.log(key, limit=100000)
    if not now_sha or len(entries) < 2:
        return {"available": bool(now_sha), "first": None, "now": now_sha, "files": [], "diff": "",
                "note": "no earlier snapshot of this workspace — nothing to diff against"}
    first = entries[-1]["sha"]
    files = snapshots.changed_files(key, first, now_sha)
    try:
        d = snapshots.diff(key, first, now_sha, max_bytes=DIFF_FILE_CAP)
    except snapshots.SnapshotError as exc:
        d = {"diff": "", "truncated": False, "error": str(exc)}
    return {"available": True, "first": first, "first_at": entries[-1].get("created_at"), "now": now_sha,
            "files": files, "diff": d.get("diff") or "", "truncated": bool(d.get("truncated"))}


def build_package(sid: str, ns: Optional[str], *, target_engine: str, run_id: Optional[str] = None,
                  step_id: Optional[str] = None) -> Dict[str, Any]:
    from services.session import session_store
    if not session_store.exists(sid, namespace=ns):
        raise ContinueError("session not found")
    work_dir = Path(session_store._session_dir(sid, namespace=ns))
    src = source_conversation(sid, ns, exclude_engine=None)
    ho = latest_handoff(sid, run_id=run_id, step_id=step_id)
    diff = workspace_diff(sid, ns, work_dir, f"engine switch → {target_engine}")
    progress = None
    p = work_dir / "PROGRESS.md"
    if p.is_file():
        try:
            progress = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            progress = None
    from_engine = (ho or {}).get("engine") or (src or {}).get("engine")
    if not from_engine and ho and ho.get("task_type"):
        from_engine = {"CLAUDE_CODE": "claude_code", "CODEX": "codex", "ANTIGRAVITY": "antigravity"}.get(ho["task_type"])
    pkg = {"version": 1, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "workspace_id": sid, "namespace": ns, "from_engine": from_engine, "to_engine": target_engine,
           "from_conversation": {"row_id": (src or {}).get("id"), "engine": (src or {}).get("engine"),
                                 "engine_session_id": (src or {}).get("engine_session_id")} if src else None,
           "handoff": ho, "workspace_diff": diff, "progress_md": progress}
    d = work_dir / ".telecode" / "continue"
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{target_engine}"
    (d / f"{stem}.json").write_text(json.dumps(pkg, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    md = render_markdown(pkg)
    (d / f"{stem}.md").write_text(md, encoding="utf-8")
    pkg["paths"] = {"json": f".telecode/continue/{stem}.json", "md": f".telecode/continue/{stem}.md"}
    return pkg


def _handoff_text(ho: Optional[Dict[str, Any]]) -> str:
    if not ho:
        return "(no handoff was recorded in this workspace)"
    if ho.get("handoff"):
        from services.run.handoff import render_block
        return render_block({"name": ho.get("name") or ho.get("source"), "handoff": ho["handoff"],
                             "files_changed": ho.get("files_changed") or []})
    return f"<last_reply>\n{ho.get('last_reply') or ''}\n</last_reply>"


def render_markdown(pkg: Dict[str, Any]) -> str:
    d = pkg.get("workspace_diff") or {}
    lines = [f"# Engine switch: {ENGINE_LABELS.get(pkg.get('from_engine') or '', pkg.get('from_engine') or '?')} → "
             f"{ENGINE_LABELS.get(pkg['to_engine'], pkg['to_engine'])}", "", f"Created {pkg['created_at']}", "",
             "## Latest handoff", "", _handoff_text(pkg.get("handoff")), "",
             "## Files changed since the first snapshot", ""]
    lines += [f"- {f.get('change')}: {f.get('path')}" for f in d.get("files") or []] or ["(none)"]
    lines += ["", "## Workspace diff", "", "```diff", d.get("diff") or d.get("note") or "", "```", ""]
    if pkg.get("progress_md"):
        lines += ["## PROGRESS.md", "", pkg["progress_md"], ""]
    return "\n".join(lines)


def render_prompt(pkg: Dict[str, Any], ask: Optional[str] = None) -> str:
    d = pkg.get("workspace_diff") or {}
    src = ENGINE_LABELS.get(pkg.get("from_engine") or "", pkg.get("from_engine") or "another engine")
    parts = ["<engine_switch>",
             f"This workspace's work was done so far by {src}; you ({ENGINE_LABELS.get(pkg['to_engine'], pkg['to_engine'])}) "
             "continue it in a fresh conversation. You have no memory of the earlier conversation — this package is "
             f"it. The full package (untruncated diff) is at {pkg['paths']['md']}.",
             _handoff_text(pkg.get("handoff"))]
    files = d.get("files") or []
    if files:
        parts += [f'<files_changed since="first snapshot" count="{len(files)}">',
                  *(f"{f.get('change')}: {f.get('path')}" for f in files[:200]), "</files_changed>"]
    if d.get("diff"):
        parts += ["<workspace_diff>", _cap(d["diff"], DIFF_PROMPT_CAP), "</workspace_diff>"]
    elif d.get("note"):
        parts.append(f"<workspace_diff note=\"{d['note']}\" />")
    if pkg.get("progress_md"):
        parts += ["<progress_md>", _cap(pkg["progress_md"], PROGRESS_CAP), "</progress_md>"]
    parts.append("</engine_switch>")
    return "\n".join(parts) + "\n\n" + (ask or DEFAULT_ASK).strip()


def continue_session(sid: str, *, engine: str, model: Optional[str] = None, is_local: bool = False,
                     ns: Optional[str] = None, prompt: Optional[str] = None, run_id: Optional[str] = None,
                     step_id: Optional[str] = None) -> Dict[str, Any]:
    from services.task.engine_map import ENGINE_TO_TASK_TYPE
    from services.task.task_manager import get_task_queue
    eng = (engine or "").strip().lower()
    if eng not in ENGINE_TO_TASK_TYPE:
        raise ContinueError(f"engine must be one of {tuple(ENGINE_TO_TASK_TYPE)}")
    queue = get_task_queue()
    if queue.session_has_active_task(sid, ns):
        raise SessionBusy("a task is running in this workspace — wait for it or cancel it")
    pkg = build_package(sid, ns, target_engine=eng, run_id=run_id, step_id=step_id)
    src_row = (pkg.get("from_conversation") or {}).get("row_id")
    text = render_prompt(pkg, prompt)
    params: Dict[str, Any] = {"prompt": text, "is_local": bool(is_local),
                              "step_ctl": {"session": "fresh", "policy": "engine_switch",
                                           "lineage": {"lineage": "engine_switch", "switched_from": src_row}}}
    if model:
        params["model"] = model
    md = {"source": "continue", "engine": eng,
          "continued_from": {"engine": pkg.get("from_engine"), "row_id": src_row,
                             "engine_session_id": (pkg.get("from_conversation") or {}).get("engine_session_id"),
                             "run_id": (pkg.get("handoff") or {}).get("run_id"),
                             "step_id": (pkg.get("handoff") or {}).get("step_id"),
                             "package": pkg["paths"]["md"]}}
    task_id = queue.submit_task(task_type=ENGINE_TO_TASK_TYPE[eng], params=params, metadata=md,
                                session_id=sid, session_namespace=ns)
    d = pkg.get("workspace_diff") or {}
    return {"task_id": task_id, "session_id": sid, "namespace": ns, "from_engine": pkg.get("from_engine"),
            "to_engine": eng, "switched_from": src_row, "package": pkg["paths"],
            "handoff_source": (pkg.get("handoff") or {}).get("source"),
            "files_changed": len(d.get("files") or []), "diff_truncated": bool(d.get("truncated")),
            "progress_md": bool(pkg.get("progress_md")), "prompt_chars": len(text)}
