"""TTS via OpenAI-compatible /v1/audio/speech.

Mirrors voice/stt.py. A successful 200 flips voice.health.tts_reachable
to True; any other outcome flips it to False — no startup probe, no
background poll.
"""
from __future__ import annotations

import logging

import aiohttp

import config
from voice import health as _health

log = logging.getLogger("telecode.voice.tts")


async def synthesize(text: str, voice: str = "", timeout: float = 30.0) -> bytes | None:
    """POST `text` to the configured TTS endpoint and return WAV bytes.

    Returns None on any failure. Updates the shared voice.health status
    so the Audio settings pill flips reachable/unreachable on every call.
    """
    try:
        base = config.tts_base_url().rstrip("/")
    except Exception:
        base = "http://127.0.0.1:6600/v1"
    url = f"{base}/audio/speech"
    payload: dict = {"model": "tts-1", "input": text}
    if voice:
        payload["voice"] = voice
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload,
                              timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                if r.status != 200:
                    body = (await r.text())[:200]
                    log.warning("TTS HTTP %s: %s", r.status, body)
                    _health.record_tts_failure(f"HTTP {r.status}")
                    return None
                data = await r.read()
                _health.record_tts_success()
                return data
    except Exception as e:
        log.error("TTS failed: %s", e)
        _health.record_tts_failure(str(e))
        return None


# A short ASCII text used by the settings "Run Test" button.
HELLO_WORLD_TEXT = "Hello from telecode."
