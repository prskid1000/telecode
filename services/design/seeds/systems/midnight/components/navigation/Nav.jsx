/* TeleDesign seed system "Midnight" — Nav.
 * React 18, no build: load with <script type="text/babel" data-presets="react" src="Nav.jsx">.
 * Exports window.Nav. Styling lives in ../../components.css and reads tokens.css only.
 * Props: see manifest.json. Icon path data from Lucide (ISC; x/search/menu are Feather-derived, MIT). */
(() => {
  const ORIENTATIONS = ["horizontal", "vertical"];
  function Nav({ brand, items = [], actions, orientation = "horizontal", label = "Main", className = "" }) {
    const o = ORIENTATIONS.includes(orientation) ? orientation : "horizontal";
    return (
      <nav aria-label={label} className={["td-nav", o === "vertical" && "td-nav--vertical", className].filter(Boolean).join(" ")}>
        {brand && <a className="td-nav__brand" href="#">{brand}</a>}
        <ul className="td-nav__items">
          {items.map((it) => (
            <li key={it.label}><a className="td-nav__link" href={it.href || "#"} aria-current={it.active ? "page" : undefined}>{it.label}</a></li>
          ))}
        </ul>
        {actions && <div className="td-nav__actions">{actions}</div>}
      </nav>
    );
  }
  Nav.orientations = ORIENTATIONS;
  Object.assign(window, { Nav });
})();
