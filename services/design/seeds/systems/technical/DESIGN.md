# Technical

> Dense developer-tool system: cool greys, a teal signal colour, IBM Plex Sans with IBM Plex Mono accents, 4px radii and compact 30px controls. For consoles, admin panels, docs, logs and data-heavy tables.
> Default theme: **light** (the other theme is `[data-theme="dark"]`).

## Overview / Voice

Technical is for people who read screens all day: small base size (13px), compact controls, a mono face for anything machine-generated (IDs, hashes, paths, timestamps, figures), and a single teal signal colour. Information density is a feature — but alignment, not decoration, is what keeps it readable.

**Voice:** precise, terse, neutral. Imperative labels ("Deploy", "Rotate key"). State facts with units ("3 replicas · 12 ms p95"). Errors say what failed and what to do; include the error code in mono.

## Colour

Cool neutral greys carry the UI; teal (`--primary`) marks the primary action, the active nav item and focus. Status colours are semantic only — success/warning/destructive map to healthy/degraded/failed and are never decorative. Charts use the five chart tokens in order; `--chart-5` (grey) is "other". Syntax-highlighting colours are out of scope (see gaps).

| Token | Light | Dark |
|---|---|---|
| `--background` | `oklch(0.99 0.002 250)` | `oklch(0.17 0.006 250)` |
| `--foreground` | `oklch(0.2 0.01 250)` | `oklch(0.93 0.005 250)` |
| `--card` | `oklch(1 0 0)` | `oklch(0.2 0.007 250)` |
| `--card-foreground` | `oklch(0.2 0.01 250)` | `oklch(0.93 0.005 250)` |
| `--popover` | `oklch(1 0 0)` | `oklch(0.22 0.008 250)` |
| `--popover-foreground` | `oklch(0.2 0.01 250)` | `oklch(0.93 0.005 250)` |
| `--primary` | `oklch(0.5 0.11 170)` | `oklch(0.76 0.13 170)` |
| `--primary-foreground` | `oklch(0.99 0.002 250)` | `oklch(0.17 0.02 170)` |
| `--secondary` | `oklch(0.955 0.004 250)` | `oklch(0.26 0.008 250)` |
| `--secondary-foreground` | `oklch(0.25 0.012 250)` | `oklch(0.93 0.005 250)` |
| `--muted` | `oklch(0.965 0.003 250)` | `oklch(0.24 0.007 250)` |
| `--muted-foreground` | `oklch(0.5 0.012 250)` | `oklch(0.67 0.01 250)` |
| `--accent` | `oklch(0.95 0.03 170)` | `oklch(0.28 0.04 170)` |
| `--accent-foreground` | `oklch(0.32 0.07 170)` | `oklch(0.9 0.05 170)` |
| `--destructive` | `oklch(0.55 0.2 27)` | `oklch(0.67 0.19 25)` |
| `--destructive-foreground` | `oklch(0.99 0.002 250)` | `oklch(0.17 0.006 250)` |
| `--success` | `oklch(0.53 0.13 150)` | `oklch(0.74 0.15 150)` |
| `--success-foreground` | `oklch(0.99 0.002 250)` | `oklch(0.17 0.006 250)` |
| `--warning` | `oklch(0.72 0.14 70)` | `oklch(0.8 0.14 75)` |
| `--warning-foreground` | `oklch(0.24 0.04 60)` | `oklch(0.2 0.03 60)` |
| `--border` | `oklch(0.9 0.005 250)` | `oklch(1 0 0 / 10%)` |
| `--input` | `oklch(0.86 0.007 250)` | `oklch(1 0 0 / 15%)` |
| `--ring` | `oklch(0.5 0.11 170)` | `oklch(0.76 0.13 170)` |
| `--chart-1` | `oklch(0.55 0.11 170)` | `oklch(0.76 0.13 170)` |
| `--chart-2` | `oklch(0.74 0.14 75)` | `oklch(0.8 0.14 75)` |
| `--chart-3` | `oklch(0.58 0.13 245)` | `oklch(0.7 0.12 245)` |
| `--chart-4` | `oklch(0.6 0.17 340)` | `oklch(0.7 0.16 340)` |
| `--chart-5` | `oklch(0.6 0.01 250)` | `oklch(0.65 0.01 250)` |
| `--overlay` | `oklch(0.15 0 0 / 45%)` | `oklch(0 0 0 / 65%)` |

Every colour lives in `tokens.css`; components and pages reference `var(--token)` only. Foreground pairs (`--x` / `--x-foreground`) are the only sanctioned text-on-fill combinations.

## Typography

IBM Plex Sans for UI and prose (400/500/600), IBM Plex Mono for code, tabular figures, IDs and small uppercase section labels (`--text-xs`, `--tracking-wide`). Base 13px, 12px in tables, headings rarely above `--text-2xl`. Always `font-variant-numeric: tabular-nums` on numbers.

| Token | Family | Weights loaded | License |
|---|---|---|---|
| `--font-display` | IBM Plex Sans | 400, 500, 600 | SIL Open Font License 1.1 |
| `--font-body` | IBM Plex Sans | 400, 500, 600 | SIL Open Font License 1.1 |
| `--font-ui` | IBM Plex Sans | 400, 500, 600 | SIL Open Font License 1.1 |
| `--font-mono` | IBM Plex Mono | 400, 500, 600 | SIL Open Font License 1.1 |

