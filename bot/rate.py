"""
Cost-based rate limiting and stale topic cleanup.

Every active session has a cost (msgs/min) from settings.json:
  - Tools: tools.<key>.rate  (e.g. claude=5, shell=3)
  - Image: rate_limits.image (e.g. 4 → 15s interval)
  - Video: rate_limits.video (e.g. 1 → 60s chunks)

Total cost must stay within rate_limits.budget_per_min (Telegram limit).
New sessions are blocked when budget is exhausted.

Stale topic checker: a background loop probes each active session's topic
via send_chat_action every 30s. If the topic is gone (user deleted it),
the session is killed and its cost freed.
"""
from __future__ import annotations

import asyncio
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


# ── Cost tracking ────────────────────────────────────────────────────────────

_active_costs: dict[str, float] = {}  # session_key -> cost (msgs/min)


def session_cost(session_key: str) -> float:
    """Look up cost for a session key."""
    backend_key = session_key.split(":")[0]
    if backend_key == "screen":
        return config.rate_cost_image()
    if backend_key == "video":
        return config.rate_cost_video()
    return config.tool_rate(backend_key)


def current_cost() -> float:
    return sum(_active_costs.values())


def available_budget() -> float:
    return max(config.rate_budget() - current_cost(), 0.0)


def can_afford(backend_key: str) -> bool:
    return session_cost(f"{backend_key}:x") <= available_budget()


def register_cost(session_key: str) -> None:
    _active_costs[session_key] = session_cost(session_key)


def unregister_cost(session_key: str) -> None:
    _active_costs.pop(session_key, None)


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
    return "thread not found" in str(exc).lower()


async def handle_topic_gone(thread_id: int) -> None:
    """User deleted/closed a topic externally — kill session, free budget."""
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
                unregister_cost(key)
                await _session_mgr.kill_session(user_id, key)
                await invalidate_topic(user_id, key)
                return


# ── Background stale topic checker ───────────────────────────────────────────

_stale_check_bot = None


async def start_stale_topic_checker(bot) -> asyncio.Task:
    """Start a background loop that periodically checks for deleted topics."""
    global _stale_check_bot
    _stale_check_bot = bot
    return asyncio.ensure_future(_stale_topic_loop())


async def _probe_topic(bot, chat_id: int, thread_id: int) -> bool:
    """Send and delete a probe message. Returns True if topic exists."""
    try:
        msg = await bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text="\u200b",  # zero-width space
        )
        await bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
        return True
    except BadRequest as e:
        if is_thread_not_found(e):
            return False
        return True  # other errors = topic probably exists
    except TelegramError:
        return True


async def _stale_topic_loop() -> None:
    """Every 30s, probe all active sessions' topics."""
    while True:
        await asyncio.sleep(30)
        if not _session_mgr or not _stale_check_bot:
            continue
        chat_id = config.telegram_group_id()
        for user_id, sessions in list(_session_mgr._sessions.items()):
            for key, s in list(sessions.items()):
                if not s.thread_id:
                    continue
                exists = await _probe_topic(_stale_check_bot, chat_id, s.thread_id)
                if not exists:
                    log.info("Stale topic detected for %s — cleaning up", key)
                    await handle_topic_gone(s.thread_id)


# ── On-demand stale check (called from /start) ──────────────────────────────

async def cleanup_stale_sessions(bot, mgr: SessionManager, user_id: int) -> None:
    """Probe each active session's topic; clean up any that are gone."""
    chat_id = config.telegram_group_id()
    sessions = mgr.user_sessions(user_id)
    for key, s in list(sessions.items()):
        if not s.thread_id:
            continue
        exists = await _probe_topic(bot, chat_id, s.thread_id)
        if not exists:
            log.info("Stale topic detected for %s — cleaning up", key)
            await handle_topic_gone(s.thread_id)
