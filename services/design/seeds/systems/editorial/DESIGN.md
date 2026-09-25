# Editorial

> Warm paper, ink and an oxblood accent. Serif display (Fraunces) over a serif text face (Source Serif 4), tight radii and generous measure — for long reads, reports, portfolios and magazine-style landing pages.
> Default theme: **light** (the other theme is `[data-theme="dark"]`).

## Overview / Voice

Editorial treats the screen like a printed page: warm off-white paper, dark brown-black ink, one oxblood accent used the way a magazine uses a second colour. Hierarchy comes from typographic contrast (a high-contrast display serif against a sturdy text serif), rules and whitespace rather than boxes.

**Voice:** considered, literate, a little dry. Headlines can be declarative sentences. Kickers (small caps labels above headlines) name the section. Captions are full sentences. Avoid marketing superlatives.

## Colour

Paper (`--background`), ink (`--foreground`) and oxblood (`--primary`) carry 95% of every layout. Ochre (`--accent`) is a highlighter wash for pull quotes and callouts, never for text. Status colours are muted to sit on paper. Dark mode is "reading lamp": warm charcoal, not blue-black.

| Token | Light | Dark |
|---|---|---|
| `--background` | `oklch(0.975 0.012 85)` | `oklch(0.19 0.012 60)` |
| `--foreground` | `oklch(0.22 0.02 50)` | `oklch(0.93 0.015 85)` |
| `--card` | `oklch(0.99 0.008 85)` | `oklch(0.23 0.014 60)` |
| `--card-foreground` | `oklch(0.22 0.02 50)` | `oklch(0.93 0.015 85)` |
| `--popover` | `oklch(0.99 0.008 85)` | `oklch(0.25 0.015 60)` |
| `--popover-foreground` | `oklch(0.22 0.02 50)` | `oklch(0.93 0.015 85)` |
| `--primary` | `oklch(0.42 0.13 28)` | `oklch(0.7 0.13 32)` |
| `--primary-foreground` | `oklch(0.98 0.01 85)` | `oklch(0.18 0.02 40)` |
| `--secondary` | `oklch(0.93 0.02 80)` | `oklch(0.29 0.015 60)` |
| `--secondary-foreground` | `oklch(0.28 0.03 50)` | `oklch(0.92 0.015 85)` |
| `--muted` | `oklch(0.945 0.015 80)` | `oklch(0.27 0.012 60)` |
| `--muted-foreground` | `oklch(0.5 0.03 60)` | `oklch(0.72 0.02 75)` |
| `--accent` | `oklch(0.9 0.05 75)` | `oklch(0.33 0.04 70)` |
| `--accent-foreground` | `oklch(0.3 0.05 60)` | `oklch(0.93 0.015 85)` |
| `--destructive` | `oklch(0.52 0.19 27)` | `oklch(0.68 0.17 25)` |
| `--destructive-foreground` | `oklch(0.98 0.01 85)` | `oklch(0.18 0.02 40)` |
| `--success` | `oklch(0.5 0.1 150)` | `oklch(0.72 0.11 150)` |
| `--success-foreground` | `oklch(0.98 0.01 85)` | `oklch(0.18 0.02 40)` |
| `--warning` | `oklch(0.72 0.14 75)` | `oklch(0.8 0.13 80)` |
| `--warning-foreground` | `oklch(0.26 0.04 60)` | `oklch(0.22 0.03 60)` |
| `--border` | `oklch(0.86 0.02 75)` | `oklch(0.93 0.015 85 / 12%)` |
| `--input` | `oklch(0.83 0.022 75)` | `oklch(0.93 0.015 85 / 18%)` |
| `--ring` | `oklch(0.42 0.13 28)` | `oklch(0.7 0.13 32)` |
| `--chart-1` | `oklch(0.42 0.13 28)` | `oklch(0.7 0.13 32)` |
| `--chart-2` | `oklch(0.7 0.12 75)` | `oklch(0.8 0.12 80)` |
| `--chart-3` | `oklch(0.55 0.08 120)` | `oklch(0.7 0.09 125)` |
| `--chart-4` | `oklch(0.45 0.04 240)` | `oklch(0.68 0.05 240)` |
| `--chart-5` | `oklch(0.62 0.1 45)` | `oklch(0.74 0.09 50)` |
| `--overlay` | `oklch(0.15 0 0 / 45%)` | `oklch(0 0 0 / 65%)` |

Every colour lives in `tokens.css`; components and pages reference `var(--token)` only. Foreground pairs (`--x` / `--x-foreground`) are the only sanctioned text-on-fill combinations.

## Typography

Fraunces for display (600-700, tight leading 1.08, negative tracking at 40px+); Source Serif 4 for body at 18px with 1.6 leading and a 62-72ch measure; Instrument Sans for UI chrome (buttons, labels, table headers, nav) so controls read as controls; IBM Plex Mono for figures in tables and code. Kickers: `--font-ui`, `--text-xs`, uppercase, `--tracking-wide`. Use italic Source Serif for standfirsts.

