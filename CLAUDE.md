# CLAUDE.md — Telecode developer guide

Telegram bot that runs CLI tools (Claude Code / Codex / shell) inside a pseudo-terminal, reads pyte snapshots, posts text to forum-topic threads. Also: screen image / video capture, vision-LLM computer control, llama.cpp + dual-protocol proxy, an in-process Qt tray, and a DocGraph host supervisor.

User-facing docs in [README.md](README.md).

---

## Architecture (one-liners)

```
PTY: handler → SessionManager.send → PTYProcess.send → pyte → snapshot diff →
     subscribers → _LiveMessage.append → editMessageText (HTML <pre>)

Image:  /new screen  → ScreenCapture(hwnd)  → capture_window → JPEG → _FrameSender.send_photo
Video:  /new video   → VideoCapture(hwnd)   → frames → ffmpeg → MP4 chunks → send_video
Vision: /new computer → ComputerControl     → screenshot → vision LLM JSON → pyautogui → loop

llama.cpp + proxy: bot startup → LlamaSupervisor → /health ok → proxy on :1235
                   client req → translate to OpenAI → model swap → intercept loop → SSE back

Agent → Job → Run pipeline (Team Mode):
  Agent owns 5 markdown files in data/agents/<id>/internal/ (SOUL/USER/AGENT/MEMORY/HEARTBEAT).
  Job.pipeline = {mode, steps:[{agent_id, prompt_override, depends_on_text, phase}]}
  Phases run sequentially; same-phase steps run in parallel.
  Run executor: stage_for_run() copies SOUL/USER/MEMORY → workspace/, AGENT.md → CLAUDE.md/GEMINI.md;
                queue.submit_task; on exit writes back any modified files; unstage.

Heartbeat scheduler (off by default, settings.heartbeat.enabled=true):
  per agent each tick: parse HEARTBEAT.md (yaml fences) → reconcile to kind="heartbeat" Jobs
  → fire due+enabled entries; ephemeral=create+delete-after, persistent=submit on entry.workspace_id
```

- **Session key:** `{backend}:{name}` — colon is the separator; no colons in names.
- **Routing:** only `message_thread_id` → session.
- **Persistence:** `store.py` JSON file — topic id per `(user_id, session_key)`.
- **PTY working dir:** always `Path.home()` via `config.pty_cwd()`.

## Session cleanup

- **Fast** (`cleanup_stale_sessions`): every picker click. Checks `process.alive`. No API calls.
- **Full** (`full_cleanup`): on `/start` only. Probes each topic via `sendMessage`+`deleteMessage` to detect externally deleted topics.
- **Topic deleted externally:** caught by `_LiveMessage` / `_FrameSender` / video callbacks → `handle_topic_gone()`.
- **`/stop`:** in a session topic = stops that one; in General with no args = all; `/stop <name>` = specific.

---

## Key files

