"""GitHub import for design systems: URL → owner/repo/ref/path → tree → selected files.

The flow the design_system kind uses ("the tree is a menu, not the meal"): list the tree under a
path prefix, pick the handful of files that carry tokens and components, import only those, read
them, lift exact values.

Transport: the `gh` CLI when it is installed **and already authenticated** (`gh auth status`; we
never log in on the user's behalf) — that gives private repos and a 5 000/h rate limit; otherwise
anonymous HTTPS to `api.github.com` (public repos, 60/h). Both hit fixed GitHub hosts, and owner /
repo / ref / path are validated before they reach a URL or an argv, so a caller cannot steer the
request anywhere else.

Accepted inputs:
    https://github.com/<o>/<r>                      default branch, repo root
    https://github.com/<o>/<r>/tree/<ref>/<path>    ref may contain slashes (resolved against the API)
    https://github.com/<o>/<r>/blob/<ref>/<file>
    https://raw.githubusercontent.com/<o>/<r>/<ref>/<path>
    git@github.com:<o>/<r>.git   ·   <o>/<r>   ·   <o>/<r>@<ref>   ·   <o>/<r>/<path>
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlparse

log = logging.getLogger("telecode.services.design.github_import")

API = "https://api.github.com"
MAX_TREE_ENTRIES = 5000
MAX_IMPORT_FILES = 200
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_REF_RE = re.compile(r"^[A-Za-z0-9_./-]{1,200}$")
_SAFE_SEG_RE = re.compile(r"[^A-Za-z0-9 ._-]")

_gh_state: Dict[str, Any] = {"at": 0.0, "ok": False, "path": None}


class GitHubError(ValueError):
    pass


# ── URL parsing ───────────────────────────────────────────────────────────

def parse_url(value: str) -> Dict[str, Any]:
    """→ {owner, repo, kind: repo|tree|blob|raw, rest: [segments after the ref marker], ref?, path?}.

    `rest` holds `<ref>/<path>` still joined when the ref may contain slashes; `resolve()` splits it.
    """
    s = (value or "").strip()
    if not s:
        raise GitHubError("empty GitHub reference")
    m = re.match(r"^git@github\.com:([^/]+)/(.+?)(?:\.git)?/?$", s)
    if m:
        return _validated(m.group(1), m.group(2), "repo", [])
    if re.match(r"^https?://", s, flags=re.I):
        p = urlparse(s)
        host = (p.hostname or "").lower()
        segs = [unquote(x) for x in p.path.split("/") if x]
        if host in ("github.com", "www.github.com"):
            if len(segs) < 2:
                raise GitHubError("URL needs /<owner>/<repo>")
            owner, repo = segs[0], re.sub(r"\.git$", "", segs[1])
            if len(segs) >= 4 and segs[2] in ("tree", "blob", "commit"):
                return _validated(owner, repo, "blob" if segs[2] == "blob" else "tree", segs[3:])
            return _validated(owner, repo, "repo", [])
        if host == "raw.githubusercontent.com":
            if len(segs) < 3:
                raise GitHubError("raw URL needs /<owner>/<repo>/<ref>/<path>")
            return _validated(segs[0], segs[1], "raw", segs[2:])
        raise GitHubError(f"not a GitHub URL: {host}")
    # shorthand owner/repo[@ref][/path]
    m = re.match(r"^([^/@\s]+)/([^/@\s]+?)(?:@([^/\s]+))?(?:/(.*))?$", s)
    if not m:
        raise GitHubError("expected a GitHub URL or owner/repo")
    out = _validated(m.group(1), re.sub(r"\.git$", "", m.group(2)), "repo", [])
    if m.group(3):
        if not _REF_RE.match(m.group(3)) or ".." in m.group(3):
            raise GitHubError("invalid ref")
        out["ref"] = m.group(3)
    if m.group(4):
        out["path"] = _clean_path(m.group(4))
    return out


def _validated(owner: str, repo: str, kind: str, rest: List[str]) -> Dict[str, Any]:
    if not _NAME_RE.match(owner) or not _NAME_RE.match(repo) or owner in (".", "..") or repo in (".", ".."):
        raise GitHubError("invalid owner/repo")
    for seg in rest:
        if seg in (".", "..") or "\\" in seg or "\x00" in seg:
            raise GitHubError("invalid path segment")
    return {"owner": owner, "repo": repo, "kind": kind, "rest": rest}


def _clean_path(path: str) -> str:
    segs = [s for s in (path or "").strip("/").split("/") if s]
    for s in segs:
        if s in (".", "..") or "\\" in s or "\x00" in s:
            raise GitHubError("invalid path")
    return "/".join(segs)


# ── Transport ─────────────────────────────────────────────────────────────

def _gh_ready() -> Optional[str]:
    """gh path if installed and authenticated (cached 5 min). Never triggers a login."""
    now = time.monotonic()
    if now - _gh_state["at"] < 300:
        return _gh_state["path"] if _gh_state["ok"] else None
    path = shutil.which("gh")
    ok = False
    if path:
        try:
            r = subprocess.run([path, "auth", "status", "--hostname", "github.com"], capture_output=True,
                               timeout=15, creationflags=_CREATE_NO_WINDOW)
            ok = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
    _gh_state.update(at=now, ok=ok, path=path)
    return path if ok else None


def transport() -> str:
    return "gh" if _gh_ready() else "https"


async def _api(path: str, *, accept: str = "application/vnd.github+json", raw: bool = False) -> Any:
    """GET api.github.com/<path>. `path` is built only from validated pieces."""
    gh = _gh_ready()
    if gh:
        def run() -> Tuple[int, bytes, bytes]:
            r = subprocess.run([gh, "api", "-H", f"Accept: {accept}", path], capture_output=True, timeout=90,
                               creationflags=_CREATE_NO_WINDOW)
            return r.returncode, r.stdout, r.stderr
        code, out, err = await asyncio.to_thread(run)
        if code != 0:
            msg = err.decode("utf-8", "replace").strip() or out.decode("utf-8", "replace")[:300]
            if "404" in msg or "Not Found" in msg:
                raise GitHubError(f"not found: {path}")
            raise GitHubError(f"gh api failed: {msg[:300]}")
        return out if raw else json.loads(out.decode("utf-8") or "null")
    import aiohttp
    headers = {"Accept": accept, "User-Agent": "telecode-teledesign", "X-GitHub-Api-Version": "2022-11-28"}
    timeout = aiohttp.ClientTimeout(total=90)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as http:
        async with http.get(f"{API}/{path}", allow_redirects=True) as resp:
            if resp.status == 404:
                raise GitHubError(f"not found: {path}")
            if resp.status == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
                raise GitHubError("GitHub API rate limit reached (anonymous: 60/h). Run `gh auth login` "
                                  "yourself to raise it — TeleDesign never logs in for you.")
            if resp.status != 200:
                raise GitHubError(f"GitHub API HTTP {resp.status}: {(await resp.text())[:200]}")
            buf = bytearray()
            async for chunk in resp.content.iter_chunked(1 << 16):
                buf += chunk
                if len(buf) > 64 * 1024 * 1024:
                    raise GitHubError("GitHub API response too large")
            data = bytes(buf)
            return data if raw else json.loads(data.decode("utf-8"))


async def _commit_sha(owner: str, repo: str, ref: str) -> Optional[str]:
    try:
        out = await _api(f"repos/{owner}/{repo}/commits/{quote(ref, safe='')}", accept="application/vnd.github.sha", raw=True)
    except GitHubError:
        return None
    sha = out.decode("utf-8", "replace").strip()
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else None


# ── Resolve / tree / import ───────────────────────────────────────────────

async def resolve(value: str) -> Dict[str, Any]:
    """→ {owner, repo, ref, sha, path, default_branch, kind, html_url, transport}."""
    p = parse_url(value)
    owner, repo = p["owner"], p["repo"]
    meta = await _api(f"repos/{owner}/{repo}")
    default_branch = meta.get("default_branch") or "main"
    ref, path = p.get("ref"), p.get("path", "")
    rest = p["rest"]
    sha = None
    if rest:
        # Refs may contain slashes: try the shortest prefix first (the common case), up to 6 segments.
        for k in range(1, min(len(rest), 6) + 1):
            cand = "/".join(rest[:k])
            if not _REF_RE.match(cand) or ".." in cand:
                break
            sha = await _commit_sha(owner, repo, cand)
            if sha:
                ref, path = cand, _clean_path("/".join(rest[k:]))
                break
        if not sha:
            raise GitHubError(f"could not resolve a branch, tag or commit from {'/'.join(rest)!r}")
    else:
        ref = ref or default_branch
        sha = await _commit_sha(owner, repo, ref)
        if not sha:
            raise GitHubError(f"unknown ref {ref!r}")
    return {"owner": owner, "repo": repo, "ref": ref, "sha": sha, "path": path, "kind": p["kind"],
            "default_branch": default_branch, "private": bool(meta.get("private")),
            "description": meta.get("description") or "",
            "html_url": f"https://github.com/{owner}/{repo}/tree/{ref}/{path}".rstrip("/"),
            "transport": transport()}


async def tree(value: str, prefix: Optional[str] = None, *, max_entries: int = MAX_TREE_ENTRIES) -> Dict[str, Any]:
    """List files (and dirs) under the URL's path (or `prefix`). Recursive, capped."""
    info = await resolve(value)
    pre = _clean_path(prefix if prefix is not None else info["path"])
    o, r = info["owner"], info["repo"]
    # Descend to the prefix one level at a time: a recursive listing of the whole repo is truncated
    # by GitHub for large repos, which would silently hide deep paths.
    tree_sha, base = info["sha"], ""
    entries: List[Dict[str, Any]] = []
    truncated = False
    segs = pre.split("/") if pre else []
    for i, seg in enumerate(segs):
        level = await _api(f"repos/{o}/{r}/git/trees/{tree_sha}")
        hit = next((e for e in level.get("tree") or [] if e.get("path") == seg), None)
        if not hit:
            raise GitHubError(f"path not found in {o}/{r}@{info['ref']}: {'/'.join(segs[:i + 1])}")
        if hit.get("type") != "tree":
            if i != len(segs) - 1:
                raise GitHubError(f"not a directory: {'/'.join(segs[:i + 1])}")
            entries = [{"path": pre, "type": "file", "size": hit.get("size"), "sha": hit.get("sha")}]
            tree_sha = None
            break
        tree_sha, base = hit["sha"], "/".join(segs[:i + 1])
    if tree_sha:
        data = await _api(f"repos/{o}/{r}/git/trees/{tree_sha}?recursive=1")
        truncated = bool(data.get("truncated"))
        for e in data.get("tree") or []:
            path = f"{base}/{e.get('path', '')}" if base else e.get("path", "")
            entries.append({"path": path, "type": "dir" if e.get("type") == "tree" else "file",
                            "size": e.get("size"), "sha": e.get("sha")})
            if len(entries) >= max_entries:
                truncated = True
                break
    return {**info, "prefix": pre, "entries": entries, "truncated": truncated,
            "files": sum(1 for e in entries if e["type"] == "file")}


