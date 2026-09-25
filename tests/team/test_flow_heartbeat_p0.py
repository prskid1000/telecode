"""B3 (heartbeat engine/model/is_local), B4 (never-fired entries fire at their
first cron slot after creation), B6 (stale running state reconciled)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from services.agent.agent_manager import get_agent_manager
from services.heartbeat import state as hb_state
from services.heartbeat.parser import ScheduleEntry, parse
from services.heartbeat.scheduler import HeartbeatScheduler


def _yaml(body: str) -> str:
    return "```yaml\n" + body + "\n```\n"


# ── B3: parser ──────────────────────────────────────────────────────────────

def test_b3_parser_accepts_all_engines_model_and_local():
    r = parse(_yaml(
        "- name: a\n  cron: '0 * * * *'\n  prompt: x\n  engine: codex\n  model: gpt-5.5\n  is_local: true\n"
        "- name: b\n  cron: '0 * * * *'\n  prompt: y\n  engine: antigravity\n"
        "- name: c\n  cron: '0 * * * *'\n  prompt: z\n"
    ))
    assert r.ok, r.errors
    a, b, c = r.entries
    assert (a.engine, a.model, a.is_local) == ("codex", "gpt-5.5", True)
    assert (b.engine, b.model, b.is_local) == ("antigravity", None, False)
    assert (c.engine, c.model, c.is_local) == ("claude_code", None, False)
    assert a.to_dict()["model"] == "gpt-5.5" and a.to_dict()["is_local"] is True


def test_b3_parser_rejects_bad_is_local_and_model():
    r = parse(_yaml(
        "- name: a\n  cron: '0 * * * *'\n  prompt: x\n  is_local: maybe\n"
        "- name: b\n  cron: '0 * * * *'\n  prompt: x\n  model: 5\n"
    ))
    assert not r.entries
    assert len(r.errors) == 2


def test_b3_scheduler_passes_engine_model_local(tmp_data_root, fake_task_queue):
    from services.task.task_manager import get_task_queue
    a = get_agent_manager().create_agent("hb-eng", soul="x")
    sched = HeartbeatScheduler()
    entry = ScheduleEntry(name="e", cron="*/5 * * * *", prompt="tick", workspace="ephemeral",
                          engine="codex", model="gpt-5.5", is_local=True)
    sched._fire(a["id"], entry)
    call = fake_task_queue.last_calls[0]
    assert call["is_local"] is True and call["model"] == "gpt-5.5"
    tid = hb_state.get(a["id"], "e")["last_task_id"]
    t = get_task_queue().get_task(tid)
    assert t.task_type == "CODEX" and t.pool == "background"


def test_b3_scheduler_uses_agent_default_model_for_matching_engine(tmp_data_root, fake_task_queue):
    a = get_agent_manager().create_agent("hb-def", engine="claude_code", model="sonnet")
    sched = HeartbeatScheduler()
    sched._fire(a["id"], ScheduleEntry(name="d", cron="*/5 * * * *", prompt="t", workspace="ephemeral"))
    assert fake_task_queue.last_calls[0]["model"] == "sonnet"


# ── B4 ──────────────────────────────────────────────────────────────────────

def _backdate_first_seen(agent_id, name, minutes):
    raw = hb_state._read()
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw.setdefault(f"{agent_id}:{name}", {})["first_seen"] = ts
    hb_state._write(raw)


def test_b4_never_fired_entry_becomes_due_after_first_slot(tmp_data_root):
    sched = HeartbeatScheduler()
    e = ScheduleEntry(name="every-min", cron="* * * * *", prompt="x")
    # First observation stores first_seen and is not due (next slot is ahead).
    assert sched._is_due("agent-x", e) is False
    assert hb_state.get("agent-x", "every-min").get("first_seen")
    # The old code re-anchored to "now" every tick, so this stayed False forever.
    _backdate_first_seen("agent-x", "every-min", 3)
    assert sched._is_due("agent-x", e) is True


def test_b4_first_seen_is_stable_across_ticks(tmp_data_root):
    sched = HeartbeatScheduler()
    e = ScheduleEntry(name="hourly", cron="0 * * * *", prompt="x")
    sched._is_due("agent-y", e)
    first = hb_state.get("agent-y", "hourly")["first_seen"]
    sched._is_due("agent-y", e)
    assert hb_state.get("agent-y", "hourly")["first_seen"] == first


def test_b4_tick_fires_new_entry_once_its_slot_passes(tmp_data_root, fake_task_queue):
    am = get_agent_manager()
    a = am.create_agent("hb-new")
    am.set_internal_files(a["id"], {"HEARTBEAT.md": _yaml(
        "- name: fresh\n  cron: '* * * * *'\n  prompt: hello\n  workspace: ephemeral")})
    sched = HeartbeatScheduler()
    sched._tick()
    assert not [c for c in fake_task_queue.last_calls if c.get("agent_id") == a["id"]]
    _backdate_first_seen(a["id"], "fresh", 2)
    sched._tick()
    assert [c for c in fake_task_queue.last_calls if c.get("agent_id") == a["id"]]
    # After firing, the min gap still applies.
    fake_task_queue.last_calls.clear()
    sched._tick()
    assert not [c for c in fake_task_queue.last_calls if c.get("agent_id") == a["id"]]


# ── B6 ──────────────────────────────────────────────────────────────────────

def test_b6_heartbeat_running_state_reconciled(tmp_data_root):
    hb_state.mark_fired("ag", "stale", task_id="dead-task")
    hb_state.mark_fired("ag", "live", task_id="live-task")
    n = hb_state.reconcile_interrupted(lambda tid: tid == "live-task")
    assert n == 1
    assert hb_state.get("ag", "stale")["last_status"] == "interrupted"
    assert hb_state.get("ag", "live")["last_status"] == "running"
