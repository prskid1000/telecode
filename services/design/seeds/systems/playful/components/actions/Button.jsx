/* TeleDesign seed system "Playful" — Button.
 * React 18, no build: load with <script type="text/babel" data-presets="react" src="Button.jsx">.
 * Exports window.Button. Styling lives in ../../components.css and reads tokens.css only.
 * Props: see manifest.json. Icon path data from Lucide (ISC; x/search/menu are Feather-derived, MIT). */
(() => {
  // No object-rest destructuring: Babel standalone hoists its helper (_excluded) to global scope,
  // so two component files using ...rest collide. Copy props manually instead.
  const omit = (o, keys) => { const r = {}; for (const k in o) if (keys.indexOf(k) < 0) r[k] = o[k]; return r; };
  const VARIANTS = ["primary", "secondary", "outline", "ghost", "destructive", "link"];
  const SIZES = ["sm", "md", "lg", "icon"];
  function Button(props) {
    const { variant = "primary", size = "md", disabled = false, type = "button", className = "", children } = props;
    const rest = omit(props, ["variant", "size", "disabled", "type", "className", "children"]);
    const v = VARIANTS.includes(variant) ? variant : "primary";
    const s = SIZES.includes(size) ? size : "md";
    const cls = ["td-btn", "td-btn--" + v, "td-btn--" + s, className].filter(Boolean).join(" ");
    return <button type={type} disabled={disabled} className={cls} {...rest}>{children}</button>;
  }
  Button.variants = VARIANTS;
  Button.sizes = SIZES;
  Object.assign(window, { Button });
})();
