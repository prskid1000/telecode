"""Shared helpers for qt_sections: settings I/O, async dispatch, labels."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("telecode.tray.helpers")


# ── Settings I/O ─────────────────────────────────────────────────────

def settings_path() -> Path:
    return Path(os.environ.get("TELECODE_SETTINGS", "settings.json")).resolve()


def read_settings() -> dict:
    try:
        with open(settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.error("failed to read settings from %s: %s", settings_path(), exc)
        return {}


def get_path(d: dict, dotted: str, default=None):
    node: Any = d
    for k in _split_path(dotted):
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node


def _split_path(path: str) -> list[str]:
    """Split dotpath by '.' but allow escaping dots with '\\.'."""
    parts = []
    current = []
    escaped = False
    for char in path:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ".":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def _set_nested(root: dict, path: str, value: Any) -> None:
    """Write `value` at dotted `path` inside `root`. Intermediate dicts are
    created on demand. Unlike the previous version, every branch that can
    lose the write logs a warning — silent no-ops are how the voxtype
    hotkey-rebind bug hid for so long."""
    keys = _split_path(path)
    node = root
    for i, k in enumerate(keys[:-1]):
        if isinstance(node, list):
            if not k.isdigit():
                log.warning("_set_nested(%r): list index expected at %r, got %r — skipping",
                            path, ".".join(keys[:i+1]), k)
                return
            idx = int(k)
            if idx < 0 or idx >= len(node):
                log.warning("_set_nested(%r): list index %d out of range at %r — skipping",
                            path, idx, ".".join(keys[:i+1]))
                return
            node = node[idx]
            continue
        if not isinstance(node, dict):
            log.warning("_set_nested(%r): expected dict at %r, got %s — skipping",
                        path, ".".join(keys[:i+1]), type(node).__name__)
            return
        nxt = node.get(k)
        if nxt is None or not isinstance(nxt, (dict, list)):
            nxt = {}
            node[k] = nxt
        node = nxt
    last = keys[-1]
    if isinstance(node, dict):
        node[last] = value
    elif isinstance(node, list) and last.isdigit():
        idx = int(last)
        if 0 <= idx < len(node):
            node[idx] = value
        else:
            log.warning("_set_nested(%r): final list index %d out of range — skipping",
                        path, idx)
    else:
        log.warning("_set_nested(%r): final parent is %s (expected dict/list) — skipping",
                    path, type(node).__name__)


def patch_settings(path: str, value: Any) -> None:
    """Atomic write + config.reload(). Safe to call from the Qt thread."""
    import config as app_config
    sp = settings_path()
    raw = app_config.raw()
    _set_nested(raw, path, value)
    tmp = sp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, sp)
    try:
        app_config.reload()
    except Exception as exc:
        log.error("reload failed: %s", exc, exc_info=True)
    _emit_setting_changed(path)


def remove_path(path: str) -> None:
    """Delete a dotted key from settings.json (atomic write + reload)."""
    import config as app_config
    sp = settings_path()
    raw = app_config.raw()
    keys = _split_path(path)
    node: Any = raw
    for k in keys[:-1]:
        if not isinstance(node, dict) or k not in node:
            return
        node = node[k]
    if isinstance(node, dict):
        node.pop(keys[-1], None)
    tmp = sp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, sp)
    _emit_setting_changed(path)


# ── Settings-change signal bus ───────────────────────────────────────
# Lets dependent UI rows (Tier-2 grey-outs, Tier-1 mutual exclusions) react
# to settings writes without polling. patch_settings / remove_path emit
# `settingChanged(path)` after each successful write.
_SETTINGS_BUS: Any = None


def _get_bus() -> Any:
    global _SETTINGS_BUS
    if _SETTINGS_BUS is None:
        from PySide6.QtCore import QObject, Signal

        class _Bus(QObject):
            settingChanged = Signal(str)

        _SETTINGS_BUS = _Bus()
    return _SETTINGS_BUS


def settings_bus() -> Any:
    """The shared QObject emitting `settingChanged(str)` after every patch_settings / remove_path."""
    return _get_bus()


def _emit_setting_changed(path: str) -> None:
    # patch_settings / remove_path already handle config.reload(); just emit.
    try:
        _get_bus().settingChanged.emit(path)
    except Exception as exc:  # bus init can fail off the Qt thread; ignore
        log.debug("settings_bus emit failed for %s: %s", path, exc)


# ── Async dispatch onto the bot loop ─────────────────────────────────

def schedule(loop: asyncio.AbstractEventLoop, coro) -> None:
    """Fire-and-forget a coroutine on the bot's loop."""
    try:
        asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception as exc:
        log.warning("schedule failed: %s", exc)


