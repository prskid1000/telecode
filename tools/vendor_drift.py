"""Report drift between telecode's vendored llama.cpp patches, the vendor's
branch, and upstream — and emit a conflict report an AI can act on.

The problem this exists for: `patches/llama.cpp/<vendor>/` holds a whole
upstream fork, re-expressed as a patch series against one upstream tag. Three
things move underneath it independently:

  upstream      ggml-org/llama.cpp        — constantly
  the vendor    their fork's branch       — they add features, and they rebase
  our series    what we actually carry    — only when we regenerate

Any of the three moving invalidates assumptions the other two were built on.
Left unwatched, the first symptom is ten patches failing `--check` at once with
no indication of which of the three moved, or how far.

So this tool answers, per vendor:

  1. Does our series still apply to the tag it was generated for?      (integrity)
  2. Does it apply to the newest upstream tag?                          (drift)
  3. Has the vendor added work since the commit we ported from?         (catch-up)
  4. Has the vendor rebased onto newer upstream?                        (opportunity)

and, when something does not apply, prints the actual conflict hunks with
context, grouped by file, in a form that can be handed to a model to rewrite
the patches — which is the whole point. `git apply --check` says "no"; it does
not say what changed, and that is the part worth automating.

`collisions` is a different axis: two vendored forks that both claim ggml type
id 142, or both register the arch string "dspark", compile fine and then
silently misread weights. Each vendor declares what it claims in VENDOR.json
and this cross-checks them.

Usage:
    python tools/vendor_drift.py status
    python tools/vendor_drift.py conflicts prism [--onto b11065] [--out FILE]
    python tools/vendor_drift.py collisions

Nothing here touches the real build checkout (`llamacpp.custom_build.source_dir`);
it manages its own blobless clone under `data/vendor-src/`.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

UPSTREAM = "https://github.com/ggml-org/llama.cpp.git"

REPO = Path(__file__).resolve().parent.parent
PATCH_ROOT = REPO / "patches" / "llama.cpp"
SRC = REPO / "data" / "vendor-src" / "llama.cpp"

CONFLICT_CTX = 4


# ── git plumbing ─────────────────────────────────────────────────────

def git(*args: str, cwd: Path | None = None, check: bool = True,
        quiet: bool = False) -> str:
    kw: dict = {"capture_output": True, "text": True, "encoding": "utf-8",
                "errors": "replace", "cwd": str(cwd or SRC)}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000
    res = subprocess.run(["git", *args], **kw)
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{res.stderr.strip()}")
    if res.returncode != 0 and not quiet:
        return ""
    return res.stdout.strip()


def git_ok(*args: str, cwd: Path | None = None) -> bool:
    kw: dict = {"capture_output": True, "cwd": str(cwd or SRC)}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000
    return subprocess.run(["git", *args], **kw).returncode == 0


# ── vendor metadata ──────────────────────────────────────────────────

class Vendor:
    def __init__(self, path: Path):
        self.dir = path
        self.name = path.name
        meta = json.loads((path / "VENDOR.json").read_text(encoding="utf-8"))
        self.repo: str = meta["repo"]
        self.branch: str = meta["branch"]
        self.vendor_commit: str = meta["vendor_commit"]
        self.vendor_base: str = meta.get("vendor_base", "")
        self.ported_onto: str = meta["ported_onto"]
        self.claims: dict = meta.get("claims", {})
        self.meta = meta

    @property
    def patches(self) -> list[Path]:
        return sorted(self.dir.glob("*.patch"))


def vendors() -> list[Vendor]:
    if not PATCH_ROOT.is_dir():
        return []
    return [Vendor(d) for d in sorted(PATCH_ROOT.iterdir())
            if d.is_dir() and (d / "VENDOR.json").is_file()]


# ── the managed checkout ─────────────────────────────────────────────

def ensure_src(vs: list[Vendor], progress=print) -> None:
    """Blobless clone of upstream plus a remote per vendor. Never the build tree."""
    if not (SRC / ".git").is_dir():
        SRC.parent.mkdir(parents=True, exist_ok=True)
        progress(f"cloning upstream -> {SRC} (blobless, first run only)")
        subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout",
                        UPSTREAM, str(SRC)], check=True)
        git("remote", "rename", "origin", "upstream")
    git("fetch", "--filter=blob:none", "--tags", "--force", "upstream", quiet=True)
    for v in vs:
        if v.name not in git("remote").split():
            git("remote", "add", v.name, v.repo)
        progress(f"fetching {v.name}")
        git("fetch", "--filter=blob:none", "--force", v.name, quiet=True)


def latest_tag() -> str:
    tags = [t for t in git("tag", "--sort=-creatordate").splitlines()
            if re.fullmatch(r"b\d+", t)]
    return tags[0] if tags else ""


def worktree(ref: str, name: str) -> Path:
    wt = SRC.parent / f"wt-{name}"
    if wt.exists():
        git("worktree", "remove", str(wt), "--force", check=False, quiet=True)
    git("worktree", "add", "--detach", "-f", str(wt), ref)
    return wt


# ── status ───────────────────────────────────────────────────────────

def applies(wt: Path, patches: list[Path], three_way: bool = False) -> list[tuple[str, bool]]:
    out = []
    for p in patches:
        args = ["apply", "--check"] + (["--3way"] if three_way else []) + [str(p)]
        out.append((p.name, git_ok(*args, cwd=wt)))
    return out


def cmd_status(_args) -> int:
    vs = vendors()
    if not vs:
        print("no vendored patch sets found under patches/llama.cpp/*/VENDOR.json")
        return 0
    ensure_src(vs)
    newest = latest_tag()
    print(f"\nupstream newest tag: {newest}\n")

    rc = 0
    for v in vs:
        print("=" * 68)
        print(f"vendor: {v.name}    {v.repo}  ({v.branch})")
        print("=" * 68)

        # 1/2. integrity + drift
        for label, ref in (("generated for", v.ported_onto), ("newest upstream", newest)):
            if not ref or not git_ok("rev-parse", "--verify", f"{ref}^{{commit}}"):
                print(f"  {label:<16} {ref or '?'}: NOT FOUND upstream")
                continue
            wt = worktree(ref, f"{v.name}-{ref}")
            plain = applies(wt, v.patches)
            ok = sum(1 for _, o in plain if o)
            note = ""
            if ok != len(plain):
                three = applies(wt, v.patches, three_way=True)
                ok3 = sum(1 for _, o in three if o)
                note = f"   (with --3way: {ok3}/{len(three)})"
            print(f"  {label:<16} {ref}: {ok}/{len(plain)} apply{note}")
            if ref == v.ported_onto and ok != len(plain):
                print("    !! the series does not cleanly apply to the tag it was")
                print("       generated for — the patches have been edited by hand,")
                print("       or VENDOR.json is stale. Regenerate before trusting it.")
                rc = 1
            for nm, o in plain:
                if not o:
                    print(f"       FAIL {nm}")
            git("worktree", "remove", str(wt), "--force", check=False, quiet=True)

        # 3. vendor catch-up
        head = git("rev-parse", f"{v.name}/{v.branch}")
        if head != v.vendor_commit:
            n = git("rev-list", "--count", f"{v.vendor_commit}..{head}", check=False)
            print(f"\n  vendor has moved: {v.vendor_commit[:9]} -> {head[:9]} ({n} new commit(s))")
            print("  new work you have not ported:")
            log = git("log", "--oneline", "--no-merges",
                      f"{v.vendor_commit}..{head}", check=False)
            for line in log.splitlines()[:20]:
                print(f"    {line}")
            extra = len(log.splitlines()) - 20
            if extra > 0:
                print(f"    ... and {extra} more")
        else:
            print(f"\n  vendor branch unchanged since the port ({head[:9]})")

        # 4. did the vendor rebase?
        base = git("merge-base", "upstream/master", f"{v.name}/{v.branch}", check=False)
        if base:
            desc = git("describe", "--tags", base, check=False) or base[:9]
            if v.vendor_base and base != v.vendor_base:
                print(f"  ** vendor REBASED: base is now {desc} (was {v.vendor_base[:9]})")
                print("     Regenerating from their new base is cheaper and safer than")
                print("     carrying your own merge — it inherits their testing.")
            else:
                print(f"  vendor base: {desc} (unchanged)")
        print()
    return rc


# ── conflicts ────────────────────────────────────────────────────────

def cmd_conflicts(args) -> int:
    vs = {v.name: v for v in vendors()}
    if args.vendor not in vs:
        print(f"unknown vendor {args.vendor!r}; known: {', '.join(vs) or '(none)'}")
        return 2
    v = vs[args.vendor]
    ensure_src([v])
    ref = args.onto or latest_tag()
    if not git_ok("rev-parse", "--verify", f"{ref}^{{commit}}"):
        print(f"ref {ref!r} not found upstream")
        return 2

    wt = worktree(ref, f"{v.name}-conflicts")
    lines: list[str] = []
    w = lines.append
    w(f"# Conflict report: {v.name} onto {ref}")
    w("")
    w(f"Series:       patches/llama.cpp/{v.name}/ ({len(v.patches)} patches)")
    w(f"Generated for: {v.ported_onto}")
    w(f"Applying onto: {ref}")
    w("")
    w("Each patch below was applied with `git apply --3way`. Hunks git could")
    w("merge on its own are already resolved and are NOT shown; what follows is")
    w("only what needs a human or model decision.")
    w("")
    w("`<<<<<<< ours` is the VENDOR's code (what the patch carries).")
    w("`>>>>>>> theirs` is UPSTREAM at " + ref + ".")
    w("")
    w("Resolve toward: upstream's code PLUS the vendor's feature. Check first")
    w("whether upstream has since implemented the vendor's feature itself — if")
    w("so, keep upstream's and drop the vendor's parallel copy.")
    w("")

    clean, conflicted, failed = [], [], []
    for p in v.patches:
        if git_ok("apply", "--check", str(p), cwd=wt):
            git("apply", str(p), cwd=wt)
            clean.append(p.name)
            continue
        if git_ok("apply", "--3way", str(p), cwd=wt):
            clean.append(p.name)
            continue
        # --3way applies what it can and leaves markers; a nonzero exit with
        # markers present is a conflict, without them it is a hard failure.
        if _markers(wt):
            conflicted.append(p.name)
        else:
            failed.append(p.name)

    w("## Summary")
    w("")
    w(f"- applied cleanly: {len(clean)}")
    w(f"- conflicted:      {len(conflicted)}  {', '.join(conflicted)}")
    w(f"- could not apply: {len(failed)}  {', '.join(failed)}")
    w("")

    files = _markers(wt)
    if files:
        w(f"## Conflicts ({len(files)} file(s))")
        for rel in files:
            w("")
            w("=" * 68)
            w(f"### {rel}")
            w("=" * 68)
            w("")
            w(_render(wt / rel))
    else:
        w("No conflict markers — the series applies (possibly via 3-way).")

    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}  ({len(files)} conflicted file(s))")
    else:
        print(text)
    git("worktree", "remove", str(wt), "--force", check=False, quiet=True)
    return 0


def touched_dirs(v: Vendor) -> set[str]:
    """Directories the vendor's series edits, from the patch headers."""
    out: set[str] = set()
    for p in v.patches:
        for m in re.finditer(r'^diff --git "?a/(.+?)"? "?b/',
                             p.read_text(encoding="utf-8", errors="replace"), re.M):
            out.add(str(Path(m.group(1)).parent).replace("\\", "/"))
    return out


