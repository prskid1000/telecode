"""Run a list of llama-server configurations one after the other through tools/moe_cache_bench.py and compare the outputs with an earlier run.

One server at a time, killed between the cases; a case does not start while the GPU is busy; the bench kills its server above a VRAM limit
(`vram_limit`); the series stops at the first failure. Outputs are greedy (temperature 0), so an identical `outputs_sha256` means an identical text:
use it as a regression check ("did this build / this flag change the output?").

usage: python tools/bench_matrix.py CASES.json [--out DIR] [--baseline RESULTS.jsonl] [--only LABEL ...] [--busy-mib 1500] [--keep-going]

CASES.json
  {
    "model": "qwen3.6-35b-a3b",                    model name of llamacpp.models in settings.json
    "exe": "build/test-bin-0015/llama-server.exe",  binary to test (default: llamacpp.binary)
    "defaults": {"ctx": 8192, "pad": 0, "prompts": 4, "n_predict": 128, "probs": 0, "vram_limit": 11600, "max_threads": 8, "ready_timeout": 400},
    "cases": [
      {"label": "static"},
      {"label": "cache", "sets": ["n_cpu_moe=99", "moe_cache_mib=4000"], "extra": "-ub 1024", "env": {"GGML_CUDA_REGISTER_HOST": "1"}},
      {"label": "other_model", "model": "qwen3.8-27b", "ctx": 80000}
    ]
  }
  a case may override every default; `sets` are per-model settings (`key=value`), `extra` is added to llama-server's command line.
  pad = filler sentences (about 12 tokens each) in front of every prompt, to measure prompt processing; probs = top-N token probabilities are
  recorded (tc_<label>_probs.json, see tools/compare_runs.py).

The result lines (results.jsonl) hold: label, pp_tok_s, tg_tok_s, outputs_sha256, peak_vram_mib. Speeds on a laptop vary a lot between runs
(power mode, temperature): compare them only inside one session and repeat; the hashes and the peak memory are exact.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "tools" / "moe_cache_bench.py"
DEFAULTS = {"ctx": 8192, "pad": 0, "prompts": 4, "n_predict": 128, "probs": 0, "vram_limit": 11600, "max_threads": 8, "ready_timeout": 400}


def _python() -> str:
    """telecode's virtualenv Python when there is one (the bench imports telecode modules that need aiohttp)."""
    venv = ROOT / "telecode-venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python")
    return str(venv) if venv.exists() else sys.executable


def gpu_used_mib() -> int:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return 0
    try:
        return int(float(subprocess.run([exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout.split()[0]))
    except Exception:
        return 0


def kill_servers() -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True)
    else:
        subprocess.run(["pkill", "-9", "-f", "llama-server"], capture_output=True)


def load_results(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("{"):
            d = json.loads(line)
            out[d["label"]] = d
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases")
    ap.add_argument("--out", default="")
    ap.add_argument("--baseline", default="")
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--busy-mib", type=int, default=1500, help="a case does not start when the GPU already uses more than this")
    ap.add_argument("--keep-going", action="store_true", help="do not stop at the first failure")
    a = ap.parse_args()

    spec = json.loads(Path(a.cases).read_text(encoding="utf-8"))
    defaults = {**DEFAULTS, **spec.get("defaults", {})}
    out_dir = Path(a.out) if a.out else ROOT / "data" / "logs" / "bench_matrix" / time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    baseline = load_results(Path(a.baseline)) if a.baseline else {}
    py = _python()
    failed = changed = 0
    rows = []
    for case in spec["cases"]:
        label = case["label"]
        if a.only and label not in a.only:
            continue
        used = gpu_used_mib()
        if used > a.busy_mib:
            print(f"stop: the GPU already uses {used} MiB before '{label}' (another program or a leftover server)")
            failed += 1
            break
        c = {**defaults, **{k: v for k, v in case.items() if k not in ("label", "sets", "extra", "env", "model", "exe")}}
        cmd = [py, str(BENCH), "--model", case.get("model", spec["model"]), "--label", label, "--out", str(out_dir / label),
               "--ctx", str(c["ctx"]), "--pad", str(c["pad"]), "--prompts", str(c["prompts"]), "--n-predict", str(c["n_predict"]),
               "--vram-limit", str(c["vram_limit"]), "--max-threads", str(c["max_threads"]), "--ready-timeout", str(c["ready_timeout"])]
        if c["probs"]:
            cmd += ["--probs", str(c["probs"])]
        exe = case.get("exe", spec.get("exe", ""))
        if exe:
            cmd += ["--exe", str(ROOT / exe) if not os.path.isabs(exe) else exe]
        for s in case.get("sets", []):
            cmd += ["--set", s]
        if case.get("extra"):
            cmd += ["--extra", case["extra"]]
        env = dict(os.environ, **{k: str(v) for k, v in case.get("env", {}).items()})
        print(f"== {label}", flush=True)
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(ROOT))
        kill_servers()
        time.sleep(6)
        last = (proc.stdout.strip().splitlines() or [""])[-1]
        try:
            res = json.loads(last)
        except ValueError:
            res = {}
        if proc.returncode != 0 or "outputs_sha256" not in res:
            print(f"   FAILED (exit {proc.returncode}): {last[:200] or proc.stderr.strip()[-300:]}")
            failed += 1
            if not a.keep_going:
                break
            continue
        with results_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(res) + "\n")
        verdict = ""
        if label in baseline:
            same = baseline[label].get("outputs_sha256", "")[:16] == res["outputs_sha256"][:16]
            verdict = "  output SAME as the baseline" if same else "  output DIFFERENT from the baseline"
            changed += not same
        print(f"   pp {res['pp_tok_s']} tok/s  tg {res['tg_tok_s']} tok/s  peak {res.get('peak_vram_mib')} MiB  hash {res['outputs_sha256'][:8]}{verdict}", flush=True)
        rows.append(res)
    print(f"\n{len(rows)} cases ok, {failed} failed, {changed} differ from the baseline; results in {results_path}")
    return 1 if (failed or changed) else 0


if __name__ == "__main__":
    sys.exit(main())
