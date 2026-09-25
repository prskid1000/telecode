"""The one Engine Runner: ``run_engine(EngineRequest) -> EngineResult``.

Synchronous — it runs on a task-queue worker thread. Everything that used to
be copied across the three CLI handlers and TeleDesign lives here:

1. adapter builds argv/env/stdin (``services/engine/adapters``)
2. ``start`` event, progress "launching <cli>"
3. spawn through :mod:`services.engine.spawn` — no shell, Job-bound before
   the CLI runs, own process group
4. prompt on stdin, stdin closed; stderr drained on a thread (an unread
   stderr pipe fills and deadlocks the child)
5. stdout copied line by line to the raw log (``data/task_logs/…``, which
   TeleDesign tails), parsed by the adapter into normalised events
6. a watcher thread stops the tree on cancel (``cancel_check``) or wall-clock
   timeout even when the CLI prints nothing: graceful CTRL_BREAK, grace
   period, then ``TerminateJobObject``/tree-kill
7. result + usage normalised, ``usage``/``done`` (or ``error``) events

Budgets (P2): ``max_seconds`` is a second wall clock on the same watcher and
``max_tokens`` is checked on every normalised ``usage`` event (budget tokens =
input + cache writes + output); either stops the tree the same graceful way
and raises :class:`EngineBudgetExceeded` ("budget_exceeded: …"). ``max_usd``
is enforced by Claude itself (``--max-budget-usd``). The final ``usage``
event is emitted *before* the adapter's ``finish`` so a run that ends in an
error still reports what it spent.

Cancellation raises :class:`EngineCancelled` ("Task cancelled"), a timeout
:class:`EngineTimeout` ("timeout"), a CLI failure :class:`EngineError` with
the CLI's own stderr — the messages the handlers always raised, which
TeleDesign's stale-resume detection matches.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from services.engine.adapters import get_adapter
from services.engine.adapters.base import ParseState
from services.engine.drain import StreamDrain
from services.engine.spawn import spawn
from services.engine.types import (EngineBudgetExceeded, EngineCancelled, EngineError, EngineRequest,
                                   EngineResult, EngineTimeout)

logger = logging.getLogger("telecode.services.engine.runner")

PROMPT_HEAD_CHARS = 2048
_WATCH_SEC = 0.2


def prompt_digest(prompt: str) -> Dict[str, Any]:
    """What the ``start`` event keeps of a prompt: first 2 KB (legacy
    ``prompt`` key), length, sha256. Design prompts run to 56 KB."""
    text = prompt or ""
    return {
        "prompt": text[:PROMPT_HEAD_CHARS],
        "prompt_len": len(text),
        "prompt_sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest(),
        "prompt_truncated": len(text) > PROMPT_HEAD_CHARS,
    }


def budget_tokens(tokens: Optional[Dict[str, Any]]) -> int:
    """input + cache writes + output (cache reads excluded) — see services.run.budget."""
    t = tokens or {}
    return int(t.get("input") or 0) + int(t.get("cache_write") or 0) + int(t.get("output") or 0)


def _safe(fn, *args) -> None:
    if fn is None:
        return
    try:
        fn(*args)
    except Exception:
        logger.exception("engine sink %r failed", getattr(fn, "__name__", fn))


def run_engine(req: EngineRequest) -> EngineResult:
    adapter = get_adapter(req.engine)
    if req.engine == "codex" and req.last_msg_path is None:
        base = Path(req.log_path) if req.log_path else Path(req.cwd) / ".codex_last_message"
        req.last_msg_path = base.with_name(base.stem + ".codex_last_message.txt")

    def emit(evt: Dict[str, Any]) -> None:
        _safe(req.on_event, evt)

    def progress(p: float, msg: str) -> None:
        _safe(req.on_progress, p, msg)

    launch = adapter.build(req)
    logger.info(f"{adapter.label} starting: cwd={req.cwd} session={req.session_id} "
                f"resume={req.resume_id or 'none'}")
    progress(0.05, f"launching {adapter.label}")
    emit({"kind": "start", "engine": req.engine, "session_id": req.session_id, "cwd": str(req.cwd),
          **prompt_digest(req.prompt), "resumed": bool(req.resume_id),
          adapter.resume_start_key: req.resume_id, "is_local": req.is_local,
          **({"model": req.model} if req.model else {}),
          **({"fork": True} if req.fork and req.resume_id else {}),
          **({"budget": {k: v for k, v in (("max_usd", req.max_usd), ("max_tokens", req.max_tokens),
                                           ("max_seconds", req.max_seconds)) if v}}
             if (req.max_usd or req.max_tokens or req.max_seconds) else {})})

    started = time.monotonic()
    try:
        sp = spawn(launch.argv, cwd=Path(req.cwd), env=launch.env)
    except EngineError as exc:
        emit({"kind": "error", "message": str(exc)})
        for p in launch.cleanup:
            with contextlib.suppress(OSError):
                p.unlink()
        raise

    stop_lock = threading.Lock()
    stop: Dict[str, Optional[str]] = {"reason": None, "detail": None}

    def request_stop(reason: str = "cancelled", detail: Optional[str] = None) -> None:
        """Non-blocking: the graceful → kill sequence runs on its own thread."""
        with stop_lock:
            if stop["reason"]:
                return
            stop["reason"] = reason
            stop["detail"] = detail
        logger.info(f"{adapter.label}: stopping pid {sp.pid} ({reason})")
        threading.Thread(target=sp.stop, args=(req.kill_grace_sec,), daemon=True,
                         name=f"engine-stop-{sp.pid}").start()

    _safe(req.on_spawn, sp.pid, request_stop)
    proc = sp.proc
    try:
        proc.stdin.write(launch.stdin)
        proc.stdin.close()
    except (OSError, ValueError, AttributeError):
        pass  # stopped before reading stdin; handled below
    drain = StreamDrain(proc.stderr)

    finished = threading.Event()

    def watch() -> None:
        while not finished.wait(_WATCH_SEC):
            if req.cancel_check is not None:
                try:
                    if req.cancel_check():
                        request_stop("cancelled")
                        return
                except Exception:
                    pass
            elapsed = time.monotonic() - started
            if req.timeout_sec and elapsed > req.timeout_sec:
                request_stop("timeout")
                return
            if req.max_seconds and elapsed > req.max_seconds:
                request_stop("budget", f"wall clock {elapsed:.0f}s > max_seconds {req.max_seconds:g}")
                return

    threading.Thread(target=watch, daemon=True, name=f"engine-watch-{sp.pid}").start()

    st = ParseState()
    last_sid: Optional[str] = None
    tool_n = 0
    log_cm = open(req.log_path, "w", encoding="utf-8") if req.log_path else contextlib.nullcontext()
    try:
        with log_cm as log_fh:
            for line in proc.stdout:
                if log_fh is not None:
                    log_fh.write(line)
                    log_fh.flush()
                if stop["reason"]:
                    continue  # keep draining until the tree is gone; parse nothing
                if req.cancel_check is not None and req.cancel_check():
                    request_stop("cancelled")
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    evt = None
                if not isinstance(evt, dict):
                    if line.strip():
                        adapter.raw_line(line.strip(), st)
                    continue
                for e in adapter.parse(evt, st):
                    if e.get("kind") == "tool":
                        tool_n += 1
                        progress(min(0.9, 0.1 + 0.05 * tool_n), f"step {tool_n}: {e.get('tool')}")
                    emit(e)
                    if e.get("kind") == "usage" and req.max_tokens:
                        used = budget_tokens(e.get("tokens"))
                        if used > req.max_tokens:
                            request_stop("budget", f"tokens {used} > max_tokens {req.max_tokens}")
                if st.session_id and st.session_id != last_sid:
                    last_sid = st.session_id
                    _safe(req.on_resume_id, st.session_id)
        try:
            proc.wait(timeout=adapter.stdin_close_wait)
        except Exception:
            pass
    finally:
        finished.set()
        if sp.alive():
            sp.kill_tree()
        _safe(req.on_exit, sp.pid)
        sp.close()
        for p in launch.cleanup:
            with contextlib.suppress(OSError):
                p.unlink()

    wall_ms = int((time.monotonic() - started) * 1000)
    reason = stop["reason"]
    if reason is None and req.cancel_check is not None:
        with contextlib.suppress(Exception):
            if req.cancel_check():
                reason = "cancelled"
    if reason == "timeout":
        emit({"kind": "error", "message": f"timeout after {req.timeout_sec}s"})
        raise EngineTimeout()
    if reason == "budget":
        exc = EngineBudgetExceeded(stop["detail"] or "")
        emit({"kind": "error", "message": str(exc), "budget_exceeded": True})
        raise exc
    if reason:
        emit({"kind": "error", "message": "cancelled"})
        raise EngineCancelled()

    stderr = drain.text()
    for e in adapter.trailing_events(st):
        emit(e)
    ue = adapter.usage_event(st)
    if ue:
        emit(ue)
    try:
        result = adapter.finish(req, st, getattr(proc, "returncode", None), stderr, wall_ms)
    except EngineError as exc:
        emit({"kind": "error", "message": str(exc),
              **({"budget_exceeded": True} if isinstance(exc, EngineBudgetExceeded) else {})})
        raise
    result.log_path = str(req.log_path) if req.log_path else None
    progress(1.0, "done")
    tok = result.tokens or {}
    emit({"kind": "done", "tool_count": len(result.tool_calls), "cost_usd": result.cost_usd,
          "num_turns": result.num_turns, "input_tokens": tok.get("total_input_incl_cache", 0),
          "output_tokens": tok.get("output", 0), "cache_read_tokens": tok.get("cache_read", 0),
          "cache_write_tokens": tok.get("cache_write", 0)})
    return result
