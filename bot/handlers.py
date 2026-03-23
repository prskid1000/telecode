"""Telegram bot handlers — relay between Telegram and CLI processes."""
from __future__ import annotations
import asyncio
import logging
from html import escape as _esc

import io

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import ContextTypes

import config
from backends.registry import get_backend, all_backends
from backends.params import load_params
from sessions.manager import SessionManager
from sessions.screen import ScreenCapture, VideoCapture, enumerate_windows, get_window_title
from sessions.computer import ComputerControl
from bot.topic_manager import get_or_create_topic, invalidate_topic, close_topic
from bot.settings_handler import handle_settings
from bot.rate import (
    is_thread_not_found, handle_topic_gone, init_live_refs,
    cleanup_stale_sessions, full_cleanup,
)
from voice.health import get_status as voice_status, probe
from voice.prefs import get_prefs, set_pref, stt_active
from voice.stt import transcribe

log = logging.getLogger("telecode.handlers")

_MAX_TG_LEN = 4000

# ── Flood-control backoff ────────────────────────────────────────────────────

import time as _time

_flood_until: float = 0.0  # monotonic time until which we should back off


def _set_flood_backoff(retry_after: float) -> None:
    global _flood_until
    _flood_until = _time.monotonic() + retry_after + 1  # +1s safety margin


def _flood_active() -> bool:
    return _time.monotonic() < _flood_until

# ── Generic key map (VT100 / xterm escape sequences) ─────────────────────────

_KEYS: dict[str, str] = {
    # common
    "enter": "\r", "return": "\r",
    "esc": "\x1b", "escape": "\x1b",
    "tab": "\t",
    "backspace": "\x7f", "bs": "\x7f",
    "space": " ",
    "delete": "\x1b[3~", "del": "\x1b[3~",
    "insert": "\x1b[2~", "ins": "\x1b[2~",
    # arrows
    "up": "\x1b[A", "down": "\x1b[B",
    "right": "\x1b[C", "left": "\x1b[D",
    # navigation
    "home": "\x1b[H", "end": "\x1b[F",
    "pageup": "\x1b[5~", "pgup": "\x1b[5~",
    "pagedown": "\x1b[6~", "pgdn": "\x1b[6~",
    # function keys
    "f1": "\x1bOP", "f2": "\x1bOQ", "f3": "\x1bOR", "f4": "\x1bOS",
    "f5": "\x1b[15~", "f6": "\x1b[17~", "f7": "\x1b[18~", "f8": "\x1b[19~",
    "f9": "\x1b[20~", "f10": "\x1b[21~", "f11": "\x1b[23~", "f12": "\x1b[24~",
}

_MODIFIERS = {"ctrl", "alt", "shift"}


def _build_key_sequence(tokens: list[str]) -> str | None:
    """Parse modifier+key tokens into a VT100 escape sequence.

    Examples:
        ["enter"]           -> "\\r"
        ["ctrl", "c"]       -> "\\x03"
        ["alt", "x"]        -> "\\x1bx"
        ["ctrl", "alt", "del"] -> modifier-encoded sequence
        ["up"]              -> "\\x1b[A"
        ["ctrl", "up"]      -> "\\x1b[1;5A"
    """
    mods = set()
    key_name = None
    for t in tokens:
        low = t.lower()
        if low in _MODIFIERS:
            mods.add(low)
        else:
            key_name = low
            break

    if not key_name:
        return None

    # single printable character
    if len(key_name) == 1:
        ch = key_name
        if "ctrl" in mods:
            if ch.isalpha():
                code = chr(ord(ch.lower()) - ord("a") + 1)
                if "alt" in mods:
                    return "\x1b" + code
                return code
        if "alt" in mods:
            return "\x1b" + ch
        return ch

    base = _KEYS.get(key_name)
    if not base:
        return None

    if not mods:
        return base

    # xterm modifier encoding for special keys:  CSI 1;{mod_code} {final}
    mod_code = 1
    if "shift" in mods:
        mod_code += 1
    if "alt" in mods:
        mod_code += 2
    if "ctrl" in mods:
        mod_code += 4

    # Ctrl+letter-named keys (ctrl+backspace etc.) — just return base
    if base == "\r" or base == "\t" or base == " " or base == "\x7f":
        if "ctrl" in mods and base == "\x7f":
            return "\x1b[3;5~"  # ctrl+backspace → ctrl+delete in many terminals
        return base

    # CSI sequences like \x1b[A or \x1b[5~ → inject modifier
    if base.startswith("\x1b[") and len(base) >= 4:
        if base[-1] == "~":
            # \x1b[5~ → \x1b[5;{mod}~
            return base[:-1] + f";{mod_code}~"
        else:
            # \x1b[A → \x1b[1;{mod}A
            return f"\x1b[1;{mod_code}{base[-1]}"

    # SS3 sequences like \x1bOP → \x1b[1;{mod}P
    if base.startswith("\x1bO") and len(base) == 3:
        return f"\x1b[1;{mod_code}{base[-1]}"

    return base


BOT_COMMANDS = [
    BotCommand("start", "Choose an AI to start"),
    BotCommand("new", "Start a named session"),
    BotCommand("stop", "Stop a session"),
    BotCommand("key", "Send key (e.g. /key enter, /key ctrl c)"),
    BotCommand("pause", "Pause screen image capture"),
    BotCommand("resume", "Resume screen image capture"),
    BotCommand("voice", "Voice settings"),
    BotCommand("settings", "Configuration"),
    BotCommand("help", "List commands"),
]


def _mgr(ctx: ContextTypes.DEFAULT_TYPE) -> SessionManager:
    return ctx.bot_data["session_manager"]


