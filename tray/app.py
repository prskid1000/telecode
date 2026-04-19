"""In-process tray UI. Runs in a daemon thread spawned by main.py.

No subprocess, no HTTP, no singleton socket. Menu handlers call directly
into the LlamaSupervisor / SessionManager / proxy modules. Anything
async is scheduled onto the bot's main asyncio loop via
`asyncio.run_coroutine_threadsafe`.

Conditional disabling (enabled=callable):
  - llama/proxy/mcp sub-items grey out when their `Enabled` flag is off
  - Load/Unload/Restart gate on supervisor.alive()
  - Reasoning sub-toggles gate on the parent reasoning flags
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pystray

from tray import icon as icon_factory

log = logging.getLogger("telecode.tray")


# ══════════════════════════════════════════════════════════════════════
# Preset tables (same as before)
# ══════════════════════════════════════════════════════════════════════

PRESETS: dict[str, list[Any]] = {
    "llamacpp.idle_unload_sec":             [0, 60, 300, 600, 1800],
    "llamacpp.ready_timeout_sec":           [60, 120, 300, 600],
    "llamacpp.inference.temperature":       [0.0, 0.3, 0.6, 0.8, 1.0, 1.2],
    "llamacpp.inference.top_p":             [0.80, 0.90, 0.95, 1.00],
    "llamacpp.inference.top_k":             [0, 10, 20, 40, 100],
    "llamacpp.inference.min_p":             [0, 0.05, 0.10, 0.20],
    "llamacpp.inference.repeat_penalty":    [1.00, 1.05, 1.10, 1.20],
    "llamacpp.inference.presence_penalty":  [0, 0.5, 1.0, 1.5],
    "llamacpp.inference.context_overflow":  ["truncate_middle", "truncate", "error"],
    "proxy.max_roundtrips":                 [10, 15, 20, 30],
    "proxy.ping_interval":                  [5, 10, 20, 30],
    "streaming.interval_sec":               [0.5, 0.8, 1.0, 1.5, 2.0],
    "streaming.max_message_length":         [2000, 3000, 3800, 4096],
    "streaming.idle_timeout_sec":           [600, 1200, 1800, 3600, 7200],
    "streaming.idle_sec":                   [1.0, 2.0, 3.0, 5.0],
    "streaming.max_wait_sec":               [3.0, 5.0, 10.0, 15.0],
    "capture.image_interval":               [5, 10, 15, 30, 60],
    "capture.video_interval":               [30, 60, 120, 300],
    "tools.computer.api.format":            ["openai", "anthropic"],
    "tools.computer.capture_interval":      [1, 2, 3, 5],
    "tools.computer.max_history":           [10, 20, 50],
}

LABELS: dict[str, str] = {
    "llamacpp.idle_unload_sec":            "Idle Unload",
    "llamacpp.ready_timeout_sec":          "Ready Timeout",
    "llamacpp.inference.temperature":      "Temperature",
    "llamacpp.inference.top_p":            "Top-P",
    "llamacpp.inference.top_k":            "Top-K",
    "llamacpp.inference.min_p":            "Min-P",
    "llamacpp.inference.repeat_penalty":   "Repeat Penalty",
    "llamacpp.inference.presence_penalty": "Presence Penalty",
    "llamacpp.inference.context_overflow": "Context Overflow",
    "proxy.max_roundtrips":                "Max Round-Trips",
    "proxy.ping_interval":                 "Ping Interval",
    "streaming.interval_sec":              "Edit Interval",
    "streaming.max_message_length":        "Max Message Length",
    "streaming.idle_timeout_sec":          "Session Idle Timeout",
    "streaming.idle_sec":                  "PTY Idle Threshold",
    "streaming.max_wait_sec":              "PTY Max Wait",
    "capture.image_interval":              "Image Interval",
    "capture.video_interval":              "Video Chunk",
    "tools.computer.api.format":           "API Format",
    "tools.computer.capture_interval":     "Capture Interval",
    "tools.computer.max_history":          "Max History",
}


def _humanize(name: str) -> str:
    """Tool IDs like 'web_search' / 'code_execution' → 'Web Search' / 'Code Execution'
    for display in the tray menu. The underlying registered name is unchanged."""
    if not name:
        return ""
    parts = name.replace("-", "_").split("_")
    return " ".join(p.capitalize() for p in parts if p)


def _fmt(path: str, val) -> str:
    if "interval" in path or "timeout" in path or "unload" in path or "wait" in path:
        if isinstance(val, (int, float)):
            v = float(val)
            if v == 0:
                return "Never" if "unload" in path else "0"
            if v >= 60 and v % 60 == 0:
                return f"{int(v/60)} min" if v < 3600 else f"{int(v/3600)} h"
            return f"{v:g} s"
    if path.endswith("max_message_length"):
        return str(int(val))
    if isinstance(val, float):
        return f"{val:g}"
    return str(val)


# ══════════════════════════════════════════════════════════════════════
# Settings helpers
# ══════════════════════════════════════════════════════════════════════

def _settings_path() -> Path:
    return Path(os.environ.get("TELECODE_SETTINGS", "settings.json")).resolve()


def _read_settings() -> dict:
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _get_path(d: dict, dotted: str, default=None):
    node: Any = d
    for k in dotted.split("."):
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node


def _set_nested(root: dict, path: str, value: Any) -> None:
    keys = path.split(".")
    node = root
    for k in keys[:-1]:
        if isinstance(node, list) and k.isdigit():
            node = node[int(k)]
            continue
        if not isinstance(node, dict):
            return
        nxt = node.get(k)
        if nxt is None or not isinstance(nxt, (dict, list)):
            nxt = {}
            node[k] = nxt
        node = nxt
    last = keys[-1]
    if isinstance(node, dict):
        node[last] = value


def _patch_settings(path: str, value: Any) -> None:
    """Atomic write + config.reload(). Called from tray thread."""
    import config as app_config
    sp = _settings_path()
    raw = app_config.raw()
    _set_nested(raw, path, value)
    tmp = sp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, sp)
    try:
        app_config.reload()
    except Exception as exc:
        log.error("reload failed: %s", exc, exc_info=True)


# ══════════════════════════════════════════════════════════════════════
# TrayApp
# ══════════════════════════════════════════════════════════════════════

class TrayApp:
    def __init__(self, app, loop: asyncio.AbstractEventLoop) -> None:
        """
        Args:
            app: the python-telegram-bot Application (used to stop the bot on Quit)
            loop: the bot's asyncio event loop (used for scheduling async calls)
        """
        self._app = app
        self._loop = loop
        self._icon: pystray.Icon | None = None
        self._stop = threading.Event()
        self._status_cache: dict[str, Any] = {}
        self._stop_requested = False

    # ── Action factory ───────────────────────────────────────────────
    # pystray's _assert_action rejects callables with co_argcount > 2.
    # `lambda _i, _it, key=value: ...` has argcount=3 because the kwarg
    # default counts. Use this helper to produce a clean 2-arg action
    # that closes over the bound values via partial application.

    @staticmethod
    def _act(fn, *bound):
        """Return a (icon, item) callable that calls fn(*bound)."""
        return lambda _icon=None, _item=None: fn(*bound)

    # ── Async helpers ────────────────────────────────────────────────

    def _run_async(self, coro, timeout: float = 60.0):
        """Schedule coro on the bot's loop, block until done (or timeout).
        Use for tray menu actions that need an async call."""
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return fut.result(timeout=timeout)
        except Exception as exc:
            log.warning("async call failed: %s", exc)
            return None

    def _run_async_fire_and_forget(self, coro) -> None:
        try:
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        except Exception as exc:
            log.warning("async schedule failed: %s", exc)

    # ── Status (direct reads, no HTTP) ────────────────────────────────

    def _build_status(self) -> dict[str, Any]:
        """Assemble the live status dict used by the menu + tooltip."""
        try:
            from llamacpp import config as llama_cfg
            from llamacpp.supervisor import get_supervisor
            sup = self._run_async(get_supervisor(), timeout=5) if False else None
            # get_supervisor is an async function — but accessing the module
            # global directly works (it's the same singleton).
            from llamacpp.supervisor import _SUPERVISOR
            sup = _SUPERVISOR
            llama_enabled = llama_cfg.enabled()
            llama = {
                "enabled": llama_enabled,
                "alive": bool(sup and sup.alive()),
                "active_model": sup.active_model() if sup else "",
                "registered_models": list(llama_cfg.models().keys()),
                "default_model": llama_cfg.default_model(),
            }
            if llama_enabled and sup:
                last_used = sup.last_used()
                idle_limit = llama_cfg.idle_unload_sec()
                idle_for = (time.time() - last_used) if last_used else 0.0
                idle_remaining = max(0.0, idle_limit - idle_for) if (llama["alive"] and idle_limit > 0) else 0.0
                llama.update({
                    "idle_unload_sec": idle_limit,
                    "inflight": sup.inflight_count(),
                    "idle_for_sec": round(idle_for, 1),
                    "idle_remaining_sec": round(idle_remaining, 1),
                })
        except Exception:
            llama = {"enabled": False}

        try:
            from proxy import config as proxy_config
            proxy = {
                "enabled": proxy_config.enabled(),
                "port":    proxy_config.proxy_port(),
                "protocols": proxy_config.protocols(),
            }
        except Exception:
            proxy = {"enabled": False}

        try:
            import config as app_config
            mcp = {"enabled": app_config.mcp_server_enabled(),
                   "port": app_config.mcp_server_port()}
            if mcp["enabled"]:
                try:
                    from mcp_server.app import mcp_app
                    mcp["registered_tools"] = [t.name for t in mcp_app._tool_manager.list_tools()]
                except Exception:
                    mcp["registered_tools"] = []
            else:
                mcp["registered_tools"] = []
        except Exception:
            mcp = {"enabled": False}

        try:
            from proxy.managed_tools import _REGISTRY
            from proxy.runtime_state import get_all
            overrides = get_all().get("managed_tools", {})
            managed = {
                "tools": [
                    {"name": name, "enabled": overrides.get(name, True)}
                    for name in _REGISTRY
                ]
            }
        except Exception:
            managed = {"tools": []}

        try:
            from proxy.runtime_state import get_all as _ga
            mcp["tool_overrides"] = _ga().get("mcp_tools", {})
        except Exception:
            mcp["tool_overrides"] = {}

        try:
            from bot.rate import _session_mgr
            sessions: list[dict] = []
            if _session_mgr is not None:
                now = time.time()
                for user_id, user_sessions in _session_mgr._sessions.items():
                    for key, s in user_sessions.items():
                        sessions.append({
                            "user_id": user_id,
                            "key": key,
                            "alive": bool(getattr(s.process, "alive", False)),
                            "age_sec": round(now - getattr(s, "created_at", now), 1),
                        })
        except Exception:
            sessions = []

        return {
            "llama": llama,
            "proxy": proxy,
            "mcp": mcp,
            "managed": managed,
            "sessions": sessions,
        }

    def _poll_status(self) -> None:
        while not self._stop.is_set():
            try:
                self._status_cache = self._build_status()
                if self._icon is not None:
                    self._icon.title = self._tooltip()
            except Exception as exc:
                log.debug("status build failed: %s", exc)
            time.sleep(2.0)

    def _tooltip(self) -> str:
        llama = self._status_cache.get("llama") or {}
        parts = ["telecode"]
        if llama.get("enabled"):
            parts.append(llama.get("active_model") or "idle" if llama.get("alive") else "llama: idle")
        return " · ".join(parts)

    # ── Action: llama.cpp ────────────────────────────────────────────

    def _load_llama(self, *_a) -> None:
        async def _do():
            from llamacpp.supervisor import get_supervisor
            from llamacpp import config as llama_cfg
            sup = await get_supervisor()
            await sup.ensure_model(llama_cfg.default_model())
        self._run_async_fire_and_forget(_do())

    def _stop_llama(self, *_a) -> None:
        async def _do():
            from llamacpp.supervisor import get_supervisor
            sup = await get_supervisor()
            await sup.stop()
        self._run_async_fire_and_forget(_do())

    def _restart_llama(self, *_a) -> None:
        async def _do():
            from llamacpp.supervisor import get_supervisor
            sup = await get_supervisor()
            await sup.stop()
            await sup.start_default()
        self._run_async_fire_and_forget(_do())

    def _swap_llama(self, model: str) -> None:
        async def _do():
            from llamacpp.supervisor import get_supervisor
            sup = await get_supervisor()
            await sup.ensure_model(model)
        self._run_async_fire_and_forget(_do())

    # ── Action: managed / MCP runtime toggles ────────────────────────

    def _toggle_managed(self, name: str) -> None:
        from proxy.runtime_state import set_tool, get_all
        cur = get_all().get("managed_tools", {}).get(name, True)
        set_tool("managed_tools", name, not cur)
        # Also update the proxy's in-memory dict so the next request sees it
        try:
            from tray import state_bridge  # noqa
        except Exception:
            pass
        # In-memory overrides read by proxy.server live in the tray_api module
        # or moved? Actually moved — we need to update proxy/server.py's source
        # of truth directly now that tray_api is gone.
        try:
            import proxy.server as pserver
            if hasattr(pserver, "_RUNTIME_TOOL_OVERRIDES"):
                pserver._RUNTIME_TOOL_OVERRIDES[name] = not cur
        except Exception:
            pass

    def _toggle_mcp_tool(self, name: str) -> None:
        from proxy.runtime_state import set_tool, get_all
        cur = get_all().get("mcp_tools", {}).get(name, True)
        set_tool("mcp_tools", name, not cur)

    # ── Action: sessions ─────────────────────────────────────────────

    def _kill_session(self, user_id, key: str) -> None:
        async def _do():
            from bot.rate import _session_mgr
            if _session_mgr is not None:
                await _session_mgr.kill_session(int(user_id), key)
        self._run_async_fire_and_forget(_do())

    def _killall(self, *_a) -> None:
        async def _do():
            from bot.rate import _session_mgr
            if _session_mgr is None:
                return
            for uid in list(_session_mgr._sessions.keys()):
                await _session_mgr.kill_all_sessions(uid)
        self._run_async_fire_and_forget(_do())

    # ── Action: settings / misc ──────────────────────────────────────

    def _patch(self, path: str, value: Any) -> None:
        threading.Thread(target=lambda: _patch_settings(path, value), daemon=True).start()

    def _reload(self, *_a) -> None:
        import config as app_config
        try:
            app_config.reload()
        except Exception as exc:
            log.error("reload failed: %s", exc)

    def _toggle_protocol(self, name: str) -> None:
        from proxy import config as proxy_config
        protocols = set(proxy_config.protocols())
        if name in protocols:
            protocols.discard(name)
        else:
            protocols.add(name)
        ordered = [p for p in ("anthropic", "openai") if p in protocols]
        self._patch("proxy.protocols", ordered)

    @staticmethod
    def _open_path(p: Path | str) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(str(p))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception as exc:
            log.warning("open %s: %s", p, exc)

    def _project_path(self, *parts) -> Path:
        return _settings_path().parent.joinpath(*parts)

    def _open_settings(self, *_a) -> None:
        self._open_path(_settings_path())

    def _open_logs(self, *_a) -> None:
        self._open_path(self._project_path("data", "logs"))

    # ── Menu builders (same structure as before) ─────────────────────

    def _toggle_item(self, label: str, path: str,
                     enabled_when: Callable[[], bool] | None = None,
                     reload_marker: bool = False) -> pystray.MenuItem:
        suffix = "  ⟳" if reload_marker else ""
        return pystray.MenuItem(
            label + suffix,
            lambda *_: self._patch(path, not bool(_get_path(_read_settings(), path, False))),
            checked=lambda _it: bool(_get_path(_read_settings(), path, False)),
            enabled=enabled_when if enabled_when else lambda _it: True,
        )

    def _preset(self, path: str,
                enabled_when: Callable[[], bool] | None = None) -> pystray.MenuItem:
        label = LABELS.get(path, path.split(".")[-1])
        opts = PRESETS.get(path, [])
        def _build():
            cur = _get_path(_read_settings(), path)
            items: list[pystray.MenuItem] = []
            for v in opts:
                items.append(pystray.MenuItem(
                    _fmt(path, v),
                    self._act(self._patch, path, v),
                    radio=True,
                    checked=lambda _it, val=v: cur == val,
                ))
            return items
        return pystray.MenuItem(
            label, pystray.Menu(_build),
            enabled=enabled_when if enabled_when else lambda _it: True,
        )

    def _status_rows(self) -> list[pystray.MenuItem]:
        st = self._status_cache or {}
        llama = st.get("llama") or {}
        proxy = st.get("proxy") or {}
        mcp   = st.get("mcp")   or {}
        sessions = st.get("sessions") or []
        lines: list[str] = []
        if llama.get("enabled"):
            lines.append(
                f"⬢ llama: {llama.get('active_model','—')}"
                if llama.get("alive") else "⬡ llama: idle"
            )
        else:
            lines.append("⬡ llama: disabled")
        lines.append(
            f"⬢ proxy: :{proxy.get('port','?')} · {','.join(proxy.get('protocols', []))}"
            if proxy.get("enabled") else "⬡ proxy: disabled"
        )
        lines.append(
            f"⬢ mcp:   :{mcp.get('port','?')} · {len(mcp.get('registered_tools', []))} tools"
            if mcp.get("enabled") else "⬡ mcp: disabled"
        )
        alive = sum(1 for s in sessions if s.get("alive"))
        lines.append(f"⬢ sessions: {alive}/{len(sessions)}")
        return [pystray.MenuItem(l, None, enabled=False) for l in lines]

    # ── llama.cpp submenu ───────────────────────────────────────────

    def _llama_menu(self) -> pystray.Menu:
        def _build():
            settings = _read_settings()
            st = (self._status_cache or {}).get("llama") or {}
            enabled = bool(_get_path(settings, "llamacpp.enabled", False))
            alive = bool(st.get("alive"))
            active = st.get("active_model", "")
            models = st.get("registered_models") or list(_get_path(settings, "llamacpp.models", {}).keys())
            on = lambda _it=None: enabled

            items: list = [
                self._toggle_item("Enabled", "llamacpp.enabled", reload_marker=True),
                pystray.Menu.SEPARATOR,
            ]
            if models:
                items.append(pystray.MenuItem("Active Model:", None, enabled=False))
                for m in models:
                    items.append(pystray.MenuItem(
                        m,
                        self._act(self._swap_llama, m),
                        radio=True,
                        checked=lambda _it, model=m: model == active,
                        enabled=on,
                    ))
                items.append(pystray.Menu.SEPARATOR)
            items += [
                pystray.MenuItem("Load Now", self._load_llama,
                                 enabled=lambda _it: enabled and not alive),
                pystray.MenuItem("Unload",   self._stop_llama,
                                 enabled=lambda _it: enabled and alive),
                pystray.MenuItem("Restart",  self._restart_llama,
                                 enabled=lambda _it: enabled and alive),
                pystray.Menu.SEPARATOR,
                self._toggle_item("Auto-Start On Launch",  "llamacpp.auto_start",   enabled_when=on),
                self._preset("llamacpp.idle_unload_sec",   enabled_when=on),
                self._preset("llamacpp.ready_timeout_sec", enabled_when=on),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Sampling:", None, enabled=False),
                self._preset("llamacpp.inference.temperature",      enabled_when=on),
                self._preset("llamacpp.inference.top_p",            enabled_when=on),
                self._preset("llamacpp.inference.top_k",            enabled_when=on),
                self._preset("llamacpp.inference.min_p",            enabled_when=on),
                self._preset("llamacpp.inference.repeat_penalty",   enabled_when=on),
                self._preset("llamacpp.inference.presence_penalty", enabled_when=on),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Reasoning:", None, enabled=False),
                pystray.MenuItem(
                    "Use Reasoning",
                    lambda *_: self._patch(
                        "llamacpp.inference.disable_thinking",
                        not bool(_get_path(_read_settings(), "llamacpp.inference.disable_thinking", False)),
                    ),
                    checked=lambda _it: not bool(_get_path(_read_settings(), "llamacpp.inference.disable_thinking", False)),
                    enabled=on,
                ),
                self._toggle_item(
                    "Parse <think> Blocks",
                    "llamacpp.inference.reasoning.enabled",
                    enabled_when=lambda _it=None: enabled and not bool(
                        _get_path(_read_settings(), "llamacpp.inference.disable_thinking", False)
                    ),
                ),
                self._toggle_item(
                    "Show Reasoning In Output",
                    "llamacpp.inference.reasoning.emit_thinking_blocks",
                    enabled_when=lambda _it=None: (
                        enabled
                        and not bool(_get_path(_read_settings(), "llamacpp.inference.disable_thinking", False))
                        and bool(_get_path(_read_settings(), "llamacpp.inference.reasoning.enabled", True))
                    ),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Open llama.log",
                                 lambda *_: self._open_path(self._project_path("data", "logs", "llama.log"))),
            ]
            return items
        return pystray.Menu(_build)

    # ── Proxy + Profiles submenu ────────────────────────────────────

    def _proxy_menu(self) -> pystray.Menu:
        def _build():
            settings = _read_settings()
            enabled = bool(_get_path(settings, "proxy.enabled", False))
            on = lambda _it=None: enabled
            from proxy import config as proxy_config
            protocols = set(proxy_config.protocols())
            return [
                self._toggle_item("Enabled", "proxy.enabled", reload_marker=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Protocols:", None, enabled=False),
                pystray.MenuItem(
                    "Anthropic",
                    lambda *_: self._toggle_protocol("anthropic"),
                    checked=lambda _it: "anthropic" in protocols,
                    enabled=on,
                ),
                pystray.MenuItem(
                    "OpenAI",
                    lambda *_: self._toggle_protocol("openai"),
                    checked=lambda _it: "openai" in protocols,
                    enabled=on,
                ),
                pystray.Menu.SEPARATOR,
                self._toggle_item("Tool search (BM25)",     "proxy.tool_search",     enabled_when=on),
                self._toggle_item("Auto-load tool schemas", "proxy.auto_load_tools", enabled_when=on),
                self._toggle_item("Strip system reminders", "proxy.strip_reminders", enabled_when=on),
                self._toggle_item("Debug logging",          "proxy.debug",           enabled_when=on),
                pystray.Menu.SEPARATOR,
                self._preset("proxy.max_roundtrips", enabled_when=on),
                self._preset("proxy.ping_interval",  enabled_when=on),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Profiles", self._profiles_menu(), enabled=on),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Open telecode.log",
                                 lambda *_: self._open_path(self._project_path("data", "logs", "telecode.log"))),
            ]
        return pystray.Menu(_build)

    def _profiles_menu(self) -> pystray.Menu:
        def _build():
            profs = _get_path(_read_settings(), "proxy.client_profiles", []) or []
            if not profs:
                return [pystray.MenuItem("(no profiles)", None, enabled=False)]
            return [pystray.MenuItem(p.get("name") or f"profile-{i}", self._one_profile(i))
                    for i, p in enumerate(profs)]
        return pystray.Menu(_build)

    def _one_profile(self, idx: int) -> pystray.Menu:
        def _build():
            profs = _get_path(_read_settings(), "proxy.client_profiles", []) or []
            if idx >= len(profs):
                return [pystray.MenuItem("(gone)", None, enabled=False)]
            p = profs[idx]
            base = f"proxy.client_profiles.{idx}"

            # system_instruction = dropdown of proxy/instructions/*.md
            instr_dir = self._project_path("proxy", "instructions")
            try:
                instr_files = sorted(f.name for f in instr_dir.iterdir() if f.suffix == ".md")
            except Exception:
                instr_files = []
            cur_instr = p.get("system_instruction", "")

            # inject_managed = multi-checkbox of registered managed tools
            managed_names = [t.get("name") for t in (self._status_cache.get("managed", {}) or {}).get("tools", [])]
            cur_inject = set(p.get("inject_managed") or [])

            def _toggle_inject(name):
                cur = set(
                    (_read_settings().get("proxy", {}).get("client_profiles", []) or [{}])[idx]
                    .get("inject_managed") or []
                )
                if name in cur:
                    cur.discard(name)
                else:
                    cur.add(name)
                self._patch(f"{base}.inject_managed", sorted(cur))

            items: list = [pystray.MenuItem("System instruction:", None, enabled=False)]
            for fn in instr_files:
                items.append(pystray.MenuItem(
                    fn,
                    self._act(self._patch, f"{base}.system_instruction", fn),
                    radio=True,
                    checked=lambda _it, name=fn: cur_instr == name,
                ))
            items.append(pystray.Menu.SEPARATOR)
            items.append(pystray.MenuItem("Inject managed tools:", None, enabled=False))
            for n in managed_names:
                items.append(pystray.MenuItem(
                    _humanize(n),
                    self._act(_toggle_inject, n),
                    checked=lambda _it, name=n, ci=cur_inject: name in ci,
                ))
            items.append(pystray.Menu.SEPARATOR)
            for key, label in [
                ("tool_search",          "Tool search"),
                ("auto_load_tools",      "Auto-load tools"),
                ("strip_reminders",      "Strip reminders"),
                ("inject_date_location", "Inject date/location"),
            ]:
                items.append(self._toggle_item(label, f"{base}.{key}"))
            return items
        return pystray.Menu(_build)

    # ── MCP / Managed / Telegram / Voice / Computer / Sessions ──────

    def _mcp_menu(self) -> pystray.Menu:
        def _build():
            st = (self._status_cache or {}).get("mcp") or {}
            tools = st.get("registered_tools") or []
            items: list = [
                self._toggle_item("Enabled", "mcp_server.enabled", reload_marker=True),
            ]
            # MCP server's tool exposure is fixed at registration time; the
            # bridged versions live under "Managed Tools" where toggles
            # actually take effect on each request. Just show what's
            # registered for visibility.
            if tools:
                items.append(pystray.Menu.SEPARATOR)
                items.append(pystray.MenuItem("Registered:", None, enabled=False))
                for name in tools:
                    items.append(pystray.MenuItem(
                        f"  · {_humanize(name)}", None, enabled=False,
                    ))
            return items
        return pystray.Menu(_build)

    def _managed_menu(self) -> pystray.Menu:
        def _build():
            tools = (self._status_cache.get("managed", {}) or {}).get("tools", [])
            if not tools:
                return [pystray.MenuItem("(no managed tools)", None, enabled=False)]
            return [
                pystray.MenuItem(
                    _humanize(t.get("name", "?")),
                    self._act(self._toggle_managed, t.get("name")),
                    checked=lambda _it, n=t.get("name"), lookup=tools: next(
                        (x for x in lookup if x.get("name") == n), {}
                    ).get("enabled", True),
                )
                for t in tools
            ]
        return pystray.Menu(_build)

    def _telegram_menu(self) -> pystray.Menu:
        def _build():
            return [
                pystray.MenuItem("Streaming:", None, enabled=False),
                self._preset("streaming.interval_sec"),
                self._preset("streaming.max_message_length"),
                self._preset("streaming.idle_timeout_sec"),
                self._preset("streaming.idle_sec"),
                self._preset("streaming.max_wait_sec"),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Capture:", None, enabled=False),
                self._preset("capture.image_interval"),
                self._preset("capture.video_interval"),
            ]
        return pystray.Menu(_build)

    def _voice_menu(self) -> pystray.Menu:
        return pystray.Menu(lambda: [self._toggle_item("STT enabled", "voice.stt.enabled")])

    def _computer_menu(self) -> pystray.Menu:
        def _build():
            return [
                self._preset("tools.computer.api.format"),
                self._preset("tools.computer.capture_interval"),
                self._preset("tools.computer.max_history"),
            ]
        return pystray.Menu(_build)

    def _sessions_menu(self) -> pystray.Menu:
        def _build():
            sessions = self._status_cache.get("sessions", []) or []
            if not sessions:
                return [pystray.MenuItem("No active sessions", None, enabled=False)]
            items: list = [
                pystray.MenuItem(
                    f"{'●' if s.get('alive') else '○'} {s.get('key', '?')}",
                    self._act(self._kill_session, s.get("user_id"), s.get("key")),
                )
                for s in sessions[:10]
            ]
            items.append(pystray.Menu.SEPARATOR)
            items.append(pystray.MenuItem("Kill all", self._killall))
            return items
        return pystray.Menu(_build)

    # ── Root menu ───────────────────────────────────────────────────

    def _menu(self) -> pystray.Menu:
        def _root():
            return [
                *self._status_rows(),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("llama.cpp",     self._llama_menu()),
                pystray.MenuItem("Proxy",         self._proxy_menu()),
                pystray.MenuItem("MCP",           self._mcp_menu()),
                pystray.MenuItem("Managed tools", self._managed_menu()),
                pystray.MenuItem("Telegram",      self._telegram_menu()),
                pystray.MenuItem("Voice",         self._voice_menu()),
                pystray.MenuItem("Computer",      self._computer_menu()),
                pystray.MenuItem("Sessions",      self._sessions_menu()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Reload config",      self._reload),
                pystray.MenuItem("Open settings.json", self._open_settings, default=True),
                pystray.MenuItem("Open logs folder",   self._open_logs),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit telecode",      self._quit),
            ]
        return pystray.Menu(_root)

    # ── Lifecycle ────────────────────────────────────────────────────

    def _quit(self, *_a) -> None:
        """Graceful shutdown: signal the Telegram Application to stop.
        Once run_polling returns, the whole process exits."""
        if self._stop_requested:
            return
        self._stop_requested = True
        self._stop.set()
        log.info("tray: Quit requested — stopping bot")
        try:
            if self._icon is not None:
                self._icon.stop()
        except Exception:
            pass
        # Ask PTB to shut down from the bot's loop
        try:
            self._run_async_fire_and_forget(self._app.stop_running())
        except Exception as exc:
            log.warning("stop_running failed: %s", exc)

    def run(self) -> int:
        self._icon = pystray.Icon(
            "telecode",
            icon_factory.make_icon(),
            "telecode",
            menu=self._menu(),
        )
        threading.Thread(target=self._poll_status, daemon=True).start()
        self._icon.run()   # blocks this thread until icon.stop()
        return 0


# ══════════════════════════════════════════════════════════════════════
# Entry point used by main.py (spawns tray on a daemon thread)
# ══════════════════════════════════════════════════════════════════════

def start_tray_in_thread(app, loop: asyncio.AbstractEventLoop) -> threading.Thread:
    """Launch the tray on a background daemon thread. Returns the thread."""
    def _go():
        try:
            TrayApp(app=app, loop=loop).run()
        except Exception:
            log.exception("tray thread crashed")
    t = threading.Thread(target=_go, daemon=True, name="telecode-tray")
    t.start()
    return t
