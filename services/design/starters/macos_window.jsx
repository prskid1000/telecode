/*
 * macos_window.jsx — TeleDesign starter: desktop-app window chrome (macOS style).
 * Load with <script type="text/babel" src="macos_window.jsx"></script> after React 18 + Babel.
 * Original code. Exposes on window:
 *
 *   <MacWindow title="Notes" width={1100} height={720}
 *              sidebar={<MacSidebar sections={[{title: "Library", items: [{label: "All notes", active: true}]}]} />}
 *              toolbar={<MacToolbarButton label="New" icon="+" />}>
 *     <YourContent />
 *   </MacWindow>
 *
 * MacWindow props
 *   width / height   window size in CSS px (default 1100 × 720)
 *   title            centred title (or placed after the sidebar when `unifiedToolbar`)
 *   subtitle         second line under the title
 *   appearance       "light" | "dark"                                             default "light"
 *   sidebar          node rendered in a translucent sidebar (full height, under the traffic lights)
 *   sidebarWidth     default 232
 *   toolbar          node rendered right-aligned in the title bar
 *   unifiedToolbar   title bar merges with the toolbar (tall, 52px)                default true
 *   inactive         grey traffic lights                                           default false
 *   background       content background
 *   desktop          wallpaper behind the window when fitted: CSS background     default soft gradient
 *   fit              true → scale down to fit the viewport · "parent" → fit parent · false → 1:1
 *   scale            fixed scale (overrides fit)
 *   id               becomes data-td-id on the content element
 *
 * Also: MacTrafficLights, MacSidebar ({sections:[{title, items:[{label, icon?, active?, badge?}]}]}),
 * MacToolbarButton ({label, icon, onClick}).
 */
