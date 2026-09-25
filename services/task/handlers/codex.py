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
import time
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
    kill_proc_tree,
    make_resume_store,
    prompt_digest,
    read_resume_id,
    track_process,
    untrack_process,
    update_progress,
    StreamDrain,
)

logger = logging.getLogger("telecode.services.task.handlers.codex")


def _describe_item(item: Dict[str, Any]) -> str:
    """Best-effort one-liner for a codex `item.completed` payload."""
    if not isinstance(item, dict):
        return "item"
    itype = item.get("type") or item.get("kind") or "item"
    changes = item.get("changes")
    if isinstance(changes, list) and changes and isinstance(changes[0], dict):
        return f"{itype}: " + ", ".join(str(c.get("path", "")) for c in changes[:3])
    for key in ("command", "path", "file_path", "url", "query", "tool", "name"):
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
        # codex-cli 0.157 names: agent_message / command_execution /
        # file_change / mcp_tool_call / web_search / todo_list / error. The
        # older spellings are kept so a pinned older CLI still maps.
        if itype in ("agent_message", "assistant_message"):
            text = (item.get("text") or item.get("content") or "").strip()
            if text:
                append_event({"kind": "narrative", "text": text})
        elif itype == "error":
            msg = (item.get("message") or "").strip()
            if msg:
                append_event({"kind": "warning", "text": msg})
        elif itype in ("command_execution", "command_executed", "file_change", "mcp_tool_call",
                       "web_search", "tool_use", "patch"):
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


# `turn.completed.usage` in codex-cli 0.157 (same TokenUsage struct as the
# rollout files' token_count events): input_tokens, cached_input_tokens,
# output_tokens, reasoning_output_tokens, and on newer builds
# cache_write_input_tokens. cached_input_tokens is a SUBSET of input_tokens
# (OpenAI semantics), unlike Claude where input_tokens excludes the cache.
# There is no cost, turn-count or duration field — turns are counted from
# turn.completed events and duration is wall clock.
_USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens",
                 "reasoning_output_tokens", "cache_write_input_tokens")


def _add_usage(totals: Dict[str, int], usage: Any) -> None:
    if not isinstance(usage, dict):
        return
    for k in _USAGE_FIELDS:
        v = usage.get(k)
        if isinstance(v, (int, float)):
            totals[k] = totals.get(k, 0) + int(v)


def _normalize_usage(totals: Dict[str, int]) -> Dict[str, int]:
    """Map summed codex usage onto the handlers' shared `tokens` shape, where
    `input` excludes cache reads (Claude's convention)."""
    total_in = totals.get("input_tokens", 0)
    cached = min(totals.get("cached_input_tokens", 0), total_in)
    return {
        "input": total_in - cached,
        "output": totals.get("output_tokens", 0),
        "cache_read": cached,
        "cache_write": totals.get("cache_write_input_tokens", 0),
        "reasoning_output": totals.get("reasoning_output_tokens", 0),
        "total_input_incl_cache": total_in,
    }


def codex_task(
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
    resume_id = read_resume_id(meta.get("data"), agent_id, "codex", is_local)
    resume_store = make_resume_store(sid, ns, agent_id, "codex", is_local)

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
            model=model,
            resume_store=resume_store,
        )


# Provider id for local mode. Must not be one of Codex's reserved ids
# (openai / ollama / lmstudio), which cannot be redefined with -c.
LOCAL_PROVIDER_ID = "telecode"

# Env vars that would make Codex authenticate against (or be redirected to)
# OpenAI instead of the -c provider. OPENAI_BASE_URL is ignored by current
# Codex anyway; an exported OPENAI_API_KEY can make it bypass a custom
# provider. CODEX_HOME is deliberately NOT touched — the ChatGPT login lives
# there and non-local runs need it.
_LOCAL_ENV_STRIP = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_ACCESS_TOKEN", "CODEX_API_KEY")


def _local_provider_overrides(proxy_port: int) -> List[str]:
    """`-c` overrides that point Codex at telecode's /v1/responses.

    Codex dropped the Chat Completions wire (`wire_api="chat"` is a hard error
    since openai/codex#10157), so `wire_api=responses` is the only choice; the
    proxy forwards it to llama-server's native /v1/responses. No `env_key` →
    no Authorization header. Values are deliberately unquoted: `-c` parses the
    value as TOML and falls back to the literal string when that fails, which
    keeps these free of quote characters that would have to survive
    `shell=True` + cmd.exe quoting.
    """
    p = f"model_providers.{LOCAL_PROVIDER_ID}"
    return [
        "-c", f"model_provider={LOCAL_PROVIDER_ID}",
        "-c", f"{p}.name={LOCAL_PROVIDER_ID}",
        "-c", f"{p}.base_url=http://localhost:{proxy_port}/v1",
        "-c", f"{p}.wire_api=responses",
        # Long prefills on a local model send nothing for a while; the proxy
        # sends SSE keepalive comments, but Codex's idle timer counts events.
        "-c", f"{p}.stream_idle_timeout_ms=600000",
    ]


