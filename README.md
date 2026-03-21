# Telecode

Telegram bot that connects AI coding CLIs (Claude Code, Codex) and screen capture to a Telegram group. Each session runs in its own forum topic.

Developer docs (architecture, internals, extending) are in [CLAUDE.md](CLAUDE.md).

## Setup

### Requirements

- Python 3.11+
- Node.js 18+ (for Claude Code, Codex)

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

### 7. First session

Send `/start` in the group, or `/new claude work`.

---

## Commands

| Command | Description |
|---|---|
| `/start` | Choose a backend to start |
| `/new <backend> [name]` | Start a named session (e.g. `/new claude work`) |
| `/stop [session_key]` | Stop one or all sessions (e.g. `/stop claude:work`) |
| `/key <key>` | Send a keyboard key to the terminal |
| `/pause` | Pause screen capture |
| `/resume` | Resume screen capture |
| `/voice` | Voice input settings |
| `/settings` | Configuration |
| `/help` | List all commands |

### Screen capture

Capture any window and stream full-resolution screenshots to a topic:

```
/new screen myapp
```

Pick a window from the list. The bot streams screenshots (~2fps). Controls:

- `/pause` / `/resume` -- or use the inline buttons on the photo
- `/stop screen:myapp` -- stop capture

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

### `tools.<key>`

Each key matches `/new <key>` (e.g. `claude`, `codex`, `shell`, `powershell`, `screen`).

| Key | Type | Description |
|-----|------|-------------|
| `startup_cmd` | array | Command to run in the PTY |
| `flags` | array | Extra CLI arguments |
| `env` | object | Environment variables (empty values are omitted) |
| `session` | object | Backend-specific options (`claude`: `resume_id`) |

---

## Project structure

```
settings.json          Configuration
main.py                Entry point
config.py              Settings accessors
store.py               JSON persistence

backends/
  base.py              CLIBackend base class
  implementations.py   Claude, Codex, Shell, PowerShell, Screen
  registry.py          Backend lookup
  params.py            Load params from settings

sessions/
  process.py           PTY process + pyte screen diffing
  screen.py            Screen capture (mss + Pillow)
  manager.py           Session lifecycle manager

bot/
  handlers.py          Telegram handlers + LiveMessage + LivePhoto
  topic_manager.py     Forum topic creation
  settings_handler.py  /settings command

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

**Screen capture black** -- Window may be minimized or on another virtual desktop.

**Voice not working** -- Run `/voice`. Start your STT service, detected within 60s.
