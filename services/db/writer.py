"""One ordered background writer for high-frequency write-through (task rows
and task events), so a task-queue worker never waits on disk before or while
running its handler.

Writes run in submission order on a single daemon thread against the database
path resolved *at submit time*. :func:`flush` waits for everything queued so
far (tests, shutdown); an ``atexit`` hook flushes on a clean exit. Readers
that must see a live task's latest state read it from the in-memory queue,
not from here.
"""

from __future__ import annotations

import atexit
import logging
import queue
import threading
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("telecode.services.db.writer")

_q: "queue.Queue[Optional[tuple]]" = queue.Queue()
_thread: Optional[threading.Thread] = None
_start_lock = threading.Lock()


def _run() -> None:
    from services.db.core import connect
    while True:
        item = _q.get()
        try:
            if item is None:
                continue
            path, fn, args, done = item
            if fn is None:
                done.set()
                continue
            try:
                fn(connect(path), *args)
            except Exception:
                logger.exception("telecode.db background write failed")
        finally:
            _q.task_done()


def _ensure() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    with _start_lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_run, name="telecode-db-writer", daemon=True)
            _thread.start()


def submit(fn: Callable[..., Any], *args: Any, path: Optional[Path] = None) -> None:
    """Queue ``fn(conn, *args)`` for the writer thread."""
    from services.db.core import db_path
    _ensure()
    _q.put((path or db_path(), fn, args, None))


def flush(timeout: float = 10.0) -> bool:
    """Block until every write queued before this call has run."""
    _ensure()
    done = threading.Event()
    _q.put((None, None, (), done))
    return done.wait(timeout)


atexit.register(lambda: flush(5.0))
