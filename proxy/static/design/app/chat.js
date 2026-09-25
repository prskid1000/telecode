// TeleDesign — chat panel: chat tabs, messages, streaming, todos, question
// forms, composer (attachments, selection, comments), queue + stop, engines,
// cost / context meter.
import {
  h, icon, btn, mount, clear, api, tryApi, toast, toastError, menu, confirmDialog, promptDialog, bus, P_, prefs,
  renderMarkdown, relTime, fmtTokens, fmtCost, fmtDuration, features, emptyState, skeleton, modal,
} from "./core.js";
import { S, currentTurns, chatRunning, loadEngines } from "./state.js";
import { splitForm, parseStreamingForm, renderForm } from "./form.js";

const ENGINE_LABEL = { claude_code: "Claude Code", codex: "Codex", antigravity: "Antigravity" };
const EFFORTS = [["", "Default effort"], ["low", "Low"], ["medium", "Medium"], ["high", "High"], ["xhigh", "Extra high"], ["max", "Max"]];

let host = null, els = {};
const turnChat = new Map();      // turn_id -> chat_id
const formState = new Map();     // turn_id -> answers (kept while the form streams)
const openTools = new Set();     // turn ids whose tool list is expanded
const queues = new Map();        // chat_id -> [{text, body}]
const draft = { attachments: [], selection: null, commentIds: [], skills: [] };
let skillsCache = null;
let unsub = [];

// ── Public API used by other modules ─────────────────────────────────────
export async function sendToChat(text, extra = {}) {
  const cid = await ensureChat();
  if (!cid) return null;
  return submit(cid, text, extra);
}
bus.on("attach", (paths) => { for (const p of paths) if (!draft.attachments.includes(p)) draft.attachments.push(p); drawChips(); focusComposer(); });
bus.on("chat-selection", (sel) => { draft.selection = sel; drawChips(); focusComposer(); });
bus.on("chat-prefill", (t) => { if (els.ta) { els.ta.value = t; autosize(); focusComposer(); } });
bus.on("project-opened", async ({ firstPrompt, styleId }) => {
  await loadChats();
  if (firstPrompt) sendToChat(firstPrompt, styleId ? { style_id: styleId } : {});
});

// ── Render ───────────────────────────────────────────────────────────────
export function render(target) {
  host = target;
  unsub.forEach((f) => f()); unsub = [];
  els = {};
  els.tabs = h("div", { class: "chat-tabs", role: "tablist", "aria-label": "Chats" });
  els.meta = h("div", { class: "chat-meta" });
  els.msgs = h("div", { class: "msgs", "aria-live": "polite" });
  els.composer = composer();
  mount(host, h("div", { class: "pane" }, els.tabs, els.meta, els.msgs, els.composer));
  if (features.chats === false) { drawMissing(); return; }
  if (!S.chats.length) mount(els.msgs, skeleton(5));
  loadChats().then(() => { drawTabs(); drawMeta(); drawMessages(); });
  unsub.push(
    bus.on("ev:turn", onTurn), bus.on("ev:delta", onDelta), bus.on("ev:tool", onTool), bus.on("ev:todo", onTodo),
    bus.on("ev:form", onForm), bus.on("ev:check", onCheck), bus.on("ev:files", onFiles), bus.on("ev:chat", () => loadChats(true)),
    bus.on("poll", poll), bus.on("engines", () => { drawMeta(); drawPickers(); }),
  );
  if (!S.engines) loadEngines();
  return () => { unsub.forEach((f) => f()); unsub = []; };
}
function drawMissing() {
  mount(els.msgs, emptyState("chat", "Chat isn't available yet", "This server doesn't have the design chat API. Projects, files and systems still work."));
  els.composer.classList.add("hidden");
}

// ── Chats ────────────────────────────────────────────────────────────────
let chatsLoaded = null;
export async function loadChats(force = false) {
  if (!S.project) return;
  if (chatsLoaded === S.project.id && !force) return;
  const j = await tryApi("GET", P_(S.project.id) + "/chats", undefined, { feature: "chats" }).catch((e) => { toastError(e); return null; });
  if (!j) { if (features.chats === false && host) drawMissing(); return; }
  chatsLoaded = S.project.id;
  S.chats = j.chats || [];
  if (!S.chatId || !S.chats.some((c) => c.id === S.chatId)) S.chatId = S.project.active_chat_id && S.chats.some((c) => c.id === S.project.active_chat_id) ? S.project.active_chat_id : S.chats[0]?.id || null;
  if (S.chatId) await loadTurns(S.chatId);
  if (host) { drawTabs(); drawMeta(); drawMessages(); }
}
async function loadTurns(cid) {
  const j = await tryApi("GET", `${P_(S.project.id)}/chats/${cid}/turns`).catch(() => null);
  const turns = (j && j.turns) || [];
  // Keep streamed text for turns the server hasn't flushed yet.
  const prev = new Map((S.turns.get(cid) || []).map((t) => [t.id, t]));
  S.turns.set(cid, turns.map((t) => { turnChat.set(t.id, cid); const p = prev.get(t.id); return p && (p.text || "").length > (t.text || "").length && t.status === "running" ? { ...t, text: p.text } : t; }));
}
export async function ensureChat() {
  if (S.chatId) return S.chatId;
  if (features.chats === false) { toast("Chat isn't available on this server yet.", { kind: "error" }); return null; }
  try {
    const r = await api("POST", P_(S.project.id) + "/chats", { title: "Chat 1", engine: prefs.get("engine", undefined), is_local: prefs.get("local", undefined) });
    S.chats.push(r.chat); S.chatId = r.chat.id; S.turns.set(r.chat.id, []);
    drawTabs(); drawMeta();
    return S.chatId;
  } catch (e) { toastError(e, "Couldn't start a chat"); return null; }
}
async function newChat(fromChatId) {
  const title = `Chat ${S.chats.length + 1}`;
  try {
    const r = await api("POST", P_(S.project.id) + "/chats", { title, engine: currentChat()?.engine, is_local: currentChat()?.is_local, from_chat_id: fromChatId || undefined });
    S.chats.push(r.chat); S.chatId = r.chat.id; S.turns.set(r.chat.id, []);
    await loadTurns(r.chat.id);
    drawTabs(); drawMeta(); drawMessages(); focusComposer();
  } catch (e) { toastError(e, "Couldn't create the chat"); }
}
const currentChat = () => S.chats.find((c) => c.id === S.chatId) || null;
export async function switchChat(cid) {
  S.chatId = cid;
  bus.emit("chat-switched", cid);
  const { writeUrl } = await import("./state.js"); writeUrl();
  drawTabs(); mount(els.msgs, skeleton(4));
  await loadTurns(cid); drawMeta(); drawMessages();
}

