"""Claude Code: ``claude -p … --output-format stream-json``, prompt on stdin.

Local mode points the CLI at telecode's proxy through ``ANTHROPIC_*`` env
(``ANTHROPIC_BASE_URL`` without ``/v1`` — the SDK appends it).

P2: ``--resume <id> --fork-session`` forks a conversation; ``--max-budget-usd``
caps dollars (the CLI ends with ``subtype: error_max_budget_usd``, raised as
:class:`EngineBudgetExceeded`); each ``message_delta`` carries the message's
final usage, summed into a live ``usage`` event so the runner can enforce a
token cap mid-run.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.engine.adapters.base import (Adapter, Launch, ParseState, describe_tool,
                                           todos_from, tool_event)
from services.engine.types import EngineBudgetExceeded, EngineError, EngineRequest, EngineResult

logger = logging.getLogger("telecode.services.engine.claude")


SKIP_MODES = (None, "", "skip", "bypassPermissions")


ASK_MODE = "ask"
# The MCP server name telecode's own entry gets in the per-run --mcp-config (not
# "telecode": the user may already have a server by that name in their config).
APPROVE_SERVER = "telecode_safety"
APPROVE_TOOL = f"mcp__{APPROVE_SERVER}__approve_tool"


def permission_args(permission_mode: Optional[str], *, approve_mcp_config=None) -> List[str]:
    """``--dangerously-skip-permissions`` (interactive / pipeline runs), or for
    autonomous runs ``--permission-mode <mode> --permission-prompts none``: the
    mode decides what is allowed, and anything that would prompt is denied —
    nobody is there to answer (verified on claude 2.1.282).

    ``ask`` (P5): the default permission mode, and every prompt goes to
    telecode's ``approve_tool`` MCP tool (``--permission-prompt-tool``), which
    asks a person through the approvals inbox + Telegram and denies on timeout.
    Needs the per-run ``--mcp-config`` naming telecode's MCP server."""
    if permission_mode == ASK_MODE and approve_mcp_config:
        # The mode is pinned: with none given the CLI uses the user's settings
        # ``defaultMode`` (verified: bypassPermissions on this machine), and nothing
        # would ever be asked. ``manual`` is 2.1.282's name for the prompting mode.
        return ["--permission-mode", "manual", "--permission-prompts", "host",
                "--permission-prompt-tool", APPROVE_TOOL, "--mcp-config", str(approve_mcp_config)]
    if permission_mode in SKIP_MODES:
        return ["--dangerously-skip-permissions"]
    return ["--permission-mode", str(permission_mode), "--permission-prompts", "none"]


EFFORTS = ("low", "medium", "high", "xhigh", "max")


def effort_args(effort: Optional[str]) -> List[str]:
    """``--effort <level>`` (claude 2.1.282: low, medium, high, xhigh, max);
    None / unknown = the CLI's default."""
    return ["--effort", effort] if effort in EFFORTS else []


def approve_mcp_config(correlation: Optional[Dict[str, Any]], mcp_url: str) -> Dict[str, Any]:
    """``--mcp-config`` JSON for ``ask`` mode: telecode's MCP server over HTTP,
    with the run's ids as headers so ``approve_tool`` knows whose call it is."""
    c = correlation or {}
    headers = {f"X-Telecode-{k.replace('_id', '').replace('_', '-').title()}": str(c[k])
               for k in ("task_id", "run_id", "step_id", "agent_id", "trigger_id") if c.get(k)}
    return {"mcpServers": {APPROVE_SERVER: {"type": "http", "url": mcp_url, "headers": headers}}}


def build_argv(*, resume_id: Optional[str], model: Optional[str], is_local: bool,
               append_system_prompt_file=None, schema: Optional[Dict[str, Any]] = None,
               add_dirs=(), fork: bool = False, max_budget_usd: Optional[float] = None,
               permission_mode: Optional[str] = None, approve_mcp_config_path=None,
               effort: Optional[str] = None) -> List[str]:
    cmd = [
        "claude", "-p",
        *permission_args(permission_mode, approve_mcp_config=approve_mcp_config_path),
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]
    if resume_id:
        cmd += ["--resume", resume_id]
        if fork:
            cmd.append("--fork-session")
    if max_budget_usd:
        cmd += ["--max-budget-usd", f"{float(max_budget_usd):.4f}"]
    # Cloud: an alias (fable/opus/sonnet/haiku) or full name. Local: the llama
    # model travels as ANTHROPIC_MODEL instead.
    if model and not is_local:
        cmd += ["--model", model]
    cmd += effort_args(effort)
    if append_system_prompt_file:
        cmd += ["--append-system-prompt-file", str(append_system_prompt_file)]
    if schema:
        cmd += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
    for d in add_dirs or ():
        cmd += ["--add-dir", str(d)]
    return cmd


