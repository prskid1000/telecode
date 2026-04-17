"""Live Telegram message and frame sender used by the bot handlers.

Extracted from ``bot/handlers.py`` so the delivery pipeline (text streaming,
photo streaming, flood backoff, overlap detection) can evolve independently
of the command/routing layer.

Responsibilities:

* ``LiveMessage`` — one Telegram text message per "turn", edited in place
  as PTY output accumulates. Debounced edits, HTML-aware splitting, and a
  one-shot finalize retry so transient API errors don't leave users staring
  at a truncated reply.
* ``FrameSender`` — sends each JPEG frame as a new photo in a topic; inline
  controls are optionally re-tracked via a caller-supplied callback so this
  module has no dependency on ``handlers`` internals.
* ``TypingPinger`` — keeps ``send_chat_action("typing")`` alive while a turn
  is in flight but no reply message exists yet.

Cross-module hooks:

* ``init_live_refs(live_messages, frame_senders)`` in ``bot/rate.py`` is fed
  the dicts owned here so topic-gone detection can clear entries.
"""
from __future__ import annotations

import asyncio
import io
import logging
import time
from html import escape as _esc
from typing import Awaitable, Callable

from telegram import InlineKeyboardMarkup
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError

import config
from bot.rate import handle_topic_gone, is_thread_not_found

log = logging.getLogger("telecode.live")

# ── Fire-and-forget helper ────────────────────────────────────────────────────


