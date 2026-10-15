"""Maintain a patch series for a third-party checkout (patches/llama.cpp): regenerate it from the working tree, prove it applies, try a newer upstream tag.

The patches are the artifact, the checkout is disposable. Work in the checkout (edit the files, build, test), then `regen` rewrites the patch files
from the working tree and `verify` proves that the series, applied in order to a CLEAN `git archive HEAD`, gives exactly the working tree again.

usage: python tools/patch_series.py regen   [SERIES_DIR]
       python tools/patch_series.py verify  [SERIES_DIR]
       python tools/patch_series.py upgrade-check TAG [SERIES_DIR]
  SERIES_DIR  default patches/llama.cpp (it holds series.json and the .patch files)
  regen          rewrite every patch that has "paths" from the working tree (git diff of those paths), keeping or setting its header
  verify         (1) every patch reverses on the working tree (2) apply all in order to a clean checkout of HEAD (3) compare every touched file
  upgrade-check  fetch TAG, apply the series in order to a clean copy of it in a temp folder and say which patch fails and on which file
                 (the working tree is not touched; to really move: git checkout TAG, apply the patches, fix the rejected hunks by hand, regen)

series.json
  {
    "repo": "build/llama.cpp",                         the checkout, relative to the repo root
    "new_files": ["src/new_file.cpp"],                 files that are new in the series and untracked (git add -N is run on them)
    "patches": [
      {"file": "0001-....patch", "exact": true},       a patch that is taken as it is (somebody else's diff, an upstream PR): the tool never rewrites it,
                                                         it is reversed before the others are regenerated and applied again after, so it does not leak into them
      {"file": "0002-....patch", "paths": ["ggml"], "header": "keep"},   rewritten from `git diff -- ggml`; the header (text before the first diff) is kept
      {"file": "0003-....patch", "paths": ["src/a.cpp", "src/b.h"], "header": "Title: ...\\nRequires: 0002-....patch\\n\\n"}
    ]
  }
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "patches" / "llama.cpp"


def run(args: list[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(args, cwd=str(cwd), capture_output=True)
    if check and r.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed: {r.stderr.decode('utf-8', 'replace')[:400]}")
    return r


def load(series_dir: Path) -> tuple[dict, Path]:
    spec = json.loads((series_dir / "series.json").read_text(encoding="utf-8"))
    return spec, ROOT / spec["repo"]


def header_of(path: Path) -> str:
    s = path.read_bytes().decode("utf-8")
    i = s.find("diff --git")
    return s[:i] if i >= 0 else ""


def apply(repo: Path, patch: Path, reverse: bool = False, check: bool = False, cwd: Path | None = None):
    args = ["git", "apply", "--whitespace=nowarn", "--ignore-whitespace"]
    if reverse:
        args.append("--reverse")
    if check:
        args.append("--check")
    return run(args + [str(patch)], cwd or repo)


def regen(series_dir: Path) -> int:
    spec, repo = load(series_dir)
    if spec.get("new_files"):
        run(["git", "add", "-N", *spec["new_files"]], repo)
    exact = [series_dir / p["file"] for p in spec["patches"] if p.get("exact")]
    for f in reversed(exact):  # out of the tree while the others are regenerated
        r = apply(repo, f, reverse=True)
        if r.returncode != 0:
            raise SystemExit(f"cannot take {f.name} out of the tree: {r.stderr.decode('utf-8', 'replace')[:300]}")
    try:
        for p in spec["patches"]:
            if p.get("exact"):
                continue
            f = series_dir / p["file"]
            header = header_of(f) if p.get("header", "keep") == "keep" else p["header"]
            diff = run(["git", "diff", "--", *p["paths"]], repo).stdout.decode("utf-8")
            f.write_bytes((header + diff).encode("utf-8"))
            print(f"{f.name}: {diff.count(chr(10))} lines of diff")
    finally:
        for f in exact:
            r = apply(repo, f)
            if r.returncode != 0:
                raise SystemExit(f"cannot put {f.name} back: {r.stderr.decode('utf-8', 'replace')[:300]}")
    return verify(series_dir)


def touched(series_dir: Path) -> list[str]:
    names = set()
    for f in series_dir.glob("*.patch"):
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.startswith("diff --git a/"):
                names.add(ln.split(" b/")[1])
    return sorted(names)


def clean_copy(repo: Path, rev: str) -> Path:
    d = Path(tempfile.mkdtemp(prefix="series_"))
    tar = d / "tree.tar"
    run(["git", "archive", rev, "-o", str(tar)], repo, check=True)
    w = d / "tree"
    w.mkdir()
    subprocess.run(["tar", "-xf", str(tar), "-C", str(w)], check=True)
    return w


def verify(series_dir: Path) -> int:
    spec, repo = load(series_dir)
    order = [series_dir / p["file"] for p in spec["patches"]]
    ok = True
    for f in order:
        r = apply(repo, f, reverse=True, check=True)
        print(f"{f.name}: {'is in the working tree' if r.returncode == 0 else 'DOES NOT MATCH the working tree: ' + r.stderr.decode('utf-8', 'replace')[:200]}")
        ok &= r.returncode == 0
    w = clean_copy(repo, "HEAD")
    try:
        for f in order:
            r = apply(repo, f, cwd=w)
            print(f"clean checkout + {f.name}: {'OK' if r.returncode == 0 else 'FAILED ' + r.stderr.decode('utf-8', 'replace')[:300]}")
            ok &= r.returncode == 0
        if ok:
            names = touched(series_dir)
            bad = [n for n in names if (w / n).read_bytes().replace(b"\r\n", b"\n") != (repo / n).read_bytes().replace(b"\r\n", b"\n")]
            print(f"files compared: {len(names)}; different from the working tree: {bad}")
            ok &= not bad
    finally:
        shutil.rmtree(w.parent, ignore_errors=True)
    return 0 if ok else 1


def upgrade_check(series_dir: Path, tag: str) -> int:
    spec, repo = load(series_dir)
    run(["git", "fetch", "-q", "origin", "tag", tag, "--no-tags"], repo)
    w = clean_copy(repo, tag)
    ok = True
    try:
        for p in spec["patches"]:
            f = series_dir / p["file"]
            r = apply(repo, f, cwd=w)
            if r.returncode == 0:
                print(f"OK    {f.name}")
            else:
                ok = False
                print(f"FAIL  {f.name}\n      " + "\n      ".join(r.stderr.decode("utf-8", "replace").strip().splitlines()[:4]))
    finally:
        shutil.rmtree(w.parent, ignore_errors=True)
    print("a patch that fails here (and everything after it that needs it) must be fixed by hand on the new tag")
    return 0 if ok else 1


def main() -> int:
    a = sys.argv[1:]
    if not a or a[0] not in ("regen", "verify", "upgrade-check"):
        print(__doc__)
        return 2
    if a[0] == "upgrade-check":
        if len(a) < 2:
            print("upgrade-check needs a tag, e.g. b11641")
            return 2
        return upgrade_check(Path(a[2]) if len(a) > 2 else DEFAULT_DIR, a[1])
    series_dir = Path(a[1]) if len(a) > 1 else DEFAULT_DIR
    return regen(series_dir) if a[0] == "regen" else verify(series_dir)


if __name__ == "__main__":
    sys.exit(main())
