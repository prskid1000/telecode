"""Pinned comments on boards (docs/teledesign-contract.md §3 comment record).

Storage is `comments.json` at the project root (a JSON list). Records are
normalised on every write: unknown keys dropped, strings capped, anchors
reduced to the shared shape. Comment text is user data and is escaped by the
prompt builder, never here.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.design import store

VALID_STATUS = ("open", "sent", "resolved")
MAX_COMMENTS = 2000
_NOTE_MAX = 8000
_STR_MAX = 1000
_MENTION_MAX = 8000

_lock = threading.RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _s(value: Any, cap: int = _STR_MAX) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, (str, int, float)):
        return None
    return str(value)[:cap]


def _num(value: Any) -> float:
    try:
        f = float(value)
        return f if f == f and abs(f) < 1e7 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _anchor(a: Any) -> Dict[str, Any]:
    a = a if isinstance(a, dict) else {}
    bbox = a.get("bbox") if isinstance(a.get("bbox"), dict) else {}
    return {
        "td_id": _s(a.get("td_id"), 200),
        "selector": _s(a.get("selector")),
        "source_loc": _s(a.get("source_loc"), 400),
        "node_id": _s(a.get("node_id"), 200),
        "bbox": {k: _num(bbox.get(k)) for k in ("x", "y", "w", "h")},
    }


def _path(pid: str):
    d = store.project_dir(pid)
    return d / "comments.json" if d else None


def list_comments(pid: str) -> Optional[List[Dict[str, Any]]]:
    p = _path(pid)
    if p is None:
        return None
    data = store._read_json(p)
    return data if isinstance(data, list) else []


def _save(pid: str, items: List[Dict[str, Any]]) -> None:
    store._write_json(_path(pid), items)


def get_comment(pid: str, cid: str) -> Optional[Dict[str, Any]]:
    for c in list_comments(pid) or []:
        if c.get("id") == cid:
            return c
    return None


def create_comment(pid: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    note = _s(data.get("note"), _NOTE_MAX)
    if not note or not note.strip():
        raise ValueError("note is required")
    file = _s(data.get("file"), 400)
    if file and not store.safe_relpath(file):
        raise ValueError("invalid file")
    slide = data.get("slide_index")
    rec = {
        "id": uuid.uuid4().hex,
        "board_id": _s(data.get("board_id"), 400) or file,
        "file": file,
        "anchor": _anchor(data.get("anchor")),
        "mentioned_element": _s(data.get("mentioned_element"), _MENTION_MAX),
        "author": (_s(data.get("author"), 80) or "You").strip() or "You",
        "note": note.strip(),
        "status": "open",
        "slide_index": int(slide) if isinstance(slide, (int, float)) and 0 < slide < 10000 else None,
        "created_at": _now_iso(),
        "sent_turn_id": None,
    }
    with _lock:
        items = list_comments(pid)
        if items is None:
            return None
        if len(items) >= MAX_COMMENTS:
            raise ValueError("too many comments")
        items.append(rec)
        _save(pid, items)
    return rec


def update_comment(pid: str, cid: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    with _lock:
        items = list_comments(pid)
        for c in items or []:
            if c.get("id") != cid:
                continue
            if "note" in patch:
                note = _s(patch.get("note"), _NOTE_MAX)
                if not note or not note.strip():
                    raise ValueError("note is required")
                c["note"] = note.strip()
            if "status" in patch:
                if patch["status"] not in VALID_STATUS:
                    raise ValueError("invalid status")
                c["status"] = patch["status"]
                if c["status"] == "resolved":
                    c["resolved_at"] = _now_iso()
            if "anchor" in patch:
                c["anchor"] = _anchor(patch.get("anchor"))
            if "mentioned_element" in patch:
                c["mentioned_element"] = _s(patch.get("mentioned_element"), _MENTION_MAX)
            if "author" in patch:
                c["author"] = (_s(patch.get("author"), 80) or "You").strip() or "You"
            c["updated_at"] = _now_iso()
            _save(pid, items)
            return c
    return None


def delete_comment(pid: str, cid: str) -> bool:
    with _lock:
        items = list_comments(pid)
        if items is None:
            return False
        keep = [c for c in items if c.get("id") != cid]
        if len(keep) == len(items):
            return False
        _save(pid, keep)
        return True


def mark_sent(pid: str, ids: List[str], turn_id: str) -> List[Dict[str, Any]]:
    """Flag comments as sent to a turn; returns the records that were found."""
    out = []
    with _lock:
        items = list_comments(pid) or []
        want = set(ids)
        for c in items:
            if c.get("id") in want:
                c["status"] = "sent"
                c["sent_turn_id"] = turn_id
                out.append(c)
        if out:
            _save(pid, items)
    return out


def resolve_for_turn(pid: str, turn_id: str) -> List[str]:
    """After a successful scoped turn, its comments are resolved."""
    changed = []
    with _lock:
        items = list_comments(pid) or []
        for c in items:
            if c.get("sent_turn_id") == turn_id and c.get("status") == "sent":
                c["status"] = "resolved"
                c["resolved_at"] = _now_iso()
                changed.append(c["id"])
        if changed:
            _save(pid, items)
    return changed


def reopen_for_turn(pid: str, turn_id: str) -> List[str]:
    """A failed / cancelled scoped turn hands its comments back as open."""
    changed = []
    with _lock:
        items = list_comments(pid) or []
        for c in items:
            if c.get("sent_turn_id") == turn_id and c.get("status") == "sent":
                c["status"] = "open"
                changed.append(c["id"])
        if changed:
            _save(pid, items)
    return changed
