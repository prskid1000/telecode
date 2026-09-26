# TeleDesign — plan

One design tab inside telecode that gives **Claude Design** (prompt → generated, iterable HTML
prototypes / decks / one-pagers, design systems, comments, tweaks, export, Claude Code handoff) and
**pen.dev** (an agent-editable canvas with auto-layout, components, variables/themes, code view) on a
**single canvas**, driven by the CLIs telecode already runs (Claude Code / Codex / Antigravity / local
models through the proxy).

- Entry: tray → **Open Web UI** → `http://127.0.0.1:1235/design`; the shared top bar switches to Team (`/team`) and Tasks (`/tasks`).
- Feature-by-feature checklist: **[teledesign-parity.md](teledesign-parity.md)** — every Claude Design and
  pen.dev feature, with the phase it lands in. This file is the architecture; that one is the scope.
- Research sources (2026-09-25): Claude Design support/admin docs, a real handoff bundle and the leaked
  system prompt (read for behaviour only); docs.pen.dev, the public `.pen` schema 2.19 and `@pen.dev/cli`
  skill docs; nexu-io/open-design (Apache-2.0); open-pencil/open-pencil (MIT, v0.15.1).

### Decisions (2026-09-25)
| Decision | Choice |
|---|---|
| Tabs / modes | **One tab, one canvas.** No per-project mode. |
| Canvas engine | **open-pencil** (MIT) — patched static web build, iframed at `/design/editor/` |
| Saved format | **`.fig`** (open-pencil native). `.pen` = import now, export later (our writer, upstreamable) |
| Built-in agent | telecode **direct task sessions** (one permanent session per project chat, `--resume`). **Not** Team-Mode jobs |
| Code | Canvas \| Code \| Preview on every board; open-pencil's Code panel for layer boards |
| Prompts & default systems | **Original** TeleDesign prompts + seed systems; no Claude Design / pen.dev text or bundled libraries (licence) |

---

## 1. The canvas and its boards

A project holds one or more infinite canvases — canvas documents `docs/<id>.fig`, one of them the
default, one open in the editor at a time (the canvas bar's document switcher; agents use
`telecode_doc_list/create/open`). Everything on a canvas is a board:

| Board | What it is | Edited by | Best for |
|---|---|---|---|
| **Layer board** (pen.dev side) | native open-pencil frame: auto-layout (Yoga flex + grid), components/variants/instances, variables & collections | direct manipulation + agent via open-pencil tools | precise UI, component libraries, design systems, wireframes |
| **HTML board** (Claude Design side) | a frame registered in `boards.json` → `{src, width, height}`; the host overlays a sandboxed iframe of the generated page on it, tracking pan/zoom | agent writes files; user comments / tweaks / inline-edits | interactive prototypes, decks, animation, rich marketing |

