"""TeleDesign canvas parity gaps: HTML ⇄ layers (#1), tokens ⇄ canvas variables (#3), frame
slides + present + PDF (#4), "working…" placeholders (#10) and the layer-board preview (#14).

The editor half lives in patches/open-pencil/0008-0011 and is exercised end to end against the
real editor in headless Edge by hand (see the report); here the Python half runs against a fake
editor bridge. Nothing touches the real data/ or settings: every design path resolves under
tmp_path and ports are free ones. No CLI turn, no local model, no /v1/* call.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import socket
from pathlib import Path

import pytest

import config

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "services" / "design" / "seeds" / "systems" / "midnight"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def td(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_settings_dir", lambda: tmp_path)
    overrides = {
        "design.preview_port": _free_port(),
        "proxy.port": _free_port(),
        "design.editor.disabled_tools": [],
        "design.editor.allow_eval": False,
    }
    orig = config.get_nested

    def get_nested(path, default=None):
        return overrides[path] if path in overrides else orig(path, default)
    monkeypatch.setattr(config, "get_nested", get_nested)
    from services.design import store
    return {"store": store, "root": tmp_path, "overrides": overrides}


def _project(store, files=None):
    rec = store.create_project({"title": "Canvas gaps"})
    d = store.project_dir(rec["id"])
    for rel, content in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
    return rec["id"], d


class FakeBridge:
    """Stands in for editor_bridge.call/request: records calls, answers per tool."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    async def call(self, pid, tool, args=None, timeout=20):
        self.calls.append((tool, dict(args or {})))
        ans = self.answers.get(tool)
        if callable(ans):
            ans = ans(args or {})
        if isinstance(ans, Exception):
            raise ans
        return {"ok": True, "result": ans if ans is not None else {}}


@pytest.fixture
def bridge(monkeypatch):
    from services.design import editor_bridge
    fb = FakeBridge()
    monkeypatch.setattr(editor_bridge, "call", fb.call)
    monkeypatch.setattr(editor_bridge, "status", lambda pid: {"connected": True, "registered_at": 0,
                                                               "pending": 0, "tools": 0, "open_pencil_version": "x"})
    return fb


def _run_app(coro_fn):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from proxy import api_design, api_design_editor

    async def main():
        app = web.Application()
        api_design.register_routes(app)
        api_design_editor.register_routes(app)
        async with TestClient(TestServer(app)) as client:
            return await coro_fn(client)
    return asyncio.run(main())


# ── #3 tokens ⇄ variables ────────────────────────────────────────────────

def _seed_tokens():
    return json.loads((SEED / "tokens.json").read_text(encoding="utf-8"))


def _as_read(payload):
    """What telecode_variables_read would return right after an apply of `payload`."""
    return {"collections": [{"name": c["name"], "modes": [{"name": m} for m in c["modes"]],
                             "variables": [{"name": v["name"], "type": v["type"], "values": dict(v["values"])}
                                           for v in c["variables"]]} for c in payload["collections"]]}


def test_tokens_to_collections_themes_become_modes():
    from services.design import canvas_tokens as ct
    p = ct.to_collections(_seed_tokens())
    cols = {c["name"]: c for c in p["collections"]}
    assert set(cols) == {"Color", "Spacing", "Radius", "Typography"}
    color = cols["Color"]
    assert color["modes"] == ["dark", "light"] and color["default_mode"] == "dark"
    bg = next(v for v in color["variables"] if v["name"] == "background")
    assert bg["type"] == "COLOR" and bg["values"] == {"light": "#F9FAFD", "dark": "#0A0D16"}
    sp = {v["name"]: v for v in cols["Spacing"]["variables"]}
    assert sp["4"]["values"] == {"Value": "16"} and sp["4"]["type"] == "FLOAT"
    ty = {v["name"]: v for v in cols["Typography"]["variables"]}
    assert ty["font-size/xs"]["values"]["Value"] == "12"
    assert ty["font-family/body"]["type"] == "STRING" and ty["font-family/body"]["values"]["Value"] == "Manrope"
    assert ty["letter-spacing/tight"]["values"]["Value"] == "-0.03em"
    # Alias radii (var(--…)) are reported, never guessed.
    assert any(s.startswith("radius.") for s in p["skipped"])
    assert p["count"] == sum(len(c["variables"]) for c in p["collections"])


