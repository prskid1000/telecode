// TeleDesign — home: project gallery, templates, archived, new-project dialog.
import {
  h, icon, btn, mount, api, tryApi, toast, toastError, modal, confirmDialog, promptDialog, menu, relTime,
  kindLabel, KIND_LABELS, prefs, bus, P_, skeleton, emptyState, features, safeImgSrc,
} from "./core.js";
import { S, loadProjects, loadSystems, writeUrl } from "./state.js";

let el = null;
let q = "", sort = prefs.get("sort", "updated"), layout = prefs.get("layout", "grid"), kindFilter = "";
const selected = new Set();

// Deterministic placeholder art per project — a miniature of the kind of thing it is.
function hash(s) { let x = 2166136261; for (const c of String(s)) { x ^= c.charCodeAt(0); x = Math.imul(x, 16777619); } return x >>> 0; }
const HUES = [212, 262, 32, 158, 340, 190, 48];
export function placeholderArt(id, kind) {
  const hv = hash(id), hue = HUES[hv % HUES.length];
  const acc = `oklch(0.72 0.13 ${hue})`, soft = `oklch(0.72 0.13 ${hue} / .18)`;
  const box = (x, y, w, hh, bg, r = 2) => h("i", { style: { left: x + "%", top: y + "%", width: w + "%", height: hh + "%", background: bg, borderRadius: r + "px", position: "absolute" } });
  const ink = "var(--border-strong)";
  const kids = [];
  if (kind === "slides") {
    kids.push(box(8, 12, 84, 76, "var(--bg)", 4), box(14, 26, 46, 9, acc), box(14, 42, 36, 4, ink), box(14, 49, 30, 4, ink), box(62, 26, 24, 50, soft, 3));
  } else if (kind === "mobile_app") {
    kids.push(box(36, 8, 28, 84, "var(--bg)", 8), box(40, 18, 20, 5, ink), box(40, 28, 20, 26, soft, 4), box(40, 60, 20, 4, ink), box(40, 76, 20, 8, acc, 4));
  } else if (kind === "dashboard_table") {
    kids.push(box(6, 10, 16, 80, "var(--bg)"), box(26, 10, 68, 16, soft), box(26, 30, 32, 26, "var(--bg)"), box(62, 30, 32, 26, "var(--bg)"), box(26, 60, 68, 5, ink), box(26, 69, 68, 5, ink), box(26, 78, 50, 5, ink), box(30, 38, 14, 12, acc));
  } else if (kind === "wireframe") {
    kids.push(box(8, 10, 84, 10, "transparent"), box(8, 26, 40, 30, "transparent"), box(52, 26, 40, 6, ink), box(52, 36, 34, 4, ink), box(8, 62, 26, 24, "transparent"), box(37, 62, 26, 24, "transparent"), box(66, 62, 26, 24, "transparent"));
    kids.forEach((k) => { if (k.style.background === "transparent") k.style.outline = "1.5px dashed var(--border-strong)"; });
  } else {
    kids.push(box(0, 0, 100, 12, "var(--bg)", 0), box(8, 4, 12, 4, ink), box(8, 22, 50, 10, acc), box(8, 37, 40, 4, ink), box(8, 44, 34, 4, ink), box(8, 54, 18, 8, soft, 4), box(62, 22, 30, 40, soft, 4), box(8, 72, 26, 20, "var(--bg)"), box(37, 72, 26, 20, "var(--bg)"), box(66, 72, 26, 20, "var(--bg)"));
  }
  return h("div", { class: "ph" }, kids);
}

function thumbFor(p) {
  const wrap = h("div", { class: "thumb" }, placeholderArt(p.id, p.kind));
  if (p.thumbnail) {
    const img = h("img", { alt: "", loading: "lazy", src: `${P_(p.id)}/thumbnail?t=${encodeURIComponent(p.updated_at || "")}` });
    img.onerror = () => img.remove();
    wrap.appendChild(img);
  }
  return wrap;
}

