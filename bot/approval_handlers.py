"""Telegram side of the approvals inbox (P3) + trigger notices.

* When an approval is created (``approval.created`` on the live bus) and the bot
  is running, a message with inline **Approve** / **Reject** buttons goes to the
  General topic of ``telegram.group_id``; where it went is stored on the row
  (``approvals.telegram``). A pending approval created while the bot was down is
  posted by the periodic sweep once it is up.
* When it is decided — from the web inbox or from the buttons — the message is
  edited to show the outcome and loses its buttons.
* A button press is honoured only for users in ``telegram.allowed_user_ids`` (an
  EMPTY allowlist lets nobody decide from Telegram — the web inbox still works),
  and only for callback data of the exact shape ``apv:<a|r>:<approval id>``.
* ``trigger.notice`` (goal met, auto-paused, a notable reply) is posted to
  General as plain text.

All text is HTML-escaped; ParseMode.HTML.
"""

from __future__ import annotations

import asyncio
import logging
import re
from html import escape as _esc
from typing import Any, Dict, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError
from telegram.ext import ContextTypes

import config

log = logging.getLogger("telecode.handlers.approvals")

CALLBACK_RE = re.compile(r"^apv:([ar]):([A-Za-z0-9_-]{1,64})$")
CALLBACK_PATTERN = r"^apv:"
_BODY_EXCERPT = 1500
SWEEP_SECONDS = 30.0


def may_decide(user_id: Optional[int]) -> bool:
    allowed = config.allowed_user_ids()
    return bool(allowed) and user_id in allowed


def _group() -> int:
    try:
        return int(config.get_nested("telegram.group_id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _link(ap: Dict[str, Any]) -> str:
    port = int(config.get_nested("proxy.port", 1235))
    if ap.get("run_id"):
        job = (ap.get("payload") or {}).get("job_id")
        return f"http://127.0.0.1:{port}/team" + (f"#/job/{job}" if job else "")
    return f"http://127.0.0.1:{port}/team"


def format_approval(ap: Dict[str, Any]) -> str:
    icon = {"pending": "⏸", "approved": "✅", "rejected": "⛔", "cancelled": "✖️"}.get(ap.get("status"), "ℹ️")
    kind = {"gate": "Pipeline gate", "tool": "Tool permission", "memory": "Memory change"}.get(ap.get("kind"),
                                                                                             "Approval")
    lines = [f"{icon} <b>{_esc(kind)}: {_esc(ap.get('title') or '')}</b>"]
    job = (ap.get("payload") or {}).get("job_title")
    if job:
        lines.append(f"<i>Job:</i> {_esc(str(job))}")
    body = (ap.get("body") or "").strip()
    if body:
        if len(body) > _BODY_EXCERPT:
            body = body[:_BODY_EXCERPT].rstrip() + "…"
        lines.append(_esc(body))
    if ap.get("status") != "pending":
        who = ap.get("decided_by") or "?"
        tail = f"<b>{_esc(str(ap.get('status')).capitalize())}</b> by {_esc(who)}"
        if ap.get("decision_note"):
            tail += f": {_esc(ap['decision_note'][:500])}"
        if ap.get("edited_text"):
            tail += "\n<i>(approved with edited text)</i>"
        lines.append(tail)
    else:
        lines.append("<i>Approve or reject below, or in the web inbox (edit-then-approve is web only).</i>")
    lines.append(f"<code>{_esc(_link(ap))}</code>")
    return "\n\n".join(lines)


def keyboard(ap: Dict[str, Any]) -> Optional[InlineKeyboardMarkup]:
    if ap.get("status") != "pending":
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"apv:a:{ap['id']}"),
        InlineKeyboardButton("⛔ Reject", callback_data=f"apv:r:{ap['id']}"),
    ]])


def _bot_alive() -> bool:
    try:
        from bot.supervisor import get_supervisor
        sup = get_supervisor()
        return bool(sup and sup.alive())
    except Exception:
        return False


async def _send(bot, text: str, markup=None):
    kwargs: Dict[str, Any] = {"chat_id": _group(), "text": text, "parse_mode": ParseMode.HTML,
                              "disable_web_page_preview": True}
    if markup is not None:
        kwargs["reply_markup"] = markup
    try:
        return await bot.send_message(**kwargs)
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        return await bot.send_message(**kwargs)


async def post_approval(bot, ap: Dict[str, Any]) -> bool:
    """Post a pending approval to General; remember the message. True if sent."""
    from services import approvals
    if not _group() or ap.get("status") != "pending" or ap.get("telegram"):
        return False
    msg = await _send(bot, format_approval(ap), keyboard(ap))
    if msg is None:
        return False
    approvals.set_telegram(ap["id"], {"chat_id": getattr(msg, "chat_id", _group()),
                                      "message_id": getattr(msg, "message_id", None)})
    return True


