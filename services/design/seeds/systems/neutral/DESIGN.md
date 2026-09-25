# Neutral

> The default system: shadcn/ui's neutral oklch palette, Inter, 10px base radius. Calm, product-first, works for almost any brief that has no brand of its own.
> Default theme: **light** (the other theme is `[data-theme="dark"]`).

> Colour values: [shadcn/ui](https://github.com/shadcn-ui/ui) neutral theme (MIT). Guidance partly adapted from open-design's `default` system (Apache-2.0). See `../../NOTICE`.

## Overview / Voice

Neutral is the system TeleDesign attaches when a project has none. It takes shadcn/ui's neutral theme as-is (every colour value below comes from shadcn/ui's MIT-licensed v4 registry) and adds the pieces a design agent needs that a component library leaves implicit: a type scale, a spacing scale, elevation, motion and written rules.

**Voice:** calm, functional, quietly confident. Content first, chrome second. Sentence case everywhere; verbs on buttons ("Save changes", not "OK"). No exclamation marks in UI copy.

## Colour

Achromatic by design: the primary is near-black in light mode and near-white in dark, so hierarchy is carried by value, not hue. The only chromatic tokens are the status colours (destructive / success / warning). If a brief wants a brand colour, change `--primary` and `--ring` only — everything else is built to stay grey. Never pure black text on pure white at display sizes >= 48px; use `--foreground` (0.145) which is already slightly lifted.

| Token | Light | Dark |
|---|---|---|
| `--background` | `oklch(1 0 0)` | `oklch(0.145 0 0)` |
| `--foreground` | `oklch(0.145 0 0)` | `oklch(0.985 0 0)` |
| `--card` | `oklch(1 0 0)` | `oklch(0.205 0 0)` |
| `--card-foreground` | `oklch(0.145 0 0)` | `oklch(0.985 0 0)` |
| `--popover` | `oklch(1 0 0)` | `oklch(0.205 0 0)` |
| `--popover-foreground` | `oklch(0.145 0 0)` | `oklch(0.985 0 0)` |
| `--primary` | `oklch(0.205 0 0)` | `oklch(0.922 0 0)` |
| `--primary-foreground` | `oklch(0.985 0 0)` | `oklch(0.205 0 0)` |
| `--secondary` | `oklch(0.97 0 0)` | `oklch(0.269 0 0)` |
| `--secondary-foreground` | `oklch(0.205 0 0)` | `oklch(0.985 0 0)` |
| `--muted` | `oklch(0.97 0 0)` | `oklch(0.269 0 0)` |
| `--muted-foreground` | `oklch(0.556 0 0)` | `oklch(0.708 0 0)` |
| `--accent` | `oklch(0.97 0 0)` | `oklch(0.269 0 0)` |
| `--accent-foreground` | `oklch(0.205 0 0)` | `oklch(0.985 0 0)` |
| `--destructive` | `oklch(0.577 0.245 27.325)` | `oklch(0.704 0.191 22.216)` |
| `--destructive-foreground` | `oklch(0.985 0 0)` | `oklch(0.145 0 0)` |
| `--success` | `oklch(0.527 0.137 150.069)` | `oklch(0.696 0.17 162.48)` |
| `--success-foreground` | `oklch(0.985 0 0)` | `oklch(0.205 0 0)` |
| `--warning` | `oklch(0.769 0.165 70.08)` | `oklch(0.828 0.189 84.429)` |
| `--warning-foreground` | `oklch(0.279 0.077 45.635)` | `oklch(0.279 0.077 45.635)` |
| `--border` | `oklch(0.922 0 0)` | `oklch(1 0 0 / 10%)` |
| `--input` | `oklch(0.922 0 0)` | `oklch(1 0 0 / 15%)` |
| `--ring` | `oklch(0.708 0 0)` | `oklch(0.556 0 0)` |
| `--chart-1` | `oklch(0.87 0 0)` | `oklch(0.87 0 0)` |
| `--chart-2` | `oklch(0.556 0 0)` | `oklch(0.556 0 0)` |
| `--chart-3` | `oklch(0.439 0 0)` | `oklch(0.439 0 0)` |
| `--chart-4` | `oklch(0.371 0 0)` | `oklch(0.371 0 0)` |
| `--chart-5` | `oklch(0.269 0 0)` | `oklch(0.269 0 0)` |
| `--overlay` | `oklch(0.15 0 0 / 45%)` | `oklch(0 0 0 / 65%)` |

Every colour lives in `tokens.css`; components and pages reference `var(--token)` only. Foreground pairs (`--x` / `--x-foreground`) are the only sanctioned text-on-fill combinations.

## Typography

One family (Inter) for display, body and UI; JetBrains Mono for code and tabular IDs. Weight does the work: 600 for headings, 500 for controls and labels, 400 for body. No more than three sizes on one screen. Tighten tracking (`--tracking-tight`) at 30px and up.

| Token | Family | Weights loaded | License |
|---|---|---|---|
| `--font-display` | Inter | 400, 500, 600, 700 | SIL Open Font License 1.1 |
| `--font-body` | Inter | 400, 500, 600, 700 | SIL Open Font License 1.1 |
| `--font-ui` | Inter | 400, 500, 600, 700 | SIL Open Font License 1.1 |
| `--font-mono` | JetBrains Mono | 400, 500 | SIL Open Font License 1.1 |

