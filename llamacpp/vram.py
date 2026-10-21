"""VRAM readings via nvidia-smi — first NVIDIA GPU only, never raises.

Used by the supervisor's `min_free_vram_mib` guard and the GGUF inspector's
cache-size suggestion. A missing nvidia-smi (AMD / Intel / no driver) or any
failure answers None; callers skip their check rather than guess.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from typing import Optional

_LOG = logging.getLogger("telecode.llamacpp.vram")


def _query(field: str, timeout: float = 5.0) -> Optional[int]:
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
