# Midnight

> Dark-first product/SaaS system: blue-black surfaces, an indigo primary with a cyan secondary signal, Manrope throughout. For app shells, dashboards, developer-facing marketing and anything shown on a projector.
> Default theme: **dark** (the other theme is `[data-theme="light"]`).

## Overview / Voice

Midnight is designed dark and translated to light, not the other way round — `:root` holds the dark values. Surfaces step up in lightness (background 0.16 → card 0.20 → popover 0.22) instead of relying on shadows, borders are white at 9% so they read on any surface, and a single indigo primary does the pointing.

**Voice:** crisp, confident, technical-but-human. Short headlines (<= 8 words), concrete verbs, numbers over adjectives. Product names and features in sentence case.

## Colour

Indigo (`--primary`) for primary actions, selection and focus; cyan (`--chart-2`) is the secondary signal for charts and highlights and must not be used for buttons. Status colours are bright enough to read on 0.16 lightness. In light mode the primary deepens to 0.52 to hold 4.5:1 on white. Hero glows are allowed only as a radial gradient of `--primary` at <= 25% opacity behind a headline — one per page.

| Token | Light | Dark |
|---|---|---|
| `--background` | `oklch(0.985 0.004 270)` | `oklch(0.16 0.02 270)` |
| `--foreground` | `oklch(0.2 0.03 270)` | `oklch(0.96 0.01 270)` |
| `--card` | `oklch(1 0 0)` | `oklch(0.2 0.025 270)` |
| `--card-foreground` | `oklch(0.2 0.03 270)` | `oklch(0.96 0.01 270)` |
| `--popover` | `oklch(1 0 0)` | `oklch(0.22 0.028 270)` |
| `--popover-foreground` | `oklch(0.2 0.03 270)` | `oklch(0.96 0.01 270)` |
| `--primary` | `oklch(0.52 0.2 275)` | `oklch(0.56 0.2 275)` |
| `--primary-foreground` | `oklch(0.99 0.005 270)` | `oklch(0.99 0.005 270)` |
| `--secondary` | `oklch(0.95 0.01 270)` | `oklch(0.26 0.03 270)` |
| `--secondary-foreground` | `oklch(0.25 0.03 270)` | `oklch(0.95 0.01 270)` |
| `--muted` | `oklch(0.955 0.008 270)` | `oklch(0.24 0.025 270)` |
| `--muted-foreground` | `oklch(0.5 0.03 270)` | `oklch(0.7 0.03 270)` |
| `--accent` | `oklch(0.94 0.03 275)` | `oklch(0.3 0.06 275)` |
| `--accent-foreground` | `oklch(0.3 0.1 275)` | `oklch(0.96 0.01 270)` |
| `--destructive` | `oklch(0.58 0.22 25)` | `oklch(0.66 0.21 22)` |
| `--destructive-foreground` | `oklch(0.99 0.005 270)` | `oklch(0.16 0.02 270)` |
| `--success` | `oklch(0.52 0.13 160)` | `oklch(0.74 0.16 160)` |
| `--success-foreground` | `oklch(0.99 0.005 270)` | `oklch(0.18 0.03 160)` |
| `--warning` | `oklch(0.72 0.15 75)` | `oklch(0.8 0.15 80)` |
| `--warning-foreground` | `oklch(0.22 0.04 70)` | `oklch(0.2 0.04 70)` |
| `--border` | `oklch(0.91 0.01 270)` | `oklch(1 0 0 / 9%)` |
| `--input` | `oklch(0.88 0.012 270)` | `oklch(1 0 0 / 14%)` |
| `--ring` | `oklch(0.52 0.2 275)` | `oklch(0.66 0.19 275)` |
| `--chart-1` | `oklch(0.52 0.2 275)` | `oklch(0.66 0.19 275)` |
| `--chart-2` | `oklch(0.62 0.12 210)` | `oklch(0.78 0.13 200)` |
| `--chart-3` | `oklch(0.58 0.14 160)` | `oklch(0.74 0.16 160)` |
| `--chart-4` | `oklch(0.72 0.15 75)` | `oklch(0.8 0.15 80)` |
| `--chart-5` | `oklch(0.58 0.2 330)` | `oklch(0.68 0.2 330)` |
| `--overlay` | `oklch(0.15 0 0 / 45%)` | `oklch(0 0 0 / 65%)` |

Every colour lives in `tokens.css`; components and pages reference `var(--token)` only. Foreground pairs (`--x` / `--x-foreground`) are the only sanctioned text-on-fill combinations.

## Typography

Manrope everywhere (700-800 for display, 600 for UI, 400-500 for body) with slightly negative tracking; JetBrains Mono for keys, IDs, code and metric deltas. Base size 15px — Manrope is wide, 15 reads like 16 elsewhere. Display tracking -0.03em.

| Token | Family | Weights loaded | License |
|---|---|---|---|
| `--font-display` | Manrope | 400, 500, 600, 700, 800 | SIL Open Font License 1.1 |
| `--font-body` | Manrope | 400, 500, 600, 700, 800 | SIL Open Font License 1.1 |
| `--font-ui` | Manrope | 400, 500, 600, 700, 800 | SIL Open Font License 1.1 |
| `--font-mono` | JetBrains Mono | 400, 500 | SIL Open Font License 1.1 |

