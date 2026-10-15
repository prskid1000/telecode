"""Run llama-server under NVIDIA compute-sanitizer (memcheck) with one small request: finds illegal GPU memory accesses of new CUDA code.

Slow (minutes to load) and it loads the model for real: keep the context and the batch small, and run it while nothing else needs the GPU.
Errors are printed by the sanitizer as they happen; the server is killed after the request, the report is in --log.

usage: python tools/sanitize_server.py --exe llama-server.exe -m MODEL.gguf [--prompt TEXT] [--n-predict 6] [--log sanitizer.log] [-- extra llama-server args]
example: python tools/sanitize_server.py --exe build/test-bin-0015/llama-server.exe -m D:/.models/m.gguf -- -c 2048 -ngl 99 -ub 64 --no-warmup
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time
import urllib.request


def find_sanitizer() -> str:
    cands = glob.glob(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v*\compute-sanitizer\compute-sanitizer.exe") + glob.glob("/usr/local/cuda*/compute-sanitizer/compute-sanitizer")
    if not cands:
        raise SystemExit("compute-sanitizer not found (it comes with the CUDA toolkit)")
    return sorted(cands)[-1]


def main() -> int:
    argv = sys.argv[1:]
    extra = []
    if "--" in argv:
        i = argv.index("--")
        extra, argv = argv[i + 1:], argv[:i]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", required=True)
    ap.add_argument("-m", "--model", required=True)
    ap.add_argument("--prompt", default="The archive keeps a ledger of shipments, one line per crate. " * 2 + "Summarise the ledger.")
    ap.add_argument("--n-predict", type=int, default=6)
    ap.add_argument("--port", type=int, default=18098)
    ap.add_argument("--log", default="sanitizer.log")
    ap.add_argument("--timeout", type=int, default=1500, help="seconds to wait for the server to become ready")
    a = ap.parse_args(argv)

    cmd = [find_sanitizer(), "--tool", "memcheck", "--print-limit", "6", a.exe, "-m", a.model, "--port", str(a.port), *extra]
    log = open(a.log, "w")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{a.port}"
    t0 = time.time()
    while time.time() - t0 < a.timeout and proc.poll() is None:
        try:
            if json.load(urllib.request.urlopen(url + "/health", timeout=3)).get("status") == "ok":
                break
        except Exception:
            time.sleep(3)
    try:
        req = urllib.request.Request(url + "/completion", json.dumps({"prompt": a.prompt, "n_predict": a.n_predict, "temperature": 0}).encode(), {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=600).read()
    except Exception as exc:
        print("request failed:", exc)
    time.sleep(5)
    proc.kill()
    log.close()
    text = open(a.log, encoding="utf-8", errors="replace").read()
    errors = [ln for ln in text.splitlines() if "Invalid" in ln or "ERROR SUMMARY" in ln or "Program hit" in ln]
    print(f"sanitizer reported {len(errors)} error lines; log: {a.log}")
    for ln in errors[:10]:
        print("  ", ln[:200])
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
