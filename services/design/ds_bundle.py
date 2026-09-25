"""Precompile a design system's `components/**/*.jsx` into one browser bundle (`bundle.js`).

The bundle is what a project loads instead of N `<script type="text/babel" src>` tags: one plain
`<script src="_ds/<slug>/bundle.js">` after React 18 UMD, no Babel on the page, and every component
on `window` (each file's own `window.X` export) plus one namespace object `window.<Namespace>`.

First line, always:

    /* @ds-bundle: {"schema":"teledesign-ds-bundle/v1","system":"neutral","namespace":"Neutral",
                    "compiler":"babel-standalone@7.29.0/node","sources":{"components/…/Button.jsx":"3f2a…"},…} */

`sources` maps every compiled file to the first 12 hex chars of its sha256, so `status()` can say
whether the bundle is stale without recompiling anything.

Compiler choice (no Node at runtime is a requirement, so Node is only an accelerator):

  1. `node` if it is on PATH — runs @babel/standalone's UMD build as a CommonJS module. ~1 s.
  2. headless Edge (`msedge --headless=new --dump-dom` on a local page that loads the same
     babel.min.js and writes the compiled output, base64'd, into the DOM). ~3 s, no CDP needed.
  3. "in-browser" fallback — the bundle embeds the JSX sources and compiles them at load time with
     the page's own `window.Babel` (the pinned @babel/standalone UMD must be loaded first). Always
     works; costs the page one Babel load, which is exactly what the bundle exists to avoid, so it
     is logged and flagged in the header (`"compiler":"in-browser"`).

All three use the same Babel (@babel/standalone 7.29.0, the version the seeds pin), downloaded once
from unpkg through `proxy.media_fetch` and verified against the SRI hash the seed cards use, then
cached under `data/design/.cache/`. The preset is `react` only (classic runtime) — the output keeps
modern syntax, which every browser the preview runs in supports.

Setting: `design.bundle_compiler` = `auto` (default) | `node` | `edge` | `browser`.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config

log = logging.getLogger("telecode.services.design.ds_bundle")

BABEL_VERSION = "7.29.0"
BABEL_URL = f"https://unpkg.com/@babel/standalone@{BABEL_VERSION}/babel.min.js"
BABEL_SRI = "sha384-m08KidiNqLdpJqLq95G/LEi8Qvjl/xUYll3QILypMoQ65QorJ9Lvtp2RXYGBFj1y"
BUNDLE_NAME = "bundle.js"
HEADER_RE = re.compile(r"^/\* @ds-bundle: (\{.*?\}) \*/", re.S)
MAX_SOURCES = 200
MAX_SOURCE_BYTES = 512 * 1024

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_babel_lock = threading.Lock()


class BundleError(RuntimeError):
    pass


def _cache_dir() -> Path:
    d = Path(config._settings_dir()) / "data" / "design" / ".cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def hash12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


# ── Sources ───────────────────────────────────────────────────────────────

def component_sources(system_dir: Path) -> List[Tuple[str, bytes]]:
    """components/**/*.jsx in a stable order: manifest order first, then the rest by path."""
    comp_dir = system_dir / "components"
    if not comp_dir.is_dir():
        return []
    found = {p.relative_to(system_dir).as_posix(): p for p in comp_dir.rglob("*.jsx")
             if p.is_file() and not p.is_symlink()}
    order: List[str] = []
    for c in _manifest(system_dir).get("components") or []:
        path = c.get("path") if isinstance(c, dict) else None
        if path in found and path not in order:
            order.append(path)
    order += sorted(k for k in found if k not in order)
    out = []
    for rel in order[:MAX_SOURCES]:
        data = found[rel].read_bytes()
        if len(data) <= MAX_SOURCE_BYTES:
            out.append((rel, data))
    return out


def _manifest(system_dir: Path) -> Dict[str, Any]:
    try:
        m = json.loads((system_dir / "manifest.json").read_text(encoding="utf-8"))
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def namespace_for(system_dir: Path, slug: str) -> str:
    ns = _manifest(system_dir).get("namespace")
    if isinstance(ns, str) and re.fullmatch(r"[A-Za-z_$][\w$]{0,63}", ns):
        return ns
    base = re.sub(r"[^A-Za-z0-9]+", " ", slug or system_dir.name).title().replace(" ", "")
    if not base or not base[0].isalpha():
        base = "DS" + base
    return base + "DS" if not base.endswith("DS") else base


def export_names(system_dir: Path, sources: List[Tuple[str, bytes]]) -> List[str]:
    """Window globals the bundle collects into the namespace."""
    names: List[str] = []
    for c in _manifest(system_dir).get("components") or []:
        if not isinstance(c, dict):
            continue
        g = c.get("global") or c.get("name")
        g = g.split(".", 1)[1] if isinstance(g, str) and g.startswith("window.") else g
        if isinstance(g, str) and re.fullmatch(r"[A-Za-z_$][\w$]*", g) and g not in names:
            names.append(g)
    for rel, data in sources:
        for m in re.finditer(rb"Object\.assign\(\s*window\s*,\s*\{([^}]*)\}", data):
            for n in re.findall(rb"[A-Za-z_$][\w$]*", m.group(1)):
                s = n.decode()
                if s not in names:
                    names.append(s)
        stem = Path(rel).stem
        if re.fullmatch(r"[A-Z][\w$]*", stem) and stem not in names:
            names.append(stem)
    return names


# ── Header / status ───────────────────────────────────────────────────────

def read_header(bundle_path: Path) -> Optional[Dict[str, Any]]:
    try:
        with bundle_path.open("r", encoding="utf-8", errors="replace") as f:
            head = f.read(64 * 1024)
    except OSError:
        return None
    m = HEADER_RE.match(head)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def status(system_dir: Path) -> Dict[str, Any]:
    """{exists, stale, compiler, built_at, changed:[…], added:[…], removed:[…]} — no compiling."""
    bundle = system_dir / BUNDLE_NAME
    current = {rel: hash12(data) for rel, data in component_sources(system_dir)}
    hdr = read_header(bundle) if bundle.exists() else None
    if not hdr:
        return {"exists": bundle.exists(), "stale": bool(current), "sources": len(current),
                "changed": [], "added": sorted(current), "removed": [], "compiler": None, "built_at": None}
    old = hdr.get("sources") or {}
    changed = sorted(k for k in current if k in old and old[k] != current[k])
    added = sorted(k for k in current if k not in old)
    removed = sorted(k for k in old if k not in current)
    return {"exists": True, "stale": bool(changed or added or removed), "sources": len(current),
            "changed": changed, "added": added, "removed": removed, "compiler": hdr.get("compiler"),
            "built_at": hdr.get("built_at"), "namespace": hdr.get("namespace")}


# ── Babel acquisition ─────────────────────────────────────────────────────

def _sri_ok(data: bytes) -> bool:
    algo, _, want = BABEL_SRI.partition("-")
    return base64.b64encode(hashlib.new(algo, data).digest()).decode() == want


async def ensure_babel() -> Path:
    """Local, SRI-verified copy of @babel/standalone (downloaded once)."""
    path = _cache_dir() / f"babel-standalone-{BABEL_VERSION}.min.js"
    if path.exists() and _sri_ok(path.read_bytes()):
        return path
    from proxy import media_fetch  # lazy: aiohttp import cost only when needed
    data = await media_fetch.fetch_media_bytes(BABEL_URL, max_bytes=16 * 1024 * 1024)
    if not _sri_ok(data):
        raise BundleError("downloaded @babel/standalone does not match its SRI hash")
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path


# ── Compilers ─────────────────────────────────────────────────────────────

_NODE_SCRIPT = r"""
const Babel = require(process.argv[2]);
let buf = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', d => buf += d);
process.stdin.on('end', () => {
  const src = JSON.parse(buf), out = {};
  for (const [p, code] of Object.entries(src)) {
    try { out[p] = {code: Babel.transform(code, {presets: [['react', {runtime: 'classic'}]], filename: p, sourceType: 'script', compact: false, comments: false}).code}; }
    catch (e) { out[p] = {error: String(e && e.message || e)}; }
  }
  process.stdout.write(JSON.stringify(out));
});
"""

_EDGE_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<script src="babel.min.js"></script></head><body>
<script type="application/json" id="src">__SRC__</script>
<pre id="out"></pre>
<script>
(function(){
  var out = {}, src;
  try { src = JSON.parse(document.getElementById('src').textContent); }
  catch (e) { document.getElementById('out').textContent = 'ERR:' + e; return; }
  for (var p in src) {
    try { out[p] = {code: Babel.transform(src[p], {presets: [['react', {runtime: 'classic'}]], filename: p, sourceType: 'script', comments: false}).code}; }
    catch (e) { out[p] = {error: String(e && e.message || e)}; }
  }
  var json = JSON.stringify(out);
  document.getElementById('out').textContent = 'OK:' + btoa(unescape(encodeURIComponent(json)));
})();
</script></body></html>"""