def local_env(model: str, proxy_port: int, max_output_tokens: int,
              base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    return {
        **(os.environ if base is None else base),
        "ANTHROPIC_BASE_URL": f"http://localhost:{proxy_port}",
        "ANTHROPIC_AUTH_TOKEN": "local",
        "ANTHROPIC_MODEL": model,
        "BASH_DEFAULT_TIMEOUT_MS": "1800000",
        "BASH_MAX_TIMEOUT_MS": "3600000",
        "DISABLE_PROMPT_CACHING": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
        "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
        "CLAUDE_CODE_USE_POWERSHELL_TOOL": "1",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(max_output_tokens),
        "ENABLE_TOOL_SEARCH": "false",
    }


_LIVE_USAGE = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _norm(usage: Dict[str, Any]) -> Dict[str, int]:
    cache_reads = usage.get("cache_read_input_tokens") or 0
    cache_writes = usage.get("cache_creation_input_tokens") or 0
    return {
        "input": usage.get("input_tokens") or 0,
        "output": usage.get("output_tokens") or 0,
        "cache_read": cache_reads,
        "cache_write": cache_writes,
        "total_input_incl_cache": (usage.get("input_tokens") or 0) + cache_reads + cache_writes,
    }


class ClaudeAdapter(Adapter):
    engine = "claude_code"
    label = "claude"
    resume_start_key = "resumed_claude_session_id"
    cumulative_cost = True     # total_cost_usd sums a resumed session's invocations

    def build(self, req: EngineRequest) -> Launch:
        env: Optional[Dict[str, str]] = None
        if req.is_local:
            import config as app_config
            import llamacpp.state as llama_state
            model = req.model or llama_state.last_active_model() or "local"
            env = local_env(model, app_config.proxy_port(), app_config.tasks_local_max_output_tokens())
            logger.info(f"Local mode: using model {model} at http://localhost:{app_config.proxy_port()}")
        if req.env_extra:
            env = {**(env or os.environ), **req.env_extra}
        from services.engine import otel
        if otel.active(req.correlation):
            env = otel.with_env(env, otel.claude_env(req.correlation or {}))
        warnings: List[str] = []
        cleanup: List[Path] = []
        mode = req.permission_mode
        cfg_path = None
        if mode == ASK_MODE:
            from services.telemetry import settings as p5
            if not p5.mcp_enabled():
                warnings.append("permission mode 'ask' needs telecode's MCP server (mcp_server.enabled) for "
                                "approve_tool — falling back to 'auto'")
                logger.warning(warnings[-1])
                mode = "auto"
            else:
                import config as app_config
                d = Path(app_config._settings_dir()) / "data" / "runtime" / "engine"
                d.mkdir(parents=True, exist_ok=True)
                cfg_path = d / f"mcp-approve-{uuid.uuid4().hex}.json"
                cfg_path.write_text(json.dumps(approve_mcp_config(req.correlation, p5.mcp_url())), encoding="utf-8")
                cleanup.append(cfg_path)
                # The tool waits for a person; Claude's MCP tool timeout must outlast it.
                env = {**(env or os.environ),
                       "MCP_TOOL_TIMEOUT": str(int((p5.approval_timeout_sec() + 60) * 1000))}
        argv = build_argv(resume_id=req.resume_id, model=req.model, is_local=req.is_local,
                          append_system_prompt_file=req.system_append_file, schema=req.schema,
                          add_dirs=req.add_dirs, fork=req.fork,
                          max_budget_usd=None if req.is_local else req.max_usd,
                          permission_mode=mode, approve_mcp_config_path=cfg_path, effort=req.effort)
        extra = [str(a) for a in (req.extra_args or ())]
        if cfg_path is not None and "--mcp-config" in extra:
            # The caller has its own --mcp-config (TeleDesign's --strict-mcp-config
            # file): one variadic flag naming both files, so strict mode keeps
            # telecode_safety's approve_tool.
            i = argv.index("--mcp-config")
            del argv[i:i + 2]
            j = extra.index("--mcp-config") + 2
            extra[j:j] = [str(cfg_path)]
        argv += extra
        return Launch(argv=argv, stdin=req.prompt, env=env, cleanup=cleanup, warnings=warnings)

    def parse(self, evt: Dict[str, Any], st: ParseState) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        t = evt.get("type")
        sid = evt.get("session_id")
        if isinstance(sid, str) and sid:
            st.session_id = sid
        if t == "stream_event":
            inner = evt.get("event") or {}
            delta = inner.get("delta") or {}
            if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta" \
                    and delta.get("text"):
                out.append({"kind": "delta", "text": delta["text"]})
            elif inner.get("type") == "message_delta" and isinstance(inner.get("usage"), dict):
                # The message's final usage: sum across messages = the result's usage.
                for k in _LIVE_USAGE:
                    v = inner["usage"].get(k)
                    if isinstance(v, (int, float)):
                        st.usage[k] = st.usage.get(k, 0) + int(v)
                out.append({"kind": "usage", "tokens": _norm(st.usage), "cost_usd": None, "partial": True})
        elif t == "assistant":
            for block in (evt.get("message") or {}).get("content") or []:
                btype = block.get("type")
                if btype == "text" and (block.get("text") or "").strip():
                    out.append({"kind": "narrative", "text": block["text"].strip()})
                elif btype == "tool_use":
                    name = block.get("name", "?")
                    st.tool_calls.append(name)
                    tin = block.get("input", {})
                    out.append(tool_event(name, describe_tool(name, tin)))
                    if name == "TodoWrite" and isinstance(tin, dict):
                        out.append({"kind": "todo", "todos": todos_from(tin.get("todos"), "content")})
        elif t == "system" and evt.get("subtype") == "init":
            if isinstance(evt.get("model"), str) and evt["model"]:
                st.model = evt["model"]
        elif t == "system" and evt.get("subtype") == "api_retry":
            out.append({"kind": "retry", "attempt": evt.get("attempt"),
                        "max_retries": evt.get("max_retries"), "error": evt.get("error")})
        elif t == "result":
            st.final = evt
            st.saw_completion = True
        return out

    def trailing_events(self, st: ParseState) -> List[Dict[str, Any]]:
        # Non-JSON output with no result event: surface it as narrative.
        if not st.final and st.raw_lines:
            return [{"kind": "narrative", "text": t} for t in st.raw_lines]
        return []

    def reported_total_cost(self, st: ParseState) -> Optional[float]:
        v = (st.final or {}).get("total_cost_usd")
        return float(v) if isinstance(v, (int, float)) else None

    def _tokens(self, st: ParseState) -> Dict[str, int]:
        usage = (st.final or {}).get("usage")
        return _norm(usage if isinstance(usage, dict) else st.usage)

    def usage_event(self, st: ParseState) -> Optional[Dict[str, Any]]:
        if not st.final:
            return {"kind": "usage", "tokens": _norm(st.usage), "cost_usd": None} if st.usage else None
        return {"kind": "usage", "tokens": self._tokens(st), "cost_usd": st.final.get("total_cost_usd")}

    def finish(self, req, st, returncode, stderr, wall_ms) -> EngineResult:
        if returncode not in (0, None) and st.final is None and not st.raw_lines:
            raise EngineError(f"claude exited with code {returncode}: {stderr.strip()[:500]}")
        fin = st.final or {}
        if fin.get("subtype") == "error_max_budget_usd":
            cost = fin.get("total_cost_usd")
            raise EngineBudgetExceeded(f"cost ${float(cost or 0):.4f} reached max_usd ${float(req.max_usd or 0):.4f}")
        return EngineResult(
            engine=self.engine,
            text=fin.get("result") or "\n".join(st.raw_lines),
            engine_session_id=st.session_id or fin.get("session_id"),
            cost_usd=fin.get("total_cost_usd"),
            duration_ms=fin.get("duration_ms") or 0,
            duration_api_ms=fin.get("duration_api_ms") or 0,
            num_turns=fin.get("num_turns") or 0,
            tokens=self._tokens(st),
            tool_calls=list(st.tool_calls),
            structured_output=fin.get("structured_output"),
            exit_code=returncode,
        )
