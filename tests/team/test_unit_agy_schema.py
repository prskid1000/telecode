"""agy structured output (``--json-schema <file>``, agy >= 1.2.11) and the P2
handoff switch onto it. Fixtures are recorded real cloud runs of agy 1.2.11:

* ``agy_schema_result.jsonl`` — schema ``{"word": string}``
* ``agy_schema_handoff.jsonl`` — the shared HANDOFF_SCHEMA on a one-file step
"""

from __future__ import annotations

import json
from pathlib import Path

from services.engine.adapters import get_adapter
from services.engine.adapters.antigravity import build_argv, structured_from_result
from services.engine.adapters.base import ParseState
from services.engine.types import EngineRequest
from services.run import handoff

FIX = Path(__file__).parent / "fixtures" / "engine"
WORD_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["word"],
               "properties": {"word": {"type": "string"}}}


def _replay(fixture):
    ad = get_adapter("antigravity")
    st = ParseState()
    for line in (FIX / fixture).read_text(encoding="utf-8").splitlines():
        if line.strip():
            ad.parse(json.loads(line), st)
    return ad, st


def _req(tmp_path, **kw):
    return EngineRequest(engine="antigravity", prompt="p", cwd=tmp_path, **kw)


# ── argv ────────────────────────────────────────────────────────────────────

def test_schema_goes_to_a_temp_file_before_the_trailing_p(tmp_path):
    launch = get_adapter("antigravity").build(_req(tmp_path, schema=WORD_SCHEMA))
    argv = launch.argv
    i = argv.index("--json-schema")
    path = Path(argv[i + 1])
    assert argv[-1] == "-p=" and i < len(argv) - 2
    assert json.loads(path.read_text(encoding="utf-8")) == WORD_SCHEMA
    assert path in launch.cleanup and path.name.startswith("agy-schema-")
    assert launch.extras_at == len(argv) - 1          # engine_extras still land before -p=
    path.unlink()


def test_no_schema_no_flag(tmp_path):
    launch = get_adapter("antigravity").build(_req(tmp_path))
    assert "--json-schema" not in launch.argv and launch.cleanup == []
    assert "--json-schema" not in build_argv(work_dir=tmp_path, resume_id=None)


def test_schema_flag_with_resume(tmp_path):
    argv = build_argv(work_dir=tmp_path, resume_id="c1", schema_path=tmp_path / "s.json")
    assert argv[argv.index("--conversation") + 1] == "c1"
    assert argv[argv.index("--json-schema") + 1] == str(tmp_path / "s.json") and argv[-1] == "-p="


# ── recorded result ─────────────────────────────────────────────────────────

def test_recorded_word_result(tmp_path):
    ad, st = _replay("agy_schema_result.jsonl")
    assert st.final["structured_output"] == {"word": "banana"}
    res = ad.finish(_req(tmp_path, schema=WORD_SCHEMA), st, 0, "", 1)
    assert res.structured_output == {"word": "banana"}
    assert res.to_dict("w", with_schema=True)["structured_output"] == {"word": "banana"}
    assert "toolAction" in res.text                     # the reply text is agy's raw response, unchanged
    assert ad.finish(_req(tmp_path), st, 0, "", 1).structured_output is None   # no schema asked → none


def test_recorded_handoff_result_validates(tmp_path):
    ad, st = _replay("agy_schema_handoff.jsonl")
    res = ad.finish(_req(tmp_path, schema=handoff.HANDOFF_SCHEMA), st, 0, "", 1)
    ho, problems = handoff.validate(res.structured_output, tmp_path)
    assert problems == [] and ho["status"] == "done" and ho["verdict"] == "pass" and not ho["derived"]
    assert ho["artifacts"] == [{"path": "notes.txt", "kind": "data",
                                "description": "Text file containing exactly the text 'hello'"}]
    assert set(res.structured_output) == set(handoff.HANDOFF_SCHEMA["required"])
    assert res.tool_calls and res.engine_session_id == "918d6f71-f183-41f9-a10d-ce859c51ce5c"


def test_structured_from_result_fallbacks():
    resp = '{"toolAction":"Submitting response","toolSummary":"Submit response","word":"kiwi"}\n'
    assert structured_from_result({"response": resp}) == {"word": "kiwi"}             # submit keys dropped
    assert structured_from_result({"structured_output": '{"word":"fig"}', "response": resp}) == {"word": "fig"}
    assert structured_from_result({"response": '```json\n{"word":"lime"}\n```'}) == {"word": "lime"}
    assert structured_from_result({"response": "plain words"}) is None
    assert structured_from_result({"response": "{broken"}) is None
    assert structured_from_result({}) is None and structured_from_result(None) is None


def test_missing_structured_result_is_none_not_an_error(tmp_path):
    st = ParseState(final={"status": "SUCCESS", "response": "no json here"}, saw_completion=True)
    res = get_adapter("antigravity").finish(_req(tmp_path, schema=WORD_SCHEMA), st, 0, "", 1)
    assert res.structured_output is None and res.text == "no json here"


# ── handoff switch ──────────────────────────────────────────────────────────

GOOD = {"status": "done", "summary": "from schema", "decisions": [], "artifacts": [], "open_questions": [],
        "next_steps": [], "items": [], "verdict": "pass"}


def _write_file(wd: Path, obj) -> Path:
    p = wd / handoff.AGY_HANDOFF_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj) if not isinstance(obj, str) else obj, encoding="utf-8")
    return p


def test_agy_instructions_no_longer_ask_for_the_file():
    text = handoff.instructions("antigravity")
    assert "handoff.json" not in text and "structured form" in text


def test_schema_answer_wins_and_the_file_is_removed(tmp_path):
    p = _write_file(tmp_path, {**GOOD, "summary": "from file"})
    assert handoff.agy_structured(GOOD, tmp_path)["summary"] == "from schema"
    assert not p.exists()


def test_file_is_the_fallback_when_the_schema_answer_is_missing_or_invalid(tmp_path):
    _write_file(tmp_path, {**GOOD, "summary": "from file"})
    assert handoff.agy_structured(None, tmp_path)["summary"] == "from file"
    _write_file(tmp_path, {**GOOD, "summary": "from file"})
    assert handoff.agy_structured({"status": "bogus"}, tmp_path)["summary"] == "from file"


def test_neither_valid_keeps_the_schema_answer_so_resolve_reports_why(tmp_path):
    _write_file(tmp_path, "not json")
    s = handoff.agy_structured({"status": "bogus", "summary": "x"}, tmp_path)
    assert s == {"status": "bogus", "summary": "x"}
    ho = handoff.resolve(s, "reply", step_status="completed", error=None, work_dir=tmp_path)
    assert ho["derived"] and "invalid structured handoff" in ho["derive_reason"]
    assert handoff.agy_structured(None, tmp_path) is None                         # nothing at all
    ho = handoff.resolve(None, "reply", step_status="completed", error=None, work_dir=tmp_path)
    assert ho["derived"] and ho["summary"] == "reply"