def find_node() -> Optional[str]:
    node = shutil.which("node")
    if node:
        return node
    for env in ("NVM_SYMLINK", "NVM_HOME"):
        base = os.environ.get(env)
        if base and (Path(base) / "node.exe").exists():
            return str(Path(base) / "node.exe")
    return None


def find_edge() -> Optional[str]:
    configured = config.get_nested("design.edge_binary", "") or ""
    for cand in ([configured] if configured else []) + list(EDGE_CANDIDATES):
        if cand and Path(cand).exists():
            return cand
    return shutil.which("msedge") or shutil.which("microsoft-edge") or shutil.which("chromium")


def _compile_node(node: str, babel: Path, sources: Dict[str, str]) -> Dict[str, Dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="td-dsb-") as tmp:
        script = Path(tmp) / "compile.cjs"
        script.write_text(_NODE_SCRIPT, encoding="utf-8")
        proc = subprocess.run([node, str(script), str(babel)], input=json.dumps(sources).encode("utf-8"),
                              capture_output=True, timeout=120, creationflags=_CREATE_NO_WINDOW)
        if proc.returncode != 0:
            raise BundleError(f"node babel failed: {proc.stderr.decode('utf-8', 'replace')[-400:]}")
        return json.loads(proc.stdout.decode("utf-8"))


