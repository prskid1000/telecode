"""Build llama.cpp from source with telecode's local patches applied.

Deliberately shaped as *verify a patch against upstream*, not *maintain a fork*.
Every run starts from a clean checkout at a named upstream tag, applies the
series in `patches/llama.cpp/` on top, builds, and overlays the result onto the
same install dir `updater.py` writes to — reusing its `.bak-<ts>` snapshots, so
a custom build is reverted exactly like a bad release.

That shape matters. A permanent fork was tried before and abandoned: the cost
was never the patch, it was keeping it alive across upstream churn. So the
source tree here is disposable, the patches are the artifact, and
`git apply --check` failing is the signal that a patch has been upstreamed or
has bit-rotted — not something to paper over.

The one standing trap: `updater.py` overlays release zips onto the same
directory, so **a release update silently replaces a custom build**.
`status()` reports that (`build_is_installed`) by comparing the built binary
against the installed one.

Current series:
  0001-common-add-defer_loading-to-tool-definitions.patch
      Declares a tool to the sampling grammar while leaving its schema out of
      the rendered prompt. Upstream: ggml-org/llama.cpp#28179.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

import config as app_config
from llamacpp import config as cfg
from llamacpp import updater

log = logging.getLogger("telecode.llamacpp.patcher")

UPSTREAM_REPO = "https://github.com/ggml-org/llama.cpp.git"

# Which files an install copies is derived from the build output, never from a
# hardcoded list of executables. llama.cpp puts the actual logic in DLLs
# (llama-server-impl.dll, llama-common.dll, ggml-*.dll) behind a ~10 KB
# launcher exe, so copying only *.exe installs a stub over the release's DLLs
# and none of the patch — while still looking like it worked.
_LIB_SUFFIXES = (".exe", ".dll", ".so", ".dylib")

# Build these, not just llama-server. The release ships ~23 executables that
# all link the same DLLs; leaving them at the old build while replacing the
# DLLs under them produces launchers that load a mismatched library.
_BUILD_TARGETS = ("llama-server", "llama-cli", "llama-bench", "llama-mtmd-cli")

# Files in the install dir that belong to llama.cpp itself, so anything
# matching that our build does NOT produce is stale and must not be left
# beside our DLLs. Deliberately prefix-based: the CUDA runtime the release
# ships (cudart*, cublas*, nv*) is NOT ours to rebuild and must survive.
_OWNED_PREFIXES = ("ggml", "llama", "mtmd", "rpc-server")

Progress = Callable[[str], None]


def _noop(_: str) -> None:
    pass


# ── Paths ────────────────────────────────────────────────────────────

def _settings_dir() -> Path:
    # config._settings_dir() is the one source of truth for where settings.json
    # lives (TELECODE_SETTINGS can relocate it), so patches/ and build/ hang off
    # the same root rather than off cwd.
    return app_config._settings_dir()


def patch_dir() -> Path:
    """Where the patch series lives. Committed to the telecode repo."""
    return _settings_dir() / "patches" / "llama.cpp"


def patches() -> list[Path]:
    """The series, in apply order (lexicographic, hence the 0001- prefixes)."""
    d = patch_dir()
    if not d.is_dir():
        return []
    return sorted(d.glob("*.patch"))


def source_dir() -> Path:
    """Checkout location — `llamacpp.custom_build.source_dir`, else ~/Projects/llama.cpp.

    Defaults outside the settings dir on purpose: a checkout is several GB of
    someone else's source, which does not belong next to config, and people
    keep code on a different drive than their dotfiles. Configurable for the
    same reason.
    """
    raw = str(app_config.get_nested("llamacpp.custom_build.source_dir", "") or "").strip()
    if raw:
        return Path(cfg.resolve_path(raw))
    return Path.home() / "Projects" / "llama.cpp"


def build_dir() -> Path:
    return source_dir() / "build-telecode"


# ── Small helpers ────────────────────────────────────────────────────

def _run(cmd: list[str], cwd: Optional[Path], progress: Progress,
         *, timeout: int = 3600, env: Optional[dict[str, str]] = None) -> int:
    """Run a command, streaming stdout+stderr to `progress`. Returns exit code."""
    progress(f"$ {' '.join(cmd)}")
    kwargs: dict[str, Any] = {
        "cwd": str(cwd) if cwd else None,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
        "env": env,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except FileNotFoundError:
        progress(f"!! not found: {cmd[0]}")
        return 127
    assert proc.stdout is not None
    deadline = time.time() + timeout
    for line in proc.stdout:
        progress(line.rstrip())
        if time.time() > deadline:
            proc.kill()
            progress(f"!! timed out after {timeout}s")
            return 124
    return proc.wait()


def _git(args: list[str], progress: Progress = _noop, **kw) -> int:
    return _run(["git", *args], source_dir(), progress, **kw)


def _git_out(args: list[str]) -> str:
    """Capture git output; empty string on any failure."""
    try:
        kwargs: dict[str, Any] = {"cwd": str(source_dir()), "capture_output": True,
                                  "text": True, "timeout": 30}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000
        res = subprocess.run(["git", *args], **kwargs)
        return (res.stdout or "").strip()
    except Exception:
        return ""


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _exe(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def _built_binary() -> Optional[Path]:
    """The freshly built llama-server, wherever this generator put it."""
    for rel in ("bin", "bin/Release", "Release", "."):
        p = build_dir() / rel / _exe("llama-server")
        if p.is_file():
            return p
    return None


# ── Toolchain discovery (Windows needs vcvars for cl.exe on PATH) ─────

def _vswhere() -> Optional[Path]:
    base = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    p = Path(base) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    return p if p.is_file() else None


def _vs_install() -> Optional[Path]:
    vw = _vswhere()
    if not vw:
        return None
    try:
        res = subprocess.run(
            [str(vw), "-latest", "-products", "*",
             "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
             "-property", "installationPath"],
            capture_output=True, text=True, timeout=30,
            creationflags=0x08000000,
        )
        out = (res.stdout or "").strip().splitlines()
        return Path(out[0]) if out else None
    except Exception:
        return None


def find_cmake() -> Optional[str]:
    """cmake on PATH, else the one Visual Studio ships."""
    found = shutil.which("cmake")
    if found:
        return found
    vs = _vs_install()
    if vs:
        p = vs / "Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe"
        if p.is_file():
            return str(p)
    return None


def _vcvars() -> Optional[Path]:
    vs = _vs_install()
    if not vs:
        return None
    p = vs / "VC/Auxiliary/Build/vcvars64.bat"
    return p if p.is_file() else None


def find_ninja() -> Optional[str]:
    """ninja on PATH, else the one Visual Studio ships.

    Worth the extra lookup: on Windows the build runs inside a vcvars shell
    where ninja *is* on PATH, but this probe runs outside it. Missing it would
    silently fall back to the much slower MSBuild generator.
    """
    found = shutil.which("ninja")
    if found:
        return found
    vs = _vs_install()
    if vs:
        for rel in ("Common7/IDE/CommonExtensions/Microsoft/CMake/Ninja/ninja.exe",):
            p = vs / rel
            if p.is_file():
                return str(p)
    return None


def cuda_toolkit() -> dict[str, Any]:
    """The local CUDA toolkit, and whether it matches the installed runtime.

    This matters because install() copies executables only — not the CUDA
    runtime DLLs. The release zips ship a cudart matched to their own variant,
    so a locally built binary linked against a DIFFERENT CUDA major lands next
    to a cudart it cannot use and fails to start. Detect the mismatch before
    the build rather than after the binary is in place.
    """
    out: dict[str, Any] = {"nvcc": shutil.which("nvcc"), "version": "",
                           "major": None, "installed_major": None, "mismatch": False}
    if out["nvcc"]:
        try:
            kwargs: dict[str, Any] = {"capture_output": True, "text": True, "timeout": 15}
            if sys.platform == "win32":
                kwargs["creationflags"] = 0x08000000
            res = subprocess.run([out["nvcc"], "--version"], **kwargs)
            m = re.search(r"release (\d+)\.(\d+)", (res.stdout or "") + (res.stderr or ""))
            if m:
                out["version"] = f"{m.group(1)}.{m.group(2)}"
                out["major"] = int(m.group(1))
        except Exception as exc:
            log.debug("nvcc probe failed: %s", exc)
    try:
        out["installed_major"] = updater.cuda_major_of_variant(updater.detect_variant())
    except Exception:
        pass
    if out["major"] and out["installed_major"]:
        out["mismatch"] = out["major"] != out["installed_major"]
    return out


def toolchain() -> dict[str, Any]:
    """What's available to build with — surfaced in the tray before you click."""
    out: dict[str, Any] = {
        "git": shutil.which("git"),
        "cmake": find_cmake(),
        "ninja": find_ninja(),
        "vcvars": str(_vcvars()) if _vcvars() else None,
        "cuda": cuda_toolkit(),
    }
    out["ready"] = bool(out["git"] and out["cmake"] and
                        (sys.platform != "win32" or out["vcvars"]))
    return out