function drawTabs() {
  if (!els.tabs) return;
  mount(els.tabs, S.chats.map((c) => {
    const running = chatRunning(c.id);
    return h("button", { class: "chat-tab" + (c.id === S.chatId ? " on" : ""), role: "tab", "aria-selected": c.id === S.chatId ? "true" : "false", title: c.title,
      onclick: () => c.id !== S.chatId && switchChat(c.id), oncontextmenu: (e) => { e.preventDefault(); chatMenu(e.currentTarget, c); },
      ondblclick: () => renameChat(c) },
      running ? h("span", { class: "dot accent pulse" }) : null, h("span", { class: "nm" }, c.title || "Chat"),
      c.id === S.chatId ? h("span", { class: "ic", style: { width: "12px", height: "12px", color: "var(--faint)" }, onclick: (e) => { e.stopPropagation(); chatMenu(e.currentTarget, c); } }, icon("chevronDown")) : null);
  }), btn("", { kind: "quiet", icon: "plus", cls: "sm", title: "New chat  (Alt+N)", onClick: (e) => menu(e.currentTarget, [
    { label: "New chat", icon: "plus", hint: "Starts with a fresh context", onClick: () => newChat() },
    S.chatId ? { label: "Continue in a new chat", icon: "chat", hint: "Carries a summary of this one", onClick: () => newChat(S.chatId) } : null,
  ]) }));
}
function chatMenu(anchor, c) {
  menu(anchor, [
    { label: "Rename", icon: "edit", onClick: () => renameChat(c) },
    { label: "Continue in a new chat", icon: "chat", onClick: () => newChat(c.id) },
    "-",
    { label: "Delete chat", icon: "trash", danger: true, onClick: async () => {
      if (!await confirmDialog("Delete chat", `Delete "${c.title}"? Files the agent wrote stay in the project.`, { confirm: "Delete", danger: true })) return;
      try { await api("DELETE", `${P_(S.project.id)}/chats/${c.id}`); S.chats = S.chats.filter((x) => x.id !== c.id); S.turns.delete(c.id); if (S.chatId === c.id) { S.chatId = S.chats[0]?.id || null; if (S.chatId) await loadTurns(S.chatId); } drawTabs(); drawMeta(); drawMessages(); }
      catch (e) { toastError(e); }
    } },
  ]);
}
async function renameChat(c) {
  const t = await promptDialog("Rename chat", { value: c.title, confirm: "Rename" });
  if (!t) return;
  try { const r = await api("PATCH", `${P_(S.project.id)}/chats/${c.id}`, { title: t }); Object.assign(c, r.chat || { title: t }); drawTabs(); }
  catch (e) { toastError(e); }
}

// ── Meta row: engine + meter ─────────────────────────────────────────────
function usageTotals(turns) {
  const u = { input: 0, output: 0, cache_read: 0, cost_usd: 0, duration_ms: 0, turns: 0, last: null };
  for (const t of turns) {
    if (!t.usage || t.role === "user") continue;
    u.input += +t.usage.input || 0; u.output += +t.usage.output || 0; u.cache_read += +t.usage.cache_read || 0;
    u.cost_usd += +t.usage.cost_usd || 0; u.duration_ms += +t.usage.duration_ms || 0; u.turns++; u.last = t.usage;
  }
  return u;
}
function drawMeta() {
  if (!els.meta) return;
  const c = currentChat();
  const u = usageTotals(currentTurns());
  const ctxUsed = u.last ? (+u.last.input || 0) + (+u.last.cache_read || 0) : 0;
  const ctxWin = S.engines?.context_window || 200000;
  const pct = Math.min(100, Math.round((ctxUsed / ctxWin) * 100));
  const meter = h("button", { class: "meter", title: "Usage and context", onclick: (e) => meterPopover(e.currentTarget, u, ctxUsed, ctxWin) },
    h("span", { class: "meter-bar" }, h("i", { style: { width: pct + "%", background: pct > 85 ? "var(--err)" : pct > 65 ? "var(--warn)" : "var(--accent)" } })),
    `${fmtTokens(u.input + u.output + u.cache_read)} tok`, u.cost_usd ? h("span", null, "· " + fmtCost(u.cost_usd)) : null);
  const running = chatRunning();
  mount(els.meta,
    h("span", { class: "row", style: { gap: "6px" } }, h("span", { class: "dot " + (running ? "accent pulse" : "ok") }), running ? "Working…" : c ? (ENGINE_LABEL[c.engine] || c.engine || "Agent") + (c.is_local ? " · local" : "") : "New chat"),
    h("span", { style: { marginLeft: "auto" } }), meter);
}
function meterPopover(anchor, u, ctxUsed, ctxWin) {
  const row = (k, v) => h("div", { class: "row", style: { justifyContent: "space-between", padding: "3px 0", fontSize: "12px" } }, h("span", { class: "muted" }, k), h("span", { class: "mono" }, v));
  menu(anchor, [{ node: h("div", { style: { padding: "6px 8px", minWidth: "240px" } },
    h("div", { style: { fontWeight: 600, marginBottom: "6px" } }, "This chat"),
    row("Input tokens", fmtTokens(u.input)), row("Output tokens", fmtTokens(u.output)), row("Cache reads", fmtTokens(u.cache_read)),
    row("Cost", fmtCost(u.cost_usd)), row("Agent time", fmtDuration(u.duration_ms)), row("Turns", String(u.turns)),
    h("div", { style: { height: "1px", background: "var(--border)", margin: "8px 0" } }),
    h("div", { style: { fontWeight: 600, marginBottom: "4px" } }, "Context"),
    row("Last prompt", `${fmtTokens(ctxUsed)} / ${fmtTokens(ctxWin)}`),
    h("div", { class: "faint", style: { fontSize: "11px", marginTop: "6px", maxWidth: "240px" } }, "Switching engines starts a fresh session, so earlier context is lost. Use “Continue in a new chat” to carry a summary.")) }], { align: "right" });
}

