"""TeleDesign tools for MCP clients (and, via proxy/managed_tools.py, for local models).

Every tool talks to the running telecode proxy over HTTP
(`http://127.0.0.1:<proxy.port>/api/design/...`, port read from settings on each call)
rather than importing `services.design`, because this MCP server can run as a separate
process (`python -m mcp_server`). The proxy must be running with TeleDesign loaded.

Also registers two MCP prompts: `design` (start a TeleDesign project from any CLI) and
`design-sync` (turn the current codebase into a TeleDesign design system).
"""
from __future__ import annotations

import base64
import io
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import aiohttp

from mcp_server.app import mcp_app

log = logging.getLogger("telecode.mcp_server.design")

_primary_arg = "project_id"

_TEXT_LIMIT = 200_000
_EXPORT_KINDS = ("html", "zip", "pdf", "pptx", "png", "mp4", "handoff")


# ── HTTP plumbing ────────────────────────────────────────────────────────

def _cfg(path: str, default: Any) -> Any:
    try:
        import config
        return config.get_nested(path, default)
    except Exception:
        return default


def _base() -> str:
    return f"http://127.0.0.1:{int(_cfg('proxy.port', 1235))}"


def _preview_base() -> str:
    return f"http://127.0.0.1:{int(_cfg('design.preview_port', 1237))}"


class _HTTPError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


async def _req(method: str, path: str, *, body: Any = None, params: Optional[dict] = None,
               data: Optional[bytes] = None, content_type: Optional[str] = None,
               raw: bool = False, base: Optional[str] = None, timeout: float = 60.0) -> Any:
    headers = {"Content-Type": content_type} if content_type else {}
    url = (base or _base()) + path
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.request(method, url, json=body, params=params, data=data,
                                 headers=headers) as r:
                payload = await r.read()
                if r.status >= 400:
                    msg = payload.decode("utf-8", "replace")[:400]
                    try:
                        msg = json.loads(msg).get("error", msg)
                    except Exception:
                        pass
                    raise _HTTPError(r.status, str(msg))
                if raw:
                    return payload, r.headers.get("Content-Type", "")
                if not payload:
                    return {}
                try:
                    return json.loads(payload)
                except ValueError:
                    return {"text": payload.decode("utf-8", "replace")}
    except aiohttp.ClientConnectorError:
        raise _HTTPError(503, f"telecode proxy is not reachable at {base or _base()} — "
                              f"is telecode running with proxy.enabled?")


def _err(e: Exception) -> str:
    if isinstance(e, _HTTPError) and e.status in (404, 405, 501) and "not found" not in str(e).lower():
        return f"error: {e} (this TeleDesign feature may not be built yet)"
    return f"error: {e}"


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def _p(project_id: str) -> str:
    pid = (project_id or "").strip().lower()
    if len(pid) != 32 or any(c not in "0123456789abcdef" for c in pid):
        raise ValueError("project_id must be a 32-character hex id (see design_list_projects)")
    return pid


def _rel(path: str) -> str:
    rel = (path or "").strip().replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise ValueError("path must be project-relative (no leading /, no ..)")
    return rel


def _project_url(pid: str, **q: str) -> str:
    extra = "".join(f"&{k}={v}" for k, v in q.items() if v)
    return f"{_base()}/design?project={pid}{extra}"


# ── projects ─────────────────────────────────────────────────────────────

@mcp_app.tool()
async def design_list_projects(query: str = "", include_archived: bool = False,
                               limit: int = 30) -> str:
    """List TeleDesign projects (newest first): id, title, kind, design system, updated time.

    USE to find the project_id every other design_* tool needs. `query` filters titles
    (case-insensitive substring).
    """
    try:
        res = await _req("GET", "/api/design/projects",
                         params={"include_archived": "1"} if include_archived else None)
    except Exception as e:
        return _err(e)
    q = (query or "").lower()
    rows = [{"id": p["id"], "title": p.get("title"), "kind": p.get("kind"),
             "design_system_id": p.get("design_system_id"), "updated_at": p.get("updated_at")}
            for p in res.get("projects", []) if not q or q in (p.get("title") or "").lower()]
    return _dump({"projects": rows[:max(1, min(int(limit or 30), 200))], "total": len(rows)})


@mcp_app.tool()
async def design_get_project(project_id: str) -> str:
    """Get one TeleDesign project: its record, HTML boards, chats and a file list summary.

    Also returns `url` — the TeleDesign page for this project, to give the user.
    """
    try:
        pid = _p(project_id)
        out: dict = {"project": (await _req("GET", f"/api/design/projects/{pid}"))["project"]}
        for key, path in (("boards", "boards"), ("chats", "chats"), ("files", "files")):
            try:
                out[key] = (await _req("GET", f"/api/design/projects/{pid}/{path}")).get(key)
            except _HTTPError:
                out[key] = None
        if isinstance(out.get("files"), list) and len(out["files"]) > 80:
            out["files_truncated"] = len(out["files"])
            out["files"] = out["files"][:80]
        out["url"] = _project_url(pid)
        return _dump(out)
    except Exception as e:
        return _err(e)