export async function renderHome(root, tab) {
  S.route = tab === "projects" ? "home" : tab;
  S.homeTab = tab;
  writeUrl();
  el = root;
  const nav = h("nav", { class: "home-nav", "aria-label": "Sections" },
    navBtn("projects", "grid", "Projects", S.projects.length || null),
    navBtn("templates", "template", "Templates"),
    navBtn("systems", "palette", "Design systems", S.systems.length || null),
    navBtn("archived", "archive", "Archived"),
    h("div", { class: "spacer" }),
    h("a", { class: "nav-btn", href: "/team" }, icon("agents"), "Agent Manager"));
  const main = h("main", { class: "home-main", id: "home-main" });
  mount(root, h("div", { class: "home" }, nav, main));
  if (tab === "systems") { const m = await import("./systems.js"); return m.renderSystems(main); }
  if (tab === "templates") return renderTemplates(main);
  if (tab === "archived") return renderArchived(main);
  return renderProjects(main);
}

function navBtn(tab, ic, label, n) {
  return h("button", { class: "nav-btn" + (S.homeTab === tab ? " on" : ""), onclick: () => bus.emit("navigate", { route: tab === "projects" ? "home" : tab }) },
    icon(ic), label, n ? h("span", { class: "n" }, n) : null);
}

// ── Projects ─────────────────────────────────────────────────────────────
async function renderProjects(main) {
  const inner = h("div", { class: "home-inner" });
  mount(main, inner);
  const listHost = h("div", null, h("div", { class: "grid" }, skeleton(8, "sk-card")));
  const search = h("input", { class: "input", type: "search", placeholder: "Search projects", value: q, id: "gallery-search", "aria-label": "Search projects" });
  search.addEventListener("input", () => { q = search.value; drawList(listHost); });
  const sortSel = h("select", { class: "select", style: { width: "150px" }, "aria-label": "Sort" },
    ["updated:Last edited", "created:Date created", "title:Name"].map((s) => { const [v, l] = s.split(":"); return h("option", { value: v, selected: sort === v || null }, l); }));
  sortSel.addEventListener("change", () => { sort = sortSel.value; prefs.set("sort", sort); drawList(listHost); });
  const kindSel = h("select", { class: "select", style: { width: "140px" }, "aria-label": "Kind" },
    h("option", { value: "" }, "All kinds"), Object.entries(KIND_LABELS).map(([k, l]) => h("option", { value: k, selected: kindFilter === k || null }, l)));
  kindSel.addEventListener("change", () => { kindFilter = kindSel.value; drawList(listHost); });
  const layoutSeg = h("div", { class: "seg icons", role: "group", "aria-label": "Layout" },
    h("button", { class: layout === "grid" ? "on" : "", title: "Grid", onclick: () => setLayout("grid") }, icon("grid")),
    h("button", { class: layout === "list" ? "on" : "", title: "List", onclick: () => setLayout("list") }, icon("list")));
  function setLayout(l) { layout = l; prefs.set("layout", l); [...layoutSeg.children].forEach((b, i) => b.classList.toggle("on", (i === 0) === (l === "grid"))); drawList(listHost); }

  inner.append(
    starterBox(),
    h("div", { class: "home-head" },
      h("div", null, h("h1", null, "Projects"), h("p", null, "Everything you've designed here, newest first.")),
      h("div", { class: "home-tools" },
        h("div", { class: "search" }, icon("search"), search), kindSel, sortSel, layoutSeg,
        btn("New project", { kind: "primary", icon: "plus", onClick: () => newProjectDialog(), kbd: "N" }))),
    listHost);
  try { await Promise.all([loadProjects(), S.systems.length ? null : loadSystems()]); }
  catch (e) { mount(listHost, emptyState("alert", "Couldn't load projects", e.message, btn("Try again", { onClick: () => renderProjects(main) }))); return; }
  drawList(listHost);
}

