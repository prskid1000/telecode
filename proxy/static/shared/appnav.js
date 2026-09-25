// <tc-appnav> — one top-bar switcher across Team Mode, Task Mode and TeleDesign.
//
//   <tc-appnav active="team|tasks|design" [variant="bar|switch"] [compact]>
//     …page-specific actions (moved into the right-hand slot)…
//   </tc-appnav>
//
//   variant="bar"    (default) full top bar: brand · switch · actions
//   variant="switch" just the segmented switch, for pages that own their top bar
//   compact          icons only
//
// Links are plain <a href> so middle-click / Ctrl-click / "open in new tab" work.
// Alt+Shift+1/2/3 jump between apps (plain Alt+1/2/3 are TeleDesign's view keys).
// Nothing is carried across apps except the theme, which uses TeleDesign's own
// per-viewer key ("td:theme" in localStorage, see design/app/theme.js).
// Classic script; also safe to import() as a module. Never touches innerHTML
// with anything but the constant icon paths below.
(() => {
  "use strict";
  if (window.customElements && customElements.get("tc-appnav")) return;

  const APPS = [
    { id: "team", label: "Team", href: "/team", key: "1", title: "Team Mode — workspaces, agents, jobs & pipelines",
      icon: '<circle cx="8" cy="8.5" r="3"/><circle cx="16.5" cy="8.5" r="3"/><path d="M3 19c.5-3 2.6-4.5 5-4.5s4.5 1.5 5 4.5M12.5 15.2c1-.5 2.3-.7 4-.7 2.4 0 4.5 1.5 5 4.5"/>' },
    { id: "tasks", label: "Tasks", href: "/tasks", key: "2", title: "Task Mode — sessions, one-off tasks, skills & routines",
      icon: '<rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="M7 9.5l3 2.5-3 2.5M12.5 15h4.5"/>' },
    { id: "design", label: "Design", href: "/design", key: "3", title: "TeleDesign — design with the CLIs",
      icon: '<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.3 0 2-.8 2-1.8 0-1.3-1.2-1.6-1.2-2.8 0-1 .8-1.6 1.8-1.6h2.3a3.6 3.6 0 0 0 3.6-3.6C20.5 6.6 16.7 3.5 12 3.5z"/><circle cx="7.8" cy="11" r="1.1"/><circle cx="10.3" cy="7.3" r="1.1"/><circle cx="15" cy="7.5" r="1.1"/>' },
  ];
  const BOLT = '<path d="M13 3L5 13.5h6L10 21l8-10.5h-6z"/>';
  const THEME_KEY = "td:theme";

  // Stylesheet: load appnav.css next to this script unless the page already did.
  const scriptSrc = (document.currentScript && document.currentScript.src) || "/shared/appnav.js";
  const cssHref = new URL("appnav.css", new URL(scriptSrc, location.href)).href;
  if (![...document.querySelectorAll('link[rel="stylesheet"]')].some((l) => l.href === cssHref)) {
    const l = document.createElement("link"); l.rel = "stylesheet"; l.href = cssHref; document.head.appendChild(l);
  }

  function svg(paths) {
    const s = document.createElement("span");
    s.className = "tc-ic"; s.setAttribute("aria-hidden", "true");
    s.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
    return s;
  }
  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null && v !== false) e.setAttribute(k, v === true ? "" : String(v));
    for (const k of kids) if (k != null) e.append(k);
    return e;
  }

  // Theme follows TeleDesign's preference in every open tab.
  function themeFromPref() {
    let p = "system";
    try { const v = localStorage.getItem(THEME_KEY); if (v != null) p = JSON.parse(v); } catch { /* storage blocked */ }
    return p === "system" ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : p;
  }
  addEventListener("storage", (e) => { if (e.key === THEME_KEY) document.documentElement.dataset.theme = themeFromPref(); });

  class TcAppnav extends HTMLElement {
    static get observedAttributes() { return ["active"]; }
    connectedCallback() {
      if (this._built) { this._place(false); return; }
      if (document.readyState === "loading" && !this.childNodes.length) {
        document.addEventListener("DOMContentLoaded", () => this.connectedCallback(), { once: true });
        return;
      }
      this._build();
    }
    attributeChangedCallback() { if (this._built) this._sync(); }
    get active() { return this.getAttribute("active") || ""; }
    set active(v) { this.setAttribute("active", v); }
    get actions() { return this._actions; }

    _build() {
      this._built = true;
      const variant = this.getAttribute("variant") || "bar";
      const extra = [...this.childNodes];
      this.textContent = "";
      this._ind = el("span", { class: "tc-ind no-anim", "aria-hidden": "true" });
      this._links = APPS.map((a) => {
        const link = el("a", { class: "tc-app", href: a.href, "data-app": a.id, title: `${a.title}  (Alt+Shift+${a.key})` },
          svg(a.icon), el("span", { class: "tc-lbl" }, a.label));
        link.addEventListener("click", (e) => {
          if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
          if (a.id === this.active) { e.preventDefault(); return; }
          this._place(true, link); // slide the indicator; the browser navigates normally
        });
        return link;
      });
      this._switch = el("nav", { class: "tc-switch", "aria-label": "Telecode apps" }, this._ind, ...this._links);
      this._actions = el("div", { class: "tc-actions" }, ...extra);
      if (variant === "bar") {
        const home = (APPS.find((a) => a.id === this.active) || APPS[0]).href;
        const mark = el("span", { class: "tc-mark" }, svg(BOLT));
        this.append(el("a", { class: "tc-brand", href: home, title: "Telecode" }, mark, "Telecode"), this._switch, this._actions);
      } else {
        this.append(this._switch, this._actions);
      }
      this._sync();
      if (window.ResizeObserver) { this._ro = new ResizeObserver(() => this._place(false)); this._ro.observe(this._switch); }
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => this._place(false));
      requestAnimationFrame(() => { this._place(false); requestAnimationFrame(() => this._ind.classList.remove("no-anim")); });
    }
    _sync() {
      for (const l of this._links) {
        if (l.dataset.app === this.active) l.setAttribute("aria-current", "page"); else l.removeAttribute("aria-current");
      }
      this._place(true);
    }
    _place(animate, target) {
      const link = target || this._links.find((l) => l.dataset.app === this.active);
      if (!link || !this._ind) { if (this._ind) this._ind.style.width = "0"; return; }
      if (!animate) this._ind.classList.add("no-anim");
      this._ind.style.left = link.offsetLeft + "px";
      this._ind.style.width = link.offsetWidth + "px";
      if (!animate) requestAnimationFrame(() => this._ind && this._ind.classList.remove("no-anim"));
    }
  }
  customElements.define("tc-appnav", TcAppnav);

  // Cross-document view transitions need BOTH documents to opt in at first
  // render. TeleDesign loads this file lazily (after its first paint), so a
  // transition *into* /design would abort with an InvalidStateError — skip it.
  addEventListener("pageswap", (e) => {
    const vt = e.viewTransition;
    if (!vt) return;
    let to = "";
    try { to = new URL(e.activation && e.activation.entry && e.activation.entry.url || "", location.href).pathname; } catch { /* ignore */ }
    if (!/^\/(team|tasks)\/?$/.test(to)) vt.skipTransition();
    vt.finished.catch(() => {});
  });
  addEventListener("pagereveal", (e) => {
    const vt = e.viewTransition;
    if (vt) { vt.ready.catch(() => {}); vt.finished.catch(() => {}); }
  });

  // Alt+Shift+1/2/3 — switch app from anywhere (uses e.code: Shift changes e.key).
  document.addEventListener("keydown", (e) => {
    if (!e.altKey || !e.shiftKey || e.ctrlKey || e.metaKey) return;
    const a = APPS.find((x) => e.code === "Digit" + x.key || e.code === "Numpad" + x.key);
    if (!a || !document.querySelector("tc-appnav")) return;
    e.preventDefault();
    const nav = document.querySelector("tc-appnav");
    if (nav.active === a.id) return;
    const link = nav._links && nav._links.find((l) => l.dataset.app === a.id);
    if (link) nav._place(true, link);
    location.href = a.href;
  }, true);
})();
