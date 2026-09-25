"""AIOHTTP routes for TeleDesign agents + integrations (docs/teledesign-contract.md §11).

Parallel agents
    POST /api/design/projects/{pid}/agents                {mode, count, prompt, engines?, variant?,
                                                            iterate?, parts?, threshold?, max_rounds?,
                                                            roles?, critic_engine?, critic_is_local?}
    GET  /api/design/projects/{pid}/agents                → {"runs":[…]}
    GET  /api/design/projects/{pid}/agents/{run_id}       → {"run":{…}}
    POST /api/design/projects/{pid}/agents/{run_id}/stop  → {"run":{…}}

MCP registration
    GET  /api/design/mcp/status
    POST /api/design/mcp/register   {client, dry_run?, force?}

Host-side helpers the `design_*` MCP tools call (the MCP server may be another process):
    POST /api/design/projects/{pid}/show          {path, target:"user"|"agent"} → `show` event
    GET  /api/design/projects/{pid}/app-state     last state the UI reported
    PUT  /api/design/projects/{pid}/app-state     {active_file?, active_board?, selection?, …} (UI)
    POST /api/design/projects/{pid}/screenshot    {file, width?, height?, full_page?, selector?,
                                                   steps?, scale?, format?, save_path?} (W4 render)
    POST /api/design/projects/{pid}/eval          {file, code}                    (W4 render)
    GET  /api/design/projects/{pid}/console       ?file=                          (W4 render)
    POST /api/design/projects/{pid}/canvas/call   {tool, args?, timeout?}         (W6 editor_bridge)
    GET  /api/design/canvas/tools                 editor_tools.json               (W6)
    GET  /api/design/skills                       prompt skills + user SKILL.md library
    GET  /api/design/skills/{name}                one skill's text

The REST surface has no auth: ids are validated, paths go through `store.resolve_in`, and
mutating routes require JSON and refuse a foreign `Origin` (a preview page on the preview
port must never be able to write).
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from aiohttp import web

import config
from services.design import store

logger = logging.getLogger("telecode.proxy.api_design_agents")

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "services" / "design" / "prompts"


# ── guards ───────────────────────────────────────────────────────────────

def _own_origins() -> set:
    port = int(config.get_nested("proxy.port", 1235))
    return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


def _write_guard(request: web.Request) -> Optional[web.Response]:
    origin = request.headers.get("Origin")
    if origin and origin not in _own_origins():
        return web.json_response({"error": "Cross-origin write refused"}, status=403)
    if request.content_type != "application/json":
        return web.json_response({"error": "Expected application/json"}, status=415)
    return None


async def _json_body(request: web.Request) -> Optional[Dict[str, Any]]:
    try:
        data = await request.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _project(request: web.Request):
    pid = request.match_info["project_id"]
    if not store.valid_id(pid):
        return pid, None, web.json_response({"error": "Invalid project id"}, status=400)
    pdir = store.project_dir(pid)
    if not pdir:
        return pid, None, web.json_response({"error": "Project not found"}, status=404)
    return pid, pdir, None


def _publish(pid: str, etype: str, data: Dict[str, Any]) -> None:
    try:
        from services.design import events
        events.publish(pid, etype, data)
    except Exception:
        logger.debug("design events unavailable", exc_info=True)


def _preview_url(pid: str, rel: str) -> str:
    port = int(config.get_nested("design.preview_port", 1237))
    return f"http://127.0.0.1:{port}/p/{pid}/{rel}"


def _not_built(what: str) -> web.Response:
    return web.json_response({"error": f"{what} is not available yet"}, status=501)


# ── parallel agents ──────────────────────────────────────────────────────

async def start_agents(request: web.Request) -> web.Response:
    if (bad := _write_guard(request)) is not None:
        return bad
    pid, _pdir, err = _project(request)
    if err is not None:
        return err
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    from services.design import parallel
    try:
        run = await parallel.start_run(pid, body)
    except parallel.SpecError as e:
        return web.json_response({"error": str(e)}, status=400)
    except parallel.DesignAPIError as e:
        return web.json_response({"error": f"could not create agent chats: {e.message}"},
                                 status=502 if e.status >= 500 or e.status == 404 else e.status)
    return web.json_response({"run_id": run["id"], "chats": [a["chat_id"] for a in run["agents"]],
                              "run": parallel.public_run(run)})


async def list_agent_runs(request: web.Request) -> web.Response:
    pid, _pdir, err = _project(request)
    if err is not None:
        return err
    from services.design import parallel
    return web.json_response({"runs": parallel.list_runs(pid)})


async def get_agent_run(request: web.Request) -> web.Response:
    pid, _pdir, err = _project(request)
    if err is not None:
        return err
    from services.design import parallel
    run = parallel.get_run(pid, request.match_info["run_id"])
    if not run:
        return web.json_response({"error": "Run not found"}, status=404)
    return web.json_response({"run": parallel.public_run(run)})


async def stop_agent_run(request: web.Request) -> web.Response:
    if (bad := _write_guard(request)) is not None:
        return bad
    pid, _pdir, err = _project(request)
    if err is not None:
        return err
    from services.design import parallel
    run = await parallel.stop_run(pid, request.match_info["run_id"])
    if not run:
        return web.json_response({"error": "Run not found"}, status=404)
    return web.json_response({"run": parallel.public_run(run)})


# ── MCP registration ─────────────────────────────────────────────────────

async def mcp_status(request: web.Request) -> web.Response:
    from services.design import mcp_registration
    return web.json_response(await mcp_registration.status())


async def mcp_register(request: web.Request) -> web.Response:
    if (bad := _write_guard(request)) is not None:
        return bad
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    from services.design import mcp_registration
    client = body.get("client")
    if client not in mcp_registration.CLIENTS:
        return web.json_response({"error": f"client must be one of {', '.join(mcp_registration.CLIENTS)}"},
                                 status=400)
    res = await mcp_registration.register(client, dry_run=bool(body.get("dry_run")),
                                          force=bool(body.get("force")))
    return web.json_response(res, status=200 if res.get("ok") or res.get("conflict") else 422)


# ── show / app state ─────────────────────────────────────────────────────

async def show_file(request: web.Request) -> web.Response:
    if (bad := _write_guard(request)) is not None:
        return bad
    pid, pdir, err = _project(request)
    if err is not None:
        return err
    body = await _json_body(request) or {}
    rel = body.get("path") or ""
    target = body.get("target") or "user"
    if target not in ("user", "agent"):
        return web.json_response({"error": "target must be user or agent"}, status=400)
    p = store.resolve_in(pdir, rel)
    if not p or not p.is_file():
        return web.json_response({"error": "File not found"}, status=404)
    payload = {"path": rel, "target": target, "url": _preview_url(pid, rel)}
    if isinstance(body.get("chat_id"), str) and store.valid_id(body["chat_id"]):
        payload["chat_id"] = body["chat_id"]
    _publish(pid, "show", payload)
    return web.json_response({"ok": True, **payload})


# Reported by the UI (W2) whenever the active file / board / selection changes. In memory:
# it describes what is on the user's screen right now, which a restart makes stale anyway.
_APP_STATE: Dict[str, Dict[str, Any]] = {}
_APP_STATE_KEYS = ("active_file", "active_board", "active_chat_id", "view", "mode", "viewport",
                   "selection", "slide", "open_files")
_MAX_STATE_BYTES = 64 * 1024


async def get_app_state(request: web.Request) -> web.Response:
    pid, _pdir, err = _project(request)
    if err is not None:
        return err
    return web.json_response({"state": _APP_STATE.get(pid)})


async def put_app_state(request: web.Request) -> web.Response:
    if (bad := _write_guard(request)) is not None:
        return bad
    pid, _pdir, err = _project(request)
    if err is not None:
        return err
    if (request.content_length or 0) > _MAX_STATE_BYTES:
        return web.json_response({"error": "State too large"}, status=413)
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    state = {k: body[k] for k in _APP_STATE_KEYS if k in body}
    state["updated_at"] = time.time()
    _APP_STATE[pid] = state
    return web.json_response({"ok": True})


def latest_app_state() -> Optional[Dict[str, Any]]:
    if not _APP_STATE:
        return None
    pid, st = max(_APP_STATE.items(), key=lambda kv: kv[1].get("updated_at", 0))
    return {"project_id": pid, **st}


async def get_latest_app_state(request: web.Request) -> web.Response:
    return web.json_response({"state": latest_app_state()})


# ── render-backed helpers (W4) ───────────────────────────────────────────

def _render():
    try:
        from services.design import render
        return render
    except Exception:
        return None


_MAX_SHOT_PX = 1600
_MAX_STEPS = 100


async def screenshot(request: web.Request) -> web.Response:
    if (bad := _write_guard(request)) is not None:
        return bad
    pid, pdir, err = _project(request)
    if err is not None:
        return err
    body = await _json_body(request) or {}
    rel = body.get("file") or "index.html"
    if not store.resolve_in(pdir, rel):
        return web.json_response({"error": "Invalid file"}, status=400)
    render = _render()
    if not render or not hasattr(render, "screenshot"):
        return _not_built("Screenshot rendering")
    try:
        width = max(64, min(int(body.get("width") or 1280), 4096))
        height = max(64, min(int(body.get("height") or 800), 4096))
        scale = max(1, min(int(body.get("scale") or 1), 3))
    except (TypeError, ValueError):
        return web.json_response({"error": "width/height/scale must be integers"}, status=400)
    steps = body.get("steps")
    if steps is not None and (not isinstance(steps, list) or len(steps) > _MAX_STEPS):
        return web.json_response({"error": f"steps must be a list of at most {_MAX_STEPS}"}, status=400)
    fmt = body.get("format") or "png"
    if fmt not in ("png", "jpeg"):
        return web.json_response({"error": "format must be png or jpeg"}, status=400)
    try:
        data = await render.screenshot(_preview_url(pid, rel), width, height,
                                       full_page=bool(body.get("full_page")),
                                       selector=body.get("selector") or None, steps=steps,
                                       scale=scale, fmt=fmt)
    except Exception as e:
        logger.warning("screenshot %s/%s failed: %s", pid, rel, e)
        return web.json_response({"error": f"screenshot failed: {e}"}, status=502)
    data, ctype = _fit_image(data, fmt)
    save = body.get("save_path")
    if save:
        dest = store.resolve_in(pdir, save)
        if not dest:
            return web.json_response({"error": "Invalid save_path"}, status=400)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        _publish(pid, "files", {"changed": [save]})
        return web.json_response({"ok": True, "path": save, "bytes": len(data)})
    return web.Response(body=data, content_type=ctype)


def _fit_image(data: bytes, fmt: str):
    """Cap the long edge at 1600 px (what a model can use) and encode as asked."""
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        if max(im.size) > _MAX_SHOT_PX:
            im.thumbnail((_MAX_SHOT_PX, _MAX_SHOT_PX))
        out = io.BytesIO()
        if fmt == "jpeg":
            im.convert("RGB").save(out, "JPEG", quality=85)
            return out.getvalue(), "image/jpeg"
        im.save(out, "PNG")
        return out.getvalue(), "image/png"
    except Exception:
        return data, "image/png"


async def eval_js(request: web.Request) -> web.Response:
    if (bad := _write_guard(request)) is not None:
        return bad
    pid, pdir, err = _project(request)
    if err is not None:
        return err
    body = await _json_body(request) or {}
    rel = body.get("file") or "index.html"
    code = body.get("code")
    if not isinstance(code, str) or not code.strip() or len(code) > 100_000:
        return web.json_response({"error": "code is required (≤100 KB)"}, status=400)
    if not store.resolve_in(pdir, rel):
        return web.json_response({"error": "Invalid file"}, status=400)
    render = _render()
    if not render or not hasattr(render, "eval_js"):
        return _not_built("JS evaluation")
    if re.search(r"\breturn\b", code):
        # render.eval_js evaluates an expression; statement-style code with `return`
        # becomes an awaited async IIFE.
        code = "(async () => {\n" + code + "\n})()"
    try:
        value = await render.eval_js(_preview_url(pid, rel), code)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        value = repr(value)
    return web.json_response({"ok": True, "value": value})


async def console_logs(request: web.Request) -> web.Response:
    pid, pdir, err = _project(request)
    if err is not None:
        return err
    rel = request.query.get("file") or "index.html"
    if not store.resolve_in(pdir, rel):
        return web.json_response({"error": "Invalid file"}, status=400)
    render = _render()
    if not render or not hasattr(render, "console_errors"):
        return _not_built("Console capture")
    try:
        errors = await render.console_errors(pid, rel)
    except Exception as e:
        return web.json_response({"error": f"console capture failed: {e}"}, status=502)
    return web.json_response({"file": rel, "errors": list(errors or [])})


# ── canvas (W6 editor bridge) ────────────────────────────────────────────

_TOOL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


async def canvas_call(request: web.Request) -> web.Response:
    if (bad := _write_guard(request)) is not None:
        return bad
    pid, _pdir, err = _project(request)
    if err is not None:
        return err
    body = await _json_body(request) or {}
    tool = body.get("tool")
    args = body.get("args") or {}
    if not isinstance(tool, str) or not _TOOL_RE.match(tool) or not isinstance(args, dict):
        return web.json_response({"error": "tool (name) and args (object) required"}, status=400)
    try:
        timeout = max(1.0, min(float(body.get("timeout") or 30), 300.0))
    except (TypeError, ValueError):
        timeout = 30.0
    try:
        from services.design import editor_bridge
    except Exception:
        return _not_built("The canvas editor bridge")
    try:
        result = await editor_bridge.call(pid, tool, args, timeout)
    except Exception as e:
        # No editor page registered for this project, tool error, or timeout.
        return web.json_response({"ok": False, "error": str(e) or type(e).__name__}, status=409)
    return web.json_response({"ok": True, "result": result})


async def canvas_tools(request: web.Request) -> web.Response:
    path = _PROMPTS_DIR.parent / "editor_tools.json"
    try:
        return web.json_response(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return _not_built("The canvas tool list")
    except ValueError:
        return web.json_response({"error": "editor_tools.json is not valid JSON"}, status=500)


# ── skills ───────────────────────────────────────────────────────────────

# Prompt files that make sense to read on demand. Charter/discovery/verifier are part
# of every turn's stack (or their own calls), so they are not offered here.
_SKILL_FILES = {
    "deck": "deck.md", "tweaks": "tweaks.md", "code_export": "code_export.md",
    "html_boards": "html_boards.md", "layer_boards": "layer_boards.md", "canvas": "canvas.md",
    "comments": "comments.md", "critique": "critique.md", "web_capture": "web_capture.md",
}
_SKILL_NAME_RE = re.compile(r"^[a-z0-9_-]{1,64}(/[a-z0-9_-]{1,64})?$")


def _user_skills_dir() -> Path:
    return store.base_dir() / "skills"


def _first_line(text: str) -> str:
    for line in text.splitlines():
        s = line.strip().lstrip("#").strip()
        if s and not s.startswith("<!--") and not s.startswith("---"):
            return s[:160]
    return ""


def _skill_catalog() -> Dict[str, Dict[str, Any]]:
    cat: Dict[str, Dict[str, Any]] = {}
    for name, fname in _SKILL_FILES.items():
        p = _PROMPTS_DIR / fname
        if p.is_file():
            cat[name] = {"path": p, "source": "builtin"}
    for sub in ("kinds", "craft"):
        d = _PROMPTS_DIR / sub
        if d.is_dir():
            for p in sorted(d.glob("*.md")):
                cat[f"{sub}/{p.stem}"] = {"path": p, "source": "builtin"}
    ud = _user_skills_dir()
    if ud.is_dir():
        for p in sorted(ud.glob("*/SKILL.md")):
            name = p.parent.name.lower()
            if _SKILL_NAME_RE.match(name):
                cat[f"user/{name}"] = {"path": p, "source": "user"}
    return cat


async def list_skills(request: web.Request) -> web.Response:
    out = []
    for name, meta in sorted(_skill_catalog().items()):
        try:
            text = meta["path"].read_text(encoding="utf-8")
        except OSError:
            continue
        out.append({"name": name, "source": meta["source"], "summary": _first_line(text),
                    "bytes": len(text.encode("utf-8"))})
    return web.json_response({"skills": out})


async def read_skill(request: web.Request) -> web.Response:
    name = request.match_info["name"].lower()
    if not _SKILL_NAME_RE.match(name):
        return web.json_response({"error": "Invalid skill name"}, status=400)
    meta = _skill_catalog().get(name)
    if not meta:
        return web.json_response({"error": "Skill not found"}, status=404)
    text = meta["path"].read_text(encoding="utf-8")
    return web.json_response({"name": name, "source": meta["source"], "text": text})


def register_routes(app: web.Application):
    base = "/api/design/projects/{project_id}"
    app.router.add_post(base + "/agents", start_agents)
    app.router.add_get(base + "/agents", list_agent_runs)
    app.router.add_get(base + "/agents/{run_id}", get_agent_run)
    app.router.add_post(base + "/agents/{run_id}/stop", stop_agent_run)

    app.router.add_get("/api/design/mcp/status", mcp_status)
    app.router.add_post("/api/design/mcp/register", mcp_register)

    app.router.add_post(base + "/show", show_file)
    app.router.add_get(base + "/app-state", get_app_state)
    app.router.add_put(base + "/app-state", put_app_state)
    app.router.add_get("/api/design/app-state", get_latest_app_state)
    app.router.add_post(base + "/screenshot", screenshot)
    app.router.add_post(base + "/eval", eval_js)
    app.router.add_get(base + "/console", console_logs)
    app.router.add_post(base + "/canvas/call", canvas_call)
    app.router.add_get("/api/design/canvas/tools", canvas_tools)

    app.router.add_get("/api/design/skills", list_skills)
    app.router.add_get("/api/design/skills/{name:.+}", read_skill)
