// TeleDesign — deck presenter: the deck full screen, plus a notes column with
// the current slide's speaker notes, the next slide and a timer.
import { h, icon, btn, bus } from "./core.js";
import { S, previewUrl } from "./state.js";
import { makePreviewFrame, unregisterFrame, post } from "./bridge.js";
import { speakerNotes } from "./preview.js";

export async function present(file, slide) {
  if (!file) return;
  const notes = await speakerNotes(file);
  let index = slide?.index || 1, count = slide?.count || 0;
  const frame = makePreviewFrame(previewUrl(S.project.id, file) + `#slide=${index}`, { file, role: "presenter", title: "Presentation" });
  const timer = h("div", { class: "timer" }, "00:00");
  const cur = h("div", { class: "cur" });
  const next = h("div", { class: "next" });
  const counter = h("span", { class: "mono", style: { minWidth: "56px", textAlign: "center" } });
  const withNotes = notes.length > 0;
  const root = h("div", { class: "presenter" + (withNotes ? "" : " solo"), role: "dialog", "aria-label": "Presenter" },
    h("div", { class: "main" }, frame,
      h("div", { class: "pbar" },
        btn("", { kind: "quiet", icon: "chevronLeft", cls: "sm", title: "Previous", onClick: () => go("prev") }), counter,
        btn("", { kind: "quiet", icon: "chevronRight", cls: "sm", title: "Next", onClick: () => go("next") }),
        btn("", { kind: "quiet", icon: "x", cls: "sm", title: "Exit  (Esc)", onClick: () => exit() }))),
    withNotes ? h("aside", { class: "notes" }, timer, h("div", { style: { fontSize: "11.5px", color: "#8a93a1" } }, "Speaker notes"), cur, next) : null);
  document.body.appendChild(root);
  const started = Date.now();
  const tick = setInterval(() => { const s = Math.floor((Date.now() - started) / 1000); timer.textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; }, 1000);
  const draw = () => {
    counter.textContent = count ? `${index} / ${count}` : String(index);
    cur.textContent = notes[index - 1] || "No notes for this slide.";
    next.textContent = notes[index] ? "Next: " + notes[index].slice(0, 140) + (notes[index].length > 140 ? "…" : "") : count && index >= count ? "Last slide" : "";
  };
  draw();
  const go = (action, i) => post(frame, { type: "td:slide", action, index: i });
  const off = bus.on("slide", (e) => { if (e.frame === frame) { index = e.index; count = e.count || count; draw(); } });
  const key = (e) => {
    if (e.key === "Escape") { exit(); return; }
    if (["ArrowRight", "ArrowDown", "PageDown", " ", "Enter"].includes(e.key)) { e.preventDefault(); go("next"); }
    if (["ArrowLeft", "ArrowUp", "PageUp"].includes(e.key)) { e.preventDefault(); go("prev"); }
    if (e.key === "Home") go("first"); if (e.key === "End") go("last");
  };
  addEventListener("keydown", key, true);
  try { await root.requestFullscreen?.(); } catch { /* not allowed; the overlay still covers the window */ }
  const onFs = () => { if (!document.fullscreenElement) exit(); };
  document.addEventListener("fullscreenchange", onFs);
  function exit() {
    removeEventListener("keydown", key, true); document.removeEventListener("fullscreenchange", onFs);
    clearInterval(tick); off(); unregisterFrame(frame); root.remove();
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  }
}
