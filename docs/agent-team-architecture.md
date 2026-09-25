# Task Mode + Team Mode — architecture review and target design

Status: **P0–P3 implemented** (2026-09-25; P0 = commit 231d611, P1 = 303dc51, P2 = 0f447fa). P4–P5 remain proposals. Original proposal date 2026-09-25. Inputs: a read-only audit of `services/{session,task,agent,job,run,heartbeat,routine,skills}` +
`proxy/api_*`, and web research on 2025–2026 agent platforms (Anthropic context engineering / Agent SDK / headless docs,
OpenAI Agents SDK + Codex app-server, Google ADK, LangGraph, Microsoft Agent Framework, Letta, Mem0, CrewAI, Goose, Cline,
Copilot agent HQ, OpenClaw). Flags quoted below were checked against the installed CLIs (claude 2.1.282, codex-cli 0.157,
agy 1.2.10).

---

## 1. What is wrong today (audit, evidence in the audit notes)

### 1a. Bugs — fix before anything else
| # | Bug | Where | Effect |
|---|---|---|---|
| B1 | Team UI creates workspaces with `absolute_ttl=86400`; after a day `session_store.get()` **deletes the workspace folder** at the start of the next run, then the run proceeds in an empty folder and resume ids stop saving | `telecode.html:1078-1099`, `session_store.py:96-171`, handlers `session_store.get` | silent data loss |
| B2 | `<previous_output>` handoff is `_result_preview()` — **400 chars** | `executor.py:51-63,286,370` | pipelines barely pass information |
| B3 | Run "Task Engine" select never sent; executor hardcodes `claude_code`; heartbeat parser allows only `claude_code` and forces `is_local=False` | `executor.py:71-74,260,334`, `telecode.html:1483-1517`, `parser.py:32,124`, `scheduler.py:195` | engine choice is fake |
| B4 | Heartbeat entries with no `last_run` **never fire** (`last_dt = now` each tick) | `scheduler.py:129-151` | heartbeat dead for new entries |
| B5 | Routine `outputs_only` passes an unknown kwarg to every handler → TypeError | `routine_manager.py:248`, `task_manager.py:113` | those routines always fail |
| B6 | Task queue is in-memory; runs/steps/heartbeat `running` states never reconcile after restart; queue never evicts (full 56 KB prompts kept in events) | `task_manager.py:43`, `run_store.py:178`, `scheduler.py:39` | stuck UI, memory growth |
| B7 | Resume id is stored per **workspace**, so step B resumes step A's conversation; different jobs on one workspace share a CLI conversation | `executor.py:270-281`, handlers | context bleed between agents |
| B8 | Staging overwrites and then **deletes** a workspace's own `CLAUDE.md`/`AGENTS.md`/`MEMORY.md`; MEMORY write-back is last-writer-wins | `staging.py:68-127` | user files destroyed |
| B9 | Agent file routes join caller paths unchecked (**path traversal**); `agent_id` unvalidated | `agent_manager.py:134-152`, `api_agents.py:72-94` | security |
| B10 | Cancel sets status only; the CLI keeps running (`shell=True` → only `cmd.exe` gets terminate); cancel overwrites finished tasks | `api_tasks.py:93-97`, `executor.py:153-168` | runaway CLIs |
| B11 | Codex usage fields don't exist → cost/turns always 0; no per-run/job/agent cost roll-up | `codex.py:382-397`, `run_store.py:101` | no cost visibility |
| B12 | 5 pool workers shared by everything; a task blocked on the staging lock occupies a worker while showing RUNNING | `task_manager.py:45`, `staging.py:146` | starvation |
| B13 | `task_timeout_seconds` accepted everywhere, enforced nowhere; routine skip-if-running not under the lock | several | runaway / double fire |

### 1b. Design smells
- **Four orchestrators** (Run executor, Heartbeat scheduler, Routine manager, TeleDesign `generate`/`parallel`), each with its own
  polling, busy logic, in-memory driver registry and restart gaps. **Four cancel paths**; only TeleDesign's kills the process tree.
- Three handlers duplicate the whole prologue (prompt → log → session → resume key → staging → Popen/stdin/drain/cancel → result).
  TeleDesign re-implements it again with its own resume-key table (which had already drifted).
- Two recurring-work concepts (Heartbeat cron in HEARTBEAT.md vs Routine interval) with different guarantees.
- Agent prompt XML built twice (JS + Python) with different fields; `agent.instructions` unused.
- No events persisted, no SSE for Task/Team (TeleDesign has SSE), polling returns every task with all events.

---

## 2. Target architecture

```
                 ┌──────────── API (Task / Team / Design UIs, Telegram, MCP) ────────────┐
                 │                                                                      │
   Triggers ─────►  Orchestrator (one)  ── Run ── Step ── Attempt ──►  Engine Runner (one)  ──► CLI
   (manual, cron,   durable run graph:     │        │                   claude/codex/agy,
    interval,       phases, map, loop,     │        └ Session policy     stdin, tree-kill,
    webhook, file,  gate, budget, retry    │          resume|fork|fresh  Job-bound, OTEL env,
    GitHub, at)                            │          |ephemeral         schema output
                                           ▼
                      Store (SQLite, data/telecode.db): sessions, lineage, runs, steps,
                      attempts, events, artifacts, budgets, approvals — survives restart
                                           ▲
       Workspace snapshots (shadow git per workspace)   Agent home (git-versioned internal/)
```

