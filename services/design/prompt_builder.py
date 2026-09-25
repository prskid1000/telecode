"""Compose a design turn's prompt from `services/design/prompts/` (see its README).

The stack splits in two:

- **brief** — layers 2–6 (charter, canvas, board rules, kind skill + companions,
  design system, craft) plus the engine notes. Stable across the turns of a
  project, so it is written to `.td/brief.md` and sent in full only when the
  CLI session has not seen this exact text yet (first turn of a chat, or the
  brief changed: system attached, kind switched…). A resumed session already
  holds it in context; resending ~60 KB every turn would bury the conversation.
- **turn** — discovery (when it applies), comment instructions, and the layer-7
  blocks: project facts → canvas → references → attachments → web capture →
  mentioned elements → attached comments → form answers → the user's message.

Unknown `{{placeholders}}` raise `PromptError`: sending literal braces to the
model is a composer bug, not something to paper over.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from services.design import store

logger = logging.getLogger("telecode.services.design.prompt_builder")

PROMPTS_DIR = Path(__file__).parent / "prompts"
STARTERS_DIR = Path(__file__).parent / "starters"
BRIEF_REL = ".td/brief.md"

# Pinned UMD builds referenced by html_boards.md (computed from the unpkg files).
SRI = {
    "sri_react": "sha384-hD6/rw4ppMLGNu3tX5cjIb+uRZ7UkRJ6BPkLpg4hAu/6onKUg4lLsHAs9EBPT82L",
    "sri_react_dom": "sha384-u6aeetuaXnQ38mYT8rp6sbXaQe3NL9t+IBXmnYxwkUI2Hw4bsp2Wvmx4yRQF1uAm",
    "sri_babel": "sha384-m08KidiNqLdpJqLq95G/LEi8Qvjl/xUYll3QILypMoQ65QorJ9Lvtp2RXYGBFj1y",
}

HTML_KINDS = {"prototype", "slides", "one_pager", "animation", "landing_page", "mobile_app",
              "web_app", "other"}
LAYER_KINDS = {"wireframe", "dashboard_table", "design_system"}
TWEAK_KINDS = {"prototype", "landing_page", "mobile_app", "web_app", "other"}

_ALL_CRAFT = ("typography", "color", "layout_spacing", "motion", "copywriting", "imagery_icons")
CRAFT_BY_KIND = {
    "prototype": _ALL_CRAFT,
    "slides": ("typography", "color", "layout_spacing", "copywriting", "imagery_icons"),
    "wireframe": ("layout_spacing", "copywriting"),
    "one_pager": ("typography", "color", "layout_spacing", "copywriting", "imagery_icons"),
    "animation": ("typography", "color", "motion", "imagery_icons"),
    "landing_page": _ALL_CRAFT,
    "mobile_app": _ALL_CRAFT,
    "web_app": _ALL_CRAFT,
    "dashboard_table": ("typography", "color", "layout_spacing", "copywriting"),
    "design_system": _ALL_CRAFT,
}

ENGINE_NOTES = {
    "claude_code": (
        "- Keep your todo list in the TodoWrite tool — the host renders it live in the chat.\n"
        "- Write files with paths relative to your working directory (the project folder).\n"
    ),
    "codex": (
        "- Keep your plan current with your plan/todo tool — the host shows progress from it.\n"
        "- Write files with paths relative to your working directory (the project folder).\n"
    ),
    "antigravity": (
        "- The host cannot see your tool calls, so state each file you create or change in your reply.\n"
        "- Write files with paths relative to your working directory (the project folder).\n"
    ),
}

EFFORT_NOTES = {
    "low": "Effort: low — make the smallest change that satisfies the request; skip optional polish.",
    "medium": "Effort: medium — a solid pass; polish what the user will look at first.",
    "high": "Effort: high — take the time to explore and polish.",
    "xhigh": "Effort: extra high — explore alternatives before committing; polish thoroughly.",
    "max": "Effort: maximum — explore widely, verify carefully, polish everything.",
}

_CONVERT_RE = re.compile(r"\b(convert|to layers|to code|export react|as react|as jsx|tailwind)\b", re.I)
_VARIANT_RE = re.compile(r"\b(tweak|variant|variation|options?)\b", re.I)


class PromptError(RuntimeError):
    pass


# ── Template rendering ───────────────────────────────────────────────────

_SECTION_RE = re.compile(r"\{\{([#^])(\w+)\}\}(.*?)\{\{/\2\}\}", re.S)
_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def render(template: str, values: Dict[str, Any], *, name: str = "?") -> str:
    """`{{x}}`, `{{#x}}…{{/x}}` (non-empty), `{{^x}}…{{/x}}` (empty). Single pass for
    values, so text the user supplied is never re-scanned for placeholders."""
    def section(m: "re.Match[str]") -> str:
        kind, key, body = m.group(1), m.group(2), m.group(3)
        if key not in values:
            raise PromptError(f"{name}: unknown section placeholder {{{{{kind}{key}}}}}")
        truthy = bool(values[key])
        return body if (truthy if kind == "#" else not truthy) else ""

    prev = None
    out = template
    while prev != out:
        prev = out
        out = _SECTION_RE.sub(section, out)

    def var(m: "re.Match[str]") -> str:
        key = m.group(1)
        if key not in values:
            raise PromptError(f"{name}: unknown placeholder {{{{{key}}}}}")
        v = values[key]
        return "" if v is None else str(v)

    return _VAR_RE.sub(var, out)


def _load(rel: str) -> str:
    return (PROMPTS_DIR / rel).read_text(encoding="utf-8")


def load_prompt(rel: str, values: Dict[str, Any]) -> str:
    return render(_load(rel), values, name=rel).strip()


def comments_part_a() -> str:
    text = _load("comments.md")
    start = text.find("## Part A")
    end = text.find("## Part B")
    body = text[start:end] if start >= 0 and end > start else text
    return body.rstrip().rstrip("-").rstrip()


# ── Pieces ───────────────────────────────────────────────────────────────

def _esc(s: Any) -> str:
    # comments.md: comment text is user data — escape `<` and `&`.
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;")


def _attr(s: Any) -> str:
    return html.escape(str(s or ""), quote=True)


def user_org_domain() -> str:
    domains = config.get_nested("design.own_domains", []) or []
    return str(domains[0]) if domains else ""


def starters_table() -> str:
    idx = STARTERS_DIR / "index.json"
    try:
        items = json.loads(idx.read_text(encoding="utf-8"))
    except Exception:
        return "(No starter files are installed this turn.)"
    rows = []
    for it in items if isinstance(items, list) else []:
        f = it.get("file") if isinstance(it, dict) else None
        if not f or not (STARTERS_DIR / f).is_file():
            continue
        load_as = "text/babel" if f.endswith((".jsx", ".tsx")) else "<script src>"
        rows.append(f"- `{f}` · `{(STARTERS_DIR / f).as_posix()}` · {load_as}"
                    + (f" — {it.get('description')}" if it.get("description") else ""))
    if not rows:
        return "(No starter files are installed this turn.)"
    return "Available this turn (name · path to copy from · load as):\n" + "\n".join(rows)


def _optional(module: str, attr: str):
    try:
        mod = __import__(f"services.design.{module}", fromlist=[attr])
        return getattr(mod, attr, None)
    except Exception:
        return None


def design_system_block(project: Dict[str, Any], project_dir: Path) -> Tuple[str, str]:
    """(rendered block, ds_bundle path). Uses W5's `systems.prompt_context` / `stage`."""
    sid = project.get("design_system_id")
    if not sid:
        return "", ""
    system = store.get_system(sid)
    if not system:
        logger.warning("design prompt: project %s references missing system %s", project.get("id"), sid)
        return "", ""
    stage = _optional("systems", "stage")
    staged: Optional[Path] = None
    if stage:
        try:
            staged = stage(sid, project_dir)
        except Exception as exc:
            logger.warning("design prompt: systems.stage(%s) failed: %s", sid, exc)
    else:
        logger.info("design prompt: services.design.systems.stage not available yet — using fallback staging")
        staged = _fallback_stage(system, project_dir)
    ctx = _optional("systems", "prompt_context")
    block = ""
    if ctx:
        try:
            block = ctx(sid) or ""
        except Exception as exc:
            logger.warning("design prompt: systems.prompt_context(%s) failed: %s", sid, exc)
    if not block:
        block = _fallback_block(system, staged, project_dir)
    bundle = ""
    if staged and (staged / "bundle.js").is_file():
        bundle = (staged / "bundle.js").relative_to(project_dir).as_posix()
    return block, bundle


