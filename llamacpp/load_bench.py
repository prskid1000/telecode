"""Model-load tuning: find the fastest way to load a model from its drive, store it per model.

The patched llama-server (patch 0007, llama_fast_read) reads the weights with parallel unbuffered workers,
each read followed by a copy into the model's buffer (RAM, or an upload to the GPU). Two numbers decide the
speed, and the best values depend on the drive the model sits on and on where its weights go:

  llamacpp.models.<m>.load_readers   workers           -> LLAMA_FAST_READ_THREADS   (0 = llama-server tunes it while loading)
  llamacpp.models.<m>.load_chunk_mb  size of one read  -> LLAMA_FAST_READ_CHUNK_MB  (0 = its default, 32)
  llamacpp.models.<m>.fast_load      false             -> LLAMA_FAST_READ=0          (the ordinary single-thread reader)

`benchmark()` loads the model for real once per candidate (its own command line, on a spare port, stopped as
soon as it is ready) and keeps what llama-server itself reports for the weight reads. A stand-in that only
reads the file does not predict the loader (measured: it preferred 8 MiB reads, the loader is fastest with
32 MiB), so nothing short of the real load is used. It needs the GPU like any load: run it with no other
llama-server running (the tray takes the supervisor's lock and unloads the model first). The same guards as a
normal start apply: the memory plan is checked against llamacpp.vram_ceiling_mib, the server is killed above
it, and each run's VRAM must be released before the next.

`record_load()` keeps what every real load achieved (parsed from llama.log), so the tray shows both.
Windows only, like the fast reader itself.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

_LOG = logging.getLogger("telecode.llamacpp.load_bench")

READERS = (2, 4, 8)
CHUNKS_MB = (16, 32, 64)
REPEAT_TOP = 2             # the best few candidates are loaded once more (a laptop drive varies run to run)
NEAR_BEST = 0.97           # within 3 % of the best counts as equal: prefer fewer workers, then smaller chunks
_STORE_LOCK = threading.Lock()
_FAST_VARS = ("LLAMA_FAST_READ", "LLAMA_FAST_READ_THREADS", "LLAMA_FAST_READ_CHUNK_MB")


# ── stored results ─────────────────────────────────────────────────────

def _store_path() -> Path:
    settings_path = Path(os.environ.get("TELECODE_SETTINGS", "settings.json")).resolve()
    return settings_path.parent / "data" / "load-bench.json"


def load_store() -> dict:
    try:
        d = json.loads(_store_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _update(model: str, key: str, value: Any) -> None:
    with _STORE_LOCK:
        d = load_store()
        d.setdefault(model, {})[key] = value
        p = _store_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
        tmp.replace(p)


def env_for(mcfg: dict) -> dict[str, str]:
    """Environment for llama-server from a model's load settings (only what differs from llama-server's own default)."""
    env: dict[str, str] = {}
    if mcfg.get("fast_load") is False:
        env["LLAMA_FAST_READ"] = "0"
        return env
    for key, var in (("load_readers", "LLAMA_FAST_READ_THREADS"), ("load_chunk_mb", "LLAMA_FAST_READ_CHUNK_MB")):
        try:
            v = int(float(mcfg.get(key) or 0))
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            env[var] = str(v)
    return env


# ── what a load reports ────────────────────────────────────────────────

_READ_LINE = re.compile(r": read ([\d.]+) (GiB|MiB) .*?in ([\d.]+) s \(\d+ MiB/s, (\d+) readers(?: x (\d+) MiB)?")


def parse_load_log(text: str) -> dict:
    """The fast reader's lines of one llama-server start: total GiB, seconds and MiB/s of the weight reads."""
    gib = secs = 0.0
    readers = chunk = 0
    for m in _READ_LINE.finditer(text):
        gib += float(m.group(1)) / (1024.0 if m.group(2) == "MiB" else 1.0)
        secs += float(m.group(3))
        readers = max(readers, int(m.group(4)))
        chunk = max(chunk, int(m.group(5) or 0))
    if gib <= 0:
        return {}
    return {"read_gib": round(gib, 2), "read_s": round(secs, 2), "mib_s": round(gib * 1024 / secs) if secs else None,
            "readers": readers, "chunk_mb": chunk or None}


def record_load(model: str, ready_s: float, log_text: str) -> dict:
    rec = {"at": time.strftime("%Y-%m-%d %H:%M"), "ready_s": round(ready_s, 1), **parse_load_log(log_text)}
    try:
        _update(model, "last_load", rec)
    except OSError as exc:
        _LOG.debug("cannot store the load record: %s", exc)
    return rec


# ── the benchmark ──────────────────────────────────────────────────────

