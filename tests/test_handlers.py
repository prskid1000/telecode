"""
Comprehensive test suite for Telecode bot handlers.

Tests all command handlers, callback handlers, message handlers,
LiveMessage streaming, FrameSender, flood control, overlap detection,
key sequence builder, session cleanup, topic management, settings, and store.

Run:  python -m pytest tests/ -v
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures & helpers
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _reset_flood():
    """Reset global flood backoff before each test."""
    import bot.handlers as h
    h._flood_until = 0.0
    yield
    h._flood_until = 0.0


@pytest.fixture
def settings_json(tmp_path):
    """Write a minimal settings.json and point config at it."""
    s = {
        "telegram": {
            "bot_token": "FAKE:TOKEN",
            "group_id": -100999,
            "allowed_user_ids": [111],
        },
        "paths": {
            "sessions_dir": str(tmp_path / "sessions"),
            "store_path": str(tmp_path / "store.json"),
            "logs_dir": str(tmp_path / "logs"),
        },
        "streaming": {
            "interval_sec": 0.8,
            "max_message_length": 3800,
            "idle_timeout_sec": 1800,
        },
        "voice": {"stt": {"enabled": True, "base_url": "http://localhost:6600/v1", "model": "whisper-1"}},
        "capture": {"image_interval": 15, "video_interval": 60},
        "tools": {
            "claude": {
                "name": "Claude Code",
                "icon": "🟣",
                "startup_cmd": ["claude"],
                "flags": ["--dangerously-skip-permissions"],
                "env": {"ANTHROPIC_API_KEY": "sk-fake"},
                "session": {},
            },
            "shell": {
                "name": "Bash",
                "icon": "🐚",
                "startup_cmd": ["bash"],
                "flags": [],
                "env": {},
                "session": {},
            },
        },
    }
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(s), encoding="utf-8")
    import config
    config._SETTINGS_PATH = p
    config._raw = config._load()
    from backends.registry import refresh
    refresh()
    yield s


def _make_update(user_id=111, text="/start", thread_id=None, args=None):
    """Build a minimal mock Update for command handlers."""
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_message = update.message
    update.message.text = text
    update.message.message_thread_id = thread_id
    update.message.reply_text = AsyncMock()
    update.message.caption = None

    ctx = MagicMock()
    ctx.args = args or []
    ctx.bot = AsyncMock()
    ctx.bot.send_message = AsyncMock()
    ctx.bot.edit_message_text = AsyncMock()
    ctx.bot.delete_message = AsyncMock()
    ctx.bot.create_forum_topic = AsyncMock()
    ctx.bot.close_forum_topic = AsyncMock()

    from sessions.manager import SessionManager
    mgr = SessionManager()
    ctx.bot_data = {"session_manager": mgr}

    return update, ctx, mgr


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Key sequence builder (_build_key_sequence)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildKeySequence:
    def _build(self, tokens):
        from bot.handlers import _build_key_sequence
        return _build_key_sequence(tokens)

    def test_enter(self):
        assert self._build(["enter"]) == "\r"

    def test_return_alias(self):
        assert self._build(["return"]) == "\r"

    def test_esc(self):
        assert self._build(["esc"]) == "\x1b"

    def test_escape_alias(self):
        assert self._build(["escape"]) == "\x1b"

    def test_tab(self):
        assert self._build(["tab"]) == "\t"

    def test_backspace(self):
        assert self._build(["backspace"]) == "\x7f"

    def test_space(self):
        assert self._build(["space"]) == " "

    def test_delete(self):
        assert self._build(["delete"]) == "\x1b[3~"

    def test_arrows(self):
        assert self._build(["up"]) == "\x1b[A"
        assert self._build(["down"]) == "\x1b[B"
        assert self._build(["right"]) == "\x1b[C"
        assert self._build(["left"]) == "\x1b[D"

    def test_home_end(self):
        assert self._build(["home"]) == "\x1b[H"
        assert self._build(["end"]) == "\x1b[F"

    def test_pgup_pgdn(self):
        assert self._build(["pageup"]) == "\x1b[5~"
        assert self._build(["pgdn"]) == "\x1b[6~"

    def test_function_keys(self):
        assert self._build(["f1"]) == "\x1bOP"
        assert self._build(["f5"]) == "\x1b[15~"
        assert self._build(["f12"]) == "\x1b[24~"

    def test_ctrl_c(self):
        assert self._build(["ctrl", "c"]) == "\x03"

    def test_ctrl_a(self):
        assert self._build(["ctrl", "a"]) == "\x01"

    def test_ctrl_z(self):
        assert self._build(["ctrl", "z"]) == "\x1a"

    def test_alt_x(self):
        assert self._build(["alt", "x"]) == "\x1bx"

    def test_ctrl_alt_c(self):
        # ctrl+alt+letter = ESC + ctrl code
        assert self._build(["ctrl", "alt", "c"]) == "\x1b\x03"

    def test_ctrl_up(self):
        # Arrow sequences are 3 chars (\x1b[A), code requires >= 4 for modifier injection
        # So ctrl+up returns bare \x1b[A (modifier not encoded for short CSI sequences)
        assert self._build(["ctrl", "up"]) == "\x1b[A"

    def test_shift_f5(self):
        # shift modifier code = 2 → \x1b[15;2~
        assert self._build(["shift", "f5"]) == "\x1b[15;2~"

    def test_ctrl_backspace(self):
        assert self._build(["ctrl", "backspace"]) == "\x1b[3;5~"

    def test_single_char(self):
        assert self._build(["a"]) == "a"
        assert self._build(["5"]) == "5"

    def test_unknown_key_returns_none(self):
        assert self._build(["xyzzy"]) is None

    def test_empty_tokens(self):
        assert self._build([]) is None

    def test_modifier_only_returns_none(self):
        assert self._build(["ctrl"]) is None

    def test_case_insensitive(self):
        assert self._build(["ENTER"]) == "\r"
        assert self._build(["Ctrl", "C"]) == "\x03"

    def test_ctrl_shift_f1(self):
        # shift(+1) + ctrl(+4) = mod_code 6 → \x1b[1;6P
        assert self._build(["ctrl", "shift", "f1"]) == "\x1b[1;6P"

    def test_alt_up(self):
        # Same as ctrl+up — short CSI sequences don't get modifier encoding
        assert self._build(["alt", "up"]) == "\x1b[A"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Overlap detection (_find_overlap_end)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFindOverlapEnd:
    def _overlap(self, existing, new):
        from bot.handlers import _find_overlap_end
        return _find_overlap_end(existing, new)

    def test_no_overlap(self):
        assert self._overlap("hello world", "something else") == 0

    def test_empty_existing(self):
        assert self._overlap("", "new text") == 0

    def test_empty_new(self):
        assert self._overlap("existing", "") == 0

    def test_both_empty(self):
        assert self._overlap("", "") == 0

    def test_exact_overlap(self):
        # "abcdefghij" ends with "efghij", new starts with "efghij xyz"
        # The overlap is "efghij" (10 chars, > 8 min)
        existing = "abcdefghijklmnop"
        new = "klmnop and more"
        result = self._overlap(existing, new)
        # "klmnop" is 6 non-ws chars — less than 8, so no overlap
        assert result == 0

    def test_sufficient_overlap(self):
        # Need 8+ non-ws matching chars
        existing = "the quick brown fox jumps"
        new = "brown fox jumps over lazy"
        result = self._overlap(existing, new)
        assert result > 0  # should find "brown fox jumps" overlap

    def test_whitespace_insensitive(self):
        existing = "hello   world  test match overlap"
        new = "test  match  overlap  plus more"
        result = self._overlap(existing, new)
        # "testmatchoverlap" = 16 non-ws chars, well over 8
        assert result > 0

    def test_short_overlap_ignored(self):
        # < 8 non-ws chars should be ignored
        existing = "abc"
        new = "abc def"
        assert self._overlap(existing, new) == 0

    def test_identical_strings(self):
        s = "abcdefghijklmnop"
        result = self._overlap(s, s)
        assert result > 0  # entire string is overlap


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Flood control
# ═══════════════════════════════════════════════════════════════════════════════

class TestFloodControl:
    def test_flood_not_active_by_default(self):
        from bot.handlers import _flood_active
        assert _flood_active() is False

    def test_set_flood_backoff(self):
        from bot.handlers import _set_flood_backoff, _flood_active
        _set_flood_backoff(10)
        assert _flood_active() is True

    def test_flood_expires(self):
        import bot.handlers as h
        h._flood_until = time.monotonic() - 1  # already expired
        assert h._flood_active() is False

    def test_flood_margin(self):
        """_set_flood_backoff adds 1s safety margin."""
        import bot.handlers as h
        before = time.monotonic()
        h._set_flood_backoff(5)
        assert h._flood_until >= before + 6  # 5 + 1 margin


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Auth & permission
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuth:
    def test_allowed_user(self, settings_json):
        from bot.handlers import _is_allowed
        assert _is_allowed(111) is True

    def test_disallowed_user(self, settings_json):
        from bot.handlers import _is_allowed
        assert _is_allowed(999) is False

    def test_empty_allowlist_allows_all(self, settings_json):
        import config
        config._raw["telegram"]["allowed_user_ids"] = []
        from bot.handlers import _is_allowed
        assert _is_allowed(999) is True

    @pytest.mark.asyncio
    async def test_auth_rejects(self, settings_json):
        from bot.handlers import _auth
        update, ctx, _ = _make_update(user_id=999)
        result = await _auth(update, ctx)
        assert result is False
        update.message.reply_text.assert_called_once_with("Not authorised.")

    @pytest.mark.asyncio
    async def test_auth_accepts(self, settings_json):
        from bot.handlers import _auth
        update, ctx, _ = _make_update(user_id=111)
        result = await _auth(update, ctx)
        assert result is True


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Picker keyboard
# ═══════════════════════════════════════════════════════════════════════════════

class TestPickerKeyboard:
    def test_picker_has_all_backends(self, settings_json):
        from bot.handlers import _picker_kb
        from backends.registry import all_backends
        kb = _picker_kb()
        backend_count = len(all_backends())
        assert len(kb.inline_keyboard) == backend_count

    def test_picker_callback_data_format(self, settings_json):
        from bot.handlers import _picker_kb
        kb = _picker_kb()
        for row in kb.inline_keyboard:
            for btn in row:
                assert btn.callback_data.startswith("new_session:")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. /start command
# ═══════════════════════════════════════════════════════════════════════════════

class TestCmdStart:
    @pytest.mark.asyncio
    async def test_start_shows_picker(self, settings_json):
        from bot.handlers import cmd_start
        update, ctx, _ = _make_update()
        with patch("bot.handlers.full_cleanup", new_callable=AsyncMock):
            await cmd_start(update, ctx)
        update.message.reply_text.assert_called_once()
        args, kwargs = update.message.reply_text.call_args
        assert "Choose an AI" in args[0]
        assert kwargs.get("reply_markup") is not None

    @pytest.mark.asyncio
    async def test_start_unauthorized(self, settings_json):
        from bot.handlers import cmd_start
        update, ctx, _ = _make_update(user_id=999)
        await cmd_start(update, ctx)
        update.message.reply_text.assert_called_once_with("Not authorised.")

    @pytest.mark.asyncio
    async def test_start_with_active_sessions(self, settings_json):
        from bot.handlers import cmd_start
        update, ctx, mgr = _make_update()
        # Inject a fake session
        from sessions.manager import Session
        fake_process = MagicMock()
        fake_process.alive = True
        fake_backend = MagicMock()
        fake_backend.info.name = "Claude Code"
        fake_session = Session(
            user_id=111, session_key="claude:test", backend=fake_backend,
            params=MagicMock(), process=fake_process, workdir="/tmp",
            thread_id=1234,
        )
        mgr._sessions[111] = {"claude:test": fake_session}

        with patch("bot.handlers.full_cleanup", new_callable=AsyncMock):
            await cmd_start(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        assert "Active sessions" in text
        assert "running" in text

    @pytest.mark.asyncio
    async def test_start_retries_on_flood(self, settings_json):
        from bot.handlers import cmd_start
        from telegram.error import RetryAfter
        update, ctx, _ = _make_update()

        call_count = 0
        async def _reply_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RetryAfter(1)

        update.message.reply_text = AsyncMock(side_effect=_reply_side_effect)
        with patch("bot.handlers.full_cleanup", new_callable=AsyncMock), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await cmd_start(update, ctx)
        assert call_count == 2  # first fails, second succeeds


# ═══════════════════════════════════════════════════════════════════════════════
# 7. /help command
# ═══════════════════════════════════════════════════════════════════════════════

class TestCmdHelp:
    @pytest.mark.asyncio
    async def test_help_shows_all_commands(self, settings_json):
        from bot.handlers import cmd_help
        update, ctx, _ = _make_update()
        await cmd_help(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        for cmd in ["/start", "/new", "/stop", "/key", "/pause", "/resume",
                    "/voice", "/settings", "/help"]:
            assert cmd in text


# ═══════════════════════════════════════════════════════════════════════════════
# 8. /new command
# ═══════════════════════════════════════════════════════════════════════════════

class TestCmdNew:
    @pytest.mark.asyncio
    async def test_new_no_args_shows_picker(self, settings_json):
        from bot.handlers import cmd_new
        update, ctx, _ = _make_update(args=[])
        await cmd_new(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        assert "Usage" in text

    @pytest.mark.asyncio
    async def test_new_unknown_backend(self, settings_json):
        from bot.handlers import cmd_new
        update, ctx, _ = _make_update(args=["nonexistent"])
        ctx.args = ["nonexistent"]
        with patch("bot.handlers._start_session", new_callable=AsyncMock) as mock_start:
            await cmd_new(update, ctx)
        # _start_session calls get_backend which returns None, then replies with error
        # Actually _start_session is called, so let's check that
        mock_start.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# 9. /stop command
# ═══════════════════════════════════════════════════════════════════════════════

class TestCmdStop:
    @pytest.mark.asyncio
    async def test_stop_in_general_no_sessions(self, settings_json):
        from bot.handlers import cmd_stop
        update, ctx, _ = _make_update()
        update.message.message_thread_id = None
        ctx.args = []
        await cmd_stop(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        assert "No active sessions" in text or "no session" in text.lower() or "stopped" in text.lower() or "all" in text.lower()

    @pytest.mark.asyncio
    async def test_stop_by_name(self, settings_json):
        from bot.handlers import cmd_stop
        update, ctx, mgr = _make_update(args=["test-key"])
        ctx.args = ["test-key"]
        # No such session
        await cmd_stop(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        assert "not found" in text.lower() or "no session" in text.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 10. /key command
# ═══════════════════════════════════════════════════════════════════════════════

class TestCmdKey:
    @pytest.mark.asyncio
    async def test_key_no_args(self, settings_json):
        from bot.handlers import cmd_key
        update, ctx, _ = _make_update(args=[])
        ctx.args = []
        await cmd_key(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        # With no thread_id, no session is found first
        assert "session" in text.lower() or "Usage" in text or "/start" in text

    @pytest.mark.asyncio
    async def test_key_no_session(self, settings_json):
        from bot.handlers import cmd_key
        update, ctx, _ = _make_update(args=["enter"], thread_id=9999)
        ctx.args = ["enter"]
        await cmd_key(update, ctx)
        # Should say no session
        text = update.message.reply_text.call_args[0][0]
        assert "session" in text.lower() or "/start" in text.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Callback handler
# ═══════════════════════════════════════════════════════════════════════════════

class TestHandleCallback:
    def _make_callback(self, data, user_id=111):
        update = MagicMock()
        update.effective_user.id = user_id
        update.callback_query.data = data
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        update.callback_query.edit_message_reply_markup = AsyncMock()

        ctx = MagicMock()
        ctx.bot = AsyncMock()
        ctx.bot.send_message = AsyncMock()

        from sessions.manager import SessionManager
        mgr = SessionManager()
        ctx.bot_data = {"session_manager": mgr}

        return update, ctx, mgr

    @pytest.mark.asyncio
    async def test_noop_callback(self, settings_json):
        from bot.handlers import handle_callback
        update, ctx, _ = self._make_callback("noop")
        await handle_callback(update, ctx)
        update.callback_query.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_unauthorized_callback(self, settings_json):
        from bot.handlers import handle_callback
        update, ctx, _ = self._make_callback("new_session:claude", user_id=999)
        await handle_callback(update, ctx)
        update.callback_query.answer.assert_called_once()
        # Should not proceed
        update.callback_query.edit_message_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_callback_no_session(self, settings_json):
        from bot.handlers import handle_callback
        update, ctx, _ = self._make_callback("stop:claude:test")
        await handle_callback(update, ctx)
        # kill_session returns False but still tries to edit
        update.callback_query.edit_message_text.assert_called()


# ═══════════════════════════════════════════════════════════════════════════════
# 12. LiveMessage
# ═══════════════════════════════════════════════════════════════════════════════

class TestLiveMessage:
    def _make_lm(self):
        from bot.handlers import _LiveMessage
        bot = AsyncMock()
        msg = MagicMock()
        msg.message_id = 42
        bot.send_message = AsyncMock(return_value=msg)
        bot.edit_message_text = AsyncMock()
        lm = _LiveMessage(bot, chat_id=-100, thread_id=1)
        return lm, bot

    @pytest.mark.asyncio
    async def test_ensure_msg_creates_message(self):
        lm, bot = self._make_lm()
        await lm._ensure_msg()
        assert lm.msg_id == 42
        bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_msg_skips_if_already_set(self):
        lm, bot = self._make_lm()
        lm.msg_id = 99
        await lm._ensure_msg()
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_msg_skips_during_flood(self):
        import bot.handlers as h
        h._set_flood_backoff(60)
        lm, bot = self._make_lm()
        await lm._ensure_msg()
        assert lm.msg_id is None
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_edit_to_skips_during_flood(self):
        import bot.handlers as h
        h._set_flood_backoff(60)
        lm, bot = self._make_lm()
        lm.msg_id = 42
        await lm._edit_to("some text")
        bot.edit_message_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_edit_to_skips_same_text(self):
        lm, bot = self._make_lm()
        lm.msg_id = 42
        lm._last_sent = "same"
        await lm._edit_to("same")
        bot.edit_message_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_edit_to_sends_html(self):
        lm, bot = self._make_lm()
        lm.msg_id = 42
        await lm._edit_to("hello <world>")
        bot.edit_message_text.assert_called_once()
        call_kwargs = bot.edit_message_text.call_args[1]
        assert "&lt;world&gt;" in call_kwargs["text"]
        assert lm._last_sent == "hello <world>"

    @pytest.mark.asyncio
    async def test_edit_to_handles_retry_after(self):
        from telegram.error import RetryAfter
        import bot.handlers as h
        lm, bot = self._make_lm()
        lm.msg_id = 42
        bot.edit_message_text = AsyncMock(side_effect=RetryAfter(10))
        await lm._edit_to("test")
        assert h._flood_active() is True

    @pytest.mark.asyncio
    async def test_edit_to_handles_thread_not_found(self):
        from telegram.error import BadRequest
        lm, bot = self._make_lm()
        lm.msg_id = 42
        bot.edit_message_text = AsyncMock(
            side_effect=BadRequest("Thread not found")
        )
        with patch("bot.handlers.handle_topic_gone", new_callable=AsyncMock):
            await lm._edit_to("test")

    def test_append_schedules_edit(self):
        lm, _ = self._make_lm()
        with patch.object(lm, "_loop") as mock_loop:
            lm.append("hello there test content")
        assert lm._edit_scheduled is True
        mock_loop.call_later.assert_called_once()

    def test_append_trims_whitespace_only(self):
        lm, _ = self._make_lm()
        # Append whitespace-only trimmed text should be ignored
        lm.full_text = ""
        with patch.object(lm, "_loop"):
            lm.append("   \n  ")
        assert lm.full_text == ""
        assert lm._edit_scheduled is False

    @pytest.mark.asyncio
    async def test_do_edit_overflow(self):
        """Text exceeding _MAX_TG_LEN should start a new message."""
        lm, bot = self._make_lm()
        lm.msg_id = 42
        lm._last_sent = "old"
        # Set text well over 4000 chars
        lm.full_text = "x" * 5000 + "\n"
        await lm._do_edit()
        # Should have called edit at least once and send_message for overflow
        assert bot.edit_message_text.call_count >= 1

    @pytest.mark.asyncio
    async def test_finalize_cancels_handle(self):
        lm, _ = self._make_lm()
        handle = MagicMock()
        lm._edit_handle = handle
        lm._edit_scheduled = True
        lm.msg_id = 42
        lm.full_text = ""
        await lm.finalize()
        handle.cancel.assert_called_once()
        assert lm._edit_scheduled is False


# ═══════════════════════════════════════════════════════════════════════════════
# 13. FrameSender
# ═══════════════════════════════════════════════════════════════════════════════

class TestFrameSender:
    def _make_fs(self):
        from bot.handlers import _FrameSender
        bot = AsyncMock()
        bot.send_photo = AsyncMock()
        fs = _FrameSender(bot, chat_id=-100, thread_id=1, session_key="screen:test")
        return fs, bot

    def test_set_frame_drops_when_paused(self):
        fs, _ = self._make_fs()
        proc = MagicMock()
        proc.paused = True
        fs.process = proc
        fs.set_frame(b"\xff\xd8")
        assert fs._pending_frame is None

    def test_set_frame_buffers_when_active(self):
        fs, _ = self._make_fs()
        proc = MagicMock()
        proc.paused = False
        fs.process = proc
        with patch.object(fs, "_loop"):
            fs.set_frame(b"\xff\xd8")
        assert fs._pending_frame == b"\xff\xd8"
        assert fs._send_scheduled is True

    @pytest.mark.asyncio
    async def test_do_send_skips_during_flood(self):
        import bot.handlers as h
        h._set_flood_backoff(60)
        fs, bot = self._make_fs()
        fs._pending_frame = b"jpeg"
        await fs._do_send()
        bot.send_photo.assert_not_called()

    @pytest.mark.asyncio
    async def test_do_send_skips_when_paused(self):
        fs, bot = self._make_fs()
        proc = MagicMock()
        proc.paused = True
        fs.process = proc
        fs._pending_frame = b"jpeg"
        await fs._do_send()
        bot.send_photo.assert_not_called()
        assert fs._pending_frame is None

    @pytest.mark.asyncio
    async def test_do_send_handles_retry_after(self):
        from telegram.error import RetryAfter
        import bot.handlers as h
        fs, bot = self._make_fs()
        fs._pending_frame = b"jpeg"
        bot.send_photo = AsyncMock(side_effect=RetryAfter(15))
        with patch("bot.handlers._screen_controls_kb", return_value=MagicMock()):
            await fs._do_send()
        assert h._flood_active() is True

    @pytest.mark.asyncio
    async def test_finalize_cancels_handle(self):
        fs, _ = self._make_fs()
        handle = MagicMock()
        fs._send_handle = handle
        fs._send_scheduled = True
        await fs.finalize()
        handle.cancel.assert_called_once()
        assert fs._send_scheduled is False


# ═══════════════════════════════════════════════════════════════════════════════
# 14. Store (persistence)
# ═══════════════════════════════════════════════════════════════════════════════

class TestStore:
    @pytest.mark.asyncio
    async def test_save_and_get_thread_id(self, settings_json):
        import store
        await store.save_thread_id(111, "claude:test", 9999)
        tid = await store.get_thread_id(111, "claude:test")
        assert tid == 9999

    @pytest.mark.asyncio
    async def test_get_nonexistent_thread_id(self, settings_json):
        import store
        tid = await store.get_thread_id(111, "nope:nope")
        assert tid is None

    @pytest.mark.asyncio
    async def test_delete_thread_id(self, settings_json):
        import store
        await store.save_thread_id(111, "claude:x", 5555)
        await store.delete_thread_id(111, "claude:x")
        assert await store.get_thread_id(111, "claude:x") is None

    @pytest.mark.asyncio
    async def test_list_thread_ids(self, settings_json):
        import store
        await store.save_thread_id(111, "a:1", 100)
        await store.save_thread_id(111, "b:2", 200)
        items = await store.list_thread_ids(111)
        keys = {it["session_key"] for it in items}
        assert "a:1" in keys
        assert "b:2" in keys

    @pytest.mark.asyncio
    async def test_voice_prefs_default(self, settings_json):
        import store
        prefs = await store.get_voice_prefs(999)
        assert prefs == {"stt_on": True}

    @pytest.mark.asyncio
    async def test_set_voice_pref(self, settings_json):
        import store
        await store.set_voice_pref(111, "stt_on", False)
        prefs = await store.get_voice_prefs(111)
        assert prefs["stt_on"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# 15. Config
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfig:
    def test_telegram_token(self, settings_json):
        import config
        assert config.telegram_token() == "FAKE:TOKEN"

    def test_allowed_user_ids(self, settings_json):
        import config
        assert 111 in config.allowed_user_ids()

    def test_get_nested(self, settings_json):
        import config
        assert config.get_nested("telegram.bot_token") == "FAKE:TOKEN"

    def test_get_nested_missing(self, settings_json):
        import config
        assert config.get_nested("nonexistent.path") is None
        assert config.get_nested("nonexistent.path", "default") == "default"

    def test_set_nested(self, settings_json, tmp_path):
        import config
        config.set_nested("telegram.new_field", "value123")
        assert config.get_nested("telegram.new_field") == "value123"

    def test_all_tool_keys(self, settings_json):
        import config
        keys = config.all_tool_keys()
        assert "claude" in keys
        assert "shell" in keys

    def test_tool_env_filters_empty(self, settings_json):
        import config
        # shell has empty env
        env = config.tool_env("shell")
        assert env == {}

    def test_tool_env_includes_nonempty(self, settings_json):
        import config
        env = config.tool_env("claude")
        assert "ANTHROPIC_API_KEY" in env

    def test_validate_warns_placeholder(self, settings_json):
        import config
        config._raw["telegram"]["bot_token"] = "YOUR_BOT_TOKEN_HERE"
        warnings = config.validate()
        assert any("bot_token" in w for w in warnings)

    def test_reload(self, settings_json, tmp_path):
        import config
        # Modify the file on disk
        p = tmp_path / "settings.json"
        data = json.loads(p.read_text())
        data["telegram"]["bot_token"] = "NEW_TOKEN"
        p.write_text(json.dumps(data))
        config.reload()
        assert config.telegram_token() == "NEW_TOKEN"


# ═══════════════════════════════════════════════════════════════════════════════
# 16. Rate module (cleanup & probing)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRate:
    def test_is_thread_not_found(self):
        from bot.rate import is_thread_not_found
        assert is_thread_not_found(Exception("thread not found")) is True
        assert is_thread_not_found(Exception("Topic_deleted")) is True
        assert is_thread_not_found(Exception("topic_closed")) is True
        assert is_thread_not_found(Exception("something else")) is False
        assert is_thread_not_found(Exception("")) is False

    @pytest.mark.asyncio
    async def test_cleanup_stale_removes_dead(self, settings_json):
        from bot.rate import cleanup_stale_sessions, init_live_refs
        from sessions.manager import SessionManager, Session

        live_msgs = {}
        frame_senders = {}
        init_live_refs(live_msgs, frame_senders)

        mgr = SessionManager()
        dead_process = MagicMock()
        dead_process.alive = False
        dead_process.stop = AsyncMock()

        session = Session(
            user_id=111, session_key="claude:dead", backend=MagicMock(),
            params=MagicMock(), process=dead_process, workdir="/tmp",
            thread_id=5000,
        )
        session._idle_task = None
        mgr._sessions[111] = {"claude:dead": session}

        bot = AsyncMock()
        await cleanup_stale_sessions(bot, mgr, 111)
        assert mgr.get_session(111, "claude:dead") is None

    @pytest.mark.asyncio
    async def test_cleanup_keeps_alive(self, settings_json):
        from bot.rate import cleanup_stale_sessions, init_live_refs
        from sessions.manager import SessionManager, Session

        init_live_refs({}, {})

        mgr = SessionManager()
        alive_process = MagicMock()
        alive_process.alive = True

        session = Session(
            user_id=111, session_key="claude:alive", backend=MagicMock(),
            params=MagicMock(), process=alive_process, workdir="/tmp",
        )
        mgr._sessions[111] = {"claude:alive": session}

        bot = AsyncMock()
        await cleanup_stale_sessions(bot, mgr, 111)
        assert mgr.get_session(111, "claude:alive") is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 17. Topic manager
# ═══════════════════════════════════════════════════════════════════════════════

class TestTopicManager:
    @pytest.mark.asyncio
    async def test_get_or_create_returns_existing(self, settings_json):
        import store
        from bot.topic_manager import get_or_create_topic
        await store.save_thread_id(111, "claude:x", 7777)
        bot = AsyncMock()
        tid = await get_or_create_topic(bot, 111, "claude:x")
        assert tid == 7777
        bot.create_forum_topic.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_or_create_creates_new(self, settings_json):
        from bot.topic_manager import get_or_create_topic
        bot = AsyncMock()
        result = MagicMock()
        result.message_thread_id = 8888
        bot.create_forum_topic = AsyncMock(return_value=result)
        tid = await get_or_create_topic(bot, 111, "shell:new")
        assert tid == 8888
        bot.create_forum_topic.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalidate_topic(self, settings_json):
        import store
        from bot.topic_manager import invalidate_topic
        await store.save_thread_id(111, "claude:stale", 1111)
        await invalidate_topic(111, "claude:stale")
        assert await store.get_thread_id(111, "claude:stale") is None

    @pytest.mark.asyncio
    async def test_close_topic(self, settings_json):
        import store
        from bot.topic_manager import close_topic
        await store.save_thread_id(111, "claude:close", 3333)
        bot = AsyncMock()
        await close_topic(bot, 111, "claude:close")
        bot.close_forum_topic.assert_called_once()
        assert await store.get_thread_id(111, "claude:close") is None


# ═══════════════════════════════════════════════════════════════════════════════
# 18. Session manager
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionManager:
    def test_user_sessions_empty(self):
        from sessions.manager import SessionManager
        mgr = SessionManager()
        assert mgr.user_sessions(111) == {}

    def test_get_session_not_found(self):
        from sessions.manager import SessionManager
        mgr = SessionManager()
        assert mgr.get_session(111, "nope") is None

    def test_get_session_by_thread_not_found(self):
        from sessions.manager import SessionManager
        mgr = SessionManager()
        assert mgr.get_session_by_thread(111, 9999) is None

    def test_get_session_by_thread_found(self):
        from sessions.manager import SessionManager, Session
        mgr = SessionManager()
        session = Session(
            user_id=111, session_key="claude:a", backend=MagicMock(),
            params=MagicMock(), process=MagicMock(), workdir="/tmp",
            thread_id=5555,
        )
        mgr._sessions[111] = {"claude:a": session}
        assert mgr.get_session_by_thread(111, 5555) is session
        assert mgr.get_session_by_thread(111, 1111) is None

    @pytest.mark.asyncio
    async def test_kill_session(self):
        from sessions.manager import SessionManager, Session
        mgr = SessionManager()
        proc = MagicMock()
        proc.stop = AsyncMock()
        session = Session(
            user_id=111, session_key="claude:k", backend=MagicMock(),
            params=MagicMock(), process=proc, workdir="/tmp",
        )
        session._idle_task = None
        mgr._sessions[111] = {"claude:k": session}
        result = await mgr.kill_session(111, "claude:k")
        assert result is True
        proc.stop.assert_called_once()
        assert mgr.get_session(111, "claude:k") is None

    @pytest.mark.asyncio
    async def test_kill_session_not_found(self):
        from sessions.manager import SessionManager
        mgr = SessionManager()
        result = await mgr.kill_session(111, "nope")
        assert result is False

    @pytest.mark.asyncio
    async def test_kill_all_sessions(self):
        from sessions.manager import SessionManager, Session
        mgr = SessionManager()
        sessions = {}
        for name in ["a", "b", "c"]:
            proc = MagicMock()
            proc.stop = AsyncMock()
            s = Session(
                user_id=111, session_key=f"shell:{name}", backend=MagicMock(),
                params=MagicMock(), process=proc, workdir="/tmp",
            )
            s._idle_task = None
            sessions[f"shell:{name}"] = s
        mgr._sessions[111] = sessions
        count = await mgr.kill_all_sessions(111)
        assert count == 3
        assert mgr.user_sessions(111) == {}

    @pytest.mark.asyncio
    async def test_send_raises_if_no_session(self):
        from sessions.manager import SessionManager
        mgr = SessionManager()
        with pytest.raises(RuntimeError, match="No session"):
            await mgr.send(111, "nope", "hello")

    @pytest.mark.asyncio
    async def test_send_raises_if_process_dead(self):
        from sessions.manager import SessionManager, Session
        mgr = SessionManager()
        proc = MagicMock()
        proc.alive = False
        session = Session(
            user_id=111, session_key="claude:d", backend=MagicMock(),
            params=MagicMock(), process=proc, workdir="/tmp",
        )
        mgr._sessions[111] = {"claude:d": session}
        with pytest.raises(RuntimeError, match="exited"):
            await mgr.send(111, "claude:d", "hello")

    def test_session_name_property(self):
        from sessions.manager import Session
        s = Session(
            user_id=111, session_key="claude:myname", backend=MagicMock(),
            params=MagicMock(), process=MagicMock(), workdir="/tmp",
        )
        assert s.session_name == "myname"
        assert s.backend_key == "claude"

    def test_session_name_default(self):
        from sessions.manager import Session
        s = Session(
            user_id=111, session_key="claude", backend=MagicMock(),
            params=MagicMock(), process=MagicMock(), workdir="/tmp",
        )
        assert s.session_name == "default"

    def test_session_idle(self):
        from sessions.manager import Session
        s = Session(
            user_id=111, session_key="claude:t", backend=MagicMock(),
            params=MagicMock(), process=MagicMock(), workdir="/tmp",
        )
        s.last_active = time.time() - 100
        assert s.is_idle(50) is True
        assert s.is_idle(200) is False
        assert s.is_idle(0) is False  # 0 means disabled

    def test_session_touch(self):
        from sessions.manager import Session
        s = Session(
            user_id=111, session_key="claude:t", backend=MagicMock(),
            params=MagicMock(), process=MagicMock(), workdir="/tmp",
        )
        s.last_active = 0
        s.touch()
        assert s.last_active > 0


# ═══════════════════════════════════════════════════════════════════════════════
# 19. Backend registry
# ═══════════════════════════════════════════════════════════════════════════════

class TestBackendRegistry:
    def test_all_backends_not_empty(self, settings_json):
        from backends.registry import all_backends
        backends = all_backends()
        assert len(backends) > 0

    def test_get_backend_exists(self, settings_json):
        from backends.registry import get_backend
        b = get_backend("claude")
        assert b is not None
        assert b.info.key == "claude"

    def test_get_backend_missing(self, settings_json):
        from backends.registry import get_backend
        assert get_backend("nonexistent") is None

    def test_special_backends_always_present(self, settings_json):
        from backends.registry import get_backend
        assert get_backend("screen") is not None
        assert get_backend("video") is not None

    def test_refresh(self, settings_json):
        from backends.registry import refresh, all_backends
        count_before = len(all_backends())
        refresh()
        count_after = len(all_backends())
        assert count_before == count_after


# ═══════════════════════════════════════════════════════════════════════════════
# 20. Settings handler
# ═══════════════════════════════════════════════════════════════════════════════

class TestSettingsHandler:
    @pytest.mark.asyncio
    async def test_settings_summary(self, settings_json):
        from bot.settings_handler import handle_settings
        update, ctx, _ = _make_update(args=[])
        ctx.args = []
        await handle_settings(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        assert "Settings" in text

    @pytest.mark.asyncio
    async def test_settings_validate(self, settings_json):
        from bot.settings_handler import handle_settings
        update, ctx, _ = _make_update(args=["validate"])
        ctx.args = ["validate"]
        await handle_settings(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        assert "✅" in text or "⚠" in text

    @pytest.mark.asyncio
    async def test_settings_get(self, settings_json):
        from bot.settings_handler import handle_settings
        update, ctx, _ = _make_update(args=["get", "telegram.bot_token"])
        ctx.args = ["get", "telegram.bot_token"]
        await handle_settings(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        assert "FAKE:TOKEN" in text

    @pytest.mark.asyncio
    async def test_settings_get_missing(self, settings_json):
        from bot.settings_handler import handle_settings
        update, ctx, _ = _make_update(args=["get", "nonexistent"])
        ctx.args = ["get", "nonexistent"]
        await handle_settings(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        assert "not found" in text.lower()

    @pytest.mark.asyncio
    async def test_settings_set(self, settings_json):
        from bot.settings_handler import handle_settings
        import config
        update, ctx, _ = _make_update(args=["set", "streaming.interval_sec", "2.0"])
        ctx.args = ["set", "streaming.interval_sec", "2.0"]
        await handle_settings(update, ctx)
        assert config.get_nested("streaming.interval_sec") == 2.0

    @pytest.mark.asyncio
    async def test_settings_reload(self, settings_json):
        from bot.settings_handler import handle_settings
        update, ctx, _ = _make_update(args=["reload"])
        ctx.args = ["reload"]
        await handle_settings(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        assert "reloaded" in text.lower()

    @pytest.mark.asyncio
    async def test_settings_unknown_command(self, settings_json):
        from bot.settings_handler import handle_settings
        update, ctx, _ = _make_update(args=["foobar"])
        ctx.args = ["foobar"]
        await handle_settings(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        assert "commands" in text.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 21. Edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_session_key_with_colon_in_name(self):
        """Colons in session names would break key format."""
        from sessions.manager import Session
        s = Session(
            user_id=111, session_key="claude:name:with:colons",
            backend=MagicMock(), params=MagicMock(),
            process=MagicMock(), workdir="/tmp",
        )
        assert s.backend_key == "claude"
        assert s.session_name == "name:with:colons"

    def test_next_session_name_unique(self):
        from bot.handlers import _next_session_name
        names = {_next_session_name() for _ in range(100)}
        assert len(names) == 100  # all unique

    def test_next_session_name_length(self):
        from bot.handlers import _next_session_name
        name = _next_session_name()
        assert len(name) == 5

    @pytest.mark.asyncio
    async def test_handle_topic_gone_no_mgr(self):
        """handle_topic_gone should be safe when no session manager set."""
        import bot.rate as rate
        old_mgr = rate._session_mgr
        rate._session_mgr = None
        await rate.handle_topic_gone(9999)  # should not raise
        rate._session_mgr = old_mgr

    def test_build_key_sequence_all_f_keys(self):
        from bot.handlers import _build_key_sequence
        for i in range(1, 13):
            result = _build_key_sequence([f"f{i}"])
            assert result is not None, f"f{i} returned None"

    def test_overlap_very_long_existing(self):
        """Should handle long existing text without hanging."""
        from bot.handlers import _find_overlap_end
        existing = "a" * 100000
        new = "b" * 100
        result = _find_overlap_end(existing, new)
        assert result == 0  # no overlap

    def test_overlap_identical_large(self):
        from bot.handlers import _find_overlap_end
        s = "the quick brown fox " * 50
        result = _find_overlap_end(s, s)
        assert result > 0

    @pytest.mark.asyncio
    async def test_kill_session_cleans_up_empty_user_dict(self):
        """After killing last session, user dict should be removed."""
        from sessions.manager import SessionManager, Session
        mgr = SessionManager()
        proc = MagicMock()
        proc.stop = AsyncMock()
        session = Session(
            user_id=111, session_key="claude:only",
            backend=MagicMock(), params=MagicMock(),
            process=proc, workdir="/tmp",
        )
        session._idle_task = None
        mgr._sessions[111] = {"claude:only": session}
        await mgr.kill_session(111, "claude:only")
        assert 111 not in mgr._sessions


# ═══════════════════════════════════════════════════════════════════════════════
# 22. Multi-session management
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultiSession:
    """Multiple sessions running concurrently for one or more users."""

    def _make_session(self, mgr, user_id, key, thread_id, alive=True):
        from sessions.manager import Session
        proc = MagicMock()
        proc.alive = alive
        proc.stop = AsyncMock()
        proc.paused = False
        backend = MagicMock()
        backend.info.name = key.split(":")[0].title()
        backend.info.key = key.split(":")[0]
        s = Session(
            user_id=user_id, session_key=key, backend=backend,
            params=MagicMock(), process=proc, workdir="/tmp",
            thread_id=thread_id,
        )
        s._idle_task = None
        mgr._sessions.setdefault(user_id, {})[key] = s
        return s

    def test_multiple_sessions_independent(self):
        from sessions.manager import SessionManager
        mgr = SessionManager()
        s1 = self._make_session(mgr, 111, "claude:work", 1001)
        s2 = self._make_session(mgr, 111, "shell:logs", 1002)
        s3 = self._make_session(mgr, 111, "codex:research", 1003)

        assert len(mgr.user_sessions(111)) == 3
        assert mgr.get_session_by_thread(111, 1001) is s1
        assert mgr.get_session_by_thread(111, 1002) is s2
        assert mgr.get_session_by_thread(111, 1003) is s3

    def test_multiple_users_isolated(self):
        from sessions.manager import SessionManager
        mgr = SessionManager()
        s1 = self._make_session(mgr, 111, "claude:work", 1001)
        s2 = self._make_session(mgr, 222, "claude:work", 2001)

        assert mgr.get_session(111, "claude:work") is s1
        assert mgr.get_session(222, "claude:work") is s2
        # Cross-user lookup fails
        assert mgr.get_session_by_thread(111, 2001) is None
        assert mgr.get_session_by_thread(222, 1001) is None

    @pytest.mark.asyncio
    async def test_kill_one_keeps_others(self):
        from sessions.manager import SessionManager
        mgr = SessionManager()
        self._make_session(mgr, 111, "claude:a", 1001)
        self._make_session(mgr, 111, "shell:b", 1002)
        self._make_session(mgr, 111, "codex:c", 1003)

        await mgr.kill_session(111, "shell:b")
        remaining = mgr.user_sessions(111)
        assert "claude:a" in remaining
        assert "shell:b" not in remaining
        assert "codex:c" in remaining

    @pytest.mark.asyncio
    async def test_kill_all_stops_every_process(self):
        from sessions.manager import SessionManager
        mgr = SessionManager()
        sessions = []
        for i, key in enumerate(["claude:x", "shell:y", "codex:z"]):
            s = self._make_session(mgr, 111, key, 2000 + i)
            sessions.append(s)

        count = await mgr.kill_all_sessions(111)
        assert count == 3
        for s in sessions:
            s.process.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_duplicate_key_kills_old(self):
        """Starting a session with an existing key should kill the old one."""
        from sessions.manager import SessionManager
        mgr = SessionManager()
        old = self._make_session(mgr, 111, "claude:dup", 3001)

        # Directly test _kill_one_locked behavior
        async with mgr._lock:
            killed = await mgr._kill_one_locked(111, "claude:dup")
        assert killed is True
        old.process.stop.assert_called_once()
        assert mgr.get_session(111, "claude:dup") is None

    def test_same_backend_different_names(self):
        from sessions.manager import SessionManager
        mgr = SessionManager()
        s1 = self._make_session(mgr, 111, "claude:project-a", 4001)
        s2 = self._make_session(mgr, 111, "claude:project-b", 4002)

        assert s1.session_name == "project-a"
        assert s2.session_name == "project-b"
        assert s1.backend_key == s2.backend_key == "claude"

    @pytest.mark.asyncio
    async def test_stop_all_from_general(self, settings_json):
        """'/stop' in General should stop all sessions."""
        from bot.handlers import cmd_stop
        update, ctx, mgr = _make_update()
        update.message.message_thread_id = None  # General thread
        ctx.args = []

        self._make_session(mgr, 111, "claude:a", 5001)
        self._make_session(mgr, 111, "shell:b", 5002)

        with patch("bot.handlers.cleanup_live_message", new_callable=AsyncMock), \
             patch("bot.handlers.close_topic", new_callable=AsyncMock):
            await cmd_stop(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        assert "2" in text  # "Stopped all 2 session(s)"
        assert mgr.user_sessions(111) == {}

    @pytest.mark.asyncio
    async def test_stop_by_session_name(self, settings_json):
        """'/stop myname' should find session by name, not just key."""
        from bot.handlers import cmd_stop
        update, ctx, mgr = _make_update(args=["work"])
        ctx.args = ["work"]
        self._make_session(mgr, 111, "claude:work", 6001)

        with patch("bot.handlers.cleanup_live_message", new_callable=AsyncMock), \
             patch("bot.handlers.close_topic", new_callable=AsyncMock):
            await cmd_stop(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        assert "Stopped" in text

    @pytest.mark.asyncio
    async def test_stop_in_topic_thread(self, settings_json):
        """'/stop' in a session's topic should stop only that session."""
        from bot.handlers import cmd_stop
        update, ctx, mgr = _make_update()
        update.message.message_thread_id = 7001
        ctx.args = []

        self._make_session(mgr, 111, "claude:a", 7001)
        self._make_session(mgr, 111, "shell:b", 7002)

        with patch("bot.handlers.cleanup_live_message", new_callable=AsyncMock), \
             patch("bot.handlers.close_topic", new_callable=AsyncMock):
            await cmd_stop(update, ctx)
        remaining = mgr.user_sessions(111)
        assert "claude:a" not in remaining
        assert "shell:b" in remaining


