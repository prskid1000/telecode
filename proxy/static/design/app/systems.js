// TeleDesign — Design systems browser: list, detail (overview, specimens by
// group, tokens, components, fonts), create / extract / import / publish /
// default / remix / clean up / try / export / delete.
import {
  h, icon, btn, mount, clear, api, tryApi, toast, toastError, modal, confirmDialog, promptDialog, menu, bus, relTime,
  renderMarkdown, skeleton, emptyState, features, ApiMissing,
} from "./core.js";
import { S, loadSystems, writeUrl, dsysUrl } from "./state.js";
import { swatchesFor } from "./gallery.js";

const SYS = (id) => `/api/design/systems/${encodeURIComponent(id)}`;
const fileCache = new Map();
async function sysFile(id, path, as = "text") {
  const k = id + "|" + path;
  if (fileCache.has(k)) return fileCache.get(k);
  const p = tryApi("GET", `${SYS(id)}/files/${path.split("/").map(encodeURIComponent).join("/")}`, undefined, { feature: "systemFiles", as }).catch(() => null);
  fileCache.set(k, p);
  return p;
}
async function sysJson(id, path) { const t = await sysFile(id, path); if (typeof t !== "string") return null; try { return JSON.parse(t); } catch { return null; } }

export async function renderSystems(main) {
  S.route = "systems";
  const params = new URLSearchParams(location.search);
  const sid = S.systemId || params.get("system");
  if (sid) return renderDetail(main, sid);
  S.systemId = null; writeUrl();
  const grid = h("div", { class: "grid" }, skeleton(5, "sk-card"));
  mount(main, h("div", { class: "home-inner" },
    h("div", { class: "home-head" }, h("div", null, h("h1", null, "Design systems"), h("p", null, "Tokens, components and rules the agent designs with. One is the default for new projects.")),
      h("div", { class: "home-tools" },
        btn("Import", { icon: "upload", onClick: () => importDialog(main) }),
        btn("New system", { kind: "primary", icon: "plus", onClick: () => createDialog(main) }))),
    grid));
  try { await loadSystems(); } catch (e) { mount(grid, emptyState("alert", "Couldn't load design systems", e.message)); return; }
  if (!S.systems.length) { grid.className = ""; mount(grid, emptyState("palette", "No design systems", "Create one from a codebase, a website or a brand PDF.", btn("New system", { kind: "primary", icon: "plus", onClick: () => createDialog(main) }))); return; }
  const sorted = S.systems.slice().sort((a, b) => (b.is_default - a.is_default) || (a.name || "").localeCompare(b.name || ""));
  mount(grid, sorted.map((s) => {
    const art = h("div", { class: "thumb" });
    paintThumb(art, s);
    return h("article", { class: "card sys-card", tabindex: 0, onclick: (e) => { if (!e.target.closest("button")) openSystem(main, s.id); }, onkeydown: (e) => { if (e.key === "Enter") openSystem(main, s.id); } },
      art,
      btn("", { kind: "quiet", icon: "more", cls: "sm card-menu", title: "Actions", onClick: (e) => systemMenu(e.currentTarget, s, main) }),
      h("div", { class: "card-body" },
        h("div", { class: "row", style: { gap: "6px" } }, h("span", { class: "card-title grow" }, s.name), s.is_default ? h("span", { class: "pill accent" }, icon("star"), "Default") : null, statusPill(s.status)),
        h("div", { class: "card-meta" }, h("span", { class: "ellipsis" }, s.description || (s.seed ? "Bundled with TeleDesign" : "Updated " + relTime(s.updated_at))))));
  }));
}
function statusPill(st) {
  if (!st || st === "published") return null;
  return h("span", { class: "pill " + (st === "failed" ? "err" : st === "extracting" ? "accent" : "warn") }, st === "extracting" ? "Extracting" : st === "draft" ? "Draft" : st);
}
async function paintThumb(el, s) {
  const tokens = await sysJson(s.id, "tokens.json");
  const theme = tokens?.defaultTheme || "light";
  const c = tokens?.color?.[theme] || {};
  const val = (k) => c[k]?.hex || c[k]?.value || null;
  const fam = tokens?.typography?.fontFamily?.display?.family || tokens?.typography?.fontFamily?.body?.family;
  el.style.background = val("background") || "var(--panel2)";
  el.style.color = val("foreground") || "var(--text)";
  mount(el, h("div", { class: "aa", style: { fontFamily: fam ? `'${fam}', serif` : "inherit" } }, "Aa", h("span", { style: { fontSize: "12px", fontWeight: 400, marginLeft: "10px", opacity: .7, fontFamily: "var(--sans)" } }, fam || "")),
    swatchesFor(s));
  if (fam) loadFont(fam);
}
const loadedFonts = new Set();
function loadFont(fam) {
  if (loadedFonts.has(fam) || !/^[\w .-]{2,60}$/.test(fam)) return;
  loadedFonts.add(fam);
  const l = document.createElement("link");
  l.rel = "stylesheet"; l.href = `https://fonts.googleapis.com/css2?family=${encodeURIComponent(fam).replace(/%20/g, "+")}:wght@400;600&display=swap`;
  document.head.appendChild(l);
}
function openSystem(main, id) { S.systemId = id; writeUrl(true); renderDetail(main, id); }

