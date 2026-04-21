"""STT via OpenAI-compatible endpoint.

A successful 200 flips voice.health.stt_reachable to True; any other
outcome flips it to False — no startup probe, no background poll.
"""
from __future__ import annotations
import io, logging, base64
import aiohttp
import config
from voice import health as _health

log = logging.getLogger("telecode.voice.stt")

# A small "Hello World" WAV clip for testing.
HELLO_WORLD_AUDIO = base64.b64decode(
    "UklGRuy3AABXQVZFZm10IBAAAAABAAEAIlYAACJWAAABAAgAZGF0Yci3AACIhISCgH5+enx+gHx4cm5udHp+fHh0cHJ0enx8enx"
    "+fn6GiIyMiIaKio6Wmp6goJycnp6coJ6cnJqanqSoqqigmJKSmJygop6ampycmpaOjIiIhoSChoiGgHx2cG5qaGhiYmRkZmRgXF"
    "pUUlJUVlhcVlZYWmBgYFxaWlxiZmpsbGxsampubnBwbmxsbnB0cHBubGpqbGxwcnRwbnB2enyAfoB8fH5+goSEhISCgICEhoyK"
    "hoCChIiMjIyKiIaCgoaMioiEhISGjpaanJyYlpSWnqKkoJyYlpiYmJaSkI6MioSKjIyIhIB8en5+fHp2eHp6fn6AfHp8enh8fo"
    "SEgoJ+foKGiIaAeHR2dn58fHx8fHZ2cnBydnRwcHJ6fHp2dHBwbm5sbm5wcHJubnB0dHBwbG50dnx8enp4enZ2dnZ0dnh4fH5+"
    "fHp2dHR0enZ0cnZ6eHp6eHh0dnh0dHZ2eHZ0cGxqamZiXFpYWFhaWlhYVlhYVlZaWl5gYGJkZmhoaGxucHJ0eHR2en58fHh0dn"
    "Z8foCAgH6ChoSEhISEhoaIjJKWmpiSkpSWmp6enJiampqcnpqYkI6MjIyOjIiGhoiIhoqIiISChISKjo6MiISEhoqOkJKSkpSQ"
    "jpKYmpqWkJCOkpKUkpKQio6OjI6MhoKAenp2dHJuamhoZGRoamhmZmZkZmhqamhoaGxsbm5qamZoaGpqbm5ubmxuamxsbm50cn"
    "R2fHx+foSAfn6CiIqMjpKSkpaeoqCWlJSWlpSanp6alJSSkJCSlpaSkpKWmqCemJKOjI6SlpaYnJqUlJaYlpKQkJCSlpqYkI6O"
    "joyKiIiEgHx+hoaIhoJ8dHBucnR0cHJycHJucGpoZmZoZGZoaG5ubmpsamhkZGRkZGZqbGhmZmhkZGZkYmJkZGZqbG5wcGhoam"
    "5yenx6eHZ0dnp6eHh4dnp6fHx6eHRybG5ucnR0cnBudHRycnBwcHZ4enp+fnx8fHZ4fH6EhISGiIiGioqMjoyKiIaIjJSampiW"
    "kI6MkI6UlJKQjIqEhoqIgoCAfHp+goCAfnh4dnJ2dHRycHBsbnJwdG5waGhqampsaGRiZmhoamhmaGZsbGxubmxsbm5wcnR0cH"
    "BubnJ0eHp2dHZ2eHp8fn5+fnx+goaKioqEhIiOlJiYmJSSkI6OjpCQkJCQjo6SkpKMjIiGiIqMkJCQkI6MioiIhoqKioaIhIaG"
    "ioiKioqKjIqIiIaEhIKCgICEhISAfHp4eHp6dnR2cHBycnJ0dHJycGpudHR0dHBsbG5ucnJ0cnBwcHZ4eHp4dHBydnp+hIB8eH"
    "Z2dnp+fHh2dnR6en5+fHZ0cHBydnRybmxqamZoZmJgXFpaYmZoampoaGhucHJycHBwdnx+goaGiIiMjpSWmpqYlpacnqCinpy"
    "goqakoqSkoKCepKaopqKenJaWlJKSkpCKjIyMjo6MiIaIhIiKjpKUkpKSkI6OjIiGgHx6fHp4dnZ0dHJ0dnR0dHJydHZ2eHZ2"
    "dHJ0dnh8foCAfn6CgoB8fHx4eHRwdHZ4eHhyamhmaGpsam5ydHZ2eHh2dnJucG5udHx+goB+gH54dnRydHZ4fHx+goSAfn58en"
    "58fH6GjpKSlpKQjpKQkpKSkIyOjpCMioaAgHp4dHBycHBubGRgXlpaWFZSUlBQUlRQTkxITEpMTkxMTE5SVlpYWFxiZmhqbnR2"
    "en5+gIB8enx+"
)


async def transcribe(audio_bytes: bytes, filename: str = "voice.ogg") -> str | None:
    url = f"{config.stt_base_url().rstrip('/')}/audio/transcriptions"
    ext = (filename or "").lower()
    ct = "audio/ogg"
    if ext.endswith(".wav"): ct = "audio/wav"
    elif ext.endswith(".mp3"): ct = "audio/mpeg"
    elif ext.endswith(".m4a"): ct = "audio/mp4"

    try:
        form = aiohttp.FormData()
        form.add_field("file", io.BytesIO(audio_bytes), filename=filename, content_type=ct)
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
