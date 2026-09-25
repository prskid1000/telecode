/* TeleDesign seed system "Playful" — Card.
 * React 18, no build: load with <script type="text/babel" data-presets="react" src="Card.jsx">.
 * Exports window.Card. Styling lives in ../../components.css and reads tokens.css only.
 * Props: see manifest.json. Icon path data from Lucide (ISC; x/search/menu are Feather-derived, MIT). */
(() => {
  // No object-rest destructuring: Babel standalone hoists its helper (_excluded) to global scope,
  // so two component files using ...rest collide. Copy props manually instead.
  const omit = (o, keys) => { const r = {}; for (const k in o) if (keys.indexOf(k) < 0) r[k] = o[k]; return r; };
  const PADDINGS = ["sm", "md", "lg"];
  const ELEVATIONS = ["flat", "raised"];
  function Card(props) {
    const { title, description, footer, padding = "md", elevation = "flat", className = "", children } = props;
    const rest = omit(props, ["title", "description", "footer", "padding", "elevation", "className", "children"]);
    const p = PADDINGS.includes(padding) ? padding : "md";
    const e = ELEVATIONS.includes(elevation) ? elevation : "flat";
    const cls = ["td-card", "td-card--pad-" + p, e === "raised" && "td-card--raised", className].filter(Boolean).join(" ");
    return (
      <section className={cls} {...rest}>
        {(title || description) && (
          <header className="td-card__header">
            {title && <h3 className="td-card__title">{title}</h3>}
            {description && <p className="td-card__description">{description}</p>}
          </header>
        )}
        {children && <div className="td-card__content">{children}</div>}
        {footer && <footer className="td-card__footer">{footer}</footer>}
      </section>
    );
  }
  Card.paddings = PADDINGS;
  Card.elevations = ELEVATIONS;
  Object.assign(window, { Card });
})();
