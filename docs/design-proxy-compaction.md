# Design: Make the telecode proxy speak the Anthropic compaction beta

> Goal: when Claude Code (or any compaction-aware Anthropic client) runs against
> `ANTHROPIC_BASE_URL=http://localhost:1235/v1`, the proxy should honor the
> `compact-2026-01-12` contract end-to-end so the client cannot tell it isn't
> talking to `api.anthropic.com`. Underneath, llama.cpp knows nothing about
> compaction — the proxy emulates the whole feature on its existing intercept loop.

Companion to [research-claude-code-compact.md](research-claude-code-compact.md).

---

## 0. The contract we must imitate (from the API docs)

**Request (what an official client sends):**
- Header: `anthropic-beta: compact-2026-01-12`
- Body, top-level:
  ```json
  "context_management": {
    "edits": [{
      "type": "compact_20260112",
      "trigger": { "type": "input_tokens", "value": 150000 },
      "instructions": "optional custom summary prompt",
      "pause_after_compaction": false
    }]
  }
  ```
  `trigger.value` min 50 000; default 150 000.

**Response (what we must return):**
- Non-stream — `content` *leads* with a compaction block, then the normal blocks:
  ```json
  "content": [
    { "type": "compaction", "content": "…summary…" },
    { "type": "text", "text": "…main answer…" }
  ],
  "usage": { "input_tokens": …, "output_tokens": …,
    "iterations": [
      { "type": "compaction", "input_tokens": 180000, "output_tokens": 3500 },
      { "type": "message",    "input_tokens": 23000,  "output_tokens": 1000 }
    ] }
  ```
- Stream — `content_block_start{content_block:{type:"compaction"}}` →
  `content_block_delta{delta:{type:"compaction_delta", content:"…"}}` →
  `content_block_stop`, **then** the normal blocks; final `message_delta.usage`
  carries `iterations`. If `pause_after_compaction:true` → emit only the
  compaction block, set `stop_reason:"compaction"`, stop.
- **Next request:** the client re-sends the `compaction` block inside an assistant
  turn; the server drops everything *before* it. There is no id/marker — the
  presence of the block is the boundary.

> Anthropic-only. The OpenAI `/v1/chat/completions` path has no compaction block;
> for Codex-local we can only do the *silent* autonomous variant (§5). Honors
> rule #8 (protocol concerns stay in adapters + translate.py).

---

## Phase 0 — RESULT (measured 2026-06-10, claude-cli/2.1.170, local mode)

**Verdict: Claude Code does NOT use server-side compaction. `/compact` is 100% client-side.**
Confirmed across all three layers against the proxy:

1. **Endpoint** — access log shows *only* `POST /v1/messages?beta=true`. No compaction
   endpoint, no capability call, no `count_tokens`, no 404s.
2. **Body** — `context_management` absent from every request.
3. **Header** — `anthropic-beta` =
   `claude-code-20250219, interleaved-thinking-2025-05-14, mid-conversation-system-2026-04-07,
   effort-2025-11-24, structured-outputs-2025-12-15`. **`compact-2026-01-12` is NOT in the list** —
   CC doesn't even advertise the capability. (`anthropic-version: 2023-06-01`.)

A successful `/compact` ("Compacted") was observed while none of the above changed — so CC
summarized locally (ordinary completion calls) and rewrote its own transcript, then continued
with the summary as the first message of a fresh conversation (`<session>…Summary: 1. Primary
Request and Intent…`).

**Consequence:** Phases 1–4 (beta emulation) are **unnecessary for Claude Code** — the proxy
already "appears official" because CC never asks the server to compact. The only value-add left
is the **autonomous ctx-size watchdog (§5)** as a backstop for clients that *don't* self-compact.
Header capture is via `header_log_middleware` → `data/logs/request_headers.log`.

> Betas CC *does* send that the proxy already handles: `effort-*` (→ `output_config.effort`,
> translate.py L447) and `structured-outputs-*` (→ `response_format`, L588). Good — those are live.

---

## Phase 0 (original) — VERIFY the client actually sends the beta

Everything below is wasted if Claude Code never emits `context_management` against
a custom base URL. **This is undocumented — measure it, don't assume.**

1. Set `proxy.debug: true`, point `claude` at the proxy in local mode, drive a
   session past ~150k tokens (or lower `ctx_size`).
2. Inspect `data/logs/proxy_full_*.json` for an inbound request carrying
   `context_management` / the `anthropic-beta: compact-2026-01-12` header.