@mcp_app.tool()
async def design_create_project(title: str, kind: str = "prototype",
                                design_system_id: str = "", prompt: str = "",
                                engine: str = "") -> str:
    """Create a TeleDesign project. Optionally start the built-in design agent on it.

    Args:
        title: Project title.
        kind: prototype | slides | wireframe | one_pager | animation | landing_page |
              mobile_app | web_app | dashboard_table | design_system | other.
        design_system_id: Attach a design system (see design_list_systems); empty = none.
        prompt: When set, open a chat and send this brief as the first turn, so telecode's
                own agent (Claude Code / Codex / Antigravity) designs it. Leave empty if YOU
                will write the files with design_write_file.
        engine: claude_code | codex | antigravity for that first turn (default from settings).

    Returns the project, the chat/turn when a prompt was sent, and the UI `url`.
    """
    try:
        body: dict = {"title": title, "kind": kind}
        if design_system_id:
            body["design_system_id"] = design_system_id
        proj = (await _req("POST", "/api/design/projects", body=body))["project"]
        out: dict = {"project": proj, "url": _project_url(proj["id"])}
        if prompt.strip():
            out.update(await _send(proj["id"], prompt, "", engine, wait=False, timeout=0))
        return _dump(out)
    except Exception as e:
        return _err(e)


async def _send(pid: str, text: str, chat_id: str, engine: str, *, wait: bool,
                timeout: float) -> dict:
    if not chat_id:
        chat_body: dict = {"title": "From MCP"}
        if engine:
            chat_body["engine"] = engine
        chat_id = (await _req("POST", f"/api/design/projects/{pid}/chats", body=chat_body))["chat"]["id"]
    body: dict = {"text": text}
    if engine:
        body["engine"] = engine
    turn = (await _req("POST", f"/api/design/projects/{pid}/chats/{chat_id}/turns", body=body))["turn"]
    out = {"chat_id": chat_id, "turn": turn}
    if wait:
        deadline = time.monotonic() + timeout
        import asyncio
        while time.monotonic() < deadline:
            turns = (await _req("GET", f"/api/design/projects/{pid}/chats/{chat_id}/turns")).get("turns", [])
            idx = next((i for i, t in enumerate(turns) if t.get("id") == turn.get("id")), None)
            cand = None
            if idx is not None:
                cand = turns[idx] if turns[idx].get("role") != "user" else next(
                    (t for t in turns[idx + 1:] if t.get("role") == "assistant"), None)
            if cand and cand.get("status") in ("done", "failed", "cancelled", "error"):
                out["result"] = {k: cand.get(k) for k in ("id", "status", "text", "changed_files",
                                                          "form", "usage", "error")}
                return out
            await asyncio.sleep(2.0)
        out["result"] = {"status": "still running", "note": "poll with design_get_project / the UI"}
    return out


@mcp_app.tool()
async def design_send_message(project_id: str, text: str, chat_id: str = "", engine: str = "",
                              wait: bool = False, timeout_sec: int = 900) -> str:
    """Send a message to TeleDesign's built-in design agent (a new turn in a project chat).

    The agent edits the project's files and the user watches it live in the TeleDesign tab.
    Leave `chat_id` empty to open a new chat. With `wait=true`, block until the turn ends
    (up to `timeout_sec`) and return its reply, changed files and usage.
    """
    try:
        return _dump(await _send(_p(project_id), text, chat_id, engine, wait=wait,
                                 timeout=max(10, min(int(timeout_sec or 900), 3600))))
    except Exception as e:
        return _err(e)


# ── files ────────────────────────────────────────────────────────────────

@mcp_app.tool()
async def design_list_files(project_id: str, prefix: str = "") -> str:
    """List a project's files (path, size, mtime, kind). `prefix` narrows to a folder."""
    try:
        pid = _p(project_id)
        res = await _req("GET", f"/api/design/projects/{pid}/files",
                         params={"prefix": prefix} if prefix else None)
        return _dump(res)
    except Exception as e:
        return _err(e)


@mcp_app.tool()
async def design_read_file(project_id: str, path: str, offset: int = 0,
                           max_chars: int = 60_000) -> str:
    """Read a text file from a project (HTML, JSX, CSS, JSON, MD…).

    Long files are paged: pass `offset` (characters) to continue. Binary files return
    their size only — use design_image_metadata for images.
    """
    try:
        pid, rel = _p(project_id), _rel(path)
        data, ctype = await _req("GET", f"/api/design/projects/{pid}/files/{rel}", raw=True)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return f"{rel}: binary file ({len(data)} bytes, {ctype or 'unknown type'})"
        offset = max(0, int(offset or 0))
        limit = max(1000, min(int(max_chars or 60_000), _TEXT_LIMIT))
        chunk = text[offset:offset + limit]
        more = offset + limit < len(text)
        tail = f"\n\n[… {len(text) - offset - limit} more chars — call again with offset={offset + limit}]" if more else ""
        return chunk + tail
    except Exception as e:
        return _err(e)


