"""Structured step handoffs (P2).

Every pipeline step ends with a handoff object::

    {status: done|partial|blocked|failed, summary, decisions[],
     artifacts[{path, kind, description}], open_questions[], next_steps[],
     verdict: pass|fail|unknown}

* Every engine produces it as its structured final answer — the shared
  :data:`HANDOFF_SCHEMA` goes to ``claude --json-schema`` / ``codex exec
  --output-schema`` / ``agy --json-schema <file>`` (agy >= 1.2.11; strict:
  every property required, no extras, which Codex demands).
* Antigravity fallback: when agy's ``result.structured_output`` is missing or
  invalid, a ``.telecode/handoff.json`` in its working directory (the pre-1.2.11
  mechanism; no longer asked for) is used if present — :func:`agy_structured`.
  The handler clears the file before the run and removes it after.
* Missing or invalid → :func:`derive` builds one from the step's final text
  (``status`` / ``verdict`` = ``unknown``, ``derived: true``) instead of failing
  the step.

The next step's prompt gets a ``<handoff>`` block (:func:`render_block`) —
summary, decisions, open questions, next steps, verdict and the artifacts'
stored paths with descriptions — instead of the raw reply; the full reply stays
in ``run_steps`` / ``tasks``. Artifacts are copied to
``data/runs/<run>/artifacts/<step>/`` (:func:`collect_artifacts`), so files an
ephemeral / parallel step wrote survive its session.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("telecode.services.run.handoff")

STATUSES = ("done", "partial", "blocked", "failed")
VERDICTS = ("pass", "fail", "unknown")
AGY_HANDOFF_REL = ".telecode/handoff.json"

SUMMARY_CAP = 16 * 1024
ITEM_CAP = 2000
LIST_CAP = 50
ARTIFACT_FILE_CAP = 50 * 1024 * 1024
ARTIFACT_TOTAL_CAP = 200 * 1024 * 1024
ARTIFACT_COUNT_CAP = 200

_STR_LIST = {"type": "array", "items": {"type": "string"}}

HANDOFF_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "decisions", "artifacts", "open_questions", "next_steps", "items", "verdict"],
    "properties": {
        "status": {"type": "string", "enum": list(STATUSES),
                   "description": "done = goal met; partial = some of it; blocked = cannot proceed without input; failed = could not do it"},
        "summary": {"type": "string", "description": "What was done and the outcome, for the next step (a few sentences)"},
        "decisions": {**_STR_LIST, "description": "Choices made that the next step must respect"},
        "artifacts": {
            "type": "array",
            "description": "Files created or changed that matter to the next step",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "kind", "description"],
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the working directory"},
                    "kind": {"type": "string", "description": "e.g. code, doc, data, report, config"},
                    "description": {"type": "string", "description": "One line: what it is"},
                },
            },
        },
        "open_questions": {**_STR_LIST, "description": "Unresolved questions"},
        "next_steps": {**_STR_LIST, "description": "What should happen next"},
        "items": {**_STR_LIST, "description": "Independent work items for a fan-out (map) step to run one per worker; empty unless asked for"},
        "verdict": {"type": "string", "enum": list(VERDICTS),
                    "description": "Did the step meet its goal? pass / fail / unknown"},
    },
}


def instructions(engine: str) -> str:
    """The <handoff_instructions> block appended to every step prompt."""
    body = ("When you finish this step, report a handoff for whoever continues the work: status "
            "(done | partial | blocked | failed), a short summary of what you did and the outcome, the key "
            "decisions, the artifacts that matter (files you created or changed — path relative to your working "
            "directory, a kind, a one-line description), open questions, next steps, and a verdict "
            "(pass | fail | unknown) on whether the step's goal was met.")
    # All three engines take the schema (agy via --json-schema since 1.2.11), so
    # the wording is shared; the agy file is only read as a fallback (agy_structured).
    body += "\nYour final answer is captured in exactly that structured form."
    return f"<handoff_instructions>\n{body}\n</handoff_instructions>"


# ── Validation ──────────────────────────────────────────────────────────────

def _str(v: Any, cap: int = ITEM_CAP) -> str:
    if v is None:
        return ""
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s.strip()[:cap]


def _str_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v] if v.strip() else []
    if not isinstance(v, list):
        return [_str(v)]
    return [s for s in (_str(x) for x in v[:LIST_CAP]) if s]


def _norm_path(path: str, work_dir: Optional[Path]) -> Optional[str]:
    """Relative, inside work_dir (absolute paths inside it are relativised)."""
    p = (path or "").strip().replace("\\", "/")
    if not p:
        return None
    pp = Path(p)
    if pp.is_absolute():
        if work_dir is None:
            return None
        try:
            p = pp.resolve().relative_to(Path(work_dir).resolve()).as_posix()
        except (ValueError, OSError):
            return None
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if not parts or any(x == ".." for x in parts):
        return None
    return "/".join(parts)


def validate(obj: Any, work_dir: Optional[Path] = None) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Normalise a model-produced handoff. Returns (handoff | None, problems).

    Lenient on shape (a string where a list belongs becomes a one-item list,
    over-long text is capped, artifacts outside the working directory are
    dropped with a note) but a non-object, a missing summary or an unknown
    status/verdict makes it invalid (None)."""
    problems: List[str] = []
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except ValueError:
            return None, ["not JSON"]
    if not isinstance(obj, dict):
        return None, ["not an object"]
    status = _str(obj.get("status")).lower()
    verdict = _str(obj.get("verdict") or "unknown").lower()
    summary = _str(obj.get("summary"), SUMMARY_CAP)
    if status not in STATUSES:
        problems.append(f"status {status!r} not one of {STATUSES}")
    if verdict not in VERDICTS:
        problems.append(f"verdict {verdict!r} not one of {VERDICTS}")
    if not summary:
        problems.append("summary is empty")
    arts: List[Dict[str, str]] = []
    raw_arts = obj.get("artifacts") or []
    if isinstance(raw_arts, dict):
        raw_arts = [raw_arts]
    for a in raw_arts[:LIST_CAP] if isinstance(raw_arts, list) else []:
        if isinstance(a, str):
            a = {"path": a}
        if not isinstance(a, dict):
            continue
        rel = _norm_path(_str(a.get("path")), work_dir)
        if not rel:
            problems.append(f"artifact path {a.get('path')!r} is outside the working directory — dropped")
            continue
        arts.append({"path": rel, "kind": _str(a.get("kind") or "file", 64) or "file",
                     "description": _str(a.get("description"))})
    if status not in STATUSES or verdict not in VERDICTS or not summary:
        return None, problems
    return {
        "status": status, "summary": summary, "decisions": _str_list(obj.get("decisions")),
        "artifacts": arts, "open_questions": _str_list(obj.get("open_questions")),
        "next_steps": _str_list(obj.get("next_steps")), "items": _str_list(obj.get("items")),
        "verdict": verdict, "derived": False,
        **({"notes": problems} if problems else {}),
    }, problems


