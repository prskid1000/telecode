"""Integration tests for AgentManager (filesystem-backed)."""

from __future__ import annotations

import threading
from services.agent.agent_manager import get_agent_manager, INTERNAL_FILES


def test_create_agent_seeds_internal_files(tmp_data_root):
    m = get_agent_manager()
    a = m.create_agent("alice", soul="I am Alice")
    files = m.get_internal_files(a["id"])
    # All five whitelisted internal files exist
    assert set(files.keys()) == set(INTERNAL_FILES)
    assert files["SOUL.md"] == "I am Alice"
    # The other four are seeded empty
    for f in ("USER.md", "AGENT.md", "MEMORY.md", "HEARTBEAT.md"):
        assert files[f] == ""


def test_create_agent_without_soul_starts_empty():
    m = get_agent_manager()
    a = m.create_agent("nobody")
    files = m.get_internal_files(a["id"])
    assert all(v == "" for v in files.values())


def test_set_internal_files_writes_partial_update(tmp_data_root):
    m = get_agent_manager()
    a = m.create_agent("eve")
    m.set_internal_files(a["id"], {"MEMORY.md": "remembered"})
    files = m.get_internal_files(a["id"])
    assert files["MEMORY.md"] == "remembered"
    assert files["SOUL.md"] == ""    # untouched


def test_set_internal_files_rejects_unknown_filenames(tmp_data_root):
    m = get_agent_manager()
    a = m.create_agent("eve")
    m.set_internal_files(a["id"], {
        "MEMORY.md": "kept",
        "SECRETS.md": "should be ignored",
    })
    files = m.get_internal_files(a["id"])
    assert files["MEMORY.md"] == "kept"
    assert "SECRETS.md" not in files
    # Confirm no rogue file ended up on disk
    internal_dir = m._get_agent_internal_dir(a["id"])
    assert not (internal_dir / "SECRETS.md").exists()


def test_set_internal_files_returns_false_for_missing_agent(tmp_data_root):
    m = get_agent_manager()
    assert m.set_internal_files("nonexistent-id", {"MEMORY.md": "x"}) is False


def test_delete_agent_removes_internal_dir(tmp_data_root):
    m = get_agent_manager()
    a = m.create_agent("dead")
    m.set_internal_files(a["id"], {"MEMORY.md": "x"})
    internal_dir = m._get_agent_internal_dir(a["id"])
    assert internal_dir.exists()
    m.delete_agent(a["id"])
    assert not internal_dir.exists()
    # And no leftover JSON either
    assert m.get_agent(a["id"]) is None


def test_list_agents_orders_by_updated_desc(tmp_data_root):
    import time
    m = get_agent_manager()
    a = m.create_agent("first")
    time.sleep(1.1)    # iso timestamp resolution is 1s
    b = m.create_agent("second")
    time.sleep(1.1)
    m.update_agent(a["id"], {"name": "first-updated"})
    listed = [x["name"] for x in m.list_agents()]
    assert listed[0] == "first-updated"


def test_per_agent_lock_serializes_writeback(tmp_data_root):
    """Concurrent writes from two threads must not corrupt files."""
    m = get_agent_manager()
    a = m.create_agent("loaded")

    errs = []
    def writer(tag):
        try:
            for _ in range(20):
                m.set_internal_files(a["id"], {"MEMORY.md": tag * 1000})
        except Exception as exc:
            errs.append(exc)

    t1 = threading.Thread(target=writer, args=("A",))
    t2 = threading.Thread(target=writer, args=("B",))
    t1.start(); t2.start()
    t1.join(); t2.join()
    assert errs == []
    # File should be one or the other (consistent), not torn.
    final = m.get_internal_files(a["id"])["MEMORY.md"]
    assert final in ("A" * 1000, "B" * 1000)


def test_user_files_dir_independent_of_internal(tmp_data_root):
    """User-uploaded files (data/agents/<id>/files/) and internal/ are
    siblings; one shouldn't leak into the other's listing."""
    m = get_agent_manager()
    a = m.create_agent("split")
    m.save_file(a["id"], "report.md", b"# Report\n")
    m.set_internal_files(a["id"], {"MEMORY.md": "internal note"})

    user_files = m.list_files(a["id"])
    assert [f["path"] for f in user_files] == ["report.md"]

    internal = m.get_internal_files(a["id"])
    assert "report.md" not in internal
    assert internal["MEMORY.md"] == "internal note"
