"""Server side of open-pencil's automation bridge, hosted by the proxy.

open-pencil drives a live editor over a small WebSocket protocol (upstream:
`packages/mcp/src/browser-rpc.ts` on the server, `src/app/automation/bridge/
server.ts` in the page). Upstream runs that server inside a Node MCP process;
TeleDesign runs it here instead, so an agent's canvas tool call executes in the
very editor the user is watching and no Node process exists at runtime.

Protocol, exactly as the page speaks it (all messages JSON objects):

    server → page   {"type": "register", "token": null}       on connect (never the token)
    page → server   {"type": "register", "token": "<t>"}      becomes the page for its project
    server → page   {"type": "request", "id": "<uuid>", "command": "tool",
                     "args": {"name": "<tool>", "args": {...}, "document_id"?, "page_id"?}}
    page → server   {"type": "response", "id": "<uuid>", "ok": true, "result": ..., "target": {...}}
                    {"type": "response", "id": "<uuid>", "ok": false, "error": "..."}

Other commands the page handles: list_documents, save_file, eval, export,
export_jsx, selection (and any core RPC name as a fallback). Upstream also lets
a second client `auth` and forward `request`s through the socket; that is kept
for parity. One page per project: a newer registration replaces the older one
and fails its in-flight requests ("Browser reconnected"), as upstream does.

Page endpoint: `GET /api/design/editor-bridge?project=<pid>` (see
proxy/api_design_editor.py). The token is per process and reaches the page only
inside the editor's index.html, which a cross-origin page cannot read; the
WebSocket handshake is additionally refused for a foreign Origin, because a
WebSocket is not covered by CORS and a generated preview page must never be
able to impersonate the editor.

Python API:
    await call(pid, tool, args=None, timeout=20)   → {"ok": True, "result": …, "target": …}
    await call_mcp(pid, tool, args)                → MCP-shaped {"content": [...], "isError"?}
    call_threadsafe(pid, tool, args, timeout)      → same as call(), from any thread
    status(pid) / tools() / tool_names()
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import re
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from aiohttp import WSMsgType, web

import config
from services.design import store

logger = logging.getLogger("telecode.services.design.editor_bridge")

TOOLS_JSON = Path(__file__).parent / "editor_tools.json"

RPC_TIMEOUT = 20.0          # upstream RPC_TIMEOUT
APP_WAIT_TIMEOUT = 10.0     # upstream APP_WAIT_TIMEOUT
MAX_MESSAGE_BYTES = 96 * 1024 * 1024   # export_image base64 of a large board
MAX_RESULT_BYTES = 900_000  # upstream MAX_RESULT_BYTES for MCP-shaped results

APP_NOT_CONNECTED = (
    "The TeleDesign canvas is not open for this project. Open the project in TeleDesign "
    "(the editor page registers itself on load) and try again."
)

# Commands the page understands besides "tool" (bridge/handlers.ts).
RAW_COMMANDS = ("list_documents", "save_file", "eval", "export", "export_jsx", "selection")

# Tool results that `path` writes to a file instead of returning (mcp/tool/output.ts).
_PATH_OUTPUTS = {"export_svg": "svg", "export_image": "base64", "get_jsx": "jsx"}

_TOKEN = secrets.token_hex(32)


class EditorBridgeError(RuntimeError):
    pass


class _Page:
    __slots__ = ("ws", "pid", "registered_at", "remote")

    def __init__(self, ws: web.WebSocketResponse, pid: str, remote: str):
        self.ws = ws
        self.pid = pid
        self.registered_at = time.time()
        self.remote = remote


_pages: Dict[str, _Page] = {}                              # pid → registered page
_pending: Dict[str, "tuple[str, asyncio.Future]"] = {}     # request id → (pid, future)
_waiters: Dict[str, List[asyncio.Future]] = {}             # pid → futures awaiting a page
_loop: Optional[asyncio.AbstractEventLoop] = None
_tools_cache: Dict[str, Any] = {"mtime": None, "data": None}
_tools_lock = threading.Lock()


def token() -> str:
    """The per-process bridge token the editor page presents in `register`."""
    return _TOKEN


def _authorized(value: Any) -> bool:
    return isinstance(value, str) and hmac.compare_digest(value.encode(), _TOKEN.encode())


# ── Tool descriptors (dumped at build time by tools/build_open_pencil.py) ──

def _tools_data() -> Dict[str, Any]:
    with _tools_lock:
        try:
            mtime = TOOLS_JSON.stat().st_mtime
        except FileNotFoundError:
            return {"tools": [], "count": 0}
        if _tools_cache["mtime"] != mtime:
            try:
                _tools_cache["data"] = json.loads(TOOLS_JSON.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("editor_bridge: unreadable %s: %s", TOOLS_JSON, exc)
                _tools_cache["data"] = {"tools": [], "count": 0}
            _tools_cache["mtime"] = mtime
        return _tools_cache["data"]


def _policy_enabled(desc: Dict[str, Any]) -> bool:
    disabled = config.get_nested("design.editor.disabled_tools", []) or []
    if desc.get("name") in disabled:
        return False
    # Upstream keeps `eval` (arbitrary JS in the editor) off unless opted in.
    if desc.get("availability") == "eval":
        return bool(config.get_nested("design.editor.allow_eval", False))
    return True


def tools(include_schema: bool = True) -> List[Dict[str, Any]]:
    out = []
    for desc in _tools_data().get("tools", []):
        d = dict(desc)
        d["enabled"] = _policy_enabled(desc)
        if not include_schema:
            d.pop("input_schema", None)
            d.pop("command", None)
        out.append(d)
    return out


def tool_names() -> List[str]:
    return [t["name"] for t in tools(False) if t["enabled"]]


def open_pencil_version() -> Optional[str]:
    return _tools_data().get("open_pencil_version")


def _descriptor(name: str) -> Optional[Dict[str, Any]]:
    for desc in _tools_data().get("tools", []):
        if desc.get("name") == name:
            return desc
    return None


# ── Page side: the WebSocket ─────────────────────────────────────────

def _fail_pending(pid: str, reason: str) -> None:
    for rid, (owner, fut) in list(_pending.items()):
        if owner == pid:
            _pending.pop(rid, None)
            if not fut.done():
                fut.set_exception(EditorBridgeError(reason))


def _wake_waiters(pid: str) -> None:
    for fut in _waiters.pop(pid, []):
        if not fut.done():
            fut.set_result(None)


async def _send(ws: web.WebSocketResponse, body: Dict[str, Any]) -> None:
    if not ws.closed:
        await ws.send_str(json.dumps(body))


async def handle_ws(request: web.Request, pid: str) -> web.WebSocketResponse:
    """One editor page. `pid` has already been validated by the route."""
    global _loop
    _loop = asyncio.get_running_loop()
    ws = web.WebSocketResponse(heartbeat=30.0, max_msg_size=MAX_MESSAGE_BYTES)
    await ws.prepare(request)
    remote = request.remote or "?"
    authenticated = False
    # Invite registration without revealing the token (upstream sendRegisterPrompt).
    await _send(ws, {"type": "register", "token": None})
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                if msg.type == WSMsgType.ERROR:
                    logger.warning("editor_bridge: socket error: %s", ws.exception())
                continue
            try:
                data = json.loads(msg.data)
            except Exception:
                logger.warning("editor_bridge: malformed message from %s", remote)
                continue
            if not isinstance(data, dict):
                await ws.close()
                break
            kind = data.get("type")
            if kind == "register":
                if not _authorized(data.get("token")):
                    logger.warning("editor_bridge: rejected registration for %s from %s", pid, remote)
                    await ws.close()
                    break
                authenticated = True
                previous = _pages.get(pid)
                _pages[pid] = _Page(ws, pid, remote)
                if previous and previous.ws is not ws:
                    _fail_pending(pid, "Browser reconnected")
                    await previous.ws.close()
                logger.info("editor_bridge: page registered for project %s", pid)
                _wake_waiters(pid)
                continue
            if kind == "auth":
                if not _authorized(data.get("token")):
                    await ws.close()
                    break
                authenticated = True
                continue
            if not authenticated:
                await ws.close()
                break
            if kind == "response":
                page = _pages.get(pid)
                rid = data.get("id")
                if not page or page.ws is not ws or not isinstance(rid, str):
                    continue
                entry = _pending.pop(rid, None)
                if entry is None:
                    continue
                fut = entry[1]
                body = {k: v for k, v in data.items() if k not in ("type", "id")}
                if fut.done():
                    continue
                if body.get("ok") is False:
                    fut.set_exception(EditorBridgeError(str(body.get("error") or "RPC failed")))
                else:
                    fut.set_result(body)
            elif kind == "request":
                # A second authenticated client forwarding through the page (upstream parity).
                rid = data.get("id")
                if not isinstance(rid, str):
                    continue
                body = {k: v for k, v in data.items() if k not in ("type", "id")}
                asyncio.ensure_future(_forward(ws, pid, rid, body))
    finally:
        page = _pages.get(pid)
        if page and page.ws is ws:
            _pages.pop(pid, None)
            _fail_pending(pid, "Browser disconnected")
            logger.info("editor_bridge: page for project %s disconnected", pid)
    return ws


async def _forward(ws: web.WebSocketResponse, pid: str, rid: str, body: Dict[str, Any]) -> None:
    try:
        result = await request(pid, str(body.get("command") or ""), body.get("args") or {})
        payload = result if isinstance(result, dict) else {"result": result}
        await _send(ws, {**payload, "type": "response", "id": rid, "ok": True})
    except Exception as exc:
        await _send(ws, {"type": "response", "id": rid, "ok": False, "error": str(exc)})


# ── Server side: sending requests ────────────────────────────────────

async def _wait_for_page(pid: str, timeout: float) -> _Page:
    page = _pages.get(pid)
    if page and not page.ws.closed:
        return page
    fut = asyncio.get_running_loop().create_future()
    _waiters.setdefault(pid, []).append(fut)
    try:
        await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        raise EditorBridgeError(APP_NOT_CONNECTED) from None
    finally:
        lst = _waiters.get(pid)
        if lst and fut in lst:
            lst.remove(fut)
    page = _pages.get(pid)
    if not page or page.ws.closed:
        raise EditorBridgeError(APP_NOT_CONNECTED)
    return page


async def request(pid: str, command: str, args: Dict[str, Any], *,
                  timeout: float = RPC_TIMEOUT, wait: float = APP_WAIT_TIMEOUT) -> Dict[str, Any]:
    """Send one raw bridge request to the project's page and await its response body."""
    if not store.valid_id(pid):
        raise EditorBridgeError("Invalid project id")
    if not command:
        raise EditorBridgeError("Missing command")
    page = await _wait_for_page(pid, wait)
    rid = str(uuid.uuid4())
    fut = asyncio.get_running_loop().create_future()
    _pending[rid] = (pid, fut)
    try:
        await _send(page.ws, {"command": command, "args": args, "type": "request", "id": rid})
        return await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        raise EditorBridgeError(f"RPC timeout ({round(timeout)}s)") from None
    finally:
        _pending.pop(rid, None)


