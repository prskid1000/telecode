// TeleDesign — reacts to messages from preview iframes (contract §6): comment
// targets, selection, inline edits, knobs, draw, tweaks, console, deck position,
// in-page navigation and window.telecode.complete(). Loaded once at boot.
import { h, icon, btn, api, tryApi, toast, toastError, bus, P_, prefs, features, debounce } from "./core.js";
import { S, loadComments, previewUrl } from "./state.js";
import { post, framesFor } from "./bridge.js";

export const live = {
  console: new Map(),     // file -> [{level, args, at}]
  tweaks: new Map(),      // file -> {available, on}
  slides: new Map(),      // file -> {index, count}
  ready: new Map(),       // file -> td:ready payload
};

bus.on("preview", ({ msg, frame, info }) => {
  const file = info.file;
  switch (msg.type) {
    case "td:ready":
      live.ready.set(file, msg);
      if (msg.deck && msg.deck.count) live.slides.set(file, { index: live.slides.get(file)?.index || 1, count: msg.deck.count });
      if (msg.tweaks) setTweaks(file, { available: true });
      // Re-apply the current mode: a reload resets the page to "view".
      if (S.mode !== "view") post(frame, { type: "td:set-mode", mode: S.mode });
      if (live.tweaks.get(file)?.on) post(frame, { type: "__activate_edit_mode" });
      sendPins(file, frame);
      bus.emit("live", { file, what: "ready" });
      break;
    case "td:console": {
      const list = live.console.get(file) || [];
      list.push({ level: msg.level || "log", args: (msg.args || []).map(String), at: msg.at || new Date().toISOString() });
      if (list.length > 500) list.splice(0, list.length - 500);
      live.console.set(file, list);
      bus.emit("console", file);
      break;
    }
    case "td:slide-changed": case "slideIndexChanged": {
      const index = +(msg.index ?? msg.slideIndex ?? 1) || 1;
      const count = +(msg.count ?? msg.total ?? live.slides.get(file)?.count ?? 0) || 0;
      live.slides.set(file, { index, count });
      bus.emit("slide", { file, index, count, frame });
      break;
    }
    case "__edit_mode_available": setTweaks(file, { available: true }); break;
    case "__edit_mode_dismissed": setTweaks(file, { on: false }); break;
    case "__edit_mode_set_keys": queueTweak(file, msg.edits || {}); break;
    case "td:select":
      S.selection = { ...msg, file };
      bus.emit("selection", S.selection);
      // Selection rides along with the next chat turn as a removable chip (chat.js).
      if (info.role === "preview" || info.role === "board") bus.emit("chat-context", { kind: "element", sel: S.selection });
      if (S.mode === "edit" || S.mode === "knobs") import("./workspace.js").then((m) => { if (S.panel !== "inspect") m.setPanel("inspect"); });
      break;
    case "td:comment-target": commentPopover(msg, frame, file); break;
    case "td:text-edit": writeBack(file, { op: "text", value: msg.new_text, td_id: msg.td_id, source_loc: msg.source_loc, selector: msg.selector, old: msg.old_text }); break;
    case "td:style-edit": writeBack(file, { op: "style", value: msg.style || {}, td_id: msg.td_id, source_loc: msg.source_loc, selector: msg.selector }); break;
    case "td:draw": onDraw(msg, file); break;
    case "td:navigate": onNavigate(msg.path, file); break;
    case "td:complete": onComplete(msg, frame); break;
    case "td:eval-result": bus.emit("eval-result", msg); break;
    case "td:pin-click":
      import("./workspace.js").then((m) => { m.setPanel("comments"); setTimeout(() => bus.emit("focus-comment", String(msg.id)), 80); });
      break;
    case "td:key":
      if ((msg.action === "undo" || msg.action === "redo") && info.role === "preview") undoStep(file, msg.action);
      break;
  }
});

