// TeleDesign — project workspace: top bar, files rail, stage, side panel, SSE.
import {
  h, icon, btn, mount, clear, api, tryApi, toast, toastError, menu, confirmDialog, promptDialog, bus, P_, encPath, modal,
  fileKind, isHtml, kindLabel, prefs, features, relTime, emptyState, skeleton, fmtBytes,
} from "./core.js";
import {
  S, writeUrl, loadFiles, loadComments, loadVersions, loadAssets, loadBoards, detectEditor, htmlFiles, assetFor, previewUrl,
} from "./state.js";
import { broadcast } from "./bridge.js";

let root = null, es = null, pollTimer = null;
const els = {};

export const MODES = [
  { id: "view", icon: "cursor", label: "Interact", key: "V" },
  { id: "comment", icon: "comment", label: "Comment", key: "C" },
  { id: "edit", icon: "inspect", label: "Select and inspect", key: "E" },
  { id: "text", icon: "text", label: "Edit text", key: "T" },
  { id: "knobs", icon: "knobs", label: "Move, resize and knobs", key: "K" },
  { id: "draw", icon: "draw", label: "Draw on the design", key: "D" },
];

export function setMode(m) {
  S.mode = m;
  broadcast({ type: "td:set-mode", mode: m });
  bus.emit("mode", m);
}
export function setView(v) {
  if (!["canvas", "code", "preview"].includes(v)) return;
  S.view = v; writeUrl(); drawViewSwitch(); drawStage();
}
export function setPanel(p) {
  S.panel = p; writeUrl(); drawSideTabs(); drawSide();
  if (els.side?.classList.contains("collapsed")) toggleSide(true);
}
export function openFile(path, { view, background = false, agent = false } = {}) {
  if (!path) return;
  if (!S.tabs.includes(path)) S.tabs.push(path);
  if (!background) {
    S.activeFile = path;
    const v = view || (isHtml(path) ? (S.view === "code" ? "code" : "preview") : "code");
    if (v !== S.view) { S.view = v; drawViewSwitch(); }
    writeUrl();
    drawStage();
  } else bus.emit("tabs");
  drawRail();
  if (agent) toast(`The agent opened ${path}`, { action: background ? { label: "Show", run: () => openFile(path) } : null });
}
export function closeTab(path) {
  const i = S.tabs.indexOf(path);
  if (i < 0) return;
  S.tabs.splice(i, 1);
  if (S.activeFile === path) S.activeFile = S.tabs[Math.max(0, i - 1)] || null;
  writeUrl(); drawStage();
}

// ── Open ─────────────────────────────────────────────────────────────────
export async function openProject(host, pid, opts = {}) {
  root = host;
  disconnect();
  mount(root, h("div", { class: "ws" }, h("div", { class: "stage" }, h("div", { class: "stage-body" },
    h("div", { style: { padding: "40px", maxWidth: "520px" } }, skeleton(6))))));
  let project;
  try { project = (await api("GET", P_(pid))).project; }
  catch (e) {
    mount(root, h("div", { class: "ws", style: { display: "grid", placeItems: "center" } },
      emptyState("alert", e.status === 404 ? "Project not found" : "Couldn't open the project", e.status === 404 ? "It may have been deleted." : e.message,
        btn("Back to projects", { icon: "back", onClick: () => bus.emit("navigate", { route: "home" }) }))));
    return;
  }
  Object.assign(S, {
    route: "project", project, chats: [], chatId: opts.chat || null, files: [], comments: [], versions: [], assets: [], boards: {},
    tabs: [], activeFile: opts.file || null, view: opts.view || "canvas", panel: opts.panel || "chat", mode: "view", selection: null,
    deepBoard: opts.board || null, deepNode: opts.node || null,
  });
  S.turns = new Map();
  document.title = `${project.title} — TeleDesign`;
  build();
  await Promise.all([loadFiles(), loadComments(), loadVersions(), loadAssets(), loadBoards(), detectEditor()]);
  if (S.activeFile) S.tabs = [S.activeFile];
  else {
    const first = htmlFiles()[0];
    if (first) { S.tabs = [first.path]; S.activeFile = first.path; }
  }
  if (!opts.view && S.activeFile && !htmlFiles().length) S.view = "code";
  writeUrl();
  drawRail(); drawViewSwitch(); drawStage(); drawSideTabs(); drawSide();
  // chat.js listens for project-opened (first prompt) — make sure it is loaded.
  await import("./chat.js");
  connect(pid);
  bus.emit("project-opened", { firstPrompt: opts.firstPrompt, styleId: opts.styleId });
}