async def _put_file(pid: str, rel: str, data: bytes) -> dict:
    return await _req("PUT", f"/api/design/projects/{pid}/files/{rel}", data=data,
                      content_type="application/octet-stream")


async def _get_assets(pid: str) -> list:
    try:
        return (await _req("GET", f"/api/design/projects/{pid}/assets")).get("assets", [])
    except _HTTPError as e:
        if e.status == 404 and "project" in str(e).lower():
            raise
        return []


async def _put_assets(pid: str, assets: list) -> None:
    await _req("PUT", f"/api/design/projects/{pid}/assets", body={"assets": assets})


def _merge_asset(assets: list, entry: dict) -> list:
    """Register or re-register one asset by path. Re-registering resets review status."""
    out = [a for a in assets if a.get("path") != entry["path"]]
    prev = next((a for a in assets if a.get("path") == entry["path"]), {})
    merged = {**prev, **{k: v for k, v in entry.items() if v not in (None, "")}}
    merged["status"] = "needs-review"
    merged.setdefault("id", prev.get("id") or entry["path"])
    merged.setdefault("name", Path(entry["path"]).stem.replace("-", " ").replace("_", " "))
    out.append(merged)
    return out


@mcp_app.tool()
async def design_write_file(project_id: str, path: str, content: str, asset: bool = False,
                            asset_name: str = "", group: str = "", subtitle: str = "",
                            viewport_width: int = 0, viewport_height: int = 0) -> str:
    """Create or overwrite a text file in a project; the UI refreshes and a version is saved.

    Use descriptive filenames (`checkout-v2.html`, not `index2.html`); for a new take on a
    design, write a v2 copy instead of overwriting the original.

    Args:
        path: Project-relative path, e.g. `landing.html` or `components/Card.jsx`.
        content: Full file text.
        asset: Also register the file as a deliverable in the Review tab (status resets to
               needs-review). HTML files are auto-registered by TeleDesign anyway.
        asset_name / group / subtitle: Review-card label, group (e.g. "Screens", "Type",
               "Colors", "Components") and subtitle.
        viewport_width / viewport_height: Intended viewport for the review card (0 = default).
    """
    try:
        pid, rel = _p(project_id), _rel(path)
        res = await _put_file(pid, rel, content.encode("utf-8"))
        out = {"ok": True, "path": rel, "version": res.get("version"),
               "preview": f"{_preview_base()}/p/{pid}/{rel}"}
        if asset:
            entry: dict = {"path": rel, "name": asset_name, "group": group, "subtitle": subtitle}
            if viewport_width and viewport_height:
                entry["viewport"] = {"width": int(viewport_width), "height": int(viewport_height)}
            await _put_assets(pid, _merge_asset(await _get_assets(pid), entry))
            out["asset"] = "registered (needs-review)"
        return _dump(out)
    except Exception as e:
        return _err(e)


@mcp_app.tool()
async def design_delete_file(project_id: str, path: str) -> str:
    """Delete a file from a project (it stays recoverable from the Versions timeline)."""
    try:
        pid, rel = _p(project_id), _rel(path)
        await _req("DELETE", f"/api/design/projects/{pid}/files/{rel}")
        return _dump({"ok": True, "deleted": rel})
    except Exception as e:
        return _err(e)


async def _file_op(op: str, project_id: str, src: str, dst: str, overwrite: bool) -> str:
    try:
        pid = _p(project_id)
        return _dump(await _req("POST", f"/api/design/projects/{pid}/file-ops",
                                body={"op": op, "from": _rel(src), "to": _rel(dst), "overwrite": overwrite}))
    except Exception as e:
        return _err(e)


@mcp_app.tool()
async def design_copy_file(project_id: str, src: str, dst: str, overwrite: bool = False) -> str:
    """Copy a project file or folder (e.g. `checkout.html` → `checkout-v2.html`, or `screens/` → `screens-v2/`).

    A copy of a registered deliverable is registered too (same name/group/subtitle/viewport,
    status needs-review), so the Review tab shows the new take. `dst` ending in `/` or naming
    an existing folder copies into it. Refuses to overwrite unless `overwrite=true`.
    """
    return await _file_op("copy", project_id, src, dst, overwrite)


@mcp_app.tool()
async def design_move_file(project_id: str, src: str, dst: str, overwrite: bool = False) -> str:
    """Move / rename a project file or folder. Its Review-tab registration (id and status)
    and any HTML board on the canvas that renders it follow it to the new path."""
    return await _file_op("move", project_id, src, dst, overwrite)


