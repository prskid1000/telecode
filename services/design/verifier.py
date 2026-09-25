"""Verifier checks callable on demand — the directed-check mode (docs/teledesign-parity.md A "Verifier").

The post-turn verifier (`generate._verifier`) is a *full sweep*: silent on pass,
wakes the designer on failure. This module adds the other mode Claude Design has —
a **directed check**: a caller (an agent through `design_verify`, the REST route
`POST /api/design/projects/{pid}/verify`, or the CLI) names what to check
("does the nav collapse at 375px?") and always gets a report back, pass or fail.

    await run_check(pid, files=None, task="", screenshots=True, layers=None,
                    model=None, turn_id=None, chat_id=None) -> report

The report is built from the same evidence the sweep uses — `render.verify` (console,
blank, overflow, small text, hit targets, WCAG contrast, broken resources, deck
checks) plus the layer-board inspection (`editor_bridge.inspect_layers` →
`pen_problems`). When `design.local_helpers` allows the local model, the
verifier prompt (`prompts/verifier.md`, with `verifier_task` filled) is run over
that evidence and its `<verifier-result>` parsed; otherwise the report says so
(`model.ran: false`) and the calling agent judges the task against the evidence
itself — it is an agent too, and the evidence is what a verifier would look at.
Nothing here starts a turn or edits a file.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from services.design import store

logger = logging.getLogger("telecode.services.design.verifier")

MAX_FILES = 12
_RESULT_RE = re.compile(r'<verifier-result\s+status="(pass|fail)"\s*/?>(.*?)(?:</verifier-result>|$)', re.S)


def default_files(pid: str) -> List[str]:
    """The HTML deliverables to check when the caller names none."""
    d = store.project_dir(pid)
    if not d:
        return []
    out: List[str] = []
    try:
        from services.design import assets as dassets
        for a in dassets.get_assets(pid) or []:
            p = a.get("path") or ""
            if p.lower().endswith(".html") and (d / p).is_file() and p not in out:
                out.append(p)
    except Exception:
        pass
    if not out:
        out = sorted(p.relative_to(d).as_posix() for p in d.glob("*.html"))
    return out[:MAX_FILES]


def _parse_model(reply: Optional[str]) -> Optional[Dict[str, Any]]:
    m = _RESULT_RE.search(reply or "")
    if not m:
        return None
    items: List[Any] = []
    body = (m.group(2) or "").strip()
    if body:
        try:
            items = json.loads(body)
        except json.JSONDecodeError:
            items = []
    items = [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    return {"status": m.group(1), "items": items}


async def _model_pass(pid: str, files: List[str], res: Dict[str, Any], task: str) -> Dict[str, Any]:
    try:
        from services.design import generate, prompt_builder
    except Exception as exc:  # pragma: no cover — both always importable inside telecode
        return {"ran": False, "reason": f"verifier prompt unavailable: {exc}"}
    findings: Any = []
    project = store.get_project(pid) or {}
    if project.get("design_system_id"):
        try:
            from services.design import systems
            import asyncio
            findings = await asyncio.to_thread(systems.lint, store.project_dir(pid), project["design_system_id"])
        except Exception as exc:
            logger.info("verifier: lint failed: %s", exc)
    prompt = prompt_builder.verifier_prompt(
        files_changed=", ".join(files),
        console_log=json.dumps(res.get("console") or {}, ensure_ascii=False)[:20000],
        screenshots="\n".join(res.get("screenshots") or [])[:4000],
        lint_findings=json.dumps(findings, ensure_ascii=False, default=str)[:20000] if findings else "",
        pen_problems=res.get("pen_problems") or "",
        verifier_task=task,
    ) + ("\n\nRender checks already found (confirm, don't repeat):\n" + json.dumps(res.get("issues"))[:10000]
         if res.get("issues") else "")
    complete = getattr(generate, "_local_complete", None)
    reply = await complete(prompt, max_tokens=1500) if complete else None
    if reply is None:
        return {"ran": False, "reason": "no local model (design.local_helpers is off, or the proxy / llama.cpp "
                                        "is not running) — judge the task against the evidence yourself"}
    parsed = _parse_model(reply)
    if not parsed:
        return {"ran": True, "status": None, "items": [], "raw": (reply or "")[:2000],
                "reason": "the model did not return a <verifier-result> block"}
    return {"ran": True, **parsed}


def _publish(pid: str, turn_id: Optional[str], chat_id: Optional[str], payload: Dict[str, Any]) -> None:
    if not turn_id:
        return
    try:
        from services.design import events
        events.publish(pid, "check", {"turn_id": turn_id, **({"chat_id": chat_id} if chat_id else {}),
                                      "stage": "verifier", **payload})
    except Exception:
        logger.debug("verifier: events unavailable", exc_info=True)


async def run_check(pid: str, files: Optional[List[str]] = None, task: str = "", *,
                    screenshots: bool = True, layers: Optional[bool] = None, model: Optional[bool] = None,
                    turn_id: Optional[str] = None, chat_id: Optional[str] = None) -> Dict[str, Any]:
    """Run the verifier now. `task` set = directed check (always reports); empty = full sweep.

    Returns {mode, status: "pass"|"issues"|"fail", task, files, issues, pen_problems,
             layers, screenshots, console, decks, model: {ran, status?, items?, reason?}, answer?}.
    """
    from services.design import render
    if not store.project_dir(pid):
        raise ValueError("project not found")
    task = (task or "").strip()[:4000]
    mode = "directed" if task else "sweep"
    files = [f for f in (files if files else default_files(pid)) if isinstance(f, str) and store.safe_relpath(f)]
    files = files[:MAX_FILES]
    directed = mode == "directed"
    _publish(pid, turn_id, chat_id, {"status": "running", "directed": directed, "task": task})
    res = await render.verify(pid, files, screenshots=screenshots, layers=layers)
    issues = list(res.get("issues") or [])
    use_model = model if model is not None else True
    mres: Dict[str, Any] = {"ran": False, "reason": "not requested"}
    if use_model:
        mres = await _model_pass(pid, files, res, task)
        if mres.get("ran") and mres.get("items"):
            known = {(i.get("file"), i.get("what")) for i in issues}
            for it in mres["items"]:
                if it.get("severity") == "info":
                    continue  # the directed answer, reported separately
                if (it.get("file"), it.get("what")) not in known:
                    issues.append({**it, "check": it.get("check") or "model"})
    answer = None
    if mres.get("ran"):
        answer = next((i for i in mres.get("items") or [] if i.get("severity") == "info"), None)
    serious = [i for i in issues if i.get("severity") in ("blocker", "major")]
    if directed and mres.get("ran") and mres.get("status"):
        status = mres["status"]
    else:
        status = "issues" if issues else "pass"
    report: Dict[str, Any] = {
        "mode": mode, "status": status, "task": task, "files": files, "issues": issues,
        "serious": len(serious), "pen_problems": res.get("pen_problems") or "", "layers": res.get("layers"),
        "screenshots": res.get("screenshots") or [], "console": res.get("console") or {},
        "decks": res.get("decks") or {}, "model": {k: v for k, v in mres.items() if k != "items"},
    }
    if answer:
        report["answer"] = answer
    if directed and not mres.get("ran"):
        report["note"] = ("Directed check: no model pass ran, so the task was not judged here. The evidence above "
                          "(issues, screenshots, console, layer problems) is what the verifier would read — "
                          "answer the task from it, probing further with design_eval_js / design_screenshot.")
    # A sweep that passes stays silent in the UI; a directed check always shows.
    _publish(pid, turn_id, chat_id, {"status": "pass" if status == "pass" else "issues", "directed": directed,
                                     "task": task, "issues": issues[:50]})
    return report
