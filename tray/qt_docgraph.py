"""DocGraph section — single host model.

After docgraph 2.2.0, telecode supervises one `docgraph host` process
covering every configured root. The UI mirrors that mental model:

  Host          — start/stop/restart of the single child + bind config.
  Roots         — table of registered repos. Per-row Index button + Watch
                  toggle. Add / remove. Watch toggle persists to settings;
                  the host needs a restart to pick up watch flips.
  LLM           — augmentation knobs that apply at index time.
  Embeddings    — embedding model + GPU.

Live log tailing is delegated to the global Logs section — the host's
stdout/stderr and the telecode-side wrapper logger both land in
`data/logs/docgraph.log`.
"""
from __future__ import annotations

import asyncio
import re
import logging
import time
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QProgressBar,
)

from tray.qt_widgets import row_label, Toggle, WrapLabel
from tray.qt_helpers import (
    read_settings, get_path, patch_settings, schedule, humanize,
)
from tray.qt_theme import FG, FG_DIM, FG_MUTE, BG, BG_CARD, BG_ELEV, BORDER, OK, ERR, WARN
from tray.qt_sections import (
    _page, _card, _section_header, _row, _toggle_row, _line_row,
    _number_row, _enum_row_strs, _wrap_align, _idle_unload_row,
)

log = logging.getLogger("telecode.tray.docgraph")


def build_docgraph_tabs(window) -> QWidget:
    """Single scrollable page; 4 stacked cards."""
    scroll, _, layout = _page()
    refresh_fns: list[Callable[[], None]] = []

    # Binary
    binary_card, bb = _card("Binary")
    bb.addWidget(_line_row(
        "docgraph.binary",
        "Binary Path",
        "docgraph",
        "Bare name = use PATH. Empty = autodetect.",
        cli="docgraph CLI",
    ))
    layout.addWidget(binary_card)

    for build in (
        _build_host_card,
        _build_roots_card,
        _build_llm_card,
        _build_prompts_card,
        _build_embeddings_card,
        _build_reranker_card,
    ):
        card, refresh = build(window)
        layout.addWidget(card)
        if refresh is not None:
            refresh_fns.append(refresh)

    layout.addStretch(1)

    def refresh():
        for fn in refresh_fns:
            try:
                fn()
            except Exception:
                pass

    scroll.refresh = refresh  # type: ignore[attr-defined]
    return scroll


# ── Helpers ──────────────────────────────────────────────────────────────

def _bot_loop(window) -> asyncio.AbstractEventLoop | None:
    return getattr(window, "bot_loop", None)


def _run(window, coro_fn) -> None:
    loop = _bot_loop(window)
    if loop is None:
        return
    try:
        schedule(loop, coro_fn())
    except Exception as exc:
        log.warning("docgraph dispatch: %s", exc)


def _status_pill(getter):
    pill = QLabel("…")
    pill.setProperty("class", "stat_pill")

    def _refresh():
        try:
            ok, text = getter()
        except Exception as exc:
            pill.setText(f"err: {exc}")
            pill.setStyleSheet(f"color: {ERR};")
            return
        pill.setText(text)
        pill.setStyleSheet(f"color: {OK if ok else FG_MUTE};")

    return pill, _refresh


def _path_already_indexed(path: str) -> bool:
    """Detect whether `path` already has a docgraph index on disk.

    `<path>/.docgraph/cache.json` is the marker — the indexer writes it
    after every successful run and `/api/admin/clear` deletes it as part
    of the wipe. `graph.kuzu/` would also work as a marker but Kuzu's
    `wipe(keep_schema=False)` re-creates the directory with an empty
    schema, so it persists across Clear and the pill would lie."""
    if not path:
        return False
    try:
        from pathlib import Path as _Path
        marker = _Path(path).expanduser() / ".docgraph" / "cache.json"
        return marker.exists()
    except (OSError, ValueError):
        return False


def _path_already_wiki_built(path: str) -> bool:
    """Detect whether `<path>/.docgraph/wiki/` has any `.md` page.
    Same purpose as `_path_already_indexed`: distinguish 'wiki has been
    built sometime (perhaps via CLI directly)' from 'never built'."""
    if not path:
        return False
    try:
        from pathlib import Path as _Path
        wiki_dir = _Path(path).expanduser() / ".docgraph" / "wiki"
        if not wiki_dir.exists():
            return False
        return any(wiki_dir.glob("*.md"))
    except (OSError, ValueError):
        return False


def _wiki_page_count(path: str) -> int | None:
    if not path:
        return None
    try:
        from pathlib import Path as _Path
        wiki_dir = _Path(path).expanduser() / ".docgraph" / "wiki"
        if not wiki_dir.exists():
            return None
        return sum(1 for p in wiki_dir.rglob("*.md") if p.is_file())
    except (OSError, ValueError):
        return None


def _format_ago(ts: float | None) -> str:
    import time as _time
    if not ts:
        return "never"
    delta = max(0, int(_time.time() - ts))
    if delta < 60:    return f"{delta}s ago"
    if delta < 3600:  return f"{delta // 60}m ago"
    if delta < 86400: return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"
    return f"{delta // 86400}d ago"


# ── Phase metadata for index/wiki progress bars ─────────────────────────
#
# Mirrors the `_emit("<phase>", ...)` calls in docgraph/index.py + wiki.py.
# Each phase carries:
#   - a human label
#   - whether it's count-driven (else indeterminate)
# The order defines the "[i/N]" ordinal shown to the user. Phases that
# are conditional on cfg (llm_augment, embed_chunks) still
# get a slot — if they don't fire, the ordinal just skips them.

_INDEX_PHASES: list[tuple[str, str, bool]] = [
    ("fetch_links",    "fetching links",  False),
    ("start",          "starting",        False),
    ("delete",         "removing stale",  True),
    ("parse",          "parsing files",   True),
    ("llm_augment",    "llm docstrings",  True),
    ("seed_ids",       "seeding ids",     False),
    ("embed_entities", "embed entities",  True),
    ("embed_chunks",   "embed chunks",    True),
    ("symbol_table",   "symbol table",    False),
    ("edges",          "writing edges",   False),
    ("tier4_pagerank", "pagerank",        False),
    ("done",           "done",            True),
]
_INDEX_PHASE_INDEX = {p[0]: (i, p[1]) for i, p in enumerate(_INDEX_PHASES)}

_WIKI_PHASES: list[tuple[str, str, bool]] = [
    ("start",                 "preparing modules", True),
    ("module",                "writing module",        True),
    ("done",                  "done",                  True),
]
_WIKI_PHASE_INDEX = {p[0]: (i, p[1]) for i, p in enumerate(_WIKI_PHASES)}

_TERMINAL_JOB_STATUSES = {"done", "failed", "cancelled", "ok", "success"}


def _is_terminal_job_status(status: object) -> bool:
    return str(status or "").lower() in _TERMINAL_JOB_STATUSES


def _fmt_count(n: int) -> str:
    """Compact integer formatter — 173553 → '173.5k', 1234567 → '1.23M'."""
    n = int(n)
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    if n < 1_000_000_000:
        return f"{n / 1_000_000:.2f}M".rstrip("0").rstrip(".")
    return f"{n / 1_000_000_000:.2f}B".rstrip("0").rstrip(".")


def _fmt_phase_label(kind: str, phase: str, module: str = "") -> tuple[str, int, int]:
    """Return (display_label, ordinal, total_phases) for the given phase."""
    table = _INDEX_PHASE_INDEX if kind == "index" else _WIKI_PHASE_INDEX
    total = len(_INDEX_PHASES) if kind == "index" else len(_WIKI_PHASES)
    if phase in table:
        i, label = table[phase]
        if kind == "wiki" and phase == "module" and module:
            label = f"{label} · {module}"
        return label, i + 1, total
    # Dynamic BFS-level phases emitted as "fetch:N" (N = depth level).
    if kind == "index" and phase.startswith("fetch:"):
        level = phase[6:]
        fetch_ord = _INDEX_PHASE_INDEX.get("fetch_links", (0, ""))[0] + 1
        return f"fetching · level {level}", fetch_ord, total
    return phase or "?", 0, total


# ── Host card ────────────────────────────────────────────────────────────

