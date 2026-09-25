"""Deck → PowerPoint (python-pptx, imported lazily).

Two modes (`options.mode`):

* ``screenshots`` (default) — one full-bleed picture per slide, speaker notes attached.
  Pixel-exact, not editable.
* ``editable`` — per slide, the text is read from the DOM (rects, runs, computed font /
  size / weight / colour / alignment / line height), the page is re-captured with every
  extracted glyph made transparent (``-webkit-text-fill-color`` — ``color`` itself is left
  alone so borders and icons drawn in ``currentColor`` survive), and the picture becomes
  the slide background under real, editable text boxes.

Options: ``fontSwaps`` ``{"Inter": "Arial"}`` (first family of each stack, case-insensitive),
``hideSelectors`` (hidden in both modes; deck controls are always hidden), ``scale`` (1–2,
screenshot density).

Validation flags on the job: ``duplicate_adjacent`` (two consecutive slides render
identically — usually navigation that did not move), ``slide_size_mismatch`` (a slide's
box differs from the declared canvas, or the canvas is not 16:9), ``no_speaker_notes``
(missing, or not one entry per slide), plus ``web_fonts`` (families PowerPoint will
substitute unless swapped) and ``deck_nav_forced``.

Notes come from ``<script type="application/json" id="td-speaker-notes">`` (or
``id="speaker-notes"``): a JSON array, one entry per slide.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.design import render

log = logging.getLogger("telecode.services.design.pptx_export")

SLIDE_WIDTH_EMU = 12192000  # 13.333 in — PowerPoint's 16:9 default
_OFFICE_FONTS = {
    "arial", "calibri", "cambria", "candara", "consolas", "constantia", "corbel", "courier new", "georgia",
    "segoe ui", "tahoma", "times new roman", "trebuchet ms", "verdana", "aptos", "helvetica", "garamond",
    "century gothic", "franklin gothic", "gill sans mt", "lucida console", "palatino linotype", "book antiqua",
    "impact", "segoe ui semibold", "segoe ui light", "arial black", "sans-serif", "serif", "monospace",
}
_GENERIC = {"sans-serif": "Arial", "serif": "Times New Roman", "monospace": "Consolas", "system-ui": "Segoe UI",
            "ui-sans-serif": "Segoe UI", "ui-serif": "Georgia", "ui-monospace": "Consolas",
            "-apple-system": "Segoe UI", "blinkmacsystemfont": "Segoe UI"}

_TEXT_JS = r"""
(slideIndex, W) => {
  const root = slideIndex ? window.__tdDeck.slides()[slideIndex - 1] : document.body;
  const rr = slideIndex ? root.getBoundingClientRect() : {x: 0, y: 0, width: innerWidth, height: innerHeight};
  const factor = rr.width ? W / rr.width : 1;
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'CANVAS', 'IFRAME', 'VIDEO', 'AUDIO',
                        'TEXTAREA', 'INPUT', 'SELECT', 'OPTION', 'IMG', 'PICTURE', 'OBJECT', 'EMBED']);
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const cx = cv.getContext('2d', {willReadFrequently: true});
  const cache = new Map();
  const rgba = (c) => {
    if (cache.has(c)) return cache.get(c);
    cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = c; cx.fillRect(0, 0, 1, 1);
    const d = cx.getImageData(0, 0, 1, 1).data; const v = [d[0], d[1], d[2], +(d[3] / 255).toFixed(3)];
    cache.set(c, v); return v;
  };
  const blockish = (cs) => cs.display !== 'inline' && cs.display !== 'contents';
  const hidden = (cs) => cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity < 0.02;
  const tt = (t, m) => m === 'uppercase' ? t.toUpperCase() : m === 'lowercase' ? t.toLowerCase()
      : m === 'capitalize' ? t.replace(/(^|\s)(\p{L})/gu, (a, s, c) => s + c.toUpperCase()) : t;
  const style = (cs) => {
    const fill = cs.webkitTextFillColor && cs.webkitTextFillColor !== cs.color ? cs.webkitTextFillColor : cs.color;
    let color = rgba(fill);
    if (color[3] === 0 && cs.backgroundClip === 'text') color = rgba(cs.color === fill ? '#000' : cs.color);
    return {font: cs.fontFamily, size: parseFloat(cs.fontSize), weight: parseInt(cs.fontWeight) || 400,
            italic: cs.fontStyle === 'italic' || cs.fontStyle.startsWith('oblique'),
            underline: (cs.textDecorationLine || '').includes('underline'),
            strike: (cs.textDecorationLine || '').includes('line-through'),
            color, ls: cs.letterSpacing === 'normal' ? 0 : parseFloat(cs.letterSpacing) || 0};
  };
  const pseudo = (el, which) => {
    const c = getComputedStyle(el, which).content;
    if (!c || c === 'none' || c === 'normal') return '';
    const m = c.match(/^"([\s\S]*)"$/); return m ? m[1] : '';
  };
  const collect = (block) => {
    const runs = [], rects = [];
    const walk = (node) => {
      for (const ch of node.childNodes) {
        if (ch.nodeType === 3) {
          const raw = ch.textContent; if (!raw) continue;
          const cs = getComputedStyle(ch.parentElement);
          const pre = /^pre/.test(cs.whiteSpace);
          const t = pre ? raw : raw.replace(/\s+/g, ' ');
          if (!t.trim() && !runs.length) continue;
          const rg = document.createRange(); rg.selectNodeContents(ch);
          for (const r of rg.getClientRects()) if (r.width > 0.5 && r.height > 0.5) rects.push(r);
          runs.push({t: tt(t, cs.textTransform), pre, ...style(cs)});
        } else if (ch.nodeType === 1) {
          if (ch instanceof SVGElement || SKIP.has(ch.tagName)) continue;
          if (ch.tagName === 'BR') { runs.push({br: true}); continue; }
          const cs = getComputedStyle(ch);
          if (hidden(cs) || blockish(cs)) continue;
          const b = pseudo(ch, '::before'); if (b) runs.push({t: b, ...style(cs)});
          walk(ch);
          const a = pseudo(ch, '::after'); if (a) runs.push({t: a, ...style(cs)});
        }
      }
    };
    const bcs = getComputedStyle(block);
    if (bcs.display === 'list-item' && bcs.listStyleType !== 'none') {
      const idx = [...block.parentElement.children].filter(c => c.tagName === 'LI').indexOf(block) + 1;
      runs.push({t: /decimal|numer/.test(bcs.listStyleType) ? `${idx}. ` : '• ', ...style(bcs)});
    }
    const b = pseudo(block, '::before'); if (b) runs.push({t: b, ...style(bcs)});
    walk(block);
    const a = pseudo(block, '::after'); if (a) runs.push({t: a, ...style(bcs)});
    return {runs, rects};
  };
  const blocks = [];
  const visit = (el) => {
    if (el instanceof SVGElement || SKIP.has(el.tagName)) return;
    if (el.closest('.deck-controls, [data-td-rt]')) return;
    const cs = getComputedStyle(el);
    if (cs.display === 'none') return;
    if ((el === root || blockish(cs)) && cs.visibility !== 'hidden' && +cs.opacity >= 0.02) {
      const {runs, rects} = collect(el);
      if (rects.length && runs.some(r => r.t && r.t.trim())) {
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        const tops = [];
        for (const r of rects) {
          x0 = Math.min(x0, r.left); y0 = Math.min(y0, r.top); x1 = Math.max(x1, r.right); y1 = Math.max(y1, r.bottom);
          if (!tops.some(t => Math.abs(t - r.top) < 3)) tops.push(r.top);
        }
        const size = parseFloat(cs.fontSize);
        const lh = cs.lineHeight === 'normal' ? size * 1.2 : parseFloat(cs.lineHeight);
        let align = cs.textAlign;
        if (align === 'start') align = cs.direction === 'rtl' ? 'right' : 'left';
        if (align === 'end') align = cs.direction === 'rtl' ? 'left' : 'right';
        blocks.push({x: (x0 - rr.x) * factor, y: (y0 - rr.y) * factor, w: (x1 - x0) * factor, h: (y1 - y0) * factor,
                     lines: tops.length, align, line_height: lh, runs, opacity: +cs.opacity});
        el.setAttribute('data-td-rt-txt', '');
      }
    }
    for (const c of el.children) visit(c);
  };
  visit(root);
  return {factor, blocks};
}
"""

_HIDE_TEXT_CSS = (
    "[data-td-rt-txt], [data-td-rt-txt] * { -webkit-text-fill-color: transparent !important;"
    " text-shadow: none !important; text-decoration-color: transparent !important;"
    " -webkit-text-stroke: 0 !important; }"
    "[data-td-rt-txt]::marker, [data-td-rt-txt] *::marker { color: transparent !important; }"
)


def _first_family(stack: str) -> str:
    return (stack.split(",")[0] if stack else "").strip().strip("'\"")


def _map_font(stack: str, swaps: Dict[str, str]) -> str:
    fam = _first_family(stack)
    low = fam.lower()
    for k, v in swaps.items():
        if k.lower() == low:
            return v
    return _GENERIC.get(low, fam) or "Arial"


async def _capture_slides(ctx, mode: str) -> Dict[str, Any]:
    opts = ctx.options
    scale = max(1.0, min(2.0, float(opts.get("scale") or 1)))
    hide = [".deck-controls"] + [s for s in (opts.get("hideSelectors") or []) if isinstance(s, str)]
    slides: List[Dict[str, Any]] = []
    async with render.open_page(1920, 1080, scale) as page:
        info = await render.load_for_render(page, ctx.url())
        await page.hide(hide)
        is_deck = bool(info.get("is_deck"))
        W, H = (int(info["w"]), int(info["h"])) if is_deck else (1920, 1080)
        count = int(info["count"]) if is_deck else 1
        for i in range(1, count + 1):
            nav = await render.deck_go(page, i) if is_deck else {}
            text = None
            if mode == "editable":
                text = await page.call(_TEXT_JS, i if is_deck else 0, W, timeout=60)
                await page.add_style(_HIDE_TEXT_CSS, "__td_rt_hidetext")
                await page.frame()
            png = await page.capture("png")
            if mode == "editable":
                await page.remove_style("__td_rt_hidetext")
                await page.eval("document.querySelectorAll('[data-td-rt-txt]').forEach(e => "
                                "e.removeAttribute('data-td-rt-txt'))", await_promise=False)
            slides.append({"png": png, "text": text, "forced": bool(nav.get("forced")), "size": nav.get("size")})
            ctx.progress(0.05 + 0.75 * i / count, f"Slide {i}/{count}")
        errors = page.errors
    return {"info": info, "is_deck": is_deck, "W": W, "H": H, "slides": slides, "errors": errors}


def _build(cap: Dict[str, Any], mode: str, swaps: Dict[str, str], out: Path) -> Dict[str, Any]:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
    from pptx.util import Emu, Pt

    W, H = cap["W"], cap["H"]
    prs = Presentation()
    prs.slide_width = Emu(SLIDE_WIDTH_EMU)
    prs.slide_height = Emu(round(SLIDE_WIDTH_EMU * H / W))
    emu_px = SLIDE_WIDTH_EMU / W
    pt_px = emu_px / 12700.0
    notes = cap["info"].get("notes") if cap["is_deck"] else None
    blank = prs.slide_layouts[6]
    align_map = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT,
                 "justify": PP_ALIGN.JUSTIFY}
    fonts_used: Dict[str, str] = {}
    text_boxes = 0

    for idx, s in enumerate(cap["slides"]):
        slide = prs.slides.add_slide(blank)
        slide.shapes.add_picture(io.BytesIO(s["png"]), 0, 0, width=prs.slide_width, height=prs.slide_height)
        if mode == "editable" and s["text"]:
            for b in s["text"]["blocks"]:
                runs = b["runs"]
                # CSS whitespace collapsing across run boundaries, then trim the ends.
                prev_space = True
                for r in runs:
                    if r.get("br") or r.get("pre"):
                        prev_space = bool(r.get("br"))
                        continue
                    t = r["t"]
                    if prev_space:
                        t = t.lstrip(" ")
                    prev_space = t.endswith(" ")
                    r["t"] = t
                for r in reversed(runs):
                    if r.get("br"):
                        continue
                    r["t"] = r["t"].rstrip(" ") if not r.get("pre") else r["t"]
                    if r["t"]:
                        break
                if not any((r.get("t") or "").strip() for r in runs):
                    continue
                w = b["w"] * 1.06 + 2
                x = b["x"]
                if b["align"] == "center":
                    x -= (w - b["w"]) / 2
                elif b["align"] == "right":
                    x -= (w - b["w"])
                x, y = max(0.0, x), max(0.0, b["y"])
                w = min(w, W - x) if W - x > 4 else w
                tb = slide.shapes.add_textbox(Emu(round(x * emu_px)), Emu(round(y * emu_px)),
                                              Emu(max(1, round(w * emu_px))), Emu(max(1, round(b["h"] * emu_px))))
                tf = tb.text_frame
                tf.word_wrap = b["lines"] > 1
                tf.auto_size = MSO_AUTO_SIZE.NONE
                tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
                tf.vertical_anchor = MSO_ANCHOR.TOP
                para = tf.paragraphs[0]

                def fmt_para(p):
                    p.alignment = align_map.get(b["align"], PP_ALIGN.LEFT)
                    if b.get("line_height"):
                        p.line_spacing = Pt(round(b["line_height"] * pt_px, 1))

                fmt_para(para)
                for r in runs:
                    if r.get("br"):
                        para = tf.add_paragraph()
                        fmt_para(para)
                        continue
                    chunks = r["t"].split("\n") if r.get("pre") else [r["t"]]
                    for ci, chunk in enumerate(chunks):
                        if ci:
                            para = tf.add_paragraph()
                            fmt_para(para)
                        if not chunk:
                            continue
                        run = para.add_run()
                        run.text = chunk
                        f = run.font
                        name = _map_font(r["font"], swaps)
                        fonts_used[_first_family(r["font"])] = name
                        f.name = name
                        f.size = Pt(round(r["size"] * pt_px * 2) / 2)
                        f.bold = r["weight"] >= 600
                        f.italic = bool(r["italic"])
                        f.underline = bool(r["underline"])
                        c = r["color"]
                        f.color.rgb = RGBColor(int(c[0]), int(c[1]), int(c[2]))
                        rpr = run._r.get_or_add_rPr()
                        if r.get("ls"):
                            rpr.set("spc", str(int(round(r["ls"] * pt_px * 100))))
                        if r.get("strike"):
                            rpr.set("strike", "sngStrike")
                        alpha = float(c[3]) * float(b.get("opacity") or 1)
                        if alpha < 0.99:
                            from lxml import etree
                            srgb = rpr.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}srgbClr")
                            if srgb is not None:
                                a = etree.SubElement(srgb, "{http://schemas.openxmlformats.org/drawingml/2006/main}alpha")
                                a.set("val", str(int(max(0.0, alpha) * 100000)))
                text_boxes += 1
        if notes and idx < len(notes) and notes[idx]:
            slide.notes_slide.notes_text_frame.text = str(notes[idx])
    prs.save(str(out))
    return {"fonts_used": fonts_used, "text_boxes": text_boxes}


async def export(ctx) -> Path:
    import asyncio

    mode = ctx.options.get("mode", "screenshots")
    if mode not in ("screenshots", "editable"):
        mode = "screenshots"
    swaps = {str(k): str(v) for k, v in (ctx.options.get("fontSwaps") or {}).items()
             if isinstance(k, str) and isinstance(v, str) and v.strip()}
    cap = await _capture_slides(ctx, mode)
    info, slides = cap["info"], cap["slides"]
    n = len(slides)

    # Validation flags
    if not cap["is_deck"]:
        ctx.flag("not_a_deck", "The page has no [data-td-slide] sections; exported the first 1920×1080 viewport "
                 "as a single slide")
    hashes = []
    for s in slides:
        hsh = hashlib.sha256(s["png"])
        if s["text"]:
            hsh.update(json.dumps([[b["x"], b["y"], [r.get("t") for r in b["runs"]]] for b in s["text"]["blocks"]],
                                  sort_keys=True).encode("utf-8"))
        hashes.append(hsh.hexdigest())
    dups = [i + 1 for i in range(1, n) if hashes[i] == hashes[i - 1]]
    if dups:
        ctx.flag("duplicate_adjacent", "Slide(s) " + ", ".join(str(d) for d in dups)
                 + " render identically to the slide before — navigation may not have moved",
                 slides=dups)
    if cap["is_deck"]:
        W, H = cap["W"], cap["H"]
        bad = [i + 1 for i, s in enumerate(slides)
               if s.get("size") and (abs(s["size"][0] - W) > 1 or abs(s["size"][1] - H) > 1)]
        if bad:
            ctx.flag("slide_size_mismatch", f"Slide box differs from the declared {W}×{H} canvas on slide(s) "
                     + ", ".join(map(str, bad[:10])), slides=bad)
        elif abs(W / H - 16 / 9) > 0.01:
            ctx.flag("slide_size_mismatch", f"Deck canvas is {W}×{H}, not 16:9 — the PowerPoint slide size was "
                     "set to match", width=W, height=H)
        notes = info.get("notes")
        if not notes:
            ctx.flag("no_speaker_notes", "The deck has no speaker notes (#td-speaker-notes)")
        elif len(notes) != n:
            ctx.flag("no_speaker_notes", f"{len(notes)} speaker-note entries for {n} slides", notes=len(notes))
        forced = [i + 1 for i, s in enumerate(slides) if s["forced"]]
        if forced:
            ctx.flag("deck_nav_forced", "The deck did not respond to td:slide navigation; slides were shown by "
                     "force for capture", slides=forced)
    if cap["errors"]:
        ctx.flag("console_errors", f"{len(cap['errors'])} console error(s) while rendering",
                 errors=cap["errors"][:5])

    ctx.progress(0.85, "Building PowerPoint")
    out = ctx.out_dir / ctx.name("pptx", "-editable" if mode == "editable" else "")
    built = await asyncio.to_thread(_build, cap, mode, swaps, out)
    if mode == "editable":
        web = sorted({src for src, dst in built["fonts_used"].items()
                      if dst.lower() not in _OFFICE_FONTS and src.lower() not in {k.lower() for k in swaps}})
        if web:
            ctx.flag("web_fonts", "PowerPoint will substitute these unless installed or swapped (fontSwaps): "
                     + ", ".join(web), fonts=web)
        ctx.job["stats"] = {"slides": n, "text_boxes": built["text_boxes"]}
    else:
        ctx.job["stats"] = {"slides": n}
    return out