function systemMenu(anchor, s, main) {
  menu(anchor, [
    { label: "Open", icon: "external", onClick: () => openSystem(main, s.id) },
    !s.is_default ? { label: "Make default", icon: "star", onClick: () => act(s, "default", main) } : null,
    { label: s.status === "published" ? "Unpublish" : "Publish", icon: "checkCircle", onClick: () => act(s, "publish", main) },
    { label: "Try it in a scratch project", icon: "sparkle", onClick: () => act(s, "try", main) },
    { label: "Remix", icon: "copy", hint: "A new chat that edits a copy", onClick: () => act(s, "remix", main) },
    { label: "Clean it up", icon: "wand", hint: "The agent tidies an imported system", onClick: () => act(s, "cleanup", main) },
    { label: "Export ZIP", icon: "download", onClick: () => { location.href = `${SYS(s.id)}/export`; } },
    "-",
    { label: "Delete", icon: "trash", danger: true, onClick: async () => {
      if (!await confirmDialog("Delete design system", `Delete "${s.name}"? Projects using it keep their staged copy.`, { confirm: "Delete", danger: true })) return;
      await api("DELETE", SYS(s.id)).catch(toastError); S.systemId = null; await loadSystems(); renderSystems(main);
    } },
  ], { align: "right", width: "250px" });
}
async function act(s, what, main) {
  try {
    let r;
    if (what === "publish") {
      const target = s.status === "published" ? "draft" : "published";
      r = await tryApi("POST", `${SYS(s.id)}/publish`, { published: target === "published" }, { feature: "sysPublish" });
      if (r === null) r = await api("PATCH", SYS(s.id), { status: target });
      toast(target === "published" ? "Published" : "Moved back to draft", { kind: "success" });
    } else if (what === "default") {
      r = await tryApi("POST", `${SYS(s.id)}/default`, {}, { feature: "sysDefault" });
      if (r === null) r = await api("PATCH", SYS(s.id), { is_default: true });
      toast(`${s.name} is now the default`, { kind: "success" });
    } else {
      r = await api("POST", `${SYS(s.id)}/${what}`, {});
      const pid = r.project_id || r.project?.id;
      if (pid) { bus.emit("navigate", { route: "project", id: pid, chat: r.chat_id || null }); return; }
      toast("Started", { kind: "success" });
    }
    await loadSystems();
    if (S.systemId) renderDetail(main, S.systemId); else renderSystems(main);
  } catch (e) {
    if (e instanceof ApiMissing) toast("That action isn't available on this server yet.", { kind: "error" });
    else toastError(e);
  }
}

