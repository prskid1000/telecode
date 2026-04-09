"""Text-to-speech tool via Kokoro TTS."""
from __future__ import annotations

import os
import tempfile

import aiohttp

from mcp_server.app import mcp_app

try:
    import config as _cfg
    _TTS_URL = _cfg.get_nested("mcp_server.tts_url", "http://127.0.0.1:6500")
except Exception:
    _TTS_URL = os.environ.get("KOKORO_URL", "http://127.0.0.1:6500")


@mcp_app.tool()
async def speak(
    text: str,
    voice: str = "af_heart",
    output_path: str = "",
) -> str:
    """Generate speech audio from text using Kokoro TTS.

    Args:
        text: The text to speak.
        voice: Kokoro voice ID (e.g. af_heart, am_adam, bf_emma). Defaults to af_heart.
        output_path: Optional file path for the output WAV. If empty, saves to a temp file.

    Returns:
        Absolute path to the generated audio file.
    """
    url = f"{_TTS_URL}/v1/audio/speech"
    payload = {"model": "kokoro", "input": text, "voice": voice}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status != 200:
                body = await resp.text()
                return f"Error: Kokoro TTS returned HTTP {resp.status}: {body[:200]}"
            audio = await resp.read()

    if not output_path:
        fd, output_path = tempfile.mkstemp(suffix=".wav", prefix="tts_")
        os.close(fd)

    with open(output_path, "wb") as f:
        f.write(audio)

    return output_path
