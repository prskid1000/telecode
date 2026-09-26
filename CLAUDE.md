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
  Agent owns data/agents/<id>/internal/ — a git repo (SOUL/USER/AGENT/HEARTBEAT, memory/ = MEMORY.md index +
  typed topic files, skills/). See "Agent memory".
  Job.pipeline.steps run sequentially by phase; same-phase in parallel. Step kinds: agent (default) | map
  (fan-out over the previous handoff's items) | loop (body → check → feedback) | gate (human approval,
  run awaiting_input) | reduce (merge the previous phase's handoffs) — the last four own their phase.
  stage_for_run() copies SOUL/USER/MEMORY → workspace (backing up the workspace's own copies), AGENT.md →
  --append-system-prompt-file (Claude) / AGENTS.md (Codex, agy), skills → .agents/skills + .claude/skills; on exit
  the write-back is a git commit run:<id> step:<id> (per-run branch merge when others wrote), restores.
  Engine/model/local per step: step override > run body > agent default. Resume id per (workspace, agent, engine).

Triggers (the one scheduler, both modes): data/telecode.db triggers + trigger_fires. Target task | agent_prompt |
  job; schedule cron(IANA tz) | every | at; events webhook (bearer) | GitHub (HMAC + filters) | file watch.
  Daemon thread in the proxy. HEARTBEAT.md compiles to agent_prompt triggers (fire on schedule only while
  heartbeat.enabled). Routines and the heartbeat loop are gone (migrated once).
Approvals: data/telecode.db approvals (gate | tool | memory) → web inbox badge + Telegram Approve / Edit & approve /
  Reject; gate deadlines (timeout_sec + on_timeout) resolved by a checker thread (decided_by "timeout").
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
- `proxy/api_{sessions,tasks,agents,jobs,runs,triggers,approvals}.py` — REST surface (no auth; the two trigger
  webhook routes authenticate per trigger). Any caller-supplied
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
- `services/engine/` — the Engine Runner (`runner.py`, `spawn.py`, `task_bridge.py`, `adapters/`); `services/db/` — `data/telecode.db` (tasks, events, runs, steps, session lineage, approvals, triggers + fires); `services/bus.py` + `proxy/api_events.py` — SSE. See "Task engines".
- `services/task/staging.py` — `stage_for_run()` ctx-mgr: back up the workspace's own same-named files (`data/staging_backups/`, crash-repaired on the next stage), copy in (MEMORY.md = the memory index; agent skills into `.agents/skills/` + `.claude/skills/`, never over the workspace's own), write back as a commit in the agent's internal repo, restore. Per-workspace `Lock`. HEARTBEAT.md NOT staged. See "Agent memory".
- `services/memory/` — agent memory (P4): `repo` (git of `internal/`), `store` (index + topics, migration, pinned), `merge` (`merge3` fallback), `reflection` (trigger → proposal → `memory` approval), `engine_extras()` / `pinned_constraints()`. `services/skills/agent_skills.py` — per-agent skills. REST in `proxy/api_agents.py` (`/memory…`, `/pinned`) and `proxy/api_skills.py` (`/api/agents/{id}/skills…`). UI: `proxy/static/shared/memory.js` + `memory.css`.
- `services/task/task_manager.py` — queue with two pools (`interactive`: Task submits + TeleDesign; `background`: trigger fires, run steps; `tasks.pools.{interactive,background}_workers`, default 4/3, read at startup). One cancel path, `cancel_task()` / `TaskQueue.cancel`: never overwrites a finished task, stamps `completed_at`, tree-kills every pid the handler registered (`task_utils.track_process`). `task_timeout_seconds` is enforced by a watchdog (tree-kill → FAILED `"timeout"`). Finished tasks evicted after 1 h (newest 500 always kept); `start` events keep a prompt digest (first 2 KB + len + sha256), not the prompt.
- `services/session/session_store.py` — session folders + `session.json`. Ephemeral sessions (`data.ephemeral` or namespace `run-parallel`/`trigger`/`heartbeat`) are deleted on expiry; everything else is a workspace: absolute TTL ignored, idle expiry **archives** to `data/task_sessions/_archived/<ns|~root>/<id>`, and `ensure()` (every submit) restores it and refreshes `last_used_at`. A session with a pending/running task never expires. Idle `0` = never; `POST /api/sessions` without a TTL creates a never-expiring workspace. `GET /api/sessions?include_archived=1`, `POST /api/sessions/{id}/restore`.
- `services/task/safe_paths.py` — `validate_id` / `resolve_in`, used by the agent, job and run stores and their APIs (bad id or escaping filename → 400).
- `services/triggers/*` — the one scheduler (see "Triggers"). `services/approvals.py` — the approvals inbox (see "Approvals").
- `services/run/executor.py` — pipeline driver: Run → Step → Attempt (attempts live in the step record). Per-step **session policy** `resume|fork|fresh|fresh_handoff|ephemeral` (blank = resume for a single-step phase; a parallel phase is always ephemeral). Each attempt: shadow-git snapshot before/after → structured **handoff** (`services/run/handoff.py`; Claude `--json-schema`, Codex `--output-schema`, agy `--json-schema <file>` (fallback: a `.telecode/handoff.json` it wrote), else derived from the reply) → artifacts copied to `data/runs/<run>/artifacts/<step>/` → the next step gets `<handoff>` blocks. **Budgets** (`services/run/budget.py`): run budget (POST body over `job.budget`) split over remaining steps, per-step override, enforced per attempt (tokens/wall clock by the runner, Claude `--max-budget-usd`) → `budget_exceeded`. **Retries**: `retry_step(run, step, retry|retry_clean)` continues downstream; `spec.auto_retry` for transient failures. `step_diff` / `revert_step`. Runs/steps can be `budget_exceeded`; `reconcile_orphaned_runs()` marks leftovers `interrupted` (retryable). **Step kinds (P3)**: `map` (workers in `step.workers[]`, `max_parallel`, even budget share per worker, ephemeral copies — or `worker_session: fork`: each worker gets its OWN copy of the planner's post-step workspace plus a fork of the planner's conversation, so forks run in parallel; `services/run/fork_workspace.py` copies the planner's Claude `<id>.jsonl` into `~/.claude/projects/<encoded cwd>/` of the worker folder (every non-alphanumeric char of the absolute cwd → `-`) (Claude resumes only from its own cwd's project dir; `CLAUDE_CONFIG_DIR` honoured) and removes the copy after; Codex rollouts are global so `exec fork` needs nothing; agy / cross-engine / missing session file / a CLI "no conversation found" → that worker alone runs fresh + the planner's handoff, `worker.fork_fallback` says why; workers' files come back as artifacts only, never into the planner workspace; real concurrency is capped by `tasks.pools.background_workers`), `loop` (`step.iterations[]`; check = command in the workspace / grader agent in a FRESH session on a copy / JSON Schema via `services/run/jsonschema_lite.py`), `gate` (approval row → step + run `awaiting_input`, driver exits; `_on_gate_decided` resumes from the next phase — edited text = the gate's handoff — or ends the run `rejected`; optional `timeout_sec` / `on_timeout`, see "Approvals"), `reduce` (all previous handoffs + merge instructions, fresh by default). Trigger-fired runs carry `overrides.permission_mode` and `job_snapshot.context/pinned`. Details: docs/agent-team-architecture.md "P2 as built" / "P3 as built".
- `services/snapshots/` — shadow git per workspace (`data/snapshots/<ns|root>--<sid>.git`, `--git-dir/--work-tree` only, never the workspace's own `.git`): parentless commits kept by `refs/snapshots/*`, `take / log / latest / changed_files / diff / restore (safety snapshot first) / export (git archive into a new folder) / prune`; `tasks.snapshots.{enabled,keep,max_file_mb,max_repo_mb}`. Routes under `/api/sessions/{sid}/snapshots` and `/api/runs/{run}/steps/{step}/{diff,revert,retry,artifacts/…}`.
- `services/design/**`, `proxy/api_design*.py`, `proxy/static/teledesign.html` + `proxy/static/design/` — TeleDesign (see its section).
- `mcp_server/app.py`, `tools/*` — FastMCP (stateless streamable HTTP, port 1236). Drop-in auto-discovery. `tools/approvals.py` = `approve_tool` (P5 permission prompts).
- `services/telemetry/*`, `proxy/api_telemetry.py`, `proxy/api_continue.py`, `services/engine/{otel,handover}.py` — P5 observability, safety, cross-engine continue (see "Task engines").

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
- Telegram section: Bot Control card (Start/Stop/Restart, auto_start/auto_restart) + Paths/Streaming/Capture/Triggers.
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

## Triggers (`services/triggers/`, `proxy/api_triggers.py`)

One scheduler for everything that starts work on its own — replaces Routines and the Heartbeat loop. **The
scheduler thread runs inside the proxy** (`scheduler.start()` from `start_proxy_background()`) — bot-only
deployments don't fire.

- **Record** (`triggers` table, full JSON in `data`; `model.py` validates): `target {kind: task | agent_prompt | job, prompt, engine, model, is_local, agent_id, workspace_id, id}`, `schedule {cron, tz} | {every_seconds ≥ 60} | {at, tz}` (none = events / by hand), `events {webhook {enabled, token}, github {enabled, secret, events, branches, authors, labels}, file {enabled, workspace_id, glob, debounce_seconds}}`, `session shared | fresh`, `active_hours {start, end, tz, days}`, `skip_if_empty {path | heartbeat_section}`, `ok_suppression` + `ok_tokens` (default `HEARTBEAT_OK`, `NO_REPLY`), `notify`, `model_override`, `goal {check_command, features_file, max_fires, max_cost_usd}`, `auto_pause_after_failures` (3), `catch_up skip | once`, `pinned`, `permission_mode` (default `auto`), `effort` (null = CLI default), `task_timeout_seconds`, `preface`, `outputs_only`, `state {next_fire_at, last_fire_at, counters, consecutive_failures, cost_usd, paused_reason}`. History = `trigger_fires` rows (`running | completed | ok | skipped | failed | cancelled | interrupted`; consecutive identical skips collapse into one row with a count).
- **Fire** (`fire.py`, under a per-trigger `RLock`, record re-read inside): due? → previous fire still running (→ skipped) → goal limits (max fires / cost → pause + notice) → active hours (not for "fire now") → skip-if-empty (not for "fire now") → submit. task / agent_prompt → a queue task on the background pool (shared = the trigger's permanent session `trigger-<id8>` or the agent's workspace; fresh = a throwaway session in namespace `trigger`); job → `executor.create_and_launch` with the preface/payload as `job_snapshot.context` and the pinned constraints at every step's tail. Prompt: preface → directive → `<trigger-payload untrusted="true" source="webhook|github|file">…</trigger-payload>` (always for event payloads; `</trigger-payload` inside is neutralised; 64 KB cap) → output rule → `<pinned_constraints>` (trigger's + the agent's `## Pinned constraints`). A one-off `at` disables itself.
- **Completion** (`reconcile_fire`, every tick + inline on reads): a reply matching an OK token → `ok` (no notification); failures build the streak → auto-pause at K; the goal (check command exit 0 in the workspace / `features.json` all passing / limits) → pause + `trigger.notice` (Telegram).
- **Scheduler** (`scheduler.py`): tick every `triggers.tick_seconds` (15), at most `triggers.max_fires_per_tick` (4); file watch polls every 2 s (first scan = baseline, fires `debounce_seconds` after the last change with the changed paths as payload, absorbs changes made while its fire runs); HEARTBEAT.md compiled every 60 s. Catch-up: a due time more than max(3 × tick, 120 s) old was missed → `skip` records one skipped fire and moves on, `once` fires once.
- **HEARTBEAT.md** (`heartbeat.py`) is an authoring format: each ```yaml entry (`name, prompt, cron | every | at, tz, workspace ephemeral|persistent, engine, model, is_local, enabled, active_hours, skip_if_empty, ok_suppression, notify, catch_up, goal, auto_pause_after_failures, pinned, permission_mode, timeout`) compiles to a trigger with `source: heartbeat`, `source_key: hb:<agent>:<name>` on save / reconcile / every minute; removed entries delete their trigger; a UI pause survives recompiles; `enabled: false` → disabled. These fire on schedule only while `heartbeat.enabled` (fire-now always works) and are read-only over REST except pause/resume.
- **Safety**: Claude runs from triggers get `--permission-mode <mode> --permission-prompts none` (`adapters/claude.permission_args`; mode via task metadata `permission_mode` → `EngineRequest.permission_mode`); `skip` / `bypassPermissions` fall back to `--dangerously-skip-permissions`. Verified on claude 2.1.282: sonnet honours `auto`; haiku reports `default` (auto unavailable), where "no prompts" denies edits and commands. Codex / agy map the same modes onto their own sandbox / mode flags — table under "Task engines" → **Permission modes**.
- **Migration** (`migrate.py`, once per DB, `meta.triggers_migrated`): `data/routines/*.json` → task triggers (renamed `data/routines.migrated`), kind=heartbeat jobs → `data/jobs/_migrated_heartbeat/`, `heartbeat-state.json` last fires → trigger state (renamed `.migrated`).
- **API**: `GET/POST /api/triggers` (list masks the token / secret), `POST /api/triggers/preview`, `GET/PATCH/DELETE /api/triggers/{id}`, `…/{pause,resume,run-now,token,fires}`; webhooks `POST /api/triggers/{id}/fire` (`Authorization: Bearer <token>`, body ≤ 256 KB) and `POST /api/triggers/{id}/github` (`X-Hub-Signature-256`; `ping` → pong; filters) — every auth failure is 401. Deleting a job deletes its triggers; deleting an agent deletes its HEARTBEAT.md triggers.
- **UI**: `/shared/triggers.js` (list, detail with history + webhook URL/token/curl copy, editor). Task Mode: **Triggers** tab + **By trigger** history. Team Mode: rail **Triggers** section, `#/trigger/<id>`, a Triggers card on each job and agent.

## Approvals (`services/approvals.py`, `proxy/api_approvals.py`, `bot/approval_handlers.py`)

- `approvals` rows `{kind gate|tool|memory, run_id, step_id, title, body, payload, status pending|approved|rejected|cancelled|skipped, decided_*, edited_text, telegram}`; `decide()` is an atomic pending→decided UPDATE (web and Telegram can't both win; the loser gets 409 / "already approved"), then runs the kind's handler (gate → `executor._on_gate_decided`). Pending rows survive restarts.
- **Gate timeouts**: a gate's `timeout_sec` (≥ 10, optional) + `on_timeout reject|approve|skip` (default reject) → the approval's `payload.deadline_at` / `on_timeout` (surfaced top-level; also on the step as `deadline_at` / `on_timeout`). `approvals.expire_due()` resolves overdue pending rows with `decided_by = "timeout"` (reserved — `decide(by="timeout")` is refused): reject → `rejected` (run ends rejected), approve → `approved` (run continues), skip → approval `skipped`, gate step `skipped` with `gate_decision.status = "skipped"`, which the executor and `run_store.finalise` treat as passed. A daemon thread (`start_timeout_checker`, every 5 s, first pass immediate) is started by `reconcile_orphaned_runs()` at startup when any deadline is pending and by `create()` for a row with a deadline — the deadline lives in the table, so a restart catches up. Usual `approval.decided` SSE + Telegram edit.
- REST: `GET /api/approvals?status=pending|…|all`, `POST /api/approvals/{id}/approve {note?, edited_text?}` / `…/reject`. Live: `approval.created|decided` on `GET /api/events?kinds=approval`.
- UI: inbox button + count in `<tc-appnav>`'s right slot (`manager.js` `approvalsButton` / `openApprovalsInbox` / `approvalCard`); the run monitor renders a gate's card inline. Pages share ONE global SSE (`globalFeed`) — browsers allow ~6 connections per host.
- Telegram: `ApprovalNotifier` (bot loop) posts pending approvals to General (`telegram.group_id`) with Approve / Reject buttons (`apv:a|r:<id>`, pattern-matched before the catch-all callback handler), edits the message when decided anywhere, sweeps every 30 s for approvals created while the bot was down, and posts `trigger.notice`s. Only `telegram.allowed_user_ids` may decide — an empty allowlist means nobody decides from Telegram. **Edit & approve** (`apv:e:<id>`, gate + tool, not memory): the bot posts a `ForceReply(selective)` prompt @-mentioning the presser; only that user's text reply *to the prompt* within 15 min and ≤ 4000 chars approves, with the reply as `edited_text` (tool: must be a JSON object) — the same `decide()` the web path uses. Pending prompts are in memory (`_edit_prompts`; a restart forgets them). `handle_approval_reply` is registered in handler group -2 (`filters.TEXT & filters.REPLY`) and raises `ApplicationHandlerStop` only for replies it handled, so other replies still reach `handle_text`. A pending gate with a deadline shows it.

---

## Agent memory (`services/memory/`, `services/skills/agent_skills.py`)

- **`internal/` is a git repo** (`data/agents/<id>/internal/.git`, own history, never a workspace's git; plain git, `CREATE_NO_WINDOW`, global config ignored, LF files). Every change is a commit: staging write-back `run:<run> step:<step>` / `task:<id>`, UI/API edits `ui: …`, reflection `reflection: …`, reverts `revert: …`, the migration `migrate: …`; trailers `telecode-<kind|run|step|task|approval>: …`. `repo.lock_for(dir)` is the one per-agent lock (agent_manager writes take it too).
- **Write-back** = one commit on main when nothing else committed since staging (it also carries files the engine wrote straight into `memory/`); otherwise the run's commit is built on the staged base in a temp index and merged as a per-run branch (`git merge --no-ff`); only files git reports conflicted go through `merge3` (same-point appends keep both; true overlaps → `<<<<<<< this run` markers + a warning). No git on PATH → plain `merge3` writes.
- **Layout**: `memory/MEMORY.md` = the index (API name `MEMORY.md`; ≤200 lines / 25 KB, one `- [Name](file.md) — description` per memory) + topic files with frontmatter `name, description, type: user|feedback|project|reference` (+ `helpful`/`harmful` on feedback). Only the index is staged (first line names the topic dir when topics exist; stripped on write-back). A pre-P4 `internal/MEMORY.md` is migrated once on first touch (`store.ensure`): committed, renamed `MEMORY.legacy.md` (kept in history), split by headings into topics, removed; `reflection.start()` sweeps all agents at proxy startup.
- **`engine_extras(agent_id, engine, workspace)`** (called by the Engine Runner): Claude → `--settings data/agents/<id>/claude-memory-settings.json` (`{"autoMemoryDirectory": "<internal/memory>"}` — verified on 2.1.282: auto memory then loads that index) + `add_dirs [memory]`; Codex / agy → `add_dirs [memory]`; a reflection fire → Claude `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, nothing else.
- **Pinned constraints** = the `## Pinned constraints` section of AGENT.md, one source: `services.memory.pinned_constraints()` (triggers append it at the tail of every fire via `_common.pinned_constraints`; rotation carries it). `PUT /api/agents/{id}/pinned`.
- **Skills**: `internal/skills/<name>/SKILL.md` (+ files), staged per run and removed after (manifest `skills` → crash-repaired). Promote a global skill (`POST /api/skills/{name}/promote {agent_id}`) / copy to global (`POST /api/agents/{id}/skills/{name}/copy-to-global`); 409 on a name clash without `overwrite`.
- **Reflection**: one `agent_prompt` trigger per agent (created via `services.triggers.service`, fresh session, agent's engine/model, cloud unless `tasks.memory.reflect_local`, cron `tasks.memory.reflect_cron` in `reflect_tz`, **paused** unless nightly is on). Fires nightly, after `tasks.memory.reflect_after_runs` runs (default 10, 0 = off; per-agent override; a pipeline run counts once; counted in staging), or on Reflect now. Staged with `.telecode/memory_reflection_input.md` (index, topics + counters, pinned, recent runs' handoffs / replies / errors) and never writes back; its JSON reply (`{summary, operations:[{op: ADD|UPDATE|DELETE|NOOP, file, name, description, type, body, helpful, harmful, reason}]}`) becomes a proposal commit (`refs/proposals/<task>`, work tree untouched) + an approval of kind `memory` (body = diff). Approve → merged into memory; reject → ref dropped; a newer proposal supersedes. State: `data/agents/<id>/reflection.json`; a 30 s ticker (proxy) turns finished fires into approvals.
- **REST**: `GET /api/agents/{id}/memory`, `PUT …/memory/index`, `POST …/memory/index/rebuild`, `GET|PUT|DELETE …/memory/topics/{file}`, `GET …/memory/history?limit&path`, `GET …/memory/diff?commit&path`, `POST …/memory/revert {commit}` (409 on overlap), `GET|PUT …/memory/reflection {nightly, after_runs}`, `POST …/memory/reflect` (409 while one runs), `GET …/memory/engine-extras?engine`.

## Task engines (`services/engine/`, `services/task/handlers/`)

Three CLIs are dispatched as task types, all sharing the **same handler signature** (`prompt, is_local, agent_id, agent, job, agent_files, job_files, model, schema`) and the same `_agent_task_schema`. The executor and the trigger scheduler are engine-agnostic — they pick the task_type via the single shared map in `services/task/engine_map.py`:

| engine string | task_type     | binary  | handler                                  |
|---------------|---------------|---------|------------------------------------------|
| `claude_code` | `CLAUDE_CODE` | `claude`| `services/task/handlers/claude_code.py`  |
| `codex`       | `CODEX`       | `codex` | `services/task/handlers/codex.py`        |
| `antigravity` | `ANTIGRAVITY` | `agy`   | `services/task/handlers/antigravity.py`  |

**One Engine Runner.** Every CLI run — the three handlers *and* TeleDesign's `design_turn_handler` — goes through `services.engine.run_engine(EngineRequest) -> EngineResult` (`runner.py`, sync, on the task-queue thread). The handlers are thin wrappers: `handlers/_common.prepare()` (prompt, session, raw-log path, scoped resume id) → `stage_for_run` → `_run_*_subprocess` → `engine/task_bridge.task_request()` + `run_in_task()`, which bind the request to the current task (events → `task_utils.append_event`, progress, `is_cancelled`, pid + stop registered with the queue, the task's timeout, a session-lineage row). Engine specifics are thin adapters, `services/engine/adapters/{claude,codex,antigravity}.py` — argv, env, stdin form, stream parser, usage normalisation, finish/failure rules. The runner owns the rest:
- **Spawn** (`engine/spawn.py`): no shell — `shutil.which` + direct spawn, so `proc.pid` is the CLI. A `.cmd`/`.bat` npm shim is unwrapped to `node <script.js>`; any other batch shim runs as a `cmd.exe /d /s /c` command-line string after its args are checked for cmd metacharacters (the prompt is never on argv). On Windows the CLI is created `CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`, assigned to `process.py`'s kill-on-close Job **and** a per-run Job nested in it, then resumed (`NtResumeProcess`) — nothing it spawns can start outside the Jobs.
- **Stop** (cancel via `TaskQueue.cancel` / TeleDesign Stop, the queue's timeout watchdog, or `EngineRequest.timeout_sec` / `cancel_check` seen by the runner's watcher thread even while the CLI is silent): non-blocking; CTRL_BREAK to the CLI's process group from a helper attached to the CLI's own hidden console (telecode's console is never signalled), `tasks.kill_grace_sec` (default 3) grace, then `TerminateJobObject` on the per-run Job (reaches grandchildren whose parent already exited, which `taskkill /T` cannot) + tree-kill. The per-run Job has no kill-on-close flag, so processes an agent deliberately leaves running survive the run (until telecode exits), as before. Raises `EngineCancelled("Task cancelled")` / `EngineTimeout("timeout")`; CLI failures raise `EngineError` with the CLI's stderr (TeleDesign's stale-resume regex matches it).
- **Normalised events** (`engine/types.EVENT_KINDS`): `start | delta | narrative | tool | todo | usage | warning | retry | done | error`. Tool events carry `tool`, `name`, `summary` and (agy) `input`, so every consumer's old keys still work. `delta` is streamed live only (SSE) — except agy, whose only text channel it is, persisted as `narrative_delta`.
- **Raw logs unchanged**: stdout is copied line by line to `data/task_logs/<task_id>.jsonl` (agy task runs: `.txt`), same paths and format — TeleDesign's `_drive` tails them.
- **Structured output** (`schema` param): Claude `--json-schema` (result `structured_output`), Codex `--output-schema <tmp file>` (last message parsed as JSON), agy `--json-schema <tmp file>` (agy ≥ 1.2.11; a path, not an inline string — Windows argv length/quoting; in stream-json it applies to the final `result` event only, whose `result.structured_output` holds the object — `response` repeats it plus agy's `toolAction`/`toolSummary` submit keys, used minus those as a fallback; verified 1.2.11, fixture `agy_schema_result.jsonl`). Pipeline/rotation handoffs for agy take that answer; `.telecode/handoff.json` is no longer requested, only read as a fallback when the schema answer is missing/invalid (`handoff.agy_structured`, from `handlers/_common`). Returned as `result.structured_output` when a schema was given.
- **P2 request fields**: `fork` (Claude `--fork-session`, Codex `exec … fork <id>`, agy runs fresh), `max_usd` (Claude `--max-budget-usd`), `max_tokens` / `max_seconds` (runner-enforced; budget tokens = input + cache writes + output) → `EngineBudgetExceeded("budget_exceeded: …")`. The final `usage` event is emitted before `finish`, and `task_bridge` keeps `metadata.engine_session_id` / `usage_live` on the task, so a failed attempt can be resumed and accounted. Handlers take `step_ctl` (session / resume_id / fork_from / budget / add_dirs / handoff / rotate) from the executor; `_common.run` also does **session rotation** (`tasks.rotate_after_tokens` 400k, trigger fires `tasks.rotate_after_fires` 50: ask the old conversation for a handoff, continue fresh with it + AGENT.md's `## Pinned constraints`).

**Per-engine specifics** (kept parallel to Claude on purpose so future Codex/Antigravity feature parity is a one-file patch):

- **Claude Code** — `claude -p --dangerously-skip-permissions --output-format stream-json --verbose --include-partial-messages [--resume <id>]`, prompt on **stdin**. `[--model <m>]` in cloud mode; the agent's AGENT.md goes in with `--append-system-prompt-file data/runtime/agent_prompts/<task>.md` (deleted after the run) — the workspace's own `CLAUDE.md` is never written. Note the CLI records the system prompt on a conversation's first request and replays it on resume until compaction, so an edited AGENT.md reaches an already-resumed conversation only after that. Local mode env: `ANTHROPIC_BASE_URL=http://localhost:<proxy>` (no `/v1` — the SDK appends it), `ANTHROPIC_AUTH_TOKEN=local`, `ANTHROPIC_MODEL=<llama>`, `CLAUDE_CODE_MAX_OUTPUT_TOKENS=config.tasks_local_max_output_tokens()` (`tasks.local.max_output_tokens`, default 16384 to match `claudel.bat`). (`AGENT_BRIDGE["claude"] = CLAUDE.md` remains for callers of `stage_for_run` that pass no `agent_md_file`.)
- **Codex** — `codex exec [-c …] --sandbox danger-full-access -C <dir> [resume <SID>] --json --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --output-last-message <path> [--model <m>] -` (prompt on **stdin**, verified on codex-cli 0.157; the sandbox/bypass flags shown are the skip-mode default — see **Permission modes**). `--sandbox` and `-C` are `exec`-only and must precede `resume`, which rejects them. Resume id from `thread.started.thread_id`. Usage: `turn.completed.usage` is `{input_tokens, cached_input_tokens, output_tokens, reasoning_output_tokens[, cache_write_input_tokens]}` — **`cached_input_tokens` is a subset of `input_tokens`** (the handler maps `tokens.input` = non-cached, like Claude); summed across `turn.completed` events, `num_turns` = their count, `duration_ms` = wall clock, `cost_usd` = `None` (Codex reports no cost). Staging bridge: `AGENT.md ↔ AGENTS.md`. Event mapping (0.157 names, old spellings kept): `item.completed{agent_message} → narrative`; `item.completed{error} → warning`; `item.completed{command_execution,file_change,mcp_tool_call,web_search} → tool`; `item.*{todo_list} → todo`; `turn.completed.usage → usage` (totals in `done`); `turn.failed` / `error` → `retry` (an object `error` is flattened to its message); final text from `--output-last-message`.
  **Local mode** = a `-c` provider, never env: `-c model_provider=telecode -c model_providers.telecode.{name=telecode, base_url=http://localhost:<proxy>/v1, wire_api=responses, stream_idle_timeout_ms=600000}` + `--model <llama model>`. No `env_key`, so no auth header. Values unquoted on purpose (`-c` parses TOML and falls back to the literal string), so no quote characters are ever needed on argv. Codex removed the Chat Completions wire (`wire_api="chat"` is a hard error since openai/codex#10157) and ignores `OPENAI_BASE_URL`; an exported `OPENAI_API_KEY` can make it bypass the custom provider, so the child env drops `OPENAI_API_KEY`/`OPENAI_BASE_URL`/`CODEX_ACCESS_TOKEN`/`CODEX_API_KEY`. `CODEX_HOME` and `~/.codex/config.toml` are left alone, so the ChatGPT login still serves non-local runs. Provider id must not be `openai`/`ollama`/`lmstudio` (reserved). Codex also calls `GET /v1/models?client_version=…` expecting its own model-catalog shape, fails to parse ours, and falls back to generic metadata (one `error` item per run, harmless).
- **Antigravity** — `agy --input-format stream-json --output-format stream-json --dangerously-skip-permissions --add-dir <dir> [--model <m>] [--conversation <id>] -p=` (`--dangerously-skip-permissions` = skip-mode default, see **Permission modes**), prompt on **stdin** as one `{"event":"user","message":{"content":…}}` line (`-p=` with an empty value; a bare `-p` swallows the next flag). Staging bridge: `AGENT.md ↔ AGENTS.md`. Event mapping (verified Sept 2026): `init.conversation_id` → stored and replayed with `--conversation`, so resume works; `step_update{step_type:"tool", state:"ACTIVE"}` → tool; `step_update{step_type:"agent_response"}.text_delta` → `delta` (persisted as `narrative_delta`); `result{status, response, num_turns, usage}` → done. `cost_usd` is `None` (unknown), like Codex.
  **Local mode** (agy ≥ 1.1.13; verified on 1.2.10): agy's Gemini-API route — `"modelProvider": "gemini"` in `~/.gemini/antigravity-cli/settings.json` + `GEMINI_API_KEY=local` + `GOOGLE_GEMINI_BASE_URL=http://localhost:<proxy>` (no `/v1`; the genai SDK appends `/v1beta/models/…`) — against the proxy's Gemini endpoint. The user's real `~/.gemini` (OAuth login, settings) is never touched: the child gets `USERPROFILE`/`HOME` = `<settings_dir>/data/agy-local-home` (agy is Go; `os.UserHomeDir()` reads `USERPROFILE` on Windows), created by `ensure_local_home()`, which forces only `modelProvider` and keeps whatever agy writes there. `GOOGLE_API_KEY` & co. are stripped (the SDK prefers it over `GEMINI_API_KEY`). The model is agy's custom-model URL form `--model gemini-api://local/models/<llama model>`: agy's catalog rejects any other unknown name, and with this form it still posts to `GOOGLE_GEMINI_BASE_URL`, putting `local/models/<name>` in the URL path, where the proxy takes the part after the last `/models/`. Local conversations live in the isolated home, so their id is a separate key, `last_antigravity_local_conversation_id` (cloud: `last_antigravity_conversation_id`). **Gaps:** the child's shell tools also see the isolated `USERPROFILE` (`~`, git/npm user config resolve there); agy fires a concurrent title-generation call (`gemini-3.1-flash-lite-preview`, routed to the loaded model) that competes for the single slot and is cancelled when the turn ends; no cost field.

**Permission modes** (`EngineRequest.permission_mode`, from task metadata ← trigger / job `permission_mode`; `claude.permission_args`, `codex.permission_plan`, `antigravity.permission_args`). Neither `codex exec` nor headless agy can wait for a person, so nothing below blocks; `ask`/`manual` fall back with a `warning` event (like Claude's `ask` without the MCP server).

| mode | Claude | Codex (`codex exec`, 0.157) | agy (1.2.11) |
|---|---|---|---|
| none / `skip` / `bypassPermissions` | `--dangerously-skip-permissions` | `--sandbox danger-full-access` + `--dangerously-bypass-approvals-and-sandbox` | `--dangerously-skip-permissions` |
| `auto` (trigger default) | `--permission-mode auto --permission-prompts none` | `--approve-for-me` (implies workspace-write; escalations go to Codex's auto reviewer — reported as `approval_policy: on-request`, `sandbox_policy: workspace-write`) | `--mode accept-edits` |
| `acceptEdits` | `--permission-mode acceptEdits …none` | `--sandbox workspace-write -c approval_policy=never` | `--mode accept-edits` |
| `dontAsk` | `--permission-mode dontAsk …none` | `--sandbox workspace-write -c approval_policy=never` | no flag (default mode: everything that would prompt is denied) |
| `plan` | `--permission-mode plan …none` | `--sandbox read-only -c approval_policy=never` | `--mode plan` |
| `ask` / `manual` | `approve_tool` via `--permission-prompt-tool` (see Safety below) | as `acceptEdits` + warning | `--mode accept-edits` + warning |

TeleDesign turns use the same vocabulary per chat (default `acceptEdits`, see "TeleDesign").

**Effort** (`EngineRequest.effort`, from task metadata `effort` — TeleDesign chat/turn, a Task submit's
`metadata.effort`, a job step's `effort`, a trigger's / HEARTBEAT.md entry's `effort` (run override for
its job steps); values `low | medium | high | xhigh | max`, `services.engine.types.normalize_effort`):
Claude `--effort <level>` (2.1.282 accepts all five), Codex `-c model_reasoning_effort=<level>` (0.157's
enum is none/minimal/low/medium/high/xhigh/max; whether a model takes a level is the API's call), agy
`--effort` (1.2.11: low/medium/high/max — `xhigh` → `high`). Empty = the CLI's own default. The `start`
event records `effort` and `permission_mode`.

**Per-run cost** (`services/engine/cost.py`). Claude's `total_cost_usd` on a resumed/forked conversation is
cumulative across its invocations. The runner (adapter flag `cumulative_cost`) looks up the conversation's
last reported total in `engine_cost_totals` (`sessions_repo.cost_total` / `set_cost_total`, keyed
(engine, CLI session id), written after every run that reported a total — failed ones too) and subtracts
it — only when the run continued that same conversation (same CLI id) or forked it; a new id without a
fork means the CLI started over — so the `usage` / `done` events, the `invoke_agent` span (→ telemetry summaries), the handler result
(Task tasks, run steps, trigger fires) and `sessions_index.cumulative_cost_usd` are per-run;
`EngineResult.cost_total_usd` keeps the CLI's figure. A total **below** the base is taken as the run's own
(the CLI restores a session's cost only in some cases, e.g. a session last run elsewhere). `EngineRequest.cost_base_usd` is a caller fallback when the table has no
entry. Codex / agy report no cost (None), untouched. Not done: `--max-budget-usd` is passed as-is — if
Claude checks it against the restored cumulative cost, a resumed step's budget is effectively smaller.

Codex: `--sandbox` and `--approve-for-me` are exec-only (before `resume`/`fork`) and **mutually exclusive** (clap refuses both); `-a/--ask-for-approval` and `--full-auto` are not `exec` options in 0.157 — `-c approval_policy=never` is. workspace-write = writes in `-C` + `--add-dir`s, no network, refusals go back to the model; on Windows it is Codex's `[windows] sandbox = "elevated"` sandbox (set up on this machine). agy headless (verified with a real accept-edits run): the edit went through, `run_command` was auto-denied without hanging (`step_update` state `ERROR`, "user denied permission to run command"; `result.denied_actions`), both surfaced as `warning` events; settings allow-rules don't apply in `-p`. Not wired (would be the route to a real `ask`): Codex `app-server` JSON-RPC approval requests (a second adapter), agy `PreToolUse` hooks (`.agents/hooks.json`, decision `allow|deny|ask`; staging would have to write it into the workspace) or Codex hooks. agy `--sandbox` (terminal restrictions) is unused — unverified on Windows.

**Why stdin for all three.** A prompt on the command line hits the Windows limit (32 KB, ~8 KB through a `.cmd` shim) — TeleDesign prompts stack the charter, a design system and comments and run far past it. Measured: a 48 KB prompt completes and resumes on Claude Code and Antigravity; Codex's `-` verified end to end on 0.157.

**Resume ids are scoped per (workspace, agent, engine[, local])** — `session.data["resume"]["<agent_id|_>:<engine>[:local]"]` (`task_utils.resume_scope_key` / `read_resume_id` / `make_resume_store`), so pipeline step B never resumes step A's conversation. Runs without an agent fall back to (and keep writing) the legacy flat keys `last_claude_session_id` / `last_codex_session_id` / `last_antigravity[_local]_conversation_id`. TeleDesign's `design_turn_handler` builds its request with `task_request()` and writes the legacy keys through `generate.resume_slot()` (`antigravity_local` for agy local).

**Adding a fourth engine** = (0) an adapter in `services/engine/adapters/` + an entry in `adapters/__init__._ADAPTERS`, (1) a thin `services/task/handlers/<name>.py` matching the signature, (2) one entry in `AGENT_BRIDGE` (`services/task/staging.py`), (3) one entry in `ENGINE_TO_TASK_TYPE` (`services/task/engine_map.py`), (4) `register_handler(...)` in `task_registry.py`, (5) `<option>` in `telecode.html` (`TEAM_ENGINES`), `shared/triggers.js` (`TRIGGER_ENGINES`) and `shared/manager.js` (`AGENT_ENGINES`). No changes to the executor or the trigger scheduler.

**Subprocess lifecycle — closed.** Every engine CLI is Job-bound before its first instruction (see the Engine Runner above), so a hard kill of telecode takes the whole CLI tree down, and cancel/timeout stop it gracefully, then kill it. `task_utils.track_process` remains only for a handler that spawns its own process outside the runner.

**stderr is drained on a thread (`engine/drain.StreamDrain`).** The runner iterates stdout only; an unread `stderr=PIPE` fills the ~4 KB Windows pipe buffer and the child blocks on its next stderr write while we block on its stdout — a silent hang. Codex hits it reliably on an expired ChatGPT login (one token-refresh error per request, even in local mode).

**Persistent store — `data/telecode.db`** (`services/db/`: stdlib sqlite3, WAL, one connection per thread, `schema_migrations`; `core.MIGRATIONS` is append-only). It holds task/run/step state, parsed task events, the session lineage index, (P5, migration 4) telemetry — `spans` / `metric_points` / `log_events` — and (migration 5) `engine_cost_totals` (per-run cost base, see **Per-run cost**) — **nothing else**: `data/logs/*` and the raw CLI logs in `data/task_logs/` are unchanged.
- `tasks` + `task_events` (`task_repo`): `TaskQueue` writes through on every state change (`persist`; progress throttled to 2/s) and `append_event` stores each event with a 1-based `seq`, both via one ordered background writer thread (`db/writer.py`) so a worker never waits on disk. Events are capped per task: first 50 + last 2000. The in-memory queue stays authoritative for live tasks; `get_task_record` / `list_task_records` fall back to the DB, so `GET /api/tasks[/{id}]` survive a restart (DB-only tasks in the list carry just their `start` event). At startup (`api_runs._reconcile_on_startup`) DB tasks left pending/running and not live in this process become `failed` / `interrupted: telecode restarted …`.
- `runs` + `run_steps`: `services/run/run_store.py` is SQLite-only (same record shape and API). Pre-SQLite `data/runs/*.json` are imported once per database (`meta.runs_json_imported`) and left on disk as a manual backup; nothing reads them afterwards.
- `sessions_index` (`sessions_repo`): one row per CLI conversation, scoped like resume ids (namespace, workspace, agent, engine, local), with cumulative tokens/cost, `cumulative_tokens` (budget tokens, drives rotation), `runs_count`, `kind` (task/run/trigger/design; older rows routine/heartbeat), `parent_id` → the conversation it superseded, and `lineage` (fresh/resume/fork/rotation/ephemeral) + `forked_from` / `rotated_from`. `run_steps.handoff` holds each step's handoff (migration 2).

**Live events / SSE** (`services/bus.py`, `proxy/api_events.py`; the same thread-safe bounded-queue pattern as TeleDesign's `events.py`): `GET /api/tasks/{id}/events` replays (`?after=` / `Last-Event-ID`) then streams `event` (id = seq), `delta`, `status`, then `end`; `GET /api/runs/{id}/events` streams `run` records plus the step tasks' frames; `GET /api/events?kinds=task,run` is the global feed of `task.status` / `run.update` summaries. Polling routes are unchanged; `index.html` / `telecode.html` subscribe through `manager.js liveEvents()` and fall back to their old poll intervals when a stream is unavailable (404 / error before open).

**Observability (P5)** (`services/telemetry/`, `proxy/api_telemetry.py`, `services/engine/otel.py`, `proxy/static/shared/observe.{js,css}`). Every engine run whose request carries `correlation` (task_bridge sets it from the task: task / run / step / agent / job / trigger ids) is an `invoke_agent` span with `execute_tool` children, under the run's `invoke_workflow` span (GenAI semconv; deterministic trace id per run). Claude is spawned with `CLAUDE_CODE_ENABLE_TELEMETRY=1` + OTLP/JSON to `http://127.0.0.1:<proxy>/otlp` + `OTEL_RESOURCE_ATTRIBUTES=telecode.*=…`; Codex with `-c otel.{exporter,metrics_exporter}.otlp-http.{endpoint,protocol=json}` (dotted, unquoted) + the same `OTEL_RESOURCE_ATTRIBUTES` — **verified honoured** by a real 0.157 `codex exec` through the CODEX handler (every `resourceLogs`/`resourceSpans` block carried `service.name=codex_exec` and all `telecode.*` ids; events `codex.conversation_starts` (approval/sandbox policy, model), `codex.user_prompt` (`[REDACTED]`), `codex.api_request`, `codex.startup_phase`); Codex traces only with `telemetry.cli_traces` (else `otel.trace_exporter=none`: one tiny run exported ~2,000 internal spans, ~4.7 MB); inherited `OTEL_*` exporter vars are scrubbed — **nothing is exported off-box**. The receiver (`POST /otlp/v1/{metrics,logs,traces}`, JSON or protobuf via the dependency-free decoder in `telemetry/protobuf.py`) answers **loopback peers only** and drops account identity attributes. `telemetry.enabled` (true), `telemetry.retention_days` (14). Dashboards: `GET /api/telemetry/summary?group=agent|job|trigger|engine|model&since=7d`, `…/runs/{id}/timeline`, `…/triggers/{id}/passk`, `…/tasks/{id}`. Runs get `process_ok` + `verdict` (pass|fail|unknown) + `verdict_source` from `telemetry/verdict.py` when a driver exits: a job's `outcome_check {command}` (run in the workspace, exit 0 = pass) outranks the final handoff verdicts; cancelled = unknown. Input tokens include cache reads/writes, so cache-read ratio = cache_read / input.

**Safety (P5)**: permission mode `ask` (trigger / job `permission_mode`, task metadata) → Claude `--permission-mode manual --permission-prompts host --permission-prompt-tool mcp__telecode_safety__approve_tool --mcp-config <per-run file>` (X-Telecode-* headers carry the ids). The mode must be pinned — unpinned, the user's settings `defaultMode` wins (here `bypassPermissions`). `mcp_server/tools/approvals.py::approve_tool` opens a `tool` approval (inbox + Telegram), waits `safety.approval_timeout_sec` (600) → deny, and must return a **single text block** (`structured_output=False`; Claude rejects FastMCP's `structuredContent`). Without `mcp_server.enabled`, `ask` falls back to `auto` with a warning event.

**Cross-engine continue (P5)**: `POST /api/sessions/{sid}/continue {engine, model?, prompt?, run_id?, step_id?}` (`services/engine/handover.py`) — latest handoff + workspace diff since the first snapshot + PROGRESS.md, written to `<ws>/.telecode/continue/`, prepended to a fresh conversation on the other engine in the same workspace; lineage `engine_switch`, `switched_from` = source row.

**P4 hook**: for agent runs the runner calls `services.memory.engine_extras(agent_id, engine, workspace)` (lazy import, failures = no extras): `add_dirs` join `req.add_dirs` (Codex `--add-dir` is exec-only, before `resume`), `args` go before the prompt marker (`Launch.extras_at`), `env` is merged.

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

Claude Design + pen.dev on **one canvas**, driven by the existing CLIs. Tray → **Open Web UI** (lands here) →
`http://127.0.0.1:<proxy.port>/design`. Architecture: [docs/teledesign.md](docs/teledesign.md); the
interface every module codes against: [docs/teledesign-contract.md](docs/teledesign-contract.md); scope:
[docs/teledesign-parity.md](docs/teledesign-parity.md).

- **Boards.** Layer boards = native open-pencil frames in a canvas document (`docs/<id>.fig`). HTML boards = frames registered in
  `boards.json`, keyed by a **board key** stored inside the frame (open-pencil renumbers node ids on every
  reopen), with the generated page overlaid live in a sandboxed iframe.
- **Turns are direct task sessions, not Team Mode.** One session per project chat (namespace `design`),
  `DESIGN_TURN` task type, cwd = project folder, resumed per engine. `prompt_builder.py` stacks
  `prompts/` in README order; the brief (~55 KB) is split into sections and each is sent only when the
  CLI conversation hasn't seen that exact text (`plan_delivery`: first turn of a chat, or the section
  changed); otherwise the turn points at `.td/brief.md`. Post-turn: done gate (console errors → one fix
  turn) → verifier (`render.verify`) → thumbnail → auto-title. Events: in-process pub/sub → SSE `…/events`.
- **Turn cost (Claude Code, `services/design/engine_opts.py`).** Every API call of a turn re-sends the
  CLI's own context, and a design project sits in `data/design/projects/<id>` *inside this checkout*, so by
  default the CLI walks up and loads this 93 KB CLAUDE.md. Measured on 2.1.282 (trivial first turn, first
  API call): **~99k tokens before, ~37k after** — the rest was this CLAUDE.md + global CLAUDE.md + memory
  (~95k chars), 33 built-in tool schemas (~85k), a 155-skill listing (~30k), user MCP servers and 30–60
  claude.ai connector tools. Defaults: `--strict-mcp-config --mcp-config <only telecode's server>` (also
  drops claude.ai connectors — verified in the init event), `--tools Read,Write,Edit,Glob,Grep,Bash`,
  `--disallowedTools` for the telecode MCP tools meant for outside clients (`design_send_message`, file
  CRUD, speak…; `--disallowedTools` does remove them from the request), `--setting-sources user`,
  `--disable-slash-commands`, `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`.
  All under `design.claude.*` (tray → TeleDesign → Turn cost); flags reach the CLI through
  `EngineRequest.extra_args` (Claude adapter only). `TodoWrite` does not exist in 2.1.282 `-p` at all.
- **Brief delivery.** `design.brief_mode: system` (Claude only): a fresh conversation gets its sections in
  `.td/system-<chat>.md` → `--append-system-prompt-file`, and **the same file is passed on every later
  launch** (the CLI records the system prompt on the first request; identical bytes also keep the cache);
  sections added or changed later go in the message as `## Brief update`. `design.brief_select`
  (`classify_turn`): comment / mention / auto-fix turns and short change requests on a project with
  content are `edit` turns and skip `kind`, `tweaks`, `code_export` and `craft_*` (the deck contract
  stays for slides); unclear → `build`. Licence `<!-- … -->` headers are stripped at load.
- **Per-turn cost.** Claude's `total_cost_usd` (and `modelUsage`) on a resumed session is **cumulative**
  over every invocation of that session, while `usage` is per invocation (verified: turn 2 reported
  $1.04 = $0.92 turn 1 + $0.12). The correction is the Engine Runner's, shared with every caller (see
  "Task engines" → **Per-run cost**), so `turn.usage.cost_usd` is the turn's own. The old per-chat
  `session.data.design_cost[slot]` is only read as the runner's fallback base for chats that predate it.
- **Permission mode & effort.** Per chat (`PATCH …/chats/{cid}` `{permission_mode, effort}`) or per turn
  (turn body, wins), default `design.permission_mode` = **`acceptEdits`** (a turn only edits files in its
  project folder; verified with a real `claude --model haiku --effort low` design launch: the page was
  written, init reported `permissionMode: acceptEdits`). Vocabulary `auto | acceptEdits | dontAsk | plan |
  ask | skip` (null = the setting), mapped per engine by the adapters (table under "Task engines"); it rides
  in the task metadata (`generate._submit`) → `task_bridge` → `EngineRequest.permission_mode` / `effort`.
  In `acceptEdits` Claude's Bash calls that are not simple file operations are denied (nobody is asked).
  `ask` = `approve_tool` via the approvals inbox + Telegram (needs `mcp_server.enabled`, else `auto` + a
  warning); the approve server's config joins the design `--strict-mcp-config` file in **one** variadic
  `--mcp-config a b`. UI: `chat.js` effort menu + phone sheet carry a "Permissions" group.
- **Two origins.** The API/UI on `proxy.port`; generated pages on `design.preview_port` (1237) —
  `preview.py`, CSP `connect-src 'self'`, bridge injected. Every mutating `/api/design/*` route requires a
  JSON/octet-stream/multipart content type and rejects a foreign `Origin` (middleware in `api_design.py`),
  so a preview page can never write. Preflights only pass for `proxy.cors_origins` — never add `*`.
- **Canvas editor** = patched open-pencil static build (`python tools/build_open_pencil.py`, patches in
  `patches/open-pencil/`, vendored at `proxy/static/design/editor/`). Agent tool calls reach the live page
  over `editor_bridge.py`'s WebSocket — no Node at runtime. Rebuild after bumping the tag; `--check` tells
  you whether the series still applies. Never hand-edit the vendored output or the disposable source dir.
- **Canvas documents** (patch 0013): `docs/<id>.fig` + `docs/canvases.json` (`store.list_docs /
  create_doc / update_doc / delete_doc`, `get_canvas / save_canvas(pid, data, doc_id)`); a legacy
  `doc.fig` moves to `docs/main.fig` on first access (`_migrate_docs`; a restored one migrates again).
  The editor opens `?doc=` (absent = default), its bridge socket carries `doc` (`editor_bridge.open_doc`);
  `telecode_doc_list` / `_create` are answered by the proxy, `telecode_doc_open` reloads the page and
  waits for it to register again. REST `…/docs[/{doc}[/canvas|/canvas.json]]`; never delete the open
  or last one (409 / 400). `GET …/editor?doc=` → `doc_id` = the one asked about, `open_doc` = the one
  open. `.fig`, mirrors and `canvases.json` are refused by the Files API (`files._canvas_owned`) and
  left out of share snapshots — they change only through their routes.
- **JSON mirror** (0014): the editor PUTs `docs/<id>.fig.json` after every save; `save_canvas_mirror`
  re-serializes it canonically (sorted keys, LF) and skips identical writes. Read-only, never loaded.
- **Script nodes** (0015): frame + `telecode/script` plugin data → a project `.js` (`@input` header)
  run in a sandboxed iframe + Worker, returns Design JSX; `GET …/editor/scripts?path=` is what the page
  polls. Controls / badges in `app/canvas_nodes.js`.
- **Theme axes / slots / procedural fills** (0016–0018), canvas tools only (`telecode_theme_*`,
  `telecode_slot_*`, `telecode_fill_*`), driven from the canvas bar's **Theme** menu and **Layer** panel
  (`app/canvas_extras.js`, over `/editor/call`). Every collection is an axis; a cross-collection alias
  resolves in the node's mode for *that* collection. A slot = a frame in a component replaced by an
  instance exposed as an instance-swap property (content components in a "Slot content" section) —
  `.fig` derives instance children from the main component, so content put straight into an instance is
  lost on reload; the swap value is not. A shader / mesh fill = a CUSTOM paint whose `customEffectId`
  links to `telecode/fill:<id>` plugin data (SkSL runtime effect with `u_size` + named uniforms; mesh =
  Coons patch per cell); shaders are compiled before saving (bad SkSL refused with the message); other
  renderers and the HTML export see the fallback colour. Static only — no `@time` animation.
- **Build notes.** `--check` applies each patch after checking it (0008+ extend files 0007 adds). Clone a
  build dir with `core.autocrlf=false` or `git apply` fails on CRLF; keep the series LF. The upstream
  headless-CanvasKit loader breaks on Windows paths (`URL.pathname` → `/C:/…`), so render tests load
  CanvasKit themselves (`tests/engine/render/canvas/procedural-fills.test.ts`).
- **Canvas parity commands** (patches 0008–0011, `telecode_*` bridge commands; REST in
  `api_design_editor.py` under `…/editor/`). **HTML → layers** is *not* open-pencil's dom-css importer on
  the source (TeleDesign pages are React/Babel, and that importer lays out in a 1000px sandbox, only
  positions flex children, reads only data-URL images): `canvas_convert.layout_snapshot` renders the page
  in headless Edge and sends measured boxes/text/images/SVG to `telecode_import_html`, which builds them
  flat (absolute) in a new frame beside the board, one undo step. `{html, css}` still runs the dom-css
  importer. An agent's `telecode_import_html {src}` takes the snapshot server-side too
  (`editor_bridge.call`). **Layers → HTML** = dom-css `exportHTMLBundle` (standalone) via
  `telecode_export_html`; `…/convert {direction:"to-html"}` writes the file + registers a new board.
  **Layer-board Preview** writes that export to `.layers/<slug>-<id>.html` (dot dir: hidden from Files,
  served by the preview origin), re-exported on `td-editor:saved`. **Tokens** (`canvas_tokens.py`):
  tokens.json → collections `Color` (mode per theme) / `Spacing` / `Radius` / `Typography`, upserted by
  name; Pull writes changed values back into the tokens.json it came from (project root, else the staged
  `_ds/<slug>/` copy — replaced on restage) and the matching `--var` in tokens.css. **Slides** = a page's
  top-level frames in layer order; Present renders each via `export_image`, PDF via `export_pdf`.
  **"working…"** marks are plugin data `telecode/placeholder`; the host draws the hatch and clears all
  marks when a turn ends (and on canvas open with no turn running); marks older than 2 h show as stalled.
- **Patch 0012 fixes autosave** of a reopened canvas: the first autosave awaited the .fig population
  worker's original archive, which the worker never answers on failure, and autosave serialises saves —
  so nothing after a reopen (agent or user) was saved until an explicit Save. The wait is now bounded.
  Bridge `select_nodes` / `switch_page` only move the plugin-API selection/page, not the editor's; the
  host's `td-editor:focus` does both. Building from a deep path (e.g. a scratch copy) fails on MAX_PATH
  (sharp's DLL): use a short `--src`; `build_open_pencil.py` keeps the path as given (`absolute()`).
- **Render/export** share one headless Edge (`render.py`, CDP, idle-closed, Job-bound); `render.stop()` on
  proxy cleanup. Exports are background jobs under `data/design/exports/` (24 h).
- **Verifier checks.** `render.verify` = console, blank, overflow, small text, hit targets, **WCAG contrast**
  (text vs the background composited from its ancestors' `background-color`; anything with a
  background-image / gradient / media behind the text is *unknown and skipped*, never guessed; `<3:1` major,
  else minor), broken resources, deck checks — plus **layer boards** (`design.verifier.layer_boards`, default
  on): `editor_bridge.inspect_layers` runs open-pencil's own `analyze_overlaps` / `analyze_typography` per page
  and `export_image` of top-level frames, and returns `pen_problems`. It needs the editor page (a .fig is
  only parsed in the browser — no Node at runtime), so with no page attached it **skips with a logged
  reason**. Severity is deliberately conservative: only a node >25% outside its parent is `major` (can wake a
  fix turn); sibling overlaps are minor (a label on a rectangle is a "sibling overlap"). **Directed checks**:
  `verifier.run_check(task=…)` (`POST …/verify`, MCP `design_verify`, CLI `verify --task`) always reports;
  its model pass runs only under `design.local_helpers`, otherwise the report hands the evidence to the
  calling agent (`model.ran: false` + `note`).
- **Live preview console.** `preview.js` relays every `td:console` line to `POST …/console/live` (in memory,
  500/file, `seq` cursor); `design_get_console(source="live"|"headless"|"auto")` reads the user's own view.
- **`design_browser`** (`POST /api/design/browser` → `render.browse`) is the one renderer path that opens a
  caller-supplied URL: a throwaway browser context, `Network.setBlockedURLs` for `ws(s)://*`, and CDP `Fetch`
  pausing **every** request so the host fetches it under `proxy/media_fetch.py`'s rules (http(s), every
  resolved address public) and fulfils it; a 3xx goes back to Edge so each redirect hop is re-checked.
  Blocked requests are listed. Loopback/LAN URLs are refused by design — test with a patched
  `_GuardedFetcher.check`.
- **File ops.** `POST …/file-ops {op: copy|move}` (MCP `design_copy_file` / `design_move_file`): a copy
  inherits the source's assets.json entry as a fresh needs-review registration; a move keeps the entry's id
  and status and repoints boards.json.
- **MCP registration** (`mcp_registration.py`): CLI `mcp add` for Claude Code / Codex / Antigravity / Gemini;
  JSON edit (parse-or-refuse, only our key, one `.telecode-bak`, atomic) for OpenCode / Kiro / Claude Desktop
  (stdio `npx -y mcp-remote <url>` — its config file takes stdio servers only). **Never writes for a client
  that is not installed** (binary / install path, not a leftover config folder — `~/.gemini` exists wherever
  agy does). Tray card: MCP server reachability + "Re-check connection".
- **Headless CLI**: `python -m services.design.cli [--base URL] [--json] projects|create|get|files|send|run|
  export|screenshot|verify|console` over REST only (stdlib urllib; `send` streams the SSE events; `run` is
  the batch form: `--prompt/--tasks/--in x.pen/--engine/--model/--effort/--export/--out`).
- **Share links are snapshots** (`share.py`). Create = the tree (sources, uploads, staged `_ds/`) frozen as
  blobs in the project's `.versions/objects` (shared with history); recipients load it from the **preview
  origin** at `/s/{token}/…` (bridge injected, blobs only — a revoked/expired token 404s there too) and can
  download it as a ZIP (`/api/design/s/{token}/download`). The owner's list shows "N changes since shared";
  `PATCH …/share/{token}` `{resnapshot|role|expires_in}` updates in place (same URL). Create/update return a
  missing-dependency list (relative refs the snapshot lacks). Records from before snapshots (no `snapshot`)
  stay live. Expiry 60 s – 365 d or never; still gated by `design.share.enabled`.
- **Undo on HTML boards = version steps** (`versions.step`, `POST …/versions/undo {path, direction}`): the
  file's distinct states in history order; each step writes the older/newer content and records a `restore`
  version marked `undo` (never destructive). Cursor in `.versions/undo.json`; when the file on disk no
  longer matches the cursor's head (a user/agent edit), redo is dropped and the line rebuilt. Ctrl+Z inside
  the page reaches the host because `td-bridge.js` forwards it as `td:key` (page inputs keep native undo).
- **Chat context + download cards** (`chat.js`). The current preview selection and the editor's selected
  layers ride along with the next turn as removable chips (`selection:{file, board_id, elements:[…]}`,
  Preferences switch `autoContext`). `<download-card path="…" label="…" kind="file|folder|project"/>` in a
  reply becomes a card → `GET …/download?path=&kind=` (folder/project zipped in memory, 512 MB cap); export
  jobs render as cards under the turn they ran in (UI-started ones under "Your exports").
- **Export extras.** PPTX: `googleFontImports` (fonts.googleapis.com only), `resetTransformSelector`,
  `slides:[{index, selector, showJs, delay}]`, `save_to_project_path` (`pptx_export.py`). Standalone HTML
  carries a `<noscript>` full-page picture (`export._noscript_fallback`; `options.noscript:false` to skip).
  **Send to Google Slides** (`gslides.py`): `shutil.which("gws")`, npm shim unwrapped (no shell), `gws auth
  status` checked first and **never** a login — signed out → 412 + "run `gws auth login`"; else PPTX →
  `gws drive files create --upload` with the Slides MIME type. **Figma import** (`figma_import.py`): user
  token at `design.figma.token` (`config.set_nested`), frames → `imports/figma/*.png` + `figma-*.html`
  boards, image URLs through `media_fetch`.
- **Handoff to a coding session** (`handoff.start_session`, `POST …/handoff/session`): new Task-Mode
  workspace session `handoff-<slug>-<hex>` (root namespace, no expiry) with the bundle in `handoff/`, one
  CLAUDE_CODE/CODEX/ANTIGRAVITY task whose prompt names the repo by absolute path (Task Mode owns the CLI's
  cwd, so the agent is told to `cd` there). Repo picker: `GET /api/design/fs/dirs?path=` (dir names only).
- **Sketches** are `scraps/<name>.napkin` (`{type:"td-napkin", image: PNG data URL, board, bbox}`) +
  `scraps/.<name>.thumbnail.png`; draw mode saves one, opening a .napkin reopens it (`app/napkin.js`).
  **Welcome sample**: an empty gallery calls `POST /api/design/welcome` → `templates.ensure_welcome` (once
  per install via `data/design/.welcome.json`; `design.welcome_project: false` disables).
- **Design systems**: seeds in `services/design/seeds/` auto-install on first listing (stable uuid5 ids;
  deleted seeds stay deleted). `systems.py` stages a selective copy into `_ds/`, `lint.py` enforces
  `adherence.json`, `ds_bundle.py` precompiles components (node → Edge → in-browser fallback). Manifest
  `brandFonts` (provided / substituted / missing, list or Claude-Design object form) → `systems.brand_fonts`.
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
- **Trigger not firing** — `proxy.enabled` (the scheduler lives in the proxy; log line `trigger scheduler started`); HEARTBEAT.md triggers also need `heartbeat.enabled`. Look at the trigger's history: `skipped` rows say why (previous fire running, outside active hours, nothing to do, missed while down, limits). A paused trigger shows its `paused_reason` (auto-pause / goal met). **Webhook 401** — wrong / rotated token, or the webhook event is off. **Run stuck "awaiting input"** — a gate: approve it in the inbox (top right) or on Telegram.
- **"No session for thread"** — `/new` again. **CLI exits at once** — API key / `startup_cmd` / binary on PATH. **Stuck on prompt** — `/key enter` or `/key y`. **Garbled stream** — TUI limitation; tune diff. **Settings ignored** — `/settings reload` or restart.
- **Screen capture blank** — window on another virtual desktop. **Video encoding** — ffmpeg on PATH. **Computer control wrong spot** — DPI: `_get_window_rect` must be logical coords. **Computer control LLM error** — `base_url` should be proxy (`:1235/v1`).
- **llama-server won't start** — `data/logs/llama.log`; verify `binary` and `models.<default>.path`. **Model swap hangs** — bump `llamacpp.ready_timeout_sec`. **`<think>` leaks** — per-model `inference_defaults.reasoning.start/end` must match.
- **Tests that drive the live telecode** — `tests/team/test_e2e_http.py` posts `is_local: true` runs to `:1235`, which makes the running telecode load llama; it only runs with `TELECODE_E2E_HTTP=1`.
- **ToolSearch not triggered** — with `proxy.debug`, inspect `data/logs/requests/req_*.json` (`intercepts: []` means the proxy never intercepted; the round was decided as passthrough). The dumps directory is **cleared on every startup**, so capture the repro before restarting. `proxy_full_*.json` no longer exists. **Tools missing after search** — try `re:` prefix; check `MAX_SEARCH_RESULTS`. **MCP speak/transcribe** — VoxType on `:6600` or repoint URLs.
- **TeleDesign turn fails instantly** — `PromptError: unknown placeholder` means a prompt gained a placeholder the builder doesn't fill. **Preview blank / console 404s** — preview site on `design.preview_port` not up (look for `design preview` in telecode.log). **Canvas blank** — `proxy/static/design/editor/` missing → rebuild with `python tools/build_open_pencil.py`. **Design turn can't see a tool / MCP server / CLAUDE.md** — the `design.claude.*` cost defaults hide them on purpose; relax the one you need.
- **DocGraph host won't start** — `data/logs/docgraph_host.log`; verify `docgraph.binary`. **Kuzu lock error** — `IndexRunner` should route through `/api/admin/index` (check `docgraph_index.log` for "host route failed"). **Bridge tools missing** — host alive AND `/mcp` responding (uvicorn lifespan `on`).

---

## Running in background (Win)

`pythonw main.py` — no console. For auto-start: Windows Scheduled Task with `pythonw.exe`.

## Dependencies

**python-telegram-bot**, **aiohttp**, **aiofiles**, **pyte**, **pywinpty** (Win PTY), **mss**, **Pillow**, **pywin32** (Win Session 0), **pyautogui**, **mcp**. ffmpeg on PATH for video.
