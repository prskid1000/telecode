"""Adapter contract: an engine is argv + env + stdin + a stream parser.

The runner owns everything else (spawn, Job binding, cancel/timeout, stderr
drain, raw log, progress, event fan-out). An adapter never touches the task
queue or the session store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.engine.types import EngineRequest, EngineResult


@dataclass
class Launch:
    argv: List[str]
    stdin: str
    env: Optional[Dict[str, str]] = None
    # Temp files the runner deletes after the run (e.g. a Codex schema file).
    cleanup: List[Path] = field(default_factory=list)
    # Non-fatal notes the runner emits as ``warning`` events (e.g. a permission
    # mode that had to fall back).
    warnings: List[str] = field(default_factory=list)
    # Where engine_extras args go: index into argv (None = append).
    extras_at: Optional[int] = None


@dataclass
class ParseState:
    session_id: Optional[str] = None
    final: Optional[Dict[str, Any]] = None
    text_parts: List[str] = field(default_factory=list)
    raw_lines: List[str] = field(default_factory=list)
    tool_calls: List[str] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=dict)
    turns: int = 0
    saw_completion: bool = False
    model: Optional[str] = None     # the model the CLI reports it is using (Claude's init event)


def describe_tool(name: str, tool_input: Any) -> str:
    """``Name: <first path/pattern/command/url argument>`` (Claude's summary)."""
    if not isinstance(tool_input, dict):
        return name
    for key in ("file_path", "path", "pattern", "command", "url"):
        if key in tool_input:
            return f"{name}: {tool_input[key]}"
    return name


def tool_event(name: str, summary: Optional[str] = None, tool_input: Any = None) -> Dict[str, Any]:
    """One tool-call event carrying every key any consumer reads
    (``tool``/``summary`` — Claude/Codex style, ``name``/``input`` — agy style)."""
    evt: Dict[str, Any] = {"kind": "tool", "tool": name, "name": name,
                           "summary": summary if summary is not None else name}
    if tool_input is not None:
        evt["input"] = tool_input
    return evt


def todos_from(items: Any, text_key: str, done_key: Optional[str] = None) -> List[Dict[str, str]]:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        if done_key:
            status = "completed" if it.get(done_key) else "pending"
        else:
            status = str(it.get("status") or "pending")
        out.append({"text": str(it.get(text_key) or it.get("text") or it.get("content") or ""),
                    "status": status})
    return out


class Adapter:
    engine = ""
    label = ""                 # progress wording: "launching <label>"
    resume_start_key = ""      # key of the resumed id in the start event
    persist_deltas = False     # agy streams text only as deltas
    stdin_close_wait = 30.0    # proc.wait timeout after stdout EOF

    def build(self, req: EngineRequest) -> Launch:  # pragma: no cover - abstract
        raise NotImplementedError

    def parse(self, evt: Dict[str, Any], st: ParseState) -> List[Dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def raw_line(self, line: str, st: ParseState) -> None:
        st.raw_lines.append(line)

    def usage_event(self, st: ParseState) -> Optional[Dict[str, Any]]:
        return None

    def finish(self, req: EngineRequest, st: ParseState, returncode: Optional[int],
               stderr: str, wall_ms: int) -> EngineResult:  # pragma: no cover
        raise NotImplementedError

    def trailing_events(self, st: ParseState) -> List[Dict[str, Any]]:
        """Events emitted after the stream ends (before usage/done)."""
        return []
