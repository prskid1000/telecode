/*
 * design_canvas.jsx — TeleDesign starter: 2+ static design options side by side in one board.
 * Load with <script type="text/babel" src="design_canvas.jsx"></script> after React 18 + Babel.
 * Original code. Exposes on window:
 *
 *   <DesignCanvas title="Pricing card" subtitle="Three directions for the Pro plan">
 *     <DesignOption label="A · Quiet" note="Type-led, one accent" width={420} height={560}>…</DesignOption>
 *     <DesignOption label="B · Bold" width={420} height={560}>…</DesignOption>
 *     <DesignOption label="C · Dense" width={420} height={560} recommended>…</DesignOption>
 *   </DesignCanvas>
 *
 * DesignCanvas props
 *   title / subtitle   header above the row (omit for none)
 *   gap                space between options (default 64)
 *   padding            space around the row (default 72)
 *   columns            wrap after N options (default: one row)
 *   background         canvas colour (default warm grey with a dot grid)
 *   dots               dot grid on/off (default true)
 *   appearance         "light" | "dark" canvas chrome
 *   fit                true → scale the whole set down to fit the viewport (default true); false → 1:1
 *
 * DesignOption props
 *   label, note        caption above the option ("A · Quiet", one line of rationale)
 *   width / height     fixed artboard size in CSS px (omit to size to content)
 *   background         artboard fill (default #fff); `frameless` drops the card look (for devices)
 *   recommended        small "Recommended" badge
 *   id                 becomes data-td-id on the artboard (defaults to "option-<label slug>")
 *
 * Zoom: a small control in the corner switches Fit / 100%; at 100% the canvas scrolls.
 * Options are static — for live variants use Tweaks (prompts/tweaks.md) instead.
 */
