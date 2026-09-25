/* TeleDesign seed system "Editorial" — Input.
 * React 18, no build: load with <script type="text/babel" data-presets="react" src="Input.jsx">.
 * Exports window.Input. Styling lives in ../../components.css and reads tokens.css only.
 * Props: see manifest.json. Icon path data from Lucide (ISC; x/search/menu are Feather-derived, MIT). */
(() => {
  // No object-rest destructuring: Babel standalone hoists its helper (_excluded) to global scope,
  // so two component files using ...rest collide. Copy props manually instead.
  const omit = (o, keys) => { const r = {}; for (const k in o) if (keys.indexOf(k) < 0) r[k] = o[k]; return r; };
  const SIZES = ["sm", "md", "lg"];
  let seq = 0;
  function Input(props) {
    const { label, hint, error, size = "md", type = "text", invalid = false, id, className = "" } = props;
    const rest = omit(props, ["label", "hint", "error", "size", "type", "invalid", "id", "className"]);
    const [autoId] = React.useState(() => "td-input-" + (++seq));
    const inputId = id || autoId;
    const s = SIZES.includes(size) ? size : "md";
    const bad = invalid || Boolean(error);
    const hintId = (hint || error) ? inputId + "-hint" : undefined;
    return (
      <div className="td-field">
        {label && <label className="td-label" htmlFor={inputId}>{label}</label>}
        <input id={inputId} type={type} aria-invalid={bad ? "true" : undefined} aria-describedby={hintId}
          className={["td-input", s !== "md" && "td-input--" + s, className].filter(Boolean).join(" ")} {...rest} />
        {(error || hint) && <span id={hintId} className={error ? "td-hint td-hint--error" : "td-hint"}>{error || hint}</span>}
      </div>
    );
  }
  Input.sizes = SIZES;
  Object.assign(window, { Input });
})();