### 2.1 One Engine Runner (`services/engine/`)
Single `run_engine(EngineRequest) -> EngineResult` used by Task, Team, Routines, Heartbeat **and** TeleDesign:
- `EngineRequest{engine, model, is_local, effort, cwd, prompt, session: SessionRef, policy, schema?, budget?, env_extra, add_dirs[], system_append_file?, agents_json?}`.
- Spawns through `process.py` (Job Object, `CREATE_NEW_PROCESS_GROUP`), captures the real CLI pid, **tree-kills** on cancel
  (graceful CTRL_BREAK first), enforces wall-clock timeout itself, drains stderr (already), normalises events
  (`start|delta|tool|todo|usage|done|error`) and usage across engines.
- Engine adapters stay thin: argv + event parser + resume/fork flags + structured-output flag
  (`claude --json-schema`, `codex exec --output-schema`, agy → validated `.telecode/out.json`).
- **Explicit context, not implicit files** (research finding 1): pass agent instructions with
  `--append-system-prompt-file` (Claude) / `AGENTS.md` (Codex, agy) instead of relying on a staged `CLAUDE.md` being picked up —
  robust if headless defaults change (`--bare` exists today) and stops clobbering the workspace's own `CLAUDE.md` (B8).

### 2.2 Sessions as first-class records + lineage
Table `sessions{id, engine, engine_session_id, workspace_id, agent_id, parent_id, fork_of_step, kind(task|workspace|routine|design|ephemeral),
policy, created_by, cumulative_tokens, cumulative_cost, status, ttl, rotated_from}`.
- **Resume scope = (workspace, agent, engine)**, not workspace (fixes B7). Step B never resumes A's conversation.
- **Per-step session policy**: `resume | fork | fresh | fresh+handoff | ephemeral` (Claude `--resume --fork-session`,
  `codex exec fork`, `--ephemeral`).
- **Rotation**: when a session passes a token/cost threshold or N fires, ask for a structured handoff and continue in a fresh
  session seeded with it + pinned constraints (research: compaction keeps ~17 % of user constraints).
- **Cross-engine continue**: neutral context package (handoff JSON + workspace diff + PROGRESS.md) → child session on another engine.
- TTL by kind; **absolute TTL never applies to workspaces** (fixes B1); expiry archives, never deletes, while a run references it.

### 2.3 Durable runs (Run → Step → Attempt)
- Persisted in SQLite; on startup, reconcile: attempts whose process is gone → `interrupted`, offer resume/retry (fixes B6).
- Step kinds: `agent` (today), **`map`** (fan-out width from previous structured output, `max_parallel`), **`loop`**
  (evaluator-optimizer: generate → check (command | grader agent in a fresh session | schema) → feedback, `max_iterations`),
  **`gate`** (human approval via UI inbox + Telegram buttons; run status `awaiting_input`), `reduce`.
- **Typed handoffs** replace `<previous_output>`: each step emits `{status, summary, decisions[], artifacts[{path,kind}],
  open_questions[], next_steps[], verdict}`; the next step gets summary + artifact **paths** (just-in-time retrieval), never a
  400-char preview (fixes B2). Artifacts live in `runs/<run>/artifacts/<step>/` and are copied back from ephemeral sessions.
- **Budgets** per Run/Job/Agent ($, tokens, wall-clock) split across steps → `--max-budget-usd` for Claude, runner-enforced for
  Codex/agy; one cancel token propagates to every parallel attempt.
- **Retries**: "retry step" (resume same session) vs "retry clean" (restore snapshot, fresh session); idempotency key per step.
- Engine/model/local selectable per **agent default → job step override → run override** (fixes B3).

