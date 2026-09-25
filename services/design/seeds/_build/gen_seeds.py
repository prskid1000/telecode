"""Generate services/design/seeds/systems/<slug>/ from seed_specs.SYSTEMS."""
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from seed_specs import SYSTEMS, FONTS, SPACING, OFL, GF  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "systems"

REACT = ('<script src="https://unpkg.com/react@18.3.1/umd/react.production.min.js" integrity="sha384-DGyLxAyjq0f9SPpVevD6IgztCFlnMF6oW/XQGmfe+IsZ8TqEiDrcHkMLKI6fiB/Z" crossorigin="anonymous"></script>\n'
         '<script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js" integrity="sha384-gTGxhz21lVGYNMcdJOyq01Edg0jhn/c22nsx0kyqP0TxaV5WVdsSH1fSDUf5YJj1" crossorigin="anonymous"></script>\n'
         '<script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js" integrity="sha384-m08KidiNqLdpJqLq95G/LEi8Qvjl/xUYll3QILypMoQ65QorJ9Lvtp2RXYGBFj1y" crossorigin="anonymous"></script>')

COLOR_TOKENS = ["background", "foreground", "card", "card-foreground", "popover", "popover-foreground",
                "primary", "primary-foreground", "secondary", "secondary-foreground", "muted", "muted-foreground",
                "accent", "accent-foreground", "destructive", "destructive-foreground", "success", "success-foreground",
                "warning", "warning-foreground", "border", "input", "ring",
                "chart-1", "chart-2", "chart-3", "chart-4", "chart-5", "overlay"]

# Per-system component-level choices (component tokens + JSX defaults).
EXTRA = {
    "neutral":   dict(border_w="1px", ring_w="3px", radius_input="var(--radius-md)", radius_badge="var(--radius-md)", button_shadow="none", press="0px", card_shadow="var(--shadow-xs)", badge_font="var(--font-ui)", badge_case="none", th_font="var(--font-ui)", th_case="none", tabs="pill", striped="false", density="comfortable"),
    "editorial": dict(border_w="1px", ring_w="2px", radius_input="var(--radius-sm)", radius_badge="var(--radius-sm)", button_shadow="none", press="0px", card_shadow="none", badge_font="var(--font-ui)", badge_case="uppercase", th_font="var(--font-ui)", th_case="uppercase", tabs="underline", striped="false", density="comfortable"),
    "midnight":  dict(border_w="1px", ring_w="3px", radius_input="var(--radius-md)", radius_badge="var(--radius-full)", button_shadow="none", press="0px", card_shadow="none", badge_font="var(--font-ui)", badge_case="none", th_font="var(--font-ui)", th_case="none", tabs="pill", striped="false", density="compact"),
    "playful":   dict(border_w="2px", ring_w="4px", radius_input="var(--radius-md)", radius_badge="var(--radius-full)", button_shadow="var(--shadow-sm)", press="2px", card_shadow="var(--shadow-md)", badge_font="var(--font-ui)", badge_case="none", th_font="var(--font-ui)", th_case="none", tabs="pill", striped="true", density="comfortable"),
    "technical": dict(border_w="1px", ring_w="2px", radius_input="var(--radius-md)", radius_badge="var(--radius-sm)", button_shadow="none", press="0px", card_shadow="none", badge_font="var(--font-mono)", badge_case="uppercase", th_font="var(--font-mono)", th_case="uppercase", tabs="underline", striped="false", density="compact"),
}
OVERLAY = {"light": "oklch(0.15 0 0 / 45%)", "dark": "oklch(0 0 0 / 65%)"}


# ── colour maths: oklch → sRGB hex (for .pen, which takes hex only) ────────
def _parse_oklch(s):
    m = re.match(r"oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*(?:/\s*([\d.]+)(%?))?\s*\)", s)
    if not m:
        raise ValueError(s)
    L, C, H = float(m[1]), float(m[2]), float(m[3])
    a = 1.0
    if m[4] is not None:
        a = float(m[4]) / (100 if m[5] else 1)
    return L, C, H, a


def oklch_hex(s):
    L, C, H, a = _parse_oklch(s)
    h = math.radians(H)
    A, B = C * math.cos(h), C * math.sin(h)
    l_ = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3
    m_ = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3
    s_ = (L - 0.0894841775 * A - 1.2914855480 * B) ** 3
    r = 4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_
    g = -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_
    b = -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_

    def enc(x):
        x = max(0.0, min(1.0, x))
        x = 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055
        return round(max(0, min(1, x)) * 255)
    out = "#%02X%02X%02X" % (enc(r), enc(g), enc(b))
    if a < 1:
        out += "%02X" % round(a * 255)
    return out


def colors(sys_, theme):
    c = dict(sys_[theme])
    c["overlay"] = OVERLAY[theme]
    return c


def stack(fam):
    return f"'{fam}', {FONTS[fam]['fallback']}"


def families(s):
    seen = []
    for k in ("display", "body", "ui", "mono"):
        f = s["fonts"][k]
        if f not in seen:
            seen.append(f)
    return seen


def gf_url(s):
    return "https://fonts.googleapis.com/css2?" + "&".join("family=" + FONTS[f]["q"] for f in families(s)) + "&display=swap"


def radius_ref(s, key):
    return "9999px" if s[key] == "full" else f"var(--radius-{s[key]})"


# ── tokens.css / tokens.json ────────────────────────────────────────────────
def static_vars(s):
    e = EXTRA[s["slug"]]
    v = {}
    for k in ("display", "body", "ui", "mono"):
        v[f"font-{k}"] = stack(s["fonts"][k])
    v.update({"font-weight-regular": "400", "font-weight-medium": "500", "font-weight-semibold": "600", "font-weight-bold": "700"})
    for k, px in s["type"].items():
        v[f"text-{k}"] = f"{px}px"
    for k, x in s["leading"].items():
        v[f"leading-{k}"] = str(x)
    for k, x in s["tracking"].items():
        v[f"tracking-{k}"] = x
    for k, px in SPACING:
        v[f"space-{k}"] = f"{px}px"
    for k, px in s["radius"].items():
        v[f"radius-{k}"] = f"{px}px"
    v["radius-full"] = "9999px"
    v["radius-control"] = radius_ref(s, "radius_control")
    v["radius-surface"] = radius_ref(s, "radius_surface")
    v["radius-input"] = e["radius_input"]
    v["radius-badge"] = e["radius_badge"]
    for k, px in s["control_h"].items():
        v[f"control-h-{k}"] = f"{px}px"
    v["control-border-width"] = e["border_w"]
    v["ring-width"] = e["ring_w"]
    v["nav-h"] = f"{s['nav_h']}px"
    for k, px in s["row_h"].items():
        v[f"row-h-{k}"] = f"{px}px"
    b = s["button"]
    v.update({"button-font": f"var(--font-{b['font']})", "button-weight": str(b["weight"]), "button-case": b["case"],
              "button-tracking": b["tracking"], "button-shadow": e["button_shadow"], "button-press": e["press"],
              "card-shadow": e["card_shadow"], "badge-font": e["badge_font"], "badge-case": e["badge_case"],
              "table-head-font": e["th_font"], "table-head-case": e["th_case"]})
    m = s["motion"]
    v.update({"duration-fast": m["fast"], "duration-base": m["base"], "duration-slow": m["slow"],
              "ease-standard": m["standard"], "ease-out": m["out"], "ease-emphasis": m["emphasis"]})
    return v


def theme_block(s, theme):
    lines = [f"  color-scheme: {theme};"]
    for t in COLOR_TOKENS:
        lines.append(f"  --{t}: {colors(s, theme)[t]};")
    for k, val in s[f"shadow_{theme}"].items():
        lines.append(f"  --shadow-{k}: {val};")
    return "\n".join(lines)


def tokens_css(s):
    d = s["default_theme"]
    o = "light" if d == "dark" else "dark"
    dsel = f':root, [data-theme="{d}"]' + (", .dark" if d == "dark" else "")
    osel = f'[data-theme="{o}"]' + (", .dark" if o == "dark" else "")
    sv = "\n".join(f"  --{k}: {v};" for k, v in static_vars(s).items())
    return f"""/* TeleDesign seed system: {s['name']} ({s['slug']}) — tokens.css
 * The ONLY file in this system that may contain raw colour values.
 * Default theme: {d}. Switch with data-theme="light|dark" on <html> or any subtree.
 * Fonts are not @import-ed here; link them in the page <head>:
 *   {gf_url(s)}
 * {'Colour values: shadcn/ui neutral theme (MIT) + TeleDesign additions (success, warning, destructive-foreground, overlay).' if s['slug']=='neutral' else 'Original TeleDesign palette.'}
 */

{dsel} {{
{theme_block(s, d)}
}}

{osel} {{
{theme_block(s, o)}
}}

:root {{
{sv}
}}

[data-theme] {{
  background-color: var(--background);
  color: var(--foreground);
}}

@media (prefers-reduced-motion: reduce) {{
  :root {{
    --duration-fast: 0ms;
    --duration-base: 0ms;
    --duration-slow: 0ms;
    --button-press: 0px;
  }}
}}
"""


def tokens_json(s):
    sv = static_vars(s)
    col = {th: {t: {"value": colors(s, th)[t], "hex": oklch_hex(colors(s, th)[t])} for t in COLOR_TOKENS} for th in ("light", "dark")}
    pick = lambda pre: {k[len(pre):]: v for k, v in sv.items() if k.startswith(pre)}
    return {
        "system": s["slug"], "name": s["name"], "format": "teledesign-tokens/v1",
        "themes": ["light", "dark"], "defaultTheme": s["default_theme"],
        "color": col,
        "typography": {
            "fontFamily": {k: {"family": s["fonts"][k], "stack": sv[f"font-{k}"]} for k in ("display", "body", "ui", "mono")},
            "fontWeight": pick("font-weight-"), "fontSize": pick("text-"), "lineHeight": pick("leading-"), "letterSpacing": pick("tracking-"),
        },
        "spacing": pick("space-"),
        "radius": {k: v for k, v in pick("radius-").items()},
        "shadow": {th: s[f"shadow_{th}"] for th in ("light", "dark")},
        "motion": {"duration": pick("duration-"), "easing": pick("ease-")},
        "component": {k: sv[k] for k in sv if k.split("-")[0] in ("control", "ring", "nav", "row", "button", "card", "badge", "table")},
    }


