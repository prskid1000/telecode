"""An agent's memory: an index + typed topic files, versioned in git.

Layout of ``data/agents/<id>/internal/`` (a git repo, :mod:`.repo`)::

    SOUL.md  USER.md  AGENT.md  HEARTBEAT.md
    memory/
        MEMORY.md                 the index — one line per memory, ≤200 lines / 25 KB
        feedback_<slug>.md        a topic file per memory, with frontmatter
        project_<slug>.md …
    skills/<name>/SKILL.md        portable skills (services/skills/agent_skills.py)

Topic file::

    ---
    name: Deploys go through staging
    description: one line — what the index shows
    type: feedback            # user | feedback | project | reference
    helpful: 2                # feedback only: ACE counters the reflection job bumps
    harmful: 0
    ---
    body…

Index line: ``- [Name](file.md) — description``. The same shape Claude Code's
auto memory uses, so ``autoMemoryDirectory`` can point straight at ``memory/``
and there is one memory, not one per folder. Only the index is staged into a
run's workspace (as ``MEMORY.md``); topic files are reached through
``--add-dir`` (see :func:`services.memory.engine_extras`).

The pre-P4 single ``MEMORY.md`` is migrated once, per agent, by :func:`ensure`:
committed as it was, renamed to ``MEMORY.legacy.md`` (so it stays in the
history), then split into topic files by heading and removed from the tree.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.memory import repo

logger = logging.getLogger("telecode.services.memory.store")

MEMORY_DIR = "memory"
INDEX = "MEMORY.md"
INDEX_REL = f"{MEMORY_DIR}/{INDEX}"
LEGACY = "MEMORY.md"            # the pre-P4 location, internal/MEMORY.md
LEGACY_KEPT = "MEMORY.legacy.md"
TYPES = ("user", "feedback", "project", "reference")
INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25 * 1024
TOPIC_MAX_BYTES = 256 * 1024
TOPIC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.md$")
_INDEX_LINE_RE = re.compile(r"\]\(([^)\s]+\.md)\)")
PINNED_HEADING = "Pinned constraints"
_PINNED_RE = re.compile(r"^#{1,6}\s*pinned constraints\s*$", re.I | re.M)


# ── paths ───────────────────────────────────────────────────────────────────

def internal_dir(agent_id: str) -> Path:
    from services.agent.agent_manager import get_agent_manager
    return get_agent_manager()._get_agent_internal_dir(agent_id)


def memory_dir(agent_id: str) -> Path:
    return internal_dir(agent_id) / MEMORY_DIR


def agent_state_dir(agent_id: str) -> Path:
    """``data/agents/<id>/`` — outside the git repo (reflection state, runtime files)."""
    return internal_dir(agent_id).parent


def check_topic_name(name: str) -> str:
    name = (name or "").strip()
    if name == INDEX or not TOPIC_RE.match(name) or ".." in name:
        raise ValueError(f"topic file must be a plain name like feedback_deploys.md (not {INDEX}), got {name!r}")
    return name


# ── frontmatter ────────────────────────────────────────────────────────────

def parse_topic(text: str) -> Tuple[Dict[str, Any], str]:
    text = (text or "").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    meta: Dict[str, Any] = {}
    for line in text[4:end].splitlines():
        m = re.match(r"^\s*([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*$", line)
        if not m:
            continue
        k, v = m.group(1), m.group(2)
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k in ("helpful", "harmful"):
            try:
                v = int(v)
            except ValueError:
                v = 0
        meta[k] = v
    return meta, text[end + 4:].lstrip("\n")


def _yaml_str(v: str) -> str:
    v = " ".join(str(v or "").split())
    return f'"{v}"' if (not v or v[0] in "\"'[{>|*&!%@`#-" or ": " in v or v.endswith(":")) and '"' not in v else v


def render_topic(meta: Dict[str, Any], body: str) -> str:
    t = meta.get("type") if meta.get("type") in TYPES else "project"
    lines = ["---", f"name: {_yaml_str(meta.get('name') or 'Untitled')}",
             f"description: {_yaml_str(meta.get('description') or '')}", f"type: {t}"]
    if t == "feedback":
        lines += [f"helpful: {int(meta.get('helpful') or 0)}", f"harmful: {int(meta.get('harmful') or 0)}"]
    for k, v in meta.items():
        if k not in ("name", "description", "type", "helpful", "harmful") and isinstance(v, (str, int)):
            lines.append(f"{k}: {_yaml_str(str(v))}")
    body = (body or "").replace("\r\n", "\n").strip("\n")
    return "\n".join(lines) + "\n---\n\n" + body + "\n"


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")
    return (s or "note")[:48].rstrip("_")


def unique_topic_name(existing: List[str], type_: str, name: str) -> str:
    base = f"{type_ if type_ in TYPES else 'project'}_{slug(name)}"
    cand, n = f"{base}.md", 2
    taken = {e.lower() for e in existing}
    while cand.lower() in taken or cand == INDEX:
        cand, n = f"{base}_{n}.md", n + 1
    return cand


def infer_type(heading: str, body: str = "") -> str:
    h = (heading or "").lower()
    if re.search(r"\b(reference|references|links?|urls?|resources?|docs?|documentation|contacts?|api)\b", h):
        return "reference"
    if re.search(r"\b(feedback|lessons?|mistakes?|corrections?|rules?|guidelines?|gotchas?|don'?t|avoid|always|never)\b", h):
        return "feedback"
    if re.search(r"\b(user|owner|about (me|you)|profile|preferences?|who)\b", h):
        return "user"
    return "project"


def describe(body: str, limit: int = 150) -> str:
    for line in (body or "").splitlines():
        s = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line).strip()
        s = re.sub(r"[*_`#>]+", "", s).strip()
        if s and not s.startswith("---"):
            return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"
    return ""


def index_line(fname: str, meta: Dict[str, Any]) -> str:
    name = " ".join(str(meta.get("name") or fname[:-3]).split()).replace("]", ")")
    desc = " ".join(str(meta.get("description") or "").split())
    return f"- [{name}]({fname})" + (f" — {desc}" if desc else "")


def index_files(index_text: str) -> List[str]:
    return [m.group(1) for m in _INDEX_LINE_RE.finditer(index_text or "")]


def index_warnings(text: str) -> List[str]:
    out = []
    lines = (text or "").splitlines()
    size = len((text or "").encode("utf-8"))
    if len(lines) > INDEX_MAX_LINES:
        out.append(f"index has {len(lines)} lines — only the first {INDEX_MAX_LINES} are loaded; "
                   "fold detail into topic files")
    if size > INDEX_MAX_BYTES:
        out.append(f"index is {size // 1024} KB — only the first 25 KB are loaded")
    return out


def upsert_index_line(index_text: str, fname: str, meta: Dict[str, Any]) -> str:
    line = index_line(fname, meta)
    lines = (index_text or "").replace("\r\n", "\n").split("\n")
    for i, l in enumerate(lines):
        if f"]({fname})" in l:
            lines[i] = line
            return "\n".join(lines)
    text = "\n".join(lines).rstrip("\n")
    if not text.strip():
        text = "# Memory Index\n"
    return text + "\n" + line + "\n"


def drop_index_line(index_text: str, fname: str) -> str:
    lines = (index_text or "").replace("\r\n", "\n").split("\n")
    return "\n".join(l for l in lines if f"]({fname})" not in l)


def build_index(topics: List[Tuple[str, Dict[str, Any]]]) -> str:
    return "# Memory Index\n\n" + "".join(index_line(f, m) + "\n" for f, m in topics)


# ── the one-time split of a legacy MEMORY.md ───────────────────────────────

def split_legacy(text: str) -> Tuple[str, Dict[str, str]]:
    """Heuristic: one topic per section at the shallowest heading level that
    repeats (usually ``##``); text before the first section → "General". A
    file without such headings becomes one "Notes" topic. Returns (index, {file: text})."""
    text = (text or "").replace("\r\n", "\n").strip("\n")
    if not text.strip():
        return "", {}
    heads = [(m.start(), len(m.group(1)), m.group(2).strip())
             for m in re.finditer(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", text, re.M)]
    level = None
    for lv in (2, 1, 3):
        if sum(1 for h in heads if h[1] == lv) >= (1 if lv == 2 else 2):
            level = lv
            break
    sections: List[Tuple[str, str]] = []
    if level is None:
        body = re.sub(r"^#\s+.*\n?", "", text, count=1) if heads and heads[0][0] == 0 and heads[0][1] == 1 else text
        sections.append(("Notes", body))
    else:
        cuts = [h for h in heads if h[1] == level]
        pre = text[: cuts[0][0]]
        pre = re.sub(r"^#\s+.*\n?", "", pre.lstrip("\n"), count=1) if level > 1 else pre
        if pre.strip():
            sections.append(("General", pre))
        for i, (pos, _lv, title) in enumerate(cuts):
            end = cuts[i + 1][0] if i + 1 < len(cuts) else len(text)
            body = text[pos:end].split("\n", 1)
            sections.append((title, body[1] if len(body) > 1 else ""))
    topics: Dict[str, str] = {}
    entries: List[Tuple[str, Dict[str, Any]]] = []
    for title, body in sections:
        body = body.strip("\n")
        if not body.strip():
            continue
        t = infer_type(title, body)
        meta = {"name": title, "description": describe(body), "type": t}
        fname = unique_topic_name(list(topics), t, title)
        topics[fname] = render_topic(meta, body)
        entries.append((fname, meta))
    return build_index(entries), topics


# ── ensure: repo + layout, migrating once ──────────────────────────────────

def _normalise_crlf(d: Path) -> None:
    for p in d.rglob("*.md"):
        if ".git" in p.parts:
            continue
        try:
            b = p.read_bytes()
            if b"\r\n" in b:
                p.write_bytes(b.replace(b"\r\n", b"\n"))
        except OSError:
            pass


def ensure(agent_id: str) -> Path:
    """The agent's internal dir, a git repo with the P4 layout. Idempotent and
    cheap once done; the first call on a pre-P4 agent migrates it."""
    d = internal_dir(agent_id)
    mdir = d / MEMORY_DIR
    if repo.is_repo(d) and (mdir / INDEX).is_file() and not (d / LEGACY).exists() \
            and not (d / LEGACY_KEPT).exists():
        return d
    d.mkdir(parents=True, exist_ok=True)
    with repo.lock_for(d):
        use_git = repo.available()
        if use_git and not repo.is_repo(d):
            _normalise_crlf(d)
            repo.init(d, "migrate: start history of the agent's internal files")
        legacy = d / LEGACY
        kept = d / LEGACY_KEPT
        if legacy.exists() and not (mdir / INDEX).exists():
            text = legacy.read_text(encoding="utf-8")
            if text.strip():
                if use_git:
                    legacy.replace(kept)
                    repo.commit_all(d, repo.message("migrate: keep the original MEMORY.md as MEMORY.legacy.md",
                                                    kind="migrate"))
                    source = kept
                else:
                    source = legacy
                index, topics = split_legacy(text)
                for fname, body in topics.items():
                    repo.write_text(mdir / fname, body)
                repo.write_text(mdir / INDEX, index)
                source.unlink(missing_ok=True)
                if use_git:
                    repo.commit_all(d, repo.message(
                        f"migrate: split MEMORY.md into an index + {len(topics)} topic file(s)", kind="migrate"))
                logger.info(f"agent {agent_id}: migrated MEMORY.md → memory/ ({len(topics)} topics)")
            else:
                legacy.unlink()
        elif kept.exists() and not (mdir / INDEX).exists():   # crashed between the two migration commits
            text = kept.read_text(encoding="utf-8")
            index, topics = split_legacy(text)
            for fname, body in topics.items():
                repo.write_text(mdir / fname, body)
            repo.write_text(mdir / INDEX, index)
            kept.unlink()
            if use_git:
                repo.commit_all(d, repo.message(
                    f"migrate: split MEMORY.md into an index + {len(topics)} topic file(s)", kind="migrate"))
        elif legacy.exists():
            legacy.unlink()          # both exist: the index wins, the stray file is history
        if not (mdir / INDEX).exists():
            repo.write_text(mdir / INDEX, "")
        if use_git and repo.dirty(d):
            repo.commit_all(d, repo.message("migrate: memory/ layout", kind="migrate"))
    return d


def migrate_all() -> int:
    """Run :func:`ensure` for every agent (startup)."""
    from services.agent.agent_manager import get_agent_manager
    n = 0
    for a in get_agent_manager().list_agents():
        try:
            ensure(a["id"])
            n += 1
        except Exception:
            logger.exception(f"memory migration failed for agent {a.get('id')}")
    return n


# ── topics / index API ─────────────────────────────────────────────────────

def read_index(agent_id: str) -> str:
    p = ensure(agent_id) / INDEX_REL
    return p.read_text(encoding="utf-8") if p.exists() else ""


def list_topics(agent_id: str) -> List[Dict[str, Any]]:
    mdir = ensure(agent_id) / MEMORY_DIR
    idx = set(index_files(read_index(agent_id)))
    out = []
    for p in sorted(mdir.glob("*.md")):
        if p.name == INDEX:
            continue
        meta, body = parse_topic(p.read_text(encoding="utf-8"))
        out.append({"file": p.name, "name": meta.get("name") or p.stem, "description": meta.get("description") or "",
                    "type": meta.get("type") if meta.get("type") in TYPES else "project",
                    "helpful": int(meta.get("helpful") or 0), "harmful": int(meta.get("harmful") or 0),
                    "bytes": p.stat().st_size, "in_index": p.name in idx})
    return out


def read_topic(agent_id: str, fname: str) -> Optional[Dict[str, Any]]:
    fname = check_topic_name(fname)
    p = ensure(agent_id) / MEMORY_DIR / fname
    if not p.is_file():
        return None
    text = p.read_text(encoding="utf-8")
    meta, body = parse_topic(text)
    return {"file": fname, "meta": meta, "body": body, "content": text}


def _commit(agent_id: str, subject: str, **trailers: Any) -> Optional[str]:
    d = internal_dir(agent_id)
    if not repo.available() or not repo.is_repo(d):
        return None
    return repo.commit_all(d, repo.message(subject, kind="ui", **trailers))


def write_index(agent_id: str, text: str) -> Dict[str, Any]:
    d = ensure(agent_id)
    with repo.lock_for(d):
        repo.write_text(d / INDEX_REL, text or "")
        sha = _commit(agent_id, "ui: edit memory index")
    return {"commit": sha, "warnings": index_warnings(text)}


def write_topic(agent_id: str, fname: str, *, content: Optional[str] = None,
                meta: Optional[Dict[str, Any]] = None, body: Optional[str] = None,
                update_index: bool = True) -> Dict[str, Any]:
    """Create / replace a topic file (raw ``content``, or ``meta`` + ``body``)
    and keep its index line in step."""
    fname = check_topic_name(fname)
    if content is None:
        content = render_topic(meta or {}, body or "")
    if len(content.encode("utf-8")) > TOPIC_MAX_BYTES:
        raise ValueError(f"topic file too large (limit {TOPIC_MAX_BYTES // 1024} KB)")
    parsed, _ = parse_topic(content)
    t = parsed.get("type")
    if t and t not in TYPES:
        raise ValueError(f"type must be one of {TYPES}")
    d = ensure(agent_id)
    with repo.lock_for(d):
        existed = (d / MEMORY_DIR / fname).exists()
        repo.write_text(d / MEMORY_DIR / fname, content)
        if update_index:
            idx = (d / INDEX_REL).read_text(encoding="utf-8") if (d / INDEX_REL).exists() else ""
            repo.write_text(d / INDEX_REL, upsert_index_line(idx, fname, parsed or {"name": fname[:-3]}))
        sha = _commit(agent_id, f"ui: {'edit' if existed else 'add'} memory {fname}")
    return {"commit": sha, "file": fname}


def delete_topic(agent_id: str, fname: str) -> bool:
    fname = check_topic_name(fname)
    d = ensure(agent_id)
    with repo.lock_for(d):
        p = d / MEMORY_DIR / fname
        if not p.exists():
            return False
        p.unlink()
        idx = (d / INDEX_REL).read_text(encoding="utf-8") if (d / INDEX_REL).exists() else ""
        repo.write_text(d / INDEX_REL, drop_index_line(idx, fname))
        _commit(agent_id, f"ui: delete memory {fname}")
    return True


def rebuild_index(agent_id: str) -> Dict[str, Any]:
    """Regenerate the index from the topic files' frontmatter (keeps order of
    files already listed, appends the rest)."""
    d = ensure(agent_id)
    with repo.lock_for(d):
        cur = read_index(agent_id)
        topics = {t["file"]: t for t in list_topics(agent_id)}
        order = [f for f in index_files(cur) if f in topics] + sorted(f for f in topics if f not in index_files(cur))
        text = build_index([(f, topics[f]) for f in order])
        repo.write_text(d / INDEX_REL, text)
        sha = _commit(agent_id, "ui: rebuild memory index from topic files")
    return {"commit": sha, "index": text, "warnings": index_warnings(text)}


# ── pinned constraints (a section of AGENT.md) ─────────────────────────────

def section_bounds(text: str) -> Optional[Tuple[int, int, int]]:
    """(heading start, body start, body end) of the ``## Pinned constraints`` section."""
    m = _PINNED_RE.search(text or "")
    if not m:
        return None
    rest = text[m.end():]
    nxt = re.search(r"^#{1,6}\s", rest, re.M)
    return m.start(), m.end(), m.end() + (nxt.start() if nxt else len(rest))


def pinned_from_text(text: str) -> str:
    b = section_bounds(text or "")
    return (text[b[1]:b[2]]).strip() if b else ""


def replace_pinned(text: str, pinned: str) -> str:
    text = (text or "").replace("\r\n", "\n")
    pinned = (pinned or "").replace("\r\n", "\n").strip()
    b = section_bounds(text)
    block = f"## {PINNED_HEADING}\n\n{pinned}\n" if pinned else ""
    if b:
        before, after = text[: b[0]], text[b[2]:]
        if not block:
            return (before.rstrip("\n") + ("\n\n" + after.lstrip("\n") if after.strip() else "\n")).lstrip("\n") \
                if before.strip() else after.lstrip("\n")
        return before + block + ("\n" + after.lstrip("\n") if after.strip() else "")
    if not block:
        return text
    return (text.rstrip("\n") + "\n\n" if text.strip() else "") + block
