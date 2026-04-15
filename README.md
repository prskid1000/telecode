# Telecode

Telegram bot that connects AI coding CLIs (Claude Code, Codex), screen image capture, and screen video recording to a Telegram group. Each session runs in its own forum topic.

Developer docs (architecture, internals, extending) are in [CLAUDE.md](CLAUDE.md).

## Setup

### Requirements

- Python 3.11+
- Node.js 18+ (for Claude Code, Codex)
- ffmpeg (for video recording)

### 1. Install CLI tools

```bash
npm install -g @anthropic-ai/claude-code
npm install -g @openai/codex
```

### 2. Create a Telegram bot

1. Message @BotFather on Telegram, send `/newbot`
2. Copy the bot token

### 3. Create a Telegram group with Topics

1. Create a new group, add your bot
2. Enable Topics in group settings
3. Make the bot admin with: Manage Topics, Send Messages, Edit Messages
4. Get the group ID: add @userinfobot to the group, copy the chat ID, remove it
5. Get your user ID: DM @userinfobot

### 4. Install

```bash
cd telecode
python3 -m venv .venv
source .venv/bin/activate    # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

### 5. Configure

Copy the example and fill in your values:

```bash
cp settings.example.json settings.json
```

Edit `settings.json` — at minimum set these three fields:

```json
{
  "telegram": {
    "bot_token": "your-token-from-botfather",
    "group_id": -100your-group-id,
    "allowed_user_ids": [your-user-id]
  }
}
```

See `settings.example.json` for all available options. `settings.json` is gitignored — never commit it.

API keys can be set from Telegram:

```
/settings tool claude env ANTHROPIC_API_KEY sk-ant-...
/settings tool codex  env OPENAI_API_KEY   sk-...
```

### 6. Run

```bash
python main.py
```

To run in the background without a console window (Windows):

```bash
pythonw main.py
```

#### Auto-start on login (Windows)

Create a scheduled task (requires admin):

```powershell
Register-ScheduledTask -TaskName "Telecode" `
  -Action (New-ScheduledTaskAction -Execute "pythonw.exe" -Argument "main.py" -WorkingDirectory "C:\path\to\telecode") `
  -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) `
  -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)) `
  -Force
```

Use `pythonw.exe` (not `python.exe`) to keep the terminal hidden.

### HTTPS access via Tailscale Funnel

Exposes the proxy and MCP server over HTTPS with a persistent domain. Required for integrations that run in browser sandboxes (e.g. Claude for Excel/PowerPoint/Word add-ins).

**One-time setup:**

1. Install Tailscale:

   ```powershell
   winget install Tailscale.Tailscale
   ```

2. Log in and enable HTTPS + Funnel on your tailnet:

   ```bash
   tailscale login
   tailscale cert  # enables HTTPS for your machine
   ```

3. Enable Funnel in the [Tailscale admin console](https://login.tailscale.com/admin/acls) — add `"nodeAttrs"` with `"funnel"` capability (see [Tailscale Funnel docs](https://tailscale.com/kb/1223/funnel)).

4. Add CORS and host settings to `settings.json`:

   ```json
   {
     "proxy": {
       "enabled": true,
       "host": "0.0.0.0",
       "cors_origins": ["https://pivot.claude.ai"]
     },
     "mcp_server": {
       "enabled": true,
       "host": "0.0.0.0",
       "cors_origins": ["https://pivot.claude.ai"]
     }
   }
   ```

5. Start Telecode — it auto-detects `tailscale` on PATH and starts Funnel subprocesses:
   - `https://<machine>.<tailnet>.ts.net` → proxy (port 1235)
   - `https://<machine>.<tailnet>.ts.net:8443` → MCP server (port 1236)

   Funnel processes die with the bot. If Tailscale is not installed, a warning is logged and the bot runs normally without HTTPS.

**Using with Claude Office add-ins:**