// ── Messages ─────────────────────────────────────────────────────────────
function drawMessages() {
  if (!els.msgs) return;
  const turns = currentTurns();
  if (!S.chatId || !turns.length) {
    mount(els.msgs, emptyState("sparkle", "Start with a brief", "Describe the design, attach screenshots or a brand PDF, or point at an element in the preview and ask for a change.",
      h("div", { class: "col", style: { width: "100%", maxWidth: "300px" } }, ["A landing page for a climbing gym, bold and warm", "Three directions for a pricing page", "Turn the attached screenshot into a clickable prototype"].map((s) =>
        h("button", { class: "opt", style: { width: "100%" }, onclick: () => { els.ta.value = s; autosize(); focusComposer(); } }, s)))));
    return;
  }
  const atBottom = els.msgs.scrollHeight - els.msgs.scrollTop - els.msgs.clientHeight < 60;
  clear(els.msgs);
  turns.forEach((t, i) => els.msgs.appendChild(renderTurn(t, turns, i)));
  if (atBottom || !els.msgs._scrolled) { els.msgs.scrollTop = els.msgs.scrollHeight; els.msgs._scrolled = true; }
}
let rafPending = new Set(), rafId = 0;
function redrawTurn(id) {
  rafPending.add(id);
  if (rafId) return;
  rafId = requestAnimationFrame(() => {
    rafId = 0;
    const ids = [...rafPending]; rafPending.clear();
    const turns = currentTurns();
    const atBottom = els.msgs && els.msgs.scrollHeight - els.msgs.scrollTop - els.msgs.clientHeight < 80;
    for (const tid of ids) {
      const i = turns.findIndex((t) => t.id === tid);
      if (i < 0 || !els.msgs) continue;
      const old = els.msgs.querySelector(`[data-turn="${CSS.escape(tid)}"]`);
      const nu = renderTurn(turns[i], turns, i);
      if (old) old.replaceWith(nu); else { drawMessages(); return; }
    }
    if (atBottom && els.msgs) els.msgs.scrollTop = els.msgs.scrollHeight;
  });
}

function answeredFor(turns, i, formId) {
  for (let k = i + 1; k < turns.length; k++) {
    const fa = turns[k].form_answers;
    if (fa && (!formId || fa.id === formId || fa.form_id === formId || !fa.id)) return fa.answers || fa;
    if (turns[k].role === "user" && !fa) return null;
  }
  return null;
}