function build() {
  const title = h("input", { class: "proj-title", value: S.project.title, "aria-label": "Project name", size: Math.max(8, S.project.title.length) });
  title.addEventListener("input", () => { title.size = Math.max(8, title.value.length); });
  title.addEventListener("keydown", (e) => { if (e.key === "Enter") title.blur(); if (e.key === "Escape") { title.value = S.project.title; title.blur(); } });
  title.addEventListener("change", async () => {
    const t = title.value.trim();
    if (!t || t === S.project.title) { title.value = S.project.title; return; }
    try { S.project = (await api("PATCH", P_(S.project.id), { title: t, title_locked: true })).project || { ...S.project, title: t }; document.title = `${t} — TeleDesign`; toast("Renamed", { kind: "success" }); }
    catch (e) { toastError(e); title.value = S.project.title; }
  });
  els.title = title;
  els.viewSwitch = h("div", { class: "seg", role: "tablist", "aria-label": "View" });
  els.saveState = h("span", { class: "save-state" });
  const exportBtn = btn("Export", { icon: "download", onClick: (e) => { const a = e.currentTarget; import("./exporter.js").then((m) => m.exportMenu(a)); } });
  const shareBtn = btn("Share", { icon: "share", onClick: () => import("./exporter.js").then((m) => m.shareDialog()) });
  const agentsBtn = btn("Agents", { icon: "agents", kind: "ghost", title: "Run several agents in parallel", onClick: () => import("./exporter.js").then((m) => (m.runs.list.length ? m.runsDialog() : m.agentsDialog())) });
  els.agentsBtn = agentsBtn;
  const topbar = h("header", { class: "topbar" },
    h("a", { class: "brand", href: "/design", onclick: (e) => { e.preventDefault(); bus.emit("navigate", { route: "home" }); }, title: "All projects" },
      h("span", { class: "brand-mark" }, icon("edit")), "TeleDesign"),
    h("span", { class: "crumb-sep" }, "/"), title,
    h("span", { class: "pill" }, kindLabel(S.project.kind)),
    els.saveState,
    h("div", { class: "center" }, els.viewSwitch),
    h("div", { class: "right" },
      btn("", { kind: "quiet", icon: "panelLeft", title: "Toggle files  (Ctrl+Alt+B)", onClick: () => toggleRail() }),
      btn("", { kind: "quiet", icon: "panel", title: "Toggle side panel  (Ctrl+\\)", onClick: () => toggleSide() }),
      h("span", { class: "divider-v" }),
      agentsBtn, shareBtn, exportBtn,
      btn("", { kind: "quiet", icon: "more", title: "More", onClick: (e) => projectMore(e.currentTarget) })));
  els.rail = h("aside", { class: "rail" + (prefs.get("railHidden", false) ? " collapsed" : ""), "aria-label": "Files" });
  els.stagebar = h("div", { class: "stagebar" });
  els.stageBody = h("div", { class: "stage-body" });
  els.stage = h("section", { class: "stage", "aria-label": "Design" }, els.stagebar, els.stageBody);
  els.sideTabs = h("div", { class: "side-tabs", role: "tablist" });
  els.sideBody = h("div", { class: "side-body" });
  const resize = h("div", { class: "side-resize", title: "Drag to resize" });
  els.side = h("aside", { class: "side" + (prefs.get("sideHidden", false) ? " collapsed" : ""), "aria-label": "Assistant and review" }, resize, els.sideTabs, els.sideBody);
  const w = prefs.get("sideW", null); if (w) document.documentElement.style.setProperty("--panel-w", w + "px");
  resize.addEventListener("pointerdown", (e) => {
    resize.setPointerCapture(e.pointerId); resize.classList.add("drag");
    const move = (ev) => { const nw = Math.max(320, Math.min(720, innerWidth - ev.clientX)); document.documentElement.style.setProperty("--panel-w", nw + "px"); prefs.set("sideW", nw); };
    const up = () => { resize.classList.remove("drag"); resize.removeEventListener("pointermove", move); resize.removeEventListener("pointerup", up); };
    resize.addEventListener("pointermove", move); resize.addEventListener("pointerup", up);
  });
  mount(root, topbar, h("div", { class: "ws" }, els.rail, els.stage, els.side));
  // Agents API present? Probe lazily and hide the button when it's missing.
  import("./exporter.js").then((m) => m.loadRuns()).then(() => { if (features.agents === false) agentsBtn.classList.add("hidden"); else drawAgentsBtn(); });
}

