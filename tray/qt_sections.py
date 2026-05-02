"""Per-section widget builders for the settings window.

Each `build_<id>(window)` returns a QWidget. The window holds a cache so
sections are only built once. If a section defines a `refresh()` method,
the window calls it every 1s for live status.

Sections call helpers for settings patch + async dispatch.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QGridLayout, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QLineEdit, QSpinBox,
)

from tray.qt_widgets import Toggle, NumberEditor, row_label
from tray.qt_helpers import (
    read_settings, get_path, patch_settings, remove_path, schedule,
    humanize, format_protocol, build_status,
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
    head_l = QHBoxLayout(head)
    head_l.setContentsMargins(18, 14, 18, 14)
    head_l.setSpacing(10)
    t = QLabel(title)
    t.setProperty("class", "card_title")
    head_l.addWidget(t)
    if sub:
        s = QLabel(sub)
        s.setProperty("class", "card_sub")
        head_l.addWidget(s)
    head_l.addStretch(1)
    outer.addWidget(head)

    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setStyleSheet(f"color: {BORDER};")
    outer.addWidget(sep)

    body = QWidget()
    body_l = QVBoxLayout(body)
    body_l.setContentsMargins(18, 14, 18, 14)
    body_l.setSpacing(12)
    outer.addWidget(body)
    return card, body_l


def _section_header(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("class", "section_header")
    return lbl


def _row(left: QWidget, right: QWidget) -> QWidget:
    """Two-column row: label | control."""
    w = QWidget()
    l = QHBoxLayout(w)
    l.setContentsMargins(0, 0, 0, 0)
    l.setSpacing(14)
    left.setFixedWidth(280)
    l.addWidget(left, 0, Qt.AlignmentFlag.AlignTop)
    l.addWidget(right, 1)
    return w


def _toggle_row(path: str, label: str, help_text: str = "",
                enabled_fn: Callable[[], bool] | None = None) -> QWidget:
    """Boolean toggle row. Writes settings.json + config.reload on change."""
    t = Toggle()
    t.setChecked(bool(get_path(read_settings(), path, False)))
    if enabled_fn:
        t.setEnabled(enabled_fn())

    def _on(_state: int) -> None:
        patch_settings(path, t.isChecked())
    t.stateChanged.connect(_on)

    return _row(row_label(label, help_text, path), _wrap_align(t, Qt.AlignmentFlag.AlignLeft))


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
                help_text: str = "") -> QWidget:
    """Numeric row (text input + slider, linked)."""
    ne = NumberEditor(minimum, maximum, step, decimals, unit)
    cur = get_path(read_settings(), path, minimum)
    try:
        ne.setValue(float(cur))
    except (TypeError, ValueError):
        ne.setValue(float(minimum))
    ne.valueChanged.connect(lambda v: patch_settings(path, v if decimals > 0 else int(round(v))))
    return _row(row_label(label, help_text, path), ne)


def _enum_row(path: str, label: str, options: list[tuple[str, Any]],
              help_text: str = "") -> QWidget:
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
    return _row(row_label(label, help_text, path), _wrap_align(cb, Qt.AlignmentFlag.AlignLeft))


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
    grid.setHorizontalSpacing(14)
    grid.setVerticalSpacing(14)
    grid_body.addLayout(grid)

    # 5 tiles in a responsive 3-col grid: llama | proxy | docgraph / mcp | sessions | (empty)
    specs = [
        ("llama",    "llama.cpp"),
        ("proxy",    "Proxy"),
        ("docgraph", "DocGraph"),
        ("mcp",      "MCP"),
        ("sessions", "Sessions"),
    ]
    tiles: dict[str, _StatusTile] = {}
    for i, (key, label) in enumerate(specs):
        tile = _StatusTile(label)
        tiles[key] = tile
        grid.addWidget(tile, i // 3, i % 3)

    # Stretch the empty trailing column to keep tiles equal width.
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

    scroll.refresh = refresh  # type: ignore[attr-defined]
    refresh()
    return scroll


# ── Status tile widget ────────────────────────────────────────────────────

class _StatusTile(QFrame):
    """One status card. Top color stripe + title + big value + sub text +
    a slot for an optional visualization (progress bar / chip strip / dots)."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.setStyleSheet(
            f"_StatusTile {{ background: {BG_CARD}; border: 1px solid {BORDER}; "
            f"border-radius: 8px; }}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Top accent stripe (4px)
        self._stripe = QFrame()
        self._stripe.setFixedHeight(3)
        self._stripe.setStyleSheet(f"background: {FG_MUTE}; border-top-left-radius: 8px; border-top-right-radius: 8px;")
        outer.addWidget(self._stripe)

        # Body padding
        body_w = QWidget()
        body = QVBoxLayout(body_w)
        body.setContentsMargins(14, 12, 14, 12)
        body.setSpacing(4)
        outer.addWidget(body_w)

        self._title = QLabel(title)
        self._title.setStyleSheet(
            f"color: {FG_MUTE}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;"
        )
        body.addWidget(self._title)

        self._value = QLabel("—")
        self._value.setStyleSheet(f"color: {FG}; font-size: 18px; font-weight: 500;")
        body.addWidget(self._value)

        self._sub = QLabel("")
        self._sub.setStyleSheet(f"color: {FG_DIM}; font-size: 12px;")
        self._sub.setWordWrap(True)
        body.addWidget(self._sub)

        # Visualization slot — populated by per-section refreshers.
        self._viz_host = QWidget()
        viz_layout = QVBoxLayout(self._viz_host)
        viz_layout.setContentsMargins(0, 6, 0, 0)
        viz_layout.setSpacing(4)
        body.addWidget(self._viz_host)

        body.addStretch(1)

    def set_state(self, state: str) -> None:
        """state ∈ {'ok','warn','err','mute'} — drives the top stripe color."""
        color = {"ok": OK, "warn": WARN, "err": ERR, "mute": FG_MUTE}.get(state, FG_MUTE)
        self._stripe.setStyleSheet(
            f"background: {color}; border-top-left-radius: 8px; border-top-right-radius: 8px;"
        )

    def set_value(self, text: str) -> None:
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

    if n_roots:
        tile.set_viz(_make_dots(n_roots, n_watch if alive else 0, on_color=OK, off_color=ACCENT))
    elif err and not alive:
        tile.set_viz(None)
    else:
        tile.set_viz(None)


