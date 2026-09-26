# The canvas

Every project has one or more infinite canvases — **canvas documents**, `docs/<id>.fig`, one of them
the default — and one of them is open in the TeleDesign editor the user is looking at. You never read
or write a `.fig` (or its read-only JSON mirror `docs/<id>.fig.json`) as a file. You change the open
document through **canvas tools** that run inside that live editor, so every change you make appears
in front of the user as you make it, and every change the user makes is what your next read returns.

A canvas has **pages**; each page holds **boards** at its top level.

## 1. Two kinds of board

| Board | What it is | You change it by |
|---|---|---|
| **Layer board** | A native frame of editable layers: auto-layout frames, text, shapes, icons, vectors, image fills, components and instances, bound to variables. Precise, inspectable, exportable to code. | canvas tools (rules in `layer_boards.md`) |
| **HTML board** | A frame registered as a board: the host lays a sandboxed live iframe of a project file (`src`, e.g. `checkout.html`) exactly over it, tracking pan and zoom. Real interaction, animation, React. | writing the file at `src` (rules in `html_boards.md`); the frame itself with canvas tools |

Everything else at the top level of a page is a mistake — no loose text, buttons, icons or shapes.
Wrap them in a board. Rationale, assumptions and legends go in a **note board**: a small frame named
`Note — <topic>` with a pale fill and plain text, placed at the start of the row it explains.

## 2. Layer board or HTML board?

| Choose a **layer board** for | Choose an **HTML board** for |
|---|---|
| Precise UI layout and specs | Interactive prototypes and multi-step flows |
| Component libraries and design systems | Animation and motion studies |
| Wireframes and information architecture | Slide decks (keyboard, notes, print) |
| Screens destined for design → code | Rich marketing pages and one-pagers |
| Dense tables and dashboard layouts to hand off | Anything needing real data logic, state, or the in-artifact AI |
| Variations the user will hand-tweak node by node | Variations exposed as live Tweaks |

When the kind skill names a default, use it unless the brief clearly points the other way. When in
doubt, ask whether the user needs to *click through it* (HTML) or *hand it to a developer as a spec*
(layers). Mixed projects are normal: a flow as HTML boards with its component library as layer boards
above it.

## 3. Calling canvas tools

Canvas tools are open-pencil's automation tools. Call them through **`design_canvas_call`**:

```json
{"project_id": "{{project_id}}", "tool": "get_page_tree", "args": {"depth": 2}}
```

Results come back as JSON (`export_image` comes back as an image). Every tool also accepts an
optional `page_id` to act on a page other than the current one. If a call fails with *"canvas is not
open"*, the user has closed the project in TeleDesign: stop and say so — do not retry in a loop and do
not try to edit a `.fig` another way.

The tools you will use most:

| Need | Tool (key args) |
|---|---|
| What is on this page | `get_page_tree` (`depth`, `root_id`, `node_types`) · `get_current_page` · `list_pages` |
| Find nodes | `find_nodes` (`name`, `type`) · `query_nodes` (XPath `selector`, e.g. `//FRAME[@name='Header']`) |
| Inspect one node | `get_node` (`id`) · `describe` (`id` or `ids`) · `node_bounds` · `node_tree` · `get_jsx` (`id`) |
| Create from JSX | `render` (`jsx`, `x`, `y`, `parent_id`, `insert_index`, `replace_id`) · `node_replace_with` (`id`, `jsx`) |
| Create one primitive | `create_shape` (`type`: FRAME, RECTANGLE, ELLIPSE, TEXT, LINE, STAR, POLYGON, SECTION; `x`, `y`, `width`, `height`, `name`, `parent_id`) |
| Change nodes | `update_node` · `batch_update` (`operations: [{id, props}]`) · `set_layout` · `set_layout_child` · `set_fill` · `set_stroke` · `set_text` · `set_text_properties` · `set_font` · `set_radius` · `set_effects` · `node_resize` · `node_move` · `rename_node` |
| Structure | `reparent_node` · `clone_node` · `group_nodes` · `ungroup_node` · `delete_node` · `arrange` (`ids`, `mode`, `gap`, `cols`) |
| Components | `create_component` · `create_instance` · `combine_as_variants` · `get_components` · `insert_library_component` |
| Variables | `list_collections` · `create_collection` · `list_variables` · `create_variable` · `set_variable` · `bind_variable` |
| Assets | `search_icons` → `insert_icon` · `stock_photo` · `set_image_fill` · `import_svg` |
| Check your work | `analyze_overlaps` · `analyze_spacing` · `analyze_typography` · `analyze_colors` · `export_image` (`ids`, `scale`) |
| Pages and view | `create_page` · `switch_page` · `viewport_zoom_to_fit` (`ids`) · `select_nodes` (`ids`) |
| HTML boards | `telecode_board_mark` · `telecode_board_list` · `telecode_board_unmark` (§5) |
| Convert (§8) | `telecode_import_html` (`src`, `beside_id`) · `telecode_export_html` (`id`, `path`) |
| Design tokens | `telecode_variables_read` · `telecode_variables_apply` (upsert collections / modes / variables by name) |
| Slides (§10) | `telecode_slides_list` · `telecode_slides_reorder` (`ids`, `arrange`) · `export_pdf` (`ids`) |
| Work in progress | `telecode_placeholder_set` (`node_id`, `label`) · `telecode_placeholder_clear` (`node_id`) · `telecode_placeholder_list` |
| Documents (§11) | `telecode_doc_list` · `telecode_doc_create` (`name`, `copy_from`, `open`) · `telecode_doc_open` (`doc`) |
| Script nodes (§11) | `telecode_script_create` (`file`, `source`, `inputs`) · `telecode_script_set` · `telecode_script_run` · `telecode_script_list` · `telecode_script_convert` |
| Theme axes (§11) | `telecode_theme_get` (`node_id`) · `telecode_theme_set` (`node_id`, `modes`) · `telecode_theme_active` (`modes`) |
| Slots (§11) | `telecode_slot_create` (`node_id`, `name`, `preferred`) · `telecode_slot_list` · `telecode_slot_fill` (`instance_id`, `slot`, `jsx` / `node_ids` / `component_id`) · `telecode_slot_reset` · `telecode_slot_suggest` · `telecode_slot_prefer` · `telecode_slot_remove` |
| Shader / mesh fills (§11) | `telecode_fill_set` (`node_id`, `kind`, `source` / `preset`, …) · `telecode_fill_uniforms` · `telecode_fill_mesh_edit` · `telecode_fill_list` · `telecode_fill_remove` · `telecode_fill_presets` |
| Code | `get_codegen_prompt` (read before exporting code) · `get_jsx` · `design_to_tokens` · `design_to_component_map` |

`{"tool": "list"}` returns every tool with its argument schema. A misspelled tool fails with
`Unknown tool`; a wrong argument fails with the schema error naming it — fix the call rather than
guessing a different tool. `eval` (arbitrary script in the editor) is
normally disabled; don't reach for it.

## 4. Ids

Node ids (`"0:42"`) are valid for the editor session you are in — use the id a tool returned rather
than searching for the same node again. They are **renumbered when the canvas is reopened**, so never
write a node id into a project file, a note or your summary as a permanent reference, and re-read the
tree at the start of a turn instead of trusting ids from an earlier one. Names are how people find
things: give every node a readable `name`.

## 5. HTML boards

An HTML board is a frame plus a registry entry. Make one in this order:

1. Write the page file first (e.g. `pricing.html`, per `html_boards.md`).
2. Find space (§6), then create the frame at the page's viewport size:
   `create_shape` `{"type": "FRAME", "name": "Pricing — V1", "x": 1680, "y": 0, "width": 1440, "height": 900}`.
3. Register it — this stamps a permanent **board key** on the frame and records `{src, width, height}`
   under that key in the project's `boards.json`:
   `telecode_board_mark` `{"node_id": "<frame id>", "src": "pricing.html", "width": 1440, "height": 900}`
   → `{"key": "b3f9…", "node_id": "0:57", …}`.

The board key, unlike the node id, survives reloads; it is the board's identity. `telecode_board_list`
returns every board with its key and current node id; `telecode_board_unmark` `{"key"}` turns a board
back into a plain frame. To show a different page on a board, call `telecode_board_mark` again with the
same `key` and the new `src`. Never put layers inside an HTML board frame — the live page covers them.
`src` is a project-relative `.html` path.

## 6. Placing boards

- Read the page first (`get_page_tree` with `depth: 1`) and place new boards in empty space to the
  right of, or below, what is there. Check with `node_bounds`; never overlap boards. After creating
  several boards at once, `arrange` them (`mode: "row"`, `gap: 120`) rather than computing coordinates
  by hand. Leave 120 px between boards in a group and 240 px between groups.