def fire(coro: Awaitable) -> asyncio.Task:
    """Schedule a coroutine and log any unhandled exception.

    ``asyncio.ensure_future`` alone would swallow exceptions into GC warnings;
    this attaches a done-callback so failures land in the app log.
    """
    task = asyncio.ensure_future(coro)

    def _log_exc(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            log.error("Background task failed: %s", exc, exc_info=exc)

    task.add_done_callback(_log_exc)
    return task


# ── Flood-control backoff (per-chat) ──────────────────────────────────────────
# Telegram rate limits apply per chat, so we track backoff per chat_id rather
# than globally — a flood in one group should not throttle edits in another.

_flood_until: dict[int, float] = {}


def set_flood_backoff(chat_id: int, retry_after: float) -> None:
    _flood_until[chat_id] = time.monotonic() + retry_after + 1  # +1s safety


def flood_active(chat_id: int) -> bool:
    deadline = _flood_until.get(chat_id, 0.0)
    return time.monotonic() < deadline


# ── HTML-escape-aware length and splitting ────────────────────────────────────

_TG_HARD_LIMIT = 4096  # Telegram's absolute message length (post-HTML)


def max_tg_len() -> int:
    """Effective max length from config, capped at Telegram's hard limit."""
    try:
        return min(config.max_msg_length(), _TG_HARD_LIMIT)
    except Exception:
        return 3800


def _escape_expansion(ch: str) -> int:
    """Extra chars ``html.escape`` adds for a single character (0 for most)."""
    if ch == "&":
        return 4  # &amp;
    if ch == "<" or ch == ">":
        return 3  # &lt; / &gt;
    return 0


def escaped_len(text: str) -> int:
    """Approximate length after HTML escaping (``&`` / ``<`` / ``>`` expand).

    Telegram counts the *escaped* HTML text against its 4096-char limit, so we
    must account for entity expansion when deciding where to split.
    """
    return len(text) + text.count("&") * 4 + text.count("<") * 3 + text.count(">") * 3


def _escape_prefix_sums(text: str) -> list[int]:
    """Cumulative escaped length of ``text[:i]``; ``prefix[0] == 0``.

    Enables ``O(1)`` lookup of ``escaped_len(text[:k])`` for any ``k`` and
    therefore ``O(log n)`` binary-search in :func:`safe_split` (replaces the
    old quadratic step-back loop).
    """
    prefix: list[int] = [0] * (len(text) + 1)
    total = 0
    for i, ch in enumerate(text):
        total += 1 + _escape_expansion(ch)
        prefix[i + 1] = total
    return prefix


def safe_split(text: str, limit: int, last_sent: str) -> int:
    """Find an index so ``text[:index]`` fits within ``limit`` *after escaping*.

    Prefers splitting on a newline to avoid cutting mid-line. If ``last_sent``
    is non-empty, we split exactly at its boundary so the already-delivered
    head stays and the remainder overflows into a fresh message.
    """
    if last_sent:
        return len(last_sent)
    if not text:
        return 0

    prefix = _escape_prefix_sums(text)
    # Binary-search the rightmost ``k`` with ``prefix[k] <= limit``.
    lo, hi = 1, len(text)
    best = 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if prefix[mid] <= limit:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    # Prefer a newline boundary inside the fitting window.
    nl = text.rfind("\n", 0, best)
    if nl > 0:
        return nl + 1
    return best


# ── Overlap detection (Z-algorithm, whitespace-insensitive) ───────────────────

_MIN_OVERLAP = 8  # minimum non-ws chars to count as genuine overlap


def _z_array(s: str) -> list[int]:
    """Classic Z-algorithm: ``z[i]`` = longest substring starting at ``i`` that
    matches a prefix of ``s``. ``O(len(s))`` time and space.
    """
    n = len(s)
    z = [0] * n
    if n == 0:
        return z
    z[0] = n
    l = r = 0
    for i in range(1, n):
        if i < r:
            z[i] = min(r - i, z[i - l])
        while i + z[i] < n and s[z[i]] == s[i + z[i]]:
            z[i] += 1
        if i + z[i] > r:
            l, r = i, i + z[i]
    return z


def find_overlap_end(existing: str, new: str) -> int:
    """Return the index into ``new`` where genuinely-new content begins.

    Whitespace-insensitive: both strings are compared on their non-whitespace
    projections (so TUI padding/reflow doesn't block an overlap match). Uses
    Z-algorithm on ``new_chars + '\\x01' + existing_tail`` so the total cost is
    linear in the combined length.
    """
    if not existing or not new:
        return 0

    # Only look at the tail of existing that could plausibly overlap.
    tail = existing[-(len(new) * 3) :] if len(existing) > len(new) * 3 else existing
    ex_chars = [c for c in tail if not c.isspace()]
    nw = [(i, c) for i, c in enumerate(new) if not c.isspace()]

    if not ex_chars or not nw:
        return 0

    ex_proj = "".join(ex_chars)
    nw_proj = "".join(c for _, c in nw)

    # Sentinel guaranteed not to appear in either projection.
    combined = nw_proj + "\x01" + ex_proj
    z = _z_array(combined)
    off = len(nw_proj) + 1  # index where ex_proj starts inside combined

    # Walk ex_proj start positions; the earliest full suffix match wins.
    len_ex = len(ex_proj)
    min_start = max(0, len_ex - len(nw_proj))
    for start in range(min_start, len_ex - _MIN_OVERLAP + 1):
        remaining = len_ex - start
        if z[off + start] >= remaining and remaining >= _MIN_OVERLAP:
            # We matched the last ``remaining`` chars of ex against the first
            # ``remaining`` chars of nw → skip that many tokens in new.
            return nw[remaining - 1][0] + 1
    return 0


# ── Live text message ─────────────────────────────────────────────────────────

_EDIT_DEBOUNCE = 1.0      # min seconds between edits on a live stream
_FINAL_RETRY_SEC = 2.0    # backoff before retrying a failed final flush


class LiveMessage:
    """One bot message that keeps getting edited as output streams in.

    The first chunk of a turn is delivered eagerly (no 1s debounce wait) so
    the user sees progress immediately; subsequent chunks are coalesced at
    ``_EDIT_DEBOUNCE`` cadence to respect Telegram's per-chat edit rate.
    """

    def __init__(self, bot, chat_id: int, thread_id: int):
        self.bot = bot
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.msg_id: int | None = None
        self.full_text = ""
        self._last_sent = ""
        self._edit_scheduled = False
        self._edit_handle: asyncio.TimerHandle | None = None
        self._final_retry_done = False
        self._loop = asyncio.get_event_loop()
        self._typing: "TypingPinger | None" = TypingPinger(bot, chat_id, thread_id)
        self._typing.start()

    async def _ensure_msg(self) -> None:
        """Create the placeholder message if it doesn't exist yet.

        Does *not* short-circuit on flood: the send will either succeed or
        surface ``RetryAfter``, in which case we record the backoff and let
        the next ``append`` try again. Silently skipping here would strand
        turns that produced only a single short chunk.
        """
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
            self._stop_typing()
        except RetryAfter as e:
            set_flood_backoff(self.chat_id, e.retry_after)
            log.warning("LiveMessage flood control — backing off %ds", e.retry_after)
        except BadRequest as e:
            if is_thread_not_found(e):
                fire(handle_topic_gone(self.thread_id))
                self._stop_typing()
                return
            log.warning("LiveMessage: failed to create message: %s", e)
        except TelegramError as e:
            log.warning("LiveMessage: failed to create message: %s", e)

    def append(self, text: str) -> None:
        """Append new output, trimming any overlap with what we already have."""
        skip = find_overlap_end(self.full_text, text)
        trimmed = text[skip:] if skip > 0 else text
        if not trimmed.strip():
            return
        self.full_text += trimmed + "\n"
        if self._edit_scheduled:
            return
        self._edit_scheduled = True
        # First chunk of the turn goes out right away so the user sees life;
        # subsequent chunks wait for the debounce interval.
        delay = 0.0 if not self._last_sent and self.msg_id is None else _EDIT_DEBOUNCE
        if delay <= 0.0:
            fire(self._do_edit())
        else:
            self._edit_handle = self._loop.call_later(
                delay, lambda: fire(self._do_edit())
            )

    async def _do_edit(self) -> None:
        """Edit the message with current accumulated text, splitting as needed."""
        self._edit_scheduled = False
        await self._ensure_msg()

        display = self.full_text.strip()
        if not display or display == self._last_sent:
            return

        limit = max_tg_len()

        # Keep splitting until the remaining display fits. The old fallback
        # ``_truncate_to_fit`` dropped the head; now we always open a new
        # overflow message so nothing is lost.
        while escaped_len(display) > limit:
            split_at = safe_split(display, limit, self._last_sent)
            head = display[:split_at].strip()
            await self._edit_to(self._last_sent or head)

            overflow = display[split_at:].strip()
            self.full_text = overflow + "\n"
            self.msg_id = None
            self._last_sent = ""
            await self._ensure_msg()
            display = overflow
            if not display:
                return

        await self._edit_to(display)

    async def _edit_to(self, text: str) -> None:
        """Perform the actual ``editMessageText`` API call."""
        if not self.msg_id or not text.strip():
            return
        if text == self._last_sent:
            return
        if flood_active(self.chat_id):
            return  # will catch up on the next edit
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.msg_id,
                text=f"<pre>{_esc(text)}</pre>",
                parse_mode=ParseMode.HTML,
            )
            self._last_sent = text
        except RetryAfter as e:
            set_flood_backoff(self.chat_id, e.retry_after)
            log.warning("LiveMessage flood control — backing off %ds", e.retry_after)
        except BadRequest as e:
            if is_thread_not_found(e):
                fire(handle_topic_gone(self.thread_id))
                return
            if "not modified" not in str(e).lower():
                log.warning("LiveMessage edit failed: %s", e)
        except TelegramError as e:
            log.warning("LiveMessage edit failed: %s", e)

    async def finalize(self) -> None:
        """Final edit — flush everything left. Schedules one retry on failure
        so transient Telegram hiccups don't leave the user with a truncated
        reply forever."""
        if self._edit_handle:
            self._edit_handle.cancel()
            self._edit_handle = None
        self._edit_scheduled = False
        await self._do_edit()
        self._stop_typing()

        pending = self.full_text.strip()
        if pending and pending != self._last_sent and not self._final_retry_done:
            self._final_retry_done = True
            self._loop.call_later(_FINAL_RETRY_SEC, lambda: fire(self._do_edit()))

    def _stop_typing(self) -> None:
        if self._typing is not None:
            self._typing.stop()
            self._typing = None


