"""Brand extraction: URL → colours, fonts, logos, computed styles → draft design tokens.

Two passes, the second optional:

1. **Static** — the page HTML and up to `MAX_STYLESHEETS` linked stylesheets, every one fetched
   through `proxy.media_fetch` (the SSRF boundary: public addresses only, redirects re-validated,
   byte caps). Reads `<meta name="theme-color">`, icons / `og:image`, `<img>`/`<svg>` logo
   candidates, Google-Fonts links, `@font-face`, `font-family` declarations, `:root` custom
   properties and every colour literal (counted).
2. **Rendered** (`render=True`) — headless Edge over CDP. Every request the page makes is paused
   through the CDP `Fetch` domain and only continued when `media_fetch`'s address check passes, so
   a public page cannot make the browser fetch `http://127.0.0.1:…` or the metadata address on the
   caller's behalf. Then `getComputedStyle` of body / headings / links / buttons / inputs / nav /
   cards, same-origin `:root` variables, loaded font faces and logo candidates.
   Residual gaps, stated plainly: WebSocket / WebRTC connections are not interceptable through
   `Fetch`, and DNS could in principle rebind between our check and the browser's own lookup.

Output `tokens` follow the design_system kind's `tokens.json` shape — role names, not hues — with
`status: "extracted"` (read from a declared value), `"estimated"` (picked by frequency/role
heuristics) or `"proposed"` (defaulted). Fetched content is **data**: nothing from the page is ever
put into a prompt as an instruction by this module.
"""

from __future__ import annotations

import asyncio
import colorsys
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, unquote

log = logging.getLogger("telecode.services.design.brand_extract")

MAX_HTML_BYTES = 4 * 1024 * 1024
MAX_CSS_BYTES = 2 * 1024 * 1024
MAX_STYLESHEETS = 8
RENDER_TIMEOUT_SEC = 35.0
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class BrandExtractError(ValueError):
    pass


# ── Colour maths ──────────────────────────────────────────────────────────

_NAMED = {"white": (255, 255, 255), "black": (0, 0, 0), "red": (255, 0, 0), "blue": (0, 0, 255),
          "green": (0, 128, 0), "gray": (128, 128, 128), "grey": (128, 128, 128),
          "orange": (255, 165, 0), "yellow": (255, 255, 0), "purple": (128, 0, 128),
          "navy": (0, 0, 128), "teal": (0, 128, 128), "silver": (192, 192, 192)}

COLOR_RE = re.compile(
    r"#[0-9a-fA-F]{8}\b|#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3,4}\b|"
    r"\b(?:rgba?|hsla?|oklch)\([^()]*\)")


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def rgb_to_oklch(r: int, g: int, b: int) -> Tuple[float, float, float]:
    lr, lg, lb = (_srgb_to_linear(x / 255) for x in (r, g, b))
    l_ = 0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb
    m_ = 0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb
    s_ = 0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in (l_, m_, s_))
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    A = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    B = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    C = math.hypot(A, B)
    H = (math.degrees(math.atan2(B, A)) + 360) % 360
    return L, C, H if C > 1e-4 else 0.0


def oklch_to_rgb(L: float, C: float, H: float) -> Tuple[int, int, int]:
    a = C * math.cos(math.radians(H))
    b = C * math.sin(math.radians(H))
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    r = 4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_
    g = -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_
    bb = -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_
    return tuple(int(round(_linear_to_srgb(x) * 255)) for x in (r, g, bb))  # type: ignore[return-value]


