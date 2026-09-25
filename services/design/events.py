"""In-process pub/sub for TeleDesign events, keyed by project id.

The SSE route (`GET /api/design/projects/{pid}/events`) subscribes; everything
else publishes. Event types are the contract's (docs/teledesign-contract.md §4.3):
turn, delta, tool, todo, files, form, check, title, thumbnail, comments,
assets, error, show.

`publish()` is safe from any thread: subscribers are asyncio queues owned by
the proxy loop, so a publish from a worker thread hops onto that loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("telecode.services.design.events")

# A subscriber that stops reading (a stalled tab) must not grow without bound.
_QUEUE_MAX = 2000
HEARTBEAT_SEC = 15.0

_lock = threading.Lock()
# pid -> list of (loop, queue, chat_filter)
_subs: Dict[str, List[Tuple[asyncio.AbstractEventLoop, asyncio.Queue, Optional[str]]]] = {}


class Subscription:
    def __init__(self, pid: str, chat_id: Optional[str] = None):
        self.pid = pid
        self.chat_id = chat_id
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._entry = (self.loop, self.queue, chat_id)
        with _lock:
            _subs.setdefault(pid, []).append(self._entry)

    def close(self) -> None:
        with _lock:
            lst = _subs.get(self.pid) or []
            if self._entry in lst:
                lst.remove(self._entry)
            if not lst:
                _subs.pop(self.pid, None)

    async def get(self, timeout: float) -> Optional[Tuple[str, Dict[str, Any]]]:
        try:
            return await asyncio.wait_for(self.queue.get(), timeout)
        except asyncio.TimeoutError:
            return None


def _offer(queue: asyncio.Queue, item: Tuple[str, Dict[str, Any]]) -> None:
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        # Drop the oldest: a reconnecting client re-reads state over REST anyway.
        try:
            queue.get_nowait()
            queue.put_nowait(item)
        except Exception:
            pass


def publish(pid: str, etype: str, data: Optional[Dict[str, Any]] = None) -> None:
    data = data or {}
    chat_id = data.get("chat_id")
    with _lock:
        targets = list(_subs.get(pid) or [])
    if not targets:
        return
    item = (etype, data)
    for loop, queue, chat_filter in targets:
        # Chat-scoped subscribers skip other chats' events; project-wide ones
        # (no chat_id in the payload: files, comments, title…) reach everyone.
        if chat_filter and chat_id and chat_id != chat_filter:
            continue
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            _offer(queue, item)
        else:
            try:
                loop.call_soon_threadsafe(_offer, queue, item)
            except RuntimeError:
                pass  # loop closed


def subscriber_count(pid: str) -> int:
    with _lock:
        return len(_subs.get(pid) or [])


def format_sse(etype: str, data: Dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    # One `data:` line per line of payload keeps the frame valid even if a
    # value ever carries a raw newline (json.dumps escapes them, but be strict).
    lines = "".join(f"data: {ln}\n" for ln in payload.split("\n"))
    return f"event: {etype}\n{lines}\n".encode("utf-8")