_FALLBACK_FILES = ("USAGE.md", "DESIGN.md", "tokens.css", "tokens.json", "manifest.json", "components.css")


def _fallback_stage(system: Dict[str, Any], project_dir: Path) -> Optional[Path]:
    src = store.base_dir() / "systems" / system["id"]
    slug = re.sub(r"[^a-z0-9-]+", "-", (system.get("slug") or system.get("name") or "system").lower()).strip("-")
    dst = project_dir / "_ds" / (slug or "system")
    try:
        dst.mkdir(parents=True, exist_ok=True)
        for name in _FALLBACK_FILES:
            if (src / name).is_file():
                (dst / name).write_bytes((src / name).read_bytes())
        return dst
    except OSError as exc:
        logger.warning("design prompt: fallback stage failed: %s", exc)
        return None


def _fallback_block(system: Dict[str, Any], staged: Optional[Path], project_dir: Path) -> str:
    if not staged:
        return ""

    def read(name: str, cap: int) -> str:
        p = staged / name
        try:
            t = p.read_text(encoding="utf-8")
        except OSError:
            return "(not provided)"
        return t if len(t) <= cap else t[:cap] + f"\n… (truncated — full text: {p.relative_to(project_dir).as_posix()})"

    rel = staged.relative_to(project_dir).as_posix()
    index = "\n".join(f"{p.relative_to(project_dir).as_posix()} · {p.suffix.lstrip('.')} · {p.stat().st_size}"
                      for p in sorted(staged.rglob("*")) if p.is_file())
    return (f"## Design system: {system.get('name')}  (read-only copy at {rel})\n\n"
            f"### How to use it\n{read('USAGE.md', 6000)}\n\n"
            f"### DESIGN.md\n{read('DESIGN.md', 12000)}\n\n"
            f"### Tokens (tokens.css)\n{read('tokens.css', 12000)}\n\n"
            f"### Manifest (manifest.json)\n{read('manifest.json', 6000)}\n\n"
            f"### Files you can copy from\n{index}")


