# CLAUDE.md — Telecode developer guide

Telegram bot running CLI tools (Claude Code / Codex / shell) inside a pseudo-terminal, reading pyte snapshots and posting to forum-topic threads. Also: screen image/video capture, vision-LLM computer control, llama.cpp + dual-protocol proxy, in-process Qt tray, DocGraph host supervisor.

User-facing docs in [README.md](README.md).

---

## Architecture

```
PTY:    handler → SessionManager.send → PTYProcess → pyte → snapshot diff → _LiveMessage → editMessageText
Image:  /new screen   → ScreenCapture(hwnd) → JPEG → _FrameSender
Video:  /new video    → VideoCapture(hwnd)  → ffmpeg → MP4 chunks
Vision: /new computer → ComputerControl     → screenshot → vision LLM JSON → pyautogui → loop
Proxy:  client → translate to OpenAI → model swap → intercept loop → SSE back

Agent → Job → Run (Team Mode):
  Agent owns 5 files in data/agents/<id>/internal/ (SOUL/USER/AGENT/MEMORY/HEARTBEAT).
  Job.pipeline.steps run sequentially by phase; same-phase in parallel.
  stage_for_run() copies SOUL/USER/MEMORY → workspace, AGENT.md → CLAUDE.md/GEMINI.md; on exit writes back.

Heartbeat (Team Mode, off by default): parse HEARTBEAT.md → kind="heartbeat" Jobs → fire due+enabled.
Routines (Task Mode, separate): data/routines/<id>.json, 60s tick in proxy, fires active+due via
  task_manager.submit_task. Skip-if-running. Preface prepended each fire. next_fire_at always advances.
```

- **Session key:** `{backend}:{name}` — no colons in names. Routing by `message_thread_id` only.
- **PTY cwd:** `Path.home()` via `config.pty_cwd()`.
- **Session cleanup:** fast (process.alive) on picker click; full (probe via send+delete) on `/start`; `_LiveMessage`/`_FrameSender`/video callbacks → `handle_topic_gone()`.
- **`/stop`:** in session topic = that one; in General no args = all; `/stop <name>` = specific.

---

## Key files

- `settings.json` — only config source. `config.py` — read/write accessors (always functions for hot-reload), paths resolve relative to settings.json dir.
- `main.py` — startup, `asyncio.run(_async_main())`. `bot/supervisor.py` — `BotSupervisor` (full PTB lifecycle + optional auto-restart; `initialize()` deferred so telecode boots offline).
- `store.py` — topics JSON (topic id per `(user_id, session_key)`).
- `sessions/{terminal,screen,computer,manager}.py` — PTY+pyte+diff / image+video capture / vision LLM loop / session lifecycle.
- `bot/{handlers,live,rate,topic_manager,settings_handler}.py` — commands / LiveMessage+FrameSender+TypingPinger / cleanup / topic CRUD / `/settings`.
- `backends/{implementations,registry}.py` — `GenericCLIBackend` + Screen/Video; registry auto-built from `settings.json` tools.
- `voice/*` — STT transcribe + lazy health.
- `process.py` — subprocess lifecycle: Win Job (`KILL_ON_JOB_CLOSE`) binds every spawn to this Python; `kill_process_tree`, `sweep_port` (cmdline-aware orphan killer); `LlamaSupervisor`.
- `llamacpp/{argv,config,state}.py` — build llama-server argv / settings / persist last-active model.
- `tray/{app,qt_window,qt_sections,qt_docgraph}.py` — Qt tray on daemon thread; frameless window with sidebar + `QStackedWidget`; section builders; DocGraph panel.
- `docgraph/{config,process,bridge}.py` — settings / one `HostSupervisor` / MCP-client → managed_tools registration. `docgraph/{stats,index,wiki,progress}_state.py` — TTL'd tray caches.
- `proxy/server.py` — dual-protocol aiohttp proxy + intercept loop. `proxy/translate.py` — Anthropic↔OpenAI shape, `ReasoningState` `<think>` machine, `AnthropicStreamState` rebuilds events from OpenAI SSE.
- `proxy/{tokenizer,tool_search,tool_registry,managed_tools,runtime_state}.py` — tokenize wrapper / BM25 / `ToolSearch` meta-tool / proxy-handled tools (WebSearch/speak/transcribe + auto-bridged MCP) / overrides JSON.
- `proxy/api_{sessions,tasks,agents,jobs,runs,routines}.py` — REST surface (no auth).
- `services/task/staging.py` — `stage_for_run()` ctx-mgr: copy in, diff on exit, write back. Per-workspace `Lock`. HEARTBEAT.md NOT staged.
- `services/routine/*` — JSON store (atomic, per-routine `RLock`, `MIN_ROUTINE_INTERVAL_SECONDS=60`) / 60s daemon tick / CRUD with inline reconcile.
- `services/run/executor.py` — pipeline driver per Run. Single-step phase = job workspace; multi-step = ephemeral session per step. Threads outputs via `<previous_output(s)>`.
- `services/heartbeat/*` — YAML-fence parser, atomic state, HEARTBEAT.md → kind:"heartbeat" Jobs, async tick.
- `mcp_server/app.py`, `tools/*` — FastMCP (stateless streamable HTTP, port 1236). Drop-in auto-discovery.

