// TeleDesign — Preview view: tab bar of open files, device/viewport switcher,
// zoom, interaction modes, deck controls + speaker notes + presenter, Tweaks
// toggle and the page console.
import { h, icon, btn, mount, clear, bus, prefs, menu, tryApi, P_, encPath, emptyState, isHtml, toast } from "./core.js";
import { S, previewUrl, htmlFiles, writeUrl } from "./state.js";
import { makePreviewFrame, unregisterFrame, post } from "./bridge.js";
import { modeToolbar, openFile, closeTab, setMode } from "./workspace.js";
import { live, toggleTweaks, undoStep, refreshUndo, undoInfo } from "./interact.js";
import { boardSize } from "./canvas.js";
import { features } from "./core.js";

// Relay the live preview console to the host (POST …/console/live), so an agent's
// design_get_console(source="live") reads what the user's own preview logged.
const consoleRelayed = new WeakSet();
const consoleRelayFiles = new Set();
let consoleRelayTimer = 0;
bus.on("console", (file) => {
  consoleRelayFiles.add(file);
  if (!consoleRelayTimer) consoleRelayTimer = setTimeout(flushConsoleRelay, 400);
});
function flushConsoleRelay() {
  consoleRelayTimer = 0;
  const files = [...consoleRelayFiles];
  consoleRelayFiles.clear();
  if (!S.project || features.consoleRelay === false) return;
  for (const file of files) {
    const entries = (live.console.get(file) || []).filter((l) => !consoleRelayed.has(l));
    entries.forEach((l) => consoleRelayed.add(l));
    if (entries.length) tryApi("POST", P_(S.project.id) + "/console/live", { file, entries: entries.slice(-200) }, { feature: "consoleRelay" }).catch(() => {});
  }
}

const DEVICES = [
  { id: "fit", label: "Fill", icon: "fit", w: 0, h: 0 },
  { id: "desktop", label: "Desktop", icon: "desktop", w: 1440, h: 900 },
  { id: "laptop", label: "Laptop", icon: "desktop", w: 1280, h: 800 },
  { id: "tablet", label: "Tablet", icon: "tablet", w: 834, h: 1112 },
  { id: "mobile", label: "Mobile", icon: "phone", w: 390, h: 844 },
];
const ZOOMS = ["fit", 0.5, 0.75, 1, 1.25];

