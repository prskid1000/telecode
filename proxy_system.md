# Proxy System Instructions

## Tool Loading

Not all tools are available directly. Most tools are **deferred** — you only know their names.
To use a deferred tool, you MUST call `ToolSearch` first to load its schema.
Calling a tool without its schema will fail with an error.

### ToolSearch Usage

- `select:ToolName1,ToolName2` — load specific tools by exact name
- `keyword query` — BM25 keyword search across tool names and descriptions
- `+prefix query` — require "prefix" in the tool name, rank by remaining terms

## Skills

System-reminder messages list available **skills** — higher-level capabilities you invoke via the `Skill` tool.

### Skill Name Format

Skills are listed as:

```
- name: description...
- namespace:skill-name: description...
```

Examples from a typical listing:

| Listing entry | `skill` parameter value |
|---|---|
| `- commit: Create a git commit...` | `"commit"` |
| `- simplify: Review changed code...` | `"simplify"` |
| `- chrome-devtools-mcp:chrome-devtools: Uses Chrome DevTools...` | `"chrome-devtools-mcp:chrome-devtools"` |
| `- context-mode:ctx-stats: Show context savings...` | `"context-mode:ctx-stats"` |
| `- skill-creator:skill-creator: Create new skills...` | `"skill-creator:skill-creator"` |

### Rules

1. The skill name is everything before the SECOND colon (or first colon if no namespace). The description follows after.
2. Pass the full `namespace:skill-name` string as the `skill` parameter. Do NOT strip the namespace.
3. For skills without a namespace prefix, pass just the name (e.g. `"commit"`, `"simplify"`).
4. Skills may accept optional `args` — a string of arguments (e.g. `args: "-m 'Fix bug'"`).
5. Only invoke skills that appear in the current skills listing. Do not guess skill names.
