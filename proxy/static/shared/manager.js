// Agent Manager kit — shared by Team Mode (telecode.html, /team) and Task Mode
// (index.html, /tasks); served from /shared/manager.js as a classic script.
// Mirrors TeleDesign's core.js rules:
//   * DOM is built with h(); strings always become text nodes. Server/user text
//     is never assigned to innerHTML — the only innerHTML is the constant icon SVG.
//   * No native alert/confirm/prompt: toast(), confirmDialog(), promptDialog().
"use strict";

// ── DOM ────────────────────────────────────────────────────────────────
function h(tag, attrs, ...children) {
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
  appendKids(el, children);
  return el;
}
function appendKids(el, children) {
  for (const c of children) {
    if (c == null || c === false) continue;
    if (Array.isArray(c)) appendKids(el, c);
    else if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
}
const $ = (id) => document.getElementById(id);
function clear(el) { while (el && el.firstChild) el.removeChild(el.firstChild); return el; }
function mount(el, ...children) { if (!el) return el; clear(el); appendKids(el, children); return el; }

// ── Icons (constant, trusted SVG paths — the only innerHTML) ───────────
const ICON_PATHS = {
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  folder: '<path d="M3.5 7a2 2 0 0 1 2-2h3.8l2 2h7.2a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
  history: '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4h4"/><path d="M12 8v4.5l3 2"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  checkCircle: '<circle cx="12" cy="12" r="8.5"/><path d="M8 12.3l2.7 2.7L16 9.6"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
  code: '<path d="M8.5 7L3.5 12l5 5M15.5 7l5 5-5 5"/>',
  play: '<path d="M8 5.5v13l10.5-6.5z"/>',
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
  pause: '<rect x="6.5" y="5.5" width="3.5" height="13" rx="1"/><rect x="14" y="5.5" width="3.5" height="13" rx="1"/>',
  send: '<path d="M4 12l16-7.5-6 16-2.5-6.5z"/><path d="M11.5 14L20 4.5"/>',
  download: '<path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/>',
  upload: '<path d="M12 20V9M7 13.5l5-5 5 5M5 4h14"/>',
  more: '<circle cx="5.5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="18.5" cy="12" r="1.3"/>',
  chevronDown: '<path d="M6 9.5l6 6 6-6"/>',
  chevronRight: '<path d="M9.5 6l6 6-6 6"/>',
  chevronUp: '<path d="M6 14.5l6-6 6 6"/>',
  arrowDown: '<path d="M12 4v16M6 14l6 6 6-6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
  moon: '<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z"/>',
  keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M9.5 10h.01M13 10h.01M16.5 10h.01M7 14h10"/>',
  refresh: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4v4h-4"/>',
  external: '<path d="M14 4h6v6M20 4l-8.5 8.5"/><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
  trash: '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5h9l1-12.5"/>',
  copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/>',
  save: '<path d="M5 4h11l3 3v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/>',
  sparkle: '<path d="M12 3.5l1.9 5.2 5.3 1.9-5.3 1.9L12 17.7l-1.9-5.2-5.3-1.9 5.3-1.9z"/>',
  layers: '<path d="M12 3.5l9 4.8-9 4.8-9-4.8z"/><path d="M3 12.5l9 4.8 9-4.8"/><path d="M3 16.5l9 4.8 9-4.8" opacity=".55"/>',
  palette: '<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.3 0 2-.8 2-1.8 0-1.3-1.2-1.6-1.2-2.8 0-1 .8-1.6 1.8-1.6h2.3a3.6 3.6 0 0 0 3.6-3.6C20.5 6.6 16.7 3.5 12 3.5z"/><circle cx="7.8" cy="11" r="1.1"/><circle cx="10.3" cy="7.3" r="1.1"/><circle cx="15" cy="7.5" r="1.1"/>',
  terminal: '<rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="M7 9.5l3 2.5-3 2.5M12.5 15h4.5"/>',
  agents: '<circle cx="8" cy="8.5" r="3"/><circle cx="16.5" cy="8.5" r="3"/><path d="M3 19c.5-3 2.6-4.5 5-4.5s4.5 1.5 5 4.5M12.5 15.2c1-.5 2.3-.7 4-.7 2.4 0 4.5 1.5 5 4.5"/>',
  agent: '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20c.7-3.8 3.4-5.8 7-5.8s6.3 2 7 5.8"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  bolt: '<path d="M13 3L5 13.5h6L10 21l8-10.5h-6z"/>',
  alert: '<path d="M12 4l9 15.5H3z"/><path d="M12 10v4M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 8h.01"/>',
  dot: '<circle cx="12" cy="12" r="4"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  heart: '<path d="M12 19.5s-7.5-4.4-7.5-10A4.2 4.2 0 0 1 12 7a4.2 4.2 0 0 1 7.5 2.5c0 5.6-7.5 10-7.5 10z"/>',
  pulse: '<path d="M3 12h4l2.5-6 5 12 2.5-6h4"/>',
  briefcase: '<rect x="3.5" y="7" width="17" height="12.5" rx="2"/><path d="M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7M3.5 12.5h17"/>',
  workflow: '<rect x="3.5" y="4" width="6" height="5" rx="1.2"/><rect x="14.5" y="4" width="6" height="5" rx="1.2"/><rect x="9" y="15" width="6" height="5" rx="1.2"/><path d="M6.5 9v2.5a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1V9M12 12.5V15"/>',
  wrench: '<path d="M14.5 5.5a4 4 0 0 0 4.8 4.8L20 11l-9 9a2.1 2.1 0 0 1-3-3l9-9 .7.7"/><path d="M14.5 5.5L17 3l1.5 3.5L22 8"/>',
  message: '<path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H10l-4 3.5V16H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"/>',
  flag: '<path d="M5 21V4.5M5 4.5h11l-2 4 2 4H5"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/>',
  coins: '<ellipse cx="12" cy="7" rx="6.5" ry="2.8"/><path d="M5.5 7v5c0 1.5 2.9 2.8 6.5 2.8s6.5-1.3 6.5-2.8V7M5.5 12v5c0 1.5 2.9 2.8 6.5 2.8s6.5-1.3 6.5-2.8v-5"/>',
  hash: '<path d="M9 4L7 20M17 4l-2 16M4.5 9h15M3.5 15h15"/>',
  arrowIn: '<path d="M12 4v11M7 10.5l5 5 5-5"/><path d="M5 20h14"/>',
  arrowOut: '<path d="M12 16V5M7 9.5l5-5 5 5"/><path d="M5 20h14"/>',
  swap: '<path d="M7 7h12l-3.5-3.5M17 17H5l3.5 3.5"/>',
  inbox: '<path d="M3.5 13.5l2.6-7.2A1.5 1.5 0 0 1 7.5 5.3h9a1.5 1.5 0 0 1 1.4 1L20.5 13.5V18a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18z"/><path d="M3.5 13.5h4.5l1.5 2.5h5l1.5-2.5h4.5"/>',
  gate: '<path d="M5 20V6.5L12 4l7 2.5V20"/><path d="M9 20v-6h6v6M5 11h14"/>',
  fanout: '<circle cx="5" cy="12" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="19" cy="12" r="2"/><circle cx="19" cy="19" r="2"/><path d="M7 12h10M7 11l10-5.5M7 13l10 5.5"/>',
  loop: '<path d="M17 7.5A7 7 0 1 0 19 12"/><path d="M19 5v4h-4"/>',
  merge: '<circle cx="19" cy="12" r="2"/><circle cx="5" cy="5" r="2"/><circle cx="5" cy="12" r="2"/><circle cx="5" cy="19" r="2"/><path d="M7 12h10M7 5.5L17 11M7 18.5L17 13"/>',
  webhook: '<path d="M9 7.5a3.5 3.5 0 1 1 5.2 3l2.3 4.5"/><path d="M6.5 17.5a3.5 3.5 0 1 1 1-6.8l2.5-4.4"/><path d="M11 17.5h6a3.5 3.5 0 1 0-1.5-6.6"/>',
  target: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1"/>',
};
function icon(name, cls = "") {
  const span = document.createElement("span");
  span.className = "ic " + cls;
  span.setAttribute("aria-hidden", "true");
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${ICON_PATHS[name] || ICON_PATHS.dot}</svg>`;
  return span;
}
// btn("Label", {icon, kind:"primary|ghost|danger|quiet", size:"sm", title, onClick, kbd, id})
function btn(label, o = {}) {
  return h("button", {
    type: "button", id: o.id || null,
    class: ["btn", o.kind || "", o.size || "", label ? "" : "btn-icon", o.cls || ""].join(" ").replace(/\s+/g, " ").trim(),
    title: o.title || null, "aria-label": o.aria || (label ? null : o.title) || null,
    disabled: o.disabled || null, onclick: o.onClick || null,
  }, o.icon ? icon(o.icon) : null, label ? h("span", null, label) : null, o.kbd ? h("kbd", null, o.kbd) : null);
}

// ── Formatting ─────────────────────────────────────────────────────────
function fmtNum(n) { return (n == null) ? "—" : Number(n).toLocaleString(); }
function fmtCost(n) { return (n == null) ? "—" : "$" + Number(n).toFixed(4); }
function fmtMs(n) { if (n == null) return "—"; return n < 1000 ? `${n}ms` : n < 60000 ? `${(n / 1000).toFixed(1)}s` : `${Math.floor(n / 60000)}m ${Math.round((n % 60000) / 1000)}s`; }
function fmtTokens(n) { if (n == null) return "—"; n = +n || 0; if (n < 1000) return String(n); if (n < 1e6) return (n / 1000).toFixed(n < 1e4 ? 1 : 0) + "k"; return (n / 1e6).toFixed(1) + "M"; }
function formatBytes(bytes) {
  bytes = +bytes || 0;
  if (bytes === 0) return "0 B";
  const k = 1024, sizes = ["B", "KB", "MB", "GB"];
  const i = Math.min(sizes.length - 1, Math.floor(Math.log(bytes) / Math.log(k)));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(i ? 1 : 0)) + " " + sizes[i];
}
function fmtTime(s) { if (!s) return ""; const d = new Date(s); return isNaN(d) ? String(s) : d.toLocaleTimeString(); }
function fmtDateTime(s) { if (!s) return "—"; const d = new Date(s); return isNaN(d) ? String(s) : d.toLocaleString(); }
function relTime(iso) {
  if (!iso) return "";
  const t = Date.parse(iso); if (isNaN(t)) return String(iso);
  const s = Math.round((Date.now() - t) / 1000), fut = s < 0, a = Math.abs(s);
  const w = a < 45 ? null : a < 3600 ? `${Math.round(a / 60)} min` : a < 86400 ? `${Math.round(a / 3600)} h` : `${Math.round(a / 86400)} d`;
  if (!w) return fut ? "in a moment" : "just now";
  return fut ? `in ${w}` : `${w} ago`;
}
function durationBetween(a, b) {
  const t0 = Date.parse(a); if (isNaN(t0)) return null;
  const t1 = b ? Date.parse(b) : Date.now(); if (isNaN(t1)) return null;
  return Math.max(0, t1 - t0);
}
function shortId(id, n = 8) { id = String(id || ""); return id.length > n ? id.slice(0, n) + "…" : id; }

// ── Status pills ───────────────────────────────────────────────────────
const STATUS_TONE = {
  pending: "warn", queued: "warn", running: "accent", active: "accent", completed: "ok", done: "ok", success: "ok",
  failed: "err", error: "err", cancelled: "err", canceled: "err", partial: "warn", paused: "warn", skipped: "", resumed: "accent",
  budget_exceeded: "warn", interrupted: "warn", awaiting_input: "violet", rejected: "err", approved: "ok", ok: "ok",
  disabled: "",
};
const LIVE_STATUSES = ["pending", "running"];
const TERMINAL_RUN = ["completed", "failed", "partial", "cancelled", "interrupted", "budget_exceeded", "rejected"];
function statusPill(status, opts = {}) {
  const s = String(status || "unknown");
  const tone = opts.tone != null ? opts.tone : (STATUS_TONE[s.toLowerCase()] ?? "");
  const live = s === "running" || s === "awaiting_input" || s === "active" && opts.pulse;
  return h("span", { class: "pill " + tone, title: opts.title || null },
    h("span", { class: "dot" + (live ? " pulse" : "") }), opts.label || s.replace(/_/g, " "));
}

// ── API (same contract as the original pages: throw body.error || statusText) ─
async function api(path, opts = {}) {
  let r;
  try { r = await fetch(path, opts); }
  catch (e) { throw new Error("Can't reach telecode's proxy — is it running?"); }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || r.statusText || ("HTTP " + r.status));
  return body;
}
const jsonOpts = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// ── Toasts ─────────────────────────────────────────────────────────────
let toastHost;
function toast(msg, opts = {}) {
  if (!toastHost) { toastHost = h("div", { class: "toasts", role: "status", "aria-live": "polite" }); document.body.appendChild(toastHost); }
  const kind = opts.kind || "info";
  const dismiss = () => { el.classList.remove("in"); setTimeout(() => el.remove(), 200); };
  const el = h("div", { class: "toast " + kind },
    icon(kind === "error" ? "alert" : kind === "success" ? "checkCircle" : "info"),
    h("div", { class: "toast-msg" }, msg),
    h("button", { class: "toast-x", "aria-label": "Dismiss", onclick: () => dismiss() }, icon("x")));
  toastHost.appendChild(el);
  requestAnimationFrame(() => el.classList.add("in"));
  setTimeout(dismiss, opts.ms || (kind === "error" ? 7000 : 3500));
  return dismiss;
}
function showToast(message, isError = false) { return toast(message, { kind: isError ? "error" : "success" }); }

// ── Modal dialogs ──────────────────────────────────────────────────────
// modal({title, subtitle, body, actions:[{label, kind, icon, id, onClick(close) -> false keeps open}], cls, width, footLeft, onClose})
function modal(opts) {
  const dlg = h("dialog", { class: "modal " + (opts.cls || ""), style: opts.width ? { width: opts.width } : null, id: opts.id || null });
  const close = (v) => { if (dlg.open) dlg.close(); dlg.remove(); opts.onClose && opts.onClose(v); };
  const actions = (opts.actions || []).map((a) => {
    const b = btn(a.label, { kind: a.kind, icon: a.icon, id: a.id, kbd: a.kbd });
    if (a.primary || a.kind === "primary") b.dataset.primary = "1";
    b.addEventListener("click", async () => {
      if (!a.onClick) return close();
      b.disabled = true;
      try { const r = await a.onClick(close, b); if (r !== false && a.autoClose !== false) close(r); }
      catch (e) { toast(e.message || String(e), { kind: "error" }); }
      finally { b.disabled = false; }
    });
    return b;
  });
  dlg.append(
    h("header", { class: "modal-head" },
      h("h2", null, opts.title || ""),
      opts.subtitle ? h("p", { class: "modal-sub" }, opts.subtitle) : null,
      h("button", { class: "btn btn-icon quiet modal-x", type: "button", "aria-label": "Close", title: "Close (Esc)", onclick: () => close() }, icon("x"))),
    h("div", { class: "modal-body" }, opts.body || null),
    ...(actions.length ? [h("footer", { class: "modal-foot" }, opts.footLeft || h("span", { class: "faint", style: { fontSize: "11.5px" } }, h("kbd", null, "Ctrl"), " ", h("kbd", null, "Enter"), " to confirm"), h("div", { class: "row" }, actions))] : []),
  );
  dlg.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
  dlg.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      const p = [...dlg.querySelectorAll("[data-primary]")].pop();
      if (p && !p.disabled) { e.preventDefault(); p.click(); }
    }
  });
  dlg._close = close;
  dlg.addEventListener("mousedown", (e) => { if (e.target === dlg) close(); });
  document.body.appendChild(dlg);
  dlg.showModal();
  const first = dlg.querySelector("[autofocus], .modal-body input:not([type=checkbox]):not([disabled]), .modal-body textarea:not([readonly]), .modal-body select:not([disabled])");
  if (first) setTimeout(() => first.focus(), 30);
  return { el: dlg, close };
}
// Esc closes the topmost dialog wherever focus is (as TeleDesign's core.js does).
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const top = [...document.querySelectorAll("dialog.modal[open]")].pop();
  if (top && top._close) { e.preventDefault(); e.stopPropagation(); top._close(); }
}, true);
function confirmDialog(title, message, { confirm = "Confirm", danger = false, extra = null } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const acts = [{ label: "Cancel", kind: "ghost", onClick: () => { done = true; resolve(false); } }];
    if (extra) acts.push({ label: extra.label, kind: extra.kind || "ghost", onClick: () => { done = true; resolve(extra.value); } });
    acts.push({ label: confirm, kind: danger ? "danger" : "primary", primary: true, onClick: () => { done = true; resolve(true); } });
    modal({ title, body: typeof message === "string" ? h("p", { class: "muted" }, message) : message, width: "440px", actions: acts,
      onClose: () => { if (!done) resolve(false); } });
  });
}
function promptDialog(title, { label = "", hint = "", value = "", placeholder = "", confirm = "Create", validate = null } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const input = h("input", { class: "input", value, placeholder, autofocus: true, spellcheck: "false" });
    const err = h("div", { class: "field-err hidden" });
    const check = () => { const m = validate ? validate(input.value.trim()) : null; err.textContent = m || ""; err.classList.toggle("hidden", !m); input.classList.toggle("invalid", !!m); return !m; };
    const m = modal({
      title, width: "440px",
      body: h("label", { class: "field" }, label ? h("span", { class: "field-label" }, label) : null, input, err, hint ? h("span", { class: "field-hint" }, hint) : null),
      actions: [
        { label: "Cancel", kind: "ghost", onClick: () => { done = true; resolve(null); } },
        { label: confirm, kind: "primary", onClick: () => { if (!check()) return false; done = true; resolve(input.value.trim()); } },
      ],
      onClose: () => { if (!done) resolve(null); },
    });
    input.addEventListener("input", () => { if (input.classList.contains("invalid")) check(); });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); if (!check()) return; done = true; resolve(input.value.trim()); m.close(); } });
  });
}
function openTextViewer(title, text, subtitle) {
  const copyB = btn("Copy", { icon: "copy", kind: "ghost", onClick: () => copyText(text) });
  modal({ title, subtitle, cls: "wide", body: h("pre", { class: "viewer-pre" }, text), footLeft: copyB, actions: [{ label: "Close", kind: "primary" }] });
}

// ── Clipboard ──────────────────────────────────────────────────────────
async function copyText(text, what = "Copied to clipboard") {
  try { await navigator.clipboard.writeText(String(text)); }
  catch {
    const ta = h("textarea", { style: { position: "fixed", left: "-9999px" } }, String(text));
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } catch { /* ignore */ }
    ta.remove();
  }
  toast(what, { kind: "success", ms: 1600 });
}
function idChip(id, n = 8, title) {
  return h("span", { class: "idchip", title: title || String(id) }, shortId(id, n),
    h("button", { type: "button", title: "Copy ID", "aria-label": "Copy ID", onclick: (e) => { e.stopPropagation(); copyText(id, "ID copied"); } }, icon("copy")));
}

// ── Theme (shares TeleDesign's per-viewer "td:theme" preference) ───────
const prefs = {
  get(k, d) { try { const v = localStorage.getItem("td:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("td:" + k, JSON.stringify(v)); } catch { /* storage blocked */ } },
};
const themeMq = matchMedia("(prefers-color-scheme: light)");
function currentTheme() { const p = prefs.get("theme", "system"); return p === "system" ? (themeMq.matches ? "light" : "dark") : p; }
function applyTheme() {
  document.documentElement.dataset.theme = currentTheme();
  const b = $("themeToggle");
  if (b) { mount(b, icon(currentTheme() === "dark" ? "sun" : "moon")); b.title = `Switch to ${currentTheme() === "dark" ? "light" : "dark"} theme  (Alt+Shift+L)`; }
}
function toggleTheme() { prefs.set("theme", currentTheme() === "dark" ? "light" : "dark"); applyTheme(); }
themeMq.addEventListener?.("change", () => { if (prefs.get("theme", "system") === "system") applyTheme(); });

// ── Top bar: the shared <tc-appnav> (appnav.js) + this page's actions ──
// mode: "team" | "tasks"
function renderTopbar(host, mode) {
  const nav = h("tc-appnav", { active: mode },
    h("span", { class: "conn", id: "connState", title: "Connection to telecode's proxy" }, h("span", { class: "dot ok" }), "Connected"),
    h("span", { class: "divider-v" }),
    approvalsButton(),
    btn(null, { icon: "keyboard", kind: "quiet", title: "Keyboard shortcuts  (?)", onClick: () => showShortcuts() }),
    h("button", { type: "button", class: "btn btn-icon quiet", id: "themeToggle", onclick: toggleTheme }));
  host.replaceWith(nav);
  applyTheme();
  startApprovalsBadge();
  return nav;
}
let connOk = true;
function setConn(ok) {
  if (ok === connOk) return; connOk = ok;
  const el = $("connState"); if (!el) return;
  mount(el, h("span", { class: "dot " + (ok ? "ok" : "err pulse") }), ok ? "Connected" : "Proxy unreachable");
}

// ── Keyboard shortcuts ─────────────────────────────────────────────────
const SHORTCUTS = [];
function addShortcut(s) { SHORTCUTS.push(s); }
function isTyping(e) { const t = e.target; return t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)); }
function showShortcuts() {
  const body = h("div", { class: "keys" });
  let grp = null;
  for (const s of SHORTCUTS) {
    if (s.group !== grp) { grp = s.group; body.appendChild(h("div", { class: "grp" }, grp)); }
    body.append(h("span", null, s.desc), h("span", null, s.keys.map(k => h("kbd", null, k))));
  }
  modal({ title: "Keyboard shortcuts", body, width: "460px" });
}
document.addEventListener("keydown", (e) => {
  if (document.querySelector("dialog.modal[open]")) return;
  for (const s of SHORTCUTS) {
    if (!s.match(e)) continue;
    if (isTyping(e) && !s.whileTyping) continue;
    e.preventDefault(); s.run(e); return;
  }
});
function stdShortcuts() {
  addShortcut({ group: "General", desc: "Show shortcuts", keys: ["?"], match: e => e.key === "?" && !e.ctrlKey, run: showShortcuts });
  addShortcut({ group: "General", desc: "Toggle light / dark", keys: ["Alt", "Shift", "L"], whileTyping: true, match: e => e.altKey && e.shiftKey && e.code === "KeyL", run: toggleTheme });
  // Handled by <tc-appnav>; listed here so the sheet is complete.
  addShortcut({ group: "Switch app", desc: "Team Mode", keys: ["Alt", "Shift", "1"], match: () => false, run() {} });
  addShortcut({ group: "Switch app", desc: "Task Mode", keys: ["Alt", "Shift", "2"], match: () => false, run() {} });
  addShortcut({ group: "Switch app", desc: "TeleDesign", keys: ["Alt", "Shift", "3"], match: () => false, run() {} });
}
// Ctrl+Enter inside any [data-submit="<button id>"] container clicks that button.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || !(e.ctrlKey || e.metaKey)) return;
  if (document.querySelector("dialog.modal[open]")) return;
  const box = e.target.closest && e.target.closest("[data-submit]");
  if (!box) return;
  const b = $(box.dataset.submit);
  if (b && !b.disabled) { e.preventDefault(); b.click(); }
});

// ── Engine selector + local-mode switch ────────────────────────────────
const AGENT_ENGINES = ["CLAUDE_CODE", "CODEX", "ANTIGRAVITY"];
const ENGINE_LABELS = { CLAUDE_CODE: "Claude Code", CODEX: "Codex", ANTIGRAVITY: "Antigravity", ECHO: "Echo (test)" };
const LOCAL_TIP = "Run this engine on the local llama.cpp model through the telecode proxy (Claude Code → /v1/messages, Codex → /v1/responses, Antigravity → Gemini API)";
const engineSelects = [];
let taskTypesLoaded = null;
function engineOption(t) { return h("option", { value: t }, ENGINE_LABELS[t] ? `${ENGINE_LABELS[t]}  ·  ${t}` : t); }
// engineControl({selectId, localId, value, local, disabledSelect}) → {el, select, local}
function engineControl(o) {
  const select = h("select", { class: "select", id: o.selectId, title: "Task engine", disabled: o.disabledSelect || null }, AGENT_ENGINES.map(engineOption));
  const local = h("input", { type: "checkbox", id: o.localId, role: "switch" });
  const sw = h("label", { class: "switch-field", title: LOCAL_TIP, for: o.localId },
    h("span", { class: "switch" }, local, h("span")), h("span", null, "Local mode"), icon("info"));
  local.checked = !!o.local;
  const sync = () => {
    sw.classList.toggle("on", local.checked);
    const agentic = AGENT_ENGINES.includes(select.value);
    local.disabled = !agentic;
    sw.style.opacity = agentic ? "" : ".5";
    sw.title = agentic ? LOCAL_TIP : "Local mode applies to Claude Code, Codex and Antigravity only";
  };
  local.addEventListener("change", sync); select.addEventListener("change", sync);
  engineSelects.push({ select, sync, value: o.value });
  if (taskTypesLoaded) addExtraEngines(select, taskTypesLoaded);
  select.value = o.value && [...select.options].some(x => x.value === o.value) ? o.value : (o.value ? (select.appendChild(engineOption(o.value)), o.value) : "CLAUDE_CODE");
  sync();
  return { el: h("div", { class: "engine-row" }, select, sw), select, local, sync };
}
function addExtraEngines(select, types) {
  if (types.ECHO && ![...select.options].some(o => o.value === "ECHO")) select.appendChild(engineOption("ECHO"));
}
async function loadTaskTypes() {
  try { taskTypesLoaded = await api("/api/tasks/types"); }
  catch { taskTypesLoaded = {}; }
  for (const e of engineSelects) { const v = e.select.value; addExtraEngines(e.select, taskTypesLoaded); e.select.value = v; e.sync(); }
}

// ── Empty / loading states ─────────────────────────────────────────────
function emptyState(ic, title, text, action, small) {
  return h("div", { class: "empty" + (small ? " sm" : "") }, h("div", { class: "empty-ic" }, icon(ic)),
    h("div", { class: "empty-title" }, title), text ? h("p", null, text) : null, action || null);
}
function skeleton(n = 3, cls = "sk-line") { return h("div", null, Array.from({ length: n }, () => h("div", { class: "sk " + cls }))); }
function errorBox(msg, retry) {
  return h("div", { class: "err-box row", style: { alignItems: "flex-start" } }, icon("alert"), h("div", { class: "grow" }, msg), retry ? btn("Retry", { size: "sm", kind: "ghost", onClick: retry }) : null);
}

// ── Task event stream ──────────────────────────────────────────────────
// Groups consecutive tool calls into a collapsible block, merges streamed
// narrative deltas, and renders start/done/retry as compact meta lines.
const openToolGroups = new Set();
function evTime(e) { if (!e.ts) return null; const d = new Date(e.ts); return isNaN(d) ? null : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
function evText(e) {
  if (typeof e.text === "string") return e.text;
  if (typeof e.summary === "string") return e.summary;
  const { ts, kind, ...rest } = e; return JSON.stringify(rest);
}
function evRow(cls, ic, main, e) {
  return h("div", { class: "ev " + cls }, h("span", { class: "ev-ic" }, icon(ic)), h("div", { class: "ev-main" }, main), e && evTime(e) ? h("span", { class: "ev-t" }, evTime(e)) : null);
}
function detailsBlock(label, obj) {
  const txt = typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);
  return h("details", null, h("summary", null, icon("chevronRight"), label), h("pre", null, txt));
}
function kv(pairs) { return h("span", { class: "kv" }, pairs.filter(p => p[1] != null && p[1] !== "").map(([k, v]) => h("span", null, k + " ", h("b", null, String(v))))); }
function buildEventNodes(events, streamKey) {
  const out = [];
  let i = 0;
  while (i < events.length) {
    const e = events[i] || {}; const k = e.kind || "info";
    if (k === "tool") {
      const start = i, group = [];
      while (i < events.length && (events[i] || {}).kind === "tool") group.push(events[i++]);
      const gkey = streamKey + ":" + start;
      const wrap = h("div", { class: "tools" + (openToolGroups.has(gkey) ? " open" : "") });
      const names = group.map(g => g.tool || g.name || "tool");
      const head = h("button", { type: "button", class: "tools-head", "aria-expanded": String(openToolGroups.has(gkey)),
        onclick: () => { const on = wrap.classList.toggle("open"); head.setAttribute("aria-expanded", String(on)); on ? openToolGroups.add(gkey) : openToolGroups.delete(gkey); } },
        icon("chevronRight", "chev"), icon("wrench"), h("span", null, group.length === 1 ? "1 tool call" : `${group.length} tool calls`),
        h("span", { class: "names" }, [...new Set(names)].join(" · ")), evTime(group[group.length - 1]) ? h("span", { class: "ev-t" }, evTime(group[group.length - 1])) : null);
      const list = h("div", { class: "tools-list" }, group.map(g => {
        const name = g.tool || g.name || "tool";
        let summ = typeof g.summary === "string" ? g.summary : (g.input != null ? JSON.stringify(g.input) : "");
        if (summ.startsWith(name + ": ")) summ = summ.slice(name.length + 2);
        return h("div", { class: "tool-line", title: summ }, h("b", null, name), h("span", null, summ), evTime(g) ? h("span", { class: "ev-t" }, evTime(g)) : null);
      }));
      wrap.append(head, list); out.push(wrap); continue;
    }
    if (k === "narrative_delta") {
      let txt = ""; const first = e;
      while (i < events.length && (events[i] || {}).kind === "narrative_delta") txt += (events[i++].text || "");
      out.push(evRow("narrative", "message", txt, first)); continue;
    }
    i++;
    if (k === "narrative") out.push(evRow("narrative", "message", evText(e), e));
    else if (k === "start") {
      out.push(evRow("meta start", "play", [h("span", null, e.resumed ? "Resumed session " : "Started "),
        kv([["engine session", e.resumed_claude_session_id ? shortId(e.resumed_claude_session_id) : null], ["local", e.is_local ? "yes" : null], ["cwd", e.cwd]]),
        (e.prompt || e.cwd) ? detailsBlock("Details", (({ ts, kind, ...r }) => r)(e)) : null], e));
    } else if (k === "done") {
      out.push(evRow("meta ok", "check", [h("span", null, "Finished  "), kv([["tools", e.tool_count], ["turns", e.num_turns], ["cost", e.cost_usd != null ? fmtCost(e.cost_usd) : null],
        ["in", e.input_tokens != null ? fmtTokens(e.input_tokens) : null], ["out", e.output_tokens != null ? fmtTokens(e.output_tokens) : null],
        ["cache read", e.cache_read_tokens ? fmtTokens(e.cache_read_tokens) : null]])], e));
    } else if (k === "retry") {
      out.push(evRow("warn", "refresh", `API retry ${e.attempt ?? "?"}${e.max_retries != null ? "/" + e.max_retries : ""}${e.error ? " — " + (typeof e.error === "string" ? e.error : JSON.stringify(e.error)) : ""}`, e));
    } else if (k === "usage") {
      // running token totals; the "done" line already shows the final numbers
    } else if (k === "todo") {
      const todos = Array.isArray(e.todos) ? e.todos : [];
      out.push(evRow("meta", "check", [h("span", null, `Todos ${todos.filter(t => t.status === "completed").length}/${todos.length}  `),
        h("span", { class: "faint" }, todos.map(t => (t.status === "completed" ? "✓ " : t.status === "in_progress" ? "▸ " : "· ") + (t.text || "")).join("   "))], e));
    } else if (k === "warning") out.push(evRow("warn", "alert", evText(e), e));
    else if (k === "error") out.push(evRow("err", "alert", evText(e), e));
    else out.push(evRow("meta", "dot", [h("span", { class: "mono" }, k + "  "), evText(e)], e));
  }
  return out;
}
// Updates `host` in place; skips work when nothing changed, keeps the reader's
// scroll position unless they were already following the tail.
function updateStream(host, events, o = {}) {
  const sig = events.length + "|" + (o.status || "") + "|" + JSON.stringify(events[events.length - 1] || null).length;
  if (host._sig === sig) return;
  host._sig = sig;
  let body = host.querySelector(".stream-body");
  if (!body) {
    const copyB = btn(null, { icon: "copy", kind: "quiet", size: "sm", title: "Copy raw events (JSON)", onClick: () => copyText(JSON.stringify(host._events || [], null, 2), "Events copied") });
    const followB = btn(null, { icon: "arrowDown", kind: "quiet", size: "sm", title: "Jump to latest", onClick: () => { body.scrollTop = body.scrollHeight; } });
    host.classList.add("stream");
    mount(host, h("div", { class: "stream-head" }, icon("pulse"), h("span", { class: "grow ttl" }, "Events"), followB, copyB),
      body = h("div", { class: "stream-body" + (o.tall ? " tall" : "") }));
  }
  host._events = events;
  const ttl = host.querySelector(".stream-head .ttl");
  const tools = events.filter(e => e && e.kind === "tool").length;
  mount(ttl, "Events ", h("span", { class: "faint" }, `· ${events.length}${tools ? ` · ${tools} tool call${tools === 1 ? "" : "s"}` : ""}`));
  const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 24 || !body.childNodes.length;
  const shown = o.limit ? events.slice(-o.limit) : events;
  const offset = events.length - shown.length;
  const nodes = buildEventNodes(shown, (o.key || "s") + ":" + offset);
  if (!nodes.length) mount(body, h("div", { class: "ev meta" }, h("span", { class: "ev-ic" }, icon("clock")), h("div", { class: "ev-main" }, o.emptyText || "No events yet.")));
  else mount(body, offset ? h("div", { class: "ev meta" }, h("span", { class: "ev-ic" }, icon("more")), h("div", { class: "ev-main" }, `${offset} earlier event${offset === 1 ? "" : "s"} hidden`)) : null, nodes);
  if (atBottom) body.scrollTop = body.scrollHeight;
}

// ── Live events (SSE) with automatic fallback to polling ───────────────
// liveEvents(url, { types, onOpen, onEvent(type, data, id), onEnd(data), onDown, onFallback })
// → { close(), live }. EventSource cannot see HTTP status, so an error before the
// first open (a 404 from an older server, a proxy without the route) or a CLOSED
// stream means "not available": it closes and calls onFallback — keep polling.
// After a successful open the browser reconnects on its own (resuming from the
// last event id); onDown fires meanwhile so callers can poll until it is back.
function liveEvents(url, o = {}) {
  const none = { close() {}, get live() { return false; } };
  if (typeof EventSource === "undefined") { if (o.onFallback) o.onFallback(); return none; }
  let opened = false, closed = false;
  let es;
  try { es = new EventSource(url); } catch { if (o.onFallback) o.onFallback(); return none; }
  es.onopen = () => { opened = true; if (o.onOpen) o.onOpen(); };
  es.onerror = () => {
    if (closed) return;
    if (!opened || es.readyState === EventSource.CLOSED) { closed = true; es.close(); if (o.onFallback) o.onFallback(); }
    else if (o.onDown) o.onDown();
  };
  for (const t of (o.types || [])) {
    es.addEventListener(t, (ev) => {
      let d = null; try { d = JSON.parse(ev.data); } catch { /* keep null */ }
      if (t === "end") { closed = true; es.close(); if (o.onEnd) o.onEnd(d); return; }
      if (o.onEvent) o.onEvent(t, d, ev.lastEventId);
    });
  }
  return { close() { closed = true; es.close(); }, get live() { return opened && !closed; } };
}
// One EventSource for every global-feed listener on the page (task, run,
// approval and trigger summaries). Browsers allow ~6 connections per host, so
// each listener opening its own stream would starve plain fetches.
// globalFeed(types, onEvent(type, data)) → {live}
const FEED_TYPES = ["task.status", "run.update", "approval.created", "approval.decided",
  "trigger.update", "trigger.fire", "trigger.notice", "trigger.deleted"];
const feedSubs = [];
let feedStream = null;
function globalFeed(types, onEvent) {
  feedSubs.push({ types: new Set(types), onEvent });
  if (!feedStream) {
    feedStream = liveEvents("/api/events?kinds=task,run,approval,trigger", { types: FEED_TYPES,
      onEvent: (t, d) => { for (const s of feedSubs) if (s.types.has(t)) { try { s.onEvent(t, d); } catch (e) { console.warn(e); } } } });
  }
  return { get live() { return !!(feedStream && feedStream.live); } };
}
// Coalesce bursts (one refresh per `ms`, trailing).
function debounced(fn, ms = 300) { let t = null; return (...a) => { if (t) return; t = setTimeout(() => { t = null; fn(...a); }, ms); }; }

// ── Task result (stats + text) ─────────────────────────────────────────
function resultText(r) {
  if (r == null) return "";
  if (typeof r === "string") return r;
  if (typeof r.result === "string") return r.result;
  if (r.result && typeof r.result.result === "string") return r.result.result;
  return "";
}
function statTile(ic, k, v, title) { return h("div", { class: "stat", title: title || null }, h("div", { class: "k" }, icon(ic), k), h("div", { class: "v" }, v)); }
function renderStats(r) {
  const tok = (r && r.tokens) || {};
  return h("div", { class: "stats" },
    statTile("coins", "Cost", fmtCost(r.cost_usd)),
    statTile("clock", "Duration", fmtMs(r.duration_ms)),
    statTile("history", "Turns", fmtNum(r.num_turns)),
    statTile("arrowIn", "Input tok", fmtNum(tok.input), tok.cache_read != null ? `cache read ${fmtNum(tok.cache_read)} · cache write ${fmtNum(tok.cache_write)}` : null),
    statTile("arrowOut", "Output tok", fmtNum(tok.output)),
    Array.isArray(r.tool_calls) ? statTile("wrench", "Tool calls", fmtNum(r.tool_calls.length)) : null);
}
function renderResultBlock(r, o = {}) {
  let text = resultText(r);
  if (!text && o.fallback) text = o.fallback;
  let raw = "";
  if (!text && r && typeof r === "object") raw = JSON.stringify(r, null, 2);
  const body = text || raw;
  const box = h("div", { class: "col", style: { gap: "10px" } });
  if (r && typeof r === "object") box.appendChild(renderStats(r));
  if (body) box.appendChild(h("div", { class: "result" },
    h("div", { class: "result-head" }, icon(text ? "message" : "code"), h("span", { class: "grow" }, text ? (o.title || "Result") : "Raw result"),
      btn("Copy", { icon: "copy", kind: "quiet", size: "sm", title: "Copy result", onClick: () => copyText(body, "Result copied") })),
    h("pre", null, body)));
  return box;
}
function renderErrorBlock(err, title = "Error") {
  return h("div", { class: "err-box" }, h("div", { class: "row" }, icon("alert"), h("b", { class: "grow" }, title),
    btn("Copy", { icon: "copy", kind: "quiet", size: "sm", onClick: () => copyText(err, "Error copied") })), h("pre", null, String(err)));
}

// ── Form validation ──────────────────────────────────────────────────
// fieldError(input, msg): shows msg under the input's .field ("" clears). Returns true when valid.
function fieldError(input, msg) {
  const f = input.closest(".field") || input.parentElement;
  let err = f.querySelector(".field-err");
  if (!err) {
    err = h("span", { class: "field-err" });
    let anchor = input; while (anchor.parentElement && anchor.parentElement !== f) anchor = anchor.parentElement;
    anchor.insertAdjacentElement("afterend", err);
  }
  err.textContent = msg || ""; err.classList.toggle("hidden", !msg); input.classList.toggle("invalid", !!msg);
  if (msg) input.focus();
  return !msg;
}
function posInt(v) { const n = parseInt(v, 10); return Number.isFinite(n) && n > 0 && String(n) === String(v).trim() ? n : null; }

// ── Files ──────────────────────────────────────────────────────────────
function triggerDownload(url, name) {
  const a = h("a", { href: url, download: name || "" });
  document.body.appendChild(a); a.click(); a.remove();
}
function wireDrop(el, onFiles) {
  ["dragenter", "dragover"].forEach(ev => el.addEventListener(ev, (e) => { e.preventDefault(); el.classList.add("over"); }));
  ["dragleave", "drop"].forEach(ev => el.addEventListener(ev, (e) => { e.preventDefault(); el.classList.remove("over"); }));
  el.addEventListener("drop", (e) => { if (e.dataTransfer && e.dataTransfer.files.length) onFiles(e.dataTransfer.files); });
}

// ── P2: session policy, budgets, handoffs, diffs, snapshots ───────────
// Shared by Team Mode (pipeline editor, run monitor, workspace view) and Task
// Mode (session snapshots). All server text lands in text nodes via h().
const SESSION_POLICIES = [
  ["", "Default (by position)"], ["resume", "Resume"], ["fork", "Fork"], ["fresh", "Fresh"],
  ["fresh_handoff", "Fresh + handoff"], ["ephemeral", "Ephemeral copy"],
];
const POLICY_HINTS = {
  "": "Resume in the job workspace for a single-step phase; an ephemeral copy in a parallel phase.",
  resume: "Continue this agent's conversation in the job workspace.",
  fork: "Branch the previous step's (same engine) or this agent's conversation — Claude --fork-session, Codex exec fork. Antigravity: fresh + handoff.",
  fresh: "New conversation in the job workspace.",
  fresh_handoff: "New conversation, always given the previous step's handoff.",
  ephemeral: "New conversation in a throwaway copy of the workspace; changed files are kept as artifacts.",
};
function policySelect(value, o = {}) {
  const sel = h("select", { class: "select", disabled: o.disabled || null, title: "Session policy" },
    SESSION_POLICIES.map(([v, l]) => h("option", { value: v, selected: (value || "") === v || null }, l)));
  return sel;
}
// budgetFields({max_usd, max_tokens, max_seconds}, {placeholder, disabled, onChange}) → {el, get()}
function budgetFields(b, o = {}) {
  b = b || {};
  const mk = (key, ph, step) => {
    const i = h("input", { class: "input", type: "number", min: "0", step, value: b[key] != null ? b[key] : "",
      placeholder: ph, disabled: o.disabled || null, "data-budget": key });
    if (o.onChange) i.addEventListener("input", () => o.onChange(get()));
    return i;
  };
  const usd = mk("max_usd", o.placeholder || "no cap", "0.01"), tok = mk("max_tokens", o.placeholder || "no cap", "1000"),
    sec = mk("max_seconds", o.placeholder || "no cap", "1");
  const get = () => {
    const out = {};
    for (const [k, i] of [["max_usd", usd], ["max_tokens", tok], ["max_seconds", sec]]) {
      const v = i.value.trim(); if (v !== "" && Number(v) > 0) out[k] = k === "max_usd" ? Number(v) : Math.round(Number(v));
    }
    return out;
  };
  const cell = (label, input, unit) => h("label", { class: "field", style: { marginBottom: 0 } }, h("span", { class: "field-label" }, label),
    h("span", { class: "suffix" }, input, h("span", null, unit)));
  const el = h("div", { class: "budget-row" }, cell("Max cost", usd, "$"), cell("Max tokens", tok, "tok"), cell("Max time", sec, "sec"));
  return { el, get, inputs: { usd, tok, sec } };
}
// Spent vs budget. budget: {max_usd,max_tokens,max_seconds}, spent: {usd, usd_complete, tokens, seconds}
function budgetMeter(budget, spent) {
  budget = budget || {}; spent = spent || {};
  const row = (ic, k, used, cap, fmt, note) => {
    const pct = cap ? Math.min(100, Math.round(100 * (used || 0) / cap)) : 0;
    return h("div", { class: "m", title: note || null },
      h("div", { class: "k" }, icon(ic), k, cap ? h("span", { class: "grow" }) : null, cap ? h("span", null, pct + "%") : null),
      h("div", { class: "v" }, fmt(used), cap ? " / " + fmt(cap) : " · no cap"),
      cap ? h("div", { class: "bar" + (pct >= 100 ? " err" : pct >= 80 ? " warn" : "") }, h("i", { style: { width: pct + "%" } })) : null);
  };
  return h("div", { class: "meter" },
    row("coins", "Cost", spent.usd, budget.max_usd, (v) => fmtCost(v || 0), spent.usd_complete === false ? "Codex / Antigravity report no cost — the dollar total is incomplete" : null),
    row("hash", "Tokens", spent.tokens, budget.max_tokens, (v) => fmtTokens(v || 0), "input + cache writes + output (cache reads not counted)"),
    row("clock", "Time", spent.seconds, budget.max_seconds, (v) => fmtMs(Math.round((v || 0) * 1000))));
}
const HANDOFF_TONE = { done: "ok", partial: "warn", blocked: "warn", failed: "err", unknown: "" };
const VERDICT_TONE = { pass: "ok", fail: "err", unknown: "" };
// renderHandoffCard(handoff, {artifactUrl(path) → url|null})
function renderHandoffCard(ho, o = {}) {
  if (!ho) return null;
  const list = (title, items) => items && items.length ? h("div", null, h("h4", null, title), h("ul", null, items.map(x => h("li", null, x)))) : null;
  const arts = (ho.artifacts || []);
  return h("div", { class: "handoff" },
    h("div", { class: "handoff-head" }, icon("flag"), h("b", null, "Handoff"),
      statusPill(ho.status || "unknown", { tone: HANDOFF_TONE[ho.status] ?? "" }),
      h("span", { class: "pill " + (VERDICT_TONE[ho.verdict] ?? "") }, "verdict: " + (ho.verdict || "unknown")),
      ho.derived ? h("span", { class: "pill", title: ho.derive_reason || "" }, "derived from reply") : null,
      h("span", { class: "grow" }),
      ho.summary ? btn("Copy", { icon: "copy", kind: "quiet", size: "sm", onClick: () => copyText(ho.summary, "Summary copied") }) : null),
    h("div", { class: "handoff-body" },
      ho.summary ? h("div", { class: "summary" }, ho.summary) : null,
      list("Decisions", ho.decisions), list("Open questions", ho.open_questions), list("Next steps", ho.next_steps),
      arts.length ? h("div", { class: "arts" }, h("h4", null, `Artifacts (${arts.length})`), h("div", { class: "files" }, arts.map(a => {
        const url = o.artifactUrl && a.stored_path && !a.is_dir ? o.artifactUrl(a.path) : null;
        return h("div", { class: "frow" }, icon(a.is_dir ? "folder" : "file"), h("span", { class: "nm", title: a.stored_path || a.path }, a.path),
          a.kind ? h("span", { class: "pill" }, a.kind) : null,
          h("span", { class: "meta ellipsis", style: { maxWidth: "45%" }, title: a.description || "" }, a.missing ? "missing" : a.skipped || a.description || ""),
          a.bytes != null ? h("span", { class: "meta" }, formatBytes(a.bytes)) : null,
          url ? h("span", { class: "acts" }, h("a", { class: "btn quiet sm btn-icon", href: url, title: "Download artifact" }, icon("download"))) : null);
      }))) : null));
}
// Unified diff → coloured lines (text nodes only).
function diffView(text, truncated) {
  const lines = String(text || "").split("\n");
  return h("pre", { class: "diff-view" }, lines.map(l => {
    const cls = l.startsWith("diff --git") ? "dl-file" : l.startsWith("@@") ? "dl-hunk"
      : (l.startsWith("+++") || l.startsWith("---") || l.startsWith("index ") || l.startsWith("new file") || l.startsWith("deleted file") || l.startsWith("Binary")) ? "dl-meta"
      : l.startsWith("+") ? "dl-add" : l.startsWith("-") ? "dl-del" : "";
    return h("span", { class: cls || null }, l || " ");
  }), truncated ? h("span", { class: "dl-meta" }, "… diff truncated — pick a file for its full diff") : null);
}
// openDiffModal({title, subtitle, load(path|null) → Promise<{files, diff, truncated}>, footLeft})
async function openDiffModal(o) {
  const filesHost = h("div", { class: "diff-files" }, skeleton(4, "sk-row"));
  const viewHost = h("div", { style: { minWidth: 0 } }, skeleton(8));
  const m = modal({ title: o.title, subtitle: o.subtitle, cls: "wide", width: "1100px",
    body: h("div", { class: "diff-grid" }, filesHost, viewHost), footLeft: o.footLeft || h("span"),
    actions: [{ label: "Close", kind: "primary" }] });
  let current = null;
  const show = async (path) => {
    current = path;
    [...filesHost.querySelectorAll("button")].forEach(b => b.classList.toggle("on", (b.dataset.path || null) === path));
    mount(viewHost, skeleton(8));
    try {
      const d = await o.load(path);
      if (current !== path) return;
      mount(viewHost, d.diff ? diffView(d.diff, d.truncated) : emptyState("check", "No changes", path ? "This file did not change." : "Nothing changed between these snapshots.", null, true));
      return d;
    } catch (e) { mount(viewHost, errorBox(e.message)); }
  };
  const d = await show(null);
  if (!d) { mount(filesHost, h("div", { class: "empty sm" }, h("p", null, "—"))); return m; }
  const files = d.files || [];
  const glyph = { added: "A", modified: "M", deleted: "D" };
  mount(filesHost,
    h("button", { type: "button", class: "on", onclick: () => show(null) }, icon("layers"), h("span", { class: "nm" }, `All files (${files.length})`)),
    files.map(f => h("button", { type: "button", "data-path": f.path, title: f.path, onclick: () => show(f.path) },
      h("span", { class: "st " + f.change }, glyph[f.change] || "M"), h("span", { class: "nm" }, f.path),
      h("span", { class: "n" }, f.binary ? "bin" : `+${f.additions ?? 0} −${f.deletions ?? 0}`))));
  return m;
}
const SNAP_TONE = { before: "", after: "accent", manual: "violet", restore: "ok", safety: "warn" };
// renderSnapshotTimeline(host, {sid, ns, title}) — list + diff vs previous + restore + snapshot now.
async function renderSnapshotTimeline(host, o) {
  const q = o.ns ? `?namespace=${encodeURIComponent(o.ns)}` : "";
  const base = `/api/sessions/${encodeURIComponent(o.sid)}/snapshots`;
  const reload = () => renderSnapshotTimeline(host, o);
  mount(host, skeleton(3, "sk-row"));
  let r;
  try { r = await api(base + q); } catch (e) { mount(host, h("div", { class: "card-body" }, errorBox(e.message, reload))); return; }
  const snaps = r.snapshots || [];
  const head = h("div", { class: "row", style: { padding: "8px 12px", borderBottom: "1px solid var(--border)" } },
    h("span", { class: "faint grow", style: { fontSize: "12px" } }, r.enabled ? `${snaps.length} snapshot${snaps.length === 1 ? "" : "s"} · shadow git, the workspace's own .git is never touched` : "Snapshots are off (tasks.snapshots.enabled) or git is not on PATH"),
    r.enabled ? btn("Snapshot now", { icon: "save", kind: "ghost", size: "sm", onClick: async () => {
      try { await api(base + q, jsonOpts("POST", { label: "manual snapshot" })); toast("Snapshot taken", { kind: "success" }); reload(); }
      catch (e) { toast(e.message, { kind: "error" }); }
    } }) : null,
    btn(null, { icon: "refresh", kind: "quiet", size: "sm", title: "Refresh", onClick: reload }));
  if (!snaps.length) { mount(host, head, emptyState("history", "No snapshots yet", "A snapshot is taken before and after every pipeline step, or press Snapshot now.", null, true)); return; }
  mount(host, head, h("div", { class: "files snaps" }, snaps.map((s, i) => h("div", { class: "frow" },
    h("span", { class: "kind" }, h("span", { class: "pill " + (SNAP_TONE[s.kind] ?? "") }, s.kind || "snap")),
    h("span", { class: "nm", title: s.label }, s.label || s.sha.slice(0, 8)),
    s.run ? h("span", { class: "meta mono", title: "run " + s.run + (s.step ? "\nstep " + s.step : "") }, "run " + shortId(s.run, 6)) : null,
    h("span", { class: "meta mono", title: s.sha }, s.sha.slice(0, 8)),
    h("span", { class: "meta", title: fmtDateTime(s.created_at) }, relTime(s.created_at)),
    h("span", { class: "acts" },
      i < snaps.length - 1 ? btn(null, { icon: "code", kind: "quiet", size: "sm", title: "Diff vs the previous snapshot", onClick: () => openDiffModal({
        title: "Snapshot diff", subtitle: `${snaps[i + 1].sha.slice(0, 8)} → ${s.sha.slice(0, 8)} · ${s.label || ""}`,
        load: (path) => api(`${base}/${s.sha}/diff${q}${q ? "&" : "?"}base=${snaps[i + 1].sha}${path ? "&path=" + encodeURIComponent(path) : ""}`) }) }) : null,
      btn(null, { icon: "history", kind: "quiet", size: "sm", title: "Restore the workspace to this snapshot", onClick: async () => {
        const ok = await confirmDialog("Restore this snapshot?", h("p", { class: "muted" }, "The workspace's tracked files become exactly what they were at ",
          h("code", null, s.sha.slice(0, 8)), " (", s.label || "", "). The current state is snapshotted first, so this can be undone."), { confirm: "Restore", danger: true });
        if (!ok) return;
        try { await api(`${base}/${s.sha}/restore${q}`, jsonOpts("POST", {})); toast("Workspace restored", { kind: "success" }); reload(); if (o.onRestored) o.onRestored(); }
        catch (e) { toast(e.message, { kind: "error" }); }
      } }))))));
}