(function () {
  const R = window.React;
  const { useLayoutEffect, useRef, useState } = R;

  const slug = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

  const designCanvasCss = `
  .tdc-root{position:fixed;inset:0;overflow:auto;-webkit-font-smoothing:antialiased}
  .tdc-root.fit{overflow:hidden}
  .tdc-zoom{position:fixed;right:16px;bottom:16px;z-index:5;display:flex;gap:2px;padding:3px;border-radius:10px;
    background:rgba(255,255,255,.9);box-shadow:0 0 0 1px rgba(0,0,0,.06),0 4px 14px rgba(0,0,0,.08);
    -webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px)}
  .tdc-zoom button{all:unset;cursor:pointer;height:26px;padding:0 10px;border-radius:7px;
    font:600 11.5px/26px ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:#57534e}
  .tdc-zoom button:hover{background:rgba(0,0,0,.05)}
  .tdc-zoom button.on{background:#1c1917;color:#fafaf9}
  .tdc-dark .tdc-zoom{background:rgba(30,30,34,.9);box-shadow:0 0 0 1px rgba(255,255,255,.08)}
  .tdc-dark .tdc-zoom button{color:#d6d3d1}
  .tdc-dark .tdc-zoom button.on{background:#fafaf9;color:#1c1917}
  `;

  function DesignOption(props) {
    // Rendered by DesignCanvas; kept as a component so authors can nest anything inside.
    const frameless = !!props.frameless;
    const dark = props.appearance === 'dark';
    const id = props.id || (props.label ? 'option-' + slug(props.label) : undefined);
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, flex: 'none' }}>
        {props.label || props.note || props.recommended
          ? (
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, minHeight: 20, maxWidth: props.width || 'none' }}>
              {props.label
                ? <span style={{ font: '650 14px/1.2 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif', color: dark ? '#fafaf9' : '#1c1917', letterSpacing: '-0.005em', whiteSpace: 'nowrap' }}>{props.label}</span>
                : null}
              {props.note
                ? <span style={{ font: '13px/1.35 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif', color: dark ? '#a8a29e' : '#78716c', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>{props.note}</span>
                : null}
              {props.recommended
                ? <span style={{ marginLeft: 'auto', font: '650 10.5px/1 ui-sans-serif, system-ui, sans-serif', letterSpacing: '.04em', textTransform: 'uppercase', color: '#15803d', background: '#dcfce7', padding: '5px 8px', borderRadius: 999, whiteSpace: 'nowrap' }}>Recommended</span>
                : null}
            </div>
          )
          : null}
        <div data-td-id={id} style={{
          position: 'relative', width: props.width, height: props.height, overflow: 'hidden', flex: 'none',
          background: frameless ? 'transparent' : (props.background || '#ffffff'),
          borderRadius: frameless ? 0 : (props.radius == null ? 6 : props.radius),
          boxShadow: frameless ? 'none' : (dark
            ? '0 0 0 1px rgba(255,255,255,.08), 0 20px 50px -20px rgba(0,0,0,.6)'
            : '0 0 0 1px rgba(28,25,23,.06), 0 1px 2px rgba(28,25,23,.06), 0 20px 50px -24px rgba(28,25,23,.28)')
        }}>
          {props.children}
        </div>
      </div>
    );
  }

  function DesignCanvas(props) {
    const dark = props.appearance === 'dark';
    const gap = props.gap == null ? 64 : props.gap;
    const pad = props.padding == null ? 72 : props.padding;
    const [zoom, setZoom] = useState(props.fit === false ? 'actual' : 'fit');
    const [s, setS] = useState(1);
    const [size, setSize] = useState({ w: 0, h: 0 });
    const contentRef = useRef(null);

    useLayoutEffect(() => {
      const el = contentRef.current;
      if (!el) return undefined;
      const measure = () => {
        const w = el.scrollWidth, h = el.scrollHeight;
        setSize({ w, h });
        if (zoom !== 'fit') { setS(1); return; }
        const v = Math.min(window.innerWidth / (w || 1), window.innerHeight / (h || 1), 1);
        setS(v > 0 && isFinite(v) ? v : 1);
      };
      measure();
      let ro = null;
      try { ro = new ResizeObserver(measure); ro.observe(el); } catch (e) { /* ignore */ }
      window.addEventListener('resize', measure);
      const t = setTimeout(measure, 300); // fonts / images settling
      return () => { window.removeEventListener('resize', measure); if (ro) ro.disconnect(); clearTimeout(t); };
    }, [zoom]);

    const bg = props.background || (dark ? '#161518' : '#f3f1ed');
    const dotColor = dark ? 'rgba(255,255,255,.07)' : 'rgba(28,25,23,.09)';
    const kids = R.Children.toArray(props.children).map((child) =>
      R.isValidElement(child) && child.type === DesignOption && dark && !child.props.appearance
        ? R.cloneElement(child, { appearance: 'dark' })
        : child
    );
    const rows = [];
    if (props.columns > 0) {
      for (let i = 0; i < kids.length; i += props.columns) rows.push(kids.slice(i, i + props.columns));
    } else {
      rows.push(kids);
    }

    return (
      <div className={'tdc-root' + (zoom === 'fit' ? ' fit' : '') + (dark ? ' tdc-dark' : '')} data-td-id="design-canvas" style={{
        background: bg,
        backgroundImage: props.dots === false ? 'none' : `radial-gradient(${dotColor} 1px, transparent 1.2px)`,
        backgroundSize: '22px 22px'
      }}>
        <style>{designCanvasCss}</style>
        <div style={{
          width: size.w * s || 'auto', height: size.h * s || 'auto', position: 'relative',
          margin: zoom === 'fit' ? `${Math.max(0, (window.innerHeight - size.h * s) / 2)}px auto 0` : 0
        }}>
          <div ref={contentRef} style={{
            position: zoom === 'fit' ? 'absolute' : 'relative', left: 0, top: 0, transform: `scale(${s})`, transformOrigin: '0 0',
            padding: pad, display: 'inline-flex', flexDirection: 'column', gap: 40, boxSizing: 'border-box'
          }}>
            {props.title || props.subtitle
              ? (
                <header style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {props.title
                    ? <h1 data-td-id="design-canvas-title" style={{ margin: 0, font: '700 28px/1.15 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif', letterSpacing: '-0.02em', color: dark ? '#fafaf9' : '#1c1917' }}>{props.title}</h1>
                    : null}
                  {props.subtitle
                    ? <p style={{ margin: 0, font: '15px/1.45 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif', color: dark ? '#a8a29e' : '#78716c', maxWidth: 720 }}>{props.subtitle}</p>
                    : null}
                </header>
              )
              : null}
            {rows.map((row, i) => (
              <div key={i} style={{ display: 'flex', gap, alignItems: 'flex-start' }}>{row}</div>
            ))}
          </div>
        </div>
        <div className="tdc-zoom" data-no-nav>
          <button type="button" className={zoom === 'fit' ? 'on' : ''} onClick={() => setZoom('fit')}>Fit</button>
          <button type="button" className={zoom === 'actual' ? 'on' : ''} onClick={() => setZoom('actual')}>100%</button>
        </div>
      </div>
    );
  }

  Object.assign(window, { DesignCanvas, DesignOption });
})();
