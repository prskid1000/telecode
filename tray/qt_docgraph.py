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
log lands at `data/logs/docgraph_host.log`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit,
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
        _build_roots_card,
        _build_docs_card,
        _build_documents_index_card,
        _build_llm_card,
        _build_prompts_card,
        _build_embeddings_card,
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

    `<path>/.docgraph/graph.kuzu` is the marker — the indexer always
    creates it, and a directory rather than a single file means we
    can be lenient about what's inside (Kuzu writes several artefacts
    under it). Used by `_RootRow` to distinguish 'CLI-indexed already'
    from 'fresh repo, never indexed' when telecode itself has no run
    record for the path."""
    if not path:
        return False
    try:
        from pathlib import Path as _Path
        marker = _Path(path).expanduser() / ".docgraph" / "graph.kuzu"
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


# ── Host card ────────────────────────────────────────────────────────────

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
    body.addWidget(_toggle_row("docgraph.host.gpu", "GPU embeddings",
                                "Forwards DOCGRAPH_GPU=1.",
                                cli="--gpu"))

    actions = QWidget()
    ar = QHBoxLayout(actions)
    ar.setContentsMargins(0, 0, 0, 0); ar.setSpacing(8)
    start_btn = QPushButton("▶ Start"); start_btn.setProperty("class", "primary")
    stop_btn  = QPushButton("Stop");    stop_btn.setProperty("class", "danger")
    restart_btn = QPushButton("Restart")
    ar.addWidget(start_btn); ar.addWidget(stop_btn); ar.addWidget(restart_btn)
    ar.addStretch(1)
    body.addWidget(_row(row_label("Actions"), actions))
    # Status pill + log hint omitted — the global Status tile and Logs
    # section already cover both. No-op refresher kept so the card's
    # public shape (refresh callable) is unchanged.
    refresh_status = lambda: None  # noqa: E731

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

    # Build wikis row: 📖 + ✕ cancel + status pill.
    run_all_wiki_btn = QPushButton("📖 Build wikis")
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
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(6)

        self._edit = QLineEdit(path)
        self._edit.setPlaceholderText("/path/to/repo")
        self._edit.editingFinished.connect(self._on_edit_done)
        # Edit consumes all horizontal slack — trailing widgets pack
        # tightly on the right with no gap. Min width keeps trailing
        # widgets from being pushed off-screen on narrow windows; the
        # QLineEdit scrolls internally for longer paths, and the tooltip
        # exposes the full string on hover.
        self._edit.setMinimumWidth(140)
        self._edit.setToolTip(path or "/path/to/repo")
        h.addWidget(self._edit, 1)

        self._index_btn = QPushButton("▶")
        self._index_btn.setToolTip(
            "Index this root.\n"
            "POST /api/admin/index?root=<slug>  if host is alive,\n"
            "else falls back to:  docgraph index <path>"
        )
        self._index_btn.setFixedWidth(34)
        self._index_btn.clicked.connect(self._trigger_index)
        h.addWidget(self._index_btn)

        self._wiki_btn = QPushButton("📖")
        self._wiki_btn.setToolTip(
            "Build the wiki for this root.\n"
            "POST /api/wiki/build?root=<slug>  if host is alive,\n"
            "else falls back to:  docgraph wiki <path>\n"
            "Full toggle on = --force (rebuild every page)"
        )
        self._wiki_btn.setFixedWidth(34)
        self._wiki_btn.clicked.connect(self._trigger_wiki)
        h.addWidget(self._wiki_btn)

        self._stats_btn = QPushButton("📊")
        self._stats_btn.setToolTip(
            "Show stats for this root.\n"
            "GET /api/stats?root=<slug> — entity + edge counts.\n"
            "Read-only; works while the host is alive (free) or via a brief\n"
            "`docgraph stats <path>` subprocess if not."
        )
        self._stats_btn.setFixedWidth(34)
        self._stats_btn.clicked.connect(self._trigger_stats)
        h.addWidget(self._stats_btn)

        self._clear_btn = QPushButton("🗑")
        self._clear_btn.setToolTip(
            "Clear this root's index.\n"
            "POST /api/admin/clear?root=<slug> — wipe the index, cache, and\n"
            "wiki for this root. Confirmation required. Host stays alive;\n"
            "the workspace re-opens its read-only handle once the wipe is done."
        )
        self._clear_btn.setFixedWidth(34)
        self._clear_btn.clicked.connect(self._trigger_clear)
        h.addWidget(self._clear_btn)

        self._watch = Toggle()
        self._watch.setChecked(bool(watch))
        self._watch.toggled.connect(self._on_watch_toggled)
        self._watch.setToolTip(
            "Watch — auto-reindex on file changes.\n"
            "Forwards as `docgraph host --watch <path>`. "
            "Restart the host to apply a flipped flag."
        )
        h.addWidget(self._watch)

        self._pill = QLabel("…")
        self._pill.setProperty("class", "stat_pill")
        self._pill.setMinimumWidth(0)
        self._pill.setMaximumWidth(140)
        self._pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pill.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._pill.setToolTip("Index status")
        h.addWidget(self._pill, 0)

        self._wiki_pill = QLabel("…")
        self._wiki_pill.setProperty("class", "stat_pill")
        self._wiki_pill.setMinimumWidth(0)
        self._wiki_pill.setMaximumWidth(120)
        self._wiki_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._wiki_pill.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._wiki_pill.setToolTip("Wiki status")
        h.addWidget(self._wiki_pill, 0)

        rm_btn = QPushButton("✕")
        rm_btn.setFlat(True)
        rm_btn.setFixedWidth(28)
        rm_btn.setStyleSheet(
            f"QPushButton {{ color: {FG_DIM}; border: none; background: transparent; }}"
            f" QPushButton:hover {{ color: #ff6b6b; }}"
        )
        rm_btn.clicked.connect(lambda: self._on_remove(self))
        h.addWidget(rm_btn)

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

    def _trigger_stats(self) -> None:
        path = self.text().strip()
        if not path:
            return
        async def _go():
            from docgraph.process import fetch_stats
            text = await fetch_stats(path)
            from PySide6.QtWidgets import QMessageBox
            from PySide6.QtCore import QMetaObject, Qt as _Qt, QTimer
            # Hop to the GUI thread — _go() runs on the asyncio loop.
            QTimer.singleShot(0, lambda: QMessageBox.information(
                self, f"Stats — {path}", text
            ))
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
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QMessageBox as _MB
            kind = _MB.information if ok else _MB.warning
            title = "Cleared" if ok else "Clear failed"
            QTimer.singleShot(0, lambda: kind(self, title, detail))
        _run(self._window, _go)

    def refresh_state(self) -> None:
        path = self.text().strip()
        self._refresh_index_pill(path)
        self._refresh_wiki_pill(path)

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
        "Optional local LLM for index docstrings + wiki pages.",
    )
    body.addWidget(_line_row("docgraph.llm.model", "Model",
                              "qwen3.6-35b",
                              "Empty = off.",
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
                                cli="index --llm-max-tokens"))
    body.addWidget(_number_row("docgraph.llm.max_tokens_wiki", "Wiki Max Tokens",
                                256, 32768, 256, 0, "",
                                "Default 4096.",
                                cli="wiki --llm-max-tokens"))
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
    Telecode forwards them to docgraph as
    `DOCGRAPH_LLM_PROMPT_DOCSTRING` / `DOCGRAPH_LLM_PROMPT_WIKI` env vars
    when launching the host or a wiki/index subprocess. Empty value =
    use docgraph's built-in default."""
    from PySide6.QtWidgets import QPlainTextEdit
    from PySide6.QtGui import QFontDatabase

    card, body = _card(
        "LLM prompts",
        "Override docgraph's built-in prompts. Empty = built-in default. "
        "Docstring template MUST keep {kind} / {name} / {language} / {body}.",
    )

    def _editor(setting_path: str, default: str, label: str, help_text: str,
                env_var: str, height: int) -> tuple[QWidget, QWidget]:
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

        editor_row = _row(row_label(label, help_text, setting_path, env_var), te)
        actions_row = _row(row_label("Actions"), actions)
        return editor_row, actions_row

    er, ar = _editor(
        "docgraph.llm.prompts.docstring",
        _DOCSTRING_PROMPT_DEFAULT,
        "Docstring template",
        "Used by `docgraph index --llm-model`.",
        "DOCGRAPH_LLM_PROMPT_DOCSTRING",
        height=140,
    )
    body.addWidget(er); body.addWidget(ar)

    body.addWidget(_section_header("Wiki"))
    er, ar = _editor(
        "docgraph.llm.prompts.wiki",
        _WIKI_PROMPT_DEFAULT,
        "Wiki output-format tail",
        "Used by `docgraph wiki`. Replaces the trailing output-format block.",
        "DOCGRAPH_LLM_PROMPT_WIKI",
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


def _build_embeddings_card(window) -> tuple[QFrame, Callable[[], None] | None]:
    card, body = _card(
        "Embeddings",
        "Shared by index runs + host process.",
    )
    body.addWidget(_line_row("docgraph.embeddings.model", "Model",
                              "BAAI/bge-small-en-v1.5",
                              "Empty = default.",
                              cli="DOCGRAPH_EMBED_MODEL"))
    body.addWidget(_toggle_row("docgraph.embeddings.gpu", "GPU embeddings",
                                "Needs onnxruntime-gpu/-directml/-silicon.",
                                cli="--gpu"))
    body.addWidget(_number_row("docgraph.index.workers", "Index workers",
                                0, 64, 1, 0, "", "0 = default.",
                                cli="--workers"))
    return card, None
