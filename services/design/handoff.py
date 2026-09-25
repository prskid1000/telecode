"""Handoff bundle for a coding agent (docs/teledesign.md §4.5).

    <slug>/
      README.md          prompts/handoff_README.md, filled in ("CODING AGENTS: READ THIS FIRST")
      chats/NN-<title>.md   every chat transcript, oldest first (contract §4.5 format)
      project/…          the design files (incl. project/_ds/ — the staged design system)
      uploads/           material the user supplied

The primary design is the active file (the `file` the UI sends, else the project's
primary file). `build_bundle` is synchronous (plain file copies) so the copy-prompt
route can build one on demand; `export()` zips it as a job result.
"""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.design import render, store

_TEMPLATE = Path(__file__).parent / "prompts" / "handoff_README.md"
_PROJECT_EXCLUDE = {".versions", ".td", "chats", "uploads"}
_PROJECT_EXCLUDE_FILES = {"thumbnail.webp"}


def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s or "").strip("-").lower()
    return (s or "design")[:60]


def _fill(template: str, values: Dict[str, Any]) -> str:
    """{{key}} substitution plus {{#key}}…{{/key}} sections kept only when key is truthy."""
    def section(m: re.Match) -> str:
        return m.group(2) if values.get(m.group(1)) else ""
    out = re.sub(r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}", section, template, flags=re.S)
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values.get(m.group(1), "")), out)


def _chat_index(pdir: Path) -> List[Dict[str, Any]]:
    try:
        idx = json.loads((pdir / "chats" / "index.json").read_text(encoding="utf-8"))
        chats = idx if isinstance(idx, list) else idx.get("chats", [])
        return [c for c in chats if isinstance(c, dict) and isinstance(c.get("id"), str)]
    except Exception:
        return []


