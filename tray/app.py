"""Qt-based tray launcher. Runs in a daemon thread started by main.py.

Architecture:
  - Qt (QApplication, QSystemTrayIcon, SettingsWindow) lives on its own
    daemon thread. All GUI work happens there.
  - Telegram bot lives on the main thread (run_polling + asyncio loop).
  - Menu + window actions that need to call async code dispatch via
    asyncio.run_coroutine_threadsafe(coro, bot_loop).
  - Quit signals the bot via bot_app.stop_running() scheduled on the loop.

One entry point: `start_tray_in_thread(bot_app, bot_loop)`. Called from
main.py:_post_init once the Telegram application is up.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("telecode.tray")


def start_tray_in_thread(bot_app, bot_loop: asyncio.AbstractEventLoop) -> threading.Thread:
    """Spawn the Qt tray + window on a daemon thread. Returns the thread.
    The bot process keeps running on the main thread as usual."""
    def _go():
        try:
            _run_qt(bot_app, bot_loop)
        except Exception:
            log.exception("Qt thread crashed")
    t = threading.Thread(target=_go, daemon=True, name="telecode-qt")
    t.start()
    return t


def _run_qt(bot_app, bot_loop: asyncio.AbstractEventLoop) -> None:
    log.info("tray: Qt thread starting")
    # Import Qt only inside the thread that'll own it — keeps qApp bound here.
    from PySide6.QtCore import Qt, QCoreApplication
    from PySide6.QtGui import QIcon, QPixmap, QAction
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu

    from tray.qt_theme import QSS
    from tray.qt_window import SettingsWindow
    from tray import icon as icon_factory
    from tray.qt_helpers import schedule, patch_settings, read_settings, get_path, build_status, humanize, format_protocol

    # Qt wants high-DPI attributes set before QApplication is instantiated
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("telecode")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(QSS)

    # Main window (hidden at start — shown by tray click)
    window = SettingsWindow(bot_app, bot_loop)

    # Tray icon — render our PIL bolt to a QPixmap
    def _make_qicon() -> QIcon:
        pil = icon_factory.make_icon(size=64)
        from io import BytesIO
        buf = BytesIO()
        pil.save(buf, format="PNG")
        pm = QPixmap()
        # Auto-detect from PNG magic bytes — passing a format arg here triggers
        # a PySide6 overload-resolution bug on some versions ("called with
        # wrong argument values") even though the stubs allow bytes.
        pm.loadFromData(buf.getvalue())
        return QIcon(pm)

    tray = QSystemTrayIcon(_make_qicon(), app)
    tray.setToolTip("telecode")

    menu = QMenu()
    menu.setStyleSheet(QSS)

    def _sched(coro_factory):
        try:
            schedule(bot_loop, coro_factory())
        except Exception as exc:
            log.warning("schedule: %s", exc)

    def _toggle_and_reload(path: str, value):
        try:
            patch_settings(path, value)
        except Exception as exc:
            log.error("patch %s failed: %s", path, exc)

    # ── Llama submenu ────────────────────────────────────────────────
    llama_menu = menu.addMenu("⬡ Llama")
    llama_header = QAction("Disabled", llama_menu); llama_header.setEnabled(False)
    llama_model  = QAction("Model: —", llama_menu); llama_model.setEnabled(False)
    llama_menu.addAction(llama_header)
    llama_menu.addAction(llama_model)
    llama_menu.addSeparator()
    llama_autostart = QAction("Auto Start", llama_menu); llama_autostart.setCheckable(True)
    llama_autostart.triggered.connect(lambda checked: _toggle_and_reload("llamacpp.auto_start", bool(checked)))
    llama_menu.addAction(llama_autostart)
    llama_menu.addSeparator()
    load_action    = QAction("Load Now",   llama_menu)
    unload_action  = QAction("Unload",     llama_menu)
    restart_action = QAction("Restart",    llama_menu)
    def _load():
        async def _do():
            from process import get_supervisor
            from llamacpp import config as cfg
            sup = await get_supervisor()
            await sup.ensure_model(cfg.default_model())
        _sched(_do)
    def _unload():
        async def _do():
            from process import get_supervisor
            sup = await get_supervisor()
            await sup.stop()
        _sched(_do)
    def _restart():
        async def _do():
            from process import get_supervisor
            sup = await get_supervisor()
            await sup.stop()
            await sup.start_default()
        _sched(_do)
    load_action.triggered.connect(_load)
    unload_action.triggered.connect(_unload)
    restart_action.triggered.connect(_restart)
    llama_menu.addAction(load_action)
    llama_menu.addAction(unload_action)
    llama_menu.addAction(restart_action)

    # ── Proxy submenu ────────────────────────────────────────────────
    proxy_menu = menu.addMenu("⬡ Proxy")
    proxy_header = QAction("Disabled", proxy_menu); proxy_header.setEnabled(False)
    proxy_protos = QAction("Protocols: —", proxy_menu); proxy_protos.setEnabled(False)
    proxy_menu.addAction(proxy_header)
    proxy_menu.addAction(proxy_protos)
    proxy_menu.addSeparator()
    proxy_enabled = QAction("Enabled (restart required)", proxy_menu); proxy_enabled.setCheckable(True)
    proxy_enabled.triggered.connect(lambda checked: _toggle_and_reload("proxy.enabled", bool(checked)))
    proxy_menu.addAction(proxy_enabled)
    proxy_debug = QAction("Debug Dumps", proxy_menu); proxy_debug.setCheckable(True)
    proxy_debug.triggered.connect(lambda checked: _toggle_and_reload("proxy.debug", bool(checked)))
    proxy_menu.addAction(proxy_debug)

    # ── MCP submenu ──────────────────────────────────────────────────
    mcp_menu = menu.addMenu("⬡ MCP")
    mcp_header = QAction("Disabled", mcp_menu); mcp_header.setEnabled(False)
    mcp_tools  = QAction("Tools: 0", mcp_menu); mcp_tools.setEnabled(False)
    mcp_menu.addAction(mcp_header)
    mcp_menu.addAction(mcp_tools)
    mcp_menu.addSeparator()
    mcp_enabled = QAction("Enabled (restart required)", mcp_menu); mcp_enabled.setCheckable(True)
    mcp_enabled.triggered.connect(lambda checked: _toggle_and_reload("mcp_server.enabled", bool(checked)))
    mcp_menu.addAction(mcp_enabled)

    # ── Bot submenu ──────────────────────────────────────────────────
    bot_menu = menu.addMenu("⬢ Bot")
    bot_sessions = QAction("Sessions: 0 / 0", bot_menu); bot_sessions.setEnabled(False)
    bot_group    = QAction("Group: —",        bot_menu); bot_group.setEnabled(False)
    bot_users    = QAction("Allowed Users: 0", bot_menu); bot_users.setEnabled(False)
    bot_menu.addAction(bot_sessions)
    bot_menu.addAction(bot_group)
    bot_menu.addAction(bot_users)

    menu.addSeparator()

    # Open settings (default left-click action)
    open_settings_action = QAction("Open Settings Window", menu)
    open_settings_action.triggered.connect(window.toggle_visibility)
    menu.addAction(open_settings_action)
    menu.setDefaultAction(open_settings_action)

    def _open_ui():
        import webbrowser
        settings = read_settings()
        host = get_path(settings, "proxy.host", "127.0.0.1")
        if host == "0.0.0.0": host = "127.0.0.1"
        port = get_path(settings, "proxy.port", 1235)
        webbrowser.open(f"http://{host}:{port}/ui")

    open_ui_action = QAction("Open Agent Manager (Browser)", menu)
    open_ui_action.triggered.connect(_open_ui)
    menu.addAction(open_ui_action)

    def _open_docgraph_ui():
        import webbrowser
        settings = read_settings()
        host = get_path(settings, "docgraph.host.host", "127.0.0.1") or "127.0.0.1"
        if host == "0.0.0.0":
            host = "127.0.0.1"
        port = get_path(settings, "docgraph.host.port", 5500) or 5500
        webbrowser.open(f"http://{host}:{port}")

    open_docgraph_action = QAction("Open Document Index (Browser)", menu)
    open_docgraph_action.triggered.connect(_open_docgraph_ui)
    open_docgraph_action.setVisible(False)
    menu.addAction(open_docgraph_action)

    menu.addSeparator()

    quit_action = QAction("Quit Telecode", menu)
    def _quit():
        log.info("tray: quit requested")
        # Ask PTB to stop — this returns run_polling on the main thread,
        # which triggers _post_shutdown (stops proxy + supervisor + funnels).
        try:
            schedule(bot_loop, bot_app.stop_running())
        except Exception as exc:
            log.warning("stop_running: %s", exc)
        # Tear down Qt on this thread
        tray.hide()
        app.quit()
        # Hard-stop watchdog: if graceful shutdown is still hanging after 5 s
        # (stuck HTTP long-poll, blocked await in _post_shutdown, deadlock,
        # …) force-exit. os._exit() releases our Windows Job Object handle,
        # so every child process bound via proc_group (llama-server, Tailscale
        # funnels, PTY-driven CLIs) is kernel-reaped automatically.
        def _nuke() -> None:
            log.warning("tray: graceful shutdown timed out — forcing os._exit(0)")
            os._exit(0)
        threading.Timer(5.0, _nuke).start()
    quit_action.triggered.connect(_quit)
    menu.addAction(quit_action)

    tray.setContextMenu(menu)

    # Left click → toggle window
    def _on_activated(reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # left click
            window.toggle_visibility()
    tray.activated.connect(_on_activated)

    # ── Live menu labels ─────────────────────────────────────────────
    from PySide6.QtCore import QTimer
    menu_timer = QTimer()
    menu_timer.setInterval(2000)

    def _refresh_info():
        st = build_status()
        llama = st.get("llama") or {}
        proxy = st.get("proxy") or {}
        mcp   = st.get("mcp")   or {}
        sessions = st.get("sessions") or []
        settings = read_settings()

        # ── Llama ────────────────────────────────────────────────────
        alive_bool = bool(llama.get("alive"))
        llama_enabled = bool(llama.get("enabled"))
        if llama_enabled:
            llama_menu.setTitle(f"⬢ Llama: {llama.get('active_model') or 'Idle'}" if alive_bool else "⬡ Llama: Idle")
            llama_header.setText("Running" if alive_bool else "Idle")
        else:
            llama_menu.setTitle("⬡ Llama: Disabled")
            llama_header.setText("Disabled")
        llama_model.setText(f"Model: {llama.get('active_model') or llama.get('default_model') or '—'}")
        llama_autostart.blockSignals(True)
        llama_autostart.setChecked(bool(get_path(settings, "llamacpp.auto_start", False)))
        llama_autostart.blockSignals(False)
        load_action.setEnabled(llama_enabled and not alive_bool)
        unload_action.setEnabled(llama_enabled and alive_bool)
        restart_action.setEnabled(llama_enabled and alive_bool)

        # ── Proxy ────────────────────────────────────────────────────
        if proxy.get("enabled"):
            protos = ", ".join(format_protocol(p) for p in proxy.get("protocols", []))
            proxy_menu.setTitle(f"⬢ Proxy: :{proxy.get('port', '?')}")
            proxy_header.setText(f"Port :{proxy.get('port', '?')}")
            proxy_protos.setText(f"Protocols: {protos or '—'}")
        else:
            proxy_menu.setTitle("⬡ Proxy: Disabled")
            proxy_header.setText("Disabled")
            proxy_protos.setText("Protocols: —")
        proxy_enabled.blockSignals(True)
        proxy_enabled.setChecked(bool(get_path(settings, "proxy.enabled", False)))
        proxy_enabled.blockSignals(False)
        proxy_debug.blockSignals(True)
        proxy_debug.setChecked(bool(get_path(settings, "proxy.debug", False)))
        proxy_debug.blockSignals(False)

        # ── MCP ──────────────────────────────────────────────────────
        if mcp.get("enabled"):
            n_tools = len(mcp.get("registered_tools", []))
            mcp_menu.setTitle(f"⬢ MCP: :{mcp.get('port', '?')}")
            mcp_header.setText(f"Port :{mcp.get('port', '?')}")
            mcp_tools.setText(f"Tools: {n_tools}")
        else:
            mcp_menu.setTitle("⬡ MCP: Disabled")
            mcp_header.setText("Disabled")
            mcp_tools.setText("Tools: 0")
        mcp_enabled.blockSignals(True)
        mcp_enabled.setChecked(bool(get_path(settings, "mcp_server.enabled", False)))
        mcp_enabled.blockSignals(False)

        # ── Bot ──────────────────────────────────────────────────────
        alive = sum(1 for s in sessions if s.get("alive"))
        bot_menu.setTitle(f"⬢ Bot: {alive} / {len(sessions)}")
        bot_sessions.setText(f"Sessions: {alive} / {len(sessions)}")
        group_id = get_path(settings, "telegram.group_id", "—")
        bot_group.setText(f"Group: {group_id}")
        allowed = get_path(settings, "telegram.allowed_user_ids", []) or []
        bot_users.setText(f"Allowed Users: {len(allowed)}")

        # ── DocGraph: surface the "Open UI" link only when host is alive ──
        try:
            from docgraph.process import status_snapshot as _dg_status
            dg = _dg_status()
            host_alive = bool(dg.get("host", {}).get("alive"))
            open_docgraph_action.setVisible(host_alive)
            if host_alive:
                p = dg.get("host", {}).get("port") or get_path(settings, "docgraph.host.port", 5500)
                open_docgraph_action.setText(f"Open Document Index (Browser) — :{p}")
        except Exception:
            open_docgraph_action.setVisible(False)

        # Tray hover tooltip — newline-separated so every piece of info
        # is visible at once on hover instead of one collapsed line.
        tooltip_lines: list[str] = ["telecode"]
        if llama.get("enabled"):
            if alive_bool and llama.get("active_model"):
                tooltip_lines.append(f"Llama: {llama['active_model']} (alive)")
            else:
                tooltip_lines.append("Llama: stopped")
        if proxy.get("enabled"):
            tooltip_lines.append(
                f"Proxy :{proxy.get('port', '?')} · {', '.join(proxy.get('protocols') or [])}"
            )
        if mcp.get("enabled"):
            n_tools = len(mcp.get("registered_tools", []))
            tooltip_lines.append(f"MCP :{mcp.get('port', '?')} · {n_tools} tools")
        tooltip_lines.append(f"Sessions: {alive} / {len(sessions)}")
        tray.setToolTip("\n".join(tooltip_lines))

    menu_timer.timeout.connect(_refresh_info)
    menu_timer.start()
    _refresh_info()

    tray.show()
    log.info("tray: icon shown (visible=%s, supported=%s)",
             tray.isVisible(), QSystemTrayIcon.isSystemTrayAvailable())

    # Block this thread on the Qt event loop
    app.exec()
    log.info("tray: Qt loop exited")
