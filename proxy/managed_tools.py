"""Proxy-managed tools — injected into the model's tool list and intercepted
on tool_use, so the model can call them without a separate MCP connection.

Each tool has an Anthropic-format schema (injected into core tools via
`split_tools`) and an async handler (dispatched in `_handle_streaming`).
CC's versions of the same tools are stripped from the request so the model
only sees ours.

Adding a new managed tool:
  1. Define the schema dict (Anthropic tool format)
  2. Write an async handler: (input_dict) -> str
  3. Call `register(name, schema, handler, strip=["CCToolName"])`
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger("telecode.proxy.managed_tools")

# Handler returns (summary_line, tool_result_content).
# summary_line is displayed to the user (e.g. "Found 5 results (general)").
# tool_result_content is what the model sees in its conversation history.
Handler = Callable[[dict[str, Any]], Awaitable[tuple[str, str]]]


@dataclass
class LLMHook:
    """Declarative LLM pre/post processing for managed tools.

    pre_llm:  model's tool input → LLM → structured result → merged into
              handler args as `_pre_llm` dict. Handler reads what it needs.
    post_llm: handler's result string → LLM → structured result → replaces
              the tool_result content sent back to the model.
    """
    system: str                # system instruction for the LLM
    prompt_template: str       # f-string using tool input keys, e.g. "{query}"
    schema: dict[str, Any]     # JSON schema for structured output
    max_tokens: int = 100


@dataclass
class ManagedTool:
    name: str
    schema: dict[str, Any]
    handler: Handler
    strip_from_cc: list[str] = field(default_factory=list)
    primary_arg: str = ""
    pre_llm: LLMHook | None = None
    post_llm: LLMHook | None = None


_REGISTRY: dict[str, ManagedTool] = {}


def register(
    name: str,
    schema: dict[str, Any],
    handler: Handler,
    strip: list[str] | None = None,
    primary_arg: str = "",
    pre_llm: LLMHook | None = None,
    post_llm: LLMHook | None = None,
) -> None:
    _REGISTRY[name] = ManagedTool(
        name=name, schema=schema, handler=handler,
        strip_from_cc=strip or [],
        primary_arg=primary_arg,
        pre_llm=pre_llm,
        post_llm=post_llm,
    )
    hooks = []
    if pre_llm:
        hooks.append("pre_llm")
    if post_llm:
        hooks.append("post_llm")
    log.info("Registered managed tool: %s (hooks: %s)", name, hooks or "none")


def get_schemas() -> list[dict[str, Any]]:
    return [t.schema for t in _REGISTRY.values()]


def get_strip_names() -> set[str]:
    names: set[str] = set()
    for t in _REGISTRY.values():
        names.update(t.strip_from_cc)
        names.add(t.name)
    return names


def get_handler(name: str) -> Handler | None:
    t = _REGISTRY.get(name)
    return t.handler if t else None


def get_tool(name: str) -> ManagedTool | None:
    return _REGISTRY.get(name)


def is_managed(name: str) -> bool:
    return name in _REGISTRY


async def run_pre_llm(tool: ManagedTool, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Run the pre_llm hook: model input → LLM → structured result → merged into args.

    Returns enriched copy of tool_input with `_pre_llm` dict added.
    If no pre_llm hook or LLM fails, returns tool_input unchanged.
    """
    if not tool.pre_llm:
        return tool_input
    from proxy.llm import structured_call
    try:
        prompt = tool.pre_llm.prompt_template.format(**tool_input)
    except KeyError as exc:
        log.warning("pre_llm prompt_template missing key: %s", exc)
        return tool_input
    full_prompt = f"{tool.pre_llm.system}\n\n{prompt}" if tool.pre_llm.system else prompt
    result = await structured_call(
        full_prompt, tool.pre_llm.schema,
        max_tokens=tool.pre_llm.max_tokens,
        schema_name=f"{tool.name}_pre",
    )
    enriched = dict(tool_input)
    enriched["_pre_llm"] = result
    log.info("pre_llm %s: %s", tool.name, result)
    return enriched


async def run_post_llm(tool: ManagedTool, tool_result: str) -> str:
    """Run the post_llm hook: tool result → LLM → structured output → new result.

    Returns the LLM's response as a formatted string that replaces the
    original tool result. If no post_llm hook or LLM fails, returns
    tool_result unchanged.
    """
    if not tool.post_llm:
        return tool_result
    from proxy.llm import structured_call
    import json
    try:
        prompt = tool.post_llm.prompt_template.format(result=tool_result)
    except KeyError as exc:
        log.warning("post_llm prompt_template missing key: %s", exc)
        return tool_result
    full_prompt = f"{tool.post_llm.system}\n\n{prompt}" if tool.post_llm.system else prompt
    result = await structured_call(
        full_prompt, tool.post_llm.schema,
        max_tokens=tool.post_llm.max_tokens,
        schema_name=f"{tool.name}_post",
    )
    if result:
        log.info("post_llm %s: %s", tool.name, result)
        return json.dumps(result, indent=2)
    return tool_result


def format_visibility(name: str, tool_input: dict[str, Any], summary: str) -> str:
    """Build the CC-native-style visibility line for a managed tool call.

    Format:
        ● ToolName("primary_arg_value")
        └  Summary line

    Generic — works for any managed tool that declares `primary_arg`.
    """
    tool = _REGISTRY.get(name)
    if not tool or not tool.primary_arg:
        return f"● {name}()\n└  {summary}"
    arg_val = str(tool_input.get(tool.primary_arg, ""))
    # Truncate long args for display
    if len(arg_val) > 80:
        arg_val = arg_val[:77] + "..."
    return f"● {name}(\"{arg_val}\")\n└  {summary}"


# ── Tool auto-discovery ────────────────────────────────────────────────
# Drop a .py file in proxy/managed/ calling register(...) at module level
# and it's picked up here. No changes needed in this file to add a new tool.
import proxy.managed  # noqa: F401  triggers pkgutil import of all modules
