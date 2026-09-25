"""Fake engine CLI for the Engine Runner tests.

Replays a recorded stream-json log to stdout (as the real claude/codex/agy
would), after reading the whole prompt from stdin. Options:

  --replay FILE          lines to print (recorded CLI output)
  --stdin-out FILE       write what arrived on stdin here
  --delay SEC            pause between lines
  --grandchild PIDFILE   spawn a sleeping grandchild first, write its pid
  --orphan PIDFILE       spawn a child that spawns a sleeping grandchild and exits
                         (the grandchild is orphaned — no live parent link)
  --hang                 after replaying, print one line and sleep 120 s
  --break-marker FILE    on CTRL_BREAK / SIGTERM write "break" here and exit 0
  --stderr-kb N          write N KB to stderr before any stdout (pipe-drain test)
  --exit N               exit code
"""

import argparse
import os
import signal
import subprocess
import sys
import time

SLEEPER = [sys.executable, "-c", "import time; time.sleep(120)"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay")
    ap.add_argument("--stdin-out")
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--grandchild")
    ap.add_argument("--orphan")
    ap.add_argument("--hang", action="store_true")
    ap.add_argument("--break-marker")
    ap.add_argument("--stderr-kb", type=int, default=0)
    ap.add_argument("--exit", type=int, default=0)
    a, _rest = ap.parse_known_args()

    if a.break_marker:
        def on_break(*_):
            with open(a.break_marker, "w") as fh:
                fh.write("break")
            os._exit(0)
        signal.signal(getattr(signal, "SIGBREAK", signal.SIGTERM), on_break)
        signal.signal(signal.SIGTERM, on_break)

    # Real CLIs read UTF-8 stdin and write UTF-8 stdout regardless of the ANSI code page.
    data = sys.stdin.buffer.read().decode("utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    if a.stdin_out:
        with open(a.stdin_out, "w", encoding="utf-8") as fh:
            fh.write(data)
    if a.stderr_kb:
        sys.stderr.write(("x" * 1023 + "\n") * a.stderr_kb)
        sys.stderr.flush()
    if a.grandchild:
        p = subprocess.Popen(SLEEPER)
        with open(a.grandchild, "w") as fh:
            fh.write(str(p.pid))
    if a.orphan:
        mid = ("import subprocess,sys;"
               f"p=subprocess.Popen({SLEEPER!r});"
               f"open(r'{a.orphan}','w').write(str(p.pid))")
        subprocess.Popen([sys.executable, "-c", mid]).wait()
    if a.replay:
        with open(a.replay, encoding="utf-8") as fh:
            for line in fh:
                sys.stdout.write(line if line.endswith("\n") else line + "\n")
                sys.stdout.flush()
                if a.delay:
                    time.sleep(a.delay)
    if a.hang:
        print('{"type":"hang"}', flush=True)
        for _ in range(1200):   # short sleeps so a SIGBREAK handler runs promptly
            time.sleep(0.1)
    return a.exit


if __name__ == "__main__":
    sys.exit(main())