def _compile_edge(edge: str, babel: Path, sources: Dict[str, str]) -> Dict[str, Dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="td-dsb-") as tmp:
        t = Path(tmp)
        shutil.copyfile(babel, t / "babel.min.js")
        payload = json.dumps(sources).replace("</", "<\\/")
        (t / "compile.html").write_text(_EDGE_PAGE.replace("__SRC__", payload), encoding="utf-8")
        profile = t / "profile"
        args = [edge, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                "--disable-extensions", "--disable-background-networking", f"--user-data-dir={profile}",
                "--virtual-time-budget=20000", "--dump-dom", (t / "compile.html").as_uri()]
        proc = subprocess.run(args, capture_output=True, timeout=120, creationflags=_CREATE_NO_WINDOW)
        dom = proc.stdout.decode("utf-8", "replace")
        m = re.search(r'<pre id="out">(.*?)</pre>', dom, re.S)
        text = html.unescape(m.group(1)) if m else ""
        if not text.startswith("OK:"):
            raise BundleError(f"edge babel failed: {(text or proc.stderr.decode('utf-8', 'replace'))[-400:]}")
        return json.loads(base64.b64decode(text[3:]).decode("utf-8"))


# ── Build ─────────────────────────────────────────────────────────────────

def _header(meta: Dict[str, Any]) -> str:
    return "/* @ds-bundle: " + json.dumps(meta, separators=(",", ":")).replace("*/", "*\\/") + " */\n"


def _namespace_tail(ns: str, names: List[str]) -> str:
    picks = ", ".join(f"{json.dumps(n)}" for n in names)
    return (f"\n;(function(){{var ns = window[{json.dumps(ns)}] = window[{json.dumps(ns)}] || {{}};\n"
            f"  [{picks}].forEach(function(n){{ if (window[n] !== undefined) ns[n] = window[n]; }});\n"
            f"  ns.__dsBundle = {{system: {json.dumps(ns)}, components: Object.keys(ns)}};\n}})();\n")