function starterBox() {
  let kind = "prototype";
  const ta = h("textarea", { class: "input", placeholder: "Describe what you want to design. For example: an onboarding flow for a meal-planning app, three directions.", "aria-label": "Describe your design" });
  const chips = h("div", { class: "starter-kinds" });
  const KINDS = ["prototype", "slides", "landing_page", "mobile_app", "dashboard_table", "wireframe", "one_pager", "animation"];
  const drawChips = () => mount(chips, KINDS.map((k) => h("button", { class: "kind-chip" + (k === kind ? " on" : ""), onclick: () => { kind = k; drawChips(); } }, kindLabel(k))));
  drawChips();
  const go = async () => {
    const text = ta.value.trim();
    if (!text) { ta.focus(); return; }
    const sys = S.systems.find((s) => s.is_default);
    await createAndOpen({ title: text.split(/[.\n]/)[0].slice(0, 60), kind, design_system_id: sys?.id || null }, text);
  };
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); go(); } });
  const art = h("div", { class: "starter-art" },
    miniBoard(8, 18, 38, 58, "Home — v1", 212), miniBoard(52, 10, 22, 50, "Mobile", 262), miniBoard(78, 26, 34, 44, "Pricing", 32));
  return h("section", { class: "starter" },
    h("div", { class: "starter-left" },
      h("h2", null, "What are we designing?"),
      ta, chips,
      h("div", { class: "row" }, h("span", { class: "muted", style: { fontSize: "11.5px", flex: 1 } }, "The agent may ask a few questions before it starts."),
        btn("Advanced", { kind: "ghost", onClick: () => newProjectDialog({ prompt: ta.value, kind }) }),
        btn("Start designing", { kind: "primary", icon: "sparkle", onClick: go, kbd: "Ctrl ↵" }))),
    h("div", { class: "starter-right", "aria-hidden": "true" }, art));
}
function miniBoard(x, y, w, hh, label, hue) {
  const acc = `oklch(0.72 0.13 ${hue})`;
  const i = (l, t, ww, hhh, bg) => h("i", { style: { left: l + "%", top: t + "%", width: ww + "%", height: hhh + "%", background: bg } });
  return [h("span", { class: "lbl", style: { left: x + "%", top: `calc(${y}% - 16px)` } }, label),
    h("div", { class: "b", style: { left: x + "%", top: y + "%", width: w + "%", height: hh + "%" } },
      i(8, 10, 50, 10, acc), i(8, 26, 70, 5, "var(--border-strong)"), i(8, 35, 56, 5, "var(--border-strong)"), i(8, 50, 84, 38, `oklch(0.72 0.13 ${hue} / .16)`))];
}

function filtered() {
  const ql = q.trim().toLowerCase();
  let list = S.projects.filter((p) => (!ql || (p.title || "").toLowerCase().includes(ql)) && (!kindFilter || p.kind === kindFilter));
  const key = sort === "title" ? (p) => (p.title || "").toLowerCase() : sort === "created" ? (p) => p.created_at || "" : (p) => p.updated_at || "";
  list = list.slice().sort((a, b) => (key(a) < key(b) ? 1 : key(a) > key(b) ? -1 : 0) * (sort === "title" ? -1 : 1));
  return list;
}