function renderTurn(t, turns, i) {
  if (t.role === "user") return renderUser(t);
  const wrap = h("div", { class: "msg assistant", dataset: { turn: t.id } });
  const running = t.status === "running" || t.status === "queued";
  wrap.appendChild(h("div", { class: "who" }, h("span", { class: "av" }, icon("sparkle")), ENGINE_LABEL[t.engine] || "Designer",
    t.is_local ? h("span", { class: "pill" }, "local") : null,
    t.status === "queued" ? h("span", { class: "pill" }, "Queued") : running ? h("span", { class: "pill accent" }, h("span", { class: "dot accent pulse" }), "Working") : null,
    t.status === "cancelled" ? h("span", { class: "pill" }, "Stopped") : null,
    t.status === "failed" ? h("span", { class: "pill err" }, "Failed") : null));

  const { before, formSrc, after, complete } = splitForm(t.text || "");
  const body = (before + (complete ? after : "")).trim();
  if (body) {
    const md = renderMarkdown(body);
    if (running && !formSrc) md.lastElementChild?.classList.add("caret");
    wrap.appendChild(md);
  } else if (running && !formSrc && !(t.tools || []).length) wrap.appendChild(h("div", { class: "md muted caret" }, "Thinking"));

  if (t.todos && t.todos.length) {
    const done = t.todos.filter((x) => x.status === "completed" || x.status === "done").length;
    wrap.appendChild(h("div", { class: "todos" }, h("div", { class: "todos-h" }, h("span", null, "Plan"), h("span", null, `${done} / ${t.todos.length}`)),
      t.todos.map((x) => {
        const st = x.status === "completed" || x.status === "done" ? "done" : x.status === "in_progress" || x.status === "running" ? "run" : "";
        return h("div", { class: "todo " + st }, h("span", { class: "tick" + (st === "done" ? " on" : st === "run" ? " run" : "") }), h("span", null, x.text || x.content || ""));
      })));
  }
  const tools = t.tools || [];
  if (tools.length) {
    const box = h("div", { class: "tools" + (openTools.has(t.id) ? " open" : "") });
    const last = tools[tools.length - 1];
    box.append(h("button", { class: "tools-head", onclick: () => { box.classList.toggle("open"); box.classList.contains("open") ? openTools.add(t.id) : openTools.delete(t.id); } },
      icon("chevronRight", "chev"), icon("terminal"),
      running ? h("span", { class: "ellipsis grow" }, `${last.name} `, h("span", { class: "faint mono" }, last.input_preview || "")) : h("span", { class: "grow" }, `Used ${tools.length} tool${tools.length > 1 ? "s" : ""}`),
      h("span", { class: "faint" }, tools.length)),
      h("div", { class: "tools-list" }, tools.map((x) => h("div", { class: "tool-line" }, h("b", null, x.name), h("span", { title: x.input_preview || "" }, x.input_preview || "")))));
    wrap.appendChild(box);
  }
  if (formSrc != null || t.form) {
    const form = t.form || parseStreamingForm(formSrc);
    if (form) {
      const answered = answeredFor(turns, i, form.id);
      if (!formState.has(t.id)) formState.set(t.id, {});
      wrap.appendChild(renderForm(form, {
        streaming: running && !complete && !t.form, answered, state: formState.get(t.id),
        upload: async (files) => (await import("./workspace.js")).uploadFiles(files),
        onSubmit: (answers, summary) => sendToChat(summary || "Here are my answers.", { form_answers: { id: form.id, answers } }),
        onSkip: () => sendToChat("Skip the questions — decide everything for me and build it.", { form_answers: { id: form.id, answers: {}, skipped: true } }),
      }));
    }
  }
  for (const c of t.checks || []) wrap.appendChild(checkLine(c));
  if (t.error) {
    const msg = typeof t.error === "string" ? t.error : t.error.message || "The turn failed.";
    wrap.appendChild(h("div", { class: "err-box" }, h("div", { class: "row" }, icon("alert"), h("span", { class: "grow" }, msg.split("\n")[0])),
      msg.includes("\n") || t.error.details ? h("details", null, h("summary", null, "Show details"), h("pre", null, t.error.details || msg)) : null));
  }
  const changed = (t.changed_files || []).filter((p) => !/^(boards|comments|assets)\.json$|^thumbnail\.webp$|^doc\.fig$/.test(p));
  if (changed.length || t.version) {
    wrap.appendChild(h("div", { class: "chips" },
      changed.slice(0, 8).map((p) => h("span", { class: "chip link", title: "Open " + p, onclick: () => bus.emit("open-file", p) }, icon("file"), h("span", { class: "nm" }, p))),
      changed.length > 8 ? h("span", { class: "chip" }, `+${changed.length - 8} more`) : null,
      t.version ? h("span", { class: "chip link", title: "Show in versions", onclick: () => import("./workspace.js").then((m) => { m.setPanel("versions"); bus.emit("select-version", t.version); }) }, icon("history"), "v" + t.version) : null));
  }
  const foot = [];
  if (t.usage && !running) {
    if (t.usage.duration_ms) foot.push(fmtDuration(t.usage.duration_ms));
    const tok = (+t.usage.input || 0) + (+t.usage.output || 0);
    if (tok) foot.push(`${fmtTokens(tok)} tokens`);
    if (t.usage.cost_usd) foot.push(fmtCost(t.usage.cost_usd));
  }
  if (t.finished_at && !running) foot.push(relTime(t.finished_at));
  if (foot.length) wrap.appendChild(h("div", { class: "msg-foot" }, foot.map((f, k) => [k ? h("span", { class: "sep" }, "·") : null, f])));
  return wrap;
}
function checkLine(c) {
  const labels = {
    running: c.stage === "verifier" ? "Checking the design…" : "Checking for console errors…",
    pass: c.stage === "verifier" ? "Checked — no issues" : "No console errors",
    issues: "Found issues — fixing…", fixing: "Found issues — fixing…", timeout: "Check didn't complete",
  };
  const cls = c.status === "pass" ? "pass" : c.status === "issues" || c.status === "fixing" ? "issues" : c.status === "timeout" ? "timeout" : "";
  const el = h("div", { class: "check-line " + cls }, icon(c.status === "pass" ? "checkCircle" : c.status === "running" ? "refresh" : "alert"), h("span", { class: "grow" }, labels[c.status] || c.status));
  if (c.issues && c.issues.length) return h("div", null, el, h("details", { style: { fontSize: "11.5px", color: "var(--muted)", margin: "4px 2px" } }, h("summary", null, `${c.issues.length} issue${c.issues.length > 1 ? "s" : ""}`),
    h("ul", { style: { margin: "4px 0", paddingLeft: "18px" } }, c.issues.map((x) => h("li", null, typeof x === "string" ? x : x.message || JSON.stringify(x))))));
  return el;
}
function renderUser(t) {
  const chips = [];
  (t.attachments || []).forEach((p) => chips.push(h("span", { class: "chip link", onclick: () => bus.emit("open-file", p) }, icon("paperclip"), h("span", { class: "nm" }, p.split("/").pop()))));
  if ((t.comment_ids || []).length) chips.push(h("span", { class: "chip cmt" }, icon("comment"), `${t.comment_ids.length} comment${t.comment_ids.length > 1 ? "s" : ""}`));
  if (t.selection) chips.push(h("span", { class: "chip sel" }, icon("inspect"), h("span", { class: "nm" }, selLabel(t.selection))));
  if (t.form_answers) chips.push(h("span", { class: "chip" }, icon("check"), t.form_answers.skipped ? "Skipped questions" : "Answered questions"));
  return h("div", { class: "msg user", dataset: { turn: t.id } },
    t.text ? h("div", { class: "bubble" }, t.text) : null,
    chips.length ? h("div", { class: "chips" }, chips) : null,
    h("div", { class: "msg-foot" }, relTime(t.created_at), t.status === "queued" ? h("span", { class: "pill" }, "queued") : null));
}
function selLabel(sel) {
  if (!sel) return "";
  if (sel.td_id) return "#" + sel.td_id;
  if (sel.text) return `"${String(sel.text).slice(0, 30)}"`;
  return (sel.selector || "element").split(" ").slice(-1)[0];
}

