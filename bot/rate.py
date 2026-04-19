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
    """Detect the various error strings Telegram returns when a topic
    has been closed, deleted, or otherwise made unreachable.

    Seen in the wild (all lowercased, so case doesn't matter):
      - "thread not found"
      - "message thread not found"
      - "topic_deleted"
      - "topic was deleted"
      - "topic_closed"
      - "message thread is closed"
      - "topic_not_modified"   (occasionally on edits into a gone topic)
      - "chat not found"       (if the whole supergroup is gone — rare)
      - "bad request: topic"   (catch-all for topic_* variants)
    """
    msg = str(exc).lower()
    return any(s in msg for s in (
        "thread not found",
        "topic_deleted",
        "topic was deleted",
        "topic_closed",
        "topic closed",
        "message thread is closed",
        "topic not found",
        "topic_not_found",
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
    """Send '.' and delete it. Returns True if topic exists.

    Any send failure that matches `is_thread_not_found` → topic gone.
    For anything else we log the raw error and fail-open (assume the
    topic exists) so a transient Telegram blip doesn't kill live
    sessions. The raw error is logged at INFO so we can tune the
    is_thread_not_found matchers for error strings we haven't seen
    yet."""
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
            log.info("Probe topic %d: GONE (BadRequest: %s)", thread_id, e)
            return False
        log.info("Probe topic %d: BadRequest (keeping session): %s", thread_id, e)
        return True
    except TelegramError as e:
        # Some builds of PTB wrap the error differently — fall back to
        # the string sniff on generic TelegramError too.
        if is_thread_not_found(e):
            log.info("Probe topic %d: GONE (TelegramError: %s)", thread_id, e)
            return False
        log.info("Probe topic %d: TelegramError (keeping session): %s", thread_id, e)
        return True


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


async def full_cleanup_all(bot, mgr: SessionManager) -> None:
    """Run `full_cleanup` for every known user. Cheap when there are
    no sessions, one sendMessage+delete per live session otherwise."""
    for user_id in list(mgr._sessions.keys()):
        try:
            await full_cleanup(bot, mgr, user_id)
        except Exception as exc:
            log.warning("full_cleanup(%s) failed: %s", user_id, exc)


async def topic_check_loop(bot, mgr: SessionManager, interval_sec: int = 60) -> None:
    """Periodic background sweep that probes every tracked topic via
    `full_cleanup_all`. Needed because Telegram doesn't send a service
    message when a forum topic is DELETED (only when it's CLOSED) —
    without this, deleted-topic sessions linger until the next /start.

    Interval is bounded: one sendMessage+delete per live session per
    tick. At the default 60 s + a couple of sessions, that's a handful
    of API calls per minute, well under Telegram's limits."""
    import asyncio
    try:
        while True:
            await asyncio.sleep(interval_sec)
            await full_cleanup_all(bot, mgr)
    except asyncio.CancelledError:
        return


async def full_cleanup(bot, mgr: SessionManager, user_id: int) -> None:
    """Full cleanup: dead processes + probe topics.

    Called from /start and from `topic_check_loop` (every 60 s by
    default). Probes each session's topic to detect externally deleted
    topics.
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
