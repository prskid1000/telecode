<!-- Portions adapted from nexu-io/open-design (Apache-2.0): the interaction-state contrast rules
     (§2). See NOTICE. -->

# Accessibility

Target WCAG 2.2 AA in every artifact, including prototypes — a prototype that can't be used with a
keyboard teaches the wrong thing.

## 1. Perceivable

- Contrast: text ≥ 4.5:1, large text and meaningful icons/graphics ≥ 3:1, focus indicators ≥ 3:1
  against adjacent colours.
- Never colour alone for meaning — add text, icon, pattern or position.
- Meaningful images get accurate `alt`; decorative ones `alt=""`. Icons-only buttons get an
  `aria-label`.
- Text resizes to 200% without loss; layouts reflow at 320px CSS width.

## 2. States keep their contrast

Define foreground and background for every state as a **pair** — default, hover, focus, active,
selected, disabled. After any state change, text contrast must be at least what it was. Hover changes
background, border, shadow or position — never fades the text toward the background. Disabled is the
only state allowed to lose contrast, and it must still look disabled rather than invisible.

## 3. Operable

- Everything clickable is reachable and operable by keyboard, in reading order; no keyboard traps;
  `Esc` closes overlays and returns focus to the trigger.
- Visible `:focus-visible` ring on every focusable element (2px, offset 2px, ≥ 3:1).
- Targets ≥ 44×44 (iOS / web touch), 48×48dp (Android), ≥ 24×24 with spacing for dense desktop UI.
- Respect `prefers-reduced-motion`: remove parallax, large movement and auto-play; keep instant
  state changes. Nothing flashes more than three times a second.
- No time limits without a way to extend; carousels and auto-advancing content can pause.

## 4. Understandable and robust

- Use the element that means the thing: `<button>` for actions, `<a href>` for navigation,
  `<label for>` on every input, `<fieldset>`/`<legend>` for groups, headings in order (one `h1`),
  landmarks (`header`, `nav`, `main`, `footer`).
- Custom widgets (tabs, menus, comboboxes, dialogs) follow their ARIA pattern — roles, states
  (`aria-selected`, `aria-expanded`), and keyboard model — or use native elements instead.
- Errors say what's wrong and how to fix it, next to the field, announced (`aria-live` or
  `aria-describedby`); don't clear the user's input.
- Set `lang` on `<html>`; don't disable zoom.

## 5. Layer boards

Accessibility still applies to specs: contrast via variables, target sizes, a visible focus variant
for every interactive component, and names that will become accessible labels in code.