def _write_output(pid: str, tool: str, rel: str, result: Any) -> Optional[Dict[str, Any]]:
    """`path` on export_svg / export_image / get_jsx writes into the project folder."""
    field = _PATH_OUTPUTS.get(tool)
    if not field or not isinstance(result, dict) or not isinstance(result.get(field), str):
        return None
    if not store.safe_relpath(rel):
        raise EditorBridgeError(f"Path must be project-relative: {rel}")
    pdir = store.project_dir(pid)
    if not pdir:
        raise EditorBridgeError("Project not found")
    target = pdir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    data = base64.b64decode(result[field]) if field == "base64" else result[field].encode("utf-8")
    target.write_bytes(data)
    return {"written": rel, "byteLength": len(data)}


# ── HTML boards (boards.json keyed by a board key stamped on the frame) ──

BOARD_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def new_board_key() -> str:
    return "b" + secrets.token_hex(8)


async def register_board(pid: str, node_id: str, src: str, width: Any = None, height: Any = None,
                         key: Optional[str] = None, timeout: float = RPC_TIMEOUT) -> Dict[str, Any]:
    """Make frame `node_id` an HTML board rendering `src`; returns {key, node_id, src, width, height}.

    Node ids are session-local in the editor (a .fig reload renumbers them), so
    boards.json is keyed by a board key the editor stamps on the frame as plugin
    data. The registry is written first — marking makes the page re-read it — and
    rolled back if the mark fails.
    """
    if not isinstance(node_id, str) or not node_id or len(node_id) > 64:
        raise EditorBridgeError("node_id required")
    if not isinstance(src, str) or not store.safe_relpath(src) or not src.endswith(".html"):
        raise EditorBridgeError("src must be a project-relative .html path")
    key = key or new_board_key()
    if not isinstance(key, str) or not BOARD_KEY_RE.match(key):
        raise EditorBridgeError("Invalid board key")
    boards = dict(store.get_boards(pid) or {})
    previous = boards.get(key)
    boards[key] = {"src": src, "width": width, "height": height}
    if not store.save_boards(pid, boards):
        raise EditorBridgeError("Invalid board")
    try:
        body = await call(pid, "telecode_board_mark", {"node_id": node_id, "key": key}, timeout)
    except Exception:
        if previous is None:
            boards.pop(key, None)
        else:
            boards[key] = previous
        store.save_boards(pid, boards)
        raise
    result = body.get("result") or {}
    return {"key": key, "node_id": result.get("node_id", node_id), **boards[key]}


