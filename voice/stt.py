"""STT via voicemode-windows OpenAI-compatible endpoint."""
from __future__ import annotations
import io, logging
import aiohttp
import config

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
                    log.warning("STT HTTP %s: %s", r.status, (await r.text())[:200])
                    return None
                return (await r.json()).get("text", "").strip() or None
    except Exception as e:
        log.error("STT failed: %s", e)
        return None