// ── Create / extract / import ────────────────────────────────────────────
function createDialog(main) {
  const name = h("input", { class: "input", placeholder: "Acme product UI", autofocus: true });
  const rows = h("div", { class: "col" });
  const TYPES = [["codebase", "Local codebase", "C:\\code\\acme-web"], ["github", "GitHub repo", "https://github.com/acme/web/tree/main/src"], ["url", "Website", "https://acme.com"], ["files", "Uploaded files", "brand.pdf, screenshots"], ["screenshots", "Screenshots", ""]];
  const sources = [{ type: "codebase", ref: "" }];
  const drawRows = () => mount(rows, sources.map((s, i) => {
    const sel = h("select", { class: "select", style: { width: "160px" } }, TYPES.map(([v, l]) => h("option", { value: v, selected: s.type === v || null }, l)));
    const ref = h("input", { class: "input", value: s.ref, placeholder: TYPES.find((t) => t[0] === s.type)?.[2] || "" });
    const file = h("input", { type: "file", multiple: true, class: "hidden" });
    sel.addEventListener("change", () => { s.type = sel.value; drawRows(); });
    ref.addEventListener("input", () => (s.ref = ref.value));
    file.addEventListener("change", () => { s.files = [...file.files]; s.ref = s.files.map((f) => f.name).join(", "); drawRows(); });
    const needsFiles = s.type === "files" || s.type === "screenshots";
    return h("div", { class: "row" }, sel, needsFiles ? h("div", { class: "row grow" }, btn(s.files?.length ? `${s.files.length} file(s)` : "Choose files", { icon: "upload", cls: "sm", onClick: () => file.click() }), file, h("span", { class: "faint ellipsis grow", style: { fontSize: "11.5px" } }, s.ref)) : ref,
      sources.length > 1 ? btn("", { kind: "quiet", icon: "x", cls: "sm", title: "Remove", onClick: () => { sources.splice(i, 1); drawRows(); } }) : null);
  }));
  drawRows();
  modal({
    title: "New design system", subtitle: "Point the agent at real sources; it extracts exact tokens, writes DESIGN.md and builds specimen cards for review.", width: "620px",
    body: h("div", null, h("label", { class: "field" }, h("span", { class: "field-label" }, "Name"), name),
      h("div", { class: "field" }, h("span", { class: "field-label" }, "Sources"), rows, h("div", null, btn("Add source", { kind: "ghost", icon: "plus", cls: "sm", onClick: () => { sources.push({ type: "url", ref: "" }); drawRows(); } })))),
    actions: [
      { label: "Cancel", kind: "ghost" },
      { label: "Create empty", onClick: async () => { const r = await api("POST", "/api/design/systems", { name: name.value.trim() || "Untitled system" }); await loadSystems(); S.systemId = r.system.id; writeUrl(); renderDetail(main, r.system.id); } },
      { label: "Create and extract", kind: "primary", icon: "sparkle", onClick: async () => {
        const nm = name.value.trim() || "Untitled system";
        const valid = sources.filter((s) => s.ref.trim() || s.files?.length);
        if (!valid.length) { toast("Add at least one source, or choose Create empty.", { kind: "error" }); return false; }
        const r = await api("POST", "/api/design/systems", { name: nm, sources: valid.map(({ type, ref }) => ({ type, ref })) });
        const sid = r.system.id;
        // Uploaded files go to the extraction project once it exists; pass names for now.
        let x;
        try { x = await api("POST", `${SYS(sid)}/extract`, { sources: valid.map(({ type, ref }) => ({ type, ref })) }, { feature: "sysExtract" }); }
        catch (e) {
          if (e instanceof ApiMissing) { toast("Created. Extraction isn't available on this server yet.", { kind: "error" }); await loadSystems(); S.systemId = sid; renderDetail(main, sid); return; }
          throw e;
        }
        if (x.project_id) {
          const files = valid.flatMap((s) => s.files || []);
          if (files.length) { const fd = new FormData(); files.forEach((f) => fd.append("file", f, f.name)); await tryApi("POST", `/api/design/projects/${x.project_id}/uploads`, fd).catch(() => null); }
          bus.emit("navigate", { route: "project", id: x.project_id, chat: x.chat_id || null });
        }
      } },
    ],
  });
}
function importDialog(main) {
  const inp = h("input", { type: "file", accept: ".zip", class: "input", style: { height: "auto", padding: "6px" } });
  modal({ title: "Import a design system", subtitle: "A TeleDesign export, or a Claude Design _ds/ folder zipped.", width: "460px",
    body: h("div", null, inp),
    actions: [{ label: "Cancel", kind: "ghost" }, { label: "Import", kind: "primary", icon: "upload", onClick: async () => {
      if (!inp.files[0]) { toast("Choose a .zip file", { kind: "error" }); return false; }
      const fd = new FormData(); fd.append("file", inp.files[0], inp.files[0].name);
      try { const r = await api("POST", "/api/design/systems/import", fd, { feature: "sysImport" }); await loadSystems(); toast("Imported", { kind: "success" }); if (r.system?.id) { S.systemId = r.system.id; renderDetail(main, r.system.id); } else renderSystems(main); }
      catch (e) { if (e instanceof ApiMissing) toast("Import isn't available on this server yet.", { kind: "error" }); else throw e; }
    } }] });
}