### 2.4 Workspace snapshots
Shadow git per workspace (`GIT_DIR=data/snapshots/<ws>.git`, work tree = session folder) committed before/after every attempt:
per-run diff viewer, revert (covers Bash edits, unlike Claude's file checkpointing), "fork from here" = snapshot + session fork.
Same mechanism TeleDesign's `versions.py` already uses conceptually — unify.

### 2.5 Memory and agents
- `internal/` becomes a git repo: every write-back is a commit `run:<id>` → memory diff per run, rollback (Letta MemFS pattern).
- MEMORY.md → **index + typed topic files** (`user|feedback|project|reference`), only the index staged; topics on demand via `--add-dir`.
- Point Claude's `autoMemoryDirectory` at the agent's memory (or disable auto memory) so there is one memory, not one per folder.
- **Pinned constraints** block in AGENT.md, re-injected at the tail of every fire (cache-safe).
- **Portable skills** per agent (`internal/skills/` staged to `.agents/skills` + `.claude/skills`) — procedural memory for all engines.
- **Reflection job** (sleep-time consolidation, Mem0 ADD/UPDATE/DELETE/NOOP + ACE helpful/harmful counters) proposes a memory
  diff after K runs → approval gate. Runs on the local model only when `design.local_helpers`-style opt-in is on.
- Merge-on-write-back (three-way on the git repo) instead of last-writer-wins (B8).

### 2.6 One scheduler
Routines and Heartbeat merge into **Triggers** on a Job or Agent: `cron | interval | at | webhook (/fire + per-trigger token,
payload wrapped as untrusted) | github (filters) | file-watch`. Shared guarantees: skip-if-running under a lock, catch-up policy,
activeHours + timezone, empty-skip, `OK`-reply suppression, session `shared|fresh`, cheap-model override, goal/stop condition
(`check`, `max_fires`, `max_cost`), auto-pause after K consecutive failures. HEARTBEAT.md stays as an authoring format that
compiles to triggers. Fixes B4, B5, B13.

### 2.7 Observability
- Events persisted (SQLite, capped per attempt) and streamed over **SSE** for Task/Team like TeleDesign; UIs stop polling everything.
- OTLP receiver in the proxy: spawn CLIs with `CLAUDE_CODE_ENABLE_TELEMETRY=1` / Codex `-c otel.*` and
  `OTEL_RESOURCE_ATTRIBUTES=telecode.run_id=…,telecode.step_id=…,telecode.agent_id=…` → per-run cost/tokens without parsing
  each stream (fixes B11). Own spans follow GenAI semconv (`invoke_workflow`, `invoke_agent`, `execute_tool`).
- Run **verdict** (from structured output) separate from process status; optional outcome check command; pass^k for routines.

### 2.8 Safety for autonomous runs
Replace `--dangerously-skip-permissions` for heartbeat/routine/webhook runs with `--permission-mode auto --permission-prompts none`
(Claude) or `--permission-prompt-tool` → telecode MCP `approve_tool` → Telegram with timeout → deny. Branch-prefixed pushes,
per-trigger connector scoping, untrusted payload wrapping, the Job-Object gap closed by the Engine Runner (B10).

### 2.9 UI (the redesign in progress plugs into this)
Agent board (Working / Needs input / Idle / Done / Failed with one-line live summaries), approvals inbox, run timeline with grouped
tool cards + phase Gantt + cost per step, session/lineage tree, per-run diff + memory-diff tabs, artifacts browser, kanban view of
tasks, shared Team · Tasks · Design navbar.

---

## 3. Phased plan

| Phase | Scope | Risk |
|---|---|---|
| **P0 — Fix-first** (S) — **done** | B1 (no absolute TTL on workspaces; expiry archives), B2 (full handoff text, capped at 16 KB, + artifact list), B3 (engine/model/local per step & heartbeat), B4, B5, B9 (path validation), B10 (tree-kill via shared helper), B11 (codex usage), B12 (separate pool for heartbeat/routine), B13 (enforce timeout; lock skip-if-running), cancel no longer overwrites finished tasks | low, contained |
| **P1 — Engine Runner + Store** (M) — **done** | `services/engine/` used by all modes incl. TeleDesign; SQLite store for tasks/events/runs/steps/attempts/sessions + startup reconcile; SSE for Task/Team; resume scope (workspace, agent, engine); explicit context via `--append-system-prompt-file`; stop clobbering workspace CLAUDE.md | medium — touches every mode; keep the REST surface backward compatible |
| **P2 — Handoffs, budgets, snapshots** (M) — **done** | structured step outputs + artifacts; budgets; retries; shadow-git snapshots + diff/revert; session policy per step (resume/fork/fresh/ephemeral) | medium |
| **P3 — New step kinds + triggers** (M) — **done** | map / loop / gate / reduce; approvals inbox + Telegram; unified Triggers (cron/interval/at/webhook/github/file) with heartbeat cost controls and goals | medium |
| **P4 — Memory** (S–M) — **done** | git-versioned `internal/`, index + topic files, pinned constraints, autoMemoryDirectory, portable skills, reflection job with approval | low |
| **P5 — Observability + safety** (M) | OTLP receiver + GenAI spans + cost dashboards; verdicts/evals; auto permission mode + approve_tool; session rotation at thresholds; cross-engine continue | medium |

### P1 as built (2026-09-25)
- `services/engine/`: `run_engine(EngineRequest) -> EngineResult` used by the three task handlers (now thin wrappers) and TeleDesign's `design_turn_handler`. No shell; CLI created suspended, bound to the lifetime Job and a nested per-run Job, then resumed; cancel/timeout = CTRL_BREAK (helper on the CLI's console) → grace → `TerminateJobObject` + tree-kill. Normalised events `start|delta|narrative|tool|todo|usage|warning|retry|done|error`; adapters per engine; `schema` → Claude `--json-schema` / Codex `--output-schema` (agy: not yet). The P0 Job-binding gap is closed.
- `data/telecode.db` (`services/db/`): tasks + capped task events (write-through via one background writer), runs + run_steps (run_store is SQLite-only; one-time import of `data/runs/*.json`, files kept), `sessions_index` lineage. Startup reconcile of tasks. Logs and raw CLI logs unchanged.
- SSE: `GET /api/tasks/{id}/events` (replay + live), `GET /api/runs/{id}/events`, `GET /api/events?kinds=task,run`; Task and Team UIs subscribe and fall back to polling.
- Not in P1 (deferred): Attempt as a separate table (a step still maps to one task), `fork|fresh|ephemeral` session policies and rotation (P2), OTEL env on spawn (P5), a REST route for `sessions_index`, agy structured output.

