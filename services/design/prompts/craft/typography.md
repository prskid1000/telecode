# Typography

## Choosing faces

- Use the design system's families. Without one, pair a display face with personality and a quiet,
  highly legible body face — two families, three at most with a mono. One family is acceptable for
  utility and data-dense products.
- All Google Fonts are available to HTML boards (load only the weights you use, `display=swap`);
  the design system's `fonts/` take precedence. Always give a fallback stack.
- Set weights by role and stick to them: e.g. body 400, emphasis 500/600, display 600–800. Two or
  three weights per family is plenty.

## Scale

- Use a modular scale with clear steps (ratio 1.2–1.333 for UI, 1.333–1.618 for editorial and
  marketing). Adjacent levels must look obviously different; if two sizes are within 2px, merge them.
- Hierarchy comes from size **and** weight **and** colour together — not size alone.
- Fluid display sizes on responsive pages: `font-size: clamp(2.5rem, 1.2rem + 4vw, 5rem)`.
- Minimums: UI body 14px (16px preferred on marketing and reading surfaces); captions 12px;
  slides 24px (28px+ preferred), slide titles 44px+; print 9pt body.

## Setting

- Line height: body 1.45–1.6; headings 1.05–1.25 (tighter as size grows); UI labels 1.2–1.3.
- Line length: 55–75 characters for reading; wider only for data.
- Tracking: tighten large display text (−0.01 to −0.03em); loosen small caps and all-caps labels
  (+0.04 to +0.08em); leave body at 0.
- All caps only for short labels and eyebrows, never for sentences.
- `text-wrap: balance` on headings, `text-wrap: pretty` on paragraphs; no orphaned single word on a
  heading's last line — fix with width or wording, not `<br>`.
- Numbers that change or compare: `font-variant-numeric: tabular-nums`; use real minus (−),
  en dashes for ranges (9–5), proper quotes (“ ” ‘ ’) and ellipsis (…).
- Don't fake bold or italic; load the real style. Don't underline anything that isn't a link.
- Left-align body text. Centre at most 2–3 lines. Justify only with hyphenation, in print-like
  layouts.

## Layer boards

`lineHeight` is a ratio (1.4), `letterSpacing` is in px; set `fontFamily`, `fontSize`, `fontWeight`
and `fill` on every text node — nothing inherits.