- **Views:** *Canvas* (the board in place) · *Code* (HTML board: its real source, editable; layer board:
  open-pencil's Code panel — JSX / Tailwind JSX / HTML+CSS, edit → live preview → commit) · *Preview*
  (HTML board: full-size live page; layer board: its HTML export, live).
- **Convert:** HTML board → layers via open-pencil `dom-css` (HTML/CSS/Tailwind → editable nodes, the
  equivalent of pen.dev's browser import); layer board → code via its exporters (and → HTML board by
  writing that export as a project file).
- Why a registry and not a node: `.fig` has no "live page" node type. The frame keeps its size and
  position in the document; the registry says what renders on it; the overlay does the rendering.

---

## 2. Architecture

```
/design  (proxy/static/teledesign.html — vanilla JS shell)
 ├─ left:   projects gallery · design systems · templates
 ├─ centre: <iframe /design/editor/?project=<id>>   ← patched open-pencil build (CanvasKit + Yoga)
 │            + HTML-board overlays: <iframe sandbox="allow-scripts"> per registered frame,
 │              positioned from the editor's viewport; bridge for comment/edit/tweaks/draw
 └─ right:  Chat (tabs) · Comments · Files · Tweaks · Versions · Review · (Layers/Props live in editor)
      │ REST + SSE /api/design/*            │ WebSocket /api/design/editor-bridge
proxy (aiohttp :1235)  proxy/api_design.py  │
 └─ services/design/                        │
     store.py      projects, canvas (.fig), boards, imports, systems      ← done
     prompts/      original prompt stack (34 files, see prompts/README.md)  ← done
     seeds/        default design systems + style archetypes (auto-installed)  ← done
     generate.py   chat turn → prompt stack → task_manager.submit_task → SSE events
     bridge.py     speaks open-pencil's register/request/response protocol to the editor page,
                   so MCP tool calls execute in the canvas the user is watching (no Node process)
     render.py     headless Edge over CDP: HTML-board screenshots, console logs, print-to-PDF
     export.py     standalone HTML · ZIP · PDF · PPTX · PNG · MP4 · handoff bundle · .pen
     systems.py    design-system extraction (codebase / GitHub / URL / files / screenshots)
     lint.py       design-system adherence (tokens, fonts, imports, component props)
 mcp_server/tools/design.py   design_* tools (+ proxied open-pencil tools) for any CLI / local model
```

### 2.1 open-pencil integration
Built once off-box with Bun from a pinned release tag plus `patches/open-pencil/*.patch` — the same
"patch series, not a fork" rule as `patches/llama.cpp/` — then vendored as static files under
`proxy/static/design/editor/`. No Node at runtime.

| Patch | Why |
|---|---|
| `createWebHistory(import.meta.env.BASE_URL)` (`src/router.ts`) | serve under `/design/editor/` |
| drop `openPencilPwaPlugin()` (`vite.config.ts`) | service worker scope `/` would hijack the proxy |
| base-relative bundled font paths (`packages/core/src/text/fonts.ts`) | fonts hard-coded at `/` |
| `canConnect` true + bridge URL/token from query (`src/app/automation/mcp/runtime.ts`, `bridge/server.ts`) | production web builds never attach to MCP |
| `telecode` `StorageAdapter` (`src/app/integrations/storage/providers.ts`) → `GET/PUT /api/design/projects/{id}/canvas` | save/load to our store instead of download/IndexedDB |
| HTML-board hooks: expose viewport + frame bounds changes to the host | overlay positioning |
| 0013 several canvas documents: `?doc=`, storage binding `<project>/<doc>`, bridge socket per document, `telecode_doc_open` | more than one canvas per project |
| 0014 deterministic JSON mirror PUT to `…/docs/{doc}/canvas.json` after every save | git-friendly diffs of a binary `.fig` |
| 0015 script nodes (sandboxed iframe + Worker, `@input` header, `telecode_script_*`) | generated / data-driven layers |
| 0016 theme axes: cross-collection aliases resolve in the node's mode for that collection; `telecode_theme_*` | light/dark × brand × density |
| 0017 component slots on instance-swap properties (`telecode_slot_*`) | per-instance content that survives `.fig` |
| 0018 shader (SkSL runtime effect) and mesh-gradient (Coons patch) fills as CUSTOM paints + plugin data (`telecode_fill_*`) | procedural fills |

- aiohttp serves `/design/editor/*` with `application/wasm` for `.wasm` and an `index.html` fallback.
- The editor's built-in AI chat is pointed at the proxy (`openai-compatible`, base `…:1235/v1`) or replaced
  by our chat via `window.openPencil.setChatTransport`.
- Upgrades: re-apply patches per release (0.15.0 already broke the MCP/SDK contracts); a `tools/`
  drift check like `vendor_drift.py` runs `git apply --check` against new tags.
- Collaboration (Yjs) is not needed for agent + user: agent tool calls land in the same live editor.

### 2.2 The built-in agent (direct task sessions)
- Each project chat = one task session in namespace `design` (`services/session`), cwd = the project folder,
  resumed with `last_claude_session_id` / `last_codex_session_id`. Multiple named chats per project.
- A turn: `generate.py` assembles the prompt stack (`prompts/README.md` order), stages
  `CLAUDE.md`/`AGENTS.md` (charter + design-system pointer — never overwriting a user's own root file), and
  calls `task_manager.submit_task(task_type, params={prompt, is_local, agent_id}, metadata={source:"design",
  project_id, chat_id, turn_id}, session_id=…)`.
- Optional persona: pick any Team-Mode agent to borrow its `AGENT.md`; that is the only Team-Mode touch.
- Parallel agents / variations / "let it cook" / critique jury = N sessions fired concurrently by
  `generate.py` (Split Work vs Side by Side), each writing its own boards. Engine + effort per turn.
- Tools available to the agent: open-pencil's tools (via the bridge) for layer boards; file tools (its
  own CLI) for HTML boards; `design_*` MCP tools for everything host-side.
- Events: `/api/design/projects/{id}/events` (SSE) streams narrative / tool / todo / done / file-changed /
  cost, so the UI shows progress and reloads boards as files land. Stop = terminate the task.

---

## 3. Data model (`data/design/`)

```
projects/<id>.json            {id,title,kind,design_system_id,agent_id,session_id,current_version,archived,…}
projects/<id>/                ← agent cwd
  docs/<doc>.fig              canvas documents (open-pencil); a legacy doc.fig moves to
                              docs/main.fig on first access                                 ← done
  docs/canvases.json          {default, docs:[{id, name, created_at, updated_at}]}          ← done
  docs/<doc>.fig.json         deterministic JSON mirror, written by the editor on save      ← done
  scripts/*.js                script-node programs (any project .js path)                   ← done
  boards.json                 {frame_node_id: {src, width, height}}                         ← done
  imports/*.pen               imported pen.dev files                                        ← done
  comments.json               [{id, board_id, anchor:{node_id|selector|source_loc}, author, note,
                                status: open|sent|resolved, mentioned_element, created_at}]
  assets.json                 review manifest: deliverables with group/viewport/subtitle and
                              status needs-review|approved|changes-requested
  chats/<chat>.md             transcripts (handoff format)
  *.html *.jsx *.css assets/ uploads/ scraps/*.napkin     HTML-board sources
  .versions/manifest.json     [{v, files{path:sha256}, prompt, parent, origin: agent|user|tweak, at}]
  .versions/<sha>             content-addressed snapshots (incl. docs/*.fig + mirrors)
  _ds/<system>/               staged copy of the attached design system (read-only to the agent)
systems/<id>.json + systems/<id>/   see §4.4 (format mirrors seeds/)
templates/<id>/                     saved projects with intro text + cover
```

Kinds (`store.VALID_KINDS`): prototype, slides, wireframe, one_pager, animation, landing_page,
mobile_app, web_app, dashboard_table, design_system, other.

---

## 4. Feature areas (summary — full list in the parity checklist)

### 4.1 Core loop
Clarifying `<question-form>` (text, options, svg-options, slider, color, file, freeform; mandatory
"Explore a few options / Decide for me / Other"), direction picker from style archetypes, chat with live
todo/progress, queue + steer, Stop, engine/effort picker, cost + context meter, multiple chats, auto title,
`done` gate (console errors must be clean), silent-on-pass verifier + directed checks, versions with
compare/restore, asset review pane, files panel, preview tab bar, viewport switcher, deep links
(`?board=` / `?node=`).

### 4.2 HTML boards
React 18.3.1 / Babel 7.29 UMD with SRI, multi-file, `data-td-id` anchors (+ Babel plugin stamping
file:line for write-back), comment / inline-text / knobs / move-resize modes, `<mentioned-element>`
payload, hard-scoped `<attached-comments>` turns with authors and batching, Tweaks (`__edit_mode_*` +
`EDITMODE-BEGIN/END`, written back without a model call), draw/napkin, 7 starters (canvas, iOS, Android,
macOS, browser, animation stage, deck stage), deck contract (1920×1080, 1-indexed labels, speaker notes,
print CSS), `window.telecode.complete()` (aliased as `window.claude.complete` for imported projects).
Previews served from a **separate origin** (second port) with CSP, not just `sandbox`.

### 4.3 Layer boards (open-pencil)
Inherited: CanvasKit renderer, Yoga flex + grid, pen tool / vector editing / booleans, components +
variants + instances, variables & collections, text + fonts, image/stock fills, icons, layers/props panels,
Code panel, XPath query, lint, token/cluster analysis, JSX/SVG/PDF/PPTX/PNG/`.fig` export, `.fig` +
`.pen` import, 113 agent tools. Added by patches: `placeholder` "working…" frames, Slides panel +
Present (0010–0011), several documents + JSON mirror (0013–0014), script nodes (0015), theme axes (0016),
slots (0017), shader + mesh fills (0018) — the canvas bar's **Theme** menu and **Layer** panel
(`app/canvas_extras.js`) drive the last three. Still open: keyboard map parity and the gaps listed per row
in parity §B.

### 4.4 Design systems (creator)
- Package (both seeds and user systems): `system.json`, `DESIGN.md` (Sources, Context, Colour, Type,
  Spacing, Radius, Elevation, Motion, Iconography, Imagery, Components, Known gaps, Intentional additions),
  `USAGE.md`, `tokens.css` + `tokens/*.css` + `tokens.json`, `manifest.json` (namespace, components,
  startingPoints, cards, tokens, fonts, brandFonts, themes), `components/**.jsx` + `*.card.html`
  (`@tdCard group=…`), `guidelines/`, `ui_kits/`, `bundle.js` (`@ds-bundle` header + source hashes),
  `adherence.json`, `system.lib.pen` (the same tokens + components as a canvas library — the portable
  source; `openpencil convert` produces the `.fig` library the editor loads),
  `SKILL.md` export for coding agents.
- Create from: codebase path, GitHub repo, URL (brand extract via headless Edge, through `media_fetch`),
  PDF/PPTX/images/Figma `.fig`, screenshots, or chat. Runs as a design-system chat in a direct session
  (explore → DESIGN.md + tokens → cards → manifest → review), status `draft → published`.
- Lifecycle: default (exactly one), published toggle, Remix, "clean up" for imported systems, "Try it"
  scratch project, import Claude Design `_ds/` folders and zips, export zip.
- One token source (`tokens.json`) → `tokens.css` (HTML boards) + editor variables (layer boards); lint both.
- Seeds: `services/design/seeds/` — Neutral (default, shadcn/ui MIT tokens), Editorial, Midnight,
  Playful, Technical + 16 original style archetypes for the direction picker (`design_get_style`).

### 4.5 Export
Standalone HTML (inlined, `__bundler_thumbnail`), ZIP, PDF (CDP print; decks one page per slide; open-for-print),
PPTX (python-pptx: screenshots + notes, then editable text; font swaps; validation flags), PNG/JPEG/WEBP
1–3×, MP4 (frame capture → ffmpeg, via `window.tdTimeline.seek`), handoff bundle (`README.md` "CODING
AGENTS: READ THIS FIRST", `chats/`, `project/`, `_ds/`, active file recorded) + copy-prompt + "send to a
coding session in a chosen repo", `.fig` / `.pen` / JSX / Tailwind / HTML+CSS for layer boards, share
snapshots with view/comment/edit tokens. Send-to (Google Slides via `gws` first, Canva, Vercel…) deferred.

### 4.6 Integrations
MCP tools for external CLIs (`design_*`, including `/design` and `/design-sync` prompts), one-click MCP
registration for Claude Code / Codex / Gemini / Antigravity, Telegram `/design` + notify-on-done +
screenshots, Routines for scheduled refreshes, optional Team-Mode job step "open/iterate a design".

---

## 5. Phases

| Phase | Deliverable |
|---|---|
| **0 — Scaffold** ✅ | tray entry, `/design` shell, store (projects, `.fig` canvas, boards, `.pen` import, systems), REST, prompt set, seed systems + archetypes (auto-installed) |
| **1 — Core loop on HTML boards** | stdin prompt fix, `generate.py` + SSE, question form, chats, preview origin + bridge, comments → scoped edits, versions, files/tab bar, done gate, standalone HTML + ZIP |
| **2 — Design systems** | package, seeds loaded, create-from-codebase/GitHub/URL/files, browser tab, lint, prompt injection, archetypes |
| **3 — HTML-board editing, decks, export** | tweaks, inline text / knobs / move-resize write-back, draw/napkin, starters, deck + notes, PDF/PPTX/PNG/MP4, handoff, verifier |
| **4 — Canvas (open-pencil)** | patched build vendored, storage adapter, bridge + tools, HTML-board overlays on frames, Convert both ways, Code view, `.pen` import, parity §B items, prompts `canvas.md`/`layer_boards.md` updated to the real tool set |
| **5 — Agents** | parallel sessions (split / side-by-side / let it cook), jury, skills library, MCP tools + registration, Telegram, routines, handoff-to-session |
| **6 — Polish** | share snapshots + roles, templates, gallery, shaders/scripts, image generation, send-to, `.pen` export, Figma import |

Phase 1 deliberately starts on HTML boards inside the shell's own preview so the agent loop can ship
before the editor build exists; phase 4 moves those boards onto the canvas.

---

## 6. Risks and prerequisites

- **Prompt length (blocker for P1).** `services/task/handlers/claude_code.py:130` passes `-p <prompt>` on the
  command line with `shell=True`; design prompts exceed the ~8 KB (`.cmd` shim) / 32 KB Windows limit.
  Move Claude Code to stdin (`--input-format stream-json`), and check Codex (`codex exec -`) and Antigravity.
- **Unauthenticated API vs generated pages.** Previews go on a separate origin with CSP `connect-src` limited
  to the pinned CDNs; mutating `/api/design/*` routes accept only JSON / octet-stream (non-CORS-simple), which
  a sandboxed page cannot send cross-origin without a preflight. The canvas `PUT` already enforces this.
- **open-pencil churn.** Pre-1.0, breaking changes per minor; patch series + drift check; pin the tag.
- **`.pen` fidelity.** Import is lossy (no gradients/image fills, prompt nodes dropped, single page); say so
  in the import dialog. Our `.pen` writer comes later.
- **URL inputs** (web capture, brand extract, remote images) go through `proxy/media_fetch.py`.
- **Licensing.** open-pencil MIT + deps (CanvasKit BSD-3, Yoga MIT, Inter/Noto OFL) → NOTICE. open-design
  Apache-2.0 → attribution in `prompts/NOTICE`. pen.dev: format knowledge only; its EULA forbids reverse
  engineering and competing products — no binaries, WASM, bundled libraries or guide text. Claude Design
  prompt: behaviour only, never text. No brand-named seed systems.
- **Token burn.** Route verifier/critique/title to the local model by default.
- **Subprocess lifecycle.** Design turns inherit the task-handler Job-Object gap (CLAUDE.md "Shared caveat");
  Stop must terminate the CLI.
- **Prompt set follows the canvas.** `prompts/canvas.md` and `layer_boards.md` currently describe a
  `design_pen_ops` op list and an `{type:"html"}` node; both are rewritten in P4 to open-pencil's tools and
  the `boards.json` registry.
