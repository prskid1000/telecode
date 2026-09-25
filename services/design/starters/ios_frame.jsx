/*
 * ios_frame.jsx — TeleDesign starter: an iPhone-style device frame for phone screens.
 * Load with <script type="text/babel" src="ios_frame.jsx"></script> after React 18 + Babel.
 * Original code (generic modern-phone proportions; no Apple artwork). Exposes on window:
 *
 *   <IOSFrame appearance="light" background="#fff" time="9:41">
 *     <YourScreen />
 *   </IOSFrame>
 *
 * Props
 *   width / height   screen size in CSS px (default 390 × 844)
 *   appearance       "light" (dark status-bar glyphs) | "dark" (light glyphs)       default "light"
 *   background       screen background                                                default white / #000
 *   time             status-bar clock text                                           default "9:41"
 *   safeArea         pad content below the status bar and above the home indicator   default true
 *   statusBar / homeIndicator   set false to hide either                              default true
 *   fit              true → scale to fit the viewport · "parent" → fit the parent box · false → 1:1
 *   scale            fixed scale (overrides fit)
 *   color            frame finish: "graphite" | "silver" | "sand" | "midnight"         default "graphite"
 *   caption          small label under the device
 *   id               becomes data-td-id on the screen element
 *
 * Also: IOSStatusBar, IOSHomeIndicator, IOS_SAFE = {top: 54, bottom: 34}.
 * The screen element scrolls (scrollbar hidden) — scroll it with element.scrollTo, never
 * scrollIntoView.
 */