def run_sync(loop: asyncio.AbstractEventLoop, coro, timeout: float = 10.0):
    """Block until coro completes on the bot loop. Use sparingly — the
    Qt thread stalls if the bot loop is busy."""
    try:
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)
    except Exception as exc:
        log.warning("run_sync failed: %s", exc)
        return None


# ── Label helpers ────────────────────────────────────────────────────

def humanize(name: str) -> str:
    if not name:
        return ""
    parts = name.replace("-", "_").split("_")
    return " ".join(p.capitalize() for p in parts if p)


def format_protocol(p: str) -> str:
    return {"openai": "OpenAI", "anthropic": "Anthropic"}.get(p, p.title())


# ── Status snapshot (direct reads from in-process globals) ───────────

def build_status() -> dict[str, Any]:
    """Assemble the live state dict for Status/Sessions/etc. Reads from
    the running supervisor / session manager directly — no HTTP."""
    import time

    try:
        from llamacpp import config as llama_cfg
        from process import _SUPERVISOR as sup
        llama_enabled = llama_cfg.enabled()
        llama = {
            "enabled": llama_enabled,
            "alive": bool(sup and sup.alive()),
            "active_model": sup.active_model() if sup else "",
            "registered_models": list(llama_cfg.models().keys()),
            "default_model": llama_cfg.default_model(),
        }
        if llama_enabled and sup:
            last = sup.last_used()
            idle_limit = llama_cfg.idle_unload_sec()
            idle_for = (time.time() - last) if last else 0.0
            idle_rem = max(0.0, idle_limit - idle_for) if (llama["alive"] and idle_limit > 0) else 0.0
            llama.update({
                "idle_unload_sec":    idle_limit,
                "inflight":           sup.inflight_count(),
                "idle_for_sec":       round(idle_for, 1),
                "idle_remaining_sec": round(idle_rem, 1),
            })
    except Exception:
        llama = {"enabled": False}

    try:
        from proxy import config as pc
        proxy = {
            "enabled":   pc.enabled(),
            "port":      pc.proxy_port(),
            "protocols": pc.protocols(),
        }
    except Exception:
        proxy = {"enabled": False}

    try:
        import config as ac
        mcp = {"enabled": ac.mcp_server_enabled(), "port": ac.mcp_server_port()}
        if mcp["enabled"]:
            from mcp_server.app import mcp_app
            mcp["registered_tools"] = [t.name for t in mcp_app._tool_manager.list_tools()]
        else:
            mcp["registered_tools"] = []
    except Exception:
        mcp = {"enabled": False, "registered_tools": []}

    try:
        from proxy.managed_tools import _REGISTRY
        from proxy.runtime_state import get_all
        ovr = get_all().get("managed_tools", {})
        managed = [{"name": n, "enabled": ovr.get(n, True)} for n in _REGISTRY]
    except Exception:
        managed = []

    try:
        from bot.rate import _session_mgr
        sessions: list[dict] = []
        if _session_mgr is not None:
            now = time.time()
            for user_id, user_sessions in _session_mgr._sessions.items():
                for key, s in user_sessions.items():
                    sessions.append({
                        "user_id":   user_id,
                        "key":       key,
                        "backend":   key.split(":", 1)[0] if ":" in key else key,
                        "thread_id": s.thread_id,
                        "alive":     bool(getattr(s.process, "alive", False)),
                        "turns":     getattr(s, "turn_count", 0),
                        "age_sec":   round(now - getattr(s, "created_at", now), 1),
                    })
    except Exception:
        sessions = []

    try:
        from docgraph.process import status_snapshot as _dg_status
        docgraph = _dg_status()
    except Exception:
        docgraph = {}

    return {
        "llama": llama, "proxy": proxy, "mcp": mcp,
        "managed": managed, "sessions": sessions,
        "docgraph": docgraph,
    }


