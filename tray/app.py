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
    # Import Qt only inside the thread that'll own it — keeps qApp bound here.
    from PySide6.QtCore import Qt, QCoreApplication
    from PySide6.QtGui import QIcon, QPixmap, QAction
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu

    from tray.qt_theme import QSS
    from tray.qt_window import SettingsWindow
    from tray import icon as icon_factory
    from tray.qt_helpers import schedule, patch_settings, read_settings, get_path, build_status

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
        pm.loadFromData(buf.getvalue(), "PNG")
        return QIcon(pm)

    tray = QSystemTrayIcon(_make_qicon(), app)
    tray.setToolTip("telecode")

    menu = QMenu()
    menu.setStyleSheet(QSS)

    # ── Live status info rows ────────────────────────────────────────
    info_llama = QAction("⬡ Llama: Disabled", menu); info_llama.setEnabled(False)
    info_proxy = QAction("⬡ Proxy: Disabled", menu); info_proxy.setEnabled(False)
    info_mcp   = QAction("⬡ MCP: Disabled",   menu); info_mcp.setEnabled(False)
    info_ses   = QAction("⬢ Sessions: 0 / 0", menu); info_ses.setEnabled(False)
    for a in (info_llama, info_proxy, info_mcp, info_ses):
        menu.addAction(a)
    menu.addSeparator()

    # Open settings (default left-click action)
    open_settings_action = QAction("Open Settings Window", menu)
    open_settings_action.triggered.connect(window.toggle_visibility)
    menu.addAction(open_settings_action)
    menu.setDefaultAction(open_settings_action)

    reload_action = QAction("Reload Config", menu)
    def _reload():
        import config as app_config
        try:
            app_config.reload()
        except Exception as exc:
            log.error("reload failed: %s", exc)
    reload_action.triggered.connect(_reload)
    menu.addAction(reload_action)

    menu.addSeparator()

    # llama quick actions
    load_action    = QAction("Load Llama Now", menu)
    unload_action  = QAction("Unload Llama",   menu)
    restart_action = QAction("Restart Llama",  menu)
    def _sched(coro_factory):
        try:
            schedule(bot_loop, coro_factory())
        except Exception as exc:
            log.warning("schedule: %s", exc)
    def _load():
        async def _do():
            from llamacpp.supervisor import get_supervisor
            from llamacpp import config as cfg
            sup = await get_supervisor()
            await sup.ensure_model(cfg.default_model())
        _sched(_do)
    def _unload():
        async def _do():
            from llamacpp.supervisor import get_supervisor
            sup = await get_supervisor()
            await sup.stop()
        _sched(_do)
    def _restart():
        async def _do():
            from llamacpp.supervisor import get_supervisor
            sup = await get_supervisor()
            await sup.stop()
            await sup.start_default()
        _sched(_do)
    load_action.triggered.connect(_load)
    unload_action.triggered.connect(_unload)
    restart_action.triggered.connect(_restart)
    menu.addAction(load_action)
    menu.addAction(unload_action)
    menu.addAction(restart_action)
    menu.addSeparator()

    # Open file helpers
    def _open_path(p: Path) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(str(p))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception as exc:
            log.warning("open %s: %s", p, exc)

    from tray.qt_helpers import settings_path as _sp
    open_sj = QAction("Open settings.json", menu)
    open_sj.triggered.connect(lambda: _open_path(_sp()))
    menu.addAction(open_sj)
    open_logs = QAction("Open Logs Folder", menu)
    open_logs.triggered.connect(lambda: _open_path(_sp().parent / "data" / "logs"))
    menu.addAction(open_logs)

    menu.addSeparator()

    quit_action = QAction("Quit Telecode", menu)
    def _quit():
        log.info("tray: quit requested")
        # Ask PTB to stop — this returns run_polling on the main thread
        try:
            schedule(bot_loop, bot_app.stop_running())
        except Exception as exc:
            log.warning("stop_running: %s", exc)
        # Tear down Qt on this thread
        tray.hide()
        app.quit()
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

        if llama.get("enabled"):
            info_llama.setText(
                f"⬢ Llama: {llama.get('active_model', '—')}"
                if llama.get("alive") else "⬡ Llama: Idle"
            )
        else:
            info_llama.setText("⬡ Llama: Disabled")
        if proxy.get("enabled"):
            from tray.qt_helpers import format_protocol
            protos = ", ".join(format_protocol(p) for p in proxy.get("protocols", []))
            info_proxy.setText(f"⬢ Proxy: :{proxy.get('port', '?')} · {protos}")
        else:
            info_proxy.setText("⬡ Proxy: Disabled")
        if mcp.get("enabled"):
            info_mcp.setText(f"⬢ MCP: :{mcp.get('port', '?')} · {len(mcp.get('registered_tools', []))} Tools")
        else:
            info_mcp.setText("⬡ MCP: Disabled")
        alive = sum(1 for s in sessions if s.get("alive"))
        info_ses.setText(f"⬢ Sessions: {alive} / {len(sessions)}")

        alive_bool = bool(llama.get("alive"))
        llama_enabled = bool(llama.get("enabled"))
        load_action.setEnabled(llama_enabled and not alive_bool)
        unload_action.setEnabled(llama_enabled and alive_bool)
        restart_action.setEnabled(llama_enabled and alive_bool)
        # tooltip
        bits = ["telecode"]
        if alive_bool and llama.get("active_model"):
            bits.append(llama["active_model"])
        tray.setToolTip(" · ".join(bits))

    menu_timer.timeout.connect(_refresh_info)
    menu_timer.start()
    _refresh_info()

    tray.show()

    # Block this thread on the Qt event loop
    app.exec()
    log.info("tray: Qt loop exited")