// ── Events ───────────────────────────────────────────────────────────────
function turnsOf(cid) { if (!S.turns.has(cid)) S.turns.set(cid, []); return S.turns.get(cid); }
function findTurn(tid) {
  const cid = turnChat.get(tid);
  if (cid) { const t = turnsOf(cid).find((x) => x.id === tid); if (t) return [t, cid]; }
  for (const [c, list] of S.turns) { const t = list.find((x) => x.id === tid); if (t) { turnChat.set(tid, c); return [t, c]; } }
  return [null, null];
}
function upsert(turn) {
  const cid = turn.chat_id || turnChat.get(turn.id) || S.chatId;
  if (!cid) return;
  turnChat.set(turn.id, cid);
  const list = turnsOf(cid);
  if (turn.role === "user" && !list.some((x) => x.id === turn.id)) {
    const k = list.findIndex((x) => String(x.id).startsWith("local-") && x.text === turn.text);
    if (k >= 0) list.splice(k, 1);
  }
  if (turn.role !== "user" && !turn._placeholder) {
    for (let k = list.length - 1; k >= 0; k--) if (list[k]._placeholder) list.splice(k, 1);
  }
  const i = list.findIndex((x) => x.id === turn.id);
  if (i >= 0) {
    const prev = list[i];
    // Streamed text wins over a stale record that hasn't caught up yet.
    const text = turn.status === "running" && (prev.text || "").length > (turn.text || "").length ? prev.text : turn.text ?? prev.text;
    list[i] = { ...prev, ...turn, text, checks: turn.checks || prev.checks };
  } else list.push(turn);
  return cid;
}
function onTurn(t) {
  if (!t || !t.id) return;
  const cid = upsert(t);
  const done = ["done", "failed", "cancelled"].includes(t.status);
  if (cid === S.chatId) { redrawTurn(t.id); drawMeta(); drawSendState(); }
  drawTabs();
  if (done && t.role !== "user") {
    if (t.status === "failed" && cid === S.chatId) toast("The turn failed. See the chat for details.", { kind: "error" });
    setTimeout(() => drainQueue(cid), 50);
  }
}
function ensureAssistant(tid) {
  let [t, cid] = findTurn(tid);
  // A server that keys events on the user's turn id: stream into a companion reply.
  if (t && t.role === "user") { const uid = tid; tid = uid + ":reply"; const [r, rc] = findTurn(tid); if (r) return [r, rc]; t = null; cid = turnChat.get(uid); }
  if (t) return [t, cid];
  t = { id: tid, role: "assistant", chat_id: cid || S.chatId, text: "", status: "running", tools: [], todos: [] };
  upsert(t);
  return findTurn(tid);
}
function onDelta(d) {
  if (!d || !d.turn_id) return;
  const [t, cid] = ensureAssistant(d.turn_id);
  t.text = (t.text || "") + (d.text || "");
  if (t.status !== "running") t.status = "running";
  if (cid === S.chatId) redrawTurn(t.id);
}
function onTool(d) {
  const [t, cid] = ensureAssistant(d.turn_id);
  (t.tools = t.tools || []).push({ name: d.name, input_preview: d.input_preview });
  if (cid === S.chatId) redrawTurn(t.id);
}
function onTodo(d) {
  const [t, cid] = ensureAssistant(d.turn_id);
  t.todos = d.todos || [];
  if (cid === S.chatId) redrawTurn(t.id);
}
function onForm(d) {
  const [t, cid] = ensureAssistant(d.turn_id);
  t.form = d.form;
  if (cid === S.chatId) redrawTurn(t.id);
}
function onCheck(d) {
  if (!d.turn_id) return;
  const [t, cid] = ensureAssistant(d.turn_id);
  const checks = t.checks = t.checks || [];
  const i = checks.findIndex((c) => c.stage === d.stage);
  // The verifier is silent on pass: drop a passing full sweep instead of showing it.
  if (d.stage === "verifier" && d.status === "pass" && !d.directed) { if (i >= 0) checks.splice(i, 1); }
  else if (i >= 0) checks[i] = d; else checks.push(d);
  if (cid === S.chatId) redrawTurn(t.id);
}
function onFiles(d) {
  if (!d.turn_id) return;
  const [t, cid] = findTurn(d.turn_id);
  if (!t) return;
  t.changed_files = [...new Set([...(t.changed_files || []), ...(d.changed || [])])];
  if (d.version) t.version = d.version;
  if (cid === S.chatId) redrawTurn(t.id);
}
async function poll() {
  if (!S.chatId || !chatRunning()) return;
  await loadTurns(S.chatId);
  drawMessages(); drawMeta(); drawSendState(); drawTabs();
  if (!chatRunning()) drainQueue(S.chatId);
}

