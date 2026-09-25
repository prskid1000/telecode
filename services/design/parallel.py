"""TeleDesign parallel agents: split / side-by-side / let-it-cook / design jury.

An orchestrator over W1's chat + turn API (docs/teledesign-contract.md §4.1, §11).
Every agent is its own chat (so its own CLI session, visible as a tab in the UI)
and writes under its own file prefix, so siblings never overwrite each other.

Modes
-----
- ``split``        — N agents divide one brief; agent i owns slice i and writes under its prefix.
- ``side_by_side`` — N agents each do the whole brief (engines may differ) for comparison.
- ``let_it_cook``  — N variants of one brief along an axis (``variant``: layout | style); with
                     ``iterate`` each agent gets one self-review pass after its first draft.
- ``jury``         — one generator → ``count`` critics in parallel (``prompts/critique.md``,
                     JSON scores) → reviser, until the critics' mean ``overall`` ≥ threshold
                     (default 8.0) or ``max_rounds`` critique rounds (default 3) have run.

Transport: the proxy's own REST surface over HTTP (``http://127.0.0.1:<proxy.port>``). This
module runs *inside* the proxy loop, and going through HTTP keeps W1's validation, 409
turn-queueing and event publishing in one place instead of re-implementing them here.
Tests swap the transport by passing ``api=`` (any object with ``DesignAPI``'s methods).

Run records persist to ``data/design/projects/<pid>/agents/<run_id>.json`` and are
re-published to the UI as ``agents`` events on every state change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

import config

logger = logging.getLogger("telecode.services.design.parallel")

MODES = ("split", "side_by_side", "let_it_cook", "jury")
ENGINES = ("claude_code", "codex", "antigravity")
TERMINAL = ("done", "failed", "cancelled", "error")
CRITIC_ROLES = ("designer", "brand", "a11y", "copy", "ux")
DIMENSIONS = ("coherence", "hierarchy", "craft", "function", "specificity")

JURY_THRESHOLD = 8.0
JURY_MAX_ROUNDS = 3

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def proxy_base_url() -> str:
    """The proxy's own origin. Read every call — `proxy.port` is hot-reloadable."""
    return f"http://127.0.0.1:{int(config.get_nested('proxy.port', 1235))}"


def max_agents() -> int:
    try:
        return max(1, min(int(config.get_nested("design.max_parallel_agents", 6)), 12))
    except (TypeError, ValueError):
        return 6


# ── HTTP transport ───────────────────────────────────────────────────────

class DesignAPIError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


