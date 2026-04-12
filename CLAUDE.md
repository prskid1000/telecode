# CLAUDE.md — Telecode developer guide

Telecode is a **Telegram bot** that runs **CLI tools** (Claude Code, Codex, shell) inside a **pseudo-terminal**, reads their screen with **pyte**, and posts text to **forum topic** threads. It also supports **screen image capture**, **screen video recording**, and **vision-LLM-driven computer control** of any window or the full screen.

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
    -> _FrameSender.set_frame -> send_photo (interval = capture.image_interval)

Screen video capture:
    /new video -> window picker (inline keyboard)
    -> user picks window -> VideoCapture(hwnd) starts
    -> capture_window(hwnd) -> JPEG frames saved to temp dir (3fps)
    -> every capture.video_interval seconds: ffmpeg encodes frames -> MP4 -> send_video
    -> repeats until /stop

Computer control (vision LLM):
    /new computer -> window picker + "Full Screen" option
    -> user picks target -> ComputerControl(hwnd) starts
    -> user sends instruction via Telegram
    -> capture screenshot (with cursor drawn) -> send to user + vision LLM
    -> LLM returns structured JSON: {thought, done, action}
    -> execute action via pyautogui (click/type/key/scroll)
    -> capture post-action screenshot -> edit same photo message
    -> repeat (one action per LLM call) until done=true or user sends new message

