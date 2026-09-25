// TeleDesign — light/dark theme (per viewer; "system" follows the OS).
import { prefs, bus } from "./core.js";

const mq = matchMedia("(prefers-color-scheme: light)");
export function currentTheme() {
  const p = prefs.get("theme", "system");
  return p === "system" ? (mq.matches ? "light" : "dark") : p;
}
export function applyTheme() {
  document.documentElement.dataset.theme = currentTheme();
  bus.emit("theme", currentTheme());
}
export function toggleTheme() {
  prefs.set("theme", currentTheme() === "dark" ? "light" : "dark");
  applyTheme();
}
mq.addEventListener?.("change", () => { if (prefs.get("theme", "system") === "system") applyTheme(); });
