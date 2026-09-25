"""``approve_tool`` — Claude Code's ``--permission-prompt-tool`` for permission mode ``ask`` (P5).

A trigger / job / task with ``permission_mode: ask`` runs ``claude -p`` with
``--permission-prompt-tool mcp__telecode_safety__approve_tool`` and a per-run
``--mcp-config`` naming this server (``services/engine/adapters/claude.py``).
Whenever Claude would ask for permission it calls this tool with
``{tool_name, input, tool_use_id}``; the tool opens an approval of kind ``tool``
(``services/approvals.py``) — the web inbox and the Telegram notifier both
show it — and waits:

* approved → ``{"behavior": "allow", "updatedInput": <input>}`` (an approval
  approved with edited text that parses as a JSON object replaces the input);
* rejected → ``{"behavior": "deny", "message": "Denied by <who>: <note>"}``;
* no decision within ``safety.approval_timeout_sec`` (default 600), or the
  task stopped meanwhile → the approval is cancelled and the call is denied.

The run's ids arrive as ``X-Telecode-Task|Run|Step|Agent|Trigger`` headers on
the MCP HTTP request (set in the per-run config), so the approval is linked to
its run and step. Returned as JSON in a single text block (no structured
content), the shape Claude Code expects.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

from mcp.server.fastmcp import Context

from mcp_server.app import mcp_app

logger = logging.getLogger("telecode.mcp_server.approvals")

POLL_SEC = 0.5
BODY_CAP = 16 * 1024


def _headers(ctx: Optional[Context]) -> Dict[str, str]:
    try:
        req = ctx.request_context.request  # starlette Request on the streamable-HTTP transport
        return {k.lower(): v for k, v in (req.headers or {}).items()} if req is not None else {}
    except Exception:
        return {}


def _timeout() -> float:
    try:
        from services.telemetry import settings
        return settings.approval_timeout_sec()
    except Exception:
        return 600.0


def _task_stopped(task_id: Optional[str]) -> bool:
    if not task_id:
        return False
    try:
        from services.task.task_manager import get_task_queue
        q = get_task_queue()
        t = q.get_task(task_id)
        return t is not None and not q.is_active(task_id)
    except Exception:
        return False


def _describe(tool_name: str, tool_input: Any) -> str:
    try:
        from services.engine.adapters.base import describe_tool
        head = describe_tool(tool_name, tool_input)
    except Exception:
        head = tool_name
    try:
        blob = json.dumps(tool_input, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        blob = str(tool_input)
    if len(blob) > BODY_CAP:
        blob = blob[:BODY_CAP] + "\n… (truncated)"
    return f"{head}\n\n{blob}"


def decision_response(ap: Dict[str, Any], tool_input: Any, timed_out: bool = False) -> Dict[str, Any]:
    status = ap.get("status")
    if status == "approved":
        updated = tool_input if isinstance(tool_input, dict) else {}
        edited = (ap.get("edited_text") or "").strip()
        if edited:
            try:
                parsed = json.loads(edited)
                if isinstance(parsed, dict):
                    updated = parsed
            except ValueError:
                pass
        return {"behavior": "allow", "updatedInput": updated}
    who = ap.get("decided_by") or "someone"
    note = (ap.get("decision_note") or "").strip()
    if status == "rejected":
        return {"behavior": "deny", "message": f"Denied by {who}" + (f": {note}" if note else ".")}
    if timed_out:
        return {"behavior": "deny", "message": f"No one approved this within {int(_timeout())}s — denied."}
    return {"behavior": "deny", "message": "The approval was cancelled" + (f" ({note})" if note else "") + " — denied."}


async def request_approval(tool_name: str, tool_input: Any, tool_use_id: str = "",
                           headers: Optional[Dict[str, str]] = None,
                           timeout: Optional[float] = None) -> Dict[str, Any]:
    from services import approvals
    h = headers or {}
    ids = {k: (h.get(f"x-telecode-{k}") or None) for k in ("task", "run", "step", "agent", "trigger")}
    timeout = _timeout() if timeout is None else timeout
    ap = await asyncio.to_thread(
        approvals.create, "tool", title=f"Allow {tool_name}?", body=_describe(tool_name, tool_input),
        payload={"tool_name": tool_name, "input": tool_input, "tool_use_id": tool_use_id or None,
                 "task_id": ids["task"], "agent_id": ids["agent"], "timeout_sec": timeout},
        run_id=ids["run"], step_id=ids["step"], trigger_id=ids["trigger"])
    logger.info(f"approve_tool: waiting on approval {ap['id'][:8]} for {tool_name} (task {ids['task']})")
    deadline = time.monotonic() + timeout
    while True:
        cur = await asyncio.to_thread(approvals.get, ap["id"]) or ap
        if cur.get("status") != "pending":
            return decision_response(cur, tool_input)
        if time.monotonic() > deadline:
            cur = await asyncio.to_thread(approvals.cancel, ap["id"], f"no decision within {int(timeout)}s") or cur
            if cur.get("status") != "cancelled":      # decided in the last instant
                return decision_response(cur, tool_input)
            return decision_response(cur, tool_input, timed_out=True)
        if _task_stopped(ids["task"]):
            cur = await asyncio.to_thread(approvals.cancel, ap["id"], "the task stopped") or cur
            return decision_response(cur, tool_input)
        await asyncio.sleep(POLL_SEC)


# structured_output=False: Claude Code requires the result to be exactly one text block
# ("Permission prompt tool returned an invalid result. Expected a single text block param
# with type="text" and a string text value." — seen on 2.1.282 when FastMCP also attached
# structuredContent for the str return).
@mcp_app.tool(structured_output=False)
async def approve_tool(tool_name: str, input: Optional[Dict[str, Any]] = None, tool_use_id: str = "",
                       ctx: Context = None) -> str:
    """Permission prompt for an autonomous Claude Code run (``--permission-prompt-tool``).

    Asks a person — telecode's approvals inbox and Telegram — whether the agent may
    run ``tool_name`` with ``input``, and waits for the answer (denies on timeout).

    Args:
        tool_name: The tool Claude wants to use (e.g. Bash, Edit, mcp__x__y).
        input: The tool's arguments.
        tool_use_id: Claude's id for this tool call.

    Returns:
        JSON: {"behavior": "allow", "updatedInput": {...}} or {"behavior": "deny", "message": "..."}.
    """
    res = await request_approval(tool_name, input if input is not None else {}, tool_use_id, _headers(ctx))
    return json.dumps(res, ensure_ascii=False)
