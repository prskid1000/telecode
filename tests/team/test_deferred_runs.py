"""Deferred P3 items: gate timeouts, Telegram edit-then-approve, truly parallel
forked map workers.

Real task handlers, run executor and Engine Runner; the CLI is the fake
(fixtures/engine/fake_agent_cli.py); Telegram is fake Update / CallbackQuery /
Message / Bot objects — nothing is sent anywhere. ``test_real_claude_*`` runs a
real ``claude --model haiku`` fork map only with ``TELECODE_REAL_CLAUDE=1``."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram import ForceReply
from telegram.ext import ApplicationHandlerStop

from bot import approval_handlers as ah
from services import approvals, snapshots
from services.agent.agent_manager import get_agent_manager
from services.db.core import connect
from services.engine.adapters import get_adapter
from services.job.job_manager import get_job_manager
from services.run import executor, fork_workspace
from services.run.run_store import get_run_store
from services.session import session_store
from services.task.task_manager import get_task_queue

FAKE = Path(__file__).parent / "fixtures" / "engine" / "fake_agent_cli.py"
TERMINAL = ("completed", "failed", "partial", "cancelled", "interrupted", "budget_exceeded", "rejected")
HO = lambda **kw: json.dumps({"status": "done", "summary": "ok", "decisions": [], "artifacts": [],  # noqa: E731
                              "open_questions": [], "next_steps": [], "items": [], "verdict": "pass", **kw})


# ── fixtures / helpers ──────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_data_root, monkeypatch):
    import config
    from services.task.task_registry import register_default_tasks
    q = get_task_queue()
    saved = dict(q.task_handlers)
    register_default_tasks()
    monkeypatch.setattr(config, "tasks_retry_backoff_seconds", lambda: 0.05)
    monkeypatch.setattr(config, "tasks_kill_grace_seconds", lambda: 0.5)
    claude_home = tmp_data_root / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    calls = {"argv": tmp_data_root / "argv.jsonl", "prompts": tmp_data_root / "prompts.jsonl"}
    yield {"root": tmp_data_root, "calls": calls, "monkeypatch": monkeypatch, "claude_home": claude_home}
    approvals.stop_timeout_checker()
    q.task_handlers.clear()
    q.task_handlers.update(saved)


def fake(env, engine, script):
    ad = get_adapter(engine)
    orig = type(ad).build

    def build(req):
        launch = orig(ad, req)
        launch.argv = [sys.executable, str(FAKE), "--argv-out", str(env["calls"]["argv"]),
                       "--prompt-out", str(env["calls"]["prompts"]),
                       *[str(a) for a in script(req)], "--", *launch.argv[1:]]
        return launch
    env["monkeypatch"].setattr(ad, "build", build)


def prompts(env):
    p = env["calls"]["prompts"]
    return [json.loads(l).replace("\r\n", "\n") for l in p.read_text(encoding="utf-8").splitlines()] \
        if p.exists() else []


def argvs(env):
    p = env["calls"]["argv"]
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def wait_run(run_id, pred=None, deadline=40):
    pred = pred or (lambda r: r["status"] in TERMINAL and not executor.is_active(r["run_id"]))
    end = time.time() + deadline
    rs = get_run_store()
    while time.time() < end:
        r = rs.get_run(run_id)
        if r and pred(r):
            return r
        time.sleep(0.05)
    return rs.get_run(run_id)


def waiting(r):
    return r["status"] == "awaiting_input" and not executor.is_active(r["run_id"])


def setup_job(steps, ws="ws-deferred", mode="custom", **job):
    a = get_agent_manager().create_agent("deferred-bot", soul="s")
    if not session_store.exists(ws):
        session_store.create(session_id=ws, data={"name": ws})
    for i, s in enumerate(steps):
        s.setdefault("agent_id", a["id"] if s.get("kind") != "gate" else None)
        s.setdefault("phase", i)
    j = get_job_manager().create_job({"title": "deferred", "workspace_id": ws, "task_description": "go",
                                      "pipeline": {"mode": mode, "steps": steps}, **job})
    return j, session_store._session_dir(ws)


def start(j, **kw):
    return asyncio.run(executor.start_run(job=j, source="user", **kw))


def set_deadline(approval_id, when):
    """Move an approval's persisted deadline (tests can't wait the 10 s minimum)."""
    row = connect().execute("SELECT payload FROM approvals WHERE id=?", (approval_id,)).fetchone()
    p = json.loads(row["payload"])
    p["deadline_at"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    connect().execute("UPDATE approvals SET payload=? WHERE id=?", (json.dumps(p), approval_id))


def past():
    return datetime.now(timezone.utc) - timedelta(seconds=5)


def run(coro):
    return asyncio.run(coro)


# ── 1. gate timeouts ────────────────────────────────────────────────────────

def test_gate_timeout_fields_are_normalised(tmp_data_root):
    m = get_job_manager()
    j = m.create_job({"title": "x", "pipeline": {"mode": "sequential", "steps": [
        {"agent_id": "a"}, {"kind": "gate", "gate": {"title": "ok?", "timeout_sec": "60", "on_timeout": "SKIP"}},
        {"kind": "gate", "gate": {"title": "no timeout", "timeout_sec": 0, "on_timeout": "approve"}}]}})
    g1, g2 = j["pipeline"]["steps"][1]["gate"], j["pipeline"]["steps"][2]["gate"]
    assert g1["timeout_sec"] == 60 and g1["on_timeout"] == "skip"
    assert "timeout_sec" not in g2 and "on_timeout" not in g2
    with pytest.raises(ValueError, match="on_timeout"):
        m.create_job({"title": "x", "pipeline": {"mode": "single", "steps": [
            {"kind": "gate", "gate": {"timeout_sec": 60, "on_timeout": "explode"}}]}})
    with pytest.raises(ValueError, match="timeout_sec"):
        m.create_job({"title": "x", "pipeline": {"mode": "single", "steps": [
            {"kind": "gate", "gate": {"timeout_sec": 3}}]}})


def test_gate_timeout_reject_ends_the_run_and_records_the_deadline(env):
    fake(env, "claude_code", lambda req: ["--handoff", HO(summary="draft")])
    j, _ = setup_job([{"prompt_override": "A"}, {"kind": "gate", "gate": {"title": "Ship?", "timeout_sec": 60}},
                      {"prompt_override": "B"}])
    r = wait_run(start(j)["run_id"], waiting)
    gate = r["steps"][1]
    ap = approvals.get(gate["approval_id"])
    dl = datetime.fromisoformat(ap["deadline_at"].replace("Z", "+00:00"))
    assert 50 < (dl - datetime.now(timezone.utc)).total_seconds() <= 61
    assert ap["on_timeout"] == "reject" and gate["deadline_at"] == ap["deadline_at"] and gate["on_timeout"] == "reject"
    assert approvals.expire_due() == []                                   # not due yet
    with pytest.raises(approvals.ApprovalError, match="reserved"):
        approvals.decide(ap["id"], "approve", by="timeout")
    done = approvals.expire_due(now=dl + timedelta(seconds=1))
    assert [d["id"] for d in done] == [ap["id"]]
    ap = approvals.get(ap["id"])
    assert ap["status"] == "rejected" and ap["decided_by"] == "timeout" and "on_timeout: reject" in ap["decision_note"]
    r = wait_run(r["run_id"])
    assert r["status"] == "rejected" and [s["status"] for s in r["steps"]] == ["completed", "rejected", "skipped"]
    assert "rejected by timeout" in r["steps"][1]["error"]


def test_gate_timeout_approve_continues_the_run(env):
    fake(env, "claude_code", lambda req: ["--handoff", HO(summary="step done")])
    j, _ = setup_job([{"prompt_override": "A"},
                      {"kind": "gate", "gate": {"timeout_sec": 30, "on_timeout": "approve"}},
                      {"prompt_override": "AFTER", "depends_on_text": True}])
    r = wait_run(start(j)["run_id"], waiting)
    set_deadline(r["steps"][1]["approval_id"], past())
    approvals.expire_due()
    r = wait_run(r["run_id"])
    assert r["status"] == "completed", r
    g = r["steps"][1]
    assert g["gate_decision"]["by"] == "timeout" and g["handoff"]["summary"].startswith("Approved by timeout")
    assert any("AFTER" in p for p in prompts(env))


def test_gate_timeout_skip_passes_the_gate_over(env):
    fake(env, "claude_code", lambda req: ["--handoff", HO(summary="the draft")])
    j, _ = setup_job([{"prompt_override": "A"},
                      {"kind": "gate", "gate": {"timeout_sec": 30, "on_timeout": "skip"}},
                      {"prompt_override": "AFTER", "depends_on_text": True}])
    r = wait_run(start(j)["run_id"], waiting)
    aid = r["steps"][1]["approval_id"]
    set_deadline(aid, past())
    approvals.expire_due()
    r = wait_run(r["run_id"])
    assert r["status"] == "completed", r                                  # a timeout-skipped gate counts as done
    g = r["steps"][1]
    assert g["status"] == "skipped" and g["gate_decision"]["status"] == "skipped" and not g.get("handoff")
    assert approvals.get(aid)["status"] == "skipped"
    after = [p for p in prompts(env) if "AFTER" in p][0]
    assert "the draft" in after                                           # what the gate was shown is passed on


def test_gate_timeout_checker_survives_a_restart(env, monkeypatch):
    fake(env, "claude_code", lambda req: ["--handoff", HO()])
    j, _ = setup_job([{"kind": "gate", "gate": {"timeout_sec": 30}}, {"prompt_override": "AFTER"}])
    r = wait_run(start(j)["run_id"], waiting)
    approvals.stop_timeout_checker()                                      # "telecode stops"
    set_deadline(r["steps"][0]["approval_id"], past())                    # … the deadline passes while down
    monkeypatch.setattr(approvals, "TIMEOUT_CHECK_SECONDS", 0.05)
    assert approvals.has_deadlines()
    executor.reconcile_orphaned_runs()                                    # startup: the checker comes back
    r = wait_run(r["run_id"])
    assert r["status"] == "rejected" and approvals.get(r["steps"][0]["approval_id"])["decided_by"] == "timeout"
    assert not approvals.has_deadlines()


def test_gate_decided_before_the_deadline_is_left_alone(env):
    fake(env, "claude_code", lambda req: ["--handoff", HO()])
    j, _ = setup_job([{"kind": "gate", "gate": {"timeout_sec": 30, "on_timeout": "reject"}},
                      {"prompt_override": "AFTER"}])
    r = wait_run(start(j)["run_id"], waiting)
    aid = r["steps"][0]["approval_id"]
    approvals.decide(aid, "approve", by="alice")
    set_deadline(aid, past())
    assert approvals.expire_due() == [] and approvals.expire(aid) is None
    assert wait_run(r["run_id"])["status"] == "completed"


def test_gate_timeout_is_shown_and_edited_on_telegram(env, tg):
    ap = approvals.create("gate", title="Deploy <prod>", body="b", deadline_at="2030-01-01T00:00:00Z",
                          on_timeout="skip")
    txt = ah.format_approval(ap)
    assert "Decide by</i> <b>2030-01-01T00:00:00Z</b>" in txt and "skipped and the run goes on" in txt
    assert "Deploy &lt;prod&gt;" in txt
    bot = FakeBot()
    run(ah.post_approval(bot, ap))
    set_deadline(ap["id"], past())
    approvals.expire_due()
    notifier = ah.ApprovalNotifier(bot)
    run(notifier.handle("approval.decided", {"id": ap["id"]}))
    edit = bot.edited[-1]
    assert "Skipped</b> by timeout" in edit["text"] and edit["reply_markup"] is None


# ── 2. Telegram edit & approve ──────────────────────────────────────────────

class FakeMessage:
    def __init__(self, chat_id=-100123, message_id=1, text=None, reply_to=None, thread=None):
        self.chat_id, self.message_id, self.text = chat_id, message_id, text
        self.reply_to_message = reply_to
        self.message_thread_id, self.is_topic_message = thread, bool(thread)
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append((text, kw))


class FakeQuery:
    def __init__(self, data, message=None):
        self.data, self.message = data, message or FakeMessage(message_id=77)
        self.answers, self.edits = [], []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text, **kw):
        self.edits.append((text, kw))


class FakeBot:
    def __init__(self):
        self.sent, self.edited = [], []

    async def send_message(self, **kw):
        self.sent.append(kw)
        return SimpleNamespace(chat_id=kw["chat_id"], message_id=500 + len(self.sent))

    async def edit_message_text(self, **kw):
        self.edited.append(kw)


def _user(uid=42, username="alice", first="Al<i>ce"):
    return SimpleNamespace(id=uid, username=username, first_name=first)


@pytest.fixture
def tg(monkeypatch):
    import config
    state = {"allowed": {42, 43}, "group": -100123}
    monkeypatch.setattr(config, "allowed_user_ids", lambda: state["allowed"])
    real = config.get_nested
    monkeypatch.setattr(config, "get_nested", lambda path, default=None:
                        state["group"] if path == "telegram.group_id" else real(path, default))
    monkeypatch.setattr(ah, "_bot_alive", lambda: True)
    approvals.register_handler("tool", lambda ap: None)
    ah._edit_prompts.clear()
    yield state
    ah._edit_prompts.clear()


def press_edit(ap, bot, user=None):
    """Press ✏️ on the approval; returns (query, prompt message id or None)."""
    q = FakeQuery(f"apv:e:{ap['id']}")
    up = SimpleNamespace(callback_query=q, effective_user=user or _user())
    n = len(bot.sent)
    run(ah.handle_approval_callback(up, SimpleNamespace(bot=bot)))
    return q, (500 + len(bot.sent) if len(bot.sent) > n else None)


def posted(ap, bot):
    run(ah.post_approval(bot, ap))
    return approvals.get(ap["id"])


def reply(bot, prompt_id, text, user=None, chat_id=-100123):
    m = FakeMessage(chat_id=chat_id, message_id=900, text=text,
                    reply_to=SimpleNamespace(message_id=prompt_id))
    up = SimpleNamespace(effective_message=m, effective_user=user or _user())
    stopped = False
    try:
        run(ah.handle_approval_reply(up, SimpleNamespace(bot=bot)))
    except ApplicationHandlerStop:
        stopped = True
    return m, stopped


def test_edit_button_only_for_editable_kinds(tg):
    gate = approvals.create("gate", title="g")
    mem = approvals.create("memory", title="m")
    assert [b.callback_data for b in ah.keyboard(gate).inline_keyboard[0]] == \
        [f"apv:a:{gate['id']}", f"apv:e:{gate['id']}", f"apv:r:{gate['id']}"]
    assert [b.callback_data[:6] for b in ah.keyboard(mem).inline_keyboard[0]] == ["apv:a:", "apv:r:"]
    bot = FakeBot()
    q, pid = press_edit(mem, bot)
    assert q.answers[-1][1] is True and "cannot be edited" in q.answers[-1][0] and pid is None


def test_edit_and_approve_flow_approves_with_the_reply(tg):
    bot = FakeBot()
    ap = posted(approvals.create("gate", title="Ship <eu> & us", body="x"), bot)
    q, prompt_id = press_edit(ap, bot)
    assert q.answers[-1] == ("Reply to my message with your edited text.", False)
    sent = bot.sent[-1]
    assert isinstance(sent["reply_markup"], ForceReply) and sent["reply_markup"].selective
    assert '<a href="tg://user?id=42">Al&lt;i&gt;ce</a>' in sent["text"]          # mention, escaped
    assert "Ship &lt;eu&gt; &amp; us" in sent["text"] and sent["parse_mode"] == "HTML"
    # a reply to some other message is not ours: untouched, no stop
    m, stopped = reply(bot, 12345, "hello")
    assert not stopped and m.replies == []
    # a different (even allowlisted) user cannot answer
    m, stopped = reply(bot, prompt_id, "sneaky", user=_user(43, "bob", "Bob"))
    assert stopped and "Only Al&lt;i&gt;ce" in m.replies[-1][0] and approvals.get(ap["id"])["status"] == "pending"
    # too long: refused, the prompt stays usable
    m, stopped = reply(bot, prompt_id, "x" * (ah.EDIT_TEXT_CAP + 1))
    assert stopped and "Too long" in m.replies[-1][0] and approvals.get(ap["id"])["status"] == "pending"
    # the pressing user's reply approves with it
    m, stopped = reply(bot, prompt_id, "  Ship only <EU> & log it  ")
    assert stopped
    got = approvals.get(ap["id"])
    assert got["status"] == "approved" and got["edited_text"] == "Ship only <EU> & log it"
    assert got["decided_by"] == "telegram:alice"
    assert "✅ Approved <b>Ship &lt;eu&gt; &amp; us</b>" in m.replies[-1][0]
    assert bot.edited and "(approved with edited text)" in bot.edited[-1]["text"] and bot.edited[-1]["reply_markup"] is None
    # the prompt is used up: a second reply is no longer ours
    m, stopped = reply(bot, prompt_id, "again")
    assert not stopped and m.replies == []


def test_edit_prompt_expires_after_15_minutes(tg, monkeypatch):
    ap = approvals.create("gate", title="g")
    bot = FakeBot()
    _, pid = press_edit(ap, bot)
    real = time.time
    monkeypatch.setattr(ah, "time", SimpleNamespace(time=lambda: real() + ah.EDIT_WINDOW_SECONDS + 5))
    m, stopped = reply(bot, pid, "late text")
    assert stopped and "expired" in m.replies[-1][0] and approvals.get(ap["id"])["status"] == "pending"


def test_edit_press_needs_the_allowlist_and_a_pending_approval(tg):
    ap = approvals.create("gate", title="g")
    bot = FakeBot()
    q, pid = press_edit(ap, bot, user=_user(7, "mallory", "M"))
    assert "Not allowed" in q.answers[-1][0] and pid is None
    tg["allowed"] = set()                                             # empty allowlist: nobody
    q, pid = press_edit(ap, bot)
    assert "Not allowed" in q.answers[-1][0] and pid is None
    tg["allowed"] = {42}
    approvals.decide(ap["id"], "reject", by="web")
    q, pid = press_edit(ap, bot)
    assert "Already rejected by web" in q.answers[-1][0] and pid is None


def test_edit_reply_after_a_web_decision_says_so(tg):
    ap = approvals.create("gate", title="g")
    bot = FakeBot()
    _, pid = press_edit(ap, bot)
    approvals.decide(ap["id"], "approve", by="web")
    m, stopped = reply(bot, pid, "my text")
    assert stopped and "Already approved by web" in m.replies[-1][0]
    assert approvals.get(ap["id"])["edited_text"] is None


def test_tool_edit_must_be_a_json_object(tg):
    ap = approvals.create("tool", title="Bash", body="rm")
    bot = FakeBot()
    _, pid = press_edit(ap, bot)
    m, stopped = reply(bot, pid, "not json")
    assert stopped and "JSON object" in m.replies[-1][0] and approvals.get(ap["id"])["status"] == "pending"
    m, stopped = reply(bot, pid, '{"command": "ls"}')
    assert stopped and approvals.get(ap["id"])["edited_text"] == '{"command": "ls"}'


def test_telegram_edit_feeds_the_next_pipeline_step(env, tg):
    fake(env, "claude_code", lambda req: ["--handoff", HO(summary="draft ready")] if "DRAFT" in req.prompt
         else ["--handoff", HO(summary="shipped")])
    j, _ = setup_job([{"prompt_override": "DRAFT"}, {"kind": "gate", "gate": {"title": "Ship?"}},
                      {"prompt_override": "SHIP", "depends_on_text": True}])
    r = wait_run(start(j)["run_id"], waiting)
    ap = approvals.get(r["steps"][1]["approval_id"])
    bot = FakeBot()
    _, pid = press_edit(ap, bot)
    assert reply(bot, pid, "Ship only the EU build.")[1]
    r = wait_run(r["run_id"])
    assert r["status"] == "completed", r
    assert r["steps"][1]["handoff"]["summary"] == "Ship only the EU build."
    assert "Ship only the EU build." in [p for p in prompts(env) if "SHIP" in p][0]


# ── 3. parallel forked map workers ──────────────────────────────────────────

def claude_session_file(env, cwd, sid):
    return env["claude_home"] / "projects" / fork_workspace.claude_project_dirname(cwd) / f"{sid}.jsonl"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path shape")
def test_claude_project_dirname_matches_claude_code():
    # the folder Claude Code itself created for this repo / an ephemeral run session
    assert fork_workspace.claude_project_dirname(Path(r"C:\Users\prith\.telecode")) == "C--Users-prith--telecode"
    assert fork_workspace.claude_project_dirname(
        Path(r"C:\Users\prith\.telecode\data\task_sessions\_ns\run-parallel\run-07e7f5ac-84cf7ce2")) == \
        "C--Users-prith--telecode-data-task-sessions--ns-run-parallel-run-07e7f5ac-84cf7ce2"


def test_prepare_fork_per_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "ch"))
    src, dst = tmp_path / "planner", tmp_path / "worker"
    src.mkdir(), dst.mkdir()
    assert fork_workspace.prepare_fork("codex", "T1", src_cwd=src, dst_cwd=dst)["ok"]        # global rollouts
    agy = fork_workspace.prepare_fork("antigravity", "C1", src_cwd=src, dst_cwd=dst)
    assert not agy["ok"] and "no conversation fork" in agy["reason"]
    miss = fork_workspace.prepare_fork("claude_code", "S1", src_cwd=src, dst_cwd=dst)
    assert not miss["ok"] and "not found" in miss["reason"]
    pdir = tmp_path / "ch" / "projects" / fork_workspace.claude_project_dirname(src)
    pdir.mkdir(parents=True)
    (pdir / "S1.jsonl").write_text('{"type":"user"}\n')
    (pdir / "S1").mkdir()
    (pdir / "S1" / "sub.jsonl").write_text("{}")
    ok = fork_workspace.prepare_fork("claude_code", "S1", src_cwd=src, dst_cwd=dst)
    wdir = tmp_path / "ch" / "projects" / fork_workspace.claude_project_dirname(dst)
    assert ok["ok"] and (wdir / "S1.jsonl").read_text() == '{"type":"user"}\n' and (wdir / "S1" / "sub.jsonl").exists()
    fork_workspace.cleanup_fork(ok)
    assert not (wdir / "S1.jsonl").exists() and not (wdir / "S1").exists() and (pdir / "S1.jsonl").exists()
    assert not fork_workspace.prepare_fork("claude_code", "../evil", src_cwd=src, dst_cwd=dst)["ok"]
    assert fork_workspace.missing_session("claude exited with code 1: No conversation found with session ID: x")
    assert not fork_workspace.missing_session("API Error: 529 overloaded_error")


def test_populate_workspace_copy_or_snapshot(tmp_data_root):
    src = tmp_data_root / "ws"
    (src / "node_modules" / "x").mkdir(parents=True)
    (src / "node_modules" / "x" / "i.js").write_text("big")
    (src / ".git").mkdir()
    (src / ".git" / "HEAD").write_text("ref")
    (src / "a.txt").write_text("one")
    (src / ".env").write_text("SECRET=1")
    (src / ".gitignore").write_text(".env\n")
    (src / "session.json").write_text("{}")
    key = snapshots.key_for("ws-pop")
    after = snapshots.take(key, src, "after planner")
    if not after:
        pytest.skip("git / snapshots unavailable")
    d1 = tmp_data_root / "w1"
    info = fork_workspace.populate_workspace(d1, src_dir=src, snapshot_key=key, snapshot_after=after)
    assert info["from"] == "copy" and (d1 / "a.txt").read_text() == "one" and (d1 / ".env").exists()
    assert not (d1 / "node_modules").exists() and not (d1 / ".git").exists() and not (d1 / "session.json").exists()
    (src / "a.txt").write_text("moved on")                                  # the workspace moved since
    snapshots.take(key, src, "later")
    d2 = tmp_data_root / "w2"
    info = fork_workspace.populate_workspace(d2, src_dir=src, snapshot_key=key, snapshot_after=after)
    assert info["from"] == "snapshot" and (d2 / "a.txt").read_text() == "one" and not (d2 / ".env").exists()
    d3 = tmp_data_root / "w3"                                             # the planner's copy is gone
    assert fork_workspace.populate_workspace(d3, src_dir=tmp_data_root / "gone", snapshot_key=key,
                                             snapshot_after=after)["from"] == "snapshot"


def _fork_map_job(env, items, max_parallel):
    return setup_job([{"name": "Planner", "prompt_override": "PLAN"},
                      {"name": "Worker", "kind": "map", "prompt_override": "DO",
                       "map": {"items_from": "items", "max_parallel": max_parallel, "worker_session": "fork"},
                       "budget": {"max_usd": 1.0}},
                      {"name": "Reducer", "kind": "reduce", "prompt_override": "MERGE"}])


def test_forked_map_workers_run_in_parallel_in_their_own_folders(env):
    barrier = threading.Barrier(2, timeout=15)
    seen = {}
    lock = threading.Lock()
    planner_dir = {}

    def script(req):
        p = req.prompt
        if "<map_item" in p:
            item = "alpha" if "alpha\n</map_item>" in p else "beta"
            cwd = Path(req.cwd)
            with lock:
                seen[item] = {"cwd": cwd, "resume": req.resume_id, "fork": req.fork,
                              "staged": claude_session_file(env, cwd, "S-PLAN").is_file(),
                              "plan_copied": (cwd / "plan.txt").read_text() if (cwd / "plan.txt").exists() else None}
            barrier.wait()                                     # both workers are live at once, or this raises
            return ["--session", f"S-{item}", "--write", f"{item}.txt={item} done",
                    "--handoff", HO(summary=f"did {item}", artifacts=[{"path": f"{item}.txt", "kind": "doc"}])]
        if "<reduce_instructions>" in p:
            return ["--handoff", HO(summary="merged")]
        planner_dir["cwd"] = Path(req.cwd)
        f = claude_session_file(env, req.cwd, "S-PLAN")          # what the real CLI would have written
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('{"type":"user","sessionId":"S-PLAN"}\n')
        return ["--session", "S-PLAN", "--write", "plan.txt=the plan",
                "--handoff", HO(summary="planned", items=["alpha", "beta"])]
    fake(env, "claude_code", script)
    j, wd = _fork_map_job(env, ["alpha", "beta"], 2)
    r = wait_run(start(j)["run_id"])
    assert r["status"] == "completed", r
    mp = r["steps"][1]
    assert all(w["status"] == "completed" and w["fork"] and not w.get("fork_fallback") for w in mp["workers"])
    assert {w["workspace_from"]["from"] for w in mp["workers"]} == {"copy"}
    a, b = seen["alpha"], seen["beta"]
    assert a["cwd"] != b["cwd"] and wd not in (a["cwd"], b["cwd"])           # own folders, not the planner's
    assert a["resume"] == b["resume"] == "S-PLAN" and a["fork"] and b["fork"]
    assert a["staged"] and b["staged"] and a["plan_copied"] == b["plan_copied"] == "the plan"
    worker_argv = [x for x, p in zip(argvs(env), prompts(env)) if "<map_item" in p]
    assert all(x[x.index("--resume") + 1] == "S-PLAN" and "--fork-session" in x for x in worker_argv)
    # nothing merges back into the planner workspace; files come back as artifacts
    assert (wd / "plan.txt").exists() and not (wd / "alpha.txt").exists() and not (wd / "beta.txt").exists()
    arts = {x["path"] for x in mp["handoff"]["artifacts"]}
    assert arts == {"w1/alpha.txt", "w2/beta.txt"}
    # the staged copies of the planner's conversation are cleaned up; the planner's own stays
    assert not claude_session_file(env, a["cwd"], "S-PLAN").exists()
    assert claude_session_file(env, planner_dir["cwd"], "S-PLAN").exists()
    red = next(p for p in prompts(env) if "<reduce_instructions>" in p)
    assert "did alpha" in red and "did beta" in red


def test_forked_map_respects_max_parallel(env):
    state = {"active": 0, "max": 0}
    lock = threading.Lock()

    def script(req):
        p = req.prompt
        if "<map_item" in p:
            with lock:
                state["active"] += 1
                state["max"] = max(state["max"], state["active"])
            time.sleep(0.6)
            with lock:
                state["active"] -= 1
            return ["--handoff", HO(summary="w")]
        if "<reduce_instructions>" in p:
            return ["--handoff", HO()]
        f = claude_session_file(env, req.cwd, "S-PLAN")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}\n")
        return ["--session", "S-PLAN", "--handoff", HO(items=["one", "two", "three"])]
    fake(env, "claude_code", script)
    j, _ = _fork_map_job(env, None, 2)
    r = wait_run(start(j)["run_id"])
    assert r["status"] == "completed", r
    assert len(r["steps"][1]["workers"]) == 3 and state["max"] == 2
    assert r["steps"][1]["budget"]["max_usd"] == pytest.approx(1.0 / 3, rel=1e-3)


def test_fork_falls_back_to_fresh_handoff_when_the_session_is_missing(env):
    def script(req):
        p = req.prompt
        if "<map_item" in p:
            return ["--handoff", HO(summary="w")]
        if "<reduce_instructions>" in p:
            return ["--handoff", HO()]
        return ["--session", "S-NOFILE", "--handoff", HO(summary="PLANNER-NOTE", items=["solo"])]  # no jsonl written
    fake(env, "claude_code", script)
    j, _ = _fork_map_job(env, None, 2)
    r = wait_run(start(j)["run_id"])
    assert r["status"] == "completed", r
    w = r["steps"][1]["workers"][0]
    assert w["fork"] is False and "not found" in w["fork_fallback"]
    wp = [(x, p) for x, p in zip(argvs(env), prompts(env)) if "<map_item" in p]
    assert "--resume" not in wp[0][0] and "PLANNER-NOTE" in wp[0][1]       # fresh, seeded by the handoff


def test_fork_reruns_fresh_when_the_cli_cannot_find_the_conversation(env, monkeypatch):
    counter = env["root"] / "fork.cnt"

    def script(req):
        p = req.prompt
        if "<map_item" in p:
            return ["--fail-times", "1", "--counter", str(counter), "--handoff", HO(summary="w ok")]
        if "<reduce_instructions>" in p:
            return ["--handoff", HO()]
        f = claude_session_file(env, req.cwd, "S-PLAN")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}\n")
        return ["--session", "S-PLAN", "--handoff", HO(items=["solo"])]
    fake(env, "claude_code", script)
    # the fake's failure text is "overloaded"; stand in for Claude's "No conversation found"
    monkeypatch.setattr(fork_workspace, "missing_session", lambda error, events=None: True)
    j, _ = _fork_map_job(env, None, 2)
    r = wait_run(start(j)["run_id"])
    assert r["status"] == "completed", r
    w = r["steps"][1]["workers"][0]
    assert w["fork"] is False and "could not find" in w["fork_fallback"]
    wargv = [x for x, p in zip(argvs(env), prompts(env)) if "<map_item" in p]
    assert len(wargv) == 2 and "--fork-session" in wargv[0] and "--resume" not in wargv[1]


def test_fork_across_engines_falls_back(env):
    def script(req):
        p = req.prompt
        if "<map_item" in p:
            return ["--handoff", HO(summary="w")]
        return ["--session", "S-PLAN", "--handoff", HO(items=["solo"])]
    fake(env, "claude_code", script)
    ad = get_adapter("codex")
    orig = type(ad).build

    def build(req):                                           # the codex worker, on the Claude-shaped fake
        launch = orig(ad, req)
        launch.argv = [sys.executable, str(FAKE), "--handoff", HO(summary="codex w"), "--", *launch.argv[1:]]
        return launch
    env["monkeypatch"].setattr(ad, "build", build)     # (its outcome does not matter: the fallback is decided first)
    j, _ = setup_job([{"prompt_override": "PLAN"},
                      {"kind": "map", "engine": "codex", "prompt_override": "DO",
                       "map": {"items_from": "items", "worker_session": "fork"}}])
    r = wait_run(start(j)["run_id"])
    w = r["steps"][1]["workers"][0]
    assert w["fork"] is False and "the planner ran on claude_code, the workers on codex" in w["fork_fallback"]


# ── real Claude (opt-in) ────────────────────────────────────────────────────

@pytest.mark.skipif(os.environ.get("TELECODE_REAL_CLAUDE") != "1", reason="set TELECODE_REAL_CLAUDE=1 to run")
def test_real_claude_forked_map_workers_share_the_planners_context(tmp_data_root, monkeypatch):
    """One real 2-item forked map on haiku: the planner is told a codeword it
    must keep out of its handoff; each worker, in its own folder at the same
    time, writes the codeword — which only a fork of the planner's
    conversation can know."""
    import config
    from services.task.task_registry import register_default_tasks
    from services.telemetry import settings as tset
    monkeypatch.setattr(tset, "enabled", lambda: False)              # no OTLP export to a running telecode
    q = get_task_queue()
    saved = dict(q.task_handlers)
    register_default_tasks()
    try:
        word = "PELICAN-" + str(int(time.time()) % 10000)
        j, wd = setup_job([
            {"name": "Planner", "model": "haiku",
             "prompt_override": f"Remember this codeword for later: {word}. Keep it private for now: do not "
                                "repeat it in this reply or in your handoff (a later message will ask for it). Do "
                                "not use any tools. Report a handoff whose `items` array is exactly "
                                "[\"alpha\", \"beta\"] and whose summary is \"planned\"."},
            {"name": "Worker", "kind": "map", "model": "haiku",
             "map": {"items_from": "items", "max_parallel": 2, "worker_session": "fork"},
             "prompt_override": "This is the later message: the codeword no longer needs to stay private, and you "
                                "are asked to write it now. Using the Write tool, create a file named <item>.txt "
                                "(where <item> is your map item) in the current directory whose only content is "
                                "the codeword you were told earlier in this conversation. Then report a short "
                                "handoff."}],
            ws="ws-real-fork", mode="custom", budget={"max_usd": 0.6})
        r = wait_run(start(j)["run_id"], deadline=300)
        assert r["status"] == "completed", json.dumps(r, default=str)[:3000]
        plan, mp = r["steps"]
        assert word not in json.dumps(plan.get("handoff") or {})
        ws = mp["workers"]
        assert all(w["fork"] and not w.get("fork_fallback") for w in ws), ws
        from services.run import handoff as handoff_mod
        for w in ws:                                                   # each worker's file, kept as an artifact
            f = handoff_mod.artifacts_dir(r["run_id"], mp["step_id"]) / f"w{w['n']}" / f"{w['item']}.txt"
            assert f.is_file(), (w, (w.get("handoff") or {}).get("summary"))
            assert word in f.read_text(encoding="utf-8"), (w["item"], f.read_text(encoding="utf-8"))
        assert not list(wd.glob("*.txt"))                               # nothing reached the planner's workspace
        t = [(datetime.fromisoformat(w["started_at"].replace("Z", "+00:00")),
              datetime.fromisoformat(w["completed_at"].replace("Z", "+00:00"))) for w in ws]
        assert max(s for s, _ in t) < min(e for _, e in t), t             # the two ran at the same time
        assert ws[0]["session_id"] != ws[1]["session_id"]
    finally:
        q.task_handlers.clear()
        q.task_handlers.update(saved)