| Path | Role |
|---|---|
| `settings.json` | Only config source. |
| `config.py` | Read/write accessors (always functions for hot-reload). `store_path` / `logs_dir` resolve relative to the `settings.json` directory. |
| `main.py` | App startup, handlers, `set_my_commands`. No background STT poll. |
| `store.py` | Topics JSON. |
| `sessions/terminal.py` | PTY + pyte + snapshot diff + timers. |
| `sessions/screen.py` | Image capture, video recording, window enumeration. |
| `sessions/computer.py` | Vision-LLM computer control loop (capture + actions + LLM). |
| `sessions/manager.py` | Start/kill sessions, send/send_raw, interrupt, pause/resume. |
| `bot/handlers.py` | Commands, callbacks, window pickers, capture controls. |
| `bot/live.py` | `LiveMessage`, `FrameSender`, `TypingPinger`. Per-chat flood backoff, overlap detection, HTML-aware splitting. |
| `bot/rate.py` | Stale session cleanup, topic probing. |
| `bot/topic_manager.py` | Create/reuse forum topics. |
| `bot/settings_handler.py` | `/settings` parsing. |
| `backends/implementations.py` | `GenericCLIBackend` (data-driven) + Screen / Video (non-PTY). |
| `backends/registry.py` | Auto-built from `settings.json` tools. |
| `voice/*` | STT transcribe + lazy health (per-call success/failure tracking, no startup probe). |
| `process.py` | All subprocess lifecycle: Windows Job Object (`KILL_ON_JOB_CLOSE`) binding every spawned child to this Python's lifetime; `kill_process_tree(pid)`; `sweep_port()` (cmdline-aware orphan killer); `LlamaSupervisor` (readiness probe + post-`/health` stability guard, inflight-gated idle unload, model swap). |
| `llamacpp/argv.py`, `config.py`, `state.py` | Build llama-server argv + read settings + persist last-active model. |
| `tray/app.py` | Qt tray on a daemon thread. Async actions via `run_coroutine_threadsafe(coro, bot_loop)`. |
| `tray/qt_window.py` | `SettingsWindow` — frameless `QMainWindow` with sidebar + `QStackedWidget`. Per-section refresh on a 1s `QTimer`. |
| `tray/qt_sections.py` | Builders for Status / llama / Models / Proxy / MCP / Managed / **DocGraph** / Tools / Telegram / Voice / Computer / Sessions / Requests / Logs / Raw. |
| `tray/qt_docgraph.py` | DocGraph settings panel (Host / Roots / LLM / Embeddings / Reranker cards). Per-row stats chip + progress bars. Restart-host buttons inside cards whose settings need a host respawn. |
| `docgraph/config.py` | Accessors over `settings.docgraph.*` — `host_*`, `roots()` / `root_paths_to_watch()`, `llm_*`, `embeddings_*`, `rerank_*`, `index_workers`, `documents_*`. |
| `docgraph/process.py` | One `HostSupervisor` (single `docgraph host --root … [--watch …] --port <N>` child). `IndexRunner` POSTs `/api/admin/index?root=<slug>` when the host is alive; subprocess fallback when down. **All config goes through CLI flags** — no `DOCGRAPH_*` env vars. Long-form prompt overrides written to `data/runtime/*.txt` and passed via `--llm-prompt-*-file`. |
| `docgraph/bridge.py` | MCP-client → managed_tools registration. `bridge_host(host, port)` opens a streamable-HTTP session against `/mcp`, calls `list_tools()`, registers each as `docgraph_<tool>` (no per-root prefix — agents pass `root=<slug>` per call). |
| `docgraph/{stats,index,wiki,progress}_state.py` | TTL'd in-memory caches for the tray. `stats_state` dedups parallel fetches via in-flight set. |
| `proxy/server.py` | Dual-protocol (Anthropic + OpenAI) aiohttp proxy with intercept loop. |
| `proxy/translate.py` | Anthropic ↔ OpenAI shape conversions. `ReasoningState` `<think>` machine. `AnthropicStreamState` rebuilds `message_start` / `content_block_*` / `thinking` / `tool_use` / `message_stop` from OpenAI SSE. |
| `proxy/tokenizer.py` | Wraps llama.cpp `/tokenize` + `/apply-template` for accurate `count_tokens`. |
| `proxy/tool_search.py`, `tool_registry.py` | BM25 + regex search engine; core/deferred tool splitting; `ToolSearch` meta-tool. |
| `proxy/managed_tools.py` | Registry of proxy-handled tools (WebSearch, speak, transcribe). MCP tools auto-bridged. |
| `proxy/api_{sessions,tasks,agents,jobs,runs}.py` | Pythonmagic-style task/agent/job/run REST surface. |
| `proxy/runtime_state.py` | Persists managed/MCP-tool toggles to `data/runtime-overrides.json`. |
| `services/task/staging.py` | `stage_for_run()` ctx-mgr — copies SOUL/USER/MEMORY → workspace/, AGENT.md → workspace/CLAUDE.md or GEMINI.md (engine-dependent). On exit, diffs vs snapshot, writes back modified files, deletes staged. Per-workspace `threading.Lock`. HEARTBEAT.md is intentionally NOT staged. |
| `services/run/executor.py` | Pipeline driver thread per Run. Phase-based; single-step phase = job workspace, multi-step = ephemeral session per step. Threads outputs via `<previous_output>` / `<previous_outputs>`. |
| `services/heartbeat/{parser,state,reconcile,scheduler}.py` | YAML-fence parser, atomic JSON state, sync HEARTBEAT.md → `kind:"heartbeat"` Jobs, async tick loop. |
| `mcp_server/app.py`, `tools/*` | FastMCP (stateless streamable HTTP, port 1236). Drop-in tools/resources/prompts auto-discovered. |

