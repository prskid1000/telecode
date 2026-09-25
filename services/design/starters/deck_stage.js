/*
 * deck_stage.js — TeleDesign starter: slide-deck shell implementing prompts/deck.md.
 * Load with a plain <script src="deck_stage.js"></script> (NOT text/babel). Original code.
 *
 * Author only the slides:
 *
 *   <deck-stage width="1920" height="1080">
 *     <section data-td-slide data-td-screen="01 Title" data-td-id="slide-title">…</section>
 *     <section data-td-slide data-td-screen="02 Problem" data-td-id="slide-problem">…</section>
 *   </deck-stage>
 *
 * The element (light DOM, no shadow root) builds the deck.md structure around them:
 *   <deck-stage id="deck-stage" data-td-deck data-td-w data-td-h>
 *     <div class="deck-canvas"> …your sections… </div>
 *     <nav class="deck-controls"> ‹  1 / 12  › </nav>
 *   </deck-stage>
 * A hand-written `<div id="deck-stage" data-td-deck>` skeleton (deck.md §1) without its own script
 * is enhanced the same way, so both spellings behave identically.
 *
 * Behaviour (deck.md §2): scale-to-fit letterboxed on --deck-bg (default #111); → ↓ Space PageDown
 * next, ← ↑ PageUp previous, Home/End first/last, click the right/left third of the stage;
 * position in #slide=N (1-indexed) + localStorage (try/catch); posts
 * {type:"td:slide-changed", index, count, total} to the host on load and on every change; accepts
 * {type:"td:slide", action:"next"|"prev"|"first"|"last"|"go", index}; print = one unscaled page
 * per slide (@page sized to the deck), controls hidden. Speaker notes: optional
 * <script type="application/json" id="td-speaker-notes">["…", …]</script> in <head>.
 *
 * Attributes: width / height (or data-td-w / data-td-h), background (letterbox colour),
 * no-controls, no-click-nav. Mark any element data-no-nav to exempt it from click navigation.
 * Script API: stage.go(n) · stage.next() · stage.prev() · stage.index · stage.count · stage.notes
 * and the same on window.tdDeck. Event: "slidechange" {detail:{index, count}} on the stage.
 */
