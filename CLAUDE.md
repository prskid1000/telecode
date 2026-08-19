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

- **Claude Code** — `claude -p <prompt> --dangerously-skip-permissions --output-format stream-json --verbose --include-partial-messages [--resume <id>]`. Resume key: `last_claude_session_id`. Local mode env: `ANTHROPIC_BASE_URL=http://localhost:<proxy>/v1`, `ANTHROPIC_AUTH_TOKEN=local`, `ANTHROPIC_MODEL=<llama>`. Staging bridge: `AGENT.md ↔ CLAUDE.md`.
- **Codex** — `codex exec [resume <SID>] --json --dangerously-bypass-approvals-and-sandbox --sandbox danger-full-access --skip-git-repo-check -C <dir> --output-last-message <path> [--model <m>] "<prompt>"`. Resume key: `last_codex_session_id` (captured from `thread.started` events). Local mode env: `OPENAI_BASE_URL=http://localhost:<proxy>/v1`, `OPENAI_API_KEY=local`, `CODEX_API_KEY=local`. Staging bridge: `AGENT.md ↔ AGENTS.md`. Event mapping: `thread.started → store sid`; `item.completed{assistant_message}/reasoning → narrative`; `item.completed{command_executed,file_change,mcp_tool_call,tool_use,patch} → tool`; `turn.completed.usage → done`. Final text falls back to the `--output-last-message` file when no JSON `result` is emitted.
- **Antigravity (v1, plain text)** — `agy -p "<prompt>" --dangerously-skip-permissions --add-dir <dir> [--conversation <id>]`. Staging bridge: `AGENT.md ↔ AGENTS.md`. **Known gaps** (revisit when `agy` ships them): no structured JSON stream (community reports `--output-format json` is documented but not implemented), so tool calls are invisible — every stdout line becomes a `narrative` event; no conversation ID surfaces in `-p` mode, so resume is fresh-by-default (`-c` is process-global and unsafe across concurrent routines); no documented `--base-url` / `--model` flag, so `is_local=True` is accepted but warned-and-ignored. Token counts return zeros. The handler's external surface still mirrors the other two so the upgrade path is "rewrite the body of `_run_antigravity_subprocess`, nothing else."

**Adding a fourth engine** = (1) new `services/task/handlers/<name>.py` matching the signature, (2) one entry in `AGENT_BRIDGE` (`services/task/staging.py`), (3) one entry in `ENGINE_TO_TASK_TYPE` (`services/task/engine_map.py`), (4) `register_handler(...)` in `task_registry.py`, (5) `<option>` in both `proxy/static/index.html` and `telecode.html`. No changes to executor / heartbeat / routine manager.

**Shared caveat — subprocess lifecycle.** All three handlers spawn raw `subprocess.Popen(..., shell=True, creationflags=CREATE_NO_WINDOW)` and do **not** bind to the process-wide Windows Job Object set up in `process.py`. If telecode is killed mid-run, the CLI child may outlive it (and any grandchildren it spawned). Mitigation today: handlers call `proc.terminate()` → `proc.kill()` on cancel/exit. Follow-up: migrate all three to a shared helper that goes through `process.py` so `KILL_ON_JOB_CLOSE` covers them. The hole has been there for Claude Code since day one — adding Codex/Antigravity didn't make it worse, just wider.

**Likelihood of upstream convergence:**
- *Codex* — high. OpenAI explicitly tracks Claude's automation surface (`--dangerously-bypass-approvals-and-sandbox`, `--json`, `--output-schema`, resume subcommands). Expect the event schema to stay JSONL and grow rather than break; the defensive `usage` field reads in `codex.py` are there to absorb minor renames.
- *Antigravity* — medium-low. Google copied `--dangerously-skip-permissions` verbatim but the headless story (JSON stream, conversation IDs, base-url, model selection) is incomplete in this release. Plan for plain-text scraping to be the reality for several months; treat any JSON arrival as a v2 upgrade rather than baseline.

---

## llama.cpp supervisor (`llamacpp/`)

