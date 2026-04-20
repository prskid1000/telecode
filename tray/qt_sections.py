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
    QHeaderView, QLineEdit,
)

from tray.qt_widgets import Toggle, NumberEditor, row_label
from tray.qt_helpers import (
    read_settings, get_path, patch_settings, remove_path, schedule,
    humanize, format_protocol, build_status,
)
from tray.qt_theme import FG, FG_DIM, FG_MUTE, BG, BG_CARD, BORDER, OK, ERR


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
    # AlwaysOn — AsNeeded was unreliable because widget-resizable mode
    # stretches content to viewport width so h-scroll never activated.
    # Explicit always-visible bars guarantee discoverability.
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
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
    left.setFixedWidth(240)
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

    tiles: dict[str, tuple[QLabel, QLabel]] = {}
    specs = [
        ("llama", "llama.cpp"),
        ("proxy", "Proxy"),
        ("mcp", "MCP"),
        ("sessions", "Sessions"),
    ]
    for i, (key, label) in enumerate(specs):
        tile = QFrame()
        tile.setStyleSheet(f"QFrame {{ background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 14px; }}")
        tl = QVBoxLayout(tile)
        tl.setSpacing(4)
        name = QLabel(label)
        name.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;")
        val = QLabel("—")
        val.setStyleSheet(f"color: {FG}; font-size: 18px; font-weight: 500;")
        sub = QLabel("")
        sub.setStyleSheet(f"color: {FG_DIM}; font-size: 12px;")
        tl.addWidget(name)
        tl.addWidget(val)
        tl.addWidget(sub)
        tiles[key] = (val, sub)
        grid.addWidget(tile, i // 2, i % 2)

    layout.addWidget(grid_card)
    layout.addStretch(1)

    def refresh() -> None:
        st = build_status()
        llama = st.get("llama", {})
        proxy = st.get("proxy", {})
        mcp = st.get("mcp", {})
        sessions = st.get("sessions", [])

        if llama.get("enabled"):
            if llama.get("alive"):
                tiles["llama"][0].setText(f"● {llama.get('active_model', '—')}")
                bits = []
                if llama.get("inflight", 0):
                    bits.append(f"{llama['inflight']} In-Flight")
                elif llama.get("idle_remaining_sec", 0) > 0:
                    bits.append(f"Auto-Unload In {int(llama['idle_remaining_sec'])}s")
                tiles["llama"][1].setText(" · ".join(bits) or "Ready")
            else:
                tiles["llama"][0].setText("○ Idle")
                tiles["llama"][1].setText("Loads On First Request")
        else:
            tiles["llama"][0].setText("○ Disabled")
            tiles["llama"][1].setText("")

        if proxy.get("enabled"):
            tiles["proxy"][0].setText(f"● :{proxy.get('port', '?')}")
            tiles["proxy"][1].setText(", ".join(format_protocol(p) for p in proxy.get("protocols", [])))
        else:
            tiles["proxy"][0].setText("○ Disabled")
            tiles["proxy"][1].setText("")

        if mcp.get("enabled"):
            tiles["mcp"][0].setText(f"● :{mcp.get('port', '?')}")
            tiles["mcp"][1].setText(f"{len(mcp.get('registered_tools', []))} Tools Registered")
        else:
            tiles["mcp"][0].setText("○ Disabled")
            tiles["mcp"][1].setText("")

        alive = sum(1 for s in sessions if s.get("alive"))
        tiles["sessions"][0].setText(f"{alive} / {len(sessions)}")
        tiles["sessions"][1].setText("Active / Total")

    scroll.refresh = refresh  # type: ignore[attr-defined]
    refresh()
    return scroll


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

    # Sampling card
    samp, samp_body = _card("Sampling defaults")
    samp_body.addWidget(_number_row("llamacpp.inference.temperature",      "Temperature",     0.0, 1.5, 0.05, 2))
    samp_body.addWidget(_number_row("llamacpp.inference.top_p",            "Top-P",           0.0, 1.0, 0.01, 2))
    samp_body.addWidget(_number_row("llamacpp.inference.top_k",            "Top-K",           0,   200, 1,    0))
    samp_body.addWidget(_number_row("llamacpp.inference.min_p",            "Min-P",           0.0, 1.0, 0.01, 2))
    samp_body.addWidget(_number_row("llamacpp.inference.repeat_penalty",   "Repeat Penalty",  0.5, 2.0, 0.01, 2))
    samp_body.addWidget(_number_row("llamacpp.inference.presence_penalty", "Presence Penalty", 0.0, 2.0, 0.05, 2))
    layout.addWidget(samp)

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
    layout.addWidget(rcard)

    layout.addStretch(1)

    # Refresh on timer (models list may change from settings.json edit)
    def refresh() -> None:
        _refresh_models()
        from process import _SUPERVISOR as sup
        alive = bool(sup and sup.alive())
        load_btn.setEnabled(not alive)
        unload_btn.setEnabled(alive)
        restart_btn.setEnabled(alive)
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
    body.addWidget(_toggle_row("proxy.debug", "Debug Logging",
                                "Dump full request/response JSON under data/logs/proxy_full_*.json."))

    body.addWidget(_section_header("Limits"))
    body.addWidget(_number_row("proxy.max_roundtrips", "Max Round-Trips",
                                1, 50, 1, 0, "",
                                "How many intercept/tool rounds before giving up per request."))
    body.addWidget(_number_row("proxy.ping_interval", "Ping Interval",
                                1, 60, 1, 0, "s",
                                "Anthropic `event: ping` frame cadence during long generations."))
    layout.addWidget(master)

    layout.addStretch(1)
    return scroll


# ══════════════════════════════════════════════════════════════════════
# MCP / Managed / Telegram / Voice / Computer / Sessions / Logs
# ══════════════════════════════════════════════════════════════════════

def _mcp(window) -> QWidget:
    scroll, _, layout = _page()
    card, body = _card("MCP Server")
    body.addWidget(_toggle_row("mcp_server.enabled", "Enabled",
                                "Streamable HTTP MCP server for external clients. Restart required."))
    body.addWidget(_section_header("Registered Tools"))
    tools_wrap = QWidget()
    tw = QVBoxLayout(tools_wrap)
    tw.setContentsMargins(0, 0, 0, 0)
    tw.setSpacing(4)
    body.addWidget(tools_wrap)
    layout.addWidget(card)
    layout.addStretch(1)

    def refresh() -> None:
        for i in reversed(range(tw.count())):
            w = tw.itemAt(i).widget()
            if w:
                w.deleteLater()
        st = build_status().get("mcp", {})
        tools = st.get("registered_tools", [])
        if not tools:
            l = QLabel("—  None")
            l.setStyleSheet(f"color: {FG_MUTE};")
            tw.addWidget(l)
            return
        for name in tools:
            l = QLabel(f"·  {humanize(name)}")
            l.setStyleSheet(f"color: {FG_DIM};")
            tw.addWidget(l)
    scroll.refresh = refresh  # type: ignore[attr-defined]
    refresh()
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

    def refresh() -> None:
        for i in reversed(range(rows_wrap.count())):
            w = rows_wrap.itemAt(i).widget()
            if w:
                w.deleteLater()
        tools = build_status().get("managed", [])
        if not tools:
            l = QLabel("No managed tools registered.")
            l.setStyleSheet(f"color: {FG_MUTE};")
            rows_wrap.addWidget(l)
            return
        from proxy.runtime_state import set_tool
        for t in tools:
            name = t.get("name", "?")
            enabled = t.get("enabled", True)
            t_widget = Toggle()
            t_widget.setChecked(enabled)
            def _toggle(_s: int, n=name, tw=t_widget) -> None:
                set_tool("managed_tools", n, tw.isChecked())
            t_widget.stateChanged.connect(_toggle)
            rows_wrap.addWidget(_row(row_label(humanize(name), "", name),
                                      _wrap_align(t_widget, Qt.AlignmentFlag.AlignLeft)))
    scroll.refresh = refresh  # type: ignore[attr-defined]
    refresh()
    return scroll


def _telegram(window) -> QWidget:
    scroll, _, layout = _page()
    stream_card, sb = _card("Streaming", "Telegram message edit + PTY flush tuning")
    sb.addWidget(_number_row("streaming.interval_sec",       "Edit Interval",        0.3, 3.0, 0.1, 1, "s"))
    sb.addWidget(_number_row("streaming.max_message_length", "Max Message Length",   500, 4096, 100, 0))
    sb.addWidget(_number_row("streaming.idle_timeout_sec",   "Session Idle Timeout", 60, 86400, 60, 0, "s"))
    sb.addWidget(_number_row("streaming.idle_sec",           "PTY Idle Threshold",   0.3, 10.0, 0.1, 1, "s"))
    sb.addWidget(_number_row("streaming.max_wait_sec",       "PTY Max Wait",         1.0, 30.0, 0.5, 1, "s"))
    layout.addWidget(stream_card)

    cap_card, cb = _card("Capture", "Screen image / video intervals")
    cb.addWidget(_number_row("capture.image_interval", "Image Interval", 1, 300, 1, 0, "s"))
    cb.addWidget(_number_row("capture.video_interval", "Video Chunk",    10, 600, 10, 0, "s"))
    layout.addWidget(cap_card)
    layout.addStretch(1)
    return scroll


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
    url_lbl = QLabel("")
    url_lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")
    body.addWidget(_row(row_label("Health",
                                    "Reflects the outcome of the most recent transcribe request. "
                                    "No background probing — status only changes when a voice message is processed."),
                         _wrap_align(pill, Qt.AlignmentFlag.AlignLeft)))
    body.addWidget(_row(row_label("Endpoint", ""), url_lbl))

    import config as _cfg
    def refresh() -> None:
        vs = _voice_status()
        url_lbl.setText(_cfg.stt_base_url())
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
    body.addWidget(_enum_row("tools.computer.api.format", "API Format",
                              [("OpenAI", "openai"), ("Anthropic", "anthropic")]))
    body.addWidget(_number_row("tools.computer.capture_interval", "Capture Interval", 1, 30, 1, 0, "s"))
    body.addWidget(_number_row("tools.computer.max_history",      "Max History",      5, 100, 5, 0))
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
        "tray-bot.stderr.log",
    ]
    MAX_TAIL_BYTES = 512 * 1024  # last ~512 KB is plenty for UI

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
    for n in LOG_FILES:
        picker.addItem(n)
    picker.setMinimumWidth(200)

    size_label = QLabel("—")
    size_label.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")

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
        return _sp().parent / "data" / "logs" / name

    def _human_bytes(n: int) -> str:
        size: float = float(n)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

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
        # preserve scroll unless follow is on
        at_bottom = follow_cb.isChecked() or (
            viewer.verticalScrollBar().value() >= viewer.verticalScrollBar().maximum() - 2
        )
        cursor = viewer.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
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
    "ubatch_size": 512,
    "parallel": 1,
    "flash_attn": True,
    "cache_type_k": "f16",
    "cache_type_v": "f16",
    "mlock": False,
    "no_mmap": False,
    "n_cpu_moe": 0,
    "jinja": True,
    "inference_defaults": {
        "temperature": 0.7,
        "top_p": 0.95,
        "top_k": 40,
        "presence_penalty": 0.0,
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
        p = f"llamacpp.models.{key}"
        form_layout.addWidget(_section_header("Paths"))
        form_layout.addWidget(_line_row(f"{p}.path",   "GGUF Path",
                                         "D:/models/foo.gguf",
                                         "Absolute path to the model .gguf file."))
        form_layout.addWidget(_line_row(f"{p}.mmproj", "mmproj Path (vision)",
                                         "D:/models/mmproj.gguf",
                                         "Optional — only needed for vision-capable GGUFs (Qwen-VL etc)."))

        form_layout.addWidget(_section_header("Capacity / Compute"))
        form_layout.addWidget(_number_row(f"{p}.ctx_size",     "Context Size",       512, 1048576, 256, 0, "tok"))
        form_layout.addWidget(_number_row(f"{p}.n_gpu_layers", "GPU Layers",         0,   200,     1,   0, "",
                                           "Layers offloaded to GPU. Higher = faster, more VRAM."))
        form_layout.addWidget(_number_row(f"{p}.n_cpu_moe",    "CPU MoE Layers",     0,   200,     1,   0, "",
                                           "MoE experts kept on CPU. 0 = all on GPU."))
        form_layout.addWidget(_number_row(f"{p}.threads",      "Threads",            1,   128,     1,   0))
        form_layout.addWidget(_number_row(f"{p}.ubatch_size",  "Micro-Batch Size",   32,  8192,    32,  0, "tok"))
        form_layout.addWidget(_number_row(f"{p}.parallel",     "Parallel Slots",     1,   32,      1,   0))

        form_layout.addWidget(_section_header("Cache"))
        form_layout.addWidget(_enum_row_strs(f"{p}.cache_type_k", "Cache Type (K)", _CACHE_TYPES))
        form_layout.addWidget(_enum_row_strs(f"{p}.cache_type_v", "Cache Type (V)", _CACHE_TYPES))

        form_layout.addWidget(_section_header("Flags"))
        form_layout.addWidget(_toggle_row(f"{p}.flash_attn", "Flash Attention"))
        form_layout.addWidget(_toggle_row(f"{p}.mlock",      "Mlock"))
        form_layout.addWidget(_toggle_row(f"{p}.no_mmap",    "No mmap"))
        form_layout.addWidget(_toggle_row(f"{p}.jinja",      "Jinja Chat Template",
                                           "Use the built-in tokenizer chat template (required for tools)."))

        form_layout.addWidget(_section_header("Inference Defaults"))
        ip = f"{p}.inference_defaults"
        form_layout.addWidget(_number_row(f"{ip}.temperature",      "Temperature",      0.0, 1.5, 0.05, 2))
        form_layout.addWidget(_number_row(f"{ip}.top_p",            "Top-P",            0.0, 1.0, 0.01, 2))
        form_layout.addWidget(_number_row(f"{ip}.top_k",            "Top-K",            0,   200, 1,    0))
        form_layout.addWidget(_number_row(f"{ip}.presence_penalty", "Presence Penalty", 0.0, 2.0, 0.05, 2))

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
        patch_settings(f"llamacpp.models.{name}", copy.deepcopy(_MODEL_DEFAULTS))
        _refresh_picker(preserve_key=name)

    def _on_remove():
        key = picker.currentData()
        if not key:
            return
        if QMessageBox.question(content, "Remove", f"Delete model '{key}'?") != QMessageBox.StandardButton.Yes:
            return
        remove_path(f"llamacpp.models.{key}")
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
              placeholder: str = "one per line") -> QWidget:
    """List-of-strings editor (newline-separated, debounced)."""
    from PySide6.QtWidgets import QPlainTextEdit
    te = QPlainTextEdit()
    te.setPlaceholderText(placeholder)
    te.setFixedHeight(72)
    cur = get_path(read_settings(), path, []) or []
    te.setPlainText("\n".join(str(x) for x in cur))
    def _commit():
        vals = [l for l in te.toPlainText().splitlines() if l.strip() != ""]
        patch_settings(path, vals)
    _debounced_commit(te, _commit)
    return _row(row_label(label, help_text, path), te)


