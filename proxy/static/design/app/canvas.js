// TeleDesign — Canvas view.
//
// With the open-pencil build present (/design/editor/index.html) this hosts the
// editor iframe and overlays each registered HTML board (boards.json) on its
// frame, tracking td-editor:viewport / td-editor:frames. Without it, a built-in
// pan/zoom canvas lays every HTML file out as a board.
//
// The editor protocol is documented above renderEditor().
import { h, icon, btn, mount, clear, bus, prefs, api, toast, toastError, emptyState, P_, isHtml, promptDialog, menu, modal, isTyping } from "./core.js";
import { S, htmlFiles, assetFor, previewUrl, loadBoards, writeUrl, chatRunning } from "./state.js";
import { makePreviewFrame, unregisterFrame, post } from "./bridge.js";
import { modeToolbar, openFile, setView } from "./workspace.js";
import { mountDocs, mountScripts, preferredDoc } from "./canvas_nodes.js";
import { mountExtras } from "./canvas_extras.js";

export function render(body, bar) {
  const useEditor = S.editorAvailable && prefs.get("canvasEngine", "editor") === "editor";
  const r = useEditor ? renderEditor(body, bar) : renderBuiltin(body, bar);
  if (S.editorAvailable) {
    // Switch between the layer editor and a plain grid of every page.
    const seg = h("div", { class: "seg", role: "group", "aria-label": "Canvas engine" },
      h("button", { class: useEditor ? "on" : "", title: "Layer canvas (open-pencil): frames, components, variables — HTML boards overlaid", onclick: () => { prefs.set("canvasEngine", "editor"); import("./workspace.js").then((m) => m.drawStage()); } }, icon("layers"), "Layers"),
      h("button", { class: useEditor ? "" : "on", title: "Every HTML page side by side", onclick: () => { prefs.set("canvasEngine", "grid"); import("./workspace.js").then((m) => m.drawStage()); } }, icon("grid"), "Pages"));
    bar.appendChild(seg);
  }
  // Phones: the canvas is shown, but editing it wants a pointer and room. Say
  // so, and offer each HTML board in Preview instead.
  if (!matchMedia("(max-width: 600px)").matches) return r;
  bar.insertBefore(btn("Boards", { kind: "ghost", cls: "sm m-boards", icon: "eye", title: "Open an HTML board in Preview", onClick: (e) => boardsSheet(e.currentTarget) }), bar.children[1] || null);
  if (prefs.get("canvasNoteHidden", false)) return r;
  // A strip between the stage bar and the editor, so it hides none of the editor's own chrome.
  const note = h("div", { class: "m-canvas-note", role: "note" },
    icon("info"), h("span", { class: "grow" }, "Canvas editing works best on a larger screen."),
    btn("Preview boards", { kind: "primary", cls: "sm", icon: "eye", onClick: (e) => boardsSheet(e.currentTarget) }),
    btn("", { kind: "quiet", icon: "x", cls: "sm", title: "Hide this note", onClick: () => { prefs.set("canvasNoteHidden", true); note.remove(); } }));
  bar.after(note);
  return () => { note.remove(); if (typeof r === "function") r(); };
}
function boardsSheet(anchor) {
  const files = htmlFiles();
  menu(anchor, files.length ? [{ heading: "Open a board in Preview" }, ...files.map((f) => ({ label: f.path, icon: "canvas", hint: boardSize(f.path).join(" × "), onClick: () => openFile(f.path, { view: "preview" }) }))]
    : [{ label: "No HTML boards yet — ask the agent for one", disabled: true }], { width: "260px" });
}

// Board size: registry → asset viewport → kind default.
export function boardSize(path) {
  for (const b of Object.values(S.boards || {})) if (b && b.src === path && b.width && b.height) return [+b.width, +b.height];
  const a = assetFor(path);
  if (a?.viewport?.width && a?.viewport?.height) return [+a.viewport.width, +a.viewport.height];
  if (S.project.kind === "slides" || /deck|slides/i.test(path)) return [1920, 1080];
  if (S.project.kind === "mobile_app" || /mobile|ios|android/i.test(path)) return [390, 844];
  return [1440, 900];
}

