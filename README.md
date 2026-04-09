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

Edit `settings.json`:

```json
{
  "telegram": {
    "bot_token": "your-token-here",
    "group_id": -100your-group-id,
    "allowed_user_ids": [your-user-id]
  }
}
```

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

All options in `settings.json`. Use `TELECODE_SETTINGS` env var to point to a different file.

### `telegram`

| Key | Type | Description |
|-----|------|-------------|
| `bot_token` | string | Token from @BotFather |
| `group_id` | number | Forum supergroup id (starts with `-100`) |
| `allowed_user_ids` | array | User ids allowed to use the bot. Empty = open to all |

### `paths`

| Key | Type | Description |
|-----|------|-------------|
| `sessions_dir` | string | Base directory for session data |
| `store_path` | string | JSON file for topic-session mapping |
| `logs_dir` | string | Log directory (default `./data/logs`) |

PTY processes always start in the OS home directory.

### `streaming`

| Key | Type | Description |
|-----|------|-------------|
| `interval_sec` | number | Live message update interval (seconds) |
| `max_message_length` | number | Max chars before splitting to a new message |
| `idle_timeout_sec` | number | Auto-stop after this many idle seconds (0 = off) |

### `voice.stt`

| Key | Type | Description |
|-----|------|-------------|
| `enabled` | boolean | Enable STT and voice message transcription |
| `base_url` | string | OpenAI-compatible STT endpoint |
| `model` | string | Model name (e.g. `whisper-1`) |

### `capture`

| Key | Type | Description |
|-----|------|-------------|
| `image_interval` | number | Seconds between image capture sends (default 15) |
| `video_interval` | number | Seconds per video chunk / recording length per segment (default 60) |

### `mcp_server`

Streamable HTTP MCP server exposing local TTS/STT as tools for Claude Code or any MCP client. Starts automatically with Telecode in a background thread.

| Key | Type | Description |
|-----|------|-------------|
| `enabled` | boolean | Enable the MCP server (default `true`) |
| `host` | string | Listen address (default `127.0.0.1`) |
| `port` | number | Listen port (default `1236`) |
| `tts_url` | string | Kokoro TTS base URL (default `http://127.0.0.1:6500`) |
| `stt_url` | string | Whisper STT base URL (default `http://127.0.0.1:6600`) |

**Tools provided:**
- `speak(text, voice?, output_path?)` — generate speech via Kokoro TTS, returns audio file path
- `transcribe(audio_path, language?)` — transcribe audio via Whisper STT, returns text. Accepts local paths or remote URLs (http/https)

**Add to Claude Code:**
```bash
claude mcp add telecode-audio --transport streamable-http --url http://127.0.0.1:1236/mcp
```

**Run standalone** (without Telegram bot):
```bash
python -m mcp_server
```

**Adding new tools/resources/prompts:** drop a `.py` file in the appropriate folder — auto-discovered at startup:
- `mcp_server/tools/` — `@mcp_app.tool()` decorators
- `mcp_server/resources/` — `@mcp_app.resource()` decorators
- `mcp_server/prompts/` — `@mcp_app.prompt()` decorators

### `proxy`

Tool-search proxy that sits between Claude Code and LM Studio. Reduces 101 tools to ~9 core tools (matching Opus's core set), injects a `ToolSearch` meta-tool for on-demand schema loading, and appends a dynamic tool catalog to the system prompt. Strips duplicate deferred-tool reminders from messages.

| Key | Type | Description |
|-----|------|-------------|
| `enabled` | boolean | Enable the proxy (default `false`) |
| `port` | number | Proxy listen port (default `1235`) |
| `upstream_url` | string | LM Studio or other backend URL (default `http://localhost:1234`) |
| `core_tools` | array | Override which tools stay loaded (default: Bash, Edit, Read, Write, Glob, Grep, Agent, Skill) |

When enabled, the proxy starts automatically with Telecode. Point your `claude-local` tool's `ANTHROPIC_BASE_URL` at `http://localhost:<port>` to route through it. Also works standalone with Claude Code by setting `ANTHROPIC_BASE_URL=http://localhost:1235`.

**Run standalone** (without Telegram bot):
```bash
python -m proxy
```

### `tools.<key>`

Each key under `tools` becomes a backend available via `/new <key>`. Add any tool — no code changes needed.

| Key | Type | Description |
|-----|------|-------------|
| `name` | string | Display name (optional — defaults to title-cased key) |
| `icon` | string | Emoji icon (optional — defaults to 🔧) |
| `startup_cmd` | array | Command to run in the PTY |
| `flags` | array | Extra CLI arguments |
| `env` | object | Environment variables (empty values are omitted) |
| `session` | object | Backend-specific options (e.g. `resume_id` → `--resume`) |

Built-in backends: `claude`, `claude-local`, `codex`, `codex-local`, `shell`, `powershell`.
Screen image capture (`screen`), video recording (`video`), and computer control (`computer`) are internal non-PTY backends.

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
  config.py            Proxy settings

mcp_server/
  app.py               FastMCP instance (stateless streamable HTTP)
  server.py            Background startup for telecode integration
  __main__.py           Standalone entry: python -m mcp_server
  tools/
    __init__.py        Auto-discovers tool modules (drop-in)
    tts.py             speak tool (Kokoro TTS)
    stt.py             transcribe tool (Whisper STT)
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