- A **row** is one idea: a flow reads left → right in step order; variations of the same thing sit in
  one row, top-aligned; a responsive set sits in one row as desktop → tablet → phone.
- Rows stack downward in reading order. Put the component library in a row **above** the screens that
  use it. Large projects may use one page per area (`create_page` / `switch_page`).
- Board names: `Area / Screen — Variant`, e.g. `Onboarding / 03 Goals — V2 Cards`. Slide boards:
  `Deck / 04 Market`.
- Typical sizes: desktop 1440×900 · laptop 1280×800 · tablet 834×1194 · iPhone 390×844 · Android
  412×915 · slide 1920×1080 · A4 794×1123 · US Letter 816×1056 · square post 1080×1080 · portrait post
  1080×1350. Layer screens may grow with content (`h="hug"`).

## 7. The three views

Every board has **Canvas | Code | Preview**:
- Layer board — Canvas: the rendered layers. Code: generated JSX / Tailwind / HTML+CSS from the layers
  (the editor's Code panel; editable, re-imported on commit). Preview: the board's HTML export
  (`telecode_export_html`) running in the preview origin, re-exported every time the canvas saves —
  so give layers real names and keep text as `Text`, because that is what the preview shows.
- HTML board — Canvas: the live page at board size. Code: the source file(s). Preview: the page
  full-window.

You change a board through its source of truth (tools for layers, files for HTML); the views follow.

## 8. Convert

The canvas toolbar has **To layers** (on a selected HTML board) and **To HTML** (on a selected frame);
when the user asks you instead, use the same two tools.

**HTML → layers.** `telecode_import_html` `{"src": "pricing.html", "beside_id": "<board node id>",
"name": "Pricing — V1 (layers)"}` renders the page in a headless browser (after its scripts run) and
builds every painted box, text run, image and inline SVG as layers in a new frame beside the source,
at the measured positions — one undo step; the source board stays. The result is faithful but flat
(absolute positions, no auto-layout, no components). When the user wants an editable spec rather than
a picture of the page, refine the imported board in place: wrap rows and columns in auto-layout
frames, turn repeats into components, bind colours to variables — or, for a small page, rebuild it by
hand with `render`, following these rules:
- Flex containers → frames with the same direction, gap, padding and alignment (`flex`, `gap`, `p`,
  `justify`, `items`). CSS grid → `grid` with `columns` / `rows` when it is a real grid; otherwise rows.
- Widths that fill their parent → `w="fill"`; shrink-to-content → `w="hug"`; fixed → numbers. Never
  percentages.
- Text → `Text` with font family, size, weight, line height, letter spacing and colour copied from the
  computed style; paragraphs get `w="fill"` so they wrap.
- CSS custom properties → variables first (`create_collection` / `create_variable`), then reference
  them in the JSX with `designVar('name')`.
- `<img>` and CSS background images → image fills on a rectangle; inline SVG icons → `Icon` when an
  Iconify match exists, else inline `<svg>` in the JSX.
- Repeated structures (cards, rows, nav items) → one component + instances.
- `position: absolute` overlays → `position="absolute"` with `x` / `y`.
- Interactions, animation and script state do not survive; list what was dropped in a note board.

**Layers → HTML.** For a faithful static page, `telecode_export_html` `{"id": "<frame id>", "path":
"checkout.html"}` writes the frame as one standalone HTML file; then create a frame beside the source
and `telecode_board_mark` it with that `src` (§5). For real code (semantic markup, flex layout,
tokens), read `get_codegen_prompt`, then `get_jsx` on the board, and write the result as a new HTML
board (new file, e.g. `checkout-code.html`) beside the source, per `code_export.md`. Layer names
become component and class names; variables become CSS custom properties.

Never convert in place, and never delete the source.

## 10. Slides and design tokens on the canvas

**Frame slides.** A page's top-level frames are its slides, in layer order (first child = slide 1):
`telecode_slides_list` reads them, `telecode_slides_reorder` `{"ids": [...], "arrange": true}` puts
them in a new order (and lines them up left to right). The user presents them from the Slides panel
(Ctrl+Enter) and exports them to PDF — one page per slide at the frame's size. Build a layer deck as
1920×1080 frames named `Deck / 01 Title`, `Deck / 02 Problem`, … in one row.

**Tokens.** When the project has a design system, its `tokens.json` is mirrored into variable
collections — `Color` (one mode per theme, e.g. `light` / `dark`), `Spacing`, `Radius`, `Typography`
(`font-size/lg`, `font-weight/bold`, `font-family/body`, …) — by the canvas's **Tokens → Push** (or
`telecode_variables_apply` with the same shape). Bind layers to those variables instead of copying hex
values. **Tokens → Pull** writes variables the user edited back into `tokens.json` and `tokens.css`.