def cmd_orphans(args) -> int:
    """Files upstream ADDED next to code the vendor patches.

    This is the failure mode with no error message. When upstream moves a struct
    into a brand-new header, that header is a pure addition — it conflicts with
    nothing, so the vendor's additions to that struct are silently dropped and
    the series still applies cleanly. It bit us once already: b11065 moved
    `vk_device_struct` into `ggml-vulkan-types.h` and the fork's FWHT pipeline
    members simply vanished.

    A new file cannot be diffed against a vendor change that predates it, so
    there is nothing to detect mechanically. What this does instead is narrow
    where to look: new upstream files sitting in directories the series already
    edits. It is a hint, not a proof — the only real check is compiling.
    """
    vs = {v.name: v for v in vendors()}
    if args.vendor not in vs:
        print(f"unknown vendor {args.vendor!r}; known: {', '.join(vs) or '(none)'}")
        return 2
    v = vs[args.vendor]
    ensure_src([v])
    new = args.onto or latest_tag()
    old = v.ported_onto
    for ref in (old, new):
        if not git_ok("rev-parse", "--verify", f"{ref}^{{commit}}"):
            print(f"ref {ref!r} not found upstream")
            return 2

    dirs = touched_dirs(v)
    added = [l.split("\t", 1)[1]
             for l in git("diff", "--name-status", "--diff-filter=A",
                          old, new, check=False).splitlines() if "\t" in l]
    hits = [f for f in added if str(Path(f).parent).replace("\\", "/") in dirs]

    print(f"\n{v.name}: upstream {old} -> {new}")
    print(f"  {len(added)} file(s) added upstream; "
          f"{len(hits)} of them in directories this series edits\n")
    if not hits:
        print("  nothing to review.")
        return 0
    print("  Review each: did upstream move something out of a file the series")
    print("  patches, and did the vendor have additions in the moved part?\n")
    for f in sorted(hits):
        print(f"    {f}")
    return 0