@mcp_app.tool()
async def design_browser(url: str = "", project_id: str = "", file: str = "", width: int = 1280,
                         height: int = 800, full_page: bool = False, steps: list[dict] | None = None,
                         save_path: str = ""):
    """Open a web page in TeleDesign's headless browser: a screenshot plus a DOM outline
    (title, headings, landmarks, links, buttons, images, top fonts and colours, text excerpt).

    Use it to look at a reference site, a competitor, or the user's live product before
    designing. `url` must be public http(s): every request the page makes is fetched by
    telecode under its URL guard (no localhost / LAN / metadata addresses; blocked requests
    are listed). Or pass `project_id` + `file` to outline a project page. `steps` run before
    capture like design_screenshot's. With `project_id` + `save_path` the screenshot is
    saved into the project (e.g. `references/acme-home.jpg`) instead of returned.
    """
    from mcp.server.fastmcp import Image
    try:
        body: dict = {"width": width, "height": height, "full_page": full_page}
        if url:
            body["url"] = url
        if project_id:
            body["project_id"] = _p(project_id)
            if file:
                body["file"] = _rel(file)
            if save_path:
                body["save_path"] = _rel(save_path)
        if steps:
            body["steps"] = steps
        res = await _req("POST", "/api/design/browser", body=body, timeout=180)
        shot = res.pop("screenshot_b64", None)
        text = _dump(res)
        if not shot:
            return text
        return [Image(data=base64.b64decode(shot), format="png" if "png" in res.get("mime", "") else "jpeg"), text]
    except Exception as e:
        return _err(e)


@mcp_app.tool()
async def design_copy_starter(project_id: str, kind: str, directory: str = "") -> str:
    """Copy a TeleDesign starter component into the project and return how to load it.

    Starters: design_canvas.jsx, ios_frame.jsx, android_frame.jsx, macos_window.jsx,
    browser_window.jsx, animations.jsx (Stage/Sprite/useTime/Easing/interpolate timeline),
    deck_stage.js (the `<deck-stage>` slide element). `kind` may be given with or without
    its extension; a wrong extension is an error (a .jsx starter must stay .jsx).
    `directory` is the project folder to copy into (default: project root).
    """
    try:
        pid = _p(project_id)
        index = (await _req("GET", "/starters/index.json", base=_preview_base()))
        entries = index if isinstance(index, list) else index.get("starters", [])
        want = (kind or "").strip()
        stem, _, ext = want.rpartition(".") if "." in want else (want, "", "")
        match = None
        for e in entries:
            f = e.get("file", "")
            if want in (e.get("kind"), f) or stem and f.rsplit(".", 1)[0] == stem:
                match = e
                break
        if not match:
            names = ", ".join(e.get("file", "") for e in entries)
            return f"error: unknown starter '{kind}'. Available: {names}"
        fname = match["file"]
        if ext and not fname.endswith("." + ext):
            return f"error: starter '{stem}' is {fname}; it cannot be copied as .{ext}"
        data, _ = await _req("GET", f"/starters/{fname}", base=_preview_base(), raw=True)
        rel = _rel(f"{directory.strip('/')}/{fname}" if directory.strip("/") else fname)
        await _put_file(pid, rel, data)
        load = (f'<script type="text/babel" src="{rel}"></script>' if fname.endswith(".jsx")
                else f'<script src="{rel}"></script>')
        return _dump({"ok": True, "path": rel, "description": match.get("description"),
                      "load_as": load})
    except Exception as e:
        return _err(e)


# ── comments / assets ────────────────────────────────────────────────────

@mcp_app.tool()
async def design_get_comments(project_id: str, status: str = "open", board_id: str = "") -> str:
    """List a project's pinned comments (note, author, anchor element, board/file, status).

    `status`: open | sent | resolved | all. Address them, then call design_resolve_comments.
    """
    try:
        pid = _p(project_id)
        res = await _req("GET", f"/api/design/projects/{pid}/comments")
        items = res.get("comments", res) if isinstance(res, dict) else res
        items = [c for c in (items or []) if (status == "all" or c.get("status") == status)
                 and (not board_id or c.get("board_id") == board_id)]
        return _dump({"comments": items, "count": len(items)})
    except Exception as e:
        return _err(e)


@mcp_app.tool()
async def design_resolve_comments(project_id: str, comment_ids: list[str],
                                  status: str = "resolved") -> str:
    """Mark comments resolved (or `open` to reopen) once you have addressed them."""
    if status not in ("resolved", "open", "sent"):
        return "error: status must be resolved, open or sent"
    try:
        pid = _p(project_id)
        done, failed = [], {}
        for cid in comment_ids or []:
            try:
                await _req("PATCH", f"/api/design/projects/{pid}/comments/{cid}", body={"status": status})
                done.append(cid)
            except Exception as e:
                failed[cid] = str(e)
        return _dump({"updated": done, "failed": failed})
    except Exception as e:
        return _err(e)