// ── Built-in canvas ──────────────────────────────────────────────────────
function renderBuiltin(body, bar) {
  const pid = S.project.id;
  const tb = modeToolbar();
  bar.append(tb,
    h("span", { class: "divider-v" }),
    h("span", { class: "faint", style: { fontSize: "12px" } }, `${htmlFiles().length} board${htmlFiles().length === 1 ? "" : "s"}`),
    h("span", { class: "grow" }),
    btn("New board", { icon: "plus", kind: "ghost", cls: "sm", onClick: newBoard }),
    btn("", { kind: "quiet", icon: "refresh", cls: "sm", title: "Reload all boards", onClick: () => reloadAll() }));

  const world = h("div", { class: "board-world" });
  const cv = h("div", { class: "board-canvas", tabindex: 0, "aria-label": "Canvas" }, world);
  const zoomLbl = h("button", { class: "zoom", title: "Zoom to 100%  (Shift+0)", onclick: () => zoomTo(1) });
  const hud = h("div", { class: "canvas-hud" }, h("div", { class: "hud" },
    btn("", { kind: "quiet", icon: "zoomOut", cls: "sm", title: "Zoom out  (-)", onClick: () => zoomBy(1 / 1.25) }), zoomLbl,
    btn("", { kind: "quiet", icon: "zoomIn", cls: "sm", title: "Zoom in  (+)", onClick: () => zoomBy(1.25) }),
    btn("", { kind: "quiet", icon: "fit", cls: "sm", title: "Zoom to fit  (Shift+1)", onClick: () => fit() })));
  mount(body, cv, hud);

  const key = "view:" + pid;
  let view = prefs.get(key, null) || { x: 60, y: 60, z: 0.25 };
  const posKey = "pos:" + pid;
  const positions = prefs.get(posKey, {});
  let selected = S.deepBoard || S.activeFile || null;
  const boardEls = new Map();

  function layout() {
    const files = htmlFiles();
    const out = [];
    let x = 0, y = 0, rowH = 0;
    const GAP = 160, MAXW = 6400;
    for (const f of files) {
      const [w, hh] = boardSize(f.path);
      if (positions[f.path]) { out.push({ path: f.path, x: positions[f.path][0], y: positions[f.path][1], w, h: hh }); continue; }
      if (x > 0 && x + w > MAXW) { x = 0; y += rowH + GAP; rowH = 0; }
      out.push({ path: f.path, x, y, w, h: hh });
      x += w + GAP; rowH = Math.max(rowH, hh);
    }
    return out;
  }
  let boards = [];
  function draw() {
    for (const [, el] of boardEls) { const f = el.querySelector("iframe"); if (f) unregisterFrame(f); }
    boardEls.clear(); clear(world);
    boards = layout();
    if (!boards.length) {
      body.querySelector(".canvas-empty")?.remove();
      body.appendChild(h("div", { class: "canvas-empty" }, emptyState("canvas", "The canvas is empty", "Ask the agent in the chat for a first design, or create a board yourself.",
        h("div", { class: "row" }, btn("Ask the agent", { kind: "primary", icon: "sparkle", onClick: () => { import("./workspace.js").then((m) => m.setPanel("chat")); setTimeout(() => document.getElementById("chat-input")?.focus(), 50); } }),
          btn("New board", { icon: "plus", onClick: newBoard })))));
      return;
    }
    body.querySelector(".canvas-empty")?.remove();
    for (const b of boards) {
      const a = assetFor(b.path);
      const frame = makePreviewFrame(previewUrl(pid, b.path), { file: b.path, role: "board", title: b.path }, { lazy: true });
      const label = h("div", { class: "board-label" }, h("b", null, b.path.replace(/\.html?$/i, "")), h("span", { class: "faint" }, `${b.w}×${b.h}`),
        a?.status ? h("span", { class: "dot " + (a.status === "approved" ? "ok" : a.status === "changes-requested" ? "err" : "warn"), title: a.status }) : null);
      const el = h("div", { class: "board" + (selected === b.path ? " sel live" : ""), style: { left: b.x + "px", top: b.y + "px", width: b.w + "px", height: b.h + "px" }, dataset: { path: b.path } },
        label, h("div", { class: "board-frame" }, frame, h("div", { class: "board-shield" })));
      el.querySelector(".board-shield").addEventListener("mousedown", (e) => { if (e.button === 0 && !spaceDown) { select(b.path); } });
      el.querySelector(".board-shield").addEventListener("dblclick", () => openFile(b.path, { view: "preview" }));
      label.addEventListener("dblclick", () => openFile(b.path, { view: "preview" }));
      wireLabelDrag(label, b, el);
      boardEls.set(b.path, el);
      world.appendChild(el);
    }
    applyView();
  }
  function select(path) {
    selected = path;
    S.deepBoard = path; S.activeFile = path; writeUrl();
    if (!S.tabs.includes(path)) S.tabs.push(path);
    for (const [p, el] of boardEls) { el.classList.toggle("sel", p === path); el.classList.toggle("live", p === path); }
  }
  function wireLabelDrag(label, b, el) {
    label.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.stopPropagation(); select(b.path);
      const sx = e.clientX, sy = e.clientY, ox = b.x, oy = b.y;
      let moved = false;
      label.setPointerCapture(e.pointerId);
      const mv = (ev) => { const dx = (ev.clientX - sx) / view.z, dy = (ev.clientY - sy) / view.z; if (Math.abs(dx) + Math.abs(dy) > 2) moved = true; b.x = Math.round(ox + dx); b.y = Math.round(oy + dy); el.style.left = b.x + "px"; el.style.top = b.y + "px"; };
      const up = () => { label.removeEventListener("pointermove", mv); label.removeEventListener("pointerup", up); if (moved) { positions[b.path] = [b.x, b.y]; prefs.set(posKey, positions); } };
      label.addEventListener("pointermove", mv); label.addEventListener("pointerup", up);
    });
  }
  function applyView() {
    world.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.z})`;
    const dot = 20 * view.z;
    cv.style.setProperty("--dot", (dot < 8 ? dot * 4 : dot) + "px");
    cv.style.backgroundPosition = `${view.x}px ${view.y}px, 0 0`;
    zoomLbl.textContent = Math.round(view.z * 100) + "%";
    const inv = 1 / view.z;
    for (const el of boardEls.values()) { const l = el.firstChild; l.style.transform = `scale(${inv})`; }
    saveView();
  }
  const saveView = (() => { let t; return () => { clearTimeout(t); t = setTimeout(() => prefs.set(key, view), 300); }; })();
  function zoomAt(nz, cx, cy) {
    nz = Math.max(0.02, Math.min(4, nz));
    view.x = cx - (cx - view.x) * (nz / view.z); view.y = cy - (cy - view.y) * (nz / view.z); view.z = nz; applyView();
  }
  function zoomBy(f) { const r = cv.getBoundingClientRect(); zoomAt(view.z * f, r.width / 2, r.height / 2); }
  function zoomTo(z) { const r = cv.getBoundingClientRect(); zoomAt(z, r.width / 2, r.height / 2); }
  function fit(only) {
    const list = only ? boards.filter((b) => b.path === only) : boards;
    if (!list.length) return;
    const r = cv.getBoundingClientRect();
    if (r.width < 50 || r.height < 50) return;   // hidden (another tab on a phone)
    const minX = Math.min(...list.map((b) => b.x)), minY = Math.min(...list.map((b) => b.y));
    const maxX = Math.max(...list.map((b) => b.x + b.w)), maxY = Math.max(...list.map((b) => b.y + b.h));
    const pad = 80;
    const z = Math.min((r.width - pad * 2) / (maxX - minX), (r.height - pad * 2) / (maxY - minY), 1);
    view.z = z; view.x = (r.width - (maxX - minX) * z) / 2 - minX * z; view.y = (r.height - (maxY - minY) * z) / 2 - minY * z + 10;
    applyView();
  }
  function reloadAll(paths) {
    for (const [p, el] of boardEls) {
      if (paths && paths.length && !paths.includes(p)) continue;
      const f = el.querySelector("iframe"); if (f) f.src = previewUrl(pid, p, "&r=" + Date.now());
    }
  }

  // Pan / zoom input.
  let spaceDown = false, panning = null;
  cv.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = cv.getBoundingClientRect();
    if (e.ctrlKey || e.metaKey) zoomAt(view.z * Math.pow(1.0018, -e.deltaY), e.clientX - r.left, e.clientY - r.top);
    else { view.x -= e.shiftKey ? e.deltaY : e.deltaX; view.y -= e.shiftKey ? 0 : e.deltaY; applyView(); }
  }, { passive: false });
  cv.addEventListener("pointerdown", (e) => {
    const onBoard = e.target.closest(".board");
    if (e.button === 1 || (e.button === 0 && (spaceDown || !onBoard))) {
      e.preventDefault();
      if (!onBoard && e.button === 0 && !spaceDown) { selected = null; for (const el of boardEls.values()) el.classList.remove("sel", "live"); }
      panning = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
      cv.classList.add("panning"); cv.setPointerCapture(e.pointerId);
    }
  });
  cv.addEventListener("pointermove", (e) => { if (!panning) return; view.x = panning.vx + e.clientX - panning.x; view.y = panning.vy + e.clientY - panning.y; applyView(); });
  const endPan = () => { panning = null; cv.classList.remove("panning"); };
  cv.addEventListener("pointerup", endPan); cv.addEventListener("pointercancel", endPan);
  const kd = (e) => {
    if (e.code === "Space" && !e.repeat && !/^(INPUT|TEXTAREA)$/.test(e.target.tagName) && !e.target.isContentEditable) {
      spaceDown = true; cv.classList.add("space");
      for (const el of boardEls.values()) el.classList.remove("live");
      if (e.target === cv || e.target === document.body) e.preventDefault();
    }
  };
  const ku = (e) => { if (e.code === "Space") { spaceDown = false; cv.classList.remove("space"); if (selected) boardEls.get(selected)?.classList.add("live"); } };
  addEventListener("keydown", kd); addEventListener("keyup", ku);

  const offs = [
    bus.on("canvas-zoom", (a) => a === "in" ? zoomBy(1.25) : a === "out" ? zoomBy(0.8) : a === "fit" ? fit() : a === "100" ? zoomTo(1) : a === "selection" ? fit(selected) : null),
    bus.on("files-changed", () => { draw(); }),
    bus.on("reload-previews", (paths) => reloadAll(paths)),
    bus.on("assets", () => draw()),
    bus.on("canvas-refresh", () => {}),
  ];
  loadBoards().then(() => { draw(); if (!prefs.get(key, null)) requestAnimationFrame(() => fit()); if (S.deepBoard && boardEls.has(S.deepBoard)) requestAnimationFrame(() => fit(S.deepBoard)); });
  draw();
  if (!prefs.get(key, null)) requestAnimationFrame(() => fit());

  return () => {
    removeEventListener("keydown", kd); removeEventListener("keyup", ku);
    offs.forEach((f) => f()); tb._cleanup && tb._cleanup();
    for (const el of boardEls.values()) { const f = el.querySelector("iframe"); if (f) unregisterFrame(f); }
  };
}

async function newBoard() {
  const name = await promptDialog("New board", { label: "File name", value: `board-${htmlFiles().length + 1}.html`, confirm: "Create" });
  if (!name) return;
  const file = isHtml(name) ? name : name + ".html";
  if (!/^[\w./ -]+$/.test(file) || file.includes("..")) { toast("Use letters, numbers, dots, dashes and slashes only.", { kind: "error" }); return; }
  const html = `<!doctype html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1">\n  <title>${file.replace(/\.html?$/, "")}</title>\n  <style>body{margin:0;font:16px/1.5 system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;color:#667}</style>\n</head>\n<body>\n  <p data-td-id="placeholder">Empty board — describe it in the chat.</p>\n</body>\n</html>\n`;
  try {
    await api("PUT", P_(S.project.id) + "/files/" + file.split("/").map(encodeURIComponent).join("/"), new Blob([html]));
    const { loadFiles } = await import("./state.js");
    await loadFiles(); bus.emit("files-changed");
    toast("Board created", { kind: "success" });
  } catch (e) { toastError(e, "Couldn't create the board"); }
}