# ── Status ───────────────────────────────────────────────────────────

def applied_patches() -> list[str]:
    """Patches whose changes are already present in the working tree.

    Determined by asking git to reverse-apply: if that succeeds the patch is in.
    Cheaper and more honest than tracking state in a file that can go stale.
    """
    src = source_dir()
    if not (src / ".git").is_dir():
        return []
    out: list[str] = []
    for p in patches():
        kwargs: dict[str, Any] = {"cwd": str(src), "capture_output": True, "timeout": 60}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000
        try:
            res = subprocess.run(["git", "apply", "--reverse", "--check", str(p)], **kwargs)
            if res.returncode == 0:
                out.append(p.name)
        except Exception:
            continue
    return out


def status() -> dict[str, Any]:
    src = source_dir()
    built = _built_binary()
    installed = updater.install_dir() / _exe("llama-server")
    st: dict[str, Any] = {
        "source_dir": str(src),
        "source_present": (src / ".git").is_dir(),
        "origin": _git_out(["remote", "get-url", "origin"]),
        "checked_out": _git_out(["describe", "--tags", "--always"]),
        "head": _git_out(["rev-parse", "--short", "HEAD"]),
        "dirty": bool(_git_out(["status", "--porcelain"])),
        "patch_dir": str(patch_dir()),
        "patches": [p.name for p in patches()],
        "patches_applied": [],
        "build_dir": str(build_dir()),
        "built_binary": str(built) if built else None,
        "installed_binary": str(installed) if installed.is_file() else None,
        "installed_version": updater.installed_version(),
        "build_is_installed": False,
        "installed_patch": installed_patch_info(),
        "toolchain": toolchain(),
    }
    if st["source_present"]:
        st["patches_applied"] = applied_patches()
    # A release update overlays the same directory, so the custom build can be
    # silently replaced. Compare content rather than trusting a flag.
    if built and installed.is_file():
        st["build_is_installed"] = _sha256(built) == _sha256(installed)
    return st


