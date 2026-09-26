"""TeleDesign canvas: several documents per project, the JSON mirror, script-node files, and the
host half of theme axes / slots / shader + mesh fills (patches/open-pencil/0013-0018).

The editor half is tested in open-pencil's own suite (tests/engine/app/telecode/*) and end to end
against the real editor in headless Edge (see the report); here the Python half runs against a
temporary settings dir. Nothing touches the real data/ or settings, no CLI turn, no local model.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import pytest

import config

REPO = Path(__file__).resolve().parents[1]
FIG = b"PK\x03\x04" + b"\x00" * 64          # store.save_canvas only checks the ZIP magic


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
    from services.design import editor_bridge, store
    monkeypatch.setattr(editor_bridge, "open_doc", lambda pid: None)
    return store


def _project(store, title="Docs"):
    rec = store.create_project({"title": title})
    return rec["id"], store.project_dir(rec["id"])


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


# ── store: documents ─────────────────────────────────────────────────────

def test_new_project_has_one_default_document(td):
    pid, d = _project(td)
    listing = td.list_docs(pid)
    assert listing["default"] == "main"
    assert [(x["id"], x["default"], x["has_canvas"], x["mirror"]) for x in listing["docs"]] == [
        ("main", True, False, None)]
    assert td.get_canvas(pid) is None
    assert not td.has_canvas(d)


def test_legacy_doc_fig_moves_to_docs_main(td):
    pid, d = _project(td)
    (d / "doc.fig").write_bytes(FIG)
    assert td.has_canvas(d)                         # legacy layout still counts before migration
    listing = td.list_docs(pid)
    assert not (d / "doc.fig").exists()
    assert (d / "docs" / "main.fig").read_bytes() == FIG
    assert listing["default"] == "main" and listing["docs"][0]["has_canvas"] is True
    idx = json.loads((d / "docs" / "canvases.json").read_text(encoding="utf-8"))
    assert idx["default"] == "main" and [x["id"] for x in idx["docs"]] == ["main"]
    assert td.get_canvas(pid) == FIG and td.get_canvas(pid, "main") == FIG
    # A restored doc.fig (an old version) is migrated again, onto the default document.
    (d / "doc.fig").write_bytes(FIG + b"v2")
    td.list_docs(pid)
    assert (d / "docs" / "main.fig").read_bytes() == FIG + b"v2" and not (d / "doc.fig").exists()


def test_orphan_fig_without_index_entry_is_adopted(td):
    pid, d = _project(td)
    assert td.save_canvas(pid, FIG)                            # writes docs/main.fig and the index
    (d / "docs" / "wire-frames.fig").write_bytes(FIG)
    ids = [x["id"] for x in td.list_docs(pid)["docs"]]
    assert ids == ["main", "wire-frames"]
    (d / "docs" / "Bad Name.fig").write_bytes(FIG)             # not a valid id: ignored
    assert [x["id"] for x in td.list_docs(pid)["docs"]] == ["main", "wire-frames"]


def test_create_copy_rename_default_delete(td):
    pid, d = _project(td)
    assert td.save_canvas(pid, FIG)                            # default document
    a = td.create_doc(pid, "  Wire   frames ")
    assert a["id"] == "wire-frames" and a["name"] == "Wire frames" and a["has_canvas"] is False
    b = td.create_doc(pid, "Wire frames", copy_from="main")
    assert b["id"] == "wire-frames-2" and b["has_canvas"] is True
    assert (d / "docs" / "wire-frames-2.fig").read_bytes() == FIG
    assert td.create_doc(pid, "x", copy_from="nope") is None
    assert td.update_doc(pid, "wire-frames", {"name": "Wires", "default": True})["default"] is True
    assert td.list_docs(pid)["default"] == "wire-frames"
    assert td.get_canvas(pid) is None                         # the new default has no canvas yet
    assert td.save_canvas(pid, FIG + b"w")                     # writes the default: wire-frames
    assert td.get_canvas(pid, "wire-frames") == FIG + b"w" and td.get_canvas(pid, "main") == FIG
    assert td.update_doc(pid, "nope", {"name": "x"}) is None
    td.save_canvas_mirror(pid, "wire-frames", b'{"a": 1}')
    listing = td.delete_doc(pid, "wire-frames")
    assert [x["id"] for x in listing["docs"]] == ["main", "wire-frames-2"]
    assert listing["default"] == "main"                       # the deleted default moves on
    assert not (d / "docs" / "wire-frames.fig").exists() and not (d / "docs" / "wire-frames.fig.json").exists()
    assert td.delete_doc(pid, "wire-frames-2")
    assert td.delete_doc(pid, "main") is None                 # never the last one


def test_save_canvas_validates(td):
    pid, _ = _project(td)
    assert not td.save_canvas(pid, b"not a zip")
    assert not td.save_canvas(pid, FIG, "nope")               # unknown document
    assert not td.save_canvas(pid, FIG, "../x")
    assert td.get_canvas(pid, "../x") is None
    assert not td.save_canvas("0" * 32, FIG)


def test_doc_limit(td, monkeypatch):
    pid, _ = _project(td)
    monkeypatch.setattr(td, "MAX_DOCS", 3)
    assert td.create_doc(pid, "a") and td.create_doc(pid, "b")
    assert td.create_doc(pid, "c") is None


def test_canvas_paths_and_helpers(td):
    assert td.is_canvas_path("doc.fig") and td.is_canvas_path("docs/main.fig")
    assert not td.is_canvas_path("docs/sub/x.fig") and not td.is_canvas_path("docs/main.fig.json")
    assert not td.is_canvas_path("other/main.fig")
    assert td.valid_doc_id("main") and td.valid_doc_id("a-1") and not td.valid_doc_id("-a")
    assert not td.valid_doc_id("A") and not td.valid_doc_id("x" * 41) and not td.valid_doc_id(None)
    assert td.canvas_rel("main") == "docs/main.fig"


# ── store: JSON mirror ───────────────────────────────────────────────────

def test_mirror_is_canonical_and_idempotent(td):
    pid, d = _project(td)
    rel = td.save_canvas_mirror(pid, None, json.dumps({"b": [1, {"z": 1, "a": 2}], "a": "é"}).encode())
    assert rel == "docs/main.fig.json"
    text = (d / rel).read_text(encoding="utf-8")
    assert text == td.canonical_json({"a": "é", "b": [1, {"a": 2, "z": 1}]})
    assert text.index('"a"') < text.index('"b"') and text.endswith("}\n") and "\r" not in text
    mtime = (d / rel).stat().st_mtime_ns
    assert td.save_canvas_mirror(pid, "main", json.dumps({"a": "é", "b": [1, {"a": 2, "z": 1}]}).encode()) == rel
    assert (d / rel).stat().st_mtime_ns == mtime               # same content: not rewritten
    assert td.get_canvas_mirror(pid) == text
    assert td.save_canvas_mirror(pid, None, b"[1, 2]") is None      # not an object
    assert td.save_canvas_mirror(pid, None, b"{nope") is None
    assert td.save_canvas_mirror(pid, "nope", b"{}") is None
    assert td.list_docs(pid)["docs"][0]["mirror"] == rel


# ── canvas files are written by their own routes only ────────────────────

def test_generic_file_writes_cannot_touch_canvas_documents(td):
    from services.design import files as dfiles
    for rel in ("doc.fig", "docs/main.fig", "docs/main.fig.json", "docs/canvases.json"):
        assert not dfiles.writable(rel), rel
    assert dfiles.writable("docs/notes.md") and dfiles.writable("scripts/bars.js")
    pid, d = _project(td)
    assert not dfiles.write_file(pid, "docs/main.fig", FIG)
    assert not (d / "docs" / "main.fig").exists()


def test_share_snapshots_leave_canvas_documents_out(td):
    from services.design import share
    for rel in ("doc.fig", "docs/main.fig", "docs/wires.fig.json", "docs/canvases.json", "boards.json"):
        assert share._excluded(rel), rel
    assert not share._excluded("docs/notes.md") and not share._excluded("index.html")


def test_prompt_canvas_summary_names_documents(td):
    from services.design import prompt_builder
    pid, d = _project(td)
    assert "docs/*.fig" not in prompt_builder.canvas_summary(d)
    td.save_canvas(pid, FIG)
    assert "telecode_doc_list" in prompt_builder.canvas_summary(d)


# ── REST ─────────────────────────────────────────────────────────────────

def test_docs_routes(td, monkeypatch):
    pid, d = _project(td)
    from services.design import editor_bridge
    base = f"/api/design/projects/{pid}/docs"

    async def go(c):
        r = await (await c.get(base)).json()
        assert r["default"] == "main" and r["open"] is None and len(r["docs"]) == 1
        r = await c.post(base, json={"name": "Wireframes"})
        assert r.status == 200 and (await r.json())["doc"]["id"] == "wireframes"
        assert (await c.post(base, json={"copy_from": "../x"})).status == 400
        assert (await c.post(base, json={"copy_from": "nope"})).status == 400
        r = await (await c.patch(base + "/wireframes", json={"name": "Wires", "default": True})).json()
        assert r["doc"]["name"] == "Wires" and r["doc"]["default"] is True
        assert (await c.patch(base + "/nope", json={"name": "x"})).status == 404

        # .fig bytes: octet-stream only (not CORS-simple), ZIP magic, known document.
        assert (await c.put(base + "/wireframes/canvas", data=FIG,
                            headers={"Content-Type": "text/plain"})).status == 415
        assert (await c.put(base + "/wireframes/canvas", data=b"junk",
                            headers={"Content-Type": "application/octet-stream"})).status == 400
        assert (await c.put(base + "/nope/canvas", data=FIG,
                            headers={"Content-Type": "application/octet-stream"})).status == 400
        assert (await c.get(base + "/wireframes/canvas")).status == 404
        assert (await c.put(base + "/wireframes/canvas", data=FIG,
                            headers={"Content-Type": "application/octet-stream"})).status == 200
        r = await c.get(base + "/wireframes/canvas")
        assert r.status == 200 and await r.read() == FIG
        r = await c.get(f"/api/design/projects/{pid}/canvas")        # legacy route = the default document
        assert r.status == 200 and await r.read() == FIG
        assert (await c.get(base + "/main/canvas")).status == 404       # main was never saved

        # Mirror: JSON object only, canonical on disk, readable back.
        assert (await c.put(base + "/wireframes/canvas.json", data=b"[1]",
                            headers={"Content-Type": "application/json"})).status == 400
        assert (await c.put(base + "/wireframes/canvas.json", data=b'{"b":1,"a":2}',
                            headers={"Content-Type": "text/plain"})).status == 415
        r = await (await c.put(base + "/wireframes/canvas.json", data=b'{"b":1,"a":2}',
                               headers={"Content-Type": "application/json"})).json()
        assert r["path"] == "docs/wireframes.fig.json"
        r = await c.get(base + "/wireframes/canvas.json")
        assert r.status == 200 and (await r.text()).startswith('{\n "a": 2')
        assert (await c.get(base + "/main/canvas.json")).status == 404

        # Editor status: the document asked about, and the one actually open.
        st = (await (await c.get(f"/api/design/projects/{pid}/editor?doc=wireframes")).json())["editor"]
        assert st["doc_id"] == "wireframes" and st["has_canvas"] is True and st["open_doc"] is None
        assert (await c.get(f"/api/design/projects/{pid}/editor?doc=nope")).status == 404

        # Delete: never the open document, never the last one.
        monkeypatch.setattr(editor_bridge, "open_doc", lambda p: "main")
        assert (await c.delete(base + "/main")).status == 409
        monkeypatch.setattr(editor_bridge, "open_doc", lambda p: None)
        r = await (await c.delete(base + "/main")).json()
        assert [x["id"] for x in r["docs"]] == ["wireframes"]
        assert (await c.delete(base + "/wireframes")).status == 400
    _run_app(go)
    assert (d / "docs" / "wireframes.fig").read_bytes() == FIG


def test_script_files_route(td):
    pid, d = _project(td)
    (d / "scripts").mkdir()
    (d / "scripts" / "bars.js").write_text("// @input n: number = 3\nreturn []\n", encoding="utf-8")
    base = f"/api/design/projects/{pid}/editor/scripts"

    async def go(c):
        assert (await c.get(base)).status == 400
        r = await (await c.get(base + "?path=scripts/bars.js&path=missing.js&path=../x.js&path=notes.md&text=1")).json()
        f = r["files"]
        assert f["scripts/bars.js"]["exists"] and f["scripts/bars.js"]["text"].startswith("// @input")
        assert len(f["scripts/bars.js"]["sha256"]) == 64
        assert f["missing.js"]["exists"] is False
        assert f["../x.js"]["error"] and f["notes.md"]["error"]
        r = await (await c.get(base + "?path=scripts/bars.js")).json()
        assert "text" not in r["files"]["scripts/bars.js"]
        assert (await c.get(f"/api/design/projects/{'0' * 32}/editor/scripts?path=a.js")).status == 404
    _run_app(go)


def test_local_doc_tools_through_editor_call(td):
    """telecode_doc_list / telecode_doc_create are answered by the proxy (no page needed)."""
    pid, _ = _project(td)

    async def go(c):
        url = f"/api/design/projects/{pid}/editor/call"
        r = await (await c.post(url, json={"tool": "telecode_doc_list"})).json()
        assert r["ok"] and r["result"]["default"] == "main" and r["result"]["open"] is None
        r = await (await c.post(url, json={"tool": "telecode_doc_create", "args": {"name": "Flows"}})).json()
        assert r["ok"] and r["result"]["doc"]["id"] == "flows"
        r = await c.post(url, json={"tool": "telecode_doc_create", "args": {"copy_from": "../etc"}})
        assert r.status == 400
        r = await c.post(url, json={"tool": "telecode_doc_open", "args": {"doc": "nope"}})
        assert r.status == 400 and "Unknown document" in (await r.json())["error"]
    _run_app(go)
    assert [x["id"] for x in td.list_docs(pid)["docs"]] == ["main", "flows"]


def test_open_document_waits_for_the_page_to_come_back(td, monkeypatch):
    from services.design import editor_bridge as eb
    pid, _ = _project(td)
    td.create_doc(pid, "Flows")
    monkeypatch.setattr(eb, "open_doc", lambda p: eb._pages[p].doc if p in eb._pages else None)
    sent = []

    class WS:
        closed = False

    async def fake_request(p, command, args, timeout=None):
        sent.append((command, dict(args)))
        # The page acknowledges and reloads on the other document: a new registration.
        async def reload():
            await asyncio.sleep(0.05)
            eb._pages[p] = eb._Page(WS(), p, "test", args["doc"])
        asyncio.ensure_future(reload())
        return {"ok": True}
    monkeypatch.setattr(eb, "request", fake_request)
    monkeypatch.setattr(eb, "_publish_docs", lambda *a, **k: None)
    eb._pages[pid] = eb._Page(WS(), pid, "test", "main")
    try:
        r = asyncio.run(eb.open_document(pid, "flows", timeout=5))
        assert r["result"] == {"open": "flows", "switched": True}
        assert sent == [("telecode_doc_open", {"doc": "flows"})]
        assert asyncio.run(eb.open_document(pid, "flows"))["result"]["switched"] is False
        with pytest.raises(eb.EditorBridgeError):
            asyncio.run(eb.open_document(pid, "nope"))
    finally:
        eb._pages.pop(pid, None)


def test_layer_issues_name_the_open_document():
    from services.design import editor_bridge as eb
    f = {"category": "parent-overflow", "severity": "critical", "nodeA": {"id": "1:2"}, "nodeB": {}}
    assert eb.layer_issue({"name": "P"}, f, "docs/flows.fig")["file"] == "docs/flows.fig"


# ── editor build: descriptors, patches, prompts, shell ───────────────────

NEW_TOOLS = {
    "telecode_doc_list": "local", "telecode_doc_create": "local", "telecode_doc_open": "telecode_doc_open",
    "telecode_script_create": None, "telecode_script_set": None, "telecode_script_run": None,
    "telecode_script_list": None, "telecode_script_convert": None,
    "telecode_theme_get": None, "telecode_theme_set": None, "telecode_theme_active": None,
    "telecode_slot_create": None, "telecode_slot_list": None, "telecode_slot_fill": None, "telecode_slot_reset": None,
    "telecode_slot_suggest": None, "telecode_slot_prefer": None, "telecode_slot_remove": None,
    "telecode_fill_set": None, "telecode_fill_list": None, "telecode_fill_remove": None, "telecode_fill_presets": None,
    "telecode_fill_uniforms": None, "telecode_fill_mesh_edit": None,
}


def test_editor_tools_json_carries_the_canvas_feature_tools():
    data = json.loads((REPO / "services" / "design" / "editor_tools.json").read_text(encoding="utf-8"))
    by = {t["name"]: t for t in data["tools"]}
    for name, command in NEW_TOOLS.items():
        assert name in by, name
        assert by[name]["command"] == (command or name), name
    assert by["telecode_fill_set"]["input_schema"]["required"] == ["node_id", "kind"]
    assert set(by["telecode_slot_fill"]["input_schema"]["properties"]) >= {"instance_id", "slot", "jsx", "node_ids", "component_id"}
    assert by["telecode_theme_set"]["effect"] == "write" and by["telecode_theme_get"]["effect"] == "read"
    fill_set = by["telecode_fill_set"]["input_schema"]["properties"]
    assert {"source", "lang", "glsl", "sksl"} <= set(fill_set)
    assert set(by["telecode_fill_mesh_edit"]["input_schema"]["properties"]) >= {"node_id", "index", "phase", "points"}
    assert "preferred" in by["telecode_slot_create"]["input_schema"]["properties"]


def test_patch_series_carries_0013_to_0021():
    names = sorted(p.name for p in (REPO / "patches" / "open-pencil").glob("*.patch"))
    assert [n[:4] for n in names] == [f"{i:04d}" for i in range(1, 22)]
    tail = "\n".join(names[12:])
    for word in ("several-canvas-documents", "JSON-mirror", "script-nodes", "theme-axes", "slots", "mesh-gradient",
                 "shader-fill-inputs-GLSL", "per-instance-slot-content", "slots-edited-in-place"):
        assert word in tail, word
    for p in names[12:]:
        raw = (REPO / "patches" / "open-pencil" / p).read_bytes()
        assert b"\r\n" not in raw, f"{p} has CRLF line endings (git apply needs them byte-exact)"


def test_check_mode_applies_the_series_cumulatively(monkeypatch, tmp_path):
    """--check must apply each patch after checking it: later patches edit files earlier ones add."""
    import tools.build_open_pencil as bop
    p1, p2 = tmp_path / "0001-a.patch", tmp_path / "0002-b.patch"
    p1.write_text("x", encoding="utf-8")
    p2.write_text("y", encoding="utf-8")
    monkeypatch.setattr(bop, "patches", lambda: [p1, p2])
    monkeypatch.setattr(bop, "_which", lambda name: name)
    calls = []

    class Res:
        returncode = 0
        stdout = stderr = ""

    def fake_run(cmd, cwd=None, **kw):
        calls.append(cmd[1:3])
        return Res()
    monkeypatch.setattr(bop, "_run", fake_run)
    info = bop.apply_patches(tmp_path, check_only=True)
    assert all(p["applies"] for p in info)
    assert calls == [["apply", "--check"], ["apply", "--whitespace=nowarn"]] * 2


def test_prompts_document_the_canvas_feature_tools():
    text = (REPO / "services" / "design" / "prompts" / "canvas.md").read_text(encoding="utf-8")
    for name in ("telecode_doc_list", "telecode_doc_create", "telecode_doc_open", "telecode_script_create",
                 "telecode_theme_set", "telecode_slot_create", "telecode_slot_fill", "telecode_fill_set",
                 "telecode_fill_presets"):
        assert name in text, name
    assert "doc.fig" not in text


def test_shell_wires_the_canvas_extras():
    app = REPO / "proxy" / "static" / "design" / "app"
    canvas = (app / "canvas.js").read_text(encoding="utf-8")
    extras = (app / "canvas_extras.js").read_text(encoding="utf-8")
    assert 'from "./canvas_extras.js"' in canvas and "extras.onSelection(selNodes)" in canvas
    for tool in ("telecode_theme_get", "telecode_theme_set", "telecode_theme_active", "telecode_slot_list",
                 "telecode_slot_create", "telecode_slot_fill", "telecode_slot_reset", "telecode_slot_suggest",
                 "telecode_slot_prefer", "telecode_slot_remove", "telecode_fill_set", "telecode_fill_list",
                 "telecode_fill_remove", "telecode_fill_presets", "telecode_fill_uniforms", "telecode_fill_mesh_edit"):
        assert tool in extras, tool
    # Overlays: hatched empty slots, mesh point handles; generated uniform controls.
    for marker in ("td-editor:slots", "td-slot-hatch", "td-mesh-dot", 'type: "range"', 'type: "color"', 'class: "pad"'):
        assert marker in extras, marker
    assert "extras.onMessage(d.type, p)" in canvas
    ws = (app / "workspace.js").read_text(encoding="utf-8")
    assert "isInternal" in ws and "docs/canvases.json" in ws