@mcp_app.tool()
async def design_register_assets(project_id: str, assets: list[dict] | None = None,
                                 unregister: list[str] | None = None) -> str:
    """Register deliverables in the project's Review tab, or remove them.

    Args:
        assets: [{path, name?, group?, subtitle?, viewport?:{width,height}, board_id?}].
                Re-registering an existing path resets its status to needs-review.
        unregister: asset ids or paths to remove from review (files are kept).
    """
    try:
        pid = _p(project_id)
        current = await _get_assets(pid)
        for a in assets or []:
            if not isinstance(a, dict) or not a.get("path"):
                return "error: each asset needs a path"
            current = _merge_asset(current, {**a, "path": _rel(a["path"])})
        drop = set(unregister or [])
        current = [a for a in current if a.get("id") not in drop and a.get("path") not in drop]
        await _put_assets(pid, current)
        return _dump({"ok": True, "assets": current})
    except Exception as e:
        return _err(e)


# ── preview / render ─────────────────────────────────────────────────────

@mcp_app.tool()
async def design_show(project_id: str, path: str, target: str = "user") -> str:
    """Open a project file in the TeleDesign preview.

    target="user" opens it in the user's main pane (use when a deliverable is ready for them
    to look at); target="agent" opens it in a side tab without taking the user's focus.
    """
    try:
        pid, rel = _p(project_id), _rel(path)
        return _dump(await _req("POST", f"/api/design/projects/{pid}/show",
                                body={"path": rel, "target": target}))
    except Exception as e:
        return _err(e)


@mcp_app.tool()
async def design_screenshot(project_id: str, file: str = "index.html", width: int = 1280,
                            height: int = 800, full_page: bool = False, selector: str = "",
                            steps: list[dict] | None = None, scale: int = 1, hq: bool = False,
                            save_path: str = ""):
    """Screenshot a project page rendered headlessly (capped at 1600 px on the long edge).

    Args:
        file: Project-relative HTML file.
        width / height: Viewport in CSS px.
        full_page: Capture the whole scroll height.
        selector: Capture only this element.
        steps: Up to 100 scripted steps run before capture, e.g.
               [{"code": "document.querySelector('#next').click()", "delay": 400}].
        scale: Device pixel ratio 1–3.
        hq: PNG instead of JPEG.
        save_path: Save into the project at this path instead of returning the image
                   (e.g. `screenshots/home.png`).
    """
    from mcp.server.fastmcp import Image
    try:
        pid, rel = _p(project_id), _rel(file)
        body: dict = {"file": rel, "width": width, "height": height, "full_page": full_page,
                      "scale": scale, "format": "png" if hq else "jpeg"}
        if selector:
            body["selector"] = selector
        if steps:
            body["steps"] = steps
        if save_path:
            body["save_path"] = _rel(save_path)
            return _dump(await _req("POST", f"/api/design/projects/{pid}/screenshot", body=body,
                                    timeout=180))
        data, ctype = await _req("POST", f"/api/design/projects/{pid}/screenshot", body=body,
                                 raw=True, timeout=180)
        return Image(data=data, format="png" if "png" in ctype else "jpeg")
    except Exception as e:
        return _err(e)


@mcp_app.tool()
async def design_eval_js(project_id: str, code: str, file: str = "index.html") -> str:
    """Run JavaScript in a project page (headless render) and return the JSON-able result.

    `code` is an expression (promises are awaited), e.g.
    `document.querySelectorAll('h1').length`; statement-style code that uses `return`
    is also accepted, e.g. `const h = [...document.querySelectorAll('h1')]; return h.map(x => x.textContent)`.
    """
    try:
        pid, rel = _p(project_id), _rel(file)
        return _dump(await _req("POST", f"/api/design/projects/{pid}/eval",
                                body={"file": rel, "code": code}, timeout=120))
    except Exception as e:
        return _err(e)


@mcp_app.tool()
async def design_get_console(project_id: str, file: str = "index.html", source: str = "auto",
                             level: str = "", since: int = 0) -> str:
    """Console output of a project page.

    source:
      live     — what the page logged in the USER's open TeleDesign preview (their clicks,
                 tweaks and slide changes included), relayed by the web UI. `level`
                 = error | warn filters; `since` = a `seq` from a previous call returns only
                 newer lines. Empty `file` = every open file.
      headless — reload the page headless and return its load-time errors (empty = clean).
      auto     — live when the UI has reported lines for this file, plus the headless errors.
    Check this after writing a page; fix every error before telling the user it's done.
    """
    if source not in ("auto", "live", "headless"):
        return "error: source must be auto, live or headless"
    try:
        pid = _p(project_id)
        rel = _rel(file) if file else ""
        out: dict = {}
        if source in ("auto", "live"):
            params = {k: str(v) for k, v in (("file", rel), ("level", level), ("since", since)) if v}
            live = await _req("GET", f"/api/design/projects/{pid}/console/live", params=params or None)
            if source == "live" or live.get("entries"):
                out["live"] = live
            if source == "live" and not live.get("ui_seen_at"):
                out["note"] = "TeleDesign is not open for this project, so there is no live console to read"
        if source == "headless" or (source == "auto" and rel):
            out["headless"] = await _req("GET", f"/api/design/projects/{pid}/console",
                                         params={"file": rel or "index.html"}, timeout=120)
        return _dump(out)
    except Exception as e:
        return _err(e)


