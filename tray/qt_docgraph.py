"""DocGraph section — single host model.

After docgraph 2.2.0, telecode supervises one `docgraph host` process
covering every configured root. The UI mirrors that mental model:

  Host          — start/stop/restart of the single child + bind config.
  Roots         — table of registered repos. Per-row Index button + Watch
                  toggle. Add / remove. Watch toggle persists to settings;
                  the host needs a restart to pick up watch flips.
  LLM           — augmentation knobs that apply at index time.
  Embeddings    — embedding model + GPU.

Live log tailing is delegated to the global Logs section — the host's
stdout/stderr and the telecode-side wrapper logger both land in
`data/logs/docgraph.log`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QProgressBar,
)

from tray.qt_widgets import row_label, Toggle, WrapLabel
from tray.qt_helpers import (
    read_settings, get_path, patch_settings, schedule, humanize,
)
from tray.qt_theme import FG, FG_DIM, FG_MUTE, BG, BG_CARD, BG_ELEV, BORDER, OK, ERR, WARN
from tray.qt_sections import (
    _page, _card, _section_header, _row, _toggle_row, _line_row,
    _list_row, _number_row, _enum_row_strs, _wrap_align,
)

log = logging.getLogger("telecode.tray.docgraph")


def build_docgraph_tabs(window) -> QWidget:
    """Single scrollable page; 4 stacked cards."""
    scroll, _, layout = _page()
    refresh_fns: list[Callable[[], None]] = []

    # Binary
    binary_card, bb = _card("Binary")
    bb.addWidget(_line_row(
        "docgraph.binary",
        "Binary Path",
        "docgraph",
        "Bare name = use PATH. Empty = autodetect.",
        cli="docgraph CLI",
    ))
    layout.addWidget(binary_card)

    for build in (
        _build_host_card,
        _build_groups_card,
        _build_roots_card,
        _build_docs_card,
        _build_documents_index_card,
        _build_llm_card,
        _build_prompts_card,
        _build_embeddings_card,
        _build_reranker_card,
    ):
        card, refresh = build(window)
        layout.addWidget(card)
        if refresh is not None:
            refresh_fns.append(refresh)

    layout.addStretch(1)

    def refresh():
        for fn in refresh_fns:
            try:
                fn()
            except Exception:
                pass

    scroll.refresh = refresh  # type: ignore[attr-defined]
    return scroll


# ── Helpers ──────────────────────────────────────────────────────────────

def _bot_loop(window) -> asyncio.AbstractEventLoop | None:
    return getattr(window, "bot_loop", None)


def _run(window, coro_fn) -> None:
    loop = _bot_loop(window)
    if loop is None:
        return
    try:
        schedule(loop, coro_fn())
    except Exception as exc:
        log.warning("docgraph dispatch: %s", exc)


def _status_pill(getter):
    pill = QLabel("…")
    pill.setProperty("class", "stat_pill")

    def _refresh():
        try:
            ok, text = getter()
        except Exception as exc:
            pill.setText(f"err: {exc}")
            pill.setStyleSheet(f"color: {ERR};")
            return
        pill.setText(text)
        pill.setStyleSheet(f"color: {OK if ok else FG_MUTE};")

    return pill, _refresh


def _path_already_indexed(path: str) -> bool:
    """Detect whether `path` already has a docgraph index on disk.

    `<path>/.docgraph/cache.json` is the marker — the indexer writes it
    after every successful run and `/api/admin/clear` deletes it as part
    of the wipe. `graph.kuzu/` would also work as a marker but Kuzu's
    `wipe(keep_schema=False)` re-creates the directory with an empty
    schema, so it persists across Clear and the pill would lie."""
    if not path:
        return False
    try:
        from pathlib import Path as _Path
        marker = _Path(path).expanduser() / ".docgraph" / "cache.json"
        return marker.exists()
    except (OSError, ValueError):
        return False


def _path_already_wiki_built(path: str) -> bool:
    """Detect whether `<path>/.docgraph/wiki/` has any `.md` page.
    Same purpose as `_path_already_indexed`: distinguish 'wiki has been
    built sometime (perhaps via CLI directly)' from 'never built'."""
    if not path:
        return False
    try:
        from pathlib import Path as _Path
        wiki_dir = _Path(path).expanduser() / ".docgraph" / "wiki"
        if not wiki_dir.exists():
            return False
        return any(wiki_dir.glob("*.md"))
    except (OSError, ValueError):
        return False


def _format_ago(ts: float | None) -> str:
    import time as _time
    if not ts:
        return "never"
    delta = max(0, int(_time.time() - ts))
    if delta < 60:    return f"{delta}s ago"
    if delta < 3600:  return f"{delta // 60}m ago"
    if delta < 86400: return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


# ── Phase metadata for index/wiki progress bars ─────────────────────────
#
# Mirrors the `_emit("<phase>", ...)` calls in docgraph/index.py + wiki.py.
# Each phase carries:
#   - a human label
#   - whether it's count-driven (else indeterminate)
# The order defines the "[i/N]" ordinal shown to the user. Phases that
# are conditional on cfg (llm_augment, embed_chunks, documents) still
# get a slot — if they don't fire, the ordinal just skips them.

_INDEX_PHASES: list[tuple[str, str, bool]] = [
    ("start",          "starting",        False),
    ("delete",         "removing stale",  True),
    ("parse",          "parsing files",   True),
    ("llm_augment",    "llm docstrings",  True),
    ("seed_ids",       "seeding ids",     False),
    ("embed_entities", "embed entities",  True),
    ("embed_chunks",   "embed chunks",    True),
    ("symbol_table",   "symbol table",    False),
    ("edges",          "writing edges",   False),
    ("tier4_pagerank", "pagerank",        False),
    ("documents",      "documents",       False),
    ("done",           "done",            True),
]
_INDEX_PHASE_INDEX = {p[0]: (i, p[1]) for i, p in enumerate(_INDEX_PHASES)}

_WIKI_PHASES: list[tuple[str, str, bool]] = [
    ("start",  "preparing modules", True),
    ("module", "writing module",    True),
    ("done",   "done",              True),
]
_WIKI_PHASE_INDEX = {p[0]: (i, p[1]) for i, p in enumerate(_WIKI_PHASES)}


def _fmt_count(n: int) -> str:
    """Compact integer formatter — 173553 → '173.5k', 1234567 → '1.23M'."""
    n = int(n)
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    if n < 1_000_000_000:
        return f"{n / 1_000_000:.2f}M".rstrip("0").rstrip(".")
    return f"{n / 1_000_000_000:.2f}B".rstrip("0").rstrip(".")


def _fmt_phase_label(kind: str, phase: str, module: str = "") -> tuple[str, int, int]:
    """Return (display_label, ordinal, total_phases) for the given phase."""
    table = _INDEX_PHASE_INDEX if kind == "index" else _WIKI_PHASE_INDEX
    total = len(_INDEX_PHASES) if kind == "index" else len(_WIKI_PHASES)
    if phase in table:
        i, label = table[phase]
        if kind == "wiki" and phase == "module" and module:
            label = f"{label} · {module}"
        return label, i + 1, total
    return phase or "?", 0, total


# ── Host card ────────────────────────────────────────────────────────────

def _build_groups_card(window) -> tuple[QFrame, Callable[[], None]]:
    """Groups management card — add/edit/remove groups and their member paths.

    Groups are the newer multi-root feature; roots are legacy. When groups
    are configured, roots are ignored. Each group has a name, db_path, and
    a list of member paths with watch flags."""
    card, body = _card(
        "Groups",
        "Multiple code paths sharing one Kuzu database per group.",
    )

    # Full rebuild toggle
    body.addWidget(_toggle_row("docgraph.host.full_rebuild", "Full rebuild",
                                "On = --full / --force. Off = incremental."))

    groups_widget = _GroupsTable(window)
    body.addWidget(groups_widget)

        # Global action: Index all groups
    index_all_btn = QPushButton("▶ Index all")
    index_all_btn.setProperty("class", "primary")
    index_all_btn.clicked.connect(lambda: _index_all_groups(window))
    
    index_all_cancel = QPushButton("✕")
    index_all_cancel.setProperty("class", "danger")
    index_all_cancel.setFixedWidth(28)
    index_all_cancel.clicked.connect(lambda: _run(window, lambda: get_index().cancel()))
    
    index_all_status = QLabel("idle")
    index_all_status.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
    
    idx_w = QWidget()
    il = QHBoxLayout(idx_w); il.setContentsMargins(0, 0, 0, 0); il.setSpacing(8)
    il.addWidget(index_all_btn); il.addWidget(index_all_cancel); il.addWidget(index_all_status, 0); il.addStretch(1)
    body.addWidget(_row(row_label("Index all groups"), idx_w))

    # Global action: Build wikis for all groups
    wiki_all_btn = QPushButton("📋 Build wikis")
    wiki_all_btn.setProperty("class", "primary")
    wiki_all_btn.clicked.connect(lambda: _build_all_wikis(window))
    
    wiki_all_cancel = QPushButton("✕")
    wiki_all_cancel.setProperty("class", "danger")
    wiki_all_cancel.setFixedWidth(28)
    wiki_all_cancel.clicked.connect(lambda: _run(window, lambda: get_wiki().cancel()))
    
    wiki_status_lbl = QLabel("idle")
    wiki_status_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
    
    wiki_w = QWidget()
    wl = QHBoxLayout(wiki_w); wl.setContentsMargins(0, 0, 0, 0); wl.setSpacing(8)
    wl.addWidget(wiki_all_btn); wl.addWidget(wiki_all_cancel); wl.addWidget(wiki_status_lbl, 0); wl.addStretch(1)
    body.addWidget(_row(row_label("Build wikis for all groups"), wiki_w))

    def refresh():
        groups_widget.refresh()

    return card, refresh


def _index_all_groups(window) -> None:
    """Index all configured groups."""
    async def _go():
        from docgraph.process import get_host
        host = get_host()
        if not host._conn:
            return
        try:
            groups = get_path(read_settings(), "docgraph.groups", []) or []
            for group in groups:
                if isinstance(group, dict):
                    name = group.get("name", "")
                    if name:
                        await host._conn.api(f"/api/admin/index?root={name}")
        except Exception as e:
            log.warning("index all groups failed: %s", e)
    _run(window, _go)


