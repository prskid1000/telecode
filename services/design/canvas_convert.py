"""HTML boards ⇄ canvas layers, and live previews of layer boards (TeleDesign gaps #1, #14).

    to_layers(pid, src, …)     render the page in headless Edge (preview origin), take a
                               layout snapshot (every painted box / text run / image /
                               inline SVG with its measured rect and computed paint, after
                               the page's scripts ran) and hand it to the editor command
                               `telecode_import_html` (patches/open-pencil/0008), which
                               builds it as layers in a new frame beside the board.
    to_html(pid, node_id, …)   `telecode_export_html` (open-pencil's own dom-css HTML
                               export) → a project .html file → a new frame beside the
                               source registered as an HTML board showing that file.
    layer_preview(pid, node_id) the same export written to `.layers/<slug>.html` — hidden
                               from the Files rail (dot directory) but served by the preview
                               origin — for the canvas's Preview pane, rewritten on save.

Why a snapshot rather than open-pencil's DOM/CSS importer on the page source: TeleDesign
pages are mostly React/Babel, so their markup is empty until scripts run, and the
importer lays pages out in a 1000px sandbox, positions only flex children and reads
only data-URL images. The editor command still accepts `{html, css}` for that importer
(static markup, flexbox → auto-layout).

The snapshot only ever loads a §5 preview-origin URL built by `render.preview_url()`;
images are inlined in the page with `fetch()`, which the preview origin's CSP limits
to that origin, so nothing here reaches a caller-chosen host.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from services.design import editor_bridge, render, store

log = logging.getLogger("telecode.services.design.canvas_convert")

LAYER_PREVIEW_DIR = ".layers"
MAX_SNAPSHOT_NODES = 4000
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 24 * 1024 * 1024
IMPORT_TIMEOUT = 90.0
EXPORT_TIMEOUT = 45.0


class ConvertError(RuntimeError):
    pass


# Runs in the rendered page. Returns the layout snapshot `telecode_import_html`
# consumes (see html-layers.ts `SnapNode`). Colours go through a 2D canvas so any
# CSS colour syntax (oklch(), color-mix(), …) arrives as sRGB numbers.
_SNAPSHOT_JS = r"""
async (maxNodes, maxImage, maxTotal) => {
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const cx = cv.getContext('2d', { willReadFrequently: true });
  const rgba = (css) => {
    if (!css || css === 'transparent' || css === 'rgba(0, 0, 0, 0)') return null;
    cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = css; cx.fillRect(0, 0, 1, 1);
    // getImageData is un-premultiplied: straight sRGB + alpha.
    const d = cx.getImageData(0, 0, 1, 1).data;
    if (!d[3]) return null;
    return [d[0] / 255, d[1] / 255, d[2] / 255, +(d[3] / 255).toFixed(3)];
  };
  const px = (v) => { const n = parseFloat(v); return Number.isFinite(n) ? n : 0; };
  let count = 0, imgBytes = 0;
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'HEAD', 'META', 'LINK', 'TITLE', 'BR', 'WBR']);
  const sx = scrollX, sy = scrollY;
  const rectOf = (r) => ({ x: r.left + sx, y: r.top + sy, w: r.width, h: r.height });
  const nameOf = (el) => {
    const tid = el.getAttribute && (el.getAttribute('data-td-id') || el.getAttribute('data-td-screen'));
    if (tid) return tid;
    if (el.id) return el.tagName.toLowerCase() + '#' + el.id;
    const cls = (el.getAttribute && el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean)[0];
    return el.tagName.toLowerCase() + (cls ? '.' + cls : '');
  };
  const toDataURL = async (url) => {
    try {
      if (!url) return null;
      if (url.startsWith('data:')) return url.length < maxImage * 1.4 ? url : null;
      const res = await fetch(url);
      if (!res.ok) return null;
      const blob = await res.blob();
      if (blob.size > maxImage || imgBytes + blob.size > maxTotal) return null;
      if (/svg/.test(blob.type)) {
        // Rasterise SVG images (the canvas takes PNG/JPEG/WEBP fills).
        const img = new Image(); img.src = URL.createObjectURL(blob); await img.decode();
        const c = document.createElement('canvas'); c.width = Math.max(1, img.naturalWidth); c.height = Math.max(1, img.naturalHeight);
        c.getContext('2d').drawImage(img, 0, 0); URL.revokeObjectURL(img.src);
        const d = c.toDataURL('image/png'); imgBytes += d.length * 0.75; return d;
      }
      imgBytes += blob.size;
      return await new Promise((ok) => { const fr = new FileReader(); fr.onload = () => ok(fr.result); fr.onerror = () => ok(null); fr.readAsDataURL(blob); });
    } catch (e) { return null; }
  };
  const gradient = (bgi) => {
    const m = /^(repeating-)?(linear|radial)-gradient\((.*)\)$/.exec(bgi.trim());
    if (!m || m[1]) return null;
    const parts = []; let depth = 0, cur = '';
    for (const ch of m[3]) { if (ch === '(') depth++; if (ch === ')') depth--; if (ch === ',' && !depth) { parts.push(cur.trim()); cur = ''; } else cur += ch; }
    parts.push(cur.trim());
    let angle = 180;
    if (m[2] === 'linear' && parts.length) {
      const a = parts[0];
      if (/deg$/.test(a)) { angle = parseFloat(a); parts.shift(); }
      else if (/turn$/.test(a)) { angle = parseFloat(a) * 360; parts.shift(); }
      else if (/^to /.test(a)) {
        const t = a.slice(3); angle = { top: 0, right: 90, bottom: 180, left: 270, 'top right': 45, 'right top': 45, 'bottom right': 135, 'right bottom': 135, 'bottom left': 225, 'left bottom': 225, 'top left': 315, 'left top': 315 }[t] ?? 180; parts.shift();
      }
    } else if (m[2] === 'radial' && parts.length && !/^(rgb|#|hsl|oklch|lab|color)/.test(parts[0])) parts.shift();
    const stops = [];
    parts.forEach((p, i) => {
      const mm = /^(.*?\))\s*([\d.]+%)?\s*([\d.]+%)?$/.exec(p) || /^(\S+)\s*([\d.]+%)?$/.exec(p);
      if (!mm) return;
      const c = rgba(mm[1]); if (!c) return;
      const pos = mm[2] ? parseFloat(mm[2]) / 100 : (parts.length > 1 ? i / (parts.length - 1) : 0);
      stops.push([c, pos]);
    });
    return stops.length >= 2 ? { type: m[2], angle, stops } : null;
  };
  const shadows = (v) => {
    if (!v || v === 'none') return undefined;
    const out = []; let depth = 0, cur = ''; const list = [];
    for (const ch of v) { if (ch === '(') depth++; if (ch === ')') depth--; if (ch === ',' && !depth) { list.push(cur); cur = ''; } else cur += ch; }
    list.push(cur);
    for (const s of list) {
      const colM = /(rgba?\([^)]*\)|#[0-9a-f]{3,8}|[a-z]+\([^)]*\))/i.exec(s);
      const c = colM ? rgba(colM[1]) : [0, 0, 0, 1];
      const nums = s.replace(colM ? colM[1] : '', '').replace('inset', '').trim().split(/\s+/).map(px);
      if (!c || nums.length < 2) continue;
      out.push({ x: nums[0], y: nums[1], blur: nums[2] || 0, spread: nums[3] || 0, c, inset: /inset/.test(s) });
    }
    return out.length ? out : undefined;
  };
  const textOf = (cs, node, rects, text) => {
    const u = rects.reduce((a, r) => ({ l: Math.min(a.l, r.left), t: Math.min(a.t, r.top), r: Math.max(a.r, r.right), b: Math.max(a.b, r.bottom) }), { l: 1e9, t: 1e9, r: -1e9, b: -1e9 });
    const lines = new Set(rects.map((r) => Math.round(r.top))).size;
    const lh = cs.lineHeight === 'normal' ? 0 : px(cs.lineHeight);
    const ls = cs.letterSpacing === 'normal' ? 0 : px(cs.letterSpacing);
    const fam = (cs.fontFamily || '').split(',')[0].replace(/["']/g, '').trim();
    const ta = { center: 'CENTER', right: 'RIGHT', end: 'RIGHT', justify: 'JUSTIFIED' }[cs.textAlign] || 'LEFT';
    const tc = { uppercase: 'UPPER', lowercase: 'LOWER', capitalize: 'TITLE' }[cs.textTransform];
    const td = /underline/.test(cs.textDecorationLine) ? 'UNDERLINE' : /line-through/.test(cs.textDecorationLine) ? 'STRIKETHROUGH' : undefined;
    const t = { k: 'text', x: u.l + sx, y: u.t + sy, w: u.r - u.l, h: u.b - u.t, t: text, c: rgba(cs.color) || [0, 0, 0, 1],
      fs: px(cs.fontSize), fw: +cs.fontWeight || 400, ff: fam, it: cs.fontStyle === 'italic', lines };
    if (lh) t.lh = lh; if (ls) t.ls = ls; if (ta !== 'LEFT' && lines > 1) t.ta = ta; if (tc) t.tc = tc; if (td) t.td = td;
    return t;
  };
  const walk = async (el, parentOpacity) => {
    if (count >= maxNodes || SKIP.has(el.tagName)) return null;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || (cs.visibility === 'hidden' && el.tagName !== 'BODY')) return null;
    const r = el.getBoundingClientRect();
    const node = { k: 'box', n: nameOf(el), ...rectOf(r) };
    const op = +cs.opacity;
    if (op < 1) node.op = op;
    if (op === 0) return null;
    if (el instanceof SVGSVGElement) {
      if (r.width < 0.5 || r.height < 0.5) return null;
      const clone = el.cloneNode(true);
      clone.setAttribute('width', String(r.width)); clone.setAttribute('height', String(r.height));
      if (!clone.getAttribute('xmlns')) clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
      const color = cs.color;
      let svg = clone.outerHTML.replace(/currentColor/g, color);
      if (svg.length > 400000) return null;
      count++;
      return { k: 'svg', n: nameOf(el), ...rectOf(r), svg };
    }
    const bg = rgba(cs.backgroundColor); if (bg) node.bg = bg;
    const bgi = cs.backgroundImage;
    if (bgi && bgi !== 'none') {
      const g = gradient(bgi); if (g) node.grad = g;
      const u = /url\(["']?([^"')]+)["']?\)/.exec(bgi);
      if (u) { const d = await toDataURL(u[1]); if (d) { node.img = d; node.fit = cs.backgroundSize === 'contain' ? 'FIT' : 'FILL'; } }
    }
    const radii = [cs.borderTopLeftRadius, cs.borderTopRightRadius, cs.borderBottomRightRadius, cs.borderBottomLeftRadius].map((v) => {
      const s = String(v).split(' ')[0]; return /%$/.test(s) ? parseFloat(s) / 100 * Math.min(r.width, r.height) : px(s); });
    if (radii.some((v) => v > 0)) node.r = radii;
    const bw = [cs.borderTopWidth, cs.borderRightWidth, cs.borderBottomWidth, cs.borderLeftWidth].map(px);
    const bstyle = cs.borderTopStyle !== 'none' ? cs.borderTopStyle : cs.borderLeftStyle;
    if (bw.some((v) => v > 0) && bstyle !== 'none' && bstyle !== 'hidden') {
      const colors = [cs.borderTopColor, cs.borderRightColor, cs.borderBottomColor, cs.borderLeftColor];
      const idx = bw.findIndex((v) => v > 0);
      const bc = rgba(colors[idx]);
      if (bc) { node.bw = bw.map((v, i) => (cs[['borderTopStyle','borderRightStyle','borderBottomStyle','borderLeftStyle'][i]] === 'none' ? 0 : v)); node.bc = bc; if (bstyle === 'dashed' || bstyle === 'dotted') node.bs = bstyle; }
    }
    const sh = shadows(cs.boxShadow); if (sh) node.sh = sh;
    if (/(hidden|clip|auto|scroll)/.test(cs.overflowX + cs.overflowY) && el.tagName !== 'BODY' && el.tagName !== 'HTML') node.clip = true;
    if (el.tagName === 'IMG' || el.tagName === 'CANVAS' || el.tagName === 'VIDEO') {
      let d = null;
      if (el.tagName === 'IMG') d = await toDataURL(el.currentSrc || el.src);
      else if (el.tagName === 'CANVAS') { try { d = el.toDataURL('image/png'); } catch (e) {} }
      else if (el.poster) d = await toDataURL(el.poster);
      node.k = 'img'; if (d) node.img = d;
      node.fit = { contain: 'FIT', 'scale-down': 'FIT', none: 'CROP' }[cs.objectFit] || 'FILL';
      count++;
      return node;
    }
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') {
      const text = el.tagName === 'SELECT' ? (el.selectedOptions[0] || {}).textContent || '' : (el.value || el.placeholder || '');
      count++;
      const kids = [];
      if (text && el.type !== 'checkbox' && el.type !== 'radio' && el.type !== 'range') {
        const inner = { left: r.left + px(cs.paddingLeft) + px(cs.borderLeftWidth), top: r.top, right: r.right - px(cs.paddingRight), bottom: r.bottom };
        const t = textOf(cs, el, [inner], text);
        t.y = r.top + sy + (r.height - (px(cs.lineHeight) || px(cs.fontSize) * 1.25)) / 2; t.h = px(cs.lineHeight) || px(cs.fontSize) * 1.25;
        if (!el.value && el.placeholder) { const pc = rgba(getComputedStyle(el, '::placeholder').color); if (pc) t.c = pc; }
        kids.push(t);
      }
      if (kids.length) node.ch = kids;
      return node;
    }
    count++;
    const kids = [];
    for (const child of el.childNodes) {
      if (count >= maxNodes) break;
      if (child.nodeType === 3) {
        const raw = child.textContent;
        if (!raw || !raw.trim()) continue;
        const range = document.createRange(); range.selectNodeContents(child);
        const rects = [...range.getClientRects()].filter((q) => q.width > 0 && q.height > 0);
        if (!rects.length) continue;
        const text = cs.whiteSpace.startsWith('pre') ? raw : raw.replace(/\s+/g, ' ').trim();
        count++;
        kids.push(textOf(cs, el, rects, text));
      } else if (child.nodeType === 1) {
        const k = await walk(child, op);
        if (!k) continue;
        const bare = k.k === 'box' && !k.bg && !k.grad && !k.img && !k.bw && !k.sh && !k.clip && k.op === undefined;
        // Unpainted wrappers add nothing but a level: hoist a lone child, drop empty ones.
        if (bare && k.ch && k.ch.length === 1) kids.push(k.ch[0]);
        else if (bare && !k.ch) continue;
        else kids.push(k);
      }
    }
    if (kids.length) node.ch = kids;
    return node;
  };
  document.querySelectorAll('[data-td-rt],[data-td-overlay]').forEach((e) => e.remove && e.remove());
  const root = await walk(document.body, 1) || { k: 'box', x: 0, y: 0, w: innerWidth, h: innerHeight };
  const htmlBg = rgba(getComputedStyle(document.documentElement).backgroundColor);
  const bodyBg = rgba(getComputedStyle(document.body).backgroundColor);
  root.bg = bodyBg || htmlBg || [1, 1, 1, 1];
  const width = Math.max(document.documentElement.scrollWidth, innerWidth);
  const height = Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0, innerHeight);
  root.x = 0; root.y = 0; root.w = width; root.h = height; delete root.clip;
  return { title: document.title || '', width, height, truncated: count >= maxNodes, nodes: count, image_bytes: Math.round(imgBytes), root };
}
"""


def snapshot_js() -> str:
    return _SNAPSHOT_JS


async def layout_snapshot(pid: str, src: str, width: int = 1440, height: int = 900) -> Dict[str, Any]:
    """Render `src` from the project in headless Edge and return its layout snapshot."""
    if not store.valid_id(pid) or not store.project_dir(pid):
        raise ConvertError("Project not found")
    if not isinstance(src, str) or not store.safe_relpath(src) or not re.search(r"\.html?$", src, re.I):
        raise ConvertError("src must be a project-relative .html path")
    if not store.resolve_in(store.project_dir(pid), src) or not (store.project_dir(pid) / src).is_file():
        raise ConvertError(f"No such page: {src}")
    width = max(240, min(int(width or 1440), 3840))
    height = max(240, min(int(height or 900), 4320))
    url = render.preview_url(pid, src)
    async with render.open_page(width, height) as page:
        info = await render.load_for_render(page, url)
        if info.get("is_deck"):
            # A deck: convert the slide on screen (slide 1), at the deck's own size.
            await render.deck_go(page, 1)
        snap = await page.call(_SNAPSHOT_JS, MAX_SNAPSHOT_NODES, MAX_IMAGE_BYTES, MAX_TOTAL_IMAGE_BYTES,
                               timeout=90)
    if not isinstance(snap, dict) or not isinstance(snap.get("root"), dict):
        raise ConvertError("The page produced no layout")
    snap["src"] = src
    return snap


def _board(pid: str, key: str) -> Dict[str, Any]:
    boards = store.get_boards(pid) or {}
    b = boards.get(key)
    if not isinstance(b, dict) or not b.get("src"):
        raise ConvertError(f"Unknown HTML board: {key}")
    return b


async def _node_id_for_board(pid: str, key: str) -> Optional[str]:
    try:
        body = await editor_bridge.call(pid, "telecode_board_list", {}, 15)
    except editor_bridge.EditorBridgeError:
        return None
    for b in (body.get("result") or {}).get("boards") or []:
        if isinstance(b, dict) and b.get("key") == key:
            return b.get("node_id")
    return None


async def to_layers(pid: str, *, key: Optional[str] = None, src: Optional[str] = None,
                    width: Any = None, height: Any = None, name: Optional[str] = None) -> Dict[str, Any]:
    """Convert an HTML board (by board key) or any project page (by src) into canvas layers."""
    beside = None
    if key:
        board = _board(pid, key)
        src = board["src"]
        width = width or board.get("width")
        height = height or board.get("height")
        beside = await _node_id_for_board(pid, key)
    if not src:
        raise ConvertError("key or src required")
    try:
        w = int(float(width)) if width else 1440
        h = int(float(height)) if height else 900
    except (TypeError, ValueError):
        w, h = 1440, 900
    if not editor_bridge.status(pid)["connected"]:
        raise editor_bridge.EditorBridgeError(editor_bridge.APP_NOT_CONNECTED)
    snap = await layout_snapshot(pid, src, w, h)
    label = name or f"{Path(src).stem} (layers)"
    args: Dict[str, Any] = {"snapshot": snap, "name": label}
    if beside:
        args["beside_id"] = beside
    body = await editor_bridge.call(pid, "telecode_import_html", args, IMPORT_TIMEOUT)
    result = dict(body.get("result") or {})
    result.update({"src": src, "truncated": bool(snap.get("truncated")), "snapshot_nodes": snap.get("nodes")})
    return result


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip()).strip("-.").lower()
    return (s or "layers")[:60]


def _unique_path(pdir: Path, base: str) -> str:
    rel = f"{base}.html"
    n = 2
    while (pdir / rel).exists():
        rel = f"{base}-{n}.html"
        n += 1
    return rel


async def export_html(pid: str, node_id: str) -> Dict[str, Any]:
    if not isinstance(node_id, str) or not node_id or len(node_id) > 64:
        raise ConvertError("node_id required")
    body = await editor_bridge.call(pid, "telecode_export_html", {"id": node_id}, EXPORT_TIMEOUT)
    result = body.get("result") or {}
    if not isinstance(result.get("html"), str):
        raise ConvertError("The editor returned no HTML")
    return result


async def to_html(pid: str, node_id: str, path: Optional[str] = None) -> Dict[str, Any]:
    """Export frame `node_id` as a project .html page and put it on the canvas as an HTML board."""
    pdir = store.project_dir(pid)
    if not pdir:
        raise ConvertError("Project not found")
    exported = await export_html(pid, node_id)
    if path:
        if not store.safe_relpath(path) or not path.lower().endswith(".html") or path.split("/")[0].startswith("."):
            raise ConvertError("path must be a project-relative .html path")
        rel = path
    else:
        rel = _unique_path(pdir, _slug(exported.get("name") or "frame"))
    target = store.resolve_in(pdir, rel)
    if target is None:
        raise ConvertError("path must stay inside the project")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(exported["html"], encoding="utf-8")
    width = int(round(float(exported.get("width") or 1440)))
    height = int(round(float(exported.get("height") or 900)))
    # A new frame to the right of the source, then register it as a board for the file.
    bounds = await editor_bridge.call(pid, "node_bounds", {"id": node_id}, 15)
    b = bounds.get("result") or {}
    try:
        x = float(b.get("x", 0)) + float(b.get("width", width)) + 120
        y = float(b.get("y", 0))
    except (TypeError, ValueError):
        x, y = 0.0, 0.0
    created = await editor_bridge.call(pid, "create_shape", {
        "type": "FRAME", "name": f"{Path(rel).stem} (HTML)", "width": width, "height": height,
        "x": round(x), "y": round(y)}, 15)
    new_id = (created.get("result") or {}).get("id")
    if not new_id:
        raise ConvertError("Could not create the board frame")
    board = await editor_bridge.register_board(pid, new_id, rel, width, height)
    return {"path": rel, "board": board, "node_id": new_id, "source_id": node_id,
            "bytes": len(exported["html"].encode("utf-8"))}


async def layer_preview(pid: str, node_id: str) -> Dict[str, Any]:
    """Write frame `node_id`'s HTML export to `.layers/<slug>.html` for the Preview pane."""
    pdir = store.project_dir(pid)
    if not pdir:
        raise ConvertError("Project not found")
    exported = await export_html(pid, node_id)
    safe_id = re.sub(r"[^A-Za-z0-9]+", "-", node_id).strip("-")[:24] or "node"
    rel = f"{LAYER_PREVIEW_DIR}/{_slug(exported.get('name') or 'frame')}-{safe_id}.html"
    target = pdir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(exported["html"], encoding="utf-8")
    return {"path": rel, "url": render.preview_url(pid, rel), "name": exported.get("name"),
            "width": exported.get("width"), "height": exported.get("height")}
