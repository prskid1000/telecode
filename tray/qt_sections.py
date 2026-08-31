"""Per-section widget builders for the settings window.

Each `build_<id>(window)` returns a QWidget. The window holds a cache so
sections are only built once. If a section defines a `refresh()` method,
the window calls it every 1s for live status.

Sections call helpers for settings patch + async dispatch.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

log = logging.getLogger("telecode.tray.qt_sections")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QGridLayout, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QLineEdit, QSpinBox, QSizePolicy, QProgressBar, QMessageBox,
)

from tray.qt_widgets import Toggle, NumberEditor, WrapLabel, row_label
from tray.qt_helpers import (
    read_settings, get_path, patch_settings, remove_path, schedule,
    humanize, format_protocol, build_status, settings_bus,
    tailscale_status, tailscale_funnel_url,
)
from tray.qt_theme import (
    FG, FG_DIM, FG_MUTE, BG, BG_ELEV, BG_CARD, BORDER, OK, WARN, ERR, ACCENT,
)


# ══════════════════════════════════════════════════════════════════════
# Common layout primitives
# ══════════════════════════════════════════════════════════════════════

def _page() -> tuple[QScrollArea, QWidget, QVBoxLayout]:
    """Scrollable page container with both v+h scrollbars AsNeeded.

    `setWidgetResizable(True)` on its own only gives vertical scroll;
    the content widget gets shrunk to fit horizontally so wide values
    (model paths, request previews, tool cmdlines) were getting elided.
    Combined with an explicit H-scrollbar policy the page now scrolls
    either direction when content exceeds the viewport."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    content = QWidget()
    content.setObjectName("content")
    layout = QVBoxLayout(content)
    layout.setContentsMargins(26, 22, 26, 22)
    layout.setSpacing(18)
    scroll.setWidget(content)
    return scroll, content, layout


def _card(title: str, sub: str = "") -> tuple[QFrame, QVBoxLayout]:
    """Card frame with header + body."""
    card = QFrame()
    card.setProperty("class", "card")
    outer = QVBoxLayout(card)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    head = QWidget()
    head_l = QVBoxLayout(head)
    head_l.setContentsMargins(18, 14, 18, 14)
    head_l.setSpacing(4)
    title_row = QHBoxLayout()
    title_row.setSpacing(10)
    t = QLabel(title)
    t.setProperty("class", "card_title")
    title_row.addWidget(t, 0, Qt.AlignmentFlag.AlignVCenter)
    title_row.addStretch(1)
    head_l.addLayout(title_row)
    if sub:
        s = WrapLabel(sub)
        s.setProperty("class", "card_sub")
        # WrapLabel defaults to vertical=MinimumExpanding so it fills any
        # leftover space in the parent layout. That's fine inside row help
        # text but inside _card it pushes the sub-title into the middle of
        # an empty header band when the body widget is tall (Logs viewer,
        # Requests splitter, Raw JSON editor). Pin it to content height.
        s.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Maximum)
        head_l.addWidget(s)
    # Same reason — head as a whole shouldn't grow vertically beyond its
    # natural size, regardless of body height.
    head.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    outer.addWidget(head)

    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setStyleSheet(f"color: {BORDER};")
    outer.addWidget(sep)

    body = QWidget()
    body_l = QVBoxLayout(body)
    body_l.setContentsMargins(18, 14, 18, 14)
    body_l.setSpacing(12)
    outer.addWidget(body, 1)
    return card, body_l


