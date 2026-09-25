/*
 * td-telecode.js — TeleDesign preview runtime: in-page AI for generated designs.
 *
 *   const text = await window.telecode.complete("Write a tagline for a bakery");
 *   const text = await window.telecode.complete([{role: "user", content: "…"}]);
 *   const text = await window.telecode.complete({messages: […], max_tokens: 400});
 *
 * The preview origin has no network access to the proxy (CSP connect-src 'self'), so the call is
 * relayed to the host page:
 *
 *   preview → host   {type: "td:complete", id, messages, options}
 *   host → preview   {type: "td:complete-result", id, ok: true, text}
 *                    {type: "td:complete-result", id, ok: false, error}
 *
 * The host answers through the proxy's /v1/chat/completions (local model). `window.claude.complete`
 * is aliased for projects imported from Claude artifacts. Resolves with the assistant text (string);
 * rejects with an Error on failure or after the timeout (default 120 s, `options.timeout_ms`).
 *
 * td-bridge.js installs the same API (it shares this file's implementation contract), so a page
 * that loads both gets one instance. Standalone exports can load this file alone.
 *
 * Original TeleDesign code.
 */
(function () {
  'use strict';
  if (window.telecode && window.telecode.__td) return;

  var pending = {};
  var seq = 0;

  function hostOrigin() {
    try {
      var b = window.__tdBridge;
      if (b && b.hostOrigin) return b.hostOrigin;
    } catch (e) { /* ignore */ }
    try {
      var q = new URLSearchParams(location.search).get('td_host');
      if (q) return new window.URL(q).origin;
    } catch (e) { /* ignore */ }
    try {
      var s = sessionStorage.getItem('td_host');
      if (s) return s;
    } catch (e) { /* ignore */ }
    try {
      if (document.referrer) {
        var r = new window.URL(document.referrer).origin;
        if (r !== location.origin) return r;
      }
    } catch (e) { /* ignore */ }
    return '*';
  }

  function normalize(input) {
    if (typeof input === 'string') return { messages: [{ role: 'user', content: input }], options: {} };
    if (Array.isArray(input)) return { messages: input, options: {} };
    if (input && typeof input === 'object') {
      var opts = {};
      for (var k in input) if (k !== 'messages' && k !== 'prompt' && Object.prototype.hasOwnProperty.call(input, k)) opts[k] = input[k];
      var msgs = Array.isArray(input.messages) ? input.messages
        : typeof input.prompt === 'string' ? [{ role: 'user', content: input.prompt }] : [];
      return { messages: msgs, options: opts };
    }
    return { messages: [{ role: 'user', content: String(input) }], options: {} };
  }

  function plain(messages) {
    // Keep it structured-cloneable: role + string/array content only.
    return messages.map(function (m) {
      if (typeof m === 'string') return { role: 'user', content: m };
      var c = m && m.content;
      return { role: (m && m.role) || 'user', content: typeof c === 'string' || Array.isArray(c) ? JSON.parse(JSON.stringify(c)) : String(c == null ? '' : c) };
    });
  }

  function complete(input) {
    return new Promise(function (resolve, reject) {
      try {
        if (window.parent === window) throw new Error('telecode.complete is only available inside the TeleDesign preview');
        var n = normalize(input);
        var id = 'c' + Date.now().toString(36) + '-' + (++seq);
        var ms = +n.options.timeout_ms || 120000;
        delete n.options.timeout_ms;
        var timer = setTimeout(function () {
          delete pending[id];
          reject(new Error('telecode.complete timed out after ' + Math.round(ms / 1000) + 's'));
        }, ms);
        pending[id] = { resolve: resolve, reject: reject, timer: timer };
        window.parent.postMessage({ type: 'td:complete', id: id, messages: plain(n.messages), options: JSON.parse(JSON.stringify(n.options)) }, hostOrigin());
      } catch (e) {
        reject(e instanceof Error ? e : new Error(String(e)));
      }
    });
  }

  function onMessage(e) {
    try {
      if (e.source !== window.parent) return;
      var d = e.data;
      if (!d || d.type !== 'td:complete-result' || !pending[d.id]) return;
      var host = hostOrigin();
      if (host !== '*' && e.origin !== host) return;
      var p = pending[d.id];
      delete pending[d.id];
      clearTimeout(p.timer);
      if (d.ok === false || d.error) p.reject(new Error(String(d.error || 'completion failed')));
      else p.resolve(typeof d.text === 'string' ? d.text : typeof d.content === 'string' ? d.content : String(d.text == null ? '' : d.text));
    } catch (err) { /* never throw into the page */ }
  }

  window.addEventListener('message', onMessage, false);
  window.telecode = { __td: true, complete: complete };
  try {
    if (!window.claude) window.claude = { complete: complete };
    else if (typeof window.claude.complete !== 'function') window.claude.complete = complete;
  } catch (e) { /* ignore */ }
})();
