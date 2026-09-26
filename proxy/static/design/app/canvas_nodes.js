// TeleDesign — canvas documents and script nodes (host side).
//
// Editor side: patches/open-pencil/0013 (several documents per project) and 0015
// (script nodes); server side: proxy/api_design_editor.py (/docs, /editor/scripts).
//
//  - mountDocs: the document switcher in the canvas bar. Each project holds
//    docs/<id>.fig documents; the editor iframe opens one (`?doc=`), and switching
//    reloads it on another. The choice is remembered per project.
//  - mountScripts: property controls for the selected script node (its file's
//    @input header), Re-run / Reseed / Convert to layers, and an error badge drawn
//    over every script node whose last run failed.
import { h, icon, btn, api, toast, toastError, P_, promptDialog, confirmDialog, menu, prefs, bus } from "./core.js";
import { openFile } from "./workspace.js";

const CSS = `
.td-docs-btn .doc-name { max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.td-sx { position: absolute; pointer-events: none; border: 2px dashed #e5484d; border-radius: 4px; }
.td-sx .td-sx-badge { position: absolute; left: 6px; top: 6px; max-width: calc(100% - 12px); padding: 3px 8px; border-radius: 6px; background: #e5484d; color: #fff;
  font: 600 11.5px/1.35 system-ui, sans-serif; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; box-shadow: 0 2px 8px rgba(0,0,0,.18); }
.td-script { position: absolute; z-index: 7; width: 272px; max-height: calc(100% - 24px); border-radius: 10px; overflow: hidden; }
.td-script .td-script-body { overflow: auto; padding: 8px 10px 10px; display: flex; flex-direction: column; gap: 8px; font-size: 12.5px; }
.td-script .row { display: grid; grid-template-columns: 88px 1fr; gap: 8px; align-items: center; }
.td-script .row > span { color: var(--muted, #6b7280); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.td-script .row .pair { display: flex; gap: 6px; align-items: center; min-width: 0; }
.td-script .row input[type=range] { flex: 1; min-width: 0; }
.td-script .row input.num { width: 58px; }
.td-script .row input[type=color] { width: 30px; height: 26px; padding: 0; border: 1px solid var(--border, rgba(0,0,0,.15)); border-radius: 5px; background: none; }
.td-script .row .input { min-width: 0; width: 100%; padding: 4px 6px; font-size: 12.5px; }
.td-script .td-script-err { color: var(--err, #d92d20); background: var(--err-soft, rgba(229,72,77,.12)); border-radius: 6px; padding: 6px 8px; white-space: pre-wrap; word-break: break-word; }
.td-script .td-script-warn { color: #92400e; font-size: 11.5px; }
.td-script .td-script-meta { color: var(--muted, #6b7280); font-size: 11.5px; }
.td-script .actions { display: flex; flex-wrap: wrap; gap: 6px; }
`;

function ensureCss() {
  if (document.getElementById("td-canvas-nodes-css")) return;
  document.head.appendChild(h("style", { id: "td-canvas-nodes-css" }, CSS));
}

// ── Documents ────────────────────────────────────────────────────────────

const docPref = (pid) => "canvasDoc:" + pid;

/** The document the canvas should open for `pid` (null = the project's default). */
export function preferredDoc(pid) {
  const v = prefs.get(docPref(pid), null);
  return typeof v === "string" && /^[a-z0-9][a-z0-9-]{0,39}$/.test(v) ? v : null;
}