async def update_message(bot, ap: Dict[str, Any]) -> bool:
    """Edit the posted message to the approval's current state."""
    tg = ap.get("telegram") or {}
    if not tg.get("message_id"):
        return False
    try:
        await bot.edit_message_text(chat_id=tg.get("chat_id") or _group(), message_id=tg["message_id"],
                                    text=format_approval(ap), parse_mode=ParseMode.HTML,
                                    reply_markup=keyboard(ap), disable_web_page_preview=True)
        return True
    except TelegramError as e:
        if "not modified" in str(e).lower():
            return True
        log.warning("approval message edit failed: %s", e)
        return False


async def handle_approval_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    from services import approvals
    q = update.callback_query
    m = CALLBACK_RE.match(q.data or "")
    user = update.effective_user
    if not m:
        await q.answer("Unknown action.")
        return
    if not may_decide(getattr(user, "id", None)):
        await q.answer("Not allowed — only telegram.allowed_user_ids can decide approvals.", show_alert=True)
        return
    decision = "approve" if m.group(1) == "a" else "reject"
    who = f"telegram:{getattr(user, 'username', None) or getattr(user, 'id', '?')}"
    try:
        ap = await asyncio.to_thread(approvals.decide, m.group(2), decision, by=who)
        await q.answer("Approved." if decision == "approve" else "Rejected.")
    except approvals.AlreadyDecided as exc:
        ap = exc.approval
        await q.answer(f"Already {ap.get('status')} by {ap.get('decided_by') or '?'}.", show_alert=True)
    except approvals.ApprovalError as exc:
        await q.answer(str(exc)[:190], show_alert=True)
        return
    try:
        await q.edit_message_text(format_approval(ap), parse_mode=ParseMode.HTML, reply_markup=keyboard(ap),
                                  disable_web_page_preview=True)
    except TelegramError as e:
        if "not modified" not in str(e).lower():
            log.warning("approval callback edit failed: %s", e)


def format_notice(data: Dict[str, Any]) -> str:
    icon = {"goal": "🎯", "auto_pause": "⏸", "reply": "🔔"}.get(data.get("kind"), "ℹ️")
    text = str(data.get("text") or "")
    if len(text) > 3500:
        text = text[:3500].rstrip() + "…"
    return f"{icon} <b>Trigger</b> — {_esc(str(data.get('name') or ''))}\n\n{_esc(text)}"


class ApprovalNotifier:
    """Follows the global live feed (approval + trigger kinds) on the bot's loop."""

    def __init__(self, bot):
        self.bot = bot

    async def handle(self, etype: str, data: Dict[str, Any]) -> None:
        if not _bot_alive() or not _group():
            return
        from services import approvals
        if etype == "approval.created":
            ap = await asyncio.to_thread(approvals.get, data.get("id"))
            if ap:
                await post_approval(self.bot, ap)
        elif etype == "approval.decided":
            ap = await asyncio.to_thread(approvals.get, data.get("id"))
            if ap:
                await update_message(self.bot, ap)
        elif etype == "trigger.notice":
            await _send(self.bot, format_notice(data))

    async def sweep(self) -> int:
        """Post pending approvals that never reached Telegram (bot was down)."""
        if not _bot_alive() or not _group():
            return 0
        from services import approvals
        n = 0
        for ap in await asyncio.to_thread(approvals.list_approvals, "pending", 50):
            if not ap.get("telegram"):
                try:
                    n += int(await post_approval(self.bot, ap))
                except TelegramError as e:
                    log.warning("posting approval %s failed: %s", ap.get("id"), e)
        return n

    async def run(self) -> None:
        from services import bus
        sub = bus.Subscription(kinds={"approval", "trigger"})
        last_sweep = 0.0
        loop = asyncio.get_running_loop()
        try:
            while True:
                item = await sub.get(5.0)
                if item is not None:
                    try:
                        await self.handle(*item)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        log.warning("approval notifier: %s", e)
                if loop.time() - last_sweep >= SWEEP_SECONDS:
                    last_sweep = loop.time()
                    try:
                        await self.sweep()
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        log.debug("approval sweep failed: %s", e)
        finally:
            sub.close()


def start_approval_notifier(app) -> asyncio.Task:
    task = asyncio.ensure_future(ApprovalNotifier(app.bot).run())
    app.bot_data["_approval_notifier"] = task
    return task
