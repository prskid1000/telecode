"""Telegram approvals (bot/approval_handlers.py) with fake Update / CallbackQuery
/ Bot objects — nothing is sent to Telegram."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bot import approval_handlers as ah
from services import approvals


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.answers = []
        self.edits = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text, **kw):
        self.edits.append((text, kw))


class FakeBot:
    def __init__(self):
        self.sent = []
        self.edited = []

    async def send_message(self, **kw):
        self.sent.append(kw)
        return SimpleNamespace(chat_id=kw["chat_id"], message_id=100 + len(self.sent))

    async def edit_message_text(self, **kw):
        self.edited.append(kw)


def _update(data, user_id=42, username="alice"):
    q = FakeQuery(data)
    return SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=user_id, username=username)), q


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    import config
    state = {"allowed": {42}, "group": -100123, "alive": True}
    monkeypatch.setattr(config, "allowed_user_ids", lambda: state["allowed"])
    real = config.get_nested
    monkeypatch.setattr(config, "get_nested", lambda path, default=None:
                        state["group"] if path == "telegram.group_id" else real(path, default))
    monkeypatch.setattr(ah, "_bot_alive", lambda: state["alive"])
    approvals.register_handler("tool", lambda ap: None)
    return state


def _ap(title="Deploy <prod> & friends"):
    return approvals.create("tool", title=title, body="run `rm -rf build/` <b>now</b>",
                            payload={"job_title": "Nightly <job>"})


def run(coro):
    return asyncio.run(coro)


def test_callback_validates_data_and_allowlist(cfg):
    ap = _ap()
    up, q = _update("apv:x:" + ap["id"])
    run(ah.handle_approval_callback(up, None))
    assert q.answers == [("Unknown action.", False)] and approvals.get(ap["id"])["status"] == "pending"
    up, q = _update("apv:a:../../etc")
    run(ah.handle_approval_callback(up, None))
    assert q.answers[0][0] == "Unknown action."
    up, q = _update(f"apv:a:{ap['id']}", user_id=7)                   # not on the allowlist
    run(ah.handle_approval_callback(up, None))
    assert q.answers[0][1] is True and "Not allowed" in q.answers[0][0]
    cfg["allowed"] = set()                                             # empty allowlist: nobody decides here
    up, q = _update(f"apv:a:{ap['id']}")
    run(ah.handle_approval_callback(up, None))
    assert "Not allowed" in q.answers[0][0] and approvals.get(ap["id"])["status"] == "pending"


def test_callback_approves_then_reports_already_decided(cfg):
    ap = _ap()
    up, q = _update(f"apv:a:{ap['id']}")
    run(ah.handle_approval_callback(up, None))
    done = approvals.get(ap["id"])
    assert done["status"] == "approved" and done["decided_by"] == "telegram:alice"
    assert q.answers == [("Approved.", False)]
    text, kw = q.edits[0]
    assert "<b>Approved</b> by telegram:alice" in text and kw["reply_markup"] is None
    assert "Deploy &lt;prod&gt; &amp; friends" in text and "&lt;b&gt;now&lt;/b&gt;" in text
    up, q = _update(f"apv:r:{ap['id']}")
    run(ah.handle_approval_callback(up, None))
    assert q.answers[0] == ("Already approved by telegram:alice.", True)
    assert approvals.get(ap["id"])["status"] == "approved"


def test_notifier_posts_with_buttons_and_edits_on_web_decision(cfg):
    bot = FakeBot()
    n = ah.ApprovalNotifier(bot)
    ap = _ap("Gate")
    run(n.handle("approval.created", {"id": ap["id"]}))
    msg = bot.sent[0]
    assert msg["chat_id"] == -100123 and msg["parse_mode"] == "HTML"
    rows = msg["reply_markup"].inline_keyboard
    assert [b.callback_data for b in rows[0]] == [f"apv:a:{ap['id']}", f"apv:r:{ap['id']}"]
    assert all(len(b.callback_data.encode()) <= 64 for b in rows[0])
    assert approvals.get(ap["id"])["telegram"] == {"chat_id": -100123, "message_id": 101}
    run(n.handle("approval.created", {"id": ap["id"]}))                # already posted → not again
    assert len(bot.sent) == 1
    approvals.decide(ap["id"], "reject", by="web", note="no <thanks>")
    run(n.handle("approval.decided", {"id": ap["id"]}))
    ed = bot.edited[0]
    assert ed["message_id"] == 101 and ed["reply_markup"] is None and "no &lt;thanks&gt;" in ed["text"]


def test_notifier_is_silent_when_bot_is_down_and_sweep_catches_up(cfg):
    bot = FakeBot()
    n = ah.ApprovalNotifier(bot)
    cfg["alive"] = False
    ap = _ap("While down")
    run(n.handle("approval.created", {"id": ap["id"]}))
    assert bot.sent == [] and run(n.sweep()) == 0
    cfg["alive"] = True
    assert run(n.sweep()) == 1 and approvals.get(ap["id"])["telegram"]["message_id"] == 101
    assert run(n.sweep()) == 0


def test_trigger_notice_is_escaped(cfg):
    bot = FakeBot()
    run(ah.ApprovalNotifier(bot).handle("trigger.notice", {"name": "t<1>", "kind": "goal", "text": "a < b"}))
    assert bot.sent[0]["text"] == "🎯 <b>Trigger</b> — t&lt;1&gt;\n\na &lt; b"
