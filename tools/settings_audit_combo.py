"""Static audit (plan G.1): set each settings key alone, build argv, check the flag it emits against the binaries' --help."""
import json
import os
import subprocess
import sys
import tempfile

ROOT = r"D:\Projects\Telecode"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
STOCK = r"D:\Projects\Telecode\llama\llama-server.exe"
PATCHED = r"D:\Projects\Telecode\build\test-bin-0016\llama-server.exe"

tmp = tempfile.mkdtemp(prefix="audit_")
SETTINGS = os.path.join(tmp, "settings.json")
os.environ["TELECODE_SETTINGS"] = SETTINGS


def write_settings(g=None, m=None):
    model = {"path": "D:/x/model.gguf"}
    model.update(m or {})
    glob = {"binary": PATCHED, "models": {"m": model}, "default_model": "m"}
    glob.update(g or {})
    json.dump({"llamacpp": glob}, open(SETTINGS, "w"))


write_settings()
import config as app_config  # noqa: E402
from llamacpp import argv as A  # noqa: E402
from llamacpp import flag_audit as FA  # noqa: E402


def help_spec(exe):
    out = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=120).stdout
    return FA.parse_help(out)


stock = help_spec(STOCK)
patched = help_spec(PATCHED)
print("flags in stock help:", len(stock), " patched:", len(patched))


def known(spec, flag):
    if flag in spec:
        return spec[flag]
    for s in spec.values():
        if flag in (s.get("aliases") or []):
            return s
    return None


def sample(kind, flag_spec, key):
    if kind in ("flag",):
        return True
    if kind in ("onoff", "bool_pair"):
        return True
    if kind == "path":
        return "D:/x/file.bin"
    if kind == "pathlist":
        return "D:/x/a.gguf,D:/x/b.gguf"
    if kind == "pathscaled":
        return "D:/x/a.gguf:0.5"
    if kind == "json_obj":
        return '{"a":1}'
    if kind == "range2":
        return "0 3"
    if kind == "multi":
        return "a,b"
    allowed = (flag_spec or {}).get("allowed") or []
    if allowed:
        return allowed[0]
    return 3


def flags_of(argv):
    return [a for a in argv if isinstance(a, str) and a.startswith("-") and len(a) > 1 and not a.lstrip("-").replace(".", "").isdigit()]



import random
random.seed(11)
tables = [("global", k, f, kd) for k, f, kd in A._GLOBAL_FLAG_SPECS] + [("model", k, f, kd) for k, f, kd in A._MODEL_FLAG_SPECS]
EXCL = {"moe_cache_cpu_miss", "moe_cache_mib", "fit", "n_gpu_layers", "n_gpu_layers_draft", "spec_default", "spec_type"}
bad = 0
n_checked = 0
for trial in range(400):
    pick = random.sample(tables, random.randint(8, 25))
    g, m = {}, {}
    # parents on at random so the dependent rows get emitted
    if random.random() < 0.7:
        m.update({"moe_cache_mib": 2000, "moe_cache_cpu_miss": True})
    if random.random() < 0.5:
        g["spec_type"] = "draft-mtp"
    wanted = []
    for scope, key, flag, kind in pick:
        if key in EXCL:
            continue
        flag0 = flag[0] if isinstance(flag, tuple) else flag
        spec = known(patched, flag0) or known(stock, flag0)
        val = sample(kind, spec, key)
        (g if scope == "global" else m)[key] = val
        wanted.append((key, flag0, kind))
    write_settings(g, m)
    app_config.reload()
    argv = A.build_argv("m")
    fl = flags_of(argv)
    dup = {f for f in fl if fl.count(f) > 1 and not (known(patched, f) or {}).get("repeat")}
    # multi kind may repeat; ignore those
    multis = {(x[1]) for x in wanted if x[2] == "multi"}
    dup -= multis
    unk = [f for f in set(fl) if known(patched, f) is None]
    n_checked += 1
    if dup or unk:
        bad += 1
        print("trial", trial, "dup", sorted(dup), "unknown", unk)
print("combos checked:", n_checked, " with problems:", bad)