### P2 as built (2026-09-25)
- **Run → Step → Attempt.** Attempts are records inside the step (`run_steps.data.attempts[]`: mode, task, status, CLI
  session, snapshots, usage, budget), not a table. Everything a retry needs is copied into the run at creation
  (`job_snapshot`, per-step `spec`). The executor (`services/run/executor.py`) was rewritten around `_attempt()`.
- **Handoffs** (`services/run/handoff.py`): shared strict JSON Schema → Claude `--json-schema` / Codex `--output-schema`;
  agy is told to write `.telecode/handoff.json` (read + removed by the handler). Invalid/missing → derived from the final
  text (`status/verdict: unknown`, `derived: true`). The next step gets `<handoff>` block(s) (summary, decisions, open
  questions, next steps, verdict, artifact stored paths + descriptions, files changed) + `--add-dir` of those artifact
  dirs; the full reply stays on the step (`result_text`, 256 KB) and the task row. Stored per step in the step record and
  a `run_steps.handoff` column (migration 2). Artifacts are copied to `data/runs/<run>/artifacts/<step>/`; an ephemeral
  step also keeps every added/modified file there (the "parallel files lost" fix). `GET …/steps/{id}/artifacts/{path}`.
- **Budgets** (`services/run/budget.py`): `job.budget` / POST body `budget` / `step.budget`, `{max_usd, max_tokens,
  max_seconds}`; unset dimensions of a step get an even share of what the run has left ($/tokens per remaining step,
  seconds per remaining phase). Claude `--max-budget-usd` (result `subtype: error_max_budget_usd`); the runner enforces
  wall clock + tokens from normalised `usage` events (Claude now emits a live `usage` per `message_delta`) and raises
  `EngineBudgetExceeded` → step `budget_exceeded`. **Budget tokens = input + cache writes + output** (cache reads excluded,
  or one Claude turn would eat any cap). A phase whose run budget is used up is not started. Run shows `budget` vs `spent`.
  Codex/agy report no cost, so `max_usd` is not enforceable for them (shown in the UI).