@mcp_app.tool()
async def design_verify(project_id: str, task: str = "", files: list[str] | None = None,
                        layers: bool = True, screenshots: bool = True, turn_id: str = "",
                        chat_id: str = "") -> str:
    """Run TeleDesign's verifier now and get its report.

    task empty  — full sweep: console errors, blank boards, overflow, text under 12px (24px on
                  decks), hit targets under 44px, WCAG contrast, broken images/fonts, deck
                  navigation/labels, and layer-board overflow/overlap (needs the canvas open).
    task set    — directed check (always reports, pass or fail), e.g. "the pricing cards line
                  up at 375px" or "every slide has a title". The report carries the evidence;
                  when no local model is enabled for helpers, judge the task from it yourself.
    files       — project-relative HTML files (default: the project's registered deliverables).
    turn_id / chat_id — show the result on that turn in the user's chat.
    """
    try:
        pid = _p(project_id)
        body: dict = {"task": task, "layers": layers, "screenshots": screenshots}
        if files:
            body["files"] = [_rel(f) for f in files]
        if turn_id:
            body["turn_id"] = turn_id
        if chat_id:
            body["chat_id"] = chat_id
        return _dump(await _req("POST", f"/api/design/projects/{pid}/verify", body=body, timeout=600))
    except Exception as e:
        return _err(e)


@mcp_app.tool()
async def design_image_metadata(project_id: str, path: str) -> str:
    """Image facts for a project file: format, width×height, mode, alpha, animated frames."""
    try:
        pid, rel = _p(project_id), _rel(path)
        data, ctype = await _req("GET", f"/api/design/projects/{pid}/files/{rel}", raw=True)
        if rel.lower().endswith(".svg") or "svg" in ctype:
            return _dump({"path": rel, "format": "svg", "bytes": len(data), "vector": True})
        from PIL import Image as PILImage
        im = PILImage.open(io.BytesIO(data))
        has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
        return _dump({"path": rel, "format": (im.format or "").lower(), "width": im.width,
                      "height": im.height, "mode": im.mode, "has_alpha": has_alpha,
                      "animated": bool(getattr(im, "is_animated", False)),
                      "frames": int(getattr(im, "n_frames", 1)), "bytes": len(data)})
    except Exception as e:
        return _err(e)


# ── export ───────────────────────────────────────────────────────────────

@mcp_app.tool()
async def design_export(project_id: str, kind: str, file: str = "", board_ids: list[str] | None = None,
                        options: dict | None = None, wait: bool = True, timeout_sec: int = 300) -> str:
    """Export a project: html (standalone), zip, pdf, pptx, png, mp4 or handoff bundle.

    options examples: pptx {"mode":"editable"|"screenshots","fontSwaps":{…},"hideSelectors":[…]};
    png {"scale":2,"format":"png"|"jpeg"|"webp"}. With `wait`, polls the job and returns the
    download URL (and any validation flags, e.g. duplicate_adjacent / no_speaker_notes).
    """
    if kind not in _EXPORT_KINDS:
        return f"error: kind must be one of {', '.join(_EXPORT_KINDS)}"
    try:
        pid = _p(project_id)
        body: dict = {"options": options or {}}
        if file:
            body["file"] = _rel(file)
        if board_ids:
            body["board_ids"] = board_ids
        job = await _req("POST", f"/api/design/projects/{pid}/export/{kind}", body=body)
        job_id = job.get("job_id")
        if not wait or not job_id:
            return _dump(job)
        import asyncio
        deadline = time.monotonic() + max(10, min(int(timeout_sec or 300), 1800))
        while time.monotonic() < deadline:
            st = await _req("GET", f"/api/design/projects/{pid}/export/jobs/{job_id}")
            if st.get("status") in ("done", "failed", "error", "cancelled"):
                if st.get("download_url", "").startswith("/"):
                    st["download_url"] = _base() + st["download_url"]
                return _dump({"job_id": job_id, **st})
            await asyncio.sleep(1.5)
        return _dump({"job_id": job_id, "status": "running", "note": "still exporting"})
    except Exception as e:
        return _err(e)


# ── app state ────────────────────────────────────────────────────────────

