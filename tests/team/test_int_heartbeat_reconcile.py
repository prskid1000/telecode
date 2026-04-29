"""Integration tests for heartbeat reconcile (HEARTBEAT.md ↔ HB Jobs)."""

from __future__ import annotations

from services.agent.agent_manager import get_agent_manager
from services.job.job_manager import get_job_manager
from services.heartbeat.reconcile import reconcile_agent


HB_TWO_ENTRIES = """
```yaml
- name: morning
  cron: "0 9 * * *"
  prompt: morning briefing
  workspace: ephemeral

- name: weekly
  cron: "0 4 * * 1"
  prompt: weekly review
  workspace: ephemeral
```
"""

HB_ONE_ENTRY = """
```yaml
- name: morning
  cron: "0 9 * * *"
  prompt: morning briefing UPDATED
  workspace: ephemeral
```
"""


def _seed_agent(heartbeat_md):
    am = get_agent_manager()
    a = am.create_agent("hb-test")
    am.set_internal_files(a["id"], {"HEARTBEAT.md": heartbeat_md})
    return a


def test_reconcile_creates_hb_jobs_per_yaml_entry(tmp_data_root):
    a = _seed_agent(HB_TWO_ENTRIES)
    summary = reconcile_agent(a["id"])
    assert summary["created"] == 2
    assert summary["errors"] == []

    jm = get_job_manager()
    hb_jobs = [j for j in jm.list_jobs(kind="heartbeat") if j["agent_id"] == a["id"]]
    names = {(j["heartbeat_entry"] or {}).get("name") for j in hb_jobs}
    assert names == {"morning", "weekly"}


def test_reconcile_idempotent_on_unchanged_yaml(tmp_data_root):
    a = _seed_agent(HB_TWO_ENTRIES)
    reconcile_agent(a["id"])
    summary = reconcile_agent(a["id"])
    assert summary["created"] == 0
    assert summary["updated"] == 0
    assert summary["archived"] == 0


def test_reconcile_updates_drifted_entries(tmp_data_root):
    a = _seed_agent(HB_TWO_ENTRIES)
    reconcile_agent(a["id"])

    am = get_agent_manager()
    am.set_internal_files(a["id"], {"HEARTBEAT.md": HB_ONE_ENTRY})
    summary = reconcile_agent(a["id"])
    # weekly archived (no longer in YAML); morning's prompt drifted → updated
    assert summary["archived"] == 1
    assert summary["updated"] >= 1

    jm = get_job_manager()
    morning = jm.find_heartbeat_job(a["id"], "morning")
    weekly = jm.find_heartbeat_job(a["id"], "weekly")
    assert morning is not None
    assert morning["task_description"] == "morning briefing UPDATED"
    assert morning["archived"] is False
    assert weekly is not None
    assert weekly["archived"] is True


def test_reconcile_unarchives_when_entry_returns(tmp_data_root):
    """If the user removes an entry then re-adds it, the archived job
    should be unarchived rather than a new one created."""
    a = _seed_agent(HB_TWO_ENTRIES)
    reconcile_agent(a["id"])

    am = get_agent_manager()
    # Drop weekly
    am.set_internal_files(a["id"], {"HEARTBEAT.md": HB_ONE_ENTRY})
    reconcile_agent(a["id"])
    # Add weekly back
    am.set_internal_files(a["id"], {"HEARTBEAT.md": HB_TWO_ENTRIES})
    summary = reconcile_agent(a["id"])

    jm = get_job_manager()
    weekly = jm.find_heartbeat_job(a["id"], "weekly")
    assert weekly is not None
    assert weekly["archived"] is False
    # One job updated (unarchived); none re-created.
    assert summary["created"] == 0


def test_reconcile_returns_errors_for_bad_yaml(tmp_data_root):
    am = get_agent_manager()
    a = am.create_agent("err-bot")
    am.set_internal_files(a["id"], {"HEARTBEAT.md":
        "```yaml\n- name: bad\n  cron: nonsense\n  prompt: x\n```"
    })
    summary = reconcile_agent(a["id"])
    assert summary["created"] == 0
    assert any("invalid cron" in e["msg"] for e in summary["errors"])


def test_reconcile_returns_error_for_missing_agent(tmp_data_root):
    summary = reconcile_agent("no-such-agent")
    assert summary["created"] == 0
    assert summary["errors"] and "agent not found" in summary["errors"][0]["msg"]
