"""Design turns: chat message → prompt stack → CLI in the project folder → events.

    start_turn()           validate, record the user + assistant turns, build the prompt,
                           submit a DESIGN_TURN task, start the driver
    DESIGN_TURN handler    (task thread) runs the engine's `_run_*_subprocess` with
                           work_dir = the project folder and the chat session's resume id
    _drive()               (proxy loop) polls the task's metadata events every 250 ms and
                           tails the CLI's JSONL log → SSE events (delta / tool / todo / files)
    _finish()              snapshot a version, register assets, parse <question-form>,
                           write the transcript, then the post-turn pipeline:
                           done gate → verifier → thumbnail, and the auto title

W4's `render` and W5's `systems` are imported lazily; a missing module or
function skips its stage with a log line rather than failing the turn.

Stop terminates the CLI: `shell=True` spawns a `cmd.exe` whose child is the
real CLI, and `proc.terminate()` in the handlers only reaches the shell. The
handler thread therefore records the shell's PID as it spawns, so Stop can
kill the whole tree (and bind it to the telecode Job Object on the way).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from services.design import assets as dassets
from services.design import chats as dchats
from services.design import comments as dcomments
from services.design import events, prompt_builder, store, versions
from services.design import files as dfiles

logger = logging.getLogger("telecode.services.design.generate")

TASK_TYPE = "DESIGN_TURN"
POLL_SEC = 0.25
FILE_SCAN_SEC = 1.0
MAX_TEXT = 100_000
MAX_ATTACHMENTS = 50
MAX_COMMENTS_PER_TURN = 100
RENDER_TIMEOUT = 120.0

# Resume key per engine in the session's data (set by the handlers).
RESUME_KEYS = {
    "claude_code": "last_claude_session_id",
    "codex": "last_codex_session_id",
    "antigravity": "last_antigravity_conversation_id",
    # agy local runs in an isolated home, so its conversations are separate.
    "antigravity_local": "last_antigravity_local_conversation_id",
}


def resume_slot(engine: str, is_local: bool) -> str:
    """Key into RESUME_KEYS (and the per-slot brief record). Claude and Codex keep
    their session files in the user's home either way; agy local does not."""
    return "antigravity_local" if engine == "antigravity" and is_local else engine
BINARIES = {"claude_code": "claude", "codex": "codex", "antigravity": "agy"}


class BusyError(RuntimeError):
    """A turn is already running in this chat (HTTP 409)."""


