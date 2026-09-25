"""P4 integration: git-versioned internal/, the one-time migration, staging
write-back as commits (fast-forward / merge of a per-run branch / merge3
fallback / direct writes), topic-file reachability, per-agent skills staging,
pinned constraints as the one source, engine_extras."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from services.agent.agent_manager import get_agent_manager
from services.memory import engine_extras, pinned_constraints, repo, set_pinned_constraints, store
from services.skills import agent_skills
from services.task import staging
from services.task.staging import stage_for_run


def _agent(**files):
    am = get_agent_manager()
    a = am.create_agent("mem-p4")
    if files:
        am.set_internal_files(a["id"], files)
    return a["id"]


def _idir(aid) -> Path:
    return get_agent_manager()._get_agent_internal_dir(aid)


def _subjects(aid):
    return [c["subject"] for c in repo.history(_idir(aid), limit=100)]


@pytest.fixture
def as_task(monkeypatch):
    """Pretend the stage runs inside a task (run R1 / step S1 unless overridden)."""
    info = {"task_id": "T1", "run_id": "R1", "step_id": "S1", "trigger_id": None, "engine": "codex"}
    monkeypatch.setattr(staging, "_task_info", lambda: dict(info))
    return info


# ── repo + migration ────────────────────────────────────────────────────────

def test_new_agent_is_a_git_repo_and_edits_are_commits(tmp_data_root):
    aid = _agent()
    d = _idir(aid)
    assert repo.is_repo(d) and (d / "memory" / "MEMORY.md").is_file() and not (d / "MEMORY.md").exists()
    get_agent_manager().set_internal_files(aid, {"SOUL.md": "I am", "MEMORY.md": "idx"})
    subj = _subjects(aid)
    assert subj[0] == "ui: edit SOUL.md, MEMORY.md" and subj[-1].startswith("agent created")
    assert not repo.dirty(d)
    assert (d / "memory" / "MEMORY.md").read_bytes() == b"idx"


def test_legacy_memory_md_is_split_once_and_kept_in_history(tmp_data_root):
    aid = _agent()
    d = _idir(aid)
    # rebuild a pre-P4 agent: no repo, MEMORY.md at the top, CRLF from Windows write_text
    shutil.rmtree(d / ".git", onerror=lambda f, p, e: (Path(p).chmod(0o700), f(p)))
    shutil.rmtree(d / "memory")
    legacy = "# Memory\r\n\r\n## Lessons\r\n- never deploy on Friday\r\n\r\n## Links\r\n- http://wiki\r\n"
    (d / "MEMORY.md").write_bytes(legacy.encode())
    files = get_agent_manager().get_internal_files(aid)          # first read migrates
    assert "feedback_lessons.md" in files["MEMORY.md"] and "reference_links.md" in files["MEMORY.md"]
    assert not (d / "MEMORY.md").exists() and not (d / "MEMORY.legacy.md").exists()
    assert (d / "memory" / "feedback_lessons.md").is_file()
    commits = repo.history(d, limit=20)
    kept = next(c for c in commits if "MEMORY.legacy.md" in c["subject"])
    assert repo.read_at(d, kept["sha"], "MEMORY.legacy.md") == legacy.replace("\r\n", "\n")
    assert commits[0]["subject"].startswith("migrate: split MEMORY.md into an index + 2")
    n = len(commits)
    get_agent_manager().get_internal_files(aid)                  # idempotent
    assert len(repo.history(d, limit=20)) == n
    assert store.migrate_all() == 1


# ── write-back as commits ───────────────────────────────────────────────────

def test_writeback_is_a_run_commit_fast_forward(tmp_data_root, tmp_path, as_task):
    aid = _agent(**{"MEMORY.md": "m1\n", "AGENT.md": "rules"})
    ws = tmp_path / "ws"
    ws.mkdir()
    with stage_for_run(aid, "ws1", ws, engine="codex"):
        (ws / "MEMORY.md").write_text("m1\nlearned\n", encoding="utf-8")
        (ws / "AGENTS.md").write_text("rules v2", encoding="utf-8")
    c = repo.history(_idir(aid), limit=5)[0]
    assert c["subject"] == "run:R1 step:S1" and c["run_id"] == "R1" and c["step_id"] == "S1"
    assert c["task_id"] == "T1" and len(c["parents"]) == 1
    assert {f["path"] for f in c["files"]} == {"memory/MEMORY.md", "AGENT.md"}
    assert get_agent_manager().get_internal_files(aid)["AGENT.md"] == "rules v2"


def test_standalone_task_commit_label(tmp_data_root, tmp_path, as_task):
    as_task.update(run_id=None, step_id=None, task_id="TASK9")
    aid = _agent(**{"MEMORY.md": "a\n"})
    ws = tmp_path / "ws"
    ws.mkdir()
    with stage_for_run(aid, "ws1", ws, engine="codex"):
        (ws / "MEMORY.md").write_text("a\nb\n", encoding="utf-8")
    assert _subjects(aid)[0] == "task:TASK9"


def test_concurrent_writes_merge_a_per_run_branch(tmp_data_root, tmp_path, as_task):
    aid = _agent(**{"MEMORY.md": "top\n\nmiddle\n\nbottom\n"})
    am = get_agent_manager()
    ws = tmp_path / "ws"
    ws.mkdir()
    with stage_for_run(aid, "ws1", ws, engine="codex"):
        (ws / "MEMORY.md").write_text("TOP\n\nmiddle\n\nbottom\n", encoding="utf-8")
        am.set_internal_files(aid, {"MEMORY.md": "top\n\nmiddle\n\nBOTTOM\n"})     # another writer meanwhile
    assert am.get_internal_files(aid)["MEMORY.md"] == "TOP\n\nmiddle\n\nBOTTOM\n"   # git merged cleanly
    top = repo.history(_idir(aid), limit=5)
    assert top[0]["subject"] == "merge run:R1 step:S1" and len(top[0]["parents"]) == 2
    assert "run:R1 step:S1" in [c["subject"] for c in top]
    assert not repo.dirty(_idir(aid))
    assert not [r for r in repo._out(repo._run(_idir(aid), "for-each-ref", "refs/heads/incoming")).splitlines()]


def test_direct_writes_into_memory_dir_are_committed_with_the_run(tmp_data_root, tmp_path, as_task):
    aid = _agent()
    ws = tmp_path / "ws"
    ws.mkdir()
    with stage_for_run(aid, "ws1", ws, engine="claude"):
        # e.g. Claude's auto memory (autoMemoryDirectory = internal/memory) writing a topic
        repo.write_text(store.memory_dir(aid) / "feedback_tabs.md",
                        store.render_topic({"name": "Tabs", "type": "feedback"}, "use tabs"))
    c = repo.history(_idir(aid), limit=3)[0]
    assert c["subject"] == "run:R1 step:S1 (memory files)"
    assert [f["path"] for f in c["files"]] == ["memory/feedback_tabs.md"]


def test_staged_index_names_the_topic_dir_and_header_is_stripped(tmp_data_root, tmp_path, as_task):
    aid = _agent(**{"MEMORY.md": "# Memory Index\n\n- [Tabs](feedback_tabs.md) — use tabs\n"})
    repo.write_text(store.memory_dir(aid) / "feedback_tabs.md", "---\nname: Tabs\ntype: feedback\n---\nuse tabs\n")
    ws = tmp_path / "ws"
    ws.mkdir()
    with stage_for_run(aid, "ws1", ws, engine="codex") as snap:
        staged = (ws / "MEMORY.md").read_text(encoding="utf-8")
        assert staged.startswith("<!-- memory topic files: ") and str(store.memory_dir(aid)) in staged.splitlines()[0]
        assert "<!--" not in snap["MEMORY.md"]
        (ws / "MEMORY.md").write_text(staged + "- [New](project_new.md) — n\n", encoding="utf-8")
    idx = get_agent_manager().get_internal_files(aid)["MEMORY.md"]
    assert not idx.startswith("<!--") and idx.endswith("- [New](project_new.md) — n\n")


def test_unchanged_run_makes_no_commit(tmp_data_root, tmp_path, as_task):
    aid = _agent(**{"MEMORY.md": "x"})
    n = len(_subjects(aid))
    ws = tmp_path / "ws"
    ws.mkdir()
    with stage_for_run(aid, "ws1", ws, engine="codex"):
        pass
    assert len(_subjects(aid)) == n


# ── skills staging ──────────────────────────────────────────────────────────

def test_agent_skills_staged_into_both_roots_and_removed(tmp_data_root, tmp_path, as_task):
    aid = _agent()
    agent_skills.upsert_skill(aid, "deploy", "---\ndescription: how we deploy\n---\nsteps")
    agent_skills.write_skill_file(aid, "deploy", "ref/notes.md", b"extra")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".claude").mkdir()                                          # pre-existing parent stays
    (ws / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    with stage_for_run(aid, "ws1", ws, engine="codex"):
        for root in (".agents/skills", ".claude/skills"):
            assert (ws / root / "deploy" / "SKILL.md").read_text(encoding="utf-8").endswith("steps")
            assert (ws / root / "deploy" / "ref" / "notes.md").read_bytes() == b"extra"
    assert not (ws / ".agents").exists() and not (ws / ".claude" / "skills").exists()
    assert (ws / ".claude" / "settings.json").exists()
    assert "ui: add skill deploy" in _subjects(aid)


def test_workspace_skill_is_never_clobbered(tmp_data_root, tmp_path, as_task):
    aid = _agent()
    agent_skills.upsert_skill(aid, "deploy", "agent version")
    ws = tmp_path / "ws"
    own = ws / ".claude" / "skills" / "deploy"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("workspace version", encoding="utf-8")
    with stage_for_run(aid, "ws1", ws, engine="claude"):
        assert (own / "SKILL.md").read_text(encoding="utf-8") == "workspace version"
        assert (ws / ".agents" / "skills" / "deploy" / "SKILL.md").read_text(encoding="utf-8") == "agent version"
    assert (own / "SKILL.md").read_text(encoding="utf-8") == "workspace version"
    assert not (ws / ".agents").exists()


def test_crashed_run_skills_are_cleaned_by_next_stage(tmp_data_root, tmp_path, as_task):
    aid = _agent()
    agent_skills.upsert_skill(aid, "s1", "x")
    ws = tmp_path / "ws"
    ws.mkdir()
    staging._backup_existing(ws, staging._staged_filenames("codex"), staging._backup_dir(ws))
    staging._stage(aid, ws, "codex")
    staging._manifest_add(ws, "skills", agent_skills.stage(aid, ws))
    assert (ws / ".agents" / "skills" / "s1").is_dir()
    agent_skills.delete_skill(aid, "s1")                  # the next run has no skills at all
    with stage_for_run(aid, "ws1", ws, engine="codex"):
        assert not (ws / ".agents").exists()
    assert not (ws / ".claude").exists() and not (ws / "AGENTS.md").exists()


def test_promote_and_copy_to_global(tmp_data_root, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_SKILLS_DIR", str(tmp_path / "global-skills"))
    from services.skills import skill_store
    skill_store.upsert_skill("g-one", "global one")
    aid = _agent()
    got = agent_skills.promote_to_agent("g-one", aid)
    assert got["content"] == "global one"
    with pytest.raises(agent_skills.SkillExists):
        agent_skills.promote_to_agent("g-one", aid)
    agent_skills.upsert_skill(aid, "mine", "agent skill")
    out = agent_skills.copy_to_global(aid, "mine")
    assert out["content"] == "agent skill" and (tmp_path / "global-skills" / "mine" / "SKILL.md").exists()
    with pytest.raises(agent_skills.SkillExists):
        agent_skills.copy_to_global(aid, "mine")


# ── pinned constraints, engine extras ───────────────────────────────────────

def test_pinned_constraints_one_source_for_rotation_and_triggers(tmp_data_root):
    aid = _agent(**{"AGENT.md": "# Rules\nBe brief.\n"})
    set_pinned_constraints(aid, "- never push to main")
    assert pinned_constraints(aid) == "- never push to main"
    md = get_agent_manager().get_internal_files(aid)["AGENT.md"]
    assert md.startswith("# Rules\nBe brief.") and "## Pinned constraints" in md
    assert _subjects(aid)[0] == "ui: edit pinned constraints"
    from services.task.handlers._common import pinned_constraints as common_pinned
    from services.triggers.fire import _pinned
    assert common_pinned(aid) == "- never push to main"
    assert _pinned({"pinned": "trigger rule", "target": {"agent_id": aid}}) == "trigger rule\n- never push to main"


def test_engine_extras_per_engine(tmp_data_root, monkeypatch):
    aid = _agent()
    mdir = str(store.memory_dir(aid))
    cl = engine_extras(aid, "claude_code", None)
    assert cl["args"][0] == "--settings" and cl["env"] == {} and cl["add_dirs"] == [mdir]
    assert json.loads(Path(cl["args"][1]).read_text(encoding="utf-8")) == {"autoMemoryDirectory": mdir} == cl["settings"]
    assert Path(cl["args"][1]).parent == store.agent_state_dir(aid)          # outside the repo
    for e in ("codex", "antigravity"):
        assert engine_extras(aid, e, None) == {"args": [], "env": {}, "add_dirs": [mdir]}
    assert engine_extras(None, "claude_code") == {"args": [], "env": {}, "add_dirs": []}
    assert engine_extras("no-such-agent", "codex") == {"args": [], "env": {}, "add_dirs": []}
    from services.memory import reflection
    monkeypatch.setattr(reflection, "current_task_is_reflection", lambda a: True)
    assert engine_extras(aid, "claude_code") == {"args": [], "env": {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
                                                 "add_dirs": []}
    assert engine_extras(aid, "codex") == {"args": [], "env": {}, "add_dirs": []}


# ── history / diff / revert ─────────────────────────────────────────────────

def test_history_diff_and_revert(tmp_data_root):
    aid = _agent(**{"MEMORY.md": "one\n"})
    store.write_topic(aid, "user_role.md", meta={"name": "Role", "description": "engineer", "type": "user"},
                      body="staff engineer")
    d = _idir(aid)
    c = repo.history(d, limit=1)[0]
    shown = repo.show(d, c["sha"])
    assert {f["path"] for f in shown["files"]} == {"memory/user_role.md", "memory/MEMORY.md"}
    assert "+staff engineer" in shown["diff"]
    new = repo.revert(d, c["sha"])
    assert new and not (store.memory_dir(aid) / "user_role.md").exists()
    assert store.read_index(aid) == "one\n"
    assert repo.history(d, limit=1)[0]["subject"].startswith("revert: ui: add memory user_role.md")
    with pytest.raises(ValueError):
        repo.revert(d, repo.history(d, limit=100)[-1]["sha"])            # the root commit
    # a revert whose lines changed since → conflict, tree untouched
    get_agent_manager().set_internal_files(aid, {"MEMORY.md": "two\n"})
    first_edit = next(x for x in repo.history(d, limit=100) if x["subject"] == "ui: edit MEMORY.md")
    get_agent_manager().set_internal_files(aid, {"MEMORY.md": "three\n"})
    with pytest.raises(repo.MemoryConflict):
        repo.revert(d, first_edit["sha"])
    assert store.read_index(aid) == "three\n" and not repo.dirty(d)
