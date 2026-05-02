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
from voice.health import get_status as voice_status
from bot.handlers import (
    cmd_start, cmd_help, cmd_new, cmd_stop,
    cmd_settings, cmd_key,
    handle_callback, handle_text, handle_voice_msg, handle_document,
    handle_forum_topic_closed, normalize_mention,
    BOT_COMMANDS,
)
from bot.rate import set_session_manager, topic_check_loop
from proxy.server import start_proxy_background
from mcp_server.server import start_mcp_background
from llamacpp import config as llama_cfg
from process import get_supervisor, shutdown_supervisor
from tray.app import start_tray_in_thread


# Per-subsystem log files. Each value is the prefix that logger names must
# start with to land in the file. A single Filter dispatches by name so we
# don't have to attach handlers to every individual logger.
_SUB_LOGS = {
    "proxy.log":     ("telecode.proxy", "telecode.runtime_state", "telecode.web_search"),
    "mcp.log":       ("telecode.mcp_server",),
    "bot.log":       ("telecode.handlers", "telecode.live", "telecode.rate",
                      "telecode.topic_manager"),
    "voice.log":     ("telecode.voice",),
    "docgraph.log":  ("telecode.docgraph",),
}


class _PrefixFilter(logging.Filter):
    """Pass records whose logger name starts with any of `prefixes`."""
    def __init__(self, prefixes: tuple[str, ...]) -> None:
        super().__init__()
        self._prefixes = prefixes

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        return any(record.name == p or record.name.startswith(p + ".")
                   for p in self._prefixes)


def _rotate_previous_logs() -> None:
    """Rotate every per-subsystem log (telecode/llama/proxy/mcp/bot/voice) to
    .prev so traces from the previous run survive a restart. Also prunes old
    proxy_full_*.json dumps. Best-effort — skips files held by other processes."""
    import glob
    logs_dir = config.logs_dir()
    if not os.path.isdir(logs_dir):
        return
    basenames = ["telecode.log", "llama.log"] + list(_SUB_LOGS.keys())
    for basename in basenames:
        current = os.path.join(logs_dir, basename)
        prev = os.path.join(logs_dir, f"{basename}.prev")
        if os.path.exists(current):
            try:
                if os.path.exists(prev):
                    os.unlink(prev)
                os.replace(current, prev)
            except OSError:
                pass  # locked (e.g. llama-server still writing) — append mode continues
    for path in glob.glob(os.path.join(logs_dir, "proxy_full_*.json")):
        try:
            os.unlink(path)
        except OSError:
            pass


def _setup_logging() -> None:
    os.makedirs(config.logs_dir(), exist_ok=True)
    _rotate_previous_logs()
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

    # Per-subsystem files. Records still propagate to root (telecode.log) so
    # the unified log keeps everything; these dedicated files just give a
    # focused view (proxy noise vs. bot noise vs. voice noise).
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root = logging.getLogger()
    for basename, prefixes in _SUB_LOGS.items():
        h = logging.FileHandler(
            os.path.join(config.logs_dir(), basename), encoding="utf-8"
        )
        h.setFormatter(fmt)
        h.addFilter(_PrefixFilter(prefixes))
        root.addHandler(h)


