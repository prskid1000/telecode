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

# Binaries worth copying out of a build. Anything else the build produces is
# left behind — we overlay onto a release install that already has the rest.
_ARTIFACTS = ("llama-server", "llama-cli", "llama-bench", "llama-mtmd-cli")

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
    """Checkout location. `llamacpp.custom_build.source_dir`, else a default
    under the settings dir. Configurable because a source tree is big and
    users keep code on a different drive than their config."""
    raw = str(app_config.get_nested("llamacpp.custom_build.source_dir", "") or "").strip()
    if raw:
        return Path(cfg.resolve_path(raw))
    return _settings_dir() / "build" / "llama.cpp"


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


def toolchain() -> dict[str, Any]:
    """What's available to build with — surfaced in the tray before you click."""
    out: dict[str, Any] = {
        "git": shutil.which("git"),
        "cmake": find_cmake(),
        "ninja": find_ninja(),
        "vcvars": str(_vcvars()) if _vcvars() else None,
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
        rc = _run(["git", "clone", "--filter=blob:none", UPSTREAM_REPO, str(src)],
                  src.parent, progress, timeout=3600)
        if rc != 0:
            return {"ok": False, "error": f"clone failed (exit {rc})"}
    else:
        progress("fetching upstream")
        if _git(["fetch", "--tags", "--force", "origin"], progress, timeout=1800) != 0:
            return {"ok": False, "error": "fetch failed"}

    target = tag.strip()
    if not target:
        progress("resolving latest release tag")
        try:
            import asyncio
            rel = asyncio.run(updater.fetch_latest_release())
            target = (rel or {}).get("tag_name", "") or ""
        except Exception as exc:
            progress(f"!! could not resolve latest tag: {exc}")
    if not target:
        target = "origin/master"
        progress("falling back to origin/master")

    progress(f"checking out {target}")
    if _git(["checkout", "--force", target], progress) != 0:
        return {"ok": False, "error": f"checkout {target} failed"}
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
    build_args = [cmake, "--build", str(bdir), "--config", "Release",
                  "--target", "llama-server", "-j", str(jobs)]

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
    if rec.get("sha256") != _sha256(binp):
        out["reason"] = "stock (binary replaced since it was patched)"
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

    ts = time.strftime("%Y%m%d-%H%M%S")
    backup = target / f".bak-{ts}"
    backup.mkdir(parents=True, exist_ok=True)

    src_root = built.parent
    copied: list[str] = []
    for stem in _ARTIFACTS:
        name = _exe(stem)
        s = src_root / name
        if not s.is_file():
            continue
        d = target / name
        if d.is_file():
            shutil.copy2(d, backup / name)
        shutil.copy2(s, d)
        copied.append(name)
        progress(f"installed {name}")

    if not copied:
        shutil.rmtree(backup, ignore_errors=True)
        return {"ok": False, "error": "no artifacts found next to the built binary"}

    # Same marker updater.restore_backup() reads, so Restore lists this like a
    # release backup rather than an unlabelled directory.
    try:
        (backup / ".telecode-version").write_text(
            updater.installed_version() or "unknown", encoding="utf-8")
    except OSError:
        pass

    # Record what we wrote so installed_patch_info() can tell a patched binary
    # from a stock one of the same build number.
    try:
        _marker_path().write_text(json.dumps({
            "tag": _git_out(["describe", "--tags", "--always"]),
            "head": _git_out(["rev-parse", "--short", "HEAD"]),
            "patches": applied_patches(),
            "sha256": _sha256(target / _exe("llama-server")),
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }, indent=2), encoding="utf-8")
    except OSError as exc:
        progress(f"!! could not write {MARKER}: {exc}")

    return {"ok": True, "installed": copied, "backup": str(backup)}
