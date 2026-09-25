# Deck contract

Every HTML deck in TeleDesign follows one contract, so the host can navigate it, sync speaker notes,
export PDF/PPTX one page per slide and restore the position after a reload. If the `deck_stage.js`
starter is listed in `{{starters}}`, copy it and only author slides. Otherwise implement exactly the
skeleton below — don't invent your own scaling, navigation or print logic.

## 1. Structure

```html
<body>
  <div id="deck-stage" data-td-deck data-td-w="1920" data-td-h="1080">
    <div class="deck-canvas">
      <section data-td-slide data-td-screen="01 Title" data-td-id="slide-title">…</section>
      <section data-td-slide data-td-screen="02 Problem" data-td-id="slide-problem">…</section>
    </div>
    <nav class="deck-controls" aria-label="Slide controls">
      <button data-td-id="deck-prev" aria-label="Previous slide">‹</button>
      <span class="deck-count" aria-live="polite">1 / 12</span>
      <button data-td-id="deck-next" aria-label="Next slide">›</button>
    </nav>
  </div>
</body>
```

- One `<section data-td-slide>` per slide, direct children of `.deck-canvas`, in order.
- Design canvas: **1920×1080** (16:9) unless the brief says otherwise; change `data-td-w/h` to match.
- Slides are labelled `data-td-screen="NN Name"` — two digits, 1-indexed. The counter shows
  `index / total` with the same numbering.
- Controls live **outside** the scaled canvas so they stay usable at any size.

## 2. Behaviour

- **Scale to fit**: the canvas is laid out at its native size and scaled with
  `transform: scale(min(vw / W, vh / H))`, centred, letterboxed on the deck background colour.
- **Keys**: → ↓ Space PageDown = next; ← ↑ PageUp = previous; Home / End = first / last.
  Clicking the right / left third of the stage also navigates. Ignore keys while focus is in an
  input.
- **Position**: current slide in the hash (`#slide=4`, 1-indexed), read on load; also mirrored to
  `localStorage` inside `try/catch`.
- **Host messages**: post `{type:"td:slide-changed", index, count, total}` (1-indexed; `count` and `total` are the same number, both sent for compatibility) to `window.parent`
  on load and on every change. Accept `{type:"td:slide", action:"next"|"prev"|"first"|"last"|"go", index}`.
- **Speaker notes** — only when the user asked for them. Add to `<head>`:
  `<script type="application/json" id="td-speaker-notes">["Slide 1 script…", "Slide 2 script…"]</script>`
  One entry per slide, in order, written as a full conversational script. With notes, slides can
  carry less text.
- **Print**: `@page { size: 1920px 1080px; margin: 0 }`; every slide prints as exactly one page,
  unscaled, all visible; controls hidden.

## 3. Reference skeleton

```html
<style>
  html, body { margin: 0; height: 100%; background: var(--deck-bg, #111); overflow: hidden; }
  #deck-stage { position: fixed; inset: 0; }
  .deck-canvas { position: absolute; left: 50%; top: 50%; transform-origin: 0 0; }
  [data-td-slide] { position: absolute; inset: 0; width: var(--w); height: var(--h);
                    box-sizing: border-box; display: none; overflow: hidden; }
  [data-td-slide].is-active { display: block; }
  .deck-controls { position: fixed; right: 16px; bottom: 16px; display: flex; gap: 8px;
                   align-items: center; font: 14px/1 system-ui; color: #fff; opacity: .7; }
  .deck-controls button { min-width: 44px; min-height: 44px; }
  @media print {
    @page { size: 1920px 1080px; margin: 0; }
    html, body { overflow: visible; height: auto; background: none; }
    #deck-stage, .deck-canvas { position: static; transform: none !important; height: auto !important; }
    [data-td-slide] { display: block !important; position: relative; page-break-after: always;
                      break-after: page; }
    .deck-controls { display: none; }
  }
</style>
<script>
(() => {
  const stage = document.getElementById('deck-stage');
  const canvas = stage.querySelector('.deck-canvas');
  const slides = [...canvas.querySelectorAll(':scope > [data-td-slide]')];
  const W = +stage.dataset.tdW || 1920, H = +stage.dataset.tdH || 1080;
  const key = 'td-deck:' + location.pathname;
  const count = stage.querySelector('.deck-count');
  canvas.style.setProperty('--w', W + 'px'); canvas.style.setProperty('--h', H + 'px');
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';

  const fit = () => {
    const s = Math.min(innerWidth / W, innerHeight / H);
    canvas.style.transform = `scale(${s}) translate(-50%, -50%)`;
  };
  const read = () => {
    const m = location.hash.match(/slide=(\d+)/);
    if (m) return +m[1];
    try { return +localStorage.getItem(key) || 1; } catch { return 1; }
  };
  let cur = 1;
  const go = (n) => {
    cur = Math.max(1, Math.min(slides.length, n));
    slides.forEach((el, i) => el.classList.toggle('is-active', i === cur - 1));
    count.textContent = `${cur} / ${slides.length}`;
    history.replaceState(null, '', '#slide=' + cur);
    try { localStorage.setItem(key, cur); } catch {}
    parent.postMessage({ type: 'td:slide-changed', index: cur, count: slides.length, total: slides.length }, '*');
  };

  addEventListener('resize', fit);
  addEventListener('keydown', (e) => {
    if (e.target.closest('input, textarea, select, [contenteditable]')) return;
    if (['ArrowRight', 'ArrowDown', 'PageDown', ' '].includes(e.key)) { e.preventDefault(); go(cur + 1); }
    if (['ArrowLeft', 'ArrowUp', 'PageUp'].includes(e.key)) { e.preventDefault(); go(cur - 1); }
    if (e.key === 'Home') go(1);
    if (e.key === 'End') go(slides.length);
  });
  stage.addEventListener('click', (e) => {
    if (e.target.closest('button, a, input, [data-no-nav]')) return;
    const x = e.clientX / innerWidth;
    if (x > 2 / 3) go(cur + 1); else if (x < 1 / 3) go(cur - 1);
  });
  stage.querySelector('[data-td-id="deck-prev"]').onclick = () => go(cur - 1);
  stage.querySelector('[data-td-id="deck-next"]').onclick = () => go(cur + 1);
  addEventListener('message', (e) => {
    const d = e.data || {};
    if (d.type !== 'td:slide') return;
    if (d.action === 'next') go(cur + 1);
    else if (d.action === 'prev') go(cur - 1);
    else if (d.action === 'first') go(1);
    else if (d.action === 'last') go(slides.length);
    else if (d.action === 'go') go(+d.index);
  });
  fit(); go(read());
})();
</script>
```

## 4. Slide design system

Before writing slides, decide and state the deck's system: title treatment, section-divider layout,
body layout(s), image layouts (full-bleed, split), data layout, closing layout. Use it to create
rhythm on purpose — a different background for section dividers, full-bleed image slides where the
imagery matters. One or two background colours for the whole deck, no more. Never three slides of the
same layout in a row unless they form a sequence.

Deck text is never below 24px on a 1920×1080 canvas; titles 44px and up. If content doesn't fit at
those sizes, split the slide or cut words — never shrink the type. Keep content at least 96px from
the edges.

## 5. Decks as layer boards

A deck may instead be a row of 1920×1080 layer boards named `Deck / NN Name`. Present mode shows the
row as slides; PDF export writes one page per board. Use layer boards when the user wants to edit
slides node by node or hand them off as specs; use the HTML contract when they need notes, animation
or live data.
