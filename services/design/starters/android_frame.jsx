/*
 * android_frame.jsx — TeleDesign starter: an Android-style device frame for phone screens.
 * Load with <script type="text/babel" src="android_frame.jsx"></script> after React 18 + Babel.
 * Original code (generic modern-phone proportions). Exposes on window:
 *
 *   <AndroidFrame appearance="light" background="#fff" time="12:30">
 *     <YourScreen />
 *   </AndroidFrame>
 *
 * Props
 *   width / height   screen size in CSS px (default 412 × 915)
 *   appearance       "light" (dark status-bar glyphs) | "dark" (light glyphs)       default "light"
 *   background       screen background                                                default #fff / #111
 *   time             status-bar clock text                                           default "12:30"
 *   safeArea         pad content below the status bar and above the gesture bar      default true
 *   statusBar / navBar          set false to hide either                              default true
 *   navigation       "gesture" (pill) | "buttons" (back / home / recents)            default "gesture"
 *   fit              true → scale to fit the viewport · "parent" → fit the parent box · false → 1:1
 *   scale            fixed scale (overrides fit)
 *   color            frame finish: "obsidian" | "porcelain" | "bay" | "hazel"         default "obsidian"
 *   caption          small label under the device
 *   id               becomes data-td-id on the screen element
 *
 * Also: AndroidStatusBar, AndroidNavBar, ANDROID_SAFE = {top: 40, bottom: 24}.
 */
