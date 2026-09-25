# Team-mode test suite

Pytest suite for agents, jobs, runs, and heartbeats.

## Layout

| File | Layer | What it covers |
|---|---|---|
| `test_unit_heartbeat_parser.py` | unit | YAML fences, validation rules, error reporting, `next_fires` |
| `test_unit_pipeline_normalize.py` | unit | `_normalize_pipeline` for all 4 modes, phase auto-fill, renumbering |
| `test_unit_run_helpers.py` | unit | `_engine_to_task_type`, `_result_preview`, `_build_step_prompt` (single + multi-output threading) |
| `test_unit_agent_prompt.py` | unit | XML envelope, `<instructions>` removal, escaping, `resolve_prompt` precedence |
| `test_int_agent_manager.py` | integration | Agent CRUD, internal-files whitelist, per-agent lock, user-files isolation |
| `test_int_job_manager.py` | integration | Job CRUD, kind/archived filters, pipeline normalisation on update, `find_heartbeat_job` |
| `test_int_run_store.py` | integration | Run CRUD, `finalise()` status aggregation matrix |
| `test_int_heartbeat_state.py` | integration | Atomic state file, `mark_fired/finished`, `prune_orphans` |
| `test_int_heartbeat_reconcile.py` | integration | YAML → HB Jobs sync (create / update / archive / un-archive on return) |
| `test_int_staging.py` | integration | Stage / writeback / unstage with the AGENT.md → CLAUDE.md rename |
| `test_flow_run_executor.py` | flow | Phase-based executor with a fake task handler — single, sequential, parallel, custom; output threading; failure halt; cancellation |
| `test_flow_heartbeat_scheduler.py` | flow | `_is_due`, `_fire`, `_sweep_ephemeral`, `_tick` (reconcile + cap on concurrent fires) |
| `test_int_session_expiry.py` | integration | B1: absolute TTL ignored for workspaces, idle expiry archives, in-use never expires, `ensure()` restores |
| `test_flow_run_handoff_engine.py` | flow | B2 full handoff + files changed, B3 run/step/agent engine precedence, B6 orphaned runs, B10 cancel, B11 usage roll-up |
| `test_flow_heartbeat_p0.py` | flow | B3 heartbeat engine/model/is_local, B4 first fire via `first_seen`, B6 stale state |
| `test_flow_routine_p0.py` | flow | B5 outputs_only, B13 lock/task_type/timeout, B6 interrupted fires |
| `test_int_handlers_p0.py` | integration | B3/B7/B8 through the real handlers with the CLI mocked, B6 prompt digest, B11 codex usage |
| `test_int_staging_p0.py` | integration | B8 backup/restore, crash repair, three-way MEMORY merge |
| `test_int_paths_api_p0.py` | integration | B9 id/path validation at manager and HTTP level; B3 run body over HTTP |
| `test_int_task_queue_p0.py` | integration | B10 real process-tree kill, B13 timeout, B12 pools, B6 eviction |
| `test_unit_engine_adapters_p1.py` | unit | P1 adapters: argv/env/stdin per engine and event normalisation from recorded claude / codex 0.157 / agy logs (`fixtures/engine/`) |
| `test_int_engine_runner_p1.py` | integration | P1 runner with `fixtures/engine/fake_cli.py`: sinks + raw log, stdin, stderr drain, failure, cancel/timeout tree-kill incl. an orphaned grandchild, graceful CTRL_BREAK, Job membership, .cmd/.bat shims, queue cancel |
| `test_int_store_sse_p1.py` | integration | P1 store + SSE: restart persistence, reconcile, event cap, REST fallback, task/run/global SSE, run JSON import, lineage, TeleDesign → runner |
| `test_e2e_http.py` | e2e | Hits the running proxy at `:1235` — workspace/agent CRUD, pipeline normalisation, parallel/custom run topology, heartbeat validate/reconcile/archive. Auto-skipped when server is down. |

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
- **`fake_task_queue`** — registers a synthetic `CLAUDE_CODE` handler that doesn't spawn a CLI. Exposes `.set_result(text)`, `.fail()`, `.block()` / `.release()` (for cancellation tests), `.last_calls`, and `.reset()`. Lets the flow tests exercise the run executor and heartbeat scheduler end-to-end without subprocess overhead.

## E2E

The HTTP suite uses `pytest.mark.skipif` against `/api/tasks/types` reachability so it's a no-op when the proxy isn't running. Each test creates its own resources and cancels long-running runs early to keep wall-time bounded — full e2e file finishes in ~6s when the server is up.
