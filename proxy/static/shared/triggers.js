// Triggers UI kit (P3) — shared by Team Mode (/team) and Task Mode (/tasks).
// Loaded after /shared/manager.js; uses its h(), btn(), modal(), api(), liveEvents().
//
//   renderTriggerList(host, {filter, onOpen, names, emptyText, emptyAction})
//   renderTriggerDetail(host, id, {names, onChanged, onDeleted, onBack, openTask, openRun})
//   openTriggerEditor({trigger, defaults, names, onSaved})
//
// names = {jobs:[{id,title}], agents:[{id,name}], workspaces:[{session_id,data:{name}}]} — for labels / pickers.
// REST: /api/triggers (list/create/preview), /api/triggers/{id} (get/patch/delete), …/pause, …/resume,
// …/run-now, …/token, …/fires; live via /api/events?kinds=trigger. All server text goes into text nodes.
"use strict";

const TRIGGER_TARGETS = [["task", "Task prompt"], ["agent_prompt", "Agent prompt"], ["job", "Team job run"]];
const TRIGGER_ENGINES = [["claude_code", "Claude Code"], ["codex", "Codex"], ["antigravity", "Antigravity"]];
const TRIGGER_PERMS = [
  ["auto", "auto — risky actions denied, never prompts (default)"],
  ["acceptEdits", "acceptEdits — file edits allowed, other prompts denied"],
  ["dontAsk", "dontAsk — only pre-approved tools"],
  ["plan", "plan — read-only planning"],
  ["skip", "skip — --dangerously-skip-permissions (as interactive runs)"],
];
const FIRE_TONE = { running: "accent", completed: "ok", ok: "ok", skipped: "", failed: "err", cancelled: "err", interrupted: "warn" };
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const COMMON_TZ = ["UTC", "Europe/London", "Europe/Berlin", "America/New_York", "America/Los_Angeles", "Asia/Kolkata",
  "Asia/Dubai", "Asia/Singapore", "Asia/Tokyo", "Australia/Sydney"];
function browserTz() { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; } }

function fmtEverySec(sec) {
  sec = +sec || 0;
  if (!sec) return "";
  if (sec % 86400 === 0) return `every ${sec / 86400} day${sec === 86400 ? "" : "s"}`;
  if (sec % 3600 === 0) return `every ${sec / 3600} h`;
  if (sec % 60 === 0) return `every ${sec / 60} min`;
  return `every ${sec} s`;
}
function triggerWhen(t) {
  const s = t.schedule || {};
  if (s.cron) return `cron ${s.cron}${s.tz && s.tz !== "UTC" ? " · " + s.tz : ""}`;
  if (s.every_seconds) return fmtEverySec(s.every_seconds);
  if (s.at) return "once · " + fmtDateTime(s.at);
  return "";
}
function triggerEvents(t) {
  const e = t.events || {};
  return [e.webhook && e.webhook.enabled ? "webhook" : null, e.github && e.github.enabled ? "GitHub" : null,
    e.file && e.file.enabled ? "files" : null].filter(Boolean);
}
function triggerWhenAll(t) {
  const parts = [triggerWhen(t), ...triggerEvents(t)].filter(Boolean);
  return parts.length ? parts.join(" + ") : "by hand only";
}
function triggerTarget(t, names = {}) {
  const tg = t.target || {};
  if (tg.kind === "job") { const j = (names.jobs || []).find(x => x.id === tg.id); return "job · " + (j ? j.title : shortId(tg.id, 8)); }
  if (tg.kind === "agent_prompt") { const a = (names.agents || []).find(x => x.id === tg.agent_id); return "agent · " + (a ? a.name : shortId(tg.agent_id, 8)); }
  return "task · " + ((TRIGGER_ENGINES.find(e => e[0] === tg.engine) || [0, tg.engine || "Claude Code"])[1]);
}
function triggerIcon(t) { const tg = (t.target || {}).kind; return tg === "job" ? "workflow" : tg === "agent_prompt" ? "agent" : "terminal"; }
function webhookUrl(t, kind = "fire") { return `${location.origin}/api/triggers/${encodeURIComponent(t.id)}/${kind}`; }

