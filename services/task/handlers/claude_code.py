"""Claude Code task handler: runs claude -p in a session folder. Ported from pythonmagic."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

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
    kill_proc_tree as _kill_tree,
    make_resume_store,
    prompt_digest,
    read_resume_id,
    track_process,
    untrack_process,
    update_progress,
    StreamDrain,
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
    prompt: Optional[str] = None,
    is_local: bool = False,
    *,
    agent_id: Optional[str] = None,
    agent: Optional[Dict[str, Any]] = None,
    job: Optional[Dict[str, Any]] = None,
    agent_files: Optional[List[Any]] = None,
    job_files: Optional[List[Any]] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Run Claude Code in the session folder assigned by the queue.

    Accepts either a pre-rendered `prompt` string or structured fields
    (`agent`, `job`, optional `agent_files`/`job_files`) which are rendered
    into the shared <agent_task> XML via services.task.agent_prompt.

    When `agent_id` is provided, SOUL/USER/MEMORY are staged into the
    workspace and the agent's AGENT.md is handed to the CLI with
    `--append-system-prompt-file` (never written over the workspace's own
    CLAUDE.md); changes are merged back afterwards. See services.task.staging.

    `model` (optional) is a Claude alias or full model name in cloud mode, the
    llama model name in local mode. The resume id is scoped per
    (workspace, agent, engine[, local]) — see task_utils.resume_scope_key.
    """
    prompt = resolve_prompt({
        "prompt": prompt,
        "agent": agent,
        "job": job,
        "agent_files": agent_files,
        "job_files": job_files,
    })
    # Resolve agent_id: explicit param wins, else fall back to agent dict
    if not agent_id and isinstance(agent, dict):
        agent_id = agent.get("id")

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
    resume_id = read_resume_id(meta.get("data"), agent_id, "claude_code", is_local)
    resume_store = make_resume_store(sid, ns, agent_id, "claude_code", is_local)

    agent_md_file = None
    if agent_id:
        agent_md_file = (Path(app_config._settings_dir()) / "data" / "runtime"
                         / "agent_prompts" / f"{task_id}.md")

    with stage_for_run(agent_id, sid, work_dir, engine="claude", agent_md_file=agent_md_file):
        append_file = agent_md_file if (agent_md_file and agent_md_file.exists()
                                        and agent_md_file.stat().st_size > 0) else None
        return _run_claude_subprocess(
            prompt=prompt,
            work_dir=work_dir,
            sid=sid,
            ns=ns,
            resume_id=resume_id,
            is_local=is_local,
            log_path=log_path,
            model=model,
            resume_store=resume_store,
            append_system_prompt_file=append_file,
        )


