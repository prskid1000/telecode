# Task Mode + Team Mode — architecture review and target design

Status: **proposal** (2026-09-25). Inputs: a read-only audit of `services/{session,task,agent,job,run,heartbeat,routine,skills}` +
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
| **P0 — Fix-first** (S) | B1 (no absolute TTL on workspaces; expiry archives), B2 (full handoff text, capped at 16 KB, + artifact list), B3 (engine/model/local per step & heartbeat), B4, B5, B9 (path validation), B10 (tree-kill via shared helper), B11 (codex usage), B12 (separate pool for heartbeat/routine), B13 (enforce timeout; lock skip-if-running), cancel no longer overwrites finished tasks | low, contained |
| **P1 — Engine Runner + Store** (M) | `services/engine/` used by all modes incl. TeleDesign; SQLite store for tasks/events/runs/steps/attempts/sessions + startup reconcile; SSE for Task/Team; resume scope (workspace, agent, engine); explicit context via `--append-system-prompt-file`; stop clobbering workspace CLAUDE.md | medium — touches every mode; keep the REST surface backward compatible |
| **P2 — Handoffs, budgets, snapshots** (M) | structured step outputs + artifacts; budgets; retries; shadow-git snapshots + diff/revert; session policy per step (resume/fork/fresh/ephemeral) | medium |
| **P3 — New step kinds + triggers** (M) | map / loop / gate / reduce; approvals inbox + Telegram; unified Triggers (cron/interval/at/webhook/github/file) with heartbeat cost controls and goals | medium |
| **P4 — Memory** (S–M) | git-versioned `internal/`, index + topic files, pinned constraints, autoMemoryDirectory, portable skills, reflection job with approval | low |
| **P5 — Observability + safety** (M) | OTLP receiver + GenAI spans + cost dashboards; verdicts/evals; auto permission mode + approve_tool; session rotation at thresholds; cross-engine continue | medium |

Backward compatibility rule: existing `/api/*` routes keep their shapes (new fields only); existing agents/jobs/routines/heartbeats
migrate on first load; the old pages keep working until the redesign lands.

## 4. Sources (primary)
Anthropic: effective context engineering · building effective agents · effective harnesses for long-running agents · multi-agent
research system · demystifying evals · Claude Code headless / cli-reference / agent-sdk sessions / file-checkpointing / memory /
permission-modes / monitoring-usage / routines / agent-teams. OpenAI: Agents SDK handoffs, sessions, guardrails; Codex app-server
README, config-advanced. Google ADK: sessions/state, compaction, context-aware multi-agent blog. LangGraph interrupts / time travel.
Microsoft Agent Framework: human-in-the-loop, checkpoints. Letta: context repositories, MemFS, sleep-time. Mem0 (arXiv 2504.19413),
ACE (arXiv 2510.04618), Zep/Graphiti (arXiv 2501.13956), compaction constraint loss (arXiv 2608.11242). Goose recipes/subrecipes,
Cline checkpoints, Copilot agent HQ, OpenClaw heartbeat, A2A spec, MCP 2025-11-25 tasks, OTel GenAI semconv.
