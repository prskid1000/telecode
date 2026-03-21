"""Async PTY process — cross-platform (Unix openpty / Windows ConPTY via pywinpty).

Uses pyte virtual terminal for proper screen rendering.  Output diffing uses
two layers:

  1. **History** (scrolled-off content) is always stable — we simply track
     how many lines we've already emitted.
  2. **Display** (visible screen) is diffed line-by-line with
     `difflib.SequenceMatcher`.  'insert' = new lines → emit.
     'replace' = modified lines → emit only if NOT similar to the old line
     (similarity is measured by stripping non-alphanumeric chars and comparing
     with SequenceMatcher ratio, so spinner/timer changes are ignored).
     'equal' and 'delete' → skip.

This avoids the old character-level prefix-match problem where a single
spinner-character change caused the entire screen to be re-sent.
"""
import asyncio
import difflib
import logging
import os
import sys
from typing import Callable

import pyte

_IS_WIN = sys.platform == "win32"
_COLS, _ROWS = 200, 300
_HISTORY = 5000       # lines of scrollback history to keep
_IDLE_SEC = 2.0       # seconds of silence after last data → streaming is done, send
_MAX_WAIT_SEC = 5.0   # max seconds to buffer before forcing an intermediate send

log = logging.getLogger("telecode.process")

_BOX_DRAW_RANGES = (
    range(0x2500, 0x2580),  # box drawing
    range(0x2580, 0x25A0),  # block elements
)


