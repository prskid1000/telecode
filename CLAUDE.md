# CLAUDE.md — Telecode developer guide

Telecode is a **Telegram bot** that runs **CLI tools** (Claude Code, Codex, shell) inside a **pseudo-terminal**, reads their screen with **pyte**, and posts text to **forum topic** threads. It does **not** call any AI API itself.

---

## Setup

**Requirements**

- Python **3.11+**
- **Node.js** if you use Claude Code or Codex CLIs
- Telegram **group with Topics** enabled; bot must be admin (Manage Topics, send/edit messages)

**Install**

```bash
cd telecode
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS
pip install -r requirements.txt
```

**Configure** — edit `settings.json`:

- `telegram.bot_token` — from @BotFather
- `telegram.group_id` — supergroup id (usually starts with `-100`)
- `telegram.allowed_user_ids` — list of numeric user ids (empty = anyone can use the bot; avoid in production)
- `paths.sessions_dir`, `paths.store_path`, `paths.logs_dir` — where sessions and JSON store live
- `tools.<name>` — `startup_cmd`, `flags`, `env` per CLI

**Run**

```bash
python main.py
```

Logs: `paths.logs_dir` (default `./data/logs/telecode.log`). Optional env: `TELECODE_SETTINGS` = path to an alternate `settings.json`.

**First use**

In the group: `/start` or `/new claude work`. Reply inside the **topic** thread the bot creates.

---

## User-facing commands (bot)

| Command | Purpose |
|--------|---------|
| `/start` | Pick a backend; starts a session |
| `/new <backend> [name]` | e.g. `/new claude work`, `/new shell logs` |
| `/stop` | Stop all sessions for the user |
| `/stop <session_key>` | e.g. `/stop claude:work` |
| `/key …` | Send keys to the PTY (see below) |
| `/voice` | STT on/off and health |
| `/settings …` | Edit config (see below) |
| `/help` | Short command list |

**`/key` examples:** `/key enter`, `/key esc`, `/key ctrl c`, `/key up`, `/key y`

**`/settings` (summary)**

- `/settings` — summary
- `/settings reload` — reload `settings.json` from disk
- `/settings validate` — sanity checks
- `/settings get <dot.path>` / `/settings set <dot.path> <value>`
- `/settings tool` / `/settings tool <key>` — tool config
- `/settings tool <key> cmd …`, `flag add|remove …`, `env VAR value`, `env VAR --delete`
- `/settings voice stt on|off|url …|model …`

---

## Architecture (short)

```
User message (topic thread)
    → handlers.handle_text / voice / document
    → SessionManager.get_session_by_thread → SessionManager.send()
    → PTYProcess.send()  (adds \r)
    → pyte parses output → snapshot → diff vs last snapshot → subscribers
    → _send_output → _LiveMessage.append → editMessageText (<pre>, HTML)
```

- **Session key:** `{backend}:{name}` — colon is the separator; do not use colons in names.
- **Routing:** only `message_thread_id` → session. No other routing.
- **Persistence:** `store.py` JSON file — topic id per `(user_id, session_key)`; voice prefs.

---

## Key files

| Path | Role |
|------|------|
| `settings.json` | Only config source |
| `config.py` | Read/write accessors (must be **functions** for hot-reload) |
| `main.py` | App startup, handlers, `set_my_commands`, voice probe loop |
| `store.py` | Topics + voice prefs JSON |
| `sessions/process.py` | PTY + pyte + snapshot diff + timers |
| `sessions/manager.py` | Start/kill sessions, send, send_raw, interrupt |
| `bot/handlers.py` | Commands, callbacks, `_LiveMessage`, `/key` |
| `bot/topic_manager.py` | Create/reuse forum topics |
| `bot/settings_handler.py` | `/settings` parsing |
| `backends/implementations.py` | Claude, Codex, shell |
| `backends/registry.py` | `get_backend`, `all_backends` |
| `backends/params.py` | Load tool params from settings |
| `voice/*` | STT health, prefs, transcribe |

---

## Rules (do not break)

1. **Config** — only `settings.json`; no scattered env vars except `TELECODE_SETTINGS` / what `config.py` reads from that file.
2. **`config.py`** — always `config.foo()`, never cached module-level constants for values that can change.
3. **Sessions** — key format `backend:name`; routing by `thread_id` only.
4. **Processes** — real PTY (Unix `openpty`, Windows ConPTY via pywinpty), not plain pipes.
5. **Telegram** — `ParseMode.HTML`; escape user/process text with `html.escape()` where needed.
6. **No** in-bot AI and **no** separate “memory” layer — CLIs own context.

---

## PTY output pipeline (`sessions/process.py`)

1. Raw bytes → **pyte** `HistoryScreen` + `Stream`.
2. Each snapshot = **history lines** + **display lines** (one top-to-bottom list).
3. Compare to **previous** full list: find **new** lines only (anchors + segment diff + “similar line” filter so spinners/status lines do not spam).
4. Emit chunks to subscribers on **idle** (~2s) or **max wait** (~5s); poll safety net every 5s.
5. **Input:** `send()` appends `\r` (not `\n`) so TUIs accept the line.

Tunables near top of `process.py`: idle interval, max wait, screen rows/history size.

---

## Live Telegram message (`bot/handlers.py`)

- **`_LiveMessage`:** one message per “turn”, updated by `append()`; debounced edits (~1s) to respect rate limits; overflow opens a new message.
- **Overlap trim:** new chunks may overlap the tail of what was already sent; a small overlap detector skips duplicate tail.

---

## Adding a CLI backend

1. Class in `backends/implementations.py` extending `CLIBackend` (`info`, optional `build_launch_cmd`, `startup_message`).
2. Register instance in `backends/registry.py`.
3. Add `tools.<key>` in `settings.json` (`startup_cmd`, `flags`, `env`, `session` if needed).
4. Keep icon dicts in sync if you duplicate icons in `topic_manager.py`.

Test: `/new <key> test`.

---

## Adding a Telegram command

1. `async def cmd_xxx(update, ctx)` in `bot/handlers.py`.
2. `app.add_handler(CommandHandler("xxx", cmd_xxx))` in `main.py`.
3. Add to `BOT_COMMANDS` and `cmd_help()`.

---

## Voice

- OpenAI-compatible **STT** URL in `settings.json` → `voice.health` probes `/models` on startup and every 60s.
- Voice messages in a topic → transcribe → same path as text to the session.

---

## Common problems

| Symptom | What to check |
|--------|----------------|
| Bot silent | Token, group id, bot admin, Topics on |
| “No session for thread” | `/new` again; store may be missing mapping |
| CLI exits at once | Missing API key, wrong `startup_cmd`, binary not on PATH |
| Stuck on prompt | User sends `/key enter` or `/key y` |
| Garbled / noisy stream | TUI limitation; tune diff in `process.py` or use a non-interactive CLI mode if the tool supports it |
| `settings` change ignored | `/settings reload` or restart |

---

## Dependencies (see `requirements.txt`)

Includes **python-telegram-bot**, **aiohttp**, **pyte**, **pywinpty** (Windows PTY). Install OS build tools if pywinpty fails to build.
