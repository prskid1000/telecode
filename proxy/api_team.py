import logging
import json
import os
import traceback
from pathlib import Path
from aiohttp import web
from services.team_store import get_team_store
from services.task.task_manager import get_task_queue, TaskStatus
from services.session import session_store

logger = logging.getLogger("telecode.proxy.api_team")

def _error_json(exc: Exception):
    tb = traceback.format_exc()
    logger.error(f"API Error: {exc}\n{tb}")
    return web.json_response({"success": False, "error": str(exc), "traceback": tb}, status=500)

# ─── Specialists ───────────────────────────────────────────────────────────

async def list_specialists(request: web.Request):
    try: return web.json_response({"success": True, "specialists": get_team_store().get_agents()})
    except Exception as e: return _error_json(e)

async def onboard_specialist(request: web.Request):
    try:
        d = await request.json()
        sid = get_team_store().add_agent(name=d.get("name"), task_type=d.get("task_type"), instructions=d.get("instructions", ""), is_local=d.get("is_local", True), avatar=d.get("avatar", "👤"), equipment=d.get("equipment", []))
        return web.json_response({"success": True, "specialist_id": sid})
    except Exception as e: return _error_json(e)

async def update_specialist(request: web.Request):
    try:
        sid = request.match_info["specialist_id"]
        d = await request.json()
        store = get_team_store()
        if sid in store.state["agents"]:
            store.state["agents"][sid].update(d)
            store.save()
            return web.json_response({"success": True})
        return web.json_response({"error": "Not found"}, status=404)
    except Exception as e: return _error_json(e)

async def fire_specialist(request: web.Request):
    try:
        get_team_store().delete_agent(request.match_info["specialist_id"])
        return web.json_response({"success": True})
    except Exception as e: return _error_json(e)

# ─── Workspaces ────────────────────────────────────────────────────────────

async def list_workspaces(request: web.Request):
    try:
        workspaces = []
        for s in session_store.list_all():
            d = s.get("data") or {}
            workspaces.append({
                "id": s["session_id"],
                "name": str(d.get("project_name") or s["session_id"][:8]),
                "governance": {"shift_limit": d.get("task_timeout"), "deadline": d.get("absolute_ttl"), "namespace": d.get("namespace")},
                "created_at": s.get("created_at")
            })
        return web.json_response({"success": True, "workspaces": workspaces})
    except Exception as e: return _error_json(e)

async def create_workspace(request: web.Request):
    try:
        d = await request.json()
        sess = session_store.create()
        session_store.patch_data(sess["session_id"], {"project_name": d.get("name", "New Project"), "task_timeout": 3600, "absolute_ttl": 86400})
        get_team_store().add_event(sess["session_id"], "workspace_created", f"Project '{d.get('name')}' initialized.")
        return web.json_response({"success": True, "workspace_id": sess["session_id"]})
    except Exception as e: return _error_json(e)

async def delete_workspace(request: web.Request):
    try:
        wid = request.match_info["workspace_id"]
        session_store.delete(wid)
        get_team_store().add_event(wid, "workspace_deleted", "Project terminated and data liquidated.")
        return web.json_response({"success": True})
    except Exception as e: return _error_json(e)

async def configure_workspace(request: web.Request):
    try:
        wid = request.match_info["workspace_id"]
        d = await request.json()
        upd = {}
        if "name" in d: upd["project_name"] = d["name"]
        if "shift_limit" in d: upd["task_timeout"] = d["shift_limit"]
        if "deadline" in d: upd["absolute_ttl"] = d["deadline"]
        if "namespace" in d: upd["namespace"] = d["namespace"]
        session_store.patch_data(wid, upd)
        get_team_store().add_event(wid, "governance_updated", "Security and limits updated.")
        return web.json_response({"success": True})
    except Exception as e: return _error_json(e)

async def get_workspace_history(request: web.Request):
    try:
        wid = request.match_info["workspace_id"]
        return web.json_response({"success": True, "history": get_team_store().get_history(wid)})
    except Exception as e: return _error_json(e)

# ─── Relay Engine ──────────────────────────────────────────────────────────

