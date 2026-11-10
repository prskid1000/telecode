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
BG          = "#0c0c0c"
BG_ELEV     = "#121412"
BG_CARD     = "#171a17"
BG_ROW      = "#1e231e"
BG_HOVER    = "#232a23"
FG          = "#dde3dc"
FG_DIM      = "#8a968a"
FG_MUTE     = "#566156"
ACCENT      = "#8fbf7f"
ACCENT_2    = "#9fd48c"
WARN        = "#e0ce8a"
ERR         = "#dc9089"
OK          = "#9fd48c"
BORDER      = "#252b25"
BORDER_SOFT = "#1b1f1b"


QSS = f"""
* {{
    color: {FG};
    font-family: "Inter", "Segoe UI Variable", "Segoe UI", -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
    font-size: 13px;
}}

QWidget#window_root {{
    background: {BG};
}}

/* Menus (tray + context) */
QMenu {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px;
    color: {FG};
    font-size: 12.5px;
}}
QMenu::item {{
    padding: 7px 22px 7px 12px;
    border-radius: 4px;
    margin: 1px 2px;
}}
QMenu::item:selected {{ background: {BG_ROW}; }}
QMenu::item:disabled {{ color: {FG_MUTE}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}

/* Sidebar */
QListWidget#sidebar {{
    background: {BG_ELEV};
    border: none;
    border-right: 1px solid {BORDER_SOFT};
    outline: 0;
    padding: 10px 6px;
    font-size: 13px;
}}
QListWidget#sidebar::item {{
    padding: 9px 14px;
    border-radius: 6px;
    color: {FG_DIM};
    margin-bottom: 2px;
    min-height: 22px;
}}
QListWidget#sidebar::item:hover {{
    background: {BG_HOVER};
    color: {FG};
}}
QListWidget#sidebar::item:selected {{
    background: {BG_CARD};
    color: {FG};
    border-left: 2px solid {ACCENT};
    font-weight: 500;
}}

/* Titlebar */
QWidget#titlebar {{
    background: {BG};
    border-bottom: 1px solid {BORDER_SOFT};
}}
QLabel#titlebar_title {{
    font-weight: 600;
    font-size: 13px;
    color: {FG};
    letter-spacing: 0.02em;
}}
QLabel#titlebar_icon {{ font-size: 14px; }}
QPushButton.tb_btn {{
    background: transparent;
    border: none;
    color: {FG_DIM};
    padding: 0 16px;
    font-size: 13px;
    min-height: 34px;
    max-height: 34px;
}}
QPushButton.tb_btn:hover {{ background: {BG_HOVER}; color: {FG}; }}
QPushButton.tb_close:hover {{ background: #b5524b; color: white; }}

/* Scroll area + content */
QScrollArea {{ background: {BG}; border: none; }}
QWidget#content {{ background: {BG}; }}

/* Cards */
QFrame.card {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame.patched {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-left: 3px solid {ACCENT_2};
    border-radius: 8px;
}}
QLabel.patched_badge {{
    font-size: 9px;
    font-weight: 700;
    color: {BG};
    background: {ACCENT_2};
    border-radius: 3px;
    padding: 1px 6px;
    letter-spacing: 0.04em;
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
    background: #0f110f;
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
    background: #101310;
}}

QComboBox {{
    background: #0f110f;
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
QPushButton.primary:hover {{ background: #a3cc93; }}
QPushButton.primary:disabled {{
    background: {BG_ROW}; color: {FG_MUTE}; border: 1px solid {BORDER};
}}
QPushButton.danger {{ color: {ERR}; }}
QPushButton.danger:hover {{
    background: rgba(220, 144, 137, 25);
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
QLabel.stat_pill_ok  {{ color: {OK};  border-color: rgba(159, 212, 140, 80); }}
QLabel.stat_pill_err {{ color: {ERR}; border-color: rgba(220, 144, 137, 80); }}

/* Compact stat chip — paired with .stat_pill in the docgraph row strip.
   Smaller padding, monospace digits, used for live count badges. */
QLabel.stat_chip {{
    background: rgba(143, 191, 127, 18);
    border: 1px solid rgba(143, 191, 127, 60);
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 10.5px;
    font-family: "JetBrains Mono", Consolas, "Courier New", monospace;
    color: {FG};
    letter-spacing: 0.02em;
}}
QLabel.stat_chip[muted="true"] {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    color: {FG_MUTE};
}}

/* Progress bars — sleek, slim, gradient-filled. Two color variants
   keyed off a `kind` dynamic property: idx (accent blue) and wiki
   (accent teal). Uses a gradient chunk so the bar reads as a
   first-class status surface, not a stock Qt control. */
QProgressBar {{
    background: #0a0b0a;
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 0;
    text-align: center;
    color: {FG};
    font-family: "JetBrains Mono", Consolas, "Courier New", monospace;
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.02em;
    min-height: 18px;
    max-height: 18px;
}}
QProgressBar::chunk {{
    border-radius: 4px;
    margin: 1px;
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 rgba(143, 191, 127, 220),
        stop: 1 rgba(159, 212, 140, 220)
    );
}}
QProgressBar[kind="idx"]::chunk {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0   rgba(143, 191, 127, 235),
        stop: 0.5 rgba(163, 204, 147, 235),
        stop: 1   rgba(184, 214, 172, 235)
    );
}}
QProgressBar[kind="wiki"]::chunk {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0   rgba(159, 212, 140, 235),
        stop: 0.5 rgba(178, 222, 162, 235),
        stop: 1   rgba(198, 232, 186, 235)
    );
}}
QProgressBar[state="idle"]::chunk {{
    background: transparent;
}}
QProgressBar[state="idle"] {{
    color: {FG_MUTE};
    border: 1px dashed {BORDER};
}}

QLabel.toggle_label {{ color: {FG}; font-size: 12px; }}

/* Scrollbars — visible on dark backgrounds. Vertical was 8px on a
   transparent background with a #252b25 handle that sat barely above
   card bg; basically invisible. Widened + higher-contrast handle,
   and a matching horizontal rule so h-scroll actually renders. */
QScrollBar:vertical {{
    background: {BG_ELEV}; width: 12px; margin: 2px 0; border: none;
}}
QScrollBar::handle:vertical {{
    background: #3a4d3a; border-radius: 5px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: #647c64; }}
QScrollBar:horizontal {{
    background: {BG_ELEV}; height: 12px; margin: 0 2px; border: none;
}}
QScrollBar::handle:horizontal {{
    background: #3a4d3a; border-radius: 5px; min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: #647c64; }}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0; height: 0; background: transparent;
}}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* Tables */
QTableWidget {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    gridline-color: {BORDER_SOFT};
    selection-background-color: {BG_ROW};
    alternate-background-color: #191c19;
    font-size: 11.5px;
}}
QHeaderView {{
    background: {BG_ELEV};
    border: none;
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
