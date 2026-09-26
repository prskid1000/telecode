# TeleDesign prompt set

The single prompt source for TeleDesign design turns (`services/design/generate.py`). Every file here
is Markdown with `{{placeholders}}` that the composer fills before the text goes to the CLI
(Claude Code / Codex / Antigravity). Nothing else in the codebase should carry design-prompt prose.

Licensing: original text, except the files that carry the header
"Portions adapted from nexu-io/open-design (Apache-2.0)" — see [NOTICE](NOTICE).

---

## 1. Composition order (a design turn)

The stack is ordered **stable → volatile** so the prefix stays byte-identical across turns of the
same project and the prompt cache (and llama.cpp's prefix cache, for local runs) survives. Anything
that changes per turn goes last.

| # | Layer | File(s) | Included when |
|---|---|---|---|
| 1 | Discovery | `discovery.md` | The turn starts a new brief: first turn of a project, or the user asked for something new. **Omit** on comment-scoped turns, tweak-only turns and turns that begin with `<form-answers>` (answers are then already in the turn context). The *Direction picker* section renders only when no design system is attached. |
| 2 | Charter | `charter.md` | Always. |
| 3 | Canvas charter | `canvas.md` | Always. |
| 3a | Board rules | `html_boards.md` | The project has, or this turn may create, an HTML board (default for prototype, slides, one_pager, animation, landing_page, mobile_app, web_app). |
| 3b | Board rules | `layer_boards.md` | The project has, or this turn may create, a layer board (default for wireframe, dashboard_table, design_system, and any turn that converts). Include both 3a and 3b on a Convert turn. |
| 4 | Kind skill | `kinds/<project_kind>.md` | Always — exactly one, from `{{project_kind}}`. |
| 4a | Kind companions | `deck.md`, `tweaks.md`, `code_export.md` | `deck.md` for `slides` (and any deck board); `tweaks.md` for any HTML board that is interactive or asks for variants; `code_export.md` on "Convert → code" / "Export React" turns. |
| 5 | Design system | rendered `{{design_system}}` block (see §3) | A system is attached to the project. Order inside: USAGE.md → DESIGN.md → tokens.css → manifest.json → file index. |
| 6 | Craft | `craft/*.md` | Per the table in §2. |
| 7 | Turn context | rendered blocks (see §4) | Per turn: project facts → canvas summary → references → attachments → web capture (`web_capture.md` + payload) → `<mentioned-element>` → `<attached-comments>` (from `comments.md`) → `<form-answers>` → the user's message. |

Prompts that are **not** part of the stack — each is its own call with its own inputs:

| File | Used by | Model |
|---|---|---|
| `verifier.md` | Post-turn verification pass (`render.py` screenshots + console + lint) | local by default |
| `critique.md` | Design Jury / explicit "critique this" | local by default |
| `title.md` | Auto project title after the first turn | smallest local model |
| `handoff_README.md` | Not a prompt — the `README.md` written into the handoff bundle by `export.py` | — |
| `web_capture.md` | Stacked at layer 7 only when a capture payload is attached | — |

---

## 2. Craft rules per kind

`anti_slop.md` and `accessibility.md` are always included. The rest:

| Kind | typography | color | layout_spacing | motion | copywriting | imagery_icons |
|---|---|---|---|---|---|---|
| prototype | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| slides | ✓ | ✓ | ✓ | – | ✓ | ✓ |
| wireframe | – | – | ✓ | – | ✓ | – |
| one_pager | ✓ | ✓ | ✓ | – | ✓ | ✓ |
| animation | ✓ | ✓ | – | ✓ | – | ✓ |
| landing_page | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| mobile_app | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| web_app | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| dashboard_table | ✓ | ✓ | ✓ | – | ✓ | – |
| design_system | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

`store.VALID_KINDS` currently lists `prototype, slides, wireframe, one_pager, other`. `other` maps
to `kinds/prototype.md`. The remaining kinds (`animation`, `landing_page`, `mobile_app`, `web_app`,
`dashboard_table`, `design_system`) need adding to `VALID_KINDS` before they can be selected.

---

## 3. The design-system block

Rendered by the composer when `project.design_system_id` is set. The system is copied read-only into
the project at `_ds/<slug>/` before the turn, so paths inside it resolve from the agent's cwd.

```
## Design system: {{design_system_name}}  (read-only copy at {{ds_path}})

### How to use it
{{ds_usage}}

### DESIGN.md
{{ds_design_md}}

### Tokens (tokens.css)
{{ds_tokens_css}}

### Manifest (manifest.json)
{{ds_manifest}}

### Files you can copy from
{{ds_file_index}}
```

`{{ds_file_index}}` is one line per file: `path · kind · bytes` (components, cards, ui_kits, fonts,
assets). Long systems: the composer may truncate DESIGN.md sections to headings plus the first
paragraph and add `(full text: {{ds_path}}/DESIGN.md)`; the agent is told to read it.

---

## 4. Turn-context blocks

```
## This project
- Title: {{project_title}} · Kind: {{project_kind}} · Turn: {{turn_number}}
- Engine: {{engine}} · Local model: {{is_local}}
- Today: {{today}} · User org domain: {{user_org_domain}}

## Canvas right now
{{canvas_summary}}
```

`{{canvas_summary}}` — one line per top-level node of the canvas (the open `docs/<id>.fig`, read through the editor):
`node_id · type (frame|component|…) · name · x,y · width×height · board key + src (HTML boards)` plus
the currently focused board (`{{active_board}}`) and open deep link (`{{deep_link}}`).

Then, each only if non-empty: `{{references}}` (read-only `@project/<id>/<path>` files staged via
`--add-dir`), `{{attachments}}` (uploads with project-relative paths), `{{web_capture}}`,
`{{mentioned_element}}`, `{{comments}}` (rendered with `comments.md`), `{{form_answers}}`, and
finally `{{user_message}}`.

---

## 5. Placeholder reference

Placeholders use `{{name}}`. A section wrapped in `{{#name}} … {{/name}}` renders only when `name` is
non-empty; `{{^name}} … {{/name}}` renders only when it is empty. Unknown placeholders are a
composer bug — the composer must fail loudly rather than send literal braces.

| Placeholder | Value | Used in |
|---|---|---|
| `project_title` | Project title (may be "Untitled") | turn context, handoff_README, title |
| `project_kind` | One of the kinds above | turn context, README routing |
| `project_id` | 32-hex id | handoff_README |
| `turn_number` | 1-based turn counter | turn context |
| `engine` | `claude_code` / `codex` / `antigravity` | turn context |
| `is_local` | `true` when routed to the local llama.cpp model | turn context |
| `today` | ISO date | turn context |
| `user_org_domain` | Domain of the user's configured email, or empty | charter (brand rule) |
| `no_design_system` | `"1"` when no system is attached, else empty | discovery (direction picker) |
| `design_system` | The whole rendered block of §3, or empty | layer 5 |
| `design_system_name`, `ds_path`, `ds_usage`, `ds_design_md`, `ds_tokens_css`, `ds_manifest`, `ds_file_index` | Parts of the block | §3 |
| `ds_bundle` | Path of the compiled component bundle (`_ds_bundle.js`) or empty | html_boards |
| `canvas_summary`, `active_board`, `deep_link` | Canvas state | turn context |
| `references` | Cross-project reference list | turn context |
| `attachments` | Upload list | turn context |
| `web_capture` | Capture payload (see `web_capture.md`) | turn context |
| `mentioned_element` | Rendered `<mentioned-element>` block(s) | turn context |
| `comments` | Rendered `<attached-comments>` block | turn context |
| `form_answers` | `<form-answers>` block from the question form | turn context |
| `user_message` | The user's text | turn context |
| `starters` | Table of starter files available this turn (`name · path · load as`) | html_boards |
| `sri_react`, `sri_react_dom`, `sri_babel` | `sha384-…` integrity strings for the pinned UMD builds | html_boards |
| `allowed_cdns` | Extra script origins the artifact CSP allows, or empty | html_boards |
| `ai_helper` | `"1"` when `window.telecode.complete()` is enabled for this project | charter |
| `ai_helper_limits` | Human line, e.g. "local model, 1024 output tokens, 20 calls/min" | charter |
| `image_tools` | Description of image-generation tools available this turn, or empty | charter, imagery_icons |
| `verifier_task` | Directed-check instruction, or empty for a full sweep | verifier |
| `console_log`, `screenshots`, `lint_findings`, `pen_problems`, `files_changed` | Verifier inputs | verifier |
| `critic_role`, `artifact_ref`, `brief`, `round`, `previous_scores` | Critique inputs | critique |
| `first_message`, `first_reply_excerpt` | Title inputs | title |
| `chat_count`, `primary_file`, `primary_board`, `bundle_tree`, `ds_note`, `exported_at` | Handoff bundle | handoff_README |
| `count`, `comment_id`, `board_id`, `board_name`, `file`, `slide_label`, `note`, `element_id`, `selector`, `x`/`y`/`w`/`h`, `text_hint`, `style` | Per-comment fields, local to the `comments.md` Part B template (repeated per comment) | comments |

---

## 6. Protocol identifiers (stable API — do not rename)

| Identifier | Meaning |
|---|---|
| `data-td-id="kebab-id"` | Source-level element anchor in HTML boards |
| `data-td-screen="03 Checkout"` | Screen / slide label, 1-indexed |
| `data-td-live="n"` | Runtime-only handle stamped by the bridge; never in source |
| `td:*` postMessage types | Iframe ↔ host bridge (`td:comment-target`, `td:apply-style`, `td:slide`, `td:slide-changed`, `td:complete`, `td:ready`) |
| `__edit_mode_available` / `__activate_edit_mode` / `__deactivate_edit_mode` / `__edit_mode_set_keys` / `__edit_mode_dismissed` | Tweaks protocol (kept for Claude Design import compatibility) |
| `/*EDITMODE-BEGIN*/{…}/*EDITMODE-END*/` | Persisted tweak defaults |
| `data-td-deck`, `#deck-stage`, `data-td-slide` | Deck contract |
| `#td-speaker-notes` | Speaker-notes JSON script |
| `<!-- @tdCard group="…" -->` | Design-system specimen card marker |
| `<question-form>` / `<form-answers>` | Clarifying-question round trip |
| `<attached-comments>` / `<mentioned-element>` | Scoped-edit context |
| `boards.json` `{board_key: {src, width, height}}` | HTML board registry. The board key is stored inside the frame in its canvas document (node ids change on every reopen); register with `telecode_board_mark` |
| `assets.json` | Deliverable registry for the Review tab |
| `window.telecode.complete()` | In-artifact AI helper |
| `window.tdTimeline` | Seekable timeline for animation export |
