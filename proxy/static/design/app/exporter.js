// TeleDesign — Export menu + job cards, Share dialog, parallel Agents dialog,
// Engines & integrations dialog.
import {
  h, icon, btn, mount, clear, api, tryApi, toast, toastError, modal, menu, bus, P_, prefs, copyText, features, relTime, ApiMissing, emptyState, confirmDialog,
} from "./core.js";
import { S, htmlFiles, loadEngines, previewUrl } from "./state.js";
import { applyTheme } from "./theme.js";

// ── Export ───────────────────────────────────────────────────────────────
const KINDS = [
  { kind: "html", label: "Standalone HTML", icon: "file", hint: "One self-contained file" },
  { kind: "zip", label: "Project ZIP", icon: "archive", hint: "Every source file" },
  { kind: "pdf", label: "PDF", icon: "file", hint: "Decks print one slide per page" },
  { kind: "print", label: "Open for print", icon: "external", hint: "New tab, then Ctrl+P", local: true },
  { kind: "pptx", label: "PowerPoint", icon: "present", hint: "Screenshots or editable text" },
  { kind: "png", label: "Image", icon: "image", hint: "PNG, JPEG or WEBP at 1–3×" },
  { kind: "mp4", label: "Video (MP4)", icon: "play", hint: "For animations" },
  "-",
  { kind: "gslides", label: "Send to Google Slides", icon: "present", hint: "Via the gws CLI, as a new presentation", local: true },
  "-",
  { kind: "handoff", label: "Handoff bundle", icon: "code", hint: "For a coding agent to implement" },
  { kind: "handoff-session", label: "Hand off to a coding session", icon: "repo", hint: "Pick a repo; a Task-mode session starts there", local: true },
  { kind: "handoff-prompt", label: "Copy handoff prompt", icon: "copy", hint: "Paste into Claude Code or Codex", local: true },
];
export function exportMenu(anchor) {
  const cur = S.activeFile && /\.html?$/i.test(S.activeFile) ? S.activeFile : htmlFiles()[0]?.path;
  menu(anchor, [{ heading: cur ? `Export ${cur}` : "Export" }, ...KINDS.map((k) => k === "-" ? "-" : {
    label: k.label, icon: k.icon, hint: k.hint, disabled: features["export_" + k.kind] === false || (!cur && !["zip", "handoff", "handoff-prompt", "handoff-session"].includes(k.kind)),
    onClick: () => exportKind(k.kind, cur),
  })], { align: "right", width: "280px" });
}
function fileSelect(value) {
  const s = h("select", { class: "select" }, htmlFiles().map((f) => h("option", { value: f.path, selected: f.path === value || null }, f.path)));
  return s;
}
const field = (label, ctl, hint) => h("label", { class: "field" }, h("span", { class: "field-label" }, label), ctl, hint ? h("span", { class: "field-hint" }, hint) : null);