# ══════════════════════════════════════════════════════════════════════
# llama.cpp
# ══════════════════════════════════════════════════════════════════════

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
    def _refresh_models() -> None:
        model_box.blockSignals(True)
        model_box.clear()
        models = list(get_path(read_settings(), "llamacpp.models", {}) or {})
        from process import _SUPERVISOR as sup
        active = sup.active_model() if sup else ""
        for m in models:
            model_box.addItem(m, m)
        if active in models:
            model_box.setCurrentIndex(models.index(active))
        model_box.blockSignals(False)
    _refresh_models()

    def _on_model_chosen(_i: int) -> None:
        m = model_box.currentData()
        if not m:
            return
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
            await sup.ensure_model(cfg.default_model())
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
    spawn_body.addWidget(_enum_row("llamacpp.split_mode",       "Split Mode",
                                    [("Layer (default)", "layer"),
                                     ("Row",             "row"),
                                     ("None",            "none")],
                                    "--split-mode: how layers are sharded across GPUs."))
    spawn_body.addWidget(_line_row("llamacpp.numa",             "NUMA Strategy",
                                    "distribute / isolate / numactl",
                                    "--numa. Empty = no NUMA tweaks."))
    spawn_body.addWidget(_number_row("llamacpp.seed",           "Seed",             -1, 2147483647, 1, 0, "",
                                      "--seed: -1 = random."))
    spawn_body.addWidget(_number_row("llamacpp.keep",           "Keep Tokens",       0, 8192, 32, 0, "tok",
                                      "--keep: tokens from prompt always kept when truncating."))
    layout.addWidget(spawn_card)

    # Caching policy card — server-wide
    cache_card, cache_body = _card("Caching",
                                    "llamacpp.* — KV cache & slot / checkpoint policy")
    cache_body.addWidget(_toggle_row("llamacpp.kv_offload",     "KV Offload (GPU)",
                                      "--kv-offload / --no-kv-offload: keep KV cache on GPU. Toggle off to stay on CPU."))
    cache_body.addWidget(_toggle_row("llamacpp.kv_unified",     "Unified KV",
                                      "--kv-unified / --no-kv-unified: single KV buffer shared across all slots."))
    cache_body.addWidget(_toggle_row("llamacpp.cache_prompt",   "Cache Prompt",
                                      "--cache-prompt / --no-cache-prompt: reuse KV across requests with shared prefixes."))
    cache_body.addWidget(_toggle_row("llamacpp.clear_idle",     "Clear Idle Slots",
                                      "--clear-idle / --no-clear-idle: save and clear idle slots when a new task arrives."))
    cache_body.addWidget(_number_row("llamacpp.cache_ram",      "Cache RAM Ceiling", 0, 524288, 128, 0, "MiB",
                                      "-cram, --cache-ram: maximum host-memory cache size. 0 = unset."))
    cache_body.addWidget(_toggle_row("llamacpp.swa_full",       "SWA Full Cache",
                                      "--swa-full: allocate full-size SWA (sliding-window attention) cache."))
    cache_body.addWidget(_number_row("llamacpp.ctx_checkpoints",           "Ctx Checkpoints",          0, 256, 1, 0, "",
                                      "--ctx-checkpoints: max number of context checkpoints per slot."))
    cache_body.addWidget(_number_row("llamacpp.checkpoint_every_n_tokens", "Checkpoint Every N",       0, 100000, 256, 0, "tok",
                                      "--checkpoint-every-n-tokens: create a checkpoint during prefill every N tokens."))
    cache_body.addWidget(_number_row("llamacpp.defrag_thold",   "Defrag Threshold",  0.0, 1.0, 0.05, 2, "",
                                      "--defrag-thold (deprecated). Fragmentation ratio above which KV is defragged."))
    cache_body.addWidget(_line_row("llamacpp.slot_save_path",   "Slot Save Path",
                                    "./data/slots",
                                    "--slot-save-path: directory for /slots/save & /slots/restore."))
    layout.addWidget(cache_card)

    # Speculative decoding card — server-wide algorithm, per-model draft pair
    spec_card, spec_body = _card("Speculative Decoding",
                                  "llamacpp.* — algorithm choice & tuning. Draft model lives per-model.")
    spec_body.addWidget(_enum_row("llamacpp.spec_type", "Spec Type",
                                   [("(auto — default)",         ""),
                                    ("None (disable)",           "none"),
                                    ("N-gram Simple (prompt-lookup)", "ngram-simple"),
                                    ("N-gram Map-K",             "ngram-map-k"),
                                    ("N-gram Map-K4V",           "ngram-map-k4v"),
                                    ("N-gram Mod",               "ngram-mod"),
                                    ("N-gram Cache (on-disk)",   "ngram-cache")],
                                   "--spec-type. Auto = server picks based on whether a draft model is set. "
                                   "ngram-cache is the only mode that reads --lookup-cache-* files."))
    spec_body.addWidget(_number_row("llamacpp.spec_ngram_size_n",   "N-gram N",            0, 16, 1, 0, "",
                                     "--spec-ngram-size-n: lookup key length. 0 = default."))
    spec_body.addWidget(_number_row("llamacpp.spec_ngram_size_m",   "N-gram M",            0, 16, 1, 0, "",
                                     "--spec-ngram-size-m: draft length per match. 0 = default."))
    spec_body.addWidget(_number_row("llamacpp.spec_ngram_min_hits", "N-gram Min Hits",     0, 64, 1, 0, "",
                                     "--spec-ngram-min-hits: minimum match frequency for map-based modes."))
    spec_body.addWidget(_number_row("llamacpp.threads_draft",       "Threads (draft)",     0, 128, 1, 0, "",
                                     "--threads-draft: CPU threads for draft-model generation. 0 = match --threads."))
    spec_body.addWidget(_number_row("llamacpp.threads_batch_draft", "Threads (draft batch)", 0, 128, 1, 0, "",
                                     "--threads-batch-draft: CPU threads for draft-model prompt processing."))
    layout.addWidget(spec_card)

    # Sampling card
    samp, samp_body = _card("Sampling defaults")
    samp_body.addWidget(_number_row("llamacpp.inference.temperature",       "Temperature",       0.0, 1.5, 0.05, 2))
    samp_body.addWidget(_number_row("llamacpp.inference.top_p",             "Top-P",             0.0, 1.0, 0.01, 2))
    samp_body.addWidget(_number_row("llamacpp.inference.top_k",             "Top-K",             0,   200, 1,    0))
    samp_body.addWidget(_number_row("llamacpp.inference.min_p",             "Min-P",             0.0, 1.0, 0.01, 2))
    samp_body.addWidget(_number_row("llamacpp.inference.repeat_penalty",    "Repeat Penalty",    0.5, 2.0, 0.01, 2))
    samp_body.addWidget(_number_row("llamacpp.inference.presence_penalty",  "Presence Penalty",  0.0, 2.0, 0.05, 2))
    samp_body.addWidget(_number_row("llamacpp.inference.frequency_penalty", "Frequency Penalty", 0.0, 2.0, 0.05, 2))
    samp_body.addWidget(_number_row("llamacpp.inference.max_tokens",        "Max Tokens",       -1,  1048576, 64, 0, "tok",
                                      "Hard cap on generated tokens. -1 = unlimited / model-default."))
    samp_body.addWidget(_list_row("llamacpp.inference.stop", "Stop Strings",
                                   "Generation halts when any of these appears (one per line).",
                                   "</s>"))
    samp_body.addWidget(_enum_row("llamacpp.inference.context_overflow", "Context Overflow",
                                   [("Truncate Middle", "truncate_middle"),
                                    ("Truncate Left",   "truncate_left"),
                                    ("Truncate Right",  "truncate_right"),
                                    ("Error",           "error")],
                                   "What to do when prompt exceeds ctx_size."))
    layout.addWidget(samp)

    # Structured output card
    so_card, so_body = _card("Structured Output", "llamacpp.inference.structured_output.* — JSON schema / GBNF grammar")
    so_body.addWidget(_toggle_row("llamacpp.inference.structured_output.enabled",
                                   "Enabled",
                                   "Force JSON schema or GBNF grammar on every generation."))
    so_body.addWidget(_json_row("llamacpp.inference.structured_output.schema",
                                 "JSON Schema", default=None, height=140,
                                 help_text="JSON Schema object — overrides response shape via response_format."))
    so_body.addWidget(_code_row("llamacpp.inference.structured_output.grammar",
                                 "GBNF Grammar", "(empty)",
                                 "Inline GBNF — alternative to JSON Schema.",
                                 height=180, highlighter=_GbnfHighlighter))
    layout.addWidget(so_card)

    # Reasoning card
    rcard, rbody = _card("Reasoning")
    # "Use reasoning" is INVERSE of disable_thinking
    use_t = Toggle()
    use_t.setChecked(not bool(get_path(read_settings(), "llamacpp.inference.disable_thinking", False)))
    def _on_use(_s: int) -> None:
        patch_settings("llamacpp.inference.disable_thinking", not use_t.isChecked())
    use_t.stateChanged.connect(_on_use)
    rbody.addWidget(_row(row_label("Use Reasoning",
                                    "Off → inject chat_template_kwargs.enable_thinking=false (Qwen3.5)."),
                          _wrap_align(use_t, Qt.AlignmentFlag.AlignLeft)))
    rbody.addWidget(_toggle_row("llamacpp.inference.reasoning.enabled",
                                 "Parse <think> Blocks",
                                 "Split <think>…</think> out of the text stream."))
    rbody.addWidget(_toggle_row("llamacpp.inference.reasoning.emit_thinking_blocks",
                                 "Show Reasoning In Output",
                                 "Forward parsed thinking as Anthropic thinking content blocks."))
    rbody.addWidget(_toggle_row("llamacpp.inference.drop_prior_thinking",
                                 "Drop Prior-Turn Thinking",
                                 "On (default): strip thinking blocks from prior assistant turns before sending upstream — llama.cpp regenerates. Off: reinject as <think>...</think> for trained-adaptive models that need prior reasoning for multi-turn coherence."))
    layout.addWidget(rcard)

    # ── Reasoning Effort Map card ────────────────────────────────────
    from PySide6.QtWidgets import QInputDialog as _QInputDialog, QMessageBox as _QMessageBox
    from tray.qt_theme import BG_ELEV as _BG_ELEV_MAP

    map_card, map_body = _card("Reasoning Effort Map",
        "Per-request effort presets — selected when the client sends "
        "reasoning_effort: \"low\"/\"medium\"/... Merged into chat_template_kwargs.")

    rows_host = QWidget()
    rows_layout = QVBoxLayout(rows_host)
    rows_layout.setContentsMargins(0, 0, 0, 0)
    rows_layout.setSpacing(8)
    map_body.addWidget(rows_host)

    add_bar = QWidget()
    ab = QHBoxLayout(add_bar)
    ab.setContentsMargins(0, 4, 0, 0)
    ab.setSpacing(8)
    add_key_edit = QLineEdit()
    add_key_edit.setPlaceholderText("preset key (e.g. ultra)")
    add_key_edit.setMaximumWidth(200)
    add_preset_btn = QPushButton("+ Add Preset")
    add_preset_btn.setProperty("class", "primary")
    ab.addWidget(add_key_edit)
    ab.addWidget(add_preset_btn)
    ab.addStretch(1)
    map_body.addWidget(add_bar)

    _ENABLE_OPTIONS = [("(default)", None), ("on", True), ("off", False)]

    def _build_effort_row(key: str, data: dict) -> QWidget:
        ek = key.replace(".", r"\.")
        base = f"llamacpp.inference.reasoning_effort_map.{ek}"

        row = QFrame()
        row.setStyleSheet(
            f"QFrame {{ background: {_BG_ELEV_MAP}; border: 1px solid {BORDER};"
            f" border-radius: 6px; }}"
        )
        outer = QVBoxLayout(row)
        outer.setContentsMargins(10, 8, 10, 10)
        outer.setSpacing(6)

        head_w = QWidget()
        head_l = QHBoxLayout(head_w)
        head_l.setContentsMargins(0, 0, 0, 0)
        head_l.setSpacing(8)
        chev = QPushButton("▶")
        chev.setFlat(True)
        chev.setCursor(Qt.CursorShape.PointingHandCursor)
        chev.setFixedWidth(22)
        chev.setStyleSheet(
            f"QPushButton {{ color: {FG_DIM}; border: none; background: transparent;"
            f" padding: 0; font-size: 11px; }}"
            f" QPushButton:hover {{ color: {FG}; }}"
        )
        head = QLabel(f"<b style='color:{FG}'>{key}</b>")
        head.setTextFormat(Qt.TextFormat.RichText)
        head.setCursor(Qt.CursorShape.PointingHandCursor)
        rm_btn = QPushButton("Remove")
        rm_btn.setProperty("class", "danger")
        rm_btn.setMaximumWidth(90)
        head_l.addWidget(chev)
        head_l.addWidget(head, 1)
        head_l.addWidget(rm_btn)
        outer.addWidget(head_w)

        body = QWidget()
        body.setStyleSheet("QWidget { background: transparent; border: none; }")
        rl = QGridLayout(body)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setHorizontalSpacing(10)
        rl.setVerticalSpacing(6)
        outer.addWidget(body)

        # thinking_budget_tokens
        tbt_lbl = QLabel("Budget Tokens"); tbt_lbl.setStyleSheet(f"color: {FG_DIM};")
        tbt = NumberEditor(0, 262144, 256, 0, "tok")
        tbt.setValue(float(data.get("thinking_budget_tokens", 0) or 0))
        tbt.valueChanged.connect(
            lambda v, b=base: patch_settings(f"{b}.thinking_budget_tokens", int(v))
        )
        rl.addWidget(tbt_lbl, 0, 0)
        rl.addWidget(tbt, 0, 1)

        # reasoning_effort (string; empty = field omitted)
        re_lbl = QLabel("Effort"); re_lbl.setStyleSheet(f"color: {FG_DIM};")
        re_edit = QLineEdit()
        re_edit.setText(str(data.get("reasoning_effort", "") or ""))
        re_edit.setPlaceholderText("low / medium / high (blank = omit)")
        def _commit_effort(b=base, e=re_edit):
            val = e.text().strip()
            if val:
                patch_settings(f"{b}.reasoning_effort", val)
            else:
                remove_path(f"{b}.reasoning_effort")
        re_edit.editingFinished.connect(_commit_effort)
        rl.addWidget(re_lbl, 1, 0)
        rl.addWidget(re_edit, 1, 1)

        # enable_thinking — tri-state (default / on / off)
        et_lbl = QLabel("Enable Thinking"); et_lbl.setStyleSheet(f"color: {FG_DIM};")
        et_combo = QComboBox()
        for disp, _val in _ENABLE_OPTIONS:
            et_combo.addItem(disp)
        cur = data.get("enable_thinking", None)
        if "enable_thinking" not in data:
            et_combo.setCurrentIndex(0)
        elif bool(cur):
            et_combo.setCurrentIndex(1)
        else:
            et_combo.setCurrentIndex(2)

        def _commit_enable(_i, b=base, c=et_combo):
            disp, val = _ENABLE_OPTIONS[c.currentIndex()]
            if val is None:
                remove_path(f"{b}.enable_thinking")
            else:
                patch_settings(f"{b}.enable_thinking", bool(val))
        et_combo.currentIndexChanged.connect(_commit_enable)
        rl.addWidget(et_lbl, 2, 0)
        rl.addWidget(et_combo, 2, 1)
        rl.setColumnStretch(1, 1)

        body.setVisible(False)

        def _toggle(_=None, b=body, c=chev):
            new_state = not b.isVisible()
            b.setVisible(new_state)
            c.setText("▼" if new_state else "▶")
        chev.clicked.connect(_toggle)
        head.mousePressEvent = lambda ev, t=_toggle: t()

        def _on_remove(b=base, k=key):
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
        for k, v in emap.items():
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
    body.addWidget(_toggle_row("proxy.auto_load_tools", "Auto-Load Tool Schemas",
                                "First blind call to a deferred tool injects its schema automatically."))
    body.addWidget(_toggle_row("proxy.strip_reminders", "Strip System Reminders",
                                "Remove <system-reminder> blocks from message history before forwarding."))
    body.addWidget(_toggle_row("proxy.sort_tools", "Sort Tools Alphabetically",
                                "Sort body.tools by name before forwarding. Stabilises the prompt prefix when "
                                "a client reorders its tool list (cache-friendly), at the cost of overriding "
                                "any deliberate primacy ordering. Off by default."))
    body.addWidget(_toggle_row("proxy.debug", "Debug Logging",
                                "Dump full request/response JSON under data/logs/proxy_full_*.json."))

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
    body.addWidget(_list_row("proxy.core_tools", "Core Tools",
                              "Names that stay always-loaded for clients (one per line). "
                              "Everything else becomes deferred and goes through ToolSearch.",
                              "Bash"))
    body.addWidget(_list_row("proxy.cors_origins", "CORS Origins",
                              "Allowed origins for the proxy HTTP server (one per line).",
                              "https://example.com"))

    body.addWidget(_section_header("Model Mapping"))
    body.addWidget(_kv_row("proxy.model_mapping", "Aliases",
                            "Rewrites body.model on each request (one ALIAS=target per line). "
                            "Useful for tricking Claude/OpenAI clients into pointing at your local model.",
                            typed=False))
    layout.addWidget(master)

    # Client profiles — picker + per-profile editor
    layout.addWidget(_proxy_profiles_card())

    layout.addStretch(1)
    return scroll


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
            ("strip_reminders",      "Strip Reminders",     "Drop <system-reminder> blocks."),
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

        # System instruction
        form_layout.addWidget(_section_header("System Instruction"))
        si_le = QLineEdit(); si_le.setText(str(prof.get("system_instruction", "") or ""))
        si_le.setPlaceholderText("system.md / office.md / (empty)")
        si_le.editingFinished.connect(lambda: _patch("system_instruction", si_le.text() or None))
        form_layout.addWidget(_row(row_label("Instruction File",
                                              "Filename in proxy/instructions/. Empty = no injection."), si_le))

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

        form_layout.addWidget(_list_editor("inject_managed", "Inject Managed",
                                            "Managed tool names to inject for this client."))
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
    body.addWidget(_line_row("mcp_server.host", "Host", "127.0.0.1"))
    body.addWidget(_number_row("mcp_server.port", "Port", 1024, 65535, 1, 0))
    body.addWidget(_list_row("mcp_server.cors_origins", "CORS Origins",
                              "Allowed origins for the MCP HTTP server (one per line). Empty = no CORS.",
                              "https://example.com"))

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
                tw.addWidget(_line_row("mcp_server.stt_url", "  └─ Whisper Endpoint", "http://127.0.0.1:6600"))
                tw.addWidget(_enum_row_strs("voice.stt.model", "  └─ Whisper Model", _WHISPER_MODELS))
            elif nl == "tts":
                tw.addWidget(_line_row("mcp_server.tts_url", "  └─ Kokoro Endpoint", "http://127.0.0.1:6500"))

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

        for t in tools:
            name = t.get("name", "?")
            nl = name.lower()
            enabled = t.get("enabled", True)

            prefix = name.split("_", 1)[0] if "_" in name else ""
            in_group = prefix in group_prefixes
            if in_group and prefix not in seen_groups:
                seen_groups.add(prefix)
                hdr = QLabel(humanize(prefix))
                hdr.setStyleSheet(
                    f"color: {FG_MUTE}; font-size: 11px; "
                    f"text-transform: uppercase; letter-spacing: 0.05em; "
                    f"padding-top: 4px;"
                )
                rows_wrap.addWidget(hdr)

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
            rows_wrap.addWidget(_row(row_label(label_text, "", name),
                                      _wrap_align(t_widget, Qt.AlignmentFlag.AlignLeft)))

            if nl == "transcribe":
                rows_wrap.addWidget(_line_row("mcp_server.stt_url", "  └─ Whisper Endpoint", "http://127.0.0.1:6600"))
                rows_wrap.addWidget(_enum_row_strs("voice.stt.model", "  └─ Whisper Model", _WHISPER_MODELS))
            elif nl == "speak":
                rows_wrap.addWidget(_line_row("mcp_server.tts_url", "  └─ Kokoro Endpoint", "http://127.0.0.1:6500"))

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
    scroll.refresh = refresh  # type: ignore[attr-defined]
    refresh()
    return scroll


