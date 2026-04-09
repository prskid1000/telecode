"""Speech-to-text tool via Whisper STT."""
from __future__ import annotations

import os
from urllib.parse import urlparse

import aiohttp

from mcp_server.app import mcp_app

try:
    import config as _cfg
    _STT_URL = _cfg.get_nested("mcp_server.stt_url", "http://127.0.0.1:6600")
except Exception:
    _STT_URL = os.environ.get("WHISPER_URL", "http://127.0.0.1:6600")

_CT_MAP = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
           ".webm": "audio/webm", ".flac": "audio/flac", ".m4a": "audio/mp4"}


async def _fetch_remote(url: str) -> tuple[bytes, str, str]:
    """Download a remote audio file. Returns (bytes, filename, content_type)."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} fetching {url}")
            audio_bytes = await resp.read()

    # Derive filename and content type from URL path
    path = urlparse(url).path
    filename = os.path.basename(path) or "audio.wav"
    ext = os.path.splitext(filename)[1].lower()
    content_type = _CT_MAP.get(ext, "application/octet-stream")
    return audio_bytes, filename, content_type


@mcp_app.tool()
async def transcribe(
    audio_path: str,
    language: str = "en",
) -> str:
    """Transcribe an audio file to text using Whisper STT.

    Args:
        audio_path: Local file path or remote URL (http/https) to an audio file (WAV, MP3, WEBM, OGG, etc.).
        language: Language code for transcription (default: en).

    Returns:
        Transcribed text, or an error message.
    """
    is_url = audio_path.startswith("http://") or audio_path.startswith("https://")

    if is_url:
        try:
            audio_bytes, filename, content_type = await _fetch_remote(audio_path)
        except Exception as e:
            return f"Error downloading audio: {e}"
    else:
        if not os.path.isfile(audio_path):
            return f"Error: file not found: {audio_path}"
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()
        ext = os.path.splitext(audio_path)[1].lower()
        content_type = _CT_MAP.get(ext, "application/octet-stream")
        filename = os.path.basename(audio_path)

    form = aiohttp.FormData()
    form.add_field("file", audio_bytes, filename=filename, content_type=content_type)
    form.add_field("model", "whisper-1")
    form.add_field("language", language)
    form.add_field("response_format", "json")

    async with aiohttp.ClientSession() as session:
        async with session.post(f"{_STT_URL}/v1/audio/transcriptions", data=form, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status != 200:
                body = await resp.text()
                return f"Error: Whisper STT returned HTTP {resp.status}: {body[:200]}"
            result = await resp.json()
            return result.get("text", "").strip() or "(empty transcription)"
