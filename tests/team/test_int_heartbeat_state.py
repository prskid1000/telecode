"""Integration tests for heartbeat state file (atomic JSON)."""

from __future__ import annotations

from services.heartbeat import state as hb_state


def test_get_returns_empty_when_unknown(tmp_data_root):
    assert hb_state.get("missing-agent", "missing-entry") == {}


def test_mark_fired_persists(tmp_data_root):
    hb_state.mark_fired("agent-1", "entry-A", task_id="t-1")
    s = hb_state.get("agent-1", "entry-A")
    assert s["last_status"] == "running"
    assert s["last_task_id"] == "t-1"
    assert s["last_run"].endswith("Z")


def test_mark_finished_updates_status_and_finish_time(tmp_data_root):
    hb_state.mark_fired("agent-1", "entry-A", task_id="t-1")
    hb_state.mark_finished("agent-1", "entry-A", "completed", task_id="t-1")
    s = hb_state.get("agent-1", "entry-A")
    assert s["last_status"] == "completed"
    assert "last_finished" in s


def test_independent_keys_per_agent_entry(tmp_data_root):
    hb_state.mark_fired("agent-1", "morning", task_id="t-1")
    hb_state.mark_fired("agent-2", "morning", task_id="t-2")
    assert hb_state.get("agent-1", "morning")["last_task_id"] == "t-1"
    assert hb_state.get("agent-2", "morning")["last_task_id"] == "t-2"


def test_prune_orphans_removes_unknown_keys(tmp_data_root):
    hb_state.mark_fired("agent-1", "still-here", task_id="t-1")
    hb_state.mark_fired("agent-1", "deleted", task_id="t-2")
    hb_state.mark_fired("agent-2", "also-deleted", task_id="t-3")

    known = {"agent-1:still-here"}
    removed = hb_state.prune_orphans(known)
    assert removed == 2

    assert hb_state.get("agent-1", "still-here")
    assert hb_state.get("agent-1", "deleted") == {}
    assert hb_state.get("agent-2", "also-deleted") == {}


def test_prune_orphans_returns_zero_when_all_kept(tmp_data_root):
    hb_state.mark_fired("a", "x", task_id="t")
    assert hb_state.prune_orphans({"a:x"}) == 0


def test_atomic_writes_no_partial_state(tmp_data_root):
    """Smoke: state file should be valid JSON after many writes."""
    import json
    for i in range(50):
        hb_state.mark_fired(f"agent-{i % 5}", f"entry-{i % 3}", task_id=f"t-{i}")
    p = hb_state._state_path()
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert len(data) > 0