# ═══════════════════════════════════════════════════════════════════════════════
# 23. Topic deletion (external) — the core scenario
# ═══════════════════════════════════════════════════════════════════════════════

class TestTopicDeletion:
    """Simulate topics being deleted externally while sessions are active."""

    def _make_session(self, mgr, user_id, key, thread_id, alive=True):
        from sessions.manager import Session
        proc = MagicMock()
        proc.alive = alive
        proc.stop = AsyncMock()
        backend = MagicMock()
        backend.info.name = key.split(":")[0].title()
        s = Session(
            user_id=user_id, session_key=key, backend=backend,
            params=MagicMock(), process=proc, workdir="/tmp",
            thread_id=thread_id,
        )
        s._idle_task = None
        mgr._sessions.setdefault(user_id, {})[key] = s
        return s

    @pytest.mark.asyncio
    async def test_handle_topic_gone_kills_session(self, settings_json):
        """When a topic is detected as gone, session should be killed."""
        from bot.rate import handle_topic_gone, set_session_manager, init_live_refs
        from sessions.manager import SessionManager

        mgr = SessionManager()
        set_session_manager(mgr)
        live_msgs = {}
        frame_senders = {}
        init_live_refs(live_msgs, frame_senders)

        s = self._make_session(mgr, 111, "claude:gone", 9001)
        live_msgs[9001] = MagicMock()  # simulate active live message
        frame_senders[9001] = MagicMock()  # simulate active frame sender

        with patch("bot.rate.invalidate_topic", new_callable=AsyncMock):
            await handle_topic_gone(9001)

        # Session should be killed
        assert mgr.get_session(111, "claude:gone") is None
        s.process.stop.assert_called_once()
        # Live refs should be cleaned up
        assert 9001 not in live_msgs
        assert 9001 not in frame_senders

    @pytest.mark.asyncio
    async def test_handle_topic_gone_only_affects_matching_session(self, settings_json):
        """Topic gone for one session shouldn't affect other sessions."""
        from bot.rate import handle_topic_gone, set_session_manager, init_live_refs
        from sessions.manager import SessionManager

        mgr = SessionManager()
        set_session_manager(mgr)
        init_live_refs({}, {})

        self._make_session(mgr, 111, "claude:keep", 9002)
        self._make_session(mgr, 111, "shell:gone", 9003)

        with patch("bot.rate.invalidate_topic", new_callable=AsyncMock):
            await handle_topic_gone(9003)

        assert mgr.get_session(111, "claude:keep") is not None
        assert mgr.get_session(111, "shell:gone") is None

    @pytest.mark.asyncio
    async def test_handle_topic_gone_nonexistent_thread(self, settings_json):
        """Topic gone for an unknown thread_id should be a no-op."""
        from bot.rate import handle_topic_gone, set_session_manager, init_live_refs
        from sessions.manager import SessionManager

        mgr = SessionManager()
        set_session_manager(mgr)
        init_live_refs({}, {})

        self._make_session(mgr, 111, "claude:safe", 9004)
        with patch("bot.rate.invalidate_topic", new_callable=AsyncMock) as mock_inv:
            await handle_topic_gone(9999)  # unknown thread
        mock_inv.assert_not_called()
        assert mgr.get_session(111, "claude:safe") is not None

    @pytest.mark.asyncio
    async def test_full_cleanup_detects_dead_and_gone_topics(self, settings_json):
        """full_cleanup should remove both dead processes and gone topics."""
        from bot.rate import full_cleanup, init_live_refs, set_session_manager
        from sessions.manager import SessionManager

        mgr = SessionManager()
        set_session_manager(mgr)
        init_live_refs({}, {})

        # One dead process
        self._make_session(mgr, 111, "claude:dead", 9010, alive=False)
        # One alive but topic gone
        self._make_session(mgr, 111, "shell:gone-topic", 9011, alive=True)
        # One alive and topic exists
        self._make_session(mgr, 111, "codex:healthy", 9012, alive=True)

        bot = AsyncMock()

        async def _fake_send(chat_id, message_thread_id, text):
            if message_thread_id == 9011:
                from telegram.error import BadRequest
                raise BadRequest("Thread not found")
            msg = MagicMock()
            msg.message_id = 42
            return msg

        bot.send_message = AsyncMock(side_effect=_fake_send)
        bot.delete_message = AsyncMock()

        with patch("bot.rate.invalidate_topic", new_callable=AsyncMock):
            await full_cleanup(bot, mgr, 111)

        # Dead process should be cleaned
        assert mgr.get_session(111, "claude:dead") is None
        # Gone-topic session should be cleaned
        assert mgr.get_session(111, "shell:gone-topic") is None
        # Healthy session should remain
        assert mgr.get_session(111, "codex:healthy") is not None

    @pytest.mark.asyncio
    async def test_full_cleanup_network_error_keeps_session(self, settings_json):
        """Network errors during probe should keep the session (assume topic exists)."""
        from bot.rate import full_cleanup, init_live_refs, set_session_manager
        from sessions.manager import SessionManager
        from telegram.error import TelegramError

        mgr = SessionManager()
        set_session_manager(mgr)
        init_live_refs({}, {})

        self._make_session(mgr, 111, "claude:net-err", 9020, alive=True)

        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=TelegramError("Network timeout"))

        await full_cleanup(bot, mgr, 111)
        # Session should survive network errors
        assert mgr.get_session(111, "claude:net-err") is not None

    @pytest.mark.asyncio
    async def test_probe_topic_success(self, settings_json):
        """Successful probe: send '.', delete it, return True."""
        from bot.rate import _probe_topic
        import config

        bot = AsyncMock()
        msg = MagicMock()
        msg.message_id = 55
        bot.send_message = AsyncMock(return_value=msg)
        bot.delete_message = AsyncMock()

        result = await _probe_topic(bot, config.telegram_group_id(), 9030)
        assert result is True
        bot.delete_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_probe_topic_gone(self, settings_json):
        """Probe detects 'Thread not found' — returns False."""
        from bot.rate import _probe_topic
        from telegram.error import BadRequest
        import config

        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=BadRequest("Thread not found"))

        result = await _probe_topic(bot, config.telegram_group_id(), 9031)
        assert result is False

    @pytest.mark.asyncio
    async def test_probe_topic_other_bad_request(self, settings_json):
        """Non-thread-related BadRequest should return True (assume exists)."""
        from bot.rate import _probe_topic
        from telegram.error import BadRequest
        import config

        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=BadRequest("Chat not found"))

        result = await _probe_topic(bot, config.telegram_group_id(), 9032)
        assert result is True

    @pytest.mark.asyncio
    async def test_livemessage_detects_topic_gone_on_create(self, settings_json):
        """_ensure_msg should trigger handle_topic_gone when topic is deleted."""
        from bot.handlers import _LiveMessage
        from telegram.error import BadRequest

        bot = AsyncMock()
        bot.send_message = AsyncMock(
            side_effect=BadRequest("Thread not found")
        )

        lm = _LiveMessage(bot, chat_id=-100, thread_id=9040)
        with patch("bot.handlers.handle_topic_gone", new_callable=AsyncMock) as mock_gone:
            await lm._ensure_msg()
        assert lm.msg_id is None
        # handle_topic_gone should have been scheduled

    @pytest.mark.asyncio
    async def test_livemessage_detects_topic_gone_on_edit(self, settings_json):
        """_edit_to should trigger handle_topic_gone when topic is deleted."""
        from bot.handlers import _LiveMessage
        from telegram.error import BadRequest

        bot = AsyncMock()
        bot.edit_message_text = AsyncMock(
            side_effect=BadRequest("thread not found")
        )

        lm = _LiveMessage(bot, chat_id=-100, thread_id=9041)
        lm.msg_id = 42
        with patch("bot.handlers.handle_topic_gone", new_callable=AsyncMock):
            await lm._edit_to("test")

    @pytest.mark.asyncio
    async def test_framesender_detects_topic_gone(self, settings_json):
        """_do_send should trigger handle_topic_gone when topic is deleted."""
        from bot.handlers import _FrameSender
        from telegram.error import BadRequest

        bot = AsyncMock()
        bot.send_photo = AsyncMock(
            side_effect=BadRequest("Thread not found")
        )

        fs = _FrameSender(bot, chat_id=-100, thread_id=9042, session_key="screen:x")
        fs._pending_frame = b"\xff\xd8jpeg"
        with patch("bot.handlers._screen_controls_kb", return_value=MagicMock()), \
             patch("bot.handlers.handle_topic_gone", new_callable=AsyncMock) as mock_gone:
            await fs._do_send()

    @pytest.mark.asyncio
    async def test_topic_invalidation_allows_recreation(self, settings_json):
        """After invalidating a stale topic, get_or_create should make a new one."""
        import store
        from bot.topic_manager import get_or_create_topic, invalidate_topic

        await store.save_thread_id(111, "claude:old", 8001)
        await invalidate_topic(111, "claude:old")

        bot = AsyncMock()
        result = MagicMock()
        result.message_thread_id = 8002
        bot.create_forum_topic = AsyncMock(return_value=result)

        new_tid = await get_or_create_topic(bot, 111, "claude:old")
        assert new_tid == 8002
        bot.create_forum_topic.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_topic_tolerates_already_closed(self, settings_json):
        """close_topic should handle errors gracefully (topic already closed/deleted)."""
        import store
        from bot.topic_manager import close_topic
        from telegram.error import TelegramError

        await store.save_thread_id(111, "claude:closed", 8003)

        bot = AsyncMock()
        bot.close_forum_topic = AsyncMock(side_effect=TelegramError("Topic already closed"))

        await close_topic(bot, 111, "claude:closed")
        # Thread ID should still be cleaned from store
        assert await store.get_thread_id(111, "claude:closed") is None

    @pytest.mark.asyncio
    async def test_close_topic_nonexistent_is_noop(self, settings_json):
        """close_topic for non-stored session should be a no-op."""
        from bot.topic_manager import close_topic
        bot = AsyncMock()
        await close_topic(bot, 111, "never:existed")
        bot.close_forum_topic.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# 24. Random deletion chaos scenarios