# ── components ──────────────────────────────────────────────────────────────
COMPONENTS_CSS = """/* TeleDesign seed components — styled from tokens.css only (no raw colours / px outside tokens). */
.td-btn { display: inline-flex; align-items: center; justify-content: center; gap: var(--space-2);
  height: var(--control-h-md); padding: 0 var(--space-4); border: var(--control-border-width) solid transparent;
  border-radius: var(--radius-control); font-family: var(--button-font); font-size: var(--text-sm);
  font-weight: var(--button-weight); letter-spacing: var(--button-tracking); text-transform: var(--button-case);
  line-height: 1; white-space: nowrap; cursor: pointer; box-shadow: var(--button-shadow);
  transition: background-color var(--duration-fast) var(--ease-standard), box-shadow var(--duration-fast) var(--ease-standard), transform var(--duration-fast) var(--ease-emphasis); }
.td-btn:focus-visible { outline: none; box-shadow: 0 0 0 var(--ring-width) color-mix(in oklch, var(--ring) 50%, transparent); }
.td-btn:active { transform: translateY(var(--button-press)); box-shadow: none; }
.td-btn:disabled { opacity: 0.5; pointer-events: none; }
.td-btn svg { width: 1.15em; height: 1.15em; flex: none; }
.td-btn--sm { height: var(--control-h-sm); padding: 0 var(--space-3); font-size: var(--text-xs); }
.td-btn--lg { height: var(--control-h-lg); padding: 0 var(--space-6); font-size: var(--text-base); }
.td-btn--icon { width: var(--control-h-md); padding: 0; }
.td-btn--primary { background: var(--primary); color: var(--primary-foreground); }
.td-btn--primary:hover { background: color-mix(in oklch, var(--primary) 88%, var(--background)); }
.td-btn--secondary { background: var(--secondary); color: var(--secondary-foreground); }
.td-btn--secondary:hover { background: color-mix(in oklch, var(--secondary) 80%, var(--foreground) 6%); }
.td-btn--outline { background: var(--background); color: var(--foreground); border-color: var(--border); }
.td-btn--outline:hover, .td-btn--ghost:hover { background: var(--accent); color: var(--accent-foreground); }
.td-btn--ghost { background: transparent; color: var(--foreground); box-shadow: none; }
.td-btn--destructive { background: var(--destructive); color: var(--destructive-foreground); }
.td-btn--destructive:hover { background: color-mix(in oklch, var(--destructive) 88%, var(--background)); }
.td-btn--link { background: none; color: var(--primary); height: auto; padding: 0; box-shadow: none; text-underline-offset: 0.25em; }
.td-btn--link:hover { text-decoration: underline; }

.td-field { display: grid; gap: var(--space-1-5); font-family: var(--font-ui); }
.td-label { font-size: var(--text-sm); font-weight: var(--font-weight-medium); color: var(--foreground); }
.td-input { height: var(--control-h-md); width: 100%; box-sizing: border-box; padding: 0 var(--space-3);
  border: var(--control-border-width) solid var(--input); border-radius: var(--radius-input);
  background: var(--background); color: var(--foreground); font: inherit; font-size: var(--text-sm);
  transition: border-color var(--duration-fast) var(--ease-standard), box-shadow var(--duration-fast) var(--ease-standard); }
.td-input::placeholder { color: var(--muted-foreground); }
.td-input:focus-visible { outline: none; border-color: var(--ring); box-shadow: 0 0 0 var(--ring-width) color-mix(in oklch, var(--ring) 35%, transparent); }
.td-input[aria-invalid="true"] { border-color: var(--destructive); }
.td-input[aria-invalid="true"]:focus-visible { box-shadow: 0 0 0 var(--ring-width) color-mix(in oklch, var(--destructive) 30%, transparent); }
.td-input:disabled { opacity: 0.5; cursor: not-allowed; background: var(--muted); }
.td-input--sm { height: var(--control-h-sm); font-size: var(--text-xs); }
.td-input--lg { height: var(--control-h-lg); font-size: var(--text-base); padding: 0 var(--space-4); }
.td-hint { font-size: var(--text-xs); color: var(--muted-foreground); }
.td-hint--error { color: var(--destructive); }

.td-card { display: flex; flex-direction: column; background: var(--card); color: var(--card-foreground);
  border: 1px solid var(--border); border-radius: var(--radius-surface); box-shadow: var(--card-shadow); overflow: hidden; }
.td-card--raised { box-shadow: var(--shadow-md); }
.td-card--pad-sm > * { padding-inline: var(--space-4); } .td-card--pad-sm > :first-child { padding-top: var(--space-4); } .td-card--pad-sm > :last-child { padding-bottom: var(--space-4); }
.td-card--pad-md > * { padding-inline: var(--space-6); } .td-card--pad-md > :first-child { padding-top: var(--space-6); } .td-card--pad-md > :last-child { padding-bottom: var(--space-6); }
.td-card--pad-lg > * { padding-inline: var(--space-8); } .td-card--pad-lg > :first-child { padding-top: var(--space-8); } .td-card--pad-lg > :last-child { padding-bottom: var(--space-8); }
.td-card__header { display: grid; gap: var(--space-1-5); padding-bottom: var(--space-4); }
.td-card__title { margin: 0; font-family: var(--font-display); font-size: var(--text-lg); font-weight: var(--font-weight-semibold); line-height: var(--leading-snug); letter-spacing: var(--tracking-tight); }
.td-card__description { margin: 0; font-size: var(--text-sm); color: var(--muted-foreground); line-height: var(--leading-normal); }
.td-card__content { font-size: var(--text-sm); line-height: var(--leading-normal); }
.td-card__footer { display: flex; gap: var(--space-2); align-items: center; padding-top: var(--space-4); }

.td-badge { display: inline-flex; align-items: center; gap: var(--space-1); padding: var(--space-0-5) var(--space-2);
  border: 1px solid transparent; border-radius: var(--radius-badge); font-family: var(--badge-font); font-size: var(--text-xs);
  font-weight: var(--font-weight-medium); text-transform: var(--badge-case); letter-spacing: var(--tracking-normal); line-height: var(--leading-snug); white-space: nowrap; }
.td-badge--default { background: var(--primary); color: var(--primary-foreground); }
.td-badge--secondary { background: var(--secondary); color: var(--secondary-foreground); }
.td-badge--outline { border-color: var(--border); color: var(--foreground); }
.td-badge--success { background: var(--success); color: var(--success-foreground); }
.td-badge--warning { background: var(--warning); color: var(--warning-foreground); }
.td-badge--destructive { background: var(--destructive); color: var(--destructive-foreground); }

.td-tabs { display: grid; gap: var(--space-4); font-family: var(--font-ui); }
.td-tabs__list { display: inline-flex; align-items: center; gap: var(--space-1); justify-self: start; }
.td-tabs__tab { border: 0; background: none; cursor: pointer; font: inherit; font-size: var(--text-sm);
  font-weight: var(--font-weight-medium); color: var(--muted-foreground); height: calc(var(--control-h-md) - var(--space-1-5));
  padding: 0 var(--space-3); transition: color var(--duration-fast) var(--ease-standard), background-color var(--duration-fast) var(--ease-standard); }
.td-tabs__tab:focus-visible { outline: none; box-shadow: 0 0 0 var(--ring-width) color-mix(in oklch, var(--ring) 50%, transparent); }
.td-tabs__tab[aria-selected="true"] { color: var(--foreground); }
.td-tabs--pill .td-tabs__list { background: var(--muted); padding: var(--space-0-5); border-radius: var(--radius-control); }
.td-tabs--pill .td-tabs__tab { border-radius: var(--radius-control); }
.td-tabs--pill .td-tabs__tab[aria-selected="true"] { background: var(--background); box-shadow: var(--shadow-xs); }
.td-tabs--underline .td-tabs__list { gap: var(--space-4); border-bottom: 1px solid var(--border); justify-self: stretch; }
.td-tabs--underline .td-tabs__tab { padding: 0; border-bottom: 2px solid transparent; margin-bottom: -1px; }
.td-tabs--underline .td-tabs__tab[aria-selected="true"] { border-bottom-color: var(--primary); }
.td-tabs__panel { font-size: var(--text-sm); line-height: var(--leading-normal); }

.td-dialog-overlay { position: fixed; inset: 0; background: var(--overlay); display: grid; place-items: center; padding: var(--space-4); z-index: 50; }
.td-dialog-overlay--inline { position: relative; inset: auto; min-height: 100%; }
.td-dialog { position: relative; width: 100%; box-sizing: border-box; display: grid; gap: var(--space-4); padding: var(--space-6);
  background: var(--popover); color: var(--popover-foreground); border: 1px solid var(--border); border-radius: var(--radius-surface);
  box-shadow: var(--shadow-lg); font-family: var(--font-body); }
.td-dialog--sm { max-width: 400px; } .td-dialog--md { max-width: 520px; } .td-dialog--lg { max-width: 720px; }
.td-dialog__title { margin: 0; font-family: var(--font-display); font-size: var(--text-xl); font-weight: var(--font-weight-semibold); letter-spacing: var(--tracking-tight); line-height: var(--leading-snug); }
.td-dialog__description { margin: var(--space-1-5) 0 0; font-size: var(--text-sm); color: var(--muted-foreground); line-height: var(--leading-normal); }
.td-dialog__footer { display: flex; justify-content: flex-end; gap: var(--space-2); }
.td-dialog__close { position: absolute; top: var(--space-3); right: var(--space-3); }

.td-table-wrap { width: 100%; overflow-x: auto; }
.td-table { width: 100%; border-collapse: collapse; font-family: var(--font-body); font-size: var(--text-sm); color: var(--foreground); }
.td-table caption { caption-side: bottom; padding-top: var(--space-3); font-size: var(--text-xs); color: var(--muted-foreground); text-align: left; }
.td-table th { text-align: left; font-family: var(--table-head-font); text-transform: var(--table-head-case); font-size: var(--text-xs);
  font-weight: var(--font-weight-medium); letter-spacing: var(--tracking-wide); color: var(--muted-foreground); border-bottom: 1px solid var(--border); }
.td-table td { border-bottom: 1px solid var(--border); white-space: nowrap; }
.td-table th, .td-table td { padding: 0 var(--space-3); }
.td-table--compact tr { height: var(--row-h-compact); }
.td-table--comfortable tr { height: var(--row-h-comfortable); }
.td-table--striped tbody tr:nth-child(even) { background: var(--muted); }
.td-table tbody tr:hover { background: color-mix(in oklch, var(--muted) 70%, transparent); }
.td-table .td-num { text-align: right; font-family: var(--font-mono); font-variant-numeric: tabular-nums; }

.td-nav { display: flex; align-items: center; gap: var(--space-6); height: var(--nav-h); padding: 0 var(--space-6);
  background: var(--background); color: var(--foreground); border-bottom: 1px solid var(--border); font-family: var(--font-ui); box-sizing: border-box; }
.td-nav__brand { font-family: var(--font-display); font-weight: var(--font-weight-bold); font-size: var(--text-lg); letter-spacing: var(--tracking-tight); text-decoration: none; color: inherit; }
.td-nav__items { display: flex; gap: var(--space-1); list-style: none; margin: 0; padding: 0; }
.td-nav__link { display: inline-flex; align-items: center; height: var(--control-h-sm); padding: 0 var(--space-3); border-radius: var(--radius-control);
  font-size: var(--text-sm); font-weight: var(--font-weight-medium); color: var(--muted-foreground); text-decoration: none;
  transition: color var(--duration-fast) var(--ease-standard), background-color var(--duration-fast) var(--ease-standard); }
.td-nav__link:hover { color: var(--foreground); background: var(--accent); }
.td-nav__link[aria-current="page"] { color: var(--foreground); background: var(--secondary); }
.td-nav__actions { margin-left: auto; display: flex; gap: var(--space-2); align-items: center; }
.td-nav--vertical { flex-direction: column; align-items: stretch; height: auto; width: 240px; padding: var(--space-4); gap: var(--space-4); border-bottom: 0; border-right: 1px solid var(--border); }
.td-nav--vertical .td-nav__items { flex-direction: column; }
.td-nav--vertical .td-nav__link { width: 100%; box-sizing: border-box; }
.td-nav--vertical .td-nav__actions { margin-left: 0; margin-top: auto; }
"""