- **Retries**: `POST /api/runs/{run}/steps/{step}/retry {mode: retry|retry_clean, budget?}` (409 while the run is live);
  `retry` resumes the attempt's CLI session with "continue from where you stopped" (fresh when it has none),
  `retry_clean` restores the step's first pre-step snapshot and starts fresh. The step and all later phases reset to
  pending and a new driver continues. `spec.auto_retry` (0–5) re-runs after transient failures (overloaded / rate limit /
  5xx / network, matched in the error and the attempt's events) with backoff `tasks.retry_backoff_sec · 2^(n-1)`.
- **Snapshots** (`services/snapshots/`): parentless commits + `refs/snapshots/<ms>-<seq>` in
  `data/snapshots/<ns|root>--<sid>.git` (so pruning never rewrites a sha a step points at); `GIT_CONFIG_GLOBAL=devnull`,
  `CREATE_NO_WINDOW`, excludes `.telecode/`, `session.json`, `node_modules/`, venvs, files > `max_file_mb`; honours the
  workspace's own `.gitignore`; nested repos become gitlinks. Restore = safety snapshot → `read-tree -m -u` → restore
  snapshot (undoable). Keep `tasks.snapshots.keep` (200) / `max_repo_mb` (2048). API: step `diff` / `revert`,
  `GET|POST /api/sessions/{sid}/snapshots`, `…/snapshots/{sha}/diff`, `…/snapshots/{sha}/restore`.
- **Session policy** per step: `resume | fork | fresh | fresh_handoff | ephemeral`; blank = resume for a single-step phase,
  **ephemeral forced** for every step of a parallel phase (Claude resumes only in the cwd a session began in). fork =
  Claude `--resume <id> --fork-session`, Codex `codex exec … fork <id>` (verified on 0.157), agy → fresh + handoff. Fork
  source = the previous step's session when it is the same engine in the same workspace, else the scope's own.
  `sessions_index` gains `lineage` (fresh/resume/fork/rotation/ephemeral), `policy`, `forked_from`, `rotated_from`,
  `cumulative_tokens` (migration 2); `GET /api/sessions/{sid}/lineage`.
- **Rotation** (`handlers/_common.py`, every task, not only runs): resuming a conversation whose `cumulative_tokens` ≥
  `tasks.rotate_after_tokens` (400k) — or a routine's whose `runs_count` ≥ `tasks.rotate_after_fires` (50) — first asks it
  for a structured handoff, then continues fresh with `<rotated_session>` (that handoff + the agent's `## Pinned
  constraints` section of AGENT.md) prepended; lineage `rotation`.
- **UI**: pipeline step "Session, budget, retries" (policy, auto-retry, step budget), job run budget, run-modal budget
  override; run monitor budget meter, per-step handoff card with artifact downloads, attempts pill, Retry / Retry clean /
  Diff (file list + coloured unified diff) / Revert; snapshot timeline with diff + restore in the Team workspace view and
  the Task-mode session tab. Shared pieces in `shared/manager.js` / `manager.css`.
- **Deferred**: an `attempts` table and step-level idempotency keys; cross-engine continue (P5); map/loop/gate kinds (P3);
  the "fork from here" snapshot+fork action; dollar caps for Codex/agy (no cost reported); agy's token cap (it reports
  usage only at the end, so only its wall clock is enforced mid-run).

### P3 as built (2026-09-25)
- **Step kinds** (`job_manager._normalize_kind` / `executor`): `kind: agent | map | loop | gate | reduce`; the last four must be
  alone in their phase (map / reduce also need a previous phase). **map** — width = the previous phase's handoff field
  (`items_from`: `next_steps | items | open_questions | decisions | artifacts`; `items` is a new, always-present handoff field and
  the planner gets `<fanout_instructions>` when the next step reads it), `max_items`, `max_parallel`; each worker is an
  ephemeral copy of the workspace with the previous handoff + `<map_item>`, or (`worker_session: fork`) a fork of the planner's
  conversation in the planner's workspace (serialised by the staging lock). The step budget is split per worker (seconds per
  wave). Workers live in `step.workers[]` (own snapshots → `…/diff?worker=n`, artifacts under `artifacts/<step>/w<n>/`); the
  step's handoff merges them and the next phase gets one `<handoff>` per worker; `retry` re-runs only unfinished workers.
  **loop** — the body is an agent attempt (`attempts[].mode = iteration`), then a check: `command` (shell in the workspace,
  exit 0 = pass, output in `data/runs/<run>/checks/`), `grader` (an agent — optional own agent / engine / model — in a FRESH
  ephemeral copy with the rubric, answering `{verdict, summary, findings}` via `--json-schema`; agy: a `VERDICT:` line) or
  `schema` (the body's handoff, or a workspace JSON file, against a JSON Schema — `services/run/jsonschema_lite.py`). Findings
  go back to the body, which resumes its conversation; `max_iterations` (1–10); a step budget covers all iterations; the grader's
  usage counts toward the step. `step.iterations[]` records each check. **gate** — an `approvals` row, step + run
  `awaiting_input`, the driver exits (no thread held); startup reconcile leaves it waiting; approve (optionally with edited text,
  which becomes the gate's handoff; what the gate was shown is passed through too) launches a new driver from the next phase;
  reject → step + run `rejected`; cancel cancels the approval; a rejected gate can be asked again (`retry`). **reduce** — an agent
  that gets every previous handoff + `<reduce_instructions>`, fresh conversation by default.
- **Run store**: statuses `awaiting_input`, `rejected` (runs and steps); an awaiting run has no `completed_at`; runs carry
  `trigger_id` / `trigger_fire_id`; `overrides.permission_mode` / `session_policy`; `job_snapshot.context` / `pinned`.
- **Approvals** (`services/approvals.py`, migration 3): `approvals` table; atomic decide + per-kind handler; REST
  `/api/approvals[/{id}/approve|reject]`; `approval.created|decided` on the global SSE feed. Telegram
  (`bot/approval_handlers.py`): posts pending approvals to General with Approve / Reject (`apv:a|r:<id>`), edits on any decision,
  sweeps for ones created while the bot was down, posts trigger notices; only `allowed_user_ids` decide (empty = nobody).
- **Triggers** (`services/triggers/`, migration 3: `triggers` + `trigger_fires`): one record type (target task | agent_prompt |
  job; schedule cron + IANA tz | every | at; events webhook / GitHub / file; session shared | fresh; active hours; skip-if-empty;
  OK-reply suppression; model override; goal (check command / features.json / max fires / max cost) → auto-pause + notice;
  auto-pause after K failures; skip-if-running under a per-trigger lock; catch-up skip | once; pinned constraints at the tail;
  untrusted payload wrapping). One daemon thread in the proxy (15 s tick, 2 s file poll, 60 s HEARTBEAT.md compile).
  HEARTBEAT.md compiles to `agent_prompt` triggers (`source_key hb:<agent>:<name>`), gated by `heartbeat.enabled` for
  unattended fires. `services/routine/`, `services/heartbeat/`, `proxy/api_routines.py` and `kind: heartbeat` jobs are gone;
  `migrate.py` moved their data once (routines → task triggers with counters and last fire; heartbeat state → last fire times).
  Deleting a job / agent deletes its triggers. Windows needs the `tzdata` package (now in requirements.txt).
- **Safety**: trigger-fired Claude runs (task, agent and every step of a trigger-fired job run) use `--permission-mode <mode>
  --permission-prompts none` (per-trigger `permission_mode`, default `auto`; `skip` keeps `--dangerously-skip-permissions`).
  Verified with claude 2.1.282: `--permission-mode acceptEdits` and (with sonnet) `auto` are reflected in the CLI's init event;
  with haiku, `auto` degrades to `default` — with prompts off, edits and commands are then denied (the editor says so).
  Webhook / GitHub / file payloads are always wrapped as `<trigger-payload untrusted="true">`; webhook tokens compare in constant
  time, GitHub deliveries must carry a valid HMAC; list responses mask tokens and secrets; bodies over 256 KB → 413.
- **UI**: pipeline editor step-kind picker + kind fields (map source / parallel / items / worker session; loop check type /
  command / rubric / grader engine / schema / max iterations; gate title / instructions; reduce hint); run monitor map lanes
  (worker cards with events modal + diff), loop iterations with verdicts / findings, gate card with Approve / Edit & approve /
  Reject; approvals inbox (badge in `<tc-appnav>`, modal with waiting / history); `shared/triggers.js` list / detail (stats,
  schedule, conditions, webhook URL + token + curl, GitHub URL + secret, history filterable by ok / suppressed / skipped / failed)
  / editor. Task Mode: Triggers tab + By-trigger history; Team Mode: rail Triggers section, trigger page, Triggers cards on jobs
  and agents. All global live updates share one EventSource (`globalFeed`).
- **Deferred**: `tool` and `memory` approval producers (P4/P5: `--permission-prompt-tool` → `approve_tool`, reflection-job memory
  diffs); gate timeouts / reminders; edit-then-approve from Telegram; per-trigger connector / MCP scoping; a permission-mode
  equivalent for Codex / agy (they keep their bypass flags); map over a nested structured field other than the handoff lists;
  a parallel fork mode for map workers (forks share the planner's cwd, so they serialise); trigger-level budgets beyond
  `max_cost_usd`; an `attempts` table (still records in the step).

### P4 as built (2026-09-25)
- **Git-versioned `internal/`** (`services/memory/repo.py`): `data/agents/<id>/internal/.git`, separate from every workspace git and
  the snapshot repos; `CREATE_NO_WINDOW`, `GIT_CONFIG_GLOBAL=devnull`, LF files. Every change is a commit with `telecode-*`
  trailers: write-back `run:<run> step:<step>` / `task:<id>`, `ui: …`, `reflection: …`, `revert: …`, `migrate: …`. One re-entrant
  lock per repo (`repo.lock_for`) shared by staging, the memory API and `agent_manager`.
- **Merge-on-write-back replaces the P0 merge**: when nothing committed since the run was staged, one commit on main (it also
  carries files the engine wrote straight into `memory/`); otherwise the run's commit is built on the staged base in a temporary
  index and merged as a per-run branch (`git merge --no-ff`, `merge run:…`). Only files git reports conflicted go through the P0
  `merge3` (moved to `services/memory/merge.py`, still re-exported by staging): same-point appends keep both, true overlaps keep
  both between `<<<<<<< this run` markers + a warning. API: `GET /api/agents/{id}/memory/history?limit&path`,
  `GET …/memory/diff?commit&path`, `POST …/memory/revert {commit}` (a new commit; 409 when later changes overlap; the root is refused).
- **Index + typed topic files** (`services/memory/store.py`): `memory/MEMORY.md` is the index (the API still calls it `MEMORY.md`;
  ≤200 lines / 25 KB, warned not enforced for agent writes, `- [Name](file.md) — description`) + topic files with frontmatter
  `name / description / type: user|feedback|project|reference` (+ `helpful` / `harmful` on feedback) — Claude Code's own auto-memory
  shape. Only the index is staged; its first line names the topic dir when topics exist (stripped on write-back). REST:
  `GET /api/agents/{id}/memory`, `PUT …/memory/index`, `POST …/memory/index/rebuild`, `GET|PUT|DELETE …/memory/topics/{file}`.
- **Migration, once per agent** (`store.ensure`, on first touch; `reflection.start()` sweeps every agent at proxy start): `git init`
  of the existing files (CRLF normalised) → `MEMORY.md` renamed `MEMORY.legacy.md` (a commit, so the original stays in history) →
  split by the shallowest repeating heading (usually `##`; text before it → "General"; no headings → one "Notes" topic; type from
  heading keywords) → legacy file removed. An interrupted migration resumes from `MEMORY.legacy.md`.
- **One memory per agent across engines** — `services.memory.engine_extras(agent_id, engine, workspace)`, applied by the Engine
  Runner (P5 wiring): Claude `--settings <data/agents/<id>/claude-memory-settings.json>` = `{"autoMemoryDirectory": "<internal/memory>"}`
  (flag settings are honoured for this key, project settings are not; verified with claude 2.1.282 + haiku: the index is loaded
  from there) + `add_dirs [memory]`; Codex / agy `add_dirs [memory]` (codex 0.157 `exec --add-dir` exists); returns `settings` too,
  for a runner that must merge several `--settings`. A reflection fire gets `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` and no memory dir.
- **Pinned constraints**: the `## Pinned constraints` section of AGENT.md, one parser — `services.memory.pinned_constraints()`
  (`_common.pinned_constraints`, which triggers and rotation call, delegates to it). `GET|PUT /api/agents/{id}/pinned`.
- **Portable skills** (`services/skills/agent_skills.py`): `internal/skills/<name>/` staged into `.agents/skills/` and
  `.claude/skills/` for the run and removed after (only what staging created, incl. empty parents; recorded in the staging manifest,
  so a crash is cleaned by the next stage); a skill folder the workspace already has is never staged over. REST:
  `/api/agents/{id}/skills[/{name}[/files/{rel}]]`, `POST …/skills/{name}/copy-to-global`, `POST /api/skills/{name}/promote {agent_id}`.
- **Reflection** (`services/memory/reflection.py`): one `agent_prompt` trigger per agent through `services.triggers.service`
  (fresh session, the agent's engine / model — or `tasks.memory.reflect_model` —, cloud unless `tasks.memory.reflect_local`, cron
  `tasks.memory.reflect_cron` in `reflect_tz`, paused unless nightly is switched on). Fires nightly, after
  `tasks.memory.reflect_after_runs` runs (default 10; 0 = off; per-agent override; a pipeline run counts once), or on Reflect now
  (409 while one runs). The fire is staged with `.telecode/memory_reflection_input.md` (index, every topic with counters, pinned
  constraints, the runs since the last reflection: handoffs, replies, errors) and its write-back is discarded. The JSON reply
  (Mem0 ADD / UPDATE / DELETE / NOOP per candidate, ACE helpful / harmful deltas) becomes a proposal commit on the current memory
  (`refs/proposals/<task>`, work tree untouched) and an approval of kind `memory` whose body is the diff; approve → merged
  (fast-forward, or a merge when memory moved), reject → the ref is dropped; a newer proposal supersedes a pending one; an
  unparseable reply or nothing to change is recorded, not proposed. A 30 s ticker in the proxy turns finished fires into approvals.
- **UI** (`shared/memory.js` + `memory.css`, mounted in the Team agent view): Memory & skills card — Memory (index + topic list with
  type / counters / unindexed flags, editors, new memory, rebuild index, per-file history), History (commit timeline with kind,
  run / task, per-commit diff, revert), Skills (CRUD, from global, copy to global), Pinned (editor), Reflection (Reflect now,
  nightly switch, after-N-runs, trigger link, last proposal with its operations, review). The approvals inbox renders a `memory`
  approval's summary + coloured diff (no "Edit & approve").
- **Deferred**: writing back skill edits a run makes in `.agents/skills` (they are discarded with the staged copy); injecting the
  agent's pinned constraints at the tail of every *pipeline step* (they reach steps through AGENT.md; triggers and rotation append
  them); local-model reflection behind a `design.local_helpers`-style switch beyond `tasks.memory.reflect_local`; semantic dedupe /
  embeddings for reflection; editing a memory proposal before approving it; a Telegram rendering of the diff (the inbox shows it).

