/*
 * td-babel-source.js — TeleDesign preview runtime.
 *
 * Registers a Babel-standalone plugin ("td-source") that stamps every host JSX element
 * (<div>, <button>, <svg> … — never components or fragments) with
 *
 *     data-td-src="<project-relative file>:<line>:<col>"      (both 1-based)
 *
 * and makes it a default plugin for every `text/babel` / `text/jsx` script on the page, so the
 * inspector can write edits back to the exact JSX tag deterministically.
 *
 * Positions:
 *   - external scripts (`<script type="text/babel" src="app/cards.jsx">`) → `app/cards.jsx:L:C`
 *   - inline scripts → the position inside the *HTML file* (`index.html:L:C`). Babel only knows
 *     the position inside the script text, so for pages with inline Babel scripts this file
 *     defers Babel's own DOMContentLoaded run, fetches the page source once (same origin), maps
 *     every inline script to its offset, then runs `Babel.transformScriptTags()` itself.
 *     The preview server's own injected `/_td/*` tags are discounted, so line numbers match the
 *     file on disk. If the fetch fails, inline elements are simply not stamped.
 *
 * Load order: the preview server injects this before the first text/babel script; it also copes
 * with being loaded before Babel itself. It never throws into the page.
 *
 * Original TeleDesign code.
 */
