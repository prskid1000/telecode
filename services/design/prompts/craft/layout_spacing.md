<!-- Portions adapted from nexu-io/open-design (Apache-2.0): the layout-integrity invariants and the
     image-overlay rule (§3, §4). See NOTICE. -->

# Layout and spacing

## 1. Scale and grid

- One spacing scale for everything, on a 4px base: 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 · 96 · 128.
  Use the design system's scale when there is one. Off-scale values need a reason.
- Proximity is meaning: related items tighter (8–16), groups looser (24–32), sections loosest
  (64–128). If everything is equally spaced, nothing is grouped.
- Pick a grid and keep to it: 12 columns for web layouts, 4 on mobile, with a consistent gutter
  (16–32px) and a max content width (1120–1280px for apps and marketing, ~680px for reading).
- Align to few edges. Every element should share a left edge or a centre line with something else.

## 2. Composition

- One focal point per view; one dominant axis per section.
- Give the page rhythm: alternate dense and open sections, vary section heights, let one element
  break the grid on purpose.
- Use CSS Grid for two-dimensional layout and `gap` rather than margins between siblings;
  container queries when a component must adapt to its slot rather than the viewport.
- Whitespace separates before lines do; add a divider only where whitespace can't.

## 3. Integrity — hard requirements

- No accidental overlaps. No element hidden behind another unless it's an intentional overlay.
- Text fits its container at every supported width — no clipped labels, no text escaping a table
  cell or button. Fix the container or the copy; never hide overflow to disguise it.
- Oversized display text fits its column: reduce the size, allow wrapping or widen the column —
  never `white-space: nowrap` that pushes into neighbours.
- No orphans: a heading or button label must not leave one short word alone on its last line when
  the line above has room.
- Charts use filled marks, not outlines alone.
- No horizontal scrolling at 360px on responsive pages (tables excepted, inside their own scroller).
- Interactive elements keep their size when their label changes (min-width, reserved space for
  loading spinners).

## 4. Text over images

Anchor overlays (badges, captions, labels) to one corner with equal insets; keep them fully inside
the image; don't cover faces or the subject; give them a solid or frosted panel. If no corner is
safe, put the text beside the image.

## 5. Common spacing defaults

| Context | Gap | Padding |
|---|---|---|
| Page sections | 64–128 | — |
| Screen sections (app) | 24–32 | page 24–32 |
| Card grids | 16–24 | cards 20–24 |
| Form fields, stacked | 16 | inputs 8–10 × 12–16 |
| Buttons in a group | 8–12 | buttons 10 × 16 (md) |
| List rows | 0 (dividers) or 4 | 12 × 16 |