### P5 as built (2026-09-25)
- **OTLP receiver** (`proxy/api_telemetry.py`, `services/telemetry/`): `POST /otlp/v1/{metrics,logs,traces}`, OTLP/JSON and
  OTLP/protobuf (a dependency-free, schema-driven decoder in `telemetry/protobuf.py`; unknown fields skipped), gzip accepted,
  **loopback peers only** (403 otherwise, whatever `proxy.host` is). Rows go to migration-4 tables `spans` / `metric_points` /
  `log_events`, keyed by the `telecode.*` resource attributes (task / run / step / agent / job / trigger); account identity
  attributes (`user.email`, `user.account_uuid`, `organization.id`…) are dropped at ingest. Retention `telemetry.retention_days`
  (14), pruned at most hourly. `telemetry.enabled` (default true) gates the CLI env, own spans and ingest.
- **CLI env** (`services/engine/otel.py`, applied by the adapters when the request carries `correlation`): Claude gets
  `CLAUDE_CODE_ENABLE_TELEMETRY=1`, `OTEL_{METRICS,LOGS}_EXPORTER=otlp`, `OTEL_EXPORTER_OTLP_PROTOCOL=http/json`,
  `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:<proxy>/otlp`, delta temporality, short export intervals and
  `OTEL_RESOURCE_ATTRIBUTES=telecode.task_id=…,telecode.run_id=…,telecode.step_id=…,…` (percent-encoded); inherited `OTEL_*`
  exporter/endpoint/header vars are scrubbed first so nothing leaves the box. **Verified on claude 2.1.282** with a real haiku run:
  it posts `/otlp/v1/logs` and `/otlp/v1/metrics` as OTLP/JSON, every record carries the resource ids, the events are
  `claude_code.{api_request,tool_result,tool_decision,user_prompt,assistant_response,mcp_server_connection,…}` (api_request has
  `cost_usd, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, duration_ms, model`), metrics
  `claude_code.{cost.usage,token.usage(type=input|output|cacheRead|cacheCreation),session.count,active_time.total}`; the OTLP cost
  summed to exactly the stream's `total_cost_usd`. Codex: `-c otel.environment=telecode -c otel.log_user_prompt=false` +
  `otel.{exporter,metrics_exporter,trace_exporter}.otlp-http.{endpoint,protocol=json}` (dotted, unquoted; **verified on 0.157**
  that the config loader accepts them — `codex -c … mcp list` validates without a model call and names every variant) plus
  `OTEL_RESOURCE_ATTRIBUTES` in the env (not verified to be honoured). agy: no OTel export.
