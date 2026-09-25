/*
 * browser_window.jsx — TeleDesign starter: web-browser chrome around a website design.
 * Load with <script type="text/babel" src="browser_window.jsx"></script> after React 18 + Babel.
 * Original, brand-neutral chrome. Exposes on window:
 *
 *   <BrowserWindow url="acme.com/pricing" tabs={[{title: "Pricing — Acme", active: true}, {title: "Docs"}]}>
 *     <YourWebsite />
 *   </BrowserWindow>
 *
 * Props
 *   width / height   window size in CSS px (default 1440 × 900; the page viewport is smaller by
 *                    the chrome height: 84px with tabs, 48px with `tabs={false}`)
 *   url              address-bar text (no network access happens)                  default "example.com"
 *   secure           padlock in the address bar                                     default true
 *   tabs             [{title, active?, favicon? (node or colour string)}] or false  default one tab from `title`
 *   title            used for the default tab                                       default the url host
 *   appearance       "light" | "dark"                                               default "light"
 *   background       page background                                                default white / #121212
 *   fit              true → scale down to fit the viewport · "parent" → fit parent · false → 1:1
 *   scale            fixed scale (overrides fit)
 *   desktop          backdrop behind the window when fitted (CSS background)
 *   id               becomes data-td-id on the page element (the scrolling viewport)
 *
 * The page element scrolls; use element.scrollTo on it, never scrollIntoView. Also exported:
 * BrowserTabs, BrowserToolbar, BROWSER_CHROME = {withTabs: 84, withoutTabs: 48}.
 */
