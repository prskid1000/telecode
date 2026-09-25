"""Design-system adherence lint for TeleDesign projects.

Rules come from the attached system's `adherence.json` (schema `teledesign-adherence/v1`, see the
seeds) — or, for a system that has none, from `derive_adherence()`, which reads its tokens and
manifest. Four families, as the parity checklist asks:

  * values     — raw colour (hex / rgb / hsl / oklch / named) outside the files the system allows
                 (`no-raw-color.allowIn`, normally `tokens.css`), px off the spacing / radius /
                 font-size scales, font weights off the allowed set, `var(--x)` of a token nothing
                 defines (`unknown-token`)
  * fonts      — any primary font family (CSS, JSX style objects, Google-Fonts links, `.pen`
                 fontFamily) not in `font-family.allowedFamilies`
  * imports    — `no-restricted-imports` (ES import / require / CDN script+link URLs), plus icon
                 libraries other than `icons.allowedLibraries`
  * components — `forbid-elements` (react/forbid-elements shape) and per-component prop enums from
                 `components.props` (`<Button variant="fancy">` is an error)

Built-in Babel-standalone pitfalls are checked too (a bare top-level `const styles =` collides
across text/babel scripts; `type="module"` next to text/babel breaks the global-export pattern).

Scopes: `.css`, `.html`/`.htm` (only <style> blocks, style="" and colour attributes are read as CSS
— text content such as a swatch label "oklch(1 0 0)" is not a violation), `.jsx/.js/.tsx/.ts`
(string literals, style objects, embedded CSS), and `.pen` JSON (fills, strokes, effects, fonts,
padding/gap/radius numbers, `$--variable` references).

No dependencies, no Node: a regex/tokenizer lint is deliberate — it has to run inside the proxy on
every verifier pass, and its findings are advice for the agent, not a compiler gate.
"""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

Finding = Dict[str, Any]

MAX_FILES = 400
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FINDINGS_PER_FILE = 200
MAX_FINDINGS = 2000

# Directories inside a project that are never the project's own design output.
EXCLUDE_DIRS = {"_ds", ".versions", ".td", "chats", "uploads", "imports", "node_modules", ".git",
                "scraps", "__pycache__"}
TEXT_EXTS = {".css", ".html", ".htm", ".jsx", ".js", ".tsx", ".ts", ".mjs"}
PEN_EXTS = {".pen"}

_SEVERITIES = ("error", "warn", "info")

GENERIC_FAMILIES = {
    "serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-sans-serif",
    "ui-serif", "ui-monospace", "ui-rounded", "math", "emoji", "fangsong", "inherit", "initial",
    "unset", "revert", "-apple-system", "blinkmacsystemfont",
}

# CSS named colours (level 4) minus the keywords that are always fine.
_NAMED_COLORS = set("""
aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown
burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan
darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid
darkred darksalmon darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet
deeppink deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro
ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki
lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow
lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray
lightslategrey lightsteelblue lightyellow lime limegreen linen magenta maroon mediumaquamarine
mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen
mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace
olive olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred
papayawhip peachpuff peru pink plum powderblue purple rebeccapurple red rosybrown royalblue
saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue slategray slategrey
snow springgreen steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke yellow
yellowgreen
""".split())

_COLOR_PROPS = {
    "color", "background", "background-color", "border", "border-color", "border-top",
    "border-right", "border-bottom", "border-left", "border-top-color", "border-right-color",
    "border-bottom-color", "border-left-color", "outline", "outline-color", "fill", "stroke",
    "stop-color", "flood-color", "caret-color", "accent-color", "text-decoration-color",
    "column-rule-color", "box-shadow", "text-shadow", "background-image", "border-block-color",
    "border-inline-color",
}
_COLOR_ATTRS = ("fill", "stroke", "color", "bgcolor", "stop-color", "flood-color")

# Icon libraries we recognise in import specifiers and CDN URLs.
_ICON_LIBS = {
    "lucide": ("lucide", "lucide-react", "lucide-static"),
    "font-awesome": ("font-awesome", "@fortawesome/*", "fontawesome"),
    "heroicons": ("heroicons", "@heroicons/*"),
    "material-icons": ("material-icons", "material-symbols", "@mui/icons-material", "@material-design-icons/*"),
    "bootstrap-icons": ("bootstrap-icons",),
    "feather": ("feather-icons", "react-feather"),
    "phosphor": ("@phosphor-icons/*", "phosphor-react", "phosphor-icons"),
    "tabler": ("@tabler/icons*", "tabler-icons"),
    "ionicons": ("ionicons",),
    "remixicon": ("remixicon", "remixicon-react"),
    "iconify": ("@iconify/*", "iconify-icon"),
}

_RAW_COLOR_DEFAULT = r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla|hwb|oklch|oklab|lab|lch|color)\("


# ── Config ────────────────────────────────────────────────────────────────

def load_adherence(system_dir: Path) -> Dict[str, Any]:
    """The system's adherence rules, or rules derived from its tokens when it ships none."""
    try:
        data = json.loads((system_dir / "adherence.json").read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("rules"), dict):
            return data
    except (OSError, ValueError):
        pass
    return derive_adherence(system_dir)


def parse_custom_properties(css: str) -> Dict[str, str]:
    """Every `--name: value` declared in `css` (last one wins)."""
    out: Dict[str, str] = {}
    for m in re.finditer(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;{}]+)", _strip_css_comments(css)):
        out[m.group(1)] = m.group(2).strip()
    return out


def _px_of(value: str) -> Optional[float]:
    m = re.fullmatch(r"\s*(-?\d*\.?\d+)(px)?\s*", value or "")
    if m:
        return float(m.group(1))
    m = re.fullmatch(r"\s*(-?\d*\.?\d+)rem\s*", value or "")
    return float(m.group(1)) * 16 if m else None


