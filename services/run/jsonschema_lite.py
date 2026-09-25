"""A small JSON Schema validator for loop ``schema`` checks (no dependency).

Covers the keywords a check schema realistically uses: type (incl. lists),
enum, const, required, properties, additionalProperties (bool | schema), items,
minItems / maxItems, minLength / maxLength, minimum / maximum, pattern, anyOf,
allOf, oneOf, not. Unknown keywords are ignored (as JSON Schema does).
:func:`validate` returns a list of human-readable problems — empty = valid.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

_TYPES = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and v.is_integer()),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}
MAX_PROBLEMS = 50


def validate(instance: Any, schema: Any, path: str = "$") -> List[str]:
    out: List[str] = []
    _check(instance, schema, path, out)
    return out[:MAX_PROBLEMS]


def _check(v: Any, s: Any, path: str, out: List[str]) -> None:
    if len(out) >= MAX_PROBLEMS or s is True or s is None:
        return
    if s is False:
        out.append(f"{path}: not allowed")
        return
    if not isinstance(s, dict):
        return
    t = s.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES.get(x, lambda _v: True)(v) for x in types):
            out.append(f"{path}: expected {' or '.join(map(str, types))}, got {type(v).__name__}")
            return
    if "const" in s and v != s["const"]:
        out.append(f"{path}: must equal {s['const']!r}")
    if isinstance(s.get("enum"), list) and v not in s["enum"]:
        out.append(f"{path}: must be one of {s['enum']!r}")
    if isinstance(v, str):
        if isinstance(s.get("minLength"), int) and len(v) < s["minLength"]:
            out.append(f"{path}: shorter than {s['minLength']}")
        if isinstance(s.get("maxLength"), int) and len(v) > s["maxLength"]:
            out.append(f"{path}: longer than {s['maxLength']}")
        if isinstance(s.get("pattern"), str):
            try:
                if not re.search(s["pattern"], v):
                    out.append(f"{path}: does not match /{s['pattern']}/")
            except re.error:
                pass
    if _TYPES["number"](v):
        if isinstance(s.get("minimum"), (int, float)) and v < s["minimum"]:
            out.append(f"{path}: below minimum {s['minimum']}")
        if isinstance(s.get("maximum"), (int, float)) and v > s["maximum"]:
            out.append(f"{path}: above maximum {s['maximum']}")
    if isinstance(v, list):
        if isinstance(s.get("minItems"), int) and len(v) < s["minItems"]:
            out.append(f"{path}: fewer than {s['minItems']} items")
        if isinstance(s.get("maxItems"), int) and len(v) > s["maxItems"]:
            out.append(f"{path}: more than {s['maxItems']} items")
        if isinstance(s.get("items"), (dict, bool)):
            for i, x in enumerate(v):
                _check(x, s["items"], f"{path}[{i}]", out)
    if isinstance(v, dict):
        for k in s.get("required") or []:
            if k not in v:
                out.append(f"{path}: missing required property {k!r}")
        props: Dict[str, Any] = s.get("properties") or {}
        for k, sub in props.items():
            if k in v:
                _check(v[k], sub, f"{path}.{k}", out)
        extra = s.get("additionalProperties", True)
        for k in v:
            if k in props:
                continue
            if extra is False:
                out.append(f"{path}: unexpected property {k!r}")
            elif isinstance(extra, dict):
                _check(v[k], extra, f"{path}.{k}", out)
    for sub in s.get("allOf") or []:
        _check(v, sub, path, out)
    if isinstance(s.get("anyOf"), list) and s["anyOf"]:
        if not any(not validate(v, sub, path) for sub in s["anyOf"]):
            out.append(f"{path}: matches none of anyOf")
    if isinstance(s.get("oneOf"), list) and s["oneOf"]:
        n = sum(1 for sub in s["oneOf"] if not validate(v, sub, path))
        if n != 1:
            out.append(f"{path}: matches {n} of oneOf (exactly 1 required)")
    if isinstance(s.get("not"), (dict, bool)) and not validate(v, s["not"], path):
        out.append(f"{path}: must not match the 'not' schema")
