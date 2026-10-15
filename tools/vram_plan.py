"""What GPU memory will llama-server plan to use for a configuration, WITHOUT loading the weights.

llama-server runs a fitting dry run at start and logs the memory it will use (model + context + compute, and the same for a draft / MTP context).
This builds telecode's command line for a model, starts the server for a few seconds, reads those lines at log level 4 and kills it before the
real load (also killed when more than 3 GB appear on the GPU, which means the weights are being loaded). The numbers are exact for that command
line: the KV cache of every --parallel slot, the recurrent state, the compute buffers, the speculative decoding context. The CUDA runtime adds
about 450 MiB that the plan does not count, and other programs add theirs.

usage: python tools/vram_plan.py MODEL [--ctx N] [--exe llama-server.exe] [--set key=v1,v2,...]... [-- extra llama-server args]
  MODEL  a model name of llamacpp.models in settings.json
  --set  a per-model setting; several values make a matrix (all combinations are planned), `ub=` is the micro-batch (-ub)
  --ctx  context size (the bench caps it at 8192 otherwise)
example: python tools/vram_plan.py qwen3.8-27b --ctx 80000 --set ub=512,2048 -- --spec-type draft-mtp --spec-draft-n-max 3
"""
import argparse
import itertools
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from llamacpp import preflight  # noqa: E402

BENCH = os.path.join(ROOT, "tools", "moe_cache_bench.py")


def _python() -> str:
    """telecode's virtualenv Python when there is one (the bench imports telecode modules that need aiohttp)."""
    venv = os.path.join(ROOT, "telecode-venv", "Scripts" if sys.platform == "win32" else "bin", "python.exe" if sys.platform == "win32" else "python")
    return venv if os.path.exists(venv) else sys.executable


def command(model: str, exe: str, ctx: str, sets: list[str], extra: str) -> list[str]:
    env = dict(os.environ, BENCH_PRINT_CMD="1")  # the bench prints the command line instead of starting the server
    args = [_python(), BENCH, "--model", model, "--out", os.path.join(tempfile.gettempdir(), "vram_plan")]
    if exe:
        args += ["--exe", exe]
    if ctx:
        args += ["--ctx", ctx]
    for s in sets:
        args += ["--set", s]
    args += ["--extra", extra]
    out = subprocess.run(args, capture_output=True, text=True, env=env).stdout.strip().splitlines()
    return json.loads(out[-1])


def main() -> int:
    argv = sys.argv[1:]
    extra = ""
    if "--" in argv:
        i = argv.index("--")
        extra = " ".join(argv[i + 1:])
        argv = argv[:i]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model")
    ap.add_argument("--ctx", default="")
    ap.add_argument("--exe", default="")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=V1,V2")
    ap.add_argument("--overhead-mib", type=int, default=450, help="the CUDA runtime, added to the plan (default 450)")
    a = ap.parse_args(argv)
    axes = []
    for s in a.set:
        k, _, v = s.partition("=")
        axes.append([f"{k}={x}" for x in v.split(",")])
    for combo in itertools.product(*axes) if axes else [()]:
        sets = [c for c in combo if not c.startswith("ub=")]
        ub = next((c.split("=")[1] for c in combo if c.startswith("ub=")), "")
        plan = preflight.planned_mib(command(a.model, a.exe, a.ctx, sets, (f"-ub {ub} " if ub else "") + extra))
        label = " ".join(combo) or "(as configured)"
        if plan:
            print(f"{label:40s} plan {plan['total']:6d} MiB (main {plan['target']}, draft {plan['draft']}, projector {plan['mmproj']}) + runtime {a.overhead_mib} = {plan['total'] + a.overhead_mib} MiB")
        else:
            print(f"{label:40s} no plan could be read")
    return 0


if __name__ == "__main__":
    sys.exit(main())
