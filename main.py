"""Telecode — entry point."""
import asyncio
import logging
import os
import shutil
import subprocess
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
    cmd_settings, cmd_key,
    handle_callback, handle_text, handle_voice_msg, handle_document,
    handle_forum_topic_closed,
    BOT_COMMANDS,
)
from bot.rate import set_session_manager
from proxy.server import start_proxy_background
from mcp_server.server import start_mcp_background


def _clear_previous_logs() -> None:
    """Delete telecode.log and proxy_full_*.json from the logs dir so every
    run starts with a clean slate. Best-effort — skips files held by other
    processes."""
    import glob
    logs_dir = config.logs_dir()
    if not os.path.isdir(logs_dir):
        return
    targets = [os.path.join(logs_dir, "telecode.log")]
    targets.extend(glob.glob(os.path.join(logs_dir, "proxy_full_*.json")))
    for path in targets:
        try:
            os.unlink(path)
        except OSError:
            pass  # file locked or doesn't exist


def _setup_logging() -> None:
    os.makedirs(config.logs_dir(), exist_ok=True)
    _clear_previous_logs()
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
    # A dependency may install a handler on the root logger during imports
    # (e.g. RichHandler). plain basicConfig() is then a no-op and telecode.log
    # never receives records — force=True replaces the root configuration.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def _start_tailscale_funnels(log: logging.Logger) -> list[subprocess.Popen]:
    """Start Tailscale Funnel subprocesses for proxy and MCP server.

    Returns list of Popen objects (empty if tailscale not found).
    Processes die automatically when the parent exits.
    """
    if not shutil.which("tailscale"):
        log.warning("Tailscale not found on PATH — HTTPS funnel not available. "
                     "Install Tailscale for external HTTPS access.")
        return []

    # Get the machine's Tailscale FQDN for logging the HTTPS URL
    ts_domain = ""
    try:
        cmd = ["tailscale", "status", "--json"]
        if sys.platform == "win32":
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            import json
            status = json.loads(result.stdout)
            ts_domain = status.get("Self", {}).get("DNSName", "").rstrip(".")
    except Exception as exc:
        log.warning("Could not resolve Tailscale FQDN (%s: %s); funnels will still start.",
                    type(exc).__name__, exc)

    funnels: list[subprocess.Popen] = []
    # Each entry: (https_port, local_port, label)
    # Funnel at root (no --set-path) so paths like /v1/messages arrive unchanged.
    routes: list[tuple[int, int, str]] = []

    if config.get_nested("proxy.enabled", False):
        port = int(config.get_nested("proxy.port", 1235))
        routes.append((443, port, "proxy"))

    if config.get_nested("mcp_server.enabled", False):
        port = int(config.get_nested("mcp_server.port", 1236))
        routes.append((8443, port, "mcp"))

    for https_port, local_port, label in routes:
        try:
            cmd = ["tailscale", "funnel", "--bg", "--https", str(https_port), str(local_port)]
            if sys.platform == "win32":
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            funnels.append(proc)
            port_suffix = "" if https_port == 443 else f":{https_port}"
            url = f"https://{ts_domain}{port_suffix}" if ts_domain else f"<tailscale>{port_suffix}"
            log.info("Tailscale Funnel (%s): %s -> localhost:%d (pid %d)", label, url, local_port, proc.pid)
        except Exception as exc:
            log.warning("Failed to start Tailscale Funnel for %s: %s", label, exc)

    return funnels


async def _post_init(app) -> None:
    log = logging.getLogger("telecode.main")
    os.makedirs(os.path.dirname(config.store_path()) or ".", exist_ok=True)
    log.info("Store path: %s", config.store_path())

    warnings = config.validate()
    for w in warnings:
        log.warning(w)

    try:
        await app.bot.set_my_commands(BOT_COMMANDS)
        log.info("Registered %d commands with Telegram", len(BOT_COMMANDS))
    except Exception as exc:
        log.error("set_my_commands failed: %s", exc, exc_info=True)

    try:
        vs = await probe()
        log.info("Voice: STT=%s", "OK" if vs.stt_available else "unavailable")
        app.bot_data["_probe_task"] = asyncio.ensure_future(probe_loop(60))
    except Exception as exc:
        log.error("Voice probe init failed: %s", exc, exc_info=True)

    try:
        runner = await start_proxy_background()
        if runner:
            app.bot_data["_proxy_runner"] = runner
    except Exception as exc:
        log.error("Proxy startup failed: %s", exc, exc_info=True)

    try:
        mcp_thread = start_mcp_background(config.mcp_server_host(), config.mcp_server_port())
        if mcp_thread:
            app.bot_data["_mcp_thread"] = mcp_thread
    except Exception as exc:
        log.error("MCP server startup failed: %s", exc, exc_info=True)

    try:
        funnels = _start_tailscale_funnels(log)
        if funnels:
            app.bot_data["_tailscale_funnels"] = funnels
    except Exception as exc:
        log.error("Tailscale funnel startup failed: %s", exc, exc_info=True)


async def _post_shutdown(app) -> None:
    # Stop proxy
    runner = app.bot_data.get("_proxy_runner")
    if runner:
        await runner.cleanup()

    # Stop Tailscale Funnel subprocesses — terminate then wait (with kill fallback).
    for proc in app.bot_data.get("_tailscale_funnels", []):
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        except Exception:
            pass

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
    app.add_handler(CommandHandler("settings", cmd_settings))

    app.add_handler(CommandHandler("key", cmd_key))

    app.add_handler(CallbackQueryHandler(handle_callback))

    app.add_handler(MessageHandler(filters.StatusUpdate.FORUM_TOPIC_CLOSED, handle_forum_topic_closed))
    app.add_handler(MessageHandler(filters.Document.ALL,           handle_document))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO,  handle_voice_msg))
    # Drop only OUR registered commands; let unknown /foo (e.g. CC's own
    # /resume, /clear, /compact, /model) flow through to the CLI via handle_text.
    _own_cmds = [c.command for c in BOT_COMMANDS]
    app.add_handler(MessageHandler(filters.TEXT & ~filters.Command(_own_cmds), handle_text))

    log.info("Bot running. Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
