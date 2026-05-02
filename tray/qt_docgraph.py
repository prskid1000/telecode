"""DocGraph section builder — single sidebar entry, internal QTabWidget.

Tabs: Index / Watch / Serve / MCP. Daemon lives at the bottom of the MCP tab
since it pairs naturally with the MCP children (shared embedding daemon).

Each tab follows the existing card/row pattern from qt_sections, plus a live
log tail at the bottom. Master toggles dispatch start/stop on the bot loop.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QPlainTextEdit, QFileDialog,
)

from tray.qt_widgets import Toggle, row_label
from tray.qt_helpers import (
    read_settings, get_path, patch_settings, schedule, humanize,
)
from tray.qt_theme import FG, FG_DIM, FG_MUTE, BG_CARD, OK, ERR, WARN
from tray.qt_sections import (
    _page, _card, _section_header, _row, _toggle_row, _line_row,
    _list_row, _number_row, _enum_row_strs, _wrap_align,
)

log = logging.getLogger("telecode.tray.docgraph")


def build_docgraph_tabs(window) -> QWidget:
    scroll, _, layout = _page()

    binary_card, bb = _card(
        "Binary",
        "docgraph CLI path. Empty = autodetect (which → venvs)."
    )
    bb.addWidget(_line_row("docgraph.binary", "Binary",
                            "/absolute/path/to/docgraph or empty for auto",
                            ""))
    binary_status = QLabel("…")
    binary_status.setProperty("class", "stat_pill")
    bb.addWidget(_row(row_label("Resolved", "Live binary auto-detect result"),
                       _wrap_align(binary_status, Qt.AlignmentFlag.AlignLeft)))
    browse_btn = QPushButton("Browse for docgraph executable…")
    bb.addWidget(browse_btn)

    def _browse():
        path, _ = QFileDialog.getOpenFileName(scroll, "Locate docgraph", "",
                                                "All files (*)")
        if path:
            patch_settings("docgraph.binary", path)
    browse_btn.clicked.connect(_browse)

    layout.addWidget(binary_card)

    tabs = QTabWidget()
    tabs.addTab(_build_index_tab(window), "Index")
    tabs.addTab(_build_watch_tab(window), "Watch")
    tabs.addTab(_build_serve_tab(window), "Serve")
    tabs.addTab(_build_mcp_tab(window), "MCP")

    last = str(read_settings().get("tray", {}).get("docgraph", {}).get("last_tab", "") or "")
    if last:
        for i in range(tabs.count()):
            if tabs.tabText(i).lower() == last.lower():
                tabs.setCurrentIndex(i)
                break

    def _on_tab(i: int):
        try:
            patch_settings("tray.docgraph.last_tab", tabs.tabText(i))
        except Exception:
            pass
    tabs.currentChanged.connect(_on_tab)

    layout.addWidget(tabs, 1)
    layout.addStretch(0)

    def _refresh_binary():
        try:
            from docgraph import config as dg_cfg
            resolved = dg_cfg.resolve_binary()
            if resolved:
                binary_status.setText(f"✓ {resolved}")
                binary_status.setStyleSheet(f"color: {OK};")
            else:
                binary_status.setText("✗ not found")
                binary_status.setStyleSheet(f"color: {ERR};")
        except Exception as exc:
            binary_status.setText(f"err: {exc}")
            binary_status.setStyleSheet(f"color: {ERR};")

    _refresh_binary()

    def refresh():
        _refresh_binary()
        for i in range(tabs.count()):
            page = tabs.widget(i)
            r = getattr(page, "refresh", None)
            if callable(r):
                try:
                    r()
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


def _make_log_tail(path_getter) -> QWidget:
    """Live tail panel — polls file size, appends delta. Used in each tab."""
    container = QWidget()
    v = QVBoxLayout(container)
    v.setContentsMargins(0, 8, 0, 0)
    v.setSpacing(4)

    header_row = QHBoxLayout()
    header_row.setSpacing(8)
    title = QLabel("Live log")
    title.setStyleSheet(f"color: {FG_DIM}; font-weight: 600;")
    path_lbl = QLabel("")
    path_lbl.setStyleSheet(f"color: {FG_MUTE}; font-family: monospace;")
    clear_btn = QPushButton("Clear view")
    open_btn  = QPushButton("Open externally")
    for btn in (clear_btn, open_btn):
        btn.setProperty("class", "tb_btn")
        btn.setFixedHeight(22)
    header_row.addWidget(title)
    header_row.addWidget(path_lbl, 1)
    header_row.addWidget(clear_btn)
    header_row.addWidget(open_btn)
    v.addLayout(header_row)

    edit = QPlainTextEdit()
    edit.setReadOnly(True)
    edit.setStyleSheet(f"background: {BG_CARD}; color: {FG}; font-family: monospace;")
    edit.setMinimumHeight(160)
    v.addWidget(edit, 1)

    state = {"path": "", "offset": 0}

    def _set_path(p: str) -> None:
        if state["path"] != p:
            state["path"] = p
            state["offset"] = 0
            edit.clear()
            path_lbl.setText(p or "")

    def _tick():
        path = path_getter() or ""
        _set_path(path)
        if not path or not os.path.exists(path):
            return
        try:
            size = os.path.getsize(path)
            if size < state["offset"]:
                state["offset"] = 0
                edit.clear()
            if size > state["offset"]:
                with open(path, "rb") as f:
                    f.seek(state["offset"])
                    chunk = f.read(size - state["offset"])
                state["offset"] = size
                try:
                    text = chunk.decode("utf-8", errors="replace")
                except Exception:
                    text = repr(chunk)
                edit.appendPlainText(text.rstrip("\n"))
        except OSError:
            pass

    def _clear():
        edit.clear()
        state["offset"] = os.path.getsize(state["path"]) if (state["path"] and os.path.exists(state["path"])) else 0
    clear_btn.clicked.connect(_clear)

    def _open():
        try:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            if state["path"]:
                QDesktopServices.openUrl(QUrl.fromLocalFile(state["path"]))
        except Exception:
            pass
    open_btn.clicked.connect(_open)

    timer = QTimer(container)
    timer.setInterval(1000)
    timer.timeout.connect(_tick)
    timer.start()
    container.refresh = _tick  # type: ignore[attr-defined]
    return container


def _status_pill(getter) -> tuple[QWidget, callable]:
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


# ── Index tab ────────────────────────────────────────────────────────────────

def _build_index_tab(window) -> QWidget:
    scroll, _, layout = _page()

    card, body = _card("Index", "docgraph index — one-shot reindex over each path in sequence")

    body.addWidget(_section_header("Paths"))
    body.addWidget(_list_row("docgraph.index.paths", "Paths",
                              "One repo path per line. Each runs in sequence.",
                              "/path/to/repo"))

    body.addWidget(_section_header("Flags"))
    body.addWidget(_toggle_row("docgraph.index.full", "Full reindex (--full)",
                                "Wipe + rebuild instead of incremental delta."))
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

    # Action row
    action_row = QHBoxLayout()
    action_row.setSpacing(8)
    run_btn = QPushButton("▶ Run index")
    run_btn.setProperty("class", "primary")
    cancel_btn = QPushButton("Cancel")
    cancel_btn.setProperty("class", "danger")
    status_lbl, refresh_status = _status_pill(_index_status_text)
    action_row.addWidget(run_btn)
    action_row.addWidget(cancel_btn)
    action_row.addWidget(status_lbl, 1)
    body.addLayout(action_row)

    def _on_run():
        async def _go():
            from docgraph.process import get_index
            await get_index().run()
        _run(window, _go)

    def _on_cancel():
        async def _go():
            from docgraph.process import get_index
            await get_index().cancel()
        _run(window, _go)

    run_btn.clicked.connect(_on_run)
    cancel_btn.clicked.connect(_on_cancel)

    layout.addWidget(card)

    tail = _make_log_tail(_index_log_path)
    layout.addWidget(tail, 1)

    def refresh():
        refresh_status()
        try:
            tail.refresh()
        except Exception:
            pass
    scroll.refresh = refresh  # type: ignore[attr-defined]
    return scroll


def _index_status_text() -> tuple[bool, str]:
    try:
        from docgraph.process import get_index
        s = get_index().status()
    except Exception as exc:
        return False, f"err: {exc}"
    if s["alive"]:
        return True, f"running · {s.get('current_path') or '?'}"
    return False, f"last: {s.get('last_status', 'idle')}"


def _index_log_path() -> str:
    try:
        from docgraph import config as dg_cfg
        return dg_cfg.log_path("index")
    except Exception:
        return ""


# ── Watch tab ────────────────────────────────────────────────────────────────

def _build_watch_tab(window) -> QWidget:
    return _build_role_tab(
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
        log_path_fn=lambda: _role_log_path("watch"),
        get_supervisor=lambda: __import__("docgraph.process", fromlist=["get_watch"]).get_watch(),
    )


# ── Serve tab ────────────────────────────────────────────────────────────────

def _build_serve_tab(window) -> QWidget:
    tab = _build_role_tab(
        window, role="serve",
        title="Serve",
        sub="docgraph serve — web UI + JSON API. Read-only DB.",
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
        log_path_fn=lambda: _role_log_path("serve"),
        get_supervisor=lambda: __import__("docgraph.process", fromlist=["get_serve"]).get_serve(),
    )

    # Add "Open in Browser" button — only enabled when serve is alive.
    open_btn = QPushButton("🌐  Open DocGraph UI in Browser")
    open_btn.setProperty("class", "primary")
    open_btn.setEnabled(False)

    def _do_open():
        import webbrowser
        from docgraph import config as dg_cfg
        host = dg_cfg.serve_host()
        if host == "0.0.0.0":
            host = "127.0.0.1"
        webbrowser.open(f"http://{host}:{dg_cfg.serve_port()}")

    open_btn.clicked.connect(_do_open)

    inner = tab.widget()
    if inner is not None and inner.layout() is not None:
        inner.layout().insertWidget(1, open_btn)

    prev_refresh = getattr(tab, "refresh", None)

    def refresh_with_open():
        if prev_refresh is not None:
            try:
                prev_refresh()
            except Exception:
                pass
        try:
            from docgraph.process import status_snapshot
            alive = bool(status_snapshot().get("serve", {}).get("alive"))
        except Exception:
            alive = False
        open_btn.setEnabled(alive)
    tab.refresh = refresh_with_open  # type: ignore[attr-defined]
    return tab


# ── MCP tab (+ Daemon at bottom) ─────────────────────────────────────────────

def _build_mcp_tab(window) -> QWidget:
    scroll, _, layout = _page()

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

    layout.addWidget(card)

    # ── Daemon block ────────────────────────────────────────────────────
    dcard, dbody = _card("Daemon",
                         "docgraph daemon start — shared loopback embedding daemon. Optional.")
    dbody.addWidget(_toggle_row("docgraph.daemon.enabled",      "Enabled",      "Master toggle."))
    dbody.addWidget(_toggle_row("docgraph.daemon.auto_start",   "Auto-start",   "Spawn at boot if Enabled."))
    dbody.addWidget(_toggle_row("docgraph.daemon.auto_restart", "Auto-restart", "Re-spawn on unexpected exit."))
    dbody.addWidget(_number_row("docgraph.daemon.port", "Port", 1024, 65535, 1, 0,
                                  "", "Loopback only. Default 5577."))
    dbody.addWidget(_line_row("docgraph.daemon.model", "Model",
                                "BAAI/bge-small-en-v1.5",
                                "Must match what your repos were indexed with."))
    dbody.addWidget(_toggle_row("docgraph.daemon.gpu", "GPU", "Loads on GPU via ONNX Runtime."))

    drow = QHBoxLayout()
    dstart = QPushButton("▶ Start"); dstart.setProperty("class", "primary")
    dstop  = QPushButton("Stop");    dstop.setProperty("class", "danger")
    drow.addWidget(dstart); drow.addWidget(dstop); drow.addStretch(1)
    dbody.addLayout(drow)

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

    layout.addWidget(dcard)

    tail = _make_log_tail(lambda: _role_log_path("daemon"))
    layout.addWidget(tail, 1)

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
        try:
            tail.refresh()
        except Exception:
            pass
    scroll.refresh = refresh  # type: ignore[attr-defined]
    refresh()
    return scroll


# ── Generic role tab (Watch / Serve) ─────────────────────────────────────────

def _build_role_tab(window, *, role: str, title: str, sub: str,
                     rows: list[tuple], status_fn, log_path_fn, get_supervisor) -> QWidget:
    scroll, _, layout = _page()

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

    layout.addWidget(card)

    tail = _make_log_tail(log_path_fn)
    layout.addWidget(tail, 1)

    def refresh():
        refresh_status()
        try:
            tail.refresh()
        except Exception:
            pass
    scroll.refresh = refresh  # type: ignore[attr-defined]
    refresh()
    return scroll


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


def _role_log_path(role: str) -> str:
    try:
        from docgraph import config as dg_cfg
        return dg_cfg.log_path(role)
    except Exception:
        return ""
