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
  stage_for_run() copies SOUL/USER/MEMORY → workspace, AGENT.md → CLAUDE.md; on exit writes back.

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
- `llamacpp/{argv,config,state}.py` — build llama-server argv / settings / persist last-active model. `llamacpp/updater.py` — pull llama.cpp release zips, overlay onto the install dir (per-file `.bak-<ts>/` backups), and list/restore/delete those version backups. `llamacpp/flag_audit.py` — probe any binary's `--help` into a flag spec, cross-check telecode's emitted argv against it, and diff two builds' flag surfaces (tray "Version Manager" card).
- `tray/{app,qt_window,qt_sections,qt_docgraph}.py` — Qt tray on daemon thread; frameless window with sidebar + `QStackedWidget`; section builders; DocGraph panel.
- `docgraph/{config,process,bridge}.py` — settings / one `HostSupervisor` / MCP-client → managed_tools registration. `docgraph/{stats,index,wiki,progress}_state.py` — TTL'd tray caches.
- `proxy/server.py` — multi-protocol aiohttp proxy (Anthropic / OpenAI / Gemini adapters) + intercept loop + `/v1/responses` passthrough. `proxy/translate.py` — Anthropic↔OpenAI and Gemini↔OpenAI shape, `ReasoningState` `<think>` machine, `AnthropicStreamState` / `GeminiStreamState` rebuild events from OpenAI SSE, `normalize_responses_request` for the Responses passthrough.
- `proxy/{tokenizer,tool_search,tool_registry,managed_tools,runtime_state}.py` — tokenize wrapper / BM25 / `ToolSearch` meta-tool / proxy-handled tools (WebSearch/speak/transcribe + auto-bridged MCP) / overrides JSON.
- `proxy/api_{sessions,tasks,agents,jobs,runs,routines}.py` — REST surface (no auth). Any caller-supplied
  URL goes through `proxy/media_fetch.py`, and any caller-supplied filename through
  `JobManager._resolve_in` — the surface has no auth, so neither may be trusted.
- `proxy/llama_caps.py` — functional capability probes against the running llama-server, cached
  per (server, model). `defer_loading` is probed via `/apply-template`, which renders a prompt
  **without inference** — a probe that ran a completion would take the single slot and evict the
  very cache the feature exists to protect. Any failure answers False, i.e. fall back.
- `llamacpp/patcher.py` — checkout / apply `patches/llama.cpp/*.patch` / build / install, behind the
  tray's "Patched Build" card. The source tree is disposable and hard-reset on every fetch; the
  patches are the artifact.
- `proxy/media_fetch.py` — guarded fetch for caller-supplied URLs: http/https only, every resolved
  address must be public (loopback / private / link-local incl. `169.254.169.254` refused), redirects
  re-validated per hop, byte cap enforced while streaming. The proxy runs on the user's machine and
  can reach hosts the caller cannot, so this is the SSRF boundary.
- `services/task/staging.py` — `stage_for_run()` ctx-mgr: copy in, diff on exit, write back. Per-workspace `Lock`. HEARTBEAT.md NOT staged.
- `services/routine/*` — JSON store (atomic, per-routine `RLock`, `MIN_ROUTINE_INTERVAL_SECONDS=60`) / 60s daemon tick / CRUD with inline reconcile.
- `services/run/executor.py` — pipeline driver per Run. Single-step phase = job workspace; multi-step = ephemeral session per step. Threads outputs via `<previous_output(s)>`.
- `services/heartbeat/*` — YAML-fence parser, atomic state, HEARTBEAT.md → kind:"heartbeat" Jobs, async tick.
- `services/design/**`, `proxy/api_design*.py`, `proxy/static/teledesign.html` + `proxy/static/design/` — TeleDesign (see its section).
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

