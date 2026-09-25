/*
 * animations.jsx — TeleDesign starter: timeline animation stage (prompts/kinds/animation.md).
 * Load with <script type="text/babel" src="animations.jsx"></script> after React 18 + Babel.
 * Original code. Exposes on window:
 *
 *   <Stage width={1920} height={1080} duration={12} fps={30} background="#0b0b10"
 *          autoplay loop markers={[{t: 0, label: "Intro"}, …]}>
 *     <Sprite start={0} end={3}>{(s) => <h1 style={entryExit(s, {from: "up"})}>Hello</h1>}</Sprite>
 *     <Sprite start={2.5} end={8}><Card /></Sprite>          // Card calls useSprite()
 *   </Stage>
 *
 *   useTime()            → seconds on the global timeline (0 outside a Stage)
 *   useSprite()          → {t, progress, start, end, duration, active} for the enclosing Sprite
 *   useTimeline()        → {time, duration, fps, playing, play, pause, seek}
 *   Easing.*             → linear, inQuad, outQuad, inOutQuad, inCubic, outCubic, inOutCubic,
 *                          inQuart, outQuart, inOutQuart, inExpo, outExpo, inOutExpo, inSine,
 *                          outSine, inOutSine, inBack, outBack, inOutBack, outElastic, outBounce,
 *                          bezier(x1, y1, x2, y2), steps(n)
 *   interpolate(t, [t0, t1, …], [v0, v1, …], easing?) → number | "#rrggbb" | "rgba(…)" | [numbers]
 *                          (clamped; `easing` is a function or an array, one per segment)
 *   entryExit(sprite, {from, in, out, distance, easeIn, easeOut, scale}) → style {opacity, transform}
 *   <Reveal from="up" in={0.6} out={0.4}>…</Reveal>          (inside a Sprite)
 *   <Scrubber />         (the Stage renders one; use it standalone inside custom layouts)
 *
 *   window.tdTimeline = {duration, fps, seek(t), play(), pause(), time, playing}
 *   seek(t) renders synchronously (flushSync) so the MP4 exporter can capture every frame.
 *   The playhead persists in the hash (#t=4.2). Keys: Space play/pause, ←/→ one frame
 *   (Shift: 1 s), Home/End. prefers-reduced-motion starts paused.
 *
 * Every property must be a pure function of time — no setTimeout chains or self-running CSS
 * animations; they cannot be scrubbed or exported.
 */
