"""
JSON persistence store.

Stores two things:
  topics[user_id][session_key] = thread_id
  voice_prefs[user_id]         = {"stt_on": bool}

File location: settings.paths.store_path (default: ./data/telecode.json)
"""
from __future__ import annotations
import json
import os
import asyncio
from typing import Any
import config

_lock = asyncio.Lock()


def _store_path() -> str:
    return config.store_path()


def _load() -> dict[str, Any]:
    path = _store_path()
    if not os.path.exists(path):
        return {"topics": {}, "voice_prefs": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict[str, Any]) -> None:
    path = _store_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)   # atomic write


# ── Topics ────────────────────────────────────────────────────────────────────

async def get_thread_id(user_id: int, session_key: str) -> int | None:
    async with _lock:
        data = _load()
        return data["topics"].get(str(user_id), {}).get(session_key)


async def save_thread_id(user_id: int, session_key: str, thread_id: int) -> None:
    async with _lock:
        data = _load()
        data["topics"].setdefault(str(user_id), {})[session_key] = thread_id
        _save(data)


async def delete_thread_id(user_id: int, session_key: str) -> None:
    async with _lock:
        data = _load()
        data["topics"].get(str(user_id), {}).pop(session_key, None)
        _save(data)


async def list_thread_ids(user_id: int) -> list[dict]:
    async with _lock:
        data = _load()
        return [
            {"session_key": k, "thread_id": v}
            for k, v in data["topics"].get(str(user_id), {}).items()
        ]


# ── Voice prefs ───────────────────────────────────────────────────────────────

async def get_voice_prefs(user_id: int) -> dict[str, bool]:
    async with _lock:
        data = _load()
        prefs = data["voice_prefs"].get(str(user_id))
    return prefs if prefs else {"stt_on": True}


async def set_voice_pref(user_id: int, key: str, value: bool) -> None:
    assert key in ("stt_on",)
    async with _lock:
        data = _load()
        data["voice_prefs"].setdefault(str(user_id), {"stt_on": True})[key] = value
        _save(data)