def _markers(wt: Path) -> list[str]:
    """Files carrying conflict markers. Text files only — a .gguf fixture can
    contain the byte sequence by coincidence, and grep will happily report it."""
    out = []
    for f in wt.rglob("*"):
        if not f.is_file() or ".git" in f.parts:
            continue
        if f.suffix.lower() in (".gguf", ".bin", ".png", ".jpg", ".zip", ".o", ".a"):
            continue
        try:
            txt = f.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        if re.search(r"^<{7} ", txt, re.M):
            out.append(f.relative_to(wt).as_posix())
    return sorted(out)


def _render(path: Path) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out, i, n = [], 0, 0
    while i < len(lines):
        if lines[i].startswith("<<<<<<<"):
            n += 1
            j = i
            while j < len(lines) and not lines[j].startswith(">>>>>>>"):
                j += 1
            lo, hi = max(0, i - CONFLICT_CTX), min(len(lines), j + 1 + CONFLICT_CTX)
            out.append(f"--- hunk {n} (lines {i + 1}-{j + 1}) ---")
            out.extend(f"{k + 1:6d}  {lines[k]}" for k in range(lo, hi))
            out.append("")
            i = j + 1
        else:
            i += 1
    return "\n".join(out)


# ── collisions ───────────────────────────────────────────────────────

