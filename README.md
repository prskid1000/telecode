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
| `/pause` | Pause image/video capture |
| `/resume` | Resume image/video capture |
| `/voice` | Voice input settings |
| `/settings` | Configuration |
| `/help` | List all commands |

### Screen image capture

Capture any window and stream screenshots to a topic (interval = `capture.image_interval` seconds, default 15):

```
/new screen myapp
```

Pick a window from the list. Each frame is sent as a new photo message. Controls:

- `/pause` / `/resume` -- pause/resume streaming
- `/stop screen:myapp` -- stop capture

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
- `/pause` / `/resume` -- pause/resume
- `/stop` -- stop the session

**Requirements:** `pyautogui` (included in requirements.txt)

### Screen video capture

Record a window continuously in video chunks (length = `capture.video_interval` seconds, default 60):

```
/new video myapp
```

Pick a window from the list. The bot records at 3fps, encodes each chunk with ffmpeg (libx264, ultrafast, lightweight), and sends it as a video message. Recording continues until stopped. Controls:

- `/pause` / `/resume` -- pause/resume recording (paused time doesn't count)
- `/stop video:myapp` -- stop recording (encodes and sends any remaining frames)

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

Send a voice message in a session topic -- transcribed via STT and sent as text. Toggle with `/voice`. Requires an OpenAI-compatible STT endpoint (default `http://localhost:6600/v1`).

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
| `store_path` | JSON file for topic↔session mapping (default `./data/telecode.json`) |
| `logs_dir` | Log directory (default `./data/logs`) |

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

Users toggle per-user with `/voice`.

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
| `tool_splitting` | Split incoming tools into core (always forwarded) + deferred (searchable via ToolSearch) |
| `strip_reminders` | Drop `<system-reminder>` blocks from messages |
| `auto_load_tools` | Auto-load a deferred tool's schema the first time the model calls it blindly |
| `lift_tool_result_images` | Lift images out of array-form `tool_result` content — LM Studio workaround |
| `location` | User location for the date/location system-reminder (empty = auto-detect via IP) |
| `core_tools` | Default list of tool names that stay core when splitting (profiles can override) |
| `cors_origins` | CORS allowed origins |
| `model_mapping` | Rewrite model IDs: `{"claude-opus-4-6": "qwen3.5-35b-a3b"}` — applied to `/v1/messages` and `/v1/models` |
| `client_profiles` | Per-client behavior routing — see below |
| `web_search.enabled` | Enable the Brave Search scraper as a managed tool |

Point `ANTHROPIC_BASE_URL=http://localhost:1235` at the proxy. Also runs standalone: `python -m proxy`.

**Managed tools** (`proxy/managed_tools.py`): schemas injected into the model's tool list; intercepted on `tool_use`, executed locally, looped up to 15 rounds per turn. Currently registered: `WebSearch` (Brave scraper), `speak` (Kokoro TTS), `transcribe` (Whisper STT). Optional `pre_llm`/`post_llm` `LLMHook`s for arg enrichment / result post-processing via `proxy/llm.py`.

#### Client profiles (`proxy.client_profiles`)

Match requests by header substring and apply per-client transforms. First match wins; no match = global `proxy.*` defaults. System instruction markdown files live under `proxy/instructions/`.

```json
{
  "name": "office",
  "match": {"header": "Referer", "contains": "pivot.claude.ai"},
  "system_instruction": "office.md",
  "tool_splitting": false,
  "intercept": false,
  "inject_date_location": false,
  "strip_tool_types": ["web_search_*", "code_execution_*"],
  "strip_cache_control": true
}
```

| Key | Description |
|---|---|
| `name` | Label for logging |
| `match.header` | Header name to check (case-insensitive) |
| `match.contains` | Substring that must appear in the header value |
| `system_instruction` | Markdown file in `proxy/instructions/` — prepended to the client's system prompt |
| `tool_splitting` | Override global tool splitting for this client |
| `intercept` | Intercept tool calls locally (ToolSearch, managed tools) |
| `inject_date_location` | Add the date/location `<system-reminder>` |
| `core_tools` | Tool names that stay core when splitting. Falls back to `proxy.core_tools` |
| `inject_managed` | List of managed tools to inject (e.g. `["WebSearch", "speak"]`). Strips CC's same-name versions and replaces them. Default = all registered |
| `strip_tool_names` | Drop tools by exact name (e.g. `["WebSearch"]`) |
| `strip_tool_types` | Drop tools by `type` field. Supports `"prefix_*"` wildcards |
| `drop_non_custom_tools` | Drop any tool with `type != "custom"` (strips Anthropic server-side tools) |
| `strip_cache_control` | Remove `cache_control` keys (LM Studio rejects unknown fields) |

The **office** profile unlocks Claude for Excel/PowerPoint/Word against a local model — Office add-ins silently retry unless every turn returns a `tool_use` block, so this profile preserves their tools, strips Anthropic-hosted ones (`web_search_20250305`, `code_execution_20250825`), and swaps in an Office-aware system prompt.

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

## Project structure

```
settings.json          Configuration
main.py                Entry point
config.py              Settings accessors
store.py               JSON persistence

backends/
  base.py              CLIBackend base class
  implementations.py   GenericCLIBackend (data-driven) + Screen, Video, Computer
  registry.py          Auto-built from settings.json tools
  params.py            Load params from settings

sessions/
  process.py           PTY process + pyte screen diffing
  screen.py            Screen image capture (PrintWindow/mss) + video recording (ffmpeg)
  computer.py          Vision LLM computer control (capture + actions)
  manager.py           Session lifecycle manager

bot/
  handlers.py          Telegram handlers + LiveMessage + LivePhoto
  topic_manager.py     Forum topic creation
  settings_handler.py  /settings command

proxy/
  __main__.py          Standalone entry: python -m proxy
  server.py            aiohttp streaming proxy with ToolSearch interception
  tool_search.py       BM25 + regex search engine
  tool_registry.py     Core/deferred tool splitting
  managed_tools.py     Managed tool registry + LLM hooks
  llm.py               Upstream LLM structured_call utility
  web_search.py        Brave Search scraper + result formatter
  config.py            Proxy settings

mcp_server/
  app.py               FastMCP instance (stateless streamable HTTP)
  server.py            Background startup for telecode integration
  __main__.py           Standalone entry: python -m mcp_server
  tools/
    __init__.py        Auto-discovers tool modules (drop-in)
    tts.py             speak tool (Kokoro TTS)
    stt.py             transcribe tool (Whisper STT)
    web_search.py      web_search tool (Brave scraper)
  resources/
    __init__.py        Auto-discovers resource modules (drop-in)
  prompts/
    __init__.py        Auto-discovers prompt modules (drop-in)

voice/
  health.py            STT availability probe
  prefs.py             Per-user STT toggle
  stt.py               Speech-to-text
```

---

## Troubleshooting

**Bot doesn't respond** -- Check bot_token, ensure bot is admin with Manage Topics.

**CLI exits immediately** -- Missing API key or CLI not installed.

**No output** -- Interactive prompt waiting. Send `/key enter` or `/key y`.

**Screen capture blank/black** -- Window may be on another virtual desktop. Minimized windows are auto-restored.

**Voice not working** -- Run `/voice`. Start your STT service, detected within 60s.

**Video encoding fails** -- Ensure ffmpeg is installed and on PATH.
