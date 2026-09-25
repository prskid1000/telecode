<!-- Portions adapted from nexu-io/open-design (Apache-2.0): the knob catalogue and the
     curated-swatch rule (§4). The message names and EDITMODE markers are kept for Claude Design
     import compatibility. See NOTICE. -->

# Tweaks — live controls inside an HTML board

The toolbar has a **Tweaks** toggle. When it is on, your page shows a small panel of controls that
change the design live — colours, type, density, copy, layout variants, feature flags. You design the
panel; it lives inside the page. When the toggle is off the page must look final: no panel, no handle.

## 1. Protocol

Order matters — register the listener **before** announcing, or the host's activate message can
arrive before anything is listening and the toggle silently does nothing.

```js
// 1. listen
window.addEventListener('message', (e) => {
  const t = e.data && e.data.type;
  if (t === '__activate_edit_mode') setTweaksOpen(true);
  if (t === '__deactivate_edit_mode') setTweaksOpen(false);
});
// 2. announce (only now) — visible:false because the panel starts hidden
window.parent.postMessage({ type: '__edit_mode_available', visible: false }, '*');

// 3. on every change: apply live, then persist the changed keys only
function setTweak(key, value) {
  applyTweak(key, value);
  window.parent.postMessage({ type: '__edit_mode_set_keys', edits: { [key]: value } }, '*');
}

// 4. if the user closes the panel from inside (× or Esc)
function closeTweaks() {
  setTweaksOpen(false);
  window.parent.postMessage({ type: '__edit_mode_dismissed' }, '*');
}
```

## 2. Persisted defaults

The host persists tweak values by rewriting them **in the source file**, not in browser storage.
Declare the defaults once, in an inline `<script>` in the **entry HTML file**, between the markers:

```js
const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "accent": "cobalt",
  "typePair": "serif-sans",
  "density": "normal",
  "heroLayout": "split",
  "showTestimonials": true
}/*EDITMODE-END*/;
```

- The text between the markers is strict JSON (double-quoted keys and strings, no comments, no
  trailing comma). Exactly **one** such block per project entry file.
- On `__edit_mode_set_keys` the host merges the keys, rewrites the block and the change survives
  reload. Read initial state from `TWEAK_DEFAULTS`; don't also store tweaks in `localStorage`.
- Keys are stable camelCase; values are what the design reads (preset ids, numbers, booleans).

## 3. The panel

- Title it **Tweaks**. A compact floating panel bottom-right (≈280px wide), or inline handles next to
  the thing being varied. Collapsible; never covering the primary action on small boards.
- Group controls by what they change; label each with the design consequence ("Hero layout"), not
  the implementation ("--hero-grid").
- Controls: segmented buttons or a select for variants, curated swatches for colour, a stepped
  slider for density or scale, a switch for feature flags, a short text field for copy.
- It is part of the design — style it with the design's own tokens, quietly.

## 4. What to expose

- If the user asked for variants of one element (three hero treatments, four button styles), make
  them a single Tweak that cycles the options in place.
- If the user asked for nothing, add **two or three** anyway — the ones that reveal something
  (a bolder layout, an alternative type pairing, a denser mode). Don't overbuild; five is the ceiling.
- Colour: curated swatches (4–6) that all work with the design — never a free colour picker.
- Type scale and density: three steps each (e.g. 0.9 / 1 / 1.15); wider ranges break layouts.
- Motion: Off / Subtle / Lively; honour `prefers-reduced-motion` by defaulting to Off.
- Light / dark: only if both are genuinely designed.
- Structure the CSS so a tweak flips a few custom properties or a data attribute
  (`<html data-density="tight">`), not dozens of inline styles.

Test every control once: apply it, reload, confirm it persisted and the layout held. Remove any
control that breaks the layout rather than shipping it.

## 5. Layer boards

Layer boards don't run code. The equivalent of a Tweak there is a variable or theme axis
(`set_variables`) plus a board per variant; say so if the user asks for live controls on a layer board.