# ── Operations ───────────────────────────────────────────────────────

def _normalize_origin(progress: Progress = _noop) -> None:
    """Force `origin` to the main llama.cpp repo.

    A previous origin is preserved as `fork` rather than dropped: this may be
    a checkout someone also pushes PR branches from, and silently destroying
    their push path to make our fetch tidy would be a poor trade.
    """
    cur = _git_out(["remote", "get-url", "origin"])
    if cur == UPSTREAM_REPO:
        return
    if cur:
        if not _git_out(["remote", "get-url", "fork"]):
            _git(["remote", "add", "fork", cur], progress)
            progress(f"previous origin kept as remote 'fork': {cur}")
        _git(["remote", "set-url", "origin", UPSTREAM_REPO], progress)
    else:
        _git(["remote", "add", "origin", UPSTREAM_REPO], progress)
    progress(f"origin -> {UPSTREAM_REPO}")


def fetch_source(tag: str = "", progress: Progress = _noop) -> dict[str, Any]:
    """Clone or update the checkout and hard-reset to `tag`.

    Empty tag = whatever `updater.fetch_latest_release()` considers current, so
    a patched build tracks the same version the release updater would install.
    Always resets: a patched tree is disposable, the patches are the artifact.
    """
    src = source_dir()
    src.parent.mkdir(parents=True, exist_ok=True)

    if not (src / ".git").is_dir():
        progress(f"cloning {UPSTREAM_REPO} -> {src}")
        # Full history is not needed and costs ~1.5 GB; tags are, for checkout.
        # --progress because git prints none of it when stderr is not a TTY,
        # which makes a multi-minute clone look like a hang.
        rc = _run(["git", "clone", "--progress", "--filter=blob:none",
                   UPSTREAM_REPO, str(src)],
                  src.parent, progress, timeout=3600)
        if rc != 0:
            return {"ok": False, "error": f"clone failed (exit {rc})"}
    else:
        # `origin` is always the main repo. A checkout living in a normal
        # projects folder tends to acquire someone's fork as origin, and then
        # "latest upstream release" quietly means "latest tag that fork
        # happens to have" — which is exactly what was happening here.
        _normalize_origin(progress)
        progress("fetching tags from origin")
        if _git(["fetch", "--progress", "--tags", "--force", "origin"],
                progress, timeout=1800) != 0:
            return {"ok": False, "error": f"fetch from {UPSTREAM_REPO} failed"}

    target = tag.strip()
    if not target:
        progress("resolving latest release tag")
        try:
            import asyncio
            rel = asyncio.run(updater.fetch_latest_release())
            target = (rel or {}).get("tag_name", "") or ""
        except Exception as exc:
            # Deliberately NOT falling back to origin/master. Master is ahead of
            # the newest release, may not build, and the patch may not apply to
            # it — and the user asked for "latest release", so quietly building
            # something else is worse than stopping. Name a tag to override.
            return {"ok": False,
                    "error": f"could not resolve the latest release tag ({exc}). "
                             f"Set Upstream Tag explicitly to build a known version."}
    if not target:
        return {"ok": False,
                "error": "no release tag resolved; set Upstream Tag explicitly"}
    progress(f"latest release resolves to {target}")

    progress(f"checking out {target}")
    if _git(["checkout", "--force", target], progress) != 0:
        return {"ok": False, "error":
                f"checkout {target} failed — the tag may not exist upstream"}
    _git(["reset", "--hard"], progress)
    _git(["clean", "-fd"], progress)
    return {"ok": True, "tag": target, "head": _git_out(["rev-parse", "--short", "HEAD"])}


