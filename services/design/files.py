"""Project files: list / read / write / delete / uploads (docs/teledesign-contract.md §4.1).

Every caller-supplied path goes through `store.resolve_in`, so a path can
neither climb out of the project nor follow a link the agent planted.
Host-owned areas (`.versions/`, `.td/`, `chats/`) are hidden from listings
and refuse writes; `_ds/` is listed (the agent reads it) but is read-only.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import unquote

from services.design import store

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_UPLOADS_PER_REQUEST = 50
MAX_LISTED_FILES = 5000

# Top-level names never listed or snapshot.
# `agents/` holds W7 parallel-run state (services/design/parallel.py), not design files.
HIDDEN_TOP = {".versions", ".td", "chats", "agents"}
# Top-level names listed but never written through the API.
READONLY_TOP = HIDDEN_TOP | {"_ds"}
# Files the host owns at the project root (own routes, not raw writes).
HOST_FILES = {"comments.json"}

_KIND_BY_EXT = {
    ".html": "html", ".htm": "html",
    ".jsx": "jsx", ".tsx": "jsx",
    ".js": "js", ".mjs": "js", ".ts": "js",
    ".css": "css",
    ".json": "json",
    ".md": "markdown", ".txt": "text",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
    ".svg": "image", ".avif": "image", ".ico": "image",
    ".woff": "font", ".woff2": "font", ".ttf": "font", ".otf": "font",
    ".mp4": "video", ".webm": "video", ".mov": "video",
    ".mp3": "audio", ".wav": "audio", ".ogg": "audio",
    ".pdf": "pdf", ".pptx": "pptx",
    ".pen": "pen", ".fig": "fig", ".napkin": "napkin",
    ".zip": "archive",
}


def kind_of(path: str) -> str:
    return _KIND_BY_EXT.get(os.path.splitext(path)[1].lower(), "other")


def iter_project_files(root: Path, include_readonly: bool = True,
                       limit: int = MAX_LISTED_FILES) -> Iterator[Tuple[str, Path]]:
    """Yield (relpath, path) for every regular file a user or agent authored.

    Skips host areas, temp files and anything whose name fails safe_relpath
    (the API could never address it anyway). Does not follow directory links.
    """
    count = 0
    root_s = str(root)
    for dirpath, dirnames, filenames in os.walk(root_s, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root_s).replace("\\", "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        if not rel_dir:
            dirnames[:] = [d for d in dirnames
                           if d not in HIDDEN_TOP and (include_readonly or d not in READONLY_TOP)
                           and not d.startswith(".")]
        else:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        dirnames.sort()
        for fn in sorted(filenames):
            if fn.endswith(".tmp") or fn.startswith(".td-"):
                continue
            rel = f"{rel_dir}/{fn}" if rel_dir else fn
            if not store.safe_relpath(rel):
                continue
            p = Path(dirpath) / fn
            if p.is_symlink() or not p.is_file():
                continue
            yield rel, p
            count += 1
            if count >= limit:
                return


def list_files(pid: str, prefix: str = "") -> Optional[List[Dict[str, Any]]]:
    root = store.project_dir(pid)
    if not root:
        return None
    prefix = (prefix or "").strip("/")
    out = []
    for rel, p in iter_project_files(root):
        if prefix and not (rel == prefix or rel.startswith(prefix + "/")):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        out.append({"path": rel, "size": st.st_size, "mtime": int(st.st_mtime), "kind": kind_of(rel)})
    return out


def _top(rel: str) -> str:
    return rel.split("/", 1)[0]


def resolve(pid: str, rel: str) -> Optional[Path]:
    root = store.project_dir(pid)
    if not root:
        return None
    return store.resolve_in(root, rel)


def read_path(pid: str, rel: str) -> Optional[Path]:
    if _top(rel) in HIDDEN_TOP:
        return None
    p = resolve(pid, rel)
    return p if p and p.is_file() else None


def writable(rel: str) -> bool:
    return store.safe_relpath(rel) and _top(rel) not in READONLY_TOP and rel not in HOST_FILES


def write_file(pid: str, rel: str, data: bytes) -> bool:
    if not writable(rel) or len(data) > MAX_FILE_BYTES:
        return False
    p = resolve(pid, rel)
    if not p or p.is_dir():
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".td-{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)
    return True


def delete_file(pid: str, rel: str) -> bool:
    if not writable(rel):
        return False
    p = resolve(pid, rel)
    if not p or not p.is_file():
        return False
    p.unlink()
    # Drop now-empty parent folders up to the project root.
    root = store.project_dir(pid)
    parent = p.parent
    while root and parent != root and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent
    return True


_UNSAFE_UPLOAD_CHARS = re.compile(r"[^A-Za-z0-9 ._()+-]")


def upload_name(pid: str, filename: str) -> Optional[str]:
    """A fresh, safe `uploads/<name>` for an uploaded file (never overwrites)."""
    root = store.project_dir(pid)
    if not root:
        return None
    base = os.path.basename(unquote(filename or "").replace("\\", "/")).strip()
    base = _UNSAFE_UPLOAD_CHARS.sub("_", base).strip(" .") or f"upload-{int(time.time())}"
    stem, ext = os.path.splitext(base)
    stem, ext = stem[:80] or "upload", ext[:16]
    candidate = f"{stem}{ext}"
    n = 1
    while (root / "uploads" / candidate).exists():
        n += 1
        candidate = f"{stem}-{n}{ext}"
    rel = f"uploads/{candidate}"
    return rel if store.safe_relpath(rel) else None
