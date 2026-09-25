// TeleDesign — shared-link viewer (/design/s/{token}). Read-only preview of a
// project's pages; "comment" and "edit" links can also leave comments, which go
// through the token-scoped route (POST /api/design/s/{token}/comments).
import { h, icon, btn, mount, clear, api, toast, toastError, prefs, relTime, emptyState, isHtml, promptDialog } from "./core.js";
import { S } from "./state.js";
import { makePreviewFrame, unregisterFrame, post } from "./bridge.js";
import { applyTheme, toggleTheme, currentTheme } from "./theme.js";
import { bus } from "./core.js";

applyTheme();
const app = document.getElementById("app");
const token = decodeURIComponent((location.pathname.match(/\/design\/s\/([^/?#]+)/) || [])[1] || "");

async function boot() {
  if (!token) return fail("This link is incomplete.");
  let meta;
  try { meta = await api("GET", `/api/design/s/${encodeURIComponent(token)}`); }
  catch (e) { return fail(e.status === 404 ? "This link doesn't exist or was revoked." : e.message); }
  if (meta.preview_origin && /^https?:\/\/[^/]+$/.test(meta.preview_origin)) S.previewOrigin = meta.preview_origin;
  const project = meta.project || {};
  S.project = project;
  document.title = `${project.title || "Shared design"} — TeleDesign`;
  const canComment = meta.role === "comment" || meta.role === "edit";
  const pages = (meta.files || []).filter((f) => isHtml(f.path) && !f.path.startsWith("uploads/") && !f.path.startsWith("_ds/"));
  let active = new URLSearchParams(location.search).get("file");
  if (!pages.some((p) => p.path === active)) active = pages[0]?.path || null;
  let mode = "view", frame = null, comments = meta.comments || [];

  const themeBtn = btn("", { kind: "quiet", icon: currentTheme() === "dark" ? "sun" : "moon", title: "Toggle light / dark", onClick: () => { toggleTheme(); mount(themeBtn, icon(currentTheme() === "dark" ? "sun" : "moon")); } });
  const modeBtn = canComment ? btn("Comment", { icon: "comment", cls: "sm", title: "Click anything to comment", onClick: () => setMode(mode === "comment" ? "view" : "comment") }) : null;
  const tabs = h("div", { class: "tabbar", role: "tablist" });
  const stage = h("div", { class: "preview-scroll", style: { display: "block" } });
  const list = h("div", { class: "pane-scroll" });
  const side = canComment ? h("aside", { class: "side", style: { width: "340px" } }, h("div", { class: "pane" }, h("div", { class: "pane-head" }, h("h3", null, "Comments"), h("span", { class: "faint", style: { fontSize: "11.5px" } }, "as ", prefs.get("author", "Guest"))), list)) : null;
  mount(app,
    h("header", { class: "topbar" },
      h("span", { class: "brand" }, h("span", { class: "brand-mark" }, icon("edit")), "TeleDesign"),
      h("span", { class: "crumb-sep" }, "/"), h("b", { style: { fontSize: "13px" } }, project.title || "Shared design"),
      h("span", { class: "pill" }, meta.role === "view" ? "View only" : meta.role === "comment" ? "Can comment" : "Can edit"),
      h("div", { class: "right" }, modeBtn, canComment ? btn("", { kind: "quiet", icon: "edit", title: "Your name on comments", onClick: async () => { const n = await promptDialog("Your name", { value: prefs.get("author", "Guest"), confirm: "Save" }); if (n) { prefs.set("author", n); drawComments(); } } }) : null, themeBtn)),
    h("div", { class: "ws" }, h("section", { class: "stage", style: { flex: 1 } }, tabs, h("div", { class: "stage-body" }, h("div", { class: "preview-area" }, stage))), side));

  function setMode(m) { mode = m; modeBtn?.classList.toggle("on", m === "comment"); if (frame) post(frame, { type: "td:set-mode", mode: m }); if (m === "comment") toast("Click anything in the design to comment on it."); }
  function drawTabs() {
    mount(tabs, pages.map((p) => h("button", { class: "ptab" + (p.path === active ? " on" : ""), onclick: () => { active = p.path; const u = new URL(location.href); u.searchParams.set("file", active); history.replaceState(null, "", u); drawTabs(); drawFrame(); } }, icon("canvas"), p.path)));
  }
  function drawFrame() {
    if (frame) unregisterFrame(frame);
    clear(stage);
    if (!active) { mount(stage, h("div", { style: { display: "grid", placeItems: "center", height: "100%" } }, emptyState("eye", "Nothing to show yet", "This project has no pages."))); return; }
    frame = makePreviewFrame(`${S.previewOrigin}/p/${encodeURIComponent(project.id)}/${active.split("/").map(encodeURIComponent).join("/")}?td_host=${encodeURIComponent(location.origin)}`, { file: active, role: "preview" });
    stage.appendChild(h("div", { class: "device", style: { width: "100%", height: "100%", borderRadius: 0, boxShadow: "none" } }, frame));
  }
  function drawComments() {
    if (!side) return;
    const mine = comments.filter((c) => (c.file || c.board_id) === active);
    if (!mine.length) { mount(list, emptyState("comment", "No comments on this page", "Turn on Comment and click anything to leave one.")); return; }
    mount(list, mine.map((c, i) => h("div", { class: "cmt" + (c.status === "resolved" ? " resolved" : "") }, h("span", { class: "pinno" }, i + 1),
      h("div", { class: "body" }, h("div", { class: "top" }, h("b", null, c.author || "Guest"), h("span", null, relTime(c.created_at))), h("div", { class: "note" }, c.note)))));
  }
  bus.on("preview", async ({ msg, info }) => {
    if (msg.type === "td:ready" && mode !== "view") post(frame, { type: "td:set-mode", mode });
    if (msg.type === "td:navigate" && typeof msg.path === "string") { const p = msg.path.split(/[?#]/)[0]; if (pages.some((x) => x.path === p)) { active = p; drawTabs(); drawComments(); } }
    if (msg.type !== "td:comment-target" || !canComment) return;
    const note = await promptDialog("Comment", { label: msg.td_id ? "#" + msg.td_id : msg.selector || "", multiline: true, confirm: "Add comment" });
    if (!note) return;
    try {
      const r = await api("POST", `/api/design/s/${encodeURIComponent(token)}/comments`, {
        board_id: info.file, file: info.file, note, author: prefs.get("author", "Guest"), status: "open",
        anchor: { td_id: msg.td_id || null, selector: msg.selector || null, source_loc: msg.source_loc || null, node_id: null, bbox: msg.bbox || null },
        mentioned_element: msg.mentioned_element || null,
      });
      comments.push(r.comment || r); drawComments(); toast("Comment added", { kind: "success" });
    } catch (e) { toastError(e, "Couldn't add the comment"); }
  });
  drawTabs(); drawFrame(); drawComments();
}
function fail(msg) {
  mount(app, h("div", { style: { display: "grid", placeItems: "center", height: "100vh" } }, emptyState("link", "Can't open this design", msg)));
}
boot();
