// TeleDesign — theme axes, slots and shader / mesh fills of canvas layers (host side).
//
// Editor side: patches/open-pencil/0016 (theme axes), 0017 (component slots), 0018 (shader
// and mesh-gradient fills). All three are canvas tools (telecode_theme_*, telecode_slot_*,
// telecode_fill_*), so this module only calls them through POST …/editor/call — the same
// path agents use through design_canvas_call.
//
//  - Theme (bar button): the document-wide mode of each axis (variable collection).
//  - Layer (bar button, a panel that follows the selection): the selected layer's mode per
//    axis (inherit / pin), its slots (make a slot, fill one with Design JSX, reset) and its
//    fills (add a mesh gradient or a shader preset, remove; a shader that does not compile
//    shows the compiler's message).
import { h, icon, btn, toast, toastError, promptDialog, menu, prefs } from "./core.js";

const CSS = `
.td-extras { position: absolute; z-index: 7; width: 296px; max-height: calc(100% - 24px); border-radius: 10px; overflow: hidden; }
.td-extras .td-x-body { overflow: auto; padding: 8px 10px 10px; display: flex; flex-direction: column; gap: 8px; font-size: 12.5px; }
.td-extras h4 { margin: 4px 0 0; font-size: 11px; letter-spacing: .04em; text-transform: uppercase; color: var(--muted, #6b7280); }
.td-extras .row { display: grid; grid-template-columns: 84px minmax(0, 1fr) auto; gap: 6px; align-items: center; }
.td-extras .row > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.td-extras .row .input { min-width: 0; width: 100%; padding: 3px 6px; font-size: 12.5px; }
.td-extras .faint { color: var(--muted, #6b7280); font-size: 11.5px; }
.td-extras .td-x-err { color: var(--err, #d92d20); background: var(--err-soft, rgba(229,72,77,.12)); border-radius: 6px; padding: 4px 6px; white-space: pre-wrap; word-break: break-word; font-size: 11.5px; }
.td-extras .actions { display: flex; flex-wrap: wrap; gap: 6px; }
`;

/** Starter mesh for "Mesh gradient": a 3×3 grid of dusk colours. */
export const MESH_STARTER = [
  ["#0b1026", "#3b2a7a", "#0b1026"],
  ["#e0567a", "#f7b267", "#3b2a7a"],
  ["#0b1026", "#e0567a", "#0b1026"],
];

const prefKey = (pid) => "canvasExtras:" + pid;

