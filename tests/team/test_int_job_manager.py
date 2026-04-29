"""Integration tests for JobManager."""

from __future__ import annotations

from services.job.job_manager import get_job_manager


def test_create_job_with_legacy_agent_id_builds_single_pipeline(tmp_data_root):
    m = get_job_manager()
    j = m.create_job({"title": "t", "agent_id": "a", "workspace_id": "ws"})
    assert j["pipeline"]["mode"] == "single"
    assert len(j["pipeline"]["steps"]) == 1
    assert j["pipeline"]["steps"][0]["agent_id"] == "a"


def test_create_job_with_explicit_pipeline_kept(tmp_data_root):
    m = get_job_manager()
    j = m.create_job({
        "title": "t", "workspace_id": "ws",
        "pipeline": {"mode": "sequential", "steps": [
            {"agent_id": "a"}, {"agent_id": "b"},
        ]},
    })
    assert j["pipeline"]["mode"] == "sequential"
    assert [s["phase"] for s in j["pipeline"]["steps"]] == [0, 1]


def test_kind_defaults_to_user(tmp_data_root):
    m = get_job_manager()
    j = m.create_job({"title": "t", "agent_id": "a"})
    assert j["kind"] == "user"
    assert j["archived"] is False


def test_invalid_kind_falls_back_to_user(tmp_data_root):
    m = get_job_manager()
    j = m.create_job({"title": "t", "kind": "🤡", "agent_id": "a"})
    assert j["kind"] == "user"


def test_list_jobs_filters_by_kind(tmp_data_root):
    m = get_job_manager()
    u = m.create_job({"title": "user-job", "agent_id": "a"})
    h = m.create_job({"title": "hb-job", "kind": "heartbeat", "agent_id": "a",
                      "heartbeat_entry": {"name": "x", "cron": "0 9 * * *"}})

    user_only = m.list_jobs(kind="user")
    assert {j["id"] for j in user_only} == {u["id"]}

    hb_only = m.list_jobs(kind="heartbeat")
    assert {j["id"] for j in hb_only} == {h["id"]}


def test_archived_jobs_hidden_by_default(tmp_data_root):
    m = get_job_manager()
    j = m.create_job({"title": "t", "agent_id": "a"})
    m.update_job(j["id"], {"archived": True})

    assert j["id"] not in {x["id"] for x in m.list_jobs()}
    assert j["id"] in {x["id"] for x in m.list_jobs(include_archived=True)}


def test_find_heartbeat_job_by_name(tmp_data_root):
    m = get_job_manager()
    h = m.create_job({
        "title": "tick", "kind": "heartbeat", "agent_id": "agent-x",
        "heartbeat_entry": {"name": "tick", "cron": "0 9 * * *"},
    })
    found = m.find_heartbeat_job("agent-x", "tick")
    assert found is not None and found["id"] == h["id"]
    assert m.find_heartbeat_job("agent-x", "no-such") is None
    assert m.find_heartbeat_job("other-agent", "tick") is None


def test_update_job_normalises_pipeline(tmp_data_root):
    m = get_job_manager()
    j = m.create_job({"title": "t", "agent_id": "a"})
    m.update_job(j["id"], {"pipeline": {"mode": "custom", "steps": [
        {"agent_id": "a", "phase": 5},
        {"agent_id": "b", "phase": 5},
        {"agent_id": "c", "phase": 10},
    ]}})
    after = m.get_job(j["id"])
    # Renumbered to contiguous 0..N-1
    assert [s["phase"] for s in after["pipeline"]["steps"]] == [0, 0, 1]


def test_delete_job_removes_files_dir(tmp_data_root):
    m = get_job_manager()
    j = m.create_job({"title": "t", "agent_id": "a"})
    m.save_file(j["id"], "doc.txt", b"hi")
    fdir = m._get_job_files_dir(j["id"])
    assert fdir.exists()
    m.delete_job(j["id"])
    assert not fdir.exists()
    assert m.get_job(j["id"]) is None