// ── list ───────────────────────────────────────────────────────────────
async function renderTriggerList(host, o = {}) {
  if (!host) return [];
  if (!host.childNodes.length) mount(host, skeleton(3, "sk-row"));
  const q = new URLSearchParams(Object.entries(o.filter || {}).filter(([, v]) => v)).toString();
  let r;
  try { r = await api("/api/triggers" + (q ? "?" + q : "")); }
  catch (e) { mount(host, h("div", { style: { padding: "8px" } }, errorBox(e.message, () => renderTriggerList(host, o)))); return []; }
  const list = (r.triggers || []).filter(o.where || (() => true));
  if (!list.length) { mount(host, emptyState("clock", o.emptyTitle || "No triggers", o.emptyText || "A trigger runs a prompt or a job on a schedule, a webhook, a GitHub event or a file change.", o.emptyAction || null, true)); return list; }
  mount(host, h("div", { class: "trig-list" }, list.map(t => {
    const st = t.state || {};
    const on = o.activeId === t.id;
    return h("div", { class: "trig-row" + (on ? " on" : ""), tabindex: "0", role: "button", "data-tid": t.id,
      onclick: () => o.onOpen && o.onOpen(t), onkeydown: (e) => { if (e.key === "Enter" && o.onOpen) o.onOpen(t); } },
      h("span", { class: "dot " + (t.status === "active" ? "ok" : t.status === "paused" ? "warn" : ""), title: t.status }),
      icon(triggerIcon(t)),
      h("div", { class: "item-2" },
        h("span", { class: "nm" }, t.name, t.source === "heartbeat" ? h("span", { class: "tag", style: { marginLeft: "6px" }, title: "Compiled from HEARTBEAT.md" }, "HB") : null),
        h("span", { class: "sub" }, `${triggerWhenAll(t)} · ${triggerTarget(t, o.names)}`)),
      st.last_status ? statusPill(st.last_status, { tone: FIRE_TONE[st.last_status] ?? "" }) : null,
      h("span", { class: "faint", style: { fontSize: "11px", minWidth: "62px", textAlign: "right" }, title: st.next_fire_at ? "next fire " + fmtDateTime(st.next_fire_at) : "" },
        t.status === "active" && st.next_fire_at ? relTime(st.next_fire_at) : t.status !== "active" ? t.status : "—"));
  })));
  return list;
}

