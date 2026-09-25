# Colour

## Source of the palette

- The design system, else the brand source, else the chosen direction. Never borrow the app's own
  chrome colours, and never invent unrelated hues.
- Need another shade? Derive it from an existing token in `oklch()`: keep hue and chroma, move
  lightness (`oklch(from var(--accent) calc(l + .08) c h)` or a literal `oklch()`); mix toward a
  neutral with `color-mix(in oklab, var(--accent) 12%, var(--surface))` for tints.

## Structure

- Think in roles: background, surface (1–2 levels), foreground, muted foreground, border, accent,
  on-accent, and status (success, warning, danger, info). Name variables by role.
- Neutrals carry 90% of the interface. Give them a slight hue from the brand (chroma 0.005–0.02)
  rather than dead grey.
- One accent, used for the primary action and at most one other emphasis per screen. Status colours
  are for status only — never decoration, never the brand accent.
- Build a complete working set: primary, a secondary/domain colour if the product needs one, and the
  four status colours, each with a readable foreground.

## Contrast

- Body and UI text ≥ 4.5:1 against its background; large text (≥ 24px, or ≥ 19px bold) and icons
  ≥ 3:1; placeholder text still ≥ 4.5:1 if it carries meaning.
- Check every state pair — hover, pressed, selected, disabled — not just the default. Hover moves the
  background lightness by ~0.06–0.12 in OKLch; it never makes the text fainter.
- Never light-on-light or dark-on-dark; when a solid button inverts on hover, swap foreground and
  background together.
- Don't rely on colour alone: pair status colour with an icon, label or sign.

## Dark mode

- Design it, don't invert it: dark surfaces around L 15–22%, raised surfaces lighter (not darker),
  borders subtler, accent lightness raised to keep contrast, pure black only for OLED-specific work.
- Reduce saturation of large coloured areas in dark mode; keep status colours distinguishable.
- Switch with a data attribute (`[data-theme="dark"]`) or themed variables — not a second stylesheet.

## Gradients and effects

- A gradient must represent something (light, depth, a brand mark) — subtle, same-hue or adjacent-hue
  stops. No rainbow, no two-stop purple→blue.
- Shadows are tinted by the background hue, soft and few (2–3 levels). Elevation for overlays and
  interactive lift, not for every card.
