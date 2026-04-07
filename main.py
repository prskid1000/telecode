"""Telecode — entry point."""
import asyncio
import logging
import os
import sys

from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters,
)

import config
from sessions.manager import SessionManager
from voice.health import probe, probe_loop
from bot.handlers import (
    cmd_start, cmd_help, cmd_new, cmd_stop,
    cmd_voice, cmd_settings, cmd_key,
    cmd_pause, cmd_resume,
    handle_callback, handle_text, handle_voice_msg, handle_document,
    BOT_COMMANDS,
)
from bot.rate import set_session_manager
from proxy.server import start_proxy_background


def _setup_logging() -> None:
    os.makedirs(config.logs_dir(), exist_ok=True)
    handlers = []
    # Stream handler — only if stdout is available (not pythonw)
    if sys.stdout is not None:
        try:
            _utf8_stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False)
            handlers.append(logging.StreamHandler(_utf8_stdout))
        except (AttributeError, OSError):
            pass  # pythonw or no console
    file_handler = logging.FileHandler(
        os.path.join(config.logs_dir(), "telecode.log"), encoding="utf-8"
    )
    handlers.append(file_handler)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


async def _post_init(app) -> None:
    log = logging.getLogger("telecode.main")
    os.makedirs(os.path.dirname(config.store_path()) or ".", exist_ok=True)
    log.info("Store path: %s", config.store_path())

    warnings = config.validate()
    for w in warnings:
        log.warning(w)

    await app.bot.set_my_commands(BOT_COMMANDS)
    log.info("Registered %d commands with Telegram", len(BOT_COMMANDS))

    vs = await probe()
    log.info("Voice: STT=%s", "OK" if vs.stt_available else "unavailable")
    app.bot_data["_probe_task"] = asyncio.ensure_future(probe_loop(60))

    # Start tool-search proxy
    runner = await start_proxy_background()
    if runner:
        app.bot_data["_proxy_runner"] = runner


async def _post_shutdown(app) -> None:
    # Stop proxy
    runner = app.bot_data.get("_proxy_runner")
    if runner:
        await runner.cleanup()

    for key in ("_probe_task", "_stale_check_task"):
        task = app.bot_data.get(key)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def main() -> None:
    _setup_logging()
    log = logging.getLogger("telecode.main")

    try:
        token = config.telegram_token()
    except (KeyError, FileNotFoundError) as e:
        print(str(e))
        sys.exit(1)

    log.info("Starting Telecode")

    app = (Application.builder().token(token)
           .post_init(_post_init)
           .post_shutdown(_post_shutdown)
           .build())
    mgr = SessionManager()
    app.bot_data["session_manager"] = mgr
    set_session_manager(mgr)

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("new",      cmd_new))
    app.add_handler(CommandHandler("stop",     cmd_stop))
    app.add_handler(CommandHandler("voice",    cmd_voice))
    app.add_handler(CommandHandler("settings", cmd_settings))

    app.add_handler(CommandHandler("key", cmd_key))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))

    app.add_handler(CallbackQueryHandler(handle_callback))

    app.add_handler(MessageHandler(filters.Document.ALL,           handle_document))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO,  handle_voice_msg))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log.info("Bot running. Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