export function mountExtras({ pid, body, bar, vp, callTool }) {
  if (!document.getElementById("td-canvas-extras-css")) document.head.appendChild(h("style", { id: "td-canvas-extras-css" }, CSS));
  let open = prefs.get(prefKey(pid), false) === true;
  let selected = null;
  let panel = null;
  let seq = 0;

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

  function onSelection(nodes) {
    const one = (nodes || []).length === 1 ? nodes[0] : null;
    selected = one && one.type !== "CANVAS" ? one : null;
    draw();
  }

  function place() {
    if (!panel) return;
    const cc = vp().canvas;
    if (cc && cc.width) {
      panel.style.right = Math.max(12, body.clientWidth - (cc.left + cc.width) + 12) + "px";
      panel.style.bottom = Math.max(12, body.clientHeight - (cc.top + cc.height) + 12) + "px";
    } else { panel.style.right = "12px"; panel.style.bottom = "12px"; }
  }

  const soft = (p) => p.catch(() => null);

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

  function slotSection(node, slots, act) {
    if (!slots || slots.kind === "none") return [];
    if (slots.kind === "instance") {
      if (!slots.slots.length) return [];
      const out = [h("h4", null, "Slots")];
      for (const s of slots.slots) {
        out.push(h("div", { class: "row" }, h("span", { title: s.name }, s.name),
          h("span", { class: "faint", title: s.content_component || "" }, s.filled ? "custom · " + (s.content_component || "") : "default"),
          h("div", { class: "actions" },
            btn("Fill…", { kind: "ghost", cls: "sm", title: "Give this instance its own content (Design JSX)", onClick: async () => {
              const jsx = await promptDialog(`Fill slot "${s.name}"`, { label: "Design JSX", multiline: true, confirm: "Fill",
                placeholder: '<Frame flex="col" gap={8} p={16}><Text>Hello</Text></Frame>' });
              if (jsx) act("Fill slot", callTool("telecode_slot_fill", { instance_id: node.id, slot: s.name, jsx }, 60));
            } }),
            s.filled ? btn("Reset", { kind: "ghost", cls: "sm", title: "Back to the component's default content",
              onClick: () => act("Reset slot", callTool("telecode_slot_reset", { instance_id: node.id, slot: s.name })) }) : null)));
      }
      return out;
    }
    const out = [h("h4", null, "Slots")];
    if (slots.slots.length) out.push(h("div", { class: "faint" }, `${slots.name}: ` + slots.slots.map((s) => s.name).join(", ")));
    const isSlotItself = slots.slots.some((s) => s.node_id === node.id);
    if (node.id !== slots.node_id && !isSlotItself) {
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
    if (f.kind === "shader") return "Shader · " + (f.definition.preset || "custom SkSL");
    return String(f.type || "").toLowerCase().replace(/_/g, " ");
  }

  function fillSection(node, fills, act) {
    if (!fills || !Array.isArray(fills.fills)) return [];
    const out = [h("h4", null, "Fills")];
    for (const f of fills.fills) {
      const procedural = f.kind === "mesh" || f.kind === "shader";
      out.push(h("div", { class: "row" }, h("span", null, `#${f.index + 1}`), h("span", { title: fillLabel(f) }, fillLabel(f)),
        procedural ? btn("", { kind: "quiet", icon: "trash", cls: "sm", title: "Remove this fill",
          onClick: () => act("Remove fill", callTool("telecode_fill_remove", { node_id: node.id, index: f.index })) }) : h("span")));
      if (f.error) out.push(h("div", { class: "td-x-err" }, "Shader error: " + f.error));
    }
    out.push(h("div", { class: "actions" },
      btn("Mesh gradient", { icon: "plus", kind: "ghost", cls: "sm", title: "Add a 3×3 mesh gradient on top",
        onClick: () => act("Mesh fill", callTool("telecode_fill_set", { node_id: node.id, kind: "mesh", colors: MESH_STARTER })) }),
      btn("Shader ▾", { icon: "plus", kind: "ghost", cls: "sm", title: "Add a shader fill from a preset", onClick: async (e) => {
        const el = e.currentTarget;
        let presets = [];
        try { presets = (await callTool("telecode_fill_presets", {}))?.presets || []; } catch (err) { toastError(err, "Couldn't list shader presets"); return; }
        menu(el, [{ heading: "Shader presets" }, ...presets.map((p) => ({ label: p.name, hint: p.description,
          onClick: () => act("Shader fill", callTool("telecode_fill_set", { node_id: node.id, kind: "shader", preset: p.name })) }))], { width: "260px" });
      } })));
    return out;
  }

  async function draw() {
    layerBtn.classList.toggle("on", open);
    if (!open || !selected) { if (panel) { panel.remove(); panel = null; } return; }
    const my = ++seq;
    const node = selected;
    const [theme, slots, fills] = await Promise.all([
      soft(callTool("telecode_theme_get", { node_id: node.id })),
      soft(callTool("telecode_slot_list", { node_id: node.id })),
      soft(callTool("telecode_fill_list", { node_id: node.id })),
    ]);
    if (my !== seq || !open || selected?.id !== node.id) return;
    const act = (label, p) => p.then(() => draw()).catch((e) => toastError(e, label + " failed"));
    const sections = [...themeSection(node, theme, act), ...slotSection(node, slots, act), ...fillSection(node, fills, act)];
    const el = h("section", { class: "td-dock td-extras", role: "region", "aria-label": "Layer: theme, slots and fills", dataset: { node: node.id } },
      h("header", null, icon("sliders"), h("span", { class: "grow" }, h("b", null, node.name || node.id), h("span", { class: "faint" }, " · " + String(node.type || "").toLowerCase())),
        btn("", { kind: "quiet", icon: "x", cls: "sm", title: "Close", onClick: () => { open = false; prefs.set(prefKey(pid), false); draw(); } })),
      h("div", { class: "td-x-body" }, ...(sections.length ? sections : [h("div", { class: "faint" }, "Nothing to set on this layer.")])));
    if (panel) panel.replaceWith(el); else body.appendChild(el);
    panel = el;
    place();
  }

  return {
    onSelection, place,
    refresh: () => draw(),
    cleanup() { themeBtn.remove(); layerBtn.remove(); if (panel) panel.remove(); },
  };
}
