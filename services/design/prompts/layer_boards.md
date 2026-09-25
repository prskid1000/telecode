# Layer boards — building with the canvas tools

A layer board is a top-level frame on a canvas page. Everything inside it is a real layer you create
and change with the canvas tools (`design_canvas_call`, see `canvas.md` §3). The editor has its own
layout engine: auto-layout resembles CSS flexbox and grid but it is **not** a browser — if a property
isn't described here or in a tool's schema, it doesn't exist.

## 1. The fastest path: `render` with design JSX

Build a board, or any section of one, as one JSX string:

```json
{"tool": "render", "args": {"x": 1680, "y": 0, "jsx": "<Frame name=\"Pricing — V1\" w={1440} h=\"hug\" flex=\"col\" gap={48} p={80} bg=\"#FFFFFF\">…</Frame>"}}
```

It returns `{id, name, type, children}` — keep those ids for follow-up edits in this turn.
`render` with `parent_id` (and optionally `insert_index`) adds a section inside an existing frame;
`replace_id` swaps a placeholder for the real thing in the same position; `node_replace_with` does the
same by id. `get_jsx` on an existing node returns its JSX in the same syntax, so read → edit → re-render
is the way to restructure a section. `diff_jsx` compares two nodes.

Design JSX is scene authoring, not React DOM:

**Elements**: `Frame` (alias `View`) — the container, the only one with layout and padding · `Text` ·
`Rectangle` (`Rect`) · `Ellipse` · `Line` · `Star` · `Polygon` · `Vector` · inline `<svg>` with `<path>`
· `Group` · `Section` · `Icon` · `Component` · `ComponentSet` · `Instance`.

**Size**: `w` / `h` take pixels, `"hug"` (size to content) or `"fill"` (take the space the auto-layout
parent offers); `minW` / `maxW` / `minH` / `maxH`. No percentages, no `vh`, no `calc()`. `grow` shares
out free space.

**Auto-layout** (on a `Frame`): `flex="row"` or `flex="col"`; `gap`; padding `p`, `px`, `py`, `pt` /
`pr` / `pb` / `pl` (longhands win); `justify="start|center|end|between"` on the main axis;
`items="start|center|end|stretch"` on the cross axis; `wrap` + `rowGap` for wrapping rows. **Grid**:
`grid` with `columns="1fr 240px 1fr"` / `rows`, children placed with `colStart`, `rowStart`,
`colSpan`, `rowSpan`. There is no margin. Children of a layout ignore `x` / `y`; to pin something over
a layout (a badge on a card), give it `position="absolute"` and `x` / `y`.

**Paint and shape**: `bg` (fill) · `stroke`, `strokeWidth`, `strokeAlign`, `strokeDash` · `rounded` or
`roundedTL` / `roundedTR` / `roundedBL` / `roundedBR`, `cornerSmoothing` · `opacity`, `rotate`,
`blendMode` · `overflow="hidden"` to clip · `shadow="0 8 24 #0000001F"` or `effects={[dropShadow({…})]}`
· `blur` · gradients through `fills={[linearGradient([...stops])]}` (also `radialGradient`,
`angularGradient`, `diamondGradient`). Colours are `#RRGGBB` / `#RRGGBBAA` or a variable reference.

**Text**: content goes inside `Text`. `size`, `font`, `weight`, `color`, `lineHeight`, `letterSpacing`,
`textAlign`, `textDecoration`, `textCase`, `maxLines` / `truncate` (only for intended truncation). A
text node inside a column that should wrap gets `w="fill"`; a label that sizes itself gets no width.
Properties never cascade: give every `Text` its own `size` and `color`.

**Icons**: `<Icon name="lucide:settings" size={20} color="#1F2937" />` (any Iconify set; `search_icons`
finds names). Pick one icon set per project.

**Images** are fills on a leaf shape, never on a container with children: render a `Rectangle` with a
readable name, then `stock_photo` (`[{id, query}]`) or `set_image_fill` (base64 data) on it.

Keep one JSX call to one coherent section so a failure is easy to fix; a board of five sections is
five `render` calls into the board frame, not one enormous string.

## 2. Precise edits