def _build_host_card(window) -> tuple[QFrame, Callable[[], None]]:
    card, body = _card(
        "Host",
        "One docgraph host process serves every configured root. "
        "Restart to apply settings changes.",
    )

    body.addWidget(_toggle_row("docgraph.host.enabled", "Enabled",
                                "Off = host is stopped right now."))
    body.addWidget(_toggle_row("docgraph.host.auto_start", "Auto-start",
                                "Start the host when telecode boots."))
    body.addWidget(_toggle_row("docgraph.host.auto_restart", "Auto-restart",
                                "Re-spawn on unexpected exit."))
    body.addWidget(_line_row("docgraph.host.host", "Bind Host", "127.0.0.1",
                              cli="--host"))
    body.addWidget(_number_row("docgraph.host.port", "Bind Port", 1024, 65535, 1, 0,
                                cli="--port"))
    body.addWidget(_number_row("docgraph.host.debounce", "Watcher debounce",
                                50, 5000, 50, 0, "ms",
                                "Default 500. Only used with watched roots.",
                                cli="--debounce"))

    # Endpoints — derived from {Bind Host}:{Bind Port}. Updated by
    # refresh_status() so editing the host/port fields shows the new
    # URLs immediately, without waiting for a Restart.
    endpoints_lbl = QLabel("")
    endpoints_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    endpoints_lbl.setOpenExternalLinks(True)
    endpoints_lbl.setStyleSheet(
        f"color: {OK}; font-size: 11.5px; font-family: 'JetBrains Mono', Consolas, monospace;"
    )
    endpoints_lbl.setWordWrap(True)
    body.addWidget(_row(row_label("Endpoints",
        "REST API / MCP streamable-HTTP. Telecode bridges the MCP "
        "endpoint into the proxy as `docgraph_*` tools."), endpoints_lbl))

    actions = QWidget()
    ar = QHBoxLayout(actions)
    ar.setContentsMargins(0, 0, 0, 0); ar.setSpacing(8)
    start_btn = QPushButton("▶ Start"); start_btn.setProperty("class", "primary")
    stop_btn  = QPushButton("Stop");    stop_btn.setProperty("class", "danger")
    restart_btn = QPushButton("Restart")
    ar.addWidget(start_btn); ar.addWidget(stop_btn); ar.addWidget(restart_btn)
    ar.addStretch(1)
    body.addWidget(_row(row_label("Actions"), actions))

    def refresh_status() -> None:
        # Gate Start/Stop/Restart on actual liveness so the user can't
        # double-click Start on an already-running host (or Stop one
        # that's already dead). Read straight from the supervisor — the
        # `enabled` setting is just sticky intent, not real state.
        try:
            from docgraph.process import status_snapshot
            from docgraph.process import get_index, get_wiki
            alive = bool((status_snapshot().get("host") or {}).get("alive"))
            busy = bool(get_index().status().get("alive") or get_wiki().status().get("alive"))
        except Exception:
            alive = False
            busy = False
        start_btn.setEnabled(not alive and not busy)
        stop_btn.setEnabled(alive and not busy)
        restart_btn.setEnabled(alive and not busy)

        # Endpoints (recomputed every refresh — host/port edits land in
        # settings.json on focus-out, so the next 1 s tick shows them).
        try:
            from docgraph import config as _dg_cfg
            h = (_dg_cfg.host_host() or "127.0.0.1").strip() or "127.0.0.1"
            p = int(_dg_cfg.host_port() or 5500)
        except Exception:
            h, p = "127.0.0.1", 5500
        base = f"http://{h}:{p}"
        endpoints_lbl.setText(
            f"<a style='color:{OK}' href='{base}/api/roots'>{base}/api/</a>"
            f" &nbsp;·&nbsp; "
            f"<span style='color:{OK}'><b>{base}/mcp/</b></span>"
        )

    def _on_start():
        async def _go():
            from docgraph.process import get_host
            await get_host().start()
        _run(window, _go)

    def _on_stop():
        async def _go():
            from docgraph.process import get_host
            await get_host().stop()
        _run(window, _go)

    def _on_restart():
        async def _go():
            from docgraph.process import get_host
            sup = get_host()
            await sup.stop()
            await sup.start()
        _run(window, _go)

    start_btn.clicked.connect(_on_start)
    stop_btn.clicked.connect(_on_stop)
    restart_btn.clicked.connect(_on_restart)

    refresh_status()
    return card, refresh_status


def _host_status_text() -> tuple[bool, str]:
    try:
        from docgraph.process import status_snapshot
        s = status_snapshot().get("host", {}) or {}
    except Exception as exc:
        return False, f"err: {exc}"
    if s.get("alive"):
        pid = s.get("pid"); port = s.get("port")
        bridged = s.get("bridged") or 0
        bits = []
        if pid:     bits.append(f"pid={pid}")
        if port:    bits.append(f"port={port}")
        if bridged: bits.append(f"bridged={bridged}")
        return True, "alive  " + "  ".join(bits)
    err = s.get("last_error")
    if err:
        return False, f"failed: {err}"
    return False, "stopped" + (" (enabled)" if s.get("enabled") else "")


# ── Roots card (multi-row table) ────────────────────────────────────────

def _build_roots_card(window) -> tuple[QFrame, Callable[[], None]]:
    card, body = _card(
        "Roots",
        "Repos registered with the host. Watch flips need a host restart.",
    )

    # Build the master Full toggle FIRST so its state can be threaded
    # into per-row Index/Wiki buttons via a getter.
    from tray.qt_widgets import Toggle as _Toggle
    all_force = _Toggle()
    all_force.setToolTip(
        "On  = docgraph index --full / docgraph wiki --force\n"
        "Off = incremental index / resumable wiki"
    )

    paths_widget = _RootsTable(window, force_getter=all_force.isChecked)
    body.addWidget(paths_widget)

    body.addWidget(_section_header("Global actions"))

    body.addWidget(_row(row_label(
        "Full rebuild",
        "On = --full / --force. Off = incremental."),
        _wrap_align(all_force, Qt.AlignmentFlag.AlignLeft)))

    # Index all row: ▶ + ✕ cancel + status pill.
    run_all_btn = QPushButton("▶ Index all")
    run_all_btn.setProperty("class", "primary")
    run_all_btn.setStyleSheet("padding: 4px 14px;")
    run_all_btn.setToolTip("Index every configured root.")
    cancel_btn = QPushButton("✕")
    cancel_btn.setProperty("class", "danger")
    cancel_btn.setFixedWidth(28)
    cancel_btn.setToolTip("Cancel the running index pass.")
    status_lbl, refresh_status = _status_pill(_index_status_text)

    idx_w = QWidget()
    il = QHBoxLayout(idx_w); il.setContentsMargins(0, 0, 0, 0); il.setSpacing(8)
    il.addWidget(run_all_btn); il.addWidget(cancel_btn)
    il.addWidget(status_lbl, 0); il.addStretch(1)
    body.addWidget(_row(row_label("Index all roots"), idx_w))

    # Build wikis row: ▶ + ✕ cancel + status pill.
    run_all_wiki_btn = QPushButton("▶ Build wikis")
    run_all_wiki_btn.setProperty("class", "primary")
    run_all_wiki_btn.setStyleSheet("padding: 4px 14px;")
    run_all_wiki_btn.setToolTip("Build the wiki for every configured root.")
    action_btn_w = max(run_all_btn.sizeHint().width(), run_all_wiki_btn.sizeHint().width())
    run_all_btn.setFixedWidth(action_btn_w)
    run_all_wiki_btn.setFixedWidth(action_btn_w)
    cancel_wiki_btn = QPushButton("✕")
    cancel_wiki_btn.setProperty("class", "danger")
    cancel_wiki_btn.setFixedWidth(28)
    cancel_wiki_btn.setToolTip("Cancel the running wiki build.")
    wiki_status_lbl, refresh_wiki_status = _status_pill(_wiki_status_text)

    wiki_w = QWidget()
    wl = QHBoxLayout(wiki_w); wl.setContentsMargins(0, 0, 0, 0); wl.setSpacing(8)
    wl.addWidget(run_all_wiki_btn); wl.addWidget(cancel_wiki_btn)
    wl.addWidget(wiki_status_lbl, 0); wl.addStretch(1)
    body.addWidget(_row(row_label("Build wikis for all roots"), wiki_w))

    def _all():
        async def _go():
            from docgraph.process import get_index
            await get_index().run_all(force=bool(all_force.isChecked()))
        _run(window, _go)

    def _on_cancel():
        async def _go():
            from docgraph.process import get_index
            await get_index().cancel()
        _run(window, _go)

    def _all_wiki():
        async def _go():
            from docgraph.process import get_wiki
            await get_wiki().run_all(force=bool(all_force.isChecked()))
        _run(window, _go)

    def _on_cancel_wiki():
        async def _go():
            from docgraph.process import get_wiki
            await get_wiki().cancel()
        _run(window, _go)

    run_all_btn.clicked.connect(_all)
    cancel_btn.clicked.connect(_on_cancel)
    run_all_wiki_btn.clicked.connect(_all_wiki)
    cancel_wiki_btn.clicked.connect(_on_cancel_wiki)

    def refresh():
        try:
            from docgraph.process import get_index, get_wiki
            from docgraph import index_state, wiki_state
            index_status = get_index().status()
            wiki_status = get_wiki().status()
            index_path = str(index_status.get("current_path") or "")
            wiki_path = str(wiki_status.get("current_path") or "")
            index_live = index_state.get(index_path) if index_path else None
            wiki_live = wiki_state.get(wiki_path) if wiki_path else None
            index_alive = bool(index_status.get("alive")) and not _is_terminal_job_status((index_live or {}).get("last_status"))
            wiki_alive = bool(wiki_status.get("alive")) and not _is_terminal_job_status((wiki_live or {}).get("last_status"))
        except Exception:
            index_alive = wiki_alive = False

        busy = index_alive or wiki_alive

        all_force.setEnabled(not busy)
        run_all_btn.setEnabled(not busy)
        cancel_btn.setEnabled(index_alive)
        run_all_wiki_btn.setEnabled(not busy)
        cancel_wiki_btn.setEnabled(wiki_alive)
        paths_widget.refresh(busy=busy)

        refresh_status()
        refresh_wiki_status()

    return card, refresh