---

## Rules (do not break)

1. Only `settings.json` (env var `TELECODE_SETTINGS` to relocate).
2. Always `config.foo()`, never cached module constants for changeable values.
3. Sessions key `backend:name`; routing by `thread_id` only.
4. Real PTY (Unix openpty / Windows ConPTY via pywinpty). llama-server owned by `LlamaSupervisor` — don't spawn manually.
5. Telegram: `ParseMode.HTML` + `html.escape()` user/process text.
6. No in-bot AI, no separate memory layer — CLIs own context.
7. `cache_control` always stripped in translator.
8. Internal canonical shape is OpenAI. Protocol concerns live only in `ClientAdapter` subclasses + `proxy/translate.py`.
9. DocGraph config is CLI-flag-only — no `DOCGRAPH_*` env vars.

---

## PTY output (`sessions/terminal.py`)

Raw bytes → pyte `HistoryScreen`+`Stream` → snapshot → diff vs previous (patience anchors + segment diff + similar-line filter so spinners don't spam) → emit on idle (2s default) or max-wait (5s); poll every 5s. `send()` appends `\r` (not `\n`) so TUIs accept the line. Tunable per-tool via `tools.<key>.streaming.{idle_sec,max_wait_sec}`.

## Capture (`sessions/screen.py`, `sessions/computer.py`)

`enumerate_windows()` platform-specific (Win EnumWindows+DWMWA_CLOAKED; Linux wmctrl/xdotool; macOS CGWindowList). `capture_window()`: PrintWindow (Win, z-order-independent), `import` (Linux), `screencapture` (macOS), mss fallback. Session 0 (Win service) spawns helper via `WTSQueryUserToken`+`CreateProcessAsUser`.

`VideoCapture(hwnd, duration=capture.video_interval, fps=3)` saves JPEGs → `ffmpeg libx264 -preset ultrafast -crf 32 -pix_fmt yuv420p`. `scale=trunc(iw/2)*2:trunc(ih/2)*2` for libx264 even-dim.

`ComputerControl(hwnd)` is duck-type compatible with PTYProcess/ScreenCapture. `hwnd=0` (`FULL_SCREEN_HWND`) = full screen via mss. Mouse cursor drawn as red crosshair. Screenshots = physical pixels, window rect = logical — ratio handles DPI; `pyautogui` gets logical coords. Loop: capture → vision LLM `{thought, done, action}` → pyautogui → post-action capture → repeat. `wait` capped at 30s. LLM API: openai / anthropic / claude-code (uses `--resume` + `--json-schema`, forwards `base_url`/`api_key`/`model` as `ANTHROPIC_*` env). First screenshot = new photo; subsequent = `edit_message_media`.

---

## Subprocess lifecycle (`process.py`)

- **Win Job Object** — every spawn bound to a process-wide Job flagged `KILL_ON_JOB_CLOSE`. atexit fallback without pywin32.
- **`kill_process_tree(pid)`** — graceful first; `taskkill /T` on Win, `killpg` on Unix.
- **`sweep_port(port, whitelist)`** — kills orphans whose exe **or** cmdline matches the whitelist; foreign listeners logged.
- **`LlamaSupervisor`** — one active llama-server, `ensure_model(name)` under asyncio lock. `_wait_ready` polls `/health`, checks `proc.poll()` per iteration; after `status:"ok"` re-polls 1s later to catch orphans. Inflight-gated idle unload.

Child refuses to die: check `tasklist /FI "IMAGENAME eq llama-server.exe"` — should empty within ~2s after telecode exits. If not, Job didn't take (look for `could not create Job Object` — usually missing pywin32).

---

## System tray UI (`tray/`)

Qt tray + settings window in a daemon thread inside the bot process. No separate process, webview, or PyInstaller.

- Sync actions on tray thread; async via `asyncio.run_coroutine_threadsafe(coro, bot_loop)`.
- Quit → `app.bot_data["_request_stop"]()` → `_STOP_EVENT` → clean shutdown.
- Submenus refresh every 2s; toggles persist via `patch_settings` (atomic write + `config.reload()`). Managed/MCP toggles → `data/runtime-overrides.json`; last-active llama model → `data/llama-state.json`.
- Telegram section: Bot Control card (Start/Stop/Restart, auto_start/auto_restart) + Paths/Streaming/Capture/Heartbeat.
- Proxy → Client Profiles: `inject_managed` is a checkbox grid from live `managed_tools._REGISTRY`.

**Dependency-aware widgets (qt_sections.py).** `_dependent(row, [parent_paths], predicate)` greys a row when predicate is False; listens on `settings_bus()` (Qt `QObject` that `patch_settings`/`remove_path` emit on). Used for ngram knobs (per spec_type), draft-side controls, YaRN factors (`rope_scaling != "yarn"`), etc. `_mutex_bools(path_a, path_b)` + `_mutex_spec_default_vs_type()` wire mutual exclusion over the bus; registered idempotently via `_MUTEX_REGISTERED`.

---

## DocGraph integration (`docgraph/`)

Telecode supervises **one** DocGraph subprocess (`docgraph host --root … --port 5500`) covering every configured root. Host exposes web UI + JSON API + MCP HTTP on one port; bridge registers each tool once as `docgraph_<tool>` (closed-enum `root` arg scopes per call).

- **Supervisor:** `HostSupervisor` — spawn includes every applicable flag (`--gpu`, `--embed-model`, `--rerank-*`, `--llm-*`).
- **Index:** `IndexRunner`. Host alive → POST `/api/admin/index?root=<slug>&full=<bool>` (preferred — Kuzu writer lock is exclusive); host down → spawn `docgraph index <path>` with CLI flags.
- **Stdio MCP for editors:** not telecode-managed. Editors launch `docgraph mcp <path> --transport stdio`, which proxies through the host.
- **CLI-flag-only config.** Long-form prompts materialized to `data/runtime/docgraph_llm_prompt_*.txt`, passed via `--llm-prompt-*-file`. Only env on spawn: `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1`.
- **Binary detection:** `docgraph.binary` empty → `shutil.which("docgraph")` → `<settings_dir>/.venv/Scripts/docgraph.exe`, `~/.local/bin/docgraph.bat`, `~/.docgraph/.venv/Scripts/docgraph.exe`.
- **Settings (`settings.docgraph.*`):** `binary`, `host.{enabled, auto_start, auto_restart, host, port, gpu, debounce}`, `roots:[{path, watch, pinned}]`, `llm.*`, `embeddings.{model, gpu, idle_unload_sec}`, `rerank.{default, model, gpu, idle_unload_sec}`, `index.{workers, embed_batch_size}`, `wiki.depth`. Adding/removing roots or flipping `watch` auto-restarts the host. Pinned roots render 📌 and are read-only end-to-end.
- **Per-root config** (not in settings): `<root>/.docgraph/repos.json` (extra sibling paths) and `links.json` (`[{url, depth, max_pages, ttl_hours, last_fetched, page_count}]`; `depth=0` = seed only, `max_pages=0` = unlimited).
- **MCP bridge.** On host start (after `/api/roots` reachable): `streamablehttp_client("http://h:port/mcp")` → `list_tools()` → register each in `proxy.managed_tools._REGISTRY` as `docgraph_<tool>`. Handler closure opens a transient session per call.
- **Auto-start.** `host.auto_start: true` → spawned in `_post_init` after the proxy. Independent of `host.enabled` (that's live-state). Teardown: bridge → host.
- **Win hybrid graphics (`_ensure_high_perf_gpu`).** Writes `HKCU\Software\Microsoft\DirectX\UserGpuPreferences\<docgraph.exe> = "GpuPreference=2;"` before spawn — otherwise `CREATE_NO_WINDOW` processes get iGPU from DXGI default and DML lands on Intel.

Logs: `data/logs/docgraph_host.log` + `data/logs/docgraph_index.log`.

---

## Routines (Task Mode) (`services/routine/`, `proxy/api_routines.py`)

Recurring task fires against a permanent task-mode session. Independent of Team-Mode Heartbeat. **Manager runs inside the proxy aiohttp process** — bot-only deployments don't tick.

- **Record:** `data/routines/<id>.json` — `{prompt, task_type ("CLAUDE_CODE"|"GEMINI"), schedule.every_seconds≥60, session_id, status, next_fire_at, last_fire_at, last_task_id, last_completed_*, total/skipped_runs}`. Atomic tmp+rename under per-routine `RLock`.
- **Manager:** daemon thread inside `start_proxy_background()`. 60s loop; bootstrap-tick fires immediately so missed routines recover.
- **Tick:** every routine → `_reconcile_completion(last_task_id)`. Active+due (`status=="active"` and `now ≥ next_fire_at`) → `fire_routine`.
- **Fire:** skip-if-running (PENDING/RUNNING → record skipped, `next_fire_at` still advances). Heartbeat preface prepended (tick #, cadence, time-since-last-fire, "recurring wake-up — build on prior work"). Submits via `task_manager.submit_task(..., session_id=rec["session_id"])`; handlers resume the same session.
- **Service:** `get_routine`/`list_routines`/`run_now` reconcile inline so UI's 5s poll catches terminal status within seconds.
- **API:** `/api/routines` list/create, `/api/routines/<id>` get/patch/delete (`?delete_session=true`), `…/{pause,resume,run-now,runs}`.
- **UI:** `proxy/static/index.html`. Left **Routines** tab (form + Save/Pause/Resume/Run-now/Delete); right **By routine** tab (collapsible per-routine task lists). Tab handler scoped per `.tabs` via `data-tab` vs `data-rtab`.

---

## llama.cpp supervisor (`llamacpp/`)

Tracks llama-server **v9243** — `--no-ui` (not `--no-webui`), `draft-mtp` in `--spec-type`, `--spec-default`, `--direct-io`, etc. `LlamaSupervisor.start_default()` runs in `_post_init` BEFORE the proxy; stdout+stderr → `data/logs/llama.log`. Shutdown (SIGTERM, 4s wait, kill) runs AFTER the proxy.

- **argv builder** (`argv.py`): table-driven `settings_key → --cli-flag` with per-row `kind` (`flag`/`onoff`/`bool_pair`/`value`/`value_nz`/`path`). `value_nz` skips zero (0 = "use default"); `value` emits literal 0 (0 = "disable"). Two tables: `_GLOBAL_FLAG_SPECS` (`llamacpp.*`), `_MODEL_FLAG_SPECS` (`llamacpp.models.<m>.*`). `spec_type` is comma-separated (v9243+); `ngram-mod` has its own `(n-min, n-max, n-match)` flags distinct from `(size-n, size-m, min-hits)` shared by other ngram strategies.
- **Model swap:** `ensure_model(name)` resolves via `llamacpp.models` → `proxy.model_mapping` → `default_model`. Different = stop + respawn + `/health` poll.
- **Ready probe:** `/health` `"ok"` = ready; 503/`"loading model"` = warming; connection error = down. Deadline `llamacpp.ready_timeout_sec` (default 120). 1s re-poll after `"ok"` catches orphans.

**Settings layout (no duplicates):**
- `llamacpp.*` — server-wide CLI flags (threads, batch, mlock, kv_*, spec_type, cache_ram, endpoints, server-mode, timeout, api_prefix).
- `llamacpp.models.<m>.*` — per-model flags re-taking effect on respawn (ctx_size, n_gpu_layers, n_cpu_moe, mmproj, rope_*, yarn_*, draft_*, lora, grammar, reasoning_*, override_*, device, chat_template).
- `llamacpp.models.<m>.inference_defaults.*` — proxy-applied request-body fields (temperature, top_p, max_tokens, stop, reasoning.*, chat_template_kwargs).
- `llamacpp.inference.*` — proxy-applied global fallbacks. Hierarchy: request body > per-model > top-level. "Proxy Behavior" card exposes only keys without per-model equivalent (`context_overflow`, `drop_prior_thinking`, `disable_thinking`, `structured_output.*`, `reasoning_effort_map.*`).

Server-wide flags must NOT appear in `_MODEL_DEFAULTS` — `argv.py` ignores them per-model.

---

## Proxy pipeline (`proxy/`)

Dual-protocol middleware in front of llama.cpp. Both Anthropic `/v1/messages` and OpenAI `/v1/chat/completions` exposed; internal canonical shape is OpenAI. Port 1235; standalone via `python -m proxy`. Started from `_post_init` AFTER the supervisor.

1. **Profile match:** first `client_profile` whose `match.header` contains `match.contains`. **Model mapping:** `body.model` rewritten via `llama_cfg.resolve_model()`; response model reverse-mapped to client alias.
2. **Translation** (`translate.py`): Anthropic → OpenAI (`tool_use` → assistant `tool_calls`; `tool_result` → `role:"tool"` + lifted user message with image parts; `cache_control` dropped recursively; `system` flattened into leading `{"role":"system"}`). OpenAI is near-identity + `cache_prompt=true` + `stream_options.include_usage=true`. Defaults: request body > per-model > top-level.
3. **Managed-tool injection:** registry names + `strip_from_cc` → strip set; Anthropic schemas converted to OpenAI tools.
4. **Tool search** (`tool_search: true`): splits tools into core + deferred; `ToolSearch` meta-tool injected; deferred names in `<system-reminder>` on first user message. Auto-load (`auto_load_tools: true`): blind call → schema as tool_result, model retries. Otherwise blocks and instructs `ToolSearch(select:Name)`. Hallucination guard: unknown name → BM25 top-5.
5. **System prompts:** `system_instruction` prepends a markdown file with `<if dotted.key="value">` conditionals; `inject_date_location` appends date+location. `strip_reminders` keeps skills + deferred-tools listing.
6. **Intercept loop:** OpenAI internal shape. `_run_upstream_round` branches on first content signal. Tool_call → assemble → `InterceptedToolCall`. Otherwise stream live via adapter. `_start_heartbeat`: Anthropic gets `: keepalive` + `event: ping` every `proxy.ping_interval`; OpenAI gets `: keepalive` only. Up to `proxy.max_roundtrips` (default 15).
7. **Adapters** (`AnthropicAdapter`/`OpenAIAdapter`): per-round `*StreamState`. Status lines = synthetic content blocks at indices `0..status_emitted-1`. `<think>` openers across delta boundaries via max-tag-length lookahead. `thinking_delta` when `emit_thinking_blocks=true`.
8. **`count_tokens`:** full prepare → `/apply-template` → `/tokenize`. **`/v1/embeddings`:** forwarded verbatim. **CORS:** `cors_origins`; streaming gets headers via `_apply_cors_to_stream()` before `prepare()`.

To use: `llamacpp.enabled` + `proxy.enabled`, fill `llamacpp.binary` + `llamacpp.models.<name>.path`, point clients at `http://localhost:1235`.

---

## Live Telegram messages (`bot/live.py`)

- **`LiveMessage`:** one text message per "turn", updated by `append()`. First chunk edits immediately; subsequent coalesce on ~1s debounce. Overflow loops into fresh messages — no head-truncation. `_safe_split` uses cumulative escape-count prefix sums + binary search. Overlap trimmed via Z-algorithm.
- **`finalize()` retry:** if last `_do_edit` didn't land, schedules one more 2s later.
- **`TypingPinger`:** `sendChatAction("typing")` every 4s until first reply / topic-gone / `finalize()` / 60s cap.
- **Per-chat flood:** `flood_active(chat_id)` / `set_flood_backoff(chat_id, retry_after)`.
- **`FrameSender`:** new photo per frame. Inline buttons (`cap_pause:`/`cap_resume:`/`stop:`). `controls_kb_factory` + `track_controls` injected at construction (no back-import).
- **Latest-message-only controls:** `_track_controls` keeps a per-thread pointer to the most recent inline-keyboard message; previous has its keyboard stripped via `edit_message_reply_markup(reply_markup=None)` first.

---

## Logging (`main.py`)

`data/logs/telecode.log`. Startup **rotates to `telecode.log.prev`** so a crash + restart preserves the prior traceback. `_install_crash_handlers` + `_install_asyncio_exception_handler` catch uncaught exceptions (incl. unawaited tasks) — essential under `pythonw`. `run_polling` wrapped; fatal error logs `CRITICAL Bot crashed: …`.

**When debugging a crash, check `telecode.log.prev` first.**

---

## Adding things

**CLI backend** — add a `tools.<key>` entry: `{name, icon, startup_cmd, flags, env, session, streaming:{idle_sec, max_wait_sec}}`. The registry auto-creates a `GenericCLIBackend` for any key that isn't `screen`/`video`. Test: `/settings reload` then `/new <key> test`.

**Telegram command** — `async def cmd_xxx(update, ctx)` in `bot/handlers.py` → `app.add_handler(CommandHandler("xxx", cmd_xxx))` in `main.py` → add to `BOT_COMMANDS` and `cmd_help()`.

## MCP server (`mcp_server/`)

FastMCP streamable HTTP, port 1236. Drop-in `tools/`/`resources/`/`prompts/` auto-discovered via `pkgutil.iter_modules`. Built-ins: `speak`, `transcribe`, `web_search` (Brave). Audio defaults to VoxType (`:6600`); repoint via `mcp_server.stt_url`/`tts_url`. Local models routed through the proxy inject these via `managed_tools.py` — no MCP needed.

`claude mcp add telecode --transport streamable-http --url http://127.0.0.1:1236/mcp`

---

## Common problems

- **Bot offline / silent / stops** — check `telegram.auto_start`, token, group id, bot admin, Topics on. Look in `telecode.log.prev`. Set `telegram.auto_restart: true`.
- **Heartbeat/Routine not firing** — `heartbeat.enabled` / `proxy.enabled`. Routine manager logs `routine_manager: heartbeat started`. Routine "Last completed" only reconciles while proxy up; next poll catches up.
- **"No session for thread"** — `/new` again. **CLI exits at once** — API key / `startup_cmd` / binary on PATH. **Stuck on prompt** — `/key enter` or `/key y`. **Garbled stream** — TUI limitation; tune diff. **Settings ignored** — `/settings reload` or restart.
- **Screen capture blank** — window on another virtual desktop. **Video encoding** — ffmpeg on PATH. **Computer control wrong spot** — DPI: `_get_window_rect` must be logical coords. **Computer control LLM error** — `base_url` should be proxy (`:1235/v1`).
- **llama-server won't start** — `data/logs/llama.log`; verify `binary` and `models.<default>.path`. **Model swap hangs** — bump `llamacpp.ready_timeout_sec`. **`<think>` leaks** — per-model `inference_defaults.reasoning.start/end` must match.
- **ToolSearch not triggered** — with `proxy.debug`, inspect `data/logs/proxy_full_*.json`. **Tools missing after search** — try `re:` prefix; check `MAX_SEARCH_RESULTS`. **MCP speak/transcribe** — VoxType on `:6600` or repoint URLs.
- **DocGraph host won't start** — `data/logs/docgraph_host.log`; verify `docgraph.binary`. **Kuzu lock error** — `IndexRunner` should route through `/api/admin/index` (check `docgraph_index.log` for "host route failed"). **Bridge tools missing** — host alive AND `/mcp` responding (uvicorn lifespan `on`).

---

## Running in background (Win)

`pythonw main.py` — no console. For auto-start: Windows Scheduled Task with `pythonw.exe`.

## Dependencies

**python-telegram-bot**, **aiohttp**, **aiofiles**, **pyte**, **pywinpty** (Win PTY), **mss**, **Pillow**, **pywin32** (Win Session 0), **pyautogui**, **mcp**. ffmpeg on PATH for video.
