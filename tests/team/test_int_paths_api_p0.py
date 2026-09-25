"""B9 — agent/job/run ids validated and agent file paths contained, at the
manager layer and over HTTP (400, not 500 / not a write outside the dir)."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services.agent.agent_manager import get_agent_manager
from services.job.job_manager import get_job_manager
from services.run.run_store import get_run_store


def test_b9_manager_rejects_traversal(tmp_data_root):
    am = get_agent_manager()
    a = am.create_agent("paths")
    for bad in ("../../evil.txt", "..\\..\\evil.txt", "C:/Windows/evil.txt", "/abs/evil.txt"):
        with pytest.raises(ValueError):
            am.save_file(a["id"], bad, b"x")
    assert not (tmp_data_root / "evil.txt").exists()
    am.save_file(a["id"], "sub/ok.txt", b"ok")
    assert am.get_file_path(a["id"], "sub/ok.txt").read_bytes() == b"ok"
    for bad_id in ("..", "../x", "a/b", "a.b", ""):
        with pytest.raises(ValueError):
            am.list_files(bad_id)
        assert am.get_agent(bad_id) is None
        assert get_job_manager().get_job(bad_id) is None
        assert get_run_store().get_run(bad_id) is None


_BOUNDARY = "p0boundary"


def _raw_multipart(filename: str) -> bytes:
    crlf = "\r\n"
    return (f"--{_BOUNDARY}{crlf}"
            f'Content-Disposition: form-data; name="files"; filename="{filename}"{crlf}'
            f"Content-Type: application/octet-stream{crlf}{crlf}"
            f"pwn{crlf}"
            f"--{_BOUNDARY}--{crlf}").encode()


def _app():
    from proxy import api_agents, api_jobs, api_runs
    app = web.Application()
    api_agents.register_routes(app)
    api_jobs.register_routes(app)
    api_runs.register_routes(app)
    return app


async def _with_client(fn):
    client = TestClient(TestServer(_app()))
    await client.start_server()
    try:
        return await fn(client)
    finally:
        await client.close()


def test_b9_http_returns_400_for_bad_ids_and_paths(tmp_data_root):
    a = get_agent_manager().create_agent("http-paths")

    async def go(c):
        out = {}
        r = await c.get("/api/agents/a.b/files")
        out["bad_agent_id"] = r.status
        r = await c.get(f"/api/agents/{a['id']}/files/..%5C..%5Csecret.txt")
        out["get_traversal"] = r.status
        r = await c.delete(f"/api/agents/{a['id']}/files/C:%2Fx.txt")
        out["delete_drive"] = r.status
        # aiohttp's client percent-encodes filenames; build the body by hand
        # the way curl would send it, with a raw "../../" in the filename.
        r = await c.post(f"/api/agents/{a['id']}/files", data=_raw_multipart("../../pwned.txt"),
                         headers={"Content-Type": f"multipart/form-data; boundary={_BOUNDARY}"})
        out["upload_traversal"] = r.status
        j = get_job_manager().create_job({"title": "t"})
        r = await c.post(f"/api/jobs/{j['id']}/files", data=_raw_multipart("sub/../../../pwned.txt"),
                         headers={"Content-Type": f"multipart/form-data; boundary={_BOUNDARY}"})
        out["job_upload_traversal"] = r.status
        r = await c.get("/api/runs/bad.id")
        out["bad_run_id"] = r.status
        r = await c.post("/api/jobs", json={"title": "x", "pipeline": {
            "mode": "single", "steps": [{"agent_id": a["id"], "engine": "gpt5"}]}})
        out["bad_step_engine"] = r.status
        r = await c.post("/api/agents", json={"name": "n", "engine": "nope"})
        out["bad_agent_engine"] = r.status
        return out

    out = asyncio.run(_with_client(go))
    assert out == {k: 400 for k in out}, out
    assert not (tmp_data_root / "data" / "agents" / "pwned.txt").exists()
    assert not (tmp_data_root / "data" / "pwned.txt").exists()


def test_b3_http_run_body_overrides(tmp_data_root, fake_task_queue):
    """POST /api/jobs/{id}/runs accepts engine/model/is_local (UI names)."""
    import time
    from services.session import session_store
    a = get_agent_manager().create_agent("http-run")
    session_store.create(session_id="ws-http", data={"name": "ws"})
    j = get_job_manager().create_job({"title": "t", "workspace_id": "ws-http",
                                      "pipeline": {"mode": "single", "steps": [{"agent_id": a["id"]}]}})

    async def go(c):
        r = await c.post(f"/api/jobs/{j['id']}/runs", json={"engine": "codex", "model": "m", "is_local": False})
        return r.status, await r.json()

    status, body = asyncio.run(_with_client(go))
    assert status == 200, body
    step = body["run"]["steps"][0]
    assert (step["engine"], step["model"], step["is_local"]) == ("codex", "m", False)
    rid = body["run"]["run_id"]
    end = time.time() + 5
    while time.time() < end and get_run_store().get_run(rid)["status"] == "running":
        time.sleep(0.05)