# ── Typing indicator ──────────────────────────────────────────────────────────

_TYPING_REPING_SEC = 4.0      # Telegram's typing action lasts ~5s; re-ping below
_TYPING_MAX_DURATION = 60.0   # hard cap so a silent PTY can't leak pings forever


class TypingPinger:
    """Keeps ``sendChatAction("typing")`` alive for a thread until ``stop()``.

    Used to close the perceived-latency gap between the user sending a message
    and the bot's first reply appearing (which waits for the PTY idle/max-wait
    flush). The indicator auto-expires after ~5s so we re-ping every 4s, and
    self-cancels after ``_TYPING_MAX_DURATION`` to avoid leaking the ping loop
    when a turn produces no output at all.
    """

    def __init__(self, bot, chat_id: int, thread_id: int | None):
        self.bot = bot
        self.chat_id = chat_id
        self.thread_id = thread_id
        self._task: asyncio.Task | None = None
        self._stopped = False

    def start(self) -> None:
        if self._task is not None or self._stopped:
            return
        self._task = asyncio.ensure_future(self._run())

    def stop(self) -> None:
        self._stopped = True
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run(self) -> None:
        started = time.monotonic()
        try:
            while not self._stopped:
                if time.monotonic() - started > _TYPING_MAX_DURATION:
                    return
                try:
                    await self.bot.send_chat_action(
                        chat_id=self.chat_id,
                        message_thread_id=self.thread_id,
                        action=ChatAction.TYPING,
                    )
                except BadRequest as e:
                    if is_thread_not_found(e):
                        return
                except TelegramError:
                    pass
                await asyncio.sleep(_TYPING_REPING_SEC)
        except asyncio.CancelledError:
            return


