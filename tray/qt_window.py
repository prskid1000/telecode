"""Main settings window — frameless, dark-themed, sidebar + content.

Widget tree:
    QWidget#window_root (main fixed container)
      ├── QWidget#titlebar (custom drag handle + window buttons)
      └── QHBoxLayout
            ├── QListWidget#sidebar
            └── QScrollArea (content; QWidget#content inside)

Navigation:
    Sidebar row → loads the matching section widget from qt_sections.
    Save bar at bottom of each section collects dirty patches and
    applies them via settings patch + config.reload on save.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QPoint, Signal, QTimer
from PySide6.QtGui import QIcon, QPixmap, QMouseEvent
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QScrollArea, QFrame, QPushButton, QStackedWidget,
)

from tray.qt_theme import QSS, BG
from tray import qt_sections as sections
from tray.qt_helpers import read_settings, patch_settings

log = logging.getLogger("telecode.tray.window")


SECTIONS: list[tuple[str, str, str]] = [
    # (id, label, icon-char)
    ("status",   "Status",     "⚡"),
    ("llama",    "llama.cpp",  "🤖"),
    ("models",   "Models",     "📦"),
    ("proxy",    "Proxy",      "🔀"),
    ("mcp",      "MCP",        "🧩"),
    ("managed",  "Managed",    "🛠"),
    ("docgraph", "DocGraph",   "🧠"),
    ("tools",    "Tools",      "🧰"),
    ("telegram", "Telegram",   "💬"),
    ("voice",    "Voice",      "🎙"),
    ("computer", "Computer",   "🖥"),
    ("sessions", "Sessions",   "📡"),
    ("requests", "Requests",   "📨"),
    ("logs",     "Logs",       "📜"),
    ("raw",      "Raw",        "📄"),
]


class TitleBar(QWidget):
    """Custom frameless titlebar: drag region + minimize / maximize / close."""
    minimize_clicked = Signal()
    maximize_clicked = Signal()
    close_clicked    = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("titlebar")
        self.setFixedHeight(34)
        self._drag_pos: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(8)

        icon = QLabel("⚡")
        icon.setObjectName("titlebar_icon")
        layout.addWidget(icon)
        title = QLabel("telecode")
        title.setObjectName("titlebar_title")
        layout.addWidget(title)
        layout.addStretch(1)

        for text, sig, extra_class in [
            ("─", self.minimize_clicked, ""),
            ("▢", self.maximize_clicked, ""),
            ("✕", self.close_clicked,    "tb_close"),
        ]:
            btn = QPushButton(text)
            btn.setProperty("class", f"tb_btn {extra_class}".strip())
            btn.setFixedHeight(34)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(sig.emit)
            layout.addWidget(btn)

    # drag-to-move
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(e.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(e)


class SettingsWindow(QMainWindow):
    """Frameless settings window — always hidden on start, shown by
    QSystemTrayIcon's click.

    Args:
        bot_app: the python-telegram-bot Application (used to call
                 stop_running on Quit from the window)
        bot_loop: the bot's asyncio loop (for scheduling async calls)
    """

    def __init__(self, bot_app, bot_loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._app = bot_app
        self._loop = bot_loop

        self.setWindowTitle("telecode")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.resize(1100, 720)

        # Root
        root = QWidget()
        root.setObjectName("window_root")
        root.setStyleSheet(QSS)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Titlebar
        self._titlebar = TitleBar(root)
        self._titlebar.minimize_clicked.connect(self.showMinimized)
        self._titlebar.maximize_clicked.connect(self._toggle_maximize)
        self._titlebar.close_clicked.connect(self.hide)
        root_layout.addWidget(self._titlebar)

        # Body (sidebar | content)
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self._sidebar = QListWidget()
        self._sidebar.setObjectName("sidebar")
        self._sidebar.setFixedWidth(220)
        for sid, label, icon in SECTIONS:
            item = QListWidgetItem(f"  {icon}    {label}")
            item.setData(Qt.ItemDataRole.UserRole, sid)
            self._sidebar.addItem(item)
        self._sidebar.currentRowChanged.connect(self._on_section_changed)

        self._stack = QStackedWidget()
        self._page_cache: dict[str, QWidget] = {}

        body_layout.addWidget(self._sidebar)
        body_layout.addWidget(self._stack, 1)

        root_layout.addWidget(body, 1)
        self.setCentralWidget(root)

        # Restore last-viewed section (persisted under `tray.last_section`).
        # Falls back to row 0 if the saved id is no longer present.
        last = str(read_settings().get("tray", {}).get("last_section", "") or "")
        initial_row = 0
        if last:
            for i, (sid, *_rest) in enumerate(SECTIONS):
                if sid == last:
                    initial_row = i
                    break
        self._sidebar.setCurrentRow(initial_row)

        # Live status refresh (1s) — drives sections that care (Status / Sessions)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._tick)
        self._status_timer.start()

    # ── Public helpers ───────────────────────────────────────────────

    @property
    def bot_loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    @property
    def bot_app(self):
        return self._app

    def toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    # ── Internals ────────────────────────────────────────────────────

    def _toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _on_section_changed(self, row: int) -> None:
        if row < 0 or row >= len(SECTIONS):
            return
        sid, label, _icon = SECTIONS[row]
        if sid not in self._page_cache:
            self._page_cache[sid] = sections.build(sid, self)
            self._stack.addWidget(self._page_cache[sid])
        self._stack.setCurrentWidget(self._page_cache[sid])
        # Persist last-viewed section so the next launch lands on the same
        # page. Best-effort — settings write failures shouldn't break nav.
        try:
            patch_settings("tray.last_section", sid)
        except Exception:
            pass

    def _tick(self) -> None:
        """Let each loaded section refresh itself if it wants to."""
        for page in self._page_cache.values():
            refresh = getattr(page, "refresh", None)
            if callable(refresh):
                try:
                    refresh()
                except Exception:
                    pass
