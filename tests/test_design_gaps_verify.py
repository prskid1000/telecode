"""TeleDesign parity gaps: verifier (layer boards, directed checks, contrast), live console relay,
copy/move file ops, the guarded design browser, MCP registration for more clients, and the
headless CLI.

Nothing here touches the real data/ or settings: every design path resolves under tmp_path,
ports are overridden to free ones, MCP registration only runs against a fake HOME, and the
headless-Edge tests use a throwaway profile (skipped when Edge is not installed). No CLI design
turn, no local model, no /v1/* call.
"""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import socket
import threading
from contextlib import redirect_stdout
from pathlib import Path

import pytest

import config


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def td(tmp_path, monkeypatch):
    """Design store under tmp_path + fixed settings for the keys these features read."""
    monkeypatch.setattr(config, "_settings_dir", lambda: tmp_path)
    overrides = {
        "design.preview_port": _free_port(),
        "proxy.port": _free_port(),
        "mcp_server.port": _free_port(),
        "design.local_helpers": False,
        "design.verifier.layer_boards": True,
        "design.mcp_name": "telecode",
        "design.editor.disabled_tools": [],
        "design.editor.allow_eval": False,
    }
    orig = config.get_nested

    def get_nested(path, default=None):
        return overrides[path] if path in overrides else orig(path, default)
    monkeypatch.setattr(config, "get_nested", get_nested)
    from services.design import store
    return {"store": store, "overrides": overrides, "root": tmp_path}


def _project(store, files=None):
    rec = store.create_project({"title": "Gaps test"})
    d = store.project_dir(rec["id"])
    for rel, text in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return rec["id"], d


# ── #2 layer boards: open-pencil findings → verifier issues ──────────────

def test_layer_issue_mapping_is_conservative():
    from services.design import editor_bridge as eb
    page = {"id": "0:1", "name": "Home"}
    base = {"nodeA": {"id": "1:2", "name": "Title", "type": "TEXT", "x": 0, "y": 0, "width": 400, "height": 40},
            "nodeB": {"id": "1:1", "name": "Card", "type": "FRAME", "x": 0, "y": 0, "width": 200, "height": 200},
            "message": 'Text "Title" extends 200px outside parent "Card"', "suggestion": "Clip it"}
    crit = eb.layer_issue(page, {**base, "category": "parent-overflow", "severity": "critical"})
    assert crit["severity"] == "major" and crit["file"] == "docs/main.fig" and crit["board"] == "1:2"
    assert eb.layer_issue(page, {**base, "category": "parent-overflow", "severity": "critical"},
                          "docs/wires.fig")["file"] == "docs/wires.fig"
    assert crit["check"] == "layer_parent_overflow" and "Home" in crit["where"]
    assert eb.layer_issue(page, {**base, "category": "parent-overflow", "severity": "major"})["severity"] == "minor"
    assert eb.layer_issue(page, {**base, "category": "sibling-overlap", "severity": "major"})["severity"] == "minor"
    assert eb.layer_issue(page, {**base, "category": "sibling-overlap", "severity": "minor"}) is None
    assert eb.layer_issue(page, {**base, "category": "overlay", "severity": "critical"}) is None


def test_inspect_layers_skips_without_editor(td):
    from services.design import editor_bridge as eb
    pid, _ = _project(td["store"])
    rep = asyncio.run(eb.inspect_layers(pid))
    assert rep["ran"] is False and "canvas editor" in rep["skipped"]


