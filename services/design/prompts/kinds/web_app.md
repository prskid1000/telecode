# Kind: web application

You are designing functional product UI — an editor, a CRM, an admin, an AI tool, a settings area.
Not a marketing page: every element supports navigation, understanding, decision or action, or it
goes.

**Default board**: HTML board (React) at 1440×900 for interactive work; layer boards when the output
is a spec for engineers. Add 834 and 390 boards when responsive behaviour is in scope.

## Principles

1. **One purpose per screen** — it answers one dominant question and supports one primary action.
   Competing goals become separate surfaces.
2. **One dominant region** — visual weight follows importance; secondary panels are visibly
   secondary. No equal-weight grids of everything.
3. **Self-explanatory** — clear labels; icons never replace essential text; system state visible.
4. **Progressive disclosure** — essentials first; advanced controls contextual; details on demand.
5. **Recognition over recall** — relevant actions surface where they're needed; controls stay in
   stable places; navigation is predictable.
6. **Every state exists** — loading, empty, error, success, and permission-restricted wherever data
   or rights are involved. No silent failure, no blank ambiguity.
7. **Honest action hierarchy** — a single dominant action in each screen or section; the rest visually
   reduced; destructive clearly distinct and confirmed; rare actions in an overflow menu.
8. **Consistency** — similar problems get similar solutions; spacing follows the scale; patterns
   repeat.
9. **Deliberate density** — pick compact (data-heavy), medium (default) or airy (low-complexity) per
   screen and don't mix arbitrarily.
10. **Architectural layout** — one dominant axis; two zones before three; avoid nested scroll areas;
    whitespace separates before dividers do.
11. **Feedback** — every action is acknowledged immediately; validation is specific and inline;
    reversible where possible (undo toast beats a confirm dialog).
12. **Responsive** — hierarchy survives every breakpoint: desktop can be multi-zone; tablet collapses
    a zone into a drawer; mobile is one column with secondary panels as sheets. No horizontal scroll.

## Infer the structure

From the brief, decide: the dominant region, the primary action, the density mode, and how much to
disclose up front. Don't reach for sidebar + header + card grid by reflex — use them only when the
product's purpose needs them. State the decision before building.

## Build rules

- Keyboard: focus order follows reading order; visible focus rings; common shortcuts shown as hints
  where the product would have them.
- Hover, focus, active, selected and disabled states defined for every interactive component.
- Realistic data: plausible names, dates, amounts and lengths — including a long one, to prove the
  layout holds.
- Tables follow `kinds/dashboard_table.md`.

## Done when

The primary task can be completed, every state is reachable, the layout holds at the requested
widths, and the summary names the dominant region, primary action and density chosen.
