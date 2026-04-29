"""Unit tests for services.heartbeat.parser."""

from __future__ import annotations

from services.heartbeat.parser import parse, next_fires


def test_no_yaml_block_returns_no_entries_no_errors():
    text = "# heartbeat\n\nJust some prose, no YAML."
    r = parse(text)
    assert r.ok
    assert r.entries == []


def test_minimal_valid_entry():
    text = (
        "```yaml\n"
        "- name: tick\n"
        '  cron: "*/5 * * * *"\n'
        "  prompt: ping\n"
        "```\n"
    )
    r = parse(text)
    assert r.ok
    assert len(r.entries) == 1
    e = r.entries[0]
    assert e.name == "tick"
    assert e.cron == "*/5 * * * *"
    assert e.prompt == "ping"
    assert e.workspace == "ephemeral"   # default
    assert e.engine == "claude_code"     # default
    assert e.enabled is True             # default


def test_persistent_requires_workspace_id():
    text = (
        "```yaml\n"
        "- name: hourly\n"
        '  cron: "0 * * * *"\n'
        "  prompt: cleanup\n"
        "  workspace: persistent\n"
        "```\n"
    )
    r = parse(text)
    assert not r.ok
    assert any("workspace_id required" in e["msg"] for e in r.errors)


def test_persistent_with_workspace_id_ok():
    text = (
        "```yaml\n"
        "- name: hourly\n"
        '  cron: "0 * * * *"\n'
        "  prompt: cleanup\n"
        "  workspace: persistent\n"
        "  workspace_id: ws-abc\n"
        "```\n"
    )
    r = parse(text)
    assert r.ok
    assert r.entries[0].workspace == "persistent"
    assert r.entries[0].workspace_id == "ws-abc"


def test_invalid_cron_caught():
    text = (
        "```yaml\n"
        "- name: bad\n"
        '  cron: "not a cron"\n'
        "  prompt: x\n"
        "```\n"
    )
    r = parse(text)
    assert not r.ok
    assert any("invalid cron" in e["msg"] for e in r.errors)


def test_duplicate_name_blocks_second_entry():
    text = (
        "```yaml\n"
        "- name: same\n"
        '  cron: "0 9 * * *"\n'
        "  prompt: a\n"
        "- name: same\n"
        '  cron: "0 10 * * *"\n'
        "  prompt: b\n"
        "```\n"
    )
    r = parse(text)
    assert len(r.entries) == 1     # first one wins
    assert any("duplicate name" in e["msg"] for e in r.errors)


def test_invalid_engine_rejected():
    text = (
        "```yaml\n"
        "- name: x\n"
        '  cron: "0 * * * *"\n'
        "  prompt: y\n"
        "  engine: gpt5\n"
        "```\n"
    )
    r = parse(text)
    assert not r.ok
    assert any("engine must be one of" in e["msg"] for e in r.errors)


def test_disabled_entry_parsed_but_marked():
    text = (
        "```yaml\n"
        "- name: paused\n"
        '  cron: "0 * * * *"\n'
        "  prompt: x\n"
        "  enabled: false\n"
        "```\n"
    )
    r = parse(text)
    assert r.ok
    assert r.entries[0].enabled is False


def test_multiple_yaml_fences_concatenated():
    text = (
        "```yaml\n- name: a\n  cron: 0 9 * * *\n  prompt: a\n```\n"
        "Some prose between.\n"
        "```yaml\n- name: b\n  cron: 0 10 * * *\n  prompt: b\n```\n"
    )
    r = parse(text)
    assert r.ok
    assert {e.name for e in r.entries} == {"a", "b"}


def test_prose_outside_fences_ignored():
    text = (
        "Heading\n\nNotes about why these exist.\n\n"
        "```yaml\n- name: tick\n  cron: 0 9 * * *\n  prompt: x\n```\n"
        "More notes after.\n"
    )
    r = parse(text)
    assert r.ok
    assert len(r.entries) == 1


def test_partial_validity_other_entries_still_parse():
    """A bad entry shouldn't poison its siblings."""
    text = (
        "```yaml\n"
        "- name: good\n"
        '  cron: "0 9 * * *"\n'
        "  prompt: ok\n"
        "- name: bad\n"
        '  cron: "garbage"\n'
        "  prompt: y\n"
        "- name: also-good\n"
        '  cron: "0 10 * * *"\n'
        "  prompt: also ok\n"
        "```\n"
    )
    r = parse(text)
    assert not r.ok
    assert {e.name for e in r.entries} == {"good", "also-good"}
    assert any(e.get("name") == "bad" for e in r.errors)


def test_next_fires_returns_iso_strings():
    text = "```yaml\n- name: x\n  cron: 0 9 * * *\n  prompt: y\n```"
    r = parse(text)
    fires = next_fires(r.entries[0], count=3)
    assert len(fires) == 3
    for f in fires:
        assert f.endswith("Z")
        assert "T" in f