def parse_color(value: str) -> Optional[Tuple[int, int, int, float]]:
    """CSS colour → (r, g, b, alpha). Supports hex, rgb[a], hsl[a], oklch and a few names."""
    v = (value or "").strip().lower()
    if v in _NAMED:
        return (*_NAMED[v], 1.0)
    if v.startswith("#"):
        h = v[1:]
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8) or not re.fullmatch(r"[0-9a-f]+", h):
            return None
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a
    m = re.fullmatch(r"(rgba?|hsla?|oklch)\((.*)\)", v)
    if not m:
        return None
    fn, args = m.group(1), re.split(r"[\s,/]+", m.group(2).strip())
    args = [x for x in args if x]
    if len(args) < 3:
        return None

    def num(x: str, scale: float = 1.0) -> float:
        return float(x[:-1]) / 100 * scale if x.endswith("%") else float(x.replace("deg", ""))
    try:
        alpha = num(args[3], 1.0) if len(args) > 3 else 1.0
        if fn.startswith("rgb"):
            r, g, b = (num(x, 255) for x in args[:3])
            return int(round(r)), int(round(g)), int(round(b)), alpha
        if fn.startswith("hsl"):
            h = num(args[0]) / 360
            s = num(args[1], 1.0)
            l = num(args[2], 1.0)
            r, g, b = colorsys.hls_to_rgb(h % 1, l, s)
            return int(round(r * 255)), int(round(g * 255)), int(round(b * 255)), alpha
        L = num(args[0], 1.0) if args[0].endswith("%") else float(args[0])
        C = num(args[1], 0.4) if args[1].endswith("%") else float(args[1])
        H = float(args[2].replace("deg", "")) if args[2] != "none" else 0.0
        return (*oklch_to_rgb(L, C, H), alpha)
    except (ValueError, IndexError):
        return None