// ── Detail ───────────────────────────────────────────────────────────────
const GROUP_ORDER = ["Type", "Typography", "Colors", "Colour", "Spacing", "Components", "Brand"];
async function renderDetail(main, sid) {
  S.systemId = sid; writeUrl();
  const inner = h("div", { class: "home-inner sys-detail" }, skeleton(6));
  mount(main, inner);
  if (!S.systems.length) await loadSystems().catch(() => {});
  let s = S.systems.find((x) => x.id === sid);
  if (!s) { const r = await tryApi("GET", SYS(sid)).catch(() => null); s = r?.system; }
  if (!s) { mount(inner, emptyState("palette", "Design system not found", "", btn("All systems", { icon: "back", onClick: () => { S.systemId = null; renderSystems(main); } }))); return; }
  const [manifest, tokens] = await Promise.all([sysJson(sid, "manifest.json"), sysJson(sid, "tokens.json")]);
  let tab = new URLSearchParams(location.search).get("tab") || "overview";
  const tabs = h("div", { class: "subtabs", role: "tablist" });
  const body = h("div");
  const TABS = [["overview", "Overview"], ["specimens", "Specimens"], ["tokens", "Tokens"], ["components", "Components"], ["fonts", "Fonts"]];
  const drawTabs = () => mount(tabs, TABS.map(([k, l]) => h("button", { class: "subtab" + (tab === k ? " on" : ""), role: "tab", onclick: () => { tab = k; drawTabs(); drawBody(); } }, l)));
  mount(inner,
    h("button", { class: "btn quiet sm", style: { marginBottom: "10px", alignSelf: "flex-start", marginLeft: "-8px" }, onclick: () => { S.systemId = null; writeUrl(true); renderSystems(main); } }, icon("back"), "All systems"),
    h("div", { class: "sys-hero" },
      h("div", { class: "grow" }, h("h1", null, s.name, s.is_default ? h("span", { class: "pill accent" }, icon("star"), "Default") : null, statusPill(s.status) || h("span", { class: "pill ok" }, "Published")),
        h("p", null, s.description || "No description yet."),
        h("div", { class: "row", style: { marginTop: "8px", gap: "12px", fontSize: "12px", color: "var(--faint)" } },
          s.version ? h("span", null, "v" + s.version) : null, s.license ? h("span", { class: "ellipsis", style: { maxWidth: "420px" } }, s.license) : null, h("span", null, "Updated " + relTime(s.updated_at)))),
      h("div", { class: "row" },
        btn("Try it", { icon: "sparkle", onClick: () => act(s, "try", main) }),
        btn("Remix", { icon: "copy", onClick: () => act(s, "remix", main) }),
        !s.is_default ? btn("Make default", { icon: "star", onClick: () => act(s, "default", main) }) : null,
        btn(s.status === "published" ? "Unpublish" : "Publish", { kind: s.status === "published" ? "" : "primary", onClick: () => act(s, "publish", main) }),
        btn("", { kind: "quiet", icon: "more", title: "More", onClick: (e) => systemMenu(e.currentTarget, s, main) }))),
    tabs, body);
  drawTabs();
  function drawBody() {
    const u = new URL(location.href); u.searchParams.set("tab", tab); history.replaceState(null, "", u);
    clear(body);
    if (tab === "overview") return overview(body, sid, manifest, tokens);
    if (tab === "specimens") return specimens(body, sid, manifest);
    if (tab === "tokens") return tokensView(body, tokens);
    if (tab === "components") return componentsView(body, sid, manifest);
    if (tab === "fonts") return fontsView(body, manifest, tokens);
  }
  drawBody();
}