// ── open-pencil editor host ──────────────────────────────────────────────
// Protocol (patches/open-pencil/0006): messages are {type, payload}. Frames
// carry the board key as `id` (stable across reloads) plus the current
// `node_id`; viewport gives {x, y, zoom, canvas:{left, top, width, height}} and
// screen = canvas.left/top + canvasCoord * zoom + (x, y), inside the iframe.
function renderEditor(body, bar) {
  const pid = S.project.id;
  const tb = modeToolbar();
  let vp = { x: 0, y: 0, zoom: 1, canvas: { left: 0, top: 0, width: 0, height: 0 } };
  let frames = [];
  let ready = false;
  const overlays = new Map();   // board key -> {el, iframe, src}
  const pending = new Map();    // request_id -> {src, width, height}
  const status = h("span", { class: "faint", style: { fontSize: "12px" } }, "Loading the canvas…");
  const placeBtn = btn("Place pages", { icon: "canvas", kind: "ghost", cls: "sm hidden", title: "Put pages that aren't on the canvas yet onto it as live boards", onClick: () => placeAll() });
  // Contextual: the selected frame can become an HTML board, or stop being one.
  const markBtn = btn("Make HTML board", { icon: "canvas", kind: "ghost", cls: "sm hidden", title: "Show a project page live on the selected frame", onClick: () => markSelected() });
  const unmarkBtn = btn("Remove board", { icon: "x", kind: "quiet", cls: "sm hidden", title: "Stop rendering a page on the selected frame (the frame stays)", onClick: () => unmarkSelected() });
  let selNodes = [];
  bar.append(tb, h("span", { class: "divider-v" }), status, h("span", { class: "grow" }), markBtn, unmarkBtn, placeBtn,
    btn("HTML board", { icon: "plus", kind: "ghost", cls: "sm", title: "Place one HTML page on the canvas as a live board", onClick: () => placeOne() }));
  const editorUrl = (doc) => "/design/editor/?" + new URLSearchParams({ project: pid, ...(doc ? { doc } : {}) });
  const frame = h("iframe", { class: "editor-frame", src: editorUrl(preferredDoc(pid)), title: "Canvas editor", allow: "clipboard-read; clipboard-write" });
  const layer = h("div", { class: "overlay-layer" });
  mount(body, frame, layer);
  const send = (type, payload = {}) => { try { frame.contentWindow.postMessage({ type: "td-editor:" + type, payload }, location.origin); } catch {} };
  const gaps = mountCanvasGaps({ pid, body, bar, layer, send, vp: () => vp, selection: () => selNodes });
  // Several canvas documents per project (0013) and script nodes (0015).
  const docs = mountDocs({ pid, bar, anchor: tb, reload: (doc) => { ready = false; status.textContent = "Loading the canvas…"; frame.src = editorUrl(doc); } });
  const scriptsUi = mountScripts({ pid, body, bar, layer, send, vp: () => vp });
  // Theme axes (0016), slots and shader / mesh fills (0017–0021): canvas tools over /editor/call.
  const extras = mountExtras({ pid, body, bar, layer, vp: () => vp,
    callTool: async (tool, args = {}, timeout = 30) => (await api("POST", P_(pid) + "/editor/call", { tool, args, timeout }, { feature: "editorCall" })).result });

  const placedSrcs = () => new Set(Object.values(S.boards || {}).map((b) => b && b.src).filter(Boolean));
  function drawPlaceBtn() {
    const missing = htmlFiles().filter((f) => !placedSrcs().has(f.path));
    placeBtn.classList.toggle("hidden", !ready || !missing.length);
    mount(placeBtn, icon("canvas"), h("span", null, `Place ${missing.length} page${missing.length === 1 ? "" : "s"}`));
  }
  function place() {
    const c = vp.canvas || { left: 0, top: 0, width: body.clientWidth, height: body.clientHeight };
    layer.style.clipPath = c.width ? `inset(${c.top}px ${Math.max(0, body.clientWidth - c.left - c.width)}px ${Math.max(0, body.clientHeight - c.top - c.height)}px ${c.left}px)` : "";
    for (const f of frames) {
      const src = f.src || S.boards[f.id]?.src;
      if (!src) continue;
      let o = overlays.get(f.id);
      if (o && o.src !== src) { unregisterFrame(o.iframe); o.el.remove(); overlays.delete(f.id); o = null; }
      if (!o) {
        const ifr = makePreviewFrame(previewUrl(pid, src), { file: src, role: "board", title: src });
        const el = h("div", { class: "ov" }, ifr);
        layer.appendChild(el);
        o = { el, iframe: ifr, src };
        overlays.set(f.id, o);
      }
      const reg = S.boards[f.id] || {};
      const bw = +reg.width || f.width, bh = +reg.height || f.height;
      const sx = (f.width * vp.zoom) / bw;
      o.el.style.width = bw + "px"; o.el.style.height = bh + "px";
      o.el.style.transform = `translate(${c.left + f.x * vp.zoom + vp.x}px, ${c.top + f.y * vp.zoom + vp.y}px) scale(${sx})`;
      // Pointer events go to the editor (select / move the frame) unless a
      // page-interaction mode is on.
      o.el.style.pointerEvents = S.mode === "view" ? "none" : "auto";
    }
    for (const [id, o] of overlays) if (!frames.some((f) => f.id === id)) { unregisterFrame(o.iframe); o.el.remove(); overlays.delete(id); }
  }
  function insert(src) {
    const [w, hh] = boardSize(src);
    const request_id = Math.random().toString(36).slice(2);
    pending.set(request_id, { src, width: w, height: hh });
    send("insert-frame", { name: src.replace(/\.html?$/i, ""), width: w, height: hh, request_id });
    return new Promise((res) => { pending.get(request_id).done = res; setTimeout(() => res(false), 8000); });
  }
  async function placeOne() {
    const files = htmlFiles().map((f) => f.path);
    if (!files.length) { toast("There are no HTML pages yet. Ask the agent for one first."); return; }
    const src = await promptDialog("Place an HTML board", { label: `Page (${files.slice(0, 4).join(", ")}${files.length > 4 ? ", …" : ""})`, value: S.activeFile && isHtml(S.activeFile) ? S.activeFile : files[0], confirm: "Place" });
    if (!src) return;
    if (!files.includes(src)) { toast("That page isn't in the project.", { kind: "error" }); return; }
    await insert(src);
  }
  async function placeAll() {
    const missing = htmlFiles().filter((f) => !placedSrcs().has(f.path));
    let firstKey = null;
    for (const f of missing) { await insert(f.path); firstKey = firstKey || Object.keys(S.boards).find((k) => S.boards[k].src === f.path); }
    if (firstKey) send("focus", { node_id: firstKey });
    toast(`Placed ${missing.length} board${missing.length === 1 ? "" : "s"}`, { kind: "success" });
  }
  function drawSelActions() {
    const one = selNodes.length === 1 ? selNodes[0] : null;
    markBtn.classList.toggle("hidden", !(one && !one.board && /FRAME|COMPONENT|SECTION/.test(one.type || "FRAME")));
    unmarkBtn.classList.toggle("hidden", !(one && one.board && S.boards[one.board]));
  }
  async function markSelected() {
    const n = selNodes[0];
    if (!n) return;
    const files = htmlFiles().map((f) => f.path);
    if (!files.length) { toast("There are no HTML pages yet.", { kind: "error" }); return; }
    const src = await promptDialog("Make HTML board", { label: `Page to show on "${n.name}" (${files.slice(0, 4).join(", ")}${files.length > 4 ? ", …" : ""})`, value: files[0], confirm: "Make board" });
    if (!src) return;
    if (!files.includes(src)) { toast("That page isn't in the project.", { kind: "error" }); return; }
    const [w, hh] = boardSize(src);
    try {
      const r = await api("POST", P_(pid) + "/editor/boards", { node_id: n.id, src, width: Math.round(n.width) || w, height: Math.round(n.height) || hh }, { feature: "editorBoards" });
      await loadBoards(); send("boards", { boards: S.boards }); toast(`${n.name} now shows ${src}`, { kind: "success" });
      if (r.board?.key) n.board = r.board.key;
      drawSelActions(); drawPlaceBtn();
    } catch (e) {
      // No server route: stamp the key in the editor and register it ourselves.
      const request_id = Math.random().toString(36).slice(2);
      pending.set(request_id, { src, width: Math.round(n.width) || w, height: Math.round(n.height) || hh, mark: true });
      send("mark-board", { node_id: n.id, request_id });
    }
  }
  async function unmarkSelected() {
    const key = selNodes[0]?.board;
    if (!key) return;
    try { await api("DELETE", P_(pid) + "/editor/boards/" + encodeURIComponent(key)); }
    catch { const nb = { ...S.boards }; delete nb[key]; S.boards = nb; await api("PUT", P_(pid) + "/boards", { boards: nb }).catch(toastError); }
    await loadBoards(); send("boards", { boards: S.boards }); selNodes[0].board = null; drawSelActions(); drawPlaceBtn();
    toast("Board removed; the frame stays on the canvas.", { kind: "success" });
  }
  async function register(key, spec) {
    S.boards = { ...S.boards, [key]: { src: spec.src, width: spec.width, height: spec.height } };
    try { await api("PUT", P_(pid) + "/boards", { boards: S.boards }); send("boards", { boards: S.boards }); drawPlaceBtn(); }
    catch (e) { toastError(e, "Couldn't register the board"); }
  }
  const off = bus.on("editor", async (d) => {
    if (d.source !== frame.contentWindow) return;
    const p = d.payload && typeof d.payload === "object" ? d.payload : d;
    if (scriptsUi.onMessage(d.type, p)) return;
    if (extras.onMessage(d.type, p)) return;
    switch (d.type) {
      case "td-editor:ready":
        ready = true; status.textContent = p.page_name ? `Canvas · ${p.page_name}` : "Canvas";
        docs.onReady(p.doc_id || null);
        if (S.deepNode) send("focus", { node_id: S.deepNode });
        else if (S.deepBoard && S.boards[S.deepBoard]) send("focus", { node_id: S.deepBoard });
        drawPlaceBtn();
        break;
      case "td-editor:viewport": vp = { x: +p.x || 0, y: +p.y || 0, zoom: +p.zoom || 1, canvas: p.canvas || vp.canvas }; place(); gaps.place(); scriptsUi.place(); extras.place(); break;
      case "td-editor:placeholders": gaps.setPlaceholders(Array.isArray(d.payload) ? d.payload : p.items || []); break;
      case "td-editor:present": gaps.present(p.node_id || null); break;
      case "td-editor:frames": frames = Array.isArray(d.payload) ? d.payload : Array.isArray(p.frames) ? p.frames : []; place(); break;
      case "td-editor:selection": {
        const ids = p.ids || [];
        S.deepNode = ids[0] || null;
        const boardKey = (p.nodes || []).map((n) => n.board).find(Boolean);
        if (boardKey && S.boards[boardKey]?.src) { S.deepBoard = boardKey; S.activeFile = S.boards[boardKey].src; if (!S.tabs.includes(S.activeFile)) S.tabs.push(S.activeFile); }
        writeUrl();
        bus.emit("editor-selection", p);
        selNodes = p.nodes || [];
        drawSelActions();
        gaps.onSelection(selNodes);
        scriptsUi.onSelection(selNodes);
        extras.onSelection(selNodes);
        break;
      }
      case "td-editor:saved": status.textContent = "Saved"; setTimeout(() => (status.textContent = "Canvas"), 1500); gaps.onSaved(); break;
      case "td-editor:board-marked":
      case "td-editor:frame-created": {
        const spec = pending.get(p.request_id) || (pending.size === 1 ? [...pending.values()][0] : null);
        if (!spec || !p.id) break;
        pending.delete(p.request_id);
        await register(p.id, spec);
        spec.done && spec.done(true);
        break;
      }
      case "td-editor:error":
        if (p.request_id && pending.has(p.request_id)) { pending.get(p.request_id).done?.(false); pending.delete(p.request_id); }
        toast("Canvas: " + (p.message || "something went wrong"), { kind: "error" });
        break;
    }
  });
  const offMode = bus.on("mode", place);
  const offReload = bus.on("reload-previews", (paths) => { for (const o of overlays.values()) if (!paths.length || paths.includes(o.src)) o.iframe.src = previewUrl(pid, o.src, "&r=" + Date.now()); });
  const offBoards = bus.on("boards", () => { send("boards", { boards: S.boards }); drawPlaceBtn(); });
  const offFiles = bus.on("files-changed", drawPlaceBtn);
  const offFocus = bus.on("canvas-focus", (key) => send("focus", { node_id: key }));
  const ro = new ResizeObserver(() => send("get-state"));
  ro.observe(body);
  loadBoards();
  return () => { off(); offMode(); offReload(); offBoards(); offFiles(); offFocus(); ro.disconnect(); gaps.cleanup(); docs.cleanup(); scriptsUi.cleanup(); extras.cleanup(); tb._cleanup && tb._cleanup(); for (const o of overlays.values()) unregisterFrame(o.iframe); };
}