(function () {
  const R = window.React;
  const RD = window.ReactDOM;
  const { createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, useCallback } = R;

  // ---------------------------------------------------------------------------------------------
  // Easing
  // ---------------------------------------------------------------------------------------------

  const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
  const c1 = 1.70158, c2 = c1 * 1.525, c3 = c1 + 1, c4 = (2 * Math.PI) / 3;
  const outBounce = (x) => {
    const n1 = 7.5625, d1 = 2.75;
    if (x < 1 / d1) return n1 * x * x;
    if (x < 2 / d1) return n1 * (x -= 1.5 / d1) * x + 0.75;
    if (x < 2.5 / d1) return n1 * (x -= 2.25 / d1) * x + 0.9375;
    return n1 * (x -= 2.625 / d1) * x + 0.984375;
  };
  function bezier(x1, y1, x2, y2) {
    // cubic-bezier(x1, y1, x2, y2) like CSS; Newton + bisection on x.
    const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
    const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
    const sx = (t) => ((ax * t + bx) * t + cx) * t;
    const sy = (t) => ((ay * t + by) * t + cy) * t;
    const dx = (t) => (3 * ax * t + 2 * bx) * t + cx;
    return (x) => {
      x = clamp01(x);
      let t = x;
      for (let i = 0; i < 8; i++) {
        const e = sx(t) - x;
        if (Math.abs(e) < 1e-6) return sy(t);
        const d = dx(t);
        if (Math.abs(d) < 1e-6) break;
        t -= e / d;
      }
      let lo = 0, hi = 1;
      t = x;
      for (let i = 0; i < 30; i++) {
        const v = sx(t);
        if (Math.abs(v - x) < 1e-6) break;
        if (v < x) lo = t; else hi = t;
        t = (lo + hi) / 2;
      }
      return sy(t);
    };
  }
  const Easing = {
    linear: (x) => x,
    inQuad: (x) => x * x,
    outQuad: (x) => 1 - (1 - x) * (1 - x),
    inOutQuad: (x) => (x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2),
    inCubic: (x) => x * x * x,
    outCubic: (x) => 1 - Math.pow(1 - x, 3),
    inOutCubic: (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2),
    inQuart: (x) => x * x * x * x,
    outQuart: (x) => 1 - Math.pow(1 - x, 4),
    inOutQuart: (x) => (x < 0.5 ? 8 * x * x * x * x : 1 - Math.pow(-2 * x + 2, 4) / 2),
    inExpo: (x) => (x === 0 ? 0 : Math.pow(2, 10 * x - 10)),
    outExpo: (x) => (x === 1 ? 1 : 1 - Math.pow(2, -10 * x)),
    inOutExpo: (x) => (x === 0 ? 0 : x === 1 ? 1 : x < 0.5 ? Math.pow(2, 20 * x - 10) / 2 : (2 - Math.pow(2, -20 * x + 10)) / 2),
    inSine: (x) => 1 - Math.cos((x * Math.PI) / 2),
    outSine: (x) => Math.sin((x * Math.PI) / 2),
    inOutSine: (x) => -(Math.cos(Math.PI * x) - 1) / 2,
    inBack: (x) => c3 * x * x * x - c1 * x * x,
    outBack: (x) => 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2),
    inOutBack: (x) => (x < 0.5
      ? (Math.pow(2 * x, 2) * ((c2 + 1) * 2 * x - c2)) / 2
      : (Math.pow(2 * x - 2, 2) * ((c2 + 1) * (x * 2 - 2) + c2) + 2) / 2),
    outElastic: (x) => (x === 0 ? 0 : x === 1 ? 1 : Math.pow(2, -10 * x) * Math.sin((x * 10 - 0.75) * c4) + 1),
    outBounce,
    bezier,
    steps: (n) => (x) => Math.min(1, Math.floor(clamp01(x) * n) / n),
    // The shared set the animation prompt asks for:
    enter: bezier(0.16, 1, 0.3, 1),   // ease-out for entrances
    exit: bezier(0.7, 0, 0.84, 0),    // ease-in for exits
    move: bezier(0.65, 0, 0.35, 1)    // gentle in-out for moves
  };

  // ---------------------------------------------------------------------------------------------
  // interpolate
  // ---------------------------------------------------------------------------------------------

  function parseColor(s) {
    if (typeof s !== 'string') return null;
    let m = /^#([0-9a-f]{3,8})$/i.exec(s.trim());
    if (m) {
      let h = m[1];
      if (h.length === 3 || h.length === 4) h = h.split('').map((c) => c + c).join('');
      const n = parseInt(h.slice(0, 6), 16);
      const a = h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1;
      return [(n >> 16) & 255, (n >> 8) & 255, n & 255, a];
    }
    m = /^rgba?\(([^)]+)\)$/i.exec(s.trim());
    if (m) {
      const p = m[1].split(/[\s,/]+/).filter(Boolean).map(parseFloat);
      return [p[0] || 0, p[1] || 0, p[2] || 0, p[3] == null ? 1 : p[3]];
    }
    return null;
  }
  function formatColor(c) {
    const r = Math.round(c[0]), g = Math.round(c[1]), b = Math.round(c[2]);
    if (c[3] >= 0.999) return '#' + [r, g, b].map((x) => ('0' + Math.max(0, Math.min(255, x)).toString(16)).slice(-2)).join('');
    return `rgba(${r}, ${g}, ${b}, ${Math.round(c[3] * 1000) / 1000})`;
  }
  function mix(a, b, p) {
    if (typeof a === 'number' && typeof b === 'number') return a + (b - a) * p;
    if (Array.isArray(a) && Array.isArray(b)) return a.map((v, i) => mix(v, b[i], p));
    const ca = parseColor(a), cb = parseColor(b);
    if (ca && cb) return formatColor(ca.map((v, i) => v + (cb[i] - v) * p));
    return p < 1 ? a : b;
  }
  function interpolate(t, input, output, easing) {
    if (!input || !output || !input.length) return output ? output[0] : undefined;
    if (t <= input[0]) return output[0];
    const last = input.length - 1;
    if (t >= input[last]) return output[last];
    let i = 0;
    while (i < last - 1 && t >= input[i + 1]) i++;
    const span = input[i + 1] - input[i];
    let p = span > 0 ? (t - input[i]) / span : 1;
    const ease = Array.isArray(easing) ? easing[i] : easing;
    if (typeof ease === 'function') p = ease(p);
    return mix(output[i], output[i + 1], p);
  }

  // ---------------------------------------------------------------------------------------------
  // Contexts + hooks
  // ---------------------------------------------------------------------------------------------

  const TimeContext = createContext(null);
  const SpriteContext = createContext(null);

  function useTimeline() {
    const ctx = useContext(TimeContext);
    return ctx || { time: 0, duration: 0, fps: 30, playing: false, play() {}, pause() {}, seek() {} };
  }
  function useTime() {
    const ctx = useContext(TimeContext);
    return ctx ? ctx.time : 0;
  }
  function spriteState(time, start, end) {
    const e = isFinite(end) ? end : Infinity;
    const duration = isFinite(e) ? e - start : Infinity;
    const t = time - start;
    const progress = isFinite(duration) && duration > 0 ? clamp01(t / duration) : 0;
    return { t, progress, start, end: e, duration, active: time >= start && time < e };
  }
  function useSprite() {
    const s = useContext(SpriteContext);
    const time = useTime();
    return s || spriteState(time, 0, Infinity);
  }

  // ---------------------------------------------------------------------------------------------
  // Sprite + entry/exit helpers
  // ---------------------------------------------------------------------------------------------

  function Sprite(props) {
    const time = useTime();
    const start = props.start || 0;
    const end = props.end == null ? Infinity : props.end;
    const s = spriteState(time, start, end);
    if (!s.active && !props.keepMounted) return null;
    const content = typeof props.children === 'function' ? props.children(s) : props.children;
    return <SpriteContext.Provider value={s}>{content}</SpriteContext.Provider>;
  }

  // `from` = where the element comes from; it exits continuing in the same travel direction.
  //   "bottom" (alias "up": travels upward) · "top" (alias "down") · "left" · "right"
  //   "fade" (opacity only) · "scale" (grows from opts.scale, default 0.92) · "none"
  const DIRS = { bottom: [0, 1], top: [0, -1], left: [-1, 0], right: [1, 0] };
  const DIR_ALIAS = { up: 'bottom', down: 'top' };
  function entryExit(sprite, opts) {
    const o = opts || {};
    const s = sprite || { t: 0, duration: Infinity };
    const from = o.from || 'up';
    const inDur = o.in == null ? 0.6 : o.in;
    const outDur = o.out == null ? 0.4 : o.out;
    const dist = o.distance == null ? 40 : o.distance;
    const easeIn = o.easeIn || Easing.enter;
    const easeOut = o.easeOut || Easing.exit;
    const pin = inDur > 0 ? easeIn(clamp01(s.t / inDur)) : 1;
    const remain = isFinite(s.duration) ? s.duration - s.t : Infinity;
    const pout = outDur > 0 && isFinite(remain) ? easeOut(clamp01(1 - remain / outDur)) : 0;
    const vis = pin * (1 - pout);
    const key = DIR_ALIAS[from] || from;
    let transform = 'none';
    if (DIRS[key]) {
      const d = DIRS[key];
      // entering: offset shrinks to 0; exiting: continue past in the travel direction
      const k = (1 - pin) * dist - pout * dist;
      transform = `translate3d(${d[0] * k}px, ${d[1] * k}px, 0)`;
    } else if (key === 'scale') {
      const sc = (o.scale == null ? 0.92 : o.scale);
      transform = `scale(${sc + (1 - sc) * pin - pout * (1 - sc)})`;
    }
    return { opacity: key === 'none' ? 1 : vis, transform, willChange: 'opacity, transform' };
  }

  function Reveal(props) {
    const s = useSprite();
    const style = entryExit(s, { from: props.from, in: props.in, out: props.out, distance: props.distance, scale: props.scale, easeIn: props.easeIn, easeOut: props.easeOut });
    const Tag = props.as || 'div';
    return <Tag className={props.className} style={Object.assign({}, props.style, style)}>{props.children}</Tag>;
  }

  // ---------------------------------------------------------------------------------------------
  // Fit-to-viewport helper
  // ---------------------------------------------------------------------------------------------

  function useFit(ref, w, h, reserveBottom) {
    const [scale, setScale] = useState(1);
    useLayoutEffect(() => {
      const el = ref.current;
      if (!el) return undefined;
      const measure = () => {
        const r = el.getBoundingClientRect();
        const avW = r.width || window.innerWidth;
        const avH = (r.height || window.innerHeight) - (reserveBottom || 0);
        const s = Math.min(avW / w, avH / h);
        setScale(isFinite(s) && s > 0 ? s : 1);
      };
      measure();
      let ro = null;
      try { ro = new ResizeObserver(measure); ro.observe(el); } catch (e) { /* ignore */ }
      window.addEventListener('resize', measure);
      return () => {
        window.removeEventListener('resize', measure);
        if (ro) ro.disconnect();
      };
    }, [w, h, reserveBottom]);
    return scale;
  }

  // ---------------------------------------------------------------------------------------------
  // Scrubber
  // ---------------------------------------------------------------------------------------------

  const animScrubberCss = `
  .tda-bar{display:flex;align-items:center;gap:12px;height:52px;padding:0 16px;box-sizing:border-box;
    background:rgba(18,18,22,.86);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);
    border-top:1px solid rgba(255,255,255,.07);color:#e4e4e7;
    font:500 12px/1 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;user-select:none}
  .tda-play{all:unset;box-sizing:border-box;width:34px;height:34px;border-radius:50%;display:flex;
    align-items:center;justify-content:center;cursor:pointer;background:#fafafa;color:#0b0b10;flex:none;
    transition:transform .12s ease}
  .tda-play:hover{transform:scale(1.06)}
  .tda-play:focus-visible{outline:2px solid #60a5fa;outline-offset:2px}
  .tda-time{font-variant-numeric:tabular-nums;min-width:92px;color:#a1a1aa;letter-spacing:.01em}
  .tda-time b{color:#fafafa;font-weight:600}
  .tda-track{position:relative;flex:1;height:34px;cursor:pointer;touch-action:none}
  .tda-rail{position:absolute;left:0;right:0;top:15px;height:4px;border-radius:2px;background:rgba(255,255,255,.14)}
  .tda-fill{position:absolute;left:0;top:15px;height:4px;border-radius:2px;background:#fafafa}
  .tda-knob{position:absolute;top:10px;width:14px;height:14px;margin-left:-7px;border-radius:50%;background:#fff;
    box-shadow:0 1px 4px rgba(0,0,0,.4)}
  .tda-mark{position:absolute;top:6px;width:2px;height:22px;margin-left:-1px;border-radius:1px;background:rgba(255,255,255,.28)}
  .tda-mark span{position:absolute;bottom:26px;left:50%;transform:translateX(-50%);white-space:nowrap;font-size:10px;
    color:#a1a1aa;opacity:0;transition:opacity .15s;pointer-events:none}
  .tda-track:hover .tda-mark span{opacity:1}
  .tda-loop{all:unset;cursor:pointer;padding:6px 8px;border-radius:6px;color:#71717a;font-weight:600}
  .tda-loop.on{color:#fafafa;background:rgba(255,255,255,.08)}
  `;

  function fmt(t) {
    const m = Math.floor(t / 60);
    const s = t - m * 60;
    return m + ':' + (s < 10 ? '0' : '') + s.toFixed(1);
  }

  function Scrubber(props) {
    const tl = useTimeline();
    const trackRef = useRef(null);
    const dragging = useRef(false);
    const wasPlaying = useRef(false);
    const at = (clientX) => {
      const r = trackRef.current.getBoundingClientRect();
      return clamp01((clientX - r.left) / (r.width || 1)) * tl.duration;
    };
    const onDown = (e) => {
      e.preventDefault();
      dragging.current = true;
      wasPlaying.current = tl.playing;
      tl.pause();
      try { e.currentTarget.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      tl.seek(at(e.clientX));
    };
    const onMove = (e) => { if (dragging.current) tl.seek(at(e.clientX)); };
    const onUp = () => {
      if (!dragging.current) return;
      dragging.current = false;
      if (wasPlaying.current) tl.play();
    };
    const pct = tl.duration > 0 ? (tl.time / tl.duration) * 100 : 0;
    return (
      <div className="tda-bar" data-td-id="timeline-controls" data-no-nav style={props.style}>
        <button type="button" className="tda-play" data-td-id="timeline-play"
                aria-label={tl.playing ? 'Pause' : 'Play'} onClick={() => (tl.playing ? tl.pause() : tl.play())}>
          {tl.playing
            ? <svg width="14" height="14" viewBox="0 0 14 14"><rect x="2.5" y="2" width="3" height="10" rx="1" fill="currentColor"/><rect x="8.5" y="2" width="3" height="10" rx="1" fill="currentColor"/></svg>
            : <svg width="14" height="14" viewBox="0 0 14 14"><path d="M4 2.4v9.2a.6.6 0 0 0 .9.5l7.4-4.6a.6.6 0 0 0 0-1L4.9 1.9a.6.6 0 0 0-.9.5z" fill="currentColor"/></svg>}
        </button>
        <div className="tda-time"><b>{fmt(tl.time)}</b> / {fmt(tl.duration)}</div>
        <div className="tda-track" ref={trackRef} role="slider" aria-label="Timeline"
             aria-valuemin={0} aria-valuemax={tl.duration} aria-valuenow={Math.round(tl.time * 10) / 10}
             onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
          <div className="tda-rail" />
          <div className="tda-fill" style={{ width: pct + '%' }} />
          {(props.markers || []).map((m, i) => (
            <div key={i} className="tda-mark" style={{ left: (tl.duration ? (m.t / tl.duration) * 100 : 0) + '%' }}>
              {m.label ? <span>{m.label}</span> : null}
            </div>
          ))}
          <div className="tda-knob" style={{ left: pct + '%' }} />
        </div>
        {props.onToggleLoop
          ? <button type="button" className={'tda-loop' + (props.loop ? ' on' : '')} onClick={props.onToggleLoop}
                    aria-pressed={!!props.loop} title="Loop">Loop</button>
          : null}
      </div>
    );
  }

  // ---------------------------------------------------------------------------------------------
  // Stage
  // ---------------------------------------------------------------------------------------------

  function readHashTime() {
    const m = /(?:^#|[#&])t=([\d.]+)/.exec(location.hash || '');
    return m ? parseFloat(m[1]) : null;
  }
  function writeHashTime(t) {
    try {
      const hash = (location.hash || '').replace(/^#/, '');
      const v = 't=' + (Math.round(t * 10) / 10);
      const next = /(^|&)t=[\d.]+/.test(hash) ? hash.replace(/(^|&)t=[\d.]+/, '$1' + v) : (hash ? hash + '&' : '') + v;
      history.replaceState(history.state, '', '#' + next);
    } catch (e) { /* ignore */ }
  }
  function prefersReducedMotion() {
    try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; }
  }

  function Stage(props) {
    const width = props.width || 1920;
    const height = props.height || 1080;
    const duration = Math.max(0.1, props.duration || 10);
    const fps = props.fps || 30;
    const controls = props.controls !== false;
    const BAR = controls ? 52 : 0;
    const initial = useMemo(() => {
      const h = readHashTime();
      return h != null ? Math.max(0, Math.min(duration, h)) : 0;
    }, []);
    const [time, setTime] = useState(initial);
    const [playing, setPlaying] = useState(() => (props.autoplay !== false && !prefersReducedMotion()));
    const [loop, setLoop] = useState(props.loop !== false);
    const timeRef = useRef(initial);
    const playingRef = useRef(playing);
    const loopRef = useRef(loop);
    const hostRef = useRef(null);
    const scale = useFit(hostRef, width, height, BAR);
    loopRef.current = loop;

    const seekTo = useCallback((t, sync) => {
      const v = Math.max(0, Math.min(duration, +t || 0));
      timeRef.current = v;
      if (sync && RD && RD.flushSync) RD.flushSync(() => setTime(v));
      else setTime(v);
      return v;
    }, [duration]);

    const play = useCallback(() => {
      if (timeRef.current >= duration - 1e-3) seekTo(0);
      playingRef.current = true;
      setPlaying(true);
    }, [duration, seekTo]);
    const pause = useCallback(() => {
      playingRef.current = false;
      setPlaying(false);
      writeHashTime(timeRef.current);
    }, []);
    const seek = useCallback((t) => {
      const v = seekTo(t, true);
      if (!playingRef.current) writeHashTime(v);
      return v;
    }, [seekTo]);

    // playback clock
    useEffect(() => {
      playingRef.current = playing;
      if (!playing) return undefined;
      let raf = 0;
      let last = performance.now();
      let lastHash = 0;
      const tick = (now) => {
        const dt = Math.min(0.1, (now - last) / 1000);
        last = now;
        let t = timeRef.current + dt;
        if (t >= duration) {
          if (loopRef.current) t = t % duration;
          else {
            t = duration;
            seekTo(t);
            playingRef.current = false;
            setPlaying(false);
            writeHashTime(t);
            return;
          }
        }
        seekTo(t);
        if (now - lastHash > 500) { lastHash = now; writeHashTime(t); }
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
      return () => cancelAnimationFrame(raf);
    }, [playing, duration, seekTo]);

    // window.tdTimeline — defined in a layout effect so its presence means "mounted and ready"
    useLayoutEffect(() => {
      const api = {
        duration,
        fps,
        width,
        height,
        seek: (t) => { seek(t); return true; },
        play: () => play(),
        pause: () => pause(),
        get time() { return timeRef.current; },
        get playing() { return playingRef.current; }
      };
      window.tdTimeline = api;
      return () => { if (window.tdTimeline === api) delete window.tdTimeline; };
    }, [duration, fps, width, height, seek, play, pause]);

    // keyboard
    useEffect(() => {
      const onKey = (e) => {
        const t = e.target;
        if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
        if (t && t.closest && t.closest('input, textarea, select, [contenteditable]')) return;
        const frame = 1 / fps;
        if (e.key === ' ' || e.key === 'k') { e.preventDefault(); if (playingRef.current) pause(); else play(); }
        else if (e.key === 'ArrowRight') { e.preventDefault(); pause(); seek(timeRef.current + (e.shiftKey ? 1 : frame)); }
        else if (e.key === 'ArrowLeft') { e.preventDefault(); pause(); seek(timeRef.current - (e.shiftKey ? 1 : frame)); }
        else if (e.key === 'Home') { e.preventDefault(); seek(0); }
        else if (e.key === 'End') { e.preventDefault(); pause(); seek(duration); }
      };
      window.addEventListener('keydown', onKey);
      return () => window.removeEventListener('keydown', onKey);
    }, [fps, duration, play, pause, seek]);

    const ctx = useMemo(() => ({ time, duration, fps, playing, play, pause, seek }), [time, duration, fps, playing, play, pause, seek]);
    const bg = props.background || '#0b0b10';
    const letterbox = props.letterbox || '#050507';

    return (
      <TimeContext.Provider value={ctx}>
        <style>{animScrubberCss}</style>
        <div data-td-id="animation-root"
             style={{ position: 'fixed', inset: 0, display: 'flex', flexDirection: 'column', background: letterbox, overflow: 'hidden' }}>
          <div ref={hostRef} style={{ position: 'relative', flex: 1, minHeight: 0, overflow: 'hidden' }}>
            <div data-td-id="animation-stage" data-td-screen={props.label || '01 Stage'}
                 style={{
                   position: 'absolute', left: '50%', top: '50%', width, height,
                   marginLeft: -width / 2, marginTop: -height / 2,
                   transform: `scale(${scale})`, transformOrigin: '50% 50%',
                   background: bg, overflow: 'hidden', boxShadow: '0 30px 80px rgba(0,0,0,.35)'
                 }}>
              {props.children}
            </div>
          </div>
          {controls
            ? <Scrubber markers={props.markers} loop={loop} onToggleLoop={() => setLoop((v) => !v)} />
            : null}
        </div>
      </TimeContext.Provider>
    );
  }

  Object.assign(window, {
    Stage, Sprite, Scrubber, Reveal,
    useTime, useSprite, useTimeline,
    Easing, interpolate, entryExit,
    TimeContext, SpriteContext
  });
})();
