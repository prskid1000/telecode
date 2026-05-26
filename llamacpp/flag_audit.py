"""On-demand llama-server CLI option auditor.

llama-server's flag surface drifts between builds — flags get renamed,
removed, or have their accepted values changed (e.g. v9243 dropped
``--checkpoint-every-n-tokens`` in favour of ``--checkpoint-min-step``).
When that happens the argv telecode builds is rejected and the server
refuses to start. This module catches that class of breakage *before* a
spawn, on demand from the tray's Updates section.

What it does
------------
1. Parse ``llama-server --help`` into a structured spec: every option,
   all its aliases, whether it takes a value, and any acceptable values
   (``{none,linear,yarn}`` enums, ``[on|off|auto]`` toggles,
   ``allowed values: …`` continuation lines, comma-list placeholders).
2. Persist that spec as a timestamped *snapshot* under
   ``data/cli-audit/llama-server/``. The most recent capture is also kept
   as ``baseline.json`` (the default comparison target). ``restore()``
   promotes any snapshot back to baseline, backing up the existing
   baseline first — so a known-good capture is always recoverable.
3. **Audit** = two checks in one pass:
     - *cross-check*: every flag telecode would actually emit (from
       ``argv.build_argv`` across all configured models) is validated
       against the live ``--help`` — unknown/removed flags and
       out-of-range enum values are reported.
     - *snapshot diff*: the live capture vs a chosen snapshot — options
       removed / added / with changed accepted-values.
4. Every audit appends a human-readable report to
   ``data/logs/cli_audit.log`` (viewable in the tray Logs section).

Stdlib only; safe to import without Qt. All parsing is heuristic against
llama.cpp's ``common_arg`` help formatter (fixed description column,
2+-space gap between the flag header and its description).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import config as app_config
from llamacpp import config as cfg
from llamacpp import argv as argv_builder


# ── Paths ──────────────────────────────────────────────────────────────────

def _audit_dir() -> Path:
    """``data/cli-audit/llama-server`` — derived from the logs dir's parent
    (the ``data`` directory) so it follows TELECODE_SETTINGS relocation."""
    base = Path(cfg.log_file()).resolve().parent.parent  # …/data
    d = base / "cli-audit" / "llama-server"
    d.mkdir(parents=True, exist_ok=True)
    (d / "snapshots").mkdir(exist_ok=True)
    return d


def _baseline_path() -> Path:
    return _audit_dir() / "baseline.json"


def _snapshot_path(ts: str) -> Path:
    return _audit_dir() / "snapshots" / f"{ts}.json"


def log_path() -> str:
    return os.path.join(app_config.logs_dir(), "cli_audit.log")


# ── Help parsing ─────────────────────────────────────────────────────────────

_FLAG_TOKEN = re.compile(r"^--?[A-Za-z0-9][A-Za-z0-9._-]*$")
_SECTION_RE = re.compile(r"^-{3,}.*-{3,}\s*$")


def binary_path() -> str:
    """Resolved llama-server binary (PATH lookup applied)."""
    b = cfg.binary()
    return shutil.which(b) or b


def _run_capture(args: list[str]) -> str:
    creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creation,
        timeout=30,
        text=True,
        errors="replace",
    )
    return proc.stdout or ""


def detect_version(binary: Optional[str] = None) -> dict[str, str]:
    """Best-effort version/build extraction from ``--version``."""
    b = binary or binary_path()
    out = ""
    try:
        out = _run_capture([b, "--version"])
    except Exception:
        return {"version": "", "build": "", "raw": ""}
    raw = out.strip().splitlines()[0].strip() if out.strip() else ""
    ver = ""
    build = ""
    m = re.search(r"version:\s*(\S+)", out)
    if m:
        ver = m.group(1)
    m = re.search(r"\(([0-9a-fA-F]{6,})\)", out)
    if m:
        build = m.group(1)
    return {"version": ver, "build": build, "raw": raw}


def _extract_allowed(header_str: str, cont_text: str) -> list[str]:
    """Pull acceptable values from the value placeholder and/or the
    ``allowed values: …`` continuation text. Returns [] when free-form.

    Reads the *raw* header string (commas intact) — splitting it into tokens
    first would destroy the ``{a,b,c}`` / comma-list delimiters."""
    allowed: list[str] = []

    # {a,b,c}
    for grp in re.findall(r"\{([^}]+)\}", header_str):
        allowed += [x.strip() for x in grp.split(",") if x.strip()]
    # [on|off|auto]
    for grp in re.findall(r"\[([^\]]+)\]", header_str):
        if "|" in grp:
            allowed += [x.strip() for x in grp.split("|") if x.strip()]
    # bare comma-list placeholder, e.g. --spec-type none,draft-simple,...
    for tok in header_str.split():
        if tok.startswith("-"):
            continue
        if "," in tok and re.fullmatch(r"[a-z0-9][a-z0-9,_-]*", tok):
            allowed += [x.strip() for x in tok.split(",") if x.strip()]
    # continuation: "allowed values: f32, f16, bf16, …"
    m = re.search(r"allowed values:\s*(.+)", cont_text)
    if m:
        seg = m.group(1).split("(")[0]
        allowed += [x.strip() for x in seg.split(",") if x.strip()]

    # de-dup preserving order, drop ellipsis artifacts
    seen: set[str] = set()
    out: list[str] = []
    for a in allowed:
        a = a.strip()
        if not a or a in ("...", "…") or a in seen:
            continue
        seen.add(a)
        out.append(a)
    return out


def parse_help(text: str) -> dict[str, dict[str, Any]]:
    """Parse ``--help`` text → {canonical_flag: spec}.

    spec = {aliases, canonical, takes_value, allowed, deprecated, removed}.
    """
    lines = text.splitlines()

    # Group each definition line with its indented continuation lines.
    blocks: list[tuple[str, str]] = []  # (defline, continuation_text)
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        is_def = bool(stripped) and stripped[0] == "-" and indent <= 8 \
            and not _SECTION_RE.match(stripped)
        if not is_def:
            i += 1
            continue
        defline = line
        cont: list[str] = []
        j = i + 1
        while j < n:
            nxt = lines[j]
            nstrip = nxt.strip()
            nindent = len(nxt) - len(nxt.lstrip(" "))
            # continuation = indented, non-empty, not a new flag/section
            if nstrip and (nstrip[0] != "-" or nindent > 8) \
                    and not _SECTION_RE.match(nstrip):
                cont.append(nstrip)
                j += 1
                continue
            break
        blocks.append((defline, " ".join(cont)))
        i = j

    specs: dict[str, dict[str, Any]] = {}
    for defline, cont in blocks:
        segs = re.split(r" {2,}", defline.strip())
        header_parts: list[str] = []
        desc_parts: list[str] = []
        in_header = True
        for s in segs:
            if in_header and s.startswith("-"):
                header_parts.append(s)
            else:
                in_header = False
                desc_parts.append(s)
        desc = " ".join(desc_parts + ([cont] if cont else [])).strip()

        header_str = " ".join(header_parts)

        # Tokenise the header into aliases + value placeholder tokens.
        header_tokens: list[str] = []
        for part in header_parts:
            for tok in re.split(r"[,\s]+", part.strip()):
                if tok:
                    header_tokens.append(tok)

        aliases = [t.rstrip(",") for t in header_tokens
                   if t.startswith("-") and _FLAG_TOKEN.match(t.rstrip(","))]
        if not aliases:
            continue
        value_tokens = [t for t in header_tokens if not t.startswith("-")]
        takes_value = bool(value_tokens)

        canonical = next((a for a in aliases if a.startswith("--")), aliases[0])
        allowed = _extract_allowed(header_str, cont)
        low_desc = desc.lower()
        removed = "argument has been removed" in low_desc or "has been removed" in low_desc
        deprecated = "deprecated" in low_desc

        specs[canonical] = {
            "aliases": aliases,
            "canonical": canonical,
            "takes_value": takes_value,
            "allowed": allowed,
            "deprecated": deprecated,
            "removed": removed,
        }
    return specs


def _alias_index(specs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Flat map of every alias token → its spec."""
    idx: dict[str, dict[str, Any]] = {}
    for spec in specs.values():
        for a in spec["aliases"]:
            idx[a] = spec
    return idx