async function exportKind(kind, file) {
  const pid = S.project.id;
  if (kind === "print") { window.open(previewUrl(pid, file, "&print=1"), "_blank", "noopener"); toast("Opened in a new tab — press Ctrl+P there to print."); return; }
  if (kind === "handoff-prompt") {
    const r = await tryApi("GET", `${P_(pid)}/export/handoff/prompt`, undefined, { feature: "export_handoff-prompt" }).catch((e) => { toastError(e); return undefined; });
    if (r === null) toast("The handoff prompt isn't available on this server yet.", { kind: "error" });
    else if (r) copyText(r.prompt || "");
    return;
  }
  if (kind === "gslides") return gslidesDialog(file);
  if (kind === "handoff-session") return handoffDialog(file);
  if (kind === "zip" || kind === "handoff") return startExport(kind, { file });
  const fsel = fileSelect(file);
  const opts = {};
  const body = h("div", null, field("Page", fsel));
  if (kind === "pptx") {
    const mode = h("div", { class: "seg" });
    let m = "screenshots";
    const drawM = () => mount(mode, [["screenshots", "Screenshots"], ["editable", "Editable text"]].map(([v, l]) => h("button", { type: "button", class: m === v ? "on" : "", onclick: () => { m = v; drawM(); } }, l)));
    drawM();
    const swaps = h("textarea", { class: "input", rows: 2, placeholder: "Newsreader = Georgia\nInter = Arial" });
    const hide = h("input", { class: "input", placeholder: ".deck-controls, .tweaks" });
    const gfonts = h("input", { class: "input", placeholder: "Inter:wght@400;700, Newsreader" });
    const reset = h("input", { class: "input", placeholder: "#stage, .deck-scale" });
    const saveOn = h("input", { type: "checkbox" });
    const savePath = h("input", { class: "input", value: "exports/" + (S.project.title || "deck").replace(/[^\w-]+/g, "-").toLowerCase().slice(0, 40) + ".pptx", disabled: true, style: { flex: 1 } });
    saveOn.addEventListener("change", () => { savePath.disabled = !saveOn.checked; });
    // Per-slide overrides: [{index, selector, showJs, delay}] — when present they define the slide list.
    const rows = [];
    const slideHost = h("div", { class: "slide-specs" });
    const drawRows = () => {
      mount(slideHost, rows.length ? h("div", { class: "spec-row head" }, h("span", null, "#"), h("span", null, "Selector"), h("span", null, "Run before capture (JS)"), h("span", null, "Delay ms"), h("span")) : null,
        rows.map((r, i) => h("div", { class: "spec-row" },
          h("input", { class: "input", type: "number", min: 1, value: r.index ?? i + 1, "aria-label": "Deck slide", oninput: (e) => { r.index = +e.target.value || null; } }),
          h("input", { class: "input", value: r.selector || "", placeholder: "whole slide", "aria-label": "Selector", oninput: (e) => { r.selector = e.target.value; } }),
          h("input", { class: "input mono", value: r.showJs || "", placeholder: "document.querySelector('#tab2').click()", "aria-label": "showJs", oninput: (e) => { r.showJs = e.target.value; } }),
          h("input", { class: "input", type: "number", min: 0, max: 10000, value: r.delay || 0, "aria-label": "Delay", oninput: (e) => { r.delay = +e.target.value || 0; } }),
          btn("", { kind: "quiet", icon: "x", cls: "sm", title: "Remove", onClick: () => { rows.splice(i, 1); drawRows(); } }))),
        btn(rows.length ? "Add slide" : "Customise slides…", { kind: "ghost", cls: "sm", icon: "plus", onClick: () => { rows.push({ index: rows.length + 1, selector: "", showJs: "", delay: 0 }); drawRows(); } }));
    };
    drawRows();
    const adv = h("details", { class: "adv" }, h("summary", null, "More options"),
      field("Load Google Fonts", gfonts, "Comma-separated families (or fonts.googleapis.com URLs) the page uses but doesn't import."),
      field("Reset transform on", reset, "A scaled stage or wrapper to un-scale before capture."),
      field("Per-slide capture", slideHost, "Each row is one slide: go to that deck slide, run the JS, wait, then capture the selector's box."),
      h("label", { class: "check" }, saveOn, "Also save the .pptx into the project"), h("div", { class: "row" }, savePath));
    body.append(field("Mode", mode, "Screenshots look exact. Editable keeps text as real PowerPoint text boxes."), field("Font swaps", swaps, "One per line, for fonts PowerPoint won't have."), field("Hide before capture", hide, "CSS selectors"), adv);
    opts.get = () => {
      const o = { mode: m, fontSwaps: Object.fromEntries(swaps.value.split("\n").map((l) => l.split("=").map((x) => x.trim())).filter((p) => p[0] && p[1])), hideSelectors: hide.value.split(",").map((x) => x.trim()).filter(Boolean) };
      const gf = gfonts.value.split(",").map((x) => x.trim()).filter(Boolean);
      if (gf.length) o.googleFontImports = gf;
      if (reset.value.trim()) o.resetTransformSelector = reset.value.trim();
      if (rows.length) o.slides = rows.map((r, i) => ({ index: r.index || i + 1, selector: (r.selector || "").trim() || undefined, showJs: (r.showJs || "").trim() || undefined, delay: r.delay || undefined }));
      if (saveOn.checked && savePath.value.trim()) o.save_to_project_path = savePath.value.trim();
      return o;
    };
  } else if (kind === "png") {
    let scale = 2, fmt = "png";
    const sc = h("div", { class: "seg" }); const drawS = () => mount(sc, [1, 2, 3].map((v) => h("button", { type: "button", class: scale === v ? "on" : "", onclick: () => { scale = v; drawS(); } }, v + "×"))); drawS();
    const fm = h("div", { class: "seg" }); const drawF = () => mount(fm, ["png", "jpeg", "webp"].map((v) => h("button", { type: "button", class: fmt === v ? "on" : "", onclick: () => { fmt = v; drawF(); q.parentNode.classList.toggle("hidden", fmt === "png"); } }, v.toUpperCase())));
    const q = h("input", { type: "range", min: 40, max: 100, value: 90, style: { accentColor: "var(--accent)" } });
    const full = h("input", { type: "checkbox" });
    drawF();
    body.append(h("div", { class: "row", style: { gap: "18px" } }, field("Scale", sc), field("Format", fm)), h("div", { class: "field hidden" }, h("span", { class: "field-label" }, "Quality"), q),
      h("label", { class: "check" }, full, "Capture the full page height"));
    opts.get = () => ({ scale, format: fmt, quality: +q.value, full_page: full.checked });
  } else if (kind === "mp4") {
    const dur = h("input", { class: "input", type: "number", value: 10, min: 1, max: 300, style: { width: "100px" } });
    const fps = h("select", { class: "select", style: { width: "100px" } }, [24, 30, 60].map((v) => h("option", { value: v, selected: v === 30 || null }, v + " fps")));
    body.append(h("div", { class: "row", style: { gap: "18px" } }, field("Length (seconds)", dur), field("Frame rate", fps)),
      h("p", { class: "faint", style: { fontSize: "11.5px", margin: 0 } }, "The page needs a timeline (window.tdTimeline) — the animation starter provides one."));
    opts.get = () => ({ duration: +dur.value, fps: +fps.value });
  } else if (kind === "pdf") {
    const ls = h("input", { type: "checkbox", checked: true });
    body.append(h("label", { class: "check" }, ls, "Landscape (decks and wide boards)"));
    opts.get = () => ({ landscape: ls.checked });
  } else opts.get = () => ({});
  const title = KINDS.find((k) => k.kind === kind)?.label || "Export";
  modal({ title: `Export — ${title}`, width: "460px", body, actions: [{ label: "Cancel", kind: "ghost" }, { label: "Export", kind: "primary", icon: "download", onClick: () => startExport(kind, { file: fsel.value, options: opts.get() }) }] });
}

let jobsHost = null;
function jobsEl() { if (!jobsHost || !jobsHost.isConnected) { jobsHost = h("div", { class: "jobs", "aria-live": "polite" }); document.body.appendChild(jobsHost); } return jobsHost; }

async function startExport(kind, { file, options = {}, board_ids } = {}) {
  const pid = S.project.id;
  let r;
  try { r = await api("POST", `${P_(pid)}/export/${kind}`, { file, board_ids, options }, { feature: "export_" + kind }); }
  catch (e) {
    if (e instanceof ApiMissing) { toast("That export isn't available on this server yet.", { kind: "error" }); return; }
    toastError(e, "Export failed"); return;
  }
  const label = KINDS.find((k) => k.kind === kind)?.label || kind;
  // The result lands in the chat as a download card; the floating card is for
  // when the chat isn't on screen.
  bus.emit("export-started", { job_id: r.job_id, kind, file });
  const chatVisible = matchMedia("(max-width: 900px)").matches ? S.mtab === "chat" && S.panel === "chat"
    : S.panel === "chat" && !document.querySelector(".ws .side.collapsed");
  if (!chatVisible) jobCard({ title: label, sub: file || S.project.title, poll: () => api("GET", `${P_(pid)}/export/jobs/${encodeURIComponent(r.job_id)}`),
    download: (j) => j.download_url || `${P_(pid)}/export/download/${encodeURIComponent(r.job_id)}` });
  else toast(`${label} export started — it appears in the chat when ready.`);
}