SPECIMEN_CSS = """/* Specimen-card chrome (TeleDesign seeds). Token-only. */
html, body { margin: 0; }
body { background: var(--background); color: var(--foreground); font-family: var(--font-body); }
.td-panes { display: grid; grid-template-columns: 1fr 1fr; min-height: 100vh; }
@media (max-width: 720px) { .td-panes { grid-template-columns: 1fr; } }
.td-panes--stack { grid-template-columns: 1fr; }
.td-pane { padding: var(--space-8); display: grid; align-content: start; gap: var(--space-6); background: var(--background); color: var(--foreground); }
.td-pane__label { font-family: var(--font-mono); font-size: var(--text-xs); letter-spacing: var(--tracking-wide); text-transform: uppercase; color: var(--muted-foreground); }
.td-row { display: flex; flex-wrap: wrap; gap: var(--space-3); align-items: center; }
.td-stack { display: grid; gap: var(--space-4); }
.td-swatch-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: var(--space-3); }
.td-swatch { border: 1px solid var(--border); border-radius: var(--radius-md); overflow: hidden; font-family: var(--font-mono); font-size: var(--text-xs); }
.td-swatch__chip { height: 56px; }
.td-swatch__meta { padding: var(--space-2); display: grid; gap: var(--space-0-5); }
.td-swatch__meta span:last-child { color: var(--muted-foreground); }
.td-type-row { display: grid; grid-template-columns: 96px 1fr; gap: var(--space-4); align-items: baseline; border-bottom: 1px solid var(--border); padding-bottom: var(--space-3); }
.td-type-row code { font-family: var(--font-mono); font-size: var(--text-xs); color: var(--muted-foreground); }
.td-space-row { display: grid; grid-template-columns: 96px 1fr; gap: var(--space-4); align-items: center; font-family: var(--font-mono); font-size: var(--text-xs); color: var(--muted-foreground); }
.td-space-bar { height: var(--space-3); background: var(--primary); border-radius: var(--radius-sm); }
"""

ICONS = {
    "x": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M18 6 6 18" /><path d="m6 6 12 12" /></svg>',
    "menu": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 5h16" /><path d="M4 12h16" /><path d="M4 19h16" /></svg>',
    "search": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m21 21-4.34-4.34" /><circle cx="11" cy="11" r="8" /></svg>',
}

# name → (group dir, card group label, viewport, props spec, jsx body)
COMP = {}

COMP["Button"] = ("actions", "Buttons", "800x360", {
    "variant": {"type": "enum", "values": ["primary", "secondary", "outline", "ghost", "destructive", "link"], "default": "primary"},
    "size": {"type": "enum", "values": ["sm", "md", "lg", "icon"], "default": "md"},
    "disabled": {"type": "boolean", "default": False},
    "type": {"type": "enum", "values": ["button", "submit", "reset"], "default": "button"},
    "children": {"type": "node"},
}, """
  const VARIANTS = ["primary", "secondary", "outline", "ghost", "destructive", "link"];
  const SIZES = ["sm", "md", "lg", "icon"];
  function Button(props) {
    const { variant = "primary", size = "md", disabled = false, type = "button", className = "", children } = props;
    const rest = omit(props, ["variant", "size", "disabled", "type", "className", "children"]);
    const v = VARIANTS.includes(variant) ? variant : "primary";
    const s = SIZES.includes(size) ? size : "md";
    const cls = ["td-btn", "td-btn--" + v, "td-btn--" + s, className].filter(Boolean).join(" ");
    return <button type={type} disabled={disabled} className={cls} {...rest}>{children}</button>;
  }
  Button.variants = VARIANTS;
  Button.sizes = SIZES;
  Object.assign(window, { Button });
""")

COMP["Input"] = ("forms", "Forms", "800x420", {
    "label": {"type": "string"}, "hint": {"type": "string"}, "error": {"type": "string"},
    "size": {"type": "enum", "values": ["sm", "md", "lg"], "default": "md"},
    "type": {"type": "enum", "values": ["text", "email", "password", "search", "number", "url", "tel"], "default": "text"},
    "invalid": {"type": "boolean", "default": False}, "disabled": {"type": "boolean", "default": False},
    "placeholder": {"type": "string"}, "id": {"type": "string"},
}, """
  const SIZES = ["sm", "md", "lg"];
  let seq = 0;
  function Input(props) {
    const { label, hint, error, size = "md", type = "text", invalid = false, id, className = "" } = props;
    const rest = omit(props, ["label", "hint", "error", "size", "type", "invalid", "id", "className"]);
    const [autoId] = React.useState(() => "td-input-" + (++seq));
    const inputId = id || autoId;
    const s = SIZES.includes(size) ? size : "md";
    const bad = invalid || Boolean(error);
    const hintId = (hint || error) ? inputId + "-hint" : undefined;
    return (
      <div className="td-field">
        {label && <label className="td-label" htmlFor={inputId}>{label}</label>}
        <input id={inputId} type={type} aria-invalid={bad ? "true" : undefined} aria-describedby={hintId}
          className={["td-input", s !== "md" && "td-input--" + s, className].filter(Boolean).join(" ")} {...rest} />
        {(error || hint) && <span id={hintId} className={error ? "td-hint td-hint--error" : "td-hint"}>{error || hint}</span>}
      </div>
    );
  }
  Input.sizes = SIZES;
  Object.assign(window, { Input });
""")

COMP["Card"] = ("layout", "Layout", "800x420", {
    "title": {"type": "string"}, "description": {"type": "string"}, "footer": {"type": "node"},
    "padding": {"type": "enum", "values": ["sm", "md", "lg"], "default": "md"},
    "elevation": {"type": "enum", "values": ["flat", "raised"], "default": "flat"},
    "children": {"type": "node"},
}, """
  const PADDINGS = ["sm", "md", "lg"];
  const ELEVATIONS = ["flat", "raised"];
  function Card(props) {
    const { title, description, footer, padding = "md", elevation = "flat", className = "", children } = props;
    const rest = omit(props, ["title", "description", "footer", "padding", "elevation", "className", "children"]);
    const p = PADDINGS.includes(padding) ? padding : "md";
    const e = ELEVATIONS.includes(elevation) ? elevation : "flat";
    const cls = ["td-card", "td-card--pad-" + p, e === "raised" && "td-card--raised", className].filter(Boolean).join(" ");
    return (
      <section className={cls} {...rest}>
        {(title || description) && (
          <header className="td-card__header">
            {title && <h3 className="td-card__title">{title}</h3>}
            {description && <p className="td-card__description">{description}</p>}
          </header>
        )}
        {children && <div className="td-card__content">{children}</div>}
        {footer && <footer className="td-card__footer">{footer}</footer>}
      </section>
    );
  }
  Card.paddings = PADDINGS;
  Card.elevations = ELEVATIONS;
  Object.assign(window, { Card });
""")

COMP["Badge"] = ("data-display", "Data display", "800x240", {
    "variant": {"type": "enum", "values": ["default", "secondary", "outline", "success", "warning", "destructive"], "default": "default"},
    "children": {"type": "node"},
}, """
  const VARIANTS = ["default", "secondary", "outline", "success", "warning", "destructive"];
  function Badge(props) {
    const { variant = "default", className = "", children } = props;
    const rest = omit(props, ["variant", "className", "children"]);
    const v = VARIANTS.includes(variant) ? variant : "default";
    return <span className={["td-badge", "td-badge--" + v, className].filter(Boolean).join(" ")} {...rest}>{children}</span>;
  }
  Badge.variants = VARIANTS;
  Object.assign(window, { Badge });
""")