const notesCache = new Map();   // file -> {mtime, notes:[]}
export async function speakerNotes(file) {
  if (!file || !isHtml(file)) return [];
  const meta = S.files.find((f) => f.path === file);
  const c = notesCache.get(file);
  if (c && c.mtime === meta?.mtime) return c.notes;
  const src = await tryApi("GET", P_(S.project.id) + "/files/" + encPath(file), undefined, { as: "text" }).catch(() => null);
  let notes = [];
  if (typeof src === "string") {
    const m = src.match(/<script[^>]*id=["'](?:td-)?speaker-notes["'][^>]*>([\s\S]*?)<\/script>/i);
    if (m) { try { const j = JSON.parse(m[1]); if (Array.isArray(j)) notes = j.map((x) => (typeof x === "string" ? x : x?.text || JSON.stringify(x))); } catch { /* not JSON */ } }
  }
  notesCache.set(file, { mtime: meta?.mtime, notes });
  return notes;
}

export function render(body, bar) {
  const pid = S.project.id;
  let device = prefs.get("device:" + pid, null);
  let zoom = prefs.get("zoom", "fit");
  let showConsole = false, showNotes = prefs.get("notes", true);
  let frame = null;
  const offs = [];

  // ── Bar ────────────────────────────────────────────────────────────────
  const tb = modeToolbar();
  const devSeg = h("div", { class: "seg icons dev-seg", role: "group", "aria-label": "Viewport" });
  // Phones: the viewport presets as one compact menu button.
  const devBtn = h("button", { class: "pick dev-compact", title: "Viewport", "aria-label": "Viewport" });
  const zoomBtn = h("button", { class: "pick", title: "Zoom" });
  const deckBox = h("div", { class: "row", style: { gap: "2px" } });
  const tweaksBtn = btn("Tweaks", { icon: "sliders", cls: "sm hidden", title: "Show the page's live controls", onClick: () => { const t = live.tweaks.get(S.activeFile); toggleTweaks(S.activeFile, !(t && t.on)); } });
  const notesBtn = btn("", { kind: "quiet", icon: "notes", cls: "sm hidden", title: "Speaker notes", onClick: () => { showNotes = !showNotes; prefs.set("notes", showNotes); drawBody(); } });
  const consoleBtn = h("button", { class: "btn quiet sm", title: "Console", onclick: () => { showConsole = !showConsole; drawBody(); } });
  const presentBtn = btn("Present", { icon: "present", cls: "sm hidden present-btn", title: "Present", onClick: () => present() });
  // Undo / redo = step the active file back / forward through its versions.
  const undoBtn = btn("", { kind: "quiet", icon: "undo", cls: "sm", title: "Undo — previous version of this page  (Ctrl+Z)", onClick: () => S.activeFile && undoStep(S.activeFile, "undo") });
  const redoBtn = btn("", { kind: "quiet", icon: "redo", cls: "sm", title: "Redo  (Ctrl+Shift+Z)", onClick: () => S.activeFile && undoStep(S.activeFile, "redo") });
  const drawUndo = () => {
    const u = undoInfo.get(S.activeFile) || {};
    undoBtn.disabled = !u.can_undo; redoBtn.disabled = !u.can_redo;
    const gone = features.undo === false || !S.activeFile || !isHtml(S.activeFile);
    undoBtn.classList.toggle("hidden", gone); redoBtn.classList.toggle("hidden", gone);
  };
  bar.append(tb, h("span", { class: "divider-v" }), devSeg, devBtn, zoomBtn, deckBox, h("span", { class: "grow" }), undoBtn, redoBtn, tweaksBtn, notesBtn, consoleBtn,
    btn("", { kind: "quiet", icon: "refresh", cls: "sm", title: "Reload  (Ctrl+R)", onClick: () => reload() }),
    btn("", { kind: "quiet", icon: "external", cls: "sm", title: "Open in a new tab", onClick: () => S.activeFile && window.open(previewUrl(pid, S.activeFile), "_blank", "noopener") }),
    presentBtn);

  const tabbar = h("div", { class: "tabbar", role: "tablist", "aria-label": "Open files" });
  const area = h("div", { class: "preview-area" }, tabbar);
  mount(body, area);
  const scroll = h("div", { class: "preview-scroll" });
  const notesPane = h("div", { class: "notes-pane hidden" });
  const consolePane = h("div", { class: "console hidden" });
  area.append(scroll, notesPane, consolePane);

  function defaultDevice() {
    if (!S.activeFile) return DEVICES[0];
    const [w, hh] = boardSize(S.activeFile);
    return { id: "board", label: `${w}×${hh}`, icon: "canvas", w, h: hh };
  }
  const curDevice = () => DEVICES.find((d) => d.id === device) || (device === "board" || !device ? defaultDevice() : DEVICES[0]);

  function drawBar() {
    const cur = curDevice();
    const board = defaultDevice();
    const list = [board.id === "board" ? board : null, ...DEVICES].filter(Boolean);
    const pickDev = (d) => { device = d.id; prefs.set("device:" + pid, device); drawBody(); drawBar(); };
    mount(devSeg, list.map((d) => h("button", { class: cur.id === d.id ? "on" : "", title: `${d.label}${d.w ? ` — ${d.w}×${d.h}` : ""}`, onclick: () => pickDev(d) }, icon(d.icon), d.id === "board" ? h("span", { style: { fontSize: "11px" } }, d.label) : null)));
    mount(devBtn, icon(cur.icon), h("span", { class: "lbl" }, cur.label), icon("chevronDown"));
    devBtn.onclick = (e) => menu(e.currentTarget, [{ heading: "Viewport" }, ...list.map((d) => ({ label: d.id === "board" ? `Board — ${d.label}` : d.label, hint: d.w ? `${d.w} × ${d.h}` : "The page at this screen's width", icon: d.icon, checked: cur.id === d.id, onClick: () => pickDev(d) }))]);
    mount(zoomBtn, zoom === "fit" ? "Fit" : Math.round(zoom * 100) + "%", icon("chevronDown"));
    zoomBtn.onclick = (e) => menu(e.currentTarget, ZOOMS.map((z) => ({ label: z === "fit" ? "Fit to window" : Math.round(z * 100) + "%", icon: z === zoom ? "check" : null, onClick: () => { zoom = z; prefs.set("zoom", z); drawBody(); drawBar(); } })));
    zoomBtn.classList.toggle("hidden", cur.id === "fit");
    const tw = live.tweaks.get(S.activeFile);
    tweaksBtn.classList.toggle("hidden", !(tw && tw.available));
    tweaksBtn.classList.toggle("on", !!(tw && tw.on));
    const errs = (live.console.get(S.activeFile) || []).filter((l) => l.level === "error").length;
    mount(consoleBtn, icon("terminal"), errs ? h("span", { class: "count err" }, errs) : null);
    consoleBtn.classList.toggle("on", showConsole);
    const sl = live.slides.get(S.activeFile);
    if (sl && sl.count) {
      mount(deckBox, h("span", { class: "divider-v" }),
        btn("", { kind: "quiet", icon: "chevronLeft", cls: "sm", title: "Previous slide  (←)", onClick: () => slide("prev") }),
        h("span", { class: "mono", style: { minWidth: "52px", textAlign: "center", color: "var(--muted)" } }, `${sl.index} / ${sl.count}`),
        btn("", { kind: "quiet", icon: "chevronRight", cls: "sm", title: "Next slide  (→)", onClick: () => slide("next") }));
      presentBtn.classList.remove("hidden");
    } else { clear(deckBox); presentBtn.classList.add("hidden"); }
    speakerNotes(S.activeFile || "").then((n) => { notesBtn.classList.toggle("hidden", !n.length); notesBtn.classList.toggle("on", showNotes && !!n.length); });
  }
  function slide(action, index) { if (frame) post(frame, { type: "td:slide", action, index }); }

  function drawTabs() {
    mount(tabbar, S.tabs.map((p) => h("div", { class: "ptab" + (p === S.activeFile ? " on" : ""), role: "tab", tabindex: 0, title: p, "aria-selected": p === S.activeFile ? "true" : "false",
      onclick: () => { if (p !== S.activeFile) { S.activeFile = p; writeUrl(); drawAll(); } },
      onauxclick: (e) => { if (e.button === 1) closeTab(p); } },
      icon(isHtml(p) ? "canvas" : "file"), p,
      h("button", { class: "x", "aria-label": "Close " + p, onclick: (e) => { e.stopPropagation(); closeTab(p); } }, icon("x")))),
    btn("", { kind: "quiet", icon: "plus", cls: "sm", title: "Open a file", onClick: (e) => menu(e.currentTarget, htmlFiles().filter((f) => !S.tabs.includes(f.path)).map((f) => ({ label: f.path, icon: "canvas", onClick: () => openFile(f.path, { view: "preview" }) })).concat(htmlFiles().every((f) => S.tabs.includes(f.path)) ? [{ label: "All pages are open", disabled: true }] : [])) }));
  }

  function drawBody() {
    if (frame) { unregisterFrame(frame); frame = null; }
    clear(scroll);
    const file = S.activeFile;
    if (!file || !isHtml(file)) {
      mount(scroll, h("div", { style: { margin: "auto" } }, emptyState("eye", htmlFiles().length ? "Pick a page to preview" : "Nothing to preview yet",
        htmlFiles().length ? "Open a page from the files list or the + above." : "Once the agent writes an HTML page, it shows up here.",
        htmlFiles()[0] ? btn("Open " + htmlFiles()[0].path, { onClick: () => openFile(htmlFiles()[0].path, { view: "preview" }) }) : null)));
      notesPane.classList.add("hidden"); consolePane.classList.add("hidden");
      return;
    }
    const d = curDevice();
    const sl = live.slides.get(file);
    const hash = sl && sl.index > 1 ? `#slide=${sl.index}` : "";
    frame = makePreviewFrame(previewUrl(pid, file) + hash, { file, role: "preview", title: file });
    if (d.id === "fit") {
      scroll.style.display = "block";
      const wrap = h("div", { class: "device", style: { width: "100%", height: "100%", borderRadius: "0", boxShadow: "none" } }, frame);
      scroll.appendChild(wrap);
    } else {
      scroll.style.display = "flex";
      const z = zoom === "fit" ? fitZoom(d) : +zoom;
      const dev = h("div", { class: "device" + (d.id === "mobile" ? " mobile" : ""), style: { width: d.w + "px", height: d.h + "px", transform: `scale(${z})` } }, frame);
      const wrap = h("div", { class: "device-wrap", style: { width: d.w * z + "px", height: d.h * z + "px" } }, h("span", { class: "device-size" }, `${d.w} × ${d.h} · ${Math.round(z * 100)}%`), dev);
      scroll.appendChild(h("div", { class: "preview-center" }, wrap));
    }
    drawNotes(); drawConsole();
  }
  async function drawNotes() {
    const notes = await speakerNotes(S.activeFile || "");
    const sl = live.slides.get(S.activeFile);
    if (!notes.length || !showNotes) { notesPane.classList.add("hidden"); return; }
    notesPane.classList.remove("hidden");
    const i = (sl?.index || 1) - 1;
    mount(notesPane, h("h5", null, `Speaker notes — slide ${i + 1}`), h("div", { style: { whiteSpace: "pre-wrap" } }, notes[i] || "No notes for this slide."));
  }
  function drawConsole() {
    consolePane.classList.toggle("hidden", !showConsole);
    if (!showConsole) return;
    const logs = live.console.get(S.activeFile) || [];
    const bodyEl = h("div", { class: "console-body" }, logs.length ? logs.map((l) => h("div", { class: "log " + l.level }, h("span", { class: "at" }, new Date(l.at).toLocaleTimeString()), h("span", null, l.args.join(" "))))
      : h("div", { class: "faint", style: { padding: "8px 12px" } }, "No console output from this page."));
    mount(consolePane, h("div", { class: "console-head" }, icon("terminal"), "Console", h("span", { class: "faint" }, S.activeFile), h("span", { class: "grow" }),
      btn("Clear", { kind: "quiet", cls: "sm", onClick: () => { live.console.set(S.activeFile, []); drawConsole(); drawBar(); } }),
      btn("", { kind: "quiet", icon: "x", cls: "sm", title: "Close", onClick: () => { showConsole = false; drawBody(); drawBar(); } })), bodyEl);
    bodyEl.scrollTop = bodyEl.scrollHeight;
  }
  // Fit scale for a fixed viewport. On a phone the board fills the width (the
  // page scrolls vertically) rather than shrinking to fit both axes.
  function fitZoom(d) {
    const narrow = scroll.clientWidth < 600;
    const avail = { w: scroll.clientWidth - (narrow ? 16 : 48), h: scroll.clientHeight - 60 };
    const z = narrow ? Math.min(1, avail.w / d.w) : Math.min(1, avail.w / d.w, avail.h / d.h);
    return z > 0 ? z : 1;
  }
  function reload() { if (frame && S.activeFile) frame.src = previewUrl(pid, S.activeFile, "&r=" + Date.now()); }
  function drawAll() { drawTabs(); drawBar(); drawBody(); drawUndo(); refreshUndo(S.activeFile).then(drawUndo); }

  function present() {
    import("./present.js").then((m) => m.present(S.activeFile, live.slides.get(S.activeFile)));
  }

  drawAll();
  const ro = new ResizeObserver(() => { if (curDevice().id !== "fit" && zoom === "fit" && frame && scroll.clientWidth) { const f = frame; const d = curDevice(); const z = fitZoom(d); const dev = f.parentNode; if (dev && dev.classList.contains("device")) { dev.style.transform = `scale(${z})`; const w = dev.parentNode; w.style.width = d.w * z + "px"; w.style.height = d.h * z + "px"; w.firstChild.textContent = `${d.w} × ${d.h} · ${Math.round(z * 100)}%`; } } });
  ro.observe(scroll);
  offs.push(
    bus.on("tabs", (e) => { drawTabs(); if (e && e.navigated) drawBar(); }),
    bus.on("console", (f) => { if (f === S.activeFile) { drawBar(); drawConsole(); } }),
    bus.on("slide", (e) => { if (e.file === S.activeFile) { drawBar(); drawNotes(); } }),
    bus.on("tweaks", (f) => { if (f === S.activeFile) drawBar(); }),
    bus.on("live", (e) => { if (e.file === S.activeFile) drawBar(); }),
    bus.on("reload-previews", (paths) => { if (!paths.length || paths.includes(S.activeFile)) { notesCache.delete(S.activeFile); reload(); drawBar(); } }),
    bus.on("files-changed", () => drawTabs()),
    bus.on("preview-slide", (a) => slide(a)),
    bus.on("preview-reload", reload),
    bus.on("undo-state", (e) => { if (e.file === S.activeFile) drawUndo(); }),
  );
  return () => { ro.disconnect(); offs.forEach((f) => f()); tb._cleanup && tb._cleanup(); if (frame) unregisterFrame(frame); };
}
