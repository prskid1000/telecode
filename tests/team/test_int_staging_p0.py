"""B8 — staging never destroys the workspace's own CLAUDE.md / AGENTS.md /
SOUL / USER / MEMORY, repairs a crashed run, and merges write-back three-way."""

from __future__ import annotations

from services.agent.agent_manager import get_agent_manager
from services.task import staging
from services.task.staging import merge3, stage_for_run


def _agent(**files):
    am = get_agent_manager()
    a = am.create_agent("stage-p0")
    am.set_internal_files(a["id"], {"AGENT.md": "agent rules", "MEMORY.md": "m1\n", **files})
    return a["id"]


def test_b8_existing_workspace_files_restored_after_run(tmp_data_root, tmp_path):
    aid = _agent()
    ws = tmp_path / "ws"
    ws.mkdir()
    own = {"AGENTS.md": "project AGENTS", "MEMORY.md": "project memory", "SOUL.md": "project soul"}
    for k, v in own.items():
        (ws / k).write_text(v, encoding="utf-8")

    with stage_for_run(aid, "ws-own", ws, engine="codex"):
        assert (ws / "AGENTS.md").read_text(encoding="utf-8") == "agent rules"
        assert (ws / "MEMORY.md").read_text(encoding="utf-8") == "m1\n"
    for k, v in own.items():
        assert (ws / k).read_text(encoding="utf-8") == v, k
    assert not (ws / "USER.md").exists()                     # was absent → removed again


def test_b8_external_agent_md_leaves_claude_md_alone(tmp_data_root, tmp_path):
    aid = _agent()
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "CLAUDE.md").write_text("project CLAUDE", encoding="utf-8")
    ext = tmp_path / "runtime" / "agent.md"
    with stage_for_run(aid, "ws-ext", ws, engine="claude", agent_md_file=ext):
        assert (ws / "CLAUDE.md").read_text(encoding="utf-8") == "project CLAUDE"
        assert ext.read_text(encoding="utf-8") == "agent rules"
    assert (ws / "CLAUDE.md").read_text(encoding="utf-8") == "project CLAUDE"
    assert not ext.exists()


def test_b8_crashed_run_repaired_on_next_stage(tmp_data_root, tmp_path):
    aid = _agent()
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("project AGENTS", encoding="utf-8")
    # Simulate a crash: back up + stage, but never unstage.
    staging._backup_existing(ws, staging._staged_filenames("codex"), staging._backup_dir(ws))
    staging._stage(aid, ws, "codex")
    assert (ws / "AGENTS.md").read_text(encoding="utf-8") == "agent rules"
    # Next run restores first, then backs up the *real* file again.
    with stage_for_run(aid, "ws-crash", ws, engine="codex"):
        pass
    assert (ws / "AGENTS.md").read_text(encoding="utf-8") == "project AGENTS"


def test_b8_memory_concurrent_appends_both_kept(tmp_data_root, tmp_path):
    aid = _agent()
    am = get_agent_manager()
    ws = tmp_path / "ws"
    ws.mkdir()
    with stage_for_run(aid, "ws-m", ws, engine="codex"):
        (ws / "MEMORY.md").write_text("m1\nfrom this run\n", encoding="utf-8")
        # another run on another workspace wrote back meanwhile
        am.set_internal_files(aid, {"MEMORY.md": "m1\nfrom other run\n"})
    merged = am.get_internal_files(aid)["MEMORY.md"]
    assert "from this run" in merged and "from other run" in merged
    assert "<<<<<<<" not in merged                            # two appends merge cleanly


def test_b8_memory_true_conflict_keeps_both_with_markers(tmp_data_root, tmp_path, caplog):
    aid = _agent(**{"MEMORY.md": "line a\nline b\n"})
    am = get_agent_manager()
    ws = tmp_path / "ws"
    ws.mkdir()
    with stage_for_run(aid, "ws-c", ws, engine="codex"):
        (ws / "MEMORY.md").write_text("line a\nOURS b\n", encoding="utf-8")
        am.set_internal_files(aid, {"MEMORY.md": "line a\nTHEIRS b\n"})
    merged = am.get_internal_files(aid)["MEMORY.md"]
    assert staging.CONFLICT_OURS in merged and staging.CONFLICT_THEIRS in merged
    assert "OURS b" in merged and "THEIRS b" in merged
    assert any("Write-back conflict" in r.message for r in caplog.records)


def test_b8_merge3_cases():
    assert merge3("a\n", "a\nx\n", "a\n") == ("a\nx\n", False)           # only ours
    assert merge3("a\n", "a\n", "a\ny\n") == ("a\ny\n", False)           # only theirs
    text, c = merge3("1\n2\n3\n", "ONE\n2\n3\n", "1\n2\nTHREE\n")        # disjoint
    assert (text, c) == ("ONE\n2\nTHREE\n", False)
    text, c = merge3("x", "x\nours", "x\ntheirs")                         # no trailing newline
    assert not c and "ours" in text and "theirs" in text