def _telegram(window) -> QWidget:
    scroll, _, layout = _page()

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
    layout.addStretch(1)
    return scroll


_WHISPER_MODELS = [
    ("Tiny (fastest)", "tiny"),
    ("Base", "base"),
    ("Small (default)", "small"),
    ("Medium", "medium"),
    ("Large v3 (best)", "large-v3"),
    ("Turbo", "distil-large-v3"),
    ("OpenAI (whisper-1)", "whisper-1"),
]


def _voice(window) -> QWidget:
    from voice.health import get_status as _voice_status

    scroll, _, layout = _page()
    card, body = _card("Voice")
    body.addWidget(_toggle_row("voice.stt.enabled", "STT Enabled",
                                "Auto-transcribe voice messages via a local Whisper endpoint."))

    # Live health pill — state is updated by real voice.stt.transcribe()
    # calls (no probe). Refreshed by the window's 1s tick.
    pill = QLabel("⚪ untested")
    pill.setProperty("class", "stat_pill")
    body.addWidget(_row(row_label("Health",
                                    "Reflects the outcome of the most recent transcribe request. "
                                    "No background probing — status only changes when a voice message is processed."),
                         _wrap_align(pill, Qt.AlignmentFlag.AlignLeft)))

    body.addWidget(_line_row("voice.stt.base_url", "Endpoint", "http://localhost:6600/v1"))
    body.addWidget(_enum_row_strs("voice.stt.model", "Model", _WHISPER_MODELS))

    # Test button
    from voice.stt import transcribe, HELLO_WORLD_AUDIO
    test_btn = QPushButton("Run Test")
    test_btn.setFixedWidth(80)
    test_btn.setProperty("class", "ghost")

    def run_test() -> None:
        test_btn.setEnabled(False)
        test_btn.setText("Testing...")

        async def _run() -> None:
            try:
                # Use a .wav filename since we provided a WAV header.
                # Use a shorter timeout (5s) for the UI test button.
                await transcribe(HELLO_WORLD_AUDIO, filename="test.wav", timeout=5.0)
                refresh()
            except Exception as e:
                print(f"STT Test Error: {e}")
            finally:
                test_btn.setEnabled(True)
                test_btn.setText("Run Test")

        schedule(window.bot_loop, _run())

    test_btn.clicked.connect(run_test)

    body.addWidget(_row(row_label("Test", "Send a sample 'Hello World' audio to verify the endpoint."),
                        _wrap_align(test_btn, Qt.AlignmentFlag.AlignLeft)))

    import config as _cfg
    def refresh() -> None:
        vs = _voice_status()
        print(f"DEBUG: Voice refresh, vs={vs}")
        if not vs.stt_configured:
            pill.setText("⚫ disabled")
            pill.setProperty("class", "stat_pill")
        elif not vs.stt_last_checked:
            pill.setText("⚪ untested")
            pill.setProperty("class", "stat_pill")
        elif vs.stt_reachable:
            pill.setText("🟢 reachable")
            pill.setProperty("class", "stat_pill stat_pill_ok")
        else:
            pill.setText("🔴 last call failed")
            pill.setProperty("class", "stat_pill stat_pill_err")
        pill.style().unpolish(pill); pill.style().polish(pill)
    scroll.refresh = refresh  # type: ignore[attr-defined]
    refresh()

    layout.addWidget(card)
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
        "docgraph_watch.log",  "docgraph_watch.log.prev",
        "docgraph_serve.log",  "docgraph_serve.log.prev",
        "docgraph_daemon.log", "docgraph_daemon.log.prev",
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