function drawList(host) {
  const list = filtered();
  if (!S.projects.length) {
    mount(host, emptyState("sparkle", "No projects yet", "Describe a design above, or start from a template.",
      h("div", { class: "row" }, btn("New project", { kind: "primary", icon: "plus", onClick: () => newProjectDialog() }),
        btn("Browse templates", { onClick: () => bus.emit("navigate", { route: "templates" }) }))));
    return;
  }
  if (!list.length) { mount(host, emptyState("search", "No matches", "Try a different search or kind.")); return; }
  const sysName = (id) => S.systems.find((s) => s.id === id)?.name;
  let body;
  if (layout === "list") {
    body = h("div", { class: "list-view" },
      h("div", { class: "list-row list-head" }, h("span"), h("span"), h("span", null, "Name"), h("span", null, "Kind"), h("span", null, "Design system"), h("span", null, "Edited"), h("span")),
      list.map((p) => {
        const row = h("div", { class: "list-row", tabindex: 0, onclick: (e) => { if (!e.target.closest("button,input")) open(p); }, onkeydown: (e) => { if (e.key === "Enter") open(p); } },
          selBox(p, host), h("div", { class: "mini" }, thumbFor(p).firstChild, p.thumbnail ? thumbFor(p).lastChild : null),
          h("span", { class: "card-title" }, p.title), h("span", { class: "muted" }, kindLabel(p.kind)),
          h("span", { class: "muted ellipsis" }, sysName(p.design_system_id) || "—"), h("span", { class: "faint" }, relTime(p.updated_at)),
          btn("", { kind: "quiet", icon: "more", title: "Project actions", cls: "sm", onClick: (e) => projectMenu(e.currentTarget, p, host) }));
        return row;
      }));
  } else {
    body = h("div", { class: "grid" + (selected.size ? " multi" : "") }, list.map((p) => {
      const card = h("article", { class: "card" + (selected.has(p.id) ? " selected" : ""), tabindex: 0,
        onclick: (e) => { if (e.target.closest("button,input")) return; if (e.shiftKey || e.ctrlKey || e.metaKey || selected.size) { toggleSel(p, host); } else open(p); },
        onkeydown: (e) => { if (e.key === "Enter") open(p); } },
        thumbFor(p),
        h("div", { class: "card-check" }, selBox(p, host)),
        btn("", { kind: "quiet", icon: "more", title: "Project actions", cls: "sm card-menu", onClick: (e) => projectMenu(e.currentTarget, p, host) }),
        h("div", { class: "card-body" },
          h("div", { class: "card-title", title: p.title }, p.title),
          h("div", { class: "card-meta" }, h("span", null, kindLabel(p.kind)), sysName(p.design_system_id) ? h("span", { class: "ellipsis" }, "· " + sysName(p.design_system_id)) : null,
            h("span", { style: { marginLeft: "auto", flexShrink: 0 } }, relTime(p.updated_at)))));
      return card;
    }));
  }
  const bulk = selected.size ? h("div", { class: "bulkbar" }, h("span", null, `${selected.size} selected`),
    btn("Archive", { icon: "archive", onClick: () => bulkArchive(host) }),
    btn("Delete", { kind: "danger", icon: "trash", onClick: () => bulkDelete(host) }),
    btn("", { kind: "quiet", icon: "x", title: "Clear selection", onClick: () => { selected.clear(); drawList(host); } })) : null;
  mount(host, body, bulk);
}
function selBox(p, host) {
  return h("input", { type: "checkbox", class: "check", checked: selected.has(p.id), "aria-label": "Select " + p.title, onclick: (e) => { e.stopPropagation(); toggleSel(p, host); } });
}
function toggleSel(p, host) { selected.has(p.id) ? selected.delete(p.id) : selected.add(p.id); drawList(host); }
async function bulkArchive(host) {
  for (const id of selected) await api("PATCH", `/api/design/projects/${id}`, { archived: true }).catch(toastError);
  toast(`Archived ${selected.size} project${selected.size > 1 ? "s" : ""}`, { kind: "success" });
  selected.clear(); await loadProjects(); drawList(host);
}
async function bulkDelete(host) {
  if (!await confirmDialog("Delete projects", `Delete ${selected.size} project(s) and all their files? This can't be undone.`, { confirm: "Delete", danger: true })) return;
  for (const id of selected) await api("DELETE", `/api/design/projects/${id}`).catch(toastError);
  toast("Deleted", { kind: "success" }); selected.clear(); await loadProjects(); drawList(host);
}

