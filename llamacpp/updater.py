"""llama.cpp release updater.

Pulls pre-built binary zips from `ggml-org/llama.cpp` GitHub releases and
overlays them onto the directory containing `llamacpp.binary`. Files
that get overwritten are moved into a per-install `.bak-<ts>/` first so
the swap is reversible.

Variant selection: each platform has a catalog of zip flavours
(cuda-12 / cuda-13 / vulkan / hip / cpu / …). `detect_variant()` probes
the local GPU to pick a sensible default; the user can override via the
tray (persisted to `llamacpp.update.variant`).
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp

from llamacpp import config as cfg

log = logging.getLogger("telecode.llamacpp.updater")


GITHUB_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
USER_AGENT = "telecode-llamacpp-updater/1.0"


# Per-platform variant catalog. Each entry: (key, [name-substrings, all required], label).
# Order matters for the tray dropdown (top = preferred default).
VARIANTS: dict[str, list[tuple[str, list[str], str]]] = {
    "win": [
        ("cuda-13",  ["win-cuda-13"],    "NVIDIA CUDA 13.x"),
        ("cuda-12",  ["win-cuda-12"],    "NVIDIA CUDA 12.x"),
        ("vulkan",   ["win-vulkan"],     "Vulkan (any GPU)"),
        ("hip",      ["win-hip"],        "AMD HIP (Radeon)"),
        ("sycl",     ["win-sycl"],       "Intel SYCL"),
        ("cpu",      ["win-cpu-x64"],    "CPU only (x64)"),
        ("cpu-arm",  ["win-cpu-arm64"],  "CPU only (ARM64)"),
        ("opencl",   ["win-opencl"],     "OpenCL (Adreno ARM64)"),
    ],
    "linux": [
        ("vulkan",   ["ubuntu-vulkan-x64"],   "Vulkan (Linux x64)"),
        ("vulkan-a", ["ubuntu-vulkan-arm64"], "Vulkan (Linux ARM64)"),
        ("rocm",     ["ubuntu-rocm"],         "AMD ROCm (Linux x64)"),
        ("sycl-fp16",["ubuntu-sycl-fp16"],    "Intel SYCL fp16 (Linux x64)"),
        ("sycl-fp32",["ubuntu-sycl-fp32"],    "Intel SYCL fp32 (Linux x64)"),
        ("openvino", ["ubuntu-openvino"],     "Intel OpenVINO (Linux x64)"),
        ("arm64",    ["ubuntu-arm64"],        "Linux ARM64 (CPU)"),
        ("x64",      ["ubuntu-x64"],          "Linux x64 (CPU)"),
    ],
    "mac": [
        ("arm64-kleidiai", ["macos-arm64-kleidiai"], "macOS ARM64 (KleidiAI)"),
        ("arm64",          ["macos-arm64"],          "macOS ARM64"),
        ("x64",            ["macos-x64"],            "macOS x64"),
    ],
}


def platform_key() -> str:
    if sys.platform == "win32":
        return "win"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def variants_for_platform() -> list[tuple[str, str]]:
    """[(key, label), …] for the current OS."""
    return [(k, label) for (k, _p, label) in VARIANTS[platform_key()]]


# ── Binary / install dir resolution ──────────────────────────────────

def _binary_path() -> Path:
    """Resolve the configured binary to an absolute path (best-effort)."""
    raw = cfg.binary() or "llama-server"
    p = Path(raw)
    if p.is_absolute():
        return p
    found = shutil.which(raw)
    return Path(found) if found else p


def install_dir() -> Path:
    """Directory whose contents we overlay with the new release."""
    return _binary_path().parent


def installed_version() -> Optional[str]:
    """Run `<binary> --version` and parse the build number. None on failure."""
    binp = _binary_path()
    if not binp.exists() and shutil.which(str(binp)) is None:
        return None
    try:
        kwargs: dict[str, Any] = {"capture_output": True, "text": True, "timeout": 8}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        res = subprocess.run([str(binp), "--version"], **kwargs)
        out = (res.stdout or "") + (res.stderr or "")
        for pat in (r"version:\s*(\d+)", r"\bbuild\b\s*[:=]\s*(\d+)", r"\bb(\d{3,6})\b"):
            m = re.search(pat, out)
            if m:
                return m.group(1)
    except Exception as exc:
        log.debug("installed_version failed: %s", exc)
    return None


# ── GitHub release lookup ────────────────────────────────────────────

async def fetch_latest_release() -> dict[str, Any]:
    headers = {"User-Agent": USER_AGENT,
               "Accept": "application/vnd.github+json"}
    async with aiohttp.ClientSession() as session:
        async with session.get(GITHUB_API, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as r:
            r.raise_for_status()
            return await r.json()


def build_from_tag(tag: str) -> Optional[str]:
    """'b6789' → '6789'."""
    m = re.search(r"b(\d+)", tag or "")
    return m.group(1) if m else None


def pick_cudart_asset(release: dict[str, Any], variant_key: str) -> Optional[dict[str, Any]]:
    """Return the matching `cudart-llama-bin-win-cuda-XX.Y-x64.zip` asset for a
    Windows CUDA variant. None otherwise.

    Layout matters because CUDA major versions are not ABI-compatible — a
    `llama-bin-win-cuda-13.x` build needs `cudart-llama-bin-win-cuda-13.x`
    DLLs alongside it.
    """
    if not variant_key.startswith("cuda-") or platform_key() != "win":
        return None
    major = variant_key.split("-", 1)[1]  # "12" or "13"
    needle = f"cudart-llama-bin-win-cuda-{major}"
    for asset in release.get("assets") or []:
        name = str(asset.get("name") or "").lower()
        if name.endswith(".zip") and name.startswith(needle):
            return asset
    return None


def _locate_cudart(major: int) -> Optional[Path]:
    """Return a path to `cudart64_<major>.dll` found via the install dir,
    CUDA_PATH, or PATH (in that order). None if nothing matches.

    Python 3.8+ doesn't search PATH for `ctypes.WinDLL`, so we walk the
    filesystem manually rather than relying on dynamic linking.
    """
    if sys.platform != "win32":
        return None
    dll = f"cudart64_{major}.dll"
    candidates: list[Path] = [install_dir()]
    import os as _os
    cuda_path = _os.environ.get("CUDA_PATH", "")
    if cuda_path:
        candidates.append(Path(cuda_path) / "bin" / "x64")
        candidates.append(Path(cuda_path) / "bin")
    for raw in (_os.environ.get("PATH", "") or "").split(_os.pathsep):
        raw = raw.strip().strip('"')
        if raw:
            candidates.append(Path(_os.path.expandvars(raw)))
    seen: set[str] = set()
    for c in candidates:
        key = str(c).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            p = c / dll
            if p.exists():
                return p
        except OSError:
            continue
    return None


def _read_pe_file_version(path: Path) -> Optional[tuple[int, int, int, int]]:
    """Return (major, minor, build, revision) from a Windows PE file's
    VS_FIXEDFILEINFO resource. None on failure / non-Windows.

    We use this to read cudart64_<major>.dll's actual version stamp so a
    user with CUDA Toolkit 13.0 isn't told they're compatible with a
    13.1-built llama-server.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        ver = ctypes.WinDLL("version", use_last_error=True)
        size = ver.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
            return None
        block = ctypes.c_void_p()
        block_len = wintypes.UINT()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(block), ctypes.byref(block_len)):
            return None
        # VS_FIXEDFILEINFO: DWORD dwSignature, dwStrucVersion,
        #                   dwFileVersionMS, dwFileVersionLS, ...
        u32 = (ctypes.c_uint32 * 13).from_address(block.value)
        ms, ls = u32[2], u32[3]
        return ((ms >> 16) & 0xFFFF, ms & 0xFFFF,
                (ls >> 16) & 0xFFFF, ls & 0xFFFF)
    except Exception as exc:
        log.debug("PE file-version read failed for %s: %s", path, exc)
        return None