def derive_adherence(system_dir: Path) -> Dict[str, Any]:
    """Build adherence rules from tokens.css (+ tokens/*.css) and manifest.json.

    Used for imported / extracted systems that have no adherence.json yet. Colour tokens are the
    custom properties whose value parses as a colour; the spacing/radius/font-size scales are the
    px values of `--space*` / `--radius*` / `--text*|--font-size*` tokens; families come from the
    manifest's fonts or the first family of every `--font*` token.
    """
    css = ""
    for f in [system_dir / "tokens.css", *sorted((system_dir / "tokens").glob("*.css"))]:
        try:
            css += f.read_text(encoding="utf-8", errors="replace") + "\n"
        except OSError:
            pass
    props = parse_custom_properties(css)
    raw_re = re.compile(_RAW_COLOR_DEFAULT)
    color_tokens = sorted(k for k, v in props.items() if raw_re.search(v) and "shadow" not in k)

    def scale(prefixes: Tuple[str, ...]) -> Tuple[List[str], List[float]]:
        names, vals = [], set()
        for k, v in props.items():
            if k.startswith(prefixes):
                names.append(k)
                px = _px_of(v)
                if px is not None:
                    vals.add(px)
        return sorted(names), sorted(vals)

    space_tokens, space_px = scale(("--space", "--spacing", "--gap"))
    radius_tokens, radius_px = scale(("--radius", "--rounded"))
    text_tokens, text_px = scale(("--text-", "--font-size"))
    families: List[str] = []
    try:
        man = json.loads((system_dir / "manifest.json").read_text(encoding="utf-8"))
        for f in man.get("fonts") or []:
            fam = f.get("family") if isinstance(f, dict) else None
            if fam and fam not in families:
                families.append(fam)
    except (OSError, ValueError):
        man = {}
    for k, v in props.items():
        if k.startswith("--font") and "weight" not in k and "size" not in k:
            first = _families(v)[:1]
            for fam in first:
                if fam.lower() not in GENERIC_FAMILIES and not fam.startswith("var(") and fam not in families:
                    families.append(fam)
    comp_props: Dict[str, Dict[str, List[str]]] = {}
    for c in (man.get("components") or []) if isinstance(man, dict) else []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        enums = {}
        for pname, spec in (c.get("props") or {}).items():
            if isinstance(spec, dict) and spec.get("type") == "enum" and isinstance(spec.get("values"), list):
                enums[pname] = [str(x) for x in spec["values"]]
            elif isinstance(spec, list):
                enums[pname] = [str(x) for x in spec]
        if enums:
            comp_props[c["name"]] = enums
    rules: Dict[str, Any] = {
        "no-raw-color": {"severity": "error", "allowIn": ["tokens.css", "tokens/*.css"], "pattern": _RAW_COLOR_DEFAULT,
                         "allowValues": ["transparent", "currentColor", "inherit"],
                         "message": "Use a colour token."},
        "color-tokens": {"severity": "error", "allowed": color_tokens},
        "font-family": {"severity": "error", "allowedFamilies": families,
                        "genericFallbacks": sorted(GENERIC_FAMILIES)},
        "components": {"severity": "error", "props": comp_props},
    }
    if space_px:
        rules["spacing"] = {"severity": "warn", "tokens": space_tokens, "scalePx": space_px,
                            "allowRawPx": [0, 1],
                            "properties": ["margin", "padding", "gap", "row-gap", "column-gap", "inset",
                                           "top", "right", "bottom", "left"]}
    if radius_px:
        rules["radius"] = {"severity": "warn", "tokens": radius_tokens, "scalePx": radius_px}
    if text_px:
        rules["font-size"] = {"severity": "warn", "tokens": text_tokens, "scalePx": text_px}
    return {"schema": "teledesign-adherence/v1", "system": system_dir.name, "derived": True, "rules": rules}


