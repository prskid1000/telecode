# Motion

Motion explains change — where something came from, what it became, what caused it. If an animation
explains nothing, remove it.

## Timing

| Change | Duration |
|---|---|
| Press / hover feedback | 80–120ms |
| Small UI (toggle, checkbox, tooltip) | 120–180ms |
| Panels, menus, cards expanding | 180–260ms |
| Page / large-area transitions | 250–400ms |
| Storytelling beats (animation kind) | as the content needs; hold text ≥ 1.5s |

Exits are ~20% faster than entrances. Anything over 400ms in product UI needs a reason.

## Easing

- Entrances and responses: ease-out (`cubic-bezier(.2, .7, .2, 1)`).
- Exits: ease-in (`cubic-bezier(.4, 0, 1, 1)`).
- Moves within the screen: ease-in-out (`cubic-bezier(.45, 0, .2, 1)`).
- Springs for direct manipulation (drag, sheets) — critically damped, no wobble in productivity UI.
- Never linear for UI movement (only for progress and continuous rotation).

## Rules

- Animate `transform` and `opacity`; avoid animating layout properties (`width`, `top`, `height`) on
  large elements. Use `view-transition` or FLIP for layout changes.
- Keep distances short (8–24px slides); scale from 0.96–0.98, not from 0.
- Stagger lists lightly (20–40ms per item, cap the total at ~300ms).
- One orchestrated moment per page (a hero entrance, a count-up) beats motion everywhere.
- Loading: skeletons shaped like the content for waits over ~300ms; spinners only for short
  indeterminate actions; never block the whole screen for a local action.
- `prefers-reduced-motion: reduce` → replace movement with a quick fade or an instant change.
- Put durations and easings in tokens (`--dur-fast`, `--ease-out`) so Tweaks and the design system
  can control them.