1. Install "Claude by Anthropic" from Microsoft AppSource in Excel/PowerPoint/Word
2. On the sign-in screen, select **Enterprise gateway**
3. Enter gateway URL: `https://<machine>.<tailnet>.ts.net`
4. Enter any API token (the proxy accepts any value)
5. The add-in auto-discovers models from LM Studio via `/v1/models`

The proxy converts LM Studio's OpenAI model list to Anthropic format automatically.

### 7. First session

Send `/start` in the group, or `/new claude work`.

---

## Commands

| Command | Description |
|---|---|
| `/start` | Choose a backend to start |
| `/new <backend> [name]` | Start a named session (e.g. `/new claude work`) |
| `/stop [session_key]` | Stop current session, or all from General (e.g. `/stop claude:work`) |
| `/key <key>` | Send a keyboard key to the terminal |
| `/settings` | Configuration |
| `/help` | List all commands |

### Screen image capture

Capture any window and stream screenshots to a topic (interval = `capture.image_interval` seconds, default 15):

```
/new screen myapp
```

Pick a window from the list. Each frame is sent as a new photo message. The inline control buttons follow the **latest** frame — as new photos arrive, buttons are stripped from the previous one so only the most recent message is interactive:

- ⏸ Pause / ▶ Resume -- pause/resume streaming
- ⏹ Stop -- stop capture (or `/stop screen:myapp` from anywhere)

**Platform support:**
- **Windows**: Uses PrintWindow API -- captures the specific window even if behind other windows
- **Linux**: Uses ImageMagick `import -window`, falls back to mss region capture
- **macOS**: Uses `screencapture -l<wid>`, falls back to mss region capture

Minimized windows are automatically restored before capture.

### Computer control (vision LLM)

Control any window or the entire screen using a vision-capable LLM. The bot captures screenshots, sends them to the LLM, and executes the LLM's actions (click, type, key, scroll):

```
/new computer
```

Pick a window or "Full Screen" from the list. Then send natural language instructions:

- "Navigate to google.com"
- "Click on the search box and type hello"
- "Scroll down and click the blue button"

The LLM performs one action at a time, verifying the result after each step. A screenshot is sent to the topic after each action. The loop continues until the LLM marks the task as done.

**Configuration** in `settings.json` under `tools.computer`:

```json
"computer": {
  "api": {
    "base_url": "http://localhost:1234/v1",
    "api_key": "lm-studio",
    "model": "qwen3.5-9b",
    "format": "openai"
  },
  "capture_interval": 3,
  "max_history": 20,
  "system_prompt": ""
}
```

Works with any OpenAI-compatible or Anthropic-compatible vision API (LM Studio, Ollama, vLLM, etc.), or directly via **Claude Code CLI**. Set `api.format` to `"openai"` (default), `"anthropic"`, or `"claude-code"` to switch. Requires a vision-capable model.

