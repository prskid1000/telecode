"""Unit tests for job_manager._normalize_pipeline (pure function, no I/O)."""

from __future__ import annotations

from services.job.job_manager import _normalize_pipeline


def _phases(p):
    return [s["phase"] for s in p["steps"]]

def _agents(p):
    return [s["agent_id"] for s in p["steps"]]


def test_single_truncates_to_one_step_phase_zero():
    p = _normalize_pipeline({"mode": "single", "steps": [
        {"agent_id": "a"}, {"agent_id": "b"}, {"agent_id": "c"},
    ]})
    assert p["mode"] == "single"
    assert _phases(p) == [0]
    assert _agents(p) == ["a"]


def test_sequential_phase_per_step():
    p = _normalize_pipeline({"mode": "sequential", "steps": [
        {"agent_id": "a"}, {"agent_id": "b"}, {"agent_id": "c"},
    ]})
    assert _phases(p) == [0, 1, 2]


def test_parallel_all_phase_zero():
    p = _normalize_pipeline({"mode": "parallel", "steps": [
        {"agent_id": "a"}, {"agent_id": "b"}, {"agent_id": "c"},
    ]})
    assert _phases(p) == [0, 0, 0]


def test_custom_explicit_phases_kept():
    p = _normalize_pipeline({"mode": "custom", "steps": [
        {"agent_id": "a", "phase": 0},
        {"agent_id": "b", "phase": 1},
        {"agent_id": "c", "phase": 1},
        {"agent_id": "d", "phase": 2},
    ]})
    assert _phases(p) == [0, 1, 1, 2]


def test_custom_gappy_phases_renumbered_contiguous():
    p = _normalize_pipeline({"mode": "custom", "steps": [
        {"agent_id": "a", "phase": 5},
        {"agent_id": "b", "phase": 5},
        {"agent_id": "c", "phase": 10},
    ]})
    # Renumbered to 0-based contiguous
    assert _phases(p) == [0, 0, 1]


def test_custom_missing_phase_falls_back_to_index():
    p = _normalize_pipeline({"mode": "custom", "steps": [
        {"agent_id": "a"},
        {"agent_id": "b", "phase": 0},
        {"agent_id": "c"},
    ]})
    # a → idx 0; b → 0 (explicit); c → idx 2 → renumbered to 1
    assert _phases(p) == [0, 0, 1]


def test_invalid_mode_falls_back_to_single():
    p = _normalize_pipeline({"mode": "🤡", "steps": [
        {"agent_id": "a"}, {"agent_id": "b"},
    ]})
    assert p["mode"] == "single"
    assert _phases(p) == [0]


def test_steps_without_agent_id_dropped():
    p = _normalize_pipeline({"mode": "sequential", "steps": [
        {"agent_id": "a"},
        {"name": "no agent"},                # dropped
        {"agent_id": "b"},
        "not even a dict",                    # dropped
    ]})
    assert _agents(p) == ["a", "b"]
    assert _phases(p) == [0, 1]


def test_step_id_assigned_when_missing():
    p = _normalize_pipeline({"mode": "single", "steps": [{"agent_id": "a"}]})
    assert p["steps"][0]["step_id"]
    assert isinstance(p["steps"][0]["step_id"], str)


def test_step_id_preserved_when_provided():
    p = _normalize_pipeline({"mode": "single", "steps": [
        {"agent_id": "a", "step_id": "stable-id"},
    ]})
    assert p["steps"][0]["step_id"] == "stable-id"


def test_custom_non_int_phase_falls_back_to_index():
    p = _normalize_pipeline({"mode": "custom", "steps": [
        {"agent_id": "a", "phase": "abc"},
        {"agent_id": "b"},
    ]})
    # Both steps end up on phases 0,1 because both fell back to step index
    assert _phases(p) == [0, 1]


def test_default_field_values():
    p = _normalize_pipeline({"mode": "sequential", "steps": [{"agent_id": "a"}]})
    s = p["steps"][0]
    assert s["name"] == ""
    assert s["prompt_override"] == ""
    assert s["depends_on_text"] is False
