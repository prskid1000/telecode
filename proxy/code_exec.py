"""Sandboxed Python executor for the `code_execution` managed tool.

Runs user-supplied Python in a subprocess with timeout, captures stdout/stderr,
returns a structured result matching Anthropic's code_execution schema.

No client-tool bridging — pure computation. Scoped for data analysis,
parsing, math; not for Excel I/O (model calls set_cell_range etc. directly).
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
from typing import Any

log = logging.getLogger("telecode.proxy.code_exec")


CODE_EXEC_SCHEMA: dict[str, Any] = {
    "name": "code_execution",
    "description": (
        "Execute Python 3 code in a sandboxed subprocess. Use for data analysis, "
        "calculations, CSV/JSON parsing, text processing, or any computation too "
        "complex for plain reasoning. Libraries available: pandas, numpy, json, "
        "math, statistics, datetime, re, csv, io. No network access. "
        "Returns stdout (use print() to emit results) and stderr on error. "
        "Timeout: 30 seconds. Cannot call other tools — use set_cell_range / "
        "get_cell_ranges etc. directly for spreadsheet I/O."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python 3 source code to execute. Print results to stdout.",
            },
        },
        "required": ["code"],
    },
}


async def run_code(code: str, timeout: int = 30) -> tuple[str, str]:
    """Execute Python code in a subprocess.

    Returns (summary_line, tool_result_content).
    """
    # Write code to a temp file to avoid command-line length limits / escaping
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
            return ("timeout", f"[code_execution] TIMEOUT after {timeout}s")

        stdout = stdout_b.decode("utf-8", errors="replace").strip()
        stderr = stderr_b.decode("utf-8", errors="replace").strip()
        rc = proc.returncode

        if rc != 0:
            summary = f"error (rc={rc})"
            out = f"[code_execution] EXIT CODE {rc}\n"
            if stderr:
                out += f"STDERR:\n{stderr}\n"
            if stdout:
                out += f"STDOUT:\n{stdout}\n"
            return (summary, out.strip())

        summary = f"ok ({len(stdout)} chars stdout)"
        if stderr:
            return (summary, f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}")
        return (summary, stdout or "(no output)")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def handler(args: dict[str, Any]) -> tuple[str, str]:
    """Managed-tool handler signature: (input_dict) -> (summary, result)."""
    code = args.get("code", "")
    if not code:
        return ("no code", "[code_execution] ERROR: empty code")
    log.info("code_execution: running %d chars", len(code))
    return await run_code(code)
