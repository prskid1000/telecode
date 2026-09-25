// TeleDesign — boot + routing.
import { h, icon, btn, mount, bus, toastError } from "./core.js";
import { S, readUrl, loadEngines, loadSystems, writeUrl, loadConfig } from "./state.js";
import { applyTheme, toggleTheme, currentTheme } from "./theme.js";
import "./bridge.js";
import "./interact.js";
import { sheet } from "./shortcuts.js";

applyTheme();
const app = document.getElementById("app");
// Shared Team · Tasks · Design switcher (/shared/appnav.js). Optional: if the
// route is missing TeleDesign renders exactly as before.
import("/shared/appnav.js").catch(() => {});
// Phones get the icon-only switch on the home bar too (the component's own
// "compact" attribute; nothing here restyles it).
const narrow = matchMedia("(max-width: 600px)");
const appnav = (compact) => {
  const el = h("tc-appnav", { active: "design", variant: "switch", compact: compact || narrow.matches ? "" : null });
  if (!compact) narrow.addEventListener("change", () => el.toggleAttribute("compact", narrow.matches));
  return el;
};

function homeTopbar() {
  const themeBtn = btn("", { kind: "quiet", icon: currentTheme() === "dark" ? "sun" : "moon", title: "Toggle light / dark  (Alt+Shift+L)", onClick: () => toggleTheme() });
  bus.on("theme", (t) => mount(themeBtn, icon(t === "dark" ? "sun" : "moon")));
  return h("header", { class: "topbar" },
    h("a", { class: "brand", href: "/design", onclick: (e) => { e.preventDefault(); go({ route: "home" }, true); } }, h("span", { class: "brand-mark" }, icon("edit")), "TeleDesign"),
    appnav(false),
    h("div", { class: "right" },
      btn("", { kind: "quiet", icon: "settings", title: "Preferences", onClick: () => import("./exporter.js").then((m) => m.settingsDialog()) }),
      btn("", { kind: "quiet", icon: "keyboard", cls: "kbd-btn", title: "Keyboard shortcuts  (?)", onClick: () => sheet() }),
      themeBtn));
}

async function go(nav, push = false) {
  const { route } = nav;
  document.querySelectorAll(".popover, .menu").forEach((e) => e.remove());
  if (route === "project") {
    const ws = await import("./workspace.js");
    if (S.route !== "project" || !S.project || S.project.id !== nav.id) {
      if (push) history.pushState(null, "", `${location.pathname}?project=${encodeURIComponent(nav.id)}`);
      mount(app);
      await ws.openProject(app, nav.id, nav);
      addProjectTopbarExtras();
    }
    return;
  }
  (await import("./workspace.js")).disconnect();
  S.project = null;
  S.systemId = route === "systems" ? nav.system || null : null;
  document.title = "TeleDesign";
  const body = h("div", { style: { flex: "1", display: "flex", minHeight: 0 } });
  mount(app, homeTopbar(), body);
  const { renderHome } = await import("./gallery.js");
  S.route = route === "home" ? "home" : route;
  if (push) { writeUrl(true); }
  await renderHome(body, route === "home" ? "projects" : route);
}
function addProjectTopbarExtras() {
  const right = app.querySelector(".topbar .right");
  if (!right) return;
  const themeBtn = btn("", { kind: "quiet", icon: currentTheme() === "dark" ? "sun" : "moon", title: "Toggle light / dark  (Alt+Shift+L)", onClick: () => toggleTheme() });
  bus.on("theme", (t) => themeBtn.isConnected && mount(themeBtn, icon(t === "dark" ? "sun" : "moon")));
  right.prepend(appnav(true), h("span", { class: "divider-v" }));
  right.append(btn("", { kind: "quiet", icon: "keyboard", cls: "kbd-btn", title: "Keyboard shortcuts  (?)", onClick: () => sheet() }), themeBtn);
}

bus.on("navigate", (nav) => go(nav, true).catch(toastError));
addEventListener("popstate", () => route().catch(toastError));

async function route() {
  const u = readUrl();
  if (u.project) return go({ route: "project", id: u.project, file: u.file, chat: u.chat, view: u.view, panel: u.panel, board: u.board, node: u.node });
  S.route = "home";
  return go({ route: ["templates", "systems", "archived"].includes(u.home) ? u.home : "home", system: u.system });
}

loadEngines().catch(() => {});
loadSystems().catch(() => {});
loadConfig().catch(() => null).then(() => route()).catch((e) => { console.error(e); mount(app, h("div", { style: { padding: "40px" } }, "TeleDesign failed to start: ", String(e.message || e))); });
