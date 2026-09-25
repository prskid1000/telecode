# Playful

> Rounded, saturated consumer system: cream background, coral primary, lavender and lemon supporting colours, Fredoka display with Nunito text, pill buttons and springy motion. For consumer apps, onboarding, education and campaigns.
> Default theme: **light** (the other theme is `[data-theme="dark"]`).

## Overview / Voice

Playful is chunky and warm: a cream page, bold coral actions, candy-coloured supporting fills and soft "stacked" shadows (a hard offset plus a soft blur) that make surfaces feel tactile. Everything is rounded; controls are pills.

**Voice:** friendly, encouraging, second person. Short sentences, contractions, one emoji at most per screen (never in buttons). Celebrate completions; never blame the user in errors ("That code didn't work — try again?").

## Colour

Coral (`--primary`) is the action colour; lavender (`--secondary`) is for secondary actions and selected states; lemon (`--accent`) is a highlight fill for badges, stickers and marker-style emphasis behind words. Use colour in blocks — a whole card in `--secondary` is fine — but keep one coral CTA per view. Coral sits at 0.57 lightness so white labels just clear 4.5:1; buttons are still 800 weight so the label reads at small sizes.

| Token | Light | Dark |
|---|---|---|
| `--background` | `oklch(0.985 0.012 95)` | `oklch(0.2 0.04 290)` |
| `--foreground` | `oklch(0.25 0.04 290)` | `oklch(0.97 0.01 95)` |
| `--card` | `oklch(1 0 0)` | `oklch(0.25 0.05 290)` |
| `--card-foreground` | `oklch(0.25 0.04 290)` | `oklch(0.97 0.01 95)` |
| `--popover` | `oklch(1 0 0)` | `oklch(0.27 0.055 290)` |
| `--popover-foreground` | `oklch(0.25 0.04 290)` | `oklch(0.97 0.01 95)` |
| `--primary` | `oklch(0.57 0.2 28)` | `oklch(0.72 0.18 30)` |
| `--primary-foreground` | `oklch(0.99 0.005 95)` | `oklch(0.2 0.04 290)` |
| `--secondary` | `oklch(0.92 0.06 300)` | `oklch(0.34 0.09 300)` |
| `--secondary-foreground` | `oklch(0.35 0.13 300)` | `oklch(0.95 0.02 300)` |
| `--muted` | `oklch(0.955 0.02 95)` | `oklch(0.28 0.045 290)` |
| `--muted-foreground` | `oklch(0.5 0.04 290)` | `oklch(0.76 0.03 290)` |
| `--accent` | `oklch(0.93 0.12 100)` | `oklch(0.86 0.15 100)` |
| `--accent-foreground` | `oklch(0.32 0.06 90)` | `oklch(0.22 0.04 90)` |
| `--destructive` | `oklch(0.58 0.22 20)` | `oklch(0.7 0.19 22)` |
| `--destructive-foreground` | `oklch(0.99 0.005 95)` | `oklch(0.2 0.04 290)` |
| `--success` | `oklch(0.53 0.14 152)` | `oklch(0.76 0.16 152)` |
| `--success-foreground` | `oklch(0.99 0.005 95)` | `oklch(0.2 0.04 290)` |
| `--warning` | `oklch(0.82 0.16 78)` | `oklch(0.85 0.15 82)` |
| `--warning-foreground` | `oklch(0.3 0.06 60)` | `oklch(0.24 0.05 70)` |
| `--border` | `oklch(0.9 0.025 290)` | `oklch(1 0 0 / 12%)` |
| `--input` | `oklch(0.86 0.03 290)` | `oklch(1 0 0 / 18%)` |
| `--ring` | `oklch(0.57 0.2 28)` | `oklch(0.72 0.18 30)` |
| `--chart-1` | `oklch(0.66 0.19 30)` | `oklch(0.72 0.18 30)` |
| `--chart-2` | `oklch(0.6 0.2 300)` | `oklch(0.7 0.18 300)` |
| `--chart-3` | `oklch(0.86 0.16 100)` | `oklch(0.88 0.15 100)` |
| `--chart-4` | `oklch(0.75 0.14 165)` | `oklch(0.8 0.13 165)` |
| `--chart-5` | `oklch(0.7 0.13 235)` | `oklch(0.76 0.12 235)` |
| `--overlay` | `oklch(0.15 0 0 / 45%)` | `oklch(0 0 0 / 65%)` |

Every colour lives in `tokens.css`; components and pages reference `var(--token)` only. Foreground pairs (`--x` / `--x-foreground`) are the only sanctioned text-on-fill combinations.

## Typography

Fredoka (600-700) for display and numbers — round terminals match the radii; Nunito for body (400) and UI (700-800); DM Mono for codes and vouchers. Base 17px. Headlines are short and can break onto two lines deliberately.

| Token | Family | Weights loaded | License |
|---|---|---|---|
| `--font-display` | Fredoka | 500, 600, 700 | SIL Open Font License 1.1 |
| `--font-body` | Nunito | 400, 600, 700, 800 | SIL Open Font License 1.1 |
| `--font-ui` | Nunito | 400, 600, 700, 800 | SIL Open Font License 1.1 |
| `--font-mono` | DM Mono | 400, 500 | SIL Open Font License 1.1 |

