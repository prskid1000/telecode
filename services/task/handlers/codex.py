"""Codex task handler: runs `codex exec --json` in a session folder.

Mirrors services.task.handlers.claude_code on purpose — same signature, same
flow, same event surface, so the executor / heartbeat / routine call sites are
oblivious to which CLI they're driving. When OpenAI's flag/event surface
shifts (e.g. new event kinds in `codex exec --json`), only this file needs
updating.

Codex parallels Claude:
  --dangerously-bypass-approvals-and-sandbox  ~ --dangerously-skip-permissions
  --sandbox danger-full-access               (no Claude equivalent)
  --json                                     ~ --output-format stream-json
  -C <dir>                                   ~ cwd
  --output-last-message <path>               (extra fallback for final text)
  codex exec resume <SID> "<prompt>"         ~ --resume <ID>
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.session import session_store
from services.task.agent_prompt import resolve_prompt
from services.task.staging import stage_for_run
from services.task.task_utils import (
    append_event,
    get_session_folder,
    get_session_id,
    get_session_namespace,
    get_task_id,
    is_cancelled,
    update_progress,
)

logger = logging.getLogger("telecode.services.task.handlers.codex")


def _describe_item(item: Dict[str, Any]) -> str:
    """Best-effort one-liner for a codex `item.completed` payload."""
    if not isinstance(item, dict):
        return "item"
    itype = item.get("type") or item.get("kind") or "item"
    for key in ("command", "path", "file_path", "url", "tool", "name"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return f"{itype}: {v}"
    return itype


def _handle_event(evt: Dict[str, Any], tool_calls: List[str]) -> Optional[str]:
    """Map codex JSONL events to telecode event kinds.

    Returns a captured session id if the event carries one.
    """
    t = evt.get("type") or ""
    captured_sid: Optional[str] = None

    if t == "thread.started":
        thread = evt.get("thread") or {}
        sid = thread.get("id") or evt.get("thread_id")
        if isinstance(sid, str) and sid:
            captured_sid = sid

    elif t == "item.completed":
        item = evt.get("item") or {}
        itype = item.get("type") or ""
        if itype == "assistant_message":
            text = (item.get("text") or item.get("content") or "").strip()
            if text:
                append_event({"kind": "narrative", "text": text})
        elif itype in ("command_executed", "file_change", "mcp_tool_call", "tool_use", "patch"):
            tool_calls.append(itype)
            append_event({
                "kind": "tool",
                "tool": itype,
                "summary": _describe_item(item),
            })
            approx = min(0.9, 0.1 + 0.05 * len(tool_calls))
            update_progress(approx, f"step {len(tool_calls)}: {itype}")
        elif itype == "reasoning":
            text = (item.get("text") or "").strip()
            if text:
                append_event({"kind": "narrative", "text": text})

    elif t == "turn.failed" or t == "error":
        append_event({
            "kind": "retry",
            "attempt": evt.get("attempt"),
            "max_retries": evt.get("max_retries"),
            "error": evt.get("error") or evt.get("message"),
        })

    return captured_sid


def codex_task(
    prompt: Optional[str] = None,
    is_local: bool = False,
    *,
    agent_id: Optional[str] = None,
    agent: Optional[Dict[str, Any]] = None,
    job: Optional[Dict[str, Any]] = None,
    agent_files: Optional[List[Any]] = None,
    job_files: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Run Codex (`codex exec --json`) in the session folder.

    Signature mirrors claude_code_task verbatim so the executor / routine /
    heartbeat layers are engine-agnostic.
    """
    prompt = resolve_prompt({
        "prompt": prompt,
        "agent": agent,
        "job": job,
        "agent_files": agent_files,
        "job_files": job_files,
    })
    if not agent_id and isinstance(agent, dict):
        agent_id = agent.get("id")

    task_id = get_task_id() or "no-task"

    import config as app_config
    log_dir = Path(app_config._settings_dir()) / "data" / "task_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{task_id}.jsonl"
    last_msg_path = log_dir / f"{task_id}.codex_last_message.txt"

    sid = get_session_id()
    ns = get_session_namespace()
    work_dir = get_session_folder()
    if not sid or not work_dir:
        raise RuntimeError("No session bound to this task")

    meta = session_store.get(sid, namespace=ns) or {}
    resume_id = (meta.get("data") or {}).get("last_codex_session_id")

    with stage_for_run(agent_id, sid, work_dir, engine="codex"):
        return _run_codex_subprocess(
            prompt=prompt,
            work_dir=work_dir,
            sid=sid,
            ns=ns,
            resume_id=resume_id,
            is_local=is_local,
            log_path=log_path,
            last_msg_path=last_msg_path,
        )


def _build_codex_argv(
    *,
    prompt: str,
    work_dir: Path,
    resume_id: Optional[str],
    last_msg_path: Path,
    model: Optional[str],
) -> List[str]:
    common = [
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "--sandbox", "danger-full-access",
        "--skip-git-repo-check",
        "-C", str(work_dir),
        "--output-last-message", str(last_msg_path),
    ]
    if model:
        common += ["--model", model]

    if resume_id:
        # codex exec resume <SESSION_ID> [flags...] "<prompt>"
        return ["codex", "exec", "resume", resume_id, *common, prompt]
    return ["codex", "exec", *common, prompt]


