"""Host RAM readings for the llama-server lifecycle — never raises, None when unknown.

`available_mib()`  RAM the OS can hand out now (free + standby/cached), in MiB.
`process_mib(pid)` RAM one process occupies now (resident / working set: what its exit gives back), in MiB.
                   Committed-but-untouched reservations (e.g. a prompt cache not filled yet) are not counted.

Windows uses kernel32/psapi through ctypes (telecode does not depend on psutil); Linux reads /proc.
"""
from __future__ import annotations

import logging
import sys
from typing import Optional

_LOG = logging.getLogger("telecode.llamacpp")

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]


def available_mib() -> Optional[int]:
    try:
        if sys.platform == "win32":
            st = _MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(st)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return None
            return int(st.ullAvailPhys // (1024 * 1024))
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception as exc:  # noqa: BLE001 — a reading, never a reason to fail
        _LOG.debug("available RAM unreadable: %s", exc)
    return None


def process_mib(pid: int) -> Optional[int]:
    try:
        if sys.platform == "win32":
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return None
            try:
                pmc = _PROCESS_MEMORY_COUNTERS_EX()
                pmc.cb = ctypes.sizeof(pmc)
                if not ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                    return None
                return int(pmc.WorkingSetSize // (1024 * 1024))
            finally:
                k32.CloseHandle(h)
        with open(f"/proc/{pid}/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("process %s memory unreadable: %s", pid, exc)
    return None


def describe_release(held_mib: Optional[int], avail_before: Optional[int], avail_after: Optional[int]) -> tuple[str, bool]:
    """(text for the unload line, whether RAM looks still held). Held RAM counts as returned when available RAM
    rose by at least half of what the process held; other programs allocating meanwhile make it noisy, so the
    flag only feeds a warning."""
    if held_mib is None or avail_before is None or avail_after is None:
        return "RAM freed: unknown", False
    freed = avail_after - avail_before
    text = f"RAM freed: {freed / 1024:.1f} GiB, process used {held_mib / 1024:.1f} GiB"
    return text, held_mib >= 1024 and freed < held_mib // 2


__all__ = ["available_mib", "process_mib", "describe_release"]
