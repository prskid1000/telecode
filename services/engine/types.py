"""Engine Runner request/result types and the normalised event vocabulary."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Normalised event kinds every adapter emits (``kind`` field of an event dict):
#   start      spawn is about to happen (session/cwd/prompt digest/resume info)
#   delta      a streamed text fragment (live only; persisted for agy as
#              ``narrative_delta``, its only text channel)
#   narrative  a complete assistant text block
#   tool       a tool call {tool, name, summary, input}
#   todo       the agent's todo list {todos: [{text, status}]}
#   usage      token usage so far {tokens, cost_usd}
#   warning    a non-fatal CLI error item
#   retry      an API retry {attempt, max_retries, error}
#   done       the run finished {tool_count, cost_usd, num_turns, *_tokens}
#   error      the run failed {message}
EVENT_KINDS = ("start", "delta", "narrative", "tool", "todo", "usage",
               "warning", "retry", "done", "error")

ENGINES = ("claude_code", "codex", "antigravity")

# Reasoning effort vocabulary (Claude Code's levels; each adapter maps it onto its CLI).
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def normalize_effort(value) -> str:
    """"" (= the CLI's default) or one of EFFORTS; anything else is a ValueError."""
    v = str(value or "").strip()
    if v and v not in EFFORTS:
        raise ValueError(f"effort must be one of {EFFORTS} (or empty)")
    return v


class EngineError(RuntimeError):
    """The CLI failed (non-zero exit with no output, error result, spawn failure)."""


class EngineCancelled(EngineError):
    def __init__(self, message: str = "Task cancelled"):
        super().__init__(message)


class EngineTimeout(EngineError):
    def __init__(self, message: str = "timeout"):
        super().__init__(message)


class EngineBudgetExceeded(EngineError):
    """A budget cap (tokens / wall clock / Claude's --max-budget-usd) stopped
    the run. The message always starts with ``budget_exceeded:`` — the run
    executor maps that to the step status ``budget_exceeded``."""

    PREFIX = "budget_exceeded"

    def __init__(self, detail: str = ""):
        super().__init__(f"{self.PREFIX}: {detail}" if detail else self.PREFIX)


@dataclass
class EngineRequest:
    engine: str                                  # claude_code | codex | antigravity
    prompt: str
    cwd: Path
    model: Optional[str] = None
    is_local: bool = False
    resume_id: Optional[str] = None
    # Persist a newly observed CLI session/thread/conversation id.
    on_resume_id: Optional[Callable[[str], None]] = None
    # Raw CLI stdout is copied here line by line (data/task_logs/<task>.jsonl|.txt).
    log_path: Optional[Path] = None
    # Codex --output-last-message target (defaults next to log_path).
    last_msg_path: Optional[Path] = None
    # Claude: --append-system-prompt-file (the agent's AGENT.md).
    system_append_file: Optional[Path] = None
    # Structured output: Claude --json-schema, Codex --output-schema, agy --json-schema <file>.
    schema: Optional[Dict[str, Any]] = None
    timeout_sec: Optional[float] = None
    # Fork the resumed session instead of continuing it (Claude --fork-session,
    # Codex `exec fork`; agy has no fork — the adapter runs fresh).
    fork: bool = False
    # Budget caps for this run (None = unlimited). Tokens are budget tokens
    # (input + cache writes + output) from the normalised usage events;
    # max_usd goes to Claude's --max-budget-usd (other engines report no cost).
    max_usd: Optional[float] = None
    max_tokens: Optional[int] = None
    max_seconds: Optional[float] = None
    env_extra: Dict[str, str] = field(default_factory=dict)
    # Extra CLI flags appended to the adapter's argv (Claude only; TeleDesign's
    # cost options: --strict-mcp-config, --tools, --setting-sources …).
    extra_args: List[str] = field(default_factory=list)
    add_dirs: List[Path] = field(default_factory=list)
    # Autonomous runs (triggers): Claude --permission-mode <mode> --permission-prompts none
    # instead of --dangerously-skip-permissions. None / "skip" / "bypassPermissions" = skip.
    # "ask" (P5): permission prompts go to telecode's MCP approve_tool (web inbox + Telegram).
    permission_mode: Optional[str] = None
    # Reasoning effort (low | medium | high | xhigh | max; None = the CLI's default):
    # Claude --effort, Codex -c model_reasoning_effort=…, agy --effort (xhigh → high).
    effort: Optional[str] = None
    # Claude reports ``total_cost_usd`` cumulatively across a resumed session. The
    # runner looks up the conversation's last reported total (sessions_repo
    # cost_total) and subtracts it; this is only the caller's fallback when the
    # shared table has no entry (e.g. TeleDesign's pre-existing per-chat record).
    cost_base_usd: Optional[float] = None
    # Sinks (all optional). on_event gets every normalised event dict.
    on_event: Optional[Callable[[Dict[str, Any]], None]] = None
    on_progress: Optional[Callable[[float, str], None]] = None
    cancel_check: Optional[Callable[[], bool]] = None
    # on_spawn(pid, stop) — stop(reason) asks the runner to stop the CLI tree
    # (non-blocking); on_exit(pid) once the process is gone.
    on_spawn: Optional[Callable[[int, Callable[[str], None]], None]] = None
    on_exit: Optional[Callable[[int], None]] = None
    # Echoed into the start event (the workspace/session the run belongs to).
    session_id: Optional[str] = None
    kill_grace_sec: float = 3.0
    # P5 correlation ids {task_id, run_id, step_id, agent_id, job_id, trigger_id,
    # attempt, source, workspace_id, agent_name}: OTEL_RESOURCE_ATTRIBUTES for the
    # CLI, own GenAI spans, approve_tool headers. None = no telemetry for this run.
    correlation: Optional[Dict[str, Any]] = None


@dataclass
class EngineResult:
    engine: str
    text: str = ""
    engine_session_id: Optional[str] = None
    cost_usd: Optional[float] = None       # this run's own cost (per-run, see cost_base_usd)
    # The CLI's own figure before the per-run correction (Claude: cumulative per session).
    cost_total_usd: Optional[float] = None
    duration_ms: int = 0
    duration_api_ms: int = 0
    num_turns: int = 0
    tokens: Dict[str, int] = field(default_factory=dict)
    tool_calls: List[str] = field(default_factory=list)
    log_path: Optional[str] = None
    structured_output: Any = None
    exit_code: Optional[int] = None

    # Engine-specific key the handlers have always returned the CLI id under.
    _SESSION_KEYS = {
        "claude_code": "claude_session_id",
        "codex": "codex_session_id",
        "antigravity": "antigravity_conversation_id",
    }

    def to_dict(self, session_id: Optional[str], *, with_schema: bool = False) -> Dict[str, Any]:
        """The handler result shape (unchanged since before the runner)."""
        cost: Any = self.cost_usd
        if self.engine == "claude_code":
            cost = self.cost_usd or 0
        out: Dict[str, Any] = {
            "result": self.text,
            "session_id": session_id,
            self._SESSION_KEYS.get(self.engine, "engine_session_id"): self.engine_session_id,
            "cost_usd": cost,
            "duration_ms": self.duration_ms,
            "duration_api_ms": self.duration_api_ms,
            "num_turns": self.num_turns,
            "tokens": dict(self.tokens),
            "tool_calls": list(self.tool_calls),
            "log_path": self.log_path,
        }
        if with_schema:
            out["structured_output"] = self.structured_output
        return out
