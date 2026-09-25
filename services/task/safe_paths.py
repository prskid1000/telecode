"""Id validation and path containment shared by the agent / job / run stores.

The REST surface has no auth and takes ids and file names straight from the
URL or a multipart filename, so every one of them is validated here before it
is joined onto a data directory (B9).
"""

from __future__ import annotations

import re
from pathlib import Path

# uuid4 ids plus the slug-ish ids older records and tests use. No dots, so
# "." / ".." can never be an id; no separators, so an id is one path segment.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def validate_id(value: str, kind: str = "id") -> str:
    """Return `value` if it is a safe record id, else raise ValueError."""
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ValueError(f"invalid {kind}: {value!r}")
    return value


def resolve_in(base_dir: Path, filename: str) -> Path:
    """Resolve `filename` inside `base_dir`, refusing to escape it.

    Rejects empty names, absolute paths and drive letters outright (on Windows
    "C:/x" and "\\\\host\\share" both survive a naive join), then checks the
    resolved path is still under the resolved base.
    """
    if not filename:
        raise ValueError("filename is required")
    candidate = Path(filename)
    if candidate.is_absolute() or candidate.drive or candidate.anchor:
        raise ValueError(f"unsafe filename: {filename!r}")
    base = base_dir.resolve()
    dest = (base / candidate).resolve()
    if dest != base and base not in dest.parents:
        raise ValueError(f"unsafe filename: {filename!r}")
    return dest
