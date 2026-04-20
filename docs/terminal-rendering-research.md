# Terminal capture & diff algorithms — research bibliography

Curated list of **50+** resources (papers, specs, implementations, blogs) relevant to **superior rendering** of PTY/TUI output into clean text streams (e.g. for Telegram). Grouped by topic; URLs are the primary references.

---

## 1. Diff algorithms (line / text alignment)

| # | Resource | Notes |
|---|----------|--------|
| 1 | [Bram Cohen — Patience Diff Advantages (LiveJournal)](https://bramcohen.livejournal.com/73318.html) | Original motivation for patience diff |
| 2 | [James Coglan — The patience diff algorithm](https://blog.jcoglan.com/2017/09/19/the-patience-diff-algorithm/) | Clear explanation |
| 3 | [James Coglan — Implementing patience diff](https://blog.jcoglan.com/2017/09/28/implementing-patience-diff/) | Implementation walkthrough |
| 4 | [Stack Overflow — Where is patience diff implemented?](https://stackoverflow.com/questions/16066288/where-can-i-find-the-patience-diff-implemented) | Pointers to Bazaar/Git/Jane Street |
| 5 | [Jane Street — patdiff (patience-based file diff)](https://opensource.janestreet.com/patdiff/) | Production OCaml tool |
| 6 | [Eugene W. Myers — An O(ND) Difference Algorithm and its Variations (Janelia / publication page)](https://www.janelia.org/publication/ond-difference-algorithm-and-its-variations) | **1986 Algorithmica** — foundation of many diffs |
| 7 | [Myers diff explained — jsdiff docs](https://www.jsdiff.com/docs/myers-diff-algorithm.html) | Practical overview |
| 8 | [Neil Fraser — Myers diff PDF mirror](https://neil.fraser.name/writing/diff/myers.pdf) | PDF access to classic paper |
| 9 | [Paul Heckel — A technique for isolating differences between files (ACM, 1978)](https://dl.acm.org/doi/10.1145/359460.359467) | **O(n)** line diff; unique-line insight overlaps with patience |
|10 | [Wikipedia — Diff](https://en.wikipedia.org/wiki/Diff) | History + algorithm families |
|11 | [Ray Gardner — How histogram diff actually works (2025)](https://www.raygard.net/2025/01/28/how-histogram-diff-works/) | **Git histogram** — extends patience when few unique lines |
|12 | [Ray Gardner — Histogram diff implementation](https://www.raygard.net/2025/01/29/a-histogram-diff-implementation/) | Working code discussion |
|13 | [Stack Overflow — `git diff --patience` vs `--histogram`](https://stackoverflow.com/questions/32365271/whats-the-difference-between-git-diff-patience-and-git-diff-histogram) | When each wins |
|14 | [JGit `HistogramDiff` API](https://archive.eclipse.org/jgit/docs/jgit-2.0.0.201206130900-r/apidocs/org/eclipse/jgit/diff/HistogramDiff.html) | Reference implementation lineage |
|15 | [algomaster99 / myers-vs-histogram (GitHub)](https://github.com/algomaster99/myers-vs-histogram) | Empirical comparison project |

---

## 2. Hashing & binary / chunk diff (adjacent techniques)

| # | Resource | Notes |
|---|----------|--------|
|16 | [Wikipedia — Rabin–Karp](https://en.wikipedia.org/wiki/Rabin%E2%80%93Karp_algorithm) | Rolling hash for matching blocks |
|17 | [cp-algorithms — Rabin-Karp](https://cp-algorithms.com/string/rabin-karp.html) | Competitive-programming style |
|18 | [OpenGenus — LCS via rolling hash](https://iq.opengenus.org/longest-common-substring-using-rolling-hash/) | Substring / block matching ideas |
|19 | [Colin Percival — bsdiff](http://www.daemonology.net/bsdiff/) | Binary diff (bytewise) — not line-oriented but related tooling |
|20 | [divvun/bidiff (Rust)](https://github.com/divvun/bidiff) | Modern binary diff, parallel, zstd chunks |

---

## 3. Semantic / structural diff (if you ever parse code from terminal)

| # | Resource | Notes |
|---|----------|--------|
|21 | [GumTree — GitHub wiki](https://github.com/GumTreeDiff/gumtree/wiki) | **AST diff** — move/update/delete |
|22 | [GumTree — Getting started](https://github.com/GumTreeDiff/gumtree/wiki/Getting-Started) | Multi-language |
|23 | [SemanticDiff — docs](https://semanticdiff.com/docs/what-is-semanticdiff) | Product framing of semantic diffs |
|24 | [Microsoft Research — LASE](https://www.microsoft.com/en-us/research/publication/lase-locating-and-applying-systematic-edits-by-learning-from-examples/) | Learning systematic edits |
|25 | [SpoonLabs / gumtree-spoon-ast-diff](https://github.com/SpoonLabs/gumtree-spoon-ast-diff) | Java AST diffs |

---

## 4. Collaborative editing theory (OT / CRDT) — “merge streams without garbling”

| # | Resource | Notes |
|---|----------|--------|
|26 | [Raph Levien — Unified theory of OT and CRDT (Medium)](https://medium.com/@raphlinus/towards-a-unified-theory-of-operational-transformation-and-crdt-70485876f72f) | High-level bridge |
|27 | [ACM — Real differences between OT and CRDT](https://dl.acm.org/doi/10.1145/3375186) | Formal comparison |
|28 | [arXiv — OT/CRDT non-interleaving / Fugue (2023)](https://export.arxiv.org/pdf/2305.00583v1.pdf) | Concurrency & ordering |

---

## 5. Terminal emulation, scrollback, capture (industry patterns)

| # | Resource | Notes |
|---|----------|--------|
|29 | [pyte — GitHub (selectel/pyte)](https://github.com/selectel/pyte) | VT parser + `Screen` / `HistoryScreen` |
|30 | [pyte — Read the Docs API](https://pyte.readthedocs.io/en/latest/api.html) | `dirty` lines, `HistoryScreen` |
|31 | [pyte — PyPI](https://pypi.org/project/pyte/) | Version / deps |
|32 | [WezTerm — `pane:get_lines_as_text`](https://wezfurlong.org/wezterm/config/lua/pane/get_lines_as_text.html) | **Plain text from viewport + scrollback** |
|33 | [WezTerm — `get_lines_as_escapes`](https://wezterm.org/config/lua/pane/get_lines_as_escapes.html) | Preserve SGR/color |
|34 | [tmux — capture-pane / scrollback (tutorial)](https://tmuxai.dev/tmux-capture-pane/) | `-S -` full scrollback pattern |
|35 | [Unix.SE — Write all tmux scrollback to file](https://unix.stackexchange.com/questions/26548/write-all-tmux-scrollback-to-a-file) | Practical recipes |
|36 | [Unix.SE — Get tmux scroll buffer](https://unix.stackexchange.com/questions/125647/get-tmux-scroll-buffer-contents) | Line-range capture |
|37 | [man7 — tmux(1)](https://man7.org/linux/man-pages/man1/tmux.1.html) | Official options |
|38 | [asciinema — asciicast v3 spec](https://docs.asciinema.org/manual/asciicast/v3/) | **Timed event stream** (alternative to ad-hoc diff) |
|39 | [asciinema — v3 announcement blog](https://blog.asciinema.org/post/three-point-o/) | NDJSON + deltas |
|40 | [GNU `script` man page (manned.org)](https://manned.org/script) | Raw **typescript** session capture |
|41 | [Red Hat — script / scriptreplay](https://www.redhat.com/sysadmin/record-terminal-script-scriptreplay) | Timing + replay |
|42 | [VTE — `scrollback-lines` property](https://gnome.pages.gitlab.gnome.org/vte/gtk4/property.Terminal.scrollback-lines.html) | **Alternate screen has no scrollback** |
|43 | [VTE GitLab — O(1) rewrap / scrollback design discussion](https://gitlab.gnome.org/GNOME/vte/-/issues/2154) | Resize + scrollback theory |
|44 | [xterm.js — `IBuffer` API](https://xtermjs.org/docs/api/terminal/interfaces/ibuffer) | `baseY`, viewport, buffer length |
|45 | [xterm.js — alternate screen + scrollback issue](https://github.com/xtermjs/xterm.js/issues/3607) | Real-world buffer semantics |
|46 | [Alacritty — scrollback implementation PR](https://github.com/alacritty/alacritty/pull/657) | **VecDeque** grid + scrollback |
|47 | [jwilm.io — Alacritty scrollback lands](https://jwilm.io/blog/alacritty-lands-scrollback/) | Design post |
|48 | [Zellij — edit scrollback in `$EDITOR`](https://zellij.dev/news/edit-scrollback-compact/) | Treat scrollback as a **document** |
|49 | [Zellij — DumpScreen / actions](https://zellij.dev/documentation/keybindings-possible-actions) | Export pane contents |
|50 | [suckless scroll / ring buffer discussion](https://git.suckless.org/sites/commit/308bc705a0482ddc18020ab584c11a9f37723cf9.html) | Ring buffer + view offset |
|51 | [muccc/scrollback (GitHub)](https://github.com/muccc/scrollback) | External scrollback helper |

---

## 6. PTY / OS layers (where bytes come from)

| # | Resource | Notes |
|---|----------|--------|
|52 | [Microsoft — Creating a pseudoconsole session](https://learn.microsoft.com/en-us/windows/console/creating-a-pseudoconsole-session) | **ConPTY** |
|53 | [Microsoft — `CreatePseudoConsole`](https://learn.microsoft.com/en-us/windows/console/createpseudoconsole) | API reference |
|54 | [Microsoft DevBlogs — Introducing ConPTY](https://devblogs.microsoft.com/commandline/windows-command-line-introducing-the-windows-pseudo-console-conpty/) | Architecture narrative |
|55 | [Windows Terminal — in-process ConPTY spec](https://github.com/microsoft/terminal/blob/main/doc/specs/%2313000%20-%20In-process%20ConPTY.md) | Deep dive |

---

## 7. Rich TUI rendering (damage / planes — why TUIs are hard)

| # | Resource | Notes |
|---|----------|--------|
|56 | [notcurses — `notcurses_render(3)`](https://notcurses.com/notcurses_render.3.html) | Compositing planes → output |
|57 | [dankwiki — Notcurses](https://nick-black.com/dankwiki/index.php?title=Notcurses) | Author’s design notes |
|58 | [libvterm (Neovim)](https://github.com/neovim/libvterm) | Another VT100 core used in editors |

---

## 8. Bypass TUI entirely (best fidelity for Claude Code)

| # | Resource | Notes |
|---|----------|--------|
|59 | [Claude Code — CLI reference](https://code.claude.com/docs/en/cli-reference) | `--print`, `--output-format`, `--include-partial-messages` |
|60 | [Claude Code — Headless / programmatic](https://code.claude.com/docs/en/headless.md) | Official non-interactive path |
|61 | [GitHub — anthropics/claude-code streaming print issue #733](https://github.com/anthropics/claude-code/issues/733) | Real-world stream-json discussion |

---

## Takeaways for Telecode (PTY mode)

1. **Single scrollback document** — Aligns with tmux `capture-pane -S -` / WezTerm `get_lines_as_text` (resources **32–37**).
2. **Patience + histogram** — Patience anchors (**1–5**); when few unique lines exist, histogram diff (**11–14**) is the usual Git upgrade path.
3. **Myers / Heckel** — Underpin `difflib` and many tools (**6–10**); understand limits vs patience on repeated lines.
4. **Alternate screen** — Many full-screen TUIs don’t populate classic scrollback (**42**, **45**); pyte `HistoryScreen` still helps but cannot beat **structured** output (**59–61**).
5. **Future upgrade** — Optional **asciicast-like** event log (**38–39**) or Claude **stream-json** session (**59–61**) avoids fighting the renderer.

---

## What Telecode implements (`sessions/terminal.py`)

| Idea | Source family | In code |
|------|----------------|---------|
| One scrollback + viewport stream | tmux / WezTerm | `_full_snapshot_lines` |
| NFKC + whitespace keys | Unicode / plain-text export | `_norm_key` |
| Unique-line anchors + LIS | Patience (Cohen, Coglan, Git) | `_patience_anchor_indices`, `_lis_indices` |
| Rare-key monotone anchors | Histogram / low-frequency (JGit, Gardner) | `_histogram_greedy_anchors` |
| Anchor choice | patience then histogram | `_choose_anchors` |
| Recursive splits | Local patience inside hunks | `_extract_new_lines_impl` + `_MAX_ANCHOR_DEPTH` |
| Segment LCS | Myers-class (via `difflib`) | `_diff_segment` |
| Cosmetic-only replace | Fuzzy line match | `_similar` |

**Not** implemented (different problem domains): GumTree/LASE (need AST), OT/CRDT (need op streams), Rabin–Karp/bsdiff (optional speed/binary), notcurses plane composite (renderer internal).

---

*Generated as a research index; not all links were fetched in full. Prefer official docs and papers for implementation details.*
