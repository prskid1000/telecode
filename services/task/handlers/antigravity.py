"""Antigravity task handler: runs `agy -p` in a session folder.

Mirrors services.task.handlers.claude_code so the executor / heartbeat /
routine call sites stay engine-agnostic.

V1 LIMITATIONS (May 2026 — revisit when `agy` gains a stable JSON mode):
  * No structured streaming. `agy -p` emits plain text on stdout; we surface
    it as `narrative` events. Tool calls are not visible to telecode.
  * No conversation ID in non-interactive output. We cannot reliably resume a
    specific session, so resume is fresh-by-default. (The `-c` "most recent
    conversation" flag is process-global and unsafe for concurrent routines.)
  * No documented `--base-url` or `--model` flag. `is_local=True` is a no-op
    with a warning — runs hit the cloud anyway.
  * No documented usage / cost fields. Token counts in the return value are
    zeros.

When Antigravity ships `--output-format json` + conversation IDs in `-p`
mode + a base-url flag, the body of `_run_antigravity_subprocess` is the
only thing that needs to change — the public surface already mirrors
claude_code_task and codex_task.
"""

from __future__ import annotations

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

logger = logging.getLogger("telecode.services.task.handlers.antigravity")


def antigravity_task(
    prompt: Optional[str] = None,
    is_local: bool = False,
    *,
    agent_id: Optional[str] = None,
    agent: Optional[Dict[str, Any]] = None,
    job: Optional[Dict[str, Any]] = None,
    agent_files: Optional[List[Any]] = None,
    job_files: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Run Antigravity (`agy -p`) in the session folder."""
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
    log_path = log_dir / f"{task_id}.txt"

    sid = get_session_id()
    ns = get_session_namespace()
    work_dir = get_session_folder()
    if not sid or not work_dir:
        raise RuntimeError("No session bound to this task")

    # NOTE: when agy exposes IDs in -p mode, read it here and pass to the
    # subprocess via --conversation <ID>. For now: always fresh.
    meta = session_store.get(sid, namespace=ns) or {}
    resume_id = (meta.get("data") or {}).get("last_antigravity_conversation_id")

    if is_local:
        logger.warning(
            "antigravity_task: is_local=True ignored — agy has no documented "
            "--base-url / --model flag for headless mode in this release."
        )

    with stage_for_run(agent_id, sid, work_dir, engine="antigravity"):
        return _run_antigravity_subprocess(
            prompt=prompt,
            work_dir=work_dir,
            sid=sid,
            ns=ns,
            resume_id=resume_id,
            log_path=log_path,
        )


def _build_antigravity_argv(
    *,
    prompt: str,
    work_dir: Path,
    resume_id: Optional[str],
) -> List[str]:
    cmd: List[str] = [
        "agy",
        "-p", prompt,
        "--dangerously-skip-permissions",
        "--add-dir", str(work_dir),
    ]
    if resume_id:
        cmd += ["--conversation", resume_id]
    return cmd


def _run_antigravity_subprocess(
    *,
    prompt: str,
    work_dir: Path,
    sid: str,
    ns: Optional[str],
    resume_id: Optional[str],
    log_path: Path,
) -> Dict[str, Any]:
    cmd = _build_antigravity_argv(prompt=prompt, work_dir=work_dir, resume_id=resume_id)

    logger.info(f"Antigravity starting: cwd={work_dir} session={sid} resume={resume_id or 'none'}")
    update_progress(0.05, "launching agy")
    append_event({
        "kind": "start",
        "session_id": sid,
        "cwd": str(work_dir),
        "prompt": prompt,
        "resumed": bool(resume_id),
        "resumed_antigravity_conversation_id": resume_id,
        "is_local": False,  # always false in v1; see is_local warning above
    })

    creation = 0
    if os.name == "nt":
        creation = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(
        cmd,
        cwd=str(work_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=None,
        text=True,
        bufsize=1,
        shell=True,
        creationflags=creation,
    )

    accumulated_text: List[str] = []
    # agy streams plain text; treat each non-empty line as a narrative chunk
    # to keep the UX similar to Claude's streaming narrative.
    try:
        with log_path.open("w", encoding="utf-8") as log_fh:
            assert proc.stdout is not None
            for line in proc.stdout:
                log_fh.write(line)
                log_fh.flush()

                if is_cancelled():
                    logger.info("Cancellation requested — terminating Antigravity")
                    proc.terminate()
                    raise RuntimeError("Task cancelled")

                stripped = line.rstrip("\r\n")
                if stripped.strip():
                    accumulated_text.append(stripped)
                    append_event({"kind": "narrative", "text": stripped})

        # `agy -p` has a default 5m timeout; we add slack on top.
        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()

    stderr = (proc.stderr.read() if proc.stderr else "") or ""
    if proc.returncode != 0 and not accumulated_text:
        raise RuntimeError(f"agy exited with code {proc.returncode}: {stderr.strip()[:500]}")

    final_text = "\n".join(accumulated_text).strip()

    update_progress(1.0, "done")
    append_event({
        "kind": "done",
        "tool_count": 0,            # not visible without structured output
        "cost_usd": None,
        "num_turns": 1,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    })

    return {
        "result": final_text,
        "session_id": sid,
        "antigravity_conversation_id": None,  # see TODO at top of file
        "cost_usd": 0,
        "duration_ms": 0,
        "duration_api_ms": 0,
        "num_turns": 1,
        "tokens": {
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "cache_write": 0,
            "total_input_incl_cache": 0,
        },
        "tool_calls": [],
        "log_path": str(log_path),
    }
