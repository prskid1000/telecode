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

from tray.qt_widgets import row_label, Toggle
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
        "Path to docgraph executable. Bare name = use PATH. Empty = autodetect "
        "(which → settings-dir venv → ~/.local/bin → ~/.docgraph venv).",
        cli="resolves the `docgraph` CLI invoked everywhere below",
    ))
    layout.addWidget(binary_card)

    for build in (
        _build_host_card,
        _build_roots_card,
        _build_llm_card,
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


def _log_hint(basename: str) -> QLabel:
    lbl = QLabel(f"📜 Live log → Logs section · file: <code>{basename}</code>")
    lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")
    lbl.setTextFormat(Qt.TextFormat.RichText)
    return lbl


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
        "Web UI + JSON API + MCP HTTP all on the same port. Restart to pick up "
        "settings changes (root list, watch flags, etc.).",
    )

    body.addWidget(_toggle_row("docgraph.host.enabled", "Enabled",
                                "Live-state flag. Off here = host is stopped right now. "
                                "Doesn't affect Auto-start at boot."))
    body.addWidget(_toggle_row("docgraph.host.auto_start", "Auto-start",
                                "Start the host when telecode boots. Independent of Enabled."))
    body.addWidget(_toggle_row("docgraph.host.auto_restart", "Auto-restart",
                                "Re-spawn on unexpected exit."))
    body.addWidget(_line_row("docgraph.host.host", "Bind Host", "127.0.0.1",
                              cli="docgraph host --host"))
    body.addWidget(_number_row("docgraph.host.port", "Bind Port", 1024, 65535, 1, 0,
                                cli="docgraph host --port"))
    body.addWidget(_toggle_row("docgraph.host.gpu", "GPU embeddings",
                                "Forwards DOCGRAPH_GPU=1 to the host process.",
                                cli="DOCGRAPH_GPU=1 / docgraph host --gpu"))

    action_row = QHBoxLayout()
    action_row.setSpacing(8)
    start_btn = QPushButton("▶ Start"); start_btn.setProperty("class", "primary")
    stop_btn  = QPushButton("Stop");    stop_btn.setProperty("class", "danger")
    restart_btn = QPushButton("Restart")
    action_row.addWidget(start_btn)
    action_row.addWidget(stop_btn)
    action_row.addWidget(restart_btn)
    action_row.addStretch(1)
    body.addLayout(action_row)
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
        "Each row is a repo registered with the host. Per-row Index "
        "POSTs /api/admin/index when the host is alive (else falls back "
        "to a `docgraph index <path>` subprocess). The Full toggle below "
        "governs both per-row Index and Index-all-roots — on = `--full` "
        "(wipe + rebuild), off = incremental. Watch toggle is persistent "
        "— flip and restart the host to take effect.",
    )

    # Build the master Full toggle FIRST so its state can be threaded
    # into per-row Index buttons via a getter.
    from tray.qt_widgets import Toggle as _Toggle
    all_force = _Toggle()
    all_force.setToolTip(
        "Governs every Index button in this card.\n"
        "On  = docgraph index --full   (wipe + rebuild)\n"
        "Off = incremental"
    )

    paths_widget = _RootsTable(window, force_getter=all_force.isChecked)
    body.addWidget(paths_widget)

    action_row = QHBoxLayout()
    action_row.setSpacing(8)
    all_force_lbl = QLabel("Full")
    all_force_lbl.setStyleSheet(f"color: {FG_DIM};")
    all_force_lbl.setToolTip(all_force.toolTip())
    run_all_btn = QPushButton("▶ Index all roots")
    cancel_btn = QPushButton("Cancel")
    cancel_btn.setProperty("class", "danger")
    status_lbl, refresh_status = _status_pill(_index_status_text)
    action_row.addWidget(all_force_lbl)
    action_row.addWidget(all_force)
    action_row.addWidget(run_all_btn)
    action_row.addWidget(cancel_btn)
    action_row.addWidget(status_lbl, 1)
    body.addLayout(action_row)
    body.addWidget(_log_hint("docgraph_index.log"))

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

    run_all_btn.clicked.connect(_all)
    cancel_btn.clicked.connect(_on_cancel)

    def refresh():
        refresh_status()
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
        h.setSpacing(8)

        self._edit = QLineEdit(path)
        self._edit.setPlaceholderText("/path/to/repo")
        self._edit.editingFinished.connect(self._on_edit_done)
        h.addWidget(self._edit, 1)

        self._index_btn = QPushButton("▶ Index")
        self._index_btn.setToolTip(
            "POST /api/admin/index?root=<slug>  if host is alive,\n"
            "else falls back to:  docgraph index <path>"
        )
        self._index_btn.clicked.connect(self._trigger_index)
        h.addWidget(self._index_btn)

        watch_lbl = QLabel("Watch")
        watch_lbl.setStyleSheet(f"color: {FG_DIM};")
        watch_lbl.setToolTip(
            "Forwards as `docgraph host --watch <path>`. "
            "Restart the host to apply a flipped flag."
        )
        h.addWidget(watch_lbl)
        self._watch = Toggle()
        self._watch.setChecked(bool(watch))
        self._watch.toggled.connect(self._on_watch_toggled)
        self._watch.setToolTip(
            "Forwards as `docgraph host --watch <path>`. "
            "Restart the host to apply a flipped flag."
        )
        h.addWidget(self._watch)

        self._pill = QLabel("…")
        self._pill.setProperty("class", "stat_pill")
        self._pill.setMinimumWidth(180)
        h.addWidget(self._pill)

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

    def refresh_state(self) -> None:
        path = self.text().strip()
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
            # No telecode-tracked run for this path — check the filesystem
            # so we don't show 'never indexed' for a repo someone already
            # indexed via the CLI directly.
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


