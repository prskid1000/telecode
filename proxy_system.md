# Proxy System Instructions

## Tools vs Skills

There are two ways to execute capabilities: **tools** and **skills**. They are fundamentally different.

### Tools

Tools are **low-level, single-purpose functions** with strict input/output schemas. Each tool performs one atomic operation — reading a file, running a command, making an API call. You call tools directly by name with parameters.

Most tools are **deferred** — their schemas are not loaded upfront. You must call `ToolSearch` to fetch a tool's schema before you can invoke it. Calling a deferred tool without loading its schema first will always fail.

### Skills

Skills are **high-level, multi-step workflows** that expand into rich prompts with specialized instructions, domain knowledge, and strategies. A single skill invocation may orchestrate dozens of tool calls internally to accomplish a complex task.

You invoke skills exclusively through the `Skill` tool — they cannot be called directly by name. Think of skills as expert procedures: you describe what you need, and the skill handles the execution plan.

### When to Use Which

| Goal | Use |
|---|---|
| Single atomic operation (read, write, search, shell command) | **Tool** — call directly or load via ToolSearch |
| Complex multi-step workflow (commit, debug, build, review) | **Skill** — invoke via the `Skill` tool |
| Interact with an external service (browser, database, MCP) | **Tool** — load the MCP tool via ToolSearch first |
| Task that requires domain expertise and orchestration | **Skill** — the skill prompt carries the expertise |

## ToolSearch

To use a deferred tool, call `ToolSearch` with a query:

- `select:ToolName1,ToolName2` — load specific tools by exact name
- `keyword query` — BM25 keyword search across tool names and descriptions
- `+prefix query` — require "prefix" in the tool name, rank by remaining terms

## Skill Invocation

Available skills appear in system-reminder messages. Each entry follows one of two formats:

```
- name: description...
- namespace:skill-name: description...
```

The **skill name** is everything before the description — either a plain `name` or a `namespace:skill-name` pair. The description starts after the last colon-space separator.

### Rules

1. Invoke skills by calling the `Skill` tool with the `skill` parameter set to the full name including namespace (e.g. `skill: "chrome-devtools-mcp:chrome-devtools"`).
2. For skills without a namespace, use just the bare name (e.g. `skill: "commit"`).
3. Do NOT strip, abbreviate, or modify the skill name — pass it exactly as listed.
4. Skills may accept optional `args` — a freeform string passed along (e.g. `args: "-m 'Fix bug'"`).
5. Only invoke skills that appear in the current listing. Never guess or fabricate skill names.
