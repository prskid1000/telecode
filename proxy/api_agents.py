"""AIOHTTP routes for Agent management."""

from __future__ import annotations

import logging
from aiohttp import web
from pathlib import Path

from services.agent.agent_manager import get_agent_manager
from services.task.safe_paths import validate_id

logger = logging.getLogger("telecode.proxy.api_agents")


def _check_id(request: web.Request):
    """400 for an agent_id that is not one safe path segment (B9)."""
    try:
        validate_id(request.match_info.get("agent_id", ""), "agent_id")
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return None


def _guarded(fn):
    """Validate agent_id first; map ValueError (unsafe file name, bad engine) to 400."""
    import functools

    @functools.wraps(fn)
    async def wrapper(request: web.Request) -> web.Response:
        if "agent_id" in request.match_info:
            bad = _check_id(request)
            if bad is not None:
                return bad
        try:
            return await fn(request)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
    return wrapper

async def list_agents(request: web.Request) -> web.Response:
    agents = get_agent_manager().list_agents()
    return web.json_response({"agents": agents})

async def create_agent(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    
    name = data.get("name")
    if not name:
        return web.json_response({"error": "Name is required"}, status=400)
    
    instructions = data.get("instructions", "")
    soul = data.get("soul", "")
    agent = get_agent_manager().create_agent(name, instructions, soul=soul,
                                             engine=data.get("engine"), model=data.get("model"))
    return web.json_response({"agent": agent})

async def get_agent(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    agent = get_agent_manager().get_agent(agent_id)
    if not agent:
        return web.json_response({"error": "Agent not found"}, status=404)
    return web.json_response({"agent": agent})

async def update_agent(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    
    agent = get_agent_manager().update_agent(agent_id, data)
    if not agent:
        return web.json_response({"error": "Agent not found"}, status=404)
    return web.json_response({"agent": agent})

async def delete_agent(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    success = get_agent_manager().delete_agent(agent_id)
    if not success:
        return web.json_response({"error": "Agent not found"}, status=404)
    try:  # its HEARTBEAT.md triggers go with it
        from services.triggers import store as trigger_store
        for rec in trigger_store.list_all(source="heartbeat", agent_id=agent_id):
            trigger_store.delete(rec["id"])
    except Exception:
        logger.exception(f"deleting heartbeat triggers of {agent_id} failed")
    return web.json_response({"success": True})

async def list_agent_files(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    files = get_agent_manager().list_files(agent_id)
    return web.json_response({"files": files})

async def upload_agent_files(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    reader = await request.multipart()
    count = 0
    while True:
        part = await reader.next()
        if part is None: break
        if part.name != "files": continue
        
        filename = part.filename
        if not filename: continue
        
        content = await part.read()
        get_agent_manager().save_file(agent_id, filename, content)
        count += 1
    
    return web.json_response({"success": True, "count": count})

async def get_agent_file(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    rel_path = request.match_info["rel_path"]
    p = get_agent_manager().get_file_path(agent_id, rel_path)
    if not p:
        return web.json_response({"error": "File not found"}, status=404)
    return web.FileResponse(p)

async def delete_agent_file(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    rel_path = request.match_info["rel_path"]
    success = get_agent_manager().delete_file(agent_id, rel_path)
    if not success:
        return web.json_response({"error": "File not found"}, status=404)
    return web.json_response({"success": True})

async def get_agent_internal(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    if not get_agent_manager().get_agent(agent_id):
        return web.json_response({"error": "Agent not found"}, status=404)
    files = get_agent_manager().get_internal_files(agent_id)
    return web.json_response({"files": files})

async def update_agent_internal(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    files = data.get("files") or {}
    ok = get_agent_manager().set_internal_files(agent_id, files)
    if not ok:
        return web.json_response({"error": "Agent not found"}, status=404)

    # HEARTBEAT.md compiles to triggers on save.
    reconcile_summary = None
    if "HEARTBEAT.md" in files:
        try:
            from services.triggers.heartbeat import compile_agent
            reconcile_summary = compile_agent(agent_id)
        except Exception as exc:
            logger.exception(f"reconcile_agent failed for {agent_id}: {exc}")
            reconcile_summary = {"errors": [{"msg": str(exc)}]}

    return web.json_response({"success": True, "reconcile": reconcile_summary})


async def validate_agent_heartbeat(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    if not get_agent_manager().get_agent(agent_id):
        return web.json_response({"error": "Agent not found"}, status=404)

    try:
        data = await request.json()
    except Exception:
        data = {}

    text = data.get("text")
    if text is None:
        # default: validate the file currently on disk
        files = get_agent_manager().get_internal_files(agent_id)
        text = files.get("HEARTBEAT.md", "") or ""

    from services.triggers.heartbeat import parse
    parsed = parse(text, agent_id)
    for e in parsed["entries"]:
        e.pop("body", None)
    return web.json_response(parsed)


async def reconcile_agent_heartbeat(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    if not get_agent_manager().get_agent(agent_id):
        return web.json_response({"error": "Agent not found"}, status=404)
    from services.triggers.heartbeat import compile_agent
    return web.json_response(compile_agent(agent_id))

def register_routes(app: web.Application):
    g = _guarded
    app.router.add_get("/api/agents", list_agents)
    app.router.add_post("/api/agents", g(create_agent))
    app.router.add_get("/api/agents/{agent_id}", g(get_agent))
    app.router.add_put("/api/agents/{agent_id}", g(update_agent))
    app.router.add_delete("/api/agents/{agent_id}", g(delete_agent))
    app.router.add_get("/api/agents/{agent_id}/internal", g(get_agent_internal))
    app.router.add_put("/api/agents/{agent_id}/internal", g(update_agent_internal))
    app.router.add_post("/api/agents/{agent_id}/heartbeat/validate", g(validate_agent_heartbeat))
    app.router.add_post("/api/agents/{agent_id}/heartbeat/reconcile", g(reconcile_agent_heartbeat))
    app.router.add_get("/api/agents/{agent_id}/files", g(list_agent_files))
    app.router.add_post("/api/agents/{agent_id}/files", g(upload_agent_files))
    app.router.add_get("/api/agents/{agent_id}/files/{rel_path:.*}", g(get_agent_file))
    app.router.add_delete("/api/agents/{agent_id}/files/{rel_path:.*}", g(delete_agent_file))
    register_memory_routes(app)


# ── P4: memory (index + topics, history, revert), pinned constraints, reflection ──

def _agent_or_404(agent_id: str):
    if not get_agent_manager().get_agent(agent_id):
        return web.json_response({"error": "Agent not found"}, status=404)
    return None


async def _body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        raise ValueError("Invalid JSON")
    if not isinstance(data, dict):
        raise ValueError("body must be a JSON object")
    return data


def _safe_rel(path):
    if path and (".." in path.replace("\\", "/").split("/") or path.startswith(("/", "\\")) or ":" in path):
        raise ValueError("path must be relative to the agent's internal dir")
    return path or None


def _memory_view(agent_id: str) -> dict:
    from services.memory import repo, store
    d = store.ensure(agent_id)
    index = store.read_index(agent_id)
    return {"index": index, "topics": store.list_topics(agent_id), "warnings": store.index_warnings(index),
            "dir": str(d / store.MEMORY_DIR), "head": repo.head(d) if repo.is_repo(d) else None,
            "git": repo.is_repo(d), "limits": {"lines": store.INDEX_MAX_LINES, "bytes": store.INDEX_MAX_BYTES}}


async def get_memory(request: web.Request) -> web.Response:
    agent_id = request.match_info["agent_id"]
    nf = _agent_or_404(agent_id)
    if nf is not None:
        return nf
    return web.json_response(_memory_view(agent_id))


async def put_memory_index(request: web.Request) -> web.Response:
    from services.memory import store
    agent_id = request.match_info["agent_id"]
    nf = _agent_or_404(agent_id)
    if nf is not None:
        return nf
    data = await _body(request)
    content = data.get("content")
    if not isinstance(content, str):
        raise ValueError("'content' (string) is required")
    size = len(content.encode("utf-8"))
    if size > 4 * store.INDEX_MAX_BYTES:
        raise ValueError(f"index too large ({size // 1024} KB) — keep it under 25 KB and move detail into topic files")
    res = store.write_index(agent_id, content)
    return web.json_response({**res, **_memory_view(agent_id)})


async def rebuild_memory_index(request: web.Request) -> web.Response:
    from services.memory import store
    agent_id = request.match_info["agent_id"]
    nf = _agent_or_404(agent_id)
    if nf is not None:
        return nf
    res = store.rebuild_index(agent_id)
    return web.json_response({**res, **_memory_view(agent_id)})


async def get_memory_topic(request: web.Request) -> web.Response:
    from services.memory import store
    agent_id = request.match_info["agent_id"]
    nf = _agent_or_404(agent_id)
    if nf is not None:
        return nf
    t = store.read_topic(agent_id, request.match_info["file"])
    if t is None:
        return web.json_response({"error": "topic not found"}, status=404)
    return web.json_response(t)


async def put_memory_topic(request: web.Request) -> web.Response:
    from services.memory import store
    agent_id = request.match_info["agent_id"]
    nf = _agent_or_404(agent_id)
    if nf is not None:
        return nf
    data = await _body(request)
    if isinstance(data.get("content"), str):
        res = store.write_topic(agent_id, request.match_info["file"], content=data["content"])
    else:
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        if meta.get("type") and meta["type"] not in store.TYPES:
            raise ValueError(f"type must be one of {store.TYPES}")
        res = store.write_topic(agent_id, request.match_info["file"], meta=meta, body=str(data.get("body") or ""))
    return web.json_response({**res, "topic": store.read_topic(agent_id, res["file"])})


async def delete_memory_topic(request: web.Request) -> web.Response:
    from services.memory import store
    agent_id = request.match_info["agent_id"]
    nf = _agent_or_404(agent_id)
    if nf is not None:
        return nf
    if not store.delete_topic(agent_id, request.match_info["file"]):
        return web.json_response({"error": "topic not found"}, status=404)
    return web.json_response({"success": True})


async def get_memory_history(request: web.Request) -> web.Response:
    from services.memory import repo, store
    agent_id = request.match_info["agent_id"]
    nf = _agent_or_404(agent_id)
    if nf is not None:
        return nf
    try:
        limit = int(request.query.get("limit") or 50)
    except ValueError:
        raise ValueError("limit must be an integer")
    path = _safe_rel(request.query.get("path"))
    d = store.ensure(agent_id)
    return web.json_response({"commits": repo.history(d, limit=limit, path=path), "head": repo.head(d)})


async def get_memory_diff(request: web.Request) -> web.Response:
    from services.memory import repo, store
    agent_id = request.match_info["agent_id"]
    nf = _agent_or_404(agent_id)
    if nf is not None:
        return nf
    path = _safe_rel(request.query.get("path"))
    try:
        return web.json_response(repo.show(store.ensure(agent_id), request.query.get("commit") or "", path))
    except LookupError as exc:
        return web.json_response({"error": str(exc)}, status=404)


async def post_memory_revert(request: web.Request) -> web.Response:
    from services.memory import repo, store
    agent_id = request.match_info["agent_id"]
    nf = _agent_or_404(agent_id)
    if nf is not None:
        return nf
    data = await _body(request)
    try:
        sha = repo.revert(store.ensure(agent_id), str(data.get("commit") or ""))
    except LookupError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except repo.MemoryConflict as exc:
        return web.json_response({"error": str(exc)}, status=409)
    return web.json_response({"commit": sha, "noop": sha is None, **_memory_view(agent_id)})


async def get_pinned(request: web.Request) -> web.Response:
    from services.memory import pinned_constraints
    agent_id = request.match_info["agent_id"]
    nf = _agent_or_404(agent_id)
    if nf is not None:
        return nf
    return web.json_response({"text": pinned_constraints(agent_id)})


async def put_pinned(request: web.Request) -> web.Response:
    from services.memory import pinned_constraints, set_pinned_constraints
    agent_id = request.match_info["agent_id"]
    nf = _agent_or_404(agent_id)
    if nf is not None:
        return nf
    data = await _body(request)
    text = data.get("text")
    if not isinstance(text, str):
        raise ValueError("'text' (string) is required")
    if len(text) > 16000:
        raise ValueError("pinned constraints are limited to 16000 characters")
    agent_md = set_pinned_constraints(agent_id, text)
    return web.json_response({"text": pinned_constraints(agent_id), "agent_md": agent_md})


async def get_reflection(request: web.Request) -> web.Response:
    from services.memory import reflection
    agent_id = request.match_info["agent_id"]
    nf = _agent_or_404(agent_id)
    if nf is not None:
        return nf
    return web.json_response(reflection.status(agent_id))


async def put_reflection(request: web.Request) -> web.Response:
    from services.memory import reflection
    agent_id = request.match_info["agent_id"]
    nf = _agent_or_404(agent_id)
    if nf is not None:
        return nf
    data = await _body(request)
    nightly = data.get("nightly")
    after = data.get("after_runs")
    if nightly is not None and not isinstance(nightly, bool):
        raise ValueError("nightly must be true or false")
    if after is not None:
        try:
            after = int(after)
        except (TypeError, ValueError):
            raise ValueError("after_runs must be an integer (-1 = the setting's default, 0 = off)")
    return web.json_response(reflection.set_options(agent_id, nightly=nightly, after_runs=after))


async def post_reflect_now(request: web.Request) -> web.Response:
    from services.memory import reflection
    agent_id = request.match_info["agent_id"]
    nf = _agent_or_404(agent_id)
    if nf is not None:
        return nf
    try:
        res = reflection.reflect_now(agent_id, reason="reflect now")
    except reflection.Busy as exc:
        return web.json_response({"error": str(exc)}, status=409)
    return web.json_response({"fire": res, "status": reflection.status(agent_id)})


async def get_engine_extras(request: web.Request) -> web.Response:
    from services.memory import engine_extras
    agent_id = request.match_info["agent_id"]
    nf = _agent_or_404(agent_id)
    if nf is not None:
        return nf
    engine = request.query.get("engine") or (get_agent_manager().get_agent(agent_id) or {}).get("engine") \
        or "claude_code"
    return web.json_response({"engine": engine, **engine_extras(agent_id, engine)})


def register_memory_routes(app: web.Application):
    g = _guarded
    base = "/api/agents/{agent_id}"
    app.router.add_get(f"{base}/memory", g(get_memory))
    app.router.add_put(f"{base}/memory/index", g(put_memory_index))
    app.router.add_post(f"{base}/memory/index/rebuild", g(rebuild_memory_index))
    app.router.add_get(f"{base}/memory/topics/{{file}}", g(get_memory_topic))
    app.router.add_put(f"{base}/memory/topics/{{file}}", g(put_memory_topic))
    app.router.add_delete(f"{base}/memory/topics/{{file}}", g(delete_memory_topic))
    app.router.add_get(f"{base}/memory/history", g(get_memory_history))
    app.router.add_get(f"{base}/memory/diff", g(get_memory_diff))
    app.router.add_post(f"{base}/memory/revert", g(post_memory_revert))
    app.router.add_get(f"{base}/memory/reflection", g(get_reflection))
    app.router.add_put(f"{base}/memory/reflection", g(put_reflection))
    app.router.add_post(f"{base}/memory/reflect", g(post_reflect_now))
    app.router.add_get(f"{base}/memory/engine-extras", g(get_engine_extras))
    app.router.add_get(f"{base}/pinned", g(get_pinned))
    app.router.add_put(f"{base}/pinned", g(put_pinned))
    import services.memory.reflection  # noqa: F401 — registers the `memory` approval handler
