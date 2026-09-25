/*
 * td-bridge.js — TeleDesign preview bridge (iframe side of docs/teledesign-contract.md §6).
 *
 * Injected by the preview origin into every served HTML page. Works on plain HTML and on React
 * (UMD + Babel standalone) pages, never throws into the page, and keeps everything it draws inside
 * one shadow root (<td-rt-overlay data-td-rt>) that it excludes from everything it reports.
 *
 * Host → preview:  td:set-mode · td:highlight · td:eval · td:apply-style · td:set-viewport · td:slide
 *                  __activate_edit_mode / __deactivate_edit_mode (Tweaks — relayed to late listeners)
 *                  td:set-pins · td:complete-result                         (extensions, see report)
 * Preview → host:  td:ready · td:console · td:select · td:comment-target · td:text-edit ·
 *                  td:style-edit · td:draw · td:eval-result · td:navigate · td:complete
 *                  td:pin-click                                             (extension)
 *
 * Modes: view (page is live; relative link clicks reported) · comment (click → comment target +
 * pin) · edit (select; drag to move, 8 handles to resize, arrow keys nudge → style edits) ·
 * text (inline contenteditable → text edit) · knobs (per-element quick-props popover → style
 * edits) · draw (canvas overlay → PNG).
 *
 * Original TeleDesign code.
 */