For changes to existing layers use the targeted tools rather than re-rendering:
`update_node` (position, size, opacity, corner radius, visibility, text, font size / weight, name) ·
`set_layout` (direction, spacing, padding, align, counter_align) · `set_layout_child` (sizing
FIXED / HUG / FILL, grow, align_self, absolute positioning) · `set_fill` · `set_stroke` · `set_text` /
`set_text_properties` / `set_font` · `set_radius` · `set_effects` · `node_resize` / `node_move` ·
`rename_node` · `reparent_node` · `clone_node` · `delete_node`. Several changes at once:
`batch_update` with `operations: [{id, props}]`.

Never fix a board by deleting and rebuilding it — change the existing nodes, so the user's own edits
and any instances survive.

## 3. Build order

1. **Tokens first.** `list_collections` / `list_variables` to see what exists (don't clobber it), then
   `create_collection` + `create_variable` for anything missing. Reference variables in JSX with
   `designVar('Color/accent')`; bind existing layers with `bind_variable` (colour fields use indexed
   paths such as `fills/0/color`). When a design system is attached, its variables are already in the
   document — use them.
2. **Components that repeat**, in the component row above the screens (see §4).
3. **The board frame**, named for what it is, at its final width; height `"hug"` for screens that grow.
   Give it a first rough pass quickly so the user sees the structure early.
4. **Sections**, in the order a designer would lay them out, each a `render` into the board.
5. **Check** (§5), then move to the next board. Finish a board before starting another.

## 4. Components, variants and instances

- Turn a finished frame into a component with `create_component` (`id`), or author it directly as
  `<Component name="Button/Primary" …>` in JSX.
- Variants: components that share a parent and are named `Category/Value` (`Button/Primary`,
  `Button/Secondary`) become one variant set with `combine_as_variants` (`ids`).
- Instances: `create_instance` (`component_id`, `x`, `y`) or `<Instance of="<component id>" />` in JSX.
  Text, visibility and swap properties defined on the component (`properties` / `propertyRefs`) are
  set per instance through `properties` — don't hand-edit an instance's inner layers to change its
  label.
- Library components: `get_components` (optionally `name`) lists local and library components;
  `insert_library_component` places one by `library_id` + `asset_key`.
- Reuse and generalise an existing component before making a near-duplicate.

## 5. Checking your work

After each board:
- `analyze_overlaps` (`scope` the board) for text bleeding out of frames, siblings on top of each
  other, content covering a footer. Fix the cause — resize the container or change a sizing mode —
  never hide overflow with clipping.
- `describe` the board for a semantic read-back; `node_bounds` / `get_node` when a number matters.
- `analyze_spacing`, `analyze_typography`, `analyze_colors` when the question is consistency with the
  scale and the tokens.
- `export_image` (`ids: [board]`, `scale: 1`) only when the board or a major section is finished and
  the question is visual (colour, type, balance). It costs context: prefer the smallest node that
  answers the question, and at most one image per board per turn unless the user asked.

Then check by eye in the image you took: nothing collapsed to zero, no accidental overlap, text
contrast sufficient, spacing on the scale, fonts actually loaded (`get_font_status` if text looks
wrong).

## 6. Charts, tables, artwork

- Bar and column charts: frames in an auto-layout; donuts from an `Ellipse` with `innerRadius`; labels
  placed by layout, never by hand-matched coordinates. Line charts need a vector path — keep them simple
  or use an HTML board.
- Tables: a column frame of row frames (`w="fill"`), each row holding **cell frames** that hold the
  content. Never put text directly in a row. Consider `grid` with `columns` for strict alignment.
- Inline `<svg>` always carries a `viewBox`. Don't draw logos, people or illustrations from paths — use
  a labelled placeholder rectangle and ask for the asset.

## 7. Code ↔ design

- **Design → code**: call `get_codegen_prompt` and follow it with `code_export.md`; read the board with
  `get_jsx`. Tokens come out with `design_to_tokens` (CSS custom properties, Tailwind, JSON) and the
  component inventory with `design_to_component_map`. Meaningful layer names become component and class
  names.
- **Code → design** (rebuild a component or screen from a repo): read the source and its styles first;
  create its tokens as variables before drawing; rebuild each code component as a component with the
  **same name**; mirror flex structure 1:1 and use `grid` where the code does; copy exact values, never
  "roughly". Record anything you had to approximate in a note board beside it.
- HTML boards are opaque to these tools — the layers under an HTML board are covered by its live page.
  Change an HTML board by editing its file.
