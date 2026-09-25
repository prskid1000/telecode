# Using Neutral

How an agent applies this system in a TeleDesign project. The project receives a read-only copy at `_ds/neutral/`.

## Read order

1. This file.
2. `DESIGN.md` — intent, voice, rules and known gaps. Its Do / Don't lines are requirements, not suggestions.
3. `tokens.css` — the only source of colour, type, spacing, radius, shadow and motion values.
4. `manifest.json` — component inventory with prop enums, specimen cards, fonts and themes.
5. Open a `*.card.html` only when you need to see a component's exact states.

## Design mode (HTML / React)

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap">
<link rel="stylesheet" href="_ds/neutral/tokens.css">
<link rel="stylesheet" href="_ds/neutral/components.css">
```

- Themes: `:root` is **light**. Put `data-theme="dark"` on `<html>` (or any subtree) for the other theme; both can appear side by side.
- Components: load after React 18.3.1 / ReactDOM / Babel standalone 7.29 (SRI hashes are in any `*.card.html`):
  `<script type="text/babel" data-presets="react" src="_ds/neutral/components/actions/Button.jsx"></script>` — each file sets `window.<Name>`.
  Pass only the prop values listed in `manifest.json`; unknown enum values fall back to the default.
- In your own `text/babel` files avoid `const { a, ...rest } = props`: Babel standalone hoists the rest helper (`_excluded`) to global scope, so a second file doing the same throws "Identifier '_excluded' has already been declared". Wrap each file in an IIFE and copy props manually (see `omit` in `Button.jsx`).
- Write your own CSS with `var(--token)` only. Raw hex / rgb / oklch, raw px for spacing or radius, and font families not listed in `manifest.json` are flagged by `adherence.json` after every turn.
- Need a value the system lacks? Use the closest token and leave a visible `/* TODO(design-system): … */` comment. Do not add tokens to `_ds/`.
- Plain-HTML pages may use the class API directly (`td-btn td-btn--primary td-btn--md`, `td-input`, `td-card`, `td-badge td-badge--success`, …) — same CSS, no React needed.

## Pencil mode (.pen)

- Import the library: `"imports": { "ds": "_ds/neutral/system.lib.pen" }` and bind properties to its variables (`"fill": "$--primary"`, `"cornerRadius": "$--radius-control"`, `"fontFamily": "$--font-ui"`).
- Theme axis `Mode` = `Light` (default) / `Dark`; set `"theme": {"Mode": "Dark"}` on a frame to preview the other theme.
- Instantiate components with `ref` (e.g. `btn-primary-md`, `input-default`, `card-default`, `badge-success`, `tabs-pill`, `dialog-default`, `table-default`, `nav-horizontal`) and change text through `descendants` (`{"btn-primary-md-label": {"content": "Save"}}`). Component names follow `Component/Variant[/Size]`.
- Colours in the library are hex conversions of the oklch tokens (the .pen format is hex-only); treat `tokens.json` as the source of truth.

## System-specific rules

- Let whitespace separate groups; add dividers only between unrelated sections.
- Use at most one primary button per view.
- Do not invent colour values outside the token set — if a brief needs one, leave a visible TODO comment and use the closest token.
- Avoid: No gradients on surfaces.
- Avoid: No drop shadows on inputs.
- Avoid: No more than three type sizes per screen.

## Checklist before finishing a turn

- No raw colours, no off-scale spacing, no off-system fonts (run the adherence lint mentally: `adherence.json`).
- Every text/fill pair is a documented foreground pair.
- Checked both themes if the page supports theme switching.
- Icons are Lucide; icon-only buttons have `aria-label`.
