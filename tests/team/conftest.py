"""Shared fixtures for team-mode test suite (agents/jobs/runs/heartbeat).

The team-mode managers each cache their `data/...` base directory in their
__init__ from `config._settings_dir()`. Tests must redirect that directory
AND re-instantiate the singleton so the cached path picks up the temp dir.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure project root on sys.path
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def tmp_data_root(tmp_path, monkeypatch):
    """Redirect every team-mode singleton to a fresh tmp data dir.

    Yields the tmp Path. After the test, singletons are reset so the next
    test gets a fresh state.
    """
    import config

    monkeypatch.setattr(config, "_settings_dir", lambda: tmp_path)

    # Re-init singletons against the redirected base dir.
    from services.agent import agent_manager as am
    am._manager = am.AgentManager()

    from services.job import job_manager as jm
    jm._manager = jm.JobManager()

    from services.run import run_store as rs
    rs._store = rs.RunStore()

    yield tmp_path

    # Reset singletons so the next test gets a clean instance.
    am._manager = am.AgentManager()
    jm._manager = jm.JobManager()
    rs._store = rs.RunStore()


@pytest.fixture
def fake_task_queue(monkeypatch):
    """Register a synthetic CLAUDE_CODE / GEMINI handler that doesn't spawn a CLI.

    Yields a wrapper exposing:
      - last_calls: list of (task_type, params, metadata, session_id) per submit
      - completed_immediately(text=...): make tasks finish with this result
      - failing(): make tasks finish with status=failed
      - blocking(): make tasks remain RUNNING until released

    Useful for exercising the run executor without spawning real subprocesses.
    """
    import threading
    from services.task.task_manager import get_task_queue

    queue = get_task_queue()
    state = {
        "mode": "complete",       # complete | fail | block
        "last_calls": [],
        "release": threading.Event(),
        "calls_made": threading.Event(),
        "result_text": "ok",
    }
    state["release"].set()

    def fake_handler(prompt=None, is_local=False, agent_id=None, **kw):
        state["last_calls"].append({
            "prompt": prompt, "is_local": is_local, "agent_id": agent_id, **kw,
        })
        state["calls_made"].set()
        if state["mode"] == "block":
            state["release"].wait(timeout=10)
        if state["mode"] == "fail":
            raise RuntimeError("fake failure")
        return {
            "result": state["result_text"],
            "session_id": "fake",
            "cost_usd": 0,
            "duration_ms": 1,
        }

    # Re-register for both engines so executor's _engine_to_task_type maps cleanly.
    queue.register_handler("CLAUDE_CODE", fake_handler, description="(test) fake")
    queue.register_handler("GEMINI", fake_handler, description="(test) fake")

    class Wrapper:
        @property
        def last_calls(self):
            return state["last_calls"]
        def reset(self):
            state["last_calls"].clear()
            state["mode"] = "complete"
            state["release"].set()
            state["calls_made"].clear()
            state["result_text"] = "ok"
        def fail(self):    state["mode"] = "fail"
        def block(self):
            state["mode"] = "block"
            state["release"].clear()
        def release(self): state["release"].set()
        def set_result(self, t): state["result_text"] = t

    yield Wrapper()

    state["release"].set()