const open = (p) => bus.emit("navigate", { route: "project", id: p.id });

function projectMenu(anchor, p, host) {
  menu(anchor, [
    { label: "Open", icon: "external", onClick: () => open(p) },
    { label: "Rename", icon: "edit", onClick: async () => {
      const t = await promptDialog("Rename project", { value: p.title, confirm: "Rename" });
      if (!t) return;
      await api("PATCH", `/api/design/projects/${p.id}`, { title: t, title_locked: true }).catch(toastError);
      await loadProjects(); drawList(host);
    } },
    features.templates !== false ? { label: "Save as template", icon: "template", onClick: () => saveAsTemplate(p) } : null,
    { label: "Copy link", icon: "link", onClick: () => navigator.clipboard?.writeText(`${location.origin}/design?project=${p.id}`).then(() => toast("Link copied", { kind: "success" })) },
    "-",
    { label: "Archive", icon: "archive", onClick: async () => {
      await api("PATCH", `/api/design/projects/${p.id}`, { archived: true }).catch(toastError);
      toast("Archived", { kind: "success", action: { label: "Undo", run: async () => { await api("PATCH", `/api/design/projects/${p.id}`, { archived: false }); await loadProjects(); drawList(host); } } });
      await loadProjects(); drawList(host);
    } },
    { label: "Delete", icon: "trash", danger: true, onClick: async () => {
      if (!await confirmDialog("Delete project", `Delete "${p.title}" and all its files? This can't be undone.`, { confirm: "Delete", danger: true })) return;
      await api("DELETE", `/api/design/projects/${p.id}`).catch(toastError);
      await loadProjects(); drawList(host);
    } },
  ], { align: "right" });
}

