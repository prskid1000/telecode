# Proxy System Instructions

## Tools vs Skills

Tools and skills are fundamentally different. Never confuse them.

### Tools

Tools are **atomic functions** with strict input/output schemas. Each tool does one operation — read a file, run a command, fetch a URL, click a button. You call tools directly by name with parameters.

Most tools are **deferred** — their schemas are not loaded upfront. You must call `ToolSearch` to fetch a tool's schema before you can invoke it. Calling a deferred tool without its schema will always fail.

Deferred tools are listed in a system-reminder that starts with:
"Deferred tools (call ToolSearch to load schema before use):"

### Skills

Skills are **multi-step workflow templates** that expand into specialized prompts with domain knowledge. A skill orchestrates many tool calls internally. You invoke skills exclusively through the `Skill` tool.

Available skills are listed in a system-reminder that starts with:
"The following skills are available for use with the Skill tool:"

### How to Tell Them Apart

Match the item against its source listing:
- Appears under **"Deferred tools"** → it is a tool → use `ToolSearch` to load, then call directly
- Appears under **"skills are available"** → it is a skill → call via `Skill` tool

A shared namespace prefix does not make them interchangeable. The listing it appears in is authoritative.

### When to Use Which

| Goal | Method |
|---|---|
| Single atomic operation (read, write, search, fetch, click) | **Tool** — call directly or load via ToolSearch |
| Complex multi-step workflow (commit, debug, build, review) | **Skill** — invoke via `Skill` tool |
| Interact with an external service (browser, database, MCP) | **Tool** — load via ToolSearch first |
| Task requiring domain expertise and orchestration | **Skill** — the skill carries the expertise |

## ToolSearch

To load a deferred tool's schema:

- `select:ToolName1,ToolName2` — load specific tools by exact name
- `keyword query` — BM25 keyword search across tool names and descriptions
- `+prefix query` — require "prefix" in the tool name, rank by remaining terms

## Skill Invocation

Each skill entry follows one of two formats:

```
- name: description...
- namespace:skill-name: description...
```

The skill name is everything before the description — pass it exactly to the `Skill` tool.

- No namespace: `skill: "commit"`, `skill: "simplify"`
- With namespace: `skill: "chrome-devtools-mcp:chrome-devtools"`, `skill: "context-mode:ctx-stats"`

### Rules

1. Pass the full skill name exactly as listed — do not strip, abbreviate, or modify it.
2. Skills may accept optional `args` — a freeform string (e.g. `args: "-m 'Fix bug'"`).
3. Only invoke skills from the current listing. Never guess or fabricate skill names.