class _Cfg:
    """Pre-compiled view of an adherence document plus the known token names."""

    def __init__(self, adherence: Dict[str, Any], known_tokens: Iterable[str] = (),
                 token_px: Optional[Dict[str, Dict[float, str]]] = None):
        r = (adherence or {}).get("rules") or {}
        self.rules = r
        rc = r.get("no-raw-color") or {}
        self.raw_color_sev = _sev(rc.get("severity"), "error") if rc or not r else None
        try:
            self.raw_color_re = re.compile(rc.get("pattern") or _RAW_COLOR_DEFAULT)
        except re.error:
            self.raw_color_re = re.compile(_RAW_COLOR_DEFAULT)
        self.raw_color_allow_in = set(rc.get("allowIn") or ["tokens.css"])
        self.raw_color_msg = rc.get("message") or "Use a colour token."
        sp = r.get("spacing") or {}
        self.space_sev = _sev(sp.get("severity"), "warn") if sp else None
        self.space_props = tuple(sp.get("properties") or ())
        self.space_scale = {float(x) for x in sp.get("scalePx") or []}
        self.space_raw_ok = {float(x) for x in sp.get("allowRawPx") or [0]}
        self.space_msg = sp.get("message") or "Use a spacing token."
        rd = r.get("radius") or {}
        self.radius_sev = _sev(rd.get("severity"), "warn") if rd else None
        self.radius_scale = {float(x) for x in rd.get("scalePx") or []}
        ff = r.get("font-family") or {}
        self.font_sev = _sev(ff.get("severity"), "error") if ff else None
        self.font_allowed = {f.lower() for f in ff.get("allowedFamilies") or []}
        self.font_display = sorted(ff.get("allowedFamilies") or [])
        self.font_generic = {f.lower() for f in ff.get("genericFallbacks") or []} | GENERIC_FAMILIES
        fs = r.get("font-size") or {}
        self.fsize_sev = _sev(fs.get("severity"), "warn") if fs else None
        self.fsize_scale = {float(x) for x in fs.get("scalePx") or []}
        fw = r.get("font-weight") or {}
        self.fweight_sev = _sev(fw.get("severity"), "warn") if fw else None
        self.fweight_allowed = {int(x) for x in fw.get("allowed") or []}
        comp = r.get("components") or {}
        self.comp_sev = _sev(comp.get("severity"), "error")
        self.comp_props: Dict[str, Dict[str, set]] = {
            name: {p: {str(v) for v in vals} for p, vals in (props or {}).items() if isinstance(vals, list)}
            for name, props in (comp.get("props") or {}).items() if isinstance(props, dict)}
        self.comp_msg = comp.get("message") or "Unknown prop value; see manifest.json."
        fe = r.get("forbid-elements") or r.get("react/forbid-elements") or {}
        self.forbid_sev = _sev(fe.get("severity"), "error") if fe else None
        self.forbid: Dict[str, str] = {}
        self.forbid_scope = fe.get("scope") or "all"
        self.forbid_allow_in = [str(x) for x in fe.get("allowIn") or []]
        for e in fe.get("elements") or fe.get("forbid") or []:
            if isinstance(e, str):
                self.forbid[e] = f"<{e}> is not allowed by this design system."
            elif isinstance(e, dict) and e.get("element"):
                self.forbid[e["element"]] = e.get("message") or f"<{e['element']}> is not allowed."
        ri = r.get("no-restricted-imports") or {}
        self.imports_sev = _sev(ri.get("severity"), "error") if ri else None
        self.imports: List[Tuple[str, str]] = []  # (pattern, message)
        for p in ri.get("paths") or []:
            if isinstance(p, str):
                self.imports.append((p, f"'{p}' is restricted by this design system."))
            elif isinstance(p, dict) and p.get("name"):
                self.imports.append((p["name"], p.get("message") or f"'{p['name']}' is restricted."))
        for p in ri.get("patterns") or []:
            if isinstance(p, str):
                self.imports.append((p, f"imports matching '{p}' are restricted."))
            elif isinstance(p, dict):
                for g in p.get("group") or []:
                    self.imports.append((g, p.get("message") or f"imports matching '{g}' are restricted."))
        ic = r.get("icons") or {}
        self.icons_sev = _sev(ic.get("severity"), "warn") if ic.get("allowedLibraries") else None
        allowed_icons = {x.lower() for x in ic.get("allowedLibraries") or []}
        self.icon_block: List[Tuple[str, str]] = []
        if self.icons_sev:
            for lib, pats in _ICON_LIBS.items():
                if lib not in allowed_icons:
                    for pat in pats:
                        self.icon_block.append((pat, f"Icon library '{lib}' is not part of this system "
                                                     f"(allowed: {', '.join(sorted(allowed_icons))})."))
        bab = r.get("babel") or {}
        self.babel_enabled = bab.get("enabled", True) is not False
        tok = r.get("unknown-token") or {}
        self.unknown_sev = _sev(tok.get("severity"), "warn") if tok.get("enabled", True) is not False else None
        self.known_tokens = set(known_tokens)
        self.token_px = token_px or {}


def _sev(value: Any, default: str) -> str:
    return value if value in _SEVERITIES else default


# ── Helpers ───────────────────────────────────────────────────────────────

def _strip_css_comments(text: str) -> str:
    """Blank /* … */ comments, keeping offsets (and so line numbers) intact."""
    return re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)