export function toggleRail(force) {
  const hide = force === undefined ? !els.rail.classList.contains("collapsed") : !force;
  els.rail.classList.toggle("collapsed", hide); prefs.set("railHidden", hide);
}
export function toggleSide(force) {
  const hide = force === undefined ? !els.side.classList.contains("collapsed") : !force;
  els.side.classList.toggle("collapsed", hide); prefs.set("sideHidden", hide);
}

function projectMore(anchor) {
  const p = S.project;
  menu(anchor, [
    { label: "Rename", icon: "edit", onClick: () => { els.title.focus(); els.title.select(); } },
    { label: "Design system", icon: "palette", hint: S.systems.find((s) => s.id === p.design_system_id)?.name || "None", onClick: () => pickSystem() },
    { label: "Check design-system adherence", icon: "checkCircle", hint: "Hard-coded colours, fonts, off-system components", onClick: () => lintDialog() },
    { label: "Import a .pen file", icon: "upload", hint: "Lossy: no gradients, image fills or prompt nodes", onClick: () => importPen() },
    { label: "Save as template", icon: "template", onClick: () => import("./gallery.js").then((m) => m.saveAsTemplate(p)) },
    { label: "Preferences", icon: "settings", onClick: () => import("./exporter.js").then((m) => m.settingsDialog()) },
    { label: "Copy link to this view", icon: "link", onClick: () => navigator.clipboard?.writeText(location.href).then(() => toast("Link copied", { kind: "success" })) },
    "-",
    { label: "Archive project", icon: "archive", onClick: async () => { await api("PATCH", P_(p.id), { archived: true }).catch(toastError); bus.emit("navigate", { route: "home" }); toast("Archived", { kind: "success" }); } },
    { label: "Delete project", icon: "trash", danger: true, onClick: async () => {
      if (!await confirmDialog("Delete project", `Delete "${p.title}" and all its files? This can't be undone.`, { confirm: "Delete", danger: true })) return;
      await api("DELETE", P_(p.id)).catch(toastError); bus.emit("navigate", { route: "home" });
    } },
  ], { align: "right", width: "240px" });
}