def test_inspect_layers_with_live_editor(td, monkeypatch, tmp_path):
    from services.design import editor_bridge as eb
    import base64
    pid, _ = _project(td["store"])
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
    calls = []

    async def fake_call(p, tool, args=None, timeout=20):
        calls.append((tool, dict(args or {})))
        if tool == "list_pages":
            return {"ok": True, "result": {"current": "Home", "pages": [{"id": "0:1", "name": "Home"}]}}
        if tool == "analyze_overlaps":
            assert args["page_id"] == "0:1" and args["severity"] == "major"
            return {"ok": True, "result": {"overlaps": [
                {"category": "parent-overflow", "severity": "critical", "message": "Text extends 90px outside",
                 "suggestion": "clip", "nodeA": {"id": "2:1", "name": "T", "type": "TEXT"},
                 "nodeB": {"id": "2:0", "name": "F", "type": "FRAME"}, "area": 900, "ratio": 0.5}]}}
        if tool == "analyze_typography":
            return {"ok": True, "result": {"groups": [{"size": 10, "count": 3}, {"size": 16, "count": 9}]}}
        if tool == "get_page_tree":
            return {"ok": True, "result": {"page": "Home", "children": [{"id": "2:0", "type": "FRAME", "name": "F"}]}}
        if tool == "export_image":
            return {"ok": True, "result": {"base64": png, "mimeType": "image/png"}}
        raise AssertionError(tool)
    monkeypatch.setattr(eb, "status", lambda p: {"connected": True})
    monkeypatch.setattr(eb, "call", fake_call)
    rep = asyncio.run(eb.inspect_layers(pid, shots_dir=tmp_path / "shots"))
    assert rep["ran"] and not rep["skipped"]
    sev = sorted(i["severity"] for i in rep["issues"])
    assert sev == ["major", "minor"]                      # overflow + 3 tiny text nodes
    assert "Text extends 90px outside" in rep["text"] and "under 12px" in rep["text"]
    assert len(rep["screenshots"]) == 1 and Path(rep["screenshots"][0]).read_bytes().startswith(b"\x89PNG")


def test_verify_carries_pen_problems(td, monkeypatch):
    from services.design import editor_bridge as eb, render
    pid, d = _project(td["store"])
    (d / "doc.fig").write_bytes(b"fig")

    async def fake_inspect(p, shots_dir=None, **kw):
        return {"ran": True, "skipped": None, "issues": [{"severity": "major", "file": "doc.fig", "what": "clipped",
                                                          "where": "x", "check": "layer_parent_overflow"}],
                "problems": [{"m": 1}], "text": "- [critical] clipped", "screenshots": []}
    monkeypatch.setattr(eb, "inspect_layers", fake_inspect)
    res = asyncio.run(render.verify(pid, [], screenshots=False))
    assert res["status"] == "issues" and res["pen_problems"] == "- [critical] clipped"
    assert res["layers"]["ran"] is True
    res = asyncio.run(render.verify(pid, [], screenshots=False, layers=False))
    assert res["status"] == "pass" and res["layers"]["skipped"] == "disabled"


# ── #5 directed verifier ─────────────────────────────────────────────────

def test_directed_check_always_reports(td, monkeypatch):
    from services.design import render, verifier
    pid, _ = _project(td["store"], {"index.html": "<h1>x</h1>"})
    seen = {}

    async def fake_verify(p, files, screenshots=True, layers=None):
        seen["files"] = files
        return {"status": "pass", "issues": [], "screenshots": [], "console": {"index.html": []}, "decks": {},
                "pen_problems": "", "layers": {"ran": False, "skipped": "x"}}
    monkeypatch.setattr(render, "verify", fake_verify)
    events = []
    import services.design.events as ev
    monkeypatch.setattr(ev, "publish", lambda p, t, d=None: events.append((t, d)))
    tid = "a" * 32
    rep = asyncio.run(verifier.run_check(pid, None, "Does the H1 read 'x'?", turn_id=tid))
    assert seen["files"] == ["index.html"]
    assert rep["mode"] == "directed" and rep["status"] == "pass" and rep["model"]["ran"] is False
    assert "note" in rep                                   # the caller judges the task from the evidence
    assert [e[1]["status"] for e in events] == ["running", "pass"]
    assert all(e[1]["directed"] and e[1]["turn_id"] == tid for e in events)
    sweep = asyncio.run(verifier.run_check(pid, ["index.html"], ""))
    assert sweep["mode"] == "sweep" and "note" not in sweep