def _kv_row(path: str, label: str, help_text: str = "",
            typed: bool = False) -> QWidget:
    """Key=value dict editor (one `K=V` per line, debounced).

    typed=True: values go through JSON parsing — so `enable_thinking=false`
    becomes {"enable_thinking": false} (bool), `budget=4096` becomes int,
    `voice=alloy` stays string. Needed for places like chat_template_kwargs
    where downstream jinja templates distinguish `false` from `"false"`."""
    import json as _json
    from PySide6.QtWidgets import QPlainTextEdit
    te = QPlainTextEdit()
    te.setPlaceholderText("KEY=value")
    te.setFixedHeight(120)
    cur = get_path(read_settings(), path, {}) or {}

    def _stringify(v: Any) -> str:
        if typed and not isinstance(v, str):
            try:
                return _json.dumps(v)
            except Exception:
                return str(v)
        return str(v)

    te.setPlainText("\n".join(f"{k}={_stringify(v)}" for k, v in cur.items()))

    def _parse(v: str) -> Any:
        if not typed:
            return v
        v = v.strip()
        try:
            return _json.loads(v)
        except Exception:
            return v  # fall back to raw string

    def _commit():
        out: dict[str, Any] = {}
        for line in te.toPlainText().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                if k:
                    out[k] = _parse(v)
        patch_settings(path, out)
    _debounced_commit(te, _commit)
    return _row(row_label(label, help_text, path), te)


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

    def _refresh_picker(preserve_key: str | None = None):
        picker.blockSignals(True)
        picker.clear()
        for k in list(get_path(read_settings(), "tools", {}) or {}):
            display = k
            nm = get_path(read_settings(), f"tools.{k}.name", "") or humanize(k)
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
        patch_settings(f"tools.{name}", default)
        _refresh_picker(preserve_key=name)

    def _on_remove():
        key = picker.currentData()
        if not key:
            return
        if QMessageBox.question(content, "Remove", f"Delete tool '{key}'?") != QMessageBox.StandardButton.Yes:
            return
        remove_path(f"tools.{key}")
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
    editor.setStyleSheet(
        "background-color: transparent; color: #cdd3de; "
        "font-family: 'Cascadia Code', Consolas, monospace; "
        "font-size: 12px; border: none; padding: 6px;"
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


_BUILDERS: dict[str, Callable[[Any], QWidget]] = {
    "status":   _status,
    "llama":    _llama,
    "models":   _models,
    "proxy":    _proxy,
    "mcp":      _mcp,
    "managed":  _managed,
    "tools":    _tools,
    "telegram": _telegram,
    "voice":    _voice,
    "computer": _computer,
    "sessions": _sessions,
    "requests": _requests,
    "logs":     _logs,
    "raw":      _raw,
}