---

## Rules (do not break)

1. **Config** — only `settings.json`; no scattered env vars except `TELECODE_SETTINGS`.
2. **`config.py`** — always `config.foo()`, never cached module-level constants for values that can change.
3. **Sessions** — key format `backend:name`; routing by `thread_id` only.
4. **Processes** — real PTY (Unix `openpty`, Windows ConPTY via pywinpty). llama-server is owned by `LlamaSupervisor`; do not spawn it manually.
5. **Telegram** — `ParseMode.HTML`; escape user/process text with `html.escape()`.
6. **No** in-bot AI and **no** separate "memory" layer — CLIs own context.
7. **`cache_control`** — always stripped in the translator; never a per-profile toggle.
8. **Internal canonical shape is OpenAI.** All intercept-loop logic works on OpenAI tools / tool_calls. Protocol-specific concerns live only in the two `ClientAdapter` subclasses and `proxy/translate.py`.
9. **DocGraph configuration is CLI-flag-only.** No `DOCGRAPH_*` env vars in our spawn calls — every knob is a `--flag`.

---

## PTY output (`sessions/terminal.py`)

Raw bytes → pyte `HistoryScreen` + `Stream` → snapshot = history + display lines → diff vs previous (patience anchors + segment diff + similar-line filter so spinners don't spam) → emit on idle (default 2s) or max-wait (5s); poll every 5s. `send()` appends `\r` (not `\n`) so TUIs accept the line. Tunable per-tool via `tools.<key>.streaming.{idle_sec,max_wait_sec}`.

## Screen image capture (`sessions/screen.py`)

`enumerate_windows()` is platform-specific (Windows: `EnumWindows` + `DwmGetWindowAttribute(DWMWA_CLOAKED)`; Linux: `wmctrl`/`xdotool`; macOS: `CGWindowListCopyWindowInfo`). `capture_window()` uses `PrintWindow` (Windows, z-order independent), `import` (Linux, fallback `mss`), or `screencapture` (macOS, fallback `mss`). Frames pushed to subscribers every `capture.image_interval` seconds; `_FrameSender` sends each as a new photo. Session 0 (Windows service) spawns a helper in the user's session via `WTSQueryUserToken` + `CreateProcessAsUser`.

## Screen video (`sessions/screen.py`)

`VideoCapture(hwnd, duration=capture.video_interval, fps=3)` saves numbered JPEGs, encodes with `ffmpeg libx264 -preset ultrafast -crf 32 -pix_fmt yuv420p`, sends MP4 with `_capture_controls_kb`. `scale=trunc(iw/2)*2:trunc(ih/2)*2` for libx264 even-dim requirement.

## Computer control (`sessions/computer.py`)

`ComputerControl(hwnd)` is duck-type compatible with PTYProcess/ScreenCapture. `hwnd=0` (sentinel `FULL_SCREEN_HWND`) = entire screen via `mss`. Mouse cursor drawn onto every screenshot as a red crosshair. Coordinates: screenshots are physical pixels, window rect is logical pixels — the ratio handles DPI scaling. `pyautogui` receives logical coords. Action loop: capture → vision LLM (structured `{thought, done, action}`) → `pyautogui` action → post-action capture (edited photo in place) → loop until `done=true` or new user message. `wait` actions handled async, capped at 30s. LLM API supports openai / anthropic / claude-code wire formats (toggled by `api.format`); claude-code uses `--resume` + `--json-schema` and forwards `base_url`/`api_key`/`model` as `ANTHROPIC_*` env vars to the subprocess. First screenshot = new photo; subsequent = `edit_message_media`.

---

## Subprocess lifecycle (`process.py`)

**Generic primitives:**
- **Windows Job Object** — every spawn (`bind_to_lifetime_job(pid, proc=…)`) bound to a process-wide Job flagged `KILL_ON_JOB_CLOSE`. When this Python exits for any reason, the OS releases the handle and every member dies.
- **atexit fallback** for clean exits without pywin32.
- **`kill_process_tree(pid, force=False)`** — graceful first; `taskkill /T` on Windows, `killpg` on Unix.
- **`sweep_port(port, whitelist)`** — kills orphans holding a port whose exe **or** cmdline matches the whitelist. Foreign listeners are logged, not killed.

**`LlamaSupervisor`** — one active llama-server, model-swap via `ensure_model(name)` under an asyncio lock. `_wait_ready` polls `/health`, checks `proc.poll()` per iteration, and after `status:"ok"` waits 1s and re-polls to catch an orphan that would fake readiness on the same port. Inflight-gated idle unload (`begin_request` / `end_request` + watcher).

If a tracked subprocess refuses to die: check `tasklist /FI "IMAGENAME eq llama-server.exe"` after telecode exits — should be empty within ~2s. If not, the Job didn't take (look for `could not create Job Object` in `data/logs/telecode.log` — usually missing pywin32).

---

## System tray UI (`tray/`)

Qt tray + settings window in a daemon thread inside the bot process. No separate tray process, no webview, no PyInstaller bundle.

- `main.py:_post_init` → `tray.app.start_tray_in_thread(app, loop)`.
- Sync actions run on the tray thread; async use `asyncio.run_coroutine_threadsafe(coro, bot_loop)`.
- Quit → `app.stop_running()` on the loop → `run_polling` returns → clean exit.
- Right-click submenus refreshed every 2s; toggles persist via `patch_settings` (atomic write + `config.reload()`). Managed/MCP-tool toggles → `data/runtime-overrides.json`; last-active llama model → `data/llama-state.json`.

---

## DocGraph integration (`docgraph/`)

Telecode supervises **one** [DocGraph](../.docgraph) subprocess (`docgraph host --root … --root … --port 5500`) covering every configured root. The host exposes web UI + JSON API + MCP HTTP on one port; the bridge registers each tool once as `docgraph_<tool>` (agents pick the repo per call via the closed-enum `root` argument).

| Concept | Shape |
|---|---|
| Long-running supervisor | `HostSupervisor` (only one). Spawns `docgraph host --host <h> --port <p> --root <p1> --root <p2> [--watch <pX>] …` plus every applicable config flag (`--gpu`, `--embed-model`, `--rerank-*`, `--llm-*`, `--documents`, …). |
| One-shot index | `IndexRunner`. **Two routes:** (a) host alive → POSTs `http://h:p/api/admin/index?root=<slug>&full=<bool>`, lets the host run the pass in-process via the workspace's writer-lock dance, writes the response's captured `log` into `docgraph_index.log`; (b) host down → spawns `docgraph index <path>` with all CLI flags. The host route is preferred — Kuzu's writer lock is exclusive vs. any other connection on the same DB file. |
| Stdio MCP for editors | Not telecode-managed. Editors launch `docgraph mcp <path> --transport stdio`, which probes the running host and proxies through it. |

**Config is CLI-flag-only.** Everything in `settings.docgraph.*` becomes a flag at spawn time. Long-form prompt text (`docgraph.llm.prompts.docstring` / `.wiki`) is materialized to `data/runtime/docgraph_llm_prompt_{docstring,wiki}.txt` and passed via `--llm-prompt-{docstring,wiki}-file` so we dodge argv-length / quoting hazards. The only env vars on subprocess spawns are `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1` (govern Python stdio encoding; no CLI equivalent).

**Binary detection.** `docgraph.binary` empty → `shutil.which("docgraph")` → fall through to `<settings_dir>/.venv/Scripts/docgraph.exe`, `~/.local/bin/docgraph.bat`, `~/.docgraph/.venv/Scripts/docgraph.exe`. Non-empty = use verbatim.

**Settings shape** (`settings.docgraph.*`):
- `binary`, `host.{enabled, auto_start, auto_restart, host, port, gpu, debounce}`.
- `roots: [{path, watch}, ...]`. Flipping `watch` requires a host restart.
- `llm.{model, host, port, format, max_tokens, max_tokens_wiki, prompts.{docstring, wiki}}`.
- `embeddings.{model, gpu}`.
- `rerank.{default, model, gpu}`.
- `index.{workers, embed_batch_size, documents.{enabled, text_extensions, asset_extensions}}`.
- `wiki.depth`.

**MCP bridge** (`docgraph/bridge.py`). On `HostSupervisor` start, after `/api/roots` is reachable: open `streamablehttp_client("http://<h>:<port>/mcp")`, `await session.list_tools()`, register each in `proxy.managed_tools._REGISTRY` as `docgraph_<tool>`. Handler closure opens a transient session per invocation. The closed-enum `root` argument scopes calls to a specific repo.

**Auto-start.** `docgraph.host.auto_start: true` → spawned in `main.py:_post_init` after the proxy. **Independent of `host.enabled`** — Auto-start fires even if Enabled is OFF (Enabled is the live-state flag). `_post_shutdown` tears down: bridge → host process.

**Tray UI.** Cards: Host (start/stop/restart + bind config), Roots (table with per-row Index/Wiki/Clear/Watch + ✕ remove + `+ Add root`), LLM, Embeddings, Reranker. Embeddings + Reranker cards have a "🔄 Restart host" button — those settings only take effect on the next host spawn.

**Logs.** `data/logs/docgraph_host.log` + `data/logs/docgraph_index.log`. Live tail in the global Logs section.

---

## llama.cpp supervisor (`llamacpp/`)

1. **Spawn:** `LlamaSupervisor.start_default()` runs from `main.py:_post_init` BEFORE the proxy. stdout+stderr merged into `data/logs/llama.log` (append, one banner per restart).
2. **argv builder** (`llamacpp/argv.py`): walks `llamacpp.models.<model>` → flags via a table-driven mapper (`ctx_size → --ctx-size`, `n_gpu_layers → --n-gpu-layers`, `flash_attn → --flash-attn`, `mmproj`, `draft_model → --model-draft`, `chat_template`, `jinja`, `cache_type_k/v`, `slot_save_path`, LoRA, grammar, …). Anything else: `extra_args: [["--flag","value"]]`. Both per-model and top-level `extra_args` honored.
3. **Model swap:** `ensure_model(name)` resolves through `llamacpp.models` → `proxy.model_mapping` → `default_model`. Different model = stop + respawn + `/health` poll.
4. **Ready probe:** `/health` `"ok"` = ready; `503` / `"loading model"` = warming; connection error = not up. Deadline: `llamacpp.ready_timeout_sec` (default 120).
5. **Shutdown:** `shutdown_supervisor()` in `main.py:_post_shutdown` — SIGTERM, 4s wait, then kill. Fired AFTER the proxy runner.

---

## Proxy pipeline (`proxy/`)

Dual-protocol middleware in front of llama.cpp. Both **Anthropic** `/v1/messages` and **OpenAI** `/v1/chat/completions` exposed; internal canonical shape is OpenAI.

1. **Startup:** `start_proxy_background()` from `main.py:_post_init` AFTER the supervisor. Port 1235 by default. Standalone via `python -m proxy`.
2. **Protocols** (`proxy.protocols`): `["anthropic", "openai"]` by default.
3. **Profile matching:** `_match_profile(headers)` — first `client_profile` whose `match.header` contains `match.contains` wins.
4. **Model mapping** (`proxy.model_mapping`): rewrites `body.model`. Resolved via `llama_cfg.resolve_model()` (registry → mapping → default). Response model reverse-mapped back to client alias.
5. **Translation** (`proxy/translate.py`): `anthropic_request_to_internal` (blocks → OpenAI parts; `tool_use` → assistant `tool_calls`; `tool_result` arrays → `role:"tool"` strings + lifted user message with image parts; `cache_control` dropped recursively; `system` flattened into leading `{"role":"system"}`); `openai_request_to_internal` (near-identity + `cache_prompt=true` + `stream_options.include_usage=true`). Inference defaults merged from `llamacpp.inference` + per-model `inference_defaults`, overridable by request.
6. **Managed-tool injection** (`inject_managed`): registry names + `strip_from_cc` lists become a strip set; Anthropic-shape schemas converted to OpenAI tools. Always intercepted.
7. **Tool search** (`tool_search: true`): splits OpenAI tools into core + deferred; `ToolSearch` meta-tool injected when deferred non-empty. Deferred names listed in a `<system-reminder>` on the first user message.
   - **Auto-load** (`auto_load_tools: true`): first blind call → schema returned as tool_result; model retries.
   - **Unloaded-tool guard** (`auto_load_tools: false`): blocks; instructs `ToolSearch(select:Name)`.
   - **Hallucination guard**: unknown name → BM25 top-5 suggestions in a `<functions>` block.
8. **System prompts**: `system_instruction` (profile, prepends a markdown file with `<if dotted.key="value">` conditionals); `inject_date_location` (appends date + location as `<system-reminder>`).
9. **Message transforms**: `strip_reminders` (keeps skills + deferred-tools listing), `cache_control` always stripped, tool-result image lifting always on.
10. **Intercept loop** (`_run_streaming` / `_run_non_streaming`): operates on internal (OpenAI) shape. Each round-trip → `_run_upstream_round` reads OpenAI SSE and **branches on first content signal**. Intercepted tool_call → assemble args → return `InterceptedToolCall`. Anything else → stream live through the adapter. `_start_heartbeat` runs for the request lifetime — Anthropic clients get `: keepalive` + `event: ping` every `proxy.ping_interval`; OpenAI clients get `: keepalive` only. Loops up to `proxy.max_roundtrips` (default 15).
11. **Client adapters** (`AnthropicAdapter` / `OpenAIAdapter`): own per-round `*StreamState`. Status lines are synthetic content blocks at indices `0..status_emitted-1`. `<think>` openers across delta boundaries handled via max-tag-length lookahead. `thinking_delta` blocks emitted when `emit_thinking_blocks=true`.
12. **Intercept handlers** (5 branches in `_run_streaming`): `ToolSearch` (BM25), managed tools (`pre_llm` → `handler` → `post_llm`), auto-load first blind call, unloaded-tool guard, hallucination guard.
13. **Token counting** (`/v1/messages/count_tokens`): full prepare pipeline → llama.cpp `/apply-template` → `/tokenize`. Exact, no generation.
14. **Embeddings** (`/v1/embeddings`): forwarded to llama-server verbatim.
15. **CORS**: `cors_origins` list. Streaming responses get headers via `_apply_cors_to_stream()` before `prepare()`.

To use: `llamacpp.enabled: true` + `proxy.enabled: true`, fill in `llamacpp.binary` + `llamacpp.models.<name>.path`, point client tools at `http://localhost:1235`.

---

## Live Telegram messages (`bot/live.py`)

- **`LiveMessage`:** one text message per "turn", updated by `append()`. First chunk edits immediately; subsequent chunks coalesce on a ~1s debounce. Overflow loops into fresh messages — no head-truncation. `_safe_split` uses cumulative escape-count prefix sums + binary search. Overlap with prior text trimmed by `find_overlap_end` (Z-algorithm scan).
- **`finalize()` retry:** if the last `_do_edit` didn't land, schedules one more 2s later.
- **`TypingPinger`:** `sendChatAction("typing")` every 4s until first reply, or topic-gone, or `finalize()`, or 60s hard cap.
- **Per-chat flood:** `flood_active(chat_id)` / `set_flood_backoff(chat_id, retry_after)` — per-chat dict, not global.
- **`FrameSender`:** new photo per frame (interval = `capture.image_interval`). Inline buttons (`cap_pause:` / `cap_resume:` / `stop:`). `controls_kb_factory` + `track_controls` injected at construction so this module doesn't import `bot/handlers.py` back.
- **Latest-message-only controls** (in `bot/handlers.py`): `_track_controls(bot, msg)` keeps a per-thread pointer to the most recent inline-keyboard message. Every send-with-`reply_markup` call funnels through it; the previously tracked message has its keyboard stripped via `edit_message_reply_markup(reply_markup=None)` first.

---

## Logging & crash traces (`main.py`)

- Log file: `data/logs/telecode.log`. On startup it's **rotated to `telecode.log.prev`** (not deleted) — so after a crash + restart the previous run's traceback survives.
- `_install_crash_handlers` + `_install_asyncio_exception_handler` route uncaught exceptions (incl. unawaited task exceptions) to the log. Essential under `pythonw`.
- `run_polling` is wrapped — a fatal error logs `CRITICAL Bot crashed: …` before re-raising.
- **When debugging a crash, always check `telecode.log.prev` first** — the live `telecode.log` is from after the restart.

---

## Adding a CLI backend

Add a `tools.<key>` to `settings.json`:

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

The registry auto-creates a `GenericCLIBackend` for any key that isn't a special non-PTY backend (`screen`, `video`). No code changes needed. Test: `/settings reload` then `/new <key> test`.

## Adding a Telegram command

1. `async def cmd_xxx(update, ctx)` in `bot/handlers.py`.
2. `app.add_handler(CommandHandler("xxx", cmd_xxx))` in `main.py`.
3. Add to `BOT_COMMANDS` and `cmd_help()`.

## MCP server (`mcp_server/`)

FastMCP streamable HTTP, port 1236. Drop-in `tools/` / `resources/` / `prompts/` auto-discovered via `pkgutil.iter_modules`. Built-ins: `speak` (Kokoro TTS), `transcribe` (Whisper STT), `web_search` (Brave). For local models routed through the proxy these are injected via `managed_tools.py` — no MCP connection needed; the MCP server is for external clients or Claude Code against the real Anthropic API.

`claude mcp add telecode --transport streamable-http --url http://127.0.0.1:1236/mcp`

---

## Common problems

| Symptom | What to check |
|---|---|
| Bot silent | Token, group id, bot admin, Topics on. Also `data/logs/telecode.log.prev`. |
| Bot stops after a while | `data/logs/telecode.log.prev` — crash handlers route exceptions there. |
| "No session for thread" | `/new` again; store may be missing mapping. |
| CLI exits at once | Missing API key, wrong `startup_cmd`, binary not on PATH. |
| Stuck on prompt | `/key enter` or `/key y`. |
| Garbled stream | TUI limitation; tune diff in `process.py` or use a non-interactive CLI mode. |
| `settings` change ignored | `/settings reload` or restart. |
| Screen capture blank | Window may be on another virtual desktop; minimized are auto-restored. |
| Video encoding fails | Ensure ffmpeg is on PATH; check ffmpeg stderr in logs. |
| Computer control wrong spot | DPI scaling — `_get_window_rect` must return logical coords. |
| Computer control LLM error | `base_url` should point at the proxy (`http://localhost:1235/v1`), not llama-server directly. |
| Voice not working | Start STT and send a voice message. First request hits the endpoint directly. |
| Proxy not starting | `proxy.enabled: true`, port not in use. |
| llama-server won't start | `data/logs/llama.log` for the binary output; verify `binary` and `models.<default>.path`. |
| Model swap hangs | `llamacpp.ready_timeout_sec` too short for a large GGUF — increase. |
| `<think>` blocks leak into text | Per-model `inference_defaults.reasoning.start/end` tags must match what the model emits. |
| ToolSearch not triggered | Model may not call it; check upstream reachable; with `proxy.debug` on, inspect `data/logs/proxy_full_*.json`. |
| Tools missing after search | Try `re:` prefix; check `MAX_SEARCH_RESULTS`. |
| MCP server not starting | `mcp_server.enabled: true`; port 1236 free. |
| MCP speak/transcribe fails | Kokoro TTS on `:6500` / Whisper STT on `:6600` must be running. |
| DocGraph host won't start | `data/logs/docgraph_host.log`. Verify `docgraph.binary`. |
| DocGraph host can't bind port | Another process holds `docgraph.host.port`. The supervisor sweeps before bind. |
| DocGraph index Kuzu lock error | `docgraph index` subprocess and host both opened the same DB. The IndexRunner is supposed to route through `/api/admin/index` when the host is alive — check `docgraph_index.log` for "host route failed". |
| DocGraph bridge tools missing | Host must be alive AND `/mcp` must respond (lifespan must be `on` in uvicorn). Check `docgraph_host.log` for `mcp.server.streamable_http_manager: ... session manager started`. |

---

## Running in background (Windows)

`pythonw main.py` instead of `python main.py` — no console window. For auto-start, use a Windows Scheduled Task with `pythonw.exe` as the executable.

## Dependencies (see `requirements.txt`)

**python-telegram-bot**, **aiohttp**, **aiofiles**, **pyte**, **pywinpty** (Windows PTY), **mss** (fallback capture on Linux/Mac), **Pillow** (JPEG), **pywin32** (Windows Session 0), **pyautogui** (mouse/keyboard), **mcp** (MCP SDK). ffmpeg on PATH for video.