async function lintDialog() {
  const body = h("div", null, skeleton(4));
  modal({ title: "Design-system adherence", subtitle: "Checks every source file against the attached system's rules.", width: "760px", body,
    actions: [{ label: "Close", kind: "ghost" }, { label: "Ask the agent to fix these", kind: "primary", icon: "sparkle", onClick: () => {
      bus.emit("chat-prefill", "Fix the design-system adherence findings: replace hard-coded values with the system's tokens and off-system elements with its components. Change nothing else.");
      setPanel("chat");
    } }] });
  let r;
  try { r = await api("POST", "/api/design/lint", { project_id: S.project.id }, { feature: "lint" }); }
  catch (e) { mount(body, emptyState("alert", e.status === 404 ? "No design system to check against" : "Couldn't run the check", e.status === 404 ? "Attach a design system to this project first." : e.message)); return; }
  const f = r.findings || [];
  const c = r.counts || {};
  if (!f.length) { mount(body, emptyState("checkCircle", "No findings", `${r.files ?? 0} files checked — everything uses the system.`)); return; }
  const sev = { error: "err", warn: "warn", info: "" };
  mount(body, h("div", { class: "row", style: { marginBottom: "10px" } }, h("span", { class: "pill err" }, `${c.error || 0} errors`), h("span", { class: "pill warn" }, `${c.warn || 0} warnings`), h("span", { class: "pill" }, `${c.info || 0} notes`), h("span", { class: "faint" }, `${r.files ?? "?"} files`)),
    h("div", { class: "list-view" }, h("table", { class: "tok-table" }, h("tr", null, h("th", null, ""), h("th", null, "Where"), h("th", null, "Rule"), h("th", null, "Finding")),
      f.slice(0, 300).map((x) => h("tr", { style: { cursor: x.file ? "pointer" : "default" }, onclick: () => x.file && openFile(x.file, { view: "code" }) },
        h("td", null, h("span", { class: "pill " + (sev[x.severity] ?? "") }, x.severity)), h("td", { class: "mono" }, `${x.file || ""}${x.line ? ":" + x.line : ""}`),
        h("td", { class: "mono" }, x.rule || ""), h("td", null, x.message || ""))))));
}
async function importPen() {
  const inp = h("input", { type: "file", accept: ".pen,application/json", class: "hidden" });
  document.body.appendChild(inp);
  inp.addEventListener("change", async () => {
    const f = inp.files[0]; inp.remove();
    if (!f) return;
    const fd = new FormData(); fd.append("file", f, f.name);
    try { const r = await api("POST", P_(S.project.id) + "/import/pen", fd); toast(`Imported ${r.path}. Open it from the canvas editor's File menu.`, { kind: "success" }); loadFiles(); }
    catch (e) { toastError(e, "Import failed"); }
  });
  inp.click();
}
async function pickSystem() {
  const { loadSystems } = await import("./state.js");
  if (!S.systems.length) await loadSystems();
  const anchor = els.title;
  menu(anchor, [{ heading: "Attach a design system" },
    { label: "None", checked: !S.project.design_system_id, icon: !S.project.design_system_id ? "check" : null, onClick: () => setSystem(null) },
    ...S.systems.map((s) => ({ label: s.name, hint: s.status, checked: S.project.design_system_id === s.id, icon: S.project.design_system_id === s.id ? "check" : null, onClick: () => setSystem(s.id) }))]);
}
async function setSystem(id) {
  try { S.project = (await api("PATCH", P_(S.project.id), { design_system_id: id })).project; toast(id ? "Design system attached. The next turn uses it." : "Design system removed", { kind: "success" }); }
  catch (e) { toastError(e); }
}

// ── View switch ──────────────────────────────────────────────────────────
function drawViewSwitch() {
  if (!els.viewSwitch) return;
  mount(els.viewSwitch, [["canvas", "canvas", "Canvas", "1"], ["code", "code", "Code", "2"], ["preview", "eye", "Preview", "3"]].map(([v, ic, l, k]) =>
    h("button", { class: S.view === v ? "on" : "", role: "tab", "aria-selected": S.view === v ? "true" : "false", title: `${l}  (Alt+${k})`, onclick: () => setView(v) }, icon(ic), l)));
}

