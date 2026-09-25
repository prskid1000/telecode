"""Event ingress for triggers: bearer-token webhooks and GitHub webhooks.

* ``POST /api/triggers/{id}/fire`` — ``Authorization: Bearer <token>`` (the
  trigger's own token, compared in constant time). The request body (JSON or
  text, ≤ 256 KB) becomes the payload, always wrapped as untrusted.
* ``POST /api/triggers/{id}/github`` — GitHub's ``X-Hub-Signature-256``
  (HMAC-SHA256 of the raw body with the trigger's secret) is required and
  verified; ``ping`` answers pong; the event must pass the trigger's filters
  (event names, branches, authors, labels — each empty = any). The payload
  handed to the agent is a summary plus the (truncated) event JSON.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Dict, List, Optional, Tuple

MAX_BODY = 256 * 1024


def check_bearer(rec: Dict[str, Any], header: Optional[str]) -> bool:
    wh = ((rec.get("events") or {}).get("webhook") or {})
    token = wh.get("token") or ""
    if not wh.get("enabled") or not token or not header:
        return False
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(value.strip().encode(), token.encode())


def check_github_signature(rec: Dict[str, Any], body: bytes, header: Optional[str]) -> bool:
    gh = ((rec.get("events") or {}).get("github") or {})
    secret = gh.get("secret") or ""
    if not gh.get("enabled") or not secret or not header or not header.startswith("sha256="):
        return False
    want = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want.encode(), header.strip().encode())


def parse_body(raw: bytes, content_type: str = "") -> Any:
    text = raw.decode("utf-8", "replace")
    if "json" in (content_type or "") or text.lstrip().startswith(("{", "[")):
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text


def _branch(event: str, p: Dict[str, Any]) -> Optional[str]:
    ref = p.get("ref")
    if isinstance(ref, str) and ref.startswith("refs/heads/"):
        return ref[len("refs/heads/"):]
    pr = p.get("pull_request") or {}
    if pr:
        return ((pr.get("base") or {}).get("ref")) or None
    wr = p.get("workflow_run") or {}
    if wr.get("head_branch"):
        return wr["head_branch"]
    return ref if isinstance(ref, str) else None


def _author(p: Dict[str, Any]) -> Optional[str]:
    for path in (("pull_request", "user", "login"), ("issue", "user", "login"), ("comment", "user", "login"),
                 ("sender", "login"), ("pusher", "name")):
        cur: Any = p
        for k in path:
            cur = cur.get(k) if isinstance(cur, dict) else None
        if isinstance(cur, str) and cur:
            return cur
    return None


def _labels(p: Dict[str, Any]) -> List[str]:
    out = []
    for obj in (p.get("pull_request") or {}, p.get("issue") or {}):
        for lb in obj.get("labels") or []:
            if isinstance(lb, dict) and lb.get("name"):
                out.append(lb["name"])
    if isinstance(p.get("label"), dict) and p["label"].get("name"):
        out.append(p["label"]["name"])
    return out


def github_filter(rec: Dict[str, Any], event: str, payload: Any) -> Tuple[bool, str]:
    """(passes, reason when it does not)."""
    gh = ((rec.get("events") or {}).get("github") or {})
    p = payload if isinstance(payload, dict) else {}
    events = [e.lower() for e in gh.get("events") or []]
    if events:
        action = str(p.get("action") or "")
        if event.lower() not in events and f"{event}.{action}".lower() not in events:
            return False, f"event {event}{'.' + action if action else ''} not in {events}"
    if gh.get("branches"):
        b = _branch(event, p)
        if b not in gh["branches"]:
            return False, f"branch {b!r} not in {gh['branches']}"
    if gh.get("authors"):
        a = _author(p)
        if a not in gh["authors"]:
            return False, f"author {a!r} not in {gh['authors']}"
    if gh.get("labels"):
        have = set(_labels(p))
        if not have & set(gh["labels"]):
            return False, f"none of the labels {sorted(have)} in {gh['labels']}"
    return True, ""


def github_payload(event: str, payload: Any, delivery: Optional[str] = None) -> Dict[str, Any]:
    p = payload if isinstance(payload, dict) else {}
    repo = (p.get("repository") or {}).get("full_name")
    obj = p.get("pull_request") or p.get("issue") or {}
    summary = {k: v for k, v in {
        "event": event, "action": p.get("action"), "delivery": delivery, "repository": repo,
        "branch": _branch(event, p), "author": _author(p), "labels": _labels(p) or None,
        "title": obj.get("title") or (p.get("head_commit") or {}).get("message"),
        "url": obj.get("html_url") or (p.get("compare") if isinstance(p.get("compare"), str) else None),
        "number": obj.get("number"),
    }.items() if v not in (None, "", [])}
    raw = json.dumps(p, ensure_ascii=False, default=str)
    if len(raw) > 48 * 1024:
        raw = raw[:48 * 1024] + " …(truncated)"
    return {"summary": summary, "event_json": raw}