def cmd_collisions(_args) -> int:
    """Two forks claiming the same ggml type id or arch string compile fine and
    then silently misread weights. This is the only check that catches it."""
    vs = vendors()
    if len(vs) < 1:
        print("no vendors")
        return 0

    seen: dict[str, dict] = {}
    bad = 0
    for v in vs:
        for kind, items in v.claims.items():
            pairs = items.items() if isinstance(items, dict) else {x: None for x in items}.items()
            for key, val in pairs:
                slot = f"{kind}:{key}"
                if slot in seen and seen[slot]["vendor"] != v.name:
                    print(f"!! COLLISION {kind} {key!r}: "
                          f"{seen[slot]['vendor']} and {v.name}")
                    bad += 1
                seen[slot] = {"vendor": v.name, "value": val}
                if val is not None:
                    vslot = f"{kind}={val}"
                    if vslot in seen and seen[vslot]["key"] != key:
                        print(f"!! COLLISION {kind} id {val}: "
                              f"{seen[vslot]['key']} ({seen[vslot]['vendor']}) "
                              f"and {key} ({v.name})")
                        bad += 1
                    seen[vslot] = {"key": key, "vendor": v.name}

    for v in vs:
        print(f"{v.name}: " + json.dumps(v.claims))
    print(f"\n{len(vs)} vendor(s), {bad} collision(s)")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="integrity + drift + vendor catch-up")
    c = sub.add_parser("conflicts", help="AI-readable conflict report")
    c.add_argument("vendor")
    c.add_argument("--onto", default="", help="upstream ref (default: newest tag)")
    c.add_argument("--out", default="", help="write to file instead of stdout")
    sub.add_parser("collisions", help="cross-vendor id/arch claim check")
    o = sub.add_parser("orphans",
                       help="new upstream files that may have silently orphaned "
                            "vendor additions")
    o.add_argument("vendor")
    o.add_argument("--onto", default="", help="upstream ref (default: newest tag)")
    args = ap.parse_args()
    return {"status": cmd_status, "conflicts": cmd_conflicts,
            "collisions": cmd_collisions, "orphans": cmd_orphans}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