// ── Canvas parity: Convert, Preview, Tokens, Slides/Present, "working…" ─────
// (docs/teledesign-parity.md gaps #1 #3 #4 #10 #14; editor side in
// patches/open-pencil/0008-0011, server side in proxy/api_design_editor.py.)
const GAPS_CSS = `
.td-ph { position: absolute; transform-origin: 0 0; pointer-events: none; border: 2px dashed var(--accent, #6b5cff); border-radius: 4px;
  background: repeating-linear-gradient(135deg, rgba(107,92,255,.10) 0 10px, rgba(107,92,255,.02) 10px 20px); }
.td-ph .td-ph-badge { position: absolute; left: 6px; top: 6px; display: inline-flex; align-items: center; gap: 6px; padding: 3px 9px 3px 7px;
  border-radius: 999px; background: var(--accent, #6b5cff); color: #fff; font: 600 11.5px/1.4 system-ui, sans-serif; white-space: nowrap; box-shadow: 0 2px 8px rgba(0,0,0,.18); }
.td-ph .td-ph-badge i { width: 7px; height: 7px; border-radius: 50%; background: #fff; animation: td-ph-pulse 1.1s ease-in-out infinite; }
.td-ph.stale { border-color: #9aa1ad; background: repeating-linear-gradient(135deg, rgba(154,161,173,.12) 0 10px, transparent 10px 20px); }
.td-ph.stale .td-ph-badge { background: #6b7280; } .td-ph.stale .td-ph-badge i { animation: none; }
@keyframes td-ph-pulse { 50% { opacity: .35; } }
@media (prefers-reduced-motion: reduce) { .td-ph .td-ph-badge i { animation: none; } }
.td-dock { position: absolute; z-index: 6; background: var(--panel, #fff); color: var(--text, #1d1f23); box-shadow: var(--shadow-pop, 0 8px 28px rgba(0,0,0,.18)); border: 1px solid var(--border, rgba(0,0,0,.1)); display: flex; flex-direction: column; }
.td-dock header { display: flex; align-items: center; gap: 6px; padding: 6px 8px; border-bottom: 1px solid var(--border, rgba(0,0,0,.08)); font-size: 12.5px; color: var(--text, inherit); }
.td-dock header .faint { color: var(--muted, #6b7280); }
.td-dock header b { font-weight: 600; } .td-dock header .grow { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.td-lpv { right: 12px; top: 12px; bottom: 12px; width: min(46%, 760px); border-radius: 10px; overflow: hidden; }
.td-lpv .td-lpv-body { flex: 1; position: relative; background: repeating-conic-gradient(rgba(0,0,0,.04) 0 25%, transparent 0 50%) 0 0 / 16px 16px; overflow: auto; }
.td-lpv .td-lpv-body iframe { border: 0; background: #fff; transform-origin: 0 0; position: absolute; left: 0; top: 0; }
.td-slides { left: 12px; right: 12px; bottom: 12px; height: 172px; border-radius: 10px; }
.td-slides .td-strip { flex: 1; display: flex; gap: 12px; padding: 10px 12px; overflow-x: auto; overflow-y: hidden; align-items: flex-start; }
.td-slide { flex: none; width: 168px; cursor: pointer; border-radius: 6px; padding: 4px; border: 2px solid transparent; user-select: none; }
.td-slide:hover { background: var(--hover, rgba(0,0,0,.04)); } .td-slide .cap { color: var(--text, inherit); } .td-slide.on { border-color: var(--accent, #6b5cff); }
.td-slide.drop-before { box-shadow: -4px 0 0 var(--accent, #6b5cff); } .td-slide.drop-after { box-shadow: 4px 0 0 var(--accent, #6b5cff); }
.td-slide .th { height: 92px; border-radius: 4px; background: var(--panel2, #eceef2) center / contain no-repeat; position: relative; border: 1px solid rgba(0,0,0,.08); }
.td-slide .th input { position: absolute; left: 4px; top: 4px; margin: 0; }
.td-slide .cap { display: flex; gap: 6px; font-size: 11.5px; margin-top: 4px; align-items: baseline; } .td-slide .cap span:last-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.td-slides .td-empty { padding: 22px; font-size: 13px; opacity: .7; }
.td-present { position: fixed; inset: 0; z-index: 1000; background: #0b0c0e; display: grid; place-items: center; outline: none; }
.td-present img { width: 100vw; height: 100vh; object-fit: contain; display: block; }
.td-present img:not([src]) { display: none; }
.td-present .td-pbar { position: absolute; left: 50%; bottom: 18px; transform: translateX(-50%); display: flex; gap: 6px; align-items: center; padding: 6px 10px;
  border-radius: 999px; background: rgba(20,22,26,.72); color: #e8eaee; font: 12.5px system-ui, sans-serif; opacity: 0; transition: opacity .2s; }
.td-present:hover .td-pbar, .td-present .td-pbar:focus-within { opacity: 1; }
.td-present .td-pbar .btn { color: inherit; } .td-present .td-wait { color: #8a93a1; font: 13px system-ui, sans-serif; }
.td-diff { max-height: 50vh; overflow: auto; font-size: 12.5px; } .td-diff table { border-collapse: collapse; width: 100%; }
.td-diff td { padding: 4px 6px; border-bottom: 1px solid var(--border, rgba(0,0,0,.08)); vertical-align: top; } .td-diff code { font-size: 11.5px; }
.td-diff .sw { display: inline-block; width: 12px; height: 12px; border-radius: 3px; vertical-align: -2px; margin-right: 4px; border: 1px solid rgba(0,0,0,.15); }
`;