Scale (`--text-*`, px): xs 13 · sm 15 · base 17 · lg 19 · xl 22 · 2xl 28 · 3xl 36 · 4xl 48 · 5xl 64. Line heights: tight 1.1, snug 1.3, normal 1.55, relaxed 1.7. Tracking: tight -0.01em, normal 0em, wide 0.03em.

## Spacing & layout

Roomy 4px grid: component padding `--space-4`-`--space-6`, card gaps `--space-6`, section spacing `--space-20`. Touch targets >= 44px (the md control height).

Scale (`--space-*`, px): 0=0 · px=1 · 0-5=2 · 1=4 · 1-5=6 · 2=8 · 3=12 · 4=16 · 5=20 · 6=24 · 8=32 · 10=40 · 12=48 · 16=64 · 20=80 · 24=96. Control heights: sm 36px, md 44px, lg 52px; nav bar 68px; table rows compact 44px, comfortable 56px.

## Radius

10 / 14 / 20 / 28px; controls use `--radius-full` (pills); cards and sheets use 28px. Images get `--radius-lg` or are cut into blobs with SVG masks.

`--radius-*`: sm 10px · md 14px · lg 20px · xl 28px · full 9999px. Aliases: `--radius-control` → full, `--radius-surface` → xl.

## Elevation

"Stacked" shadows: a hard 2-10px downward offset in foreground at 8-10% plus a soft blur, like a sticker lifted off paper. Buttons press down (translateY 2px, shadow shrinks) on :active.

`--shadow-xs|sm|md|lg` are defined per theme (dark themes use deeper shadows). `--card-shadow` = `var(--shadow-md)`.

## Motion

Springy: `--ease-emphasis` (overshoot) for entrances, toggles, checkmarks and counters; 140-360ms. Allowed: small rotations (±3°) on stickers, confetti on completion. Respect reduced motion by switching springs to `--ease-standard`.

Durations: fast 140ms · base 220ms · slow 360ms. Easing: standard `cubic-bezier(0.3, 0, 0.2, 1)` · out `cubic-bezier(0.22, 1, 0.36, 1)` · emphasis `cubic-bezier(0.34, 1.56, 0.64, 1)`. `prefers-reduced-motion` zeroes the durations in `tokens.css`.

## Iconography

Lucide at 2-2.25px stroke with round caps, 20-24px, often inside a 40px tinted circle (`--secondary` or `--accent`).

Library: [Lucide](https://lucide.dev) (ISC). In Pencil mode use `icon` nodes with `library: "lucide"`.

## Imagery

Flat, colourful illustration and cut-out photography on tinted blocks. People smiling at the camera are fine here. Avoid dark or moody photos.

## Components

Pill buttons with heavy labels and a stacked shadow; primary coral, secondary lavender, outline uses a 2px border. Inputs are 44px, 2px border, pill-ish (`--radius-md`). Cards are white on cream with 28px radius and stacked shadow. Badges are lemon/lavender stickers. Tabs default to pill. Tables are comfortable density with zebra striping.

- **Button** (`components/actions/Button.jsx`) — `variant`: primary|secondary|outline|ghost|destructive|link, `size`: sm|md|lg|icon, `disabled`: boolean, `type`: button|submit|reset
- **Input** (`components/forms/Input.jsx`) — `label`: string, `hint`: string, `error`: string, `size`: sm|md|lg, `type`: text|email|password|search|number|url|tel, `invalid`: boolean, `disabled`: boolean, `placeholder`: string, `id`: string
- **Card** (`components/layout/Card.jsx`) — `title`: string, `description`: string, `footer`: node, `padding`: sm|md|lg, `elevation`: flat|raised
- **Badge** (`components/data-display/Badge.jsx`) — `variant`: default|secondary|outline|success|warning|destructive
- **Tabs** (`components/navigation/Tabs.jsx`) — `items`: array, `value`: string, `defaultValue`: string, `onChange`: function, `variant`: pill|underline
- **Dialog** (`components/overlays/Dialog.jsx`) — `open`: boolean, `onClose`: function, `title`: string, `description`: string, `footer`: node, `size`: sm|md|lg, `inline`: boolean
- **Table** (`components/data-display/Table.jsx`) — `columns`: array, `rows`: array, `density`: compact|comfortable, `striped`: boolean, `caption`: string
- **Nav** (`components/navigation/Nav.jsx`) — `brand`: node, `items`: array, `actions`: node, `orientation`: horizontal|vertical, `label`: string

Each component has a `*.card.html` specimen next to it showing both themes. Styling is in `components.css` and uses tokens only.

- Do: Keep one coral CTA per view.
- Do: Use tinted circles behind icons.
- Do: Round everything — no square corners anywhere.
- Don't: No thin (<400) weights.
- Don't: No grey-on-grey low-contrast text.
- Don't: No emoji inside buttons or form labels.

## Known gaps

- No illustration set is bundled — pick an OFL/CC0 set per project.
- Confetti / celebration is described, not a component.
- Coral + white is only just above 4.5:1; do not set small regular-weight coral text on white either.
