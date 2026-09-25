"""P5 settings — read on every call (hot-reload), never cached.

``telemetry.*``
  enabled              bool, default true. The CLIs get OTel env pointing at the
                       proxy's receiver, and telecode writes its own spans.
  retention_days       rows older than this are pruned (default 14).
  metric_interval_ms   OTEL_METRIC_EXPORT_INTERVAL for the CLIs (default 10000 —
                       the SDK default of 60 s outlives most short runs; the CLI
                       flushes on exit either way).
  logs_interval_ms     OTEL_LOGS_EXPORT_INTERVAL (default 2000).
  cli_traces           bool, default false: also ask Claude for its beta traces
                       (CLAUDE_CODE_ENHANCED_TELEMETRY_BETA + OTEL_TRACES_EXPORTER).
  max_body_mb          receiver request cap (default 16).

``safety.*``
  approval_timeout_sec  how long ``approve_tool`` waits for a person before it
                        denies (default 600).
"""

from __future__ import annotations

from typing import Any


def _get(path: str, default: Any) -> Any:
    try:
        import config
        v = config.get_nested(path, default)
    except Exception:
        return default
    return default if v is None or v == "" else v


def _int(path: str, default: int, lo: int = 0) -> int:
    try:
        return max(lo, int(_get(path, default)))
    except (TypeError, ValueError):
        return default


def enabled() -> bool:
    v = _get("telemetry.enabled", True)
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "off")
    return bool(v)


def retention_days() -> int:
    return _int("telemetry.retention_days", 14, lo=1)


def metric_interval_ms() -> int:
    return _int("telemetry.metric_interval_ms", 10000, lo=1000)


def logs_interval_ms() -> int:
    return _int("telemetry.logs_interval_ms", 2000, lo=500)


def cli_traces() -> bool:
    return bool(_get("telemetry.cli_traces", False))


def max_body_bytes() -> int:
    return _int("telemetry.max_body_mb", 16, lo=1) * 1024 * 1024


def proxy_port() -> int:
    try:
        import config
        return int(config.proxy_port())
    except Exception:
        return 1235


def receiver_endpoint() -> str:
    """Base OTLP endpoint the CLIs export to (always loopback — never off-box)."""
    return f"http://127.0.0.1:{proxy_port()}/otlp"


def approval_timeout_sec() -> float:
    try:
        return max(5.0, float(_get("safety.approval_timeout_sec", 600)))
    except (TypeError, ValueError):
        return 600.0


def mcp_enabled() -> bool:
    try:
        import config
        return bool(config.mcp_server_enabled())
    except Exception:
        return False


def mcp_url() -> str:
    try:
        import config
        host = config.mcp_server_host() or "127.0.0.1"
        port = int(config.mcp_server_port())
    except Exception:
        host, port = "127.0.0.1", 1236
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return f"http://{host}:{port}/mcp"
