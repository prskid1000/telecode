"""telecode's own spans, following the OTel GenAI semantic conventions.

* ``invoke_workflow <job>`` — one per Run (written by the run executor when a
  driver finishes; replaced on every relaunch — retry, gate approval).
* ``invoke_agent <agent|engine>`` — one per Engine Runner call, i.e. one per
  step attempt / map worker / loop iteration or grader / Task-mode task /
  TeleDesign turn. Written when the CLI starts (``status=unset``, no end) and
  replaced when it ends with usage (``gen_ai.usage.*``) and cost.
* ``execute_tool <tool>`` — one per normalised ``tool`` event. The stream gives
  a tool call's start only, so a tool span ends at the next event of the run
  (``telecode.duration_estimated=true``).

Ids: every span of a run shares ``trace_id = sha256("run:<run_id>")[:32]`` (a
task outside a run: ``"task:<task_id>"``), and the workflow span id is
``sha256("wf:<run_id>")[:16]``, so an ``invoke_agent`` span can name its parent
without any coordination. Writes go through the ordered background DB writer —
a CLI's event loop never waits on disk.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger("telecode.services.telemetry.spans")

PROVIDERS = {"claude_code": "anthropic", "codex": "openai", "antigravity": "gcp.gemini"}


def _h(text: str, n: int) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def trace_id_for(run_id: Optional[str] = None, task_id: Optional[str] = None) -> str:
    if run_id:
        return _h(f"run:{run_id}", 32)
    return _h(f"task:{task_id or os.urandom(8).hex()}", 32)


def workflow_span_id(run_id: str) -> str:
    return _h(f"wf:{run_id}", 16)


def new_span_id() -> str:
    return os.urandom(8).hex()


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_ms(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp() * 1000)


def _write(rows) -> None:
    """Queue span rows for the background DB writer (never raises)."""
    try:
        from services.db import writer
        from services.telemetry import store
        writer.submit(lambda conn, rs: store.insert_spans(rs, conn=conn), list(rows))
    except Exception:
        logger.exception("span write failed")


def _enabled() -> bool:
    try:
        from services.telemetry import settings
        return settings.enabled()
    except Exception:
        return False


class AgentSpan:
    """``invoke_agent`` for one engine run + its ``execute_tool`` children."""

    def __init__(self, *, engine: str, correlation: Mapping[str, Any], model: Optional[str] = None,
                 is_local: bool = False, session_id: Optional[str] = None):
        c = {k: (str(v) if v not in (None, "") else None) for k, v in (correlation or {}).items()}
        self.c = c
        self.engine = engine
        self.model = model
        self.trace_id = trace_id_for(c.get("run_id"), c.get("task_id"))
        self.span_id = new_span_id()
        self.parent = workflow_span_id(c["run_id"]) if c.get("run_id") else None
        self.start = now_ms()
        self.attrs: Dict[str, Any] = {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.provider.name": PROVIDERS.get(engine, engine),
            "gen_ai.agent.id": c.get("agent_id") or None,
            "gen_ai.agent.name": c.get("agent_name") or None,
            "gen_ai.request.model": model or None,
            "gen_ai.conversation.id": None,
            "telecode.engine": engine,
            "telecode.is_local": bool(is_local),
            "telecode.workspace_id": session_id,
            "telecode.attempt": c.get("attempt"),
            "telecode.source": c.get("source"),
        }
        self._lock = threading.Lock()
        self._tool: Optional[Dict[str, Any]] = None
        self.tools = 0
        self.last_usage: Optional[Dict[str, Any]] = None   # the runner's latest usage event
        self._write_self(status="unset", end=None)

    # ── rows ──
    def _row(self, **kw) -> Dict[str, Any]:
        c = self.c
        base = {"trace_id": self.trace_id, "source": "telecode", "task_id": c.get("task_id"),
                "run_id": c.get("run_id"), "step_id": c.get("step_id"), "agent_id": c.get("agent_id"),
                "job_id": c.get("job_id"), "trigger_id": c.get("trigger_id"), "engine": self.engine,
                "model": self.model, "received_ms": now_ms()}
        base.update(kw)
        return base

    def _write_self(self, *, status: str, end: Optional[int], message: Optional[str] = None,
                    usage: Optional[Dict[str, Any]] = None) -> None:
        u = usage or {}
        attrs = {k: v for k, v in self.attrs.items() if v is not None}
        name = f"invoke_agent {self.c.get('agent_name') or self.c.get('agent_id') or self.engine}"
        _write([self._row(span_id=self.span_id, parent_span_id=self.parent, name=name, operation="invoke_agent",
                          kind="internal", start_ms=self.start, end_ms=end,
                          duration_ms=(end - self.start) if end else None, status=status, status_message=message,
                          input_tokens=u.get("input_tokens"), output_tokens=u.get("output_tokens"),
                          cache_read_tokens=u.get("cache_read_tokens"), cache_write_tokens=u.get("cache_write_tokens"),
                          cost_usd=u.get("cost_usd"), attributes=json.dumps(attrs, default=str))])

    def _close_tool(self, at: int, status: str = "ok") -> None:
        t = self._tool
        if not t:
            return
        self._tool = None
        t.update(end_ms=at, duration_ms=max(0, at - t["start_ms"]), status=status)
        _write([t])

    # ── event hooks (runner) ──
    def on_event(self, evt: Dict[str, Any]) -> None:
        kind = evt.get("kind")
        if kind in ("delta", "usage", "start"):
            return
        at = now_ms()
        with self._lock:
            if kind == "tool":
                self._close_tool(at)
                self.tools += 1
                name = str(evt.get("tool") or evt.get("name") or "?")
                attrs = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": name,
                         "gen_ai.tool.type": "function" if not name.startswith("mcp__") else "extension",
                         "telecode.summary": str(evt.get("summary") or "")[:300],
                         "telecode.duration_estimated": True}
                self._tool = self._row(span_id=new_span_id(), parent_span_id=self.span_id,
                                       name=f"execute_tool {name}", operation="execute_tool", kind="internal",
                                       start_ms=at, end_ms=None, duration_ms=None, status="unset",
                                       tool_name=name, attributes=json.dumps(attrs, default=str))
            elif kind in ("narrative", "todo", "warning", "retry", "done", "error"):
                self._close_tool(at, "error" if kind == "error" else "ok")

    def set_model(self, model: Optional[str]) -> None:
        """The model the CLI reports (e.g. ``claude-haiku-4-5-…`` for ``--model haiku``) —
        the ``model`` column groups by it; the requested alias stays in gen_ai.request.model."""
        if model:
            self.model = model
            self.attrs["gen_ai.response.model"] = model

    def finish(self, *, status: str, message: Optional[str] = None, tokens: Optional[Dict[str, Any]] = None,
               cost_usd: Optional[float] = None, engine_session_id: Optional[str] = None,
               num_turns: Optional[int] = None) -> None:
        end = now_ms()
        with self._lock:
            self._close_tool(end, "error" if status == "error" else "ok")
        t = tokens or {}
        usage = {"input_tokens": int(t.get("total_input_incl_cache") or t.get("input") or 0) if t else None,
                 "output_tokens": int(t.get("output") or 0) if t else None,
                 "cache_read_tokens": int(t.get("cache_read") or 0) if t else None,
                 "cache_write_tokens": int(t.get("cache_write") or 0) if t else None,
                 "cost_usd": float(cost_usd) if isinstance(cost_usd, (int, float)) else None}
        if t:
            self.attrs.update({"gen_ai.usage.input_tokens": usage["input_tokens"],
                               "gen_ai.usage.output_tokens": usage["output_tokens"],
                               "gen_ai.usage.cache_read.input_tokens": usage["cache_read_tokens"],
                               "gen_ai.usage.cache_creation.input_tokens": usage["cache_write_tokens"]})
        if engine_session_id:
            self.attrs["gen_ai.conversation.id"] = engine_session_id
        if num_turns:
            self.attrs["telecode.num_turns"] = num_turns
        self.attrs["telecode.tool_calls"] = self.tools
        if status == "error" and message:
            self.attrs["error.type"] = message.split(":", 1)[0][:80]
        self._write_self(status=status, end=end, message=(message or "")[:500] or None, usage=usage)


def start_agent_span(**kw) -> Optional[AgentSpan]:
    if not _enabled():
        return None
    try:
        return AgentSpan(**kw)
    except Exception:
        logger.exception("could not open an invoke_agent span")
        return None


def record_workflow(run: Dict[str, Any]) -> None:
    """Upsert the run's ``invoke_workflow`` span from the run record."""
    if not _enabled() or not run or not run.get("run_id"):
        return
    rid = run["run_id"]
    start = iso_ms(run.get("started_at") or run.get("created_at")) or now_ms()
    end = iso_ms(run.get("completed_at"))
    st = run.get("status")
    status = "ok" if st == "completed" else "unset" if st in ("pending", "running", "awaiting_input") else "error"
    u = run.get("usage") or {}
    snap = run.get("job_snapshot") or {}
    attrs = {"gen_ai.operation.name": "invoke_workflow", "gen_ai.workflow.name": snap.get("title") or None,
             "telecode.run_status": st, "telecode.mode": run.get("mode"), "telecode.source": run.get("source"),
             "telecode.verdict": run.get("verdict"), "telecode.process_ok": run.get("process_ok"),
             "telecode.steps": len(run.get("steps") or []), "telecode.cost_complete": u.get("cost_complete")}
    row = {"trace_id": trace_id_for(rid), "span_id": workflow_span_id(rid), "parent_span_id": None,
           "name": f"invoke_workflow {snap.get('title') or rid[:8]}", "operation": "invoke_workflow",
           "kind": "internal", "source": "telecode", "start_ms": start, "end_ms": end,
           "duration_ms": (end - start) if end else None, "status": status,
           "status_message": None if status != "error" else st, "task_id": None, "run_id": rid, "step_id": None,
           "agent_id": None, "job_id": run.get("job_id"), "trigger_id": run.get("trigger_id"), "engine": None,
           "model": None, "tool_name": None, "input_tokens": u.get("input_tokens"),
           "output_tokens": u.get("output_tokens"), "cache_read_tokens": u.get("cache_read_tokens"),
           "cache_write_tokens": u.get("cache_write_tokens"), "cost_usd": u.get("cost_usd"),
           "attributes": json.dumps({k: v for k, v in attrs.items() if v is not None}, default=str),
           "received_ms": now_ms()}
    _write([row])
