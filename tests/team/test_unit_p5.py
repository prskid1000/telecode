"""P5 unit tests: OTLP normalisation (recorded Claude Code 2.1.282 payloads,
sanitised — tests/team/fixtures/otlp/), the protobuf decoder, OTel env for the
CLIs, permission mode ``ask`` argv, verdict rules, pass^k, engine_extras."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from services.telemetry import otlp, protobuf, store, summary, verdict

FIX = Path(__file__).parent / "fixtures" / "otlp"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ── OTLP normalisation ──────────────────────────────────────────────────────

def test_recorded_claude_logs_normalise_with_correlation_and_cost():
    table, rows = otlp.normalise("logs", load("claude_logs.json"))
    assert table == "log_events"
    api = [r for r in rows if r["name"] == "claude_code.api_request"]
    assert len(api) == 2
    assert round(sum(r["cost_usd"] for r in api), 7) == 0.0577632      # = the stream's total_cost_usd
    assert {r["run_id"] for r in rows} == {"run-real-1"} and {r["step_id"] for r in rows} == {"step-real-1"}
    assert {r["task_id"] for r in rows} == {"task-real-1"}
    assert api[0]["model"].startswith("claude-haiku") and api[0]["cache_read_tokens"] > 0
    assert api[0]["cache_write_tokens"] > 0 and api[0]["duration_ms"] > 0
    tr = next(r for r in rows if r["name"] == "claude_code.tool_result")
    assert tr["tool_name"] == "Bash" and tr["success"] == 1
    assert all(r["session_id"] for r in api)


def test_recorded_claude_metrics_normalise():
    _, rows = otlp.normalise("metrics", load("claude_metrics.json"))
    cost = [r for r in rows if r["name"] == "claude_code.cost.usage"]
    assert round(sum(r["value"] for r in cost), 7) == 0.0577632
    toks = {r["type"] for r in rows if r["name"] == "claude_code.token.usage"}
    assert toks == {"input", "output", "cacheRead", "cacheCreation"}
    assert all(r["temporality"] == "delta" for r in rows)
    assert {r["agent_id"] for r in rows} == {"agent-real-1"}


def test_pii_attributes_are_dropped():
    payload = {"resourceLogs": [{"resource": {"attributes": [
        {"key": "user.email", "value": {"stringValue": "me@example.com"}},
        {"key": "telecode.run_id", "value": {"stringValue": "r1"}}]},
        "scopeLogs": [{"logRecords": [{"timeUnixNano": "1790000000000000000", "body": {"stringValue": "claude_code.api_request"},
                                        "attributes": [{"key": "organization.id", "value": {"stringValue": "org"}},
                                                       {"key": "cost_usd", "value": {"stringValue": "0.5"}}]}]}]}]}
    _, rows = otlp.normalise("logs", payload)
    assert rows[0]["cost_usd"] == 0.5 and rows[0]["run_id"] == "r1"
    blob = json.dumps(rows[0]["attributes"]) + json.dumps(rows[0]["resource"])
    assert "example.com" not in blob and "organization.id" not in blob
    assert rows[0]["ts_ms"] == 1790000000000


def test_protobuf_roundtrip_matches_json_for_all_signals():
    for sig, fx in (("logs", "claude_logs.json"), ("metrics", "claude_metrics.json")):
        j = load(fx)
        pb = protobuf.encode_request(sig, j)
        back = protobuf.decode_request(sig, pb)
        _, a = otlp.normalise(sig, j, received_ms=1)
        _, b = otlp.normalise(sig, back, received_ms=1)
        strip = lambda rs: [{k: v for k, v in r.items() if k not in ("attributes", "resource")} for r in rs]  # noqa: E731
        assert strip(a) == strip(b)
    trace = {"resourceSpans": [{"resource": {"attributes": [{"key": "telecode.task_id", "value": {"stringValue": "t9"}}]},
                                "scopeSpans": [{"spans": [{"traceId": "0af7651916cd43dd8448eb211c80319c", "spanId": "b7ad6b7169203331",
                                                           "name": "llm", "kind": 3, "startTimeUnixNano": 1790000000000000000,
                                                           "endTimeUnixNano": 1790000001500000000, "status": {"code": 2, "message": "boom"},
                                                           "attributes": [{"key": "gen_ai.usage.input_tokens", "value": {"intValue": 12}}]}]}]}]}
    back = protobuf.decode_request("traces", protobuf.encode_request("traces", trace))
    _, rows = otlp.normalise("traces", back)
    assert rows[0]["trace_id"] == "0af7651916cd43dd8448eb211c80319c" and rows[0]["duration_ms"] == 1500
    assert rows[0]["status"] == "error" and rows[0]["input_tokens"] == 12 and rows[0]["task_id"] == "t9"
    assert rows[0]["kind"] == "client" and rows[0]["source"] == "otlp"


def test_protobuf_skips_unknown_fields_and_rejects_garbage():
    # field 99 (varint) then a valid resourceLogs=[] → decodes, unknown skipped
    assert protobuf.decode_request("logs", bytes([0x98, 0x06, 0x01])) == {}
    with pytest.raises(protobuf.DecodeError):
        protobuf.decode_request("logs", b"\x0a\xff\xff\xff")


def test_ingest_writes_rows_and_prune_respects_retention():
    otlp.ingest("logs", load("claude_logs.json"))
    otlp.ingest("metrics", load("claude_metrics.json"))
    c = store.counts()
    assert c["log_events"]["rows"] > 5 and c["metric_points"]["rows"] > 5
    from services.db.core import connect
    connect().execute("UPDATE log_events SET ts_ms = 1000")     # 1970 → older than any retention
    assert store.prune(14, force=True) >= 1
    assert store.counts()["log_events"]["rows"] == 0 and store.counts()["metric_points"]["rows"] > 0


# ── OTel env for the CLIs ───────────────────────────────────────────────────

def test_claude_otel_env_and_scrub(monkeypatch):
    from services.engine import otel
    import config
    monkeypatch.setattr(config, "proxy_port", lambda: 18999)
    corr = {"task_id": "t1", "run_id": "r 1,x", "step_id": "s1", "agent_id": None}
    env = otel.claude_env(corr)
    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1" and env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/json"
    assert env["OTEL_METRICS_EXPORTER"] == env["OTEL_LOGS_EXPORTER"] == "otlp"
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://127.0.0.1:18999/otlp"
    ra = env["OTEL_RESOURCE_ATTRIBUTES"]
    assert "telecode.task_id=t1" in ra and "telecode.run_id=r%201%2Cx" in ra and "agent_id" not in ra
    merged = otel.with_env({"PATH": "x", "OTEL_EXPORTER_OTLP_ENDPOINT": "https://evil.example", "OTEL_EXPORTER_OTLP_HEADERS": "k=v",
                            "OTEL_TRACES_EXPORTER": "otlp"}, env)
    assert merged["OTEL_EXPORTER_OTLP_ENDPOINT"].startswith("http://127.0.0.1:") and "OTEL_EXPORTER_OTLP_HEADERS" not in merged
    assert merged["OTEL_TRACES_EXPORTER"] == "none" and merged["PATH"] == "x"


def test_codex_otel_overrides_are_quote_free_and_local(monkeypatch):
    from services.engine import otel
    import config
    monkeypatch.setattr(config, "proxy_port", lambda: 18999)
    ov = otel.codex_overrides()
    vals = ov[1::2]
    assert all(a == "-c" for a in ov[0::2])
    assert "otel.exporter.otlp-http.endpoint=http://127.0.0.1:18999/otlp/v1/logs" in vals
    assert "otel.metrics_exporter.otlp-http.endpoint=http://127.0.0.1:18999/otlp/v1/metrics" in vals
    assert "otel.trace_exporter=none" in vals   # traces only with telemetry.cli_traces (test_unit_codex_otel)
    assert not any('"' in v or "'" in v for v in vals)


def test_adapters_add_telemetry_only_with_correlation(tmp_path, monkeypatch):
    from services.engine import EngineRequest
    from services.engine.adapters import get_adapter
    req = EngineRequest(engine="claude_code", prompt="p", cwd=tmp_path, correlation={"task_id": "t1"})
    launch = get_adapter("claude_code").build(req)
    assert launch.env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    plain = get_adapter("claude_code").build(EngineRequest(engine="claude_code", prompt="p", cwd=tmp_path))
    assert plain.env is None
    import config
    real = config.get_nested
    monkeypatch.setattr(config, "get_nested", lambda k, d=None: False if k == "telemetry.enabled" else real(k, d))
    off = get_adapter("claude_code").build(req)
    assert off.env is None
    monkeypatch.setattr(config, "get_nested", real)
    creq = EngineRequest(engine="codex", prompt="p", cwd=tmp_path, correlation={"task_id": "t1"},
                         last_msg_path=tmp_path / "m.txt")
    cl = get_adapter("codex").build(creq)
    i = cl.argv.index("exec")
    assert cl.argv[i + 1] == "-c" and any(a.startswith("otel.exporter.otlp-http.endpoint=") for a in cl.argv)
    assert cl.argv[-1] == "-" and cl.extras_at == len(cl.argv) - 1
    assert "telecode.task_id=t1" in cl.env["OTEL_RESOURCE_ATTRIBUTES"]


# ── permission mode ask ─────────────────────────────────────────────────────

def test_permission_ask_uses_approve_tool_when_mcp_enabled(tmp_data_root, monkeypatch):
    from services.engine import EngineRequest
    from services.engine.adapters import claude as cl
    from services.engine.adapters import get_adapter
    import config
    monkeypatch.setattr(config, "mcp_server_enabled", lambda: True)
    monkeypatch.setattr(config, "mcp_server_port", lambda: 18236)
    req = EngineRequest(engine="claude_code", prompt="p", cwd=tmp_data_root, permission_mode="ask",
                        correlation={"task_id": "t1", "run_id": "r1", "step_id": "s1", "trigger_id": "tr1"})
    launch = get_adapter("claude_code").build(req)
    a = launch.argv
    assert "--dangerously-skip-permissions" not in a
    assert a[a.index("--permission-mode") + 1] == "manual" and a[a.index("--permission-prompts") + 1] == "host"
    assert a[a.index("--permission-prompt-tool") + 1] == "mcp__telecode_safety__approve_tool"
    cfg = json.loads(Path(a[a.index("--mcp-config") + 1]).read_text(encoding="utf-8"))
    srv = cfg["mcpServers"]["telecode_safety"]
    assert srv["type"] == "http" and srv["url"] == "http://127.0.0.1:18236/mcp"
    assert srv["headers"] == {"X-Telecode-Task": "t1", "X-Telecode-Run": "r1", "X-Telecode-Step": "s1",
                              "X-Telecode-Trigger": "tr1"}
    assert Path(a[a.index("--mcp-config") + 1]) in launch.cleanup
    assert int(launch.env["MCP_TOOL_TIMEOUT"]) > 600_000
    assert cl.permission_args(None) == ["--dangerously-skip-permissions"]


def test_permission_ask_falls_back_to_auto_without_mcp(tmp_data_root, monkeypatch):
    from services.engine import EngineRequest
    from services.engine.adapters import get_adapter
    import config
    monkeypatch.setattr(config, "mcp_server_enabled", lambda: False)
    launch = get_adapter("claude_code").build(EngineRequest(engine="claude_code", prompt="p", cwd=tmp_data_root,
                                                            permission_mode="ask"))
    a = launch.argv
    assert a[a.index("--permission-mode") + 1] == "auto" and "--permission-prompt-tool" not in a
    assert launch.warnings and "falling back to 'auto'" in launch.warnings[0]


def test_trigger_and_job_accept_ask():
    from services.triggers import model
    assert "ask" in model.PERMISSION_MODES
    from services.job.job_manager import _normalize_job_permission_mode
    assert _normalize_job_permission_mode("ask") == "ask"
    with pytest.raises(ValueError):
        _normalize_job_permission_mode("yolo")


# ── verdicts ────────────────────────────────────────────────────────────────

def _run(status, *verdicts, phase_last=1):
    steps = [{"step_id": "a", "status": "completed", "spec": {"phase": 0}, "handoff": {"verdict": "fail"}}]
    steps += [{"step_id": f"z{i}", "status": "completed" if status == "completed" else "failed",
               "spec": {"phase": phase_last}, "handoff": {"verdict": v}} for i, v in enumerate(verdicts)]
    return {"run_id": "r", "status": status, "steps": steps}


@pytest.mark.parametrize("status,verdicts,outcome,expect,source", [
    ("completed", ["pass", "pass"], None, "pass", "handoff"),
    ("completed", ["pass", "fail"], None, "fail", "handoff"),
    ("completed", ["pass", "unknown"], None, "unknown", "handoff"),
    ("failed", ["pass"], None, "fail", "process"),
    ("cancelled", ["pass"], {"passed": True, "exit_code": 0}, "unknown", "process"),
    ("completed", ["pass"], {"passed": False, "exit_code": 1}, "fail", "outcome_check"),
    ("partial", ["fail"], {"passed": True, "exit_code": 0}, "pass", "outcome_check"),
])
def test_verdict_rules(status, verdicts, outcome, expect, source):
    v = verdict.compute(_run(status, *verdicts), outcome)
    assert (v["verdict"], v["verdict_source"]) == (expect, source)
    assert v["process_ok"] is (status == "completed")
    # the final phase only: step "a" (phase 0, verdict fail) never counts
    assert [h["step_id"] for h in v["verdict_detail"]["handoffs"]] == [f"z{i}" for i in range(len(verdicts))]


def test_normalize_outcome_check():
    assert verdict.normalize_outcome_check(None) is None
    assert verdict.normalize_outcome_check("  ") is None
    assert verdict.normalize_outcome_check("pytest -q") == {"command": "pytest -q", "timeout_seconds": 300}
    assert verdict.normalize_outcome_check({"command": "x", "timeout_seconds": 99999})["timeout_seconds"] == 3600
    with pytest.raises(ValueError):
        verdict.normalize_outcome_check(5)


# ── time parsing, pass^k ────────────────────────────────────────────────────

def test_parse_time():
    import time as _t
    now = _t.time() * 1000
    assert abs(summary.parse_time("7d") - (now - 7 * 86400e3)) < 5000
    assert summary.parse_time("1790000000000") == 1790000000000
    assert summary.parse_time("2026-09-25T00:00:00Z") == 1790294400000
    assert summary.parse_time(None, 5) == 5
    with pytest.raises(ValueError):
        summary.parse_time("yesterday")


def test_trigger_passk_counts_verdicts_and_process(tmp_data_root):
    from services.db.core import connect
    from services.run.run_store import get_run_store
    c = connect()
    rs = get_run_store()
    run_pass = rs.create_run(job_id="j", mode="single", source="trigger", steps=[])
    rs.update_run(run_pass["run_id"], {"status": "completed", "verdict": "pass"})
    run_fail = rs.create_run(job_id="j", mode="single", source="trigger", steps=[])
    rs.update_run(run_fail["run_id"], {"status": "completed", "verdict": "fail"})     # process ok, task failed
    fires = [("completed", run_pass["run_id"]), ("completed", run_fail["run_id"]), ("ok", None), ("skipped", None),
             ("failed", None), ("completed", None)]
    for i, (st, rid) in enumerate(fires, start=1):
        c.execute("INSERT INTO trigger_fires (id, trigger_id, seq, source, status, run_id, fired_at) VALUES (?,?,?,?,?,?,?)",
                  (f"f{i}", "T1", i, "schedule", st, rid, f"2026-09-25T00:00:0{i}Z"))
    d = summary.trigger_passk("T1", 3)
    assert [f["seq"] for f in d["fires"]] == [6, 5, 3]            # newest first, skipped excluded
    assert [f["outcome"] for f in d["fires"]] == ["pass", "fail", "pass"]
    assert d["passes"] == 2 and d["pass_k"] == 0 and d["p_hat"] == round(2 / 3, 4)
    d5 = summary.trigger_passk("T1", 5)
    assert [f["outcome"] for f in d5["fires"]] == ["pass", "fail", "pass", "fail", "pass"]
    assert d5["fires"][1]["basis"] == "process" and d5["fires"][3]["basis"] == "verdict"
    assert summary.trigger_passk("T1", 10)["pass_k"] is None       # fewer than k decided fires


# ── engine_extras (P4 hook) ─────────────────────────────────────────────────

def test_engine_extras_missing_module_and_injection(tmp_path, monkeypatch):
    from services.engine import EngineRequest, runner
    from services.engine.adapters.base import Launch
    req = EngineRequest(engine="codex", prompt="p", cwd=tmp_path, correlation={"agent_id": "A1"})
    import services
    monkeypatch.setitem(sys.modules, "services.memory", None)       # import → ImportError (P4 not installed)
    monkeypatch.delattr(services, "memory", raising=False)
    assert runner.engine_extras(req) == {}
    mod = types.ModuleType("services.memory")
    seen = {}

    def engine_extras(agent_id, engine, workspace):
        seen.update(agent_id=agent_id, engine=engine, workspace=workspace)
        return {"args": ["-c", "x=1"], "env": {"MEM": "on"}, "add_dirs": [str(tmp_path / "mem")]}
    mod.engine_extras = engine_extras
    monkeypatch.setitem(sys.modules, "services.memory", mod)
    monkeypatch.setattr(services, "memory", mod, raising=False)
    ex = runner.engine_extras(req)
    assert seen == {"agent_id": "A1", "engine": "codex", "workspace": tmp_path}
    launch = Launch(argv=["codex", "exec", "--json", "-"], stdin="", extras_at=3)
    runner._apply_extras(launch, ex)
    assert launch.argv == ["codex", "exec", "--json", "-c", "x=1", "-"] and launch.env["MEM"] == "on"
    assert runner.engine_extras(EngineRequest(engine="codex", prompt="p", cwd=tmp_path)) == {}  # no agent → none

    def boom(*a):
        raise RuntimeError("nope")
    mod.engine_extras = boom
    assert runner.engine_extras(req) == {}
