"""Design tokens ↔ canvas variables (TeleDesign parity gap #3).

One source of truth, `tokens.json` (format `teledesign-tokens/v1`, see
services/design/seeds/_build/gen_seeds.py), mirrored into the canvas editor's
variable collections — and read back when the user edits them there.

    tokens.json                         canvas (open-pencil variable collections)
    color.<theme>.<name> {value, hex}   "Color"       COLOR,  one mode per theme
    spacing.<k>  "4px"                  "Spacing"     FLOAT
    radius.<k>   "8px"                  "Radius"      FLOAT
    typography.fontSize.<k>             "Typography"  FLOAT   font-size/<k>
    typography.fontWeight.<k>                         FLOAT   font-weight/<k>
    typography.lineHeight.<k>                         FLOAT   line-height/<k>
    typography.letterSpacing.<k>                      STRING  letter-spacing/<k>
    typography.fontFamily.<k>.family                  STRING  font-family/<k>

Push (`to_collections`) is the payload of the editor command
`telecode_variables_apply` (patches/open-pencil/0009): collections, modes and
variables are upserted *by name*, so pushing twice is a no-op and variables the
user added on the canvas are left alone. Pull (`apply_canvas`) takes the
`telecode_variables_read` result, updates the token document in place and
returns a diff; `rewrite_tokens_css` carries the changed values into the
matching `--custom-property` declarations of tokens.css, so HTML boards pick
them up too. Pull is user-initiated (a button), so it writes without an
approval step — the diff is reported, and Versions can restore the files.

Where a project's tokens live (`locate`): its own `tokens.json` at the project
root (a design-system project edits its package in place), else the staged
copy of its design system in `_ds/<slug>/tokens.json`. A staged copy is
replaced when the system is restaged, which the pull result says.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

COLOR = "Color"
SPACING = "Spacing"
RADIUS = "Radius"
TYPOGRAPHY = "Typography"
VALUE_MODE = "Value"

# typography.<group> → (canvas name prefix, variable type, css var prefix)
_TYPO: Dict[str, Tuple[str, str, str]] = {
    "fontSize": ("font-size", "FLOAT", "text-"),
    "fontWeight": ("font-weight", "FLOAT", "font-weight-"),
    "lineHeight": ("line-height", "FLOAT", "leading-"),
    "letterSpacing": ("letter-spacing", "STRING", "tracking-"),
    "fontFamily": ("font-family", "STRING", "font-"),
}
_TYPO_BY_PREFIX = {v[0]: k for k, v in _TYPO.items()}

_NUM_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(px)?\s*$")
_HEX_RE = re.compile(r"^#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
MAX_VARIABLES = 2000


class TokensError(ValueError):
    pass


# ── Locating a project's tokens ──────────────────────────────────────

def locate(project_dir: Path) -> Optional[Dict[str, Any]]:
    """{"json": rel, "css": rel|None, "staged": bool} for the project's tokens, or None."""
    root = Path(project_dir)
    if (root / "tokens.json").is_file():
        css = "tokens.css" if (root / "tokens.css").is_file() else None
        return {"json": "tokens.json", "css": css, "staged": False}
    ds = root / "_ds"
    if ds.is_dir():
        for d in sorted(p for p in ds.iterdir() if p.is_dir() and not p.name.startswith(".")):
            if (d / "tokens.json").is_file():
                rel = f"_ds/{d.name}"
                css = f"{rel}/tokens.css" if (d / "tokens.css").is_file() else None
                return {"json": f"{rel}/tokens.json", "css": css, "staged": True}
    return None