def _dest_rel(repo_path: str, strip: str) -> str:
    rel = repo_path[len(strip):].lstrip("/") if strip and repo_path.startswith(strip) else repo_path
    segs = []
    for s in rel.split("/"):
        s = _SAFE_SEG_RE.sub("_", s).strip(" ")[:120] or "_"
        if s in (".", ".."):
            s = "_"
        segs.append(s)
    return "/".join(segs)


async def import_files(value: str, paths: List[str], dest_dir: Path, *, dest_prefix: str = "",
                       strip_prefix: Optional[str] = None) -> Dict[str, Any]:
    """Copy the selected repo files into `dest_dir/<dest_prefix>/…`. Returns what landed and what didn't.

    `paths` may name files or directories (a directory imports every file under it, still capped).
    """
    if not paths:
        raise GitHubError("no paths selected")
    clean = [_clean_path(str(p)) for p in paths[:MAX_IMPORT_FILES * 2]]
    # List only the smallest directory that holds every selection (keeps big repos untruncated).
    parents = [p.split("/")[:-1] for p in clean if p]
    common: List[str] = []
    for parts in zip(*parents) if parents else []:
        if all(x == parts[0] for x in parts):
            common.append(parts[0])
        else:
            break
    t = await tree(value, prefix="/".join(common))
    by_path = {e["path"]: e for e in t["entries"]}
    wanted: List[Dict[str, Any]] = []
    for raw in paths[:MAX_IMPORT_FILES * 2]:
        p = _clean_path(str(raw))
        e = by_path.get(p)
        if e and e["type"] == "file":
            wanted.append(e)
        elif e and e["type"] == "dir" or any(k.startswith(p + "/") for k in by_path):
            wanted += [x for k, x in by_path.items() if k.startswith(p + "/") and x["type"] == "file"]
    seen, uniq = set(), []
    for e in wanted:
        if e["path"] not in seen:
            seen.add(e["path"])
            uniq.append(e)
    strip = _clean_path(strip_prefix) if strip_prefix is not None else ""
    prefix = _clean_path(dest_prefix)
    imported, skipped, total = [], [], 0
    base = dest_dir.resolve()
    for e in uniq:
        if len(imported) >= MAX_IMPORT_FILES:
            skipped.append({"path": e["path"], "reason": f"over the {MAX_IMPORT_FILES}-file cap"})
            continue
        size = e.get("size") or 0
        if size > MAX_FILE_BYTES:
            skipped.append({"path": e["path"], "reason": f"larger than {MAX_FILE_BYTES // 1024} KB"})
            continue
        if total + size > MAX_TOTAL_BYTES:
            skipped.append({"path": e["path"], "reason": "total import size cap reached"})
            continue
        try:
            blob = await _api(f"repos/{t['owner']}/{t['repo']}/git/blobs/{e['sha']}")
            data = base64.b64decode(blob.get("content") or "") if blob.get("encoding") == "base64" \
                else (blob.get("content") or "").encode("utf-8")
        except (GitHubError, ValueError) as exc:
            skipped.append({"path": e["path"], "reason": str(exc)[:200]})
            continue
        rel = "/".join(x for x in (prefix, _dest_rel(e["path"], strip)) if x)
        target = (dest_dir / rel).resolve()
        if base not in target.parents:
            skipped.append({"path": e["path"], "reason": "unsafe destination"})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        total += len(data)
        imported.append({"path": e["path"], "dest": rel, "bytes": len(data)})
    return {"owner": t["owner"], "repo": t["repo"], "ref": t["ref"], "sha": t["sha"],
            "imported": imported, "skipped": skipped, "bytes": total, "transport": t["transport"]}


def tree_as_text(t: Dict[str, Any], limit: int = 400) -> str:
    """Compact listing for a prompt: `path · size` per file."""
    lines = [f"# {t['owner']}/{t['repo']}@{t['ref']} ({t['sha'][:12]}) under /{t.get('prefix') or ''}"]
    for e in t["entries"][:limit]:
        if e["type"] == "file":
            lines.append(f"{e['path']} · {e.get('size') or 0}")
    if len(t["entries"]) > limit or t.get("truncated"):
        lines.append(f"… truncated ({len(t['entries'])} entries listed)")
    return "\n".join(lines)