def _run_codex_subprocess(
    *,
    prompt: str,
    work_dir: Path,
    sid: str,
    ns: Optional[str],
    resume_id: Optional[str],
    is_local: bool,
    log_path: Path,
    last_msg_path: Path,
) -> Dict[str, Any]:
    import config as app_config

    env = None
    model: Optional[str] = None
    if is_local:
        import llamacpp.state as llama_state
        model = llama_state.last_active_model() or "local"
        proxy_url = f"http://localhost:{app_config.proxy_port()}/v1"
        env = {
            **os.environ,
            "OPENAI_BASE_URL": proxy_url,
            "OPENAI_API_KEY": "local",
            "CODEX_API_KEY": "local",
        }
        logger.info(f"Local mode: codex pointed at {proxy_url} (model={model})")

    cmd = _build_codex_argv(
        prompt=prompt,
        work_dir=work_dir,
        resume_id=resume_id,
        last_msg_path=last_msg_path,
        model=model,
    )

    logger.info(f"Codex starting: cwd={work_dir} session={sid} resume={resume_id or 'none'}")
    update_progress(0.05, "launching codex")
    append_event({
        "kind": "start",
        "session_id": sid,
        "cwd": str(work_dir),
        "prompt": prompt,
        "resumed": bool(resume_id),
        "resumed_codex_session_id": resume_id,
        "is_local": is_local,
    })

    creation = 0
    if os.name == "nt":
        creation = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(
        cmd,
        cwd=str(work_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,
        shell=True,
        creationflags=creation,
    )

    tool_calls: List[str] = []
    final_usage: Dict[str, Any] = {}
    captured_codex_sid: Optional[str] = None
    accumulated_text: List[str] = []
    saw_turn_completed = False

    try:
        with log_path.open("w", encoding="utf-8") as log_fh:
            assert proc.stdout is not None
            for line in proc.stdout:
                log_fh.write(line)
                log_fh.flush()

                if is_cancelled():
                    logger.info("Cancellation requested — terminating Codex")
                    proc.terminate()
                    raise RuntimeError("Task cancelled")

                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    if line.strip():
                        accumulated_text.append(line.strip())
                    continue

                evt_sid = _handle_event(evt, tool_calls)
                if evt_sid and evt_sid != captured_codex_sid:
                    captured_codex_sid = evt_sid
                    session_store.patch_data(sid, {"last_codex_session_id": evt_sid}, namespace=ns)

                if evt.get("type") == "turn.completed":
                    saw_turn_completed = True
                    usage = evt.get("usage") or {}
                    if isinstance(usage, dict):
                        final_usage = usage

        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()

    stderr = (proc.stderr.read() if proc.stderr else "") or ""
    if proc.returncode != 0 and not saw_turn_completed and not accumulated_text:
        raise RuntimeError(f"codex exited with code {proc.returncode}: {stderr.strip()[:500]}")

    # Final assistant text: prefer --output-last-message file; fall back to
    # whatever we accumulated from non-JSON lines.
    final_text = ""
    try:
        if last_msg_path.exists():
            final_text = last_msg_path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.warning(f"Could not read codex last-message file: {exc}")
    if not final_text:
        final_text = "\n".join(accumulated_text)

    if not saw_turn_completed and accumulated_text:
        for txt in accumulated_text:
            append_event({"kind": "narrative", "text": txt})

    # Codex usage fields are not 100% stable across versions — normalize defensively.
    input_tokens = final_usage.get("input_tokens") or final_usage.get("prompt_tokens") or 0
    cache_reads = (
        final_usage.get("cached_input_tokens")
        or final_usage.get("cache_read_input_tokens")
        or 0
    )
    output_tokens = final_usage.get("output_tokens") or final_usage.get("completion_tokens") or 0
    total_input = input_tokens + cache_reads

    update_progress(1.0, "done")
    append_event({
        "kind": "done",
        "tool_count": len(tool_calls),
        "cost_usd": final_usage.get("total_cost_usd"),
        "num_turns": final_usage.get("num_turns"),
        "input_tokens": total_input,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_reads,
        "cache_write_tokens": 0,
    })

    return {
        "result": final_text,
        "session_id": sid,
        "codex_session_id": captured_codex_sid,
        "cost_usd": final_usage.get("total_cost_usd") or 0,
        "duration_ms": final_usage.get("duration_ms") or 0,
        "duration_api_ms": final_usage.get("duration_api_ms") or 0,
        "num_turns": final_usage.get("num_turns") or 0,
        "tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "cache_read": cache_reads,
            "cache_write": 0,
            "total_input_incl_cache": total_input,
        },
        "tool_calls": tool_calls,
        "log_path": str(log_path),
    }
