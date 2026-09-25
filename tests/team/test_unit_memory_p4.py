"""P4 unit: topic frontmatter, index lines, the legacy MEMORY.md split,
pinned-constraints section edits, proposal parsing / planning helpers."""

from __future__ import annotations

from services.memory import store
from services.memory.reflection import parse_proposal


def test_topic_roundtrip_and_feedback_counters():
    text = store.render_topic({"name": "Deploys: staging first", "description": "never straight to prod",
                               "type": "feedback", "helpful": 2}, "Body line\n")
    meta, body = store.parse_topic(text)
    assert meta == {"name": "Deploys: staging first", "description": "never straight to prod",
                    "type": "feedback", "helpful": 2, "harmful": 0}
    assert body.strip() == "Body line"
    # non-feedback types carry no counters; unknown type → project
    meta2, _ = store.parse_topic(store.render_topic({"name": "x", "type": "weird"}, "b"))
    assert meta2["type"] == "project" and "helpful" not in meta2


def test_index_lines_upsert_drop_and_files():
    idx = store.upsert_index_line("", "user_role.md", {"name": "Role", "description": "data engineer"})
    assert idx == "# Memory Index\n\n- [Role](user_role.md) — data engineer\n"
    idx = store.upsert_index_line(idx, "user_role.md", {"name": "Role", "description": "staff engineer"})
    assert "staff engineer" in idx and "data engineer" not in idx
    idx = store.upsert_index_line(idx, "project_x.md", {"name": "X"})
    assert store.index_files(idx) == ["user_role.md", "project_x.md"]
    assert store.index_files(store.drop_index_line(idx, "user_role.md")) == ["project_x.md"]


def test_index_warnings_limits():
    assert store.index_warnings("- a\n" * 10) == []
    w = store.index_warnings("- a\n" * 201)
    assert w and "201 lines" in w[0]
    assert any("KB" in x for x in store.index_warnings("x" * (26 * 1024)))


def test_split_legacy_by_h2_with_preamble_and_types():
    legacy = ("# Memory\n\nThe team ships weekly.\n\n## User preferences\n- likes short answers\n\n"
              "## Lessons learned\n- never run migrations on Friday\n\n## Links\n- wiki: http://w\n\n"
              "## Current project\nBuilding the ETL.\n### detail\nsub-heading kept\n")
    index, topics = store.split_legacy(legacy)
    names = list(topics)
    assert names == ["project_general.md", "user_user_preferences.md", "feedback_lessons_learned.md",
                     "reference_links.md", "project_current_project.md"]
    meta, body = store.parse_topic(topics["feedback_lessons_learned.md"])
    assert meta["type"] == "feedback" and meta["helpful"] == 0 and "migrations on Friday" in body
    assert "sub-heading kept" in topics["project_current_project.md"]
    assert store.index_files(index) == names
    assert "- [Lessons learned](feedback_lessons_learned.md) — never run migrations on Friday" in index


def test_split_legacy_without_headings_is_one_topic_and_empty_is_nothing():
    index, topics = store.split_legacy("remember: the API key rotates monthly\n")
    assert list(topics) == ["project_notes.md"] and "rotates monthly" in index
    assert store.split_legacy("   \n") == ("", {})


def test_unique_topic_names():
    assert store.unique_topic_name(["user_a.md"], "user", "A") == "user_a_2.md"
    assert store.unique_topic_name([], "bogus", "Hello World!") == "project_hello_world.md"


def test_pinned_section_parse_replace_remove():
    md = "# Rules\nBe nice.\n\n## Pinned constraints\n- never push to main\n\n## Other\nx\n"
    assert store.pinned_from_text(md) == "- never push to main"
    new = store.replace_pinned(md, "- stay in the workspace")
    assert store.pinned_from_text(new) == "- stay in the workspace" and "## Other\nx" in new and "Be nice." in new
    gone = store.replace_pinned(new, "")
    assert "Pinned constraints" not in gone and "## Other" in gone and "Be nice." in gone
    added = store.replace_pinned("# Rules\n", "only read")
    assert added.endswith("## Pinned constraints\n\nonly read\n") and store.pinned_from_text(added) == "only read"
    assert store.pinned_from_text(store.replace_pinned("", "a")) == "a"


def test_parse_proposal_variants():
    body = '{"summary": "s", "operations": [{"op": "NOOP"}]}'
    assert parse_proposal(body)["summary"] == "s"
    assert parse_proposal("Here you go:\n```json\n" + body + "\n```\nbye")["operations"][0]["op"] == "NOOP"
    assert parse_proposal("prefix " + body + " suffix")["summary"] == "s"
    assert parse_proposal("no json here") is None
    assert parse_proposal('{"summary": "no ops"}') is None


def test_check_topic_name_rejects_traversal_and_index():
    import pytest
    for bad in ("../x.md", "MEMORY.md", "a/b.md", ".hidden.md", "x.txt", ""):
        with pytest.raises(ValueError):
            store.check_topic_name(bad)
    assert store.check_topic_name("feedback_x.md") == "feedback_x.md"
