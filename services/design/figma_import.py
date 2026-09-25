"""Import frames from a Figma link as images + HTML boards (Figma REST API).

The personal access token is the user's, stored in settings.json at
`design.figma.token` (read through `config.get_nested` every time, written with
`config.set_nested`). It is never echoed back to the UI.

    parse_link(url)          → {key, node_id?}
    list_frames(url)         → {file_name, frames:[{id, name, page, width, height}]}
    import_frames(pid, url, ids?) → {files:[…], boards:[…]}

Each imported frame becomes `imports/figma/<slug>.png` (rendered by Figma at 2×)
plus a board page `figma-<slug>.html` that shows it at its design size, so it lands
on the canvas like any other HTML board and the agent can rebuild it. The image
URLs Figma returns are fetched through `proxy.media_fetch` (the SSRF guard).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import config
from services.design import files as dfiles
from services.design import store

log = logging.getLogger("telecode.services.design.figma_import")

API = "https://api.figma.com/v1"
MAX_FRAMES = 20
_LINK_RE = re.compile(r"^/(?:file|design|proto|board)/([A-Za-z0-9]{10,64})(?:/|$)")
_FRAME_TYPES = {"FRAME", "COMPONENT", "COMPONENT_SET", "SECTION"}


class FigmaError(RuntimeError):
    pass


def token() -> str:
    t = config.get_nested("design.figma.token", "") or ""
    return t if isinstance(t, str) else ""


def set_token(value: Optional[str]) -> None:
    v = (value or "").strip()
    if v and not re.match(r"^[A-Za-z0-9_-]{20,200}$", v):
        raise ValueError("That doesn't look like a Figma personal access token")
    config.set_nested("design.figma.token", v)


def parse_link(url: str) -> Dict[str, Optional[str]]:
    if not isinstance(url, str):
        raise ValueError("a Figma link is required")
    u = urlparse(url.strip())
    if u.scheme != "https" or u.hostname not in ("www.figma.com", "figma.com"):
        raise ValueError("expected a https://www.figma.com/design/… (or /file/…) link")
    m = _LINK_RE.match(u.path)
    if not m:
        raise ValueError("couldn't find a file key in that Figma link")
    node = (parse_qs(u.query).get("node-id") or [None])[0]
    if node:
        node = node.replace("-", ":")
        if not re.match(r"^[0-9]+:[0-9]+$", node):
            node = None
    return {"key": m.group(1), "node_id": node}


async def _get_json(path: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """GET api.figma.com (fixed host) with the user's token."""
    import aiohttp
    tok = token()
    if not tok:
        raise FigmaError("No Figma token yet. Add a personal access token (Figma → Settings → Security).")
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.get(API + path, params=params or {}, headers={"X-Figma-Token": tok}) as r:
            try:
                data = await r.json(content_type=None)
            except Exception:
                data = {}
            if r.status in (401, 403):
                raise FigmaError("Figma refused the token (expired, or no access to this file).")
            if r.status == 404:
                raise FigmaError("Figma couldn't find that file — check the link and that your account can open it.")
            if r.status >= 400:
                raise FigmaError(f"Figma API error {r.status}: {str(data.get('err') or data.get('message') or '')[:200]}")
            return data if isinstance(data, dict) else {}


async def _fetch_bytes(url: str) -> bytes:
    from proxy import media_fetch
    return await media_fetch.fetch_media_bytes(url, max_bytes=32 * 1024 * 1024)


def _frames_from(doc: Dict[str, Any], only: Optional[str] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def box(n: Dict[str, Any]) -> Dict[str, int]:
        b = n.get("absoluteBoundingBox") or {}
        return {"width": max(1, round(float(b.get("width") or 0))), "height": max(1, round(float(b.get("height") or 0)))}

    for page in doc.get("children") or []:
        if not isinstance(page, dict):
            continue
        for n in page.get("children") or []:
            if isinstance(n, dict) and n.get("type") in _FRAME_TYPES and n.get("id"):
                if only and n["id"] != only:
                    continue
                out.append({"id": n["id"], "name": str(n.get("name") or n["id"])[:120],
                            "page": str(page.get("name") or "")[:120], **box(n)})
    return out[:200]


async def list_frames(url: str) -> Dict[str, Any]:
    link = parse_link(url)
    if link["node_id"]:
        data = await _get_json(f"/files/{link['key']}/nodes", {"ids": link["node_id"], "depth": "1"})
        node = ((data.get("nodes") or {}).get(link["node_id"]) or {}).get("document") or {}
        frames = []
        if node.get("type") in _FRAME_TYPES:
            b = node.get("absoluteBoundingBox") or {}
            frames = [{"id": node["id"], "name": str(node.get("name") or node["id"])[:120], "page": "",
                       "width": max(1, round(float(b.get("width") or 0))),
                       "height": max(1, round(float(b.get("height") or 0)))}]
        else:
            frames = _frames_from({"children": [node]})
        return {"key": link["key"], "file_name": data.get("name") or "", "frames": frames}
    data = await _get_json(f"/files/{link['key']}", {"depth": "2"})
    return {"key": link["key"], "file_name": data.get("name") or "",
            "frames": _frames_from(data.get("document") or {})}


def _slug(s: str) -> str:
    return (re.sub(r"[^A-Za-z0-9]+", "-", s or "").strip("-").lower() or "frame")[:48]


def _board_html(name: str, img_rel: str, w: int, h: int) -> str:
    import html
    n = html.escape(name)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width={w}">
<title>{n}</title>
<style>html,body{{margin:0;background:#fff}}img{{display:block;width:{w}px;height:auto}}</style>
</head>
<body data-td-figma="1">
<!-- Imported from Figma as a picture. Ask the agent to rebuild it as real HTML. -->
<img src="{html.escape(img_rel)}" alt="{n}" width="{w}" height="{h}">
</body>
</html>
"""


async def import_frames(pid: str, url: str, ids: Optional[List[str]] = None) -> Dict[str, Any]:
    if not store.get_project(pid):
        raise LookupError("project not found")
    listing = await list_frames(url)
    frames = listing["frames"]
    if ids:
        want = {i for i in ids if isinstance(i, str)}
        frames = [f for f in frames if f["id"] in want]
    frames = frames[:MAX_FRAMES]
    if not frames:
        raise FigmaError("No frames to import (top-level frames, components and sections are imported).")
    imgs = await _get_json(f"/images/{listing['key']}", {"ids": ",".join(f["id"] for f in frames),
                                                         "format": "png", "scale": "2"})
    urls = imgs.get("images") or {}
    written: List[str] = []
    boards: List[str] = []
    used: set = set()
    for f in frames:
        src = urls.get(f["id"])
        if not src:
            log.info("figma: no render for %s", f["id"])
            continue
        data = await _fetch_bytes(src)
        slug = _slug(f["name"])
        base, n = slug, 2
        while slug in used:
            slug, n = f"{base}-{n}", n + 1
        used.add(slug)
        img_rel = f"imports/figma/{slug}.png"
        page_rel = f"figma-{slug}.html"
        if not dfiles.write_file(pid, img_rel, data):
            raise FigmaError(f"couldn't write {img_rel}")
        dfiles.write_file(pid, page_rel, _board_html(f["name"], img_rel, f["width"], f["height"]).encode("utf-8"))
        written += [img_rel, page_rel]
        boards.append(page_rel)
    if not boards:
        raise FigmaError("Figma returned no images for those frames.")
    return {"file_name": listing["file_name"], "files": written, "boards": boards}