def _next_session_name() -> str:
    """Generate a short unique session id (5 hex chars)."""
    import secrets
    return secrets.token_hex(3)[:5]


async def _kill_and_cleanup(ctx: ContextTypes.DEFAULT_TYPE, user_id: int, session_key: str) -> bool:
    """Kill a session and clean up live senders."""
    session = _mgr(ctx).get_session(user_id, session_key)
    if session and session.thread_id:
        _frame_senders.pop(session.thread_id, None)
    return await _mgr(ctx).kill_session(user_id, session_key)


def _is_allowed(user_id: int) -> bool:
    allowed = config.allowed_user_ids()
    return not allowed or user_id in allowed


async def _auth(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    if not _is_allowed(update.effective_user.id):
        await update.effective_message.reply_text("Not authorised.")
        return False
    return True


def _picker_kb() -> InlineKeyboardMarkup:
    rows = []
    for b in all_backends():
        rows.append([InlineKeyboardButton(
            b.info.name, callback_data=f"new_session:{b.info.key}",
        )])
    return InlineKeyboardMarkup(rows)


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _auth(update, ctx):
        return
    user_id = update.effective_user.id
    await full_cleanup(ctx.bot, _mgr(ctx), user_id)
    sessions = _mgr(ctx).user_sessions(user_id)
    if sessions:
        lines = ["Active sessions:\n"]
        for key, s in sessions.items():
            status = "running" if s.process.alive else "stopped"
            lines.append(f"  {_esc(s.backend.info.name)} ({_esc(s.session_name)}) - {status}")
        lines.append("\nStart another:")
        text = "\n".join(lines)
    else:
        text = "Choose an AI to start:"
    try:
        await update.message.reply_text(text, reply_markup=_picker_kb())
    except RetryAfter as e:
        _set_flood_backoff(e.retry_after)
        log.warning("/start: flood control — retry in %ds", e.retry_after)
        await asyncio.sleep(e.retry_after + 1)
        try:
            await update.message.reply_text(text, reply_markup=_picker_kb())
        except TelegramError as e2:
            log.warning("/start: retry also failed: %s", e2)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _auth(update, ctx):
        return
    await update.message.reply_text(
        "<b>Sessions</b>\n"
        "/start - Choose an AI\n"
        "/new &lt;ai&gt; [name] - Start a session\n"
        "/stop [name] - Stop session(s)\n\n"
        "<b>Terminal keys</b>\n"
        "/key enter, /key esc, /key tab\n"
        "/key up, /key down, /key left, /key right\n"
        "/key ctrl c, /key alt x, /key f5\n"
        "/key home, /key end, /key pgup, /key pgdn\n"
        "/key space, /key backspace, /key delete\n\n"
        "<b>Screen capture</b>\n"
        "/new screen [name] - Stream window images\n"
        "/new video [name] - Record 1-min window video\n"
        "/new computer [name] - Control a window via vision LLM\n"
        "/pause - Pause capture\n"
        "/resume - Resume capture\n\n"
        "<b>Other</b>\n"
        "/voice - Voice settings\n"
        "/settings - Configuration\n"
        "/help - This message",
        parse_mode=ParseMode.HTML,
    )


async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _auth(update, ctx):
        return
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /new claude  or  /new claude work",
            reply_markup=_picker_kb(),
        )
        return
    backend_key = args[0].lower()
    session_name = args[1] if len(args) > 1 else _next_session_name()
    await _start_session(update, ctx, backend_key, session_name)


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _auth(update, ctx):
        return
    user_id = update.effective_user.id
    args = ctx.args or []
    if args:
        key = args[0]
        # Try exact key match first, then search by session name
        session = _mgr(ctx).get_session(user_id, key)
        if not session:
            # Search by session name across all user sessions
            for sk, s in _mgr(ctx).user_sessions(user_id).items():
                if s.session_name == key:
                    session = s
                    key = sk
                    break
        if session and session.thread_id:
            await cleanup_live_message(session.thread_id)
        killed = await _kill_and_cleanup(ctx, user_id, key)
        if killed:
            await close_topic(ctx.bot, user_id, key)
            await update.message.reply_text(f"Stopped {key}.")
        else:
            await update.message.reply_text(f"No session '{key}'.")
    else:
        # No args: stop the session in the current thread, or all if in General
        session = _session_for_thread(update, ctx)
        if session:
            key = session.session_key
            if session.thread_id:
                await cleanup_live_message(session.thread_id)
            killed = await _kill_and_cleanup(ctx, user_id, key)
            if killed:
                await close_topic(ctx.bot, user_id, key)
                await update.message.reply_text(f"Stopped {key}.")
            else:
                await update.message.reply_text("Session already stopped.")
        else:
            # In General thread (or no matching session): stop all
            sessions = _mgr(ctx).user_sessions(user_id)
            if sessions:
                for s in sessions.values():
                    if s.thread_id:
                        await cleanup_live_message(s.thread_id)
                        _frame_senders.pop(s.thread_id, None)
                n = await _mgr(ctx).kill_all_sessions(user_id)
                await update.message.reply_text(f"Stopped all {n} session(s).")
            else:
                await update.message.reply_text("No active sessions.")


async def cmd_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _auth(update, ctx):
        return
    user_id = update.effective_user.id
    prefs   = await get_prefs(user_id)
    vs      = voice_status()
    stt_label = f"STT {'ON' if prefs['stt_on'] else 'OFF'}"
    await update.message.reply_text(
        vs.summary(),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(stt_label, callback_data="toggle:stt")],
            [InlineKeyboardButton("Re-check", callback_data="voice:probe")],
        ]),
    )


