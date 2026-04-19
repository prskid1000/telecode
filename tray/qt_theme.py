"""Dark theme QSS for the telecode settings window + tray menu.

Single source of truth. Applied at QApplication level so it cascades
to QMenu (system tray), QMainWindow, and every child widget.
"""
from __future__ import annotations

# Base palette — pulled from the same tones we used in the old web UI so
# the vibe is consistent with screenshots in the README.
BG          = "#0d1016"
BG_ELEV     = "#141924"
BG_CARD     = "#1a202e"
BG_ROW      = "#1e2536"
FG          = "#e6ebf2"
FG_DIM      = "#94a2b8"
FG_MUTE     = "#5b6a82"
ACCENT      = "#6ba4ff"
ACCENT_2    = "#56e0c2"
WARN        = "#f5a524"
ERR         = "#ff6e6e"
OK          = "#56e0c2"
BORDER      = "#232c3e"


QSS = f"""
* {{
    color: {FG};
    font-family: "Segoe UI", Inter, Arial, sans-serif;
    font-size: 13px;
}}

QWidget#window_root {{
    background: {BG};
}}

QMenu {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px;
    color: {FG};
}}
QMenu::item {{
    padding: 6px 22px 6px 12px;
    border-radius: 4px;
    margin: 1px 2px;
}}
QMenu::item:selected {{
    background: {BG_ROW};
}}
QMenu::item:disabled {{
    color: {FG_MUTE};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 4px 6px;
}}
QMenu::indicator {{
    width: 14px;
    height: 14px;
    margin-left: 4px;
}}

/* Sidebar */
QListWidget#sidebar {{
    background: {BG_ELEV};
    border: none;
    outline: 0;
    padding: 10px 6px;
    font-size: 13.5px;
}}
QListWidget#sidebar::item {{
    padding: 7px 10px;
    border-radius: 6px;
    color: {FG_DIM};
    margin-bottom: 2px;
}}
QListWidget#sidebar::item:hover {{
    background: {BG_CARD};
    color: {FG};
}}
QListWidget#sidebar::item:selected {{
    background: {BG_CARD};
    color: {FG};
    border-left: 2px solid {ACCENT};
}}

/* Titlebar */
QWidget#titlebar {{
    background: {BG};
    border-bottom: 1px solid {BORDER};
}}
QLabel#titlebar_title {{
    font-weight: 500;
    font-size: 13px;
    color: {FG};
}}
QLabel#titlebar_icon {{
    font-size: 16px;
}}
QPushButton.tb_btn {{
    background: transparent;
    border: none;
    color: {FG_DIM};
    padding: 0 16px;
    font-size: 14px;
    min-height: 32px;
}}
QPushButton.tb_btn:hover {{
    background: {BG_ROW};
    color: {FG};
}}
QPushButton.tb_close:hover {{
    background: #e81123;
    color: white;
}}

/* Content area */
QScrollArea {{
    background: {BG};
    border: none;
}}
QWidget#content {{
    background: {BG};
}}

/* Section cards */
QFrame.card {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QLabel.card_title {{
    font-size: 15px;
    font-weight: 600;
    color: {FG};
}}
QLabel.card_sub {{
    font-size: 12px;
    color: {FG_DIM};
}}
QLabel.section_header {{
    font-size: 11px;
    font-weight: 600;
    color: {FG_MUTE};
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-top: 4px;
}}

/* Row label / help text */
QLabel.row_label {{
    color: {FG};
    font-size: 13px;
}}
QLabel.row_help {{
    color: {FG_MUTE};
    font-size: 11.5px;
}}
QLabel.key_path {{
    color: {FG_MUTE};
    font-family: "JetBrains Mono", Consolas, monospace;
    font-size: 11px;
}}

/* Inputs */
QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: #11151f;
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    color: {FG};
    selection-background-color: {ACCENT};
    selection-color: {BG};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {ACCENT};
}}

QComboBox {{
    background: #11151f;
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 30px 5px 10px;
    color: {FG};
    min-height: 24px;
}}
QComboBox:hover, QComboBox:focus {{
    border: 1px solid {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {FG};
    selection-background-color: {BG_ROW};
    outline: 0;
    padding: 4px;
}}

/* Slider */
QSlider::groove:horizontal {{
    height: 4px;
    background: {BORDER};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {FG};
    width: 14px;
    margin-top: -5px;
    margin-bottom: -5px;
    border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{
    background: {ACCENT};
}}

/* Buttons */
QPushButton {{
    background: {BG_ROW};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 14px;
    color: {FG};
    font-size: 12.5px;
}}
QPushButton:hover {{
    border: 1px solid {ACCENT};
}}
QPushButton:disabled {{
    color: {FG_MUTE};
    background: {BG_CARD};
}}
QPushButton.primary {{
    background: {ACCENT};
    color: {BG};
    border: 1px solid {ACCENT};
    font-weight: 600;
}}
QPushButton.primary:hover {{
    background: #7db0ff;
}}
QPushButton.primary:disabled {{
    background: {BG_ROW};
    color: {FG_MUTE};
    border: 1px solid {BORDER};
}}
QPushButton.danger {{
    color: {ERR};
}}
QPushButton.danger:hover {{
    background: rgba(255, 110, 110, 25);
    border: 1px solid {ERR};
}}
QPushButton.ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {FG_DIM};
}}
QPushButton.ghost:hover {{
    color: {FG};
    border: 1px solid {BORDER};
}}

/* Status pills */
QLabel.stat_pill {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 20px;
    padding: 4px 12px;
    font-size: 11.5px;
    color: {FG_DIM};
}}
QLabel.stat_pill_ok {{
    color: {OK};
    border-color: {OK};
}}
QLabel.stat_pill_err {{
    color: {ERR};
    border-color: {ERR};
}}

/* Toggle labels */
QLabel.toggle_label {{
    color: {FG};
}}

/* Scrollbars */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
}}
QScrollBar::handle:vertical {{
    background: #232c3e;
    border-radius: 5px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background: #2a3147;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
}}

/* Save bar */
QFrame#save_bar {{
    background: {BG};
    border-top: 1px solid {BORDER};
}}
QLabel#save_bar_count {{
    color: {FG_DIM};
    font-size: 12px;
}}

/* Table (sessions) */
QTableWidget {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    gridline-color: {BORDER};
    selection-background-color: {BG_ROW};
    alternate-background-color: {BG_ROW};
}}
QHeaderView::section {{
    background: {BG_ELEV};
    color: {FG_MUTE};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 10px;
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}}
"""
