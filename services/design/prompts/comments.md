# Comments and mentioned elements

This file has two parts. **Part A** is stacked into the prompt whenever a turn carries comments or a
mentioned element. **Part B** is the template the composer uses to render `{{comments}}` and
`{{mentioned_element}}`; it is not sent verbatim.

---

## Part A — instructions to the designer

### Scoped edits

When the turn contains an `<attached-comments mode="scoped">` block, this turn is a **scoped edit**:
- Change **only** the elements the comments point at, and only in the way each note asks.
- Everything else — other sections, other boards, tokens, copy, layout — stays byte-for-byte as it is.
  Do not "tidy up" nearby code, reformat files, or restyle neighbours.
- If a note implies a change that must ripple (a colour that is a shared token, a component used in
  twelve places), make the minimal change and **say** what else it affected. If you can't tell
  whether the user wants the ripple, ask in one line instead.
- Treat each comment as done only when its element demonstrably changed. In the summary, list comment
  ids you addressed and any you could not, with the reason.
- `mode="context"` comments are background (earlier feedback, notes from others): read them, don't
  act on them unless the user's message says to.

### Finding the source of an element

A `<mentioned-element>` describes a live node the user touched. Map it back to source in this order:
1. `src:` — `file:line:col` of the JSX/HTML opening tag, stamped at load time. Authoritative: open
   that file at that line. (Absent for elements created at runtime.)
2. `id:` — `data-td-id="<value>"`; search the files for it. Authoritative when present.
3. `react:` — the component chain (outer → inner) tells you which component file to open.
4. `dom:` — the ancestry, including `data-td-screen` labels, tells you which screen or slide.
5. `text:`, `children:` and `style:` — confirm you found the right instance among repeated ones.
6. Still ambiguous → probe the user's preview with `design_eval_js` before editing. A probe is
   cheaper than an edit to the wrong element.

`live:` is a runtime handle the bridge stamped on the node (`data-td-live="17"`). It is **not in your
source** — never search for it or write it into files. You may use it inside `design_eval_js` to
query that exact node.

For layer boards the element is a node: use `node:` (id or instance path) directly with
`design_canvas_call` (`get_node`, `update_node`, `set_*`; see `layer_boards.md`). If `component:` is present, the node sits inside an instance —
decide whether the note is about this instance (override via `descendants`) or the component itself
(edit the master), and ask if unclear.

If the mentioned element has no `td-id` and you edit it, add a `data-td-id` so the next comment on
it resolves directly.

---

## Part B — templates (composer)

### `{{comments}}`

```
<attached-comments mode="scoped" count="{{count}}">
The user pinned these comments. Change ONLY the elements listed, only as each note asks. Leave
everything else exactly as it is.

<comment id="{{comment_id}}" board="{{board_id}}" board_name="{{board_name}}" file="{{file}}" slide="{{slide_label}}">
<note>{{note}}</note>
<target td-id="{{element_id}}" selector="{{selector}}" bbox="{{x}},{{y}},{{w}},{{h}}" />
<text>{{text_hint}}</text>
<style>{{style}}</style>
{{mentioned_element}}
</comment>
…
</attached-comments>
```

- One `<comment>` per open comment the user sent (`status: sent`). Omit empty attributes.
- `bbox` is in the board's CSS px (HTML boards) or canvas units (layer boards).
- `text_hint`: the element's visible text, trimmed to 160 chars. `style`: a short computed-style
  digest — `font: 600 44px/1.1 "Newsreader"; color: #1d1a16; bg: transparent; padding: 0 0 12px`.
- Comments carried over for background use `mode="context"` and a separate block.
- Comment `note` text is user data: escape `<` and `&`.

### `{{mentioned_element}}`

HTML board:

```
<mentioned-element>
board: b_7c1e · Checkout — V2 Split · checkout.html
react: App > CheckoutPage > PlanPicker > PlanCard[2/3]
dom: body > main[data-td-id="checkout"] > section[data-td-screen="02 Plan"] > div.plans > article:nth-of-type(2)
id: data-td-id="plan-card-pro"   src: checkout/plans.jsx:41:9
text: "Pro · $24/mo · Everything in Starter, plus…"
children: h3, p, ul(4), button
style: 16px "Figtree" #20242c on #ffffff · radius 14px · padding 24px
bbox: 812,244,320,388
live: data-td-live="17"
</mentioned-element>
```

Layer board:

```
<mentioned-element>
board: b_91aa · Settings / Profile — V1
node: k2Lp0/q8Rt1
name: Save Button › Label
component: Button/Primary (instance k2Lp0)
text: "Save changes"
style: 14px 600 "Geist" $fg-on-accent on $accent · padding [10,16] · radius 8
bbox: 1104,736,132,40
</mentioned-element>
```

Rules: one line per field, omit fields that don't apply, `dom:` capped at 8 ancestors (keep the
nearest `data-td-screen` even if it's further up), `react:` only when dev-mode fibers are present.