COMP["Tabs"] = ("navigation", "Navigation", "800x320", {
    "items": {"type": "array", "of": "{id: string, label: string, content: node}"},
    "value": {"type": "string"}, "defaultValue": {"type": "string"}, "onChange": {"type": "function"},
    "variant": {"type": "enum", "values": ["pill", "underline"], "default": "@@TABS@@"},
}, """
  const VARIANTS = ["pill", "underline"];
  function Tabs({ items = [], value, defaultValue, onChange, variant = "@@TABS@@", className = "" }) {
    const [inner, setInner] = React.useState(defaultValue || (items[0] && items[0].id));
    const active = value !== undefined ? value : inner;
    const v = VARIANTS.includes(variant) ? variant : "@@TABS@@";
    const select = (id) => { if (value === undefined) setInner(id); if (onChange) onChange(id); };
    const onKey = (e, i) => {
      const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!d || !items.length) return;
      const next = items[(i + d + items.length) % items.length];
      select(next.id);
      const btn = e.currentTarget.parentElement.querySelector('[data-tab="' + next.id + '"]');
      if (btn) btn.focus();
    };
    const current = items.find((t) => t.id === active);
    return (
      <div className={["td-tabs", "td-tabs--" + v, className].filter(Boolean).join(" ")}>
        <div className="td-tabs__list" role="tablist">
          {items.map((t, i) => (
            <button key={t.id} type="button" role="tab" data-tab={t.id} className="td-tabs__tab"
              aria-selected={t.id === active} tabIndex={t.id === active ? 0 : -1}
              onClick={() => select(t.id)} onKeyDown={(e) => onKey(e, i)}>{t.label}</button>
          ))}
        </div>
        {current && current.content !== undefined && <div className="td-tabs__panel" role="tabpanel">{current.content}</div>}
      </div>
    );
  }
  Tabs.variants = VARIANTS;
  Object.assign(window, { Tabs });
""")

COMP["Dialog"] = ("overlays", "Overlays", "800x480", {
    "open": {"type": "boolean", "default": False}, "onClose": {"type": "function"},
    "title": {"type": "string"}, "description": {"type": "string"}, "footer": {"type": "node"},
    "size": {"type": "enum", "values": ["sm", "md", "lg"], "default": "md"},
    "inline": {"type": "boolean", "default": False, "note": "render in flow (specimens, static mockups) instead of a fixed overlay"},
    "children": {"type": "node"},
}, """
  const SIZES = ["sm", "md", "lg"];
  const X = @@ICON_X@@;
  function Dialog({ open = false, onClose, title, description, footer, size = "md", inline = false, children }) {
    React.useEffect(() => {
      if (!open || inline) return undefined;
      const onKey = (e) => { if (e.key === "Escape" && onClose) onClose(); };
      document.addEventListener("keydown", onKey);
      return () => document.removeEventListener("keydown", onKey);
    }, [open, inline, onClose]);
    const titleId = React.useId();
    if (!open) return null;
    const s = SIZES.includes(size) ? size : "md";
    return (
      <div className={"td-dialog-overlay" + (inline ? " td-dialog-overlay--inline" : "")}
        onMouseDown={(e) => { if (e.target === e.currentTarget && onClose) onClose(); }}>
        <div className={"td-dialog td-dialog--" + s} role="dialog" aria-modal={inline ? undefined : "true"} aria-labelledby={title ? titleId : undefined}>
          {onClose && <button type="button" className="td-btn td-btn--ghost td-btn--icon td-dialog__close" aria-label="Close" onClick={onClose}>{X}</button>}
          {(title || description) && (
            <div>
              {title && <h2 id={titleId} className="td-dialog__title">{title}</h2>}
              {description && <p className="td-dialog__description">{description}</p>}
            </div>
          )}
          {children}
          {footer && <div className="td-dialog__footer">{footer}</div>}
        </div>
      </div>
    );
  }
  Dialog.sizes = SIZES;
  Object.assign(window, { Dialog });
""")

COMP["Table"] = ("data-display", "Data display", "800x420", {
    "columns": {"type": "array", "of": "{key: string, label: string, numeric?: boolean}"},
    "rows": {"type": "array", "of": "object keyed by column key"},
    "density": {"type": "enum", "values": ["compact", "comfortable"], "default": "@@DENSITY@@"},
    "striped": {"type": "boolean", "default": "@@STRIPED@@"},
    "caption": {"type": "string"},
}, """
  const DENSITIES = ["compact", "comfortable"];
  function Table({ columns = [], rows = [], density = "@@DENSITY@@", striped = @@STRIPED@@, caption, className = "" }) {
    const d = DENSITIES.includes(density) ? density : "@@DENSITY@@";
    const cls = ["td-table", "td-table--" + d, striped && "td-table--striped", className].filter(Boolean).join(" ");
    return (
      <div className="td-table-wrap">
        <table className={cls}>
          {caption && <caption>{caption}</caption>}
          <thead><tr>{columns.map((c) => <th key={c.key} scope="col" className={c.numeric ? "td-num" : undefined}>{c.label}</th>)}</tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.id || i}>{columns.map((c) => <td key={c.key} className={c.numeric ? "td-num" : undefined}>{r[c.key]}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  Table.densities = DENSITIES;
  Object.assign(window, { Table });
""")

COMP["Nav"] = ("navigation", "Navigation", "1100x860", {
    "brand": {"type": "node"}, "items": {"type": "array", "of": "{label: string, href: string, active?: boolean}"},
    "actions": {"type": "node"}, "orientation": {"type": "enum", "values": ["horizontal", "vertical"], "default": "horizontal"},
    "label": {"type": "string", "default": "Main"},
}, """
  const ORIENTATIONS = ["horizontal", "vertical"];
  function Nav({ brand, items = [], actions, orientation = "horizontal", label = "Main", className = "" }) {
    const o = ORIENTATIONS.includes(orientation) ? orientation : "horizontal";
    return (
      <nav aria-label={label} className={["td-nav", o === "vertical" && "td-nav--vertical", className].filter(Boolean).join(" ")}>
        {brand && <a className="td-nav__brand" href="#">{brand}</a>}
        <ul className="td-nav__items">
          {items.map((it) => (
            <li key={it.label}><a className="td-nav__link" href={it.href || "#"} aria-current={it.active ? "page" : undefined}>{it.label}</a></li>
          ))}
        </ul>
        {actions && <div className="td-nav__actions">{actions}</div>}
      </nav>
    );
  }
  Nav.orientations = ORIENTATIONS;
  Object.assign(window, { Nav });
""")

SPECIMENS = {
    "Button": ("", """
  <div className="td-row">{Button.variants.map((v) => <Button key={v} variant={v}>{v[0].toUpperCase() + v.slice(1)}</Button>)}</div>
  <div className="td-row"><Button size="sm">Small</Button><Button size="md">Medium</Button><Button size="lg">Large</Button>
    <Button size="icon" variant="outline" aria-label="Search">{SEARCH}</Button><Button disabled>Disabled</Button></div>
"""),
    "Input": ("", """
  <div className="td-stack" style={{ maxWidth: 320 }}>
    <Input label="Email" type="email" placeholder="you@example.com" hint="We never share it." />
    <Input label="Workspace" defaultValue="north-studio" error="That name is taken." />
    <Input label="Disabled" placeholder="Read only" disabled />
    <div className="td-row"><Input size="sm" placeholder="Small" /><Input size="lg" placeholder="Large" /></div>
  </div>
"""),
    "Card": ("../actions/Button.jsx", """
  <div className="td-row" style={{ alignItems: "stretch" }}>
    <Card title="Weekly digest" description="A short summary of what changed." footer={<><Button size="sm">Open</Button><Button size="sm" variant="ghost">Dismiss</Button></>} style={{ width: 280 }}>
      Twelve items were updated and three need review.
    </Card>
    <Card elevation="raised" padding="lg" title="Raised" description="Popovers and floating panels." style={{ width: 240 }} />
  </div>
"""),
    "Badge": ("", """
  <div className="td-row">{Badge.variants.map((v) => <Badge key={v} variant={v}>{v}</Badge>)}</div>
"""),
    "Tabs": ("", """
  <Tabs items={[{ id: "overview", label: "Overview", content: "Summary of the project and its status." },
    { id: "activity", label: "Activity", content: "Recent changes by the team." },
    { id: "settings", label: "Settings", content: "Configure who can see this." }]} />
  <Tabs variant={"@@TABS@@" === "pill" ? "underline" : "pill"} items={[{ id: "a", label: "Day" }, { id: "b", label: "Week" }, { id: "c", label: "Month" }]} />
"""),
    "Dialog": ("../actions/Button.jsx", """
  <div style={{ minHeight: 300 }}>
    <Dialog open inline size="sm" onClose={() => {}} title="Delete project?" description="This removes all boards and versions. It cannot be undone."
      footer={<><Button variant="outline">Cancel</Button><Button variant="destructive">Delete</Button></>} />
  </div>
"""),
    "Table": ("Badge.jsx", """
  <Table caption="Three most recent runs" columns={[{ key: "name", label: "Run" }, { key: "status", label: "Status" }, { key: "dur", label: "Duration", numeric: true }, { key: "cost", label: "Cost", numeric: true }]}
    rows={[{ name: "nightly-build", status: <Badge variant="success">passed</Badge>, dur: "4m 12s", cost: "0.84" },
      { name: "deploy-preview", status: <Badge variant="warning">slow</Badge>, dur: "9m 03s", cost: "1.92" },
      { name: "migrate-db", status: <Badge variant="destructive">failed</Badge>, dur: "0m 41s", cost: "0.11" },
      { name: "lint", status: <Badge variant="secondary">queued</Badge>, dur: "—", cost: "—" }]} />
"""),
    "Nav": ("../actions/Button.jsx", """
  <Nav brand="Northwind" items={[{ label: "Overview", active: true }, { label: "Projects" }, { label: "Reports" }]}
    actions={<><Button variant="ghost" size="icon" aria-label="Search">{SEARCH}</Button><Button size="sm">New</Button></>} />
  <Nav orientation="vertical" brand="Northwind" items={[{ label: "Inbox", active: true }, { label: "Drafts" }, { label: "Archive" }]} actions={<Button size="sm" variant="outline">Settings</Button>} />
"""),
}


