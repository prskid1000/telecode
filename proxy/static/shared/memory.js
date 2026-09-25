// Agent memory panel (P4) — mounted in Team Mode's agent view; needs /shared/manager.js.
//   TCMemory.mount(host, agentId, { onInternalChanged(), openTrigger(id) })
// Tabs: Memory (index + typed topic files, editor) · History (git commits of the
// agent's internal/, per-commit diff, revert) · Skills (per-agent portable skills,
// promote from / copy to global) · Pinned (## Pinned constraints of AGENT.md) ·
// Reflection (status, options, Reflect now). Also TCMemory.approvalBody(ap) for
// the approvals inbox. DOM built with h() only — no untrusted innerHTML.
// REST: /api/agents/{id}/memory[/index|/index/rebuild|/topics/{f}|/history|/diff|
// /revert|/reflection|/reflect], /api/agents/{id}/pinned, /api/agents/{id}/skills…
"use strict";
(function () {
  const TYPES = ["user", "feedback", "project", "reference"];
  const TYPE_TONE = { user: "accent", feedback: "violet", project: "", reference: "ok" };
  const TYPE_HINT = {
    user: "who the agent works for — role, goals, preferences",
    feedback: "guidance about how to work — corrections, lessons (with helpful / harmful counters)",
    project: "context about ongoing work",
    reference: "pointers to external resources",
  };
  const KIND_TONE = { run: "accent", task: "accent", merge: "violet", ui: "", reflection: "ok", revert: "warn", migrate: "", direct: "" };
  const TABS = [["memory", "Memory", "file"], ["history", "History", "history"], ["skills", "Skills", "wrench"],
    ["pinned", "Pinned", "flag"], ["reflection", "Reflection", "sparkle"]];
  const enc = encodeURIComponent;

  function typePill(t) { return h("span", { class: "pill " + (TYPE_TONE[t] ?? ""), title: TYPE_HINT[t] || "" }, t || "project"); }
  function slug(s) { return String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 48) || "note"; }

  function mountPanel(host, agentId, opts = {}) {
    const base = `/api/agents/${enc(agentId)}`;
    const st = { tab: prefs.get("mem:tab", "memory"), sel: "__index", mem: null, dirty: false };
    const tabsEl = h("div", { class: "tabs", role: "tablist", "aria-label": "Memory" });
    const body = h("div", { class: "mem-body" });
    const headNote = h("span", { class: "faint", style: { fontSize: "11.5px" } });
    const root = h("section", { class: "card mem-card", id: "agent-memory" },
      h("div", { class: "card-head" }, h("h3", null, icon("layers"), "Memory & skills",
        h("span", { class: "hint" }, "git-versioned · only the index is staged; topic files via --add-dir")), headNote),
      tabsEl, body);
    mount(host, root);
    const changed = () => { if (opts.onInternalChanged) { try { opts.onInternalChanged(); } catch { /* ignore */ } } };
    const guard = async () => !st.dirty || await confirmDialog("Discard unsaved memory edits?", "The open memory file has unsaved changes.", { confirm: "Discard", danger: true });

    function renderTabs() {
      mount(tabsEl, TABS.map(([k, l, ic]) => h("button", { type: "button", role: "tab", class: "tab" + (st.tab === k ? " on" : ""), "aria-selected": String(st.tab === k),
        "data-mtab": k, onclick: async () => { if (st.tab === k || !(await guard())) return; st.dirty = false; st.tab = k; prefs.set("mem:tab", k); renderTabs(); show(); } },
        icon(ic), l)));
    }
    function show() {
      mount(body, skeleton(4, "sk-row"));
      ({ memory: showMemory, history: showHistory, skills: showSkills, pinned: showPinned, reflection: showReflection }[st.tab] || showMemory)();
    }

    // ── Memory: index + topics ────────────────────────────────────────
    async function showMemory() {
      try { st.mem = await api(`${base}/memory`); } catch (e) { mount(body, h("div", { class: "card-body" }, errorBox(e.message, show))); return; }
      const m = st.mem;
      headNote.textContent = m.head ? `HEAD ${m.head.slice(0, 8)}` : "";
      if (st.sel !== "__index" && !m.topics.some(t => t.file === st.sel)) st.sel = "__index";
      const list = h("div", { class: "files mem-list" },
        h("div", { class: "frow mem-item" + (st.sel === "__index" ? " on" : ""), onclick: () => pick("__index"), title: "memory/MEMORY.md — staged into every run" },
          icon("file"), h("span", { class: "nm" }, "MEMORY.md", h("span", { class: "faint" }, "  index")),
          m.warnings.length ? h("span", { class: "pill warn", title: m.warnings.join("\n") }, icon("alert"), "limit") : h("span", { class: "meta" }, `${m.index.split("\n").filter(Boolean).length} lines`)),
        m.topics.length ? m.topics.map(t => h("div", { class: "frow mem-item" + (st.sel === t.file ? " on" : ""), onclick: () => pick(t.file), title: t.file + (t.description ? "\n" + t.description : "") },
          typePill(t.type), h("span", { class: "nm" }, h("span", { class: "mem-name" }, t.name), h("span", { class: "mem-desc" }, t.description || t.file)),
          t.type === "feedback" ? h("span", { class: "meta mono", title: "helpful / harmful" }, `+${t.helpful} −${t.harmful}`) : null,
          t.in_index ? null : h("span", { class: "pill warn", title: "Not listed in the index — agents only see indexed memories. Rebuild the index or save the topic." }, "unindexed")))
          : h("div", { class: "empty sm" }, h("p", null, "No topic files yet — runs, auto memory and reflection add them.")));
      const tools = h("div", { class: "row mem-tools" },
        btn("New memory", { icon: "plus", kind: "ghost", size: "sm", onClick: () => newTopic() }),
        btn(null, { icon: "refresh", kind: "quiet", size: "sm", title: "Rebuild the index from the topic files' frontmatter", onClick: async () => {
          if (!(await guard())) return;
          try { await api(`${base}/memory/index/rebuild`, jsonOpts("POST", {})); st.dirty = false; toast("Index rebuilt", { kind: "success" }); changed(); showMemory(); }
          catch (e) { toast(e.message, { kind: "error" }); }
        } }),
        h("span", { class: "grow" }),
        h("span", { class: "faint mono", title: m.dir, style: { fontSize: "11px" } }, `${m.topics.length} topic${m.topics.length === 1 ? "" : "s"}`));
      const editor = h("div", { class: "mem-editor" });
      mount(body, h("div", { class: "mem-split" }, h("div", { class: "mem-left" }, tools, list), editor));
      if (st.sel === "__index") renderIndexEditor(editor); else renderTopicEditor(editor, st.sel);
    }
    async function pick(sel) { if (sel === st.sel || !(await guard())) return; st.dirty = false; st.sel = sel; showMemory(); }

    function renderIndexEditor(host) {
      const m = st.mem;
      const ta = h("textarea", { class: "input code mem-ta", spellcheck: "false", id: "mem-index", oninput: () => { st.dirty = true; note.textContent = "Unsaved"; } });
      ta.value = m.index;
      const note = h("span", { class: "faint", style: { fontSize: "11.5px" } });
      mount(host,
        h("div", { class: "mem-ed-head" }, h("b", null, "Memory index"), h("span", { class: "faint" }, `one line per memory · ≤ ${m.limits.lines} lines / ${Math.round(m.limits.bytes / 1024)} KB`), h("span", { class: "grow" }), note,
          btn("Save", { icon: "save", kind: "primary", size: "sm", id: "mem-save", onClick: async () => {
            try { const r = await api(`${base}/memory/index`, jsonOpts("PUT", { content: ta.value })); st.dirty = false; toast(r.warnings && r.warnings.length ? "Saved — " + r.warnings[0] : "Index saved", { kind: r.warnings && r.warnings.length ? "info" : "success" }); changed(); showMemory(); }
            catch (e) { toast(e.message, { kind: "error" }); }
          } })),
        m.warnings.length ? h("div", { class: "mem-warn" }, m.warnings.map(w => h("div", null, icon("alert"), w))) : null,
        ta,
        h("div", { class: "field-hint", style: { padding: "6px 2px 0" } }, "Format: ", h("code", null, "- [Name](file.md) — one-line description"),
          ". Claude Code's auto memory reads this directory directly (", h("code", null, "autoMemoryDirectory"), "); Codex and Antigravity get it staged as MEMORY.md."));
    }

    async function renderTopicEditor(host, file) {
      mount(host, skeleton(6));
      let t;
      try { t = await api(`${base}/memory/topics/${enc(file)}`); } catch (e) { mount(host, errorBox(e.message)); return; }
      const meta = t.meta || {};
      const dirty = () => { st.dirty = true; note.textContent = "Unsaved"; };
      const name = h("input", { class: "input", value: meta.name || "", oninput: dirty, id: "mem-topic-name" });
      const desc = h("input", { class: "input", value: meta.description || "", oninput: dirty, placeholder: "One line — what the index shows" });
      const type = h("select", { class: "select", onchange: () => { dirty(); counters.classList.toggle("hidden", type.value !== "feedback"); } }, TYPES.map(x => h("option", { value: x }, x)));
      type.value = TYPES.includes(meta.type) ? meta.type : "project";
      const helpful = h("input", { class: "input", type: "number", min: "0", value: String(meta.helpful || 0), oninput: dirty, style: { width: "80px" } });
      const harmful = h("input", { class: "input", type: "number", min: "0", value: String(meta.harmful || 0), oninput: dirty, style: { width: "80px" } });
      const counters = h("div", { class: "row" + (type.value === "feedback" ? "" : " hidden") },
        h("label", { class: "row faint", style: { fontSize: "12px" } }, "helpful", helpful), h("label", { class: "row faint", style: { fontSize: "12px" } }, "harmful", harmful));
      const ta = h("textarea", { class: "input code mem-ta", spellcheck: "false", oninput: dirty, id: "mem-topic-body" });
      ta.value = t.body || "";
      const note = h("span", { class: "faint", style: { fontSize: "11.5px" } });
      mount(host,
        h("div", { class: "mem-ed-head" }, typePill(type.value), h("b", { class: "mono" }, file), h("span", { class: "grow" }), note,
          btn(null, { icon: "history", kind: "quiet", size: "sm", title: "History of this file", onClick: () => openFileHistory(`memory/${file}`) }),
          btn(null, { icon: "trash", kind: "quiet", size: "sm", title: "Delete this memory", onClick: async () => {
            if (!(await confirmDialog("Delete this memory?", h("p", { class: "muted" }, h("code", null, file), " and its index line are removed — a commit, so History can bring it back."), { confirm: "Delete", danger: true }))) return;
            try { await api(`${base}/memory/topics/${enc(file)}`, { method: "DELETE" }); st.dirty = false; st.sel = "__index"; toast("Memory deleted", { kind: "success" }); changed(); showMemory(); }
            catch (e) { toast(e.message, { kind: "error" }); }
          } }),
          btn("Save", { icon: "save", kind: "primary", size: "sm", id: "mem-save", onClick: async () => {
            if (!name.value.trim()) { fieldError(name, "Name is required"); return; }
            const m2 = { name: name.value.trim(), description: desc.value.trim(), type: type.value };
            if (type.value === "feedback") { m2.helpful = Math.max(0, +helpful.value || 0); m2.harmful = Math.max(0, +harmful.value || 0); }
            for (const [k, v] of Object.entries(meta)) if (!(k in m2) && !["helpful", "harmful"].includes(k)) m2[k] = v;
            try { await api(`${base}/memory/topics/${enc(file)}`, jsonOpts("PUT", { meta: m2, body: ta.value })); st.dirty = false; toast("Memory saved", { kind: "success" }); changed(); showMemory(); }
            catch (e) { toast(e.message, { kind: "error" }); }
          } })),
        h("div", { class: "field-row" }, h("label", { class: "field" }, h("span", { class: "field-label" }, "Name"), name),
          h("label", { class: "field" }, h("span", { class: "field-label" }, "Type"), type, h("span", { class: "field-hint" }, TYPE_HINT[type.value]))),
        h("label", { class: "field" }, h("span", { class: "field-label" }, "Description", h("span", { class: "faint" }, " — the index line")), desc),
        counters, ta);
    }

    async function newTopic() {
      if (!(await guard())) return;
      const name = h("input", { class: "input", placeholder: "e.g. Deploys go through staging", autofocus: true });
      const type = h("select", { class: "select" }, TYPES.map(x => h("option", { value: x }, `${x} — ${TYPE_HINT[x]}`)));
      type.value = "feedback";
      const desc = h("input", { class: "input", placeholder: "One line for the index" });
      const bodyTa = h("textarea", { class: "input code", rows: "6", placeholder: "The memory itself — specific and short." });
      modal({ title: "New memory", width: "560px",
        body: h("div", null, h("label", { class: "field" }, h("span", { class: "field-label" }, "Name"), name),
          h("label", { class: "field" }, h("span", { class: "field-label" }, "Type"), type),
          h("label", { class: "field" }, h("span", { class: "field-label" }, "Description"), desc),
          h("label", { class: "field" }, h("span", { class: "field-label" }, "Body"), bodyTa)),
        actions: [{ label: "Cancel", kind: "ghost" }, { label: "Create", kind: "primary", icon: "plus", onClick: async () => {
          if (!name.value.trim()) { fieldError(name, "Name is required"); return false; }
          const taken = new Set((st.mem.topics || []).map(t => t.file));
          let file = `${type.value}_${slug(name.value)}.md`, n = 2;
          while (taken.has(file)) file = `${type.value}_${slug(name.value)}_${n++}.md`;
          await api(`${base}/memory/topics/${enc(file)}`, jsonOpts("PUT", { meta: { name: name.value.trim(), description: desc.value.trim(), type: type.value }, body: bodyTa.value }));
          st.sel = file; st.dirty = false; toast("Memory added", { kind: "success" }); changed(); showMemory();
        } }] });
    }

    // ── History ────────────────────────────────────────────────────────
    function commitRows(commits, onReload) {
      return h("div", { class: "files mem-history" }, commits.map((c, i) => {
        const add = c.files.reduce((a, f) => a + f.additions, 0), del = c.files.reduce((a, f) => a + f.deletions, 0);
        const origin = c.run_id ? h("a", { href: "#", class: "meta mono", title: `run ${c.run_id}${c.step_id ? "\nstep " + c.step_id : ""}`, onclick: (e) => { e.preventDefault(); if (opts.openRun) opts.openRun(c.run_id); } }, "run " + shortId(c.run_id, 6))
          : c.task_id ? h("span", { class: "meta mono", title: "task " + c.task_id }, "task " + shortId(c.task_id, 6)) : null;
        return h("div", { class: "frow" },
          h("span", { class: "kind" }, h("span", { class: "pill " + (KIND_TONE[c.kind] ?? "") }, c.kind)),
          h("span", { class: "nm", title: c.subject }, c.subject),
          origin,
          c.files.length ? h("span", { class: "meta mono" }, `${c.files.length} file${c.files.length === 1 ? "" : "s"} +${add} −${del}`) : null,
          h("span", { class: "meta mono", title: c.sha }, c.short),
          h("span", { class: "meta", title: fmtDateTime(c.date) }, relTime(c.date)),
          h("span", { class: "acts" },
            btn(null, { icon: "code", kind: "quiet", size: "sm", title: "Show this commit's diff", onClick: () => openDiffModal({ title: "Memory change", subtitle: `${c.short} · ${c.subject}`,
              load: (path) => api(`${base}/memory/diff?commit=${c.sha}${path ? "&path=" + enc(path) : ""}`) }) }),
            c.parents.length && i >= 0 ? btn(null, { icon: "history", kind: "quiet", size: "sm", title: "Revert this change (a new commit undoing it)", onClick: async () => {
              if (!(await confirmDialog("Revert this memory change?", h("p", { class: "muted" }, "A new commit undoes ", h("code", null, c.short), " (", c.subject, "). If later changes touched the same lines the revert is refused."), { confirm: "Revert", danger: true }))) return;
              try { const r = await api(`${base}/memory/revert`, jsonOpts("POST", { commit: c.sha })); toast(r.noop ? "Nothing to undo — already reverted" : "Reverted", { kind: "success" }); changed(); onReload(); }
              catch (e) { toast(e.message, { kind: "error" }); }
            } }) : null));
      }));
    }
    async function showHistory() {
      let r;
      try { r = await api(`${base}/memory/history?limit=100`); } catch (e) { mount(body, h("div", { class: "card-body" }, errorBox(e.message, show))); return; }
      const commits = r.commits || [];
      mount(body, h("div", { class: "row", style: { padding: "8px 12px", borderBottom: "1px solid var(--border)" } },
        h("span", { class: "faint grow", style: { fontSize: "12px" } }, `${commits.length} commit${commits.length === 1 ? "" : "s"} · every run's write-back is one (run:<id> step:<id>); concurrent runs merge as branches`),
        btn(null, { icon: "refresh", kind: "quiet", size: "sm", title: "Refresh", onClick: show })),
        commits.length ? commitRows(commits, show) : emptyState("history", "No history", "Commits appear as runs write back and as you edit.", null, true));
    }
    async function openFileHistory(path) {
      const host = h("div", null, skeleton(4, "sk-row"));
      const m = modal({ title: "File history", subtitle: path, cls: "wide", width: "900px", body: host, actions: [{ label: "Close", kind: "primary" }] });
      const load = async () => {
        try { const r = await api(`${base}/memory/history?limit=100&path=${enc(path)}`); mount(host, r.commits.length ? commitRows(r.commits, () => { load(); showMemory(); }) : emptyState("history", "No history", null, null, true)); }
        catch (e) { mount(host, errorBox(e.message, load)); }
      };
      load();
      return m;
    }

    // ── Skills ─────────────────────────────────────────────────────────
    async function showSkills() {
      let r;
      try { r = await api(`${base}/skills`); } catch (e) { mount(body, h("div", { class: "card-body" }, errorBox(e.message, show))); return; }
      const skills = r.skills || [];
      mount(body,
        h("div", { class: "row", style: { padding: "8px 12px", borderBottom: "1px solid var(--border)" } },
          h("span", { class: "faint grow", style: { fontSize: "12px" } }, "Staged into each run as ", h("code", null, ".agents/skills/"), " + ", h("code", null, ".claude/skills/"), " — never over a skill the workspace already has."),
          btn("From global", { icon: "download", kind: "ghost", size: "sm", title: "Copy a skill from ~/.claude/skills into this agent", onClick: () => promoteDialog() }),
          btn("New skill", { icon: "plus", kind: "ghost", size: "sm", onClick: () => editSkill(null) })),
        skills.length ? h("div", { class: "files" }, skills.map(s => h("div", { class: "frow" }, icon("wrench"),
          h("span", { class: "nm" }, s.name, h("span", { class: "mem-desc" }, s.description || (s.has_skill_md ? "" : "no SKILL.md"))),
          h("span", { class: "meta" }, `${s.file_count} file${s.file_count === 1 ? "" : "s"}`),
          h("span", { class: "acts" },
            btn(null, { icon: "edit", kind: "quiet", size: "sm", title: "Edit SKILL.md", onClick: () => editSkill(s.name) }),
            btn(null, { icon: "upload", kind: "quiet", size: "sm", title: "Copy to global skills (~/.claude/skills)", onClick: async () => {
              const go = async (overwrite) => api(`${base}/skills/${enc(s.name)}/copy-to-global`, jsonOpts("POST", { overwrite }));
              try { await go(false); toast(`${s.name} copied to global skills`, { kind: "success" }); }
              catch (e) {
                if (/already exists/.test(e.message) && await confirmDialog("Replace the global skill?", `A global skill named ${s.name} exists. Replace it with this agent's version?`, { confirm: "Replace", danger: true })) {
                  try { await go(true); toast("Global skill replaced", { kind: "success" }); } catch (e2) { toast(e2.message, { kind: "error" }); }
                } else if (!/already exists/.test(e.message)) toast(e.message, { kind: "error" });
              }
            } }),
            btn(null, { icon: "trash", kind: "quiet", size: "sm", title: "Delete from this agent", onClick: async () => {
              if (!(await confirmDialog("Delete this skill?", `${s.name} is removed from the agent (a commit — History can restore it).`, { confirm: "Delete", danger: true }))) return;
              try { await api(`${base}/skills/${enc(s.name)}`, { method: "DELETE" }); showSkills(); } catch (e) { toast(e.message, { kind: "error" }); }
            } }))))) : emptyState("wrench", "No skills", "Procedural memory every engine can read: a SKILL.md per skill, staged into each run.", btn("New skill", { icon: "plus", kind: "ghost", size: "sm", onClick: () => editSkill(null) }), true));
    }
    async function editSkill(name) {
      let content = "---\nname: \ndescription: \n---\n\n";
      if (name) { try { content = (await api(`${base}/skills/${enc(name)}`)).content || ""; } catch (e) { toast(e.message, { kind: "error" }); return; } }
      const nm = h("input", { class: "input mono", value: name || "", disabled: !!name, placeholder: "lowercase-name", autofocus: !name });
      const ta = h("textarea", { class: "input code", rows: "16", spellcheck: "false" });
      ta.value = content;
      modal({ title: name ? `Skill ${name}` : "New skill", width: "720px",
        body: h("div", null, h("label", { class: "field" }, h("span", { class: "field-label" }, "Name"), nm, h("span", { class: "field-hint" }, "a-z, 0-9, - and _; 1–41 characters")),
          h("label", { class: "field" }, h("span", { class: "field-label" }, "SKILL.md"), ta)),
        actions: [{ label: "Cancel", kind: "ghost" }, { label: "Save", kind: "primary", icon: "save", onClick: async () => {
          const n = nm.value.trim();
          if (!/^[a-z0-9][a-z0-9_-]{0,40}$/.test(n)) { fieldError(nm, "Use a-z, 0-9, - and _"); return false; }
          await api(`${base}/skills/${enc(n)}`, jsonOpts("PUT", { content: ta.value }));
          toast("Skill saved", { kind: "success" }); showSkills();
        } }] });
    }
    async function promoteDialog() {
      let g;
      try { g = await api("/api/skills"); } catch (e) { toast(e.message, { kind: "error" }); return; }
      const list = g.skills || [];
      if (!list.length) { toast("No global skills in ~/.claude/skills", { kind: "info" }); return; }
      const sel = h("select", { class: "select" }, list.map(s => h("option", { value: s.name }, s.name + (s.description ? " — " + s.description.slice(0, 80) : ""))));
      modal({ title: "Add a global skill to this agent", width: "560px", body: h("label", { class: "field" }, h("span", { class: "field-label" }, "Global skill"), sel,
          h("span", { class: "field-hint" }, "A copy — later edits to either side stay separate.")),
        actions: [{ label: "Cancel", kind: "ghost" }, { label: "Add to agent", kind: "primary", icon: "plus", onClick: async () => {
          try { await api(`/api/skills/${enc(sel.value)}/promote`, jsonOpts("POST", { agent_id: agentId })); }
          catch (e) {
            if (!/already has/.test(e.message)) throw e;
            if (!(await confirmDialog("Replace the agent's skill?", `The agent already has ${sel.value}. Replace it with the global version?`, { confirm: "Replace", danger: true }))) return false;
            await api(`/api/skills/${enc(sel.value)}/promote`, jsonOpts("POST", { agent_id: agentId, overwrite: true }));
          }
          toast("Skill added", { kind: "success" }); showSkills();
        } }] });
    }

    // ── Pinned constraints ─────────────────────────────────────────────
    async function showPinned() {
      let r;
      try { r = await api(`${base}/pinned`); } catch (e) { mount(body, h("div", { class: "card-body" }, errorBox(e.message, show))); return; }
      const ta = h("textarea", { class: "input code mem-ta", spellcheck: "false", id: "mem-pinned", placeholder: "- Never push to main.\n- Stay inside the workspace.", oninput: () => { st.dirty = true; note.textContent = "Unsaved"; } });
      ta.value = r.text || "";
      const note = h("span", { class: "faint", style: { fontSize: "11.5px" } });
      mount(body, h("div", { class: "card-body" },
        h("div", { class: "mem-ed-head" }, h("b", null, "Pinned constraints"), h("span", { class: "faint" }, "the ", h("code", null, "## Pinned constraints"), " section of AGENT.md"), h("span", { class: "grow" }), note,
          btn("Save", { icon: "save", kind: "primary", size: "sm", id: "mem-save", onClick: async () => {
            try { await api(`${base}/pinned`, jsonOpts("PUT", { text: ta.value })); st.dirty = false; note.textContent = ""; toast("Pinned constraints saved", { kind: "success" }); changed(); }
            catch (e) { toast(e.message, { kind: "error" }); }
          } })),
        ta,
        h("div", { class: "field-hint", style: { paddingTop: "6px" } }, "Re-injected at the very end of every trigger fire's prompt and carried across session rotation, so long conversations don't lose them. Reflection never contradicts them.")));
    }

    // ── Reflection ─────────────────────────────────────────────────────
    async function showReflection() {
      let r;
      try { r = await api(`${base}/memory/reflection`); } catch (e) { mount(body, h("div", { class: "card-body" }, errorBox(e.message, show))); return; }
      const nightly = h("input", { type: "checkbox", role: "switch", id: "mem-nightly" });
      nightly.checked = !!r.nightly;
      const sw = h("label", { class: "switch-field" + (r.nightly ? " on" : ""), title: `Nightly at cron “${r.cron}” (UTC unless tasks.memory.reflect_tz)` }, h("span", { class: "switch" }, nightly, h("span")), h("span", null, "Nightly"));
      nightly.addEventListener("change", async () => {
        try { await api(`${base}/memory/reflection`, jsonOpts("PUT", { nightly: nightly.checked })); toast(nightly.checked ? "Nightly reflection on" : "Nightly reflection off", { kind: "success" }); showReflection(); }
        catch (e) { toast(e.message, { kind: "error" }); nightly.checked = !nightly.checked; }
      });
      const after = h("input", { class: "input", type: "number", min: "0", value: String(r.after_runs), style: { width: "80px" }, id: "mem-after-runs", title: "0 = off" });
      after.addEventListener("change", async () => {
        try { await api(`${base}/memory/reflection`, jsonOpts("PUT", { after_runs: Math.max(0, +after.value || 0) })); toast("Saved", { kind: "success" }); showReflection(); }
        catch (e) { toast(e.message, { kind: "error" }); }
      });
      const running = !!r.running_task_id;
      const last = r.last;
      const trig = r.trigger;
      const lastBox = last ? h("div", { class: "mem-last" },
        h("div", { class: "row" }, statusPill(last.status === "proposed" ? "awaiting_input" : last.status === "nothing_to_change" ? "ok" : last.status,
          { label: { proposed: "proposed — waiting for approval", nothing_to_change: "nothing to change", unparsed: "reply not understood", failed: "failed" }[last.status] || last.status }),
          last.decision ? statusPill(last.decision.status) : null,
          h("span", { class: "faint", title: fmtDateTime(last.at) }, relTime(last.at)), h("span", { class: "grow" }),
          last.status === "proposed" && !last.decision ? btn("Review", { icon: "inbox", kind: "primary", size: "sm", onClick: () => openApprovalsInbox() }) : null),
        last.summary ? h("p", { class: "muted", style: { margin: "4px 0" } }, last.summary) : null,
        last.error ? h("div", { class: "field-err" }, last.error) : null,
        (last.operations || []).length ? h("div", { class: "files mem-ops" }, last.operations.map(o => h("div", { class: "frow" },
          h("span", { class: "pill " + ({ ADD: "ok", UPDATE: "accent", DELETE: "err" }[o.op] || "") }, o.op),
          h("span", { class: "nm" }, o.file || o.name || "—"), h("span", { class: "meta ellipsis", title: o.error || o.reason || "" }, o.error || o.reason || ""),
          h("span", { class: "pill " + (o.status === "skipped" ? "warn" : ""), title: o.status === "applied" ? "part of the proposed diff" : "" },
            o.status === "applied" ? ((last.decision && last.decision.status === "applied") ? "applied" : "in the diff") : o.status)))) : null) : null;
      mount(body, h("div", { class: "card-body col", style: { gap: "12px" } },
        h("div", { class: "row wrap", style: { gap: "10px" } },
          btn(running ? "Reflecting…" : "Reflect now", { icon: "sparkle", kind: "primary", size: "sm", id: "mem-reflect", disabled: running, onClick: async () => {
            try { await api(`${base}/memory/reflect`, jsonOpts("POST", {})); toast("Reflection started — the proposal lands in the Approvals inbox", { kind: "success" }); showReflection(); }
            catch (e) { toast(e.message, { kind: "error" }); }
          } }),
          sw, h("label", { class: "row faint", style: { fontSize: "12.5px" } }, "after", after, "runs"),
          h("span", { class: "grow" }),
          trig ? h("a", { href: "#", class: "faint", style: { fontSize: "12px" }, onclick: (e) => { e.preventDefault(); if (opts.openTrigger) opts.openTrigger(trig.id); } }, icon("clock"), " trigger · ", trig.engine, trig.model ? " / " + trig.model : "", trig.is_local ? " · local" : " · cloud") : null,
          btn(null, { icon: "refresh", kind: "quiet", size: "sm", title: "Refresh", onClick: showReflection })),
        h("div", { class: "row wrap faint", style: { fontSize: "12px", gap: "14px" } },
          h("span", null, h("b", { class: "mono" }, String(r.runs_since)), ` run${r.runs_since === 1 ? "" : "s"} since the last reflection`, r.after_runs ? ` (reflects at ${r.after_runs})` : " (automatic reflection off)"),
          h("span", null, "last: ", r.last_reflection_at ? relTime(r.last_reflection_at) : "never"),
          trig && trig.status === "active" && trig.next_fire_at ? h("span", null, "next nightly: ", fmtDateTime(trig.next_fire_at)) : null,
          r.pending_approval_ids.length ? h("span", { class: "pill violet" }, `${r.pending_approval_ids.length} proposal waiting`) : null),
        h("p", { class: "field-hint", style: { margin: 0 } }, "Reads the index, the topic files and the handoffs of recent runs, and proposes ADD / UPDATE / DELETE per memory (with helpful / harmful counters on feedback). Nothing changes until you approve the diff in the Approvals inbox. Runs in the cloud on the agent's engine", r.local ? " — local mode is ON (tasks.memory.reflect_local)." : "."),
        lastBox || emptyState("sparkle", "No reflection yet", "Reflect now, or let it run after enough runs.", null, true)));
    }

    renderTabs();
    show();
    return { refresh: show, get dirty() { return st.dirty; } };
  }

  // Approvals inbox body for kind "memory": summary, operations, coloured diff.
  function approvalBody(ap) {
    const p = ap.payload || {};
    return h("div", { class: "col", style: { gap: "6px" } },
      h("div", { class: "row wrap faint", style: { fontSize: "11.5px", gap: "10px" } },
        p.agent_id ? h("a", { href: `/team#/agent/${enc(p.agent_id)}`, title: "Open the agent" }, icon("agent"), " ", p.agent_name || "agent") : null,
        (p.files || []).length ? h("span", null, `${p.files.length} file${p.files.length === 1 ? "" : "s"}`) : null,
        (p.warnings || []).map(w => h("span", { class: "pill warn" }, w))),
      p.summary ? h("p", { class: "muted", style: { margin: 0, fontSize: "12.5px" } }, p.summary) : null,
      ap.body ? diffView(ap.body) : null);
  }

  window.TCMemory = { mount: mountPanel, approvalBody };
})();