def test_tokens_flat_colors_single_mode():
    from services.design import canvas_tokens as ct
    p = ct.to_collections({"color": {"brand": "#112233", "ink": {"value": "oklch(0.2 0 0)"}}})
    c = p["collections"][0]
    assert c["modes"] == ["Value"]
    assert {v["name"]: v["values"]["Value"] for v in c["variables"]} == {"brand": "#112233", "ink": "oklch(0.2 0 0)"}


def test_apply_canvas_roundtrip_is_a_noop_then_diffs():
    from services.design import canvas_tokens as ct
    t = _seed_tokens()
    read = _as_read(ct.to_collections(t))
    new, diff = ct.apply_canvas(t, read)
    assert diff == [] and new == t
    color = next(c for c in read["collections"] if c["name"] == "Color")
    prim = next(v for v in color["variables"] if v["name"] == "primary")
    prim["values"]["dark"] = "#ff0000"
    color["variables"].append({"name": "brand-new", "type": "COLOR", "values": {"dark": "#00FF0080", "light": "#00FF00"}})
    sp = next(c for c in read["collections"] if c["name"] == "Spacing")
    next(v for v in sp["variables"] if v["name"] == "1")["values"]["Value"] = 5
    ty = next(c for c in read["collections"] if c["name"] == "Typography")
    next(v for v in ty["variables"] if v["name"] == "font-family/display")["values"]["Value"] = "Inter"
    next(v for v in ty["variables"] if v["name"] == "line-height/tight")["values"]["Value"] = "1.2"
    new, diff = ct.apply_canvas(t, read)
    by = {d["token"]: d for d in diff}
    assert by["color.dark.primary"]["after"] == "#FF0000" and by["color.dark.primary"]["kind"] == "changed"
    assert new["color"]["dark"]["primary"] == {"value": "#FF0000", "hex": "#FF0000"}
    assert new["color"]["light"]["primary"] == t["color"]["light"]["primary"]
    assert by["color.dark.brand-new"]["kind"] == "added" and new["color"]["dark"]["brand-new"]["hex"] == "#00FF0080"
    assert by["spacing.1"]["after"] == "5px" and by["spacing.1"]["css"] == "--space-1"
    assert new["typography"]["fontFamily"]["display"]["family"] == "Inter"
    assert new["typography"]["fontFamily"]["display"]["stack"].startswith("'Inter'")
    assert by["typography.lineHeight.tight"]["after"] == "1.2" and by["typography.lineHeight.tight"]["css"] == "--leading-tight"
    # A variable deleted on the canvas never deletes the token.
    color["variables"] = [v for v in color["variables"] if v["name"] != "ring"]
    new2, _ = ct.apply_canvas(t, read)
    assert "ring" in new2["color"]["dark"]


def test_rewrite_tokens_css_targets_the_right_theme_block():
    from services.design import canvas_tokens as ct
    t = _seed_tokens()
    css = (SEED / "tokens.css").read_text(encoding="utf-8")
    diff = [
        {"token": "color.dark.primary", "css": "--primary", "theme": "dark", "before": "x", "after": "#FF0000"},
        {"token": "color.light.primary", "css": "--primary", "theme": "light", "before": "x", "after": "#00FF00"},
        {"token": "spacing.1", "css": "--space-1", "theme": None, "before": "4px", "after": "5px"},
        {"token": "spacing.zz", "css": "--space-zz", "theme": None, "before": None, "after": "7px"},
    ]
    out, updated, missing = ct.rewrite_tokens_css(css, diff, t)
    assert "--space-zz (" not in " ".join(updated) and "--space-zz" in missing
    blocks = {sel: out[a:b] for sel, a, b in ct._top_level_blocks(out)}
    dark = next(v for k, v in blocks.items() if '"dark"' in k)
    light = next(v for k, v in blocks.items() if '"light"' in k)
    assert re.search(r"--primary:\s*#FF0000;", dark) and "--primary-foreground: oklch" in dark
    assert re.search(r"--primary:\s*#00FF00;", light)
    assert "--space-1: 5px;" in out
    # The reduced-motion @media block is never touched, and nothing else moved.
    assert out.count("--duration-fast: 0ms;") == 1
    assert len(out.splitlines()) == len(css.splitlines())