(function () {
  const R = window.React;
  const { useLayoutEffect, useRef, useState } = R;

  const BROWSER_CHROME = { withTabs: 84, withoutTabs: 48 };

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
        const pad = fit === 'parent' ? 0 : 32;
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

  const browserTheme = {
    light: {
      strip: '#e8eaed', tab: '#ffffff', tabText: '#1f1f1f', tabDim: '#5f6368', bar: '#ffffff', omni: '#f1f3f4',
      omniText: '#1f1f1f', omniDim: '#5f6368', icon: '#5f6368', border: 'rgba(0,0,0,.1)', page: '#ffffff',
      frame: '0 0 0 1px rgba(0,0,0,.12), 0 24px 70px rgba(15,23,42,.22), 0 6px 18px rgba(15,23,42,.1)'
    },
    dark: {
      strip: '#1f1f22', tab: '#35363a', tabText: '#e8eaed', tabDim: '#9aa0a6', bar: '#35363a', omni: '#202124',
      omniText: '#e8eaed', omniDim: '#9aa0a6', icon: '#c4c7c5', border: 'rgba(0,0,0,.5)', page: '#121212',
      frame: '0 0 0 1px rgba(255,255,255,.08), 0 24px 70px rgba(0,0,0,.6)'
    }
  };

  const dots = (
    <div aria-hidden="true" style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '0 14px 0 16px', flex: 'none' }}>
      <span style={{ width: 12, height: 12, borderRadius: 6, background: '#ff5f57', boxShadow: 'inset 0 0 0 .5px #e0443e' }} />
      <span style={{ width: 12, height: 12, borderRadius: 6, background: '#febc2e', boxShadow: 'inset 0 0 0 .5px #dea123' }} />
      <span style={{ width: 12, height: 12, borderRadius: 6, background: '#28c840', boxShadow: 'inset 0 0 0 .5px #1aab29' }} />
    </div>
  );

  function Favicon(props) {
    const f = props.favicon;
    if (f && typeof f !== 'string') return <span style={{ width: 16, height: 16, display: 'flex', flex: 'none' }}>{f}</span>;
    const letter = (props.title || '?').trim().charAt(0).toUpperCase();
    return (
      <span aria-hidden="true" style={{
        width: 16, height: 16, borderRadius: 4, flex: 'none', background: f || '#8b5cf6', color: '#fff',
        font: '700 10px/16px ui-sans-serif, system-ui, sans-serif', textAlign: 'center'
      }}>{letter}</span>
    );
  }

  function BrowserTabs(props) {
    const t = browserTheme[props.appearance === 'dark' ? 'dark' : 'light'];
    const tabs = props.tabs || [];
    return (
      <div style={{ height: 40, display: 'flex', alignItems: 'flex-end', background: t.strip, flex: 'none', paddingRight: 8 }}>
        <div style={{ alignSelf: 'center' }}>{dots}</div>
        <div style={{ display: 'flex', alignItems: 'flex-end', minWidth: 0, flex: 1, gap: 0 }}>
          {tabs.map((tab, i) => {
            const active = !!tab.active;
            return (
              <div key={i} style={{
                position: 'relative', height: 34, width: 236, minWidth: 72, flexShrink: 1, display: 'flex', alignItems: 'center',
                gap: 8, padding: '0 10px 0 12px', boxSizing: 'border-box', borderRadius: '10px 10px 0 0',
                background: active ? t.tab : 'transparent', color: active ? t.tabText : t.tabDim,
                font: '12.5px/1 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif', zIndex: active ? 1 : 0
              }}>
                {active
                  ? <R.Fragment>
                      <span aria-hidden="true" style={{ position: 'absolute', left: -10, bottom: 0, width: 10, height: 10, background: `radial-gradient(circle at 0 0, transparent 10px, ${t.tab} 10.5px)` }} />
                      <span aria-hidden="true" style={{ position: 'absolute', right: -10, bottom: 0, width: 10, height: 10, background: `radial-gradient(circle at 100% 0, transparent 10px, ${t.tab} 10.5px)` }} />
                    </R.Fragment>
                  : (i > 0 && !(tabs[i - 1] && tabs[i - 1].active)
                    ? <span aria-hidden="true" style={{ position: 'absolute', left: 0, top: 10, bottom: 10, width: 1, background: t.border }} />
                    : null)}
                <Favicon favicon={tab.favicon} title={tab.title} />
                <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis', lineHeight: '18px' }}>{tab.title}</span>
                <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" style={{ flex: 'none', opacity: active ? 0.8 : 0.55 }}>
                  <path d="M5 5l6 6M11 5l-6 6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                </svg>
              </div>
            );
          })}
          <div aria-hidden="true" style={{ height: 34, width: 34, display: 'flex', alignItems: 'center', justifyContent: 'center', color: t.tabDim }}>
            <svg width="16" height="16" viewBox="0 0 16 16"><path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
          </div>
        </div>
      </div>
    );
  }

  function BrowserToolbar(props) {
    const t = browserTheme[props.appearance === 'dark' ? 'dark' : 'light'];
    const url = props.url || 'example.com';
    const m = /^(https?:\/\/)?([^/?#]+)(.*)$/.exec(url) || [];
    const host = m[2] || url;
    const rest = m[3] || '';
    const ic = { width: 32, height: 32, display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 16, color: t.icon, flex: 'none' };
    return (
      <div style={{
        height: 44, display: 'flex', alignItems: 'center', gap: 2, padding: '0 8px', background: t.bar,
        borderBottom: '1px solid ' + t.border, flex: 'none', boxSizing: 'border-box'
      }}>
        {props.noTabs ? dots : null}
        <span style={ic} aria-hidden="true"><svg width="18" height="18" viewBox="0 0 18 18"><path d="M11 4 6 9l5 5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg></span>
        <span style={Object.assign({}, ic, { opacity: 0.45 })} aria-hidden="true"><svg width="18" height="18" viewBox="0 0 18 18"><path d="m7 4 5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg></span>
        <span style={ic} aria-hidden="true"><svg width="18" height="18" viewBox="0 0 18 18"><path d="M14 9a5 5 0 1 1-1.5-3.6M14 3.5V6h-2.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg></span>
        <div style={{
          flex: 1, minWidth: 0, height: 32, margin: '0 8px 0 6px', borderRadius: 16, background: t.omni, display: 'flex',
          alignItems: 'center', gap: 8, padding: '0 14px', boxSizing: 'border-box',
          font: '13.5px/1 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif'
        }}>
          {props.secure === false
            ? <span style={{ color: t.omniDim, fontSize: 12 }}>Not secure</span>
            : <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" style={{ color: t.omniDim, flex: 'none' }}>
                <rect x="2.5" y="6" width="9" height="6.5" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
                <path d="M4.5 6V4.5a2.5 2.5 0 0 1 5 0V6" fill="none" stroke="currentColor" strokeWidth="1.3" />
              </svg>}
          <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', lineHeight: '20px' }}>
            <span style={{ color: t.omniText }}>{host}</span><span style={{ color: t.omniDim }}>{rest}</span>
          </span>
          <span style={{ flex: 1 }} />
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" style={{ color: t.omniDim, flex: 'none' }}>
            <path d="m8 2 1.8 3.8 4.2.5-3.1 2.9.8 4.1L8 11.3l-3.7 2 .8-4.1L2 6.3l4.2-.5L8 2z" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
          </svg>
        </div>
        <span style={ic} aria-hidden="true">
          <span style={{ width: 24, height: 24, borderRadius: 12, background: 'linear-gradient(135deg, #a78bfa, #60a5fa)', display: 'block' }} />
        </span>
        <span style={ic} aria-hidden="true"><svg width="18" height="18" viewBox="0 0 18 18"><circle cx="9" cy="4" r="1.3" fill="currentColor" /><circle cx="9" cy="9" r="1.3" fill="currentColor" /><circle cx="9" cy="14" r="1.3" fill="currentColor" /></svg></span>
      </div>
    );
  }

  const browserFrameCss = `
  .tdf-browser-page{scrollbar-width:thin}
  `;

  function BrowserWindow(props) {
    const w = props.width || 1440;
    const h = props.height || 900;
    const dark = props.appearance === 'dark';
    const t = browserTheme[dark ? 'dark' : 'light'];
    const fit = props.fit === undefined ? true : props.fit;
    const [ref, s] = useWindowFit(fit, props.scale, w, h);
    const url = props.url || 'example.com';
    const host = (/^(?:https?:\/\/)?([^/?#]+)/.exec(url) || [])[1] || url;
    const tabs = props.tabs === false ? null : (props.tabs || [{ title: props.title || host, active: true }]);
    const win = (
      <div style={{
        width: w, height: h, borderRadius: 12, overflow: 'hidden', display: 'flex', flexDirection: 'column',
        background: props.background || t.page, boxShadow: t.frame, flex: 'none'
      }}>
        {tabs ? <BrowserTabs tabs={tabs} appearance={props.appearance} /> : null}
        <BrowserToolbar url={url} secure={props.secure} appearance={props.appearance} noTabs={!tabs} />
        <div className="tdf-browser-page" data-td-id={props.id} style={{ flex: 1, minHeight: 0, overflow: 'auto', position: 'relative', background: props.background || t.page }}>
          {props.children}
        </div>
      </div>
    );
    const scaled = (
      <div ref={ref} style={{ width: w * s, height: h * s, flex: 'none', position: 'relative' }}>
        <style>{browserFrameCss}</style>
        <div style={{ position: 'absolute', left: 0, top: 0, transform: `scale(${s})`, transformOrigin: '0 0' }}>{win}</div>
      </div>
    );
    if (fit === true && typeof props.scale !== 'number') {
      return (
        <div style={{
          minHeight: '100vh', width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', boxSizing: 'border-box',
          background: props.desktop || (dark ? 'linear-gradient(160deg, #1b1c22, #0e0f13)' : 'linear-gradient(160deg, #eef1f6, #dde3ec)')
        }}>{scaled}</div>
      );
    }
    return scaled;
  }

  Object.assign(window, { BrowserWindow, BrowserTabs, BrowserToolbar, BROWSER_CHROME });
})();