Tracks llama-server **v9243** — `--no-ui` (not `--no-webui`), `draft-mtp` in `--spec-type`, `--spec-default`, `--direct-io`, etc. `LlamaSupervisor.start_default()` runs in `_post_init` BEFORE the proxy; stdout+stderr → `data/logs/llama.log`. Shutdown (SIGTERM, 4s wait, kill) runs AFTER the proxy.

- **argv builder** (`argv.py`): table-driven `settings_key → --cli-flag` with per-row `kind` (`flag`/`onoff`/`bool_pair`/`value`/`value_nz`/`path`). `value_nz` skips zero (0 = "use default"); `value` emits literal 0 (0 = "disable"). Two tables: `_GLOBAL_FLAG_SPECS` (`llamacpp.*`), `_MODEL_FLAG_SPECS` (`llamacpp.models.<m>.*`). `spec_type` is comma-separated (v9243+); `ngram-mod` has its own `(n-min, n-max, n-match)` flags distinct from `(size-n, size-m, min-hits)` shared by other ngram strategies.
- **Model swap:** `ensure_model(name)` resolves via `llamacpp.models` → `proxy.model_mapping` → `default_model`. Different = stop + respawn + `/health` poll.
- **Ready probe:** `/health` `"ok"` = ready; 503/`"loading model"` = warming; connection error = down. Deadline `llamacpp.ready_timeout_sec` (default 120). 1s re-poll after `"ok"` catches orphans.
- **Version Manager** (`updater.py` + `flag_audit.py`, on-demand from the tray llama.cpp page's "Version Manager" card). Units are real binaries: the active `llama-server` plus every `.bak-<ts>/` the updater left behind (each a runnable previous build, tagged with `.telecode-version`). `flag_audit.probe(binary)` parses `--help` into `{flag → {aliases, takes_value, allowed, removed, deprecated}}`. **Test** = `audit_config(binary)` cross-checks every flag `build_argv` emits across all models against that build (unknown/removed flags + out-of-range enum values). **Compare** = `compare()` diffs the active build's flag surface vs a selected one (added/removed/changed). **Restore** = `updater.restore_backup(ts)` reverse-overlays a backup into the install dir (supervisor stopped first; displaced files become a fresh reversible backup). Probes run the real binary, falling back to a spec cached under `data/cli-audit/specs/b<ver>.json` when an old backup can't relaunch — the updater calls `flag_audit.record_version_spec()` before+after each install to populate that cache. Reports append to `data/logs/cli_audit.log` (in the Logs viewer allowlist). This is the guard that catches flag churn like the v9243 `--checkpoint-every-n-tokens` → `--checkpoint-min-step` rename before it breaks spawn.

**Settings layout (no duplicates):**
- `llamacpp.*` — server-wide CLI flags (threads, batch, mlock, kv_*, spec_type, cache_ram, endpoints, server-mode, timeout, api_prefix).
- `llamacpp.models.<m>.*` — per-model flags re-taking effect on respawn (ctx_size, n_gpu_layers, n_cpu_moe, mmproj, rope_*, yarn_*, draft_*, lora, grammar, reasoning_*, override_*, device, chat_template).
- `llamacpp.models.<m>.inference_defaults.*` — proxy-applied request-body fields (temperature, top_p, max_tokens, stop, reasoning.*, chat_template_kwargs).
- `llamacpp.inference.*` — proxy-applied global fallbacks. Hierarchy: request body > per-model > top-level. "Proxy Behavior" card exposes only keys without per-model equivalent (`context_overflow`, `drop_prior_thinking`, `structured_output.*`, `reasoning_effort_map.*`).

**Reasoning is two layers, deliberately split.** `reasoning_effort_map` → `thinking_budget_tokens`, a llama.cpp *body* param (model-agnostic, so global; gated behind `thinking_budget.enabled`, **off by default**, and honoured only on builds ≥ b9982 with no `--reasoning-budget` on the CLI). Per-model `inference_defaults.reasoning_effort.{template_key,map}` → the *template* string, whose vocabulary each model defines itself — Qwen 3.8 accepts only `xhigh|medium|low` (aliasing `high`→`xhigh`) and `raise_exception`s otherwise, GPT-OSS wants `low|medium|high`. Map keys are Claude Code’s effort levels; values are the model’s. An unmapped or empty value emits nothing. Values are not validated — they are per-model and visible in the tray, so a bad one is a visible config error rather than something to guard here.

**`context_overflow` is implemented in the proxy, not llama.cpp.** llama.cpp has no prompt-truncation flag at all — `--context-shift` covers generation running past the context (and is disabled for many models), while an oversized *prompt* is rejected at admission with `exceed_context_size_error`. `_apply_context_overflow` runs last in `_prepare_internal_body`, tokenizes via `/apply-template` + `/tokenize`, and drops whole messages (never the leading system block, never the last two, tool_calls→tool groups move together). `truncate_middle` is the cache-friendly policy: the head stays byte-identical so the prefix cache survives.

**Thinking on/off is per-model and KEY-GATED** (`inference_defaults.thinking.{template_key,enabled}`). The key gates it, not the toggle: empty/absent `template_key` emits nothing and the template decides — so "leave it alone" needs no third state. With a key set, the toggle picks the value (on→`true`, off→`false`), which is why it both enables and disables. Per-model because the switch differs: Qwen 3.x/3.8 read `enable_thinking` (false makes the template prefill an empty `<think></think>`, so nothing is generated); models whose lever is an effort string use `reasoning_effort` instead. Distinct from `reasoning.enabled`, which only controls whether the proxy *parses* `<think>` — the model still generates it and still pays the context. The superseded `disable_thinking` shape is still honoured.

Server-wide flags must NOT appear in `_MODEL_DEFAULTS` — `argv.py` ignores them per-model.

---

## Proxy pipeline (`proxy/`)

Dual-protocol middleware in front of llama.cpp. Both Anthropic `/v1/messages` and OpenAI `/v1/chat/completions` exposed; internal canonical shape is OpenAI. Port 1235; standalone via `python -m proxy`. Started from `_post_init` AFTER the supervisor.

1. **Profile match:** first `client_profile` whose `match.header` contains `match.contains`. **Model mapping:** `body.model` rewritten via `llama_cfg.resolve_model()`; response model reverse-mapped to client alias.
2. **Translation** (`translate.py`): Anthropic → OpenAI (`tool_use` → assistant `tool_calls`; `tool_result` → `role:"tool"` + lifted user message with image parts; `cache_control` dropped recursively; `system` flattened into leading `{"role":"system"}`). `_normalize_system_messages` merges only the **leading run** of system messages into index 0; what happens to a system message arriving mid-conversation is `proxy.mid_system_messages` (per-profile overridable): `demote` (default — keeps its position, re-roled to `user`, and held back past a `tool_calls`→`tool` run so the pairing stays adjacent), `strip`, `merge_top` (legacy hoist) or `keep` (no-op). Only `demote`/`strip` are both template-safe and cache-safe: `merge_top` satisfies Qwen’s "system must be first" check but appends to the tail of the front block every turn, shifting the whole conversation and pinning llama.cpp’s prefix cache (measured: 53% worst-turn reuse vs 100%). OpenAI is near-identity + `cache_prompt=true` + `stream_options.include_usage=true`. Defaults: request body > per-model > top-level.
3. **Managed-tool injection:** registry names + `strip_from_cc` → strip set; Anthropic schemas converted to OpenAI tools.
4. **Tool search** (`tool_search: true`): splits tools into core + deferred; `ToolSearch` meta-tool injected; deferred names in `<system-reminder>` on first user message. Auto-load (`auto_load_tools: true`): blind call → schema as tool_result, model retries. Otherwise blocks and instructs `ToolSearch(select:Name)`. Hallucination guard: unknown name → BM25 top-5.
5. **System prompts:** `system_instruction` prepends a markdown file with `<if dotted.key="value">` conditionals; `inject_date_location` appends date+location as **plain text at the tail** of the system block — not wrapped in `<system-reminder>` (that got it stripped again a few steps later, making the flag a silent no-op) and not at the head (the date rolls over daily; at the tail only the conversation after it needs re-prefilling). `strip_reminders` drops `<system-reminder>` blocks **and** per-turn `<total_tokens>` budget lines, keeping skills + deferred-tools listings — re-appended at the tail in fixed order, so the result is byte-stable turn over turn.
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
