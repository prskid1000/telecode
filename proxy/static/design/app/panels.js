// TeleDesign — side panels other than chat: Comments, Versions, Review,
// Tweaks, Inspect.
import {
  h, icon, btn, mount, clear, api, tryApi, toast, toastError, bus, P_, encPath, relTime, emptyState, skeleton, modal,
  confirmDialog, promptDialog, menu, features, isHtml, prefs,
} from "./core.js";
import { S, loadComments, loadVersions, loadAssets, loadFiles, previewUrl, versionFiles } from "./state.js";
import { post, framesFor } from "./bridge.js";
import { live, toggleTweaks, queueTweak, sendComments, highlight, writeBack, createComment, reloadFile } from "./interact.js";

export function render(panel, host) {
  const f = { comments: renderComments, versions: renderVersions, review: renderReview, tweaks: renderTweaks, inspect: renderInspect }[panel];
  return f ? f(host) : null;
}
const missing = (host, ic, what) => mount(host, h("div", { class: "pane" }, h("div", { class: "pane-scroll" }, emptyState(ic, `${what} aren't available yet`, "This server doesn't expose that API. The control comes back once it does."))));

// ── Comments ─────────────────────────────────────────────────────────────
function renderComments(host) {
  if (features.comments === false) return missing(host, "comment", "Comments");
  let filter = prefs.get("cmtFilter", "open");
  const picked = new Set();
  const list = h("div", { class: "pane-scroll" });
  const foot = h("div", { class: "pane-foot" });
  const seg = h("div", { class: "seg" });
  const drawSeg = () => mount(seg, [["open", "Open"], ["sent", "Sent"], ["resolved", "Resolved"], ["all", "All"]].map(([v, l]) =>
    h("button", { class: filter === v ? "on" : "", onclick: () => { filter = v; prefs.set("cmtFilter", v); drawSeg(); draw(); } }, l, v !== "all" ? h("span", { class: "faint", style: { fontSize: "11px" } }, S.comments.filter((c) => (c.status || "open") === v).length) : null)));
  drawSeg();
  mount(host, h("div", { class: "pane" },
    h("div", { class: "pane-head" }, h("h3", null, "Comments"),
      btn("Add", { icon: "plus", cls: "sm", title: "Comment mode  (C)", onClick: async () => { (await import("./workspace.js")).setMode("comment"); if (S.view === "code") (await import("./workspace.js")).setView("preview"); toast("Click anything in the design to comment on it."); } })),
    h("div", { class: "filters" }, seg), list, foot));

  function draw() {
    const items = S.comments.filter((c) => filter === "all" || (c.status || "open") === filter);
    if (!S.comments.length) { mount(list, emptyState("comment", "No comments yet", "Switch to comment mode (C) and click anything in the design. Comments go to the agent as scoped edits.")); drawFoot(); return; }
    if (!items.length) { mount(list, emptyState("checkCircle", filter === "open" ? "No open comments" : "Nothing here", filter === "open" ? "Everything has been sent or resolved." : "")); drawFoot(); return; }
    const byFile = new Map();
    items.forEach((c) => { const k = c.file || c.board_id || "Canvas"; if (!byFile.has(k)) byFile.set(k, []); byFile.get(k).push(c); });
    const numbers = new Map(S.comments.map((c, i) => [c.id, i + 1]));
    clear(list);
    for (const [file, cs] of byFile) {
      list.appendChild(h("div", { class: "file-group" }, icon("file"), file, h("span", { class: "grow" }), h("span", null, cs.length)));
      for (const c of cs) {
        const st = c.status || "open";
        const a = c.anchor || {};
        const row = h("div", { class: "cmt" + (st === "resolved" ? " resolved" : ""), onmouseenter: () => highlight(file, a), onmouseleave: () => highlight(file, null) },
          st === "open" ? h("input", { type: "checkbox", checked: picked.has(c.id), "aria-label": "Select comment", onchange: (e) => { e.target.checked ? picked.add(c.id) : picked.delete(c.id); drawFoot(); } }) : null,
          h("span", { class: "pinno" }, numbers.get(c.id)),
          h("div", { class: "body" },
            h("div", { class: "top" }, h("b", null, c.author || "You"), h("span", null, relTime(c.created_at)),
              st === "sent" ? h("span", { class: "pill accent" }, "Sent") : st === "resolved" ? h("span", { class: "pill ok" }, "Resolved") : null,
              c.slide_index ? h("span", { class: "pill" }, "Slide " + c.slide_index) : null,
              h("span", { class: "grow" }),
              h("span", { class: "acts" },
                btn("", { kind: "quiet", icon: "eye", cls: "sm", title: "Show in the design", onClick: () => goTo(c) }),
                st !== "resolved" ? btn("", { kind: "quiet", icon: "check", cls: "sm", title: "Resolve", onClick: () => setStatus(c, "resolved") }) : btn("", { kind: "quiet", icon: "restore", cls: "sm", title: "Reopen", onClick: () => setStatus(c, "open") }),
                btn("", { kind: "quiet", icon: "more", cls: "sm", title: "More", onClick: (e) => menu(e.currentTarget, [
                  st === "open" ? { label: "Send to agent", icon: "send", onClick: () => sendComments([c.id]) } : null,
                  { label: "Edit", icon: "edit", onClick: async () => { const n = await promptDialog("Edit comment", { value: c.note, multiline: true }); if (n) patch(c, { note: n }); } },
                  { label: "Delete", icon: "trash", danger: true, onClick: async () => { await api("DELETE", `${P_(S.project.id)}/comments/${c.id}`).catch(toastError); loadComments(); } },
                ], { align: "right" }) }))),
            h("div", { class: "note" }, c.note),
            a.td_id || a.selector || a.source_loc ? h("div", { class: "anchor", title: [a.td_id && "#" + a.td_id, a.selector, a.source_loc].filter(Boolean).join("  ") }, a.td_id ? "#" + a.td_id : a.selector || a.source_loc) : null));
        list.appendChild(row);
      }
    }
    drawFoot();
  }
  function drawFoot() {
    const open = S.comments.filter((c) => (c.status || "open") === "open");
    if (!open.length) { foot.classList.add("hidden"); return; }
    foot.classList.remove("hidden");
    const ids = picked.size ? [...picked] : open.map((c) => c.id);
    const others = open.some((c) => c.author && c.author !== prefs.get("author", "You"));
    mount(foot, h("div", { class: "row" }, h("span", { class: "muted grow", style: { fontSize: "12px" } }, picked.size ? `${picked.size} selected` : `${open.length} open`),
      btn(picked.size ? `Send ${picked.size} to agent` : others ? "Address all from teammates" : "Send all to agent", { kind: "primary", icon: "send", cls: "sm", onClick: async () => { await sendComments(ids); picked.clear(); } })));
  }
  async function setStatus(c, status) { await patch(c, { status }); }
  async function patch(c, body) {
    try { await api("PATCH", `${P_(S.project.id)}/comments/${c.id}`, body); await loadComments(); }
    catch (e) { toastError(e); }
  }
  async function goTo(c) {
    const file = c.file || c.board_id;
    const ws = await import("./workspace.js");
    if (file && isHtml(file)) ws.openFile(file, { view: "preview" });
    setTimeout(() => { highlight(file, c.anchor); if (c.slide_index) for (const f of framesFor(file)) post(f, { type: "td:slide", action: "go", index: c.slide_index }); }, 600);
  }
  draw();
  const off = bus.on("comments", () => { drawSeg(); draw(); });
  loadComments();
  return off;
}

