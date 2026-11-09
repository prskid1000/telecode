"""VRAM readings — first NVIDIA GPU only, never raises.

Used by the supervisor's guards (ceiling, preflight, unload confirmation) and the GGUF inspector's
cache-size suggestion. Read through NVML (nvml.dll / libnvidia-ml.so, the library nvidia-smi itself uses)
via ctypes: a reading takes well under a millisecond, where starting nvidia-smi took 0.4-2 s each time and
a model load took a dozen of them. The values are nvidia-smi's (memory info v2: `used` leaves out the
driver-reserved memory, as nvidia-smi shows it). nvidia-smi remains the fallback when NVML cannot be loaded.
A missing driver (AMD / Intel) or any failure answers None; callers skip their check rather than guess.
"""
from __future__ import annotations

import ctypes
import logging
import shutil
import subprocess
import sys
import threading
from typing import Optional

_LOG = logging.getLogger("telecode.llamacpp.vram")


class _MemoryV2(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint), ("total", ctypes.c_ulonglong), ("reserved", ctypes.c_ulonglong),
                ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class _ProcessInfo(ctypes.Structure):  # nvmlProcessInfo_v2_t (the _v3 query's element)
    _fields_ = [("pid", ctypes.c_uint), ("usedGpuMemory", ctypes.c_ulonglong),
                ("gpuInstanceId", ctypes.c_uint), ("computeInstanceId", ctypes.c_uint)]


_NVML_LOCK = threading.Lock()
_nvml = None          # the loaded library once initialised
_nvml_dev = None      # handle of GPU 0
_nvml_failed = False  # do not retry a library that cannot be loaded


def _nvml_handle():
    """(library, device handle) or (None, None). Loaded and initialised once."""
    global _nvml, _nvml_dev, _nvml_failed
    if _nvml is not None or _nvml_failed:
        return _nvml, _nvml_dev
    with _NVML_LOCK:
        if _nvml is not None or _nvml_failed:
            return _nvml, _nvml_dev
        try:
            lib = ctypes.CDLL("nvml.dll" if sys.platform == "win32" else "libnvidia-ml.so.1")
            if lib.nvmlInit_v2() != 0:
                raise OSError("nvmlInit_v2 failed")
            dev = ctypes.c_void_p()
            if lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev)) != 0:
                raise OSError("no GPU 0")
            _nvml_dev, _nvml = dev, lib
        except Exception as exc:  # noqa: BLE001 — fall back to nvidia-smi
            _nvml_failed = True
            _LOG.debug("NVML unavailable, using nvidia-smi: %s", exc)
    return _nvml, _nvml_dev


def _nvml_memory() -> Optional[_MemoryV2]:
    lib, dev = _nvml_handle()
    if lib is None:
        return None
    m = _MemoryV2()
    m.version = ctypes.sizeof(_MemoryV2) | (2 << 24)  # NVML_STRUCT_VERSION(Memory, 2)
    try:
        if lib.nvmlDeviceGetMemoryInfo_v2(dev, ctypes.byref(m)) != 0:
            return None
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("NVML memory info failed: %s", exc)
        return None
    return m


def _nvml_mib(field: str) -> Optional[int]:
    m = _nvml_memory()
    if m is None:
        return None
    return int((getattr(m, field) + 512 * 1024) // (1024 * 1024))  # rounded like nvidia-smi (never 1 MiB under it)


_NVML_FIELD = {"memory.free": "free", "memory.total": "total", "memory.used": "used"}


def _query(field: str, timeout: float = 5.0) -> Optional[int]:
    key = _NVML_FIELD.get(field)
    v = _nvml_mib(key) if key else None
    return v if v is not None else _query_smi(field, timeout)


def _query_smi(field: str, timeout: float = 5.0) -> Optional[int]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        out = subprocess.run(
            [exe, f"--query-gpu={field}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout, creationflags=creation,
        )
        if out.returncode != 0:
            return None
        first = (out.stdout or "").strip().splitlines()[0].strip()
        return int(float(first))
    except Exception as exc:
        _LOG.debug("nvidia-smi %s failed: %s", field, exc)
        return None


def free_vram_mib() -> Optional[int]:
    """Free memory (MiB) of the first NVIDIA GPU, or None when it cannot be read."""
    return _query("memory.free")


def total_vram_mib() -> Optional[int]:
    """Total memory (MiB) of the first NVIDIA GPU, or None when it cannot be read."""
    return _query("memory.total")


def used_vram_mib() -> Optional[int]:
    """Used memory (MiB) of the first NVIDIA GPU, or None when it cannot be read."""
    return _query("memory.used", timeout=3.0)

def _nvml_compute_pids() -> Optional[set[int]]:
    lib, dev = _nvml_handle()
    if lib is None:
        return None
    try:
        count = ctypes.c_uint(0)
        r = lib.nvmlDeviceGetComputeRunningProcesses_v3(dev, ctypes.byref(count), None)
        if r == 0:
            return set()
        if r != 7:  # NVML_ERROR_INSUFFICIENT_SIZE: there are processes, ask again with room for them
            return None
        n = count.value + 8
        arr = (_ProcessInfo * n)()
        count = ctypes.c_uint(n)
        if lib.nvmlDeviceGetComputeRunningProcesses_v3(dev, ctypes.byref(count), arr) != 0:
            return None
        return {int(arr[i].pid) for i in range(count.value)}
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("NVML compute processes failed: %s", exc)
        return None


def compute_app_pids(timeout: float = 3.0) -> Optional[set[int]]:
    """Pids holding a compute context on the GPU (nvidia-smi --query-compute-apps), or None when it cannot be read."""
    pids = _nvml_compute_pids()
    if pids is not None:
        return pids
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        out = subprocess.run(
            [exe, "--query-compute-apps=pid", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=timeout, creationflags=creation,
        )
        if out.returncode != 0:
            return None
        return {int(line.strip()) for line in (out.stdout or "").splitlines() if line.strip().isdigit()}
    except Exception as exc:
        _LOG.debug("nvidia-smi compute apps failed: %s", exc)
        return None


def _query_str(field: str, timeout: float = 5.0) -> str:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return ""
    creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        out = subprocess.run(
            [exe, f"--query-gpu={field}", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=timeout, creationflags=creation,
        )
        if out.returncode != 0:
            return ""
        return (out.stdout or "").strip().splitlines()[0].strip()
    except Exception as exc:
        _LOG.debug("nvidia-smi %s failed: %s", field, exc)
        return ""


import functools
import re

@functools.lru_cache(maxsize=1)
def is_blackwell_gpu() -> bool:
    """Return True if an NVIDIA Blackwell architecture GPU (e.g. RTX 50-series, SM 12.x) is detected."""
    name = _query_str("name")
    if name and re.search(r"RTX\s*50|Blackwell|B100|B200|GB200", name, re.IGNORECASE):
        return True
    cap = _query_str("compute_cap")
    if cap:
        try:
            return float(cap.split(".")[0]) >= 12
        except ValueError:
            pass
    return False
