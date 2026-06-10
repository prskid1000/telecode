# Research: How `/compact` Works in Claude Code

> Question investigated: *Is context compaction a Claude Code (client) feature, a backend/API feature, or a model capability?*
>
> **Short answer:** It's **both** — a two-layer feature. Claude Code (the CLI harness) decides *when and why* to compact and stitches the result back into the session; the **Anthropic API + the model** do the *actual summarization*. It is not a magic property of the model alone, and it is not purely client-side string trimming.

---

## 1. What `/compact` is

`/compact` frees up context by **summarizing the conversation so far** and replacing the older history with that summary, so you can keep working in the *same* session without hitting the context-window ceiling.

- Optional focus instructions: `/compact focus on the API changes`.
- Preserves: your requests/intent, key technical decisions, code snippets, files touched, errors + fixes, pending tasks.
- Discards: verbose tool outputs (full test logs, file dumps), intermediate reasoning, command spew.

**vs `/clear`:** `/clear` wipes context and starts fresh (old session still reachable via `/resume`). `/compact` keeps continuity by compressing.

---

## 2. Where it's implemented (the architecture question)

Compaction is a **dual-layer** feature:

### Client side — Claude Code CLI harness
- Owns the `/compact` command and the **auto-compaction** lifecycle.
- Monitors input-token usage (visible via `/context`).
- Decides *when* to trigger (approaching the window limit), *what* prompt to use, and how to **re-inject startup content** afterward.
- Detects pathological "thrashing" and bails out with a warning.

### API / backend side — Anthropic Claude API
- Exposes the underlying capability as an API beta: `context_management.edits[].type: "compact_20260112"` (header `anthropic-beta: compact-2026-01-12`).
- The **model itself** performs the summarization when called with the summarization prompt.
- The API returns a structured **`compaction` block** containing the summary.

> **Decision layer = Claude Code. Summarization work = model, via the API.**
> Claude Code is orchestration on top of the API's compaction primitive — not a model parameter you flip, and not just local truncation.

```
Claude Code /compact
    │ (decides when, builds prompt)
    ▼
Anthropic API  (compact-2026-01-12 beta)
    │ (calls model with summarization prompt)
    ▼
Claude model   (writes the summary)
    │ (API returns)
    ▼
compaction block  →  becomes the new "head" of the conversation
```

---

## 3. How the mechanism works (step by step)

1. **Detection** — Claude Code watches input tokens; triggers on threshold or on manual `/compact`.
2. **Summarization prompt** — model is asked to write a structured summary (intent, concepts, files, snippets, fixes, pending work). A custom `/compact <instructions>` **replaces** the default prompt entirely.
3. **API returns a `compaction` block** — the summary in a structured container.
4. **Context replacement** — on the next request, Claude Code **drops all message blocks before the compaction block**. History becomes `[summary] → [new messages]`.
5. **Preserved vs discarded** — preserved: prompts, decisions, snippets, fixes, next steps. Discarded: full tool outputs, intermediate reasoning, verbose file reads.
6. **What reloads after compaction:**
   - ✅ Re-injected automatically: system prompt, CLAUDE.md (project + global), auto-memory (MEMORY.md), MCP tool names, environment info.
   - ❌ **Not** re-injected: skill descriptions (only skills you actually invoked survive — capped ~5K/skill, ~25K total), path-scoped rules (lost until the matching file is read again).
   - N/A: hooks (they're code, not context).

---

## 4. Auto-compact / micro-compact

- Runs **automatically** in the background as you approach the window limit; you see a "Conversation compacted" notice.
- Default threshold around **~150K tokens** on standard 200K-window models (configurable on the API side).
- Not periodic — fires only when needed.
- **Thrashing guard:** if context immediately refills to the limit several times (e.g. a huge file read balloons it right back), Claude Code stops retrying and warns: *"Autocompact is thrashing…"*. Recovery: read files in chunks, `/compact` with a focus, delegate to a subagent, or `/clear`.

---

## 5. Custom instructions

Yes:
```
/compact focus on the API changes
/compact keep only the plan and the diff
/compact focus on the solution, drop the failed approaches
```
The instructions **replace** the default summarization prompt (they don't merely append to it).

---

## 6. Settings / config

**Claude Code (CLI):** compaction is always enabled; no per-session off toggle. `/context` monitors usage; `/memory` manages what auto-reloads; `/model` affects the context limit (Fable 5 / Opus 4.6+ / Sonnet 4.6 support 1M windows — compaction still behaves the same).

**Anthropic API (used internally):**
- `context_management.edits[].type: "compact_20260112"`
- `trigger: {"type": "input_tokens", "value": 100000}` — custom threshold
- `instructions: "..."` — custom summarization prompt
- `pause_after_compaction: true` — halt after summarizing for review
- header `anthropic-beta: compact-2026-01-12`

---

## 7. `/compact` vs `/clear`

| | `/compact` | `/clear` |
|---|---|---|
| Session | Continues same session | New session (old in `/resume`) |
| Context | Summarized; progress kept | Wiped; true fresh start |
| Use when | Long session filling up | Unrelated next task |
| History | Available (compressed) | Gone |

---

## 8. Relationship to other context-management features

- **Context editing (beta, `context-editing-2026-01-12`):** surgical, programmatic per-message insert/remove. *Different from compaction* (automatic summarization). Not exposed as a Claude Code user command.
- **Memory tool:** agent-invoked persistent storage *across* conversations. Not the same as user-authored auto-memory (MEMORY.md/CLAUDE.md). Compaction doesn't touch it — it lives outside the window.
- **Prompt caching:** orthogonal cost-optimization layer; composes fine with compaction (cache the system prompt; compact the message history). Claude Code uses it internally.

---

## Bottom line

| Aspect | Answer |
|---|---|
| Is it "Claude Code internal"? | **Partly** — the orchestration (when/why/re-inject) is Claude Code. |
| Is it in the backend/API? | **Yes** — the compaction primitive is an Anthropic API beta. |
| Is it the model? | **Yes** — the model writes the summary. |
| Net | Client decides + API primitive + model summarizes = three layers cooperating. |

---

## Sources

- Compaction — Anthropic API docs: https://platform.claude.com/docs/en/build-with-claude/compaction.md
- Commands reference (`/compact`): https://code.claude.com/docs/en/commands.md#compact
- How Claude Code works — context window: https://code.claude.com/docs/en/how-claude-code-works.md#the-context-window
- Explore the context window: https://code.claude.com/docs/en/context-window.md
- Troubleshooting — auto-compaction thrashing: https://code.claude.com/docs/en/troubleshooting.md#auto-compaction-stops-with-a-thrashing-error