// ── P3: approvals inbox (badge in <tc-appnav>, inbox panel, gate cards) ──
// GET /api/approvals?status=pending|all, POST /api/approvals/{id}/approve|reject
// {note?, edited_text?}; live via /api/events?kinds=approval. After any decision
// made here a "tc:approval" window event fires so the page can refresh.
const APPROVAL_KIND = { gate: "Pipeline gate", tool: "Tool permission", memory: "Memory change" };
let approvalsCount = 0;
const approvalListeners = new Set();
function onApprovalsChange(fn) { approvalListeners.add(fn); return () => approvalListeners.delete(fn); }
function approvalsButton() {
  const count = h("span", { class: "count accent hidden", id: "approvalsCount" }, "0");
  return h("button", { type: "button", class: "btn quiet approvals-btn", id: "approvalsBtn", title: "Approvals inbox — gates waiting for a decision",
    "aria-label": "Approvals inbox", onclick: () => openApprovalsInbox() }, icon("inbox"), count);
}
function setApprovalsCount(n) {
  approvalsCount = n;
  const c = $("approvalsCount"); if (!c) return;
  c.textContent = n > 99 ? "99+" : String(n); c.classList.toggle("hidden", !n);
  const b = $("approvalsBtn"); if (b) { b.classList.toggle("on", n > 0); b.title = n ? `${n} approval${n === 1 ? "" : "s"} waiting — open the inbox` : "Approvals inbox — nothing waiting"; }
}
let approvalsStarted = false;
async function refreshApprovalsCount() {
  try { const r = await api("/api/approvals?status=pending"); setApprovalsCount(r.pending_count || 0); approvalListeners.forEach(fn => { try { fn(r); } catch { /* ignore */ } }); }
  catch { /* proxy down: keep the last count */ }
}
function startApprovalsBadge() {
  if (approvalsStarted) return; approvalsStarted = true;
  refreshApprovalsCount();
  const soon = debounced(refreshApprovalsCount, 400);
  const feed = globalFeed(["approval.created", "approval.decided"], soon);
  let ticks = 0;
  setInterval(() => { if (!feed.live || ++ticks % 4 === 0) refreshApprovalsCount(); }, 15000);
}
async function decideApproval(ap, decision, { note, edited } = {}) {
  const body = {};
  if (note) body.note = note;
  if (edited) body.edited_text = edited;
  const r = await api(`/api/approvals/${encodeURIComponent(ap.id)}/${decision}`, jsonOpts("POST", body));
  toast(decision === "approve" ? "Approved" : "Rejected", { kind: "success" });
  window.dispatchEvent(new CustomEvent("tc:approval", { detail: r.approval }));
  refreshApprovalsCount();
  return r.approval;
}
// approvalCard(ap, {onDone(ap), compact}) — the decision UI shared by the inbox and the run monitor's gate card.
function approvalCard(ap, o = {}) {
  const pending = ap.status === "pending";
  const payload = ap.payload || {};
  const jobLink = payload.job_id ? h("a", { href: `/team#/job/${encodeURIComponent(payload.job_id)}`, title: "Open the job and its run" }, icon("workflow"), " ", payload.job_title || "job") : null;
  const head = h("div", { class: "apv-head" }, icon(ap.kind === "gate" ? "gate" : "inbox"),
    h("b", { class: "ellipsis grow" }, ap.title || "Approval"),
    h("span", { class: "pill violet" }, APPROVAL_KIND[ap.kind] || ap.kind),
    statusPill(ap.status),
    h("span", { class: "faint", title: fmtDateTime(ap.created_at), style: { fontSize: "11.5px" } }, relTime(ap.created_at)));
  const body = ap.kind === "memory" && window.TCMemory ? TCMemory.approvalBody(ap)   // P4: a memory diff (shared/memory.js)
    : ap.body ? h("pre", { class: "apv-body" }, ap.body) : null;
  const meta = h("div", { class: "row wrap faint", style: { fontSize: "11.5px", gap: "10px" } }, jobLink,
    ap.run_id ? h("span", { class: "mono" }, "run " + shortId(ap.run_id, 8)) : null,
    ap.telegram ? h("span", { title: "Also posted to Telegram with Approve / Reject buttons" }, icon("send"), " on Telegram") : null);
  const box = h("div", { class: "apv" + (o.compact ? " compact" : "") }, head, meta, body);
  if (!pending) {
    box.append(h("div", { class: "apv-decided" }, h("span", null, `${ap.status} by `, h("b", null, ap.decided_by || "?"), " · ", relTime(ap.decided_at)),
      ap.decision_note ? h("div", null, "Note: ", ap.decision_note) : null,
      ap.edited_text ? h("div", null, h("span", { class: "faint" }, "Edited text handed on: "), h("pre", { class: "apv-body" }, ap.edited_text)) : null));
    return box;
  }
  const note = h("input", { class: "input", placeholder: "Note (optional) — recorded with the decision", maxlength: "2000" });
  const edit = h("textarea", { class: "input hidden", rows: "4", placeholder: ap.kind === "gate"
    ? "What the next step should be told — replaces the default “Approved by …” note and becomes the gate's handoff"
    : "The edited version to approve" });
  const busy = (on) => [...box.querySelectorAll(".apv-acts button")].forEach(b => { b.disabled = on; });
  const go = async (decision, withEdit) => {
    if (withEdit && !edit.value.trim()) { fieldError(edit, "Write the edited text, or use plain Approve"); return; }
    busy(true);
    try { const done = await decideApproval(ap, decision, { note: note.value.trim(), edited: withEdit ? edit.value.trim() : "" }); if (o.onDone) o.onDone(done); }
    catch (e) { toast(e.message, { kind: "error" }); busy(false); }
  };
  const editBtn = btn("Edit & approve", { icon: "edit", kind: "ghost", size: "sm", title: "Approve with your own text for the next step", onClick: () => {
    if (edit.classList.contains("hidden")) { edit.classList.remove("hidden"); edit.focus(); editBtn.querySelector("span").textContent = "Approve edited"; }
    else go("approve", true);
  } });
  box.append(h("div", { class: "field", style: { margin: 0 } }, edit), note,
    h("div", { class: "row apv-acts" },
      btn("Approve", { icon: "check", kind: "primary", size: "sm", onClick: () => go("approve", false) }), ap.kind === "memory" ? null : editBtn,
      h("span", { class: "grow" }),
      btn("Reject", { icon: "x", kind: "danger", size: "sm", onClick: () => go("reject", false) })));
  return box;
}
async function openApprovalsInbox() {
  let tab = "pending";
  const list = h("div", { class: "col", style: { gap: "10px" } }, skeleton(3, "sk-block"));
  const seg = h("div", { class: "seg sm" });
  const load = async () => {
    mount(seg, [["pending", "Waiting"], ["all", "History"]].map(([k, l]) => h("button", { type: "button", class: tab === k ? "on" : "", onclick: () => { tab = k; load(); } },
      l, k === "pending" && approvalsCount ? h("span", { class: "faint" }, " " + approvalsCount) : null)));
    try {
      const r = await api(`/api/approvals?status=${tab}`);
      setApprovalsCount(r.pending_count || 0);
      const rows = (r.approvals || []).filter(a => tab === "all" || a.status === "pending");
      if (!rows.length) { mount(list, emptyState("inbox", tab === "pending" ? "Nothing waiting" : "No approvals yet", tab === "pending" ? "Pipeline gates show up here (and on Telegram when the bot runs) until someone decides." : "Decided approvals are kept here.", null, true)); return; }
      mount(list, rows.map(ap => approvalCard(ap, { onDone: () => load() })));
    } catch (e) { mount(list, errorBox(e.message, load)); }
  };
  const m = modal({ title: "Approvals", subtitle: "Human decisions the pipelines are waiting on", cls: "wide", width: "760px",
    body: h("div", { class: "col", style: { gap: "12px" } }, h("div", { class: "row" }, seg, h("span", { class: "grow" }), btn(null, { icon: "refresh", kind: "quiet", size: "sm", title: "Refresh", onClick: () => load() })), list),
    footLeft: h("span", { class: "faint", style: { fontSize: "11.5px" } }, "Decisions made here or on Telegram apply at once; the run continues from the gate."),
    actions: [{ label: "Close", kind: "ghost" }] });
  const off = onApprovalsChange(() => { if (m.el.open && tab === "pending") load(); });
  m.el.addEventListener("close", off);
  load();
  return m;
}

