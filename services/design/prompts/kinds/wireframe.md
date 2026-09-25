# Kind: wireframes and storyboards

You are exploring **ideas**, not polishing one. Wireframes let the user compare many structural
approaches quickly, before anyone argues about colour.

**Default board**: layer boards (fast to rearrange, easy for the user to edit). An HTML board is
fine for a clickable low-fi flow.

## Approach

- Breadth first: for the key screen or problem, produce **4–8 distinct structural ideas**, not three
  shades of one. Vary the model — list vs. canvas vs. conversation vs. wizard; content-first vs.
  action-first; one screen vs. progressive steps.
- Then depth for the promising ones: the flow as a storyboard row (step boards left→right, a `note`
  under each saying what the user does and sees).
- Label every board with the idea's name and one line of intent (`Search / C — Conversational:
  query refines through chips`).

## Visual language (keep it deliberately rough)

- Greys only: one background, 2–3 greys for blocks and lines, black text. A single highlight colour
  (for the element under discussion) at most.
- One plain sans at 3–4 sizes. No shadows, gradients, imagery or icons beyond simple glyphs.
- Real headings and real labels — lorem ipsum hides the actual design questions. Longer copy can be
  grey lines, but label what they stand for ("3 lines: delivery promise").
- Placeholder boxes for images with an X or a label ("Product photo, 4:3").
- Annotate decisions and open questions in `note` nodes beside the board, not inside it.

## Layer-board specifics

- Build shared pieces (nav bar, list row, card) as components first so ideas can reuse them.
- Keep the placeholder flag on while a board is in progress; clear it per board.
- Run the layout problems check per board — overlaps look like intent in a wireframe and mislead.

## Done when

The ideas are laid out in rows the user can scan, each named and annotated, and the summary
recommends the two most promising with one sentence each on why.
