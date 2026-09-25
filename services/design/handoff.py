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


# ── Handoff straight into a coding session (Task Mode) ────────────────────
#
# The bundle is staged into a fresh Task-Mode workspace session (`handoff/`), and
# one CLI task is submitted on that session with a starter prompt that names the
# chosen repository. The CLI's own cwd stays the session folder (Task Mode owns
# where a CLI runs); the prompt tells it to work in the repository by absolute
# path, and every engine runs with full file access, so it can. Follow-up turns
# go through Task Mode (/tasks) on the same session, which resumes the same CLI
# conversation.

MAX_DIRS_LISTED = 400
HANDOFF_ENGINES = ("claude_code", "codex", "antigravity")


def list_dirs(path: Optional[str]) -> Dict[str, Any]:
    """Sub-directories of an absolute `path` (default: the user's home), for the
    repo picker. Names only — never file contents."""
    import os
    base = Path(path).expanduser() if path else Path.home()
    if not base.is_absolute():
        raise ValueError("path must be absolute")
    try:
        base = base.resolve()
    except OSError:
        raise ValueError("path not found")
    if not base.is_dir():
        raise ValueError("not a folder")
    dirs = []
    try:
        with os.scandir(base) as it:
            for e in it:
                try:
                    if not e.is_dir(follow_symlinks=False) or e.name.startswith((".", "$")):
                        continue
                except OSError:
                    continue
                p = Path(e.path)
                dirs.append({"name": e.name, "path": str(p), "git": (p / ".git").exists()})
                if len(dirs) >= MAX_DIRS_LISTED:
                    break
    except PermissionError:
        raise ValueError("permission denied")
    dirs.sort(key=lambda d: d["name"].lower())
    roots = []
    if os.name == "nt":
        import string
        roots = [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
    parent = str(base.parent) if base.parent != base else None
    return {"path": str(base), "parent": parent, "git": (base / ".git").exists(), "dirs": dirs,
            "home": str(Path.home()), "roots": roots}


def session_prompt(repo: Path, bundle_rel: str, primary: Optional[str], extra: str = "") -> str:
    lines = [
        f"Implement a TeleDesign design in the repository at:\n\n    {repo}\n",
        f"That repository is where every code change goes: `cd` into it before running any command, and edit "
        f"files there by absolute path. Your current folder is only a staging area holding the handoff bundle "
        f"at `{bundle_rel}/`.",
        f"Start with `{bundle_rel}/README.md` — it says what to read and in what order: every conversation in "
        f"`{bundle_rel}/chats/` (oldest first), then the primary design"
        + (f" `{bundle_rel}/project/{primary}`" if primary else "") + " and everything it loads.",
        "Read the repository's own conventions first (its CLAUDE.md / AGENTS.md / README, framework, components, "
        "tokens, routing) and rebuild the design in that stack, matching the visual result exactly — spacing, "
        "sizes, colour, type, radii, states and motion. The HTML/JSX files are a specification, not code to paste; "
        "leave out prototype plumbing (Tweaks panel, data-td-* attributes, deck controls, device frames).",
        "If the scope is unclear (which screens, which variation), stop and ask before writing code.",
    ]
    if extra.strip():
        lines.append("Notes from the designer:\n\n" + extra.strip()[:8000])
    return "\n\n".join(lines) + "\n"


def start_session(pid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Stage the bundle into a new Task-Mode session and submit the first turn.

    body: {repo (absolute folder), engine, model?, is_local?, file?, note?}
    Returns {session_id, namespace, task_id, task_type, repo, prompt}.
    """
    import tempfile
    import uuid

    from services.session import session_store
    from services.task.engine_map import ENGINE_TO_TASK_TYPE
    from services.task.task_manager import get_task_queue

    rec = store.get_project(pid)
    if not rec:
        raise LookupError("project not found")
    repo_raw = body.get("repo")
    if not isinstance(repo_raw, str) or not repo_raw.strip():
        raise ValueError("repo (an absolute folder path) is required")
    repo = Path(repo_raw.strip()).expanduser()
    if not repo.is_absolute() or not repo.is_dir():
        raise ValueError("repo must be an existing absolute folder")
    repo = repo.resolve()
    engine = body.get("engine") or "claude_code"
    if engine not in HANDOFF_ENGINES:
        raise ValueError("engine must be claude_code, codex or antigravity")
    model = body.get("model")
    if model is not None and (not isinstance(model, str) or len(model) > 200 or not re.match(r"^[\w.:/@+-]*$", model)):
        raise ValueError("invalid model")
    file = body.get("file")
    if file is not None and (not isinstance(file, str) or not store.safe_relpath(file)):
        raise ValueError("invalid file")
    note = body.get("note") if isinstance(body.get("note"), str) else ""

    sid = f"handoff-{_slug(rec.get('title') or 'design')[:40]}-{uuid.uuid4().hex[:8]}"
    meta = session_store.create(session_id=sid, session_idle_timeout_seconds=0, data={
        "source": "teledesign-handoff", "project_id": pid, "repo": str(repo)})
    folder = session_store._session_dir(sid, None)
    with tempfile.TemporaryDirectory(prefix="td-handoff-") as tmp:
        bundle = build_bundle(pid, Path(tmp), file)
        dest = folder / "handoff"
        shutil.copytree(bundle, dest)
    primary = render.primary_file(pid, file)
    prompt = session_prompt(repo, "handoff", primary, note)
    params: Dict[str, Any] = {"prompt": prompt, "is_local": bool(body.get("is_local"))}
    if model:
        params["model"] = model
    task_type = ENGINE_TO_TASK_TYPE[engine]
    task_id = get_task_queue().submit_task(
        task_type=task_type, params=params, session_id=meta["session_id"],
        metadata={"source": "teledesign-handoff", "project_id": pid, "repo": str(repo)})
    return {"session_id": meta["session_id"], "namespace": None, "task_id": task_id, "task_type": task_type,
            "repo": str(repo), "prompt": prompt, "engine": engine, "model": model,
            "is_local": params["is_local"]}
