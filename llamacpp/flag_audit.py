"""llama-server CLI option auditor + version flag comparison.

llama-server's flag surface drifts between builds — flags get renamed,
removed, or have their accepted values changed (e.g. v9243 dropped
``--checkpoint-every-n-tokens`` for ``--checkpoint-min-step``). When that
happens the argv telecode builds is rejected and the server refuses to
start. This module backs the tray **Version Manager** card.

It operates on *real binaries*: the active ``llama-server`` plus every
``.bak-<ts>`` the updater leaves behind (see ``llamacpp.updater``). For any
binary it can:

1. **Probe** — run ``--help`` / ``--version`` and parse the flag surface into
   ``{flag → {aliases, takes_value, allowed, removed, deprecated}}``.
2. **Audit a config** — cross-check every flag ``argv.build_argv`` would emit
   across all configured models against that binary's flags (unknown/removed
   flags, out-of-range enum values).
3. **Compare** two binaries — diff their flag surfaces (added / removed /
   changed accepted-values).

Probing a binary is always tried *live* first; if an old backup binary can't
relaunch (missing DLLs), it falls back to a spec cached at update time
(``record_version_spec`` is called by the updater before+after each install).
Specs are cached under ``data/cli-audit/specs/`` keyed by build number.
Audit / compare reports append to ``data/logs/cli_audit.log`` (in the tray
Logs allowlist).

Stdlib only; safe to import without Qt. Parsing is heuristic against
llama.cpp's ``common_arg`` help formatter (fixed description column, 2+-space
gap between the flag header and its description).
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

def _spec_dir() -> Path:
    """``data/cli-audit/specs`` — version-keyed flag-spec cache."""
    base = Path(cfg.log_file()).resolve().parent.parent  # …/data
    d = base / "cli-audit" / "specs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path() -> str:
    return os.path.join(app_config.logs_dir(), "cli_audit.log")


# ── Help parsing ─────────────────────────────────────────────────────────────

_FLAG_TOKEN = re.compile(r"^--?[A-Za-z0-9][A-Za-z0-9._-]*$")
_SECTION_RE = re.compile(r"^-{3,}.*-{3,}\s*$")
# A real flag is "-"/"--" + a letter; "-1"/"-0.5" is a (negative) value.
_IS_FLAG = re.compile(r"^--?[A-Za-z]")


def binary_path() -> str:
    """Resolved active llama-server binary (PATH lookup applied)."""
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

    Reads the *raw* header string (commas intact) — tokenising it first would
    destroy the ``{a,b,c}`` / comma-list delimiters."""
    allowed: list[str] = []

    for grp in re.findall(r"\{([^}]+)\}", header_str):              # {a,b,c}
        allowed += [x.strip() for x in grp.split(",") if x.strip()]
    for grp in re.findall(r"\[([^\]]+)\]", header_str):             # [on|off|auto]
        if "|" in grp:
            allowed += [x.strip() for x in grp.split("|") if x.strip()]
    for tok in header_str.split():                                  # bare comma-list
        if tok.startswith("-"):
            continue
        if "," in tok and re.fullmatch(r"[a-z0-9][a-z0-9,_-]*", tok):
            allowed += [x.strip() for x in tok.split(",") if x.strip()]
    m = re.search(r"allowed values:\s*(.+)", cont_text)             # continuation
    if m:
        seg = m.group(1).split("(")[0]
        allowed += [x.strip() for x in seg.split(",") if x.strip()]

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
    blocks: list[tuple[str, str]] = []
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


# ── Probe a binary → spec snapshot ────────────────────────────────────────────

def probe(binary: Optional[str] = None) -> dict[str, Any]:
    """Parse the live ``--help`` of `binary` (default: active) into a snapshot."""
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


# ── Version-keyed spec cache ──────────────────────────────────────────────────

def _spec_key(snapshot: dict[str, Any]) -> str:
    ver = (snapshot.get("version") or "").strip()
    if ver:
        return f"b{ver}"
    build = (snapshot.get("build") or "").strip()
    if build:
        return f"build-{build}"
    return "ts-" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def cache_spec(snapshot: dict[str, Any]) -> Path:
    path = _spec_dir() / f"{_spec_key(snapshot)}.json"
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return path


def load_cached_spec(version: str) -> Optional[dict[str, Any]]:
    """Load a cached spec by build number (e.g. '9145')."""
    if not version:
        return None
    p = _spec_dir() / f"b{version}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def record_version_spec(binary: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Probe `binary` and cache its spec. Best-effort — never raises.

    Called by the updater before and after each install so a build's flag
    surface is preserved even after its binary is replaced or can't relaunch.
    """
    try:
        snap = probe(binary)
        cache_spec(snap)
        return snap
    except Exception:
        return None


def _spec_for(binary: Optional[str], version_hint: str = "") -> dict[str, Any]:
    """Spec for a binary: live probe first, cached spec (by version hint) as
    fallback when the binary won't launch."""
    try:
        return probe(binary)
    except Exception as exc:
        cached = load_cached_spec(version_hint)
        if cached is not None:
            cached = dict(cached)
            cached["_source"] = "cache"
            return cached
        raise RuntimeError(
            f"could not probe binary and no cached spec for b{version_hint or '?'}: {exc}"
        )


# ── Emitted-flag extraction ───────────────────────────────────────────────────

def _emitted_flags() -> dict[str, set[str]]:
    """Every flag telecode would emit, mapped to the set of values seen for it
    across all configured models. Flags with no value map to an empty set."""
    out: dict[str, set[str]] = {}
    for name in cfg.models().keys():
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


