# CLAUDE.md — Telecode Developer Guide

## What This Is

Telecode is a Telegram bot that relays text between Telegram and CLI processes (Claude Code, Codex, shell). It spawns each CLI in a pseudo-terminal, captures output via a pyte virtual terminal with line-level diff, and sends clean text to Telegram. It does not implement any AI.

## Architecture

```
Telegram message → handlers.py → SessionManager.send() → PTYProcess stdin
                                                              ↓
Telegram ← _send_output() ← LiveMessage ← line-diff ← pyte screen ← PTYProcess stdout
```

Each session is a PTYProcess. Output goes through pyte HistoryScreen, then a two-layer diff:
1. **History** (scrolled-off content) — always stable, tracked by count
2. **Display** (visible screen) — diffed line-by-line with `difflib.SequenceMatcher`; spinner/timer updates detected via similarity ratio and skipped

Clean text is sent as `<pre>` HTML messages in the session's forum topic via `_LiveMessage` (edit-in-place streaming).

Sessions are keyed by `"{backend}:{name}"` (e.g. `"claude:work"`). Each maps to a Telegram forum topic thread via `message_thread_id`. Config lives in `settings.json`, accessed via `config.py` functions. Persistence (topic mappings, voice prefs) is a JSON file.

## Key Files

- `settings.json` — all config, single source of truth
- `config.py` — typed accessors, always functions (for hot-reload)
- `store.py` — JSON persistence (topics, voice prefs)
- `sessions/process.py` — PTY + pyte screen + SequenceMatcher line diff
- `sessions/manager.py` — session lifecycle, keyed by (user_id, session_key)
- `bot/handlers.py` — all Telegram handlers, `_LiveMessage` streaming, `/key` command
- `bot/topic_manager.py` — forum topic creation
- `bot/settings_handler.py` — `/settings` command tree
- `backends/implementations.py` — one class per CLI tool
- `backends/registry.py` — backend lookup

## Rules

1. `settings.json` is the only config source. No `os.getenv()` outside `config.py`.
2. `config.py` accessors are functions, not constants. Always `config.foo()`.
3. `session_key` format is `"{backend}:{name}"`. Colon is the delimiter.
4. Session routing uses `thread_id` only. No other routing.
5. CLI processes use PTY (pseudo-terminal), not pipes.
6. No memory/context system. CLIs manage their own context.
7. All tool config is global in `settings.json`.
8. Telegram messages use `ParseMode.HTML`. Escape dynamic content with `html.escape()`.

## Data Flow

1. User types in a Telegram topic thread
2. `handle_text()` finds session by `message_thread_id`
3. Previous `_LiveMessage` finalized, new one created for this turn
4. `SessionManager.send()` writes `text + "\r"` to PTY stdin
5. PTY output read by background reader → fed to `pyte.Stream`
6. On idle (2s) or max-wait (5s), `_do_snapshot()` runs:
   - Extracts history lines (count-based, always new)
   - Extracts display lines, diffs vs previous with `SequenceMatcher`
   - `_similar()` detects TUI chrome updates (spinner/timer changes) and skips them
   - Only genuinely new lines are emitted to subscribers
7. `_send_output()` → `_LiveMessage.append()` (overlap dedup) → debounced `editMessageText`

## Output Diff Algorithm (process.py)

The diff uses `difflib.SequenceMatcher` at the line level:

- **`insert`** — brand new lines → always emitted
- **`replace`** — lines that changed → emitted only if NOT similar to old line
- **`equal`** — unchanged lines → skipped
- **`delete`** — removed lines → skipped

Similarity check (`_similar`): strips all non-alphanumeric characters and compares with `SequenceMatcher.ratio() > 0.7`. This catches spinner changes (`· → * → ✶`), timer updates (`1m 25s → 1m 26s`), and other ephemeral TUI chrome without any hardcoded patterns.

## Interactive Terminal Keys (handlers.py)

The `/key` command sends any keyboard input to the PTY:

- `_KEYS` dict maps key names to VT100 escape sequences
- `_build_key_sequence(tokens)` parses modifier+key combinations
- Modifiers (ctrl, alt, shift) combined using xterm modifier encoding
- `SessionManager.send_raw()` writes raw bytes to PTY stdin

Supports: all printable characters, enter, esc, tab, backspace, space, delete, insert, arrows, home/end, pgup/pgdn, f1-f12, and any ctrl/alt/shift combination.

## Live Message Streaming (handlers.py)

`_LiveMessage` manages a single Telegram message that gets edited in-place as output streams in:

- `append(text)` — runs overlap detection (`_find_overlap_end`), then schedules debounced edit
- `_do_edit()` — calls `editMessageText` (rate-limited to 1 edit/sec)
- Overflow (>4000 chars) — finalizes current message, starts a new one
- `finalize()` — cancels pending timers, does final edit

One `_LiveMessage` per thread. Replaced when user sends a new message.

## Adding a CLI Backend

1. Add class in `backends/implementations.py`:

```python
class MyCLIBackend(CLIBackend):
    @property
    def info(self) -> BackendInfo:
        return BackendInfo(
            key="mycli", name="My CLI",
            description="Does things",
            base_cmd=config.tool_startup_cmd("mycli"),
            default_flags=config.tool_flags("mycli"),
        )
```

2. Register in `backends/registry.py`
3. Add config block in `settings.json` under `tools`

## Adding a Bot Command

1. Write handler in `bot/handlers.py`
2. Import and register in `main.py`
3. Add to `cmd_help()` and `BOT_COMMANDS` list

## Voice System

```
voice/health.py  — probe STT on startup + every 60s
voice/prefs.py   — per-user STT toggle (JSON store)
voice/stt.py     — audio → text via OpenAI-compatible API
```

STT service is auto-detected. If down, voice input is silently disabled.

## Common Issues

- **Process exits immediately** — missing API key or CLI not on PATH
- **Interactive prompt blocking** — CLI asks "Trust this folder?" etc. User sends `/key enter` to proceed
- **No output** — CLI may show interactive prompt that pyte captures but isn't meaningful
- **Duplicate output** — similarity threshold in `_similar()` may need tuning for new CLIs
- **config.py function vs variable** — always use functions for hot-reload
- **HTML escaping** — all dynamic content must go through `html.escape()`
