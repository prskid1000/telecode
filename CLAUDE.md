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
| `config.py` | Read/write accessors (must be **functions** for hot-reload). `store_path` / `logs_dir` resolve relative paths against the `settings.json` directory (not cwd). |
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
| `proxy/server.py` | aiohttp streaming proxy with intercept loop (ToolSearch + managed tools) |
| `proxy/tool_search.py` | BM25 + regex search engine (zero deps) |
| `proxy/tool_registry.py` | Core/deferred tool splitting, ToolSearch + managed tool schema injection |
| `proxy/llm.py` | Generic `structured_call(prompt, schema)` — upstream LLM via `/v1/chat/completions` for proxy-internal use |
| `proxy/managed_tools.py` | Registry of proxy-handled tools (WebSearch, speak, transcribe); schemas + handlers + LLM hooks |
| `proxy/web_search.py` | Brave Search scraper + result formatter |
| `proxy/config.py` | Proxy settings (port, upstream, upstream_model, core tools, BM25 params, web_search, client_profiles, model_mapping) |
| `proxy/instructions/system.md` | Default Claude Code system instruction (tool_search path) |
| `proxy/instructions/office.md` | Office add-in profile system instruction |
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

## Proxy pipeline (`proxy/`)

Anthropic-API-compatible middleware for local models (LM Studio, Ollama, etc.). Every transform is an independent flag; profiles override per-client.

1. **Startup:** `start_proxy_background()` from `main.py:_post_init`. Port 1235 by default. Also runs standalone via `python -m proxy`.

2. **Profile matching:** `_match_profile(headers)` — first `client_profile` whose `match.header` contains `match.contains` wins. Each profile independently overrides any feature flag; unset fields fall back to global `proxy.*`.

3. **Model mapping** (`proxy.model_mapping`): rewrites `body.model` (e.g. `claude-opus-4-6` → `qwen3.5-35b-a3b`). Applied to both `/v1/messages` and `/v1/models`.

