"""Async PTY process — cross-platform (Unix openpty / Windows ConPTY via pywinpty).

Uses pyte virtual terminal for proper screen rendering.  Each snapshot is one
ordered list: **history + display** (same idea as tmux ``capture-pane -S -`` /
WezTerm ``get_lines_as_text`` — one scrollback document, not two independent
streams).

Rendering pipeline (**hybrid diff**, informed by patience / histogram / Heckel
ideas and terminal scrollback-as-document practice — see ``docs/terminal-rendering-research.md``):

1. **Unified snapshot** — one list ``history + display`` (tmux / WezTerm model).
2. **Patience anchors** — keys unique on *both* sides (Bram Cohen); LIS on right
   indices = non-crossing backbone (Git ``--patience``).
3. **Histogram LIS** — if patience yields ``< 2`` anchors, collect ``(i, j)``
   pairs whose key is *rare* (low ``occ(prev)+occ(curr)``), capped per line;
   sort by ``(i, -j)`` then **LIS on ``j``** so at most one anchor per ``i`` and
   no crossing matches.
4. **Histogram greedy** — last resort if LIS still finds ``< 2`` anchors.
5. **Recursive refinement** — split at anchors, recurse into each segment (max
   depth) so locally-unique lines inside a hunk also anchor (local patience).
6. **Segment diff** — ``difflib.SequenceMatcher`` on normalized keys (Myers-class
   LCS used inside CPython’s matcher); emit originals; ``_similar`` skips TUI-only
   replaces.

AST diff (GumTree) / OT / CRDT are **not** used here — they apply to structured
documents, not raw terminal lines.  Rabin–Karp / bsdiff are optional future
optimizations for speed or binary transcripts.

For Claude Code, best fidelity remains **``--print`` / ``stream-json``** (no TUI).
"""
import asyncio
import bisect
import difflib
import logging
import os
import sys
import unicodedata
from collections import Counter, defaultdict
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
_BOX_DRAW_CHARS = frozenset(
    chr(c) for rng in _BOX_DRAW_RANGES for c in rng
) | frozenset(" \t")