// ── Stage ────────────────────────────────────────────────────────────────
let stageCleanup = null;
export function drawStage() {
  if (!els.stageBody) return;
  if (stageCleanup) { try { stageCleanup(); } catch {} stageCleanup = null; }
  clear(els.stagebar); clear(els.stageBody);
  const mod = S.view === "code" ? import("./code.js") : S.view === "preview" ? import("./preview.js") : import("./canvas.js");
  mod.then((m) => { stageCleanup = m.render(els.stageBody, els.stagebar) || null; }).catch((e) => { console.error(e); toastError(e); });
}
export function modeToolbar() {
  const seg = h("div", { class: "seg icons", role: "toolbar", "aria-label": "Interaction mode" });
  const draw = () => mount(seg, MODES.map((m) => h("button", { class: S.mode === m.id ? "on" : "", title: `${m.label}  (${m.key})`, "aria-pressed": S.mode === m.id ? "true" : "false", onclick: () => setMode(m.id) }, icon(m.icon))));
  draw();
  const off = bus.on("mode", draw);
  seg._cleanup = off;
  return seg;
}

// ── Files rail ───────────────────────────────────────────────────────────
export function drawRail() {
  if (!els.rail) return;
  const up = h("input", { type: "file", multiple: true, class: "hidden" });
  up.addEventListener("change", () => { uploadFiles([...up.files]); up.value = ""; });
  const head = h("div", { class: "rail-head" }, h("span", { class: "t" }, "Files"),
    btn("", { kind: "quiet", icon: "upload", cls: "sm", title: "Upload files", onClick: () => up.click() }),
    btn("", { kind: "quiet", icon: "plus", cls: "sm", title: "New file", onClick: () => newFile() }),
    btn("", { kind: "quiet", icon: "refresh", cls: "sm", title: "Refresh", onClick: () => loadFiles().then(drawRail) }), up);
  const body = h("div", { class: "rail-body" });
  if (features.files === false) body.appendChild(emptyState("folder", "File list unavailable", "This server doesn't expose project files yet."));
  else {
    const boards = htmlFiles();
    body.append(h("div", { class: "rail-group" }, icon("canvas"), "Boards", h("span", { class: "faint", style: { marginLeft: "auto", fontWeight: 400 } }, boards.length || "")));
    if (!boards.length) body.appendChild(h("div", { class: "faint", style: { padding: "4px 8px 8px", fontSize: "12px" } }, "Pages the agent writes appear here."));
    boards.forEach((f) => body.appendChild(fileItem(f, true)));
    const rest = S.files.filter((f) => !boards.includes(f) && !INTERNAL.has(f.path));
    if (rest.length) {
      body.append(h("div", { class: "rail-group" }, icon("folder"), "All files"));
      body.append(tree(rest));
    }
  }
  const dz = h("div", { class: "dropzone" }, "Drop files to upload");
  wireDrop(els.rail, dz);
  mount(els.rail, head, body, dz);
}
function wireDrop(target, dz) {
  if (target._dropWired) return;
  target._dropWired = true;
  target.addEventListener("dragover", (e) => { if ([...(e.dataTransfer?.types || [])].includes("Files")) { e.preventDefault(); target.querySelector(".dropzone")?.classList.add("over"); } });
  target.addEventListener("dragleave", () => target.querySelector(".dropzone")?.classList.remove("over"));
  target.addEventListener("drop", (e) => { e.preventDefault(); target.querySelector(".dropzone")?.classList.remove("over"); if (e.dataTransfer.files.length) uploadFiles([...e.dataTransfer.files]); });
}
function tree(files) {
  const rootNode = { dirs: new Map(), files: [] };
  for (const f of files) {
    const parts = f.path.split("/");
    let n = rootNode;
    for (const d of parts.slice(0, -1)) { if (!n.dirs.has(d)) n.dirs.set(d, { dirs: new Map(), files: [] }); n = n.dirs.get(d); }
    n.files.push(f);
  }
  const openDirs = new Set(prefs.get("openDirs:" + S.project.id, []));
  const drawNode = (n, prefix) => {
    const out = [];
    for (const [name, sub] of [...n.dirs].sort()) {
      const key = prefix + name;
      const d = h("div", { class: "tree-dir" + (openDirs.has(key) ? " open" : "") });
      d.append(h("div", { class: "tree-item", onclick: () => { d.classList.toggle("open"); d.classList.contains("open") ? openDirs.add(key) : openDirs.delete(key); prefs.set("openDirs:" + S.project.id, [...openDirs]); } },
        icon("chevronRight", "chev"), h("span", { class: "nm" }, name)), h("div", { class: "tree-kids" }, drawNode(sub, key + "/")));
      out.push(d);
    }
    n.files.sort((a, b) => a.path.localeCompare(b.path)).forEach((f) => out.push(fileItem(f, false)));
    return out;
  };
  return h("div", null, drawNode(rootNode, ""));
}
// Store bookkeeping files: not design sources, so not listed.
const INTERNAL = new Set(["boards.json", "comments.json", "assets.json", "thumbnail.webp", "doc.fig"]);
const KIND_ICON = { html: "canvas", script: "code", style: "palette", image: "image", text: "notes", json: "code", napkin: "draw", other: "file" };
function fileItem(f, asBoard) {
  const a = assetFor(f.path);
  const st = a?.status;
  const name = asBoard ? f.path : f.path.split("/").pop();
  return h("div", { class: "tree-item" + (S.activeFile === f.path ? " on" : ""), title: `${f.path} · ${fmtBytes(f.size)}${f.mtime ? " · " + relTime(typeof f.mtime === "number" ? new Date(f.mtime * 1000).toISOString() : f.mtime) : ""}`,
    onclick: () => openFile(f.path), ondblclick: () => openFile(f.path, { view: "code" }),
    oncontextmenu: (e) => { e.preventDefault(); fileMenu(e.currentTarget, f); } },
    icon(KIND_ICON[fileKind(f.path)] || "file"), h("span", { class: "nm" }, name),
    st ? h("span", { class: "dot " + (st === "approved" ? "ok" : st === "changes-requested" ? "err" : "warn"), title: st.replace("-", " ") }) : null,
    btn("", { kind: "quiet", icon: "more", cls: "sm act", title: "File actions", onClick: (e) => { e.stopPropagation(); fileMenu(e.currentTarget, f); } }));
}
function fileMenu(anchor, f) {
  menu(anchor, [
    isHtml(f.path) ? { label: "Open preview", icon: "eye", onClick: () => openFile(f.path, { view: "preview" }) } : null,
    { label: "Open in code", icon: "code", onClick: () => openFile(f.path, { view: "code" }) },
    isHtml(f.path) ? { label: "Open in new tab", icon: "external", onClick: () => window.open(previewUrl(S.project.id, f.path), "_blank", "noopener") } : null,
    { label: "Add to chat", icon: "paperclip", onClick: () => bus.emit("attach", [f.path]) },
    { label: "Copy path", icon: "copy", onClick: () => navigator.clipboard?.writeText(f.path) },
    "-",
    { label: "Delete", icon: "trash", danger: true, onClick: async () => {
      if (!await confirmDialog("Delete file", `Delete ${f.path}? You can restore it from Versions.`, { confirm: "Delete", danger: true })) return;
      try { await api("DELETE", P_(S.project.id) + "/files/" + encPath(f.path)); closeTab(f.path); await loadFiles(); drawRail(); toast("Deleted", { kind: "success" }); }
      catch (e) { toastError(e); }
    } },
  ]);
}
async function newFile() {
  const name = await promptDialog("New file", { label: "Path inside the project", placeholder: "about.html", confirm: "Create" });
  if (!name) return;
  if (!/^[\w./ -]+$/.test(name) || name.includes("..")) { toast("Use letters, numbers, dots, dashes and slashes only.", { kind: "error" }); return; }
  const starter = isHtml(name) ? `<!doctype html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width, initial-scale=1">\n  <title>${name.replace(/\.html?$/, "")}</title>\n</head>\n<body>\n\n</body>\n</html>\n` : "";
  try { await api("PUT", P_(S.project.id) + "/files/" + encPath(name), new Blob([starter])); await loadFiles(); drawRail(); openFile(name, { view: "code" }); }
  catch (e) { toastError(e, "Couldn't create the file"); }
}
export async function uploadFiles(files) {
  if (!files.length) return [];
  const fd = new FormData();
  files.forEach((f) => fd.append("file", f, f.name));
  try {
    const r = await api("POST", P_(S.project.id) + "/uploads", fd, { feature: "uploads" });
    toast(`Uploaded ${files.length} file${files.length > 1 ? "s" : ""}`, { kind: "success" });
    await loadFiles(); drawRail();
    return r.paths || [];
  } catch (e) { toastError(e, "Upload failed"); return []; }
}

