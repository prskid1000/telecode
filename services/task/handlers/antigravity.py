"""Antigravity task handler: runs `agy` in a session folder.

Mirrors services.task.handlers.claude_code so the executor / heartbeat /
routine call sites stay engine-agnostic.

Transport (verified against agy, Sept 2026): the prompt goes on **stdin** as one
`--input-format stream-json` message, `{"event":"user","message":{"content":...}}`,
with `--output-format stream-json`. Passing it as `-p <prompt>` on a
`shell=True` command line hits the Windows command-length limit (~8 KB through a
.cmd shim) once a design prompt is stacked in. The stream gives:

  * `init`        -> `conversation_id` (stored as last_antigravity_conversation_id,
                     replayed with --conversation, so resume works now)
  * `step_update` -> step_type `tool` (tool_name/tool_info) and `agent_response`
                     (`text_delta`), with per-step usage
  * `result`      -> status, response, num_turns, usage

Local mode (`is_local=True`, agy >= 1.1.13): agy's Gemini-API route is enabled
by `"modelProvider": "gemini"` in `~/.gemini/antigravity-cli/settings.json` plus
`GEMINI_API_KEY`, with `GOOGLE_GEMINI_BASE_URL` pointing at telecode's proxy,
which serves the Gemini protocol (`/v1beta/models/*`). The user's real
`~/.gemini` holds their OAuth login and settings and is never touched: the
child gets its own home (`<settings_dir>/data/agy-local-home`, via USERPROFILE
and HOME — agy is Go, and on Windows `os.UserHomeDir()` reads USERPROFILE).
The model is selected with agy's custom-model form
`--model gemini-api://local/models/<llama model>`; agy still posts to
GOOGLE_GEMINI_BASE_URL, and the proxy takes the name after the last
`/models/`. Local conversations live in the isolated home, so their id is
stored under a separate key (last_antigravity_local_conversation_id) — a cloud
conversation id cannot be resumed locally or vice versa.

Still missing: no cost field.
"""

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
    kill_proc_tree,
    make_resume_store,
    prompt_digest,
    read_resume_id,
    track_process,
    untrack_process,
    update_progress,
    StreamDrain,
)

logger = logging.getLogger("telecode.services.task.handlers.antigravity")

_RESUME_KEY = "last_antigravity_conversation_id"
_RESUME_KEY_LOCAL = "last_antigravity_local_conversation_id"

# Credentials/backends the genai SDK would prefer over GEMINI_API_KEY
# (GOOGLE_API_KEY wins when both are set) or that switch it to Vertex.
_LOCAL_ENV_STRIP = ("GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT",
                    "GOOGLE_CLOUD_LOCATION", "GOOGLE_APPLICATION_CREDENTIALS")


def local_home() -> Path:
    import config as app_config
    return Path(app_config._settings_dir()) / "data" / "agy-local-home"


def ensure_local_home(home: Optional[Path] = None) -> Path:
    """Create the isolated agy home with `modelProvider: gemini` set.

    Existing keys in that settings.json are preserved (agy writes its own
    there, e.g. trusted workspaces); only modelProvider is forced.
    """
    home = home or local_home()
    cfg_dir = home / ".gemini" / "antigravity-cli"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "settings.json"
    data: Dict[str, Any] = {}
    try:
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
            if isinstance(loaded, dict):
                data = loaded
    except (OSError, json.JSONDecodeError):
        data = {}
    if data.get("modelProvider") != "gemini":
        data["modelProvider"] = "gemini"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    return home


def local_env(proxy_port: int, home: Path, base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ if base is None else base)
    for k in _LOCAL_ENV_STRIP:
        env.pop(k, None)
    env.update({
        "USERPROFILE": str(home),
        "HOME": str(home),
        "GEMINI_API_KEY": "local",
        # No /v1 — the genai SDK appends /v1beta/models/... itself.
        "GOOGLE_GEMINI_BASE_URL": f"http://localhost:{proxy_port}",
    })
    return env


def local_model_arg(model: str) -> str:
    """agy's custom-model URL form. The host part is ignored for routing (agy
    posts to GOOGLE_GEMINI_BASE_URL); the proxy reads the name after /models/."""
    return f"gemini-api://local/models/{model}"


