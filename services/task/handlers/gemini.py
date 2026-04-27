"""Gemini task handler: runs gemini -p in a session folder. Ported from pythonmagic/claude pattern."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.session import session_store
from services.task.agent_prompt import resolve_prompt
from services.task.task_utils import (
    append_event,
    get_session_folder,
    get_session_id,
    get_session_namespace,
    get_task_id,
    is_cancelled,
    update_progress,
)

logger = logging.getLogger("telecode.services.task.handlers.gemini")

def _describe_tool(name: str, tool_input: Dict[str, Any]) -> str:
    if not isinstance(tool_input, dict):
        return name
    for key in ("file_path", "path", "pattern", "command", "url"):
        if key in tool_input:
            return f"{name}: {tool_input[key]}"
    return name

def _handle_event(evt: Dict[str, Any], tool_calls: List[str]) -> None:
    etype = evt.get("type")
    
    # 1. Narrative events (already handled in loop for accumulated_text, but we still want individual events)
    # Actually we'll handle narrative append_event in the loop too to keep them in sync.
    
    if etype == "tool_use":
        name = evt.get("tool_name", "?")
        tool_calls.append(name)
        append_event({
            "kind": "tool",
            "tool": name,
            "summary": _describe_tool(name, evt.get("parameters", {})),
        })
        update_progress(min(0.9, 0.1 + 0.05 * len(tool_calls)), f"step {len(tool_calls)}: {name}")

    elif etype == "thought":
        thought = evt.get("text", "").strip()
        if thought: append_event({"kind": "thought", "text": thought})
    elif etype == "tool_call":
        name = evt.get("name", "?")
        tool_calls.append(name)
        append_event({
            "kind": "tool",
            "tool": name,
            "summary": _describe_tool(name, evt.get("input", {})),
        })
        update_progress(min(0.9, 0.1 + 0.05 * len(tool_calls)), f"step {len(tool_calls)}: {name}")
    
    elif etype == "assistant":
        # Anthropic-style blocks
        for block in evt.get("message", {}).get("content", []):
            btype = block.get("type")
            if btype == "tool_use":
                name = block.get("name", "?")
                tool_calls.append(name)
                append_event({
                    "kind": "tool",
                    "tool": name,
                    "summary": _describe_tool(name, block.get("input", {})),
                })
                update_progress(min(0.9, 0.1 + 0.05 * len(tool_calls)), f"step {len(tool_calls)}: {name}")
    
    # 4. System events
    elif etype == "system" and evt.get("subtype") == "api_retry":
        append_event({
            "kind": "retry",
            "attempt": evt.get("attempt"),
            "max_retries": evt.get("max_retries"),
            "error": evt.get("error"),
        })

def gemini_task(
    prompt: Optional[str] = None,
    is_local: bool = False,
    *,
    agent: Optional[Dict[str, Any]] = None,
    job: Optional[Dict[str, Any]] = None,
    agent_files: Optional[List[Any]] = None,
    job_files: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Run Gemini CLI in the session folder.

    Accepts either a pre-rendered `prompt` string or structured fields
    (`agent`, `job`, optional `agent_files`/`job_files`) which are rendered
    into the shared <agent_task> XML via services.task.agent_prompt.
    """
    prompt = resolve_prompt({
        "prompt": prompt,
        "agent": agent,
        "job": job,
        "agent_files": agent_files,
        "job_files": job_files,
    })
    task_id = get_task_id() or "no-task"
    
    import config as app_config
    log_dir = Path(app_config._settings_dir()) / "data" / "task_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{task_id}.jsonl"

    sid = get_session_id()
    ns = get_session_namespace()
    work_dir = get_session_folder()
    if not sid or not work_dir:
        raise RuntimeError("No session bound to this task")

    meta = session_store.get(sid, namespace=ns) or {}
    resume_id = (meta.get("data") or {}).get("last_gemini_session_id")

    cmd = [
        "gemini", "-p", json.dumps(prompt),
        "--yolo",
        "--output-format", "stream-json",
    ]
    if resume_id:
        cmd += ["--resume", resume_id]

    env = None
    if is_local:
        import llamacpp.state as llama_state
        model = llama_state.last_active_model() or "local"
        proxy_url = f"http://localhost:{app_config.proxy_port()}"
        
        # Primary env var for Gemini CLI custom endpoints
        env = {
            **os.environ,
            "GOOGLE_API_KEY": "local",
            "GEMINI_API_KEY": "local",
            "GOOGLE_GEMINI_BASE_URL": proxy_url,
        }
        cmd += ["-m", model]
        logger.info(f"Local mode: using model {model} at {proxy_url}")

    logger.info(f"Gemini starting: cwd={work_dir} session={sid} resume={resume_id or 'none'}")
    update_progress(0.05, "launching gemini")
    append_event({
        "kind": "start",
        "session_id": sid,
        "cwd": str(work_dir),
        "prompt": prompt,
        "resumed": bool(resume_id),
        "resumed_gemini_session_id": resume_id,
        "is_local": is_local,
    })

    proc = subprocess.Popen(
        cmd,
        cwd=str(work_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,
        shell=True,
    )

    tool_calls: List[str] = []
    accumulated_text: List[str] = []
    num_assistant_messages = 0
    last_event_was_tool = False
    final: Optional[Dict[str, Any]] = None
    captured_gemini_sid: Optional[str] = None

    try:
        with log_path.open("w", encoding="utf-8") as log_fh:
            assert proc.stdout is not None
            for line in proc.stdout:
                log_fh.write(line)
                log_fh.flush()

                if is_cancelled():
                    logger.info("Cancellation requested — terminating Gemini")
                    proc.terminate()
                    raise RuntimeError("Task cancelled")

                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = evt.get("type")
                # Manage accumulated_text here for better turn control
                if etype == "message" and evt.get("role") == "assistant":
                    content = evt.get("content", "")
                    if content:
                        # If this follows a tool result OR if it's not a delta, it's a new turn/block
                        is_delta = evt.get("delta", False)
                        if last_event_was_tool or not is_delta or not accumulated_text:
                            accumulated_text.append(content)
                            num_assistant_messages += 1
                        else:
                            accumulated_text[-1] += content
                        
                        append_event({"kind": "narrative", "text": content})
                        last_event_was_tool = False
                elif etype in ("tool_use", "tool_call"):
                    last_event_was_tool = True
                elif etype == "tool_result":
                    # After a tool result, the next assistant message is definitely a new turn
                    last_event_was_tool = True

                # Still call _handle_event for events we don't handle here (progress, events)
                _handle_event(evt, tool_calls) 

                evt_sid = evt.get("session_id")
                if evt_sid and evt_sid != captured_gemini_sid:
                    captured_gemini_sid = evt_sid
                    session_store.patch_data(sid, {"last_gemini_session_id": evt_sid}, namespace=ns)

                if evt.get("type") == "result":
                    final = evt

        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()

    stderr = (proc.stderr.read() if proc.stderr else "") or ""
    if proc.returncode != 0 and final is None:
        raise RuntimeError(f"gemini exited with code {proc.returncode}: {stderr.strip()[:500]}")

    gemini_session_id = captured_gemini_sid or (final or {}).get("session_id")
    fin = final or {}
    stats = fin.get("stats") or {}
    
    # Map stats to UI expected format
    tokens = {
        "input": stats.get("input_tokens") or stats.get("input") or 0,
        "output": stats.get("output_tokens") or 0,
        "cached": stats.get("cached") or 0,
        "total": stats.get("total_tokens") or 0,
    }
    
    cost_usd = fin.get("total_cost_usd") or stats.get("cost_usd") or 0
    duration_ms = stats.get("duration_ms") or fin.get("duration_ms") or 0
    num_turns = fin.get("num_turns") or stats.get("num_turns") or num_assistant_messages or 0

    update_progress(1.0, "done")
    append_event({
        "kind": "done",
        "tool_count": len(tool_calls),
        "cost_usd": cost_usd,
        "num_turns": num_turns,
        "input_tokens": tokens["input"],
        "output_tokens": tokens["output"],
    })

    return {
        "result": "\n\n".join(accumulated_text) or fin.get("result", ""),
        "session_id": sid,
        "gemini_session_id": gemini_session_id,
        "cost_usd": cost_usd,
        "duration_ms": duration_ms,
        "duration_api_ms": stats.get("duration_api_ms") or 0,
        "num_turns": num_turns,
        "tokens": tokens,
        "tool_calls": tool_calls,
        "log_path": str(log_path),
    }

