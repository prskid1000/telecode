// TeleDesign — core helpers shared by every module.
//
// Rules this file enforces for the rest of the app:
//   * DOM is built with h(); strings always become text nodes. Nothing user- or
//     agent-supplied is ever assigned to innerHTML. The only innerHTML writes are
//     the constant icon strings below.
//   * Every API call goes through api(). A 404/405 whose body is not our JSON
//     error shape means "route not built yet": the feature flag flips off and the
//     caller gets an ApiMissing error so it can hide the control.

// ── DOM ──────────────────────────────────────────────────────────────────
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === "html") throw new Error("h(): raw html is not allowed");
      else if (k in el && typeof v !== "string" && k !== "list") el[k] = v;
      else el.setAttribute(k, v === true ? "" : String(v));
    }
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children) {
    if (c == null || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export function clear(el) { while (el && el.firstChild) el.removeChild(el.firstChild); return el; }
export function mount(el, ...children) { clear(el); append(el, children); return el; }

// ── Icons (constant, trusted SVG — the only innerHTML in the app) ────────
const P = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
  chat: '<path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H10l-4 3.5V16H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"/>',
  comment: '<path d="M12 20l-3-3H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-3z"/><path d="M8.5 9h7M8.5 12.5h4"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  folder: '<path d="M3.5 7a2 2 0 0 1 2-2h3.8l2 2h7.2a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
  history: '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4h4"/><path d="M12 8v4.5l3 2"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  checkCircle: '<circle cx="12" cy="12" r="8.5"/><path d="M8 12.3l2.7 2.7L16 9.6"/>',
  sliders: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
  inspect: '<path d="M4 4l6.5 16 2.3-6.7L19.5 11z"/>',
  cursor: '<path d="M6 3.5l12 7.3-5.3 1.3 3.2 6.2-2.3 1.2-3.2-6.2L6 17z"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
  text: '<path d="M5 6V4.5h14V6M12 4.5v15M9 19.5h6"/>',
  knobs: '<circle cx="8" cy="8" r="3"/><circle cx="16" cy="16" r="3"/><path d="M8 11v9M8 4v1M16 4v9M16 19v1"/>',
  draw: '<path d="M4 19c3-1 4-4 6.5-4S13 18 15.5 17s2-5 4.5-6"/><path d="M15 4l5 5"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
  code: '<path d="M8.5 7L3.5 12l5 5M15.5 7l5 5-5 5"/>',
  canvas: '<rect x="3.5" y="3.5" width="7" height="7" rx="1"/><rect x="13.5" y="3.5" width="7" height="10" rx="1"/><rect x="3.5" y="13.5" width="7" height="7" rx="1"/>',
  play: '<path d="M8 5.5v13l10.5-6.5z"/>',
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
  send: '<path d="M4 12l16-7.5-6 16-2.5-6.5z"/><path d="M11.5 14L20 4.5"/>',
  paperclip: '<path d="M20 11.5l-7.8 7.8a4.6 4.6 0 0 1-6.5-6.5l8.1-8.1a3 3 0 0 1 4.3 4.3l-8 8a1.5 1.5 0 0 1-2.2-2.2l7.3-7.3"/>',
  download: '<path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/>',
  upload: '<path d="M12 20V9M7 13.5l5-5 5 5M5 4h14"/>',
  share: '<circle cx="6" cy="12" r="2.5"/><circle cx="17.5" cy="6" r="2.5"/><circle cx="17.5" cy="18" r="2.5"/><path d="M8.3 10.8l7-3.6M8.3 13.2l7 3.6"/>',
  more: '<circle cx="5.5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="18.5" cy="12" r="1.3"/>',
  chevronDown: '<path d="M6 9.5l6 6 6-6"/>',
  chevronRight: '<path d="M9.5 6l6 6-6 6"/>',
  chevronLeft: '<path d="M14.5 6l-6 6 6 6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
  moon: '<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z"/>',
  keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M9.5 10h.01M13 10h.01M16.5 10h.01M7 14h10"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 13.5a7.7 7.7 0 0 0 0-3l2-1.6-2-3.4-2.4 1a7.6 7.6 0 0 0-2.6-1.5L14 2.5h-4l-.4 2.5A7.6 7.6 0 0 0 7 6.5l-2.4-1-2 3.4 2 1.6a7.7 7.7 0 0 0 0 3l-2 1.6 2 3.4 2.4-1a7.6 7.6 0 0 0 2.6 1.5l.4 2.5h4l.4-2.5a7.6 7.6 0 0 0 2.6-1.5l2.4 1 2-3.4z"/>',
  refresh: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4v4h-4"/>',
  external: '<path d="M14 4h6v6M20 4l-8.5 8.5"/><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
  trash: '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5h9l1-12.5"/>',
  copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/>',
  sparkle: '<path d="M12 3.5l1.9 5.2 5.3 1.9-5.3 1.9L12 17.7l-1.9-5.2-5.3-1.9 5.3-1.9z"/><path d="M18.5 16l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
  layers: '<path d="M12 3.5l9 4.8-9 4.8-9-4.8z"/><path d="M3 12.5l9 4.8 9-4.8"/><path d="M3 16.5l9 4.8 9-4.8" opacity=".55"/>',
  palette: '<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.3 0 2-.8 2-1.8 0-1.3-1.2-1.6-1.2-2.8 0-1 .8-1.6 1.8-1.6h2.3a3.6 3.6 0 0 0 3.6-3.6C20.5 6.6 16.7 3.5 12 3.5z"/><circle cx="7.8" cy="11" r="1.1"/><circle cx="10.3" cy="7.3" r="1.1"/><circle cx="15" cy="7.5" r="1.1"/>',
  template: '<rect x="3.5" y="3.5" width="17" height="17" rx="2"/><path d="M3.5 9h17M9 9v11.5"/>',
  archive: '<rect x="3.5" y="4" width="17" height="4.5" rx="1"/><path d="M5 8.5V19a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8.5M10 12.5h4"/>',
  star: '<path d="M12 4l2.5 5.2 5.7.8-4.1 4 1 5.6-5.1-2.7-5.1 2.7 1-5.6-4.1-4 5.7-.8z"/>',
  desktop: '<rect x="3" y="4.5" width="18" height="12" rx="1.5"/><path d="M9 20h6M12 16.5V20"/>',
  tablet: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M11 18h2"/>',
  phone: '<rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 18h2"/>',
  fit: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  present: '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M12 16v4M8 20h8M10 8v4l3.5-2z"/>',
  notes: '<path d="M6 3.5h12a1 1 0 0 1 1 1v15a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1z"/><path d="M8.5 8h7M8.5 11.5h7M8.5 15h4"/>',
  terminal: '<rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="M7 9.5l3 2.5-3 2.5M12.5 15h4.5"/>',
  agents: '<circle cx="8" cy="8.5" r="3"/><circle cx="16.5" cy="8.5" r="3"/><path d="M3 19c.5-3 2.6-4.5 5-4.5s4.5 1.5 5 4.5M12.5 15.2c1-.5 2.3-.7 4-.7 2.4 0 4.5 1.5 5 4.5"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  image: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.8"/><path d="M20.5 16l-5-5-8.5 8.5"/>',
  back: '<path d="M10 6l-6 6 6 6M4 12h16"/>',
  bolt: '<path d="M13 3L5 13.5h6L10 21l8-10.5h-6z"/>',
  alert: '<path d="M12 4l9 15.5H3z"/><path d="M12 10v4M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/>',
  dot: '<circle cx="12" cy="12" r="4"/>',
  split: '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><path d="M12 4.5v15"/>',
  diff: '<path d="M8 3.5v7M4.5 7h7"/><path d="M4.5 17h7"/><rect x="14" y="3.5" width="6.5" height="17" rx="1.5"/>',
  restore: '<path d="M4 12a8 8 0 1 0 2.4-5.7L4 8.5"/><path d="M4 4v4.5h4.5"/>',
  zoomIn: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2M8 11h6M11 8v6"/>',
  zoomOut: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2M8 11h6"/>',
  panel: '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><path d="M15 4.5v15"/>',
  panelLeft: '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><path d="M9 4.5v15"/>',
  wand: '<path d="M4 20L15 9M13.5 5.5V3M17 7l1.8-1.8M18.5 10.5H21M11.5 7H9M17 13l1.8 1.8"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  redo: '<path d="M15 14l5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
  figma: '<path d="M9 3.5h3v6H9a3 3 0 0 1 0-6zM12 3.5h3a3 3 0 0 1 0 6h-3zM9 9.5h3v6H9a3 3 0 0 1 0-6zM12 12.5a3 3 0 1 0 6 0 3 3 0 0 0-6 0zM9 15.5h3v2a3 3 0 1 1-3-2z"/>',
  repo: '<path d="M6 4.5h11a1 1 0 0 1 1 1V17H7a2 2 0 0 0 0 4h11"/><path d="M6 4.5A1.5 1.5 0 0 0 4.5 6v13"/><path d="M9 8.5h6"/>',
};

export function icon(name, cls = "") {
  const span = document.createElement("span");
  span.className = "ic " + cls;
  span.setAttribute("aria-hidden", "true");
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${P[name] || P.dot}</svg>`;
  return span;
}

// Button helper: btn("Label", {icon, kind:"primary|ghost|danger|quiet", title, onClick, kbd})
export function btn(label, opts = {}) {
  const b = h("button", {
    type: opts.type || "button",
    class: ["btn", opts.kind || "", label ? "" : "btn-icon", opts.cls || ""].join(" ").trim(),
    title: opts.title || (label ? null : opts.aria) || null,
    "aria-label": opts.aria || (label ? null : opts.title) || null,
    disabled: opts.disabled || null,
    onclick: opts.onClick || null,
  }, opts.icon ? icon(opts.icon) : null, label ? h("span", null, label) : null,
     opts.kbd ? h("kbd", null, opts.kbd) : null);
  return b;
}

// ── Formatting ───────────────────────────────────────────────────────────
export function relTime(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (isNaN(t)) return "";
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 86400 * 7) return `${Math.round(s / 86400)} d ago`;
  return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}
export function fmtTokens(n) {
  n = +n || 0;
  if (n < 1000) return String(n);
  if (n < 1e6) return (n / 1000).toFixed(n < 1e4 ? 1 : 0) + "k";
  return (n / 1e6).toFixed(1) + "M";
}
export function fmtBytes(n) {
  n = +n || 0;
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}
export function fmtCost(c) { c = +c || 0; return c === 0 ? "$0" : c < 0.01 ? "<$0.01" : "$" + c.toFixed(2); }
export function fmtDuration(ms) {
  ms = +ms || 0;
  if (ms < 1000) return ms + " ms";
  const s = ms / 1000;
  return s < 60 ? s.toFixed(1) + " s" : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}
export const KIND_LABELS = {
  prototype: "Prototype", slides: "Slides", wireframe: "Wireframe", one_pager: "One-pager",
  animation: "Animation", landing_page: "Landing page", mobile_app: "Mobile app", web_app: "Web app",
  dashboard_table: "Dashboard", design_system: "Design system", other: "Other",
};
export const kindLabel = (k) => KIND_LABELS[k] || "Design";
export function fileKind(path) {
  const ext = (String(path).split(".").pop() || "").toLowerCase();
  if (["html", "htm"].includes(ext)) return "html";
  if (["jsx", "js", "mjs", "ts", "tsx"].includes(ext)) return "script";
  if (ext === "css") return "style";
  if (["png", "jpg", "jpeg", "gif", "webp", "svg", "avif"].includes(ext)) return "image";
  if (["md", "txt"].includes(ext)) return "text";
  if (ext === "json") return "json";
  if (ext === "napkin") return "napkin";
  return "other";
}
export const isHtml = (p) => fileKind(p) === "html";

// ── Safe local prefs (per-viewer conveniences only) ──────────────────────
export const prefs = {
  get(k, d) { try { const v = localStorage.getItem("td:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("td:" + k, JSON.stringify(v)); } catch { /* storage blocked */ } },
};

// ── Event bus ────────────────────────────────────────────────────────────
const listeners = new Map();
export const bus = {
  on(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); return () => listeners.get(type).delete(fn); },
  emit(type, data) { for (const fn of listeners.get(type) || []) { try { fn(data); } catch (e) { console.error("[td] listener", type, e); } } },
};

// ── API ──────────────────────────────────────────────────────────────────
export class ApiError extends Error {
  constructor(message, status, body) { super(message); this.status = status; this.body = body; }
}
export class ApiMissing extends ApiError {}

// Feature flags: undefined = unknown, true = seen working, false = route missing.
export const features = {};
export const hasFeature = (k) => features[k] !== false;

export async function api(method, url, body, opts = {}) {
  const init = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body instanceof Blob || body instanceof ArrayBuffer || typeof body === "string" && opts.raw) {
    init.body = body; init.headers["Content-Type"] = "application/octet-stream";
  } else if (body !== undefined) {
    init.body = JSON.stringify(body); init.headers["Content-Type"] = "application/json";
  } else if (method !== "GET" && method !== "HEAD") {
    // Mutating routes insist on JSON (non-CORS-simple), even with no payload.
    init.body = "{}"; init.headers["Content-Type"] = "application/json";
  }
  let r;
  try { r = await fetch(url, init); }
  catch (e) { throw new ApiError("Can't reach the TeleDesign server. Check that telecode's proxy is running.", 0); }
  const ct = r.headers.get("content-type") || "";
  const isJson = ct.includes("application/json");
  if (!r.ok) {
    const j = isJson ? await r.json().catch(() => ({})) : null;
    if ((r.status === 404 || r.status === 405 || r.status === 501) && !isJson) {
      if (opts.feature) features[opts.feature] = false;
      throw new ApiMissing("This feature isn't available on this server yet.", r.status);
    }
    throw new ApiError((j && (j.error || j.message)) || `${r.status} ${r.statusText}`, r.status, j);
  }
  if (opts.feature) features[opts.feature] = true;
  if (opts.as === "text") return r.text();
  if (opts.as === "blob") return r.blob();
  if (opts.as === "response") return r;
  return isJson ? r.json() : r.text();
}

// Returns null instead of throwing when the route is missing.
export async function tryApi(method, url, body, opts = {}) {
  try { return await api(method, url, body, opts); }
  catch (e) { if (e instanceof ApiMissing) return null; throw e; }
}

export const P_ = (pid) => `/api/design/projects/${encodeURIComponent(pid)}`;
export const encPath = (p) => String(p).split("/").map(encodeURIComponent).join("/");

// ── Toasts ───────────────────────────────────────────────────────────────
let toastHost;
export function toast(msg, opts = {}) {
  if (!toastHost) { toastHost = h("div", { class: "toasts", role: "status", "aria-live": "polite" }); document.body.appendChild(toastHost); }
  const kind = opts.kind || "info";
  const el = h("div", { class: "toast " + kind },
    icon(kind === "error" ? "alert" : kind === "success" ? "checkCircle" : "info"),
    h("div", { class: "toast-msg" }, msg),
    opts.action ? h("button", { class: "toast-act", onclick: () => { opts.action.run(); dismiss(); } }, opts.action.label) : null,
    h("button", { class: "toast-x", "aria-label": "Dismiss", onclick: () => dismiss() }, icon("x")));
  toastHost.appendChild(el);
  requestAnimationFrame(() => el.classList.add("in"));
  const dismiss = () => { el.classList.remove("in"); setTimeout(() => el.remove(), 200); };
  setTimeout(dismiss, opts.ms || (kind === "error" ? 7000 : 3500));
  return dismiss;
}
export function toastError(e, prefix) {
  const m = e instanceof Error ? e.message : String(e);
  toast(prefix ? `${prefix}: ${m}` : m, { kind: "error" });
}

// ── Modal dialogs ────────────────────────────────────────────────────────
// modal({title, body: Node, actions:[{label, kind, onClick(close) -> maybe false}], width, onClose})
export function modal(opts) {
  const dlg = h("dialog", { class: "modal " + (opts.cls || ""), style: opts.width ? { width: opts.width } : null });
  const close = (v) => { if (dlg.open) dlg.close(); dlg.remove(); opts.onClose && opts.onClose(v); };
  const actions = (opts.actions || []).map((a) => {
    const b = btn(a.label, { kind: a.kind, icon: a.icon });
    b.addEventListener("click", async () => {
      if (!a.onClick) return close();
      b.disabled = true;
      try { const r = await a.onClick(close, b); if (r !== false && a.autoClose !== false) close(r); }
      catch (e) { toastError(e); }
      finally { b.disabled = false; }
    });
    return b;
  });
  dlg.append(
    h("header", { class: "modal-head" },
      h("h2", null, opts.title || ""),
      opts.subtitle ? h("p", { class: "modal-sub" }, opts.subtitle) : null,
      h("button", { class: "btn btn-icon quiet modal-x", "aria-label": "Close", onclick: () => close() }, icon("x"))),
    h("div", { class: "modal-body" }, opts.body || null),
    ...(actions.length ? [h("footer", { class: "modal-foot" }, opts.footLeft || h("span"), h("div", { class: "row" }, actions))] : []),
  );
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
  dlg._close = close;
  dlg.addEventListener("mousedown", (e) => { if (e.target === dlg) close(); });
  document.body.appendChild(dlg);
  dlg.showModal();
  const first = dlg.querySelector("[autofocus], input, textarea, select");
  if (first) setTimeout(() => first.focus(), 30);
  return { el: dlg, close };
}

export function confirmDialog(title, message, { confirm = "Confirm", danger = false } = {}) {
  return new Promise((resolve) => {
    let done = false;
    modal({
      title, body: h("p", { class: "muted" }, message), width: "420px",
      actions: [
        { label: "Cancel", kind: "ghost", onClick: () => { done = true; resolve(false); } },
        { label: confirm, kind: danger ? "danger" : "primary", onClick: () => { done = true; resolve(true); } },
      ],
      onClose: () => { if (!done) resolve(false); },
    });
  });
}

export function promptDialog(title, { label = "", value = "", placeholder = "", confirm = "Save", multiline = false } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const input = multiline
      ? h("textarea", { class: "input", rows: 4, placeholder, autofocus: true }, value)
      : h("input", { class: "input", value, placeholder, autofocus: true });
    const m = modal({
      title, width: "440px",
      body: h("label", { class: "field" }, label ? h("span", { class: "field-label" }, label) : null, input),
      actions: [
        { label: "Cancel", kind: "ghost", onClick: () => { done = true; resolve(null); } },
        { label: confirm, kind: "primary", onClick: () => { done = true; resolve(input.value.trim()); } },
      ],
      onClose: () => { if (!done) resolve(null); },
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (!multiline || e.ctrlKey || e.metaKey)) { e.preventDefault(); done = true; resolve(input.value.trim()); m.close(); }
    });
  });
}

// ── Popover menus ────────────────────────────────────────────────────────
// menu(anchor, [{label, icon, onClick, danger, kbd, disabled, hint} | "-" | {heading}])
let openMenu = null;
export function closeMenu() { if (openMenu) { openMenu.remove(); openMenu = null; } }
export function menu(anchor, items, { align = "left", width, cls } = {}) {
  closeMenu();
  const el = h("div", { class: "menu" + (cls ? " " + cls : ""), role: "menu", style: width ? { minWidth: width } : null });
  for (const it of items) {
    if (!it) continue;
    if (it === "-") { el.appendChild(h("div", { class: "menu-sep" })); continue; }
    if (it.heading) { el.appendChild(h("div", { class: "menu-heading" }, it.heading)); continue; }
    if (it.node) { el.appendChild(it.node); continue; }
    el.appendChild(h("button", {
      class: "menu-item" + (it.danger ? " danger" : "") + (it.checked ? " checked" : ""), role: "menuitem",
      disabled: it.disabled || null, dataset: it.data || null,
      onclick: (e) => { e.stopPropagation(); closeMenu(); it.onClick && it.onClick(); },
    }, it.icon ? icon(it.icon) : h("span", { class: "ic" }),
       h("span", { class: "menu-label" }, it.label, it.hint ? h("small", null, it.hint) : null),
       it.kbd ? h("kbd", null, it.kbd) : null));
  }
  document.body.appendChild(el);
  const r = anchor.getBoundingClientRect();
  const mw = el.offsetWidth, mh = el.offsetHeight;
  let x = align === "right" ? r.right - mw : r.left;
  let y = r.bottom + 4;
  if (y + mh > innerHeight - 8) y = Math.max(8, r.top - mh - 4);
  x = Math.max(8, Math.min(x, innerWidth - mw - 8));
  el.style.left = x + "px"; el.style.top = y + "px";
  openMenu = el;
  setTimeout(() => {
    // A menu opened from another menu's item replaces it: the old menu's
    // listener must not close the new one.
    const off = (e) => {
      if (openMenu !== el) { document.removeEventListener("mousedown", off, true); return; }
      if (!el.contains(e.target)) { closeMenu(); document.removeEventListener("mousedown", off, true); }
    };
    document.addEventListener("mousedown", off, true);
  });
  return el;
}
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (openMenu) { closeMenu(); e.stopPropagation(); return; }
  // Topmost open dialog, wherever focus happens to be.
  const top = [...document.querySelectorAll("dialog.modal[open]")].pop();
  if (top && top._close) { e.preventDefault(); e.stopPropagation(); top._close(); }
}, true);

// ── Minimal, safe markdown → DOM (no HTML passthrough) ───────────────────
export function renderMarkdown(src) {
  const root = h("div", { class: "md" });
  const lines = String(src || "").replace(/\r\n/g, "\n").split("\n");
  let i = 0, list = null, para = [];
  const flushPara = () => { if (para.length) { root.appendChild(h("p", null, inline(para.join(" ")))); para = []; } };
  const flushList = () => { list = null; };
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^```\s*([\w-]*)/);
    if (fence) {
      flushPara(); flushList();
      const buf = []; i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      root.appendChild(h("pre", { class: "md-code" }, h("code", null, buf.join("\n"))));
      continue;
    }
    const hd = line.match(/^(#{1,4})\s+(.*)/);
    if (hd) { flushPara(); flushList(); root.appendChild(h("h" + Math.min(6, hd[1].length + 2), null, inline(hd[2]))); i++; continue; }
    const li = line.match(/^\s*([-*]|\d+\.)\s+(.*)/);
    if (li) {
      flushPara();
      const ordered = /\d/.test(li[1]);
      if (!list || list.ordered !== ordered) { list = { el: h(ordered ? "ol" : "ul"), ordered }; root.appendChild(list.el); }
      const task = li[2].match(/^\[( |x)\]\s+(.*)/i);
      list.el.appendChild(task ? h("li", { class: "task" }, h("span", { class: "tick" + (task[1] !== " " ? " on" : "") }), inline(task[2])) : h("li", null, inline(li[2])));
      i++; continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      flushPara(); flushList();
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
      const cells = (r) => r.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const body = rows.filter((r) => !/^\s*\|[\s:|-]+\|\s*$/.test(r));
      const t = h("table", { class: "md-table" });
      body.forEach((r, ri) => t.appendChild(h("tr", null, cells(r).map((c) => h(ri ? "td" : "th", null, inline(c))))));
      root.appendChild(h("div", { class: "md-table-wrap" }, t));
      continue;
    }
    if (/^>\s?/.test(line)) { flushPara(); flushList(); root.appendChild(h("blockquote", null, inline(line.replace(/^>\s?/, "")))); i++; continue; }
    if (!line.trim()) { flushPara(); flushList(); i++; continue; }
    if (/^---+$/.test(line.trim())) { flushPara(); flushList(); root.appendChild(h("hr")); i++; continue; }
    flushList();
    para.push(line.trim()); i++;
  }
  flushPara();
  return root;
}

function inline(text) {
  const out = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*|_[^_\s][^_]*_)|(\[[^\]]+\]\([^)\s]+\))/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (m[1]) out.push(h("code", null, t.slice(1, -1)));
    else if (m[2]) out.push(h("strong", null, t.slice(2, -2)));
    else if (m[3]) out.push(h("em", null, t.slice(1, -1)));
    else if (m[4]) {
      const mm = t.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);
      const href = mm[2];
      if (/^https?:\/\//i.test(href)) out.push(h("a", { href, target: "_blank", rel: "noopener noreferrer" }, mm[1]));
      else out.push(h("span", { class: "md-ref" }, mm[1]));
    }
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// ── Misc ─────────────────────────────────────────────────────────────────
export function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
export function isTyping(e) {
  const t = e.target;
  return t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.closest?.(".CodeMirror"));
}
export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied to clipboard", { kind: "success" }); }
  catch { promptDialog("Copy this", { value: text, confirm: "Done" }); }
}
export function skeleton(n, cls = "sk-line") { return Array.from({ length: n }, () => h("div", { class: "sk " + cls })); }
export function emptyState(iconName, title, text, action) {
  return h("div", { class: "empty" }, h("div", { class: "empty-ic" }, icon(iconName)),
    h("div", { class: "empty-title" }, title), text ? h("p", null, text) : null, action || null);
}
// Only allow image URLs we can reason about (same origin, preview origin, data:image).
export function safeImgSrc(url, allowedOrigins = []) {
  if (!url || typeof url !== "string") return null;
  if (/^data:image\/(png|jpe?g|gif|webp|svg\+xml);/i.test(url)) return url;
  if (url.startsWith("/") && !url.startsWith("//")) return url;
  try { const u = new URL(url); return allowedOrigins.includes(u.origin) || u.origin === location.origin ? url : null; } catch { return null; }
}
