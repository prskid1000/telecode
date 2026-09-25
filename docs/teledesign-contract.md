# TeleDesign — build contract

Shared interface for everyone building TeleDesign in parallel. **If you need to change anything in this
file, say so in your report instead of silently diverging.** Architecture: [teledesign.md](teledesign.md).
Scope checklist: [teledesign-parity.md](teledesign-parity.md). Prompts: `services/design/prompts/README.md`.

Repo rules that apply (from CLAUDE.md): only `settings.json` for config, read through
`config.get_nested("design.x", default)` every time (hot-reload, never cache); Telegram text is
`ParseMode.HTML` + `html.escape()`; the REST surface has no auth, so every caller-supplied id/path is
validated and every caller-supplied URL goes through `proxy/media_fetch.py`; subprocesses via
`subprocess.Popen(..., creationflags=CREATE_NO_WINDOW)` like the task handlers.

Python: `C:\Users\prith\.telecode\telecode-venv\Scripts\python.exe` (Bash tool has no `python` with
aiohttp; use PowerShell for the venv). Node 24 is on PATH in PowerShell (nvm). Edge:
`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`. ffmpeg on PATH. Test with a scratch
settings dir: `$env:TELECODE_SETTINGS = "<scratch>\settings.json"` containing `{}` — never touch the real
`data/`.

---

## 1. File ownership (do not edit files you don't own)