// openTaskModal(taskId, title) — one task's events + reply in a dialog (map workers,
// loop graders, trigger fires). Refreshes every 2 s while the task is live.
function openTaskModal(taskId, title) {
  const head = h("div", { class: "row wrap" });
  const stream = h("div");
  const res = h("div");
  const m = modal({ title: title || "Task", subtitle: taskId, cls: "wide", width: "900px",
    body: h("div", { class: "col", style: { gap: "12px" } }, head, stream, res), actions: [{ label: "Close", kind: "ghost" }] });
  let timer = null;
  const load = async () => {
    let tk;
    try { tk = await api(`/api/tasks/${encodeURIComponent(taskId)}`); } catch (e) { mount(res, errorBox(e.message)); return; }
    const md = tk.metadata || {};
    mount(head, statusPill(tk.status), h("b", null, ENGINE_LABELS[tk.task_type] || tk.task_type), idChip(tk.task_id, 13),
      md.trigger_name ? h("span", { class: "pill" }, icon("clock"), md.trigger_name) : null,
      md.worker ? h("span", { class: "pill accent" }, "worker #" + md.worker) : null, md.role ? h("span", { class: "pill" }, md.role) : null,
      h("span", { class: "faint", style: { fontSize: "12px" } }, fmtDateTime(tk.created_at)));
    updateStream(stream, md.events || [], { key: "modal:" + taskId, limit: 300, status: tk.status, tall: true, emptyText: "No events" });
    mount(res, h("div", { class: "col", style: { gap: "10px" } }, tk.result ? renderResultBlock(tk.result) : null, tk.error ? renderErrorBlock(tk.error) : null));
    if (!["pending", "running"].includes(tk.status) && timer) { clearInterval(timer); timer = null; }
  };
  load();
  timer = setInterval(() => { if (!m.el.isConnected) { clearInterval(timer); return; } load(); }, 2000);
  return m;
}
