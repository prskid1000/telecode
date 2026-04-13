"""Standalone entry point: python -m proxy"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

# Ensure project root is on sys.path so 'config' and 'proxy' resolve
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp import web
from proxy.server import create_app
from proxy import config as proxy_config


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def _run() -> None:
    port = proxy_config.proxy_port()
    upstream = proxy_config.upstream_url()

    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    try:
        await asyncio.Event().wait()  # run forever
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    _setup_logging()
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