| Owner | Files |
|---|---|
| **W1 backend core** | `services/design/{store.py, chats.py, generate.py, prompt_builder.py, versions.py, comments.py, files.py, assets.py, events.py, preview.py, templates.py, share.py}`, `proxy/api_design.py` |
| **W2 frontend** | `proxy/static/teledesign.html`, `proxy/static/design/app/**` (JS/CSS modules served at `/design/app/*`) |
| **W3 preview runtime + starters** | `services/design/runtime/**` (`td-bridge.js`, `td-babel-source.js`, `td-telecode.js`), `services/design/starters/**` |
| **W4 render + export** | `services/design/{render.py, export.py, handoff.py, pptx_export.py, video_export.py}`, `proxy/api_design_export.py` |
| **W5 design systems** | `services/design/{systems.py, lint.py, ds_bundle.py, brand_extract.py, github_import.py}`, `proxy/api_design_systems.py` |
| **W6 canvas editor** | `patches/open-pencil/**`, `tools/build_open_pencil.py`, `proxy/static/design/editor/**` (vendored build), `services/design/editor_bridge.py`, `proxy/api_design_editor.py`, prompts `canvas.md` + `layer_boards.md` |
| **W7 agents + integrations** | `services/design/{parallel.py, mcp_registration.py}`, `mcp_server/tools/design.py`, `proxy/api_design_agents.py`, `bot/design_handlers.py` (+ the minimal wiring lines in `main.py`/`bot/handlers.py` for `/design`) |
| coordinator | `proxy/server.py`, `tray/app.py`, `docs/*`, `CLAUDE.md`, `README.md`, `requirements.txt`, `services/design/prompts/**` (except W6's two files), `services/design/seeds/**` |

Need a change in someone else's file? Put it in your report as "request for <owner>".

---

## 2. Settings (`settings.json` → `design.*`, all optional)

| Key | Default | Meaning |
|---|---|---|
| `design.preview_port` | `1237` | second aiohttp site: the preview origin (§5) |
| `design.default_engine` | `"claude_code"` | `claude_code` \| `codex` \| `antigravity` |
| `design.default_is_local` | `false` | route turns through the local proxy/llama |
| `design.verifier.enabled` / `.is_local` | `true` / `true` | post-turn verifier; local model by default |
| `design.own_domains` | `[]` | brand-impersonation exception list |
| `design.max_parallel_agents` | `6` | cap for split / side-by-side / let-it-cook |
| `design.share.enabled` | `false` | share tokens only work when true |
| `design.telegram_notify` / `design.telegram_notify_interval_sec` | `false` / `15` | post a Telegram message + thumbnail when a web-started turn finishes |
| `design.agent_turn_timeout_sec` | `1800` | per-turn cap inside parallel runs |
| `design.jury.critics_local` | `true` | critique turns on the local model |
| `design.mcp_name` | `"telecode"` | server name used by MCP registration |

---

## 3. Data on disk (per project, `data/design/projects/<pid>/`)

Already implemented by `store.py`: `<pid>.json`, `doc.fig`, `boards.json`, `imports/`, `comments.json`.
New (W1 unless noted):

```
chats/index.json          [{id, title, engine, is_local, effort, session_id, created_at, updated_at}]
chats/<chat_id>.jsonl     one JSON object per line: turn records (§4.3)
chats/<chat_id>.md        transcript, handoff format (§4.6), rewritten after every turn
assets.json               {"assets":[{id, name, group, path, board_id?, viewport:{width,height}?,
                            subtitle?, status:"needs-review"|"approved"|"changes-requested", versions:[v…]}]}
.versions/manifest.json   {"versions":[{v, at, origin:"agent"|"user"|"tweak"|"restore", turn_id?, prompt?,
                            parent, files:{rel_path: sha256}}]}
.versions/objects/<sha256>   content-addressed blobs (doc.fig included)
_ds/<slug>/               staged copy of the attached design system (W5 provides stage function)
.td/brief.md              staged charter for the CLI (never overwrite a user's root CLAUDE.md / AGENTS.md;
                          the CLI is pointed at it through the prompt — see §4.2)
uploads/  scraps/*.napkin  assets/  *.html *.jsx *.css      agent-written sources
thumbnail.webp            latest project thumbnail (W4)
```

Project record gains (W1): `active_chat_id`, `title_locked` (bool), `thumbnail` (bool).

Comment record (W1 owns storage; shape is shared):
```json
{"id":"<hex32>","board_id":"<frame id or html file>","file":"index.html",
 "anchor":{"td_id":"hero-cta","selector":"main > section:nth-of-type(1) .cta","source_loc":"index.html:42:7",
           "node_id":null,"bbox":{"x":0,"y":0,"w":0,"h":0}},
 "mentioned_element":"<mentioned-element>…</mentioned-element>",
 "author":"You","note":"make this bolder","status":"open|sent|resolved","slide_index":null,
 "created_at":"…","sent_turn_id":null}
```

---

## 4. W1 backend: REST, turns, events

All routes under `/api/design/projects/{pid}` validate `pid` with `store.valid_id`. Mutating routes
require `Content-Type: application/json` (or octet-stream / multipart where stated) **and** reject a
request whose `Origin` header is present and is not the proxy's own origin (`http://127.0.0.1:<proxy.port>`
or `http://localhost:<proxy.port>`) — a preview page on `:1237` must never be able to write.

