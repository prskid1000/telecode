"""Codex: ``codex exec … --json -`` (prompt on stdin), verified on codex-cli 0.157.

``--sandbox`` and ``-C`` are ``exec``-only and must precede ``resume``, which
rejects them. Local mode is a ``-c`` provider (never env) pointing at the
proxy's ``/v1/responses``; the child env drops the OpenAI/Codex credentials
that would make Codex bypass it. ``CODEX_HOME`` is left alone, so the ChatGPT
login still serves cloud runs.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.engine.adapters.base import Adapter, Launch, ParseState, todos_from, tool_event
from services.engine.types import EngineError, EngineRequest, EngineResult

logger = logging.getLogger("telecode.services.engine.codex")

# Must not be one of Codex's reserved provider ids (openai / ollama / lmstudio).
LOCAL_PROVIDER_ID = "telecode"
LOCAL_ENV_STRIP = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_ACCESS_TOKEN", "CODEX_API_KEY")

# turn.completed.usage (TokenUsage). cached_input_tokens is a SUBSET of
# input_tokens (OpenAI semantics), unlike Claude. No cost/turn/duration fields.
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens",
                "reasoning_output_tokens", "cache_write_input_tokens")

_TOOL_ITEMS = ("command_execution", "command_executed", "file_change", "mcp_tool_call",
               "web_search", "tool_use", "patch")


def local_provider_overrides(proxy_port: int) -> List[str]:
    """``-c`` overrides pointing Codex at telecode's /v1/responses (values
    unquoted on purpose: ``-c`` falls back to the literal string)."""
    p = f"model_providers.{LOCAL_PROVIDER_ID}"
    return [
        "-c", f"model_provider={LOCAL_PROVIDER_ID}",
        "-c", f"{p}.name={LOCAL_PROVIDER_ID}",
        "-c", f"{p}.base_url=http://localhost:{proxy_port}/v1",
        "-c", f"{p}.wire_api=responses",
        # Long local prefills send nothing for a while; Codex's idle timer counts events.
        "-c", f"{p}.stream_idle_timeout_ms=600000",
    ]


def local_env(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ if base is None else base)
    for k in LOCAL_ENV_STRIP:
        env.pop(k, None)
    return env


def build_argv(*, work_dir: Path, resume_id: Optional[str], last_msg_path: Path,
               model: Optional[str], provider_overrides: Optional[List[str]] = None,
               schema_path: Optional[Path] = None) -> List[str]:
    exec_only = ["--sandbox", "danger-full-access", "-C", str(work_dir)]
    common = [
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--output-last-message", str(last_msg_path),
    ]
    if model:
        common += ["--model", model]
    if schema_path:
        common += ["--output-schema", str(schema_path)]
    overrides = list(provider_overrides or [])
    # "-" = read the prompt from stdin.
    if resume_id:
        return ["codex", "exec", *overrides, *exec_only, "resume", resume_id, *common, "-"]
    return ["codex", "exec", *overrides, *exec_only, *common, "-"]


def add_usage(totals: Dict[str, int], usage: Any) -> None:
    if not isinstance(usage, dict):
        return
    for k in USAGE_FIELDS:
        v = usage.get(k)
        if isinstance(v, (int, float)):
            totals[k] = totals.get(k, 0) + int(v)


def normalize_usage(totals: Dict[str, int]) -> Dict[str, int]:
    """Summed codex usage → the shared ``tokens`` shape (``input`` excludes cache reads)."""
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


def describe_item(item: Dict[str, Any]) -> str:
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


class CodexAdapter(Adapter):
    engine = "codex"
    label = "codex"
    resume_start_key = "resumed_codex_session_id"

    def build(self, req: EngineRequest) -> Launch:
        import config as app_config
        env: Optional[Dict[str, str]] = None
        overrides: List[str] = []
        model = req.model
        if req.is_local:
            import llamacpp.state as llama_state
            model = model or llama_state.last_active_model() or "local"
            overrides = local_provider_overrides(app_config.proxy_port())
            env = local_env()
            logger.info(f"Local mode: codex provider '{LOCAL_PROVIDER_ID}' -> "
                        f"http://localhost:{app_config.proxy_port()}/v1/responses (model={model})")
        if req.env_extra:
            env = {**(env or os.environ), **req.env_extra}
        cleanup: List[Path] = []
        schema_path = None
        if req.schema:
            d = Path(app_config._settings_dir()) / "data" / "runtime" / "engine"
            d.mkdir(parents=True, exist_ok=True)
            schema_path = d / f"schema-{uuid.uuid4().hex}.json"
            schema_path.write_text(json.dumps(req.schema), encoding="utf-8")
            cleanup.append(schema_path)
        if req.add_dirs:
            logger.info("codex: add_dirs not supported by codex exec — ignored")
        argv = build_argv(work_dir=req.cwd, resume_id=req.resume_id, last_msg_path=req.last_msg_path,
                          model=model, provider_overrides=overrides, schema_path=schema_path)
        return Launch(argv=argv, stdin=req.prompt, env=env, cleanup=cleanup)

    def parse(self, evt: Dict[str, Any], st: ParseState) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        t = evt.get("type") or ""
        if t == "thread.started":
            sid = (evt.get("thread") or {}).get("id") or evt.get("thread_id")
            if isinstance(sid, str) and sid:
                st.session_id = sid
        elif t in ("item.started", "item.updated", "item.completed"):
            item = evt.get("item") or {}
            itype = item.get("type") or ""
            if itype == "todo_list":
                out.append({"kind": "todo", "todos": todos_from(item.get("items"), "text", "completed")})
            elif t != "item.completed":
                pass
            elif itype in ("agent_message", "assistant_message"):
                text = (item.get("text") or item.get("content") or "").strip()
                if text:
                    out.append({"kind": "narrative", "text": text})
            elif itype == "error":
                msg = (item.get("message") or "").strip()
                if msg:
                    out.append({"kind": "warning", "text": msg})
            elif itype in _TOOL_ITEMS:
                st.tool_calls.append(itype)
                out.append(tool_event(itype, describe_item(item)))
            elif itype == "reasoning":
                text = (item.get("text") or "").strip()
                if text:
                    out.append({"kind": "narrative", "text": text})
        elif t in ("turn.failed", "error"):
            err = evt.get("error") or evt.get("message")
            if isinstance(err, dict):  # turn.failed: {"error": {"message": ...}}
                err = err.get("message") or json.dumps(err)
            out.append({"kind": "retry", "attempt": evt.get("attempt"), "max_retries": evt.get("max_retries"),
                        "error": err})
        elif t == "turn.completed":
            st.saw_completion = True
            st.turns += 1
            add_usage(st.usage, evt.get("usage"))
            out.append({"kind": "usage", "tokens": normalize_usage(st.usage), "cost_usd": None})
        return out

    def trailing_events(self, st: ParseState) -> List[Dict[str, Any]]:
        if not st.saw_completion and st.raw_lines:
            return [{"kind": "narrative", "text": t} for t in st.raw_lines]
        return []

    def finish(self, req, st, returncode, stderr, wall_ms) -> EngineResult:
        if returncode not in (0, None) and not st.saw_completion and not st.raw_lines:
            raise EngineError(f"codex exited with code {returncode}: {stderr.strip()[:500]}")
        final_text = ""
        try:
            if req.last_msg_path and Path(req.last_msg_path).exists():
                final_text = Path(req.last_msg_path).read_text(encoding="utf-8").strip()
        except Exception as exc:
            logger.warning(f"Could not read codex last-message file: {exc}")
        if not final_text:
            final_text = "\n".join(st.raw_lines)
        structured = None
        if req.schema and final_text:
            try:
                structured = json.loads(final_text)
            except ValueError:
                logger.warning("codex: --output-schema reply is not valid JSON")
        return EngineResult(
            engine=self.engine, text=final_text, engine_session_id=st.session_id,
            cost_usd=None,  # codex exec reports no cost: None = unknown, not free
            duration_ms=wall_ms, duration_api_ms=0, num_turns=st.turns,
            tokens=normalize_usage(st.usage), tool_calls=list(st.tool_calls),
            structured_output=structured, exit_code=returncode)
