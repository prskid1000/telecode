"""Auto-discover and register all prompt modules in this package.

To add a new prompt: create a .py file in this directory with functions
decorated with @mcp_app.prompt(). No other files need to change.
"""
from __future__ import annotations

import importlib
import pkgutil
import os

_pkg_dir = os.path.dirname(__file__)
for _, name, _ in pkgutil.iter_modules([_pkg_dir]):
    importlib.import_module(f".{name}", __name__)
