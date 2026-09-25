// TeleDesign — Canvas view.
//
// With the open-pencil build present (/design/editor/index.html) this hosts the
// editor iframe and overlays each registered HTML board (boards.json) on its
// frame, tracking td-editor:viewport / td-editor:frames. Without it, a built-in
// pan/zoom canvas lays every HTML file out as a board.
//
// The editor protocol is documented above renderEditor().
import { h, icon, btn, mount, clear, bus, prefs, api, toast, toastError, emptyState, P_, isHtml, promptDialog } from "./core.js";
import { S, htmlFiles, assetFor, previewUrl, loadBoards, writeUrl } from "./state.js";
import { makePreviewFrame, unregisterFrame, post } from "./bridge.js";
import { modeToolbar, openFile, setView } from "./workspace.js";

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
  return r;
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
  const frame = h("iframe", { class: "editor-frame", src: "/design/editor/?" + new URLSearchParams({ project: pid }), title: "Canvas editor", allow: "clipboard-read; clipboard-write" });
  const layer = h("div", { class: "overlay-layer" });
  mount(body, frame, layer);
  const send = (type, payload = {}) => { try { frame.contentWindow.postMessage({ type: "td-editor:" + type, payload }, location.origin); } catch {} };

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
    switch (d.type) {
      case "td-editor:ready":
        ready = true; status.textContent = p.page_name ? `Canvas · ${p.page_name}` : "Canvas";
        if (S.deepNode) send("focus", { node_id: S.deepNode });
        else if (S.deepBoard && S.boards[S.deepBoard]) send("focus", { node_id: S.deepBoard });
        drawPlaceBtn();
        break;
      case "td-editor:viewport": vp = { x: +p.x || 0, y: +p.y || 0, zoom: +p.zoom || 1, canvas: p.canvas || vp.canvas }; place(); break;
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
        break;
      }
      case "td-editor:saved": status.textContent = "Saved"; setTimeout(() => (status.textContent = "Canvas"), 1500); break;
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
  return () => { off(); offMode(); offReload(); offBoards(); offFiles(); offFocus(); ro.disconnect(); tb._cleanup && tb._cleanup(); for (const o of overlays.values()) unregisterFrame(o.iframe); };
}
