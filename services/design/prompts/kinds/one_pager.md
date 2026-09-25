# Kind: one-pager

A single page that must work in two places: on screen and printed or saved as PDF. Product
sheets, event flyers, briefs, KPI summaries, case studies, CVs.

**Default board**: HTML board at page size — A4 794×1123 or US Letter 816×1056 CSS px (96dpi);
ask which if the audience's region is unclear.

## Structure

- Decide the one thing a reader must take away in five seconds; make it the largest element.
- Scan path: headline → key visual or number → 2–4 supporting blocks → call to action / contact.
- Use a real grid (6 or 12 columns with a consistent gutter); every block snaps to it.
- Leave margins that survive printers: ≥ 12mm (≈ 45px) on all sides.

## Print contract

```css
@page { size: A4; margin: 0; }            /* or: size: letter; */
html, body { width: 210mm; }              /* 8.5in for Letter */
.page { width: 210mm; height: 297mm; box-sizing: border-box; padding: 12mm; overflow: hidden; }
@media print { .no-print { display: none; } * { -webkit-print-color-adjust: exact; print-color-adjust: exact; } }
```

- Body text ≥ 9pt on paper (≥ 12px on screen equivalents); captions ≥ 7.5pt; anything a reader must
  act on (URL, phone, date) ≥ 10pt.
- Avoid huge solid colour areas unless the brand demands them (ink cost, banding).
- Everything must fit the page exactly — no second page unless the brief says "two pages". If it
  doesn't fit, cut content with the user, don't shrink type.
- Links print as visible text (short URL or QR placeholder), not "click here".

## Data-driven one-pagers

KPI or status sheets refreshed by routines: keep the data in one inline JSON block at the top of the
file (`<script type="application/json" id="td-data">`) and render from it, so a routine can update
numbers without touching layout. Always show the data's as-of date.

## Done when

The page renders at its paper size with nothing clipped, prints to exactly one page, and the summary
states the paper size and the one takeaway it is built around.
