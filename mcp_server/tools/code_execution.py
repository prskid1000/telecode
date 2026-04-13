"""Sandboxed Python 3 execution tool.

Runs user-supplied Python in a subprocess with timeout, captures stdout/stderr.
Exposed via both MCP transport and the proxy's managed-tool intercept loop.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile

from mcp_server.app import mcp_app


# Metadata for the proxy bridge (optional; bridge reads these module attrs)
_primary_arg = "code"
_strip_from_cc = ["code_execution"]


@mcp_app.tool()
async def code_execution(code: str, timeout: int = 30) -> str:
    """Execute Python 3 code in a sandboxed subprocess.

    Use for data analysis, calculations, CSV/JSON parsing, text processing, or
    any computation too complex for plain reasoning. Libraries available:
    pandas, numpy, json, math, statistics, datetime, re, csv, io. No network.
    Print results to stdout. Cannot call other tools.

    Args:
        code: Python 3 source code to execute. Use print() to emit results.
        timeout: Max execution time in seconds (default 30).

    Returns:
        Stdout on success, or an error report (exit code + stderr) on failure.
    """
    if not code:
        return "[code_execution] ERROR: empty code"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        path = f.name

    try:
        if sys.platform == "win32":
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-u", path,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-u", path,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"[code_execution] TIMEOUT after {timeout}s"

        stdout = stdout_b.decode("utf-8", errors="replace").strip()
        stderr = stderr_b.decode("utf-8", errors="replace").strip()
        rc = proc.returncode

        if rc != 0:
            out = f"[code_execution] EXIT CODE {rc}\n"
            if stderr:
                out += f"STDERR:\n{stderr}\n"
            if stdout:
                out += f"STDOUT:\n{stdout}\n"
            return out.strip()

        if stderr:
            return f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
        return stdout or "(no output)"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