// ── Undo / redo on an HTML board = version steps (POST …/versions/undo) ───
// Non-destructive: each step restores the file as it was in an earlier version
// and records that as a new version, so the Versions panel shows every step.
let stepping = false;
export const undoInfo = new Map();   // file -> {can_undo, can_redo}
export async function refreshUndo(file) {
  if (!S.project || !file || !/\.html?$/i.test(file) || features.undo === false) return null;
  const j = await tryApi("GET", `${P_(S.project.id)}/versions/undo?path=${encodeURIComponent(file)}`, undefined, { feature: "undo" }).catch(() => null);
  if (j) { undoInfo.set(file, j); bus.emit("undo-state", { file, ...j }); }
  return j;
}
export async function undoStep(file, direction = "undo") {
  if (!S.project || !file || stepping) return;
  if (features.undo === false) { toast("Undo isn't available on this server yet.", { kind: "error" }); return; }
  stepping = true;
  try {
    const r = await api("POST", `${P_(S.project.id)}/versions/undo`, { path: file, direction }, { feature: "undo" });
    if (!r.ok) { toast(direction === "undo" ? `Nothing earlier to go back to in ${file}` : `Nothing to redo in ${file}`); return; }
    undoInfo.set(file, { can_undo: r.can_undo, can_redo: r.can_redo });
    bus.emit("undo-state", { file, can_undo: r.can_undo, can_redo: r.can_redo });
    const v = r.version?.v;
    toast(`${direction === "undo" ? "Undid" : "Redid"} — ${file} is back to v${r.to_v}${v ? ` (saved as v${v})` : ""}`, {
      action: direction === "undo" && r.can_redo !== false ? { label: "Redo", run: () => undoStep(file, "redo") } : r.can_undo ? { label: "Undo", run: () => undoStep(file, "undo") } : null });
    bus.emit("reload-previews", [file]);
  } catch (e) { toastError(e, direction === "undo" ? "Undo failed" : "Redo failed"); }
  finally { stepping = false; }
}
bus.on("ev:files", (d) => { if (S.activeFile && (!d?.changed?.length || d.changed.includes(S.activeFile))) setTimeout(() => refreshUndo(S.activeFile), 300); });

// ── Tweaks ───────────────────────────────────────────────────────────────
function setTweaks(file, patch) {
  live.tweaks.set(file, { ...(live.tweaks.get(file) || { available: false, on: false }), ...patch });
  bus.emit("tweaks", file);
}
export function toggleTweaks(file, on) {
  setTweaks(file, { on });
  for (const f of framesFor(file)) post(f, { type: on ? "__activate_edit_mode" : "__deactivate_edit_mode" });
}
const pendingTweaks = new Map();
const flushTweaks = debounce(async () => {
  for (const [file, edits] of pendingTweaks) {
    pendingTweaks.delete(file);
    const r = await tryApi("POST", P_(S.project.id) + "/tweaks", { file, edits }, { feature: "tweaks" }).catch((e) => { toastError(e, "Couldn't save the tweak"); return undefined; });
    if (r === null) { toast("Tweak changes can't be saved on this server yet; they apply until reload.", { kind: "error" }); return; }
    if (r) { bus.emit("tweak-saved", { file, edits, version: r.version }); }
  }
}, 600);
export function queueTweak(file, edits) {
  pendingTweaks.set(file, { ...(pendingTweaks.get(file) || {}), ...edits });
  flushTweaks();
}

// ── Comments ─────────────────────────────────────────────────────────────
let pop = null;
function closePop() { if (pop) { pop.remove(); pop = null; } }
function frameScale(frame) { const r = frame.getBoundingClientRect(); return [r, frame.clientWidth ? r.width / frame.clientWidth : 1]; }

function commentPopover(msg, frame, file) {
  closePop();
  const [r, sc] = frameScale(frame);
  const cx = msg.click?.x ?? (msg.bbox ? msg.bbox.x + msg.bbox.w : 20), cy = msg.click?.y ?? (msg.bbox ? msg.bbox.y : 20);
  let x = r.left + cx * sc + 12, y = r.top + cy * sc - 10;
  const ta = h("textarea", { class: "input", rows: 3, placeholder: "Leave a comment for the agent…", autofocus: true });
  const author = prefs.get("author", "You");
  const target = msg.td_id ? `#${msg.td_id}` : msg.selector || "element";
  const submit = async (send) => {
    const note = ta.value.trim();
    if (!note) { ta.focus(); return; }
    const c = await createComment(file, msg, note, author);
    if (!c) return;
    closePop();
    if (send) await sendComments([c.id]);
    else toast("Comment added", { kind: "success", action: { label: "Send to agent", run: () => sendComments([c.id]) } });
  };
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(true); }
    else if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(false); }
    if (e.key === "Escape") closePop();
  });
  pop = h("div", { class: "popover", role: "dialog", "aria-label": "New comment" },
    h("div", { class: "row" }, h("span", { class: "pinno", style: { width: "18px", height: "18px", borderRadius: "50% 50% 50% 3px", background: "var(--pin)", display: "inline-block" } }),
      h("b", { style: { fontSize: "12px" } }, author), h("span", { class: "target grow" }, target), btn("", { kind: "quiet", icon: "x", cls: "sm", title: "Cancel", onClick: closePop })),
    msg.text ? h("div", { class: "faint", style: { fontSize: "11.5px" } }, `"${String(msg.text).slice(0, 80)}"`) : null,
    ta,
    h("div", { class: "row" }, h("span", { class: "faint grow", style: { fontSize: "11px" } }, h("kbd", null, "↵"), " save  ", h("kbd", null, "Ctrl ↵"), " send"),
      btn("Save", { cls: "sm", onClick: () => submit(false) }), btn("Send to agent", { kind: "primary", cls: "sm", onClick: () => submit(true) })));
  document.body.appendChild(pop);
  x = Math.min(x, innerWidth - pop.offsetWidth - 10); y = Math.max(10, Math.min(y, innerHeight - pop.offsetHeight - 10));
  pop.style.left = x + "px"; pop.style.top = y + "px";
  setTimeout(() => ta.focus(), 20);
  const off = (e) => { if (pop && !pop.contains(e.target)) { closePop(); document.removeEventListener("mousedown", off, true); } };
  setTimeout(() => document.addEventListener("mousedown", off, true), 50);
}
addEventListener("keydown", (e) => { if (e.key === "Escape") closePop(); });