@mcp_app.tool()
async def design_get_app_state(project_id: str = "") -> str:
    """What the user has open in TeleDesign right now: project, active file/board, view,
    mode, and the current selection (node ids and/or <mentioned-element> blocks).

    Leave project_id empty for whichever project the user touched last.
    """
    try:
        if project_id:
            pid = _p(project_id)
            res = await _req("GET", f"/api/design/projects/{pid}/app-state")
            state = res.get("state")
            if state is not None:
                state = {"project_id": pid, **state}
        else:
            state = (await _req("GET", "/api/design/app-state")).get("state")
        if not state:
            return _dump({"state": None, "note": "TeleDesign is not open, or has not reported state yet"})
        return _dump({"state": state})
    except Exception as e:
        return _err(e)


# ── design systems / styles / skills ─────────────────────────────────────

@mcp_app.tool()
async def design_list_systems() -> str:
    """List design systems (id, name, status, default flag, description)."""
    try:
        res = await _req("GET", "/api/design/systems")
        rows = [{k: s.get(k) for k in ("id", "name", "slug", "status", "is_default", "description")}
                for s in res.get("systems", [])]
        return _dump({"systems": rows})
    except Exception as e:
        return _err(e)


@mcp_app.tool()
async def design_get_system(system_id: str, file: str = "") -> str:
    """Get a design system: its record and file index; with `file`, that file's text
    (e.g. `DESIGN.md`, `USAGE.md`, `tokens.css`, `manifest.json`).
    """
    try:
        sid = _p(system_id)
        if file:
            data, _ = await _req("GET", f"/api/design/systems/{sid}/files/{_rel(file)}", raw=True)
            return data.decode("utf-8", "replace")[:_TEXT_LIMIT]
        out: dict = {"system": (await _req("GET", f"/api/design/systems/{sid}"))["system"]}
        try:
            out["files"] = (await _req("GET", f"/api/design/systems/{sid}/files")).get("files")
        except _HTTPError:
            out["files"] = None
        return _dump(out)
    except Exception as e:
        return _err(e)


def _local_styles() -> list:
    p = Path(__file__).resolve().parents[2] / "services" / "design" / "seeds" / "styles" / "styles.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("styles", [])


@mcp_app.tool()
async def design_get_style(style_id: str = "") -> str:
    """Style archetypes for choosing a visual direction (fonts, colour, imagery, mood).

    Empty `style_id` lists them all (id, name, summary); with an id, returns the full
    archetype to apply.
    """
    try:
        try:
            res = await _req("GET", "/api/design/styles")
            styles = res.get("styles", res) if isinstance(res, dict) else res
        except _HTTPError as e:
            if e.status not in (404, 405, 501):
                raise
            styles = _local_styles()
        if style_id:
            hit = next((s for s in styles if s.get("id") == style_id or s.get("slug") == style_id), None)
            return _dump(hit) if hit else f"error: no style '{style_id}'"
        return _dump({"styles": [{"id": s.get("id") or s.get("slug"), "name": s.get("name"),
                                  "summary": s.get("summary") or s.get("description")}
                                 for s in styles]})
    except Exception as e:
        return _err(e)


@mcp_app.tool()
async def design_read_skill(name: str = "") -> str:
    """Read a TeleDesign skill guide. Empty `name` lists the catalog.

    Built-ins: `deck`, `tweaks`, `code_export`, `html_boards`, `layer_boards`, `canvas`,
    `comments`, `critique`, `kinds/<kind>` (prototype, slides, wireframe, animation, …),
    `craft/<topic>` (typography, color, layout_spacing, motion, copywriting, imagery_icons,
    accessibility, anti_slop); user skills are `user/<name>`.
    """
    try:
        if not name:
            return _dump(await _req("GET", "/api/design/skills"))
        res = await _req("GET", f"/api/design/skills/{name.strip().lower()}")
        return res.get("text", "")
    except Exception as e:
        return _err(e)


@mcp_app.tool()
async def design_extract_system(name: str, sources: list[dict]) -> str:
    """Create a design system and start extracting it from sources (runs as a TeleDesign chat).

    sources: [{"type": "codebase"|"github"|"url"|"files"|"screenshots", "ref": "<path|url|…>"}],
    e.g. [{"type":"codebase","ref":"C:/code/my-app"}]. Returns the system id and the
    project/chat where the extraction runs.
    """
    try:
        system = (await _req("POST", "/api/design/systems", body={"name": name, "sources": sources}))["system"]
        res = await _req("POST", f"/api/design/systems/{system['id']}/extract", body={"sources": sources})
        out = {"system_id": system["id"], **res}
        if res.get("project_id"):
            out["url"] = _project_url(res["project_id"], chat=res.get("chat_id", ""))
        return _dump(out)
    except Exception as e:
        return _err(e)


# ── canvas / agents ──────────────────────────────────────────────────────

