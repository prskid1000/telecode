"""Engine-session lineage index (``sessions_index``).

One row per CLI conversation. The *scope* of a row is the resume scope —
(namespace, workspace_id, agent_id, engine, is_local), the same key
``task_utils.resume_scope_key`` stores resume ids under — so a workspace with
two agents has two independent rows. When a scope's conversation changes the
previous row is marked ``superseded`` and the new row's ``parent_id`` points at
it. ``lineage`` says why the conversation changed:

* ``fresh``    — first run in the scope, or a step with session policy fresh
* ``resume``   — the resumed id could not continue and the CLI started over
* ``fork``     — ``--fork-session`` / ``codex exec fork``; ``forked_from`` is
  the row of the conversation that was forked (any scope)
* ``rotation`` — the old conversation passed ``tasks.rotate_after_tokens`` /
  ``rotate_after_fires`` and handed off; ``rotated_from`` is its row
* ``ephemeral`` — a throwaway session (parallel / ephemeral step)

Cumulative tokens/cost are summed per row; ``cumulative_tokens`` counts budget
tokens (input + cache writes + output, cache reads excluded — see
``services.run.budget``) and drives rotation; ``cost_complete`` goes false once
any run reported no cost (Codex, Antigravity).
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from services.db.core import connect, now_iso

LINEAGES = ("fresh", "resume", "fork", "rotation", "ephemeral")


def _row(r) -> Dict[str, Any]:
    d = dict(r)
    d["is_local"] = bool(d["is_local"])
    d["cost_complete"] = bool(d["cost_complete"])
    return d


def find_by_engine_session(engine_session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Newest row for a CLI conversation id (any scope)."""
    if not engine_session_id:
        return None
    r = connect().execute("SELECT * FROM sessions_index WHERE engine_session_id=? ORDER BY updated_at DESC LIMIT 1",
                          (engine_session_id,)).fetchone()
    return _row(r) if r else None


def get(row_id: str) -> Optional[Dict[str, Any]]:
    r = connect().execute("SELECT * FROM sessions_index WHERE id=?", (row_id,)).fetchone()
    return _row(r) if r else None


def record_run(*, namespace: Optional[str], workspace_id: Optional[str], agent_id: Optional[str],
               engine: str, is_local: bool, engine_session_id: Optional[str], kind: str,
               created_by: str, task_id: Optional[str], tokens: Optional[Dict[str, Any]] = None,
               cost_usd: Optional[float] = None, lineage: Optional[str] = None,
               policy: Optional[str] = None, forked_from_session: Optional[str] = None,
               rotated_from: Optional[str] = None) -> Optional[str]:
    """Add one finished engine run to the lineage index. Returns the row id.

    ``forked_from_session`` is the CLI id that was forked (its row is looked
    up in any scope); ``rotated_from`` is the row id of the rotated-out
    conversation."""
    if not engine_session_id:
        return None
    conn = connect()
    now = now_iso()
    tokens = tokens or {}
    tin = int(tokens.get("total_input_incl_cache") or tokens.get("input") or 0)
    tout = int(tokens.get("output") or 0)
    tbud = int(tokens.get("input") or 0) + int(tokens.get("cache_write") or 0) + tout
    scope = (namespace or "", workspace_id or "", agent_id or "", engine, 1 if is_local else 0)
    cur = conn.execute(
        "SELECT * FROM sessions_index WHERE namespace=? AND workspace_id=? AND agent_id=? AND engine=? "
        "AND is_local=? AND status='active' ORDER BY updated_at DESC LIMIT 1", scope).fetchone()
    if cur and cur["engine_session_id"] == engine_session_id:
        conn.execute(
            "UPDATE sessions_index SET cumulative_input_tokens=cumulative_input_tokens+?, "
            "cumulative_output_tokens=cumulative_output_tokens+?, cumulative_tokens=cumulative_tokens+?, "
            "cumulative_cost_usd=cumulative_cost_usd+?, cost_complete=cost_complete AND ?, runs_count=runs_count+1, "
            "last_task_id=?, updated_at=? WHERE id=?",
            (tin, tout, tbud, float(cost_usd or 0), 0 if cost_usd is None else 1, task_id, now, cur["id"]))
        return cur["id"]
    parent = cur["id"] if cur else None
    if parent:
        conn.execute("UPDATE sessions_index SET status='superseded', updated_at=? WHERE id=?", (now, parent))
    forked_row = find_by_engine_session(forked_from_session) if forked_from_session else None
    if lineage not in LINEAGES:
        lineage = "fork" if forked_row else "rotation" if rotated_from else "resume" if parent else "fresh"
    rid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO sessions_index (id, namespace, workspace_id, agent_id, engine, is_local, engine_session_id, "
        "parent_id, kind, created_by, cumulative_input_tokens, cumulative_output_tokens, cumulative_cost_usd, "
        "cost_complete, runs_count, last_task_id, status, created_at, updated_at, lineage, policy, forked_from, "
        "rotated_from, cumulative_tokens) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,'active',?,?,?,?,?,?,?)",
        (rid, *scope[:4], scope[4], engine_session_id, parent, kind, created_by, tin, tout,
         float(cost_usd or 0), 0 if cost_usd is None else 1, task_id, now, now, lineage, policy,
         forked_row["id"] if forked_row else None, rotated_from, tbud))
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
    return [_row(r) for r in connect().execute(sql, args)]