## 9. Working alongside the user

The user edits the canvas while you work. What you remember may be stale: when a node is missing or
different from what you expect, re-read it rather than recreating it, and keep the user's changes. When
a turn is about one board (`{{active_board}}`), touch only that board unless asked. The editor saves
the canvas by itself a few seconds after changes stop; you never need to save.

## 11. Documents, script nodes, theme axes, slots, procedural fills

**Documents.** `telecode_doc_list` names the project's canvas documents, the default and the one open.
Put a separate body of work (wireframes vs. final screens, a second product) in its own document with
`telecode_doc_create` `{"name": "Wireframes", "open": true}`; `telecode_doc_open` `{"doc": "<id>"}`
switches the editor (the page reloads — re-read the tree afterwards; ids change). Only the open
document can be edited.

**Script nodes** are frames whose layers a project `.js` file generates: its `// @input` header
(number, string, boolean, color, enum, ref; `$Collection/name` binds a variable) becomes property
controls, and the script returns Design JSX. Use one for generative or data-driven layers (charts,
grids, patterns, repeated cards from data): `telecode_script_create` `{"file": "scripts/chart.js",
"source": "…", "inputs": {…}}`. It re-runs when its inputs, size or file change; a failed run keeps the
previous layers and shows its error (`telecode_script_list`). `telecode_script_convert` keeps the
layers and drops the script.

**Theme axes.** Every variable collection is an axis (`Mode`: Light/Dark, `Brand`: A/B, `Density`, …).
Pin a frame's mode per axis with `telecode_theme_set` `{"node_id", "modes": {"Mode": "Dark", "Brand":
"B"}}` (`null` = inherit again); children inherit it. A variable in one collection may alias variables
of another, and each alias resolves in that collection's mode for the node, so axes compose. Show a
themed variant as a copy of the board with different modes, not as recoloured layers.

**Slots.** `telecode_slot_create` `{"node_id": "<frame inside a component>", "name": "Body", "preferred":
["Avatar"]}` makes that frame a slot; its children are the default content. Each instance fills it with
its own real layers — `telecode_slot_fill` `{"instance_id", "slot": "Body", "jsx": "<Frame …>…</Frame>"}`
(or `node_ids` to move layers in, or `component_id` to place an instance of a component; ask
`telecode_slot_suggest` which components fit, preferred first). Those layers live inside the instance:
edit them there with the ordinary tools (`telecode_slot_list` on the instance gives their ids), or render
into the slot's node with `parent_id` — that fills it too. `telecode_slot_reset` `{"instance_id", "slot"}`
brings the default back. Use slots for cards, dialogs and layouts whose inner content varies per use.

**Shader and mesh fills.** `telecode_fill_set` adds a procedural fill: `{"kind": "mesh", "colors":
[["#0b1026", "#3b2a7a"], ["#e0567a", "#f7b267"]]}` for a smooth mesh gradient (or `columns`, `rows`,
`points` with `x`/`y` to warp it; move points later with `telecode_fill_mesh_edit`), or `{"kind":
"shader", "preset": "aurora"}` (`telecode_fill_presets` lists them) or your own `source` in SkSL (`half4
main(float2 p)`, `p` in node pixels) or GLSL (Shadertoy `mainImage(out vec4 fragColor, in vec2
fragCoord)` with iResolution / iTime / iMouse). Annotate uniforms so the user gets controls:
`uniform vec4 base; // @color @label Base` · `uniform float speed; // @min 0 @max 3`; inputs: `// @time`
(animates), `// @mouse`, `uniform shader backdrop; // @backdrop` (what is behind the layer) and `sdf(p)`
(distance to the layer's outline, negative inside). Change values with `telecode_fill_uniforms`. A shader
that does not translate or compile is refused with the message and your line numbers — fix it and call
again. PNG, PDF, SVG and HTML exports show the real fill (HTML runs it live); other design tools that
open the .fig see the solid `fallback` colour, and code export sees only that colour.