@mcp_app.tool()
async def design_canvas_call(project_id: str, tool: str, args: dict | None = None,
                             timeout_sec: int = 30) -> str:
    """Call one open-pencil canvas tool in the editor the user has open (layer boards).

    Requires the project open in TeleDesign's canvas. `tool` is an editor tool name (empty
    `tool="list"` returns the available tools and their parameters). Besides open-pencil's own
    tools there are TeleDesign's `telecode_*` ones: canvas documents (`telecode_doc_list`,
    `_create`, `_open`), script nodes (`telecode_script_*`), theme axes (`telecode_theme_get`,
    `_set`, `_active`), component slots (`telecode_slot_create`, `_list`, `_fill`, `_reset`) and
    shader / mesh-gradient fills (`telecode_fill_set`, `_list`, `_remove`, `_presets`).
    """
    try:
        if tool == "list":
            return _dump(await _req("GET", "/api/design/canvas/tools"))
        pid = _p(project_id)
        return _dump(await _req("POST", f"/api/design/projects/{pid}/canvas/call",
                                body={"tool": tool, "args": args or {}, "timeout": timeout_sec},
                                timeout=float(timeout_sec) + 10))
    except Exception as e:
        return _err(e)


@mcp_app.tool()
async def design_spawn_agents(project_id: str, mode: str, prompt: str, count: int = 2,
                              engines: list[str] | None = None, variant: str = "",
                              iterate: bool = False, parts: list[str] | None = None) -> str:
    """Run several design agents in parallel on one project, each in its own chat and folder.

    mode:
      split        — divide the brief; `parts` optionally names each agent's slice.
      side_by_side — every agent does the whole brief (mix `engines` to compare CLIs).
      let_it_cook  — `count` (2–6) distinct variants along `variant` = layout | style;
                     `iterate` adds one self-review pass per agent.
      jury         — one generator, `count` critics (designer/brand/a11y/copy/ux) score it,
                     reviser iterates until mean ≥ 8.0 or 3 rounds.
    Returns the run id and chat ids; progress shows in the UI (and GET …/agents/<run_id>).
    """
    try:
        pid = _p(project_id)
        body: dict = {"mode": mode, "prompt": prompt, "count": count, "iterate": iterate}
        if engines:
            body["engines"] = engines
        if variant:
            body["variant"] = variant
        if parts:
            body["parts"] = parts
        res = await _req("POST", f"/api/design/projects/{pid}/agents", body=body)
        return _dump({"run_id": res.get("run_id"), "chats": res.get("chats"),
                      "url": _project_url(pid)})
    except Exception as e:
        return _err(e)


# ── MCP prompts ──────────────────────────────────────────────────────────

@mcp_app.prompt(name="design", description="Start a TeleDesign project from this CLI: "
                "prototype, deck, one-pager, landing page, app screens…")
def design_prompt(brief: str = "", kind: str = "prototype") -> str:
    return f"""You have TeleDesign, telecode's design tool, through the `design_*` MCP tools.

Brief from the user: {brief or "(ask the user what they want to design)"}
Kind: {kind}

1. Call `design_list_systems` and pick the default system unless the brief names a brand
   or the user's codebase has one (then suggest `/design-sync` first).
2. If the brief is thin, ask the user up to five short questions (audience, content,
   variations wanted, fidelity, brand) before building.
3. Create the project with `design_create_project` (title, kind="{kind}", the system id)
   and pass the full brief as `prompt` so TeleDesign's own agent builds it — the user
   watches it live at the returned `url`. Give the user that URL.
   Alternatively, if the user wants *you* to build it here, create the project without a
   prompt and write the files yourself with `design_write_file`, checking each page with
   `design_get_console` and `design_screenshot`, then `design_show` it to the user.
4. For several directions, use `design_spawn_agents` (mode="let_it_cook", variant
   "layout" or "style"); for a quality pass, mode="jury".
5. Iterate with `design_send_message`; read feedback with `design_get_comments` and
   resolve what you addressed with `design_resolve_comments`.
"""


@mcp_app.prompt(name="design-sync", description="Turn this codebase's tokens and components "
                "into a TeleDesign design system")
def design_sync_prompt(path: str = "", name: str = "") -> str:
    return f"""Sync this codebase into a TeleDesign design system.

Codebase: {path or "the current working directory (use its absolute path)"}
System name: {name or "the product's name, from package.json / README"}

1. Look at the codebase first: where tokens live (CSS variables, Tailwind config, theme
   files), the font setup, the icon set and the core components. Note exact values.
2. Call `design_list_systems`. If a system for this product already exists, tell the user
   and ask whether to refresh it or create a new one.
3. Call `design_extract_system` with name and
   sources=[{{"type": "codebase", "ref": "<absolute path>"}}] (add a "url" source for a live
   marketing site if there is one). TeleDesign runs the extraction as a chat; give the user
   the returned `url` to review the specimen cards.
4. When it finishes, summarise what was captured and any gaps (fonts not found, icons
   substituted) so the user can publish it or fix them.
"""
