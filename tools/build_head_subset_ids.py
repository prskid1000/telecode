"""Build a ranked token-id list for --head-subset-ids / --spec-draft-head-subset-ids from your own text.

The dense base output head or MTP draft head can keep only some rows of the output head on the GPU. By default these are the first tokens of the vocabulary; this tool
ranks the tokens your own conversations use and writes them most used first, so the GPU rows are the ones the drafts really pick.

usage: python tools/build_head_subset_ids.py --out data/head-ids.txt [--top 32768] [--server http://127.0.0.1:18080] FILE_OR_FOLDER ...
  FILE_OR_FOLDER: .txt / .md / .jsonl / .json (all text in them is used); folders are searched recursively
  --server: a running llama-server (any model of the SAME family: the token ids must be those of the model that drafts); default from settings.json
"""
import argparse
import collections
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CHUNK = 20000  # characters per tokenize request


def texts(paths):
    for p in paths:
        files = []
        if os.path.isdir(p):
            for d, _dirs, fs in os.walk(p):
                files += [os.path.join(d, f) for f in fs if f.lower().endswith((".txt", ".md", ".jsonl", ".json"))]
        else:
            files.append(p)
        for f in files:
            try:
                raw = open(f, encoding="utf-8", errors="replace").read()
            except OSError as exc:
                print("skipped", f, exc, file=sys.stderr)
                continue
            if f.lower().endswith((".jsonl", ".json")):
                strings = []

                def walk(o):
                    if isinstance(o, str):
                        strings.append(o)
                    elif isinstance(o, dict):
                        for v in o.values():
                            walk(v)
                    elif isinstance(o, list):
                        for v in o:
                            walk(v)
                for line in (raw.splitlines() if f.lower().endswith(".jsonl") else [raw]):
                    try:
                        walk(json.loads(line))
                    except ValueError:
                        pass
                raw = "\n".join(strings)
            for i in range(0, len(raw), CHUNK):
                yield raw[i:i + CHUNK]


def tokenize(server, text):
    req = urllib.request.Request(server.rstrip("/") + "/tokenize", json.dumps({"content": text, "add_special": False}).encode(),
                                 {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=120))["tokens"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--top", type=int, default=32768, help="how many token ids to write")
    ap.add_argument("--server", default="")
    a = ap.parse_args()
    server = a.server
    if not server:
        from llamacpp import config as cfg
        server = cfg.upstream_url()
    counts = collections.Counter()
    n_chunks = 0
    for t in texts(a.inputs):
        for tok in tokenize(server, t):
            counts[tok] += 1
        n_chunks += 1
    if not counts:
        raise SystemExit("no text found")
    ranked = [tok for tok, _c in counts.most_common(a.top)]
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(str(t) for t in ranked) + "\n")
    total = sum(counts.values())
    covered = sum(counts[t] for t in ranked)
    print(f"{n_chunks} chunks, {total} tokens, {len(counts)} distinct; wrote {len(ranked)} ids to {a.out}, they cover {100.0*covered/total:.1f}% of the tokens")


if __name__ == "__main__":
    main()