(function () {
  const R = window.React;
  const { useLayoutEffect, useRef, useState } = R;

  const IOS_SAFE = { top: 54, bottom: 34 };
  const BEZEL = 13;
  const FINISH = {
    graphite: ['#4a4d52', '#26282b', '#6b6e73'],
    silver: ['#e3e4e6', '#b9bbbe', '#f4f5f6'],
    sand: ['#d8cbb8', '#b3a48d', '#ebe1d2'],
    midnight: ['#2b3140', '#141821', '#454c5e']
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

  function IOSStatusBar(props) {
    const fg = props.appearance === 'dark' ? '#fff' : '#000';
    return (
      <div style={{
        position: 'absolute', left: 0, right: 0, top: 0, height: IOS_SAFE.top, zIndex: 20, pointerEvents: 'none',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '4px 30px 0 44px',
        boxSizing: 'border-box', color: fg,
        font: '600 17px/1 -apple-system, "SF Pro Text", "Inter", "Segoe UI", system-ui, sans-serif', letterSpacing: '-0.02em'
      }}>
        <span style={{ width: 54, textAlign: 'center' }}>{props.time || '9:41'}</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <svg width="18" height="12" viewBox="0 0 18 12" aria-hidden="true">
            <rect x="0" y="8" width="3" height="4" rx="1" fill={fg} />
            <rect x="5" y="5.5" width="3" height="6.5" rx="1" fill={fg} />
            <rect x="10" y="3" width="3" height="9" rx="1" fill={fg} />
            <rect x="15" y="0" width="3" height="12" rx="1" fill={fg} />
          </svg>
          <svg width="16" height="12" viewBox="0 0 16 12" aria-hidden="true">
            <path d="M8 2.3c2.3 0 4.4.9 6 2.4l1.2-1.3A10.3 10.3 0 0 0 8 .5C5.2.5 2.7 1.6.8 3.4L2 4.7a8.5 8.5 0 0 1 6-2.4z" fill={fg} />
            <path d="M8 5.9c1.3 0 2.5.5 3.4 1.3l1.2-1.3A6.7 6.7 0 0 0 8 4.1c-1.8 0-3.4.7-4.6 1.8l1.2 1.3c.9-.8 2.1-1.3 3.4-1.3z" fill={fg} />
            <path d="M8 9.5c.4 0 .8.2 1.1.4L8 11.2 6.9 9.9c.3-.2.7-.4 1.1-.4z" fill={fg} />
            <path d="M8 7.7c.9 0 1.6.3 2.2.8L8 11.2 5.8 8.5c.6-.5 1.3-.8 2.2-.8z" fill={fg} />
          </svg>
          <svg width="27" height="13" viewBox="0 0 27 13" aria-hidden="true">
            <rect x="0.5" y="0.5" width="23" height="12" rx="3.8" fill="none" stroke={fg} strokeOpacity="0.4" />
            <rect x="2" y="2" width="20" height="9" rx="2.5" fill={fg} />
            <path d="M25 4.5v4c.8-.3 1.4-1.1 1.4-2s-.6-1.7-1.4-2z" fill={fg} fillOpacity="0.45" />
          </svg>
        </span>
      </div>
    );
  }

  function IOSHomeIndicator(props) {
    return (
      <div aria-hidden="true" style={{
        position: 'absolute', left: '50%', bottom: 8, width: 139, height: 5, marginLeft: -69.5, borderRadius: 3,
        background: props.appearance === 'dark' ? 'rgba(255,255,255,.92)' : 'rgba(0,0,0,.88)', zIndex: 20, pointerEvents: 'none'
      }} />
    );
  }

  const iosFrameCss = `
  .tdf-ios-screen{scrollbar-width:none}
  .tdf-ios-screen::-webkit-scrollbar{display:none}
  `;

  function IOSFrame(props) {
    const w = props.width || 390;
    const h = props.height || 844;
    const appearance = props.appearance || 'light';
    const fit = props.fit === undefined ? true : props.fit;
    const [ref, s] = useDeviceFit(fit, props.scale, w + BEZEL * 2 + 8, h + BEZEL * 2 + (props.caption ? 40 : 0));
    const finish = FINISH[props.color] || FINISH.graphite;
    const outerW = w + BEZEL * 2, outerH = h + BEZEL * 2;
    const screenRadius = Math.round(Math.min(w, h) * 0.141);
    const bg = props.background || (appearance === 'dark' ? '#000' : '#fff');
    const safe = props.safeArea !== false;
    const btn = (side, top, height) => (
      <div aria-hidden="true" style={{
        position: 'absolute', top, height, width: 4, [side]: -3, borderRadius: side === 'left' ? '2px 0 0 2px' : '0 2px 2px 0',
        background: `linear-gradient(${side === 'left' ? 90 : 270}deg, ${finish[1]}, ${finish[0]})`
      }} />
    );
    const device = (
      <div style={{ position: 'relative', width: outerW, height: outerH, flex: 'none' }}>
        {btn('left', 120, 32)}
        {btn('left', 176, 62)}
        {btn('left', 250, 62)}
        {btn('right', 200, 96)}
        <div style={{
          position: 'absolute', inset: 0, borderRadius: screenRadius + BEZEL,
          background: `linear-gradient(145deg, ${finish[2]}, ${finish[0]} 30%, ${finish[1]} 70%, ${finish[2]})`,
          boxShadow: '0 1px 1px rgba(0,0,0,.2), 0 30px 60px -12px rgba(0,0,0,.35), 0 18px 36px -18px rgba(0,0,0,.4)'
        }} />
        <div style={{ position: 'absolute', inset: 2.5, borderRadius: screenRadius + BEZEL - 2.5, background: '#050506' }} />
        <div className="tdf-ios-screen" data-td-id={props.id} style={{
          position: 'absolute', left: BEZEL, top: BEZEL, width: w, height: h, borderRadius: screenRadius,
          overflow: 'hidden', background: bg, isolation: 'isolate'
        }}>
          <div className="tdf-ios-screen" style={{
            position: 'absolute', inset: 0, overflowY: 'auto', overflowX: 'hidden',
            paddingTop: safe ? IOS_SAFE.top : 0, paddingBottom: safe ? IOS_SAFE.bottom : 0, boxSizing: 'border-box'
          }}>
            {props.children}
          </div>
          {props.statusBar === false ? null : <IOSStatusBar appearance={appearance} time={props.time} />}
          <div aria-hidden="true" style={{
            position: 'absolute', top: 11, left: '50%', width: 124, height: 36, marginLeft: -62, borderRadius: 20,
            background: '#000', zIndex: 30
          }}>
            <div style={{ position: 'absolute', right: 22, top: 12, width: 12, height: 12, borderRadius: 6, background: 'radial-gradient(circle at 35% 35%, #2a3350, #0b0d14 60%)' }} />
          </div>
          {props.homeIndicator === false ? null : <IOSHomeIndicator appearance={appearance} />}
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
        <style>{iosFrameCss}</style>
        {fit === true && typeof props.scale !== 'number'
          ? <div style={{ minHeight: '100vh', width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', boxSizing: 'border-box' }}>{scaled}</div>
          : scaled}
      </R.Fragment>
    );
  }

  Object.assign(window, { IOSFrame, IOSStatusBar, IOSHomeIndicator, IOS_SAFE });
})();
