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
PATCHED = r"D:\Projects\Telecode\build\test-bin-0008\llama-server.exe"

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


base_flags = None
rows = []
problems = []
for scope, table in (("global", A._GLOBAL_FLAG_SPECS), ("model", A._MODEL_FLAG_SPECS)):
    for key, flag, kind in table:
        flag0 = flag[0] if isinstance(flag, tuple) else flag
        spec = known(patched, flag0) or known(stock, flag0)
        val = sample(kind, spec, key)
        g, m = ({key: val}, {}) if scope == "global" else ({}, {key: val})
        # a few keys are only emitted together with their parent
        if key.startswith("moe_cache_") and key not in ("moe_cache_mib", "moe_cache_cpu_miss"):
            m.update({"moe_cache_mib": 1000, "moe_cache_cpu_miss": True, "moe_cache_route_bias": 0.05, "moe_cache_prewarm": True} if key in ("moe_cache_route_top", "moe_cache_route_scale") else {"moe_cache_mib": 1000, "moe_cache_cpu_miss": True})
        if key.startswith("spec_draft_adaptive") or key == "spec_draft_head_subset":
            g.update({"spec_type": "draft-mtp", "spec_draft_adaptive": True})
        if key.startswith("moe_cache_prefill") or key == "moe_cache_staging_slots":
            m["moe_cache_prefill_share"] = 0.5
        write_settings(g, m)
        app_config.reload()
        try:
            argv = A.build_argv("m")
        except Exception as e:
            problems.append((key, "build_argv raised " + repr(e)))
            continue
        want = flag0 if kind != "bool_pair" else flag[0]
        n = argv.count(want)
        in_help = known(patched, want) is not None
        in_stock = known(stock, want) is not None
        status = "ok"
        if n != 1 and kind != "multi":
            status = f"emitted {n}x"
        elif not in_help:
            status = "NOT IN PATCHED --help"
        rows.append((scope, key, want, kind, n, "stock" if in_stock else ("patched-only" if in_help else "unknown"), status))
        if status != "ok":
            problems.append((key, status))

print(f"{len(rows)} keys audited")
patched_only = [r for r in rows if r[5] == "patched-only"]
unknown = [r for r in rows if r[5] == "unknown"]
print("patched-only flags:", len(patched_only), " unknown to both:", len(unknown))
for r in unknown:
    print("  UNKNOWN:", r)
print("problems:", len(problems))
for p in problems:
    print("  ", p)
json.dump({"rows": rows, "problems": problems}, open(os.path.join(os.path.dirname(__file__), "audit_static.json"), "w"), indent=1)