class DesignAPI:
    """Thin async client for `/api/design/*` on the running proxy.

    Shared by the parallel orchestrator and the Telegram `/design` command. A fresh
    `aiohttp.ClientSession` per call keeps it loop-agnostic (the bot and the proxy
    share a loop today, but nothing here depends on that).
    """

    def __init__(self, base_url: Optional[str] = None, timeout: float = 30.0):
        self._base = base_url
        self._timeout = timeout

    @property
    def base(self) -> str:
        return self._base or proxy_base_url()

    async def request(self, method: str, path: str, *, json_body: Any = None,
                      params: Optional[Dict[str, Any]] = None, data: Optional[bytes] = None,
                      content_type: Optional[str] = None, raw: bool = False,
                      timeout: Optional[float] = None) -> Any:
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        to = aiohttp.ClientTimeout(total=timeout or self._timeout)
        async with aiohttp.ClientSession(timeout=to) as s:
            async with s.request(method, self.base + path, json=json_body, params=params,
                                 data=data, headers=headers) as r:
                body = await r.read()
                if r.status >= 400:
                    msg = body.decode("utf-8", "replace")[:500]
                    try:
                        msg = json.loads(msg).get("error", msg)
                    except Exception:
                        pass
                    raise DesignAPIError(r.status, str(msg))
                if raw:
                    return body
                if not body:
                    return {}
                try:
                    return json.loads(body)
                except ValueError:
                    return {"text": body.decode("utf-8", "replace")}

    # Projects / chats / turns (W1 §4.1)
    async def create_project(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return (await self.request("POST", "/api/design/projects", json_body=data))["project"]

    async def get_project(self, pid: str) -> Dict[str, Any]:
        return (await self.request("GET", f"/api/design/projects/{pid}"))["project"]

    async def list_projects(self) -> List[Dict[str, Any]]:
        return (await self.request("GET", "/api/design/projects")).get("projects", [])

    async def list_chats(self, pid: str) -> List[Dict[str, Any]]:
        return (await self.request("GET", f"/api/design/projects/{pid}/chats")).get("chats", [])

    async def create_chat(self, pid: str, data: Dict[str, Any]) -> Dict[str, Any]:
        return (await self.request("POST", f"/api/design/projects/{pid}/chats", json_body=data))["chat"]

    async def list_turns(self, pid: str, cid: str, after: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"after": after} if after else None
        res = await self.request("GET", f"/api/design/projects/{pid}/chats/{cid}/turns", params=params)
        return res.get("turns", [])

    async def post_turn(self, pid: str, cid: str, body: Dict[str, Any],
                        busy_wait: float = 600.0) -> Dict[str, Any]:
        """Start a turn; on 409 (a turn is already running in this chat) wait and retry."""
        deadline = time.monotonic() + busy_wait
        while True:
            try:
                res = await self.request("POST", f"/api/design/projects/{pid}/chats/{cid}/turns",
                                         json_body=body)
                return res["turn"]
            except DesignAPIError as e:
                if e.status == 409 and time.monotonic() < deadline:
                    await asyncio.sleep(2.0)
                    continue
                raise

    async def stop_chat(self, pid: str, cid: str) -> None:
        await self.request("POST", f"/api/design/projects/{pid}/chats/{cid}/stop", json_body={})

    async def list_comments(self, pid: str) -> List[Dict[str, Any]]:
        res = await self.request("GET", f"/api/design/projects/{pid}/comments")
        return res.get("comments", res if isinstance(res, list) else [])

    async def get_file(self, pid: str, path: str) -> bytes:
        return await self.request("GET", f"/api/design/projects/{pid}/files/{path}", raw=True)

    async def wait_turn(self, pid: str, cid: str, turn: Dict[str, Any], *, poll: float = 1.0,
                        timeout: float = 3600.0) -> Dict[str, Any]:
        """Block until the turn started by `post_turn` reaches a terminal status.

        `post_turn` may return either the assistant turn being generated or the user
        turn that queued it (the contract leaves that open), so: watch the returned
        record if it is the assistant's, else the first assistant turn after it.
        Returns the terminal assistant record (or the user record if *it* failed).
        """
        tid = turn.get("id")
        deadline = time.monotonic() + timeout
        while True:
            turns = await self.list_turns(pid, cid)
            found = _resolve_assistant(turns, tid, turn)
            if found is not None and found.get("status") in TERMINAL:
                return found
            if time.monotonic() > deadline:
                raise asyncio.TimeoutError(f"turn {tid} did not finish in {timeout:.0f}s")
            await asyncio.sleep(poll)


def _resolve_assistant(turns: List[Dict[str, Any]], tid: Optional[str],
                       started: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    idx = next((i for i, t in enumerate(turns) if t.get("id") == tid), None)
    if idx is None:
        return None
    rec = turns[idx]
    if rec.get("role", started.get("role")) != "user":
        return rec
    if rec.get("status") in ("failed", "cancelled", "error"):
        return rec
    for t in turns[idx + 1:]:
        if t.get("role") == "assistant":
            return t
    return None


# ── Prompt text for each agent ───────────────────────────────────────────

def agent_prefix(mode: str, run_id: str, index: int) -> str:
    short = {"split": "split", "side_by_side": "sbs", "let_it_cook": "cook", "jury": "jury"}[mode]
    return f"agents/{short}-{run_id[:6]}/a{index + 1}"


_LAYOUT_HINTS = (
    "a classic, conventional structure executed exceptionally well",
    "an asymmetric, editorial layout with a strong focal element",
    "a dense, information-rich grid",
    "a sparse, spacious composition with one idea per screen",
    "a card/modular system that could scale to many items",
    "an unexpected structure — break one convention on purpose and justify it",
)
_STYLE_HINTS = (
    "restrained and neutral — let typography carry it",
    "bold colour and high contrast",
    "soft, warm and approachable",
    "technical and precise, monospace accents",
    "editorial, with serif display type",
    "playful, with one signature motion or illustration idea",
)


def build_agent_prompt(mode: str, prompt: str, index: int, count: int, prefix: str, *,
                       variant: Optional[str] = None, part: Optional[str] = None) -> str:
    head = [f"<parallel-agent mode=\"{mode}\" index=\"{index + 1}\" of=\"{count}\">"]
    head.append(f"Write every file you create under `{prefix}/` (create it). Do not modify files "
                f"outside that folder — {count - 1} sibling agent(s) are working in this project "
                f"at the same time, each in its own folder.")
    if mode == "split":
        if part:
            head.append(f"You own this part of the brief, and only this part: {part}")
        else:
            head.append(f"Split the brief below into {count} roughly equal parts in the order it "
                        f"lists its screens/sections/pages; you own part {index + 1} of {count}. "
                        f"Build only your part, but keep names, tokens and navigation consistent "
                        f"with the whole so the parts can be joined.")
    elif mode == "side_by_side":
        head.append("Do the whole brief on your own. The user will compare your result side by "
                    "side with the other agents' results, so make your strongest attempt.")
    elif mode == "let_it_cook":
        axis = variant if variant in ("layout", "style") else "layout"
        hints = _LAYOUT_HINTS if axis == "layout" else _STYLE_HINTS
        head.append(f"You are variant {index + 1} of {count}, varying the **{axis}**. Take this "
                    f"direction: {hints[index % len(hints)]}. It must be clearly distinct from "
                    f"the siblings; keep the content of the brief the same.")
    head.append("Don't ask clarifying questions in this turn — make reasonable decisions and "
                "note them briefly at the end of your reply.")
    head.append("</parallel-agent>")
    return "\n".join(head) + "\n\n" + prompt


def build_iterate_prompt(prefix: str) -> str:
    return (f"Review your own work under `{prefix}/` against the brief: open it, check hierarchy, "
            f"spacing, copy and states, and fix the three most important problems you find. "
            f"Stay inside `{prefix}/`. End with one line per fix.")


def load_critique_template() -> str:
    text = (_PROMPTS_DIR / "critique.md").read_text(encoding="utf-8")
    # Drop the licence comment at the top — it is for maintainers, not the model.
    return re.sub(r"^\s*<!--.*?-->\s*", "", text, count=1, flags=re.S)


def critique_fields(role: str, artifact_ref: str, brief: str, round_no: int,
                    previous: Optional[List[Dict[str, Any]]]) -> Dict[str, str]:
    """The critique.md placeholders, as W1's turn body field `critique` carries them."""
    prev = "none"
    if previous:
        prev = json.dumps([{"role": p.get("role"), "overall": p.get("overall"),
                            "must_fix": p.get("must_fix", [])} for p in previous],
                          ensure_ascii=False)
    return {"critic_role": role, "artifact_ref": artifact_ref, "brief": brief,
            "round": str(round_no), "previous_scores": prev}


def build_critique_prompt(role: str, artifact_ref: str, brief: str, round_no: int,
                          previous: Optional[List[Dict[str, Any]]]) -> str:
    values = critique_fields(role, artifact_ref, brief, round_no, previous)
    out = load_critique_template()
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", v)
    left = re.findall(r"\{\{[#^/]?[a-z_]+\}\}", out)
    if left:  # README §5: an unknown placeholder is a composer bug — fail loudly.
        raise ValueError(f"critique.md: unfilled placeholders {sorted(set(left))}")
    return out


def build_revise_prompt(prefix: str, round_no: int, critiques: List[Dict[str, Any]],
                        mean: float, threshold: float) -> str:
    fixes: List[str] = []
    seen = set()
    for c in critiques:
        for m in c.get("must_fix") or []:
            if not isinstance(m, dict):
                m = {"where": "", "what": str(m)}
            key = (m.get("where", ""), m.get("what", ""))
            if key in seen:
                continue
            seen.add(key)
            fixes.append(f"- [{c.get('role')}] {m.get('where', '')}: {m.get('what', '')}")
    verdicts = "\n".join(f"- {c.get('role')} ({c.get('overall')}): {c.get('verdict', '')}"
                         for c in critiques)
    keep = sorted({k for c in critiques for k in (c.get("keep") or []) if isinstance(k, str)})
    return (f"<jury-feedback round=\"{round_no}\" mean=\"{mean:.1f}\" target=\"{threshold:.1f}\">\n"
            f"Verdicts:\n{verdicts}\n\nMust fix (highest impact first):\n"
            + ("\n".join(fixes[:12]) or "- (none listed)")
            + ("\n\nKeep: " + "; ".join(keep[:8]) if keep else "")
            + f"\n</jury-feedback>\n\nRevise the artifact under `{prefix}/` to address the must-fix "
              f"items. Keep what the jury said to keep. Stay inside `{prefix}/`. Don't ask questions.")


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def parse_critique(text: str, role: str = "") -> Optional[Dict[str, Any]]:
    """Pull the critic's JSON object out of its reply; None when there is none.

    Tolerates a fenced block or prose around the object. `overall` is recomputed from
    the five dimension scores when missing or inconsistent (critique.md §2: the mean,
    one decimal), so a critic can't inflate it.
    """
    if not text:
        return None
    candidates = [m.group(1) for m in _JSON_FENCE_RE.finditer(text)]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except ValueError:
            continue
        if not isinstance(obj, dict) or not isinstance(obj.get("scores"), dict):
            continue
        vals = []
        for d in DIMENSIONS:
            s = obj["scores"].get(d)
            s = s.get("score") if isinstance(s, dict) else s
            try:
                vals.append(max(0.0, min(10.0, float(s))))
            except (TypeError, ValueError):
                pass
        if not vals:
            continue
        obj["overall"] = round(sum(vals) / len(vals), 1)
        obj.setdefault("role", role)
        obj.setdefault("must_fix", [])
        return obj
    return None


# ── Usage aggregation ────────────────────────────────────────────────────

_USAGE_KEYS = ("input", "output", "cache_read", "cost_usd", "duration_ms")


def empty_usage() -> Dict[str, float]:
    return {k: 0 for k in _USAGE_KEYS}


def add_usage(total: Dict[str, float], usage: Optional[Dict[str, Any]]) -> None:
    for k in _USAGE_KEYS:
        try:
            total[k] = round(total.get(k, 0) + float((usage or {}).get(k) or 0), 6)
        except (TypeError, ValueError):
            pass


# ── Runs ─────────────────────────────────────────────────────────────────

_runs: Dict[str, Dict[str, Any]] = {}
_tasks: Dict[str, asyncio.Task] = {}


def _runs_dir(pid: str) -> Optional[Path]:
    from services.design import store
    d = store.project_dir(pid)
    return d / "agents" if d else None


def _persist(run: Dict[str, Any]) -> None:
    run["updated_at"] = _now_iso()
    try:
        d = _runs_dir(run["project_id"])
        if d:
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / f"{run['id']}.json.tmp"
            tmp.write_text(json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(d / f"{run['id']}.json")
    except Exception:
        logger.exception("parallel: could not persist run %s", run.get("id"))
    try:
        from services.design import events
        events.publish(run["project_id"], "agents", {"run": public_run(run)})
    except Exception:
        pass


def public_run(run: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in run.items() if not k.startswith("_")}


def get_run(pid: str, run_id: str) -> Optional[Dict[str, Any]]:
    if not _RUN_ID_RE.match(run_id or ""):
        return None
    run = _runs.get(run_id)
    if run and run["project_id"] == pid:
        return run
    d = _runs_dir(pid)
    if not d:
        return None
    try:
        return json.loads((d / f"{run_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_runs(pid: str) -> List[Dict[str, Any]]:
    d = _runs_dir(pid)
    out: Dict[str, Dict[str, Any]] = {}
    if d and d.is_dir():
        for f in d.glob("*.json"):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
                out[rec["id"]] = rec
            except (OSError, ValueError, KeyError):
                continue
    for rid, run in _runs.items():
        if run["project_id"] == pid:
            out[rid] = run
    return sorted((public_run(r) for r in out.values()), key=lambda r: r.get("created_at", ""),
                  reverse=True)


class SpecError(ValueError):
    pass


def validate_spec(body: Dict[str, Any]) -> Dict[str, Any]:
    mode = body.get("mode")
    if mode not in MODES:
        raise SpecError(f"mode must be one of {', '.join(MODES)}")
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise SpecError("prompt is required")
    if len(prompt) > 200_000:
        raise SpecError("prompt too long")
    cap = max_agents()
    default_count = 3 if mode == "jury" else 2
    try:
        count = int(body.get("count") or default_count)
    except (TypeError, ValueError):
        raise SpecError("count must be an integer")
    upper = min(cap, len(CRITIC_ROLES)) if mode == "jury" else cap
    lower = 1 if mode in ("jury", "split", "side_by_side") else 2
    if not lower <= count <= upper:
        raise SpecError(f"count must be between {lower} and {upper} for {mode}")
    engines = body.get("engines") or []
    if not isinstance(engines, list) or any(e not in ENGINES for e in engines):
        raise SpecError(f"engines must be a list of {', '.join(ENGINES)}")
    variant = body.get("variant")
    if variant is not None and variant not in ("layout", "style"):
        raise SpecError("variant must be layout or style")
    parts = body.get("parts")
    if parts is not None and (not isinstance(parts, list) or not all(isinstance(p, str) for p in parts)):
        raise SpecError("parts must be a list of strings")
    spec = {
        "mode": mode, "count": count, "prompt": prompt, "engines": engines,
        "variant": variant, "iterate": bool(body.get("iterate", False)),
        "parts": parts, "is_local": body.get("is_local"), "effort": body.get("effort"),
    }
    if mode == "jury":
        try:
            spec["threshold"] = float(body.get("threshold", JURY_THRESHOLD))
            spec["max_rounds"] = max(1, min(int(body.get("max_rounds", JURY_MAX_ROUNDS)), 5))
        except (TypeError, ValueError):
            raise SpecError("threshold/max_rounds must be numbers")
        roles = body.get("roles") or list(CRITIC_ROLES[:count])
        if not isinstance(roles, list) or any(r not in CRITIC_ROLES for r in roles):
            raise SpecError(f"roles must be drawn from {', '.join(CRITIC_ROLES)}")
        spec["roles"] = roles[:count]
        crit_engine = body.get("critic_engine")
        if crit_engine is not None and crit_engine not in ENGINES:
            raise SpecError("critic_engine is not a known engine")
        spec["critic_engine"] = crit_engine
        cl = body.get("critic_is_local")
        # Default follows design.local_helpers (off): nothing loads the local model unasked.
        spec["critic_is_local"] = (bool(config.get_nested("design.jury.critics_local",
                                                          config.get_nested("design.local_helpers", False)))
                                   if cl is None else bool(cl))
    return spec


def _engine_for(spec: Dict[str, Any], i: int) -> str:
    engines = spec.get("engines") or []
    if engines:
        return engines[i % len(engines)]
    return config.get_nested("design.default_engine", "claude_code")


async def start_run(pid: str, body: Dict[str, Any], api: Optional[Any] = None) -> Dict[str, Any]:
    """Validate, create the agents' chats, and launch the orchestration task.

    Returns the run record (chats already exist, turns are in flight).
    """
    spec = validate_spec(body)
    api = api or DesignAPI()
    run_id = uuid.uuid4().hex
    run: Dict[str, Any] = {
        "id": run_id, "project_id": pid, "mode": spec["mode"], "status": "starting",
        "spec": {k: v for k, v in spec.items() if k != "prompt"},
        "prompt": spec["prompt"][:4000],
        "agents": [], "critics": [], "rounds": [], "usage": empty_usage(),
        "result": None, "error": None, "created_at": _now_iso(), "finished_at": None,
    }
    n_agents = 1 if spec["mode"] == "jury" else spec["count"]
    for i in range(n_agents):
        engine = _engine_for(spec, i)
        prefix = agent_prefix(spec["mode"], run_id, i)
        label = {"split": "Split", "side_by_side": "Side by side", "let_it_cook": "Variant",
                 "jury": "Jury · generator"}[spec["mode"]]
        chat_body = {"title": f"{label} {i + 1}/{n_agents}" if n_agents > 1 else label,
                     "engine": engine}
        if spec.get("is_local") is not None:
            chat_body["is_local"] = bool(spec["is_local"])
        if spec.get("effort"):
            chat_body["effort"] = spec["effort"]
        chat = await api.create_chat(pid, chat_body)
        run["agents"].append({"index": i, "chat_id": chat["id"], "engine": engine, "prefix": prefix,
                              "status": "queued", "turns": [], "usage": empty_usage(),
                              "changed_files": [], "error": None})
    _runs[run_id] = run
    _persist(run)
    _tasks[run_id] = asyncio.ensure_future(_drive(run, spec, api))
    return run


async def stop_run(pid: str, run_id: str, api: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    run = get_run(pid, run_id)
    if not run:
        return None
    api = api or DesignAPI()
    task = _tasks.get(run_id)
    if task and not task.done():
        task.cancel()
    for a in list(run.get("agents", [])) + list(run.get("critics", [])):
        if a.get("status") in ("queued", "running"):
            try:
                await api.stop_chat(pid, a["chat_id"])
            except Exception as e:
                logger.warning("parallel: stop chat %s failed: %s", a.get("chat_id"), e)
            a["status"] = "cancelled"
    if run.get("status") not in ("done", "failed", "converged", "max_rounds"):
        run["status"] = "stopped"
        run["finished_at"] = _now_iso()
    _persist(run)
    return run


async def _run_turn(api: Any, run: Dict[str, Any], who: Dict[str, Any], text: str,
                    *, extra: Optional[Dict[str, Any]] = None,
                    fallback_text: Optional[str] = None) -> Dict[str, Any]:
    """Post one turn in `who`'s chat, wait for it, fold its usage into the run.

    `extra` holds optional body fields (the critique skill); if the server rejects them
    with a 400, the turn is re-sent without them, as `fallback_text` when given.
    """
    pid = run["project_id"]
    body: Dict[str, Any] = {"text": text}
    if who.get("engine"):
        body["engine"] = who["engine"]
    if extra:
        body.update(extra)
    who["status"] = "running"
    _persist(run)
    try:
        started = await api.post_turn(pid, who["chat_id"], body)
    except DesignAPIError as e:
        if e.status == 400 and extra:
            bare = {k: v for k, v in body.items() if k not in extra}
            if fallback_text:
                bare["text"] = fallback_text
            started = await api.post_turn(pid, who["chat_id"], bare)
        else:
            raise
    who["turns"].append(started.get("id"))
    timeout = float(config.get_nested("design.agent_turn_timeout_sec", 3600))
    final = await api.wait_turn(pid, who["chat_id"], started, timeout=timeout)
    add_usage(who["usage"], final.get("usage"))
    add_usage(run["usage"], final.get("usage"))
    for f in final.get("changed_files") or []:
        if f not in who["changed_files"]:
            who["changed_files"].append(f)
    who["status"] = "idle" if final.get("status") == "done" else final.get("status", "failed")
    if final.get("status") != "done":
        who["error"] = final.get("error") or f"turn {final.get('status')}"
    _persist(run)
    return final


async def _drive(run: Dict[str, Any], spec: Dict[str, Any], api: Any) -> None:
    run["status"] = "running"
    _persist(run)
    try:
        if spec["mode"] == "jury":
            await _drive_jury(run, spec, api)
        else:
            await _drive_parallel(run, spec, api)
    except asyncio.CancelledError:
        run["status"] = "stopped"
        run["finished_at"] = _now_iso()
        _persist(run)
        raise
    except Exception as e:
        logger.exception("parallel: run %s failed", run["id"])
        run["status"] = "failed"
        run["error"] = str(e)
        run["finished_at"] = _now_iso()
        _persist(run)
    finally:
        _tasks.pop(run["id"], None)


async def _drive_parallel(run: Dict[str, Any], spec: Dict[str, Any], api: Any) -> None:
    count = len(run["agents"])
    parts = spec.get("parts") or []

    async def one(agent: Dict[str, Any]) -> None:
        i = agent["index"]
        text = build_agent_prompt(spec["mode"], spec["prompt"], i, count, agent["prefix"],
                                  variant=spec.get("variant"),
                                  part=parts[i] if i < len(parts) else None)
        final = await _run_turn(api, run, agent, text)
        if spec.get("iterate") and final.get("status") == "done":
            await _run_turn(api, run, agent, build_iterate_prompt(agent["prefix"]))
        agent["status"] = "done" if agent.get("error") is None else "failed"
        _persist(run)

    results = await asyncio.gather(*(one(a) for a in run["agents"]), return_exceptions=True)
    for agent, res in zip(run["agents"], results):
        if isinstance(res, asyncio.CancelledError):
            raise res
        if isinstance(res, Exception):
            agent["status"] = "failed"
            agent["error"] = str(res)
    ok = [a for a in run["agents"] if a["status"] == "done"]
    run["status"] = "done" if ok else "failed"
    run["result"] = {"completed": len(ok), "failed": len(run["agents"]) - len(ok),
                     "prefixes": [a["prefix"] for a in ok]}
    run["finished_at"] = _now_iso()
    _persist(run)


def _artifact_ref(gen: Dict[str, Any]) -> str:
    files = gen.get("changed_files") or []
    preview_port = int(config.get_nested("design.preview_port", 1237))
    lines = [f"folder `{gen['prefix']}/`"]
    html = [f for f in files if f.endswith(".html")]
    if files:
        lines.append("files: " + ", ".join(f"`{f}`" for f in files[:40]))
    for f in html[:6]:
        lines.append(f"preview: http://127.0.0.1:{preview_port}/p/{{project_id}}/{f}")
    return "; ".join(lines)


async def _drive_jury(run: Dict[str, Any], spec: Dict[str, Any], api: Any) -> None:
    pid = run["project_id"]
    gen = run["agents"][0]
    threshold = spec["threshold"]
    first = build_agent_prompt("jury", spec["prompt"], 0, 1, gen["prefix"])
    final = await _run_turn(api, run, gen, first)
    if final.get("status") != "done":
        raise RuntimeError(f"generator turn {final.get('status')}: {final.get('error')}")

    # One chat per critic role for the whole run, so round 2+ critics remember round 1.
    critic_engine = spec.get("critic_engine") or config.get_nested("design.default_engine",
                                                                  "claude_code")
    for role in spec["roles"]:
        chat = await api.create_chat(pid, {"title": f"Jury · {role}", "engine": critic_engine,
                                           "is_local": spec["critic_is_local"]})
        run["critics"].append({"role": role, "chat_id": chat["id"], "engine": critic_engine,
                               "status": "queued", "turns": [], "usage": empty_usage(),
                               "changed_files": [], "error": None})
    _persist(run)

    previous: Optional[List[Dict[str, Any]]] = None
    for round_no in range(1, spec["max_rounds"] + 1):
        ref = _artifact_ref(gen).replace("{project_id}", pid)

        async def critique(critic: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            fields = critique_fields(critic["role"], ref, spec["prompt"][:6000], round_no, previous)
            # W1's prompt builder renders critique.md from `critique` when kind_skill is
            # "critique"; the fully rendered template is the fallback for a server without it.
            res = await _run_turn(
                api, run, critic,
                f"Review the artifact as the {critic['role']} juror (round {round_no}). "
                f"Do not edit any files. Reply with exactly one JSON object as specified.",
                extra={"kind_skill": "critique", "critique": fields},
                fallback_text=build_critique_prompt(critic["role"], ref, spec["prompt"][:6000],
                                                    round_no, previous))
            parsed = parse_critique(res.get("text") or "", critic["role"])
            critic["status"] = "idle"
            if parsed is None:
                critic["error"] = "no parseable JSON in critique"
            return parsed

        got = await asyncio.gather(*(critique(c) for c in run["critics"]), return_exceptions=True)
        for g in got:
            if isinstance(g, asyncio.CancelledError):
                raise g
        scores = [g for g in got if isinstance(g, dict)]
        mean = round(sum(s["overall"] for s in scores) / len(scores), 2) if scores else 0.0
        passed = bool(scores) and mean >= threshold
        run["rounds"].append({
            "round": round_no, "mean": mean, "passed": passed, "valid_critiques": len(scores),
            "critiques": [{"role": s.get("role"), "overall": s.get("overall"),
                           "verdict": s.get("verdict", ""), "must_fix": s.get("must_fix", []),
                           "scores": {d: (s["scores"].get(d) or {}).get("score")
                                      if isinstance(s["scores"].get(d), dict)
                                      else s["scores"].get(d) for d in DIMENSIONS}}
                          for s in scores],
        })
        _persist(run)
        if not scores:
            raise RuntimeError(f"round {round_no}: no critic returned a parseable score")
        if passed:
            run["status"] = "converged"
            break
        if round_no == spec["max_rounds"]:
            run["status"] = "max_rounds"
            break
        final = await _run_turn(api, run, gen, build_revise_prompt(gen["prefix"], round_no, scores,
                                                                   mean, threshold))
        if final.get("status") != "done":
            raise RuntimeError(f"reviser turn {final.get('status')}: {final.get('error')}")
        previous = scores

    for c in run["critics"]:
        c["status"] = "done"
    gen["status"] = "done"
    last = run["rounds"][-1]
    run["result"] = {"final_mean": last["mean"], "rounds": len(run["rounds"]),
                     "threshold": threshold, "passed": last["passed"], "prefix": gen["prefix"]}
    run["finished_at"] = _now_iso()
    _persist(run)