**`claude-code` format** runs `claude -p` as a subprocess with `--output-format json` and `--json-schema`. When `base_url`, `api_key`, or `model` are set, they are passed as `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, and `ANTHROPIC_MODEL` env vars — so the same settings block works for both cloud and local (LM Studio) backends. Leave them empty to use Claude Code's default configuration. Conversation continuity is maintained via `--resume`.

Controls:
- Send a new message to interrupt and give a new instruction
- ⏸ Pause / ▶ Resume inline buttons under the capture message
- ⏹ Stop inline button (or `/stop` from anywhere)

**Requirements:** `pyautogui` (included in requirements.txt)

### Screen video capture

Record a window continuously in video chunks (length = `capture.video_interval` seconds, default 60):

```
/new video myapp
```

Pick a window from the list. The bot records at 3fps, encodes each chunk with ffmpeg (libx264, ultrafast, lightweight), and sends it as a video message. Recording continues until stopped. Each chunk carries inline control buttons and buttons are stripped from the previous chunk as the new one arrives (only the latest video is interactive):

- ⏸ Pause / ▶ Resume -- pause/resume recording (paused time doesn't count)
- ⏹ Stop -- stop recording (or `/stop video:myapp` from anywhere)

### Terminal keys (`/key`)

| Command | Key |
|---|---|
| `/key enter` | Enter |
| `/key esc` | Escape |
| `/key tab` | Tab |
| `/key backspace` | Backspace |
| `/key space` | Space |
| `/key delete` | Delete |
| `/key up` / `down` / `left` / `right` | Arrow keys |
| `/key home` / `end` | Home / End |
| `/key pgup` / `pgdn` | Page Up / Down |
| `/key f1` .. `/key f12` | Function keys |
| `/key ctrl c` | Ctrl+C (interrupt) |
| `/key ctrl d` | Ctrl+D (EOF) |
| `/key alt x` | Alt+X |
| `/key ctrl shift a` | Ctrl+Shift+A |
| `/key y` / `/key n` | Single characters |

### Settings (`/settings`)

| Command | Description |
|---|---|
| `/settings` | Show config summary |
| `/settings reload` | Hot-reload from disk |
| `/settings validate` | Check for issues |
| `/settings get <path>` | Read a value (e.g. `voice.stt.enabled`) |
| `/settings set <path> <value>` | Set and save |
| `/settings tool [key]` | Tool config |
| `/settings tool <key> cmd <cmd...>` | Set startup command |
| `/settings tool <key> flag add/remove <flag>` | Manage flags |
| `/settings tool <key> env <VAR> <value>` | Set env var |
| `/settings tool <key> env <VAR> --delete` | Remove env var |
| `/settings voice stt on/off/url/model` | Voice config |

### Voice

Send a voice message in a session topic -- transcribed via STT and sent as text. Active whenever the STT endpoint health probe succeeds; silently skipped when the endpoint is down. Requires an OpenAI-compatible STT endpoint (default `http://localhost:6600/v1`).

---

## Settings reference

All options live in `settings.json`. See [`settings.example.json`](settings.example.json) for a complete template. Use the `TELECODE_SETTINGS` env var to point to a different file. Every optional feature is gated by its own `enabled` flag — nothing turns on unless you flip it.

### `telegram` — bot credentials & access control

| Key | Description |
|---|---|
| `bot_token` | Token from @BotFather |
| `group_id` | Forum supergroup ID (starts with `-100`) |
| `allowed_user_ids` | List of Telegram user IDs allowed to use the bot. Empty = open to all |

### `paths` — state storage

| Key | Description |
|---|---|
| `store_path` | JSON file for topic↔session mapping (default `./data/telecode.json`). Relative paths are resolved from the directory that contains `settings.json` (or `TELECODE_SETTINGS`), not from the process current working directory. |
| `logs_dir` | Log directory (default `./data/logs`). Same resolution rule as `store_path` — keeps `telecode.log` next to `proxy_full_*.json` even when the bot is started from another folder (e.g. `pythonw` / Scheduled Task). |

### `streaming` — Telegram live-message tuning

| Key | Description |
|---|---|
| `interval_sec` | Seconds between live-message edits (default `0.8`) |
| `max_message_length` | Max chars before splitting into a new message (default `3800`) |
| `idle_timeout_sec` | Auto-stop a session after N idle seconds (`0` = off) |

### `voice.stt` — transcribe Telegram voice messages

| Key | Description |
|---|---|
| `enabled` | Turn on voice-to-text |
| `base_url` | OpenAI-compatible STT endpoint (e.g. `http://localhost:6600/v1`) |
| `model` | STT model name (e.g. `whisper-1`) |

Activation is automatic: when the endpoint health probe succeeds voice messages are transcribed; otherwise they're silently skipped.

### `capture` — screen capture cadence

| Key | Description |
|---|---|
| `image_interval` | Seconds between image capture sends (default `15`) |
| `video_interval` | Seconds per video chunk (default `60`) |

### `mcp_server` — expose tools over MCP