def apply_patches(progress: Progress = _noop) -> dict[str, Any]:
    """Apply the series. A --check failure means the patch no longer fits —
    usually because it landed upstream. That's a result, not an error to hide."""
    src = source_dir()
    if not (src / ".git").is_dir():
        return {"ok": False, "error": "no source checkout — fetch first"}

    series = patches()
    if not series:
        return {"ok": True, "applied": [], "note": "no patches in the series"}

    applied: list[str] = []
    skipped: list[str] = []
    failed: list[dict[str, str]] = []
    already = set(applied_patches())

    for p in series:
        if p.name in already:
            progress(f"-- {p.name}: already applied, skipping")
            skipped.append(p.name)
            continue
        progress(f"-- {p.name}")
        if _git(["apply", "--check", str(p)], progress) != 0:
            progress(f"!! {p.name} does not apply — upstreamed, or bit-rotted")
            failed.append({"patch": p.name, "reason": "does not apply"})
            continue
        if _git(["apply", str(p)], progress) != 0:
            failed.append({"patch": p.name, "reason": "apply failed after check passed"})
            continue
        applied.append(p.name)

    return {"ok": not failed, "applied": applied, "skipped": skipped, "failed": failed}


def build(progress: Progress = _noop, *, cuda: Optional[bool] = None,
          jobs: int = 0) -> dict[str, Any]:
    """Configure + build. CUDA defaults to whatever variant the updater would
    have installed, so a patched build matches the release it replaces."""
    src = source_dir()
    if not src.is_dir():
        return {"ok": False, "error": "no source checkout — fetch first"}
    tc = toolchain()
    if not tc["ready"]:
        return {"ok": False, "error": f"toolchain incomplete: {tc}"}

    if cuda is None:
        # Absent key means ON. A CPU-only build silently installed over a CUDA
        # release is a large, hard-to-notice regression, so the safe default is
        # the expensive one; the updater's variant only decides when there is
        # no GPU to build for at all.
        cfg_cuda = app_config.get_nested("llamacpp.custom_build.cuda", None)
        if cfg_cuda is None:
            cuda = True
        else:
            cuda = bool(cfg_cuda)
    if not jobs:
        jobs = max(1, (os.cpu_count() or 4) - 2)

    bdir = build_dir()
    cmake = tc["cmake"]
    gen = ["-G", "Ninja"] if tc["ninja"] else []
    cfg_args = [
        cmake, "-B", str(bdir), "-S", str(src), *gen,
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DGGML_CUDA={'ON' if cuda else 'OFF'}",
        "-DLLAMA_BUILD_TESTS=OFF",
        "-DLLAMA_BUILD_EXAMPLES=OFF",
    ]
    if cuda:
        cu = tc["cuda"]
        if not cu["nvcc"]:
            return {"ok": False, "error": "GGML_CUDA is on but no nvcc on PATH — "
                                          "install the CUDA toolkit, or turn CUDA off"}
        if cu["mismatch"]:
            return {"ok": False, "error":
                    f"CUDA toolkit {cu['version']} would link against cudart "
                    f"{cu['major']}, but the installed llama.cpp is a CUDA "
                    f"{cu['installed_major']} build and ships that runtime. Installing "
                    f"the result would produce a binary that cannot start. Install a "
                    f"matching toolkit, or turn CUDA off."}
        # Build only for the GPU that is actually here. The release zips are fat
        # binaries covering every arch; we are not shipping this one anywhere, so
        # native is both faster to compile and smaller.
        cfg_args.append("-DCMAKE_CUDA_ARCHITECTURES=native")
        progress(f"CUDA toolkit {cu['version']} (arch: native)")
    build_args = [cmake, "--build", str(bdir), "--config", "Release",
                  "--target", *_BUILD_TARGETS, "-j", str(jobs)]

    if sys.platform == "win32":
        # cl.exe only exists on PATH inside a vcvars shell, so the whole
        # configure+build has to run in one cmd. Assert VSCMD_VER — a silently
        # failed vcvars otherwise looks like "no CMAKE_CXX_COMPILER".
        bat = bdir.parent / "telecode-build.bat"
        bat.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "@echo off",
            f'call "{tc["vcvars"]}" >nul',
            'if "%VSCMD_VER%"=="" ( echo VCVARS_FAILED & exit /b 90 )',
            "echo VSCMD_VER=%VSCMD_VER%",
            " ".join(f'"{a}"' if " " in a else a for a in cfg_args),
            "if errorlevel 1 ( echo CONFIGURE_FAILED & exit /b 91 )",
            " ".join(f'"{a}"' if " " in a else a for a in build_args),
            "if errorlevel 1 ( echo BUILD_FAILED & exit /b 92 )",
            "echo BUILD_OK",
        ]
        bat.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
        rc = _run(["cmd", "/c", str(bat)], src, progress, timeout=7200)
    else:
        rc = _run(cfg_args, src, progress, timeout=1800)
        if rc == 0:
            rc = _run(build_args, src, progress, timeout=7200)

    if rc != 0:
        return {"ok": False, "error": f"build failed (exit {rc})"}
    built = _built_binary()
    if not built:
        return {"ok": False, "error": "build reported success but no llama-server was produced"}
    progress(f"built {built}")
    return {"ok": True, "binary": str(built), "cuda": cuda}


