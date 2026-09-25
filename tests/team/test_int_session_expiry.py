"""B1 — workspaces are never deleted by TTL; expiry archives them, an in-use
session never expires, and ensure() restores instead of resurrect-then-delete."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

from services.session import session_store
from services.task.task_manager import get_task_queue


def _age(sid, ns=None, *, created_s=0, used_s=0):
    """Backdate a session's created_at / last_used_at by N seconds."""
    p = session_store._session_json_path(sid, ns)
    meta = json.loads(p.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    meta["created_at"] = (now - timedelta(seconds=created_s)).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta["last_used_at"] = (now - timedelta(seconds=used_s)).strftime("%Y-%m-%dT%H:%M:%SZ")
    p.write_text(json.dumps(meta), encoding="utf-8")


def test_b1_absolute_ttl_ignored_for_workspace(tmp_data_root):
    # The old Team UI created workspaces with absolute_ttl=86400.
    session_store.create(session_id="ws-abs", data={"name": "ws"}, absolute_ttl_seconds=86400)
    (session_store._session_dir("ws-abs") / "notes.md").write_text("keep me", encoding="utf-8")
    _age("ws-abs", created_s=3 * 86400, used_s=10)
    assert session_store.get("ws-abs") is not None
    assert (session_store._session_dir("ws-abs") / "notes.md").read_text(encoding="utf-8") == "keep me"


def test_b1_idle_expiry_archives_workspace_not_deletes(tmp_data_root):
    session_store.create(session_id="ws-idle", data={"name": "ws"}, session_idle_timeout_seconds=60)
    (session_store._session_dir("ws-idle") / "report.md").write_text("work", encoding="utf-8")
    _age("ws-idle", created_s=7200, used_s=7200)

    assert session_store.get("ws-idle") is None                  # left the live set
    archived = session_store._archive_dir("ws-idle", None)
    assert (archived / "report.md").read_text(encoding="utf-8") == "work"
    assert [m["session_id"] for m in session_store.list_archived()] == ["ws-idle"]


def test_b1_ephemeral_session_still_deleted(tmp_data_root):
    session_store.create(session_id="eph", namespace="heartbeat", data={"ephemeral": True},
                         session_idle_timeout_seconds=60, absolute_ttl_seconds=60)
    _age("eph", "heartbeat", created_s=7200, used_s=7200)
    assert session_store.get("eph", namespace="heartbeat") is None
    assert not session_store._archive_dir("eph", "heartbeat").exists()
    assert not session_store._session_dir("eph", "heartbeat").exists()


def test_b1_session_with_running_task_never_expires(tmp_data_root):
    queue = get_task_queue()
    release = threading.Event()
    started = threading.Event()

    def blocker(**_kw):
        started.set()
        release.wait(10)
        return {"ok": True}

    queue.register_handler("P0_BLOCK", blocker)
    session_store.create(session_id="eph-busy", namespace="run-parallel", data={"ephemeral": True},
                         session_idle_timeout_seconds=60, absolute_ttl_seconds=60)
    tid = queue.submit_task("P0_BLOCK", {}, session_id="eph-busy", session_namespace="run-parallel")
    try:
        assert started.wait(5)
        _age("eph-busy", "run-parallel", created_s=7200, used_s=7200)
        # Both the handler's own get() and the sweeper must leave it alone.
        assert session_store.get("eph-busy", namespace="run-parallel") is not None
        session_store._last_sweep = 0
        session_store.sweep_expired()
        assert session_store.exists("eph-busy", namespace="run-parallel")
    finally:
        release.set()
    assert tid


def test_b1_ensure_restores_archived_workspace(tmp_data_root):
    session_store.create(session_id="ws-back", data={"name": "ws"}, session_idle_timeout_seconds=60)
    (session_store._session_dir("ws-back") / "keep.txt").write_text("v1", encoding="utf-8")
    _age("ws-back", created_s=7200, used_s=7200)
    session_store._last_sweep = 0
    session_store.sweep_expired()
    assert not session_store.exists("ws-back")

    # A job submitting onto the archived workspace gets its files back — not an
    # empty folder that the next get() would delete again.
    meta = session_store.ensure("ws-back")
    assert meta["session_id"] == "ws-back"
    assert (session_store._session_dir("ws-back") / "keep.txt").read_text(encoding="utf-8") == "v1"
    assert session_store.get("ws-back") is not None             # last_used refreshed


def test_b1_ensure_refreshes_last_used(tmp_data_root):
    session_store.create(session_id="ws-touch", data={"name": "ws"}, session_idle_timeout_seconds=60)
    _age("ws-touch", created_s=7200, used_s=7200)
    session_store.ensure("ws-touch")                            # submit = use
    assert session_store.get("ws-touch") is not None


def test_b1_zero_idle_means_never(tmp_data_root):
    session_store.create(session_id="ws-forever", data={"name": "ws"}, session_idle_timeout_seconds=0)
    _age("ws-forever", created_s=400 * 86400, used_s=400 * 86400)
    assert session_store.get("ws-forever") is not None


def test_b1_legacy_session_json_backward_compatible(tmp_data_root):
    """A pre-B1 session.json (no new fields) still loads and follows the rules."""
    folder = session_store._session_dir("legacy-ws")
    folder.mkdir(parents=True)
    old = datetime.now(timezone.utc) - timedelta(days=5)
    (folder / "session.json").write_text(json.dumps({
        "session_id": "legacy-ws", "namespace": None,
        "created_at": old.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_used_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_idle_timeout_seconds": 86400, "absolute_ttl_seconds": 86400,
        "task_ids": [], "data": {"name": "old"},
    }), encoding="utf-8")
    assert session_store.get("legacy-ws") is not None


def test_b1_reserved_archive_id(tmp_data_root):
    import pytest
    with pytest.raises(ValueError):
        session_store.create(session_id="_archived")