def _install_crash_handlers(log: logging.Logger) -> None:
    """Route uncaught exceptions and unhandled asyncio task exceptions into
    the log file. Under pythonw (no stderr) this is the only place an
    unexpected traceback can land — without it a silent crash just stops the
    process with no trace."""
    def _sys_excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            return  # normal Ctrl+C exit
        log.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))
    sys.excepthook = _sys_excepthook

    try:
        import threading
        def _thread_excepthook(args):
            log.critical(
                "Uncaught thread exception in %s",
                getattr(args.thread, "name", "?"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        threading.excepthook = _thread_excepthook
    except Exception:
        pass


def _install_asyncio_exception_handler(log: logging.Logger) -> None:
    """Attach a loop-level handler so exceptions from tasks scheduled with
    ensure_future that never get awaited still land in telecode.log. Must be
    called from inside a running loop (uses ``get_running_loop`` so we don't
    accidentally bind to a non-running default loop on 3.12+)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    def _handler(_loop, context):
        exc = context.get("exception")
        message = context.get("message", "asyncio error")
        if exc is not None:
            log.error("Asyncio: %s", message, exc_info=exc)
        else:
            log.error("Asyncio: %s (context=%r)", message, context)
    loop.set_exception_handler(_handler)


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

    from process import bind_to_lifetime_job
    for https_port, local_port, label in routes:
        try:
            cmd = ["tailscale", "funnel", "--bg", "--https", str(https_port), str(local_port)]
            if sys.platform == "win32":
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            # Bind to the kill-on-close job so the OS reaps this if we die.
            bind_to_lifetime_job(proc.pid, proc=proc)
            funnels.append(proc)
            port_suffix = "" if https_port == 443 else f":{https_port}"
            url = f"https://{ts_domain}{port_suffix}" if ts_domain else f"<tailscale>{port_suffix}"
            log.info("Tailscale Funnel (%s): %s -> localhost:%d (pid %d)", label, url, local_port, proc.pid)
        except Exception as exc:
            log.warning("Failed to start Tailscale Funnel for %s: %s", label, exc)

    return funnels


async def _preload_one(supervisor, model: str, log) -> None:
    try:
        active = await supervisor.ensure_model(model)
        log.info("llama.cpp: preloaded '%s'", active)
    except Exception as exc:
        log.error("llama.cpp: preload '%s' failed: %s", model, exc)


async def _post_init(app) -> None:
    log = logging.getLogger("telecode.main")
    _install_asyncio_exception_handler(log)
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

    # Background topic-liveness sweep: Telegram emits no service message
    # on forum-topic DELETION (only on close), so without an active probe
    # our sessions linger until the user runs /start. topic_check_loop
    # runs `full_cleanup_all` every 60 s, one sendMessage+delete per live
    # session per tick. Cheap enough for a personal bot.
    mgr = app.bot_data.get("session_manager")
    if mgr is not None:
        app.bot_data["_topic_check_task"] = asyncio.ensure_future(
            topic_check_loop(app.bot, mgr, interval_sec=60)
        )

    # Voice: no startup probe + no background poll. The first incoming voice
    # message hits the STT endpoint directly; voice.health state is driven by
    # those real requests (record_success / record_failure inside
    # voice.stt.transcribe). If the endpoint is down at boot we don't know
    # and don't care until a user actually sends audio.
    vs = voice_status()
    log.info(
        "Voice: STT=%s (lazy — health tracked from real transcribe calls)",
        "configured" if vs.stt_configured else "disabled",
    )

    # llama-server is LAZY by default — the proxy's first request triggers
    # `supervisor.ensure_model()` which spawns llama-server then. This means:
    #   - telecode boots in seconds, not minutes
    #   - VRAM stays free until you actually use a local model
    #   - the idle-unload watcher (llamacpp.idle_unload_sec, default 300s)
    #     stops the server again after inactivity so VRAM gets released
    #
    # Eager paths (only if the user opts in):
    #   - `llamacpp.auto_start: true`         → load default_model now
    #   - per-model `preload: true`           → load that model now
    if llama_cfg.enabled():
        try:
            supervisor = await get_supervisor()
            app.bot_data["_llama_supervisor"] = supervisor
            preload = list(llama_cfg.preload_models())
            # Eager-load only if explicitly asked. The "remembered" last-active
            # model is just used as the implicit default when a request omits
            # `model` — never auto-loaded on startup. Lazy mode is the default
            # so VRAM stays free until something actually needs the LLM.
            if llama_cfg.auto_start():
                from llamacpp import state as llama_state
                remembered = llama_state.last_active_model()
                if remembered and remembered in llama_cfg.models():
                    preload.insert(0, remembered)
                    log.info("llama.cpp: auto_start → loading remembered '%s'", remembered)
                else:
                    preload.insert(0, llama_cfg.default_model())
            preload = list(dict.fromkeys(p for p in preload if p))
            for model in preload:
                # spawn in background so a slow load doesn't block the bot
                asyncio.ensure_future(_preload_one(supervisor, model, log))
            if not preload:
                log.info("llama.cpp: lazy mode — model loads on first request")
        except Exception as exc:
            log.error("llama-server supervisor init failed: %s", exc, exc_info=True)

    try:
        runner = await start_proxy_background()
        if runner:
            app.bot_data["_proxy_runner"] = runner
    except Exception as exc:
        log.error("Proxy startup failed: %s", exc, exc_info=True)

    # System tray UI — runs on a daemon thread inside this process.
    # Menu clicks use run_coroutine_threadsafe() onto the bot's loop for
    # async calls; sync stuff (settings patches) runs directly.
    try:
        loop = asyncio.get_running_loop()
        app.bot_data["_tray_thread"] = start_tray_in_thread(app, loop)
    except Exception as exc:
        log.error("Tray startup failed: %s", exc, exc_info=True)

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

    # Heartbeat scheduler — fires HB jobs on cron schedule. Reads HEARTBEAT.md
    # from each agent's storage and reconciles HB Jobs in the sidebar each tick.
    try:
        if config.heartbeat_enabled():
            from services.heartbeat.scheduler import start_scheduler
            await start_scheduler()
    except Exception as exc:
        log.error("Heartbeat scheduler startup failed: %s", exc, exc_info=True)

    # DocGraph supervisors — bring up any role with auto_start=true. Bridge
    # registration happens inside McpSupervisor.start() once each child is ready.
    try:
        from docgraph.process import autostart_all as docgraph_autostart
        await docgraph_autostart()
    except Exception as exc:
        log.error("DocGraph auto-start failed: %s", exc, exc_info=True)


async def _post_shutdown(app) -> None:
    # DocGraph supervisors — close MCP bridges + kill subprocesses + free ports
    # before the proxy tears down (managed_tools._REGISTRY entries removed first).
    try:
        from docgraph.process import shutdown_all as docgraph_shutdown
        await docgraph_shutdown()
    except Exception:
        pass

    # Heartbeat scheduler — stop before the task queue's executors die so
    # in-flight tracking can settle.
    try:
        from services.heartbeat.scheduler import stop_scheduler
        await stop_scheduler()
    except Exception:
        pass

    # Tray is a daemon thread — dies with the interpreter; no explicit stop.

    # Stop proxy first (so no new requests land on a dying llama-server)
    runner = app.bot_data.get("_proxy_runner")
    if runner:
        await runner.cleanup()

    # Stop llama-server AFTER the proxy — the supervisor owns the process.
    try:
        await shutdown_supervisor()
    except Exception:
        pass

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

    for key in ("_probe_task", "_stale_check_task", "_topic_check_task"):
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
    _install_crash_handlers(log)

    # Single-instance guard: named mutex on Windows, flock on Unix. Bails
    # out before we try to bind the proxy / MCP ports (which would also
    # fail, but noisily).
    from single_instance import acquire as _acquire_instance
    if not _acquire_instance():
        log.warning("Another telecode instance is already running — exiting")
        sys.exit(0)

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

    # Pre-handler in group -1: strips `/cmd@botname` -> `/cmd` on every
    # incoming message so all downstream handlers (CommandHandler, handle_text,
    # forwarded-to-CLI text) see a normalized form.
    app.add_handler(MessageHandler(filters.TEXT, normalize_mention), group=-1)

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
    # Register AFTER all CommandHandlers so in group 0 they match their own
    # commands first and run; anything they don't claim (plain text, unknown
    # /foo like CC's /resume /clear /compact /model) falls through to here.
    app.add_handler(MessageHandler(filters.TEXT, handle_text))

    log.info("Bot running. Ctrl+C to stop.")
    try:
        app.run_polling(drop_pending_updates=True)
    except KeyboardInterrupt:
        log.info("Bot stopped by user.")
    except Exception as exc:
        # Capture any crash that bubbles out of the polling loop so pythonw
        # doesn't silently eat the traceback.
        log.critical("Bot crashed: %s", exc, exc_info=exc)
        raise


if __name__ == "__main__":
    main()
