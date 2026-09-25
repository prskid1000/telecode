/* TeleDesign seed system "Neutral" — Table.
 * React 18, no build: load with <script type="text/babel" data-presets="react" src="Table.jsx">.
 * Exports window.Table. Styling lives in ../../components.css and reads tokens.css only.
 * Props: see manifest.json. Icon path data from Lucide (ISC; x/search/menu are Feather-derived, MIT). */
(() => {
  const DENSITIES = ["compact", "comfortable"];
  function Table({ columns = [], rows = [], density = "comfortable", striped = false, caption, className = "" }) {
    const d = DENSITIES.includes(density) ? density : "comfortable";
    const cls = ["td-table", "td-table--" + d, striped && "td-table--striped", className].filter(Boolean).join(" ");
    return (
      <div className="td-table-wrap">
        <table className={cls}>
          {caption && <caption>{caption}</caption>}
          <thead><tr>{columns.map((c) => <th key={c.key} scope="col" className={c.numeric ? "td-num" : undefined}>{c.label}</th>)}</tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.id || i}>{columns.map((c) => <td key={c.key} className={c.numeric ? "td-num" : undefined}>{r[c.key]}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  Table.densities = DENSITIES;
  Object.assign(window, { Table });
})();