Scale (`--text-*`, px): xs 12 · sm 14 · base 15 · lg 17 · xl 20 · 2xl 24 · 3xl 32 · 4xl 44 · 5xl 60. Line heights: tight 1.1, snug 1.3, normal 1.55, relaxed 1.7. Tracking: tight -0.03em, normal -0.005em, wide 0.04em.

## Spacing & layout

4px grid; app shells at `--space-4`/`--space-6` padding, marketing sections at `--space-24`. 12-col grid, 1240px max width, 24px gutters. Sidebar 240px, top bar `--nav-h` (60px).

Scale (`--space-*`, px): 0=0 · px=1 · 0-5=2 · 1=4 · 1-5=6 · 2=8 · 3=12 · 4=16 · 5=20 · 6=24 · 8=32 · 10=40 · 12=48 · 16=64 · 20=80 · 24=96. Control heights: sm 32px, md 36px, lg 44px; nav bar 60px; table rows compact 36px, comfortable 48px.

## Radius

8px controls, 12px cards, 16px dialogs and hero media. Badges are pills.

`--radius-*`: sm 6px · md 8px · lg 12px · xl 16px · full 9999px. Aliases: `--radius-control` → md, `--radius-surface` → lg.

## Elevation

Lightness steps first, shadow second. In dark mode shadows include a 1px white 4-7% outline ring so raised surfaces separate from the background; in light mode shadows are cool-tinted.

`--shadow-xs|sm|md|lg` are defined per theme (dark themes use deeper shadows). `--card-shadow` = `none`.

## Motion

180ms standard, 320ms for panels and dialogs with `--ease-out`; one springy `--ease-emphasis` for toggles and success states. Keep list animations under 40ms stagger.

Durations: fast 120ms · base 180ms · slow 320ms. Easing: standard `cubic-bezier(0.2, 0, 0, 1)` · out `cubic-bezier(0.16, 1, 0.3, 1)` · emphasis `cubic-bezier(0.34, 1.3, 0.64, 1)`. `prefers-reduced-motion` zeroes the durations in `tokens.css`.

## Iconography

Lucide, 16/20px, 1.75px stroke, `currentColor`; icons in `--muted-foreground` unless active.

Library: [Lucide](https://lucide.dev) (ISC). In Pencil mode use `icon` nodes with `library: "lucide"`.

## Imagery

Product UI screenshots on `--card` with a 1px border and `--radius-xl`; abstract imagery limited to the single primary glow. No photos of people at laptops.

## Components

Primary buttons are indigo with white text; secondary is a raised `--secondary` surface; ghost is the default for toolbars. Inputs sit on `--background` with a translucent `--input` border and an indigo ring. Cards are `--card` with a border, no shadow, 12px radius. Tabs default to pill. Tables are compact with sticky headers in `--muted`.

- **Button** (`components/actions/Button.jsx`) — `variant`: primary|secondary|outline|ghost|destructive|link, `size`: sm|md|lg|icon, `disabled`: boolean, `type`: button|submit|reset
- **Input** (`components/forms/Input.jsx`) — `label`: string, `hint`: string, `error`: string, `size`: sm|md|lg, `type`: text|email|password|search|number|url|tel, `invalid`: boolean, `disabled`: boolean, `placeholder`: string, `id`: string
- **Card** (`components/layout/Card.jsx`) — `title`: string, `description`: string, `footer`: node, `padding`: sm|md|lg, `elevation`: flat|raised
- **Badge** (`components/data-display/Badge.jsx`) — `variant`: default|secondary|outline|success|warning|destructive
- **Tabs** (`components/navigation/Tabs.jsx`) — `items`: array, `value`: string, `defaultValue`: string, `onChange`: function, `variant`: pill|underline
- **Dialog** (`components/overlays/Dialog.jsx`) — `open`: boolean, `onClose`: function, `title`: string, `description`: string, `footer`: node, `size`: sm|md|lg, `inline`: boolean
- **Table** (`components/data-display/Table.jsx`) — `columns`: array, `rows`: array, `density`: compact|comfortable, `striped`: boolean, `caption`: string
- **Nav** (`components/navigation/Nav.jsx`) — `brand`: node, `items`: array, `actions`: node, `orientation`: horizontal|vertical, `label`: string

Each component has a `*.card.html` specimen next to it showing both themes. Styling is in `components.css` and uses tokens only.

- Do: Start from `[data-theme="dark"]`; check the light theme second.
- Do: Use lightness steps (background → card → popover) to express depth.
- Do: Keep cyan for data, indigo for action.
- Don't: No pure black (#000) backgrounds.
- Don't: No more than one glow per page.
- Don't: No coloured text on coloured surfaces other than the documented foreground pairs.

## Known gaps

- No command-palette, sidebar or toast specimens yet.
- Light theme is a translation and has had less review than dark.
- Glow effect is described, not tokenised.
