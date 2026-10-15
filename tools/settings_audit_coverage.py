"""Coverage audit: every command-line flag that our llama.cpp patches add must have a telecode setting, and every setting must have a tray row.

The flags are read from the patch files (the `"--flag"` strings in the lines a patch adds to common/arg.cpp), so the answer does not depend on which
upstream version the stock binary happens to be; the patched binary's `--help` must list each of them.

usage: python tools/settings_audit_coverage.py [patched llama-server.exe]
exit code 1 when something is missing
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(ROOT, "build", "llama.cpp", "build-telecode", "bin", "llama-server.exe")
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from llamacpp import argv as A  # noqa: E402


def help_flags(exe: str) -> set[str]:
    out = subprocess.run([exe, "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120).stdout
    return set(re.findall(r"(?<![\w-])(--[a-z0-9][a-z0-9-]*)", out))


def patch_flags(patch_dir: str) -> set[str]:
    """The flags a patch adds: the quoted --names in the added lines of common/arg.cpp."""
    flags: set[str] = set()
    for name in sorted(os.listdir(patch_dir)):
        if not name.endswith(".patch"):
            continue
        in_arg = False
        for ln in open(os.path.join(patch_dir, name), encoding="utf-8", errors="replace").read().splitlines():
            if ln.startswith("diff --git"):
                in_arg = ln.endswith("common/arg.cpp")
            elif in_arg and ln.startswith("+") and not ln.startswith("+++"):
                flags.update(re.findall(r'"(--[a-z][a-z0-9-]*)"', ln))
    return flags


ours = patch_flags(os.path.join(ROOT, "patches", "llama.cpp"))
have = help_flags(EXE)
tables = list(A._GLOBAL_FLAG_SPECS) + list(A._MODEL_FLAG_SPECS)
flag_to_key = {}
for key, flag, _kind in tables:
    for f in (flag if isinstance(flag, tuple) else (flag,)):
        flag_to_key[f] = key
# a --no-X twin is covered by the row of --X (bool_pair / onoff)
missing = sorted(f for f in ours if f not in flag_to_key and not (f.startswith("--no-") and ("--" + f[5:]) in flag_to_key))
absent = sorted(f for f in ours if f not in have and not f.startswith("--no-"))
print(f"flags added by the patches: {len(ours)}; without a telecode setting: {len(missing)}; not in the binary's --help: {len(absent)}")
for f in missing:
    print("   MISSING SETTING:", f)
for f in absent:
    print("   NOT IN --help:", f)

tray = open(os.path.join(ROOT, "tray", "qt_sections.py"), encoding="utf-8").read()
no_row = [key for key, _flag, _kind in tables if not re.search(r"[.\"']" + re.escape(key) + r"[\"']", tray)]
print(f"settings keys: {len(tables)}; without a tray row: {len(no_row)}")
for k in no_row:
    print("   NO TRAY ROW:", k)
sys.exit(1 if (missing or absent or no_row) else 0)