// Generic progress card: poll() → {status, progress, error, flags}
export function jobCard({ title, sub, poll, download, stop, onDone }) {
  const bar = h("i", { style: { width: "3%" } });
  const status = h("span", { class: "faint", style: { fontSize: "11.5px" } }, "Starting…");
  const actions = h("div", { class: "row" });
  const flags = h("div", { class: "flags" });
  const card = h("div", { class: "job" }, h("div", { class: "top" }, icon("download"), h("b", null, title),
    btn("", { kind: "quiet", icon: "x", cls: "sm", title: "Dismiss", onClick: () => { stopped = true; card.remove(); } })),
    h("div", { class: "faint ellipsis", style: { fontSize: "11.5px" } }, sub), h("div", { class: "bar" }, bar), status, flags, actions);
  if (stop) actions.appendChild(btn("Stop", { cls: "sm", onClick: () => stop() }));
  jobsEl().appendChild(card);
  let stopped = false, tries = 0;
  const FLAG_TEXT = { duplicate_adjacent: "Two adjacent slides look identical.", slide_size_mismatch: "Some slides aren't the deck's size.", no_speaker_notes: "No speaker notes were found." };
  const tick = async () => {
    if (stopped || !card.isConnected) return;
    let j;
    try { j = await poll(); tries = 0; } catch (e) { if (++tries > 5) { status.textContent = "Lost track of this job: " + e.message; return; } setTimeout(tick, 2000); return; }
    const p = Math.max(3, Math.min(100, Math.round((j.progress <= 1 ? j.progress * 100 : j.progress) || 0)));
    bar.style.width = p + "%";
    const st = j.status || "running";
    status.textContent = st === "done" || st === "completed" ? "Ready" : st === "failed" ? "Failed: " + (j.error || "unknown error") : st === "cancelled" ? "Stopped" : (j.message || "Working…") + (p > 3 ? ` ${p}%` : "");
    mount(flags, (j.flags || []).map((f) => h("div", null, "⚠ ", typeof f === "string" ? FLAG_TEXT[f] || f : f.message || JSON.stringify(f))));
    // Actions marked data-keep (links a caller added) survive the finish.
    const clearActs = () => { for (const c of [...actions.children]) if (!c.dataset.keep) c.remove(); };
    if (st === "done" || st === "completed") {
      bar.style.width = "100%";
      clearActs();
      if (download) { const url = download(j); if (url && (url.startsWith("/") || url.startsWith(location.origin))) actions.appendChild(h("a", { class: "btn primary sm", href: url, download: "" }, icon("download"), "Download")); }
      if (onDone) onDone(j, actions);
      return;
    }
    if (st === "failed" || st === "cancelled") { bar.style.background = "var(--err)"; clearActs(); return; }
    setTimeout(tick, 1000);
  };
  setTimeout(tick, 400);
  return card;
}

