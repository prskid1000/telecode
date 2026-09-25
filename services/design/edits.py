"""Model-free write-back into HTML-board sources (docs/teledesign-contract.md §4.1 tweaks, §4.4 edits).

- `apply_tweaks(src, edits)` merges keys into the one `/*EDITMODE-BEGIN*/{…}/*EDITMODE-END*/`
  JSON block (prompts/tweaks.md).
- `apply_edit(src, file, …)` changes one element located by `source_loc`
  (`data-td-src="file:line:col"`, stamped by the Babel plugin) or by a unique
  literal `data-td-id` — its text, its inline style, or an allowlisted attribute.

Anything the parser cannot pin down exactly raises `Ambiguous`; the route
answers `{"ok": false, "route": "agent"}` and the client sends the change as a
comment turn instead. Never a best guess into someone's source.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Dict, Optional, Tuple


class Ambiguous(Exception):
    pass


class EditError(ValueError):
    pass


# ── Tweaks ───────────────────────────────────────────────────────────────

_EDITMODE_RE = re.compile(r"/\*EDITMODE-BEGIN\*/(.*?)/\*EDITMODE-END\*/", re.S)
MAX_TWEAK_KEYS = 200
MAX_TWEAK_JSON = 64 * 1024


def apply_tweaks(src: str, edits: Dict[str, Any]) -> str:
    if not isinstance(edits, dict) or not edits or len(edits) > MAX_TWEAK_KEYS:
        raise EditError("edits must be a non-empty object")
    for k in edits:
        if not isinstance(k, str) or not re.match(r"^[A-Za-z_$][A-Za-z0-9_$.-]{0,63}$", k):
            raise EditError(f"invalid tweak key {k!r}")
    if len(json.dumps(edits)) > MAX_TWEAK_JSON:
        raise EditError("edits too large")
    blocks = list(_EDITMODE_RE.finditer(src))
    if not blocks:
        raise EditError("no /*EDITMODE-BEGIN*/…/*EDITMODE-END*/ block in this file")
    if len(blocks) > 1:
        raise EditError("more than one EDITMODE block in this file")
    m = blocks[0]
    try:
        current = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise EditError(f"EDITMODE block is not valid JSON: {exc}")
    if not isinstance(current, dict):
        raise EditError("EDITMODE block is not a JSON object")
    current.update(edits)
    # Keep the block's own indentation style: one key per line when it was multi-line.
    multiline = "\n" in m.group(1)
    body = json.dumps(current, indent=2 if multiline else None, ensure_ascii=False)
    if multiline:
        indent = re.match(r"[ \t]*", src[src.rfind("\n", 0, m.start()) + 1:m.start()]).group(0)
        body = body.replace("\n", "\n" + indent)
    body = body.replace("*/", "*\\/")  # never close the marker comment early
    return src[:m.start(1)] + body + src[m.end(1):]


# ── Element location ─────────────────────────────────────────────────────

STYLE_ALLOW = {
    "color", "background", "background-color", "font-size", "font-weight", "font-family",
    "letter-spacing", "line-height", "text-align", "padding", "padding-top", "padding-right",
    "padding-bottom", "padding-left", "margin", "margin-top", "margin-right", "margin-bottom",
    "margin-left", "gap", "border-radius", "width", "height", "opacity", "box-shadow", "transform",
}
ATTR_ALLOW = {"alt", "title", "placeholder", "aria-label", "href", "src"}
_BAD_VALUE_RE = re.compile(r"[;{}<>\"\\`]|url\s*\(|expression\s*\(|javascript:|/\*|\*/", re.I)
_TAG_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9.:_-]*")


def _camel(prop: str) -> str:
    return re.sub(r"-([a-z])", lambda m: m.group(1).upper(), prop)


def _kebab(prop: str) -> str:
    return re.sub(r"[A-Z]", lambda m: "-" + m.group(0).lower(), prop)


def parse_open_tag(src: str, start: int, jsx: bool) -> Tuple[str, int, bool]:
    """At `src[start] == '<'`: (tag name, index just past the tag's '>', self-closing)."""
    if start < 0 or start >= len(src) or src[start] != "<":
        raise Ambiguous("location is not the start of a tag")
    m = _TAG_NAME_RE.match(src, start + 1)
    if not m:
        raise Ambiguous("not an element tag")
    i, depth, quote = m.end(), 0, None
    while i < len(src):
        c = src[i]
        if quote:
            if c == "\\" and jsx:
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'" or (c == "`" and depth):
            quote = c
        elif jsx and c == "{":
            depth += 1
        elif jsx and c == "}":
            depth -= 1
        elif c == ">" and depth == 0:
            return m.group(0), i + 1, src[i - 1] == "/"
        i += 1
    raise Ambiguous("unterminated tag")


def _offset_of(src: str, line: int, col: int) -> int:
    lines = src.split("\n")
    if line < 1 or line > len(lines):
        raise Ambiguous("source_loc line out of range")
    off = sum(len(ln) + 1 for ln in lines[:line - 1])
    for c in (col, col - 1):  # Babel columns are 0-based; accept 1-based too
        if 0 <= c < len(lines[line - 1]) and lines[line - 1][c] == "<":
            return off + c
    raise Ambiguous("source_loc does not point at a tag")


def _parse_loc(loc: str, file: str) -> Tuple[int, int]:
    m = re.match(r"^(.*):(\d+):(\d+)$", loc or "")
    if not m:
        raise EditError("source_loc must be file:line:col")
    if m.group(1).lstrip("./") != file.lstrip("./") and not file.endswith("/" + m.group(1).lstrip("./")):
        raise Ambiguous("source_loc names a different file")
    return int(m.group(2)), int(m.group(3))


def _td_id_re(td_id: str) -> "re.Pattern[str]":
    return re.compile(r"""data-td-id\s*=\s*(?:"%s"|'%s'|\{\s*["']%s["']\s*\})""" % ((re.escape(td_id),) * 3))


def locate(src: str, file: str, jsx: bool, source_loc: Optional[str], td_id: Optional[str]) -> int:
    if source_loc:
        start = _offset_of(src, *_parse_loc(source_loc, file))
        if td_id:
            _, end, _ = parse_open_tag(src, start, jsx)
            if not _td_id_re(td_id).search(src[start:end]):
                raise Ambiguous("td_id and source_loc disagree")
        return start
    if not td_id or not re.match(r"^[A-Za-z0-9_:.-]{1,120}$", td_id):
        raise Ambiguous("need a source_loc or a literal data-td-id")
    hits = list(_td_id_re(td_id).finditer(src))
    if len(hits) != 1:
        raise Ambiguous(f"data-td-id {td_id!r} occurs {len(hits)} times")
    start = src.rfind("<", 0, hits[0].start())
    _, end, _ = parse_open_tag(src, start, jsx)
    if not (start < hits[0].start() < end):
        raise Ambiguous("data-td-id is not inside an opening tag")
    return start


# ── Operations ───────────────────────────────────────────────────────────

def _set_text(src: str, start: int, jsx: bool, value: Any) -> str:
    if not isinstance(value, str) or len(value) > 20000:
        raise EditError("text value must be a string")
    tag, end, self_closing = parse_open_tag(src, start, jsx)
    if self_closing:
        raise Ambiguous("element has no text child")
    close = src.find("<", end)
    if close < 0 or not re.match(r"</\s*%s\s*>" % re.escape(tag), src[close:]):
        raise Ambiguous("element has child elements — not a plain text node")
    inner = src[end:close]
    if jsx and ("{" in inner or "}" in inner):
        raise Ambiguous("text contains a JSX expression")
    if jsx:
        if re.search(r"[{}<>]", value):
            raise Ambiguous("new text needs JSX escaping")
        new = value
    else:
        new = html.escape(value, quote=False)
    lead = re.match(r"\s*", inner).group(0)
    trail = inner[len(inner.rstrip()):] if inner.strip() else ""
    return src[:end] + lead + new + trail + src[close:]


def _clean_style(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise EditError("style value must be an object")
    out = {}
    for k, v in value.items():
        prop = _kebab(str(k)).lower()
        if prop not in STYLE_ALLOW:
            raise EditError(f"style property {k!r} is not editable")
        v = str(v).strip() if isinstance(v, (str, int, float)) else None
        if not v or len(v) > 200 or _BAD_VALUE_RE.search(v):
            raise EditError(f"invalid value for {k!r}")
        out[prop] = v
    return out


_HTML_STYLE_RE = re.compile(r"""(\sstyle\s*=\s*)(["'])(.*?)\2""", re.S | re.I)
_JSX_STYLE_RE = re.compile(r"""(\sstyle\s*=\s*)\{\{(.*?)\}\}""", re.S)
_JSX_PAIR_RE = re.compile(r"""\s*([A-Za-z_$][\w$]*|["'][\w-]+["'])\s*:\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|-?\d+(?:\.\d+)?)\s*""")


def _set_style(src: str, start: int, jsx: bool, value: Any) -> str:
    style = _clean_style(value)
    _, end, self_closing = parse_open_tag(src, start, jsx)
    tag_src = src[start:end]
    if jsx:
        m = _JSX_STYLE_RE.search(tag_src)
        pairs: Dict[str, str] = {}
        if m:
            body = m.group(2).strip().rstrip(",")
            for part in [p for p in body.split(",") if p.strip()] if body else []:
                pm = _JSX_PAIR_RE.fullmatch(part)
                if not pm:
                    raise Ambiguous("style object is not a plain literal")
                pairs[pm.group(1).strip("\"'")] = pm.group(2)
        elif re.search(r"\sstyle\s*=", tag_src):
            raise Ambiguous("style is an expression, not a literal object")
        for prop, v in style.items():
            pairs[_camel(prop)] = json.dumps(v)
        rendered = "{{ " + ", ".join(f"{k}: {v}" for k, v in pairs.items()) + " }}"
        if m:
            new_tag = tag_src[:m.start()] + m.group(1) + rendered + tag_src[m.end():]
        else:
            new_tag = _insert_attr(tag_src, f"style={rendered}", self_closing)
    else:
        m = _HTML_STYLE_RE.search(tag_src)
        decls: Dict[str, str] = {}
        if m:
            for d in html.unescape(m.group(3)).split(";"):
                if ":" in d:
                    k, v = d.split(":", 1)
                    if k.strip():
                        decls[k.strip().lower()] = v.strip()
        decls.update(style)
        rendered = html.escape("; ".join(f"{k}: {v}" for k, v in decls.items()), quote=True)
        if m:
            new_tag = tag_src[:m.start()] + m.group(1) + f'"{rendered}"' + tag_src[m.end():]
        else:
            new_tag = _insert_attr(tag_src, f'style="{rendered}"', self_closing)
    return src[:start] + new_tag + src[end:]


def _insert_attr(tag_src: str, attr: str, self_closing: bool) -> str:
    cut = len(tag_src) - (2 if self_closing else 1)
    head = tag_src[:cut].rstrip()
    return head + " " + attr + (" />" if self_closing else ">")


def _set_attr(src: str, start: int, jsx: bool, value: Any) -> str:
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        raise EditError("attr value must be {name, value}")
    name = value["name"].lower()
    if name not in ATTR_ALLOW:
        raise EditError(f"attribute {name!r} is not editable")
    v = value.get("value")
    if not isinstance(v, str) or len(v) > 2000:
        raise EditError("attribute value must be a string")
    if name in ("href", "src") and re.match(r"^\s*(javascript|vbscript|data):", v, re.I):
        raise EditError("unsafe URL scheme")
    if jsx and re.search(r"[{}]", v):
        raise Ambiguous("value needs JSX escaping")
    _, end, self_closing = parse_open_tag(src, start, jsx)
    tag_src = src[start:end]
    jsx_name = {"aria-label": "aria-label"}.get(name, name)
    pat = re.compile(r"""(\s%s\s*=\s*)(?:"[^"]*"|'[^']*'|\{[^{}]*\})""" % re.escape(jsx_name), re.I)
    literal = f'"{html.escape(v, quote=True)}"'
    hits = list(pat.finditer(tag_src))
    if len(hits) > 1:
        raise Ambiguous("attribute appears twice")
    if hits and jsx and tag_src[hits[0].end(1)] == "{":
        raise Ambiguous("attribute is an expression")
    if hits:
        h = hits[0]
        new_tag = tag_src[:h.start()] + h.group(1) + literal + tag_src[h.end():]
    else:
        new_tag = _insert_attr(tag_src, f"{jsx_name}={literal}", self_closing)
    return src[:start] + new_tag + src[end:]


OPS = {"text": _set_text, "style": _set_style, "attr": _set_attr}


def apply_edit(src: str, file: str, op: str, value: Any, *, source_loc: Optional[str] = None,
               td_id: Optional[str] = None) -> str:
    if op not in OPS:
        raise EditError("op must be text, style or attr")
    jsx = file.lower().endswith((".jsx", ".tsx", ".js"))
    if not jsx and source_loc:
        # Babel stamps loc inside the script, not the HTML file — only trust it for JSX sources.
        if not td_id:
            raise Ambiguous("source_loc in an HTML file")
        source_loc = None
    start = locate(src, file, jsx, source_loc, td_id)
    return OPS[op](src, start, jsx, value)
