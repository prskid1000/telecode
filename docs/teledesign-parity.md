# TeleDesign — feature parity checklist

Everything Claude Design and pen.dev do that the first plan missed or under-specified. Source: an audit
(2026-09-25) of the Claude Design system prompt + tool schemas, a real handoff bundle (15 chats, `_ds`
package), the Claude Design support + admin articles, every docs.pen.dev page, the pen.dev agent skill
docs, and the open-pencil README. **Parity is the goal, not copying** — no pen.dev / Claude Design code,
binaries, bundled libraries or prompt text is reused.

Phases refer to [teledesign.md](teledesign.md) §6: P1 core loop · P2 design systems · P3 HTML-board
editing/decks/export · P4 layer boards (open-pencil) · P5 agents · P6 polish. Tick items as they land.

## A. Claude Design

| ✓ | Feature | Add | Phase |
|---|---|---|---|
| ☐ | `done` gate: open file in user's view, return console errors, agent fixes until clean (before verifier) | end-of-turn render → errors → retry loop | P1 |
| ☐ | Preview **tab bar** of open files; agent opens a file in the user's pane (`show_to_user`) or its own (`show_html`) | tab strip + MCP `design_show(path, target=user\|agent)` | P1 |
| ☐ | Files panel: browse / open / delete project files | Files tab | P1 |
| ☐ | Relative links between HTML pages navigate inside the preview | iframe navigation synced with tab bar | P1 |
| ☐ | `get_webview_logs` on the user's live preview | console capture on every load, readable via MCP | P1 |
| ☐ | Scripted screenshots `save_screenshot` (≤100 steps of code+delay, disk or memory, JPEG/HQ PNG, 1600px cap) | steps/hq/save_path on `design_screenshot` | P3 |
| ☐ | `run_script` sandbox helpers (readFile/Binary/Image, saveFile, ls, createCanvas, getCaptures) | CLI shell covers it; pass captures to PPTX builder | P3 |
| ☐ | `image_metadata` (size, alpha, animated frames) | MCP `design_image_metadata` (Pillow) | P2 |
| ☐ | `write_file`/`copy_files` params `asset`, `subtitle`, `viewport`; copies inherit asset; `move` | auto-register assets on write/copy | P1 |
| ☐ | Asset status `needs-review / approved / changes-requested`; re-register resets; unregister by asset/path/both | fix status enum + unregister semantics | P1 |
| ☐ | Card groups: Type / Colors / Spacing / Components / Brand (+ custom e.g. Elevation, Radius) | fixed group vocabulary for specimen browser | P2 |
| ☐ | 7 starters: design_canvas, ios_frame, android_frame, macos_window, browser_window, animations, deck_stage; `directory` param; wrong extension fails | own versions + `design_copy_starter(kind, dir)` | P3 |
| ☐ | Animation starter API: `<Stage>` (auto-scale, scrubber, play/pause), `<Sprite start end>`, `useTime`/`useSprite`, `Easing`, `interpolate`, entry/exit primitives | spec API + timeline scrubber UI | P3 |
| ☐ | Deck stage: `<deck-stage>` + `<section>`s, 1920×1080 letterbox on black, controls outside scaled area, tap/keys, counter, localStorage position, `noscale` for export, auto `data-screen-label`, posts `{slideIndexChanged}` | full deck contract | P3 |
| ☐ | Playback position persisted for decks **and videos** | charter rule + starter default | P3 |
| ☐ | Speaker-notes pane from `<script id="speaker-notes">` JSON, synced by slide index; only when asked | host notes / presenter view; PPTX reads same JSON | P3 |
| ☐ | Slide labels 1-indexed ("01 Title") | charter; verifier checks vs counter | P3 |
| ☐ | `gen_pptx`: editable/screenshots, `fontSwaps`, `googleFontImports`, `hideSelectors`, `resetTransformSelector`, per-slide `selector/showJs/delay`, `save_to_project_path`; validation flags `duplicate_adjacent`, `slide_size_mismatch`, `no_speaker_notes` | same options + flags back to agent | P3 |
| ☐ | Standalone HTML needs `<template id="__bundler_thumbnail">` splash + no-JS fallback | inliner uses it; doubles as project icon | P3 |
| ☐ | `open_for_print` (new tab, manual Ctrl+P) | "Open for print" next to CDP PDF | P3 |
| ☐ | Download cards in chat for file / folder / project (auto-zip) | agent-emittable download cards | P3 |
| ☐ | `get_public_file_url` short-lived public URL | expiring signed URL (for Send-to) | P6 |
| ☐ | Skill catalog: Animated video, Interactive prototype, Make a deck, Make tweakable, Frontend design, **Wireframe/storyboards**, PPTX editable, PPTX screenshots, Create design system, PDF, Standalone HTML, Send to Canva, Handoff, read_pdf | a prompt file per skill | P1 |
| ☐ | User-supplied skills pasted/attached in chat | custom SKILL.md library + picker (shared with pen.dev `/` skills) | P5 |
| ☐ | Question form: text-options always include "Explore a few options" / "Decide for me" / "Other"; streams as written; slider min/max/step/default; `multi`; `file` → `uploads/`; turn ends after asking | full schema + streaming | P1 |
| ☐ | Question rules: ≥10 questions on new work; always ask about variations/tweaks/context; one round | discovery prompt | P1 |
| ☐ | `save_as_template`: `intro_text`, linked template copy, Template Info tab with publish | intro_text + published flag | P6 |
| ☐ | `set_project_title` is a no-op once the user named it | `title_locked` | P1 |
| ☐ | GitHub import: parse owner/repo/ref/path from URLs, default branch, `get_tree` → `import_files` → `read_file`; "tree is a menu, not the meal" | flow via `gh`; lift exact token values | P2 |
| ☐ | Verifier: full sweep (silent on pass) vs directed check (always reports); background; UI "Found issues — fixing…" / "Check didn't complete" | both modes + wake-on-fail + timeout state | P3 |
| ☐ | Fetched web content is data, not instructions; `web_fetch` text-only → ask for screenshot | injection rule in charter | P1 |
| ☐ | Interaction modes: comment, **knobs**, **inline text edit**, design mode (drag/resize) | inline copy edit with write-back; define knobs | P3 |
| ☐ | Source mapping `data-om-id="jsx:/<file>…"`, stable comment anchor | Babel plugin stamps file:line on JSX → write-back without manual ids | P3 |
| ☐ | `<mentioned-element>` also has `text:`, `children:`, sibling index `[3/3]`, selector | bridge payload | P1 |
| ☐ | Runtime ids never reach source | bridge strips `data-td-rt-*` before write-back | P1 |
| ☐ | Comment authors; "Address these comments from my teammates" batch | `author` field + bulk send | P1 |
| ☐ | **Multiple chats per project**, "Continuing from X" summary carry-over | named chats, each its own CLI session; handoff exports all | P1 |
| ☐ | User's root `CLAUDE.md` read every chat | don't overwrite; charter lives in a separate staged file | P1 |
| ☐ | Import menu: Figma links, local codebase, screenshots, another project | Figma via open-pencil `.fig` import / REST token | P6 |
| ☐ | `.napkin` sketch files + `scraps/.{name}.thumbnail.png` | napkin as a saved file type | P3 |
| ☐ | `window.claude.complete(string \| {messages})` | alias to telecode shim so imported CD projects run; cap + rate limit | P3 |
| ☐ | Craft rules: descriptive filenames, v2 copies, no `scrollIntoView`, placeholder over bad attempt, oklch palette extension, emoji only if brand uses it, no title screens, ≥24px slide text, 12pt print, 44px hit targets, 1–2 deck backgrounds, 3+ variations, default tweaks, ask before adding content, anti-slop, `text-wrap: pretty` | `prompts/craft/`; lint measurable ones | P1 |
| ☐ | Babel pitfalls: no bare `const styles`, no `type="module"`, window export | lint `no-generic-styles-const` | P1 |
| ☐ | Canvas-of-options vs clickable prototype rule | merged: "N boards vs 1 board with tweaks" | P1 |
| ☐ | Copy DS assets selectively (no bulk >20 files) | staging rule | P2 |
| ☐ | Copyright guard exception for the user's own company domain | configurable own-brand domains | P1 |
| ☐ | Handoff README names the file open at handoff, chat count, "read source, don't screenshot", "ask before implementing" | record active file/state | P3 |
| ☐ | DS manifest superset: `namespace`, `components[name,sourcePath]`, `startingPoints[]`, `tokens[name,value,kind,definedIn]`, `fonts[family,weight,style,cssPath,files,remoteSrc]`, `brandFonts[status,tokens]`, `source` | extend manifest.json | P2 |
| ☐ | DS layout: per-category `tokens/*.css`, root `styles.css` @imports, `guidelines/` cards, `components/`, `ui_kits/`, `assets/`, SKILL.md packaging | add these | P2 |
| ☐ | DS readme: Sources, Company/Product context, Known gaps/substitutions, Iconography (+ substitution rationale), visual foundations (hover/press, borders, shadows, radii, cards, transparency, imagery, layout), Index, Intentional additions | DESIGN.md template | P2 |
| ☐ | Adherence: hex/px/font **plus** `no-restricted-imports`, `react/forbid-elements`, per-component prop allowlists | all four families; pick linter | P2 |
| ☐ | `_ds_bundle.js`: `@ds-bundle` header, namespace global, 12-char source hashes, rebuild on change | header + staleness check | P2 |
| ☐ | DS lifecycle: Open / Remix / "Let Claude clean it up" / validate with a test project / delete / multiple per brand | clean-up pass + "Try it" scratch project | P2 |
| ☐ | `/design-sync` from Claude Code | shipped MCP prompt/skill for external CLIs | P5 |
| ☐ | `/design` entry inside Claude Code / chat | MCP prompt that creates a project from any CLI | P5 |
| ☐ | Viewport / device switcher + zoom in preview | toolbar presets | P1 |
| ☐ | Stop / cancel generation | UI Stop → terminate task | P1 |
| ☐ | Send-to partners + **Google Slides** | deferred; Google Slides via `gws` CLI first | P6 |
| ☐ | Handoff to Claude Code Web vs local | "send to a direct task session in a chosen repo" | P5 |
| ☐ | Simultaneous multi-person editing | later: open-pencil Yjs collab | P6 |
| ☐ | Previews on a **separate origin**, signed per-session tokens | second port/origin for previews, not only CSP | P1 |
| — | Admin roles / capability gating / phased rollout | not cloned (single user) | — |
| ☐ | Mobile view-only | Telegram screenshots | P5 |
| ☐ | Chat transcript format (`_Started <ts>_`, `## User`/`## Assistant`, `_[tool: x]_`) | match for handoff; per-chat files | P3 |