function ensureGapsCss() {
  if (document.getElementById("td-canvas-gaps-css")) return;
  document.head.appendChild(h("style", { id: "td-canvas-gaps-css" }, GAPS_CSS));
}

const TURN_OVER = new Set(["done", "failed", "cancelled", "error", "stopped"]);

function mountCanvasGaps({ pid, body, bar, layer, send, vp, selection }) {
  ensureGapsCss();
  const callTool = async (tool, args = {}, timeout = 30) => {
    const r = await api("POST", P_(pid) + "/editor/call", { tool, args, timeout }, { feature: "editorCall" });
    return r.result;
  };
  const E = P_(pid) + "/editor";

  // Toolbar: contextual convert/preview, then Slides and Tokens.
  const toLayersBtn = btn("To layers", { icon: "layers", kind: "ghost", cls: "sm hidden", title: "Convert this HTML board into editable canvas layers (a new frame beside it)", onClick: () => convertToLayers() });
  const toHtmlBtn = btn("To HTML", { icon: "code", kind: "ghost", cls: "sm hidden", title: "Export this frame as an HTML page and place it as a live HTML board", onClick: () => convertToHtml() });
  const previewBtn = btn("HTML preview", { icon: "eye", kind: "ghost", cls: "sm hidden", title: "Show this layer board as its HTML export, refreshed on every save", onClick: () => togglePreview(true) });
  const slidesBtn = btn("Slides", { icon: "present", kind: "ghost", cls: "sm", title: "Top-level frames as slides: reorder, present (Ctrl+Enter), export PDF", onClick: () => toggleSlides() });
  const tokensBtn = btn("Tokens", { icon: "palette", kind: "ghost", cls: "sm", title: "Sync the design system's tokens with canvas variables", onClick: (e) => tokensMenu(e.currentTarget) });
  const anchor = bar.querySelector(".grow");
  anchor.after(toLayersBtn, toHtmlBtn, previewBtn);
  bar.append(slidesBtn, tokensBtn);

  let sel = [];
  function onSelection(nodes) {
    sel = nodes || [];
    const one = sel.length === 1 ? sel[0] : null;
    const isBoard = !!(one && one.board && S.boards[one.board]);
    const isFrame = !!(one && /FRAME|COMPONENT|SECTION|INSTANCE|GROUP/.test(one.type || ""));
    toLayersBtn.classList.toggle("hidden", !isBoard);
    toHtmlBtn.classList.toggle("hidden", !(isFrame && !isBoard));
    previewBtn.classList.toggle("hidden", !(isFrame && !isBoard));
    if (slides.open) slides.highlight(sel.map((n) => n.id));
  }

  // ── #1 Convert ─────────────────────────────────────────────────────────
  async function convertToLayers() {
    const n = sel[0];
    if (!n?.board) return;
    const src = S.boards[n.board]?.src || "the page";
    const stop = toast(`Converting ${src} to layers…`, { ms: 60000 });
    toLayersBtn.disabled = true;
    try {
      const r = await api("POST", E + "/convert", { direction: "to-layers", key: n.board }, { feature: "editorConvert" });
      stop();
      toast(`${src} → ${r.nodes} layer${r.nodes === 1 ? "" : "s"}${r.truncated ? " (truncated — very large page)" : ""}`, { kind: "success" });
      if (r.id) send("focus", { node_id: r.id });
    } catch (e) { stop(); toastError(e, "Convert to layers failed"); }
    finally { toLayersBtn.disabled = false; }
  }
  async function convertToHtml() {
    const n = sel[0];
    if (!n) return;
    const suggested = (String(n.name || "frame").toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "frame") + ".html";
    const path = await promptDialog("Convert to HTML", { label: `Save "${n.name}" as a project page`, value: suggested, confirm: "Convert" });
    if (!path) return;
    if (!/^[\w./ -]+\.html$/i.test(path) || path.includes("..") || path.startsWith(".")) { toast("Use a project-relative .html path.", { kind: "error" }); return; }
    toHtmlBtn.disabled = true;
    try {
      const r = await api("POST", E + "/convert", { direction: "to-html", node_id: n.id, path }, { feature: "editorConvert" });
      const { loadFiles } = await import("./state.js");
      await loadFiles(); await loadBoards(); bus.emit("files-changed"); send("boards", { boards: S.boards });
      toast(`Saved ${r.path} and placed it as an HTML board`, { kind: "success", action: { label: "Open", run: () => openFile(r.path, { view: "preview" }) } });
      if (r.node_id) send("focus", { node_id: r.board?.key || r.node_id });
    } catch (e) {
      // A path that already exists: say so plainly.
      toastError(e, "Convert to HTML failed");
    } finally { toHtmlBtn.disabled = false; }
  }

  // ── #14 Preview view of a layer board ──────────────────────────────────
  const pv = { open: false, node: null, el: null, frame: null, path: null, busy: false, again: false };
  function togglePreview(open) {
    if (!open) { closePreview(); return; }
    const n = sel[0];
    if (!n) return;
    pv.node = { id: n.id, name: n.name, width: n.width, height: n.height };
    if (!pv.el) {
      const title = h("span", { class: "grow" });
      const bodyEl = h("div", { class: "td-lpv-body" });
      pv.el = h("section", { class: "td-dock td-lpv", role: "region", "aria-label": "Layer board preview" },
        h("header", null, icon("eye"), title,
          btn("", { kind: "quiet", icon: "refresh", cls: "sm", title: "Re-export now", onClick: () => refreshPreview() }),
          btn("", { kind: "quiet", icon: "external", cls: "sm", title: "Open in a new tab", onClick: () => pv.path && window.open(previewUrl(pid, pv.path), "_blank", "noopener") }),
          btn("", { kind: "quiet", icon: "x", cls: "sm", title: "Close preview", onClick: () => closePreview() })),
        bodyEl);
      pv.title = title; pv.body = bodyEl;
      new ResizeObserver(() => fitPreview()).observe(bodyEl);
      body.appendChild(pv.el);
      placeDocks();
    }
    pv.open = true;
    pv.title.textContent = `${pv.node.name} — HTML export (updates on save)`;
    refreshPreview();
  }
  function fitPreview() {
    if (!pv.frame || !pv.body) return;
    const w = +pv.frame.dataset.w || 1440, hh = +pv.frame.dataset.h || 900;
    const s = Math.min(1, (pv.body.clientWidth - 2) / w);
    pv.frame.style.width = w + "px"; pv.frame.style.height = hh + "px";
    pv.frame.style.transform = `scale(${s})`;
  }
  async function refreshPreview() {
    if (!pv.open || !pv.node) return;
    if (pv.busy) { pv.again = true; return; }
    pv.busy = true;
    try {
      const r = await api("POST", E + "/layer-preview", { node_id: pv.node.id }, { feature: "editorPreview" });
      if (r.missing) { closePreview(); toast("The previewed frame is no longer on the canvas."); return; }
      pv.path = r.path;
      const url = previewUrl(pid, r.path, "&r=" + Date.now());
      if (!pv.frame) {
        pv.frame = makePreviewFrame(url, { file: r.path, role: "board", title: "Layer board preview" });
        pv.body.appendChild(pv.frame);
      } else pv.frame.src = url;
      pv.frame.dataset.w = Math.round(+r.width || 1440); pv.frame.dataset.h = Math.round(+r.height || 900);
      fitPreview();
    } catch (e) {
      toastError(e, "Preview failed");
    } finally {
      pv.busy = false;
      if (pv.again) { pv.again = false; refreshPreview(); }
    }
  }
  function closePreview() {
    pv.open = false;
    if (pv.frame) { unregisterFrame(pv.frame); pv.frame = null; }
    pv.el?.remove(); pv.el = null;
  }
  const refreshPreviewSoon = (() => { let t; return () => { clearTimeout(t); t = setTimeout(refreshPreview, 400); }; })();

  // ── #3 Tokens ↔ variables ──────────────────────────────────────────────
  async function tokensMenu(anchorEl) {
    let info = null;
    try { info = await api("GET", E + "/tokens", undefined, { feature: "editorTokens" }); } catch (e) { toastError(e); return; }
    if (!info.available) {
      menu(anchorEl, [{ heading: "Tokens ↔ canvas variables" }, { label: info.error || "No tokens in this project", disabled: true }], { width: "300px", align: "right" });
      return;
    }
    const cols = (info.collections || []).map((c) => `${c.name} (${c.variables})`).join(", ");
    menu(anchorEl, [
      { heading: `Tokens: ${info.json}${info.staged ? " (design system copy)" : ""}` },
      { label: "Push tokens → canvas variables", icon: "upload", hint: `${info.count} variables · ${cols}`, onClick: () => pushTokens() },
      { label: "Pull canvas variables → tokens", icon: "download", hint: `Writes ${info.json}${info.css ? " + " + info.css : ""}`, onClick: () => pullTokens(info) },
    ], { width: "340px", align: "right" });
  }
  async function pushTokens() {
    try {
      const r = await api("POST", E + "/tokens/push", {}, { feature: "editorTokens" });
      const rep = r.report || {};
      toast(`Canvas variables: ${rep.created || 0} created, ${rep.updated || 0} updated, ${rep.unchanged || 0} unchanged` +
        (rep.collections_created ? ` · ${rep.collections_created} new collection${rep.collections_created > 1 ? "s" : ""}` : "") +
        (r.skipped?.length ? ` · ${r.skipped.length} token${r.skipped.length > 1 ? "s" : ""} not mirrored (aliases)` : ""), { kind: "success", ms: 6000 });
    } catch (e) { toastError(e, "Push failed"); }
  }
  async function pullTokens(info) {
    try {
      const r = await api("POST", E + "/tokens/pull", {}, { feature: "editorTokens" });
      if (!r.diff?.length) { toast("Tokens already match the canvas variables.", { kind: "success" }); return; }
      const row = (d) => {
        const hex = /^#[0-9a-f]{6,8}$/i;
        return h("tr", null,
          h("td", null, h("code", null, d.token)),
          h("td", null, d.before != null ? h("span", null, hex.test(d.before) ? h("i", { class: "sw", style: { background: d.before } }) : null, String(d.before)) : h("em", { class: "faint" }, "new")),
          h("td", null, "→"),
          h("td", null, hex.test(d.after) ? h("i", { class: "sw", style: { background: d.after } }) : null, String(d.after)));
      };
      modal({
        title: "Canvas variables written to tokens", width: "620px",
        subtitle: `${r.diff.length} change${r.diff.length > 1 ? "s" : ""} → ${r.written.join(", ")}${r.staged ? " — this is the project's copy of its design system; restaging the system replaces it" : ""}`,
        body: h("div", { class: "td-diff" }, h("table", null, r.diff.map(row)),
          r.css_missing?.length ? h("p", { class: "faint" }, `Not declared in ${info.css || "tokens.css"} (tokens.json only): ${r.css_missing.join(", ")}`) : null),
        actions: [{ label: "Done", kind: "primary" }],
      });
      bus.emit("reload-previews", []);
    } catch (e) { toastError(e, "Pull failed"); }
  }

  // ── #4 Slides panel + Present ──────────────────────────────────────────
  const slides = { open: false, el: null, strip: null, list: [], checked: new Set(), thumbs: new Map(), drag: null, sel: new Set() };
  async function thumb(id, maxEdge = 320) {
    const key = id + "@" + maxEdge;
    if (slides.thumbs.has(key)) return slides.thumbs.get(key);
    const p = callTool("export_image", { ids: [id], format: "PNG", scale: 1, maxEdge }, 60)
      .then((r) => (r && r.base64 ? `data:${r.mimeType || "image/png"};base64,${r.base64}` : null)).catch(() => null);
    slides.thumbs.set(key, p);
    return p;
  }
  function toggleSlides(force) {
    const open = force ?? !slides.open;
    if (!open) { slides.el?.remove(); slides.el = null; slides.open = false; slidesBtn.classList.remove("on"); placeDocks(); return; }
    slides.open = true; slidesBtn.classList.add("on");
    slides.sel = new Set(sel.map((n) => n.id));
    const count = h("span", { class: "faint" });
    slides.count = count;
    slides.strip = h("div", { class: "td-strip", role: "listbox", "aria-label": "Slides", tabindex: 0 });
    slides.el = h("section", { class: "td-dock td-slides", role: "region", "aria-label": "Slides" },
      h("header", null, icon("present"), h("b", null, "Slides"), count, h("span", { class: "grow" }),
        btn("Present", { kind: "primary", icon: "play", cls: "sm", title: "Present from the selected slide  (Ctrl+Enter)", onClick: () => present() }),
        btn("PDF", { kind: "ghost", icon: "download", cls: "sm", title: "Export slides to PDF (the ticked ones, or all)", onClick: () => exportPdf() }),
        btn("", { kind: "quiet", icon: "refresh", cls: "sm", title: "Refresh", onClick: () => loadSlides(true) }),
        btn("", { kind: "quiet", icon: "x", cls: "sm", title: "Close", onClick: () => toggleSlides(false) })),
      slides.strip);
    body.appendChild(slides.el);
    placeDocks();
    loadSlides();
  }
  async function loadSlides(fresh) {
    if (!slides.open) return;
    if (fresh) slides.thumbs.clear();
    try {
      const r = await api("GET", E + "/slides", undefined, { feature: "editorSlides" });
      slides.list = r.slides || [];
      drawSlides();
    } catch (e) {
      mount(slides.strip, h("div", { class: "td-empty" }, e.status === 409 ? "The canvas is still loading…" : "Couldn't list the slides: " + e.message));
    }
  }
  function drawSlides() {
    if (!slides.strip) return;
    slides.count.textContent = slides.list.length ? `${slides.list.length} top-level frame${slides.list.length === 1 ? "" : "s"}` : "";
    if (!slides.list.length) { mount(slides.strip, h("div", { class: "td-empty" }, "No top-level frames on this page. Every top-level frame is a slide.")); return; }
    for (const id of [...slides.checked]) if (!slides.list.some((s) => s.id === id)) slides.checked.delete(id);
    mount(slides.strip, slides.list.map((s, i) => {
      const th = h("div", { class: "th" },
        h("input", { type: "checkbox", title: "Include in PDF export", checked: slides.checked.has(s.id) || null,
          onclick: (e) => { e.stopPropagation(); e.target.checked ? slides.checked.add(s.id) : slides.checked.delete(s.id); } }));
      thumb(s.id).then((u) => { if (u) th.style.backgroundImage = `url("${u}")`; });
      const el = h("div", { class: "td-slide" + (slides.sel.has(s.id) ? " on" : ""), role: "option", draggable: "true", dataset: { id: s.id }, title: `${s.name} — ${Math.round(s.width)}×${Math.round(s.height)}` },
        th, h("div", { class: "cap" }, h("b", null, String(i + 1)), h("span", null, s.name)));
      el.addEventListener("click", () => { slides.sel = new Set([s.id]); highlight([s.id]); send("focus", { node_id: s.id }); });
      el.addEventListener("dblclick", () => present(s.id));
      el.addEventListener("dragstart", (e) => { slides.drag = s.id; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", s.id); });
      el.addEventListener("dragover", (e) => {
        if (!slides.drag || slides.drag === s.id) return;
        e.preventDefault();
        const r = el.getBoundingClientRect(); const after = e.clientX > r.left + r.width / 2;
        el.classList.toggle("drop-after", after); el.classList.toggle("drop-before", !after);
      });
      el.addEventListener("dragleave", () => el.classList.remove("drop-after", "drop-before"));
      el.addEventListener("drop", (e) => {
        e.preventDefault();
        const after = el.classList.contains("drop-after");
        el.classList.remove("drop-after", "drop-before");
        const from = slides.drag; slides.drag = null;
        if (!from || from === s.id) return;
        const ids = slides.list.map((x) => x.id).filter((x) => x !== from);
        ids.splice(ids.indexOf(s.id) + (after ? 1 : 0), 0, from);
        reorder(ids);
      });
      el.addEventListener("dragend", () => { slides.drag = null; });
      return el;
    }));
  }
  function highlight(ids) {
    slides.sel = new Set(ids);
    slides.strip?.querySelectorAll(".td-slide").forEach((el) => el.classList.toggle("on", slides.sel.has(el.dataset.id)));
  }
  slides.highlight = highlight;
  async function reorder(ids) {
    const prev = slides.list;
    slides.list = ids.map((id) => prev.find((s) => s.id === id)).filter(Boolean);
    drawSlides();
    try {
      const r = await api("POST", E + "/slides/order", { ids, arrange: true }, { feature: "editorSlides" });
      slides.list = r.slides || slides.list;
      slides.thumbs.clear();
      drawSlides();
    } catch (e) { slides.list = prev; drawSlides(); toastError(e, "Reorder failed"); }
  }
  async function exportPdf() {
    const ids = slides.list.map((s) => s.id).filter((id) => !slides.checked.size || slides.checked.has(id));
    if (!ids.length) { toast("No slides to export."); return; }
    const stop = toast(`Exporting ${ids.length} slide${ids.length > 1 ? "s" : ""} to PDF…`, { ms: 60000 });
    try {
      const res = await api("POST", E + "/slides/pdf", { ids }, { feature: "editorSlides", as: "response" });
      const blob = await res.blob();
      const cd = res.headers.get("content-disposition") || "";
      const name = /filename="([^"]+)"/.exec(cd)?.[1] || "slides.pdf";
      const a = h("a", { href: URL.createObjectURL(blob), download: name });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 30000);
      stop(); toast(`Downloaded ${name}`, { kind: "success" });
    } catch (e) { stop(); toastError(e, "PDF export failed"); }
  }
  async function present(startId) {
    if (document.querySelector(".td-present")) return;
    let list = slides.list;
    if (!list.length) {
      try { list = (await api("GET", E + "/slides", undefined, { feature: "editorSlides" })).slides || []; }
      catch (e) { toastError(e, "Can't present"); return; }
    }
    if (!list.length) { toast("There are no top-level frames to present."); return; }
    const selId = startId || [...slides.sel][0] || sel.map((n) => n.id).find((id) => list.some((s) => s.id === id));
    let index = Math.max(0, list.findIndex((s) => s.id === selId));
    const img = h("img", { alt: "" });
    const wait = h("div", { class: "td-wait" }, "Rendering…");
    const counter = h("span", { class: "mono", style: { minWidth: "64px", textAlign: "center" } });
    const root = h("div", { class: "td-present", role: "dialog", "aria-label": "Presentation", tabindex: -1 }, wait, img,
      h("div", { class: "td-pbar" },
        btn("", { kind: "quiet", icon: "chevronLeft", cls: "sm", title: "Previous  (←)", onClick: () => go(index - 1) }), counter,
        btn("", { kind: "quiet", icon: "chevronRight", cls: "sm", title: "Next  (→ / Space)", onClick: () => go(index + 1) }),
        btn("", { kind: "quiet", icon: "x", cls: "sm", title: "Exit  (Esc)", onClick: () => exit() })));
    document.body.appendChild(root);
    const edge = Math.min(4096, Math.max(1280, Math.round(Math.max(screen.width, screen.height) * (devicePixelRatio || 1))));
    let token = 0;
    async function go(i) {
      if (i < 0 || i >= list.length) return;
      index = i;
      counter.textContent = `${index + 1} / ${list.length}`;
      const mine = ++token;
      const u = await thumb(list[index].id, edge);
      if (mine !== token) return;
      if (u) { img.src = u; wait.textContent = ""; } else { img.removeAttribute("src"); wait.textContent = "This slide could not be rendered."; }
      img.alt = list[index].name || `Slide ${index + 1}`;
      if (list[index + 1]) thumb(list[index + 1].id, edge);   // preload the next one
    }
    const key = (e) => {
      if (e.key === "Escape") { e.preventDefault(); exit(); return; }
      if (["ArrowRight", "ArrowDown", "PageDown", " ", "Enter"].includes(e.key)) { e.preventDefault(); go(index + 1); }
      else if (["ArrowLeft", "ArrowUp", "PageUp", "Backspace"].includes(e.key)) { e.preventDefault(); go(index - 1); }
      else if (e.key === "Home") { e.preventDefault(); go(0); }
      else if (e.key === "End") { e.preventDefault(); go(list.length - 1); }
    };
    addEventListener("keydown", key, true);
    root.addEventListener("click", (e) => { if (e.target === img || e.target === root) go(index + 1); });
    const onFs = () => { if (!document.fullscreenElement) exit(); };
    try { await root.requestFullscreen?.(); document.addEventListener("fullscreenchange", onFs); } catch { /* not allowed: the overlay still covers the window */ }
    root.focus();
    go(index);
    function exit() {
      removeEventListener("keydown", key, true); document.removeEventListener("fullscreenchange", onFs);
      root.remove();
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      if (list[index]) send("focus", { node_id: list[index].id });
    }
  }
  const presentKey = (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.shiftKey && S.view === "canvas" && !isTyping(e)) { e.preventDefault(); present(); }
  };
  addEventListener("keydown", presentKey);

  // ── #10 "working…" placeholder frames ──────────────────────────────────
  let placeholders = [];
  let firstPlaceholders = true;
  const phEls = new Map();
  function setPlaceholders(items) {
    placeholders = Array.isArray(items) ? items : [];
    // Marks found when the canvas opens with no turn running were left by a turn
    // that ended while the canvas was closed: clear them rather than show them.
    if (firstPlaceholders && placeholders.length && !chatRunning()) { firstPlaceholders = false; clearPlaceholders(); }
    firstPlaceholders = false;
    place();
  }
  function place() {
    const v = vp();
    const c = v.canvas || { left: 0, top: 0 };
    for (const p of placeholders) {
      let el = phEls.get(p.node_id);
      if (!el) {
        el = h("div", { class: "td-ph" }, h("span", { class: "td-ph-badge" }, h("i"), h("span")));
        layer.appendChild(el); phEls.set(p.node_id, el);
      }
      el.classList.toggle("stale", !!p.stale);
      el.querySelector(".td-ph-badge span").textContent = p.stale ? `${p.label || "Working…"} (stalled)` : p.label || "Working…";
      el.title = p.stale ? "Marked as in progress by an agent turn that has ended" : `${p.name || "Frame"} is being built by the agent`;
      el.style.left = c.left + p.x * v.zoom + v.x + "px"; el.style.top = c.top + p.y * v.zoom + v.y + "px";
      el.style.width = Math.max(4, p.width * v.zoom) + "px"; el.style.height = Math.max(4, p.height * v.zoom) + "px";
    }
    for (const [id, el] of phEls) if (!placeholders.some((p) => p.node_id === id)) { el.remove(); phEls.delete(id); }
    placeDocks();
  }
  async function clearPlaceholders() {
    if (!placeholders.length) return;
    try { await callTool("telecode_placeholder_clear", {}, 20); } catch { /* the editor clears stale marks itself */ }
  }
  // A turn that ends takes its "working…" marks with it.
  const offTurn = bus.on("ev:turn", (t) => {
    if (!t || !TURN_OVER.has(t.status)) return;
    if (t.role && t.role !== "assistant" && t.role !== "user") return;
    setTimeout(() => { if (!chatRunning(t.chat_id || S.chatId)) clearPlaceholders(); }, 300);
  });

  // Docks sit over the editor's canvas, not over its layers / properties panels.
  function placeDocks() {
    const c = vp().canvas;
    if (!c || !c.width) return;
    const right = Math.max(12, body.clientWidth - (c.left + c.width) + 12);
    const left = c.left + 12;
    const bottom = Math.max(12, body.clientHeight - (c.top + c.height) + 12);
    if (pv.el) { pv.el.style.right = right + "px"; pv.el.style.top = c.top + 12 + "px"; pv.el.style.bottom = bottom + (slides.el ? slides.el.offsetHeight + 12 : 0) + "px";
      pv.el.style.width = `min(${Math.round(c.width * 0.55)}px, 760px)`; }
    if (slides.el) { slides.el.style.left = left + "px"; slides.el.style.right = right + "px"; slides.el.style.bottom = bottom + "px"; }
  }

  function onSaved() {
    if (pv.open) refreshPreviewSoon();
    if (slides.open) { clearTimeout(slides.t); slides.t = setTimeout(() => loadSlides(true), 600); }
  }

  return {
    place, setPlaceholders, onSelection, onSaved, present,
    cleanup() { removeEventListener("keydown", presentKey); offTurn(); closePreview(); toggleSlides(false); for (const el of phEls.values()) el.remove(); },
  };
}