MARKER = ".telecode-patched.json"


def _marker_path() -> Path:
    return updater.install_dir() / MARKER


def installed_patch_info() -> dict[str, Any]:
    """What the *installed* binary is, patch-wise.

    A build number cannot answer this — a patched b10775 and a stock b10775
    report the same version. So install() records the sha256 of what it wrote,
    and this compares it against the binary that is actually there now. That
    comparison is the whole point: the release updater overlays the same
    directory and would otherwise leave a stale marker claiming "patched" over
    a stock binary it just installed.
    """
    binp = updater.install_dir() / _exe("llama-server")
    out: dict[str, Any] = {"patched": False, "patches": [], "tag": "", "reason": ""}
    if not binp.is_file():
        out["reason"] = "no installed binary"
        return out
    mp = _marker_path()
    if not mp.is_file():
        out["reason"] = "stock (no patch marker)"
        return out
    try:
        rec = json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        out["reason"] = "unreadable marker"
        return out
    files = rec.get("files") or {}
    if not files:
        out["reason"] = "stock (marker predates per-file verification)"
        return out
    for name, digest in files.items():
        f = updater.install_dir() / name
        if not f.is_file() or _sha256(f) != digest:
            # One replaced file is enough: a release update overlays the whole
            # directory, and a partially-overwritten install is not "patched".
            out["reason"] = f"stock ({name} replaced since it was patched)"
            return out
    out.update(patched=True, patches=rec.get("patches") or [],
               tag=rec.get("tag") or "", reason="")
    return out