def _local_env(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ if base is None else base)
    for k in _LOCAL_ENV_STRIP:
        env.pop(k, None)
    return env


def _build_codex_argv(
    *,
    prompt: str,
    work_dir: Path,
    resume_id: Optional[str],
    last_msg_path: Path,
    model: Optional[str],
    provider_overrides: Optional[List[str]] = None,
) -> List[str]:
    # Flags `codex exec resume` also accepts go after the subcommand. The two
    # `exec`-only flags (--sandbox, -C) must precede `resume`, which rejects
    # them ("unexpected argument '--sandbox'", verified on codex-cli 0.157).
    exec_only = ["--sandbox", "danger-full-access", "-C", str(work_dir)]
    common = [
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--output-last-message", str(last_msg_path),
    ]
    if model:
        common += ["--model", model]
    overrides = list(provider_overrides or [])

    # "-" = read the prompt from stdin (see _run_codex_subprocess). On argv a
    # design prompt overflows the Windows command line on this shell=True spawn.
    if resume_id:
        # codex exec [-c ...] --sandbox X -C dir resume <SESSION_ID> [flags...] -
        return ["codex", "exec", *overrides, *exec_only, "resume", resume_id, *common, "-"]
    return ["codex", "exec", *overrides, *exec_only, *common, "-"]


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
    model: Optional[str] = None,
    resume_store: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Spawn `codex exec --json`. `resume_store(id)` persists a new thread id;
    when omitted (the TeleDesign path) the legacy `last_codex_session_id` key
    is written."""
    import config as app_config

    env = None
    overrides: List[str] = []
    if is_local:
        import llamacpp.state as llama_state
        model = model or llama_state.last_active_model() or "local"
        overrides = _local_provider_overrides(app_config.proxy_port())
        env = _local_env()
        logger.info(f"Local mode: codex provider '{LOCAL_PROVIDER_ID}' -> "
                    f"http://localhost:{app_config.proxy_port()}/v1/responses (model={model})")

    cmd = _build_codex_argv(
        prompt=prompt,
        work_dir=work_dir,
        resume_id=resume_id,
        last_msg_path=last_msg_path,
        model=model,
        provider_overrides=overrides,
    )

    logger.info(f"Codex starting: cwd={work_dir} session={sid} resume={resume_id or 'none'}")
    update_progress(0.05, "launching codex")
    append_event({
        "kind": "start",
        "session_id": sid,
        "cwd": str(work_dir),
        **prompt_digest(prompt),
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
    started = time.monotonic()
    tracked_pid = track_process(proc)
    assert proc.stdin is not None
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except OSError:
        pass  # killed before reading stdin (cancel/timeout); handled below
    stderr_drain = StreamDrain(proc.stderr)

    tool_calls: List[str] = []
    usage_totals: Dict[str, int] = {}
    turns = 0
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
                    kill_proc_tree(proc)
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
                    if resume_store:
                        resume_store(evt_sid)
                    else:
                        session_store.patch_data(sid, {"last_codex_session_id": evt_sid}, namespace=ns)

                if evt.get("type") == "turn.completed":
                    saw_turn_completed = True
                    turns += 1
                    _add_usage(usage_totals, evt.get("usage"))

        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            kill_proc_tree(proc)
        untrack_process(tracked_pid)

    if is_cancelled():
        raise RuntimeError("Task cancelled")
    duration_ms = int((time.monotonic() - started) * 1000)

    stderr = stderr_drain.text()
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

    tokens = _normalize_usage(usage_totals)

    update_progress(1.0, "done")
    append_event({
        "kind": "done",
        "tool_count": len(tool_calls),
        "cost_usd": None,           # codex exec reports no cost
        "num_turns": turns,
        "input_tokens": tokens["total_input_incl_cache"],
        "output_tokens": tokens["output"],
        "cache_read_tokens": tokens["cache_read"],
        "cache_write_tokens": tokens["cache_write"],
    })

    return {
        "result": final_text,
        "session_id": sid,
        "codex_session_id": captured_codex_sid,
        # codex exec --json has no cost field; None = unknown, not free.
        "cost_usd": None,
        "duration_ms": duration_ms,
        "duration_api_ms": 0,
        "num_turns": turns,
        "tokens": tokens,
        "tool_calls": tool_calls,
        "log_path": str(log_path),
    }