def component_jsx(s, name):
    e = EXTRA[s["slug"]]
    body = COMP[name][4].replace("@@TABS@@", e["tabs"]).replace("@@DENSITY@@", e["density"]).replace("@@STRIPED@@", e["striped"]).replace("@@ICON_X@@", ICONS["x"])
    if "omit(" in body:
        body = ("\n  // No object-rest destructuring: Babel standalone hoists its helper (_excluded) to global scope,\n"
                "  // so two component files using ...rest collide. Copy props manually instead.\n"
                "  const omit = (o, keys) => { const r = {}; for (const k in o) if (keys.indexOf(k) < 0) r[k] = o[k]; return r; };" + body)
    return (f"/* TeleDesign seed system \"{s['name']}\" — {name}.\n"
            f" * React 18, no build: load with <script type=\"text/babel\" data-presets=\"react\" src=\"{name}.jsx\">.\n"
            f" * Exports window.{name}. Styling lives in ../../components.css and reads tokens.css only.\n"
            f" * Props: see manifest.json. Icon path data from Lucide (ISC; x/search/menu are Feather-derived, MIT). */\n"
            f"(() => {{{body}}})();\n")


def head(s, title, extra_css=""):
    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{s['name']} · {title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{gf_url(s)}">
<link rel="stylesheet" href="{extra_css}tokens.css">
<link rel="stylesheet" href="{extra_css}components.css">
<link rel="stylesheet" href="{extra_css}specimen.css">"""


def component_card(s, name):
    group_dir, group, vp, _props, _ = COMP[name]
    dep, body = SPECIMENS[name]
    body = body.replace("@@TABS@@", EXTRA[s["slug"]]["tabs"])
    deps = [d for d in dep.split(",") if d]
    dep_tags = "".join(f'<script type="text/babel" data-presets="react" src="{d}"></script>\n' for d in deps)
    search = ICONS["search"]
    stack = " td-panes--stack" if name == "Nav" else ""
    d, o = s["default_theme"], ("light" if s["default_theme"] == "dark" else "dark")
    return f"""<!-- @tdCard group="{group}" name="{name}" viewport="{vp}" -->
<!doctype html>
<html lang="en" data-theme="{d}">
<head>
{head(s, name, "../../")}
{REACT}
</head>
<body>
<div id="root"></div>
{dep_tags}<script type="text/babel" data-presets="react" src="{name}.jsx"></script>
<script type="text/babel" data-presets="react">
const SEARCH = {search};
function Specimen() {{
  return (<>{body}  </>);
}}
function Pane({{ theme }}) {{
  return <section className="td-pane" data-theme={{theme}}><div className="td-pane__label">{name} · {{theme}}</div><Specimen /></section>;
}}
ReactDOM.createRoot(document.getElementById("root")).render(<div className="td-panes{stack}"><Pane theme="{d}" /><Pane theme="{o}" /></div>);
</script>
</body>
</html>
"""


def colors_card(s):
    d, o = s["default_theme"], ("light" if s["default_theme"] == "dark" else "dark")

    def pane(th):
        sw = "\n".join(
            f'      <div class="td-swatch"><div class="td-swatch__chip" style="background: var(--{t})"></div>'
            f'<div class="td-swatch__meta"><span>--{t}</span><span>{colors(s, th)[t]}</span></div></div>'
            for t in COLOR_TOKENS)
        return f'  <section class="td-pane" data-theme="{th}">\n    <div class="td-pane__label">Colour · {th}</div>\n    <div class="td-swatch-grid">\n{sw}\n    </div>\n  </section>'
    return f"""<!-- @tdCard group="Colors" name="Colour tokens" viewport="1200x900" -->
<!doctype html>
<html lang="en" data-theme="{d}">
<head>
{head(s, "Colour", "../")}
</head>
<body>
<div class="td-panes">
{pane(d)}
{pane(o)}
</div>
</body>
</html>
"""


def type_card(s):
    d = s["default_theme"]
    rows = []
    for k in reversed(list(s["type"].keys())):
        fam = "display" if k in ("2xl", "3xl", "4xl", "5xl") else "body"
        lead = "tight" if fam == "display" else "normal"
        track = "tight" if fam == "display" else "normal"
        wt = "bold" if fam == "display" else "regular"
        rows.append(f'    <div class="td-type-row"><code>--text-{k}<br>{s["type"][k]}px · {fam}</code>'
                    f'<div style="font-family: var(--font-{fam}); font-size: var(--text-{k}); line-height: var(--leading-{lead}); letter-spacing: var(--tracking-{track}); font-weight: var(--font-weight-{wt})">'
                    f'{"Design is how it works" if fam == "display" else "The quick brown fox jumps over the lazy dog, 0123456789."}</div></div>')
    fams = "\n".join(
        f'    <div class="td-type-row"><code>--font-{k}</code><div style="font-family: var(--font-{k}); font-size: var(--text-lg)">{s["fonts"][k]} — Aa Bb Cc 0123 {{}}[]</div></div>'
        for k in ("display", "body", "ui", "mono"))
    return f"""<!-- @tdCard group="Typography" name="Type scale" viewport="1200x1000" -->
<!doctype html>
<html lang="en" data-theme="{d}">
<head>
{head(s, "Typography", "../")}
</head>
<body>
<section class="td-pane" data-theme="{d}">
  <div class="td-pane__label">Families</div>
  <div class="td-stack">
{fams}
  </div>
  <div class="td-pane__label">Scale</div>
  <div class="td-stack">
{chr(10).join(rows)}
  </div>
</section>
</body>
</html>
"""


def spacing_card(s):
    d = s["default_theme"]
    rows = "\n".join(f'    <div class="td-space-row"><span>--space-{k} · {px}px</span><div class="td-space-bar" style="width: var(--space-{k})"></div></div>' for k, px in SPACING if px)
    rad = "\n".join(f'    <div class="td-space-row"><span>--radius-{k}</span><div style="width: 96px; height: 48px; background: var(--muted); border: 1px solid var(--border); border-radius: var(--radius-{k})"></div></div>' for k in list(s["radius"]) + ["control", "surface", "full"])
    sh = "\n".join(f'    <div class="td-space-row"><span>--shadow-{k}</span><div style="width: 160px; height: 64px; background: var(--card); border-radius: var(--radius-md); box-shadow: var(--shadow-{k})"></div></div>' for k in ("xs", "sm", "md", "lg"))
    return f"""<!-- @tdCard group="Spacing" name="Spacing, radius and elevation" viewport="900x1100" -->
<!doctype html>
<html lang="en" data-theme="{d}">
<head>
{head(s, "Spacing", "../")}
</head>
<body>
<section class="td-pane" data-theme="{d}">
  <div class="td-pane__label">Spacing (4px grid)</div>
  <div class="td-stack">
{rows}
  </div>
  <div class="td-pane__label">Radius</div>
  <div class="td-stack">
{rad}
  </div>
  <div class="td-pane__label">Elevation</div>
  <div class="td-stack">
{sh}
  </div>
</section>
</body>
</html>
"""


# ── .pen library ────────────────────────────────────────────────────────────
def pen_library(s):
    d = s["default_theme"].capitalize()
    o = "Light" if d == "Dark" else "Dark"
    V = {}
    for t in COLOR_TOKENS:
        V[f"--{t}"] = {"type": "color", "value": [
            {"value": oklch_hex(colors(s, d.lower())[t]), "theme": {"Mode": d}},
            {"value": oklch_hex(colors(s, o.lower())[t]), "theme": {"Mode": o}}]}
    for k, px in s["radius"].items():
        V[f"--radius-{k}"] = {"type": "number", "value": px}
    V["--radius-full"] = {"type": "number", "value": 9999}
    rc = s["radius_control"]
    V["--radius-control"] = {"type": "number", "value": 9999 if rc == "full" else s["radius"][rc]}
    V["--radius-surface"] = {"type": "number", "value": s["radius"][s["radius_surface"]]}
    ri = EXTRA[s["slug"]]["radius_input"].split("-")[-1].rstrip(")")
    V["--radius-input"] = {"type": "number", "value": s["radius"][ri]}
    rb = EXTRA[s["slug"]]["radius_badge"].split("-")[-1].rstrip(")")
    V["--radius-badge"] = {"type": "number", "value": 9999 if rb == "full" else s["radius"][rb]}
    for k, px in SPACING:
        V[f"--space-{k}"] = {"type": "number", "value": px}
    for k, px in s["type"].items():
        V[f"--text-{k}"] = {"type": "number", "value": px}
    for k, px in s["control_h"].items():
        V[f"--control-h-{k}"] = {"type": "number", "value": px}
    V["--nav-h"] = {"type": "number", "value": s["nav_h"]}
    for k in ("display", "body", "ui", "mono"):
        V[f"--font-{k}"] = {"type": "string", "value": s["fonts"][k]}
    V["--button-weight"] = {"type": "string", "value": str(s["button"]["weight"])}
    bw = int(EXTRA[s["slug"]]["border_w"].rstrip("px"))

    upper = s["button"]["case"] == "uppercase"
    def label(cid, text, fill, size="$--text-sm", weight="$--button-weight", font="$--font-ui", **kw):
        n = {"type": "text", "id": cid, "name": "Label", "content": text.upper() if upper and font == "$--font-ui" and weight == "$--button-weight" else text,
             "fill": fill, "fontFamily": font, "fontSize": size, "fontWeight": weight}
        n.update(kw)
        return n

    children = [{"type": "note", "id": "lib-note", "name": "About this library", "x": 0, "y": -160, "width": 640, "height": 120,
                 "content": f"TeleDesign seed library — {s['name']}. Generated from tokens.json; every fill, radius, size and font is bound to a variable. "
                            f"Theme axis Mode ({d} default / {o}). Components are reusable frames named Component/Variant[/Size]; instantiate with ref and override text via descendants."}]
    x, y = 0, 0
    BTN = {"primary": ("$--primary", "$--primary-foreground", None), "secondary": ("$--secondary", "$--secondary-foreground", None),
           "outline": ("$--background", "$--foreground", "$--border"), "ghost": (None, "$--foreground", None),
           "destructive": ("$--destructive", "$--destructive-foreground", None)}
    SZ = {"Small": ("sm", "$--space-3", "$--text-xs"), "Medium": ("md", "$--space-4", "$--text-sm"), "Large": ("lg", "$--space-6", "$--text-base")}
    for vi, (v, (fill, fg, stroke)) in enumerate(BTN.items()):
        for si, (sn, (sk, pad, ts)) in enumerate(SZ.items()):
            cid = f"btn-{v}-{sk}"
            n = {"type": "frame", "id": cid, "name": f"Button/{v.capitalize()}/{sn}", "reusable": True, "x": x + si * 180, "y": y + vi * 64,
                 "layout": "horizontal", "gap": "$--space-2", "padding": [0, pad], "height": f"$--control-h-{sk}",
                 "justifyContent": "center", "alignItems": "center", "cornerRadius": "$--radius-control",
                 "children": [label(f"{cid}-label", "Button", fg, size=ts)]}
            if fill:
                n["fill"] = fill
            if stroke:
                n.update({"stroke": stroke, "strokeWidth": bw, "strokeAlignment": "inner"})
            children.append(n)
    children.append({"type": "frame", "id": "btn-link-md", "name": "Button/Link/Medium", "reusable": True, "x": x + 540, "y": y,
                     "layout": "horizontal", "alignItems": "center", "height": "$--control-h-md",
                     "children": [label("btn-link-md-label", "Learn more", "$--primary", underline=True)]})
    y += 360

    INP = {"Default": ("$--input", "Placeholder", "$--muted-foreground", "Helper text", None),
           "Focus": ("$--ring", "Typing…", "$--foreground", "Helper text", None),
           "Invalid": ("$--destructive", "bad@", "$--foreground", "Enter a valid email.", "$--destructive"),
           "Disabled": ("$--input", "Read only", "$--muted-foreground", "Not editable", None)}
    for i, (vn, (stroke, ph, phc, hint, hintc)) in enumerate(INP.items()):
        cid = f"input-{vn.lower()}"
        field = {"type": "frame", "id": f"{cid}-field", "name": "Field", "layout": "horizontal", "alignItems": "center",
                 "width": "fill_container", "height": "$--control-h-md", "padding": [0, "$--space-3"],
                 "fill": "$--muted" if vn == "Disabled" else "$--background", "cornerRadius": "$--radius-input",
                 "stroke": stroke, "strokeWidth": bw + (1 if vn == "Focus" else 0), "strokeAlignment": "inner",
                 "children": [{"type": "text", "id": f"{cid}-value", "name": "Value", "content": ph, "fill": phc,
                               "fontFamily": "$--font-ui", "fontSize": "$--text-sm"}]}
        n = {"type": "frame", "id": cid, "name": f"Input/{vn}", "reusable": True, "x": i * 320, "y": y, "width": 280,
             "layout": "vertical", "gap": "$--space-1-5",
             "opacity": 0.5 if vn == "Disabled" else 1,
             "children": [{"type": "text", "id": f"{cid}-label", "name": "Label", "content": "Email", "fill": "$--foreground",
                           "fontFamily": "$--font-ui", "fontSize": "$--text-sm", "fontWeight": "500"},
                          field,
                          {"type": "text", "id": f"{cid}-hint", "name": "Hint", "content": hint, "fill": hintc or "$--muted-foreground",
                           "fontFamily": "$--font-ui", "fontSize": "$--text-xs"}]}
        if vn != "Disabled":
            del n["opacity"]
        children.append(n)
    y += 160

    for i, (vn, eff) in enumerate((("Default", None), ("Raised", True))):
        cid = f"card-{vn.lower()}"
        n = {"type": "frame", "id": cid, "name": f"Card/{vn}", "reusable": True, "x": i * 380, "y": y, "width": 340,
             "layout": "vertical", "gap": "$--space-4", "padding": "$--space-6", "fill": "$--card",
             "stroke": "$--border", "strokeWidth": 1, "strokeAlignment": "inner", "cornerRadius": "$--radius-surface",
             "children": [
                 {"type": "frame", "id": f"{cid}-header", "name": "Header", "layout": "vertical", "gap": "$--space-1-5", "width": "fill_container", "children": [
                     {"type": "text", "id": f"{cid}-title", "name": "Title", "content": "Card title", "fill": "$--card-foreground",
                      "fontFamily": "$--font-display", "fontSize": "$--text-lg", "fontWeight": "600"},
                     {"type": "text", "id": f"{cid}-description", "name": "Description", "content": "Supporting description text.",
                      "fill": "$--muted-foreground", "fontFamily": "$--font-body", "fontSize": "$--text-sm", "textGrowth": "fixed-width", "width": "fill_container"}]},
                 {"type": "frame", "id": f"{cid}-content", "name": "Content", "layout": "vertical", "width": "fill_container", "height": 64, "slot": [], "placeholder": True},
                 {"type": "frame", "id": f"{cid}-footer", "name": "Footer", "layout": "horizontal", "gap": "$--space-2", "children": [
                     {"type": "ref", "id": f"{cid}-action", "ref": "btn-primary-sm", "descendants": {"btn-primary-sm-label": {"content": "OPEN" if upper else "Open"}}}]}]}
        if eff:
            n["effect"] = {"type": "shadow", "shadowType": "outer", "offset": {"x": 0, "y": 6}, "blur": 16, "color": "#0000001F"}
        children.append(n)
    y += 280

    BADGE = {"Default": ("$--primary", "$--primary-foreground", None), "Secondary": ("$--secondary", "$--secondary-foreground", None),
             "Outline": (None, "$--foreground", "$--border"), "Success": ("$--success", "$--success-foreground", None),
             "Warning": ("$--warning", "$--warning-foreground", None), "Destructive": ("$--destructive", "$--destructive-foreground", None)}
    bfont = "$--font-mono" if s["slug"] == "technical" else "$--font-ui"
    bup = EXTRA[s["slug"]]["badge_case"] == "uppercase"
    for i, (vn, (fill, fg, stroke)) in enumerate(BADGE.items()):
        cid = f"badge-{vn.lower()}"
        n = {"type": "frame", "id": cid, "name": f"Badge/{vn}", "reusable": True, "x": i * 130, "y": y,
             "layout": "horizontal", "alignItems": "center", "padding": ["$--space-0-5", "$--space-2"], "cornerRadius": "$--radius-badge",
             "children": [{"type": "text", "id": f"{cid}-label", "name": "Label", "content": vn.upper() if bup else vn.lower(), "fill": fg,
                           "fontFamily": bfont, "fontSize": "$--text-xs", "fontWeight": "500"}]}
        if fill:
            n["fill"] = fill
        if stroke:
            n.update({"stroke": stroke, "strokeWidth": 1, "strokeAlignment": "inner"})
        children.append(n)
    y += 80

    for i, vn in enumerate(("Pill", "Underline")):
        cid = f"tabs-{vn.lower()}"
        tabs = []
        for j, lab in enumerate(("Overview", "Activity", "Settings")):
            tid = f"{cid}-tab{j + 1}"
            act = j == 0
            t = {"type": "frame", "id": tid, "name": f"Tab {j + 1}", "layout": "horizontal", "alignItems": "center",
                 "height": 30, "padding": [0, "$--space-3"] if vn == "Pill" else 0,
                 "children": [{"type": "text", "id": f"{tid}-label", "name": "Label", "content": lab,
                               "fill": "$--foreground" if act else "$--muted-foreground", "fontFamily": "$--font-ui",
                               "fontSize": "$--text-sm", "fontWeight": "500"}]}
            if vn == "Pill":
                t["cornerRadius"] = "$--radius-control"
                if act:
                    t["fill"] = "$--background"
            elif act:
                t.update({"stroke": "$--primary", "strokeWidth": {"bottom": 2}, "strokeAlignment": "inner"})
            tabs.append(t)
        n = {"type": "frame", "id": cid, "name": f"Tabs/{vn}", "reusable": True, "x": i * 400, "y": y,
             "layout": "horizontal", "alignItems": "center", "children": tabs}
        if vn == "Pill":
            n.update({"fill": "$--muted", "padding": "$--space-0-5", "gap": "$--space-1", "cornerRadius": "$--radius-control"})
        else:
            n.update({"gap": "$--space-4", "width": 360, "stroke": "$--border", "strokeWidth": {"bottom": 1}, "strokeAlignment": "inner"})
        children.append(n)
    y += 100

    children.append({"type": "frame", "id": "dialog-default", "name": "Dialog/Default", "reusable": True, "x": 0, "y": y, "width": 440,
                     "layout": "vertical", "gap": "$--space-4", "padding": "$--space-6", "fill": "$--popover", "cornerRadius": "$--radius-surface",
                     "stroke": "$--border", "strokeWidth": 1, "strokeAlignment": "inner",
                     "effect": {"type": "shadow", "shadowType": "outer", "offset": {"x": 0, "y": 16}, "blur": 40, "color": "#00000033"},
                     "children": [
                         {"type": "frame", "id": "dialog-default-top", "name": "Top", "layout": "horizontal", "width": "fill_container", "justifyContent": "space_between", "alignItems": "start", "children": [
                             {"type": "frame", "id": "dialog-default-heading", "name": "Heading", "layout": "vertical", "gap": "$--space-1-5", "width": "fill_container", "children": [
                                 {"type": "text", "id": "dialog-default-title", "name": "Title", "content": "Delete project?", "fill": "$--popover-foreground",
                                  "fontFamily": "$--font-display", "fontSize": "$--text-xl", "fontWeight": "600"},
                                 {"type": "text", "id": "dialog-default-description", "name": "Description", "content": "This removes all boards and versions. It cannot be undone.",
                                  "fill": "$--muted-foreground", "fontFamily": "$--font-body", "fontSize": "$--text-sm", "textGrowth": "fixed-width", "width": "fill_container"}]},
                             {"type": "icon", "id": "dialog-default-close", "name": "Close", "library": "lucide", "icon": "x", "width": 16, "height": 16, "fill": "$--muted-foreground"}]},
                         {"type": "frame", "id": "dialog-default-body", "name": "Body", "layout": "vertical", "width": "fill_container", "height": 24, "slot": [], "placeholder": True},
                         {"type": "frame", "id": "dialog-default-footer", "name": "Footer", "layout": "horizontal", "gap": "$--space-2", "width": "fill_container", "justifyContent": "end", "children": [
                             {"type": "ref", "id": "dialog-default-cancel", "ref": "btn-outline-md", "descendants": {"btn-outline-md-label": {"content": "CANCEL" if upper else "Cancel"}}},
                             {"type": "ref", "id": "dialog-default-confirm", "ref": "btn-destructive-md", "descendants": {"btn-destructive-md-label": {"content": "DELETE" if upper else "Delete"}}}]}]})

    rows_data = [("Run", "Status", "Duration"), ("nightly-build", "passed", "4m 12s"), ("deploy-preview", "slow", "9m 03s"), ("migrate-db", "failed", "0m 41s")]
    rh = s["row_h"][EXTRA[s["slug"]]["density"]]
    trs = []
    for ri_, row in enumerate(rows_data):
        head_ = ri_ == 0
        rid = f"table-default-row{ri_}"
        cells = []
        for ci, val in enumerate(row):
            num = ci == 2
            cells.append({"type": "frame", "id": f"{rid}-c{ci}", "name": f"Cell {ci + 1}", "layout": "horizontal", "alignItems": "center",
                          "width": "fill_container", "height": "fill_container", "padding": [0, "$--space-3"], "justifyContent": "end" if num else "start",
                          "children": [{"type": "text", "id": f"{rid}-c{ci}-text", "name": "Text", "content": val.upper() if head_ and EXTRA[s['slug']]['th_case'] == "uppercase" else val,
                                        "fill": "$--muted-foreground" if head_ else "$--foreground",
                                        "fontFamily": "$--font-mono" if (num and not head_) or (head_ and s["slug"] == "technical") else ("$--font-ui" if head_ else "$--font-body"),
                                        "fontSize": "$--text-xs" if head_ else "$--text-sm", "fontWeight": "500" if head_ else "400"}]})
        trs.append({"type": "frame", "id": rid, "name": "Header row" if head_ else f"Row {ri_}", "layout": "horizontal", "width": "fill_container",
                    "height": rh, "stroke": "$--border", "strokeWidth": {"bottom": 1}, "strokeAlignment": "inner", "children": cells})
    children.append({"type": "frame", "id": "table-default", "name": "Table/Default", "reusable": True, "x": 520, "y": y, "width": 520,
                     "layout": "vertical", "children": trs})
    y += 320

    items = ("Overview", "Projects", "Reports")

    def nav_items(pid, vertical):
        out = []
        for i, lab in enumerate(items):
            iid = f"{pid}-item{i + 1}"
            it = {"type": "frame", "id": iid, "name": f"Item {i + 1}", "layout": "horizontal", "alignItems": "center",
                  "height": "$--control-h-sm", "padding": [0, "$--space-3"], "cornerRadius": "$--radius-control",
                  "children": [{"type": "text", "id": f"{iid}-label", "name": "Label", "content": lab,
                                "fill": "$--foreground" if i == 0 else "$--muted-foreground", "fontFamily": "$--font-ui", "fontSize": "$--text-sm", "fontWeight": "500"}]}
            if vertical:
                it["width"] = "fill_container"
            if i == 0:
                it["fill"] = "$--secondary"
            out.append(it)
        return out
    brand = lambda pid: {"type": "text", "id": f"{pid}-brand", "name": "Brand", "content": "Northwind", "fill": "$--foreground",
                         "fontFamily": "$--font-display", "fontSize": "$--text-lg", "fontWeight": "700"}
    children.append({"type": "frame", "id": "nav-horizontal", "name": "Nav/Horizontal", "reusable": True, "x": 0, "y": y, "width": 1040,
                     "height": "$--nav-h", "layout": "horizontal", "alignItems": "center", "gap": "$--space-6", "padding": [0, "$--space-6"],
                     "fill": "$--background", "stroke": "$--border", "strokeWidth": {"bottom": 1}, "strokeAlignment": "inner",
                     "children": [brand("nav-horizontal"),
                                  {"type": "frame", "id": "nav-horizontal-items", "name": "Items", "layout": "horizontal", "gap": "$--space-1", "width": "fill_container", "slot": [], "children": nav_items("nav-horizontal", False)},
                                  {"type": "icon", "id": "nav-horizontal-search", "name": "Search", "library": "lucide", "icon": "search", "width": 16, "height": 16, "fill": "$--muted-foreground"},
                                  {"type": "ref", "id": "nav-horizontal-action", "ref": "btn-primary-sm", "descendants": {"btn-primary-sm-label": {"content": "NEW" if upper else "New"}}}]})
    children.append({"type": "frame", "id": "nav-vertical", "name": "Nav/Vertical", "reusable": True, "x": 0, "y": y + 110, "width": 240, "height": 360,
                     "layout": "vertical", "gap": "$--space-4", "padding": "$--space-4", "fill": "$--background",
                     "stroke": "$--border", "strokeWidth": {"right": 1}, "strokeAlignment": "inner",
                     "children": [{"type": "frame", "id": "nav-vertical-top", "name": "Top", "layout": "horizontal", "alignItems": "center", "gap": "$--space-2", "children": [
                                      {"type": "icon", "id": "nav-vertical-menu", "name": "Menu", "library": "lucide", "icon": "menu", "width": 16, "height": 16, "fill": "$--muted-foreground"},
                                      brand("nav-vertical")]},
                                  {"type": "frame", "id": "nav-vertical-items", "name": "Items", "layout": "vertical", "gap": "$--space-1", "width": "fill_container", "slot": [], "children": nav_items("nav-vertical", True)}]})

    return {"version": "2.19", "themes": {"Mode": [d, o]}, "variables": V, "children": children}


# ── docs ────────────────────────────────────────────────────────────────────
def design_md(s):
    p = s["prose"]
    d = s["default_theme"]
    o = "light" if d == "dark" else "dark"
    ctab = "\n".join(f"| `--{t}` | `{s['light'][t] if t in s['light'] else OVERLAY['light']}` | `{s['dark'][t] if t in s['dark'] else OVERLAY['dark']}` |" for t in COLOR_TOKENS)
    fams = "\n".join(f"| `--font-{k}` | {s['fonts'][k]} | {', '.join(map(str, FONTS[s['fonts'][k]]['weights']))} | {OFL} |" for k in ("display", "body", "ui", "mono"))
    scale = " · ".join(f"{k} {v}" for k, v in s["type"].items())
    rad = " · ".join(f"{k} {v}px" for k, v in s["radius"].items())
    m = s["motion"]
    comps = "\n".join(f"- **{n}** (`components/{COMP[n][0]}/{n}.jsx`) — " + ", ".join(
        f"`{k}`: " + ("|".join(v["values"]) if v.get("type") == "enum" else v["type"]) for k, v in COMP[n][3].items() if k != "children")
        for n in COMP)
    comps = comps.replace("@@TABS@@", EXTRA[s["slug"]]["tabs"]).replace("@@DENSITY@@", EXTRA[s["slug"]]["density"])
    gaps = "\n".join(f"- {g}" for g in p["gaps"])
    do = "\n".join(f"- Do: {x}" for x in p["do"])
    dont = "\n".join(f"- Don't: {x}" for x in p["dont"])
    credit = ""
    if s["slug"] == "neutral":
        credit = "\n> Colour values: [shadcn/ui](https://github.com/shadcn-ui/ui) neutral theme (MIT). Guidance partly adapted from open-design's `default` system (Apache-2.0). See `../../NOTICE`.\n"
    return f"""# {s['name']}

> {s['description']}
> Default theme: **{d}** (the other theme is `[data-theme="{o}"]`).
{credit}
## Overview / Voice

{p['overview']}

## Colour

{p['colour']}

| Token | Light | Dark |
|---|---|---|
{ctab}

Every colour lives in `tokens.css`; components and pages reference `var(--token)` only. Foreground pairs (`--x` / `--x-foreground`) are the only sanctioned text-on-fill combinations.

## Typography

{p['typography']}

| Token | Family | Weights loaded | License |
|---|---|---|---|
{fams}

Scale (`--text-*`, px): {scale}. Line heights: {', '.join(f'{k} {v}' for k, v in s['leading'].items())}. Tracking: {', '.join(f'{k} {v}' for k, v in s['tracking'].items())}.

## Spacing & layout

{p['spacing']}

Scale (`--space-*`, px): {' · '.join(f'{k}={px}' for k, px in SPACING)}. Control heights: {', '.join(f'{k} {v}px' for k, v in s['control_h'].items())}; nav bar {s['nav_h']}px; table rows {', '.join(f'{k} {v}px' for k, v in s['row_h'].items())}.

## Radius

{p['radius']}

`--radius-*`: {rad} · full 9999px. Aliases: `--radius-control` → {s['radius_control']}, `--radius-surface` → {s['radius_surface']}.

## Elevation

{p['elevation']}

`--shadow-xs|sm|md|lg` are defined per theme (dark themes use deeper shadows). `--card-shadow` = `{EXTRA[s['slug']]['card_shadow']}`.

## Motion

{p['motion']}

Durations: fast {m['fast']} · base {m['base']} · slow {m['slow']}. Easing: standard `{m['standard']}` · out `{m['out']}` · emphasis `{m['emphasis']}`. `prefers-reduced-motion` zeroes the durations in `tokens.css`.

## Iconography

{p['iconography']}

Library: [Lucide](https://lucide.dev) (ISC). In Pencil mode use `icon` nodes with `library: "lucide"`.

## Imagery

{p['imagery']}

## Components

{p['components']}

{comps}

Each component has a `*.card.html` specimen next to it showing both themes. Styling is in `components.css` and uses tokens only.

{do}
{dont}

## Known gaps

{gaps}
"""


def usage_md(s):
    d = s["default_theme"]
    o = "light" if d == "dark" else "dark"
    return f"""# Using {s['name']}

How an agent applies this system in a TeleDesign project. The project receives a read-only copy at `_ds/{s['slug']}/`.

## Read order

1. This file.
2. `DESIGN.md` — intent, voice, rules and known gaps. Its Do / Don't lines are requirements, not suggestions.
3. `tokens.css` — the only source of colour, type, spacing, radius, shadow and motion values.
4. `manifest.json` — component inventory with prop enums, specimen cards, fonts and themes.
5. Open a `*.card.html` only when you need to see a component's exact states.

## Design mode (HTML / React)

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{gf_url(s)}">
<link rel="stylesheet" href="_ds/{s['slug']}/tokens.css">
<link rel="stylesheet" href="_ds/{s['slug']}/components.css">
```

- Themes: `:root` is **{d}**. Put `data-theme="{o}"` on `<html>` (or any subtree) for the other theme; both can appear side by side.
- Components: load after React 18.3.1 / ReactDOM / Babel standalone 7.29 (SRI hashes are in any `*.card.html`):
  `<script type="text/babel" data-presets="react" src="_ds/{s['slug']}/components/actions/Button.jsx"></script>` — each file sets `window.<Name>`.
  Pass only the prop values listed in `manifest.json`; unknown enum values fall back to the default.
- In your own `text/babel` files avoid `const {{ a, ...rest }} = props`: Babel standalone hoists the rest helper (`_excluded`) to global scope, so a second file doing the same throws "Identifier '_excluded' has already been declared". Wrap each file in an IIFE and copy props manually (see `omit` in `Button.jsx`).
- Write your own CSS with `var(--token)` only. Raw hex / rgb / oklch, raw px for spacing or radius, and font families not listed in `manifest.json` are flagged by `adherence.json` after every turn.
- Need a value the system lacks? Use the closest token and leave a visible `/* TODO(design-system): … */` comment. Do not add tokens to `_ds/`.
- Plain-HTML pages may use the class API directly (`td-btn td-btn--primary td-btn--md`, `td-input`, `td-card`, `td-badge td-badge--success`, …) — same CSS, no React needed.

## Pencil mode (.pen)

- Import the library: `"imports": {{ "ds": "_ds/{s['slug']}/system.lib.pen" }}` and bind properties to its variables (`"fill": "$--primary"`, `"cornerRadius": "$--radius-control"`, `"fontFamily": "$--font-ui"`).
- Theme axis `Mode` = `{d.capitalize()}` (default) / `{o.capitalize()}`; set `"theme": {{"Mode": "{o.capitalize()}"}}` on a frame to preview the other theme.
- Instantiate components with `ref` (e.g. `btn-primary-md`, `input-default`, `card-default`, `badge-success`, `tabs-{EXTRA[s['slug']]['tabs']}`, `dialog-default`, `table-default`, `nav-horizontal`) and change text through `descendants` (`{{"btn-primary-md-label": {{"content": "Save"}}}}`). Component names follow `Component/Variant[/Size]`.
- Colours in the library are hex conversions of the oklch tokens (the .pen format is hex-only); treat `tokens.json` as the source of truth.

## System-specific rules

{chr(10).join('- ' + x for x in s['prose']['do'])}
{chr(10).join('- Avoid: ' + x for x in s['prose']['dont'])}

## Checklist before finishing a turn

- No raw colours, no off-scale spacing, no off-system fonts (run the adherence lint mentally: `adherence.json`).
- Every text/fill pair is a documented foreground pair.
- Checked both themes if the page supports theme switching.
- Icons are Lucide; icon-only buttons have `aria-label`.
"""


def manifest(s):
    comps = []
    for n, (g, grp, vp, props, _) in COMP.items():
        pr = json.loads(json.dumps(props).replace("@@TABS@@", EXTRA[s["slug"]]["tabs"]).replace("@@DENSITY@@", EXTRA[s["slug"]]["density"]))
        if n == "Table":
            pr["striped"]["default"] = EXTRA[s["slug"]]["striped"] == "true"
        comps.append({"name": n, "group": grp, "path": f"components/{g}/{n}.jsx", "card": f"components/{g}/{n}.card.html", "global": f"window.{n}", "props": pr})
    cards = [{"path": "guidelines/colors.card.html", "group": "Colors", "viewport": "1200x900", "name": "Colour tokens"},
             {"path": "guidelines/typography.card.html", "group": "Typography", "viewport": "1200x1000", "name": "Type scale"},
             {"path": "guidelines/spacing.card.html", "group": "Spacing", "viewport": "900x1100", "name": "Spacing, radius and elevation"}]
    cards += [{"path": f"components/{g}/{n}.card.html", "group": grp, "viewport": vp, "name": n} for n, (g, grp, vp, _, __) in COMP.items()]
    d = s["default_theme"]
    return {
        "schema": "teledesign-system/v1", "name": s["name"], "slug": s["slug"], "version": "1.0.0",
        "files": {"design": "DESIGN.md", "usage": "USAGE.md", "tokensCss": "tokens.css", "tokensJson": "tokens.json",
                  "componentsCss": "components.css", "specimenCss": "specimen.css", "penLibrary": "system.lib.pen", "adherence": "adherence.json"},
        "runtime": {"react": "18.3.1", "reactDom": "18.3.1", "babelStandalone": "7.29.0", "jsxPresets": "react"},
        "components": comps, "cards": cards,
        "fonts": [{"family": f, "weights": FONTS[f]["weights"], "roles": [k for k in ("display", "body", "ui", "mono") if s["fonts"][k] == f],
                   "license": OFL, "source": "Google Fonts", "specimen": GF + f.replace(" ", "+"),
                   "css": "https://fonts.googleapis.com/css2?family=" + FONTS[f]["q"] + "&display=swap"} for f in families(s)],
        "fontsCss": gf_url(s),
        "icons": {"library": "lucide", "license": "ISC", "url": "https://lucide.dev"},
        "themes": [{"id": t, "label": t.capitalize(), "default": t == d, "selector": (':root, ' if t == d else '') + f'[data-theme="{t}"]', "penTheme": {"Mode": t.capitalize()}}
                   for t in ([d, "light" if d == "dark" else "dark"])],
    }


def adherence(s):
    sv = static_vars(s)
    comp_rules = {}
    for n, (_, __, ___, props, _____) in COMP.items():
        enums = {k: v["values"] for k, v in props.items() if v.get("type") == "enum"}
        if enums:
            comp_rules[n] = enums
    return {
        "schema": "teledesign-adherence/v1", "system": s["slug"],
        "rules": {
            "no-raw-color": {"severity": "error", "allowIn": ["tokens.css"], "pattern": r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla|oklch|oklab|lab|lch|color)\(",
                             "allowValues": ["transparent", "currentColor", "inherit"], "message": "Use a colour token: var(--primary), var(--muted-foreground), …"},
            "color-tokens": {"severity": "error", "allowed": [f"--{t}" for t in COLOR_TOKENS], "allowColorMixOf": [f"--{t}" for t in COLOR_TOKENS]},
            "spacing": {"severity": "warn", "properties": ["margin", "padding", "gap", "row-gap", "column-gap", "inset", "top", "right", "bottom", "left"],
                        "tokens": [f"--space-{k}" for k, _ in SPACING], "scalePx": [px for _, px in SPACING], "allowRawPx": [0, 1],
                        "message": "Use var(--space-N); off-scale values break the 4px grid."},
            "radius": {"severity": "warn", "tokens": [f"--{k}" for k in sv if k.startswith("radius-")],
                       "scalePx": sorted(set(list(s["radius"].values()) + [0, 9999]))},
            "font-family": {"severity": "error", "allowedFamilies": families(s), "tokens": ["--font-display", "--font-body", "--font-ui", "--font-mono"],
                            "genericFallbacks": ["serif", "sans-serif", "monospace", "system-ui", "ui-sans-serif", "ui-serif", "ui-monospace", "ui-rounded"]},
            "font-size": {"severity": "warn", "tokens": [f"--text-{k}" for k in s["type"]], "scalePx": list(s["type"].values())},
            "font-weight": {"severity": "warn", "allowed": sorted(set(w for f in families(s) for w in FONTS[f]["weights"]))},
            "shadow": {"severity": "warn", "tokens": ["--shadow-xs", "--shadow-sm", "--shadow-md", "--shadow-lg", "--card-shadow", "--button-shadow"]},
            "motion": {"severity": "info", "durationTokens": ["--duration-fast", "--duration-base", "--duration-slow"], "easingTokens": ["--ease-standard", "--ease-out", "--ease-emphasis"]},
            "components": {"severity": "error", "props": comp_rules, "message": "Unknown prop value; see manifest.json."},
            "icons": {"severity": "warn", "allowedLibraries": ["lucide"]},
            "system-specific": [{"severity": "warn", "text": x} for x in s["prose"]["dont"]],
        },
    }


def system_json(s):
    return {"name": s["name"], "slug": s["slug"], "description": s["description"], "status": "published",
            "is_default": bool(s["is_default"]), "version": "1.0.0", "default_theme": s["default_theme"], "tags": s["tags"],
            "license": "MIT (TeleDesign seed content)" + ("; colour values from shadcn/ui (MIT)" if s["slug"] == "neutral" else ""),
            "fonts_license": OFL,
            "credits": s["credits"] + [{"name": f, "license": OFL, "url": GF + f.replace(' ', '+'), "used_for": "font (linked from Google Fonts, not bundled)"} for f in families(s)],
            "source": {"type": "bundled", "origin": "services/design/seeds"}}


def w(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def wj(path, obj):
    w(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def build(s):
    root = OUT / s["slug"]
    wj(root / "system.json", system_json(s))
    w(root / "DESIGN.md", design_md(s))
    w(root / "USAGE.md", usage_md(s))
    w(root / "tokens.css", tokens_css(s))
    wj(root / "tokens.json", tokens_json(s))
    w(root / "components.css", COMPONENTS_CSS)
    w(root / "specimen.css", SPECIMEN_CSS)
    wj(root / "manifest.json", manifest(s))
    wj(root / "adherence.json", adherence(s))
    wj(root / "system.lib.pen", pen_library(s))
    for n, (g, *_rest) in COMP.items():
        w(root / "components" / g / f"{n}.jsx", component_jsx(s, n))
        w(root / "components" / g / f"{n}.card.html", component_card(s, n))
    w(root / "guidelines" / "colors.card.html", colors_card(s))
    w(root / "guidelines" / "typography.card.html", type_card(s))
    w(root / "guidelines" / "spacing.card.html", spacing_card(s))


if __name__ == "__main__":
    for s in SYSTEMS:
        for th in ("light", "dark"):
            assert set(s[th]) | {"overlay"} == set(COLOR_TOKENS), (s["slug"], th, set(COLOR_TOKENS) ^ set(s[th]))
        build(s)
        print("built", s["slug"])