def _index_status_text() -> tuple[bool, str]:
    try:
        from docgraph.process import get_index
        from docgraph import index_state
        s = get_index().status()
    except Exception as exc:
        return False, f"err: {exc}"
    live = index_state.get(str(s.get("current_path") or "")) if s.get("current_path") else None
    live_status = str((live or {}).get("last_status") or "").lower()
    if live_status in _TERMINAL_JOB_STATUSES:
        what = "full" if s.get("current_force") else "incremental"
        return True, f"{live_status} · {s.get('current_path') or '?'} ({what})"
    if s["alive"]:
        what = "full" if s.get("current_force") else "incremental"
        return True, f"running · {s.get('current_path') or '?'} ({what})"
    return False, _index_totals_text()


def _index_totals_text() -> str:
    """Aggregate cached entity/edge totals across all configured roots."""
    try:
        from docgraph import config as dg_cfg
        from docgraph import stats_state
    except Exception:
        return "idle"

    total_ents = 0
    total_edges = 0
    seen = False

    for path in dg_cfg.root_paths():
        snap = stats_state.get(path)
        if not snap:
            continue
        seen = True

        ents = sum(int(snap.get(k, 0) or 0)
                   for k in ("File", "Module", "Class", "Function", "Variable"))
        edges = snap.get("edges")
        if edges is None:
            edges = sum(int(v or 0) for v in (snap.get("edges_by_type") or {}).values())

        total_ents += int(ents or 0)
        total_edges += int(edges or 0)

    if not seen:
        return "idle"
    return f"{_fmt_count(total_ents)} ents · {_fmt_count(total_edges)} edges"


def _wiki_status_text() -> tuple[bool, str]:
    try:
        from docgraph.process import get_wiki
        from docgraph import config as dg_cfg
        from docgraph import wiki_state
        s = get_wiki().status()
    except Exception as exc:
        return False, f"err: {exc}"
    live = wiki_state.get(str(s.get("current_path") or "")) if s.get("current_path") else None
    live_status = str((live or {}).get("last_status") or "").lower()
    if live_status in _TERMINAL_JOB_STATUSES:
        what = "force" if s.get("current_force") else "resumable"
        return True, f"{live_status} · {s.get('current_path') or '?'} ({what})"
    if s["alive"]:
        what = "force" if s.get("current_force") else "resumable"
        return True, f"running · {s.get('current_path') or '?'} ({what})"
    # Idle: count total wiki docs across all roots.
    try:
        total_docs = sum(
            (_wiki_page_count(p) or 0) for p in dg_cfg.root_paths()
        )
        docs_text = f"{_fmt_count(total_docs)} wiki docs" if total_docs > 0 else "idle"
        return False, docs_text
    except Exception:
        return False, "idle"


class _RootsTable(QWidget):
    """Editor for `docgraph.roots[]` (`{path, watch}` entries).

    Each row: editable path · ▶ Index · Watch toggle · status pill · ✕ remove.
    Persists to `docgraph.roots` on every edit.

    `force_getter` is a callable returning a bool — read at click time so
    flipping the master Full toggle takes effect immediately on the next
    per-row Index, without rebuilding the table.
    """

    def __init__(self, window, *, force_getter: Callable[[], bool] | None = None) -> None:
        super().__init__()
        self._window = window
        self._force_getter = force_getter or (lambda: False)
        self._restarting = False
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        v.addWidget(self._rows_host)

        add_w = QWidget()
        add_l = QHBoxLayout(add_w)
        add_l.setContentsMargins(0, 0, 0, 0)
        add_btn = QPushButton("+ Add root")
        add_btn.setProperty("class", "primary")
        add_btn.setMaximumWidth(140)
        add_btn.clicked.connect(self._on_add)
        self._add_btn = add_btn
        add_l.addWidget(add_btn)
        add_l.addStretch(1)
        v.addWidget(add_w)

        self._row_widgets: list[_RootRow] = []
        self._rebuild()

    def _rebuild(self) -> None:
        for w in self._row_widgets:
            w.setParent(None)
            w.deleteLater()
        self._row_widgets.clear()
        cur = list(get_path(read_settings(), "docgraph.roots", []) or [])
        for entry in cur:
            if isinstance(entry, dict):
                path = str(entry.get("path", "") or "")
                watch = bool(entry.get("watch", False))
                pinned = bool(entry.get("pinned", False))
            else:
                path, watch, pinned = str(entry), False, False
            self._append_row(path, watch, pinned)

    def _append_row(self, path: str, watch: bool, pinned: bool = False) -> None:
        row = _RootRow(
            path, watch, self._window,
            on_change=self._commit, on_remove=self._on_remove,
            force_getter=self._force_getter,
            pinned=pinned,
        )
        self._rows_layout.addWidget(row)
        self._row_widgets.append(row)

    def _on_add(self) -> None:
        self._append_row("", False)
        self._commit()

    def _on_remove(self, row: "_RootRow") -> None:
        if row.is_pinned():
            return
        try:
            self._row_widgets.remove(row)
        except ValueError:
            pass
        row.setParent(None)
        row.deleteLater()
        self._commit()

    def _commit(self) -> None:
        old = list(get_path(read_settings(), "docgraph.roots", []) or [])
        old_paths = {
            str(e.get("path", "") if isinstance(e, dict) else e)
            for e in old
            if (e.get("path", "") if isinstance(e, dict) else e)
        }

        out = []
        for r in self._row_widgets:
            path = r.text().strip()
            if not path:
                continue
            entry: dict = {"path": path, "watch": r.watch_state()}
            if r.is_pinned():
                entry["pinned"] = True
            out.append(entry)
        patch_settings("docgraph.roots", out)

        new_paths = {e["path"] for e in out}
        if new_paths != old_paths:
            self._restart_host_if_alive()

    def _restart_host_if_alive(self) -> None:
        self._restarting = True
        async def _go():
            from docgraph.process import get_host, status_snapshot
            try:
                alive = bool((status_snapshot().get("host") or {}).get("alive"))
            except Exception:
                alive = False
            if not alive:
                self._restarting = False
                return
            sup = get_host()
            try:
                await sup.stop()
            except Exception:
                pass
            try:
                await sup.start()
            finally:
                self._restarting = False
        _run(self._window, _go)

    def refresh(self, *, busy: bool = False) -> None:
        busy = busy or self._restarting
        cur = list(get_path(read_settings(), "docgraph.roots", []) or [])
        cur_norm = [
            {"path": str(e.get("path", "") if isinstance(e, dict) else e),
             "watch": bool(e.get("watch", False) if isinstance(e, dict) else False),
             "pinned": bool(e.get("pinned", False) if isinstance(e, dict) else False)}
            for e in cur
        ]
        cur_norm = [e for e in cur_norm if e["path"]]
        cur_view = [{"path": r.text().strip(), "watch": r.watch_state(), "pinned": r.is_pinned()}
                    for r in self._row_widgets if r.text().strip()]
        if cur_norm != cur_view:
            self._rebuild()
        self._add_btn.setEnabled(not busy)
        for r in self._row_widgets:
            r.refresh_state(busy=busy)