// ── detail ─────────────────────────────────────────────────────────────
// The detail on screen (one at a time); a single global-feed listener reloads it.
let trigDetailNow = null;
let trigFeedOn = false;
async function renderTriggerDetail(host, id, o = {}) {
  if (!host) return;
  if (!host.querySelector(".trig-detail")) mount(host, skeleton(6));
  let t, fires;
  try {
    const [d, f] = await Promise.all([api(`/api/triggers/${encodeURIComponent(id)}`), api(`/api/triggers/${encodeURIComponent(id)}/fires?limit=100`)]);
    t = d.trigger; fires = f.fires || [];
  } catch (e) { mount(host, errorBox(e.message, () => renderTriggerDetail(host, id, o))); return; }
  const reload = () => renderTriggerDetail(host, id, o);
  const st = t.state || {};
  const hb = t.source === "heartbeat";
  const act = async (path, label) => {
    try {
      const r = await api(`/api/triggers/${encodeURIComponent(id)}/${path}`, { method: "POST" });
      if (path === "run-now") toast(r.status === "fired" ? `Fired → ${r.task_id ? "task " + shortId(r.task_id) : "run " + shortId(r.run_id)}` : `Skipped: ${r.reason || "?"}`, { kind: r.status === "fired" ? "success" : "info" });
      else toast(label, { kind: "success" });
      if (o.onChanged) o.onChanged(); reload();
    } catch (e) { toast(e.message, { kind: "error" }); }
  };
  const del = async () => {
    const ok = await confirmDialog("Delete trigger?", h("p", { class: "muted" }, "Delete ", h("b", null, t.name), "? Its fire history goes with it; tasks and runs it started stay."), { confirm: "Delete", danger: true });
    if (!ok) return;
    try { await api(`/api/triggers/${encodeURIComponent(id)}`, { method: "DELETE" }); toast("Trigger deleted", { kind: "success" }); if (o.onDeleted) o.onDeleted(); }
    catch (e) { toast(e.message, { kind: "error" }); }
  };
  const tg = t.target || {};
  const head = h("div", { class: "trig-head" },
    o.onBack ? btn(null, { icon: "chevronRight", kind: "quiet", size: "sm", cls: "back", title: "Back to the list", onClick: o.onBack }) : null,
    h("div", { class: "grow" },
      h("div", { class: "row wrap" }, h("h2", null, t.name), statusPill(t.status, { tone: t.status === "active" ? "ok" : t.status === "paused" ? "warn" : "" }),
        hb ? h("span", { class: "pill violet", title: "Compiled from the agent's HEARTBEAT.md — edit it there" }, icon("heart"), "HEARTBEAT.md") : t.source === "routine" ? h("span", { class: "pill" }, "from routine") : null),
      h("div", { class: "faint", style: { fontSize: "12px", marginTop: "3px" } }, triggerWhenAll(t), " · ", triggerTarget(t, o.names), " · ", idChip(t.id, 8))),
    h("div", { class: "row wrap" },
      btn("Fire now", { icon: "bolt", kind: "primary", size: "sm", title: "Fire once now (ignores active hours and skip-if-empty; still skips if a fire is running)", onClick: () => act("run-now") }),
      t.status === "active" ? btn("Pause", { icon: "pause", kind: "ghost", size: "sm", onClick: () => act("pause", "Paused") })
        : btn("Resume", { icon: "play", kind: "ghost", size: "sm", onClick: () => act("resume", "Resumed") }),
      btn("Edit", { icon: "edit", kind: "ghost", size: "sm", disabled: hb, title: hb ? "Edit the entry in the agent's HEARTBEAT.md" : "Edit trigger",
        onClick: () => openTriggerEditor({ trigger: t, names: o.names, onSaved: () => { if (o.onChanged) o.onChanged(); reload(); } }) }),
      hb ? null : btn(null, { icon: "trash", kind: "danger", size: "sm", title: "Delete trigger", onClick: del })));
  const notes = [];
  if (st.paused_reason && t.status !== "active") notes.push(h("div", { class: "warn-box" }, icon("pause"), h("span", null, st.paused_reason)));
  if (hb && t.heartbeat_enabled === false) notes.push(h("div", { class: "info-box" }, icon("info"), h("span", null, "heartbeat.enabled is off in settings — HEARTBEAT.md triggers fire only when you press Fire now.")));
  if (st.last_error && st.last_status === "failed") notes.push(h("div", { class: "err-box" }, "Last error: ", st.last_error));
  const stats = h("div", { class: "stats" },
    statTile("clock", "Next fire", t.status === "active" && st.next_fire_at ? relTime(st.next_fire_at) : "—", st.next_fire_at ? fmtDateTime(st.next_fire_at) : null),
    statTile("history", "Last fire", st.last_fire_at ? relTime(st.last_fire_at) : "—", st.last_fire_at ? fmtDateTime(st.last_fire_at) : null),
    statTile("bolt", "Fires", fmtNum(st.total_fires || 0)),
    statTile("check", "OK / done", `${st.ok_fires || 0} / ${st.completed_fires || 0}`, "ok = replied HEARTBEAT_OK / NO_REPLY (no notification)"),
    statTile("alert", "Failed", fmtNum(st.failed_fires || 0), st.consecutive_failures ? `${st.consecutive_failures} in a row` : null),
    statTile("pause", "Skipped", fmtNum(st.skipped_fires || 0), st.last_skip_reason || null),
    statTile("coins", "Cost", fmtCost(st.cost_usd || 0)));
  const kv = (k, v) => v == null || v === "" ? null : h("div", { class: "trig-kv" }, h("span", null, k), h("span", null, v));
  const engine = (TRIGGER_ENGINES.find(e => e[0] === tg.engine) || [0, tg.engine || ""])[1];
  const what = h("div", { class: "trig-grid" },
    h("div", null, h("h4", null, "Runs"),
      kv("Target", triggerTarget(t, o.names)),
      tg.kind !== "job" ? kv("Engine", `${engine}${tg.model ? " · " + tg.model : ""}${tg.is_local ? " · local" : ""}`) : kv("Engine", tg.engine ? engine : "the job's steps"),
      kv("Model override", t.model_override || null),
      kv("Session", t.session === "fresh" ? "fresh — a throwaway copy per fire" : tg.kind === "job" ? "shared — the job's workspace" : "shared — one permanent session"),
      tg.kind !== "job" && t.session_id ? kv("Session id", t.session_id) : null,
      kv("Permissions", t.permission_mode === "skip" ? "skip (dangerously)" : `--permission-mode ${t.permission_mode} · prompts denied`),
      tg.kind !== "job" ? kv("Timeout", fmtMs((t.task_timeout_seconds || 0) * 1000)) : null,
      tg.prompt ? h("details", { class: "trig-prompt" }, h("summary", null, icon("chevronRight"), "Prompt"), h("pre", null, tg.prompt)) : null,
      t.pinned ? h("details", { class: "trig-prompt" }, h("summary", null, icon("chevronRight"), "Pinned constraints"), h("pre", null, t.pinned)) : null),
    h("div", null, h("h4", null, "When"),
      kv("Schedule", triggerWhen(t) || "none"),
      (t.upcoming || []).length ? kv("Upcoming", (t.upcoming || []).map(fmtDateTime).join(" · ")) : null,
      t.active_hours ? kv("Active hours", `${t.active_hours.start}–${t.active_hours.end} ${t.active_hours.tz} · ${t.active_hours.days.join(",")}`) : null,
      t.skip_if_empty ? kv("Skip if empty", t.skip_if_empty.heartbeat_section ? `HEARTBEAT.md § ${t.skip_if_empty.heartbeat_section}` : t.skip_if_empty.path) : null,
      kv("OK replies", t.ok_suppression ? (t.ok_tokens || []).join(", ") + " → recorded ok, not notified" : "off"),
      kv("Notify", t.notify ? "Telegram, on a notable reply" : "off"),
      kv("Catch-up", t.catch_up === "once" ? "fire once for missed slots" : "skip missed slots"),
      kv("Auto-pause", t.auto_pause_after_failures ? `after ${t.auto_pause_after_failures} failures in a row` : "never"),
      t.goal ? kv("Goal", [t.goal.check_command ? `\`${t.goal.check_command}\` exits 0` : null, t.goal.features_file ? `${t.goal.features_file} all passing` : null,
        t.goal.max_fires ? `max ${t.goal.max_fires} fires` : null, t.goal.max_cost_usd ? `max $${t.goal.max_cost_usd}` : null].filter(Boolean).join(" · ")) : null));
  const ev = t.events || {};
  const evBlocks = [];
  if (ev.webhook && ev.webhook.enabled) {
    const url = webhookUrl(t, "fire"), tok = ev.webhook.token || "";
    evBlocks.push(h("div", { class: "trig-ev" }, h("div", { class: "row" }, icon("webhook"), h("b", null, "Webhook"), h("span", { class: "grow" }),
      btn("Copy URL", { icon: "copy", kind: "ghost", size: "sm", onClick: () => copyText(url, "Webhook URL copied") }),
      btn("Copy token", { icon: "copy", kind: "ghost", size: "sm", onClick: () => copyText(tok, "Token copied") }),
      btn("Copy curl", { icon: "terminal", kind: "ghost", size: "sm", onClick: () => copyText(`curl -X POST "${url}" -H "Authorization: Bearer ${tok}" -H "Content-Type: application/json" -d '{"hello":"world"}'`, "curl command copied") }),
      btn("Rotate", { icon: "refresh", kind: "quiet", size: "sm", title: "New token — the old one stops working", onClick: async () => {
        if (!(await confirmDialog("Rotate the token?", "Callers using the current token get 401 until they switch.", { confirm: "Rotate" }))) return;
        try { await api(`/api/triggers/${encodeURIComponent(id)}/token`, { method: "POST" }); toast("Token rotated", { kind: "success" }); reload(); } catch (e) { toast(e.message, { kind: "error" }); }
      } })),
      h("code", { class: "trig-url" }, "POST ", url), h("div", { class: "field-hint" }, "Authorization: Bearer <token>. The body (JSON or text) reaches the agent wrapped as untrusted data.")));
  }
  if (ev.github && ev.github.enabled) {
    const g = ev.github, url = webhookUrl(t, "github");
    evBlocks.push(h("div", { class: "trig-ev" }, h("div", { class: "row" }, icon("code"), h("b", null, "GitHub"), h("span", { class: "grow" }),
      btn("Copy URL", { icon: "copy", kind: "ghost", size: "sm", onClick: () => copyText(url, "Payload URL copied") }),
      btn("Copy secret", { icon: "copy", kind: "ghost", size: "sm", onClick: () => copyText(g.secret || "", "Secret copied") })),
      h("code", { class: "trig-url" }, url),
      h("div", { class: "field-hint" }, "Content type application/json; the secret signs every delivery (X-Hub-Signature-256). Filters: ",
        [g.events.length ? "events " + g.events.join(",") : null, g.branches.length ? "branches " + g.branches.join(",") : null,
          g.authors.length ? "authors " + g.authors.join(",") : null, g.labels.length ? "labels " + g.labels.join(",") : null].filter(Boolean).join(" · ") || "none")));
  }
  if (ev.file && ev.file.enabled) {
    evBlocks.push(h("div", { class: "trig-ev" }, h("div", { class: "row" }, icon("folder"), h("b", null, "File watch")),
      h("div", { class: "field-hint" }, `${ev.file.glob} in ${ev.file.workspace_id || "the trigger's workspace"} · fires ${ev.file.debounce_seconds}s after the last change`)));
  }
  // history
  let filt = o._filt || "all";
  const histHost = h("div", { class: "trig-fires" });
  const seg = h("div", { class: "seg sm" });
  const FILTERS = [["all", "All"], ["ok", "OK"], ["suppressed", "Suppressed"], ["skipped", "Skipped"], ["failed", "Failed"]];
  const pick = (f) => f === "all" ? fires : f === "suppressed" ? fires.filter(x => (x.data || {}).suppressed)
    : f === "failed" ? fires.filter(x => ["failed", "cancelled", "interrupted"].includes(x.status)) : f === "ok" ? fires.filter(x => ["ok", "completed"].includes(x.status)) : fires.filter(x => x.status === f);
  const drawHist = () => {
    mount(seg, FILTERS.map(([k, l]) => h("button", { type: "button", class: filt === k ? "on" : "", onclick: () => { filt = k; o._filt = k; drawHist(); } }, l, " ", h("span", { class: "faint" }, pick(k).length))));
    const rows = pick(filt);
    if (!rows.length) { mount(histHost, emptyState("history", "No fires", filt === "all" ? "Fire it now, or wait for the schedule / an event." : "Nothing in this group.", null, true)); return; }
    mount(histHost, rows.map(f => {
      const d = f.data || {};
      const link = f.task_id ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); if (o.openTask) o.openTask(f.task_id); else copyText(f.task_id, "Task id copied"); } }, "task " + shortId(f.task_id, 6))
        : f.run_id ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); if (o.openRun) o.openRun(f.run_id, tg.id); } }, "run " + shortId(f.run_id, 6)) : null;
      return h("div", { class: "fire-row", title: d.excerpt || "" },
        h("span", { class: "faint mono", style: { minWidth: "32px" } }, "#" + f.seq),
        statusPill(f.status, { tone: FIRE_TONE[f.status] ?? "" }),
        d.suppressed ? h("span", { class: "pill", title: "The reply matched an OK token — no notification was sent" }, "suppressed") : null,
        h("span", { class: "pill" }, f.source || "?"),
        h("span", { class: "grow ellipsis fire-why" }, f.reason ? f.reason : d.excerpt ? d.excerpt.replace(/\s+/g, " ").slice(0, 160) : ""),
        d.count > 1 ? h("span", { class: "pill warn", title: d.first_at ? "first at " + fmtDateTime(d.first_at) : "" }, "×" + d.count) : null,
        f.cost_usd ? h("span", { class: "faint mono" }, fmtCost(f.cost_usd)) : null,
        link, h("span", { class: "faint", style: { fontSize: "11px", minWidth: "70px", textAlign: "right" }, title: fmtDateTime(f.fired_at) }, relTime(f.fired_at)));
    }));
  };
  drawHist();
  mount(host, h("div", { class: "trig-detail col", style: { gap: "14px" } }, head, notes, stats, what,
    evBlocks.length ? h("div", { class: "col", style: { gap: "8px" } }, h("h4", { class: "trig-h4" }, "Events"), evBlocks) : null,
    h("div", { class: "col", style: { gap: "8px" } }, h("div", { class: "row" }, h("h4", { class: "trig-h4 grow" }, "History"), seg,
      btn(null, { icon: "refresh", kind: "quiet", size: "sm", title: "Refresh", onClick: reload })), histHost)));
  // live refresh while this detail is on screen
  trigDetailNow = { host, id, reload: debounced(() => { if (host.isConnected && host.querySelector(".trig-detail")) reload(); }, 600) };
  if (!trigFeedOn) {
    trigFeedOn = true;
    globalFeed(["trigger.fire", "trigger.update"], (type, d) => {
      const cur = trigDetailNow;
      if (cur && d && (d.trigger_id === cur.id || d.id === cur.id) && cur.host.isConnected) cur.reload();
    });
  }
}