def persona_block(project: Dict[str, Any]) -> str:
    aid = project.get("agent_id")
    if not aid or not re.match(r"^[A-Za-z0-9_-]{1,64}$", str(aid)):
        return ""
    p = Path(config._settings_dir()) / "data" / "agents" / str(aid) / "internal" / "AGENT.md"
    try:
        text = p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return f"## Persona\nThe user asked you to work in this persona (from the agent's AGENT.md):\n\n{text[:12000]}"


def canvas_summary(project_dir: Path) -> str:
    boards = store._read_json(project_dir / "boards.json") or {}
    lines = []
    for node_id, b in sorted(boards.items()):
        if isinstance(b, dict):
            lines.append(f"- {node_id} · html · {b.get('src')} · {b.get('width')}×{b.get('height')}")
    on_boards = {b.get("src") for b in boards.values() if isinstance(b, dict)}
    from services.design import files as dfiles
    loose = [rel for rel, _ in dfiles.iter_project_files(project_dir, include_readonly=False)
             if rel.lower().endswith(".html") and rel not in on_boards]
    for rel in loose[:60]:
        lines.append(f"- (file) · html · {rel} · not placed on a board yet")
    if (project_dir / "doc.fig").is_file():
        lines.append("- doc.fig · layer boards live in the canvas document (read them through the canvas tools)")
    return "\n".join(lines) if lines else "The canvas is empty."


def attachments_block(project_dir: Path, rels: List[str]) -> str:
    rows = []
    for rel in rels:
        p = store.resolve_in(project_dir, rel)
        if p and p.is_file():
            rows.append(f"- `{rel}` ({p.stat().st_size} bytes)")
    if not rows:
        return ""
    return "## Attachments\nThe user attached these files (project-relative; read them before building):\n" + "\n".join(rows)


