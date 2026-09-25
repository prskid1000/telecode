"""In-process pub/sub for Task and Team (run) live events — the SSE feeds.

Same shape as TeleDesign's ``services/design/events.py``: subscribers are
bounded asyncio queues owned by the proxy loop, ``publish()`` is safe from any
thread (a worker-thread publish hops onto the subscriber's loop), and a
stalled subscriber drops its oldest item instead of growing.

Topics are ``(kind, key)``:

* ``("task", task_id)`` — every engine event of that task (``event`` frames,
  with ``seq`` when persisted; ``delta`` frames for streamed text that is not
  persisted) and ``status`` frames.
* ``("run", run_id)``   — ``run`` frames (the run record after each write) plus
  the ``event``/``status`` frames of the run's step tasks.
* the global feed       — only ``task.status`` and ``run.update`` summaries, so
  a sidebar can follow everything without receiving every token.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("telecode.services.bus")

QUEUE_MAX = 2000
HEARTBEAT_SEC = 15.0

_lock = threading.Lock()
_Entry = Tuple[asyncio.AbstractEventLoop, asyncio.Queue]
_topic_subs: Dict[Tuple[str, str], List[_Entry]] = {}
_global_subs: List[Tuple[asyncio.AbstractEventLoop, asyncio.Queue, Set[str]]] = []


class Subscription:
    """Subscribe to one topic (``kind``+``key``) or, with ``key=None``, to the
    global feed filtered by ``kinds``. Must be created on the proxy loop."""

    def __init__(self, kind: Optional[str] = None, key: Optional[str] = None,
                 kinds: Optional[Set[str]] = None):
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self.topic = (kind, key) if key is not None else None
        with _lock:
            if self.topic:
                self._entry = (self.loop, self.queue)
                _topic_subs.setdefault(self.topic, []).append(self._entry)
            else:
                self._entry = (self.loop, self.queue, set(kinds or {"task", "run"}))
                _global_subs.append(self._entry)

    def close(self) -> None:
        with _lock:
            if self.topic:
                lst = _topic_subs.get(self.topic) or []
                if self._entry in lst:
                    lst.remove(self._entry)
                if not lst:
                    _topic_subs.pop(self.topic, None)
            elif self._entry in _global_subs:
                _global_subs.remove(self._entry)

    async def get(self, timeout: float) -> Optional[Tuple[str, Dict[str, Any]]]:
        try:
            return await asyncio.wait_for(self.queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    def get_nowait(self) -> Optional[Tuple[str, Dict[str, Any]]]:
        try:
            return self.queue.get_nowait()
        except asyncio.QueueEmpty:
            return None


def _offer(queue: asyncio.Queue, item: Tuple[str, Dict[str, Any]]) -> None:
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
            queue.put_nowait(item)
        except Exception:
            pass


def _deliver(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, item) -> None:
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


def has_subscribers() -> bool:
    with _lock:
        return bool(_topic_subs or _global_subs)


def publish(kind: str, key: Optional[str], etype: str, data: Dict[str, Any]) -> None:
    """Deliver to the ``(kind, key)`` topic subscribers."""
    if not key:
        return
    with _lock:
        targets = list(_topic_subs.get((kind, key)) or [])
    for loop, queue in targets:
        _deliver(loop, queue, (etype, data))


def publish_global(kind: str, etype: str, data: Dict[str, Any]) -> None:
    """Deliver a summary to global-feed subscribers that asked for ``kind``."""
    with _lock:
        targets = [(lp, q) for lp, q, kinds in _global_subs if kind in kinds]
    for loop, queue in targets:
        _deliver(loop, queue, (etype, data))


def format_sse(etype: str, data: Dict[str, Any], event_id: Optional[Any] = None) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    lines = "".join(f"data: {ln}\n" for ln in payload.split("\n"))
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {etype}\n{lines}\n".encode("utf-8")
