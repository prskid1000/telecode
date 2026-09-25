"""Engine-session lineage index (``sessions_index``).

One row per CLI conversation. The *scope* of a row is the resume scope —
(namespace, workspace_id, agent_id, engine, is_local), the same key
``task_utils.resume_scope_key`` stores resume ids under — so a workspace with
two agents has two independent rows. When a scope's conversation changes (first
run, or a resume that could not continue and started fresh) the previous row is
marked ``superseded`` and the new row's ``parent_id`` points at it.

Cumulative tokens/cost are summed per row; ``cost_complete`` goes false once
any run reported no cost (Codex, Antigravity).
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from services.db.core import connect, now_iso


def record_run(*, namespace: Optional[str], workspace_id: Optional[str], agent_id: Optional[str],
               engine: str, is_local: bool, engine_session_id: Optional[str], kind: str,
               created_by: str, task_id: Optional[str], tokens: Optional[Dict[str, Any]] = None,
               cost_usd: Optional[float] = None) -> Optional[str]:
    """Add one finished engine run to the lineage index. Returns the row id."""
    if not engine_session_id:
        return None
    conn = connect()
    now = now_iso()
    tokens = tokens or {}
    tin = int(tokens.get("total_input_incl_cache") or tokens.get("input") or 0)
    tout = int(tokens.get("output") or 0)
    scope = (namespace or "", workspace_id or "", agent_id or "", engine, 1 if is_local else 0)
    cur = conn.execute(
        "SELECT * FROM sessions_index WHERE namespace=? AND workspace_id=? AND agent_id=? AND engine=? "
        "AND is_local=? AND status='active' ORDER BY updated_at DESC LIMIT 1", scope).fetchone()
    if cur and cur["engine_session_id"] == engine_session_id:
        conn.execute(
            "UPDATE sessions_index SET cumulative_input_tokens=cumulative_input_tokens+?, "
            "cumulative_output_tokens=cumulative_output_tokens+?, cumulative_cost_usd=cumulative_cost_usd+?, "
            "cost_complete=cost_complete AND ?, runs_count=runs_count+1, last_task_id=?, updated_at=? WHERE id=?",
            (tin, tout, float(cost_usd or 0), 0 if cost_usd is None else 1, task_id, now, cur["id"]))
        return cur["id"]
    parent = cur["id"] if cur else None
    if parent:
        conn.execute("UPDATE sessions_index SET status='superseded', updated_at=? WHERE id=?", (now, parent))
    rid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO sessions_index (id, namespace, workspace_id, agent_id, engine, is_local, engine_session_id, "
        "parent_id, kind, created_by, cumulative_input_tokens, cumulative_output_tokens, cumulative_cost_usd, "
        "cost_complete, runs_count, last_task_id, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,'active',?,?)",
        (rid, *scope[:4], scope[4], engine_session_id, parent, kind, created_by, tin, tout,
         float(cost_usd or 0), 0 if cost_usd is None else 1, task_id, now, now))
    return rid


def list_sessions(workspace_id: Optional[str] = None, namespace: Optional[str] = None,
                  limit: int = 200) -> List[Dict[str, Any]]:
    sql, args = "SELECT * FROM sessions_index WHERE 1=1", []
    if workspace_id is not None:
        sql += " AND workspace_id=?"
        args.append(workspace_id)
    if namespace is not None:
        sql += " AND namespace=?"
        args.append(namespace or "")
    sql += " ORDER BY updated_at DESC LIMIT ?"
    args.append(int(limit))
    out = []
    for r in connect().execute(sql, args):
        d = dict(r)
        d["is_local"] = bool(d["is_local"])
        d["cost_complete"] = bool(d["cost_complete"])
        out.append(d)
    return out
