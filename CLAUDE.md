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

llama.cpp + dual-protocol proxy (for local models):
    bot startup
    -> LlamaSupervisor.start_default(): spawn llama-server with argv built
       from llamacpp.models.<default>.* (-m, --ctx-size, -ngl, --mmproj,
       draft model, cache types, chat_template, extra_args escape hatch)
    -> wait /health until "ok"
    -> proxy starts on http://127.0.0.1:1235

    client request
    -> /v1/messages (Anthropic) or /v1/chat/completions (OpenAI) — both
       routes active; protocol chosen by path
       /v1/models — shape chosen by header sniff (anthropic-version /
       x-api-key → Anthropic shape; else OpenAI shape)
    -> proxy translates to INTERNAL (OpenAI) shape via proxy/translate.py
       · cache_control stripped recursively
       · Anthropic content arrays → OpenAI content parts (text / image_url)
       · tool_use / tool_result → OpenAI tool_calls / role:"tool"
       · inference defaults merged from llamacpp.inference + per-model
    -> LlamaSupervisor.ensure_model(): swap process if body.model resolves
       to a different registered model (restart with new argv, wait /health)
    -> split_tools(): core + deferred (ToolSearch BM25); managed tools
       always injected; ToolSearch meta-tool present when deferred exists
    -> intercept loop on internal shape (proxy/server._run_streaming):
        · resp.prepare() + heartbeat (SSE comments 2s; Anthropic `event: ping`
          every `proxy.ping_interval` seconds for Anthropic clients)
        · write_lock shared across all round-trips
        · POST to llama.cpp /v1/chat/completions with stream=true
        · first content signal (tool_call.name or content delta) decides:
            intercepted name → assemble arguments, break round, run handler,
                              emit status line + append assistant+tool
                              messages, re-loop
            passthrough      → translate OpenAI SSE live to client
                              (AnthropicStreamState for Anthropic clients
                               reconstructs message_start / content_block_* /
                               message_delta / message_stop, mapping
                               <think>...</think> to `thinking` blocks via
                               ReasoningState; OpenAI clients get verbatim
                               chunks with model field rewritten to the
                               client's alias)
    -> status blocks:
        Anthropic client: synthetic text content blocks at indices 0..N-1
        OpenAI client:    synthetic chat.completion.chunk content deltas
    -> /v1/messages/count_tokens uses llama.cpp /apply-template + /tokenize
       — accurate, no max_tokens=1 round-trip hack
    -> /v1/embeddings forwarded verbatim to llama-server
```

- **Session key:** `{backend}:{name}` -- colon is the separator; do not use colons in names.
- **Session naming:** auto-numbered (`claude-1`, `claude-2`) when no name given. Screen/video sessions use the window title.
- **Routing:** only `message_thread_id` -> session. No other routing.
- **Persistence:** `store.py` JSON file -- topic id per `(user_id, session_key)`.
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
| `main.py` | App startup, handlers, `set_my_commands`. No background STT poll: voice.health state is updated by real `voice.stt.transcribe` calls. |
| `store.py` | Topics JSON |
| `sessions/process.py` | PTY + pyte + snapshot diff + timers |
| `sessions/screen.py` | Image capture, video recording, window enumeration |
| `sessions/computer.py` | Vision LLM computer control (capture + actions + LLM loop) |
| `sessions/manager.py` | Start/kill sessions, send, send_raw, interrupt, pause/resume |
| `bot/handlers.py` | Commands, callbacks, window pickers, capture controls |
| `bot/live.py` | `LiveMessage`, `FrameSender`, `TypingPinger`, per-chat flood backoff, overlap detection, HTML-escape-aware splitting |
| `bot/rate.py` | Stale session cleanup, topic probing, topic-gone detection |
| `bot/topic_manager.py` | Create/reuse forum topics |
| `bot/settings_handler.py` | `/settings` parsing |
| `backends/implementations.py` | `GenericCLIBackend` (data-driven) + Screen, Video (non-PTY) |
| `backends/registry.py` | Auto-built from `settings.json` tools; `get_backend`, `all_backends`, `refresh` |
| `backends/params.py` | Load tool params from settings |
| `voice/*` | STT transcribe + lazy health state. `voice.stt.transcribe()` calls `voice.health.record_success()` / `record_failure(reason)` after every request. No startup probe, no `probe_loop`. Default `stt_reachable=True` so the first voice message reaches the endpoint; a failure flips it to False and short-circuits future attempts until the next success. |
| `llamacpp/supervisor.py` | Spawns and babysits llama-server subprocess; model-swap on demand |
| `llamacpp/argv.py` | Builds llama-server CLI argv from `llamacpp.models.<m>` + `extra_args` |
| `llamacpp/config.py` | `llamacpp.*` settings accessors + model registry resolution |
| `proc_group.py` | Windows Job Object (`KILL_ON_JOB_CLOSE`) — binds every child PID we spawn (llama-server, Tailscale funnels, PTY CLIs) to this Python process's lifetime so the OS reaps everything if we die for any reason. Also `kill_process_tree(pid)` for graceful tree shutdown via `taskkill /T`. |
| `tray/app.py` | Qt tray launcher. `start_tray_in_thread(bot_app, bot_loop)` spawns a daemon thread that runs `QApplication.exec()` with a `QSystemTrayIcon` + `SettingsWindow`. Tray click → `window.toggle_visibility()`. Menu action async calls dispatch via `asyncio.run_coroutine_threadsafe(coro, bot_loop)`. Quit calls `bot_app.stop_running()` then `app.quit()`. |
| `tray/icon.py` | Solid white lightning-bolt, transparent background (4× LANCZOS supersample). Rendered to `QPixmap` at tray init. |
| `tray/qt_theme.py` | Single QSS dark theme. `QSS` string cascades to the menu AND the window. Palette constants exported for widget code. |
| `tray/qt_widgets.py` | Custom `Toggle` (animated pill switch, `QCheckBox` subclass) and `NumberEditor` (text input + slider, linked, emits `valueChanged(float)`). |
| `tray/qt_helpers.py` | Shared: `read_settings` / `patch_settings` (atomic write + `config.reload()`), `schedule(loop, coro)` for async dispatch, `humanize(tool_id)` for labels, `build_status()` for the live snapshot dict. |
| `tray/qt_window.py` | `SettingsWindow` — frameless `QMainWindow` with custom `TitleBar` (drag/minimize/maximize/hide), sidebar `QListWidget`, `QStackedWidget` for sections. Per-section refresh via a 1s `QTimer` that calls each cached page's optional `refresh()` method. |
| `tray/qt_sections.py` | `build(section_id, window)` dispatch. One builder per sidebar entry: Status tiles / llama (sampling sliders + reasoning + Load/Unload/Restart + **idle_unload via `_idle_unload_row()` — checkbox + spinbox composite; checkbox off → stores 0 → disabled; last nonzero value remembered across toggles**) / Proxy (Enabled + protocols + flag toggles + max_roundtrips/ping_interval) / MCP / Managed / Telegram (streaming + capture) / Voice / Computer / Sessions (live QTableWidget) / **Requests** (live `QListWidget` of recent proxy requests + foldable `QTreeWidget` JSON inspector on the right — colored by status, rebuilds only when rids change, in-place cell refresh for in-flight→finished transitions) / **Logs** (live-tailing `QPlainTextEdit` with `QSyntaxHighlighter` coloring timestamps, levels, `[logger.name]`, URLs, tracebacks; 1s `QTimer` appends only new bytes, handles rotation via size-shrink detection, initial load capped at last 512 KB). |
| `proxy/request_log.py` | Thread-safe ring buffer (`MAX_ENTRIES=200`) of recent proxy request dicts — `new_request` / `set_request_preview` / `finish` called from `proxy/server.py` route handlers. `snapshot()` feeds the Requests tray section. When `proxy.debug=true`, `finish` also writes `data/logs/requests/req_<ts>_<rid>.json`. |
| `proxy/runtime_state.py` | Persists managed-tool / MCP-tool toggles to `data/runtime-overrides.json`. `is_managed_enabled(name)` consulted by `proxy/server.py` before injecting; survives restarts. |
| `llamacpp/state.py` | Persists last-active llama model to `data/llama-state.json`. Used as the implicit default when a request omits `model`. NOT auto-loaded on startup unless `llamacpp.auto_start: true`. |
| `proxy/__main__.py` | Standalone entry: `python -m proxy` |
| `proxy/server.py` | Dual-protocol (Anthropic + OpenAI) aiohttp proxy with intercept loop |
| `proxy/translate.py` | Anthropic ↔ OpenAI shape conversions; ReasoningState `<think>` state machine; AnthropicStreamState that reconstructs message_start / content_block_* / thinking / tool_use / message_stop from OpenAI SSE |
| `proxy/tokenizer.py` | Wrapper around llama.cpp `/tokenize` + `/apply-template` for accurate count_tokens |
| `proxy/tool_search.py` | BM25 + regex search engine (zero deps) |
| `proxy/tool_registry.py` | Core/deferred tool splitting, ToolSearch meta-tool schema, system instruction loading |
| `proxy/llm.py` | `structured_call(prompt, schema)` — llama.cpp via `/v1/chat/completions` for proxy-internal use |
| `proxy/managed_tools.py` | Registry of proxy-handled tools (WebSearch, speak, transcribe); schemas + handlers + LLM hooks |
| `proxy/web_search.py` | Brave Search scraper + result formatter |
| `proxy/config.py` | Proxy settings (port, protocols, CORS, core tools, client_profiles, model_mapping) |
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
4. **Processes** -- real PTY (Unix `openpty`, Windows ConPTY via pywinpty), not plain pipes. Screen capture uses PrintWindow (Win) / screencapture (Mac) / import (Linux), not PTY. Computer control uses `pyautogui` for mouse/keyboard. llama-server is spawned and babysat by `LlamaSupervisor`; do not start it manually in the same lifecycle.
5. **Telegram** -- `ParseMode.HTML`; escape user/process text with `html.escape()` where needed.
6. **No** in-bot AI and **no** separate "memory" layer -- CLIs own context.
7. **`cache_control`** -- always stripped in the translator; never a per-profile toggle. llama.cpp does its own slot-based KV caching; Anthropic's cache_control metadata has no meaning downstream.
8. **Internal canonical shape is OpenAI.** All intercept-loop logic works on OpenAI tools / tool_calls / role:"tool" messages. Protocol-specific concerns live only in the two `ClientAdapter` subclasses and in `proxy/translate.py`.

---

## PTY output pipeline (`sessions/process.py`)

1. Raw bytes -> **pyte** `HistoryScreen` + `Stream`.
2. Each snapshot = **history lines** + **display lines** (one top-to-bottom list).
3. Compare to **previous** full list: find **new** lines only (patience/histogram anchors + segment diff + "similar line" filter so spinners/status lines do not spam).
4. Emit chunks to subscribers on **idle** (defaults 2s) or **max wait** (defaults 5s); poll safety net every 5s.
5. **Input:** `send()` appends `\r` (not `\n`) so TUIs accept the line.

Both thresholds are tunable per-backend via `tools.<key>.streaming.{idle_sec,max_wait_sec}`, falling back to the global `streaming.{idle_sec,max_wait_sec}` in `settings.json`. Loaded by `backends/params.py` → `BackendParams.idle_sec` / `max_wait_sec` → `PTYProcess(..., idle_sec=..., max_wait_sec=...)`. Short-output shells (`shell`, `powershell`) flush faster than TUIs like Claude Code; tune per-tool to taste.

Other tunables near top of `process.py`: screen rows/history size.

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
5. Sends encoded MP4 via `send_video` with `_capture_controls_kb` (⏸/▶/⏹) attached to each chunk, then starts the next chunk.
6. Continues until the inline ⏹ Stop button or `/stop`. On stop, encodes and sends any remaining frames.
7. Pause: recording loop sleeps, paused time doesn't count towards chunk duration. Triggered via the ⏸ Pause inline button.
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

## Child-process lifetime (`proc_group.py`)

Every subprocess we spawn — llama-server, Tailscale funnel processes, PTY-driven CLIs (Claude Code, Codex, bash, powershell) — gets bound to a single process-wide Windows Job Object created lazily on first call to `bind_to_lifetime_job(pid, proc=…)`. The job has the `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` flag, so when this Python interpreter exits **for any reason** (Ctrl+C, Task Manager kill, pythonw crash, OS shutdown), the OS releases the job handle, the job closes, and every member process is terminated by the kernel.

Belt-and-braces:
- `atexit.register(_atexit_kill_all)` — covers clean exits where pywin32 isn't available; calls `proc.kill()` on every tracked Popen / asyncio.subprocess.Process.
- `kill_process_tree(pid, force=False)` — graceful path. Uses `taskkill /PID <pid> /T` (`/F` if force) on Windows or `os.killpg` on Unix. Walks the whole process tree, so workers spawned by the immediate child also die.

The supervisor's `_stop_locked` calls `kill_process_tree` first (graceful), waits 4s, then `kill_process_tree(force=True)`. Sessions/process.py wires PTY children via `bind_to_lifetime_job`. Tailscale funnels in main.py do the same.

If a tracked subprocess refuses to die: check `tasklist /FI "IMAGENAME eq llama-server.exe"` after telecode exits — should be empty within ~2s. If not, the Job Object didn't take (look for `proc_group: could not create Job Object` in `data/logs/telecode.log`) — usually a missing `pywin32` install.

---

## System tray UI (`tray/`)

Native pystray menu running in a daemon thread inside the bot process.
There is **no separate tray process, no webview, no HTTP RPC, no PyInstaller
bundle** — `python main.py` (or `pythonw main.py` for no console) starts
both the Telegram bot and the tray together.

Architecture:
- `main.py:_post_init` calls `tray.app.start_tray_in_thread(app, loop)` after
  the bot is initialized.
- pystray runs on a daemon thread; menu callbacks fire on that thread.
- Sync actions (settings patch, log open) run directly.
- Async actions (model swap, session kill) use
  `asyncio.run_coroutine_threadsafe(coro, bot_loop)` to schedule onto the
  Telegram bot's asyncio loop.
- Quit → `app.stop_running()` scheduled on the loop → `run_polling` returns
  → process exits cleanly.

Menu structure (every label is Title Cased; tool IDs like `web_search`
get `_humanize()`d to `Web Search` for display only):

Right-click tray menu — each subsystem gets its own submenu, populated by
the 2s `_refresh_info` timer in `tray/app.py`:

```
⬡/⬢ Llama ▸ Status line + Active model + Auto Start toggle
            + Load / Unload / Restart (enabled per supervisor state)
⬡/⬢ Proxy ▸ Status line (port) + Protocols + Enabled toggle
            (⟳ restart required) + Debug Dumps toggle
⬡/⬢ MCP   ▸ Status line (port) + Tool count + Enabled toggle
            (⟳ restart required)
⬢   Bot   ▸ Sessions alive/total + group_id + allowed_user count
            (status-only — bot is the host process)
─
Open Settings Window (default left-click)
─
Quit Telecode
```

Reload / Open settings.json / Open Logs Folder were removed from the tray
menu — all three surfaces are inside the Settings window (Logs section =
built-in live tailing viewer, Requests section = JSON tree inspector, and
any settings patch already triggers `config.reload()` atomically).


Conditional disabling via `enabled=callable`:
- Subsystem submenu items grey out when `<subsystem>.enabled = false`.
- Load disabled when alive; Unload/Restart disabled when not alive.
- Show Reasoning disabled when Parse OFF; both disabled when Use Reasoning OFF.

Persistence:
- All boolean/preset toggles → `_patch_settings()` writes settings.json
  atomically + calls `config.reload()` → effective on next reader's call.
- Managed/MCP-tool toggles → `proxy/runtime_state.py` →
  `data/runtime-overrides.json`.
- Last-active llama model → `data/llama-state.json` (used as implicit
  default; not auto-loaded on startup unless `llamacpp.auto_start: true`).

pystray gotcha: `_assert_action` rejects callables with
`co_argcount > 2`. The pattern `lambda _i, _it, key=val: ...` has
argcount=3 (defaults count). Use `TrayApp._act(fn, *bound)` which
returns a clean 2-arg lambda.

---

## llama.cpp supervisor (`llamacpp/`)

1. **Spawning:** `LlamaSupervisor.start_default()` runs from `main.py:_post_init` BEFORE the proxy. `llamacpp.binary` → `shutil.which` → `subprocess.Popen`. stdout+stderr merged into `data/logs/llama.log` (append mode, one `===== spawning =====` banner per restart).

2. **argv builder** (`llamacpp/argv.py`): walks `llamacpp.models.<model>` and emits flags via a table-driven mapper — `ctx_size → --ctx-size`, `n_gpu_layers → --n-gpu-layers`, `flash_attn → --flash-attn`, `mmproj → --mmproj`, `draft_model → --model-draft`, `chat_template`, `jinja`, `cache_type_k/v`, `slot_save_path`, LoRA, grammar, etc. Anything not special-cased goes through `extra_args: [["--flag","value"]]` verbatim. Both per-model and top-level `llamacpp.extra_args` are honored.

3. **Model swap:** `ensure_model(name)` resolves the request's model through `llamacpp.models` → `proxy.model_mapping` → `default_model`. If the resolved key differs from the running model, the supervisor stops and respawns with the new argv; `/health` is polled until `"ok"`. Swap latency surfaces as normal generation time — heartbeat pings already cover it.

4. **Ready probe:** `/health` status `"ok"` = loaded + ready; `503` / `"loading model"` = still warming up; connection errors = not up yet. Deadline = `llamacpp.ready_timeout_sec` (default 120).

5. **Shutdown:** `shutdown_supervisor()` in `main.py:_post_shutdown` sends SIGTERM (Win: `terminate()`), waits 4s, then `kill()`. Fired AFTER the proxy runner so no request is mid-flight.

---

## Proxy pipeline (`proxy/`)

Dual-protocol middleware in front of llama.cpp. Both **Anthropic** `/v1/messages` and **OpenAI** `/v1/chat/completions` are exposed to clients; internally everything is canonicalised to OpenAI shape (llama.cpp's native format) before hitting upstream. Every transform is an independent flag; profiles override per-client.

1. **Startup:** `start_proxy_background()` from `main.py:_post_init`, AFTER the supervisor. Port 1235 by default. Also runs standalone via `python -m proxy` (supervisor spawn must already be running in that mode).

2. **Protocols** (`proxy.protocols`): `["anthropic", "openai"]` by default — both route sets registered. Disabling one unregisters its routes. `/v1/models` stays dual and picks shape by header sniff (`anthropic-version` / `x-api-key` → Anthropic, else OpenAI).

3. **Profile matching:** `_match_profile(headers)` — first `client_profile` whose `match.header` contains `match.contains` wins. Each profile independently overrides any feature flag; unset fields fall back to global `proxy.*`.

4. **Model mapping** (`proxy.model_mapping`): rewrites `body.model` (e.g. `claude-opus-4-6` → `qwen3.5-35b`). `body.model` is resolved via `llama_cfg.resolve_model()` (registry → mapping → default). Response `model` is reverse-mapped back to the client's alias.

5. **Client-body translation** (`proxy/translate.py`):
   - **anthropic_request_to_internal**: Anthropic blocks → OpenAI content parts (text/image_url). Tool_use → assistant `tool_calls`. Tool_result arrays → `role:"tool"` string content + lifted user message with image parts (OpenAI `tool` role requires string content; images ride alongside). `cache_control` dropped recursively. `system` (string or list) flattened into leading `{"role":"system"}`.
   - **openai_request_to_internal**: near-identity; applies inference defaults, sets `stream_options.include_usage=true`, `cache_prompt=true`.
   - Inference defaults merged from `llamacpp.inference` + per-model `llamacpp.models.<m>.inference_defaults`, overridable by request body.

6. **Managed-tool injection** (`inject_managed`): registry names + their `strip_from_cc` list become a strip set; Anthropic-shape schemas are converted to OpenAI-shape tools and injected. Always intercepted.

7. **Tool search** (`tool_search: true`): `_apply_tool_transforms` splits OpenAI-shape tools into core + deferred (BM25 haystack in Anthropic shape). `ToolSearch` meta-tool injected when deferred is non-empty. Deferred names listed in a `<system-reminder>` appended to the first user message.

   - **Auto-load** (`auto_load_tools: true`): first blind call to a deferred name triggers the proxy to return its schema as a tool_result (schema also added to `body.tools` / `core_visible_names`); model retries, next call flushes through to the client.
   - **Unloaded-tool guard** (`auto_load_tools: false`): blocks direct calls to deferred names and instructs `ToolSearch(select:Name)`.
   - **Hallucination guard**: tool names not in `core_visible ∪ deferred ∪ managed ∪ ToolSearch` are intercepted; BM25 over `core + deferred` with the bogus name as query returns the top-5 suggestions in a `<functions>` block. No schemas auto-injected (would bloat context).

8. **System prompts** — two independent injections:
   - `system_instruction` (profile): prepends a markdown file from `proxy/instructions/` to the leading system message. `<if dotted.key="value">...</if>` conditionals supported.
   - `inject_date_location`: appends current date + location as a `<system-reminder>` (location from `proxy.location` or `ip-api.com`).

9. **Message transforms**:
   - `strip_reminders`: strip `<system-reminder>` blocks (keeps skills + our deferred-tools listing).
   - `cache_control` stripping: always on; not a knob anymore.
   - Tool-result image handling: always on; images lifted into a follow-on user message so llama.cpp's vision path sees them.

10. **Intercept loop** (`_run_streaming` / `_run_non_streaming`): operates on internal (OpenAI) shape. Each round-trip calls `_run_upstream_round`, which reads OpenAI SSE from llama.cpp and **branches on the first content signal**:
    - First `tool_call` with an intercepted (or hallucinated) name → keep assembling arguments until `finish_reason=tool_calls`, return `InterceptedToolCall`. Nothing written to client.
    - Anything else (text delta / non-intercepted tool_call / finish) → stream live through the adapter.

    `_start_heartbeat` runs for the full request lifetime; per-protocol:
    - Anthropic clients: `: keepalive\n\n` every 2s + `event: ping\ndata: {"type":"ping"}\n\n` every `proxy.ping_interval` (10s) — CC / pivot / Office add-ins recognize the ping and don't time out.
    - OpenAI clients: `: keepalive\n\n` only (no ping in the OpenAI SSE spec).

    Loops up to `proxy.max_roundtrips` (default 15). Always intercepted: `ToolSearch` (when deferred exists), all injected managed tools, all deferred names (auto-load or unloaded-guard).

11. **Client adapters** (`ClientAdapter` subclasses):
    - `AnthropicAdapter`: owns an `AnthropicStreamState` per round, which wraps a `ReasoningState` (the `<think>...</think>` state machine). Status lines = synthetic text content blocks at indices `0..status_emitted-1`; real blocks start at `status_emitted`. Stream state rebuilds `message_start` / `content_block_start` / `content_block_delta` / `message_delta` / `message_stop` events from OpenAI chunks. `<think>` openers across delta boundaries are handled via a max-tag-length lookahead buffer. `thinking_delta` blocks emitted when `emit_thinking_blocks=true` (default), stripped when false.
    - `OpenAIAdapter`: near-identity for chat.completion.chunk events. Rewrites `model` → client alias, unifies `id` across round-trips. Status lines = synthetic assistant content-delta chunks (renders as prepended text in OpenAI-speaking UIs).

12. **Intercept handlers** — five branches in `_run_streaming`:
    - **`ToolSearch`** → BM25 over deferred. Status: `● ToolSearch("q") / └  N schemas loaded: A, B, C` or `└  No matches`.
    - **Managed tools** (`is_managed`) → `pre_llm` → `handler` → `post_llm`. Status: `format_visibility(name, input, summary)`. Errors: `● Tool() / └  Failed: <exc>`.
    - **Auto-load first blind call** — deferred name not yet loaded. Schema added, model told to retry. Status: `● Loaded ToolName / └  Schema delivered · awaiting retry`.
    - **Unloaded-tool guard** — blocks the call; instructs `ToolSearch(select:...)`. Status: `● Blocked: ToolName (unloaded) / └  Model instructed to ToolSearch first`.
    - **Hallucination guard** — top-5 BM25 suggestions returned (no schemas). Status: `● Unknown tool: X / └  Suggested: A, B, C` or `└  No close matches · model told to ToolSearch with keywords`.

13. **Token counting** (`/v1/messages/count_tokens`): runs the full prepare pipeline, then calls llama.cpp `/apply-template` → `/tokenize`. Exact, no generation. Replaces the old `max_tokens=1` round-trip hack.

14. **Embeddings** (`/v1/embeddings`): forwarded to llama-server verbatim. Use for RAG etc.

15. **Managed tools registry** (`proxy/managed_tools.py`): unchanged — `ManagedTool` with Anthropic-format schema, async handler `(input) -> (summary, result)`, optional `strip_from_cc`, optional `pre_llm`/`post_llm` `LLMHook`. MCP tools auto-bridged.

16. **CORS**: `cors_origins` list. Streaming responses get headers via `_apply_cors_to_stream()` before `prepare()`.

To use: set `llamacpp.enabled: true` + `proxy.enabled: true`, fill in `llamacpp.binary` and `llamacpp.models.<name>.path`, point client tools at `http://localhost:1235` (Anthropic clients: `ANTHROPIC_BASE_URL`; OpenAI clients: `OPENAI_BASE_URL` or `base_url`).

---

## Live Telegram messages (`bot/live.py`)

Delivery layer lives in `bot/live.py`; `bot/handlers.py` only imports and wires.

- **`LiveMessage`:** one text message per "turn", updated by `append()`. First chunk of a turn edits the message immediately (no debounce); subsequent chunks coalesce on a ~1s debounce so Telegram's per-chat edit rate isn't exceeded. Overflow loops into fresh messages — no head-truncation fallback, nothing is ever silently dropped. `_safe_split` uses cumulative escape-count prefix sums + binary search (O(n + log n), not the old quadratic step-back). Overlap with prior text is trimmed by `find_overlap_end`, a Z-algorithm scan over the non-whitespace projection.
- **`finalize()` retry:** if the last `_do_edit` didn't land (full_text != _last_sent), schedules one more `_do_edit` 2s later so transient Telegram errors don't freeze a turn at a truncated reply.
- **`TypingPinger`:** started in `LiveMessage.__init__`, re-sends `sendChatAction("typing")` every 4s until the first reply message is created, then stops. Also stops on topic-gone, on `finalize()`, or after a 60s hard cap so a turn with no PTY output can't leak the ping loop.
- **Placeholder under flood:** `_ensure_msg` does NOT preemptively bail when the chat is flood-backed off — the send attempt either succeeds or surfaces `RetryAfter` (which sets the per-chat backoff). Preemptive bailing was stranding turns that produced only one short chunk.
- **Per-chat flood:** `flood_active(chat_id)` / `set_flood_backoff(chat_id, retry_after)` — state is `dict[chat_id, float]`. A flood in one chat no longer throttles edits in another.
- **`FrameSender`:** sends each frame as a **new photo message**. Send interval = `capture.image_interval`. Drops frames while paused. Inline buttons (⏸ Pause / ▶ Resume / ⏹ Stop) — callbacks `cap_pause:` / `cap_resume:` / `stop:` (see `_capture_controls_kb`). `controls_kb_factory` + `track_controls` are passed in at construction so this module doesn't import `bot/handlers.py` back.
- **Latest-message-only controls** (in `bot/handlers.py`): `_track_controls(bot, msg)` keeps a per-thread pointer (`_latest_controls_msg: dict[thread_id, message_id]`) to the most recent inline-keyboard message. Every site that sends `reply_markup=…` — `/start` picker, `/new` usage picker, dead-session picker, window picker, capture/video startup messages, each `FrameSender` photo, each video chunk — calls `_track_controls`, which silently strips the keyboard from the previously tracked message via `edit_message_reply_markup(reply_markup=None)` before recording the new one. Pause/Resume callbacks use `q.edit_message_reply_markup` (same message_id) so the tracker is still valid. Errors are swallowed (message gone / too old / unchanged).

---

## Logging & crash traces (`main.py`)

- Log file: `data/logs/telecode.log`. On startup it is **rotated to `telecode.log.prev`**, not deleted — so after a crash + restart the previous run's traceback survives in `.prev`.
- `_install_crash_handlers` installs `sys.excepthook` + `threading.excepthook`; `_install_asyncio_exception_handler` attaches a loop-level handler in `_post_init`. These route uncaught exceptions (including task exceptions that were never awaited) into `telecode.log`. Essential under `pythonw`, where `sys.stderr` goes nowhere.
- `run_polling` is wrapped in a try/except that logs a `CRITICAL Bot crashed: …` line before re-raising — so a fatal error at the polling layer lands in the log before the process exits.
- When debugging a crash, always check `data/logs/telecode.log.prev` first; the current `telecode.log` is from *after* the restart and won't have the failing run's trace.

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
  "session": {},
  "streaming": { "idle_sec": 0.5, "max_wait_sec": 2.5 }
}
```

The registry auto-creates a `GenericCLIBackend` for any key that isn't a special non-PTY backend (`screen`, `video`).
`name` and `icon` are optional — defaults to title-cased key and 🔧.
`streaming` is optional — overrides the global `streaming.idle_sec` / `streaming.max_wait_sec`. Short-output shells benefit from tighter values (~0.5s / 2.5s) than TUIs like Claude Code (the defaults 2s / 5s).

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
| Bot silent | Token, group id, bot admin, Topics on. Also check `data/logs/telecode.log.prev` for the previous run's crash trace |
| Bot stops after a while | Read `data/logs/telecode.log.prev` — crash handlers route uncaught exceptions and `run_polling` failures there |
| "No session for thread" | `/new` again; store may be missing mapping |
| CLI exits at once | Missing API key, wrong `startup_cmd`, binary not on PATH |
| Stuck on prompt | User sends `/key enter` or `/key y` |
| Garbled / noisy stream | TUI limitation; tune diff in `process.py` or use a non-interactive CLI mode |
| `settings` change ignored | `/settings reload` or restart |
| Screen capture blank | Window may be on another virtual desktop; minimized windows are auto-restored |
| Video encoding fails | Ensure ffmpeg is on PATH; check logs for ffmpeg stderr |
| Computer control clicks wrong spot | DPI scaling issue; check `_get_window_rect` returns logical coords |
| Computer control LLM error | Check llama-server is up and the proxy is running; `base_url` should point at the proxy (`http://localhost:1235/v1`), not llama-server directly |
| Voice not working | Start STT service and send a voice message. First request hits the endpoint directly; no background probe means no "wait 60s" delay, but also no passive liveness. |
| Proxy not starting | Check `proxy.enabled` is `true` in settings.json; check port not in use |
| llama-server won't start | Check `data/logs/llama.log` for the actual binary output. Verify `llamacpp.binary` path and `llamacpp.models.<default>.path` point at real files |
| Model swap hangs | `llamacpp.ready_timeout_sec` (default 120) too short for a large GGUF to load; increase it |
| `<think>` blocks leak into text | Per-model `llamacpp.models.<m>.inference_defaults.reasoning.start/end` tags must match what the model actually emits |
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