| Token | Family | Weights loaded | License |
|---|---|---|---|
| `--font-display` | Fraunces | 400, 600, 700 | SIL Open Font License 1.1 |
| `--font-body` | Source Serif 4 | 400, 600 | SIL Open Font License 1.1 |
| `--font-ui` | Instrument Sans | 400, 500, 600 | SIL Open Font License 1.1 |
| `--font-mono` | IBM Plex Mono | 400, 500, 600 | SIL Open Font License 1.1 |

Scale (`--text-*`, px): xs 13 · sm 15 · base 18 · lg 21 · xl 24 · 2xl 30 · 3xl 40 · 4xl 56 · 5xl 76. Line heights: tight 1.08, snug 1.25, normal 1.6, relaxed 1.75. Tracking: tight -0.025em, normal 0em, wide 0.08em.

## Spacing & layout

Same 4px grid, used generously. Section spacing `--space-20`/`--space-24`. Text columns max 680px; wide layouts use an asymmetric 12-col grid (text on 7, marginalia on 3). Hairline rules (1px `--border`) separate sections instead of cards.

Scale (`--space-*`, px): 0=0 · px=1 · 0-5=2 · 1=4 · 1-5=6 · 2=8 · 3=12 · 4=16 · 5=20 · 6=24 · 8=32 · 10=40 · 12=48 · 16=64 · 20=80 · 24=96. Control heights: sm 32px, md 40px, lg 48px; nav bar 64px; table rows compact 40px, comfortable 52px.

## Radius

Nearly square: 2px controls, 3-4px surfaces. Images are never rounded.

`--radius-*`: sm 2px · md 3px · lg 4px · xl 6px · full 9999px. Aliases: `--radius-control` → sm, `--radius-surface` → md.

## Elevation

Paper does not float. Default is flat with rules; `--shadow-md`/`--shadow-lg` are reserved for popovers and dialogs and use a warm-tinted shadow colour.

`--shadow-xs|sm|md|lg` are defined per theme (dark themes use deeper shadows). `--card-shadow` = `none`.

## Motion

Slow and quiet: 150-420ms, long ease-out. Fades and short rises (8px). No bounces, no parallax.

Durations: fast 150ms · base 240ms · slow 420ms. Easing: standard `cubic-bezier(0.25, 0.1, 0.25, 1)` · out `cubic-bezier(0.16, 1, 0.3, 1)` · emphasis `cubic-bezier(0.65, 0, 0.35, 1)`. `prefers-reduced-motion` zeroes the durations in `tokens.css`.

## Iconography

Sparse. Lucide at 1.5px stroke, 16px, only where a word would not do (close, search, external link). Never decorative.

Library: [Lucide](https://lucide.dev) (ISC). In Pencil mode use `icon` nodes with `library: "lucide"`.

## Imagery

Photography first — documentary, natural light, full-bleed or on the grid with a caption. Duotone (ink + paper) is acceptable for archival images. Figures and charts use the chart tokens with thin 1px strokes.

## Components

Buttons are uppercase, letter-spaced Instrument Sans with square-ish corners; primary is oxblood, secondary is a paper-tone fill. Cards are open: a top rule, a kicker, a serif headline — no heavy box. Tabs use the underline variant by default. Tables use rules only, serif body, mono figures, right-aligned numerics.

- **Button** (`components/actions/Button.jsx`) — `variant`: primary|secondary|outline|ghost|destructive|link, `size`: sm|md|lg|icon, `disabled`: boolean, `type`: button|submit|reset
- **Input** (`components/forms/Input.jsx`) — `label`: string, `hint`: string, `error`: string, `size`: sm|md|lg, `type`: text|email|password|search|number|url|tel, `invalid`: boolean, `disabled`: boolean, `placeholder`: string, `id`: string
- **Card** (`components/layout/Card.jsx`) — `title`: string, `description`: string, `footer`: node, `padding`: sm|md|lg, `elevation`: flat|raised
- **Badge** (`components/data-display/Badge.jsx`) — `variant`: default|secondary|outline|success|warning|destructive
- **Tabs** (`components/navigation/Tabs.jsx`) — `items`: array, `value`: string, `defaultValue`: string, `onChange`: function, `variant`: pill|underline
- **Dialog** (`components/overlays/Dialog.jsx`) — `open`: boolean, `onClose`: function, `title`: string, `description`: string, `footer`: node, `size`: sm|md|lg, `inline`: boolean
- **Table** (`components/data-display/Table.jsx`) — `columns`: array, `rows`: array, `density`: compact|comfortable, `striped`: boolean, `caption`: string
- **Nav** (`components/navigation/Nav.jsx`) — `brand`: node, `items`: array, `actions`: node, `orientation`: horizontal|vertical, `label`: string

Each component has a `*.card.html` specimen next to it showing both themes. Styling is in `components.css` and uses tokens only.

- Do: Keep body text at `--text-base` (18px) with a 62-72ch measure.
- Do: Use one oxblood element per viewport as the focal point.
- Do: Prefer rules and whitespace over boxes.
- Don't: No rounded image corners.
- Don't: No bright saturated colours or gradients.
- Don't: No sans-serif headlines.

## Known gaps

- No drop-cap or pull-quote component yet (build with tokens: `--font-display`, `--primary`).
- Fraunces' optical-size axis is not requested from Google Fonts (wght only) to keep payload small.
- No footnote / sidenote specimen.
