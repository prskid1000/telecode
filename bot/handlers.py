"""Telegram bot handlers — relay between Telegram and CLI processes."""
from __future__ import annotations
import asyncio
import logging
from html import escape as _esc

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

import config
from backends.registry import get_backend, all_backends
from backends.params import load_params
from sessions.manager import SessionManager
from bot.topic_manager import get_or_create_topic, invalidate_topic, close_topic
from bot.settings_handler import handle_settings
from voice.health import get_status as voice_status, probe
from voice.prefs import get_prefs, set_pref, stt_active
from voice.stt import transcribe

log = logging.getLogger("telecode.handlers")

_MAX_TG_LEN = 4000

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
    BotCommand("voice", "Voice settings"),
    BotCommand("settings", "Configuration"),
    BotCommand("help", "List commands"),
]


def _mgr(ctx: ContextTypes.DEFAULT_TYPE) -> SessionManager:
    return ctx.bot_data["session_manager"]


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
    sessions = _mgr(ctx).user_sessions(update.effective_user.id)
    if sessions:
        lines = ["Active sessions:\n"]
        for key, s in sessions.items():
            status = "running" if s.process.alive else "stopped"
            lines.append(f"  {_esc(s.backend.info.name)} ({_esc(s.session_name)}) - {status}")
        lines.append("\nStart another:")
        text = "\n".join(lines)
    else:
        text = "Choose an AI to start:"
    await update.message.reply_text(text, reply_markup=_picker_kb())


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
    await _start_session(update, ctx, args[0].lower(), args[1] if len(args) > 1 else "default")


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _auth(update, ctx):
        return
    user_id = update.effective_user.id
    args = ctx.args or []
    if args:
        key = args[0]
        # Clean up live message before killing session
        session = _mgr(ctx).get_session(user_id, key)
        if session and session.thread_id:
            await cleanup_live_message(session.thread_id)
        killed = await _mgr(ctx).kill_session(user_id, key)
        if killed:
            await close_topic(ctx.bot, user_id, key)
            await update.message.reply_text(f"Stopped {key}.")
        else:
            await update.message.reply_text(f"No session {key}.")
    else:
        # Clean up live messages for all user sessions
        for s in _mgr(ctx).user_sessions(user_id).values():
            if s.thread_id:
                await cleanup_live_message(s.thread_id)
        n = await _mgr(ctx).kill_all_sessions(user_id)
        if n:
            await update.message.reply_text(f"Stopped all {n} session(s).")
        else:
            await update.message.reply_text("No sessions to stop.")


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
        return
    if not session.process.alive:
        await update.message.reply_text("Process stopped. Use /new to restart.")
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
    except RuntimeError:
        pass


# ── Callbacks ─────────────────────────────────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q    = update.callback_query
    data = q.data
    user_id = update.effective_user.id

    if not _is_allowed(user_id):
        await q.answer()
        return

    await q.answer()

    if data.startswith("new_session:"):
        backend_key = data.split(":", 1)[1]
        backend = get_backend(backend_key)
        name = backend.info.name if backend else backend_key
        await q.edit_message_text(f"Starting {_esc(name)}\u2026", parse_mode=ParseMode.HTML)
        await _start_session_core(ctx, user_id, backend_key, "default")

    elif data.startswith("stop:"):
        session_key = data.split(":", 1)[1]
        await _mgr(ctx).kill_session(user_id, session_key)
        await close_topic(ctx.bot, user_id, session_key)
        try:
            await q.edit_message_text("Session stopped.")
        except TelegramError:
            pass

    elif data.startswith("restart:"):
        session_key  = data.split(":", 1)[1]
        backend_key  = session_key.split(":")[0]
        session_name = session_key.split(":", 1)[1] if ":" in session_key else "default"
        await _start_session_core(ctx, user_id, backend_key, session_name)

    elif data.startswith("interrupt:"):
        session_key = data.split(":", 1)[1]
        try:
            await _mgr(ctx).interrupt(user_id, session_key)
        except Exception:
            pass

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
        await update.message.reply_text(
            "No session here. Use /start to begin.",
            reply_markup=_picker_kb(),
        )
        return

    if not session.process.alive:
        await update.message.reply_text("Process stopped. Use /new to restart.")
        return

    thread_id = update.message.message_thread_id
    chat_id = config.telegram_group_id()
    bot = ctx.bot

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
        await _mgr(ctx).kill_session(user_id, session_key)
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
        try:
            msg = await self.bot.send_message(
                chat_id=self.chat_id,
                message_thread_id=self.thread_id,
                text="<pre>\u2026</pre>",
                parse_mode=ParseMode.HTML,
            )
            self.msg_id = msg.message_id
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
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.msg_id,
                text=f"<pre>{_esc(text)}</pre>",
                parse_mode=ParseMode.HTML,
            )
            self._last_sent = text
        except BadRequest as e:
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


def _session_for_thread(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    thread_id = update.message.message_thread_id if update.message else None
    if not thread_id:
        return None
    return _mgr(ctx).get_session_by_thread(update.effective_user.id, thread_id)