Tool-search proxy (for local models):
    Claude Code request (with ~100+ tools)
    -> proxy intercepts on http://127.0.0.1:1235/v1/messages
    -> split_tools(): core tools forwarded, rest stored as deferred
    -> inject ToolSearch meta-tool into core tools
    -> forward to LM Studio (http://localhost:1234)
    -> if model calls ToolSearch:
        -> BM25/regex search over deferred tools
        -> inject matched tool definitions into request
        -> transparent round-trip to LM Studio
        -> stream second response to Claude Code
    -> else: stream response through unchanged
```

- **Session key:** `{backend}:{name}` -- colon is the separator; do not use colons in names.
- **Session naming:** auto-numbered (`claude-1`, `claude-2`) when no name given. Screen/video sessions use the window title.
- **Routing:** only `message_thread_id` -> session. No other routing.
- **Persistence:** `store.py` JSON file -- topic id per `(user_id, session_key)`; voice prefs.
- **PTY working directory:** always `Path.home()`, resolved via `config.pty_cwd()`.

---

## Session cleanup

**Two cleanup levels:**
- **Fast cleanup** (`cleanup_stale_sessions`): runs on every picker click. Checks `process.alive` and removes dead sessions. No API calls.
- **Full cleanup** (`full_cleanup`): runs on `/start` only. Probes each session's topic via `sendMessage`+`deleteMessage` to detect externally deleted topics. Safe because `/start` is infrequent.

**Topic deleted externally:** detected in two ways:
1. On `/start`: `full_cleanup` probes topics and detects "thread not found".
2. On output send: `_LiveMessage`, `_FrameSender`, and video callbacks detect "thread not found" and call `handle_topic_gone()`.

**`/stop` behavior:**
- `/stop` in a session topic: stops only that session.
- `/stop` in General (no args): stops all sessions.
- `/stop <name>`: stops a specific session by name/key.

---

## Key files

| Path | Role |
|------|------|
| `settings.json` | Only config source |
| `config.py` | Read/write accessors (must be **functions** for hot-reload) |
| `main.py` | App startup, handlers, `set_my_commands`, voice probe loop (no background stale checker) |
| `store.py` | Topics + voice prefs JSON |
| `sessions/process.py` | PTY + pyte + snapshot diff + timers |
| `sessions/screen.py` | Image capture, video recording, window enumeration |
| `sessions/computer.py` | Vision LLM computer control (capture + actions + LLM loop) |
| `sessions/manager.py` | Start/kill sessions, send, send_raw, interrupt, pause/resume |
| `bot/handlers.py` | Commands, callbacks, `_LiveMessage`, `_FrameSender` |
| `bot/rate.py` | Stale session cleanup, topic probing, topic-gone detection |
| `bot/topic_manager.py` | Create/reuse forum topics |
| `bot/settings_handler.py` | `/settings` parsing |
| `backends/implementations.py` | `GenericCLIBackend` (data-driven) + Screen, Video (non-PTY) |
| `backends/registry.py` | Auto-built from `settings.json` tools; `get_backend`, `all_backends`, `refresh` |
| `backends/params.py` | Load tool params from settings |
| `voice/*` | STT health, prefs, transcribe |
| `proxy/__main__.py` | Standalone entry: `python -m proxy` |
| `proxy/server.py` | aiohttp streaming proxy with ToolSearch interception |
| `proxy/tool_search.py` | BM25 + regex search engine (zero deps) |
| `proxy/tool_registry.py` | Core/deferred tool splitting, ToolSearch injection |
| `proxy/tool_result_rewriters.py` | Generic framework: replace `tool_result` content from registered tools; auto-loads `proxy/rewriters/` |
| `proxy/rewriters/__init__.py` | Drop-in package — every sibling `.py` is auto-imported on framework load |
| `proxy/rewriters/web_search.py` | SearXNG-backed `WebSearch` rewriter + native auto-installer (clone + venv + generated settings.yml) |
| `proxy/config.py` | Proxy settings (port, upstream, core tools, BM25 params, web_search) |
| `mcp_server/app.py` | FastMCP instance (stateless streamable HTTP) |
| `mcp_server/server.py` | Background startup (daemon thread, like proxy) |
| `mcp_server/__main__.py` | Standalone entry: `python -m mcp_server` |
| `mcp_server/tools/__init__.py` | Auto-discovers tool modules (drop-in) |
| `mcp_server/tools/tts.py` | `speak` tool — Kokoro TTS → audio file |
| `mcp_server/tools/stt.py` | `transcribe` tool — Whisper STT → text |
| `mcp_server/resources/__init__.py` | Auto-discovers resource modules (drop-in) |
| `mcp_server/prompts/__init__.py` | Auto-discovers prompt modules (drop-in) |

---

## Rules (do not break)

1. **Config** -- only `settings.json`; no scattered env vars except `TELECODE_SETTINGS`.
2. **`config.py`** -- always `config.foo()`, never cached module-level constants for values that can change.
3. **Sessions** -- key format `backend:name`; routing by `thread_id` only.
4. **Processes** -- real PTY (Unix `openpty`, Windows ConPTY via pywinpty), not plain pipes. Screen capture uses PrintWindow (Win) / screencapture (Mac) / import (Linux), not PTY. Computer control uses `pyautogui` for mouse/keyboard.
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
5. Frames pushed to subscribers at `capture.image_interval` seconds (default 15).
6. `_FrameSender` sends each frame as a **new photo message**. Send interval = `capture.image_interval` seconds.
7. Pause: drops frames in `set_frame()` and cancels pending send timers.
8. Window gone: capture returns None → auto-stops and notifies.
9. **Session 0** (Windows service): spawns helper process in user's session via `WTSQueryUserToken` + `CreateProcessAsUser`.

---

## Screen video capture pipeline (`sessions/screen.py`)

1. `VideoCapture(hwnd, duration=capture.video_interval, fps=3)` records continuously in chunks.
2. Each chunk: captures frames via `capture_window()` at 3fps, saves as numbered JPEGs in a temp dir.
3. After `capture.video_interval` seconds (default 60), encodes with ffmpeg: `libx264 -preset ultrafast -crf 32 -pix_fmt yuv420p`.
4. `scale=trunc(iw/2)*2:trunc(ih/2)*2` filter ensures even dimensions for libx264.
5. Sends encoded MP4 via `send_video`, then starts the next chunk.
6. Continues until `/stop`. On stop, encodes and sends any remaining frames.
7. Pause: recording loop sleeps, paused time doesn't count towards chunk duration.
8. Minimized windows: auto-restored before each frame capture.

---

## Computer control pipeline (`sessions/computer.py`)

1. `ComputerControl(hwnd)` — duck-type compatible with PTYProcess/ScreenCapture (`.alive`, `.start()`, `.stop()`, `.subscribe()`, `.send()`).
2. `hwnd=0` (sentinel `FULL_SCREEN_HWND`) captures the entire screen via `mss`. Otherwise captures the specific window via `capture_window()`.
3. Mouse cursor position is drawn onto every screenshot as a red crosshair (since PrintWindow/mss don't capture the OS cursor).
4. Coordinates: screenshots are physical pixels, window rect is logical pixels. The ratio `img_w/win_w` naturally handles DPI scaling. `pyautogui` receives logical coords.
5. Action loop (one action per LLM call):
   - User sends message → capture screenshot → send to vision LLM with conversation history.
   - LLM returns structured JSON: `{thought, done, action}` via `response_format: json_schema`.
   - If `done=false`: execute the single action via `pyautogui`, capture post-action screenshot, send to user (edit photo in place), loop back to call LLM again.
   - `wait` actions are handled async (not blocking a thread), capped at 30s. After wait, screenshot is sent to LLM with "Continue." to check if the UI has updated.
   - If `done=true`: send final screenshot, break loop, wait for next user message.
   - If user sends new message mid-loop: interrupts via `_msg_queue.get_nowait()`, restarts with new instruction.
6. LLM API: supports OpenAI (`/chat/completions`), Anthropic (`/messages`), and Claude Code CLI (`claude -p --output-format json`) wire formats, toggled by `api.format` in settings (`"openai"`, `"anthropic"`, `"claude-code"`). Claude Code format uses `--resume` for conversation continuity and `--json-schema` for structured output. When `base_url`/`api_key`/`model` are set with `claude-code` format, they are passed as `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_MODEL` env vars to the subprocess — enabling local LM Studio backends without code changes.
7. Conversation history: rolling window of `max_history` turns. System prompt teaches computer interaction fundamentals (mouse, focus, text cursor, selection, editing, UI elements).
8. Photo delivery: first screenshot sends a new photo message, subsequent screenshots edit the same message via `edit_message_media`.
9. Text delivery: thoughts and action summaries go through `_send_output` → `_LiveMessage.append()` (same as PTY sessions).

---

## Tool-search proxy pipeline (`proxy/`)

Middleware proxy for local models (LM Studio, Ollama, etc.) that reduces tool token bloat. Claude Code sends ~100+ tool definitions per request; local models choke on them. The proxy strips non-essential tools and provides on-demand search.

1. **Startup:** `start_proxy_background()` called from `main.py:_post_init`. Listens on `127.0.0.1:{proxy.port}` (default 1235). Disabled when `proxy.enabled` is `false`. Can also run standalone via `python -m proxy`.
2. **Request interception** (`handle_messages`): extracts `tools` from the Anthropic-format request, calls `split_tools()` to separate core (always forwarded) from deferred (stored in memory). Injects `ToolSearch` meta-tool into core list.
3. **Core tools** (configurable via `proxy.core_tools`): `Bash`, `Edit`, `Read`, `Write`, `Glob`, `Grep`, `Agent`, `Skill`. Matches Opus's core set (~6.9k tokens). Everything else is deferred.
4. **System instruction injection** (`build_tool_catalog`): appends a dynamic catalog to the system message listing core tools and all deferred tools grouped by category (chrome-devtools, context-mode, code-review-graph, etc.). Strips Claude Code's duplicate `<system-reminder>` deferred-tool listings from messages.
   - **`proxy_system.md` conditional preprocessor** (`proxy_system_instruction()` in `tool_registry.py`): the markdown source is re-read per request and run through `_preprocess_conditionals()`, which resolves `<if dotted.settings.path="value">…</if>` blocks against current settings via `config.get_nested()`. Tags must live on their own lines; flat only (no nesting). Inner content is kept (tags stripped) when the value matches, otherwise the whole block is dropped — letting one source file slim down based on toggles like `proxy.strip_reminders` without maintaining parallel docs.
5. **ToolSearch interception:** if the model's response calls `ToolSearch`, the proxy intercepts (does NOT forward to Claude Code), runs BM25 or regex search on deferred tools, injects matched tool definitions into the request, appends a tool_result message, and does a transparent round-trip to the upstream. The second response streams to Claude Code.
6. **BM25 search** (`tool_search.py`): tokenizes tool name + description + arg names + arg descriptions. Standard BM25 scoring with `k1=0.9`, `b=0.4`. Regex search via `re:` prefix.
7. **`tool_result` image lifting** (`lift_tool_result_images()` in `tool_registry.py`, gated by `proxy.lift_tool_result_images`): LM Studio's Anthropic endpoint rejects `tool_result.content` when it's an array of blocks, and Anthropic itself requires `tool_result` blocks to come first in a user message — so images returned by `Read` on `.jpg` or by MCP screenshot tools can't ride inside the tool_result. The transform rewrites each array-form `tool_result.content` as a plain-string placeholder naming the `tool_use_id` and image count (e.g. `"[2 images from tool_use_id=abc appended at the end of this user message, labeled: tool_use_id=abc.1, tool_use_id=abc.2]"`) and appends the image blocks, each preceded by a matching text label, at the **end** of the same user message. Tool_results stay contiguous at the start (spec-compliant), images arrive as normal user-message image blocks (which LM Studio accepts, same as pasted-image chat), and the label cross-reference preserves provenance since position alone no longer encodes it.
8. **Streaming:** SSE events are parsed line-by-line. Non-ToolSearch content streams through immediately. ToolSearch blocks are buffered and intercepted.
9. **Tool-result rewriters** (`proxy/tool_result_rewriters.py`, gated by `proxy.tool_result_rewriting`): walks `body.messages[]`, finds `tool_result` blocks whose originating `tool_use` was for a registered tool, lets the rewriter substitute new content. Drop-in modules under `proxy/rewriters/` are auto-loaded via `pkgutil.iter_modules`. Concurrent via `asyncio.gather`. Add a rewriter: drop a file at `proxy/rewriters/<tool_name>.py`, call `make_rewriter("WebSearch", should_fn, async_replace_fn)`. CC's UI still shows the original tool_use; only the model's view of history is rewritten.
   - **`rewriters/web_search.py`**: detects CC's empty `Web search results for query: "..."` placeholder (starts with the literal prefix, no `http://`/`https://`) and queries a local SearXNG via `/search?format=json`. Surfaces SearXNG's three content channels (`results`, `infoboxes`, `answers`) so wikipedia entity infoboxes and currency conversions aren't dropped. LRU-cached by `max_results:query` (cap 256). Errors return as `ERROR: ...` strings inline.
   - **SearXNG auto-setup** (`ensure_searxng_running()`, runs on every `start_proxy_background()`): clones [`mbaozi/SearXNGforWindows`](https://github.com/mbaozi/SearXNGforWindows) into `data/searxng/repo/`, creates a venv at `data/searxng/.venv/`, pip-installs `repo/config/requirements.txt`, **copies** the patched `searx/` package into the venv's own `site-packages/` (NOT via PYTHONPATH — the embedded fork's compiled msgspec/lxml are built for Python 3.11.9 and would ImportError under any other interpreter), generates `data/searxng/config/settings.yml` from `repo/config/settings.yml` overlaid with `proxy.web_search.searxng.*` (engine enable/disable, port, secret_key, language, safesearch). The generated settings.yml lives outside `repo/` so `git pull` doesn't clobber it. All setup subprocess calls run silent via `CREATE_NO_WINDOW + STARTUPINFO.SW_HIDE`. Settings.yml is regenerated on every boot so config changes always take effect.
   - **Run command**: `.venv/Scripts/python.exe -m searx.webapp` with `cwd=data/searxng/`. cwd MUST be an ancestor of `.venv/Lib/site-packages/searx/data/` because the fork patched `searx/data/__init__.py` to compute `Path(__file__).parent.relative_to(Path.cwd())`. Settings come via `SEARXNG_SETTINGS_PATH` env var.
   - **Process lifecycle**: bound to a Windows Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` via ctypes — when Telecode dies for any reason (graceful, `Stop-ScheduledTask`, `taskkill /F`, BSOD) the kernel closes the job handle and kills every member. Belt-and-braces fallbacks: PID written to `data/searxng/searxng.pid`, and `_kill_orphan()` runs at every boot to (a) kill any pid in the file, (b) kill any process listening on `web_search.url`'s port via `Get-NetTCPConnection`. Deliberately NOT using `CREATE_NEW_PROCESS_GROUP` — that breaks job-based parent-death propagation. `stop_searxng()` is wired into `main.py:_post_shutdown` for graceful path.
   - **Engine selection** (verified empirically by enabling all 25+ candidates and probing each via shortcut bang): default picks one best engine per distinct purpose, no duplicates — `startpage` (general web), `bing news` (current news), `wikipedia` (encyclopedic facts), `wiktionary` (definitions), `reddit` (discussion), `stackoverflow` (programming Q&A), `github` (code), `mdn` (web docs), `semantic scholar` (academic papers). `startpage` is preferred over `bing` (which serves decoy spam to SearXNG scrapers); google/ddg/mojeek/brave fail with CAPTCHAs or stale HTML parsers. SearXNG only routes queries to engines whose `categories` match the request, so general queries hit `startpage`/`wikipedia`/`wiktionary` only — specialized engines fire via `!shortcut` bangs (`!gh`, `!st`, `!re`, `!bin`).
   - **CC display caveat**: CC's "Did N searches" count is computed by CC's own executor before the empty result leaves CC, so the proxy can't reach it — the UI stays at 0 even though the model sees populated results.
10. **Passthrough:** all non-`/v1/messages` requests (models, health checks) are forwarded unchanged.
11. **Shutdown:** `_post_shutdown` calls `runner.cleanup()`.

To use: set `proxy.enabled: true` in settings.json and point `claude-local`'s `ANTHROPIC_BASE_URL` at `http://localhost:1235`.

---

## Live Telegram messages (`bot/handlers.py`)

- **`_LiveMessage`:** one text message per "turn", updated by `append()`; debounced edits (~1s); overflow opens a new message. Overlap trim skips duplicate tails.
- **`_FrameSender`:** sends each frame as a **new photo message**. Send interval = `capture.image_interval`. Drops frames while paused. Inline buttons for pause/resume/stop.

---

## Adding a CLI backend

Just add a `tools.<key>` entry in `settings.json`:

```json
"my-tool": {
  "name": "My Tool",
  "icon": "🔧",
  "startup_cmd": ["my-tool"],
  "flags": ["--some-flag"],
  "env": { "API_KEY": "..." },
  "session": {}
}
```

The registry auto-creates a `GenericCLIBackend` for any key that isn't a special non-PTY backend (`screen`, `video`).
`name` and `icon` are optional — defaults to title-cased key and 🔧.
No code changes needed.

Test: `/settings reload` then `/new <key> test`.

---

## Adding a Telegram command

1. `async def cmd_xxx(update, ctx)` in `bot/handlers.py`.
2. `app.add_handler(CommandHandler("xxx", cmd_xxx))` in `main.py`.
3. Add to `BOT_COMMANDS` and `cmd_help()`.

---

## MCP audio server (`mcp_server/`)

Streamable HTTP MCP server exposing local TTS and STT as tools for Claude Code (or any MCP client). Uses FastMCP with stateless HTTP transport.

1. **Startup:** `start_mcp_background()` called from `main.py:_post_init`. Runs in a daemon thread (FastMCP manages its own uvicorn loop). Listens on `127.0.0.1:{mcp_server.port}` (default 1236). Disabled when `mcp_server.enabled` is `false`.
2. **Drop-in auto-discovery:** three folders use `pkgutil.iter_modules` to import every `.py` file automatically:
   - `mcp_server/tools/` — functions decorated with `@mcp_app.tool()`
   - `mcp_server/resources/` — functions decorated with `@mcp_app.resource()`
   - `mcp_server/prompts/` — functions decorated with `@mcp_app.prompt()`
   Adding a new tool/resource/prompt = drop a `.py` file in the right folder. No other files change.
3. **Built-in tools:**
   - `speak(text, voice?, output_path?)` — POST to Kokoro TTS (`mcp_server.tts_url`), saves audio to file, returns path.
   - `transcribe(audio_path, language?)` — POST to Whisper STT (`mcp_server.stt_url`), returns transcribed text. Accepts local file paths or remote URLs (http/https).
   - `web_search(query, allowed_domains?, blocked_domains?)` — same schema as Anthropic's official `WebSearch` tool. Reuses the SearXNG backend, cache, and config from `proxy/rewriters/web_search.py` so the MCP entry point and the proxy intercept entry point share one provider abstraction. Domain filters fetch 3× extra results then post-filter by hostname (subdomain matches included). Returns the same Anthropic-style `[N] Title / URL / Snippet` formatted string.
4. **Config:** URLs and port from `settings.json` under `mcp_server.*`, with env var fallback (`KOKORO_URL`, `WHISPER_URL`, `MCP_HOST`, `MCP_PORT`) for standalone use.
5. **Standalone:** `python -m mcp_server` runs independently of the Telegram bot.
6. **Shutdown:** daemon thread dies with the process.

To use with Claude Code: `claude mcp add telecode --transport streamable-http --url http://127.0.0.1:1236/mcp`

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
| Computer control clicks wrong spot | DPI scaling issue; check `_get_window_rect` returns logical coords |
| Computer control LLM error | Check LM Studio is running, model is loaded, base_url/model in settings |
| Voice not working | Run `/voice`; start STT service, bot detects within 60s |
| Proxy not starting | Check `proxy.enabled` is `true` in settings.json; check port not in use |
| ToolSearch not triggered | Model may not call it; check upstream is reachable; check logs for "Proxy:" lines |
| Tools missing after search | Tool may not match query; try regex with `re:` prefix; check `MAX_SEARCH_RESULTS` |
| MCP server not starting | Check `mcp_server.enabled` is `true`; check port 1236 not in use |
| MCP speak fails | Kokoro TTS must be running on `mcp_server.tts_url` (default :6500) |
| MCP transcribe fails | Whisper STT must be running on `mcp_server.stt_url` (default :6600) |

---

## Running in background (Windows)

Use `pythonw main.py` instead of `python main.py` to run without a console window. For auto-start, create a Windows Scheduled Task with `pythonw.exe` as the executable — this keeps the bot hidden with no terminal window. See README.md for the full PowerShell command.

---

## Dependencies (see `requirements.txt`)

**python-telegram-bot**, **aiohttp**, **aiofiles**, **pyte**, **pywinpty** (Windows PTY), **mss** (fallback capture on Linux/Mac), **Pillow** (JPEG encoding), **pywin32** (Windows Session 0 support), **pyautogui** (mouse/keyboard for computer control), **mcp** (MCP SDK for audio server). ffmpeg must be on PATH for video recording.
