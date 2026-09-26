"""P2 end to end for agy with ``--json-schema``: real handlers, executor and
Engine Runner; only the CLI is the fake (``fake_agent_cli.py --agy``), which
puts ``--handoff`` in ``result.structured_output`` as agy 1.2.11 does."""

from __future__ import annotations

from pathlib import Path

from tests.team.test_flow_p2_runs import HO, argvs, env, fake, prompts, setup_job, start, wait_run  # noqa: F401


def test_agy_step_handoff_comes_from_the_schema_answer(env):
    fake(env, "antigravity", lambda req: ["--agy", "--handoff", HO(summary="via schema"), "--text", "hi"])
    j, wd = setup_job([{"prompt_override": "A", "engine": "antigravity"}], mode="single")
    r = wait_run(start(j)["run_id"])
    ho = r["steps"][0]["handoff"]
    assert r["status"] == "completed" and ho["summary"] == "via schema" and not ho["derived"]
    argv = argvs(env)[0]
    schema_file = Path(argv[argv.index("--json-schema") + 1])
    assert argv[-1] == "-p="
    assert not schema_file.exists()                                   # the runner cleaned it up
    assert "handoff.json" not in prompts(env)[0]


def test_agy_without_any_handoff_derives(env):
    fake(env, "antigravity", lambda req: ["--agy", "--text", "just words"])
    j, _ = setup_job([{"prompt_override": "A", "engine": "antigravity"}], mode="single")
    r = wait_run(start(j)["run_id"])
    ho = r["steps"][0]["handoff"]
    assert r["status"] == "completed" and ho["derived"] and ho["summary"] == "just words"