| Key | Description |
|---|---|
| `enabled` | Start the MCP server |
| `host` | Listen address (default `127.0.0.1`) |
| `port` | Listen port (default `1236`) |
| `tts_url` | Kokoro TTS base URL for the `speak` tool (default `http://127.0.0.1:6500`) |
| `stt_url` | Whisper STT base URL for the `transcribe` tool (default `http://127.0.0.1:6600`) |
| `cors_origins` | CORS allowed origins — empty = disabled |

Ships with `speak`, `transcribe`, `web_search`. Add new tools by dropping a `.py` file under `mcp_server/tools/`.

Register with CC: `claude mcp add telecode --transport streamable-http --url http://127.0.0.1:1236/mcp`

### `proxy` — middleware for local models

Sits between Claude Code (or any Anthropic-API client) and LM Studio / Ollama / any OpenAI-compatible backend. Strips tools to keep the context small, injects managed tools the proxy executes locally, and routes per-client via profiles.

| Key | Description |
|---|---|
| `enabled` | Start the proxy |
| `host` | Listen address (default `127.0.0.1`; use `0.0.0.0` for Tailscale Funnel) |
| `port` | Listen port (default `1235`) |
| `upstream_url` | Backend URL (default `http://localhost:1234` — LM Studio) |
| `debug` | Dump every request body to `data/logs/proxy_full_*.json` |
| `ping_interval` | Seconds between `event: ping` heartbeats sent to the client during long streams (default `10`). `: keepalive` SSE comments still go out every 2s. |
| `max_roundtrips` | Max intercept round-trips per request before giving up (default `15`). Each ToolSearch / managed tool / auto-load / unloaded-guard consumes one. |
| `tool_search` | Split incoming tools into core (always forwarded) + deferred (searchable via ToolSearch) |
| `strip_reminders` | Drop `<system-reminder>` blocks from messages |
| `auto_load_tools` | Auto-load a deferred tool's schema the first time the model calls it blindly |
| `lift_tool_result_images` | Lift images out of array-form `tool_result` content — LM Studio workaround |
| `location` | User location for the date/location system-reminder (empty = auto-detect via IP) |
| `core_tools` | Default list of tool names that stay core when splitting (profiles can override) |
| `cors_origins` | CORS allowed origins |
| `model_mapping` | Rewrite model IDs: `{"claude-opus-4-6": "qwen3.5-35b-a3b"}` — applied to `/v1/messages` and `/v1/models` |
| `client_profiles` | Per-client behavior routing — see below |

Point `ANTHROPIC_BASE_URL=http://localhost:1235` at the proxy. Also runs standalone: `python -m proxy`.

**Managed tools** — a single drop-in folder, two transports:

- Drop a `.py` in **`mcp_server/tools/`** and it's exposed via both the MCP streamable-HTTP endpoint AND the proxy's intercept loop (auto-bridged by `proxy/managed_tools.py`). Currently registered: `code_execution`, `speak`, `transcribe`, `web_search`.

**Adding a tool:**

```python
# mcp_server/tools/my_tool.py
from mcp_server.app import mcp_app

@mcp_app.tool()
async def my_tool(query: str, limit: int = 10) -> str:
    """One-line description.

    Args:
        query: what to look up
        limit: max results
    """
    return do_thing(query, limit)
```

That's it. FastMCP derives the schema from your type hints + docstring.

**Optional module attributes** — set any of these at module level to customize bridge behavior:

```python
_primary_arg = "query"              # which arg to show in the visibility line
_strip_from_cc = ["my_tool", "MyTool"]  # client tool names to replace
_pre_llm = LLMHook(...)             # enrich args via LLM before handler runs
_post_llm = LLMHook(...)            # rewrite result via LLM before returning
```

Defaults are inferred from the schema if unset.

#### Client profiles (`proxy.client_profiles`)

Match requests by header substring and apply per-client transforms. First match wins; no match = global `proxy.*` defaults. System instruction markdown files live under `proxy/instructions/`.

```json
{
  "name": "office",
  "match": {"header": "Referer", "contains": "pivot.claude.ai"},
  "system_instruction": "office.md",
  "tool_search": false,
  "inject_date_location": false,
  "inject_managed": ["code_execution", "web_search"]
}
```

