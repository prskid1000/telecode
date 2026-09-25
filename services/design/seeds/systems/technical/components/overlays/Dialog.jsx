/* TeleDesign seed system "Technical" — Dialog.
 * React 18, no build: load with <script type="text/babel" data-presets="react" src="Dialog.jsx">.
 * Exports window.Dialog. Styling lives in ../../components.css and reads tokens.css only.
 * Props: see manifest.json. Icon path data from Lucide (ISC; x/search/menu are Feather-derived, MIT). */
(() => {
  const SIZES = ["sm", "md", "lg"];
  const X = <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M18 6 6 18" /><path d="m6 6 12 12" /></svg>;
  function Dialog({ open = false, onClose, title, description, footer, size = "md", inline = false, children }) {
    React.useEffect(() => {
      if (!open || inline) return undefined;
      const onKey = (e) => { if (e.key === "Escape" && onClose) onClose(); };
      document.addEventListener("keydown", onKey);
      return () => document.removeEventListener("keydown", onKey);
    }, [open, inline, onClose]);
    const titleId = React.useId();
    if (!open) return null;
    const s = SIZES.includes(size) ? size : "md";
    return (
      <div className={"td-dialog-overlay" + (inline ? " td-dialog-overlay--inline" : "")}
        onMouseDown={(e) => { if (e.target === e.currentTarget && onClose) onClose(); }}>
        <div className={"td-dialog td-dialog--" + s} role="dialog" aria-modal={inline ? undefined : "true"} aria-labelledby={title ? titleId : undefined}>
          {onClose && <button type="button" className="td-btn td-btn--ghost td-btn--icon td-dialog__close" aria-label="Close" onClick={onClose}>{X}</button>}
          {(title || description) && (
            <div>
              {title && <h2 id={titleId} className="td-dialog__title">{title}</h2>}
              {description && <p className="td-dialog__description">{description}</p>}
            </div>
          )}
          {children}
          {footer && <div className="td-dialog__footer">{footer}</div>}
        </div>
      </div>
    );
  }
  Dialog.sizes = SIZES;
  Object.assign(window, { Dialog });
})();