async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _auth(update, ctx):
        return
    await handle_settings(update, ctx)


# ── Generic key command ───────────────────────────────────────────────────────

async def cmd_key(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Send any keyboard key or combination to the terminal.

    /key enter          /key esc          /key tab
    /key up             /key down         /key left   /key right
    /key ctrl c         /key ctrl d       /key alt x
    /key ctrl shift a   /key f5           /key home
    /key space          /key backspace    /key delete
    /key a              /key 1            (any single character)
    """
    if not await _auth(update, ctx):
        return
    session = _session_for_thread(update, ctx)
    if not session:
        await update.message.reply_text("No session here. Use /start to begin.")
        return
    if not session.process.alive:
        await _kill_and_cleanup(ctx, update.effective_user.id, session.session_key)
        await update.message.reply_text(
            "Session ended. Start a new one:",
            reply_markup=_picker_kb(),
        )
        return

    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "<b>Usage:</b> /key &lt;key&gt;\n\n"
            "<b>Keys:</b> enter, esc, tab, backspace, space, delete,\n"
            "up, down, left, right, home, end, pgup, pgdn,\n"
            "f1-f12, or any single character (a-z, 0-9)\n\n"
            "<b>Modifiers:</b> ctrl, alt, shift\n"
            "<b>Examples:</b>\n"
            "  /key enter\n"
            "  /key ctrl c\n"
            "  /key alt f4\n"
            "  /key ctrl shift a",
            parse_mode=ParseMode.HTML,
        )
        return

    seq = _build_key_sequence(args)
    if not seq:
        await update.message.reply_text(
            f"Unknown key: {' '.join(args)}\nType /key for usage."
        )
        return

    try:
        await _mgr(ctx).send_raw(
            update.effective_user.id, session.session_key, seq
        )
    except RuntimeError as e:
        await update.message.reply_text(str(e))


# ── Callbacks ─────────────────────────────────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q    = update.callback_query
    data = q.data
    user_id = update.effective_user.id

    if not _is_allowed(user_id):
        await q.answer()
        return

    await q.answer()

    if data == "noop":
        return

    if data.startswith("new_session:"):
        backend_key = data.split(":", 1)[1]
        backend = get_backend(backend_key)
        name = backend.info.name if backend else backend_key

        await cleanup_stale_sessions(ctx.bot, _mgr(ctx), user_id)

        # Screen/video/computer: show window picker instead of starting immediately
        if backend_key in ("screen", "video", "computer"):
            cb_prefix = {"screen": "scr", "video": "vid", "computer": "cmp"}[backend_key]
            windows = enumerate_windows()
            if not windows:
                try:
                    await q.edit_message_text("No visible windows found.")
                except TelegramError:
                    pass
                return
            rows = []
            # Computer control: add "Full Screen" option at the top
            if backend_key == "computer":
                rows.append([InlineKeyboardButton(
                    "\U0001f5a5 Full Screen", callback_data="cmp:auto:0",
                )])
            for hwnd, title in windows[:20]:
                label = title[:40] + "\u2026" if len(title) > 40 else title
                cb_data = f"{cb_prefix}:auto:{hwnd}"
                if len(cb_data) <= 64:
                    rows.append([InlineKeyboardButton(label, callback_data=cb_data)])
            prompts = {
                "screen": "Pick a window to capture:",
                "video": "Pick a window to record:",
                "computer": "Pick a window or full screen to control:",
            }
            try:
                await q.edit_message_text(
                    prompts[backend_key],
                    reply_markup=InlineKeyboardMarkup(rows),
                )
            except TelegramError as e:
                log.warning("Failed to show window picker: %s", e)
            return

        session_name = _next_session_name()
        await q.edit_message_text(f"Starting {_esc(name)}\u2026", parse_mode=ParseMode.HTML)
        await _start_session_core(ctx, user_id, backend_key, session_name)

    elif data.startswith("stop:"):
        session_key = data.split(":", 1)[1]
        await _kill_and_cleanup(ctx, user_id, session_key)
        await close_topic(ctx.bot, user_id, session_key)
        try:
            await q.edit_message_text("Session stopped.")
        except TelegramError:
            pass

    elif data.startswith("restart:"):
        session_key  = data.split(":", 1)[1]
        backend_key  = session_key.split(":")[0]
        session_name = session_key.split(":", 1)[1] if ":" in session_key else "default"
        await cleanup_stale_sessions(ctx.bot, _mgr(ctx), user_id)
        await _start_session_core(ctx, user_id, backend_key, session_name)

    elif data.startswith("interrupt:"):
        session_key = data.split(":", 1)[1]
        try:
            await _mgr(ctx).interrupt(user_id, session_key)
        except Exception:
            pass

    elif data.startswith("scr:"):
        # Window picker callback: scr:{session_name}:{hwnd}
        parts = data.split(":", 2)
        if len(parts) == 3:
            session_name, hwnd_str = parts[1], parts[2]
            try:
                hwnd = int(hwnd_str)
            except ValueError:
                return
            await cleanup_stale_sessions(ctx.bot, _mgr(ctx), user_id)
            try:
                await q.edit_message_text("Starting image capture\u2026")
            except TelegramError:
                pass
            await _start_screen_session(ctx, user_id, session_name, hwnd)

    elif data.startswith("vid:"):
        # Video recording window picker callback: vid:{session_name}:{hwnd}
        parts = data.split(":", 2)
        if len(parts) == 3:
            session_name, hwnd_str = parts[1], parts[2]
            try:
                hwnd = int(hwnd_str)
            except ValueError:
                return
            await cleanup_stale_sessions(ctx.bot, _mgr(ctx), user_id)
            try:
                await q.edit_message_text("Starting video recording\u2026")
            except TelegramError:
                pass
            await _start_video_session(ctx, user_id, session_name, hwnd)

    elif data.startswith("cmp:"):
        # Computer control window picker callback: cmp:{session_name}:{hwnd}
        parts = data.split(":", 2)
        if len(parts) == 3:
            session_name, hwnd_str = parts[1], parts[2]
            try:
                hwnd = int(hwnd_str)
            except ValueError:
                return
            await cleanup_stale_sessions(ctx.bot, _mgr(ctx), user_id)
            try:
                await q.edit_message_text("Starting computer control\u2026")
            except TelegramError:
                pass
            await _start_computer_session(ctx, user_id, session_name, hwnd)

    elif data.startswith("scr_pause:"):
        session_key = data.split(":", 1)[1]
        if _mgr(ctx).pause_session(user_id, session_key):
            await q.edit_message_reply_markup(
                _screen_controls_kb(session_key, paused=True)
            )

    elif data.startswith("scr_resume:"):
        session_key = data.split(":", 1)[1]
        if _mgr(ctx).resume_session(user_id, session_key):
            await q.edit_message_reply_markup(
                _screen_controls_kb(session_key, paused=False)
            )

    elif data.startswith("toggle:"):
        side  = data.split(":", 1)[1]
        if side != "stt":
            return
        prefs = await get_prefs(user_id)
        key   = f"{side}_on"
        await set_pref(user_id, key, not prefs[key])
        new_prefs = await get_prefs(user_id)
        stt_label = f"STT {'ON' if new_prefs['stt_on'] else 'OFF'}"
        await q.edit_message_reply_markup(InlineKeyboardMarkup([
            [InlineKeyboardButton(stt_label, callback_data="toggle:stt")],
            [InlineKeyboardButton("Re-check", callback_data="voice:probe")],
        ]))

    elif data == "voice:probe":
        st = await probe()
        await q.message.reply_text(st.summary(), parse_mode=ParseMode.HTML)


# ── Messages ──────────────────────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _auth(update, ctx):
        return
    session = _session_for_thread(update, ctx)
    if not session:
        await update.message.reply_text("No session here. Use /start to begin.")
        return

    if not session.process.alive:
        # Auto-cleanup dead session and free budget
        await _kill_and_cleanup(ctx, update.effective_user.id, session.session_key)
        await update.message.reply_text("Session ended. Use /start to begin.")
        return

    # Screen/video sessions are view-only
    if isinstance(session.process, (ScreenCapture, VideoCapture)):
        await update.message.reply_text("Capture sessions are view-only.")
        return

    thread_id = update.message.message_thread_id
    chat_id = config.telegram_group_id()
    bot = ctx.bot

    # Computer control sessions: route text to LLM via .send()
    if isinstance(session.process, ComputerControl):
        # Finalize previous live message, start a fresh one for this turn
        old_lm = _live_messages.pop(thread_id, None)
        if old_lm:
            await old_lm.finalize()
        lm = _LiveMessage(bot, chat_id, thread_id)
        _live_messages[thread_id] = lm

        text = update.message.text.strip()
        log.info("Sending to computer control %s: %.100s", session.session_key, text)
        try:
            await session.process.send(text)
            session.touch()
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
        return

    # Finalize previous live message, start a fresh one for this turn
    old_lm = _live_messages.pop(thread_id, None)
    if old_lm:
        await old_lm.finalize()

    lm = _LiveMessage(bot, chat_id, thread_id)
    _live_messages[thread_id] = lm

    text = update.message.text.strip()
    log.info("Sending to %s: %.100s", session.session_key, text)
    try:
        await _mgr(ctx).send(update.effective_user.id, session.session_key, text)
    except RuntimeError as e:
        await update.message.reply_text(str(e))


async def handle_voice_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _auth(update, ctx):
        return
    user_id = update.effective_user.id
    vs      = voice_status()

    if not await stt_active(user_id, vs.stt_available):
        await update.message.reply_text("Voice input not available right now.")
        return

    msg   = await update.message.reply_text("Listening\u2026")
    voice = update.message.voice or update.message.audio
    audio = await (await voice.get_file()).download_as_bytearray()
    text  = await transcribe(bytes(audio))

    if not text:
        await msg.edit_text("Couldn't understand the audio.")
        return

    await msg.edit_text(text)

    session = _session_for_thread(update, ctx)
    if not session:
        await update.message.reply_text("No session here. Use /start to begin.")
        return

    thread_id = update.message.message_thread_id
    chat_id = config.telegram_group_id()
    old_lm = _live_messages.pop(thread_id, None)
    if old_lm:
        await old_lm.finalize()
    _live_messages[thread_id] = _LiveMessage(ctx.bot, chat_id, thread_id)

    try:
        await _mgr(ctx).send(user_id, session.session_key, text)
    except RuntimeError as e:
        await update.message.reply_text(str(e))


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _auth(update, ctx):
        return
    session = _session_for_thread(update, ctx)
    if not session:
        await update.message.reply_text("No session here. Use /start to begin.")
        return

    import os, aiofiles
    doc  = update.message.document
    dest = os.path.join(session.workdir, doc.file_name)
    async with aiofiles.open(dest, "wb") as f:
        await f.write(await (await doc.get_file()).download_as_bytearray())

    await update.message.reply_text(f"Saved {doc.file_name}")
    if update.message.caption:
        try:
            await _mgr(ctx).send(
                update.effective_user.id, session.session_key,
                f"{update.message.caption}\n\nFile saved at: {dest}",
            )
        except RuntimeError as e:
            await update.message.reply_text(str(e))


# ── Session startup ───────────────────────────────────────────────────────────

async def _start_session(update, ctx, backend_key: str, session_name: str) -> None:
    backend = get_backend(backend_key)
    if not backend:
        keys = ", ".join(b.info.key for b in all_backends())
        await update.message.reply_text(f"Unknown: {backend_key}. Available: {keys}")
        return

    # Screen/video/computer: show window picker instead of starting immediately
    if backend_key in ("screen", "video", "computer"):
        cb_prefix = {"screen": "scr", "video": "vid", "computer": "cmp"}[backend_key]
        await _show_window_picker(update, session_name, cb_prefix)
        return

    await update.message.reply_text(f"Starting {backend.info.name}\u2026")
    await _start_session_core(ctx, update.effective_user.id, backend_key, session_name)


async def _start_session_core(ctx, user_id: int, backend_key: str, session_name: str) -> None:
    backend     = get_backend(backend_key)
    session_key = f"{backend_key}:{session_name}"
    thread_id   = await get_or_create_topic(ctx.bot, user_id, session_key)
    params      = load_params(backend_key)

    async def _do_start(tid: int) -> None:
        bot     = ctx.bot
        chat_id = config.telegram_group_id()

        def on_output(text: str) -> None:
            asyncio.ensure_future(_send_output(bot, chat_id, tid, text))

        await _mgr(ctx).start_session(
            user_id=user_id, session_key=session_key, backend=backend,
            params=params, output_callback=on_output, thread_id=tid,
        )
        await bot.send_message(
            chat_id=chat_id,
            message_thread_id=tid,
            text=f"{_esc(backend.info.name)} ready. Type your message.\n"
                 f"Use /help for terminal key commands.",
            parse_mode=ParseMode.HTML,
        )

    try:
        await _do_start(thread_id)
    except BadRequest as exc:
        if "thread not found" not in str(exc).lower():
            raise
        await invalidate_topic(user_id, session_key)
        await _kill_and_cleanup(ctx, user_id, session_key)
        thread_id = await get_or_create_topic(ctx.bot, user_id, session_key)
        await _do_start(thread_id)


# ── Overlap detection (two-pointer, whitespace-insensitive) ───────────────────

_MIN_OVERLAP = 8  # min non-ws chars to count as genuine overlap


def _find_overlap_end(existing: str, new: str) -> int:
    """Two forward-moving pointers to find overlap between end of existing and start of new.

    For each candidate start position in existing, pointer i moves right
    through existing and pointer j moves right through new (both skipping
    whitespace). If i reaches the end of existing while still matching,
    then new[0..j] is the overlapping prefix we should skip.

    Returns the index in *new* where truly new content begins (0 = no overlap).
    """
    if not existing or not new:
        return 0

    # Only inspect the tail of existing (overlap can't exceed length of new)
    tail = existing[-(len(new) * 3):] if len(existing) > len(new) * 3 else existing

    ex = [c for c in tail if not c.isspace()]
    nw = [(i, c) for i, c in enumerate(new) if not c.isspace()]

    if not ex or not nw:
        return 0

    # Earliest start where remaining existing chars <= new chars
    min_start = max(0, len(ex) - len(nw))

    for start in range(min_start, len(ex) - _MIN_OVERLAP + 1):
        i = start   # forward pointer in existing
        j = 0       # forward pointer in new
        while i < len(ex) and j < len(nw):
            if ex[i] != nw[j][1]:
                break
            i += 1
            j += 1

        # i reached the end of existing → suffix/prefix overlap found
        if i == len(ex) and j >= _MIN_OVERLAP:
            return nw[j - 1][0] + 1

    return 0


# ── Live message (edit-in-place streaming) ────────────────────────────────────

_EDIT_INTERVAL = 1.0  # min seconds between edits (Telegram rate limit safety)


class _LiveMessage:
    """One bot message that keeps getting edited as output streams in."""

    def __init__(self, bot, chat_id: int, thread_id: int):
        self.bot = bot
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.msg_id: int | None = None
        self.full_text = ""
        self._last_sent = ""
        self._edit_scheduled = False
        self._edit_handle: asyncio.TimerHandle | None = None
        self._loop = asyncio.get_event_loop()

    async def _ensure_msg(self) -> None:
        """Create the placeholder message if it doesn't exist yet."""
        if self.msg_id is not None:
            return
        if _flood_active():
            return
        try:
            msg = await self.bot.send_message(
                chat_id=self.chat_id,
                message_thread_id=self.thread_id,
                text="<pre>\u2026</pre>",
                parse_mode=ParseMode.HTML,
            )
            self.msg_id = msg.message_id
        except RetryAfter as e:
            _set_flood_backoff(e.retry_after)
            log.warning("LiveMessage flood control — backing off %ds", e.retry_after)
        except BadRequest as e:
            if is_thread_not_found(e):
                asyncio.ensure_future(handle_topic_gone(self.thread_id))
                return
            log.warning("LiveMessage: failed to create message: %s", e)
        except TelegramError as e:
            log.warning("LiveMessage: failed to create message: %s", e)

    def append(self, text: str) -> None:
        """Append new output, trimming any overlap with what we already have."""
        skip = _find_overlap_end(self.full_text, text)
        trimmed = text[skip:] if skip > 0 else text
        if not trimmed.strip():
            return
        self.full_text += trimmed + "\n"
        if not self._edit_scheduled:
            self._edit_scheduled = True
            self._edit_handle = self._loop.call_later(
                _EDIT_INTERVAL, lambda: asyncio.ensure_future(self._do_edit())
            )

    async def _do_edit(self) -> None:
        """Edit the message with current accumulated text."""
        self._edit_scheduled = False
        await self._ensure_msg()

        display = self.full_text.strip()
        if not display or display == self._last_sent:
            return

        # If text exceeds limit, finalize current message and start a new one
        if len(display) > _MAX_TG_LEN:
            # Finalize current message with what fits
            await self._edit_to(self._last_sent or display[:_MAX_TG_LEN])
            # Start a new message for the overflow
            overflow = display[len(self._last_sent):].strip() if self._last_sent else display[_MAX_TG_LEN:].strip()
            self.full_text = overflow + "\n"
            self.msg_id = None
            self._last_sent = ""
            await self._ensure_msg()
            display = overflow
            if not display:
                return

        # Truncate to fit in one message
        if len(display) > _MAX_TG_LEN:
            display = display[-_MAX_TG_LEN:]

        await self._edit_to(display)

    async def _edit_to(self, text: str) -> None:
        """Perform the actual editMessageText API call."""
        if not self.msg_id or not text.strip():
            return
        if text == self._last_sent:
            return
        if _flood_active():
            # Skip this edit — will catch up on the next one
            return
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.msg_id,
                text=f"<pre>{_esc(text)}</pre>",
                parse_mode=ParseMode.HTML,
            )
            self._last_sent = text
        except RetryAfter as e:
            _set_flood_backoff(e.retry_after)
            log.warning("LiveMessage flood control — backing off %ds", e.retry_after)
        except BadRequest as e:
            if is_thread_not_found(e):
                asyncio.ensure_future(handle_topic_gone(self.thread_id))
                return
            if "not modified" not in str(e).lower():
                log.warning("LiveMessage edit failed: %s", e)
        except TelegramError as e:
            log.warning("LiveMessage edit failed: %s", e)

    async def finalize(self) -> None:
        """Final edit — flush everything left."""
        if self._edit_handle:
            self._edit_handle.cancel()
        self._edit_scheduled = False
        await self._do_edit()