def mentioned_block(selection: Any) -> str:
    if not selection:
        return ""
    items = selection.get("elements") if isinstance(selection, dict) and "elements" in selection else selection
    items = items if isinstance(items, list) else [items]
    blocks = []
    for it in items[:20]:
        if isinstance(it, str) and "<mentioned-element>" in it:
            blocks.append(it.strip()[:8000])
        elif isinstance(it, dict):
            me = it.get("mentioned_element")
            if isinstance(me, str) and "<mentioned-element>" in me:
                blocks.append(me.strip()[:8000])
                continue
            fields = []
            for label, key in (("board", "board_id"), ("td-id", "td_id"), ("node", "node_id"),
                               ("src", "source_loc"), ("dom", "selector"), ("text", "text")):
                v = it.get(key)
                if v:
                    fields.append(f"{label}: {str(v)[:300]}")
            if fields:
                blocks.append("<mentioned-element>\n" + "\n".join(fields) + "\n</mentioned-element>")
    return "\n".join(blocks)


def comments_block(comments: List[Dict[str, Any]], boards: Dict[str, Any]) -> str:
    if not comments:
        return ""
    parts = [f'<attached-comments mode="scoped" count="{len(comments)}">',
             "The user pinned these comments. Change ONLY the elements listed, only as each note asks. "
             "Leave\neverything else exactly as it is.", ""]
    for c in comments:
        a = c.get("anchor") or {}
        bb = a.get("bbox") or {}
        attrs = [("id", c.get("id")), ("board", c.get("board_id")),
                 ("board_name", (boards.get(c.get("board_id")) or {}).get("name") if isinstance(boards.get(c.get("board_id")), dict) else None),
                 ("file", c.get("file")),
                 ("slide", f"{int(c['slide_index']):02d}" if c.get("slide_index") else None),
                 ("author", c.get("author"))]
        parts.append("<comment " + " ".join(f'{k}="{_attr(v)}"' for k, v in attrs if v) + ">")
        parts.append(f"<note>{_esc(c.get('note'))}</note>")
        tattrs = [("td-id", a.get("td_id")), ("selector", a.get("selector")), ("src", a.get("source_loc")),
                  ("node", a.get("node_id"))]
        if any(bb.get(k) for k in ("w", "h")):
            tattrs.append(("bbox", ",".join(str(int(bb.get(k) or 0)) for k in ("x", "y", "w", "h"))))
        tattrs = [(k, v) for k, v in tattrs if v]
        if tattrs:
            parts.append("<target " + " ".join(f'{k}="{_attr(v)}"' for k, v in tattrs) + " />")
        me = c.get("mentioned_element")
        if me:
            parts.append(str(me).strip()[:8000])
        parts.append("</comment>")
    parts.append("</attached-comments>")
    return "\n".join(parts)


def form_answers_block(answers: Any) -> str:
    if not answers:
        return ""
    if isinstance(answers, dict) and "answers" in answers and isinstance(answers.get("answers"), dict):
        fid, body = str(answers.get("id") or "discovery"), answers["answers"]
    else:
        fid, body = "discovery", answers
    fid = re.sub(r"[^A-Za-z0-9_-]", "", fid)[:64] or "discovery"
    payload = json.dumps(body, ensure_ascii=False).replace("</", "<\\/")
    return f'<form-answers id="{fid}">{payload}</form-answers>'


# ── Build ────────────────────────────────────────────────────────────────

def _stable_values(project: Dict[str, Any], ds_block: str, ds_bundle: str) -> Dict[str, Any]:
    return {
        "project_id": project.get("id") or "",
        "user_org_domain": user_org_domain(),
        "no_design_system": "" if ds_block else "1",
        "ds_bundle": ds_bundle,
        "image_tools": "",
        "ai_helper": "1" if config.get_nested("design.ai_helper.enabled", False) else "",
        "ai_helper_limits": config.get_nested(
            "design.ai_helper.limits", "local model, 1024 output tokens, 20 calls/min"),
        "active_board": "the board named under *Canvas right now*",
        "allowed_cdns": "",
        "starters": starters_table(),
        **SRI,
    }


