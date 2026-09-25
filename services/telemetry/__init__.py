"""P5 observability: own GenAI spans, the OTLP receiver's storage, dashboards,
run verdicts.

* :mod:`.settings`  — ``telemetry.*`` / ``safety.*`` accessors (hot-reload)
* :mod:`.store`     — ``spans`` / ``metric_points`` / ``log_events`` rows + retention
* :mod:`.otlp`      — OTLP/JSON (and decoded protobuf, :mod:`.protobuf`) → rows
* :mod:`.spans`     — ``invoke_workflow`` / ``invoke_agent`` / ``execute_tool``
* :mod:`.summary`   — dashboard queries, run timeline, trigger pass^k
* :mod:`.verdict`   — run verdict (pass | fail | unknown) + job outcome checks

HTTP: ``proxy/api_telemetry.py`` (``/otlp/v1/*`` on loopback only, ``/api/telemetry/*``).
"""