// ── Versions ─────────────────────────────────────────────────────────────
function renderVersions(host) {
  if (features.versions === false) return missing(host, "history", "Versions");
  let sel = null;
  const list = h("div", { class: "pane-scroll" }, skeleton(5, "sk-block"));
  const detail = h("div", { class: "ver-detail hidden" });
  mount(host, h("div", { class: "pane" },
    h("div", { class: "pane-head" }, h("h3", null, "Versions"),
      btn("Compare", { icon: "split", cls: "sm", onClick: () => compareDialog(sel ? sel.v : null) })),
    list, detail));
  const ORIGIN = { agent: "Agent", user: "You", tweak: "Tweak", restore: "Restore" };
  function draw() {
    if (!S.versions.length) { mount(list, emptyState("history", "No versions yet", "A version is saved after every agent turn, code edit and tweak.")); return; }
    mount(list, S.versions.map((v) => h("div", { class: "ver" + (sel && sel.v === v.v ? " on" : ""), onclick: () => select(v) },
      h("div", { class: "rail-line" }, h("span", { class: "node " + (v.origin || "") })),
      h("div", { class: "info" },
        h("div", { class: "t" }, h("b", null, "v" + v.v), h("span", { class: "pill" + (v.origin === "agent" ? " accent" : v.origin === "user" ? " ok" : v.origin === "tweak" ? " violet" : v.origin === "restore" ? " warn" : "") }, ORIGIN[v.origin] || v.origin || "Version"),
          v.v === S.versions[0].v ? h("span", { class: "pill" }, "Current") : null),
        v.prompt ? h("div", { class: "p", title: v.prompt }, v.prompt) : null,
        h("div", { class: "m" }, relTime(v.at), " · ", `${v.file_count ?? Object.keys(v.files || {}).length} files`, v.changed?.length ? ` · ${v.changed.length} changed` : "")))));
  }
  function changes(v) {
    const parent = S.versions.find((x) => x.v === v.parent) || S.versions.find((x) => x.v < v.v);
    const a = parent?.files || {}, b = v.files || {};
    const out = [];
    for (const p of Object.keys(b)) if (!(p in a)) out.push([p, "a"]); else if (a[p] !== b[p]) out.push([p, "m"]);
    for (const p of Object.keys(a)) if (!(p in b)) out.push([p, "d"]);
    return { parent, out: out.sort((x, y) => x[0].localeCompare(y[0])) };
  }
  async function select(v) {
    sel = v; draw();
    const parentV = v.parent ?? S.versions.find((x) => x.v < v.v)?.v;
    await Promise.all([versionFiles(v.v), versionFiles(parentV)]);
    if (sel !== v) return;
    const { parent, out } = changes(v);
    const isCurrent = v.v === S.versions[0]?.v;
    detail.classList.remove("hidden");
    mount(detail,
      h("div", { class: "row", style: { marginBottom: "8px" } }, h("b", { class: "grow" }, `Version ${v.v}`),
        btn("", { kind: "quiet", icon: "x", cls: "sm", title: "Close", onClick: () => { sel = null; detail.classList.add("hidden"); draw(); } })),
      out.length ? out.map(([p, k]) => h("div", { class: "vfile" }, h("span", { class: "ch " + k, title: { a: "Added", m: "Modified", d: "Deleted" }[k] }, k.toUpperCase()), h("span", { class: "nm", title: p }, p),
        k !== "d" && parent ? btn("", { kind: "quiet", icon: "diff", cls: "sm", title: "Diff against v" + parent.v, onClick: () => compareDialog(v.v, parent.v, p, "diff") }) : null,
        !isCurrent && k !== "d" ? btn("", { kind: "quiet", icon: "restore", cls: "sm", title: "Restore just this file", onClick: () => restore(v, [p]) }) : null))
        : h("div", { class: "faint", style: { fontSize: "12px" } }, parent ? "No file changes against the previous version." : "First version."),
      h("div", { class: "row", style: { marginTop: "10px" } },
        btn("Compare", { icon: "split", cls: "sm", onClick: () => compareDialog(v.v, parent?.v, out.find(([p]) => isHtml(p))?.[0]) }),
        h("span", { class: "grow" }),
        !isCurrent ? btn("Restore this version", { kind: "primary", icon: "restore", cls: "sm", onClick: () => restore(v) }) : h("span", { class: "faint", style: { fontSize: "12px" } }, "This is the current version")));
  }
  async function restore(v, paths) {
    if (!await confirmDialog("Restore version", paths ? `Restore ${paths[0]} as it was in v${v.v}? Your current copy stays in the history.` : `Restore every file to v${v.v}? This creates a new version; nothing is lost.`, { confirm: "Restore" })) return;
    try {
      const r = await api("POST", `${P_(S.project.id)}/versions/${v.v}/restore`, paths ? { paths } : {});
      toast(`Restored — now version ${r.version?.v ?? r.version ?? ""}`, { kind: "success" });
      await Promise.all([loadVersions(), loadFiles()]);
      bus.emit("reload-previews", paths || []);
    } catch (e) { toastError(e, "Restore failed"); }
  }
  draw();
  loadVersions();
  const offs = [bus.on("versions", () => { draw(); if (sel) { const nv = S.versions.find((x) => x.v === sel.v); if (nv) select(nv); } }),
    bus.on("select-version", (v) => { const x = S.versions.find((y) => y.v === v); if (x) select(x); })];
  return () => offs.forEach((f) => f());
}

