"""DocGraph section builder — single sidebar entry, internal QTabWidget.

Tabs: Index / Watch / Serve / MCP. Daemon lives at the bottom of the MCP tab
since it pairs naturally with the MCP children (shared embedding daemon).

Each tab is form rows + Start/Stop/Restart + status pill. Live log tailing
is delegated to the global Logs section — it picks up `docgraph_<role>.log`
plus per-MCP-child `docgraph_mcp_<slug>.log` files automatically.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
)

from tray.qt_widgets import row_label
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

    binary_card, bb = _card("Binary")
    bb.addWidget(_line_row(
        "docgraph.binary",
        "Binary Path",
        "docgraph",
        "Path to docgraph executable. Bare name = use PATH. Empty = autodetect "
        "(which → settings-dir venv → ~/.local/bin → ~/.docgraph venv).",
    ))
    layout.addWidget(binary_card)

    tabs = QTabWidget()
    tabs.addTab(_build_index_tab(window), "Index")
    tabs.addTab(_build_watch_tab(window), "Watch")
    tabs.addTab(_build_serve_tab(window), "Serve")
    tabs.addTab(_build_mcp_tab(window),   "MCP")

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

    layout.addWidget(tabs)
    layout.addStretch(1)

    def refresh():
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


def _tab_page() -> tuple[QWidget, QVBoxLayout]:
    """Plain (non-scrolling) tab body. Outer page already scrolls."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(10)
    return page, layout


def _log_hint(basename: str) -> QLabel:
    lbl = QLabel(f"📜 Live log → Logs section · file: <code>{basename}</code>")
    lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")
    lbl.setTextFormat(Qt.TextFormat.RichText)
    return lbl


# ── Index tab ────────────────────────────────────────────────────────────────

def _build_index_tab(window) -> QWidget:
    page, layout = _tab_page()

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
    body.addWidget(_log_hint("docgraph_index.log"))

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
    layout.addStretch(1)

    page.refresh = refresh_status  # type: ignore[attr-defined]
    return page


def _index_status_text() -> tuple[bool, str]:
    try:
        from docgraph.process import get_index
        s = get_index().status()
    except Exception as exc:
        return False, f"err: {exc}"
    if s["alive"]:
        return True, f"running · {s.get('current_path') or '?'}"
    return False, f"last: {s.get('last_status', 'idle')}"


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
        log_basename="docgraph_watch.log",
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
        log_basename="docgraph_serve.log",
        get_supervisor=lambda: __import__("docgraph.process", fromlist=["get_serve"]).get_serve(),
    )

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

    if tab.layout() is not None:
        tab.layout().insertWidget(1, open_btn)

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
    page, layout = _tab_page()

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
    dbody.addWidget(_log_hint("docgraph_daemon.log"))

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
    layout.addStretch(1)

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
    page.refresh = refresh  # type: ignore[attr-defined]
    refresh()
    return page


# ── Generic role tab (Watch / Serve) ─────────────────────────────────────────

def _build_role_tab(window, *, role: str, title: str, sub: str,
                     rows: list[tuple], status_fn, log_basename: str,
                     get_supervisor) -> QWidget:
    page, layout = _tab_page()

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

    layout.addWidget(card)
    layout.addStretch(1)

    page.refresh = refresh_status  # type: ignore[attr-defined]
    refresh_status()
    return page


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