(function () {
  const R = window.React;
  const { useLayoutEffect, useRef, useState } = R;

  function useWindowFit(fit, fixed, w, h) {
    const ref = useRef(null);
    const [s, setS] = useState(typeof fixed === 'number' ? fixed : 1);
    useLayoutEffect(() => {
      if (typeof fixed === 'number') { setS(fixed); return undefined; }
      if (!fit) { setS(1); return undefined; }
      const measure = () => {
        let aw = window.innerWidth, ah = window.innerHeight;
        if (fit === 'parent' && ref.current && ref.current.parentElement) {
          const r = ref.current.parentElement.getBoundingClientRect();
          if (r.width > 40) aw = r.width;
          if (r.height > 40) ah = r.height;
        }
        const pad = fit === 'parent' ? 0 : 40;
        const v = Math.min((aw - pad * 2) / w, (ah - pad * 2) / h, 1);
        setS(v > 0 && isFinite(v) ? v : 1);
      };
      measure();
      let ro = null;
      try {
        if (fit === 'parent' && ref.current && ref.current.parentElement) {
          ro = new ResizeObserver(measure);
          ro.observe(ref.current.parentElement);
        }
      } catch (e) { /* ignore */ }
      window.addEventListener('resize', measure);
      return () => { window.removeEventListener('resize', measure); if (ro) ro.disconnect(); };
    }, [fit, fixed, w, h]);
    return [ref, s];
  }

  const macTheme = {
    light: {
      chrome: '#f6f5f4', chromeBorder: 'rgba(0,0,0,.09)', text: '#1d1d1f', dim: '#6e6e73', content: '#ffffff',
      sidebar: 'rgba(236,235,238,.82)', sideText: '#2c2c2e', sideDim: '#8e8e93', sideActive: 'rgba(0,0,0,.075)',
      frame: '0 0 0 .5px rgba(0,0,0,.28), 0 22px 70px rgba(0,0,0,.28), 0 8px 20px rgba(0,0,0,.12)', hover: 'rgba(0,0,0,.06)'
    },
    dark: {
      chrome: '#2b2a2d', chromeBorder: 'rgba(0,0,0,.5)', text: '#f2f2f7', dim: '#98989d', content: '#1e1e20',
      sidebar: 'rgba(42,41,46,.86)', sideText: '#e5e5ea', sideDim: '#8e8e93', sideActive: 'rgba(255,255,255,.1)',
      frame: '0 0 0 .5px rgba(255,255,255,.14), 0 0 0 1px rgba(0,0,0,.6), 0 22px 70px rgba(0,0,0,.55)', hover: 'rgba(255,255,255,.08)'
    }
  };

  function MacTrafficLights(props) {
    const off = props.inactive;
    const dot = (c, b) => (
      <span style={{
        width: 12, height: 12, borderRadius: 6, background: off ? (props.dark ? '#4a494d' : '#d6d5d8') : c,
        boxShadow: off ? 'inset 0 0 0 .5px rgba(0,0,0,.12)' : `inset 0 0 0 .5px ${b}`, display: 'block'
      }} />
    );
    return (
      <div aria-hidden="true" style={{ display: 'flex', gap: 8, alignItems: 'center', flex: 'none' }}>
        {dot('#ff5f57', '#e0443e')}
        {dot('#febc2e', '#dea123')}
        {dot('#28c840', '#1aab29')}
      </div>
    );
  }

  function MacToolbarButton(props) {
    const [hover, setHover] = useState(false);
    const dark = props.dark;
    return (
      <button type="button" onClick={props.onClick} title={props.label} aria-label={props.label}
              onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
              style={{
                all: 'unset', boxSizing: 'border-box', height: 28, minWidth: 28, padding: props.showLabel ? '0 10px' : 0,
                borderRadius: 6, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                cursor: 'default', color: dark ? '#d1d1d6' : '#4a4a4f', font: '500 13px/1 -apple-system, "SF Pro Text", "Inter", system-ui, sans-serif',
                background: hover ? (dark ? 'rgba(255,255,255,.08)' : 'rgba(0,0,0,.06)') : 'transparent'
              }}>
        {props.icon ? <span style={{ fontSize: 16, lineHeight: 1 }}>{props.icon}</span> : null}
        {props.showLabel || !props.icon ? <span>{props.label}</span> : null}
      </button>
    );
  }

  function MacSidebar(props) {
    const t = macTheme[props.appearance === 'dark' ? 'dark' : 'light'];
    return (
      <nav style={{ padding: '6px 10px 12px', font: '13px/1.2 -apple-system, "SF Pro Text", "Inter", system-ui, sans-serif', color: t.sideText }}>
        {(props.sections || []).map((sec, i) => (
          <div key={i} style={{ marginTop: i ? 14 : 0 }}>
            {sec.title
              ? <div style={{ font: '600 11px/1 -apple-system, "SF Pro Text", "Inter", system-ui, sans-serif', color: t.sideDim, padding: '8px 8px 6px' }}>{sec.title}</div>
              : null}
            {(sec.items || []).map((it, j) => (
              <div key={j} data-td-id={it.id} style={{
                display: 'flex', alignItems: 'center', gap: 8, height: 28, padding: '0 8px', borderRadius: 6,
                background: it.active ? t.sideActive : 'transparent', fontWeight: it.active ? 500 : 400
              }}>
                {it.icon ? <span style={{ width: 18, textAlign: 'center', color: props.accent || '#0a84ff', fontSize: 14 }}>{it.icon}</span> : null}
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.label}</span>
                {it.badge != null ? <span style={{ color: t.sideDim, fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>{it.badge}</span> : null}
              </div>
            ))}
          </div>
        ))}
      </nav>
    );
  }

  function MacWindow(props) {
    const w = props.width || 1100;
    const h = props.height || 720;
    const dark = props.appearance === 'dark';
    const t = macTheme[dark ? 'dark' : 'light'];
    const fit = props.fit === undefined ? true : props.fit;
    const [ref, s] = useWindowFit(fit, props.scale, w, h);
    const barH = props.unifiedToolbar === false ? 32 : 52;
    const sideW = props.sidebar ? (props.sidebarWidth || 232) : 0;
    const font = '-apple-system, "SF Pro Text", "Inter", "Segoe UI", system-ui, sans-serif';
    const win = (
      <div style={{
        position: 'relative', width: w, height: h, borderRadius: 12, overflow: 'hidden', background: props.background || t.content,
        boxShadow: t.frame, display: 'flex', font: '13px/1.4 ' + font, color: t.text, flex: 'none'
      }}>
        {props.sidebar
          ? (
            <aside style={{
              width: sideW, flex: 'none', background: t.sidebar, borderRight: '.5px solid ' + t.chromeBorder,
              paddingTop: barH, boxSizing: 'border-box', overflowY: 'auto', position: 'relative',
              WebkitBackdropFilter: 'blur(30px) saturate(1.6)', backdropFilter: 'blur(30px) saturate(1.6)'
            }}>
              <div style={{ position: 'absolute', left: 18, top: barH / 2 - 6 }}><MacTrafficLights inactive={props.inactive} dark={dark} /></div>
              {props.sidebar}
            </aside>
          )
          : null}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <header style={{
            height: barH, flex: 'none', display: 'flex', alignItems: 'center', gap: 12, padding: '0 14px 0 ' + (props.sidebar ? 16 : 18) + 'px',
            background: t.chrome, borderBottom: '.5px solid ' + t.chromeBorder, boxSizing: 'border-box', position: 'relative'
          }}>
            {props.sidebar ? null : <MacTrafficLights inactive={props.inactive} dark={dark} />}
            <div style={{
              flex: 1, minWidth: 0,
              textAlign: props.sidebar ? 'left' : 'center',
              paddingRight: props.sidebar ? 0 : 52
            }}>
              <div style={{ font: '600 13px/1.2 ' + font, color: t.text, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{props.title}</div>
              {props.subtitle ? <div style={{ font: '11px/1.2 ' + font, color: t.dim, marginTop: 2 }}>{props.subtitle}</div> : null}
            </div>
            {props.toolbar ? <div style={{ display: 'flex', alignItems: 'center', gap: 4, flex: 'none' }}>{props.toolbar}</div> : null}
          </header>
          <main data-td-id={props.id} style={{ flex: 1, minHeight: 0, overflow: 'auto', position: 'relative' }}>
            {props.children}
          </main>
        </div>
      </div>
    );
    const scaled = (
      <div ref={ref} style={{ width: w * s, height: h * s, flex: 'none', position: 'relative' }}>
        <div style={{ position: 'absolute', left: 0, top: 0, transform: `scale(${s})`, transformOrigin: '0 0' }}>{win}</div>
      </div>
    );
    if (fit === true && typeof props.scale !== 'number') {
      return (
        <div style={{
          minHeight: '100vh', width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', boxSizing: 'border-box',
          background: props.desktop || (dark
            ? 'radial-gradient(120% 90% at 20% 10%, #3a3552 0%, #1c1b27 55%, #121219 100%)'
            : 'radial-gradient(120% 90% at 20% 10%, #dfe6f5 0%, #c9cfe6 45%, #b7bcd8 100%)')
        }}>{scaled}</div>
      );
    }
    return scaled;
  }

  Object.assign(window, { MacWindow, MacTrafficLights, MacSidebar, MacToolbarButton });
})();