// ── Side panel ───────────────────────────────────────────────────────────
const SIDE = [
  { id: "chat", icon: "chat", label: "Chat" },
  { id: "comments", icon: "comment", label: "Comments", count: () => S.comments.filter((c) => c.status !== "resolved").length, feature: "comments" },
  { id: "versions", icon: "history", label: "Versions", feature: "versions" },
  { id: "review", icon: "checkCircle", label: "Review", count: () => S.assets.filter((a) => a.status === "needs-review").length, feature: "assets" },
  { id: "tweaks", icon: "sliders", label: "Tweaks" },
  { id: "inspect", icon: "inspect", label: "Inspect" },
];
export function drawSideTabs() {
  if (!els.sideTabs) return;
  mount(els.sideTabs, SIDE.filter((t) => !t.feature || features[t.feature] !== false || S.panel === t.id).map((t) => {
    const n = t.count ? t.count() : 0;
    return h("button", { class: "side-tab" + (S.panel === t.id ? " on" : ""), role: "tab", title: t.label, "aria-selected": S.panel === t.id ? "true" : "false", onclick: () => setPanel(t.id) },
      icon(t.icon), h("span", { class: "lbl" }, t.label), n ? h("span", { class: "count" }, n) : null);
  }));
}
let sideCleanup = null;
export function drawSide() {
  if (!els.sideBody) return;
  if (sideCleanup) { try { sideCleanup(); } catch {} sideCleanup = null; }
  clear(els.sideBody);
  const mod = S.panel === "chat" ? import("./chat.js") : import("./panels.js");
  mod.then((m) => { sideCleanup = (S.panel === "chat" ? m.render(els.sideBody) : m.render(S.panel, els.sideBody)) || null; })
    .catch((e) => { console.error(e); toastError(e); });
}
bus.on("comments", drawSideTabs);
bus.on("assets", () => { drawSideTabs(); drawRail(); });
bus.on("files", () => drawRail());