class _RootRow(QFrame):
    def __init__(self, path: str, watch: bool, window, *, on_change, on_remove,
                 force_getter: Callable[[], bool] | None = None,
                 pinned: bool = False) -> None:
        super().__init__()
        self._window = window
        self._on_change = on_change
        self._on_remove = on_remove
        self._force_getter = force_getter or (lambda: False)
        self._pinned = bool(pinned)

        self.setStyleSheet(
            f"_RootRow {{ background: {BG_ELEV}; border: 1px solid {BORDER}; border-radius: 6px; }}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(6)

        # ── Line 1: path (75%) + status pills (25%) + remove ✕ ────────
        line1 = QWidget()
        h = QHBoxLayout(line1)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)

        self._edit = QLineEdit(path)
        self._edit.setPlaceholderText("/path/to/repo")
        self._edit.editingFinished.connect(self._on_edit_done)
        self._edit.setMinimumWidth(140)
        if self._pinned:
            self._edit.setReadOnly(True)
            self._edit.setToolTip(f"{path}\n\nPinned root - path is locked.")
        else:
            self._edit.setToolTip(path or "/path/to/repo")
        # 3 : 1 split → path is the 75%, the status block is the 25%.
        h.addWidget(self._edit, 3)

        pills_w = QWidget()
        pl = QHBoxLayout(pills_w)
        pl.setContentsMargins(0, 0, 0, 0); pl.setSpacing(4)

        self._pill = QLabel("…")
        self._pill.setProperty("class", "stat_pill")
        self._pill.setMinimumWidth(0)
        self._pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pill.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._pill.setToolTip("Index status")
        pl.addWidget(self._pill, 1)

        self._wiki_pill = QLabel("…")
        self._wiki_pill.setProperty("class", "stat_pill")
        self._wiki_pill.setMinimumWidth(0)
        self._wiki_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._wiki_pill.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._wiki_pill.setToolTip("Wiki status")
        pl.addWidget(self._wiki_pill, 1)

        # Auto-refreshing stats chip — entity / edge counts pulled from
        # /api/stats every ~10s while the host is alive. Replaces the
        # old 📊 Stats button.
        self._stats_chip = QLabel("—")
        self._stats_chip.setProperty("class", "stat_chip")
        self._stats_chip.setProperty("muted", "true")
        self._stats_chip.setMinimumWidth(0)
        self._stats_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stats_chip.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._stats_chip.setToolTip("Live counts (entities · edges). Auto-refreshed.")
        pl.addWidget(self._stats_chip, 1)

        h.addWidget(pills_w, 1)
        # Tracks the last time we fired an /api/stats fetch for this row
        # so the 1s tray tick doesn't hammer the host.
        self._stats_last_fetch: float = 0.0

        rm_btn = QPushButton("✕")
        rm_btn.setFlat(True)
        rm_btn.setFixedWidth(28)
        rm_btn.setStyleSheet(
            f"QPushButton {{ color: {FG_DIM}; border: none; background: transparent; }}"
            f" QPushButton:hover {{ color: #ff6b6b; }}"
        )
        rm_btn.clicked.connect(lambda: self._on_remove(self))
        self._rm_btn = rm_btn
        if self._pinned:
            # Reserve the 28px column so pinned rows align with unpinned ones;
            # show a non-actionable pin glyph instead of the remove button.
            rm_btn.hide()
            pin_lbl = QLabel("📌")
            pin_lbl.setFixedWidth(28)
            pin_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pin_lbl.setToolTip("Pinned root - cannot be removed or reordered.")
            h.addWidget(pin_lbl)
        else:
            h.addWidget(rm_btn)

        outer.addWidget(line1)

        # ── Line 2: labeled action buttons + Watch toggle ─────────────
        line2 = QWidget()
        bh = QHBoxLayout(line2)
        bh.setContentsMargins(0, 0, 0, 0)
        bh.setSpacing(6)

        self._index_btn = QPushButton("▶ Index")
        self._index_btn.setToolTip(
            "Index this root.\n"
            "POST /api/admin/index?root=<slug>  if host is alive,\n"
            "else falls back to:  docgraph index <path>"
        )
        self._index_btn.clicked.connect(self._trigger_index)
        bh.addWidget(self._index_btn)

        self._wiki_btn = QPushButton("📖 Wiki")
        self._wiki_btn.setToolTip(
            "Build the wiki for this root.\n"
            "POST /api/wiki/build?root=<slug>  if host is alive,\n"
            "else falls back to:  docgraph wiki <path>\n"
            "Full toggle on = --force (rebuild every page)"
        )
        self._wiki_btn.clicked.connect(self._trigger_wiki)
        bh.addWidget(self._wiki_btn)

        self._clear_btn = QPushButton("🗑 Clear")
        self._clear_btn.setProperty("class", "danger")
        self._clear_btn.setToolTip(
            "Clear this root's index.\n"
            "POST /api/admin/clear?root=<slug> — wipe the index, cache, and\n"
            "wiki for this root. Confirmation required. Host stays alive;\n"
            "the workspace re-opens its read-only handle once the wipe is done."
        )
        self._clear_btn.clicked.connect(self._trigger_clear)
        bh.addWidget(self._clear_btn)

        bh.addStretch(1)

        watch_lbl = QLabel("Watch")
        watch_lbl.setStyleSheet(f"color: {FG_DIM}; font-size: 11.5px;")
        bh.addWidget(watch_lbl)
        self._watch = Toggle()
        self._watch.setChecked(bool(watch))
        self._watch.toggled.connect(self._on_watch_toggled)
        self._watch.setToolTip(
            "Watch — auto-reindex on file changes.\n"
            "Forwards as `docgraph host --watch <path>`. "
            "Restart the host to apply a flipped flag."
        )
        bh.addWidget(self._watch)

        outer.addWidget(line2)

        # ── Line 3: paired progress bars (index left, wiki right).
        # Always visible so the row keeps a consistent 3-line height;
        # bars sit in the "idle" state when nothing is running.
        self._line3 = QWidget()
        l3 = QHBoxLayout(self._line3)
        l3.setContentsMargins(0, 0, 0, 0)
        l3.setSpacing(8)

        def _mkbar(kind: str, idle_label: str) -> QProgressBar:
            bar = QProgressBar()
            bar.setProperty("kind", kind)
            bar.setProperty("state", "idle")
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(True)
            bar.setFormat(idle_label)
            bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bar.setFixedHeight(20)
            return bar

        self._idx_bar = _mkbar("idx", "index · idle")
        l3.addWidget(self._idx_bar, 1)

        self._wiki_bar = _mkbar("wiki", "wiki · idle")
        l3.addWidget(self._wiki_bar, 1)

        outer.addWidget(self._line3)

        # ── Line 4: collapsible extra-paths section ───────────────────
        self._extra_paths_section = _ExtraPathsSection(
            path, self._window, on_commit=self._on_change
        )
        outer.addWidget(self._extra_paths_section)

        # ── Line 5: collapsible external-links section ────────────────
        self._links_section = _LinksSection(
            path, self._window, on_commit=self._on_change
        )
        outer.addWidget(self._links_section)

        self.refresh_state()

    def text(self) -> str:
        return self._edit.text()

    def watch_state(self) -> bool:
        return bool(self._watch.isChecked())

    def is_pinned(self) -> bool:
        return self._pinned

    def _on_edit_done(self) -> None:
        self._edit.setToolTip(self._edit.text() or "/path/to/repo")
        self._on_change()
        self.refresh_state()

    def _on_watch_toggled(self, _checked: bool) -> None:
        self._on_change()

    def _trigger_index(self) -> None:
        path = self.text().strip()
        if not path:
            return
        force = bool(self._force_getter())
        async def _go():
            from docgraph.process import get_index
            await get_index().run(path, force=force)
        _run(self._window, _go)

    def _trigger_wiki(self) -> None:
        path = self.text().strip()
        if not path:
            return
        force = bool(self._force_getter())
        async def _go():
            from docgraph.process import get_wiki
            await get_wiki().run(path, force=force)
        _run(self._window, _go)

    def _trigger_clear(self) -> None:
        path = self.text().strip()
        if not path:
            return
        from PySide6.QtWidgets import QMessageBox
        confirm = QMessageBox.question(
            self,
            "Clear index?",
            f"Wipe the index, cache, and wiki under\n  {path}/.docgraph/\n\n"
            "Re-indexing will cost a full rebuild. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        async def _go():
            from docgraph.process import clear_index
            ok, detail = await clear_index(path)
            if ok:
                # Drop telecode's recorded run history so the pills don't
                # keep advertising the pre-clear state ("indexed", "wiki Ns ago").
                try:
                    from docgraph import index_state, wiki_state, stats_state
                    index_state.clear(path)
                    wiki_state.clear(path)
                    stats_state.drop(path)
                except Exception:
                    pass
                # Force the next refresh tick to actually fetch — otherwise
                # the per-row 10s throttle would keep the chip stuck on the
                # pre-clear render.
                self._stats_last_fetch = 0.0
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QMessageBox as _MB
            kind = _MB.information if ok else _MB.warning
            title = "Cleared" if ok else "Clear failed"
            def _show():
                kind(self, title, detail)
                if ok:
                    self.refresh_state()
            QTimer.singleShot(0, _show)
        _run(self._window, _go)

    def refresh_state(self, *, busy: bool = False) -> None:
        path = self.text().strip()
        self._refresh_index_pill(path)
        self._refresh_wiki_pill(path)
        self._refresh_stats_chip(path)
        self._refresh_progress_bars(path)
        self._extra_paths_section.set_path(path)
        self._extra_paths_section.refresh()
        self._links_section.set_path(path)
        self._links_section.refresh()

        enabled = not busy
        self._edit.setEnabled(enabled)
        self._index_btn.setEnabled(enabled)
        self._wiki_btn.setEnabled(enabled)
        self._clear_btn.setEnabled(enabled)
        self._watch.setEnabled(enabled)
        self._rm_btn.setEnabled(enabled)
        self._extra_paths_section.setEnabled(enabled)
        self._links_section.setEnabled(enabled)

    def _apply_bar_state(self, bar: QProgressBar, state: str) -> None:
        """Flip the bar between 'idle' / 'run' so the QSS picks up the
        right styling (dashed border vs. gradient chunk). Qt only re-runs
        the stylesheet on a property change if you nudge it via
        unpolish + polish."""
        if bar.property("state") == state:
            return
        bar.setProperty("state", state)
        st = bar.style()
        st.unpolish(bar)
        st.polish(bar)

    def _paint_bar(self, bar: QProgressBar, kind: str, ps: dict | None,
                   running: bool, idle_label: str) -> None:
        if not running:
            self._apply_bar_state(bar, "idle")
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFormat(idle_label)
            return

        self._apply_bar_state(bar, "run")
        phase = (ps or {}).get("phase") or "start"
        module = (ps or {}).get("module") or ""
        label, ord_, total_phases = _fmt_phase_label(kind, phase, module)
        cur = int((ps or {}).get("current") or 0)
        tot = int((ps or {}).get("total") or 0)

        if tot > 0:
            pct = max(0, min(100, int(cur * 100 / tot)))
            bar.setRange(0, 100)
            bar.setValue(pct)
            bar.setFormat(
                f"[{ord_}/{total_phases}] {label}  ·  {pct}%  "
                f"({_fmt_count(cur)}/{_fmt_count(tot)})"
            )
        else:
            # Indeterminate — Qt renders a marquee chunk when range is 0/0.
            bar.setRange(0, 0)
            bar.setFormat(f"[{ord_}/{total_phases}] {label}  ·  …")

    def _refresh_progress_bars(self, path: str) -> None:
        """Paint the live SSE progress into the per-row QProgressBars."""
        try:
            from docgraph import progress_state, index_state, wiki_state
            from docgraph.process import get_index, get_wiki
            idx_status = str((index_state.get(path) or {}).get("last_status") or "").lower() if path else ""
            wiki_status = str((wiki_state.get(path) or {}).get("last_status") or "").lower() if path else ""
            idx_running = bool(path) and get_index().current_path() == path and idx_status not in _TERMINAL_JOB_STATUSES
            wiki_running = bool(path) and get_wiki().current_path() == path and wiki_status not in _TERMINAL_JOB_STATUSES
            idx_ps = progress_state.get(path, "index") if path else None
            wiki_ps = progress_state.get(path, "wiki") if path else None
        except Exception:
            idx_running = wiki_running = False
            idx_ps = wiki_ps = None

        self._paint_bar(self._idx_bar, "index", idx_ps, idx_running, "index · idle")
        self._paint_bar(self._wiki_bar, "wiki", wiki_ps, wiki_running, "wiki · idle")

    def _refresh_stats_chip(self, path: str) -> None:
        """Display live entity/edge counts and trigger a background
        refresh when the cached snapshot is older than 10s. Cheap when
        the host is alive (one /api/stats roundtrip), no-op otherwise."""
        try:
            from docgraph import stats_state
            from docgraph.process import get_host
            host_alive = bool(get_host().alive())
        except Exception:
            host_alive = False
            stats_state = None  # type: ignore

        # Render whatever we have cached.
        snap = stats_state.get(path) if (stats_state and path) else None
        wiki_pages = _wiki_page_count(path)
        muted = "true"
        parts: list[str] = []
        if snap:
            ents = sum(int(snap.get(k, 0) or 0)
                       for k in ("File", "Module", "Class", "Function", "Variable"))
            # Server-side total (preferred). Fall back to summing edges_by_type
            # for older hosts that don't yet emit `edges`.
            edges = snap.get("edges")
            if edges is None:
                edges = sum(int(v or 0)
                            for v in (snap.get("edges_by_type") or {}).values())
            edges = int(edges or 0)
            parts.extend((f"{_fmt_count(ents)} ents", f"{_fmt_count(edges)} edges"))
            tip_lines = [f"{path or '(no path)'}", ""]
            for label in ("File", "Module", "Class", "Function", "Variable"):
                tip_lines.append(f"  {label:<10} {snap.get(label, 0)}")
            top_edges = sorted(
                ((k, int(v or 0))
                 for k, v in (snap.get("edges_by_type") or {}).items()),
                key=lambda kv: kv[1], reverse=True,
            )[:6]
            if top_edges:
                tip_lines.append("")
                for k, v in top_edges:
                    tip_lines.append(f"  {k:<14} {v}")
            if wiki_pages is not None:
                tip_lines.append("")
                tip_lines.append(f"  wiki pages  {wiki_pages}")
            self._stats_chip.setToolTip("\n".join(tip_lines))
            muted = "false"
        else:
            parts.extend(("—", "—"))
            if wiki_pages is not None:
                parts.append(f"{_fmt_count(wiki_pages)} wiki docs")
            self._stats_chip.setToolTip(
                "Live counts auto-refresh while the host is alive."
                if host_alive else
                "Start the docgraph host to see live counts."
            )
        if wiki_pages is not None and snap:
            parts.append(f"{_fmt_count(wiki_pages)} wiki docs")
        self._stats_chip.setText(" · ".join(parts) if parts else ("host offline" if not host_alive else "—"))
        if self._stats_chip.property("muted") != muted:
            self._stats_chip.setProperty("muted", muted)
            st = self._stats_chip.style()
            st.unpolish(self._stats_chip); st.polish(self._stats_chip)

        # Maybe schedule a fresh fetch. Skip if no path, no host, or
        # we already have a fresh snapshot. Throttle per-row at 10s and
        # use stats_state's in-flight flag to dedupe across rows pointing
        # at the same path.
        if not path or not host_alive or stats_state is None:
            return
        import time as _time
        now = _time.time()
        if now - self._stats_last_fetch < 10.0:
            return
        if stats_state.age(path) < 10.0:
            self._stats_last_fetch = now
            return
        if not stats_state.mark_in_flight(path):
            return
        self._stats_last_fetch = now

        async def _go():
            try:
                from docgraph.process import fetch_stats_dict
                data = await fetch_stats_dict(path)
                if data is not None:
                    stats_state.set(path, data)
            finally:
                stats_state.clear_in_flight(path)
        _run(self._window, _go)

    def _refresh_index_pill(self, path: str) -> None:
        try:
            from docgraph import index_state
            from docgraph.process import get_index
            s = index_state.get(path) if path else None
            running_path = get_index().current_path()
        except Exception:
            s, running_path = None, None
        status = str((s or {}).get("last_status") or "").lower()
        if path and running_path == path and status not in _TERMINAL_JOB_STATUSES:
            self._pill.setText("running…")
            self._pill.setStyleSheet(f"color: {WARN};")
            self._index_btn.setEnabled(False)
            return
        self._index_btn.setEnabled(True)
        if not s:
            already = _path_already_indexed(path)
            if already:
                self._pill.setText("indexed (on disk)")
                self._pill.setStyleSheet(f"color: {OK};")
            else:
                self._pill.setText("not indexed")
                self._pill.setStyleSheet(f"color: {FG_MUTE};")
            return
        ago = _format_ago(s.get("last_run", 0.0))
        status = s.get("last_status", "?")
        full = " · force" if s.get("last_was_full") else ""
        text = f"{ago} · {status}{full}"
        if status == "ok":
            self._pill.setStyleSheet(f"color: {OK};")
        elif status == "failed":
            self._pill.setStyleSheet(f"color: {ERR};")
        elif status == "running":
            self._pill.setStyleSheet(f"color: {WARN};")
        else:
            self._pill.setStyleSheet(f"color: {FG_MUTE};")
        self._pill.setText(text)

    def _refresh_wiki_pill(self, path: str) -> None:
        try:
            from docgraph import wiki_state
            from docgraph.process import get_wiki
            s = wiki_state.get(path) if path else None
            running_path = get_wiki().current_path()
        except Exception:
            s, running_path = None, None
        status = str((s or {}).get("last_status") or "").lower()
        if path and running_path == path and status not in _TERMINAL_JOB_STATUSES:
            self._wiki_pill.setText("wiki running…")
            self._wiki_pill.setStyleSheet(f"color: {WARN};")
            self._wiki_btn.setEnabled(False)
            return
        self._wiki_btn.setEnabled(True)
        if not s:
            already = _path_already_wiki_built(path)
            if already:
                self._wiki_pill.setText("wiki on disk")
                self._wiki_pill.setStyleSheet(f"color: {OK};")
            else:
                self._wiki_pill.setText("no wiki")
                self._wiki_pill.setStyleSheet(f"color: {FG_MUTE};")
            return
        ago = _format_ago(s.get("last_run", 0.0))
        status = s.get("last_status", "?")
        full = " · force" if s.get("last_was_full") else ""
        text = f"wiki {ago} · {status}{full}"
        if status == "ok":
            self._wiki_pill.setStyleSheet(f"color: {OK};")
        elif status == "failed":
            self._wiki_pill.setStyleSheet(f"color: {ERR};")
        elif status == "running":
            self._wiki_pill.setStyleSheet(f"color: {WARN};")
        else:
            self._wiki_pill.setStyleSheet(f"color: {FG_MUTE};")
        self._wiki_pill.setText(text)


# ── External-links section ───────────────────────────────────────────────



def _link_status_text(entry: dict) -> str:
    """Return a human-readable staleness label for a link entry dict."""
    lf = entry.get("last_fetched")
    ttl = float(entry.get("ttl_hours", 24.0))
    pc = entry.get("page_count")
    pages = f" · {pc}p" if pc else ""
    if lf is None:
        return "never fetched"
    delta = max(0, int(time.time() - float(lf)))
    if delta < 60:
        age = f"{delta}s ago"
    elif delta < 3600:
        age = f"{delta // 60}m ago"
    else:
        age = f"{delta // 3600}h ago"
    stale = " (stale)" if (time.time() - float(lf)) > ttl * 3600 else ""
    return f"{age}{pages}{stale}"


class _ExtraPathRow(QWidget):
    """One extra-path row: path QLineEdit + exists indicator + remove button."""

    def __init__(self, path_str: str, on_change: Callable[[], None],
                 on_remove: Callable[["_ExtraPathRow"], None]) -> None:
        super().__init__()
        self._on_change = on_change
        self._on_remove = on_remove

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)

        self._path_edit = QLineEdit(path_str)
        self._path_edit.setPlaceholderText("/path/to/extra/repo")
        self._path_edit.editingFinished.connect(self._on_edit)
        h.addWidget(self._path_edit, 1)

        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")
        self._status.setMinimumWidth(60)
        h.addWidget(self._status)
        self._update_status()

        rm = QPushButton("✕")
        rm.setFlat(True)
        rm.setFixedWidth(24)
        rm.setStyleSheet(
            f"QPushButton {{ color: {FG_DIM}; border: none; background: transparent; }}"
            f" QPushButton:hover {{ color: #ff6b6b; }}"
        )
        rm.clicked.connect(lambda: self._on_remove(self))
        h.addWidget(rm)

    def _on_edit(self) -> None:
        self._update_status()
        self._on_change()

    def _update_status(self) -> None:
        from pathlib import Path as _P
        p = self._path_edit.text().strip()
        if not p:
            self._status.setText("")
            return
        exists = _P(p).expanduser().exists()
        self._status.setText("exists" if exists else "not found")
        self._status.setStyleSheet(
            f"color: {OK}; font-size: 11px;" if exists
            else f"color: {WARN}; font-size: 11px;"
        )

    def path(self) -> str:
        return self._path_edit.text().strip()


