"""Git history for an agent's ``internal/`` directory (Letta MemFS pattern).

``data/agents/<id>/internal/`` is an ordinary git repository of its own
(``internal/.git``) — unrelated to any workspace's git and to the shadow
snapshot repos. Every change is a commit:

* staging write-back — ``run:<run_id> step:<step_id>`` or ``task:<task_id>``;
* UI / API edits — ``ui: …``; reflection proposals — ``reflection: …``;
* reverts — ``revert: …``; the one-time layout migration — ``migrate: …``.

Metadata rides in the message as ``telecode-<key>: value`` trailer lines
(kind, run, step, task, engine, approval, reverts).

**Write-back merges a per-run branch.** :func:`commit_tree_on` builds the run's
commit on the commit that was checked out when the run was staged (a temporary
index — the work tree is not touched), then :func:`integrate` brings it in: a
fast-forward when nothing else wrote meanwhile, otherwise ``git merge
--no-ff``. Only the files git reports as conflicted go through
:func:`services.memory.merge.merge3` (which keeps same-point appends from both
sides and falls back to ``<<<<<<< this run`` markers on a true overlap).

Git runs with ``CREATE_NO_WINDOW``, the user's global / system config ignored
(``GIT_CONFIG_GLOBAL=<devnull>``) and every inherited ``GIT_*`` variable
stripped, so a global ``autocrlf`` / hooks / signing can never change what the
repository holds. Files are written with LF line endings.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("telecode.services.memory.repo")

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
MAX_DIFF_BYTES = 512 * 1024
_TRAILER_RE = re.compile(r"^telecode-([a-z_]+):\s*(.*)$", re.M)
_REF_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

_locks_guard = threading.Lock()
_locks: Dict[str, threading.RLock] = {}


class MemoryGitError(RuntimeError):
    pass


class MemoryConflict(MemoryGitError):
    """A revert (or a proposal) cannot apply cleanly on the current memory."""


def lock_for(d: Path) -> threading.RLock:
    """One re-entrant lock per repository (agent_manager writes take it too)."""
    key = os.path.normcase(str(Path(d).absolute()))
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = _locks[key] = threading.RLock()
        return lk


def available() -> bool:
    return shutil.which("git") is not None


def _env() -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "telecode", "GIT_AUTHOR_EMAIL": "telecode@localhost",
        "GIT_COMMITTER_NAME": "telecode", "GIT_COMMITTER_EMAIL": "telecode@localhost",
        "LC_ALL": "C",
    })
    return env


_CFG = ("-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "-c", "core.quotepath=false",
        "-c", "core.longpaths=true", "-c", "core.fsmonitor=false", "-c", "gc.auto=0",
        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + os.devnull,
        "-c", "merge.renames=false", "-c", "init.defaultBranch=main")


def _run(d: Path, *args: str, input_bytes: Optional[bytes] = None, check: bool = True,
         env_extra: Optional[Dict[str, str]] = None, timeout: float = 120.0) -> subprocess.CompletedProcess:
    env = _env()
    if env_extra:
        env.update(env_extra)
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    cp = subprocess.run(["git", *_CFG, *args], cwd=str(d), env=env, input=input_bytes,
                        capture_output=True, timeout=timeout, creationflags=flags)
    if check and cp.returncode != 0:
        raise MemoryGitError(f"git {' '.join(args[:2])} failed ({cp.returncode}): "
                             f"{cp.stderr.decode('utf-8', 'replace').strip()[:500]}")
    return cp


def _out(cp: subprocess.CompletedProcess) -> str:
    return cp.stdout.decode("utf-8", "replace")


def write_text(path: Path, text: str) -> None:
    """LF-only UTF-8 write (what git stores; no CRLF churn on Windows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((text or "").replace("\r\n", "\n").encode("utf-8"))


def message(subject: str, **trailers: Any) -> str:
    lines = [subject.strip()[:300]]
    tr = [f"telecode-{k}: {v}" for k, v in trailers.items() if v not in (None, "")]
    if tr:
        lines += ["", *tr]
    return "\n".join(lines)


# ── basic operations ────────────────────────────────────────────────────────

def is_repo(d: Path) -> bool:
    return (Path(d) / ".git").is_dir()


def init(d: Path, subject: str = "migrate: start history of the agent's internal files") -> Optional[str]:
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    with lock_for(d):
        if not is_repo(d):
            _run(d, "init", "-q")
        return commit_all(d, message(subject, kind="migrate"), allow_empty=head(d) is None)


def head(d: Path) -> Optional[str]:
    cp = _run(d, "rev-parse", "-q", "--verify", "HEAD", check=False)
    return _out(cp).strip() or None if cp.returncode == 0 else None


def dirty(d: Path) -> bool:
    return bool(_out(_run(d, "status", "--porcelain", "-uall")).strip())


def commit_all(d: Path, msg: str, allow_empty: bool = False) -> Optional[str]:
    """Commit everything in the work tree. Returns the new sha, or None when
    there was nothing to commit."""
    with lock_for(d):
        _run(d, "add", "-A")
        if not allow_empty and head(d) is not None and \
                _run(d, "diff", "--cached", "--quiet", check=False).returncode == 0:
            return None
        _run(d, "commit", "-q", "--no-verify", *(["--allow-empty"] if allow_empty else []),
             "-F", "-", input_bytes=msg.encode("utf-8"))
        return head(d)


def read_at(d: Path, rev: str, path: str) -> Optional[str]:
    cp = _run(d, "show", f"{rev}:{path}", check=False)
    if cp.returncode != 0:
        return None
    return cp.stdout.decode("utf-8", "replace").replace("\r\n", "\n")


def ls_tree(d: Path, rev: str, prefix: str = "") -> List[str]:
    cp = _run(d, "ls-tree", "-r", "--name-only", rev, *([prefix] if prefix else []), check=False)
    return [l for l in _out(cp).splitlines() if l] if cp.returncode == 0 else []


def commit_tree_on(d: Path, base: Optional[str], files: Dict[str, Optional[str]], msg: str) -> str:
    """A commit whose parent is ``base`` and whose tree is ``base``'s with
    ``files`` applied (text = write, None = delete). Built in a temporary index;
    neither the work tree nor any branch moves."""
    idx = Path(d) / ".git" / f"telecode-index-{uuid.uuid4().hex[:10]}"
    env = {"GIT_INDEX_FILE": str(idx)}
    try:
        if base:
            _run(d, "read-tree", base, env_extra=env)
        else:
            _run(d, "read-tree", "--empty", env_extra=env)
        for path, text in files.items():
            if text is None:
                _run(d, "update-index", "--force-remove", "--", path, env_extra=env)
                continue
            blob = _out(_run(d, "hash-object", "-w", "--stdin",
                             input_bytes=text.replace("\r\n", "\n").encode("utf-8"))).strip()
            _run(d, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env_extra=env)
        tree = _out(_run(d, "write-tree", env_extra=env)).strip()
        args = ["commit-tree", tree] + (["-p", base] if base else []) + ["-F", "-"]
        return _out(_run(d, *args, input_bytes=msg.encode("utf-8"))).strip()
    finally:
        try:
            idx.unlink(missing_ok=True)
        except OSError:
            pass


def tree_of(d: Path, rev: str) -> Optional[str]:
    cp = _run(d, "rev-parse", "-q", "--verify", f"{rev}^{{tree}}", check=False)
    return _out(cp).strip() or None if cp.returncode == 0 else None


def set_ref(d: Path, ref: str, sha: str) -> None:
    _run(d, "update-ref", ref, sha)


def delete_ref(d: Path, ref: str) -> None:
    _run(d, "update-ref", "-d", ref, check=False)


def ref_exists(d: Path, ref: str) -> bool:
    return _run(d, "rev-parse", "-q", "--verify", ref, check=False).returncode == 0


# ── merging a run's branch ─────────────────────────────────────────────────

def integrate(d: Path, base: Optional[str], sha: str, label: str, *,
              direct_msg: Optional[str] = None, trailers: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Bring commit ``sha`` (built on ``base``) into ``main``.

    First commits whatever is uncommitted in the work tree (``direct_msg``:
    files an engine wrote straight into the memory dir during the run). Then
    fast-forward when ``main`` is still at ``base``, else ``git merge --no-ff``
    of a per-run branch; conflicted files are resolved with ``merge3``.
    Returns ``{head, fast_forward, conflicts: [paths with markers], resolved: [paths]}``."""
    d = Path(d)
    ref = f"refs/heads/incoming/{_REF_SAFE.sub('_', label)[:60]}-{uuid.uuid4().hex[:6]}"
    with lock_for(d):
        set_ref(d, ref, sha)
        try:
            for _ in range(3):
                commit_all(d, direct_msg or message(f"{label} (uncommitted changes)", kind="direct"))
                cur = head(d)
                if cur is None or cur == base:
                    cp = _run(d, "merge", "--ff-only", "-q", ref, check=False)
                    if cp.returncode == 0:
                        return {"head": head(d), "fast_forward": True, "conflicts": [], "resolved": []}
                    continue            # the work tree changed under us: commit it and retry
                mmsg = message(f"merge {label}", **{**(trailers or {}), "kind": "merge",
                                                     "merged_kind": (trailers or {}).get("kind")})
                cp = _run(d, "merge", "--no-ff", "--no-edit", "-q", "-m", mmsg, ref, check=False)
                if cp.returncode == 0:
                    return {"head": head(d), "fast_forward": False, "conflicts": [], "resolved": []}
                unmerged = [p for p in _out(_run(d, "diff", "--name-only", "--diff-filter=U",
                                                 check=False)).splitlines() if p]
                if not unmerged:
                    _run(d, "merge", "--abort", check=False)
                    continue
                conflicts, resolved = _resolve(d, base, sha, cur, unmerged)
                _run(d, "commit", "-q", "--no-verify", "-F", "-", input_bytes=mmsg.encode("utf-8"))
                return {"head": head(d), "fast_forward": False, "conflicts": conflicts, "resolved": resolved}
            raise MemoryGitError(f"could not merge {label}: the memory directory kept changing")
        finally:
            delete_ref(d, ref)


def _resolve(d: Path, base: Optional[str], ours_rev: str, theirs_rev: str,
             paths: List[str]) -> Tuple[List[str], List[str]]:
    from services.memory.merge import merge3
    conflicts: List[str] = []
    resolved: List[str] = []
    for p in paths:
        b = read_at(d, base, p) if base else None
        o = read_at(d, ours_rev, p)
        t = read_at(d, theirs_rev, p)
        if o is None and t is None:
            _run(d, "rm", "-q", "--cached", "--ignore-unmatch", "--", p, check=False)
            continue
        if o is None or t is None:
            text, conflict = (t if o is None else o), False   # modify/delete: never lose the modification
        else:
            text, conflict = merge3(b or "", o, t)
        write_text(Path(d) / p, text)
        _run(d, "add", "--", p)
        (conflicts if conflict else resolved).append(p)
    return conflicts, resolved


# ── history / diff / revert ────────────────────────────────────────────────

def _trailers(body: str) -> Dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in _TRAILER_RE.finditer(body or "")}


def history(d: Path, limit: int = 50, path: Optional[str] = None) -> List[Dict[str, Any]]:
    if not is_repo(d) or head(d) is None:
        return []
    fmt = "%x1e%H%x1f%P%x1f%aI%x1f%s%x1f%b%x1d"
    args = ["log", f"-n{max(1, min(int(limit), 500))}", f"--format={fmt}", "--numstat", "--no-renames"]
    if path:
        args += ["--", path]
    out = _out(_run(d, *args))
    commits: List[Dict[str, Any]] = []
    for rec in out.split("\x1e"):
        if not rec.strip():
            continue
        head_part, _, stats = rec.partition("\x1d")
        sha, parents, date, subject, body = (head_part.split("\x1f") + [""] * 5)[:5]
        files = []
        for line in stats.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                a, dl, p = parts
                files.append({"path": p, "additions": int(a) if a.isdigit() else 0,
                              "deletions": int(dl) if dl.isdigit() else 0})
        tr = _trailers(body)
        commits.append({"sha": sha, "short": sha[:8], "parents": parents.split(), "date": date,
                        "subject": subject, "kind": tr.get("kind") or _guess_kind(subject),
                        "run_id": tr.get("run"), "step_id": tr.get("step"), "task_id": tr.get("task"),
                        "approval_id": tr.get("approval"), "trailers": tr, "files": files})
    return commits


def _guess_kind(subject: str) -> str:
    s = subject.lower()
    for k in ("run", "task", "ui", "reflection", "revert", "migrate", "merge"):
        if s.startswith(k + ":") or s.startswith(k + " "):
            return k
    return "other"


def _parents(d: Path, commit: str) -> List[str]:
    return _out(_run(d, "rev-list", "--parents", "-n1", commit)).split()[1:]


def resolve_commit(d: Path, commit: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{4,64}", commit or ""):
        raise ValueError("commit must be a hex sha")
    cp = _run(d, "rev-parse", "-q", "--verify", f"{commit}^{{commit}}", check=False)
    if cp.returncode != 0:
        raise LookupError(f"unknown commit {commit}")
    return _out(cp).strip()


def show(d: Path, commit: str, path: Optional[str] = None) -> Dict[str, Any]:
    """Diff of one commit against its first parent (the empty tree for the root)."""
    sha = resolve_commit(d, commit)
    parents = _parents(d, sha)
    base = parents[0] if parents else EMPTY_TREE
    return {"sha": sha, "parents": parents, **diff_between(d, base, sha, path)}


def diff_between(d: Path, a: str, b: str, path: Optional[str] = None) -> Dict[str, Any]:
    tail = ["--", path] if path else []
    files = []
    for line in _out(_run(d, "diff", "--numstat", "--no-renames", a, b, *tail)).splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            files.append({"path": parts[2], "additions": int(parts[0]) if parts[0].isdigit() else 0,
                          "deletions": int(parts[1]) if parts[1].isdigit() else 0})
    kinds = {"A": "added", "D": "deleted"}
    for line in _out(_run(d, "diff", "--name-status", "--no-renames", a, b, *tail)).splitlines():
        st, _, p = line.partition("\t")
        for f in files:
            if f["path"] == p:
                f["change"] = kinds.get(st[:1], "modified")
    raw = _run(d, "diff", "--no-color", "--no-renames", a, b, *tail).stdout
    truncated = len(raw) > MAX_DIFF_BYTES
    return {"files": files, "diff": raw[:MAX_DIFF_BYTES].decode("utf-8", "replace").replace("\r\n", "\n"),
            "truncated": truncated}


def revert(d: Path, commit: str) -> Optional[str]:
    """A new commit undoing ``commit`` (for a merge: relative to its first
    parent). Raises MemoryConflict when later commits changed the same lines,
    ValueError for the root commit. Returns None when there is nothing to undo."""
    d = Path(d)
    with lock_for(d):
        sha = resolve_commit(d, commit)
        parents = _parents(d, sha)
        if not parents:
            raise ValueError("the first commit of the history cannot be reverted")
        commit_all(d, message("sync: uncommitted changes before a revert", kind="direct"))
        subject = _out(_run(d, "log", "-n1", "--format=%s", sha)).strip()
        cp = _run(d, "revert", "--no-commit", *(["-m", "1"] if len(parents) > 1 else []), sha, check=False)
        if cp.returncode != 0:
            _run(d, "revert", "--abort", check=False)
            _run(d, "reset", "-q", "--hard", "HEAD", check=False)
            raise MemoryConflict("later changes touch the same lines — revert them first, or edit by hand "
                                 f"({cp.stderr.decode('utf-8', 'replace').strip()[:300]})")
        if _run(d, "diff", "--cached", "--quiet", check=False).returncode == 0:
            _run(d, "revert", "--quit", check=False)
            return None
        _run(d, "commit", "-q", "--no-verify", "-F", "-",
             input_bytes=message(f"revert: {subject} ({sha[:8]})", kind="revert", reverts=sha).encode("utf-8"))
        return head(d)