(function () {
  'use strict';
  if (window.__tdBridge) return;

  var TD = (window.__tdBridge = { version: '1.0', mode: 'view', hostOrigin: null, errors: [] });
  var NativeURL = window.URL;
  var ACCENT = '#3b82f6';
  var COMMENT = '#f59e0b';
  var STYLE_ALLOW = [
    'color', 'background', 'background-color', 'font-size', 'font-weight', 'font-family', 'letter-spacing',
    'line-height', 'text-align', 'padding', 'padding-top', 'padding-right', 'padding-bottom', 'padding-left',
    'margin', 'margin-top', 'margin-right', 'margin-bottom', 'margin-left', 'gap', 'border-radius', 'width',
    'height', 'opacity', 'box-shadow', 'transform'
  ];
  var COMPUTED_KEYS = [
    'display', 'position', 'color', 'background-color', 'font-family', 'font-size', 'font-weight', 'line-height',
    'letter-spacing', 'text-align', 'padding', 'margin', 'gap', 'border-radius', 'width', 'height', 'opacity',
    'box-shadow', 'transform'
  ];
  var MODES = { view: 1, comment: 1, edit: 1, text: 1, knobs: 1, draw: 1 };

  // ---------------------------------------------------------------------------------------------
  // Safety helpers
  // ---------------------------------------------------------------------------------------------

  function note(e) {
    try {
      TD.errors.push(String((e && (e.stack || e.message)) || e).slice(0, 500));
      if (TD.errors.length > 50) TD.errors.shift();
    } catch (_) { /* ignore */ }
  }
  function safe(fn) {
    return function () {
      try { return fn.apply(this, arguments); } catch (e) { note(e); }
    };
  }
  function kebab(k) {
    return String(k).replace(/[A-Z]/g, function (c) { return '-' + c.toLowerCase(); });
  }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function round(v) { return Math.round(v * 100) / 100; }

  // ---------------------------------------------------------------------------------------------
  // Host origin + messaging
  // ---------------------------------------------------------------------------------------------

  var inIframe = false;
  try { inIframe = window.parent !== window; } catch (e) { inIframe = true; }

  function originOf(s) {
    try { return new NativeURL(s).origin; } catch (e) { return null; }
  }
  var hostOrigins = [];
  (function resolveHost() {
    var q = null;
    try { q = new URLSearchParams(location.search).get('td_host'); } catch (e) { /* ignore */ }
    var fromQuery = q && originOf(q);
    if (fromQuery) hostOrigins.push(fromQuery);
    try {
      var s = sessionStorage.getItem('td_host');
      if (s && hostOrigins.indexOf(s) === -1) hostOrigins.push(s);
    } catch (e) { /* ignore */ }
    try {
      var r = document.referrer && originOf(document.referrer);
      if (r && r !== location.origin && hostOrigins.indexOf(r) === -1) hostOrigins.push(r);
    } catch (e) { /* ignore */ }
    TD.hostOrigin = hostOrigins[0] || null;
    if (TD.hostOrigin) {
      try { sessionStorage.setItem('td_host', TD.hostOrigin); } catch (e) { /* ignore */ }
    }
  })();

  function adoptHost(origin) {
    // Only reached when nothing told us who the host is: accept a loopback parent.
    if (TD.hostOrigin || !/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/.test(origin)) return false;
    TD.hostOrigin = origin;
    hostOrigins.push(origin);
    try { sessionStorage.setItem('td_host', origin); } catch (e) { /* ignore */ }
    return true;
  }

  function post(msg, target) {
    if (!inIframe) return;
    try {
      window.parent.postMessage(msg, target || TD.hostOrigin || '*');
    } catch (e) {
      note(e);
    }
  }
  TD.post = post;

  // ---------------------------------------------------------------------------------------------
  // Console capture
  // ---------------------------------------------------------------------------------------------

  var consoleBudget = 400;
  var relaying = false;

  function stringify(v, depth) {
    depth = depth || 0;
    try {
      if (v === undefined) return 'undefined';
      if (v === null) return 'null';
      if (typeof v === 'string') return v;
      if (typeof v === 'number' || typeof v === 'boolean' || typeof v === 'bigint') return String(v);
      if (typeof v === 'function') return '[Function ' + (v.name || 'anonymous') + ']';
      if (typeof v === 'symbol') return v.toString();
      if (v instanceof Error) return (v.stack && v.stack.indexOf(v.message) !== -1 ? v.stack : v.name + ': ' + v.message + (v.stack ? '\n' + v.stack : ''));
      if (typeof Element !== 'undefined' && v instanceof Element) return '<' + v.tagName.toLowerCase() + (v.id ? '#' + v.id : '') + '>';
      if (depth > 2) return Array.isArray(v) ? '[Array(' + v.length + ')]' : '[Object]';
      var seen = [];
      return JSON.stringify(v, function (k, x) {
        if (typeof x === 'object' && x !== null) {
          if (seen.indexOf(x) !== -1) return '[Circular]';
          seen.push(x);
          if (typeof Element !== 'undefined' && x instanceof Element) return '<' + x.tagName.toLowerCase() + '>';
        }
        if (typeof x === 'function') return '[Function ' + (x.name || 'anonymous') + ']';
        if (typeof x === 'bigint') return String(x);
        return x;
      });
    } catch (e) {
      try { return String(v); } catch (e2) { return '[unserializable]'; }
    }
  }

  function relayConsole(level, args) {
    if (relaying || consoleBudget <= 0) return;
    relaying = true;
    try {
      consoleBudget--;
      var out = [];
      for (var i = 0; i < args.length && i < 20; i++) out.push(stringify(args[i]).slice(0, 4000));
      post({ type: 'td:console', level: level, args: out, at: new Date().toISOString() });
    } catch (e) {
      note(e);
    } finally {
      relaying = false;
    }
  }

  ['log', 'info', 'debug', 'warn', 'error'].forEach(function (name) {
    try {
      var orig = console[name];
      if (typeof orig !== 'function') return;
      var level = name === 'warn' ? 'warn' : name === 'error' ? 'error' : 'log';
      var wrapped = function () {
        relayConsole(level, arguments);
        return orig.apply(this, arguments);
      };
      wrapped.__tdOriginal = orig;
      console[name] = wrapped;
    } catch (e) { note(e); }
  });

  window.addEventListener('error', safe(function (e) {
    var t = e && e.target;
    if (t && t !== window && t.tagName) {
      var src = t.src || t.href || t.currentSrc || '';
      relayConsole('error', ['Failed to load <' + t.tagName.toLowerCase() + '> ' + src]);
      return;
    }
    var where = e.filename ? ' (' + e.filename + ':' + e.lineno + ':' + e.colno + ')' : '';
    var msg = (e.error && e.error.stack) ? e.error.stack : (e.message || 'Script error') + where;
    relayConsole('error', ['Uncaught ' + msg]);
  }), true);

  window.addEventListener('unhandledrejection', safe(function (e) {
    relayConsole('error', ['Unhandled promise rejection: ' + stringify(e.reason)]);
  }), false);

  // ---------------------------------------------------------------------------------------------
  // window.telecode.complete (same contract as td-telecode.js)
  // ---------------------------------------------------------------------------------------------

  var completions = {};
  var completeSeq = 0;
  (function installTelecode() {
    if (window.telecode && window.telecode.__td) return;
    function normalize(input) {
      if (typeof input === 'string') return { messages: [{ role: 'user', content: input }], options: {} };
      if (Array.isArray(input)) return { messages: input, options: {} };
      if (input && typeof input === 'object') {
        var o = {};
        for (var k in input) if (k !== 'messages' && k !== 'prompt' && Object.prototype.hasOwnProperty.call(input, k)) o[k] = input[k];
        var m = Array.isArray(input.messages) ? input.messages
          : typeof input.prompt === 'string' ? [{ role: 'user', content: input.prompt }] : [];
        return { messages: m, options: o };
      }
      return { messages: [{ role: 'user', content: String(input) }], options: {} };
    }
    function complete(input) {
      return new Promise(function (resolve, reject) {
        try {
          if (!inIframe) throw new Error('telecode.complete is only available inside the TeleDesign preview');
          var n = normalize(input);
          var id = 'c' + Date.now().toString(36) + '-' + (++completeSeq);
          var ms = +n.options.timeout_ms || 120000;
          delete n.options.timeout_ms;
          var msgs = n.messages.map(function (m) {
            if (typeof m === 'string') return { role: 'user', content: m };
            var c = m && m.content;
            return { role: (m && m.role) || 'user', content: typeof c === 'string' || Array.isArray(c) ? JSON.parse(JSON.stringify(c)) : String(c == null ? '' : c) };
          });
          completions[id] = {
            resolve: resolve, reject: reject,
            timer: setTimeout(function () {
              delete completions[id];
              reject(new Error('telecode.complete timed out after ' + Math.round(ms / 1000) + 's'));
            }, ms)
          };
          post({ type: 'td:complete', id: id, messages: msgs, options: JSON.parse(JSON.stringify(n.options)) });
        } catch (e) {
          reject(e instanceof Error ? e : new Error(String(e)));
        }
      });
    }
    window.telecode = { __td: true, complete: complete };
    try {
      if (!window.claude) window.claude = { complete: complete };
      else if (typeof window.claude.complete !== 'function') window.claude.complete = complete;
    } catch (e) { /* ignore */ }
  })();

  function onCompleteResult(d) {
    var p = completions[d.id];
    if (!p) return;
    delete completions[d.id];
    clearTimeout(p.timer);
    if (d.ok === false || d.error) p.reject(new Error(String(d.error || 'completion failed')));
    else p.resolve(typeof d.text === 'string' ? d.text : typeof d.content === 'string' ? d.content : String(d.text == null ? '' : d.text));
  }

  // ---------------------------------------------------------------------------------------------
  // Tweaks relay: replay the current edit-mode state to `message` listeners the page registers
  // after the host already sent it (Babel pages mount late).
  // ---------------------------------------------------------------------------------------------

  var tweaksState = null; // null | '__activate_edit_mode' | '__deactivate_edit_mode'
  var ownListener = null;
  (function hookAddEventListener() {
    try {
      var orig = window.addEventListener;
      window.addEventListener = function (type, listener) {
        var r = orig.apply(this, arguments);
        try {
          if (type === 'message' && listener && listener !== ownListener && tweaksState === '__activate_edit_mode' && this === window) {
            var state = tweaksState;
            setTimeout(function () {
              try {
                if (tweaksState !== state) return;
                var ev = new MessageEvent('message', { data: { type: state }, origin: TD.hostOrigin || '', source: window.parent });
                if (typeof listener === 'function') listener.call(window, ev);
                else if (listener && typeof listener.handleEvent === 'function') listener.handleEvent(ev);
              } catch (e) {
                setTimeout(function () { throw e; }, 0); // page's own error: surface it normally
              }
            }, 0);
          }
        } catch (e) { note(e); }
        return r;
      };
    } catch (e) { note(e); }
  })();

  // ---------------------------------------------------------------------------------------------
  // DOM description (selector, <mentioned-element>, React chain, hints)
  // ---------------------------------------------------------------------------------------------

  var overlayHost = null; // <td-rt-overlay>
  function isOurs(node) {
    if (!node) return false;
    if (overlayHost && (node === overlayHost || (overlayHost.contains && overlayHost.contains(node)))) return true;
    return node.nodeType === 1 && node.hasAttribute && node.hasAttribute('data-td-rt');
  }
  function elementChildren(el) {
    var out = [];
    for (var c = el.firstElementChild; c; c = c.nextElementSibling) if (!isOurs(c)) out.push(c);
    return out;
  }
  function cssEscape(s) {
    try { if (window.CSS && CSS.escape) return CSS.escape(s); } catch (e) { /* ignore */ }
    return String(s).replace(/[^a-zA-Z0-9_-]/g, function (c) { return '\\' + c; });
  }
  function attrQuote(s) {
    return '"' + String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';
  }
  function countMatches(sel) {
    try { return document.querySelectorAll(sel).length; } catch (e) { return 0; }
  }
  function nthOfType(el) {
    var p = el.parentElement;
    if (!p) return null;
    var same = 0, idx = 0;
    for (var c = p.firstElementChild; c; c = c.nextElementSibling) {
      if (c.tagName === el.tagName && !isOurs(c)) {
        same++;
        if (c === el) idx = same;
      }
    }
    return same > 1 ? idx : null;
  }

  function uniqueSelector(el) {
    try {
      var tdid = el.getAttribute('data-td-id');
      if (tdid) {
        var s0 = '[data-td-id=' + attrQuote(tdid) + ']';
        if (countMatches(s0) === 1) return s0;
      }
      if (el.id && countMatches('#' + cssEscape(el.id)) === 1) return '#' + cssEscape(el.id);
      var parts = [];
      var cur = el;
      while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
        if (cur !== el) {
          var a = cur.getAttribute('data-td-id');
          if (a && countMatches('[data-td-id=' + attrQuote(a) + ']') === 1) { parts.unshift('[data-td-id=' + attrQuote(a) + ']'); break; }
          if (cur.id && countMatches('#' + cssEscape(cur.id)) === 1) { parts.unshift('#' + cssEscape(cur.id)); break; }
        }
        var seg = cur.tagName.toLowerCase();
        var n = nthOfType(cur);
        if (n) seg += ':nth-of-type(' + n + ')';
        parts.unshift(seg);
        if (cur.tagName === 'BODY') break;
        cur = cur.parentElement;
      }
      return parts.join(' > ');
    } catch (e) {
      note(e);
      return el && el.tagName ? el.tagName.toLowerCase() : '';
    }
  }

  function domSegment(el) {
    var seg = el.tagName.toLowerCase();
    var screen = el.getAttribute('data-td-screen');
    if (screen) return seg + '[data-td-screen=' + attrQuote(screen) + ']';
    var cls = (typeof el.className === 'string' ? el.className : (el.getAttribute('class') || '')).trim().split(/\s+/)[0];
    if (cls && cls.length <= 40 && !/^td-rt/.test(cls)) seg += '.' + cls;
    var n = nthOfType(el);
    if (n) seg += ':nth-of-type(' + n + ')';
    return seg;
  }

  function domChain(el) {
    var chain = [];
    for (var cur = el; cur && cur.nodeType === 1 && cur !== document.documentElement; cur = cur.parentElement) {
      chain.unshift(cur);
      if (cur.tagName === 'BODY') break;
    }
    var MAX = 9; // the element + 8 ancestors
    if (chain.length <= MAX) return chain.map(domSegment).join(' > ');
    var tail = chain.slice(chain.length - MAX + 1); // leave room for "body > …"
    var head = [domSegment(chain[0]), '…'];
    // keep the nearest data-td-screen ancestor even if it is further up
    var inTail = tail.some(function (x) { return x.hasAttribute('data-td-screen'); });
    if (!inTail) {
      for (var i = chain.length - MAX; i > 0; i--) {
        if (chain[i].hasAttribute('data-td-screen')) {
          head.push(domSegment(chain[i]));
          if (i + 1 < chain.length - tail.length) head.push('…');
          tail = tail.slice(1);
          break;
        }
      }
    }
    return head.concat(tail.map(domSegment)).join(' > ');
  }

  function fiberOf(el) {
    for (var cur = el; cur; cur = cur.parentElement) {
      var keys;
      try { keys = Object.keys(cur); } catch (e) { return null; }
      for (var i = 0; i < keys.length; i++) {
        if (keys[i].indexOf('__reactFiber$') === 0 || keys[i].indexOf('__reactInternalInstance$') === 0) return cur[keys[i]];
      }
    }
    return null;
  }
  function componentName(type) {
    if (!type) return null;
    if (typeof type === 'function') return type.displayName || type.name || null;
    if (typeof type === 'object') {
      if (type.displayName) return type.displayName;
      if (type.render) return type.render.displayName || type.render.name || null; // forwardRef
      if (type.type) return componentName(type.type); // memo
    }
    return null;
  }
  function reactChain(el) {
    try {
      var f = fiberOf(el);
      if (!f) return '';
      var names = [];
      for (var cur = f; cur; cur = cur['return']) {
        var t = cur.type;
        if (!t || typeof t === 'string') continue;
        var name = componentName(t);
        if (!name || /^_/.test(name)) continue;
        var label = name;
        var parent = cur['return'];
        if (parent) {
          var total = 0, idx = 0;
          for (var s = parent.child; s; s = s.sibling) {
            if (s.type === t) {
              total++;
              if (s === cur || s === cur.alternate) idx = total;
            }
          }
          if (total > 1 && idx) label += '[' + idx + '/' + total + ']';
        }
        names.unshift(label);
        if (names.length >= 10) break;
      }
      return names.join(' > ');
    } catch (e) {
      note(e);
      return '';
    }
  }

  function visibleText(el, max) {
    var s = '';
    try { s = (el.innerText != null ? el.innerText : el.textContent) || ''; } catch (e) { s = el.textContent || ''; }
    s = s.replace(/\s+/g, ' ').trim();
    max = max || 160;
    return s.length > max ? s.slice(0, max - 1) + '…' : s;
  }

  function childrenSummary(el) {
    var kids = elementChildren(el);
    if (!kids.length) return '';
    var parts = [];
    var i = 0;
    while (i < kids.length) {
      var k = kids[i];
      var tag = k.tagName.toLowerCase();
      var run = 1;
      while (i + run < kids.length && kids[i + run].tagName === k.tagName) run++;
      var inner = elementChildren(k).length;
      var item = tag + (inner ? '(' + inner + ')' : '');
      if (run > 2) {
        parts.push(item + '×' + run);
        i += run;
      } else {
        parts.push(item);
        i += 1;
      }
    }
    if (parts.length > 12) parts = parts.slice(0, 12).concat(['…']);
    return parts.join(', ');
  }

  var RT_ATTR_RE = /\s(?:data-td-rt[\w-]*|data-td-live|data-td-src)(?:="[^"]*")?/g;
  function cleanAttrs(el) {
    var out = [];
    for (var i = 0; i < el.attributes.length; i++) {
      var a = el.attributes[i];
      if (/^data-td-rt/.test(a.name) || a.name === 'data-td-live' || a.name === 'data-td-src') continue;
      if (a.name === 'contenteditable' && el.hasAttribute('data-td-rt-ce')) continue;
      out.push(a.name + (a.value === '' ? '' : '="' + a.value.replace(/"/g, '&quot;') + '"'));
    }
    return out.length ? ' ' + out.join(' ') : '';
  }
  function htmlHint(el) {
    try {
      var tag = el.tagName.toLowerCase();
      var open = '<' + tag + cleanAttrs(el) + '>';
      if (/^(img|input|br|hr|meta|link|source|area|col|embed|wbr|track|param)$/.test(tag)) return open.slice(0, 400);
      var inner = '';
      if (el !== document.body) {
        inner = el.innerHTML || '';
        inner = inner.replace(/<td-rt-overlay[\s\S]*?<\/td-rt-overlay>/g, '').replace(RT_ATTR_RE, '').replace(/\s+/g, ' ').trim();
      } else {
        inner = '…';
      }
      if (inner.length > 300) inner = inner.slice(0, 300) + '…';
      return (open.length > 400 ? open.slice(0, 399) + '…>' : open) + inner + '</' + tag + '>';
    } catch (e) {
      note(e);
      return '';
    }
  }

  function computedDigest(el) {
    var out = {};
    try {
      var cs = getComputedStyle(el);
      COMPUTED_KEYS.forEach(function (k) {
        var v = cs.getPropertyValue(k);
        if (v !== '' && v != null) out[k] = v;
      });
    } catch (e) { note(e); }
    return out;
  }

  function rectOf(el) {
    try {
      var r = el.getBoundingClientRect();
      return { x: round(r.left), y: round(r.top), w: round(r.width), h: round(r.height) };
    } catch (e) {
      return { x: 0, y: 0, w: 0, h: 0 };
    }
  }

  var liveSeq = 0;
  function liveHandle(el) {
    try {
      var v = el.getAttribute('data-td-live');
      if (!v) {
        v = String(++liveSeq);
        el.setAttribute('data-td-live', v);
      }
      return v;
    } catch (e) {
      return null;
    }
  }

  function mentionedElement(el, info) {
    var lines = ['<mentioned-element>'];
    if (info.react) lines.push('react: ' + info.react);
    lines.push('dom: ' + info.dom);
    var idParts = [];
    if (info.td_id) idParts.push('id: data-td-id=' + attrQuote(info.td_id));
    if (info.source_loc) idParts.push('src: ' + info.source_loc);
    if (idParts.length) lines.push(idParts.join('   '));
    if (info.text) lines.push('text: ' + attrQuote(info.text).replace(/<\//g, '<\\/'));
    if (info.children) lines.push('children: ' + info.children);
    lines.push('</mentioned-element>');
    return lines.join('\n');
  }

  function describe(el, mode) {
    var td_id = el.getAttribute('data-td-id') || null;
    var source_loc = el.getAttribute('data-td-src') || null;
    var info = {
      td_id: td_id,
      source_loc: source_loc,
      react: reactChain(el),
      dom: domChain(el),
      text: visibleText(el, 160),
      children: childrenSummary(el)
    };
    var out = {
      mode: mode,
      selector: uniqueSelector(el),
      bbox: rectOf(el),
      text: info.text,
      html_hint: htmlHint(el),
      computed: computedDigest(el),
      mentioned_element: mentionedElement(el, info),
      tag: el.tagName.toLowerCase()
    };
    if (td_id) out.td_id = td_id;
    if (source_loc) out.source_loc = source_loc;
    if (info.react) out.react = info.react;
    var screen = el.closest && el.closest('[data-td-screen]');
    if (screen) out.screen = screen.getAttribute('data-td-screen');
    var live = liveHandle(el);
    if (live) out.live = live;
    var deck = deckInfo();
    if (deck && deck.count) out.slide_index = deck.index;
    return out;
  }
  TD.describe = describe;

  function findTarget(d) {
    try {
      if (d.td_id) {
        var el = document.querySelector('[data-td-id=' + attrQuote(d.td_id) + ']');
        if (el) return el;
      }
      if (d.live) {
        var l = document.querySelector('[data-td-live=' + attrQuote(d.live) + ']');
        if (l) return l;
      }
      if (d.selector) return document.querySelector(d.selector);
    } catch (e) { note(e); }
    return null;
  }

  // ---------------------------------------------------------------------------------------------
  // Overlay (shadow root)
  // ---------------------------------------------------------------------------------------------

  var shadow = null;
  var ui = {};
  var OVERLAY_CSS = [
    ':host{all:initial;position:fixed;inset:0;z-index:2147483647;pointer-events:none;display:block;',
    'font:12px/1.3 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:#0f172a}',
    '@media print{:host{display:none!important}}',
    '*{box-sizing:border-box}',
    '.box{position:fixed;pointer-events:none;border:1.5px solid ' + ACCENT + ';border-radius:2px;display:none}',
    '.hover{background:rgba(59,130,246,.06)}',
    '.hover.comment{border-color:' + COMMENT + ';background:rgba(245,158,11,.07)}',
    '.hover.text{border-style:dashed}',
    '.tag{position:absolute;left:-1.5px;top:-22px;height:20px;padding:0 6px;border-radius:4px 4px 4px 0;',
    'background:' + ACCENT + ';color:#fff;font-weight:600;font-size:11px;line-height:20px;white-space:nowrap;',
    'max-width:320px;overflow:hidden;text-overflow:ellipsis}',
    '.tag.below{top:auto;bottom:-22px;border-radius:0 4px 4px 4px}',
    '.hover.comment .tag{background:' + COMMENT + '}',
    '.tag .dim{opacity:.75;font-weight:500;margin-left:6px}',
    '.sel{border-width:2px}',
    '.sel.movable{pointer-events:auto;cursor:move}',
    '.h{position:absolute;width:10px;height:10px;background:#fff;border:1.5px solid ' + ACCENT + ';border-radius:2px;pointer-events:auto}',
    '.h[data-h=nw]{left:-6px;top:-6px;cursor:nwse-resize}.h[data-h=n]{left:calc(50% - 5px);top:-6px;cursor:ns-resize}',
    '.h[data-h=ne]{right:-6px;top:-6px;cursor:nesw-resize}.h[data-h=e]{right:-6px;top:calc(50% - 5px);cursor:ew-resize}',
    '.h[data-h=se]{right:-6px;bottom:-6px;cursor:nwse-resize}.h[data-h=s]{left:calc(50% - 5px);bottom:-6px;cursor:ns-resize}',
    '.h[data-h=sw]{left:-6px;bottom:-6px;cursor:nesw-resize}.h[data-h=w]{left:-6px;top:calc(50% - 5px);cursor:ew-resize}',
    '.size{position:absolute;left:50%;bottom:-26px;transform:translateX(-50%);background:#0f172a;color:#fff;',
    'font-size:11px;padding:2px 6px;border-radius:4px;white-space:nowrap;font-variant-numeric:tabular-nums}',
    '.hl{border:2px solid ' + COMMENT + ';box-shadow:0 0 0 4px rgba(245,158,11,.25);transition:opacity .2s}',
    '.pin{position:fixed;pointer-events:auto;cursor:pointer;width:26px;height:26px;margin:-26px 0 0 -2px;',
    'border-radius:13px 13px 13px 2px;background:' + COMMENT + ';color:#fff;font-weight:700;font-size:11px;',
    'display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(0,0,0,.25);border:2px solid #fff}',
    '.pin.pending{background:' + ACCENT + '}',
    '.pin.resolved{background:#94a3b8}',
    /* knobs popover */
    '.pop{position:fixed;pointer-events:auto;width:264px;background:#fff;border:1px solid rgba(15,23,42,.1);',
    'border-radius:12px;box-shadow:0 12px 40px rgba(15,23,42,.18),0 2px 6px rgba(15,23,42,.08);padding:10px 12px 12px;display:none}',
    '.pop header{display:flex;align-items:center;gap:8px;margin:0 0 8px}',
    '.pop header b{font-size:12px;font-weight:650;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
    '.pop header button{all:unset;cursor:pointer;width:22px;height:22px;border-radius:6px;display:flex;',
    'align-items:center;justify-content:center;color:#64748b;font-size:15px}',
    '.pop header button:hover{background:#f1f5f9;color:#0f172a}',
    '.pop .grp{font-size:10px;font-weight:650;letter-spacing:.06em;text-transform:uppercase;color:#94a3b8;margin:10px 0 6px}',
    '.pop .row{display:flex;align-items:center;gap:8px;margin:5px 0}',
    '.pop label{width:74px;color:#475569;font-size:12px;flex:none}',
    '.pop input[type=number],.pop select,.pop input[type=text]{all:unset;box-sizing:border-box;flex:1;min-width:0;height:26px;',
    'padding:0 8px;border:1px solid #e2e8f0;border-radius:6px;font:12px ui-sans-serif,system-ui,sans-serif;color:#0f172a;background:#fff}',
    '.pop input:focus,.pop select:focus{border-color:' + ACCENT + ';box-shadow:0 0 0 3px rgba(59,130,246,.15)}',
    '.pop input[type=color]{all:unset;width:26px;height:26px;border-radius:6px;border:1px solid #e2e8f0;cursor:pointer;flex:none;padding:0}',
    '.pop input[type=color]::-webkit-color-swatch-wrapper{padding:2px}.pop input[type=color]::-webkit-color-swatch{border:none;border-radius:4px}',
    '.pop input[type=range]{flex:1;min-width:0;margin:0;accent-color:' + ACCENT + '}',
    '.pop .seg{display:flex;flex:1;border:1px solid #e2e8f0;border-radius:6px;overflow:hidden}',
    '.pop .seg button{all:unset;flex:1;text-align:center;height:24px;line-height:24px;cursor:pointer;color:#475569;font-size:11px}',
    '.pop .seg button+button{border-left:1px solid #e2e8f0}',
    '.pop .seg button.on{background:#eff6ff;color:' + ACCENT + ';font-weight:650}',
    '.pop .val{width:36px;flex:none;text-align:right;color:#64748b;font-size:11px;font-variant-numeric:tabular-nums}',
    /* draw */
    'canvas.draw{position:fixed;inset:0;pointer-events:auto;cursor:crosshair;touch-action:none;display:none}',
    '.tools{position:fixed;left:50%;top:12px;transform:translateX(-50%);pointer-events:auto;display:none;',
    'align-items:center;gap:6px;padding:6px;background:rgba(15,23,42,.92);border-radius:12px;',
    'box-shadow:0 10px 30px rgba(0,0,0,.25);color:#fff}',
    '.tools button{all:unset;cursor:pointer;height:28px;min-width:28px;padding:0 8px;border-radius:8px;display:flex;',
    'align-items:center;justify-content:center;font:600 12px ui-sans-serif,system-ui,sans-serif;color:#e2e8f0}',
    '.tools button:hover{background:rgba(255,255,255,.1)}',
    '.tools .sw{width:18px;height:18px;min-width:0;padding:0;border-radius:50%;border:2px solid transparent}',
    '.tools .sw.on{border-color:#fff}',
    '.tools .dot{display:block;border-radius:50%;background:#e2e8f0}',
    '.tools .sz.on{background:rgba(255,255,255,.16)}',
    '.tools .sep{width:1px;height:20px;background:rgba(255,255,255,.18);margin:0 2px}',
    '.tools .send{background:' + ACCENT + ';color:#fff;padding:0 12px}',
    '.tools .send:hover{background:#2563eb}'
  ].join('');

  function ensureOverlay() {
    if (overlayHost && overlayHost.isConnected) return true;
    try {
      if (!overlayHost) {
        overlayHost = document.createElement('td-rt-overlay');
        overlayHost.setAttribute('data-td-rt', '');
        shadow = overlayHost.attachShadow({ mode: 'open' });
        var st = document.createElement('style');
        st.textContent = OVERLAY_CSS;
        shadow.appendChild(st);
        ui.hover = mk('div', 'box hover');
        ui.hoverTag = mk('div', 'tag');
        ui.hover.appendChild(ui.hoverTag);
        ui.sel = mk('div', 'box sel');
        ui.selTag = mk('div', 'tag');
        ui.sel.appendChild(ui.selTag);
        ui.size = mk('div', 'size');
        ui.handles = [];
        ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'].forEach(function (h) {
          var d = mk('div', 'h');
          d.setAttribute('data-h', h);
          ui.handles.push(d);
        });
        ui.hl = mk('div', 'box hl');
        ui.pins = mk('div', '');
        ui.pop = mk('div', 'pop');
        ui.canvas = mk('canvas', 'draw');
        ui.tools = mk('div', 'tools');
        [ui.hover, ui.sel, ui.hl, ui.pins, ui.pop, ui.canvas, ui.tools].forEach(function (n) { shadow.appendChild(n); });
        wireEditHandles();
        wireDraw();
      }
      (document.documentElement || document.body).appendChild(overlayHost);
      return true;
    } catch (e) {
      note(e);
      return false;
    }
  }
  function mk(tag, cls) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    return n;
  }
  function place(box, r) {
    box.style.display = 'block';
    box.style.left = r.left + 'px';
    box.style.top = r.top + 'px';
    box.style.width = Math.max(0, r.width) + 'px';
    box.style.height = Math.max(0, r.height) + 'px';
  }
  function setTag(tagEl, el, r) {
    tagEl.innerHTML = '';
    var id = el.getAttribute('data-td-id');
    tagEl.appendChild(document.createTextNode(id ? id : el.tagName.toLowerCase()));
    var dim = mk('span', 'dim');
    dim.textContent = Math.round(r.width) + '×' + Math.round(r.height);
    tagEl.appendChild(dim);
    tagEl.classList.toggle('below', r.top < 24);
  }

  // Cursor / selection hints for the page itself while a mode is active (light-DOM style, ours).
  var PAGE_CSS = [
    'html[data-td-rt-mode=comment],html[data-td-rt-mode=comment] *{cursor:crosshair!important}',
    'html[data-td-rt-mode=text] *:not([data-td-rt-ce]){cursor:text!important}',
    'html[data-td-rt-mode=edit] *,html[data-td-rt-mode=knobs] *{cursor:default!important}',
    'html[data-td-rt-mode=comment] *,html[data-td-rt-mode=edit] *,html[data-td-rt-mode=knobs] *{user-select:none!important;-webkit-user-select:none!important}',
    '[data-td-rt-ce]{outline:2px solid ' + ACCENT + '!important;outline-offset:2px!important;cursor:text!important;user-select:text!important;-webkit-user-select:text!important}',
    '@media print{html[data-td-rt-mode] *{cursor:auto!important}}'
  ].join('');
  var pageStyle = null;
  function ensurePageStyle() {
    try {
      if (pageStyle && pageStyle.isConnected) return;
      if (!pageStyle) {
        pageStyle = document.createElement('style');
        pageStyle.setAttribute('data-td-rt', '');
        pageStyle.textContent = PAGE_CSS;
      }
      (document.head || document.documentElement).appendChild(pageStyle);
    } catch (e) { note(e); }
  }

  // ---------------------------------------------------------------------------------------------
  // Frame loop: keeps boxes glued to their elements while anything is shown
  // ---------------------------------------------------------------------------------------------

  var hoverEl = null, selEl = null, hlEl = null, hlTimer = null, knobEl = null;
  var pins = []; // [{id, n, status, td_id?, selector?, x?, y?, el?}]
  var pendingPin = null; // {x, y, el}
  var rafId = 0;

  function needLoop() {
    return !!(hoverEl || selEl || hlEl || knobEl || pins.length || pendingPin);
  }
  function kick() {
    if (!rafId && needLoop()) rafId = requestAnimationFrame(frame);
  }
  function frame() {
    rafId = 0;
    try { render(); } catch (e) { note(e); }
    if (needLoop()) rafId = requestAnimationFrame(frame);
  }
  function render() {
    if (!shadow) return;
    if (hoverEl && hoverEl.isConnected && hoverEl !== selEl) {
      var r = hoverEl.getBoundingClientRect();
      place(ui.hover, r);
      ui.hover.className = 'box hover' + (TD.mode === 'comment' ? ' comment' : TD.mode === 'text' ? ' text' : '');
      setTag(ui.hoverTag, hoverEl, r);
    } else {
      ui.hover.style.display = 'none';
    }
    if (selEl && selEl.isConnected) {
      var s = selEl.getBoundingClientRect();
      place(ui.sel, s);
      setTag(ui.selTag, selEl, s);
      ui.size.textContent = Math.round(s.width) + ' × ' + Math.round(s.height);
    } else {
      ui.sel.style.display = 'none';
    }
    if (hlEl && hlEl.isConnected) place(ui.hl, hlEl.getBoundingClientRect());
    else ui.hl.style.display = 'none';
    if (knobEl && knobEl.isConnected) positionPop(knobEl);
    renderPins();
  }

  // ---------------------------------------------------------------------------------------------
  // Mode switching
  // ---------------------------------------------------------------------------------------------

  function setMode(mode) {
    if (!MODES[mode]) return;
    var prev = TD.mode;
    if (prev === 'text') commitText(false);
    if (prev === 'draw') hideDraw();
    closeKnobs();
    hoverEl = null;
    if (mode !== 'edit' && mode !== 'knobs') selEl = null;
    if (mode !== 'comment') pendingPin = null;
    TD.mode = mode;
    if (mode !== 'view') { ensureOverlay(); ensurePageStyle(); }
    if (shadow) {
      ui.sel.classList.toggle('movable', mode === 'edit');
      setHandles(mode === 'edit' && !!selEl);
    }
    try {
      document.documentElement.setAttribute('data-td-rt-mode', mode);
      if (mode === 'view') document.documentElement.removeAttribute('data-td-rt-mode');
    } catch (e) { /* ignore */ }
    if (mode === 'draw') showDraw();
    if (shadow) render();
    kick();
  }
  TD.setMode = setMode;

  function setHandles(on) {
    if (!shadow) return;
    ui.handles.forEach(function (h) {
      if (on && h.parentNode !== ui.sel) ui.sel.appendChild(h);
      if (!on && h.parentNode) h.parentNode.removeChild(h);
    });
    if (on && ui.size.parentNode !== ui.sel) ui.sel.appendChild(ui.size);
    if (!on && ui.size.parentNode) ui.size.parentNode.removeChild(ui.size);
  }

  function pickable(el) {
    if (!el || el.nodeType !== 1 || isOurs(el)) return null;
    if (el === document.documentElement) return document.body;
    return el;
  }
  function eventElement(e) {
    var t = e.target;
    if (t && t.nodeType === 3) t = t.parentElement;
    if (isOurs(t)) return null;
    return pickable(t);
  }
  function fromOverlay(e) {
    try {
      var p = e.composedPath ? e.composedPath() : [];
      return overlayHost ? p.indexOf(overlayHost) !== -1 : false;
    } catch (_) {
      return false;
    }
  }
  function interactive() {
    return TD.mode === 'comment' || TD.mode === 'edit' || TD.mode === 'knobs' || TD.mode === 'text';
  }

  // --- global capture listeners (installed once) -------------------------------------------------

  document.addEventListener('mousemove', safe(function (e) {
    if (!interactive() || drag) return;
    if (fromOverlay(e)) return;
    var el = eventElement(e);
    if (TD.mode === 'text' && editing && editing.el === el) el = null;
    if (el !== hoverEl) {
      hoverEl = el;
      kick();
      if (!el && shadow) render();
    }
  }), true);

  document.documentElement.addEventListener('mouseleave', safe(function () {
    hoverEl = null;
    if (shadow) render();
  }), false);

  function swallow(e) {
    e.preventDefault();
    e.stopPropagation();
    if (e.stopImmediatePropagation) e.stopImmediatePropagation();
  }

  ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'dblclick', 'auxclick', 'touchstart'].forEach(function (type) {
    window.addEventListener(type, safe(function (e) {
      if (!interactive() || fromOverlay(e)) return;
      if (TD.mode === 'text' && editing && editing.el.contains(e.target)) {
        e.stopPropagation();
        return;
      }
      swallow(e);
    }), { capture: true, passive: false });
  });

  window.addEventListener('submit', safe(function (e) {
    if (interactive()) swallow(e);
  }), true);

  window.addEventListener('click', safe(function (e) {
    if (TD.mode === 'view' || TD.mode === 'draw' || fromOverlay(e)) return;
    if (TD.mode === 'text' && editing && editing.el.contains(e.target)) {
      e.stopPropagation();
      return;
    }
    swallow(e);
    var el = eventElement(e);
    if (!el) return;
    if (TD.mode === 'comment') return onCommentClick(el, e);
    if (TD.mode === 'edit') return select(el);
    if (TD.mode === 'knobs') return openKnobs(el);
    if (TD.mode === 'text') return startText(el);
  }), true);

  // relative-link navigation reporting (view mode, after page handlers ran)
  window.addEventListener('click', safe(function (e) {
    if (TD.mode !== 'view' || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!a || isOurs(a)) return;
    var raw = a.getAttribute('href') || '';
    if (/^(#|javascript:|mailto:|tel:|data:|blob:)/i.test(raw)) return;
    var u;
    try { u = new NativeURL(a.href, location.href); } catch (err) { return; }
    if (u.origin !== location.origin) return;
    if (u.pathname === location.pathname && u.search === location.search) return; // hash change only
    post({ type: 'td:navigate', path: projectPath(u) + (u.hash || '') });
  }), false);

  window.addEventListener('keydown', safe(function (e) {
    if (TD.mode === 'text' && editing) {
      if (editing.el.contains(e.target) || e.target === editing.el) {
        e.stopPropagation();
        if (e.key === 'Escape') { e.preventDefault(); commitText(true); }
        else if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); commitText(false); }
      }
      return;
    }
    if (TD.mode === 'draw') {
      if (e.key === 'Enter') { swallow(e); sendDrawing(); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { swallow(e); undoStroke(); }
      else if (e.key === 'Escape') { swallow(e); clearDrawing(); }
      return;
    }
    if (TD.mode === 'knobs' && knobEl && e.key === 'Escape') { swallow(e); closeKnobs(); return; }
    if (TD.mode === 'edit' && selEl) {
      var tag = e.target && e.target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || (e.target && e.target.isContentEditable)) return;
      if (e.key === 'Escape') { swallow(e); select(null); return; }
      if (e.altKey && e.key === 'ArrowUp') {
        swallow(e);
        var p = selEl.parentElement;
        if (p && p !== document.documentElement) select(p);
        return;
      }
      if (e.altKey && e.key === 'ArrowDown') {
        swallow(e);
        var c = elementChildren(selEl)[0];
        if (c) select(c);
        return;
      }
      var step = e.shiftKey ? 10 : 1;
      var dx = e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0;
      var dy = e.key === 'ArrowUp' ? -step : e.key === 'ArrowDown' ? step : 0;
      if (dx || dy) {
        swallow(e);
        nudge(dx, dy);
      }
      return;
    }
    if (interactive() && e.key === 'Escape') {
      pendingPin = null;
      if (shadow) render();
    }
  }), true);

  // ---------------------------------------------------------------------------------------------
  // comment mode
  // ---------------------------------------------------------------------------------------------

  function onCommentClick(el, e) {
    var info = describe(el, 'comment');
    var r = el.getBoundingClientRect();
    info.click = { x: round(e.clientX), y: round(e.clientY) };
    info.click_rel = { x: r.width ? round((e.clientX - r.left) / r.width) : 0, y: r.height ? round((e.clientY - r.top) / r.height) : 0 };
    pendingPin = { el: el, rx: info.click_rel.x, ry: info.click_rel.y };
    post(Object.assign({ type: 'td:comment-target' }, info));
    kick();
  }

  function renderPins() {
    if (!shadow) return;
    var want = pins.slice();
    if (pendingPin) want.push({ id: '__pending', n: '+', status: 'pending', el: pendingPin.el, rx: pendingPin.rx, ry: pendingPin.ry });
    while (ui.pins.childNodes.length > want.length) ui.pins.removeChild(ui.pins.lastChild);
    want.forEach(function (p, i) {
      var node = ui.pins.childNodes[i];
      if (!node) {
        node = mk('div', 'pin');
        node.addEventListener('click', function (ev) {
          ev.stopPropagation();
          var pid = node.getAttribute('data-id');
          if (pid && pid !== '__pending') post({ type: 'td:pin-click', id: pid });
        });
        ui.pins.appendChild(node);
      }
      node.className = 'pin' + (p.status === 'pending' ? ' pending' : p.status === 'resolved' ? ' resolved' : '');
      node.setAttribute('data-id', p.id);
      if (node.textContent !== String(p.n == null ? '' : p.n)) node.textContent = p.n == null ? '' : String(p.n);
      var el = p.el && p.el.isConnected ? p.el : findTarget(p);
      p.el = el;
      var x, y;
      if (el) {
        var r = el.getBoundingClientRect();
        x = r.left + (p.rx != null ? p.rx * r.width : r.width - 4);
        y = r.top + (p.ry != null ? p.ry * r.height : 4);
      } else if (p.x != null) {
        x = p.x; y = p.y;
      }
      if (x == null) { node.style.display = 'none'; return; }
      node.style.display = 'flex';
      node.style.left = x + 'px';
      node.style.top = y + 'px';
    });
  }

  // ---------------------------------------------------------------------------------------------
  // edit mode: select, move, resize, nudge
  // ---------------------------------------------------------------------------------------------

  var drag = null;

  function select(el) {
    selEl = el;
    if (el) {
      ensureOverlay();
      post(Object.assign({ type: 'td:select' }, describe(el, TD.mode)));
    }
    setHandles(TD.mode === 'edit' && !!el);
    if (shadow) render();
    kick();
  }

  function parseTranslate(t) {
    // "translate(12px, -4px) rotate(3deg)" → {x:12, y:-4, rest:"rotate(3deg)"}
    var m = /^\s*translate\(\s*(-?[\d.]+)px\s*(?:,\s*(-?[\d.]+)px\s*)?\)\s*/.exec(t || '');
    if (m) return { x: +m[1], y: +(m[2] || 0), rest: (t || '').slice(m[0].length).trim() };
    return { x: 0, y: 0, rest: (t || '').trim() };
  }
  function composeTranslate(x, y, rest) {
    var tr = x || y ? 'translate(' + round(x) + 'px, ' + round(y) + 'px)' : '';
    return (tr + (rest ? ' ' + rest : '')).trim() || 'none';
  }

  function emitStyle(el, style) {
    if (!el) return;
    var msg = { type: 'td:style-edit', selector: uniqueSelector(el), style: style };
    var id = el.getAttribute('data-td-id');
    var src = el.getAttribute('data-td-src');
    if (id) msg.td_id = id;
    if (src) msg.source_loc = src;
    var live = el.getAttribute('data-td-live');
    if (live) msg.live = live;
    post(msg);
  }

  function wireEditHandles() {
    ui.sel.addEventListener('pointerdown', safe(function (e) {
      if (TD.mode !== 'edit' || !selEl || e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      var h = e.target && e.target.getAttribute && e.target.getAttribute('data-h');
      var r = selEl.getBoundingClientRect();
      var cs = getComputedStyle(selEl);
      var t = parseTranslate(selEl.style.transform);
      drag = {
        kind: h ? 'resize' : 'move', h: h || '', sx: e.clientX, sy: e.clientY, moved: false,
        w0: r.width, h0: r.height, tx0: t.x, ty0: t.y, rest: t.rest,
        // width/height apply to the content box unless border-box
        padX: cs.boxSizing === 'border-box' ? 0 : (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0) + (parseFloat(cs.borderLeftWidth) || 0) + (parseFloat(cs.borderRightWidth) || 0),
        padY: cs.boxSizing === 'border-box' ? 0 : (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0) + (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.borderBottomWidth) || 0),
        orig: { transform: selEl.style.transform, width: selEl.style.width, height: selEl.style.height },
        pointerId: e.pointerId
      };
      try { ui.sel.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    }));
    ui.sel.addEventListener('pointermove', safe(function (e) {
      if (!drag || !selEl) return;
      var dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 2) return;
      drag.moved = true;
      if (drag.kind === 'move') {
        selEl.style.transform = composeTranslate(drag.tx0 + dx, drag.ty0 + dy, drag.rest);
      } else {
        var h = drag.h, w = drag.w0, hh = drag.h0, tx = drag.tx0, ty = drag.ty0;
        if (h.indexOf('e') !== -1) w = drag.w0 + dx;
        if (h.indexOf('w') !== -1) { w = drag.w0 - dx; tx = drag.tx0 + dx; }
        if (h.indexOf('s') !== -1) hh = drag.h0 + dy;
        if (h.indexOf('n') !== -1) { hh = drag.h0 - dy; ty = drag.ty0 + dy; }
        if (e.shiftKey && h.length === 2 && drag.h0) {
          var ratio = drag.w0 / drag.h0;
          if (Math.abs(w / ratio - hh) > 0.5) hh = w / ratio;
        }
        w = Math.max(4, Math.round(w));
        hh = Math.max(4, Math.round(hh));
        if (h.indexOf('e') !== -1 || h.indexOf('w') !== -1) selEl.style.width = Math.max(0, w - drag.padX) + 'px';
        if (h.indexOf('n') !== -1 || h.indexOf('s') !== -1 || (e.shiftKey && h.length === 2)) selEl.style.height = Math.max(0, hh - drag.padY) + 'px';
        if (tx !== drag.tx0 || ty !== drag.ty0) selEl.style.transform = composeTranslate(tx, ty, drag.rest);
      }
      render();
    }));
    var end = safe(function (e) {
      if (!drag) return;
      var d = drag;
      drag = null;
      try { ui.sel.releasePointerCapture(d.pointerId); } catch (err) { /* ignore */ }
      if (!d.moved || !selEl) {
        // A click on the selection box: pick whatever is underneath it.
        if (e && e.type === 'pointerup' && selEl) {
          ui.sel.style.pointerEvents = 'none';
          var under = pickable(document.elementFromPoint(e.clientX, e.clientY));
          ui.sel.style.pointerEvents = '';
          if (under && under !== selEl && selEl.contains(under)) select(under);
        }
        return;
      }
      var style = {};
      if (selEl.style.transform !== d.orig.transform) style.transform = selEl.style.transform || 'none';
      if (selEl.style.width !== d.orig.width) style.width = selEl.style.width;
      if (selEl.style.height !== d.orig.height) style.height = selEl.style.height;
      if (Object.keys(style).length) emitStyle(selEl, style);
    });
    ui.sel.addEventListener('pointerup', end);
    ui.sel.addEventListener('pointercancel', end);
  }

  var nudgeTimer = null;
  function nudge(dx, dy) {
    var t = parseTranslate(selEl.style.transform);
    selEl.style.transform = composeTranslate(t.x + dx, t.y + dy, t.rest);
    render();
    var el = selEl;
    clearTimeout(nudgeTimer);
    nudgeTimer = setTimeout(safe(function () { emitStyle(el, { transform: el.style.transform || 'none' }); }), 350);
  }

  // ---------------------------------------------------------------------------------------------
  // text mode
  // ---------------------------------------------------------------------------------------------

  var editing = null; // {el, old, oldHTML, prevCE}
  var VOID_RE = /^(IMG|INPUT|BR|HR|SVG|PATH|VIDEO|CANVAS|IFRAME|SELECT|TEXTAREA|PICTURE|SOURCE|OBJECT|EMBED)$/;

  function textTarget(el) {
    // Prefer the element itself when it carries text; climb out of inline SVG etc.
    for (var cur = el; cur && cur !== document.body; cur = cur.parentElement) {
      if (VOID_RE.test(cur.tagName.toUpperCase())) continue;
      if (visibleText(cur, 5)) return cur;
    }
    return null;
  }

  function startText(el) {
    if (editing) commitText(false);
    var t = textTarget(el);
    if (!t) return;
    hoverEl = null;
    var prevCE = t.getAttribute('contenteditable');
    editing = { el: t, old: t.innerText, oldHTML: t.innerHTML, prevCE: prevCE };
    t.setAttribute('data-td-rt-ce', '');
    try { t.contentEditable = 'plaintext-only'; } catch (e) { t.contentEditable = 'true'; }
    if (t.contentEditable !== 'plaintext-only') t.contentEditable = 'true';
    t.addEventListener('blur', onEditBlur);
    try {
      t.focus({ preventScroll: true });
      var range = document.createRange();
      range.selectNodeContents(t);
      var s = window.getSelection();
      s.removeAllRanges();
      s.addRange(range);
    } catch (e) { note(e); }
    selEl = t;
    if (shadow) { setHandles(false); render(); }
    kick();
  }
  function onEditBlur() {
    setTimeout(safe(function () { commitText(false); }), 0);
  }
  function commitText(revert) {
    if (!editing) return;
    var ed = editing;
    editing = null;
    var el = ed.el;
    el.removeEventListener('blur', onEditBlur);
    var now = el.innerText;
    if (revert) el.innerHTML = ed.oldHTML;
    if (ed.prevCE == null) el.removeAttribute('contenteditable');
    else el.setAttribute('contenteditable', ed.prevCE);
    el.removeAttribute('data-td-rt-ce');
    try { window.getSelection().removeAllRanges(); } catch (e) { /* ignore */ }
    if (selEl === el) selEl = null;
    if (shadow) render();
    if (revert) return;
    var oldT = ed.old.replace(/ /g, ' ');
    var newT = now.replace(/ /g, ' ').replace(/\n$/, '');
    if (oldT === newT) return;
    var msg = { type: 'td:text-edit', selector: uniqueSelector(el), old_text: oldT, new_text: newT };
    var id = el.getAttribute('data-td-id');
    var src = el.getAttribute('data-td-src');
    if (id) msg.td_id = id;
    if (src) msg.source_loc = src;
    post(msg);
  }

  // ---------------------------------------------------------------------------------------------
  // knobs mode: quick-props popover
  // ---------------------------------------------------------------------------------------------

  var knobPending = {};
  var knobTimer = null;

  function toHex(c) {
    var m = /rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)(?:[,\s/]+([\d.]+%?))?\s*\)/.exec(c || '');
    if (!m) return /^#[0-9a-f]{6}$/i.test(c || '') ? c : null;
    var a = m[4] == null ? 1 : /%$/.test(m[4]) ? parseFloat(m[4]) / 100 : +m[4];
    if (a === 0) return null;
    return '#' + [m[1], m[2], m[3]].map(function (x) { return ('0' + (+x).toString(16)).slice(-2); }).join('');
  }

  function knobSet(prop, value) {
    if (!knobEl) return;
    knobEl.style.setProperty(prop, value);
    knobPending[prop] = value;
    var el = knobEl;
    clearTimeout(knobTimer);
    knobTimer = setTimeout(safe(function () {
      var style = knobPending;
      knobPending = {};
      emitStyle(el, style);
    }), 250);
    render();
  }
  function flushKnobs() {
    if (knobTimer && Object.keys(knobPending).length && knobEl) {
      clearTimeout(knobTimer);
      var style = knobPending;
      knobPending = {};
      emitStyle(knobEl, style);
    }
  }

  function openKnobs(el) {
    flushKnobs();
    ensureOverlay();
    knobEl = el;
    selEl = el;
    setHandles(false);
    post(Object.assign({ type: 'td:select' }, describe(el, 'knobs')));
    buildKnobs(el);
    ui.pop.style.display = 'block';
    render();
    kick();
  }
  function closeKnobs() {
    flushKnobs();
    knobEl = null;
    if (TD.mode === 'knobs') selEl = null;
    if (shadow) { ui.pop.style.display = 'none'; ui.pop.innerHTML = ''; render(); }
  }

  function buildKnobs(el) {
    var cs = getComputedStyle(el);
    var pop = ui.pop;
    pop.innerHTML = '';
    var head = mk('header', '');
    var title = mk('b', '');
    title.textContent = el.getAttribute('data-td-id') || el.tagName.toLowerCase();
    var close = mk('button', '');
    close.textContent = '×';
    close.setAttribute('aria-label', 'Close');
    close.addEventListener('click', function (e) { e.stopPropagation(); closeKnobs(); });
    head.appendChild(title);
    head.appendChild(close);
    pop.appendChild(head);

    function group(name) {
      var g = mk('div', 'grp');
      g.textContent = name;
      pop.appendChild(g);
    }
    function row(label) {
      var r = mk('div', 'row');
      var l = mk('label', '');
      l.textContent = label;
      r.appendChild(l);
      pop.appendChild(r);
      return r;
    }
    function numberRow(label, prop, value, min, max, stepv) {
      var r = row(label);
      var inp = mk('input', '');
      inp.type = 'number';
      inp.min = min; inp.max = max; inp.step = stepv || 1;
      inp.value = Math.round((parseFloat(value) || 0) * 100) / 100;
      inp.addEventListener('input', function () { if (inp.value !== '') knobSet(prop, inp.value + 'px'); });
      r.appendChild(inp);
      return inp;
    }
    function colorRow(label, prop, value) {
      var r = row(label);
      var inp = mk('input', '');
      inp.type = 'color';
      var hex = toHex(value);
      inp.value = hex || '#ffffff';
      var txt = mk('input', '');
      txt.type = 'text';
      txt.value = hex || (value && value !== 'rgba(0, 0, 0, 0)' ? value : 'transparent');
      inp.addEventListener('input', function () { txt.value = inp.value; knobSet(prop, inp.value); });
      txt.addEventListener('change', function () { knobSet(prop, txt.value); var h = toHex(txt.value); if (h) inp.value = h; });
      r.appendChild(inp);
      r.appendChild(txt);
    }

    var hasText = !!visibleText(el, 3);
    if (hasText) {
      group('Text');
      numberRow('Size', 'font-size', cs.fontSize, 6, 400, 1);
      var wr = row('Weight');
      var sel = mk('select', '');
      [100, 200, 300, 400, 500, 600, 700, 800, 900].forEach(function (w) {
        var o = mk('option', '');
        o.value = String(w);
        o.textContent = w + (w === 400 ? ' Regular' : w === 500 ? ' Medium' : w === 600 ? ' Semibold' : w === 700 ? ' Bold' : '');
        sel.appendChild(o);
      });
      sel.value = String(Math.round((parseInt(cs.fontWeight, 10) || 400) / 100) * 100);
      sel.addEventListener('change', function () { knobSet('font-weight', sel.value); });
      wr.appendChild(sel);
      colorRow('Color', 'color', cs.color);
      var ar = row('Align');
      var seg = mk('div', 'seg');
      ['left', 'center', 'right'].forEach(function (a) {
        var b = mk('button', '');
        b.textContent = a.charAt(0).toUpperCase() + a.slice(1);
        var cur = cs.textAlign === 'start' ? 'left' : cs.textAlign === 'end' ? 'right' : cs.textAlign;
        if (cur === a) b.className = 'on';
        b.addEventListener('click', function (e) {
          e.stopPropagation();
          Array.prototype.forEach.call(seg.children, function (x) { x.className = ''; });
          b.className = 'on';
          knobSet('text-align', a);
        });
        seg.appendChild(b);
      });
      ar.appendChild(seg);
    }
    group('Box');
    colorRow('Fill', 'background-color', cs.backgroundColor);
    numberRow('Padding', 'padding', cs.paddingTop, 0, 400, 1);
    numberRow('Radius', 'border-radius', cs.borderTopLeftRadius, 0, 400, 1);
    var or = row('Opacity');
    var rng = mk('input', '');
    rng.type = 'range';
    rng.min = '0'; rng.max = '1'; rng.step = '0.05';
    rng.value = cs.opacity;
    var val = mk('span', 'val');
    val.textContent = Math.round(+cs.opacity * 100) + '%';
    rng.addEventListener('input', function () { val.textContent = Math.round(+rng.value * 100) + '%'; knobSet('opacity', rng.value); });
    or.appendChild(rng);
    or.appendChild(val);

    // keep typing inside the popover away from page shortcuts
    ['keydown', 'keyup', 'keypress'].forEach(function (t) {
      pop.addEventListener(t, function (e) { e.stopPropagation(); });
    });
  }

  function positionPop(el) {
    var r = el.getBoundingClientRect();
    var pw = 264, ph = ui.pop.offsetHeight || 300;
    var vw = window.innerWidth, vh = window.innerHeight;
    var x = r.right + 12;
    if (x + pw > vw - 8) x = r.left - pw - 12;
    if (x < 8) x = clamp(r.left, 8, Math.max(8, vw - pw - 8));
    var y = clamp(r.top, 8, Math.max(8, vh - ph - 8));
    if (x === clamp(r.left, 8, Math.max(8, vw - pw - 8)) && r.left - pw - 12 < 8 && r.right + 12 + pw > vw - 8) {
      y = r.bottom + 12 + ph < vh ? r.bottom + 12 : clamp(r.top - ph - 12, 8, vh - ph - 8);
    }
    ui.pop.style.left = Math.round(x) + 'px';
    ui.pop.style.top = Math.round(y) + 'px';
  }

  // ---------------------------------------------------------------------------------------------
  // draw mode
  // ---------------------------------------------------------------------------------------------

  var strokes = []; // [{color, size, pts:[[x,y],…]}]
  var curStroke = null;
  var drawColor = '#ef4444', drawSize = 4;
  var DRAW_COLORS = ['#ef4444', '#f59e0b', '#22c55e', '#3b82f6', '#0f172a'];

  function wireDraw() {
    var c = ui.canvas;
    c.addEventListener('pointerdown', safe(function (e) {
      if (e.button !== 0) return;
      e.preventDefault();
      curStroke = { color: drawColor, size: drawSize, pts: [[e.clientX, e.clientY]] };
      strokes.push(curStroke);
      try { c.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      paint();
    }));
    c.addEventListener('pointermove', safe(function (e) {
      if (!curStroke) return;
      var evs = e.getCoalescedEvents ? e.getCoalescedEvents() : [e];
      if (!evs.length) evs = [e];
      evs.forEach(function (ev) { curStroke.pts.push([ev.clientX, ev.clientY]); });
      paint();
    }));
    var up = safe(function () { curStroke = null; syncTools(); });
    c.addEventListener('pointerup', up);
    c.addEventListener('pointercancel', up);
    c.addEventListener('wheel', function (e) {
      try { window.scrollBy(e.deltaX, e.deltaY); } catch (err) { /* ignore */ }
    }, { passive: true });
    window.addEventListener('resize', safe(function () { if (TD.mode === 'draw') sizeCanvas(); }));
  }

  function sizeCanvas() {
    var c = ui.canvas, dpr = window.devicePixelRatio || 1;
    c.width = Math.round(window.innerWidth * dpr);
    c.height = Math.round(window.innerHeight * dpr);
    c.style.width = window.innerWidth + 'px';
    c.style.height = window.innerHeight + 'px';
    paint();
  }

  function strokePath(ctx, s, ox, oy) {
    var p = s.pts;
    ctx.strokeStyle = s.color;
    ctx.fillStyle = s.color;
    ctx.lineWidth = s.size;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    if (p.length === 1) {
      ctx.beginPath();
      ctx.arc(p[0][0] - ox, p[0][1] - oy, s.size / 2, 0, Math.PI * 2);
      ctx.fill();
      return;
    }
    ctx.beginPath();
    ctx.moveTo(p[0][0] - ox, p[0][1] - oy);
    for (var i = 1; i < p.length - 1; i++) {
      var mx = (p[i][0] + p[i + 1][0]) / 2, my = (p[i][1] + p[i + 1][1]) / 2;
      ctx.quadraticCurveTo(p[i][0] - ox, p[i][1] - oy, mx - ox, my - oy);
    }
    var l = p[p.length - 1];
    ctx.lineTo(l[0] - ox, l[1] - oy);
    ctx.stroke();
  }

  function paint() {
    var c = ui.canvas;
    var ctx = c.getContext('2d');
    var dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, c.width, c.height);
    strokes.forEach(function (s) { strokePath(ctx, s, 0, 0); });
  }

  function buildTools() {
    var t = ui.tools;
    t.innerHTML = '';
    DRAW_COLORS.forEach(function (col) {
      var b = mk('button', 'sw' + (col === drawColor ? ' on' : ''));
      b.style.background = col;
      b.title = col;
      b.addEventListener('click', function (e) { e.stopPropagation(); drawColor = col; buildTools(); });
      t.appendChild(b);
    });
    t.appendChild(mk('span', 'sep'));
    [2, 4, 8].forEach(function (sz) {
      var b = mk('button', 'sz' + (sz === drawSize ? ' on' : ''));
      var d = mk('span', 'dot');
      d.style.width = d.style.height = sz + 2 + 'px';
      b.appendChild(d);
      b.title = sz + 'px';
      b.addEventListener('click', function (e) { e.stopPropagation(); drawSize = sz; buildTools(); });
      t.appendChild(b);
    });
    t.appendChild(mk('span', 'sep'));
    var undo = mk('button', '');
    undo.textContent = 'Undo';
    undo.addEventListener('click', function (e) { e.stopPropagation(); undoStroke(); });
    var clr = mk('button', '');
    clr.textContent = 'Clear';
    clr.addEventListener('click', function (e) { e.stopPropagation(); clearDrawing(); });
    var send = mk('button', 'send');
    send.textContent = 'Send';
    send.setAttribute('data-td-rt-send', '');
    send.addEventListener('click', function (e) { e.stopPropagation(); sendDrawing(); });
    t.appendChild(undo);
    t.appendChild(clr);
    t.appendChild(send);
    syncTools();
  }
  function syncTools() {
    var send = ui.tools.querySelector('[data-td-rt-send]');
    if (send) send.style.opacity = strokes.length ? '1' : '.5';
  }
  function undoStroke() { strokes.pop(); paint(); syncTools(); }
  function clearDrawing() { strokes = []; curStroke = null; paint(); syncTools(); }

  function showDraw() {
    ensureOverlay();
    ui.canvas.style.display = 'block';
    ui.tools.style.display = 'flex';
    buildTools();
    sizeCanvas();
  }
  function hideDraw() {
    if (!shadow) return;
    ui.canvas.style.display = 'none';
    ui.tools.style.display = 'none';
    curStroke = null;
  }

  function sendDrawing() {
    if (!strokes.length) return;
    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity, pad = 0;
    strokes.forEach(function (s) {
      pad = Math.max(pad, s.size);
      s.pts.forEach(function (p) {
        if (p[0] < minX) minX = p[0];
        if (p[1] < minY) minY = p[1];
        if (p[0] > maxX) maxX = p[0];
        if (p[1] > maxY) maxY = p[1];
      });
    });
    pad += 4;
    minX = Math.floor(Math.max(0, minX - pad));
    minY = Math.floor(Math.max(0, minY - pad));
    maxX = Math.ceil(Math.min(window.innerWidth, maxX + pad));
    maxY = Math.ceil(Math.min(window.innerHeight, maxY + pad));
    var w = Math.max(1, maxX - minX), h = Math.max(1, maxY - minY);
    var dpr = window.devicePixelRatio || 1;
    var off = document.createElement('canvas');
    off.width = Math.round(w * dpr);
    off.height = Math.round(h * dpr);
    var ctx = off.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    strokes.forEach(function (s) { strokePath(ctx, s, minX, minY); });
    var url = off.toDataURL('image/png');
    // What the drawing covers helps the model: the elements under the stroke box.
    var under = [];
    try {
      var cx = minX + w / 2, cy = minY + h / 2;
      ui.canvas.style.pointerEvents = 'none';
      var hit = pickable(document.elementFromPoint(cx, cy));
      ui.canvas.style.pointerEvents = '';
      if (hit) under.push(describe(hit, 'draw'));
    } catch (e) { note(e); }
    var msg = { type: 'td:draw', png_data_url: url, bbox: { x: minX, y: minY, w: w, h: h }, strokes: strokes.length };
    if (under.length) msg.target = under[0];
    post(msg);
    clearDrawing();
  }

  // ---------------------------------------------------------------------------------------------
  // Decks
  // ---------------------------------------------------------------------------------------------

  function deckRoot() {
    try {
      return document.querySelector('deck-stage, #deck-stage, [data-td-deck]');
    } catch (e) {
      return null;
    }
  }
  function deckSlides(root) {
    var s = root.querySelectorAll('[data-td-slide]');
    if (!s.length) {
      var canvas = root.querySelector('.deck-canvas') || root;
      s = canvas.querySelectorAll(':scope > section');
    }
    return Array.prototype.slice.call(s);
  }
  function slideVisible(el) {
    if (el.classList.contains('is-active') || el.hasAttribute('data-active') || el.getAttribute('aria-hidden') === 'false') return true;
    try {
      var cs = getComputedStyle(el);
      return cs.display !== 'none' && cs.visibility !== 'hidden' && +cs.opacity > 0.01;
    } catch (e) {
      return false;
    }
  }
  function deckInfo() {
    var root = deckRoot();
    if (!root) return null;
    var slides = deckSlides(root);
    var idx = 0;
    if (typeof root.index === 'number' && root.index > 0) idx = root.index;
    else {
      for (var i = 0; i < slides.length; i++) {
        if (slides[i].classList.contains('is-active')) { idx = i + 1; break; }
      }
      if (!idx) for (var j = 0; j < slides.length; j++) if (slideVisible(slides[j])) { idx = j + 1; break; }
    }
    return { root: root, slides: slides, count: slides.length, index: idx || 1 };
  }
  TD.deckInfo = function () {
    var d = deckInfo();
    return d ? { count: d.count, index: d.index } : null;
  };

  function pressKey(key) {
    var ev = new KeyboardEvent('keydown', { key: key, code: key, bubbles: true, cancelable: true });
    document.body.dispatchEvent(ev);
  }

  function onSlide(d) {
    var before = deckInfo();
    if (!before) return;
    var target = d.action === 'next' ? before.index + 1
      : d.action === 'prev' ? before.index - 1
        : d.action === 'first' ? 1
          : d.action === 'last' ? before.count
            : d.action === 'go' ? +d.index : NaN;
    if (!isFinite(target)) return;
    target = clamp(Math.round(target), 1, before.count);
    // The deck handles td:slide itself (deck.md). Only step in if nothing moved.
    setTimeout(safe(function () {
      var now = deckInfo();
      if (!now || now.index === target) return;
      var root = now.root;
      if (typeof root.go === 'function') { root.go(target); return; }
      if (window.tdDeck && typeof window.tdDeck.go === 'function') { window.tdDeck.go(target); return; }
      var n = now.index;
      if (target === 1) { pressKey('Home'); return; }
      if (target === now.count) { pressKey('End'); return; }
      var steps = Math.abs(target - n);
      for (var i = 0; i < steps && i < 200; i++) pressKey(target > n ? 'ArrowRight' : 'ArrowLeft');
    }), 120);
  }

  // ---------------------------------------------------------------------------------------------
  // td:eval
  // ---------------------------------------------------------------------------------------------

  function toPlain(v, depth, seen) {
    if (v === undefined) return null;
    if (v === null || typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean') {
      return typeof v === 'number' && !isFinite(v) ? String(v) : v;
    }
    if (typeof v === 'bigint') return String(v);
    if (typeof v === 'function') return '[Function ' + (v.name || 'anonymous') + ']';
    if (typeof v === 'symbol') return v.toString();
    if (v instanceof Error) return { error: v.name, message: v.message, stack: v.stack };
    if (typeof Element !== 'undefined' && v instanceof Element) {
      return {
        element: v.tagName.toLowerCase(), td_id: v.getAttribute('data-td-id') || undefined,
        selector: uniqueSelector(v), text: visibleText(v, 200), bbox: rectOf(v), html: htmlHint(v)
      };
    }
    if (depth > 5) return '[…]';
    if (seen.indexOf(v) !== -1) return '[Circular]';
    seen.push(v);
    if (Array.isArray(v) || (typeof NodeList !== 'undefined' && v instanceof NodeList) || (typeof HTMLCollection !== 'undefined' && v instanceof HTMLCollection)) {
      var arr = [];
      for (var i = 0; i < v.length && i < 500; i++) arr.push(toPlain(v[i], depth + 1, seen));
      return arr;
    }
    if (v instanceof Date) return v.toISOString();
    if (typeof Map !== 'undefined' && v instanceof Map) {
      var mo = {};
      v.forEach(function (val, k) { mo[String(k)] = toPlain(val, depth + 1, seen); });
      return mo;
    }
    var o = {};
    var keys = Object.keys(v).slice(0, 200);
    keys.forEach(function (k) {
      try { o[k] = toPlain(v[k], depth + 1, seen); } catch (e) { o[k] = '[unreadable]'; }
    });
    return o;
  }

  function runEval(d) {
    var id = d.id;
    var reply = function (ok, value, error) {
      var msg = { type: 'td:eval-result', id: id, ok: ok };
      if (ok) msg.value = value;
      else msg.error = error;
      post(msg);
    };
    var code = String(d.code == null ? '' : d.code);
    var result;
    try {
      try {
        result = (0, eval)(code);
      } catch (e1) {
        if (!(e1 instanceof SyntaxError)) throw e1;
        // `await` at top level, or `return` statements: run as an async function body.
        var body = /(^|[^\w$.])return[\s;(]/.test(code) ? code : 'return (' + code + '\n)';
        try {
          result = (0, eval)('(async () => {' + body + '\n})()');
        } catch (e2) {
          if (!(e2 instanceof SyntaxError) || body === code) throw e1;
          result = (0, eval)('(async () => {' + code + '\n})()');
        }
      }
    } catch (err) {
      reply(false, null, String((err && err.stack) || err));
      return;
    }
    var finish = function (v) {
      try { reply(true, toPlain(v, 0, [])); } catch (e) { reply(false, null, 'unserializable result: ' + e); }
    };
    if (result && typeof result.then === 'function') {
      var done = false;
      var t = setTimeout(function () { if (!done) { done = true; reply(false, null, 'timeout after 15s'); } }, 15000);
      result.then(function (v) { if (!done) { done = true; clearTimeout(t); finish(v); } },
        function (err) { if (!done) { done = true; clearTimeout(t); reply(false, null, String((err && err.stack) || err)); } });
    } else {
      finish(result);
    }
  }

  // ---------------------------------------------------------------------------------------------
  // highlight / apply-style / pins
  // ---------------------------------------------------------------------------------------------

  function highlight(d) {
    clearTimeout(hlTimer);
    var el = d && (d.td_id || d.selector || d.live) ? findTarget(d) : null;
    hlEl = el;
    if (el) {
      ensureOverlay();
      if (d.scroll) {
        // never scrollIntoView (it scrolls the host): scroll the nearest scroller instead
        try {
          var r = el.getBoundingClientRect();
          if (r.top < 0 || r.bottom > window.innerHeight) window.scrollBy({ top: r.top - window.innerHeight / 3, behavior: 'smooth' });
        } catch (e) { /* ignore */ }
      }
      if (d.duration_ms) hlTimer = setTimeout(function () { hlEl = null; if (shadow) render(); }, +d.duration_ms);
    }
    if (shadow) render();
    kick();
  }

  function applyStyle(d) {
    var el = findTarget(d);
    if (!el || !d.style || typeof d.style !== 'object') return;
    Object.keys(d.style).forEach(function (k) {
      var prop = kebab(k);
      if (STYLE_ALLOW.indexOf(prop) === -1) return;
      var v = d.style[k];
      if (v == null || v === '') el.style.removeProperty(prop);
      else el.style.setProperty(prop, String(v));
    });
    if (shadow) render();
  }

  function setPins(d) {
    var list = Array.isArray(d.pins) ? d.pins : [];
    pins = list.slice(0, 500).map(function (p) {
      return {
        id: String(p.id), n: p.n != null ? p.n : p.index, status: p.status || 'open', td_id: p.td_id, selector: p.selector,
        live: p.live, x: p.x, y: p.y, rx: p.rx, ry: p.ry
      };
    });
    if (pins.length) ensureOverlay();
    pendingPin = null;
    if (shadow) render();
    kick();
  }

  // ---------------------------------------------------------------------------------------------
  // Host → preview dispatch
  // ---------------------------------------------------------------------------------------------

  ownListener = safe(function (e) {
    if (!inIframe || e.source !== window.parent) return;
    var known = hostOrigins.indexOf(e.origin) !== -1;
    if (!known && !adoptHost(e.origin)) return;
    var d = e.data;
    if (!d || typeof d !== 'object' || typeof d.type !== 'string') return;
    switch (d.type) {
      case 'td:set-mode': setMode(d.mode); break;
      case 'td:highlight': highlight(d); break;
      case 'td:eval': runEval(d); break;
      case 'td:apply-style': applyStyle(d); break;
      case 'td:set-viewport': TD.viewport = { width: +d.width || 0, height: +d.height || 0 }; break;
      case 'td:slide': onSlide(d); break;
      case 'td:set-pins': setPins(d); break;
      case 'td:complete-result': onCompleteResult(d); break;
      case 'td:ping': post({ type: 'td:pong', id: d.id }); break;
      case '__activate_edit_mode':
      case '__deactivate_edit_mode': tweaksState = d.type; break;
      default: break;
    }
  });
  window.addEventListener('message', ownListener, false);

  // ---------------------------------------------------------------------------------------------
  // td:ready
  // ---------------------------------------------------------------------------------------------

  function projectPath(u) {
    try {
      var p = decodeURIComponent(u.pathname);
      var m = /^\/p\/[^/]+\/(.*)$/.exec(p);
      return m ? m[1] : p.replace(/^\/+/, '');
    } catch (e) {
      return String(u.pathname || '');
    }
  }

  function hasBabel() {
    try { return !!document.querySelector('script[type="text/babel"], script[type="text/jsx"]'); } catch (e) { return false; }
  }
  function hasTweaks() {
    try {
      var s = document.getElementsByTagName('script');
      for (var i = 0; i < s.length; i++) if (!s[i].src && (s[i].textContent || '').indexOf('/*EDITMODE-BEGIN*/') !== -1) return true;
    } catch (e) { /* ignore */ }
    return false;
  }

  var readySent = false;
  function sendReady() {
    if (readySent) return;
    readySent = true;
    var msg = {
      type: 'td:ready',
      title: document.title || '',
      url: location.href,
      path: projectPath(location),
      has_babel: hasBabel(),
      tweaks: hasTweaks()
    };
    var d = deckInfo();
    if (d && d.count) msg.deck = { count: d.count, index: d.index };
    post(msg);
  }
  TD.sendReady = sendReady;

  function whenSettled(cb) {
    // After load, wait for the DOM to go quiet (React/Babel mount) — at most 2.5 s.
    var last = Date.now();
    var start = last;
    var mo = null;
    try {
      mo = new MutationObserver(function (records) {
        for (var i = 0; i < records.length; i++) {
          var t = records[i].target;
          if (!isOurs(t)) { last = Date.now(); return; }
        }
      });
      mo.observe(document.documentElement, { childList: true, subtree: true, attributes: false });
    } catch (e) { /* ignore */ }
    (function check() {
      var now = Date.now();
      var babelPending = hasBabel() && window.__tdBabelSource && !window.__tdBabelSource.ready;
      if ((now - last >= 200 && !babelPending) || now - start > 2500) {
        if (mo) mo.disconnect();
        cb();
      } else {
        setTimeout(check, 60);
      }
    })();
  }

  function onLoad() {
    whenSettled(safe(sendReady));
  }
  if (document.readyState === 'complete') setTimeout(onLoad, 0);
  else window.addEventListener('load', onLoad, false);

  window.addEventListener('scroll', function () { kick(); }, { passive: true, capture: true });
  window.addEventListener('resize', function () { kick(); }, { passive: true });
})();