def parse_cuda_version_from_asset(name: str) -> Optional[tuple[int, int]]:
    """Extract `(major, minor)` from an asset name like
    `llama-b9145-bin-win-cuda-13.1-x64.zip` → `(13, 1)`."""
    m = re.search(r"win-cuda-(\d+)\.(\d+)", (name or "").lower())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def _decode_cudart_version(located: Path) -> Optional[tuple[int, int]]:
    """Read `cudart64_<major>.dll`'s actual CUDA version.

    NVIDIA stamps `CUDART_VERSION = MAJOR*1000 + MINOR*10` into the low
    16 bits of `dwFileVersionLS`. e.g. 13000 → CUDA 13.0, 13010 → 13.1,
    12040 → 12.4. If that signal is unavailable, fall back to parsing the
    install path (e.g. `…\\CUDA\\v13.0\\…`).
    """
    ver = _read_pe_file_version(located)
    if ver is not None:
        ls_low = ver[3]
        if ls_low >= 1000:  # plausible CUDART_VERSION
            major = ls_low // 1000
            minor = (ls_low % 1000) // 10
            return (major, minor)
    # Fallback: parse the path.
    m = re.search(r"[\\/]CUDA[\\/]v(\d+)\.(\d+)[\\/]", str(located), re.IGNORECASE)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


