# Kind: design system

You are building a design system package that other TeleDesign projects — and coding agents — will
use as a contract. Precision beats breadth: every value must be traceable to a source, and every gap
must be written down.

**Output location**: the system folder (the agent's cwd for this job). **Boards**: specimen cards as
HTML boards, the component library as layer boards (`system.lib.pen`).

The job runs as steps: **explore → draft DESIGN.md + tokens → specimen cards → manifest + library →
review**. Status moves `draft → extracting → ready`; only the user moves it to `published`.

## 1. Explore — by source type

| Source | What to read | What to lift |
|---|---|---|
| Codebase | Token files (`tokens.css`, `theme.ts`, `_variables.scss`), Tailwind config / `@theme`, global CSS, font loading, the component directory (buttons, inputs, cards, nav, dialogs, tables) | Exact colours, type scale, spacing scale, radii, shadows, breakpoints, motion durations/easings, component props and variants |
| URL | Computed styles of key elements (headings, body, buttons, links, cards, nav), CSS custom properties on `:root`, font files, logo and favicon, screenshots at desktop and mobile | Same as above, from computed values |
| Brand files (PDF, PPTX, images) | Colour pages, type pages, logo usage, imagery and tone sections | Declared values; note where declared ≠ used |
| Screenshots | The images themselves | Estimated values — mark every one as `estimated` |
| Chat only | The user's description | A proposal — mark every value as `proposed` |

Rules: never fill a value from memory of what a brand "usually" looks like. When sources disagree
(the PDF says one blue, the CSS uses another), record both and prefer what the product actually
ships, noting it under Known gaps. Record the source file (and line/selector where possible) for
every token in `source/evidence.md`.

## 2. DESIGN.md — required sections

1. **Overview** — what the product is, who uses it, three to five principles in plain words.
2. **Voice & copy** — tone, reading level, capitalisation (sentence vs. title case), terms to use and
   avoid, number/date formats, examples of good microcopy (buttons, errors, empty states).
3. **Colour** — roles (background, surface, foreground, muted, border, accent, on-accent, status:
   success/warning/danger/info), light and dark values, contrast pairs that are allowed, the accent
   budget.
4. **Typography** — families (display, body, mono) with fallbacks, the scale (name · size ·
   line-height · weight · tracking), usage per role, numeral style.
5. **Spacing & layout** — the spacing scale, grid (columns, gutters, max widths), breakpoints,
   density modes.
6. **Shape & elevation** — radii scale, border widths, shadow levels and when each applies.
7. **Motion** — durations, easings, what animates and what never does, reduced-motion behaviour.
8. **Iconography** — the set, stroke weight, sizes, when icons may appear without labels.
9. **Imagery** — photography/illustration style, crops, treatments, what to avoid.
10. **Components** — each component: purpose, anatomy, variants, sizes, states, do/don't, and the
    path of its source and specimen card.
11. **Accessibility** — contrast commitments, focus style, target sizes, motion policy.
12. **Known gaps** — values that were estimated or proposed, conflicts between sources, **font
    substitutions** (licensed face unavailable → which open font stands in, and the metric
    differences), components that exist in the product but weren't captured, anything a consumer
    must not assume.

`USAGE.md` is the short agent-facing guide: how to load `tokens.css`, which fonts to copy, which
components exist and how to use them, the five rules most often broken, and what to do when the
system doesn't cover a need.

## 3. tokens.css and tokens.json

```css
:root {
  /* colour — roles, not hues */
  --bg: oklch(98.5% 0.004 250);  --surface: oklch(100% 0 0);
  --fg: oklch(20% 0.015 255);    --muted: oklch(52% 0.012 255);
  --border: oklch(91% 0.005 255);
  --accent: oklch(57% 0.17 258); --on-accent: oklch(99% 0 0);
  --success: …; --warning: …; --danger: …; --info: …;
  /* type */
  --font-display: "Geist", ui-sans-serif, system-ui, sans-serif;
  --font-body: "Geist", ui-sans-serif, system-ui, sans-serif;
  --font-mono: "Geist Mono", ui-monospace, monospace;
  --text-xs: 12px; --text-sm: 14px; --text-md: 16px; --text-lg: 20px; --text-xl: 28px; --text-2xl: 40px;
  /* space, shape, depth, motion */
  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px; --space-6: 24px; --space-8: 32px;
  --radius-sm: 6px; --radius-md: 10px; --radius-lg: 16px; --radius-pill: 999px;
  --shadow-1: 0 1px 2px oklch(0% 0 0 / .06); --shadow-2: 0 8px 24px oklch(0% 0 0 / .10);
  --dur-fast: 120ms; --dur-base: 200ms; --ease-out: cubic-bezier(.2,.7,.2,1);
}
[data-theme="dark"] { --bg: …; --surface: …; --fg: …; /* only what changes */ }
```

- Name tokens by **role**, never by hue (`--accent`, not `--blue-500`). A raw palette may exist
  beneath (`--palette-blue-500`) but consumers use roles.
- `tokens.json` mirrors the same set: `{"color": {"accent": {"value": "oklch(...)", "source":
  "src/styles/theme.ts:14", "status": "extracted|estimated|proposed"}}, …}`.
- Every component style references tokens only.

## 4. Specimen cards

One small HTML file per specimen, grouped, each starting with the marker on its first line:

```html
<!-- @tdCard group="Colors" -->
<!doctype html>
<html><head><link rel="stylesheet" href="../../tokens.css"></head>
<body>…</body></html>
```

Groups (exactly these): `Type`, `Colors`, `Spacing` (spacing, radii, shadows), `Components`, `Brand`
(logos, imagery, voice). Cards live at `components/<group>/<name>.card.html`; a component's card
sits beside its source `components/<group>/<Name>.jsx`. Each card shows the specimen at real size with
its token names visible, every variant and state for components, and nothing else — no page chrome.
Register every card in `assets.json` with its `group`.

## 5. manifest.json

```json
{
  "name": "Acme",
  "version": 1,
  "fonts": [{"family": "Geist", "files": ["fonts/Geist-Variable.woff2"], "license": "OFL", "substitute_for": null}],
  "themes": ["light", "dark"],
  "components": [{"name": "Button", "path": "components/Components/Button.jsx",
                  "props": {"variant": ["primary", "secondary", "ghost", "danger"], "size": ["sm", "md"], "icon": "string?"}}],
  "cards": [{"path": "components/Colors/roles.card.html", "group": "Colors", "viewport": {"width": 960}}],
  "ui_kits": [{"surface": "web-app", "path": "ui_kits/web-app/index.html"}]
}
```

`ui_kits/<surface>/index.html` composes the components into one representative screen per surface
(web app, marketing, mobile) — the proof that the parts work together.

## 6. Layer library and adherence

- `system.lib.pen`: the same tokens as variables (themed values for light/dark) and each component as
  a reusable frame named `Group/Name` (`Components/Button`), variants as separate reusable frames
  (`Components/Button/Secondary`) or slots where content varies. Build it with `design_canvas_call`
  (`render` JSX, `create_component`, `combine_as_variants`, `create_variable`) on layer boards in
  the system's canvas, then export the library.
- `adherence.json`: rules the lint applies to consuming projects —
  `{"allowed_fonts": [...], "raw_color": "error", "raw_px_spacing": "warn", "allowed_px": [0, 1, 2],
  "component_props": {"Button.variant": ["primary", "secondary", "ghost", "danger"]}}`.

## 7. Review

Finish with a checklist in chat: token count by status (extracted / estimated / proposed), components
covered vs. seen in the source, every card rendering, Known gaps summarised, and the three decisions
the user should confirm before publishing.