- **Own spans** (`telemetry/spans.py`, GenAI semconv): `invoke_workflow` per run (executor, on every driver exit), `invoke_agent`
  per Engine Runner call (step attempt, map worker, loop iteration / grader, Task-mode task, design turn) with `gen_ai.usage.*`,
  cost, the CLI-reported model, conversation id; `execute_tool` per normalised tool event (ends at the next event —
  `telecode.duration_estimated`). Deterministic trace id per run and workflow span id, so parents link without coordination;
  written through the ordered DB writer.
- **Dashboards**: `GET /api/telemetry/summary?group=agent|job|trigger|engine|model&since=7d` (totals, zero-filled by-day series,
  groups with failure rate + cache-read ratio + run outcomes, top tools, OTLP cost per model), `…/runs/{id}/timeline` (phases →
  steps → attempts/workers with times, cost, tokens, model, tool counts, OTLP cost per step), `…/triggers/{id}/passk?k=`,
  `…/tasks/{id}`, `…/status`. UI `proxy/static/shared/observe.{js,css}` (inline SVG / HTML, hover tooltips, table toggle,
  light/dark roles validated with the dataviz validator): Team overview "Cost & outcomes" card, run-monitor verdict + Gantt,
  Task-mode "Usage" tab, pass^k tile on trigger pages.