// ── Composer ─────────────────────────────────────────────────────────────
function composer() {
  const ta = h("textarea", { placeholder: "Describe a change, or ask for something new…", rows: 1, "aria-label": "Message", id: "chat-input" });
  els.ta = ta;
  ta.addEventListener("input", () => { autosize(); slashCheck(); });
  ta.addEventListener("keydown", (e) => {
    if (slashOpen && (e.key === "Enter" || e.key === "Tab")) { const first = document.querySelector(".menu .menu-item:not([disabled])"); if (first) { e.preventDefault(); first.click(); return; } }
    if (slashOpen && e.key === "Escape") { import("./core.js").then((m) => m.closeMenu()); slashOpen = false; return; }
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); onSend(); }
    if (e.key === "ArrowUp" && !ta.value) { const last = [...currentTurns()].reverse().find((t) => t.role === "user" && t.text); if (last) { e.preventDefault(); ta.value = last.text; autosize(); } }
    if (e.key === "Escape") ta.blur();
  });
  ta.addEventListener("paste", async (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (!files.length) return;
    e.preventDefault();
    const paths = await (await import("./workspace.js")).uploadFiles(files);
    bus.emit("attach", paths);
  });
  const fileIn = h("input", { type: "file", multiple: true, class: "hidden" });
  fileIn.addEventListener("change", async () => { const paths = await (await import("./workspace.js")).uploadFiles([...fileIn.files]); fileIn.value = ""; bus.emit("attach", paths); });
  els.chips = h("div", { class: "chips" });
  els.queue = h("div", { class: "queue" });
  els.send = h("button", { class: "send", title: "Send  (Enter)", "aria-label": "Send", onclick: () => onSend() }, icon("send"));
  els.pickers = h("div", { class: "row", style: { gap: "2px" } });
  const box = h("div", { class: "compose-box" }, ta,
    h("div", { class: "compose-row" },
      h("button", { class: "pick", title: "Attach files", onclick: () => fileIn.click() }, icon("paperclip")), fileIn,
      els.slashBtn = h("button", { class: "pick hidden", title: "Add a skill  (type /)", onclick: (e) => skillMenu(e.currentTarget, "") }, "/"),
      els.pickers, h("span", { class: "grow" }), els.send));
  box.addEventListener("dragover", (e) => { if ([...(e.dataTransfer?.types || [])].includes("Files")) { e.preventDefault(); box.classList.add("drag"); } });
  box.addEventListener("dragleave", () => box.classList.remove("drag"));
  box.addEventListener("drop", async (e) => { e.preventDefault(); box.classList.remove("drag"); const paths = await (await import("./workspace.js")).uploadFiles([...e.dataTransfer.files]); bus.emit("attach", paths); });
  const el = h("div", { class: "composer" }, els.queue, els.chips, box,
    h("div", { class: "hint-row" }, h("span", null, "Enter to send · Shift+Enter for a new line"), h("span", null, "Ctrl+K to focus")));
  setTimeout(() => { drawChips(); drawPickers(); drawSendState(); drawQueue(); loadSkills().then((l) => els.slashBtn && els.slashBtn.classList.toggle("hidden", !l.length)); });
  return el;
}
function autosize() { const ta = els.ta; if (!ta) return; ta.style.height = "auto"; ta.style.height = Math.min(220, ta.scrollHeight) + "px"; }
export function focusComposer() { if (els.ta) { els.ta.focus(); } }

function drawPickers() {
  if (!els.pickers) return;
  const c = currentChat();
  const eng = c?.engine || prefs.get("engine", S.engines?.default_engine || "claude_code");
  const local = c ? !!c.is_local : prefs.get("local", false);
  const effort = c?.effort ?? prefs.get("effort", "");
  const engines = S.engines?.engines || [{ id: "claude_code", available: true }, { id: "codex", available: true }, { id: "antigravity", available: true }];
  const engBtn = h("button", { class: "pick", title: "Engine", onclick: (e) => menu(e.currentTarget, [
    { heading: "Engine" },
    ...engines.map((x) => ({ label: ENGINE_LABEL[x.id] || x.id, hint: x.available === false ? "Not installed" : x.version || null, disabled: x.available === false, icon: x.id === eng ? "check" : null, checked: x.id === eng, onClick: () => setChatOpt({ engine: x.id }) })),
    "-",
    { label: "Route through the local model", icon: local ? "check" : null, checked: local, hint: S.engines?.local?.available === false ? "Local model not running" : S.engines?.local?.model || null, disabled: S.engines?.local?.available === false && !local, onClick: () => setChatOpt({ is_local: !local }) },
    { label: "Engines and integrations…", icon: "settings", onClick: () => import("./exporter.js").then((m) => m.settingsDialog()) },
  ]) }, icon("bolt"), (ENGINE_LABEL[eng] || eng) + (local ? " · local" : ""), icon("chevronDown"));
  const effBtn = h("button", { class: "pick", title: "Reasoning effort", onclick: (e) => menu(e.currentTarget, [{ heading: "Effort" },
    ...EFFORTS.map(([v, l]) => ({ label: l, icon: (effort || "") === v ? "check" : null, checked: (effort || "") === v, onClick: () => setChatOpt({ effort: v || null }) }))]) },
    (EFFORTS.find(([v]) => v === (effort || ""))?.[1] || "Effort").replace(" effort", ""), icon("chevronDown"));
  // Local mode as a visible switch, not only a menu item: it changes where the
  // turn runs (the local llama.cpp model via the proxy), so it should be seen.
  const localInfo = S.engines?.local || {};
  const localOff = localInfo.available === false && !local;
  const localSw = h("label", { class: "pick local-switch", title: localOff
      ? "Local model not available (llama.cpp or proxy disabled)"
      : `Run ${ENGINE_LABEL[eng] || eng} on the local model${localInfo.model ? " (" + localInfo.model + ")" : ""} through the telecode proxy` },
    h("span", { class: "switch" },
      h("input", { type: "checkbox", checked: local, disabled: localOff, "aria-label": "Local mode",
        onchange: (e) => setChatOpt({ is_local: e.currentTarget.checked }) }),
      h("span")),
    "Local");
  mount(els.pickers, engBtn, localSw, effBtn);
}
async function setChatOpt(patch) {
  if (patch.engine) prefs.set("engine", patch.engine);
  if ("is_local" in patch) prefs.set("local", patch.is_local);
  if ("effort" in patch) prefs.set("effort", patch.effort || "");
  const c = currentChat();
  if (c) {
    if (patch.engine && patch.engine !== c.engine && currentTurns().length) toast("Switching engines starts a fresh session. Earlier context won't carry over.");
    Object.assign(c, patch);
    tryApi("PATCH", `${P_(S.project.id)}/chats/${c.id}`, patch).catch(toastError);
  }
  drawPickers(); drawMeta();
}