- **Supervisor:** `HostSupervisor` — spawn includes every applicable flag (`--gpu`, `--embed-model`, `--rerank-*`, `--llm-*`, `--embed-idle-unload-sec`, `--rerank-idle-unload-sec`, and the daemon flags below). No VRAM-reaper / host-restart-on-idle loop — that was removed (it fired mid-index and corrupted the cache). VRAM is reclaimed by docgraph itself: in-process idle-unload, or the daemon's idle-exit.
- **Embedding daemon (`embeddings.daemon.*`).** With `enabled: true`, the host gets `--embed-daemon --daemon-port N [--daemon-idle-exit-sec X]` and routes embed+rerank to a shared `docgraph daemon` (one warm model + one CUDA context, queued). The host spawns it **lazily** on first use and it runs **detached** (not bound to our Job), so `_stop_locked` sweeps the daemon port on host stop. `idle_exit_sec>0` lets the daemon self-exit to free the context; the host respawns it on next demand.
- **Index:** `IndexRunner`. Host alive → POST `/api/admin/index?root=<slug>&full=<bool>` (preferred — Kuzu writer lock is exclusive); host down → spawn `docgraph index <path>` with CLI flags.
- **Stdio MCP for editors:** not telecode-managed. Editors launch `docgraph mcp <path> --transport stdio`, which proxies through the host.
- **CLI-flag-only config.** Long-form prompts materialized to `data/runtime/docgraph_llm_prompt_*.txt`, passed via `--llm-prompt-*-file`. Only env on spawn: `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1`.
- **Binary detection:** `docgraph.binary` empty → `shutil.which("docgraph")` → `<settings_dir>/.venv/Scripts/docgraph.exe`, `~/.local/bin/docgraph.bat`, `~/.docgraph/.venv/Scripts/docgraph.exe`.
- **Settings (`settings.docgraph.*`):** `binary`, `host.{enabled, auto_start, host, port, gpu, debounce}`, `roots:[{path, watch, pinned}]`, `llm.*`, `embeddings.{model, gpu, torch_compile, idle_unload_sec, daemon:{enabled, port, idle_exit_sec}}`, `rerank.{default, model, gpu, torch_compile, idle_unload_sec}`, `index.{workers, embed_batch_size}`, `wiki.depth`. Adding/removing roots or flipping `watch` auto-restarts the host. Pinned roots render 📌 and are read-only end-to-end. (`host.auto_restart` is gone — the reaper it gated was removed.)
- **Per-root config** (not in settings): `<root>/.docgraph/repos.json` (extra sibling paths) and `links.json` (`[{url, depth, max_pages, ttl_hours, last_fetched, page_count}]`; `depth=0` = seed only, `max_pages=0` = unlimited).
- **MCP bridge.** On host start (after `/api/roots` reachable): `streamablehttp_client("http://h:port/mcp")` → `list_tools()` → register each in `proxy.managed_tools._REGISTRY` as `docgraph_<tool>`. Handler closure opens a transient session per call.
- **Auto-start.** `host.auto_start: true` → spawned in `_post_init` after the proxy. Independent of `host.enabled` (that's live-state). Teardown: bridge → host.
- **Win hybrid graphics (`_ensure_high_perf_gpu`).** Writes `HKCU\Software\Microsoft\DirectX\UserGpuPreferences\<docgraph.exe> = "GpuPreference=2;"` before spawn — otherwise `CREATE_NO_WINDOW` processes get iGPU from DXGI default and DML lands on Intel.

Logs: `data/logs/docgraph_host.log` + `data/logs/docgraph_index.log`.

---

## Routines (Task Mode) (`services/routine/`, `proxy/api_routines.py`)

Recurring task fires against a permanent task-mode session. Independent of Team-Mode Heartbeat. **Manager runs inside the proxy aiohttp process** — bot-only deployments don't tick.

- **Record:** `data/routines/<id>.json` — `{prompt, task_type ("CLAUDE_CODE" | "CODEX" | "ANTIGRAVITY"), schedule.every_seconds≥60, session_id, status, next_fire_at, last_fire_at, last_task_id, last_completed_*, total/skipped_runs}`. Atomic tmp+rename under per-routine `RLock`.
- **Manager:** daemon thread inside `start_proxy_background()`. 60s loop; bootstrap-tick fires immediately so missed routines recover.
- **Tick:** every routine → `_reconcile_completion(last_task_id)`. Active+due (`status=="active"` and `now ≥ next_fire_at`) → `fire_routine`.
- **Fire:** skip-if-running (PENDING/RUNNING → record skipped, `next_fire_at` still advances). Heartbeat preface prepended (tick #, cadence, time-since-last-fire, "recurring wake-up — build on prior work"). Submits via `task_manager.submit_task(..., session_id=rec["session_id"])`; handlers resume the same session.
- **Service:** `get_routine`/`list_routines`/`run_now` reconcile inline so UI's 5s poll catches terminal status within seconds.
- **API:** `/api/routines` list/create, `/api/routines/<id>` get/patch/delete (`?delete_session=true`), `…/{pause,resume,run-now,runs}`.
- **UI:** `proxy/static/index.html`. Left **Routines** tab (form + Save/Pause/Resume/Run-now/Delete); right **By routine** tab (collapsible per-routine task lists). Tab handler scoped per `.tabs` via `data-tab` vs `data-rtab`.

---

## Task engines (`services/task/handlers/`)

Three CLIs are dispatched as task types, all sharing the **same handler signature** (`prompt, is_local, agent_id, agent, job, agent_files, job_files`) and the same `_agent_task_schema`. The executor / heartbeat scheduler / routine manager are engine-agnostic — they pick the task_type via the single shared map in `services/task/engine_map.py`:

| engine string | task_type     | binary  | handler                                  |
|---------------|---------------|---------|------------------------------------------|
| `claude_code` | `CLAUDE_CODE` | `claude`| `services/task/handlers/claude_code.py`  |
| `codex`       | `CODEX`       | `codex` | `services/task/handlers/codex.py`        |
| `antigravity` | `ANTIGRAVITY` | `agy`   | `services/task/handlers/antigravity.py`  |

**Per-engine specifics** (kept parallel to Claude on purpose so future Codex/Antigravity feature parity is a one-file patch):

- **Claude Code** — `claude -p --dangerously-skip-permissions --output-format stream-json --verbose --include-partial-messages [--resume <id>]`, prompt on **stdin**. Resume key: `last_claude_session_id`. Local mode env: `ANTHROPIC_BASE_URL=http://localhost:<proxy>` (no `/v1` — the SDK appends it), `ANTHROPIC_AUTH_TOKEN=local`, `ANTHROPIC_MODEL=<llama>`, `CLAUDE_CODE_MAX_OUTPUT_TOKENS=config.tasks_local_max_output_tokens()` (`tasks.local.max_output_tokens`, default 16384 to match `claudel.bat`). Staging bridge: `AGENT.md ↔ CLAUDE.md`.
- **Codex** — `codex exec [-c …] --sandbox danger-full-access -C <dir> [resume <SID>] --json --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --output-last-message <path> [--model <m>] -` (prompt on **stdin**, verified on codex-cli 0.157). `--sandbox` and `-C` are `exec`-only and must precede `resume`, which rejects them. Resume key: `last_codex_session_id` (from `thread.started.thread_id`). Staging bridge: `AGENT.md ↔ AGENTS.md`. Event mapping (0.157 names, old spellings kept): `item.completed{agent_message} → narrative`; `item.completed{error} → warning`; `item.completed{command_execution,file_change,mcp_tool_call,web_search} → tool`; `turn.completed.usage → done`; final text from `--output-last-message`.
  **Local mode** = a `-c` provider, never env: `-c model_provider=telecode -c model_providers.telecode.{name=telecode, base_url=http://localhost:<proxy>/v1, wire_api=responses, stream_idle_timeout_ms=600000}` + `--model <llama model>`. No `env_key`, so no auth header. Values unquoted on purpose (`-c` parses TOML and falls back to the literal string), so nothing has to survive `shell=True` quoting. Codex removed the Chat Completions wire (`wire_api="chat"` is a hard error since openai/codex#10157) and ignores `OPENAI_BASE_URL`; an exported `OPENAI_API_KEY` can make it bypass the custom provider, so the child env drops `OPENAI_API_KEY`/`OPENAI_BASE_URL`/`CODEX_ACCESS_TOKEN`/`CODEX_API_KEY`. `CODEX_HOME` and `~/.codex/config.toml` are left alone, so the ChatGPT login still serves non-local runs. Provider id must not be `openai`/`ollama`/`lmstudio` (reserved). Codex also calls `GET /v1/models?client_version=…` expecting its own model-catalog shape, fails to parse ours, and falls back to generic metadata (one `error` item per run, harmless).
- **Antigravity** — `agy --input-format stream-json --output-format stream-json --dangerously-skip-permissions --add-dir <dir> [--model <m>] [--conversation <id>] -p=`, prompt on **stdin** as one `{"event":"user","message":{"content":…}}` line (`-p=` with an empty value; a bare `-p` swallows the next flag). Staging bridge: `AGENT.md ↔ AGENTS.md`. Event mapping (verified Sept 2026): `init.conversation_id` → stored and replayed with `--conversation`, so resume works; `step_update{step_type:"tool", state:"ACTIVE"}` → tool; `step_update{step_type:"agent_response"}.text_delta` → `narrative_delta`; `result{status, response, num_turns, usage}` → done.
  **Local mode** (agy ≥ 1.1.13; verified on 1.2.10): agy's Gemini-API route — `"modelProvider": "gemini"` in `~/.gemini/antigravity-cli/settings.json` + `GEMINI_API_KEY=local` + `GOOGLE_GEMINI_BASE_URL=http://localhost:<proxy>` (no `/v1`; the genai SDK appends `/v1beta/models/…`) — against the proxy's Gemini endpoint. The user's real `~/.gemini` (OAuth login, settings) is never touched: the child gets `USERPROFILE`/`HOME` = `<settings_dir>/data/agy-local-home` (agy is Go; `os.UserHomeDir()` reads `USERPROFILE` on Windows), created by `ensure_local_home()`, which forces only `modelProvider` and keeps whatever agy writes there. `GOOGLE_API_KEY` & co. are stripped (the SDK prefers it over `GEMINI_API_KEY`). The model is agy's custom-model URL form `--model gemini-api://local/models/<llama model>`: agy's catalog rejects any other unknown name, and with this form it still posts to `GOOGLE_GEMINI_BASE_URL`, putting `local/models/<name>` in the URL path, where the proxy takes the part after the last `/models/`. Local conversations live in the isolated home, so their id is a separate key, `last_antigravity_local_conversation_id` (cloud: `last_antigravity_conversation_id`). **Gaps:** the child's shell tools also see the isolated `USERPROFILE` (`~`, git/npm user config resolve there); agy fires a concurrent title-generation call (`gemini-3.1-flash-lite-preview`, routed to the loaded model) that competes for the single slot and is cancelled when the turn ends; no cost field.

**Why stdin for all three.** Every handler spawns with `shell=True`, and a prompt on the command line hits the Windows limit (~8 KB through a `.cmd` shim, 32 KB otherwise) — TeleDesign prompts stack the charter, a design system and comments and run far past it. Measured: a 48 KB prompt completes and resumes on Claude Code and Antigravity; Codex's `-` verified end to end on 0.157.

**Adding a fourth engine** = (1) new `services/task/handlers/<name>.py` matching the signature, (2) one entry in `AGENT_BRIDGE` (`services/task/staging.py`), (3) one entry in `ENGINE_TO_TASK_TYPE` (`services/task/engine_map.py`), (4) `register_handler(...)` in `task_registry.py`, (5) `<option>` in both `proxy/static/index.html` and `telecode.html`. No changes to executor / heartbeat / routine manager.

**Shared caveat — subprocess lifecycle.** All three handlers spawn raw `subprocess.Popen(..., shell=True, creationflags=CREATE_NO_WINDOW)` and do **not** bind to the process-wide Windows Job Object set up in `process.py`. If telecode is killed mid-run, the CLI child may outlive it (and any grandchildren it spawned). Mitigation today: handlers call `proc.terminate()` → `proc.kill()` on cancel/exit. Follow-up: migrate all three to a shared helper that goes through `process.py` so `KILL_ON_JOB_CLOSE` covers them. The hole has been there for Claude Code since day one — adding Codex/Antigravity didn't make it worse, just wider.

**stderr is drained on a thread (`task_utils.StreamDrain`).** The handlers iterate stdout only; an unread `stderr=PIPE` fills the ~4 KB Windows pipe buffer and the child blocks on its next stderr write while we block on its stdout — a silent hang. Codex hits it reliably on an expired ChatGPT login (one token-refresh error per request, even in local mode).

**Likelihood of upstream convergence:**
- *Codex* — high. OpenAI explicitly tracks Claude's automation surface (`--dangerously-bypass-approvals-and-sandbox`, `--json`, `--output-schema`, resume subcommands). Expect the event schema to stay JSONL and grow rather than break; the defensive `usage` field reads in `codex.py` are there to absorb minor renames.
- *Antigravity* — medium, and moving. The JSON stream, conversation IDs and a Gemini-API route with a custom base URL have all landed (see above); what remains is cosmetic (cost field, a real `--base-url` instead of the home-override).

---

## llama.cpp supervisor (`llamacpp/`)

Tracks llama-server **b10733** — `--no-ui` (not `--no-webui`), `draft-mtp` in `--spec-type`,
`--spec-default`, `--load-mode`, etc. `--mlock` / `--no-mmap` / `--direct-io` are **deprecated upstream**
and no longer emitted: `_emit_load_mode()` maps them onto `--load-mode`, whose accepted values are a
closed set verified by probing the binary — `auto | none | mmap | mlock | mmap+mlock | dio`. Wider
combinations (`mlock+dio`, `mmap+mlock+dio`) are rejected by the parser, so it is a choice, not a bitmask. `LlamaSupervisor.start_default()` runs in `_post_init` BEFORE the proxy; stdout+stderr → `data/logs/llama.log`. Shutdown (SIGTERM, 4s wait, kill) runs AFTER the proxy.

- **argv builder** (`argv.py`): table-driven `settings_key → --cli-flag` with per-row `kind` (`flag`/`onoff`/`bool_pair`/`value`/`value_nz`/`path`). `value_nz` skips zero (0 = "use default"); `value` emits literal 0 (0 = "disable"). Two tables: `_GLOBAL_FLAG_SPECS` (`llamacpp.*`), `_MODEL_FLAG_SPECS` (`llamacpp.models.<m>.*`). `spec_type` is comma-separated (v9243+); `ngram-mod` has its own `(n-min, n-max, n-match)` flags distinct from `(size-n, size-m, min-hits)` shared by other ngram strategies.
- **Model swap:** `ensure_model(name)` resolves via `llamacpp.models` → `proxy.model_mapping` → `default_model`. Different = stop + respawn + `/health` poll.
- **Ready probe:** `/health` `"ok"` = ready; 503/`"loading model"` = warming; connection error = down. Deadline `llamacpp.ready_timeout_sec` (default 120). 1s re-poll after `"ok"` catches orphans.
- **Version Manager** (`updater.py` + `flag_audit.py`, on-demand from the tray llama.cpp page's "Version Manager" card). Units are real binaries: the active `llama-server` plus every `.bak-<ts>/` the updater left behind (each a runnable previous build, tagged with `.telecode-version`). `flag_audit.probe(binary)` parses `--help` into `{flag → {aliases, takes_value, allowed, removed, deprecated}}`. **Test** = `audit_config(binary)` cross-checks every flag `build_argv` emits across all models against that build (unknown/removed flags + out-of-range enum values). **Compare** = `compare()` diffs the active build's flag surface vs a selected one (added/removed/changed). **Restore** = `updater.restore_backup(ts)` reverse-overlays a backup into the install dir (supervisor stopped first; displaced files become a fresh reversible backup). Probes run the real binary, falling back to a spec cached under `data/cli-audit/specs/b<ver>.json` when an old backup can't relaunch — the updater calls `flag_audit.record_version_spec()` before+after each install to populate that cache. Reports append to `data/logs/cli_audit.log` (in the Logs viewer allowlist). This is the guard that catches flag churn like the v9243 `--checkpoint-every-n-tokens` → `--checkpoint-min-step` rename before it breaks spawn.

- **Patched Build** (`patcher.py`, tray card below Version Manager). Clones llama.cpp, hard-resets to an
  upstream tag (empty = whatever the release updater considers current, so a patched build matches the
  release it replaces), applies `patches/llama.cpp/*.patch`, builds, and overlays the artifacts onto the
  same install dir with the same `.bak-<ts>` snapshot — so **Version Manager → Restore undoes a custom
  build exactly like a bad release**. `git apply --check` failing is a *result*, not an error to force:
  it means the patch landed upstream or bit-rotted. Applied-ness is derived by reverse-applying each
  patch rather than tracked in a file that can go stale. **The standing trap is the converse: "Update
  Now" overlays a release zip onto the same directory and silently replaces a custom build**, which is
  why `status()` compares the built binary against the installed one (`build_is_installed`) instead of
  trusting a flag. Deliberately shaped as *verify a patch before proposing it upstream*, not *maintain a
  fork* — the last attempt at a permanent fork was abandoned, and the cost was never the patch, it was
  keeping it alive across upstream churn.
   Current series: `0001-common-add-defer_loading-to-tool-definitions.patch` (ggml-org/llama.cpp#28179).

- **A subdirectory of `patches/llama.cpp/` is a whole third-party fork, vendored.** Different kind of
  thing from a top-level patch: not awaiting upstream, and thousands of lines or nothing — some GGUFs
  simply cannot be read by stock llama.cpp. `patches()` returns top-level (ours) first, then each vendor
  dir; `patch_key()` keys by relative path because two vendors both numbering from 0001 would otherwise
  alias each other in `applied_patches()`. Framework, resolution rule and regeneration runbook:
  [docs/vendor-patches.md](docs/vendor-patches.md).

- **`tools/vendor_drift.py` is how vendored ports stay current** — `status` (does the series still apply to
  its own tag? to newest upstream? has the vendor moved or rebased?), `conflicts <vendor> --onto <tag>`
  (3-way applies and prints **only** the hunks git could not resolve, formatted for a model to rewrite),
  `collisions` (two vendors claiming the same ggml type or arch string compile fine and then
  **silently misread weights** — each vendor declares `claims` in `VENDOR.json` and this is the only
  check that catches it). **Resolution rule: upstream-latest PLUS the vendor's feature, never one side
  wholesale — and check first whether upstream has since implemented the feature itself.**

**Keep `cache_ram` non-zero.** `--cache-ram N` is the host-RAM prompt cache in MiB (upstream default
8192; **0 disables it**), and `--cache-idle-slots` saves an idle slot's KV there when a new task claims
the slot instead of destroying it. With both off, a single short request evicts a long conversation and
forces a full re-prefill — measured 2026-09-03: a 2,206-token session-title call took the one slot
(`parallel: 1`) from a 67,883-token conversation, costing 50 s to rebuild. Costs host RAM, not VRAM.

**Settings layout (no duplicates):**
- `llamacpp.*` — server-wide CLI flags (threads, batch, load_mode, kv_*, spec_type, cache_ram, endpoints,
  server-mode, timeout, api_prefix, video_*).
- `llamacpp.models.<m>.*` — per-model flags re-taking effect on respawn (ctx_size, n_gpu_layers, n_cpu_moe, mmproj, rope_*, yarn_*, draft_*, lora, grammar, reasoning_*, override_*, device, chat_template).
- `llamacpp.models.<m>.inference_defaults.*` — proxy-applied request-body fields (temperature, top_p, max_tokens, stop, reasoning.*, chat_template_kwargs).
- `llamacpp.inference.*` — proxy-applied global fallbacks. Hierarchy: request body > per-model > top-level. "Proxy Behavior" card exposes only keys without per-model equivalent (`context_overflow`, `drop_prior_thinking`, `structured_output.*`, `reasoning_effort_map.*`).

**Reasoning is two layers, deliberately split.** `reasoning_effort_map` → `thinking_budget_tokens`, a llama.cpp *body* param (model-agnostic, so global; gated behind `thinking_budget.enabled`, **off by default**, and honoured only on builds ≥ b9982 with no `--reasoning-budget` on the CLI). Per-model `inference_defaults.reasoning_effort.{template_key,map}` → the *template* string, whose vocabulary each model defines itself — Qwen 3.8 accepts only `xhigh|medium|low` (aliasing `high`→`xhigh`) and `raise_exception`s otherwise, GPT-OSS wants `low|medium|high`. Map keys are Claude Code’s effort levels; values are the model’s. An unmapped or empty value emits nothing. Values are not validated — they are per-model and visible in the tray, so a bad one is a visible config error rather than something to guard here.

**`context_overflow` is implemented in the proxy, not llama.cpp.** llama.cpp has no prompt-truncation flag at all — `--context-shift` covers generation running past the context (and is disabled for many models), while an oversized *prompt* is rejected at admission with `exceed_context_size_error`. `_apply_context_overflow` runs last in `_prepare_internal_body`, tokenizes via `/apply-template` + `/tokenize`, and drops whole messages (never the leading system block, never the last two, tool_calls→tool groups move together). `truncate_middle` is the cache-friendly policy: the head stays byte-identical so the prefix cache survives.

**Thinking on/off is per-model and KEY-GATED** (`inference_defaults.thinking.{template_key,enabled}`). The key gates it, not the toggle: empty/absent `template_key` emits nothing and the template decides — so "leave it alone" needs no third state. With a key set, the toggle picks the value (on→`true`, off→`false`), which is why it both enables and disables. Per-model because the switch differs: Qwen 3.x/3.8 read `enable_thinking` (false makes the template prefill an empty `<think></think>`, so nothing is generated); models whose lever is an effort string use `reasoning_effort` instead. Distinct from `reasoning.enabled`, which only controls whether the proxy *parses* `<think>` — the model still generates it and still pays the context. The superseded `disable_thinking` shape is still honoured.

Server-wide flags must NOT appear in `_MODEL_DEFAULTS` — `argv.py` ignores them per-model.

---

## Proxy pipeline (`proxy/`)

Multi-protocol middleware in front of llama.cpp. Anthropic `/v1/messages`, OpenAI `/v1/chat/completions` and Gemini `/v1beta/models/*` run the full pipeline below; OpenAI `/v1/responses` is a passthrough (item 11). Internal canonical shape is OpenAI. `proxy.protocols` gates the routes (`anthropic` / `openai` — also covers `/v1/responses` / `gemini`; default all three). Port 1235; standalone via `python -m proxy`. Started from `_post_init` AFTER the supervisor.

1. **Profile match:** first `client_profile` whose `match.header` contains `match.contains`. **Model mapping:** `body.model` rewritten via `llama_cfg.resolve_model()`; response model reverse-mapped to client alias.
2. **Translation** (`translate.py`): Anthropic → OpenAI (`tool_use` → assistant `tool_calls`; `tool_result` → `role:"tool"` + lifted user message with image parts; `cache_control` dropped recursively; `system` flattened into leading `{"role":"system"}`). `_normalize_system_messages` merges only the **leading run** of system messages into index 0; what happens to a system message arriving mid-conversation is `proxy.mid_system_messages` (per-profile overridable): `demote` (default — keeps its position, re-roled to `user`, and held back past a `tool_calls`→`tool` run so the pairing stays adjacent), `strip`, `merge_top` (legacy hoist) or `keep` (no-op). Only `demote`/`strip` are both template-safe and cache-safe: `merge_top` satisfies Qwen’s "system must be first" check but appends to the tail of the front block every turn, shifting the whole conversation and pinning llama.cpp’s prefix cache (measured: 53% worst-turn reuse vs 100%). OpenAI is near-identity + `cache_prompt=true` + `stream_options.include_usage=true`. Defaults: request body > per-model > top-level.
3. **Managed-tool injection:** registry names + `strip_from_cc` → strip set; Anthropic schemas converted to OpenAI tools.
4. **Tool search** (`tool_search: true`): splits tools into core + deferred; `ToolSearch` meta-tool injected; deferred names in `<system-reminder>` on first user message. Auto-load (`auto_load_tools: true`): blind call → schema as tool_result, model retries. Otherwise blocks and instructs `ToolSearch(select:Name)`. Hallucination guard: unknown name → BM25 top-5.

   **Loading a tool costs a full re-prefill, and the append is load-bearing.** Tools render at
   position 0 of the Qwen template (the first system block opens with the `<tools>` list), so
   appending a loaded schema to `body["tools"]` shifts the entire prefix. llama.cpp's
   longest-common-prefix similarity collapses — core is only ~6% of a long prompt, well under the
   0.10 threshold — and it falls back to `selected slot by LRU`, re-prefilling everything
   (measured 2026-09-03: 68,390 tokens / 51 s on a 70K conversation).
   It cannot be fixed by *not* appending: with the tool undeclared, llama.cpp's lazy grammar
   constrains the call to the declared array, and the model does not fail — it silently calls the
   wrong tool. Measured: asked for `secret_lookup(record_id="R-4417")` with only `get_weather`
   declared, it reasoned correctly and then emitted `get_weather(city="R-4417")`. A permissive
   `{"type":"object"}` stub is no escape either; the argument grammar is built from the declared
   schema, so arguments collapse to `{}`. Declaring every deferred schema up front would cost
   ~10,400 tokens of permanent context (55 tools, 41,775 chars without descriptions).
   What *is* fixed: the loaded set used to revert on the very next request, because
   `_apply_tool_transforms` recomputes the split from the static `core_tools` list — paying the
   re-prefill a **second** time. `_sticky_tools` (LRU, 64 conversations, keyed on the `session_id`
   inside the client's `metadata.user_id`) keeps a loaded tool core for the rest of that
   conversation. One break per tool per conversation, not two.

   **With a llama.cpp that carries our `defer_loading` patch there is no break at all.** The flag
   declares a tool to the sampling grammar while leaving its schema out of the rendered prompt, so the
   proxy declares *every* tool from turn 1 (deferred ones flagged) and the declared set never changes:
   revealing a schema later is just a `tool_result` at the tail. Support is detected at runtime by
   `proxy/llama_caps.py`, never by build number — a local build may or may not carry the patch — and
   any probe failure answers False, falling back to the split above. Two things follow from that split
   in behaviour: on the patched path `_sticky_tools` is deliberately **inert** (everything is declared
   already, so promoting a tool would only make it *rendered*, moving the prefix), and the intercept
   loop skips the `body["tools"]` append entirely.
5. **System prompts:** `system_instruction` prepends a markdown file with `<if dotted.key="value">` conditionals; `inject_date_location` appends date+location as **plain text at the tail** of the system block — not wrapped in `<system-reminder>` (that got it stripped again a few steps later, making the flag a silent no-op) and not at the head (the date rolls over daily; at the tail only the conversation after it needs re-prefilling). `strip_reminders` drops `<system-reminder>` blocks **and** per-turn `<total_tokens>` budget lines, keeping skills + deferred-tools listings — re-appended at the tail in fixed order, so the result is byte-stable turn over turn.
5b. **Client-context stripping** (`proxy/tool_registry.py`, steps 3b/3c — after translation, BEFORE every injection of ours, so our own content can never be caught). Claude Code's preamble is the largest line item in the prompt: measured on a bare `say hi` in this repo, 58,168 chars of text (+93,964 of tool JSON). It arrives by **three** different carriers, and each needs its own lever:
   - **Leading system message** — `strip_client_system_noise()` removes the billing header, `# Environment` and `gitStatus:` **unconditionally, no setting**. `gitStatus` is the top cache-breaker in the whole request: at offset 5,955 it invalidates ~90% of the prompt (CLAUDE.md included) on every commit, branch switch and dirty-file edit. All three regexes are anchored on Claude Code's exact adjacent wording (`(?=\nYou have been invoked in the following environment)`, `(?=\s*This is the git status)`) because the same code path serves plain OpenAI clients, where `# Environment` is a heading anyone might write.
   - **Per-turn system messages** (agent roster / skills / MCP instructions — *not* reminders, so `strip_reminders` never reached them) — `strip_turn_context()`. Agent types go unconditionally; `strip_skills` and `strip_mcp_instructions` are per-profile toggles. Identified by **position** (anything after the leading system block), never role, because the default `mid_system_messages: "demote"` has already re-roled them to `user` by then.
   - **The `<system-reminder>` on the first user turn** — where CLAUDE.md actually lives ([the docs](https://code.claude.com/docs/en/memory) are explicit: "delivered as a user message after the system prompt, not as part of the system prompt itself"). `keep_claude_md: N` keeps the first N of the concatenated documents (managed policy → user → project → nested; measured 7,182 / 33,102 / 733 chars). `-1` leaves them alone, `0` drops them. It doubles as the **exclusion from `strip_reminders`**: the block rides inside the reminder, so `extract_claude_md()` re-wraps the kept documents into the preserve list rather than letting the wrapper take them.

  **`keep_memory: N` is a second count of the same shape** (`-1` all / `0` none / `N` first N), governing the auto-memory documents, and the CLAUDE.md count deliberately ignores them. The auto-memory index is CLAUDE.md-shaped but a different thing — model-written, **last** in load order, and the smallest document in the block — so one shared positional count drops it before anything else. Under the old single dial, `keep_claude_md: 2` in a project with its own CLAUDE.md kept a 13KB project file and silently discarded a 2.7KB memory index, with nothing in the log to say so. Both counts exist globally (`proxy.*`) and per-profile, profile winning. Setting `keep_claude_md: 0` alone no longer empties the block — pair it with `keep_memory: 0` for that.

  **`keep_rules: N` is the third count**, for `.claude/rules/**.md`. Rules are many and small where CLAUDE.md files are few and large, so one shared limit cuts by accident of load order: a project with six rules emits eight documents, and `keep_claude_md: 2` would keep the two big files and drop every rule.

  All three kinds are plural, which is why all three are counts rather than counts and switches:

  | kind | what it is | cardinality |
  | --- | --- | --- |
  | `claude_md` | managed policy, then each directory's `CLAUDE.md` + `CLAUDE.local.md` from `~/.claude` down to the project (`@path` imports expand inline, so they are **not** separate documents) | few, large |
  | `rules` | `.claude/rules/**.md` without `paths:` frontmatter, user- and project-level, walked recursively | many, small |
  | `memory` | the auto-memory index. `~/.claude/projects/<project>/memory/` holds `MEMORY.md` plus one topic file per memory, but [only the index loads at session start](https://code.claude.com/docs/en/memory) (first 200 lines or 25KB) — topic files are read on demand, so this count sees one document per request | one |

  **Classification is by path, because the labels collide.** Verified against a live request: a rules file arrives under the *same* `(project instructions, checked into the codebase)` label as a project `CLAUDE.md`, and a user-level rule under the same label as `~/.claude/CLAUDE.md`. Only `memory` has a label of its own (`(user's auto-memory, …)`). Path-scoped rules (`paths:` frontmatter) never reach this block at all — they arrive mid-conversation as a fresh reminder when Claude reads a matching file, which is a prefix-cache concern rather than a counting one.

   `strip_client_system_prompt` is the all-or-nothing version of the first bullet: empty the leading block entirely and let `system_instruction` stand in its place (13,854 → 5,159 chars on a live request). Two things it costs. It applies to **every** request the profile matches, including Claude Code's session-title call — a separate conversation whose entire instruction ("You are naming a coding session… return JSON with a single `title` field") lives in that block, so titles stop working. And with no `system_instruction` set the model gets no system prompt at all; the proxy logs a warning rather than silently doing it.

   Worth knowing about that title call generally: it carries the same `User-Agent`, so it matches the same profile and receives the full `system.md` — 5,094 of its 8,284 chars, 61% — to write five words with zero tools. It is the one request the proxy makes *bigger* (809 → 2,135 tokens). Splitting it off would need a second profile, and `_match_profile` inspects headers only.

   `_drop_empty_turns()` then prunes turns the strippers emptied — an empty turn is pure chat-template scaffolding (`<|im_start|>user\n<|im_end|>`). Tool-protocol turns (`tool_calls` / `role:"tool"`) are exempt from every stripper and from the prune, so the pairing templates require is never broken; turns carrying an image keep their remaining blocks.
6. **Intercept loop:** OpenAI internal shape. `_run_upstream_round` branches on first content signal. Tool_call → assemble → `InterceptedToolCall`. Otherwise stream live via adapter. `_start_heartbeat`: Anthropic gets `: keepalive` + `event: ping` every `proxy.ping_interval`; OpenAI gets `: keepalive` only. Up to `proxy.max_roundtrips` (default 15).
7. **Adapters** (`AnthropicAdapter`/`OpenAIAdapter`): per-round `*StreamState`. Status lines = synthetic content blocks at indices `0..status_emitted-1`. `<think>` openers across delta boundaries via max-tag-length lookahead. `thinking_delta` when `emit_thinking_blocks=true`.
8. **Multimodal — image, video, audio, handled uniformly.** llama.cpp takes `image_url`, `input_video` and
   `input_audio`, and it *throws* on any content type it does not recognise (`unsupported content[].type`),
   so every other client spelling must be **renamed, not forwarded**: `video_url` and `audio_url` become
   `input_*` in `translate.py::_normalize_media_parts`. `input_audio` happens to match OpenAI's own spelling,
   but its payload is still normalised — OpenAI puts raw base64 in `.data`, some clients send a `data:` URI,
   llama.cpp also allows `.url` — so exactly one shape goes upstream. `input_audio.format` is dropped;
   llama.cpp ignores it and sniffs the container (miniaudio: mp3/wav/flac).
   On the Anthropic side, `video` and `audio` blocks are **telecode extensions** mirroring its own `image`
   block — the Messages API has neither.
   `server.py::_inline_media_urls` resolves every remote URL through `media_fetch` *before* translation
   (translate.py is deliberately sync and pure); a refused URL becomes a 400 rather than a silent drop.
   That includes images, which used to be handed to llama.cpp to fetch: same SSRF primitive (llama.cpp runs
   on this machine, so a URL the caller cannot reach is one it can) and its own fetch caps at 10 MB / 10 s.
   The trade is that a **localhost or LAN image URL is now refused** — drop the `"image"` entries from
   `_URL_MEDIA_*` to restore it. Each kind needs an mmproj: audio wants an audio-capable projector
   (Qwen2-Audio, Ultravox, Voxtral), video additionally wants ffmpeg/ffprobe for `--video-fps` /
   `--video-timestamp-interval`.
9. **`count_tokens`:** full prepare → `/apply-template` → `/tokenize`. **`/v1/embeddings`:** forwarded verbatim. **CORS:** `cors_origins`; streaming gets headers via `_apply_cors_to_stream()` before `prepare()`.
10. **Gemini protocol (`/v1beta/models/{model}:generateContent | :streamGenerateContent?alt=sse | :countTokens`, `GET /v1beta/models[/{m}]`).** A third `ClientAdapter` (`GeminiAdapter`) on the same pipeline — profiles, model mapping, managed tools, intercept loop. Exists for Antigravity's local mode; shapes verified against agy 1.2.10 (google-genai-sdk 1.71, Go). Translation lives in `translate.py` (`gemini_request_to_internal`, `GeminiStreamState`, `openai_response_to_gemini`):
    - `systemInstruction` → leading system message; `contents[].parts` → `text`, `inlineData` (image → `image_url`, video/audio → `input_*`, text/* decoded), `fileData` (http(s) inlined by `_inline_media_urls` through `media_fetch`, like every other URL; gs:// / Files-API refs become a placeholder), `functionCall` → assistant `tool_calls`, `functionResponse` → `role:"tool"` **whatever the carrying role** (agy sends them under `role:"model"`), with `{output: str}` unwrapped and id-less responses paired to the oldest open call of the same name; `thought: true` parts follow `drop_prior_thinking`.
    - `tools[].functionDeclarations` take `parametersJsonSchema` (plain JSON Schema — what agy sends) or `parameters` (Gemini's OpenAPI subset: `STRING`/`OBJECT` lowered, `nullable` → `[t,"null"]`, `propertyOrdering`/`$schema` dropped). Google-hosted tools (`googleSearch`, `codeExecution`) are ignored with a log line. `toolConfig.functionCallingConfig` AUTO/ANY/NONE (+ `allowedFunctionNames`) → `tool_choice`.
    - `generationConfig` → temperature/topP/topK/maxOutputTokens/stopSequences/penalties/seed (request > per-model > top-level, as everywhere), `responseMimeType`/`responseJsonSchema` → `response_format`, `thinkingConfig.thinkingLevel`/`thinkingBudget` → effort (0 → `none`, −1 → model decides; an explicit budget only behind `thinking_budget.enabled`, so agy's Gemini-tuned 1024 cannot silently cap a local model), `includeThoughts` → `emit_thinking_blocks` (Gemini's default is **off**, so thoughts are dropped unless asked for).
    - Out: `candidates[0].content.parts` with `text` / `{text, thought:true}` / `functionCall{id,name,args}`. Function-call arguments are assembled and emitted whole (Gemini has no partial-args delta), and the finishing parts are held back so they share the final chunk with `finishReason` + `usageMetadata` (llama.cpp sends usage after the finish chunk). No `[DONE]`.
    - **Keepalive is `data: {}`, not `: keepalive`.** The genai SDK's stream reader fails the whole turn on any line that does not start with `data:` (`iterateResponseStream: invalid stream chunk: : keepalive` — measured), and an empty `GenerateContentResponse` is the one frame it tolerates. `_start_heartbeat(protocol="gemini")` sends that.
    - Model name = whatever follows the last `/models/` in the URL (agy's `gemini-api://local/models/<name>` lands as `/v1beta/models/local/models/<name>:…`). A name that is neither registered nor mapped (agy's built-in `gemini-3.1-flash-lite-preview` title call) goes to the **already-loaded** model, else last-active — never `default_model`, which could force a swap mid-task. Unknown `/v1beta/*` paths answer a Gemini-shaped 404 and log `gemini: unhandled …`.
    - The `antigravity` client profile (User-Agent `google-genai-sdk`) turns off `tool_search` and managed-tool injection: agy runs its own tool loop, ToolSearch status lines would land in agy's reply text, and managed `web_search` duplicates agy's `search_web`.
11. **`/v1/responses` is a passthrough to llama-server's own Responses endpoint** (ggml-org/llama.cpp#18486; this build b10733), not a translator — it exists for Codex, which speaks nothing else. Model mapping + `ensure_model` + inflight gating + SSE keepalive comments (Codex's parser skips them) + the model name reverse-mapped in every event. `translate.normalize_responses_request` keeps the body inside what llama.cpp accepts: non-`function` tools dropped (its converter supports function tools only; Codex's `custom`/`local_shell`/`web_search` would fail the request — logged), leading `developer`/`system` items folded into `instructions` and later ones demoted to `user` (Qwen raises on a non-leading system message), `cache_control` stripped, sampling defaults + per-model `chat_template_kwargs`/thinking switch/effort template merged, and **`reasoning_format: "deepseek"` set per request** — with the server's `--reasoning-format none` the `<think>` block otherwise lands in `output_text` (shown to the user and replayed as the assistant's words); with it llama.cpp emits proper `reasoning` items and turns replayed ones back into template thinking (measured). **Trade-off:** no managed-tool injection, no ToolSearch split, no intercept loop, no `system_instruction`, no reminder/CLAUDE.md stripping and no `context_overflow` on this path — the client's own tool loop runs against llama.cpp directly. Chosen because the passthrough worked first time with Codex 0.157 (tool round trips, resume); a translator would buy those features at the cost of re-implementing the Responses event stream.
12. **Client disconnects.** `_run_upstream_round` releases the supervisor's inflight slot in a `finally`, so a client that hangs up mid-stream (agy cancels its title call when a turn ends) no longer pins the inflight count — which used to disable idle-unload until restart. `_run_streaming` logs `client disconnected mid-stream` instead of an aiohttp traceback. aiohttp only notices on a later write, so generation runs on briefly after the hang-up.

To use: `llamacpp.enabled` + `proxy.enabled`, fill `llamacpp.binary` + `llamacpp.models.<name>.path`, point clients at `http://localhost:1235`.

---

## TeleDesign (`services/design/`, `proxy/api_design*.py`, `/design`)

Claude Design + pen.dev on **one canvas**, driven by the existing CLIs. Tray → **Open TeleDesign** →
`http://127.0.0.1:<proxy.port>/design`. Architecture: [docs/teledesign.md](docs/teledesign.md); the
interface every module codes against: [docs/teledesign-contract.md](docs/teledesign-contract.md); scope:
[docs/teledesign-parity.md](docs/teledesign-parity.md).

- **Boards.** Layer boards = native open-pencil frames in `doc.fig`. HTML boards = frames registered in
  `boards.json`, keyed by a **board key** stored inside the frame (open-pencil renumbers node ids on every
  reopen), with the generated page overlaid live in a sandboxed iframe.
- **Turns are direct task sessions, not Team Mode.** One session per project chat (namespace `design`),
  `DESIGN_TURN` task type, cwd = project folder, resumed per engine. `prompt_builder.py` stacks
  `prompts/` in README order; the brief (~56 KB) is sent on a chat's first turn and whenever it changes,
  otherwise the turn points at `.td/brief.md`. Post-turn: done gate (console errors → one fix turn) →
  verifier (`render.verify`) → thumbnail → auto-title. Events: in-process pub/sub → SSE `…/events`.
- **Two origins.** The API/UI on `proxy.port`; generated pages on `design.preview_port` (1237) —
  `preview.py`, CSP `connect-src 'self'`, bridge injected. Every mutating `/api/design/*` route requires a
  JSON/octet-stream/multipart content type and rejects a foreign `Origin` (middleware in `api_design.py`),
  so a preview page can never write. Preflights only pass for `proxy.cors_origins` — never add `*`.
- **Canvas editor** = patched open-pencil static build (`python tools/build_open_pencil.py`, patches in
  `patches/open-pencil/`, vendored at `proxy/static/design/editor/`). Agent tool calls reach the live page
  over `editor_bridge.py`'s WebSocket — no Node at runtime. Rebuild after bumping the tag; `--check` tells
  you whether the series still applies. Never hand-edit the vendored output or the disposable source dir.
- **Render/export** share one headless Edge (`render.py`, CDP, idle-closed, Job-bound); `render.stop()` on
  proxy cleanup. Exports are background jobs under `data/design/exports/` (24 h).
- **Design systems**: seeds in `services/design/seeds/` auto-install on first listing (stable uuid5 ids;
  deleted seeds stay deleted). `systems.py` stages a selective copy into `_ds/`, `lint.py` enforces
  `adherence.json`, `ds_bundle.py` precompiles components (node → Edge → in-browser fallback).
- **MCP tools** live in `mcp_server/tools/design.py` (the normal drop-in framework, auto-bridged to local
  models) and call the proxy over HTTP; canvas tools go through one tool, `design_canvas_call`.
- **Prompts are original.** Claude Design's leaked prompt and pen.dev's skill docs were read for behaviour
  only; never paste their text. open-design adaptations are listed in `prompts/NOTICE`. Every
  `{{placeholder}}` a prompt uses must be supplied by `prompt_builder._stable_values` or the turn — an
  unknown one raises `PromptError` and fails the turn (build every kind once after editing prompts).
- **Babel pitfall in generated/seed JSX:** no object-rest destructuring across `text/babel` files (Babel
  standalone's `_excluded` helper collides globally); wrap files in IIFEs, export via `Object.assign(window, …)`.

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
- **ToolSearch not triggered** — with `proxy.debug`, inspect `data/logs/requests/req_*.json` (`intercepts: []` means the proxy never intercepted; the round was decided as passthrough). The dumps directory is **cleared on every startup**, so capture the repro before restarting. `proxy_full_*.json` no longer exists. **Tools missing after search** — try `re:` prefix; check `MAX_SEARCH_RESULTS`. **MCP speak/transcribe** — VoxType on `:6600` or repoint URLs.
- **TeleDesign turn fails instantly** — `PromptError: unknown placeholder` means a prompt gained a placeholder the builder doesn't fill. **Preview blank / console 404s** — preview site on `design.preview_port` not up (look for `design preview` in telecode.log). **Canvas blank** — `proxy/static/design/editor/` missing → rebuild with `python tools/build_open_pencil.py`.
- **DocGraph host won't start** — `data/logs/docgraph_host.log`; verify `docgraph.binary`. **Kuzu lock error** — `IndexRunner` should route through `/api/admin/index` (check `docgraph_index.log` for "host route failed"). **Bridge tools missing** — host alive AND `/mcp` responding (uvicorn lifespan `on`).

---

## Running in background (Win)

`pythonw main.py` — no console. For auto-start: Windows Scheduled Task with `pythonw.exe`.

## Dependencies

**python-telegram-bot**, **aiohttp**, **aiofiles**, **pyte**, **pywinpty** (Win PTY), **mss**, **Pillow**, **pywin32** (Win Session 0), **pyautogui**, **mcp**. ffmpeg on PATH for video.