def cudart_satisfies(asset_name: str) -> bool:
    """True if the locally available `cudart64_<major>.dll` is at least as
    new as the version the asset was built against.

    Forward-compat only: a 13.0 cudart can NOT host a binary built against
    13.1 (a 13.1 binary may reference symbols introduced in that minor).
    """
    want = parse_cuda_version_from_asset(asset_name)
    if want is None:
        return False
    major, minor = want
    located = _locate_cudart(major)
    if located is None:
        return False
    decoded = _decode_cudart_version(located)
    if decoded is None:
        return False
    sys_major, sys_minor = decoded
    if sys_major != major:
        return False
    return sys_minor >= minor


def cudart_present_for(major: int, target_dir: Optional[Path] = None) -> bool:
    """Back-compat wrapper — major-only presence check.

    Prefer `cudart_satisfies(asset_name)` which checks minor-version
    compatibility too.
    """
    return _locate_cudart(major) is not None


def cuda_major_of_variant(variant_key: str) -> Optional[int]:
    if not variant_key.startswith("cuda-"):
        return None
    try:
        return int(variant_key.split("-", 1)[1])
    except Exception:
        return None


def pick_asset(release: dict[str, Any], variant_key: str) -> Optional[dict[str, Any]]:
    """Return the asset dict whose name matches all patterns for `variant_key`.

    Filters:
      • must start with `llama-b<n>-bin-` (skips `cudart-*`, `*xcframework*`).
      • Windows = `.zip`; macOS / Linux = `.tar.gz`.
      • all variant patterns must be substrings.
      • arm64 zips only match arm64 variants and vice-versa.
    """
    pkey = platform_key()
    patterns: list[str] = []
    for k, pats, _label in VARIANTS[pkey]:
        if k == variant_key:
            patterns = [p.lower() for p in pats]
            break
    if not patterns:
        return None
    is_win = (pkey == "win")
    ext = ".zip" if is_win else ".tar.gz"
    for asset in release.get("assets") or []:
        name = str(asset.get("name") or "").lower()
        if not name.endswith(ext):
            continue
        if not name.startswith("llama-b"):
            continue
        if "-bin-" not in name:
            continue
        if all(p in name for p in patterns):
            return asset
    return None


# ── Auto-detect ──────────────────────────────────────────────────────

def detect_variant() -> str:
    pkey = platform_key()
    if pkey == "win":
        ver = _detect_cuda_major()
        if ver is not None:
            return "cuda-13" if ver >= 13 else "cuda-12"
        if _detect_amd_gpu():
            return "hip"
        return "vulkan"
    if pkey == "mac":
        import platform as _pl
        return "arm64" if _pl.machine().lower() in ("arm64", "aarch64") else "x64"
    # linux: no cuda zip published; vulkan covers most accelerators
    return "vulkan"


def _detect_cuda_major() -> Optional[int]:
    """Probe `nvidia-smi` for driver version, map to CUDA major (12 or 13)."""
    try:
        kwargs: dict[str, Any] = {"capture_output": True, "text": True, "timeout": 4}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            **kwargs,
        )
        if res.returncode != 0:
            return None
        line = (res.stdout or "").strip().splitlines()[:1]
        if not line:
            return None
        major = int(line[0].split(".")[0])
        # NVIDIA driver → max CUDA runtime: r580+ → CUDA 13, r525+ → CUDA 12
        if major >= 580:
            return 13
        if major >= 525:
            return 12
        return 12
    except Exception:
        return None


def _detect_amd_gpu() -> bool:
    if sys.platform != "win32":
        return False
    try:
        kwargs: dict[str, Any] = {"capture_output": True, "text": True, "timeout": 4,
                                  "creationflags": 0x08000000}
        res = subprocess.run(
            ["wmic", "path", "win32_VideoController", "get", "name"],
            **kwargs,
        )
        names = (res.stdout or "").lower()
        return "amd" in names or "radeon" in names
    except Exception:
        return False


# ── Download + install ───────────────────────────────────────────────

ProgressCB = Callable[[str, float], None]