async def list_tickets(request: web.Request):
    try:
        tickets = [t for t in get_team_store().get_tickets() if t["session_id"] == request.match_info["workspace_id"]]
        return web.json_response({"success": True, "board": {s: [t for t in tickets if t["status"] == s] for s in ["blocked", "todo", "inprogress", "done", "failed"]}})
    except Exception as e: return _error_json(e)

async def assign_ticket(request: web.Request):
    try:
        d = await request.json()
        tid = get_team_store().create_ticket(title=d.get("title"), prompt=d.get("prompt"), agent_id=d.get("specialist_id") or None, session_id=request.match_info["workspace_id"], depends_on=d.get("depends_on") or None)
        return web.json_response({"success": True, "ticket_id": tid})
    except Exception as e: return _error_json(e)

async def update_ticket(request: web.Request):
    try:
        tid = request.match_info["ticket_id"]
        d = await request.json()
        store = get_team_store()
        ticket = next((t for t in store.get_tickets() if t["id"] == tid), None)
        if not ticket: return web.json_response({"error": "Not found"}, status=404)
        
        # --- STATE MACHINE VALIDATION ---
        new_status = d.get("status", ticket["status"])
        
        # 1. Blocked is immutable until auto-unlocked
        if ticket["status"] == "blocked" and new_status != "blocked":
             # Only allow manual override if dependency is gone
             if ticket.get("depends_on"):
                 dep = next((x for x in store.get_tickets() if x["id"] == ticket["depends_on"]), None)
                 if dep and dep["status"] != "done":
                     return web.json_response({"success": False, "error": "Cannot manually unblock while dependency is active."}, status=403)

        # 2. Cannot drag into "In Progress" (Shift must be started via API)
        if new_status == "inprogress" and ticket["status"] != "inprogress":
             return web.json_response({"success": False, "error": "Shifts must be started via the 'Start Shift' command."}, status=403)

        # Handle specialist swap logic
        if ticket["status"] == "inprogress" and "agent_id" in d and d["agent_id"] != ticket["agent_id"]:
            if ticket.get("task_id"): get_task_queue().cancel_task(ticket["task_id"])
            store.add_event(ticket["session_id"], "specialist_swapped", f"Hot-swapped specialist on '{ticket['title']}'")
        
        store.update_ticket(tid, **d)
        return web.json_response({"success": True})
    except Exception as e: return _error_json(e)

async def delete_ticket(request: web.Request):
    try:
        tid = request.match_info["ticket_id"]
        store = get_team_store()
        ticket = next((t for t in store.get_tickets() if t["id"] == tid), None)
        if ticket and ticket["task_id"]: get_task_queue().cancel_task(ticket["task_id"])
        store.delete_ticket(tid)
        return web.json_response({"success": True})
    except Exception as e: return _error_json(e)

async def start_shift(request: web.Request):
    try:
        tid = request.match_info["ticket_id"]
        shift_id = await _execute_shift_logic(tid)
        return web.json_response({"success": True, "shift_id": shift_id})
    except Exception as e: return _error_json(e)

async def _execute_shift_logic(tid):
    store = get_team_store()
    ticket = next((t for t in store.get_tickets() if t["id"] == tid), None)
    if not ticket: raise Exception("Ticket not found")
    if not ticket.get("agent_id"): raise Exception("Assignment must have a specialist before starting shift.")
    
    spec = {a["id"]: a for a in store.get_agents()}.get(ticket["agent_id"])
    if not spec: raise Exception("Specialist not found")
    
    # Deploy Work Files
    if spec.get("equipment"):
        from process import get_supervisor
        work_dir = (await get_supervisor()).get_session_folder(ticket["session_id"])
        for item in spec["equipment"]:
            try: 
                p = Path(work_dir) / item["path"]
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(item["content"], encoding="utf-8")
            except Exception: pass

    sess = session_store.get(ticket["session_id"])
    gov = sess.get("data") or {}
    vault = (await fetch_files(ticket["session_id"]))
    v_ctx = "\n\nVAULT:\n" + "\n".join([f"- {f['path']}" for f in vault if f['path'].startswith(".vault/")])
    
    relay = ""
    if ticket.get("depends_on"):
        prev = next((t for t in store.get_tickets() if t["id"] == ticket["depends_on"]), None)
        if prev and prev.get("task_id"):
            task = get_task_queue().get_task(prev["task_id"])
            if task and task.result: relay = f"\n\nRELAY BRIEF:\n{json.dumps(task.result)}"

    task_id = get_task_queue().submit_task(
        task_type=spec["task_type"],
        params={"prompt": f"ROLE: {spec['name']}\nGOV: {spec['instructions']}{v_ctx}{relay}\n\nGOAL: {ticket['prompt']}", "is_local": spec["is_local"]},
        session_id=ticket["session_id"],
        task_timeout_seconds=int(gov.get("task_timeout")) if gov.get("task_timeout") else None,
        absolute_ttl_seconds=int(gov.get("absolute_ttl")) if gov.get("absolute_ttl") else None,
        session_namespace=gov.get("namespace"),
        metadata={"ticket_id": tid, "specialist_name": spec["name"], "specialist_avatar": spec["avatar"]}
    )
    store.update_ticket(tid, status="inprogress", task_id=task_id)
    store.add_event(ticket["session_id"], "shift_started", f"{spec['name']} started shift on '{ticket['title']}'", {"task_id": task_id})
    return task_id