export async function createComment(file, sel, note, author = prefs.get("author", "You")) {
  const slide = live.slides.get(file);
  const body = {
    board_id: file, file,
    anchor: { td_id: sel.td_id || null, selector: sel.selector || null, source_loc: sel.source_loc || null, node_id: sel.node_id || null, bbox: sel.bbox || null },
    mentioned_element: sel.mentioned_element || null,
    author, note, status: "open", slide_index: slide ? slide.index : null,
  };
  try {
    const r = await api("POST", P_(S.project.id) + "/comments", body, { feature: "comments" });
    const c = r.comment || r;
    await loadComments();
    return c;
  } catch (e) { toastError(e, "Couldn't save the comment"); return null; }
}
export async function sendComments(ids) {
  if (!ids.length) return;
  const { sendToChat, ensureChat } = await import("./chat.js");
  let chatId = S.chatId;
  if (!chatId) chatId = await ensureChat();
  const r = chatId ? await tryApi("POST", P_(S.project.id) + "/comments/send", { ids, chat_id: chatId }, { feature: "commentSend" }).catch((e) => { toastError(e); return undefined; }) : null;
  if (r === undefined) return;
  if (r === null) {
    // No batch route: fall back to a normal turn that carries the comment ids.
    await sendToChat(ids.length === 1 ? "Address this comment." : `Address these ${ids.length} comments.`, { comment_ids: ids });
  } else {
    bus.emit("ev:turn", r.turn ? { ...r.turn, chat_id: r.turn.chat_id || chatId } : null);
  }
  await loadComments();
  toast(ids.length === 1 ? "Sent to the agent" : `Sent ${ids.length} comments to the agent`, { kind: "success" });
  import("./workspace.js").then((m) => m.setPanel("chat"));
}
function sendPins(file, frame) {
  // td:set-pins (bridge extension): the bridge draws a pin per open comment.
  const pins = S.comments.filter((c) => (c.file || c.board_id) === file && c.status !== "resolved").map((c, i) => ({ id: c.id, n: i + 1, td_id: c.anchor?.td_id, selector: c.anchor?.selector, status: c.status }));
  post(frame, { type: "td:set-pins", pins });
}
bus.on("comments", () => { for (const f of S.tabs) for (const fr of framesFor(f)) sendPins(f, fr); });

// ── Deterministic write-back, falling back to the agent ──────────────────
export async function writeBack(file, edit) {
  const body = { file, op: edit.op, value: edit.value, td_id: edit.td_id || undefined, source_loc: edit.source_loc || undefined };
  const r = await tryApi("POST", P_(S.project.id) + "/edits", body, { feature: "edits" }).catch((e) => { toastError(e); return undefined; });
  if (r === undefined) return false;
  if (r && r.ok) { toast(edit.op === "text" ? "Text updated in the source" : "Style saved to the source", { kind: "success" }); return true; }
  // Ambiguous element or no route: ask the agent through a comment.
  const desc = edit.op === "text" ? `Change the text to: "${edit.value}"` + (edit.old ? ` (was "${String(edit.old).slice(0, 80)}")` : "")
    : `Apply these styles: ${Object.entries(edit.value || {}).map(([k, v]) => `${k}: ${v}`).join("; ")}`;
  const c = await createComment(file, { td_id: edit.td_id, selector: edit.selector, source_loc: edit.source_loc, mentioned_element: S.selection?.mentioned_element }, desc);
  if (c) {
    toast("This one needs the agent — sent as a comment.", { action: { label: "Undo", run: () => api("DELETE", `${P_(S.project.id)}/comments/${c.id}`).then(loadComments) } });
    await sendComments([c.id]);
  }
  return false;
}