# ── Capture / snapshots ──────────────────────────────────────────────────────

def capture(binary: Optional[str] = None) -> dict[str, Any]:
    """Parse the live ``--help``, build a snapshot dict (not yet persisted)."""
    b = binary or binary_path()
    help_text = _run_capture([b, "--help"])
    if not help_text.strip():
        raise RuntimeError(f"`{b} --help` produced no output")
    specs = parse_help(help_text)
    if not specs:
        raise RuntimeError(f"could not parse any flags from `{b} --help`")
    ver = detect_version(b)
    return {
        "captured_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "binary": b,
        "version": ver["version"],
        "build": ver["build"],
        "version_raw": ver["raw"],
        "flag_count": len(specs),
        "flags": specs,
    }


def save_snapshot(snapshot: dict[str, Any], *, make_baseline: bool = True) -> str:
    """Persist a snapshot under snapshots/<ts>.json (+ promote to baseline).

    Returns the snapshot timestamp id.
    """
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = _snapshot_path(ts)
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    if make_baseline:
        _baseline_path().write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return ts


def list_snapshots() -> list[dict[str, Any]]:
    """Saved snapshots, newest first. Each: {ts, version, build, captured_at,
    flag_count, label, is_baseline}."""
    base = _audit_dir() / "snapshots"
    baseline = load_baseline()
    baseline_at = baseline.get("captured_at") if baseline else None
    out: list[dict[str, Any]] = []
    for f in sorted(base.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        ts = f.stem
        ver = data.get("version") or "?"
        cap = data.get("captured_at", ts)
        is_baseline = baseline_at is not None and data.get("captured_at") == baseline_at
        label = f"{cap} · b{ver} · {data.get('flag_count', '?')} flags"
        if is_baseline:
            label += "  (baseline)"
        out.append({
            "ts": ts,
            "version": ver,
            "build": data.get("build", ""),
            "captured_at": cap,
            "flag_count": data.get("flag_count"),
            "label": label,
            "is_baseline": is_baseline,
        })
    return out


def load_snapshot(ts: str) -> dict[str, Any]:
    return json.loads(_snapshot_path(ts).read_text(encoding="utf-8"))


def load_baseline() -> Optional[dict[str, Any]]:
    p = _baseline_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def restore(ts: str) -> None:
    """Promote snapshot <ts> to baseline, backing up the current baseline first."""
    snap = load_snapshot(ts)  # raises if missing
    cur = _baseline_path()
    if cur.exists():
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = _audit_dir() / "snapshots" / f"baseline-backup-{stamp}.json"
        backup.write_text(cur.read_text(encoding="utf-8"), encoding="utf-8")
    cur.write_text(json.dumps(snap, indent=2), encoding="utf-8")


def delete(ts: str) -> None:
    p = _snapshot_path(ts)
    if p.exists():
        p.unlink()


# ── Emitted-flag extraction ───────────────────────────────────────────────────

# A real flag is "-" or "--" followed by a letter. A token like "-1" or
# "-0.5" is a (negative) value, not a flag — important for --seed -1, etc.
_IS_FLAG = re.compile(r"^--?[A-Za-z]")


def _emitted_flags() -> dict[str, set[str]]:
    """Every flag telecode would emit, mapped to the set of values seen for it
    across all configured models. Flags with no value map to an empty set."""
    out: dict[str, set[str]] = {}
    models = list(cfg.models().keys())
    if not models:
        return out
    for name in models:
        try:
            argv = argv_builder.build_argv(name)
        except Exception:
            continue
        k = 1  # skip argv[0] (binary)
        while k < len(argv):
            tok = argv[k]
            if isinstance(tok, str) and _IS_FLAG.match(tok):
                nxt = argv[k + 1] if k + 1 < len(argv) else None
                if isinstance(nxt, str) and not _IS_FLAG.match(nxt):
                    out.setdefault(tok, set()).add(nxt)
                    k += 2
                    continue
                out.setdefault(tok, set())
            k += 1
    return out


# ── Audit ──────────────────────────────────────────────────────────────────

def audit(compare_ts: Optional[str] = None) -> dict[str, Any]:
    """Parse live help, cross-check telecode's emitted flags, and diff against a
    snapshot. Returns a structured report (also written to the log)."""
    live = capture()
    specs = live["flags"]
    idx = _alias_index(specs)

    # ── 1. cross-check emitted flags ──
    unknown: list[dict[str, Any]] = []
    removed_used: list[dict[str, Any]] = []
    deprecated_used: list[dict[str, Any]] = []
    bad_values: list[dict[str, Any]] = []
    for flag, values in sorted(_emitted_flags().items()):
        spec = idx.get(flag)
        if spec is None:
            unknown.append({"flag": flag})
            continue
        if spec.get("removed"):
            removed_used.append({"flag": flag, "canonical": spec["canonical"]})
        elif spec.get("deprecated"):
            deprecated_used.append({"flag": flag, "canonical": spec["canonical"]})
        allowed = spec.get("allowed") or []
        if allowed:
            allowed_low = {a.lower() for a in allowed}
            for v in sorted(values):
                # comma-joined values (e.g. --spec-type a,b) validate per item
                parts = [p.strip() for p in v.split(",")] if "," in v else [v]
                bad = [p for p in parts if p and p.lower() not in allowed_low]
                if bad:
                    bad_values.append({
                        "flag": flag, "value": v,
                        "bad": bad, "allowed": allowed,
                    })

    # ── 2. snapshot diff ──
    base = load_snapshot(compare_ts) if compare_ts else load_baseline()
    diff: dict[str, Any] = {"compared": False}
    if base:
        old = base.get("flags", {})
        new = specs
        old_keys = set(old.keys())
        new_keys = set(new.keys())
        changed: list[dict[str, Any]] = []
        for k in sorted(old_keys & new_keys):
            o_allowed = old[k].get("allowed") or []
            n_allowed = new[k].get("allowed") or []
            if o_allowed != n_allowed:
                changed.append({"flag": k, "old": o_allowed, "new": n_allowed})
        diff = {
            "compared": True,
            "against": base.get("captured_at", compare_ts or "baseline"),
            "against_version": base.get("version", ""),
            "removed": sorted(old_keys - new_keys),
            "added": sorted(new_keys - old_keys),
            "changed": changed,
        }

    report = {
        "ran_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "binary": live["binary"],
        "version": live["version"],
        "build": live["build"],
        "flag_count": live["flag_count"],
        "models_checked": list(cfg.models().keys()),
        "cross_check": {
            "unknown": unknown,
            "removed_used": removed_used,
            "deprecated_used": deprecated_used,
            "bad_values": bad_values,
        },
        "diff": diff,
        "ok": not (unknown or removed_used or bad_values),
    }
    write_log(report)
    return report


# ── Report formatting / logging ───────────────────────────────────────────────

def format_report(rep: dict[str, Any]) -> str:
    """Compact plaintext rendering used for both the log file and the tray."""
    cc = rep["cross_check"]
    lines: list[str] = []
    lines.append(f"llama-server flag audit — {rep['ran_at']}")
    lines.append(f"  binary : {rep['binary']}")
    lines.append(f"  version: b{rep['version'] or '?'} ({rep['build'] or '?'}), "
                 f"{rep['flag_count']} flags")
    lines.append(f"  models : {', '.join(rep['models_checked']) or '(none)'}")

    if cc["unknown"]:
        lines.append(f"  [X] flags telecode uses that are UNKNOWN ({len(cc['unknown'])}):")
        for u in cc["unknown"]:
            lines.append(f"       {u['flag']}")
    if cc["removed_used"]:
        lines.append(f"  [X] flags telecode uses that were REMOVED ({len(cc['removed_used'])}):")
        for r in cc["removed_used"]:
            lines.append(f"       {r['flag']}")
    if cc["bad_values"]:
        lines.append(f"  [!] values outside the allowed set ({len(cc['bad_values'])}):")
        for bv in cc["bad_values"]:
            lines.append(f"       {bv['flag']} = {bv['value']}  "
                         f"(bad: {','.join(bv['bad'])}; allowed: {','.join(bv['allowed'])})")
    if cc["deprecated_used"]:
        lines.append(f"  [~] deprecated flags telecode still uses ({len(cc['deprecated_used'])}):")
        for d in cc["deprecated_used"]:
            lines.append(f"       {d['flag']}")
    if not (cc["unknown"] or cc["removed_used"] or cc["bad_values"] or cc["deprecated_used"]):
        lines.append("  [ok] all emitted flags valid against the live --help")

    diff = rep["diff"]
    if diff.get("compared"):
        lines.append(f"  diff vs {diff['against']} (b{diff.get('against_version') or '?'}): "
                     f"{len(diff['removed'])} removed, {len(diff['added'])} added, "
                     f"{len(diff['changed'])} changed")
        for k in diff["removed"]:
            lines.append(f"       - {k}")
        for k in diff["added"]:
            lines.append(f"       + {k}")
        for c in diff["changed"]:
            lines.append(f"       ~ {c['flag']}: {','.join(c['old']) or '∅'} "
                         f"-> {','.join(c['new']) or '∅'}")
    else:
        lines.append("  diff: no snapshot to compare against")
    return "\n".join(lines)


def write_log(rep: dict[str, Any]) -> None:
    try:
        with open(log_path(), "a", encoding="utf-8") as fp:
            fp.write(format_report(rep))
            fp.write("\n" + ("─" * 70) + "\n")
    except Exception:
        pass
