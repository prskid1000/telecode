"""Project chats and their turn records (docs/teledesign-contract.md §3, §4.2, §4.5).

    chats/index.json        [{id, title, engine, is_local, effort, session_id, created_at, updated_at,
                              continuing_from?}]
    chats/<cid>.jsonl       one turn record per line (user and assistant), rewritten atomically
    chats/<cid>.md          transcript in handoff format, rewritten after every turn

Each chat is one permanent direct task session in namespace `design`
(`<pid>-<cid>`), so a CLI resume id survives across turns and restarts.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from services.design import store

logger = logging.getLogger("telecode.services.design.chats")

SESSION_NAMESPACE = "design"
ENGINES = ("claude_code", "codex", "antigravity")
EFFORTS = (None, "low", "medium", "high", "xhigh", "max")
MAX_CHATS = 100
# A project chat is permanent: effectively no idle expiry.
SESSION_IDLE_SECONDS = 10 * 365 * 24 * 3600

_lock = threading.RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cdir(pid: str) -> Optional[Path]:
    d = store.project_dir(pid)
    return d / "chats" if d else None


def session_id_for(pid: str, cid: str) -> str:
    # session_store ids: [a-zA-Z0-9_.-]{1,128}; two 32-hex ids + '-' = 65.
    return f"{pid}-{cid}"[:128]


def default_engine() -> str:
    e = config.get_nested("design.default_engine", "claude_code")
    return e if e in ENGINES else "claude_code"


def _clean_opts(data: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    if "title" in data and isinstance(data["title"], str) and data["title"].strip():
        out["title"] = data["title"].strip()[:120]
    if "engine" in data:
        if data["engine"] not in ENGINES:
            raise ValueError("invalid engine")
        out["engine"] = data["engine"]
    if "is_local" in data:
        out["is_local"] = bool(data["is_local"])
    if "effort" in data:
        if data["effort"] not in EFFORTS:
            raise ValueError("invalid effort")
        out["effort"] = data["effort"]
    return out


def list_chats(pid: str) -> Optional[List[Dict[str, Any]]]:
    d = _cdir(pid)
    if d is None:
        return None
    data = store._read_json(d / "index.json")
    return data if isinstance(data, list) else []


def get_chat(pid: str, cid: str) -> Optional[Dict[str, Any]]:
    if not store.valid_id(cid):
        return None
    for c in list_chats(pid) or []:
        if c.get("id") == cid:
            return c
    return None


def _save_index(pid: str, chats: List[Dict[str, Any]]) -> None:
    store._write_json(_cdir(pid) / "index.json", chats)


def create_chat(pid: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    with _lock:
        chats = list_chats(pid)
        if chats is None:
            return None
        if len(chats) >= MAX_CHATS:
            raise ValueError("too many chats in this project")
        cid = uuid.uuid4().hex
        now = _now_iso()
        rec = _clean_opts(data, {
            "id": cid,
            "title": f"Chat {len(chats) + 1}",
            "engine": default_engine(),
            "is_local": bool(config.get_nested("design.default_is_local", False)),
            "effort": None,
            "session_id": session_id_for(pid, cid),
            "created_at": now,
            "updated_at": now,
        })
        src_id = data.get("from_chat_id")
        if src_id:
            src = get_chat(pid, src_id)
            if not src:
                raise ValueError("from_chat_id not found")
            rec["continuing_from"] = {"chat_id": src_id, "title": src.get("title"),
                                      "summary": summarize(pid, src_id)}
        chats.append(rec)
        _save_index(pid, chats)
        proj = store.get_project(pid) or {}
        if not proj.get("active_chat_id"):
            store.set_project_fields(pid, active_chat_id=cid)
        return rec


def update_chat(pid: str, cid: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    with _lock:
        chats = list_chats(pid) or []
        for i, c in enumerate(chats):
            if c.get("id") == cid:
                chats[i] = _clean_opts(data, c)
                chats[i]["updated_at"] = _now_iso()
                _save_index(pid, chats)
                return chats[i]
    return None


def delete_chat(pid: str, cid: str) -> bool:
    with _lock:
        chats = list_chats(pid) or []
        keep = [c for c in chats if c.get("id") != cid]
        if len(keep) == len(chats):
            return False
        _save_index(pid, keep)
        d = _cdir(pid)
        for ext in (".jsonl", ".md"):
            try:
                (d / f"{cid}{ext}").unlink()
            except FileNotFoundError:
                pass
        _drop_session(pid, cid)
        proj = store.get_project(pid) or {}
        if proj.get("active_chat_id") == cid:
            store.set_project_fields(pid, active_chat_id=keep[-1]["id"] if keep else None)
        return True


def _drop_session(pid: str, cid: str) -> None:
    try:
        from services.session import session_store
        session_store.delete(session_id_for(pid, cid), namespace=SESSION_NAMESPACE)
    except Exception as exc:
        logger.warning("design chats: could not delete session for %s/%s: %s", pid, cid, exc)


def drop_project_sessions(pid: str) -> None:
    """Called before a project is deleted: its chats' sessions go too."""
    for c in list_chats(pid) or []:
        _drop_session(pid, c["id"])