# ── Tailscale Funnel status (TTL-cached, background refresh) ──────────
# `tailscale status --json` spawns a subprocess (up to a 5s timeout), so it
# must never run on the Qt thread. We cache the last result and refresh it in
# a daemon thread when stale, returning the cached value immediately. Safe to
# call from a 1s refresh loop — only one fetch is in flight at a time.
_TS_LOCK = threading.Lock()
_TS_CACHE: dict[str, Any] = {"fetched_at": 0.0, "data": None}
_TS_FETCHING = False
_TS_TTL_SEC = 15.0


def _tailscale_fetch() -> dict[str, Any]:
    """Blocking `tailscale status --json` → Self node summary."""
    info: dict[str, Any] = {"connected": False, "dns_name": "", "ips": []}
    if not shutil.which("tailscale"):
        info["error"] = "Tailscale not on PATH"
        return info
    try:
        kw: dict[str, Any] = dict(capture_output=True, text=True, timeout=5)
        if sys.platform == "win32":
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(["tailscale", "status", "--json"], **kw)
        if result.returncode != 0:
            info["error"] = (result.stderr or "tailscale status failed").strip()[:200] or "tailscale status failed"
            return info
        status = json.loads(result.stdout or "{}")
        self_node = status.get("Self", {}) or {}
        dns = (self_node.get("DNSName") or "").rstrip(".")
        backend = status.get("BackendState", "")
        info["dns_name"] = dns
        info["ips"] = list(self_node.get("TailscaleIPs", []) or [])
        info["backend_state"] = backend
        info["connected"] = bool(dns) and backend == "Running" and bool(self_node.get("Online", True))
        if not info["connected"] and not info.get("error"):
            info["error"] = f"Tailscale {backend or 'not running'}"
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return info


def tailscale_status(force: bool = False) -> dict[str, Any]:
    """Cached Tailscale Self status: {connected, dns_name, ips, backend_state, error?}.

    Returns the last cached value immediately; kicks off a background refresh
    when the cache is stale (TTL ~15s) or `force` is set. Before the first
    fetch lands, returns {connected: False, pending: True}."""
    global _TS_FETCHING
    now = time.time()
    with _TS_LOCK:
        data = _TS_CACHE["data"]
        fresh = data is not None and (now - _TS_CACHE["fetched_at"]) < _TS_TTL_SEC
        if fresh and not force:
            return data
        if _TS_FETCHING:
            return data if data is not None else {"connected": False, "pending": True}
        _TS_FETCHING = True

    def _worker() -> None:
        global _TS_FETCHING
        res: dict[str, Any] = {"connected": False, "error": "fetch failed"}
        try:
            res = _tailscale_fetch()
        finally:
            with _TS_LOCK:
                _TS_CACHE["data"] = res
                _TS_CACHE["fetched_at"] = time.time()
                _TS_FETCHING = False

    threading.Thread(target=_worker, name="ts-status", daemon=True).start()
    return data if data is not None else {"connected": False, "pending": True}


def tailscale_funnel_url(dns_name: str, https_port: int = 443) -> str:
    """Build the public funnel URL for a machine DNS name.

    Mirrors main._start_tailscale_funnels: funnel runs at the root path, the
    proxy on https 443 (no port suffix), the MCP server on 8443."""
    if not dns_name:
        return ""
    suffix = "" if https_port == 443 else f":{https_port}"
    return f"https://{dns_name}{suffix}"
