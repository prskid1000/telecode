"""TeleDesign turn cost options (services/design/engine_opts.py, prompt_builder
section delivery, generate's per-turn cost) — unit + one flow through the real
start_turn → DESIGN_TURN → Engine Runner path with the fake CLI.

Measured context these guard (claude 2.1.282, trivial first turn): ~99k tokens
per API call before, ~37k after; see CLAUDE.md "TeleDesign → Turn cost".
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

import config
from services.design import engine_opts, prompt_builder

FIX = Path(__file__).parent / "fixtures" / "engine"
FAKE = FIX / "fake_cli.py"


@pytest.fixture
def settings(monkeypatch):
    raw = {"design": {"local_helpers": False, "verifier": {"enabled": False}},
           "mcp_server": {"enabled": True, "port": 18766}, "proxy": {"enabled": False},
           "llamacpp": {"enabled": False}, "telemetry": {"enabled": False}}
    monkeypatch.setattr(config, "_raw", raw)
    return raw


# ── engine_opts ─────────────────────────────────────────────────────────────

def test_claude_launch_defaults(settings, tmp_data_root):
    args, env = engine_opts.claude_launch()
    i = args.index("--mcp-config")
    assert args[i - 1] == "--strict-mcp-config"
    cfg = json.loads(Path(args[i + 1]).read_text(encoding="utf-8"))
    assert cfg == {"mcpServers": {"telecode": {"type": "http", "url": "http://127.0.0.1:18766/mcp"}}}
    assert Path(args[i + 1]).is_relative_to(tmp_data_root)
    assert args[args.index("--tools") + 1] == "Read,Write,Edit,Glob,Grep,Bash"
    dis = args[args.index("--disallowedTools") + 1].split(",")
    assert "mcp__telecode__design_send_message" in dis and "mcp__telecode__speak" in dis
    assert "mcp__telecode__design_canvas_call" not in dis and "mcp__telecode__design_eval_js" not in dis
    assert args[args.index("--setting-sources") + 1] == "user"
    assert "--disable-slash-commands" in args
    assert env == {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}


def test_claude_launch_everything_off_is_the_cli_default(settings, tmp_data_root):
    settings["design"]["claude"] = {"strict_mcp": False, "tools": "default", "disallowed_tools": [],
                                    "setting_sources": "all", "claude_md": True, "auto_memory": True,
                                    "skills": True}
    assert engine_opts.claude_launch() == ([], {})


def test_claude_launch_custom_values(settings, tmp_data_root):
    settings["mcp_server"]["enabled"] = False
    settings["design"]["mcp_name"] = "td"
    settings["design"]["claude"] = {"tools": ["Read", "Write"], "disallowed_tools": "design_export, WebFetch",
                                    "setting_sources": ""}
    args, _ = engine_opts.claude_launch()
    cfg = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text(encoding="utf-8"))
    assert cfg == {"mcpServers": {}}                      # telecode's server off → no MCP at all
    assert args[args.index("--tools") + 1] == "Read,Write"
    assert args[args.index("--disallowedTools") + 1] == "mcp__td__design_export,WebFetch"
    assert args[args.index("--setting-sources") + 1] == ""  # no settings files at all


def test_brief_mode_is_claude_only(settings):
    assert engine_opts.brief_mode("claude_code") == "system"
    assert engine_opts.brief_mode("codex") == "message" and engine_opts.brief_mode("antigravity") == "message"
    settings["design"]["brief_mode"] = "message"
    assert engine_opts.brief_mode("claude_code") == "message"
    settings["design"]["brief_mode"] = "bogus"
    assert engine_opts.brief_mode("claude_code") == "system"


def test_claude_adapter_appends_extra_args(tmp_path):
    from services.engine.adapters import get_adapter
    from services.engine.types import EngineRequest
    launch = get_adapter("claude_code").build(EngineRequest(
        engine="claude_code", prompt="p", cwd=tmp_path, extra_args=["--strict-mcp-config", "--tools", "Read"]))
    assert launch.argv[-3:] == ["--strict-mcp-config", "--tools", "Read"]
    assert launch.argv[:2] == ["claude", "-p"]


# ── prompt_builder: sections, turn types, delivery ──────────────────────────

def _project(tmp_path, kind="prototype", **kw):
    d = tmp_path / "proj"
    d.mkdir(parents=True, exist_ok=True)
    return {"id": "p" * 32, "title": "Cost", "kind": kind, **kw}, d


def test_sections_rebuild_the_full_brief(settings, tmp_path):
    project, d = _project(tmp_path)
    b = prompt_builder.build(project, {}, {"text": "make a page", "engine": "claude_code"}, project_dir=d)
    assert b["turn_type"] == "build" and b["needed"] == [s["id"] for s in b["sections"]]
    assert "\n\n---\n\n".join(s["text"] for s in b["sections"]) + "\n" == b["brief"]
    ids = [s["id"] for s in b["sections"]]
    assert ids[:3] == ["header", "charter", "canvas"] and "kind" in ids and "craft_anti_slop" in ids
    assert "<!--" not in b["brief"]                       # licence notes stay in the files


def test_edit_turns_get_a_lean_brief(settings, tmp_path):
    project, d = _project(tmp_path)
    (d / "index.html").write_text("<h1>x</h1>", encoding="utf-8")
    edit = prompt_builder.build(project, {}, {"text": "Change the heading to Hi", "engine": "claude_code"},
                                project_dir=d, project_turns=1)
    assert edit["turn_type"] == "edit"
    assert not any(i == "kind" or i == "tweaks" or i.startswith("craft_") for i in edit["needed"])
    assert {"charter", "canvas", "html_boards", "engine_notes"} <= set(edit["needed"])
    # Same brief, same shas — only the selection differs.
    full = prompt_builder.build(project, {}, {"text": "Make a pricing page", "engine": "claude_code"},
                                project_dir=d, project_turns=1)
    assert full["turn_type"] == "build" and full["brief"] == edit["brief"]
    # Comments and auto-fix turns are edits; a brand-new project never is.
    assert prompt_builder.build(project, {}, {"text": "fix", "auto": "fix"}, project_dir=d)["turn_type"] == "edit"
    assert prompt_builder.build(project, {}, {"text": "see note"}, project_dir=d, project_turns=1,
                                comments=[{"id": "c1", "note": "red"}])["turn_type"] == "edit"
    assert prompt_builder.build(project, {}, {"text": "convert to code"}, project_dir=d)["turn_type"] == "convert"
    empty, d2 = _project(tmp_path / "e")
    assert prompt_builder.build(empty, {}, {"text": "Change the heading"}, project_dir=d2,
                                project_turns=1)["turn_type"] == "build"
    settings["design"]["brief_select"] = False
    assert prompt_builder.build(project, {}, {"text": "Change the heading to Hi"}, project_dir=d,
                                project_turns=1)["turn_type"] == "build"


def test_slides_edit_keeps_the_deck_contract(settings, tmp_path):
    project, d = _project(tmp_path, kind="slides")
    (d / "deck.html").write_text("<div data-td-deck></div>", encoding="utf-8")
    b = prompt_builder.build(project, {}, {"text": "Rename slide 3"}, project_dir=d, project_turns=2)
    assert b["turn_type"] == "edit" and "deck" in b["needed"] and "kind" not in b["needed"]


def test_plan_delivery(settings, tmp_path):
    project, d = _project(tmp_path)
    b = prompt_builder.build(project, {}, {"text": "make a page", "engine": "claude_code"}, project_dir=d)
    shas = {s["id"]: s["sha"] for s in b["sections"]}

    sysm = prompt_builder.plan_delivery(b, sent=None, fresh=True, mode="system")
    assert sysm["system"] == b["brief"] and b["brief"] not in sysm["prompt"]
    assert sysm["prompt"].endswith(b["turn"]) and sysm["sections"] == shas

    msg = prompt_builder.plan_delivery(b, sent=None, fresh=True, mode="message")
    assert msg["system"] is None and msg["prompt"].startswith(b["brief"]) and msg["prompt"].endswith(b["turn"])

    same = prompt_builder.plan_delivery(b, sent=shas, fresh=False, mode="system")
    assert same["system"] is None and "unchanged since your last turn" in same["prompt"]
    assert len(same["prompt"]) < len(b["turn"]) + 400

    # A resumed session never gets a system prompt again — changes go in the message, per section.
    changed = dict(shas, charter="old")
    upd = prompt_builder.plan_delivery(b, sent=changed, fresh=False, mode="system")
    charter = next(s["text"] for s in b["sections"] if s["id"] == "charter")
    assert upd["system"] is None and upd["prompt"].startswith("## Brief update") and charter in upd["prompt"]
    assert next(s["text"] for s in b["sections"] if s["id"] == "canvas") not in upd["prompt"]
    assert upd["sections"] == shas

    # Resumed but unknown (pre-upgrade record): the needed sections, in the message.
    unk = prompt_builder.plan_delivery(b, sent={}, fresh=False, mode="system")
    assert unk["system"] is None and unk["prompt"].startswith(b["brief"])


def test_lean_then_full_sends_only_the_difference(settings, tmp_path):
    project, d = _project(tmp_path)
    (d / "index.html").write_text("<h1>x</h1>", encoding="utf-8")
    edit = prompt_builder.build(project, {}, {"text": "Change the heading to Hi"}, project_dir=d, project_turns=1)
    first = prompt_builder.plan_delivery(edit, sent=None, fresh=True, mode="system")
    assert "craft_anti_slop" not in first["sections"] and "rules not loaded for this turn" in first["prompt"]
    full = prompt_builder.build(project, {}, {"text": "Make a pricing page"}, project_dir=d, project_turns=2)
    nxt = prompt_builder.plan_delivery(full, sent=first["sections"], fresh=False, mode="system")
    assert nxt["prompt"].startswith("## Brief update")
    assert next(s["text"] for s in full["sections"] if s["id"] == "craft_anti_slop") in nxt["prompt"]
    assert next(s["text"] for s in full["sections"] if s["id"] == "charter") not in nxt["prompt"]


def test_turn_cost_subtracts_the_cumulative_base():
    # The correction is shared now (services.engine.cost, applied by the runner).
    from services.engine.cost import per_run_cost
    assert per_run_cost(1.0392322, 0.9161136) == pytest.approx(0.1231186, abs=1e-6)
    assert per_run_cost(0.5, 0.0) == 0.5
    assert per_run_cost(0.5, 0.9) == 0.5         # a new conversation: never negative
    assert per_run_cost(None, 0.3) is None


# ── Flow: two real turns through start_turn with the fake CLI ───────────────

def _replay_with_cost(tmp_path: Path, cost: float) -> Path:
    out = tmp_path / f"replay-{cost}.jsonl"
    lines = []
    for ln in (FIX / "claude_stream.jsonl").read_text(encoding="utf-8").splitlines():
        evt = json.loads(ln)
        if evt.get("type") == "result":
            evt["total_cost_usd"] = cost
        lines.append(json.dumps(evt))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def test_two_turns_through_start_turn(settings, tmp_data_root, tmp_path, monkeypatch):
    from services.design import chats as dchats, generate, store
    from services.engine.adapters import get_adapter

    replays = [_replay_with_cost(tmp_path, 0.36), _replay_with_cost(tmp_path, 0.39)]
    launches = []
    ad = get_adapter("claude_code")
    orig = type(ad).build

    def build(req):
        launch = orig(ad, req)
        n = len(launches)
        launches.append({"argv": list(launch.argv), "env": dict(launch.env or {}), "stdin": launch.stdin,
                         "system": req.system_append_file.read_text(encoding="utf-8")
                         if req.system_append_file else None})
        launch.argv = [sys.executable, str(FAKE), "--replay", str(replays[n])]
        return launch
    monkeypatch.setattr(ad, "build", build)
    monkeypatch.setattr(generate, "engine_available", lambda e: True)

    async def no_post(*a, **k):
        return None
    monkeypatch.setattr(generate, "_post_turn", no_post)
    generate.register_task_type()

    project = store.create_project({"title": "Cost", "kind": "prototype"})
    chat = dchats.create_chat(project["id"], {})
    pid, cid = project["id"], chat["id"]

    async def turn(text):
        out = await generate.start_turn(pid, cid, {"text": text})
        for _ in range(400):
            if (pid, cid) not in generate._chat_running:
                break
            await asyncio.sleep(0.05)
        return [t for t in dchats.read_turns(pid, cid) if t["id"] == out["turn"]["id"]][-1]

    t1 = asyncio.run(turn("Create a tiny page with a heading. Just build it."))
    assert t1["status"] == "done", t1.get("error")
    first = launches[0]
    argv = first["argv"]
    assert "--strict-mcp-config" in argv and "--disable-slash-commands" in argv
    assert argv[argv.index("--append-system-prompt-file") + 1].endswith(f"system-{cid}.md")
    brief = (store.project_dir(pid) / ".td" / "brief.md").read_text(encoding="utf-8")
    assert first["system"] == brief                      # a build turn: the whole brief, as system prompt
    assert brief not in first["stdin"] and "in your system prompt" in first["stdin"]
    assert first["env"]["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
    assert t1["usage"]["cost_usd"] == pytest.approx(0.36)

    t2 = asyncio.run(turn("Change the heading to 'Hello Again'."))
    assert t2["status"] == "done", t2.get("error")
    second = launches[1]
    assert second["system"] == first["system"]           # same file every launch → cache hit
    assert "--resume" in second["argv"]
    assert "unchanged since your last turn" in second["stdin"] and len(second["stdin"]) < 2000
    assert t2["usage"]["cost_usd"] == pytest.approx(0.03)  # 0.39 cumulative − 0.36 before
