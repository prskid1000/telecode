"""Auto-discover and register all tool modules in this package.

To add a new tool: create a .py file in this directory with functions
decorated with @mcp_app.tool(). No other files need to change.
"""
from __future__ import annotations

import importlib
import pkgutil
import os

_pkg_dir = os.path.dirname(__file__)
for _, name, _ in pkgutil.iter_modules([_pkg_dir]):
    try:
        from proxy.runtime_state import is_mcp_tool_enabled
        if not is_mcp_tool_enabled(name):
            continue
    except ImportError:
        pass
    importlib.import_module(f".{name}", __name__)