# ── Shared dicts (one LiveMessage / FrameSender per thread_id) ────────────────

live_messages: dict[int, LiveMessage] = {}
frame_senders: dict[int, "FrameSender"] = {}


async def send_output(bot, chat_id: int, thread_id: int, text: str) -> None:
    """PTY output callback entry point — append to the live message."""
    lm = live_messages.get(thread_id)
    if not lm:
        lm = LiveMessage(bot, chat_id, thread_id)
        live_messages[thread_id] = lm
    lm.append(text)


async def cleanup_live_message(thread_id: int) -> None:
    """Finalize and drop the live message for a stopped session."""
    lm = live_messages.pop(thread_id, None)
    if lm:
        await lm.finalize()


# ── Frame sender (photo streaming) ────────────────────────────────────────────


class FrameSender:
    """Sends each JPEG frame as a new photo message in a topic.

    ``track_controls`` is an optional callback that the caller (handlers) uses
    to keep inline keyboards only on the most recent message in a thread.
    Kept as a parameter so this module has no import cycle back to handlers.
    """

    def __init__(
        self,
        bot,
        chat_id: int,
        thread_id: int,
        session_key: str,
        controls_kb_factory: Callable[[str, bool], InlineKeyboardMarkup] | None = None,
        track_controls: Callable[..., Awaitable[None]] | None = None,
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.session_key = session_key
        # Duck-typed: set after session starts, reads ``paused`` and ``alive``.
        self.process: object | None = None
        self._pending_frame: bytes | None = None
        self._send_scheduled = False
        self._send_handle: asyncio.TimerHandle | None = None
        self._loop = asyncio.get_event_loop()
        self._controls_kb = controls_kb_factory
        self._track_controls = track_controls

    def set_frame(self, jpeg_bytes: bytes) -> None:
        """Buffer the latest frame; schedule a send if not already pending."""
        if self.process and getattr(self.process, "paused", False):
            return
        self._pending_frame = jpeg_bytes
        if self._send_scheduled:
            return
        self._send_scheduled = True
        self._send_handle = self._loop.call_later(
            config.image_interval(), lambda: fire(self._do_send())
        )

    async def _do_send(self) -> None:
        self._send_scheduled = False
        if self.process and getattr(self.process, "paused", False):
            self._pending_frame = None
            return
        frame = self._pending_frame
        if not frame:
            return
        self._pending_frame = None

        if flood_active(self.chat_id):
            return
        try:
            photo_buf = io.BytesIO(frame)
            photo_buf.name = "frame.jpg"
            kb = None
            if self._controls_kb and self.process and getattr(self.process, "alive", False):
                kb = self._controls_kb(self.session_key, False)
            msg = await self.bot.send_photo(
                chat_id=self.chat_id,
                message_thread_id=self.thread_id,
                photo=photo_buf,
                reply_markup=kb,
            )
            if kb is not None and self._track_controls is not None:
                await self._track_controls(self.bot, msg)
        except RetryAfter as e:
            set_flood_backoff(self.chat_id, e.retry_after)
            log.warning("FrameSender flood control — backing off %ds", e.retry_after)
        except BadRequest as e:
            if is_thread_not_found(e):
                fire(handle_topic_gone(self.thread_id))
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