export function mountDocs({ pid, bar, anchor, reload }) {
  ensureCss();
  let listing = { default: null, docs: [] };
  let open = preferredDoc(pid);
  const label = h("span", { class: "doc-name" }, "Canvas");
  const b = h("button", { class: "btn ghost sm td-docs-btn", type: "button", title: "Canvas documents in this project",
    onclick: (e) => showMenu(e.currentTarget) }, icon("layers"), label, h("span", { class: "faint" }, "▾"));
  (anchor || bar.firstChild)?.after ? (anchor || bar.firstChild).after(b) : bar.prepend(b);

  const current = () => listing.docs.find((d) => d.id === (open || listing.default));
  function draw() {
    const doc = current();
    label.textContent = doc ? doc.name : "Canvas";
    b.title = doc ? `Canvas document: ${doc.name} (docs/${doc.id}.fig)${listing.docs.length > 1 ? ` — ${listing.docs.length} in this project` : ""}` : "Canvas documents";
  }
  async function refresh() {
    try {
      listing = await api("GET", P_(pid) + "/docs", undefined, { feature: "canvasDocs" });
      if (open && !listing.docs.some((d) => d.id === open)) { open = null; prefs.set(docPref(pid), null); }
      draw();
    } catch { b.classList.add("hidden"); }
  }
  function switchTo(id) {
    const doc = listing.docs.find((d) => d.id === id);
    if (!doc) return;
    open = id === listing.default ? null : id;
    prefs.set(docPref(pid), open);
    draw();
    reload(open);
  }
  async function create(copyFrom) {
    const name = await promptDialog(copyFrom ? "Duplicate canvas" : "New canvas", {
      label: "Name", value: copyFrom ? `${current()?.name || "Canvas"} copy` : "", placeholder: "e.g. Wireframes", confirm: "Create" });
    if (!name) return;
    try {
      const r = await api("POST", P_(pid) + "/docs", { name, ...(copyFrom ? { copy_from: copyFrom } : {}) });
      await refresh();
      switchTo(r.doc.id);
      toast(`Opened ${r.doc.name}`, { kind: "success" });
    } catch (e) { toastError(e, "Couldn't create the canvas"); }
  }
  async function rename(doc) {
    const name = await promptDialog("Rename canvas", { label: "Name", value: doc.name, confirm: "Rename" });
    if (!name || name === doc.name) return;
    try { await api("PATCH", P_(pid) + "/docs/" + doc.id, { name }); await refresh(); } catch (e) { toastError(e, "Rename failed"); }
  }
  async function makeDefault(doc) {
    try { await api("PATCH", P_(pid) + "/docs/" + doc.id, { default: true }); await refresh(); toast(`${doc.name} is now the default canvas`, { kind: "success" }); }
    catch (e) { toastError(e, "Couldn't change the default"); }
  }
  async function remove(doc) {
    if (!(await confirmDialog("Delete canvas", `Delete "${doc.name}" (docs/${doc.id}.fig)? Earlier versions stay in the project's history.`, { confirm: "Delete", danger: true }))) return;
    try { await api("DELETE", P_(pid) + "/docs/" + doc.id); await refresh(); } catch (e) { toastError(e, "Delete failed"); }
  }
  async function showMenu(el) {
    await refresh();
    const cur = current();
    const items = [{ heading: "Canvas documents" },
      ...listing.docs.map((d) => ({ label: d.name, icon: d.id === cur?.id ? "check" : "canvas", checked: d.id === cur?.id,
        hint: [d.default ? "default" : "", d.has_canvas ? "" : "empty"].filter(Boolean).join(" · ") || null, onClick: () => d.id !== cur?.id && switchTo(d.id) })),
      "-",
      { label: "New canvas…", icon: "plus", onClick: () => create(null) },
      cur ? { label: "Duplicate this canvas…", icon: "copy", disabled: !cur.has_canvas, onClick: () => create(cur.id) } : null,
      cur ? { label: "Rename…", icon: "edit", onClick: () => rename(cur) } : null,
      cur && !cur.default ? { label: "Make default", icon: "star", onClick: () => makeDefault(cur) } : null,
      cur?.mirror ? { label: "Open JSON mirror", icon: "code", hint: "read-only, for diffs", onClick: () => openFile(cur.mirror, { view: "code" }) } : null];
    const others = listing.docs.filter((d) => d.id !== cur?.id);
    if (others.length) items.push("-", { heading: "Delete" }, ...others.map((d) => ({ label: d.name, icon: "trash", danger: true, onClick: () => remove(d) })));
    menu(el, items, { width: "240px" });
  }
  const offEv = bus.on("ev:docs", refresh);
  refresh();
  return {
    // The editor may have switched itself (an agent's telecode_doc_open): follow it.
    onReady(docId) {
      if (docId && docId !== (open || listing.default)) { open = docId === listing.default ? null : docId; prefs.set(docPref(pid), open); }
      refresh();
    },
    cleanup() { offEv(); b.remove(); },
  };
}

// ── Script nodes ─────────────────────────────────────────────────────────

