# Telecode

Telegram bot that connects AI coding CLIs (Claude Code, Codex) to a Telegram group. Each session runs in its own forum topic. You type in Telegram, the CLI processes it, output streams back.

## Setup

### Requirements

- Python 3.11+
- Node.js 18+ (for Claude Code, Codex)

### 1. Install CLI tools

Install whichever ones you want to use:

```bash
npm install -g @anthropic-ai/claude-code
npm install -g @openai/codex
```

### 2. Create a Telegram bot

1. Message @BotFather on Telegram
2. Send `/newbot`, follow the prompts
3. Copy the bot token

### 3. Create a Telegram group with Topics

1. Create a new group, add your bot
2. Enable Topics in group settings
3. Make the bot admin with: Manage Topics, Send Messages, Edit Messages

Get the group ID (starts with `-100`):
- Add @userinfobot to the group, it posts the chat ID. Remove it after.

Get your user ID:
- DM @userinfobot, it replies with your numeric ID.

### 4. Install Telecode

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
  },
  "paths": {
    "sessions_dir": "./data/sessions",
    "store_path": "./data/telecode.json",
    "logs_dir": "./data/logs"
  }
}
```

PTY processes always start in the OS home directory.

API keys can be set later from Telegram:

```
/settings tool claude env ANTHROPIC_API_KEY sk-ant-...
/settings tool codex  env OPENAI_API_KEY   sk-...
```

Or edit `settings.json` directly under each tool's `env` block.

Every key is described in **[Settings reference](#settings-reference)** below.

### 6. Run

```bash
python main.py
```

### 7. First session

Send `/start` in the Telegram group. Pick an AI. A topic thread is created. Type your message in that thread.

Or from text:

```
/new claude work
```

---

## Commands

| Command | Description |
|---|---|
| `/start` | Choose an AI to start |
| `/new <ai> [name]` | Start a named session (e.g. `/new claude work`) |
| `/stop [name]` | Stop one session or all (e.g. `/stop claude:work`) |
| `/key <key>` | Send a keyboard key to the terminal |
| `/voice` | Voice input settings (STT toggle) |
| `/settings` | Configuration |
| `/help` | List all commands |

### Multiple sessions

Each session gets its own forum topic. Run multiple at once:

```
/new claude work
/new claude research
/new shell logs
```

Switch between them by typing in the corresponding topic thread.

### Terminal keys (`/key`)

Send any keyboard key, modifier, or combination to the running CLI process.

**Basic keys:**

| Command | Key |
|---|---|
| `/key enter` | Enter / Return |
| `/key esc` | Escape |
| `/key tab` | Tab |
| `/key backspace` | Backspace |
| `/key space` | Space |
| `/key delete` | Delete |
| `/key insert` | Insert |

**Arrow & navigation:**

| Command | Key |
|---|---|
| `/key up` / `down` / `left` / `right` | Arrow keys |
| `/key home` / `end` | Home / End |
| `/key pgup` / `pgdn` | Page Up / Page Down |

**Function keys:**

```
/key f1  ..  /key f12
```

**Modifier combinations:**

| Command | Result |
|---|---|
| `/key ctrl c` | Ctrl+C (interrupt) |
| `/key ctrl d` | Ctrl+D (EOF) |
| `/key alt x` | Alt+X |
| `/key ctrl shift a` | Ctrl+Shift+A |
| `/key alt f4` | Alt+F4 |

**Single characters:**

```
/key a    /key 1    /key y    /key n
```

This is how you respond to interactive CLI prompts (e.g. "Trust this folder?" → `/key y` then `/key enter`).

### Settings (`/settings`)

| Command | Description |
|---|---|
| `/settings` | Show full config summary |
| `/settings reload` | Hot-reload settings.json from disk |
| `/settings validate` | Check for missing/placeholder values |
| `/settings get <path>` | Read a config value (e.g. `voice.stt.enabled`) |
| `/settings set <path> <value>` | Set a value and save |

**Tool configuration:**

| Command | Description |
|---|---|
| `/settings tool` | List all tools and their config |
| `/settings tool <key>` | Show one tool's config |
| `/settings tool <key> cmd <cmd...>` | Set the startup command |
| `/settings tool <key> flag add <flag>` | Add a CLI flag |
| `/settings tool <key> flag remove <flag>` | Remove a CLI flag |
| `/settings tool <key> env <VAR> <value>` | Set an environment variable |
| `/settings tool <key> env <VAR> --delete` | Remove an environment variable |

**Voice (STT) configuration:**

| Command | Description |
|---|---|
| `/settings voice stt` | Show STT config |
| `/settings voice stt on` | Enable speech-to-text |
| `/settings voice stt off` | Disable speech-to-text |
| `/settings voice stt url <url>` | Set STT endpoint URL |
| `/settings voice stt model <model>` | Set STT model name |

### Voice

Send a voice message in a session topic — it gets transcribed (STT) and sent as text input to the CLI.

Toggle with `/voice`. Requires an OpenAI-compatible STT service running locally.

---

## Voice setup (optional)

Telecode can transcribe voice messages using any OpenAI-compatible STT endpoint. Install and run your STT service separately (e.g. [voicemode-windows](https://github.com/prskid1000/voicemode-windows)). Telecode auto-detects it within 60 seconds.

Default endpoint:

```
STT: http://localhost:6600/v1
```

Configure via `/settings voice stt url <url>` or edit `settings.json`.

---

## Settings reference

All options live in `settings.json`. To use a different file, set the environment variable **`TELECODE_SETTINGS`** to its full path before starting the bot.

Changes from Telegram use `/settings set …` or tool/voice subcommands; they write the same JSON. After hand-editing the file, run **`/settings reload`** or restart the bot.

### `telegram`

| Key | Type | Description |
|-----|------|-------------|
| `bot_token` | string | Token from @BotFather. |
| `group_id` | number | Forum supergroup id (typically starts with `-100`). |
| `allowed_user_ids` | array of numbers | Telegram user ids allowed to use the bot. If **empty**, any group member can use it (avoid in production). |

### `paths`

| Key | Type | Description |
|-----|------|-------------|
| `sessions_dir` | string | Base directory for session data. |
| `store_path` | string | JSON file for topic-session mapping and voice prefs. |
| `logs_dir` | string | Directory for `telecode.log`. If omitted, defaults to `./data/logs`. |

Relative paths are resolved from the process current working directory (usually the folder where you run `python main.py`).

### `streaming`

| Key | Type | Description |
|-----|------|-------------|
| `interval_sec` | number | How often the live Telegram message is debounced/updated while output streams (seconds). |
| `max_message_length` | number | Soft limit for a single Telegram message body before the bot continues in a new message (characters). |
| `idle_timeout_sec` | number | If the user sends nothing for this many seconds, the session is stopped automatically. Set to **`0`** to disable. |

### `voice.stt`

OpenAI-compatible speech-to-text (`POST …/audio/transcriptions`).

| Key | Type | Description |
|-----|------|-------------|
| `enabled` | boolean | When true, the bot probes the STT URL and accepts voice messages in topics. |
| `base_url` | string | API base (e.g. `http://localhost:6600/v1`) — no trailing slash required. |
| `model` | string | Model name sent in the multipart form (e.g. `whisper-1`). |

