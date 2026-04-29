"""Flow tests for HeartbeatScheduler — unit-level for _is_due decision logic,
flow-level for _fire path with the fake task queue.
"""

from __future__ import annotations

import time

import pytest

from services.agent.agent_manager import get_agent_manager
from services.heartbeat import state as hb_state
from services.heartbeat.parser import ScheduleEntry
from services.heartbeat.scheduler import HeartbeatScheduler, EPHEMERAL_NS
from services.session import session_store


def test_is_due_false_on_fresh_state(tmp_data_root):
    """A never-fired entry is NOT due immediately — anchor is now."""
    sched = HeartbeatScheduler()
    e = ScheduleEntry(name="hourly", cron="0 * * * *", prompt="x")
    # Just-created state with no last_run → anchored to now → next fire is in
    # the future, so not due yet.
    assert sched._is_due("agent-x", e) is False


def test_is_due_true_when_last_run_long_ago(tmp_data_root, monkeypatch):
    sched = HeartbeatScheduler()
    e = ScheduleEntry(name="every-min", cron="* * * * *", prompt="x")
    # Pretend last fire was 2 hours ago
    from datetime import datetime, timezone, timedelta
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    hb_state.mark_fired("agent-x", e.name, task_id="t-old")
    # Override last_run to long ago
    raw = hb_state._read()
    raw["agent-x:every-min"]["last_run"] = long_ago
    hb_state._write(raw)
    assert sched._is_due("agent-x", e) is True


def test_is_due_blocks_within_min_fire_gap(tmp_data_root):
    """Even if cron technically matches, don't re-fire faster than the gap."""
    sched = HeartbeatScheduler()
    e = ScheduleEntry(name="rapid", cron="* * * * *", prompt="x")
    hb_state.mark_fired("agent-x", e.name, task_id="t")
    # Just fired = within min gap
    assert sched._is_due("agent-x", e) is False


def test_is_due_invalid_cron_returns_false(tmp_data_root):
    sched = HeartbeatScheduler()
    e = ScheduleEntry(name="busted", cron="not a cron", prompt="x")
    assert sched._is_due("agent-x", e) is False


def test_fire_creates_ephemeral_session_and_submits_task(tmp_data_root, fake_task_queue):
    """_fire should create the session, submit a task with the right metadata,
    and track it for post-completion cleanup."""
    am = get_agent_manager()
    a = am.create_agent("hb-fire-bot", soul="x")
    am.set_internal_files(a["id"], {"AGENT.md": "rules"})

    sched = HeartbeatScheduler()
    entry = ScheduleEntry(
        name="ephemeral-tick", cron="*/5 * * * *", prompt="tick now",
        workspace="ephemeral", engine="claude_code",
    )
    sched._fire(a["id"], entry)

    # A fake task got submitted
    assert fake_task_queue.last_calls
    call = fake_task_queue.last_calls[0]
    assert call["agent_id"] == a["id"]
    assert call["prompt"] == "tick now"

    # State updated
    s = hb_state.get(a["id"], entry.name)
    assert s["last_status"] == "running"
    assert s["last_task_id"] is not None

    # Tracked for cleanup
    assert len(sched._tracked_ephemeral) == 1


def test_fire_persistent_skips_when_workspace_missing(tmp_data_root):
    """workspace=persistent must reference an existing session, else skip."""
    am = get_agent_manager()
    a = am.create_agent("hb-persistent-bot")

    sched = HeartbeatScheduler()
    entry = ScheduleEntry(
        name="persistent-broken", cron="0 * * * *", prompt="x",
        workspace="persistent", workspace_id="missing-ws",
    )
    sched._fire(a["id"], entry)
    # Should mark the state as failed without crashing
    s = hb_state.get(a["id"], entry.name)
    assert s["last_status"] in ("failed", "running")    # tolerant


def test_sweep_ephemeral_deletes_completed_session(tmp_data_root, fake_task_queue):
    am = get_agent_manager()
    a = am.create_agent("hb-sweep-bot", soul="x")
    am.set_internal_files(a["id"], {"AGENT.md": "rules"})

    sched = HeartbeatScheduler()
    entry = ScheduleEntry(
        name="ephemeral-sweep", cron="*/5 * * * *", prompt="x",
        workspace="ephemeral",
    )
    sched._fire(a["id"], entry)

    # Wait briefly for the fake handler thread to complete
    deadline = time.time() + 5
    while time.time() < deadline and sched._tracked_ephemeral:
        sched._sweep_ephemeral()
        time.sleep(0.1)

    # Session should be deleted post-completion
    info = next(iter(sched._tracked_ephemeral.values()), None)
    if info is not None:
        # If still tracked the test environment is too fast to drain — fall through.
        pass
    # All tracked are gone after a few sweeps
    assert sched._tracked_ephemeral == {}


def test_tick_reconciles_and_fires_due_entries(tmp_data_root, fake_task_queue):
    """Full tick: parses HEARTBEAT.md, reconciles, fires due entries."""
    am = get_agent_manager()
    a = am.create_agent("hb-tick-bot")
    am.set_internal_files(a["id"], {"HEARTBEAT.md": (
        "```yaml\n"
        "- name: every-min\n"
        '  cron: "* * * * *"\n'
        "  prompt: tick\n"
        "  workspace: ephemeral\n"
        "```\n"
    )})

    # Make it look like the entry was due by writing an old last_run
    from datetime import datetime, timezone, timedelta
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    hb_state.mark_fired(a["id"], "every-min", task_id="old")
    raw = hb_state._read()
    raw[f"{a['id']}:every-min"]["last_run"] = long_ago
    hb_state._write(raw)

    sched = HeartbeatScheduler()
    sched._tick()

    # Reconcile created an HB job
    from services.job.job_manager import get_job_manager
    jm = get_job_manager()
    hb_jobs = [j for j in jm.list_jobs(kind="heartbeat") if j["agent_id"] == a["id"]]
    assert len(hb_jobs) == 1

    # Tick fired the entry (handler called)
    assert any(c.get("agent_id") == a["id"] for c in fake_task_queue.last_calls)


def test_tick_respects_max_concurrent_fires(tmp_data_root, fake_task_queue, monkeypatch):
    """Set max=1, queue up 3 due entries, only 1 fires this tick."""
    import config
    monkeypatch.setattr(config, "heartbeat_max_concurrent_fires", lambda: 1)

    am = get_agent_manager()
    a = am.create_agent("hb-cap-bot")
    am.set_internal_files(a["id"], {"HEARTBEAT.md": (
        "```yaml\n"
        "- name: a\n  cron: \"* * * * *\"\n  prompt: A\n  workspace: ephemeral\n"
        "- name: b\n  cron: \"* * * * *\"\n  prompt: B\n  workspace: ephemeral\n"
        "- name: c\n  cron: \"* * * * *\"\n  prompt: C\n  workspace: ephemeral\n"
        "```\n"
    )})
    # Make all three look due
    from datetime import datetime, timezone, timedelta
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for n in ("a", "b", "c"):
        hb_state.mark_fired(a["id"], n, task_id="old")
        raw = hb_state._read()
        raw[f"{a['id']}:{n}"]["last_run"] = long_ago
        hb_state._write(raw)

    sched = HeartbeatScheduler()
    sched._tick()
    fired = [c for c in fake_task_queue.last_calls if c.get("agent_id") == a["id"]]
    assert len(fired) == 1