_MODEL_DEFAULTS: dict[str, Any] = {
    "path": "",
    "mmproj": "",
    "ctx_size": 4096,
    "n_gpu_layers": 0,
    "threads": 8,
    "batch_size": 2048,
    "ubatch_size": 512,
    "parallel": 1,
    "flash_attn": True,
    "cache_type_k": "f16",
    "cache_type_v": "f16",
    "mlock": False,
    "no_mmap": False,
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
    ("q4_1", "q4_1"), ("q4_0", "q4_0"),
]


def _line_row(path: str, label: str, placeholder: str = "", help_text: str = "") -> QWidget:
    """Free-text string row."""
    le = QLineEdit()
    le.setPlaceholderText(placeholder)
    le.setText(str(get_path(read_settings(), path, "") or ""))
    le.editingFinished.connect(lambda: patch_settings(path, le.text()))
    return _row(row_label(label, help_text, path), le)


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
    remove_btn = QPushButton("Remove")
    remove_btn.setProperty("class", "danger")
    set_default_btn = QPushButton("Set As Default")
    set_default_btn.setProperty("class", "ghost")
    default_lbl = QLabel(""); default_lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")
    top.addWidget(picker); top.addWidget(default_lbl); top.addStretch(1)
    top.addWidget(set_default_btn); top.addWidget(add_btn); top.addWidget(remove_btn)
    body.addLayout(top)

    # ── Form container ──────────────────────────────────────────────
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
        # Escape dots in the model name so get_path/patch_settings doesn't split it
        ek = key.replace(".", r"\.")
        p = f"llamacpp.models.{ek}"
        form_layout.addWidget(_section_header("Paths"))
        form_layout.addWidget(_line_row(f"{p}.path",   "GGUF Path",
                                         "D:/models/foo.gguf",
                                         "Absolute path to the model .gguf file."))
        form_layout.addWidget(_line_row(f"{p}.mmproj", "mmproj Path (vision)",
                                         "D:/models/mmproj.gguf",
                                         "Optional — only needed for vision-capable GGUFs (Qwen-VL etc)."))

        form_layout.addWidget(_section_header("Capacity"))
        form_layout.addWidget(_number_row(f"{p}.ctx_size",     "Context Size",       512, 1048576, 256, 0, "tok"))
        form_layout.addWidget(_number_row(f"{p}.n_gpu_layers", "GPU Layers",         0,   200,     1,   0, "",
                                           "Layers offloaded to GPU. Higher = faster, more VRAM."))
        form_layout.addWidget(_number_row(f"{p}.n_cpu_moe",    "CPU MoE Layers",     0,   200,     1,   0, "",
                                           "MoE experts kept on CPU. 0 = all on GPU."))

        form_layout.addWidget(_section_header("Context Fitting"))
        form_layout.addWidget(_toggle_row(f"{p}.fit",          "Fit Context",
                                           "--fit on: auto-shrink ctx_size to what the model + KV actually fits in available memory."))
        form_layout.addWidget(_number_row(f"{p}.fit_ctx",      "Fit Ctx Ceiling",    0,   2097152, 1024, 0, "tok",
                                           "--fit-ctx: max ctx the fitter is allowed to grow to. 0 = use ctx_size."))
        form_layout.addWidget(_number_row(f"{p}.fit_target",   "Fit Target Headroom", 0,  16384,   16,   0, "MB",
                                           "--fit-target: free VRAM/RAM (MB) to leave after fitting."))

        form_layout.addWidget(_section_header("Cache"))
        form_layout.addWidget(_enum_row_strs(f"{p}.cache_type_k", "Cache Type (K)", _CACHE_TYPES))
        form_layout.addWidget(_enum_row_strs(f"{p}.cache_type_v", "Cache Type (V)", _CACHE_TYPES))
        form_layout.addWidget(_number_row(f"{p}.cache_reuse",     "Cache Reuse",          0,   8192, 32, 0, "tok",
                                           "--cache-reuse: tokens to retain when reusing an existing slot."))

        form_layout.addWidget(_section_header("Flags"))
        form_layout.addWidget(_toggle_row(f"{p}.preload",       "Preload",
                                           "Load this model at telecode startup regardless of auto_start."))
        form_layout.addWidget(_toggle_row(f"{p}.flash_attn",    "Flash Attention"))
        form_layout.addWidget(_toggle_row(f"{p}.cpu_moe",       "CPU MoE (all experts)",
                                           "--cpu-moe: keep ALL MoE expert layers on CPU (overrides n_cpu_moe)."))
        form_layout.addWidget(_toggle_row(f"{p}.jinja",         "Jinja Chat Template",
                                           "Use the built-in tokenizer chat template (required for tools)."))
        form_layout.addWidget(_code_row(f"{p}.chat_template",   "Chat Template Override",
                                         "(empty = use model's built-in)",
                                         "--chat-template: override the GGUF's chat template by name "
                                         "or paste an inline jinja template.",
                                         height=200, highlighter=_JinjaHighlighter))

        form_layout.addWidget(_section_header("RoPE"))
        form_layout.addWidget(_line_row(f"{p}.rope_scaling",     "RoPE Scaling",
                                         "none / linear / yarn",
                                         "--rope-scaling. Empty = model default."))
        form_layout.addWidget(_number_row(f"{p}.rope_freq_base", "RoPE Freq Base",       0, 10000000, 1000, 0, "",
                                           "--rope-freq-base. 0 = model default."))
        form_layout.addWidget(_number_row(f"{p}.rope_freq_scale","RoPE Freq Scale",      0, 4.0, 0.05, 2, "",
                                           "--rope-freq-scale. 0 = model default."))
        form_layout.addWidget(_number_row(f"{p}.yarn_orig_ctx",  "YaRN Orig Ctx",        0, 1048576, 1024, 0, "tok",
                                           "--yarn-orig-ctx: original training context for YaRN scaling."))

        form_layout.addWidget(_section_header("Draft Model (Speculative)"))
        form_layout.addWidget(_line_row(f"{p}.draft_model", "Draft Model (GGUF)",
                                         "D:/models/draft-0.6b.gguf",
                                         "--model-draft: separate small LM for draft tokens. "
                                         "Leave empty + Spec Type=ngram-simple for prompt-lookup self-speculation."))
        form_layout.addWidget(_number_row(f"{p}.ctx_size_draft",     "Draft Ctx Size",   0, 1048576, 256, 0, "tok",
                                           "--ctx-size-draft: context size for the draft model. 0 = match main."))
        form_layout.addWidget(_number_row(f"{p}.n_gpu_layers_draft", "Draft GPU Layers", 0, 200, 1, 0, "",
                                           "--n-gpu-layers-draft / -ngld: layers of the draft model on GPU."))
        form_layout.addWidget(_enum_row_strs(f"{p}.cache_type_k_draft", "Draft Cache (K)", [("(default)", "")] + _CACHE_TYPES,
                                              "--cache-type-k-draft: K-cache dtype for the draft model."))
        form_layout.addWidget(_enum_row_strs(f"{p}.cache_type_v_draft", "Draft Cache (V)", [("(default)", "")] + _CACHE_TYPES,
                                              "--cache-type-v-draft: V-cache dtype for the draft model."))
        form_layout.addWidget(_line_row(f"{p}.device_draft",         "Draft Devices",
                                         "e.g. CUDA0,CUDA1",
                                         "--device-draft / -devd: comma-separated device list for draft offload."))
        form_layout.addWidget(_number_row(f"{p}.draft_n",     "Draft Max Tokens",  0, 32,   1,    0, "",
                                           "--draft-max: max draft tokens per step. 0 = disabled. Typical: 8."))
        form_layout.addWidget(_number_row(f"{p}.draft_n_min", "Draft Min Tokens",  0, 32,   1,    0, "",
                                           "--draft-min: minimum draft length before accepting. Typical: 0–2."))
        form_layout.addWidget(_number_row(f"{p}.draft_p_min", "Draft Min Probability", 0.0, 1.0, 0.05, 2, "",
                                           "--draft-p-min: reject draft tokens below this probability. "
                                           "Draft-model: 0.5–0.75. N-gram: 0.1."))
        form_layout.addWidget(_line_row(f"{p}.lookup_cache_static", "Lookup Cache (static)",
                                         "./data/lookup-static.bin",
                                         "--lookup-cache-static. Only used when Spec Type = ngram-cache. "
                                         "Precomputed via llama-lookup-create; read-only at runtime."))
        form_layout.addWidget(_line_row(f"{p}.lookup_cache_dynamic", "Lookup Cache (dynamic)",
                                         "./data/lookup-dyn.bin",
                                         "--lookup-cache-dynamic. Only loaded when Spec Type = ngram-cache. "
                                         "NOTE: llama-server does not persist writes — file will not be created or updated."))

        form_layout.addWidget(_section_header("Inference Defaults"))
        ip = f"{p}.inference_defaults"
        form_layout.addWidget(_number_row(f"{ip}.temperature",      "Temperature",      0.0, 1.5, 0.05, 2))
        form_layout.addWidget(_number_row(f"{ip}.top_p",            "Top-P",            0.0, 1.0, 0.01, 2))
        form_layout.addWidget(_number_row(f"{ip}.top_k",            "Top-K",            0,   200, 1,    0))
        form_layout.addWidget(_number_row(f"{ip}.min_p",            "Min-P",            0.0, 1.0, 0.01, 2))
        form_layout.addWidget(_number_row(f"{ip}.presence_penalty", "Presence Penalty", 0.0, 2.0, 0.05, 2))
        form_layout.addWidget(_number_row(f"{ip}.repeat_penalty",   "Repeat Penalty",   0.5, 2.0, 0.01, 2))

        form_layout.addWidget(_section_header("Reasoning"))
        rp = f"{ip}.reasoning"
        form_layout.addWidget(_toggle_row(f"{rp}.enabled",              "Parse <think> Blocks"))
        form_layout.addWidget(_line_row(f"{rp}.start",                  "Start Tag", "<think>"))
        form_layout.addWidget(_line_row(f"{rp}.end",                    "End Tag",   "</think>"))
        form_layout.addWidget(_toggle_row(f"{rp}.emit_thinking_blocks", "Emit Thinking Blocks"))

        form_layout.addWidget(_section_header("Chat Template Kwargs"))
        form_layout.addWidget(_kv_row(f"{ip}.chat_template_kwargs",
            "Kwargs",
            "Merged into every request's chat_template_kwargs. Values are "
            "JSON-parsed — use `enable_thinking=false`, `reasoning_effort=low`, "
            "`budget=4096`. Anything the model's jinja template reads.",
            typed=True))

        form_layout.addWidget(_section_header("LoRA"))
        form_layout.addWidget(_line_row(f"{p}.lora",        "LoRA Adapter (path)",
                                         "/path/to/adapter.gguf",
                                         "--lora: GGUF LoRA adapter file."))
        form_layout.addWidget(_number_row(f"{p}.lora_scale", "LoRA Scale",
                                           0.0, 4.0, 0.05, 2, "",
                                           "--lora-scaled: blend strength (1.0 = full)."))

        form_layout.addWidget(_section_header("Grammar"))
        form_layout.addWidget(_code_row(f"{p}.grammar",       "GBNF Grammar (inline)",
                                         "(empty)",
                                         "--grammar: inline GBNF for constrained decoding.",
                                         height=180, highlighter=_GbnfHighlighter))
        form_layout.addWidget(_line_row(f"{p}.grammar_file",  "Grammar File",
                                         "/path/to/grammar.gbnf",
                                         "--grammar-file: load GBNF from disk."))

        form_layout.addWidget(_section_header("Extra CLI Args"))
        form_layout.addWidget(_pair_list_row(f"{p}.extra_args", "Extra Args",
            'Per-model escape hatch — one [flag, value] pair per row. '
            'Top-level llamacpp.extra_args is also appended.'))

        form_layout.addWidget(_section_header("Per-Model Reasoning Override"))
        form_layout.addWidget(_number_row(f"{p}.reasoning_budget",        "Reasoning Budget",   -1, 1048576, 256, 0, "tok",
                                           "--reasoning-budget. -1 = unlimited, 0 = disable thinking."))
        form_layout.addWidget(_number_row(f"{p}.reasoning_budget_message","Reasoning Budget (per message)", -1, 1048576, 256, 0, "tok",
                                           "--reasoning-budget-message. Per-turn cap."))
        form_layout.addWidget(_line_row(f"{p}.reasoning_format",          "Reasoning Format",
                                         "deepseek / none",
                                         "--reasoning-format: how the server tags think blocks."))

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
        name, ok = QInputDialog.getText(content, "Add Model", "Model key (e.g. qwen3-30b):")
        if not ok:
            return
        name = name.strip()
        valid, err = _valid_key(name)
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
    "voice":    _voice,
    "computer": _computer,
    "sessions": _sessions,
    "requests": _requests,
    "logs":     _logs,
    "raw":      _raw,
}
