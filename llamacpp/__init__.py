"""llama.cpp subprocess supervisor + argv builder.

Owns the llama-server process lifecycle for the proxy:
  - spawn + babysit a single llama-server instance
  - probe /health until ready
  - swap models on demand (restart with new argv)
  - tear down on shutdown

All configuration comes from settings.json under `llamacpp.*`. See
`config.py` accessors in this package (`llamacpp/config.py`) for the
full shape.
"""
from __future__ import annotations

from llamacpp.supervisor import LlamaSupervisor, get_supervisor, shutdown_supervisor

__all__ = ["LlamaSupervisor", "get_supervisor", "shutdown_supervisor"]
