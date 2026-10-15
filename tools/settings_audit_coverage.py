"""Coverage audit: every flag the patched llama-server has and the stock one lacks must have a telecode setting, every setting must have a tray row.

usage: python tools/settings_audit_coverage.py [patched llama-server.exe] [stock llama-server.exe]
exit code 1 when something is missing
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARGS = [os.path.abspath(a) for a in sys.argv[1:3]]
sys.path.insert(0, ROOT)
os.chdir(ROOT)
PATCHED = ARGS[0] if len(ARGS) > 0 else os.path.join(ROOT, "build", "llama.cpp", "build-telecode", "bin", "llama-server.exe")
STOCK = ARGS[1] if len(ARGS) > 1 else os.path.join(ROOT, "llama", "llama-server.exe")

from llamacpp import argv as A  # noqa: E402


def help_flags(exe):
    out = subprocess.run([exe, "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120).stdout
    return set(re.findall(r"(?<![\w-])(--[a-z0-9][a-z0-9-]*)", out))


patched, stock = help_flags(PATCHED), help_flags(STOCK)
only = sorted(patched - stock)
tables = list(A._GLOBAL_FLAG_SPECS) + list(A._MODEL_FLAG_SPECS)
flag_to_key = {}
for key, flag, _kind in tables:
    for f in (flag if isinstance(flag, tuple) else (flag,)):
        flag_to_key[f] = key
# a --no-X twin is covered by the row of --X (bool_pair / onoff)
missing = [f for f in only if f not in flag_to_key and not (f.startswith("--no-") and ("--" + f[5:]) in flag_to_key)]
print(f"flags only in the patched build: {len(only)}; without a telecode setting: {len(missing)}")
for f in missing:
    print("   MISSING SETTING:", f)

tray = open(os.path.join(ROOT, "tray", "qt_sections.py"), encoding="utf-8").read()
no_row = []
for key, flag, _kind in tables:
    if not re.search(r"[.\"']" + re.escape(key) + r"[\"']", tray):
        no_row.append(key)
print(f"settings keys: {len(tables)}; without a tray row: {len(no_row)}")
for k in no_row:
    print("   NO TRAY ROW:", k)
sys.exit(1 if (missing or no_row) else 0)
