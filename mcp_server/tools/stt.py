"""Speech-to-text tool via Whisper STT."""
from __future__ import annotations

import os

import aiohttp

from mcp_server.app import mcp_app

try:
    import config as _cfg
    _STT_URL = _cfg.get_nested("mcp_server.stt_url", "http://127.0.0.1:6600")
except Exception:
    _STT_URL = os.environ.get("WHISPER_URL", "http://127.0.0.1:6600")


@mcp_app.tool()
async def transcribe(
    audio_path: str,
    language: str = "en",
) -> str:
    """Transcribe an audio file to text using Whisper STT.

    Args:
        audio_path: Absolute path to the audio file (WAV, MP3, WEBM, OGG, etc.).
        language: Language code for transcription (default: en).

    Returns:
        Transcribed text, or an error message.
    """
    if not os.path.isfile(audio_path):
        return f"Error: file not found: {audio_path}"

    url = f"{_STT_URL}/v1/audio/transcriptions"

    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    # Guess content type from extension
    ext = os.path.splitext(audio_path)[1].lower()
    ct_map = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
              ".webm": "audio/webm", ".flac": "audio/flac", ".m4a": "audio/mp4"}
    content_type = ct_map.get(ext, "application/octet-stream")
    filename = os.path.basename(audio_path)

    form = aiohttp.FormData()
    form.add_field("file", audio_bytes, filename=filename, content_type=content_type)
    form.add_field("model", "whisper-1")
    form.add_field("language", language)
    form.add_field("response_format", "json")

    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=form, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status != 200:
                body = await resp.text()
                return f"Error: Whisper STT returned HTTP {resp.status}: {body[:200]}"
            result = await resp.json()
            return result.get("text", "").strip() or "(empty transcription)"