(`inject_managed: ["code_execution"]` already strips the client's `code_execution` and replaces it with our sandbox-backed version — so `strip_tool_names` only needs to cover what we're *not* injecting a replacement for, here `web_search`. `strip_cache_control` defaults to `true` globally — LM Studio rejects the field — so it's omitted here.)

| Key | Description |
|---|---|
| `name` | Label for logging |
| `match.header` | Header name to check (case-insensitive) |
| `match.contains` | Substring that must appear in the header value |
| `system_instruction` | Markdown file in `proxy/instructions/` — prepended to the client's system prompt |
| `tool_search` | Split tools into core + deferred, inject ToolSearch, intercept its calls. Self-contained feature |
| `inject_date_location` | Add the date/location `<system-reminder>` |
| `strip_reminders` | Strip `<system-reminder>` blocks from messages |
| `lift_tool_result_images` | Lift image blocks out of array-form tool_results (LM Studio workaround) |
| `auto_load_tools` | When `tool_search` defers tools: if `true`, auto-load a deferred tool's schema on first blind call; if `false`, deferred tools must be loaded via ToolSearch first |
| `core_tools` | Tool names that stay core when splitting. Falls back to `proxy.core_tools` |
| `inject_managed` | List of managed-tool names to inject (e.g. `["web_search", "speak", "code_execution"]`). Strips client's same-name versions and replaces them. Default = all registered managed tools |
| `strip_tool_names` | Drop tools by exact name. For Anthropic server-side tools, the name is stable across versions (`web_search`, `code_execution`) so one entry catches every version |
| `strip_cache_control` | Remove `cache_control` keys (defaults to `true` — LM Studio rejects the field) |

The **office** profile unlocks Claude for Excel/PowerPoint/Word against a local model — Office add-ins silently retry unless every turn returns a `tool_use` block, so this profile preserves their tools, strips Anthropic-hosted ones (`web_search_20250305`, `code_execution_20250825`), and swaps in an Office-aware system prompt.

**Intercepts** — five active branches, each produces a `tool_result` for the model plus a live status line for the user:

1. **`ToolSearch`** — BM25 over deferred tools (`select:Name,Name` / `+prefix rest` / keyword search). Returns matching `<functions>` block.
2. **Managed tools** — `web_search`, `code_execution`, `speak`, `transcribe`, plus any `mcp_server/tools/*.py` drop-in (auto-bridged via `managed_tools.py`). Runs `pre_llm → handler → post_llm`.
3. **Auto-load** (`auto_load_tools: true`) — first blind call to a deferred tool injects its schema and asks the model to retry; the second call passes through to CC for execution.
4. **Unloaded-tool guard** (`auto_load_tools: false`) — if the model calls a deferred tool directly by name, blocks it and instructs `ToolSearch(select:<tool>)` first.
5. **Hallucination guard** (always on) — any tool_use name outside the known set (core visible ∪ deferred ∪ managed ∪ ToolSearch) triggers BM25 over core+deferred with the bogus name as query; top 5 matches are returned as suggestions in the tool_result (no schemas injected — that would bloat context). Model picks the right name, retries, and auto-load handles the single matched schema.

**Streaming behaviour** — the intercept loop branches on the first `content_block_start`. Intercepted tool names → buffer + handle + retry. Anything else (text, non-intercepted tool_use) → flush live to the client with zero added latency. Large final tool_use payloads (e.g. `execute_office_js` story drafts) stream through as they arrive — no end-of-response timeout.

The FIRST `message_start` event is forwarded to the client immediately (even on an intercept round) so the client's SSE parser commits and renders subsequent status blocks live. Subsequent rounds' `message_start` / `message_delta` / `message_stop` on intercept rounds are dropped so there's still exactly one message envelope per request.

A heartbeat task runs for the entire request (both buffer + passthrough, **across round-trips and during local handler execution**): `: keepalive` SSE comments every 2s for wire-level liveness, plus `event: ping` every `proxy.ping_interval` seconds (default `10`) as Anthropic's official live-progress signal. CC / pivot / Office add-ins recognize the pings and won't time out even on minute-long generations or slow `code_execution` calls.

**Visibility status blocks** — every intercept produces a CC-style `● Tool("arg")\n└  summary` line. `_emit_live_status()` writes the block to the wire **immediately after each handler returns** (under a shared write lock, with the payload writer drained) — so the user sees each tool call the moment it happens, not bundled with the final model reply. A `status_emitted` counter shifts upstream's block indices so everything stays self-consistent on the wire. Examples:

```
● ToolSearch("notebook jupyter")
└  3 schemas loaded: NotebookEdit, ReadNotebook, ConvertNotebook

● WebSearch("dark fantasy 1692")
└  5 results from brave.com

● Loaded mcp__chrome__take_snapshot
└  Schema delivered · awaiting retry

● Blocked: NotebookEdit (unloaded)
└  Model instructed to ToolSearch first

● Unknown tool: read_notebok
└  Suggested: NotebookEdit, ReadNotebook, ReadFile
```

Adding a new managed tool needs zero status-rendering code — `format_visibility()` derives the line from the tool's `primary_arg` and the handler's returned summary.

### `tools.<key>` — CLI backends

Each key under `tools` becomes a backend available via `/new <key>`. No code changes needed.

| Key | Description |
|---|---|
| `name` | Display name (defaults to title-cased key) |
| `icon` | Emoji icon (defaults to 🔧) |
| `startup_cmd` | Command array run inside the PTY (e.g. `["claude"]`) |
| `flags` | Extra CLI arguments appended to the command |
| `env` | Environment variables (empty values are omitted) |
| `session` | Backend-specific options (e.g. `resume_id` → `--resume`) |

Built-in keys: `claude`, `claude-local`, `codex`, `codex-local`, `shell`, `powershell`. Internal non-PTY backends: `screen` (image capture), `video` (recording), `computer` (vision-LLM control).

### `tools.computer` — vision-LLM computer control

| Key | Description |
|---|---|
| `api.base_url` | API endpoint (HTTP URL for `openai`/`anthropic`; sets `ANTHROPIC_BASE_URL` env var for `claude-code`) |
| `api.api_key` | API key (or `ANTHROPIC_AUTH_TOKEN` for `claude-code`) |
| `api.model` | Vision-capable model name |
| `api.format` | `"openai"` (default), `"anthropic"`, or `"claude-code"` |
| `capture_interval` | Seconds between captures (default `3`) |
| `max_history` | Conversation turns retained (default `20`) |
| `system_prompt` | Override the built-in prompt (empty = default) |

### `tools.computer`

| Key | Type | Description |
|-----|------|-------------|
| `api.base_url` | string | API endpoint (for `openai`/`anthropic`: HTTP URL; for `claude-code`: sets `ANTHROPIC_BASE_URL` env var, empty = use default) |
| `api.api_key` | string | API key (for `claude-code`: sets `ANTHROPIC_AUTH_TOKEN` env var) |
| `api.model` | string | Vision-capable model name (for `claude-code`: sets `ANTHROPIC_MODEL` env var) |
| `api.format` | string | `"openai"` (default), `"anthropic"`, or `"claude-code"` — selects wire format |
| `capture_interval` | number | Seconds between captures (default 3) |
| `max_history` | number | Max conversation turns to keep (default 20) |
| `system_prompt` | string | Override the default system prompt (empty = use built-in) |

---

## Architecture & flows

Three services run inside the same process, all launched by `main.py:_post_init`:

```
                              ┌──────────────────────────────┐
                              │        main.py               │
                              │   (single process, asyncio)  │
                              └──────────────────────────────┘
                                   │         │         │
                 ┌─────────────────┘         │         └──────────────────┐
                 ▼                           ▼                            ▼
        ┌──────────────┐          ┌──────────────────┐         ┌──────────────────┐
        │ Telegram bot │          │   Proxy (1235)   │         │  MCP server (1236)│
        │              │          │   aiohttp        │         │  FastMCP ASGI     │
        │ handlers.py  │          │   server.py      │         │  app.py           │
        └──────────────┘          └──────────────────┘         └──────────────────┘
                 │                           │                            │
                 ▼                           ▼                            ▼
        ┌──────────────┐          ┌──────────────────┐         ┌──────────────────┐
        │  sessions/   │          │  Local backend   │         │  External MCP    │
        │  PTY, screen │          │  (LM Studio etc.)│         │  clients         │
        │  video, LLM  │          └──────────────────┘         └──────────────────┘
        └──────────────┘
```

### 1. Telegram bot flow

```
User sends message in topic thread
    │
    ▼
bot/handlers.py:handle_text / handle_voice / handle_document
    │
    ▼
sessions/manager.py:SessionManager.get_session_by_thread
    │
    ▼              (by backend type)
    ├──► sessions/process.py    → PTY + pyte screen-diff → _LiveMessage.append → editMessageText
    ├──► sessions/screen.py     → PrintWindow / mss    → _FrameSender → send_photo / send_video
    └──► sessions/computer.py   → capture + vision LLM → pyautogui → edit_message_media

Backends built from settings.json/tools.<key> by backends/registry.py
Topic ↔ session map persisted in store.py (JSON)
```

### 2. Proxy flow

```
Claude Code / Office add-in / any client
    │ POST /v1/messages  (Anthropic format)
    ▼
proxy/server.py:handle_messages
    │
    ├─► _match_profile(headers)                         ← first profile match wins
    ├─► model_mapping (rewrite `model`)
    ├─► profile-driven tool filter                       ← strip_tool_names/types, cache_control
    ├─► inject system_instruction (proxy/instructions/)  ← office.md | system.md
    ├─► inject date + location system-reminder           ← optional
    ├─► split_tools(core, strip, inject_managed)         ← tool_registry.py
    │                                                      injects ToolSearch + managed schemas
    ▼
_handle_streaming (intercept loop, up to proxy.max_roundtrips rounds)
    │  · prepares resp + starts heartbeat (: keepalive 2s, event: ping 10s)
    │  · write_lock shared across the whole request
    │
    ├─► _forward_stream → upstream POST /v1/messages (to LM Studio)
    │     │  branches on FIRST content_block_start:
    │     │
    │     ├─► tool name in intercept set  →  buffer only the tool_use input,
    │     │     return it (nothing written to client yet)
    │     │
    │     └─► text / non-intercepted tool →  flush buffered + stream rest LIVE
    │           to client, indices shifted past any emitted status blocks
    │
    ├─► intercept handler runs:
    │     ├─► ToolSearch       → BM25 search over deferred tools (tool_search.py)
    │     ├─► WebSearch        → Brave scraper (web_search.py)
    │     ├─► code_execution   → Python sandbox (managed_tools.py)
    │     ├─► speak/transcribe → MCP-style handlers (managed_tools.py)
    │     ├─► auto_load_tools  → inject schema, request retry
    │     └─► unloaded guard   → block, instruct ToolSearch first
    │
    ├─► _emit_live_status(status_line) → writes `● Tool(arg)` + `└ summary`
    │     synthetic text block to the wire NOW (user sees it immediately),
    │     increments status_emitted counter
    │
    ├─► append [tool_use, tool_result] to body.messages; loop
    │
    └─► final upstream response → _forward_stream streams live to client,
          upstream indices shifted by status_emitted

GET /v1/models → converts OpenAI list → Anthropic list; model_mapping aliases listed first
Everything else → passthrough to upstream
```

### 3. MCP server flow

```
External MCP client (e.g. real CC via `claude mcp add telecode ...`)
    │ Streamable HTTP POST /mcp
    ▼
mcp_server/app.py (FastMCP + Starlette + CORS middleware)
    │
    ▼
mcp_server/tools/*.py   (auto-discovered via pkgutil)
    ├── tts.py         → Kokoro TTS (speak)
    ├── stt.py         → Whisper STT (transcribe)
    └── web_search.py  → Brave scraper (same backend as proxy's WebSearch)

mcp_server/resources/  ← drop-in auto-discover (empty by default)
mcp_server/prompts/    ← drop-in auto-discover (empty by default)
```

### How the three connect

- **Proxy and MCP share tool implementations** (Brave scraper, TTS, STT) but expose them via different protocols. Local models route through the proxy; external MCP clients connect to the MCP server.
- **Bot sessions can use the proxy as their backend** via `tools.claude-local.env.ANTHROPIC_BASE_URL=http://localhost:1235`.
- **Tailscale Funnel** (optional, auto-started by `main.py`) exposes both proxy and MCP over HTTPS — lets browser-sandboxed clients (Office add-ins) reach the proxy.

### File map

```
main.py                Entry point — launches bot + proxy + MCP + Tailscale Funnel
config.py              Settings accessors (hot-reloadable)
store.py               JSON persistence for topic↔session map
settings.json          Your config (gitignored)
settings.example.json  Full settings template

backends/              CLI backend system (data-driven from settings.json/tools)
  base.py              CLIBackend abstract base
  registry.py          Auto-builds GenericCLIBackend for each tools.<key>
  params.py            Per-tool param accessors
  implementations.py   Screen / Video / Computer (non-PTY) + GenericCLIBackend

sessions/              Session lifecycle
  manager.py           Start/kill sessions, route messages by thread_id
  process.py           PTY + pyte screen-diff (Unix openpty / Windows pywinpty)
  screen.py            Image capture (PrintWindow/mss) + video (ffmpeg)
  computer.py          Vision-LLM computer control (pyautogui)

bot/                   Telegram layer
  handlers.py          Commands, callbacks, _LiveMessage, _FrameSender
  topic_manager.py     Create/reuse forum topics
  settings_handler.py  /settings command parser
  rate.py              Stale session cleanup + topic probing

proxy/                 Anthropic-API-compatible middleware (port 1235)
  __main__.py          Standalone entry: python -m proxy
  server.py            aiohttp request handler + intercept loop
  config.py            Profile + global proxy settings accessors
  tool_registry.py     split_tools + proxy_system_instruction loader
  tool_search.py       BM25 + regex search engine
  managed_tools.py     Managed-tool registry + MCP→proxy auto-bridge
  web_search.py        Brave Search scraper
  llm.py               structured_call(prompt, schema) utility
  instructions/        Profile system prompts
    system.md          Default Claude Code path
    office.md          Office add-in profile

mcp_server/            Streamable-HTTP MCP server (port 1236)
  __main__.py          Standalone entry: python -m mcp_server
  app.py               FastMCP instance + CORS wrapper
  server.py            Background thread launcher
  tools/               Drop-in tool modules (auto-discovered)
    tts.py             speak — Kokoro TTS
    stt.py             transcribe — Whisper STT
    web_search.py      web_search — Brave scraper
    code_execution.py  code_execution — sandboxed Python subprocess
  resources/           Drop-in resource modules (auto-discovered)
  prompts/             Drop-in prompt modules (auto-discovered)

voice/                 STT availability + transcription
  health.py            STT availability probe
  stt.py               Speech-to-text transcription client
```

---

## Troubleshooting

**Bot doesn't respond** -- Check bot_token, ensure bot is admin with Manage Topics.

**CLI exits immediately** -- Missing API key or CLI not installed.

**No output** -- Interactive prompt waiting. Send `/key enter` or `/key y`.

**Screen capture blank/black** -- Window may be on another virtual desktop. Minimized windows are auto-restored.

**Voice not working** -- Start your STT service; the health probe detects it within 60s and voice messages are transcribed automatically.

**Video encoding fails** -- Ensure ffmpeg is installed and on PATH.
