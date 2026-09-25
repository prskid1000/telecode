"""Workspace snapshots — a shadow git repository per workspace.

``GIT_DIR = data/snapshots/<key>.git``, work tree = the session folder. The
workspace's own ``.git`` is never touched: every command passes
``--git-dir``/``--work-tree`` explicitly, git never tracks a path named
``.git``, and nothing is ever written into the work tree except by
:func:`restore` (which only rewrites tracked paths).

Model
-----
* A snapshot is a **parentless commit** of the whole work tree (``git add -A``
  → ``write-tree`` → ``commit-tree``), kept alive by a ref
  ``refs/snapshots/<ms>-<seq>``. Diffs between any two snapshots work without a
  parent chain, so pruning is "delete the oldest refs + gc" and never rewrites
  the shas that run steps point at.
* Metadata rides in the commit message as ``telecode-<key>: value`` trailer
  lines (run, step, attempt, phase = before|after|manual|restore).
* Ignored: ``.telecode/`` (engine scratch), ``session.json`` / ``.session.*``
  (the session store's own files), ``node_modules/``, ``__pycache__/``,
  virtualenvs, every file larger than ``tasks.snapshots.max_file_mb`` (default
  50 MB, rewritten into ``info/exclude`` before each snapshot) and whatever the
  workspace's own ``.gitignore`` files ignore. Nested repositories are recorded
  as gitlinks (their content is not snapshotted).
* Retention: ``tasks.snapshots.keep`` newest snapshots per workspace (default
  200) and ``tasks.snapshots.max_repo_mb`` (default 2048) — beyond either, the
  oldest refs are dropped and the repo is gc'd on a background thread.

Git runs as a subprocess with ``CREATE_NO_WINDOW``, the user's global/system
git config disabled (``GIT_CONFIG_GLOBAL=<devnull>``, ``GIT_CONFIG_NOSYSTEM``)
so a global ``excludesFile`` or ``autocrlf`` can't change what a snapshot
holds, and every inherited ``GIT_*`` variable stripped.

Every public function returns ``None``/empty on a git failure and logs it —
a snapshot is bookkeeping and never fails a run.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("telecode.services.snapshots")

REF_PREFIX = "refs/snapshots/"
STATIC_EXCLUDES = (
    "/.telecode/",
    "/session.json",
    ".session.*",
    "node_modules/",
    "__pycache__/",
    ".venv/",
    "venv/",
    ".mypy_cache/",
    ".pytest_cache/",
)
_SKIP_WALK = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".telecode"}
MAX_DIFF_BYTES = 512 * 1024
_KEY_RE = re.compile(r"[^A-Za-z0-9_.-]+")

_locks_guard = threading.Lock()
_locks: Dict[str, threading.RLock] = {}
_seq = 0
_seq_lock = threading.Lock()


class SnapshotError(RuntimeError):
    pass


# ── Paths / keys ────────────────────────────────────────────────────────────

def base_dir() -> Path:
    import config
    return Path(config._settings_dir()) / "data" / "snapshots"


def key_for(session_id: str, namespace: Optional[str] = None) -> str:
    """Stable repo key for a session folder: ``<ns|root>--<session_id>``."""
    return _KEY_RE.sub("_", f"{namespace or 'root'}--{session_id}")


def git_dir(key: str) -> Path:
    return base_dir() / f"{_KEY_RE.sub('_', key)}.git"


def _lock(key: str) -> threading.RLock:
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = _locks[key] = threading.RLock()
        return lk


def available() -> bool:
    import config
    return bool(config.snapshots_enabled()) and shutil.which("git") is not None


# ── git plumbing ────────────────────────────────────────────────────────────

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
        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + os.devnull)


def _git(key: str, work_tree: Optional[Path], *args: str, input_bytes: Optional[bytes] = None,
         check: bool = True, timeout: float = 300.0) -> subprocess.CompletedProcess:
    cmd = ["git", *_CFG, f"--git-dir={git_dir(key)}"]
    if work_tree is not None:
        cmd.append(f"--work-tree={work_tree}")
    cmd += list(args)
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    cwd = str(work_tree) if work_tree is not None and Path(work_tree).is_dir() else str(base_dir())
    cp = subprocess.run(cmd, cwd=cwd, env=_env(), input=input_bytes, capture_output=True,
                        timeout=timeout, creationflags=flags)
    if check and cp.returncode != 0:
        raise SnapshotError(f"git {' '.join(args[:2])} failed ({cp.returncode}): "
                            f"{cp.stderr.decode('utf-8', 'replace').strip()[:500]}")
    return cp


def _out(cp: subprocess.CompletedProcess) -> str:
    return cp.stdout.decode("utf-8", "replace")


def _ensure_repo(key: str) -> None:
    gd = git_dir(key)
    if (gd / "HEAD").exists():
        return
    gd.parent.mkdir(parents=True, exist_ok=True)
    flags = 0x08000000 if sys.platform == "win32" else 0
    cp = subprocess.run(["git", "init", "--bare", "-q", str(gd)], env=_env(), capture_output=True,
                        creationflags=flags, cwd=str(gd.parent))
    if cp.returncode != 0:
        raise SnapshotError(f"git init failed: {cp.stderr.decode('utf-8', 'replace')[:300]}")
    _git(key, None, "config", "core.bare", "false")
    (gd / "info").mkdir(exist_ok=True)


def _escape_pattern(rel: str) -> str:
    out = re.sub(r"([*?\[\]\\!# ])", r"\\\1", rel)
    return "/" + out


def _write_excludes(key: str, work_tree: Path) -> List[str]:
    """Static patterns + every file over the size cap. Returns the big files."""
    import config
    limit = int(config.snapshots_max_file_mb()) * 1024 * 1024
    big: List[str] = []
    for dirpath, dirnames, filenames in os.walk(work_tree):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_WALK]
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                if p.stat().st_size > limit:
                    big.append(p.relative_to(work_tree).as_posix())
            except OSError:
                continue
    lines = ["# written by telecode (services/snapshots) — regenerated before every snapshot",
             *STATIC_EXCLUDES, *(_escape_pattern(b) for b in big)]
    (git_dir(key) / "info" / "exclude").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return big


def _next_ref() -> str:
    global _seq
    with _seq_lock:
        _seq = (_seq + 1) % 1000
        return f"{REF_PREFIX}{int(time.time() * 1000):013d}-{_seq:03d}"


def _message(label: str, meta: Dict[str, Any]) -> str:
    lines = [label.strip().splitlines()[0][:200] if label.strip() else "snapshot", ""]
    for k, v in (meta or {}).items():
        if v is None or v == "":
            continue
        lines.append(f"telecode-{k}: {str(v).splitlines()[0][:300]}")
    return "\n".join(lines) + "\n"


def _parse_message(msg: str) -> Tuple[str, Dict[str, str]]:
    lines = (msg or "").splitlines()
    label = lines[0] if lines else ""
    meta: Dict[str, str] = {}
    for ln in lines[1:]:
        m = re.match(r"^telecode-([A-Za-z0-9_]+): (.*)$", ln)
        if m:
            meta[m.group(1)] = m.group(2)
    return label, meta


# ── Public API ──────────────────────────────────────────────────────────────

def take(key: str, work_tree: Path, label: str = "snapshot", meta: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Snapshot the whole work tree. Returns the commit sha (None if disabled/failed)."""
    if not available():
        return None
    work_tree = Path(work_tree)
    if not work_tree.is_dir():
        return None
    try:
        with _lock(key):
            _ensure_repo(key)
            big = _write_excludes(key, work_tree)
            _git(key, work_tree, "add", "-A", "--ignore-errors", ".", check=False)
            tree = _out(_git(key, work_tree, "write-tree")).strip()
            m = dict(meta or {})
            if big:
                m["skipped_large"] = ", ".join(big[:10]) + (f" (+{len(big) - 10})" if len(big) > 10 else "")
            sha = _out(_git(key, work_tree, "commit-tree", tree, input_bytes=_message(label, m).encode("utf-8"))).strip()
            _git(key, None, "update-ref", _next_ref(), sha)
        _maybe_prune(key)
        return sha
    except (SnapshotError, OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"snapshot {key} failed: {exc}")
        return None