def load(project_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(where, tokens) — raises TokensError when there are none or they don't parse."""
    where = locate(project_dir)
    if not where:
        raise TokensError("This project has no tokens.json (attach a design system, or add tokens.json)")
    try:
        tokens = json.loads((Path(project_dir) / where["json"]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TokensError(f"{where['json']} is not valid JSON: {exc}") from None
    if not isinstance(tokens, dict):
        raise TokensError(f"{where['json']} must hold a JSON object")
    return where, tokens


# ── Value helpers ────────────────────────────────────────────────────

def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = _NUM_RE.match(value)
        if m:
            return float(m.group(1))
    return None


def _fmt_num(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:.4f}".rstrip("0").rstrip(".")


def _color_entry(entry: Any) -> Optional[str]:
    """The colour a token entry stands for, in a form the editor parses (hex preferred)."""
    if isinstance(entry, str):
        return entry.strip() or None
    if isinstance(entry, dict):
        hx = entry.get("hex")
        if isinstance(hx, str) and _HEX_RE.match(hx.strip()):
            return hx.strip()
        val = entry.get("value")
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _norm_hex(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    v = value.strip().upper()
    if not _HEX_RE.match(v):
        return None
    if len(v) == 9 and v.endswith("FF"):
        v = v[:7]
    return v


def _themes(tokens: Dict[str, Any]) -> List[str]:
    color = tokens.get("color")
    themes = [t for t in (tokens.get("themes") or []) if isinstance(t, str) and t]
    if isinstance(color, dict) and themes and all(isinstance(color.get(t), dict) for t in themes):
        return themes
    return []


# ── Push: tokens.json → collections payload ──────────────────────────

def to_collections(tokens: Dict[str, Any]) -> Dict[str, Any]:
    """The `telecode_variables_apply` payload for a token document, plus what was skipped."""
    out: List[Dict[str, Any]] = []
    skipped: List[str] = []
    count = 0

    color = tokens.get("color")
    themes = _themes(tokens)
    if isinstance(color, dict):
        if themes:
            default = tokens.get("defaultTheme") if tokens.get("defaultTheme") in themes else themes[0]
            names: List[str] = []
            for t in themes:
                for n in color[t]:
                    if n not in names:
                        names.append(n)
            variables = []
            for n in names:
                values = {}
                for t in themes:
                    c = _color_entry(color[t].get(n))
                    if c:
                        values[t] = c
                if values:
                    variables.append({"name": n, "type": "COLOR", "values": values})
                else:
                    skipped.append(f"color.{n}")
            modes = [default] + [t for t in themes if t != default]
            out.append({"name": COLOR, "modes": modes, "default_mode": default, "variables": variables})
        else:
            variables = []
            for n, entry in color.items():
                c = _color_entry(entry)
                if c:
                    variables.append({"name": n, "type": "COLOR", "values": {VALUE_MODE: c}})
                else:
                    skipped.append(f"color.{n}")
            out.append({"name": COLOR, "modes": [VALUE_MODE], "default_mode": VALUE_MODE, "variables": variables})

    for key, cname in (("spacing", SPACING), ("radius", RADIUS)):
        group = tokens.get(key)
        if not isinstance(group, dict):
            continue
        variables = []
        for k, v in group.items():
            n = _num(v)
            if n is None:
                skipped.append(f"{key}.{k}")
                continue
            variables.append({"name": str(k), "type": "FLOAT", "values": {VALUE_MODE: _fmt_num(n)}})
        if variables:
            out.append({"name": cname, "modes": [VALUE_MODE], "default_mode": VALUE_MODE, "variables": variables})

    typo = tokens.get("typography")
    if isinstance(typo, dict):
        variables = []
        for group, (prefix, vtype, _css) in _TYPO.items():
            entries = typo.get(group)
            if not isinstance(entries, dict):
                continue
            for k, v in entries.items():
                if group == "fontFamily":
                    fam = v.get("family") if isinstance(v, dict) else v
                    if isinstance(fam, str) and fam.strip():
                        variables.append({"name": f"{prefix}/{k}", "type": vtype, "values": {VALUE_MODE: fam.strip()}})
                    else:
                        skipped.append(f"typography.{group}.{k}")
                    continue
                if vtype == "FLOAT":
                    n = _num(v)
                    if n is None:
                        skipped.append(f"typography.{group}.{k}")
                        continue
                    variables.append({"name": f"{prefix}/{k}", "type": vtype, "values": {VALUE_MODE: _fmt_num(n)}})
                elif isinstance(v, (str, int, float)) and str(v).strip():
                    variables.append({"name": f"{prefix}/{k}", "type": vtype, "values": {VALUE_MODE: str(v).strip()}})
                else:
                    skipped.append(f"typography.{group}.{k}")
        if variables:
            out.append({"name": TYPOGRAPHY, "modes": [VALUE_MODE], "default_mode": VALUE_MODE, "variables": variables})

    for c in out:
        count += len(c["variables"])
    if count > MAX_VARIABLES:
        raise TokensError(f"Too many tokens to mirror ({count} > {MAX_VARIABLES})")
    return {"collections": out, "skipped": skipped, "count": count}


# ── Pull: collections read back → tokens.json + diff ─────────────────

def _by_name(read: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for c in (read or {}).get("collections") or []:
        if isinstance(c, dict) and isinstance(c.get("name"), str):
            out.setdefault(c["name"], c)
    return out


def _values(var: Dict[str, Any]) -> Dict[str, Any]:
    vals = var.get("values")
    return vals if isinstance(vals, dict) else {}


def _set_color(entry: Any, hx: str) -> Any:
    """New token entry for colour `hx`, keeping the entry's shape and an unchanged `value`."""
    if isinstance(entry, dict):
        new = dict(entry)
        new["hex"] = hx
        new["value"] = hx
        return new
    return hx


def apply_canvas(tokens: Dict[str, Any], read: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Merge canvas variables into a copy of `tokens`; returns (new_tokens, diff).

    diff items: {"token": dotted path, "css": custom property or None, "theme": str|None,
                 "before": str|None, "after": str, "kind": "changed"|"added"}.
    Tokens with no canvas variable are left untouched (a deleted variable never
    deletes a token), and so is anything that is not a plain value (aliases).
    """
    new = copy.deepcopy(tokens)
    diff: List[Dict[str, Any]] = []
    cols = _by_name(read)

    c = cols.get(COLOR)
    if c:
        themes = _themes(new)
        color = new.setdefault("color", {})
        if not isinstance(color, dict):
            color = new["color"] = {}
        for var in c.get("variables") or []:
            if not isinstance(var, dict) or var.get("type") != "COLOR" or not isinstance(var.get("name"), str):
                continue
            name = var["name"]
            for mode, value in _values(var).items():
                hx = _norm_hex(value)
                if not hx:
                    continue
                if themes:
                    if mode not in themes:
                        continue
                    block = color.setdefault(mode, {})
                    path, theme = f"color.{mode}.{name}", mode
                else:
                    if mode != VALUE_MODE and len(_values(var)) > 1:
                        continue
                    block = color
                    path, theme = f"color.{name}", None
                before = block.get(name)
                old_hex = _norm_hex(before.get("hex") if isinstance(before, dict) else before)
                if before is not None and old_hex == hx:
                    continue
                if before is not None and old_hex is None:
                    # The token is not hex (e.g. an oklch() string with no hex twin): compare
                    # nothing and take the canvas value only if the user changed it there.
                    orig = _color_entry(before)
                    if orig and _norm_hex(orig) == hx:
                        continue
                block[name] = _set_color(before, hx) if before is not None else {"value": hx, "hex": hx}
                diff.append({"token": path, "css": f"--{name}", "theme": theme,
                             "before": _color_entry(before), "after": hx,
                             "kind": "changed" if before is not None else "added"})

    for key, cname, css_prefix in (("spacing", SPACING, "space-"), ("radius", RADIUS, "radius-")):
        col = cols.get(cname)
        if not col:
            continue
        group = new.setdefault(key, {})
        if not isinstance(group, dict):
            continue
        for var in col.get("variables") or []:
            if not isinstance(var, dict) or var.get("type") != "FLOAT":
                continue
            n = _num(_values(var).get(VALUE_MODE, next(iter(_values(var).values()), None)))
            if n is None:
                continue
            name = str(var.get("name"))
            before = group.get(name)
            if before is not None and _num(before) == n:
                continue
            after = f"{_fmt_num(n)}px"
            group[name] = after
            diff.append({"token": f"{key}.{name}", "css": f"--{css_prefix}{name}", "theme": None,
                         "before": None if before is None else str(before), "after": after,
                         "kind": "changed" if before is not None else "added"})

    col = cols.get(TYPOGRAPHY)
    if col:
        typo = new.setdefault("typography", {})
        if isinstance(typo, dict):
            for var in col.get("variables") or []:
                if not isinstance(var, dict) or not isinstance(var.get("name"), str) or "/" not in var["name"]:
                    continue
                prefix, _, k = var["name"].partition("/")
                group = _TYPO_BY_PREFIX.get(prefix)
                if not group or not k:
                    continue
                raw = _values(var).get(VALUE_MODE, next(iter(_values(var).values()), None))
                if raw is None or isinstance(raw, dict):
                    continue
                entries = typo.setdefault(group, {})
                before = entries.get(k)
                _p, vtype, css_prefix = _TYPO[group]
                if group == "fontFamily":
                    fam = str(raw).strip()
                    old = before.get("family") if isinstance(before, dict) else before
                    if not fam or old == fam:
                        continue
                    if isinstance(before, dict):
                        nb = dict(before)
                        nb["family"] = fam
                        stack = nb.get("stack")
                        if isinstance(stack, str) and isinstance(old, str) and old:
                            nb["stack"] = stack.replace(old, fam, 1)
                        entries[k] = nb
                        after_css = nb.get("stack") or fam
                    else:
                        entries[k] = fam
                        after_css = fam
                    diff.append({"token": f"typography.{group}.{k}", "css": f"--{css_prefix}{k}", "theme": None,
                                 "before": old, "after": fam, "css_value": after_css,
                                 "kind": "changed" if before is not None else "added"})
                    continue
                if vtype == "FLOAT":
                    n = _num(raw)
                    if n is None:
                        continue
                    if before is not None and _num(before) == n:
                        continue
                    unit = "px" if group == "fontSize" else ""
                    after = f"{_fmt_num(n)}{unit}"
                else:
                    after = str(raw).strip()
                    if not after or (before is not None and str(before).strip() == after):
                        continue
                entries[k] = after
                diff.append({"token": f"typography.{group}.{k}", "css": f"--{css_prefix}{k}", "theme": None,
                             "before": None if before is None else str(before), "after": after,
                             "kind": "changed" if before is not None else "added"})
    return new, diff


# ── tokens.css rewrite ───────────────────────────────────────────────

def _top_level_blocks(css: str) -> List[Tuple[str, int, int]]:
    """(selector, body_start, body_end) for every top-level rule (not inside @media etc.)."""
    blocks = []
    depth = 0
    i = 0
    sel_start = 0
    body_start = -1
    in_comment = False
    quote = ""
    while i < len(css):
        ch = css[i]
        if in_comment:
            if css.startswith("*/", i):
                in_comment = False
                i += 2
                continue
            i += 1
            continue
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if css.startswith("/*", i):
            in_comment = True
            i += 2
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "{":
            if depth == 0:
                body_start = i + 1
                selector = css[sel_start:i]
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and body_start >= 0:
                blocks.append((re.sub(r"/\*.*?\*/", "", selector, flags=re.S).strip(), body_start, i))
                body_start = -1
                sel_start = i + 1
            depth = max(depth, 0)
        elif ch == ";" and depth == 0:
            sel_start = i + 1
        i += 1
    return blocks


def _selector_theme(selector: str, themes: List[str]) -> Optional[str]:
    for t in themes:
        if re.search(r"""\[data-theme=["']?%s["']?\]""" % re.escape(t), selector) or \
                re.search(r"(^|[\s,])\.%s\b" % re.escape(t), selector):
            return t
    return None


def rewrite_tokens_css(css: str, diff: List[Dict[str, Any]], tokens: Dict[str, Any]) -> Tuple[str, List[str], List[str]]:
    """Carry diff values into tokens.css; returns (css, updated_props, not_found_props).

    Themed colour changes go into the block(s) for their theme; `:root` counts as
    the default theme only when it is combined with that theme's selector (the
    seed layout: `:root, [data-theme="dark"], .dark {…}`). Everything else goes
    into plain top-level `:root` blocks. Nothing is appended: a property that is
    not declared in tokens.css is reported, not invented.
    """
    themes = _themes(tokens)
    edits: List[Tuple[int, int, str]] = []
    updated: List[str] = []
    missing: List[str] = []
    blocks = _top_level_blocks(css)
    for item in diff:
        prop = item.get("css")
        if not prop:
            continue
        value = item.get("css_value") or item["after"]
        theme = item.get("theme")
        themed, plain_root = [], []
        for selector, b0, b1 in blocks:
            if selector.startswith("@"):
                continue
            has_root = bool(re.search(r"(^|[\s,]):root\b", selector))
            sel_theme = _selector_theme(selector, themes)
            if sel_theme is None and has_root:
                plain_root.append((b0, b1))
            elif theme and sel_theme == theme:
                themed.append((b0, b1))
            elif not theme and has_root:
                plain_root.append((b0, b1))
        pattern = re.compile(r"(?<![\w-])(%s\s*:\s*)([^;{}]*?)(\s*(?:;|$))" % re.escape(prop))

        def _hits(spans):
            found = []
            for b0, b1 in spans:
                for m in pattern.finditer(css[b0:b1]):
                    found.append((b0 + m.start(2), b0 + m.end(2), value))
            return found

        default = tokens.get("defaultTheme") if tokens.get("defaultTheme") in themes else (themes[0] if themes else None)
        found = _hits(themed) if theme else _hits(plain_root)
        if theme and not found and theme == default:
            # `:root { … }` alone carries the default theme in many hand-written files.
            found = _hits(plain_root)
        edits.extend(found)
        (updated if found else missing).append(prop if not theme else f"{prop} ({theme})")
    for start, end, value in sorted(edits, key=lambda e: e[0], reverse=True):
        css = css[:start] + value + css[end:]
    return css, updated, missing


# ── Pull, end to end ─────────────────────────────────────────────────

def pull(project_dir: Path, read: Dict[str, Any]) -> Dict[str, Any]:
    """Write canvas variables back into the project's tokens.json (+ tokens.css)."""
    where, tokens = load(project_dir)
    new, diff = apply_canvas(tokens, read)
    result: Dict[str, Any] = {"source": where["json"], "css": where["css"], "staged": where["staged"],
                              "diff": diff, "written": [], "css_updated": [], "css_missing": []}
    if not diff:
        return result
    root = Path(project_dir)
    (root / where["json"]).write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    result["written"].append(where["json"])
    if where["css"]:
        path = root / where["css"]
        css = path.read_text(encoding="utf-8")
        new_css, updated, missing = rewrite_tokens_css(css, diff, new)
        result["css_updated"], result["css_missing"] = updated, missing
        if new_css != css:
            path.write_text(new_css, encoding="utf-8")
            result["written"].append(where["css"])
    return result