def _section_header(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("class", "section_header")
    return lbl


def _row(left: QWidget, right: QWidget) -> QWidget:
    """Two-column row: label | control.

    Label column has a soft cap (max 280) and a small floor (160) so
    the row can shrink along with the card on narrow windows instead
    of forcing the page to a horizontal scrollbar."""
    w = QWidget()
    l = QHBoxLayout(w)
    l.setContentsMargins(0, 0, 0, 0)
    l.setSpacing(14)
    left.setMinimumWidth(160)
    left.setMaximumWidth(280)
    l.addWidget(left, 0, Qt.AlignmentFlag.AlignTop)
    l.addWidget(right, 1)
    return w


def _toggle_row(path: str, label: str, help_text: str = "",
                enabled_fn: Callable[[], bool] | None = None,
                cli: str = "") -> QWidget:
    """Boolean toggle row. Writes settings.json + config.reload on change."""
    t = Toggle()
    t.setChecked(bool(get_path(read_settings(), path, False)))
    if enabled_fn:
        t.setEnabled(enabled_fn())

    def _on(_state: int) -> None:
        patch_settings(path, t.isChecked())
    t.stateChanged.connect(_on)

    return _row(row_label(label, help_text, path, cli),
                _wrap_align(t, Qt.AlignmentFlag.AlignLeft))


def _wrap_align(widget: QWidget, alignment) -> QWidget:
    w = QWidget()
    l = QHBoxLayout(w)
    l.setContentsMargins(0, 0, 0, 0)
    l.addWidget(widget, 0, alignment)
    l.addStretch(1)
    return w


def _number_row(path: str, label: str,
                minimum: float, maximum: float,
                step: float = 0.01, decimals: int = 2, unit: str = "",
                help_text: str = "", cli: str = "") -> QWidget:
    """Numeric row (text input + slider, linked)."""
    ne = NumberEditor(minimum, maximum, step, decimals, unit)
    cur = get_path(read_settings(), path, minimum)
    try:
        ne.setValue(float(cur))
    except (TypeError, ValueError):
        ne.setValue(float(minimum))
    ne.valueChanged.connect(lambda v: patch_settings(path, v if decimals > 0 else int(round(v))))
    # Same cap as _line_row — keep the slider track from spanning the
    # entire window on wide displays, but don't pinch it so tight that
    # the slider has nowhere to drag.
    ne.setMaximumWidth(720)
    return _row(row_label(label, help_text, path, cli), _wrap_align(ne, Qt.AlignmentFlag.AlignLeft))


def _enum_row(path: str, label: str, options: list[tuple[str, Any]],
              help_text: str = "", *, max_width: int | None = None) -> QWidget:
    """Dropdown row. options: list of (display, value)."""
    cb = QComboBox()
    cur = get_path(read_settings(), path)
    selected = 0
    for i, (disp, val) in enumerate(options):
        cb.addItem(disp, val)
        if cur == val:
            selected = i
    cb.setCurrentIndex(selected)
    cb.currentIndexChanged.connect(
        lambda i: patch_settings(path, cb.itemData(i))
    )
    if max_width is not None:
        cb.setMaximumWidth(max_width)
    return _row(row_label(label, help_text, path), _wrap_align(cb, Qt.AlignmentFlag.AlignLeft))


def _dependent(row: QWidget, parent_paths: list[str], predicate) -> QWidget:
    """Grey-out `row` whenever predicate(*current_parent_values) is False.

    Subscribes to the settings bus so the disabled state tracks live edits
    (made via the same tray UI, via patch_settings from anywhere, or via
    direct settings.json writes routed through the same helpers).

    `predicate` receives one positional arg per parent_path, in order.
    """

    def _evaluate() -> None:
        snap = read_settings()
        try:
            vals = [get_path(snap, p) for p in parent_paths]
            enabled = bool(predicate(*vals))
        except Exception:
            enabled = True
        row.setEnabled(enabled)

    bus = settings_bus()

    def _on_change(path: str) -> None:
        # Cheap match: re-evaluate only when one of our parents (or a
        # subpath of it) changed.
        for p in parent_paths:
            if path == p or path.startswith(p + ".") or p.startswith(path + "."):
                _evaluate()
                return

    bus.settingChanged.connect(_on_change)
    _evaluate()
    # Cache the disconnect lambda on the widget so it can be cleaned up by
    # Qt's garbage collector when the row is reparented away.
    row._dep_disconnect = lambda: bus.settingChanged.disconnect(_on_change)  # type: ignore[attr-defined]
    return row


def _has_spec(spec_value, *names: str) -> bool:
    """True if any of `names` is in the comma-separated spec_type value."""
    if not spec_value:
        return False
    active = {s.strip() for s in str(spec_value).split(",") if s.strip()}
    return any(n in active for n in names)


def _spec_value_is_active(spec_value) -> bool:
    """True iff spec_type is set to something other than empty/none."""
    raw = str(spec_value or "").strip().lower()
    if not raw:
        return False
    active = {s.strip() for s in raw.split(",") if s.strip()}
    return bool(active - {"none"})


_MUTEX_REGISTERED: set[tuple[str, ...]] = set()


def _mutex_bools(path_a: str, path_b: str) -> None:
    """Bind two boolean settings so turning one ON clears the other.

    Fires through the settings bus so the cross-clear works regardless of
    which UI control did the write. Idempotent — wires the listener at
    most once per (path_a, path_b) pair.
    """
    key = (path_a, path_b)
    if key in _MUTEX_REGISTERED:
        return
    _MUTEX_REGISTERED.add(key)

    bus = settings_bus()
    _busy = {"on": False}

    def _on_change(path: str) -> None:
        if _busy["on"]:
            return
        snap = read_settings()
        if path == path_a and bool(get_path(snap, path_a)):
            if bool(get_path(snap, path_b)):
                _busy["on"] = True
                try:
                    patch_settings(path_b, False)
                finally:
                    _busy["on"] = False
        elif path == path_b and bool(get_path(snap, path_b)):
            if bool(get_path(snap, path_a)):
                _busy["on"] = True
                try:
                    patch_settings(path_a, False)
                finally:
                    _busy["on"] = False

    bus.settingChanged.connect(_on_change)


def _mutex_spec_default_vs_type() -> None:
    """spec_default ↔ spec_type checkboxes: enabling one clears the other.

    - spec_default=true → write spec_type="none"
    - spec_type becomes non-empty/non-none → write spec_default=false
    """
    key = ("llamacpp.spec_default", "llamacpp.spec_type")
    if key in _MUTEX_REGISTERED:
        return
    _MUTEX_REGISTERED.add(key)

    bus = settings_bus()
    _busy = {"on": False}

    def _on_change(path: str) -> None:
        if _busy["on"]:
            return
        snap = read_settings()
        sd = bool(get_path(snap, "llamacpp.spec_default"))
        st = get_path(snap, "llamacpp.spec_type")
        if path == "llamacpp.spec_default" and sd and _spec_value_is_active(st):
            _busy["on"] = True
            try:
                patch_settings("llamacpp.spec_type", "none")
            finally:
                _busy["on"] = False
        elif path == "llamacpp.spec_type" and _spec_value_is_active(st) and sd:
            _busy["on"] = True
            try:
                patch_settings("llamacpp.spec_default", False)
            finally:
                _busy["on"] = False

    bus.settingChanged.connect(_on_change)


_SPEC_TYPE_OPTIONS: list[tuple[str, str, str]] = [
    # (key, display, tooltip)
    ("draft-simple",   "draft-simple",   "Standard speculative decoding with a small draft model."),
    ("draft-eagle3",   "draft-eagle3",   "EAGLE-3 tree-based draft. Needs an EAGLE-trained draft model."),
    ("draft-mtp",      "draft-mtp",      "Multi-Token Prediction heads built into the main model (Qwen3-Next / DeepSeek-V3). No draft model needed."),
    ("ngram-simple",   "ngram-simple",   "Prompt-lookup n-gram (cheapest; no draft model)."),
    ("ngram-map-k",    "ngram-map-k",    "Map-keyed n-gram variant."),
    ("ngram-map-k4v",  "ngram-map-k4v",  "Map-keyed n-gram with 4-value buckets."),
    ("ngram-mod",      "ngram-mod",      "Modular n-gram lookup, fits well alongside draft-mtp."),
    ("ngram-cache",    "ngram-cache",    "On-disk n-gram cache. Requires --lookup-cache-static/dynamic."),
]


def _spec_type_multi_row(path: str, label: str) -> QWidget:
    """Multi-select Spec Type. Serializes the selected checkboxes back to
    `settings[path]` as a comma-joined string (v9243+ --spec-type accepts a
    comma-separated list). Empty selection → "none"."""
    from PySide6.QtWidgets import QCheckBox, QGridLayout, QWidget as _W

    raw = str(get_path(read_settings(), path, "") or "").strip()
    selected: set[str] = set()
    if raw and raw.lower() != "none":
        selected = {s.strip() for s in raw.split(",") if s.strip()}

    host = _W()
    grid = QGridLayout(host)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(18)
    grid.setVerticalSpacing(4)

    boxes: list[tuple[str, QCheckBox]] = []

    def _commit() -> None:
        active = [k for k, cb in boxes if cb.isChecked()]
        patch_settings(path, ",".join(active) if active else "none")

    cols = 2
    for i, (key, disp, tip) in enumerate(_SPEC_TYPE_OPTIONS):
        cb = QCheckBox(disp)
        cb.setChecked(key in selected)
        cb.setToolTip(tip)
        cb.stateChanged.connect(lambda _s: _commit())
        boxes.append((key, cb))
        grid.addWidget(cb, i // cols, i % cols)

    help_text = ("--spec-type. Tick zero or more strategies — v9243+ stacks comma-separated values. "
                 "Tick nothing = 'none'. Use 'Spec Default' below to let the server auto-pick instead.")
    return _row(row_label(label, help_text, path, "--spec-type"), host)


def _idle_unload_row(path: str, default_sec: int = 300) -> QWidget:
    """Auto-unload composite: [Enabled] + [N s spinbox]. Stores one int:
        0          → disabled
        > 0        → unload after N seconds
    The last nonzero value is remembered across checkbox toggles so
    turning Auto-Unload off → on restores the previous duration."""
    from PySide6.QtWidgets import QCheckBox, QSpinBox, QWidget as _W
    cur = int(get_path(read_settings(), path, 0) or 0)
    w = _W()
    l = QHBoxLayout(w); l.setContentsMargins(0, 0, 0, 0); l.setSpacing(10)

    cb = QCheckBox("Auto-Unload")
    cb.setChecked(cur > 0)
    sp = QSpinBox()
    sp.setRange(1, 86400)
    sp.setSingleStep(30)
    sp.setSuffix(" s")
    sp.setEnabled(cur > 0)
    sp.setValue(cur if cur > 0 else default_sec)

    state = {"remembered": cur if cur > 0 else default_sec}

    def _on_spin(v: int) -> None:
        state["remembered"] = int(v)
        if cb.isChecked():
            patch_settings(path, int(v))
    sp.valueChanged.connect(_on_spin)

    def _on_cb(checked: bool) -> None:
        sp.setEnabled(checked)
        patch_settings(path, int(state["remembered"]) if checked else 0)
    cb.toggled.connect(_on_cb)

    l.addWidget(cb); l.addWidget(sp); l.addStretch(1)
    return w


# ══════════════════════════════════════════════════════════════════════
# Dispatch
# ══════════════════════════════════════════════════════════════════════

def build(section_id: str, window) -> QWidget:
    fn = _BUILDERS.get(section_id)
    if fn is None:
        return _placeholder(section_id)
    return fn(window)


def _placeholder(name: str) -> QWidget:
    scroll, _, layout = _page()
    layout.addWidget(QLabel(f"Section '{name}' — coming soon"))
    layout.addStretch(1)
    return scroll


# ══════════════════════════════════════════════════════════════════════
# Status
# ══════════════════════════════════════════════════════════════════════

def _status(window) -> QWidget:
    scroll, content, layout = _page()

    grid_card, grid_body = _card("Status", "Live state, updated every second")
    grid = QGridLayout()
    grid.setHorizontalSpacing(16)
    grid.setVerticalSpacing(16)
    grid_body.addLayout(grid)

    # 6 tiles in a 3-col grid: llama | proxy | docgraph / mcp | sessions | telegram
    specs = [
        ("llama",    "llama.cpp"),
        ("proxy",    "Proxy"),
        ("docgraph", "DocGraph"),
        ("mcp",      "MCP"),
        ("sessions", "Sessions"),
        ("telegram", "Telegram"),
    ]
    tiles: dict[str, _StatusTile] = {}
    for i, (key, label) in enumerate(specs):
        tile = _StatusTile(label)
        tiles[key] = tile
        grid.addWidget(tile, i // 3, i % 3)

    for c in range(3):
        grid.setColumnStretch(c, 1)

    layout.addWidget(grid_card)
    layout.addStretch(1)

    def refresh() -> None:
        st = build_status()
        _refresh_llama(tiles["llama"], st.get("llama", {}))
        _refresh_proxy(tiles["proxy"], st.get("proxy", {}))
        _refresh_docgraph(tiles["docgraph"], st.get("docgraph", {}))
        _refresh_mcp(tiles["mcp"], st.get("mcp", {}))
        _refresh_sessions(tiles["sessions"], st.get("sessions", []))
        _refresh_telegram(tiles["telegram"])

    scroll.refresh = refresh  # type: ignore[attr-defined]
    refresh()
    return scroll


# ── Status tile widget ────────────────────────────────────────────────────

class _StatusTile(QFrame):
    """One status card. Left accent bar + title + status dot + big value + sub text +
    a slot for an optional visualization (progress bar / chip strip / dots)."""

    _STATE_COLORS = {"ok": OK, "warn": WARN, "err": ERR, "mute": FG_MUTE}

    def __init__(self, title: str) -> None:
        super().__init__()
        self._state = "mute"
        self._apply_card_style()

        # Horizontal root: left bar | content
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Left accent bar (replaces top stripe — more modern dashboard feel)
        self._bar = QFrame()
        self._bar.setFixedWidth(4)
        self._bar.setStyleSheet(
            f"background: {FG_MUTE}; border-top-left-radius: 8px; border-bottom-left-radius: 8px;"
        )
        root.addWidget(self._bar)

        # Content
        body_w = QWidget()
        body = QVBoxLayout(body_w)
        body.setContentsMargins(14, 14, 16, 14)
        body.setSpacing(6)
        root.addWidget(body_w, 1)

        # Header row: TITLE · · · ● dot
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(0)
        self._title = QLabel(title.upper())
        self._title.setStyleSheet(
            f"color: {FG_MUTE}; font-size: 10px; letter-spacing: 1.5px; font-weight: 500;"
        )
        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color: {FG_MUTE}; font-size: 9px;")
        hdr.addWidget(self._title)
        hdr.addStretch(1)
        hdr.addWidget(self._dot)
        body.addLayout(hdr)

        # Value — larger and bolder
        self._value = QLabel("—")
        self._value.setStyleSheet(f"color: {FG}; font-size: 22px; font-weight: 600;")
        body.addWidget(self._value)

        self._sub = QLabel("")
        self._sub.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        self._sub.setWordWrap(True)
        body.addWidget(self._sub)

        # Visualization slot — populated by per-section refreshers.
        self._viz_host = QWidget()
        viz_layout = QVBoxLayout(self._viz_host)
        viz_layout.setContentsMargins(0, 4, 0, 0)
        viz_layout.setSpacing(4)
        body.addWidget(self._viz_host)

        body.addStretch(1)

    def _apply_card_style(self) -> None:
        # ok → solid green border; warn → orange; err → red; mute → dark grey
        _BORDERS = {
            "ok":   "rgba(86, 224, 194, 0.55)",   # OK  #56e0c2 at ~55%
            "warn": "rgba(245, 165, 36,  0.50)",   # WARN
            "err":  "rgba(255, 110, 110, 0.50)",   # ERR
            "mute": BORDER,
        }
        border = _BORDERS.get(self._state, BORDER)
        self.setStyleSheet(
            f"_StatusTile {{ background: {BG_CARD}; border: 1px solid {border}; "
            f"border-radius: 8px; }}"
        )

    def set_state(self, state: str) -> None:
        """state ∈ {'ok','warn','err','mute'} — drives bar + dot + card border."""
        self._state = state
        color = self._STATE_COLORS.get(state, FG_MUTE)
        self._bar.setStyleSheet(
            f"background: {color}; border-top-left-radius: 8px; border-bottom-left-radius: 8px;"
        )
        self._dot.setStyleSheet(f"color: {color}; font-size: 9px;")
        self._apply_card_style()

    def set_value(self, text: str) -> None:
        # Strip leading ●/○ — state is now shown via bar + dot
        if text and text[0] in "●○":
            text = text[1:].lstrip()
        self._value.setText(text)

    def set_sub(self, text: str) -> None:
        self._sub.setText(text)

    def set_viz(self, widget: QWidget | None) -> None:
        layout = self._viz_host.layout()
        # Clear existing children.
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if widget is not None:
            layout.addWidget(widget)


def _make_dots(total: int, on: int, *, on_color: str, off_color: str | None = None,
               max_dots: int = 24) -> QWidget:
    """Tiny dot strip — renders up to `max_dots` dots; collapses with "+ N"
    suffix when total exceeds the cap."""
    off_color = off_color or FG_MUTE
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(3)
    show = min(total, max_dots)
    for i in range(show):
        dot = QLabel("●")
        color = on_color if i < on else off_color
        dot.setStyleSheet(f"color: {color}; font-size: 9px;")
        h.addWidget(dot)
    if total > max_dots:
        more = QLabel(f"+{total - max_dots}")
        more.setStyleSheet(f"color: {FG_MUTE}; font-size: 10px;")
        h.addWidget(more)
    h.addStretch(1)
    return w


def _make_progress(ratio: float, label: str = "") -> QWidget:
    """Thin progress bar. ratio in [0, 1]."""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(2)
    if label:
        cap = QLabel(label)
        cap.setStyleSheet(f"color: {FG_MUTE}; font-size: 10px;")
        v.addWidget(cap)
    track = QFrame()
    track.setFixedHeight(4)
    track.setStyleSheet(f"background: {BG_ELEV}; border-radius: 2px;")
    fill_h = QHBoxLayout(track)
    fill_h.setContentsMargins(0, 0, 0, 0)
    fill_h.setSpacing(0)
    fill = QFrame()
    fill.setStyleSheet(f"background: {WARN}; border-radius: 2px;")
    fill_h.addWidget(fill, max(1, int(round(max(0.0, min(1.0, ratio)) * 100))))
    fill_h.addStretch(max(1, 100 - int(round(max(0.0, min(1.0, ratio)) * 100))))
    v.addWidget(track)
    return w


def _make_chips(items: list[str]) -> QWidget:
    """Small label chips (e.g. proxy protocols)."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(4)
    for item in items[:6]:
        chip = QLabel(item)
        chip.setStyleSheet(
            f"background: {BG_ELEV}; color: {FG_DIM}; "
            f"border: 1px solid {BORDER}; border-radius: 3px; "
            f"padding: 1px 6px; font-size: 10px;"
        )
        h.addWidget(chip)
    h.addStretch(1)
    return w


# ── Per-section refreshers ────────────────────────────────────────────────

def _refresh_llama(tile: _StatusTile, llama: dict) -> None:
    if not llama.get("enabled"):
        tile.set_state("mute")
        tile.set_value("○ Disabled")
        tile.set_sub("")
        tile.set_viz(None)
        return
    if llama.get("alive"):
        tile.set_state("ok")
        tile.set_value(f"● {llama.get('active_model', '—')}")
        bits = []
        inflight = int(llama.get("inflight", 0) or 0)
        if inflight:
            bits.append(f"{inflight} in-flight")
        else:
            bits.append("Ready")
        tile.set_sub(" · ".join(bits))
        idle_limit = float(llama.get("idle_unload_sec", 0) or 0)
        idle_rem = float(llama.get("idle_remaining_sec", 0) or 0)
        if idle_limit > 0 and idle_rem > 0 and not inflight:
            ratio = max(0.0, min(1.0, idle_rem / idle_limit))
            tile.set_viz(_make_progress(ratio, f"Auto-unload in {int(idle_rem)}s"))
        else:
            tile.set_viz(None)
    else:
        tile.set_state("mute")
        tile.set_value("○ Idle")
        tile.set_sub("Loads on first request")
        tile.set_viz(None)


def _refresh_proxy(tile: _StatusTile, proxy: dict) -> None:
    if not proxy.get("enabled"):
        tile.set_state("mute")
        tile.set_value("○ Disabled")
        tile.set_sub("")
        tile.set_viz(None)
        return
    tile.set_state("ok")
    tile.set_value(f"● :{proxy.get('port', '?')}")
    tile.set_sub("")
    protocols = [format_protocol(p) for p in proxy.get("protocols", [])]
    tile.set_viz(_make_chips(protocols) if protocols else None)


def _refresh_mcp(tile: _StatusTile, mcp: dict) -> None:
    if not mcp.get("enabled"):
        tile.set_state("mute")
        tile.set_value("○ Disabled")
        tile.set_sub("")
        tile.set_viz(None)
        return
    tile.set_state("ok")
    tile.set_value(f"● :{mcp.get('port', '?')}")
    tools = mcp.get("registered_tools", []) or []
    tile.set_sub(f"{len(tools)} tools registered")
    tile.set_viz(_make_dots(len(tools), len(tools), on_color=ACCENT))


def _refresh_sessions(tile: _StatusTile, sessions: list[dict]) -> None:
    alive = sum(1 for s in sessions if s.get("alive"))
    total = len(sessions)
    if total == 0:
        tile.set_state("mute")
    else:
        tile.set_state("ok" if alive else "warn")
    tile.set_value(f"{alive} / {total}")
    tile.set_sub("Active / Total")
    if total > 0:
        tile.set_viz(_make_dots(total, alive, on_color=OK, off_color=FG_MUTE))
    else:
        tile.set_viz(None)


def _model_chip(label: str, loaded: bool, idle_for: float,
                 unload_after: float) -> QWidget:
    """One model indicator chip: [● EMB · 12s] when loaded, [○ EMB · idle]
    when not. `idle_for` is seconds since last use; `unload_after` is the
    configured threshold (0 = never)."""
    chip = QWidget()
    h = QHBoxLayout(chip)
    h.setContentsMargins(6, 1, 8, 1); h.setSpacing(5)
    dot = QLabel("●" if loaded else "○")
    dot.setStyleSheet(
        f"color: {OK if loaded else FG_MUTE}; font-size: 9px;"
    )
    name = QLabel(label)
    name.setStyleSheet(
        f"color: {FG_DIM}; font-size: 10px; letter-spacing: 0.5px; font-weight: 600;"
    )
    if loaded:
        if unload_after > 0:
            remaining = max(0.0, unload_after - idle_for)
            tail = f"unload in {int(remaining)}s" if remaining > 0 else "unloading…"
        else:
            tail = f"idle {int(idle_for)}s" if idle_for >= 1 else "active"
    else:
        tail = "not loaded"
    sub = QLabel(tail)
    sub.setStyleSheet(f"color: {FG_MUTE}; font-size: 10px;")
    h.addWidget(dot); h.addWidget(name); h.addWidget(sub)
    chip.setStyleSheet(
        f"QWidget {{ background: {BG_ELEV}; border: 1px solid {BORDER}; "
        f"border-radius: 3px; }}"
    )
    return chip


def _make_model_chips(models: dict) -> QWidget | None:
    """Two-chip row for the docgraph tile viz slot: one for embeddings,
    one for the reranker. `models` is the `/api/admin/models_status`
    payload — None means the host is unreachable."""
    if not models:
        return None

    def _agg(entries: list, threshold: float) -> tuple[bool, float, float]:
        # Aggregate across pooled instances (usually 1 per class).
        # `loaded` = any loaded; `idle_for` = smallest idle age among
        # loaded entries (most-recently-used wins).
        loaded = any(bool(e.get("loaded")) for e in entries)
        idle_for = 0.0
        if loaded:
            candidates = [float(e.get("idle_for_sec", 0) or 0)
                          for e in entries if e.get("loaded")]
            if candidates:
                idle_for = min(candidates)
        return loaded, idle_for, float(threshold or 0.0)

    embed_entries  = list(models.get("embed",  []) or [])
    rerank_entries = list(models.get("rerank", []) or [])
    e_loaded, e_idle, e_after = _agg(embed_entries,
                                     models.get("embed_unload_after", 0))
    r_loaded, r_idle, r_after = _agg(rerank_entries,
                                     models.get("rerank_unload_after", 0))

    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
    h.addWidget(_model_chip("EMB",  e_loaded, e_idle, e_after))
    h.addWidget(_model_chip("RRK",  r_loaded, r_idle, r_after))
    h.addStretch(1)
    return row


def _refresh_docgraph(tile: _StatusTile, dg: dict) -> None:
    host = (dg.get("host") or {}) if isinstance(dg, dict) else {}
    if not host.get("enabled") and not host.get("alive"):
        tile.set_state("mute")
        tile.set_value("○ Disabled")
        tile.set_sub("")
        tile.set_viz(None)
        return
    err = host.get("last_error")
    alive = bool(host.get("alive"))
    if alive:
        tile.set_state("ok")
        tile.set_value(f"● :{host.get('port', '?')}")
    elif err:
        tile.set_state("err")
        tile.set_value("✗ Failed")
    else:
        tile.set_state("warn")
        tile.set_value("○ Stopped")

    # Roots total comes from the configured settings (since the live host
    # status doesn't carry the slug list — keeps this widget independent
    # of an HTTP probe).
    roots = list(get_path(read_settings(), "docgraph.roots", []) or [])
    roots = [r for r in roots if isinstance(r, dict) and (r.get("path") or "").strip()]
    n_roots = len(roots)
    n_watch = sum(1 for r in roots if r.get("watch"))
    bridged = int(host.get("bridged", 0) or 0)
    bits = []
    if n_roots:
        bits.append(f"{n_roots} root{'s' if n_roots != 1 else ''}")
    if n_watch:
        bits.append(f"{n_watch} watching")
    if bridged:
        bits.append(f"{bridged} tools bridged")
    if err and not alive:
        bits = [err]
    tile.set_sub(" · ".join(bits) if bits else ("alive" if alive else ""))

    # Embed + rerank load indicators come from /api/admin/models_status
    # (TTL-cached in docgraph/process.py). Only meaningful when the host
    # is alive — when it isn't, the probe returns None and we skip.
    tile.set_viz(_make_model_chips(dg.get("models")) if alive else None)


def _refresh_telegram(tile: "_StatusTile") -> None:
    try:
        from bot.supervisor import status_snapshot
        snap = status_snapshot()
    except Exception:
        snap = {}
    alive = bool(snap.get("alive"))
    err   = snap.get("last_error") or ""
    if alive:
        tile.set_state("ok")
        tile.set_value("Polling")
        tile.set_sub("Receiving updates")
    elif err:
        tile.set_state("err")
        tile.set_value("Error")
        tile.set_sub(err[:80])
    else:
        tile.set_state("mute")
        tile.set_value("Stopped")
        tile.set_sub("Start via tray or settings")
    tile.set_viz(None)


# ══════════════════════════════════════════════════════════════════════
# llama.cpp
# ══════════════════════════════════════════════════════════════════════

def _llama_updater_card(window) -> QWidget:
    """Updater card — check GitHub releases + overlay new binaries.

    Variant: auto-detected on first open (saved to llamacpp.update.variant)
    with a dropdown for manual override. "Check for Updates" pulls the
    latest release info; "Update Now" downloads the matched zip and
    overlays it onto the directory containing llamacpp.binary, backing
    up any overwritten files into a per-install .bak-<ts>/ folder.
    """
    from PySide6.QtCore import QObject, Signal as _Signal
    from llamacpp import updater as upd

    class _UpdBridge(QObject):
        status = _Signal(str, float)        # message, ratio (0..1)
        check_done = _Signal(dict)          # {ok, tag, build, asset_name, error}
        install_done = _Signal(dict)        # {ok, asset, files_installed, files_replaced, backup, error}

    bridge = _UpdBridge()

    card, body = _card("Updater",
                       "Pull pre-built binaries from ggml-org/llama.cpp releases and overlay them on the install folder")

    # ── Variant row (auto-detect + dropdown) ──────────────────────────
    variant_box = QComboBox()
    variants = upd.variants_for_platform()
    variant_box.addItem("Auto-detect (recommended)", "")
    for key, label in variants:
        variant_box.addItem(label, key)

    saved_variant = str(get_path(read_settings(), "llamacpp.update.variant", "") or "")
    if saved_variant:
        for i in range(variant_box.count()):
            if variant_box.itemData(i) == saved_variant:
                variant_box.setCurrentIndex(i)
                break

    def _resolved_variant() -> str:
        chosen = variant_box.currentData() or ""
        return chosen if chosen else upd.detect_variant()

    auto_hint = QLabel("")
    auto_hint.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px; padding-top: 2px;")

    def _refresh_auto_hint() -> None:
        detected = upd.detect_variant()
        label = next((lbl for k, lbl in variants if k == detected), detected)
        auto_hint.setText(f"Auto-detect → {label}")
    _refresh_auto_hint()

    variant_col = QWidget()
    vc = QVBoxLayout(variant_col)
    vc.setContentsMargins(0, 0, 0, 0)
    vc.setSpacing(2)
    vc.addWidget(variant_box)
    vc.addWidget(auto_hint)

    def _on_variant_changed(_i: int) -> None:
        patch_settings("llamacpp.update.variant", variant_box.currentData() or "")
    variant_box.currentIndexChanged.connect(_on_variant_changed)

    body.addWidget(_row(row_label("Variant",
                                   "Which release zip to pull. Auto-detect probes nvidia-smi / GPU "
                                   "and falls back to Vulkan."),
                        variant_col))

    # ── Version rows ──────────────────────────────────────────────────
    cur_label = QLabel("—")
    cur_label.setStyleSheet(f"color: {FG}; font-family: 'JetBrains Mono', Consolas, monospace;")
    body.addWidget(_row(row_label("Installed",
                                   "Build number reported by `llama-server --version`."),
                        cur_label))

    latest_label = QLabel("—")
    latest_label.setStyleSheet(f"color: {FG}; font-family: 'JetBrains Mono', Consolas, monospace;")
    body.addWidget(_row(row_label("Latest available",
                                   "Run \"Check for Updates\" to fetch the latest release tag."),
                        latest_label))

    asset_label = QLabel("—")
    asset_label.setStyleSheet(f"color: {FG_MUTE}; font-family: 'JetBrains Mono', Consolas, monospace; font-size: 11px;")
    body.addWidget(_row(row_label("Matched asset",
                                   "Asset name that will be downloaded for the chosen variant."),
                        asset_label))

    install_dir_label = QLabel(str(upd.install_dir()))
    install_dir_label.setStyleSheet(f"color: {FG_MUTE}; font-family: 'JetBrains Mono', Consolas, monospace; font-size: 11px;")
    install_dir_label.setWordWrap(True)
    body.addWidget(_row(row_label("Install folder",
                                   "Directory holding llamacpp.binary — this is where the new files land."),
                        install_dir_label))

    # ── Actions + progress ───────────────────────────────────────────
    check_btn = QPushButton("Check for Updates")
    update_btn = QPushButton("Update Now")
    update_btn.setProperty("class", "primary")
    update_btn.setEnabled(False)

    status_lbl = QLabel("")
    status_lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11.5px;")

    progress = QProgressBar()
    progress.setRange(0, 100)
    progress.setValue(0)
    progress.setTextVisible(False)
    progress.setFixedHeight(6)
    progress.hide()

    actions = QWidget()
    al = QHBoxLayout(actions)
    al.setContentsMargins(0, 0, 0, 0)
    al.setSpacing(8)
    al.addWidget(check_btn)
    al.addWidget(update_btn)
    al.addStretch(1)
    body.addWidget(actions)
    body.addWidget(status_lbl)
    body.addWidget(progress)

    # State shared with the action handlers (last-seen check result).
    _state: dict[str, Any] = {"release": None, "asset": None, "latest_build": None,
                              "companions": [], "variant_key": ""}

    # ── Status / signal handling ─────────────────────────────────────
    def _on_status(msg: str, ratio: float) -> None:
        status_lbl.setText(msg)
        if ratio > 0.0:
            progress.show()
            progress.setValue(max(0, min(100, int(ratio * 100))))
    bridge.status.connect(_on_status)

    def _refresh_installed() -> None:
        v = upd.installed_version()
        if v:
            cur_label.setText(f"b{v}")
        else:
            cur_label.setText("(unknown — run `llama-server --version` to check)")
        install_dir_label.setText(str(upd.install_dir()))

    def _on_check_done(res: dict) -> None:
        check_btn.setEnabled(True)
        if not res.get("ok"):
            latest_label.setText("—")
            asset_label.setText("—")
            update_btn.setEnabled(False)
            status_lbl.setText(f"Check failed: {res.get('error') or 'unknown error'}")
            return
        tag = res.get("tag") or ""
        build = res.get("build") or ""
        latest_label.setText(f"b{build}  ({tag})" if build else tag)
        asset_name = res.get("asset_name") or ""
        companions = res.get("companions") or []
        if asset_name:
            display = asset_name
            if companions:
                display += "\n  + " + "\n  + ".join(companions)
            asset_label.setText(display)
            cur = upd.installed_version()
            already_latest = bool(cur and build and cur == build and not companions)
            # Don't offer a re-install of the build that's already installed —
            # there's nothing to update to.
            update_btn.setEnabled(not already_latest)
            if already_latest:
                status_lbl.setText("Already on the latest build.")
            else:
                msg = "New build available." if cur != build else ""
                if companions:
                    msg = (msg + " " if msg else "") + f"Will also fetch {len(companions)} companion zip(s)."
                status_lbl.setText(msg)
        else:
            asset_label.setText("(no asset matches this variant)")
            update_btn.setEnabled(False)
            status_lbl.setText("No matching asset for the selected variant.")
    bridge.check_done.connect(_on_check_done)

    def _on_install_done(res: dict) -> None:
        check_btn.setEnabled(True)
        update_btn.setEnabled(True)
        if not res.get("ok"):
            status_lbl.setText(f"Update failed: {res.get('error') or 'unknown error'}")
            return
        msg = (f"Installed {res.get('asset')} → {res.get('files_installed', 0)} files "
               f"({res.get('files_replaced', 0)} replaced)")
        if res.get("backup"):
            msg += f" · backup: {res['backup']}"
        status_lbl.setText(msg)
        progress.setValue(100)
        _refresh_installed()
    bridge.install_done.connect(_on_install_done)

    # ── Button handlers ──────────────────────────────────────────────
    def _on_check() -> None:
        check_btn.setEnabled(False)
        update_btn.setEnabled(False)
        progress.hide()
        progress.setValue(0)
        status_lbl.setText("Fetching latest release info…")
        variant_key = _resolved_variant()

        async def _do() -> None:
            try:
                release = await upd.fetch_latest_release()
                tag = str(release.get("tag_name") or "")
                build = upd.build_from_tag(tag) or ""
                asset = upd.pick_asset(release, variant_key)
                companions: list[dict] = []
                # CUDA variants: pair with the cudart zip unless our system
                # already has a cudart64_<major>.dll whose file version is
                # >= the version the asset was built against. NVIDIA only
                # guarantees forward compatibility (newer cudart hosts older
                # binaries), so an asset built on 13.1 won't run on a
                # 13.0-only system.
                cuda_major = upd.cuda_major_of_variant(variant_key)
                if cuda_major is not None and asset is not None:
                    if not upd.cudart_satisfies(asset.get("name", "")):
                        cudart = upd.pick_cudart_asset(release, variant_key)
                        if cudart:
                            companions.append(cudart)
                _state["release"] = release
                _state["asset"] = asset
                _state["latest_build"] = build
                _state["companions"] = companions
                _state["variant_key"] = variant_key
                bridge.check_done.emit({
                    "ok": True,
                    "tag": tag,
                    "build": build,
                    "asset_name": (asset or {}).get("name", ""),
                    "companions": [c.get("name", "") for c in companions],
                })
            except Exception as exc:
                log.exception("llama.cpp updater: check failed")
                bridge.check_done.emit({"ok": False, "error": str(exc)})
        schedule(window.bot_loop, _do())
    check_btn.clicked.connect(_on_check)

    def _on_update() -> None:
        asset = _state.get("asset")
        if not asset:
            status_lbl.setText("Run \"Check for Updates\" first.")
            return
        cur = upd.installed_version()
        latest = _state.get("latest_build") or ""
        companions = _state.get("companions") or []
        total_size = (int(asset.get("size") or 0)
                      + sum(int(c.get("size") or 0) for c in companions))
        lines = [
            f"Download {asset.get('name')}",
            f"  size: ~{(int(asset.get('size') or 0)) / 1_048_576:.1f} MiB",
        ]
        for c in companions:
            lines.append(f"+ {c.get('name')} (~{(int(c.get('size') or 0)) / 1_048_576:.1f} MiB)")
        lines.append("")
        lines.append(f"Total: ~{total_size / 1_048_576:.1f} MiB")
        lines.append(f"Installs into: {upd.install_dir()}")
        lines.append(f"Installed: b{cur or '?'} → latest: b{latest or '?'}")
        lines.append("")
        lines.append("The running llama-server will be stopped; existing files "
                     "will be backed up under .bak-<timestamp>/ inside the install folder.")
        info = "\n".join(lines)
        resp = QMessageBox.question(card, "Update llama.cpp", info,
                                    QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                                    QMessageBox.StandardButton.Ok)
        if resp != QMessageBox.StandardButton.Ok:
            return

        check_btn.setEnabled(False)
        update_btn.setEnabled(False)
        progress.show()
        progress.setValue(0)
        status_lbl.setText("Starting update…")

        def _progress(msg: str, ratio: float) -> None:
            # Called from the bot loop — funnel to the Qt thread via signal.
            bridge.status.emit(msg, ratio)

        async def _do() -> None:
            try:
                res = await upd.download_and_install(
                    asset,
                    companions=companions,
                    progress=_progress,
                )
                bridge.install_done.emit(res)
            except Exception as exc:
                log.exception("llama.cpp updater: install failed")
                bridge.install_done.emit({"ok": False, "error": str(exc)})
        schedule(window.bot_loop, _do())
    update_btn.clicked.connect(_on_update)

    _refresh_installed()
    return card


def _llama_version_manager_card(window) -> QWidget:
    """Manage installed + backed-up llama.cpp versions, and audit/compare their
    CLI flag surfaces.

    The updater leaves every replaced build in `<install_dir>/.bak-<ts>/`, so
    each backup is a real, runnable previous version. This card lists the
    active binary + all backups and can:
      • Test     — probe a version's `--help`/`--version` and cross-check the
                   flags telecode emits against it ("would my config run on it?").
      • Compare  — diff the flag surface of the active binary vs a selected one.
      • Restore  — revert the install to a backup (stops the supervisor first;
                   the displaced files become a fresh, reversible backup).
      • Delete   — prune a backup folder.

    Probes run the real binary; if an old backup can't relaunch, they fall back
    to the spec cached at update time. Reports append to data/logs/cli_audit.log.
    """
    import asyncio as _asyncio
    from PySide6.QtWidgets import QPlainTextEdit
    from llamacpp import flag_audit as fa
    from llamacpp import updater as upd

    mono = "font-family: 'JetBrains Mono', Consolas, monospace;"

    card, body = _card(
        "Version Manager",
        "Audit, compare, and restore installed / backed-up llama.cpp versions. "
        "Run a flag audit after updating to catch removed/renamed flags before "
        "they break startup. Reports append to data/logs/cli_audit.log.",
    )

    info = QLabel("…")
    info.setWordWrap(True)
    info.setStyleSheet(f"color: {FG}; {mono} font-size: 11px;")
    body.addWidget(_row(row_label("Active",
                                   "Currently installed binary and its build."),
                        info))

    version_box = QComboBox()
    version_box.setMinimumWidth(360)
    body.addWidget(_row(row_label("Backup / version",
                                   "Active binary plus every .bak-<ts> the updater "
                                   "kept. Selected = target of Test / Compare / "
                                   "Restore / Delete."),
                        _wrap_align(version_box, Qt.AlignmentFlag.AlignLeft)))

    # ── Action buttons ───────────────────────────────────────────────
    test_btn = QPushButton("Test Selected")
    compare_btn = QPushButton("Compare Current ↔ Selected")
    audit_btn = QPushButton("Audit Active Config")
    audit_btn.setProperty("class", "primary")
    restore_btn = QPushButton("Restore Selected")
    delete_btn = QPushButton("Delete Backup")
    delete_btn.setProperty("class", "danger")

    actions = QWidget()
    al = QHBoxLayout(actions)
    al.setContentsMargins(0, 0, 0, 0)
    al.setSpacing(8)
    for b in (test_btn, compare_btn, audit_btn, restore_btn, delete_btn):
        al.addWidget(b)
    al.addStretch(1)
    body.addWidget(actions)

    status_lbl = QLabel("")
    status_lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11.5px;")
    body.addWidget(status_lbl)

    results = QPlainTextEdit()
    results.setReadOnly(True)
    results.setStyleSheet(
        f"background: {BG_ELEV}; color: {FG}; border: 1px solid {BORDER}; "
        f"border-radius: 6px; {mono} font-size: 11px;"
    )
    results.setMinimumHeight(190)
    results.setPlaceholderText("Run Test / Compare / Audit to see results here.")
    body.addWidget(results)

    # ── Helpers ───────────────────────────────────────────────────────
    def _set_status(text: str, ok: bool | None = None) -> None:
        color = FG_MUTE if ok is None else (OK if ok else ERR)
        status_lbl.setText(text)
        status_lbl.setStyleSheet(f"color: {color}; font-size: 11.5px;")

    def _selected() -> dict | None:
        return version_box.currentData()

    def _selected_target() -> tuple[str | None, str]:
        """(binary_path_or_None, version_hint) for the selected entry.
        None binary = active (flag_audit resolves it itself)."""
        sel = _selected() or {}
        if sel.get("kind") == "backup":
            binp = upd.backup_binary(sel["ts"])
            return (str(binp) if binp else None, sel.get("version") or "")
        return (None, "")

    def _refresh_info(v: dict | None = None) -> None:
        try:
            if v is None:
                v = fa.detect_version()
            info.setText(f"{fa.binary_path()}\nb{v['version'] or '?'} ({v['build'] or '?'})")
        except Exception as exc:
            info.setText(f"binary probe failed: {exc}")

    _last_backup_sig: list = [object()]

    def _backup_sig() -> tuple:
        """Cheap disk-only fingerprint of the backup set (no `--version`
        probe). Used to skip the combobox rebuild on idle ticks so the
        dropdown only re-populates when a `.bak-<ts>` is actually added,
        removed, or restored."""
        try:
            return tuple(
                (b["ts"], b.get("mtime"), b.get("file_count"),
                 b.get("has_binary"), b.get("version"))
                for b in upd.list_backups()
            )
        except Exception:
            return ()

    def _reload_versions(force: bool = False) -> None:
        # Skip the rebuild (and the relatively expensive `--version` probe)
        # when the on-disk backup set is unchanged — lets this run cheaply on
        # every page tick so a backup created by an "Update Now" after this
        # card was built still shows up without restarting telecode.
        sig = _backup_sig()
        if not force and sig == _last_backup_sig[0]:
            return
        _last_backup_sig[0] = sig
        version_box.blockSignals(True)
        prev = _selected()
        version_box.clear()
        av = fa.detect_version()
        # The active binary changes exactly when the .bak-* set does (update /
        # restore), so refresh the "Active" label here too — reuse this probe
        # rather than spawning `--version` a second time.
        _refresh_info(av)
        version_box.addItem(f"Active · b{av['version'] or '?'} ({av['build'] or '?'})",
                            {"kind": "active"})
        for b in upd.list_backups():
            import datetime as _dt
            when = _dt.datetime.fromtimestamp(b["mtime"]).strftime("%Y-%m-%d %H:%M") \
                if b.get("mtime") else b["ts"]
            run = "" if b["has_binary"] else "  [no binary]"
            version_box.addItem(
                f"Backup · b{b['version'] or '?'} · {when} · {b['file_count']} files{run}",
                {"kind": "backup", "ts": b["ts"], "version": b["version"],
                 "has_binary": b["has_binary"]})
        version_box.blockSignals(False)
        # Restore previous selection by ts where possible.
        if prev and prev.get("kind") == "backup":
            for i in range(version_box.count()):
                d = version_box.itemData(i)
                if d and d.get("kind") == "backup" and d.get("ts") == prev.get("ts"):
                    version_box.setCurrentIndex(i)
                    break
        _sync_buttons()

    def _sync_buttons() -> None:
        sel = _selected() or {}
        is_backup = sel.get("kind") == "backup"
        restore_btn.setEnabled(is_backup)
        delete_btn.setEnabled(is_backup)

    # ── Actions (flag_audit calls are synchronous + fast) ─────────────
    def _on_test() -> None:
        try:
            binp, vh = _selected_target()
            rep = fa.audit_config(binary=binp, version_hint=vh)
            results.setPlainText(fa.format_audit(rep))
            cc = rep["cross_check"]
            n_bad = len(cc["unknown"]) + len(cc["removed_used"]) + len(cc["bad_values"])
            src = "" if rep.get("source") == "live" else " (from cache)"
            if rep["ok"]:
                _set_status(f"b{rep['version'] or '?'}{src}: config valid against this build.",
                            ok=True)
            else:
                _set_status(f"b{rep['version'] or '?'}{src}: {n_bad} problem(s) — see results.",
                            ok=False)
        except Exception as exc:
            _set_status(f"Test failed: {exc}", ok=False)
            results.setPlainText(str(exc))

    def _on_compare() -> None:
        try:
            binp, vh = _selected_target()
            rep = fa.compare(binary_a=None, binary_b=binp, version_b=vh)
            results.setPlainText(fa.format_compare(rep))
            _set_status(f"Compared current b{rep['a_version'] or '?'} ↔ "
                        f"selected b{rep['b_version'] or '?'}: "
                        f"{len(rep['added'])} added, {len(rep['removed'])} removed, "
                        f"{len(rep['changed'])} changed.", ok=None)
        except Exception as exc:
            _set_status(f"Compare failed: {exc}", ok=False)
            results.setPlainText(str(exc))

    def _on_audit() -> None:
        try:
            rep = fa.audit_config()  # active binary
            results.setPlainText(fa.format_audit(rep))
            cc = rep["cross_check"]
            n_bad = len(cc["unknown"]) + len(cc["removed_used"]) + len(cc["bad_values"])
            if rep["ok"]:
                _set_status("Active config valid against the installed build.", ok=True)
            else:
                _set_status(f"{n_bad} problem(s) on the active build — see results + log.",
                            ok=False)
        except Exception as exc:
            _set_status(f"Audit failed: {exc}", ok=False)
            results.setPlainText(str(exc))

    def _on_restore() -> None:
        sel = _selected() or {}
        if sel.get("kind") != "backup":
            return
        ts = sel["ts"]
        try:
            # Stop the supervisor first — Windows locks the running exe/DLLs.
            loop = getattr(window, "bot_loop", None)
            try:
                from process import _SUPERVISOR  # type: ignore
                if _SUPERVISOR and loop is not None and _SUPERVISOR.alive():
                    _set_status("Stopping llama-server before restore…")
                    fut = _asyncio.run_coroutine_threadsafe(_SUPERVISOR.stop(), loop)
                    fut.result(timeout=20)
            except Exception as exc:
                log.warning("supervisor stop before restore failed: %s", exc)
            res = upd.restore_backup(ts)
            _refresh_info()
            _reload_versions()
            redo = res.get("redo_backup")
            tail = f" (previous build saved to {redo})" if redo else ""
            _set_status(f"Restored b{res.get('restored_version') or '?'} — "
                        f"{res['files_restored']} files{tail}. Reload the model to use it.",
                        ok=True)
            results.setPlainText(
                f"Restored from : {res['restored_from']}\n"
                f"Files restored: {res['files_restored']}\n"
                f"Redo backup   : {res.get('redo_backup') or '(none)'}\n"
                f"Restored build: b{res.get('restored_version') or '?'}\n\n"
                f"Reload / respawn the model (Status → Load, or next request) "
                f"to start using the restored binary.")
        except Exception as exc:
            _set_status(f"Restore failed: {exc}", ok=False)
            results.setPlainText(str(exc))

    def _on_delete() -> None:
        sel = _selected() or {}
        if sel.get("kind") != "backup":
            return
        ts = sel["ts"]
        try:
            upd.delete_backup(ts)
            _reload_versions()
            _set_status(f"Deleted backup {ts}.", ok=None)
        except Exception as exc:
            _set_status(f"Delete failed: {exc}", ok=False)

    test_btn.clicked.connect(_on_test)
    compare_btn.clicked.connect(_on_compare)
    audit_btn.clicked.connect(_on_audit)
    restore_btn.clicked.connect(_on_restore)
    delete_btn.clicked.connect(_on_delete)
    version_box.currentIndexChanged.connect(lambda _i: _sync_buttons())

    _reload_versions(force=True)  # also populates the "Active" label via _refresh_info
    # Let the owning page re-scan for new backups on its refresh tick.
    card.reload_versions = _reload_versions  # type: ignore[attr-defined]
    return card


def _llama(window) -> QWidget:
    scroll, content, layout = _page()

    # Master card: Enabled + active model + actions
    master, body = _card("llama.cpp", "Local model supervisor")
    # Enabled toggle
    body.addWidget(_toggle_row("llamacpp.enabled",
                               "Enabled",
                               "Master switch. Disabling does not stop an already-running model — restart telecode."))
    body.addWidget(_section_header("Active Model"))

    # Model swap dropdown
    model_box = QComboBox()
    _last_models_sig: list = [None]
    _user_selected_model: list[str | None] = [None]

    def _refresh_models() -> None:
        view = model_box.view()
        if view and view.isVisible():
            return

        models = list(get_path(read_settings(), "llamacpp.models", {}) or {})
        from process import _SUPERVISOR as sup
        from llamacpp import config as cfg
        active = sup.active_model() if (sup and sup.alive()) else ""
        target_model = active or _user_selected_model[0] or cfg.default_model()

        sig = (tuple(models), active, target_model)
        if sig == _last_models_sig[0]:
            return
        _last_models_sig[0] = sig

        current_items = [model_box.itemText(i) for i in range(model_box.count())]
        if current_items != models:
            model_box.blockSignals(True)
            model_box.clear()
            for m in models:
                model_box.addItem(m, m)
            model_box.blockSignals(False)

        if target_model in models:
            idx = models.index(target_model)
            if model_box.currentIndex() != idx:
                model_box.blockSignals(True)
                model_box.setCurrentIndex(idx)
                model_box.blockSignals(False)

    _refresh_models()

    def _on_model_chosen(_i: int) -> None:
        m = model_box.currentData()
        if not m:
            return
        _user_selected_model[0] = m
        async def _do():
            from process import get_supervisor
            sup = await get_supervisor()
            await sup.ensure_model(m)
        schedule(window.bot_loop, _do())
    model_box.currentIndexChanged.connect(_on_model_chosen)
    body.addWidget(_row(row_label("Model", "Swap to a different registered model."), model_box))

    # Actions row
    action_row = QWidget()
    ar = QHBoxLayout(action_row)
    ar.setContentsMargins(0, 0, 0, 0)
    ar.setSpacing(8)
    load_btn = QPushButton("Load Now")
    load_btn.setProperty("class", "primary")
    unload_btn = QPushButton("Unload")
    unload_btn.setProperty("class", "danger")
    restart_btn = QPushButton("Restart")

    def _load():
        async def _do():
            from process import get_supervisor
            from llamacpp import config as cfg
            sup = await get_supervisor()
            chosen = model_box.currentData() or cfg.default_model()
            await sup.ensure_model(chosen)
        schedule(window.bot_loop, _do())
    def _unload():
        async def _do():
            from process import get_supervisor
            sup = await get_supervisor()
            await sup.stop()
        schedule(window.bot_loop, _do())
    def _restart():
        async def _do():
            from process import get_supervisor
            sup = await get_supervisor()
            await sup.stop()
            await sup.start_default()
        schedule(window.bot_loop, _do())
    load_btn.clicked.connect(_load)
    unload_btn.clicked.connect(_unload)
    restart_btn.clicked.connect(_restart)

    ar.addWidget(load_btn)
    ar.addWidget(unload_btn)
    ar.addWidget(restart_btn)
    ar.addStretch(1)
    body.addWidget(action_row)

    body.addWidget(_section_header("Lifecycle"))
    body.addWidget(_toggle_row("llamacpp.auto_start", "Auto-Start On Launch",
                               "Load the default / remembered model at telecode startup."))
    body.addWidget(_row(row_label("Idle Unload",
        "Stop llama-server after N seconds of no requests. Next request "
        "(proxy or tray Load) respawns it. Checkbox = master on/off; the "
        "spinbox is remembered across toggles.",
        "llamacpp.idle_unload_sec"),
        _idle_unload_row("llamacpp.idle_unload_sec", 300)))
    body.addWidget(_number_row("llamacpp.ready_timeout_sec", "Ready Timeout",
                               30, 900, 30, 0, "s",
                               "Max time to wait for /health to return ok after spawn."))

    layout.addWidget(master)

    # ── Updater ──────────────────────────────────────────────────────
    layout.addWidget(_llama_updater_card(window))
    version_manager_card = _llama_version_manager_card(window)
    layout.addWidget(version_manager_card)

    # Server (binary + binding)
    srv_card, srv_body = _card("Server", "llamacpp.* — binary + binding (restart required)")
    srv_body.addWidget(_line_row("llamacpp.binary", "Binary Path", "llama-server",
                                  "Path to llama-server executable. Bare name = use PATH."))
    srv_body.addWidget(_line_row("llamacpp.host", "Host", "127.0.0.1",
                                  "0.0.0.0 to expose on LAN. Internal callers always use 127.0.0.1."))
    srv_body.addWidget(_number_row("llamacpp.port", "Port", 1024, 65535, 1, 0))
    srv_body.addWidget(_password_row("llamacpp.api_key", "API Key",
                                      "leave empty to disable",
                                      "Optional --api-key. Clients must send Authorization: Bearer <key>."))
    srv_body.addWidget(_number_row("llamacpp.timeout", "HTTP Timeout", 0, 86400, 30, 0, "s",
                                    "--timeout: server read/write timeout. 0 = use llama-server default (600s)."))
    srv_body.addWidget(_line_row("llamacpp.api_prefix", "API Prefix", "/llama",
                                  "--api-prefix: mount the server under a path prefix (for reverse-proxy setups)."))
    srv_body.addWidget(_line_row("llamacpp.media_path", "Media Path", "./data/media",
                                  "--media-path: directory served via file:// URLs in chat completion image refs."))
    srv_body.addWidget(_pair_list_row("llamacpp.extra_args", "Extra CLI Args",
                                  'Appended to every spawn. One [flag, value] pair per row '
                                  '— leave value empty for flag-only switches.'))
    layout.addWidget(srv_card)

    # Spawn / compute card — server-wide, not per-model
    spawn_card, spawn_body = _card("Spawn / Compute",
                                    "llamacpp.* — CPU, batching, memory, GPU layout (restart required)")
    spawn_body.addWidget(_number_row("llamacpp.threads",        "Threads",           1,  128, 1, 0, "",
                                      "--threads: CPU threads for generation."))
    spawn_body.addWidget(_number_row("llamacpp.threads_batch",  "Threads (batch)",   0,  128, 1, 0, "",
                                      "--threads-batch: CPU threads for prompt processing. 0 = match --threads."))
    spawn_body.addWidget(_number_row("llamacpp.batch_size",     "Batch Size",        32, 8192, 32, 0, "tok",
                                      "--batch-size: logical batch size. Tokens processed per upstream step."))
    spawn_body.addWidget(_enum_row("llamacpp.split_mode",       "Split Mode",
                                    [("Layer (default)", "layer"),
                                     ("Row",             "row"),
                                     ("Tensor (EXPERIMENTAL)", "tensor"),
                                     ("None",            "none")],
                                    "--split-mode: how layers are sharded across GPUs."))
    spawn_body.addWidget(_enum_row("llamacpp.numa",             "NUMA Strategy",
                                    [("(no tweaks)", ""),
                                     ("Distribute", "distribute"),
                                     ("Isolate", "isolate"),
                                     ("Numactl", "numactl")],
                                    "--numa: attempt optimizations that help on some NUMA systems."))
    spawn_body.addWidget(_number_row("llamacpp.ubatch_size",    "Micro-Batch Size",  32, 8192, 32, 0, "tok",
                                      "--ubatch-size: physical sub-batch. Usually = batch_size / 2 or / 4."))
    spawn_body.addWidget(_number_row("llamacpp.parallel",       "Parallel Slots",    1,  32,  1, 0, "",
                                      "--parallel: number of concurrent request slots."))
    spawn_body.addWidget(_toggle_row("llamacpp.cont_batching",  "Continuous Batching",
                                      "--cont-batching: process multiple requests in one batch."))
    spawn_body.addWidget(_toggle_row("llamacpp.mlock",          "Mlock",
                                      "--mlock: force the OS to keep model pages in RAM."))
    spawn_body.addWidget(_toggle_row("llamacpp.no_mmap",        "No mmap",
                                      "--no-mmap: load the model fully into RAM instead of memory-mapping it."))
    spawn_body.addWidget(_number_row("llamacpp.main_gpu",       "Main GPU",          0,  16,  1, 0, "",
                                      "--main-gpu: index of the primary CUDA/ROCm device."))
    spawn_body.addWidget(_line_row("llamacpp.tensor_split",     "Tensor Split",
                                    "e.g. 0.5,0.5",
                                    "--tensor-split: comma-separated weights for multi-GPU split."))

    spawn_body.addWidget(_number_row("llamacpp.seed",           "Seed",             -1, 2147483647, 1, 0, "",
                                      "--seed: -1 = random."))
    spawn_body.addWidget(_number_row("llamacpp.keep",           "Keep Tokens",       0, 8192, 32, 0, "tok",
                                      "--keep: tokens from prompt always kept when truncating."))

    spawn_body.addWidget(_section_header("Scheduling"))
    spawn_body.addWidget(_line_row("llamacpp.cpu_mask", "CPU Mask",
                                    "hex, e.g. c03c03 — empty = all cores",
                                    "--cpu-mask: hex affinity mask for the generation thread pool. Bit 0 = CPU 0. "
                                    "On hybrid Intel CPUs the P-cores are not contiguous, so a mask is needed where "
                                    "a range won't do. Empty = every core."))
    spawn_body.addWidget(_line_row("llamacpp.cpu_range", "CPU Range",
                                    "lo-hi, e.g. 0-7",
                                    "--cpu-range: contiguous CPU range for the generation pool. Complements CPU Mask; "
                                    "leave empty if using a mask."))
    spawn_body.addWidget(_line_row("llamacpp.cpu_mask_batch", "CPU Mask (batch)",
                                    "hex — empty = same as CPU Mask",
                                    "--cpu-mask-batch: affinity mask for the prompt-processing pool. "
                                    "WARNING: llama-server defaults this to --cpu-mask. If CPU Mask pins generation to "
                                    "P-cores only, set this explicitly (e.g. ffffff) or prefill loses the E-cores too."))
    spawn_body.addWidget(_line_row("llamacpp.cpu_range_batch", "CPU Range (batch)",
                                    "lo-hi, e.g. 0-23",
                                    "--cpu-range-batch: contiguous CPU range for the prompt-processing pool."))
    spawn_body.addWidget(_toggle_row("llamacpp.cpu_strict", "CPU Strict",
                                      "--cpu-strict <0|1>: pin threads strictly to selected cores (default 0)."))
    spawn_body.addWidget(_toggle_row("llamacpp.cpu_strict_batch", "CPU Strict (batch)",
                                      "--cpu-strict-batch: same as --cpu-strict but for prompt-processing threads."))
    spawn_body.addWidget(_number_row("llamacpp.prio",             "Priority",         -1, 3, 1, 0, "",
                                      "--prio: process/thread priority. -1=low, 0=normal, 1=medium, 2=high, 3=realtime."))
    spawn_body.addWidget(_number_row("llamacpp.prio_batch",       "Priority (batch)", -1, 3, 1, 0, "",
                                      "--prio-batch: priority for prompt-processing threads."))
    spawn_body.addWidget(_number_row("llamacpp.poll",             "Polling",           0, 100, 5, 0, "",
                                      "--poll <0..100>: polling intensity when waiting for work. 0=no polling, 50=default."))
    spawn_body.addWidget(_number_row("llamacpp.poll_batch",       "Polling (batch)",   0, 100, 5, 0, "",
                                      "--poll-batch: polling for the batch pool."))
    spawn_body.addWidget(_number_row("llamacpp.threads_http",     "HTTP Threads",      0, 128, 1, 0, "",
                                      "--threads-http: HTTP request worker threads. 0 = unset (server default)."))

    spawn_body.addWidget(_section_header("Memory / Offload"))
    spawn_body.addWidget(_toggle_row("llamacpp.no_host",          "No Host Buffer",
                                      "--no-host: skip host (CPU) buffer allocations so secondary buffers can be used. Advanced."))
    spawn_body.addWidget(_toggle_row("llamacpp.direct_io",        "Direct I/O",
                                      "--direct-io: use O_DIRECT during load (bypasses OS page cache). Useful on fast NVMe with --no-mmap."))
    spawn_body.addWidget(_toggle_row("llamacpp.repack",           "Weight Repack",
                                      "--repack / --no-repack: pack weights for faster CPU kernels (default enabled)."))
    spawn_body.addWidget(_toggle_row("llamacpp.op_offload",       "Op Offload",
                                      "--op-offload / --no-op-offload: offload host tensor ops to device (default enabled)."))
    spawn_body.addWidget(_toggle_row("llamacpp.check_tensors",    "Check Tensors",
                                      "--check-tensors: verify model tensor data on load (slows startup; catches corrupted GGUFs)."))
    spawn_body.addWidget(_toggle_row("llamacpp.skip_chat_parsing", "Skip Chat Parsing (debug)",
                                      "--skip-chat-parsing: force the server to skip Jinja chat-template parsing. Useful for debugging template bugs."))
    spawn_body.addWidget(_number_row("llamacpp.n_predict",         "N-Predict (default)", -1, 1048576, 64, 0, "tok",
                                      "--n-predict: server-wide default cap on tokens generated. -1 = unlimited."))
    layout.addWidget(spawn_card)

    # ── Endpoints card — diagnostic / monitoring endpoints ───────────
    ep_card, ep_body = _card("Endpoints",
                              "llamacpp.* — diagnostic / monitoring HTTP endpoints (server-wide).")
    ep_body.addWidget(_toggle_row("llamacpp.slots", "Slots Endpoint",
                                   "--slots / --no-slots: expose /slots monitoring (default enabled). Slot state shows the live prompt cache and KV usage."))
    ep_body.addWidget(_toggle_row("llamacpp.metrics", "Prometheus /metrics",
                                   "--metrics: expose Prometheus-compatible /metrics endpoint."))
    ep_body.addWidget(_toggle_row("llamacpp.props", "Mutable /props",
                                   "--props: allow POST /props to change global properties (samplers etc.) at runtime."))
    layout.addWidget(ep_card)

    # ── Server Mode card — alternate endpoint modes ───────────────────
    _mutex_bools("llamacpp.embedding", "llamacpp.rerank")
    sm_card, sm_body = _card("Server Mode",
                              "llamacpp.* — repurpose the running server for embedding or reranking. "
                              "Leave all OFF for normal chat completions.")
    sm_body.addWidget(_dependent(
        _toggle_row("llamacpp.embedding", "Embedding Mode",
                     "--embedding: restrict server to embedding requests. Requires an embedding-capable GGUF."),
        ["llamacpp.rerank"], lambda r: not bool(r)))
    sm_body.addWidget(_dependent(
        _toggle_row("llamacpp.rerank", "Rerank Mode",
                     "--rerank: enable /rerank endpoint. Requires a reranker GGUF."),
        ["llamacpp.embedding"], lambda e: not bool(e)))
    sm_body.addWidget(_dependent(
        _enum_row("llamacpp.pooling", "Pooling",
                   [("(model default)", ""),
                    ("none",  "none"),
                    ("mean",  "mean"),
                    ("cls",   "cls"),
                    ("last",  "last"),
                    ("rank",  "rank")],
                   "--pooling: pooling type for embedding output. Only meaningful when Embedding Mode is on."),
        ["llamacpp.embedding"], lambda e: bool(e)))
    layout.addWidget(sm_card)

    # Caching policy card — server-wide
    cache_card, cache_body = _card("Caching",
                                    "llamacpp.* — KV cache & slot / checkpoint policy")
    cache_body.addWidget(_toggle_row("llamacpp.kv_offload",     "KV Offload (GPU)",
                                      "--kv-offload / --no-kv-offload: keep KV cache on GPU. Toggle off to stay on CPU."))
    # Unified KV only matters with multiple slots (parallel > 1 or -1 = auto).
    cache_body.addWidget(_dependent(
        _toggle_row("llamacpp.kv_unified",     "Unified KV",
                     "--kv-unified / --no-kv-unified: single KV buffer shared across all slots."),
        ["llamacpp.parallel"], lambda p: int(p or 1) != 1))
    cache_body.addWidget(_toggle_row("llamacpp.cache_prompt",   "Cache Prompt",
                                      "--cache-prompt / --no-cache-prompt: reuse KV across requests with shared prefixes."))
    # cache_idle_slots needs unified KV AND a positive cache_ram.
    cache_body.addWidget(_dependent(
        _toggle_row("llamacpp.cache_idle_slots", "Cache Idle Slots",
                     "--cache-idle-slots / --no-cache-idle-slots: save and clear idle slots when a new task arrives. "
                     "(Renamed from --clear-idle in upstream b9145.) Requires Unified KV + Cache RAM > 0."),
        ["llamacpp.kv_unified", "llamacpp.cache_ram"],
        lambda u, cr: bool(u) and int(cr or 0) > 0))
    cache_body.addWidget(_toggle_row("llamacpp.context_shift",  "Context Shift",
                                      "--context-shift / --no-context-shift: rotate the KV ring buffer instead of erroring on context overflow."))
    cache_body.addWidget(_toggle_row("llamacpp.warmup",         "Warmup",
                                      "--warmup / --no-warmup: run a token-less warmup pass at load (default enabled)."))
    cache_body.addWidget(_number_row("llamacpp.sleep_idle_seconds", "Sleep Idle After", 0, 86400, 60, 0, "s",
                                      "--sleep-idle-seconds: send the server to a low-power sleep after N seconds of idleness. "
                                      "Note: telecode's own idle_unload still runs and will fully stop llama-server first."))
    cache_body.addWidget(_number_row("llamacpp.cache_ram",      "Cache RAM Ceiling", 0, 524288, 128, 0, "MiB",
                                      "-cram, --cache-ram: maximum host-memory cache size. 0 = unset."))
    cache_body.addWidget(_toggle_row("llamacpp.swa_full",       "SWA Full Cache",
                                      "--swa-full: allocate full-size SWA (sliding-window attention) cache."))
    cache_body.addWidget(_number_row("llamacpp.ctx_checkpoints",           "Ctx Checkpoints",          0, 256, 1, 0, "",
                                      "--ctx-checkpoints: max number of context checkpoints per slot."))
    cache_body.addWidget(_number_row("llamacpp.checkpoint_min_step",       "Checkpoint Min Step",      0, 100000, 256, 0, "tok",
                                      "--checkpoint-min-step: minimum token spacing between context checkpoints (0 = no minimum)."))
    cache_body.addWidget(_line_row("llamacpp.slot_save_path",   "Slot Save Path",
                                    "./data/slots",
                                    "--slot-save-path: directory for /slots/save & /slots/restore."))
    layout.addWidget(cache_card)

    # Speculative decoding card — server-wide algorithm, per-model draft pair
    spec_card, spec_body = _card("Speculative Decoding",
                                  "llamacpp.* — algorithm choice & tuning. Draft model lives per-model.")
    # Multi-select Spec Type. v9243+ accepts a comma-separated list, so stacking
    # is supported (e.g. "draft-mtp,ngram-mod" = MTP heads for high-confidence
    # guesses + ngram-mod for verbatim runs). Serialized as a comma-joined string
    # in settings.json. "none" is intentionally NOT a checkbox — uncheck all.
    _mutex_spec_default_vs_type()
    spec_body.addWidget(_dependent(
        _spec_type_multi_row("llamacpp.spec_type", "Spec Type"),
        ["llamacpp.spec_default"],
        lambda sd: not bool(sd),
    ))
    spec_body.addWidget(_dependent(
        _toggle_row("llamacpp.spec_default",         "Spec Default (auto-pick)",
                     "--spec-default: let llama-server pick a working spec config based on what the model exposes "
                     "(e.g. enables draft-mtp automatically if the model has MTP heads). Mutually exclusive with the checkboxes above."),
        ["llamacpp.spec_type"],
        lambda st: not _spec_value_is_active(st),
    ))
    # The three rows below feed --spec-ngram-{simple,map-k,map-k4v}-{size-n,size-m,min-hits}
    # — argv.build_argv() picks the per-mode flag from the chosen Spec Type.
    # ngram-mod has its own (n-min, n-max, n-match) shape — separate rows below.
    # Shared ngram triplet for simple / map-k / map-k4v — greyed out when none active.
    _ngram_simple_active = lambda st: _has_spec(st, "ngram-simple", "ngram-map-k", "ngram-map-k4v")
    spec_body.addWidget(_dependent(
        _number_row("llamacpp.spec_ngram_size_n",   "N-gram N (size-n)",   0, 64, 1, 0, "",
                     "Lookup key length for ngram-simple / ngram-map-k / ngram-map-k4v. 0 = server default."),
        ["llamacpp.spec_type"], _ngram_simple_active))
    spec_body.addWidget(_dependent(
        _number_row("llamacpp.spec_ngram_size_m",   "N-gram M (size-m)",   0, 64, 1, 0, "",
                     "Draft length per match for ngram-simple / ngram-map-k / ngram-map-k4v. 0 = default."),
        ["llamacpp.spec_type"], _ngram_simple_active))
    spec_body.addWidget(_dependent(
        _number_row("llamacpp.spec_ngram_min_hits", "N-gram Min Hits",     0, 64, 1, 0, "",
                     "Minimum match frequency for ngram-simple / ngram-map-k / ngram-map-k4v. 0 = default."),
        ["llamacpp.spec_type"], _ngram_simple_active))
    # ngram-mod specific knobs (n-min/n-max added in v9243; n-match was already there) —
    # greyed when ngram-mod is not in spec_type.
    _ngram_mod_active = lambda st: _has_spec(st, "ngram-mod")
    spec_body.addWidget(_dependent(
        _number_row("llamacpp.spec_ngram_mod_n_min",   "N-gram Mod N-Min",   0, 256, 1, 0, "tok",
                     "--spec-ngram-mod-n-min: minimum ngram tokens for ngram-mod (default 48)."),
        ["llamacpp.spec_type"], _ngram_mod_active))
    spec_body.addWidget(_dependent(
        _number_row("llamacpp.spec_ngram_mod_n_max",   "N-gram Mod N-Max",   0, 256, 1, 0, "tok",
                     "--spec-ngram-mod-n-max: maximum ngram tokens for ngram-mod (default 64)."),
        ["llamacpp.spec_type"], _ngram_mod_active))
    spec_body.addWidget(_dependent(
        _number_row("llamacpp.spec_ngram_mod_n_match", "N-gram Mod N-Match", 0, 256, 1, 0, "tok",
                     "--spec-ngram-mod-n-match: lookup length for ngram-mod (default 24). 0 = falls back to N (size-n)."),
        ["llamacpp.spec_type"], _ngram_mod_active))
    # Draft-side thread knobs only matter when a draft-* spec type or ngram-cache is active.
    _draft_active = lambda st: _has_spec(st, "draft-simple", "draft-eagle3", "draft-mtp", "ngram-cache")
    spec_body.addWidget(_dependent(
        _number_row("llamacpp.threads_draft",       "Threads (draft)",     0, 128, 1, 0, "",
                     "--threads-draft: CPU threads for draft-model generation. 0 = match --threads."),
        ["llamacpp.spec_type"], _draft_active))
    spec_body.addWidget(_dependent(
        _number_row("llamacpp.threads_batch_draft", "Threads (draft batch)", 0, 128, 1, 0, "",
                     "--threads-batch-draft: CPU threads for draft-model prompt processing."),
        ["llamacpp.spec_type"], _draft_active))
    layout.addWidget(spec_card)

    # Proxy Behavior card — global behavior knobs with NO per-model equivalent.
    # Sampling defaults (temperature/top_p/etc.) live in the Models section per
    # model since each model has its own sweet spot.
    pb_card, pb_body = _card("Proxy Behavior",
                              "Global proxy behavior — these have no per-model override. "
                              "Sampler defaults moved to the Models tab per-model form.")
    pb_body.addWidget(_enum_row("llamacpp.inference.context_overflow", "Context Overflow",
                                 [("Truncate Middle", "truncate_middle"),
                                  ("Truncate Left",   "truncate_left"),
                                  ("Truncate Right",  "truncate_right"),
                                  ("Error",           "error")],
                                 "What to do when the prompt exceeds ctx_size."))
    pb_body.addWidget(_toggle_row("llamacpp.inference.drop_prior_thinking",
                                   "Drop Prior-Turn Thinking",
                                   "LAYER 1 of 3, and the one that wins. On (default): strip thinking blocks "
                                   "from prior assistant turns before the request leaves the proxy, so llama.cpp "
                                   "never sees them and regenerates instead. Off: keep them, re-injected as "
                                   "<think>...</think> for models that need prior reasoning for multi-turn "
                                   "coherence." "\n\n" "While this is ON the other two layers have nothing to "
                                   "act on: the model's Preserve Reasoning toggle (layer 3) greys out, and a "
                                   "preserve_thinking chat-template kwarg (layer 2) is asking the template to "
                                   "re-render content that was already deleted here."))
    layout.addWidget(pb_card)

    # Structured output card
    so_card, so_body = _card("Structured Output", "llamacpp.inference.structured_output.* — JSON schema / GBNF grammar")
    so_body.addWidget(_toggle_row("llamacpp.inference.structured_output.enabled",
                                   "Enabled",
                                   "Force JSON schema or GBNF grammar on every generation."))
    # Inert unless structured output is enabled.
    so_body.addWidget(_dependent(
        _json_row("llamacpp.inference.structured_output.schema",
                                 "JSON Schema", default=None, height=140,
                                 help_text="JSON Schema object — overrides response shape via response_format."),
        ["llamacpp.inference.structured_output.enabled"], lambda e: bool(e)))
    # Inert unless structured output is enabled.
    so_body.addWidget(_dependent(
        _code_row("llamacpp.inference.structured_output.grammar",
                                 "GBNF Grammar", "(empty)",
                                 "Inline GBNF — alternative to JSON Schema.",
                                 height=180, highlighter=_GbnfHighlighter),
        ["llamacpp.inference.structured_output.enabled"], lambda e: bool(e)))
    layout.addWidget(so_card)

    # (Reasoning card removed — its drop_prior_thinking toggle moved into the
    # Proxy Behavior card; reasoning.enabled and reasoning.emit_thinking_blocks
    # are per-model only now and live in the Models tab.)

    # ── Reasoning Effort Map card ────────────────────────────────────
    from PySide6.QtWidgets import QInputDialog as _QInputDialog, QMessageBox as _QMessageBox
    from tray.qt_theme import BG_ELEV as _BG_ELEV_MAP

    map_card, map_body = _card("Reasoning Effort Map (token budget)",
        "Per-request effort presets — selected when the client sends "
        "reasoning_effort: \"low\"/\"medium\"/... Sets llama.cpp's "
        "thinking_budget_tokens (a server body param, model-agnostic). "
        "It does NOT set the template's reasoning_effort string — that "
        "vocabulary is model-specific, so it lives on the Models tab.")

    map_body.addWidget(_toggle_row("llamacpp.inference.thinking_budget.enabled",
        "Enable Token Budget",
        "Off by default. When off, the proxy never sends thinking_budget_tokens "
        "and llama.cpp's own default applies (unrestricted). Turn on only to "
        "impose a hard cap on reasoning tokens per request. The presets below "
        "have no effect while this is off."))

    # The presets only mean anything while the budget layer is on, so grey the
    # whole lot out with it. setEnabled propagates to children, so wrapping the
    # two containers covers every row plus the add-bar.
    _budget_path = "llamacpp.inference.thinking_budget.enabled"
    _budget_on = lambda en: bool(en)

    rows_host = QWidget()
    rows_layout = QVBoxLayout(rows_host)
    rows_layout.setContentsMargins(0, 0, 0, 0)
    rows_layout.setSpacing(8)
    map_body.addWidget(_dependent(rows_host, [_budget_path], _budget_on))

    add_bar = QWidget()
    ab = QHBoxLayout(add_bar)
    ab.setContentsMargins(0, 4, 0, 0)
    ab.setSpacing(8)
    add_key_edit = QLineEdit()
    add_key_edit.setPlaceholderText("preset key (e.g. ultra)")
    add_key_edit.setMinimumWidth(360)
    add_preset_btn = QPushButton("+ Add")
    add_preset_btn.setProperty("class", "primary")
    add_preset_btn.setMaximumWidth(80)
    ab.addWidget(add_key_edit, 1)
    ab.addWidget(add_preset_btn)
    ab.addStretch(1)
    map_body.addWidget(_dependent(add_bar, [_budget_path], _budget_on))

    from config import STANDARD_EFFORT_KEYS as _STD_EFFORT_KEYS

    def _build_effort_row(key: str, data: dict) -> QWidget:
        ek = key.replace(".", r"\.")
        base = f"llamacpp.inference.reasoning_effort_map.{ek}"
        is_standard = key.lower() in _STD_EFFORT_KEYS

        row = QFrame()
        row.setStyleSheet(
            f"QFrame {{ background: {_BG_ELEV_MAP}; border: 1px solid {BORDER};"
            f" border-radius: 6px; }}"
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(10, 6, 10, 6)
        rl.setSpacing(10)

        # Name column — QLabel for standard keys, QLineEdit for custom (renamable)
        if is_standard:
            label_html = (
                f"<b style='color:{FG}'>{key}</b>"
                f" <span style='color:{FG_MUTE}; font-weight:normal; font-size:10.5px;'>· standard</span>"
            )
            head = QLabel(label_html)
            head.setTextFormat(Qt.TextFormat.RichText)
            head.setMinimumWidth(140)
        else:
            head = QLineEdit(key)
            head.setMinimumWidth(140)
            head.setMaximumWidth(180)
            head.setStyleSheet(
                f"QLineEdit {{ background: transparent; border: 1px solid transparent;"
                f" color: {FG}; font-weight: bold; padding: 2px 4px; }}"
                f" QLineEdit:focus {{ border: 1px solid {BORDER}; background: {BG_ELEV}; }}"
            )
            head.setToolTip("Custom preset — edit name to rename. Standard keys (none/minimal/low/medium/high/max/adaptive) cannot be renamed.")

            def _commit_rename(old=key, e=head):
                new = e.text().strip()
                if not new or new == old:
                    e.setText(old)
                    return
                if new.lower() in _STD_EFFORT_KEYS:
                    _QMessageBox.warning(content, "Reserved",
                                         f"'{new}' is a standard key — choose a different name.")
                    e.setText(old)
                    return
                if not re.match(r"^[A-Za-z0-9_-]+$", new):
                    _QMessageBox.warning(content, "Invalid Key",
                                         "Use letters, digits, underscore, or dash only.")
                    e.setText(old)
                    return
                existing = get_path(read_settings(), "llamacpp.inference.reasoning_effort_map", {}) or {}
                if new in existing:
                    _QMessageBox.warning(content, "Exists",
                                         f"Preset '{new}' already exists.")
                    e.setText(old)
                    return
                cur_data = existing.get(old, {})
                old_ek = old.replace(".", r"\.")
                new_ek = new.replace(".", r"\.")
                remove_path(f"llamacpp.inference.reasoning_effort_map.{old_ek}")
                patch_settings(f"llamacpp.inference.reasoning_effort_map.{new_ek}", cur_data)
                _refresh_effort_map()
            head.editingFinished.connect(_commit_rename)

        # Budget Tokens — key absent means "unlimited" (model decides),
        # key present means "cap at N tokens" (auto-clamped to >= 1 at emit
        # time because llama-server silently drops `0` in the body).
        has_budget = "thinking_budget_tokens" in data
        current_value = int(data.get("thinking_budget_tokens", 4096) or 4096)
        if current_value < 1:
            current_value = 4096

        unl_lbl = QLabel("Unlimited"); unl_lbl.setStyleSheet(f"color: {FG_DIM};")
        unl = Toggle()
        unl.setChecked(not has_budget)
        unl.setToolTip("When on, omits thinking_budget_tokens — model decides how long to think.")

        tbt_lbl = QLabel("Budget"); tbt_lbl.setStyleSheet(f"color: {FG_DIM};")
        tbt = NumberEditor(1, 262144, 256, 0, "tok")
        tbt.setValue(float(current_value))

        # "Unlimited" indicator that replaces the Budget editor when active.
        inf_lbl = QLabel("∞")
        inf_lbl.setStyleSheet(f"color: {ACCENT}; font-size: 18px; font-weight: 600;")
        inf_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        def _apply_visibility(is_unlimited: bool):
            tbt_lbl.setVisible(not is_unlimited)
            tbt.setVisible(not is_unlimited)
            inf_lbl.setVisible(is_unlimited)

        _apply_visibility(not has_budget)

        def _on_unl_changed(_state, b=base, editor=tbt, toggle=unl):
            if toggle.isChecked():
                remove_path(f"{b}.thinking_budget_tokens")
                _apply_visibility(True)
            else:
                v = max(1, int(editor.value() or 4096))
                if int(editor.value() or 0) != v:
                    editor.setValue(float(v))
                patch_settings(f"{b}.thinking_budget_tokens", v)
                _apply_visibility(False)

        unl.stateChanged.connect(_on_unl_changed)
        tbt.valueChanged.connect(
            lambda v, b=base: patch_settings(f"{b}.thinking_budget_tokens", max(1, int(v)))
        )

        rm_btn = QPushButton("Remove")
        rm_btn.setProperty("class", "danger")
        rm_btn.setMaximumWidth(90)
        if is_standard:
            rm_btn.setEnabled(False)
            rm_btn.setToolTip("Standard Claude Code / OpenAI effort key — cannot be deleted. Edit Budget inline.")

        rl.addWidget(head)
        rl.addWidget(unl_lbl)
        rl.addWidget(unl)
        rl.addWidget(tbt_lbl)
        rl.addWidget(tbt, 1)
        rl.addWidget(inf_lbl, 1)
        rl.addWidget(rm_btn)

        def _on_remove(_checked=False, b=base, k=key):
            if _QMessageBox.question(content, "Remove Preset",
                                      f"Delete reasoning effort preset '{k}'?") \
                    != _QMessageBox.StandardButton.Yes:
                return
            remove_path(b)
            _refresh_effort_map()
        rm_btn.clicked.connect(_on_remove)

        return row

    def _refresh_effort_map() -> None:
        # Clear existing rows
        while rows_layout.count():
            item = rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        emap = get_path(read_settings(), "llamacpp.inference.reasoning_effort_map", {}) or {}
        if not isinstance(emap, dict) or not emap:
            empty_lbl = QLabel("No presets — add one below.")
            empty_lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11.5px; padding: 6px;")
            rows_layout.addWidget(empty_lbl)
            return
        
        # Standard keys in canonical (ascending-effort) order, custom keys
        # alphabetically after them. Derived from STANDARD_EFFORT_KEYS rather
        # than hardcoded: a literal copy here went stale when `xhigh` was
        # added, dropping it into the unknown-key bucket so it rendered after
        # `max` instead of before it.
        sort_order = {k: i for i, k in enumerate(_STD_EFFORT_KEYS)}

        def _sort_key(k: str):
            k_low = k.lower()
            if k_low in sort_order:
                return (0, sort_order[k_low])
            return (1, k_low)


        sorted_keys = sorted(emap.keys(), key=_sort_key)
        for k in sorted_keys:
            v = emap[k]
            rows_layout.addWidget(_build_effort_row(k, v if isinstance(v, dict) else {}))

    def _on_add_preset() -> None:
        key = (add_key_edit.text() or "").strip()
        if not key:
            key, ok = _QInputDialog.getText(content, "Add Effort Preset",
                                             "Preset key (e.g. ultra):")
            if not ok:
                return
            key = key.strip()
        if not key:
            return
        if not re.match(r"^[A-Za-z0-9_-]+$", key):
            _QMessageBox.warning(content, "Invalid Key",
                                  "Use letters, digits, underscore, or dash only.")
            return
        existing = get_path(read_settings(), "llamacpp.inference.reasoning_effort_map", {}) or {}
        if key in existing:
            _QMessageBox.warning(content, "Exists", f"Preset '{key}' already exists.")
            return
        patch_settings(f"llamacpp.inference.reasoning_effort_map.{key}", {
            "thinking_budget_tokens": 4096,
        })
        add_key_edit.clear()
        _refresh_effort_map()

    add_preset_btn.clicked.connect(_on_add_preset)
    _refresh_effort_map()
    layout.addWidget(map_card)

    # ── Speed Test card ──────────────────────────────────────────────
    from PySide6.QtCore import QObject, Signal as _Signal
    from tray.qt_theme import BG_ELEV as _BG_ELEV

    class _BenchBridge(QObject):
        finished = _Signal(dict)
        progress = _Signal(str)

    bench = _BenchBridge()

    sp_card, sp_body = _card("Speed Test",
                             "Benchmark prompt-eval and generation throughput against llama-server (bypasses proxy)")

    ctx_editor = NumberEditor(0, 262144, 256, 0, "tok")
    ctx_editor.setValue(2048)
    prompt_hint = QLabel("")
    prompt_hint.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px; padding-top: 2px;")

    prompt_col = QWidget()
    pc_l = QVBoxLayout(prompt_col)
    pc_l.setContentsMargins(0, 0, 0, 0)
    pc_l.setSpacing(2)
    pc_l.addWidget(ctx_editor)
    pc_l.addWidget(prompt_hint)
    sp_body.addWidget(_row(row_label("Prompt Tokens",
                                      "Synthetic prompt size to feed for prompt-eval timing. "
                                      "Capped at the active model's ctx_size minus gen tokens "
                                      "and a small chat-template overhead."),
                            prompt_col))

    gen_editor = NumberEditor(8, 4096, 8, 0, "tok")
    gen_editor.setValue(128)
    sp_body.addWidget(_row(row_label("Generated Tokens",
                                      "How many tokens to generate (gen-speed sample). "
                                      "Larger = more accurate gen rate but slower test."),
                            gen_editor))

    _TMPL_OVERHEAD = 512  # chat-template + BOS/role tokens headroom

    def _active_ctx_size() -> tuple[str, int]:
        try:
            from llamacpp import config as _lc
            m = _lc.default_model()
            ctx = int((_lc.model_cfg(m) or {}).get("ctx_size", 0) or 0)
            return m, ctx
        except Exception:
            return "", 0

    def _update_prompt_cap() -> None:
        model, ctx = _active_ctx_size()
        n_pred = int(gen_editor.value())
        cap = max(256, ctx - n_pred - _TMPL_OVERHEAD) if ctx > 0 else 262144
        ctx_editor.setRange(0, float(cap))
        if ctx > 0:
            prompt_hint.setText(
                f"max {cap:,} tok · model ctx {ctx:,} · reserved {n_pred + _TMPL_OVERHEAD:,} "
                f"(gen {n_pred:,} + overhead {_TMPL_OVERHEAD:,})"
            )
        else:
            prompt_hint.setText("no active model — slider uncapped")
        if ctx_editor.value() > cap:
            ctx_editor.setValue(cap)

    gen_editor.valueChanged.connect(lambda *_: _update_prompt_cap())
    _update_prompt_cap()

    ctrl_w = QWidget()
    ctrl_layout = QHBoxLayout(ctrl_w)
    ctrl_layout.setContentsMargins(0, 0, 0, 0)
    ctrl_layout.setSpacing(8)
    run_btn = QPushButton("Run Test")
    run_btn.setProperty("class", "primary")
    sp_status = QLabel("")
    sp_status.setStyleSheet(f"color: {FG_MUTE}; font-size: 11.5px;")
    ctrl_layout.addWidget(run_btn)
    ctrl_layout.addWidget(sp_status, 1)
    sp_body.addWidget(ctrl_w)

    sp_results = QLabel("")
    sp_results.setTextFormat(Qt.TextFormat.RichText)
    sp_results.setWordWrap(True)
    sp_results.setStyleSheet(
        f"color: {FG};"
        f" background: {_BG_ELEV};"
        f" border: 1px solid {BORDER};"
        f" border-radius: 6px;"
        f" padding: 10px 12px;"
        f" font-family: 'JetBrains Mono', Consolas, 'Cascadia Mono', monospace;"
        f" font-size: 12px;"
    )
    sp_results.hide()
    sp_body.addWidget(sp_results)

    def _on_bench_finished(res: dict) -> None:
        run_btn.setEnabled(True)
        if not res.get("ok"):
            sp_status.setText(f"Error: {res.get('error') or 'failed'}")
            sp_results.hide()
            return
        wall_s = float(res.get("wall_ms", 0)) / 1000.0
        model = res.get("model") or "?"
        sp_status.setText(f"Done in {wall_s:.2f}s · model: {model}")

        actual = int(res.get("actual_prompt_tokens", 0))
        pn = int(res.get("prompt_n", 0)); pps = float(res.get("prompt_per_second", 0)); pms = float(res.get("prompt_ms", 0))
        gn = int(res.get("predicted_n", 0)); gps = float(res.get("predicted_per_second", 0)); gms = float(res.get("predicted_ms", 0))
        total_tok = pn + gn
        total_s = (pms + gms) / 1000.0 if (pms + gms) > 0 else wall_s

        def _fmt_tps(v: float) -> str:
            return f"<b style='color:{OK}'>{v:,.1f}</b> tok/s"

        rows = [
            f"<b>Prompt Eval</b> &nbsp; {pn:,} tok &nbsp;·&nbsp; {_fmt_tps(pps)} &nbsp;·&nbsp; {pms:,.0f} ms"
            + (f" &nbsp;<span style='color:{FG_MUTE}'>(requested {actual:,})</span>" if actual and actual != pn else ""),
            f"<b>Generation</b> &nbsp; {gn:,} tok &nbsp;·&nbsp; {_fmt_tps(gps)} &nbsp;·&nbsp; {gms:,.0f} ms",
            f"<b>Combined</b> &nbsp;&nbsp;&nbsp; {total_tok:,} tok &nbsp;·&nbsp; {total_s:.2f} s wall &nbsp;·&nbsp; "
            f"first-token latency ≈ <b>{pms:,.0f} ms</b>",
        ]
        sp_results.setText("<br>".join(rows))
        sp_results.show()

    def _on_bench_progress(msg: str) -> None:
        sp_status.setText(msg)

    bench.finished.connect(_on_bench_finished)
    bench.progress.connect(_on_bench_progress)

    def _run_bench() -> None:
        target = int(ctx_editor.value())
        n_pred = int(gen_editor.value())
        run_btn.setEnabled(False)
        bench.progress.emit(f"Running ({target:,} prompt tok → {n_pred:,} gen tok)…")
        sp_results.hide()

        async def _do() -> None:
            try:
                from llamacpp.benchmark import run_speed_test
                res = await run_speed_test(target, n_predict=n_pred)
            except Exception as exc:
                res = {"ok": False, "error": str(exc)}
            bench.finished.emit(res)

        schedule(window.bot_loop, _do())

    run_btn.clicked.connect(_run_bench)
    layout.addWidget(sp_card)

    layout.addStretch(1)

    # Refresh on timer (models list may change from settings.json edit)
    def refresh() -> None:
        _refresh_models()
        from process import _SUPERVISOR as sup
        alive = bool(sup and sup.alive())
        load_btn.setEnabled(not alive)
        unload_btn.setEnabled(alive)
        restart_btn.setEnabled(alive)
        _update_prompt_cap()
        # Pick up backups created after this page was first built (e.g. by an
        # "Update Now") — cheap no-op unless the .bak-* set changed on disk.
        reload_versions = getattr(version_manager_card, "reload_versions", None)
        if callable(reload_versions):
            reload_versions()
    scroll.refresh = refresh  # type: ignore[attr-defined]
    refresh()
    return scroll


# ══════════════════════════════════════════════════════════════════════
# Proxy
# ══════════════════════════════════════════════════════════════════════

def _proxy(window) -> QWidget:
    scroll, _, layout = _page()

    master, body = _card("Proxy", "Anthropic + OpenAI HTTP surface")
    body.addWidget(_toggle_row("proxy.enabled", "Enabled",
                                "Serves /v1/messages and /v1/chat/completions. Port change needs restart."))

    body.addWidget(_section_header("Network"))
    body.addWidget(_line_row("proxy.host", "Host", "127.0.0.1"))
    body.addWidget(_number_row("proxy.port", "Port", 1024, 65535, 1, 0))

    # Protocols — multi-checkbox
    body.addWidget(_section_header("Protocols"))
    proto_row = QWidget()
    prl = QHBoxLayout(proto_row)
    prl.setContentsMargins(0, 0, 0, 0)
    prl.setSpacing(16)
    for proto in ["anthropic", "openai"]:
        t = Toggle()
        current = set(get_path(read_settings(), "proxy.protocols", []) or [])
        t.setChecked(proto in current)
        def _make_toggle(name=proto, widget=t):
            def _h(_s: int) -> None:
                protos = set(get_path(read_settings(), "proxy.protocols", []) or [])
                if widget.isChecked():
                    protos.add(name)
                else:
                    protos.discard(name)
                patch_settings("proxy.protocols", sorted(protos))
            return _h
        t.stateChanged.connect(_make_toggle())
        sub = QHBoxLayout()
        sub.setSpacing(6)
        cell = QWidget()
        cl = QHBoxLayout(cell)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        cl.addWidget(t)
        lbl = QLabel(format_protocol(proto))
        lbl.setProperty("class", "toggle_label")
        cl.addWidget(lbl)
        prl.addWidget(cell)
    prl.addStretch(1)
    body.addWidget(proto_row)

    body.addWidget(_section_header("Behavior"))
    body.addWidget(_toggle_row("proxy.tool_search", "Tool Search (BM25)",
                                "Split client tools into core + deferred; deferred retrievable via ToolSearch."))
    # Auto-load acts on DEFERRED tools, which only exist when Tool Search splits them out.
    body.addWidget(_dependent(
        _toggle_row("proxy.auto_load_tools", "Auto-Load Tool Schemas",
                                "First blind call to a deferred tool injects its schema automatically."),
        ["proxy.tool_search"], lambda t: bool(t)))
    body.addWidget(_enum_row(
        "proxy.mid_system_messages", "Mid-Session System Messages",
        [("Demote to user, keep position (recommended)", "demote"),
         ("Strip them", "strip"),
         ("Merge into top system block (legacy \u2014 breaks prompt cache)", "merge_top"),
         ("Leave untouched (may 500 on strict templates)", "keep")],
        "What to do with a `system` message that arrives after the conversation has started. "
        "Chat templates like Qwen's refuse a system message that is not first. Demote is the only "
        "option that is both template-safe and cache-safe \u2014 merging them at the top makes the "
        "front block grow every turn, which pins llama.cpp's prefix cache and re-prefills the whole "
        "history on every request.",
        max_width=430))
    body.addWidget(_toggle_row("proxy.strip_reminders", "Strip Client Bookkeeping",
                                "Remove <system-reminder> blocks and per-turn <total_tokens> budget lines "
                                "from message history before forwarding. Skills listings, the deferred-tool "
                                "listing and our own date/location injection are kept."))
    body.addWidget(_toggle_row("proxy.sort_tools", "Sort Tools Alphabetically",
                                "Sort body.tools by name before forwarding. Stabilises the prompt prefix when "
                                "a client reorders its tool list (cache-friendly), at the cost of overriding "
                                "any deliberate primacy ordering. Off by default."))
    body.addWidget(_toggle_row("proxy.debug", "Debug Logging",
                                "Dump full request/response JSON under data/logs/proxy_full_*.json."))

    # Client-context strippers. These already had globals in proxy/config.py
    # and were already honoured by _pget as the fallback — they just had no
    # row here, so the only way to reach them was to hand-edit settings.json.
    # Each is the default for requests matching NO client profile, and the
    # fallback for a profile that omits it; a profile's own toggle wins.
    body.addWidget(_section_header("Client Context"))
    body.addWidget(_toggle_row("proxy.strip_client_system_prompt", "Strip Client System Prompt",
                                "Drop the client's own system prompt and let the System Instruction stand "
                                "in its place. Applies to EVERY matching request — including Claude Code's "
                                "session-title call, whose whole instruction lives in that block. With no "
                                "System Instruction set, the model gets no system prompt at all. "
                                "Per-profile setting overrides this."))
    body.addWidget(_toggle_row("proxy.strip_skills", "Strip Skills Listing",
                                "Drop the `The following skills are available…` catalogue (~8.5KB). Arrives "
                                "in a per-turn system message, so Strip Client Bookkeeping never reaches it. "
                                "The model can no longer pick a skill by name. "
                                "Per-profile setting overrides this."))
    body.addWidget(_toggle_row("proxy.strip_mcp_instructions", "Strip MCP Instructions",
                                "Drop `# MCP Server Instructions`. Same carrier as the skills catalogue. "
                                "This one is guidance your MCP servers supplied — stripping it makes the "
                                "model use those tools blind. Per-profile setting overrides this."))
    body.addWidget(_number_row("proxy.keep_claude_md", "Keep CLAUDE.md Files",
                                -1, 6, 1, 0, "",
                                "How many of the concatenated CLAUDE.md documents to keep, in load order — "
                                "user-global, then project, then nested, then MEMORY.md. -1 = all (leave "
                                "the block alone), 0 = drop it entirely. Also the exclusion from Strip "
                                "Client Bookkeeping: the block rides inside the <system-reminder>, so any "
                                "value >= 0 is honoured either way. Per-profile setting overrides this."))

    body.addWidget(_section_header("Limits"))
    body.addWidget(_number_row("proxy.max_roundtrips", "Max Round-Trips",
                                1, 50, 1, 0, "",
                                "How many intercept/tool rounds before giving up per request."))
    body.addWidget(_number_row("proxy.ping_interval", "Ping Interval",
                                1, 60, 1, 0, "s",
                                "Anthropic `event: ping` frame cadence during long generations."))
    body.addWidget(_line_row("proxy.location", "Location",
                              "auto via ip-api.com",
                              "Appended to system prompt when inject_date_location=true. Empty = auto-detect."))

    body.addWidget(_section_header("Tool Set"))
    # The core/deferred split only happens under Tool Search.
    body.addWidget(_dependent(
        _list_row("proxy.core_tools", "Core Tools",
                              "Names that stay always-loaded for clients (one per line). "
                              "Everything else becomes deferred and goes through ToolSearch.",
                              "Bash"),
        ["proxy.tool_search"], lambda t: bool(t)))
    body.addWidget(_list_row("proxy.cors_origins", "CORS Origins",
                              "Allowed origins for the proxy HTTP server (one per line).",
                              "https://example.com"))

    body.addWidget(_section_header("Model Mapping"))
    body.addWidget(_kv_row("proxy.model_mapping", "Aliases",
                            "Rewrites body.model on each request (one ALIAS=target per line). "
                            "Useful for tricking Claude/OpenAI clients into pointing at your local model.",
                            typed=False))
    layout.addWidget(master)

    # Tailscale Funnel — public HTTPS URL for this machine + copy + live status
    ts_card, ts_refresh = _tailscale_funnel_card()
    layout.addWidget(ts_card)

    # Client profiles — picker + per-profile editor
    layout.addWidget(_proxy_profiles_card())

    layout.addStretch(1)
    # Window calls refresh() every 1s; tailscale_status is TTL-cached so this is cheap.
    scroll.refresh = lambda: ts_refresh(force=False)  # type: ignore[attr-defined]
    return scroll


def _tailscale_funnel_card() -> tuple[QFrame, Any]:
    """Proxy-tab card showing the Tailscale Funnel URL + Copy button + live status."""
    from PySide6.QtWidgets import QApplication

    card, body = _card("Tailscale Funnel",
                       "Public HTTPS URL for this machine's proxy, served via Tailscale Funnel")

    # Status row: colored dot + label + manual refresh
    status_w = QWidget()
    srow = QHBoxLayout(status_w)
    srow.setContentsMargins(0, 0, 0, 0)
    srow.setSpacing(8)
    dot = QLabel("●")
    status_lbl = QLabel("Checking…")
    status_lbl.setProperty("class", "toggle_label")
    refresh_btn = QPushButton("Refresh")
    srow.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
    srow.addWidget(status_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
    srow.addStretch(1)
    srow.addWidget(refresh_btn)
    body.addWidget(status_w)

    # URL row: read-only selectable field + Copy
    url_w = QWidget()
    urow = QHBoxLayout(url_w)
    urow.setContentsMargins(0, 0, 0, 0)
    urow.setSpacing(8)
    url_le = QLineEdit()
    url_le.setReadOnly(True)
    url_le.setPlaceholderText("(unavailable — Tailscale not connected)")
    copy_btn = QPushButton("Copy")
    urow.addWidget(url_le, 1)
    urow.addWidget(copy_btn)
    body.addWidget(url_w)

    def _copy() -> None:
        txt = url_le.text().strip()
        if not txt:
            return
        QApplication.clipboard().setText(txt)
        copy_btn.setText("Copied!")
        QTimer.singleShot(1200, lambda: copy_btn.setText("Copy"))

    copy_btn.clicked.connect(_copy)

    def refresh(force: bool = False) -> None:
        st = tailscale_status(force=force)
        dns = st.get("dns_name", "")
        url = tailscale_funnel_url(dns, 443)  # proxy funnel = https 443
        if url_le.text() != url:
            url_le.setText(url)
        copy_btn.setEnabled(bool(url))

        proxy_enabled = bool(get_path(read_settings(), "proxy.enabled", False))
        if st.get("connected"):
            dot.setStyleSheet(f"color: {OK};")
            status_lbl.setText("Connected — funnel live"
                               if proxy_enabled
                               else "Connected — enable the proxy to serve the funnel")
        elif st.get("pending"):
            dot.setStyleSheet(f"color: {WARN};")
            status_lbl.setText("Checking…")
        else:
            dot.setStyleSheet(f"color: {ERR};")
            status_lbl.setText(st.get("error") or "Not connected")

    refresh_btn.clicked.connect(lambda: refresh(force=True))
    refresh()
    return card, refresh


def _proxy_profiles_card() -> QFrame:
    """Editor for proxy.client_profiles[] — pick → edit → add/remove."""
    from PySide6.QtWidgets import QInputDialog as _QInputDialog, QMessageBox as _QMessageBox

    card, body = _card("Client Profiles",
                       "proxy.client_profiles[] — per-client overrides matched by request header")

    top = QHBoxLayout(); top.setSpacing(8)
    picker = QComboBox(); picker.setMinimumWidth(220)
    add_btn = QPushButton("+ Add Profile"); add_btn.setProperty("class", "primary")
    rm_btn  = QPushButton("Remove");        rm_btn.setProperty("class", "danger")
    top.addWidget(picker); top.addStretch(1); top.addWidget(add_btn); top.addWidget(rm_btn)
    body.addLayout(top)

    form_host = QWidget()
    form_layout = QVBoxLayout(form_host)
    form_layout.setContentsMargins(0, 4, 0, 0)
    form_layout.setSpacing(8)
    body.addWidget(form_host)

    def _profiles() -> list[dict]:
        v = get_path(read_settings(), "proxy.client_profiles", []) or []
        return list(v) if isinstance(v, list) else []

    def _save_profiles(profs: list[dict]) -> None:
        patch_settings("proxy.client_profiles", profs)

    def _clear():
        _flush_pending(form_host)
        while form_layout.count():
            it = form_layout.takeAt(0)
            w = it.widget()
            if w: w.deleteLater()

    def _build_form(idx: int):
        _clear()
        profs = _profiles()
        if idx < 0 or idx >= len(profs):
            return
        prof = profs[idx]

        def _patch(field: str, value):
            ps = _profiles()
            if 0 <= idx < len(ps):
                if value is None:
                    ps[idx].pop(field, None)
                else:
                    ps[idx][field] = value
                _save_profiles(ps)

        def _patch_match(sub: str, value):
            ps = _profiles()
            if 0 <= idx < len(ps):
                m = dict(ps[idx].get("match", {}) or {})
                if value:
                    m[sub] = value
                else:
                    m.pop(sub, None)
                ps[idx]["match"] = m
                _save_profiles(ps)

        # Identity
        form_layout.addWidget(_section_header("Identity"))
        name_le = QLineEdit(); name_le.setText(str(prof.get("name", "")))
        name_le.editingFinished.connect(lambda: _patch("name", name_le.text()))
        form_layout.addWidget(_row(row_label("Name", "Display name (must be unique)."), name_le))

        # Match rule
        form_layout.addWidget(_section_header("Match Rule"))
        hdr_le = QLineEdit(); hdr_le.setText(str((prof.get("match") or {}).get("header", "")))
        hdr_le.setPlaceholderText("User-Agent")
        hdr_le.editingFinished.connect(lambda: _patch_match("header", hdr_le.text()))
        form_layout.addWidget(_row(row_label("Match Header", "HTTP request header name to inspect."), hdr_le))

        cont_le = QLineEdit(); cont_le.setText(str((prof.get("match") or {}).get("contains", "")))
        cont_le.setPlaceholderText("substring")
        cont_le.editingFinished.connect(lambda: _patch_match("contains", cont_le.text()))
        form_layout.addWidget(_row(row_label("Contains", "Substring required in the header value."), cont_le))

        # Behavior toggles
        form_layout.addWidget(_section_header("Behavior"))
        for field, label, hlp in [
            ("tool_search",          "Tool Search",         "BM25 tool retrieval for this client."),
            ("auto_load_tools",      "Auto-Load Tools",     "First blind call injects schema."),
            ("strip_reminders",      "Strip Reminders",     "Drop <system-reminder> blocks + <total_tokens> lines."),
            ("sort_tools",           "Sort Tools",          "Sort body.tools alphabetically (cache-stable)."),
            ("inject_date_location", "Inject Date/Location","Append today's date + location to system prompt."),
        ]:
            cur_val = bool(prof.get(field, False))
            t = Toggle(); t.setChecked(cur_val)
            def _make(field=field, widget=t):
                def _h(_s: int):
                    _patch(field, bool(widget.isChecked()))
                return _h
            t.stateChanged.connect(_make())
            form_layout.addWidget(_row(row_label(label, hlp), _wrap_align(t, Qt.AlignmentFlag.AlignLeft)))

        # Client context — what we REMOVE from what the client sent.
        # The billing header, `# Environment`, `gitStatus:` and the agent-type
        # roster are stripped unconditionally and have no rows here.
        form_layout.addWidget(_section_header("Client Context"))

        # CLAUDE.md is a count, not a switch: `# claudeMd` is every CLAUDE.md
        # on the path concatenated, in load order. It also doubles as the
        # exclusion from Strip Reminders — the block rides inside the reminder,
        # so any value >= 0 is honoured whether reminders are stripped or not.
        try:
            _cur_keep = int(prof.get("keep_claude_md", -1))
        except (TypeError, ValueError):
            _cur_keep = -1
        keep_cb = QComboBox()
        _keep_opts: list[tuple[str, int]] = [
            ("All (no limit)", -1), ("None — drop the block", 0)]
        _keep_opts += [(f"Keep first {n}", n) for n in range(1, 7)]
        for _txt, _val in _keep_opts:
            keep_cb.addItem(_txt, _val)
        keep_cb.setCurrentIndex(
            next((i for i, (_, v) in enumerate(_keep_opts) if v == _cur_keep), 0))
        keep_cb.currentIndexChanged.connect(
            lambda _i: _patch("keep_claude_md", int(keep_cb.currentData())))
        form_layout.addWidget(_row(
            row_label("Keep CLAUDE.md Files",
                      "How many of the concatenated CLAUDE.md documents to keep, "
                      "in load order — user-global, then project, then nested, "
                      "then MEMORY.md. Also the exclusion from Strip Reminders, "
                      "which would otherwise take the whole block."),
            keep_cb))

        for field, label, hlp in [
            ("strip_client_system_prompt", "Strip Client System Prompt",
             "Drop the client's own system prompt and let the System "
             "Instruction below stand in its place. Applies to EVERY request "
             "this profile matches — including Claude Code's session-title "
             "call, whose whole instruction lives in that block. With no "
             "System Instruction set, the model gets no system prompt at all."),
            ("strip_skills", "Strip Skills Listing",
             "Drop the `The following skills are available…` catalogue "
             "(~8.5KB). Arrives in a per-turn system message, so Strip "
             "Reminders never reaches it. The model can no longer pick a "
             "skill by name."),
            ("strip_mcp_instructions", "Strip MCP Instructions",
             "Drop `# MCP Server Instructions`. Same carrier as the skills "
             "catalogue. This one is guidance your MCP servers supplied — "
             "stripping it makes the model use those tools blind."),
        ]:
            cur_val = bool(prof.get(field, False))
            t = Toggle(); t.setChecked(cur_val)
            def _make_cc(field=field, widget=t):
                def _h(_s: int):
                    _patch(field, bool(widget.isChecked()))
                return _h
            t.stateChanged.connect(_make_cc())
            form_layout.addWidget(_row(row_label(label, hlp),
                                       _wrap_align(t, Qt.AlignmentFlag.AlignLeft)))

        # System instruction
        form_layout.addWidget(_section_header("System Instruction"))
        from pathlib import Path
        instructions_dir = Path(__file__).resolve().parent.parent / "proxy" / "instructions"
        instruction_options = [("(none)", "")]
        if instructions_dir.is_dir():
            for f in sorted(instructions_dir.iterdir()):
                if f.is_file():
                    instruction_options.append((f.name, f.name))
        
        cur_val = str(prof.get("system_instruction", "") or "")
        if cur_val and not any(v == cur_val for _, v in instruction_options):
            instruction_options.append((cur_val, cur_val))
            
        si_cb = QComboBox()
        selected_idx = 0
        for i, (disp, val) in enumerate(instruction_options):
            si_cb.addItem(disp, val)
            if cur_val == val:
                selected_idx = i
        si_cb.setCurrentIndex(selected_idx)
        si_cb.currentIndexChanged.connect(lambda i: _patch("system_instruction", si_cb.itemData(i) or None))
        
        form_layout.addWidget(_row(row_label("Instruction File",
                                              "Filename in proxy/instructions/. Empty = no injection."),
                                   _wrap_align(si_cb, Qt.AlignmentFlag.AlignLeft)))

        # Lists
        form_layout.addWidget(_section_header("Tool Lists"))
        from PySide6.QtWidgets import QPlainTextEdit
        def _list_editor(field: str, label: str, hlp: str) -> QWidget:
            from tray.qt_theme import BG_ELEV as _BG_ELEV

            host = QWidget()
            hl = QVBoxLayout(host)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(6)

            rows_host = QWidget()
            rows_layout = QVBoxLayout(rows_host)
            rows_layout.setContentsMargins(0, 0, 0, 0)
            rows_layout.setSpacing(6)
            hl.addWidget(rows_host)

            add_w = QWidget()
            add_l = QHBoxLayout(add_w)
            add_l.setContentsMargins(0, 0, 0, 0)
            add_l.setSpacing(6)
            add_btn = QPushButton("+ Add")
            add_btn.setProperty("class", "primary")
            add_btn.setMaximumWidth(110)
            add_l.addWidget(add_btn)
            add_l.addStretch(1)
            hl.addWidget(add_w)

            entries: list[tuple[QLineEdit, QWidget]] = []

            def _commit() -> None:
                vals = [e.text().strip() for e, _ in entries if e.text().strip()]
                _patch(field, vals)

            def _build_row(value: str = "") -> QWidget:
                row = QFrame()
                row.setStyleSheet(
                    f"QFrame {{ background: {_BG_ELEV}; border: 1px solid {BORDER};"
                    f" border-radius: 6px; }}"
                )
                rl = QHBoxLayout(row)
                rl.setContentsMargins(8, 6, 8, 6)
                rl.setSpacing(6)
                edit = QLineEdit(); edit.setText(value)
                edit.setPlaceholderText("name")
                rm_btn = QPushButton("✕")
                rm_btn.setFlat(True)
                rm_btn.setFixedWidth(28)
                rm_btn.setStyleSheet(
                    f"QPushButton {{ color: {FG_DIM}; border: none; background: transparent; }}"
                    f" QPushButton:hover {{ color: #ff6b6b; }}"
                )
                rl.addWidget(edit, 1)
                rl.addWidget(rm_btn)

                entry = (edit, row)
                entries.append(entry)
                edit.editingFinished.connect(_commit)

                def _remove():
                    try:
                        entries.remove(entry)
                    except ValueError:
                        pass
                    row.setParent(None)
                    row.deleteLater()
                    _commit()
                rm_btn.clicked.connect(_remove)

                rows_layout.addWidget(row)
                return row

            for item in (prof.get(field, []) or []):
                _build_row(str(item))

            def _on_add():
                row = _build_row("")
                try:
                    row.findChild(QLineEdit).setFocus()
                except Exception:
                    pass
            add_btn.clicked.connect(_on_add)

            return _row(row_label(label, hlp), host)

        def _managed_checklist(field: str, label: str, hlp: str) -> QWidget:
            """Styled inject_managed selector matching the Managed Tools layout.

            Groups tools by prefix (docgraph_* gets a DOCGRAPH header with
            └─ Humanized labels + muted key below). Other tools render as flat
            rows. Two-column layout using Toggle widgets, exactly like the
            Managed section.
            """
            from collections import Counter
            from proxy import managed_tools as _mt
            from PySide6.QtGui import QPalette, QColor

            available = sorted(_mt._REGISTRY.keys())
            current_set = set(prof.get(field, []) or [])
            # include tools already saved in THIS profile not yet in live registry
            extras = sorted(current_set - set(available))
            all_names = available + extras

            toggles_map: dict[str, Toggle] = {}

            def _commit():
                selected = [n for n in all_names if toggles_map.get(n) and toggles_map[n].isChecked()]
                _patch(field, selected)

            # Detect prefixes used ≥2 times → render as a group
            prefix_counts: Counter = Counter()
            for n in all_names:
                if "_" in n:
                    prefix_counts[n.split("_", 1)[0]] += 1
            group_prefixes = {p for p, c in prefix_counts.items() if c >= 2}

            _bg_color = QColor(BG_ELEV)
            inner = QWidget()
            inner.setAutoFillBackground(True)
            _ip = QPalette(); _ip.setColor(QPalette.ColorRole.Window, _bg_color)
            inner.setPalette(_ip)
            vl = QVBoxLayout(inner)
            vl.setContentsMargins(10, 8, 10, 8)
            vl.setSpacing(2)

            def _make_cell(name: str, display: str, key_hint: str) -> QWidget:
                """One (label-stack | toggle) cell."""
                from proxy.runtime_state import is_managed_enabled
                globally_on = is_managed_enabled(name)

                t = Toggle()
                t.setChecked(name in current_set)
                t.setEnabled(globally_on)   # greyed-out when disabled in Managed section
                toggles_map[name] = t

                def _on(_s: int, _n=name, _t=t):
                    _commit()

                t.stateChanged.connect(_on)

                fg = FG if globally_on else FG_MUTE
                lbl = QLabel(display)
                lbl.setStyleSheet(f"color: {fg}; font-size: 12px;")
                sub = QLabel(key_hint)
                sub.setStyleSheet(f"color: {FG_MUTE}; font-size: 10px;")

                lstack = QWidget()
                ll = QVBoxLayout(lstack)
                ll.setContentsMargins(0, 0, 0, 0)
                ll.setSpacing(0)
                ll.addWidget(lbl)
                ll.addWidget(sub)

                cell = QWidget()
                cl = QHBoxLayout(cell)
                cl.setContentsMargins(0, 2, 0, 2)
                cl.setSpacing(6)
                cl.addWidget(lstack, 1)
                cl.addWidget(_wrap_align(t, Qt.AlignmentFlag.AlignLeft), 0)
                return cell

            def _flush_pair_row(pair: list) -> None:
                row_w = QWidget()
                hl = QHBoxLayout(row_w)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.setSpacing(14)
                for cell in pair:
                    hl.addWidget(cell, 1)
                if len(pair) < 2:
                    hl.addWidget(QWidget(), 1)
                vl.addWidget(row_w)

            seen_groups: set[str] = set()

            # --- grouped pass: render each group as 2-col rows ---
            for prefix in sorted(group_prefixes):
                group_names = [n for n in all_names if n.startswith(prefix + "_")]
                if not group_names:
                    continue
                seen_groups.add(prefix)

                # group header + master toggle
                from proxy.runtime_state import is_managed_enabled as _ime
                any_globally_on = any(_ime(n) for n in group_names)
                hdr = QLabel(humanize(prefix).upper())
                hdr.setStyleSheet(
                    f"color: {FG if any_globally_on else FG_MUTE};"
                    f" font-size: 12px; font-weight: 600;"
                    f" letter-spacing: 0.06em; padding-top: 6px;"
                )
                grp_t = Toggle()
                grp_t.setChecked(any(n in current_set for n in group_names))
                grp_t.setEnabled(any_globally_on)

                def _grp_on(_s: int, names=tuple(group_names), gt=grp_t):
                    on = gt.isChecked()
                    for cn in names:
                        tw = toggles_map.get(cn)
                        if tw and tw.isChecked() != on:
                            tw.setChecked(on)

                grp_t.stateChanged.connect(_grp_on)
                hdr_row = QWidget()
                hrl = QHBoxLayout(hdr_row)
                hrl.setContentsMargins(0, 0, 0, 0)
                hrl.addWidget(hdr, 1)
                hrl.addWidget(_wrap_align(grp_t, Qt.AlignmentFlag.AlignLeft), 0)
                vl.addWidget(hdr_row)

                # child rows in pairs
                pair: list = []
                for name in group_names:
                    tail = name[len(prefix) + 1:] if name.startswith(prefix + "_") else name
                    cell = _make_cell(name, f"  └─ {humanize(tail)}", name)
                    pair.append(cell)
                    if len(pair) == 2:
                        _flush_pair_row(pair)
                        pair = []
                if pair:
                    _flush_pair_row(pair)

            # --- flat tools (no group) ---
            flat_names = [n for n in all_names
                          if n.split("_", 1)[0] not in group_prefixes or "_" not in n]
            pair = []
            for name in flat_names:
                cell = _make_cell(name, humanize(name), name)
                pair.append(cell)
                if len(pair) == 2:
                    _flush_pair_row(pair)
                    pair = []
            if pair:
                _flush_pair_row(pair)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.viewport().setAutoFillBackground(True)
            _vp = QPalette(); _vp.setColor(QPalette.ColorRole.Window, _bg_color)
            scroll.viewport().setPalette(_vp)
            scroll.setWidget(inner)
            # height: header rows + child pair rows + flat pair rows, ~44px each
            n_groups = len(group_prefixes)
            n_group_children = sum(
                -(-len([n for n in all_names if n.startswith(p + "_")]) // 2)
                for p in group_prefixes
            )
            flat_names_count = len(all_names) - sum(
                len([n for n in all_names if n.startswith(p + "_")])
                for p in group_prefixes
            )
            n_flat_rows = -(-(flat_names_count) // 2)
            est_h = (n_groups + n_group_children + n_flat_rows) * 44 + 20
            scroll.setFixedHeight(min(400, max(80, est_h)))
            scroll.setStyleSheet(
                f"QScrollArea {{ background: {BG_ELEV}; border: 1px solid {BORDER};"
                f" border-radius: 6px; }}"
            )
            return _row(row_label(label, hlp), scroll)

        form_layout.addWidget(_managed_checklist("inject_managed", "Inject Managed",
                                                  "Managed tools to inject for this client."))
        form_layout.addWidget(_list_editor("core_tools", "Core Tools (override)",
                                            "Override the global proxy.core_tools for this client. Empty = inherit."))
        form_layout.addWidget(_list_editor("strip_tool_names", "Strip Tool Names",
                                            "Tool names to remove from the client-supplied tool set."))

    def _refresh_picker(preserve_idx: int | None = None):
        picker.blockSignals(True)
        picker.clear()
        profs = _profiles()
        for i, p in enumerate(profs):
            picker.addItem(f"{p.get('name', f'profile-{i}')}", i)
        picker.blockSignals(False)
        if profs:
            idx = preserve_idx if (preserve_idx is not None and 0 <= preserve_idx < len(profs)) else 0
            picker.setCurrentIndex(idx)
            _build_form(idx)
        else:
            _clear()
            empty = QLabel("No profiles — add one above.")
            empty.setStyleSheet(f"color: {FG_MUTE}; font-size: 11.5px; padding: 6px;")
            form_layout.addWidget(empty)

    def _on_pick(_i: int):
        idx = picker.currentData()
        if idx is not None:
            _build_form(int(idx))

    def _on_add():
        name, ok = _QInputDialog.getText(card, "Add Profile", "Profile name:")
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            return
        profs = _profiles()
        if any(p.get("name") == name for p in profs):
            _QMessageBox.warning(card, "Exists", f"Profile '{name}' already exists.")
            return
        profs.append({
            "name": name,
            "match": {"header": "User-Agent", "contains": ""},
            "tool_search": False,
            "auto_load_tools": False,
            "strip_reminders": False,
            "sort_tools": False,
            "inject_date_location": False,
            "keep_claude_md": -1,
            "strip_client_system_prompt": False,
            "strip_skills": False,
            "strip_mcp_instructions": False,
            "inject_managed": [],
            "core_tools": [],
            "strip_tool_names": [],
        })
        _save_profiles(profs)
        _refresh_picker(preserve_idx=len(profs) - 1)

    def _on_remove():
        idx = picker.currentData()
        if idx is None:
            return
        idx = int(idx)
        profs = _profiles()
        if idx < 0 or idx >= len(profs):
            return
        nm = profs[idx].get("name", f"profile-{idx}")
        if _QMessageBox.question(card, "Remove Profile", f"Delete '{nm}'?") \
                != _QMessageBox.StandardButton.Yes:
            return
        del profs[idx]
        _save_profiles(profs)
        _refresh_picker()

    picker.currentIndexChanged.connect(_on_pick)
    add_btn.clicked.connect(_on_add)
    rm_btn.clicked.connect(_on_remove)
    _refresh_picker()

    # Rebuild the form when managed-tool enabled states change (e.g. user
    # toggles a tool in the Managed Tools section while this profile is open).
    def _managed_state_key() -> frozenset:
        from proxy import managed_tools as _mt
        from proxy.runtime_state import is_managed_enabled
        return frozenset((n, is_managed_enabled(n)) for n in _mt._REGISTRY)

    _last_mgt: list[frozenset] = [_managed_state_key()]

    def _tick_managed():
        cur = _managed_state_key()
        if cur != _last_mgt[0]:
            _last_mgt[0] = cur
            idx = picker.currentData()
            if idx is not None:
                _build_form(int(idx))

    _mgt_timer = QTimer(card)
    _mgt_timer.setInterval(1000)
    _mgt_timer.timeout.connect(_tick_managed)
    _mgt_timer.start()

    return card


# ══════════════════════════════════════════════════════════════════════
# MCP / Managed / Telegram / Voice / Computer / Sessions / Logs
# ══════════════════════════════════════════════════════════════════════

def _mcp(window) -> QWidget:
    import pkgutil
    import os
    import mcp_server.tools as _tools_pkg
    from proxy.runtime_state import set_tool, is_mcp_tool_enabled

    scroll, _, layout = _page()
    card, body = _card("MCP Server")
    body.addWidget(_toggle_row("mcp_server.enabled", "Enabled",
                                "Streamable HTTP MCP server for external clients. Restart required."))

    body.addWidget(_section_header("Network"))
    # Nothing is bound while the MCP server is off.
    body.addWidget(_dependent(
        _line_row("mcp_server.host", "Host", "127.0.0.1"),
        ["mcp_server.enabled"], lambda e: bool(e)))
    # Nothing is bound while the MCP server is off.
    body.addWidget(_dependent(
        _number_row("mcp_server.port", "Port", 1024, 65535, 1, 0),
        ["mcp_server.enabled"], lambda e: bool(e)))
    # Nothing is bound while the MCP server is off.
    body.addWidget(_dependent(
        _list_row("mcp_server.cors_origins", "CORS Origins",
                              "Allowed origins for the MCP HTTP server (one per line). Empty = no CORS.",
                              "https://example.com"),
        ["mcp_server.enabled"], lambda e: bool(e)))

    body.addWidget(_section_header("Registered Tools"))
    tools_wrap = QWidget()
    tw = QVBoxLayout(tools_wrap)
    tw.setContentsMargins(0, 0, 0, 0)
    tw.setSpacing(10)
    body.addWidget(tools_wrap)
    layout.addWidget(card)
    layout.addStretch(1)

    # Cache for toggles so refresh can sync state
    toggles: dict[str, Toggle] = {}

    def _rebuild() -> None:
        for i in reversed(range(tw.count())):
            w = tw.itemAt(i).widget()
            if w: w.deleteLater()
        toggles.clear()

        pkg_dir = os.path.dirname(_tools_pkg.__file__)
        tool_modules = [name for _, name, _ in pkgutil.iter_modules([pkg_dir])]

        if not tool_modules:
            l = QLabel("—  No tool modules found")
            l.setStyleSheet(f"color: {FG_MUTE};")
            tw.addWidget(l)
            return

        for name in sorted(tool_modules):
            enabled = is_mcp_tool_enabled(name)
            t = Toggle()
            t.setChecked(enabled)
            def _toggle(_s, n=name, widget=t):
                set_tool("mcp_tools", n, widget.isChecked())
            t.stateChanged.connect(_toggle)
            toggles[name] = t

            tw.addWidget(_row(row_label(humanize(name), "", name),
                               _wrap_align(t, Qt.AlignmentFlag.AlignLeft)))

            nl = name.lower()
            if nl == "stt":
                tw.addWidget(_line_row("mcp_server.stt_url", "  └─ Endpoint", "http://127.0.0.1:6600"))
            elif nl == "tts":
                tw.addWidget(_line_row("mcp_server.tts_url", "  └─ Endpoint", "http://127.0.0.1:6600"))

    def refresh() -> None:
        # For now, just sync toggles with runtime_state
        from proxy.runtime_state import load
        current_state = load().get("mcp_tools", {})
        for name, t in toggles.items():
            enabled = current_state.get(name, True)
            if t.isChecked() != enabled:
                t.blockSignals(True)
                t.setChecked(enabled)
                t.blockSignals(False)

    _rebuild()
    scroll.refresh = refresh  # type: ignore[attr-defined]
    return scroll


def _managed(window) -> QWidget:
    scroll, _, layout = _page()
    card, body = _card("Managed Tools",
                       "Proxy-injected tools — WebSearch, code_execution, speak, transcribe, + bridged MCP")
    rows_wrap = QVBoxLayout()
    rows_wrap.setSpacing(10)
    body.addLayout(rows_wrap)

    layout.addWidget(card)

    layout.addStretch(1)

    # name -> Toggle widget, so refresh can sync state without rebuilding.
    toggles: dict[str, Toggle] = {}
    # prefix -> (master Toggle, [child names]) for grouped sections.
    group_toggles: dict[str, tuple[Toggle, list[str]]] = {}
    # Tracks last tool ordering so we only tear down on actual changes.
    last_names: list[str] = []
    empty_label: QLabel | None = None

    def _rebuild(tools: list[dict]) -> None:
        nonlocal empty_label
        for i in reversed(range(rows_wrap.count())):
            w = rows_wrap.itemAt(i).widget()
            if w:
                w.deleteLater()
        toggles.clear()
        group_toggles.clear()
        empty_label = None
        if not tools:
            empty_label = QLabel("No managed tools registered.")
            empty_label.setStyleSheet(f"color: {FG_MUTE};")
            rows_wrap.addWidget(empty_label)
            return
        from proxy.runtime_state import set_tool
        # Group rows by leading prefix so families (e.g. docgraph_*) render
        # under a single header with └─ child indentation, like the Kokoro
        # endpoint hangs off Speak. Prefix has to appear at least twice to
        # qualify; lone tools render as flat top-level rows.
        from collections import Counter
        prefixes = Counter()
        for t in tools:
            n = t.get("name", "")
            if "_" in n:
                prefixes[n.split("_", 1)[0]] += 1
        group_prefixes = {p for p, c in prefixes.items() if c >= 2}
        seen_groups: set[str] = set()

        # Buffer for the docgraph group — rendered as a 2-column grid above
        # the rest of the managed tools instead of one tall column.
        docgraph_cells: list[tuple[QWidget, QWidget]] = []

        def _flush_docgraph_grid() -> None:
            if not docgraph_cells:
                return
            for start in range(0, len(docgraph_cells), 2):
                chunk = docgraph_cells[start:start + 2]
                row_w = QWidget()
                hl = QHBoxLayout(row_w)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.setSpacing(14)
                for (lw, tw) in chunk:
                    cell = QWidget()
                    cl = QHBoxLayout(cell)
                    cl.setContentsMargins(0, 0, 0, 0)
                    cl.setSpacing(6)
                    cl.addWidget(lw, 1)
                    cl.addWidget(_wrap_align(tw, Qt.AlignmentFlag.AlignLeft), 0)
                    hl.addWidget(cell, 1)
                # Pad short final row so cell widths stay consistent
                for _ in range(2 - len(chunk)):
                    hl.addWidget(QWidget(), 1)
                rows_wrap.addWidget(row_w)
            docgraph_cells.clear()

        docgraph_tools = [t for t in tools if t.get("name", "").startswith("docgraph_")]
        other_tools = [t for t in tools if not t.get("name", "").startswith("docgraph_")]

        def _render_docgraph_group() -> None:
            if not docgraph_tools:
                return
            prefix = "docgraph"
            seen_groups.add(prefix)
            hdr_label = QLabel(humanize(prefix))
            hdr_label.setStyleSheet(
                f"color: {FG}; font-size: 13px; font-weight: 500; "
                f"text-transform: uppercase; letter-spacing: 0.06em; "
                f"padding-top: 4px;"
            )
            children = [x.get("name", "") for x in docgraph_tools]
            any_on = any(bool(x.get("enabled", True)) for x in docgraph_tools)
            grp_toggle = Toggle()
            grp_toggle.setChecked(any_on)

            def _grp_toggle(_s: int, names=tuple(children), gw=grp_toggle) -> None:
                on = gw.isChecked()
                for cn in names:
                    cw = toggles.get(cn)
                    if cw is None:
                        continue
                    if cw.isChecked() != on:
                        cw.setChecked(on)

            grp_toggle.stateChanged.connect(_grp_toggle)
            group_toggles[prefix] = (grp_toggle, list(children))
            rows_wrap.addWidget(_row(hdr_label,
                                      _wrap_align(grp_toggle, Qt.AlignmentFlag.AlignLeft)))

            for t in docgraph_tools:
                name = t.get("name", "?")
                enabled = t.get("enabled", True)
                t_widget = Toggle()
                t_widget.setChecked(enabled)

                def _toggle(_s: int, n=name, tw=t_widget) -> None:
                    set_tool("managed_tools", n, tw.isChecked())

                t_widget.stateChanged.connect(_toggle)
                toggles[name] = t_widget

                tail = name[len(prefix) + 1:] if name.startswith(prefix + "_") else name
                label_w = row_label(f"  └─ {humanize(tail)}", "", name)
                docgraph_cells.append((label_w, t_widget))

            _flush_docgraph_grid()

        _render_docgraph_group()

        for t in other_tools:
            name = t.get("name", "?")
            nl = name.lower()
            enabled = t.get("enabled", True)

            prefix = name.split("_", 1)[0] if "_" in name else ""
            in_group = prefix in group_prefixes

            # Leaving the docgraph block — flush whatever we collected
            # before rendering the next (non-docgraph) row.
            if in_group and prefix not in seen_groups:
                seen_groups.add(prefix)
                hdr_label = QLabel(humanize(prefix))
                hdr_label.setStyleSheet(
                    f"color: {FG}; font-size: 13px; font-weight: 500; "
                    f"text-transform: uppercase; letter-spacing: 0.06em; "
                    f"padding-top: 4px;"
                )
                # Master toggle for the whole group: ON if any child is on,
                # toggling sets every child to that state.
                children = [x.get("name", "") for x in tools
                            if x.get("name", "").startswith(prefix + "_")]
                any_on = any(
                    bool(x.get("enabled", True)) for x in tools
                    if x.get("name", "") in children
                )
                grp_toggle = Toggle()
                grp_toggle.setChecked(any_on)

                def _grp_toggle(_s: int, names=tuple(children), gw=grp_toggle) -> None:
                    on = gw.isChecked()
                    for cn in names:
                        cw = toggles.get(cn)
                        if cw is None:
                            continue
                        if cw.isChecked() != on:
                            cw.setChecked(on)  # cascades through child stateChanged
                grp_toggle.stateChanged.connect(_grp_toggle)
                group_toggles[prefix] = (grp_toggle, list(children))
                rows_wrap.addWidget(_row(hdr_label,
                                          _wrap_align(grp_toggle, Qt.AlignmentFlag.AlignLeft)))

            t_widget = Toggle()
            t_widget.setChecked(enabled)
            def _toggle(_s: int, n=name, tw=t_widget) -> None:
                set_tool("managed_tools", n, tw.isChecked())
            t_widget.stateChanged.connect(_toggle)
            toggles[name] = t_widget

            if in_group:
                # Tail of the name after the prefix, humanized.
                tail = name[len(prefix) + 1:] if name.startswith(prefix + "_") else name
                label_text = f"  └─ {humanize(tail)}"
            else:
                label_text = humanize(name)

            label_w = row_label(label_text, "", name)
            rows_wrap.addWidget(_row(label_w,
                                      _wrap_align(t_widget, Qt.AlignmentFlag.AlignLeft)))

            if nl == "transcribe":
                rows_wrap.addWidget(_line_row("mcp_server.stt_url", "  └─ Endpoint", "http://127.0.0.1:6600"))
            elif nl == "speak":
                rows_wrap.addWidget(_line_row("mcp_server.tts_url", "  └─ Endpoint", "http://127.0.0.1:6600"))

    def refresh() -> None:
        nonlocal last_names
        tools = build_status().get("managed", [])
        names = [t.get("name", "?") for t in tools]
        if names != last_names:
            _rebuild(tools)
            last_names = names
            return
        # Same set of tools — just sync check state without animating or
        # re-triggering the stateChanged -> set_tool write.
        for t in tools:
            name = t.get("name", "?")
            enabled = bool(t.get("enabled", True))
            tw = toggles.get(name)
            if tw is None or tw.isChecked() == enabled:
                continue
            tw.blockSignals(True)
            tw.setChecked(enabled)
            tw.blockSignals(False)
        # Reflect mixed/all-on/all-off state on the group master toggles.
        for prefix, (gw, children) in group_toggles.items():
            on_states = [bool(toggles[c].isChecked()) for c in children if c in toggles]
            if not on_states:
                continue
            any_on = any(on_states)
            if gw.isChecked() != any_on:
                gw.blockSignals(True)
                gw.setChecked(any_on)
                gw.blockSignals(False)
    scroll.refresh = refresh  # type: ignore[attr-defined]
    refresh()
    return scroll


def _telegram(window) -> QWidget:
    scroll, _, layout = _page()

    # ── Live controls card ────────────────────────────────────────────
    from tray.qt_docgraph import _status_pill, _run  # reuse same helpers
    from tray.qt_theme import OK, ERR, FG_MUTE

    ctrl_card, cb = _card("Bot Control", "Start · Stop · Restart the Telegram polling loop")

    cb.addWidget(_toggle_row("telegram.auto_start",
                              "Auto-start",
                              "Start the bot automatically when Telecode launches."))
    cb.addWidget(_toggle_row("telegram.auto_restart",
                              "Auto-restart",
                              "Re-start polling if the updater stops unexpectedly."))

    actions_w = QWidget()
    ar = QHBoxLayout(actions_w)
    ar.setContentsMargins(0, 0, 0, 0); ar.setSpacing(8)
    start_btn   = QPushButton("▶ Start"); start_btn.setProperty("class", "primary")
    stop_btn    = QPushButton("Stop");    stop_btn.setProperty("class", "danger")
    restart_btn = QPushButton("Restart")
    ar.addWidget(start_btn); ar.addWidget(stop_btn); ar.addWidget(restart_btn)
    ar.addStretch(1)
    cb.addWidget(_row(row_label("Actions"), actions_w))

    def _bot_status_text() -> tuple[bool, str]:
        try:
            from bot.supervisor import status_snapshot
            s = status_snapshot()
        except Exception as exc:
            return False, f"err: {exc}"
        if s.get("alive"):
            return True, "polling"
        err = s.get("last_error")
        if err:
            return False, f"error: {err[:60]}"
        return False, "stopped"

    status_pill, refresh_pill = _status_pill(_bot_status_text)
    cb.addWidget(_row(row_label("Status"), status_pill))

    def _refresh_ctrl() -> None:
        try:
            from bot.supervisor import status_snapshot
            s = status_snapshot()
            alive = bool(s.get("alive"))
            busy  = bool(s.get("busy"))
        except Exception:
            alive = busy = False
        start_btn.setEnabled(not alive and not busy)
        stop_btn.setEnabled(alive and not busy)
        restart_btn.setEnabled(alive and not busy)
        refresh_pill()

    def _on_start():
        async def _go():
            from bot.supervisor import get_supervisor
            sup = get_supervisor()
            if sup:
                await sup.start()
        _run(window, _go)

    def _on_stop():
        async def _go():
            from bot.supervisor import get_supervisor
            sup = get_supervisor()
            if sup:
                await sup.stop()
        _run(window, _go)

    def _on_restart():
        async def _go():
            from bot.supervisor import get_supervisor
            sup = get_supervisor()
            if sup:
                await sup.restart()
        _run(window, _go)

    start_btn.clicked.connect(_on_start)
    stop_btn.clicked.connect(_on_stop)
    restart_btn.clicked.connect(_on_restart)
    _refresh_ctrl()
    layout.addWidget(ctrl_card)

    # ── Credentials / access card ─────────────────────────────────────
    bot_card, bb = _card("Telegram Bot", "telegram.* — token + group + access")
    bb.addWidget(_password_row("telegram.bot_token", "Bot Token", "123456:ABC-DEF...",
                                "From @BotFather. Restart required after change."))
    bb.addWidget(_number_row("telegram.group_id", "Group ID",
                              -10000000000000.0, 10000000000000.0, 1, 0, "",
                              "Numeric chat id (negative for supergroups). Restart required."))
    bb.addWidget(_int_list_row("telegram.allowed_user_ids", "Allowed User IDs",
                                "Whitelist of Telegram user ids. Empty = anyone in the group.",
                                "one user_id per line"))
    layout.addWidget(bot_card)

    scroll.refresh = _refresh_ctrl  # type: ignore[attr-defined]

    paths_card, pb = _card("Paths", "paths.* — file locations (relative paths anchor to settings.json directory)")
    pb.addWidget(_line_row("paths.store_path", "Store Path", "./data/telecode.json",
                            "Topic mapping JSON — survives restarts."))
    pb.addWidget(_line_row("paths.logs_dir", "Logs Dir", "./data/logs",
                            "Where telecode/llama/proxy/mcp/voice logs are written."))
    layout.addWidget(paths_card)

    stream_card, sb = _card("Streaming", "Telegram message edit + PTY flush tuning")
    sb.addWidget(_number_row("streaming.interval_sec",       "Edit Interval",        0.3, 3.0, 0.1, 1, "s"))
    sb.addWidget(_number_row("streaming.max_message_length", "Max Message Length",   500, 4096, 100, 0))
    sb.addWidget(_number_row("streaming.idle_timeout_sec",   "Session Idle Timeout", 60, 86400, 60, 0, "s"))
    sb.addWidget(_number_row("streaming.idle_sec",           "PTY Idle Threshold",   0.3, 10.0, 0.1, 1, "s"))
    sb.addWidget(_number_row("streaming.max_wait_sec",       "PTY Max Wait",         1.0, 30.0, 0.5, 1, "s"))
    sb.addWidget(_toggle_row("streaming.dump_raw_pty",       "Dump Raw PTY",
                              "Write raw PTY bytes to data/logs/pty_<cmd>_<timestamp>.bin + .txt for diagnosing missing-output issues. Restart the bot after toggling."))
    layout.addWidget(stream_card)

    cap_card, cb = _card("Capture", "Screen image / video intervals")
    cb.addWidget(_number_row("capture.image_interval", "Image Interval", 1, 300, 1, 0, "s"))
    cb.addWidget(_number_row("capture.video_interval", "Video Chunk",    10, 600, 10, 0, "s"))
    layout.addWidget(cap_card)

    hb_card, hb = _card("Heartbeat Scheduler",
                         "heartbeat.* — periodic agent job firing from HEARTBEAT.md entries")
    hb.addWidget(_toggle_row("heartbeat.enabled", "Enabled",
                              "Run the heartbeat tick loop. When off, no HEARTBEAT.md entries fire."))
    # Only ticks while Heartbeat is enabled.
    hb.addWidget(_dependent(
        _number_row("heartbeat.tick_seconds", "Tick Interval",
                              10, 3600, 10, 0, "s",
                              "How often the scheduler checks each agent's HEARTBEAT.md for due entries."),
        ["heartbeat.enabled"], lambda e: bool(e)))
    # Only ticks while Heartbeat is enabled.
    hb.addWidget(_dependent(
        _number_row("heartbeat.ephemeral_ttl_seconds", "Ephemeral TTL",
                              60, 86400, 60, 0, "s",
                              "Seconds after which fired ephemeral entries are auto-deleted."),
        ["heartbeat.enabled"], lambda e: bool(e)))
    # Only ticks while Heartbeat is enabled.
    hb.addWidget(_dependent(
        _number_row("heartbeat.max_concurrent_fires", "Max Concurrent",
                              1, 20, 1, 0, "",
                              "Maximum heartbeat entries allowed to fire simultaneously per tick."),
        ["heartbeat.enabled"], lambda e: bool(e)))
    # Only ticks while Heartbeat is enabled.
    hb.addWidget(_dependent(
        _number_row("heartbeat.min_fire_gap_seconds", "Min Fire Gap",
                              0, 3600, 10, 0, "s",
                              "Minimum seconds between consecutive fires of the same heartbeat entry."),
        ["heartbeat.enabled"], lambda e: bool(e)))
    layout.addWidget(hb_card)

    layout.addStretch(1)
    return scroll


def _audio(window) -> QWidget:
    """Unified STT + TTS section. Both talk to the same VoxType server
    (or any OpenAI-compatible STT/TTS endpoint) via different routes
    on a shared base URL — single port, two cards."""
    from voice.health import get_status as _voice_status

    scroll, _, layout = _page()

    # ── STT card ────────────────────────────────────────────────────
    stt_card, stt_body = _card("STT",
                                "Speech-to-text via OpenAI /v1/audio/transcriptions")
    stt_body.addWidget(_toggle_row("voice.stt.enabled", "Enabled",
                                    "Auto-transcribe voice messages."))

    stt_pill = QLabel("⚪ untested")
    stt_pill.setProperty("class", "stat_pill")
    stt_body.addWidget(_row(row_label("Health",
                                        "Reflects the outcome of the most recent transcribe request. "
                                        "No background probing — status only changes when a voice message is processed."),
                             _wrap_align(stt_pill, Qt.AlignmentFlag.AlignLeft)))

    # No endpoint to talk to while STT is off.
    stt_body.addWidget(_dependent(
        _line_row("voice.stt.base_url", "Endpoint",
                                   "http://127.0.0.1:6600/v1",
                                   "Host + port of the STT server (VoxType by default). "
                                   "VoxType picks the STT model from its own settings — "
                                   "telecode only addresses the endpoint."),
        ["voice.stt.enabled"], lambda e: bool(e)))

    # STT Test button
    from voice.stt import transcribe as _stt_transcribe, HELLO_WORLD_AUDIO
    stt_test_btn = QPushButton("Run Test")
    stt_test_btn.setFixedWidth(80)
    stt_test_btn.setProperty("class", "ghost")

    def _run_stt_test() -> None:
        stt_test_btn.setEnabled(False)
        stt_test_btn.setText("Testing...")

        async def _run() -> None:
            try:
                await _stt_transcribe(HELLO_WORLD_AUDIO, filename="test.wav", timeout=5.0)
                refresh()
            except Exception as e:
                log.warning("STT test failed: %s", e)
            finally:
                stt_test_btn.setEnabled(True)
                stt_test_btn.setText("Run Test")

        schedule(window.bot_loop, _run())

    stt_test_btn.clicked.connect(_run_stt_test)
    stt_body.addWidget(_row(row_label("Test",
                                        "Send a sample 'Hello World' audio to verify the endpoint."),
                             _wrap_align(stt_test_btn, Qt.AlignmentFlag.AlignLeft)))

    layout.addWidget(stt_card)

    # ── TTS card ────────────────────────────────────────────────────
    tts_card, tts_body = _card("TTS",
                                "Text-to-speech via OpenAI /v1/audio/speech")
    tts_body.addWidget(_toggle_row("voice.tts.enabled", "Enabled",
                                    "Allow telecode to synthesise audio via the TTS endpoint."))

    tts_pill = QLabel("⚪ untested")
    tts_pill.setProperty("class", "stat_pill")
    tts_body.addWidget(_row(row_label("Health",
                                        "Updated by real /v1/audio/speech calls. No background probing."),
                             _wrap_align(tts_pill, Qt.AlignmentFlag.AlignLeft)))

    # No endpoint to talk to while TTS is off.
    tts_body.addWidget(_dependent(
        _line_row("voice.tts.base_url", "Endpoint",
                                   "http://127.0.0.1:6600/v1",
                                   "Host + port of the TTS server (VoxType by default). "
                                   "VoxType picks the TTS model + voice from its own "
                                   "settings — telecode only addresses the endpoint."),
        ["voice.tts.enabled"], lambda e: bool(e)))

    # TTS Test button — synthesizes a short phrase and stores the WAV.
    from voice.tts import synthesize as _tts_synthesize, HELLO_WORLD_TEXT
    tts_test_btn = QPushButton("Run Test")
    tts_test_btn.setFixedWidth(80)
    tts_test_btn.setProperty("class", "ghost")

    def _run_tts_test() -> None:
        tts_test_btn.setEnabled(False)
        tts_test_btn.setText("Testing...")

        async def _run() -> None:
            try:
                await _tts_synthesize(HELLO_WORLD_TEXT, timeout=10.0)
                refresh()
            except Exception as e:
                log.warning("TTS test failed: %s", e)
            finally:
                tts_test_btn.setEnabled(True)
                tts_test_btn.setText("Run Test")

        schedule(window.bot_loop, _run())

    tts_test_btn.clicked.connect(_run_tts_test)
    tts_body.addWidget(_row(row_label("Test",
                                        "Synthesise a short phrase to verify the endpoint."),
                             _wrap_align(tts_test_btn, Qt.AlignmentFlag.AlignLeft)))

    layout.addWidget(tts_card)

    def _paint_pill(pill: QLabel, configured: bool, last_checked: bool,
                     reachable: bool) -> None:
        if not configured:
            pill.setText("⚫ disabled")
            pill.setProperty("class", "stat_pill")
        elif not last_checked:
            pill.setText("⚪ untested")
            pill.setProperty("class", "stat_pill")
        elif reachable:
            pill.setText("🟢 reachable")
            pill.setProperty("class", "stat_pill stat_pill_ok")
        else:
            pill.setText("🔴 last call failed")
            pill.setProperty("class", "stat_pill stat_pill_err")
        pill.style().unpolish(pill); pill.style().polish(pill)

    def refresh() -> None:
        vs = _voice_status()
        _paint_pill(stt_pill, vs.stt_configured, vs.stt_last_checked, vs.stt_reachable)
        _paint_pill(tts_pill, vs.tts_configured, vs.tts_last_checked, vs.tts_reachable)

    scroll.refresh = refresh  # type: ignore[attr-defined]
    refresh()

    layout.addStretch(1)
    return scroll


def _computer(window) -> QWidget:
    scroll, _, layout = _page()
    card, body = _card("Computer Control", "Vision LLM that clicks/types on any window")

    body.addWidget(_section_header("LLM Endpoint"))
    body.addWidget(_enum_row("tools.computer.api.format", "API Format",
                              [("OpenAI", "openai"),
                               ("Anthropic", "anthropic"),
                               ("Claude Code CLI", "claude-code")]))
    body.addWidget(_line_row("tools.computer.api.base_url", "Base URL",
                              "http://localhost:1235/v1",
                              "Vision-capable LLM endpoint. Local proxy: http://localhost:1235/v1"))
    body.addWidget(_password_row("tools.computer.api.api_key", "API Key",
                                  "leave empty for local",
                                  "OPENAI_API_KEY / ANTHROPIC_API_KEY / Bearer token."))
    body.addWidget(_line_row("tools.computer.api.model", "Model",
                              "qwen3.6-35b",
                              "Model name passed in the request. Vision-capable required."))

    body.addWidget(_section_header("Behavior"))
    body.addWidget(_number_row("tools.computer.capture_interval", "Capture Interval", 1, 30, 1, 0, "s"))
    body.addWidget(_number_row("tools.computer.max_history",      "Max History",      5, 100, 5, 0,
                                "", "Rolling conversation window (turns)."))

    body.addWidget(_section_header("System Prompt Override"))
    body.addWidget(_json_row("tools.computer.system_prompt", "System Prompt",
                              default="", height=140,
                              help_text="String — overrides the built-in computer-control prompt. "
                                        "Empty/null = use default. (JSON-quoted because of escaping.)"))
    layout.addWidget(card)
    layout.addStretch(1)
    return scroll


def _sessions(window) -> QWidget:
    scroll, _, layout = _page()
    card, body = _card("Active Sessions")
    table = QTableWidget(0, 5)
    table.setHorizontalHeaderLabels(["Backend", "Key", "User", "Thread", "Age"])
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    body.addWidget(table)

    actions = QWidget()
    al = QHBoxLayout(actions)
    al.setContentsMargins(0, 0, 0, 0)
    kill_sel = QPushButton("Kill Selected")
    kill_sel.setProperty("class", "danger")
    kill_all = QPushButton("Kill All")
    kill_all.setProperty("class", "danger")
    al.addStretch(1)
    al.addWidget(kill_sel)
    al.addWidget(kill_all)
    body.addWidget(actions)

    def _kill_selected():
        row = table.currentRow()
        if row < 0:
            return
        uid = int(table.item(row, 2).data(Qt.ItemDataRole.UserRole))
        key = table.item(row, 1).text()
        async def _do():
            from bot.rate import _session_mgr
            if _session_mgr is not None:
                await _session_mgr.kill_session(uid, key)
        schedule(window.bot_loop, _do())
    def _kill_all():
        async def _do():
            from bot.rate import _session_mgr
            if _session_mgr is None: return
            for uid in list(_session_mgr._sessions.keys()):
                await _session_mgr.kill_all_sessions(uid)
        schedule(window.bot_loop, _do())
    kill_sel.clicked.connect(_kill_selected)
    kill_all.clicked.connect(_kill_all)

    layout.addWidget(card)
    layout.addStretch(1)

    def refresh() -> None:
        sessions = build_status().get("sessions", [])
        table.setRowCount(len(sessions))
        for i, s in enumerate(sessions):
            vals = [
                s.get("backend", "?"),
                s.get("key", "?"),
                str(s.get("user_id", "?")),
                str(s.get("thread_id", "—")),
                f"{int(s.get('age_sec', 0) // 60)}m",
            ]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(i, j, item)
            # store user_id on the User column cell for kill-by-row
            table.item(i, 2).setData(Qt.ItemDataRole.UserRole, s.get("user_id"))
        kill_sel.setEnabled(bool(sessions))
        kill_all.setEnabled(bool(sessions))
    scroll.refresh = refresh  # type: ignore[attr-defined]
    refresh()
    return scroll


def _logs(window) -> QWidget:
    """Live-tailing log viewer with level coloring."""
    import os, re, subprocess, sys as _s
    from PySide6.QtCore import QRegularExpression
    from PySide6.QtGui import (
        QTextCharFormat, QColor, QSyntaxHighlighter, QFont, QTextCursor,
    )
    from PySide6.QtWidgets import QPlainTextEdit, QCheckBox
    from tray.qt_helpers import settings_path as _sp
    from tray.qt_theme import ACCENT, WARN, ERR, OK, FG_DIM, FG_MUTE, BG_ELEV

    LOG_FILES = [
        "telecode.log", "telecode.log.prev",
        "llama.log",    "llama.log.prev",
        "proxy.log",    "proxy.log.prev",
        "mcp.log",      "mcp.log.prev",
        "bot.log",      "bot.log.prev",
        "voice.log",    "voice.log.prev",
        "docgraph.log",        "docgraph.log.prev",
        "docgraph_index.log",  "docgraph_index.log.prev",
        "docgraph_wiki.log",   "docgraph_wiki.log.prev",
        "docgraph_watch.log",  "docgraph_watch.log.prev",
        "docgraph_serve.log",  "docgraph_serve.log.prev",
        "docgraph_daemon.log", "docgraph_daemon.log.prev",
        "cli_audit.log",
        "tray-bot.stderr.log",
    ]
    MAX_TAIL_BYTES = 512 * 1024  # last ~512 KB is plenty for UI

    def _get_log_files():
        """Get all standard log files + any in task_logs + raw PTY dumps."""
        out = list(LOG_FILES)
        try:
            task_log_dir = _sp().parent / "data" / "task_logs"
            if task_log_dir.exists():
                for f in task_log_dir.iterdir():
                    if f.is_file() and f.suffix in (".log", ".jsonl"):
                        out.append(f"task_logs/{f.name}")
        except Exception:
            pass
        # Raw PTY dumps (.txt only — .bin is binary, not viewable)
        try:
            logs_dir = _sp().parent / "data" / "logs"
            if logs_dir.exists():
                pty_dumps = sorted(
                    (f for f in logs_dir.iterdir()
                     if f.is_file() and f.name.startswith("pty_") and f.suffix == ".txt"),
                    key=lambda f: f.stat().st_mtime,
                    reverse=True,
                )
                for f in pty_dumps[:20]:  # cap at 20 most recent
                    out.append(f.name)
        except Exception:
            pass
        # Per-repo docgraph mcp child logs: docgraph_mcp_<slug>.log
        try:
            logs_dir = _sp().parent / "data" / "logs"
            if logs_dir.exists():
                for f in sorted(logs_dir.iterdir(), key=lambda f: f.name):
                    name = f.name
                    if (f.is_file() and name.startswith("docgraph_mcp_")
                            and (name.endswith(".log") or name.endswith(".log.prev"))):
                        out.append(name)
        except Exception:
            pass
        return out

    class LogHighlighter(QSyntaxHighlighter):
        """Color timestamps, levels, logger names, tracebacks, numbers."""
        def __init__(self, doc):
            super().__init__(doc)
            def fmt(color: str, bold: bool = False) -> QTextCharFormat:
                f = QTextCharFormat()
                f.setForeground(QColor(color))
                if bold:
                    f.setFontWeight(QFont.Weight.DemiBold)
                return f
            self._rules = [
                # timestamp: 2026-04-19 13:00:32,913
                (QRegularExpression(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}[,\.]?\d*"), fmt(FG_MUTE)),
                # level tokens
                (QRegularExpression(r"\b(CRITICAL|FATAL)\b"), fmt("#ff9aa2", True)),
                (QRegularExpression(r"\b(ERROR|ERR)\b"),      fmt(ERR, True)),
                (QRegularExpression(r"\b(WARN(ING)?)\b"),     fmt(WARN, True)),
                (QRegularExpression(r"\b(INFO)\b"),           fmt(ACCENT, True)),
                (QRegularExpression(r"\b(DEBUG|TRACE)\b"),    fmt(FG_DIM, True)),
                # logger name in brackets: [telecode.tray]
                (QRegularExpression(r"\[[\w\.\-]+\]"), fmt(OK)),
                # python traceback
                (QRegularExpression(r'^\s*File\s+".+?",\s+line\s+\d+.*$'), fmt("#b892ff")),
                (QRegularExpression(r"^\s*Traceback \(most recent call last\):.*$"), fmt(ERR, True)),
                (QRegularExpression(r"^\s*\w*(Error|Exception):.*$"), fmt(ERR)),
                # URLs
                (QRegularExpression(r"https?://\S+"), fmt(ACCENT)),
                # JSON Highlighting (for JSONL task logs)
                (QRegularExpression(r'"[^"\\]*(?:\\.[^"\\]*)*"\s*:'), fmt(ACCENT, True)),   # keys
                (QRegularExpression(r':\s*"[^"\\]*(?:\\.[^"\\]*)*"'), fmt(OK)),              # string vals
                (QRegularExpression(r'\b(true|false|null)\b'),        fmt(WARN, True)),      # keywords
                # numbers (soft)
                (QRegularExpression(r"\b\d+(\.\d+)?\b"), fmt("#a8b3c7")),
            ]

        def highlightBlock(self, text: str) -> None:
            for rx, f in self._rules:
                it = rx.globalMatch(text)
                while it.hasNext():
                    m = it.next()
                    self.setFormat(m.capturedStart(), m.capturedLength(), f)

    scroll, _, layout = _page()
    card, body = _card("Logs", "Live-tailing viewer · auto-refreshes")

    # ── Top bar: file picker + actions ───────────────────────────────
    top = QHBoxLayout()
    top.setSpacing(8)

    picker = QComboBox()
    def _refresh_picker():
        cur = picker.currentText()
        picker.blockSignals(True)
        picker.clear()
        files = _get_log_files()
        for n in files:
            picker.addItem(n)
        if cur in files:
            picker.setCurrentText(cur)
        picker.blockSignals(False)

    _refresh_picker()
    picker.setMinimumWidth(240)

    size_label = QLabel("—")
    size_label.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")

    refresh_picker_btn = QPushButton("↻")
    refresh_picker_btn.setToolTip("Refresh file list")
    refresh_picker_btn.setProperty("class", "ghost icon")
    refresh_picker_btn.setFixedWidth(30)
    refresh_picker_btn.clicked.connect(_refresh_picker)

    follow_cb = Toggle()
    follow_cb.setChecked(True)
    follow_lbl = QLabel("Follow")
    follow_lbl.setProperty("class", "toggle_label")

    clear_btn = QPushButton("Clear View")
    clear_btn.setProperty("class", "ghost")
    open_btn = QPushButton("Open Externally")
    open_btn.setProperty("class", "ghost")
    reveal_btn = QPushButton("Reveal Folder")
    reveal_btn.setProperty("class", "ghost")

    top.addWidget(picker)
    top.addWidget(refresh_picker_btn)
    top.addWidget(size_label)
    top.addStretch(1)
    top.addWidget(follow_lbl)
    top.addWidget(follow_cb)
    top.addSpacing(8)
    top.addWidget(clear_btn)
    top.addWidget(open_btn)
    top.addWidget(reveal_btn)
    body.addLayout(top)

    # ── Viewer ───────────────────────────────────────────────────────
    viewer = QPlainTextEdit()
    viewer.setReadOnly(True)
    viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    viewer.setStyleSheet(
        f"QPlainTextEdit {{ background: {BG_ELEV}; border: 1px solid {BORDER};"
        f" border-radius: 6px; font-family: 'JetBrains Mono', Consolas, monospace;"
        f" font-size: 11.5px; padding: 6px 8px; selection-background-color: {ACCENT};"
        f" selection-color: #000; }}"
    )
    viewer.setMinimumHeight(480)
    highlighter = LogHighlighter(viewer.document())
    body.addWidget(viewer, 1)

    # ── State + helpers ──────────────────────────────────────────────
    state: dict[str, Any] = {"path": None, "pos": 0, "size": 0}

    def _log_path(name: str):
        if name.startswith("task_logs/"):
            return _sp().parent / "data" / "task_logs" / name[10:]
        return _sp().parent / "data" / "logs" / name

    def _human_bytes(n: int) -> str:
        size: float = float(n)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def _pretty_json(text: str) -> str:
        """Best-effort line-by-line JSON pretty print (for JSONL files)."""
        import json
        lines = text.splitlines()
        out = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                    out.append(json.dumps(data, indent=2, ensure_ascii=False))
                except Exception:
                    out.append(line)
            else:
                out.append(line)
        return "\n".join(out)

    def _load_initial(path):
        viewer.clear()
        if not path.exists():
            viewer.setPlainText(f"[file not found: {path}]")
            state["pos"] = 0
            state["size"] = 0
            size_label.setText("—")
            return
        size = path.stat().st_size
        state["size"] = size
        start = max(0, size - MAX_TAIL_BYTES)
        try:
            with open(path, "rb") as f:
                f.seek(start)
                if start > 0:
                    f.readline()  # drop partial line
                data = f.read()
                state["pos"] = f.tell()
            text = data.decode("utf-8", errors="replace")
            if path.suffix in (".json", ".jsonl"):
                text = _pretty_json(text)
            if start > 0:
                text = f"… (showing last {_human_bytes(len(data))} of {_human_bytes(size)}) …\n" + text
            viewer.setPlainText(text)
            if follow_cb.isChecked():
                viewer.moveCursor(QTextCursor.MoveOperation.End)
            size_label.setText(_human_bytes(size))
        except Exception as e:
            viewer.setPlainText(f"[error reading {path}: {e}]")

    def _tail():
        path = state.get("path")
        if path is None or not path.exists():
            return
        try:
            size = path.stat().st_size
        except OSError:
            return
        # rotation/truncation: reload from scratch
        if size < state["pos"]:
            _load_initial(path)
            return
        if size == state["pos"]:
            return
        try:
            with open(path, "rb") as f:
                f.seek(state["pos"])
                data = f.read()
                state["pos"] = f.tell()
                state["size"] = size
        except Exception:
            return
        if not data:
            return
        text = data.decode("utf-8", errors="replace")
        if path.suffix in (".json", ".jsonl"):
            text = _pretty_json(text)
        # preserve scroll unless follow is on
        at_bottom = follow_cb.isChecked() or (
            viewer.verticalScrollBar().value() >= viewer.verticalScrollBar().maximum() - 2
        )
        cursor = viewer.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not viewer.toPlainText().endswith("\n") and text:
             cursor.insertText("\n")
        cursor.insertText(text)
        size_label.setText(_human_bytes(size))
        if at_bottom:
            viewer.moveCursor(QTextCursor.MoveOperation.End)

    def _on_pick(idx: int):
        name = picker.itemText(idx)
        state["path"] = _log_path(name)
        state["pos"] = 0
        _load_initial(state["path"])

    def _open_external():
        p = state.get("path")
        if not p:
            return
        try:
            if _s.platform == "win32":
                os.startfile(str(p))
            elif _s.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception:
            pass

    def _reveal():
        p = state.get("path")
        if not p:
            return
        folder = p.parent
        try:
            if _s.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(p)]) if p.exists() else os.startfile(str(folder))
            elif _s.platform == "darwin":
                subprocess.Popen(["open", "-R", str(p)]) if p.exists() else subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception:
            pass

    picker.currentIndexChanged.connect(_on_pick)
    clear_btn.clicked.connect(viewer.clear)
    open_btn.clicked.connect(_open_external)
    reveal_btn.clicked.connect(_reveal)

    # Initial load
    _on_pick(0)

    # Tail timer — owned by the page widget so it stops when the page is destroyed
    tail_timer = QTimer(scroll)
    tail_timer.setInterval(1000)
    tail_timer.timeout.connect(_tail)
    tail_timer.start()

    layout.addWidget(card)
    return scroll


# ══════════════════════════════════════════════════════════════════════
# Models (llamacpp.models.*) — add/remove + full field editor
# ══════════════════════════════════════════════════════════════════════

# NOTE: Server-wide flags (threads, batch_size, ubatch_size, parallel, mlock,
# no_mmap, etc.) live ONLY at top-level `llamacpp.*`. They are intentionally
# absent here — adding them to a per-model block would have no effect because
# argv.build_argv() reads them from the top-level config, not from this dict.
_MODEL_DEFAULTS: dict[str, Any] = {
    "path": "",
    "mmproj": "",
    "ctx_size": 4096,
    # 99 = "all layers on GPU"; llama-server caps to the model's actual layer
    # count. Matches settings.example.json's recommended baseline. Users with
    # tight VRAM can lower this or flip fit on (which suppresses --n-gpu-layers
    # entirely so the auto-fitter can pick).
    "n_gpu_layers": 99,
    "flash_attn": "auto",
    "cache_type_k": "f16",
    "cache_type_v": "f16",
    "n_cpu_moe": 0,
    "jinja": True,
    "fit": False,
    "fit_ctx": 0,
    "fit_target": 0,
    "inference_defaults": {
        "temperature": 0.7,
        "top_p": 0.95,
        "top_k": 40,
        "min_p": 0.0,
        "presence_penalty": 0.0,
        "repeat_penalty": 1.0,
        "frequency_penalty": 0.0,
        "max_tokens": -1,
        "stop": [],
        "reasoning": {
            "enabled": False,
            "start": "<think>",
            "end": "</think>",
            "emit_thinking_blocks": False,
        },
    },
}

_CACHE_TYPES = [
    ("f32", "f32"), ("f16", "f16"), ("bf16", "bf16"),
    ("q8_0", "q8_0"), ("q5_1", "q5_1"), ("q5_0", "q5_0"),
    ("q4_1", "q4_1"), ("q4_0", "q4_0"), ("iq4_nl", "iq4_nl"),
]


def _line_row(path: str, label: str, placeholder: str = "",
              help_text: str = "", cli: str = "") -> QWidget:
    """Free-text string row."""
    le = QLineEdit()
    le.setPlaceholderText(placeholder)
    le.setText(str(get_path(read_settings(), path, "") or ""))
    le.editingFinished.connect(lambda: patch_settings(path, le.text()))
    # Cap so the input doesn't stretch across a wide settings window,
    # but stay generous enough that values like model names or URL
    # endpoints aren't visibly pinched. 720 matches the natural reading
    # width of a wide value (model paths, full URLs) without making
    # short values like "127.0.0.1" sit in a sparse row.
    le.setMaximumWidth(720)
    return _row(row_label(label, help_text, path, cli), _wrap_align(le, Qt.AlignmentFlag.AlignLeft))


def _code_row(path: str, label: str, placeholder: str = "",
              help_text: str = "", *, height: int = 160,
              highlighter: type | None = None) -> QWidget:
    """Multi-line free-text editor with monospace font + optional highlighter."""
    from PySide6.QtWidgets import QPlainTextEdit
    from PySide6.QtGui import QFontDatabase
    te = QPlainTextEdit()
    te.setFixedHeight(height)
    mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    mono.setPointSize(10)
    te.setFont(mono)
    te.setPlaceholderText(placeholder)
    te.setPlainText(str(get_path(read_settings(), path, "") or ""))
    if highlighter is not None:
        highlighter(te.document())

    def _commit():
        patch_settings(path, te.toPlainText())
    _debounced_commit(te, _commit, delay_ms=600)
    return _row(row_label(label, help_text, path), te)


def _make_rule_highlighter(rules: list[tuple[str, str, bool]]) -> type:
    """Build a QSyntaxHighlighter subclass from (regex, color, bold) tuples.

    Later rules win on overlap (last setFormat call overrides)."""
    from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont
    from PySide6.QtCore import QRegularExpression as QRE

    compiled = []
    for pattern, color, bold in rules:
        f = QTextCharFormat()
        f.setForeground(QColor(color))
        if bold:
            f.setFontWeight(QFont.Weight.Bold)
        compiled.append((QRE(pattern), f))

    class _RuleBased(QSyntaxHighlighter):
        def highlightBlock(self, text):
            for pattern, fmt in compiled:
                it = pattern.globalMatch(text)
                while it.hasNext():
                    m = it.next()
                    self.setFormat(m.capturedStart(), m.capturedLength(), fmt)

    return _RuleBased


# GBNF: comments → strings/char-classes → operators → rule heads
_GbnfHighlighter = _make_rule_highlighter([
    (r'"(?:[^"\\]|\\.)*"',                "#a3e635", False),  # terminal strings
    (r"\[(?:[^\]\\]|\\.)*\]",            "#f0abfc", False),  # char classes
    (r"[*+?|()]",                         "#fb923c", True),   # quantifiers / alt
    (r"::=",                              "#fb923c", True),   # rule operator
    (r"^\s*[A-Za-z_][A-Za-z0-9_-]*(?=\s*::=)",
                                          "#7dd3fc", True),   # rule head
    (r"#[^\n]*",                          "#64748b", False),  # comments
])

# Jinja: comments → strings → keywords → tag delimiters (last so they win)
_JinjaHighlighter = _make_rule_highlighter([
    (r"'(?:[^'\\]|\\.)*'",                "#a3e635", False),
    (r'"(?:[^"\\]|\\.)*"',                "#a3e635", False),
    (r"\b(?:if|elif|else|endif|for|endfor|in|not|and|or|is|set|"
     r"endset|block|endblock|extends|include|macro|endmacro|with|"
     r"endwith|true|false|none|loop|self)\b",
                                          "#fb923c", True),
    (r"\{#.*?#\}",                        "#64748b", False),
    (r"\{%-?|-?%\}|\{\{-?|-?\}\}",       "#7dd3fc", True),
])


def _enum_row_strs(path: str, label: str, options: list[tuple[str, str]],
                   help_text: str = "") -> QWidget:
    return _enum_row(path, label, [(d, v) for d, v in options], help_text)


def _models(window) -> QWidget:
    from PySide6.QtWidgets import QStackedWidget, QInputDialog, QMessageBox

    scroll, content, layout = _page()
    card, body = _card("Models", "llamacpp.models.* — registered model registry")

    # ── Picker row ───────────────────────────────────────────────────
    top = QHBoxLayout(); top.setSpacing(8)
    picker = QComboBox(); picker.setMinimumWidth(240)
    add_btn = QPushButton("+ Add")
    add_btn.setProperty("class", "primary")
    rename_btn = QPushButton("Rename")
    remove_btn = QPushButton("Remove")
    remove_btn.setProperty("class", "danger")
    set_default_btn = QPushButton("Set As Default")
    set_default_btn.setProperty("class", "ghost")
    default_lbl = QLabel(""); default_lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")
    top.addWidget(picker); top.addWidget(default_lbl); top.addStretch(1)
    top.addWidget(set_default_btn); top.addWidget(rename_btn); top.addWidget(add_btn); top.addWidget(remove_btn)
    body.addLayout(top)

    # ── Form container ──────────────────────────────────────────────
    # Sits at PAGE level, not inside the Models card, so the per-section cards
    # built by _build_form are top-level siblings exactly like the llama.cpp
    # page's Server / Spawn / Caching cards — rather than cards nested inside
    # another card. Margins zero and spacing 18 to match _page()'s own layout.
    layout.addWidget(card)
    form_host = QWidget()
    form_layout = QVBoxLayout(form_host)
    form_layout.setContentsMargins(0, 0, 0, 0)
    form_layout.setSpacing(18)
    layout.addWidget(form_host)
    layout.addStretch(1)

    def _clear_form():
        # Flush pending debounced edits so typing isn't lost on picker change
        _flush_pending(form_host)
        while form_layout.count():
            item = form_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _build_form(key: str):
        _clear_form()
        # Escape dots in the model name so get_path/patch_settings doesn't split it
        ek = key.replace(".", r"\.")
        p = f"llamacpp.models.{ek}"

        # Each _section_header used to be a bare label in one long flat form.
        # They are cards now, matching the llama.cpp page: _sec() opens one and
        # returns its body layout, which _sl then points at, so every row after
        # it lands inside that card without touching the ~90 addWidget calls.
        def _sec(title: str, sub: str = ""):
            _c, _b = _card(title, sub)
            form_layout.addWidget(_c)
            return _b

        _sl = form_layout   # anything before the first card (there is none today)
        _sl = _sec("Paths",
                    "llamacpp.models.<m>.path — GGUF file on disk")
        _sl.addWidget(_line_row(f"{p}.path",   "GGUF Path",
                                         "D:/models/foo.gguf",
                                         "Absolute path to the model .gguf file."))
        _sl = _sec("Vision",
                    "llamacpp.models.<m>.mmproj — multimodal projector; leave empty for text-only models")
        _sl.addWidget(_line_row(f"{p}.mmproj", "mmproj Path",
                                         "D:/models/mmproj.gguf",
                                         "Optional — only needed for vision-capable GGUFs (Qwen-VL etc)."))
        _has_mmproj = lambda v: bool(str(v or "").strip())
        _sl.addWidget(_dependent(
            _toggle_row(f"{p}.mmproj_offload",  "mmproj on GPU",
                         "--mmproj-offload / --no-mmproj-offload: keep the vision projector in VRAM (default enabled). "
                         "Disable to save ~1 GiB VRAM at the cost of slower per-image prefill."),
            [f"{p}.mmproj"], _has_mmproj))
        _sl.addWidget(_dependent(
            _number_row(f"{p}.image_min_tokens", "Image Min Tokens",  0, 16384, 64, 0, "tok",
                         "--image-min-tokens: minimum tokens each image consumes (dynamic-resolution vision models). 0 = read from model."),
            [f"{p}.mmproj"], _has_mmproj))
        _sl.addWidget(_dependent(
            _number_row(f"{p}.image_max_tokens", "Image Max Tokens",  0, 16384, 64, 0, "tok",
                         "--image-max-tokens: maximum tokens per image. 0 = read from model. Lower = less prefill compute."),
            [f"{p}.mmproj"], _has_mmproj))

        _sl = _sec("Capacity",
                    "llamacpp.models.<m>.* — context window and layer placement")
        _sl.addWidget(_number_row(f"{p}.ctx_size",     "Context Size",       512, 1048576, 256, 0, "tok"))
        # n_gpu_layers is greyed when fit is on — the auto-fitter aborts if it
        # sees a user-pinned ngl, so argv.build_argv() suppresses --n-gpu-layers
        # in that case. This dependency mirrors that suppression in the UI.
        _fit_off = lambda f: not bool(f)
        _sl.addWidget(_dependent(
            _number_row(f"{p}.n_gpu_layers", "GPU Layers",         0,   200,     1,   0, "",
                                               "Layers offloaded to GPU. Higher = faster, more VRAM."),
            [f"{p}.fit"], _fit_off))
        _sl.addWidget(_number_row(f"{p}.n_cpu_moe",    "CPU MoE Layers",     0,   200,     1,   0, "",
                                           "MoE experts kept on CPU. 0 = all on GPU."))
        _sl.addWidget(_line_row(f"{p}.device",         "Devices",
                                         "e.g. CUDA0,CUDA1 / Vulkan0 / none",
                                         "--device: explicit comma-separated device list. Empty = let llama-server pick (split-mode applies)."))

        _sl = _sec("Context Fitting",
                    "--fit — auto-shrink ctx_size to what the GPU can hold")
        _sl.addWidget(_toggle_row(f"{p}.fit",          "Fit Context",
                                           "--fit on: auto-shrink ctx_size to what the model + KV actually fits in available memory."))
        _fit_on = lambda f: bool(f)
        _sl.addWidget(_dependent(
            _number_row(f"{p}.fit_ctx",      "Fit Ctx Ceiling",    0,   2097152, 1024, 0, "tok",
                         "--fit-ctx: max ctx the fitter is allowed to grow to. 0 = use ctx_size."),
            [f"{p}.fit"], _fit_on))
        _sl.addWidget(_dependent(
            _number_row(f"{p}.fit_target",   "Fit Target Headroom", 0,  16384,   16,   0, "MB",
                         "--fit-target: free VRAM/RAM (MB) to leave after fitting."),
            [f"{p}.fit"], _fit_on))

        _sl = _sec("Cache",
                    "--cache-type-* — KV cache quantisation and reuse")
        _sl.addWidget(_enum_row_strs(f"{p}.cache_type_k", "Cache Type (K)", _CACHE_TYPES))
        _sl.addWidget(_enum_row_strs(f"{p}.cache_type_v", "Cache Type (V)", _CACHE_TYPES))
        _sl.addWidget(_number_row(f"{p}.cache_reuse",     "Cache Reuse",          0,   8192, 32, 0, "tok",
                                           "--cache-reuse: tokens to retain when reusing an existing slot."))

        _sl = _sec("Flags",
                    "llamacpp.models.<m>.* — per-model llama-server switches")
        _sl.addWidget(_toggle_row(f"{p}.preload",       "Preload",
                                           "Load this model at telecode startup regardless of auto_start."))
        _sl.addWidget(_enum_row_strs(f"{p}.flash_attn", "Flash Attention",
                                              [("Auto (default)", "auto"),
                                               ("On", "on"),
                                               ("Off", "off")],
                                              "--flash-attn: set Flash Attention use ('on', 'off', or 'auto')."))
        _sl.addWidget(_toggle_row(f"{p}.cpu_moe",       "CPU MoE (all experts)",
                                           "--cpu-moe: keep ALL MoE expert layers on CPU (overrides n_cpu_moe)."))
        _sl.addWidget(_toggle_row(f"{p}.jinja",         "Jinja Chat Template",
                                           "Use the built-in tokenizer chat template (required for tools)."))
        _sl.addWidget(_code_row(f"{p}.chat_template",   "Chat Template Override",
                                         "(empty = use model's built-in)",
                                         "--chat-template: override the GGUF's chat template by name "
                                         "or paste an inline jinja template.",
                                         height=200, highlighter=_JinjaHighlighter))
        _sl.addWidget(_line_row(f"{p}.chat_template_file", "Chat Template File",
                                         "/path/to/template.jinja",
                                         "--chat-template-file: load the jinja template from a file (alternative to the inline override above)."))

        _sl = _sec("RoPE",
                    "--rope-* — position-embedding scaling to stretch context past training length")
        _sl.addWidget(_enum_row(
            f"{p}.rope_scaling", "RoPE Scaling",
            [("Model default", ""), ("none", "none"), ("linear", "linear"), ("yarn", "yarn")],
            "--rope-scaling. Empty = model default.",
            max_width=360))
        _has_rope_scaling = lambda rs: bool(str(rs or "").strip()) and str(rs).strip().lower() != "none"
        _yarn_active = lambda rs: str(rs or "").strip().lower() == "yarn"
        _sl.addWidget(_dependent(
            _number_row(f"{p}.rope_freq_base", "RoPE Freq Base",       0, 10000000, 1000, 0, "",
                         "--rope-freq-base. 0 = model default."),
            [f"{p}.rope_scaling"], _has_rope_scaling))
        _sl.addWidget(_dependent(
            _number_row(f"{p}.rope_freq_scale","RoPE Freq Scale",      0, 4.0, 0.05, 2, "",
                         "--rope-freq-scale. 0 = model default."),
            [f"{p}.rope_scaling"], _has_rope_scaling))

        _sl = _sec("YaRN",
                    "--yarn-* — only applied when RoPE Scaling is set to yarn")
        _sl.addWidget(_dependent(
            _number_row(f"{p}.yarn_orig_ctx",  "YaRN Orig Ctx",        0, 1048576, 1024, 0, "tok",
                         "--yarn-orig-ctx: original training context for YaRN scaling."),
            [f"{p}.rope_scaling"], _yarn_active))
        # YaRN fine-tuning — defaults are -1.0 (auto) upstream; 0 here means
        # "skip flag emission, use server default".
        _sl.addWidget(_dependent(
            _number_row(f"{p}.yarn_ext_factor",  "YaRN Ext Factor",  -1.0, 4.0, 0.05, 2, "",
                         "--yarn-ext-factor: extrapolation mix factor. 0 = use server default (auto)."),
            [f"{p}.rope_scaling"], _yarn_active))
        _sl.addWidget(_dependent(
            _number_row(f"{p}.yarn_attn_factor", "YaRN Attn Factor", -1.0, 4.0, 0.05, 2, "",
                         "--yarn-attn-factor: scale sqrt(t) or attention magnitude. 0 = default."),
            [f"{p}.rope_scaling"], _yarn_active))
        _sl.addWidget(_dependent(
            _number_row(f"{p}.yarn_beta_slow",   "YaRN Beta Slow",   -1.0, 4.0, 0.05, 2, "",
                         "--yarn-beta-slow: high correction dim (alpha). 0 = default."),
            [f"{p}.rope_scaling"], _yarn_active))
        _sl.addWidget(_dependent(
            _number_row(f"{p}.yarn_beta_fast",   "YaRN Beta Fast",   -1.0, 64.0, 0.5, 2, "",
                         "--yarn-beta-fast: low correction dim (beta). 0 = default."),
            [f"{p}.rope_scaling"], _yarn_active))

        _sl = _sec("Draft Model (Speculative)",
                    "--spec-* / --draft-* — a small draft model proposes tokens the main model verifies")
        _sl.addWidget(_line_row(f"{p}.draft_model", "Draft Model (GGUF)",
                                         "D:/models/draft-0.6b.gguf",
                                         "--model-draft: separate small LM for draft tokens. "
                                         "Leave empty + Spec Type=ngram-simple for prompt-lookup self-speculation, "
                                         "or Spec Type=draft-mtp to use the main model's MTP heads."))
        # Draft knobs are active when either: a draft GGUF is set, OR a draft-*
        # spec strategy is active (draft-mtp uses model's own heads, no draft GGUF).
        _draft_knobs_active = lambda dm, st: bool(str(dm or "").strip()) or _has_spec(st, "draft-simple", "draft-eagle3", "draft-mtp")
        _draft_paths = [f"{p}.draft_model", "llamacpp.spec_type"]
        _sl.addWidget(_dependent(
            _number_row(f"{p}.n_gpu_layers_draft", "Draft GPU Layers", 0, 200, 1, 0, "",
                         "--n-gpu-layers-draft / -ngld: layers of the draft model on GPU."),
            _draft_paths, _draft_knobs_active))
        _sl.addWidget(_dependent(
            _enum_row_strs(f"{p}.cache_type_k_draft", "Draft Cache (K)", [("(default)", "")] + _CACHE_TYPES,
                            "--cache-type-k-draft: K-cache dtype for the draft model."),
            _draft_paths, _draft_knobs_active))
        _sl.addWidget(_dependent(
            _enum_row_strs(f"{p}.cache_type_v_draft", "Draft Cache (V)", [("(default)", "")] + _CACHE_TYPES,
                            "--cache-type-v-draft: V-cache dtype for the draft model."),
            _draft_paths, _draft_knobs_active))
        _sl.addWidget(_dependent(
            _line_row(f"{p}.device_draft",         "Draft Devices",
                       "e.g. CUDA0,CUDA1",
                       "--device-draft / -devd: comma-separated device list for draft offload."),
            _draft_paths, _draft_knobs_active))
        _sl.addWidget(_dependent(
            _toggle_row(f"{p}.cpu_moe_draft",      "Draft CPU MoE (all)",
                         "--cpu-moe-draft: keep ALL MoE expert layers of the draft model on CPU (mirrors --cpu-moe)."),
            _draft_paths, _draft_knobs_active))
        _sl.addWidget(_dependent(
            _number_row(f"{p}.n_cpu_moe_draft",    "Draft CPU MoE Layers", 0, 200, 1, 0, "",
                         "--n-cpu-moe-draft: first N MoE layers of the draft model on CPU. 0 = all on GPU."),
            _draft_paths, _draft_knobs_active))
        _sl.addWidget(_dependent(
            _number_row(f"{p}.draft_n",     "Draft Max Tokens",  0, 32,   1,    0, "",
                         "--spec-draft-n-max: max draft tokens per step. v9243 default 3 (was 16)."),
            _draft_paths, _draft_knobs_active))
        _sl.addWidget(_dependent(
            _number_row(f"{p}.draft_n_min", "Draft Min Tokens",  0, 32,   1,    0, "",
                         "--spec-draft-n-min: minimum draft length before accepting. Typical: 0–2."),
            _draft_paths, _draft_knobs_active))
        _sl.addWidget(_dependent(
            _number_row(f"{p}.draft_p_min", "Draft Min Probability", 0.0, 1.0, 0.05, 2, "",
                         "--spec-draft-p-min: reject draft tokens below this probability. "
                         "v9243 default 0.00 (was 0.75). Draft-model: 0.5–0.75. N-gram: 0.1."),
            _draft_paths, _draft_knobs_active))
        _sl.addWidget(_dependent(
            _number_row(f"{p}.draft_p_split", "Draft P-Split", 0.0, 1.0, 0.05, 2, "",
                         "--spec-draft-p-split: speculative decoding split probability (default 0.10)."),
            _draft_paths, _draft_knobs_active))
        _sl.addWidget(_dependent(
            _line_row(f"{p}.spec_draft_override_tensor", "Draft Tensor Override",
                       "blk\\.[0-9]+\\.ffn_.*=CPU",
                       "--spec-draft-override-tensor: per-tensor buffer override for the draft model. Pattern=buffer (regex)."),
            _draft_paths, _draft_knobs_active))
        # Lookup caches only matter for spec_type=ngram-cache.
        _ngram_cache_active = lambda st: _has_spec(st, "ngram-cache")
        _sl.addWidget(_dependent(
            _line_row(f"{p}.lookup_cache_static", "Lookup Cache (static)",
                       "./data/lookup-static.bin",
                       "--lookup-cache-static. Only used when Spec Type = ngram-cache. "
                       "Precomputed via llama-lookup-create; read-only at runtime."),
            ["llamacpp.spec_type"], _ngram_cache_active))
        _sl.addWidget(_dependent(
            _line_row(f"{p}.lookup_cache_dynamic", "Lookup Cache (dynamic)",
                       "./data/lookup-dyn.bin",
                       "--lookup-cache-dynamic. Only loaded when Spec Type = ngram-cache. "
                       "NOTE: llama-server does not persist writes — file will not be created or updated."),
            ["llamacpp.spec_type"], _ngram_cache_active))

        # Per-model Inference Defaults — proxy-applied per-request body fields.
        # Override hierarchy: request body > this > top-level llamacpp.inference.
        _sl = _sec("Inference Defaults",
                    "inference_defaults.* — applied by the proxy to every request body; request values win")
        ip = f"{p}.inference_defaults"
        _sl.addWidget(_number_row(f"{ip}.temperature",       "Temperature",       0.0, 1.5, 0.05, 2))
        _sl.addWidget(_number_row(f"{ip}.top_p",             "Top-P",             0.0, 1.0, 0.01, 2))
        _sl.addWidget(_number_row(f"{ip}.top_k",             "Top-K",             0,   200, 1,    0))
        _sl.addWidget(_number_row(f"{ip}.min_p",             "Min-P",             0.0, 1.0, 0.01, 2))
        _sl.addWidget(_number_row(f"{ip}.presence_penalty",  "Presence Penalty",  0.0, 2.0, 0.05, 2))
        _sl.addWidget(_number_row(f"{ip}.repeat_penalty",    "Repeat Penalty",    0.5, 2.0, 0.01, 2))
        _sl.addWidget(_number_row(f"{ip}.frequency_penalty", "Frequency Penalty", 0.0, 2.0, 0.05, 2))
        _sl.addWidget(_number_row(f"{ip}.max_tokens",        "Max Tokens",       -1,   1048576, 64, 0, "tok",
                                           "Hard cap on generated tokens. -1 = unlimited / model-default."))
        _sl.addWidget(_list_row(f"{ip}.stop", "Stop Strings",
                                         "Generation halts when any of these appears (one per line).",
                                         "</s>"))

        _sl = _sec("Reasoning Parser",
                    "inference_defaults.reasoning.* — how the proxy detects and surfaces think blocks; does not change what the model generates")
        rp = f"{ip}.reasoning"
        _sl.addWidget(_toggle_row(f"{rp}.enabled",              "Parse <think> Blocks"))
        _think_enabled = lambda en: bool(en)
        _sl.addWidget(_dependent(
            _line_row(f"{rp}.start",                  "Start Tag", "<think>"),
            [f"{rp}.enabled"], _think_enabled))
        _sl.addWidget(_dependent(
            _line_row(f"{rp}.end",                    "End Tag",   "</think>"),
            [f"{rp}.enabled"], _think_enabled))
        _sl.addWidget(_dependent(
            _toggle_row(f"{rp}.emit_thinking_blocks", "Emit Thinking Blocks"),
            [f"{rp}.enabled"], _think_enabled))

        _sl = _sec("Thinking",
                    "inference_defaults.thinking.* — the model's own on/off switch; empty key leaves it to the template")
        tkp = f"{ip}.thinking"
        _sl.addWidget(_line_row(f"{tkp}.template_key", "Template Key", "",
            "Which chat-template variable carries the on/off switch. Qwen 3.x / 3.8: "
            "enable_thinking. LEAVE EMPTY to send nothing and let the template decide "
            "- the toggle below only takes effect once a key is set. Models whose lever "
            "is an effort string rather than a boolean are handled by the Reasoning "
            "Effort map below."))
        _sl.addWidget(_dependent(
            _toggle_row(f"{tkp}.enabled", "Thinking",
                "On sends <key>=true, off sends <key>=false. Sets the model own "
                "chat-template switch, so off actually stops reasoning being generated "
                "- unlike Parse <think> Blocks above, which only controls whether the "
                "proxy surfaces thinking while the model still generates it and still "
                "pays the context."),
            [f"{tkp}.template_key"], lambda k: bool(str(k or "").strip())))

        _sl = _sec("Reasoning Effort",
                    "inference_defaults.reasoning_effort.* — Claude Code effort level → this model's template vocabulary")
        rep = f"{ip}.reasoning_effort"
        _sl.addWidget(_line_row(f"{rep}.template_key", "Template Key", "reasoning_effort",
            "Which chat-template variable carries the effort string. Qwen 3.x / "
            "GPT-OSS both call it `reasoning_effort`."))
        # Dynamic list, same affordances as the token budget card on the
        # llama.cpp page: framed row per entry, inline value, Remove, and an
        # Add bar for custom effort keys. Standard Claude Code keys are seeded
        # by config._ensure_model_effort_maps and cannot be deleted or renamed;
        # anything else is a custom key the user added.
        from config import STANDARD_EFFORT_KEYS as _STD_EFFORT_KEYS_M

        _eff_rows_host = QWidget()
        _eff_rows_layout = QVBoxLayout(_eff_rows_host)
        _eff_rows_layout.setContentsMargins(0, 0, 0, 0)
        _eff_rows_layout.setSpacing(8)
        # An empty Template Key disables the whole mapping in the translator
        # (proxy/translate.py::_apply_reasoning_effort_template), so grey the
        # rows out with it rather than let them look live. Qwen 3.6, for one,
        # has no reasoning_effort variable at all — clearing the key is how you
        # switch this off for such a model.
        _eff_key_set = lambda k: bool(str(k or "").strip())
        _sl.addWidget(_dependent(_eff_rows_host, [f"{rep}.template_key"], _eff_key_set))

        _eff_add_bar = QWidget()
        _eab = QHBoxLayout(_eff_add_bar)
        _eab.setContentsMargins(0, 4, 0, 0)
        _eab.setSpacing(8)
        _eff_add_key = QLineEdit()
        _eff_add_key.setPlaceholderText("custom effort key (e.g. ultra)")
        _eff_add_key.setMinimumWidth(300)
        _eff_add_btn = QPushButton("+ Add")
        _eff_add_btn.setProperty("class", "primary")
        _eff_add_btn.setMaximumWidth(80)
        _eab.addWidget(_eff_add_key, 1)
        _eab.addWidget(_eff_add_btn)
        _eab.addStretch(1)
        _sl.addWidget(_dependent(_eff_add_bar, [f"{rep}.template_key"], _eff_key_set))

        def _build_model_effort_row(ekey: str, value) -> QWidget:
            path = f"{rep}.map.{ekey}"
            is_std = ekey.lower() in _STD_EFFORT_KEYS_M
            row = QFrame()
            row.setStyleSheet(
                f"QFrame {{ background: {BG_ELEV}; border: 1px solid {BORDER};"
                f" border-radius: 6px; }}"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 6, 10, 6)
            rl.setSpacing(10)

            if is_std:
                head = QLabel(
                    f"<b style='color:{FG}'>{ekey}</b>"
                    f" <span style='color:{FG_MUTE}; font-weight:normal;"
                    f" font-size:10.5px;'>&#183; claude code</span>"
                )
                head.setTextFormat(Qt.TextFormat.RichText)
                head.setMinimumWidth(140)
            else:
                head = QLineEdit(ekey)
                head.setMinimumWidth(140)
                head.setMaximumWidth(180)
                head.setStyleSheet(
                    f"QLineEdit {{ background: transparent;"
                    f" border: 1px solid transparent; color: {FG};"
                    f" font-weight: bold; padding: 2px 4px; }}"
                    f" QLineEdit:focus {{ border: 1px solid {BORDER};"
                    f" background: {BG_ELEV}; }}"
                )
                head.setToolTip("Custom effort key - edit to rename.")

                def _rename(old=ekey, e=head):
                    new_k = e.text().strip()
                    if not new_k or new_k == old:
                        e.setText(old)
                        return
                    if new_k.lower() in _STD_EFFORT_KEYS_M:
                        QMessageBox.warning(content, "Reserved", f"{new_k} is a standard key.")
                        e.setText(old)
                        return
                    cur = get_path(read_settings(), f"{rep}.map", {}) or {}
                    if new_k in cur:
                        QMessageBox.warning(content, "Exists", f"{new_k} already exists.")
                        e.setText(old)
                        return
                    val = cur.get(old, "")
                    remove_path(f"{rep}.map.{old}")
                    patch_settings(f"{rep}.map.{new_k}", val)
                    _refresh_model_effort_map()
                head.editingFinished.connect(_rename)
            rl.addWidget(head)

            arrow = QLabel("→")
            arrow.setStyleSheet(f"color: {FG_DIM};")
            rl.addWidget(arrow)

            ed = QLineEdit()
            ed.setPlaceholderText("(empty - emits nothing for this level)")
            ed.setText("" if value is None else str(value))
            ed.setToolTip(f"Template value this model expects when the client asks for effort {ekey}.")

            def _commit(e=ed, pth=path):
                patch_settings(pth, e.text().strip())
            ed.editingFinished.connect(_commit)
            rl.addWidget(ed, 1)

            rm = QPushButton("Remove")
            rm.setProperty("class", "danger")
            rm.setMaximumWidth(90)
            if is_std:
                rm.setEnabled(False)
                rm.setToolTip("Standard Claude Code effort key - cannot be deleted. Clear the value to emit nothing.")
            else:
                def _remove(_c=False, k=ekey):
                    ans = QMessageBox.question(content, "Remove", f"Delete effort key {k}?")
                    if ans != QMessageBox.StandardButton.Yes:
                        return
                    remove_path(f"{rep}.map.{k}")
                    _refresh_model_effort_map()
                rm.clicked.connect(_remove)
            rl.addWidget(rm)
            return row

        _EFF_ORDER = {k: i for i, k in enumerate(_STD_EFFORT_KEYS_M)}

        def _refresh_model_effort_map() -> None:
            while _eff_rows_layout.count():
                it = _eff_rows_layout.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.deleteLater()
            emap = get_path(read_settings(), f"{rep}.map", {}) or {}
            if not isinstance(emap, dict) or not emap:
                lbl = QLabel("No effort keys - add one below.")
                lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11.5px; padding: 6px;")
                _eff_rows_layout.addWidget(lbl)
                return

            def _sk(k: str):
                kl = k.lower()
                return (0, _EFF_ORDER[kl]) if kl in _EFF_ORDER else (1, kl)
            for k in sorted(emap.keys(), key=_sk):
                _eff_rows_layout.addWidget(_build_model_effort_row(k, emap.get(k)))

        def _on_add_effort_key() -> None:
            k = (_eff_add_key.text() or "").strip()
            if not k:
                return
            if not re.match(r"^[A-Za-z0-9_-]+$", k):
                QMessageBox.warning(content, "Invalid Key", "Use letters, digits, underscore, or dash only.")
                return
            cur = get_path(read_settings(), f"{rep}.map", {}) or {}
            if k in cur:
                QMessageBox.warning(content, "Exists", f"{k} already exists.")
                return
            patch_settings(f"{rep}.map.{k}", "")
            _eff_add_key.clear()
            _refresh_model_effort_map()

        _eff_add_btn.clicked.connect(_on_add_effort_key)
        _refresh_model_effort_map()

        _sl = _sec("Chat Template Kwargs",
                    "inference_defaults.chat_template_kwargs — arbitrary kwargs merged into every request")
        _sl.addWidget(_kv_row(f"{ip}.chat_template_kwargs",
            "Kwargs",
            "Merged into every request's chat_template_kwargs. Values are "
            "JSON-parsed -- anything the model's jinja template reads." "\n\n"
            "Prefer the dedicated controls where they exist: Thinking for "
            "enable_thinking, Reasoning Effort for reasoning_effort, and Preserve "
            "Reasoning (Per-Model Reasoning Override card) for preserve_thinking -- "
            "that last one is layer 2 of the prior-reasoning chain, overridden by both "
            "the --reasoning-preserve flag and the proxy's Drop Prior-Turn Thinking.",
            typed=True))

        _sl = _sec("LoRA",
                    "--lora — adapter applied on top of the base model")
        _sl.addWidget(_line_row(f"{p}.lora",        "LoRA Adapter (path)",
                                         "/path/to/adapter.gguf",
                                         "--lora: GGUF LoRA adapter file."))
        _sl.addWidget(_dependent(
            _number_row(f"{p}.lora_scale", "LoRA Scale",
                         0.0, 4.0, 0.05, 2, "",
                         "--lora-scaled: blend strength (1.0 = full)."),
            [f"{p}.lora"], lambda l: bool(str(l or "").strip())))

        _sl = _sec("Grammar",
                    "--grammar / --grammar-file — constrain generation with GBNF")
        _sl.addWidget(_code_row(f"{p}.grammar",       "GBNF Grammar (inline)",
                                         "(empty)",
                                         "--grammar: inline GBNF for constrained decoding.",
                                         height=180, highlighter=_GbnfHighlighter))
        _sl.addWidget(_line_row(f"{p}.grammar_file",  "Grammar File",
                                         "/path/to/grammar.gbnf",
                                         "--grammar-file: load GBNF from disk."))

        _sl = _sec("Advanced Placement",
                    "--override-* / --device — tensor and KV placement overrides")
        _sl.addWidget(_line_row(f"{p}.override_tensor", "Override Tensor",
                                         "blk\\.[0-9]+\\.ffn_.*=CPU",
                                         "--override-tensor: per-tensor buffer placement. Pattern=buffer (regex), comma-separated. "
                                         "Common use: pin specific layer weights to CPU/GPU."))
        _sl.addWidget(_line_row(f"{p}.override_kv", "Override KV",
                                         "tokenizer.ggml.add_bos_token=bool:false",
                                         "--override-kv: override GGUF metadata at load. Format KEY=TYPE:VALUE, comma-separated."))

        _sl = _sec("Extra CLI Args",
                    "llamacpp.models.<m>.extra_args — raw flags appended to the spawn argv")
        _sl.addWidget(_pair_list_row(f"{p}.extra_args", "Extra Args",
            'Per-model escape hatch — one [flag, value] pair per row. '
            'Top-level llamacpp.extra_args is also appended.'))

        _sl = _sec("Per-Model Reasoning Override",
                    "--reasoning* — server-side reasoning flags for this model")
        _sl.addWidget(_enum_row_strs(f"{p}.reasoning", "Reasoning",
                                              [("(server default — auto)", ""),
                                               ("on",   "on"),
                                               ("off",  "off"),
                                               ("auto", "auto")],
                                              "--reasoning: master toggle for thinking. 'auto' detects from template."))
        # Budgets are meaningless once thinking is forced off.
        _reason_not_off = lambda r: str(r or "").strip().lower() != "off"
        _sl.addWidget(_dependent(
            _number_row(f"{p}.reasoning_budget",        "Reasoning Budget",   -1, 1048576, 256, 0, "tok",
                         "--reasoning-budget. -1 = unlimited, 0 = disable thinking."),
            [f"{p}.reasoning"], _reason_not_off))
        _sl.addWidget(_dependent(
            _number_row(f"{p}.reasoning_budget_message","Reasoning Budget (per message)", -1, 1048576, 256, 0, "tok",
                         "--reasoning-budget-message. Per-turn cap."),
            [f"{p}.reasoning"], _reason_not_off))
        # Same --reasoning gate as the budget rows above.
        _sl.addWidget(_dependent(
            _enum_row_strs(f"{p}.reasoning_format", "Reasoning Format",
                                              [("(model default)", ""),
                                               ("none — keep <think> inline", "none"),
                                               ("deepseek — split into reasoning_content", "deepseek"),
                                               ("deepseek-legacy — both", "deepseek-legacy"),
                                               ("auto", "auto")],
                                              "--reasoning-format: how the server tags think blocks. "
                                              "Telecode parses <think> in the proxy — pick 'none' if this "
                                              "model is consumed via the proxy."),
            [f"{p}.reasoning"], _reason_not_off))
        # Layer 3 of the prior-reasoning chain. Layer 1 (the proxy's
        # drop_prior_thinking) strips the content before llama.cpp ever sees it,
        # so while that is on this switch has nothing to preserve -- grey it out
        # rather than let it look effective.
        _sl.addWidget(_dependent(
            _toggle_row(f"{p}.reasoning_preserve", "Preserve Reasoning",
                "LAYER 3 of 3 -- the server-side lever. --reasoning-preserve keeps the "
                "reasoning trace across the whole history, not just the last assistant "
                "message. llama-server suggests it at startup when the template "
                "advertises supports_preserve_reasoning." "\n\n" "Prefer this over "
                "setting a preserve_thinking chat-template kwarg by hand (layer 2): this "
                "flag drives that same template variable, and its default is whatever "
                "the template says. Note the templates disagree -- Qwen 3.8 preserves "
                "when the variable is undefined, Qwen 3.6 drops unless it is explicitly "
                "true." "\n\n" "Requires Drop Prior-Turn Thinking (layer 1, Proxy "
                "Behavior card) to be OFF, otherwise there is no prior reasoning left "
                "to keep."),
            ["llamacpp.inference.drop_prior_thinking"], lambda d: not bool(d)))

    def _refresh_picker(preserve_key: str | None = None):
        picker.blockSignals(True)
        picker.clear()
        models = list(get_path(read_settings(), "llamacpp.models", {}) or {})
        for m in models:
            picker.addItem(m, m)
        if preserve_key and preserve_key in models:
            picker.setCurrentIndex(models.index(preserve_key))
        picker.blockSignals(False)
        default_lbl.setText(f"default: {get_path(read_settings(), 'llamacpp.default_model', '—') or '—'}")
        if picker.count():
            _build_form(picker.currentData() or picker.itemData(0))
        else:
            _clear_form()

    def _on_pick(_i: int):
        key = picker.currentData()
        if key:
            _build_form(key)

    def _on_add():
        import copy
        name, ok = QInputDialog.getText(content, "Add Model", "Model key (e.g. qwen3.8-27b):")
        if not ok:
            return
        name = name.strip()
        valid, err = _valid_model_name(name)
        if not valid:
            QMessageBox.warning(content, "Invalid Name", err)
            return
        existing = get_path(read_settings(), "llamacpp.models", {}) or {}
        if name in existing:
            QMessageBox.warning(content, "Exists", f"Model '{name}' already exists.")
            return
        # deepcopy so nested dicts are never shared with _MODEL_DEFAULTS
        ename = name.replace(".", r"\.")
        patch_settings(f"llamacpp.models.{ename}", copy.deepcopy(_MODEL_DEFAULTS))
        _refresh_picker(preserve_key=name)

    def _on_rename():
        import copy
        key = picker.currentData()
        if not key:
            return
        new_name, ok = QInputDialog.getText(content, "Rename Model", "New model name / key (e.g. qwen3.8-27b):", text=key)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == key:
            return
        valid, err = _valid_model_name(new_name)
        if not valid:
            QMessageBox.warning(content, "Invalid Name", err)
            return
        raw_settings = read_settings()
        existing = get_path(raw_settings, "llamacpp.models", {}) or {}
        if new_name in existing:
            QMessageBox.warning(content, "Exists", f"Model '{new_name}' already exists.")
            return

        ek_old = key.replace(".", r"\.")
        ek_new = new_name.replace(".", r"\.")
        model_data = get_path(raw_settings, f"llamacpp.models.{ek_old}", {}) or {}

        patch_settings(f"llamacpp.models.{ek_new}", copy.deepcopy(model_data))
        remove_path(f"llamacpp.models.{ek_old}")

        if get_path(read_settings(), "llamacpp.default_model") == key:
            patch_settings("llamacpp.default_model", new_name)

        _refresh_picker(preserve_key=new_name)

    def _on_remove():
        key = picker.currentData()
        if not key:
            return
        if QMessageBox.question(content, "Remove", f"Delete model '{key}'?") != QMessageBox.StandardButton.Yes:
            return
        ek = key.replace(".", r"\.")
        remove_path(f"llamacpp.models.{ek}")
        # If default pointed at it, clear default
        if get_path(read_settings(), "llamacpp.default_model") == key:
            patch_settings("llamacpp.default_model", "")
        _refresh_picker()

    def _on_set_default():
        key = picker.currentData()
        if key:
            patch_settings("llamacpp.default_model", key)
            default_lbl.setText(f"default: {key}")

    picker.currentIndexChanged.connect(_on_pick)
    add_btn.clicked.connect(_on_add)
    rename_btn.clicked.connect(_on_rename)
    remove_btn.clicked.connect(_on_remove)
    set_default_btn.clicked.connect(_on_set_default)

    _refresh_picker()
    return scroll


# ══════════════════════════════════════════════════════════════════════
# Tools (tools.*) — CLI & computer tool entries with add/remove
# ══════════════════════════════════════════════════════════════════════

_TOOL_DEFAULTS_CLI: dict[str, Any] = {
    "name": "",
    "icon": "🔧",
    "startup_cmd": [],
    "flags": [],
    "env": {},
    "session": {"resume_id": ""},
}


def _debounced_commit(te, commit_fn, delay_ms: int = 500):
    """Attach a QTimer so we patch settings only when typing pauses.

    Also exposes `te._commit_now()` so `_flush_pending(container)` can force
    any in-flight debounced edits to persist before the form is rebuilt /
    the widget destroyed (e.g. on picker change)."""
    timer = QTimer(te)
    timer.setSingleShot(True)
    timer.setInterval(delay_ms)
    timer.timeout.connect(commit_fn)
    te.textChanged.connect(lambda: timer.start())
    def _commit_now():
        if timer.isActive():
            timer.stop()
            try:
                commit_fn()
            except Exception:
                pass
    te._commit_now = _commit_now  # type: ignore[attr-defined]


def _flush_pending(container) -> None:
    """Fire any pending debounced commits attached to QPlainTextEdit descendants."""
    from PySide6.QtWidgets import QPlainTextEdit
    for te in container.findChildren(QPlainTextEdit):
        fn = getattr(te, "_commit_now", None)
        if callable(fn):
            fn()


_MODEL_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _valid_model_name(name: str) -> tuple[bool, str]:
    """Validate model registry keys (e.g. 'qwen3.8-27b', 'qwen3.6-35b')."""
    if not name:
        return False, "Model name cannot be empty."
    if not _MODEL_KEY_RE.match(name):
        return False, ("Use letters, digits, '.', '_' or '-' only (must start with a letter or digit, "
                       "max 64 chars). Colons, slashes, and spaces are not allowed.")
    return True, ""


_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def _valid_key(name: str) -> tuple[bool, str]:
    """Reject keys containing ':', '.', whitespace, or JSON-hostile chars.
    Session keys follow `backend:name`; colons / dots would corrupt routing."""
    if not name:
        return False, "Name cannot be empty."
    if not _KEY_RE.match(name):
        return False, ("Use letters, digits, '_' or '-' only (must start with a letter, "
                       "max 64 chars). Colons, dots, and spaces are not allowed.")
    return True, ""


def _list_row(path: str, label: str, help_text: str = "",
              placeholder: str = "value") -> QWidget:
    """Structured list-of-strings editor (one row per entry, add/remove)."""
    return _build_array_row(path, label, help_text, placeholder, int_only=False)


def _build_array_row(path: str, label: str, help_text: str,
                      placeholder: str, *, int_only: bool) -> QWidget:
    from tray.qt_theme import BG_ELEV as _BG_ELEV
    from PySide6.QtGui import QIntValidator

    host = QWidget()
    hl = QVBoxLayout(host)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(6)

    rows_host = QWidget()
    rows_layout = QVBoxLayout(rows_host)
    rows_layout.setContentsMargins(0, 0, 0, 0)
    rows_layout.setSpacing(6)
    hl.addWidget(rows_host)

    add_w = QWidget()
    add_l = QHBoxLayout(add_w)
    add_l.setContentsMargins(0, 0, 0, 0)
    add_l.setSpacing(6)
    add_btn = QPushButton("+ Add")
    add_btn.setProperty("class", "primary")
    add_btn.setMaximumWidth(110)
    add_l.addWidget(add_btn)
    add_l.addStretch(1)
    hl.addWidget(add_w)

    entries: list[tuple[QLineEdit, QWidget]] = []

    def _commit() -> None:
        if int_only:
            out_i: list[int] = []
            for edit, _w in entries:
                s = edit.text().strip()
                if not s:
                    continue
                try:
                    out_i.append(int(s))
                except ValueError:
                    continue
            patch_settings(path, out_i)
        else:
            out_s = [edit.text() for edit, _w in entries if edit.text().strip() != ""]
            patch_settings(path, out_s)

    def _build_row(value: str = "") -> QWidget:
        row = QFrame()
        row.setStyleSheet(
            f"QFrame {{ background: {_BG_ELEV}; border: 1px solid {BORDER};"
            f" border-radius: 6px; }}"
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(8, 6, 8, 6)
        rl.setSpacing(6)

        edit = QLineEdit(); edit.setText(value)
        edit.setPlaceholderText(placeholder)
        if int_only:
            edit.setValidator(QIntValidator())
        rm_btn = QPushButton("✕")
        rm_btn.setFlat(True)
        rm_btn.setFixedWidth(28)
        rm_btn.setStyleSheet(
            f"QPushButton {{ color: {FG_DIM}; border: none; background: transparent; }}"
            f" QPushButton:hover {{ color: #ff6b6b; }}"
        )
        rl.addWidget(edit, 1)
        rl.addWidget(rm_btn)

        entry = (edit, row)
        entries.append(entry)
        edit.editingFinished.connect(_commit)

        def _remove():
            try:
                entries.remove(entry)
            except ValueError:
                pass
            row.setParent(None)
            row.deleteLater()
            _commit()
        rm_btn.clicked.connect(_remove)

        rows_layout.addWidget(row)
        return row

    cur = get_path(read_settings(), path, []) or []
    if isinstance(cur, list):
        for item in cur:
            s = str(item)
            if int_only:
                try:
                    int(s)
                except ValueError:
                    continue
            _build_row(s)

    def _on_add():
        row = _build_row("")
        try:
            row.findChild(QLineEdit).setFocus()
        except Exception:
            pass
    add_btn.clicked.connect(_on_add)

    return _row(row_label(label, help_text, path), host)


def _kv_row(path: str, label: str, help_text: str = "",
            typed: bool = False) -> QWidget:
    """Structured key→value dict editor (one row per pair).

    typed=True: values go through JSON parsing — so `enable_thinking=false`
    becomes {"enable_thinking": false} (bool), `budget=4096` becomes int,
    `voice=alloy` stays string. Needed for places like chat_template_kwargs
    where downstream jinja templates distinguish `false` from `"false"`."""
    import json as _json
    from tray.qt_theme import BG_ELEV as _BG_ELEV

    host = QWidget()
    hl = QVBoxLayout(host)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(6)

    rows_host = QWidget()
    rows_layout = QVBoxLayout(rows_host)
    rows_layout.setContentsMargins(0, 0, 0, 0)
    rows_layout.setSpacing(6)
    hl.addWidget(rows_host)

    add_w = QWidget()
    add_l = QHBoxLayout(add_w)
    add_l.setContentsMargins(0, 0, 0, 0)
    add_l.setSpacing(6)
    add_btn = QPushButton("+ Add")
    add_btn.setProperty("class", "primary")
    add_btn.setMaximumWidth(110)
    add_l.addWidget(add_btn)
    add_l.addStretch(1)
    hl.addWidget(add_w)

    pairs: list[tuple[QLineEdit, QLineEdit, QWidget]] = []

    def _stringify(v: Any) -> str:
        if typed and not isinstance(v, str):
            try:
                return _json.dumps(v)
            except Exception:
                return str(v)
        return str(v)

    def _parse(v: str) -> Any:
        if not typed:
            return v
        s = v.strip()
        try:
            return _json.loads(s)
        except Exception:
            return v

    def _commit() -> None:
        out: dict[str, Any] = {}
        for k_edit, v_edit, _w in pairs:
            k = k_edit.text().strip()
            if not k:
                continue
            out[k] = _parse(v_edit.text())
        patch_settings(path, out)

    def _build_row(k: str = "", v: str = "") -> QWidget:
        row = QFrame()
        row.setStyleSheet(
            f"QFrame {{ background: {_BG_ELEV}; border: 1px solid {BORDER};"
            f" border-radius: 6px; }}"
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(8, 6, 8, 6)
        rl.setSpacing(6)

        k_edit = QLineEdit(); k_edit.setText(k)
        k_edit.setPlaceholderText("KEY")
        k_edit.setMinimumWidth(160)
        eq = QLabel("="); eq.setStyleSheet(f"color: {FG_DIM}; padding: 0 2px;")
        v_edit = QLineEdit(); v_edit.setText(v)
        v_edit.setPlaceholderText("value")
        rm_btn = QPushButton("✕")
        rm_btn.setFlat(True)
        rm_btn.setFixedWidth(28)
        rm_btn.setStyleSheet(
            f"QPushButton {{ color: {FG_DIM}; border: none; background: transparent; }}"
            f" QPushButton:hover {{ color: #ff6b6b; }}"
        )

        rl.addWidget(k_edit)
        rl.addWidget(eq)
        rl.addWidget(v_edit, 1)
        rl.addWidget(rm_btn)

        entry: tuple[QLineEdit, QLineEdit, QWidget] = (k_edit, v_edit, row)
        pairs.append(entry)

        k_edit.editingFinished.connect(_commit)
        v_edit.editingFinished.connect(_commit)

        def _remove():
            try:
                pairs.remove(entry)
            except ValueError:
                pass
            row.setParent(None)
            row.deleteLater()
            _commit()
        rm_btn.clicked.connect(_remove)

        rows_layout.addWidget(row)
        return row

    cur = get_path(read_settings(), path, {}) or {}
    if isinstance(cur, dict):
        for k, v in cur.items():
            _build_row(str(k), _stringify(v))

    def _on_add():
        row = _build_row("", "")
        # focus the new key field for immediate typing
        try:
            row.findChild(QLineEdit).setFocus()
        except Exception:
            pass
    add_btn.clicked.connect(_on_add)

    return _row(row_label(label, help_text, path), host)


def _json_row(path: str, label: str, default: Any = None,
              height: int = 100, help_text: str = "") -> QWidget:
    """Generic JSON value editor with syntax highlighting + format/error feedback.

    Saves silently on parse error so user can keep typing without losing state."""
    import json as _json
    from PySide6.QtWidgets import QPlainTextEdit
    from PySide6.QtGui import QFont, QFontDatabase

    host = QWidget()
    hl = QVBoxLayout(host)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(4)

    te = QPlainTextEdit()
    te.setFixedHeight(height)
    mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    mono.setPointSize(10)
    te.setFont(mono)
    te.setTabChangesFocus(False)
    cur = get_path(read_settings(), path, default)
    try:
        te.setPlainText(_json.dumps(cur, indent=2, ensure_ascii=False))
    except Exception:
        te.setPlainText("")
    _JsonHighlighter(te.document())
    hl.addWidget(te)

    bar = QWidget()
    bar_l = QHBoxLayout(bar)
    bar_l.setContentsMargins(0, 0, 0, 0)
    bar_l.setSpacing(8)
    fmt_btn = QPushButton("Format")
    fmt_btn.setMaximumWidth(80)
    fmt_btn.setStyleSheet(
        f"QPushButton {{ color: {FG_DIM}; background: transparent;"
        f" border: 1px solid {BORDER}; border-radius: 4px; padding: 2px 8px; font-size: 11px; }}"
        f" QPushButton:hover {{ color: {FG}; }}"
    )
    status = QLabel("")
    status.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")
    bar_l.addWidget(fmt_btn)
    bar_l.addWidget(status, 1)
    hl.addWidget(bar)

    def _validate() -> tuple[bool, Any]:
        txt = te.toPlainText().strip()
        if not txt:
            return True, default
        try:
            return True, _json.loads(txt)
        except _json.JSONDecodeError as e:
            status.setText(f"✕ {e.msg} (line {e.lineno}, col {e.colno})")
            status.setStyleSheet("color: #ff6b6b; font-size: 11px;")
            return False, None

    def _commit():
        ok, val = _validate()
        if not ok:
            return
        patch_settings(path, val)
        status.setText("✓ saved")
        status.setStyleSheet(f"color: {OK}; font-size: 11px;")

    def _on_format():
        ok, val = _validate()
        if not ok:
            return
        try:
            te.setPlainText(_json.dumps(val, indent=2, ensure_ascii=False))
            status.setText("✓ formatted")
            status.setStyleSheet(f"color: {OK}; font-size: 11px;")
        except Exception:
            pass
    fmt_btn.clicked.connect(_on_format)

    _debounced_commit(te, _commit, delay_ms=800)
    return _row(row_label(label, help_text, path), host)


class _JsonHighlighter:
    """Lightweight JSON syntax highlighter — strings, numbers, literals, keys."""
    def __init__(self, doc):
        from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont
        from PySide6.QtCore import QRegularExpression as QRE

        def make_fmt(color: str, bold: bool = False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            return f

        # Order matters — later rules overwrite earlier formatting on overlap.
        rules = [
            # Numbers (won't overlap with strings since they're outside quotes)
            (QRE(r'(?<![A-Za-z_"])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b'),
             make_fmt("#f0abfc")),
            # Literals
            (QRE(r'\b(?:true|false|null)\b'), make_fmt("#fb923c", bold=True)),
            # String values (any quoted token); applied before keys so keys win.
            (QRE(r'"(?:[^"\\]|\\.)*"'), make_fmt("#a3e635")),
            # Keys: a quoted token followed by a colon — overrides the string color
            (QRE(r'"(?:[^"\\]|\\.)*"(?=\s*:)'), make_fmt("#7dd3fc", bold=True)),
        ]

        class _Inner(QSyntaxHighlighter):
            def highlightBlock(self, text):
                for pattern, fmt in rules:
                    it = pattern.globalMatch(text)
                    while it.hasNext():
                        m = it.next()
                        self.setFormat(m.capturedStart(), m.capturedLength(), fmt)

        self._inner = _Inner(doc)


def _int_list_row(path: str, label: str, help_text: str = "",
                  placeholder: str = "value") -> QWidget:
    """Structured list-of-ints editor (one row per entry, add/remove)."""
    return _build_array_row(path, label, help_text, placeholder, int_only=True)


def _pair_list_row(path: str, label: str, help_text: str = "",
                    flag_placeholder: str = "--flag",
                    value_placeholder: str = "value (optional)") -> QWidget:
    """Structured editor for `[[flag, value], ...]` style CLI arg lists.

    Rows preserve order; both fields are free-text. A row with empty flag is
    skipped on commit. A row with empty value commits as `[flag]` (single-element
    list — flag-only switch)."""
    from tray.qt_theme import BG_ELEV as _BG_ELEV

    host = QWidget()
    hl = QVBoxLayout(host)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(6)

    rows_host = QWidget()
    rows_layout = QVBoxLayout(rows_host)
    rows_layout.setContentsMargins(0, 0, 0, 0)
    rows_layout.setSpacing(6)
    hl.addWidget(rows_host)

    add_w = QWidget()
    add_l = QHBoxLayout(add_w)
    add_l.setContentsMargins(0, 0, 0, 0)
    add_l.setSpacing(6)
    add_btn = QPushButton("+ Add")
    add_btn.setProperty("class", "primary")
    add_btn.setMaximumWidth(110)
    add_l.addWidget(add_btn)
    add_l.addStretch(1)
    hl.addWidget(add_w)

    pairs: list[tuple[QLineEdit, QLineEdit, QWidget]] = []

    def _commit() -> None:
        out: list[list[str]] = []
        for f_edit, v_edit, _w in pairs:
            f = f_edit.text().strip()
            if not f:
                continue
            v = v_edit.text()
            out.append([f, v] if v != "" else [f])
        patch_settings(path, out)

    def _build_row(flag: str = "", value: str = "") -> QWidget:
        row = QFrame()
        row.setStyleSheet(
            f"QFrame {{ background: {_BG_ELEV}; border: 1px solid {BORDER};"
            f" border-radius: 6px; }}"
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(8, 6, 8, 6)
        rl.setSpacing(6)

        f_edit = QLineEdit(); f_edit.setText(flag)
        f_edit.setPlaceholderText(flag_placeholder)
        f_edit.setMinimumWidth(160)
        v_edit = QLineEdit(); v_edit.setText(value)
        v_edit.setPlaceholderText(value_placeholder)
        rm_btn = QPushButton("✕")
        rm_btn.setFlat(True)
        rm_btn.setFixedWidth(28)
        rm_btn.setStyleSheet(
            f"QPushButton {{ color: {FG_DIM}; border: none; background: transparent; }}"
            f" QPushButton:hover {{ color: #ff6b6b; }}"
        )
        rl.addWidget(f_edit)
        rl.addWidget(v_edit, 1)
        rl.addWidget(rm_btn)

        entry = (f_edit, v_edit, row)
        pairs.append(entry)
        f_edit.editingFinished.connect(_commit)
        v_edit.editingFinished.connect(_commit)

        def _remove():
            try:
                pairs.remove(entry)
            except ValueError:
                pass
            row.setParent(None)
            row.deleteLater()
            _commit()
        rm_btn.clicked.connect(_remove)

        rows_layout.addWidget(row)
        return row

    cur = get_path(read_settings(), path, []) or []
    if isinstance(cur, list):
        for item in cur:
            if isinstance(item, list) and item:
                f = str(item[0])
                v = str(item[1]) if len(item) > 1 else ""
                _build_row(f, v)
            elif isinstance(item, str) and item:
                _build_row(item, "")

    def _on_add():
        row = _build_row("", "")
        try:
            row.findChild(QLineEdit).setFocus()
        except Exception:
            pass
    add_btn.clicked.connect(_on_add)

    return _row(row_label(label, help_text, path), host)


def _password_row(path: str, label: str, placeholder: str = "",
                  help_text: str = "") -> QWidget:
    """String row with masked input — used for tokens / API keys."""
    le = QLineEdit()
    le.setPlaceholderText(placeholder)
    le.setEchoMode(QLineEdit.EchoMode.Password)
    le.setText(str(get_path(read_settings(), path, "") or ""))
    le.editingFinished.connect(lambda: patch_settings(path, le.text()))
    return _row(row_label(label, help_text, path), le)


def _tools(window) -> QWidget:
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    scroll, content, layout = _page()
    card, body = _card("Tools", "tools.* — CLI tools + computer control entries")

    # ── Picker row ───────────────────────────────────────────────────
    top = QHBoxLayout(); top.setSpacing(8)
    picker = QComboBox(); picker.setMinimumWidth(240)
    add_btn = QPushButton("+ Add CLI Tool"); add_btn.setProperty("class", "primary")
    remove_btn = QPushButton("Remove"); remove_btn.setProperty("class", "danger")
    top.addWidget(picker); top.addStretch(1); top.addWidget(add_btn); top.addWidget(remove_btn)
    body.addLayout(top)

    form_host = QWidget()
    form_layout = QVBoxLayout(form_host)
    form_layout.setContentsMargins(0, 4, 0, 0)
    form_layout.setSpacing(10)
    body.addWidget(form_host)
    layout.addWidget(card)
    layout.addStretch(1)

    def _clear_form():
        # Flush pending debounced edits so typing isn't lost on picker change
        _flush_pending(form_host)
        while form_layout.count():
            item = form_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _build_form(key: str):
        _clear_form()
        p = f"tools.{key}"
        data = get_path(read_settings(), p, {}) or {}
        # Robust shape check: computer-control tool has a dict-typed `api` key
        # with the specific `format` sub-key and NO `startup_cmd` (a future
        # CLI tool with its own `api` block would still have startup_cmd).
        api = data.get("api") if isinstance(data, dict) else None
        is_computer = (
            isinstance(api, dict)
            and "format" in api
            and not data.get("startup_cmd")
        )

        form_layout.addWidget(_section_header("Identity"))
        form_layout.addWidget(_line_row(f"{p}.name", "Display Name", "My Tool"))
        form_layout.addWidget(_line_row(f"{p}.icon", "Icon (emoji)", "🔧"))

        if is_computer:
            # Computer-control shape
            form_layout.addWidget(_section_header("API"))
            form_layout.addWidget(_line_row(f"{p}.api.base_url", "Base URL",
                                             "http://localhost:1235/v1"))
            form_layout.addWidget(_line_row(f"{p}.api.api_key",  "API Key", "local"))
            form_layout.addWidget(_line_row(f"{p}.api.model",    "Model", "qwen3.5-35b"))
            form_layout.addWidget(_enum_row_strs(f"{p}.api.format", "Format",
                                                   [("OpenAI", "openai"),
                                                    ("Anthropic", "anthropic"),
                                                    ("Claude Code CLI", "claude-code")]))
            form_layout.addWidget(_section_header("Loop"))
            form_layout.addWidget(_number_row(f"{p}.capture_interval", "Capture Interval", 1, 30, 1, 0, "s"))
            form_layout.addWidget(_number_row(f"{p}.max_history",      "Max History",       5, 100, 5, 0))
            form_layout.addWidget(_line_row(f"{p}.system_prompt",      "System Prompt",    ""))
        else:
            # CLI shape
            form_layout.addWidget(_section_header("Command"))
            form_layout.addWidget(_list_row(f"{p}.startup_cmd", "Startup Cmd",
                                             "One binary / arg per line. First line = binary.",
                                             "claude"))
            form_layout.addWidget(_list_row(f"{p}.flags",        "Flags",
                                             "Extra CLI flags, one per line.",
                                             "--dangerously-skip-permissions"))
            form_layout.addWidget(_section_header("Environment"))
            form_layout.addWidget(_kv_row(f"{p}.env", "env",
                                           "One KEY=value per line. Applied when spawning this tool."))
            form_layout.addWidget(_section_header("Session"))
            form_layout.addWidget(_line_row(f"{p}.session.resume_id",
                                             "Resume ID",
                                             "Set by the bot — used to reattach to prior runs."))

            form_layout.addWidget(_section_header("Streaming Overrides"))
            form_layout.addWidget(_number_row(f"{p}.streaming.idle_sec",     "PTY Idle Threshold",
                                               0.0, 10.0, 0.1, 1, "s",
                                               "Per-tool override of streaming.idle_sec. 0 = inherit global."))
            form_layout.addWidget(_number_row(f"{p}.streaming.max_wait_sec", "PTY Max Wait",
                                               0.0, 30.0, 0.5, 1, "s",
                                               "Per-tool override of streaming.max_wait_sec. 0 = inherit global."))

    def _refresh_picker(preserve_key: str | None = None):
        picker.blockSignals(True)
        picker.clear()
        for k in list(get_path(read_settings(), "tools", {}) or {}):
            display = k
            ek = k.replace(".", r"\.")
            nm = get_path(read_settings(), f"tools.{ek}.name", "") or humanize(k)
            display = f"{k}  —  {nm}"
            picker.addItem(display, k)
        if preserve_key:
            for i in range(picker.count()):
                if picker.itemData(i) == preserve_key:
                    picker.setCurrentIndex(i); break
        picker.blockSignals(False)
        if picker.count():
            _build_form(picker.currentData() or picker.itemData(0))
        else:
            _clear_form()

    def _on_pick(_i: int):
        key = picker.currentData()
        if key:
            _build_form(key)

    def _on_add():
        import copy
        name, ok = QInputDialog.getText(content, "Add CLI Tool",
                                         "Tool key (letters/digits/hyphens, e.g. powershell):")
        if not ok:
            return
        name = name.strip()
        valid, err = _valid_key(name)
        if not valid:
            QMessageBox.warning(content, "Invalid Name", err)
            return
        existing = get_path(read_settings(), "tools", {}) or {}
        if name in existing:
            QMessageBox.warning(content, "Exists", f"Tool '{name}' already exists.")
            return
        default = copy.deepcopy(_TOOL_DEFAULTS_CLI)
        default["name"] = humanize(name)
        ename = name.replace(".", r"\.")
        patch_settings(f"tools.{ename}", default)
        _refresh_picker(preserve_key=name)

    def _on_remove():
        key = picker.currentData()
        if not key:
            return
        if QMessageBox.question(content, "Remove", f"Delete tool '{key}'?") != QMessageBox.StandardButton.Yes:
            return
        ek = key.replace(".", r"\.")
        remove_path(f"tools.{ek}")
        _refresh_picker()

    picker.currentIndexChanged.connect(_on_pick)
    add_btn.clicked.connect(_on_add)
    remove_btn.clicked.connect(_on_remove)

    _refresh_picker()
    return scroll


def _requests(window) -> QWidget:
    """Live request log viewer with a foldable structured JSON tree."""
    import time as _time
    from PySide6.QtWidgets import (
        QSplitter, QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem,
    )
    from PySide6.QtGui import QColor, QBrush, QFont
    from tray.qt_theme import ACCENT, WARN, ERR, OK, FG_DIM, FG_MUTE, BG_ELEV, BG_CARD

    try:
        from proxy import request_log
    except Exception:
        request_log = None  # type: ignore[assignment]

    scroll, content, layout = _page()
    layout.setContentsMargins(16, 14, 16, 14)
    card, body = _card("Requests", "Live proxy request log · click to inspect")

    # ── Top controls ─────────────────────────────────────────────────
    top = QHBoxLayout(); top.setSpacing(8)
    count_lbl = QLabel("0 requests"); count_lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")
    pause_lbl = QLabel("Pause"); pause_lbl.setProperty("class", "toggle_label")
    pause_cb = Toggle()
    clear_btn = QPushButton("Clear"); clear_btn.setProperty("class", "ghost")
    expand_btn = QPushButton("Expand All"); expand_btn.setProperty("class", "ghost")
    collapse_btn = QPushButton("Collapse"); collapse_btn.setProperty("class", "ghost")
    top.addWidget(count_lbl)
    top.addStretch(1)
    top.addWidget(pause_lbl); top.addWidget(pause_cb)
    top.addSpacing(8)
    top.addWidget(expand_btn); top.addWidget(collapse_btn); top.addWidget(clear_btn)
    body.addLayout(top)

    # ── Split: list | tree ───────────────────────────────────────────
    split = QSplitter(Qt.Orientation.Horizontal)
    split.setChildrenCollapsible(False)

    req_list = QListWidget()
    req_list.setStyleSheet(
        f"QListWidget {{ background: {BG_ELEV}; border: 1px solid {BORDER};"
        f" border-radius: 6px; outline: 0; font-family: 'JetBrains Mono', Consolas, monospace;"
        f" font-size: 11px; padding: 4px; }}"
        f"QListWidget::item {{ padding: 5px 8px; border-radius: 3px; margin-bottom: 1px; }}"
        f"QListWidget::item:hover {{ background: {BG_CARD}; }}"
        f"QListWidget::item:selected {{ background: {BG_CARD}; color: {FG}; border-left: 2px solid {ACCENT}; }}"
    )
    req_list.setMinimumWidth(320)

    tree = QTreeWidget()
    tree.setHeaderLabels(["Key", "Value"])
    tree.setAlternatingRowColors(False)
    tree.setStyleSheet(
        f"QTreeWidget {{ background: {BG_ELEV}; border: 1px solid {BORDER};"
        f" border-radius: 6px; font-family: 'JetBrains Mono', Consolas, monospace;"
        f" font-size: 11px; padding: 4px; outline: 0; }}"
        f"QTreeWidget::item {{ padding: 2px 4px; }}"
        f"QTreeWidget::item:hover {{ background: {BG_CARD}; }}"
        f"QTreeWidget::item:selected {{ background: {BG_CARD}; color: {FG}; }}"
        f"QHeaderView {{ background: {BG_CARD}; border: none; }}"
        f"QHeaderView::section {{ background: {BG_CARD}; color: {FG_MUTE};"
        f" padding: 4px 6px; border: none; border-bottom: 1px solid {BORDER};"
        f" font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; }}"
        f"QTreeWidget::branch {{ background: transparent; }}"
    )
    tree.header().resizeSection(0, 260)
    # Long values (full content blocks, web-search result bodies) need
    # horizontal scrolling — header().setStretchLastSection(False) stops
    # Qt from capping the last column at the widget width, and
    # setSectionResizeMode(Interactive) lets values extend beyond the
    # viewport so the tree's own h-scrollbar kicks in.
    from PySide6.QtWidgets import QHeaderView as _QHV
    tree.header().setStretchLastSection(False)
    tree.header().setSectionResizeMode(0, _QHV.ResizeMode.Interactive)
    tree.header().setSectionResizeMode(1, _QHV.ResizeMode.ResizeToContents)
    tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    tree.setTextElideMode(Qt.TextElideMode.ElideNone)

    split.addWidget(req_list)
    split.addWidget(tree)
    split.setStretchFactor(0, 0)
    split.setStretchFactor(1, 1)
    split.setSizes([340, 700])
    body.addWidget(split, 1)

    card.setMinimumHeight(560)
    layout.addWidget(card, 1)

    # ── JSON → QTreeWidgetItem ───────────────────────────────────────
    TYPE_COLORS = {
        "str":   "#c8e2a8",
        "int":   "#a8b3c7",
        "float": "#a8b3c7",
        "bool":  ACCENT,
        "null":  FG_MUTE,
    }

    def _leaf_item(key: str, value: Any) -> QTreeWidgetItem:
        if value is None:
            tname, shown = "null", "null"
        elif isinstance(value, bool):
            tname, shown = "bool", "true" if value else "false"
        elif isinstance(value, int):
            tname, shown = "int", str(value)
        elif isinstance(value, float):
            tname, shown = "float", f"{value:g}"
        elif isinstance(value, str):
            tname = "str"
            shown = value if len(value) < 300 else value[:300] + f"… (+{len(value)-300} chars)"
            shown = f"\"{shown}\""
        else:
            tname, shown = type(value).__name__, repr(value)
        it = QTreeWidgetItem([key, shown])
        it.setForeground(1, QBrush(QColor(TYPE_COLORS.get(tname, FG))))
        it.setForeground(0, QBrush(QColor(ACCENT)))
        return it

    def _populate(parent: QTreeWidgetItem, value: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, (dict, list)):
                    summary = f"{{{len(v)}}}" if isinstance(v, dict) else f"[{len(v)}]"
                    child = QTreeWidgetItem([str(k), summary])
                    child.setForeground(0, QBrush(QColor(ACCENT)))
                    child.setForeground(1, QBrush(QColor(FG_MUTE)))
                    parent.addChild(child)
                    _populate(child, v)
                else:
                    parent.addChild(_leaf_item(str(k), v))
        elif isinstance(value, list):
            for i, v in enumerate(value):
                if isinstance(v, (dict, list)):
                    summary = f"{{{len(v)}}}" if isinstance(v, dict) else f"[{len(v)}]"
                    child = QTreeWidgetItem([f"[{i}]", summary])
                    child.setForeground(0, QBrush(QColor(WARN)))
                    child.setForeground(1, QBrush(QColor(FG_MUTE)))
                    parent.addChild(child)
                    _populate(child, v)
                else:
                    parent.addChild(_leaf_item(f"[{i}]", v))

    def _render(entry: dict[str, Any]) -> None:
        tree.clear()
        root = tree.invisibleRootItem()
        _populate(root, entry)
        # Pre-expand top-level only
        for i in range(root.childCount()):
            root.child(i).setExpanded(i < 3)

    # ── Status color helper ──────────────────────────────────────────
    def _status_color(status: int | None, error: str) -> str:
        if error:
            return ERR
        if status is None:
            return WARN  # in-flight
        if status >= 500:
            return ERR
        if status >= 400:
            return WARN
        if status >= 200:
            return OK
        return FG_MUTE

    # ── Refresh driver ───────────────────────────────────────────────
    state: dict[str, Any] = {"selected_rid": None, "snapshot": []}

    def _fmt_row(e: dict[str, Any]) -> str:
        ts = _time.strftime("%H:%M:%S", _time.localtime(e["started_at"]))
        status = e.get("status")
        status_txt = f"{status}" if status is not None else "…"
        dur = e.get("duration_ms")
        dur_txt = f"{dur}ms" if dur is not None else "—"
        proto = (e.get("inbound_protocol") or "")[:4]
        model = (e.get("client_model") or "")[:28]
        return f"{ts}  {status_txt:>3}  {dur_txt:>6}  {proto:<4}  {model}"

    def refresh():
        if pause_cb.isChecked() or request_log is None:
            return
        snap = request_log.snapshot()
        state["snapshot"] = snap
        count_lbl.setText(f"{len(snap)} requests")

        # Rebuild list only if rids changed (cheap header compare)
        prev_rids = [req_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(req_list.count())]
        new_rids = [e["rid"] for e in snap]
        if prev_rids != new_rids:
            sel_rid = state.get("selected_rid")
            req_list.blockSignals(True)
            req_list.clear()
            for e in snap:
                item = QListWidgetItem(_fmt_row(e))
                item.setData(Qt.ItemDataRole.UserRole, e["rid"])
                item.setForeground(QBrush(QColor(_status_color(e.get("status"), e.get("error", "")))))
                req_list.addItem(item)
            # restore selection
            if sel_rid and sel_rid in new_rids:
                req_list.setCurrentRow(new_rids.index(sel_rid))
            req_list.blockSignals(False)
        else:
            # Update status colors / durations in place (in-flight → finished)
            for i, e in enumerate(snap):
                item = req_list.item(i)
                if item is None:
                    continue
                item.setText(_fmt_row(e))
                item.setForeground(QBrush(QColor(_status_color(e.get("status"), e.get("error", "")))))

        # Refresh tree if selected entry changed
        rid = state.get("selected_rid")
        if rid:
            entry = next((e for e in snap if e["rid"] == rid), None)
            if entry is not None and entry.get("finished_at") != state.get("_last_finished"):
                state["_last_finished"] = entry.get("finished_at")
                _render(entry)

    def _on_select(row: int):
        if row < 0 or row >= req_list.count():
            return
        rid = req_list.item(row).data(Qt.ItemDataRole.UserRole)
        state["selected_rid"] = rid
        entry = next((e for e in state["snapshot"] if e["rid"] == rid), None)
        if entry is not None:
            state["_last_finished"] = entry.get("finished_at")
            _render(entry)

    req_list.currentRowChanged.connect(_on_select)
    clear_btn.clicked.connect(lambda: (request_log.clear() if request_log else None, req_list.clear(), tree.clear(), state.update({"selected_rid": None})))
    expand_btn.clicked.connect(tree.expandAll)
    collapse_btn.clicked.connect(tree.collapseAll)

    scroll.refresh = refresh  # type: ignore[attr-defined]
    refresh()
    return scroll


def _raw(window) -> QWidget:
    """JSON editor for settings.json — the catch-all for anything not
    exposed by the curated sections. Syntax-highlighted, validates on
    save, best-effort atomic write + config.reload()."""
    import json as _json
    from PySide6.QtCore import QRegularExpression, Qt
    from PySide6.QtGui import QTextCharFormat, QColor, QSyntaxHighlighter, QFont
    from PySide6.QtWidgets import QPlainTextEdit, QLabel, QPushButton, QWidget, QVBoxLayout
    from tray.qt_theme import ACCENT, WARN, ERR, OK, FG_DIM, FG_MUTE
    from tray.qt_helpers import settings_path as _sp

    # Build a non-scrolling page: the outer `_page()`'s QScrollArea
    # fights with the editor's own vertical scrollbar (editor has
    # Expanding size policy → grows to content → QScrollArea never
    # scrolls → editor's scrollbar never appears either). Using a plain
    # QWidget host lets QPlainTextEdit own its scrollbars cleanly.
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(26, 22, 26, 22)
    layout.setSpacing(18)
    card, body = _card("Raw settings.json",
                        "Everything the curated sections don't cover. "
                        "Editable JSON — Save validates + atomic-writes + config.reload().")

    class JsonHighlighter(QSyntaxHighlighter):
        def __init__(self, doc):
            super().__init__(doc)
            def fmt(color: str, bold: bool = False) -> QTextCharFormat:
                f = QTextCharFormat()
                f.setForeground(QColor(color))
                if bold:
                    f.setFontWeight(QFont.Weight.DemiBold)
                return f
            self._rules = [
                (QRegularExpression(r'"[^"\\]*(?:\\.[^"\\]*)*"\s*:'), fmt(ACCENT, True)),   # keys
                (QRegularExpression(r':\s*"[^"\\]*(?:\\.[^"\\]*)*"'), fmt(OK)),              # string vals
                (QRegularExpression(r'\b(true|false|null)\b'),        fmt(WARN, True)),      # keywords
                (QRegularExpression(r'\b-?\d+\.?\d*(?:[eE][+-]?\d+)?\b'), fmt("#b892ff")),   # numbers
            ]
        def highlightBlock(self, text):  # type: ignore[override]
            for regex, f in self._rules:
                it = regex.globalMatch(text)
                while it.hasNext():
                    m = it.next()
                    self.setFormat(m.capturedStart(), m.capturedLength(), f)

    editor = QPlainTextEdit()
    # Inlining scrollbar QSS because setStyleSheet on the editor can
    # block the global QScrollBar rules from reaching its own child
    # scrollbars (Qt cascade quirk), which left the scrollbars
    # invisibly-styled (0 width / transparent handle) even though the
    # policy was AlwaysOn.
    editor.setStyleSheet(
        "QPlainTextEdit {"
        "  background-color: transparent; color: #cdd3de;"
        "  font-family: 'Cascadia Code', Consolas, monospace;"
        "  font-size: 12px; border: none; padding: 6px;"
        "}"
        "QScrollBar:vertical {"
        "  background: #151b28; width: 14px; margin: 2px 0; border: none;"
        "}"
        "QScrollBar::handle:vertical {"
        "  background: #4a5a82; border-radius: 5px; min-height: 28px;"
        "}"
        "QScrollBar::handle:vertical:hover { background: #6b82b8; }"
        "QScrollBar:horizontal {"
        "  background: #151b28; height: 14px; margin: 0 2px; border: none;"
        "}"
        "QScrollBar::handle:horizontal {"
        "  background: #4a5a82; border-radius: 5px; min-width: 28px;"
        "}"
        "QScrollBar::handle:horizontal:hover { background: #6b82b8; }"
        "QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }"
        "QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }"
    )
    from PySide6.QtWidgets import QSizePolicy
    editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    editor.setMinimumHeight(320)
    editor.setTabChangesFocus(False)
    # Force both scrollbars visible so long JSON doesn't vanish off the
    # bottom/right edge. Word-wrap off so indentation-based scanning of
    # big objects reads naturally.
    editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    JsonHighlighter(editor.document())

    status = QLabel("")
    status.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")

    row = QHBoxLayout()
    row.setContentsMargins(0, 4, 0, 0)
    row.setSpacing(8)
    reload_btn = QPushButton("Reload From Disk")
    reload_btn.setProperty("class", "ghost")
    save_btn = QPushButton("Save")
    save_btn.setProperty("class", "primary")
    row.addWidget(status, 1)
    row.addWidget(reload_btn)
    row.addWidget(save_btn)

    def _load_into_editor() -> None:
        try:
            p = _sp()
            text = p.read_text(encoding="utf-8")
            editor.setPlainText(text)
            status.setText(f"loaded from {p} ({len(text)} bytes)")
            status.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        except Exception as exc:
            status.setText(f"read failed: {exc}")
            status.setStyleSheet(f"color: {ERR}; font-size: 11px;")

    def _on_save() -> None:
        text = editor.toPlainText()
        try:
            data = _json.loads(text)
        except Exception as exc:
            status.setText(f"invalid JSON — not saved: {exc}")
            status.setStyleSheet(f"color: {ERR}; font-size: 11px;")
            return
        if not isinstance(data, dict):
            status.setText("top-level must be a JSON object")
            status.setStyleSheet(f"color: {ERR}; font-size: 11px;")
            return
        # Atomic write + reload via the same path as patch_settings
        import os as _os
        p = _sp()
        try:
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(_json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
            _os.replace(tmp, p)
            try:
                import config as app_config
                app_config.reload()
            except Exception as exc:
                status.setText(f"saved but reload failed: {exc}")
                status.setStyleSheet(f"color: {WARN}; font-size: 11px;")
                return
            status.setText(f"saved + reloaded ({len(text)} bytes)")
            status.setStyleSheet(f"color: {OK}; font-size: 11px;")
        except Exception as exc:
            status.setText(f"write failed: {exc}")
            status.setStyleSheet(f"color: {ERR}; font-size: 11px;")

    reload_btn.clicked.connect(_load_into_editor)
    save_btn.clicked.connect(_on_save)

    body.addWidget(editor, 1)       # stretch factor 1 so editor fills card
    body.addLayout(row)
    layout.addWidget(card, 1)       # card itself fills the page vertically
    # No trailing stretch — the card already owns the vertical space.

    _load_into_editor()
    return host


def _docgraph(window) -> QWidget:
    from tray.qt_docgraph import build_docgraph_tabs
    return build_docgraph_tabs(window)


_BUILDERS: dict[str, Callable[[Any], QWidget]] = {
    "status":   _status,
    "llama":    _llama,
    "models":   _models,
    "proxy":    _proxy,
    "mcp":      _mcp,
    "managed":  _managed,
    "docgraph": _docgraph,
    "tools":    _tools,
    "telegram": _telegram,
    "audio":    _audio,
    "computer": _computer,
    "sessions": _sessions,
    "requests": _requests,
    "logs":     _logs,
    "raw":      _raw,
}