def exists(key: str, sha: Optional[str]) -> bool:
    if not sha or not re.fullmatch(r"[0-9a-f]{7,40}", sha or "") or not (git_dir(key) / "HEAD").exists():
        return False
    try:
        return _git(key, None, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def log(key: str, limit: int = 500) -> List[Dict[str, Any]]:
    """Snapshots newest first: {sha, ref, created_at, label, kind, run_id, step_id, attempt, …}."""
    if not (git_dir(key) / "HEAD").exists():
        return []
    try:
        cp = _git(key, None, "for-each-ref", "--sort=-refname", f"--count={int(limit)}",
                  "--format=%(refname)%00%(objectname)%00%(creatordate:iso-strict)%00%(contents)%01",
                  REF_PREFIX)
    except (SnapshotError, OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"snapshot log {key} failed: {exc}")
        return []
    out: List[Dict[str, Any]] = []
    for rec in _out(cp).split("\x01"):
        rec = rec.strip("\n")
        if not rec:
            continue
        parts = rec.split("\x00")
        if len(parts) < 4:
            continue
        label, meta = _parse_message(parts[3])
        out.append({"sha": parts[1], "ref": parts[0], "created_at": parts[2], "label": label,
                    "kind": meta.pop("phase", "manual"), **meta})
    return out


def previous(key: str, sha: str) -> Optional[str]:
    """The snapshot taken just before ``sha`` (by ref order), if any."""
    entries = log(key, limit=100000)
    for i, e in enumerate(entries):
        if e["sha"].startswith(sha):
            return entries[i + 1]["sha"] if i + 1 < len(entries) else None
    return None


_STATUS = {"A": "added", "M": "modified", "D": "deleted", "T": "modified"}


def changed_files(key: str, before: str, after: str) -> List[Dict[str, Any]]:
    """[{path, change: added|modified|deleted, additions, deletions, binary}] between two snapshots."""
    if not (exists(key, before) and exists(key, after)):
        return []
    try:
        ns = _out(_git(key, None, "diff", "--no-renames", "--name-status", "-z", before, after))
        num = _out(_git(key, None, "diff", "--no-renames", "--numstat", "-z", before, after))
    except (SnapshotError, OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"snapshot diff {key} failed: {exc}")
        return []
    stats: Dict[str, Tuple[Optional[int], Optional[int]]] = {}
    for rec in num.split("\x00"):
        if not rec:
            continue
        bits = rec.split("\t", 2)
        if len(bits) == 3:
            a, d, p = bits
            stats[p] = (None if a == "-" else int(a), None if d == "-" else int(d))
    toks = [t for t in ns.split("\x00")]
    files: List[Dict[str, Any]] = []
    i = 0
    while i + 1 < len(toks):
        st, path = toks[i], toks[i + 1]
        i += 2
        if not st:
            continue
        a, d = stats.get(path, (0, 0))
        files.append({"path": path, "change": _STATUS.get(st[0], "modified"),
                      "additions": a, "deletions": d, "binary": a is None})
    return files


def diff(key: str, before: str, after: str, path: Optional[str] = None,
         max_bytes: int = MAX_DIFF_BYTES) -> Dict[str, Any]:
    """Unified diff before→after (optionally one path). ``truncated`` when capped."""
    if not (exists(key, before) and exists(key, after)):
        raise SnapshotError("snapshot not found (pruned or never taken)")
    args = ["diff", "--no-color", "--no-ext-diff", "--no-renames", "-U3", before, after]
    if path:
        args += ["--", path]
    raw = _git(key, None, *args).stdout
    truncated = len(raw) > max_bytes
    return {"diff": raw[:max_bytes].decode("utf-8", "replace"), "truncated": truncated, "bytes": len(raw)}


def restore(key: str, work_tree: Path, sha: str, label: Optional[str] = None,
            meta: Optional[Dict[str, Any]] = None) -> Dict[str, Optional[str]]:
    """Make the work tree match snapshot ``sha`` (tracked paths only).

    First a safety snapshot of the current state (so the restore is itself
    undoable), then a two-tree ``read-tree -m -u`` from that state to ``sha``
    (writes changed files, deletes files ``sha`` did not have), then a
    ``restore`` snapshot. Ignored/excluded files and nested ``.git`` dirs are
    left alone. Returns {"safety": sha, "restored": sha}.
    """
    if not available():
        raise SnapshotError("snapshots are disabled or git is not on PATH")
    work_tree = Path(work_tree)
    if not exists(key, sha):
        raise SnapshotError("snapshot not found (pruned or never taken)")
    with _lock(key):
        safety = take(key, work_tree, f"before restore to {sha[:8]}", {**(meta or {}), "phase": "safety"})
        if not safety:
            raise SnapshotError("could not take the safety snapshot before restoring")
        _git(key, work_tree, "read-tree", "-m", "-u", f"{safety}^{{tree}}", f"{sha}^{{tree}}")
        done = take(key, work_tree, label or f"restored to {sha[:8]}",
                    {**(meta or {}), "phase": "restore", "restored_from": sha})
    return {"safety": safety, "restored": done}


def delete_repo(key: str) -> None:
    with _lock(key):
        shutil.rmtree(git_dir(key), ignore_errors=True)


# ── Retention ───────────────────────────────────────────────────────────────

def _repo_bytes(key: str) -> int:
    total = 0
    for dirpath, _d, files in os.walk(git_dir(key)):
        for f in files:
            try:
                total += (Path(dirpath) / f).stat().st_size
            except OSError:
                pass
    return total


def _maybe_prune(key: str) -> None:
    import config
    keep = max(1, int(config.snapshots_keep()))
    try:
        refs = [l for l in _out(_git(key, None, "for-each-ref", "--sort=refname", "--format=%(refname)",
                                      REF_PREFIX)).splitlines() if l]
    except (SnapshotError, OSError, subprocess.SubprocessError):
        return
    drop = refs[:-keep] if len(refs) > keep else []
    if drop:
        _drop_refs(key, drop)
        _gc_background(key)


def _drop_refs(key: str, refs: List[str]) -> None:
    script = "".join(f"delete {r}\n" for r in refs).encode("utf-8")
    try:
        _git(key, None, "update-ref", "--stdin", input_bytes=script)
    except (SnapshotError, OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"snapshot prune {key} failed: {exc}")


def _gc_background(key: str) -> None:
    def run() -> None:
        import config
        try:
            with _lock(key):
                _git(key, None, "gc", "--prune=now", "--quiet", check=False, timeout=900)
                cap = int(config.snapshots_max_repo_mb()) * 1024 * 1024
                if _repo_bytes(key) > cap:
                    refs = [l for l in _out(_git(key, None, "for-each-ref", "--sort=refname",
                                                 "--format=%(refname)", REF_PREFIX)).splitlines() if l]
                    if len(refs) > 1:
                        _drop_refs(key, refs[: len(refs) // 2])
                        _git(key, None, "gc", "--prune=now", "--quiet", check=False, timeout=900)
                        logger.warning(f"snapshot repo {key} over {cap >> 20} MB — dropped the oldest half")
        except Exception:
            logger.exception(f"snapshot gc {key} failed")
    threading.Thread(target=run, daemon=True, name=f"snapshot-gc-{key[:24]}").start()


def prune_now(key: str, keep: int) -> int:
    """Synchronous prune to ``keep`` refs (tests / maintenance). Returns refs dropped."""
    refs = [l for l in _out(_git(key, None, "for-each-ref", "--sort=refname", "--format=%(refname)",
                                 REF_PREFIX)).splitlines() if l]
    drop = refs[:-keep] if keep and len(refs) > keep else []
    if drop:
        _drop_refs(key, drop)
        with _lock(key):
            _git(key, None, "gc", "--prune=now", "--quiet", check=False)
    return len(drop)
