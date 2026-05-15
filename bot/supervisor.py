"""BotSupervisor — optional Telegram bot lifecycle management.

Mirrors the HostSupervisor pattern from docgraph/process.py:
  start() / stop() / restart() / alive() / status_snapshot()
  Auto-restart watches Updater.running and respawns on crash.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import config

if TYPE_CHECKING:
    from telegram.ext import Application
    from sessions.manager import SessionManager

log = logging.getLogger("telecode.bot.supervisor")

_SUP: "BotSupervisor | None" = None


def get_supervisor() -> "BotSupervisor | None":
    return _SUP


def set_supervisor(sup: "BotSupervisor") -> None:
    global _SUP
    _SUP = sup


def status_snapshot() -> dict:
    sup = _SUP
    if sup is None:
        return {"alive": False, "busy": False, "last_error": None,
                "auto_start": config.telegram_auto_start(),
                "auto_restart": config.telegram_auto_restart()}
    return sup.status_snapshot()


class BotSupervisor:
    def __init__(self, app: "Application", mgr: "SessionManager") -> None:
        self._app = app
        self._mgr = mgr
        self._alive = False
        self._busy = False
        self._initialized = False
        self._last_error: str | None = None
        self._restart_task: asyncio.Task | None = None
        self._topic_task: asyncio.Task | None = None

    # ── Public API ────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._alive or self._busy:
            return
        self._busy = True
        try:
            log.info("BotSupervisor: starting")
            if not self._initialized:
                # Deferred until first start so telecode can boot offline:
                # Application.initialize() calls bot.get_me() against
                # api.telegram.org.
                await self._app.initialize()
                self._initialized = True
            await self._app.start()
            await self._app.updater.start_polling(drop_pending_updates=True)
            self._alive = True
            self._last_error = None
            log.info("BotSupervisor: polling started")
            try:
                from bot.handlers import BOT_COMMANDS
                await self._app.bot.set_my_commands(BOT_COMMANDS)
                log.info("BotSupervisor: registered %d bot commands", len(BOT_COMMANDS))
            except Exception as exc:
                log.warning("set_my_commands failed: %s", exc)
            from bot.rate import topic_check_loop
            self._topic_task = asyncio.ensure_future(
                topic_check_loop(self._app.bot, self._mgr, interval_sec=60)
            )
            if config.telegram_auto_restart():
                self._arm_restart()
        except Exception as exc:
            self._last_error = str(exc)
            log.error("BotSupervisor: start failed: %s", exc, exc_info=True)
            raise
        finally:
            self._busy = False

    async def stop(self) -> None:
        self._disarm_restart()
        self._cancel_topic_task()
        if not self._alive or self._busy:
            return
        self._busy = True
        try:
            log.info("BotSupervisor: stopping")
            await self._app.updater.stop()
            await self._app.stop()
            self._alive = False
            log.info("BotSupervisor: stopped")
        except Exception as exc:
            log.error("BotSupervisor: stop error: %s", exc, exc_info=True)
        finally:
            self._busy = False

    async def restart(self) -> None:
        log.info("BotSupervisor: restarting")
        await self.stop()
        await self.start()

    async def shutdown_app(self) -> None:
        """Tear down the PTB Application. No-op if initialize() never ran."""
        if not self._initialized:
            return
        try:
            await self._app.shutdown()
        finally:
            self._initialized = False

    def alive(self) -> bool:
        if not self._alive:
            return False
        updater = getattr(self._app, "updater", None)
        return bool(updater and updater.running)

    @property
    def busy(self) -> bool:
        return self._busy

    def last_error(self) -> str | None:
        return self._last_error

    def status_snapshot(self) -> dict:
        return {
            "alive":        self.alive(),
            "busy":         self._busy,
            "last_error":   self._last_error,
            "auto_start":   config.telegram_auto_start(),
            "auto_restart": config.telegram_auto_restart(),
        }

    # ── Internal ──────────────────────────────────────────────────────

    def _cancel_topic_task(self) -> None:
        if self._topic_task and not self._topic_task.done():
            self._topic_task.cancel()
        self._topic_task = None

    def _arm_restart(self) -> None:
        self._disarm_restart()
        self._restart_task = asyncio.ensure_future(self._restart_loop())

    def _disarm_restart(self) -> None:
        if self._restart_task and not self._restart_task.done():
            self._restart_task.cancel()
        self._restart_task = None

    async def _restart_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(2)
                if not self._alive or self._busy:
                    continue
                updater = getattr(self._app, "updater", None)
                if updater and not updater.running:
                    log.warning("BotSupervisor: updater stopped — auto-restarting")
                    self._alive = False
                    self._cancel_topic_task()
                    try:
                        await self.start()
                    except Exception as exc:
                        log.error("BotSupervisor: auto-restart failed: %s", exc)
        except asyncio.CancelledError:
            pass
