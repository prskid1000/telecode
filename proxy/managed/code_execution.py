"""Managed `code_execution` — sandboxed Python 3 subprocess."""
from __future__ import annotations

from proxy.code_exec import CODE_EXEC_SCHEMA, handler
from proxy.managed_tools import register

register("code_execution", CODE_EXEC_SCHEMA, handler,
         strip=["code_execution"], primary_arg="code")