export async function compareDialog(a, b, path, tab = "side") {
  if (!S.versions.length) await loadVersions();
  if (!S.versions.length) { toast("There are no versions to compare yet."); return; }
  let va = a ?? S.versions[0].v, vb = b ?? (S.versions.find((x) => x.v < va)?.v ?? va);
  await Promise.all([versionFiles(va), versionFiles(vb)]);
  const allPaths = () => [...new Set([...Object.keys(S.versions.find((x) => x.v === va)?.files || {}), ...Object.keys(S.versions.find((x) => x.v === vb)?.files || {})])].sort();
  let p = path || allPaths().find(isHtml) || allPaths()[0];
  const vsel = (val, on) => { const s = h("select", { class: "select", style: { width: "120px" } }, S.versions.map((v) => h("option", { value: v.v, selected: v.v === val || null }, `v${v.v} · ${v.origin || ""}`))); s.addEventListener("change", () => on(+s.value)); return s; };
  const fsel = h("select", { class: "select", style: { width: "220px" } });
  const drawF = () => mount(fsel, allPaths().map((x) => h("option", { value: x, selected: x === p || null }, x)));
  drawF();
  fsel.addEventListener("change", () => { p = fsel.value; drawBody(); });
  const tabs = h("div", { class: "seg" });
  const drawTabs = () => mount(tabs, [["side", "Side by side"], ["diff", "Diff"]].map(([k, l]) => h("button", { class: tab === k ? "on" : "", onclick: () => { tab = k; drawTabs(); drawBody(); } }, l)));
  drawTabs();
  const body = h("div", { style: { height: "100%", minHeight: 0 } });
  const m = modal({
    title: "Compare versions", cls: "full",
    body: h("div", { style: { display: "flex", flexDirection: "column", gap: "12px", height: "100%" } },
      h("div", { class: "row wrap" }, vsel(vb, async (v) => { vb = v; await versionFiles(v); drawF(); drawBody(); }), icon("chevronRight"), vsel(va, async (v) => { va = v; await versionFiles(v); drawF(); drawBody(); }), fsel, h("span", { class: "grow" }), tabs),
      body),
  });
  m.el.querySelector(".modal-body").style.display = "flex"; m.el.querySelector(".modal-body").style.flexDirection = "column";
  body.style.flex = "1";
  async function versionText(v, path) {
    const files = S.versions.find((x) => x.v === v)?.files;
    if (files && !(path in files)) return null;
    return tryApi("GET", `${P_(S.project.id)}/versions/${v}/files/${encPath(path)}`, undefined, { as: "text" }).catch(() => null);
  }
  function srcdocFrame(html) {
    // Opaque origin (no allow-same-origin): the old page runs but can't reach
    // this origin. <base> points relative assets at the current preview files.
    const base = `<base href="${S.previewOrigin}/p/${encodeURIComponent(S.project.id)}/">`;
    const doc = /<head[^>]*>/i.test(html) ? html.replace(/<head[^>]*>/i, (m0) => m0 + base) : base + html;
    return h("iframe", { sandbox: "allow-scripts", srcdoc: doc, title: "Version preview" });
  }
  async function drawBody() {
    mount(body, h("div", { class: "muted", style: { padding: "20px" } }, "Loading…"));
    if (!p) { mount(body, emptyState("file", "No files", "")); return; }
    if (tab === "diff") {
      const r = await tryApi("GET", `${P_(S.project.id)}/versions/diff?a=${vb}&b=${va}&path=${encodeURIComponent(p)}`).catch((e) => ({ error: e.message }));
      let diff = r?.diff;
      if (r === null || diff === undefined) {
        const [ta, tb] = await Promise.all([versionText(vb, p), versionText(va, p)]);
        diff = ta === null && tb === null ? null : naiveDiff(ta || "", tb || "", `v${vb}/${p}`, `v${va}/${p}`);
      }
      if (diff == null) { mount(body, emptyState("diff", "Diff isn't available", r?.error || "")); return; }
      mount(body, h("div", { class: "diff", style: { height: "100%" } }, diff.split("\n").map((l) => h("div", { class: l.startsWith("@@") ? "hunk" : l.startsWith("+++") || l.startsWith("---") ? "meta" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "" }, l || " "))));
      return;
    }
    if (!isHtml(p)) { tab = "diff"; drawTabs(); return drawBody(); }
    const [ta, tb] = await Promise.all([versionText(vb, p), versionText(va, p)]);
    const col = (label, html) => h("div", { class: "compare-col" }, h("div", { class: "row" }, h("b", null, label), h("span", { class: "faint mono" }, p)),
      h("div", { class: "frame" }, html === null ? emptyState("file", "Not in this version", "") : srcdocFrame(html)));
    mount(body, h("div", { class: "compare" }, col(`v${vb}`, ta), col(`v${va}`, tb)));
  }
  drawBody();
}
// Line-level LCS diff, used only when the server has no diff route.
function naiveDiff(a, b, na, nb) {
  const A = a.split("\n"), B = b.split("\n");
  if (A.length * B.length > 4e6) return `--- ${na}\n+++ ${nb}\n@@ file too large for an in-browser diff @@`;
  const n = A.length, m = B.length, dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = [`--- ${na}`, `+++ ${nb}`];
  let i = 0, j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && A[i] === B[j]) { out.push(" " + A[i]); i++; j++; }
    else if (j < m && (i >= n || dp[i][j + 1] >= dp[i + 1][j])) { out.push("+" + B[j]); j++; }
    else { out.push("-" + A[i]); i++; }
  }
  return out.join("\n");
}