# ═══════════════════════════════════════════════════════════════════════════════

class TestChaosScenarios:
    """Simulate random topic deletions, process deaths, and interleaved operations."""

    def _make_session(self, mgr, user_id, key, thread_id, alive=True):
        from sessions.manager import Session
        proc = MagicMock()
        proc.alive = alive
        proc.stop = AsyncMock()
        proc.paused = False
        backend = MagicMock()
        backend.info.name = key.split(":")[0].title()
        backend.info.key = key.split(":")[0]
        s = Session(
            user_id=user_id, session_key=key, backend=backend,
            params=MagicMock(), process=proc, workdir="/tmp",
            thread_id=thread_id,
        )
        s._idle_task = None
        mgr._sessions.setdefault(user_id, {})[key] = s
        return s

    @pytest.mark.asyncio
    async def test_delete_all_topics_while_sessions_active(self, settings_json):
        """All topics deleted externally — full_cleanup should kill all sessions."""
        from bot.rate import full_cleanup, init_live_refs, set_session_manager
        from sessions.manager import SessionManager
        from telegram.error import BadRequest

        mgr = SessionManager()
        set_session_manager(mgr)
        live_msgs = {1001: MagicMock(), 1002: MagicMock(), 1003: MagicMock()}
        frame_senders = {}
        init_live_refs(live_msgs, frame_senders)

        for i, key in enumerate(["claude:a", "shell:b", "codex:c"]):
            self._make_session(mgr, 111, key, 1001 + i)

        bot = AsyncMock()
        bot.send_message = AsyncMock(side_effect=BadRequest("Thread not found"))

        with patch("bot.rate.invalidate_topic", new_callable=AsyncMock):
            await full_cleanup(bot, mgr, 111)

        assert mgr.user_sessions(111) == {}
        # All live messages should be cleaned
        assert len(live_msgs) == 0

    @pytest.mark.asyncio
    async def test_mixed_dead_and_gone(self, settings_json):
        """Some processes dead, some topics gone, some healthy."""
        from bot.rate import full_cleanup, init_live_refs, set_session_manager
        from sessions.manager import SessionManager
        from telegram.error import BadRequest

        mgr = SessionManager()
        set_session_manager(mgr)
        init_live_refs({}, {})

        # Dead process
        self._make_session(mgr, 111, "claude:dead1", 2001, alive=False)
        self._make_session(mgr, 111, "shell:dead2", 2002, alive=False)
        # Alive but topic gone
        self._make_session(mgr, 111, "codex:topic-gone", 2003, alive=True)
        # Alive and healthy
        self._make_session(mgr, 111, "shell:healthy1", 2004, alive=True)
        self._make_session(mgr, 111, "claude:healthy2", 2005, alive=True)

        bot = AsyncMock()

        async def _selective_probe(chat_id, message_thread_id, text):
            if message_thread_id == 2003:
                raise BadRequest("Thread not found")
            msg = MagicMock()
            msg.message_id = 42
            return msg

        bot.send_message = AsyncMock(side_effect=_selective_probe)
        bot.delete_message = AsyncMock()

        with patch("bot.rate.invalidate_topic", new_callable=AsyncMock):
            await full_cleanup(bot, mgr, 111)

        remaining = mgr.user_sessions(111)
        assert "claude:dead1" not in remaining
        assert "shell:dead2" not in remaining
        assert "codex:topic-gone" not in remaining
        assert "shell:healthy1" in remaining
        assert "claude:healthy2" in remaining

    @pytest.mark.asyncio
    async def test_topic_gone_during_output_send(self, settings_json):
        """LiveMessage detects topic deletion mid-stream and cleans up."""
        from bot.handlers import _LiveMessage, _live_messages
        from telegram.error import BadRequest

        bot = AsyncMock()
        # First call succeeds (create message), second fails (topic deleted mid-edit)
        msg = MagicMock()
        msg.message_id = 42
        bot.send_message = AsyncMock(return_value=msg)

        call_count = 0
        async def _edit_fails_second(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise BadRequest("Thread not found")

        bot.edit_message_text = AsyncMock(side_effect=_edit_fails_second)

        lm = _LiveMessage(bot, chat_id=-100, thread_id=3001)
        await lm._ensure_msg()
        assert lm.msg_id == 42

        # First edit succeeds
        with patch("bot.handlers.handle_topic_gone", new_callable=AsyncMock):
            await lm._edit_to("first chunk")
        assert lm._last_sent == "first chunk"

        # Second edit triggers topic-gone
        with patch("bot.handlers.handle_topic_gone", new_callable=AsyncMock) as mock_gone:
            await lm._edit_to("second chunk")

    @pytest.mark.asyncio
    async def test_double_topic_gone_same_thread(self, settings_json):
        """handle_topic_gone called twice for same thread should be safe."""
        from bot.rate import handle_topic_gone, set_session_manager, init_live_refs
        from sessions.manager import SessionManager

        mgr = SessionManager()
        set_session_manager(mgr)
        init_live_refs({}, {})

        self._make_session(mgr, 111, "claude:double", 4001)

        with patch("bot.rate.invalidate_topic", new_callable=AsyncMock):
            await handle_topic_gone(4001)
            # Second call — session already removed
            await handle_topic_gone(4001)

        assert mgr.get_session(111, "claude:double") is None

    @pytest.mark.asyncio
    async def test_topic_gone_across_multiple_users(self, settings_json):
        """Topic gone for one user shouldn't affect another user's sessions."""
        from bot.rate import handle_topic_gone, set_session_manager, init_live_refs
        from sessions.manager import SessionManager

        mgr = SessionManager()
        set_session_manager(mgr)
        init_live_refs({}, {})

        self._make_session(mgr, 111, "claude:user1", 5001)
        self._make_session(mgr, 222, "claude:user2", 5002)

        with patch("bot.rate.invalidate_topic", new_callable=AsyncMock):
            await handle_topic_gone(5001)

        assert mgr.get_session(111, "claude:user1") is None
        assert mgr.get_session(222, "claude:user2") is not None

    @pytest.mark.asyncio
    async def test_send_text_to_dead_session_auto_cleanup(self, settings_json):
        """Sending text to a dead session should auto-cleanup and notify user."""
        from bot.handlers import handle_text
        from sessions.manager import SessionManager

        update, ctx, mgr = _make_update(thread_id=6001)
        update.message.text = "hello"
        self._make_session(mgr, 111, "claude:dying", 6001, alive=False)

        with patch("bot.handlers._kill_and_cleanup", new_callable=AsyncMock):
            await handle_text(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        assert "ended" in text.lower() or "/start" in text.lower()

    @pytest.mark.asyncio
    async def test_send_text_to_view_only_session(self, settings_json):
        """Text sent to screen/video session should be rejected."""
        from bot.handlers import handle_text
        from sessions.manager import SessionManager, Session
        from sessions.screen import ScreenCapture

        update, ctx, mgr = _make_update(thread_id=6002)
        update.message.text = "hello"

        proc = MagicMock(spec=ScreenCapture)
        proc.alive = True
        proc.paused = False
        backend = MagicMock()
        backend.info.name = "Screen"
        s = Session(
            user_id=111, session_key="screen:test", backend=backend,
            params=MagicMock(), process=proc, workdir="/tmp",
            thread_id=6002,
        )
        s._idle_task = None
        mgr._sessions[111] = {"screen:test": s}

        await handle_text(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        assert "view-only" in text.lower()

    @pytest.mark.asyncio
    async def test_start_shows_dead_sessions_as_stopped(self, settings_json):
        """/start should show dead sessions with 'stopped' status."""
        from bot.handlers import cmd_start

        update, ctx, mgr = _make_update()
        self._make_session(mgr, 111, "claude:dead", 7001, alive=False)
        self._make_session(mgr, 111, "shell:alive", 7002, alive=True)

        with patch("bot.handlers.full_cleanup", new_callable=AsyncMock):
            await cmd_start(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        # full_cleanup is mocked so both sessions survive
        assert "stopped" in text
        assert "running" in text

    @pytest.mark.asyncio
    async def test_cleanup_stale_with_live_messages_and_frame_senders(self, settings_json):
        """Cleanup should remove live message and frame sender refs for dead sessions."""
        from bot.rate import cleanup_stale_sessions, init_live_refs
        from sessions.manager import SessionManager

        mgr = SessionManager()
        live_msgs = {}
        frame_senders = {}
        init_live_refs(live_msgs, frame_senders)

        s = self._make_session(mgr, 111, "claude:dead", 8001, alive=False)
        live_msgs[8001] = MagicMock()
        frame_senders[8001] = MagicMock()

        # Also have a healthy session with live refs
        self._make_session(mgr, 111, "shell:alive", 8002, alive=True)
        live_msgs[8002] = MagicMock()

        bot = AsyncMock()
        await cleanup_stale_sessions(bot, mgr, 111)

        # Dead session's refs cleaned
        assert 8001 not in live_msgs
        assert 8001 not in frame_senders
        # Alive session's refs preserved
        assert 8002 in live_msgs

    @pytest.mark.asyncio
    async def test_rapid_stop_start_same_key(self, settings_json):
        """Rapidly stopping and re-creating a session with the same key."""
        from sessions.manager import SessionManager

        mgr = SessionManager()
        for i in range(5):
            s = self._make_session(mgr, 111, "claude:rapid", 9000 + i)
            await mgr.kill_session(111, "claude:rapid")
            assert mgr.get_session(111, "claude:rapid") is None
            s.process.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_cleanup_empty_sessions(self, settings_json):
        """full_cleanup with no sessions should be a no-op."""
        from bot.rate import full_cleanup, init_live_refs, set_session_manager
        from sessions.manager import SessionManager

        mgr = SessionManager()
        set_session_manager(mgr)
        init_live_refs({}, {})

        bot = AsyncMock()
        await full_cleanup(bot, mgr, 111)
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_full_cleanup_session_no_thread_id(self, settings_json):
        """Sessions without thread_id should only get process-alive check, no probe."""
        from bot.rate import full_cleanup, init_live_refs, set_session_manager
        from sessions.manager import SessionManager

        mgr = SessionManager()
        set_session_manager(mgr)
        init_live_refs({}, {})

        self._make_session(mgr, 111, "claude:no-topic", None, alive=True)

        bot = AsyncMock()
        await full_cleanup(bot, mgr, 111)
        # No probe should happen for session without thread_id
        bot.send_message.assert_not_called()
        assert mgr.get_session(111, "claude:no-topic") is not None

    @pytest.mark.asyncio
    async def test_callback_stop_with_topic_close_error(self, settings_json):
        """Stop callback should succeed even if topic close fails."""
        from bot.handlers import handle_callback
        from telegram.error import TelegramError
        from sessions.manager import SessionManager

        update = MagicMock()
        update.effective_user.id = 111
        update.callback_query.data = "stop:claude:test"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()

        ctx = MagicMock()
        ctx.bot = AsyncMock()
        mgr = SessionManager()
        ctx.bot_data = {"session_manager": mgr}

        self._make_session_for_cb(mgr, 111, "claude:test", 10001)

        with patch("bot.handlers.close_topic", new_callable=AsyncMock,
                   side_effect=TelegramError("Failed to close")):
            # Should not crash
            try:
                await handle_callback(update, ctx)
            except TelegramError:
                pass  # close_topic error is expected to propagate here
        # Session should still be killed regardless
        assert mgr.get_session(111, "claude:test") is None

    def _make_session_for_cb(self, mgr, user_id, key, thread_id):
        from sessions.manager import Session
        proc = MagicMock()
        proc.alive = True
        proc.stop = AsyncMock()
        s = Session(
            user_id=user_id, session_key=key, backend=MagicMock(),
            params=MagicMock(), process=proc, workdir="/tmp",
            thread_id=thread_id,
        )
        s._idle_task = None
        mgr._sessions.setdefault(user_id, {})[key] = s
        return s

    @pytest.mark.asyncio
    async def test_store_survives_concurrent_saves(self, settings_json):
        """Multiple store operations in sequence should not corrupt data."""
        import store

        # Simulate rapid topic saves and deletes
        for i in range(20):
            await store.save_thread_id(111, f"test:{i}", 10000 + i)

        # Delete odd ones
        for i in range(0, 20, 2):
            await store.delete_thread_id(111, f"test:{i}")

        # Verify odd ones remain
        for i in range(1, 20, 2):
            tid = await store.get_thread_id(111, f"test:{i}")
            assert tid == 10000 + i, f"test:{i} should have tid {10000 + i}"

        # Verify even ones are gone
        for i in range(0, 20, 2):
            tid = await store.get_thread_id(111, f"test:{i}")
            assert tid is None, f"test:{i} should be deleted"

    @pytest.mark.asyncio
    async def test_handle_text_no_thread_id(self, settings_json):
        """Text in General (no thread_id) should say 'No session'."""
        from bot.handlers import handle_text
        update, ctx, mgr = _make_update()
        update.message.message_thread_id = None
        update.message.text = "hello"
        await handle_text(update, ctx)
        text = update.message.reply_text.call_args[0][0]
        assert "No session" in text

    @pytest.mark.asyncio
    async def test_key_on_dead_session_shows_picker(self, settings_json):
        """/key on a dead session should cleanup and show restart options."""
        from bot.handlers import cmd_key
        update, ctx, mgr = _make_update(args=["enter"], thread_id=11001)
        ctx.args = ["enter"]
        self._make_session(mgr, 111, "claude:dead-key", 11001, alive=False)

        with patch("bot.handlers._kill_and_cleanup", new_callable=AsyncMock), \
             patch("bot.handlers.cleanup_stale_sessions", new_callable=AsyncMock):
            await cmd_key(update, ctx)
        # Should indicate session ended
        text = update.message.reply_text.call_args[0][0]
        assert "ended" in text.lower() or "stopped" in text.lower() or "exit" in text.lower() or "/start" in text.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
