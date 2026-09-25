// Observability kit (P5) — served from /shared/observe.js after manager.js (uses its h(), icon(),
// btn(), api(), modal(), toast(), fmt*()). Everything is exported on window.TCObserve:
//
//   mountDashboard(host, {range, group, compact})  cost / tokens by day, breakdown by agent|job|trigger|
//                                                  engine|model, failure rate, cache-read ratio, top tools,
//                                                  run outcomes (process ok vs task succeeded), pass^k
//   mountRunTimeline(host, run)                    verdict + outcome check + Gantt of phases/steps/attempts
//                                                  with cost per step (re-fetches when the run changes)
//   continueButton({sessionId, namespace, engine, runId, stepId, onStarted})
//                                                  "Continue on…" → POST /api/sessions/{sid}/continue
//   verdictPills(run)                              process-status pill + verdict pill
//   passkStrip(triggerId, k)                       last-k fires strip (element, fills in async)
//
// Charts are inline SVG / HTML built with createElement(NS) — no innerHTML, server text is always a
// text node. Every chart has a hover tooltip and a "Table" toggle (the accessible view); colours come
// from observe.css roles and never carry meaning alone (status bars also have labels / tooltips).
"use strict";
(function () {
  const SVGNS = "http://www.w3.org/2000/svg";
  function s(tag, attrs, ...kids) {
    const el = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null && v !== false) el.setAttribute(k, String(v));
    for (const c of kids.flat()) if (c != null && c !== false) el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
    return el;
  }
  // Cost with the "incomplete" marker: an engine that reports no cost (Codex, agy) shows "—", not $0.
  const costOf = (v, complete) => (!complete && !v) ? "—" : money(v) + (complete ? "" : "+");
  const money = (v) => v == null ? "—" : v >= 100 ? "$" + v.toFixed(0) : v >= 1 ? "$" + v.toFixed(2) : "$" + v.toFixed(v >= 0.01 ? 3 : 4);
  const pct = (v) => v == null ? "—" : (v * 100).toFixed(v >= 0.1 || v === 0 ? 0 : 1) + "%";
  const ENGINE = { claude_code: "Claude Code", codex: "Codex", antigravity: "Antigravity" };
  const dayLabel = (d) => { const x = new Date(d + "T00:00:00"); return isNaN(x) ? d : x.toLocaleDateString([], { month: "short", day: "numeric" }); };

  // ── tooltip ─────────────────────────────────────────────────────────
  let tipEl;
  function tip(e, rows, title) {
    if (!tipEl) { tipEl = h("div", { class: "obs-tip", role: "tooltip" }); document.body.appendChild(tipEl); }
    mount(tipEl, title ? h("div", null, h("b", null, title)) : null,
      rows.filter(r => r && r[1] != null && r[1] !== "").map(([k, v]) => h("div", { class: "r" }, h("span", { class: "muted" }, k), h("span", null, String(v)))));
    tipEl.classList.add("on");
    const pad = 14, w = tipEl.offsetWidth, ht = tipEl.offsetHeight;
    let x = e.clientX + pad, y = e.clientY + pad;
    if (x + w > innerWidth - 8) x = e.clientX - w - pad;
    if (y + ht > innerHeight - 8) y = e.clientY - ht - pad;
    tipEl.style.left = Math.max(8, x) + "px"; tipEl.style.top = Math.max(8, y) + "px";
  }
  function untip() { if (tipEl) tipEl.classList.remove("on"); }
  function hoverable(el, rowsFn, titleFn) {
    el.addEventListener("mousemove", (e) => tip(e, rowsFn(), titleFn ? titleFn() : null));
    el.addEventListener("mouseleave", untip);
    return el;
  }

  // ── chart primitives ────────────────────────────────────────────────
  function niceMax(v) { if (!(v > 0)) return 1; const p = Math.pow(10, Math.floor(Math.log10(v))); const n = v / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }

  // Vertical bars (one series, or stacked segments). data: [{label, parts:[v…], tip:[[k,v]…], title}]
  function barChart(host, { data, series, fmt, height = 150, ariaLabel }) {
    const W = Math.max(280, Math.floor(host.clientWidth || 560)), H = height, L = 44, R = 6, T = 8, B = 20;
    const totals = data.map(d => d.parts.reduce((a, b) => a + (b || 0), 0));
    const max = niceMax(Math.max(0, ...totals));
    const n = Math.max(1, data.length), slot = (W - L - R) / n, bw = Math.max(2, Math.min(28, slot - 2));
    const y = (v) => T + (H - T - B) * (1 - v / max);
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": ariaLabel || "" });
    for (let i = 0; i <= 4; i++) {
      const v = max * i / 4, yy = y(v);
      svg.append(s("line", { class: "gridline", x1: L, x2: W - R, y1: yy, y2: yy }),
        s("text", { class: "axis", x: L - 6, y: yy + 3.5, "text-anchor": "end" }, fmt(v)));
    }
    const every = Math.ceil(n / Math.max(1, Math.floor((W - L) / 64)));
    data.forEach((d, i) => {
      const x = L + i * slot + (slot - bw) / 2;
      let acc = 0;
      const g = s("g");
      d.parts.forEach((v, j) => {
        if (!v) return;
        const y0 = y(acc), y1 = y(acc + v);
        acc += v;
        const top = acc === totals[i];
        const hgt = Math.max(1, y0 - y1 - (j > 0 ? 0 : 0));
        // 4px rounded data-end on the top segment only; 2px surface gap between stacked segments.
        g.append(s("rect", { class: "mark", x, y: y1 + (j > 0 ? 0 : 0), width: bw, height: Math.max(1, hgt - (acc !== v ? 2 : 0)),
          rx: top ? Math.min(4, bw / 2) : 0, fill: `var(${series[j].color})` }));
        if (top && hgt > 4) g.append(s("rect", { x, y: y1 + 4, width: bw, height: Math.max(0, hgt - 4 - (acc !== v ? 2 : 0)), fill: `var(${series[j].color})` }));
      });
      const hit = s("rect", { class: "hit", x: L + i * slot, y: T, width: slot, height: H - T - B });
      hoverable(hit, () => d.tip, () => d.title);
      svg.append(g, hit);
      if (i % every === 0) svg.append(s("text", { class: "axis", x: x + bw / 2, y: H - 5, "text-anchor": "middle" }, d.label));
    });
    svg.append(s("line", { x1: L, x2: W - R, y1: y(0), y2: y(0), stroke: "var(--border-strong)", "stroke-width": 1 }));
    return svg;
  }

  function legend(series) {
    return h("div", { class: "obs-legend" }, series.map(se => h("span", null, h("i", { style: { background: `var(${se.color})` } }), se.label)));
  }
  function tableView(cols, rows) {
    return h("div", { class: "obs-scroll" }, h("table", { class: "obs-table" },
      h("thead", null, h("tr", null, cols.map(c => h("th", { class: c.num ? "num" : null }, c.label)))),
      h("tbody", null, rows.map(r => h("tr", null, cols.map(c => h("td", { class: (c.num ? "num" : "") + (c.lbl ? " lbl" : "") || null, title: c.lbl ? String(c.get(r) ?? "") : null }, c.get(r))))))));
  }
  // A panel whose body can flip between the chart and its table.
  function panel(title, hint, renderChart, renderTable) {
    const body = h("div", { class: "obs-chart" });
    let asTable = false;
    const toggle = btn("Table", { kind: "quiet", size: "sm", icon: "layers", title: "Show the numbers as a table" });
    const draw = () => { mount(body, asTable ? renderTable() : renderChart(body)); toggle.classList.toggle("on", asTable); };
    toggle.onclick = () => { asTable = !asTable; draw(); };
    const el = h("section", { class: "obs-panel" }, h("header", null, h("h4", null, title), hint ? h("span", { class: "hint" }, hint) : null, h("span", { class: "grow" }), renderTable ? toggle : null), body);
    requestAnimationFrame(draw);
    el._redraw = draw;
    return el;
  }
  function kpi(ic, k, v, sub, title) {
    return h("div", { class: "obs-kpi", title: title || null }, h("div", { class: "k" }, icon(ic), k), h("div", { class: "v" }, v), sub ? h("div", { class: "s" }, sub) : null);
  }
  function seg(options, value, onPick, aria) {
    const el = h("div", { class: "seg sm", role: "tablist", "aria-label": aria });
    const draw = () => mount(el, options.map(([v, label]) => h("button", { type: "button", class: v === value ? "on" : null, role: "tab", "aria-selected": String(v === value),
      onclick: () => { value = v; draw(); onPick(v); } }, label)));
    draw();
    return el;
  }

  // ── pass^k ──────────────────────────────────────────────────────────
  function passkRender(d) {
    if (!d || !d.fires) return h("span", { class: "faint" }, "—");
    const fires = d.fires.slice().reverse();
    const title = `last ${d.fires.length} fires · ${d.passes}/${d.decided} passed` + (d.p_hat_k != null ? ` · p̂^${d.k} = ${d.p_hat_k}` : "") + (d.pass_k != null ? ` · pass^${d.k} = ${d.pass_k}` : "");
    return h("span", { class: "obs-passk", title, "aria-label": title },
      fires.map(f => h("b", { class: f.outcome, title: `#${f.seq} ${f.outcome} (${f.basis}) · ${f.status}` }, f.outcome === "pass" ? "✓" : f.outcome === "fail" ? "✗" : "?")),
      d.pass_k != null ? h("span", { class: "faint mono", style: { marginLeft: "4px", fontSize: "11px" } }, `pass^${d.k}=${d.pass_k}`) : null);
  }
  function passkStrip(triggerId, k = 5) {
    const el = h("span", { class: "faint" }, "…");
    api(`/api/telemetry/triggers/${encodeURIComponent(triggerId)}/passk?k=${k}`).then(d => el.replaceWith(passkRender(d))).catch(() => { el.textContent = "—"; });
    return el;
  }

  // ── dashboard ───────────────────────────────────────────────────────
  const RANGES = [["24h", "24h"], ["7d", "7d"], ["14d", "14d"], ["30d", "30d"]];
  const GROUPS = [["agent", "Agent"], ["job", "Job"], ["trigger", "Trigger"], ["engine", "Engine"], ["model", "Model"]];
  const TOK_SERIES = [{ key: "cache", label: "Cache reads", color: "--viz-3" }, { key: "input", label: "Other input", color: "--viz-1" }, { key: "output", label: "Output", color: "--viz-2" }];

  function mountDashboard(host, o = {}) {
    const st = { range: o.range || "14d", group: o.group || "agent", data: null };
    const root = h("div", { class: "obs" });
    const bar = h("div", { class: "obs-bar" });
    const body = h("div", { class: "obs" }, skeleton(4, "sk-row"));
    mount(host, root);
    root.append(bar, body);
    const load = async () => {
      try { st.data = await api(`/api/telemetry/summary?group=${st.group}&since=${st.range}`); render(); }
      catch (e) { mount(body, errorBox(e.message, load)); }
    };
    mount(bar, seg(RANGES, st.range, (v) => { st.range = v; load(); }, "Time range"),
      seg(GROUPS, st.group, (v) => { st.group = v; load(); }, "Group by"),
      h("span", { class: "grow" }),
      btn(null, { icon: "refresh", kind: "quiet", size: "sm", title: "Refresh", onClick: load }));
    function render() {
      const d = st.data, t = d.totals, r = d.runs || {};
      if (!t.attempts && !(d.otlp || {}).log_events) {
        mount(body, h("div", { class: "obs-empty" }, "No engine runs in this window yet. Cost, tokens and tool use appear here as soon as a task, a run step or a trigger fire finishes."));
        return;
      }
      const kpis = h("div", { class: "obs-kpis" },
        kpi("coins", "Cost", costOf(t.cost_usd, t.cost_complete), t.cost_complete ? `${t.attempts} engine runs` : "Codex / Antigravity report no cost", "Sum of Claude's reported cost over the window"),
        kpi("hash", "Tokens", fmtTokens(t.input_tokens + t.output_tokens), `${fmtTokens(t.input_tokens)} in · ${fmtTokens(t.output_tokens)} out`),
        kpi("history", "Cache-read ratio", pct(t.cache_read_ratio), `${fmtTokens(t.cache_read_tokens)} read from cache`, "cache_read_tokens / input_tokens (input includes cache reads and writes)"),
        kpi("alert", "Engine failures", pct(t.failure_rate), `${t.errors} of ${t.attempts - t.running} finished`),
        kpi("checkCircle", "Runs: process ok", pct(r.process_ok_rate), `${r.process_ok || 0} of ${(r.runs || 0) - (r.active || 0)} completed`, "Every step of the run completed"),
        kpi("check", "Runs: task succeeded", pct(r.success_rate), `${r.pass || 0} pass · ${r.fail || 0} fail · ${r.unknown || 0} unknown`, "Verdict: the job's outcome check, else the final handoff verdict"));
      const days = d.by_day || [];
      const costPanel = panel("Cost by day", d.otlp && d.otlp.cost_by_model.length ? "Claude-reported" : null,
        (el) => barChart(el, { ariaLabel: "Cost by day", fmt: money, series: [{ color: "--viz-1" }],
          data: days.map(x => ({ label: dayLabel(x.day), parts: [x.cost_usd], title: dayLabel(x.day),
            tip: [["Cost", money(x.cost_usd) + (x.cost_complete ? "" : " (incomplete)")], ["Engine runs", x.attempts], ["Failed", x.errors]] })) }),
        () => tableView([{ label: "Day", get: x => x.day }, { label: "Cost", num: 1, get: x => money(x.cost_usd) }, { label: "Runs", num: 1, get: x => x.attempts }, { label: "Failed", num: 1, get: x => x.errors }], days));
      const tokPanel = panel("Tokens by day", null,
        (el) => h("div", null, barChart(el, { ariaLabel: "Tokens by day, stacked: cache reads, other input, output", fmt: fmtTokens, series: TOK_SERIES,
          data: days.map(x => ({ label: dayLabel(x.day), title: dayLabel(x.day),
            parts: [x.cache_read_tokens, Math.max(0, x.input_tokens - x.cache_read_tokens), x.output_tokens],
            tip: [["Cache reads", fmtTokens(x.cache_read_tokens)], ["Other input", fmtTokens(Math.max(0, x.input_tokens - x.cache_read_tokens))], ["Output", fmtTokens(x.output_tokens)], ["Cache-read ratio", pct(x.cache_read_ratio)]] })) }), legend(TOK_SERIES)),
        () => tableView([{ label: "Day", get: x => x.day }, { label: "Input", num: 1, get: x => fmtNum(x.input_tokens) }, { label: "Cache reads", num: 1, get: x => fmtNum(x.cache_read_tokens) }, { label: "Output", num: 1, get: x => fmtNum(x.output_tokens) }, { label: "Cache ratio", num: 1, get: x => pct(x.cache_read_ratio) }], days));
      const gmax = Math.max(0, ...d.groups.map(g => g.cost_usd || 0));
      const gname = (GROUPS.find(g => g[0] === d.group) || [])[1] || d.group;
      const cols = [{ label: gname, lbl: 1, get: g => d.group === "engine" ? (ENGINE[g.key] || g.label) : g.label },
        { label: "Cost", get: g => h("div", { class: "obs-inbar" }, h("span", { class: "mono" }, costOf(g.cost_usd, g.cost_complete)),
          h("b", { "aria-hidden": "true" }, h("i", { style: { width: (gmax ? Math.round(100 * (g.cost_usd || 0) / gmax) : 0) + "%" } }))) },
        { label: "Runs", num: 1, get: g => g.attempts }, { label: "Fail rate", num: 1, get: g => pct(g.failure_rate) },
        { label: "Tokens", num: 1, get: g => fmtTokens(g.input_tokens + g.output_tokens) }, { label: "Cache", num: 1, get: g => pct(g.cache_read_ratio) }];
      if (d.group === "job" || d.group === "trigger") cols.push({ label: "Ok / pass", num: 1, get: g => g.runs ? `${pct(g.runs.process_ok_rate)} / ${pct(g.runs.success_rate)}` : "—" });
      if (d.group === "trigger") cols.push({ label: "Last 5", get: g => g.key ? passkRender(g.pass_k) : "" });
      const groupPanel = h("section", { class: "obs-panel" }, h("header", null, h("h4", null, `By ${gname.toLowerCase()}`), h("span", { class: "hint" }, "cost, engine runs, failure rate, tokens, cache-read ratio")),
        d.groups.length ? tableView(cols, d.groups) : h("div", { class: "obs-empty" }, "Nothing in this window."));
      const tmax = Math.max(1, ...d.top_tools.map(x => x.count));
      const toolPanel = h("section", { class: "obs-panel" }, h("header", null, h("h4", null, "Top tools"), h("span", { class: "hint" }, "calls · red = errored")),
        d.top_tools.length ? h("div", { class: "obs-hbar" }, d.top_tools.map(x => [
          h("span", { class: "nm", title: x.name }, x.name),
          hoverable(h("div", { class: "tr" }, h("i", { style: { width: (100 * x.count / tmax) + "%" } }), x.errors ? h("i", { class: "err", style: { width: (100 * x.errors / tmax) + "%" } }) : null),
            () => [["Calls", x.count], ["Errored", x.errors], ["Error rate", pct(x.error_rate)]], () => x.name),
          h("span", { class: "val" }, String(x.count))])) : h("div", { class: "obs-empty" }, "No tool calls."));
      const outc = h("section", { class: "obs-panel" }, h("header", null, h("h4", null, "Run outcomes"), h("span", { class: "hint" }, "process ok ≠ task succeeded")),
        h("div", { class: "obs-outcomes" },
          kpi("checkCircle", "Completed", `${r.process_ok || 0}`, `of ${(r.runs || 0) - (r.active || 0)} finished runs`),
          kpi("check", "Verdict pass", `${r.pass || 0}`, `${r.fail || 0} fail · ${r.unknown || 0} unknown`),
          kpi("alert", "Completed but failed", `${r.ok_but_fail || 0}`, "the process finished, the check said no", "Runs whose every step completed but whose verdict is fail"),
          kpi("sparkle", "Failed but passed", `${r.failed_but_pass || 0}`, "a step failed, the outcome check passed")));
      const otlp = d.otlp || {};
      const otlpPanel = otlp.cost_by_model && otlp.cost_by_model.length ? h("section", { class: "obs-panel" },
        h("header", null, h("h4", null, "Per model (from the CLIs' OTLP export)"), h("span", { class: "hint" }, `${fmtNum(otlp.log_events)} events · ${fmtNum(otlp.metric_points)} metric points`)),
        tableView([{ label: "Model", lbl: 1, get: x => x.model || "?" }, { label: "Requests", num: 1, get: x => x.requests }, { label: "Cost", num: 1, get: x => money(x.cost_usd) },
          { label: "Input", num: 1, get: x => fmtTokens(x.input_tokens) }, { label: "Cache reads", num: 1, get: x => fmtTokens(x.cache_read_tokens) }, { label: "Output", num: 1, get: x => fmtTokens(x.output_tokens) }], otlp.cost_by_model)) : null;
      mount(body, kpis, h("div", { class: "obs-grid" }, costPanel, tokPanel), groupPanel, h("div", { class: "obs-grid" }, toolPanel, outc), otlpPanel);
      if (!host._obsRO && window.ResizeObserver) {
        let w = host.clientWidth;
        host._obsRO = new ResizeObserver(() => { if (Math.abs(host.clientWidth - w) > 40) { w = host.clientWidth; costPanel._redraw && render(); } });
        host._obsRO.observe(host);
      }
    }
    load();
    return { reload: load };
  }

  // ── verdict pills ───────────────────────────────────────────────────
  function verdictPills(run) {
    if (!run || !run.verdict) return null;
    const tone = { pass: "ok", fail: "err" }[run.verdict] || "";
    const src = { outcome_check: "outcome check", handoff: "final handoff", process: "run status" }[run.verdict_source] || run.verdict_source || "";
    return h("span", { class: "row", style: { gap: "6px" } },
      h("span", { class: "pill " + (run.process_ok ? "ok" : "warn"), title: "Did the process finish? (every step completed)" }, icon(run.process_ok ? "check" : "alert"), run.process_ok ? "process ok" : "process " + String(run.status || "").replace(/_/g, " ")),
      h("span", { class: "pill " + tone, title: `Did the task succeed? — from the ${src}` + ((run.verdict_detail || {}).reason ? `: ${run.verdict_detail.reason}` : "") },
        icon(run.verdict === "pass" ? "checkCircle" : run.verdict === "fail" ? "x" : "info"), "verdict " + run.verdict, src ? h("span", { class: "faint" }, " · " + src) : null));
  }

  // ── run timeline ────────────────────────────────────────────────────
  const SEG_TONE = { completed: "ok", failed: "err", cancelled: "err", rejected: "err", budget_exceeded: "warn", interrupted: "warn", running: "live", pending: "", awaiting_input: "live", skipped: "" };
  function mountRunTimeline(host, run) {
    if (!host || !run) return;
    const sig = JSON.stringify([run.run_id, run.status, run.verdict, (run.steps || []).map(x => [x.status, (x.attempts || []).length, (x.workers || []).map(w => w.status)])]);
    const live = !TERMINAL_RUN.includes(run.status);
    if (host._obsSig === sig && (!live || Date.now() - (host._obsAt || 0) < 5000)) return;
    host._obsSig = sig; host._obsAt = Date.now();
    api(`/api/telemetry/runs/${encodeURIComponent(run.run_id)}/timeline`).then(r => { if (host._obsSig === sig) mount(host, renderTimeline(r.timeline)); })
      .catch(e => mount(host, h("div", { class: "field-hint" }, "Timeline unavailable: " + e.message)));
  }
  function renderTimeline(t) {
    const t0 = t.start_ms, t1 = Math.max(t.end_ms || t.now_ms, t0 + 1000), span = t1 - t0;
    const pos = (a, b) => { const l = Math.max(0, Math.min(100, 100 * (a - t0) / span)); const r = Math.max(l, Math.min(100, 100 * ((b || t.now_ms) - t0) / span)); return { left: l + "%", width: Math.max(0.6, r - l) + "%" }; };
    const verdict = h("div", { class: "obs-verdict" }, verdictPills(t),
      t.usage && t.usage.cost_usd != null ? h("span", { class: "pill", title: "Stream-reported cost" + (t.otlp_cost_usd != null ? ` · OTLP-reported ${money(t.otlp_cost_usd)}` : "") }, icon("coins"), money(t.usage.cost_usd) + (t.usage.cost_complete === false ? "+" : "")) : null,
      t.otlp_cost_usd != null ? h("span", { class: "faint", style: { fontSize: "11.5px" }, title: "Cost the CLIs exported over OpenTelemetry (cross-check)" }, `OTLP ${money(t.otlp_cost_usd)}`) : null,
      t.outcome_check ? h("span", { class: "pill " + (t.outcome_check.passed ? "ok" : t.outcome_check.passed === false ? "err" : ""), title: t.outcome_check.command }, icon("terminal"), `outcome check: exit ${t.outcome_check.exit_code ?? "?"}`) : null,
      t.outcome_check && t.outcome_check.output ? h("pre", null, "$ " + t.outcome_check.command + "\n" + t.outcome_check.output) : null);
    if (!t0) return h("div", { class: "obs-tl" }, verdict, h("div", { class: "field-hint" }, "Not started yet."));
    const g = h("div", { class: "obs-gantt", role: "table", "aria-label": "Run timeline" });
    const ticks = [0, .25, .5, .75, 1].map(f => h("span", { style: { left: (f * 100) + "%" } }, fmtMs(Math.round(f * span))));
    g.append(h("span"), h("div", { class: "axis" }, ticks), h("span", { class: "cost faint" }, "cost"));
    for (const ph of t.phases) {
      g.append(h("div", { class: "ph" }, `Phase ${ph.phase}` + (ph.steps.length > 1 ? ` · ${ph.steps.length} in parallel` : "")));
      for (const st of ph.steps) {
        const track = h("div", { class: "track" });
        const subs = st.workers && st.workers.length ? st.workers.map(w => ({ ...w, label: `worker #${w.n}`, sub: true })) : (st.attempts || []).map(a => ({ ...a, label: `attempt ${a.n}${a.mode && a.mode !== "run" ? " · " + a.mode : ""}` }));
        const bars = subs.length ? subs : (st.start_ms ? [{ ...st, label: st.kind, status: st.status }] : []);
        for (const b of bars) {
          if (!b.start_ms) continue;
          const el = h("div", { class: `seg ${SEG_TONE[b.status] || ""}${b.sub ? " sub" : ""}`, style: pos(b.start_ms, b.end_ms), tabindex: "0",
            "aria-label": `${st.name} ${b.label}: ${b.status}, ${fmtMs((b.end_ms || t.now_ms) - b.start_ms)}` });
          hoverable(el, () => [["Status", String(b.status || "").replace(/_/g, " ")], ["Duration", fmtMs((b.end_ms || t.now_ms) - b.start_ms)],
            ["Cost", b.cost_usd != null ? money(b.cost_usd) : null], ["Tokens", b.input_tokens != null ? `${fmtTokens(b.input_tokens)} in · ${fmtTokens(b.output_tokens)} out` : null],
            ["Model", b.model || st.model], ["Tool calls", b.tools], ["Verdict", b.verdict], ["Error", b.error ? String(b.error).slice(0, 120) : null], ["Item", b.item ? String(b.item).slice(0, 80) : null]],
            () => `${st.name} · ${b.label}`);
          track.append(el);
        }
        g.append(h("div", { class: "nm", role: "rowheader" }, statusPill(st.status), h("span", { class: "t", title: st.name }, st.name), st.kind !== "agent" ? h("span", { class: "pill" }, st.kind) : null,
            st.verdict && st.kind !== "gate" ? h("span", { class: "pill " + ({ pass: "ok", fail: "err" }[st.verdict] || ""), title: "handoff verdict" }, st.verdict) : null),
          track, h("span", { class: "cost", title: st.otlp_cost_usd != null ? `OTLP-reported ${money(st.otlp_cost_usd)}` : null }, st.cost_usd != null ? money(st.cost_usd) : st.kind === "gate" ? "" : "—"));
      }
    }
    g.append(h("div", { class: "kk" }, [["--viz-ok", "completed"], ["--viz-live", "running / waiting"], ["--viz-err", "failed / cancelled"], ["--viz-warn", "budget / interrupted"], ["--viz-idle", "other"]].map(([c, l]) => h("span", null, h("i", { style: { background: `var(${c})` } }), l))));
    return h("div", { class: "obs-tl" }, verdict, g);
  }

  // ── cross-engine continue ───────────────────────────────────────────
  function continueButton(o) {
    return btn(o.label || "Continue on…", { icon: "swap", kind: o.kind || "ghost", size: "sm", title: "Carry this workspace's work over to another engine: latest handoff + workspace diff + PROGRESS.md → a fresh conversation there", onClick: () => openContinue(o) });
  }
  function openContinue(o) {
    const engines = Object.keys(ENGINE).filter(e => e !== o.engine);
    const sel = h("select", { class: "select" }, engines.map(e => h("option", { value: e }, ENGINE[e])));
    const model = h("input", { class: "input", placeholder: "blank = the CLI's default", spellcheck: "false" });
    const ask = h("textarea", { class: "input", rows: 3, placeholder: "Optional — what to do next (default: continue from where it stopped and finish)" });
    modal({ title: "Continue on another engine", width: "520px",
      subtitle: `${o.engine ? "From " + (ENGINE[o.engine] || o.engine) + " · " : ""}workspace ${String(o.sessionId).slice(0, 12)}`,
      body: h("div", { class: "col", style: { gap: "10px" } },
        h("div", { class: "info-box" }, icon("info"), h("span", null, "A CLI conversation can't move between engines, so a context package travels instead: the latest handoff", o.stepId ? " of this step" : "", ", the workspace diff since its first snapshot, and PROGRESS.md. The new engine starts a fresh conversation in the same workspace; the lineage records the switch.")),
        h("label", { class: "field" }, h("span", { class: "field-label" }, "Engine"), sel),
        h("label", { class: "field" }, h("span", { class: "field-label" }, "Model"), model),
        h("label", { class: "field" }, h("span", { class: "field-label" }, "Instructions"), ask)),
      actions: [{ label: "Cancel", kind: "ghost" }, { label: "Continue", kind: "primary", icon: "send", onClick: async () => {
        const body = { engine: sel.value };
        if (model.value.trim()) body.model = model.value.trim();
        if (ask.value.trim()) body.prompt = ask.value.trim();
        if (o.namespace) body.namespace = o.namespace;
        if (o.runId) body.run_id = o.runId;
        if (o.stepId) body.step_id = o.stepId;
        const r = await api(`/api/sessions/${encodeURIComponent(o.sessionId)}/continue`, jsonOpts("POST", body));
        toast(`Continuing on ${ENGINE[r.to_engine] || r.to_engine} — task ${String(r.task_id).slice(0, 8)} (${r.files_changed} changed file${r.files_changed === 1 ? "" : "s"} in the package)`, { kind: "success" });
        if (o.onStarted) o.onStarted(r);
      } }] });
  }

  window.TCObserve = { mountDashboard, mountRunTimeline, renderTimeline, continueButton, verdictPills, passkStrip, passkRender };
})();