def to_hex(rgb: Tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % rgb


def to_oklch_css(rgb: Tuple[int, int, int], alpha: float = 1.0) -> str:
    L, C, H = rgb_to_oklch(*rgb)
    base = f"oklch({L:.3f} {C:.3f} {H:.1f}"
    return base + (f" / {round(alpha * 100)}%)" if alpha < 0.999 else ")")


def _luminance(rgb: Tuple[int, int, int]) -> float:
    r, g, b = (_srgb_to_linear(x / 255) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: Tuple[int, int, int], b: Tuple[int, int, int]) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ── Static HTML parse ─────────────────────────────────────────────────────

class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._in_style = False
        self.styles: List[str] = []
        self.inline: List[str] = []
        self.stylesheets: List[str] = []
        self.icons: List[Dict[str, str]] = []
        self.meta: Dict[str, str] = {}
        self.logos: List[Dict[str, str]] = []
        self._svg_depth = 0
        self._svg_buf: List[str] = []
        self._svg_is_logo = False

    def handle_starttag(self, tag: str, attrs_list: List[Tuple[str, Optional[str]]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs_list}
        if self._svg_depth:
            self._svg_depth += 1
            self._svg_buf.append(self.get_starttag_text() or "")
            return
        if tag == "title":
            self._in_title = True
        elif tag == "style":
            self._in_style = True
        elif tag == "link":
            rel = a.get("rel", "").lower()
            href = a.get("href", "")
            if "stylesheet" in rel and href:
                self.stylesheets.append(href)
            elif "icon" in rel and href:
                self.icons.append({"rel": rel, "href": href, "sizes": a.get("sizes", "")})
            elif "mask-icon" in rel and href:
                self.icons.append({"rel": rel, "href": href, "color": a.get("color", "")})
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key in ("theme-color", "og:image", "og:site_name", "msapplication-tilecolor", "description",
                       "og:title", "application-name"):
                self.meta.setdefault(key, a.get("content", ""))
        elif tag == "img":
            hay = " ".join((a.get("class", ""), a.get("alt", ""), a.get("src", ""), a.get("id", ""))).lower()
            if ("logo" in hay or "brand" in hay) and a.get("src"):
                self.logos.append({"type": "img", "src": a["src"], "alt": a.get("alt", "")})
        elif tag == "svg":
            hay = " ".join((a.get("class", ""), a.get("id", ""), a.get("aria-label", ""))).lower()
            self._svg_depth = 1
            self._svg_buf = [self.get_starttag_text() or "<svg>"]
            self._svg_is_logo = "logo" in hay or "brand" in hay
        if a.get("style"):
            self.inline.append(a["style"])

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if self._svg_depth:
            self._svg_buf.append(self.get_starttag_text() or "")
            return
        if tag == "svg":
            return  # an empty self-closed <svg/> has nothing to keep
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self._svg_depth:
            self._svg_buf.append(f"</{tag}>")
            self._svg_depth -= 1
            if self._svg_depth == 0 and self._svg_is_logo and len(self.logos) < 8:
                markup = "".join(self._svg_buf)
                if len(markup) < 40000:
                    self.logos.append({"type": "svg", "markup": markup})
            return
        if tag == "title":
            self._in_title = False
        elif tag == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._svg_depth:
            self._svg_buf.append(data)
        elif self._in_title:
            self.title += data
        elif self._in_style:
            self.styles.append(data)


def _css_facts(css: str) -> Dict[str, Any]:
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    colors: Counter = Counter()
    for m in COLOR_RE.finditer(css):
        colors[m.group(0)] += 1
    families: Counter = Counter()
    for m in re.finditer(r"font-family\s*:\s*([^;}]+)", css, flags=re.I):
        first = m.group(1).split(",")[0].strip().strip("'\"")
        if first and not first.startswith("var("):
            families[first] += 1
    faces = [m.group(1).strip().strip("'\"") for m in
             re.finditer(r"@font-face\s*{[^}]*?font-family\s*:\s*([^;}]+)", css, flags=re.I | re.S)]
    root_vars: Dict[str, str] = {}
    for m in re.finditer(r"(?:^|})\s*(?::root|html)[^{]*{([^}]*)}", css):
        for vm in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+)", m.group(1)):
            root_vars[vm.group(1)] = vm.group(2).strip()
    imports = re.findall(r"@import\s+(?:url\()?\s*['\"]?([^'\")\s;]+)", css)
    radii = Counter(m.group(1) for m in re.finditer(r"border-radius\s*:\s*([\d.]+px)", css))
    return {"colors": colors, "families": families, "faces": faces, "vars": root_vars,
            "imports": imports, "radii": radii}


def _google_families(url: str) -> List[str]:
    if "fonts.googleapis.com" not in url:
        return []
    return [unquote(m.group(1)).replace("+", " ") for m in re.finditer(r"family=([^&:;]+)", url)]


# ── Rendered pass (CDP) ───────────────────────────────────────────────────

_PROBE_JS = r"""
(() => {
  const q = s => { try { return Array.from(document.querySelectorAll(s)).slice(0, 6); } catch (e) { return []; } };
  const pick = el => { const c = getComputedStyle(el); const r = el.getBoundingClientRect();
    return {tag: el.tagName.toLowerCase(), cls: String(el.getAttribute('class') || '').slice(0, 80),
      text: (el.innerText || '').trim().slice(0, 40), color: c.color, bg: c.backgroundColor, font: c.fontFamily,
      size: c.fontSize, weight: c.fontWeight, lh: c.lineHeight, ls: c.letterSpacing, radius: c.borderTopLeftRadius,
      pad: c.padding, shadow: c.boxShadow, border: c.borderTopWidth + ' ' + c.borderTopStyle + ' ' + c.borderTopColor,
      w: Math.round(r.width), h: Math.round(r.height)}; };
  const groups = {body: 'body', h1: 'h1', h2: 'h2', h3: 'h3', p: 'p', a: 'a[href]',
    button: 'button, [role=button], a[class*=btn], a[class*=button], .btn, .button, input[type=submit]',
    input: 'input[type=text], input[type=email], input[type=search], select, textarea',
    nav: 'nav, header', card: '[class*=card], article', code: 'code, pre', footer: 'footer'};
  const styles = {}; for (const k in groups) styles[k] = q(groups[k]).map(pick);
  const root = getComputedStyle(document.documentElement); const vars = {};
  for (const sh of Array.from(document.styleSheets)) { let rules; try { rules = sh.cssRules; } catch (e) { continue; }
    for (const r of Array.from(rules || [])) { if (r.selectorText && /(^|,)\s*(:root|html)\b/.test(r.selectorText)) {
      for (const n of Array.from(r.style)) if (n.startsWith('--') && Object.keys(vars).length < 400) vars[n] = root.getPropertyValue(n).trim(); } } }
  const hay = e => ((e.getAttribute('class') || '') + ' ' + (e.getAttribute('alt') || '') + ' ' + (e.getAttribute('src') || '') + ' ' +
    (e.id || '') + ' ' + (e.getAttribute('aria-label') || '') + ' ' + ((e.closest('a,header') || {}).className || '')).toLowerCase();
  const logos = Array.from(document.querySelectorAll('img, svg')).filter(e => /logo|brand/.test(hay(e))).slice(0, 6)
    .map(e => e.tagName.toLowerCase() === 'img' ? {type: 'img', src: e.currentSrc || e.src, alt: e.alt || ''}
                                                : {type: 'svg', markup: e.outerHTML.slice(0, 40000)});
  const fonts = []; try { document.fonts.forEach(f => { if (f.status === 'loaded') fonts.push(f.family.replace(/["']/g, '')); }); } catch (e) {}
  return JSON.stringify({title: document.title, url: location.href, styles, vars, logos, fonts: Array.from(new Set(fonts))});
})()
"""


async def _address_ok(url: str, cache: Dict[str, bool]) -> bool:
    from proxy import media_fetch
    p = urlparse(url)
    if p.scheme in ("data", "blob"):
        return True
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    key = f"{p.scheme}://{p.hostname}"
    if key not in cache:
        try:
            await asyncio.to_thread(media_fetch._check_url, url)
            cache[key] = True
        except media_fetch.MediaFetchError:
            cache[key] = False
    return cache[key]


async def render_probe(url: str, *, width: int = 1440, height: int = 900) -> Dict[str, Any]:
    """Load `url` in headless Edge with every request address-checked; return the probe JSON."""
    import aiohttp
    from services.design.ds_bundle import find_edge

    edge = find_edge()
    if not edge:
        raise BrandExtractError("headless render unavailable: Edge/Chromium not found")
    if not await _address_ok(url, {}):
        raise BrandExtractError("refusing to render a non-public URL")
    tmp = tempfile.mkdtemp(prefix="td-brand-")
    proc = subprocess.Popen(
        [edge, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         "--disable-extensions", "--disable-background-networking", "--disable-sync", "--mute-audio",
         "--remote-debugging-port=0", f"--user-data-dir={tmp}", f"--window-size={width},{height}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_CREATE_NO_WINDOW)
    try:
        port_file = Path(tmp) / "DevToolsActivePort"
        for _ in range(100):
            if port_file.exists() and port_file.read_text().strip():
                break
            await asyncio.sleep(0.1)
        lines = port_file.read_text().split()
        ws_url = f"ws://127.0.0.1:{lines[0]}{lines[1]}"
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(ws_url, max_msg_size=64 * 1024 * 1024) as ws:
                return await asyncio.wait_for(_drive(ws, url), timeout=RENDER_TIMEOUT_SEC)
    except asyncio.TimeoutError as exc:
        raise BrandExtractError("render timed out") from exc
    except (OSError, IndexError) as exc:
        raise BrandExtractError(f"render failed: {exc}") from exc
    finally:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


async def _drive(ws: Any, url: str) -> Dict[str, Any]:
    import aiohttp
    seq = 0
    pending: Dict[int, asyncio.Future] = {}
    session_box: Dict[str, str] = {}
    loaded = asyncio.Event()
    cache: Dict[str, bool] = {}
    blocked: List[str] = []

    async def send(method: str, params: Optional[Dict[str, Any]] = None, session: Optional[str] = None) -> Any:
        nonlocal seq
        seq += 1
        fut = asyncio.get_running_loop().create_future()
        pending[seq] = fut
        msg: Dict[str, Any] = {"id": seq, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        await ws.send_str(json.dumps(msg))
        return await fut

    async def on_paused(params: Dict[str, Any], session: str) -> None:
        rid = params["requestId"]
        req_url = params.get("request", {}).get("url", "")
        try:
            ok = await _address_ok(req_url, cache)
            if ok:
                await send("Fetch.continueRequest", {"requestId": rid}, session)
            else:
                blocked.append(req_url[:200])
                await send("Fetch.failRequest", {"requestId": rid, "errorReason": "AccessDenied"}, session)
        except Exception:
            pass

    async def reader() -> None:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                break
            data = json.loads(msg.data)
            if "id" in data and data["id"] in pending:
                fut = pending.pop(data["id"])
                if not fut.done():
                    if "error" in data:
                        fut.set_exception(BrandExtractError(str(data["error"].get("message"))))
                    else:
                        fut.set_result(data.get("result", {}))
            elif data.get("method") == "Fetch.requestPaused":
                asyncio.ensure_future(on_paused(data["params"], data.get("sessionId", "")))
            elif data.get("method") == "Page.loadEventFired":
                loaded.set()

    rtask = asyncio.ensure_future(reader())
    try:
        target = await send("Target.createTarget", {"url": "about:blank"})
        att = await send("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})
        sid = att["sessionId"]
        session_box["id"] = sid
        await send("Fetch.enable", {"patterns": [{"urlPattern": "*"}]}, sid)
        await send("Page.enable", {}, sid)
        await send("Page.navigate", {"url": url}, sid)
        try:
            await asyncio.wait_for(loaded.wait(), timeout=20)
        except asyncio.TimeoutError:
            pass  # evaluate whatever rendered
        await asyncio.sleep(1.5)  # late CSS-in-JS / font swaps
        res = await send("Runtime.evaluate", {"expression": _PROBE_JS, "returnByValue": True}, sid)
        value = (res.get("result") or {}).get("value")
        out = json.loads(value) if isinstance(value, str) else {}
        out["blocked_requests"] = blocked[:50]
        return out
    finally:
        rtask.cancel()


# ── Token drafting ────────────────────────────────────────────────────────

def _chroma(rgb: Tuple[int, int, int]) -> float:
    return rgb_to_oklch(*rgb)[1]


def _entry(rgb: Tuple[int, int, int], source: str, status: str, alpha: float = 1.0) -> Dict[str, Any]:
    return {"value": to_oklch_css(rgb, alpha), "hex": to_hex(rgb), "source": source, "status": status}


def draft_tokens(facts: Dict[str, Any]) -> Dict[str, Any]:
    """Role-named draft tokens from the gathered facts (see module docstring for status meanings)."""
    colors: Dict[str, Dict[str, Any]] = {}
    styles = (facts.get("computed") or {}).get("styles") or {}
    tally: Counter = Counter()
    where: Dict[Tuple[int, int, int], str] = {}

    def solid(value: str) -> Optional[Tuple[int, int, int]]:
        c = parse_color(value)
        return c[:3] if c and c[3] >= 0.9 else None

    for hex_or_css, n in (facts.get("color_counts") or {}).items():
        rgb = solid(hex_or_css)
        if rgb:
            tally[rgb] += n
            where.setdefault(rgb, "stylesheet")

    body = (styles.get("body") or [{}])[0] if styles.get("body") else {}
    bg = solid(body.get("bg", "")) if body else None
    fg = solid(body.get("color", "")) if body else None
    if bg:
        colors["background"] = _entry(bg, "computed body background-color", "extracted")
    if fg:
        colors["foreground"] = _entry(fg, "computed body color", "extracted")
    if not bg:
        bg = (255, 255, 255)
        colors["background"] = _entry(bg, "default (page background is transparent / not rendered)", "proposed")
    if not fg:
        dark = [c for c, _ in tally.most_common() if _luminance(c) < 0.05]
        fg = dark[0] if dark else (17, 17, 17)
        colors["foreground"] = _entry(fg, "most frequent dark colour" if dark else "default", "estimated" if dark else "proposed")

    # Primary: declared vars first, then buttons, theme-color, then the most frequent saturated colour.
    primary = None
    psrc = ""
    for name, val in (facts.get("css_vars") or {}).items():
        if re.search(r"(primary|brand|accent)(?!-foreground|-fg|-text|-contrast)", name):
            rgb = solid(val)
            if rgb and _chroma(rgb) > 0.03:
                primary, psrc = rgb, f"CSS variable {name}"
                break
    status = "extracted"
    if not primary:
        for b in styles.get("button") or []:
            rgb = solid(b.get("bg", ""))
            if rgb and rgb != bg and _chroma(rgb) > 0.03:
                primary, psrc = rgb, f"computed <{b.get('tag')}> background ({b.get('text')!r})"
                break
    if not primary and facts.get("theme_color"):
        rgb = solid(facts["theme_color"])
        if rgb:
            primary, psrc = rgb, "<meta name=theme-color>"
    if not primary:
        for b in styles.get("a") or []:
            rgb = solid(b.get("color", ""))
            if rgb and _chroma(rgb) > 0.05:
                primary, psrc, status = rgb, "computed link colour", "estimated"
                break
    if not primary:
        sat = [c for c, _ in tally.most_common(40) if _chroma(c) > 0.06 and c not in (bg, fg)]
        if sat:
            primary, psrc, status = sat[0], "most frequent saturated colour in stylesheets", "estimated"
    if primary:
        colors["primary"] = _entry(primary, psrc, status)
        on = max(((255, 255, 255), fg, (0, 0, 0)), key=lambda c: contrast(c, primary))
        btn_fg = None
        for b in styles.get("button") or []:
            if solid(b.get("bg", "")) == primary:
                btn_fg = solid(b.get("color", ""))
                break
        colors["primary-foreground"] = _entry(btn_fg or on, "computed button text" if btn_fg else
                                              "highest-contrast of white/foreground/black", "extracted" if btn_fg else "estimated")
    # Links as accent when they differ from primary.
    for a in styles.get("a") or []:
        rgb = solid(a.get("color", ""))
        if rgb and rgb not in (fg, primary) and _chroma(rgb) > 0.05:
            colors["accent"] = _entry(rgb, "computed link colour", "extracted")
            break
    # Muted foreground: the most common paragraph colour that is softer than fg but still readable
    # on the page background (a <p> inside a dark footer is not the muted text colour).
    pcolors = Counter(solid(p.get("color", "")) for p in styles.get("p") or [])
    for rgb, _ in pcolors.most_common():
        if rgb and rgb != fg and contrast(rgb, bg) >= 3.0 and contrast(rgb, bg) < contrast(fg, bg):
            colors["muted-foreground"] = _entry(rgb, "computed <p> colour", "extracted")
            break
    if "muted-foreground" not in colors:
        L, C, H = rgb_to_oklch(*fg)
        Lb = rgb_to_oklch(*bg)[0]
        colors["muted-foreground"] = _entry(oklch_to_rgb(L + (Lb - L) * 0.45, C * 0.6, H),
                                            "derived: 45% from foreground toward background", "proposed")
    # Surface / muted / border derived from the background.
    Lb, Cb, Hb = rgb_to_oklch(*bg)
    dark_mode = Lb < 0.5
    step = 0.04 if not dark_mode else -0.06
    for card in styles.get("card") or []:
        rgb = solid(card.get("bg", ""))
        if rgb and rgb != bg:
            colors["card"] = _entry(rgb, "computed card background", "extracted")
            break
    colors.setdefault("card", _entry(bg, "same as background", "proposed"))
    colors["muted"] = _entry(oklch_to_rgb(Lb - step * 0.75, Cb, Hb), "derived from background", "proposed")
    border = None
    for grp in ("input", "card", "nav"):
        for el in styles.get(grp) or []:
            parts = (el.get("border") or "").split(" ", 2)
            if len(parts) == 3 and parts[0] not in ("0px", "0") and parts[1] != "none":
                border = solid(parts[2])
                # A hairline border sits close to the background; a near-black rule is not "--border".
                if border and border != bg and 1.05 <= contrast(border, bg) <= 3.2:
                    colors["border"] = _entry(border, f"computed {grp} border", "extracted")
                    break
        if "border" in colors:
            break
    colors.setdefault("border", _entry(oklch_to_rgb(Lb - step * 2.2, Cb, Hb), "derived from background", "proposed"))
    colors.setdefault("ring", dict(colors.get("primary") or colors["border"], status="proposed", source="same as primary"))

    # Typography
    typo: Dict[str, Dict[str, Any]] = {}

    def first_family(value: str) -> Optional[str]:
        fam = (value or "").split(",")[0].strip().strip("'\"")
        return fam or None

    h1 = (styles.get("h1") or [{}])[0] if styles.get("h1") else {}
    if h1.get("font"):
        typo["font-display"] = {"value": h1["font"], "family": first_family(h1["font"]),
                                "source": "computed <h1> font-family", "status": "extracted"}
    if body.get("font"):
        typo["font-body"] = {"value": body["font"], "family": first_family(body["font"]),
                             "source": "computed body font-family", "status": "extracted"}
    code = (styles.get("code") or [{}])[0] if styles.get("code") else {}
    if code.get("font"):
        typo["font-mono"] = {"value": code["font"], "family": first_family(code["font"]),
                             "source": "computed <code> font-family", "status": "extracted"}
    fams = facts.get("font_families") or []
    if "font-body" not in typo and fams:
        typo["font-body"] = {"value": f"'{fams[0]}', ui-sans-serif, system-ui, sans-serif", "family": fams[0],
                             "source": "most used font-family in stylesheets", "status": "estimated"}
    if "font-display" not in typo and "font-body" in typo:
        typo["font-display"] = dict(typo["font-body"], status="proposed", source="same as body")
    if "font-mono" not in typo:
        mono = next((f for f in fams if "mono" in f.lower() or "code" in f.lower()), None)
        typo["font-mono"] = {"value": f"'{mono}', ui-monospace, monospace" if mono else "ui-monospace, 'SFMono-Regular', Consolas, monospace",
                             "family": mono, "source": "stylesheet" if mono else "default", "status": "estimated" if mono else "proposed"}
    sizes = set()
    for grp in ("h1", "h2", "h3", "p", "body", "button", "a"):
        for el in styles.get(grp) or []:
            px = re.fullmatch(r"([\d.]+)px", el.get("size") or "")
            if px:
                sizes.add(round(float(px.group(1))))
    scale = sorted(sizes)
    names = ["xs", "sm", "base", "lg", "xl", "2xl", "3xl", "4xl", "5xl", "6xl"]
    if scale:
        base_i = min(range(len(scale)), key=lambda i: abs(scale[i] - 16))
        start = max(0, 2 - base_i)
        for i, px in enumerate(scale[:len(names) - start]):
            typo[f"text-{names[start + i]}"] = {"value": f"{px}px", "source": "computed font sizes", "status": "extracted"}
    # Radius
    radius: Dict[str, Dict[str, Any]] = {}
    for grp, name in (("button", "radius-control"), ("input", "radius-input"), ("card", "radius-surface")):
        for el in styles.get(grp) or []:
            if el.get("radius") and el["radius"] not in ("0px",):
                radius[name] = {"value": el["radius"], "source": f"computed {grp} border-radius", "status": "extracted"}
                break
    if not radius and facts.get("radius_counts"):
        common = Counter(facts["radius_counts"]).most_common(1)[0][0]
        radius["radius-md"] = {"value": common, "source": "most common border-radius in stylesheets", "status": "estimated"}
    spacing = {f"space-{n}": {"value": f"{n * 4}px", "source": "default 4px grid", "status": "proposed"}
               for n in (1, 2, 3, 4, 6, 8, 12, 16)}
    return {"color": colors, "typography": typo, "radius": radius, "spacing": spacing,
            "theme": "dark" if dark_mode else "light"}


def tokens_css(tokens: Dict[str, Any], name: str = "Extracted") -> str:
    lines = [f"/* Draft tokens for {name} — generated by TeleDesign brand extract. Review every value;",
             " * status per token is in tokens.json (extracted | estimated | proposed). */", ":root {"]
    for k, v in (tokens.get("color") or {}).items():
        lines.append(f"  --{k}: {v['value']}; /* {v['status']}: {v['source'][:60]} */")
    for k, v in (tokens.get("typography") or {}).items():
        lines.append(f"  --{k}: {v['value']};")
    for k, v in (tokens.get("radius") or {}).items():
        lines.append(f"  --{k}: {v['value']};")
    for k, v in (tokens.get("spacing") or {}).items():
        lines.append(f"  --{k}: {v['value']};")
    lines.append("}")
    return "\n".join(lines) + "\n"


# ── Entry point ───────────────────────────────────────────────────────────

async def extract(url: str, *, render: bool = False) -> Dict[str, Any]:
    """Run the static pass (always) and the rendered pass (when asked); return facts + draft tokens."""
    from proxy import media_fetch

    url = (url or "").strip()
    if re.match(r"^[a-z][a-z0-9+.-]*:", url, flags=re.I) and not re.match(r"^https?://", url, flags=re.I):
        if not re.match(r"^[^/:]+:\d+", url):  # "host:port" shorthand is not a scheme
            raise BrandExtractError(f"unsupported URL scheme: {url.split(':', 1)[0]}")
    if not re.match(r"^https?://", url, flags=re.I):
        url = "https://" + url
    try:
        raw = await media_fetch.fetch_media_bytes(url, max_bytes=MAX_HTML_BYTES)
    except media_fetch.MediaFetchError as exc:
        raise BrandExtractError(str(exc)) from exc
    page = raw.decode("utf-8", "replace")
    parser = _PageParser()
    try:
        parser.feed(page)
    except Exception as exc:  # malformed HTML: keep what was parsed
        log.info("brand_extract: html parse stopped early: %s", exc)

    css_texts = list(parser.styles) + [f"x{{{s}}}" for s in parser.inline]
    sheet_urls = [urljoin(url, h) for h in parser.stylesheets][:MAX_STYLESHEETS]
    fonts_google: List[str] = []
    for u in sheet_urls:
        fonts_google += _google_families(u)

    async def get_css(u: str) -> Tuple[str, Optional[str]]:
        if "fonts.googleapis.com" in u:
            return u, None  # families come from the URL itself
        try:
            return u, (await media_fetch.fetch_media_bytes(u, max_bytes=MAX_CSS_BYTES)).decode("utf-8", "replace")
        except media_fetch.MediaFetchError as exc:
            log.info("brand_extract: stylesheet %s skipped: %s", u, exc)
            return u, None

    fetched = await asyncio.gather(*(get_css(u) for u in sheet_urls))
    sheets_ok = []
    for u, text in fetched:
        if text:
            css_texts.append(text)
            sheets_ok.append(u)
    colors: Counter = Counter()
    families: Counter = Counter()
    faces: List[str] = []
    root_vars: Dict[str, str] = {}
    radii: Counter = Counter()
    for css in css_texts:
        f = _css_facts(css)
        colors.update(f["colors"])
        families.update(f["families"])
        faces += f["faces"]
        root_vars.update(f["vars"])
        radii.update(f["radii"])
        for imp in f["imports"]:
            fonts_google += _google_families(imp)

    computed: Optional[Dict[str, Any]] = None
    render_error = None
    if render:
        try:
            computed = await render_probe(url)
            root_vars.update({k: v for k, v in (computed.get("vars") or {}).items() if v})
        except BrandExtractError as exc:
            render_error = str(exc)

    # Colour table
    table: Dict[str, Dict[str, Any]] = {}
    for lit, n in colors.most_common(200):
        c = parse_color(lit)
        if not c:
            continue
        hx = to_hex(c[:3])
        row = table.setdefault(hx, {"hex": hx, "oklch": to_oklch_css(c[:3]), "count": 0, "sources": set()})
        row["count"] += n
        row["sources"].add("stylesheet")
    for grp, els in ((computed or {}).get("styles") or {}).items():
        for el in els:
            for key in ("color", "bg"):
                c = parse_color(el.get(key, ""))
                if c and c[3] > 0.9:
                    hx = to_hex(c[:3])
                    row = table.setdefault(hx, {"hex": hx, "oklch": to_oklch_css(c[:3]), "count": 0, "sources": set()})
                    row["count"] += 3
                    row["sources"].add(f"computed {grp} {'background' if key == 'bg' else 'color'}")
    color_rows = sorted(table.values(), key=lambda r: -r["count"])[:32]
    for r in color_rows:
        r["sources"] = sorted(r["sources"])

    fam_list = [f for f, _ in families.most_common(12)]
    for f in fonts_google + faces + list((computed or {}).get("fonts") or []):
        if f and f not in fam_list:
            fam_list.append(f)
    font_rows = [{"family": f, "uses": families.get(f, 0), "google_fonts": f in fonts_google,
                  "font_face": f in faces} for f in fam_list[:16]]

    logos = []
    for lg in parser.logos + list((computed or {}).get("logos") or []):
        if lg.get("type") == "img" and lg.get("src"):
            lg = dict(lg, src=urljoin(url, lg["src"]))
        if lg not in logos:
            logos.append(lg)
    icons = [dict(i, href=urljoin(url, i["href"])) for i in parser.icons]
    og = parser.meta.get("og:image")

    facts = {"color_counts": dict(colors.most_common(200)), "css_vars": root_vars, "computed": computed,
             "theme_color": parser.meta.get("theme-color") or parser.meta.get("msapplication-tilecolor"),
             "font_families": fam_list, "radius_counts": dict(radii)}
    tokens = draft_tokens(facts)
    title = ((computed or {}).get("title") or parser.title or "").strip()
    return {
        "url": url,
        "title": title[:200],
        "site_name": parser.meta.get("og:site_name") or parser.meta.get("application-name") or "",
        "description": (parser.meta.get("description") or "")[:400],
        "rendered": computed is not None,
        "render_error": render_error,
        "blocked_requests": (computed or {}).get("blocked_requests", []),
        "stylesheets": sheets_ok,
        "theme_color": facts["theme_color"],
        "colors": color_rows,
        "fonts": font_rows,
        "css_vars": dict(list(root_vars.items())[:200]),
        "logos": logos[:10],
        "icons": icons[:10],
        "og_image": urljoin(url, og) if og else None,
        "computed": (computed or {}).get("styles"),
        "tokens": tokens,
        "tokens_css": tokens_css(tokens, title or urlparse(url).hostname or "site"),
    }
