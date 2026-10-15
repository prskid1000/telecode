"""Download one big file over many parallel ranged connections, resumable (for slow single-connection links, e.g. model files from Hugging Face).

A single connection to a CDN is often limited to a fraction of the line; eight to sixteen ranged connections add up. The file is cut into chunks, a
`<out>.state` file records the finished chunks, so a stopped download continues where it was. Each chunk asks the URL again (signed redirects expire).

usage: python tools/parallel_download.py URL OUT_FILE [--workers 16] [--chunk-mib 32]
example: python tools/parallel_download.py https://huggingface.co/<repo>/resolve/main/<file>.gguf D:/.models/<file>.gguf
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def total_size(url: str) -> int:
    req = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers["Content-Range"].split("/")[1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--chunk-mib", type=int, default=32)
    a = ap.parse_args()

    chunk = a.chunk_mib * 1024 * 1024
    size = total_size(a.url)
    n = (size + chunk - 1) // chunk
    state_path = a.out + ".state"
    done = set(json.load(open(state_path))) if os.path.exists(state_path) else set()
    if not os.path.exists(a.out):
        with open(a.out, "wb") as f:
            f.truncate(size)
    lock = threading.Lock()
    got = [len(done) * chunk]

    def save() -> None:
        with open(state_path, "w") as f:
            json.dump(sorted(done), f)

    def fetch(i: int) -> None:
        if i in done:
            return
        start, end = i * chunk, min(size, (i + 1) * chunk) - 1
        for attempt in range(12):
            read = 0
            try:
                req = urllib.request.Request(a.url, headers={"Range": f"bytes={start}-{end}"})
                with urllib.request.urlopen(req, timeout=60) as r, open(a.out, "r+b") as f:
                    f.seek(start)
                    left = end - start + 1
                    while left > 0:
                        b = r.read(min(1 << 20, left))
                        if not b:
                            raise IOError("short read")
                        f.write(b)
                        left -= len(b)
                        read += len(b)
                        with lock:
                            got[0] += len(b)
                with lock:
                    done.add(i)
                    save()
                return
            except Exception:
                with lock:
                    got[0] -= read  # this attempt is repeated from the start of the chunk
                time.sleep(min(30, 2 ** attempt))
        print("chunk", i, "failed", flush=True)

    def report() -> None:
        t0, base = time.time(), got[0]
        while len(done) < n:
            time.sleep(30)
            print(f"{len(done)}/{n} chunks, {got[0] / 1e9:.1f}/{size / 1e9:.1f} GB, {(got[0] - base) / max(1, time.time() - t0) / 1e6:.2f} MB/s", flush=True)

    threading.Thread(target=report, daemon=True).start()
    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(fetch, range(n)))
    ok = len(done) == n
    print("DONE" if ok else "INCOMPLETE (run it again to continue)", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