def _strip_js_comments(text: str) -> str:
    """Blank // and /* */ comments outside strings, keeping offsets."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'`":
            q = c
            i += 1
            while i < n and text[i] != q:
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            # `https://` inside JSX text is not a comment start when preceded by ':'
            if i > 0 and text[i - 1] == ":":
                i += 2
                continue
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
            continue
        i += 1
    return "".join(out)


def _line_col(text: str, offset: int) -> Tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    col = offset - (text.rfind("\n", 0, offset) + 1) + 1
    return line, col


def _excerpt(text: str, offset: int) -> str:
    s = text.rfind("\n", 0, offset) + 1
    e = text.find("\n", offset)
    e = len(text) if e < 0 else e
    line = text[s:e].strip()
    return line if len(line) <= 160 else line[:157] + "…"


def _families(value: str) -> List[str]:
    out = []
    for part in _split_top(value, ","):
        p = part.strip().strip("'\"").strip()
        if p:
            out.append(p)
    return out


def _split_top(value: str, sep: str) -> List[str]:
    parts, depth, cur = [], 0, []
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _camel_to_kebab(name: str) -> str:
    return re.sub(r"[A-Z]", lambda m: "-" + m.group(0).lower(), name)


def _fmt_px(v: float) -> str:
    return str(int(v)) if v == int(v) else str(v)


class _Ctx:
    """Collects findings for one file."""

    def __init__(self, rel: str, text: str, cfg: _Cfg):
        self.rel, self.text, self.cfg = rel, text, cfg
        self.findings: List[Finding] = []
        self.base = Path(rel).name
        self.forbid_ok = any(fnmatch.fnmatch(rel, pat) for pat in cfg.forbid_allow_in)
        # allowIn entries match the basename ("tokens.css") or the relative path ("tokens/*.css").
        self.color_ok = self.base in cfg.raw_color_allow_in or any(
            fnmatch.fnmatch(rel, pat) for pat in cfg.raw_color_allow_in if "/" in pat or "*" in pat)

    def add(self, rule: str, severity: Optional[str], offset: int, message: str, **extra: Any) -> None:
        if not severity or len(self.findings) >= MAX_FINDINGS_PER_FILE:
            return
        line, col = _line_col(self.text, offset)
        f: Finding = {"rule": rule, "severity": severity, "file": self.rel, "line": line, "col": col,
                      "message": message, "excerpt": _excerpt(self.text, offset)}
        f.update({k: v for k, v in extra.items() if v is not None})
        self.findings.append(f)


# ── CSS value checks (shared by CSS, HTML style, JSX style objects) ───────

def _check_decl(ctx: _Ctx, prop: str, value: str, offset: int, *, numeric_px: bool = False) -> None:
    """One `prop: value` declaration. `offset` points at the value in ctx.text."""
    cfg = ctx.cfg
    prop = prop.strip().lower()
    v = value.strip()
    if not prop or not v:
        return
    is_custom = prop.startswith("--")
    # Colours
    if cfg.raw_color_sev and not ctx.color_ok:
        for m in cfg.raw_color_re.finditer(v):
            before = v[max(0, m.start() - 4):m.start()]
            if before.endswith("url(") or before.endswith("url('") or before.endswith('url("'):
                continue
            ctx.add("no-raw-color", cfg.raw_color_sev, offset + m.start(),
                    f"Raw colour `{_snip(v, m.start())}` in `{prop}`. {cfg.raw_color_msg}")
        if prop in _COLOR_PROPS or is_custom:
            for m in re.finditer(r"(?<![\w-])([a-zA-Z]+)(?![\w-]|\()", v):
                if m.group(1).lower() in _NAMED_COLORS:
                    ctx.add("no-raw-color", cfg.raw_color_sev, offset + m.start(),
                            f"Named colour `{m.group(1)}` in `{prop}`. {cfg.raw_color_msg}")
    # Unknown tokens
    if cfg.unknown_sev and cfg.known_tokens:
        for m in re.finditer(r"var\(\s*(--[A-Za-z0-9_-]+)", v):
            if m.group(1) not in cfg.known_tokens:
                ctx.add("unknown-token", cfg.unknown_sev, offset + m.start(),
                        f"`{m.group(1)}` is not defined by the design system or this project.")
    if is_custom:
        return
    # Spacing
    if cfg.space_sev and cfg.space_props and _prop_matches(prop, cfg.space_props):
        _check_px(ctx, prop, v, offset, cfg.space_scale, cfg.space_raw_ok, cfg.space_sev,
                  "spacing", numeric_px, token_map=cfg.token_px.get("space"), msg=cfg.space_msg)
    # Radius
    if cfg.radius_sev and prop.startswith("border") and "radius" in prop and cfg.radius_scale:
        _check_px(ctx, prop, v, offset, cfg.radius_scale, cfg.space_raw_ok or {0.0}, cfg.radius_sev,
                  "radius", numeric_px, token_map=cfg.token_px.get("radius"), msg="Use a radius token.")
    # Font size
    if cfg.fsize_sev and prop == "font-size" and cfg.fsize_scale:
        _check_px(ctx, prop, v, offset, cfg.fsize_scale, set(), cfg.fsize_sev, "font-size", numeric_px,
                  token_map=cfg.token_px.get("text"), msg="Use a type-scale token.", rem=True)
    # Font weight
    if cfg.fweight_sev and prop == "font-weight" and cfg.fweight_allowed:
        w = {"normal": 400, "bold": 700}.get(v.lower().strip("'\""), None)
        if w is None and re.fullmatch(r"['\"]?\d{3}['\"]?", v):
            w = int(v.strip("'\""))
        if w is not None and w not in cfg.fweight_allowed:
            ctx.add("font-weight", cfg.fweight_sev, offset,
                    f"font-weight {w} is not in the system's weights "
                    f"({', '.join(str(x) for x in sorted(cfg.fweight_allowed))}).")
    # Font family
    if cfg.font_sev and prop in ("font-family", "font"):
        fam_value = v
        if prop == "font":
            m = re.search(r"\d*\.?\d+(?:px|rem|em|pt|%)(?:\s*/\s*[\w.%-]+)?\s+(.+)$", v)
            if not m:
                return
            fam_value = m.group(1)
        _check_family(ctx, fam_value, offset)


def _snip(v: str, start: int) -> str:
    m = re.match(r"#[0-9a-fA-F]{3,8}|[a-z]+\([^)]*\)?", v[start:])
    return (m.group(0) if m else v[start:start + 24])[:40]


def _prop_matches(prop: str, props: Tuple[str, ...]) -> bool:
    for p in props:
        if prop == p or (p in ("margin", "padding", "inset", "gap") and prop.startswith(p + "-")):
            return True
    return False


def _check_px(ctx: _Ctx, prop: str, v: str, offset: int, scale: set, raw_ok: set, sev: str,
              rule: str, numeric_px: bool, *, token_map: Optional[Dict[float, str]], msg: str,
              rem: bool = False) -> None:
    if "var(" in v and not re.search(r"\d(px|rem)\b", v):
        return
    units = r"px|rem" if rem else r"px"
    pat = rf"(?<![\w.#-])(-?\d*\.?\d+)({units})\b"
    hits = list(re.finditer(pat, v))
    if numeric_px and not hits:
        hits = list(re.finditer(r"^(-?\d*\.?\d+)()$", v.strip()))
    for m in hits:
        num = abs(float(m.group(1))) * (16 if m.group(2) == "rem" else 1)
        if num in raw_ok:
            continue
        tok = token_map.get(num) if token_map else None
        if num in scale:
            ctx.add(f"{rule}-raw-px", "info", offset + m.start(),
                    f"Raw {_fmt_px(num)}px in `{prop}` is on the scale — "
                    f"prefer {('var(' + tok + ')') if tok else 'the token'}.", suggestion=f"var({tok})" if tok else None)
        else:
            near = min(scale, key=lambda s: abs(s - num)) if scale else None
            ntok = token_map.get(near) if (token_map and near is not None) else None
            hint = f" Nearest: {_fmt_px(near)}px" + (f" (var({ntok}))" if ntok else "") + "." if near is not None else ""
            ctx.add(f"{rule}-off-scale", sev, offset + m.start(),
                    f"{_fmt_px(num)}px in `{prop}` is off the {rule} scale. {msg}{hint}",
                    suggestion=f"var({ntok})" if ntok else None)


def _check_family(ctx: _Ctx, value: str, offset: int) -> None:
    cfg = ctx.cfg
    fams = _families(value)
    if not fams or not cfg.font_allowed:
        return
    first = fams[0]
    if first.startswith("var(") or first.lower() in cfg.font_generic or first.lower() in cfg.font_allowed:
        return
    ctx.add("font-family", cfg.font_sev, offset,
            f"Font `{first}` is not part of the system (allowed: "
            f"{', '.join(cfg.font_display)}). "
            f"Use var(--font-body) / var(--font-display).")


def _lint_css_block(ctx: _Ctx, css: str, base_offset: int) -> None:
    """Walk declarations in a stylesheet fragment; `base_offset` maps back into ctx.text."""
    css = _strip_css_comments(css)
    i, n, start = 0, len(css), 0
    while i <= n:
        ch = css[i] if i < n else ";"
        if ch in "{};":
            seg = css[start:i]
            if ch != "{" and ":" in seg:
                colon = seg.index(":")
                prop = seg[:colon]
                val = seg[colon + 1:]
                pstrip = prop.strip()
                if re.fullmatch(r"-{0,2}[A-Za-z][A-Za-z0-9_-]*", pstrip):
                    lead = len(val) - len(val.lstrip())
                    _check_decl(ctx, pstrip, val, base_offset + start + colon + 1 + lead)
            elif ch == "{":
                _check_at_rule(ctx, seg, base_offset + start)
            start = i + 1
        i += 1
    # @import url(...) of web fonts / restricted CSS
    for m in re.finditer(r"@import\s+(?:url\()?\s*['\"]?([^'\")\s;]+)", css):
        _check_url_import(ctx, m.group(1), base_offset + m.start(1))


def _check_at_rule(ctx: _Ctx, prelude: str, offset: int) -> None:
    return None  # selectors/at-rules carry no values we lint


def _check_style_attr(ctx: _Ctx, style: str, offset: int) -> None:
    pos = 0
    for part in style.split(";"):
        if ":" in part:
            colon = part.index(":")
            val = part[colon + 1:]
            lead = len(val) - len(val.lstrip())
            _check_decl(ctx, part[:colon].strip(), val, offset + pos + colon + 1 + lead)
        pos += len(part) + 1


# ── Imports ───────────────────────────────────────────────────────────────

_CDN_PKG_RES = (
    re.compile(r"(?:unpkg\.com|cdn\.jsdelivr\.net/npm|esm\.sh|cdn\.skypack\.dev)/(@[^/@]+/[^/@?#]+|[^/@?#]+)"),
    re.compile(r"cdnjs\.cloudflare\.com/ajax/libs/([^/?#]+)"),
    re.compile(r"cdn\.jsdelivr\.net/gh/[^/]+/([^/@?#]+)"),
)


def _pkg_from_url(url: str) -> Optional[str]:
    for rx in _CDN_PKG_RES:
        m = rx.search(url)
        if m:
            return m.group(1).lower()
    return None


def _import_blocked(spec: str, pairs: List[Tuple[str, str]]) -> Optional[str]:
    s = spec.lower()
    for pat, msg in pairs:
        p = pat.lower()
        if s == p or s.startswith(p + "/") or fnmatch.fnmatch(s, p):
            return msg
    return None


def _check_import_spec(ctx: _Ctx, spec: str, offset: int) -> None:
    cfg = ctx.cfg
    if cfg.imports_sev:
        msg = _import_blocked(spec, cfg.imports)
        if msg:
            ctx.add("no-restricted-imports", cfg.imports_sev, offset, f"`{spec}`: {msg}")
            return
    if cfg.icons_sev:
        msg = _import_blocked(spec, cfg.icon_block)
        if msg:
            ctx.add("icons", cfg.icons_sev, offset, msg)


def _check_url_import(ctx: _Ctx, url: str, offset: int) -> None:
    cfg = ctx.cfg
    if "fonts.googleapis.com" in url and cfg.font_sev and cfg.font_allowed:
        for m in re.finditer(r"family=([^&:;]+)", url):
            fam = re.sub(r"\+", " ", m.group(1))
            fam = re.sub(r"%20", " ", fam)
            if fam.lower() not in cfg.font_allowed:
                ctx.add("font-family", cfg.font_sev, offset,
                        f"Web font `{fam}` is loaded but is not part of the system.")
    pkg = _pkg_from_url(url)
    if pkg:
        _check_import_spec(ctx, pkg, offset)
    elif cfg.icons_sev or cfg.imports_sev:
        # Non-CDN URLs (e.g. kit.fontawesome.com): match by any path segment / host label.
        low = url.lower()
        for pat, msg in cfg.icon_block + cfg.imports:
            p = pat.lower().rstrip("*").strip("@/")
            if p and len(p) > 3 and "*" not in p and p in low:
                rule = "icons" if (pat, msg) in cfg.icon_block else "no-restricted-imports"
                ctx.add(rule, cfg.icons_sev if rule == "icons" else cfg.imports_sev, offset,
                        f"`{url[:80]}`: {msg}")
                return


# ── JS / JSX ──────────────────────────────────────────────────────────────

_STYLE_KEY_RE = re.compile(
    r"(?<![\w$.-])(['\"]?)(padding\w*|margin\w*|gap|rowGap|columnGap|inset\w*|top|right|bottom|left|"
    r"fontSize|fontFamily|fontWeight|borderRadius|border\w*Radius|color|background|backgroundColor|"
    r"border\w*Color|borderColor|fill|stroke|boxShadow|outlineColor)\1\s*:\s*"
    r"(\"[^\"\n]*\"|'[^'\n]*'|`[^`]*`|-?\d*\.?\d+)(?=\s*[,}\n])")


def _lint_js(ctx: _Ctx, code: str, base_offset: int, *, jsx: bool) -> None:
    cfg = ctx.cfg
    code = _strip_js_comments(code)
    # imports
    for m in re.finditer(r"""(?:\bimport\s+(?:[^'"]*?\sfrom\s+)?|\brequire\(\s*|\bimport\(\s*)(['"])([^'"\n]+)\1""", code):
        _check_import_spec(ctx, m.group(2), base_offset + m.start(2))
    # style objects (camelCase keys)
    seen: set = set()
    for m in _STYLE_KEY_RE.finditer(code):
        key, raw = m.group(2), m.group(3)
        prop = _camel_to_kebab(key)
        if raw[0] in "\"'`":
            val = raw[1:-1]
            numeric = False
        else:
            val = raw
            numeric = True
        if numeric and prop in ("font-weight",):
            numeric = False
        seen.add(base_offset + m.start(3))
        _check_decl(ctx, prop, val, base_offset + m.start(3) + (1 if raw[0] in "\"'`" else 0),
                    numeric_px=numeric and prop not in ("font-weight",))
    # colours in any other string literal (e.g. fill="#fff", const c = "oklch(...)")
    if cfg.raw_color_sev and not ctx.color_ok:
        for m in re.finditer(r"(\"|'|`)((?:\\.|(?!\1).)*)\1", code, flags=re.S):
            s_off = base_offset + m.start(2)
            if s_off - 1 in seen or s_off in seen:
                continue
            body = m.group(2)
            if len(body) > 4000:
                continue
            # Embedded CSS (template literals / style strings with declarations).
            if re.search(r"[a-z-]+\s*:\s*[^;]+;", body) and ("{" in body or ";" in body):
                _lint_css_block(ctx, body, s_off)
                continue
            for cm in cfg.raw_color_re.finditer(body):
                pre = code[max(0, m.start() - 24):m.start()]
                if re.search(r"(href|id|to|selector|querySelector\w*\()\s*[=({]?\s*$", pre):
                    continue
                if body.startswith("#") and cm.start() == 0 and not re.fullmatch(r"#[0-9a-fA-F]{3,8}", body):
                    continue
                ctx.add("no-raw-color", cfg.raw_color_sev, s_off + cm.start(),
                        f"Raw colour `{_snip(body, cm.start())}` in a string. {cfg.raw_color_msg}")
    if jsx:
        _lint_jsx_elements(ctx, code, base_offset)
    if cfg.babel_enabled:
        for m in re.finditer(r"^(const|let|var)\s+styles\s*=", code, flags=re.M):
            ctx.add("babel-generic-styles-const", "warn", base_offset + m.start(),
                    "Top-level `const styles` collides across text/babel scripts (they share one "
                    "global scope). Name it per component (`buttonStyles`) or wrap the file in an IIFE.")


def _parse_jsx_attrs(code: str, i: int) -> Tuple[List[Tuple[str, Optional[str], int]], int]:
    """Parse attributes from code[i:] up to the tag end. Returns ([(name, literal|None, offset)], end)."""
    attrs: List[Tuple[str, Optional[str], int]] = []
    n = len(code)
    while i < n:
        while i < n and code[i].isspace():
            i += 1
        if i >= n:
            break
        if code[i] == ">" or code.startswith("/>", i):
            return attrs, i
        if code[i] == "{":  # spread {...props}
            i = _skip_braces(code, i)
            continue
        m = re.match(r"[A-Za-z_$][\w$:.-]*", code[i:])
        if not m:
            return attrs, i
        name = m.group(0)
        i += len(name)
        while i < n and code[i].isspace():
            i += 1
        if i < n and code[i] == "=":
            i += 1
            while i < n and code[i].isspace():
                i += 1
            if i < n and code[i] in "\"'":
                q = code[i]
                j = code.find(q, i + 1)
                j = n if j < 0 else j
                attrs.append((name, code[i + 1:j], i + 1))
                i = j + 1
            elif i < n and code[i] == "{":
                j = _skip_braces(code, i)
                inner = code[i + 1:j - 1].strip()
                lm = re.fullmatch(r"(['\"`])([^'\"`]*)\1", inner)
                attrs.append((name, lm.group(2) if lm else None, i + 1))
                i = j
            else:
                attrs.append((name, None, i))
        else:
            attrs.append((name, "true", i))
    return attrs, i


def _skip_braces(code: str, i: int) -> int:
    depth, n = 0, len(code)
    while i < n:
        c = code[i]
        if c in "\"'`":
            q = c
            i += 1
            while i < n and code[i] != q:
                i += 2 if code[i] == "\\" else 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _lint_jsx_elements(ctx: _Ctx, code: str, base_offset: int) -> None:
    cfg = ctx.cfg
    for m in re.finditer(r"<([A-Za-z][\w.]*)(?=[\s/>])", code):
        tag = m.group(1)
        prev = code[max(0, m.start() - 1):m.start()]
        if prev and (prev.isalnum() or prev == "_"):
            continue  # `a<b` comparisons
        short = tag.split(".")[-1]
        if cfg.forbid_sev and not ctx.forbid_ok and tag in cfg.forbid and cfg.forbid_scope in ("all", "jsx"):
            ctx.add("forbid-elements", cfg.forbid_sev, base_offset + m.start(), cfg.forbid[tag])
        enums = cfg.comp_props.get(short) if short[:1].isupper() else None
        if not enums:
            continue
        attrs, _ = _parse_jsx_attrs(code, m.end())
        for name, lit, off in attrs:
            allowed = enums.get(name)
            if allowed is None or lit is None:
                continue
            if lit not in allowed:
                ctx.add("component-props", cfg.comp_sev, base_offset + off,
                        f"<{short} {name}=\"{lit}\">: not one of {', '.join(sorted(allowed))}. {cfg.comp_msg}")


# ── HTML ──────────────────────────────────────────────────────────────────

def _lint_html(ctx: _Ctx) -> None:
    text = ctx.text
    cfg = ctx.cfg
    masked = re.sub(r"<!--.*?-->", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    has_babel = bool(re.search(r"<script[^>]*type=['\"]text/babel['\"]", masked, flags=re.I))
    blocks: List[Tuple[int, int]] = []
    for m in re.finditer(r"<style\b[^>]*>(.*?)</style>", masked, flags=re.S | re.I):
        _lint_css_block(ctx, m.group(1), m.start(1))
        blocks.append((m.start(), m.end()))
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", masked, flags=re.S | re.I):
        attrs = m.group(1)
        blocks.append((m.start(), m.end()))
        src = re.search(r"\bsrc\s*=\s*['\"]([^'\"]+)", attrs)
        if src:
            _check_url_import(ctx, src.group(1), m.start(1) + src.start(1))
        typ = (re.search(r"\btype\s*=\s*['\"]([^'\"]+)", attrs) or [None, ""])[1].lower()
        if typ == "module" and has_babel and cfg.babel_enabled:
            ctx.add("babel-type-module", "warn", m.start(),
                    "`type=\"module\"` beside text/babel scripts: modules don't share globals, so the "
                    "window-export pattern breaks. Use plain text/babel scripts.")
        body = m.group(2)
        if not body.strip() or typ in ("application/json", "application/ld+json", "text/template", "importmap"):
            continue
        _lint_js(ctx, body, m.start(2), jsx=(typ == "text/babel" or "jsx" in typ))
    # markup outside <style>/<script>
    def in_block(pos: int) -> bool:
        return any(a <= pos < b for a, b in blocks)
    for m in re.finditer(r"<([a-zA-Z][\w:-]*)(\s[^<>]*?)?/?>", masked):
        if in_block(m.start()):
            continue
        tag = m.group(1)
        attrs = m.group(2) or ""
        a_off = m.start(2) if m.group(2) else m.end()
        if cfg.forbid_sev and not ctx.forbid_ok and tag in cfg.forbid and cfg.forbid_scope in ("all", "html"):
            ctx.add("forbid-elements", cfg.forbid_sev, m.start(), cfg.forbid[tag])
        for am in re.finditer(r"""\b([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""", attrs):
            name = am.group(1).lower()
            val = am.group(2) if am.group(2) is not None else am.group(3)
            voff = a_off + (am.start(2) if am.group(2) is not None else am.start(3))
            if name == "style":
                _check_style_attr(ctx, val, voff)
            elif name in _COLOR_ATTRS:
                _check_decl(ctx, name, val, voff)
            elif name == "href" and tag.lower() == "link":
                _check_url_import(ctx, val, voff)


# ── .pen ──────────────────────────────────────────────────────────────────

def lint_pen(rel: str, data: Any, cfg: _Cfg) -> List[Finding]:
    findings: List[Finding] = []
    known = set(cfg.known_tokens)
    if isinstance(data, dict) and isinstance(data.get("variables"), dict):
        known |= set(data["variables"].keys())

    def add(rule: str, sev: Optional[str], pointer: str, node_id: Optional[str], msg: str) -> None:
        if sev and len(findings) < MAX_FINDINGS_PER_FILE:
            findings.append({"rule": rule, "severity": sev, "file": rel, "line": None, "col": None,
                             "pointer": pointer, "node_id": node_id, "message": msg})

    def check_color(val: Any, ptr: str, nid: Optional[str], effect: bool = False) -> None:
        if isinstance(val, str):
            if val.startswith("$"):
                name = val[1:]
                if cfg.unknown_sev and known and name not in known:
                    add("unknown-token", cfg.unknown_sev, ptr, nid, f"Variable `{val}` is not defined.")
            elif cfg.raw_color_sev and cfg.raw_color_re.search(val):
                add("no-raw-color", "info" if effect else cfg.raw_color_sev, ptr, nid,
                    f"Raw colour `{val}` — bind it to a system variable (`$--primary`, …).")
        elif isinstance(val, dict):
            for k in ("color", "fill", "value"):
                if k in val:
                    check_color(val[k], f"{ptr}.{k}", nid, effect)
            for k in ("colors", "stops"):
                if isinstance(val.get(k), list):
                    for i, stop in enumerate(val[k]):
                        check_color(stop, f"{ptr}.{k}[{i}]", nid, effect)
        elif isinstance(val, list):
            for i, v in enumerate(val):
                check_color(v, f"{ptr}[{i}]", nid, effect)

    def check_num(val: Any, ptr: str, nid: Optional[str], prop: str) -> None:
        vals = val if isinstance(val, list) else [val]
        for i, v in enumerate(vals):
            p = f"{ptr}[{i}]" if isinstance(val, list) else ptr
            if isinstance(v, str) and v.startswith("$"):
                if cfg.unknown_sev and known and v[1:] not in known:
                    add("unknown-token", cfg.unknown_sev, p, nid, f"Variable `{v}` is not defined.")
                continue
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            num = abs(float(v))
            if prop in ("padding", "gap") and cfg.space_sev and cfg.space_scale:
                if num not in cfg.space_scale and num not in cfg.space_raw_ok:
                    add("spacing-off-scale", cfg.space_sev, p, nid, f"{_fmt_px(num)} is off the spacing scale.")
            elif prop == "cornerRadius" and cfg.radius_sev and cfg.radius_scale:
                if num not in cfg.radius_scale and num != 0:
                    add("radius-off-scale", cfg.radius_sev, p, nid, f"radius {_fmt_px(num)} is off the radius scale.")
            elif prop == "fontSize" and cfg.fsize_sev and cfg.fsize_scale:
                if num not in cfg.fsize_scale:
                    add("font-size-off-scale", cfg.fsize_sev, p, nid, f"font size {_fmt_px(num)} is off the type scale.")

    def walk(node: Any, ptr: str) -> None:
        if isinstance(node, list):
            for i, c in enumerate(node):
                walk(c, f"{ptr}[{i}]")
            return
        if not isinstance(node, dict):
            return
        nid = node.get("id") if isinstance(node.get("id"), str) else None
        for key in ("fill", "stroke", "fills", "strokes"):
            if key in node:
                v = node[key]
                if key.startswith("stroke") and isinstance(v, dict) and "fill" in v:
                    check_color(v["fill"], f"{ptr}.{key}.fill", nid)
                else:
                    check_color(v, f"{ptr}.{key}", nid)
        for key in ("effect", "effects"):
            if key in node:
                check_color(node[key], f"{ptr}.{key}", nid, effect=True)
        ff = node.get("fontFamily")
        if isinstance(ff, str) and not ff.startswith("$") and cfg.font_sev and cfg.font_allowed:
            if ff.lower() not in cfg.font_allowed and ff.lower() not in cfg.font_generic:
                add("font-family", cfg.font_sev, f"{ptr}.fontFamily", nid, f"Font `{ff}` is not part of the system.")
        elif isinstance(ff, str) and ff.startswith("$") and cfg.unknown_sev and known and ff[1:] not in known:
            add("unknown-token", cfg.unknown_sev, f"{ptr}.fontFamily", nid, f"Variable `{ff}` is not defined.")
        for key in ("padding", "gap", "cornerRadius", "fontSize"):
            if key in node:
                check_num(node[key], f"{ptr}.{key}", nid, key)
        fw = node.get("fontWeight")
        if cfg.fweight_sev and cfg.fweight_allowed and isinstance(fw, (int, str)) and not str(fw).startswith("$"):
            try:
                w = int(fw)
                if w not in cfg.fweight_allowed:
                    add("font-weight", cfg.fweight_sev, f"{ptr}.fontWeight", nid, f"font-weight {w} is not in the system's weights.")
            except ValueError:
                pass
        for key, child in node.items():
            if key in ("children", "slots", "descendants") or isinstance(child, (dict, list)) and key not in (
                    "fill", "stroke", "fills", "strokes", "effect", "effects", "variables", "themes", "padding"):
                walk(child, f"{ptr}.{key}")

    walk(data.get("children") if isinstance(data, dict) and "children" in data else data, "children"
         if isinstance(data, dict) else "")
    return findings


# ── Entry points ──────────────────────────────────────────────────────────

def token_context(system_dir: Optional[Path]) -> Tuple[set, Dict[str, Dict[float, str]]]:
    """Token names the system defines, and px → token maps for suggestions."""
    names: set = set()
    px_maps: Dict[str, Dict[float, str]] = {"space": {}, "radius": {}, "text": {}}
    if not system_dir:
        return names, px_maps
    css = ""
    for f in [system_dir / "tokens.css", system_dir / "components.css", system_dir / "specimen.css",
              system_dir / "styles.css", *sorted((system_dir / "tokens").glob("*.css"))]:
        try:
            css += f.read_text(encoding="utf-8", errors="replace") + "\n"
        except OSError:
            pass
    props = parse_custom_properties(css)
    names |= set(props)
    for k, v in props.items():
        px = _px_of(v)
        if px is None:
            continue
        if k.startswith("--space") and px not in px_maps["space"]:
            px_maps["space"][px] = k
        elif k.startswith("--radius") and px not in px_maps["radius"]:
            px_maps["radius"][px] = k
        elif k.startswith("--text-") and px not in px_maps["text"]:
            px_maps["text"][px] = k
    try:
        pen = json.loads((system_dir / "system.lib.pen").read_text(encoding="utf-8"))
        names |= set((pen.get("variables") or {}).keys())
    except (OSError, ValueError, AttributeError):
        pass
    return names, px_maps


def lint_text(rel: str, text: str, cfg: _Cfg) -> List[Finding]:
    ctx = _Ctx(rel, text, cfg)
    ext = Path(rel).suffix.lower()
    if ext == ".css":
        _lint_css_block(ctx, text, 0)
    elif ext in (".html", ".htm"):
        _lint_html(ctx)
    elif ext in (".jsx", ".js", ".tsx", ".ts", ".mjs"):
        _lint_js(ctx, text, 0, jsx=ext in (".jsx", ".tsx") or "React" in text or "</" in text)
    return ctx.findings


def iter_files(root: Path, exclude_dirs: Iterable[str] = EXCLUDE_DIRS) -> List[Path]:
    excl = set(exclude_dirs)
    out: List[Path] = []
    stack = [root]
    while stack and len(out) < MAX_FILES:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.is_symlink():
                continue
            if p.is_dir():
                if p.name not in excl and not p.name.startswith("."):
                    stack.append(p)
            elif p.suffix.lower() in TEXT_EXTS | PEN_EXTS:
                out.append(p)
    return out


def lint_dir(root: Path, adherence: Dict[str, Any], *, system_dir: Optional[Path] = None,
             exclude_dirs: Iterable[str] = EXCLUDE_DIRS, files: Optional[List[str]] = None) -> Dict[str, Any]:
    """Lint every design source under `root` against `adherence`.

    Returns {"findings": [...], "counts": {"error": n, "warn": n, "info": n}, "files": n}.
    `files` limits the run to those root-relative paths (e.g. the files a turn changed).
    """
    known, px_maps = token_context(system_dir)
    paths = [root / f for f in files] if files else iter_files(root, exclude_dirs)
    # Tokens the project defines itself are known too (a page-local `--hero-h`).
    texts: List[Tuple[str, Path, str]] = []
    for p in paths:
        try:
            if not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
                continue
            rel = p.relative_to(root).as_posix()
            texts.append((rel, p, p.read_text(encoding="utf-8", errors="replace")))
        except (OSError, ValueError):
            continue
    for rel, p, t in texts:
        if p.suffix.lower() != ".pen":
            known |= set(parse_custom_properties(t))
    cfg = _Cfg(adherence, known, px_maps)
    findings: List[Finding] = []
    for rel, p, t in texts:
        if len(findings) >= MAX_FINDINGS:
            break
        if p.suffix.lower() == ".pen":
            try:
                findings.extend(lint_pen(rel, json.loads(t), cfg))
            except ValueError as exc:
                findings.append({"rule": "pen-parse", "severity": "warn", "file": rel, "line": None,
                                 "col": None, "message": f"not valid .pen JSON: {exc}"})
        else:
            findings.extend(lint_text(rel, t, cfg))
    findings = findings[:MAX_FINDINGS]
    counts = {s: 0 for s in _SEVERITIES}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return {"findings": findings, "counts": counts, "files": len(texts)}


def format_findings(findings: List[Finding], limit: int = 60) -> str:
    """Compact text for prompts (`{{lint_findings}}` in verifier.md)."""
    if not findings:
        return "(no design-system findings)"
    order = {"error": 0, "warn": 1, "info": 2}
    rows = sorted(findings, key=lambda f: (order.get(f["severity"], 3), f["file"], f.get("line") or 0))
    lines = []
    for f in rows[:limit]:
        loc = f"{f['file']}:{f['line']}:{f['col']}" if f.get("line") else f"{f['file']} {f.get('pointer', '')}"
        lines.append(f"- [{f['severity']}] {f['rule']} {loc} — {f['message']}")
    if len(rows) > limit:
        lines.append(f"- … {len(rows) - limit} more")
    return "\n".join(lines)