Scale (`--text-*`, px): xs 12 · sm 14 · base 16 · lg 18 · xl 20 · 2xl 24 · 3xl 30 · 4xl 36 · 5xl 48. Line heights: tight 1.2, snug 1.35, normal 1.5, relaxed 1.65. Tracking: tight -0.02em, normal 0em, wide 0.02em.

## Spacing & layout

4px grid. Inside components use `--space-2`..`--space-4`; between groups `--space-6`/`--space-8`; between page sections `--space-16`..`--space-24`. Layout: 12 columns, 1200px max content width, 24px gutters; 8 columns at 640-1023px; 4 columns below 640px.

Scale (`--space-*`, px): 0=0 · px=1 · 0-5=2 · 1=4 · 1-5=6 · 2=8 · 3=12 · 4=16 · 5=20 · 6=24 · 8=32 · 10=40 · 12=48 · 16=64 · 20=80 · 24=96. Control heights: sm 32px, md 36px, lg 40px; nav bar 56px; table rows compact 36px, comfortable 48px.

## Radius

Derived from shadcn's `--radius: 0.625rem`: sm 6, md 8 (controls), lg 10, xl 14 (cards, dialogs). Pills (`--radius-full`) only for badges and avatars.

`--radius-*`: sm 6px · md 8px · lg 10px · xl 14px · full 9999px. Aliases: `--radius-control` → md, `--radius-surface` → xl.

## Elevation

Mostly flat: cards use a 1px `--border` and `--shadow-xs`. Popovers and menus use `--shadow-md`, dialogs `--shadow-lg`. No glassmorphism, no coloured shadows.

`--shadow-xs|sm|md|lg` are defined per theme (dark themes use deeper shadows). `--card-shadow` = `var(--shadow-xs)`.

## Motion

Short and functional: 100-250ms, `--ease-standard`. Animate opacity and transform only. Respect `prefers-reduced-motion` by dropping transforms and keeping fades.

Durations: fast 100ms · base 150ms · slow 250ms. Easing: standard `cubic-bezier(0.2, 0, 0, 1)` · out `cubic-bezier(0, 0, 0.2, 1)` · emphasis `cubic-bezier(0.3, 0, 0, 1.2)`. `prefers-reduced-motion` zeroes the durations in `tokens.css`.

## Iconography

Lucide, 16px in controls and 20px in navigation, 1.5-2px stroke, `currentColor`. Icons sit left of labels with `--space-2` gap. Icon-only buttons need an `aria-label`.

Library: [Lucide](https://lucide.dev) (ISC). In Pencil mode use `icon` nodes with `library: "lucide"`.

## Imagery

Product screenshots and plain photography with neutral grading. No stock illustrations, no gradient blobs. Placeholder imagery is a `--muted` block with a centred caption, never a fake photo.

## Components

Primary buttons are solid `--primary`; secondary is `--secondary`; outline is a 1px `--border` on `--background`; ghost has no chrome until hover. Inputs are 36px with a 1px `--input` border and a 3px `--ring` at 50% on focus. Tables are borderless except for row dividers. Tabs default to a pill track on `--muted`.

- **Button** (`components/actions/Button.jsx`) — `variant`: primary|secondary|outline|ghost|destructive|link, `size`: sm|md|lg|icon, `disabled`: boolean, `type`: button|submit|reset
- **Input** (`components/forms/Input.jsx`) — `label`: string, `hint`: string, `error`: string, `size`: sm|md|lg, `type`: text|email|password|search|number|url|tel, `invalid`: boolean, `disabled`: boolean, `placeholder`: string, `id`: string
- **Card** (`components/layout/Card.jsx`) — `title`: string, `description`: string, `footer`: node, `padding`: sm|md|lg, `elevation`: flat|raised
- **Badge** (`components/data-display/Badge.jsx`) — `variant`: default|secondary|outline|success|warning|destructive
- **Tabs** (`components/navigation/Tabs.jsx`) — `items`: array, `value`: string, `defaultValue`: string, `onChange`: function, `variant`: pill|underline
- **Dialog** (`components/overlays/Dialog.jsx`) — `open`: boolean, `onClose`: function, `title`: string, `description`: string, `footer`: node, `size`: sm|md|lg, `inline`: boolean
- **Table** (`components/data-display/Table.jsx`) — `columns`: array, `rows`: array, `density`: compact|comfortable, `striped`: boolean, `caption`: string
- **Nav** (`components/navigation/Nav.jsx`) — `brand`: node, `items`: array, `actions`: node, `orientation`: horizontal|vertical, `label`: string

Each component has a `*.card.html` specimen next to it showing both themes. Styling is in `components.css` and uses tokens only.

- Do: Let whitespace separate groups; add dividers only between unrelated sections.
- Do: Use at most one primary button per view.
- Do: Do not invent colour values outside the token set — if a brief needs one, leave a visible TODO comment and use the closest token.
- Don't: No gradients on surfaces.
- Don't: No drop shadows on inputs.
- Don't: No more than three type sizes per screen.

## Known gaps

- No sidebar tokens (shadcn ships `--sidebar-*`; use `--card`/`--muted` instead).
- Chart palette is greyscale (shadcn neutral default); pick a chromatic chart set per project if data needs to be distinguished by hue.
- `--success` / `--warning` / `--destructive-foreground` are TeleDesign additions, not shadcn tokens.
- No data-viz, date-picker, toast or combobox specimens yet.
