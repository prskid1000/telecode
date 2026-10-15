"""Memory plan of a llama-server command line WITHOUT loading the weights.

llama-server runs a fitting dry run at start (no weights read) and logs, for the model and for the draft / MTP context, how much GPU memory it will use
(model + context + compute). This starts the server, reads those lines at log level 4 and kills it before the real load. The plan counts everything
llama.cpp knows about: the KV cache, the recurrent state, the compute buffers, the speculative decoding context and the draft head subset.

Used by the supervisor's preflight check (`llamacpp.preflight_vram`): a start whose plan, plus what other programs already use, passes
`llamacpp.vram_ceiling_mib` is refused with the numbers, instead of loading until the GPU is full (which hard-crashes some machines).
Never raises: any failure answers None and the caller skips the check.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
import time
from typing import Optional

from llamacpp import vram

_LOG = logging.getLogger("telecode.llamacpp.preflight")

_MMPROJ = re.compile(r"estimated worst-case memory usage of mmproj is ([\d.]+) MiB")
_ROW = re.compile(r"CUDA0 \(.*?\)\s*\|\s*(\d+) =\s*(\d+) \+ \((\d+) =\s*(\d+) \+\s*(\d+) \+\s*(\d+)\)")
# more than this on the GPU while only the plan is being made means the weights are being loaded: stop at once
_LOAD_GUARD_MIB = 3000


def _expects_draft(argv: list[str]) -> bool:
    return any(a in ("--spec-type", "--spec-default", "--model-draft", "-md") for a in argv)


def planned_mib(argv: list[str], env: Optional[dict] = None, timeout: float = 30.0) -> Optional[dict]:
    """{"target": MiB, "draft": MiB, "mmproj": MiB, "total": MiB} of the first GPU, or None when the plan could not be read.

    target: the model, the KV cache of every slot (--parallel), the recurrent state and the compute buffers; draft: the draft / MTP context on top;
    mmproj: the vision projector, only when it is offloaded to the GPU. The CUDA runtime's own memory is NOT in the plan (about 0.4-0.5 GB, see
    llamacpp.preflight_overhead_mib) and neither are other programs."""
    need = 2 if _expects_draft(argv) else 1
    cmd = list(argv) + ["-lv", "4"]
    creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                                encoding="utf-8", errors="replace", env=env, creationflags=creation)
    except OSError as exc:
        _LOG.debug("preflight: cannot start %s: %s", cmd[0], exc)
        return None
    lines: list[str] = []

    def _read() -> None:
        assert proc.stdout is not None
        for ln in proc.stdout:
            lines.append(ln)
    threading.Thread(target=_read, daemon=True).start()
    t0 = time.time()
    try:
        while time.time() - t0 < timeout and proc.poll() is None:
            text = "".join(lines)
            if len(_ROW.findall(text)) >= need:
                break
            used = vram.used_vram_mib()
            if used is not None and used > _LOAD_GUARD_MIB:
                _LOG.warning("preflight: the server started loading the weights (%d MiB in use), stopped", used)
                break
            time.sleep(0.2)
    finally:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    rows = [tuple(int(x) for x in m) for m in _ROW.findall("".join(lines))]
    if len(rows) < need:
        return None
    target = rows[0][2]  # what the server plans to use on the GPU
    # the draft context shares the weights: only its own context and compute buffers come on top
    draft = sum(r[4] + r[5] for r in rows[1:need])
    mm = _MMPROJ.search("".join(lines))
    offloaded = ("--mmproj" in argv or "-mm" in argv or "--mmproj-url" in argv) and "--no-mmproj-offload" not in argv
    mmproj = int(float(mm.group(1))) if (mm and offloaded) else 0
    return {"target": target, "draft": draft, "mmproj": mmproj, "total": target + draft + mmproj}