async def unregister_board(pid: str, key: str, timeout: float = RPC_TIMEOUT) -> Dict[str, Any]:
    """Drop board `key` from boards.json and, when the editor is open, from its frame."""
    if not isinstance(key, str) or not BOARD_KEY_RE.match(key):
        raise EditorBridgeError("Invalid board key")
    boards = dict(store.get_boards(pid) or {})
    existed = boards.pop(key, None) is not None
    store.save_boards(pid, boards)
    unmarked = False
    if status(pid)["connected"]:
        try:
            body = await request(pid, "telecode_board_unmark", {"key": key}, timeout=timeout)
            unmarked = bool((body.get("result") or {}).get("removed"))
        except EditorBridgeError as exc:
            logger.info("editor_bridge: unmark %s failed: %s", key, exc)
    return {"removed": existed, "unmarked": unmarked}


async def call(pid: str, tool: str, args: Optional[Dict[str, Any]] = None,
               timeout: float = RPC_TIMEOUT) -> Dict[str, Any]:
    """Run one open-pencil tool in the project's live editor.

    Returns the page's response body: {"ok": True, "result": …, "target": {…}}.
    Raises EditorBridgeError when the tool is unknown/disabled, no editor page is
    open for the project, the call times out, or the tool itself fails.
    """
    args = dict(args or {})
    desc = _descriptor(tool)
    if desc is None and tool not in RAW_COMMANDS:
        raise EditorBridgeError(f"Unknown tool: {tool}")
    if desc is not None and not _policy_enabled(desc):
        raise EditorBridgeError(f"Tool disabled by settings (design.editor): {tool}")
    command = (desc or {}).get("command") or tool
    # The board tools also own boards.json: with `src`, mark = register.
    if tool == "telecode_board_mark" and "src" in args:
        board = await register_board(pid, args.get("node_id"), args.get("src"), args.get("width"),
                                     args.get("height"), args.get("key"), timeout)
        return {"ok": True, "result": board}
    if tool == "telecode_board_unmark":
        return {"ok": True, "result": await unregister_board(pid, args.get("key"), timeout)}
    if command == "local":
        if tool == "get_codegen_prompt":
            return {"ok": True, "result": {"prompt": _tools_data().get("codegen_prompt", "")}}
        raise EditorBridgeError(f"Unknown local tool: {tool}")
    target = {k: args.pop(k) for k in ("document_id", "page_id") if isinstance(args.get(k), str)}
    if command == "tool":
        body = await request(pid, "tool", {**target, "name": tool, "args": args}, timeout=timeout)
    else:
        body = await request(pid, command, {**target, **args}, timeout=timeout)
    rel = args.get("path")
    if isinstance(rel, str) and rel:
        written = _write_output(pid, tool, rel, body.get("result"))
        if written:
            body = {**body, "result": written}
    return body