def build_patched(progress: Progress = _noop, *, tag: str = "") -> dict[str, Any]:
    """fetch -> apply -> build, as one action.

    Split into three buttons originally; that exposed an ordering the user has
    no reason to care about, and every intermediate state is either useless or
    a bug. Failures still stop at the step that failed and say which.
    """
    progress("== fetch / reset source")
    res = fetch_source(tag, progress)
    if not res.get("ok"):
        return {"ok": False, "step": "fetch", "error": res.get("error")}

    progress("== apply patches")
    ap = apply_patches(progress)
    if not ap.get("ok"):
        return {"ok": False, "step": "apply", "error": "one or more patches did not apply",
                "failed": ap.get("failed") or []}

    progress("== build")
    bd = build(progress)
    if not bd.get("ok"):
        return {"ok": False, "step": "build", "error": bd.get("error")}

    return {"ok": True, "tag": res.get("tag"), "head": res.get("head"),
            "applied": ap.get("applied") or [], "skipped": ap.get("skipped") or [],
            "binary": bd.get("binary"), "cuda": bd.get("cuda")}


def build_artifacts() -> list[Path]:
    """Every binary the build produced, next to llama-server."""
    built = _built_binary()
    if not built:
        return []
    out: list[Path] = []
    for f in sorted(built.parent.iterdir()):
        if not f.is_file():
            continue
        if sys.platform == "win32":
            if f.suffix.lower() in (".exe", ".dll"):
                out.append(f)
        elif f.suffix.lower() in ("", ".so", ".dylib") or ".so." in f.name:
            out.append(f)
    return out


def _probe_commit(binary: Path) -> str:
    """The commit `<binary> --version` reports, or '' if it will not run."""
    try:
        kwargs: dict[str, Any] = {"capture_output": True, "text": True, "timeout": 30}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000
        res = subprocess.run([str(binary), "--version"], **kwargs)
        m = re.search(r"commit\s+([0-9a-f]{7,40})",
                      (res.stdout or "") + (res.stderr or ""))
        return m.group(1) if m else ""
    except Exception:
        return ""