async def download_and_install(
    asset: dict[str, Any],
    *,
    companions: Optional[list[dict[str, Any]]] = None,
    target_dir: Optional[Path] = None,
    progress: Optional[ProgressCB] = None,
) -> dict[str, Any]:
    """Download `asset` (+ `companions`), extract, overlay onto `target_dir`.

    Files we are about to overwrite are moved into `target_dir/.bak-<ts>/`
    first so the swap is reversible. Other files in `target_dir` are left
    alone. The llama supervisor is stopped first so DLLs aren't locked.

    `companions` is for paired assets (e.g. `cudart-llama-bin-win-cuda-13.1`
    alongside the main `llama-bin-win-cuda-13.1` zip). All are extracted
    into the same backup window so a single restore reverses everything.
    """
    queue: list[dict[str, Any]] = [asset] + list(companions or [])
    for a in queue:
        if not a.get("browser_download_url"):
            raise RuntimeError(f"asset missing browser_download_url: {a.get('name')}")

    target_dir = Path(target_dir) if target_dir else install_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    ts = int(time.time())
    tmp_root = target_dir.parent / f".llamacpp-update-{ts}"
    tmp_root.mkdir(parents=True, exist_ok=True)
    bak_dir = target_dir / f".bak-{ts}"

    total_bytes = sum(int(a.get("size") or 0) for a in queue) or 1
    bytes_done = 0

    files_moved = 0
    files_installed = 0
    asset_summary: list[str] = []

    try:
        # 1. Stop supervisor so DLLs unload.
        try:
            from process import _SUPERVISOR  # type: ignore
            if _SUPERVISOR and _SUPERVISOR.alive():
                if progress:
                    progress("Stopping llama-server…", 0.01)
                await _SUPERVISOR.stop()
        except Exception as exc:
            log.warning("supervisor stop failed (continuing anyway): %s", exc)

        async with aiohttp.ClientSession() as session:
            for idx, a in enumerate(queue, start=1):
                name = str(a.get("name") or "")
                url = a.get("browser_download_url")
                size = int(a.get("size") or 0)
                asset_summary.append(name)
                zip_path = tmp_root / name
                extract_dir = tmp_root / f"extracted-{idx}"
                extract_dir.mkdir()

                # ── Download ─────────────────────────────────────────
                if progress:
                    progress(f"Downloading {name} ({idx}/{len(queue)})…",
                             0.02 + 0.70 * (bytes_done / total_bytes))
                async with session.get(url, headers={"User-Agent": USER_AGENT}) as r:
                    r.raise_for_status()
                    expected = int(r.headers.get("Content-Length") or size or 0)
                    got = 0
                    last_emit = 0.0
                    with open(zip_path, "wb") as f:
                        async for chunk in r.content.iter_chunked(1 << 16):
                            f.write(chunk)
                            got += len(chunk)
                            if progress and (time.monotonic() - last_emit) > 0.25:
                                running = bytes_done + got
                                progress(
                                    f"Downloading {name}… {running / 1_048_576:.1f} / {total_bytes / 1_048_576:.1f} MiB",
                                    0.02 + 0.70 * (running / total_bytes),
                                )
                                last_emit = time.monotonic()
                bytes_done += max(got, size)

                # ── Extract ──────────────────────────────────────────
                if progress:
                    progress(f"Extracting {name}…", 0.76)
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(extract_dir)

                src_root = _find_payload_root(extract_dir, expect_llama=(idx == 1))
                if src_root is None:
                    raise RuntimeError(f"extracted zip {name} has no recognizable payload")

                # ── Overlay (per-file backup) ───────────────────────
                if progress:
                    progress(f"Installing files from {name}…", 0.82)
                for item in src_root.iterdir():
                    dst = target_dir / item.name
                    if dst.exists():
                        bak_dir.mkdir(parents=True, exist_ok=True)
                        bak_target = bak_dir / item.name
                        if not bak_target.exists():
                            # Only back up the first time each filename gets clobbered.
                            shutil.move(str(dst), str(bak_target))
                            files_moved += 1
                        else:
                            # Already shadowed in this run — just delete the live copy.
                            if dst.is_dir():
                                shutil.rmtree(dst, ignore_errors=True)
                            else:
                                dst.unlink()
                    shutil.move(str(item), str(dst))
                    files_installed += 1

        if progress:
            progress("Done.", 1.0)
        return {
            "ok": True,
            "installed_into": str(target_dir),
            "backup": str(bak_dir) if files_moved else None,
            "files_installed": files_installed,
            "files_replaced": files_moved,
            "asset": " + ".join(asset_summary) if asset_summary else "",
        }
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _find_payload_root(extract_dir: Path, *, expect_llama: bool) -> Optional[Path]:
    """Locate the directory containing the payload files.

    `expect_llama=True` requires `llama-server[.exe]`; `False` (used for
    cudart and similar companions) accepts any .dll/.so/.exe near the top.
    """
    if expect_llama:
        binary_name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
        matches = [p for p in extract_dir.rglob("llama-server*") if p.is_file()]
        exact = [m for m in matches if m.name.lower() == binary_name.lower()]
        if exact:
            return exact[0].parent
        if matches:
            return matches[0].parent
        return None

    # Companion zip: pick the shallowest directory containing payload files.
    payload_exts = (".dll", ".so", ".dylib", ".exe", ".lib")
    best: Optional[Path] = None
    best_depth = 10_000
    for p in extract_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in payload_exts:
            continue
        depth = len(p.relative_to(extract_dir).parts)
        if depth < best_depth:
            best = p.parent
            best_depth = depth
    return best or extract_dir
