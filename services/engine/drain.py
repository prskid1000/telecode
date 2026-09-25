"""Drain a subprocess pipe on a daemon thread.

The runner iterates stdout only; a ``stderr=PIPE`` nobody reads fills the OS
pipe buffer (~4 KB on Windows) and the child blocks on its next stderr write —
forever, since we are blocked on its stdout. Codex hits this reliably on an
expired ChatGPT login (one token-refresh error per request).
"""

from __future__ import annotations

import threading
from typing import Any


class StreamDrain:
    def __init__(self, stream: Any, limit: int = 256 * 1024) -> None:
        self._chunks: list = []
        self._size = 0
        self._limit = limit
        self._thread = threading.Thread(target=self._run, args=(stream,), daemon=True)
        self._thread.start()

    def _run(self, stream: Any) -> None:
        if stream is None:
            return
        try:
            for line in stream:
                if self._size < self._limit:
                    self._chunks.append(line)
                    self._size += len(line)
        except Exception:
            pass

    def text(self, timeout: float = 5.0) -> str:
        self._thread.join(timeout)
        return "".join(self._chunks)
