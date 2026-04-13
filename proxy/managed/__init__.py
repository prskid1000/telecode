"""Auto-discover and register drop-in managed tools.

Each module in this package is imported at startup. A module registers
a managed tool by calling `proxy.managed_tools.register(...)` at import time.
No code changes elsewhere — drop a `.py` file here and it's picked up.
"""
from __future__ import annotations

import importlib
import os
import pkgutil

_pkg_dir = os.path.dirname(__file__)
for _, _name, _ in pkgutil.iter_modules([_pkg_dir]):
    importlib.import_module(f".{_name}", __name__)
