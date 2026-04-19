"""Voice service health tracking.

No startup probe, no background poll loop. `stt_reachable` flips based
on the outcome of real `transcribe()` calls — optimistic on first use
(we haven't seen it fail), pessimistic after a failure until the next
successful call flips it back.

This is the flow the user asked for: the only request we send to the
STT endpoint is the actual audio — zero "wake up STT every 60s" traffic.
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
    stt_last_checked: bool = False  # True once a real request has been made

    @property
    def stt_available(self) -> bool:
        return self.stt_configured and self.stt_reachable

    def summary(self) -> str:
        def dot(ok: bool) -> str:
            return "🟢" if ok else "🔴"
        lines = ["<b>🎙️ Voice</b>\n"]
        if self.stt_configured:
            if not self.stt_last_checked:
                lines.append("Speech-to-text: ⚪ untested — tries on first voice message")
            else:
                status = "connected" if self.stt_reachable else "not reachable"
                lines.append(f"Speech-to-text: {dot(self.stt_reachable)} {status}")
        else:
            lines.append("Speech-to-text: ⚫ off")
        if self.stt_configured and self.stt_last_checked and not self.stt_reachable:
            lines.append("\n<i>Last transcribe request failed.</i>")
        return "\n".join(lines)


# Optimistic default: reachable=True so the first voice message actually
# hits the endpoint. record_failure() flips it on the first real failure;
# record_success() flips it back.
_status = VoiceStatus(
    stt_configured=False,
    stt_reachable=True,
    stt_last_checked=False,
)


def _refresh_configured() -> None:
    """Pick up live settings changes (e.g. toggling voice.stt.enabled)."""
    _status.stt_configured = config.stt_enabled()


def get_status() -> VoiceStatus:
    _refresh_configured()
    return _status


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
