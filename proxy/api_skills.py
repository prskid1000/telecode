"""AIOHTTP routes for managing Claude Code skills.

Wraps :mod:`services.skills.skill_store`. Skills are operator-tier configuration
(reusable agent context that the CLIs auto-discover from the user's home), not
per-user data — so the routes follow telecode's existing open `/api/*` posture.

Endpoints (all under ``/api/skills``):

| Method  | Path                              | Purpose                          |
|---------|-----------------------------------|----------------------------------|
| GET     | ``/``                             | List skills                      |
| GET     | ``/_roots``                       | Diagnostic: list enabled roots   |
| GET     | ``/<name>``                       | Read SKILL.md + file index       |
| PUT     | ``/<name>``                       | Create / update SKILL.md         |
| DELETE  | ``/<name>``                       | Remove the skill folder          |
| GET     | ``/<name>/files/<rel:path>``      | Download a reference file        |
| PUT     | ``/<name>/files/<rel:path>``      | Upload / overwrite a ref file    |
| DELETE  | ``/<name>/files/<rel:path>``      | Delete a reference file          |
"""

from __future__ import annotations

import logging
from aiohttp import web

from services.skills import skill_store
from proxy import request_log

logger = logging.getLogger("telecode.proxy.api_skills")


def _log_req(request: web.Request):
    return request_log.new_request(request.method, request.path, inbound_protocol="skills-api")


def _err(rid, message: str, status: int) -> web.Response:
    request_log.finish(rid, status, message)
    return web.json_response({"error": message}, status=status)


async def list_skills(request: web.Request) -> web.Response:
    rid = _log_req(request)
    try:
        out = {"skills": skill_store.list_skills()}
    except Exception as e:
        return _err(rid, str(e), 500)
    request_log.set_response_preview(rid, out)
    request_log.finish(rid, 200)
    return web.json_response(out)


async def list_roots(request: web.Request) -> web.Response:
    rid = _log_req(request)
    try:
        out = {"roots": skill_store.roots_info()}
    except Exception as e:
        return _err(rid, str(e), 500)
    request_log.set_response_preview(rid, out)
    request_log.finish(rid, 200)
    return web.json_response(out)


async def get_skill(request: web.Request) -> web.Response:
    rid = _log_req(request)
    name = request.match_info["name"]
    try:
        skill = skill_store.get_skill(name)
    except ValueError as e:
        return _err(rid, str(e), 400)
    if not skill:
        return _err(rid, f"skill '{name}' not found", 404)
    request_log.set_response_preview(rid, skill)
    request_log.finish(rid, 200)
    return web.json_response(skill)


async def upsert_skill(request: web.Request) -> web.Response:
    """Create or replace a skill's SKILL.md.

    Accepts either:
      - JSON body ``{"content": "..."}``, OR
      - Raw text/markdown body (Content-Type: text/plain or text/markdown).
    """
    rid = _log_req(request)
    name = request.match_info["name"]
    ctype = (request.headers.get("Content-Type") or "").split(";")[0].strip()
    if ctype == "application/json":
        try:
            body = await request.json()
        except Exception:
            return _err(rid, "invalid JSON body", 400)
        content = body.get("content") if isinstance(body, dict) else None
        if not isinstance(content, str):
            return _err(rid, "'content' (string) is required", 400)
    else:
        content = await request.text()
    if not content.strip():
        return _err(rid, "content is empty", 400)
    try:
        skill = skill_store.upsert_skill(name, content)
    except ValueError as e:
        return _err(rid, str(e), 400)
    request_log.set_response_preview(rid, skill)
    request_log.finish(rid, 200)
    return web.json_response(skill)


async def delete_skill(request: web.Request) -> web.Response:
    rid = _log_req(request)
    name = request.match_info["name"]
    try:
        removed = skill_store.delete_skill(name)
    except ValueError as e:
        return _err(rid, str(e), 400)
    if not removed:
        return _err(rid, f"skill '{name}' not found", 404)
    out = {"deleted": name}
    request_log.set_response_preview(rid, out)
    request_log.finish(rid, 200)
    return web.json_response(out)


