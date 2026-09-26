// TeleDesign — theme axes, slots and shader / mesh fills of canvas layers (host side).
//
// Editor side: patches/open-pencil/0016 (theme axes), 0017–0021 (slots, shader / mesh
// fills). Everything here is a canvas tool (telecode_theme_*, telecode_slot_*,
// telecode_fill_*) called through POST …/editor/call — the path agents use through
// design_canvas_call — plus two editor messages: td-editor:slots (empty slots to hatch)
// and td-editor:selection (what the Layer panel shows).
//
//  - Theme (bar button): the document-wide mode of each axis (variable collection).
//  - Layer (bar button, a panel that follows the selection):
//      theme per axis (inherit / pin);
//      slots — make a slot, preferred components, fill (JSX or a suggested component),
//      reset per slot;
//      fills — per-uniform controls generated from the shader's declared uniforms
//      (sliders, colour pickers, 2D pads), shader code (SkSL or GLSL), presets, mesh
//      gradients with on-canvas point handles (drag = move, click = colour; one undo
//      step per drag), remove.
//  - Empty slots are hatched on the canvas.
import { h, icon, btn, toast, toastError, promptDialog, menu, prefs } from "./core.js";

const CSS = `
.td-extras { position: absolute; z-index: 7; width: 312px; max-height: calc(100% - 24px); border-radius: 10px; overflow: hidden; display: flex; flex-direction: column; }
.td-extras .td-x-body { overflow: auto; padding: 8px 10px 10px; display: flex; flex-direction: column; gap: 8px; font-size: 12.5px; }
.td-extras h4 { margin: 4px 0 0; font-size: 11px; letter-spacing: .04em; text-transform: uppercase; color: var(--muted, #6b7280); }
.td-extras .row { display: grid; grid-template-columns: 84px minmax(0, 1fr) auto; gap: 6px; align-items: center; }
.td-extras .row > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.td-extras .row .input { min-width: 0; width: 100%; padding: 3px 6px; font-size: 12.5px; }
.td-extras .ctl { display: grid; grid-template-columns: 84px minmax(0, 1fr) 52px; gap: 6px; align-items: center; }
.td-extras .ctl > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted, #6b7280); }
.td-extras .ctl input[type=range] { width: 100%; }
.td-extras .ctl input.num { width: 52px; padding: 2px 4px; font-size: 12px; }
.td-extras .ctl input[type=color] { width: 100%; height: 24px; padding: 0; border: 1px solid var(--border, rgba(0,0,0,.15)); border-radius: 5px; background: none; }
.td-extras .pad { position: relative; width: 100%; height: 64px; border-radius: 6px; background: var(--soft, rgba(0,0,0,.06)); cursor: crosshair; touch-action: none; }
.td-extras .pad i { position: absolute; width: 10px; height: 10px; margin: -5px 0 0 -5px; border-radius: 50%; background: var(--accent, #4f46e5); box-shadow: 0 0 0 2px #fff; pointer-events: none; }
.td-extras .fillbox { border: 1px solid var(--border, rgba(0,0,0,.1)); border-radius: 8px; padding: 6px 8px; display: flex; flex-direction: column; gap: 6px; }
.td-extras .faint { color: var(--muted, #6b7280); font-size: 11.5px; }
.td-extras .td-x-err { color: var(--err, #d92d20); background: var(--err-soft, rgba(229,72,77,.12)); border-radius: 6px; padding: 4px 6px; white-space: pre-wrap; word-break: break-word; font-size: 11.5px; }
.td-extras .actions { display: flex; flex-wrap: wrap; gap: 6px; }
.td-slot-hatch { position: absolute; pointer-events: none; border: 1.5px dashed rgba(124, 58, 237, .75); border-radius: 3px;
  background: repeating-linear-gradient(135deg, rgba(124, 58, 237, .16) 0 6px, transparent 6px 12px); }
.td-slot-hatch span { position: absolute; left: 4px; top: 3px; padding: 1px 6px; border-radius: 4px; font: 600 10.5px/1.4 system-ui, sans-serif; color: #fff; background: rgba(124, 58, 237, .9); white-space: nowrap; max-width: calc(100% - 8px); overflow: hidden; text-overflow: ellipsis; }
.td-mesh-dot { position: absolute; pointer-events: auto; width: 14px; height: 14px; margin: -7px 0 0 -7px; border-radius: 50%; border: 2px solid #fff; box-shadow: 0 0 0 1px rgba(0,0,0,.45), 0 2px 6px rgba(0,0,0,.25); cursor: grab; touch-action: none; z-index: 8; }
.td-mesh-dot.drag { cursor: grabbing; transform: scale(1.2); }
`;

