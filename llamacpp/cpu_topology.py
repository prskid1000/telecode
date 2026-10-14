"""CPU core classes for --cpu-mask: which logical CPUs are performance cores.

Hybrid Intel parts (P + E cores) enumerate the two classes interleaved, so a
mask is the only way to pin a thread pool to one class. Windows reports each
physical core's `EfficiencyClass` (higher = faster) through
`GetLogicalProcessorInformationEx(RelationProcessorCore)`; this reads it and
turns the classes into hex masks the way llama-server's `--cpu-mask` wants them
(bit N = logical CPU N, processor groups laid end to end).

Read-only: nothing here writes settings — the tray offers the result as buttons.
"""
from __future__ import annotations

import logging
import os
import struct
import sys
from typing import Any

_LOG = logging.getLogger("telecode.llamacpp.cpu_topology")

_RELATION_PROCESSOR_CORE = 0
_ERROR_INSUFFICIENT_BUFFER = 122


def _mask_hex(cpus: list[int]) -> str:
    """Hex mask (lowercase, no prefix) with one bit per logical CPU; '' for none."""
    m = 0
    for c in cpus:
        m |= 1 << c
    return format(m, "x") if m else ""


def _result(total: int, perf: list[int], eff: list[int]) -> dict[str, Any]:
    return {
        "logical_cpus": total,
        "performance_mask_hex": _mask_hex(perf),
        "efficiency_mask_hex": _mask_hex(eff),
        "performance_cpus": perf,
        "efficiency_cpus": eff,
        "hybrid": bool(perf and eff),
    }


def _all_cpus() -> dict[str, Any]:
    """Non-hybrid answer: every logical CPU counts as a performance CPU."""
    n = os.cpu_count() or 1
    return _result(n, list(range(n)), [])


def _windows_cores() -> list[tuple[int, list[int]]]:
    """[(efficiency_class, [logical cpu, ...])] one entry per physical core."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = k32.GetLogicalProcessorInformationEx
    fn.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    fn.restype = wintypes.BOOL
    k32.GetActiveProcessorCount.argtypes = [wintypes.WORD]
    k32.GetActiveProcessorCount.restype = wintypes.DWORD
    k32.GetActiveProcessorGroupCount.restype = wintypes.WORD

    need = wintypes.DWORD(0)
    fn(_RELATION_PROCESSOR_CORE, None, ctypes.byref(need))
    if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER or need.value == 0:
        raise OSError("GetLogicalProcessorInformationEx: no size")
    buf = ctypes.create_string_buffer(need.value)
    if not fn(_RELATION_PROCESSOR_CORE, buf, ctypes.byref(need)):
        raise ctypes.WinError(ctypes.get_last_error())
    raw = buf.raw[:need.value]

    # Logical CPU numbering: groups are laid end to end by their active count.
    base: list[int] = []
    acc = 0
    for g in range(int(k32.GetActiveProcessorGroupCount()) or 1):
        base.append(acc)
        acc += int(k32.GetActiveProcessorCount(g))

    cores: list[tuple[int, list[int]]] = []
    off = 0
    while off + 8 <= len(raw):
        rel, size = struct.unpack_from("<II", raw, off)
        if size < 8 or off + size > len(raw):
            raise ValueError("malformed processor information record")
        if rel == _RELATION_PROCESSOR_CORE:
            # PROCESSOR_RELATIONSHIP: Flags(1) EfficiencyClass(1) Reserved(20)
            # GroupCount(2) GroupMask[] of {Mask u64, Group u16, Reserved u16[3]}.
            eff_class = raw[off + 9]
            (groups,) = struct.unpack_from("<H", raw, off + 30)
            cpus: list[int] = []
            for i in range(groups):
                mask, grp = struct.unpack_from("<QH", raw, off + 32 + i * 16)
                start = base[grp] if grp < len(base) else grp * 64
                cpus.extend(start + b for b in range(64) if mask >> b & 1)
            cores.append((eff_class, sorted(cpus)))
        off += size
    return cores


def detect() -> dict[str, Any]:
    """{logical_cpus, performance_mask_hex, efficiency_mask_hex, performance_cpus,
    efficiency_cpus, hybrid}. Hybrid = more than one distinct EfficiencyClass; the
    highest class is the performance cores. Anything but Windows, or any failure:
    hybrid False and every CPU listed as performance."""
    if sys.platform != "win32":
        return _all_cpus()
    try:
        cores = _windows_cores()
        classes = {c for c, _ in cores}
        if not cores or len(classes) < 2:
            total = sum(len(cpus) for _, cpus in cores) or (os.cpu_count() or 1)
            return _result(total, sorted(x for _, cpus in cores for x in cpus) or list(range(total)), [])
        top = max(classes)
        perf = sorted(x for c, cpus in cores if c == top for x in cpus)
        eff = sorted(x for c, cpus in cores if c != top for x in cpus)
        return _result(len(perf) + len(eff), perf, eff)
    except Exception as exc:
        _LOG.warning("cpu topology unavailable (%s) — treating all CPUs as one class", exc)
        return _all_cpus()


def all_cpus_mask() -> str:
    """Hex mask covering every logical CPU (what 'all cores' means for --cpu-mask-batch)."""
    info = detect()
    return _mask_hex(sorted(info["performance_cpus"] + info["efficiency_cpus"]))


def describe(info: dict[str, Any] | None = None) -> str:
    """Multi-line human summary for the tray."""
    info = info or detect()
    lines = [f"logical CPUs: {info['logical_cpus']}   hybrid: {'yes' if info['hybrid'] else 'no'}"]
    lines.append(f"performance: {len(info['performance_cpus'])} CPUs  mask {info['performance_mask_hex'] or '-'}")
    if info["efficiency_cpus"]:
        lines.append(f"efficiency:  {len(info['efficiency_cpus'])} CPUs  mask {info['efficiency_mask_hex']}")
    lines.append(f"all CPUs mask: {_mask_hex(sorted(info['performance_cpus'] + info['efficiency_cpus']))}")
    return "\n".join(lines)


if __name__ == "__main__":
    import json
    print(json.dumps(detect(), indent=2))
