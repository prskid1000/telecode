"""DocGraph section builder — single sidebar entry, single scrollable page.

Cards stack vertically: Binary → Index → Watch → Serve → MCP → Daemon. Each
card carries its own form rows + Start/Stop/Restart + status pill. Live log
tailing is delegated to the global Logs section — it picks up
`docgraph_<role>.log` plus per-MCP-child `docgraph_mcp_<slug>.log` files
automatically.

The previous tabbed layout caused QTabWidget pane-sizing headaches (cards
stretching to fill the tallest tab's height). A flat stacked page matches the
llama.cpp section's pattern and avoids them entirely.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)

from tray.qt_widgets import row_label
from tray.qt_helpers import (
    read_settings, get_path, patch_settings, schedule, humanize,
)
from tray.qt_theme import FG, FG_DIM, FG_MUTE, BG, BG_CARD, BORDER, OK, ERR, WARN
from tray.qt_sections import (
    _page, _card, _section_header, _row, _toggle_row, _line_row,
    _list_row, _number_row, _enum_row_strs, _wrap_align,
)

log = logging.getLogger("telecode.tray.docgraph")


def build_docgraph_tabs(window) -> QWidget:
    """Build the DocGraph section. Name kept for qt_sections._docgraph caller."""
    scroll, _, layout = _page()

    refresh_fns: list[Callable[[], None]] = []

    # Binary card — single row, autodetect chain documented in the help text.
    binary_card, bb = _card("Binary")
    bb.addWidget(_line_row(
        "docgraph.binary",
        "Binary Path",
        "docgraph",
        "Path to docgraph executable. Bare name = use PATH. Empty = autodetect "
        "(which → settings-dir venv → ~/.local/bin → ~/.docgraph venv).",
    ))
    layout.addWidget(binary_card)

    # Stacked cards — each builder returns (card, refresh_fn).
    for build in (
        _build_index_card,
        _build_watch_card,
        _build_serve_card,
        _build_mcp_card,
        _build_daemon_card,
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


# ── Helpers ──────────────────────────────────────────────────────────────────

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


# ── Index card ───────────────────────────────────────────────────────────────

def _build_index_card(window) -> tuple[QFrame, Callable[[], None]]:
    card, body = _card("Index", "Per-path reindex. Incremental by default; Force = --full wipe + rebuild.")

    body.addWidget(_section_header("Paths"))
    paths_widget = _PathsTable(window)
    body.addWidget(paths_widget)

    body.addWidget(_section_header("Flags (apply to every Index/Force run)"))
    body.addWidget(_number_row("docgraph.index.workers", "Workers", 0, 64, 1, 0,
                                "", "0 = docgraph default"))
    body.addWidget(_toggle_row("docgraph.index.gpu", "GPU embeddings (--gpu)",
                                "Requires onnxruntime-gpu/-directml/-silicon installed."))
    body.addWidget(_line_row("docgraph.index.embedding_model", "Embedding Model",
                              "BAAI/bge-small-en-v1.5",
                              "Empty = docgraph default. Sets DOCGRAPH_EMBED_MODEL."))

    body.addWidget(_section_header("LLM-augmented docstrings (optional)"))
    body.addWidget(_line_row("docgraph.index.llm_model", "LLM Model",
                              "qwen3.6-35b",
                              "Setting this enables --llm-model. Empty = off."))
    body.addWidget(_line_row("docgraph.index.llm_host", "LLM Host", "localhost"))
    body.addWidget(_number_row("docgraph.index.llm_port", "LLM Port", 1, 65535, 1, 0))
    body.addWidget(_line_row("docgraph.index.llm_format", "LLM Format", "openai | anthropic"))
    body.addWidget(_number_row("docgraph.index.llm_max_tokens", "LLM Max Tokens",
                                10, 4096, 50, 0))

    action_row = QHBoxLayout()
    action_row.setSpacing(8)
    from tray.qt_widgets import Toggle as _Toggle
    all_force_lbl = QLabel("Force")
    all_force_lbl.setStyleSheet(f"color: {FG_DIM};")
    all_force_lbl.setToolTip("Pass --full to every path when running 'Index all'")
    all_force = _Toggle()
    all_force.setToolTip("Pass --full to every path when running 'Index all'")
    run_all_btn = QPushButton("▶ Index all paths")
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
        what = "force" if s.get("current_force") else "incremental"
        return True, f"running · {s.get('current_path') or '?'} ({what})"
    return False, "idle"


# ── Per-path paths editor + per-row Index/Force buttons ──────────────────────

class _PathsTable(QWidget):
    """Custom paths editor for the Index card.

    Each row: editable path field, Force toggle, ▶ Index button,
    status pill ("never" / "2m ago · ok" / "running"), remove (✕).
    Persists to `docgraph.index.paths` on every edit.
    """

    def __init__(self, window) -> None:
        super().__init__()
        self._window = window
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
        add_btn = QPushButton("+ Add path")
        add_btn.setProperty("class", "primary")
        add_btn.setMaximumWidth(140)
        add_btn.clicked.connect(self._on_add)
        add_l.addWidget(add_btn)
        add_l.addStretch(1)
        v.addWidget(add_w)

        self._row_widgets: list[_PathRow] = []
        self._rebuild()

    def _rebuild(self) -> None:
        for w in self._row_widgets:
            w.setParent(None)
            w.deleteLater()
        self._row_widgets.clear()
        cur = list(get_path(read_settings(), "docgraph.index.paths", []) or [])
        for s in cur:
            self._append_row(str(s))

    def _append_row(self, value: str) -> None:
        row = _PathRow(value, self._window, on_change=self._commit, on_remove=self._on_remove)
        self._rows_layout.addWidget(row)
        self._row_widgets.append(row)

    def _on_add(self) -> None:
        self._append_row("")
        self._commit()

    def _on_remove(self, row: "_PathRow") -> None:
        try:
            self._row_widgets.remove(row)
        except ValueError:
            pass
        row.setParent(None)
        row.deleteLater()
        self._commit()

    def _commit(self) -> None:
        out = [r.text() for r in self._row_widgets if r.text().strip() != ""]
        try:
            patch_settings("docgraph.index.paths", out)
        except Exception:
            pass

    def refresh(self) -> None:
        # Compare against the *non-empty* displayed rows so a freshly-added
        # blank row (not yet typed into) survives the next refresh tick —
        # otherwise '+ Add path' would vanish before the user can fill it in.
        cur = [str(p) for p in (get_path(read_settings(), "docgraph.index.paths", []) or [])]
        existing_nonempty = [r.text() for r in self._row_widgets if r.text().strip()]
        if cur != existing_nonempty:
            self._rebuild()
        else:
            for r in self._row_widgets:
                r.refresh_state()


class _PathRow(QWidget):
    """One path row inside `_PathsTable`.

    Layout: [path field] [Force toggle] [▶ Index] [status pill] [✕]
    """

    def __init__(self, value: str, window, *, on_change, on_remove) -> None:
        super().__init__()
        self._window = window
        self._on_change = on_change
        self._on_remove = on_remove

        from PySide6.QtWidgets import QLineEdit
        from tray.qt_theme import BG_ELEV, BORDER
        from tray.qt_widgets import Toggle

        self.setStyleSheet(
            f"_PathRow {{ background: {BG_ELEV}; border: 1px solid {BORDER}; border-radius: 6px; }}"
        )
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(8)

        self._edit = QLineEdit(value)
        self._edit.setPlaceholderText("/path/to/repo")
        self._edit.editingFinished.connect(self._on_change)
        h.addWidget(self._edit, 1)

        force_lbl = QLabel("Force")
        force_lbl.setStyleSheet(f"color: {FG_DIM};")
        force_lbl.setToolTip("Pass --full to wipe + rebuild instead of incremental delta")
        h.addWidget(force_lbl)
        self._force = Toggle()
        self._force.setToolTip("Pass --full to wipe + rebuild instead of incremental delta")
        h.addWidget(self._force)

        self._index_btn = QPushButton("▶ Index")
        self._index_btn.setToolTip("Run docgraph index for this path")
        self._index_btn.clicked.connect(self._trigger)
        h.addWidget(self._index_btn)

        self._pill = QLabel("never")
        self._pill.setProperty("class", "stat_pill")
        self._pill.setMinimumWidth(160)
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

    def _trigger(self) -> None:
        path = self.text().strip()
        if not path:
            return
        force = bool(self._force.isChecked())
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
            self._pill.setText("never indexed")
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


def _format_ago(ts: float) -> str:
    import time as _time
    if not ts:
        return "never"
    delta = max(0, int(_time.time() - ts))
    if delta < 60:    return f"{delta}s ago"
    if delta < 3600:  return f"{delta // 60}m ago"
    if delta < 86400: return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


# ── Watch / Serve cards (generic) ────────────────────────────────────────────

def _build_watch_card(window) -> tuple[QFrame, Callable[[], None]]:
    return _build_role_card(
        window, role="watch",
        title="Watch",
        sub="docgraph watch — auto-reindex on changes. Holds writer lock; stop Serve/MCP for the same path first.",
        rows=[
            ("toggle", "docgraph.watch.enabled",      "Enabled",      "Master toggle. Off = kill subprocess + free its port."),
            ("toggle", "docgraph.watch.auto_start",   "Auto-start",   "Start at boot if Enabled."),
            ("toggle", "docgraph.watch.auto_restart", "Auto-restart", "Re-spawn on unexpected exit."),
            ("line",   "docgraph.watch.path",         "Path",         "Repo to watch (falls back to docgraph.default_path)."),
            ("toggle", "docgraph.watch.serve_too",    "Run web UI too (--serve)", "Watcher + serve in one process."),
            ("line",   "docgraph.watch.host",         "Host",         "Bind address (only with --serve)."),
            ("port",   "docgraph.watch.port",         "Port",         "Bind port (only with --serve)."),
        ],
        status_fn=_role_status_text("watch"),
        log_basename="docgraph_watch.log",
        get_supervisor=lambda: __import__("docgraph.process", fromlist=["get_watch"]).get_watch(),
    )


def _build_serve_card(window) -> tuple[QFrame, Callable[[], None]]:
    return _build_role_card(
        window, role="serve",
        title="Serve",
        sub="docgraph serve — web UI + JSON API. Read-only DB. Use the tray menu's 'Open Document Index' entry to open it in a browser.",
        rows=[
            ("toggle", "docgraph.serve.enabled",      "Enabled",      "Master toggle."),
            ("toggle", "docgraph.serve.auto_start",   "Auto-start",   "Start at boot if Enabled."),
            ("toggle", "docgraph.serve.auto_restart", "Auto-restart", "Re-spawn on unexpected exit."),
            ("line",   "docgraph.serve.path",         "Path",         "Repo to serve (falls back to docgraph.default_path)."),
            ("line",   "docgraph.serve.host",         "Host",         "Bind address."),
            ("port",   "docgraph.serve.port",         "Port",         "Bind port."),
            ("toggle", "docgraph.serve.gpu",          "GPU embeddings", "Forwarded via DOCGRAPH_GPU=1."),
        ],
        status_fn=_role_status_text("serve"),
        log_basename="docgraph_serve.log",
        get_supervisor=lambda: __import__("docgraph.process", fromlist=["get_serve"]).get_serve(),
    )


def _build_role_card(window, *, role: str, title: str, sub: str,
                      rows: list[tuple], status_fn, log_basename: str,
                      get_supervisor) -> tuple[QFrame, Callable[[], None]]:
    card, body = _card(title, sub)
    for kind, *args in rows:
        if kind == "toggle":
            path, label, help_text = args
            body.addWidget(_toggle_row(path, label, help_text))
        elif kind == "line":
            path, label, help_text = args
            body.addWidget(_line_row(path, label, "", help_text))
        elif kind == "port":
            path, label, help_text = args
            body.addWidget(_number_row(path, label, 1, 65535, 1, 0, "", help_text))

    action_row = QHBoxLayout()
    action_row.setSpacing(8)
    start_btn = QPushButton("▶ Start"); start_btn.setProperty("class", "primary")
    stop_btn  = QPushButton("Stop");    stop_btn.setProperty("class", "danger")
    restart_btn = QPushButton("Restart")
    pill, refresh_status = _status_pill(status_fn)
    action_row.addWidget(start_btn)
    action_row.addWidget(stop_btn)
    action_row.addWidget(restart_btn)
    action_row.addWidget(pill, 1)
    body.addLayout(action_row)
    body.addWidget(_log_hint(log_basename))

    def _on_start():
        async def _go():
            sup = get_supervisor()
            await sup.start()
        _run(window, _go)

    def _on_stop():
        async def _go():
            sup = get_supervisor()
            await sup.stop()
        _run(window, _go)

    def _on_restart():
        async def _go():
            sup = get_supervisor()
            await sup.stop()
            await sup.start()
        _run(window, _go)

    start_btn.clicked.connect(_on_start)
    stop_btn.clicked.connect(_on_stop)
    restart_btn.clicked.connect(_on_restart)

    return card, refresh_status


def _role_status_text(role: str):
    def _get():
        try:
            from docgraph.process import status_snapshot
            s = status_snapshot().get(role, {})
        except Exception as exc:
            return False, f"err: {exc}"
        if s.get("alive"):
            pid = s.get("pid"); port = s.get("port")
            extra = f" pid={pid}" if pid else ""
            extra += f" port={port}" if port else ""
            return True, f"alive{extra}"
        return False, "stopped" + (" (enabled)" if s.get("enabled") else "")
    return _get


# ── MCP card ─────────────────────────────────────────────────────────────────

def _build_mcp_card(window) -> tuple[QFrame, Callable[[], None]]:
    card, body = _card(
        "MCP",
        "One docgraph mcp child per repo path. Each child's tools are bridged into the proxy as managed tools."
    )
    body.addWidget(_toggle_row("docgraph.mcp.enabled", "Enabled",
                                "Master toggle. Off = kill all children + unregister bridge."))
    body.addWidget(_toggle_row("docgraph.mcp.auto_start", "Auto-start",
                                "Spawn at boot if Enabled."))
    body.addWidget(_toggle_row("docgraph.mcp.auto_restart", "Auto-restart",
                                "Re-spawn on unexpected exit."))
    body.addWidget(_list_row("docgraph.mcp.paths", "Paths",
                              "One repo per line. Each gets its own docgraph mcp child + its own MCP port.",
                              "/path/to/repo"))
    body.addWidget(_number_row("docgraph.mcp.base_port", "Base Port", 1024, 65535, 1, 0,
                                "", "First port; subsequent children use base_port + i."))
    body.addWidget(_line_row("docgraph.mcp.host", "Host", "127.0.0.1"))
    body.addWidget(_toggle_row("docgraph.mcp.gpu", "GPU embeddings",
                                "Forwarded to each child via DOCGRAPH_GPU=1."))
    body.addWidget(_number_row("docgraph.mcp.ready_timeout_sec", "Ready Timeout",
                                5, 600, 5, 0, "s",
                                "How long to wait for /mcp to become reachable."))

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

    children_lbl = QLabel("(no children)")
    children_lbl.setStyleSheet(f"color: {FG_MUTE}; font-family: monospace;")
    body.addWidget(_section_header("Children"))
    body.addWidget(children_lbl)
    body.addWidget(_log_hint("docgraph_mcp_<slug>.log"))

    def _start():
        async def _go():
            from docgraph.process import get_mcp
            await get_mcp().start()
        _run(window, _go)

    def _stop():
        async def _go():
            from docgraph.process import get_mcp
            await get_mcp().stop()
        _run(window, _go)

    def _restart():
        async def _go():
            from docgraph.process import get_mcp
            sup = get_mcp()
            await sup.stop()
            await sup.start()
        _run(window, _go)

    start_btn.clicked.connect(_start)
    stop_btn.clicked.connect(_stop)
    restart_btn.clicked.connect(_restart)

    def refresh():
        try:
            from docgraph.process import get_mcp
            kids = get_mcp().status()
        except Exception:
            kids = []
        if not kids:
            children_lbl.setText("(no children)")
        else:
            lines = []
            for c in kids:
                pid = c.get("pid") or "?"
                state = "✓ alive" if c.get("alive") else "✗ down"
                lines.append(f"{state}  {c.get('slug','?')}  port={c.get('port')}  pid={pid}  bridged={c.get('bridged', 0)}")
            children_lbl.setText("\n".join(lines))
    refresh()
    return card, refresh


# ── Daemon card ──────────────────────────────────────────────────────────────

def _build_daemon_card(window) -> tuple[QFrame, Callable[[], None] | None]:
    card, body = _card("Daemon",
                       "docgraph daemon start — shared loopback embedding daemon. Optional.")
    body.addWidget(_toggle_row("docgraph.daemon.enabled",      "Enabled",      "Master toggle."))
    body.addWidget(_toggle_row("docgraph.daemon.auto_start",   "Auto-start",   "Spawn at boot if Enabled."))
    body.addWidget(_toggle_row("docgraph.daemon.auto_restart", "Auto-restart", "Re-spawn on unexpected exit."))
    body.addWidget(_number_row("docgraph.daemon.port", "Port", 1024, 65535, 1, 0,
                                "", "Loopback only. Default 5577."))
    body.addWidget(_line_row("docgraph.daemon.model", "Model",
                              "BAAI/bge-small-en-v1.5",
                              "Must match what your repos were indexed with."))
    body.addWidget(_toggle_row("docgraph.daemon.gpu", "GPU", "Loads on GPU via ONNX Runtime."))

    drow = QHBoxLayout()
    dstart = QPushButton("▶ Start"); dstart.setProperty("class", "primary")
    dstop  = QPushButton("Stop");    dstop.setProperty("class", "danger")
    drow.addWidget(dstart); drow.addWidget(dstop); drow.addStretch(1)
    body.addLayout(drow)
    body.addWidget(_log_hint("docgraph_daemon.log"))

    def _dstart():
        async def _go():
            from docgraph.process import get_daemon
            await get_daemon().start()
        _run(window, _go)

    def _dstop():
        async def _go():
            from docgraph.process import get_daemon
            await get_daemon().stop()
        _run(window, _go)

    dstart.clicked.connect(_dstart)
    dstop.clicked.connect(_dstop)

    return card, None
