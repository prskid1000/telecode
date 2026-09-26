# CODING AGENTS: READ THIS FIRST

You have been handed a **design bundle** exported from TeleDesign for the project
**{{project_title}}** ({{exported_at}}). Someone designed and iterated on this with a design agent;
your job is to build it for real in the target codebase.

## Do these three things before writing any code

1. **Read the conversations in `chats/` — all {{chat_count}} of them, oldest first.** The files in
   `project/` show where the design ended up; the chats show *why*: what was asked for, what was
   rejected, which variation won, and which constraints the user insisted on. Decisions made late in
   the chats override anything that looks different in older files.
2. **Read the primary design in full: `project/{{primary_file}}`** (canvas board
   "{{primary_board}}"). It was open when the handoff was triggered, so it is almost certainly what
   the user wants built. Then open everything it loads — components, stylesheets, scripts, data —
   so you understand how it fits together.
3. **Confirm scope with the user if anything is unclear** — which screens, which variation, what is
   in and out. A two-line question now is cheaper than rebuilding later.

## What these files are

The designs are **prototypes**, written in HTML/CSS/JS (React 18 via in-browser Babel) and, for
layer boards, as canvas documents (`project/docs/<id>.fig`, open them in the TeleDesign editor or any
open-pencil build; `docs/<id>.fig.json` is a readable JSON rendering of each, and per-board JSX exports
sit next to them when the bundle includes them). They are a precise specification of the intended
*result*, not code to paste. Rebuild them in the target stack's own idioms — its framework, component
library, routing, state management and styling system — and match the visual output exactly:
spacing, sizes, colours, type, radii, states and motion.

- Every value you need is in the source. Read the CSS and markup rather than rendering pages or
  taking screenshots, unless the user asks you to.
- Prototype plumbing is not part of the product: the Tweaks panel and its `/*EDITMODE-BEGIN*/` block,
  `data-td-*` attributes, deck controls, starter frames (device bezels, browser chrome) and inline
  sample data exist for design review. Carry over what they *mean* (the chosen tweak values, the real
  screens), not the mechanism.
- `data-td-screen` labels name screens and slides; the chats refer to them by those names.
- Placeholder blocks marked as placeholders are gaps the user knows about — ask what goes there.
{{#ds_note}}

## Design system

{{ds_note}} Tokens are in `project/_ds/…/tokens.css` (and `tokens.json`). Map them onto the target
codebase's existing token system rather than hardcoding values; if the codebase already has an
equivalent token, use it.
{{/ds_note}}

## Bundle contents

```
{{bundle_tree}}
```

- `README.md` — this file.
- `chats/` — conversation transcripts, one file per design turn. **Start here.**
- `project/` — the design files: HTML boards and their components, `docs/*.fig` (the canvases), `assets/`,
  `assets.json` (deliverables and their review status — `approved` items are the ones signed off).
- `project/_ds/` — the design system the project used, if any.
- `uploads/` — material the user supplied (screenshots, brand files, documents).