// ── Share ────────────────────────────────────────────────────────────────
const EXPIRY = [["", "Never expires"], ["3600", "Expires in 1 hour"], ["86400", "Expires in 1 day"], ["604800", "Expires in 7 days"], ["2592000", "Expires in 30 days"]];
function untilText(iso) {
  const t = Date.parse(iso || "");
  if (isNaN(t)) return "";
  const s = Math.round((t - Date.now()) / 1000);
  if (s <= 0) return "Expired";
  if (s < 3600) return `Expires in ${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86400) return `Expires in ${Math.round(s / 3600)} h`;
  return `Expires in ${Math.round(s / 86400)} d`;
}
export async function shareDialog() {
  const pid = S.project.id;
  const listHost = h("div", null, h("div", { class: "sk sk-line" }), h("div", { class: "sk sk-line" }));
  const warnHost = h("div");
  let role = "view";
  const roleSeg = h("div", { class: "seg" });
  const drawRole = () => mount(roleSeg, [["view", "Can view"], ["comment", "Can comment"], ["edit", "Can edit"]].map(([v, l]) => h("button", { type: "button", class: role === v ? "on" : "", onclick: () => { role = v; drawRole(); } }, l)));
  drawRole();
  const expSel = h("select", { class: "select", "aria-label": "Link expiry", style: { width: "auto" } }, EXPIRY.map(([v, l]) => h("option", { value: v }, l)));
  const createRow = h("div", { class: "row wrap share-create" }, roleSeg, expSel, h("span", { class: "grow" }), btn("Create link", { kind: "primary", icon: "link", onClick: () => create() }));
  const m = modal({ title: "Share", subtitle: "A link to a snapshot of the project as it is now — later edits don't show until you update the link. Anyone who can reach this machine can open it.", width: "600px",
    body: h("div", null, createRow, warnHost, listHost) });
  const linkOf = (t) => `${location.origin}/design/s/${encodeURIComponent(t)}`;
  const off = (msg) => { mount(listHost, emptyState("share", "Sharing is turned off", msg)); createRow.classList.add("hidden"); };
  function warnDeps(deps) {
    if (!deps || !deps.length) { clear(warnHost); return; }
    mount(warnHost, h("div", { class: "err-box warn", style: { marginBottom: "12px" } },
      h("div", { class: "row" }, icon("alert"), h("b", { class: "grow" }, `${deps.length} missing file${deps.length > 1 ? "s" : ""} in the snapshot`)),
      h("div", { class: "faint", style: { fontSize: "11.5px", margin: "4px 0" } }, "These pages reference files the project doesn't have, so they will be broken for whoever opens the link:"),
      h("ul", { style: { margin: "2px 0 0", paddingLeft: "18px", fontSize: "12px" } }, deps.slice(0, 8).map((d) => h("li", null, h("span", { class: "mono" }, d.missing), h("span", { class: "faint" }, ` — from ${d.file}`))))));
  }
  async function load() {
    let r;
    try { r = await api("GET", `${P_(pid)}/share`, undefined, { feature: "share" }); }
    catch (e) {
      if (e instanceof ApiMissing) { mount(listHost, emptyState("share", "Sharing isn't available yet", "This server doesn't have share links.")); createRow.classList.add("hidden"); return; }
      off(e.status === 403 || e.status === 400 ? "Set design.share.enabled to true in settings.json, then reload." : e.message); return;
    }
    if (r.enabled === false) { off("Set design.share.enabled to true in settings.json, then reload."); return; }
    const tokens = r.shares || r.tokens || [];
    if (!tokens.length) { mount(listHost, h("p", { class: "muted", style: { margin: 0, fontSize: "12.5px" } }, "No links yet. Create one to share a snapshot.")); return; }
    mount(listHost, tokens.map(linkRow));
  }
  function linkRow(t) {
    const tok = encodeURIComponent(t.token);
    const n = t.changed_count || 0;
    const ch = t.changes || {};
    const detail = [...(ch.modified || []).map((p) => "changed  " + p), ...(ch.added || []).map((p) => "added    " + p), ...(ch.removed || []).map((p) => "removed  " + p)].join("\n");
    const status = t.expired ? h("span", { class: "pill err" }, "Expired")
      : t.live ? h("span", { class: "pill", title: "Made before snapshots: shows the live project" }, "Live")
      : n ? h("span", { class: "pill warn", title: detail }, `${n} change${n > 1 ? "s" : ""} since shared`)
      : h("span", { class: "pill ok" }, "Up to date");
    const updateBtn = !t.live && !t.expired ? btn(n ? "Update snapshot" : "Re-share", { cls: "sm", icon: "refresh", kind: n ? "primary" : "ghost", title: "Replace what the link shows with the project as it is now (same URL)", onClick: async () => {
      try { const r = await api("PATCH", `${P_(pid)}/share/${tok}`, { resnapshot: true }); warnDeps(r.missing_dependencies); toast("Link updated to the current files", { kind: "success" }); load(); }
      catch (e) { toastError(e, "Couldn't update the link"); }
    } }) : null;
    return h("div", { class: "share-item" },
      h("div", { class: "share-link" }, h("span", { class: "pill" }, t.role || "view"), h("span", { class: "u", title: linkOf(t.token) }, linkOf(t.token)),
        btn("", { kind: "quiet", icon: "copy", cls: "sm", title: "Copy link", onClick: () => copyText(linkOf(t.token)) }),
        btn("", { kind: "quiet", icon: "more", cls: "sm", title: "More", onClick: (e) => menu(e.currentTarget, [
          { heading: "Role" },
          ...["view", "comment", "edit"].map((rv) => ({ label: rv === "view" ? "Can view" : rv === "comment" ? "Can comment" : "Can edit", checked: t.role === rv, icon: t.role === rv ? "check" : null, onClick: () => patch({ role: rv }) })),
          { heading: "Expiry" },
          ...EXPIRY.map(([v, l]) => ({ label: l, onClick: () => patch({ expires_in: v ? +v : null }) })),
          "-",
          { label: "Download what the recipient sees (ZIP)", icon: "download", onClick: () => { const a = h("a", { href: `/api/design/s/${tok}/download`, download: "" }); document.body.appendChild(a); a.click(); a.remove(); } },
          { label: "Check for missing files", icon: "checkCircle", onClick: async () => { const r = await api("GET", `${P_(pid)}/share/${tok}/check`).catch((err) => { toastError(err); return null; }); if (r) { warnDeps(r.missing_dependencies); if (!r.missing_dependencies?.length) toast("Every referenced file is in the snapshot", { kind: "success" }); } } },
          "-",
          { label: "Revoke link", icon: "trash", danger: true, onClick: async () => { await api("DELETE", `${P_(pid)}/share/${tok}`).catch(toastError); toast("Link revoked", { kind: "success" }); load(); } },
        ], { align: "right", width: "260px" }) })),
      h("div", { class: "share-meta" }, status,
        h("span", { class: "faint" }, t.snapshot ? `Snapshot ${relTime(t.snapshot.at)} · ${t.snapshot.file_count} files` : `Created ${relTime(t.created_at)}`),
        t.expires_at ? h("span", { class: "faint" }, untilText(t.expires_at)) : null,
        h("span", { class: "grow" }), updateBtn));
    async function patch(body) {
      try { await api("PATCH", `${P_(pid)}/share/${tok}`, body); load(); } catch (e) { toastError(e); }
    }
  }
  async function create() {
    try {
      const body = { role };
      if (expSel.value) body.expires_in = +expSel.value;
      const r = await api("POST", `${P_(pid)}/share`, body, { feature: "share" });
      const t = r.share?.token || r.token;
      warnDeps(r.missing_dependencies);
      if (t) copyText(r.url ? location.origin + r.url : linkOf(t));
      load();
    } catch (e) { toastError(e, e.status === 403 ? "Sharing is turned off (design.share.enabled)" : "Couldn't create the link"); }
  }
  load();
  return m;
}

// ── Parallel agents ──────────────────────────────────────────────────────
const ENG = { claude_code: "Claude Code", codex: "Codex", antigravity: "Antigravity" };
const MODE_LABEL = { split: "Split work", side_by_side: "Side by side", let_it_cook: "Let it cook", jury: "Jury" };
export const ACTIVE = (st) => ["starting", "queued", "running", "reviewing", "critiquing", "revising", "generating"].includes(st);
export const runs = { list: [], loaded: false };
export async function loadRuns() {
  if (!S.project) return [];
  const j = await tryApi("GET", `${P_(S.project.id)}/agents`, undefined, { feature: "agents" }).catch(() => null);
  runs.list = (j && j.runs) || [];
  runs.loaded = true;
  bus.emit("runs", runs.list);
  return runs.list;
}
bus.on("ev:agents", (d) => {
  const run = d && (d.run || (d.run_id ? { id: d.run_id, status: d.status } : null));
  if (!run || !run.id) return;
  const i = runs.list.findIndex((r) => r.id === run.id);
  if (i >= 0) runs.list[i] = { ...runs.list[i], ...run }; else runs.list.unshift(run);
  bus.emit("runs", runs.list);
  if (!d.run) loadRuns();
});

// Runs view: every parallel run in this project, per-agent status/usage, jury rounds.
export async function runsDialog() {
  const body = h("div", null, h("div", { class: "sk sk-block" }), h("div", { class: "sk sk-block" }));
  const m = modal({ title: "Parallel agents", subtitle: "Runs in this project. Each agent works in its own chat.", width: "780px", body,
    actions: [{ label: "Close", kind: "ghost" }, { label: "New run", kind: "primary", icon: "plus", onClick: () => { setTimeout(agentsDialog, 0); } }] });
  const draw = () => {
    if (!m.el.isConnected) { off(); return; }
    if (!runs.list.length) { mount(body, emptyState("agents", "No runs yet", "Start a run to have several agents work on the brief at once.")); return; }
    mount(body, runs.list.map((r) => runCard(r, m)));
  };
  const off = bus.on("runs", draw);
  await loadRuns(); draw();
}
function runCard(run, m) {
  const active = ACTIVE(run.status);
  const u = run.usage || {};
  const agents = (run.agents || []).concat(run.critics || []);
  const openChat = async (cid) => { m.close(); const ws = await import("./workspace.js"); ws.setPanel("chat"); const c = await import("./chat.js"); await c.loadChats(true); c.switchChat(cid); };
  const tok = (x) => x ? ((+x.input || 0) + (+x.output || 0)).toLocaleString() : "—";
  return h("div", { class: "spec-card", style: { padding: "12px 14px", marginBottom: "12px" } },
    h("div", { class: "row" }, h("b", null, MODE_LABEL[run.mode] || run.mode), h("span", { class: "pill " + (active ? "accent" : run.status === "failed" ? "err" : run.status === "cancelled" ? "" : "ok") }, active ? h("span", { class: "dot accent pulse" }) : null, String(run.status || "").replace("_", " ")),
      h("span", { class: "faint", style: { fontSize: "11.5px" } }, relTime(run.created_at)), h("span", { class: "grow" }),
      h("span", { class: "faint mono" }, tok(u) + " tok" + (u.cost_usd ? " · $" + (+u.cost_usd).toFixed(2) : "")),
      active ? btn("Stop all", { cls: "sm", icon: "stop", onClick: () => api("POST", `${P_(S.project.id)}/agents/${encodeURIComponent(run.id)}/stop`, {}).then(() => { toast("Stopping. Completed changes stay."); loadRuns(); }).catch(toastError) }) : null),
    run.prompt ? h("div", { class: "muted ellipsis", style: { fontSize: "12px", margin: "4px 0 8px" }, title: run.prompt }, run.prompt) : null,
    h("table", { class: "tok-table" }, h("tr", null, h("th", null, "Agent"), h("th", null, "Engine"), h("th", null, "Status"), h("th", null, "Files"), h("th", null, "Tokens"), h("th", null, "")),
      agents.map((a, i) => h("tr", null, h("td", null, a.role ? `Critic: ${a.role}` : `Agent ${(a.index ?? i) + 1}`), h("td", { class: "mono" }, ENG[a.engine] || a.engine || ""),
        h("td", null, h("span", { class: "pill " + (ACTIVE(a.status) ? "accent" : a.status === "failed" ? "err" : "ok") }, a.status)),
        h("td", { class: "mono" }, (a.changed_files || []).length || "—"), h("td", { class: "mono" }, tok(a.usage)),
        h("td", null, a.chat_id ? btn("Open chat", { kind: "quiet", cls: "sm", onClick: () => openChat(a.chat_id) }) : null)))),
    (run.rounds || []).length ? h("div", { style: { marginTop: "10px" } }, h("div", { style: { fontWeight: 600, fontSize: "12px", marginBottom: "4px" } }, "Jury rounds"),
      run.rounds.map((r) => h("div", { class: "row", style: { fontSize: "12px", padding: "3px 0" } }, h("span", { class: "mono" }, "Round " + r.round),
        h("span", { class: "meter-bar", style: { width: "120px" } }, h("i", { style: { width: Math.min(100, (+r.mean || 0) * 10) + "%", background: r.passed ? "var(--ok)" : "var(--warn)" } })),
        h("b", { class: "mono" }, (+r.mean || 0).toFixed(1)), r.passed ? h("span", { class: "pill ok" }, "passed") : null,
        h("span", { class: "faint ellipsis grow" }, (r.critiques || []).map((c) => `${c.role}: ${c.overall}`).join(", "))))) : null,
    run.result && run.result.final_mean != null ? h("div", { class: "faint", style: { fontSize: "12px", marginTop: "6px" } }, `Final score ${run.result.final_mean} after ${run.result.rounds} round(s)`) : null,
    run.error ? h("div", { class: "err-box", style: { marginTop: "8px" } }, String(run.error)) : null);
}
export async function agentsDialog() {
  if (!S.engines) await loadEngines();
  const max = +(S.engines?.max_parallel_agents || 6);
  let mode = "side_by_side", count = 3, variant = "layout", iterate = true;
  const modes = [
    ["split", "Split work", "Divide one brief across agents — each takes a part.", "split"],
    ["side_by_side", "Side by side", "Same brief, independent takes to compare.", "canvas"],
    ["let_it_cook", "Let it cook", "Variants along one axis, iterated.", "sparkle"],
    ["jury", "Jury", "Generate, critique in parallel, revise to a score of 8.", "checkCircle"],
  ];
  const modeGrid = h("div", { class: "agent-modes" });
  const engHost = h("div", { class: "col" });
  const extra = h("div");
  const cnt = h("output", { class: "mono" });
  const range = h("input", { type: "range", min: 1, max, value: count, style: { flex: 1, accentColor: "var(--accent)" } });
  range.addEventListener("input", () => { count = +range.value; cnt.textContent = count; drawEngines(); });
  const engines = (S.engines?.engines || [{ id: "claude_code", available: true }]).filter((e) => e.available !== false);
  const perAgent = [];
  function drawModes() {
    mount(modeGrid, modes.map(([v, l, d, ic]) => h("button", { type: "button", class: "kind-card" + (mode === v ? " on" : ""), onclick: () => { mode = v; drawModes(); drawExtra(); } },
      h("div", { class: "row" }, icon(ic), h("span", null, l)), h("small", { class: "faint", style: { fontSize: "11px", lineHeight: 1.35 } }, d))));
  }
  function drawEngines() {
    cnt.textContent = count;
    while (perAgent.length < count) perAgent.push(engines[0]?.id || "claude_code");
    mount(engHost, Array.from({ length: count }, (_, i) => {
      const s = h("select", { class: "select", style: { width: "180px", height: "26px" } }, engines.map((e) => h("option", { value: e.id, selected: perAgent[i] === e.id || null }, ENG[e.id] || e.id)));
      s.addEventListener("change", () => (perAgent[i] = s.value));
      return h("div", { class: "row" }, h("span", { class: "muted", style: { width: "64px", fontSize: "12px" } }, `Agent ${i + 1}`), s);
    }));
  }
  function drawExtra() {
    clear(extra);
    if (mode === "let_it_cook") {
      const seg = h("div", { class: "seg" });
      const d = () => mount(seg, [["layout", "Layout"], ["style", "Style"]].map(([v, l]) => h("button", { type: "button", class: variant === v ? "on" : "", onclick: () => { variant = v; d(); } }, l)));
      d();
      extra.append(field("Vary the", seg), h("label", { class: "check" }, h("input", { type: "checkbox", checked: !iterate, onchange: (e) => (iterate = !e.target.checked) }), "Don't iterate — one pass each"));
    }
  }
  const prompt = h("textarea", { class: "input", rows: 3, placeholder: "What should the agents make?" });
  drawModes(); drawEngines(); drawExtra();
  modal({ title: "Run agents in parallel", subtitle: "Each agent gets its own chat and writes its own boards.", width: "640px",
    body: h("div", null, h("div", { class: "field" }, h("span", { class: "field-label" }, "Mode"), modeGrid),
      h("div", { class: "field" }, h("span", { class: "field-label" }, "Agents"), h("div", { class: "row" }, range, cnt)),
      h("div", { class: "field" }, h("span", { class: "field-label" }, "Engine per agent"), engHost), extra, field("Brief", prompt)),
    actions: [{ label: "Cancel", kind: "ghost" }, { label: "Start agents", kind: "primary", icon: "agents", onClick: async () => {
      if (!prompt.value.trim()) { toast("Write a brief first", { kind: "error" }); return false; }
      const body = { mode, count, engines: perAgent.slice(0, count), prompt: prompt.value.trim(), iterate };
      if (mode === "let_it_cook") body.variant = variant;
      let r;
      try { r = await api("POST", `${P_(S.project.id)}/agents`, body, { feature: "agents" }); }
      catch (e) { if (e instanceof ApiMissing) toast("Parallel agents aren't available on this server yet.", { kind: "error" }); else toastError(e); return; }
      toast(`Started ${count} agent${count > 1 ? "s" : ""}`, { kind: "success" });
      const { loadChats } = await import("./chat.js"); await loadChats(true);
      await loadRuns();
      setTimeout(runsDialog, 0);
      return;
      jobCard({ title: `${modes.find((m) => m[0] === mode)[1]} · ${count} agents`, sub: body.prompt,
        poll: async () => { const j = await api("GET", `${P_(S.project.id)}/agents/${encodeURIComponent(r.run_id)}`); const run = j.run || j; return { status: run.status, progress: run.progress, message: run.round ? `Round ${run.round}` : run.message, error: run.error }; },
        stop: () => api("POST", `${P_(S.project.id)}/agents/${encodeURIComponent(r.run_id)}/stop`, {}).then(() => toast("Stopping all agents. Completed changes stay.")).catch(toastError) });
    } }] });
}

// ── Preferences (per browser) ────────────────────────────────────────────
// Engine defaults, models, local helpers and MCP registration live in
// telecode's Settings window → TeleDesign (the one writer of settings.json).
// Each chat still picks its own engine / model / local / effort in the composer.
export async function settingsDialog() {
  const author = h("input", { class: "input", value: prefs.get("author", "You"), style: { width: "200px" } });
  author.addEventListener("change", () => prefs.set("author", author.value.trim() || "You"));
  const theme = h("select", { class: "select", style: { width: "160px" } }, [["system", "Match system"], ["dark", "Dark"], ["light", "Light"]].map(([v, l]) => h("option", { value: v, selected: prefs.get("theme", "system") === v || null }, l)));
  theme.addEventListener("change", () => { prefs.set("theme", theme.value); applyTheme(); });
  const autoCtx = h("input", { type: "checkbox", checked: prefs.get("autoContext", true) !== false });
  autoCtx.addEventListener("change", () => prefs.set("autoContext", autoCtx.checked));
  modal({ title: "Preferences", width: "460px",
    body: h("div", null,
      h("div", { class: "row", style: { gap: "18px" } }, field("Name on comments", author), field("Theme", theme)),
      h("label", { class: "check", style: { marginTop: "10px" } }, autoCtx, "Add what I select (in the preview or on the canvas) to my next chat message"),
      h("p", { class: "faint", style: { fontSize: "12px", marginTop: "16px", marginBottom: 0 } },
        "Default engine, cloud or local, default models and MCP registration are in telecode's Settings window → TeleDesign (tray icon → Open Settings Window).")) });
}

// ── Send to Google Slides (gws CLI) ──────────────────────────────────────
// The server checks `gws auth status` first and never signs in for you; when
// the CLI is missing or signed out this shows what to run in a terminal.
export async function gslidesDialog(file) {
  const body = h("div", null, h("div", { class: "sk sk-line" }), h("div", { class: "sk sk-line" }));
  let st = null;
  const m = modal({ title: "Send to Google Slides", subtitle: "Exports the deck to PowerPoint, then uploads it to your Google Drive as a Slides presentation.", width: "500px", body,
    actions: [{ label: "Close", kind: "ghost" }, { label: "Send", kind: "primary", icon: "send", onClick: () => send() }] });
  const sendBtn = m.el.querySelector(".modal-foot .btn.primary");
  const fsel = fileSelect(file);
  let mode = "editable";
  const seg = h("div", { class: "seg" });
  const drawSeg = () => mount(seg, [["editable", "Editable text"], ["screenshots", "Pictures"]].map(([v, l]) => h("button", { type: "button", class: mode === v ? "on" : "", onclick: () => { mode = v; drawSeg(); } }, l)));
  drawSeg();
  async function check(refresh) {
    if (sendBtn) sendBtn.disabled = true;
    st = await tryApi("GET", "/api/design/gslides/status" + (refresh ? "?refresh=1" : ""), undefined, { feature: "gslides" }).catch((e) => ({ error: e.message }));
    if (st === null) { mount(body, emptyState("present", "Not available on this server", "This server has no Google Slides route yet.")); return; }
    if (!st.installed || !st.authed || st.can_upload === false) {
      mount(body, h("div", { class: "col", style: { gap: "10px" } },
        h("div", { class: "err-box warn" }, h("div", { class: "row" }, icon("alert"), h("b", { class: "grow" }, !st.installed ? "The gws CLI isn't installed" : !st.authed ? "gws isn't signed in" : "gws can't write to Drive")),
          h("div", { style: { fontSize: "12.5px", marginTop: "6px" } }, st.instructions || st.error || "")),
        h("pre", { class: "code-snippet mono" }, !st.installed ? "npm install -g @googleworkspace/cli\ngws auth login" : "gws auth login"),
        h("div", { class: "row" }, h("span", { class: "faint grow", style: { fontSize: "11.5px" } }, "Run it in a terminal, finish the browser sign-in, then check again."),
          btn("Check again", { cls: "sm", icon: "refresh", onClick: () => check(true) }))));
      return;
    }
    mount(body, h("div", null,
      h("div", { class: "row", style: { marginBottom: "12px", fontSize: "12.5px" } }, h("span", { class: "dot ok" }), h("span", null, "Signed in to Google as ", h("b", null, st.user || "your account"))),
      field("Page", fsel), field("PowerPoint mode", seg, "Editable keeps text as real text boxes in Slides; pictures look exact.")));
    if (sendBtn) sendBtn.disabled = false;
  }
  async function send() {
    if (!st || !st.authed) return false;
    let r;
    try { r = await api("POST", `${P_(S.project.id)}/send/google-slides`, { file: fsel.value, options: { mode } }); }
    catch (e) { toastError(e, "Couldn't send to Google Slides"); if (e.status === 412) check(true); return false; }
    // Done: an "Open in Slides" link instead of a download.
    jobCard({ title: "Google Slides", sub: fsel.value, poll: () => api("GET", `${P_(S.project.id)}/send/jobs/${encodeURIComponent(r.job_id)}`),
      onDone: (j, acts) => {
        if (!/^https:\/\/docs\.google\.com\//.test(j.url || "")) return;
        acts.appendChild(h("a", { class: "btn primary sm", href: j.url, target: "_blank", rel: "noopener noreferrer" }, icon("external"), "Open in Slides"));
        toast("Sent to Google Slides", { kind: "success", action: { label: "Open", run: () => window.open(j.url, "_blank", "noopener") } });
      } });
  }
  check(false);
}

// ── Hand off to a coding session in a chosen repo ────────────────────────
export async function handoffDialog(file) {
  if (!S.engines) await loadEngines();
  const engines = (S.engines?.engines || [{ id: "claude_code", available: true }, { id: "codex", available: true }, { id: "antigravity", available: true }]);
  const local = S.engines?.local || {};
  let engine = (engines.find((e) => e.id === prefs.get("handoffEngine", "claude_code") && e.available !== false) || engines.find((e) => e.available !== false) || engines[0]).id;
  let isLocal = false, model = "";
  const dir = prefs.get("handoffRepo", "");
  const pathIn = h("input", { class: "input mono", value: dir, placeholder: "C:\\Users\\you\\code\\my-app", "aria-label": "Repository folder", style: { flex: 1, minWidth: 0 } });
  const browser = h("div", { class: "dir-browser" });
  const engSel = h("select", { class: "select" }, engines.map((e) => h("option", { value: e.id, disabled: e.available === false || null, selected: e.id === engine || null }, (ENG[e.id] || e.id) + (e.available === false ? " (not installed)" : ""))));
  const modelSel = h("select", { class: "select" });
  const localSw = h("input", { type: "checkbox", disabled: local.available === false || null });
  const note = h("textarea", { class: "input", rows: 2, placeholder: "Anything the coding agent should know — which screens, the route to add, what to leave alone." });
  const fsel = fileSelect(file);
  const drawModels = () => {
    const info = engines.find((e) => e.id === engine) || {};
    const list = isLocal ? (local.models || []) : (info.models || []);
    const def = isLocal ? local.default_model || local.model : info.default_model;
    mount(modelSel, h("option", { value: "" }, def ? `Default — ${def}` : "Default model"), list.map((mm) => h("option", { value: mm.id, selected: mm.id === model || null }, mm.label && mm.label !== mm.id ? `${mm.label} (${mm.id})` : mm.id)));
  };
  engSel.addEventListener("change", () => { engine = engSel.value; model = ""; drawModels(); });
  modelSel.addEventListener("change", () => { model = modelSel.value; });
  localSw.addEventListener("change", () => { isLocal = localSw.checked; model = ""; drawModels(); });
  drawModels();
  async function browse(p) {
    mount(browser, h("div", { class: "faint", style: { padding: "8px", fontSize: "12px" } }, "Loading…"));
    const r = await api("GET", "/api/design/fs/dirs" + (p ? "?path=" + encodeURIComponent(p) : "")).catch((e) => { mount(browser, h("div", { class: "err-box" }, e.message)); return null; });
    if (!r) return;
    pathIn.value = r.path;
    mount(browser,
      h("div", { class: "dir-head" },
        r.parent ? btn("", { kind: "quiet", icon: "back", cls: "sm", title: "Up one folder", onClick: () => browse(r.parent) }) : null,
        h("span", { class: "mono ellipsis grow", title: r.path }, r.path), r.git ? h("span", { class: "pill ok" }, "git repo") : null,
        btn("", { kind: "quiet", icon: "folder", cls: "sm", title: "Home folder", onClick: () => browse(r.home) }),
        (r.roots || []).length ? btn("", { kind: "quiet", icon: "desktop", cls: "sm", title: "Drives", onClick: (e) => menu(e.currentTarget, r.roots.map((d) => ({ label: d, onClick: () => browse(d) }))) }) : null),
      h("div", { class: "dir-list" }, r.dirs.length ? r.dirs.map((d) => h("button", { type: "button", class: "dir-item", onclick: () => browse(d.path) }, icon(d.git ? "repo" : "folder"), h("span", { class: "ellipsis grow" }, d.name), d.git ? h("span", { class: "pill ok" }, "git") : null))
        : h("div", { class: "faint", style: { padding: "8px", fontSize: "12px" } }, "No sub-folders.")));
  }
  pathIn.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); browse(pathIn.value.trim()); } });
  modal({ title: "Hand off to a coding session", subtitle: "Starts a Task-mode session with the handoff bundle staged and a starter prompt. The agent works in the folder you pick.", width: "640px",
    body: h("div", null,
      field("Repository", h("div", { class: "col", style: { gap: "6px" } }, h("div", { class: "row" }, pathIn, btn("Browse", { cls: "sm", icon: "folder", onClick: () => browse(pathIn.value.trim()) })), browser)),
      h("div", { class: "row wrap handoff-pickers" }, field("Engine", engSel), field("Model", modelSel),
        h("label", { class: "check", title: local.available === false ? "Local model not available" : "Run on the local model through the telecode proxy" }, localSw, "Local")),
      field("Primary design", fsel), field("Notes for the agent (optional)", note)),
    actions: [{ label: "Cancel", kind: "ghost" }, { label: "Start session", kind: "primary", icon: "play", onClick: async () => {
      const repo = pathIn.value.trim();
      if (!repo) { toast("Pick the repository folder first", { kind: "error" }); return false; }
      let r;
      try { r = await api("POST", `${P_(S.project.id)}/handoff/session`, { repo, engine, model: model || undefined, is_local: isLocal, file: fsel.value || undefined, note: note.value }); }
      catch (e) { toastError(e, "Couldn't start the session"); return false; }
      prefs.set("handoffRepo", repo); prefs.set("handoffEngine", engine);
      handoffCard(r);
    } }] });
  browse(dir || "");
}
function handoffCard(r) {
  const card = jobCard({ title: `Coding session · ${ENG[r.engine] || r.engine}`, sub: r.repo,
    poll: async () => {
      const t = await api("GET", `/api/tasks/${encodeURIComponent(r.task_id)}`);
      const st = t.status === "completed" ? "done" : t.status === "pending" ? "running" : t.status;
      return { status: st, progress: t.progress || (st === "running" ? 0.3 : 0), message: t.status === "pending" ? "Queued" : "Working in " + r.repo, error: t.error };
    } });
  const keep = (el) => { el.dataset.keep = "1"; return el; };
  card.lastElementChild?.append(keep(h("a", { class: "btn sm", href: "/tasks", target: "_blank", rel: "noopener" }, icon("external"), "Open Task mode")),
    keep(btn("", { kind: "quiet", icon: "copy", cls: "sm", title: "Copy the session id", onClick: () => copyText(r.session_id) })));
  toast(`Session ${r.session_id} started — follow it in Task mode.`, { kind: "success" });
}

// ── Import from a Figma link (Figma REST, your personal access token) ────
export async function figmaDialog() {
  const st = await tryApi("GET", "/api/design/figma", undefined, { feature: "figma" }).catch(() => null);
  if (st === null) { toast("Figma import isn't available on this server yet.", { kind: "error" }); return; }
  const url = h("input", { class: "input", placeholder: "https://www.figma.com/design/AbC123…/My-file?node-id=1-2", "aria-label": "Figma link" });
  const tok = h("input", { class: "input mono", type: "password", placeholder: st.has_token ? "Saved — paste a new one to replace it" : "figd_…", autocomplete: "off", "aria-label": "Figma token" });
  const frames = h("div", { class: "figma-frames" });
  const picked = new Set();
  let listed = null;
  const saveTok = async () => { if (!tok.value.trim()) return; await api("PUT", "/api/design/figma/token", { token: tok.value.trim() }); tok.value = ""; tok.placeholder = "Saved — paste a new one to replace it"; st.has_token = true; };
  async function list() {
    try { await saveTok(); } catch (e) { toastError(e); return; }
    if (!url.value.trim()) { url.focus(); return; }
    mount(frames, h("div", { class: "faint", style: { fontSize: "12px" } }, "Reading the file…"));
    let r;
    try { r = await api("POST", `${P_(S.project.id)}/import/figma/frames`, { url: url.value.trim() }); }
    catch (e) { mount(frames, h("div", { class: "err-box" }, e.message)); return; }
    listed = r; picked.clear(); r.frames.slice(0, 20).forEach((f) => picked.add(f.id));
    if (!r.frames.length) { mount(frames, h("div", { class: "faint" }, "No top-level frames found.")); return; }
    mount(frames, h("div", { class: "faint", style: { fontSize: "12px", marginBottom: "6px" } }, `${r.file_name || "File"} — ${r.frames.length} frame${r.frames.length > 1 ? "s" : ""} (up to 20 per import)`),
      r.frames.map((f) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: picked.has(f.id), onchange: (e) => { e.target.checked ? picked.add(f.id) : picked.delete(f.id); } }),
        h("span", { class: "ellipsis grow" }, f.name), h("span", { class: "faint mono", style: { fontSize: "11px" } }, `${f.width}×${f.height}${f.page ? " · " + f.page : ""}`))));
  }
  modal({ title: "Import from Figma", subtitle: "Each frame comes in as a picture on its own board; ask the agent to rebuild the ones you want as real HTML.", width: "560px",
    body: h("div", null, field("Figma link", url),
      field("Personal access token", tok, "Figma → Settings → Security → Personal access tokens (read-only file access is enough). Stored in telecode's settings.json."),
      h("div", { class: "row", style: { marginBottom: "10px" } }, btn("List frames", { cls: "sm", icon: "search", onClick: () => list() })), frames),
    actions: [{ label: "Cancel", kind: "ghost" }, { label: "Import", kind: "primary", icon: "figma", onClick: async () => {
      try { await saveTok(); } catch (e) { toastError(e); return false; }
      if (!url.value.trim()) { toast("Paste a Figma link first", { kind: "error" }); return false; }
      const ids = listed ? [...picked] : undefined;
      if (listed && !ids.length) { toast("Pick at least one frame", { kind: "error" }); return false; }
      let r;
      try { r = await api("POST", `${P_(S.project.id)}/import/figma`, { url: url.value.trim(), ids }); }
      catch (e) { toastError(e, "Figma import failed"); return false; }
      toast(`Imported ${r.boards.length} frame${r.boards.length > 1 ? "s" : ""} from Figma`, { kind: "success" });
      const { loadFiles } = await import("./state.js"); await loadFiles();
      if (r.boards[0]) (await import("./workspace.js")).openFile(r.boards[0], { view: "preview" });
    } }] });
}