export function mountScripts({ pid, body, bar, layer, send, vp }) {
  ensureCss();
  let scripts = [];
  let components = [];
  let selectedId = null;
  const badges = new Map();
  const waiters = new Map();
  let panel = null;

  const request = (type, payload) => new Promise((resolve, reject) => {
    const request_id = Math.random().toString(36).slice(2);
    waiters.set(request_id, { resolve, reject });
    send(type, { ...payload, request_id });
    setTimeout(() => { if (waiters.delete(request_id)) reject(new Error("The canvas did not answer")); }, 20000);
  });

  const newBtn = btn("Script", { icon: "code", kind: "ghost", cls: "sm", title: "Add a script node: layers generated by a project .js file", onClick: () => createScript() });
  bar.append(newBtn);

  async function createScript() {
    const n = scripts.length + 1;
    const file = await promptDialog("New script node", { label: "Script file (created from a starter if it doesn't exist)", value: `scripts/script-${n}.js`, confirm: "Add" });
    if (!file) return;
    if (!/^[\w./ -]+\.(js|jsx|mjs|ts|tsx)$/i.test(file) || file.includes("..") || file.startsWith("/")) { toast("Use a project-relative .js path.", { kind: "error" }); return; }
    try {
      const r = await request("script-create", { file });
      toast(r.status?.ok ? `Script node added (${r.status.nodes} layers)` : `Script node added — ${r.status?.error || "run failed"}`, { kind: r.status?.ok ? "success" : "error",
        action: { label: "Edit code", run: () => openFile(file, { view: "code" }) } });
    } catch (e) { toastError(e, "Couldn't add the script node"); }
  }

  function onMessage(type, p) {
    if (type === "td-editor:scripts") {
      scripts = Array.isArray(p.scripts) ? p.scripts : [];
      components = Array.isArray(p.components) ? p.components : [];
      place();
      drawPanel();
      return true;
    }
    if (type === "td-editor:script-result") {
      const w = waiters.get(p.request_id);
      if (!w) return true;
      waiters.delete(p.request_id);
      p.ok ? w.resolve(p.result) : w.reject(new Error(p.error || "Script command failed"));
      return true;
    }
    return false;
  }

  function place() {
    const v = vp();
    const c = v.canvas || { left: 0, top: 0 };
    const failing = scripts.filter((s) => s.status && !s.status.ok);
    for (const s of failing) {
      let el = badges.get(s.node_id);
      if (!el) { el = h("div", { class: "td-sx" }, h("span", { class: "td-sx-badge" })); layer.appendChild(el); badges.set(s.node_id, el); }
      el.querySelector(".td-sx-badge").textContent = "Script error: " + (s.status.error || "failed");
      el.title = `${s.file}: ${s.status.error || "failed"}`;
      el.style.left = c.left + s.x * v.zoom + v.x + "px"; el.style.top = c.top + s.y * v.zoom + v.y + "px";
      el.style.width = Math.max(4, s.width * v.zoom) + "px"; el.style.height = Math.max(4, s.height * v.zoom) + "px";
    }
    for (const [id, el] of badges) if (!failing.some((s) => s.node_id === id)) { el.remove(); badges.delete(id); }
    if (panel) {
      const cc = v.canvas;
      // Top-right of the editor's canvas area, clear of its layers and properties panels.
      if (cc && cc.width) { panel.style.right = Math.max(12, body.clientWidth - (cc.left + cc.width) + 12) + "px"; panel.style.top = cc.top + 12 + "px"; }
    }
  }

  function onSelection(nodes) {
    const one = (nodes || []).length === 1 ? nodes[0] : null;
    selectedId = one && one.script ? one.id : null;
    drawPanel();
  }

  let pendingInputs = {};
  let inputTimer = 0;
  function setInput(name, value, immediate = false) {
    pendingInputs[name] = value;
    clearTimeout(inputTimer);
    inputTimer = setTimeout(flushInputs, immediate ? 0 : 180);
  }
  async function flushInputs() {
    const s = scripts.find((x) => x.node_id === selectedId);
    if (!s || !Object.keys(pendingInputs).length) return;
    const inputs = pendingInputs;
    pendingInputs = {};
    try { await request("script-set", { node_id: s.node_id, inputs }); } catch (e) { toastError(e, "Script update failed"); }
  }

  function control(spec, s) {
    const value = s.inputs?.[spec.name] ?? spec.default;
    const isVar = typeof value === "string" && /^\$[^/\s][^/]*\/.+$/.test(value);
    if (spec.type === "number" && !isVar) {
      const num = h("input", { class: "input num", type: "number", value, step: spec.step ?? "any", min: spec.min ?? null, max: spec.max ?? null,
        onchange: (e) => setInput(spec.name, Number(e.target.value), true) });
      if (spec.min !== undefined && spec.max !== undefined && spec.min !== null && spec.max !== null) {
        const range = h("input", { type: "range", min: spec.min, max: spec.max, step: spec.step ?? (spec.max - spec.min) / 100, value,
          oninput: (e) => { num.value = e.target.value; setInput(spec.name, Number(e.target.value)); } });
        return h("div", { class: "pair" }, range, num);
      }
      return num;
    }
    if (spec.type === "boolean" && !isVar) {
      return h("input", { type: "checkbox", checked: value === true || null, onchange: (e) => setInput(spec.name, e.target.checked, true) });
    }
    if (spec.type === "enum") {
      return h("select", { class: "input", onchange: (e) => setInput(spec.name, e.target.value, true) },
        ...(spec.options || []).map((o) => h("option", { value: o, selected: o === value || null }, o)));
    }
    if (spec.type === "ref") {
      return h("select", { class: "input", onchange: (e) => setInput(spec.name, e.target.value, true) },
        h("option", { value: "" }, "— none —"),
        ...components.map((c) => h("option", { value: c.name, selected: c.name === value || null }, c.name)));
    }
    if (spec.type === "color" && !isVar) {
      const text = h("input", { class: "input", value, onchange: (e) => setInput(spec.name, e.target.value.trim(), true), title: "#hex, or $Collection/name to bind a variable" });
      const hex = /^#[0-9a-f]{6}$/i.test(value) ? value : /^#[0-9a-f]{3}$/i.test(value) ? "#" + value.slice(1).split("").map((c) => c + c).join("") : "#000000";
      const pick = h("input", { type: "color", value: hex.slice(0, 7), oninput: (e) => { text.value = e.target.value; setInput(spec.name, e.target.value); } });
      return h("div", { class: "pair" }, pick, text);
    }
    return h("input", { class: "input", value: String(value ?? ""), title: isVar ? "Bound to a variable" : "",
      onchange: (e) => { const t = e.target.value; setInput(spec.name, spec.type === "number" && !/^\$/.test(t) ? Number(t) : spec.type === "boolean" && !/^\$/.test(t) ? t === "true" : t, true); } });
  }

  function drawPanel() {
    const s = selectedId ? scripts.find((x) => x.node_id === selectedId) : null;
    if (!s) { if (panel) { panel.remove(); panel = null; } return; }
    const focused = panel && panel.contains(document.activeElement);
    if (focused && panel.dataset.node === s.node_id) {
      // Keep the control being edited; refresh only the status lines.
      const st = panel.querySelector(".td-script-status");
      if (st) st.replaceWith(statusBlock(s));
      return;
    }
    const inputs = s.schema?.inputs || [];
    const bodyEl = h("div", { class: "td-script-body" },
      h("div", { class: "td-script-meta" }, s.schema?.name ? `${s.schema.name} · ` : "", s.file),
      ...(inputs.length ? inputs.map((spec) => h("label", { class: "row" }, h("span", { title: spec.name }, spec.label || spec.name), control(spec, s)))
        : [h("div", { class: "td-script-meta" }, s.schema ? "This script declares no @input." : "Loading the script's inputs…")]),
      statusBlock(s),
      h("div", { class: "actions" },
        btn("Re-run", { icon: "refresh", kind: "ghost", cls: "sm", onClick: () => request("script-run", { node_id: s.node_id }).catch((e) => toastError(e, "Re-run failed")) }),
        btn("Reseed", { icon: "sparkle", kind: "ghost", cls: "sm", title: "New random seed", onClick: () => request("script-set", { node_id: s.node_id, seed: "new" }).catch((e) => toastError(e, "Reseed failed")) }),
        btn("Edit code", { icon: "code", kind: "ghost", cls: "sm", onClick: () => openFile(s.file, { view: "code" }) }),
        btn("To layers", { icon: "layers", kind: "ghost", cls: "sm", title: "Keep the generated layers and drop the script", onClick: async () => {
          try { await request("script-convert", { node_id: s.node_id }); toast("Converted to ordinary layers", { kind: "success" }); } catch (e) { toastError(e, "Convert failed"); }
        } })));
    const el = h("section", { class: "td-dock td-script", role: "region", "aria-label": "Script node", dataset: { node: s.node_id } },
      h("header", null, icon("code"), h("span", { class: "grow" }, h("b", null, s.name), h("span", { class: "faint" }, " · script")),
        btn("", { kind: "quiet", icon: "x", cls: "sm", title: "Close", onClick: () => { selectedId = null; drawPanel(); } })),
      bodyEl);
    if (panel) panel.replaceWith(el); else body.appendChild(el);
    panel = el;
    place();
  }

  function statusBlock(s) {
    const st = s.status;
    const warn = (st?.warnings || []).length ? h("div", { class: "td-script-warn" }, st.warnings.join("\n")) : null;
    if (!st) return h("div", { class: "td-script-status td-script-meta" }, "Not run yet");
    if (!st.ok) return h("div", { class: "td-script-status" }, h("div", { class: "td-script-err" }, st.error || "Script failed"), warn);
    return h("div", { class: "td-script-status" }, h("div", { class: "td-script-meta" }, `${st.nodes} layer${st.nodes === 1 ? "" : "s"} · ${st.ms} ms · seed ${s.seed}`), warn);
  }

  return {
    onMessage, onSelection, place,
    cleanup() { newBtn.remove(); if (panel) panel.remove(); for (const el of badges.values()) el.remove(); badges.clear(); },
  };
}
