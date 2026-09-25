# HTML boards — files, React and the sandbox

An HTML board renders one project file in an iframe on the canvas. You author the file; the host
serves it, sandboxes it, and bridges comments, inspection, tweaks and slide state across the iframe
boundary.

## 1. The sandbox you are writing for

The page is served from a separate **preview origin** (its own port, never the TeleDesign API),
inside an iframe with `sandbox="allow-scripts allow-same-origin allow-popups allow-forms"` — same-origin
to the preview port only — under a strict CSP. Consequences you must design around:
- **No external network.** `fetch`/XHR may only read files of this project (relative URLs); any other
  host is blocked by `connect-src 'self'`. Put data inline (a JS array, a
  `<script type="application/json">` block) or in a project file. Scripts load only from the pinned CDN URLs below
  {{#allowed_cdns}}and {{allowed_cdns}}{{/allowed_cdns}}; fonts from Google Fonts or the design
  system's `fonts/`.
- **Storage is shared and may throw.** `localStorage` works but is shared by every project on the
  preview origin — prefix keys with the project/file name — and can still raise in exported copies.
  Wrap every access in `try/catch` and work without it. Positions (current slide, playhead,
  selected tab) are also mirrored in the URL hash (`#slide=4`, `#t=12.5`) and reported to the host,
  which restores them on reload.
- **No modal dialogs or popups.** `alert`, `confirm`, `prompt`, `window.open` and `target="_blank"`
  do nothing. Build in-page dialogs and toasts.
- **Forms** must `preventDefault()` and simulate the result in state.
- Never call `scrollIntoView` — it scrolls the host page. Use `element.scrollTo` / `scrollTop` on the
  scrolling container.
- The page must load with **zero console errors**. Guard optional APIs; the verifier reads the console.

## 2. Files

```
checkout.html          entry file behind the board (descriptive name)
checkout/app.jsx       components, split by responsibility, each < 1000 lines
checkout/data.js       inline sample data
checkout/checkout.css  styles (or a <style> block in the entry)
assets/…               images, logos, fonts copied into the project
```

Link pages with relative `<a href="receipt.html">`. Each page the user should see on the canvas gets
its own board; a multi-page prototype may add a small `index.html` launcher board.

## 3. Plain HTML or React

Use plain HTML + CSS (+ a little vanilla JS) for static and lightly interactive pages. Use React when
there are components, state, or many screens. When you use React, use exactly these tags — pinned
versions, with integrity and crossorigin:

```html
<script src="https://unpkg.com/react@18.3.1/umd/react.development.js"
        integrity="{{sri_react}}" crossorigin="anonymous"></script>
<script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.development.js"
        integrity="{{sri_react_dom}}" crossorigin="anonymous"></script>
<script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js"
        integrity="{{sri_babel}}" crossorigin="anonymous"></script>
```

Then load your own files in dependency order:

```html
<script type="text/babel" src="checkout/primitives.jsx"></script>
<script type="text/babel" src="checkout/steps.jsx"></script>
<script type="text/babel" src="checkout/app.jsx"></script>
```

Rules that prevent the classic breakages:
- **Every Babel script has its own scope.** A component defined in one file is invisible to the
  next unless exported. End each shared file with
  `Object.assign(window, { Button, Field, Stepper });`.
- **Unique names for module-level objects.** Two files each declaring `const styles = {…}` collide.
  Name them after the component — `const stepperStyles = {…}` — or use inline styles / CSS classes.
  Never declare a bare `styles`.
- No `type="module"`, no `import`/`export` statements, no bundler assumptions.
- **No object rest in destructuring** (`const { a, ...rest } = props`). Babel standalone emits its
  helper variable (`_excluded`) at global scope, so the second file that does it throws
  "Identifier '_excluded' has already been declared". Wrap each file in an IIFE and copy props
  explicitly (a small `omit(props, keys)` helper) instead.
{{#ds_bundle}}
- The design system ships a compiled bundle: `<script src="{{ds_bundle}}"></script>` before your
  files exposes its components on `window`. Use them instead of re-implementing.
{{/ds_bundle}}

## 4. Anchors and labels

- Put `data-td-id="kebab-case"` on everything the user is likely to point at: page regions
  (`header`, `main`, `section`, `nav`, `aside`, `footer`), headings, buttons, links, form controls,
  cards and list items, key images. Skip spacers, dividers and pure decoration.
- Ids are unique per file and descriptive: `hero-title`, `plan-card-pro`, `faq-item-refunds`.
  Repeated items get distinct suffixes. In React, derive them from data (`plan-card-${plan.id}`).
- **Never rename an existing `data-td-id`** in a revision — comments and inspector edits are bound to
  it. Add new ids for new elements.
- Label every screen and slide root with `data-td-screen="03 Payment"` — two-digit, **1-indexed**,
  matching what the user sees in counters. "Slide 5" always means the fifth slide, label `05`.
- Keep an anchored element's visual properties in one place (its class rule or its inline style).
  The inspector writes changes back to that place deterministically; properties scattered across
  several selectors force the change back through you.

## 5. Viewport and scale

The board's `width`×`height` is the iframe viewport. Responsive pages fill it with sensible
margins; don't add a title screen or centre a tiny mock in a sea of background. Fixed-size content
(decks, animations, device mockups) scales itself to fit with `transform: scale()` and keeps its
controls outside the scaled element.

## 6. Starters

Ready-made scaffolds are available — copy them into the project and build on them rather than
hand-rolling bezels, deck shells or timelines:

{{starters}}

| Starter | Use for | Load as |
|---|---|---|
| `deck_stage.js` | Any slide deck (implements the contract in `deck.md`) | `<script src>` |
| `design_canvas.jsx` | 2+ static options side by side inside one board | `text/babel` |
| `ios_frame.jsx` / `android_frame.jsx` | A phone screen that must look like a device | `text/babel` |
| `macos_window.jsx` / `browser_window.jsx` | Desktop app or website chrome | `text/babel` |
| `animations.jsx` | Timeline animation: stage, sprites, easing, scrubber, `window.tdTimeline` | `text/babel` |

Pass the extension exactly as listed — a `.js` starter loaded through Babel (or the reverse) breaks.

## 7. Probing the live preview

When a comment or `<mentioned-element>` doesn't pin down the source element, probe before editing:
`design_eval_js` runs an expression in the user's open preview and returns its value;
`design_screenshot` captures it (optionally several states: `{"states": [{"js": "go(3)"}, …]}`, up
to 12). A quick probe beats a wrong edit. Don't use these for routine self-verification.

## 8. Host bridge (for reference — don't reimplement)

The host injects its own bridge script. It stamps `data-td-live` handles, reports comment targets
(`td:comment-target`), applies inspector edits (`td:apply-style`), relays slide state
(`td:slide` / `td:slide-changed`) and provides `window.telecode.complete()`. Your page must not
intercept or re-post `td:*` messages except as `deck.md` and `tweaks.md` specify.