async def call_mcp(pid: str, tool: str, args: Optional[Dict[str, Any]] = None,
                   timeout: float = RPC_TIMEOUT) -> Dict[str, Any]:
    """Same as call(), shaped like the MCP result upstream's server returns."""
    try:
        body = await call(pid, tool, args, timeout)
    except Exception as exc:
        return {"content": [{"type": "text", "text": json.dumps({"error": str(exc)})}], "isError": True}
    result = body.get("result")
    if isinstance(result, dict) and "base64" in result and "mimeType" in result:
        data = str(result["base64"])
        if len(data) > MAX_RESULT_BYTES:
            return {"content": [{"type": "text", "text": json.dumps({"error": (
                f'Image from "{tool}" is too large ({len(data) // 1024}KB, limit '
                f"{MAX_RESULT_BYTES // 1024}KB). Export a smaller region or lower the scale.")})}],
                "isError": True}
        return {"content": [{"type": "image", "data": data, "mimeType": result["mimeType"]}]}
    text = json.dumps(result if result is not None else {}, indent=2, ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_RESULT_BYTES:
        return {"content": [{"type": "text", "text": json.dumps({"error": (
            f'Result from "{tool}" is too large. Narrow the request with depth/root_id/node_types, '
            "get_node, or find_nodes.")})}], "isError": True}
    return {"content": [{"type": "text", "text": text}]}


def call_threadsafe(pid: str, tool: str, args: Optional[Dict[str, Any]] = None,
                    timeout: float = RPC_TIMEOUT) -> Dict[str, Any]:
    """call() from a thread other than the proxy's event loop (blocking)."""
    loop = _loop
    if loop is None or loop.is_closed():
        raise EditorBridgeError(APP_NOT_CONNECTED)
    fut = asyncio.run_coroutine_threadsafe(call(pid, tool, args, timeout), loop)
    return fut.result(timeout + APP_WAIT_TIMEOUT + 5)


def status(pid: str) -> Dict[str, Any]:
    page = _pages.get(pid)
    connected = bool(page and not page.ws.closed)
    return {
        "connected": connected,
        "registered_at": page.registered_at if connected else None,
        "pending": sum(1 for owner, _ in _pending.values() if owner == pid),
        "tools": len(tool_names()),
        "open_pencil_version": open_pencil_version(),
    }


def connected_projects() -> List[str]:
    return [pid for pid, page in _pages.items() if not page.ws.closed]