def antigravity_task(
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
    """Run Antigravity (`agy -p`) in the session folder.

    `model`: an `agy models` id in cloud mode, the llama model name in local
    mode. The resume id is scoped per (workspace, agent, engine[, local]).
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
    log_path = log_dir / f"{task_id}.txt"

    sid = get_session_id()
    ns = get_session_namespace()
    work_dir = get_session_folder()
    if not sid or not work_dir:
        raise RuntimeError("No session bound to this task")

    meta = session_store.get(sid, namespace=ns) or {}
    resume_id = read_resume_id(meta.get("data"), agent_id, "antigravity", is_local)
    resume_store = make_resume_store(sid, ns, agent_id, "antigravity", is_local)

    with stage_for_run(agent_id, sid, work_dir, engine="antigravity"):
        return _run_antigravity_subprocess(
            prompt=prompt,
            work_dir=work_dir,
            sid=sid,
            ns=ns,
            resume_id=resume_id,
            log_path=log_path,
            is_local=is_local,
            model=model,
            resume_store=resume_store,
        )


def _build_antigravity_argv(
    *,
    work_dir: Path,
    resume_id: Optional[str],
    model: Optional[str] = None,
) -> List[str]:
    # `-p=` with an empty value: the prompt arrives on stdin. A bare `-p` would
    # swallow the next flag as its prompt.
    cmd: List[str] = [
        "agy",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--dangerously-skip-permissions",
        "--add-dir", str(work_dir),
    ]
    if model:
        cmd += ["--model", model]
    if resume_id:
        cmd += ["--conversation", resume_id]
    cmd.append("-p=")
    return cmd


def _stdin_message(prompt: str) -> str:
    return json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n"


def _run_antigravity_subprocess(
    *,
    prompt: str,
    work_dir: Path,
    sid: str,
    ns: Optional[str],
    resume_id: Optional[str],
    log_path: Path,
    is_local: bool = False,
    model: Optional[str] = None,
    resume_store: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Spawn agy. `resume_store(id)` persists a new conversation id; when
    omitted (the TeleDesign path) the legacy flat key is written."""
    env: Optional[Dict[str, str]] = None
    # Cloud: an `agy models` id (e.g. gemini-3.8-flash-high). Local: a llama model name.
    model_arg: Optional[str] = model if (model and not is_local) else None
    resume_key = _RESUME_KEY_LOCAL if is_local else _RESUME_KEY
    if is_local:
        import config as app_config
        import llamacpp.state as llama_state
        model = model or llama_state.last_active_model() or "local"
        home = ensure_local_home()
        env = local_env(app_config.proxy_port(), home)
        model_arg = local_model_arg(model)
        logger.info(f"Local mode: agy home={home} base_url={env['GOOGLE_GEMINI_BASE_URL']} "
                    f"model={model_arg}")

    cmd = _build_antigravity_argv(work_dir=work_dir, resume_id=resume_id, model=model_arg)

    logger.info(f"Antigravity starting: cwd={work_dir} session={sid} resume={resume_id or 'none'}")
    update_progress(0.05, "launching agy")
    append_event({
        "kind": "start",
        "session_id": sid,
        "cwd": str(work_dir),
        **prompt_digest(prompt),
        "resumed": bool(resume_id),
        "resumed_antigravity_conversation_id": resume_id,
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
        proc.stdin.write(_stdin_message(prompt))
        proc.stdin.close()
    except OSError:
        pass  # killed before reading stdin (cancel/timeout); handled below
    stderr_drain = StreamDrain(proc.stderr)

    tool_calls: List[str] = []
    text_parts: List[str] = []
    raw_lines: List[str] = []
    final: Optional[Dict[str, Any]] = None
    conversation_id: Optional[str] = None
    try:
        with log_path.open("w", encoding="utf-8") as log_fh:
            assert proc.stdout is not None
            for line in proc.stdout:
                log_fh.write(line)
                log_fh.flush()

                if is_cancelled():
                    logger.info("Cancellation requested — terminating Antigravity")
                    kill_proc_tree(proc)
                    raise RuntimeError("Task cancelled")

                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    if line.strip():
                        raw_lines.append(line.strip())
                    continue

                kind = evt.get("event")
                if kind == "init":
                    conversation_id = evt.get("conversation_id") or conversation_id
                elif kind == "step_update":
                    step = evt.get("step_update") or {}
                    conversation_id = step.get("conversation_id") or conversation_id
                    if step.get("step_type") == "tool" and step.get("state") == "ACTIVE":
                        info = step.get("tool_info") or {}
                        name = step.get("tool_name") or info.get("name") or "tool"
                        tool_calls.append(name)
                        append_event({"kind": "tool", "name": name, "input": info.get("parameters")})
                    elif step.get("step_type") == "agent_response" and step.get("text_delta"):
                        text_parts.append(step["text_delta"])
                        append_event({"kind": "narrative_delta", "text": step["text_delta"]})
                elif kind == "result":
                    final = evt.get("result") or {}
                    conversation_id = final.get("conversation_id") or conversation_id

                if conversation_id and conversation_id != resume_id:
                    if resume_store:
                        resume_store(conversation_id)
                    else:
                        session_store.patch_data(sid, {resume_key: conversation_id}, namespace=ns)
                    resume_id = conversation_id

        proc.wait(timeout=60)
    finally:
        if proc.poll() is None:
            kill_proc_tree(proc)
        untrack_process(tracked_pid)

    if is_cancelled():
        raise RuntimeError("Task cancelled")

    stderr = stderr_drain.text()
    fin = final or {}
    if fin.get("status") not in (None, "SUCCESS") and not fin.get("response"):
        raise RuntimeError(f"agy failed: {fin.get('error') or stderr.strip()[:500]}")
    if proc.returncode != 0 and final is None and not text_parts:
        raise RuntimeError(f"agy exited with code {proc.returncode}: {stderr.strip()[:500]}")

    final_text = (fin.get("response") or "".join(text_parts) or "\n".join(raw_lines)).strip()
    usage = fin.get("usage") or {}

    update_progress(1.0, "done")
    append_event({
        "kind": "done",
        "tool_count": len(tool_calls),
        "cost_usd": None,
        "num_turns": fin.get("num_turns") or 1,
        "input_tokens": usage.get("input_tokens") or 0,
        "output_tokens": usage.get("output_tokens") or 0,
        "cache_read_tokens": usage.get("cache_read_tokens") or 0,
        "cache_write_tokens": 0,
    })

    return {
        "result": final_text,
        "session_id": sid,
        "antigravity_conversation_id": conversation_id,
        "cost_usd": 0,
        "duration_ms": int((fin.get("duration_seconds") or 0) * 1000),
        "duration_api_ms": 0,
        "num_turns": fin.get("num_turns") or 1,
        "tokens": {
            "input": usage.get("input_tokens") or 0,
            "output": usage.get("output_tokens") or 0,
            "cache_read": usage.get("cache_read_tokens") or 0,
            "cache_write": 0,
            "total_input_incl_cache": usage.get("input_tokens") or 0,
        },
        "tool_calls": tool_calls,
        "log_path": str(log_path),
    }
