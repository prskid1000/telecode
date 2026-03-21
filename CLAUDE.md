# CLAUDE.md — Telecode developer guide

Telecode is a **Telegram bot** that runs **CLI tools** (Claude Code, Codex, shell) inside a **pseudo-terminal**, reads their screen with **pyte**, and posts text to **forum topic** threads. It also supports **screen capture** of any window via mss. It does **not** call any AI API itself.

User-facing docs (setup, commands, settings reference) are in [README.md](README.md).

---

## Architecture

```
User message (topic thread)
    -> handlers.handle_text / voice / document
    -> SessionManager.get_session_by_thread -> SessionManager.send()
    -> PTYProcess.send()  (adds \r)
    -> pyte parses output -> snapshot -> diff vs last snapshot -> subscribers
    -> _send_output -> _LiveMessage.append -> editMessageText (<pre>, HTML)

Screen capture:
    /new screen -> window picker (inline keyboard)
    -> user picks window -> ScreenCapture(hwnd) starts
    -> mss.grab(window_rect) -> JPEG -> subscribers
    -> _LivePhoto.set_frame -> editMessageMedia (photo)
```

- **Session key:** `{backend}:{name}` -- colon is the separator; do not use colons in names.
- **Routing:** only `message_thread_id` -> session. No other routing.
- **Persistence:** `store.py` JSON file -- topic id per `(user_id, session_key)`; voice prefs.
- **PTY working directory:** always `Path.home()`, resolved via `config.pty_cwd()`.

---

## Key files

| Path | Role |
|------|------|
| `settings.json` | Only config source |
| `config.py` | Read/write accessors (must be **functions** for hot-reload) |
| `main.py` | App startup, handlers, `set_my_commands`, voice probe loop |
| `store.py` | Topics + voice prefs JSON |
| `sessions/process.py` | PTY + pyte + snapshot diff + timers |
| `sessions/screen.py` | Screen capture (mss + Pillow), window enumeration (ctypes Win32) |
| `sessions/manager.py` | Start/kill sessions, send, send_raw, interrupt, pause/resume |
| `bot/handlers.py` | Commands, callbacks, `_LiveMessage`, `_LivePhoto`, `/key` |
| `bot/topic_manager.py` | Create/reuse forum topics |
| `bot/settings_handler.py` | `/settings` parsing |
| `backends/implementations.py` | Claude, Codex, Shell, PowerShell, Screen backends |
| `backends/registry.py` | `get_backend`, `all_backends` |
| `backends/params.py` | Load tool params from settings |
| `voice/*` | STT health, prefs, transcribe |

---

## Rules (do not break)

1. **Config** -- only `settings.json`; no scattered env vars except `TELECODE_SETTINGS`.
2. **`config.py`** -- always `config.foo()`, never cached module-level constants for values that can change.
3. **Sessions** -- key format `backend:name`; routing by `thread_id` only.
4. **Processes** -- real PTY (Unix `openpty`, Windows ConPTY via pywinpty), not plain pipes. Screen capture uses mss, not PTY.
5. **Telegram** -- `ParseMode.HTML`; escape user/process text with `html.escape()` where needed.
6. **No** in-bot AI and **no** separate "memory" layer -- CLIs own context.

---

## PTY output pipeline (`sessions/process.py`)

1. Raw bytes -> **pyte** `HistoryScreen` + `Stream`.
2. Each snapshot = **history lines** + **display lines** (one top-to-bottom list).
3. Compare to **previous** full list: find **new** lines only (patience/histogram anchors + segment diff + "similar line" filter so spinners/status lines do not spam).
4. Emit chunks to subscribers on **idle** (~2s) or **max wait** (~5s); poll safety net every 5s.
5. **Input:** `send()` appends `\r` (not `\n`) so TUIs accept the line.

Tunables near top of `process.py`: idle interval, max wait, screen rows/history size.

---

## Screen capture pipeline (`sessions/screen.py`)

1. `enumerate_windows()` via ctypes `EnumWindows` -- lists visible windows with titles.
2. `ScreenCapture(hwnd)` grabs the window rect via `GetWindowRect`, captures with `mss.grab()`.
3. Pillow converts to JPEG (quality 80, full resolution).
4. Frames pushed to subscribers at `capture_interval` (default 0.5s).
5. `_LivePhoto` in handlers sends first frame via `send_photo`, then edits via `editMessageMedia` (~1.5s interval).
6. Pause/resume: `ScreenCapture.pause()` / `.resume()` -- capture loop sleeps when paused.
7. Window gone: detected via `IsWindow()` -- auto-stops and notifies.

---

## Live Telegram messages (`bot/handlers.py`)

- **`_LiveMessage`:** one text message per "turn", updated by `append()`; debounced edits (~1s); overflow opens a new message. Overlap trim skips duplicate tails.
- **`_LivePhoto`:** one photo message per screen session, updated by `set_frame()`; debounced edits (~1.5s); latest frame wins, older dropped. Inline buttons for pause/resume/stop.

---

## Adding a CLI backend

1. Class in `backends/implementations.py` extending `CLIBackend` (`info`, optional `build_launch_cmd`, `startup_message`).
2. Register instance in `backends/registry.py`.
3. Add `tools.<key>` in `settings.json` (`startup_cmd`, `flags`, `env`, `session` if needed).
4. Add icon to `BACKEND_ICONS` in `implementations.py`.

Test: `/new <key> test`.

---

## Adding a Telegram command

1. `async def cmd_xxx(update, ctx)` in `bot/handlers.py`.
2. `app.add_handler(CommandHandler("xxx", cmd_xxx))` in `main.py`.
3. Add to `BOT_COMMANDS` and `cmd_help()`.

---

## Common problems

| Symptom | What to check |
|--------|----------------|
| Bot silent | Token, group id, bot admin, Topics on |
| "No session for thread" | `/new` again; store may be missing mapping |
| CLI exits at once | Missing API key, wrong `startup_cmd`, binary not on PATH |
| Stuck on prompt | User sends `/key enter` or `/key y` |
| Garbled / noisy stream | TUI limitation; tune diff in `process.py` or use a non-interactive CLI mode |
| `settings` change ignored | `/settings reload` or restart |
| Screen capture black | Window may be minimized or on another virtual desktop |
| Voice not working | Run `/voice`; start STT service, bot detects within 60s |

---

## Dependencies (see `requirements.txt`)

**python-telegram-bot**, **aiohttp**, **aiofiles**, **pyte**, **pywinpty** (Windows PTY), **mss** (screen capture), **Pillow** (JPEG encoding).