def _run_claude_subprocess(
    *,
    prompt: str,
    work_dir: Path,
    sid: str,
    ns: Optional[str],
    resume_id: Optional[str],
    is_local: bool,
    log_path: Path,
    model: Optional[str] = None,
    resume_store: Optional[Callable[[str], None]] = None,
    append_system_prompt_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Spawn `claude -p` and stream its events.

    `resume_store(id)` persists a new CLI session id; when omitted (the
    TeleDesign path) the legacy flat `last_claude_session_id` key is written.
    `append_system_prompt_file` passes the agent's AGENT.md explicitly.
    """
    # The prompt goes on stdin, not argv: `claude -p` reads it from there, and
    # a design prompt (charter + design system + comments) overflows the
    # Windows command line (~8 KB through a .cmd shim) on this shell=True spawn.
    cmd = [
        "claude", "-p",
        "--dangerously-skip-permissions",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]
    if resume_id:
        cmd += ["--resume", resume_id]
    # Cloud: `model` is a Claude alias (fable/opus/sonnet/haiku) or full name.
    # Local: it names the llama model and travels as ANTHROPIC_MODEL below.
    if model and not is_local:
        cmd += ["--model", model]
    if append_system_prompt_file:
        cmd += ["--append-system-prompt-file", str(append_system_prompt_file)]

    import config as app_config
    env = None
    if is_local:
        import llamacpp.state as llama_state
        model = model or llama_state.last_active_model() or "local"
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
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(app_config.tasks_local_max_output_tokens()),
            "ENABLE_TOOL_SEARCH": "false",
        }
        logger.info(f"Local mode: using model {model} at {proxy_url}")

    logger.info(f"Claude Code starting: cwd={work_dir} session={sid} resume={resume_id or 'none'}")
    update_progress(0.05, "launching claude")
    append_event({
        "kind": "start",
        "session_id": sid,
        "cwd": str(work_dir),
        **prompt_digest(prompt),
        "resumed": bool(resume_id),
        "resumed_claude_session_id": resume_id,
        "is_local": is_local,
    })

    creation = 0
    if os.name == "nt":
        creation = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(
        cmd,
        cwd=str(work_dir),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        encoding="utf-8",
        bufsize=1,
        shell=True,
        creationflags=creation,
    )
    tracked_pid = track_process(proc)
    assert proc.stdin is not None
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except OSError:
        pass  # killed before reading stdin (cancel/timeout); handled below
    stderr_drain = StreamDrain(proc.stderr)

    tool_calls: List[str] = []
    final: Optional[Dict[str, Any]] = None
    captured_claude_sid: Optional[str] = None
    accumulated_text: List[str] = []

    try:
        with log_path.open("w", encoding="utf-8") as log_fh:
            assert proc.stdout is not None
            for line in proc.stdout:
                log_fh.write(line)
                log_fh.flush()

                if is_cancelled():
                    logger.info("Cancellation requested — terminating Claude Code")
                    _kill_tree(proc)
                    raise RuntimeError("Task cancelled")

                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    if line.strip():
                        accumulated_text.append(line.strip())
                    continue

                _handle_event(evt, tool_calls)

                evt_sid = evt.get("session_id")
                if evt_sid and evt_sid != captured_claude_sid:
                    captured_claude_sid = evt_sid
                    if resume_store:
                        resume_store(evt_sid)
                    else:
                        session_store.patch_data(sid, {"last_claude_session_id": evt_sid}, namespace=ns)

                if evt.get("type") == "result":
                    final = evt

        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            _kill_tree(proc)
        untrack_process(tracked_pid)

    if is_cancelled():
        raise RuntimeError("Task cancelled")

    stderr = stderr_drain.text()
    if proc.returncode != 0 and final is None and not accumulated_text:
        raise RuntimeError(f"claude exited with code {proc.returncode}: {stderr.strip()[:500]}")

    claude_session_id = captured_claude_sid or (final or {}).get("session_id")
    fin = final or {}

    usage = fin.get("usage") or {}
    cache_reads = usage.get("cache_read_input_tokens") or 0
    cache_writes = usage.get("cache_creation_input_tokens") or 0
    total_input = (usage.get("input_tokens") or 0) + cache_reads + cache_writes

    update_progress(1.0, "done")
    
    # Narrative events for raw lines if no JSON narrative found
    if not final and accumulated_text:
        for txt in accumulated_text:
            append_event({"kind": "narrative", "text": txt})

    append_event({
        "kind": "done",
        "tool_count": len(tool_calls),
        "cost_usd": fin.get("total_cost_usd"),
        "num_turns": fin.get("num_turns"),
        "input_tokens": total_input,
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": cache_reads,
        "cache_write_tokens": cache_writes,
    })

    return {
        "result": fin.get("result") or "\n".join(accumulated_text),
        "session_id": sid,
        "claude_session_id": claude_session_id,
        "cost_usd": fin.get("total_cost_usd") or 0,
        "duration_ms": fin.get("duration_ms") or 0,
        "duration_api_ms": fin.get("duration_api_ms") or 0,
        "num_turns": fin.get("num_turns") or 0,
        "tokens": {
            "input": usage.get("input_tokens") or 0,
            "output": usage.get("output_tokens") or 0,
            "cache_read": cache_reads,
            "cache_write": cache_writes,
            "total_input_incl_cache": total_input,
        },
        "tool_calls": tool_calls,
        "log_path": str(log_path),
    }
