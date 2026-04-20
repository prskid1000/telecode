"""llama.cpp config + argv builder.

The subprocess supervisor used to live here too but was consolidated
into the top-level `process.py` alongside port-sweep, Job Object, and
tree-kill primitives. Import the supervisor from there:

    from process import get_supervisor, shutdown_supervisor, LlamaSupervisor

This package now owns only the static description of what to spawn:
config accessors (`config.py`), argv builder (`argv.py`), and the
last-active-model state file (`state.py`).
"""