def style_block(style_id: Optional[str]) -> str:
    """The direction the user picked in the new-project dialog, when no design
    system is attached: the archetype's palette, type and rules become the
    baseline instead of asking the direction question again."""
    if not style_id:
        return ""
    try:
        from services.design import systems
        style = systems.get_style(style_id)
    except Exception:
        return ""
    if not isinstance(style, dict):
        return ""
    body = json.dumps({k: v for k, v in style.items() if k != "id"}, ensure_ascii=False, indent=1)
    fence = "`" * 3
    return "\n".join([
        f"## Visual direction — {style.get('name') or style_id}",
        "The user picked this direction for the project (no design system is attached). Treat its "
        "palette, fonts, radius, density, imagery and motion as the baseline: define them as CSS custom "
        "properties on `:root` (HTML boards) or canvas variables (layer boards) and use only those. Do not "
        "ask the direction question again.",
        "",
        f"{fence}json",
        body[:6000],
        fence,
    ])


def build(project: Dict[str, Any], chat: Dict[str, Any], turn: Dict[str, Any], *,
          project_dir: Path, comments: Optional[List[Dict[str, Any]]] = None,
          turn_number: int = 1, project_turns: int = 0) -> Dict[str, Any]:
    """Returns {"brief", "brief_sha", "turn"} — see the module docstring."""
    kind = project.get("kind") if project.get("kind") in CRAFT_BY_KIND else "prototype"
    kind_skill = turn.get("kind_skill")
    kind_file = f"kinds/{kind_skill if kind_skill in CRAFT_BY_KIND else kind}.md"
    text = turn.get("text") or ""
    engine = turn.get("engine") or chat.get("engine") or "claude_code"
    boards = store._read_json(project_dir / "boards.json") or {}
    is_convert = bool(_CONVERT_RE.search(text))

    ds_block, ds_bundle = design_system_block(project, project_dir)
    values = _stable_values(project, ds_block, ds_bundle)

    brief_parts = [
        f"# TeleDesign brief — {project.get('title') or 'Untitled'}\n"
        f"_(The host rewrites this file each turn; do not edit it.)_",
        load_prompt("charter.md", values),
    ]
    persona = persona_block(project)
    if persona:
        brief_parts.append(persona)
    brief_parts.append(load_prompt("canvas.md", values))
    wants_html = kind in HTML_KINDS or bool(boards) or is_convert
    wants_layer = kind in LAYER_KINDS or is_convert or (project_dir / "doc.fig").is_file()
    if wants_html:
        brief_parts.append(load_prompt("html_boards.md", values))
    if wants_layer:
        brief_parts.append(load_prompt("layer_boards.md", values))
    brief_parts.append(load_prompt(kind_file, values))
    if kind == "slides":
        brief_parts.append(load_prompt("deck.md", values))
    if kind in TWEAK_KINDS or (wants_html and _VARIANT_RE.search(text)):
        brief_parts.append(load_prompt("tweaks.md", values))
    if is_convert:
        brief_parts.append(load_prompt("code_export.md", values))
    if ds_block:
        brief_parts.append(ds_block.strip())
    else:
        style = style_block(turn.get("style_id") or project.get("style_id"))
        if style:
            brief_parts.append(style)
    for craft in ("anti_slop", "accessibility") + tuple(CRAFT_BY_KIND[kind]):
        brief_parts.append(load_prompt(f"craft/{craft}.md", values))
    brief_parts.append("## Engine notes\n" + ENGINE_NOTES.get(engine, ENGINE_NOTES["claude_code"]).rstrip())
    brief = "\n\n---\n\n".join(p for p in brief_parts if p) + "\n"
    brief_sha = hashlib.sha256(brief.encode("utf-8")).hexdigest()

    # ── Turn part ──
    form_answers = form_answers_block(turn.get("form_answers"))
    scoped = bool(comments)
    new_brief = project_turns == 0 or bool(turn.get("new_brief"))
    turn_parts: List[str] = []
    if new_brief and not scoped and not form_answers and not text.lstrip().startswith("<form-answers") \
            and not turn.get("auto"):
        turn_parts.append(load_prompt("discovery.md", values))
    mentioned = mentioned_block(turn.get("selection"))
    if scoped or mentioned:
        turn_parts.append(comments_part_a())

    facts = [
        "## This project",
        f"- Title: {project.get('title') or 'Untitled'} · Kind: {kind} · Turn: {turn_number}",
        f"- Engine: {engine} · Local model: {'true' if turn.get('is_local') else 'false'}",
        f"- Today: {date.today().isoformat()} · User org domain: {values['user_org_domain'] or '(none)'}",
    ]
    if turn.get("effort") in EFFORT_NOTES:
        facts.append(f"- {EFFORT_NOTES[turn['effort']]}")
    turn_parts.append("\n".join(facts))
    active = ""
    sel = turn.get("selection") if isinstance(turn.get("selection"), dict) else {}
    if sel.get("board_id") or sel.get("file"):
        active = f"\nActive board: {sel.get('board_id') or ''} {sel.get('file') or ''}".rstrip()
    turn_parts.append("## Canvas right now\n" + canvas_summary(project_dir) + active)
    cont = chat.get("continuing_from")
    if cont and turn_number == 1 and cont.get("summary"):
        turn_parts.append(f"## Continuing from “{cont.get('title')}”\nThe user continued an earlier chat. "
                          f"Its tail, for context only:\n\n{cont['summary']}")
    if turn.get("references"):
        turn_parts.append("## References\n" + str(turn["references"])[:8000])
    att = attachments_block(project_dir, turn.get("attachments") or [])
    if att:
        turn_parts.append(att)
    if turn.get("web_capture"):
        turn_parts.append(load_prompt("web_capture.md", {**values, "web_capture": str(turn["web_capture"])[:60000]}))
    if mentioned:
        turn_parts.append(mentioned)
    if scoped:
        turn_parts.append(comments_block(comments or [], boards))
    if form_answers:
        turn_parts.append(form_answers)
    if kind_skill == "critique":
        crit = turn.get("critique") if isinstance(turn.get("critique"), dict) else {}
        turn_parts.append(load_prompt("critique.md", {
            k: str(crit.get(k) or d)[:8000] for k, d in (
                ("critic_role", "designer"), ("artifact_ref", "the boards changed most recently"),
                ("brief", "see the user's message"), ("round", "1"), ("previous_scores", "none"))}))
    turn_parts.append("## The user's message\n" + (text.strip() or "(no message — act on the blocks above)"))
    return {"brief": brief, "brief_sha": brief_sha, "turn": "\n\n".join(turn_parts) + "\n"}