def test_model_result_parsing():
    from services.design import verifier
    out = verifier._parse_model('<verifier-result status="fail">[{"severity":"info","what":"no"},'
                                '{"severity":"major","file":"a.html","what":"clipped"}]</verifier-result>')
    assert out["status"] == "fail" and len(out["items"]) == 2
    assert verifier._parse_model('<verifier-result status="pass"/>') == {"status": "pass", "items": []}
    assert verifier._parse_model("nothing") is None


def test_contrast_issue_severity():
    from services.design.render import contrast_issue
    assert contrast_issue("a.html", []) is None
    items = [{"where": "p", "ratio": 3.9, "need": 4.5, "size": 16, "fg": "#777777", "bg": "#ffffff", "text": "x"}]
    assert contrast_issue("a.html", items)["severity"] == "minor"
    items.append({"where": "h2", "ratio": 1.8, "need": 3, "size": 30, "fg": "#dddddd", "bg": "#ffffff",
                  "text": "y", "slide": 2})
    iss = contrast_issue("a.html", items)
    assert iss["severity"] == "major" and iss["check"] == "contrast" and "1.8:1" in iss["what"]
    assert "slides [2]" in iss["what"]


# ── #6 live console relay + #12 file ops (REST) ──────────────────────────

def _run_app(td, coro_fn):
    """Run `coro_fn(client)` against an aiohttp app carrying every design route."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from proxy import api_design, api_design_agents, api_design_editor, api_design_export, api_design_systems

    async def main():
        app = web.Application()
        api_design.register_routes(app)
        api_design_export.register_routes(app)
        api_design_systems.register_routes(app)
        api_design_editor.register_routes(app)
        api_design_agents.register_routes(app)
        async with TestClient(TestServer(app)) as client:
            return await coro_fn(client)
    return asyncio.run(main())


def test_live_console_relay(td):
    pid, _ = _project(td["store"], {"index.html": "<p>x</p>"})
    from proxy import api_design_agents as ag
    ag._LIVE_CONSOLE.pop(pid, None)

    async def go(c):
        base = f"/api/design/projects/{pid}/console/live"
        r = await c.post(base, json={"file": "index.html", "entries": [
            {"level": "log", "args": ["hello", 1], "at": "t1"}, {"level": "error", "args": ["boom"]},
            {"level": "bogus", "text": "odd"}]})
        assert r.status == 200 and (await r.json())["added"] == 3
        # A foreign origin (a preview page) may never write.
        r = await c.post(base, json={"file": "index.html", "entries": []}, headers={"Origin": "http://evil"})
        assert r.status == 403
        r = await c.post(base, json={"file": "../x", "entries": []})
        assert r.status == 400
        all_ = await (await c.get(base, params={"file": "index.html"})).json()
        assert [e["text"] for e in all_["entries"]] == ["hello 1", "boom", "odd"]
        assert all_["entries"][2]["level"] == "log"
        errs = await (await c.get(base, params={"level": "error"})).json()
        assert [e["text"] for e in errs["entries"]] == ["boom"]
        newer = await (await c.get(base, params={"since": all_["entries"][1]["seq"]})).json()
        assert [e["text"] for e in newer["entries"]] == ["odd"]
        r = await c.post(base, json={"file": "index.html", "reset": True, "entries": [{"args": ["fresh"]}]})
        again = await (await c.get(base)).json()
        assert [e["text"] for e in again["entries"]] == ["fresh"]
    _run_app(td, go)


def test_file_ops_copy_and_move(td):
    store = td["store"]
    from services.design import assets as dassets
    pid, d = _project(store, {"checkout.html": "<h1>c</h1>", "screens/a.html": "a", "screens/b.css": "b"})
    dassets.put_assets(pid, [{"path": "checkout.html", "name": "Checkout", "group": "Screens",
                              "subtitle": "v1", "viewport": {"width": 390, "height": 844}, "status": "approved"}])
    store.save_boards(pid, {"bk1": {"src": "checkout.html", "width": 390, "height": 844}})

    async def go(c):
        ops = f"/api/design/projects/{pid}/file-ops"
        r = await c.post(ops, json={"op": "copy", "from": "checkout.html", "to": "checkout-v2.html"})
        body = await r.json()
        assert r.status == 200, body
        assert (d / "checkout-v2.html").read_text() == "<h1>c</h1>"
        items = {a["path"]: a for a in dassets.get_assets(pid)}
        cp = items["checkout-v2.html"]
        assert cp["name"] == "Checkout" and cp["subtitle"] == "v1" and cp["viewport"]["width"] == 390
        assert cp["status"] == "needs-review" and cp["id"] != items["checkout.html"]["id"]
        assert items["checkout.html"]["status"] == "approved"
        # Exists → 409 unless overwrite.
        r = await c.post(ops, json={"op": "copy", "from": "checkout.html", "to": "checkout-v2.html"})
        assert r.status == 409
        # Folder copy.
        r = await c.post(ops, json={"op": "copy", "from": "screens", "to": "screens-v2"})
        assert r.status == 200 and (d / "screens-v2" / "b.css").read_text() == "b"
        # Move carries id + status and repoints the HTML board.
        old_id = items["checkout.html"]["id"]
        r = await c.post(ops, json={"op": "move", "from": "checkout.html", "to": "flows/checkout.html"})
        body = await r.json()
        assert r.status == 200, body
        assert not (d / "checkout.html").exists() and (d / "flows" / "checkout.html").is_file()
        moved = next(a for a in dassets.get_assets(pid) if a["id"] == old_id)
        assert moved["path"] == "flows/checkout.html" and moved["status"] == "approved"
        assert store.get_boards(pid)["bk1"]["src"] == "flows/checkout.html" and body["boards"] == ["bk1"]
        # Guards: escape, read-only area, bad op.
        for bad in ({"op": "copy", "from": "../x", "to": "y"}, {"op": "move", "from": "flows/checkout.html",
                                                                 "to": ".versions/x.html"},
                    {"op": "zap", "from": "a", "to": "b"}):
            r = await c.post(ops, json=bad)
            assert r.status in (400, 404), bad
        r = await c.post(ops, json={"op": "copy", "from": "nope.html", "to": "b.html"})
        assert r.status == 404
    _run_app(td, go)


def test_verify_route_validates(td, monkeypatch):
    pid, _ = _project(td["store"], {"index.html": "<p>x</p>"})
    from services.design import verifier

    async def fake_run(p, files, task, **kw):
        return {"mode": "directed" if task else "sweep", "status": "pass", "files": files, "kw": kw}
    monkeypatch.setattr(verifier, "run_check", fake_run)

    async def go(c):
        url = f"/api/design/projects/{pid}/verify"
        r = await c.post(url, json={"task": "check nav", "files": ["index.html"], "layers": False})
        body = await r.json()
        assert r.status == 200 and body["mode"] == "directed" and body["kw"]["layers"] is False
        assert (await c.post(url, json={"files": ["../x.html"]})).status == 400
        assert (await c.post(url, json={"turn_id": "nothex"})).status == 400
        assert (await c.post(url, data="x", headers={"Content-Type": "text/plain"})).status == 415
    _run_app(td, go)


def test_browser_route_refuses_non_http(td):
    async def go(c):
        r = await c.post("/api/design/browser", json={"url": "file:///C:/Windows/win.ini"})
        assert r.status == 502 and "http" in (await r.json())["error"]
        r = await c.post("/api/design/browser", json={})
        assert r.status == 400
    _run_app(td, go)


# ── headless Edge: contrast + the guarded browser ────────────────────────

def _edge() -> bool:
    try:
        from services.design import render
        render._edge_binary()
        return True
    except Exception:
        return False


needs_edge = pytest.mark.skipif(not _edge(), reason="Microsoft Edge / Chromium not installed")


@needs_edge
def test_contrast_check_in_edge(td, tmp_path):
    from services.design import render
    page = tmp_path / "c.html"
    page.write_text("""<!doctype html><body style="background:#fff">
