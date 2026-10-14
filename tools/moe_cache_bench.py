"""Benchmark one llama-server configuration: telecode's own argv for a model (plus edits),
a fixed set of greedy prompts, tokens/s out. Used to compare MoE expert-cache settings
and patched builds; it does not touch settings.json or the running telecode.

    python tools/moe_cache_bench.py --model qwen-35b --label base
    python tools/moe_cache_bench.py --model qwen-35b --label cache3g \\
        --exe D:/llama/llama-server.exe --set n_cpu_moe=99 --set moe_cache_mib=3000 \\
        --set moe_cache_cpu_miss=true --extra "--no-warmup"

Prints ONE json line: {label, pp_tok_s, tg_tok_s, runs, outputs_sha256, peak_vram_mib}.
  runs             per-prompt generation tok/s
  outputs_sha256   hash of the generated texts — equal hashes = identical output (greedy, seed 1)
  peak_vram_mib    highest GPU memory in use (nvidia-smi, first GPU, 1 s polling; the whole GPU,
                   not just llama-server) or null when nvidia-smi cannot be read

Gentler than production by default (--no-safe turns all of it off): realtime / high priority and
mlock are dropped, threads are capped at --max-threads, context at 8192 (--ctx overrides). It refuses to
run while any llama-server.exe is already running (a benchmark next to a loaded model measures
nothing and can exhaust memory), and always kills its own server on the way out.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH_HOST = "127.0.0.1"

PROMPTS = [
    "Write a long detailed essay about the history of the printing press.",
    "Explain how a B-tree works and implement insertion in Python.",
    "Describe the water cycle in detail, then list ten unrelated trivia facts.",
    "Write a Rust function that parses a CSV line with quoted fields, with tests.",
]


def _parse_value(v: str):
    try:
        return json.loads(v)
    except ValueError:
        return v


def _server_running() -> bool:
    """True when any llama-server process exists (Windows: tasklist)."""
    exe = shutil.which("tasklist")
    if not exe:
        return False
    out = subprocess.run([exe, "/FI", "IMAGENAME eq llama-server.exe", "/NH"],
                         capture_output=True, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return "llama-server.exe" in (out.stdout or "").lower()


def _port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((BENCH_HOST, port)) == 0


def _make_settings(args: argparse.Namespace) -> str:
    """Temp copy of settings.json with the bench edits applied; returns its path."""
    src = os.environ.get("TELECODE_SETTINGS") or str(ROOT / "settings.json")
    s = json.load(open(src, encoding="utf-8"))
    g = s.setdefault("llamacpp", {})
    m = g.get("models", {}).get(args.model)
    if m is None:
        raise SystemExit(f"model '{args.model}' is not in llamacpp.models")
    for kv in args.set:
        k, _, v = kv.partition("=")
        if not k or not _:
            raise SystemExit(f"--set expects key=value, got '{kv}'")
        m[k] = _parse_value(v)

    g["host"], g["port"], g["api_key"] = BENCH_HOST, args.port, ""
    if args.exe:
        g["binary"] = args.exe

    if not args.no_safe:
        for key in ("prio", "prio_batch"):
            if int(g.get(key) or 0) > 0:
                g[key] = 0
        if "mlock" in str(g.get("load_mode") or ""):
            g["load_mode"] = "none"
        for key in ("threads", "threads_batch"):
            cur = int(g.get(key) or 0)
            if cur > args.max_threads or (key == "threads" and cur == 0):
                g[key] = args.max_threads
        if args.ctx is None:
            args.ctx = min(int(m.get("ctx_size") or 8192), 8192)
    if args.ctx is not None:
        m["ctx_size"] = args.ctx
        m["ctx_size_spec"] = 0

    f = os.path.join(tempfile.mkdtemp(prefix="moe_bench_"), "settings.json")
    json.dump(s, open(f, "w", encoding="utf-8"))
    return f


def _post(port: int, path: str, body: dict, timeout: int = 900) -> dict:
    req = urllib.request.Request(f"http://{BENCH_HOST}:{port}{path}", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


class _VramPeak(threading.Thread):
    """Polls GPU memory in use once a second and keeps the maximum."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.peak: int | None = None
        self._stop_evt = threading.Event()

    def run(self) -> None:
        from llamacpp import vram
        while not self._stop_evt.is_set():
            used = vram.used_vram_mib()
            if used is not None and (self.peak is None or used > self.peak):
                self.peak = used
            self._stop_evt.wait(1.0)

    def stop(self) -> None:
        self._stop_evt.set()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", required=True, help="model name in llamacpp.models")
    ap.add_argument("--exe", default="", help="llama-server binary (default: llamacpp.binary)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="model key edit before argv is built (repeatable), e.g. n_cpu_moe=99")
    ap.add_argument("--extra", default="", help="extra llama-server args, one quoted string")
    ap.add_argument("--prompts", type=int, default=len(PROMPTS), help="how many prompts to run (cycles the 4 built-in)")
    ap.add_argument("--n-predict", type=int, default=192)
    ap.add_argument("--label", default="run")
    ap.add_argument("--out", default=str(ROOT / "data" / "logs" / "moe_bench"), help="directory for the log and outputs")
    ap.add_argument("--max-threads", type=int, default=8, help="thread cap in safe mode")
    ap.add_argument("--ctx", type=int, default=None, help="context size (safe mode default: min(model ctx, 8192))")
    ap.add_argument("--no-safe", action="store_true", help="keep priority, mlock, threads and context as configured")
    ap.add_argument("--port", type=int, default=18096)
    ap.add_argument("--ready-timeout", type=int, default=400, help="seconds to wait for /health")
    args = ap.parse_args()

    if _server_running():
        print(json.dumps({"label": args.label, "error": "a llama-server.exe is already running; stop it first"}))
        return 2
    if _port_in_use(args.port):
        print(json.dumps({"label": args.label, "error": f"port {args.port} is already in use"}))
        return 2

    os.environ["TELECODE_SETTINGS"] = _make_settings(args)
    sys.path.insert(0, str(ROOT))
    from llamacpp import argv as A
    from llamacpp import config as lcfg
    from process import kill_process_tree

    cmd = A.build_argv(args.model)
    cmd[0] = shutil.which(cmd[0]) or cmd[0]
    if args.extra:
        cmd += [a.strip('"') for a in shlex.split(args.extra, posix=(os.name != "nt"))]

    os.makedirs(args.out, exist_ok=True)
    log = open(os.path.join(args.out, f"tc_{args.label}.log"), "w")
    peak = _VramPeak()
    proc = None
    try:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=lcfg.spawn_env(args.model),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        peak.start()
        t0 = time.time()
        while True:
            if proc.poll() is not None:
                print(json.dumps({"label": args.label, "error": f"server exited ({proc.returncode}); see {log.name}"}))
                return 1
            if time.time() - t0 > args.ready_timeout:
                print(json.dumps({"label": args.label, "error": "NOT READY"}))
                return 1
            try:
                url = f"http://{BENCH_HOST}:{args.port}/health"
                if json.load(urllib.request.urlopen(url, timeout=2)).get("status") == "ok":
                    break
            except Exception:
                pass
            time.sleep(2)

        _post(args.port, "/completion", {"prompt": PROMPTS[0], "n_predict": 16, "temperature": 0, "cache_prompt": False})
        outs, tg, pp = [], [], []
        for i in range(max(1, args.prompts)):
            r = _post(args.port, "/completion",
                      {"prompt": PROMPTS[i % len(PROMPTS)], "n_predict": args.n_predict, "temperature": 0,
                       "top_k": 1, "seed": 1, "cache_prompt": False})
            outs.append(r["content"])
            tg.append(r["timings"]["predicted_per_second"])
            pp.append(r["timings"]["prompt_per_second"])
        with open(os.path.join(args.out, f"tc_{args.label}.json"), "w", encoding="utf-8") as fh:
            json.dump(outs, fh)
        print(json.dumps({
            "label": args.label,
            "pp_tok_s": round(sum(pp) / len(pp), 1),
            "tg_tok_s": round(sum(tg) / len(tg), 2),
            "runs": [round(x, 1) for x in tg],
            "outputs_sha256": hashlib.sha256(json.dumps(outs).encode("utf-8")).hexdigest(),
            "peak_vram_mib": peak.peak,
        }))
        return 0
    finally:
        peak.stop()
        if proc is not None and proc.poll() is None:
            kill_process_tree(proc.pid, force=False)
            try:
                proc.wait(20)
            except subprocess.TimeoutExpired:
                kill_process_tree(proc.pid, force=True)
                proc.kill()
        log.close()


if __name__ == "__main__":
    sys.exit(main())
