<!-- Portions adapted from nexu-io/open-design (Apache-2.0): the real-first sourcing order and the
     image-geometry rules (§1, §2). See NOTICE. -->

# Imagery and icons

## 1. Sourcing — real first

1. **The user's and the project's assets** — uploads, `_ds/…` brand assets, files in the codebase.
2. **Real imagery for real referents** — when the design names a real product, person, place, book,
   event or brand, use its actual image from a source the user provided or pointed to, copied into
   `assets/`. Never a look-alike, a drawing or an invented stand-in.
3. **Real photography for atmosphere** — scenes, textures, lifestyle — from sources the user supplied
   or approved.
{{#image_tools}}
4. **Generated imagery** ({{image_tools}}) — only for non-factual, atmospheric images, only a few
   key surfaces, and never to avoid acquiring a real asset.
{{/image_tools}}

Otherwise: **a labelled placeholder** — a neutral block with the intended content and ratio written on it
   ("Team photo, 3:2, warm daylight"). Say in the summary what's missing.

Every image is a project-local file referenced by relative path (or a data URI in standalone
exports). Never hotlink; never depend on a URL that can expire. Keep required credits and licence
notes.

## 2. Geometry and treatment

- Read an image's intrinsic width and height before placing it. Its box takes that ratio (width/height
  attributes or `aspect-ratio`); don't force it into a placeholder's ratio.
- Content images show their full frame (`object-fit: contain` or natural flow). `cover` only for
  deliberately croppable backgrounds — and choose the focal point (`object-position`).
- Constrain very tall or wide images on one axis (`max-height`) and leave the other automatic.
- Declare dimensions to prevent layout shift. Consistent treatment across a page: same corner radius,
  same border or none, same caption style.
- Text on images needs a contrast treatment (see `layout_spacing.md` §4).

## 3. Illustration and artwork

Don't draw illustrations, characters, scenery, logos or device mockups from hand-written SVG or CSS
shapes. It always reads as amateur. Use the brand's illustration library, a starter device frame,
or a placeholder — and ask for the real asset. Simple geometric decoration that is part of the layout
(a rule, a dot grid, a shape behind a number) is fine.

## 4. Icons

- One set per project — the design system's, otherwise Lucide — at one stroke weight (1.5–2px) and
  a small set of sizes (16 / 20 / 24).
- Icons clarify, they don't decorate: use them for recognisable actions and navigation, next to a
  label unless the meaning is universal (search, close, menu).
- Inline SVG with `currentColor` on HTML boards; `icon` nodes on layer boards. Never emoji as icons,
  never mixed sets.
- Align icons to the text's optical centre; give icon-only buttons a full-size target and a label.

## 5. Logos

Use the provided logo file only, at its specified clear space and minimum size, on a background that
keeps it legible. Never recolour, stretch, redraw or approximate a logo. Without the file, use the
product name in the brand's type as a placeholder.