<p id=bad style="color:#bbb">grey on white</p>
<p id=good style="color:#222">dark on white</p>
<div style="background:#0a0a0a"><p id=dark style="color:#333">dark on near-black</p></div>
<div style="background:url(x.png) #000"><p id=img style="color:#111">over an image: unknown, skipped</p></div>
<h1 id=big style="color:#888;font-size:32px">large grey passes 3:1</h1>
<p id=ok style="color:oklch(0.9 0 0)">oklch light</p>
</body>""", encoding="utf-8")

    async def go():
        try:
            async with render.open_page(900, 700) as p:
                await p.goto(page.as_uri())
                await p.eval(render._CHECKS_JS, await_promise=False)
                return await p.eval("window.__tdChecks.page(12)", await_promise=False)
        finally:
            render.stop()
    res = asyncio.run(go())
    where = {c["where"] for c in res["contrast"]}
    assert "p#bad" in where and "p#dark" in where and "p#ok" in where
    assert "p#good" not in where and "h1#big" not in where and "p#img" not in where
    bad = next(c for c in res["contrast"] if c["where"] == "p#bad")
    assert bad["fg"] == "#bbbbbb" and bad["bg"] == "#ffffff" and 1.8 < bad["ratio"] < 2.0


@needs_edge
def test_browse_guard_blocks_and_serves(td, monkeypatch):
    from aiohttp import web
    from services.design import render
    port = [0]

    async def idx(req):
        return web.Response(content_type="text/html", text=(
            '<title>Ref</title><h1>Hello</h1><nav><a href="/x">Pricing</a></nav>'
            f'<img src="/i.png"><img src="http://localhost:{port[0]}/leak.png">'))

    async def img(req):
        return web.Response(body=b"\x89PNG\r\n\x1a\n", content_type="image/png")

    async def go():
        app = web.Application()
        app.router.add_get("/", idx)
        app.router.add_get("/i.png", img)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port[0] = site._server.sockets[0].getsockname()[1]
        try:
            # Unpatched guard: a loopback URL is refused before Edge sees a byte.
            with pytest.raises(render.RenderError):
                await render.browse(f"http://127.0.0.1:{port[0]}/")
            # Treat "127.0.0.1" as the one public host for this test; "localhost" stays refused.
            async def check(self, url):
                from urllib.parse import urlsplit
                if urlsplit(url).hostname != "127.0.0.1":
                    raise render.RenderError("refused")
            monkeypatch.setattr(render._GuardedFetcher, "check", check)
            return await render.browse(f"http://127.0.0.1:{port[0]}/")
        finally:
            await runner.cleanup()
            render.stop()
    res = asyncio.run(go())
    assert res["outline"]["title"] == "Ref" and res["outline"]["headings"][0]["text"] == "Hello"
    assert any(link["text"] == "Pricing" for link in res["outline"]["links"])
    assert [b["url"] for b in res["blocked"]] == [f"http://localhost:{port[0]}/leak.png"]
    assert res["screenshot"][:2] == b"\xff\xd8"          # JPEG


# ── #18 MCP registration for more clients ────────────────────────────────

@pytest.fixture
def fake_home(td, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    from services.design import mcp_registration as reg
    monkeypatch.setattr(reg, "_home", lambda: home)
    for var in ("LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(var, str(home / var.lower()))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    installed: set = set()
    real_which = shutil.which
    monkeypatch.setattr(reg.shutil, "which",
                        lambda b: str(home / "bin" / b) if b in installed else (real_which(b) if b == "npx" else None))
    return {"home": home, "installed": installed, "reg": reg}


def test_not_installed_clients_are_never_written(fake_home):
    reg, home = fake_home["reg"], fake_home["home"]
    (home / ".config" / "opencode").mkdir(parents=True)   # a leftover folder is not an install
    (home / ".gemini").mkdir()
    for client in ("opencode", "kiro", "gemini", "claude_desktop"):
        res = reg.register_sync(client, force=True)
        assert res["ok"] is False and "not installed" in res["error"], client
        assert reg.client_status(client)["available"] is False
    assert not (home / ".config" / "opencode" / "opencode.json").exists()
    assert not (home / ".kiro").exists()


def test_opencode_and_kiro_file_registration(fake_home):
    reg, home = fake_home["reg"], fake_home["home"]
    fake_home["installed"].update({"opencode", "kiro"})
    cfg = home / ".config" / "opencode" / "opencode.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"$schema": "x", "mcp": {"other": {"type": "local"}}, "theme": "dark"}))
    dry = reg.register_sync("opencode", dry_run=True)
    assert dry["ok"] and dry["writes"][0]["key"] == "mcp.telecode"
    assert "telecode" not in json.loads(cfg.read_text())["mcp"]      # dry run wrote nothing
    res = reg.register_sync("opencode")
    assert res["ok"], res
    data = json.loads(cfg.read_text())
    assert data["mcp"]["telecode"] == {"type": "remote", "url": reg.server_url(), "enabled": True}
    assert data["mcp"]["other"] == {"type": "local"} and data["theme"] == "dark"
    assert Path(res["backup"]).is_file()
    assert reg.register_sync("opencode")["already"] is True
    # Kiro creates its file.
    res = reg.register_sync("kiro")
    assert res["ok"], res
    kiro = json.loads((home / ".kiro" / "settings" / "mcp.json").read_text())
    assert kiro["mcpServers"]["telecode"] == {"url": reg.server_url()}
    assert reg.client_status("kiro")["matches"] is True


def test_file_registration_conflict_and_unparseable(fake_home):
    reg, home = fake_home["reg"], fake_home["home"]
    fake_home["installed"].add("kiro")
    f = home / ".kiro" / "settings" / "mcp.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"mcpServers": {"telecode": {"url": "http://elsewhere/mcp"}}}))
    res = reg.register_sync("kiro")
    assert res["conflict"] and not res["ok"]
    assert json.loads(f.read_text())["mcpServers"]["telecode"]["url"] == "http://elsewhere/mcp"
    assert reg.register_sync("kiro", force=True)["ok"]
    f.write_text('{"mcpServers": {} // a comment\n}')
    res = reg.register_sync("kiro", force=True)
    assert not res["ok"] and "not plain JSON" in res["error"]
    assert "// a comment" in f.read_text()                # never rewritten


def test_claude_desktop_and_gemini_commands(fake_home, monkeypatch):
    reg, home = fake_home["reg"], fake_home["home"]
    fake_home["installed"].add("gemini")
    g = reg.register_sync("gemini", dry_run=True)
    assert g["ok"] and g["commands"][-1][1:] == ["mcp", "add", "--scope", "user", "--transport", "http",
                                                 "telecode", reg.server_url()]
    # Claude Desktop: detected by its install folder, config gets a stdio mcp-remote bridge.
    (home / "localappdata" / "AnthropicClaude").mkdir(parents=True)
    (home / "localappdata" / "AnthropicClaude" / "claude.exe").write_bytes(b"")
    monkeypatch.setattr(reg.sys, "platform", "win32")
    monkeypatch.setattr(reg.shutil, "which", lambda b: "npx" if b == "npx" else None)
    res = reg.register_sync("claude_desktop", dry_run=True)
    assert res["ok"], res
    assert res["writes"][0]["entry"] == {"command": "npx", "args": ["-y", "mcp-remote", reg.server_url()]}
    assert res["writes"][0]["path"].endswith(str(Path("appdata") / "Claude" / "claude_desktop_config.json"))
    monkeypatch.setattr(reg.shutil, "which", lambda b: None)
    assert "npx" in reg.register_sync("claude_desktop", dry_run=True)["error"]


def test_server_status_reports_down(fake_home):
    st = fake_home["reg"].server_status(timeout=1.0)
    assert st["reachable"] is False and st["url"].endswith("/mcp")


# ── #17 headless CLI ─────────────────────────────────────────────────────

@pytest.fixture
def live_api(td):
    """The design REST routes on a real port, in a background loop (the CLI is sync urllib)."""
    from aiohttp import web
    from proxy import api_design, api_design_agents, api_design_editor, api_design_export, api_design_systems
    loop = asyncio.new_event_loop()
    started = threading.Event()
    state = {}

    async def start():
        app = web.Application()
        api_design.register_routes(app)
        api_design_export.register_routes(app)
        api_design_systems.register_routes(app)
        api_design_editor.register_routes(app)
        api_design_agents.register_routes(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        state["runner"] = runner
        state["port"] = site._server.sockets[0].getsockname()[1]

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start())
        started.set()
        loop.run_forever()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert started.wait(15)
    yield f"http://127.0.0.1:{state['port']}"
    asyncio.run_coroutine_threadsafe(state["runner"].cleanup(), loop).result(15)
    loop.call_soon_threadsafe(loop.stop)
    t.join(5)


def _cli(*argv):
    from services.design import cli
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(list(argv))
    return code, buf.getvalue()


def test_cli_projects_files_verify(td, live_api, monkeypatch):
    code, out = _cli("--base", live_api, "--json", "create", "CLI made", "--kind", "slides")
    assert code == 0
    pid = json.loads(out)["project"]["id"]
    d = td["store"].project_dir(pid)
    (d / "index.html").write_text("<h1>hi</h1>")
    code, out = _cli("--base", live_api, "--json", "projects", "--query", "cli made")
    assert code == 0 and [p["id"] for p in json.loads(out)["projects"]] == [pid]
    code, out = _cli("--base", live_api, "files", "CLI made")              # title lookup, human output
    assert code == 0 and "index.html" in out
    from services.design import verifier

    async def fake_run(p, files, task, **kw):
        return {"mode": "directed", "status": "pass", "issues": [], "task": task}
    monkeypatch.setattr(verifier, "run_check", fake_run)
    code, out = _cli("--base", live_api, "--json", "verify", pid, "--task", "has an h1")
    assert code == 0 and json.loads(out)["task"] == "has an h1"
    code, out = _cli("--base", live_api, "--json", "get", "no-such-project")
    assert code == 1 and "matches 0 projects" in json.loads(out)["error"]


def test_cli_unreachable_is_a_clean_error():
    code, out = _cli("--base", f"http://127.0.0.1:{_free_port()}", "--json", "projects")
    assert code == 1 and "not reachable" in json.loads(out)["error"]


# ── MCP tools over the same REST routes ──────────────────────────────────

def test_mcp_tools_copy_move_console_verify(td, live_api, monkeypatch):
    import mcp_server.tools.design as tools
    monkeypatch.setattr(tools, "_base", lambda: live_api)
    pid, d = _project(td["store"], {"page.html": "<p>p</p>"})
    res = json.loads(asyncio.run(tools.design_copy_file(pid, "page.html", "page-v2.html")))
    assert res["ok"] and (d / "page-v2.html").is_file()
    res = json.loads(asyncio.run(tools.design_move_file(pid, "page-v2.html", "alt/page-v2.html")))
    assert res["files"] == [{"from": "page-v2.html", "to": "alt/page-v2.html"}]
    assert asyncio.run(tools.design_copy_file(pid, "page.html", "alt/page-v2.html")).startswith("error: HTTP 409")
    from proxy import api_design_agents as ag
    ag._LIVE_CONSOLE.pop(pid, None)
    ag._LIVE_CONSOLE[pid] = {"seq": 1, "updated_at": 1.0, "files": {"page.html": [
        {"seq": 1, "level": "error", "text": "Uncaught TypeError", "at": "", "received_at": 1.0}]}}
    out = json.loads(asyncio.run(tools.design_get_console(pid, "page.html", source="live")))
    assert out["live"]["entries"][0]["text"] == "Uncaught TypeError"
    assert "not open" in out["note"]                           # no app-state reported → says so
    from services.design import verifier

    async def fake_run(p, files, task, **kw):
        return {"mode": "directed", "status": "fail", "task": task, "files": files}
    monkeypatch.setattr(verifier, "run_check", fake_run)
    out = json.loads(asyncio.run(tools.design_verify(pid, task="nav collapses", files=["page.html"])))
    assert out == {"mode": "directed", "status": "fail", "task": "nav collapses", "files": ["page.html"]}
    assert asyncio.run(tools.design_get_console(pid, source="bogus")).startswith("error")
    out = asyncio.run(tools.design_browser(url="ftp://example.com/"))
    assert out.startswith("error: HTTP 502")