def derive(text: str, *, step_status: str, error: Optional[str] = None,
           reason: str = "no structured handoff") -> Dict[str, Any]:
    """A handoff built from the final text when the engine gave none (or a bad one)."""
    from services.run.executor import _cap_head_tail
    text = (text or "").strip()
    failed = step_status != "completed"
    summary = _cap_head_tail(text, SUMMARY_CAP) if text else (
        f"Step {step_status}: {error}" if error else f"Step {step_status} without a reply.")
    return {
        "status": "failed" if failed and step_status in ("failed", "budget_exceeded") else "unknown",
        "summary": summary, "decisions": [], "artifacts": [], "open_questions": [], "next_steps": [], "items": [],
        "verdict": "fail" if failed and step_status == "failed" else "unknown",
        "derived": True, "derive_reason": reason,
    }


def resolve(structured: Any, text: str, *, step_status: str, error: Optional[str],
            work_dir: Optional[Path]) -> Dict[str, Any]:
    """Structured output if valid, else a derived handoff (never raises)."""
    if structured is not None:
        ho, problems = validate(structured, work_dir)
        if ho is not None:
            return ho
        logger.info(f"handoff invalid ({'; '.join(problems)}) — deriving from the final text")
        return derive(text, step_status=step_status, error=error,
                      reason="invalid structured handoff: " + "; ".join(problems)[:300])
    return derive(text, step_status=step_status, error=error)


def read_agy_file(work_dir: Path) -> Optional[Any]:
    """Read and remove ``.telecode/handoff.json`` (None when absent/unreadable)."""
    p = Path(work_dir) / AGY_HANDOFF_REL
    if not p.is_file():
        return None
    try:
        raw = p.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    finally:
        try:
            p.unlink()
        except OSError:
            pass
    try:
        return json.loads(raw)
    except ValueError:
        return raw  # validate() reports "not JSON"


def agy_structured(structured: Any, work_dir: Path) -> Optional[Any]:
    """agy's handoff: its ``--json-schema`` answer when that validates, else a
    ``.telecode/handoff.json`` it wrote (fallback), else whatever it returned
    (so :func:`resolve` records why it was invalid). The file is always removed."""
    file_obj = read_agy_file(work_dir)
    if structured is not None and validate(structured, work_dir)[0] is not None:
        return structured
    if file_obj is not None and validate(file_obj, work_dir)[0] is not None:
        logger.info("agy: no valid --json-schema handoff — using .telecode/handoff.json")
        return file_obj
    return structured if structured is not None else file_obj