### 4.1 Routes
| Method + path | Body / query | Returns |
|---|---|---|
| `GET /design` | — | `teledesign.html` (exists) |
| `GET /design/app/{path}` | — | static from `proxy/static/design/app/` (W1 adds the route; W2 fills the dir) |
| `GET …/chats` | — | `{"chats":[…]}` |
| `POST …/chats` | `{title?, engine?, is_local?, effort?, from_chat_id?}` | `{"chat":{…}}` — `from_chat_id` seeds a "Continuing from X" summary |
| `PATCH …/chats/{cid}` / `DELETE …/chats/{cid}` | `{title?, engine?, is_local?, effort?}` | `{"chat"}` / `{"ok"}` |
| `GET …/chats/{cid}/turns` | `?after=<turn_id>` | `{"turns":[…]}` |
| `POST …/chats/{cid}/turns` | `{text, attachments?:[rel paths in uploads/], comment_ids?:[…], form_answers?:{…}, selection?:{…}, engine?, is_local?, effort?, kind_skill?}` | `{"turn":{…}}` (status `queued`/`running`) — 409 if a turn is running in this chat (client queues) |
| `POST …/chats/{cid}/stop` | — | `{"ok"}` — cancels the running turn (terminates the CLI) |
| `GET …/events` | SSE, `?chat_id=` optional | §4.4 |
| `GET …/files` | `?prefix=` | `{"files":[{path,size,mtime,kind}]}` (excludes `.versions`, `.td`, `chats`) |
| `GET …/files/{path:.*}` | — | raw file (safe_relpath) |
| `PUT …/files/{path:.*}` | octet-stream | `{"ok","version"}` — user edit (Code view, inline edits); snapshots a version `origin:"user"` |
| `DELETE …/files/{path:.*}` | — | `{"ok"}` |
| `POST …/uploads` | multipart (≤ 64 MB each) | `{"paths":["uploads/…"]}` |
| `GET …/versions` / `GET …/versions/{v}` | — | manifest / one version with file list |
| `GET …/versions/{v}/files/{path:.*}` | — | file content at that version |
| `POST …/versions/{v}/restore` | `{paths?:[…]}` | `{"version"}` (new version, origin `restore`) |
| `GET …/versions/diff` | `?a=&b=&path=` | `{"diff":"<unified>"}` |
| `GET/POST …/comments`, `PATCH/DELETE …/comments/{id}` | comment fields | comment records |
| `POST …/comments/send` | `{ids:[…], chat_id}` | `{"turn"}` — starts a scoped-edit turn |
| `GET/PUT …/assets` | `{"assets":[…]}` | review manifest; `PATCH …/assets/{id}` `{status}` |
| `POST …/tweaks` | `{file, edits:{k:v}}` | `{"ok","version"}` — rewrites the `/*EDITMODE-BEGIN*/…/*EDITMODE-END*/` JSON in `file`, no model call |
| `POST …/edits` | `{file, source_loc?, td_id?, op:"text"|"style"|"attr", value}` | `{"ok"}` or `{"ok":false,"route":"agent"}` — deterministic write-back (§4.5) |
| `POST /api/design/templates` / `GET` / `POST /api/design/templates/{id}/instantiate` | `{project_id, name, intro_text, cover?}` | template records; instantiate → new project |
| `POST …/share` / `GET …/share` / `DELETE …/share/{token}` | `{role:"view"|"comment"|"edit"}` | share tokens (only when `design.share.enabled`) |
| `GET /design/s/{token}` | — | read-only viewer page (W2 builds the page: `proxy/static/design/app/share.html`) |

`GET /api/design/engines` → `{"engines":[{id:"claude_code",available:bool,version?},…], "local":{available:bool, model?}}` (probe `shutil.which` + proxy/llama state). Cached 30 s.