// ── Draw ─────────────────────────────────────────────────────────────────
async function onDraw(msg, file) {
  if (typeof msg.png_data_url !== "string" || !msg.png_data_url.startsWith("data:image/png;base64,")) return;
  const bytes = dataUrlBytes(msg.png_data_url);
  const stem = `sketch-${file.replace(/\.html?$/i, "").replace(/[^\w]+/g, "-")}-${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "")}`;
  const { uploadFiles } = await import("./workspace.js");
  const paths = await uploadFiles([new File([bytes], stem + ".png", { type: "image/png" })]);
  // Keep the sketch as a reopenable scrap too: scraps/<name>.napkin + its thumbnail.
  const napkin = await saveNapkin(stem, { board: file, bbox: msg.bbox || null, image: msg.png_data_url, attachment: paths[0] || null }).catch(() => null);
  if (paths.length) {
    bus.emit("attach", paths);
    bus.emit("chat-prefill", `See my sketch on ${file} (attached${napkin ? `, saved as ${napkin}` : ""}) — `);
    import("./workspace.js").then((m) => m.setPanel("chat"));
  }
}
export function dataUrlBytes(url) {
  const bin = atob(String(url).split(",")[1] || "");
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}
// .napkin = {type:"td-napkin", version:1, board, bbox, width, height, image (PNG data URL), attachment?, updated_at}
// plus scraps/.<name>.thumbnail.png. Returns the napkin path.
export async function saveNapkin(stem, data, { path } = {}) {
  const rel = path || `scraps/${stem}.napkin`;
  const i = rel.lastIndexOf("/");
  const dir = i >= 0 ? rel.slice(0, i + 1) : "";
  const name = rel.slice(i + 1).replace(/\.napkin$/, "");
  const rec = { type: "td-napkin", version: 1, ...data, updated_at: new Date().toISOString() };
  const enc = (p) => p.split("/").map(encodeURIComponent).join("/");
  if (typeof data.image === "string" && data.image.startsWith("data:image/png;base64,")) {
    await api("PUT", `${P_(S.project.id)}/files/${enc(`${dir}.${name}.thumbnail.png`)}`, new Blob([dataUrlBytes(data.image)])).catch(() => null);
  }
  await api("PUT", `${P_(S.project.id)}/files/${enc(rel)}`, new Blob([JSON.stringify(rec)]));
  return rel;
}

// ── Navigation inside a preview ──────────────────────────────────────────
function onNavigate(path, from) {
  if (typeof path !== "string" || path.includes("..") || /^[a-z]+:/i.test(path)) return;
  const clean = path.replace(/^\.?\//, "").split(/[?#]/)[0];
  if (!clean) return;
  const i = S.tabs.indexOf(from);
  if (i >= 0 && !S.tabs.includes(clean)) S.tabs[i] = clean; else if (!S.tabs.includes(clean)) S.tabs.push(clean);
  if (S.activeFile === from) S.activeFile = clean;
  import("./state.js").then((m) => m.writeUrl());
  bus.emit("tabs", { navigated: true, file: clean });
}

// ── window.telecode.complete → local model through the proxy ─────────────
let inflight = 0; const stamps = [];
async function onComplete(msg, frame) {
  const reply = (p) => post(frame, { type: "td:complete-result", id: msg.id, ...p });
  const now = Date.now();
  while (stamps.length && now - stamps[0] > 60000) stamps.shift();
  if (inflight >= 2 || stamps.length >= 30) { reply({ ok: false, error: "Rate limited: at most 30 calls a minute, 2 at a time." }); return; }
  let messages = msg.messages;
  if (typeof messages === "string") messages = [{ role: "user", content: messages }];
  if (messages && !Array.isArray(messages) && Array.isArray(messages.messages)) messages = messages.messages;
  if (!Array.isArray(messages) || !messages.length) { reply({ ok: false, error: "Expected a prompt string or {messages}." }); return; }
  messages = messages.slice(-40).map((m) => ({ role: ["system", "assistant", "user"].includes(m.role) ? m.role : "user", content: typeof m.content === "string" ? m.content.slice(0, 20000) : JSON.stringify(m.content).slice(0, 20000) }));
  inflight++; stamps.push(now);
  try {
    const r = await fetch("/v1/chat/completions", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: S.engines?.local?.model || "default", messages, max_tokens: 1024, stream: false }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error?.message || j.error || `HTTP ${r.status}`);
    const text = j.choices?.[0]?.message?.content ?? "";
    reply({ ok: true, text, value: text });
  } catch (e) { reply({ ok: false, error: String(e.message || e) }); }
  finally { inflight--; }
}

// ── Highlight helpers used by the comments panel ─────────────────────────
export function highlight(file, anchor) {
  for (const f of framesFor(file)) post(f, { type: "td:highlight", td_id: anchor?.td_id || undefined, selector: anchor?.selector || undefined });
}
export function reloadFile(file) {
  for (const f of framesFor(file)) { try { f.src = previewUrl(S.project.id, file, "&r=" + Date.now()); } catch {} }
}
