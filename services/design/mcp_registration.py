"""One-click registration of telecode's MCP server with external coding CLIs.

Each client is registered through **its own** `mcp add` command, never by editing
its config file: the CLI owns its file format and merge rules, so nothing unrelated
can be clobbered. Before adding, the current entry is read back with the client's
own `mcp get` / `mcp list`:

- already registered with our URL → no-op (`already: true`)
- registered under our name with a *different* URL or command → refused as a
  conflict unless `force` (then removed and re-added — still only our key)
- absent → added

`dry_run` returns the exact argv that would run, without running it (tests use this;
it never touches the user's real config).

Supported clients (binary via `shutil.which`):

| client        | binary   | add                                                           |
|---------------|----------|---------------------------------------------------------------|
| `claude_code` | `claude` | `claude mcp add --transport http --scope user telecode <url>`  |
| `codex`       | `codex`  | `codex mcp add telecode --url <url>`                           |
| `gemini`      | `gemini` | `gemini mcp add --transport http --scope user telecode <url>`  |
| `antigravity` | `agy`    | `agy mcp add --type http telecode <url>`                       |

The URL is `http://127.0.0.1:<mcp_server.port>/mcp`, read from settings each call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger("telecode.services.design.mcp_registration")

CLIENTS: Dict[str, Dict[str, Any]] = {
    "claude_code": {"label": "Claude Code", "binaries": ["claude"]},
    "codex":       {"label": "Codex CLI", "binaries": ["codex"]},
    "gemini":      {"label": "Gemini CLI", "binaries": ["gemini"]},
    "antigravity": {"label": "Antigravity", "binaries": ["agy", "antigravity"]},
}

_TIMEOUT = 45.0
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def server_name() -> str:
    name = str(config.get_nested("design.mcp_name", "telecode") or "telecode")
    return name if _NAME_RE.match(name) else "telecode"


def server_url() -> str:
    port = int(config.get_nested("mcp_server.port", 1236))
    return f"http://127.0.0.1:{port}/mcp"


def find_binary(client: str) -> Optional[str]:
    for b in CLIENTS[client]["binaries"]:
        path = shutil.which(b)
        if path:
            return path
    return None


def add_argv(client: str, binary: str, name: str, url: str) -> List[str]:
    if client == "claude_code":
        return [binary, "mcp", "add", "--transport", "http", "--scope", "user", name, url]
    if client == "codex":
        return [binary, "mcp", "add", name, "--url", url]
    if client == "gemini":
        return [binary, "mcp", "add", "--transport", "http", "--scope", "user", name, url]
    if client == "antigravity":
        # agy: flags must come before <name>.
        return [binary, "mcp", "add", "--type", "http", name, url]
    raise ValueError(client)


def remove_argv(client: str, binary: str, name: str) -> List[str]:
    if client in ("claude_code", "gemini"):
        return [binary, "mcp", "remove", "--scope", "user", name]
    return [binary, "mcp", "remove", name]


def probe_argv(client: str, binary: str, name: str) -> List[str]:
    if client == "claude_code":
        return [binary, "mcp", "get", name]
    if client == "codex":
        return [binary, "mcp", "get", name, "--json"]
    return [binary, "mcp", "list"]


def _run(argv: List[str], timeout: float = _TIMEOUT) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
                           cwd=str(Path.home()), **kwargs)
        return {"code": p.returncode, "stdout": p.stdout or "", "stderr": p.stderr or ""}
    except subprocess.TimeoutExpired:
        return {"code": -1, "stdout": "", "stderr": f"timed out after {timeout:.0f}s"}
    except OSError as e:
        return {"code": -1, "stdout": "", "stderr": str(e)}


_URL_RE = re.compile(r"https?://[^\s\"'),]+")


def parse_probe(client: str, name: str, res: Dict[str, Any]) -> Dict[str, Any]:
    """Interpret a probe's output → {registered, url, detail}."""
    out = (res.get("stdout") or "") + "\n" + (res.get("stderr") or "")
    if client == "claude_code":
        if res.get("code") != 0 or "No MCP server named" in out:
            return {"registered": False, "url": None}
        m = re.search(r"^\s*URL:\s*(\S+)", out, re.M)
        cmd = re.search(r"^\s*Command:\s*(.+)$", out, re.M)
        return {"registered": True, "url": m.group(1) if m else None,
                "detail": None if m else (cmd.group(1).strip() if cmd else "non-http entry")}
    if client == "codex":
        if res.get("code") != 0:
            return {"registered": False, "url": None}
        try:
            obj = json.loads(res.get("stdout") or "{}")
            transport = obj.get("transport") or {}
            url = transport.get("url") or obj.get("url")
            return {"registered": True, "url": url,
                    "detail": None if url else "non-http entry"}
        except ValueError:
            m = _URL_RE.search(out)
            return {"registered": True, "url": m.group(0) if m else None}
    # gemini / antigravity: scan `mcp list` for a line naming our server.
    for line in out.splitlines():
        if re.search(rf"(^|[\s:*✓✗•-]){re.escape(name)}([\s:(]|$)", line):
            m = _URL_RE.search(line)
            return {"registered": True, "url": m.group(0) if m else None,
                    "detail": None if m else line.strip()[:200]}
    return {"registered": False, "url": None}


