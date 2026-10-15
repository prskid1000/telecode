"""Compare the outputs of runs made with tools/moe_cache_bench.py / tools/bench_matrix.py against a reference run.

Two lossy-looking differences are common and harmless: CPU and GPU kernels round differently, so a greedy text can diverge at a token where two
choices are nearly tied. `probs` shows whether a divergence is such a near-tie (small gap between the two best tokens) or a real change.

usage:
  python tools/compare_runs.py texts RUN_DIR REF_LABEL
      for every case in RUN_DIR: per prompt, how many characters it shares with the reference text ("same" = identical)
  python tools/compare_runs.py probs RUN_DIR REF_LABEL LABEL [LABEL ...]
      per prompt: the first token where LABEL differs from the reference, with the probabilities of the two best tokens in both runs
      (needs runs made with probs > 0: tc_<label>_probs.json), or "all N tokens identical" with the largest probability difference

RUN_DIR holds one folder per case (the `--out` of the bench / of bench_matrix) with tc_<label>.json inside.
"""
import json
import math
import sys
from pathlib import Path


def load(run: Path, label: str, suffix: str = ""):
    return json.loads((run / label / f"tc_{label}{suffix}.json").read_text(encoding="utf-8"))


def texts(run: Path, ref: str) -> None:
    ref_out = load(run, ref)
    print(f"reference: {ref}; per prompt: characters shared with the reference / its length")
    for d in sorted(p for p in run.iterdir() if p.is_dir()):
        try:
            out = load(run, d.name)
        except (OSError, ValueError):
            continue
        cells = []
        for a, b in zip(ref_out, out):
            n = 0
            while n < min(len(a), len(b)) and a[n] == b[n]:
                n += 1
            cells.append("same" if a == b else f"{n}/{len(a)}")
        print(f"{d.name:24s}", "  ".join(cells))


def top(entry: dict) -> list[tuple[str, float]]:
    lst = entry.get("probs") or entry.get("top_logprobs") or []
    return [(t["token"], t["prob"] if t.get("prob") is not None else math.exp(t["logprob"])) for t in lst]


def probs(run: Path, ref: str, labels: list[str]) -> None:
    ref_p = load(run, ref, "_probs")
    for label in labels:
        print(f"--- {label} vs {ref}")
        for pi, (a, b) in enumerate(zip(ref_p, load(run, label, "_probs"))):
            maxdiff = 0.0
            first = None
            for i in range(min(len(a), len(b))):
                ta, tb = top(a[i]), top(b[i])
                if not ta or not tb:
                    continue
                if ta[0][0] != tb[0][0]:
                    first = i
                    gap = ta[0][1] - (ta[1][1] if len(ta) > 1 else 0.0)
                    print(f"  prompt {pi}: first different token at {i}: reference {ta[0][0]!r} p={ta[0][1]:.3f} (2nd {ta[1][0]!r} {ta[1][1]:.3f}) -> "
                          f"{tb[0][0]!r} p={tb[0][1]:.3f} (2nd {tb[1][0]!r} {tb[1][1]:.3f}); reference gap {gap:.3f}")
                    break
                maxdiff = max(maxdiff, abs(ta[0][1] - tb[0][1]))
            if first is None:
                print(f"  prompt {pi}: all {min(len(a), len(b))} tokens identical, largest probability difference of the top token {maxdiff:.4f}")


def main() -> int:
    if len(sys.argv) < 4 or sys.argv[1] not in ("texts", "probs"):
        print(__doc__)
        return 2
    run = Path(sys.argv[2])
    if sys.argv[1] == "texts":
        texts(run, sys.argv[3])
    else:
        probs(run, sys.argv[3], sys.argv[4:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
