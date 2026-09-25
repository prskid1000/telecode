# Team-mode test suite

Pytest suite for agents, jobs, runs, triggers and approvals.

## Layout

| File | Layer | What it covers |
|---|---|---|
| `test_unit_pipeline_normalize.py` | unit | `_normalize_pipeline` for all 4 modes, phase auto-fill, renumbering |
| `test_unit_run_helpers.py` | unit | `_engine_to_task_type`, `_result_preview`, `_build_step_prompt` (single + multi-output threading) |
| `test_unit_agent_prompt.py` | unit | XML envelope, `<instructions>` removal, escaping, `resolve_prompt` precedence |
| `test_int_agent_manager.py` | integration | Agent CRUD, internal-files whitelist, per-agent lock, user-files isolation |
| `test_int_job_manager.py` | integration | Job CRUD, archived filter, the heartbeat kind is gone, pipeline normalisation on update |
| `test_int_run_store.py` | integration | Run CRUD, `finalise()` status aggregation matrix |
| `test_int_staging.py` | integration | Stage / writeback / unstage with the AGENT.md → CLAUDE.md rename |
| `test_flow_run_executor.py` | flow | Phase-based executor with a fake task handler — single, sequential, parallel, custom; output threading; failure halt; cancellation |
| `test_int_session_expiry.py` | integration | B1: absolute TTL ignored for workspaces, idle expiry archives, in-use never expires, `ensure()` restores |
| `test_flow_run_handoff_engine.py` | flow | B2 full handoff + files changed, B3 run/step/agent engine precedence, B6 orphaned runs, B10 cancel, B11 usage roll-up |
| `test_int_handlers_p0.py` | integration | B3/B7/B8 through the real handlers with the CLI mocked, B6 prompt digest, B11 codex usage |
| `test_int_staging_p0.py` | integration | B8 backup/restore, crash repair, three-way MEMORY merge |
| `test_int_paths_api_p0.py` | integration | B9 id/path validation at manager and HTTP level; B3 run body over HTTP |
| `test_int_task_queue_p0.py` | integration | B10 real process-tree kill, B13 timeout, B12 pools, B6 eviction |
| `test_unit_engine_adapters_p1.py` | unit | P1 adapters: argv/env/stdin per engine and event normalisation from recorded claude / codex 0.157 / agy logs (`fixtures/engine/`) |
| `test_int_engine_runner_p1.py` | integration | P1 runner with `fixtures/engine/fake_cli.py`: sinks + raw log, stdin, stderr drain, failure, cancel/timeout tree-kill incl. an orphaned grandchild, graceful CTRL_BREAK, Job membership, .cmd/.bat shims, queue cancel |
| `test_int_store_sse_p1.py` | integration | P1 store + SSE: restart persistence, reconcile, event cap, REST fallback, task/run/global SSE, run JSON import, lineage, TeleDesign → runner |
| `test_flow_p2_runs.py` | flow | P2 handoffs + artifacts, snapshots / diff / revert, retries, budgets, session policies, rotation (fake agent CLI) |
| `test_unit_p3.py` | unit | P3 schedule math (cron in IANA zones, intervals, one-off, active hours), trigger model, webhook / GitHub auth + filters, payload wrapping, OK replies, JSON Schema checker, Claude permission flags, approvals, global feed kinds |
| `test_flow_p3_kinds.py` | flow | P3 step kinds with the fake agent CLI: map → reduce, map retry, loop (command / grader / schema), gate approve (REST, edited text) / reject / restart / cancel |
| `test_flow_p3_triggers.py` | flow | P3 triggers: fire now, permission mode, shared session, OK suppression, skip-if-running, tick / catch-up / active hours, heartbeat gate, auto-pause, goals, one-off, skip-if-empty, file watch, job target, REST + webhook + GitHub, HEARTBEAT.md compile, migration |
| `test_flow_p3_trigger_guarantees.py` | flow | HEARTBEAT.md validation, B3 engine/model/local, B4 first slot, B6 interrupted fires, B13 timeout, single submit under concurrency, REST glue, webhook body handling |
| `../test_approval_telegram.py` | unit | Telegram approvals with fake Update / CallbackQuery / Bot: allowlist, callback validation, edit on decision, sweep, escaping |
| `test_e2e_http.py` | e2e | Hits the running proxy at `:1235` — only with `TELECODE_E2E_HTTP=1` (it would make the live telecode load llama). Workspace/agent CRUD, pipeline normalisation, run topology, HEARTBEAT.md → triggers. |

## Run

```bash
# All
./telecode-venv/Scripts/python.exe -m pytest tests/team

# By layer
./telecode-venv/Scripts/python.exe -m pytest tests/team -k "test_unit"
./telecode-venv/Scripts/python.exe -m pytest tests/team -k "test_int"
./telecode-venv/Scripts/python.exe -m pytest tests/team -k "test_flow"
./telecode-venv/Scripts/python.exe -m pytest tests/team -k "test_e2e"

# Verbose with progress
./telecode-venv/Scripts/python.exe -m pytest tests/team -v
```

## Fixtures (`conftest.py`)

- **`tmp_data_root`** — redirects `config._settings_dir()` to a tmp dir and re-instantiates every team-mode singleton (`AgentManager`, `JobManager`, `RunStore`) so their cached base directories pick up the new path. Cleans up between tests.
- **`fake_task_queue`** — registers a synthetic `CLAUDE_CODE` handler that doesn't spawn a CLI. Exposes `.set_result(text)`, `.fail()`, `.block()` / `.release()` (for cancellation tests), `.last_calls`, and `.reset()`. Lets the flow tests exercise the run executor and the trigger scheduler end-to-end without subprocess overhead.

## E2E

The HTTP suite is skipped unless `TELECODE_E2E_HTTP=1` (and the proxy answers `/api/tasks/types`). Each test creates its own resources and cancels long-running runs early to keep wall-time bounded — full e2e file finishes in ~6s when the server is up.
