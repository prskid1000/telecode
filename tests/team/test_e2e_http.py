"""End-to-end HTTP tests against a running telecode proxy.

Skipped automatically when the server isn't reachable so this can live
alongside unit/integration tests without breaking CI.

Each test creates its own workspaces / agents / jobs, cancels long-running
runs early, and cleans up after itself.
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error

import pytest

BASE = "http://127.0.0.1:1235"


def _server_up() -> bool:
    try:
        r = urllib.request.Request(f"{BASE}/api/tasks/types")
        with urllib.request.urlopen(r, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _server_up(), reason="proxy server at :1235 not reachable")


def req(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _make_workspace(name):
    sc, ws = req("POST", "/api/sessions", {
        "data": {"name": name},
        "session_idle_timeout_seconds": 3600,
        "absolute_ttl_seconds": 3600,
    })
    assert sc == 200
    return ws["session"]["session_id"]


def _make_agent(name, soul="", agent_md="", heartbeat_md=""):
    sc, a = req("POST", "/api/agents", {"name": name, "soul": soul})
    assert sc == 200
    aid = a["agent"]["id"]
    if agent_md or heartbeat_md:
        sc, _ = req("PUT", f"/api/agents/{aid}/internal", {"files": {
            "AGENT.md": agent_md, "HEARTBEAT.md": heartbeat_md,
        }})
        assert sc == 200
    return aid


def _wait_run(run_id, predicate, deadline_s=20):
    end = time.time() + deadline_s
    while time.time() < end:
        sc, rr = req("GET", f"/api/runs/{run_id}", None, timeout=5)
        run = rr.get("run") or {}
        if not run:
            time.sleep(0.5); continue
        if predicate(run):
            return run
        time.sleep(0.5)
    sc, rr = req("GET", f"/api/runs/{run_id}", None, timeout=5)
    return rr.get("run") or {}


def _cleanup(agents=(), jobs=(), workspaces=()):
    for jid in jobs:
        req("DELETE", f"/api/jobs/{jid}", None, timeout=5)
    for aid in agents:
        req("DELETE", f"/api/agents/{aid}", None, timeout=5)
    for wsid in workspaces:
        req("DELETE", f"/api/sessions/{wsid}", None, timeout=5)


# ── Workspace + Agent CRUD ─────────────────────────────────────────────

def test_workspace_crud():
    ws = _make_workspace("e2e-ws")
    sc, listing = req("GET", "/api/sessions", None)
    assert sc == 200
    assert any(s["session_id"] == ws for s in listing["sessions"])
    sc, _ = req("DELETE", f"/api/sessions/{ws}", None)
    assert sc == 200


def test_agent_crud_and_internal_files():
    aid = _make_agent("e2e-agent", soul="my soul")
    sc, intres = req("GET", f"/api/agents/{aid}/internal", None)
    files = intres["files"]
    assert files["SOUL.md"] == "my soul"
    # Update one file
    sc, _ = req("PUT", f"/api/agents/{aid}/internal", {"files": {"MEMORY.md": "remembered"}})
    assert sc == 200
    sc, intres = req("GET", f"/api/agents/{aid}/internal", None)
    assert intres["files"]["MEMORY.md"] == "remembered"
    # Cleanup
    sc, _ = req("DELETE", f"/api/agents/{aid}", None)
    assert sc == 200


# ── Pipeline normalization via API ─────────────────────────────────────

def test_pipeline_modes_normalized_by_api():
    ws = _make_workspace("e2e-pipe")
    aid = _make_agent("pipe-agent")

    cases = [
        ("single",     [{"agent_id": aid}, {"agent_id": aid}], [0]),                 # truncated to 1
        ("sequential", [{"agent_id": aid}, {"agent_id": aid}, {"agent_id": aid}], [0, 1, 2]),
        ("parallel",   [{"agent_id": aid}, {"agent_id": aid}, {"agent_id": aid}], [0, 0, 0]),
        ("custom",     [{"agent_id": aid, "phase": 5},
                        {"agent_id": aid, "phase": 5},
                        {"agent_id": aid, "phase": 10}], [0, 0, 1]),                  # renumbered
    ]
    job_ids = []
    for mode, steps, expected_phases in cases:
        sc, j = req("POST", "/api/jobs", {
            "title": f"e2e-{mode}", "workspace_id": ws,
            "pipeline": {"mode": mode, "steps": steps},
        })
        assert sc == 200, j
        actual = [s["phase"] for s in j["job"]["pipeline"]["steps"]]
        assert actual == expected_phases, f"{mode}: expected {expected_phases}, got {actual}"
        job_ids.append(j["job"]["id"])

    _cleanup(agents=[aid], jobs=job_ids, workspaces=[ws])


# ── Run executor (cancels early to bound wall-time) ────────────────────

def test_run_starts_and_cancels_cleanly():
    ws = _make_workspace("e2e-run")
    aid = _make_agent("run-agent", soul="x", agent_md="reply ok")
    sc, j = req("POST", "/api/jobs", {
        "title": "e2e-run-job", "workspace_id": ws,
        "task_description": "say hi",
        "pipeline": {"mode": "single", "steps": [{"agent_id": aid}]},
    })
    job_id = j["job"]["id"]
    sc, r = req("POST", f"/api/jobs/{job_id}/runs", {"is_local": True, "source": "user"})
    run_id = r["run"]["run_id"]
    # Wait until step assigned a session_id (i.e. moved to running)
    final = _wait_run(run_id, lambda rr: rr["steps"][0]["status"] != "pending", deadline_s=15)
    assert final["steps"][0]["session_id"] == ws    # single-step runs in job ws
    sc, _ = req("POST", f"/api/jobs/{job_id}/runs/{run_id}/cancel", {})
    assert sc == 200
    _cleanup(agents=[aid], jobs=[job_id], workspaces=[ws])


def test_parallel_run_uses_distinct_ephemeral_sessions():
    ws = _make_workspace("e2e-par")
    aid = _make_agent("par-agent", agent_md="reply A")
    sc, j = req("POST", "/api/jobs", {
        "title": "e2e-par-job", "workspace_id": ws,
        "pipeline": {"mode": "parallel", "steps": [
            {"agent_id": aid, "name": "A"},
            {"agent_id": aid, "name": "B"},
        ]},
    })
    job_id = j["job"]["id"]
    sc, r = req("POST", f"/api/jobs/{job_id}/runs", {"is_local": True, "source": "user"})
    run_id = r["run"]["run_id"]
    final = _wait_run(run_id, lambda rr: all(s["status"] != "pending" for s in rr["steps"]), deadline_s=15)
    sids = [s["session_id"] for s in final["steps"]]
    assert len(set(sids)) == 2                       # distinct
    assert all(sid != ws for sid in sids)            # not the job ws
    req("POST", f"/api/jobs/{job_id}/runs/{run_id}/cancel", {})
    _cleanup(agents=[aid], jobs=[job_id], workspaces=[ws])


def test_custom_phase_topology_starts_phase_zero_in_workspace():
    ws = _make_workspace("e2e-cus")
    aid = _make_agent("cus-agent", agent_md="ok")
    sc, j = req("POST", "/api/jobs", {
        "title": "e2e-cus-job", "workspace_id": ws,
        "pipeline": {"mode": "custom", "steps": [
            {"agent_id": aid, "name": "A", "phase": 0},
            {"agent_id": aid, "name": "B", "phase": 1},
            {"agent_id": aid, "name": "C", "phase": 1},
            {"agent_id": aid, "name": "D", "phase": 2, "depends_on_text": True},
        ]},
    })
    assert [s["phase"] for s in j["job"]["pipeline"]["steps"]] == [0, 1, 1, 2]
    job_id = j["job"]["id"]
    sc, r = req("POST", f"/api/jobs/{job_id}/runs", {"is_local": True, "source": "user"})
    run_id = r["run"]["run_id"]
    final = _wait_run(run_id, lambda rr: rr["steps"][0]["status"] != "pending", deadline_s=15)
    # Phase-0 step started in the job workspace
    assert final["steps"][0]["session_id"] == ws
    # Later phases still pending
    assert all(s["status"] == "pending" for s in final["steps"][1:])
    req("POST", f"/api/jobs/{job_id}/runs/{run_id}/cancel", {})
    _cleanup(agents=[aid], jobs=[job_id], workspaces=[ws])


# ── Heartbeat HTTP surface ─────────────────────────────────────────────

def test_heartbeat_validate_endpoint_reports_errors():
    aid = _make_agent("hb-validate-agent")
    bad_yaml = (
        "```yaml\n"
        "- name: ok\n  cron: \"0 9 * * *\"\n  prompt: a\n"
        "- name: bad\n  cron: \"garbage\"\n  prompt: b\n"
        "```\n"
    )
    sc, v = req("POST", f"/api/agents/{aid}/heartbeat/validate", {"text": bad_yaml})
    assert sc == 200
    assert v["ok"] is False
    assert any("invalid cron" in e["msg"] for e in v["errors"])
    assert any(e["name"] == "ok" for e in v["entries"])
    _cleanup(agents=[aid])


def test_heartbeat_reconcile_creates_hb_jobs():
    aid = _make_agent("hb-rec-agent", heartbeat_md=(
        "```yaml\n"
        "- name: morning\n  cron: \"0 9 * * *\"\n  prompt: brief\n  workspace: ephemeral\n"
        "```\n"
    ))
    # Reconcile already triggered by the PUT; verify the HB job exists.
    sc, jobs_hb = req("GET", "/api/jobs?kind=heartbeat", None)
    mine = [j for j in jobs_hb["jobs"] if j["agent_id"] == aid]
    assert len(mine) == 1 and mine[0]["title"] == "morning"
    _cleanup(agents=[aid], jobs=[mine[0]["id"]])


def test_heartbeat_archive_on_yaml_removal():
    aid = _make_agent("hb-arch-agent", heartbeat_md=(
        "```yaml\n"
        "- name: keepme\n  cron: \"0 9 * * *\"\n  prompt: x\n  workspace: ephemeral\n"
        "- name: dropme\n  cron: \"0 10 * * *\"\n  prompt: y\n  workspace: ephemeral\n"
        "```\n"
    ))
    # Now drop one entry
    sc, _ = req("PUT", f"/api/agents/{aid}/internal", {"files": {"HEARTBEAT.md":
        "```yaml\n- name: keepme\n  cron: \"0 9 * * *\"\n  prompt: x\n  workspace: ephemeral\n```"
    }})
    sc, jobs_hb = req("GET", "/api/jobs?kind=heartbeat&include_archived=true", None)
    mine = [j for j in jobs_hb["jobs"] if j["agent_id"] == aid]
    by_name = {(j["heartbeat_entry"] or {}).get("name"): j for j in mine}
    assert by_name["keepme"]["archived"] is False
    assert by_name["dropme"]["archived"] is True
    _cleanup(agents=[aid], jobs=[j["id"] for j in mine])