class _ExtraPathsSection(QWidget):
    """Collapsible extra local paths panel for one root row.

    Reads/writes <root>/.docgraph/repos.json directly. Paths listed here
    are indexed and wiki-fied into the same root's graph without copying
    their content — uses docgraph's Config.extra_roots at index time.
    """

    def __init__(self, path: str, window,
                 on_commit: Callable[[], None]) -> None:
        super().__init__()
        self._path = path
        self._window = window
        self._on_commit = on_commit
        self._expanded = False
        self._mtime: float = 0.0
        self._row_widgets: list[_ExtraPathRow] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 0)
        outer.setSpacing(4)

        hdr = QWidget()
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        self._toggle_btn = QPushButton("▶ Extra local paths")
        self._toggle_btn.setFlat(True)
        self._toggle_btn.setStyleSheet(
            f"QPushButton {{ color: {FG_DIM}; font-size: 11px; border: none; "
            f"background: transparent; text-align: left; padding: 0; }}"
            f" QPushButton:hover {{ color: {FG}; }}"
        )
        self._toggle_btn.clicked.connect(self._toggle)
        hl.addWidget(self._toggle_btn)
        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")
        hl.addWidget(self._summary_lbl)
        hl.addStretch(1)
        outer.addWidget(hdr)

        self._body = QWidget()
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(12, 0, 0, 0)
        bl.setSpacing(4)

        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        bl.addWidget(self._rows_host)

        add_btn = QPushButton("+ Add path")
        add_btn.setProperty("class", "ghost")
        add_btn.setMaximumWidth(110)
        add_btn.clicked.connect(self._on_add)
        bl.addWidget(add_btn)

        self._body.setVisible(False)
        outer.addWidget(self._body)

        self.refresh()

    def set_path(self, path: str) -> None:
        if path != self._path:
            self._path = path
            self._mtime = 0.0

    def _repos_path(self):
        from pathlib import Path as _P
        if not self._path:
            return None
        return _P(self._path).expanduser() / ".docgraph" / "repos.json"

    def _read_paths(self) -> list[str]:
        from docgraph.config import root_extra_paths
        return root_extra_paths(self._path) if self._path else []

    def _write_paths(self, paths: list[str]) -> None:
        from docgraph.config import save_root_extra_paths
        if self._path:
            save_root_extra_paths(self._path, paths)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._toggle_btn.setText(
            "▼ Extra local paths" if self._expanded else "▶ Extra local paths"
        )
        if self._expanded:
            self._rebuild()

    def _rebuild(self) -> None:
        for w in self._row_widgets:
            w.setParent(None)
            w.deleteLater()
        self._row_widgets.clear()
        for p in self._read_paths():
            self._append_row(p)

    def _append_row(self, path_str: str) -> None:
        row = _ExtraPathRow(path_str, on_change=self._commit, on_remove=self._on_remove)
        self._rows_layout.addWidget(row)
        self._row_widgets.append(row)

    def _on_add(self) -> None:
        self._append_row("")
        # Don't commit — nothing to save until the user fills in the path.
        # Committing here writes an empty repos.json, causing refresh() to
        # see a stale mtime and rebuild away the new empty row.

    def _on_remove(self, row: "_ExtraPathRow") -> None:
        try:
            self._row_widgets.remove(row)
        except ValueError:
            pass
        row.setParent(None)
        row.deleteLater()
        self._commit()

    def _commit(self) -> None:
        paths = [r.path() for r in self._row_widgets if r.path()]
        self._write_paths(paths)
        self._mtime = 0.0
        self._update_header(paths)

    def _update_header(self, paths: list[str]) -> None:
        n = len(paths)
        self._summary_lbl.setText(f"({n})" if n else "")

    def refresh(self) -> None:
        p = self._repos_path()
        if p is None:
            self._update_header([])
            return
        try:
            mtime = p.stat().st_mtime if p.exists() else 0.0
        except OSError:
            mtime = 0.0

        paths = self._read_paths()
        self._update_header(paths)

        if not self._expanded:
            return

        if mtime != self._mtime:
            self._mtime = mtime
            if len(paths) != len(self._row_widgets):
                self._rebuild()