def compose(built: Dict[str, Any], *, include_brief: bool) -> str:
    if include_brief:
        return (built["brief"] + "\n---\n\n"
                f"_(This brief is also saved at `{BRIEF_REL}` in your working directory.)_\n\n---\n\n"
                + built["turn"])
    return (f"_(Your standing brief is unchanged since your last turn. It is saved at `{BRIEF_REL}` — "
            f"re-read it if you need the rules.)_\n\n" + built["turn"])


def write_brief(project_dir: Path, brief: str) -> None:
    p = project_dir / ".td" / "brief.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".md.tmp")
    tmp.write_text(brief, encoding="utf-8")
    tmp.replace(p)


# ── Side prompts (not part of the stack) ─────────────────────────────────

def title_prompt(first_message: str, first_reply: str, kind: str) -> str:
    return load_prompt("title.md", {"first_message": first_message[:2000],
                                    "first_reply_excerpt": first_reply[:1200], "project_kind": kind})


def verifier_prompt(**inputs: str) -> str:
    keys = ("files_changed", "console_log", "screenshots", "lint_findings", "pen_problems", "verifier_task")
    values = {k: (inputs.get(k) or "(none)") for k in keys}
    values["verifier_task"] = inputs.get("verifier_task") or ""
    return load_prompt("verifier.md", values)