# ── Turn records ─────────────────────────────────────────────────────────

def _turns_path(pid: str, cid: str) -> Optional[Path]:
    d = _cdir(pid)
    return d / f"{cid}.jsonl" if d else None


def read_turns(pid: str, cid: str) -> List[Dict[str, Any]]:
    p = _turns_path(pid, cid)
    if not p or not p.exists():
        return []
    out = []
    try:
        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError as exc:
        logger.warning("design chats: unreadable %s: %s", p, exc)
    return out


def write_turns(pid: str, cid: str, turns: List[Dict[str, Any]]) -> None:
    p = _turns_path(pid, cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for t in turns:
            fh.write(json.dumps(t, ensure_ascii=False, default=str) + "\n")
    os.replace(tmp, p)


def upsert_turn(pid: str, cid: str, turn: Dict[str, Any]) -> None:
    with _lock:
        turns = read_turns(pid, cid)
        for i, t in enumerate(turns):
            if t.get("id") == turn["id"]:
                turns[i] = turn
                break
        else:
            turns.append(turn)
        write_turns(pid, cid, turns)
        chats = list_chats(pid) or []
        for c in chats:
            if c.get("id") == cid:
                c["updated_at"] = _now_iso()
        _save_index(pid, chats)


def assistant_turn_count(pid: str, cid: Optional[str] = None) -> int:
    ids = [cid] if cid else [c["id"] for c in list_chats(pid) or []]
    return sum(1 for i in ids for t in read_turns(pid, i)
               if t.get("role") == "assistant" and t.get("status") in ("done", "running", "queued"))


# ── Transcript (handoff format, §4.5) ────────────────────────────────────

def render_transcript(pid: str, cid: str) -> str:
    chat = get_chat(pid, cid) or {}
    lines = [f"# {chat.get('title') or 'Chat'}", f"_Started {chat.get('created_at', '')}_", ""]
    cont = chat.get("continuing_from")
    if cont:
        lines += [f"_Continuing from {cont.get('title') or cont.get('chat_id')}_", ""]
    for t in read_turns(pid, cid):
        if t.get("role") == "user":
            lines += ["## User", (t.get("text") or "").strip(), ""]
        elif t.get("role") == "assistant":
            lines.append("## Assistant")
            body = (t.get("text") or "").strip()
            if body:
                lines.append(body)
            for tool in t.get("tools") or []:
                prev = tool.get("input_preview") or ""
                lines.append(f"_[tool: {tool.get('name')}]_ {prev}".rstrip())
            if t.get("status") not in ("done", None):
                lines.append(f"_({t.get('status')}{': ' + t['error'] if t.get('error') else ''})_")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_transcript(pid: str, cid: str) -> None:
    d = _cdir(pid)
    if not d:
        return
    tmp = d / f"{cid}.md.tmp"
    tmp.write_text(render_transcript(pid, cid), encoding="utf-8")
    os.replace(tmp, d / f"{cid}.md")


def summarize(pid: str, cid: str, max_chars: int = 4000) -> str:
    """Seed for a "Continuing from X" chat: the tail of the transcript, no model call."""
    parts: List[str] = []
    for t in reversed(read_turns(pid, cid)):
        text = (t.get("text") or "").strip()
        if not text:
            continue
        who = "User" if t.get("role") == "user" else "Designer"
        parts.append(f"{who}: {text[:1200]}")
        if sum(len(p) for p in parts) > max_chars:
            break
    return "\n\n".join(reversed(parts))[-max_chars:]
