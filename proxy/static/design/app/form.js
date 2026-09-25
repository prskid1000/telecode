// TeleDesign — <question-form> renderer (prompts/discovery.md §4).
//
// Forms stream: parseStreamingForm() pulls every *complete* question object out
// of a partially-written block so questions appear as the agent writes them.
// Agent-authored SVG is shown through <img src="data:image/svg+xml,…">, which
// never runs scripts or loads external resources.
import { h, icon, btn, mount, toast } from "./core.js";

const OPEN = "<question-form>", CLOSE = "</question-form>";

// Split assistant text into {before, formSrc, after, complete}.
export function splitForm(text) {
  const s = String(text || "");
  const i = s.indexOf(OPEN);
  if (i < 0) return { before: s, formSrc: null, after: "", complete: false };
  const j = s.indexOf(CLOSE, i);
  if (j < 0) return { before: s.slice(0, i), formSrc: s.slice(i + OPEN.length), after: "", complete: false };
  return { before: s.slice(0, i), formSrc: s.slice(i + OPEN.length, j), after: s.slice(j + CLOSE.length), complete: true };
}

export function parseStreamingForm(src) {
  if (!src) return null;
  try { const f = JSON.parse(src.trim().replace(/^```(json)?|```$/g, "")); if (f && Array.isArray(f.questions)) return f; } catch { /* partial */ }
  const form = { id: (src.match(/"id"\s*:\s*"([^"]*)"/) || [])[1] || "form", title: (src.match(/"title"\s*:\s*"((?:[^"\\]|\\.)*)"/) || [])[1] || "", questions: [] };
  try { form.title = JSON.parse(`"${form.title}"`); } catch {}
  const qi = src.indexOf('"questions"');
  if (qi < 0) return form;
  let k = src.indexOf("[", qi);
  if (k < 0) return form;
  k++;
  // Walk the array, collecting balanced top-level objects.
  let depth = 0, start = -1, inStr = false, esc = false;
  for (; k < src.length; k++) {
    const c = src[k];
    if (inStr) { if (esc) esc = false; else if (c === "\\") esc = true; else if (c === '"') inStr = false; continue; }
    if (c === '"') { inStr = true; continue; }
    if (c === "{") { if (depth === 0) start = k; depth++; }
    else if (c === "}") { depth--; if (depth === 0 && start >= 0) { try { form.questions.push(JSON.parse(src.slice(start, k + 1))); } catch {} start = -1; } }
    else if (c === "]" && depth === 0) break;
  }
  return form;
}

const META = [
  { value: "explore", label: "Explore a few options", description: "Show me 3+ variations" },
  { value: "decide", label: "Decide for me", description: "Pick the strongest and say why" },
];
const normOpt = (o) => (typeof o === "string" ? { value: o, label: o } : { value: String(o.value ?? o.label), label: String(o.label ?? o.value), description: o.description ? String(o.description) : null, svg: o.svg });

export function defaultAnswers(form) {
  const a = {};
  for (const q of form.questions || []) {
    if (q.default !== undefined) a[q.id] = q.default;
    else if (q.kind === "slider") a[q.id] = q.min ?? 0;
    else if (q.kind === "color") a[q.id] = (q.swatches && q.swatches[0]) || "#3b82f6";
  }
  return a;
}

function svgSrc(svg) {
  if (typeof svg !== "string" || svg.length > 20000 || !/^\s*<svg[\s>]/i.test(svg)) return null;
  // <img> rendering already blocks script and external loads; strip them anyway.
  const clean = svg.replace(/<script[\s\S]*?<\/script>/gi, "").replace(/\son\w+\s*=\s*("[^"]*"|'[^']*')/gi, "").replace(/(href|src)\s*=\s*("(?!#)[^"]*"|'(?!#)[^']*')/gi, "");
  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(clean);
}

// renderForm(form, {streaming, answered:{…}|null, onSubmit(answers, summary), onSkip, upload(files)->paths})
export function renderForm(form, opts = {}) {
  // opts.state (an object owned by the caller) keeps answers across re-renders
  // while the form is still streaming in.
  let answers;
  if (opts.answered) answers = { ...opts.answered };
  else {
    answers = opts.state || {};
    const d = defaultAnswers(form);
    for (const k of Object.keys(d)) if (!(k in answers)) answers[k] = d[k];
  }
  const other = {};
  const root = h("div", { class: "qform" + (opts.streaming ? " streaming" : "") + (opts.answered ? " answered" : "") });
  const head = h("div", { class: "qform-head" }, h("h4", null, form.title || "A few questions"),
    opts.answered ? h("span", { class: "pill ok" }, icon("check"), "Answered") : null);
  const body = h("div", { class: "qform-body" });
  root.append(head, body);

  if (opts.answered) {
    for (const q of form.questions || []) {
      const v = opts.answered[q.id];
      if (v === undefined || v === "" || (Array.isArray(v) && !v.length)) continue;
      body.appendChild(h("div", { class: "q" }, h("div", { class: "answer" }, String(q.title || q.id).replace(/[\s?:.]+$/, ""), ": ", h("b", null, describe(q, v)))));
    }
    return root;
  }

  const qEls = {};
  for (const q of form.questions || []) {
    if (!q || !q.id) continue;
    const box = h("div", { class: "q", dataset: { q: q.id } });
    qEls[q.id] = box;
    box.append(h("div", { class: "q-title" }, q.title || q.id, q.required ? h("span", { class: "req", title: "Required" }, "*") : null));
    if (q.subtitle) box.append(h("div", { class: "q-sub" }, q.subtitle));
    box.append(control(q));
    body.appendChild(box);
  }
  if (opts.streaming && !(form.questions || []).length) body.appendChild(h("div", { class: "q muted" }, "Preparing questions…"));

  function control(q) {
    const set = (v) => { answers[q.id] = v; box().classList.remove("invalid"); };
    const box = () => qEls[q.id];
    switch (q.kind) {
      case "options": case "svg-options": {
        const multi = !!q.multi, max = +q.max || Infinity;
        let opts2 = (q.options || []).map(normOpt);
        const isSvg = q.kind === "svg-options";
        if (q.kind === "options") for (const m of META) if (!opts2.some((o) => o.value === m.value)) opts2.push({ ...m, meta: true });
        const wrap = h("div", { class: isSvg ? "svg-opts" : "opts" });
        const otherInput = h("input", { class: "input hidden", placeholder: "Describe what you want", style: { marginTop: "6px", height: "28px" } });
        otherInput.addEventListener("input", () => { other[q.id] = otherInput.value; });
        const cur = () => { const v = answers[q.id]; return multi ? (Array.isArray(v) ? v : v != null ? [v] : []) : v; };
        const draw = () => {
          const v = cur();
          const on = (val) => multi ? v.includes(val) : v === val;
          mount(wrap, opts2.map((o) => {
            const click = () => {
              if (multi) {
                let arr = cur().slice();
                if (arr.includes(o.value)) arr = arr.filter((x) => x !== o.value);
                else { if (arr.length >= max) { toast(`Pick up to ${max}`); return; } arr.push(o.value); }
                set(arr);
              } else set(o.value);
              draw();
            };
            if (isSvg) {
              const src = svgSrc(o.svg);
              return h("button", { type: "button", class: "svg-opt" + (on(o.value) ? " on" : ""), onclick: click, title: o.description || o.label },
                src ? h("img", { src, alt: "" }) : h("div", { style: { aspectRatio: "80/56", background: "var(--bg)", borderRadius: "5px" } }), h("span", null, o.label));
            }
            return h("button", { type: "button", class: "opt" + (o.meta ? " meta" : "") + (on(o.value) ? " on" : ""), onclick: click, "aria-pressed": on(o.value) ? "true" : "false" },
              o.label, o.description ? h("small", null, o.description) : null);
          }), h("button", { type: "button", class: (isSvg ? "svg-opt" : "opt meta") + (on("__other") ? " on" : ""), onclick: () => {
            if (multi) { const arr = cur().filter((x) => x !== "__other"); if (!cur().includes("__other")) arr.push("__other"); set(arr); } else set("__other");
            draw(); otherInput.classList.toggle("hidden", !(multi ? cur().includes("__other") : cur() === "__other")); if (!otherInput.classList.contains("hidden")) otherInput.focus();
          } }, isSvg ? h("div", { style: { aspectRatio: "80/56", display: "grid", placeItems: "center", color: "var(--faint)" } }, icon("edit")) : null, "Other…"));
        };
        draw();
        return h("div", null, wrap, otherInput);
      }
      case "slider": {
        const min = +q.min || 0, max = q.max != null ? +q.max : 100, step = +q.step || 1;
        if (answers[q.id] == null) answers[q.id] = min;
        const out = h("output", null, `${answers[q.id]}${q.unit ? " " + q.unit : ""}`);
        const r = h("input", { type: "range", min, max, step, value: answers[q.id], "aria-label": q.title });
        r.addEventListener("input", () => { set(+r.value); out.textContent = `${r.value}${q.unit ? " " + q.unit : ""}`; });
        return h("div", { class: "slider-row" }, h("span", { class: "faint mono" }, min), r, h("span", { class: "faint mono" }, max), out);
      }
      case "color": {
        const valid = (c) => /^#[0-9a-f]{3,8}$/i.test(c);
        const pick = h("input", { type: "color", value: valid(answers[q.id]) && answers[q.id].length === 7 ? answers[q.id] : "#3b82f6", "aria-label": q.title });
        const txt = h("input", { class: "input", value: answers[q.id] || "" });
        const sws = h("div", { class: "row", style: { gap: "5px" } });
        const drawSw = () => mount(sws, (q.swatches || []).filter(valid).map((c) => h("button", { type: "button", class: "sw-btn" + (answers[q.id] === c ? " on" : ""), style: { background: c }, title: c, onclick: () => { set(c); pick.value = c.length === 7 ? c : pick.value; txt.value = c; drawSw(); } })));
        drawSw();
        pick.addEventListener("input", () => { set(pick.value); txt.value = pick.value; drawSw(); });
        txt.addEventListener("change", () => { if (valid(txt.value)) { set(txt.value); if (txt.value.length === 7) pick.value = txt.value; drawSw(); } else txt.value = answers[q.id] || ""; });
        return h("div", { class: "color-row" }, pick, txt, sws);
      }
      case "file": {
        const list = h("div", { class: "chips" });
        const inp = h("input", { type: "file", class: "hidden", accept: q.accept || null, multiple: q.multiple ? true : null });
        const drawL = () => { const v = answers[q.id]; const arr = Array.isArray(v) ? v : v ? [v] : []; mount(list, arr.map((p) => h("span", { class: "chip" }, icon("file"), h("span", { class: "nm" }, p.split("/").pop())))); };
        inp.addEventListener("change", async () => {
          if (!opts.upload) return;
          const paths = await opts.upload([...inp.files]);
          if (!paths.length) return;
          const prev = answers[q.id];
          set(q.multiple ? [...(Array.isArray(prev) ? prev : prev ? [prev] : []), ...paths] : paths[0]);
          drawL();
        });
        drawL();
        return h("div", { class: "row wrap" }, btn("Choose files", { icon: "upload", cls: "sm", onClick: () => inp.click() }), inp,
          q.accept ? h("span", { class: "faint", style: { fontSize: "11px" } }, q.accept) : null, list);
      }
      case "freeform": {
        const ta = h("textarea", { class: "input", rows: 3, placeholder: q.placeholder || "" }, answers[q.id] || "");
        ta.addEventListener("input", () => set(ta.value));
        return ta;
      }
      default: {
        const inp = h("input", { class: "input", placeholder: q.placeholder || "", value: answers[q.id] || "" });
        inp.addEventListener("input", () => set(inp.value));
        return inp;
      }
    }
  }

  if (!opts.streaming) {
    const submit = btn("Send answers", { kind: "primary", icon: "send", onClick: () => {
      const out = {};
      let bad = null;
      for (const q of form.questions || []) {
        let v = answers[q.id];
        if (Array.isArray(v)) v = v.map((x) => (x === "__other" ? (other[q.id] || "").trim() : x)).filter((x) => x !== "");
        else if (v === "__other") v = (other[q.id] || "").trim();
        const empty = v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length);
        if (q.required && empty) { bad = bad || q.id; qEls[q.id]?.classList.add("invalid"); continue; }
        if (!empty) out[q.id] = v;
      }
      if (bad) { qEls[bad]?.scrollIntoView({ block: "center", behavior: "smooth" }); toast("Answer the required questions first", { kind: "error" }); return; }
      opts.onSubmit && opts.onSubmit(out, summarize(form, out));
    } });
    root.appendChild(h("div", { class: "qform-foot" },
      h("span", { class: "faint grow", style: { fontSize: "11.5px" } }, "Defaults are filled in."),
      opts.onSkip ? btn("Skip questions", { kind: "ghost", onClick: () => opts.onSkip() }) : null, submit));
  }
  return root;
}

function describe(q, v) {
  const opts = (q.options || []).map(normOpt).concat(META);
  const lab = (x) => opts.find((o) => o.value === x)?.label || String(x);
  if (Array.isArray(v)) return v.map((x) => (q.kind === "file" ? String(x).split("/").pop() : lab(x))).join(", ");
  if (q.kind === "slider") return `${v}${q.unit ? " " + q.unit : ""}`;
  if (q.kind === "file") return String(v).split("/").pop();
  if (q.kind === "options" || q.kind === "svg-options") return lab(v);
  return String(v).length > 120 ? String(v).slice(0, 117) + "…" : String(v);
}
export function summarize(form, answers) {
  return (form.questions || []).filter((q) => answers[q.id] !== undefined).map((q) => `${String(q.title || q.id).replace(/[\s?:.]+$/, "")}: ${describe(q, answers[q.id])}`).join("\n");
}
