/* TeleDesign seed system "Neutral" — Tabs.
 * React 18, no build: load with <script type="text/babel" data-presets="react" src="Tabs.jsx">.
 * Exports window.Tabs. Styling lives in ../../components.css and reads tokens.css only.
 * Props: see manifest.json. Icon path data from Lucide (ISC; x/search/menu are Feather-derived, MIT). */
(() => {
  const VARIANTS = ["pill", "underline"];
  function Tabs({ items = [], value, defaultValue, onChange, variant = "pill", className = "" }) {
    const [inner, setInner] = React.useState(defaultValue || (items[0] && items[0].id));
    const active = value !== undefined ? value : inner;
    const v = VARIANTS.includes(variant) ? variant : "pill";
    const select = (id) => { if (value === undefined) setInner(id); if (onChange) onChange(id); };
    const onKey = (e, i) => {
      const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!d || !items.length) return;
      const next = items[(i + d + items.length) % items.length];
      select(next.id);
      const btn = e.currentTarget.parentElement.querySelector('[data-tab="' + next.id + '"]');
      if (btn) btn.focus();
    };
    const current = items.find((t) => t.id === active);
    return (
      <div className={["td-tabs", "td-tabs--" + v, className].filter(Boolean).join(" ")}>
        <div className="td-tabs__list" role="tablist">
          {items.map((t, i) => (
            <button key={t.id} type="button" role="tab" data-tab={t.id} className="td-tabs__tab"
              aria-selected={t.id === active} tabIndex={t.id === active ? 0 : -1}
              onClick={() => select(t.id)} onKeyDown={(e) => onKey(e, i)}>{t.label}</button>
          ))}
        </div>
        {current && current.content !== undefined && <div className="td-tabs__panel" role="tabpanel">{current.content}</div>}
      </div>
    );
  }
  Tabs.variants = VARIANTS;
  Object.assign(window, { Tabs });
})();