// ── editor ─────────────────────────────────────────────────────────────
function sw(label, checked, title) {
  const i = h("input", { type: "checkbox", role: "switch" }); i.checked = !!checked;
  const l = h("label", { class: "switch-field" + (checked ? " on" : ""), title: title || null }, h("span", { class: "switch" }, i, h("span")), h("span", null, label));
  i.addEventListener("change", () => l.classList.toggle("on", i.checked));
  return { el: l, input: i, get: () => i.checked };
}
function selectOf(pairs, value, attrs = {}) {
  return h("select", { class: "select", ...attrs }, pairs.map(([v, l]) => h("option", { value: v, selected: v === value || null }, l)));
}
function fld(label, input, hint, extra) {
  return h("label", { class: "field" }, h("span", { class: "field-label" }, label, extra || null), input, hint ? h("span", { class: "field-hint" }, hint) : null);
}
function openTriggerEditor(o = {}) {
  const t = o.trigger || null;
  const d = o.defaults || {};
  const names = o.names || {};
  const tg = { kind: "task", engine: "claude_code", ...(d.target || {}), ...((t && t.target) || {}) };
  const s = (t && t.schedule) || d.schedule || {};
  const ev = (t && t.events) || {};
  const tz0 = s.tz || browserTz();
  const tzList = "tzlist-" + Math.random().toString(36).slice(2, 8);
  const tzInput = (v) => h("input", { class: "input mono", value: v || tz0, list: tzList, placeholder: "IANA zone, e.g. Europe/London", spellcheck: "false" });

  const name = h("input", { class: "input", value: (t && t.name) || d.name || "", placeholder: "e.g. Nightly digest", autofocus: true });
  const desc = h("input", { class: "input", value: (t && t.description) || "", placeholder: "(optional)" });
  // target
  const kindSel = selectOf(TRIGGER_TARGETS, tg.kind);
  const prompt = h("textarea", { class: "input", rows: "5", placeholder: "The standing directive — what each fire should do." }, tg.prompt || "");
  const engSel = selectOf(TRIGGER_ENGINES, tg.engine || "claude_code");
  const jobEng = selectOf([["", "the job's own engines"], ...TRIGGER_ENGINES], tg.kind === "job" ? (tg.engine || "") : "");
  const model = h("input", { class: "input", value: tg.model || "", placeholder: "default model" });
  const local = sw("Local", tg.is_local, "Run against the local llama.cpp model through the proxy");
  const agentSel = selectOf([["", "— agent —"], ...(names.agents || []).map(a => [a.id, a.name])], tg.agent_id || "");
  const wsPairs = [["", "(none)"], ...(names.workspaces || []).map(w => [w.session_id, (w.data && w.data.name) || w.session_id])];
  const wsSel = selectOf(wsPairs, tg.workspace_id || "");
  const jobSel = selectOf([["", "— job —"], ...(names.jobs || []).map(j => [j.id, j.title])], tg.kind === "job" ? tg.id : "");
  const targetBox = h("div");
  const drawTarget = () => {
    const k = kindSel.value;
    mount(targetBox,
      k === "job" ? h("div", { class: "field-row" }, fld("Job", jobSel, "Its pipeline runs; the trigger's context and pinned constraints reach every step."), fld("Engine override", jobEng))
        : h("div", null,
          k === "agent_prompt" ? h("div", { class: "field-row" }, fld("Agent", agentSel, "Its SOUL / AGENT / MEMORY are staged into the run."), fld("Workspace", wsSel, "Blank = its own session.")) : null,
          fld("Prompt", prompt, null, h("span", { class: "req" }, "*")),
          h("div", { class: "field" }, h("span", { class: "field-label" }, "Engine"), h("div", { class: "engine-row" }, engSel, model, local.el))),
      k === "job" ? fld("Model", model, "Optional run-level model override.") : null);
  };
  kindSel.addEventListener("change", drawTarget);
  drawTarget();
  // schedule
  const smode0 = s.cron ? "cron" : s.every_seconds ? "every" : s.at ? "at" : (t ? "none" : "every");
  const smode = selectOf([["none", "No schedule (events / by hand)"], ["cron", "Cron"], ["every", "Interval"], ["at", "Once at"]], smode0);
  const cron = h("input", { class: "input mono", value: s.cron || "0 9 * * 1-5", placeholder: "m h dom mon dow", spellcheck: "false" });
  const cronTz = tzInput(s.tz);
  const every = s.every_seconds || 3600;
  const unit0 = every % 86400 === 0 ? 86400 : every % 3600 === 0 ? 3600 : 60;
  const everyN = h("input", { class: "input", type: "number", min: "1", value: String(every / unit0) });
  const everyU = selectOf([["60", "minutes"], ["3600", "hours"], ["86400", "days"]], String(unit0));
  const atLocal = (() => { if (!s.at) return ""; const dt = new Date(s.at); const p = (n) => String(n).padStart(2, "0"); return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}T${p(dt.getHours())}:${p(dt.getMinutes())}`; })();
  const at = h("input", { class: "input", type: "datetime-local", value: atLocal });
  const preview = h("div", { class: "field-hint trig-preview" });
  const schedBox = h("div");
  const readSchedule = () => {
    const m = smode.value;
    if (m === "cron") return { cron: cron.value.trim(), tz: cronTz.value.trim() || "UTC" };
    if (m === "every") return { every_seconds: Math.round((+everyN.value || 0) * (+everyU.value)) };
    if (m === "at") return at.value ? { at: new Date(at.value).toISOString() } : { at: "" };
    return null;
  };
  // active hours
  const ah = (t && t.active_hours) || null;
  const ahOn = sw("Only within active hours", !!ah);
  const ahStart = h("input", { class: "input", type: "time", value: (ah && ah.start) || "09:00" });
  const ahEnd = h("input", { class: "input", type: "time", value: (ah && ah.end) || "18:00" });
  const ahTz = tzInput(ah && ah.tz);
  const dayBoxes = DAYS.map(dd => { const i = h("input", { type: "checkbox" }); i.checked = !ah || ah.days.includes(dd); return { dd, i, el: h("label", { class: "check" }, i, dd) }; });
  const ahBox = h("div", { class: "col" }, h("div", { class: "field-row three" }, fld("From", ahStart), fld("To", ahEnd, "Before From = across midnight"), fld("Zone", ahTz)),
    h("div", { class: "row wrap" }, dayBoxes.map(x => x.el)));
  const syncAh = () => ahBox.classList.toggle("hidden", !ahOn.get());
  ahOn.input.addEventListener("change", syncAh); syncAh();
  const readAh = () => ahOn.get() ? { start: ahStart.value, end: ahEnd.value, tz: ahTz.value.trim() || "UTC", days: dayBoxes.filter(x => x.i.checked).map(x => x.dd) } : null;
  const doPreview = debounced(async () => {
    const sch = readSchedule();
    if (!sch) { mount(preview, "No schedule — fires on events or by hand only."); return; }
    try {
      const r = await api("/api/triggers/preview", jsonOpts("POST", { schedule: sch, active_hours: readAh(), count: 4 }));
      mount(preview, (r.upcoming || []).length ? ["Next: ", (r.upcoming || []).map(fmtDateTime).join(" · ")] : "No upcoming fire.");
      preview.classList.remove("bad");
    } catch (e) { mount(preview, e.message); preview.classList.add("bad"); }
  }, 350);
  const drawSched = () => {
    const m = smode.value;
    mount(schedBox, m === "cron" ? h("div", { class: "field-row" }, fld("Cron", cron, "minute hour day-of-month month day-of-week"), fld("Time zone", cronTz))
      : m === "every" ? h("div", { class: "field-row" }, fld("Every", everyN, "At least 1 minute"), fld("Unit", everyU))
      : m === "at" ? h("div", { class: "field-row" }, fld("At", at, "Your browser's local time; fires once, then disables itself"), h("span"))
      : h("div", { class: "field-hint" }, "Enable a webhook, GitHub or file event below, or fire it by hand."));
    doPreview();
  };
  [smode, cron, cronTz, everyN, everyU, at, ahStart, ahEnd, ahTz].forEach(x => x.addEventListener("input", () => { if (x === smode) drawSched(); else doPreview(); }));
  smode.addEventListener("change", drawSched); ahOn.input.addEventListener("change", doPreview); dayBoxes.forEach(x => x.i.addEventListener("change", doPreview));
  drawSched();
  // events
  const wh = sw("Webhook", ev.webhook && ev.webhook.enabled, "POST /api/triggers/{id}/fire with a bearer token");
  const gh = sw("GitHub", ev.github && ev.github.enabled, "GitHub repository webhook (HMAC-signed)");
  const ghSecret = h("input", { class: "input mono", placeholder: t && ev.github && ev.github.secret ? "(unchanged — type to replace)" : "shared secret, 8+ characters", value: "" });
  const csv = (a) => (a || []).join(", ");
  const ghEvents = h("input", { class: "input mono", value: csv(ev.github && ev.github.events), placeholder: "pull_request.opened, push" });
  const ghBranches = h("input", { class: "input mono", value: csv(ev.github && ev.github.branches), placeholder: "main" });
  const ghAuthors = h("input", { class: "input mono", value: csv(ev.github && ev.github.authors), placeholder: "any" });
  const ghLabels = h("input", { class: "input mono", value: csv(ev.github && ev.github.labels), placeholder: "any" });
  const fw = sw("File watch", ev.file && ev.file.enabled, "Fire after files matching a glob change");
  const fwGlob = h("input", { class: "input mono", value: (ev.file && ev.file.glob) || "", placeholder: "inbox/*.md" });
  const fwWs = selectOf([["", "the trigger's workspace"], ...wsPairs.slice(1)], (ev.file && ev.file.workspace_id) || "");
  const fwDeb = h("input", { class: "input", type: "number", min: "2", value: String((ev.file && ev.file.debounce_seconds) || 10) });
  const ghBox = h("div", { class: "col" }, h("div", { class: "field-row" }, fld("Secret", ghSecret), fld("Events", ghEvents, "event or event.action; blank = any")),
    h("div", { class: "field-row three" }, fld("Branches", ghBranches), fld("Authors", ghAuthors), fld("Labels", ghLabels)));
  const fwBox = h("div", { class: "field-row three" }, fld("Glob", fwGlob), fld("In", fwWs), fld("Debounce (s)", fwDeb));
  const syncEv = () => { ghBox.classList.toggle("hidden", !gh.get()); fwBox.classList.toggle("hidden", !fw.get()); };
  [gh, fw].forEach(x => x.input.addEventListener("change", syncEv)); syncEv();
  // session & safety
  const sess = selectOf([["shared", "Shared — one conversation continues across fires"], ["fresh", "Fresh — a new throwaway session every fire"]], (t && t.session) || d.session || (tg.kind === "agent_prompt" ? "fresh" : "shared"));
  const perm = selectOf(TRIGGER_PERMS, (t && t.permission_mode) || "auto");
  const timeout = h("input", { class: "input", type: "number", min: "30", value: String((t && t.task_timeout_seconds) || 1800) });
  const cheap = h("input", { class: "input", value: (t && t.model_override) || "", placeholder: "e.g. haiku — overrides the target's model" });
  const preface = sw("Recurring preface", !t || t.preface !== false, "Frame each fire as one cycle of an ongoing assignment");
  const outputsOnly = sw("Outputs only", t && t.outputs_only, "Ask for just this fire's deliverable");
  const pinned = h("textarea", { class: "input", rows: "3", placeholder: "Constraints appended at the very end of every fire's prompt (survive long conversations)" }, (t && t.pinned) || "");
  // conditions
  const skip = (t && t.skip_if_empty) || {};
  const skipPath = h("input", { class: "input mono", value: skip.path || "", placeholder: "e.g. inbox.md — skip while missing or empty" });
  const skipSec = h("input", { class: "input", value: skip.heartbeat_section || "", placeholder: "HEARTBEAT.md heading, e.g. Tasks" });
  const okSup = sw("Suppress OK replies", !t || t.ok_suppression !== false, "A reply of HEARTBEAT_OK / NO_REPLY is recorded as ok and not notified");
  const okTok = h("input", { class: "input mono", value: csv((t && t.ok_tokens) || ["HEARTBEAT_OK", "NO_REPLY"]) });
  const notify = sw("Notify on Telegram", t && t.notify, "Post notable replies to the General topic");
  const catchUp = selectOf([["skip", "Skip missed slots"], ["once", "Fire once for missed slots"]], (t && t.catch_up) || "skip");
  // goal & limits
  const g = (t && t.goal) || {};
  const gCmd = h("input", { class: "input mono", value: g.check_command || "", placeholder: "e.g. npm test — exit 0 = goal met" });
  const gFeat = h("input", { class: "input mono", value: g.features_file || "", placeholder: "features.json — all passing = goal met" });
  const gMax = h("input", { class: "input", type: "number", min: "0", value: g.max_fires || "", placeholder: "no limit" });
  const gCost = h("input", { class: "input", type: "number", min: "0", step: "0.01", value: g.max_cost_usd || "", placeholder: "no limit" });
  const autoPause = h("input", { class: "input", type: "number", min: "0", value: String(t ? (t.auto_pause_after_failures ?? 3) : 3) });

  const sec = (ttl, ic, ...kids) => h("section", { class: "trig-sec" }, h("h4", null, icon(ic), ttl), ...kids);
  const body = h("div", { class: "trig-form" },
    h("datalist", { id: tzList }, [browserTz(), ...COMMON_TZ].filter((v, i, a) => a.indexOf(v) === i).map(z => h("option", { value: z }))),
    sec("Basics", "edit", h("div", { class: "field-row" }, fld("Name", name, null, h("span", { class: "req" }, "*")), fld("Description", desc))),
    sec("What it runs", "target", fld("Target", kindSel), targetBox),
    sec("When", "clock", fld("Schedule", smode), schedBox, preview),
    sec("Events", "webhook", h("div", { class: "row wrap" }, wh.el, gh.el, fw.el), ghBox, fwBox,
      h("div", { class: "field-hint" }, "Webhook, GitHub and file payloads always reach the agent wrapped as untrusted data. URLs and the token are shown after saving.")),
    sec("Session & safety", "cpu", h("div", { class: "field-row" }, fld("Session", sess), fld("Claude permissions", perm, "Never skipped unless you pick skip. auto needs a model with auto-mode support (sonnet / opus); with haiku the CLI falls back to default, so edits and commands are denied.")),
      h("div", { class: "field-row" }, fld("Cheap model override", cheap), fld("Timeout per fire (s)", timeout)),
      h("div", { class: "row wrap" }, preface.el, outputsOnly.el), fld("Pinned constraints", pinned)),
    sec("Conditions", "flag", h("div", { class: "row wrap" }, ahOn.el), ahBox,
      h("div", { class: "field-row" }, fld("Skip if empty — file", skipPath), fld("…or HEARTBEAT.md section", skipSec)),
      h("div", { class: "row wrap" }, okSup.el, notify.el), h("div", { class: "field-row" }, fld("OK tokens", okTok), fld("Catch-up", catchUp))),
    sec("Goal & limits", "target", h("div", { class: "field-row" }, fld("Goal check command", gCmd, "Run in the workspace after each fire"), fld("Goal features file", gFeat)),
      h("div", { class: "field-row three" }, fld("Max fires", gMax), fld("Max cost ($)", gCost), fld("Auto-pause after failures", autoPause, "0 = never")),
      h("div", { class: "field-hint" }, "A met goal or limit pauses the trigger and sends a notice.")));

  const list = (i) => i.value.split(",").map(x => x.trim()).filter(Boolean);
  const collect = () => {
    const k = kindSel.value;
    const target = k === "job" ? { kind: "job", id: jobSel.value, engine: jobEng.value || "", model: model.value.trim() }
      : { kind: k, prompt: prompt.value, engine: engSel.value, model: model.value.trim(), is_local: local.get(),
        ...(k === "agent_prompt" ? { agent_id: agentSel.value, workspace_id: wsSel.value || null } : {}) };
    const github = { enabled: gh.get(), events: list(ghEvents), branches: list(ghBranches), authors: list(ghAuthors), labels: list(ghLabels) };
    if (ghSecret.value.trim()) github.secret = ghSecret.value.trim();
    const goal = { check_command: gCmd.value.trim(), features_file: gFeat.value.trim(), max_fires: gMax.value ? +gMax.value : 0, max_cost_usd: gCost.value ? +gCost.value : null };
    return {
      name: name.value.trim(), description: desc.value.trim(), target, schedule: readSchedule(),
      events: { webhook: { enabled: wh.get() }, github, file: { enabled: fw.get(), glob: fwGlob.value.trim(), workspace_id: fwWs.value || null, debounce_seconds: +fwDeb.value || 10 } },
      session: sess.value, permission_mode: perm.value, task_timeout_seconds: +timeout.value || 1800, model_override: cheap.value.trim(),
      preface: preface.get(), outputs_only: outputsOnly.get(), pinned: pinned.value,
      active_hours: readAh(), skip_if_empty: (skipPath.value.trim() || skipSec.value.trim()) ? { path: skipPath.value.trim(), heartbeat_section: skipSec.value.trim() } : null,
      ok_suppression: okSup.get(), ok_tokens: list(okTok), notify: notify.get(), catch_up: catchUp.value,
      goal: (goal.check_command || goal.features_file || goal.max_fires || goal.max_cost_usd) ? goal : null,
      auto_pause_after_failures: +autoPause.value || 0,
    };
  };
  return modal({ title: t ? "Edit trigger" : "New trigger", subtitle: "Schedules, webhooks, GitHub and file events — one scheduler for all of them", cls: "wide", width: "860px", body,
    actions: [{ label: "Cancel", kind: "ghost" }, { label: t ? "Save trigger" : "Create trigger", kind: "primary", icon: "save", id: "trigger-save", onClick: async () => {
      const payload = collect();
      if (!fieldError(name, payload.name ? "" : "Name is required")) return false;
      if (payload.target.kind !== "job" && !payload.target.prompt.trim()) { fieldError(prompt, "Prompt is required"); return false; }
      if (payload.target.kind === "job" && !payload.target.id) { fieldError(jobSel, "Pick a job"); return false; }
      if (payload.target.kind === "agent_prompt" && !payload.target.agent_id) { fieldError(agentSel, "Pick an agent"); return false; }
      try {
        const r = t ? await api(`/api/triggers/${encodeURIComponent(t.id)}`, jsonOpts("PATCH", payload)) : await api("/api/triggers", jsonOpts("POST", payload));
        toast(t ? "Trigger saved" : "Trigger created", { kind: "success" });
        if (o.onSaved) o.onSaved(r.trigger);
      } catch (e) { toast(e.message, { kind: "error" }); return false; }
    } }] });
}