/** Starter mesh for "Mesh gradient": a 3×3 grid of dusk colours. */
export const MESH_STARTER = [
  ["#0b1026", "#3b2a7a", "#0b1026"],
  ["#e0567a", "#f7b267", "#3b2a7a"],
  ["#0b1026", "#e0567a", "#0b1026"],
];

const STARTER_SHADER = `// GLSL (Shadertoy style) or SkSL (half4 main(float2 p)) — detected automatically.
uniform vec4 tint;    // @color @label Tint @default #6d5dfc
uniform float speed;  // @min 0 @max 3 @label Speed @default 1
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 uv = fragCoord / iResolution.xy;
  float wave = 0.5 + 0.5 * sin(uv.x * 8.0 + iTime * speed);
  fragColor = vec4(mix(tint.rgb * 0.3, tint.rgb, wave * uv.y), 1.0);
}`;

const prefKey = (pid) => "canvasExtras:" + pid;
const hex2 = (v) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, "0");
const toHex = (c) => "#" + hex2(c[0] ?? 0) + hex2(c[1] ?? 0) + hex2(c[2] ?? 0);
const fromHex = (s, a = 1) => [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16) / 255).concat([a]);

export function mountExtras({ pid, body, bar, layer, vp, callTool }) {
  if (!document.getElementById("td-canvas-extras-css")) document.head.appendChild(h("style", { id: "td-canvas-extras-css" }, CSS));
  let open = prefs.get(prefKey(pid), false) === true;
  let selected = null;
  let panel = null;
  let seq = 0;
  let emptySlots = [];
  const hatches = new Map();
  let meshEdit = null; // {node, index, def, dots: [el]}

  const themeBtn = btn("Theme", { icon: "sun", kind: "ghost", cls: "sm", title: "Theme axes: the document-wide mode of each variable collection", onClick: (e) => themeMenu(e.currentTarget) });
  const layerBtn = btn("Layer", { icon: "sliders", kind: "ghost", cls: "sm", title: "Theme, slots and shader / mesh fills of the selected layer",
    onClick: () => { open = !open; prefs.set(prefKey(pid), open); draw(); } });
  bar.append(themeBtn, layerBtn);

  async function themeMenu(el) {
    let axes = [];
    try { axes = (await callTool("telecode_theme_get", {}))?.axes || []; } catch (e) { toastError(e, "Couldn't read the theme axes"); return; }
    if (!axes.length) { toast("No variable collections yet. Each collection is a theme axis (Tokens → Push creates them).", { kind: "info" }); return; }
    const items = [];
    for (const ax of axes) {
      items.push({ heading: ax.collection });
      for (const m of ax.modes) {
        items.push({ label: m, checked: m === ax.active_mode, icon: m === ax.active_mode ? "check" : null, hint: m === ax.default_mode ? "default" : null,
          onClick: () => callTool("telecode_theme_active", { modes: { [ax.collection]: m } }).then(() => draw()).catch((e) => toastError(e, "Theme change failed")) });
      }
    }
    menu(el, items, { width: "220px" });
  }

  // ── Canvas overlays: hatched empty slots, mesh point handles ───────────
  const screen = (x, y) => {
    const v = vp();
    const c = v.canvas || { left: 0, top: 0 };
    return [c.left + x * v.zoom + v.x, c.top + y * v.zoom + v.y];
  };
  function placeHatches() {
    const v = vp();
    const live = new Set();
    for (const s of emptySlots) {
      const key = s.node_id;
      live.add(key);
      let el = hatches.get(key);
      if (!el) { el = h("div", { class: "td-slot-hatch", dataset: { slot: s.slot } }, h("span")); layer.appendChild(el); hatches.set(key, el); }
      el.querySelector("span").textContent = `Slot · ${s.slot}` + (s.kind === "instance" ? "" : " (empty)");
      el.title = s.kind === "instance" ? `${s.owner}: empty slot "${s.slot}" — draw or drop a layer here, or use the Layer panel` : `${s.owner}: slot "${s.slot}" has no default content`;
      const [x, y] = screen(s.x, s.y);
      el.style.left = x + "px"; el.style.top = y + "px";
      el.style.width = Math.max(6, s.width * v.zoom) + "px"; el.style.height = Math.max(6, s.height * v.zoom) + "px";
    }
    for (const [key, el] of hatches) if (!live.has(key)) { el.remove(); hatches.delete(key); }
  }
  function placeMesh() {
    if (!meshEdit) return;
    const { node, def } = meshEdit;
    def.points.forEach((p, i) => {
      const el = meshEdit.dots[i];
      if (!el) return;
      const [x, y] = screen(node.x + p.x * node.width, node.y + p.y * node.height);
      el.style.left = x + "px"; el.style.top = y + "px";
      el.style.background = toHex(p.color);
    });
  }
  function stopMeshEdit() {
    if (!meshEdit) return;
    for (const el of meshEdit.dots) el.remove();
    meshEdit = null;
  }
  function startMeshEdit(node, fill) {
    stopMeshEdit();
    const def = JSON.parse(JSON.stringify(fill.definition));
    meshEdit = { node, index: fill.index, def, dots: [] };
    const edit = meshEdit;
    def.points.forEach((p, i) => {
      const dot = h("div", { class: "td-mesh-dot", title: `Point ${i + 1}: drag to move, click to change its colour`, dataset: { point: String(i) } });
      let drag = null;
      let pending = null;
      let timer = 0;
      const flush = () => { timer = 0; if (pending) { const pts = pending; pending = null; callTool("telecode_fill_mesh_edit", { node_id: node.id, index: edit.index, phase: "move", points: pts }).catch(() => {}); } };
      dot.addEventListener("pointerdown", (e) => {
        e.preventDefault(); e.stopPropagation();
        dot.setPointerCapture(e.pointerId);
        drag = { x0: e.clientX, y0: e.clientY, px: p.x, py: p.y, moved: false };
        dot.classList.add("drag");
        callTool("telecode_fill_mesh_edit", { node_id: node.id, index: edit.index, phase: "begin", points: [] }).catch(() => {});
      });
      dot.addEventListener("pointermove", (e) => {
        if (!drag) return;
        const z = vp().zoom || 1;
        const dx = (e.clientX - drag.x0) / z / Math.max(1, node.width);
        const dy = (e.clientY - drag.y0) / z / Math.max(1, node.height);
        if (Math.abs(e.clientX - drag.x0) + Math.abs(e.clientY - drag.y0) > 3) drag.moved = true;
        p.x = Math.min(1.5, Math.max(-0.5, drag.px + dx));
        p.y = Math.min(1.5, Math.max(-0.5, drag.py + dy));
        placeMesh();
        pending = [{ i, x: p.x, y: p.y }];
        if (!timer) timer = setTimeout(flush, 40);
      });
      const end = async () => {
        if (!drag) return;
        const moved = drag.moved;
        drag = null;
        dot.classList.remove("drag");
        clearTimeout(timer); timer = 0; pending = null;
        try {
          await callTool("telecode_fill_mesh_edit", { node_id: node.id, index: edit.index, phase: "end", points: moved ? [{ i, x: p.x, y: p.y }] : [] });
        } catch (err) { toastError(err, "Mesh edit failed"); }
        if (!moved) {
          const input = h("input", { type: "color", value: toHex(p.color), style: { position: "fixed", left: "-100px" } });
          document.body.appendChild(input);
          input.addEventListener("input", () => { p.color = fromHex(input.value, p.color[3] ?? 1); placeMesh(); });
          input.addEventListener("change", () => {
            callTool("telecode_fill_mesh_edit", { node_id: node.id, index: edit.index, points: [{ i, color: input.value }] }).then(() => draw()).catch((err) => toastError(err, "Colour change failed"));
            input.remove();
          });
          input.click();
        }
      };
      dot.addEventListener("pointerup", end);
      dot.addEventListener("pointercancel", end);
      layer.appendChild(dot);
      edit.dots.push(dot);
    });
    placeMesh();
  }

  function onMessage(type, p) {
    if (type !== "td-editor:slots") return false;
    emptySlots = Array.isArray(p.items) ? p.items : [];
    placeHatches();
    return true;
  }

  function onSelection(nodes) {
    const one = (nodes || []).length === 1 ? nodes[0] : null;
    if (!one || one.id !== meshEdit?.node.id) stopMeshEdit();
    selected = one && one.type !== "CANVAS" ? one : null;
    draw();
  }

  function place() {
    placeHatches();
    placeMesh();
    if (!panel) return;
    const cc = vp().canvas;
    if (cc && cc.width) {
      panel.style.right = Math.max(12, body.clientWidth - (cc.left + cc.width) + 12) + "px";
      panel.style.bottom = Math.max(12, body.clientHeight - (cc.top + cc.height) + 12) + "px";
    } else { panel.style.right = "12px"; panel.style.bottom = "12px"; }
  }

  const soft = (p) => p.catch(() => null);

  // ── Panel sections ─────────────────────────────────────────────────────
  function themeSection(node, theme, act) {
    const axes = theme?.axes || [];
    if (!axes.length) return [];
    const out = [h("h4", null, "Theme")];
    for (const ax of axes) {
      const r = theme.resolved?.[ax.collection] || {};
      const pinned = theme.explicit?.[ax.collection] || "";
      const sel = h("select", { class: "input", title: "Inherit, or pin a mode on this layer (everything inside follows)",
        onchange: (e) => act("Theme", callTool("telecode_theme_set", { node_id: node.id, modes: { [ax.collection]: e.target.value || null } })) },
        h("option", { value: "", selected: !pinned || null }, `Inherit (${r.mode || ax.active_mode}${r.from ? " · " + r.from : ""})`),
        ...ax.modes.map((m) => h("option", { value: m, selected: m === pinned || null }, m)));
      out.push(h("div", { class: "row" }, h("span", { title: ax.collection }, ax.collection), sel, h("span")));
    }
    return out;
  }

  async function componentMenu(el, node, slot, act) {
    let s;
    try { s = await callTool("telecode_slot_suggest", { node_id: node.id, slot }); } catch (e) { toastError(e, "Couldn't list components"); return; }
    const pref = s.components.filter((c) => c.preferred);
    const rest = s.components.filter((c) => !c.preferred);
    const item = (c) => ({ label: c.name, hint: `${Math.round(c.width)}×${Math.round(c.height)}`, icon: c.preferred ? "star" : null,
      onClick: () => act("Fill slot", callTool("telecode_slot_fill", { instance_id: node.id, slot, component_id: c.id }, 60)) });
    const items = [];
    if (pref.length) items.push({ heading: "Suggested" }, ...pref.map(item));
    if (rest.length) items.push({ heading: pref.length ? "Other components" : "Components" }, ...rest.slice(0, 40).map(item));
    if (!items.length) items.push({ heading: "No components in this document" });
    if (s.missing?.length) items.push("-", { heading: `Not found: ${s.missing.join(", ")}` });
    menu(el, items, { width: "260px" });
  }

  function slotSection(node, slots, act) {
    if (!slots || slots.kind === "none") return [];
    if (slots.kind === "instance") {
      if (!slots.slots.length) return [];
      const out = [h("h4", null, "Slots")];
      for (const s of slots.slots) {
        const state = s.filled ? (s.empty ? "own · empty" : "own · " + s.content.map((c) => c.name).join(", ")) : "default";
        out.push(h("div", { class: "row" }, h("span", { title: s.name }, s.name),
          h("span", { class: "faint", title: state }, state),
          h("div", { class: "actions" },
            btn("Fill…", { kind: "ghost", cls: "sm", title: "Give this instance its own content (Design JSX)", onClick: async () => {
              const jsx = await promptDialog(`Fill slot "${s.name}"`, { label: "Design JSX", multiline: true, confirm: "Fill",
                placeholder: '<Frame flex="col" gap={8} p={16}><Text>Hello</Text></Frame>' });
              if (jsx) act("Fill slot", callTool("telecode_slot_fill", { instance_id: node.id, slot: s.name, jsx }, 60));
            } }),
            btn("", { kind: "quiet", icon: "plus", cls: "sm", title: "Put a component in this slot (suggested first)", onClick: (e) => componentMenu(e.currentTarget, node, s.name, act) }),
            s.filled ? btn("Reset", { kind: "ghost", cls: "sm", title: "Back to the component's default content",
              onClick: () => act("Reset slot", callTool("telecode_slot_reset", { instance_id: node.id, slot: s.name })) }) : null)));
      }
      out.push(h("div", { class: "faint" }, "A filled slot's layers belong to this instance: select and edit them on the canvas."));
      return out;
    }
    const out = [h("h4", null, "Slots")];
    for (const s of slots.slots) {
      out.push(h("div", { class: "row" }, h("span", { title: s.name }, s.name),
        h("span", { class: "faint", title: (s.preferred || []).join(", ") }, s.preferred?.length ? "suggests " + s.preferred.join(", ") : (s.empty ? "no default content" : "default: " + s.default_content.join(", "))),
        h("div", { class: "actions" },
          btn("", { kind: "quiet", icon: "star", cls: "sm", title: "Preferred components (suggested first)", onClick: async () => {
            const names = await promptDialog(`Preferred components for "${s.name}"`, { label: "Component names, comma separated", value: (s.preferred || []).join(", "), confirm: "Save" });
            if (names !== null) act("Slot suggestions", callTool("telecode_slot_prefer", { node_id: slots.node_id, slot: s.name, components: names.split(",").map((x) => x.trim()).filter(Boolean) }));
          } }),
          btn("", { kind: "quiet", icon: "x", cls: "sm", title: "Unmark this slot (its layers stay)", onClick: () => act("Remove slot", callTool("telecode_slot_remove", { node_id: s.node_id })) }))));
    }
    const isSlotItself = slots.slots.some((s) => s.node_id === node.id);
    if (node.id !== slots.node_id && !isSlotItself && node.type !== "INSTANCE") {
      out.push(h("div", { class: "actions" }, btn("Make this layer a slot", { icon: "plus", kind: "ghost", cls: "sm", onClick: async () => {
        const name = await promptDialog("New slot", { label: "Slot name", value: node.name || "Content", confirm: "Create" });
        if (name) act("Create slot", callTool("telecode_slot_create", { node_id: node.id, name }));
      } })));
    } else if (!slots.slots.length) {
      out.push(h("div", { class: "faint" }, "Select a frame inside this component to make it a slot."));
    }
    return out;
  }

  function fillLabel(f) {
    if (f.kind === "mesh") return `Mesh gradient ${f.definition.columns}×${f.definition.rows}`;
    if (f.kind === "shader") return "Shader · " + (f.definition.preset || (f.lang === "glsl" ? "GLSL" : "SkSL")) + (f.animated ? " · animated" : "");
    return String(f.type || "").toLowerCase().replace(/_/g, " ");
  }

  function controlFor(node, f, c) {
    let timer = 0;
    let pendingValue = null;
    const send = (value, now) => {
      pendingValue = value;
      clearTimeout(timer);
      timer = setTimeout(() => {
        const v = pendingValue; pendingValue = null;
        callTool("telecode_fill_uniforms", { node_id: node.id, index: f.index, uniforms: { [c.name]: v } }).catch((e) => toastError(e, "Uniform update failed"));
      }, now ? 0 : 120);
    };
    const label = h("span", { title: c.name }, c.label || c.name);
    const value = c.value || [];
    if (c.color) {
      const input = h("input", { type: "color", value: toHex(value), dataset: { uniform: c.name }, oninput: (e) => send(fromHex(e.target.value, value[3] ?? 1)), onchange: (e) => send(fromHex(e.target.value, value[3] ?? 1), true) });
      return h("div", { class: "ctl" }, label, input, h("span", { class: "faint" }, toHex(value)));
    }
    if (c.floats === 2) {
      const min = c.min ?? 0, max = c.max ?? 1;
      const dot = h("i");
      const pad = h("div", { class: "pad", title: `${c.name}: drag (x ${min}…${max}, y ${min}…${max})`, dataset: { uniform: c.name } }, dot);
      const readout = h("span", { class: "faint" }, value.map((v) => (+v).toFixed(2)).join(", "));
      const show = (v) => { dot.style.left = ((v[0] - min) / (max - min || 1)) * 100 + "%"; dot.style.top = ((v[1] - min) / (max - min || 1)) * 100 + "%"; readout.textContent = v.map((x) => (+x).toFixed(2)).join(", "); };
      show(value.length ? value : [min, min]);
      const at = (e) => {
        const r = pad.getBoundingClientRect();
        const fx = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
        const fy = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
        const v = [min + fx * (max - min), min + fy * (max - min)];
        show(v);
        return v;
      };
      pad.addEventListener("pointerdown", (e) => { pad.setPointerCapture(e.pointerId); send(at(e)); });
      pad.addEventListener("pointermove", (e) => { if (e.buttons) send(at(e)); });
      pad.addEventListener("pointerup", (e) => send(at(e), true));
      return h("div", { class: "ctl" }, label, pad, readout);
    }
    if (c.floats === 1) {
      const min = c.min ?? Math.min(0, value[0] ?? 0), max = c.max ?? Math.max(1, (value[0] ?? 0) * 2 || 1);
      const num = h("input", { class: "input num", type: "number", value: value[0] ?? 0, step: c.step ?? "any", onchange: (e) => { range.value = e.target.value; send(Number(e.target.value), true); } });
      // Whole steps for wide ranges (sizes, angles), fine steps for 0…1 style ones.
      const step = c.step ?? (max - min >= 20 ? 1 : +((max - min) / 100).toPrecision(2));
      const range = h("input", { type: "range", min, max, step, value: value[0] ?? 0, dataset: { uniform: c.name },
        oninput: (e) => { num.value = e.target.value; send(Number(e.target.value)); } });
      return h("div", { class: "ctl" }, label, range, num);
    }
    const text = h("input", { class: "input", value: value.map((v) => +(+v).toFixed(3)).join(", "), dataset: { uniform: c.name },
      onchange: (e) => send(e.target.value.split(/[\s,]+/).map(Number).filter((x) => Number.isFinite(x)), true) });
    return h("div", { class: "ctl" }, label, text, h("span", { class: "faint" }, `${c.floats} floats`));
  }

  function fillSection(node, fills, act) {
    if (!fills || !Array.isArray(fills.fills)) return [];
    const out = [h("h4", null, "Fills")];
    for (const f of fills.fills) {
      const procedural = f.kind === "mesh" || f.kind === "shader";
      if (!procedural) {
        out.push(h("div", { class: "row" }, h("span", null, `#${f.index + 1}`), h("span", null, fillLabel(f)), h("span")));
        continue;
      }
      const box = h("div", { class: "fillbox", dataset: { fill: String(f.index), kind: f.kind } },
        h("div", { class: "row" }, h("span", null, `#${f.index + 1}`), h("span", { title: fillLabel(f) }, fillLabel(f)),
          h("div", { class: "actions" },
            f.kind === "mesh" ? btn(meshEdit && meshEdit.node.id === node.id && meshEdit.index === f.index ? "Done" : "Points", { kind: "ghost", cls: "sm", title: "Drag the mesh points on the canvas; click a point to change its colour",
              onClick: () => { if (meshEdit && meshEdit.index === f.index) stopMeshEdit(); else startMeshEdit(node, f); draw(); } }) : null,
            f.kind === "shader" ? btn("Code", { kind: "ghost", cls: "sm", title: "Edit the shader (SkSL or GLSL)", onClick: async () => {
              const src = await promptDialog("Shader code", { label: "SkSL (half4 main(float2 p)) or GLSL (mainImage / void main) — Ctrl+Enter to apply", multiline: true, value: f.definition.source || f.definition.sksl || "", confirm: "Apply" });
              if (src) act("Shader", callTool("telecode_fill_set", { node_id: node.id, index: f.index, kind: "shader", source: src, uniforms: f.definition.uniforms || {} }));
            } }) : null,
            btn("", { kind: "quiet", icon: "trash", cls: "sm", title: "Remove this fill",
              onClick: () => { stopMeshEdit(); act("Remove fill", callTool("telecode_fill_remove", { node_id: node.id, index: f.index })); } }))));
      if (f.error) box.append(h("div", { class: "td-x-err" }, "Shader error: " + f.error));
      if (f.kind === "shader") {
        const ins = f.inputs || {};
        const tags = ["time", "mouse", "backdrop", "sdf"].filter((k) => ins[k]);
        if (tags.length) box.append(h("div", { class: "faint" }, "Inputs: " + tags.map((t) => "@" + t).join(" · ")));
        for (const c of f.controls || []) box.append(controlFor(node, f, c));
      }
      out.push(box);
    }
    out.push(h("div", { class: "actions" },
      btn("Mesh gradient", { icon: "plus", kind: "ghost", cls: "sm", title: "Add a 3×3 mesh gradient on top",
        onClick: () => act("Mesh fill", callTool("telecode_fill_set", { node_id: node.id, kind: "mesh", colors: MESH_STARTER })) }),
      btn("Shader ▾", { icon: "plus", kind: "ghost", cls: "sm", title: "Add a shader fill", onClick: async (e) => {
        const el = e.currentTarget;
        let presets = [];
        try { presets = (await callTool("telecode_fill_presets", {}))?.presets || []; } catch (err) { toastError(err, "Couldn't list shader presets"); return; }
        const tag = (p) => [p.lang === "glsl" ? "GLSL" : "", p.animated ? "animated" : "", p.backdrop ? "backdrop" : "", p.sdf ? "sdf" : ""].filter(Boolean).join(" · ");
        menu(el, [{ heading: "Shader presets" }, ...presets.map((p) => ({ label: p.name, hint: [p.description, tag(p)].filter(Boolean).join(" — "),
          onClick: () => act("Shader fill", callTool("telecode_fill_set", { node_id: node.id, kind: "shader", preset: p.name })) })),
          "-", { label: "Custom shader…", icon: "code", onClick: async () => {
            const src = await promptDialog("New shader", { label: "SkSL or GLSL", multiline: true, value: STARTER_SHADER, confirm: "Add" });
            if (src) act("Shader fill", callTool("telecode_fill_set", { node_id: node.id, kind: "shader", source: src }));
          } }], { width: "300px" });
      } })));
    return out;
  }

  async function draw() {
    layerBtn.classList.toggle("on", open);
    if (!open || !selected) { if (panel) { panel.remove(); panel = null; } stopMeshEdit(); return; }
    const my = ++seq;
    const node = selected;
    const [theme, slots, fills] = await Promise.all([
      soft(callTool("telecode_theme_get", { node_id: node.id })),
      soft(callTool("telecode_slot_list", { node_id: node.id })),
      soft(callTool("telecode_fill_list", { node_id: node.id })),
    ]);
    if (my !== seq || !open || selected?.id !== node.id) return;
    if (meshEdit) {
      const f = fills?.fills?.find((x) => x.index === meshEdit.index && x.kind === "mesh");
      if (f) { meshEdit.def = JSON.parse(JSON.stringify(f.definition)); placeMesh(); } else stopMeshEdit();
    }
    const act = (label, p) => p.then(() => draw()).catch((e) => toastError(e, label + " failed"));
    const sections = [...themeSection(node, theme, act), ...slotSection(node, slots, act), ...fillSection(node, fills, act)];
    const focused = panel && panel.contains(document.activeElement) && panel.dataset.node === node.id;
    if (focused) return; // keep the control being dragged; the next selection redraws
    const el = h("section", { class: "td-dock td-extras", role: "region", "aria-label": "Layer: theme, slots and fills", dataset: { node: node.id } },
      h("header", null, icon("sliders"), h("span", { class: "grow" }, h("b", null, node.name || node.id), h("span", { class: "faint" }, " · " + String(node.type || "").toLowerCase())),
        btn("", { kind: "quiet", icon: "x", cls: "sm", title: "Close", onClick: () => { open = false; prefs.set(prefKey(pid), false); draw(); } })),
      h("div", { class: "td-x-body" }, ...(sections.length ? sections : [h("div", { class: "faint" }, "Nothing to set on this layer.")])));
    if (panel) panel.replaceWith(el); else body.appendChild(el);
    panel = el;
    place();
  }

  return {
    onSelection, onMessage, place,
    refresh: () => draw(),
    cleanup() { themeBtn.remove(); layerBtn.remove(); if (panel) panel.remove(); stopMeshEdit(); for (const el of hatches.values()) el.remove(); hatches.clear(); },
  };
}
