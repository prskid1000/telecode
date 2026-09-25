"""Run / step budgets (P2): ``{max_usd, max_tokens, max_seconds}``.

Resolution
----------
* Run budget = the run's POST body ``budget`` field-wise over ``job.budget``.
* A step's budget per dimension = the step's own override when set, else an
  even share of what the run has left: dollars and tokens split across the
  steps still to run, seconds across the phases still to run (the steps of one
  phase run concurrently, so they share the same wall clock).
* A dimension left unset is unlimited.

Enforcement (per attempt)
-------------------------
* Claude: ``--max-budget-usd`` for dollars (the CLI stops itself and reports
  ``error_max_budget_usd``).
* Every engine: wall clock and a token cap enforced by the Engine Runner from
  the normalised ``usage`` events — the CLI tree is stopped gracefully and the
  step ends ``budget_exceeded``. Codex and Antigravity report no cost, so a
  dollar cap cannot be enforced for them (``usd_enforced: false``).

Tokens here are *budget tokens*: input + cache writes + output. Cache reads
are not counted — they are the same context re-read every turn and would make
any cap meaningless (a single Claude turn re-reads ~20-90 k cached tokens).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

DIMS = ("max_usd", "max_tokens", "max_seconds")


def normalize(b: Any) -> Dict[str, Optional[float]]:
    """{max_usd, max_tokens, max_seconds} with positive numbers or None.
    Raises ValueError on a negative or non-numeric value."""
    out: Dict[str, Optional[float]] = {k: None for k in DIMS}
    if not b:
        return out
    if not isinstance(b, dict):
        raise ValueError("budget must be an object {max_usd, max_tokens, max_seconds}")
    for k in DIMS:
        v = b.get(k)
        if v is None or v == "":
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"budget.{k} must be a number, got {v!r}") from None
        if f < 0:
            raise ValueError(f"budget.{k} must be >= 0")
        if f == 0:
            continue  # 0 = unlimited, like blank
        out[k] = int(f) if k == "max_tokens" else round(f, 6)
    return out


def merge(*layers: Any) -> Dict[str, Optional[float]]:
    """First non-None per dimension, earlier layers winning."""
    out: Dict[str, Optional[float]] = {k: None for k in DIMS}
    for layer in layers:
        n = normalize(layer)
        for k in DIMS:
            if out[k] is None and n.get(k) is not None:
                out[k] = n[k]
    return out


def is_set(b: Optional[Dict[str, Any]]) -> bool:
    return bool(b) and any((b or {}).get(k) is not None for k in DIMS)


def usage_tokens(u: Optional[Dict[str, Any]]) -> int:
    """Budget tokens of a step usage record (see module docstring)."""
    if not u:
        return 0
    total_in = int(u.get("input_tokens") or 0)
    return max(0, total_in - int(u.get("cache_read_tokens") or 0)) + int(u.get("output_tokens") or 0)


def tokens_from_engine(tokens: Optional[Dict[str, Any]]) -> int:
    """Budget tokens of a normalised engine ``tokens`` dict."""
    t = tokens or {}
    return int(t.get("input") or 0) + int(t.get("cache_write") or 0) + int(t.get("output") or 0)


def spent(run: Dict[str, Any]) -> Dict[str, Any]:
    usd, complete, tokens = 0.0, True, 0
    for st in run.get("steps") or []:
        u = st.get("usage")
        if not u:
            continue
        tokens += usage_tokens(u)
        if u.get("cost_usd") is None:
            complete = False
        else:
            usd += float(u["cost_usd"])
    return {"usd": round(usd, 6), "usd_complete": complete, "tokens": tokens,
            "seconds": round(float(run.get("active_seconds") or 0), 1)}


def remaining(run: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """What the run has left per dimension (None = unlimited)."""
    lim = run.get("budget") or {}
    sp = spent(run)
    out: Dict[str, Optional[float]] = {}
    for k, s in (("max_usd", sp["usd"]), ("max_tokens", sp["tokens"]), ("max_seconds", sp["seconds"])):
        out[k] = None if lim.get(k) is None else max(0.0, float(lim[k]) - float(s))
    return out


def exhausted(run: Dict[str, Any]) -> Optional[str]:
    """Name of the first run budget dimension that is used up, else None."""
    for k, v in remaining(run).items():
        if v is not None and v <= 0:
            return k
    return None


def for_step(run: Dict[str, Any], step: Dict[str, Any], *, steps_left: int, phases_left: int,
             override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Effective budget for one attempt of ``step``: {max_usd, max_tokens,
    max_seconds, source:{dim: step|run|retry}}."""
    own = merge(override, (step.get("spec") or {}).get("budget"))
    rem = remaining(run)
    out: Dict[str, Any] = {"source": {}}
    for k in DIMS:
        if own.get(k) is not None:
            out[k] = own[k]
            out["source"][k] = "retry" if override and normalize(override).get(k) is not None else "step"
        elif rem.get(k) is not None:
            n = max(1, phases_left if k == "max_seconds" else steps_left)
            v = rem[k] / n
            out[k] = int(v) if k == "max_tokens" else round(v, 6)
            out["source"][k] = "run"
        else:
            out[k] = None
    return out


def usage_add(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Sum two step usage records (cost None if either side is unknown)."""
    if not a:
        return dict(b) if b else None
    if not b:
        return dict(a)
    out = dict(a)
    for k, v in b.items():
        if k == "cost_usd":
            continue
        if isinstance(v, (int, float)) and isinstance(a.get(k), (int, float)):
            out[k] = a[k] + v
        elif k not in out:
            out[k] = v
    ca, cb = a.get("cost_usd"), b.get("cost_usd")
    out["cost_usd"] = None if ca is None or cb is None else round(float(ca) + float(cb), 6)
    return out


def unenforceable(engine: str, b: Dict[str, Any]) -> List[str]:
    """Dimensions this engine cannot enforce (shown in the UI)."""
    out = []
    if b.get("max_usd") is not None and engine != "claude_code":
        out.append("max_usd")
    return out


def steps_after(steps: Iterable[Dict[str, Any]], phase: int) -> List[Dict[str, Any]]:
    return [s for s in steps if int((s.get("spec") or {}).get("phase") or 0) >= phase]