def _build_all_wikis(window) -> None:
    """Build wikis for all configured groups."""
    async def _go():
        from docgraph.process import get_host
        host = get_host()
        if not host._conn:
            return
        try:
            groups = get_path(read_settings(), "docgraph.groups", []) or []
            for group in groups:
                if isinstance(group, dict):
                    name = group.get("name", "")
                    if name:
                        await host._conn.api(f"/api/wiki/build?root={name}")
        except Exception as e:
            log.warning("build all wikis failed: %s", e)
    _run(window, _go)


class _GroupsTable(QWidget):
    """Editor for `docgraph.groups[]` — each group has name, db_path, and member paths."""

    def __init__(self, window) -> None:
        super().__init__()
        self._window = window
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        self._groups_host = QWidget()
        self._groups_layout = QVBoxLayout(self._groups_host)
        self._groups_layout.setContentsMargins(0, 0, 0, 0)
        self._groups_layout.setSpacing(6)
        v.addWidget(self._groups_host)

        add_w = QWidget()
        add_l = QHBoxLayout(add_w)
        add_l.setContentsMargins(0, 0, 0, 0)
        add_btn = QPushButton("+ Add group")
        add_btn.setProperty("class", "primary")
        add_btn.setMaximumWidth(140)
        add_btn.clicked.connect(self._on_add)
        add_l.addWidget(add_btn)
        add_l.addStretch(1)
        v.addWidget(add_w)

        self._group_widgets: list[_GroupRow] = []
        self._rebuild()

    def _rebuild(self) -> None:
        for w in self._group_widgets:
            w.setParent(None)
            w.deleteLater()
        self._group_widgets.clear()
        cur = list(get_path(read_settings(), "docgraph.groups", []) or [])
        for entry in cur:
            if isinstance(entry, dict):
                name = str(entry.get("name", "") or "")
                db_path = str(entry.get("db_path", "") or "")
                paths = list(entry.get("paths", []) or [])
                self._append_group(name, db_path, paths)

    def _append_group(self, name: str, db_path: str, paths: list) -> None:
        row = _GroupRow(
            name, db_path, paths, self._window,
            on_change=self._commit, on_remove=self._on_remove,
        )
        self._groups_layout.addWidget(row)
        self._group_widgets.append(row)

    def _on_add(self) -> None:
        self._append_group("", "", [])
        self._commit()

    def _on_remove(self, row: "_GroupRow") -> None:
        try:
            self._group_widgets.remove(row)
        except ValueError:
            pass
        row.setParent(None)
        row.deleteLater()
        self._commit()

    def _commit(self) -> None:
        out = []
        for g in self._group_widgets:
            name = g.name_text().strip()
            db_path = g.db_path_text().strip()
            paths = g.get_paths()
            if not name or not db_path or not paths:
                continue
            out.append({"name": name, "db_path": db_path, "paths": paths})
        patch_settings("docgraph.groups", out)

    def refresh(self) -> None:
        cur = list(get_path(read_settings(), "docgraph.groups", []) or [])
        cur_norm = [
            {
                "name": str(e.get("name", "") if isinstance(e, dict) else ""),
                "db_path": str(e.get("db_path", "") if isinstance(e, dict) else ""),
                "paths": list(e.get("paths", []) if isinstance(e, dict) else []),
            }
            for e in cur
        ]
        cur_norm = [e for e in cur_norm if e["name"] and e["db_path"] and e["paths"]]
        cur_view = [
            {"name": g.name_text().strip(), "db_path": g.db_path_text().strip(), "paths": g.get_paths()}
            for g in self._group_widgets
            if g.name_text().strip() and g.db_path_text().strip() and g.get_paths()
        ]
        if cur_norm != cur_view:
            self._rebuild()


