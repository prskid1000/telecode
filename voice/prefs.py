"""Per-user STT toggles — thin wrapper over store.py."""
from __future__ import annotations
import store


async def get_prefs(user_id: int) -> dict[str, bool]:
    return await store.get_voice_prefs(user_id)


async def set_pref(user_id: int, key: str, value: bool) -> None:
    await store.set_voice_pref(user_id, key, value)


async def stt_active(user_id: int, global_ok: bool) -> bool:
    if not global_ok: return False
    return (await get_prefs(user_id))["stt_on"]