4. **Tool filtering** (profile-driven): `strip_tool_names` drops tools by exact name (Anthropic's hosted tool names are stable across versions, so `["web_search", "code_execution"]` catches every version). `strip_cache_control` (default `true`) removes the `cache_control` key LM Studio rejects. Runs before any splitting.

5. **Managed-tool injection** (`inject_managed`): list of names from the managed-tool registry. For each, the matching name + its `strip_from_cc` list are added to the strip set; its schema is injected into `body.tools`. Works whether or not `tool_search` is on. Managed tools are always intercepted in the loop (no separate flag — interception is the proxy's job).

6. **Tool search** (`tool_search: true`): self-contained feature. `split_tools()` in `tool_registry.py` takes `(tools, core_names, strip_names, inject_schemas)`. Tools in `core_names` stay core; `strip_names` are dropped; everything else becomes deferred. ToolSearch meta-tool is injected when deferred is non-empty, and is always intercepted by the loop. Deferred names are listed in a `<system-reminder>` injected into the first user message.

6a. **Auto-load** (`auto_load_tools: true`): when the model calls a deferred tool by name (no ToolSearch first), the proxy intercepts on the FIRST call only, injects the matched schema into `body["tools"]`, adds the name to a local `core_visible_names` set, and returns the schema to the model as a `tool_result` asking it to re-issue the call. On the SECOND call the name is in `core_visible_names` so auto-load is skipped; the tool_use flushes through to CC, which has the tool in its registry and executes it. The "fires once" guard prevents the infinite loop of the earlier implementation. Saves a model turn compared to manual ToolSearch.

6b. **Hallucination guard**: if the model calls a tool name that's neither core nor deferred nor managed (typo, made-up name), the proxy runs BM25 over `core + deferred` with the hallucinated name as query and returns the top-5 matches in the tool_result text — **does NOT inject their schemas** into `body.tools` (that would bloat context with 5 schemas when only 1 is needed). Model picks the right name from the suggestions and retries; on that retry, auto_load (if on) injects only the single matched schema. Fires regardless of tool_search setting — Office profiles benefit too.

6c. **Unloaded-tool guard (policy)**: when `auto_load_tools: false`, direct calls to deferred (unloaded) tools are blocked and the proxy returns a tool_result telling the model to load the schema via `ToolSearch(select:...)` first. This prevents “it worked anyway” cases where the client can execute tools the upstream model never received schemas for.

7. **System prompts** — two independent injections:
   - `system_instruction` (profile): prepends a markdown file from `proxy/instructions/` to `body.system`. Supports `<if dotted.key="value">...</if>` conditional blocks.
   - `inject_date_location`: appends current date + auto-detected location as a `<system-reminder>` to `body.system` (location from `proxy.location` override or `ip-api.com`).

8. **Message transforms** (independent flags):
   - `strip_reminders`: strip `<system-reminder>` blocks from message history.
   - `lift_tool_result_images`: lift image blocks out of array-form tool_results (LM Studio array-form workaround).

9. **Intercept loop** (`_handle_streaming` / `_handle_non_streaming`): buffers upstream SSE, detects tool calls, executes locally when needed, appends `[tool_use, tool_result]` to messages, retries upstream. Loops up to 15 rounds.
   - Always intercepted/handled: `ToolSearch` (when deferred tools exist) and any injected managed tools (e.g. `web_search`, `code_execution`).
   - **Auto-load** (`auto_load_tools: true`): deferred tool names are handled on first blind call to inject schema and request a retry.
   - **Unloaded-tool guard** (`auto_load_tools: false`): direct calls to deferred (unloaded) tools are blocked and the model is instructed to run `ToolSearch(select:...)` first.
   - Unknown tool names trigger the hallucination guard (BM25 suggestions over core+deferred).

   On clean response, flushes with visibility summaries prepended into the first text_delta — no new SSE blocks, no index changes, preserves prefix cache.

10. **Managed tools registry** (`proxy/managed_tools.py`): each `ManagedTool` has an Anthropic-format schema, async handler returning `(summary, result)`, optional `strip_from_cc` list, and optional `pre_llm`/`post_llm` `LLMHook`s that call `structured_call()` (`proxy/llm.py`) for arg enrichment / result post-processing. Currently registered: `WebSearch` (Brave scraper), `speak` (Kokoro TTS), `transcribe` (Whisper STT), `code_execution` (Python 3 subprocess sandbox — 30s timeout, `-I` isolated mode, no network, used by Office add-ins for PTC-style data work). Add a tool = `register(name, schema, handler, strip=[...], pre_llm=..., post_llm=...)` — zero changes to `server.py`.

11. **Web search** (`proxy/web_search.py`): scrapes `search.brave.com/search?q=...&source=web` directly. Parser targets stable CSS selectors (`div.snippet[data-type="web"]`, `div.title`, `div.content`). Only `data-type="web"` snippets — video/image clusters excluded. No cache, browser-realistic headers.

12. **CORS**: `cors_origins` list gates middleware. Streaming responses set headers via `_apply_cors_to_stream()` before `resp.prepare()` (aiohttp middleware can't mutate headers after prepare commits them — this was needed for Office add-ins in browser sandboxes).

13. **Passthrough:** non-`/v1/messages`, non-`/v1/models` requests forwarded unchanged. `/v1/models` fetches upstream (OpenAI format), converts to Anthropic format, prepends any `model_mapping` aliases.

To use: set `proxy.enabled: true` and point `ANTHROPIC_BASE_URL` at `http://localhost:1235`.

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

## MCP server (`mcp_server/`)

Streamable HTTP MCP server (FastMCP, port 1236). Drop-in tools/resources/prompts under `mcp_server/tools/`, `resources/`, `prompts/` — auto-discovered via `pkgutil.iter_modules`. Built-in tools: `speak` (Kokoro TTS), `transcribe` (Whisper STT), `web_search` (Brave scraper, same backend as the proxy's managed WebSearch). For local models routed through the proxy, these tools are injected automatically via `managed_tools.py` — no MCP connection needed. The MCP server is for external clients or Claude Code running against the real Anthropic API.

`claude mcp add telecode --transport streamable-http --url http://127.0.0.1:1236/mcp`

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
| ToolSearch not triggered | Model may not call it; check upstream is reachable; with `proxy.debug` on, inspect `data/logs/proxy_full_*.json` dumps |
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
