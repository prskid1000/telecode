// TeleDesign — host side of the preview bridge (contract §6) and the editor
// hooks (contract §10). Every inbound message is checked against the expected
// origin before anything is read from it.
import { bus, h } from "./core.js";
import { S } from "./state.js";

// iframe element -> {file, role:"preview"|"board"|"presenter"|"card", ready:{…}}
const frames = new Map();

export function registerFrame(iframe, info) { frames.set(iframe, info); }
export function unregisterFrame(iframe) { frames.delete(iframe); }
export function frameInfo(iframe) { return frames.get(iframe); }

export function makePreviewFrame(src, info, attrs = {}) {
  const f = h("iframe", {
    src, title: info.title || info.file || "Preview",
    sandbox: "allow-scripts allow-same-origin allow-popups allow-forms",
    referrerpolicy: "no-referrer-when-downgrade",
    loading: attrs.lazy ? "lazy" : null,
    class: attrs.cls || null,
  });
  registerFrame(f, info);
  return f;
}

function frameForSource(source) {
  for (const [f, info] of frames) {
    if (!f.isConnected) { frames.delete(f); continue; }
    if (f.contentWindow === source) return [f, info];
  }
  return [null, null];
}

export function post(iframe, msg) {
  try { iframe?.contentWindow?.postMessage(msg, S.previewOrigin); } catch { /* frame gone */ }
}
export function broadcast(msg, filter) {
  for (const [f, info] of frames) {
    if (!f.isConnected) { frames.delete(f); continue; }
    if (info.role === "card" || info.role === "compare") continue;
    if (!filter || filter(info, f)) post(f, msg);
  }
}
export function framesFor(file) {
  return [...frames].filter(([f, i]) => f.isConnected && i.file === file).map(([f]) => f);
}

const PREVIEW_TYPES = new Set([
  "td:ready", "td:console", "td:select", "td:comment-target", "td:text-edit", "td:style-edit", "td:draw",
  "td:slide-changed", "td:eval-result", "td:navigate", "td:complete",
  "__edit_mode_available", "__edit_mode_dismissed", "__edit_mode_set_keys",
  // The deck contract's reference skeleton uses this spelling; accept both.
  "slideIndexChanged", "td:pin-click", "td:pong",
]);

window.addEventListener("message", (e) => {
  const d = e.data;
  if (!d || typeof d !== "object" || typeof d.type !== "string") return;

  // Editor iframe (same origin as this page, served at /design/editor/).
  if (d.type.startsWith("td-editor:")) {
    if (e.origin !== location.origin) return;
    bus.emit("editor", { ...d, source: e.source });
    return;
  }
  if (e.origin !== S.previewOrigin) return;
  if (!PREVIEW_TYPES.has(d.type)) return;
  const [frame, info] = frameForSource(e.source);
  if (!frame) return;           // not one of ours (e.g. a nested iframe)
  if (d.type === "td:ready") info.ready = d;
  bus.emit("preview", { msg: d, frame, info });
});