def cross_check(specs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Validate telecode's emitted flags against a spec's flag surface."""
    idx = _alias_index(specs)
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
                parts = [p.strip() for p in v.split(",")] if "," in v else [v]
                bad = [p for p in parts if p and p.lower() not in allowed_low]
                if bad:
                    bad_values.append({"flag": flag, "value": v,
                                       "bad": bad, "allowed": allowed})
    return {"unknown": unknown, "removed_used": removed_used,
            "deprecated_used": deprecated_used, "bad_values": bad_values}


def diff_specs(old: dict[str, dict[str, Any]],
               new: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Flag-surface diff between two specs."""
    old_keys = set(old.keys())
    new_keys = set(new.keys())
    changed: list[dict[str, Any]] = []
    for k in sorted(old_keys & new_keys):
        o_allowed = old[k].get("allowed") or []
        n_allowed = new[k].get("allowed") or []
        if o_allowed != n_allowed:
            changed.append({"flag": k, "old": o_allowed, "new": n_allowed})
    return {"removed": sorted(old_keys - new_keys),
            "added": sorted(new_keys - old_keys),
            "changed": changed}


# ── Public operations (used by the tray) ──────────────────────────────────────

def audit_config(binary: Optional[str] = None, version_hint: str = "") -> dict[str, Any]:
    """Probe `binary` and cross-check telecode's emitted flags against it."""
    snap = _spec_for(binary, version_hint)
    cc = cross_check(snap["flags"])
    report = {
        "kind": "audit",
        "ran_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "binary": snap.get("binary", binary or binary_path()),
        "version": snap.get("version", ""),
        "build": snap.get("build", ""),
        "source": snap.get("_source", "live"),
        "flag_count": snap.get("flag_count"),
        "models_checked": list(cfg.models().keys()),
        "cross_check": cc,
        "ok": not (cc["unknown"] or cc["removed_used"] or cc["bad_values"]),
    }
    write_log(format_audit(report))
    return report


def compare(binary_a: Optional[str] = None, binary_b: Optional[str] = None,
            *, version_a: str = "", version_b: str = "") -> dict[str, Any]:
    """Diff the flag surfaces of two binaries (A = current, B = selected)."""
    spec_a = _spec_for(binary_a, version_a)
    spec_b = _spec_for(binary_b, version_b)
    d = diff_specs(spec_b["flags"], spec_a["flags"])  # old=B, new=A
    report = {
        "kind": "compare",
        "ran_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "a_label": f"b{spec_a.get('version') or '?'} ({spec_a.get('binary')})",
        "b_label": f"b{spec_b.get('version') or '?'} ({spec_b.get('binary')})",
        "a_version": spec_a.get("version", ""),
        "b_version": spec_b.get("version", ""),
        "a_count": spec_a.get("flag_count"),
        "b_count": spec_b.get("flag_count"),
        "added": d["added"],       # present in A (current), absent in B
        "removed": d["removed"],   # present in B, gone in A
        "changed": d["changed"],
    }
    write_log(format_compare(report))
    return report


# ── Report formatting / logging ───────────────────────────────────────────────

def format_audit(rep: dict[str, Any]) -> str:
    cc = rep["cross_check"]
    src = "" if rep.get("source", "live") == "live" else "  [from cache]"
    lines = [
        f"AUDIT  b{rep['version'] or '?'} ({rep['build'] or '?'}){src} — {rep['ran_at']}",
        f"  binary : {rep['binary']}",
        f"  flags  : {rep['flag_count']}",
        f"  models : {', '.join(rep['models_checked']) or '(none)'}",
    ]
    if cc["unknown"]:
        lines.append(f"  [X] flags telecode uses that are UNKNOWN ({len(cc['unknown'])}):")
        lines += [f"       {u['flag']}" for u in cc["unknown"]]
    if cc["removed_used"]:
        lines.append(f"  [X] flags telecode uses that were REMOVED ({len(cc['removed_used'])}):")
        lines += [f"       {r['flag']}" for r in cc["removed_used"]]
    if cc["bad_values"]:
        lines.append(f"  [!] values outside the allowed set ({len(cc['bad_values'])}):")
        for bv in cc["bad_values"]:
            lines.append(f"       {bv['flag']} = {bv['value']}  "
                         f"(bad: {','.join(bv['bad'])}; allowed: {','.join(bv['allowed'])})")
    if cc["deprecated_used"]:
        lines.append(f"  [~] deprecated flags telecode still uses ({len(cc['deprecated_used'])}):")
        lines += [f"       {d['flag']}" for d in cc["deprecated_used"]]
    if not (cc["unknown"] or cc["removed_used"] or cc["bad_values"] or cc["deprecated_used"]):
        lines.append("  [ok] all emitted flags valid against this build")
    return "\n".join(lines)


def format_compare(rep: dict[str, Any]) -> str:
    lines = [
        f"COMPARE current {rep['a_label']} ({rep['a_count']} flags) "
        f"vs {rep['b_label']} ({rep['b_count']} flags) — {rep['ran_at']}",
        f"  {len(rep['added'])} added, {len(rep['removed'])} removed, "
        f"{len(rep['changed'])} changed (current relative to selected):",
    ]
    lines += [f"       + {k}  (new in current)" for k in rep["added"]]
    lines += [f"       - {k}  (gone in current)" for k in rep["removed"]]
    for c in rep["changed"]:
        lines.append(f"       ~ {c['flag']}: {','.join(c['new']) or '∅'} "
                     f"(was {','.join(c['old']) or '∅'})")
    if not (rep["added"] or rep["removed"] or rep["changed"]):
        lines.append("       (identical flag surface)")
    return "\n".join(lines)


def write_log(text: str) -> None:
    try:
        with open(log_path(), "a", encoding="utf-8") as fp:
            fp.write(text)
            fp.write("\n" + ("─" * 70) + "\n")
    except Exception:
        pass