// ── Review (assets) ──────────────────────────────────────────────────────
function renderReview(host) {
  if (features.assets === false) return missing(host, "checkCircle", "Review");
  let filter = "all";
  const summary = h("div", { class: "review-summary" });
  const list = h("div", { class: "pane-scroll" }, skeleton(4, "sk-block"));
  mount(host, h("div", { class: "pane" }, h("div", { class: "pane-head" }, h("h3", null, "Review"),
    btn("", { kind: "quiet", icon: "refresh", cls: "sm", title: "Refresh", onClick: () => loadAssets() })), summary, list));
  const ST = { "needs-review": ["Needs review", "warn"], approved: ["Approved", "ok"], "changes-requested": ["Changes requested", "err"] };
  function draw() {
    const counts = { "needs-review": 0, approved: 0, "changes-requested": 0 };
    S.assets.forEach((a) => { counts[a.status] = (counts[a.status] || 0) + 1; });
    mount(summary, Object.entries(ST).map(([k, [l]]) => h("div", { class: "rs" + (filter === k ? " on" : ""), onclick: () => { filter = filter === k ? "all" : k; draw(); } }, h("b", null, counts[k] || 0), h("span", null, l))));
    const items = S.assets.filter((a) => filter === "all" || a.status === filter);
    if (!S.assets.length) { mount(list, emptyState("checkCircle", "Nothing to review", "Deliverables the agent registers — pages, slides, exports — show up here for sign-off.")); return; }
    if (!items.length) { mount(list, emptyState("checkCircle", "No items with this status", "")); return; }
    const groups = new Map();
    items.forEach((a) => { const g = a.group || "Deliverables"; if (!groups.has(g)) groups.set(g, []); groups.get(g).push(a); });
    clear(list);
    let minis = 0;
    for (const [g, as] of groups) {
      list.appendChild(h("div", { class: "file-group" }, g, h("span", { class: "grow" }), h("span", null, as.length)));
      for (const a of as) {
        const [lbl, cls] = ST[a.status] || [a.status, ""];
        const mini = h("div", { class: "mini" });
        if (a.path && isHtml(a.path) && minis++ < 12) mini.appendChild(h("iframe", { src: previewUrl(S.project.id, a.path), sandbox: "allow-scripts allow-same-origin", loading: "lazy", tabindex: -1, title: "" }));
        else mini.appendChild(h("div", { style: { display: "grid", placeItems: "center", height: "100%", color: "var(--faint)" } }, icon("file")));
        list.appendChild(h("div", { class: "asset" }, mini,
          h("div", { class: "info" }, h("span", { class: "nm", title: a.path, onclick: () => a.path && import("./workspace.js").then((m) => m.openFile(a.path)) }, a.name || a.path),
            h("span", { class: "sub" }, [a.subtitle, a.viewport ? `${a.viewport.width}×${a.viewport.height}` : null, a.versions?.length ? `v${a.versions[a.versions.length - 1]}` : null].filter(Boolean).join(" · ") || a.path),
            h("span", null, h("span", { class: "pill " + cls }, lbl))),
          h("div", { class: "col", style: { gap: "3px" } },
            btn("", { kind: a.status === "approved" ? "" : "quiet", icon: "check", cls: "sm" + (a.status === "approved" ? " on" : ""), title: "Approve", onClick: () => setStatus(a, "approved") }),
            btn("", { kind: "quiet", icon: "comment", cls: "sm" + (a.status === "changes-requested" ? " on" : ""), title: "Request changes", onClick: () => requestChanges(a) }))));
      }
    }
  }
  async function setStatus(a, status) {
    const r = await tryApi("PATCH", `${P_(S.project.id)}/assets/${encodeURIComponent(a.id)}`, { status }, { feature: "assetPatch" }).catch((e) => { toastError(e); return undefined; });
    if (r === null) { // no PATCH route: rewrite the manifest
      a.status = status;
      await api("PUT", P_(S.project.id) + "/assets", { assets: S.assets }).catch(toastError);
    }
    await loadAssets();
    toast(status === "approved" ? `Approved ${a.name || a.path}` : "Marked as needing changes", { kind: "success" });
  }
  async function requestChanges(a) {
    const note = await promptDialog("Request changes", { label: `What should change in ${a.name || a.path}?`, multiline: true, confirm: "Request changes" });
    if (note === null) return;
    await setStatus(a, "changes-requested");
    if (note) {
      const c = await createComment(a.path || a.name, { td_id: null, selector: null }, note);
      if (c) toast("Comment added", { action: { label: "Send to agent", run: () => sendComments([c.id]) } });
    }
  }
  draw();
  loadAssets();
  return bus.on("assets", draw);
}

