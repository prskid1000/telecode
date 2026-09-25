// TeleDesign — Code view: the real sources of HTML boards, editable, saved with
// PUT …/files (a user version). CodeMirror 5 from cdnjs when reachable; a plain
// textarea otherwise.
import { h, icon, btn, mount, clear, api, tryApi, toast, toastError, bus, P_, encPath, fileKind, isHtml, prefs, emptyState, menu, confirmDialog } from "./core.js";
import { S, previewUrl, writeUrl, loadFiles, loadVersions } from "./state.js";
import { makePreviewFrame, unregisterFrame } from "./bridge.js";

const CM = "https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/";
let cmReady = null;
function loadCM() {
  if (cmReady) return cmReady;
  const css = (href) => new Promise((res) => { const l = document.createElement("link"); l.rel = "stylesheet"; l.href = href; l.onload = res; l.onerror = res; document.head.appendChild(l); });
  const js = (src) => new Promise((res, rej) => { const s = document.createElement("script"); s.src = src; s.crossOrigin = "anonymous"; s.onload = res; s.onerror = rej; document.head.appendChild(s); });
  cmReady = (async () => {
    await Promise.all([css(CM + "codemirror.min.css"), js(CM + "codemirror.min.js")]);
    for (const m of ["mode/xml/xml", "mode/javascript/javascript", "mode/css/css", "mode/htmlmixed/htmlmixed", "mode/jsx/jsx", "mode/markdown/markdown", "addon/selection/active-line", "addon/edit/matchbrackets", "addon/edit/closetag", "addon/edit/closebrackets"]) await js(CM + m + ".min.js");
    return window.CodeMirror;
  })().catch(() => null);
  return cmReady;
}
const modeFor = (p) => {
  const ext = p.split(".").pop().toLowerCase();
  return { html: "htmlmixed", htm: "htmlmixed", jsx: "jsx", tsx: "jsx", js: "javascript", mjs: "javascript", ts: "javascript", json: { name: "javascript", json: true }, css: "css", md: "markdown", svg: "xml", napkin: { name: "javascript", json: true } }[ext] || null;
};

const buffers = new Map();   // path -> {text, orig, dirty}

