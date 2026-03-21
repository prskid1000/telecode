"""Voice service health checker."""
from __future__ import annotations
import asyncio, logging
from dataclasses import dataclass
from html import escape as _esc
import aiohttp
import config

log = logging.getLogger("telecode.voice.health")


@dataclass
class VoiceStatus:
    stt_configured: bool
    stt_reachable:  bool

    @property
    def stt_available(self) -> bool:
        return self.stt_configured and self.stt_reachable

    def summary(self) -> str:
        """HTML-formatted voice status for Telegram."""
        def dot(ok: bool) -> str:
            return "🟢" if ok else "🔴"
        lines = ["<b>🎙️ Voice</b>\n"]
        if self.stt_configured:
            status = "connected" if self.stt_reachable else "not reachable"
            lines.append(f"Speech-to-text: {dot(self.stt_reachable)} {status}")
        else:
            lines.append("Speech-to-text: ⚫ off")
        if not self.stt_available:
            lines.append("\n<i>Voice messages won't work right now.</i>")
        return "\n".join(lines)


_status = VoiceStatus(stt_configured=False, stt_reachable=False)
_http_session: aiohttp.ClientSession | None = None


def _get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session


def get_status() -> VoiceStatus:
    return _status


async def _check(base_url: str, timeout: float = 3.0) -> bool:
    try:
        session = _get_http_session()
        async with session.get(
            f"{base_url.rstrip('/')}/models",
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as r:
            return r.status < 500
    except Exception:
        return False


async def probe() -> VoiceStatus:
    global _status
    stt_ok = await _check(config.stt_base_url()) if config.stt_enabled() else False
    if config.stt_enabled():
        log.info("STT %s at %s", "OK" if stt_ok else "UNREACHABLE", config.stt_base_url())
    _status = VoiceStatus(
        stt_configured=config.stt_enabled(),
        stt_reachable=stt_ok,
    )
    return _status


async def probe_loop(interval: int = 60) -> None:
    try:
        while True:
            await probe()
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        return
