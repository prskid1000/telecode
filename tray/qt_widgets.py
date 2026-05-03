"""Custom Qt widgets — toggle switch + NumberEditor (text + slider).

Both are paint-and-signal self-contained. NumberEditor exposes the
current value as a float; callers connect to its `valueChanged` signal.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (
    Qt, QRectF, QSize, Signal, QPropertyAnimation, QEasingCurve, Property,
)
from PySide6.QtGui import QPainter, QColor, QBrush
from PySide6.QtWidgets import (
    QCheckBox, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit,
    QSlider, QSizePolicy,
)


# ══════════════════════════════════════════════════════════════════════
# WrapLabel — word-wrapped QLabel that doesn't push parent width
# ══════════════════════════════════════════════════════════════════════

class WrapLabel(QLabel):
    """QLabel with `wordWrap=True` AND a horizontal sizeHint of 0.

    Plain `QLabel.setWordWrap(True)` wraps at runtime but still reports
    `sizeHint().width()` as the unwrapped single-line width. That width
    bubbles up through QVBoxLayouts → cards → the QScrollArea content,
    forcing a horizontal scrollbar even though the visible text wraps
    fine. Returning 0 from sizeHint().width() + minimumSizeHint().width()
    breaks that propagation; the label still computes its actual height
    via heightForWidth() once the layout assigns it a width.
    """

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.MinimumExpanding)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return super().heightForWidth(max(1, w))

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:
        w = self.width() if self.width() > 0 else 0
        h = super().heightForWidth(w) if w > 0 else super().sizeHint().height()
        return QSize(0, h)

from tray.qt_theme import ACCENT, BG_CARD, FG, FG_DIM, FG_MUTE, BORDER


# ══════════════════════════════════════════════════════════════════════
# Toggle — animated pill switch
# ══════════════════════════════════════════════════════════════════════

class Toggle(QCheckBox):
    """Sleek toggle switch. Tri-state sliding animation on toggle.
    API: same as QCheckBox — setChecked / isChecked / stateChanged."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(40, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._offset = 3
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.stateChanged.connect(self._animate)

    def _animate(self, _state: int) -> None:
        target = 21 if self.isChecked() else 3
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(target)
        self._anim.start()

    def _get_offset(self) -> int:
        return self._offset

    def _set_offset(self, v: int) -> None:
        self._offset = v
        self.update()

    offset = Property(int, _get_offset, _set_offset)

    def paintEvent(self, _e) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Track
        track_color = QColor(ACCENT) if self.isChecked() else QColor("#2a3147")
        if not self.isEnabled():
            track_color.setAlpha(90)
        p.setBrush(QBrush(track_color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(0, 0, 40, 22), 11, 11)

        # Knob
        knob_color = QColor("#ffffff" if self.isChecked() else FG_MUTE)
        if not self.isEnabled():
            knob_color.setAlpha(150)
        p.setBrush(QBrush(knob_color))
        p.drawEllipse(QRectF(self._offset, 3, 16, 16))
        p.end()


# ══════════════════════════════════════════════════════════════════════
# NumberEditor — text input paired with a slider
# ══════════════════════════════════════════════════════════════════════

class NumberEditor(QWidget):
    """
    Numeric value with three things tied together:
      - QLineEdit (primary source of truth — user can type `0.6` directly)
      - QSlider  (convenient drag for quick adjustments)
      - `valueChanged(float)` signal

    Slider resolution: 1000 steps across [min, max]. Float values are
    clamped and rounded to `step` on emission.
    """
    valueChanged = Signal(float)

    def __init__(
        self,
        minimum: float = 0.0,
        maximum: float = 1.0,
        step: float = 0.01,
        decimals: int = 2,
        unit: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._min = float(minimum)
        self._max = float(maximum)
        self._step = float(step)
        self._decimals = int(decimals)
        self._unit = unit
        self._value = self._min
        self._emit_silence = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.edit = QLineEdit()
        self.edit.setFixedWidth(80)
        self.edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.edit.editingFinished.connect(self._on_edit)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(1000)
        self.slider.setSingleStep(1)
        self.slider.valueChanged.connect(self._on_slider)

        layout.addWidget(self.edit)
        layout.addWidget(self.slider, 1)
        if unit:
            unit_label = QLabel(unit)
            unit_label.setStyleSheet(f"color: {FG_DIM}; font-size: 11.5px;")
            layout.addWidget(unit_label)

    # ── public ───────────────────────────────────────────────────────

    def value(self) -> float:
        return self._value

    def setValue(self, v: float) -> None:
        v = max(self._min, min(self._max, float(v)))
        if abs(v - self._value) < self._step / 10:
            # Still sync widgets on initial set
            self._sync_widgets(v)
            return
        self._value = v
        self._sync_widgets(v)
        if not self._emit_silence:
            self.valueChanged.emit(v)

    def setRange(self, minimum: float, maximum: float) -> None:
        self._min = float(minimum)
        self._max = float(maximum)
        self._sync_widgets(self._value)

    # ── internals ────────────────────────────────────────────────────

    def _sync_widgets(self, v: float) -> None:
        fmt = f"{{:.{self._decimals}f}}"
        self._emit_silence = True
        # Don't clobber the text while the user is typing — refresh timers
        # call setRange/setValue every second and would reset the cursor.
        if not self.edit.hasFocus():
            self.edit.setText(fmt.format(v))
        span = self._max - self._min
        pos = 0 if span == 0 else int(round(1000 * (v - self._min) / span))
        self.slider.setValue(pos)
        self._emit_silence = False

    def _on_edit(self) -> None:
        try:
            v = float(self.edit.text())
        except ValueError:
            self._sync_widgets(self._value)
            return
        self.setValue(v)

    def _on_slider(self, pos: int) -> None:
        if self._emit_silence:
            return
        span = self._max - self._min
        v = self._min + span * (pos / 1000.0)
        # snap to step
        v = round(v / self._step) * self._step
        self.setValue(v)


# ══════════════════════════════════════════════════════════════════════
# Small helpers
# ══════════════════════════════════════════════════════════════════════

def row_label(text: str, help_text: str = "", path: str = "",
              cli: str = "") -> QWidget:
    """Multi-line row label: human name + (optional) help text + key path
    + optional `cli` hint (the underlying CLI flag this row maps to —
    useful for sections like docgraph where the UI is a thin wrapper
    over a CLI flag set)."""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(2)
    lbl = QLabel(text)
    lbl.setProperty("class", "row_label")
    v.addWidget(lbl)
    if help_text:
        hl = WrapLabel(help_text)
        hl.setProperty("class", "row_help")
        v.addWidget(hl)
    # Pack settings-key-path and CLI flag onto the same mono line so the
    # row's vertical footprint doesn't grow when both are present.
    pieces = [s for s in (path, cli) if s]
    if pieces:
        p = WrapLabel("  ·  ".join(pieces))
        p.setProperty("class", "key_path")
        v.addWidget(p)
    w.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
    return w