function drawChips() {
  if (!els.chips) return;
  const chips = [];
  draft.attachments.forEach((p, i) => chips.push(h("span", { class: "chip" }, icon("paperclip"), h("span", { class: "nm" }, p.split("/").pop()),
    h("button", { class: "x", "aria-label": "Remove", onclick: () => { draft.attachments.splice(i, 1); drawChips(); } }, icon("x")))));
  draft.skills.forEach((n, i) => chips.push(h("span", { class: "chip", title: "Skill the agent reads this turn" }, icon("bolt"), h("span", { class: "nm" }, "/" + n),
    h("button", { class: "x", "aria-label": "Remove", onclick: () => { draft.skills.splice(i, 1); drawChips(); } }, icon("x")))));
  if (draft.selection) chips.push(h("span", { class: "chip sel", title: draft.selection.mentioned_element || "" }, icon("inspect"), h("span", { class: "nm" }, selLabel(draft.selection)),
    h("button", { class: "x", "aria-label": "Remove", onclick: () => { draft.selection = null; drawChips(); } }, icon("x"))));
  mount(els.chips, chips);
  els.chips.classList.toggle("hidden", !chips.length);
}
function drawSendState() {
  if (!els.send) return;
  const running = chatRunning();
  els.send.classList.toggle("stop", running && !els.ta?.value.trim());
  mount(els.send, icon(running && !els.ta?.value.trim() ? "stop" : "send"));
  els.send.title = running ? (els.ta?.value.trim() ? "Queue this message  (Enter)" : "Stop  (Ctrl+.)") : "Send  (Enter)";
}
function drawQueue() {
  if (!els.queue) return;
  const q = queues.get(S.chatId) || [];
  mount(els.queue, q.map((item, i) => h("div", { class: "queue-item" }, icon("history"), h("span", { class: "t" }, item.text),
    btn("Send now", { kind: "quiet", cls: "sm", title: "Stop the current turn and send this", onClick: async () => { q.splice(i, 1); q.unshift(item); drawQueue(); await stop(); } }),
    btn("", { kind: "quiet", icon: "x", cls: "sm", title: "Remove from queue", onClick: () => { q.splice(i, 1); drawQueue(); } }))));
}
if (typeof document !== "undefined") document.addEventListener("input", (e) => { if (e.target === els.ta) drawSendState(); });