def _norm(url: Optional[str]) -> str:
    return (url or "").rstrip("/").replace("://localhost:", "://127.0.0.1:")


def client_status(client: str) -> Dict[str, Any]:
    info = CLIENTS[client]
    binary = find_binary(client)
    rec: Dict[str, Any] = {"client": client, "label": info["label"], "binary": binary,
                           "available": bool(binary), "registered": False, "url": None,
                           "matches": False}
    if not binary:
        return rec
    name = server_name()
    probe = parse_probe(client, name, _run(probe_argv(client, binary, name), timeout=20))
    rec.update(probe)
    rec["matches"] = bool(probe.get("registered")) and _norm(probe.get("url")) == _norm(server_url())
    return rec


async def status() -> Dict[str, Any]:
    results = await asyncio.gather(*(asyncio.to_thread(client_status, c) for c in CLIENTS))
    return {
        "server": {"name": server_name(), "url": server_url(),
                   "enabled": bool(config.get_nested("mcp_server.enabled", False))},
        "clients": list(results),
    }


def register_sync(client: str, *, dry_run: bool = False, force: bool = False) -> Dict[str, Any]:
    if client not in CLIENTS:
        return {"ok": False, "client": client, "error": f"unknown client; one of {', '.join(CLIENTS)}"}
    binary = find_binary(client)
    name, url = server_name(), server_url()
    base = {"client": client, "label": CLIENTS[client]["label"], "name": name, "url": url,
            "binary": binary}
    if not binary:
        return {**base, "ok": False, "error": f"{CLIENTS[client]['binaries'][0]} not found on PATH"}
    argv = add_argv(client, binary, name, url)
    current = parse_probe(client, name, _run(probe_argv(client, binary, name), timeout=20))
    base["existing"] = current
    steps: List[List[str]] = []
    if current.get("registered"):
        if _norm(current.get("url")) == _norm(url):
            return {**base, "ok": True, "already": True, "dry_run": dry_run, "commands": []}
        if not force:
            return {**base, "ok": False, "conflict": True,
                    "error": f"an MCP server named '{name}' is already configured "
                             f"({current.get('url') or current.get('detail') or 'different entry'}); "
                             f"pass force=true to replace it"}
        steps.append(remove_argv(client, binary, name))
    steps.append(argv)
    if dry_run:
        return {**base, "ok": True, "dry_run": True, "commands": steps}
    outputs = []
    for step in steps:
        res = _run(step)
        outputs.append({"argv": step, **res})
        if res["code"] != 0:
            logger.warning("mcp register %s: %s failed: %s", client, step[1:4], res["stderr"][:300])
            return {**base, "ok": False, "commands": steps, "outputs": outputs,
                    "error": (res["stderr"] or res["stdout"]).strip()[:500] or "command failed"}
    after = client_status(client)
    return {**base, "ok": bool(after.get("matches")), "commands": steps, "outputs": outputs,
            "status": after,
            **({} if after.get("matches") else {"error": "added, but the entry did not read back"})}


async def register(client: str, *, dry_run: bool = False, force: bool = False) -> Dict[str, Any]:
    return await asyncio.to_thread(register_sync, client, dry_run=dry_run, force=force)