def install(progress: Progress = _noop) -> dict[str, Any]:
    """Overlay the built binaries onto the install dir, snapshotting what they
    replace into `.bak-<ts>/` — the same mechanism release installs use, so the
    tray's existing Restore works on a custom build too."""
    built = _built_binary()
    if not built:
        return {"ok": False, "error": "nothing built yet"}

    target = updater.install_dir()
    if not target.is_dir():
        return {"ok": False, "error": f"install dir does not exist: {target}"}

    # Probe BEFORE anything is copied. This labels the backup with the version
    # being replaced, which is what Version Manager shows next to it; reading it
    # afterwards would stamp every backup with the version that just replaced it.
    outgoing_version = updater.installed_version() or "unknown"

    ts = time.strftime("%Y%m%d-%H%M%S")
    backup = target / f".bak-{ts}"
    backup.mkdir(parents=True, exist_ok=True)

    artifacts = build_artifacts()
    if not artifacts:
        shutil.rmtree(backup, ignore_errors=True)
        return {"ok": False, "error": "no artifacts found next to the built binary"}

    # Everything llama.cpp-owned that our build does not produce is stale the
    # moment our DLLs land: a b10775 ggml-base.dll enumerates the backend DLLs
    # sitting next to it, and the release ships 15 ggml-cpu-* variants we do
    # not rebuild. Loading one of those into our base is an ABI mismatch that
    # `--version` cannot catch, because that path never loads a CPU backend.
    # Move them into the backup rather than leave them: a stale binary that
    # crashes at model load is worse than a missing one, and Restore (or
    # Update Now) brings the whole release back.
    ours = {f.name.lower() for f in artifacts}
    stale: list[Path] = []
    for f in target.iterdir():
        if not f.is_file() or f.suffix.lower() not in _LIB_SUFFIXES:
            continue
        n = f.name.lower()
        if n in ours or not n.startswith(_OWNED_PREFIXES):
            continue
        stale.append(f)

    copied: list[str] = []
    quarantined: list[str] = []
    digests: dict[str, str] = {}

    def _rollback(reason: str) -> None:
        progress(f"!! {reason} — rolling back {len(copied)} file(s)")
        for name in copied + quarantined:
            b = backup / name
            if b.is_file():
                shutil.copy2(b, target / name)
            elif name in copied:
                (target / name).unlink(missing_ok=True)
        shutil.rmtree(backup, ignore_errors=True)

    try:
        for f in stale:
            shutil.copy2(f, backup / f.name)
            f.unlink()
            quarantined.append(f.name)
        if quarantined:
            progress(f"moved {len(quarantined)} stale file(s) into the backup: "
                     + ", ".join(quarantined[:6])
                     + ("…" if len(quarantined) > 6 else ""))
        for f in artifacts:
            d = target / f.name
            if d.is_file():
                shutil.copy2(d, backup / f.name)
            shutil.copy2(f, d)
            copied.append(f.name)
            digests[f.name] = _sha256(d)
    except PermissionError as exc:
        # Windows will not let you overwrite a loaded exe or DLL. Failing
        # halfway leaves a mix of two builds, which is worse than not starting,
        # so undo everything and say what to do.
        _rollback(f"{exc.filename or 'a file'} is locked")
        return {"ok": False, "error":
                "llama-server is running — Windows locks its exe and DLLs. "
                "Stop it (tray: llama.cpp -> Stop, or let it idle-unload) and "
                "install again. Nothing was changed."}
    except OSError as exc:
        _rollback(f"copy failed: {exc}")
        return {"ok": False, "error": f"install failed: {exc}. Nothing was changed."}

    progress(f"installed {len(copied)} files: " + ", ".join(copied))

    # Verify by running it. The failure this catches is specific and was real:
    # a launcher exe from one build sitting on another build's DLLs starts,
    # prints a version, and reports the OLD commit — so "it ran" proves nothing
    # and only the commit does.
    want = _git_out(["rev-parse", "HEAD"])
    got = _probe_commit(target / _exe("llama-server"))
    if not got or not (want.startswith(got) or got.startswith(want[:len(got)])):
        _rollback(f"installed binary reports commit {got or '(would not run)'}, "
                  f"expected {want[:9]}")
        return {"ok": False, "error":
                f"install verification failed: the installed binary reports "
                f"commit {got or '(it would not run)'}, not {want[:9]}. "
                f"Rolled back."}
    progress(f"verified: installed binary reports commit {got}")

    # Same marker updater.restore_backup() reads, so this is listed and restored
    # exactly like a release backup — a custom build is undone by Version
    # Manager -> Restore, no separate mechanism. Kept on success on purpose:
    # it is the undo path, not scratch. Only a FAILED install removes it,
    # because then nothing was changed and it would describe nothing.
    try:
        (backup / ".telecode-version").write_text(outgoing_version, encoding="utf-8")
    except OSError:
        pass

    # Record what we wrote so installed_patch_info() can tell a patched binary
    # from a stock one of the same build number.
    try:
        _marker_path().write_text(json.dumps({
            "tag": _git_out(["describe", "--tags", "--always"]),
            "head": _git_out(["rev-parse", "--short", "HEAD"]),
            "patches": applied_patches(),
            "commit": got,
            "files": digests,
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }, indent=2), encoding="utf-8")
    except OSError as exc:
        progress(f"!! could not write {MARKER}: {exc}")

    return {"ok": True, "installed": copied, "quarantined": quarantined,
            "backup": str(backup)}