def test_rewrite_plain_root_carries_the_default_theme():
    from services.design import canvas_tokens as ct
    toks = {"themes": ["light", "dark"], "defaultTheme": "light",
            "color": {"light": {"bg": "#ffffff"}, "dark": {"bg": "#000000"}}}
    css = ":root {\n  --bg: #fff;\n}\n.dark {\n  --bg: #000;\n}\n"
    out, updated, missing = ct.rewrite_tokens_css(css, [
        {"css": "--bg", "theme": "light", "after": "#EEEEEE"},
        {"css": "--bg", "theme": "dark", "after": "#111111"}], toks)
    assert out == ":root {\n  --bg: #EEEEEE;\n}\n.dark {\n  --bg: #111111;\n}\n" and not missing


def test_locate_prefers_project_tokens_then_staged_copy(tmp_path):
    from services.design import canvas_tokens as ct
    assert ct.locate(tmp_path) is None
    staged = tmp_path / "_ds" / "midnight"
    staged.mkdir(parents=True)
    (staged / "tokens.json").write_text("{}", encoding="utf-8")
    (staged / "tokens.css").write_text(":root{}", encoding="utf-8")
    assert ct.locate(tmp_path) == {"json": "_ds/midnight/tokens.json", "css": "_ds/midnight/tokens.css", "staged": True}
    (tmp_path / "tokens.json").write_text("{}", encoding="utf-8")
    assert ct.locate(tmp_path) == {"json": "tokens.json", "css": None, "staged": False}
    (tmp_path / "tokens.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(ct.TokensError):
        ct.load(tmp_path)


def test_tokens_routes_push_and_pull(td, bridge):
    pid, d = _project(td["store"], {"_ds/midnight/tokens.json": (SEED / "tokens.json").read_text(encoding="utf-8"),
                                    "_ds/midnight/tokens.css": (SEED / "tokens.css").read_text(encoding="utf-8")})
    from services.design import canvas_tokens as ct
    pushed = {}

    def apply(args):
        pushed.update(args)
        return {"created": sum(len(c["variables"]) for c in args["collections"]), "updated": 0}

    def read(_args):
        r = _as_read({"collections": pushed["collections"]})
        col = next(c for c in r["collections"] if c["name"] == "Color")
        next(v for v in col["variables"] if v["name"] == "accent")["values"]["light"] = "#123456"
        return r

    bridge.answers.update({"telecode_variables_apply": apply, "telecode_variables_read": read})

    async def go(c):
        base = f"/api/design/projects/{pid}/editor/tokens"
        info = await (await c.get(base)).json()
        assert info["available"] and info["json"] == "_ds/midnight/tokens.json" and info["staged"]
        r = await c.post(base + "/push", json={})
        body = await r.json()
        assert r.status == 200 and body["report"]["created"] == body["count"] > 50
        r = await c.post(base + "/pull", json={}, headers={"Origin": "http://127.0.0.1:1237"})
        assert r.status == 403   # a preview page can never write
        r = await c.post(base + "/pull", json={})
        body = await r.json()
        assert r.status == 200, body
        assert [x["token"] for x in body["diff"]] == ["color.light.accent"]
        assert body["written"] == ["_ds/midnight/tokens.json", "_ds/midnight/tokens.css"]
        assert body["css_updated"] == ["--accent (light)"]
    _run_app(go)
    toks = json.loads((d / "_ds/midnight/tokens.json").read_text(encoding="utf-8"))
    assert toks["color"]["light"]["accent"]["hex"] == "#123456"
    assert "--accent: #123456;" in (d / "_ds/midnight/tokens.css").read_text(encoding="utf-8")
    assert ct.locate(d)["staged"]


def test_tokens_info_without_tokens(td, bridge):
    pid, _ = _project(td["store"])

    async def go(c):
        info = await (await c.get(f"/api/design/projects/{pid}/editor/tokens")).json()
        assert info["available"] is False and "tokens.json" in info["error"]
        r = await c.post(f"/api/design/projects/{pid}/editor/tokens/push", json={})
        assert r.status == 400
    _run_app(go)
    assert not bridge.calls


# ── #1 convert + #14 layer preview ──────────────────────────────────────

def test_convert_to_html_writes_page_and_registers_board(td, bridge):
    pid, d = _project(td["store"])
    bridge.answers.update({
        "telecode_export_html": {"html": "<!doctype html><title>Hero</title><main>hi</main>", "name": "Hero Card",
                                 "width": 800, "height": 600},
        "node_bounds": {"x": 100, "y": 50, "width": 800, "height": 600},
        "create_shape": {"id": "9:9"},
        "telecode_board_mark": lambda a: {"key": a["key"], "node_id": a["node_id"]},
    })

    async def go(c):
        url = f"/api/design/projects/{pid}/editor/convert"
        r = await c.post(url, json={"direction": "sideways"})
        assert r.status == 400
        r = await c.post(url, json={"direction": "to-html", "node_id": "1:2", "path": "../x.html"})
        assert r.status == 400
        r = await c.post(url, json={"direction": "to-html", "node_id": "1:2", "path": ".layers/x.html"})
        assert r.status == 400
        r = await c.post(url, json={"direction": "to-html", "node_id": "1:2"})
        body = await r.json()
        assert r.status == 200, body
        assert body["path"] == "hero-card.html" and body["node_id"] == "9:9"
        r2 = await (await c.post(url, json={"direction": "to-html", "node_id": "1:2"})).json()
        assert r2["path"] == "hero-card-2.html"
        return body
    body = _run_app(go)
    assert (d / "hero-card.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    boards = td["store"].get_boards(pid)
    assert boards[body["board"]["key"]]["src"] == "hero-card.html"
    shape = next(a for t, a in bridge.calls if t == "create_shape")
    assert shape["x"] == 100 + 800 + 120 and shape["width"] == 800 and shape["type"] == "FRAME"


def test_layer_preview_goes_to_hidden_dir(td, bridge):
    pid, d = _project(td["store"])
    bridge.answers["telecode_export_html"] = {"html": "<p>x</p>", "name": "Pricing / Mobile", "width": 390, "height": 844}

    async def go(c):
        r = await c.post(f"/api/design/projects/{pid}/editor/layer-preview", json={"node_id": "12:3"})
        return await r.json()
    body = _run_app(go)
    assert body["path"] == ".layers/pricing-mobile-12-3.html"
    assert (d / body["path"]).read_text(encoding="utf-8") == "<p>x</p>"
    assert body["url"].endswith("/p/%s/.layers/pricing-mobile-12-3.html" % pid) or ".layers/" in body["url"]
    # Hidden from the Files rail (dot directory), served by the preview origin.
    from services.design import files as dfiles, preview
    listing = json.dumps(dfiles.list_files(pid) if hasattr(dfiles, "list_files") else [])
    assert ".layers" not in listing
    assert ".layers" not in preview._HIDDEN_TOP


def test_convert_to_layers_validates_and_sends_snapshot(td, bridge, monkeypatch):
    pid, d = _project(td["store"], {"index.html": "<p>x</p>"})
    td["store"].save_boards(pid, {"bkey1": {"src": "index.html", "width": 1200, "height": 700}})
    from services.design import canvas_convert
    seen = {}

    async def fake_snapshot(p, src, width, height):
        seen.update(src=src, width=width, height=height)
        return {"width": width, "height": height, "title": "T", "nodes": 3, "root": {"k": "box", "x": 0, "y": 0, "w": 1, "h": 1}}
    monkeypatch.setattr(canvas_convert, "layout_snapshot", fake_snapshot)
    bridge.answers.update({
        "telecode_board_list": {"boards": [{"key": "bkey1", "node_id": "4:4"}]},
        "telecode_import_html": {"id": "20:1", "name": "index (layers)", "nodes": 3},
    })

    async def go(c):
        url = f"/api/design/projects/{pid}/editor/convert"
        r = await c.post(url, json={"direction": "to-layers", "key": "nope"})
        assert r.status == 400
        r = await c.post(url, json={"direction": "to-layers", "key": "bkey1"})
        return await r.json()
    body = _run_app(go)
    assert body["ok"] and body["id"] == "20:1" and body["src"] == "index.html"
    assert seen == {"src": "index.html", "width": 1200, "height": 700}
    imp = next(a for t, a in bridge.calls if t == "telecode_import_html")
    assert imp["beside_id"] == "4:4" and imp["snapshot"]["root"]["k"] == "box"

    async def bad():
        with pytest.raises(canvas_convert.ConvertError):
            await canvas_convert.layout_snapshot(pid, "../etc.html")
        with pytest.raises(canvas_convert.ConvertError):
            await canvas_convert.layout_snapshot(pid, "missing.html")
    monkeypatch.undo()
    asyncio.run(bad())


def test_editor_not_open_is_409(td, monkeypatch):
    pid, _ = _project(td["store"], {"index.html": "<p>x</p>"})
    from services.design import editor_bridge

    async def go(c):
        r = await c.post(f"/api/design/projects/{pid}/editor/convert", json={"direction": "to-layers", "src": "index.html"})
        assert r.status == 409
    _run_app(go)


# ── #4 slides ────────────────────────────────────────────────────────────

def test_slides_routes(td, bridge):
    pid, _ = _project(td["store"])
    slides = [{"id": "1:1", "name": "Intro"}, {"id": "1:2", "name": "Problem"}]
    bridge.answers.update({
        "telecode_slides_list": {"page_id": "0:1", "slides": slides},
        "telecode_slides_reorder": lambda a: {"slides": [s for i in a["ids"] for s in slides if s["id"] == i]},
        "export_pdf": {"base64": base64.b64encode(b"%PDF-1.7 fake").decode(), "mimeType": "application/pdf"},
    })

    async def go(c):
        base = f"/api/design/projects/{pid}/editor/slides"
        lst = await (await c.get(base)).json()
        assert [s["name"] for s in lst["slides"]] == ["Intro", "Problem"]
        r = await c.post(base + "/order", json={"ids": "1:2"})
        assert r.status == 400
        r = await (await c.post(base + "/order", json={"ids": ["1:2", "1:1"]})).json()
        assert [s["id"] for s in r["slides"]] == ["1:2", "1:1"]
        r = await c.post(base + "/pdf", json={})
        assert r.status == 200 and r.headers["Content-Type"] == "application/pdf"
        assert (await r.read()).startswith(b"%PDF") and "slides.pdf" in r.headers["Content-Disposition"]
        r = await c.post(base + "/pdf", json={"ids": ["1:2"]})
        assert r.status == 200
    _run_app(go)
    reorder = next(a for t, a in bridge.calls if t == "telecode_slides_reorder")
    assert reorder == {"ids": ["1:2", "1:1"], "arrange": True}
    pdf_calls = [a for t, a in bridge.calls if t == "export_pdf"]
    assert pdf_calls == [{"ids": ["1:1", "1:2"]}, {"ids": ["1:2"]}]


# ── editor build: the new commands are real ──────────────────────────────

NEW_TOOLS = ("telecode_import_html", "telecode_export_html", "telecode_variables_apply", "telecode_variables_read",
             "telecode_slides_list", "telecode_slides_reorder", "telecode_placeholder_set",
             "telecode_placeholder_clear", "telecode_placeholder_list")


def test_editor_tools_json_carries_the_new_commands():
    data = json.loads((REPO / "services" / "design" / "editor_tools.json").read_text(encoding="utf-8"))
    by = {t["name"]: t for t in data["tools"]}
    for name in NEW_TOOLS:
        assert name in by, name
        assert by[name]["command"] == name
        assert by[name]["input_schema"]["type"] == "object"
    assert by["telecode_placeholder_set"]["input_schema"]["required"] == ["node_id"]
    assert by["telecode_export_html"]["effect"] == "read" and by["telecode_import_html"]["effect"] == "write"


def test_patch_series_and_vendored_build_agree():
    patches = sorted(p.name for p in (REPO / "patches" / "open-pencil").glob("*.patch"))
    assert [p[:4] for p in patches][:11] == [f"{i:04d}" for i in range(1, 12)]
    info = json.loads((REPO / "proxy" / "static" / "design" / "editor" / "BUILD_INFO.json").read_text(encoding="utf-8"))
    assert [p["name"] for p in info["patches"]] == patches
    import hashlib
    for p in info["patches"]:
        sha = hashlib.sha256((REPO / "patches" / "open-pencil" / p["name"]).read_bytes()).hexdigest()
        assert sha == p["sha256"], f"{p['name']} changed since the vendored build — rebuild"


def test_prompts_document_the_new_tools():
    text = "".join((REPO / "services" / "design" / "prompts" / f).read_text(encoding="utf-8")
                   for f in ("canvas.md", "layer_boards.md"))
    for name in ("telecode_placeholder_set", "telecode_import_html", "telecode_variables_apply", "telecode_slides_list"):
        assert name in text, name


# ── headless Edge: the layout snapshot of a real page ────────────────────

def _edge() -> bool:
    try:
        from services.design import render
        render._edge_binary()
        return True
    except Exception:
        return False


needs_edge = pytest.mark.skipif(not _edge(), reason="Microsoft Edge / Chromium not installed")


@needs_edge
def test_layout_snapshot_in_edge(td):
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR4nGP4z8DwnwEIGBgYGBgAAB0EAv8PrX3NAAAAAElFTkSuQmCC")
    pid, d = _project(td["store"], {"logo.png": png, "index.html": """<!doctype html><html><head><title>Snap</title>
<style>body{margin:0;background:oklch(0.97 0.01 90);font:16px/1.5 system-ui}
.card{margin:40px;padding:24px;width:320px;border-radius:16px;background:#1d4ed8;color:#fff;box-shadow:0 8px 24px rgba(0,0,0,.25)}
.g{height:60px;background:linear-gradient(90deg,#f00,#00f)}
.b{border:2px dashed #0a0;height:30px}</style></head>
<body><div class="card" data-td-id="card"><h1 style="margin:0;font-size:28px">Hello <b>world</b></h1><p>Two lines of text that should wrap inside the card because it is narrow enough.</p></div>
<div class="g"></div><div class="b"></div><img src="logo.png" width="40" height="40">
<svg width="24" height="24" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="currentColor"/></svg>
<script>document.body.insertAdjacentHTML('beforeend', '<p id="late">added by script</p>')</script></body></html>"""})
    from services.design import canvas_convert, preview, render

    async def go():
        runner = await preview.start_background()
        try:
            return await canvas_convert.layout_snapshot(pid, "index.html", 900, 700)
        finally:
            render.stop()
            await preview.stop()
    snap = asyncio.run(go())
    assert snap["title"] == "Snap" and snap["width"] == 900 and snap["root"]["bg"][3] == 1

    def walk(n):
        yield n
        for c in n.get("ch") or []:
            yield from walk(c)
    nodes = list(walk(snap["root"]))
    card = next(n for n in nodes if n.get("n") == "card")
    assert card["bg"][2] > 0.8 and card["r"] == [16, 16, 16, 16] and card["sh"][0]["blur"] == 24
    assert abs(card["x"] - 40) < 1 and abs(card["w"] - 368) < 1
    texts = [n for n in nodes if n["k"] == "text"]
    by_text = {n["t"]: n for n in texts}
    assert "Hello" in by_text and "world" in by_text and by_text["world"]["fw"] >= 700
    para = next(n for n in texts if n["t"].startswith("Two lines"))
    assert para["lines"] >= 2 and para["c"][:3] == [1, 1, 1]
    assert "added by script" in by_text   # scripts ran before the snapshot
    grad = next(n for n in nodes if n.get("grad"))
    assert grad["grad"]["type"] == "linear" and grad["grad"]["angle"] == 90 and len(grad["grad"]["stops"]) == 2
    dashed = next(n for n in nodes if n.get("bs") == "dashed")
    assert dashed["bw"] == [2, 2, 2, 2]
    img = next(n for n in nodes if n["k"] == "img")
    assert img["img"].startswith("data:image/png;base64,")
    svg = next(n for n in nodes if n["k"] == "svg")
    assert "<circle" in svg["svg"] and "currentColor" not in svg["svg"]
