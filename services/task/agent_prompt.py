"""Shared structured-prompt builder for agent tasks (Claude Code, Gemini, ...).

Single source of truth for the XML wire format passed to the underlying CLI.
Mirrors the client-side `buildAgentPromptXml` in proxy/static/telecode.html.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

PROMPT_VERSION = "1"


def _esc(s: Any) -> str:
    return (
        str(s if s is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _file_paths(files: Optional[Iterable[Any]]) -> list[str]:
    out: list[str] = []
    for f in files or ():
        if isinstance(f, Mapping):
            p = f.get("path")
        else:
            p = getattr(f, "path", None) or f
        if p:
            out.append(str(p))
    return out


def render_agent_prompt_xml(
    *,
    agent: Mapping[str, Any],
    job: Mapping[str, Any],
    agent_files: Optional[Iterable[Any]] = None,
    job_files: Optional[Iterable[Any]] = None,
) -> str:
    """Render an <agent_task> XML blob from structured fields.

    `agent` requires `id`, `name`. The agent's identity (SOUL/USER/MEMORY/AGENT)
    is staged into the workspace as separate markdown files and auto-loaded by
    the CLI from CLAUDE.md / GEMINI.md, so this XML does NOT carry an
    <instructions> block.

    `job` requires `workspace_id`, `task_description`.
    `agent_files` / `job_files` are iterables of {"path": ...} or path strings.
    """
    paths = _file_paths(agent_files) + _file_paths(job_files)
    if paths:
        files_block = "  <context_files>\n" + "\n".join(
            f"    <file>{_esc(p)}</file>" for p in paths
        ) + "\n  </context_files>"
    else:
        files_block = "  <context_files/>"

    return "\n".join([
        f'<agent_task version="{PROMPT_VERSION}">',
        f'  <agent id="{_esc(agent.get("id"))}" name="{_esc(agent.get("name"))}"/>',
        f'  <workspace id="{_esc(job.get("workspace_id"))}"/>',
        "  <task_description>",
        _esc(job.get("task_description") or ""),
        "  </task_description>",
        files_block,
        "</agent_task>",
    ])


def resolve_prompt(params: Mapping[str, Any]) -> str:
    """Pick a prompt string from task params.

    Accepts either a pre-rendered `prompt` string (current path — web UI renders
    client-side), or structured fields (`agent`, `job`, optional `agent_files`,
    `job_files`) which are rendered here. Structured fields win when both are
    present.
    """
    agent = params.get("agent")
    job = params.get("job")
    if isinstance(agent, Mapping) and isinstance(job, Mapping):
        return render_agent_prompt_xml(
            agent=agent,
            job=job,
            agent_files=params.get("agent_files"),
            job_files=params.get("job_files"),
        )
    prompt = params.get("prompt")
    if isinstance(prompt, str):
        return prompt
    raise ValueError("task params must include either `prompt` or (`agent` + `job`)")
