"""Claude Code task handler: runs claude -p in a session folder. Ported from pythonmagic."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.session import session_store
from services.task.task_utils import (
    append_event,
    get_session_folder,
    get_session_id,
    get_session_namespace,
    get_task_id,
    is_cancelled,
    update_progress,
)

logger = logging.getLogger("telecode.services.task.handlers.claude_code")

def _describe_tool(name: str, tool_input: Dict[str, Any]) -> str:
    if not isinstance(tool_input, dict):
        return name
    for key in ("file_path", "path", "pattern", "command", "url"):
        if key in tool_input:
            return f"{name}: {tool_input[key]}"
    return name

def _handle_event(evt: Dict[str, Any], tool_calls: List[str]) -> None:
    t = evt.get("type")
    if t == "assistant":
        for block in evt.get("message", {}).get("content", []):
            btype = block.get("type")
            if btype == "text" and block.get("text", "").strip():
                append_event({"kind": "narrative", "text": block["text"].strip()})
            elif btype == "tool_use":
                name = block.get("name", "?")
                tool_calls.append(name)
                append_event({
                    "kind": "tool",
                    "tool": name,
                    "summary": _describe_tool(name, block.get("input", {})),
                })
                approx = min(0.9, 0.1 + 0.05 * len(tool_calls))
                update_progress(approx, f"step {len(tool_calls)}: {name}")
    elif t == "system" and evt.get("subtype") == "api_retry":
        append_event({
            "kind": "retry",
            "attempt": evt.get("attempt"),
            "max_retries": evt.get("max_retries"),
            "error": evt.get("error"),
        })

def claude_code_task(
    prompt: str,
    is_local: bool = False,
) -> Dict[str, Any]:
    """Run Claude Code in the session folder assigned by the queue."""
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
    resume_id = (meta.get("data") or {}).get("last_claude_session_id")

    cmd = [
        "claude", "-p", prompt,
        "--dangerously-skip-permissions",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]
    if resume_id:
        cmd += ["--resume", resume_id]

    env = None
    if is_local:
        import llamacpp.state as llama_state
        model = llama_state.last_active_model() or "local"
        proxy_url = f"http://localhost:{app_config.proxy_port()}"
        
        env = {
            **os.environ,
            "ANTHROPIC_BASE_URL": proxy_url,
            "ANTHROPIC_AUTH_TOKEN": "local", # or 'lmstudio' as per user snippet
            "ANTHROPIC_MODEL": model,
            "BASH_DEFAULT_TIMEOUT_MS": "1800000",
            "BASH_MAX_TIMEOUT_MS": "3600000",
            "DISABLE_PROMPT_CACHING": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
            "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
            "CLAUDE_CODE_USE_POWERSHELL_TOOL": "1",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "8192",
            "ENABLE_TOOL_SEARCH": "false",
        }
        logger.info(f"Local mode: using model {model} at {proxy_url}")

    logger.info(f"Claude Code starting: cwd={work_dir} session={sid} resume={resume_id or 'none'}")
    update_progress(0.05, "launching claude")
    append_event({
        "kind": "start",
        "session_id": sid,
        "cwd": str(work_dir),
        "prompt": prompt,
        "resumed": bool(resume_id),
        "resumed_claude_session_id": resume_id,
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
    final: Optional[Dict[str, Any]] = None
    captured_claude_sid: Optional[str] = None

    try:
        with log_path.open("w", encoding="utf-8") as log_fh:
            assert proc.stdout is not None
            for line in proc.stdout:
                log_fh.write(line)
                log_fh.flush()

                if is_cancelled():
                    logger.info("Cancellation requested — terminating Claude Code")
                    proc.terminate()
                    raise RuntimeError("Task cancelled")

                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue

                _handle_event(evt, tool_calls)

                evt_sid = evt.get("session_id")
                if evt_sid and evt_sid != captured_claude_sid:
                    captured_claude_sid = evt_sid
                    session_store.patch_data(sid, {"last_claude_session_id": evt_sid}, namespace=ns)

                if evt.get("type") == "result":
                    final = evt

        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()

    stderr = (proc.stderr.read() if proc.stderr else "") or ""
    if proc.returncode != 0 and final is None:
        raise RuntimeError(f"claude exited with code {proc.returncode}: {stderr.strip()[:500]}")

    claude_session_id = captured_claude_sid or (final or {}).get("session_id")
    fin = final or {}

    usage = fin.get("usage") or {}
    cache_reads = usage.get("cache_read_input_tokens") or 0
    cache_writes = usage.get("cache_creation_input_tokens") or 0
    total_input = (usage.get("input_tokens") or 0) + cache_reads + cache_writes

    update_progress(1.0, "done")
    append_event({
        "kind": "done",
        "tool_count": len(tool_calls),
        "cost_usd": fin.get("total_cost_usd"),
        "input_tokens": total_input,
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": cache_reads,
        "cache_write_tokens": cache_writes,
    })

    return {
        "result": fin.get("result", ""),
        "session_id": sid,
        "claude_session_id": claude_session_id,
        "cost_usd": fin.get("total_cost_usd"),
        "duration_ms": fin.get("duration_ms"),
        "duration_api_ms": fin.get("duration_api_ms"),
        "num_turns": fin.get("num_turns"),
        "tokens": {
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
            "cache_read": cache_reads,
            "cache_write": cache_writes,
            "total_input_incl_cache": total_input,
        },
        "tool_calls": tool_calls,
        "log_path": str(log_path),
    }