// ── Events (SSE, with a polling fallback) ────────────────────────────────
const EVENT_TYPES = ["turn", "delta", "tool", "todo", "files", "form", "check", "title", "thumbnail", "comments", "assets", "error", "show", "agents", "chat"];
function connect(pid) {
  disconnect();
  let opened = false;
  try { es = new EventSource(P_(pid) + "/events"); }
  catch { startPolling(); return; }
  es.onopen = () => { opened = true; setLive(true); };
  es.onerror = () => {
    setLive(false);
    if (!opened && es && es.readyState === EventSource.CLOSED) { es = null; startPolling(); }
  };
  for (const t of EVENT_TYPES) {
    es.addEventListener(t, (e) => {
      let data = null;
      try { data = JSON.parse(e.data); } catch { return; }
      handleEvent(t, data);
    });
  }
}
function setLive(on) {
  if (!els.saveState) return;
  mount(els.saveState, h("span", { class: "dot " + (on ? "ok" : "warn") }), on ? "Live" : "Reconnecting…");
  els.saveState.title = on ? "Receiving live updates from the agent" : "Lost the live connection; retrying";
}
function startPolling() {
  if (pollTimer) return;
  mount(els.saveState || h("span"), h("span", { class: "dot" }), "Polling");
  pollTimer = setInterval(() => bus.emit("poll"), 2500);
}
export function disconnect() {
  if (es) { es.close(); es = null; }
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

const refreshFiles = (() => { let t; return () => { clearTimeout(t); t = setTimeout(async () => { await loadFiles(); ensureActiveFile(); bus.emit("files-changed"); }, 250); }; })();
// A project opened before its first page existed: adopt the first page once it lands.
function ensureActiveFile() {
  if (S.activeFile && S.files.some((f) => f.path === S.activeFile)) return;
  const first = htmlFiles()[0];
  if (!first) return;
  S.activeFile = first.path;
  if (!S.tabs.includes(first.path)) S.tabs.unshift(first.path);
  writeUrl();
  bus.emit("tabs");
}

function handleEvent(type, d) {
  if (!S.project) return;
  bus.emit("ev:" + type, d);
  switch (type) {
    case "files":
      refreshFiles();
      loadVersions();
      bus.emit("reload-previews", d.changed || []);
      break;
    case "title":
      if (d.title) { S.project.title = d.title; if (els.title && document.activeElement !== els.title) { els.title.value = d.title; els.title.size = Math.max(8, d.title.length); } document.title = `${d.title} — TeleDesign`; }
      break;
    case "comments": loadComments(); break;
    case "assets": loadAssets(); break;
    case "show":
      if (d.path) openFile(d.path, { background: d.target === "agent", agent: true });
      break;
    case "error":
      if (d.message) toast(d.message, { kind: "error" });
      break;
  }
}

// Clicking on a changed file chip anywhere opens it.
bus.on("open-file", (p) => openFile(p));
bus.on("reload-previews", () => { if (S.view === "canvas") bus.emit("canvas-refresh"); });


// Agents button shows a live dot while a parallel run is active.
async function drawAgentsBtn() {
  const b = els.agentsBtn;
  if (!b) return;
  const m = await import("./exporter.js");
  const active = m.runs.list.filter((r) => m.ACTIVE(r.status)).length;
  mount(b, icon("agents"), h("span", null, "Agents"), active ? h("span", { class: "count" }, active) : null);
}
bus.on("runs", drawAgentsBtn);

// ── App state for agents (PUT …/app-state, read by design_get_app_state) ─
const pushAppState = (() => {
  let t = null, last = "";
  return () => {
    clearTimeout(t);
    t = setTimeout(async () => {
      if (!S.project || features.appState === false) return;
      const sel = S.selection ? { file: S.selection.file, td_id: S.selection.td_id || null, selector: S.selection.selector || null, source_loc: S.selection.source_loc || null,
        node_id: S.selection.node_id || null, text: S.selection.text ? String(S.selection.text).slice(0, 300) : null, mentioned_element: S.selection.mentioned_element ? String(S.selection.mentioned_element).slice(0, 4000) : null } : null;
      const { live } = await import("./interact.js");
      const slide = S.activeFile ? live.slides.get(S.activeFile) || null : null;
      const state = { active_file: S.activeFile, active_board: S.deepBoard || (S.view === "canvas" ? S.activeFile : null), active_chat_id: S.chatId, view: S.view, mode: S.mode,
        selection: sel, slide, open_files: S.tabs.slice(0, 30) };
      const key = JSON.stringify(state);
      if (key === last) return;
      last = key;
      tryApi("PUT", P_(S.project.id) + "/app-state", state, { feature: "appState" }).catch(() => {});
    }, 300);
  };
})();
for (const ev of ["url", "selection", "mode", "slide", "tabs", "chat-switched", "editor-selection"]) bus.on(ev, pushAppState);
bus.on("project-opened", pushAppState);
