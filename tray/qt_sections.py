"""Per-section widget builders for the settings window.

Each `build_<id>(window)` returns a QWidget. The window holds a cache so
sections are only built once. If a section defines a `refresh()` method,
the window calls it every 1s for live status.

Sections call helpers for settings patch + async dispatch.
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QScrollArea, QGridLayout, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QLineEdit,
)

from tray.qt_widgets import Toggle, NumberEditor, row_label
from tray.qt_helpers import (
    read_settings, get_path, patch_settings, schedule,
    humanize, format_protocol, build_status,
)
from tray.qt_theme import FG, FG_DIM, FG_MUTE, BG, BG_CARD, BORDER, OK, ERR


# ══════════════════════════════════════════════════════════════════════
# Common layout primitives
# ══════════════════════════════════════════════════════════════════════

def _page() -> tuple[QScrollArea, QWidget, QVBoxLayout]:
    """Scrollable page container."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
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
        from llamacpp.supervisor import _SUPERVISOR as sup
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
            from llamacpp.supervisor import get_supervisor
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
            from llamacpp.supervisor import get_supervisor
            from llamacpp import config as cfg
            sup = await get_supervisor()
            await sup.ensure_model(cfg.default_model())
        schedule(window.bot_loop, _do())
    def _unload():
        async def _do():
            from llamacpp.supervisor import get_supervisor
            sup = await get_supervisor()
            await sup.stop()
        schedule(window.bot_loop, _do())
    def _restart():
        async def _do():
            from llamacpp.supervisor import get_supervisor
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
    body.addWidget(_number_row("llamacpp.idle_unload_sec", "Idle Unload",
                               0, 3600, 60, 0, "s",
                               "Stop llama-server after this many seconds of no requests. 0 = never."))
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
        from llamacpp.supervisor import _SUPERVISOR as sup
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
    scroll, _, layout = _page()
    card, body = _card("Voice")
    body.addWidget(_toggle_row("voice.stt.enabled", "STT Enabled",
                                "Auto-transcribe voice messages via a local Whisper endpoint."))
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
    scroll, _, layout = _page()
    card, body = _card("Logs", "Open a log file in your default editor")
    for name in ("telecode.log", "telecode.log.prev", "llama.log", "llama.log.prev", "tray-bot.stderr.log"):
        btn = QPushButton(f"Open {name}")
        btn.setProperty("class", "ghost")
        def _open(_=False, n=name):
            import os, subprocess, sys as _s
            from pathlib import Path as _P
            from tray.qt_helpers import settings_path as _sp
            p = _sp().parent / "data" / "logs" / n
            try:
                if _s.platform == "win32":
                    os.startfile(str(p))
                elif _s.platform == "darwin":
                    subprocess.Popen(["open", str(p)])
                else:
                    subprocess.Popen(["xdg-open", str(p)])
            except Exception:
                pass
        btn.clicked.connect(_open)
        row = _row(row_label(name, "", ""), _wrap_align(btn, Qt.AlignmentFlag.AlignLeft))
        body.addWidget(row)
    layout.addWidget(card)
    layout.addStretch(1)
    return scroll


_BUILDERS: dict[str, Callable[[Any], QWidget]] = {
    "status":   _status,
    "llama":    _llama,
    "proxy":    _proxy,
    "mcp":      _mcp,
    "managed":  _managed,
    "telegram": _telegram,
    "voice":    _voice,
    "computer": _computer,
    "sessions": _sessions,
    "logs":     _logs,
}