def clear_agy_file(work_dir: Path) -> None:
    try:
        (Path(work_dir) / AGY_HANDOFF_REL).unlink()
    except OSError:
        pass


# ── Artifacts ───────────────────────────────────────────────────────────────

def artifacts_dir(run_id: str, step_id: str) -> Path:
    import config
    from services.task.safe_paths import validate_id
    return (Path(config._settings_dir()) / "data" / "runs" / validate_id(run_id, "run_id")
            / "artifacts" / validate_id(step_id, "step_id"))


def collect_artifacts(run_id: str, step_id: str, work_dir: Path, handoff: Dict[str, Any],
                      files_changed: Optional[List[Dict[str, Any]]] = None,
                      include_changed: bool = False, dest_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Copy declared artifacts (and, for ephemeral steps, every added/modified
    file) into ``data/runs/<run>/artifacts/<step>/``. Updates
    ``handoff["artifacts"]`` in place with ``stored_path`` / ``bytes`` /
    ``missing``; returns the stored list."""
    dest_root = Path(dest_root) if dest_root is not None else artifacts_dir(run_id, step_id)
    work_dir = Path(work_dir)
    declared = list(handoff.get("artifacts") or [])
    seen = {a["path"] for a in declared}
    if include_changed:
        for f in files_changed or []:
            if f.get("change") in ("added", "modified") and f.get("path") not in seen:
                declared.append({"path": f["path"], "kind": "file", "description": f"{f['change']} by this step",
                                 "undeclared": True})
                seen.add(f["path"])
    total = 0
    out: List[Dict[str, Any]] = []
    for a in declared[:ARTIFACT_COUNT_CAP]:
        rel = a.get("path") or ""
        src = work_dir / rel
        entry = dict(a)
        try:
            if src.is_file():
                size = src.stat().st_size
                if size > ARTIFACT_FILE_CAP or total + size > ARTIFACT_TOTAL_CAP:
                    entry["skipped"] = "too large to keep"
                else:
                    dst = dest_root / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    total += size
                    entry.update({"stored_path": str(dst), "bytes": size})
            elif src.is_dir():
                dst = dest_root / rel
                size = 0
                for f in src.rglob("*"):
                    if f.is_file():
                        fs = f.stat().st_size
                        if fs > ARTIFACT_FILE_CAP or total + size + fs > ARTIFACT_TOTAL_CAP:
                            entry["skipped"] = "partly too large to keep"
                            continue
                        t = dst / f.relative_to(src)
                        t.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(f, t)
                        size += fs
                total += size
                entry.update({"stored_path": str(dst), "bytes": size, "is_dir": True})
            else:
                entry["missing"] = True
        except OSError as exc:
            entry["error"] = str(exc)[:200]
        out.append(entry)
    handoff["artifacts"] = out
    return out


# ── Prompt block ────────────────────────────────────────────────────────────

def _esc(s: str) -> str:
    return str(s).replace('"', "'")


def render_block(o: Dict[str, Any]) -> str:
    """One <handoff> block for a previous step's output dict
    ({step_id, name, status, handoff, files_changed})."""
    ho = o.get("handoff") or {}
    label = o.get("name") or (o.get("step_id") or "")[:8]
    lines = [f'<handoff from="{_esc(label)}" status="{_esc(ho.get("status", "unknown"))}" '
             f'verdict="{_esc(ho.get("verdict", "unknown"))}">']
    if ho.get("summary"):
        lines += ["<summary>", ho["summary"], "</summary>"]
    for key in ("decisions", "open_questions", "next_steps", "items"):
        items = ho.get(key) or []
        if items:
            lines += [f"<{key}>", *(f"- {x}" for x in items), f"</{key}>"]
    arts = [a for a in (ho.get("artifacts") or []) if not a.get("missing")]
    if arts:
        lines.append("<artifacts>")
        for a in arts:
            where = a.get("stored_path") or a.get("path")
            desc = a.get("description") or ""
            lines.append(f"- {where}" + (f" ({a.get('kind')})" if a.get("kind") else "")
                         + (f" — {desc}" if desc else "")
                         + (f" [workspace path: {a['path']}]" if a.get("stored_path") else ""))
        lines.append("</artifacts>")
    files = o.get("files_changed") or []
    if files:
        lines += ["<files_changed>", *(f"{f.get('change', 'modified')}: {f.get('path')}" for f in files),
                  "</files_changed>"]
    lines.append("</handoff>")
    return "\n".join(lines)


def render_prev(prev_outputs: List[Dict[str, Any]]) -> str:
    blocks = [render_block(o) for o in prev_outputs if o.get("handoff") or o.get("files_changed")]
    if not blocks:
        return ""
    if len(blocks) == 1:
        return blocks[0]
    return "<handoffs>\n" + "\n".join(blocks) + "\n</handoffs>"