(function () {
  const R = window.React;
  const { useLayoutEffect, useRef, useState } = R;

  const ANDROID_SAFE = { top: 40, bottom: 24 };
  const BEZEL = 11;
  const FINISH = {
    obsidian: ['#2d2f33', '#1a1b1e', '#45484d'],
    porcelain: ['#ece8e1', '#cdc8bf', '#f7f5f1'],
    bay: ['#9fb7c9', '#7d97aa', '#c3d4e1'],
    hazel: ['#a9ad96', '#8a8e77', '#c7cab6']
  };

  function useDeviceFit(fit, fixed, w, h) {
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
        const pad = 32;
        const v = Math.min((aw - pad * 2) / w, (ah - pad * 2) / h, fit === 'parent' ? 10 : 1.5);
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

  function AndroidStatusBar(props) {
    const fg = props.appearance === 'dark' ? '#f1f3f4' : '#1f1f1f';
    return (
      <div style={{
        position: 'absolute', left: 0, right: 0, top: 0, height: ANDROID_SAFE.top, zIndex: 20, pointerEvents: 'none',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 22px 0 24px',
        boxSizing: 'border-box', color: fg, font: '500 14px/1 "Roboto", "Google Sans", "Inter", "Segoe UI", system-ui, sans-serif',
        letterSpacing: '0.01em'
      }}>
        <span>{props.time || '12:30'}</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 20.5 1.2 9.7A15.3 15.3 0 0 1 12 5.5c4.2 0 8 1.6 10.8 4.2L12 20.5z" fill={fg} />
          </svg>
          <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M21 3v18H3L21 3z" fill={fg} />
          </svg>
          <svg width="10" height="16" viewBox="0 0 10 18" aria-hidden="true">
            <rect x="3.25" y="0.5" width="3.5" height="2" rx="0.6" fill={fg} />
            <rect x="0.75" y="2.25" width="8.5" height="15" rx="1.6" fill="none" stroke={fg} strokeWidth="1.5" />
            <rect x="2.25" y="6" width="5.5" height="10" rx="0.6" fill={fg} />
          </svg>
        </span>
      </div>
    );
  }

  function AndroidNavBar(props) {
    const fg = props.appearance === 'dark' ? 'rgba(241,243,244,.9)' : 'rgba(31,31,31,.85)';
    if (props.navigation === 'buttons') {
      return (
        <div aria-hidden="true" style={{
          position: 'absolute', left: 0, right: 0, bottom: 0, height: 48, zIndex: 20, pointerEvents: 'none',
          display: 'flex', alignItems: 'center', justifyContent: 'space-around', padding: '0 56px', boxSizing: 'border-box'
        }}>
          <svg width="18" height="18" viewBox="0 0 18 18"><path d="M13 2 4 9l9 7V2z" fill="none" stroke={fg} strokeWidth="1.8" strokeLinejoin="round" /></svg>
          <svg width="18" height="18" viewBox="0 0 18 18"><circle cx="9" cy="9" r="7" fill="none" stroke={fg} strokeWidth="1.8" /></svg>
          <svg width="18" height="18" viewBox="0 0 18 18"><rect x="2.5" y="2.5" width="13" height="13" rx="2" fill="none" stroke={fg} strokeWidth="1.8" /></svg>
        </div>
      );
    }
    return (
      <div aria-hidden="true" style={{
        position: 'absolute', left: '50%', bottom: 10, width: 108, height: 4, marginLeft: -54, borderRadius: 2,
        background: fg, zIndex: 20, pointerEvents: 'none'
      }} />
    );
  }

  const androidFrameCss = `
  .tdf-and-screen{scrollbar-width:none}
  .tdf-and-screen::-webkit-scrollbar{display:none}
  `;

  function AndroidFrame(props) {
    const w = props.width || 412;
    const h = props.height || 915;
    const appearance = props.appearance || 'light';
    const fit = props.fit === undefined ? true : props.fit;
    const nav = props.navigation || 'gesture';
    const [ref, s] = useDeviceFit(fit, props.scale, w + BEZEL * 2 + 8, h + BEZEL * 2 + (props.caption ? 40 : 0));
    const finish = FINISH[props.color] || FINISH.obsidian;
    const outerW = w + BEZEL * 2, outerH = h + BEZEL * 2;
    const screenRadius = Math.round(Math.min(w, h) * 0.085);
    const bg = props.background || (appearance === 'dark' ? '#111214' : '#ffffff');
    const safe = props.safeArea !== false;
    const bottomSafe = nav === 'buttons' ? 48 : ANDROID_SAFE.bottom;
    const device = (
      <div style={{ position: 'relative', width: outerW, height: outerH, flex: 'none' }}>
        <div aria-hidden="true" style={{ position: 'absolute', right: -3, top: 170, width: 4, height: 56, borderRadius: '0 2px 2px 0', background: finish[1] }} />
        <div aria-hidden="true" style={{ position: 'absolute', right: -3, top: 260, width: 4, height: 104, borderRadius: '0 2px 2px 0', background: finish[1] }} />
        <div style={{
          position: 'absolute', inset: 0, borderRadius: screenRadius + BEZEL,
          background: `linear-gradient(160deg, ${finish[2]}, ${finish[0]} 35%, ${finish[1]})`,
          boxShadow: '0 1px 1px rgba(0,0,0,.2), 0 30px 60px -12px rgba(0,0,0,.35), 0 18px 36px -18px rgba(0,0,0,.4)'
        }} />
        <div style={{ position: 'absolute', inset: 2, borderRadius: screenRadius + BEZEL - 2, background: '#070708' }} />
        <div data-td-id={props.id} style={{
          position: 'absolute', left: BEZEL, top: BEZEL, width: w, height: h, borderRadius: screenRadius,
          overflow: 'hidden', background: bg, isolation: 'isolate'
        }}>
          <div className="tdf-and-screen" style={{
            position: 'absolute', inset: 0, overflowY: 'auto', overflowX: 'hidden',
            paddingTop: safe ? ANDROID_SAFE.top : 0, paddingBottom: safe ? bottomSafe : 0, boxSizing: 'border-box'
          }}>
            {props.children}
          </div>
          {props.statusBar === false ? null : <AndroidStatusBar appearance={appearance} time={props.time} />}
          <div aria-hidden="true" style={{
            position: 'absolute', top: 12, left: '50%', width: 22, height: 22, marginLeft: -11, borderRadius: 11,
            background: 'radial-gradient(circle at 38% 38%, #2b3246, #07080b 62%)', zIndex: 30,
            boxShadow: '0 0 0 1.5px rgba(0,0,0,.6)'
          }} />
          {props.navBar === false ? null : <AndroidNavBar appearance={appearance} navigation={nav} />}
        </div>
      </div>
    );
    const scaled = (
      <div ref={ref} style={{ width: outerW * s, height: (outerH + (props.caption ? 40 : 0)) * s, flex: 'none', position: 'relative' }}>
        <div style={{ position: 'absolute', left: 0, top: 0, transform: `scale(${s})`, transformOrigin: '0 0' }}>
          {device}
          {props.caption
            ? <div style={{ width: outerW, marginTop: 16, textAlign: 'center', font: '500 14px/1.3 ui-sans-serif, system-ui, sans-serif', color: '#6b7280' }}>{props.caption}</div>
            : null}
        </div>
      </div>
    );
    return (
      <R.Fragment>
        <style>{androidFrameCss}</style>
        {fit === true && typeof props.scale !== 'number'
          ? <div style={{ minHeight: '100vh', width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', boxSizing: 'border-box' }}>{scaled}</div>
          : scaled}
      </R.Fragment>
    );
  }

  Object.assign(window, { AndroidFrame, AndroidStatusBar, AndroidNavBar, ANDROID_SAFE });
})();
