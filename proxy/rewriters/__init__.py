"""Drop-in tool_result rewriters.

Any module placed in this package is auto-imported on first load. Each module
should call `proxy.tool_result_rewriters.register(MyRewriter())` at import time
to register itself with the framework. After that, `rewrite_messages()` will
dispatch to it whenever a matching `tool_result` block is seen in the
conversation history.

Adding a new rewriter is a drop-in: create `proxy/rewriters/<name>.py` and
nothing else needs to change.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil

log = logging.getLogger("telecode.proxy.rewriters")


def _autoload() -> None:
    """Import every sibling module so each one's `register()` call fires."""
    for mod in pkgutil.iter_modules(__path__):
        if mod.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{__name__}.{mod.name}")
        except Exception as exc:
            log.warning("Failed to load rewriter %s: %s", mod.name, exc)


_autoload()