async def get_skill_file(request: web.Request) -> web.Response:
    rid = _log_req(request)
    name = request.match_info["name"]
    rel = request.match_info["rel"]
    try:
        data = skill_store.read_skill_file(name, rel)
    except ValueError as e:
        return _err(rid, str(e), 400)
    except FileNotFoundError:
        return _err(rid, f"file '{rel}' not found in skill '{name}'", 404)
    request_log.finish(rid, 200)
    return web.Response(body=data, content_type="application/octet-stream")


async def put_skill_file(request: web.Request) -> web.Response:
    rid = _log_req(request)
    name = request.match_info["name"]
    rel = request.match_info["rel"]
    data = await request.read()
    if not data:
        return _err(rid, "empty body", 400)
    try:
        info = skill_store.write_skill_file(name, rel, data)
    except ValueError as e:
        return _err(rid, str(e), 400)
    request_log.set_response_preview(rid, info)
    request_log.finish(rid, 200)
    return web.json_response(info)


async def delete_skill_file(request: web.Request) -> web.Response:
    rid = _log_req(request)
    name = request.match_info["name"]
    rel = request.match_info["rel"]
    try:
        removed = skill_store.delete_skill_file(name, rel)
    except ValueError as e:
        return _err(rid, str(e), 400)
    if not removed:
        return _err(rid, f"file '{rel}' not found in skill '{name}'", 404)
    out = {"deleted": rel}
    request_log.set_response_preview(rid, out)
    request_log.finish(rid, 200)
    return web.json_response(out)


# ── P4: per-agent skills (internal/skills/<name>/, staged into each run) ──
# GET    /api/agents/{id}/skills                          list
# GET|PUT|DELETE /api/agents/{id}/skills/{name}           SKILL.md ({"content"} or raw text)
# GET|PUT|DELETE /api/agents/{id}/skills/{name}/files/{rel}
# POST   /api/agents/{id}/skills/{name}/copy-to-global    {overwrite?}
# POST   /api/skills/{name}/promote                       {agent_id, overwrite?}

def _agent_guard(fn):
    import functools

    @functools.wraps(fn)
    async def wrapper(request: web.Request) -> web.Response:
        from services.agent.agent_manager import get_agent_manager
        from services.task.safe_paths import validate_id
        rid = _log_req(request)
        try:
            validate_id(request.match_info.get("agent_id", ""), "agent_id")
        except ValueError as e:
            return _err(rid, str(e), 400)
        if not get_agent_manager().get_agent(request.match_info["agent_id"]):
            return _err(rid, "Agent not found", 404)
        try:
            resp = await fn(request)
        except FileNotFoundError as e:
            return _err(rid, str(e), 404)
        except ValueError as e:
            from services.skills.agent_skills import SkillExists
            return _err(rid, str(e), 409 if isinstance(e, SkillExists) else 400)
        request_log.finish(rid, resp.status)
        return resp
    return wrapper


async def _skill_content(request: web.Request) -> str:
    ctype = (request.headers.get("Content-Type") or "").split(";")[0].strip()
    if ctype == "application/json":
        try:
            body = await request.json()
        except Exception:
            raise ValueError("invalid JSON body")
        content = body.get("content") if isinstance(body, dict) else None
        if not isinstance(content, str):
            raise ValueError("'content' (string) is required")
        return content
    return await request.text()


