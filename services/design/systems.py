"""TeleDesign design systems: prompt context, staging, styles, lint, bundle, zip import/export.

Storage is `store.py`'s (W1): `data/design/systems/<id>.json` + `systems/<id>/` (package layout in
`seeds/README.md`). This module adds everything a package *does*:

    prompt_context(system_id) -> str        the §3 block of prompts/README.md
    stage(system_id, project_dir) -> Path   selective copy into <project>/_ds/<slug>/
    get_style(style_id=None)                style archetypes from seeds/styles/styles.json
    lint(project_dir, system_id) -> [..]    adherence findings (lint.py)
    build_bundle(system_id)                 components/**/*.jsx -> bundle.js (ds_bundle.py)
    export_zip / import_zip                 our zips and Claude-Design `_ds/` folder zips
    sync_from_project(system_id, pid)       copy an edited package back from its design-system project

Record extras this module keeps on the system record (store's `update_system` only patches its own
keys, so they are written here under the store lock): `slug`, `project_id` (the design_system
project that edits it), `needs_cleanup` (imported, not yet normalised), `import_format`, `bundle`
(last build summary), `published_at`.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.design import ds_bundle, lint as lint_mod, store

log = logging.getLogger("telecode.services.design.systems")

STYLES_PATH = Path(__file__).parent / "seeds" / "styles" / "styles.json"
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_ZIP_BYTES = 64 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000
STAGE_EXTRA_CAP = 20            # "copy DS assets selectively (no bulk >20 files)"
DESIGN_MD_INLINE_CAP = 20000     # longer DESIGN.md is summarised in the prompt, full text on disk

# Always staged when present.
STAGE_CORE = ("USAGE.md", "DESIGN.md", "SKILL.md", "tokens.css", "tokens.json", "styles.css",
              "components.css", "manifest.json", "adherence.json", ds_bundle.BUNDLE_NAME)

# Project-internal paths never copied back into a system from its design-system project.
_PROJECT_ONLY = {".versions", ".td", "chats", "uploads", "_ds", "scraps", "imports", "doc.fig", "docs",
                 "boards.json", "comments.json", "assets.json", "thumbnail.webp", "node_modules", ".git"}

_FONT_EXT = {".woff", ".woff2", ".ttf", ".otf", ".eot"}
_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".avif"}
_SEG_RE = re.compile(r"^[A-Za-z0-9 ._-]{1,120}$")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Records & paths ───────────────────────────────────────────────────────

def systems_root() -> Path:
    return store.base_dir() / "systems"


def system_dir(system_id: str) -> Optional[Path]:
    if not store.valid_id(system_id or "") or not store.get_system(system_id):
        return None
    d = systems_root() / system_id
    return d if d.is_dir() else None


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:48]
    return s or "system"


def slug_of(rec: Dict[str, Any]) -> str:
    s = rec.get("slug")
    if isinstance(s, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", s):
        return s
    return slugify(rec.get("name") or "")


def patch_record(system_id: str, extra: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Merge keys store.update_system does not handle into the record (under the store lock)."""
    with store._lock:
        rec = store.get_system(system_id)
        if not rec:
            return None
        rec.update(extra)
        rec["updated_at"] = _now()
        store._write_json(systems_root() / f"{system_id}.json", rec)
        return rec


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _manifest(d: Path) -> Dict[str, Any]:
    m = _read_json_file(d / "manifest.json")
    return m if isinstance(m, dict) else {}


def _read_text(p: Path, limit: int = 2 * 1024 * 1024) -> str:
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


# ── Files ─────────────────────────────────────────────────────────────────

def file_kind(rel: str) -> str:
    low = rel.lower()
    name = low.rsplit("/", 1)[-1]
    ext = os.path.splitext(name)[1]
    if name.endswith(".card.html"):
        return "guideline-card" if low.startswith("guidelines/") else "card"
    if low.startswith("ui_kits/"):
        return "ui-kit"
    if name == ds_bundle.BUNDLE_NAME or name == "_ds_bundle.js":
        return "bundle"
    if name in ("tokens.css", "tokens.json") or low.startswith("tokens/"):
        return "tokens"
    if name in ("manifest.json", "system.json", "adherence.json"):
        return name.split(".")[0]
    if ext == ".pen":
        return "library"
    if ext in (".jsx", ".tsx") and low.startswith("components/"):
        return "component"
    if ext == ".css":
        return "styles"
    if ext == ".md":
        return "doc"
    if ext in _FONT_EXT:
        return "font"
    if ext in _IMG_EXT:
        return "asset"
    return "other"