def server_running() -> bool:
    """True when any llama-server process exists."""
    if sys.platform == "win32":
        exe = shutil.which("tasklist")
        if not exe:
            return False
        out = subprocess.run([exe, "/FI", "IMAGENAME eq llama-server.exe", "/NH"], capture_output=True, text=True,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        return "llama-server.exe" in (out.stdout or "").lower()
    out = subprocess.run(["pgrep", "-x", "llama-server"], capture_output=True)
    return out.returncode == 0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _set_arg(argv: list[str], flag: str, value: str) -> list[str]:
    out = list(argv)
    if flag in out:
        out[out.index(flag) + 1] = value
    else:
        out += [flag, value]
    return out


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
            return json.load(r).get("status") == "ok"
    except Exception:  # noqa: BLE001 — not up yet
        return False


def _one_load(argv: list[str], env: dict, port: int, ceiling: int, timeout: float, log_path: str) -> dict:
    """Start llama-server, wait for /health, stop it, wait for the GPU to be released. The fast reader's numbers."""
    from llamacpp import vram
    import process as tc_process
    base = vram.used_vram_mib() or 0
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    tc_process.bind_to_lifetime_job(proc.pid, proc=proc)  # telecode exiting takes it down, as with a normal start
    t0 = time.perf_counter()
    peak = 0
    err = ""
    ready_s = None
    try:
        while True:
            if proc.poll() is not None:
                err = f"llama-server exited during the load (code {proc.returncode})"
                break
            used = vram.used_vram_mib()
            if used is not None:
                peak = max(peak, used)
                if ceiling > 0 and used > ceiling:
                    err = f"GPU memory {used} MiB passed the ceiling {ceiling} MiB: stopped"
                    break
            if _healthy(port):
                ready_s = time.perf_counter() - t0
                break
            if time.perf_counter() - t0 > timeout:
                err = f"not ready within {timeout:.0f} s"
                break
            time.sleep(0.1)
    finally:
        if proc.poll() is None:
            tc_process.kill_process_tree(proc.pid, force=True)
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                pass
    deadline = time.time() + 30
    while time.time() < deadline:
        pids = vram.compute_app_pids()
        used = vram.used_vram_mib()
        if (pids is None or proc.pid not in pids) and (used is None or used <= base + 256):
            break
        time.sleep(0.1)
    else:
        err = err or "the GPU memory of the stopped server was not released within 30 s"
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    return {"ready_s": round(ready_s, 2) if ready_s else None, "peak_vram": peak, "error": err, **parse_load_log(text)}


def choose(results: list[dict]) -> dict:
    """The candidate to keep: only those measured more than once compete (one lucky run does not win), the
    best mean wins, and anything within 3 % of it counts as equal, where fewer readers, then a smaller read win."""
    rated = [x for x in results if len(x["mib_s"]) > 1] or results
    best_mean = max(x["mean"] for x in rated)
    near = [x for x in rated if x["mean"] >= best_mean * NEAR_BEST]
    return min(near, key=lambda x: (x["readers"], x["chunk_mb"]))


def benchmark(model: str, progress: Optional[Callable[[str], None]] = None,
              should_stop: Optional[Callable[[], bool]] = None) -> dict:
    """Load `model` once per READERS x CHUNKS_MB candidate (the best REPEAT_TOP once more), store the result
    (data/load-bench.json) and return it; result["best"] is what the caller writes into the settings.
    Blocking; must run with no llama-server running."""
    say = progress or (lambda _s: None)
    stop = should_stop or (lambda: False)
    if sys.platform != "win32":
        return {"ok": False, "error": "the fast loader is Windows-only"}
    if server_running():
        return {"ok": False, "error": "a llama-server is running: unload the model first"}
    from llamacpp import argv as largv, auto_cache, config as cfg, preflight, vram

    mcfg = cfg.model_cfg(model)
    path = mcfg.get("path")
    if not path:
        return {"ok": False, "error": f"no GGUF path configured for {model}"}
    overrides = auto_cache.plan(model) if auto_cache.enabled(model) else None
    port = _free_port()
    base_argv = largv.build_argv(model, overrides) if overrides else largv.build_argv(model)
    argv = _set_arg(base_argv, "--port", str(port))
    env = dict(cfg.spawn_env(model) or os.environ)
    for k in _FAST_VARS:
        env.pop(k, None)

    ceiling = cfg.vram_ceiling_mib()
    if ceiling > 0 and cfg.preflight_vram():
        plan = preflight.planned_mib(list(argv), env)
        used = vram.used_vram_mib() or 0
        if plan and plan["total"] + cfg.preflight_overhead_mib() + used > ceiling:
            return {"ok": False, "error": f"the plan ({plan['total']} MiB + {cfg.preflight_overhead_mib()} runtime + {used} in use) "
                                          f"passes the ceiling {ceiling} MiB"}
    timeout = max(60.0, cfg.ready_timeout_sec())
    tmpdir = tempfile.mkdtemp(prefix="telecode-loadbench-")
    results: list[dict] = []
    t_start = time.time()

    def run(r: int, c: int, label: str) -> Optional[dict]:
        if stop():
            return None
        e = dict(env, LLAMA_FAST_READ_THREADS=str(r), LLAMA_FAST_READ_CHUNK_MB=str(c))
        res = _one_load(argv, e, port, ceiling, timeout, os.path.join(tmpdir, f"r{r}-c{c}.log"))
        if res["error"]:
            raise RuntimeError(f"{r} reader(s) x {c} MiB: {res['error']}")
        if not res.get("mib_s"):
            raise RuntimeError("llama-server printed no fast-reader lines: is the patched build (0007) installed?")
        say(f"{label} {r} reader(s) x {c} MiB: weights {res['read_gib']} GiB in {res['read_s']} s = {res['mib_s']} MiB/s, "
            f"ready in {res['ready_s']} s")
        return res

    grid = [(r, c) for c in CHUNKS_MB for r in READERS]
    try:
        for i, (r, c) in enumerate(grid, 1):
            res = run(r, c, f"[{i}/{len(grid)}]")
            if res is None:
                return {"ok": False, "error": "stopped"}
            results.append({"readers": r, "chunk_mb": c, "mib_s": [res["mib_s"]], "ready_s": [res["ready_s"]],
                            "read_gib": res["read_gib"], "peak_vram": res["peak_vram"]})
        for x in sorted(results, key=lambda x: -x["mib_s"][0])[:REPEAT_TOP]:
            res = run(x["readers"], x["chunk_mb"], "[again]")
            if res is None:
                return {"ok": False, "error": "stopped"}
            x["mib_s"].append(res["mib_s"])
            x["ready_s"].append(res["ready_s"])
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    for x in results:
        x["mean"] = round(sum(x["mib_s"]) / len(x["mib_s"]))
        x["ready_mean"] = round(sum(x["ready_s"]) / len(x["ready_s"]), 1)
    best = choose(results)
    record = {
        "path": str(path),
        "drive": os.path.splitdrive(os.path.abspath(str(cfg.resolve_path(path))))[0].upper(),
        "weights_gib": results[0]["read_gib"],
        "measured_at": time.strftime("%Y-%m-%d %H:%M"),
        "seconds": round(time.time() - t_start),
        "best": {"readers": best["readers"], "chunk_mb": best["chunk_mb"], "mib_s": best["mean"],
                 "ready_s": best["ready_mean"]},
        "results": results,
    }
    _update(model, "bench", record)
    say(f"best: {best['readers']} reader(s) x {best['chunk_mb']} MiB = {best['mean']} MiB/s, ready in ~{best['ready_mean']} s")
    return {"ok": True, **record}


def apply_best(model: str, best: dict) -> None:
    """Write a benchmark's best into the model's settings (headless; the tray uses patch_settings)."""
    import config as app_config
    ek = model.replace(".", r"\.")
    app_config.set_nested(f"llamacpp.models.{ek}.load_readers", int(best["readers"]))
    app_config.set_nested(f"llamacpp.models.{ek}.load_chunk_mb", int(best["chunk_mb"]))


def format_summary(model: str) -> str:
    d = load_store().get(model) or {}
    lines: list[str] = []
    b = d.get("bench")
    if b:
        best = b["best"]
        lines.append(f"Benchmark {b['measured_at']} ({b['drive']}, {b['weights_gib']} GiB of weights, {b['seconds']} s): "
                     f"best {best['readers']} readers x {best['chunk_mb']} MiB = {best['mib_s']} MiB/s, ready in ~{best['ready_s']} s")
        by = {(x["readers"], x["chunk_mb"]): x for x in b.get("results", [])}
        lines.append("  MiB/s     " + "".join(f"{r:>7} rd" for r in READERS))
        for c in CHUNKS_MB:
            lines.append(f"  {c:>3} MiB   " + "".join(f"{(by[(r, c)]['mean'] if (r, c) in by else 0):>10}" for r in READERS))
    else:
        lines.append("No benchmark yet: llama-server picks the readers while it loads (4 to 8, 32 MiB reads).")
    ll = d.get("last_load")
    if ll:
        tail = (f", weights {ll['read_gib']} GiB in {ll['read_s']} s = {ll['mib_s']} MiB/s "
                f"({ll['readers']} readers{(' x ' + str(ll['chunk_mb']) + ' MiB') if ll.get('chunk_mb') else ''})"
                if ll.get("read_gib") else " (no fast-reader lines in the log: ordinary reader, or not the patched build)")
        lines.append(f"Last load {ll['at']}: ready in {ll['ready_s']} s{tail}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    """python -m llamacpp.load_bench MODEL [--apply]: benchmark a configured model (no llama-server may be running)."""
    import argparse
    ap = argparse.ArgumentParser(prog="python -m llamacpp.load_bench")
    ap.add_argument("model")
    ap.add_argument("--apply", action="store_true", help="write the best readers / chunk size into settings.json")
    a = ap.parse_args(argv)
    r = benchmark(a.model, progress=print)
    if not r.get("ok"):
        print(r.get("error"))
        return 1
    if a.apply:
        apply_best(a.model, r["best"])
        print("written to settings.json")
    print(format_summary(a.model))
    return 0


__all__ = ["benchmark", "apply_best", "env_for", "record_load", "parse_load_log", "format_summary", "load_store",
           "server_running"]

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