async def _json_or_empty(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    try:
        body = await request.json()
    except Exception:
        raise ValueError("invalid JSON body")
    return body if isinstance(body, dict) else {}


async def agent_list_skills(request: web.Request) -> web.Response:
    from services.skills import agent_skills
    return web.json_response({"skills": agent_skills.list_skills(request.match_info["agent_id"])})


async def agent_get_skill(request: web.Request) -> web.Response:
    from services.skills import agent_skills
    s = agent_skills.get_skill(request.match_info["agent_id"], request.match_info["name"])
    if not s:
        raise FileNotFoundError(f"skill '{request.match_info['name']}' not found")
    return web.json_response(s)


async def agent_put_skill(request: web.Request) -> web.Response:
    from services.skills import agent_skills
    content = await _skill_content(request)
    return web.json_response(agent_skills.upsert_skill(request.match_info["agent_id"], request.match_info["name"],
                                                       content))


async def agent_delete_skill(request: web.Request) -> web.Response:
    from services.skills import agent_skills
    if not agent_skills.delete_skill(request.match_info["agent_id"], request.match_info["name"]):
        raise FileNotFoundError(f"skill '{request.match_info['name']}' not found")
    return web.json_response({"deleted": request.match_info["name"]})


async def agent_get_skill_file(request: web.Request) -> web.Response:
    from services.skills import agent_skills
    data = agent_skills.read_skill_file(request.match_info["agent_id"], request.match_info["name"],
                                        request.match_info["rel"])
    return web.Response(body=data, content_type="application/octet-stream")


async def agent_put_skill_file(request: web.Request) -> web.Response:
    from services.skills import agent_skills
    data = await request.read()
    if not data:
        raise ValueError("empty body")
    return web.json_response(agent_skills.write_skill_file(request.match_info["agent_id"], request.match_info["name"],
                                                           request.match_info["rel"], data))


async def agent_delete_skill_file(request: web.Request) -> web.Response:
    from services.skills import agent_skills
    if not agent_skills.delete_skill_file(request.match_info["agent_id"], request.match_info["name"],
                                          request.match_info["rel"]):
        raise FileNotFoundError(f"file '{request.match_info['rel']}' not found")
    return web.json_response({"deleted": request.match_info["rel"]})


async def agent_copy_to_global(request: web.Request) -> web.Response:
    from services.skills import agent_skills
    body = await _json_or_empty(request)
    return web.json_response(agent_skills.copy_to_global(request.match_info["agent_id"], request.match_info["name"],
                                                         overwrite=bool(body.get("overwrite"))))


async def promote_skill(request: web.Request) -> web.Response:
    from services.agent.agent_manager import get_agent_manager
    from services.skills import agent_skills
    from services.skills.agent_skills import SkillExists
    from services.task.safe_paths import validate_id
    rid = _log_req(request)
    try:
        body = await _json_or_empty(request)
        agent_id = validate_id(str(body.get("agent_id") or ""), "agent_id")
        if not get_agent_manager().get_agent(agent_id):
            return _err(rid, "Agent not found", 404)
        out = agent_skills.promote_to_agent(request.match_info["name"], agent_id,
                                            overwrite=bool(body.get("overwrite")))
    except FileNotFoundError as e:
        return _err(rid, str(e), 404)
    except SkillExists as e:
        return _err(rid, str(e), 409)
    except ValueError as e:
        return _err(rid, str(e), 400)
    request_log.finish(rid, 200)
    return web.json_response(out)


def register_agent_skill_routes(app: web.Application):
    g = _agent_guard
    base = "/api/agents/{agent_id}/skills"
    app.router.add_get(base, g(agent_list_skills))
    app.router.add_get(base + "/{name}", g(agent_get_skill))
    app.router.add_put(base + "/{name}", g(agent_put_skill))
    app.router.add_delete(base + "/{name}", g(agent_delete_skill))
    app.router.add_post(base + "/{name}/copy-to-global", g(agent_copy_to_global))
    app.router.add_get(base + "/{name}/files/{rel:.+}", g(agent_get_skill_file))
    app.router.add_put(base + "/{name}/files/{rel:.+}", g(agent_put_skill_file))
    app.router.add_delete(base + "/{name}/files/{rel:.+}", g(agent_delete_skill_file))
    app.router.add_post("/api/skills/{name}/promote", promote_skill)


def register_routes(app: web.Application):
    register_agent_skill_routes(app)
    app.router.add_get("/api/skills", list_skills)
    app.router.add_get("/api/skills/", list_skills)
    app.router.add_get("/api/skills/_roots", list_roots)
    app.router.add_get("/api/skills/{name}", get_skill)
    app.router.add_put("/api/skills/{name}", upsert_skill)
    app.router.add_delete("/api/skills/{name}", delete_skill)
    app.router.add_get("/api/skills/{name}/files/{rel:.+}", get_skill_file)
    app.router.add_put("/api/skills/{name}/files/{rel:.+}", put_skill_file)
    app.router.add_delete("/api/skills/{name}/files/{rel:.+}", delete_skill_file)
