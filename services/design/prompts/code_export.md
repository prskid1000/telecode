# Design → code

Used on "Convert → code", "Export React" and design→build job steps. The output is working code that
reproduces the design exactly, in the idioms of its destination.

## 1. Decide the destination first

- **Into an existing repo**: detect its framework, language, styling approach (Tailwind version,
  CSS Modules, styled-components, plain CSS), component library and token files before writing.
  Use what it uses, at the versions it has installed. If a component the design uses already exists
  in the repo, update or compose it — never add a parallel copy. Don't break its behaviour.
- **Standalone** (a new HTML board, or no repo named): React + Tailwind v4 by default, or HTML+CSS
  when the user asked for plain markup.
- Don't write docs or change logs alongside the code unless asked.

## 2. Read the design precisely

- Layer board: read it through `design_canvas_call` — `get_jsx` for the board (or `get_codegen_prompt`
  for a ready-made brief), `node_bounds` where exact geometry matters, `list_variables` for the tokens,
  and `get_components` for every component it instances.
- HTML board: read the source files; the markup and CSS already hold every value.
- Inventory before coding: each component and how many times it's used; for each instance, which
  overrides it applies. A nested element that **some** instance hides or replaces becomes an optional
  prop / slot; one no instance touches is always rendered.
- Copy exact values — text, icon names, sizes, spacing, radii, colours. Never approximate a path:
  paste its `geometry` into `<path d>` with the same `viewBox`.

## 3. Mapping layer properties

| `.pen` | CSS | Tailwind v4 |
|---|---|---|
| `layout: horizontal / vertical` | `display:flex; flex-direction: row / column` | `flex` / `flex flex-col` |
| `gap: 16` | `gap:16px` | `gap-4` (or `gap-[16px]` off-scale) |
| `padding: [10,16]` | `padding:10px 16px` | `py-[10px] px-4` |
| `justifyContent: space_between` | `justify-content:space-between` | `justify-between` |
| `alignItems: center` | `align-items:center` | `items-center` |
| `width: fill_container` (main axis) | `flex:1 1 0; min-width:0` | `flex-1 min-w-0` |
| `width: fill_container` (cross axis) | `align-self:stretch` | `self-stretch` |
| `fit_content` | natural size | (nothing) |
| fixed number | `width:320px` | `w-[320px]` |
| `clip: true` | `overflow:hidden` | `overflow-hidden` |
| `layoutPosition: absolute` + x/y | `position:absolute; left; top` on a `position:relative` parent | `absolute left-[..] top-[..]` + `relative` parent |
| `textGrowth: auto` | `white-space:nowrap` | `whitespace-nowrap` |
| `textGrowth: fixed-width` | wraps within its width | (default wrapping) |
| `lineHeight: 1.4` | `line-height:1.4` | `leading-[1.4]` |
| `cornerRadius: [8,8,0,0]` | `border-radius:8px 8px 0 0` | `rounded-t-lg` / arbitrary |
| `stroke` + `strokeAlignment: inner` | `border` (or `box-shadow: inset 0 0 0 Npx`) | `border` / `ring-inset` |
| `effect: shadow` | `box-shadow` | `shadow-[…]` |
| `background_blur` | `backdrop-filter:blur()` | `backdrop-blur-[…]` |
| `icon` (lucide) | the icon component / inline SVG from the same set | `lucide-react` in React projects |
| image fill `mode: fill / fit` | `object-fit: cover / contain` | `object-cover` / `object-contain` |
| `enabled: false` | not rendered | conditional render |

## 4. Tokens

- Variables become CSS custom properties with the same names (`$accent` → `var(--accent)`); themed
  variables become one `:root` set plus `[data-theme="dark"]` (or the repo's dark-mode mechanism).
- Tailwind v4: define tokens once — raw values in `:root`, exposed to utilities with
  `@theme inline { --color-accent: var(--accent); --font-display: var(--font-display); … }` — then
  use `bg-accent`, `font-display`. Don't hardcode a hex that has a token.
- Fonts: load them the way the repo does (`next/font`, a `<link>`, local `@font-face` from the design
  system's `fonts/`). Quote family names with spaces.

## 5. Components

- One component per design component, named after it (`Button/Primary` → `Button` with
  `variant="primary"`). Props cover every override any instance uses; typed props in TS projects.
- Slots become `children` or named render props.
- Carry states the design implies even when only one is drawn: hover, focus-visible, active,
  disabled, loading, empty, error.
- Semantic elements: buttons are `<button>`, links `<a>`, inputs have labels, headings keep their
  order. The design's layer names are a good source for accessible names.

## 6. Verify

Build or type-check if the repo has a command for it. Render the result (the Preview view, or the
repo's dev server if the user runs it) and compare it with the design at the design's own size:
positions, spacing, type, colour, states. Check that `fill_container` areas stretch and
`fit_content` areas hug when the viewport changes, that nothing scrolls horizontally, and that the
console is clean. Fix mismatches component by component before moving on.
