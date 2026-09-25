"""Codex runtime telemetry: ``-c otel.*`` + OTEL_RESOURCE_ATTRIBUTES, and what
the receiver makes of Codex's payloads.

The payload fixtures are trimmed from a real codex-cli 0.157 ``codex exec``
run through the CODEX task handler (2026-09-25): the resource block carried
every ``telecode.*`` id from OTEL_RESOURCE_ATTRIBUTES. Identity values are
replaced with placeholders.
"""

from __future__ import annotations

from pathlib import Path

import pytest

RESOURCE = [
    {"key": "service.namespace", "value": {"stringValue": "telecode"}},
    {"key": "telecode.workspace_id", "value": {"stringValue": "ws-1"}},
    {"key": "telecode.run_id", "value": {"stringValue": "run-probe-codex"}},
    {"key": "service.version", "value": {"stringValue": "0.157.0"}},
    {"key": "env", "value": {"stringValue": "telecode"}},
    {"key": "telecode.source", "value": {"stringValue": "probe"}},
    {"key": "telecode.step_id", "value": {"stringValue": "step-probe"}},
    {"key": "service.name", "value": {"stringValue": "codex_exec"}},
    {"key": "telecode.job_id", "value": {"stringValue": "job-probe"}},
    {"key": "telecode.task_id", "value": {"stringValue": "task-1"}},
    {"key": "telecode.trigger_id", "value": {"stringValue": "trg-probe"}},
]


def _kv(d):
    return [{"key": k, "value": {"stringValue": v}} for k, v in d.items()]


LOGS = {"resourceLogs": [{"resource": {"attributes": RESOURCE}, "scopeLogs": [{"scope": {"name": "codex_otel"},
        "logRecords": [
            {"timeUnixNano": "1790328172078000000", "attributes": _kv({
                "event.name": "codex.conversation_starts", "provider_name": "OpenAI", "approval_policy": "on-request",
                "sandbox_policy": "workspace-write", "conversation.id": "01a0d7df-conv", "model": "gpt-6-astra",
                "originator": "codex_exec", "user.email": "someone@example.com", "user.account_id": "acct-x"})},
            {"timeUnixNano": "1790328172328000000", "attributes": _kv({
                "event.name": "codex.user_prompt", "prompt_length": "177", "prompt": "[REDACTED]",
                "conversation.id": "01a0d7df-conv", "model": "gpt-6-astra"})},
        ]}]}]}

SPANS = {"resourceSpans": [{"resource": {"attributes": RESOURCE}, "scopeSpans": [{"scope": {"name": "codex"},
         "spans": [{"traceId": "AB" * 16, "spanId": "CD" * 8, "name": "stream_request",
                    "startTimeUnixNano": "1790328172000000000", "endTimeUnixNano": "1790328172500000000",
                    "attributes": []}]}]}]}


@pytest.fixture
def port(monkeypatch):
    import config
    monkeypatch.setattr(config, "proxy_port", lambda: 18999)
    return 18999


def test_codex_overrides_traces_off_by_default(port, monkeypatch):
    from services.engine import otel
    from services.telemetry import settings
    monkeypatch.setattr(settings, "cli_traces", lambda: False)
    ov = otel.codex_overrides()
    vals = ov[1::2]
    assert all(a == "-c" for a in ov[0::2])
    assert "otel.exporter.otlp-http.endpoint=http://127.0.0.1:18999/otlp/v1/logs" in vals
    assert "otel.metrics_exporter.otlp-http.endpoint=http://127.0.0.1:18999/otlp/v1/metrics" in vals
    assert "otel.trace_exporter=none" in vals
    assert not any(v.startswith("otel.trace_exporter.otlp-http") for v in vals)
    assert "otel.log_user_prompt=false" in vals
    assert not any('"' in v or "'" in v for v in vals)


def test_codex_overrides_traces_with_cli_traces(port, monkeypatch):
    from services.engine import otel
    from services.telemetry import settings
    monkeypatch.setattr(settings, "cli_traces", lambda: True)
    vals = otel.codex_overrides()[1::2]
    assert "otel.trace_exporter.otlp-http.endpoint=http://127.0.0.1:18999/otlp/v1/traces" in vals
    assert "otel.trace_exporter.otlp-http.protocol=json" in vals
    assert "otel.trace_exporter=none" not in vals


def test_codex_launch_has_otel_config_and_resource_ids(tmp_path, port, monkeypatch):
    from services.engine import EngineRequest
    from services.engine.adapters import get_adapter
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://elsewhere.example")
    corr = {"task_id": "task-1", "run_id": "run 1", "step_id": "s1", "job_id": "j1", "trigger_id": "t1",
            "workspace_id": "ws-1"}
    req = EngineRequest(engine="codex", prompt="p", cwd=tmp_path, correlation=corr, resume_id="SID",
                        permission_mode="auto", last_msg_path=tmp_path / "m.txt")
    launch = get_adapter("codex").build(req)
    a = launch.argv
    # -c pairs are exec-level: after "exec", before "resume"
    i = a.index("otel.exporter.otlp-http.endpoint=http://127.0.0.1:18999/otlp/v1/logs")
    assert a[i - 1] == "-c" and a.index("exec") < i < a.index("resume")
    ra = launch.env["OTEL_RESOURCE_ATTRIBUTES"]
    for part in ("telecode.task_id=task-1", "telecode.run_id=run%201", "telecode.step_id=s1",
                 "telecode.job_id=j1", "telecode.trigger_id=t1", "telecode.workspace_id=ws-1"):
        assert part in ra
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in launch.env       # inherited exporter settings scrubbed
    assert "--approve-for-me" in a


def test_codex_launch_without_correlation_has_no_otel(tmp_path, port):
    from services.engine import EngineRequest
    from services.engine.adapters import get_adapter
    launch = get_adapter("codex").build(EngineRequest(engine="codex", prompt="p", cwd=tmp_path,
                                                      last_msg_path=tmp_path / "m.txt"))
    assert not any(str(x).startswith("otel.") for x in launch.argv) and launch.env is None


def test_receiver_keys_codex_logs_by_resource_ids_and_drops_identity():
    from services.telemetry import otlp
    table, rows = otlp.normalise("logs", LOGS, received_ms=1)
    assert table == "log_events" and len(rows) == 2
    r = rows[0]
    assert (r["task_id"], r["run_id"], r["step_id"], r["job_id"], r["trigger_id"]) == \
        ("task-1", "run-probe-codex", "step-probe", "job-probe", "trg-probe")
    assert r["name"] == "codex.conversation_starts" and r["session_id"] == "01a0d7df-conv"
    assert r["model"] == "gpt-6-astra"
    assert r["attributes"]["sandbox_policy"] == "workspace-write"
    assert r["attributes"]["approval_policy"] == "on-request"
    assert "user.email" not in r["attributes"] and "user.account_id" not in r["attributes"]
    assert rows[1]["attributes"]["prompt"] == "[REDACTED]"


def test_receiver_keys_codex_spans_by_resource_ids():
    from services.telemetry import otlp
    table, rows = otlp.normalise("traces", SPANS, received_ms=1)
    assert table == "spans" and rows[0]["run_id"] == "run-probe-codex" and rows[0]["task_id"] == "task-1"
    assert rows[0]["name"] == "stream_request" and rows[0]["duration_ms"] == 500 and rows[0]["source"] == "otlp"