(function () {
  'use strict';
  if (window.__tdDeckStage) return;
  window.__tdDeckStage = true;

  var BASE_CSS = [
    'html,body{margin:0;height:100%;background:var(--deck-bg,#111);overflow:hidden}',
    'deck-stage,#deck-stage{position:fixed;inset:0;display:block;background:var(--deck-bg,#111);overflow:hidden;',
    '-webkit-tap-highlight-color:transparent}',
    '#deck-stage>.deck-canvas{position:absolute;left:50%;top:50%;transform-origin:0 0;',
    'box-shadow:0 0 0 1px rgba(255,255,255,.03)}',
    '#deck-stage [data-td-slide]{position:absolute;inset:0;width:var(--w);height:var(--h);box-sizing:border-box;',
    'display:none;overflow:hidden}',
    '#deck-stage [data-td-slide].is-active{display:block}',
    '#deck-stage>.deck-controls{position:fixed;right:16px;bottom:16px;display:flex;gap:4px;align-items:center;',
    'padding:4px;border-radius:999px;background:rgba(20,20,22,.72);-webkit-backdrop-filter:blur(12px);',
    'backdrop-filter:blur(12px);box-shadow:0 1px 0 rgba(255,255,255,.06) inset,0 8px 24px rgba(0,0,0,.28);',
    'font:500 13px/1 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:#f4f4f5;',
    'opacity:.6;transition:opacity .2s ease;z-index:10;user-select:none}',
    '#deck-stage>.deck-controls:hover,#deck-stage>.deck-controls:focus-within,#deck-stage[data-show-controls]>.deck-controls{opacity:1}',
    '#deck-stage>.deck-controls button{all:unset;box-sizing:border-box;min-width:44px;min-height:44px;',
    'border-radius:999px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:inherit;',
    'font-size:22px;line-height:1}',
    '#deck-stage>.deck-controls button:hover{background:rgba(255,255,255,.1)}',
    '#deck-stage>.deck-controls button:focus-visible{outline:2px solid #60a5fa;outline-offset:-2px}',
    '#deck-stage>.deck-controls button:disabled{opacity:.3;cursor:default;background:none}',
    '#deck-stage>.deck-controls .deck-count{min-width:64px;text-align:center;font-variant-numeric:tabular-nums;',
    'letter-spacing:.02em;padding:0 4px}',
    '#deck-stage[no-controls]>.deck-controls{display:none}',
    '@media print{',
    'html,body{overflow:visible!important;height:auto!important;background:none!important}',
    'deck-stage,#deck-stage,#deck-stage>.deck-canvas{position:static!important;transform:none!important;',
    'width:auto!important;height:auto!important;overflow:visible!important;box-shadow:none!important;background:none!important}',
    '#deck-stage [data-td-slide]{display:block!important;position:relative!important;inset:auto!important;',
    'width:var(--w)!important;height:var(--h)!important;break-after:page;page-break-after:always;break-inside:avoid;',
    '-webkit-print-color-adjust:exact;print-color-adjust:exact}',
    '#deck-stage [data-td-slide]:last-of-type{break-after:auto;page-break-after:auto}',
    '#deck-stage>.deck-controls{display:none!important}',
    '}'
  ].join('');

  function injectCss(W, H) {
    try {
      if (!document.getElementById('deck-stage-css')) {
        var st = document.createElement('style');
        st.id = 'deck-stage-css';
        st.textContent = BASE_CSS;
        (document.head || document.documentElement).insertBefore(st, (document.head || document.documentElement).firstChild);
      }
      var page = document.getElementById('deck-stage-page');
      if (!page) {
        page = document.createElement('style');
        page.id = 'deck-stage-page';
        (document.head || document.documentElement).appendChild(page);
      }
      page.textContent = '@media print{@page{size:' + W + 'px ' + H + 'px;margin:0}}';
    } catch (e) { /* ignore */ }
  }

  function two(n) { return (n < 10 ? '0' : '') + n; }

  function readNotes() {
    try {
      var el = document.getElementById('td-speaker-notes') || document.getElementById('speaker-notes');
      if (!el) return [];
      var v = JSON.parse(el.textContent || '[]');
      return Array.isArray(v) ? v.map(String) : [];
    } catch (e) {
      return [];
    }
  }

  function Deck(stage) {
    this.stage = stage;
    this.cur = 0;
    this.slides = [];
    this.notes = [];
    this._built = false;
  }

  Deck.prototype.build = function () {
    if (this._built) return;
    this._built = true;
    var stage = this.stage;
    var self = this;
    if (!document.getElementById('deck-stage') || document.getElementById('deck-stage') === stage) stage.id = 'deck-stage';
    stage.setAttribute('data-td-deck', '');
    var W = +(stage.getAttribute('data-td-w') || stage.getAttribute('width')) || 1920;
    var H = +(stage.getAttribute('data-td-h') || stage.getAttribute('height')) || 1080;
    stage.setAttribute('data-td-w', W);
    stage.setAttribute('data-td-h', H);
    this.W = W;
    this.H = H;
    var bg = stage.getAttribute('background');
    if (bg) document.documentElement.style.setProperty('--deck-bg', bg);
    injectCss(W, H);

    var canvas = null;
    for (var c = stage.firstElementChild; c; c = c.nextElementSibling) {
      if (c.classList.contains('deck-canvas')) { canvas = c; break; }
    }
    if (!canvas) {
      canvas = document.createElement('div');
      canvas.className = 'deck-canvas';
      var move = [];
      for (var k = stage.firstElementChild; k; k = k.nextElementSibling) {
        if (k.matches('[data-td-slide], section')) move.push(k);
      }
      move.forEach(function (n) { canvas.appendChild(n); });
      stage.insertBefore(canvas, stage.firstChild);
    }
    this.canvas = canvas;
    canvas.style.setProperty('--w', W + 'px');
    canvas.style.setProperty('--h', H + 'px');
    canvas.style.width = W + 'px';
    canvas.style.height = H + 'px';

    var slides = [];
    for (var s = canvas.firstElementChild; s; s = s.nextElementSibling) {
      if (s.hasAttribute('data-td-slide')) slides.push(s);
    }
    if (!slides.length) {
      for (var s2 = canvas.firstElementChild; s2; s2 = s2.nextElementSibling) {
        if (s2.tagName === 'SECTION') { s2.setAttribute('data-td-slide', ''); slides.push(s2); }
      }
    }
    slides.forEach(function (el, i) {
      if (!el.hasAttribute('data-td-screen')) el.setAttribute('data-td-screen', two(i + 1));
      el.setAttribute('aria-roledescription', 'slide');
      el.setAttribute('aria-label', (i + 1) + ' of ' + slides.length);
    });
    this.slides = slides;
    this.notes = readNotes();

    var controls = null;
    for (var n = stage.firstElementChild; n; n = n.nextElementSibling) {
      if (n.classList.contains('deck-controls')) { controls = n; break; }
    }
    if (!controls && !stage.hasAttribute('no-controls')) {
      controls = document.createElement('nav');
      controls.className = 'deck-controls';
      controls.setAttribute('aria-label', 'Slide controls');
      controls.innerHTML =
        '<button type="button" data-td-id="deck-prev" aria-label="Previous slide">&#8249;</button>' +
        '<span class="deck-count" aria-live="polite"></span>' +
        '<button type="button" data-td-id="deck-next" aria-label="Next slide">&#8250;</button>';
      stage.appendChild(controls);
    }
    this.controls = controls;
    this.countEl = controls ? controls.querySelector('.deck-count') : null;
    this.prevBtn = controls ? controls.querySelector('[data-td-id="deck-prev"]') : null;
    this.nextBtn = controls ? controls.querySelector('[data-td-id="deck-next"]') : null;
    if (this.prevBtn) this.prevBtn.addEventListener('click', function (e) { e.stopPropagation(); self.go(self.cur - 1); });
    if (this.nextBtn) this.nextBtn.addEventListener('click', function (e) { e.stopPropagation(); self.go(self.cur + 1); });

    this.key = 'td-deck:' + location.pathname;
    this.fit = this.fit.bind(this);
    window.addEventListener('resize', this.fit);
    try {
      if (window.ResizeObserver) new ResizeObserver(this.fit).observe(stage);
    } catch (e) { /* ignore */ }

    window.addEventListener('keydown', function (e) {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      var t = e.target;
      if (t && t.closest && t.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])')) return;
      var k = e.key;
      if (k === 'ArrowRight' || k === 'ArrowDown' || k === 'PageDown' || k === ' ' || k === 'Spacebar') { e.preventDefault(); self.go(self.cur + 1); }
      else if (k === 'ArrowLeft' || k === 'ArrowUp' || k === 'PageUp') { e.preventDefault(); self.go(self.cur - 1); }
      else if (k === 'Home') { e.preventDefault(); self.go(1); }
      else if (k === 'End') { e.preventDefault(); self.go(self.slides.length); }
    });
    if (!stage.hasAttribute('no-click-nav')) {
      stage.addEventListener('click', function (e) {
        if (e.defaultPrevented) return;
        var t = e.target;
        if (t && t.closest && t.closest('button, a, input, select, textarea, label, summary, [contenteditable], [data-no-nav], .deck-controls')) return;
        try { if (String(window.getSelection() || '').length) return; } catch (err) { /* ignore */ }
        var r = stage.getBoundingClientRect();
        var x = (e.clientX - r.left) / (r.width || 1);
        if (x > 2 / 3) self.go(self.cur + 1);
        else if (x < 1 / 3) self.go(self.cur - 1);
      });
    }
    window.addEventListener('message', function (e) {
      var d = e.data;
      if (!d || d.type !== 'td:slide') return;
      if (e.source && e.source !== window.parent) return;
      if (d.action === 'next') self.go(self.cur + 1);
      else if (d.action === 'prev') self.go(self.cur - 1);
      else if (d.action === 'first') self.go(1);
      else if (d.action === 'last') self.go(self.slides.length);
      else if (d.action === 'go') self.go(+d.index);
    });
    window.addEventListener('hashchange', function () {
      var n2 = self.fromHash();
      if (n2 && n2 !== self.cur) self.go(n2);
    });
    // Reveal the controls briefly when the pointer moves.
    var hideTimer = null;
    stage.addEventListener('pointermove', function () {
      stage.setAttribute('data-show-controls', '');
      clearTimeout(hideTimer);
      hideTimer = setTimeout(function () { stage.removeAttribute('data-show-controls'); }, 1600);
    });

    this.fit();
    this.go(this.read(), true);
  };

  Deck.prototype.fit = function () {
    var r = this.stage.getBoundingClientRect();
    var vw = r.width || window.innerWidth, vh = r.height || window.innerHeight;
    var s = Math.min(vw / this.W, vh / this.H);
    if (!isFinite(s) || s <= 0) s = 1;
    this.scale = s;
    this.canvas.style.transform = 'scale(' + s + ') translate(-50%, -50%)';
  };

  Deck.prototype.fromHash = function () {
    var m = /(?:^#|[#&])slide=(\d+)/.exec(location.hash || '');
    return m ? +m[1] : 0;
  };

  Deck.prototype.read = function () {
    var h = this.fromHash();
    if (h) return h;
    try { return +localStorage.getItem(this.key) || 1; } catch (e) { return 1; }
  };

  Deck.prototype.go = function (n, initial) {
    var count = this.slides.length;
    if (!count) return;
    n = Math.max(1, Math.min(count, Math.round(+n) || 1));
    var changed = n !== this.cur;
    this.cur = n;
    for (var i = 0; i < count; i++) {
      var on = i === n - 1;
      this.slides[i].classList.toggle('is-active', on);
      if (on) this.slides[i].removeAttribute('aria-hidden');
      else this.slides[i].setAttribute('aria-hidden', 'true');
    }
    if (this.countEl) this.countEl.textContent = n + ' / ' + count;
    if (this.prevBtn) this.prevBtn.disabled = n === 1;
    if (this.nextBtn) this.nextBtn.disabled = n === count;
    try {
      var hash = (location.hash || '').replace(/^#/, '');
      var next = /(^|&)slide=\d+/.test(hash) ? hash.replace(/(^|&)slide=\d+/, '$1slide=' + n) : (hash ? hash + '&' : '') + 'slide=' + n;
      history.replaceState(history.state, '', '#' + next);
    } catch (e) { /* ignore */ }
    try { localStorage.setItem(this.key, String(n)); } catch (e) { /* ignore */ }
    if (changed || initial) {
      try {
        if (window.parent !== window) window.parent.postMessage({ type: 'td:slide-changed', index: n, count: count, total: count }, '*');
      } catch (e) { /* ignore */ }
      try {
        this.stage.dispatchEvent(new CustomEvent('slidechange', { detail: { index: n, count: count } }));
      } catch (e) { /* ignore */ }
    }
  };

  function expose(stage, deck) {
    var api = {
      go: function (n) { deck.go(n); },
      next: function () { deck.go(deck.cur + 1); },
      prev: function () { deck.go(deck.cur - 1); },
      get index() { return deck.cur; },
      get count() { return deck.slides.length; },
      get notes() { return deck.notes; },
      get element() { return stage; }
    };
    if (!(stage instanceof DeckStageBase)) {
      stage.go = api.go;
      stage.next = api.next;
      stage.prev = api.prev;
      try {
        Object.defineProperty(stage, 'index', { get: function () { return deck.cur; }, configurable: true });
        Object.defineProperty(stage, 'count', { get: function () { return deck.slides.length; }, configurable: true });
      } catch (e) { /* ignore */ }
    }
    if (!window.tdDeck) window.tdDeck = api;
  }

  function whenParsed(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn, { once: true });
    else fn();
  }

  var DeckStageBase = typeof HTMLElement !== 'undefined' ? HTMLElement : function () {};

  if (window.customElements && !customElements.get('deck-stage')) {
    var DeckStage = function () {
      return Reflect.construct(HTMLElement, [], DeckStage);
    };
    DeckStage.prototype = Object.create(HTMLElement.prototype);
    DeckStage.prototype.constructor = DeckStage;
    Object.setPrototypeOf(DeckStage, HTMLElement);
    DeckStage.prototype.connectedCallback = function () {
      var el = this;
      if (el._deck) return;
      el._deck = new Deck(el);
      whenParsed(function () {
        el._deck.build();
        expose(el, el._deck);
      });
    };
    DeckStage.prototype.go = function (n) { if (this._deck) this._deck.go(n); };
    DeckStage.prototype.next = function () { if (this._deck) this._deck.go(this._deck.cur + 1); };
    DeckStage.prototype.prev = function () { if (this._deck) this._deck.go(this._deck.cur - 1); };
    Object.defineProperty(DeckStage.prototype, 'index', { get: function () { return this._deck ? this._deck.cur : 0; } });
    Object.defineProperty(DeckStage.prototype, 'count', { get: function () { return this._deck ? this._deck.slides.length : 0; } });
    Object.defineProperty(DeckStage.prototype, 'notes', { get: function () { return this._deck ? this._deck.notes : []; } });
    DeckStageBase = DeckStage;
    customElements.define('deck-stage', DeckStage);
  }

  // Enhance a hand-written deck.md skeleton that has no script of its own.
  whenParsed(function () {
    var div = document.getElementById('deck-stage');
    if (!div || div.tagName.toLowerCase() === 'deck-stage' || div._deck) return;
    // A page that already runs the deck.md reference script leaves an active slide behind.
    if (div.querySelector('[data-td-slide].is-active')) return;
    div._deck = new Deck(div);
    div._deck.build();
    expose(div, div._deck);
  });
})();