# ── LLM card ─────────────────────────────────────────────────────────────

def _build_llm_card(window) -> tuple[QFrame, Callable[[], None] | None]:
    card, body = _card(
        "LLM augmentation",
        "Optional. Generates one-line docstrings for entities lacking native "
        "docs. Applies at index time across every root.",
    )
    body.addWidget(_line_row("docgraph.llm.model", "Model",
                              "qwen3.6-35b",
                              "Empty = off. Setting any value enables --llm-model.",
                              cli="docgraph index --llm-model"))
    body.addWidget(_line_row("docgraph.llm.host", "Host", "localhost",
                              cli="docgraph index --llm-host"))
    body.addWidget(_number_row("docgraph.llm.port", "Port", 1, 65535, 1, 0,
                                cli="docgraph index --llm-port"))
    body.addWidget(_enum_row_strs(
        "docgraph.llm.format", "Format",
        [("OpenAI-compatible", "openai"), ("Anthropic-compatible", "anthropic")],
        "Wire format for the local LLM endpoint.",
    ))
    body.addWidget(_number_row("docgraph.llm.max_tokens", "Max Tokens",
                                10, 4096, 50, 0,
                                cli="docgraph index --llm-max-tokens"))
    return card, None


# ── Embeddings card ─────────────────────────────────────────────────────

def _build_embeddings_card(window) -> tuple[QFrame, Callable[[], None] | None]:
    card, body = _card(
        "Embeddings",
        "Settings shared by index runs and the host process.",
    )
    body.addWidget(_line_row("docgraph.embeddings.model", "Model",
                              "BAAI/bge-small-en-v1.5",
                              "Empty = docgraph default. Sets DOCGRAPH_EMBED_MODEL.",
                              cli="DOCGRAPH_EMBED_MODEL=…"))
    body.addWidget(_toggle_row("docgraph.embeddings.gpu", "GPU embeddings",
                                "Requires onnxruntime-gpu / -directml / -silicon installed.",
                                cli="docgraph index --gpu"))
    body.addWidget(_number_row("docgraph.index.workers", "Index workers",
                                0, 64, 1, 0, "", "0 = docgraph default",
                                cli="docgraph index --workers"))
    return card, None