### `tools` and `tools.<key>`

Each `<key>` matches `/new <key> …` (e.g. `claude`, `codex`, `shell`, `powershell`). You can add more keys after registering a backend in code.

| Key | Type | Description |
|-----|------|-------------|
| `startup_cmd` | array of strings | Command invoked in the PTY (program first, then fixed arguments). Must be non-empty for that tool to start. |
| `flags` | array of strings | Extra CLI arguments appended after `startup_cmd` (global defaults for that tool). |
| `env` | object (string → string) | Environment variables merged into the PTY process. Entries with **empty string values are omitted** and are not passed through. |
| `session` | object (string → string) | Backend-specific options. **`claude` only:** if `resume_id` is non-empty, the launch command includes `--resume <id>`. Other backends ignore unknown keys. |

### Dot paths for `/settings get` and `/settings set`

Use the same nesting as JSON, with dots:

- `telegram.bot_token`, `telegram.group_id`, `telegram.allowed_user_ids`
- `paths.sessions_dir`, `paths.store_path`, `paths.logs_dir`
- `streaming.interval_sec`, `streaming.max_message_length`, `streaming.idle_timeout_sec`
- `voice.stt.enabled`, `voice.stt.base_url`, `voice.stt.model`
- `tools.claude.startup_cmd`, `tools.claude.flags`, `tools.claude.env.ANTHROPIC_API_KEY`, `tools.claude.session.resume_id`, etc.

Arrays and objects: `/settings set` expects JSON-shaped values where applicable (the bot parses values; complex edits are often easier in the file directly).

---

## Adding a new CLI tool

1. Add a class in `backends/implementations.py`
2. Register it in `backends/registry.py`
3. Add config block in `settings.json` under `tools`

See existing backends for the pattern. Test with `/new <key> test`.

---

## Git

The repo includes a `.gitignore` that skips Python caches (`__pycache__/`, `*.pyc`) and the **`data/`** directory (sessions, `telecode.json` store, logs). Do not commit tokens, API keys, or local session data.

---

## Project structure

```
settings.json          All configuration
main.py                Entry point
config.py              Typed accessors for settings.json
store.py               JSON persistence (topics, voice prefs)

backends/
  base.py              CLIBackend base class
  implementations.py   Claude, Codex, Shell backends
  registry.py          Backend lookup
  params.py            Load params from settings.json

sessions/
  process.py           PTY process + pyte screen diffing
  manager.py           Session lifecycle manager

bot/
  handlers.py          Telegram command and message handlers
  topic_manager.py     Forum topic creation
  settings_handler.py  /settings command

voice/
  health.py            STT availability probe
  prefs.py             Per-user STT toggle
  stt.py               Speech-to-text
```

---

## Troubleshooting

**Bot doesn't respond** — Check bot_token in settings.json. Ensure bot is admin with Manage Topics.

**CLI exits immediately** — Missing API key or CLI not installed. Use `/settings tool <key> env` to set keys.

**No output in Telegram** — The CLI may be showing an interactive prompt. Send `/key enter` or `/key y` to proceed.

**Voice not working** — Run `/voice` to check status. Start your STT service, bot detects it within 60s.