# One LiveMessage per thread — replaced when user sends a new message
_live_messages: dict[int, _LiveMessage] = {}


async def _send_output(bot, chat_id: int, thread_id: int, text: str) -> None:
    """Called by the PTY output callback — append to the live message."""
    lm = _live_messages.get(thread_id)
    if not lm:
        lm = _LiveMessage(bot, chat_id, thread_id)
        _live_messages[thread_id] = lm
    lm.append(text)


async def cleanup_live_message(thread_id: int) -> None:
    """Finalize and remove a live message for a stopped session."""
    lm = _live_messages.pop(thread_id, None)
    if lm:
        await lm.finalize()


# ── Screen capture — window picker, live photo, pause/resume ──────────────────


def _screen_controls_kb(session_key: str, paused: bool) -> InlineKeyboardMarkup:
    if paused:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("\u25b6 Resume", callback_data=f"scr_resume:{session_key}"),
            InlineKeyboardButton("\u23f9 Stop", callback_data=f"stop:{session_key}"),
        ]])
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("\u23f8 Pause", callback_data=f"scr_pause:{session_key}"),
        InlineKeyboardButton("\u23f9 Stop", callback_data=f"stop:{session_key}"),
    ]])


async def _show_window_picker(update: Update, session_name: str, cb_prefix: str = "scr") -> None:
    """Enumerate visible windows and present an inline keyboard for selection."""
    windows = enumerate_windows()
    if not windows:
        await update.message.reply_text("No visible windows found.")
        return

    rows = []
    # Computer control: add "Full Screen" option at the top
    if cb_prefix == "cmp":
        rows.append([InlineKeyboardButton(
            "\U0001f5a5 Full Screen", callback_data=f"cmp:{session_name}:0",
        )])
    for hwnd, title in windows[:20]:
        label = title[:40] + "\u2026" if len(title) > 40 else title
        cb_data = f"{cb_prefix}:{session_name}:{hwnd}"
        if len(cb_data) <= 64:
            rows.append([InlineKeyboardButton(label, callback_data=cb_data)])

    prompts = {"scr": "Pick a window to capture:", "vid": "Pick a window to record:",
               "cmp": "Pick a window or full screen to control:"}
    await update.message.reply_text(
        prompts.get(cb_prefix, "Pick a window:"),
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _start_screen_session(ctx, user_id: int, session_name: str, hwnd: int) -> None:
    """Create a topic and start a ScreenCapture session."""
    backend = get_backend("screen")
    # Use window title as session name when user didn't provide one
    if session_name in ("default", "auto"):
        win_title = get_window_title(hwnd)
        if win_title:
            clean = win_title.strip().replace(":", "-")[:60]
            if clean:
                session_name = clean
        if session_name in ("default", "auto"):
            session_name = _next_session_name()
    session_key = f"screen:{session_name}"
    thread_id = await get_or_create_topic(ctx.bot, user_id, session_key)

    async def _do_start(tid: int) -> None:
        bot = ctx.bot
        chat_id = config.telegram_group_id()

        # Finalize any existing FrameSender for this topic
        old_lp = _frame_senders.pop(tid, None)
        if old_lp:
            await old_lp.finalize()

        lp = _FrameSender(bot, chat_id, tid, session_key)
        _frame_senders[tid] = lp

        def on_frame(jpeg_bytes: bytes) -> None:
            if not jpeg_bytes:
                # Empty = window gone
                async def _notify_closed():
                    try:
                        await bot.send_message(
                            chat_id=chat_id, message_thread_id=tid,
                            text="Window closed.",
                        )
                    except BadRequest as e:
                        if is_thread_not_found(e):
                            await handle_topic_gone(tid)
                    except TelegramError:
                        pass
                asyncio.ensure_future(_notify_closed())
                return
            lp.set_frame(jpeg_bytes)

        session = await _mgr(ctx).start_screen_session(
            user_id=user_id, session_key=session_key, backend=backend,
            hwnd=hwnd, output_callback=on_frame, thread_id=tid,
            capture_interval=config.image_interval(),
        )
        lp.process = session.process
        await bot.send_message(
            chat_id=chat_id, message_thread_id=tid,
            text="Capturing\u2026 Use /pause, /resume, or /stop.",
        )

    try:
        await _do_start(thread_id)
    except BadRequest as exc:
        if "thread not found" not in str(exc).lower():
            raise
        await invalidate_topic(user_id, session_key)
        await _kill_and_cleanup(ctx, user_id, session_key)
        thread_id = await get_or_create_topic(ctx.bot, user_id, session_key)
        await _do_start(thread_id)


async def _start_video_session(ctx, user_id: int, session_name: str, hwnd: int) -> None:
    """Create a topic and start a VideoCapture (1-min recording)."""
    backend = get_backend("video")
    # Use window title as session name when user didn't provide one
    if session_name in ("default", "auto"):
        win_title = get_window_title(hwnd)
        if win_title:
            clean = win_title.strip().replace(":", "-")[:60]
            if clean:
                session_name = clean
        if session_name in ("default", "auto"):
            session_name = _next_session_name()
    session_key = f"video:{session_name}"
    thread_id = await get_or_create_topic(ctx.bot, user_id, session_key)

    async def _do_start(tid: int) -> None:
        bot = ctx.bot
        chat_id = config.telegram_group_id()

        vc = VideoCapture(hwnd=hwnd, duration=config.video_interval(), fps=3)

        def on_text(text: str) -> None:
            async def _send_text():
                if _flood_active():
                    return
                try:
                    await bot.send_message(chat_id=chat_id, message_thread_id=tid, text=text)
                except RetryAfter as e:
                    _set_flood_backoff(e.retry_after)
                    log.warning("Video text flood control — backing off %ds", e.retry_after)
                except BadRequest as e:
                    if is_thread_not_found(e):
                        await handle_topic_gone(tid)
                        return
                    log.warning("Video text send failed: %s", e)
                except TelegramError as e:
                    log.warning("Video text send failed: %s", e)
            asyncio.ensure_future(_send_text())

        def on_video(video_bytes: bytes) -> None:
            async def _send():
                if _flood_active():
                    return
                try:
                    video_buf = io.BytesIO(video_bytes)
                    video_buf.name = "recording.mp4"
                    await bot.send_video(
                        chat_id=chat_id, message_thread_id=tid,
                        video=video_buf,
                        supports_streaming=True,
                    )
                except RetryAfter as e:
                    _set_flood_backoff(e.retry_after)
                    log.warning("Video send flood control — backing off %ds", e.retry_after)
                except BadRequest as e:
                    if is_thread_not_found(e):
                        await handle_topic_gone(tid)
                        return
                    log.warning("Failed to send video: %s", e)
                except TelegramError as e:
                    log.warning("Failed to send video: %s", e)
            asyncio.ensure_future(_send())

        vc.subscribe_text(on_text)
        vc.subscribe(on_video)

        await _mgr(ctx).start_video_session(
            user_id=user_id, session_key=session_key, backend=backend,
            hwnd=hwnd, video_callback=on_video, text_callback=on_text,
            thread_id=tid,
        )
        await bot.send_message(
            chat_id=chat_id, message_thread_id=tid,
            text=f"🎬 Recording\u2026 Sends a video every {config.video_interval()}s. Use /pause, /resume, or /stop.",
        )

    try:
        await _do_start(thread_id)
    except BadRequest as exc:
        if "thread not found" not in str(exc).lower():
            raise
        await invalidate_topic(user_id, session_key)
        await _kill_and_cleanup(ctx, user_id, session_key)
        thread_id = await get_or_create_topic(ctx.bot, user_id, session_key)
        await _do_start(thread_id)


async def _start_computer_session(ctx, user_id: int, session_name: str, hwnd: int) -> None:
    """Create a topic and start a ComputerControl session."""
    from sessions.computer import FULL_SCREEN_HWND
    backend = get_backend("computer")
    if session_name in ("default", "auto"):
        if hwnd == FULL_SCREEN_HWND:
            session_name = "Full Screen"
        else:
            win_title = get_window_title(hwnd)
            if win_title:
                clean = win_title.strip().replace(":", "-")[:60]
                if clean:
                    session_name = clean
            if session_name in ("default", "auto"):
                session_name = _next_session_name()
    session_key = f"computer:{session_name}"
    thread_id = await get_or_create_topic(ctx.bot, user_id, session_key)

    async def _do_start(tid: int) -> None:
        bot = ctx.bot
        chat_id = config.telegram_group_id()

        def on_text(text: str) -> None:
            asyncio.ensure_future(_send_output(bot, chat_id, tid, text))

        # Track the current screenshot message so we can edit it in place
        _photo_msg_id: dict[str, int | None] = {"id": None}

        def on_frame(jpeg_bytes: bytes) -> None:
            async def _send_or_edit_photo():
                if _flood_active():
                    return
                try:
                    photo_buf = io.BytesIO(jpeg_bytes)
                    photo_buf.name = "screen.jpg"
                    from telegram import InputMediaPhoto
                    if _photo_msg_id["id"]:
                        # Edit existing photo message
                        try:
                            await bot.edit_message_media(
                                chat_id=chat_id,
                                message_id=_photo_msg_id["id"],
                                media=InputMediaPhoto(media=photo_buf),
                            )
                            return
                        except BadRequest:
                            # Edit failed — fall through to send new
                            _photo_msg_id["id"] = None
                            photo_buf.seek(0)
                    # Send new photo
                    msg = await bot.send_photo(
                        chat_id=chat_id,
                        message_thread_id=tid,
                        photo=photo_buf,
                    )
                    _photo_msg_id["id"] = msg.message_id
                except RetryAfter as e:
                    _set_flood_backoff(e.retry_after)
                    log.warning("Computer photo flood control — backing off %ds", e.retry_after)
                except BadRequest as e:
                    if is_thread_not_found(e):
                        asyncio.ensure_future(handle_topic_gone(tid))
                        return
                    log.warning("Computer photo send failed: %s", e)
                except TelegramError as e:
                    log.warning("Computer photo send failed: %s", e)
            asyncio.ensure_future(_send_or_edit_photo())

        await _mgr(ctx).start_computer_session(
            user_id=user_id, session_key=session_key, backend=backend,
            hwnd=hwnd, text_callback=on_text, frame_callback=on_frame,
            thread_id=tid,
        )
        await bot.send_message(
            chat_id=chat_id, message_thread_id=tid,
            text="🖥️ <b>Computer Control</b> — Ready\n\n"
                 "Send a message to instruct the AI to control this window.\n"
                 "Use /stop to end the session.",
            parse_mode=ParseMode.HTML,
        )

    try:
        await _do_start(thread_id)
    except BadRequest as exc:
        if "thread not found" not in str(exc).lower():
            raise
        await invalidate_topic(user_id, session_key)
        await _kill_and_cleanup(ctx, user_id, session_key)
        thread_id = await get_or_create_topic(ctx.bot, user_id, session_key)
        await _do_start(thread_id)


class _FrameSender:
    """Sends each JPEG frame as a new photo message in a topic."""

    def __init__(self, bot, chat_id: int, thread_id: int, session_key: str):
        self.bot = bot
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.session_key = session_key
        self.process = None  # set after session starts, used to check paused
        self._pending_frame: bytes | None = None
        self._send_scheduled = False
        self._send_handle: asyncio.TimerHandle | None = None
        self._loop = asyncio.get_event_loop()

    def set_frame(self, jpeg_bytes: bytes) -> None:
        """Buffer the latest frame; schedule a send if not already pending."""
        if self.process and self.process.paused:
            return  # drop frames while paused
        self._pending_frame = jpeg_bytes
        if not self._send_scheduled:
            self._send_scheduled = True
            self._send_handle = self._loop.call_later(
                config.image_interval(),
                lambda: asyncio.ensure_future(self._do_send()),
            )

    async def _do_send(self) -> None:
        self._send_scheduled = False
        # Check paused again — pause may have happened after scheduling
        if self.process and self.process.paused:
            self._pending_frame = None
            return
        frame = self._pending_frame
        if not frame:
            return
        self._pending_frame = None

        if _flood_active():
            return
        try:
            photo_buf = io.BytesIO(frame)
            photo_buf.name = "frame.jpg"
            await self.bot.send_photo(
                chat_id=self.chat_id,
                message_thread_id=self.thread_id,
                photo=photo_buf,
                reply_markup=_screen_controls_kb(self.session_key, paused=False),
            )
        except RetryAfter as e:
            _set_flood_backoff(e.retry_after)
            log.warning("FrameSender flood control — backing off %ds", e.retry_after)
        except BadRequest as e:
            if is_thread_not_found(e):
                asyncio.ensure_future(handle_topic_gone(self.thread_id))
                return
            log.warning("FrameSender send failed: %s", e)
        except TelegramError as e:
            log.warning("FrameSender send failed: %s", e)
        except Exception as e:
            log.error("FrameSender unexpected error: %s", e, exc_info=True)

    async def finalize(self) -> None:
        if self._send_handle:
            self._send_handle.cancel()
        self._send_scheduled = False


# One FrameSender per screen-capture thread
_frame_senders: dict[int, _FrameSender] = {}

# Give rate module access to live dicts for cleanup on topic deletion
init_live_refs(_live_messages, _frame_senders)


async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _auth(update, ctx):
        return
    session = _session_for_thread(update, ctx)
    if not session or not isinstance(session.process, (ScreenCapture, VideoCapture, ComputerControl)):
        await update.message.reply_text("No capture/control session in this thread.")
        return
    _mgr(ctx).pause_session(update.effective_user.id, session.session_key)
    # Cancel any pending photo send
    lp = _frame_senders.get(update.message.message_thread_id)
    if lp:
        if lp._send_handle:
            lp._send_handle.cancel()
        lp._send_scheduled = False
        lp._pending_frame = None
    await update.message.reply_text("\u23f8 Paused. /resume to continue.")


async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _auth(update, ctx):
        return
    session = _session_for_thread(update, ctx)
    if not session or not isinstance(session.process, (ScreenCapture, VideoCapture, ComputerControl)):
        await update.message.reply_text("No capture/control session in this thread.")
        return
    _mgr(ctx).resume_session(update.effective_user.id, session.session_key)
    await update.message.reply_text("\u25b6 Resumed.")


def _session_for_thread(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    thread_id = update.message.message_thread_id if update.message else None
    if not thread_id:
        return None
    return _mgr(ctx).get_session_by_thread(update.effective_user.id, thread_id)