### 4.2 A turn (`generate.py`)
1. Resolve chat → session (`session_store`, namespace **`design`**, session id `"<pid>-<cid>"` truncated
   to the store's id rules). **The session folder is not the project folder**, so stage: the CLI's cwd is
   the **project folder** — `generate.py` calls the handler logic with `work_dir` = project dir. Simplest
   compliant route: register a task type `DESIGN_TURN` (W1, in `generate.py`, via
   `get_task_queue().register_handler`) whose handler resolves the project dir and invokes the engine's
   `_run_<engine>_subprocess(...)` directly with that `work_dir` and the session's resume id
   (`last_claude_session_id` / `last_codex_session_id` / `last_antigravity_conversation_id`), then patches
   the session data. Handlers already take the prompt on **stdin** (no length limit).
2. Prompt = `prompt_builder.build(project, chat, turn)` following `prompts/README.md` order, filling
   placeholders; includes: design-system block (W5 `systems.prompt_context(system_id)`), kind skill,
   craft files, `<attached-comments>` (comments.md template), `<form-answers>`, selection
   (`<mentioned-element>` blocks / node ids), attachments list, own-domain list, engine notes.
3. Snapshot touched files after the turn (hash compare vs last version) → new version `origin:"agent"`.
4. Post-turn pipeline (async, each emits events): `done` gate (W4 `render.console_errors(pid, file)` for
   every changed HTML file; if errors → one automatic fix turn with the errors, max 1) → verifier (W4
   `render` + `prompts/verifier.md`, local model when `design.verifier.is_local`; silent on pass) →
   thumbnail (W4 `render.thumbnail(pid)`) → auto-title on the first turn unless `title_locked`
   (`prompts/title.md`, local model) → parse `<question-form>` blocks from the reply into a `form` event.
5. Assets: files the agent registered in `assets.json` are kept; new HTML files are auto-registered
   (`needs-review`), re-registering resets status.

Turn record (one line in `chats/<cid>.jsonl`):
```json
{"id":"<hex32>","chat_id":"…","role":"user|assistant","text":"…","status":"queued|running|done|failed|cancelled",
 "task_id":"…","engine":"claude_code","is_local":false,"effort":null,"attachments":[],"comment_ids":[],
 "form":null,"todos":[],"tools":[{"name":"Write","input_preview":"index.html"}],
 "changed_files":["index.html"],"version":3,"usage":{"input":0,"output":0,"cache_read":0,"cost_usd":0,"duration_ms":0},
 "error":null,"created_at":"…","finished_at":"…"}
```

### 4.3 Events (SSE `…/events`)
`event: <type>\ndata: <json>\n\n`, heartbeat comment every 15 s. Types:
`turn` (full turn record on create/status change) · `delta` `{turn_id, text}` (streamed assistant text) ·
`tool` `{turn_id, name, input_preview}` · `todo` `{turn_id, todos:[{text, status}]}` (from TodoWrite) ·
`files` `{turn_id?, changed:[paths], version}` · `form` `{turn_id, form:{…question-form json…}}` ·
`check` `{turn_id, stage:"done_gate"|"verifier", status:"running"|"pass"|"issues"|"fixing"|"timeout", issues?}` ·
`title` `{title}` · `thumbnail` `{url}` · `comments` `{changed:[ids]}` · `assets` `{}` · `error` `{message}` ·
`show` `{path, target}` (agent asks the UI to open a file) · `agents` `{run_id, status}` (parallel runs, W7).
W7 helper routes: `POST …/show`, `GET/PUT …/app-state` (W2 PUTs active file/board/selection on change),
`GET /api/design/app-state`, `POST …/screenshot`, `POST …/eval`, `GET …/console`, `POST …/canvas/call`,
`GET /api/design/canvas/tools`, `GET /api/design/skills[/{name}]`, `…/agents` (§11).
Implementation: `events.py` in-process pub/sub keyed by pid; `generate.py` polls the task's
`metadata["events"]` (appended by handlers via `append_event`) every 250 ms and republishes.

### 4.4 Deterministic write-back (`POST …/edits`)
If the element has `source_loc` (stamped by W3's Babel plugin as `data-td-src="file:line:col"`) or a unique
`data-td-id` in a static HTML file: `text` replaces the element's text child in source; `style` merges into
its `style={{…}}` / `style="…"` attribute (CSS allowlist: color, background, background-color, font-size,
font-weight, font-family, letter-spacing, line-height, text-align, padding*, margin*, gap, border-radius,
width, height, opacity, box-shadow, transform); `attr` sets an allowlisted attribute. Anything ambiguous →
`{"ok":false,"route":"agent"}` and the client sends it as a comment turn instead.

### 4.5 Transcript format (`chats/<cid>.md`, used by handoff)
```
# <chat title>
_Started <iso ts>_

## User
<text>

## Assistant
<text>
_[tool: Write]_ index.html
```

---

## 5. Preview origin (W1 `preview.py`, runtime by W3)

Second aiohttp site on `127.0.0.1:<design.preview_port>`, started from `start_proxy_background()`
(coordinator wires the call to `preview.start_background()`), serving:

- `GET /p/{pid}/{path:.*}` — project files (safe_relpath, 404 outside). For `.html` responses, inject
  before `</head>` (or at top): `<script src="/_td/td-bridge.js"></script>` and, when the page uses Babel,
  `<script src="/_td/td-babel-source.js"></script>` before the first `text/babel` script.
- `GET /_td/{name}` — files from `services/design/runtime/`.
- `GET /starters/{name}` — files from `services/design/starters/`.
- `GET /ds/{pid}/{path:.*}` — the project's staged `_ds/` (read-only).
- Headers on every response: `Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'
  'unsafe-eval' https://unpkg.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; style-src 'self'
  'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; img-src 'self'
  data: blob: https:; media-src 'self' data: blob: https:; connect-src 'self'; frame-ancestors
  http://127.0.0.1:<proxy.port> http://localhost:<proxy.port>`, `Cache-Control: no-store`.
- The host embeds previews as `<iframe src="http://127.0.0.1:1237/p/<pid>/<file>" sandbox="allow-scripts
  allow-same-origin allow-popups allow-forms">` — same-origin *to the preview port* only, so storage works
  and the page still cannot reach `:1235`.

---

## 6. Bridge protocol (host ⇄ preview iframe, `postMessage`, W3 implements the iframe side, W2 the host)

Every message is `{type, ...}`. The host checks `event.origin === previewOrigin`; the bridge checks the
parent origin against `?host=` it was given (W1 appends `?td_host=<origin>` to iframe URLs; the bridge also
accepts `document.referrer`'s origin).

Host → preview:
| type | payload | effect |
|---|---|---|
| `td:set-mode` | `{mode:"view"|"comment"|"edit"|"text"|"knobs"|"draw"}` | switch interaction mode |
| `td:highlight` | `{td_id?, selector?}` | outline an element (e.g. hovering a comment) |
| `td:eval` | `{id, code}` | run JS in the page, reply `td:eval-result` (used by `design_eval_js`) |
| `td:apply-style` | `{td_id?, selector?, style:{…}}` | live preview of an inspector change (allowlist) |
| `td:set-viewport` | `{width, height}` | informational (host resizes the iframe) |
| `td:slide` | `{action:"next"|"prev"|"first"|"last"|"go", index?}` | deck navigation (1-indexed) |
| `__activate_edit_mode` / `__deactivate_edit_mode` | — | Tweaks protocol (unchanged names) |

Preview → host:
| type | payload |
|---|---|
| `td:ready` | `{title, url, has_babel, deck:{count}?, tweaks:bool}` |
| `td:console` | `{level:"log"|"warn"|"error", args:[str], at}` (also `window.onerror`, unhandledrejection) |
| `td:select` | `{mode, td_id?, source_loc?, selector, bbox, text, html_hint, computed:{…}, mentioned_element:"<mentioned-element>…"}` |
| `td:comment-target` | same as `td:select` + `{click:{x,y}}` in comment mode |
| `td:text-edit` | `{td_id?, source_loc?, selector, old_text, new_text}` |
| `td:style-edit` | `{td_id?, source_loc?, selector, style:{…}}` (move/resize/knobs produce these) |
| `td:draw` | `{png_data_url, bbox}` |
| `td:slide-changed` | `{index, count}` (1-indexed) |
| `td:eval-result` | `{id, ok, value?, error?}` |
| `td:navigate` | `{path}` (relative link clicked; host updates its tab bar) |
| `__edit_mode_available` `{visible?}` / `__edit_mode_dismissed` / `__edit_mode_set_keys` `{edits:{k:v}}` | Tweaks |

`<mentioned-element>` format (the bridge builds it, the prompt consumes it):
```
<mentioned-element>
react: App > PricingSection > PlanCard[2/3]
dom: body > main > section[data-td-screen="Pricing"] > div.plans > article:nth-of-type(2)
id: data-td-id="plan-pro"   src: index.html:88:9
text: "Pro — $24/mo"
children: h3, p, ul(4), button
</mentioned-element>
```

---

## 7. W3 runtime + starters

- `runtime/td-bridge.js` — everything in §6 iframe-side; overlay UI (hover outline, selection box with
  resize handles, comment pins, inline `contenteditable` for text mode, draw canvas), console capture,
  deck detection (`#deck-stage` / `[data-td-deck]`), React fiber chain read (`__reactFiber$*` keys,
  dev builds), strips any `data-td-rt-*` it added before reporting HTML hints.
- `runtime/td-babel-source.js` — registers a Babel standalone plugin (`Babel.registerPlugin`) that adds
  `data-td-src="<file>:<line>:<col>"` to every host JSX element, and sets it as a default plugin for
  `text/babel` scripts on the page.
- `runtime/td-telecode.js` — `window.telecode.complete(promptOrMessages)` → `postMessage` to host
  (`td:complete {id, messages}`), host answers via the proxy `/v1/chat/completions` (local model), reply
  `td:complete-result`; alias `window.claude = window.claude || {complete: window.telecode.complete}`.
- `starters/`: `design_canvas.jsx`, `ios_frame.jsx`, `android_frame.jsx`, `macos_window.jsx`,
  `browser_window.jsx`, `animations.jsx` (`Stage`, `Sprite`, `useTime`, `useSprite`, `Easing`,
  `interpolate`, entry/exit helpers, scrubber, `window.tdTimeline = {duration, seek(t), play(), pause()}`),
  `deck_stage.js` (custom element `<deck-stage>` implementing `prompts/deck.md` exactly), plus
  `starters/index.json` `[{kind, file, description}]`. All original code, React 18 UMD globals, no build.

---

## 8. W4 render + export

`render.py` — headless Edge over CDP (launch `msedge --headless=new --remote-debugging-port=0
--user-data-dir=<data/design/.edge-profile>`; talk CDP over WebSocket with `aiohttp`; one shared browser,
lazily started, idle-closed after 5 min; Job-Object-safe via `process.py` if available). API (async):
`screenshot(url, width, height, full_page=False, selector=None, steps=None, scale=1) -> bytes`,
`console_errors(pid, file) -> list[str]`, `thumbnail(pid) -> path`, `print_pdf(url, landscape, width_px,
height_px) -> bytes`, `eval_js(url, code) -> any`, `dom_snapshot(url) -> dict` (for HTML→layers).
Preview URLs are the §5 origin.

`export.py` routes (`proxy/api_design_export.py`), all `POST /api/design/projects/{pid}/export/<kind>`
with `{file?, board_ids?, options}` → `{"job_id"}`; `GET …/export/jobs/{job_id}` → `{status, progress,
download_url?, error?, flags?}`; `GET …/export/download/{job_id}` streams the result:
`html` (standalone, inlined, `__bundler_thumbnail` splash), `zip`, `pdf` (deck → one page per slide),
`pptx` (`mode:"screenshots"|"editable"`, `fontSwaps`, `hideSelectors`, notes from `#td-speaker-notes` /
`<script id="speaker-notes">`; flags `duplicate_adjacent`, `slide_size_mismatch`, `no_speaker_notes`),
`png` (`scale` 1–3, `format:"png"|"jpeg"|"webp"`, `quality`), `mp4` (`window.tdTimeline.seek` frame capture →
ffmpeg), `handoff` (§4.6 of teledesign.md: `README.md` from `prompts/handoff_README.md`, `chats/*.md`,
`project/…`, `project/_ds/…`, `uploads/`, primary = active file) plus `GET …/export/handoff/prompt` →
`{"prompt"}` (paste-ready text with the bundle path). `python-pptx` is added to requirements by the
coordinator; import it lazily.

---

## 9. W5 design systems

`systems.py` public functions used by others:
`prompt_context(system_id) -> str` (USAGE.md + DESIGN.md + tokens.css + compact manifest + file index, per
`prompts/README.md`), `stage(system_id, project_dir) -> Path` (copy into `_ds/<slug>/`, selective per the
"no bulk >20 files" rule: tokens, styles, manifest, DESIGN/USAGE, bundle, components index),
`get_style(style_id?) -> dict|list` (from `seeds/styles/styles.json`), `lint(project_dir, system_id) ->
list[finding]` (via `lint.py`).
Routes (`proxy/api_design_systems.py`, extend what is there): `GET …/systems/{id}/files` + `/files/{path}`,
`PUT …/systems/{id}/files/{path}`, `POST /api/design/systems/{id}/extract` `{sources:[{type:"codebase"|"github"|"url"|"files"|"screenshots", ref}]}` (starts a design-system chat turn through W1's `generate` with kind `design_system`; returns `{project_id, chat_id}`), `POST …/{id}/publish`, `POST …/{id}/default`, `POST …/{id}/remix` (new chat), `POST …/{id}/cleanup`, `POST …/{id}/try` (scratch project), `POST /api/design/systems/import` (multipart zip or Claude-Design `_ds/` folder zip), `GET …/{id}/export` (zip), `POST …/{id}/bundle` (rebuild `bundle.js`), `GET /api/design/styles`, `POST /api/design/lint` `{project_id}`.
Specimen cards render through the preview origin: W5 adds nothing there; the frontend loads
`http://127.0.0.1:1237/dsys/{system_id}/{path}` — **W1 adds that route in `preview.py`** serving
`data/design/systems/<id>/`.

---

## 10. W6 canvas editor (open-pencil) — as built

`python tools/build_open_pencil.py` (`--check` = patches still apply; `--tag` = try another release) →
clones into `data/design/.open-pencil-src`, applies `patches/open-pencil/0001…0007`, bun install + build,
vendors `proxy/static/design/editor/` (+ `NOTICE`, `BUILD_INFO.json`) and dumps
`services/design/editor_tools.json`. Never edit inside the source dir — every build hard-resets it.

- **Board keys.** open-pencil renumbers node ids on every reopen, so an HTML board is identified by a
  permanent *board key* stored in the frame's plugin data inside `doc.fig`; `boards.json` is keyed by
  board key. Register with the canvas tool `telecode_board_mark {node_id, src, width, height}` (or
  `POST …/editor/boards`), list with `telecode_board_list`, remove with `telecode_board_unmark`.
- **Server:** `/design/editor/{path}` (wasm MIME, immutable hashed assets, SPA fallback),
  WebSocket `/api/design/editor-bridge?project=<pid>` (same-origin only; token from `?bridge_token=` or the
  injected meta tag), `GET …/projects/{pid}/editor` (status incl. `has_canvas`), `POST …/editor/call`,
  `POST/DELETE …/editor/boards`, `GET /api/design/canvas/tools`, `POST …/canvas/call` (W7 aliases).
  `editor_bridge.call(pid, tool, args)` / `call_mcp()` / `call_threadsafe()`.
- **Editor → host** messages are `{type, payload}`: `td-editor:ready`, `td-editor:viewport {x, y, zoom,
  canvas:{x,y,width,height}}`, `td-editor:frames [{id: board_key, node_id, name, x, y, width, height}]`,
  `td-editor:selection {ids, nodes}`, `td-editor:saved`. Screen position = canvas_xy × zoom + pan +
  canvas rect offset + iframe offset. **Host → editor:** `td-editor:open {pid}`, `td-editor:focus
  {node_id}`, `td-editor:insert-frame {name, width, height, x?, y?}` → `td-editor:frame-created {id,
  node_id}`, `td-editor:mark-board {node_id, src, width, height}`.
- Settings: `design.editor.allow_eval` (default false — open-pencil's `eval` tool), `design.editor.disabled_tools` (list).
- Prompts `canvas.md` + `layer_boards.md` describe the real tools via `design_canvas_call`.

## 11. W7 agents + integrations

- `mcp_server/tools/design.py` — FastMCP tools (auto-bridged to local models): `design_list_projects`,
  `design_get_project`, `design_create_project`, `design_list_files`, `design_read_file`,
  `design_write_file`, `design_delete_file`, `design_copy_starter`, `design_get_comments`,
  `design_resolve_comments`, `design_register_assets`, `design_show` (emit `show` event to the UI),
  `design_screenshot`, `design_eval_js`, `design_image_metadata`, `design_export`, `design_get_app_state`
  (project, active board/file, selection), `design_list_systems`, `design_get_system`, `design_get_style`,
  `design_read_skill`, `design_canvas_call` (proxies one open-pencil tool through `editor_bridge.call`),
  `design_spawn_agents`. Tools talk to the running proxy over HTTP (`http://127.0.0.1:<proxy.port>`), not by
  importing services, because the MCP server can run in a separate process.
- `parallel.py` + `proxy/api_design_agents.py`: `POST /api/design/projects/{pid}/agents`
  `{mode:"split"|"side_by_side"|"let_it_cook"|"jury", count, engines?:[…], prompt, variant:"layout"|"style"?,
  iterate:bool}` → `{"run_id","chats":[…]}`; `GET …/agents/{run_id}`; `POST …/agents/{run_id}/stop`. Each agent
  = its own chat (W1 API) targeting its own board/file prefix; jury = generate → parallel critics
  (`prompts/critique.md`) → reviser until score ≥ 8.0, max 3 rounds.
- `mcp_registration.py` + `POST /api/design/mcp/register {client:"claude_code"|"codex"|"gemini"|"antigravity"}`
  → runs the client's own `mcp add` command for the telecode MCP server (installed CLI syntax:
  `claude mcp add --transport http --scope user telecode http://127.0.0.1:1236/mcp`); `GET /api/design/mcp/status`.
- `/design` Telegram command (`bot/design_handlers.py`): `/design <prompt>` in a topic → create/iterate a
  project (chat per topic), post the thumbnail via `FrameSender`-style photo when the turn finishes,
  `/design comments` lists open comments. Notify-on-done for turns started from the web UI when
  `design.telegram_notify` is true.
- MCP prompts `design` and `design-sync` (create a project from any CLI; sync a codebase's tokens/components
  into a design system through W5's extract).

---

## 12. W2 frontend

One page (`teledesign.html` + `/design/app/*.js` ES modules, vanilla, no build, no external deps except
optional CodeMirror from cdnjs for the Code view). Must implement every UI item in parity §A/§B that is not
owned by the canvas editor itself: project gallery (grid, thumbnails, sort, search, templates), new-project
dialog (kind, system, style archetype, template), chat panel (chat tabs, engine/effort/local picker, message
list with streamed text, tool lines, todo checklist, question-form renderer with all controls + mandatory
options, attachments/uploads, queue + Stop, cost/context meter), canvas area (hosts the editor iframe when
`/design/editor/index.html` exists, else a built-in pan/zoom board grid; HTML-board iframes overlaid on
frames from `td-editor:frames`), preview tab bar + viewport switcher + mode toolbar
(view/comment/edit/text/knobs/draw), Canvas|Code|Preview switch (Code = file editor with save → `PUT files`),
Comments panel (pins, authors, batch send, resolve), Files panel, Versions (timeline, compare side-by-side +
diff, restore), Review (assets + status), Tweaks panel (driven by `__edit_mode_*`), inspector (props for the
selected element → `td:apply-style` live + `POST edits`), deck presenter + speaker notes, Export menu (all W4
kinds + job progress + download cards), Share dialog, Systems browser (list, specimen cards by group, tokens
table, components, fonts; create/extract/import/publish/default/remix/try), keyboard shortcut sheet (`?`),
deep links (`?project=&board=&node=&file=&chat=`), light/dark theme toggle, toasts. Must degrade gracefully
when an API route returns 404 (feature not built yet) — hide the control, never crash.