async function overview(body, sid, manifest, tokens) {
  const doc = await sysFile(sid, "DESIGN.md");
  const cards = specCards(manifest).slice(0, 2);
  const left = h("div", { style: { flex: "1 1 520px", minWidth: 0, maxWidth: "760px" } });
  if (typeof doc === "string") left.appendChild(renderMarkdown(doc));
  else left.appendChild(emptyState("notes", features.systemFiles === false ? "System files aren't available yet" : "No DESIGN.md", features.systemFiles === false ? "This server can't read design-system files yet." : "The agent writes this during extraction."));
  const right = h("div", { style: { flex: "1 1 360px", minWidth: "320px", display: "flex", flexDirection: "column", gap: "14px" } }, cards.map((c) => specCard(sid, c, 360)));
  if (tokens) right.appendChild(colorPreview(tokens));
  mount(body, h("div", { class: "row", style: { alignItems: "flex-start", gap: "28px", flexWrap: "wrap" } }, left, right));
}
function specCards(manifest) {
  const cards = [...(manifest?.cards || [])];
  for (const c of manifest?.components || []) if (c.card && !cards.some((x) => x.path === c.card)) cards.push({ path: c.card, group: c.group || "Components", name: c.name });
  return cards;
}
function specCard(sid, c, width) {
  const [vw, vh] = String(c.viewport || "1200x800").split("x").map(Number);
  const frame = h("div", { class: "frame" });
  const cw = width || 420;
  const scale = cw / (vw || 1200);
  frame.style.height = Math.min(360, Math.round((vh || 800) * scale)) + "px";
  const ifr = h("iframe", { src: dsysUrl(sid, c.path), loading: "lazy", sandbox: "allow-scripts allow-same-origin", title: c.name || c.path, style: { width: (vw || 1200) + "px", height: (vh || 800) + "px", transform: `scale(${scale})` } });
  frame.appendChild(ifr);
  const card = h("div", { class: "spec-card" }, frame, h("div", { class: "cap" }, h("b", { class: "grow" }, c.name || c.path.split("/").pop()), h("span", { class: "pill" }, c.group || "Card"),
    btn("", { kind: "quiet", icon: "external", cls: "sm", title: "Open", onClick: () => window.open(dsysUrl(sid, c.path), "_blank", "noopener") })));
  // Re-scale once laid out, to the card's real width.
  requestAnimationFrame(() => { const w = frame.clientWidth; if (w) { const sc = w / (vw || 1200); ifr.style.transform = `scale(${sc})`; frame.style.height = Math.min(380, Math.round((vh || 800) * sc)) + "px"; } });
  return card;
}
function specimens(body, sid, manifest) {
  const cards = specCards(manifest);
  if (!cards.length) { mount(body, emptyState("canvas", "No specimen cards", "Cards are small HTML pages that show tokens and components in use.")); return; }
  const groups = new Map();
  cards.forEach((c) => { const g = c.group || "Other"; if (!groups.has(g)) groups.set(g, []); groups.get(g).push(c); });
  const order = [...groups.keys()].sort((a, b) => { const ia = GROUP_ORDER.indexOf(a), ib = GROUP_ORDER.indexOf(b); return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b); });
  mount(body, order.map((g) => h("section", { class: "spec-group" }, h("h3", null, g, h("span", { class: "faint", style: { fontWeight: 400 } }, groups.get(g).length)),
    h("div", { class: "spec-grid" }, groups.get(g).map((c) => specCard(sid, c))))));
}
function colorPreview(tokens) {
  const theme = tokens.defaultTheme || (tokens.themes || [])[0];
  const c = (theme && tokens.color?.[theme]) || {};
  const keys = Object.keys(c).filter((k) => !k.endsWith("-foreground")).slice(0, 12);
  return h("div", { class: "color-grid", style: { gridTemplateColumns: "repeat(4, 1fr)" } }, keys.map((k) => h("div", { class: "cg" }, h("div", { class: "chip-c", style: { background: c[k].value || c[k].hex || c[k], height: "34px" } }), h("div", { class: "m" }, k))));
}
function tokensView(body, tokens) {
  if (!tokens) { mount(body, emptyState("palette", "No tokens.json", features.systemFiles === false ? "This server can't read design-system files yet." : "")); return; }
  const themes = tokens.themes || Object.keys(tokens.color || {}).filter((k) => typeof tokens.color[k] === "object" && !("value" in tokens.color[k]));
  let theme = tokens.defaultTheme || themes[0];
  const seg = h("div", { class: "seg" });
  const colors = h("div", { class: "color-grid" });
  const drawColors = () => {
    mount(seg, themes.map((t) => h("button", { class: t === theme ? "on" : "", onclick: () => { theme = t; drawColors(); } }, t)));
    const c = (theme && tokens.color?.[theme]) || tokens.color || {};
    mount(colors, Object.entries(c).map(([k, v]) => { const val = v?.value || v?.hex || (typeof v === "string" ? v : ""); return h("div", { class: "cg" }, h("div", { class: "chip-c", style: { background: val } }), h("div", { class: "m" }, h("b", { style: { fontWeight: 500 } }, "--" + k), h("span", { title: val }, val), v?.hex && v.hex !== val ? h("span", null, v.hex) : null)); }));
  };
  drawColors();
  const table = (title, obj, visual) => {
    if (!obj || typeof obj !== "object") return null;
    const flat = [];
    const walk = (o, pre) => { for (const [k, v] of Object.entries(o)) { if (v && typeof v === "object" && !Array.isArray(v) && !("value" in v) && !("stack" in v)) walk(v, pre + k + "-"); else flat.push([pre + k, v && typeof v === "object" ? (v.value || v.stack || JSON.stringify(v)) : String(v)]); } };
    walk(obj, "");
    return h("section", { class: "spec-group" }, h("h3", null, title), h("div", { class: "list-view" }, h("table", { class: "tok-table" },
      h("tr", null, h("th", null, "Token"), h("th", null, "Value"), visual ? h("th", null, "") : null),
      flat.map(([k, v]) => h("tr", null, h("td", { class: "mono" }, "--" + k), h("td", { class: "mono" }, v), visual ? h("td", { style: { width: "40%" } }, visual(k, v)) : null)))));
  };
  const shadowTheme = tokens.shadow?.[theme] || tokens.shadow;
  mount(body,
    h("section", { class: "spec-group" }, h("h3", null, "Colour", h("span", { class: "grow" }), themes.length > 1 ? seg : null), colors),
    table("Typography", tokens.typography, (k, v) => /fontSize/.test(k) ? h("span", { style: { fontSize: v } }, "Aa") : /fontFamily/.test(k) ? h("span", { style: { fontFamily: v } }, "The quick brown fox") : /fontWeight/.test(k) ? h("span", { style: { fontWeight: v } }, "Weight") : null),
    table("Spacing", tokens.spacing, (k, v) => h("div", { class: "tok-bar", style: { width: `min(100%, ${v})` } })),
    table("Radius", tokens.radius, (k, v) => h("div", { class: "tok-rad", style: { borderTopLeftRadius: v } })),
    table("Elevation", shadowTheme, (k, v) => h("div", { class: "tok-shadow", style: { boxShadow: v } })),
    table("Motion", tokens.motion), table("Component tokens", tokens.component));
}
function componentsView(body, sid, manifest) {
  const comps = manifest?.components || [];
  if (!comps.length) { mount(body, emptyState("layers", "No components", "Components are JSX files with a specimen card and a props list.")); return; }
  mount(body, h("div", null, comps.map((c) => h("div", { class: "comp-row" },
    h("div", null, h("h4", null, c.name), h("div", { class: "faint", style: { fontSize: "11.5px" } }, c.group || ""), h("div", { class: "mono faint", style: { fontSize: "11px", marginTop: "4px" } }, c.path || ""),
      c.global ? h("div", { class: "mono faint", style: { fontSize: "11px" } }, c.global) : null),
    h("div", { class: "row", style: { alignItems: "flex-start", gap: "16px", flexWrap: "wrap" } },
      h("div", { style: { flex: "1 1 260px", minWidth: "240px" } }, c.props ? h("table", { class: "props-t" }, Object.entries(c.props).map(([p, d]) => h("tr", null, h("td", null, p),
        h("td", null, d.type === "enum" ? (d.values || []).join(" | ") : d.type || ""), h("td", { class: "faint" }, d.default !== undefined ? "= " + JSON.stringify(d.default) : "")))) : h("span", { class: "faint" }, "No props documented.")),
      c.card ? h("div", { style: { flex: "1 1 360px", minWidth: "320px" } }, specCard(sid, { path: c.card, name: c.name, group: c.group, viewport: (manifest.cards || []).find((x) => x.path === c.card)?.viewport || "800x420" })) : null)))));
}
function fontsView(body, manifest, tokens) {
  const fonts = manifest?.fonts || [];
  const fam = tokens?.typography?.fontFamily || {};
  const brand = Array.isArray(manifest?.brandFonts) ? manifest.brandFonts : [];
  if (!fonts.length && !Object.keys(fam).length && !brand.length) { mount(body, emptyState("text", "No fonts listed", "")); return; }
  const list = fonts.length ? fonts : Object.entries(fam).map(([role, f]) => ({ family: f.family, roles: [role] }));
  list.forEach((f) => f.family && loadFont(f.family));
  // brandFonts: what happened to the brand's own typefaces (provided / substituted / missing).
  const BF = { provided: ["Provided", "ok"], substituted: ["Substituted", "warn"], missing: ["Missing", "err"] };
  const brandBox = brand.length ? h("div", { class: "font-card brand-fonts" }, h("div", { class: "row" }, h("b", { class: "grow" }, "Brand fonts"), h("span", { class: "faint", style: { fontSize: "11.5px" } }, "From the manifest's brandFonts")),
    h("table", { class: "tok-table", style: { marginTop: "6px" } }, brand.map((b) => h("tr", null,
      h("td", null, h("b", null, b.family || "—")), h("td", null, h("span", { class: "pill " + (BF[b.status]?.[1] || "") }, BF[b.status]?.[0] || b.status || "")),
      h("td", { class: "muted" }, b.substitute ? "→ " + b.substitute : ""), h("td", { class: "mono faint" }, (b.tokens || []).join(" ")), h("td", { class: "faint" }, b.note || ""))))) : null;
  mount(body, brandBox, list.map((f) => h("div", { class: "font-card" },
    h("div", { class: "row" }, h("b", { class: "grow" }, f.family), (f.roles || []).map((r) => h("span", { class: "pill" }, r)), f.style && f.style !== "normal" ? h("span", { class: "pill" }, f.style) : null, f.license ? h("span", { class: "faint", style: { fontSize: "11.5px" } }, f.license) : null),
    h("div", { class: "sample", style: { fontFamily: `'${f.family}', system-ui` } }, "Sphinx of black quartz, judge my vow"),
    h("div", { class: "row wrap", style: { gap: "14px", fontFamily: `'${f.family}', system-ui`, color: "var(--muted)" } }, (f.weights || [400, 600]).map((w) => h("span", { style: { fontWeight: w } }, `${w} — Aa Bb Cc 0123`))),
    h("div", { class: "faint", style: { fontSize: "11.5px", marginTop: "8px" } }, f.source || "", f.specimen && /^https:\/\//.test(f.specimen) ? h("a", { href: f.specimen, target: "_blank", rel: "noopener noreferrer", style: { marginLeft: "8px" } }, "Specimen") : null))));
}