// ── Tweaks ───────────────────────────────────────────────────────────────
function renderTweaks(host) {
  const file = () => S.activeFile;
  const body = h("div", { class: "pane-scroll" });
  mount(host, h("div", { class: "pane" }, h("div", { class: "pane-head" }, h("h3", null, "Tweaks"), h("span", { class: "faint mono", style: { fontSize: "11px" } }, file() || "")), body));
  let values = null, src = null, fromPanel = false;
  async function loadValues() {
    values = null;
    if (!file() || !isHtml(file())) return;
    src = await tryApi("GET", P_(S.project.id) + "/files/" + encPath(file()), undefined, { as: "text" }).catch(() => null);
    const m = typeof src === "string" && src.match(/\/\*EDITMODE-BEGIN\*\/([\s\S]*?)\/\*EDITMODE-END\*\//);
    if (m) { try { values = JSON.parse(m[1]); } catch { values = "invalid"; } }
  }
  async function draw() {
    const f = file();
    if (!f || !isHtml(f)) { mount(body, emptyState("sliders", "Open a page first", "Tweaks are live controls a page exposes — colours, type, layout variants.")); return; }
    await loadValues();
    const t = live.tweaks.get(f) || {};
    const parts = [];
    parts.push(h("div", { class: "insp-sec" },
      h("div", { class: "row" }, h("div", { class: "grow" }, h("div", { style: { fontWeight: 600, fontSize: "12.5px" } }, t.available ? "This page has live controls" : values ? "Saved tweak values" : "No live controls on this page"),
        h("div", { class: "faint", style: { fontSize: "11.5px" } }, t.available ? "Show them on the page to adjust the design; changes save to the source without a model call." : values ? "Open the page in Preview to use its panel." : "Ask the agent to add a few — e.g. accent colour, density, hero layout.")),
        t.available ? h("label", { class: "switch", title: "Show Tweaks on the page" }, h("input", { type: "checkbox", checked: !!t.on, onchange: (e) => { toggleTweaks(f, e.target.checked); } }), h("span")) : null),
      !t.available && !values ? btn("Ask the agent to make it tweakable", { icon: "sparkle", cls: "sm", onClick: async () => { bus.emit("chat-prefill", `Make ${f} tweakable: add 2–3 live Tweaks that reveal something (for example accent colour, density, hero layout).`); (await import("./workspace.js")).setPanel("chat"); } }) : null));
    if (values && values !== "invalid") {
      parts.push(h("div", { class: "insp-sec" }, h("h4", null, icon("sliders"), "Values in the source"),
        Object.entries(values).map(([k, v]) => h("div", { class: "tw-row" }, h("label", { title: k }, k), valueControl(k, v)))));
    } else if (values === "invalid") parts.push(h("div", { class: "insp-sec" }, h("div", { class: "err-box" }, "The EDITMODE block in this file isn't valid JSON, so it can't be edited here.")));
    mount(body, parts);
  }
  function valueControl(k, v) {
    const commit = (nv) => { fromPanel = true; queueTweak(file(), { [k]: nv }); };
    if (typeof v === "boolean") return h("label", { class: "switch" }, h("input", { type: "checkbox", checked: v, onchange: (e) => commit(e.target.checked) }), h("span"));
    if (typeof v === "number") { const i = h("input", { class: "input", type: "number", value: v, step: "any" }); i.addEventListener("change", () => commit(+i.value)); return i; }
    if (typeof v === "string" && /^#[0-9a-f]{6}$/i.test(v)) { const i = h("input", { type: "color", value: v }); i.addEventListener("change", () => commit(i.value)); return h("div", { class: "color-row" }, i, h("span", { class: "mono faint" }, v)); }
    if (typeof v === "string") { const i = h("input", { class: "input", value: v }); i.addEventListener("change", () => commit(i.value)); return i; }
    return h("span", { class: "mono faint" }, JSON.stringify(v));
  }
  draw();
  const offs = [bus.on("tweaks", (f) => { if (f === file()) draw(); }), bus.on("tweak-saved", (e) => { if (e.file === file()) { toast("Tweak saved to the source", { kind: "success", ms: 1600 }); if (fromPanel) { fromPanel = false; reloadFile(e.file); } draw(); } }), bus.on("tabs", draw)];
  return () => offs.forEach((f) => f());
}

// ── Inspect ──────────────────────────────────────────────────────────────
const STYLE_FIELDS = [
  ["color", "Text colour", "color"], ["background-color", "Background", "color"], ["font-family", "Font"], ["font-size", "Size"], ["font-weight", "Weight"],
  ["line-height", "Line height"], ["letter-spacing", "Letter spacing"], ["text-align", "Align"], ["padding", "Padding"], ["margin", "Margin"], ["gap", "Gap"],
  ["border-radius", "Radius"], ["width", "Width"], ["height", "Height"], ["opacity", "Opacity"], ["box-shadow", "Shadow"],
];
function renderInspect(host) {
  const body = h("div", { class: "pane-scroll" });
  mount(host, h("div", { class: "pane" }, h("div", { class: "pane-head" }, h("h3", null, "Inspect"),
    btn("Select", { icon: "inspect", cls: "sm" + (S.mode === "edit" ? " on" : ""), title: "Select mode  (E)", onClick: async () => { const ws = await import("./workspace.js"); ws.setMode("edit"); if (S.view === "code") ws.setView("preview"); } })), body));
  let edited = {};
  function draw() {
    const s = S.selection;
    edited = {};
    if (!s) {
      mount(body, emptyState("inspect", "Nothing selected", "Turn on select mode (E) and click an element in the design to see and change its properties."));
      return;
    }
    const comp = s.computed || {};
    const label = s.td_id ? "#" + s.td_id : s.tag ? `<${s.tag}>` + (s.screen ? ` in ${s.screen}` : "") : (s.selector || "element").split(">").pop().trim();
    // Only offer in-place text editing on leaf elements; replacing a container's
    // text would flatten its children.
    const isLeaf = typeof s.text === "string" && !/\nchildren:/.test(s.mentioned_element || "");
    const secs = [];
    secs.push(h("div", { class: "insp-sec" },
      h("div", { class: "row" }, h("b", { class: "grow ellipsis", style: { fontSize: "13px" } }, label), h("span", { class: "faint mono", style: { fontSize: "11px" } }, s.file)),
      s.source_loc ? h("div", { class: "faint mono", style: { fontSize: "11px", marginTop: "3px" } }, s.source_loc) : null,
      h("div", { class: "row", style: { marginTop: "8px" } },
        btn("Add to chat", { icon: "chat", cls: "sm", onClick: async () => { bus.emit("chat-selection", s); (await import("./workspace.js")).setPanel("chat"); } }),
        btn("Comment", { icon: "comment", cls: "sm", onClick: async () => { const n = await promptDialog("Comment on " + label, { multiline: true, confirm: "Add comment" }); if (n) { const c = await createComment(s.file, s, n); if (c) toast("Comment added", { kind: "success", action: { label: "Send to agent", run: () => sendComments([c.id]) } }); } } }),
        s.source_loc ? btn("Source", { icon: "code", cls: "sm", kind: "quiet", onClick: async () => (await import("./workspace.js")).openFile(s.source_loc.split(":")[0], { view: "code" }) }) : null)));
    if (isLeaf && s.text.length < 2000) {
      const ta = h("textarea", { class: "input", rows: Math.min(5, Math.max(2, Math.ceil(s.text.length / 40))) }, s.text);
      secs.push(h("div", { class: "insp-sec" }, h("h4", null, icon("text"), "Text"), ta,
        h("div", { class: "row", style: { marginTop: "6px", justifyContent: "flex-end" } }, btn("Apply text", { cls: "sm", onClick: () => { if (ta.value !== s.text) writeBack(s.file, { op: "text", value: ta.value, td_id: s.td_id, source_loc: s.source_loc, selector: s.selector, old: s.text }).then((ok) => ok && reloadFile(s.file)); } }))));
    }
    const kv = h("div", { class: "kv" });
    for (const [prop, lbl, type] of STYLE_FIELDS) {
      const cur = comp[prop] ?? comp[prop.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] ?? "";
      const inp = h("input", { class: "input", value: cur, placeholder: "—", "aria-label": lbl, style: { fontFamily: "var(--mono)", fontSize: "11.5px" } });
      const apply = () => { edited[prop] = inp.value; for (const f of framesFor(s.file)) post(f, { type: "td:apply-style", td_id: s.td_id, selector: s.selector, style: { [prop]: inp.value } }); saveRow.classList.remove("hidden"); };
      inp.addEventListener("change", apply);
      inp.addEventListener("keydown", (e) => { if (e.key === "ArrowUp" || e.key === "ArrowDown") { const m = inp.value.match(/^(-?\d*\.?\d+)(px|em|rem|%)?$/); if (m) { e.preventDefault(); const step = e.shiftKey ? 10 : 1; inp.value = (+m[1] + (e.key === "ArrowUp" ? step : -step)) + (m[2] || ""); apply(); } } });
      let ctl = inp;
      if (type === "color") {
        const hex = toHex(cur);
        const pick = h("input", { type: "color", value: hex || "#000000", style: { width: "26px", height: "26px", border: "1px solid var(--border)", borderRadius: "6px", padding: "1px", background: "var(--panel2)" } });
        pick.addEventListener("input", () => { inp.value = pick.value; apply(); });
        ctl = h("div", { class: "row", style: { gap: "5px" } }, pick, inp);
      }
      kv.append(h("label", { title: prop }, lbl), ctl);
    }
    const saveRow = h("div", { class: "row hidden", style: { marginTop: "10px", justifyContent: "flex-end" } },
      btn("Discard", { kind: "ghost", cls: "sm", onClick: () => { reloadFile(s.file); draw(); } }),
      btn("Save to source", { kind: "primary", cls: "sm", icon: "check", onClick: async () => { const ok = await writeBack(s.file, { op: "style", value: { ...edited }, td_id: s.td_id, source_loc: s.source_loc, selector: s.selector }); if (ok) { edited = {}; saveRow.classList.add("hidden"); } } }));
    secs.push(h("div", { class: "insp-sec" }, h("h4", null, icon("palette"), "Style"), kv, saveRow,
      h("div", { class: "faint", style: { fontSize: "11px", marginTop: "8px" } }, "Changes preview live. Save writes them into the source when the element maps to one place; otherwise they go to the agent.")));
    if (s.mentioned_element) secs.push(h("div", { class: "insp-sec" }, h("h4", null, icon("code"), "What the agent sees"), h("div", { class: "me-block" }, s.mentioned_element)));
    mount(body, secs);
  }
  draw();
  const offs = [bus.on("selection", draw), bus.on("editor-selection", (d) => {
    if (!d.ids || !d.ids.length) return;
    S.selection = { file: null, node_id: d.ids[0], selector: null, text: d.nodes?.[0]?.name || null, mentioned_element: `<mentioned-element>\nnode: ${d.ids.join(", ")}\n${d.nodes?.[0]?.name ? "name: " + d.nodes[0].name + "\n" : ""}</mentioned-element>` };
    draw();
  }), bus.on("mode", () => { const b = host.querySelector(".pane-head .btn"); if (b) b.classList.toggle("on", S.mode === "edit"); })];
  return () => offs.forEach((f) => f());
}
function toHex(c) {
  if (!c) return null;
  if (/^#[0-9a-f]{6}$/i.test(c)) return c;
  const m = String(c).match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  return m ? "#" + [m[1], m[2], m[3]].map((x) => (+x).toString(16).padStart(2, "0")).join("") : null;
}