class _LinkRow(QWidget):
    """One external-link row: URL · depth · TTL · status · remove."""

    def __init__(self, entry: dict, on_change: Callable[[], None],
                 on_remove: Callable[["_LinkRow"], None]) -> None:
        super().__init__()
        self._on_change = on_change
        self._on_remove = on_remove

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)

        self._url = QLineEdit(str(entry.get("url", "")))
        self._url.setPlaceholderText("https://example.com/docs")
        self._url.editingFinished.connect(self._on_change)
        h.addWidget(self._url, 2)

        depth_edit = QLineEdit(str(max(0, int(entry.get("depth", 1)))))
        depth_edit.setFixedWidth(40)
        depth_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        depth_edit.setToolTip("Crawl depth (0 = seed page only, 1 = seed + its direct links, ...)")
        depth_edit.editingFinished.connect(self._on_depth_changed)
        self._depth = depth_edit
        h.addWidget(QLabel("Depth"))
        h.addWidget(depth_edit)

        mp_edit = QLineEdit(str(max(0, int(entry.get("max_pages", 0)))))
        mp_edit.setFixedWidth(46)
        mp_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mp_edit.setToolTip("Max pages to fetch (0 = unlimited). Use this to cap crawls at a site that has many links.")
        mp_edit.editingFinished.connect(self._on_max_pages_changed)
        self._max_pages = mp_edit
        h.addWidget(QLabel("Max"))
        h.addWidget(mp_edit)

        ttl_edit = QLineEdit(str(int(float(entry.get("ttl_hours", 24)))))
        ttl_edit.setFixedWidth(46)
        ttl_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ttl_edit.setToolTip("Hours before the URL is considered stale and re-fetched")
        ttl_edit.editingFinished.connect(self._on_ttl_changed)
        self._ttl = ttl_edit
        h.addWidget(QLabel("TTL"))
        h.addWidget(ttl_edit)

        self._status = QLabel(_link_status_text(entry))
        self._status.setStyleSheet(f"color: {FG_MUTE}; font-size: 10px;")
        self._status.setMaximumWidth(80)
        h.addWidget(self._status)

        rm = QPushButton("✕")
        rm.setFlat(True)
        rm.setFixedWidth(24)
        rm.setStyleSheet(
            f"QPushButton {{ color: {FG_DIM}; border: none; background: transparent; }}"
            f" QPushButton:hover {{ color: #ff6b6b; }}"
        )
        rm.clicked.connect(lambda: self._on_remove(self))
        h.addWidget(rm)

    def _on_depth_changed(self) -> None:
        try:
            v = max(0, int(self._depth.text()))
        except ValueError:
            v = 0
        self._depth.setText(str(v))
        self._on_change()

    def _on_max_pages_changed(self) -> None:
        try:
            v = max(0, int(self._max_pages.text()))
        except ValueError:
            v = 0
        self._max_pages.setText(str(v))
        self._on_change()

    def _on_ttl_changed(self) -> None:
        try:
            v = max(1, int(float(self._ttl.text())))
        except ValueError:
            v = 24
        self._ttl.setText(str(v))
        self._on_change()

    def to_dict(self) -> dict:
        try:
            depth = max(0, int(self._depth.text()))
        except ValueError:
            depth = 0
        try:
            max_pages = max(0, int(self._max_pages.text()))
        except ValueError:
            max_pages = 0
        try:
            ttl_hours = max(1, int(float(self._ttl.text())))
        except ValueError:
            ttl_hours = 24
        return {
            "url": self._url.text().strip(),
            "depth": depth,
            "max_pages": max_pages,
            "ttl_hours": float(ttl_hours),
            "last_fetched": None,
            "page_count": None,
        }

    def update_status(self, entry: dict) -> None:
        self._status.setText(_link_status_text(entry))