def build_sync(system_dir: Path, slug: str, *, babel: Optional[Path], compiler: str = "auto",
               system_id: Optional[str] = None) -> Dict[str, Any]:
    """Compile and write `bundle.js`. `babel=None` forces the in-browser fallback."""
    sources = component_sources(system_dir)
    if not sources:
        raise BundleError("no components/**/*.jsx to bundle")
    texts = {rel: data.decode("utf-8", "replace") for rel, data in sources}
    ns = namespace_for(system_dir, slug)
    names = export_names(system_dir, sources)
    man = _manifest(system_dir)
    compiled: Optional[Dict[str, Dict[str, str]]] = None
    used = "in-browser"
    errors: List[str] = []
    order = {"auto": ("node", "edge"), "node": ("node",), "edge": ("edge",), "browser": ()}.get(compiler, ("node", "edge"))
    if babel is not None:
        for which in order:
            try:
                if which == "node":
                    node = find_node()
                    if not node:
                        errors.append("node: not found")
                        continue
                    compiled = _compile_node(node, babel, texts)
                elif which == "edge":
                    edge = find_edge()
                    if not edge:
                        errors.append("edge: not found")
                        continue
                    compiled = _compile_edge(edge, babel, texts)
                used = f"babel-standalone@{BABEL_VERSION}/{which}"
                break
            except (BundleError, OSError, subprocess.SubprocessError, ValueError) as exc:
                errors.append(f"{which}: {exc}")
                compiled = None
    per_file_errors = {p: r["error"] for p, r in (compiled or {}).items() if r.get("error")}
    meta = {
        "schema": "teledesign-ds-bundle/v1",
        "system": slug,
        "system_id": system_id,
        "namespace": ns,
        "version": man.get("version"),
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "compiler": used,
        "react": (man.get("runtime") or {}).get("react", "18.3.1"),
        "requires": ["React", "ReactDOM"] + (["Babel"] if compiled is None else []),
        "exports": names,
        "sources": {rel: hash12(data) for rel, data in sources},
    }
    parts = [_header(meta),
             f"/* TeleDesign design-system bundle for {slug}. Generated — do not edit; rebuild with "
             f"POST /api/design/systems/<id>/bundle. Load after React 18 UMD"
             f"{' and @babel/standalone' if compiled is None else ''}. */\n"]
    if compiled is not None:
        for rel, _ in sources:
            r = compiled.get(rel) or {}
            if r.get("error"):
                msg = json.dumps(f"[ds-bundle] {rel} failed to compile: {r['error'][:300]}")
                parts.append(f"\n// --- {rel} (compile error)\nconsole.error({msg});\n")
                continue
            parts.append(f"\n// --- {rel}\n;(function(){{\ntry {{\n{r.get('code', '')}\n}} catch (e) {{ "
                         f"console.error({json.dumps('[ds-bundle] ' + rel)}, e); }}\n}}).call(window);\n")
    else:
        src_json = json.dumps(texts).replace("</", "<\\/")
        parts.append(
            "\n;(function(){\n"
            f"  var S = {src_json};\n"
            "  if (!window.Babel || !window.Babel.transform) { console.error('[ds-bundle] this bundle was built "
            "without a compiler and needs @babel/standalone loaded before it'); return; }\n"
            "  Object.keys(S).forEach(function(p){ try { (0, eval)(window.Babel.transform(S[p], "
            "{presets: [['react', {runtime: 'classic'}]], filename: p, sourceType: 'script'}).code); } "
            "catch (e) { console.error('[ds-bundle] ' + p, e); } });\n"
            "})();\n")
    parts.append(_namespace_tail(ns, names))
    out = system_dir / BUNDLE_NAME
    tmp = out.with_suffix(".js.tmp")
    tmp.write_text("".join(parts), encoding="utf-8")
    os.replace(tmp, out)
    if compiled is None:
        log.warning("ds_bundle: %s built with the in-browser fallback (%s)", slug, "; ".join(errors) or "forced")
    return {"ok": True, "path": BUNDLE_NAME, "compiler": used, "namespace": ns, "exports": names,
            "sources": len(sources), "bytes": out.stat().st_size, "compile_errors": per_file_errors,
            "fallback_reasons": errors if compiled is None else []}


async def build(system_dir: Path, slug: str, *, system_id: Optional[str] = None) -> Dict[str, Any]:
    """Async entry: fetch Babel if needed, compile off the event loop."""
    compiler = str(config.get_nested("design.bundle_compiler", "auto") or "auto")
    babel: Optional[Path] = None
    if compiler != "browser":
        try:
            babel = await ensure_babel()
        except Exception as exc:  # offline: fall back to in-browser compile
            log.warning("ds_bundle: cannot get @babel/standalone (%s) — in-browser fallback", exc)
    return await asyncio.to_thread(build_sync, system_dir, slug, babel=babel, compiler=compiler,
                                   system_id=system_id)


def ensure_fresh_sync(system_dir: Path, slug: str, system_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Rebuild when stale, synchronously, using only a cached Babel (never downloads).

    Called from `systems.stage()` so a staged `_ds/` never carries a stale bundle. Without a cached
    Babel it writes the in-browser fallback rather than blocking a turn on the network.
    """
    st = status(system_dir)
    babel = _cache_dir() / f"babel-standalone-{BABEL_VERSION}.min.js"
    with _babel_lock:
        ok = babel.exists() and _sri_ok(babel.read_bytes())
    # A fallback bundle is upgraded as soon as a compiler can run, even with unchanged sources.
    upgrade = ok and st.get("compiler") == "in-browser" and \
        str(config.get_nested("design.bundle_compiler", "auto") or "auto") != "browser"
    if not st["stale"] and not upgrade:
        return None
    try:
        return build_sync(system_dir, slug, babel=babel if ok else None,
                          compiler=str(config.get_nested("design.bundle_compiler", "auto") or "auto"),
                          system_id=system_id)
    except BundleError as exc:
        log.info("ds_bundle: %s not rebuilt: %s", slug, exc)
        return None
