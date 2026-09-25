// TeleDesign — app state, deep links, preview origin.
import { api, tryApi, bus, features, P_ } from "./core.js";

export const S = {
  route: "home",            // home | systems | templates | archived | project
  homeTab: "projects",
  project: null,
  projects: [],
  systems: [],
  templates: null,          // null = unknown / missing route
  chats: [],
  chatId: null,
  turns: new Map(),         // chatId -> [turn]
  files: [],
  comments: [],
  versions: [],
  assets: [],
  boards: {},
  tabs: [],                 // open preview tabs (file paths)
  activeFile: null,
  view: "canvas",           // canvas | code | preview
  panel: "chat",            // chat | comments | versions | review | tweaks | inspect
  mode: "view",             // view | comment | edit | text | knobs | draw
  selection: null,          // last td:select payload
  engines: null,
  previewOrigin: "http://127.0.0.1:1237",
  editorAvailable: false,
  systemId: null,           // systems browser detail
  deepBoard: null, deepNode: null,
};

// ── Preview origin ───────────────────────────────────────────────────────
export async function loadConfig() {
  const j = await tryApi("GET", "/api/design/config", undefined, { feature: "config" }).catch(() => null);
  if (j) {
    S.config = j;
    if (typeof j.preview_origin === "string" && /^https?:\/\/[^/]+$/.test(j.preview_origin)) S.previewOrigin = j.preview_origin;
  }
  return j;
}
export async function loadEngines(force = false) {
  const j = await tryApi("GET", "/api/design/engines" + (force ? "?refresh=1" : ""), undefined, { feature: "engines" }).catch(() => null);
  if (j) {
    S.engines = j;
    if (typeof j.preview_origin === "string" && /^https?:\/\/[^/]+$/.test(j.preview_origin)) S.previewOrigin = j.preview_origin;
    else if (j.preview_port) S.previewOrigin = `http://127.0.0.1:${+j.preview_port}`;
  }
  bus.emit("engines", S.engines);
  return S.engines;
}

export function previewUrl(pid, file, extra = "") {
  const path = String(file).split("/").map(encodeURIComponent).join("/");
  const q = "td_host=" + encodeURIComponent(location.origin);
  return `${S.previewOrigin}/p/${encodeURIComponent(pid)}/${path}?${q}${extra}`;
}
export const dsysUrl = (sid, path) => `${S.previewOrigin}/dsys/${encodeURIComponent(sid)}/${String(path).split("/").map(encodeURIComponent).join("/")}?td_host=${encodeURIComponent(location.origin)}`;

// ── Deep links (?project=&board=&node=&file=&chat=&view=&panel=) ────────
export function readUrl() {
  const q = new URLSearchParams(location.search);
  return {
    project: q.get("project"), board: q.get("board"), node: q.get("node"), file: q.get("file"),
    chat: q.get("chat"), view: q.get("view"), panel: q.get("panel"), system: q.get("system"),
    home: q.get("home"),
  };
}
export function writeUrl(push = false) {
  const q = new URLSearchParams();
  if (S.route === "project" && S.project) {
    q.set("project", S.project.id);
    if (S.activeFile) q.set("file", S.activeFile);
    if (S.chatId) q.set("chat", S.chatId);
    if (S.view !== "canvas") q.set("view", S.view);
    if (S.panel !== "chat") q.set("panel", S.panel);
    if (S.deepBoard) q.set("board", S.deepBoard);
    if (S.deepNode) q.set("node", S.deepNode);
  } else if (S.route === "systems") {
    q.set("home", "systems");
    if (S.systemId) q.set("system", S.systemId);
  } else if (S.route !== "home") q.set("home", S.route);
  const url = location.pathname + (q.toString() ? "?" + q : "");
  bus.emit("url");
  if (url === location.pathname + location.search) return;
  (push ? history.pushState : history.replaceState).call(history, null, "", url);
}

// ── Loaders ──────────────────────────────────────────────────────────────
export async function loadProjects(includeArchived = false) {
  const j = await api("GET", "/api/design/projects" + (includeArchived ? "?include_archived=1" : ""));
  if (!includeArchived) S.projects = j.projects || [];
  return j.projects || [];
}
export async function loadSystems() {
  const j = await api("GET", "/api/design/systems");
  S.systems = j.systems || [];
  bus.emit("systems", S.systems);
  return S.systems;
}
export async function loadFiles() {
  if (!S.project) return;
  const j = await tryApi("GET", P_(S.project.id) + "/files", undefined, { feature: "files" }).catch(() => null);
  S.files = (j && j.files) || [];
  bus.emit("files", S.files);
}
export async function loadComments() {
  if (!S.project) return;
  const j = await tryApi("GET", P_(S.project.id) + "/comments", undefined, { feature: "comments" }).catch(() => null);
  S.comments = (j && (j.comments || (Array.isArray(j) ? j : []))) || [];
  bus.emit("comments", S.comments);
}
export async function loadVersions() {
  if (!S.project) return;
  const j = await tryApi("GET", P_(S.project.id) + "/versions", undefined, { feature: "versions" }).catch(() => null);
  S.versions = ((j && j.versions) || []).slice().sort((a, b) => b.v - a.v);
  // The list is slim (no per-file hashes); keep details we already fetched.
  for (const v of S.versions) { const d = versionDetails.get(v.v); if (d) v.files = d; }
  bus.emit("versions", S.versions);
}
// Version detail (file → sha) cache; versions are immutable, so never stale.
export const versionDetails = new Map();
export async function versionFiles(v) {
  if (v == null) return null;
  if (versionDetails.has(v)) return versionDetails.get(v);
  const j = await tryApi("GET", P_(S.project.id) + "/versions/" + v).catch(() => null);
  const files = j ? (j.version?.files || Object.fromEntries((j.files || []).map((f) => [f.path, f.sha256]))) : null;
  if (files) { versionDetails.set(v, files); const rec = S.versions.find((x) => x.v === v); if (rec) rec.files = files; }
  return files;
}
export async function loadAssets() {
  if (!S.project) return;
  const j = await tryApi("GET", P_(S.project.id) + "/assets", undefined, { feature: "assets" }).catch(() => null);
  S.assets = (j && j.assets) || [];
  bus.emit("assets", S.assets);
}
export async function loadBoards() {
  if (!S.project) return;
  const j = await tryApi("GET", P_(S.project.id) + "/boards").catch(() => null);
  S.boards = (j && j.boards) || {};
  bus.emit("boards", S.boards);
}
export async function detectEditor() {
  try {
    const r = await fetch("/design/editor/index.html", { method: "HEAD" });
    S.editorAvailable = r.ok && (r.headers.get("content-type") || "").includes("html");
  } catch { S.editorAvailable = false; }
  return S.editorAvailable;
}

export function htmlFiles() {
  return S.files.filter((f) => /\.html?$/i.test(f.path) && !f.path.startsWith("uploads/") && !f.path.startsWith("_ds/"));
}
export function assetFor(path) { return S.assets.find((a) => a.path === path) || null; }
export function currentTurns() { return S.turns.get(S.chatId) || []; }
export function chatRunning(cid = S.chatId) {
  return (S.turns.get(cid) || []).some((t) => t.status === "running" || t.status === "queued");
}
export { features };