export async function saveAsTemplate(p) {
  const name = h("input", { class: "input", value: p.title, autofocus: true });
  const intro = h("textarea", { class: "input", rows: 4, placeholder: "What this template is for and how to use it. Shown when someone starts from it." });
  modal({
    title: "Save as template", subtitle: "Others start new projects from a copy of this one.", width: "460px",
    body: h("div", null, h("label", { class: "field" }, h("span", { class: "field-label" }, "Name"), name),
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Intro text"), intro)),
    actions: [{ label: "Cancel", kind: "ghost" }, { label: "Save template", kind: "primary", onClick: async () => {
      const r = await tryApi("POST", "/api/design/templates", { project_id: p.id, name: name.value.trim() || p.title, intro_text: intro.value.trim(), ...(p.thumbnail ? { cover: "thumbnail.webp" } : {}) }, { feature: "templates" });
      if (!r) { toast("Templates aren't available on this server yet.", { kind: "error" }); return; }
      toast("Template saved", { kind: "success" });
    } }],
  });
}

// ── Templates ────────────────────────────────────────────────────────────
async function renderTemplates(main) {
  const inner = h("div", { class: "home-inner" },
    h("div", { class: "home-head" }, h("div", null, h("h1", null, "Templates"), h("p", null, "Start from a saved project. You get your own copy."))));
  const host = h("div", { class: "grid" }, skeleton(4, "sk-card"));
  inner.appendChild(host);
  mount(main, inner);
  const j = await tryApi("GET", "/api/design/templates", undefined, { feature: "templates" }).catch((e) => { toastError(e); return { templates: [] }; });
  if (!j) { host.className = ""; mount(host, emptyState("template", "Templates aren't available yet", "This server doesn't have the templates API. Save-as-template will appear once it does.")); return; }
  const list = j.templates || [];
  S.templates = list;
  if (!list.length) { host.className = ""; mount(host, emptyState("template", "No templates yet", "Open a project, then choose Save as template from its menu.")); return; }
  mount(host, list.map((t) => {
    const cover = t.cover ? `/api/design/templates/${encodeURIComponent(t.id)}/cover` : null;
    return h("article", { class: "card", tabindex: 0, onclick: () => useTemplate(t) },
      h("div", { class: "thumb" }, placeholderArt(t.id, t.kind), cover ? h("img", { src: cover, alt: "" }) : null),
      h("div", { class: "card-body" }, h("div", { class: "card-title" }, t.name || "Untitled template"),
        h("div", { class: "card-meta" }, h("span", { class: "ellipsis" }, t.intro_text || kindLabel(t.kind)))));
  }));
}
async function useTemplate(t) {
  modal({
    title: t.name || "Template", width: "480px",
    body: h("div", null, t.intro_text ? h("p", { style: { whiteSpace: "pre-wrap", margin: 0 } }, t.intro_text) : h("p", { class: "muted" }, "No description.")),
    actions: [{ label: "Cancel", kind: "ghost" }, { label: "Use template", kind: "primary", onClick: async () => {
      const r = await api("POST", `/api/design/templates/${encodeURIComponent(t.id)}/instantiate`, {});
      const pid = r.project?.id || r.project_id;
      if (pid) bus.emit("navigate", { route: "project", id: pid });
    } }],
  });
}

// ── Archived ─────────────────────────────────────────────────────────────
async function renderArchived(main) {
  const host = h("div", null, skeleton(4, "sk-block"));
  mount(main, h("div", { class: "home-inner" }, h("div", { class: "home-head" }, h("div", null, h("h1", null, "Archived"), h("p", null, "Hidden from the gallery. Restore to bring one back."))), host));
  const all = await loadProjects(true).catch((e) => { toastError(e); return []; });
  const list = all.filter((p) => p.archived);
  if (!list.length) { mount(host, emptyState("archive", "Nothing archived", "Archived projects show up here.")); return; }
  mount(host, h("div", { class: "list-view" }, list.map((p) => h("div", { class: "list-row", style: { gridTemplateColumns: "64px 1fr 140px 130px auto" } },
    h("div", { class: "mini" }, placeholderArt(p.id, p.kind)), h("span", { class: "card-title" }, p.title), h("span", { class: "muted" }, kindLabel(p.kind)),
    h("span", { class: "faint" }, relTime(p.updated_at)),
    h("div", { class: "row" }, btn("Restore", { icon: "restore", cls: "sm", onClick: async () => { await api("PATCH", `/api/design/projects/${p.id}`, { archived: false }); toast("Restored", { kind: "success" }); renderArchived(main); } }),
      btn("", { kind: "quiet", icon: "trash", cls: "sm", title: "Delete", onClick: async () => {
        if (!await confirmDialog("Delete project", `Delete "${p.title}" for good?`, { confirm: "Delete", danger: true })) return;
        await api("DELETE", `/api/design/projects/${p.id}`); renderArchived(main);
      } }))))));
}

// ── New project dialog ───────────────────────────────────────────────────
const KIND_ART = {
  prototype: [[8, 14, 50, 14, 1], [8, 38, 38, 8], [8, 52, 30, 8], [62, 14, 30, 60]],
  slides: [[10, 16, 80, 68], [18, 30, 44, 12, 1], [18, 50, 30, 8]],
  wireframe: [[8, 12, 84, 12], [8, 32, 40, 52], [52, 32, 40, 22], [52, 60, 40, 24]],
  one_pager: [[30, 6, 40, 88], [36, 14, 28, 10, 1], [36, 30, 28, 4], [36, 38, 22, 4]],
  animation: [[10, 40, 18, 22, 1], [40, 30, 18, 32], [70, 20, 18, 42]],
  landing_page: [[0, 0, 100, 14], [10, 24, 50, 14, 1], [10, 44, 36, 6], [10, 60, 18, 12], [64, 24, 28, 50]],
  mobile_app: [[36, 6, 28, 88], [41, 18, 18, 22, 1], [41, 46, 18, 6], [41, 72, 18, 12]],
  web_app: [[0, 0, 20, 100], [26, 10, 66, 10], [26, 28, 30, 30, 1], [60, 28, 32, 30], [26, 64, 66, 26]],
  dashboard_table: [[8, 10, 26, 26, 1], [38, 10, 26, 26], [68, 10, 24, 26], [8, 44, 84, 8], [8, 58, 84, 8], [8, 72, 84, 8]],
  design_system: [[8, 14, 18, 18, 1], [30, 14, 18, 18], [52, 14, 18, 18], [8, 44, 60, 8], [8, 60, 40, 8]],
  other: [[20, 20, 60, 60, 1]],
};
function kindArt(k) {
  return h("div", { class: "art" }, (KIND_ART[k] || KIND_ART.other).map(([x, y, w, hh, a]) =>
    h("i", { class: a ? "a" : "", style: { left: x + "%", top: y + "%", width: w + "%", height: hh + "%" } })));
}
export function swatchesFor(sys) {
  const sw = sys.swatches || sys.preview?.swatches;
  const colors = Array.isArray(sw) && sw.length ? sw : null;
  const el = h("div", { class: "swatches" });
  if (colors) colors.slice(0, 6).forEach((c) => el.appendChild(h("i", { style: { background: String(c) } })));
  else loadSwatches(sys.id).then((cs) => cs.forEach((c) => el.appendChild(h("i", { style: { background: c } }))));
  return el;
}
const swCache = new Map();
export async function loadSwatches(sid) {
  if (swCache.has(sid)) return swCache.get(sid);
  const p = (async () => {
    const t = await tryApi("GET", `/api/design/systems/${sid}/files/tokens.json`, undefined, { feature: "systemFiles", as: "text" }).catch(() => null);
    if (!t) return ["var(--panel2)", "var(--border)", "var(--border-strong)"];
    try {
      const j = JSON.parse(t);
      const theme = j.defaultTheme || (j.themes || [])[0];
      const c = (theme && j.color?.[theme]) || j.color || {};
      const pick = ["background", "foreground", "primary", "accent", "muted", "border", "secondary"];
      const out = [];
      for (const k of pick) { const v = c[k]; const val = v && (v.hex || v.value || (typeof v === "string" ? v : null)); if (val && out.length < 5) out.push(String(val)); }
      return out.length ? out : ["var(--panel2)"];
    } catch { return ["var(--panel2)"]; }
  })();
  swCache.set(sid, p);
  return p;
}

let stylesCache = null;
async function loadStyles() {
  if (stylesCache) return stylesCache;
  const j = await tryApi("GET", "/api/design/styles", undefined, { feature: "styles" }).catch(() => null);
  stylesCache = j ? (j.styles || j) : [];
  return stylesCache;
}

export async function newProjectDialog(init = {}) {
  if (!S.systems.length) await loadSystems().catch(() => {});
  let kind = init.kind || "prototype";
  let sysId = init.systemId !== undefined ? init.systemId : (S.systems.find((s) => s.is_default)?.id || "");
  let styleId = null, templateId = null;
  const title = h("input", { class: "input", placeholder: "Untitled design", value: init.title || "", autofocus: true });
  const prompt = h("textarea", { class: "input", rows: 3, placeholder: "Optional — describe it and the agent starts right away." }, init.prompt || "");
  const kinds = h("div", { class: "kind-grid" });
  const drawKinds = () => mount(kinds, Object.keys(KIND_LABELS).map((k) => h("button", { type: "button", class: "kind-card" + (k === kind ? " on" : ""), onclick: () => { kind = k; drawKinds(); } }, kindArt(k), h("span", null, kindLabel(k)))));
  drawKinds();
  const sysPick = h("div", { class: "sys-pick" });
  const styleWrap = h("div", { class: "field" });
  const drawSys = () => {
    mount(sysPick,
      h("button", { type: "button", class: "sys-opt" + (!sysId ? " on" : ""), onclick: () => { sysId = ""; drawSys(); } },
        h("span", { class: "nm" }, "No system"), h("span", { class: "faint", style: { fontSize: "11px" } }, "Pick a direction instead")),
      S.systems.filter((s) => s.status !== "failed").map((s) => h("button", { type: "button", class: "sys-opt" + (sysId === s.id ? " on" : ""), onclick: () => { sysId = s.id; drawSys(); } },
        h("span", { class: "nm" }, s.name, s.is_default ? icon("star") : null), swatchesFor(s))));
    styleWrap.classList.toggle("hidden", !!sysId);
  };
  drawSys();
  // Direction picker (style archetypes) — only without a system.
  loadStyles().then((styles) => {
    if (!styles.length) { styleWrap.remove(); return; }
    const grid = h("div", { class: "style-grid" });
    const draw = () => mount(grid, styles.map((st) => {
      const pal = st.palette || {};
      return h("button", { type: "button", class: "style-opt" + (styleId === st.id ? " on" : ""), title: st.description || "", onclick: () => { styleId = styleId === st.id ? null : st.id; draw(); } },
        h("div", { class: "sw", style: { background: pal.background || "#fff", color: pal.foreground || "#111" } },
          h("b", { style: { fontFamily: `'${(st.fonts || {}).display || "serif"}', serif` } }, "Aa"),
          h("i", { style: { width: "10px", height: "10px", borderRadius: "3px", background: pal.accent || "#888", display: "block" } }),
          h("i", { style: { width: "10px", height: "10px", borderRadius: "3px", background: pal.muted || "#aaa", display: "block" } })),
        h("div", { class: "nm" }, st.name));
    }));
    draw();
    mount(styleWrap, h("span", { class: "field-label" }, "Direction ", h("span", { class: "faint" }, "— optional; the agent can suggest one")), grid);
  });
  // Templates
  const tplWrap = h("div", { class: "field hidden" });
  tryApi("GET", "/api/design/templates", undefined, { feature: "templates" }).then((j) => {
    const list = j?.templates || [];
    if (!list.length) return;
    const sel = h("select", { class: "select" }, h("option", { value: "" }, "Blank project"), list.map((t) => h("option", { value: t.id }, t.name)));
    sel.addEventListener("change", () => { templateId = sel.value || null; });
    mount(tplWrap, h("span", { class: "field-label" }, "Start from"), sel);
    tplWrap.classList.remove("hidden");
  }).catch(() => {});

  modal({
    title: "New project", width: "720px",
    body: h("div", null,
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Name"), title),
      h("div", { class: "field" }, h("span", { class: "field-label" }, "What are you making?"), kinds),
      h("div", { class: "field" }, h("span", { class: "field-label" }, "Design system"), sysPick),
      styleWrap, tplWrap,
      h("label", { class: "field" }, h("span", { class: "field-label" }, "Brief"), prompt)),
    actions: [
      { label: "Cancel", kind: "ghost" },
      { label: "Create project", kind: "primary", onClick: async () => {
        if (templateId) {
          const r = await api("POST", `/api/design/templates/${encodeURIComponent(templateId)}/instantiate`, { title: title.value.trim() || undefined });
          const pid = r.project?.id || r.project_id;
          if (pid) bus.emit("navigate", { route: "project", id: pid, firstPrompt: prompt.value.trim() || null });
          return;
        }
        await createAndOpen({ title: title.value.trim() || "Untitled design", kind, design_system_id: sysId || null, style_id: styleId || undefined }, prompt.value.trim());
      } },
    ],
  });
}

export async function createAndOpen(body, firstPrompt) {
  try {
    const { project } = await api("POST", "/api/design/projects", body);
    S.projects.unshift(project);
    bus.emit("navigate", { route: "project", id: project.id, firstPrompt: firstPrompt || null, styleId: body.style_id || null });
  } catch (e) { toastError(e, "Couldn't create the project"); }
}