async def fetch_files(sid):
    try: return session_store.list_files(sid)
    except Exception: return []

async def pause_shift(request: web.Request):
    try:
        tid = request.match_info["ticket_id"]
        store = get_team_store()
        ticket = next((t for t in store.get_tickets() if t["id"] == tid), None)
        if ticket and ticket["task_id"]: 
            get_task_queue().cancel_task(ticket["task_id"])
            store.add_event(ticket["session_id"], "shift_paused", f"Paused work on '{ticket['title']}'")
        store.update_ticket(tid, status="todo", task_id=None)
        return web.json_response({"success": True})
    except Exception as e: return _error_json(e)

async def sync_relay(request: web.Request):
    try:
        store = get_team_store()
        queue = get_task_queue()
        for t in store.get_tickets():
            # Coordinate with Active Shifts
            if t["status"] == "inprogress" and t["task_id"]:
                task = queue.get_task(t["task_id"])
                if task and task.status.value in ("completed", "failed"): 
                    new_status = "done" if task.status.value == "completed" else "failed"
                    store.update_ticket(t["id"], status=new_status)
                    store.add_event(t["session_id"], "shift_ended", f"Task '{t['title']}' marked as {new_status.upper()}", {"task_id": t["task_id"]})
            
            # Coordinate Relays
            if t["status"] == "blocked" and t.get("depends_on"):
                dep = next((x for x in store.get_tickets() if x["id"] == t["depends_on"]), None)
                if dep and dep["status"] == "done":
                    store.update_ticket(t["id"], status="todo")
                    store.add_event(t["session_id"], "relay_unlocked", f"Relay unlocked for '{t['title']}' (Dependency Resolved)")
                    
        return web.json_response({"success": True})
    except Exception as e: return _error_json(e)

def register_routes(app: web.Application):
    app.router.add_get("/api/v1/specialists", list_specialists)
    app.router.add_post("/api/v1/specialists", onboard_specialist)
    app.router.add_patch("/api/v1/specialists/{specialist_id}", update_specialist)
    app.router.add_delete("/api/v1/specialists/{specialist_id}", fire_specialist)
    app.router.add_get("/api/v1/workspaces", list_workspaces)
    app.router.add_post("/api/v1/workspaces", create_workspace)
    app.router.add_delete("/api/v1/workspaces/{workspace_id}", delete_workspace)
    app.router.add_patch("/api/v1/workspaces/{workspace_id}", configure_workspace)
    app.router.add_get("/api/v1/workspaces/{workspace_id}/history", get_workspace_history)
    app.router.add_get("/api/v1/workspaces/{workspace_id}/tickets", list_tickets)
    app.router.add_post("/api/v1/workspaces/{workspace_id}/tickets", assign_ticket)
    app.router.add_patch("/api/v1/tickets/{ticket_id}", update_ticket)
    app.router.add_delete("/api/v1/tickets/{ticket_id}", delete_ticket)
    app.router.add_post("/api/v1/tickets/{ticket_id}/start", start_shift)
    app.router.add_post("/api/v1/tickets/{ticket_id}/pause", pause_shift)
    app.router.add_post("/api/v1/relay/sync", sync_relay)