class _LinksSection(QWidget):
    """Collapsible external-links panel for one root row.

    Reads/writes <root>/.docgraph/links.json directly; does NOT touch
    settings.json so the links config lives with the repo data.
    """

    def __init__(self, path: str, window,
                 on_commit: Callable[[], None]) -> None:
        super().__init__()
        self._path = path
        self._window = window
        self._on_commit = on_commit
        self._expanded = False
        self._mtime: float = 0.0          # last .docgraph/links.json mtime
        self._row_widgets: list[_LinkRow] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 0)
        outer.setSpacing(4)

        # Header row (always visible)
        hdr = QWidget()
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        self._toggle_btn = QPushButton("▶ External links")
        self._toggle_btn.setFlat(True)
        self._toggle_btn.setStyleSheet(
            f"QPushButton {{ color: {FG_DIM}; font-size: 11px; border: none; "
            f"background: transparent; text-align: left; padding: 0; }}"
            f" QPushButton:hover {{ color: {FG}; }}"
        )
        self._toggle_btn.clicked.connect(self._toggle)
        hl.addWidget(self._toggle_btn)
        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")
        hl.addWidget(self._summary_lbl)
        hl.addStretch(1)
        outer.addWidget(hdr)

        # Collapsible body
        self._body = QWidget()
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(12, 0, 0, 0)
        bl.setSpacing(4)

        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        bl.addWidget(self._rows_host)

        add_btn = QPushButton("+ Add link")
        add_btn.setProperty("class", "ghost")
        add_btn.setMaximumWidth(110)
        add_btn.clicked.connect(self._on_add)
        bl.addWidget(add_btn)

        self._body.setVisible(False)
        outer.addWidget(self._body)

        self.refresh()

    def set_path(self, path: str) -> None:
        if path != self._path:
            self._path = path
            self._mtime = 0.0

    def _links_path(self):
        from pathlib import Path as _P
        if not self._path:
            return None
        p = _P(self._path).expanduser() / ".docgraph" / "links.json"
        return p

    def _read_links(self) -> list[dict]:
        from docgraph.config import root_links
        return root_links(self._path) if self._path else []

    def _write_links(self, links: list[dict]) -> None:
        from docgraph.config import save_root_links
        if self._path:
            save_root_links(self._path, links)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._toggle_btn.setText(
            "▼ External links" if self._expanded else "▶ External links"
        )
        if self._expanded:
            self._rebuild()

    def _rebuild(self) -> None:
        for w in self._row_widgets:
            w.setParent(None)
            w.deleteLater()
        self._row_widgets.clear()
        links = self._read_links()
        for entry in links:
            self._append_row(entry)

    def _append_row(self, entry: dict) -> None:
        row = _LinkRow(entry, on_change=self._commit, on_remove=self._on_remove)
        self._rows_layout.addWidget(row)
        self._row_widgets.append(row)

    def _on_add(self) -> None:
        self._append_row({"url": "", "depth": 1, "ttl_hours": 24.0,
                          "last_fetched": None, "page_count": None})
        # Don't commit — save happens via editingFinished once the user fills in the URL.

    def _on_remove(self, row: "_LinkRow") -> None:
        try:
            self._row_widgets.remove(row)
        except ValueError:
            pass
        row.setParent(None)
        row.deleteLater()
        self._commit()

    def _commit(self) -> None:
        links = [r.to_dict() for r in self._row_widgets if r.to_dict()["url"]]
        self._write_links(links)
        self._mtime = 0.0  # force header refresh
        self._update_header(links)

    def _update_header(self, links: list[dict]) -> None:
        n = len(links)
        stale = sum(
            1 for lk in links
            if lk.get("last_fetched") is None
            or (time.time() - float(lk["last_fetched"])) > float(lk.get("ttl_hours", 24)) * 3600
        )
        parts = [f"{n}"] if n else []
        if stale:
            parts.append(f"{stale} stale")
        self._summary_lbl.setText(f"({', '.join(parts)})" if parts else "")
        self._summary_lbl.setStyleSheet(
            f"color: {WARN}; font-size: 11px;" if stale
            else f"color: {FG_MUTE}; font-size: 11px;"
        )

    def refresh(self) -> None:
        """Refresh header summary; if expanded, also update status labels."""
        p = self._links_path()
        if p is None:
            self._update_header([])
            return
        try:
            mtime = p.stat().st_mtime if p.exists() else 0.0
        except OSError:
            mtime = 0.0

        links = self._read_links()
        self._update_header(links)

        if not self._expanded:
            return

        # If the file changed under us, rebuild the rows.
        if mtime != self._mtime:
            self._mtime = mtime
            # Only full-rebuild if row count changed; otherwise just update status.
            if len(links) != len(self._row_widgets):
                self._rebuild()
                return
        for row, entry in zip(self._row_widgets, links):
            row.update_status(entry)


# ── LLM card ─────────────────────────────────────────────────────────────

def _build_llm_card(window) -> tuple[QFrame, Callable[[], None] | None]:
    card, body = _card(
        "LLM augmentation",
        "Optional local LLM for index docstrings + wiki pages. The model "
        "field alone does NOT enable either feature — toggle each on below.",
    )
    body.addWidget(_toggle_row("docgraph.llm.docstrings", "Use LLM for docstrings",
                                "Generate one-sentence summaries during "
                                "indexing for entities with no native docstring. "
                                "Off by default.",
                                cli="--llm-docstrings"))
    body.addWidget(_toggle_row("docgraph.llm.wiki", "Use LLM for wiki",
                                "When off, wiki pages render the fact-sheet "
                                "fallback even if a model is configured.",
                                cli="--llm-wiki"))
    body.addWidget(_line_row("docgraph.llm.model", "Model",
                              "qwen3.6-35b",
                              "LLM id used by both features above.",
                              cli="--llm-model"))
    body.addWidget(_line_row("docgraph.llm.host", "Host", "localhost",
                              cli="--llm-host"))
    body.addWidget(_number_row("docgraph.llm.port", "Port", 1, 65535, 1, 0,
                                cli="--llm-port"))
    body.addWidget(_enum_row_strs(
        "docgraph.llm.format", "Format",
        [("OpenAI-compatible", "openai"), ("Anthropic-compatible", "anthropic")],
    ))
    body.addWidget(_number_row("docgraph.llm.max_tokens", "Index Max Tokens",
                                10, 4096, 50, 0, "",
                                "Default 150.",
                                cli="--llm-max-tokens"))
    body.addWidget(_number_row("docgraph.llm.max_tokens_wiki", "Wiki Max Tokens",
                                256, 32768, 256, 0, "",
                                "Default 4096.",
                                cli="--llm-max-tokens-wiki"))
    body.addWidget(_number_row("docgraph.llm.max_tokens_chat", "Chat Max Tokens",
                                0, 32768, 256, 0, "",
                                "Right-panel Chat tab cap. 0 = unlimited "
                                "(model writes until done on OpenAI-compatible "
                                "servers).",
                                cli="--llm-max-tokens-chat"))
    body.addWidget(_line_row("docgraph.llm.api_key", "API Key", "",
                              "Forwarded as Authorization / x-api-key per "
                              "format. Leave blank for unauthenticated local "
                              "servers (LM Studio / llama.cpp / Ollama).",
                              cli="--llm-api-key"))
    body.addWidget(_number_row("docgraph.llm.timeout", "Timeout (s)",
                                5, 600, 5, 0, "s",
                                "Per-request HTTP timeout. Wiki page generation "
                                "on big modules can take 30s+ on local LLMs.",
                                cli="--llm-timeout"))
    body.addWidget(_number_row("docgraph.wiki.depth", "Wiki folder depth",
                                1, 32, 1, 0, "",
                                "Levels deep to bucket files. 1 = top-level only, 12 = leaf folders.",
                                cli="--wiki-depth"))
    return card, None


# ── Embeddings card ─────────────────────────────────────────────────────

# ── LLM prompt overrides ───────────────────────────────────────────────

def _build_prompts_card(window) -> tuple[QFrame, Callable[[], None] | None]:
    """Two text editors that override docgraph's built-in LLM prompts.

    Stored at `docgraph.llm.prompts.docstring` / `.wiki` in settings.
    Telecode materializes the override text to a temp file and passes
    `--llm-prompt-docstring-file` / `--llm-prompt-wiki-file` to docgraph
    on host launch (and on index/wiki subprocesses). Empty value = use
    docgraph's built-in default."""
    from PySide6.QtWidgets import QPlainTextEdit
    from PySide6.QtGui import QFontDatabase

    card, body = _card(
        "LLM prompts",
        "Override docgraph's built-in prompts. Empty = built-in default. "
        "Docstring template MUST keep {kind} / {name} / {language} / {body}.",
    )

    def _editor(setting_path: str, label: str, help_text: str,
                cli_flag: str, height: int) -> tuple[QWidget, QWidget]:
        """Returns (editor_row, actions_row) — both fully shaped via `_row()`."""
        te = QPlainTextEdit()
        te.setFixedHeight(height)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPointSize(10)
        te.setFont(mono)
        cur = str(get_path(read_settings(), setting_path, "") or "")
        te.setPlaceholderText("(empty — using built-in default)")
        te.setPlainText(cur)
        # Cap so the editor doesn't span the entire window on wide
        # displays — same rationale as `_line_row`'s 720 cap.
        te.setMaximumWidth(720)

        save_btn = QPushButton("Save")
        save_btn.setProperty("class", "primary")
        clear_btn = QPushButton("Clear (use built-in)")
        clear_btn.setProperty("class", "ghost")
        info_lbl = QLabel("")
        info_lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")
        info_lbl.setWordWrap(True)

        actions = QWidget()
        ar = QHBoxLayout(actions); ar.setContentsMargins(0, 0, 0, 0); ar.setSpacing(8)
        ar.addWidget(save_btn); ar.addWidget(clear_btn)
        ar.addWidget(info_lbl, 1)

        def _show_info(msg: str) -> None:
            from PySide6.QtCore import QTimer
            info_lbl.setStyleSheet(f"color: {OK}; font-size: 11px;")
            info_lbl.setText(msg)
            QTimer.singleShot(4000, lambda: info_lbl.setText(""))

        def _save():
            patch_settings(setting_path, te.toPlainText())
            _show_info("Saved. Restart the host to apply.")

        def _clear():
            te.setPlainText("")
            patch_settings(setting_path, "")
            _show_info("Cleared. docgraph will use its built-in default. Restart the host.")

        save_btn.clicked.connect(_save)
        clear_btn.clicked.connect(_clear)

        editor_row = _row(row_label(label, help_text, setting_path, cli=cli_flag), te)
        actions_row = _row(row_label("Actions"), actions)
        return editor_row, actions_row

    er, ar = _editor(
        "docgraph.llm.prompts.docstring",
        "Docstring template",
        "Used by `docgraph index --llm-docstrings`.",
        "--llm-prompt-docstring-file",
        height=140,
    )
    body.addWidget(er); body.addWidget(ar)

    body.addWidget(_section_header("Wiki"))
    er, ar = _editor(
        "docgraph.llm.prompts.wiki",
        "Wiki output-format tail",
        "Used by `docgraph wiki`. Replaces the trailing output-format block.",
        "--llm-prompt-wiki-file",
        height=140,
    )
    body.addWidget(er); body.addWidget(ar)
    return card, None


