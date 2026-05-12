"""Voice service health tracking.

No startup probe, no background poll loop. `stt_reachable` / `tts_reachable`
flip based on the outcome of real `transcribe()` / `synthesize()` calls —
optimistic on first use, pessimistic after a failure until the next
successful call flips it back.

This is the flow we want: the only request we send to either endpoint is
the actual audio — zero "wake up every 60s" traffic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from html import escape as _esc  # noqa: F401 — re-exported for callers

import config

log = logging.getLogger("telecode.voice.health")


@dataclass
class VoiceStatus:
    stt_configured: bool
    stt_reachable:  bool
    stt_last_checked: bool = False  # True once a real STT request has been made
    tts_configured: bool = False
    tts_reachable:  bool = True
    tts_last_checked: bool = False  # True once a real TTS request has been made

    @property
    def stt_available(self) -> bool:
        return self.stt_configured and self.stt_reachable

    @property
    def tts_available(self) -> bool:
        return self.tts_configured and self.tts_reachable

    def summary(self) -> str:
        def dot(ok: bool) -> str:
            return "🟢" if ok else "🔴"
        lines = ["<b>🎙️ Audio</b>\n"]
        if self.stt_configured:
            if not self.stt_last_checked:
                lines.append("Speech-to-text: ⚪ untested — tries on first voice message")
            else:
                status = "connected" if self.stt_reachable else "not reachable"
                lines.append(f"Speech-to-text: {dot(self.stt_reachable)} {status}")
        else:
            lines.append("Speech-to-text: ⚫ off")
        if self.tts_configured:
            if not self.tts_last_checked:
                lines.append("Text-to-speech: ⚪ untested — tries on first synthesize")
            else:
                status = "connected" if self.tts_reachable else "not reachable"
                lines.append(f"Text-to-speech: {dot(self.tts_reachable)} {status}")
        else:
            lines.append("Text-to-speech: ⚫ off")
        if self.stt_configured and self.stt_last_checked and not self.stt_reachable:
            lines.append("\n<i>Last transcribe request failed.</i>")
        if self.tts_configured and self.tts_last_checked and not self.tts_reachable:
            lines.append("\n<i>Last synthesize request failed.</i>")
        return "\n".join(lines)


# Optimistic defaults: reachable=True so first requests actually hit the
# endpoint. record_failure() flips them on the first real failure;
# record_success() flips them back.
_status = VoiceStatus(
    stt_configured=False,
    stt_reachable=True,
    stt_last_checked=False,
    tts_configured=False,
    tts_reachable=True,
    tts_last_checked=False,
)


def _refresh_configured() -> None:
    """Pick up live settings changes."""
    _status.stt_configured = config.stt_enabled()
    try:
        _status.tts_configured = config.tts_enabled()
    except Exception:
        _status.tts_configured = False


def get_status() -> VoiceStatus:
    _refresh_configured()
    return _status


# ── STT health hooks ─────────────────────────────────────────────────

def record_success() -> None:
    """Called by voice.stt.transcribe() after a 200 OK response."""
    _refresh_configured()
    was_down = _status.stt_last_checked and not _status.stt_reachable
    _status.stt_reachable = True
    _status.stt_last_checked = True
    if was_down:
        log.info("STT recovered at %s", config.stt_base_url())


def record_failure(reason: str = "") -> None:
    """Called by voice.stt.transcribe() after any non-200 / exception."""
    _refresh_configured()
    was_up = not _status.stt_last_checked or _status.stt_reachable
    _status.stt_reachable = False
    _status.stt_last_checked = True
    if was_up:
        log.info("STT UNREACHABLE at %s (%s)", config.stt_base_url(), reason or "—")


# ── TTS health hooks ─────────────────────────────────────────────────

def record_tts_success() -> None:
    _refresh_configured()
    was_down = _status.tts_last_checked and not _status.tts_reachable
    _status.tts_reachable = True
    _status.tts_last_checked = True
    if was_down:
        try:
            log.info("TTS recovered at %s", config.tts_base_url())
        except Exception:
            log.info("TTS recovered")


def record_tts_failure(reason: str = "") -> None:
    _refresh_configured()
    was_up = not _status.tts_last_checked or _status.tts_reachable
    _status.tts_reachable = False
    _status.tts_last_checked = True
    if was_up:
        try:
            log.info("TTS UNREACHABLE at %s (%s)", config.tts_base_url(), reason or "—")
        except Exception:
            log.info("TTS UNREACHABLE (%s)", reason or "—")