3. Branch on the result:
   - **Beta arrives** → build the full emulation (Phases 1–4). The most likely
     trigger for CC to enable it is that `proxy.model_mapping` maps a real Claude
     alias (`claude-sonnet-4-6` → local GGUF), so CC believes it's a genuine model.
   - **Beta never arrives** (CC silently does client-side compaction, or gates the
     beta on the real endpoint) → the deterministic win is the **autonomous
     ctx-size watchdog (§5)**, which keeps the local prompt under `ctx_size`
     regardless. Build that; treat the beta emulation as opportunistic.

Either way §3 (drop pre-compaction history) is required — it's what makes
compaction actually shrink the prompt.

---

## Phase 1 — Detect & parse the directive

**File: `proxy/server.py`** — the raw client body is available in the handler
(`handle_anthropic_messages`, ~L1399) and at the top of `_prepare_internal_body`
(~L513) *before* `anthropic_request_to_internal` discards unknown keys.

Add a small dataclass + parser (new `proxy/compaction.py`):

```python
@dataclass
class CompactionDirective:
    enabled: bool
    trigger_tokens: int          # clamped to >= 50_000
    instructions: str | None
    pause_after: bool

def parse_directive(body, headers) -> CompactionDirective | None:
    if "compact-2026-01-12" not in headers.get("anthropic-beta", ""):
        return None
    for e in (body.get("context_management") or {}).get("edits", []):
        if e.get("type") == "compact_20260112":
            trg = (e.get("trigger") or {}).get("value", 150_000)
            return CompactionDirective(True, max(50_000, int(trg)),
                                       e.get("instructions"),
                                       bool(e.get("pause_after_compaction")))
    return None
```

In `_prepare_internal_body`: call `parse_directive(body, request.headers)`, then
strip `context_management` from `body` (so it never reaches the translator), and
return the directive in the `prep` dict alongside `profile`, `reasoning_cfg`, etc.

---

## Phase 2 — Honor the trigger & run the synthetic summarization round

**File: `proxy/server.py`**, inside `_run_streaming` / `_run_non_streaming`, after
`supervisor.ensure_model(active_model)` and before the main round loop.

1. **Count tokens** of `body["messages"]` via `proxy.tokenizer.count_tokens`
   (already used by `handle_count_tokens`). Gate the cost: only tokenize when a
   directive is present *or* a cheap char-estimate already looks large.
2. If `count >= directive.trigger_tokens` → run the **compaction round**
   (`proxy/compaction.py::summarize`):
   - Build a sub-body = copy of `body` with:
     - `messages` = the slice to compact (keep the leading system turn + a short
       recent tail per a `keep` policy; everything between gets summarized),
     - append a final user turn = `directive.instructions` or the default summary
       prompt (mirror CC's structured template: intent / files / decisions /
       pending work),
     - `stream:false`, `tools` removed, `response_format`/`grammar` cleared,
       reasoning forced off, `max_tokens = summary_max_tokens`.
   - Optionally route to `compaction.summary_model` (a bigger-ctx model) via
     `ensure_model` — summarizing a huge transcript is exactly when a small local
     ctx hurts. Swap back after (this *is* a model swap → restart cost; make it
     opt-in).
   - POST to `{upstream}/v1/chat/completions`; capture summary text + usage.
   - Wrap `begin_request()/end_request()` around it so idle-unload can't tear down
     llama-server mid-summary (same pattern as `_run_upstream_round`, L731/756).
3. **Rewrite history** for the main round:
   `body["messages"] = [system?] + [{"role":"assistant","content":summary}] + tail`.
   (For llama.cpp the summary is just a normal assistant text turn — this is what
   shrinks the prefill and keeps us under `ctx_size`. No restart, prefix cache
   reuses `[system]`; see research doc §"no restart".)
4. Record an intercept log entry `{"type":"compaction", "before":N, "after":M}`.

---

## Phase 3 — Drop pre-compaction history on later requests (REQUIRED)

**File: `proxy/translate.py`**, in `anthropic_request_to_internal` (L416) and
`_decompose_anthropic_message` (L106).

- Before decomposing `body["messages"]`, scan for the **last** assistant message
  containing a `{"type":"compaction"}` block. If found, truncate the list to
  `[leading system] + [that message] + [everything after it]`.
- In `_decompose_anthropic_message`, add a branch for `btype == "compaction"`:
  emit it as a plain assistant text message (`content = block["content"]`) so
  llama.cpp sees the summary as ordinary context. (Today an unknown `compaction`
  block is silently skipped at L137-158 — it must instead become text, or the
  summary is lost.)

This mirrors the real API and is idempotent: works whether *we* generated the
block (Phase 4) or the client carried one over.

