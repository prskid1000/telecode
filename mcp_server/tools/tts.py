"""Text-to-speech tool via OpenAI-compatible /v1/audio/speech.

Points at any TTS server that speaks OpenAI's audio API (VoxType's
embedded server is the default). URL is read at request time so
settings changes take effect without restarting telecode.
"""
from __future__ import annotations

import os
import tempfile

import aiohttp

from mcp_server.app import mcp_app


def _tts_url() -> str:
    """Read the configured endpoint at request time."""
    try:
        import config as _cfg
        return _cfg.get_nested("mcp_server.tts_url", "http://127.0.0.1:6600")
    except Exception:
        return os.environ.get("TTS_URL", "http://127.0.0.1:6600")


@mcp_app.tool()
async def speak(
    text: str,
    voice: str = "",
    output_path: str = "",
) -> str:
    """Generate speech audio from text via a Piper/ONNX TTS endpoint.

    Args:
        text: The text to speak.
        voice: Optional voice identifier. Piper's model file IS the voice,
            so this is usually ignored — left in for OpenAI-API compatibility.
        output_path: Optional file path for the output WAV. If empty, saves to a temp file.

    Returns:
        Absolute path to the generated audio file.
    """
    base = _tts_url().rstrip("/")
    url = f"{base}/v1/audio/speech"
    payload: dict = {"model": "tts-1", "input": text}
    if voice:
        payload["voice"] = voice

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status != 200:
                body = await resp.text()
                return f"Error: TTS server returned HTTP {resp.status}: {body[:200]}"
            audio = await resp.read()

    if not output_path:
        fd, output_path = tempfile.mkstemp(suffix=".wav", prefix="tts_")
        os.close(fd)

    with open(output_path, "wb") as f:
        f.write(audio)

    return output_path
