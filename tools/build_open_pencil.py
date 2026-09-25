"""Build the TeleDesign canvas editor: open-pencil at a pinned tag + our patch series.

    python tools/build_open_pencil.py                 # full build → proxy/static/design/editor/
    python tools/build_open_pencil.py --check         # drift check: do the patches still apply?
    python tools/build_open_pencil.py --tag v0.16.0   # try a newer upstream release

Same shape as `llamacpp/patcher.py`: *a patch series, not a fork*. Every run starts
from a clean checkout of a named upstream tag, applies `patches/open-pencil/*.patch`
on top, builds, and vendors the static output. The source tree is disposable (it
is hard-reset on every run and lives under `data/`); the patches are the artifact.
`git apply --check` failing is a result — the patch landed upstream or bit-rotted —
never something to force. Fix the patch, never hand-edit the vendored output.

Steps (each printed with its wall time):
  1. clone / fetch https://github.com/open-pencil/open-pencil into
     <settings_dir>/data/design/.open-pencil-src, `git reset --hard <tag>`, clean
  2. `git apply` every patch in order (check first, stop on the first failure)
  3. `bun install --frozen-lockfile`, `bun run build:packages`
  4. `vite build --base /design/editor/ --sourcemap hidden` (maps only feed step 6)
  5. `bun scripts/telecode-dump-tools.ts` → services/design/editor_tools.json
  6. copy dist/ → proxy/static/design/editor/ (minus Cloudflare `_headers`/`_redirects`
     and the source maps), write NOTICE (licences of every package that actually
     ended up in the bundle, read from the source maps, plus the bundled fonts) and
     BUILD_INFO.json (tag, commit, patch sha256s, tool count, file count)

Bun is required at build time only (`npm i -g bun`); nothing Node runs at runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import config as app_config  # noqa: E402

UPSTREAM_REPO = "https://github.com/open-pencil/open-pencil"
# Pinned: open-pencil is pre-1.0 and breaks its MCP/SDK contracts between minors.
DEFAULT_TAG = "v0.15.1"
BASE_PATH = "/design/editor/"

PATCH_DIR = REPO / "patches" / "open-pencil"
OUT_DIR = REPO / "proxy" / "static" / "design" / "editor"
TOOLS_JSON = REPO / "services" / "design" / "editor_tools.json"

# Cloudflare Pages config in upstream's public/ — meaningless behind aiohttp.
_DROP_FROM_DIST = {"_headers", "_redirects"}

_LICENSE_FILE_RE = re.compile(r"^(licen[cs]e|copying|notice)(\.[a-z0-9]+)?$", re.I)


def source_dir() -> Path:
    return Path(app_config._settings_dir()) / "data" / "design" / ".open-pencil-src"


# ── Process helpers ──────────────────────────────────────────────────

def _log(msg: str) -> None:
    print(msg, flush=True)


def _run(cmd: List[str], cwd: Optional[Path] = None, *, check: bool = True,
         capture: bool = False, timeout: int = 3600) -> subprocess.CompletedProcess:
    _log(f"$ {' '.join(cmd)}")
    kwargs: Dict[str, Any] = {"cwd": str(cwd) if cwd else None, "timeout": timeout}
    if capture:
        kwargs.update(capture_output=True, text=True, encoding="utf-8", errors="replace")
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    res = subprocess.run(cmd, **kwargs)
    if check and res.returncode != 0:
        if capture:
            _log((res.stdout or "") + (res.stderr or ""))
        raise SystemExit(f"!! failed ({res.returncode}): {' '.join(cmd)}")
    return res


def _which(name: str) -> str:
    found = shutil.which(name)
    if not found:
        hint = " (install with `npm i -g bun`)" if name == "bun" else ""
        raise SystemExit(f"!! `{name}` not found on PATH{hint}")
    return found


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class _Timer:
    def __init__(self) -> None:
        self.steps: List[Tuple[str, float]] = []
        self._t0 = time.monotonic()

    def step(self, name: str):
        timer = self

        class _Ctx:
            def __enter__(self):
                _log(f"\n== {name}")
                self.t = time.monotonic()

            def __exit__(self, *exc):
                dt = time.monotonic() - self.t
                timer.steps.append((name, dt))
                if exc[0] is None:
                    _log(f"   ({dt:.1f}s)")

        return _Ctx()

    def total(self) -> float:
        return time.monotonic() - self._t0


# ── Source checkout ──────────────────────────────────────────────────

def patches() -> List[Path]:
    return sorted(p for p in PATCH_DIR.glob("*.patch") if p.is_file())


def checkout(src: Path, tag: str) -> str:
    """Clean checkout of `tag`; keeps node_modules so re-runs skip most of bun install."""
    git = _which("git")
    if not (src / ".git").is_dir():
        src.parent.mkdir(parents=True, exist_ok=True)
        # autocrlf off: the patches are LF and must apply byte-exact on Windows.
        _run([git, "-c", "core.autocrlf=false", "clone", "--filter=blob:none",
              UPSTREAM_REPO, str(src)])
    _run([git, "config", "core.autocrlf", "false"], src)
    _run([git, "fetch", "--tags", "--force", "origin"], src)
    _run([git, "checkout", "-q", "--detach", tag], src)
    _run([git, "reset", "-q", "--hard", tag], src)
    _run([git, "clean", "-q", "-ffdx", "-e", "node_modules"], src)
    if shutil.which("git-lfs") or shutil.which("git-lfs.exe"):
        # canvaskit-webgpu is LFS-tracked; a missing smudge leaves pointer files.
        _run([git, "lfs", "pull"], src, check=False)
    return _run([git, "rev-parse", "HEAD"], src, capture=True).stdout.strip()


def apply_patches(src: Path, check_only: bool) -> List[Dict[str, Any]]:
    git = _which("git")
    results = []
    for p in patches():
        res = _run([git, "apply", "--check", str(p)], src, check=False, capture=True)
        ok = res.returncode == 0
        results.append({"name": p.name, "sha256": _sha256(p), "applies": ok,
                        "error": None if ok else (res.stderr or res.stdout).strip()})
        if not ok:
            _log(f"!! {p.name} does not apply:\n{results[-1]['error']}")
            break
        if not check_only:
            _run([git, "apply", "--whitespace=nowarn", str(p)], src)
        _log(f"   ok  {p.name}")
    return results


# ── Build ────────────────────────────────────────────────────────────

def build(src: Path, timer: _Timer, skip_install: bool) -> None:
    bun = _which("bun")
    if not skip_install:
        with timer.step("bun install"):
            _run([bun, "install", "--frozen-lockfile"], src)
    with timer.step("build workspace packages"):
        _run([bun, "run", "build:packages"], src)
    with timer.step("vite build"):
        shutil.rmtree(src / "dist", ignore_errors=True)
        _run([bun, "x", "--bun", "vite", "build", "--base", BASE_PATH, "--sourcemap", "hidden"], src)
    with timer.step("dump tool descriptors"):
        _run([bun, "scripts/telecode-dump-tools.ts", str(TOOLS_JSON)], src)


# ── Licences ─────────────────────────────────────────────────────────

_PKG_IN_PATH = re.compile(r"node_modules/((?:@[^/]+/)?[^/]+)/")


def _bundled_package_dirs(src: Path, dist: Path) -> Dict[str, Path]:
    """Every third-party package with at least one module in the bundle.

    Read from the source maps rather than from package.json: dependencies that
    tree-shake away entirely do not ship, and transitive ones that do ship are
    not listed there.
    """
    found: Dict[str, Path] = {}
    for m in dist.rglob("*.map"):
        try:
            sources = json.loads(m.read_text(encoding="utf-8")).get("sources") or []
        except Exception:
            continue
        base = m.parent
        for s in sources:
            s = s.replace("\\", "/")
            if "node_modules/" not in s:
                continue
            # Last node_modules segment names the package (bun nests them under .bun/).
            idx = s.rfind("node_modules/")
            match = _PKG_IN_PATH.match(s[idx:])
            if not match:
                continue
            name = match.group(1)
            if name in found or name.startswith("."):
                continue
            pkg_dir = (base / s[: idx + len("node_modules/") + len(name)]).resolve()
            # Workspace packages (symlinked into node_modules) are OpenPencil itself;
            # @open-pencil/yoga-layout etc. from the registry are third-party builds.
            if (src.resolve() / "packages") in pkg_dir.parents:
                continue
            if (pkg_dir / "package.json").is_file():
                found[name] = pkg_dir
    return found


def _license_of(pkg_dir: Path) -> Tuple[str, str, str]:
    """(version, spdx, text) for one package."""
    meta = json.loads((pkg_dir / "package.json").read_text(encoding="utf-8"))
    lic = meta.get("license")
    if isinstance(lic, dict):
        lic = lic.get("type")
    if not lic and isinstance(meta.get("licenses"), list):
        lic = " OR ".join(str(x.get("type")) for x in meta["licenses"] if isinstance(x, dict))
    text = ""
    for f in sorted(pkg_dir.iterdir()):
        if f.is_file() and _LICENSE_FILE_RE.match(f.name):
            text = f.read_text(encoding="utf-8", errors="replace").strip()
            break
    if not text:
        # No licence file in the published package: record what it declares.
        author = meta.get("author")
        if isinstance(author, dict):
            author = author.get("name")
        repo = meta.get("repository")
        if isinstance(repo, dict):
            repo = repo.get("url")
        lines = [f"The published package ships no licence file; package.json declares "
                 f"\"{lic or 'no licence field'}\"."]
        if author:
            lines.append(f"Author: {author}")
        if repo or meta.get("homepage"):
            lines.append(f"Source: {repo or meta.get('homepage')}")
        if lic and lic != "UNKNOWN":
            lines.append(f"Licence text: https://spdx.org/licenses/{str(lic).split(' ')[0]}.html")
        text = "\n".join(lines)
    return str(meta.get("version", "")), str(lic or "UNKNOWN"), text


def _ttf_names(path: Path) -> Dict[int, str]:
    """name-table strings (0 copyright, 1 family, 13 licence) from a TrueType font."""
    data = path.read_bytes()
    num_tables = struct.unpack(">H", data[4:6])[0]
    for i in range(num_tables):
        tag, _cs, offset, _len = struct.unpack(">4sIII", data[12 + 16 * i: 28 + 16 * i])
        if tag != b"name":
            continue
        _fmt, count, str_off = struct.unpack(">HHH", data[offset: offset + 6])
        out: Dict[int, str] = {}
        for r in range(count):
            pid, eid, lid, nid, length, off = struct.unpack(
                ">HHHHHH", data[offset + 6 + 12 * r: offset + 18 + 12 * r])
            raw = data[offset + str_off + off: offset + str_off + off + length]
            if nid in out:
                continue
            if pid == 3 or pid == 0:
                out[nid] = raw.decode("utf-16-be", errors="replace")
            elif pid == 1 and lid == 0:
                out[nid] = raw.decode("latin-1", errors="replace")
        return out
    return {}


def write_notice(src: Path, dist: Path, out: Path, tag: str) -> int:
    parts: List[str] = [
        "Third-party software in this directory",
        "=======================================",
        "",
        "This directory is a static build of OpenPencil "
        f"({UPSTREAM_REPO}, tag {tag}) with telecode's patch series",
        "(patches/open-pencil/*.patch) applied, produced by tools/build_open_pencil.py.",
        "The build bundles the packages listed below; each licence is reproduced as shipped.",
        "",
    ]

    def section(title: str, body: str) -> None:
        parts.extend(["", "-" * 78, title, "-" * 78, "", body.strip() or "(no licence text shipped)"])

    section(f"OpenPencil {tag} — MIT", (src / "LICENSE").read_text(encoding="utf-8"))

    ofl = src / "tests" / "fixtures" / "fonts" / "OFL.txt"
    ofl_body = ofl.read_text(encoding="utf-8") if ofl.is_file() else ""
    # Drop that file's own (Adobe) copyright line; keep the licence body.
    ofl_body = ofl_body[ofl_body.find("This Font Software"):] if "This Font Software" in ofl_body else ofl_body
    for font in sorted(dist.glob("*.ttf")):
        names = _ttf_names(font)
        copyright_line = names.get(0, "").strip()
        section(f"Font {font.name} ({names.get(1, font.stem)}) — SIL Open Font License 1.1",
                f"{copyright_line}\n\n{ofl_body}")

    pkgs = _bundled_package_dirs(src, dist)
    for name in sorted(pkgs):
        version, spdx, text = _license_of(pkgs[name])
        section(f"{name}@{version} — {spdx}", text)

    out.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return len(pkgs)


# ── Vendor ───────────────────────────────────────────────────────────

def vendor(src: Path, tag: str, commit: str, patch_info: List[Dict[str, Any]]) -> Dict[str, Any]:
    dist = src / "dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("!! dist/index.html missing — vite build did not produce output")
    staging = OUT_DIR.with_name(OUT_DIR.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(dist, staging, ignore=shutil.ignore_patterns("*.map", *_DROP_FROM_DIST))
    package_count = write_notice(src, dist, staging / "NOTICE", tag)

    files = [p for p in staging.rglob("*") if p.is_file()]
    tools = json.loads(TOOLS_JSON.read_text(encoding="utf-8"))
    info = {
        "upstream": UPSTREAM_REPO,
        "tag": tag,
        "commit": commit,
        "base": BASE_PATH,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bun": _run([_which("bun"), "--version"], capture=True).stdout.strip(),
        "patches": [{"name": p["name"], "sha256": p["sha256"]} for p in patch_info],
        "tools": tools.get("count"),
        "open_pencil_version": tools.get("open_pencil_version"),
        "third_party_packages": package_count,
        "files": len(files) + 1,
        "bytes": sum(p.stat().st_size for p in files),
        "index_sha256": _sha256(staging / "index.html"),
    }
    (staging / "BUILD_INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")

    old = OUT_DIR.with_name(OUT_DIR.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if OUT_DIR.exists():
        os.replace(OUT_DIR, old)
    os.replace(staging, OUT_DIR)
    shutil.rmtree(old, ignore_errors=True)
    return info


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tag", default=DEFAULT_TAG, help=f"upstream tag (default {DEFAULT_TAG})")
    ap.add_argument("--src", type=Path, default=None, help="disposable source dir")
    ap.add_argument("--check", action="store_true", help="only check that the patches apply")
    ap.add_argument("--skip-install", action="store_true", help="reuse node_modules as-is")
    args = ap.parse_args(argv)

    # absolute(), not resolve(): a short alias for the source dir (subst drive or
    # junction) has to survive, because Windows' MAX_PATH breaks native addons
    # (sharp's DLL) loaded from deep paths.
    src = (args.src or source_dir()).absolute()
    timer = _Timer()
    _log(f"open-pencil {args.tag} → {src}")
    with timer.step("checkout"):
        commit = checkout(src, args.tag)
    with timer.step("apply patches"):
        info = apply_patches(src, check_only=args.check)
    if not all(p["applies"] for p in info) or len(info) != len(patches()):
        _log("\n!! patch series does not apply — fix the patch (never the vendored output)")
        return 1
    if args.check:
        _log(f"\nall {len(info)} patches apply cleanly to {args.tag} ({commit[:10]})")
        return 0

    build(src, timer, args.skip_install)
    with timer.step("vendor"):
        result = vendor(src, args.tag, commit, info)

    _log("\n== done")
    for name, dt in timer.steps:
        _log(f"   {name:<28} {dt:7.1f}s")
    _log(f"   {'total':<28} {timer.total():7.1f}s")
    _log(f"   {result['files']} files, {result['bytes'] / 1e6:.1f} MB, {result['tools']} tools, "
         f"{result['third_party_packages']} third-party packages → {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
