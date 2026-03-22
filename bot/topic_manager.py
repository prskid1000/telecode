"""
Telegram Forum Topic manager — one topic per session.
Topic names: "{icon} {session_key}"   e.g. "🟣 claude:work"
Persistence via store.py (JSON file).
"""
from __future__ import annotations
import logging
from telegram import Bot
from telegram.error import TelegramError
import config
import store

log = logging.getLogger("telecode.topic_manager")


async def get_or_create_topic(bot: Bot, user_id: int, session_key: str) -> int:
    """Return an existing topic thread_id, or create a fresh one."""
    existing = await store.get_thread_id(user_id, session_key)
    if existing:
        return existing
    return await _create_topic(bot, user_id, session_key)


async def invalidate_topic(user_id: int, session_key: str) -> None:
    """Delete a stored thread_id that turned out to be stale."""
    log.warning("Invalidating stale topic for '%s'", session_key)
    await store.delete_thread_id(user_id, session_key)


async def close_topic(bot: Bot, user_id: int, session_key: str) -> None:
    thread_id = await store.get_thread_id(user_id, session_key)
    if not thread_id:
        return
    try:
        await bot.close_forum_topic(chat_id=config.telegram_group_id(),
                                    message_thread_id=thread_id)
    except TelegramError as e:
        log.warning("Could not close topic '%s': %s", session_key, e)
    await store.delete_thread_id(user_id, session_key)


async def list_topics(user_id: int) -> list[dict]:
    return await store.list_thread_ids(user_id)


async def _create_topic(bot: Bot, user_id: int, session_key: str) -> int:
    backend_key = session_key.split(":")[0]
    icon        = config.tool_icon(backend_key)
    topic_name  = f"{icon} {session_key}"

    try:
        result    = await bot.create_forum_topic(chat_id=config.telegram_group_id(), name=topic_name)
        thread_id = result.message_thread_id
    except TelegramError as e:
        log.error("Failed to create forum topic '%s': %s", topic_name, e)
        raise

    await store.save_thread_id(user_id, session_key, thread_id)
    log.info("Created topic '%s' thread_id=%d for user %d", topic_name, thread_id, user_id)
    return thread_id