class _GroupRow(QFrame):
    """Single group card — shows name, db_path, and member paths with watch toggles."""

    def __init__(self, name: str, db_path: str, paths: list, window, *, on_change, on_remove) -> None:
        super().__init__()
        self._window = window
        self._on_change = on_change
        self._on_remove = on_remove

        self.setStyleSheet(
            f"_GroupRow {{ background: {BG_ELEV}; border: 1px solid {BORDER}; border-radius: 6px; }}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(6)

        # ── Group header: name + db_path + status pills + remove ──
        header = QWidget()
        hh = QHBoxLayout(header)
        hh.setContentsMargins(0, 0, 0, 0)
        hh.setSpacing(6)

        self._name_edit = QLineEdit(name)
        self._name_edit.setPlaceholderText("Group name")
        self._name_edit.editingFinished.connect(self._on_change)
        self._name_edit.setMinimumWidth(100)
        self._name_edit.setMaximumWidth(150)
        hh.addWidget(self._name_edit)

        self._db_edit = QLineEdit(db_path)
        self._db_edit.setPlaceholderText("/path/.docgraph/db.kuzu")
        self._db_edit.editingFinished.connect(self._on_change)
        self._db_edit.setMinimumWidth(140)
        hh.addWidget(self._db_edit, 2)

        # Status pills for the group
        pills_w = QWidget()
        pl = QHBoxLayout(pills_w)
        pl.setContentsMargins(0, 0, 0, 0); pl.setSpacing(4)

        self._pill = QLabel("…")
        self._pill.setProperty("class", "stat_pill")
        self._pill.setMinimumWidth(0)
        self._pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pill.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._pill.setToolTip("Index status")
        pl.addWidget(self._pill, 1)

        self._wiki_pill = QLabel("…")
        self._wiki_pill.setProperty("class", "stat_pill")
        self._wiki_pill.setMinimumWidth(0)
        self._wiki_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._wiki_pill.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._wiki_pill.setToolTip("Wiki status")
        pl.addWidget(self._wiki_pill, 1)

        self._stats_chip = QLabel("—")
        self._stats_chip.setProperty("class", "stat_chip")
        self._stats_chip.setProperty("muted", "true")
        self._stats_chip.setMinimumWidth(0)
        self._stats_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stats_chip.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._stats_chip.setToolTip("Live counts (entities · edges). Auto-refreshed.")
        pl.addWidget(self._stats_chip, 1)

        hh.addWidget(pills_w, 2)
        self._stats_last_fetch: float = 0.0

        rm_btn = QPushButton("✕")
        rm_btn.setFlat(True)
        rm_btn.setFixedWidth(28)
        rm_btn.setStyleSheet(
            f"QPushButton {{ color: {FG_DIM}; border: none; background: transparent; }}"
            f" QPushButton:hover {{ color: #ff6b6b; }}"
        )
        rm_btn.clicked.connect(lambda: self._on_remove(self))
        hh.addWidget(rm_btn)

        outer.addWidget(header)

        # ── Member paths list ──
        members_label = QLabel("Member paths:")
        members_label.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        outer.addWidget(members_label)

        self._members_host = QWidget()
        self._members_layout = QVBoxLayout(self._members_host)
        self._members_layout.setContentsMargins(6, 0, 0, 0)
        self._members_layout.setSpacing(4)
        outer.addWidget(self._members_host)

        # ── Add member button ──
        add_member_btn = QPushButton("+ Add member")
        add_member_btn.setProperty("class", "secondary")
        add_member_btn.setMaximumWidth(120)
        add_member_btn.clicked.connect(self._on_add_member)
        outer.addWidget(add_member_btn)

        # ── Action buttons (Index, Wiki, Clear) ──
        actions_w = QWidget()
        actions_h = QHBoxLayout(actions_w)
        actions_h.setContentsMargins(0, 0, 0, 0)
        actions_h.setSpacing(6)

        self._index_btn = QPushButton("▶ Index")
        self._index_btn.setMinimumWidth(85)
        self._index_btn.clicked.connect(self._on_index)
        actions_h.addWidget(self._index_btn)

        self._wiki_btn = QPushButton("📋 Wiki")
        self._wiki_btn.setMinimumWidth(85)
        self._wiki_btn.clicked.connect(self._on_wiki)
        actions_h.addWidget(self._wiki_btn)

        self._clear_btn = QPushButton("🗑 Clear")
        self._clear_btn.setMinimumWidth(85)
        self._clear_btn.setProperty("class", "danger")
        self._clear_btn.clicked.connect(self._on_clear)
        actions_h.addWidget(self._clear_btn)

        actions_h.addStretch(1)

        # Watch toggle at the bottom of the group
        watch_lbl = QLabel("Watch")
        watch_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        actions_h.addWidget(watch_lbl)
        self._watch_toggle = Toggle()
        self._watch_toggle.toggled.connect(lambda _: self._on_change())
        actions_h.addWidget(self._watch_toggle)

        outer.addWidget(actions_w)

        # ── Line 3: paired progress bars (index left, wiki right) ──────
        self._line3 = QWidget()
        l3 = QHBoxLayout(self._line3)
        l3.setContentsMargins(0, 0, 0, 0); l3.setSpacing(8)

        def _mkbar(kind: str, idle_label: str) -> QProgressBar:
            bar = QProgressBar()
            bar.setProperty("kind", kind); bar.setProperty("state", "idle")
            bar.setRange(0, 100); bar.setValue(0); bar.setTextVisible(True)
            bar.setFormat(idle_label); bar.setAlignment(Qt.AlignmentFlag.AlignCenter); bar.setFixedHeight(20)
            return bar

        self._idx_bar = _mkbar("idx", "index · idle")
        l3.addWidget(self._idx_bar, 1)

        self._wiki_bar = _mkbar("wiki", "wiki · idle")
        l3.addWidget(self._wiki_bar, 1)

        outer.addWidget(self._line3)

        self._member_rows: list[_MemberRow] = []
        self._rebuild_members(paths)
        self.refresh_status()

    def name_text(self) -> str:
        return self._name_edit.text()

    def db_path_text(self) -> str:
        return self._db_edit.text()

    def get_paths(self) -> list:
        out = []
        for r in self._member_rows:
            path = r.path_text().strip()
            if path:
                out.append({"path": path, "watch": r.watch_state()})
        return out

    def _rebuild_members(self, paths: list) -> None:
        for w in self._member_rows:
            w.setParent(None)
            w.deleteLater()
        self._member_rows.clear()
        for path_entry in paths:
            if isinstance(path_entry, dict):
                path = str(path_entry.get("path", "") or "")
                watch = bool(path_entry.get("watch", False))
            else:
                path, watch = str(path_entry), False
            self._add_member_row(path, watch)

    def _add_member_row(self, path: str, watch: bool) -> None:
        row = _MemberRow(
            path, watch,
            on_change=self._on_change, on_remove=self._on_remove_member,
        )
        self._members_layout.addWidget(row)
        self._member_rows.append(row)

    def _on_add_member(self) -> None:
        self._add_member_row("", False)
        self._on_change()

    def _on_remove_member(self, row: "_MemberRow") -> None:
        try:
            self._member_rows.remove(row)
        except ValueError:
            pass
        row.setParent(None)
        row.deleteLater()
        self._on_change()

    def _on_index(self) -> None:
        name = self.name_text().strip()
        if not name:
            return
        async def _go():
            from docgraph.process import get_index
            await get_index().run(name, force=False)
            self.refresh_status()
        _run(self._window, _go)

    def _on_wiki(self) -> None:
        name = self.name_text().strip()
        if not name:
            return
        async def _go():
            from docgraph.process import get_wiki
            await get_wiki().run(name, force=False)
            self.refresh_status()
        _run(self._window, _go)

    def _on_clear(self) -> None:
        name = self.name_text().strip()
        if not name:
            return
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.warning(
            self, "Confirm Clear",
            f"Clear all data for group '{name}'? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        async def _go():
            from docgraph.process import clear_index
            ok, detail = await clear_index(name)
            if ok:
                try:
                    from docgraph import index_state, wiki_state, stats_state
                    index_state.clear(name)
                    wiki_state.clear(name)
                    stats_state.drop(name)
                except Exception:
                    pass
                self._stats_last_fetch = 0.0
            self.refresh_status()
        _run(self._window, _go)

    def refresh_status(self) -> None:
        name = self.name_text().strip()
        self._refresh_index_pill(name)
        self._refresh_wiki_pill(name)
        self._refresh_stats_chip(name)
        self._refresh_progress_bars(name)

    def _apply_bar_state(self, bar: QProgressBar, state: str) -> None:
        if bar.property("state") == state: return
        bar.setProperty("state", state)
        st = bar.style(); st.unpolish(bar); st.polish(bar)

    def _paint_bar(self, bar: QProgressBar, kind: str, ps: dict | None, running: bool, idle_label: str) -> None:
        if not running:
            self._apply_bar_state(bar, "idle")
            bar.setRange(0, 100); bar.setValue(0); bar.setFormat(idle_label)
            return
        self._apply_bar_state(bar, "run")
        phase = (ps or {}).get("phase") or "start"
        module = (ps or {}).get("module") or ""
        label, ord_, total_phases = _fmt_phase_label(kind, phase, module)
        cur = int((ps or {}).get("current") or 0)
        tot = int((ps or {}).get("total") or 0)
        if tot > 0:
            pct = max(0, min(100, int(cur * 100 / tot)))
            bar.setRange(0, 100); bar.setValue(pct)
            bar.setFormat(f"[{ord_}/{total_phases}] {label}  ·  {pct}%  ({_fmt_count(cur)}/{_fmt_count(tot)})")
        else:
            bar.setRange(0, 0); bar.setFormat(f"[{ord_}/{total_phases}] {label}  ·  …")

    def _refresh_progress_bars(self, name: str) -> None:
        try:
            from docgraph import progress_state
            from docgraph.process import get_index, get_wiki
            idx_running = bool(name) and get_index().current_path() == name
            wiki_running = bool(name) and get_wiki().current_path() == name
            idx_ps = progress_state.get(name, "index") if name else None
            wiki_ps = progress_state.get(name, "wiki") if name else None
        except Exception:
            idx_running = wiki_running = False
            idx_ps = wiki_ps = None
        self._paint_bar(self._idx_bar, "index", idx_ps, idx_running, "index · idle")
        self._paint_bar(self._wiki_bar, "wiki", wiki_ps, wiki_running, "wiki · idle")

    def _refresh_index_pill(self, name: str) -> None:
        try:
            from docgraph import index_state
            from docgraph.process import get_index
            s = index_state.get(name) if name else None
            running_path = get_index().current_path()
        except Exception:
            s, running_path = None, None
        if name and running_path == name:
            self._pill.setText("running…")
            self._pill.setStyleSheet(f"color: {WARN};")
            self._index_btn.setEnabled(False)
            return
        self._index_btn.setEnabled(True)
        if not s:
            self._pill.setText("not indexed")
            self._pill.setStyleSheet(f"color: {FG_MUTE};")
            return
        ago = _format_ago(s.get("last_run", 0.0))
        status = s.get("last_status", "?")
        full = " · force" if s.get("last_was_full") else ""
        text = f"{ago} · {status}{full}"
        self._pill.setStyleSheet(f"color: {OK if status == 'ok' else ERR if status == 'failed' else WARN if status == 'running' else FG_MUTE};")
        self._pill.setText(text)

    def _refresh_wiki_pill(self, name: str) -> None:
        try:
            from docgraph import wiki_state
            from docgraph.process import get_wiki
            s = wiki_state.get(name) if name else None
            running_path = get_wiki().current_path()
        except Exception:
            s, running_path = None, None
        if name and running_path == name:
            self._wiki_pill.setText("wiki running…")
            self._wiki_pill.setStyleSheet(f"color: {WARN};")
            self._wiki_btn.setEnabled(False)
            return
        self._wiki_btn.setEnabled(True)
        if not s:
            self._wiki_pill.setText("no wiki")
            self._wiki_pill.setStyleSheet(f"color: {FG_MUTE};")
            return
        ago = _format_ago(s.get("last_run", 0.0))
        status = s.get("last_status", "?")
        full = " · force" if s.get("last_was_full") else ""
        text = f"wiki {ago} · {status}{full}"
        self._wiki_pill.setStyleSheet(f"color: {OK if status == 'ok' else ERR if status == 'failed' else WARN if status == 'running' else FG_MUTE};")
        self._wiki_pill.setText(text)

    def _refresh_stats_chip(self, name: str) -> None:
        try:
            from docgraph import stats_state
            from docgraph.process import get_host
            host_alive = bool(get_host().alive())
        except Exception:
            host_alive = False
            stats_state = None  # type: ignore

        snap = stats_state.get(name) if (stats_state and name) else None
        muted = "true"
        if snap:
            ents = sum(int(snap.get(k, 0) or 0) for k in ("File", "Module", "Class", "Function", "Variable"))
            docs = int(snap.get("Doc", 0) or 0)
            edges = int(snap.get("edges") or 0)
            text = f"{_fmt_count(ents)} ents · {_fmt_count(edges)} edges"
            if docs > 0: text += f" · {_fmt_count(docs)} wiki"
            self._stats_chip.setText(text)
            muted = "false"
        else:
            self._stats_chip.setText("— · —" if host_alive else "host offline")
            
        if self._stats_chip.property("muted") != muted:
            self._stats_chip.setProperty("muted", muted)
            st = self._stats_chip.style()
            st.unpolish(self._stats_chip); st.polish(self._stats_chip)

        if not name or not host_alive or stats_state is None:
            return
        import time as _time
        now = _time.time()
        if now - self._stats_last_fetch < 10.0:
            return
        if stats_state.age(name) < 10.0:
            self._stats_last_fetch = now
            return
        if not stats_state.mark_in_flight(name):
            return
        self._stats_last_fetch = now

        async def _go():
            try:
                from docgraph.process import fetch_stats_dict
                data = await fetch_stats_dict(name)
                if data is not None:
                    stats_state.set(name, data)
            finally:
                stats_state.clear_in_flight(name)
        _run(self._window, _go)


class _MemberRow(QFrame):
    """Single member path row — path field + watch toggle + remove button."""

    def __init__(self, path: str, watch: bool, *, on_change, on_remove) -> None:
        super().__init__()
        self._on_change = on_change
        self._on_remove = on_remove

        self.setStyleSheet(
            f"_MemberRow {{ background: rgba(255,255,255,0.03); border: 1px solid {BORDER}; border-radius: 4px; }}"
        )
        h = QHBoxLayout(self)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(6)

        self._path_edit = QLineEdit(path)
        self._path_edit.setPlaceholderText("/path/to/src")
        self._path_edit.editingFinished.connect(self._on_change)
        self._path_edit.setMinimumWidth(140)
        h.addWidget(self._path_edit, 1)

        self._watch_toggle = Toggle()
        self._watch_toggle.setChecked(watch)
        self._watch_toggle.stateChanged.connect(self._on_change)
        self._watch_toggle.setFixedWidth(36)
        watch_label = QLabel("Watch")
        watch_label.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        watch_w = QWidget()
        wl = QHBoxLayout(watch_w)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(4)
        wl.addWidget(watch_label)
        wl.addWidget(self._watch_toggle)
        h.addWidget(watch_w)

        rm_btn = QPushButton("✕")
        rm_btn.setFlat(True)
        rm_btn.setFixedWidth(24)
        rm_btn.setStyleSheet(
            f"QPushButton {{ color: {FG_DIM}; border: none; background: transparent; font-size: 11px; }}"
            f" QPushButton:hover {{ color: #ff6b6b; }}"
        )
        rm_btn.clicked.connect(lambda: self._on_remove(self))
        h.addWidget(rm_btn)

    def path_text(self) -> str:
        return self._path_edit.text()

    def watch_state(self) -> bool:
        return self._watch_toggle.isChecked()


def _build_host_card(window) -> tuple[QFrame, Callable[[], None]]:
    card, body = _card(
        "Host",
        "One docgraph host process serves every configured root. "
        "Restart to apply settings changes.",
    )

    body.addWidget(_toggle_row("docgraph.host.enabled", "Enabled",
                                "Off = host is stopped right now."))
    body.addWidget(_toggle_row("docgraph.host.auto_start", "Auto-start",
                                "Start the host when telecode boots."))
    body.addWidget(_toggle_row("docgraph.host.auto_restart", "Auto-restart",
                                "Re-spawn on unexpected exit."))
    body.addWidget(_line_row("docgraph.host.host", "Bind Host", "127.0.0.1",
                              cli="--host"))
    body.addWidget(_number_row("docgraph.host.port", "Bind Port", 1024, 65535, 1, 0,
                                cli="--port"))
    body.addWidget(_number_row("docgraph.host.debounce", "Watcher debounce",
                                50, 5000, 50, 0, "ms",
                                "Default 500. Only used with watched roots.",
                                cli="--debounce"))

    actions = QWidget()
    ar = QHBoxLayout(actions)
    ar.setContentsMargins(0, 0, 0, 0); ar.setSpacing(8)
    start_btn = QPushButton("▶ Start"); start_btn.setProperty("class", "primary")
    stop_btn  = QPushButton("Stop");    stop_btn.setProperty("class", "danger")
    restart_btn = QPushButton("Restart")
    ar.addWidget(start_btn); ar.addWidget(stop_btn); ar.addWidget(restart_btn)
    ar.addStretch(1)
    body.addWidget(_row(row_label("Actions"), actions))

    def refresh_status() -> None:
        # Gate Start/Stop/Restart on actual liveness so the user can't
        # double-click Start on an already-running host (or Stop one
        # that's already dead). Read straight from the supervisor — the
        # `enabled` setting is just sticky intent, not real state.
        try:
            from docgraph.process import status_snapshot
            alive = bool((status_snapshot().get("host") or {}).get("alive"))
        except Exception:
            alive = False
        start_btn.setEnabled(not alive)
        stop_btn.setEnabled(alive)
        restart_btn.setEnabled(alive)

    def _on_start():
        async def _go():
            from docgraph.process import get_host
            await get_host().start()
        _run(window, _go)

    def _on_stop():
        async def _go():
            from docgraph.process import get_host
            await get_host().stop()
        _run(window, _go)

    def _on_restart():
        async def _go():
            from docgraph.process import get_host
            sup = get_host()
            await sup.stop()
            await sup.start()
        _run(window, _go)

    start_btn.clicked.connect(_on_start)
    stop_btn.clicked.connect(_on_stop)
    restart_btn.clicked.connect(_on_restart)

    refresh_status()
    return card, refresh_status


def _host_status_text() -> tuple[bool, str]:
    try:
        from docgraph.process import status_snapshot
        s = status_snapshot().get("host", {}) or {}
    except Exception as exc:
        return False, f"err: {exc}"
    if s.get("alive"):
        pid = s.get("pid"); port = s.get("port")
        bridged = s.get("bridged") or 0
        bits = []
        if pid:     bits.append(f"pid={pid}")
        if port:    bits.append(f"port={port}")
        if bridged: bits.append(f"bridged={bridged}")
        return True, "alive  " + "  ".join(bits)
    err = s.get("last_error")
    if err:
        return False, f"failed: {err}"
    return False, "stopped" + (" (enabled)" if s.get("enabled") else "")


# ── Roots card (multi-row table) ────────────────────────────────────────

def _build_roots_card(window) -> tuple[QFrame, Callable[[], None]]:
    card, body = _card(
        "Roots",
        "Repos registered with the host. Watch flips need a host restart.",
    )

    # Build the master Full toggle FIRST so its state can be threaded
    # into per-row Index/Wiki buttons via a getter.
    from tray.qt_widgets import Toggle as _Toggle
    all_force = _Toggle()
    all_force.setToolTip(
        "On  = docgraph index --full / docgraph wiki --force\n"
        "Off = incremental index / resumable wiki"
    )

    paths_widget = _RootsTable(window, force_getter=all_force.isChecked)
    body.addWidget(paths_widget)

    body.addWidget(_section_header("Global actions"))

    body.addWidget(_row(row_label(
        "Full rebuild",
        "On = --full / --force. Off = incremental."),
        _wrap_align(all_force, Qt.AlignmentFlag.AlignLeft)))

    # Index all row: ▶ + ✕ cancel + status pill.
    run_all_btn = QPushButton("▶ Index all")
    run_all_btn.setProperty("class", "primary")
    run_all_btn.setToolTip("Index every configured root.")
    cancel_btn = QPushButton("✕")
    cancel_btn.setProperty("class", "danger")
    cancel_btn.setFixedWidth(28)
    cancel_btn.setToolTip("Cancel the running index pass.")
    status_lbl, refresh_status = _status_pill(_index_status_text)

    idx_w = QWidget()
    il = QHBoxLayout(idx_w); il.setContentsMargins(0, 0, 0, 0); il.setSpacing(8)
    il.addWidget(run_all_btn); il.addWidget(cancel_btn)
    il.addWidget(status_lbl, 0); il.addStretch(1)
    body.addWidget(_row(row_label("Index all roots"), idx_w))

    # Build wikis row: ▶ + ✕ cancel + status pill.
    run_all_wiki_btn = QPushButton("▶ Build wikis")
    run_all_wiki_btn.setProperty("class", "primary")
    run_all_wiki_btn.setToolTip("Build the wiki for every configured root.")
    cancel_wiki_btn = QPushButton("✕")
    cancel_wiki_btn.setProperty("class", "danger")
    cancel_wiki_btn.setFixedWidth(28)
    cancel_wiki_btn.setToolTip("Cancel the running wiki build.")
    wiki_status_lbl, refresh_wiki_status = _status_pill(_wiki_status_text)

    wiki_w = QWidget()
    wl = QHBoxLayout(wiki_w); wl.setContentsMargins(0, 0, 0, 0); wl.setSpacing(8)
    wl.addWidget(run_all_wiki_btn); wl.addWidget(cancel_wiki_btn)
    wl.addWidget(wiki_status_lbl, 0); wl.addStretch(1)
    body.addWidget(_row(row_label("Build wikis for all roots"), wiki_w))

    def _all():
        async def _go():
            from docgraph.process import get_index
            await get_index().run_all(force=bool(all_force.isChecked()))
        _run(window, _go)

    def _on_cancel():
        async def _go():
            from docgraph.process import get_index
            await get_index().cancel()
        _run(window, _go)

    def _all_wiki():
        async def _go():
            from docgraph.process import get_wiki
            await get_wiki().run_all(force=bool(all_force.isChecked()))
        _run(window, _go)

    def _on_cancel_wiki():
        async def _go():
            from docgraph.process import get_wiki
            await get_wiki().cancel()
        _run(window, _go)

    run_all_btn.clicked.connect(_all)
    cancel_btn.clicked.connect(_on_cancel)
    run_all_wiki_btn.clicked.connect(_all_wiki)
    cancel_wiki_btn.clicked.connect(_on_cancel_wiki)

    def refresh():
        refresh_status()
        refresh_wiki_status()
        paths_widget.refresh()

    return card, refresh


def _index_status_text() -> tuple[bool, str]:
    try:
        from docgraph.process import get_index
        s = get_index().status()
    except Exception as exc:
        return False, f"err: {exc}"
    if s["alive"]:
        what = "full" if s.get("current_force") else "incremental"
        return True, f"running · {s.get('current_path') or '?'} ({what})"
    return False, "idle"


def _wiki_status_text() -> tuple[bool, str]:
    try:
        from docgraph.process import get_wiki
        s = get_wiki().status()
    except Exception as exc:
        return False, f"err: {exc}"
    if s["alive"]:
        what = "force" if s.get("current_force") else "resumable"
        return True, f"running · {s.get('current_path') or '?'} ({what})"
    return False, "idle"


class _RootsTable(QWidget):
    """Editor for `docgraph.roots[]` (`{path, watch}` entries).

    Each row: editable path · ▶ Index · Watch toggle · status pill · ✕ remove.
    Persists to `docgraph.roots` on every edit.

    `force_getter` is a callable returning a bool — read at click time so
    flipping the master Full toggle takes effect immediately on the next
    per-row Index, without rebuilding the table.
    """

    def __init__(self, window, *, force_getter: Callable[[], bool] | None = None) -> None:
        super().__init__()
        self._window = window
        self._force_getter = force_getter or (lambda: False)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        v.addWidget(self._rows_host)

        add_w = QWidget()
        add_l = QHBoxLayout(add_w)
        add_l.setContentsMargins(0, 0, 0, 0)
        add_btn = QPushButton("+ Add root")
        add_btn.setProperty("class", "primary")
        add_btn.setMaximumWidth(140)
        add_btn.clicked.connect(self._on_add)
        add_l.addWidget(add_btn)
        add_l.addStretch(1)
        v.addWidget(add_w)

        self._row_widgets: list[_RootRow] = []
        self._rebuild()

    def _rebuild(self) -> None:
        for w in self._row_widgets:
            w.setParent(None)
            w.deleteLater()
        self._row_widgets.clear()
        cur = list(get_path(read_settings(), "docgraph.roots", []) or [])
        for entry in cur:
            if isinstance(entry, dict):
                path = str(entry.get("path", "") or "")
                watch = bool(entry.get("watch", False))
            else:
                path, watch = str(entry), False
            self._append_row(path, watch)

    def _append_row(self, path: str, watch: bool) -> None:
        row = _RootRow(
            path, watch, self._window,
            on_change=self._commit, on_remove=self._on_remove,
            force_getter=self._force_getter,
        )
        self._rows_layout.addWidget(row)
        self._row_widgets.append(row)

    def _on_add(self) -> None:
        self._append_row("", False)
        self._commit()

    def _on_remove(self, row: "_RootRow") -> None:
        try:
            self._row_widgets.remove(row)
        except ValueError:
            pass
        row.setParent(None)
        row.deleteLater()
        self._commit()

    def _commit(self) -> None:
        out = []
        for r in self._row_widgets:
            path = r.text().strip()
            if not path:
                continue
            out.append({"path": path, "watch": r.watch_state()})
        patch_settings("docgraph.roots", out)

    def refresh(self) -> None:
        cur = list(get_path(read_settings(), "docgraph.roots", []) or [])
        cur_norm = [
            {"path": str(e.get("path", "") if isinstance(e, dict) else e),
             "watch": bool(e.get("watch", False) if isinstance(e, dict) else False)}
            for e in cur
        ]
        cur_norm = [e for e in cur_norm if e["path"]]
        cur_view = [{"path": r.text().strip(), "watch": r.watch_state()}
                    for r in self._row_widgets if r.text().strip()]
        if cur_norm != cur_view:
            self._rebuild()
        for r in self._row_widgets:
            r.refresh_state()


class _RootRow(QFrame):
    def __init__(self, path: str, watch: bool, window, *, on_change, on_remove,
                 force_getter: Callable[[], bool] | None = None) -> None:
        super().__init__()
        self._window = window
        self._on_change = on_change
        self._on_remove = on_remove
        self._force_getter = force_getter or (lambda: False)

        self.setStyleSheet(
            f"_RootRow {{ background: {BG_ELEV}; border: 1px solid {BORDER}; border-radius: 6px; }}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(6)

        # ── Line 1: path (75%) + status pills (25%) + remove ✕ ────────
        line1 = QWidget()
        h = QHBoxLayout(line1)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)

        self._edit = QLineEdit(path)
        self._edit.setPlaceholderText("/path/to/repo")
        self._edit.editingFinished.connect(self._on_edit_done)
        self._edit.setMinimumWidth(140)
        self._edit.setToolTip(path or "/path/to/repo")
        # 3 : 1 split → path is the 75%, the status block is the 25%.
        h.addWidget(self._edit, 3)

        pills_w = QWidget()
        pl = QHBoxLayout(pills_w)
        pl.setContentsMargins(0, 0, 0, 0); pl.setSpacing(4)

        self._pill = QLabel("…")
        self._pill.setProperty("class", "stat_pill")
        self._pill.setMinimumWidth(0)
        self._pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pill.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._pill.setToolTip("Index status")
        pl.addWidget(self._pill, 1)

        self._wiki_pill = QLabel("…")
        self._wiki_pill.setProperty("class", "stat_pill")
        self._wiki_pill.setMinimumWidth(0)
        self._wiki_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._wiki_pill.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._wiki_pill.setToolTip("Wiki status")
        pl.addWidget(self._wiki_pill, 1)

        # Auto-refreshing stats chip — entity / edge counts pulled from
        # /api/stats every ~10s while the host is alive. Replaces the
        # old 📊 Stats button.
        self._stats_chip = QLabel("—")
        self._stats_chip.setProperty("class", "stat_chip")
        self._stats_chip.setProperty("muted", "true")
        self._stats_chip.setMinimumWidth(0)
        self._stats_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stats_chip.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._stats_chip.setToolTip("Live counts (entities · edges). Auto-refreshed.")
        pl.addWidget(self._stats_chip, 1)

        h.addWidget(pills_w, 1)
        # Tracks the last time we fired an /api/stats fetch for this row
        # so the 1s tray tick doesn't hammer the host.
        self._stats_last_fetch: float = 0.0

        rm_btn = QPushButton("✕")
        rm_btn.setFlat(True)
        rm_btn.setFixedWidth(28)
        rm_btn.setStyleSheet(
            f"QPushButton {{ color: {FG_DIM}; border: none; background: transparent; }}"
            f" QPushButton:hover {{ color: #ff6b6b; }}"
        )
        rm_btn.clicked.connect(lambda: self._on_remove(self))
        h.addWidget(rm_btn)

        outer.addWidget(line1)

        # ── Line 2: labeled action buttons + Watch toggle ─────────────
        line2 = QWidget()
        bh = QHBoxLayout(line2)
        bh.setContentsMargins(0, 0, 0, 0)
        bh.setSpacing(6)

        self._index_btn = QPushButton("▶ Index")
        self._index_btn.setMinimumWidth(85)
        self._index_btn.setToolTip(
            "Index this root.\n"
            "POST /api/admin/index?root=<slug>  if host is alive,\n"
            "else falls back to:  docgraph index <path>"
        )
        self._index_btn.clicked.connect(self._trigger_index)
        bh.addWidget(self._index_btn)

        self._wiki_btn = QPushButton("📋 Wiki")
        self._wiki_btn.setMinimumWidth(85)
        self._wiki_btn.setToolTip(
            "Build the wiki for this root.\n"
            "POST /api/wiki/build?root=<slug>  if host is alive,\n"
            "else falls back to:  docgraph wiki <path>\n"
            "Full toggle on = --force (rebuild every page)"
        )
        self._wiki_btn.clicked.connect(self._trigger_wiki)
        bh.addWidget(self._wiki_btn)

        self._clear_btn = QPushButton("🗑 Clear")
        self._clear_btn.setMinimumWidth(85)
        self._clear_btn.setProperty("class", "danger")
        self._clear_btn.setToolTip(
            "Clear this root's index.\n"
            "POST /api/admin/clear?root=<slug> — wipe the index, cache, and\n"
            "wiki for this root. Confirmation required. Host stays alive;\n"
            "the workspace re-opens its read-only handle once the wipe is done."
        )
        self._clear_btn.clicked.connect(self._trigger_clear)
        bh.addWidget(self._clear_btn)

        bh.addStretch(1)

        watch_lbl = QLabel("Watch")
        watch_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 11.5px;")
        bh.addWidget(watch_lbl)
        self._watch = Toggle()
        self._watch.setChecked(bool(watch))
        self._watch.toggled.connect(self._on_watch_toggled)
        self._watch.setToolTip(
            "Watch — auto-reindex on file changes.\n"
            "Forwards as `docgraph host --watch <path>`. "
            "Restart the host to apply a flipped flag."
        )
        bh.addWidget(self._watch)

        outer.addWidget(line2)

        # ── Line 3: paired progress bars (index left, wiki right).
        # Always visible so the row keeps a consistent 3-line height;
        # bars sit in the "idle" state when nothing is running.
        self._line3 = QWidget()
        l3 = QHBoxLayout(self._line3)
        l3.setContentsMargins(0, 0, 0, 0)
        l3.setSpacing(8)

        def _mkbar(kind: str, idle_label: str) -> QProgressBar:
            bar = QProgressBar()
            bar.setProperty("kind", kind)
            bar.setProperty("state", "idle")
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(True)
            bar.setFormat(idle_label)
            bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bar.setFixedHeight(20)
            return bar

        self._idx_bar = _mkbar("idx", "index · idle")
        l3.addWidget(self._idx_bar, 1)

        self._wiki_bar = _mkbar("wiki", "wiki · idle")
        l3.addWidget(self._wiki_bar, 1)

        outer.addWidget(self._line3)

        self.refresh_state()

    def text(self) -> str:
        return self._edit.text()

    def watch_state(self) -> bool:
        return bool(self._watch.isChecked())

    def _on_edit_done(self) -> None:
        self._edit.setToolTip(self._edit.text() or "/path/to/repo")
        self._on_change()
        self.refresh_state()

    def _on_watch_toggled(self, _checked: bool) -> None:
        self._on_change()

    def _trigger_index(self) -> None:
        path = self.text().strip()
        if not path:
            return
        force = bool(self._force_getter())
        async def _go():
            from docgraph.process import get_index
            await get_index().run(path, force=force)
        _run(self._window, _go)

    def _trigger_wiki(self) -> None:
        path = self.text().strip()
        if not path:
            return
        force = bool(self._force_getter())
        async def _go():
            from docgraph.process import get_wiki
            await get_wiki().run(path, force=force)
        _run(self._window, _go)

    def _trigger_clear(self) -> None:
        path = self.text().strip()
        if not path:
            return
        from PySide6.QtWidgets import QMessageBox
        confirm = QMessageBox.question(
            self,
            "Clear index?",
            f"Wipe the index, cache, and wiki under\n  {path}/.docgraph/\n\n"
            "Re-indexing will cost a full rebuild. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        async def _go():
            from docgraph.process import clear_index
            ok, detail = await clear_index(path)
            if ok:
                # Drop telecode's recorded run history so the pills don't
                # keep advertising the pre-clear state ("indexed", "wiki Ns ago").
                try:
                    from docgraph import index_state, wiki_state, stats_state
                    index_state.clear(path)
                    wiki_state.clear(path)
                    stats_state.drop(path)
                except Exception:
                    pass
                # Force the next refresh tick to actually fetch — otherwise
                # the per-row 10s throttle would keep the chip stuck on the
                # pre-clear render.
                self._stats_last_fetch = 0.0
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QMessageBox as _MB
            kind = _MB.information if ok else _MB.warning
            title = "Cleared" if ok else "Clear failed"
            def _show():
                kind(self, title, detail)
                if ok:
                    self.refresh_state()
            QTimer.singleShot(0, _show)
        _run(self._window, _go)

    def refresh_state(self) -> None:
        path = self.text().strip()
        self._refresh_index_pill(path)
        self._refresh_wiki_pill(path)
        self._refresh_stats_chip(path)
        self._refresh_progress_bars(path)

    def _apply_bar_state(self, bar: QProgressBar, state: str) -> None:
        """Flip the bar between 'idle' / 'run' so the QSS picks up the
        right styling (dashed border vs. gradient chunk). Qt only re-runs
        the stylesheet on a property change if you nudge it via
        unpolish + polish."""
        if bar.property("state") == state:
            return
        bar.setProperty("state", state)
        st = bar.style()
        st.unpolish(bar)
        st.polish(bar)

    def _paint_bar(self, bar: QProgressBar, kind: str, ps: dict | None,
                   running: bool, idle_label: str) -> None:
        if not running:
            self._apply_bar_state(bar, "idle")
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFormat(idle_label)
            return

        self._apply_bar_state(bar, "run")
        phase = (ps or {}).get("phase") or "start"
        module = (ps or {}).get("module") or ""
        label, ord_, total_phases = _fmt_phase_label(kind, phase, module)
        cur = int((ps or {}).get("current") or 0)
        tot = int((ps or {}).get("total") or 0)

        if tot > 0:
            pct = max(0, min(100, int(cur * 100 / tot)))
            bar.setRange(0, 100)
            bar.setValue(pct)
            bar.setFormat(
                f"[{ord_}/{total_phases}] {label}  ·  {pct}%  "
                f"({_fmt_count(cur)}/{_fmt_count(tot)})"
            )
        else:
            # Indeterminate — Qt renders a marquee chunk when range is 0/0.
            bar.setRange(0, 0)
            bar.setFormat(f"[{ord_}/{total_phases}] {label}  ·  …")

    def _refresh_progress_bars(self, path: str) -> None:
        """Paint the live SSE progress into the per-row QProgressBars."""
        try:
            from docgraph import progress_state
            from docgraph.process import get_index, get_wiki
            idx_running = bool(path) and get_index().current_path() == path
            wiki_running = bool(path) and get_wiki().current_path() == path
            idx_ps = progress_state.get(path, "index") if path else None
            wiki_ps = progress_state.get(path, "wiki") if path else None
        except Exception:
            idx_running = wiki_running = False
            idx_ps = wiki_ps = None

        self._paint_bar(self._idx_bar, "index", idx_ps, idx_running, "index · idle")
        self._paint_bar(self._wiki_bar, "wiki", wiki_ps, wiki_running, "wiki · idle")

    def _refresh_stats_chip(self, path: str) -> None:
        """Display live entity/edge/wiki counts and trigger a background
        refresh when the cached snapshot is older than 10s. Cheap when
        the host is alive (one /api/stats roundtrip), no-op otherwise."""
        try:
            from docgraph import stats_state
            from docgraph.process import get_host
            host_alive = bool(get_host().alive())
        except Exception:
            host_alive = False
            stats_state = None  # type: ignore

        # Render whatever we have cached.
        snap = stats_state.get(path) if (stats_state and path) else None
        muted = "true"
        if snap:
            ents = sum(int(snap.get(k, 0) or 0)
                       for k in ("File", "Module", "Class", "Function", "Variable"))
            docs = int(snap.get("Doc", 0) or 0)
            # Server-side total (preferred). Fall back to summing edges_by_type
            # for older hosts that don't yet emit `edges`.
            edges = snap.get("edges")
            if edges is None:
                edges = sum(int(v or 0)
                            for v in (snap.get("edges_by_type") or {}).values())
            edges = int(edges or 0)
            
            text = f"{_fmt_count(ents)} ents · {_fmt_count(edges)} edges"
            if docs > 0:
                text += f" · {_fmt_count(docs)} wiki"
            self._stats_chip.setText(text)
            
            tip_lines = [f"{path or '(no path)'}", ""]
            for label in ("File", "Module", "Class", "Function", "Variable", "Doc"):
                val = snap.get(label, 0)
                display_label = "Wiki Pages" if label == "Doc" else label
                tip_lines.append(f"  {display_label:<12} {val}")
            
            top_edges = sorted(
                ((k, int(v or 0))
                 for k, v in (snap.get("edges_by_type") or {}).items()),
                key=lambda kv: kv[1], reverse=True,
            )[:6]
            if top_edges:
                tip_lines.append("")
                for k, v in top_edges:
                    tip_lines.append(f"  {k:<14} {v}")
            self._stats_chip.setToolTip("\n".join(tip_lines))
            muted = "false"
        else:
            self._stats_chip.setText("— · —" if host_alive else "host offline")
            self._stats_chip.setToolTip(
                "Live counts auto-refresh while the host is alive."
                if host_alive else
                "Start the docgraph host to see live counts."
            )
        if self._stats_chip.property("muted") != muted:
            self._stats_chip.setProperty("muted", muted)
            st = self._stats_chip.style()
            st.unpolish(self._stats_chip); st.polish(self._stats_chip)

        # Maybe schedule a fresh fetch. Skip if no path, no host, or
        # we already have a fresh snapshot. Throttle per-row at 10s and
        # use stats_state's in-flight flag to dedupe across rows pointing
        # at the same path.
        if not path or not host_alive or stats_state is None:
            return
        import time as _time
        now = _time.time()
        if now - self._stats_last_fetch < 10.0:
            return
        if stats_state.age(path) < 10.0:
            self._stats_last_fetch = now
            return
        if not stats_state.mark_in_flight(path):
            return
        self._stats_last_fetch = now

        async def _go():
            try:
                from docgraph.process import fetch_stats_dict
                data = await fetch_stats_dict(path)
                if data is not None:
                    stats_state.set(path, data)
            finally:
                stats_state.clear_in_flight(path)
        _run(self._window, _go)

    def _refresh_index_pill(self, path: str) -> None:
        try:
            from docgraph import index_state
            from docgraph.process import get_index
            s = index_state.get(path) if path else None
            running_path = get_index().current_path()
        except Exception:
            s, running_path = None, None
        if path and running_path == path:
            self._pill.setText("running…")
            self._pill.setStyleSheet(f"color: {WARN};")
            self._index_btn.setEnabled(False)
            return
        self._index_btn.setEnabled(True)
        if not s:
            already = _path_already_indexed(path)
            if already:
                self._pill.setText("indexed (on disk)")
                self._pill.setStyleSheet(f"color: {OK};")
            else:
                self._pill.setText("not indexed")
                self._pill.setStyleSheet(f"color: {FG_MUTE};")
            return
        ago = _format_ago(s.get("last_run", 0.0))
        status = s.get("last_status", "?")
        full = " · force" if s.get("last_was_full") else ""
        text = f"{ago} · {status}{full}"
        if status == "ok":
            self._pill.setStyleSheet(f"color: {OK};")
        elif status == "failed":
            self._pill.setStyleSheet(f"color: {ERR};")
        elif status == "running":
            self._pill.setStyleSheet(f"color: {WARN};")
        else:
            self._pill.setStyleSheet(f"color: {FG_MUTE};")
        self._pill.setText(text)

    def _refresh_wiki_pill(self, path: str) -> None:
        try:
            from docgraph import wiki_state
            from docgraph.process import get_wiki
            s = wiki_state.get(path) if path else None
            running_path = get_wiki().current_path()
        except Exception:
            s, running_path = None, None
        if path and running_path == path:
            self._wiki_pill.setText("wiki running…")
            self._wiki_pill.setStyleSheet(f"color: {WARN};")
            self._wiki_btn.setEnabled(False)
            return
        self._wiki_btn.setEnabled(True)
        if not s:
            already = _path_already_wiki_built(path)
            if already:
                self._wiki_pill.setText("wiki on disk")
                self._wiki_pill.setStyleSheet(f"color: {OK};")
            else:
                self._wiki_pill.setText("no wiki")
                self._wiki_pill.setStyleSheet(f"color: {FG_MUTE};")
            return
        ago = _format_ago(s.get("last_run", 0.0))
        status = s.get("last_status", "?")
        full = " · force" if s.get("last_was_full") else ""
        text = f"wiki {ago} · {status}{full}"
        if status == "ok":
            self._wiki_pill.setStyleSheet(f"color: {OK};")
        elif status == "failed":
            self._wiki_pill.setStyleSheet(f"color: {ERR};")
        elif status == "running":
            self._wiki_pill.setStyleSheet(f"color: {WARN};")
        else:
            self._wiki_pill.setStyleSheet(f"color: {FG_MUTE};")
        self._wiki_pill.setText(text)


# ── LLM card ─────────────────────────────────────────────────────────────

def _build_llm_card(window) -> tuple[QFrame, Callable[[], None] | None]:
    card, body = _card(
        "LLM augmentation",
        "Optional local LLM for index docstrings + wiki pages. The model "
        "field alone does NOT enable either feature — toggle each on below.",
    )
    body.addWidget(_toggle_row("docgraph.llm.docstrings", "Use LLM for docstrings",
                                "Generate one-sentence summaries during "
                                "indexing for entities with no native docstring. "
                                "Off by default.",
                                cli="--llm-docstrings"))
    body.addWidget(_toggle_row("docgraph.llm.wiki", "Use LLM for wiki",
                                "When off, wiki pages render the fact-sheet "
                                "fallback even if a model is configured.",
                                cli="--llm-wiki"))
    body.addWidget(_line_row("docgraph.llm.model", "Model",
                              "qwen3.6-35b",
                              "LLM id used by both features above.",
                              cli="--llm-model"))
    body.addWidget(_line_row("docgraph.llm.host", "Host", "localhost",
                              cli="--llm-host"))
    body.addWidget(_number_row("docgraph.llm.port", "Port", 1, 65535, 1, 0,
                                cli="--llm-port"))
    body.addWidget(_enum_row_strs(
        "docgraph.llm.format", "Format",
        [("OpenAI-compatible", "openai"), ("Anthropic-compatible", "anthropic")],
    ))
    body.addWidget(_number_row("docgraph.llm.max_tokens", "Index Max Tokens",
                                10, 4096, 50, 0, "",
                                "Default 150.",
                                cli="--llm-max-tokens"))
    body.addWidget(_number_row("docgraph.llm.max_tokens_wiki", "Wiki Max Tokens",
                                256, 32768, 256, 0, "",
                                "Default 4096.",
                                cli="--llm-max-tokens-wiki"))
    body.addWidget(_number_row("docgraph.llm.max_tokens_chat", "Chat Max Tokens",
                                0, 32768, 256, 0, "",
                                "Right-panel Chat tab cap. 0 = unlimited "
                                "(model writes until done on OpenAI-compatible "
                                "servers).",
                                cli="--llm-max-tokens-chat"))
    body.addWidget(_line_row("docgraph.llm.api_key", "API Key", "",
                              "Forwarded as Authorization / x-api-key per "
                              "format. Leave blank for unauthenticated local "
                              "servers (LM Studio / llama.cpp / Ollama).",
                              cli="--llm-api-key"))
    body.addWidget(_number_row("docgraph.llm.timeout", "Timeout (s)",
                                5, 600, 5, 0, "s",
                                "Per-request HTTP timeout. Wiki page generation "
                                "on big modules can take 30s+ on local LLMs.",
                                cli="--llm-timeout"))
    body.addWidget(_number_row("docgraph.wiki.depth", "Wiki folder depth",
                                1, 32, 1, 0, "",
                                "Levels deep to bucket files. 1 = top-level only, 12 = leaf folders.",
                                cli="--wiki-depth"))
    return card, None


# ── Embeddings card ─────────────────────────────────────────────────────

# ── External Docs (Cursor @Docs parity) ────────────────────────────────

def _build_docs_card(window) -> tuple[QFrame, Callable[[], None]]:
    """Per-root external doc ingestion. Adds, lists, and removes Doc nodes
    via the host's /api/docs/* endpoints. Picks which root to operate on
    using the same closed-enum resolver the rest of the host uses."""
    from PySide6.QtWidgets import (
        QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    )

    card, body = _card(
        "External docs",
        "Cursor @Docs parity. Per-root Doc-node ingestion.",
    )

    picker = QComboBox()
    picker.setMinimumWidth(0)
    refresh_btn = QPushButton("Refresh")
    refresh_btn.setProperty("class", "ghost")
    refresh_btn.setMaximumWidth(100)
    picker_w = QWidget()
    pl = QHBoxLayout(picker_w); pl.setContentsMargins(0, 0, 0, 0); pl.setSpacing(8)
    pl.addWidget(picker, 1); pl.addWidget(refresh_btn, 0)
    body.addWidget(_row(row_label("Root"), picker_w))

    url_edit = QLineEdit()
    url_edit.setPlaceholderText("https://example.com/docs")
    url_edit.setMinimumWidth(0)
    add_btn = QPushButton("+ Add")
    add_btn.setProperty("class", "primary")
    add_btn.setMaximumWidth(100)
    add_w = QWidget()
    al = QHBoxLayout(add_w); al.setContentsMargins(0, 0, 0, 0); al.setSpacing(8)
    al.addWidget(url_edit, 1); al.addWidget(add_btn, 0)
    body.addWidget(_row(row_label("Add doc URL"), add_w))

    status_lbl = QLabel("")
    status_lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")
    status_lbl.setWordWrap(True)
    body.addWidget(status_lbl)

    from PySide6.QtWidgets import QSizePolicy as _QSP

    table = QTableWidget(0, 4)
    table.setHorizontalHeaderLabels(["Source", "Title", "Chunks", ""])
    table.verticalHeader().setVisible(False)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    hdr = table.horizontalHeader()
    hdr.setMinimumSectionSize(50)
    hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
    table.setMinimumWidth(0)
    table.setMinimumHeight(180)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    table.setSizePolicy(_QSP.Policy.Expanding, _QSP.Policy.Preferred)
    body.addWidget(table)

    def _current_root_path() -> str:
        idx = picker.currentIndex()
        if idx < 0:
            return ""
        return str(picker.itemData(idx) or "")

    def _set_status(text: str, kind: str = "info") -> None:
        color = {"info": FG_MUTE, "ok": OK, "err": ERR}.get(kind, FG_MUTE)
        status_lbl.setStyleSheet(f"color: {color}; font-size: 11px;")
        status_lbl.setText(text)

    def _reload_table() -> None:
        path = _current_root_path()
        table.setRowCount(0)
        if not path:
            _set_status("No root configured. Add one in the Roots card.")
            return

        async def _go():
            from docgraph.process import list_docs_for
            ok, payload = await list_docs_for(path)
            from PySide6.QtCore import QTimer
            def _fill():
                if not ok:
                    _set_status(str(payload), "err")
                    return
                rows = payload if isinstance(payload, list) else []
                table.setRowCount(len(rows))
                for i, r in enumerate(rows):
                    src = r.get("source", "")
                    title = r.get("title", "") or ""
                    chunks = str(r.get("chunks", 0))
                    table.setItem(i, 0, QTableWidgetItem(src))
                    table.setItem(i, 1, QTableWidgetItem(title))
                    item_n = QTableWidgetItem(chunks)
                    item_n.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    table.setItem(i, 2, item_n)
                    rm = QPushButton("✕")
                    rm.setFlat(True)
                    rm.setFixedWidth(28)
                    rm.setStyleSheet(
                        f"QPushButton {{ color: {FG_DIM}; border: none; background: transparent; }}"
                        f" QPushButton:hover {{ color: #ff6b6b; }}"
                    )
                    rm.clicked.connect(lambda _checked, u=src: _remove(u))
                    table.setCellWidget(i, 3, rm)
                _set_status(f"{len(rows)} doc source(s) for this root.")
            QTimer.singleShot(0, _fill)

        _run(window, _go)

    def _add() -> None:
        url = url_edit.text().strip()
        path = _current_root_path()
        if not url or not path:
            return

        async def _go():
            from docgraph.process import add_doc_for
            ok, payload = await add_doc_for(path, url)
            from PySide6.QtCore import QTimer
            def _after():
                if ok:
                    chunks = payload.get("chunks", "?") if isinstance(payload, dict) else "?"
                    title = payload.get("title", "") if isinstance(payload, dict) else ""
                    _set_status(f"Added: {title or url} ({chunks} chunks).", "ok")
                    url_edit.clear()
                    _reload_table()
                else:
                    _set_status(str(payload), "err")
            QTimer.singleShot(0, _after)

        _set_status(f"Fetching {url} …", "info")
        _run(window, _go)

    def _remove(url: str) -> None:
        path = _current_root_path()
        if not url or not path:
            return
        async def _go():
            from docgraph.process import remove_doc_for
            ok, payload = await remove_doc_for(path, url)
            from PySide6.QtCore import QTimer
            def _after():
                if ok:
                    n = payload.get("removed_chunks", 0) if isinstance(payload, dict) else 0
                    _set_status(f"Removed {n} chunks for {url}.", "ok")
                    _reload_table()
                else:
                    _set_status(str(payload), "err")
            QTimer.singleShot(0, _after)
        _run(window, _go)

    add_btn.clicked.connect(_add)
    url_edit.returnPressed.connect(_add)
    refresh_btn.clicked.connect(_reload_table)
    picker.currentIndexChanged.connect(lambda _i: _reload_table())

    def refresh() -> None:
        # Repopulate the picker from the current roots list. Preserve
        # selection by path so the table doesn't blink to a different
        # root every refresh tick.
        prev = _current_root_path()
        picker.blockSignals(True)
        picker.clear()
        for entry in (get_path(read_settings(), "docgraph.roots", []) or []):
            if not isinstance(entry, dict):
                continue
            p = str(entry.get("path", "") or "").strip()
            if not p:
                continue
            picker.addItem(p, p)
        # Restore selection
        if prev:
            for i in range(picker.count()):
                if picker.itemData(i) == prev:
                    picker.setCurrentIndex(i)
                    break
        picker.blockSignals(False)
        _reload_table()

    refresh()
    return card, refresh


# ── LLM prompt overrides ───────────────────────────────────────────────

_DOCSTRING_PROMPT_DEFAULT = (
    "Write a single-sentence docstring (under 25 words) for this {kind} "
    "named `{name}` in {language}. Describe its purpose, not its implementation. "
    "Return only the sentence — no quotes, no markdown, no preamble.\n\n"
    "```{language}\n{body}\n```"
)

_WIKI_PROMPT_DEFAULT = (
    "Write a Markdown page with these sections:\n"
    "1. **Summary** — 2-3 sentences on the module's purpose.\n"
    "2. **Key entities** — bulleted list of the most important classes/functions and what each is for.\n"
    "3. **How it's used** — who imports it, in plain language.\n"
    "Total length: 200-300 words. No code blocks. Do not list every file. "
    "Only state what the facts support."
)


def _build_prompts_card(window) -> tuple[QFrame, Callable[[], None] | None]:
    """Two text editors that override docgraph's built-in LLM prompts.

    Stored at `docgraph.llm.prompts.docstring` / `.wiki` in settings.
    Telecode materializes the override text to a temp file and passes
    `--llm-prompt-docstring-file` / `--llm-prompt-wiki-file` to docgraph
    on host launch (and on index/wiki subprocesses). Empty value = use
    docgraph's built-in default."""
    from PySide6.QtWidgets import QPlainTextEdit
    from PySide6.QtGui import QFontDatabase

    card, body = _card(
        "LLM prompts",
        "Override docgraph's built-in prompts. Empty = built-in default. "
        "Docstring template MUST keep {kind} / {name} / {language} / {body}.",
    )

    def _editor(setting_path: str, default: str, label: str, help_text: str,
                cli_flag: str, height: int) -> tuple[QWidget, QWidget]:
        """Returns (editor_row, actions_row) — both fully shaped via `_row()`."""
        te = QPlainTextEdit()
        te.setFixedHeight(height)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPointSize(10)
        te.setFont(mono)
        cur = str(get_path(read_settings(), setting_path, "") or "")
        te.setPlaceholderText("(empty — using built-in default)")
        te.setPlainText(cur)
        # Cap so the editor doesn't span the entire window on wide
        # displays — same rationale as `_line_row`'s 720 cap.
        te.setMaximumWidth(720)

        save_btn = QPushButton("Save")
        save_btn.setProperty("class", "primary")
        reset_btn = QPushButton("Reset to default")
        reset_btn.setProperty("class", "ghost")
        clear_btn = QPushButton("Clear (use built-in)")
        clear_btn.setProperty("class", "ghost")
        info_lbl = QLabel("")
        info_lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")
        info_lbl.setWordWrap(True)

        actions = QWidget()
        ar = QHBoxLayout(actions); ar.setContentsMargins(0, 0, 0, 0); ar.setSpacing(8)
        ar.addWidget(save_btn); ar.addWidget(reset_btn); ar.addWidget(clear_btn)
        ar.addWidget(info_lbl, 1)

        def _save():
            patch_settings(setting_path, te.toPlainText())
            info_lbl.setStyleSheet(f"color: {OK}; font-size: 11px;")
            info_lbl.setText("Saved. Restart the host to apply.")

        def _reset():
            te.setPlainText(default)
            patch_settings(setting_path, default)
            info_lbl.setStyleSheet(f"color: {OK}; font-size: 11px;")
            info_lbl.setText("Reset to default. Restart the host to apply.")

        def _clear():
            te.setPlainText("")
            patch_settings(setting_path, "")
            info_lbl.setStyleSheet(f"color: {OK}; font-size: 11px;")
            info_lbl.setText("Cleared. docgraph will use its built-in default. Restart the host.")

        save_btn.clicked.connect(_save)
        reset_btn.clicked.connect(_reset)
        clear_btn.clicked.connect(_clear)

        editor_row = _row(row_label(label, help_text, setting_path, cli=cli_flag), te)
        actions_row = _row(row_label("Actions"), actions)
        return editor_row, actions_row

    er, ar = _editor(
        "docgraph.llm.prompts.docstring",
        _DOCSTRING_PROMPT_DEFAULT,
        "Docstring template",
        "Used by `docgraph index --llm-docstrings`.",
        "--llm-prompt-docstring-file",
        height=140,
    )
    body.addWidget(er); body.addWidget(ar)

    body.addWidget(_section_header("Wiki"))
    er, ar = _editor(
        "docgraph.llm.prompts.wiki",
        _WIKI_PROMPT_DEFAULT,
        "Wiki output-format tail",
        "Used by `docgraph wiki`. Replaces the trailing output-format block.",
        "--llm-prompt-wiki-file",
        height=140,
    )
    body.addWidget(er); body.addWidget(ar)
    return card, None


# ── Document indexing (tier 2 + 3) ─────────────────────────────────────

_DOC_DEFAULT_TEXT_EXTS = ("md", "markdown", "txt", "rst", "csv")
_DOC_DEFAULT_ASSET_EXTS = (
    "pdf", "xlsx", "xls", "docx", "doc", "ppt", "pptx",
    "png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "bmp", "tiff",
    "mp4", "mov", "webm", "avi", "mkv", "mp3", "wav", "flac", "ogg", "m4a",
    "zip", "tar", "gz", "tgz", "7z", "rar", "bz2", "xz",
    "parquet", "feather", "arrow", "h5", "hdf5", "pkl", "pickle", "npz", "npy",
    "ttf", "woff", "woff2", "otf", "eot",
    "gltf", "glb", "fbx", "obj", "stl", "blend",
)


def _build_documents_index_card(window) -> tuple[QFrame, Callable[[], None] | None]:
    """Settings for `docgraph index --documents`. Off by default. When
    enabled, the indexer adds:
      - Text-tier Doc nodes from .md/.txt/.rst/small CSVs.
      - Asset nodes for media / large / binary files.
      - REFERENCES_ edges from any code or doc that mentions an Asset
        path in a quoted string literal or markdown link.
    """
    card, body = _card(
        "Document indexing",
        "Tier-2 (text docs) + tier-3 (binary assets). Off by default.",
    )

    body.addWidget(_toggle_row(
        "docgraph.index.documents.enabled", "Enabled",
        "Master switch.",
        cli="--documents",
    ))
    body.addWidget(_list_row(
        "docgraph.index.documents.text_extensions",
        "Text extensions",
        "Empty = defaults: " + ", ".join(_DOC_DEFAULT_TEXT_EXTS),
        placeholder="md",
    ))
    body.addWidget(_list_row(
        "docgraph.index.documents.asset_extensions",
        "Asset extensions",
        "Registered as Asset nodes. Empty = built-in defaults.",
        placeholder="pdf",
    ))
    return card, None


# Curated embedding model dropdown.
#
# All entries are fastembed-native (verified against
# `TextEmbedding.list_supported_models()`). Ordered by popularity + quality:
# the BGE family covers most code/RAG installs; jina-v3 + e5-large are the
# frontier multilingual picks; jina-v2-base-code is the only code-specialized
# fastembed model; mxbai-large was MTEB top-of-list through 2024-2025;
# all-MiniLM-L6-v2 is the historically most-downloaded sentence embedding
# (Continue.dev's default, LangChain/ChromaDB common pick).
#
# DocGraph auto-derives the Kuzu schema dim from the chosen model — switching
# to a different-dim model requires `Clear` + full reindex (existing vectors
# are wrong-shape under a new dim).
def _restart_host_row(window) -> QWidget:
    """A compact 'Restart host' button. Lives inside cards whose settings
    only take effect on the next host spawn (Embeddings, Reranker).
    Disabled when the host is already stopped."""
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 4, 0, 0); h.setSpacing(8)
    btn = QPushButton("🔄 Restart host")
    btn.setProperty("class", "ghost")
    btn.setToolTip(
        "Stops the running docgraph host and starts a fresh one so the\n"
        "settings on this card take effect. Equivalent to Host → Restart."
    )

    def _on_restart():
        async def _go():
            from docgraph.process import get_host
            sup = get_host()
            try:
                await sup.stop()
            except Exception:
                pass
            await sup.start()
        _run(window, _go)

    def _refresh_enabled():
        try:
            from docgraph.process import status_snapshot
            alive = bool((status_snapshot().get("host") or {}).get("alive"))
        except Exception:
            alive = False
        btn.setEnabled(alive)
        btn.setToolTip(
            btn.toolTip() if alive else
            "Host is not running — start it from the Host card first."
        )

    btn.clicked.connect(_on_restart)
    h.addWidget(btn)
    h.addStretch(1)
    _refresh_enabled()
    # No periodic refresh wired in; if the host dies between renders the
    # next click is a no-op (sup.stop swallows, sup.start surfaces the error).
    return row


_DOCGRAPH_EMBED_MODELS: list[tuple[str, str]] = [
    ("Default (BAAI/bge-small-en-v1.5)  ·  384 · 67 MB · fastembed default", ""),
    ("BAAI/bge-base-en-v1.5  ·  768 · 210 MB · BGE family base",
     "BAAI/bge-base-en-v1.5"),
    ("BAAI/bge-large-en-v1.5  ·  1024 · 1.2 GB · full-size BGE",
     "BAAI/bge-large-en-v1.5"),
    ("mxbai-embed-large-v1  ·  1024 · 640 MB · MTEB-top through 2024-25",
     "mixedbread-ai/mxbai-embed-large-v1"),
    ("jina-embeddings-v3  ·  1024 · 2.3 GB · ~100 langs · 2026 frontier",
     "jinaai/jina-embeddings-v3"),
    ("intfloat/multilingual-e5-large  ·  1024 · 2.2 GB · ~100 langs · best multilingual",
     "intfloat/multilingual-e5-large"),
    ("jina-embeddings-v2-base-code  ·  768 · 640 MB · 30+ prog langs · 8K ctx",
     "jinaai/jina-embeddings-v2-base-code"),
    ("all-MiniLM-L6-v2  ·  384 · 90 MB · most-downloaded sentence-transformer",
     "sentence-transformers/all-MiniLM-L6-v2"),
]


def _build_embeddings_card(window) -> tuple[QFrame, Callable[[], None] | None]:
    card, body = _card(
        "Embeddings",
        "Shared by index runs + host process.",
    )
    body.addWidget(_enum_row_strs("docgraph.embeddings.model", "Model",
                                    _DOCGRAPH_EMBED_MODELS,
                                    "Schema dim auto-aligns to model. "
                                    "Switching model = Clear + reindex."))
    body.addWidget(_toggle_row("docgraph.embeddings.gpu", "GPU embeddings",
                                "Needs onnxruntime-gpu/-directml/-silicon. "
                                "Forwarded to both the host process and any "
                                "fallback `docgraph index` subprocess. On "
                                "Windows hybrid graphics, telecode writes "
                                "`GpuPreference=2;` for the docgraph binary "
                                "on spawn so the windowless host lands on "
                                "the dGPU rather than the iGPU.",
                                cli="--gpu"))
    body.addWidget(_number_row("docgraph.index.workers", "Index workers",
                                0, 64, 1, 0, "", "0 = default.",
                                cli="--workers"))
    body.addWidget(_number_row("docgraph.index.embed_batch_size", "Embed batch size",
                                0, 1024, 16, 0, "", "0 = default (256 CPU / 32 GPU). Lower if GPU saturates.",
                                cli="--embed-batch-size"))
    return card, None


# fastembed cross-encoder rerankers (verified against
# TextCrossEncoder.list_supported_models()). Cross-encoders run query+doc
# pairs through one model — much more accurate than the bi-encoder embedding
# search but only viable on the top ~50 candidates. Loaded lazily on first
# use; never paid if rerank stays off.
_DOCGRAPH_RERANK_MODELS: list[tuple[str, str]] = [
    ("Default (jinaai/jina-reranker-v1-tiny-en)  ·  130 MB · English · 8K ctx", ""),
    ("jinaai/jina-reranker-v1-turbo-en  ·  150 MB · English · 8K ctx",
     "jinaai/jina-reranker-v1-turbo-en"),
    ("jinaai/jina-reranker-v2-base-multilingual  ·  1.1 GB · ~100 langs",
     "jinaai/jina-reranker-v2-base-multilingual"),
    ("BAAI/bge-reranker-base  ·  1.0 GB · English · MTEB strong",
     "BAAI/bge-reranker-base"),
    ("Xenova/ms-marco-MiniLM-L-12-v2  ·  120 MB · English · classic ms-marco",
     "Xenova/ms-marco-MiniLM-L-12-v2"),
    ("Xenova/ms-marco-MiniLM-L-6-v2  ·  80 MB · English · smallest",
     "Xenova/ms-marco-MiniLM-L-6-v2"),
]


def _build_reranker_card(window) -> tuple[QFrame, Callable[[], None] | None]:
    card, body = _card(
        "Reranker",
        "Cross-encoder over the top ~50 search candidates. Off by default.",
    )
    body.addWidget(_toggle_row("docgraph.rerank.default", "Always rerank",
                                "Default rerank=true on /api/search + MCP search. "
                                "Costs one cross-encoder pass per query."))
    body.addWidget(_enum_row_strs("docgraph.rerank.model", "Model",
                                    _DOCGRAPH_RERANK_MODELS,
                                    "Lazy-loaded on first reranked search."))
    body.addWidget(_toggle_row("docgraph.rerank.gpu", "GPU reranker",
                                "Cross-encoder on GPU. Independent of "
                                "embeddings GPU. Needs onnxruntime-gpu/"
                                "-directml/-silicon. Falls back to CPU on init "
                                "failure.",
                                cli="--rerank-gpu"))
    return card, None
