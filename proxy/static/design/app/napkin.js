// TeleDesign — saved sketches (.napkin). Draw mode on a board saves one to
// scraps/<name>.napkin (+ scraps/.<name>.thumbnail.png); this reopens it to keep
// drawing, save, or send it to the chat. A new blank sketch starts here too.
import { h, icon, btn, api, toast, toastError, modal, bus, P_, encPath, promptDialog } from "./core.js";
import { S, loadFiles } from "./state.js";
import { saveNapkin, dataUrlBytes } from "./interact.js";

const COLORS = ["#e5484d", "#16181d", "#3d7bfd", "#2f9e44", "#f08c00"];

export async function openNapkin(path) {
  let rec;
  try {
    const txt = await api("GET", P_(S.project.id) + "/files/" + encPath(path), undefined, { as: "text" });
    rec = JSON.parse(txt);
  } catch (e) { toastError(e, "Couldn't open the sketch"); return; }
  if (!rec || rec.type !== "td-napkin") { toast("That .napkin file isn't a TeleDesign sketch.", { kind: "error" }); return; }
  editor(path, rec);
}

export async function newNapkin() {
  const name = await promptDialog("New sketch", { label: "Name", value: "sketch-" + new Date().toISOString().slice(0, 10), confirm: "Create" });
  if (!name) return;
  const stem = name.replace(/\.napkin$/i, "").replace(/[^\w .-]+/g, "-").trim() || "sketch";
  editor(`scraps/${stem}.napkin`, { type: "td-napkin", version: 1, board: null, width: 1200, height: 800, image: null }, true);
}

function editor(path, rec, fresh = false) {
  const canvas = h("canvas", { class: "napkin-canvas", "aria-label": "Sketch" });
  const ctx = canvas.getContext("2d");
  let color = COLORS[0], width = 4, dirty = fresh;
  const strokes = [];
  let base = null;
  const load = new Promise((res) => {
    if (!rec.image) { canvas.width = +rec.width || 1200; canvas.height = +rec.height || 800; res(); return; }
    const img = new Image();
    img.onload = () => { base = img; canvas.width = img.naturalWidth; canvas.height = img.naturalHeight; res(); };
    img.onerror = () => { canvas.width = 1200; canvas.height = 800; res(); };
    img.src = rec.image;
  });
  function paint() {
    ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (base) ctx.drawImage(base, 0, 0);
    for (const s of strokes) {
      ctx.strokeStyle = s.color; ctx.lineWidth = s.width; ctx.lineCap = "round"; ctx.lineJoin = "round";
      ctx.beginPath();
      s.pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      if (s.pts.length === 1) ctx.lineTo(s.pts[0][0] + 0.1, s.pts[0][1]);
      ctx.stroke();
    }
  }
  const pos = (e) => { const r = canvas.getBoundingClientRect(); return [(e.clientX - r.left) * canvas.width / r.width, (e.clientY - r.top) * canvas.height / r.height]; };
  let cur = null;
  canvas.addEventListener("pointerdown", (e) => { canvas.setPointerCapture(e.pointerId); cur = { color, width: width * (canvas.width / Math.max(1, canvas.getBoundingClientRect().width)), pts: [pos(e)] }; strokes.push(cur); dirty = true; paint(); });
  canvas.addEventListener("pointermove", (e) => { if (!cur) return; cur.pts.push(pos(e)); paint(); });
  const end = () => { cur = null; };
  canvas.addEventListener("pointerup", end); canvas.addEventListener("pointercancel", end);

  const swatches = h("div", { class: "row", style: { gap: "4px" } });
  const drawSw = () => { swatches.replaceChildren(...COLORS.map((c) => h("button", { class: "swatch" + (c === color ? " on" : ""), title: c, "aria-label": "Colour " + c, style: { background: c }, onclick: () => { color = c; drawSw(); } }))); };
  drawSw();
  const size = h("input", { type: "range", min: 1, max: 16, value: width, "aria-label": "Pen size", style: { width: "90px", accentColor: "var(--accent)" } });
  size.addEventListener("input", () => { width = +size.value; });
  const undo = () => { strokes.pop(); paint(); };
  const onKey = (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); e.stopPropagation(); undo(); } };
  const name = path.split("/").pop();
  const png = () => canvas.toDataURL("image/png");
  const save = async () => {
    const image = png();
    await saveNapkin(null, { ...rec, image, width: canvas.width, height: canvas.height }, { path });
    base = await new Promise((res) => { const i = new Image(); i.onload = () => res(i); i.src = image; });
    strokes.length = 0; dirty = false; paint();
    await loadFiles();
    toast(`Saved ${name}`, { kind: "success" });
  };
  const m = modal({
    title: "Sketch", subtitle: path + (rec.board ? ` · drawn on ${rec.board}` : ""), width: "min(1100px, 96vw)", cls: "napkin-modal",
    body: h("div", { class: "napkin" },
      h("div", { class: "row wrap napkin-tools" }, icon("draw"), swatches, size,
        btn("", { kind: "quiet", icon: "undo", cls: "sm", title: "Undo stroke  (Ctrl+Z)", onClick: undo }),
        btn("Clear", { kind: "quiet", cls: "sm", onClick: () => { strokes.length = 0; paint(); } }),
        h("span", { class: "grow" }),
        rec.board ? btn("Open board", { kind: "quiet", cls: "sm", icon: "canvas", onClick: async () => { m.close(); (await import("./workspace.js")).openFile(rec.board, { view: "preview" }); } }) : null),
      h("div", { class: "napkin-stage" }, canvas)),
    actions: [
      { label: "Close", kind: "ghost" },
      { label: "Send to chat", icon: "chat", onClick: async () => {
        if (dirty) await save();
        const { uploadFiles } = await import("./workspace.js");
        const paths = await uploadFiles([new File([dataUrlBytes(png())], name.replace(/\.napkin$/, ".png"), { type: "image/png" })]);
        if (!paths.length) return false;
        bus.emit("attach", paths);
        bus.emit("chat-prefill", `See my sketch ${path} (attached) — `);
        (await import("./workspace.js")).setPanel("chat");
      } },
      { label: "Save", kind: "primary", icon: "check", onClick: async () => { try { await save(); } catch (e) { toastError(e, "Couldn't save the sketch"); } return false; } },
    ],
    onClose: () => document.removeEventListener("keydown", onKey, true),
  });
  document.addEventListener("keydown", onKey, true);
  load.then(paint);
  return m;
}
