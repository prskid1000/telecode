"""Integration tests for services.task.staging (stage / writeback / unstage)."""

from __future__ import annotations

from pathlib import Path

from services.agent.agent_manager import get_agent_manager
from services.task.staging import stage_for_run, PASSTHROUGH_FILES


def _seed_agent_with_internals(soul="soul-v1", agent_md="agent-v1", memory="mem-v1"):
    am = get_agent_manager()
    a = am.create_agent("stage-bot", soul=soul)
    am.set_internal_files(a["id"], {
        "USER.md": "user-v1",
        "AGENT.md": agent_md,
        "MEMORY.md": memory,
    })
    return a


def test_stage_copies_passthrough_files_verbatim(tmp_data_root, tmp_path):
    a = _seed_agent_with_internals()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with stage_for_run(a["id"], "ws-1", workspace, engine="claude") as snapshot:
        for fname in PASSTHROUGH_FILES:
            assert (workspace / fname).exists()
        assert (workspace / "SOUL.md").read_text(encoding="utf-8") == "soul-v1"
        assert (workspace / "USER.md").read_text(encoding="utf-8") == "user-v1"
        assert (workspace / "MEMORY.md").read_text(encoding="utf-8") == "mem-v1"
        # Snapshot captures these too
        assert snapshot["SOUL.md"] == "soul-v1"


def test_stage_renames_agent_md_to_claude_md_for_claude(tmp_data_root, tmp_path):
    a = _seed_agent_with_internals(agent_md="my-instructions")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with stage_for_run(a["id"], "ws-1", workspace, engine="claude"):
        assert (workspace / "CLAUDE.md").read_text(encoding="utf-8") == "my-instructions"
        assert not (workspace / "AGENT.md").exists()
        assert not (workspace / "GEMINI.md").exists()


def test_stage_renames_to_gemini_md_for_gemini(tmp_data_root, tmp_path):
    a = _seed_agent_with_internals(agent_md="gemini-rules")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with stage_for_run(a["id"], "ws-1", workspace, engine="gemini"):
        assert (workspace / "GEMINI.md").read_text(encoding="utf-8") == "gemini-rules"
        assert not (workspace / "CLAUDE.md").exists()


def test_heartbeat_md_never_staged(tmp_data_root, tmp_path):
    am = get_agent_manager()
    a = am.create_agent("hb-only-bot")
    am.set_internal_files(a["id"], {"HEARTBEAT.md": "schedule data"})
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with stage_for_run(a["id"], "ws-1", workspace, engine="claude"):
        assert not (workspace / "HEARTBEAT.md").exists()


def test_writeback_persists_modifications(tmp_data_root, tmp_path):
    a = _seed_agent_with_internals(memory="initial memory")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with stage_for_run(a["id"], "ws-1", workspace, engine="claude"):
        # Simulate the agent appending to MEMORY.md
        (workspace / "MEMORY.md").write_text("initial memory\nappended note", encoding="utf-8")
    # After context exits, agent storage reflects the change
    am = get_agent_manager()
    files = am.get_internal_files(a["id"])
    assert "appended note" in files["MEMORY.md"]


def test_writeback_persists_claude_md_changes_back_to_agent_md(tmp_data_root, tmp_path):
    a = _seed_agent_with_internals(agent_md="rule v1")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with stage_for_run(a["id"], "ws-1", workspace, engine="claude"):
        # Simulate agent rewriting CLAUDE.md (its visible name)
        (workspace / "CLAUDE.md").write_text("rule v2", encoding="utf-8")
    am = get_agent_manager()
    files = am.get_internal_files(a["id"])
    assert files["AGENT.md"] == "rule v2"


def test_writeback_skipped_when_unchanged(tmp_data_root, tmp_path):
    a = _seed_agent_with_internals(soul="unchanged")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    am = get_agent_manager()
    soul_file = am._get_agent_internal_dir(a["id"]) / "SOUL.md"
    mtime_before = soul_file.stat().st_mtime_ns
    import time; time.sleep(0.01)    # ensure mtime resolution
    with stage_for_run(a["id"], "ws-1", workspace, engine="claude"):
        pass    # no edits
    # mtime unchanged → no writeback happened
    assert soul_file.stat().st_mtime_ns == mtime_before


def test_unstage_deletes_staged_files(tmp_data_root, tmp_path):
    a = _seed_agent_with_internals()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Drop a non-staged file the agent created mid-run
    extra = workspace / "report.md"
    extra.parent.mkdir(parents=True, exist_ok=True)
    with stage_for_run(a["id"], "ws-1", workspace, engine="claude"):
        extra.write_text("artefact", encoding="utf-8")
    # All staged files gone
    for fname in ("SOUL.md", "USER.md", "MEMORY.md", "CLAUDE.md"):
        assert not (workspace / fname).exists(), f"{fname} should be unstaged"
    # Non-staged file preserved
    assert extra.exists()
    assert extra.read_text(encoding="utf-8") == "artefact"


def test_unstage_runs_on_exception_inside_context(tmp_data_root, tmp_path):
    a = _seed_agent_with_internals()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    try:
        with stage_for_run(a["id"], "ws-1", workspace, engine="claude"):
            raise RuntimeError("simulated CLI crash")
    except RuntimeError:
        pass
    assert not (workspace / "SOUL.md").exists()
    assert not (workspace / "CLAUDE.md").exists()


def test_no_staging_when_agent_id_falsy(tmp_data_root, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with stage_for_run(None, "ws-1", workspace, engine="claude") as snap:
        assert snap == {}
        assert list(workspace.iterdir()) == []
