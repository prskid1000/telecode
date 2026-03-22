"""
Stale session cleanup and topic-gone detection.

Detects dead processes and externally deleted topics, then kills
the associated sessions.
"""
from __future__ import annotations

import logging

from telegram.error import BadRequest, TelegramError

import config
from bot.topic_manager import invalidate_topic
from sessions.manager import SessionManager

log = logging.getLogger("telecode.rate")

# ── Session manager ref ──────────────────────────────────────────────────────

_session_mgr: SessionManager | None = None


def set_session_manager(mgr: SessionManager) -> None:
    global _session_mgr
    _session_mgr = mgr


# ── Stale topic detection ────────────────────────────────────────────────────

# References to handlers' live dicts — set via init_live_refs()
_live_messages: dict | None = None
_frame_senders: dict | None = None


def init_live_refs(live_messages: dict, live_photos: dict) -> None:
    """Give rate module access to handler live message/photo dicts for cleanup."""
    global _live_messages, _frame_senders
    _live_messages = live_messages
    _frame_senders = live_photos


def is_thread_not_found(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(s in msg for s in (
        "thread not found",
        "topic_deleted",
        "topic_closed",
        "topic was deleted",
        "message thread is closed",
    ))


async def handle_topic_gone(thread_id: int) -> None:
    """User deleted/closed a topic externally — kill session."""
    if not _session_mgr:
        return
    for user_id, sessions in list(_session_mgr._sessions.items()):
        for key, session in list(sessions.items()):
            if session.thread_id == thread_id:
                log.info("Topic gone for %s — cleaning up session", key)
                if _live_messages is not None:
                    _live_messages.pop(thread_id, None)
                if _frame_senders is not None:
                    _frame_senders.pop(thread_id, None)
                await _session_mgr.kill_session(user_id, key)
                await invalidate_topic(user_id, key)
                return


# ── Topic probing (used by /start only) ──────────────────────────────────────

async def _probe_topic(bot, chat_id: int, thread_id: int) -> bool:
    """Send '.' and delete it. Returns True if topic exists."""
    try:
        msg = await bot.send_message(
            chat_id=chat_id, message_thread_id=thread_id, text=".",
        )
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
        except TelegramError:
            pass
        return True
    except BadRequest as e:
        if is_thread_not_found(e):
            log.info("Probe topic %d: GONE (%s)", thread_id, e)
            return False
        return True
    except TelegramError:
        return True  # network error — assume exists


# ── Cleanup ──────────────────────────────────────────────────────────────────

async def cleanup_stale_sessions(bot, mgr: SessionManager, user_id: int) -> None:
    """Fast cleanup: dead processes. No API calls."""
    sessions = mgr.user_sessions(user_id)
    for key, s in list(sessions.items()):
        if not s.process.alive:
            log.info("Dead process for %s — cleaning up", key)
            if s.thread_id:
                if _live_messages is not None:
                    _live_messages.pop(s.thread_id, None)
                if _frame_senders is not None:
                    _frame_senders.pop(s.thread_id, None)
            await mgr.kill_session(user_id, key)


async def full_cleanup(bot, mgr: SessionManager, user_id: int) -> None:
    """Full cleanup: dead processes + probe topics.

    Called from /start only. Probes each session's topic to detect
    externally deleted topics.
    """
    chat_id = config.telegram_group_id()
    sessions = mgr.user_sessions(user_id)
    for key, s in list(sessions.items()):
        if not s.process.alive:
            log.info("Dead process for %s — cleaning up", key)
            if s.thread_id:
                if _live_messages is not None:
                    _live_messages.pop(s.thread_id, None)
                if _frame_senders is not None:
                    _frame_senders.pop(s.thread_id, None)
            await mgr.kill_session(user_id, key)
            continue
        if not s.thread_id:
            continue
        if not await _probe_topic(bot, chat_id, s.thread_id):
            await handle_topic_gone(s.thread_id)