async function onSend() {
  const text = els.ta.value.trim();
  if (!text) { if (chatRunning()) stop(); return; }
  const extra = {};
  if (draft.attachments.length) extra.attachments = draft.attachments.slice();
  if (draft.selection) extra.selection = draft.selection;
  let sendText = text;
  if (draft.skills.length) {
    // kind skills map onto the turn's kind_skill; any other skill is named in the
    // message so the agent reads it (design_read_skill) before starting.
    const kindSkill = draft.skills.map((n) => n.match(/^kinds\/([\w-]+)$/)).find(Boolean);
    if (kindSkill && (S.config?.kinds || []).includes(kindSkill[1])) extra.kind_skill = kindSkill[1];
    else if (draft.skills.includes("critique")) extra.kind_skill = "critique";
    const others = draft.skills.filter((n) => !(kindSkill && n === kindSkill[0]) && n !== "critique");
    if (others.length) sendText += `\n\n(Before you start, read these skills with design_read_skill: ${others.join(", ")}.)`;
  }
  els.ta.value = ""; autosize();
  draft.attachments = []; draft.selection = null; draft.skills = []; drawChips();
  await sendToChat(sendText, extra);
}
async function submit(cid, text, extra) {
  const c = S.chats.find((x) => x.id === cid);
  const body = { text, engine: c?.engine || prefs.get("engine", undefined), is_local: c ? !!c.is_local : prefs.get("local", undefined), effort: c?.effort ?? (prefs.get("effort", "") || undefined), ...extra };
  if (chatRunning(cid)) { enqueue(cid, text, body); return null; }
  // Optimistic user turn so the message shows instantly.
  const temp = { id: "local-" + Math.random().toString(36).slice(2), role: "user", chat_id: cid, text, status: "done", created_at: new Date().toISOString(), ...extra };
  turnsOf(cid).push(temp);
  if (cid === S.chatId) drawMessages();
  try {
    const r = await api("POST", `${P_(S.project.id)}/chats/${cid}/turns`, body, { feature: "turns" });
    const list = turnsOf(cid);
    const i = list.indexOf(temp);
    // Server returns {turn: assistant, user_turn}; the SSE copy may already be here.
    const userRec = r.user_turn || (r.turn && (r.turn.role === "user" || !r.turn.role) ? r.turn : null);
    if (userRec) {
      turnChat.set(userRec.id, cid);
      if (list.some((t) => t.id === userRec.id)) { if (i >= 0) list.splice(i, 1); }
      else if (i >= 0) list[i] = { ...temp, ...userRec, role: "user" };
    }
    if (r.turn && r.turn.role && r.turn.role !== "user") upsert({ ...r.turn, chat_id: cid });
    for (const extraTurn of r.turns || []) upsert({ ...extraTurn, chat_id: cid });
    if (r.assistant) upsert({ ...r.assistant, chat_id: cid });
    // Some servers only return the user turn; make sure a working indicator shows.
    if (!list.some((t) => t.role !== "user" && (t.status === "running" || t.status === "queued")) && !["done", "failed", "cancelled"].includes(r.turn?.status)) {
      list.push({ id: "pending-" + (r.turn?.id || temp.id), role: "assistant", chat_id: cid, text: "", status: r.turn?.status || "queued", tools: [], todos: [], engine: body.engine, _placeholder: true });
    }
    if (cid === S.chatId) { drawMessages(); drawMeta(); drawSendState(); }
    drawTabs();
    return r.turn;
  } catch (e) {
    const list = turnsOf(cid);
    list.splice(list.indexOf(temp), 1);
    if (e.status === 409) { enqueue(cid, text, body); return null; }
    if (cid === S.chatId) drawMessages();
    els.ta && !els.ta.value && (els.ta.value = text);
    toastError(e, "Couldn't send");
    return null;
  }
}
function enqueue(cid, text, body) {
  if (!queues.has(cid)) queues.set(cid, []);
  queues.get(cid).push({ text, body });
  drawQueue();
  toast("Queued — it sends when the current turn finishes.");
}
async function drainQueue(cid) {
  // Clear a placeholder once the real assistant turn arrives or finishes.
  const list = turnsOf(cid);
  for (let i = list.length - 1; i >= 0; i--) if (list[i]._placeholder && list.some((t) => !t._placeholder && t.role !== "user" && t.created_at >= (list[i].created_at || ""))) list.splice(i, 1);
  if (chatRunning(cid)) return;
  const q = queues.get(cid);
  if (!q || !q.length) { if (cid === S.chatId) { drawSendState(); drawMeta(); } return; }
  const item = q.shift();
  drawQueue();
  const { text, body } = item;
  const extra = { ...body }; delete extra.text;
  await submit(cid, text, extra);
}
export async function stop() {
  if (!S.chatId) return;
  try {
    await api("POST", `${P_(S.project.id)}/chats/${S.chatId}/stop`, {});
    for (const t of currentTurns()) if (t.status === "running" || t.status === "queued") { if (t._placeholder) t.status = "cancelled"; }
    toast("Stopped. Changes made so far are kept.");
    setTimeout(() => poll(), 300);
  } catch (e) { toastError(e, "Couldn't stop"); }
}
bus.on("chat-stop", stop);
bus.on("chat-new", () => newChat());


// ── "/" skill picker (GET /api/design/skills) ────────────────────────────
async function loadSkills() {
  if (skillsCache) return skillsCache;
  const j = await tryApi("GET", "/api/design/skills", undefined, { feature: "skills" }).catch(() => null);
  skillsCache = (j && j.skills) || [];
  return skillsCache;
}
let slashOpen = false;
async function slashCheck() {
  const ta = els.ta;
  const before = ta.value.slice(0, ta.selectionStart);
  const m = before.match(/(^|\s)\/([\w/-]*)$/);
  if (!m) { if (slashOpen) { (await import("./core.js")).closeMenu(); slashOpen = false; } return; }
  const list = await loadSkills();
  if (!list.length) return;
  skillMenu(els.slashBtn && !els.slashBtn.classList.contains("hidden") ? els.slashBtn : ta, m[2], m[0].length - m[1].length);
}
async function skillMenu(anchor, q, typedLen = 0) {
  const list = await loadSkills();
  const ql = q.toLowerCase();
  const hits = list.filter((k) => !ql || k.name.toLowerCase().includes(ql) || (k.summary || "").toLowerCase().includes(ql)).slice(0, 14);
  const pick = (k) => {
    if (typedLen) { const ta = els.ta; const pos = ta.selectionStart; ta.value = ta.value.slice(0, pos - typedLen) + ta.value.slice(pos); ta.selectionStart = ta.selectionEnd = pos - typedLen; }
    if (!draft.skills.includes(k.name)) draft.skills.push(k.name);
    slashOpen = false; drawChips(); focusComposer();
  };
  menu(anchor, hits.length ? [{ heading: "Skills — the agent reads it this turn" }, ...hits.map((k) => ({ label: "/" + k.name, hint: k.summary || (k.source === "user" ? "Your skill" : ""), icon: k.source === "user" ? "star" : "bolt", onClick: () => pick(k) }))]
    : [{ label: `No skill matches "/${q}"`, disabled: true }], { width: "320px" });
  slashOpen = true;
  if (anchor === els.ta || anchor === els.slashBtn) setTimeout(() => els.ta.focus(), 0);
}