class NotFound(LookupError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _optional(module: str, attr: str):
    try:
        mod = __import__(f"services.design.{module}", fromlist=[attr])
    except Exception as exc:
        logger.info("design: services.design.%s unavailable (%s) — skipping %s", module, exc, attr)
        return None
    fn = getattr(mod, attr, None)
    if fn is None:
        logger.info("design: services.design.%s.%s not implemented yet — skipping", module, attr)
    return fn


# ── In-memory state (proxy loop) ─────────────────────────────────────────

_live: Dict[str, Dict[str, Any]] = {}              # turn_id -> running assistant turn
_chat_running: Dict[Tuple[str, str], str] = {}     # (pid, cid) -> turn_id
_drivers: Dict[str, asyncio.Task] = {}             # turn_id -> driver task
_child_pids: Dict[str, int] = {}                   # task_id -> cmd.exe pid
_spawn_lock = threading.Lock()


def running_turn(pid: str, cid: str) -> Optional[Dict[str, Any]]:
    tid = _chat_running.get((pid, cid))
    return _live.get(tid) if tid else None


# ── Child-process tracking (Windows) ─────────────────────────────────────

def _win_children(parent: int) -> List[Tuple[int, str]]:
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    out = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.th32ParentProcessID == parent:
                out.append((int(entry.th32ProcessID), entry.szExeFile))
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def _begin_spawn_watch(task_id: str) -> Optional[threading.Event]:
    """Hold the spawn lock until this task's shell appears, then record its PID.

    Serialising spawns is what makes "the new cmd.exe child" unambiguous when
    several design turns start at once. Released after at most 15 s.
    """
    if os.name != "nt":
        return None
    try:
        before = {p for p, _ in _win_children(os.getpid())}
    except Exception as exc:
        logger.debug("design: child snapshot failed: %s", exc)
        return None
    _spawn_lock.acquire()
    done = threading.Event()

    def watch() -> None:
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not done.is_set():
                for pid, exe in _win_children(os.getpid()):
                    if pid not in before and exe.lower() == "cmd.exe":
                        _child_pids[task_id] = pid
                        try:
                            import process as tc_process
                            tc_process.bind_to_lifetime_job(pid)
                            for cpid, _ in _win_children(pid):
                                tc_process.bind_to_lifetime_job(cpid)
                        except Exception:
                            pass
                        return
                time.sleep(0.05)
        finally:
            _spawn_lock.release()

    threading.Thread(target=watch, name=f"design-spawn-{task_id[:8]}", daemon=True).start()
    return done


def _kill_task_process(task_id: str) -> None:
    pid = _child_pids.get(task_id)
    if not pid:
        return
    try:
        import process as tc_process
        tc_process.kill_process_tree(pid, force=True)
        logger.info("design: killed CLI process tree %d for task %s", pid, task_id)
    except Exception as exc:
        logger.warning("design: could not kill pid %d: %s", pid, exc)


# ── DESIGN_TURN handler (runs in a task-queue thread) ────────────────────

def design_turn_handler(pid: str, chat_id: str, turn_id: str, engine: str, prompt: str,
                        is_local: bool = False, model: Optional[str] = None) -> Dict[str, Any]:
    from services.session import session_store
    from services.task.task_utils import get_session_id, get_session_namespace, get_task_id

    root = store.project_dir(pid)
    if not root:
        raise RuntimeError("Design project not found")
    sid, ns = get_session_id(), get_session_namespace()
    if not sid:
        raise RuntimeError("No session bound to this task")
    task_id = get_task_id() or uuid.uuid4().hex
    log_dir = Path(config._settings_dir()) / "data" / "task_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{task_id}.jsonl"
    data = (session_store.get(sid, namespace=ns) or {}).get("data") or {}
    resume_id = data.get(RESUME_KEYS.get(resume_slot(engine, is_local), ""))

    watch = _begin_spawn_watch(task_id)
    try:
        if engine == "claude_code":
            from services.task.handlers.claude_code import _run_claude_subprocess
            return _run_claude_subprocess(prompt=prompt, work_dir=root, sid=sid, ns=ns,
                                          resume_id=resume_id, is_local=is_local, log_path=log_path,
                                          model=model)
        if engine == "codex":
            from services.task.handlers.codex import _run_codex_subprocess
            return _run_codex_subprocess(prompt=prompt, work_dir=root, sid=sid, ns=ns,
                                         resume_id=resume_id, is_local=is_local, log_path=log_path,
                                         last_msg_path=log_dir / f"{task_id}.codex_last_message.txt",
                                         model=model)
        if engine == "antigravity":
            from services.task.handlers.antigravity import _run_antigravity_subprocess
            return _run_antigravity_subprocess(prompt=prompt, work_dir=root, sid=sid, ns=ns,
                                               resume_id=resume_id, log_path=log_path,
                                               is_local=is_local, model=model)
        raise RuntimeError(f"Unknown engine {engine!r}")
    finally:
        if watch:
            watch.set()
        _child_pids.pop(task_id, None)


def register_task_type() -> None:
    from services.task.task_manager import get_task_queue
    get_task_queue().register_handler(
        TASK_TYPE, design_turn_handler,
        description="TeleDesign chat turn: runs the chat's CLI in the design project folder",
        params_schema={"type": "object", "properties": {
            "pid": {"type": "string"}, "chat_id": {"type": "string"}, "turn_id": {"type": "string"},
            "engine": {"type": "string"}, "prompt": {"type": "string"}, "is_local": {"type": "boolean"},
            "model": {"type": "string"}},
            "required": ["pid", "chat_id", "turn_id", "engine", "prompt"]},
    )


register_task_type()


# ── Turns: read side ─────────────────────────────────────────────────────

def list_turns(pid: str, cid: str, after: Optional[str] = None) -> List[Dict[str, Any]]:
    turns = dchats.read_turns(pid, cid)
    stale = False
    for i, t in enumerate(turns):
        live = _live.get(t.get("id"))
        if live:
            turns[i] = live
        elif t.get("status") in ("queued", "running"):
            # Nothing drives it: telecode restarted mid-turn.
            t["status"] = "failed"
            t["error"] = t.get("error") or "interrupted (telecode restarted)"
            t["finished_at"] = t.get("finished_at") or _now_iso()
            stale = True
    if stale:
        dchats.write_turns(pid, cid, [t for t in turns if t.get("id") not in _live])
    if after:
        for i, t in enumerate(turns):
            if t.get("id") == after:
                return turns[i + 1:]
    return turns


# ── Turns: start ─────────────────────────────────────────────────────────

def _new_record(cid: str, role: str, **kw: Any) -> Dict[str, Any]:
    rec = {"id": uuid.uuid4().hex, "chat_id": cid, "role": role, "text": "", "status": "done",
           "task_id": None, "engine": None, "is_local": False, "model": None, "effort": None, "attachments": [],
           "comment_ids": [], "form": None, "todos": [], "tools": [], "changed_files": [],
           "version": None, "usage": None, "error": None, "created_at": _now_iso(), "finished_at": None}
    rec.update(kw)
    return rec


def _validate_body(pid: str, root: Path, body: Dict[str, Any]) -> Dict[str, Any]:
    text = body.get("text", "")
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if len(text) > MAX_TEXT:
        raise ValueError("text too long")
    atts = body.get("attachments") or []
    if not isinstance(atts, list) or len(atts) > MAX_ATTACHMENTS:
        raise ValueError("attachments must be a list of upload paths")
    for a in atts:
        if not isinstance(a, str) or not a.startswith("uploads/") or not store.resolve_in(root, a) \
                or not (root / a).is_file():
            raise ValueError(f"attachment not found: {a!r}")
    cids = body.get("comment_ids") or []
    if not isinstance(cids, list) or len(cids) > MAX_COMMENTS_PER_TURN or not all(store.valid_id(c) for c in cids):
        raise ValueError("comment_ids must be a list of comment ids")
    fa = body.get("form_answers")
    if fa is not None and not isinstance(fa, dict):
        raise ValueError("form_answers must be an object")
    if fa is not None and len(json.dumps(fa)) > 200_000:
        raise ValueError("form_answers too large")
    sel = body.get("selection")
    if sel is not None and (not isinstance(sel, (dict, list)) or len(json.dumps(sel)) > 100_000):
        raise ValueError("selection must be an object")
    if "engine" in body and body["engine"] not in dchats.ENGINES:
        raise ValueError("invalid engine")
    if "effort" in body and body["effort"] not in dchats.EFFORTS:
        raise ValueError("invalid effort")
    if body.get("style_id") is not None and not store._clean_style_id(body.get("style_id")):
        raise ValueError("invalid style_id")
    ks = body.get("kind_skill")
    if ks is not None and ks not in store.VALID_KINDS and ks != "critique":
        raise ValueError("invalid kind_skill")
    if not text.strip() and not cids and not fa:
        raise ValueError("empty turn")
    return body


def engine_available(engine: str) -> bool:
    return shutil.which(BINARIES.get(engine, "")) is not None


async def start_turn(pid: str, cid: str, body: Dict[str, Any], *, auto: Optional[str] = None,
                     parent_turn_id: Optional[str] = None) -> Dict[str, Any]:
    """Start a turn in a chat. Returns {"turn": assistant, "user_turn": user}.

    Raises NotFound, ValueError (bad input) or BusyError (409). Callable from
    other modules on the proxy loop (comments send, W5 extract, W7 parallel).
    """
    project = store.get_project(pid)
    root = store.project_dir(pid)
    chat = dchats.get_chat(pid, cid)
    if not project or not root or not chat:
        raise NotFound("project or chat not found")
    body = _validate_body(pid, root, body or {})
    key = (pid, cid)
    if key in _chat_running:
        raise BusyError("a turn is already running in this chat")
    engine = body.get("engine") or chat.get("engine") or dchats.default_engine()
    if not engine_available(engine):
        raise ValueError(f"engine {engine} is not installed (no `{BINARIES[engine]}` on PATH)")
    is_local = bool(body["is_local"]) if "is_local" in body else bool(chat.get("is_local"))
    effort = body.get("effort", chat.get("effort"))
    model = (dchats.clean_model(body["model"]) if "model" in body else chat.get("model")) \
        or dchats.default_model(engine, is_local)

    comment_recs: List[Dict[str, Any]] = []
    cids = body.get("comment_ids") or []
    if cids:
        existing = {c["id"]: c for c in dcomments.list_comments(pid) or []}
        missing = [c for c in cids if c not in existing]
        if missing:
            raise ValueError(f"comment not found: {missing[0]}")
        comment_recs = [existing[c] for c in cids]

    _chat_running[key] = "reserved"
    try:
        user_turn = _new_record(cid, "user", text=body.get("text", ""), engine=engine, is_local=is_local, model=model,
                                effort=effort, attachments=body.get("attachments") or [],
                                comment_ids=cids, form_answers=body.get("form_answers"),
                                selection=body.get("selection"))
        if auto:
            user_turn["auto"] = auto
        turn = _new_record(cid, "assistant", status="queued", engine=engine, is_local=is_local, model=model,
                           effort=effort, attachments=user_turn["attachments"], comment_ids=cids,
                           reply_to=user_turn["id"])
        if auto:
            turn["auto"] = auto
        if parent_turn_id:
            turn["parent_turn_id"] = parent_turn_id
        if comment_recs:
            comment_recs = dcomments.mark_sent(pid, cids, turn["id"])
            events.publish(pid, "comments", {"changed": cids})

        turn_number = dchats.assistant_turn_count(pid, cid) + 1
        project_turns = dchats.assistant_turn_count(pid)
        spec = {**body, "engine": engine, "is_local": is_local, "effort": effort, "auto": auto}
        try:
            built = await asyncio.to_thread(prompt_builder.build, project, chat, spec, project_dir=root,
                                            comments=comment_recs, turn_number=turn_number,
                                            project_turns=project_turns)
            await asyncio.to_thread(prompt_builder.write_brief, root, built["brief"])
        except Exception as exc:
            logger.exception("design: prompt build failed for %s/%s", pid, cid)
            turn.update(status="failed", error=f"prompt build failed: {exc}", finished_at=_now_iso())
            dchats.upsert_turn(pid, cid, user_turn)
            dchats.upsert_turn(pid, cid, turn)
            if cids:
                dcomments.reopen_for_turn(pid, turn["id"])
            _chat_running.pop(key, None)
            events.publish(pid, "turn", {**turn, "chat_id": cid})
            return {"turn": turn, "user_turn": user_turn}

        sid = chat.get("session_id") or dchats.session_id_for(pid, cid)
        include_brief = _needs_brief(sid, resume_slot(engine, is_local), built["brief_sha"])
        prompt = prompt_builder.compose(built, include_brief=include_brief)

        dchats.upsert_turn(pid, cid, user_turn)
        dchats.upsert_turn(pid, cid, turn)
        _live[turn["id"]] = turn
        _chat_running[key] = turn["id"]
        store.set_project_fields(pid, active_chat_id=cid)
        events.publish(pid, "turn", {**user_turn, "chat_id": cid})

        ctx = {"pid": pid, "cid": cid, "sid": sid, "engine": engine, "slot": resume_slot(engine, is_local),
               "brief_sha": built["brief_sha"],
               "project_turns": project_turns, "user_text": body.get("text", ""), "built": built,
               "retried": False}
        ctx["baseline"] = await asyncio.to_thread(_mtimes, root)
        _submit(turn, ctx, prompt)
        events.publish(pid, "turn", {**turn, "chat_id": cid})
        _drivers[turn["id"]] = asyncio.get_running_loop().create_task(_drive(turn, ctx))
        return {"turn": turn, "user_turn": user_turn}
    except Exception:
        if _chat_running.get(key) == "reserved":
            _chat_running.pop(key, None)
        raise


def _needs_brief(sid: str, engine: str, brief_sha: str) -> bool:
    from services.session import session_store
    data = (session_store.get(sid, namespace=dchats.SESSION_NAMESPACE) or {}).get("data") or {}
    resume = data.get(RESUME_KEYS.get(engine, ""))
    sent = (data.get("design_brief") or {}).get(engine) or {}
    return not resume or sent.get("sha") != brief_sha or sent.get("resume") != resume


def _mark_brief_sent(sid: str, engine: str, brief_sha: str) -> None:
    from services.session import session_store
    data = (session_store.get(sid, namespace=dchats.SESSION_NAMESPACE) or {}).get("data") or {}
    resume = data.get(RESUME_KEYS.get(engine, ""))
    if resume:
        session_store.patch_data(sid, {"design_brief": {engine: {"sha": brief_sha, "resume": resume}}},
                                 namespace=dchats.SESSION_NAMESPACE)


def _submit(turn: Dict[str, Any], ctx: Dict[str, Any], prompt: str) -> None:
    from services.task.task_manager import get_task_queue
    task_id = get_task_queue().submit_task(
        TASK_TYPE,
        params={"pid": ctx["pid"], "chat_id": ctx["cid"], "turn_id": turn["id"], "engine": ctx["engine"],
                "prompt": prompt, "is_local": bool(turn.get("is_local")), "model": turn.get("model")},
        metadata={"source": "design", "project_id": ctx["pid"], "chat_id": ctx["cid"], "turn_id": turn["id"]},
        session_id=ctx["sid"],
        session_namespace=dchats.SESSION_NAMESPACE,
        session_idle_timeout_seconds=dchats.SESSION_IDLE_SECONDS,
    )
    turn["task_id"] = task_id


# ── Stop ─────────────────────────────────────────────────────────────────

def stop_turn(pid: str, cid: str) -> bool:
    tid = _chat_running.get((pid, cid))
    turn = _live.get(tid) if tid else None
    if not turn:
        return False
    turn["_stop"] = True
    task_id = turn.get("task_id")
    if task_id:
        from services.task.task_manager import TaskStatus, get_task_queue
        q = get_task_queue()
        task = q.get_task(task_id)
        if task:
            with q.lock:
                if task.future and not task.future.done():
                    task.future.cancel()
                if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    task.status = TaskStatus.CANCELLED
                    task.completed_at = datetime.now()
        _kill_task_process(task_id)
    return True


# ── Driver ───────────────────────────────────────────────────────────────

def _preview(root: Path, summary: str, name: str) -> str:
    s = str(summary or "")
    if s.startswith(f"{name}: "):
        s = s[len(name) + 2:]
    try:
        p = Path(s)
        if p.is_absolute():
            s = p.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        pass
    return s[:300]


class _LogTail:
    """Incremental reader of the CLI's JSONL log (the handlers write every line)."""

    def __init__(self) -> None:
        self.path: Optional[Path] = None
        self.offset = 0
        self.buf = b""

    def read(self) -> List[Dict[str, Any]]:
        if not self.path or not self.path.exists():
            return []
        try:
            with self.path.open("rb") as fh:
                fh.seek(self.offset)
                chunk = fh.read(4 * 1024 * 1024)
        except OSError:
            return []
        self.offset += len(chunk)
        data = self.buf + chunk
        lines = data.split(b"\n")
        self.buf = lines.pop()
        out = []
        for ln in lines:
            try:
                evt = json.loads(ln.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if isinstance(evt, dict):
                out.append(evt)
        return out


def _mtimes(root: Path) -> Dict[str, Tuple[float, int]]:
    out = {}
    for rel, p in dfiles.iter_project_files(root, include_readonly=False):
        try:
            st = p.stat()
            out[rel] = (st.st_mtime, st.st_size)
        except OSError:
            pass
    return out


def _todos_from(items: Any, text_key: str, done_key: Optional[str] = None) -> List[Dict[str, str]]:
    todos = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        text = str(it.get(text_key) or it.get("content") or it.get("text") or "")[:300]
        if done_key:
            status = "completed" if it.get(done_key) else "pending"
        else:
            status = it.get("status") if it.get("status") in ("pending", "in_progress", "completed") else "pending"
        if text:
            todos.append({"text": text, "status": status})
    return todos


async def _drive(turn: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    from services.task.task_manager import TaskStatus, get_task_queue
    pid, cid = ctx["pid"], ctx["cid"]
    root = store.project_dir(pid)
    q = get_task_queue()
    seen = 0
    tail = _LogTail()
    streamed = False
    text_parts: List[str] = [turn.get("text") or ""]
    baseline = dict(ctx.get("baseline") or {})
    live_changed: set = set()
    last_scan = time.monotonic()
    usage: Dict[str, Any] = {}

    def emit_text(t: str) -> None:
        if not t:
            return
        text_parts.append(t)
        turn["text"] = "".join(text_parts)
        events.publish(pid, "delta", {"turn_id": turn["id"], "chat_id": cid, "text": t})

    try:
        while True:
            task = q.get_task(turn["task_id"])
            if task is None:
                raise RuntimeError("task vanished")
            # Read the status before the events: once terminal, the events read
            # below are complete (the handler appends `done` before it returns).
            terminal = task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
            if task.status == TaskStatus.RUNNING and turn["status"] == "queued":
                turn["status"] = "running"
                turn["started_at"] = _now_iso()
                events.publish(pid, "turn", {**_public(turn), "chat_id": cid})
            if tail.path is None and task.status != TaskStatus.PENDING:
                tail.path = Path(config._settings_dir()) / "data" / "task_logs" / f"{turn['task_id']}.jsonl"

            # 1. Streamed text + todos from the raw CLI log.
            for evt in tail.read():
                et = evt.get("type")
                if et == "stream_event":
                    inner = evt.get("event") or {}
                    if inner.get("type") == "content_block_start" and \
                            (inner.get("content_block") or {}).get("type") == "text" and turn.get("text"):
                        emit_text("\n\n")
                    delta = inner.get("delta") or {}
                    if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                        streamed = True
                        emit_text(delta.get("text") or "")
                elif et == "assistant":
                    for block in (evt.get("message") or {}).get("content") or []:
                        if block.get("type") == "tool_use" and block.get("name") == "TodoWrite":
                            todos = _todos_from((block.get("input") or {}).get("todos"), "content")
                            turn["todos"] = todos
                            events.publish(pid, "todo", {"turn_id": turn["id"], "chat_id": cid, "todos": todos})
                elif et in ("item.started", "item.updated", "item.completed"):
                    item = evt.get("item") or {}
                    if item.get("type") == "todo_list":
                        todos = _todos_from(item.get("items"), "text", "completed")
                        turn["todos"] = todos
                        events.publish(pid, "todo", {"turn_id": turn["id"], "chat_id": cid, "todos": todos})

            # 2. Handler events (narrative / tool / done) from task metadata.
            with q.lock:
                evs = list(task.metadata.get("events") or [])[seen:]
            seen += len(evs)
            for e in evs:
                kind = e.get("kind")
                if kind == "narrative" and not streamed:
                    emit_text(("\n\n" if turn.get("text") else "") + (e.get("text") or ""))
                elif kind == "narrative_delta":
                    emit_text(e.get("text") or "")
                elif kind == "tool":
                    name = str(e.get("tool") or e.get("name") or "tool")
                    summary = e.get("summary")
                    if summary is None and e.get("input") is not None:
                        summary = json.dumps(e.get("input"), ensure_ascii=False)[:300]
                    tool = {"name": name, "input_preview": _preview(root, summary or "", name)}
                    turn["tools"].append(tool)
                    events.publish(pid, "tool", {"turn_id": turn["id"], "chat_id": cid, **tool})
                elif kind == "retry":
                    events.publish(pid, "error", {"turn_id": turn["id"], "chat_id": cid, "retry": True,
                                                  "message": f"API retry {e.get('attempt')}/{e.get('max_retries')}: "
                                                             f"{str(e.get('error'))[:300]}"})
                elif kind == "done":
                    usage = e

            # 3. Files landing mid-turn, so boards reload as the agent writes.
            if root and time.monotonic() - last_scan >= FILE_SCAN_SEC:
                last_scan = time.monotonic()
                now = await asyncio.to_thread(_mtimes, root)
                changed = sorted(k for k in set(now) | set(baseline) if now.get(k) != baseline.get(k))
                fresh = [c for c in changed if c not in live_changed or now.get(c) != baseline.get(c)]
                if fresh:
                    baseline.update({k: now[k] for k in fresh if k in now})
                    for k in fresh:
                        if k not in now:
                            baseline.pop(k, None)
                    live_changed.update(fresh)
                    events.publish(pid, "files", {"turn_id": turn["id"], "chat_id": cid,
                                                  "changed": fresh, "version": None})

            if terminal:
                if root:
                    now = await asyncio.to_thread(_mtimes, root)
                    live_changed.update(k for k in set(now) | set(baseline) if now.get(k) != baseline.get(k))
                ctx["live_changed"] = live_changed
                await _finish(turn, ctx, task, usage)
                return
            await asyncio.sleep(POLL_SEC)
    except Exception as exc:
        logger.exception("design: driver for turn %s crashed", turn["id"])
        turn.update(status="failed", error=f"driver error: {exc}", finished_at=_now_iso())
        _persist_final(turn, ctx)


def _public(turn: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in turn.items() if not k.startswith("_")}


_FORM_RE = re.compile(r"<question-form>\s*(.*?)\s*</question-form>", re.S)
_STALE_RESUME_RE = re.compile(r"no conversation found|session not found|could not find (a )?session|"
                              r"thread .* not found|conversation .* not found", re.I)


def parse_form(text: str) -> Optional[Dict[str, Any]]:
    m = None
    for m in _FORM_RE.finditer(text or ""):
        pass
    if not m:
        return None
    body = m.group(1).strip()
    if body.startswith("```"):
        body = re.sub(r"^```\w*\s*|\s*```$", "", body)
    try:
        form = json.loads(body)
    except json.JSONDecodeError:
        return {"error": "invalid question-form JSON", "raw": body[:20000]}
    return form if isinstance(form, dict) else None


async def _finish(turn: Dict[str, Any], ctx: Dict[str, Any], task: Any, usage: Dict[str, Any]) -> None:
    from services.task.task_manager import TaskStatus
    pid, cid = ctx["pid"], ctx["cid"]
    result = task.result or {}

    # A stale resume id (CLI session gone) fails the first line: clear it and
    # retry once with the full brief instead of surfacing a dead chat.
    if task.status == TaskStatus.FAILED and not ctx["retried"] and not turn.get("_stop") \
            and _STALE_RESUME_RE.search(task.error or ""):
        from services.session import session_store
        logger.info("design: stale resume id for %s — retrying fresh", ctx["sid"])
        session_store.patch_data(ctx["sid"], {RESUME_KEYS[ctx["slot"]]: None},
                                 namespace=dchats.SESSION_NAMESPACE)
        ctx["retried"] = True
        _submit(turn, ctx, prompt_builder.compose(ctx["built"], include_brief=True))
        _drivers[turn["id"]] = asyncio.get_running_loop().create_task(_drive(turn, ctx))
        return

    if task.status == TaskStatus.COMPLETED:
        turn["status"] = "done"
    elif task.status == TaskStatus.CANCELLED or turn.get("_stop"):
        turn["status"] = "cancelled"
    else:
        turn["status"] = "failed"
        turn["error"] = (task.error or "failed")[:2000]
    if not (turn.get("text") or "").strip() and result.get("result"):
        turn["text"] = str(result["result"])
        events.publish(pid, "delta", {"turn_id": turn["id"], "chat_id": cid, "text": turn["text"]})
    tokens = result.get("tokens") or {}
    turn["usage"] = {
        "input": tokens.get("total_input_incl_cache") or usage.get("input_tokens") or 0,
        "output": tokens.get("output") or usage.get("output_tokens") or 0,
        "cache_read": tokens.get("cache_read") or usage.get("cache_read_tokens") or 0,
        "cost_usd": result.get("cost_usd") or usage.get("cost_usd") or 0,
        "duration_ms": result.get("duration_ms") or 0,
    }

    # Version: whatever the agent wrote, even on a failed or stopped turn.
    try:
        rec, changed = await asyncio.to_thread(versions.snapshot, pid, "agent", turn_id=turn["id"],
                                               prompt=ctx.get("user_text"))
    except Exception as exc:
        logger.exception("design: snapshot failed for %s", pid)
        rec, changed = None, []
        events.publish(pid, "error", {"turn_id": turn["id"], "chat_id": cid, "message": f"snapshot failed: {exc}"})
    if rec and rec.get("parent") is None:
        # The project's first version holds every file (boards.json, uploads…);
        # the turn only changed what it touched.
        changed = [c for c in changed if c in ctx.get("live_changed", ())]
    turn["changed_files"] = changed
    turn["version"] = rec["v"] if rec else (store.get_project(pid) or {}).get("current_version")
    if changed:
        events.publish(pid, "files", {"turn_id": turn["id"], "chat_id": cid, "changed": changed,
                                      "version": turn["version"]})
    try:
        if await asyncio.to_thread(dassets.register_changed, pid, changed, rec["v"] if rec else None) or \
                "assets.json" in ctx.get("live_changed", ()):
            events.publish(pid, "assets", {})
    except Exception:
        logger.exception("design: asset registration failed for %s", pid)

    form = parse_form(turn.get("text") or "")
    if form:
        turn["form"] = form
        events.publish(pid, "form", {"turn_id": turn["id"], "chat_id": cid, "form": form})

    if turn["status"] == "done":
        try:
            _mark_brief_sent(ctx["sid"], ctx["slot"], ctx["brief_sha"])
        except Exception:
            logger.exception("design: could not record brief for %s", ctx["sid"])
    elif turn.get("comment_ids"):
        reopened = dcomments.reopen_for_turn(pid, turn["id"])
        if reopened:
            events.publish(pid, "comments", {"changed": reopened})

    turn["finished_at"] = _now_iso()
    _persist_final(turn, ctx)
    if turn["status"] == "done":
        asyncio.get_running_loop().create_task(_post_turn(turn, ctx))


def _persist_final(turn: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    pid, cid = ctx["pid"], ctx["cid"]
    rec = _public(turn)
    try:
        dchats.upsert_turn(pid, cid, rec)
        dchats.write_transcript(pid, cid)
    except Exception:
        logger.exception("design: could not persist turn %s", turn["id"])
    _live.pop(turn["id"], None)
    _drivers.pop(turn["id"], None)
    if _chat_running.get((pid, cid)) == turn["id"]:
        _chat_running.pop((pid, cid), None)
    events.publish(pid, "turn", {**rec, "chat_id": cid})


# ── Post-turn pipeline ───────────────────────────────────────────────────

async def _post_turn(turn: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    pid, cid = ctx["pid"], ctx["cid"]
    root = store.project_dir(pid)
    if not root:
        return
    html_changed = [f for f in turn.get("changed_files") or []
                    if f.lower().endswith(".html") and (root / f).is_file() and not f.startswith("_ds/")]
    try:
        await _auto_title(turn, ctx)
    except Exception:
        logger.exception("design: auto-title failed")
    fixing = False
    try:
        fixing = await _done_gate(turn, ctx, html_changed)
    except Exception:
        logger.exception("design: done gate failed")
    if not fixing:
        try:
            await _verifier(turn, ctx, html_changed)
        except Exception:
            logger.exception("design: verifier failed")
    if turn.get("changed_files"):
        try:
            await _thumbnail(pid)
        except Exception:
            logger.exception("design: thumbnail failed")


def _check(turn: Dict[str, Any], cid: str, pid: str, stage: str, status: str, **extra: Any) -> None:
    events.publish(pid, "check", {"turn_id": turn["id"], "chat_id": cid, "stage": stage, "status": status, **extra})


async def _start_fix_turn(turn: Dict[str, Any], ctx: Dict[str, Any], text: str, stage: str) -> bool:
    if turn.get("auto"):
        return False  # one automatic fix per user turn
    try:
        await start_turn(ctx["pid"], ctx["cid"], {"text": text}, auto="fix", parent_turn_id=turn["id"])
    except BusyError:
        logger.info("design: %s found issues but the chat is busy — not auto-fixing", stage)
        return False
    except Exception as exc:
        logger.warning("design: could not start %s fix turn: %s", stage, exc)
        return False
    _check(turn, ctx["cid"], ctx["pid"], stage, "fixing")
    return True


async def _done_gate(turn: Dict[str, Any], ctx: Dict[str, Any], html_changed: List[str]) -> bool:
    """Console errors on any changed HTML board → one automatic fix turn."""
    pid, cid = ctx["pid"], ctx["cid"]
    if not html_changed:
        return False
    console_errors = _optional("render", "console_errors")
    if not console_errors:
        return False
    _check(turn, cid, pid, "done_gate", "running")
    errors: Dict[str, List[str]] = {}
    for f in html_changed[:12]:
        try:
            errs = await asyncio.wait_for(console_errors(pid, f), RENDER_TIMEOUT)
        except asyncio.TimeoutError:
            _check(turn, cid, pid, "done_gate", "timeout", file=f)
            continue
        except Exception as exc:
            logger.warning("design: console_errors(%s) failed: %s", f, exc)
            continue
        if errs:
            errors[f] = [str(e)[:1000] for e in errs[:20]]
    if not errors:
        _check(turn, cid, pid, "done_gate", "pass")
        return False
    issues = [{"severity": "blocker", "file": f, "what": e} for f, errs in errors.items() for e in errs]
    _check(turn, cid, pid, "done_gate", "issues", issues=issues)
    listing = "\n".join(f"- {f}:\n" + "\n".join(f"    {e}" for e in errs) for f, errs in errors.items())
    text = ("The host loaded your changed boards headless and they log console errors. A board is not done "
            "until its console is clean. Fix only these errors, changing nothing else:\n\n" + listing)
    return await _start_fix_turn(turn, ctx, text, "done_gate")


async def _local_complete(prompt: str, max_tokens: int = 512, timeout: float = 180.0) -> Optional[str]:
    """One completion from the local model through the proxy; None when unavailable.

    Gated by `design.local_helpers` (default off): the verifier's model pass and
    auto-title would otherwise load the local model after every turn, even when
    the turn itself ran on a cloud engine."""
    if not config.get_nested("design.local_helpers", False):
        return None
    if not config.get_nested("proxy.enabled", False) or not config.get_nested("llamacpp.enabled", False):
        return None
    try:
        import llamacpp.state as llama_state
        model = llama_state.last_active_model() or config.get_nested("llamacpp.default_model", "") or "local"
    except Exception:
        model = "local"
    import aiohttp
    url = f"http://127.0.0.1:{config.proxy_port()}/v1/chat/completions"
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
            "temperature": 0.2, "stream": False}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.post(url, json=body) as r:
                if r.status != 200:
                    logger.info("design: local completion HTTP %d", r.status)
                    return None
                data = await r.json()
    except Exception as exc:
        logger.info("design: local completion failed: %s", exc)
        return None
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return None


_VERIFIER_RE = re.compile(r'<verifier-result\s+status="(pass|fail)"\s*/?>(.*?)(?:</verifier-result>|$)', re.S)


async def _verifier(turn: Dict[str, Any], ctx: Dict[str, Any], html_changed: List[str]) -> None:
    pid, cid = ctx["pid"], ctx["cid"]
    if not html_changed or not config.get_nested("design.verifier.enabled", True):
        return
    verify = _optional("render", "verify")
    if not verify:
        return
    _check(turn, cid, pid, "verifier", "running")
    try:
        res = await asyncio.wait_for(verify(pid, html_changed, screenshots=True), RENDER_TIMEOUT * 2)
    except asyncio.TimeoutError:
        _check(turn, cid, pid, "verifier", "timeout")
        return
    issues = list(res.get("issues") or [])

    # Model pass over the same evidence (local by default; skipped when no local model).
    if config.get_nested("design.verifier.is_local", True):
        lint = _optional("systems", "lint")
        findings: Any = []
        project = store.get_project(pid) or {}
        if lint and project.get("design_system_id"):
            try:
                findings = await asyncio.to_thread(lint, store.project_dir(pid), project["design_system_id"])
            except Exception as exc:
                logger.info("design: lint failed: %s", exc)
        prompt = prompt_builder.verifier_prompt(
            files_changed=", ".join(html_changed),
            console_log=json.dumps(res.get("console") or {}, ensure_ascii=False)[:20000],
            screenshots="\n".join(res.get("screenshots") or [])[:4000],
            lint_findings=json.dumps(findings, ensure_ascii=False, default=str)[:20000] if findings else "",
            pen_problems="",
            verifier_task="",
        ) + ("\n\nRender checks already found (confirm, don't repeat):\n" + json.dumps(issues)[:10000]
             if issues else "")
        reply = await _local_complete(prompt, max_tokens=1500)
        m = _VERIFIER_RE.search(reply or "")
        if m and m.group(1) == "fail":
            try:
                extra = json.loads(m.group(2).strip() or "[]")
                known = {(i.get("file"), i.get("what")) for i in issues}
                issues += [i for i in extra if isinstance(i, dict) and (i.get("file"), i.get("what")) not in known]
            except json.JSONDecodeError:
                pass

    serious = [i for i in issues if i.get("severity") in ("blocker", "major")]
    if not issues:
        _check(turn, cid, pid, "verifier", "pass")  # silent on pass: the UI may hide it
        return
    _check(turn, cid, pid, "verifier", "issues", issues=issues[:50])
    if serious:
        listing = "\n".join(f"- [{i.get('severity')}] {i.get('file', '')} {i.get('where', '')}: {i.get('what')}"
                            + (f" — fix: {i['fix']}" if i.get("fix") else "") for i in serious[:30])
        await _start_fix_turn(turn, ctx, "The host's verifier found problems in the boards you just changed. "
                                         "Fix exactly these, changing nothing else:\n\n" + listing, "verifier")


async def _thumbnail(pid: str) -> None:
    thumb = _optional("render", "thumbnail")
    if not thumb:
        return
    try:
        await asyncio.wait_for(thumb(pid), RENDER_TIMEOUT)
    except Exception as exc:
        logger.info("design: thumbnail for %s failed: %s", pid, exc)
        return
    store.set_project_fields(pid, thumbnail=True)
    events.publish(pid, "thumbnail", {"url": f"/api/design/projects/{pid}/thumbnail?t={int(time.time())}"})


_TITLE_RE = re.compile(r"\{.*\}", re.S)


async def _auto_title(turn: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    pid = ctx["pid"]
    project = store.get_project(pid) or {}
    if project.get("title_locked") or ctx.get("project_turns") or turn.get("auto"):
        return
    prompt = prompt_builder.title_prompt(ctx.get("user_text") or "", turn.get("text") or "",
                                         project.get("kind") or "prototype")
    reply = await _local_complete(prompt, max_tokens=60, timeout=60)
    if reply is None:
        logger.info("design: auto-title skipped (no local model)")
        return
    m = _TITLE_RE.search(reply)
    try:
        title = str(json.loads(m.group(0)).get("title") or "").strip() if m else ""
    except (json.JSONDecodeError, AttributeError):
        title = ""
    title = re.sub(r"[\r\n\t]+", " ", title).strip(" \"'.")[:80]
    if not title:
        return
    fresh = store.get_project(pid) or {}
    if fresh.get("title_locked"):
        return
    store.set_project_fields(pid, title=title)
    events.publish(pid, "title", {"title": title})


# ── Engines probe (GET /api/design/engines) ──────────────────────────────

_engines_cache: Dict[str, Any] = {"at": 0.0, "data": None}


def _version_of(binary: str) -> Optional[str]:
    try:
        out = subprocess.run(f'"{binary}" --version', shell=True, capture_output=True, text=True, timeout=8,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        line = (out.stdout or out.stderr or "").strip().splitlines()
        return line[0][:120] if line else None
    except Exception:
        return None


CLAUDE_MODELS = [("fable", "Fable (latest)"), ("opus", "Opus (latest)"),
                 ("sonnet", "Sonnet (latest)"), ("haiku", "Haiku (latest)")]


def _run_quiet(cmd: str, timeout: float = 20) -> str:
    try:
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout,
                             encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.stdout or ""
    except Exception:
        return ""


def _models_for(eid: str, binary: str) -> List[Dict[str, str]]:
    """Models the CLI itself says it accepts. Claude has no list command; its
    aliases always resolve to the latest model of that family."""
    if eid == "claude_code":
        return [{"id": m, "label": l} for m, l in CLAUDE_MODELS]
    if eid == "codex":
        try:
            data = json.loads(_run_quiet(f'"{binary}" debug models') or "{}")
        except json.JSONDecodeError:
            return []
        items = data.get("models", data) if isinstance(data, dict) else data
        return [{"id": m["slug"], "label": m.get("display_name") or m["slug"]}
                for m in items or [] if isinstance(m, dict) and m.get("slug") and m.get("visibility") == "list"]
    if eid == "antigravity":
        out = []
        for line in _run_quiet(f'"{binary}" models').splitlines():
            parts = line.strip().split("\t")
            if len(parts) >= 2 and parts[0] and " " not in parts[0]:
                out.append({"id": parts[0], "label": parts[1].strip()})
        return out
    return []


def _probe_engines() -> Dict[str, Any]:
    engines = []
    for eid, binname in BINARIES.items():
        path = shutil.which(binname)
        rec: Dict[str, Any] = {"id": eid, "available": bool(path),
                               "default_model": dchats.default_model(eid, False)}
        if path:
            v = _version_of(path)
            if v:
                rec["version"] = v
            rec["models"] = _models_for(eid, path)
        engines.append(rec)
    local: Dict[str, Any] = {"available": bool(config.get_nested("proxy.enabled", False)
                                               and config.get_nested("llamacpp.enabled", False)),
                             "models": [{"id": m, "label": m} for m in (config.get_nested("llamacpp.models", {}) or {})],
                             "default_model": dchats.default_model("", True)}
    if local["available"]:
        try:
            import llamacpp.state as llama_state
            local["model"] = llama_state.last_active_model() or config.get_nested("llamacpp.default_model")
        except Exception:
            pass
    return {"engines": engines, "local": local, "default_engine": dchats.default_engine(),
            "default_is_local": bool(config.get_nested("design.default_is_local", False))}


async def engines_status() -> Dict[str, Any]:
    if _engines_cache["data"] is None or time.monotonic() - _engines_cache["at"] > 30:
        _engines_cache["data"] = await asyncio.to_thread(_probe_engines)
        _engines_cache["at"] = time.monotonic()
    return _engines_cache["data"]