def _is_decoration(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    return all(c in _BOX_DRAW_CHARS for c in stripped)


def _history_lines(screen: pyte.HistoryScreen) -> list[str]:
    """Extract scrolled-off history lines (stable — never re-rendered by TUI)."""
    lines: list[str] = []
    if hasattr(screen, "history") and screen.history.top:
        cols = screen.columns
        for hist_line in screen.history.top:
            text = "".join(
                hist_line[col].data for col in range(cols) if col in hist_line
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


def _full_snapshot_lines(screen: pyte.HistoryScreen) -> list[str]:
    """Ordered terminal content: scrollback first, then visible screen (no gap)."""
    return _history_lines(screen) + _display_lines(screen)


def _norm_key(line: str) -> str:
    """Stable key for *alignment only* (WezTerm-style plain-text extraction idea).

    NFKC normalizes compatibility glyphs (full-width digits, ligatures).
    Whitespace collapse avoids spurious diffs when the TUI reflows padding.
    Original strings are still emitted to Telegram.
    """
    s = unicodedata.normalize("NFKC", line)
    return " ".join(s.split())


def _lis_indices(seq: list[int]) -> list[int]:
    """Indices into *seq* forming one longest strictly increasing subsequence."""
    if not seq:
        return []
    tails: list[int] = []
    tail_at: list[int] = []  # index in seq for value at tails[k]
    prev: list[int] = [-1] * len(seq)
    for idx, x in enumerate(seq):
        pos = bisect.bisect_left(tails, x)
        if pos == len(tails):
            tails.append(x)
            tail_at.append(idx)
        else:
            tails[pos] = x
            tail_at[pos] = idx
        prev[idx] = tail_at[pos - 1] if pos > 0 else -1
    out: list[int] = []
    cur = tail_at[-1]
    while cur >= 0:
        out.append(cur)
        cur = prev[cur]
    out.reverse()
    return out


def _patience_anchor_indices(
    prev_k: list[str], curr_k: list[str],
    pc: Counter, cc: Counter,
) -> list[tuple[int, int]]:
    """Pairs of indices that patience-diff would lock (unique matching lines).

    Classic patience diff (Bram Cohen): lines that appear exactly once on each
    side are anchor candidates; LIS on right-hand indices keeps monotonic order
    so SequenceMatcher does not "cross" stable lines in repetitive TUIs.
    """
    if not prev_k or not curr_k:
        return []
    curr_once = {k: j for j, k in enumerate(curr_k) if cc[k] == 1}
    pairs: list[tuple[int, int]] = [
        (i, curr_once[k])
        for i, k in enumerate(prev_k)
        if pc[k] == 1 and k in curr_once
    ]
    pairs.sort(key=lambda t: t[0])
    if len(pairs) <= 1:
        return pairs
    js = [p[1] for p in pairs]
    keep = _lis_indices(js)
    return [pairs[k] for k in keep]


# Recursive split: local patience inside large hunks (terminal bursts are bursty).
_MAX_ANCHOR_DEPTH = 4
_MIN_RECURSE_TOTAL = 96  # prev+curr line count; below this, flat segment diff only

# Histogram LIS: rare keys only; cap work — terminal snapshots can be huge.
_HIST_LIS_MAX_OCC_SUM = 18
_HIST_LIS_MAX_PAIRS = 40_000
_HIST_LIS_MAX_J_PER_I = 12


def _histogram_lis_anchors(
    prev_k: list[str],
    curr_k: list[str],
    pc: Counter,
    cc: Counter,
    *,
    max_occ_sum: int = _HIST_LIS_MAX_OCC_SUM,
    max_pairs: int = _HIST_LIS_MAX_PAIRS,
    max_j_per_i: int = _HIST_LIS_MAX_J_PER_I,
) -> list[tuple[int, int]]:
    """Rare-key (i, j) pairs, then LIS on j with sort (i, -j).

    Same *i* can match several *j* (duplicate lines on the right).  Sorting by
    ``i`` ascending and ``j`` descending groups those; LIS on the ``j``
    sequence then keeps **at most one** pair per ``i`` and enforces a non-crossing
    increasing match in ``j``.
    """
    if not prev_k or not curr_k:
        return []
    curr_by_k: dict[str, list[int]] = defaultdict(list)
    for j, k in enumerate(curr_k):
        curr_by_k[k].append(j)

    pairs: list[tuple[int, int]] = []
    for i, k in enumerate(prev_k):
        if k not in curr_by_k:
            continue
        if pc[k] + cc[k] > max_occ_sum:
            continue
        for j in curr_by_k[k][:max_j_per_i]:
            pairs.append((i, j))
            if len(pairs) >= max_pairs:
                break
        if len(pairs) >= max_pairs:
            break

    if len(pairs) < 2:
        return []

    pairs.sort(key=lambda t: (t[0], -t[1]))
    js = [p[1] for p in pairs]
    keep = _lis_indices(js)
    return [pairs[idx] for idx in keep]


def _histogram_greedy_anchors(
    prev_k: list[str], curr_k: list[str],
    pc: Counter, cc: Counter,
    max_occ_sum: int = 14,
) -> list[tuple[int, int]]:
    """Low-frequency keys first, then forward monotone (i, j) pairs.

    Mirrors the *idea* of histogram diff (prefer matches on rare lines) without
    a full JGit port.  When patience finds no unique lines globally, rare keys
    still cut the diff into smaller pieces.
    """
    if not prev_k or not curr_k:
        return []
    common = set(prev_k) & set(curr_k)
    if not common:
        return []
    keys_ranked = sorted(common, key=lambda k: (pc[k] + cc[k], k))
    anchors: list[tuple[int, int]] = []
    last_i, last_j = -1, -1
    for k in keys_ranked:
        if pc[k] + cc[k] > max_occ_sum:
            break
        i_found = None
        for i in range(last_i + 1, len(prev_k)):
            if prev_k[i] == k:
                i_found = i
                break
        if i_found is None:
            continue
        j_found = None
        for j in range(last_j + 1, len(curr_k)):
            if curr_k[j] == k:
                j_found = j
                break
        if j_found is None:
            continue
        anchors.append((i_found, j_found))
        last_i, last_j = i_found, j_found
    return anchors


def _choose_anchors(prev_k: list[str], curr_k: list[str]) -> list[tuple[int, int]]:
    """Patience → histogram LIS on rare pairs → histogram greedy."""
    pc, cc = Counter(prev_k), Counter(curr_k)
    pa = _patience_anchor_indices(prev_k, curr_k, pc, cc)
    if len(pa) >= 2:
        return pa
    hl = _histogram_lis_anchors(prev_k, curr_k, pc, cc)
    if len(hl) >= 2:
        return hl
    hg = _histogram_greedy_anchors(prev_k, curr_k, pc, cc)
    if len(hg) >= 2:
        return hg
    # Caller ignores < 2 anchors; no useful result to return
    return []


def _alnum_strip(s: str) -> str:
    return "".join(c for c in s if c.isalnum() or c == " ").strip()


def _similar(a: str, b: str) -> bool:
    """Two lines are 'the same' if their alphanumeric content mostly matches.

    Strips ALL non-alphanumeric characters before comparing, so spinner
    changes, arrows, bullets, timer updates etc. are ignored.
    """
    a_alnum = _alnum_strip(a)
    b_alnum = _alnum_strip(b)
    if a_alnum == b_alnum:
        return True
    if not a_alnum or not b_alnum:
        return not a_alnum and not b_alnum
    # Fast reject: length ratio alone rules out 0.7 similarity
    la, lb = len(a_alnum), len(b_alnum)
    if la > 2.5 * lb or lb > 2.5 * la:
        return False
    return difflib.SequenceMatcher(None, a_alnum, b_alnum).ratio() > 0.7


def _diff_segment(
    prev: list[str], curr: list[str], prev_k: list[str], curr_k: list[str]
) -> list[str]:
    """SequenceMatcher on keys (Myers-class LCS via CPython); emit original lines."""
    if not prev:
        return list(curr)
    if not curr:
        return []
    sm = difflib.SequenceMatcher(None, prev_k, curr_k, autojunk=False)
    new: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            new.extend(curr[j1:j2])
        elif tag == "replace":
            old_chunk = prev[i1:i2]
            # Pre-compute alnum set so exact matches skip expensive ratio()
            old_alnum = {_alnum_strip(o) for o in old_chunk}
            for line in curr[j1:j2]:
                stripped = _alnum_strip(line)
                if stripped in old_alnum:
                    continue
                if not any(_similar(line, old) for old in old_chunk):
                    new.append(line)
    return new


def _extract_new_lines_impl(
    prev: list[str],
    curr: list[str],
    prev_k: list[str],
    curr_k: list[str],
    depth: int,
) -> list[str]:
    """Patience / histogram anchors + recurse + flat diff on small segments."""
    if not prev:
        return list(curr)
    if not curr:
        return []
    total = len(prev) + len(curr)
    if depth >= _MAX_ANCHOR_DEPTH or total < _MIN_RECURSE_TOTAL:
        return _diff_segment(prev, curr, prev_k, curr_k)

    anchors = _choose_anchors(prev_k, curr_k)
    if len(anchors) < 2:
        return _diff_segment(prev, curr, prev_k, curr_k)

    out: list[str] = []
    p_lo, c_lo = 0, 0
    for p_hi, c_hi in anchors:
        out.extend(
            _extract_new_lines_impl(
                prev[p_lo:p_hi],
                curr[c_lo:c_hi],
                prev_k[p_lo:p_hi],
                curr_k[c_lo:c_hi],
                depth + 1,
            )
        )
        p_lo, c_lo = p_hi + 1, c_hi + 1
    out.extend(
        _extract_new_lines_impl(
            prev[p_lo:],
            curr[c_lo:],
            prev_k[p_lo:],
            curr_k[c_lo:],
            depth + 1,
        )
    )
    return out


def _extract_new_lines(prev: list[str], curr: list[str]) -> list[str]:
    """Entry: build keys, run hybrid recursive diff."""
    if not prev:
        return list(curr)
    if not curr:
        return []
    prev_k = [_norm_key(x) for x in prev]
    curr_k = [_norm_key(x) for x in curr]
    return _extract_new_lines_impl(prev, curr, prev_k, curr_k, depth=0)


class PTYProcess:
    def __init__(self, cmd: list[str], cwd: str, extra_env: dict[str, str] | None = None):
        self.cmd       = cmd
        self.cwd       = cwd
        self.extra_env = extra_env or {}
        self._subscribers: list[Callable[[str], None]] = []
        self.alive: bool = False

        self._screen = pyte.HistoryScreen(_COLS, _ROWS, _HISTORY)
        self._stream = pyte.Stream(self._screen)
        self._last_full_lines: list[str] = []
        self._idle_handle: asyncio.TimerHandle | None = None
        self._max_wait_handle: asyncio.TimerHandle | None = None
        self._streaming = False
        self._snapshotting = False

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
        """Diff full terminal text (history + display) as one ordered stream."""
        if self._snapshotting:
            return
        self._snapshotting = True
        try:
            curr = _full_snapshot_lines(self._screen)
            all_new = _extract_new_lines(self._last_full_lines, curr)
            self._last_full_lines = list(curr)

            if not all_new:
                return

            text = "\n".join(all_new).strip()
            if not text:
                return

            log.info("Sending output (%d chars): %.300s", len(text), text)
            for cb in self._subscribers:
                cb(text)
        finally:
            self._snapshotting = False

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
