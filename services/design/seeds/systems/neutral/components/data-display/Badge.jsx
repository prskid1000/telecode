/* TeleDesign seed system "Neutral" — Badge.
 * React 18, no build: load with <script type="text/babel" data-presets="react" src="Badge.jsx">.
 * Exports window.Badge. Styling lives in ../../components.css and reads tokens.css only.
 * Props: see manifest.json. Icon path data from Lucide (ISC; x/search/menu are Feather-derived, MIT). */
(() => {
  // No object-rest destructuring: Babel standalone hoists its helper (_excluded) to global scope,
  // so two component files using ...rest collide. Copy props manually instead.
  const omit = (o, keys) => { const r = {}; for (const k in o) if (keys.indexOf(k) < 0) r[k] = o[k]; return r; };
  const VARIANTS = ["default", "secondary", "outline", "success", "warning", "destructive"];
  function Badge(props) {
    const { variant = "default", className = "", children } = props;
    const rest = omit(props, ["variant", "className", "children"]);
    const v = VARIANTS.includes(variant) ? variant : "default";
    return <span className={["td-badge", "td-badge--" + v, className].filter(Boolean).join(" ")} {...rest}>{children}</span>;
  }
  Badge.variants = VARIANTS;
  Object.assign(window, { Badge });
})();
