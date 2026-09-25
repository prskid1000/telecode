// TeleDesign — keyboard shortcuts and the "?" sheet.
import { h, modal, isTyping, bus, closeMenu } from "./core.js";
import { S } from "./state.js";
import { toggleTheme } from "./theme.js";

const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
const MOD = isMac ? "⌘" : "Ctrl";

// [group, keys (display), label, test(e) -> bool, run(e), when?: () => bool]
const inProject = () => S.route === "project";
const preview = () => inProject() && (S.view === "preview" || S.view === "canvas");
const ws = () => import("./workspace.js");
const SHORTCUTS = [
  ["General", ["?"], "Show keyboard shortcuts", (e) => e.key === "?", () => sheet()],
  ["General", [MOD, "K"], "Focus the chat", (e) => mod(e) && e.key.toLowerCase() === "k", async () => { if (!inProject()) return; const w = await ws(); w.setPanel("chat"); setTimeout(() => document.getElementById("chat-input")?.focus(), 30); }, null, true],
  ["General", ["Alt", "Shift", "L"], "Toggle light / dark", (e) => e.altKey && e.shiftKey && e.code === "KeyL", () => toggleTheme(), null, true],
  ["General", ["Esc"], "Close menus and dialogs", null, null],
  ["Home", ["N"], "New project", (e) => e.key.toLowerCase() === "n" && !mod(e) && !e.altKey, () => import("./gallery.js").then((m) => m.newProjectDialog()), () => !inProject()],
  ["Home", ["/"], "Search projects", (e) => e.key === "/", (e) => { const s = document.getElementById("gallery-search"); if (s) { e.preventDefault(); s.focus(); } }, () => !inProject()],
  ["Views", ["Alt", "1"], "Canvas", (e) => e.altKey && e.code === "Digit1", () => ws().then((w) => w.setView("canvas")), inProject, true],
  ["Views", ["Alt", "2"], "Code", (e) => e.altKey && e.code === "Digit2", () => ws().then((w) => w.setView("code")), inProject, true],
  ["Views", ["Alt", "3"], "Preview", (e) => e.altKey && e.code === "Digit3", () => ws().then((w) => w.setView("preview")), inProject, true],
  ["Views", [MOD, "\\"], "Toggle side panel", (e) => mod(e) && e.key === "\\", () => ws().then((w) => w.toggleSide()), inProject, true],
  ["Views", [MOD, "Alt", "B"], "Toggle files", (e) => mod(e) && e.altKey && e.code === "KeyB", () => ws().then((w) => w.toggleRail()), inProject, true],
  ["Views", ["G", "then C / V / R / T / I / M"], "Chat, versions, review, tweaks, inspect, comments", null, null],
  ["Modes", ["V"], "Interact with the page", (e) => e.key.toLowerCase() === "v" && plain(e), () => ws().then((w) => w.setMode("view")), preview],
  ["Modes", ["C"], "Comment", (e) => e.key.toLowerCase() === "c" && plain(e), () => ws().then((w) => w.setMode("comment")), preview],
  ["Modes", ["E"], "Select and inspect", (e) => e.key.toLowerCase() === "e" && plain(e), () => ws().then((w) => w.setMode("edit")), preview],
  ["Modes", ["T"], "Edit text in place", (e) => e.key.toLowerCase() === "t" && plain(e), () => ws().then((w) => w.setMode("text")), preview],
  ["Modes", ["K"], "Move, resize and knobs", (e) => e.key.toLowerCase() === "k" && plain(e), () => ws().then((w) => w.setMode("knobs")), preview],
  ["Modes", ["D"], "Draw on the design", (e) => e.key.toLowerCase() === "d" && plain(e), () => ws().then((w) => w.setMode("draw")), preview],
  ["Canvas", ["Space", "drag"], "Pan", null, null],
  ["Canvas", [MOD, "scroll"], "Zoom around the cursor", null, null],
  ["Canvas", ["+", "/", "−"], "Zoom in / out", (e) => (e.key === "+" || e.key === "=" || e.key === "-") && !mod(e), (e) => bus.emit("canvas-zoom", e.key === "-" ? "out" : "in"), () => inProject() && S.view === "canvas"],
  ["Canvas", ["Shift", "1"], "Zoom to fit", (e) => e.shiftKey && e.code === "Digit1" && !mod(e), () => bus.emit("canvas-zoom", "fit"), () => inProject() && S.view === "canvas"],
  ["Canvas", ["Shift", "2"], "Zoom to the selected board", (e) => e.shiftKey && e.code === "Digit2" && !mod(e), () => bus.emit("canvas-zoom", "selection"), () => inProject() && S.view === "canvas"],
  ["Canvas", ["Shift", "0"], "Zoom to 100%", (e) => e.shiftKey && e.code === "Digit0" && !mod(e), () => bus.emit("canvas-zoom", "100"), () => inProject() && S.view === "canvas"],
  ["Canvas", ["Double-click"], "Open a board in Preview", null, null],
  ["Preview", ["[", "/", "]"], "Previous / next slide", (e) => e.key === "[" || e.key === "]", (e) => bus.emit("preview-slide", e.key === "[" ? "prev" : "next"), () => inProject() && S.view === "preview"],
  ["Preview", [MOD, "Z"], "Undo — step the page back a version", (e) => mod(e) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "z", (e) => { e.preventDefault(); import("./interact.js").then((m) => m.undoStep(S.activeFile, "undo")); }, () => inProject() && S.view === "preview" && !!S.activeFile],
  ["Preview", [MOD, "Shift", "Z"], "Redo", (e) => mod(e) && !e.altKey && ((e.shiftKey && e.key.toLowerCase() === "z") || (!e.shiftKey && e.key.toLowerCase() === "y")), (e) => { e.preventDefault(); import("./interact.js").then((m) => m.undoStep(S.activeFile, "redo")); }, () => inProject() && S.view === "preview" && !!S.activeFile],
  ["Preview", [MOD, "R"], "Reload the page", (e) => mod(e) && e.key.toLowerCase() === "r" && !e.shiftKey, (e) => { e.preventDefault(); bus.emit("preview-reload"); }, () => inProject() && S.view === "preview", true],
  ["Chat", ["Enter"], "Send (queues while the agent works)", null, null],
  ["Chat", ["Shift", "Enter"], "New line", null, null],
  ["Chat", ["↑"], "Edit your last message (empty box)", null, null],
  ["Chat", ["/"], "Pick a skill", null, null],
  ["Chat", [MOD, "."], "Stop the agent", (e) => mod(e) && e.key === ".", () => bus.emit("chat-stop"), inProject, true],
  ["Chat", ["Alt", "N"], "New chat", (e) => e.altKey && e.code === "KeyN" && !mod(e), () => bus.emit("chat-new"), inProject, true],
  ["Code", [MOD, "S"], "Save the file", null, null],
  ["Comments", ["Enter"], "Save a comment", null, null],
  ["Comments", [MOD, "Enter"], "Save and send to the agent", null, null],
  ["Export", [MOD, "Shift", "E"], "Export", (e) => mod(e) && e.shiftKey && e.code === "KeyE", () => { const b = [...document.querySelectorAll(".topbar .btn")].find((x) => x.textContent.trim() === "Export"); if (b) import("./exporter.js").then((m) => m.exportMenu(b)); }, inProject, true],
];
function mod(e) { return isMac ? e.metaKey : e.ctrlKey; }
function plain(e) { return !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey; }

