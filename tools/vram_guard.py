"""Watch the memory in use on the first NVIDIA GPU and kill a process above a limit.

A GPU whose dedicated memory runs full can hard-crash a machine (Windows WDDM), killing a model server costs only a reload.
Run it next to any test that loads a model; it counts the WHOLE GPU (other programs included), like nvidia-smi.

usage: python tools/vram_guard.py --limit-mib 11750 [--kill llama-server.exe] [--log guard.log] [--interval 1.0] [--stop-file guard.stop]
  --limit-mib   kill above this many MiB in use (nvidia-smi MiB; Task Manager shows GiB: 11.5 GiB = 11776 MiB)
  --kill        image name to kill (default llama-server.exe)
  --stop-file   the guard ends when this file exists
"""
import argparse
import os
import shutil
import subprocess
import sys
import time


def used_mib() -> int | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
        return int(float(out.stdout.strip().splitlines()[0]))
    except Exception:
        return None


def kill(image: str) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/IM", image], capture_output=True)
    else:
        subprocess.run(["pkill", "-9", "-f", image], capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit-mib", type=int, required=True)
    ap.add_argument("--kill", default="llama-server.exe")
    ap.add_argument("--log", default="")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--stop-file", default="")
    a = ap.parse_args()
    if used_mib() is None:
        print("nvidia-smi is not available", file=sys.stderr)
        return 2
    log = open(a.log, "a", encoding="utf-8") if a.log else None
    peak = 0
    while not (a.stop_file and os.path.exists(a.stop_file)):
        used = used_mib()
        if used is not None:
            peak = max(peak, used)
            line = f"{time.strftime('%H:%M:%S')} used {used} MiB peak {peak} MiB"
            if used > a.limit_mib:
                line += f"  LIMIT {a.limit_mib} passed: killing {a.kill}"
                kill(a.kill)
            if log:
                log.write(line + "\n")
                log.flush()
            elif used > a.limit_mib:
                print(line, flush=True)
        time.sleep(a.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
