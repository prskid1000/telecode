"""One-click registration of telecode's MCP server with external coding CLIs and apps.

A client with an `mcp add` command is registered through **that command**, never by
editing its config file: the CLI owns its file format and merge rules, so nothing
unrelated can be clobbered. Before adding, the current entry is read back with the
client's own `mcp get` / `mcp list`:

- already registered with our URL → no-op (`already: true`)
- registered under our name with a *different* URL or command → refused as a
  conflict unless `force` (then removed and re-added — still only our key)
- absent → added

Clients without a non-interactive `mcp add` (OpenCode's is a TUI prompt; Kiro and
Claude Desktop have none) are registered by a JSON edit of their config file under
the same rules: parse (a file that does not parse is refused, never rewritten),
touch only our key under the client's server map, back the original up to
`<file>.telecode-bak` once, write atomically (tmp + replace).

**Nothing is written for a client that is not installed.** Installed means its
binary is found (`shutil.which` + the app's known install paths); a leftover config
folder is not enough — `~/.gemini` exists wherever Antigravity is installed, and
`~/.config/opencode` outlives an uninstall.

`dry_run` returns the exact argv / file write that would happen, without doing it
(tests use this; it never touches the user's real config).

| client           | detected by                                   | registered with                                             |
|------------------|-----------------------------------------------|-------------------------------------------------------------|
| `claude_code`    | `claude`                                      | `claude mcp add --transport http --scope user telecode <url>` |
| `codex`          | `codex`                                       | `codex mcp add telecode --url <url>`                        |
| `antigravity`    | `agy`                                         | `agy mcp add --type http telecode <url>`                    |
| `gemini`         | `gemini`                                      | `gemini mcp add --scope user --transport http telecode <url>` |
| `opencode`       | `opencode` (PATH, `~/.opencode/bin`)          | `~/.config/opencode/opencode.json` → `mcp.telecode = {type: remote, url, enabled}` |
| `kiro`           | `kiro` / Kiro.exe                             | `~/.kiro/settings/mcp.json` → `mcpServers.telecode = {url}` |
| `claude_desktop` | Claude.app / Claude.exe / its MSIX package    | `claude_desktop_config.json` → `mcpServers.telecode = {command: npx, args: [-y, mcp-remote, <url>]}` (Claude Desktop's config file takes stdio servers only, so `mcp-remote` bridges to our HTTP endpoint; needs Node's `npx`) |

The URL is `http://127.0.0.1:<mcp_server.port>/mcp`, read from settings each call.
`server_status()` says whether anything answers there (the tray's "Re-check
connection").
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger("telecode.services.design.mcp_registration")

CLIENTS: Dict[str, Dict[str, Any]] = {
    "claude_code":    {"label": "Claude Code", "binaries": ["claude"], "kind": "cli"},
    "codex":          {"label": "Codex CLI", "binaries": ["codex"], "kind": "cli"},
    "antigravity":    {"label": "Antigravity", "binaries": ["agy", "antigravity"], "kind": "cli"},
    "gemini":         {"label": "Gemini CLI", "binaries": ["gemini"], "kind": "cli"},
    "opencode":       {"label": "OpenCode", "binaries": ["opencode"], "kind": "file"},
    "kiro":           {"label": "Kiro", "binaries": ["kiro"], "kind": "file"},
    "claude_desktop": {"label": "Claude Desktop", "binaries": [], "kind": "file"},
}

_TIMEOUT = 45.0
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def server_name() -> str:
    name = str(config.get_nested("design.mcp_name", "telecode") or "telecode")
    return name if _NAME_RE.match(name) else "telecode"


def server_url() -> str:
    port = int(config.get_nested("mcp_server.port", 1236))
    return f"http://127.0.0.1:{port}/mcp"


def _home() -> Path:
    return Path.home()


def _env_dir(var: str) -> Optional[Path]:
    v = os.environ.get(var)
    return Path(v) if v else None


def _known_installs(client: str) -> List[Path]:
    """App install locations for clients that are not (only) a PATH binary."""
    home = _home()
    local, appdata = _env_dir("LOCALAPPDATA"), _env_dir("APPDATA")
    out: List[Path] = []
    if client == "opencode":
        out += [home / ".opencode" / "bin" / ("opencode.exe" if sys.platform == "win32" else "opencode")]
        if appdata:
            out.append(appdata / "npm" / "opencode.cmd")
    elif client == "kiro":
        if local:
            out.append(local / "Programs" / "Kiro" / "Kiro.exe")
        out += [Path("/Applications/Kiro.app"), home / "Applications" / "Kiro.app", Path("/usr/share/kiro/kiro")]
    elif client == "claude_desktop":
        if local:
            out.append(local / "AnthropicClaude" / "claude.exe")
            pk = local / "Packages"
            if pk.is_dir():
                out += sorted(pk.glob("Claude_*"))            # the MSIX package's data folder
        out += [Path("/Applications/Claude.app"), home / "Applications" / "Claude.app"]
    return out


def find_binary(client: str) -> Optional[str]:
    for b in CLIENTS[client]["binaries"]:
        path = shutil.which(b)
        if path:
            return path
    for p in _known_installs(client):
        if p.exists():
            return str(p)
    return None


def add_argv(client: str, binary: str, name: str, url: str) -> List[str]:
    if client == "claude_code":
        return [binary, "mcp", "add", "--transport", "http", "--scope", "user", name, url]
    if client == "codex":
        return [binary, "mcp", "add", name, "--url", url]
    if client == "antigravity":
        # agy: flags must come before <name>.
        return [binary, "mcp", "add", "--type", "http", name, url]
    if client == "gemini":
        return [binary, "mcp", "add", "--scope", "user", "--transport", "http", name, url]
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
    # antigravity: scan `mcp list` for a line naming our server.
    for line in out.splitlines():
        if re.search(rf"(^|[\s:*✓✗•-]){re.escape(name)}([\s:(]|$)", line):
            m = _URL_RE.search(line)
            return {"registered": True, "url": m.group(0) if m else None,
                    "detail": None if m else line.strip()[:200]}
    return {"registered": False, "url": None}


def _norm(url: Optional[str]) -> str:
    return (url or "").rstrip("/").replace("://localhost:", "://127.0.0.1:")


# ── config-file clients (OpenCode, Kiro, Claude Desktop) ─────────────────

def config_path(client: str) -> Optional[Path]:
    """The config file we would edit for a file client (it may not exist yet)."""
    home = _home()
    if client == "opencode":
        base = _env_dir("XDG_CONFIG_HOME") or (home / ".config")
        return base / "opencode" / "opencode.json"
    if client == "kiro":
        return home / ".kiro" / "settings" / "mcp.json"
    if client == "claude_desktop":
        if sys.platform == "win32":
            local, appdata = _env_dir("LOCALAPPDATA"), _env_dir("APPDATA")
            # An MSIX install may virtualise %APPDATA%: prefer the package's copy when it exists.
            if local and (local / "Packages").is_dir():
                for pk in sorted((local / "Packages").glob("Claude_*")):
                    f = pk / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json"
                    if f.is_file():
                        return f
            return (appdata or home / "AppData" / "Roaming") / "Claude" / "claude_desktop_config.json"
        if sys.platform == "darwin":
            return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        return home / ".config" / "Claude" / "claude_desktop_config.json"
    return None


def _container_key(client: str) -> str:
    return "mcp" if client == "opencode" else "mcpServers"


def file_entry(client: str, url: str) -> Dict[str, Any]:
    if client == "opencode":
        return {"type": "remote", "url": url, "enabled": True}
    if client == "kiro":
        return {"url": url}
    if client == "claude_desktop":
        # Claude Desktop's config file only launches stdio servers.
        return {"command": "npx", "args": ["-y", "mcp-remote", url]}
    raise ValueError(client)


def _entry_url(entry: Any) -> Optional[str]:
    if not isinstance(entry, dict):
        return None
    for k in ("url", "serverUrl", "httpUrl"):
        if isinstance(entry.get(k), str):
            return entry[k]
    for a in entry.get("args") or []:
        if isinstance(a, str) and _URL_RE.match(a):
            return a
    return None


def _read_config(path: Path) -> Dict[str, Any]:
    """-> {"exists", "data"}; raises ValueError when the file is not plain JSON we can round-trip."""
    if not path.is_file():
        return {"exists": False, "data": {}}
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        return {"exists": True, "data": {}}
    data = json.loads(text)          # JSONDecodeError is a ValueError: refuse, never rewrite
    if not isinstance(data, dict):
        raise ValueError("top level is not a JSON object")
    return {"exists": True, "data": data}


def probe_file(client: str, name: str) -> Dict[str, Any]:
    path = config_path(client)
    if path is None:
        return {"registered": False, "url": None}
    try:
        cfg = _read_config(path)
    except ValueError as e:
        return {"registered": False, "url": None, "config": str(path), "error": f"unreadable config: {e}"}
    servers = cfg["data"].get(_container_key(client))
    entry = servers.get(name) if isinstance(servers, dict) else None
    if entry is None:
        return {"registered": False, "url": None, "config": str(path)}
    url = _entry_url(entry)
    return {"registered": True, "url": url, "config": str(path),
            "detail": None if url else json.dumps(entry)[:200]}


def _write_config(path: Path, data: Dict[str, Any]) -> Optional[str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.is_file():
        bak = path.with_name(path.name + ".telecode-bak")
        if not bak.exists():
            bak.write_bytes(path.read_bytes())
            backup = str(bak)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{int(time.time() * 1000)}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return backup


def _register_file(client: str, base: Dict[str, Any], *, dry_run: bool, force: bool) -> Dict[str, Any]:
    name, url = base["name"], base["url"]
    path = config_path(client)
    if path is None:
        return {**base, "ok": False, "error": "no config location for this platform"}
    if client == "claude_desktop" and not shutil.which("npx"):
        return {**base, "ok": False, "error": "Claude Desktop reaches HTTP MCP servers through `npx mcp-remote`; "
                                              "install Node.js (npx) first"}
    try:
        cfg = _read_config(path)
    except ValueError as e:
        return {**base, "ok": False, "config": str(path),
                "error": f"{path} is not plain JSON ({e}); not rewriting it — add the entry by hand"}
    data = cfg["data"]
    key = _container_key(client)
    servers = data.get(key)
    if servers is not None and not isinstance(servers, dict):
        return {**base, "ok": False, "config": str(path), "error": f"'{key}' in {path} is not an object"}
    current = (servers or {}).get(name)
    entry = file_entry(client, url)
    base = {**base, "config": str(path), "existing": {"registered": current is not None,
                                                      "url": _entry_url(current)}}
    if current is not None:
        if _norm(_entry_url(current)) == _norm(url):
            return {**base, "ok": True, "already": True, "dry_run": dry_run, "writes": []}
        if not force:
            return {**base, "ok": False, "conflict": True,
                    "error": f"an MCP server named '{name}' is already configured in {path} "
                             f"({_entry_url(current) or 'different entry'}); pass force=true to replace it"}
    write = {"path": str(path), "key": f"{key}.{name}", "entry": entry}
    if dry_run:
        return {**base, "ok": True, "dry_run": True, "writes": [write]}
    new = dict(data)
    new[key] = {**(servers or {}), name: entry}
    try:
        backup = _write_config(path, new)
    except OSError as e:
        return {**base, "ok": False, "writes": [write], "error": f"could not write {path}: {e}"}
    after = client_status(client)
    return {**base, "ok": bool(after.get("matches")), "writes": [write], "backup": backup, "status": after,
            **({} if after.get("matches") else {"error": "written, but the entry did not read back"})}


# ── status / register ────────────────────────────────────────────────────

def client_status(client: str) -> Dict[str, Any]:
    info = CLIENTS[client]
    binary = find_binary(client)
    rec: Dict[str, Any] = {"client": client, "label": info["label"], "binary": binary,
                           "kind": info.get("kind", "cli"), "available": bool(binary), "registered": False,
                           "url": None, "matches": False}
    if not binary:
        return rec
    name = server_name()
    if info.get("kind") == "file":
        probe = probe_file(client, name)
    else:
        probe = parse_probe(client, name, _run(probe_argv(client, binary, name), timeout=20))
    rec.update(probe)
    rec["matches"] = bool(probe.get("registered")) and _norm(probe.get("url")) == _norm(server_url())
    return rec


def server_status(timeout: float = 3.0) -> Dict[str, Any]:
    """Is anything answering at the MCP URL? Any HTTP response counts (a bare GET on a
    streamable-HTTP endpoint is answered 4xx); only a refused/timed-out connection is down."""
    url = server_url()
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "text/event-stream"}),
                                    timeout=timeout) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:
        return {"url": url, "reachable": False, "error": str(getattr(e, "reason", e))[:200]}
    return {"url": url, "reachable": True, "http_status": code, "ms": round((time.monotonic() - t0) * 1000)}


async def status(check_server: bool = True) -> Dict[str, Any]:
    results = await asyncio.gather(*(asyncio.to_thread(client_status, c) for c in CLIENTS))
    server = {"name": server_name(), "url": server_url(),
              "enabled": bool(config.get_nested("mcp_server.enabled", False))}
    if check_server:
        server.update(await asyncio.to_thread(server_status))
    return {"server": server, "clients": list(results)}


def register_sync(client: str, *, dry_run: bool = False, force: bool = False) -> Dict[str, Any]:
    if client not in CLIENTS:
        return {"ok": False, "client": client, "error": f"unknown client; one of {', '.join(CLIENTS)}"}
    binary = find_binary(client)
    name, url = server_name(), server_url()
    base = {"client": client, "label": CLIENTS[client]["label"], "name": name, "url": url,
            "binary": binary}
    if not binary:
        # Never write config for a client that is not installed.
        return {**base, "ok": False, "error": f"{CLIENTS[client]['label']} is not installed"}
    if CLIENTS[client].get("kind") == "file":
        return _register_file(client, base, dry_run=dry_run, force=force)
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