def list_files(system_id: str) -> Optional[List[Dict[str, Any]]]:
    d = system_dir(system_id)
    if not d:
        return None
    out = []
    for p in sorted(d.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        rel = p.relative_to(d).as_posix()
        if any(part.startswith(".") for part in rel.split("/")) or rel.endswith(".tmp"):
            continue
        st = p.stat()
        out.append({"path": rel, "size": st.st_size, "mtime": int(st.st_mtime), "kind": file_kind(rel)})
    return out


def resolve_file(system_id: str, rel: str) -> Optional[Path]:
    d = system_dir(system_id)
    if not d or not store.safe_relpath(rel):
        return None
    p = d / rel
    return p if p.is_file() and not p.is_symlink() else None


def write_file(system_id: str, rel: str, data: bytes) -> bool:
    d = system_dir(system_id)
    if not d or not store.safe_relpath(rel) or len(data) > MAX_FILE_BYTES:
        return False
    if rel in ("bundle.js",):
        return False  # generated; rebuild via POST …/bundle
    target = d / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    patch_record(system_id, {})
    return True


def delete_file(system_id: str, rel: str) -> bool:
    p = resolve_file(system_id, rel)
    if not p:
        return False
    p.unlink()
    patch_record(system_id, {})
    return True


# ── Styles ────────────────────────────────────────────────────────────────

def get_style(style_id: Optional[str] = None) -> Any:
    """All style archetypes (list) or one (dict / None). Same data `design_get_style` exposes."""
    data = _read_json_file(STYLES_PATH) or {}
    styles = data.get("styles") or []
    if style_id is None:
        return styles
    return next((s for s in styles if s.get("id") == style_id), None)


def styles_document() -> Dict[str, Any]:
    return _read_json_file(STYLES_PATH) or {"styles": []}


# ── Prompt context ────────────────────────────────────────────────────────

def _truncate_design_md(text: str, ds_path: str) -> str:
    if len(text) <= DESIGN_MD_INLINE_CAP:
        return text.strip()
    out, para_taken, in_para = [], False, False
    for line in text.splitlines():
        if line.startswith("#"):
            out.append(line)
            para_taken, in_para = False, False
        elif not para_taken:
            if line.strip():
                out.append(line)
                in_para = True
            elif in_para:
                out.append("")
                para_taken = True
    out.append(f"\n(full text: {ds_path}/DESIGN.md — read it before designing)")
    return "\n".join(out).strip()


BRAND_FONT_STATUS = ("provided", "substituted", "missing")


def brand_fonts(man: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The manifest's `brandFonts`, normalised to
    [{family, status: provided|substituted|missing, substitute?, tokens: [css var…], note?}].

    Accepts the list form, and the Claude-Design object form
    ``{"status": …, "tokens": {"--font-display": "Newsreader", …}}`` / ``{family: {status, …}}``.
    It records what happened to the brand's own typefaces — shipped with the
    system, swapped for a stand-in (and which), or unavailable — so the agent and
    the fonts view can say so instead of silently using a fallback.
    """
    raw = man.get("brandFonts") if isinstance(man, dict) else None
    items: List[Dict[str, Any]] = []
    if isinstance(raw, dict):
        if "tokens" in raw or "status" in raw:
            toks = raw.get("tokens") or {}
            by_family: Dict[str, List[str]] = {}
            if isinstance(toks, dict):
                for tok, fam in toks.items():
                    if isinstance(fam, str):
                        by_family.setdefault(fam.split(",")[0].strip().strip("'\""), []).append(str(tok))
            for fam, tlist in (by_family or {"": []}).items():
                items.append({"family": fam, "status": raw.get("status"), "tokens": tlist,
                              "substitute": raw.get("substitute"), "note": raw.get("note")})
        else:
            for fam, v in raw.items():
                if isinstance(v, dict):
                    items.append({"family": fam, **v})
                elif isinstance(v, str):
                    items.append({"family": fam, "status": v})
    elif isinstance(raw, list):
        items = [x for x in raw if isinstance(x, dict)]
    out: List[Dict[str, Any]] = []
    for it in items[:40]:
        fam = str(it.get("family") or "").strip()[:120]
        st = str(it.get("status") or "").lower()
        st = st if st in BRAND_FONT_STATUS else ("substituted" if it.get("substitute") else "provided")
        toks = it.get("tokens")
        toks = [str(t)[:80] for t in toks][:20] if isinstance(toks, list) else             ([str(t)[:80] for t in toks.keys()][:20] if isinstance(toks, dict) else [])
        rec = {"family": fam, "status": st, "tokens": toks}
        if it.get("substitute"):
            rec["substitute"] = str(it["substitute"])[:120]
        if it.get("note"):
            rec["note"] = str(it["note"])[:400]
        if fam or toks:
            out.append(rec)
    return out


def compact_manifest(man: Dict[str, Any]) -> str:
    comps = []
    for c in man.get("components") or []:
        if not isinstance(c, dict):
            continue
        props = {}
        for k, v in (c.get("props") or {}).items():
            if isinstance(v, dict):
                if v.get("type") == "enum":
                    props[k] = "|".join(str(x) for x in v.get("values") or [])
                else:
                    props[k] = v.get("type") or "any"
            else:
                props[k] = v
        comps.append({"name": c.get("name"), "group": c.get("group"), "path": c.get("path") or c.get("sourcePath"),
                      "global": c.get("global"), "props": props})
    out = {
        "name": man.get("name"), "namespace": man.get("namespace"), "version": man.get("version"),
        "runtime": man.get("runtime"), "components": comps,
        "cards": [{"path": c.get("path"), "group": c.get("group")} for c in man.get("cards") or [] if isinstance(c, dict)],
        "fonts": [{k: v for k, v in {"family": f.get("family"), "weights": f.get("weights"),
                                     "style": f.get("style"), "css": f.get("css") or f.get("cssPath"),
                                     "files": f.get("files"), "remoteSrc": f.get("remoteSrc")}.items()
                   if v not in (None, [], "")}
                  for f in man.get("fonts") or [] if isinstance(f, dict)],
        "brandFonts": brand_fonts(man),
        "fontsCss": man.get("fontsCss"),
        "icons": man.get("icons"),
        "themes": [t.get("id") if isinstance(t, dict) else t for t in man.get("themes") or []],
        "startingPoints": man.get("startingPoints"),
    }
    return json.dumps({k: v for k, v in out.items() if v not in (None, [], {})}, ensure_ascii=False, indent=1)


def _staged_listing(system_id: str) -> Tuple[List[Tuple[str, str, int]], int]:
    d = system_dir(system_id)
    if not d:
        return [], 0
    plan = _stage_plan(d)
    rows = []
    for rel in plan:
        p = d / rel
        rows.append((rel, file_kind(rel), p.stat().st_size if p.exists() else 0))
    total = sum(1 for f in list_files(system_id) or [])
    return rows, total


def prompt_context(system_id: str) -> str:
    """The design-system block for layer 5 of the prompt stack (prompts/README.md §3)."""
    rec = store.get_system(system_id or "")
    d = system_dir(system_id or "")
    if not rec or not d:
        return ""
    slug = slug_of(rec)
    ds_path = f"_ds/{slug}"
    usage = _read_text(d / "USAGE.md").strip() or _read_text(d / "SKILL.md").strip() or \
        "(no USAGE.md — read DESIGN.md and use tokens.css custom properties only)"
    design = _truncate_design_md(_read_text(d / "DESIGN.md"), ds_path) or "(no DESIGN.md)"
    tokens = _read_text(d / "tokens.css").strip()
    if not tokens:
        tokens = "\n".join(_read_text(p) for p in sorted((d / "tokens").glob("*.css"))).strip() or "(no tokens.css)"
    man = _manifest(d)
    rows, total = _staged_listing(system_id)
    idx = [f"{ds_path}/{rel} · {kind} · {size}" for rel, kind, size in rows]
    st = ds_bundle.status(d)
    if st.get("sources"):
        ns = ds_bundle.namespace_for(d, slug)
        idx.append(f"(bundle: {ds_path}/{ds_bundle.BUNDLE_NAME} — one <script src> after React 18 UMD exposes "
                   f"every component on window and as window.{ns}.*; no text/babel needed)")
    if total > len(rows):
        idx.append(f"({total - len(rows)} more files in the source system — specimen cards, guidelines, "
                   f"library — not copied; ask via design_get_system if you need one)")
    parts = [
        f"## Design system: {rec.get('name', slug)}  (read-only copy at {ds_path})",
        "", "### How to use it", usage,
        "", "### DESIGN.md", design,
        "", "### Tokens (tokens.css)", "```css", tokens, "```",
        "", "### Manifest (manifest.json)", "```json", compact_manifest(man) if man else "{}", "```",
        "", "### Files you can copy from", "\n".join(idx) if idx else "(none)",
    ]
    return "\n".join(parts).strip() + "\n"


def bundle_rel(system_id: str) -> str:
    """Project-relative path of the staged bundle (`{{ds_bundle}}`), or '' when there is none."""
    rec = store.get_system(system_id or "")
    d = system_dir(system_id or "")
    if not rec or not d or not ds_bundle.component_sources(d):
        return ""
    return f"_ds/{slug_of(rec)}/{ds_bundle.BUNDLE_NAME}"


# ── Stage ─────────────────────────────────────────────────────────────────

def _css_urls(css: str) -> List[str]:
    return [m.group(1) for m in re.finditer(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", css)
            if not re.match(r"^(data:|https?:|#)", m.group(1))]


def _stage_plan(d: Path) -> List[str]:
    """Relative paths to copy: core files, then ≤ STAGE_EXTRA_CAP extras by priority."""
    plan: List[str] = [f for f in STAGE_CORE if (d / f).is_file()]
    plan += [p.relative_to(d).as_posix() for p in sorted((d / "tokens").glob("*.css"))] if (d / "tokens").is_dir() else []
    extras: List[str] = []
    # 1. files referenced by url() in the staged CSS (fonts, backgrounds) — without them tokens break
    for rel in [x for x in plan if x.endswith(".css")]:
        base = (d / rel).parent
        for u in _css_urls(_read_text(d / rel)):
            try:
                target = (base / u.split("?")[0].split("#")[0]).resolve()
                rp = target.relative_to(d.resolve()).as_posix()
            except (ValueError, OSError):
                continue
            if target.is_file() and rp not in plan and rp not in extras:
                extras.append(rp)
    # 2. logos / brand marks
    for p in sorted(d.glob("assets/**/*")):
        if p.is_file() and re.search(r"logo|mark|brand|icon", p.name, re.I):
            rp = p.relative_to(d).as_posix()
            if rp not in extras:
                extras.append(rp)
    # 3. component sources (readable reference next to the bundle)
    for rel, _ in ds_bundle.component_sources(d):
        if rel not in extras:
            extras.append(rel)
    return plan + extras[:STAGE_EXTRA_CAP]


def components_index(d: Path) -> List[Dict[str, Any]]:
    man = _manifest(d)
    out = []
    for c in man.get("components") or []:
        if isinstance(c, dict) and c.get("name"):
            out.append({"name": c["name"], "group": c.get("group"), "source": c.get("path") or c.get("sourcePath"),
                        "global": c.get("global") or f"window.{c['name']}", "card": c.get("card"),
                        "props": {k: (v.get("values") if isinstance(v, dict) and v.get("type") == "enum" else
                                      (v.get("type") if isinstance(v, dict) else v))
                                  for k, v in (c.get("props") or {}).items()}})
    if not out:
        for rel, _ in ds_bundle.component_sources(d):
            out.append({"name": Path(rel).stem, "source": rel, "global": f"window.{Path(rel).stem}"})
    return out


def stage(system_id: str, project_dir: Path) -> Path:
    """Copy the system (selectively) into `<project_dir>/_ds/<slug>/`, replacing any previous copy.

    Rebuilds a stale bundle first (never downloads — see ds_bundle.ensure_fresh_sync).
    Writes `components/index.json` and `.staged.json` ({system_id, staged_at, files}).
    """
    rec = store.get_system(system_id or "")
    d = system_dir(system_id or "")
    if not rec or not d:
        raise ValueError("design system not found")
    maybe_sync(system_id)
    slug = slug_of(rec)
    try:
        ds_bundle.ensure_fresh_sync(d, slug, system_id)
    except Exception:
        log.exception("systems: bundle refresh before stage failed")
    root = Path(project_dir) / "_ds"
    dest = root / slug
    tmp = root / f".{slug}.staging"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    files = []
    for rel in _stage_plan(d):
        src = d / rel
        if not src.is_file():
            continue
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, tmp / rel)
        files.append(rel)
    (tmp / "components").mkdir(exist_ok=True)
    (tmp / "components" / "index.json").write_text(json.dumps(components_index(d), indent=1), encoding="utf-8")
    files.append("components/index.json")
    (tmp / ".staged.json").write_text(json.dumps({"system_id": system_id, "slug": slug, "name": rec.get("name"),
                                                  "staged_at": _now(), "files": files}, indent=1), encoding="utf-8")
    # Other systems staged earlier (the project switched systems) go too: one system per project.
    for other in root.iterdir() if root.exists() else []:
        if other.is_dir() and other.name not in (slug, tmp.name):
            shutil.rmtree(other, ignore_errors=True)
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)
    return dest


# ── Lint ──────────────────────────────────────────────────────────────────

def lint_report(project_dir: Path, system_id: Optional[str], files: Optional[List[str]] = None) -> Dict[str, Any]:
    d = system_dir(system_id or "") if system_id else None
    adherence = lint_mod.load_adherence(d) if d else {"rules": {}}
    rep = lint_mod.lint_dir(Path(project_dir), adherence, system_dir=d, files=files)
    rep["system_id"] = system_id if d else None
    rep["derived_rules"] = bool(adherence.get("derived"))
    return rep


def lint(project_dir: Path, system_id: Optional[str]) -> List[Dict[str, Any]]:
    """Contract API: findings only (see lint_report for counts)."""
    return lint_report(project_dir, system_id)["findings"]


def lint_system(system_id: str) -> Optional[Dict[str, Any]]:
    """Self-check a package against its own rules (its tokens.css is allowed raw colours)."""
    d = system_dir(system_id)
    if not d:
        return None
    rep = lint_mod.lint_dir(d, lint_mod.load_adherence(d), system_dir=d, exclude_dirs={"source", "ui_kits"})
    rep["system_id"] = system_id
    return rep


# ── Bundle ────────────────────────────────────────────────────────────────

async def build_bundle(system_id: str) -> Dict[str, Any]:
    rec = store.get_system(system_id or "")
    d = system_dir(system_id or "")
    if not rec or not d:
        raise ValueError("design system not found")
    maybe_sync(system_id)
    res = await ds_bundle.build(d, slug_of(rec), system_id=system_id)
    patch_record(system_id, {"bundle": {"compiler": res["compiler"], "built_at": _now(), "sources": res["sources"],
                                        "namespace": res["namespace"]}})
    return res


def bundle_status(system_id: str) -> Optional[Dict[str, Any]]:
    d = system_dir(system_id or "")
    return ds_bundle.status(d) if d else None


# ── Lifecycle ─────────────────────────────────────────────────────────────

def publish(system_id: str, published: bool = True) -> Optional[Dict[str, Any]]:
    maybe_sync(system_id)
    rec = store.update_system(system_id, {"status": "published" if published else "draft"})
    if rec and published:
        rec = patch_record(system_id, {"published_at": _now()})
    return rec


def set_default(system_id: str) -> Optional[Dict[str, Any]]:
    return store.update_system(system_id, {"is_default": True})


def system_for_project(pid: str) -> Optional[str]:
    """The system a design-system project edits (for W1's post-turn sync), or None."""
    if not store.valid_id(pid or ""):
        return None
    for rec in store.list_systems():
        if rec.get("project_id") == pid:
            return rec["id"]
    return None


def copy_package_into(system_id: str, project_dir: Path) -> int:
    """Full copy of the package into a design-system project's root (the agent edits it in place)."""
    d = system_dir(system_id)
    if not d:
        return 0
    n = 0
    for p in d.rglob("*"):
        if p.is_file() and not p.is_symlink():
            rel = p.relative_to(d)
            if rel.parts[0] in _PROJECT_ONLY:
                continue
            (project_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, project_dir / rel)
            n += 1
    return n


def sync_from_project(system_id: str, pid: Optional[str] = None) -> Dict[str, Any]:
    """Copy package files from the linked design-system project back into the system.

    Only package-shaped paths travel (see _PROJECT_ONLY); files deleted in the project are not
    deleted from the system (an agent that forgot to copy a file must not erase it).
    """
    rec = store.get_system(system_id or "")
    d = system_dir(system_id or "")
    pid = pid or (rec or {}).get("project_id")
    pdir = store.project_dir(pid) if pid else None
    if not rec or not d or not pdir:
        return {"ok": False, "copied": 0}
    copied = 0
    for p in pdir.rglob("*"):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(pdir)
        if rel.parts[0] in _PROJECT_ONLY or rel.parts[0].startswith(".") or p.name.endswith(".tmp"):
            continue
        if not store.safe_relpath(rel.as_posix()) or p.stat().st_size > MAX_FILE_BYTES:
            continue
        target = d / rel
        if target.exists() and target.stat().st_size == p.stat().st_size and target.read_bytes() == p.read_bytes():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        copied += 1
    if copied:
        patch_record(system_id, {"synced_at": _now()})
    return {"ok": True, "copied": copied, "project_id": pid}


def maybe_sync(system_id: str) -> None:
    rec = store.get_system(system_id or "")
    if rec and rec.get("project_id"):
        try:
            sync_from_project(system_id, rec["project_id"])
        except Exception:
            log.exception("systems: sync from project failed")


def create_linked_project(system_id: str, title: str, *, kind: str = "design_system",
                          attach: bool = False, copy_package: bool = True) -> Dict[str, Any]:
    """New project for a system job. DS jobs get the full package at the project root; a Try-it
    project gets the system attached (and staged) like any consumer project."""
    rec = store.get_system(system_id)
    if not rec:
        raise ValueError("design system not found")
    proj = store.create_project({"title": title, "kind": kind,
                                 "design_system_id": system_id if attach else None})
    pdir = store.project_dir(proj["id"])
    if kind == "design_system" and copy_package and pdir:
        copy_package_into(system_id, pdir)
        patch_record(system_id, {"project_id": proj["id"]})
    if attach and pdir:
        stage(system_id, pdir)
    return proj


def cleanup_brief(system_id: str) -> str:
    """What the 'Let Claude clean it up' turn is told: format gaps + its own lint findings."""
    d = system_dir(system_id)
    rec = store.get_system(system_id) or {}
    if not d:
        return ""
    missing = [f for f in ("DESIGN.md", "USAGE.md", "tokens.css", "tokens.json", "manifest.json", "adherence.json",
                           "system.lib.pen") if not (d / f).is_file()]
    cards = [f for f in list_files(system_id) or [] if f["kind"] in ("card", "guideline-card")]
    unmarked = [c["path"] for c in cards if not _read_text(d / c["path"], 400).lstrip().startswith("<!-- @tdCard")]
    rep = lint_system(system_id) or {"findings": []}
    lines = [f"System: {rec.get('name')} (imported from {rec.get('import_format') or 'unknown'}; "
             f"sources: {json.dumps(rec.get('sources') or [])})"]
    lines.append("Missing package files: " + (", ".join(missing) if missing else "none"))
    if unmarked:
        lines.append("Cards without the `<!-- @tdCard group=… -->` first line: " + ", ".join(unmarked[:30]))
    if (d / "source" / "claude-design-manifest.json").exists():
        lines.append("The original Claude-Design manifest is kept at source/claude-design-manifest.json.")
    lines.append("Lint of the package against its own rules:")
    lines.append(lint_mod.format_findings([f for f in rep["findings"] if f["severity"] != "info"], limit=40))
    return "\n".join(lines)


# ── Zip export / import ───────────────────────────────────────────────────

def export_zip(system_id: str) -> Optional[Tuple[str, bytes]]:
    """(filename, bytes). Layout: `<slug>/…` with `system.json` refreshed from the record."""
    rec = store.get_system(system_id or "")
    d = system_dir(system_id or "")
    if not rec or not d:
        return None
    maybe_sync(system_id)
    slug = slug_of(rec)
    meta = _read_json_file(d / "system.json") or {}
    for k in ("name", "description", "status", "version", "default_theme", "tags", "license", "credits"):
        if rec.get(k) is not None:
            meta[k] = rec[k]
    meta["slug"] = slug
    meta["exported_from"] = {"id": system_id, "at": _now(), "app": "TeleDesign"}
    meta.pop("is_default", None)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in list_files(system_id) or []:
            if f["path"] == "system.json":
                continue
            z.write(d / f["path"], f"{slug}/{f['path']}")
        z.writestr(f"{slug}/system.json", json.dumps(meta, indent=2, ensure_ascii=False))
    return f"{slug}.zip", buf.getvalue()


def _safe_member(name: str) -> Optional[List[str]]:
    name = name.replace("\\", "/")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    segs = [s for s in name.split("/") if s]
    if not segs or any(s in (".", "..") for s in segs):
        return None
    if segs[0] == "__MACOSX" or segs[-1] in (".DS_Store", "Thumbs.db", "desktop.ini"):
        return None
    out = []
    for s in segs:
        if not _SEG_RE.match(s):
            s = re.sub(r"[^A-Za-z0-9 ._-]", "_", s)[:120]
        out.append(s)
    return out


_PKG_MARKERS = ("system.json", "manifest.json", "tokens.css", "styles.css", "DESIGN.md", "README.md", "SKILL.md")


def _package_roots(names: List[List[str]]) -> List[Tuple[str, ...]]:
    """Directories (as segment tuples) that look like a design-system package root."""
    dirs: Dict[Tuple[str, ...], set] = {}
    for segs in names:
        dirs.setdefault(tuple(segs[:-1]), set()).add(segs[-1])
    cands = []
    for dpath, files in dirs.items():
        score = sum(1 for m in _PKG_MARKERS if m in files)
        strong = "system.json" in files or "manifest.json" in files or "tokens.css" in files or "styles.css" in files
        if strong and score >= 1:
            cands.append(dpath)
    # Keep only outermost candidates (a package's own sub-dirs are not packages).
    cands.sort(key=len)
    roots: List[Tuple[str, ...]] = []
    for c in cands:
        if not any(c[:len(r)] == r for r in roots):
            roots.append(c)
    if not roots and names:
        common = os.path.commonprefix([s[:-1] for s in names])
        roots = [tuple(common)]
    return roots


def _convert_claude_design(d: Path, man: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a Claude-Design `_ds/` package in place into our layout (originals kept)."""
    notes: List[str] = []
    src_dir = d / "source"
    src_dir.mkdir(exist_ok=True)
    if man:
        (src_dir / "claude-design-manifest.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
    if not (d / "DESIGN.md").exists() and (d / "README.md").exists():
        shutil.copyfile(d / "README.md", d / "DESIGN.md")
        notes.append("DESIGN.md ← README.md")
    if not (d / "USAGE.md").exists() and (d / "SKILL.md").exists():
        shutil.copyfile(d / "SKILL.md", d / "USAGE.md")
        notes.append("USAGE.md ← SKILL.md")
    if not (d / "tokens.css").exists():
        parts = []
        tdir = d / "tokens"
        for p in sorted(tdir.glob("*.css")) if tdir.is_dir() else []:
            parts.append(f"/* ── {p.relative_to(d).as_posix()} ── */\n" + _read_text(p))
        if not parts and (d / "styles.css").exists():
            parts.append(_read_text(d / "styles.css"))
        if parts:
            (d / "tokens.css").write_text("/* Assembled on import from the Claude-Design package. */\n" +
                                          "\n".join(parts), encoding="utf-8")
            notes.append("tokens.css ← tokens/*.css" if tdir.is_dir() else "tokens.css ← styles.css")
    # Manifest superset → our keys (keep theirs alongside).
    comps = []
    for c in man.get("components") or []:
        if isinstance(c, dict) and c.get("name"):
            cc = dict(c)
            cc.setdefault("path", c.get("sourcePath"))
            cc.setdefault("global", f"window.{c['name']}")
            comps.append(cc)
    new_man = dict(man)
    new_man["schema"] = "teledesign-system/v1"
    if comps:
        new_man["components"] = comps
    fonts = []
    for f in man.get("fonts") or []:
        if isinstance(f, dict) and f.get("family"):
            ff = dict(f)
            if f.get("cssPath") and not f.get("css"):
                ff["css"] = f["cssPath"]
            if f.get("weight") and not f.get("weights"):
                ff["weights"] = [f["weight"]]
            fonts.append(ff)
    if fonts:
        new_man["fonts"] = fonts
    bf = brand_fonts(man)
    if bf:
        new_man["brandFonts"] = bf
    if not new_man.get("cards"):
        cards = []
        for p in sorted(d.rglob("*.html")):
            head = _read_text(p, 400)
            m = re.search(r"@(?:tdCard|dsCard|card)\s+group=\"([^\"]+)\"", head)
            if m or p.name.endswith(".card.html"):
                cards.append({"path": p.relative_to(d).as_posix(), "group": m.group(1) if m else "Components"})
        if cards:
            new_man["cards"] = cards
    (d / "manifest.json").write_text(json.dumps(new_man, indent=2, ensure_ascii=False), encoding="utf-8")
    # tokens.json from the manifest's token list, when there is none.
    if not (d / "tokens.json").exists() and isinstance(man.get("tokens"), list):
        tj: Dict[str, Dict[str, Any]] = {}
        for t in man["tokens"]:
            if isinstance(t, dict) and t.get("name"):
                kind = (t.get("kind") or "other").lower()
                tj.setdefault(kind, {})[t["name"].lstrip("-")] = {
                    "value": t.get("value"), "source": t.get("definedIn"), "status": "extracted"}
        if tj:
            (d / "tokens.json").write_text(json.dumps(tj, indent=2, ensure_ascii=False), encoding="utf-8")
            notes.append("tokens.json ← manifest.tokens")
    return {"notes": notes}


def import_zip(data: bytes, filename: str = "system.zip", *, name: Optional[str] = None) -> Dict[str, Any]:
    """Import one or more packages from a zip. Returns {"systems": [record…], "notes": {...}}."""
    if not data or len(data) > MAX_ZIP_BYTES:
        raise ValueError("zip is empty or larger than 64 MB")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("not a zip file") from exc
    infos = [i for i in z.infolist() if not i.is_dir()]
    if len(infos) > MAX_ZIP_ENTRIES:
        raise ValueError(f"zip has more than {MAX_ZIP_ENTRIES} files")
    if sum(i.file_size for i in infos) > MAX_ZIP_BYTES:
        raise ValueError("zip expands beyond 64 MB")
    members: List[Tuple[zipfile.ZipInfo, List[str]]] = []
    for i in infos:
        if (i.external_attr >> 16) & 0o170000 == 0o120000:  # symlink entry
            continue
        segs = _safe_member(i.filename)
        if segs and i.file_size <= MAX_FILE_BYTES:
            members.append((i, segs))
    if not members:
        raise ValueError("zip contains no importable files")
    roots = _package_roots([s for _, s in members])
    created, notes = [], {}
    for root in roots[:10]:
        n = len(root)
        files = [(i, s[n:]) for i, s in members if tuple(s[:n]) == root and len(s) > n]
        if not files:
            continue
        rel_names = {"/".join(s) for _, s in files}
        meta: Dict[str, Any] = {}
        if "system.json" in rel_names:
            try:
                meta = json.loads(z.read(next(i for i, s in files if "/".join(s) == "system.json")).decode("utf-8"))
            except (ValueError, UnicodeDecodeError, StopIteration):
                meta = {}
        man: Dict[str, Any] = {}
        if "manifest.json" in rel_names:
            try:
                man = json.loads(z.read(next(i for i, s in files if "/".join(s) == "manifest.json")).decode("utf-8"))
            except (ValueError, UnicodeDecodeError, StopIteration):
                man = {}
        ours = meta.get("exported_from", {}).get("app") == "TeleDesign" or man.get("schema") == "teledesign-system/v1"
        fmt = "teledesign" if ours else ("claude-design" if (root and root[0] == "_ds") or "SKILL.md" in rel_names
                                         or "sourcePath" in json.dumps(man.get("components") or [])[:20000]
                                         else "folder")
        sys_name = (name if len(roots) == 1 and name else None) or meta.get("name") or man.get("name") or \
            (root[-1] if root else os.path.splitext(os.path.basename(filename or ""))[0]) or "Imported system"
        rec = store.create_system({"name": str(sys_name)[:200],
                                   "sources": [{"type": "import", "ref": os.path.basename(filename or "")[:200],
                                                "format": fmt, "root": "/".join(root)}]})
        d = systems_root() / rec["id"]
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        base = d.resolve()
        for i, segs in files:
            target = (d / "/".join(segs)).resolve()
            if base not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(i) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 16)
        conv = {"notes": []}
        if fmt != "teledesign":
            conv = _convert_claude_design(d, man)
        if not (d / "adherence.json").exists():
            (d / "adherence.json").write_text(json.dumps(lint_mod.derive_adherence(d), indent=2), encoding="utf-8")
            conv["notes"].append("adherence.json derived from tokens + manifest")
        (d / "components").mkdir(exist_ok=True)
        extra = {"slug": slugify(meta.get("slug") or str(sys_name)), "import_format": fmt,
                 "needs_cleanup": fmt != "teledesign", "status": "draft"}
        for k in ("description", "version", "default_theme", "tags", "license", "credits"):
            if meta.get(k) is not None:
                extra[k] = meta[k]
        rec = patch_record(rec["id"], extra) or rec
        created.append(rec)
        notes[rec["id"]] = conv["notes"]
    if not created:
        raise ValueError("no design-system package found in the zip")
    return {"systems": created, "notes": notes}