def _is_decoration(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    return all(
        any(ord(c) in r for r in _BOX_DRAW_RANGES) or c in " \t"
        for c in stripped
    )


def _history_lines(screen: pyte.HistoryScreen) -> list[str]:
    """Extract scrolled-off history lines (stable — never re-rendered by TUI)."""
    lines: list[str] = []
    if hasattr(screen, "history") and screen.history.top:
        for hist_line in screen.history.top:
            text = "".join(
                hist_line[col].data for col in sorted(hist_line)
            ).rstrip()
            if text and not _is_decoration(text):
                lines.append(text)
    return lines


def _display_lines(screen: pyte.HistoryScreen) -> list[str]:
    """Extract current visible-screen lines (may contain ephemeral TUI chrome)."""
    lines: list[str] = []
    for line in screen.display:
        clean = line.rstrip()
        if clean and not _is_decoration(clean):
            lines.append(clean)
    return lines


def _similar(a: str, b: str) -> bool:
    """Two lines are 'the same' if their alphanumeric content mostly matches.

    Strips ALL non-alphanumeric characters before comparing, so spinner
    changes (· → * → ✶), arrows, bullets, timer updates etc. are ignored.
    Pure algorithmic — no hard-coded patterns.
    """
    a_alnum = "".join(c for c in a if c.isalnum() or c == " ").strip()
    b_alnum = "".join(c for c in b if c.isalnum() or c == " ").strip()
    if a_alnum == b_alnum:
        return True
    if not a_alnum or not b_alnum:
        return not a_alnum and not b_alnum
    return difflib.SequenceMatcher(None, a_alnum, b_alnum).ratio() > 0.7


def _extract_new_lines(prev: list[str], curr: list[str]) -> list[str]:
    """Line-level diff — return only genuinely new lines in *curr* vs *prev*.

    Uses SequenceMatcher to align the two line-lists, then:
      'insert'  — brand-new lines               → always emit
      'replace' — emit only lines NOT similar to any old line they replaced
      'equal'   — unchanged                      → skip
      'delete'  — removed from screen            → skip
    """
    if not prev:
        return list(curr)
    if not curr:
        return []
    sm = difflib.SequenceMatcher(None, prev, curr, autojunk=False)
    new: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            new.extend(curr[j1:j2])
        elif tag == "replace":
            old_chunk = prev[i1:i2]
            for line in curr[j1:j2]:
                if not any(_similar(line, old) for old in old_chunk):
                    new.append(line)
    return new


class PTYProcess:
    def __init__(self, cmd: list[str], cwd: str, extra_env: dict[str, str] | None = None):
        self.cmd       = cmd
        self.cwd       = cwd
        self.extra_env = extra_env or {}
        self._subscribers: list[Callable[[str], None]] = []
        self.alive: bool = False

        self._screen = pyte.HistoryScreen(_COLS, _ROWS, _HISTORY)
        self._stream = pyte.Stream(self._screen)
        self._last_history_len = 0
        self._last_display: list[str] = []
        self._idle_handle: asyncio.TimerHandle | None = None
        self._max_wait_handle: asyncio.TimerHandle | None = None
        self._streaming = False

        self._pty = None
        self._master_fd: int | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reader_task: asyncio.Task | None = None
        self._poll_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        env = os.environ.copy()
        env.update({k: v for k, v in self.extra_env.items() if v})

        if _IS_WIN:
            await self._start_win(env)
        else:
            await self._start_unix(env)

        self.alive = True
        self._poll_task = asyncio.ensure_future(self._poll_loop())
        asyncio.ensure_future(self._watch_exit())

    async def stop(self) -> None:
        self.alive = False
        if self._idle_handle:
            self._idle_handle.cancel()
        if self._max_wait_handle:
            self._max_wait_handle.cancel()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        if _IS_WIN:
            await self._stop_win()
        else:
            await self._stop_unix()

    async def interrupt(self) -> None:
        if _IS_WIN:
            if self._pty and self._pty.isalive():
                try:
                    self._pty.sendcontrol("c")
                except Exception:
                    pass
        else:
            import signal
            if self._process and self._process.returncode is None:
                try:
                    self._process.send_signal(signal.SIGINT)
                except ProcessLookupError:
                    pass

    async def send(self, text: str) -> None:
        if not self.alive:
            raise RuntimeError("Process is not running")
        await self.send_raw(text + "\r")

    async def send_raw(self, data: str) -> None:
        """Write raw data to the PTY without appending a newline."""
        if not self.alive:
            raise RuntimeError("Process is not running")
        if _IS_WIN:
            await self._loop.run_in_executor(None, self._pty.write, data)
        else:
            await self._loop.run_in_executor(
                None, os.write, self._master_fd, data.encode()
            )

    def subscribe(self, cb: Callable[[str], None]) -> None:
        if cb not in self._subscribers:
            self._subscribers.append(cb)

    def unsubscribe(self, cb: Callable[[str], None]) -> None:
        self._subscribers = [s for s in self._subscribers if s is not cb]

    # ── Output processing ─────────────────────────────────────────────────────

    def _process_raw(self, raw: str) -> None:
        """Feed raw PTY data into pyte and manage streaming timers."""
        self._stream.feed(raw)

        if not self._loop:
            return

        # Reset idle timer — fires _IDLE_SEC after the LAST data chunk
        if self._idle_handle:
            self._idle_handle.cancel()
        self._idle_handle = self._loop.call_later(_IDLE_SEC, self._on_idle)

        # Start max-wait timer on first data of a new streaming burst
        if not self._streaming:
            self._streaming = True
            self._max_wait_handle = self._loop.call_later(
                _MAX_WAIT_SEC, self._on_max_wait
            )
            log.info("Streaming started")

    def _on_idle(self) -> None:
        """No new data for _IDLE_SEC — streaming is done. Send final output."""
        log.info("Streaming idle — flushing output")
        self._flush_and_reset()

    def _on_max_wait(self) -> None:
        """Long response — force an intermediate send so user isn't left waiting."""
        if self._streaming:
            log.info("Max wait reached — intermediate flush")
            self._do_snapshot()
            # Schedule next max-wait interval
            if self._loop and self._streaming:
                self._max_wait_handle = self._loop.call_later(
                    _MAX_WAIT_SEC, self._on_max_wait
                )

    def _flush_and_reset(self) -> None:
        """Snapshot, send new content, and reset streaming state."""
        if self._max_wait_handle:
            self._max_wait_handle.cancel()
            self._max_wait_handle = None
        self._streaming = False
        self._do_snapshot()

    def _do_snapshot(self) -> None:
        """Two-layer snapshot: history (stable) + display (diffed line-by-line)."""
        # Layer 1 — history: content scrolled off screen is always stable
        history = _history_lines(self._screen)
        new_history = history[self._last_history_len:]
        self._last_history_len = len(history)

        # Layer 2 — display: line-level diff with similarity matching
        display = _display_lines(self._screen)
        new_display = _extract_new_lines(self._last_display, display)
        self._last_display = display

        all_new = new_history + new_display
        if not all_new:
            return

        text = "\n".join(all_new).strip()
        if not text:
            return

        log.info("Sending output (%d chars): %.300s", len(text), text)
        for cb in self._subscribers:
            cb(text)

    # ── Windows (ConPTY via pywinpty) ─────────────────────────────────────────

    async def _start_win(self, env: dict) -> None:
        from winpty import PtyProcess as WinPty
        env["TERM"] = "xterm-256color"
        self._pty = await self._loop.run_in_executor(
            None,
            lambda: WinPty.spawn(self.cmd, cwd=self.cwd, env=env, dimensions=(_ROWS, _COLS)),
        )
        self._reader_task = asyncio.ensure_future(self._win_reader())

    async def _win_reader(self) -> None:
        loop = self._loop
        while self.alive:
            try:
                raw = await loop.run_in_executor(None, self._pty.read, 4096)
            except EOFError:
                log.info("PTY reader: EOF")
                break
            except Exception as e:
                log.warning("PTY reader error: %s", e)
                break
            if raw:
                try:
                    self._process_raw(raw)
                except Exception as e:
                    log.error("_process_raw failed: %s", e, exc_info=True)

    async def _stop_win(self) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._pty:
            try:
                self._pty.close(force=True)
            except Exception:
                pass

    # ── Unix (openpty) ────────────────────────────────────────────────────────

    async def _start_unix(self, env: dict) -> None:
        master_fd, slave_fd = os.openpty()
        self._master_fd = master_fd
        env["TERM"] = "xterm-256color"
        self._process = await asyncio.create_subprocess_exec(
            *self.cmd,
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            cwd=self.cwd, env=env, close_fds=True,
        )
        os.close(slave_fd)
        self._loop.add_reader(master_fd, self._on_readable)

    def _on_readable(self) -> None:
        try:
            raw = os.read(self._master_fd, 4096)
            if raw:
                self._process_raw(raw.decode("utf-8", errors="replace"))
        except OSError:
            self.alive = False
            self._remove_reader()

    async def _stop_unix(self) -> None:
        import signal
        self._remove_reader()
        if self._process and self._process.returncode is None:
            try:
                self._process.send_signal(signal.SIGTERM)
                await asyncio.wait_for(self._process.wait(), timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
        self._close_master()

    def _remove_reader(self) -> None:
        if self._loop and self._master_fd is not None:
            try:
                self._loop.remove_reader(self._master_fd)
            except Exception:
                pass

    def _close_master(self) -> None:
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None

    # ── Shared ────────────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Safety net: periodically snapshot the screen even if timers didn't fire."""
        try:
            while self.alive:
                await asyncio.sleep(5.0)
                if self.alive:
                    self._do_snapshot()
        except asyncio.CancelledError:
            return

    async def _watch_exit(self) -> None:
        if _IS_WIN:
            while self.alive and self._pty and self._pty.isalive():
                await asyncio.sleep(0.5)
        else:
            if self._process:
                await self._process.wait()
        self.alive = False
        self._flush_and_reset()
        for cb in self._subscribers:
            cb("[Process exited]")