# Curated embedding model dropdown.
#
# All entries are fastembed-native (verified against
# `TextEmbedding.list_supported_models()`). Ordered by popularity + quality:
# the BGE family covers most code/RAG installs; jina-v3 + e5-large are the
# frontier multilingual picks; jina-v2-base-code is the only code-specialized
# fastembed model; mxbai-large was MTEB top-of-list through 2024-2025;
# all-MiniLM-L6-v2 is the historically most-downloaded sentence embedding
# (Continue.dev's default, LangChain/ChromaDB common pick).
#
# DocGraph auto-derives the Kuzu schema dim from the chosen model — switching
# to a different-dim model requires `Clear` + full reindex (existing vectors
# are wrong-shape under a new dim).
def _restart_host_row(window) -> QWidget:
    """A compact 'Restart host' button. Lives inside cards whose settings
    only take effect on the next host spawn (Embeddings, Reranker).
    Disabled when the host is already stopped."""
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 4, 0, 0); h.setSpacing(8)
    btn = QPushButton("🔄")
    btn.setProperty("class", "ghost")
    btn.setToolTip(
        "Stops the running docgraph host and starts a fresh one so the\n"
        "settings on this card take effect. Equivalent to Host → Restart."
    )

    def _on_restart():
        async def _go():
            from docgraph.process import get_host
            sup = get_host()
            try:
                await sup.stop()
            except Exception:
                pass
            await sup.start()
        _run(window, _go)

    def _refresh_enabled():
        try:
            from docgraph.process import status_snapshot
            alive = bool((status_snapshot().get("host") or {}).get("alive"))
        except Exception:
            alive = False
        btn.setEnabled(alive)
        btn.setToolTip(
            btn.toolTip() if alive else
            "Host is not running — start it from the Host card first."
        )

    btn.clicked.connect(_on_restart)
    h.addWidget(btn)
    h.addStretch(1)
    _refresh_enabled()
    # No periodic refresh wired in; if the host dies between renders the
    # next click is a no-op (sup.stop swallows, sup.start surfaces the error).
    return row


def _hf_model_row(window, path: str, label: str, default_model: str,
                  help_text: str = "", cli: str = "") -> QWidget:
    """Free-text HuggingFace model-ID row with an optional existence check.

    Saves on editingFinished (Enter / focus-out). The Check button fires an
    async HEAD request to huggingface.co/api/models/<id> and updates a status
    pill inline. Empty field = use the docgraph built-in default (shown as
    placeholder).
    """
    outer = QWidget()
    h = QHBoxLayout(outer)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)

    le = QLineEdit()
    le.setPlaceholderText(f"{default_model}  (default)")
    le.setText(str(get_path(read_settings(), path, "") or ""))
    le.setMaximumWidth(400)

    check_btn = QPushButton("Check")
    check_btn.setProperty("class", "ghost")
    check_btn.setFixedWidth(58)

    status_lbl = QLabel("")
    status_lbl.setMinimumWidth(86)
    status_lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")

    le.editingFinished.connect(lambda: patch_settings(path, le.text()))
    le.textChanged.connect(lambda: (
        status_lbl.setText(""),
        status_lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;"),
    ))

    async def _do_check():
        model_id = (le.text().strip() or default_model).strip("/")
        status_lbl.setText("checking…")
        status_lbl.setStyleSheet(f"color: {FG_MUTE}; font-size: 11px;")
        try:
            import aiohttp
            url = f"https://huggingface.co/api/models/{model_id}"
            async with aiohttp.ClientSession() as sess:
                async with sess.head(url, timeout=aiohttp.ClientTimeout(total=8),
                                     allow_redirects=True) as resp:
                    if resp.status == 200:
                        status_lbl.setText("✓ found")
                        status_lbl.setStyleSheet(f"color: {OK}; font-size: 11px;")
                    elif resp.status == 404:
                        status_lbl.setText("✗ not found")
                        status_lbl.setStyleSheet(f"color: {ERR}; font-size: 11px;")
                    else:
                        status_lbl.setText(f"? {resp.status}")
                        status_lbl.setStyleSheet(f"color: {WARN}; font-size: 11px;")
        except Exception:
            status_lbl.setText("error")
            status_lbl.setStyleSheet(f"color: {ERR}; font-size: 11px;")

    check_btn.clicked.connect(lambda: _run(window, _do_check))

    h.addWidget(le, 1)
    h.addWidget(check_btn)
    h.addWidget(status_lbl)
    h.addStretch(0)

    return _row(row_label(label, help_text, path, cli), outer)


# Default model IDs for placeholder text (= docgraph built-in defaults when field is empty).
_DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
_DEFAULT_RERANK_MODEL = "jinaai/jina-reranker-v1-tiny-en"


def _build_embeddings_card(window) -> tuple[QFrame, Callable[[], None] | None]:
    card, body = _card(
        "Embeddings",
        "Shared by index runs + host process.",
    )
    body.addWidget(_hf_model_row(window, "docgraph.embeddings.model", "Model",
                                 _DEFAULT_EMBED_MODEL,
                                 "HuggingFace sentence-transformers model id. "
                                 "Schema dim auto-aligns to model. "
                                 "Switching to a different-dim model = Clear + reindex.",
                                 cli="--embed-model"))
    body.addWidget(_toggle_row("docgraph.embeddings.gpu", "GPU embeddings",
                                "NVIDIA CUDA via torch. Requires a `+cuXY` "
                                "torch wheel installed in the docgraph venv "
                                "(see docgraph's setup.ps1). Falls back to "
                                "CPU silently if torch.cuda.is_available() "
                                "is False, and mid-run on OOM / driver errors.",
                                cli="--gpu"))
    body.addWidget(_toggle_row("docgraph.embeddings.torch_compile", "torch.compile (embeddings)",
                                "Apply torch.compile(mode='reduce-overhead') "
                                "to the embedder. Pays ~10-30s extra cold-start "
                                "for ~1.3-1.6× steady-state speedup on GPU. "
                                "Worth it for long-lived host processes; not "
                                "for one-shot index runs.",
                                cli="--embed-torch-compile"))
    body.addWidget(_number_row("docgraph.index.workers", "Index workers",
                                0, 64, 1, 0, "", "0 = default.",
                                cli="--workers"))
    body.addWidget(_number_row("docgraph.index.embed_batch_size", "Embed batch size",
                                0, 1024, 16, 0, "", "0 = default (256 CPU / 32 GPU). Lower if GPU saturates.",
                                cli="--embed-batch-size"))
    body.addWidget(_row(row_label(
        "Auto-Unload",
        "Evict the embedding ONNX session after this many seconds of "
        "idleness. 0 = never. Reloads lazily on next embed. Takes effect "
        "on next host spawn.",
        "docgraph.embeddings.idle_unload_sec",
        "--embed-idle-unload-sec",
    ), _idle_unload_row("docgraph.embeddings.idle_unload_sec", 300)))
    return card, None


def _build_reranker_card(window) -> tuple[QFrame, Callable[[], None] | None]:
    card, body = _card(
        "Reranker",
        "Cross-encoder over the top ~50 search candidates. Off by default.",
    )
    body.addWidget(_toggle_row("docgraph.rerank.default", "Always rerank",
                                "Default rerank=true on /api/search + MCP search. "
                                "Costs one cross-encoder pass per query."))
    body.addWidget(_hf_model_row(window, "docgraph.rerank.model", "Model",
                                 _DEFAULT_RERANK_MODEL,
                                 "HuggingFace cross-encoder model id. "
                                 "Lazy-loaded on first reranked search.",
                                 cli="--rerank-model"))
    body.addWidget(_toggle_row("docgraph.rerank.gpu", "GPU reranker",
                                "Cross-encoder on NVIDIA CUDA via torch. "
                                "Independent of embeddings GPU. Same `+cuXY` "
                                "torch wheel requirement; same CPU fallback "
                                "on init failure.",
                                cli="--rerank-gpu"))
    body.addWidget(_toggle_row("docgraph.rerank.torch_compile", "torch.compile (reranker)",
                                "Apply torch.compile to the cross-encoder. "
                                "Independent of the embedder flag — same "
                                "trade-off (slower first call, faster steady "
                                "state on GPU).",
                                cli="--rerank-torch-compile"))
    body.addWidget(_row(row_label(
        "Auto-Unload",
        "Evict the cross-encoder after this many seconds of idleness. "
        "0 = never. Reloads lazily on next reranked search. Takes effect "
        "on next host spawn.",
        "docgraph.rerank.idle_unload_sec",
        "--rerank-idle-unload-sec",
    ), _idle_unload_row("docgraph.rerank.idle_unload_sec", 300)))
    return card, None