let gPending = 0;
const G_MAP = { c: "chat", v: "versions", r: "review", t: "tweaks", i: "inspect", m: "comments" };
addEventListener("keydown", (e) => {
  if (document.querySelector("dialog.modal[open]") && e.key !== "Escape") {
    if (!(mod(e) && e.key === "Enter")) return;
  }
  if (document.querySelector(".presenter")) return;
  const typing = isTyping(e);
  if (gPending && !typing && inProject()) {
    const p = G_MAP[e.key.toLowerCase()];
    gPending = 0;
    if (p) { e.preventDefault(); ws().then((w) => w.setPanel(p)); return; }
  }
  if (!typing && e.key.toLowerCase() === "g" && plain(e) && inProject()) { gPending = setTimeout(() => (gPending = 0), 900); return; }
  for (const [, , , test, run, when, allowTyping] of SHORTCUTS) {
    if (!test || !run) continue;
    if (typing && !allowTyping) continue;
    if (when && !when()) continue;
    if (test(e)) { if (!["/", "?"].includes(e.key) || !typing) { closeMenu(); run(e); if (allowTyping || e.key.length === 1) e.preventDefault(); } return; }
  }
});

export function sheet() {
  const groups = new Map();
  for (const [g, keys, label] of SHORTCUTS) { if (!groups.has(g)) groups.set(g, []); groups.get(g).push([keys, label]); }
  const cols = [[], [], []];
  let i = 0;
  for (const [g, list] of groups) { cols[i % 3].push(h("h4", null, g), list.map(([keys, label]) => h("div", { class: "sc" }, h("span", null, label), h("span", null, keys.map((k) => /^(then|drag|scroll|\/)/.test(k) || k.startsWith("then") ? h("span", { class: "faint", style: { fontSize: "11px" } }, k) : h("kbd", null, k)))))); i++; }
  modal({ title: "Keyboard shortcuts", width: "940px", body: h("div", { class: "sheet" }, cols.map((c) => h("div", null, c))) });
}
