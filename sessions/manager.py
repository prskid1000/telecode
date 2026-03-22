"""
Multi-session manager — one process per (user_id, session_key).

session_key format:  "{backend_key}:{name}"
  e.g.  "claude:work", "codex:research", "shell:logs", "screen:myapp"

_sessions[user_id][session_key] = Session
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import config

log = logging.getLogger("telecode.manager")
from backends.base import CLIBackend, BackendParams
from sessions.process import PTYProcess
from sessions.screen import ScreenCapture, VideoCapture


@dataclass
class Session:
    user_id:     int
    session_key: str
    backend:     CLIBackend
    params:      BackendParams
    process:     Any  # PTYProcess | ScreenCapture (duck-typed: alive, start, stop, subscribe)
    workdir:     str
    thread_id:   int | None   = None
    turn_count:  int           = 0
    created_at:  float         = field(default_factory=time.time)
    last_active: float         = field(default_factory=time.time)
    _idle_task:  asyncio.Task | None = field(default=None, repr=False)

    @property
    def backend_key(self) -> str:
        return self.session_key.split(":")[0]

    @property
    def session_name(self) -> str:
        parts = self.session_key.split(":", 1)
        return parts[1] if len(parts) > 1 else "default"

    def touch(self) -> None:
        self.last_active = time.time()

    def is_idle(self, timeout: int) -> bool:
        return timeout > 0 and (time.time() - self.last_active) > timeout


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[int, dict[str, Session]] = {}
        self._lock = asyncio.Lock()

    async def start_session(
        self,
        user_id:          int,
        session_key:      str,
        backend:          CLIBackend,
        params:           BackendParams,
        output_callback:  Callable[[str], None],
        thread_id:        int | None = None,
    ) -> Session:
        async with self._lock:
            await self._kill_one_locked(user_id, session_key)

            workdir = self._ensure_workdir()
            process = PTYProcess(
                cmd=backend.build_launch_cmd(params),
                cwd=workdir,
                extra_env=backend.resolve_env(params),
            )
            process.subscribe(output_callback)
            await process.start()

            session = Session(
                user_id=user_id,
                session_key=session_key,
                backend=backend,
                params=params,
                process=process,
                workdir=workdir,
                thread_id=thread_id,
            )
            self._sessions.setdefault(user_id, {})[session_key] = session

            if config.idle_timeout() > 0:
                session._idle_task = asyncio.ensure_future(
                    self._idle_watcher(user_id, session_key)
                )

            return session

    async def start_screen_session(
        self,
        user_id:          int,
        session_key:      str,
        backend:          CLIBackend,
        hwnd:             int,
        output_callback:  Callable[[bytes], None],
        thread_id:        int | None = None,
        capture_interval: float = 15.0,
    ) -> Session:
        async with self._lock:
            await self._kill_one_locked(user_id, session_key)

            process = ScreenCapture(hwnd=hwnd, capture_interval=capture_interval)
            process.subscribe(output_callback)
            await process.start()

            session = Session(
                user_id=user_id,
                session_key=session_key,
                backend=backend,
                params=BackendParams(),
                process=process,
                workdir=config.pty_cwd(),
                thread_id=thread_id,
            )
            self._sessions.setdefault(user_id, {})[session_key] = session

            if config.idle_timeout() > 0:
                session._idle_task = asyncio.ensure_future(
                    self._idle_watcher(user_id, session_key)
                )

            return session

    async def start_video_session(
        self,
        user_id:        int,
        session_key:    str,
        backend:        CLIBackend,
        hwnd:           int,
        video_callback: Callable[[bytes], None],
        text_callback:  Callable[[str], None],
        thread_id:      int | None = None,
    ) -> Session:
        async with self._lock:
            await self._kill_one_locked(user_id, session_key)

            process = VideoCapture(hwnd=hwnd, duration=60, fps=3)
            process.subscribe(video_callback)
            process.subscribe_text(text_callback)
            await process.start()

            session = Session(
                user_id=user_id,
                session_key=session_key,
                backend=backend,
                params=BackendParams(),
                process=process,
                workdir=config.pty_cwd(),
                thread_id=thread_id,
            )
            self._sessions.setdefault(user_id, {})[session_key] = session
            return session

    def pause_session(self, user_id: int, session_key: str) -> bool:
        session = self._sessions.get(user_id, {}).get(session_key)
        if session and isinstance(session.process, (ScreenCapture, VideoCapture)):
            session.process.pause()
            session.touch()
            return True
        return False

    def resume_session(self, user_id: int, session_key: str) -> bool:
        session = self._sessions.get(user_id, {}).get(session_key)
        if session and isinstance(session.process, (ScreenCapture, VideoCapture)):
            session.process.resume()
            session.touch()
            return True
        return False

    async def kill_session(self, user_id: int, session_key: str) -> bool:
        async with self._lock:
            return await self._kill_one_locked(user_id, session_key)

    async def kill_all_sessions(self, user_id: int) -> int:
        async with self._lock:
            keys = list(self._sessions.get(user_id, {}).keys())
            for key in keys:
                await self._kill_one_locked(user_id, key)
            return len(keys)

    def get_session(self, user_id: int, session_key: str) -> Session | None:
        return self._sessions.get(user_id, {}).get(session_key)

    def get_session_by_thread(self, user_id: int, thread_id: int) -> Session | None:
        for session in self._sessions.get(user_id, {}).values():
            if session.thread_id == thread_id:
                return session
        return None

    def user_sessions(self, user_id: int) -> dict[str, Session]:
        return dict(self._sessions.get(user_id, {}))

    async def send(self, user_id: int, session_key: str, text: str) -> None:
        session = self._get_or_raise(user_id, session_key)
        if not session.process.alive:
            raise RuntimeError("Process has exited. Use /new to restart.")
        session.touch()
        session.turn_count += 1
        log.info("Writing to PTY [%s]: %.100s", session_key, text)
        await session.process.send(text)

    async def send_raw(self, user_id: int, session_key: str, data: str) -> None:
        """Send raw data (no newline appended) — used for special keys."""
        session = self._get_or_raise(user_id, session_key)
        if not session.process.alive:
            raise RuntimeError("Process has exited. Tap Restart to try again.")
        session.touch()
        await session.process.send_raw(data)

    async def interrupt(self, user_id: int, session_key: str) -> None:
        session = self._get_or_raise(user_id, session_key)
        await session.process.interrupt()

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _kill_one_locked(self, user_id: int, session_key: str) -> bool:
        user_map = self._sessions.get(user_id, {})
        session = user_map.pop(session_key, None)
        if session:
            if session._idle_task and not session._idle_task.done():
                session._idle_task.cancel()
            await session.process.stop()
            # Clean up empty user dict to prevent unbounded growth
            if not user_map and user_id in self._sessions:
                del self._sessions[user_id]
            return True
        return False

    def _get_or_raise(self, user_id: int, session_key: str) -> Session:
        s = self._sessions.get(user_id, {}).get(session_key)
        if not s:
            raise RuntimeError(f"No session '{session_key}'. Use /new to start one.")
        return s

    def _ensure_workdir(self) -> str:
        return config.pty_cwd()

    async def _idle_watcher(self, user_id: int, session_key: str) -> None:
        try:
            while True:
                await asyncio.sleep(60)
                session = self._sessions.get(user_id, {}).get(session_key)
                if session is None:
                    break
                if session.is_idle(config.idle_timeout()):
                    # Acquire lock explicitly instead of going through kill_session()
                    # to avoid fragile re-entrant lock acquisition patterns
                    async with self._lock:
                        await self._kill_one_locked(user_id, session_key)
                    break
        except asyncio.CancelledError:
            return
