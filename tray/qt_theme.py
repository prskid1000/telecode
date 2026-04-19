"""Dark theme QSS — sleek, rich, compact.

Design targets:
  - Information density: every panel packs 15-25% more rows than the
    previous layout by tightening padding and using two-column grids
    where the eye naturally groups fields (temp+top_p on one row etc.).
  - Subtle depth: cards have a 1px border + faint inner highlight;
    buttons have hover-only accents.
  - One dark palette — never follows Windows theme.
"""
from __future__ import annotations

# ── Palette ──────────────────────────────────────────────────────────
BG          = "#0c0f14"
BG_ELEV     = "#10141c"
BG_CARD     = "#151a24"
BG_ROW      = "#1a2030"
BG_HOVER    = "#1d2334"
FG          = "#e6ebf2"
FG_DIM      = "#8a96aa"
FG_MUTE     = "#4f5a70"
ACCENT      = "#6ba4ff"
ACCENT_2    = "#56e0c2"
WARN        = "#f5a524"
ERR         = "#ff6e6e"
OK          = "#56e0c2"
BORDER      = "#1e2636"
BORDER_SOFT = "#171d28"


QSS = f"""
* {{
    color: {FG};
    font-family: "Segoe UI", Inter, -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 12px;
}}

QWidget#window_root {{
    background: {BG};
}}

/* Menus (tray + context) */
QMenu {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px;
    color: {FG};
    font-size: 12px;
}}
QMenu::item {{
    padding: 5px 18px 5px 10px;
    border-radius: 3px;
    margin: 0 2px;
}}
QMenu::item:selected {{ background: {BG_ROW}; }}
QMenu::item:disabled {{ color: {FG_MUTE}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 3px 4px; }}

/* Sidebar */
QListWidget#sidebar {{
    background: {BG_ELEV};
    border: none;
    border-right: 1px solid {BORDER_SOFT};
    outline: 0;
    padding: 6px 4px;
    font-size: 12px;
}}
QListWidget#sidebar::item {{
    padding: 6px 10px;
    border-radius: 4px;
    color: {FG_DIM};
    margin-bottom: 1px;
}}
QListWidget#sidebar::item:hover {{
    background: {BG_HOVER};
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
    border-bottom: 1px solid {BORDER_SOFT};
}}
QLabel#titlebar_title {{
    font-weight: 500;
    font-size: 12px;
    color: {FG};
    letter-spacing: 0.01em;
}}
QLabel#titlebar_icon {{ font-size: 13px; }}
QPushButton.tb_btn {{
    background: transparent;
    border: none;
    color: {FG_DIM};
    padding: 0 14px;
    font-size: 13px;
    min-height: 30px;
    max-height: 30px;
}}
QPushButton.tb_btn:hover {{ background: {BG_HOVER}; color: {FG}; }}
QPushButton.tb_close:hover {{ background: #e81123; color: white; }}

/* Scroll area + content */
QScrollArea {{ background: {BG}; border: none; }}
QWidget#content {{ background: {BG}; }}

/* Cards */
QFrame.card {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QLabel.card_title {{
    font-size: 13px;
    font-weight: 600;
    color: {FG};
    letter-spacing: -0.01em;
}}
QLabel.card_sub {{
    font-size: 11px;
    color: {FG_DIM};
}}
QLabel.section_header {{
    font-size: 10px;
    font-weight: 600;
    color: {FG_MUTE};
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding-top: 2px;
}}

/* Row label / help text */
QLabel.row_label {{
    color: {FG};
    font-size: 12px;
}}
QLabel.row_help {{
    color: {FG_MUTE};
    font-size: 10.5px;
}}
QLabel.key_path {{
    color: {FG_MUTE};
    font-family: "JetBrains Mono", Consolas, "Courier New", monospace;
    font-size: 10px;
}}
QLabel.row_hint {{
    color: {FG_MUTE};
    font-size: 10.5px;
}}

/* Hover highlight on rows */
QWidget.row:hover {{ background: {BG_HOVER}; border-radius: 4px; }}

/* Inputs */
QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: #0d1118;
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 8px;
    color: {FG};
    selection-background-color: {ACCENT};
    selection-color: {BG};
    min-height: 20px;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {ACCENT};
    background: #0f141d;
}}

QComboBox {{
    background: #0d1118;
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 22px 4px 8px;
    color: {FG};
    min-height: 20px;
}}
QComboBox:hover, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 4px;
    color: {FG};
    selection-background-color: {BG_ROW};
    outline: 0;
    padding: 2px;
}}

/* Slider */
QSlider::groove:horizontal {{
    height: 3px;
    background: {BORDER};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {FG};
    width: 10px;
    margin-top: -4px;
    margin-bottom: -4px;
    border-radius: 5px;
}}
QSlider::handle:horizontal:hover {{ background: {ACCENT}; }}

/* Buttons */
QPushButton {{
    background: {BG_ROW};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 10px;
    color: {FG};
    font-size: 11.5px;
    min-height: 22px;
}}
QPushButton:hover {{ border: 1px solid {ACCENT}; background: {BG_HOVER}; }}
QPushButton:disabled {{ color: {FG_MUTE}; background: {BG_CARD}; }}
QPushButton.primary {{
    background: {ACCENT};
    color: {BG};
    border: 1px solid {ACCENT};
    font-weight: 600;
}}
QPushButton.primary:hover {{ background: #7db0ff; }}
QPushButton.primary:disabled {{
    background: {BG_ROW}; color: {FG_MUTE}; border: 1px solid {BORDER};
}}
QPushButton.danger {{ color: {ERR}; }}
QPushButton.danger:hover {{
    background: rgba(255, 110, 110, 25);
    border: 1px solid {ERR};
}}
QPushButton.ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {FG_DIM};
}}
QPushButton.ghost:hover {{ color: {FG}; border: 1px solid {BORDER}; }}

/* Status pill strip */
QLabel.stat_pill {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 11px;
    color: {FG_DIM};
}}
QLabel.stat_pill_ok  {{ color: {OK};  border-color: rgba(86, 224, 194, 80); }}
QLabel.stat_pill_err {{ color: {ERR}; border-color: rgba(255, 110, 110, 80); }}

QLabel.toggle_label {{ color: {FG}; font-size: 12px; }}

/* Scrollbars */
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px 0; }}
QScrollBar::handle:vertical {{
    background: #1e2636; border-radius: 4px; min-height: 18px;
}}
QScrollBar::handle:vertical:hover {{ background: #2a3147; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

/* Tables */
QTableWidget {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    gridline-color: {BORDER_SOFT};
    selection-background-color: {BG_ROW};
    alternate-background-color: #161c28;
    font-size: 11.5px;
}}
QHeaderView::section {{
    background: {BG_ELEV};
    color: {FG_MUTE};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 4px 8px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}}

/* Tool tips */
QToolTip {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    color: {FG};
    padding: 4px 8px;
    font-size: 11px;
}}
"""