def _transcript_from_jsonl(title: str, path: Path) -> str:
    """Rebuild a §4.5 transcript when the .md is missing (older chats)."""
    lines = [f"# {title}"]
    started = None
    body: List[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            t = json.loads(raw)
        except Exception:
            continue
        started = started or t.get("created_at")
        role = "User" if t.get("role") == "user" else "Assistant"
        body += ["", f"## {role}", (t.get("text") or "").strip()]
        for tool in t.get("tools") or []:
            body.append(f"_[tool: {tool.get('name', '?')}]_ {tool.get('input_preview', '')}".rstrip())
    lines.append(f"_Started {started or ''}_")
    return "\n".join(lines + body) + "\n"


def _tree(root: Path, limit: int = 120) -> str:
    out: List[str] = [root.name + "/"]

    def walk(d: Path, prefix: str) -> None:
        entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        for i, p in enumerate(entries):
            if len(out) >= limit:
                return
            last = i == len(entries) - 1
            out.append(f"{prefix}{'└── ' if last else '├── '}{p.name}{'/' if p.is_dir() else ''}")
            if p.is_dir():
                walk(p, prefix + ("    " if last else "│   "))
    walk(root, "")
    if len(out) >= limit:
        out.append("… (truncated)")
    return "\n".join(out)


def build_bundle(pid: str, dest_parent: Path, primary: Optional[str] = None) -> Path:
    """Write the bundle directory under `dest_parent`; returns its path."""
    pdir = store.project_dir(pid)
    rec = store.get_project(pid)
    if not pdir or not rec:
        raise ValueError("project not found")
    primary = render.primary_file(pid, primary) or primary or ""
    root = dest_parent / _slug(rec.get("title") or "design")
    if root.exists():
        shutil.rmtree(root)
    (root / "chats").mkdir(parents=True)
    (root / "project").mkdir()

    # chats, oldest first
    chats = sorted(_chat_index(pdir), key=lambda c: c.get("created_at") or "")
    seen = set()
    n = 0
    for c in chats:
        cid = c["id"]
        if not store.valid_id(cid) and not re.match(r"^[A-Za-z0-9_-]{1,64}$", cid):
            continue
        md, jl = pdir / "chats" / f"{cid}.md", pdir / "chats" / f"{cid}.jsonl"
        if md.is_file():
            text = md.read_text(encoding="utf-8", errors="replace")
        elif jl.is_file():
            text = _transcript_from_jsonl(c.get("title") or "Chat", jl)
        else:
            continue
        n += 1
        seen.add(cid)
        (root / "chats" / f"{n:02d}-{_slug(c.get('title') or cid)}.md").write_bytes(text.encode("utf-8"))
    # transcripts not in the index (index missing or stale)
    for md in sorted((pdir / "chats").glob("*.md")) if (pdir / "chats").is_dir() else []:
        if md.stem in seen:
            continue
        n += 1
        shutil.copyfile(md, root / "chats" / f"{n:02d}-{_slug(md.stem)}.md")

    # project files
    for p in sorted(pdir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(pdir)
        if rel.parts[0] in _PROJECT_EXCLUDE or (len(rel.parts) == 1 and rel.name in _PROJECT_EXCLUDE_FILES) \
                or p.name.endswith(".tmp"):
            continue
        dst = root / "project" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
    if (pdir / "uploads").is_dir():
        shutil.copytree(pdir / "uploads", root / "uploads", dirs_exist_ok=True)

    # README
    board = primary
    for bid, b in (store.get_boards(pid) or {}).items():
        if isinstance(b, dict) and b.get("src") == primary:
            board = bid
            break
    ds_note = ""
    sid = rec.get("design_system_id")
    if sid:
        sysrec = store.get_system(sid) or {}
        ds_note = f"This project uses the **{sysrec.get('name') or 'attached'}** design system."
    elif (pdir / "_ds").is_dir():
        ds_note = "This project uses the design system staged in `project/_ds/`."
    values = {
        "project_title": rec.get("title") or "Untitled design",
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "chat_count": n, "primary_file": primary or "(none)", "primary_board": board or "(none)",
        "ds_note": ds_note, "bundle_tree": "",
    }
    readme = root / "README.md"
    template = "\n".join(_TEMPLATE.read_text(encoding="utf-8").splitlines()) + "\n"
    readme.write_bytes(_fill(template, values).encode("utf-8"))
    values["bundle_tree"] = _tree(root)
    readme.write_bytes(_fill(template, values).encode("utf-8"))
    (root / "handoff.json").write_text(json.dumps({
        "project_id": pid, "title": values["project_title"], "primary_file": primary, "primary_board": board,
        "exported_at": values["exported_at"], "chats": n, "design_system_id": sid,
    }, indent=2), encoding="utf-8")
    return root


def prompt_for(bundle: Path, primary: Optional[str]) -> str:
    p = str(bundle)
    lines = [
        f"Implement the design in the TeleDesign handoff bundle at:\n\n    {p}\n",
        f"Start with {bundle / 'README.md'} — it says what to read and in what order: every "
        "conversation in chats/ (oldest first), then the primary design"
        + (f" project/{primary}" if primary else "") + " and everything it loads.",
        "Rebuild it in this codebase's own stack and conventions (framework, components, tokens, routing), matching "
        "the visual result exactly — spacing, sizes, colour, type, radii, states and motion. The HTML/JSX files are "
        "a specification, not code to paste; leave out prototype plumbing (Tweaks panel, data-td-* attributes, deck "
        "controls, device frames).",
        "If the scope is unclear (which screens, which variation), ask me before writing code.",
    ]
    return "\n\n".join(lines) + "\n"


def zip_bundle(bundle: Path, out: Path) -> Path:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(bundle.rglob("*")):
            if p.is_file():
                z.write(p, f"{bundle.name}/{p.relative_to(bundle).as_posix()}")
    return out


async def export(ctx) -> Path:
    import asyncio

    ctx.progress(0.1, "Collecting chats and files")
    bundle = await asyncio.to_thread(build_bundle, ctx.pid, ctx.out_dir / "bundle", ctx.file)
    ctx.job["bundle_path"] = str(bundle)
    ctx.job["prompt"] = prompt_for(bundle, render.primary_file(ctx.pid, ctx.file))
    ctx.progress(0.7, "Zipping")
    out = await asyncio.to_thread(zip_bundle, bundle, ctx.out_dir / ctx.name("zip", "-handoff"))
    return out
