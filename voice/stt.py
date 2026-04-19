"""STT via OpenAI-compatible endpoint.

A successful 200 flips voice.health.stt_reachable to True; any other
outcome flips it to False — no startup probe, no background poll.
"""
from __future__ import annotations
import io, logging
import aiohttp
import config
from voice import health as _health

log = logging.getLogger("telecode.voice.stt")


async def transcribe(audio_bytes: bytes, filename: str = "voice.ogg") -> str | None:
    url = f"{config.stt_base_url().rstrip('/')}/audio/transcriptions"
    try:
        form = aiohttp.FormData()
        form.add_field("file", io.BytesIO(audio_bytes), filename=filename, content_type="audio/ogg")
        form.add_field("model", config.stt_model())
        async with aiohttp.ClientSession() as s:
            async with s.post(url, data=form, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200:
                    body = (await r.text())[:200]
                    log.warning("STT HTTP %s: %s", r.status, body)
                    _health.record_failure(f"HTTP {r.status}")
                    return None
                text = (await r.json()).get("text", "").strip() or None
                _health.record_success()
                return text
    except Exception as e:
        log.error("STT failed: %s", e)
        _health.record_failure(str(e))
        return None
