# CLAUDE.md — Telecode developer guide

Telecode is a **Telegram bot** that runs **CLI tools** (Claude Code, Codex, shell) inside a **pseudo-terminal**, reads their screen with **pyte**, and posts text to **forum topic** threads. It also supports **screen image capture** and **screen video recording** of any window. It does **not** call any AI API itself.

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

Screen image capture:
    /new screen -> window picker (inline keyboard)
    -> user picks window -> ScreenCapture(hwnd) starts
    -> capture_window(hwnd) -> JPEG -> subscribers
    -> _LivePhoto.set_frame -> send_photo (interval = 60/rate_limits.image)

Screen video capture:
    /new video -> window picker (inline keyboard)
    -> user picks window -> VideoCapture(hwnd) starts
    -> capture_window(hwnd) -> JPEG frames saved to temp dir (3fps)
    -> every 60s: ffmpeg encodes frames -> MP4 -> send_video
    -> repeats until /stop
```

- **Session key:** `{backend}:{name}` -- colon is the separator; do not use colons in names.
- **Session naming:** auto-numbered (`claude-1`, `claude-2`) when no name given. Screen/video sessions use the window title.
- **Routing:** only `message_thread_id` -> session. No other routing.
- **Persistence:** `store.py` JSON file -- topic id per `(user_id, session_key)`; voice prefs.
- **PTY working directory:** always `Path.home()`, resolved via `config.pty_cwd()`.

---

## Cost-based rate limiting

Every active session has a cost in msgs/min (from `settings.json`):
- **Tools:** `tools.<key>.rate` (e.g. claude=5, shell=3)
- **Image:** `rate_limits.image` (e.g. 4 → sends every 15s)
- **Video:** `rate_limits.video` (e.g. 1 → one chunk/min)

Total cost must stay within `rate_limits.budget_per_min` (Telegram's 20 msgs/min per-chat limit). New sessions are blocked when budget is exhausted. The `/start` picker only shows backends that fit within remaining budget.

---

## Key files

| Path | Role |
|------|------|
| `settings.json` | Only config source |
| `config.py` | Read/write accessors (must be **functions** for hot-reload) |
| `main.py` | App startup, handlers, `set_my_commands`, voice probe loop |
| `store.py` | Topics + voice prefs JSON |
| `sessions/process.py` | PTY + pyte + snapshot diff + timers |
| `sessions/screen.py` | Image capture, video recording, window enumeration |
| `sessions/manager.py` | Start/kill sessions, send, send_raw, interrupt, pause/resume |
| `bot/handlers.py` | Commands, callbacks, `_LiveMessage`, `_LivePhoto`, cost tracking |
| `bot/topic_manager.py` | Create/reuse forum topics |
| `bot/settings_handler.py` | `/settings` parsing |
| `backends/implementations.py` | `GenericCLIBackend` (data-driven) + Screen, Video (non-PTY) |
| `backends/registry.py` | Auto-built from `settings.json` tools; `get_backend`, `all_backends`, `refresh` |
| `backends/params.py` | Load tool params from settings |
| `voice/*` | STT health, prefs, transcribe |

---

## Rules (do not break)

1. **Config** -- only `settings.json`; no scattered env vars except `TELECODE_SETTINGS`.
2. **`config.py`** -- always `config.foo()`, never cached module-level constants for values that can change.
3. **Sessions** -- key format `backend:name`; routing by `thread_id` only.
4. **Processes** -- real PTY (Unix `openpty`, Windows ConPTY via pywinpty), not plain pipes. Screen capture uses PrintWindow (Win) / screencapture (Mac) / import (Linux), not PTY.
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

## Screen image capture pipeline (`sessions/screen.py`)

1. `enumerate_windows()` -- platform-specific window enumeration. Windows uses `EnumWindows` + `DwmGetWindowAttribute(DWMWA_CLOAKED)` to list only taskbar windows (no skip lists). Linux uses `wmctrl`/`xdotool`. macOS uses `CGWindowListCopyWindowInfo`.
2. `ScreenCapture(hwnd)` captures the window via `capture_window(hwnd)`.
3. `capture_window()` acquires a per-hwnd lock, auto-restores minimized windows, then uses platform-specific capture:
   - **Windows**: `PrintWindow` API (z-order independent, captures even behind other windows).
   - **Linux**: ImageMagick `import -window`, falls back to `mss` region capture.
   - **macOS**: `screencapture -l<wid>`, falls back to `mss` region capture.
4. Pillow encodes to JPEG (quality 80, full resolution).
5. Frames pushed to subscribers at `capture_interval` (`60 / rate_limits.image` seconds).
6. `_LivePhoto` sends each frame as a **new photo message**. Send interval = `60 / rate_limits.image` seconds.
7. Pause: drops frames in `set_frame()` and cancels pending send timers.
8. Window gone: capture returns None → auto-stops and notifies.
9. **Session 0** (Windows service): spawns helper process in user's session via `WTSQueryUserToken` + `CreateProcessAsUser`.

---

## Screen video capture pipeline (`sessions/screen.py`)

1. `VideoCapture(hwnd, duration=60, fps=3)` records continuously in 1-minute chunks.
2. Each chunk: captures frames via `capture_window()` at 3fps, saves as numbered JPEGs in a temp dir.
3. After 60s, encodes with ffmpeg: `libx264 -preset ultrafast -crf 32 -pix_fmt yuv420p`.
4. `scale=trunc(iw/2)*2:trunc(ih/2)*2` filter ensures even dimensions for libx264.
5. Sends encoded MP4 via `send_video`, then starts the next chunk.
6. Continues until `/stop`. On stop, encodes and sends any remaining frames.
7. Pause: recording loop sleeps, paused time doesn't count towards chunk duration.
8. Minimized windows: auto-restored before each frame capture.

---

## Live Telegram messages (`bot/handlers.py`)

- **`_LiveMessage`:** one text message per "turn", updated by `append()`; debounced edits (~1s); overflow opens a new message. Overlap trim skips duplicate tails.
- **`_LivePhoto`:** sends each frame as a **new photo message**. Send interval derived from `rate_limits.image` config. Drops frames while paused. Inline buttons for pause/resume/stop.

---

## Adding a CLI backend

Just add a `tools.<key>` entry in `settings.json`:

```json
"my-tool": {
  "name": "My Tool",
  "icon": "🔧",
  "rate": 5,
  "startup_cmd": ["my-tool"],
  "flags": ["--some-flag"],
  "env": { "API_KEY": "..." },
  "session": {}
}
```

The registry auto-creates a `GenericCLIBackend` for any key that isn't a special non-PTY backend (`screen`, `video`).
`name`, `icon`, and `rate` are optional — defaults to title-cased key, 🔧, and 5 msgs/min.
No code changes needed.

Test: `/settings reload` then `/new <key> test`.

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
| Screen capture blank | Window may be on another virtual desktop; minimized windows are auto-restored |
| Video encoding fails | Ensure ffmpeg is on PATH; check logs for ffmpeg stderr |
| Voice not working | Run `/voice`; start STT service, bot detects within 60s |
| Rate limit hit | Too many sessions — stop some or increase `budget_per_min` |

---

## Dependencies (see `requirements.txt`)

**python-telegram-bot**, **aiohttp**, **aiofiles**, **pyte**, **pywinpty** (Windows PTY), **mss** (fallback capture on Linux/Mac), **Pillow** (JPEG encoding), **pywin32** (Windows Session 0 support). ffmpeg must be on PATH for video recording.