(function () {
  'use strict';
  if (window.__tdBabelSource) return;
  var TDS = (window.__tdBabelSource = { plugin: 'td-source', inline: [], ready: false, errors: [] });

  var DEFAULT_PLUGINS = ['transform-class-properties', 'transform-object-rest-spread', 'transform-flow-strip-types'];
  var BABEL_TYPES = { 'text/babel': 1, 'text/jsx': 1 };
  // Babel resolves filenames like paths: "Inline Babel script" → "/Inline Babel script",
  // "http://host/x.jsx" → "http:/host/x.jsx". Both are undone below.
  var INLINE_RE = /(?:^|[\\/])Inline Babel script(?: \((\d+)\))?$/;

  function note(e) {
    try { TDS.errors.push(String((e && e.message) || e)); } catch (_) { /* ignore */ }
  }

  // ---- paths -------------------------------------------------------------------------------

  // "/p/<pid>/checkout/app.jsx" → "checkout/app.jsx". Outside /p/<pid>/ → path without leading "/".
  function projectPath(href) {
    try {
      href = String(href).replace(/^[\\/]+(?=[a-z][a-z0-9+.-]*:)/i, '').replace(/^([a-z][a-z0-9+.-]*):\/(?!\/)/i, '$1://');
      var u = new window.URL(href, location.href);
      if (u.origin !== location.origin) return u.href;
      var p = decodeURIComponent(u.pathname);
      var m = /^\/p\/[^/]+\/(.*)$/.exec(p);
      return m ? m[1] : p.replace(/^\/+/, '');
    } catch (e) {
      return String(href);
    }
  }
  TDS.projectPath = projectPath;

  function pageFile() {
    return projectPath(location.pathname) || 'index.html';
  }

  // ---- the plugin --------------------------------------------------------------------------

  function tdSourcePlugin(babel) {
    var t = babel.types;
    return {
      name: 'td-source',
      visitor: {
        JSXOpeningElement: function (path, state) {
          try {
            var node = path.node;
            var name = node.name;
            if (!name || name.type !== 'JSXIdentifier') return; // <Foo.Bar>, <ns:tag>
            if (!/^[a-z]/.test(name.name)) return; // components start upper-case
            if (!node.loc) return;
            var attrs = node.attributes || [];
            for (var i = 0; i < attrs.length; i++) {
              var a = attrs[i];
              if (a.type === 'JSXAttribute' && a.name && a.name.name === 'data-td-src') return;
            }
            var where = locate(state, node.loc.start);
            if (!where) return;
            attrs.push(t.jsxAttribute(t.jsxIdentifier('data-td-src'), t.stringLiteral(where)));
          } catch (e) {
            note(e);
          }
        }
      }
    };
  }

  // Map a Babel location (line 1-based, column 0-based, relative to the script text) to
  // "<file>:<line>:<col>" (1-based) in the source file.
  function locate(state, start) {
    var filename = (state && state.file && state.file.opts && state.file.opts.filename) || state.filename || '';
    var m = INLINE_RE.exec(filename);
    if (m) {
      var idx = m[1] ? +m[1] - 1 : 0;
      var info = TDS.inline[idx];
      if (!info) return null;
      var line = info.line + start.line - 1;
      var col = (start.line === 1 ? info.col + start.column : start.column) + 1;
      return info.file + ':' + line + ':' + col;
    }
    if (!filename) return null;
    return projectPath(filename) + ':' + start.line + ':' + (start.column + 1);
  }

  // ---- registration ------------------------------------------------------------------------

  function register(B) {
    if (!B || typeof B.registerPlugin !== 'function' || B.__tdSourceRegistered) return !!(B && B.__tdSourceRegistered);
    try {
      if (!(B.availablePlugins && B.availablePlugins['td-source'])) B.registerPlugin('td-source', tdSourcePlugin);
      B.__tdSourceRegistered = true;
      return true;
    } catch (e) {
      note(e);
      return false;
    }
  }

  if (!register(window.Babel)) {
    // Babel not loaded yet: catch the UMD assignment `root.Babel = factory()`.
    try {
      var existing = Object.getOwnPropertyDescriptor(window, 'Babel');
      if (!existing || existing.configurable) {
        var held;
        Object.defineProperty(window, 'Babel', {
          configurable: true,
          enumerable: true,
          get: function () { return held; },
          set: function (v) {
            held = v;
            register(v);
            try {
              Object.defineProperty(window, 'Babel', { value: v, writable: true, configurable: true, enumerable: true });
            } catch (e) { note(e); }
          }
        });
      }
    } catch (e) {
      note(e);
    }
  }

  // ---- default plugin on every Babel script ------------------------------------------------

  function babelScripts() {
    var out = [];
    var all = document.getElementsByTagName('script');
    for (var i = 0; i < all.length; i++) {
      var type = (all[i].type || '').split(';')[0].trim().toLowerCase();
      if (BABEL_TYPES[type]) out.push(all[i]);
    }
    return out;
  }

  function addPluginAttr(script) {
    var raw = script.getAttribute('data-plugins');
    var list = raw == null ? DEFAULT_PLUGINS.slice() : raw.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
    if (list.indexOf('td-source') === -1) list.push('td-source');
    script.setAttribute('data-plugins', list.join(','));
  }

  // Remove the preview server's injected runtime tags so offsets match the file on disk.
  var INJECTED_RE = /<script\b[^>]*\bsrc=["']\/_td\/[^"']*["'][^>]*>\s*<\/script>(\r?\n)?/gi;
  var OPEN_SCRIPT_RE = /<script\b([^>]*)>/gi;
  var COMMENT_RE = /<!--[\s\S]*?-->/g;

  function mapInlineScripts(source, file) {
    var src = source.replace(INJECTED_RE, function (tag, nl, offset, whole) {
      // A tag on a line of its own was added together with its newline: drop both.
      // A tag inserted mid-line keeps whatever newline follows it (that one is the file's).
      var ownLine = offset === 0 || whole.charAt(offset - 1) === '\n';
      return nl && !ownLine ? nl : '';
    });
    // Blank out comments (keep newlines) so commented-out scripts are not counted.
    src = src.replace(COMMENT_RE, function (c) { return c.replace(/[^\n]/g, ' '); });
    var result = [];
    var m;
    OPEN_SCRIPT_RE.lastIndex = 0;
    while ((m = OPEN_SCRIPT_RE.exec(src))) {
      var attrs = m[1] || '';
      var tm = /\btype\s*=\s*["']?([^"'\s>]+)/i.exec(attrs);
      var type = tm ? tm[1].split(';')[0].toLowerCase() : '';
      if (!BABEL_TYPES[type]) continue;
      if (/\bsrc\s*=/i.test(attrs)) continue;
      var startIdx = m.index + m[0].length;
      var pre = src.slice(0, startIdx);
      var line = 1;
      for (var i = 0; i < pre.length; i++) if (pre.charCodeAt(i) === 10) line++;
      var lastNl = pre.lastIndexOf('\n');
      var col = startIdx - (lastNl + 1); // 0-based column of the first content char
      result.push({ file: file, line: line, col: col });
      var end = src.indexOf('</script', startIdx);
      OPEN_SCRIPT_RE.lastIndex = end === -1 ? src.length : end;
    }
    return result;
  }
  TDS.mapInlineScripts = mapInlineScripts;

  function runBabel() {
    try {
      var B = window.Babel;
      register(B);
      if (B && typeof B.transformScriptTags === 'function') B.transformScriptTags();
    } catch (e) {
      // A compile error in the page's own JSX: surface it like Babel would (uncaught).
      setTimeout(function () { throw e; }, 0);
    }
  }

  function onReady() {
    try {
      var scripts = babelScripts();
      if (!scripts.length) return;
      var B = window.Babel;
      register(B);
      scripts.forEach(addPluginAttr);
      var hasInline = scripts.some(function (s) { return !s.src; });
      if (!hasInline || !B || typeof B.disableScriptTags !== 'function' || typeof window.fetch !== 'function') {
        TDS.ready = true;
        return; // Babel's own DOMContentLoaded handler (on window, runs after us) takes it from here.
      }
      // Hold Babel's automatic run until inline offsets are known.
      B.disableScriptTags();
      var file = pageFile();
      var done = false;
      var finish = function (map) {
        if (done) return;
        done = true;
        TDS.inline = map || [];
        TDS.ready = true;
        runBabel();
      };
      var timer = setTimeout(function () { finish([]); }, 4000);
      window.fetch(location.href, { cache: 'no-store', credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.text() : ''; })
        .then(function (text) {
          clearTimeout(timer);
          finish(text ? mapInlineScripts(text, file) : []);
        })
        .catch(function () {
          clearTimeout(timer);
          finish([]);
        });
    } catch (e) {
      note(e);
    }
  }

  // `document` listeners fire before `window` listeners for DOMContentLoaded, so this runs before
  // Babel's own handler even though Babel registered first.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', onReady, false);
  } else {
    // Late load: Babel has already run (or will run on demand) — just make sure the plugin exists.
    TDS.ready = true;
  }
})();
