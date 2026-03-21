"""Screen capture session — captures a specific window and streams JPEG frames.

Uses mss for fast capture and Pillow for JPEG encoding.  Window enumeration
uses ctypes Win32 API (no extra dependencies).

Capture runs at a configurable interval; the Telegram send side rate-limits
independently (~1.5 s between editMessageMedia calls).
"""
import asyncio
import io
import logging
import sys
from typing import Callable

log = logging.getLogger("telecode.screen")

_IS_WIN = sys.platform == "win32"


# ── Window enumeration (Windows only) ────────────────────────────────────────

def enumerate_windows() -> list[tuple[int, str]]:
    """Return (hwnd, title) for every visible window with a non-empty title."""
    if not _IS_WIN:
        return []
    import ctypes
    from ctypes import wintypes

    # Define the callback type for EnumWindows
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    # Set argtypes/restype so ctypes marshals the callback pointer and
    # LPARAM correctly on 64-bit Windows (without this, the callback
    # argument may be silently rejected or truncated).
    user32 = ctypes.windll.user32
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int

    windows: list[tuple[int, str]] = []

    @WNDENUMPROC
    def _cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.strip()
                if title:
                    windows.append((int(hwnd), title))
        return True

    user32.EnumWindows(_cb, wintypes.LPARAM(0))
    return windows


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Bounding box (left, top, right, bottom) or None if the window is gone."""
    if not _IS_WIN:
        return None
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return (rect.left, rect.top, rect.right, rect.bottom)
    return None


def is_window_valid(hwnd: int) -> bool:
    """Check if the window handle is still alive."""
    if not _IS_WIN:
        return False
    import ctypes
    return bool(ctypes.windll.user32.IsWindow(hwnd))


# ── ScreenCapture ─────────────────────────────────────────────────────────────

class ScreenCapture:
    """Captures a window and pushes JPEG frames to subscribers."""

    def __init__(self, hwnd: int, capture_interval: float = 0.5):
        self.hwnd = hwnd
        self.capture_interval = capture_interval
        self._subscribers: list[Callable[[bytes], None]] = []
        self.alive: bool = False
        self._paused: bool = False
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── Lifecycle (same shape as PTYProcess) ──────────────────────────────

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.alive = True
        self._task = asyncio.ensure_future(self._capture_loop())

    async def stop(self) -> None:
        self.alive = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    @property
    def paused(self) -> bool:
        return self._paused

    def subscribe(self, cb: Callable[[bytes], None]) -> None:
        if cb not in self._subscribers:
            self._subscribers.append(cb)

    def unsubscribe(self, cb: Callable[[bytes], None]) -> None:
        self._subscribers = [s for s in self._subscribers if s is not cb]

    # PTYProcess compatibility stubs
    async def send(self, text: str) -> None:
        pass

    async def send_raw(self, data: str) -> None:
        pass

    async def interrupt(self) -> None:
        pass

    # ── Capture loop ──────────────────────────────────────────────────────

    async def _capture_loop(self) -> None:
        import mss

        try:
            with mss.mss() as sct:
                while self.alive:
                    if self._paused:
                        await asyncio.sleep(0.5)
                        continue

                    if not is_window_valid(self.hwnd):
                        log.warning("Window %d no longer exists — stopping", self.hwnd)
                        self.alive = False
                        for cb in self._subscribers:
                            cb(b"")  # empty = signal "window gone"
                        break

                    try:
                        frame = await self._loop.run_in_executor(
                            None, self._capture_frame, sct
                        )
                        if frame:
                            for cb in self._subscribers:
                                cb(frame)
                    except Exception as e:
                        log.error("Capture error: %s", e)

                    await asyncio.sleep(self.capture_interval)
        except asyncio.CancelledError:
            return

    def _capture_frame(self, sct) -> bytes | None:
        from PIL import Image

        rect = get_window_rect(self.hwnd)
        if not rect:
            return None
        left, top, right, bottom = rect
        width = right - left
        height = bottom - top
        if width <= 0 or height <= 0:
            return None

        region = {"left": left, "top": top, "width": width, "height": height}
        screenshot = sct.grab(region)
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return buf.getvalue()