---

## Phase 4 — Emit the compaction block in the response

**Streaming — `proxy/server.py` `AnthropicAdapter` + `proxy/translate.py`
`AnthropicStreamState`:**
- Add `emit_anthropic_compaction_block(summary, index)` to translate.py, parallel
  to `emit_anthropic_status_block` (L1081): `content_block_start` with
  `content_block:{type:"compaction"}` → `content_block_delta` with
  `delta:{type:"compaction_delta","content":chunk}` → `content_block_stop`.
- In `_run_streaming`, after `initial_frame()` (L949) and before the main round,
  if a summary was produced, write the compaction block at the first content index
  (reuse the `status_emitted` counter so real content indices shift up by one).
- `pause_after_compaction:true` → after the block, synthesize a
  `message_delta{delta:{stop_reason:"compaction"}}` + `message_stop` and **return**
  without running the main round. (Add `"compaction"` to the stop-reason vocab;
  don't route it through `_finish_reason_to_anthropic`.)
- Otherwise run the normal loop; accumulate the compaction round's usage and emit
  it as `usage.iterations[]` on the final `message_delta` (extend the payload at
  L1031-1046).

**Non-streaming — `proxy/translate.py` `openai_response_to_anthropic` (L1126):**
- Thread an optional `compaction_summary` + `compaction_usage` param; when present,
  insert `{"type":"compaction","content":summary}` at the head of `content_blocks`
  (L1146) and add `usage.iterations` (L1204).

---

## Phase 5 — Autonomous ctx-size watchdog (the deterministic fallback)

Independent of any client directive — the robust win for small local models.
Same machinery as Phase 2, but the trigger is the proxy's own:

```python
if count > compaction.auto_trigger_fraction * ctx_size_for(active_model):
    summary = await summarize(...)   # then rewrite messages (Phase 2.3)
```

- For **Anthropic clients** with the beta: surface it as a real compaction block.
- For **Anthropic clients without the beta** and **all OpenAI clients** (Codex):
  compact **silently** — just rewrite `messages`, emit no block. The client never
  knows; the prompt simply stays within `ctx_size`. This is the piece that makes
  local-mode sessions survive long runs regardless of client cooperation.

---

## Phase 6 — Config, tray, observability

**`settings.json` → `proxy.compaction.*`** (read via `proxy/config.py` accessors,
per rule #2), optionally overridable per `client_profile`:
- `enabled` (honor inbound beta), `auto.enabled` + `auto.fraction` (watchdog),
- `default_trigger_tokens`, `summary_max_tokens`, `instructions`,
- `summary_model` (optional bigger-ctx route), `emit_block` (surface vs silent).

**Tray (`tray/qt_sections.py`)** — a "Compaction" card in the Proxy section;
`_dependent` rows so `auto.fraction` greys out when `auto.enabled` is off
(same pattern as the ngram/draft knobs).

**Observability (`proxy/request_log.py`)** — log every compaction
(`before`/`after` tokens, trigger source: beta vs watchdog). Optional, log-only:
the CC-summary-prompt sniff and the cross-request reset detector from the
conversation analysis — useful tags in the request viewer, never control logic.

---

## Rules / caveats to respect

- **No model restart for compaction** — it only rewrites `messages`; llama.cpp
  reuses the `[system]` prefix and prefills the shorter remainder (`cache_prompt`,
  translate.py L605). Only the optional `summary_model` route incurs a swap.
- **Min trigger 50 000** (API rule) — clamp.
- **Tokenizer round-trip cost** — gate `/tokenize` so we don't pay it on every
  small request.
- **OpenAI path** gets silent compaction only (no compaction block exists there).
- **`max_roundtrips`** still bounds the main loop; the compaction round is extra
  and must be wrapped in `begin_request`/`end_request`.
- **`cache_control`** already stripped recursively — nothing to add.

---

## Test plan

1. **Unit** — `parse_directive` (header+body combos); Phase-3 truncation
   (compaction block mid-history → only system+summary+tail survive);
   `emit_anthropic_compaction_block` SSE shape; `iterations` usage math.
2. **Wire fidelity** — golden-file the streamed SSE for (a) compact+continue and
   (b) `pause_after_compaction`, diffing against the documented event order.
3. **Integration** — real `claude` in local mode, drive past the trigger, confirm:
   the compaction block appears, the *next* request drops pre-summary history, and
   `tasklist` shows llama-server never restarted.
4. **Watchdog** — Codex-local long session; confirm prompt stays under `ctx_size`
   and no block leaks into OpenAI output.
```