Scale (`--text-*`, px): xs 11 · sm 12 · base 13 · lg 14 · xl 16 · 2xl 20 · 3xl 24 · 4xl 30 · 5xl 40. Line heights: tight 1.2, snug 1.3, normal 1.5, relaxed 1.6. Tracking: tight -0.01em, normal 0em, wide 0.06em.

## Spacing & layout

Dense 4px grid with 2px half-steps (`--space-0-5`, `--space-1-5`). Component padding `--space-2`/`--space-3`; panels `--space-4`. Layout is full-bleed: sidebar 220px, top bar `--nav-h` (44px), content fills the rest. Align everything to a column — ragged edges are the enemy of density.

Scale (`--space-*`, px): 0=0 · px=1 · 0-5=2 · 1=4 · 1-5=6 · 2=8 · 3=12 · 4=16 · 5=20 · 6=24 · 8=32 · 10=40 · 12=48 · 16=64 · 20=80 · 24=96. Control heights: sm 26px, md 30px, lg 36px; nav bar 44px; table rows compact 28px, comfortable 36px.

## Radius

3-4px controls, 6px panels, 8px dialogs. Nothing pill-shaped except status dots.

`--radius-*`: sm 3px · md 4px · lg 6px · xl 8px · full 9999px. Aliases: `--radius-control` → md, `--radius-surface` → lg.

## Elevation

Flat panels separated by 1px borders; shadows only for popovers, menus and dialogs. Sticky table headers use `--muted` with a bottom border, no shadow.

`--shadow-xs|sm|md|lg` are defined per theme (dark themes use deeper shadows). `--card-shadow` = `none`.

## Motion

Near-instant: 80-200ms, no overshoot. Motion communicates state (spinner, progress, row insert highlight), never delight.

Durations: fast 80ms · base 120ms · slow 200ms. Easing: standard `cubic-bezier(0.2, 0, 0, 1)` · out `cubic-bezier(0, 0, 0.2, 1)` · emphasis `cubic-bezier(0.2, 0, 0, 1)`. `prefers-reduced-motion` zeroes the durations in `tokens.css`.

## Iconography

Lucide 14-16px, 1.5px stroke, `--muted-foreground` by default. Status is a 8px dot plus a word, never colour alone.

Library: [Lucide](https://lucide.dev) (ISC). In Pencil mode use `icon` nodes with `library: "lucide"`.

## Imagery

Almost none. Diagrams (architecture, sequence) drawn with 1px strokes in `--border`/`--foreground`, mono labels. Terminal and code blocks on `--muted`.

## Components

Buttons are 30px, 500 weight, 4px radius; secondary is a bordered grey; ghost for toolbars. Inputs 30px with mono text for technical values. Badges are square-ish with mono uppercase labels. Tabs default to underline. Tables are compact (28px rows), mono numerics right-aligned, zebra off, hover row highlight on `--muted`.

- **Button** (`components/actions/Button.jsx`) — `variant`: primary|secondary|outline|ghost|destructive|link, `size`: sm|md|lg|icon, `disabled`: boolean, `type`: button|submit|reset
- **Input** (`components/forms/Input.jsx`) — `label`: string, `hint`: string, `error`: string, `size`: sm|md|lg, `type`: text|email|password|search|number|url|tel, `invalid`: boolean, `disabled`: boolean, `placeholder`: string, `id`: string
- **Card** (`components/layout/Card.jsx`) — `title`: string, `description`: string, `footer`: node, `padding`: sm|md|lg, `elevation`: flat|raised
- **Badge** (`components/data-display/Badge.jsx`) — `variant`: default|secondary|outline|success|warning|destructive
- **Tabs** (`components/navigation/Tabs.jsx`) — `items`: array, `value`: string, `defaultValue`: string, `onChange`: function, `variant`: pill|underline
- **Dialog** (`components/overlays/Dialog.jsx`) — `open`: boolean, `onClose`: function, `title`: string, `description`: string, `footer`: node, `size`: sm|md|lg, `inline`: boolean
- **Table** (`components/data-display/Table.jsx`) — `columns`: array, `rows`: array, `density`: compact|comfortable, `striped`: boolean, `caption`: string
- **Nav** (`components/navigation/Nav.jsx`) — `brand`: node, `items`: array, `actions`: node, `orientation`: horizontal|vertical, `label`: string

Each component has a `*.card.html` specimen next to it showing both themes. Styling is in `components.css` and uses tokens only.

- Do: Use mono for anything a machine produced.
- Do: Right-align numerics and use tabular figures.
- Do: Pair every status colour with a word or icon.
- Don't: No large hero typography.
- Don't: No decorative colour — every hue means something.
- Don't: No rounded pills except status dots.

## Known gaps

- No syntax-highlighting palette; use a separate OFL/MIT theme if code blocks matter.
- No tree view, log viewer or key-value list specimens yet.
- 11px `--text-xs` is below common accessibility guidance — keep it to labels, never body copy.