- **Verdicts** (`telemetry/verdict.py`): every finished run gets `process_ok`, `verdict` (pass | fail | unknown),
  `verdict_source` (outcome_check | handoff | process), `verdict_detail`; job `outcome_check {command, timeout_seconds}` runs in the
  workspace after the run (also after a failed one) and decides the verdict; cancelled → unknown; else a non-completed run → fail;
  else the final phase's handoff verdicts. pass^k per trigger over the last k decided fires (run verdict, else process status):
  strict `pass_k`, `p_hat`, `p_hat^k`.
- **Safety** — permission mode `ask` (trigger `permission_mode`, job `permission_mode`, or task metadata): Claude gets
  `--permission-mode manual --permission-prompts host --permission-prompt-tool mcp__telecode_safety__approve_tool --mcp-config
  <per-run file>` naming telecode's MCP server with `X-Telecode-Task|Run|Step|Agent|Trigger` headers; `MCP_TOOL_TIMEOUT` outlasts
  the wait. `mcp_server/tools/approvals.py::approve_tool` opens a `tool` approval (web inbox + Telegram), waits
  (`safety.approval_timeout_sec`, 600) and answers `{"behavior":"allow","updatedInput":…}` (edited JSON replaces the input) /
  `{"behavior":"deny","message":…}`; timeout or a stopped task → cancelled + deny. With `mcp_server.enabled` off, `ask` falls back
  to `auto` with a warning event. Verified with real haiku runs: the flag is accepted, the MCP server connects, Claude calls the
  tool for Bash, the approval appears with the run/step ids and is decided over REST; two findings fixed from it — the mode must be
  pinned (with none, the user's `defaultMode: bypassPermissions` applied and nothing was asked), and the result must be a single
  text block (FastMCP's default `structuredContent` for a `str` return was rejected; now `structured_output=False`, wire shape
  checked with the MCP client). Auto-pause after K consecutive failures is P3's `_maybe_auto_pause` (covered by the P3 tests).
- **Cross-engine continue** (`services/engine/handover.py`, `proxy/api_continue.py`): `POST /api/sessions/{sid}/continue {engine,
  model?, is_local?, namespace?, prompt?, run_id?, step_id?}` → neutral package (latest handoff — the named step's, else the newest
  run step / Task-mode rotation handoff / structured output, else the last reply; workspace diff since the first snapshot, taken
  fresh now; PROGRESS.md) written to `<ws>/.telecode/continue/<ts>-<engine>.{json,md}`, a capped rendering prepended to a fresh
  conversation on the target engine in the same workspace; `sessions_index.lineage = engine_switch`, `switched_from` = the source
  row (migration 4). 409 while a task runs there. UI: "Continue on…" on Task sessions and finished run steps.
- Also: Codex `--add-dir` is passed (exec-only, before `resume`/`fork`; verified on 0.157) — P4's `engine_extras` add_dirs reach it.
  The runner calls `services.memory.engine_extras(agent_id, engine, workspace)` for agent runs (lazy import; failures → none).
- **Deferred**: Codex / agy permission prompts (they keep their bypass flags); Codex resource attributes and OTLP traces are
  configured but unverified at runtime (no real Codex run); Claude's beta traces behind `telemetry.cli_traces` (off); tool-span
  durations are estimated; no per-trigger connector / MCP scoping; `session rotation at thresholds` was already P2.

Compatibility: P3 replaced `/api/routines` with `/api/triggers` and the heartbeat job kind with triggers (single user — the UI moved
with it and the data migrated once); every other `/api/*` route keeps its shape (new fields only).

## 4. Sources (primary)
Anthropic: effective context engineering · building effective agents · effective harnesses for long-running agents · multi-agent
research system · demystifying evals · Claude Code headless / cli-reference / agent-sdk sessions / file-checkpointing / memory /
permission-modes / monitoring-usage / routines / agent-teams. OpenAI: Agents SDK handoffs, sessions, guardrails; Codex app-server
README, config-advanced. Google ADK: sessions/state, compaction, context-aware multi-agent blog. LangGraph interrupts / time travel.
Microsoft Agent Framework: human-in-the-loop, checkpoints. Letta: context repositories, MemFS, sleep-time. Mem0 (arXiv 2504.19413),
ACE (arXiv 2510.04618), Zep/Graphiti (arXiv 2501.13956), compaction constraint loss (arXiv 2608.11242). Goose recipes/subrecipes,
Cline checkpoints, Copilot agent HQ, OpenClaw heartbeat, A2A spec, MCP 2025-11-25 tasks, OTel GenAI semconv.