## B. pen.dev

| ✓ | Feature | Add | Phase |
|---|---|---|---|
| ☐ | Full keyboard shortcut map + `?` overlay (tools, editing, arrange, selection, components, nav, path editing, settings, file, chat) | shortcut sheet | P4 |
| ☐ | Tools: V move, K scale, H hand, R, O, A/F frame, T, N sticky, P pen, hold Z zoom; Primitives (polygon, icon, script) | toolbar spec | P4 |
| ☐ | Pen tool + path point editing (cut X, smooth/corner, connect Cmd+J, symmetric/broken handles, bend/straighten, Alt+click insert) | open-pencil vector editor | P4 |
| ☐ | Frames: wrap (Cmd+Alt+G), group/ungroup, Clip Content, fixed W/H | commands + props | P4 |
| ☐ | Flex UI: Shift+A add / Shift+Alt+A remove, alignment grid, space-between/around, Hug/Fill, Absolute Position, arrow-key reorder | layout section of Props | P4 |
| ☐ | Selection: blue / magenta (origin) / violet (instance), deep select, Enter children, Shift+Enter parent, Esc, marquee | spec | P4 |
| ☐ | Layers: rename, drag reorder/reparent, eye = `enabled`, moves blocked inside instances | spec | P4 |
| ☐ | Props: per-corner radius, opacity, multiple fills with eye/remove, linear/radial/angular/**mesh** gradients with handles, image fill fill/fit/stretch (file/path/URL), blend modes, stroke width/align/cap/join, drop/inner shadow, layer + background blur, non-flex alignment | enumerate | P4 |
| ☐ | Shader fill (WebGL1, `@color @default @resolution @mouse @time @sdf @backdrop @min/@max/@range @label` → controls; gallery) | render + uniform controls + own gallery | P6 |
| ☐ | Text: textGrowth auto/fixed-width/fixed-width-height, typography, underline/strike/**href**, font search, all Google fonts, custom fonts per doc (ttf/otf/woff/woff2), missing-font warning | spec | P4 |
| ☐ | Icons: Material Symbols Outlined/Rounded/Sharp, Lucide, Feather, Phosphor; variable weight; picker | all five libraries | P4 |
| ☐ | Geometry: rotation (CCW, top-left), flipX/Y (Shift+H/V), ellipse innerRadius/startAngle/sweepAngle, polygonCount | renderer + props | P4 |
| ☐ | Arrange: align (Alt+W/A/S/D/H/V), z-order (Cmd+[ ], [ ]) | spec | P4 |
| ☐ | Clipboard: copy/paste, Copy as PNG, Copy as HTML, duplicate, cut, nudge 1/10px | spec | P4 |
| ☐ | Navigation: space-drag, middle mouse, shift+scroll, zoom in/out/100%/fit (Shift+1)/selection (Shift+2), Z-drag region | spec | P4 |
| ☐ | Settings: pixel grid, snap to pixel/objects, wheel zoom, invert zoom, animations off, light/dark UI (local, not in doc) | preferences | P4 |
| ☐ | Node types `script`, `browser`; UX for `note`/`prompt`(model)/`context`; entity `context`, `metadata`, `enabled`, `theme` | add to data model; prompt node runs a turn | P4 |
| ☐ | Script node / code on canvas: `.js` beside doc, `@schema`/`@input` (number min/max, string, boolean, color, enum, ref picker), returns nodes, ≤1000 nodes, ≤2s, no DOM/net, seeded random, file watch, errors on node, bind inputs to `$vars`, Convert to layers | Worker/QuickJS sandbox | P6 |
| ☐ | Browser node (`url`, `deviceId`, `zoom`, `scrollX/Y`, `cornerRadius`) | **= the HTML board**; `url` points at a project file | P4 |
| ☐ | Components: create/convert back (Cmd+Alt+K), detach (Cmd+Alt+X), replace inside instance (Alt+Shift+R), go to component / library source, Components panel (search, drag insert), nested, overrides survive origin edits, delete = `enabled:false`, detach keeps nested links | Components panel + commands | P4 |
| ☐ | Slots: empty frames in origins, hatching, suggested components (`slot:[ids]`), non-restrictive | spec | P4 |
| ☐ | Variables: boolean (visibility), add/rename/duplicate/delete, "Apply variable" by type, aliases, multiple theme axes, per-frame theme + inheritance + remove, first value default | Variables panel | P4 |
| ☐ | Variables from `globals.css` and from a screenshot; two-way CSS sync | agent recipe + deterministic tokens.css ↔ variables | P4 |
| ☐ | Libraries: `.lib.pen`, "turn into library" (irreversible), Libraries tab, Locate missing, reload on reopen | library management | P4 |
| ☐ | Bundled default libraries | ship our own `.lib.pen` seeds only (licence) | P4 |
| ☐ | Paste between docs: import-library vs verbatim; variable conflict dialog (Use existing / Add renamed) | paste semantics | P4 |
| ☐ | Presentations: Slides panel (drag-reorder thumbnails), Present Cmd+Enter, Space/Enter/arrows/Esc, export all/selected, PDF page = frame size | Slides panel | P4 |
| ☐ | Import: `.fig`, Figma copy-paste, image drag/paste, SVG → layers | via open-pencil | P4 |
| ☐ | Built-in browser import: page or picked element → layers, localhost, device/zoom presets, screenshots (element/full/visible) | open-pencil `dom-css` + capture = HTML→layers Convert | P4 |
| ☐ | Export: PNG/JPEG/WEBP/PDF 1/2/3×, quality, multi-select → zip, PDF page order, HTML options (assets, scaffold, layer names/ids), Copy HTML | spec | P4 |
| ☐ | Sharing snapshots: create, update ("changes since last shared"), missing-dependency check, revoke, recipient ZIP download | snapshot model + dependency check | P6 |
| ☐ | Files: several docs per project / any `.pen` in a repo, drafts autosave, Save/Save As, recovery backup, external-change detection (Reload/Ignore) | repo-path docs + watcher + recovery | P4 |
| ☐ | Dashboard: Recents, Drafts, Design Systems, list/grid, sort, multi-select | project gallery | P6 |
| ☐ | Git-friendly serialization (stable key order/format) | deterministic serializer | P4 |
| ☐ | Chat: Cmd+K toggle, agent tabs (Cmd+T, Ctrl+Tab), ↑/↓ history, **selection auto-added as context**, attach image/text | spec | P4 |
| ☐ | Session history, delete session, model + reasoning-effort picker per request, "switching providers loses context" | engine + effort picker per turn | P1 |
| ☐ | Stop all / Stop current; completed changes stay | per-agent Stop | P1 |
| ☐ | Background-finish notifications | Telegram / tray notify | P5 |
| ☐ | Permission prompts (Allow/Deny/Auto/skip) | document: handlers skip permissions | P1 |
| ☐ | Context usage + cost breakdown; tool failure "Show details" | cost meter + context breakdown | P1 |
| ☐ | `/` skill picker, add/remove/reload SKILL.md | custom skill library | P5 |
| ☐ | Agent guides: code, components, design-system, landing-page, mobile-app, slides, table, tailwind v4, web-app | own guide per surface | P4 |
| ☐ | Layer-board agent rules: components top, screens below growing right/down, `clip:true` screens, clean root, no margin/%/baseline/stretch, verify per section, checklist, re-read doc | layer-board charter | P4 |
| ☐ | Style archetypes `get_style` (parameterised fonts/colours/imagery) | `design_get_style` over our own archetype library | P2 |
| ☐ | Parallel agents 1–6: Split Work vs Side by Side, per-agent model, reset to main, per-agent chats; Let it cook (Layout/Style, 2–6 variants, Don't iterate) | N parallel direct sessions | P5 |
| ☐ | MCP: `get_app_state` (doc + selection), `read_skill`, `get_style`, `browser`, `spawn_agents` | `design_get_app_state`, `design_read_skill`, `design_browser`, `design_spawn_agents` | P4 |
| ☐ | `execute` parity: FindEmptySpace, Print, TakeScreenshot/Export in-transaction, Get options (depth, resolveVariables, resolveInstances, includePathGeometry), visitor ctx.bounds/problems/skipChildren, name→id map, warnings, `editId` patch-and-retry, SetVariables(replace), themed values | map onto open-pencil tools / our ops | P4 |
| ☐ | `placeholder:true` on in-progress frames, shown as "working…" | renderer + agent rule | P4 |
| ☐ | Generate: ai image, stock (Unsplash), svg, vectorize, remove/replace background; async pending images; `images/` | choose providers (open-pencil `stock_photo` covers stock) | P6 |
| ☐ | Multiplayer awareness: user edits during agent turn; agent re-reads, never undoes user edits | live op streaming to canvas; rebase/refuse | P4 |
| ☐ | Headless CLI/batch (`--in/--out/--prompt/--agent/--model/--effort/--tasks/--export/...`, interactive shell) | `python -m services.design.cli` / REST | P5 |
| ☐ | One-click MCP registration for Claude Code / Codex / Gemini / Antigravity / OpenCode / Kiro / Claude Desktop | tray button writes client configs | P5 |
| ☐ | Provider matrix + "Re-check connection" | telecode engines + local proxy status | P1 |
| — | IDE extension (VS Code custom editor) | not cloned for now | — |
| ☐ | Welcome sample file | seed sample project | P6 |
| ☐ | Chart guidance (bars via layout, donuts via innerRadius) + table hierarchy | guides; line charts → script node or HTML board | P4 |
| ☐ | Inherited from open-pencil: XPath query, lint, token/cluster analysis, component sets/variants, CSS Grid, JSX/SVG/PPTX/`.fig` export, `.fig` ⇄ `.pen` convert | rewrite layer-board section around it | P4 |

## C. Decisions forced by the one-canvas merge

| Topic | Decision to make |
|---|---|
| `project.mode` | removed ✅ — board type is per node; Convert both ways |
| HTML board ↔ files | board = browser-type node whose `url` is a project-relative entry file |
| `canvas.json` | dropped — the canvas document is the canvas; pages come from open-pencil |
| `design_canvas.jsx` starter | variations become native boards; starter only for exported HTML |
| Decks | Slides panel + Present handle HTML decks (`<section>`s) and frame slides; notes for both |
| Comments | one record `{board_id, anchor:{node_id \| selector \| source_loc}}` |
| Inspector | one Props panel; backend depends on board type |
| Tweaks | per-board toggle; root file = the board's entry file |
| Undo vs versions | unified version timeline; undo on HTML boards = file version steps |
| Code view | HTML board: real sources, editable; layer board: open-pencil Code panel (edit → live preview → commit) |
| Preview view | layer board previews its HTML export |
| Tokens | one source of truth (`tokens.json`) → `tokens.css` + canvas variables; lint both |
| Screenshots/verifier | CDP for HTML boards, open-pencil `export_image` for layer boards |
| Assets review | assets = boards + files, one status model |
| Selection as context | mixed payload: node ids + `<mentioned-element>` blocks |
| Thumbnails, deep links | per board; `?board=` / `?node=` |
| In-artifact AI, device frames, animation | HTML boards only |
| Present | one presenter for both deck kinds |

## D. Under-specified (fix while implementing)

- Team Mode wording in the plan → N parallel **direct task sessions** with a small orchestrator in `generate.py`; Team Mode is only an optional external integration.
- open-pencil is the layer-board engine (P4), not an upgrade path; integration via patched static build.
- Exact `<mentioned-element>` format, comment batching and authors.
- Question-form schema, mandatory options, streaming.
- Versions: restore granularity, compare (diff and side-by-side), interaction with `--resume`.
- Verifier: checks (console, overflow, contrast, hit targets, slide labels), budget, local-model routing, UI states.
- Handoff: active file, per-chat transcripts, full `_ds` manifest.
- Export: PPTX editable details, font swaps, PDF page-size rules.
- Share links: snapshot vs live, role enforcement.
- SSE: op-level live updates for layer boards, not only file notices.
- Cost meter: context breakdown, per-agent usage in parallel runs.
- MCP list: add app-state/selection, skill, style, browser, spawn, copy/delete file, starter, show.
- Telegram: notify-on-done, comment replies mapping.
- Prompt-length fix (stdin) must cover Claude Code, Codex **and** Antigravity (`agy -p`).