export function render(body, bar) {
  const pid = S.project.id;
  let file = S.activeFile || null;
  const textFiles = () => S.files.filter((f) => !["image"].includes(fileKind(f.path)) || f.path.endsWith(".svg"));
  if (!file) file = textFiles().find((f) => isHtml(f.path))?.path || textFiles()[0]?.path || null;
  let split = prefs.get("codeSplit", true);
  let cm = null, ta = null, liveFrame = null;

  const fileBtn = h("button", { class: "pick", title: "Switch file", style: { fontFamily: "var(--mono)", fontSize: "12px" } });
  const dirtyDot = h("span", { class: "dot warn hidden", title: "Unsaved changes" });
  const saveBtn = btn("Save", { kind: "primary", cls: "sm", icon: "check", kbd: "Ctrl S", onClick: () => save() });
  const revertBtn = btn("Revert", { kind: "ghost", cls: "sm", onClick: () => revert() });
  const splitBtn = btn("", { kind: "quiet", icon: "split", cls: "sm", title: "Show preview beside the code", onClick: () => { split = !split; prefs.set("codeSplit", split); drawLive(); splitBtn.classList.toggle("on", split); } });
  splitBtn.classList.toggle("on", split);
  const note = S.editorAvailable ? h("span", { class: "faint", style: { fontSize: "11.5px" } }, "Layer boards: open the Code panel inside the canvas editor.") : null;
  bar.append(icon("code"), fileBtn, dirtyDot, h("span", { class: "grow" }), note, splitBtn, revertBtn, saveBtn);

  const pane = h("div", { class: "code-pane" });
  const liveBox = h("div", { class: "code-live hidden" });
  const host = h("div", { class: "code-host" }, pane, liveBox);
  mount(body, h("div", { class: "code-view" }, host));

  function drawFileBtn() {
    mount(fileBtn, file || "No file", icon("chevronDown"));
    fileBtn.onclick = (e) => menu(e.currentTarget, textFiles().map((f) => ({ label: f.path, icon: f.path === file ? "check" : isHtml(f.path) ? "canvas" : "file", hint: buffers.get(f.path)?.dirty ? "unsaved" : null, onClick: () => switchTo(f.path) })).concat(textFiles().length ? [] : [{ label: "No files yet", disabled: true }]), { width: "280px" });
    const b = buffers.get(file);
    dirtyDot.classList.toggle("hidden", !(b && b.dirty));
    saveBtn.disabled = !(b && b.dirty); revertBtn.disabled = !(b && b.dirty);
  }
  function switchTo(p) { stash(); file = p; S.activeFile = p; if (!S.tabs.includes(p)) S.tabs.push(p); writeUrl(); load(); }
  function stash() { if (!file) return; const b = buffers.get(file); if (b) { b.text = cm ? cm.getValue() : ta ? ta.value : b.text; b.dirty = b.text !== b.orig; } }

  async function load() {
    drawFileBtn();
    clear(pane);
    if (cm) { cm.toTextArea?.(); cm = null; }
    if (!file) { mount(pane, h("div", { style: { display: "grid", placeItems: "center", height: "100%" } }, emptyState("code", "No files yet", "Files the agent writes, and files you upload, appear here."))); drawLive(); return; }
    if (fileKind(file) === "image" && !file.endsWith(".svg")) {
      mount(pane, h("div", { style: { display: "grid", placeItems: "center", height: "100%", background: "var(--canvas)" } }, h("img", { src: P_(pid) + "/files/" + encPath(file), alt: file, style: { maxWidth: "90%", maxHeight: "90%", boxShadow: "var(--board-shadow)" } })));
      drawLive(); return;
    }
    let b = buffers.get(file);
    if (!b) {
      mount(pane, h("div", { style: { padding: "16px" } }, h("div", { class: "sk sk-line" }), h("div", { class: "sk sk-line" }), h("div", { class: "sk sk-line" })));
      const text = await api("GET", P_(pid) + "/files/" + encPath(file), undefined, { as: "text" }).catch((e) => { toastError(e, "Couldn't open " + file); return null; });
      if (text === null) return;
      b = { text, orig: text, dirty: false };
      buffers.set(file, b);
    }
    clear(pane);
    ta = h("textarea", { class: "code-ta", spellcheck: "false", "aria-label": "Source of " + file });
    ta.value = b.text;
    pane.appendChild(ta);
    ta.addEventListener("input", () => { b.text = ta.value; b.dirty = b.text !== b.orig; drawFileBtn(); });
    const CMlib = await loadCM();
    if (CMlib && ta.isConnected) {
      cm = CMlib.fromTextArea(ta, { mode: modeFor(file), lineNumbers: true, theme: "td", styleActiveLine: true, matchBrackets: true, autoCloseTags: true, autoCloseBrackets: true, tabSize: 2, indentUnit: 2, lineWrapping: false,
        extraKeys: { "Ctrl-S": () => save(), "Cmd-S": () => save() } });
      cm.on("change", () => { b.text = cm.getValue(); b.dirty = b.text !== b.orig; drawFileBtn(); });
      cm.focus();
    }
    drawLive();
  }
  function drawLive() {
    if (liveFrame) { unregisterFrame(liveFrame); liveFrame = null; }
    const show = split && file && isHtml(file);
    liveBox.classList.toggle("hidden", !show);
    clear(liveBox);
    if (show) { liveFrame = makePreviewFrame(previewUrl(pid, file), { file, role: "preview", title: "Live preview" }); liveBox.appendChild(liveFrame); }
  }
  async function save() {
    stash();
    const b = buffers.get(file);
    if (!b || !b.dirty) return;
    try {
      const r = await api("PUT", P_(pid) + "/files/" + encPath(file), new Blob([b.text], { type: "application/octet-stream" }));
      b.orig = b.text; b.dirty = false; drawFileBtn();
      toast(r && r.version ? `Saved — version ${r.version}` : "Saved", { kind: "success" });
      if (liveFrame) liveFrame.src = previewUrl(pid, file, "&r=" + Date.now());
      loadFiles(); loadVersions();
      bus.emit("reload-previews", [file]);
    } catch (e) { toastError(e, "Couldn't save"); }
  }
  async function revert() {
    const b = buffers.get(file);
    if (!b) return;
    if (!await confirmDialog("Revert changes", `Discard your unsaved edits to ${file}?`, { confirm: "Discard", danger: true })) return;
    buffers.delete(file);
    load();
  }
  const keySave = (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); save(); } };
  addEventListener("keydown", keySave);
  const offFiles = bus.on("reload-previews", (paths) => {
    // The agent rewrote a file we have open: refresh it unless the user has edits.
    for (const p of paths) { const b = buffers.get(p); if (b && !b.dirty) buffers.delete(p); else if (b && b.dirty && p === file) toast(`${p} changed on disk while you were editing. Save to keep your version, or Revert to load theirs.`, { kind: "error" }); }
    if (paths.includes(file) && !buffers.get(file)) load();
  });
  load();
  return () => { stash(); removeEventListener("keydown", keySave); offFiles(); if (liveFrame) unregisterFrame(liveFrame); };
}
