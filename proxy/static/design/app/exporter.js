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
  { kind: "handoff", label: "Handoff bundle", icon: "code", hint: "For a coding agent to implement" },
  { kind: "handoff-prompt", label: "Copy handoff prompt", icon: "copy", hint: "Paste into Claude Code or Codex", local: true },
];
export function exportMenu(anchor) {
  const cur = S.activeFile && /\.html?$/i.test(S.activeFile) ? S.activeFile : htmlFiles()[0]?.path;
  menu(anchor, [{ heading: cur ? `Export ${cur}` : "Export" }, ...KINDS.map((k) => k === "-" ? "-" : {
    label: k.label, icon: k.icon, hint: k.hint, disabled: features["export_" + k.kind] === false || (!cur && !["zip", "handoff", "handoff-prompt"].includes(k.kind)),
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
    body.append(field("Mode", mode, "Screenshots look exact. Editable keeps text as real PowerPoint text boxes."), field("Font swaps", swaps, "One per line, for fonts PowerPoint won't have."), field("Hide before capture", hide, "CSS selectors"));
    opts.get = () => ({ mode: m, fontSwaps: Object.fromEntries(swaps.value.split("\n").map((l) => l.split("=").map((x) => x.trim())).filter((p) => p[0] && p[1])), hideSelectors: hide.value.split(",").map((x) => x.trim()).filter(Boolean) });
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
  jobCard({ title: label, sub: file || S.project.title, poll: () => api("GET", `${P_(pid)}/export/jobs/${encodeURIComponent(r.job_id)}`),
    download: (j) => j.download_url || `${P_(pid)}/export/download/${encodeURIComponent(r.job_id)}` });
}

// Generic progress card: poll() → {status, progress, error, flags}
export function jobCard({ title, sub, poll, download, stop }) {
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
    if (st === "done" || st === "completed") {
      bar.style.width = "100%";
      clear(actions);
      if (download) { const url = download(j); if (url && (url.startsWith("/") || url.startsWith(location.origin))) actions.appendChild(h("a", { class: "btn primary sm", href: url, download: "" }, icon("download"), "Download")); }
      return;
    }
    if (st === "failed" || st === "cancelled") { bar.style.background = "var(--err)"; clear(actions); return; }
    setTimeout(tick, 1000);
  };
  setTimeout(tick, 400);
  return card;
}

// ── Share ────────────────────────────────────────────────────────────────
export async function shareDialog() {
  const pid = S.project.id;
  const listHost = h("div", null, h("div", { class: "sk sk-line" }), h("div", { class: "sk sk-line" }));
  let role = "view";
  const roleSeg = h("div", { class: "seg" });
  const drawRole = () => mount(roleSeg, [["view", "Can view"], ["comment", "Can comment"], ["edit", "Can edit"]].map(([v, l]) => h("button", { type: "button", class: role === v ? "on" : "", onclick: () => { role = v; drawRole(); } }, l)));
  drawRole();
  const m = modal({ title: "Share", subtitle: "A link to a snapshot of this project. Anyone who can reach this machine can open it.", width: "520px",
    body: h("div", null, h("div", { class: "row", style: { marginBottom: "14px" } }, roleSeg, h("span", { class: "grow" }), btn("Create link", { kind: "primary", icon: "link", onClick: () => create() })), listHost) });
  const linkOf = (t) => `${location.origin}/design/s/${encodeURIComponent(t)}`;
  async function load() {
    let r;
    try { r = await api("GET", `${P_(pid)}/share`, undefined, { feature: "share" }); }
    catch (e) {
      if (e instanceof ApiMissing) { mount(listHost, emptyState("share", "Sharing isn't available yet", "This server doesn't have share links.")); m.el.querySelector(".modal-body .row")?.classList.add("hidden"); return; }
      mount(listHost, emptyState("share", "Sharing is turned off", e.status === 403 || e.status === 400 ? "Set design.share.enabled to true in settings.json, then reload." : e.message)); return;
    }
    if (r.enabled === false) { mount(listHost, emptyState("share", "Sharing is turned off", "Set design.share.enabled to true in settings.json, then reload.")); m.el.querySelector(".modal-body .row")?.classList.add("hidden"); return; }
    const tokens = r.shares || r.tokens || [];
    if (!tokens.length) { mount(listHost, h("p", { class: "muted", style: { margin: 0, fontSize: "12.5px" } }, "No links yet. Create one to share a read-only snapshot.")); return; }
    mount(listHost, tokens.map((t) => h("div", { class: "share-link" }, h("span", { class: "pill" }, t.role || "view"), h("span", { class: "u", title: linkOf(t.token) }, linkOf(t.token)),
      h("span", { class: "faint", style: { fontSize: "11px" } }, relTime(t.created_at)),
      btn("", { kind: "quiet", icon: "copy", cls: "sm", title: "Copy link", onClick: () => copyText(linkOf(t.token)) }),
      btn("", { kind: "quiet", icon: "trash", cls: "sm", title: "Revoke", onClick: async () => { await api("DELETE", `${P_(pid)}/share/${encodeURIComponent(t.token)}`).catch(toastError); toast("Link revoked", { kind: "success" }); load(); } }))));
  }
  async function create() {
    try { const r = await api("POST", `${P_(pid)}/share`, { role }, { feature: "share" }); const t = r.share?.token || r.token; if (t) copyText(r.url ? location.origin + r.url : linkOf(t)); load(); }
    catch (e) { toastError(e, e.status === 403 ? "Sharing is turned off (design.share.enabled)" : "Couldn't create the link"); }
  }
  load();
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

// ── Engines & integrations ───────────────────────────────────────────────
export async function settingsDialog() {
  const engHost = h("div", null, h("div", { class: "sk sk-line" }), h("div", { class: "sk sk-line" }));
  const mcpHost = h("div");
  const author = h("input", { class: "input", value: prefs.get("author", "You"), style: { width: "200px" } });
  author.addEventListener("change", () => prefs.set("author", author.value.trim() || "You"));
  const theme = h("select", { class: "select", style: { width: "160px" } }, [["system", "Match system"], ["dark", "Dark"], ["light", "Light"]].map(([v, l]) => h("option", { value: v, selected: prefs.get("theme", "system") === v || null }, l)));
  theme.addEventListener("change", () => { prefs.set("theme", theme.value); applyTheme(); });
  async function drawEngines(force) {
    mount(engHost, h("div", { class: "sk sk-line" }));
    const e = await loadEngines(force);
    if (!e) { mount(engHost, h("p", { class: "muted", style: { fontSize: "12.5px" } }, "Engine status isn't available on this server yet.")); return; }
    mount(engHost, (e.engines || []).map((x) => h("div", { class: "eng-row" }, h("span", { class: "dot " + (x.available ? "ok" : "err") }), h("span", { class: "nm" }, ENG[x.id] || x.id),
      h("span", { class: "v" }, x.available ? x.version || "installed" : "not found on PATH"))),
      h("div", { class: "eng-row" }, h("span", { class: "dot " + (e.local?.available ? "ok" : "") }), h("span", { class: "nm" }, "Local model (proxy)"), h("span", { class: "v" }, e.local?.available ? e.local.model || "running" : "not running")));
  }
  async function drawMcp() {
    const st = await tryApi("GET", "/api/design/mcp/status", undefined, { feature: "mcp" }).catch(() => null);
    if (!st) { mount(mcpHost, h("p", { class: "muted", style: { fontSize: "12.5px", margin: 0 } }, "One-click MCP registration isn't available on this server yet. Manually: ", h("code", { class: "mono" }, "claude mcp add telecode --transport streamable-http --url http://127.0.0.1:1236/mcp"))); return; }
    const raw = st.clients || [];
    const clients = Array.isArray(raw) ? raw : Object.entries(raw).map(([client, v]) => ({ client, ...v }));
    const srv = st.server || {};
    mount(mcpHost,
      srv.url ? h("div", { class: "faint", style: { fontSize: "11.5px", marginBottom: "4px" } }, "Server ", h("span", { class: "mono" }, `${srv.name || "telecode"} at ${srv.url}`),
        srv.enabled === false ? h("span", { class: "pill warn", style: { marginLeft: "6px" } }, "mcp_server.enabled is off") : null) : null,
      clients.map((info) => {
        const c = info.client;
        const label = info.label || ENG[c] || c;
        const state = !info.available ? "not installed" : info.matches ? "registered" : info.registered ? "registered elsewhere" : "not registered";
        const reg = async (force) => {
          let r;
          try { r = await api("POST", "/api/design/mcp/register", { client: c, force: !!force }); }
          catch (e) {
            const b = e.body || {};
            if (b.conflict && !force) { if (await confirmDialog("Replace the existing entry?", b.error || "An MCP server with this name is already configured.", { confirm: "Replace" })) return reg(true); return; }
            toastError(e); return;
          }
          if (r.conflict && !force) { if (await confirmDialog("Replace the existing entry?", r.error || "An MCP server with this name is already configured.", { confirm: "Replace" })) return reg(true); return; }
          toast(r.already ? `${label} was already registered` : `Registered with ${label}`, { kind: "success" });
          drawMcp();
        };
        const preview = async () => {
          const r = await api("POST", "/api/design/mcp/register", { client: c, dry_run: true }).catch((e) => ({ error: e.message, ...(e.body || {}) }));
          const cmds = (r.commands || []).map((a) => a.join(" ")).join("\n") || r.error || (r.already ? "Already registered; nothing to run." : "Nothing to run.");
          modal({ title: `Commands for ${label}`, width: "560px", body: h("pre", { class: "md-code", style: { whiteSpace: "pre-wrap" } }, cmds) });
        };
        return h("div", { class: "eng-row" }, h("span", { class: "dot " + (info.matches ? "ok" : info.registered ? "warn" : "") }), h("span", { class: "nm" }, label),
          h("span", { class: "v" }, state),
          btn("", { kind: "quiet", icon: "terminal", cls: "sm", title: "Show the commands (dry run)", disabled: !info.available, onClick: preview }),
          btn(info.matches ? "Re-register" : "Register", { cls: "sm", disabled: !info.available, onClick: () => reg(false) }));
      }));
  }
  modal({ title: "Engines and integrations", width: "560px",
    body: h("div", null,
      h("div", { class: "section-h" }, h("h3", null, "Engines"), h("span", { class: "grow" }), btn("Re-check connection", { icon: "refresh", cls: "sm", onClick: () => drawEngines(true) })), engHost,
      h("div", { class: "section-h", style: { marginTop: "20px" } }, h("h3", null, "Use TeleDesign from other CLIs")),
      h("p", { class: "muted", style: { fontSize: "12px", marginTop: "-6px" } }, "Registers telecode's MCP server so the design_* tools and the /design prompt work there."), mcpHost,
      h("div", { class: "section-h", style: { marginTop: "20px" } }, h("h3", null, "You")),
      h("div", { class: "row", style: { gap: "18px" } }, field("Name on comments", author), field("Theme", theme))) });
  drawEngines(); drawMcp();
}
