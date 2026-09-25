# TeleDesign — feature parity checklist

Everything Claude Design and pen.dev do that the first plan missed or under-specified. Source: an audit
(2026-09-25) of the Claude Design system prompt + tool schemas, a real handoff bundle (15 chats, `_ds`
package), the Claude Design support + admin articles, every docs.pen.dev page, the pen.dev agent skill
docs, and the open-pencil README. **Parity is the goal, not copying** — no pen.dev / Claude Design code,
binaries, bundled libraries or prompt text is reused.

Phases refer to [teledesign.md](teledesign.md) §6: P1 core loop · P2 design systems · P3 HTML-board
editing/decks/export · P4 layer boards (open-pencil) · P5 agents · P6 polish. Tick items as they land.

Marks: `☑` implemented (→ evidence) · `◐` partial (what's missing) · `☐` missing · `—` deliberately not cloned.

## Coverage summary (code audit, 2026-09-25, at `c0f9811`)

Every mark below was checked against the code (`services/design/**`, `proxy/api_design*.py`,
`proxy/static/design/app/**`, `mcp_server/tools/design.py`, `bot/design_handlers.py`, the vendored
open-pencil v0.15.1 bundle and its 116 tools in `services/design/editor_tools.json`). Where a row was
built differently from what it says, the mark is `☑` and the note says what was decided instead.

| Section | ☑ | ◐ | ☐ | — | Rows |
|---|---|---|---|---|---|
| A. Claude Design | 43 | 17 | 3 | 1 | 64 |
| B. pen.dev | 9 | 36 | 11 | 1 | 57 |
| C. One-canvas decisions | 9 | 8 | 1 | — | 18 |
| D. Under-specified notes | 9 resolved | 5 open | — | — | 14 |

The Claude Design side (A) is close to done. Most of what is left is on the pen.dev canvas (B). Many B
rows are `◐` because open-pencil already covers most of the row, and the missing part is either a
shortcut or a specific node type.

### Genuinely missing: top 20, in priority order (effort S ≤ 1 day · M a few days · L a week or more)

1. **Convert HTML board → layers** (C "Convert", B "Built-in browser import"). No `dom-css` tool is among the 116 editor tools, and the UI has no Convert action. — **M/L**
2. **Verifier for layer boards** (C "Screenshots/verifier"). `generate._verifier` always passes `pen_problems=""`, so it never calls `export_image` or `analyze_*` on frames. — **M**
3. **Tokens ↔ canvas variables sync** (C "Tokens", B "Variables from globals.css"). Tokens become `tokens.css` and `system.lib.pen`, but nothing writes them into `doc.fig` variables, and nothing reads them back. — **M**
4. **Slides panel and Present for frame slides** (B "Presentations", C "Decks"/"Present"). `present.js` presents HTML decks only. — **M**
5. **Directed verifier check and a contrast check** (A "Verifier", D). `verifier_task` is never set. `render.verify` has no contrast rule. — **S**
6. **Live preview console readable via MCP** (A "`get_webview_logs`"). `td:console` is kept only in the UI. `design_get_console` reloads the page headless instead. — **S**
7. **Selection auto-added as chat context** (B "Chat"). Today it needs the manual "Add to chat" in `panels.js`. — **S**
8. **Download cards in chat** (A). There is no agent-emittable download card. — **S**
9. **Share snapshots and expiring public URLs** (B "Sharing snapshots", A "`get_public_file_url`", D). `share.py` tokens are live and never expire. There are no frozen snapshots, no "changes since last shared" and no dependency check. — **M**
10. **`placeholder` "working…" frames** (B). — **S/M**
11. **PPTX options still missing** (A "`gen_pptx`"): `googleFontImports`, `resetTransformSelector`, per-slide `selector/showJs/delay`, `save_to_project_path`. — **S/M**
12. **Missing MCP file tools**: `design_copy_file` / `design_move_file` (copies inherit asset registration) and `design_browser` (A "`write_file`/`copy_files`", B "MCP", D). — **S**
13. **Handoff into a coding session in a chosen repo** (A "Handoff to Claude Code"). Only "Copy handoff prompt" and the bundle exist. — **M**
14. **Preview view of a layer board = its HTML export** (C "Preview view"). — **M**
15. **Undo on HTML boards as version steps** (C "Undo vs versions"). — **S/M**
16. **Permission modes in design turns**, or at least documenting that they are skipped (B "Permission prompts"). — **S**
17. **Headless CLI** over the existing REST API (B). — **S/M**
18. **More MCP registration clients** (Gemini, OpenCode, Kiro, Claude Desktop) and a "Re-check connection" button (B). — **S**
19. **Send-to Google Slides via `gws`** (A "Send-to"). — **M**
20. **open-pencil gaps that need editor patches**: script node, slots, shader fill, mesh gradient, multiple theme axes, git-friendly serialization / more than one doc per project (B). — **L each**; the script node is worth the most.

Also missing but lower priority: a no-JS fallback in standalone HTML (S), `.napkin` saved sketches (S/M), `brandFonts` and per-category `tokens/*.css` in the design-system package (S/M), a welcome sample project (S), and Figma link import (M).

## A. Claude Design

| ✓ | Feature | Add | Phase |
|---|---|---|---|
| ☑ | `done` gate: open file in user's view, return console errors, agent fixes until clean (before verifier) | end-of-turn render → errors → retry loop → `generate._done_gate` (one automatic fix turn per user turn, headless render) | P1 |
| ☑ | Preview **tab bar** of open files; agent opens a file in the user's pane (`show_to_user`) or its own (`show_html`) | tab strip + MCP `design_show(path, target=user\|agent)` → `preview.js drawTabs`, `design_show`, `POST …/show` | P1 |
| ☑ | Files panel: browse / open / delete project files | Files tab → `workspace.js` Files rail (upload, open, delete) | P1 |
| ☑ | Relative links between HTML pages navigate inside the preview | iframe navigation synced with tab bar → `td-bridge.js` `td:navigate` → `interact.js onNavigate` | P1 |
| ◐ | `get_webview_logs` on the user's live preview | console capture on every load, readable via MCP — live `td:console` is shown in the preview console panel only; `design_get_console` reloads the page headless and doesn't read the user's view | P1 |
| ☑ | Scripted screenshots `save_screenshot` (≤100 steps of code+delay, disk or memory, JPEG/HQ PNG, 1600px cap) | steps/hq/save_path on `design_screenshot` → `mcp_server/tools/design.py design_screenshot`, `render.run_steps` | P3 |
| ◐ | `run_script` sandbox helpers (readFile/Binary/Image, saveFile, ls, createCanvas, getCaptures) | CLI shell covers it; pass captures to PPTX builder — CLI shell + `design_eval_js` cover it; the PPTX builder can't take agent captures | P3 |
| ☑ | `image_metadata` (size, alpha, animated frames) | MCP `design_image_metadata` (Pillow) → `design_image_metadata` | P2 |
| ◐ | `write_file`/`copy_files` params `asset`, `subtitle`, `viewport`; copies inherit asset; `move` | auto-register assets on write/copy — `design_write_file` has asset/group/subtitle/viewport, and HTML auto-registers (`assets.register_changed`); no copy or move tool | P1 |
| ☑ | Asset status `needs-review / approved / changes-requested`; re-register resets; unregister by asset/path/both | fix status enum + unregister semantics → `assets.VALID_STATUS`, `design_register_assets(unregister=ids\|paths)` | P1 |
| ☑ | Card groups: Type / Colors / Spacing / Components / Brand (+ custom e.g. Elevation, Radius) | fixed group vocabulary for specimen browser → `systems.js GROUP_ORDER`, `@tdCard group=` | P2 |
| ☑ | 7 starters: design_canvas, ios_frame, android_frame, macos_window, browser_window, animations, deck_stage; `directory` param; wrong extension fails | own versions + `design_copy_starter(kind, dir)` → `services/design/starters/*`, `design_copy_starter` | P3 |
| ☑ | Animation starter API: `<Stage>` (auto-scale, scrubber, play/pause), `<Sprite start end>`, `useTime`/`useSprite`, `Easing`, `interpolate`, entry/exit primitives | spec API + timeline scrubber UI → `starters/animations.jsx` (+ `entryExit`, `window.tdTimeline`) | P3 |
| ☑ | Deck stage: `<deck-stage>` + `<section>`s, 1920×1080 letterbox on black, controls outside scaled area, tap/keys, counter, localStorage position, `noscale` for export, auto `data-screen-label`, posts `{slideIndexChanged}` | full deck contract → `starters/deck_stage.js` (labels are `data-td-screen`; posts `td:slide-changed`, and the host also accepts `slideIndexChanged`; unscaled print replaces `noscale`) | P3 |
| ☑ | Playback position persisted for decks **and videos** | charter rule + starter default → deck: `#slide=N` + localStorage; animation: `#t=` | P3 |
| ☑ | Speaker-notes pane from `<script id="speaker-notes">` JSON, synced by slide index; only when asked | host notes / presenter view; PPTX reads same JSON → `preview.js drawNotes`, `present.js`, `pptx_export` (`td-speaker-notes` / `speaker-notes`) | P3 |
| ☑ | Slide labels 1-indexed ("01 Title") | charter; verifier checks vs counter → `deck_stage.js` auto-labels, `render._verify_file` `slide_label` | P3 |
| ◐ | `gen_pptx`: editable/screenshots, `fontSwaps`, `googleFontImports`, `hideSelectors`, `resetTransformSelector`, per-slide `selector/showJs/delay`, `save_to_project_path`; validation flags `duplicate_adjacent`, `slide_size_mismatch`, `no_speaker_notes` | same options + flags back to agent — `pptx_export.py` has modes, fontSwaps, hideSelectors and all three flags; missing: googleFontImports, resetTransformSelector, per-slide options, save_to_project_path | P3 |
| ◐ | Standalone HTML needs `<template id="__bundler_thumbnail">` splash + no-JS fallback | inliner uses it; doubles as project icon — `export.build_standalone` writes the splash; no `<noscript>` fallback, not used as the icon | P3 |
| ☑ | `open_for_print` (new tab, manual Ctrl+P) | "Open for print" next to CDP PDF → `exporter.js` `print` kind | P3 |
| ☐ | Download cards in chat for file / folder / project (auto-zip) | agent-emittable download cards | P3 |
| ◐ | `get_public_file_url` short-lived public URL | expiring signed URL (for Send-to) — `share.py` has revocable view/comment/edit tokens (`/design/s/{token}/files/…`) that never expire | P6 |
| ◐ | Skill catalog: Animated video, Interactive prototype, Make a deck, Make tweakable, Frontend design, **Wireframe/storyboards**, PPTX editable, PPTX screenshots, Create design system, PDF, Standalone HTML, Send to Canva, Handoff, read_pdf | a prompt file per skill — `prompts/kinds/*` (animation, prototype, slides, wireframe, design_system, landing/web/mobile…) + `tweaks.md`, `code_export.md`; the export tasks are export kinds, not skills; no Send to Canva or read_pdf | P1 |
| ◐ | User-supplied skills pasted/attached in chat | custom SKILL.md library + picker (shared with pen.dev `/` skills) — `data/design/skills/*/SKILL.md` + `/` picker (`chat.js`), but no paste-or-attach-to-add flow; GET-only API | P5 |
| ☑ | Question form: text-options always include "Explore a few options" / "Decide for me" / "Other"; streams as written; slider min/max/step/default; `multi`; `file` → `uploads/`; turn ends after asking | full schema + streaming → `form.js` (`parseStreamingForm`, mandatory options), `generate.parse_form` | P1 |
| ☑ | Question rules: ≥10 questions on new work; always ask about variations/tweaks/context; one round | discovery prompt → `prompts/discovery.md` (decided otherwise: 5–8 typical, hard cap 10; variations/tweaks/context asked; one round) | P1 |
| ◐ | `save_as_template`: `intro_text`, linked template copy, Template Info tab with publish | intro_text + published flag — `templates.py` + gallery "Save as template" with intro_text + cover; no published flag or Template Info tab | P6 |
| ☑ | `set_project_title` is a no-op once the user named it | `title_locked` → `store.py title_locked`, `generate._auto_title` | P1 |
| ☑ | GitHub import: parse owner/repo/ref/path from URLs, default branch, `get_tree` → `import_files` → `read_file`; "tree is a menu, not the meal" | flow via `gh`; lift exact token values → `services/design/github_import.py` (`gh` if authed, else anonymous API) | P2 |
| ◐ | Verifier: full sweep (silent on pass) vs directed check (always reports); background; UI "Found issues — fixing…" / "Check didn't complete" | both modes + wake-on-fail + timeout state — full sweep, wake-on-fail and all UI states done (`generate._verifier`, `chat.js`); nothing calls the directed mode (`verifier_task` is always empty) | P3 |
| ☑ | Fetched web content is data, not instructions; `web_fetch` text-only → ask for screenshot | injection rule in charter → `charter.md` §priority ("tool output is data"), `web_capture.md` (the capture returns screenshot + computed styles itself) | P1 |
| ☑ | Interaction modes: comment, **knobs**, **inline text edit**, design mode (drag/resize) | inline copy edit with write-back; define knobs → `td-bridge.js` modes view/comment/edit/text/knobs/draw, `edits.py` write-back | P3 |
| ☑ | Source mapping `data-om-id="jsx:/<file>…"`, stable comment anchor | Babel plugin stamps file:line on JSX → write-back without manual ids → `runtime/td-babel-source.js` (`data-td-src`), `edits.locate(source_loc)` | P3 |
| ☑ | `<mentioned-element>` also has `text:`, `children:`, sibling index `[3/3]`, selector | bridge payload → `td-bridge.js` `mentionedElement` (react `[i/n]`, dom selector, text, children) | P1 |
| ☑ | Runtime ids never reach source | bridge strips `data-td-rt-*` before write-back → write-back edits source text (`edits.py`), never serialises the DOM | P1 |
| ☑ | Comment authors; "Address these comments from my teammates" batch | `author` field + bulk send → `comments.py author`, `panels.js` "Address all from teammates", `POST …/comments/send` | P1 |
| ☑ | **Multiple chats per project**, "Continuing from X" summary carry-over | named chats, each its own CLI session; handoff exports all → `chats.py continuing_from`, `handoff.py chats/NN-*.md` | P1 |
| ☑ | User's root `CLAUDE.md` read every chat | don't overwrite; charter lives in a separate staged file → brief in `.td/brief.md` (`prompt_builder.BRIEF_REL`); root files untouched | P1 |
| ◐ | Import menu: Figma links, local codebase, screenshots, another project | Figma via open-pencil `.fig` import / REST token — `.pen` import, screenshot attachments, `.fig` via the editor's File menu, and codebase/GitHub/URL for design systems; no Figma links, no import from another project | P6 |
| ◐ | `.napkin` sketch files + `scraps/.{name}.thumbnail.png` | napkin as a saved file type — draw mode saves a PNG sketch and attaches it (`interact.onDraw`); `.napkin` is recognised as a file type but never produced | P3 |
| ☑ | `window.claude.complete(string \| {messages})` | alias to telecode shim so imported CD projects run; cap + rate limit → `runtime/td-telecode.js`, `interact.js` (30/min, 2 in flight, local model) | P3 |
| ☑ | Craft rules: descriptive filenames, v2 copies, no `scrollIntoView`, placeholder over bad attempt, oklch palette extension, emoji only if brand uses it, no title screens, ≥24px slide text, 12pt print, 44px hit targets, 1–2 deck backgrounds, 3+ variations, default tweaks, ask before adding content, anti-slop, `text-wrap: pretty` | `prompts/craft/`; lint measurable ones → `prompts/craft/*.md`; `render.verify` checks 24px slide text, 44px hit targets and overflow | P1 |
| ☑ | Babel pitfalls: no bare `const styles`, no `type="module"`, window export | lint `no-generic-styles-const` → `lint.py` `babel-generic-styles-const`, `babel-type-module` | P1 |
| ☑ | Canvas-of-options vs clickable prototype rule | merged: "N boards vs 1 board with tweaks" → `charter.md` (Tweaks for interactive, labelled boards for static) | P1 |
| ☑ | Copy DS assets selectively (no bulk >20 files) | staging rule → `systems.STAGE_EXTRA_CAP = 20`, `charter.md` | P2 |
| ☑ | Copyright guard exception for the user's own company domain | configurable own-brand domains → `design.own_domains` → `{{user_org_domain}}` in `charter.md` §11 | P1 |
| ☑ | Handoff README names the file open at handoff, chat count, "read source, don't screenshot", "ask before implementing" | record active file/state → `prompts/handoff_README.md` (`primary_file`, `chat_count`), `handoff.py` | P3 |
| ◐ | DS manifest superset: `namespace`, `components[name,sourcePath]`, `startingPoints[]`, `tokens[name,value,kind,definedIn]`, `fonts[family,weight,style,cssPath,files,remoteSrc]`, `brandFonts[status,tokens]`, `source` | extend manifest.json — `systems.py` reads namespace/components/startingPoints/tokens/fonts/source; no `brandFonts`, and fonts lack `style`/`remoteSrc` | P2 |
| ◐ | DS layout: per-category `tokens/*.css`, root `styles.css` @imports, `guidelines/` cards, `components/`, `ui_kits/`, `assets/`, SKILL.md packaging | add these — import and staging handle `tokens/*.css`, `styles.css`, `ui_kits/`, `assets/` and `SKILL.md` (`systems.py`); the seeds and our own output use a single `tokens.css` and no `ui_kits/` | P2 |
| ◐ | DS readme: Sources, Company/Product context, Known gaps/substitutions, Iconography (+ substitution rationale), visual foundations (hover/press, borders, shadows, radii, cards, transparency, imagery, layout), Index, Intentional additions | DESIGN.md template — `kinds/design_system.md` has 12 sections incl. Known gaps + font substitutions, Iconography, Imagery; missing Sources, Index, Intentional additions (only in the Remix prompt), hover/press/transparency | P2 |
| ☑ | Adherence: hex/px/font **plus** `no-restricted-imports`, `react/forbid-elements`, per-component prop allowlists | all four families; pick linter → `services/design/lint.py` (own regex linter, `adherence.json`) | P2 |
| ☑ | `_ds_bundle.js`: `@ds-bundle` header, namespace global, 12-char source hashes, rebuild on change | header + staleness check → `ds_bundle.py` (`hash12`, `status().stale`) | P2 |
| ☑ | DS lifecycle: Open / Remix / "Let Claude clean it up" / validate with a test project / delete / multiple per brand | clean-up pass + "Try it" scratch project → `api_design_systems.py` remix / cleanup / try / publish / default / delete | P2 |
| ☑ | `/design-sync` from Claude Code | shipped MCP prompt/skill for external CLIs → `design_sync_prompt` (MCP prompt) | P5 |
| ☑ | `/design` entry inside Claude Code / chat | MCP prompt that creates a project from any CLI → `design_prompt`, `design_create_project` | P5 |
| ☑ | Viewport / device switcher + zoom in preview | toolbar presets → `preview.js` DEVICES + zoom | P1 |
| ☑ | Stop / cancel generation | UI Stop → terminate task → `POST …/chats/{id}/stop` → `generate.stop_turn` | P1 |
| ☐ | Send-to partners + **Google Slides** | deferred; Google Slides via `gws` CLI first | P6 |
| ◐ | Handoff to Claude Code Web vs local | "send to a direct task session in a chosen repo" — handoff bundle + "Copy handoff prompt" only (`exporter.js`); no send-to-session | P5 |
| ☐ | Simultaneous multi-person editing | later: open-pencil Yjs collab | P6 |
| ◐ | Previews on a **separate origin**, signed per-session tokens | second port/origin for previews, not only CSP — `preview.py` on `design.preview_port` + CSP + Origin guard; no signed per-session tokens | P1 |
| — | Admin roles / capability gating / phased rollout | not cloned (single user) | — |
| ☑ | Mobile view-only | Telegram screenshots → `bot/design_handlers.py` (`/design`, photo on done) + phone/tablet layouts (`workspace.js`) | P5 |
| ☑ | Chat transcript format (`_Started <ts>_`, `## User`/`## Assistant`, `_[tool: x]_`) | match for handoff; per-chat files → `chats.render_transcript` | P3 |

## B. pen.dev

| ✓ | Feature | Add | Phase |
|---|---|---|---|
| ◐ | Full keyboard shortcut map + `?` overlay (tools, editing, arrange, selection, components, nav, path editing, settings, file, chat) | shortcut sheet — `app/shortcuts.js` `?` sheet (TeleDesign keys) + the editor's own keymap and command palette; no single merged sheet | P4 |
| ◐ | Tools: V move, K scale, H hand, R, O, A/F frame, T, N sticky, P pen, hold Z zoom; Primitives (polygon, icon, script) | toolbar spec — editor has V/F/R/O/S/P/T/H, polygon/star; missing K scale, N sticky, hold-Z, icon/script primitives | P4 |
| ◐ | Pen tool + path point editing (cut X, smooth/corner, connect Cmd+J, symmetric/broken handles, bend/straighten, Alt+click insert) | open-pencil vector editor — P pen, "Edit vector", handle mirroring, bend; tools `path_*`, `create_vector`; cut X / Cmd+J / Alt-click insert not found | P4 |
| ☑ | Frames: wrap (Cmd+Alt+G), group/ungroup, Clip Content, fixed W/H | commands + props → editor `frameSelection` Mod+Alt+G, group/ungroup, `clipContent` | P4 |
| ☑ | Flex UI: Shift+A add / Shift+Alt+A remove, alignment grid, space-between/around, Hug/Fill, Absolute Position, arrow-key reorder | layout section of Props → editor `wrapInAutoLayout` Shift+A, space-between, Hug/Fill; `set_layout`/`set_layout_child` (remove-shortcut / arrow reorder unverified) | P4 |
| ◐ | Selection: blue / magenta (origin) / violet (instance), deep select, Enter children, Shift+Enter parent, Esc, marquee | spec — marquee + component colours; no Enter / Shift+Enter child/parent commands | P4 |
| ◐ | Layers: rename, drag reorder/reparent, eye = `enabled`, moves blocked inside instances | spec — rename Mod+R, visibility, lock, drag; blocking moves inside instances unverified | P4 |
| ◐ | Props: per-corner radius, opacity, multiple fills with eye/remove, linear/radial/angular/**mesh** gradients with handles, image fill fill/fit/stretch (file/path/URL), blend modes, stroke width/align/cap/join, drop/inner shadow, layer + background blur, non-flex alignment | enumerate — all present in the editor (linear/radial/angular/diamond, 16 blends, effects) except mesh gradients | P4 |
| ☐ | Shader fill (WebGL1, `@color @default @resolution @mouse @time @sdf @backdrop @min/@max/@range @label` → controls; gallery) | render + uniform controls + own gallery | P6 |
| ◐ | Text: textGrowth auto/fixed-width/fixed-width-height, typography, underline/strike/**href**, font search, all Google fonts, custom fonts per doc (ttf/otf/woff/woff2), missing-font warning | spec — auto-resize, decoration, hyperlink, Google/Fontsource/Bunny providers, local fonts, missing-font banner; no per-doc custom font upload | P4 |
| ◐ | Icons: Material Symbols Outlined/Rounded/Sharp, Lucide, Feather, Phosphor; variable weight; picker | all five libraries — Iconify via `search_icons`/`fetch_icons`/`insert_icon` (all five sets reachable); no variable weight, no UI picker (agent-only) | P4 |
| ◐ | Geometry: rotation (CCW, top-left), flipX/Y (Shift+H/V), ellipse innerRadius/startAngle/sweepAngle, polygonCount | renderer + props — rotation, flip Shift+H/V, point count; arc/innerRadius in the model but no props controls | P4 |
| ☑ | Arrange: align (Alt+W/A/S/D/H/V), z-order (Cmd+[ ], [ ]) | spec → editor keymap (Alt+W/A/S/D/H/V, Mod+[ ], [ ]) | P4 |
| ◐ | Clipboard: copy/paste, Copy as PNG, Copy as HTML, duplicate, cut, nudge 1/10px | spec — copy/cut/paste/duplicate, Copy as PNG/SVG/JSX; no Copy as HTML | P4 |
| ◐ | Navigation: space-drag, middle mouse, shift+scroll, zoom in/out/100%/fit (Shift+1)/selection (Shift+2), Z-drag region | spec — all present except Z-drag region zoom | P4 |
| ◐ | Settings: pixel grid, snap to pixel/objects, wheel zoom, invert zoom, animations off, light/dark UI (local, not in doc) | preferences — snap to pixel/objects, rulers, animations off, light/dark; no wheel-zoom / invert-zoom options | P4 |
| ☐ | Node types `script`, `browser`; UX for `note`/`prompt`(model)/`context`; entity `context`, `metadata`, `enabled`, `theme` | add to data model; prompt node runs a turn — only the HTML board (`telecode_board_*`) stands in for `browser` | P4 |
| ☐ | Script node / code on canvas: `.js` beside doc, `@schema`/`@input` (number min/max, string, boolean, color, enum, ref picker), returns nodes, ≤1000 nodes, ≤2s, no DOM/net, seeded random, file watch, errors on node, bind inputs to `$vars`, Convert to layers | Worker/QuickJS sandbox | P6 |
| ◐ | Browser node (`url`, `deviceId`, `zoom`, `scrollX/Y`, `cornerRadius`) | **= the HTML board**; `url` points at a project file — frame + board key + live iframe (`canvas.js`, `telecode_board_mark`, patch 0006); no deviceId/zoom/scroll props | P4 |
| ◐ | Components: create/convert back (Cmd+Alt+K), detach (Cmd+Alt+X), replace inside instance (Alt+Shift+R), go to component / library source, Components panel (search, drag insert), nested, overrides survive origin edits, delete = `enabled:false`, detach keeps nested links | Components panel + commands — create Mod+Alt+K, detach (Mod+Alt+B), go to main, local component search, variants, instance swap, overrides; no Alt+Shift+R replace, delete ≠ `enabled:false` | P4 |
| ☐ | Slots: empty frames in origins, hatching, suggested components (`slot:[ids]`), non-restrictive | spec — only a Figma schema field (`isSlot`), no feature | P4 |
| ◐ | Variables: boolean (visibility), add/rename/duplicate/delete, "Apply variable" by type, aliases, multiple theme axes, per-frame theme + inheritance + remove, first value default | Variables panel — collections + modes, apply/detach, boolean, aliases, explicit modes per frame; no multiple theme axes | P4 |
| ☐ | Variables from `globals.css` and from a screenshot; two-way CSS sync | agent recipe + deterministic tokens.css ↔ variables — `brand_extract.py` reads CSS vars into a design system, never into canvas variables | P4 |
| ◐ | Libraries: `.lib.pen`, "turn into library" (irreversible), Libraries tab, Locate missing, reload on reopen | library management — editor publish/browse/update libraries; `.lib.pen` files not loaded as libraries, no Locate missing | P4 |
| ◐ | Bundled default libraries | ship our own `.lib.pen` seeds only (licence) — seeds ship `system.lib.pen`; not auto-loaded into the editor | P4 |
| ☐ | Paste between docs: import-library vs verbatim; variable conflict dialog (Use existing / Add renamed) | paste semantics | P4 |
| ◐ | Presentations: Slides panel (drag-reorder thumbnails), Present Cmd+Enter, Space/Enter/arrows/Esc, export all/selected, PDF page = frame size | Slides panel — `present.js` presents HTML decks; PPTX/PDF export; no Slides panel over frames, no Cmd+Enter | P4 |
| ☑ | Import: `.fig`, Figma copy-paste, image drag/paste, SVG → layers | via open-pencil → editor `.fig`/`.pen` open, `import_svg`, image paste; `POST …/import/pen` (Figma clipboard untested) | P4 |
| ◐ | Built-in browser import: page or picked element → layers, localhost, device/zoom presets, screenshots (element/full/visible) | open-pencil `dom-css` + capture = HTML→layers Convert — `web_capture.md` + `brand_extract.py` capture pages; no element picker, no HTML→layers Convert | P4 |
| ◐ | Export: PNG/JPEG/WEBP/PDF 1/2/3×, quality, multi-select → zip, PDF page order, HTML options (assets, scaffold, layer names/ids), Copy HTML | spec — editor PNG/JPG/WEBP/SVG/PDF + scale, `.fig`, PPTX; `export.py` png/jpeg/webp 1–3× + quality; no layer HTML options or Copy HTML | P4 |
| ◐ | Sharing snapshots: create, update ("changes since last shared"), missing-dependency check, revoke, recipient ZIP download | snapshot model + dependency check — `share.py` live tokens (view/comment/edit) + revoke; no frozen snapshot, diff, dependency check, ZIP | P6 |
| ◐ | Files: several docs per project / any `.pen` in a repo, drafts autosave, Save/Save As, recovery backup, external-change detection (Reload/Ignore) | repo-path docs + watcher + recovery — editor autosave, Save/Save As, recovery; one `doc.fig` per project, no repo `.pen` docs, no external-change prompt | P4 |
| ◐ | Dashboard: Recents, Drafts, Design Systems, list/grid, sort, multi-select | project gallery — `gallery.js` grid/list, sort, multi-select, systems + templates tabs; no Recents / Drafts views | P6 |
| ☐ | Git-friendly serialization (stable key order/format) | deterministic serializer — `doc.fig` is binary kiwi | P4 |
| ◐ | Chat: Cmd+K toggle, agent tabs (Cmd+T, Ctrl+Tab), ↑/↓ history, **selection auto-added as context**, attach image/text | spec — chat tabs, ↑ history, attachments, selection chip; selection needs manual "Add to chat", no Cmd+T / Ctrl+Tab | P4 |
| ☑ | Session history, delete session, model + reasoning-effort picker per request, "switching providers loses context" | engine + effort picker per turn → `chat.js` chats (new/rename/continue/delete), engine/model/effort picker, "switching engines starts a fresh session" | P1 |
| ◐ | Stop all / Stop current; completed changes stay | per-agent Stop — chat Stop (Mod+.) and `parallel.stop_run`; no single Stop-all | P1 |
| ◐ | Background-finish notifications | Telegram / tray notify — Telegram notify-on-done (`design.telegram_notify`); no tray/browser notification | P5 |
| ☐ | Permission prompts (Allow/Deny/Auto/skip) | document: handlers skip permissions — neither documented nor wired to the new engine permission modes | P1 |
| ☑ | Context usage + cost breakdown; tool failure "Show details" | cost meter + context breakdown → `chat.js` cost/context meter (input/output/cache vs context window), "Show details" | P1 |
| ◐ | `/` skill picker, add/remove/reload SKILL.md | custom skill library — `/` picker + `GET /api/design/skills` (user dir); no add/remove/reload UI | P5 |
| ◐ | Agent guides: code, components, design-system, landing-page, mobile-app, slides, table, tailwind v4, web-app | own guide per surface — `prompts/kinds/*` + `code_export.md`; no components or tailwind-v4 guide | P4 |
| ◐ | Layer-board agent rules: components top, screens below growing right/down, `clip:true` screens, clean root, no margin/%/baseline/stretch, verify per section, checklist, re-read doc | layer-board charter — `layer_boards.md` covers components row, no margin, verify per board; no growth direction, `clip:true` rule or checklist | P4 |
| ☑ | Style archetypes `get_style` (parameterised fonts/colours/imagery) | `design_get_style` over our own archetype library → `design_get_style`, `seeds/styles/` | P2 |
| ◐ | Parallel agents 1–6: Split Work vs Side by Side, per-agent model, reset to main, per-agent chats; Let it cook (Layout/Style, 2–6 variants, Don't iterate) | N parallel direct sessions — `parallel.py` split / side_by_side / let_it_cook / jury, per-agent engine, `max_agents`; no "reset to main" | P5 |
| ◐ | MCP: `get_app_state` (doc + selection), `read_skill`, `get_style`, `browser`, `spawn_agents` | `design_get_app_state`, `design_read_skill`, `design_browser`, `design_spawn_agents` — all present except `design_browser` | P4 |
| ◐ | `execute` parity: FindEmptySpace, Print, TakeScreenshot/Export in-transaction, Get options (depth, resolveVariables, resolveInstances, includePathGeometry), visitor ctx.bounds/problems/skipChildren, name→id map, warnings, `editId` patch-and-retry, SetVariables(replace), themed values | map onto open-pencil tools / our ops — editor `eval` (plugin API), `query_nodes`, `batch_update`, `export_image`; no FindEmptySpace, visitor ctx, editId retry | P4 |
| ☐ | `placeholder:true` on in-progress frames, shown as "working…" | renderer + agent rule | P4 |
| ◐ | Generate: ai image, stock (Unsplash), svg, vectorize, remove/replace background; async pending images; `images/` | choose providers (open-pencil `stock_photo` covers stock) — `stock_photo` + editor vectorize; no AI image, background removal, pending images | P6 |
| ◐ | Multiplayer awareness: user edits during agent turn; agent re-reads, never undoes user edits | live op streaming to canvas; rebase/refuse — agent ops land live in the user's editor via `editor_bridge.py`; "re-read, keep the user's changes" is a prompt rule only (`canvas.md`) | P4 |
| ☐ | Headless CLI/batch (`--in/--out/--prompt/--agent/--model/--effort/--tasks/--export/...`, interactive shell) | `python -m services.design.cli` / REST — REST + MCP only | P5 |
| ◐ | One-click MCP registration for Claude Code / Codex / Gemini / Antigravity / OpenCode / Kiro / Claude Desktop | tray button writes client configs — `mcp_registration.py` + `POST /api/design/mcp/register` for Claude Code / Codex / Antigravity only | P5 |
| ◐ | Provider matrix + "Re-check connection" | telecode engines + local proxy status — `GET /api/design/engines` (available/version/local) in the picker; no Re-check button | P1 |
| — | IDE extension (VS Code custom editor) | not cloned for now | — |
| ☐ | Welcome sample file | seed sample project | P6 |
| ☑ | Chart guidance (bars via layout, donuts via innerRadius) + table hierarchy | guides; line charts → script node or HTML board → `layer_boards.md` §6, `kinds/dashboard_table.md` | P4 |
| ☑ | Inherited from open-pencil: XPath query, lint, token/cluster analysis, component sets/variants, CSS Grid, JSX/SVG/PPTX/`.fig` export, `.fig` ⇄ `.pen` convert | rewrite layer-board section around it → `editor_tools.json` (`query_nodes`, `analyze_*`, `combine_as_variants`, `get_jsx`, `export_svg`…), `layer_boards.md` | P4 |

## C. Decisions forced by the one-canvas merge

| ✓ | Topic | Decision to make |
|---|---|---|
| ☑ | `project.mode` | removed ✅ — board type is per node; Convert both ways → no `mode` in `store.py`. (Convert HTML→layers itself is missing; see B "Built-in browser import") |
| ☑ | HTML board ↔ files | board = browser-type node whose `url` is a project-relative entry file → decided otherwise: a normal frame, registered in `boards.json` under a board key kept inside the frame (patch 0006, `telecode_board_mark`), overlaid by `canvas.js` |
| ☑ | `canvas.json` | dropped — the canvas document is the canvas; pages come from open-pencil → `doc.fig` via the telecode StorageAdapter (patch 0005) |
| ☑ | `design_canvas.jsx` starter | variations become native boards; starter only for exported HTML → `charter.md` (labelled boards in a row); starter kept in `starters/` |
| ◐ | Decks | Slides panel + Present handle HTML decks (`<section>`s) and frame slides; notes for both — HTML decks only (`present.js`, notes); no Slides panel, no frame slides |
| ☑ | Comments | one record `{board_id, anchor:{node_id \| selector \| source_loc}}` → `comments.py _anchor` |
| ◐ | Inspector | one Props panel; backend depends on board type — two panels: TeleDesign Inspect for HTML (`panels.js renderInspect`), editor Props for layers |
| ☑ | Tweaks | per-board toggle; root file = the board's entry file → `preview.js` Tweaks button per file, `tweaks.md` write-back |
| ◐ | Undo vs versions | unified version timeline; undo on HTML boards = file version steps — one timeline incl. `doc.fig` (`versions.py`, restore whole or per file); no undo bound to version steps |
| ◐ | Code view | HTML board: real sources, editable; layer board: open-pencil Code panel (edit → live preview → commit) — HTML ☑ (`code.js` editable); layer boards rely on the editor's own Code panel / `get_jsx`, and editing there doesn't commit anything back |
| ☐ | Preview view | layer board previews its HTML export |
| ◐ | Tokens | one source of truth (`tokens.json`) → `tokens.css` + canvas variables; lint both — `tokens.json` → `tokens.css` + `system.lib.pen`; `lint.py` lints HTML/CSS/JSX and `.pen`; nothing writes to `doc.fig` variables |
| ◐ | Screenshots/verifier | CDP for HTML boards, open-pencil `export_image` for layer boards — CDP ☑ (`render.py`); the verifier never inspects layer boards (`pen_problems=""`) |
| ☑ | Assets review | assets = boards + files, one status model → `assets.py` (`board_id`, one status enum) |
| ☑ | Selection as context | mixed payload: node ids + `<mentioned-element>` blocks → `panels.js` (editor node ids → `<mentioned-element>`), `interact.js`, `design_get_app_state` |
| ◐ | Thumbnails, deep links | per board; `?board=` / `?node=` — deep links ☑ (`state.js`); thumbnail is per project, not per board |
| ☑ | In-artifact AI, device frames, animation | HTML boards only → `td-telecode.js`, device starters, `animations.jsx` |
| ◐ | Present | one presenter for both deck kinds — HTML decks only |

## D. Under-specified (fix while implementing)

- ☑ Team Mode wording in the plan → N parallel **direct task sessions** with a small orchestrator in `generate.py`; Team Mode is only an optional external integration. → `services/design/parallel.py`
- ☑ open-pencil is the layer-board engine (P4), not an upgrade path; integration via patched static build. → `patches/open-pencil/0001–0007`, `proxy/static/design/editor/`
- ☑ Exact `<mentioned-element>` format, comment batching and authors. → `td-bridge.js`, `comments.py`, `POST …/comments/send`
- ☑ Question-form schema, mandatory options, streaming. → `form.js`, `generate.parse_form`
- ◐ Versions: restore granularity, compare (diff and side-by-side), interaction with `--resume`. → restore whole/per-file + side-by-side/diff done (`versions.py`, `panels.compareDialog`); open: the resumed session is never told that files were restored
- ◐ Verifier: checks (console, overflow, contrast, hit targets, slide labels), budget, local-model routing, UI states. → console, overflow, hit targets, slide labels, 24px text, local routing (`design.verifier.is_local`) and UI states all done; open: contrast check, directed mode
- ☑ Handoff: active file, per-chat transcripts, full `_ds` manifest. → `handoff.py`, `handoff_README.md`
- ☑ Export: PPTX editable details, font swaps, PDF page-size rules. → `pptx_export.py`, `export.py` (`@page` per deck, `page_size`)
- ◐ Share links: snapshot vs live, role enforcement. → live links with view/comment/edit roles (`share.py`); snapshots not built
- ☑ SSE: op-level live updates for layer boards, not only file notices. → resolved differently: agent ops run in the user's live editor over `editor_bridge.py`, so there is no op stream to send
- ☑ Cost meter: context breakdown, per-agent usage in parallel runs. → `chat.js` meter; `parallel.add_usage` per agent
- ◐ MCP list: add app-state/selection, skill, style, browser, spawn, copy/delete file, starter, show. → all present except `design_browser` and a copy-file tool
- ◐ Telegram: notify-on-done, comment replies mapping. → notify-on-done ☑ (`bot/design_handlers.py`); open: mapping Telegram replies onto comments
- ☑ Prompt-length fix (stdin) must cover Claude Code, Codex **and** Antigravity (`agy -p`). → `services/engine/adapters/*` (all three on stdin)
