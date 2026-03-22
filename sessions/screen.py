"""Screen image & video capture — captures a specific window.

Platform support:
- **Windows**: Win32 PrintWindow API — captures target window regardless of
  z-order (even behind other windows).  Session 0 services use
  WTSQueryUserToken + CreateProcessAsUser to spawn helpers.
- **Linux**: xdotool for window enumeration, ImageMagick `import -window` for
  per-window capture, mss region fallback.
- **macOS**: Quartz CGWindowListCopyWindowInfo for enumeration,
  `screencapture -l<wid>` for per-window capture, mss region fallback.

Video recording uses ffmpeg (must be on PATH) to encode JPEG frames into a
lightweight MP4.

Image capture runs at a configurable interval; the Telegram send side
rate-limits independently (~1.5 s between editMessageMedia calls).
"""
import asyncio
import io
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Callable

log = logging.getLogger("telecode.screen")

_IS_WIN = sys.platform == "win32"
_IS_MAC = sys.platform == "darwin"
_IS_LINUX = sys.platform.startswith("linux")


# ═══════════════════════════════════════════════════════════════════════════════
# Per-window capture
# ═══════════════════════════════════════════════════════════════════════════════

def restore_if_minimized(wid: int) -> None:
    """Auto-restore a minimized/hidden window before capture.

    Works on Windows, Linux (wmctrl/xdotool), and macOS (osascript).
    """
    if _IS_WIN:
        import ctypes
        user32 = ctypes.windll.user32
        if user32.IsIconic(wid):
            user32.ShowWindow(wid, 9)  # SW_RESTORE
            time.sleep(0.3)
    elif _IS_LINUX:
        try:
            subprocess.run(
                ["xdotool", "windowactivate", "--sync", str(wid)],
                capture_output=True, timeout=3,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    elif _IS_MAC:
        try:
            # AppleScript to unminimize by window id is unreliable;
            # activate the owning app instead
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to set visible of '
                 'first process whose unix id is (do shell script '
                 f'"lsof -p $(lsof -ti :{wid} 2>/dev/null) 2>/dev/null | head -1"'
                 ') to true'],
                capture_output=True, timeout=3,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass


def capture_window(hwnd: int) -> bytes | None:
    """Capture a window by its ID.  Returns JPEG bytes or None.

    Auto-restores minimized windows before capturing.
    On Windows uses PrintWindow (z-order independent).
    On macOS uses screencapture -l<wid>.
    On Linux uses ImageMagick import -window, falling back to mss region.
    """
    restore_if_minimized(hwnd)

    if _IS_WIN:
        return _capture_window_win32(hwnd)
    if _IS_MAC:
        return _capture_window_mac(hwnd)
    if _IS_LINUX:
        return _capture_window_linux(hwnd)
    return None


# ── Windows ──────────────────────────────────────────────────────────────────

def _capture_window_win32(hwnd: int) -> bytes | None:
    import ctypes
    from ctypes import wintypes
    from PIL import Image

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None

    wnd_dc = user32.GetWindowDC(hwnd)
    if not wnd_dc:
        return None
    mem_dc = gdi32.CreateCompatibleDC(wnd_dc)
    bitmap = gdi32.CreateCompatibleBitmap(wnd_dc, width, height)
    old_bmp = gdi32.SelectObject(mem_dc, bitmap)

    PW_RENDERFULLCONTENT = 2
    result = user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
    if not result:
        user32.PrintWindow(hwnd, mem_dc, 0)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", ctypes.c_ushort),
            ("biBitCount", ctypes.c_ushort),
            ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = width
    bmi.biHeight = -height
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0

    pixel_buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem_dc, bitmap, 0, height, pixel_buf, ctypes.byref(bmi), 0)

    gdi32.SelectObject(mem_dc, old_bmp)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, wnd_dc)

    img = Image.frombuffer("RGBA", (width, height), pixel_buf, "raw", "BGRA", 0, 1)
    img = img.convert("RGB")

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=80)
    return out.getvalue()


# ── macOS ────────────────────────────────────────────────────────────────────

def _capture_window_mac(wid: int) -> bytes | None:
    """Use screencapture -l<wid> for per-window capture on macOS."""
    fd, path = tempfile.mkstemp(suffix=".jpg", prefix="telecode_cap_")
    os.close(fd)
    try:
        r = subprocess.run(
            ["screencapture", "-x", f"-l{wid}", "-t", "jpg", path],
            capture_output=True, timeout=10,
        )
        if r.returncode != 0 or not os.path.exists(path):
            return _capture_region_fallback(wid)
        with open(path, "rb") as f:
            data = f.read()
        return data if data else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return _capture_region_fallback(wid)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ── Linux ────────────────────────────────────────────────────────────────────

def _capture_window_linux(wid: int) -> bytes | None:
    """Use ImageMagick import -window for per-window capture on Linux."""
    fd, path = tempfile.mkstemp(suffix=".jpg", prefix="telecode_cap_")
    os.close(fd)
    try:
        r = subprocess.run(
            ["import", "-window", str(wid), path],
            capture_output=True, timeout=10,
        )
        if r.returncode != 0 or not os.path.exists(path):
            return _capture_region_fallback(wid)
        with open(path, "rb") as f:
            data = f.read()
        return data if data else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return _capture_region_fallback(wid)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ── mss region fallback (Linux/Mac) ─────────────────────────────────────────

def _capture_region_fallback(wid: int) -> bytes | None:
    """Fallback: capture the screen region where the window is via mss."""
    rect = get_window_rect(wid)
    if not rect:
        return None
    left, top, right, bottom = rect
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return None
    try:
        import mss
        from PIL import Image
        region = {"left": left, "top": top, "width": width, "height": height}
        with mss.mss() as sct:
            screenshot = sct.grab(region)
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return buf.getvalue()
    except Exception as e:
        log.error("mss fallback capture failed: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Window enumeration
# ═══════════════════════════════════════════════════════════════════════════════

def enumerate_windows() -> list[tuple[int, str]]:
    """Return (window_id, title) for every visible window with a non-empty title."""
    if _IS_WIN:
        if _is_session_zero():
            log.info("Session 0 detected — enumerating via user session helper")
            result = _run_in_user_session(_ENUMERATE_SCRIPT_WIN)
            if not result:
                return []
            try:
                return [(int(w), str(t)) for w, t in json.loads(result)]
            except (json.JSONDecodeError, ValueError) as e:
                log.error("Parse error: %s", e)
                return []
        return _enumerate_win32()
    if _IS_MAC:
        return _enumerate_mac()
    if _IS_LINUX:
        return _enumerate_linux()
    return []


def _enumerate_win32() -> list[tuple[int, str]]:
    import ctypes
    from ctypes import wintypes

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
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


def _enumerate_mac() -> list[tuple[int, str]]:
    """Use Quartz CGWindowListCopyWindowInfo on macOS."""
    try:
        import Quartz  # type: ignore[import-untyped]
    except ImportError:
        # Fallback: use osascript
        return _enumerate_mac_osascript()

    kCGWindowListOptionOnScreenOnly = 1
    kCGNullWindowID = 0
    windows: list[tuple[int, str]] = []
    wlist = Quartz.CGWindowListCopyWindowInfo(
        kCGWindowListOptionOnScreenOnly, kCGNullWindowID
    )
    if wlist:
        for w in wlist:
            wid = w.get("kCGWindowNumber", 0)
            name = w.get("kCGWindowOwnerName", "")
            title = w.get("kCGWindowName", "")
            label = f"{name} — {title}" if title else name
            # Skip menu bar, dock, etc.
            layer = w.get("kCGWindowLayer", 0)
            if wid and label and layer == 0:
                windows.append((int(wid), label))
    return windows


def _enumerate_mac_osascript() -> list[tuple[int, str]]:
    """Fallback macOS enumeration via osascript."""
    script = '''
    tell application "System Events"
        set wlist to {}
        repeat with p in (every process whose visible is true)
            repeat with w in (every window of p)
                set end of wlist to {id of w, name of w, name of p}
            end repeat
        end repeat
        return wlist
    end tell
    '''
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return []
        # Parse the output (AppleScript list format is messy, use a simpler approach)
        # This is a best-effort fallback
        return []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def _enumerate_linux() -> list[tuple[int, str]]:
    """Use wmctrl -l on Linux (X11).  Falls back to xdotool."""
    # Try wmctrl first
    try:
        r = subprocess.run(
            ["wmctrl", "-l"], capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            windows: list[tuple[int, str]] = []
            for line in r.stdout.strip().splitlines():
                parts = line.split(None, 3)
                if len(parts) >= 4:
                    wid = int(parts[0], 16)
                    title = parts[3]
                    if title:
                        windows.append((wid, title))
            return windows
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass

    # Fallback: xdotool
    try:
        r = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--name", ""],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            windows = []
            for wid_str in r.stdout.strip().splitlines():
                try:
                    wid = int(wid_str)
                except ValueError:
                    continue
                # Get window title
                r2 = subprocess.run(
                    ["xdotool", "getwindowname", str(wid)],
                    capture_output=True, text=True, timeout=2,
                )
                title = r2.stdout.strip() if r2.returncode == 0 else ""
                if title:
                    windows.append((wid, title))
            return windows
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return []


# ═══════════════════════════════════════════════════════════════════════════════
# Window rect / validity (cross-platform)
# ═══════════════════════════════════════════════════════════════════════════════

def get_window_rect(wid: int) -> tuple[int, int, int, int] | None:
    """Return (left, top, right, bottom) for a window, or None."""
    if _IS_WIN:
        return _get_rect_win32(wid)
    if _IS_MAC:
        return _get_rect_mac(wid)
    if _IS_LINUX:
        return _get_rect_linux(wid)
    return None


def _get_rect_win32(hwnd: int) -> tuple[int, int, int, int] | None:
    import ctypes
    from ctypes import wintypes
    rect = wintypes.RECT()
    if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return (rect.left, rect.top, rect.right, rect.bottom)
    return None


def _get_rect_mac(wid: int) -> tuple[int, int, int, int] | None:
    try:
        import Quartz  # type: ignore[import-untyped]
        wlist = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionIncludingWindow, wid
        )
        if wlist and len(wlist) > 0:
            bounds = wlist[0].get("kCGWindowBounds", {})
            x = int(bounds.get("X", 0))
            y = int(bounds.get("Y", 0))
            w = int(bounds.get("Width", 0))
            h = int(bounds.get("Height", 0))
            return (x, y, x + w, y + h)
    except (ImportError, Exception):
        pass
    return None


def _get_rect_linux(wid: int) -> tuple[int, int, int, int] | None:
    try:
        r = subprocess.run(
            ["xdotool", "getwindowgeometry", "--shell", str(wid)],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode != 0:
            return None
        vals = {}
        for line in r.stdout.strip().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = int(v.strip())
        x = vals.get("X", 0)
        y = vals.get("Y", 0)
        w = vals.get("WIDTH", 0)
        h = vals.get("HEIGHT", 0)
        if w > 0 and h > 0:
            return (x, y, x + w, y + h)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def is_window_valid(wid: int) -> bool:
    """Check if the window is still alive."""
    if _IS_WIN:
        import ctypes
        return bool(ctypes.windll.user32.IsWindow(wid))
    if _IS_LINUX:
        try:
            r = subprocess.run(
                ["xdotool", "getwindowname", str(wid)],
                capture_output=True, timeout=2,
            )
            return r.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
    if _IS_MAC:
        rect = _get_rect_mac(wid)
        return rect is not None
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# Session 0 helpers (Windows only)
# ═══════════════════════════════════════════════════════════════════════════════

def _current_session_id() -> int:
    if not _IS_WIN:
        return -1
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32
    pid = kernel32.GetCurrentProcessId()
    sid = wintypes.DWORD()
    kernel32.ProcessIdToSessionId(pid, ctypes.byref(sid))
    return sid.value


def _is_session_zero() -> bool:
    return _IS_WIN and _current_session_id() == 0


def _run_in_user_session(script: str, timeout_ms: int = 15000) -> str | None:
    fd, script_path = tempfile.mkstemp(suffix=".py", prefix="telecode_helper_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        if not _run_in_user_session_raw(script_path, timeout_ms):
            return None
        out_path = script_path + ".out"
        if os.path.exists(out_path):
            try:
                with open(out_path, "r", encoding="utf-8") as f:
                    return f.read()
            finally:
                try:
                    os.unlink(out_path)
                except OSError:
                    pass
        return None
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


_ENUMERATE_SCRIPT_WIN = '''
import ctypes, json, os
from ctypes import wintypes
user32 = ctypes.windll.user32
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
windows = []
@WNDENUMPROC
def _cb(hwnd, _):
    if user32.IsWindowVisible(hwnd):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
            if title:
                windows.append([int(hwnd), title])
    return True
user32.EnumWindows(_cb, wintypes.LPARAM(0))
with open(__file__ + ".out", "w", encoding="utf-8") as f:
    json.dump(windows, f)
'''

# PrintWindow cross-session capture script (Windows only)
_CAPTURE_SCRIPT_WIN = '''
import ctypes, io, os, sys
from ctypes import wintypes
hwnd = {hwnd}
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
rect = wintypes.RECT()
if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
    open(__file__ + ".out", "wb").close(); sys.exit(0)
width = rect.right - rect.left
height = rect.bottom - rect.top
if width <= 0 or height <= 0:
    open(__file__ + ".out", "wb").close(); sys.exit(0)
wnd_dc = user32.GetWindowDC(hwnd)
mem_dc = gdi32.CreateCompatibleDC(wnd_dc)
bitmap = gdi32.CreateCompatibleBitmap(wnd_dc, width, height)
old_bmp = gdi32.SelectObject(mem_dc, bitmap)
user32.PrintWindow(hwnd, mem_dc, 2) or user32.PrintWindow(hwnd, mem_dc, 0)
class BMI(ctypes.Structure):
    _fields_ = [("biSize",ctypes.c_uint32),("biWidth",ctypes.c_long),
        ("biHeight",ctypes.c_long),("biPlanes",ctypes.c_ushort),
        ("biBitCount",ctypes.c_ushort),("biCompression",ctypes.c_uint32),
        ("biSizeImage",ctypes.c_uint32),("biXPelsPerMeter",ctypes.c_long),
        ("biYPelsPerMeter",ctypes.c_long),("biClrUsed",ctypes.c_uint32),
        ("biClrImportant",ctypes.c_uint32)]
bmi = BMI(); bmi.biSize = ctypes.sizeof(BMI)
bmi.biWidth = width; bmi.biHeight = -height; bmi.biPlanes = 1; bmi.biBitCount = 32
pixel_buf = ctypes.create_string_buffer(width * height * 4)
gdi32.GetDIBits(mem_dc, bitmap, 0, height, pixel_buf, ctypes.byref(bmi), 0)
gdi32.SelectObject(mem_dc, old_bmp); gdi32.DeleteObject(bitmap)
gdi32.DeleteDC(mem_dc); user32.ReleaseDC(hwnd, wnd_dc)
from PIL import Image
img = Image.frombuffer("RGBA",(width,height),pixel_buf,"raw","BGRA",0,1).convert("RGB")
buf = io.BytesIO(); img.save(buf, format="JPEG", quality=80)
with open(__file__ + ".out", "wb") as f: f.write(buf.getvalue())
'''


def _run_in_user_session_raw(script_path: str, timeout_ms: int = 15000) -> bool:
    """Run a script in the interactive user's session (Windows Session 0 only)."""
    if not _IS_WIN:
        return False

    import win32ts
    import win32process
    import win32security
    import win32profile
    import win32api
    import win32con
    import win32event

    session_id = win32ts.WTSGetActiveConsoleSessionId()
    if session_id == 0xFFFFFFFF:
        log.warning("No active console session")
        return False

    try:
        user_token = win32ts.WTSQueryUserToken(session_id)
    except Exception as e:
        log.warning("WTSQueryUserToken failed (session %d): %s", session_id, e)
        return False

    try:
        dup_token = win32security.DuplicateTokenEx(
            user_token, win32security.TOKEN_ALL_ACCESS, None,
            win32security.SecurityImpersonation, win32security.TokenPrimary,
        )
        try:
            env = win32profile.CreateEnvironmentBlock(dup_token, False)
            cmd = f'"{sys.executable}" "{script_path}"'
            si = win32process.STARTUPINFO()
            si.lpDesktop = "WinSta0\\Default"
            flags = win32con.CREATE_UNICODE_ENVIRONMENT | win32con.CREATE_NO_WINDOW
            hProcess, hThread, _, _ = win32process.CreateProcessAsUser(
                dup_token, None, cmd, None, None, False, flags, env, None, si,
            )
            win32event.WaitForSingleObject(hProcess, timeout_ms)
            win32api.CloseHandle(hProcess)
            win32api.CloseHandle(hThread)
            return True
        finally:
            win32api.CloseHandle(dup_token)
    except Exception as e:
        log.error("CreateProcessAsUser failed: %s", e, exc_info=True)
        return False
    finally:
        win32api.CloseHandle(user_token)


# ═══════════════════════════════════════════════════════════════════════════════
# ScreenCapture (image streaming)
# ═══════════════════════════════════════════════════════════════════════════════

class ScreenCapture:
    """Captures a window and pushes JPEG frames to subscribers."""

    # Default matches SCREEN_CAPTURE_INTERVAL in handlers.py
    def __init__(self, hwnd: int, capture_interval: float = 3.0):
        self.hwnd = hwnd
        self.capture_interval = capture_interval
        self._subscribers: list[Callable[[bytes], None]] = []
        self.alive: bool = False
        self._paused: bool = False
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session_zero = _is_session_zero()
        if self._session_zero and self.capture_interval < 2.0:
            self.capture_interval = 2.0

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

    async def send(self, text: str) -> None:
        pass

    async def send_raw(self, data: str) -> None:
        pass

    async def interrupt(self) -> None:
        pass

    async def _capture_loop(self) -> None:
        try:
            while self.alive:
                if self._paused:
                    await asyncio.sleep(0.5)
                    continue
                try:
                    frame = await self._loop.run_in_executor(
                        None, self._capture_frame
                    )
                    if frame is None:
                        log.warning("Window %d — capture returned None, stopping", self.hwnd)
                        self.alive = False
                        for cb in self._subscribers:
                            cb(b"")
                        break
                    if frame:
                        for cb in self._subscribers:
                            cb(frame)
                except Exception as e:
                    log.error("Capture error: %s", e)
                await asyncio.sleep(self.capture_interval)
        except asyncio.CancelledError:
            return

    def _capture_frame(self) -> bytes | None:
        if _IS_WIN and self._session_zero:
            return self._capture_frame_cross_session()
        return capture_window(self.hwnd)

    def _capture_frame_cross_session(self) -> bytes | None:
        script = _CAPTURE_SCRIPT_WIN.format(hwnd=self.hwnd)
        fd, script_path = tempfile.mkstemp(suffix=".py", prefix="telecode_cap_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(script)
            out_path = script_path + ".out"
            _run_in_user_session_raw(script_path, timeout_ms=10000)
            if os.path.exists(out_path):
                try:
                    with open(out_path, "rb") as f:
                        data = f.read()
                    return data if data and data[:2] == b'\xff\xd8' else None
                finally:
                    try:
                        os.unlink(out_path)
                    except OSError:
                        pass
            return b""
        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# VideoCapture (1-minute recording)
# ═══════════════════════════════════════════════════════════════════════════════

class VideoCapture:
    """Records a window for a fixed duration and delivers an MP4 via callback."""

    def __init__(self, hwnd: int, duration: int = 60, fps: int = 3):
        self.hwnd = hwnd
        self.duration = duration
        self.fps = fps
        self._text_subscribers: list[Callable[[str], None]] = []
        self._video_subscribers: list[Callable[[bytes], None]] = []
        self.alive: bool = False
        self._paused: bool = False
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._temp_dir: str | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.alive = True
        self._task = asyncio.ensure_future(self._record_loop())

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

    def subscribe(self, cb: Callable) -> None:
        if cb not in self._video_subscribers:
            self._video_subscribers.append(cb)

    def subscribe_text(self, cb: Callable[[str], None]) -> None:
        if cb not in self._text_subscribers:
            self._text_subscribers.append(cb)

    def unsubscribe(self, cb: Callable) -> None:
        self._video_subscribers = [s for s in self._video_subscribers if s is not cb]
        self._text_subscribers = [s for s in self._text_subscribers if s is not cb]

    async def send(self, text: str) -> None:
        pass

    async def send_raw(self, data: str) -> None:
        pass

    async def interrupt(self) -> None:
        pass

    async def _record_loop(self) -> None:
        """Continuously record 1-min chunks until stopped."""
        interval = 1.0 / self.fps
        chunk_num = 0

        try:
            while self.alive:
                chunk_num += 1
                # Record one chunk
                frame_count = await self._record_chunk(interval)

                if frame_count is None:
                    # Window gone
                    break

                if frame_count > 0:
                    video_bytes = await self._loop.run_in_executor(
                        None, self._encode, frame_count
                    )
                    if video_bytes:
                        for cb in self._video_subscribers:
                            cb(video_bytes)
                    else:
                        for cb in self._text_subscribers:
                            cb("Encoding failed.")

                # Clean up frames for next chunk
                self._cleanup_frames()

        except asyncio.CancelledError:
            # Stopped — encode whatever frames exist
            if self._temp_dir:
                actual = [f for f in os.listdir(self._temp_dir) if f.startswith("frame_")]
                if actual:
                    for cb in self._text_subscribers:
                        cb(f"Encoding {len(actual)} frames...")
                    video_bytes = await self._loop.run_in_executor(
                        None, self._encode, len(actual)
                    )
                    if video_bytes:
                        for cb in self._video_subscribers:
                            cb(video_bytes)
        finally:
            self.alive = False
            if self._temp_dir and os.path.exists(self._temp_dir):
                try:
                    shutil.rmtree(self._temp_dir)
                except OSError:
                    pass

    async def _record_chunk(self, interval: float) -> int | None:
        """Record one chunk (self.duration seconds).  Returns frame count, or None if window gone."""
        if not self._temp_dir:
            self._temp_dir = tempfile.mkdtemp(prefix="telecode_vid_")

        frame_count = 0
        start_time = time.monotonic()

        while self.alive:
            elapsed = time.monotonic() - start_time
            if elapsed >= self.duration:
                break

            if self._paused:
                await asyncio.sleep(0.5)
                start_time += 0.5
                continue

            if not is_window_valid(self.hwnd):
                log.warning("Window %d gone during video recording", self.hwnd)
                for cb in self._text_subscribers:
                    cb("Window closed.")
                return None

            try:
                frame = await self._loop.run_in_executor(
                    None, capture_window, self.hwnd
                )
                if frame:
                    frame_path = os.path.join(
                        self._temp_dir, f"frame_{frame_count:05d}.jpg"
                    )
                    with open(frame_path, "wb") as f:
                        f.write(frame)
                    frame_count += 1
            except Exception as e:
                log.error("Video frame capture error: %s", e)

            await asyncio.sleep(interval)

        return frame_count

    def _cleanup_frames(self) -> None:
        """Remove frame files from temp dir, keeping the dir for the next chunk."""
        if not self._temp_dir:
            return
        for f in os.listdir(self._temp_dir):
            if f.startswith("frame_") or f == "output.mp4":
                try:
                    os.unlink(os.path.join(self._temp_dir, f))
                except OSError:
                    pass

    def _encode(self, frame_count: int) -> bytes | None:
        if not self._temp_dir:
            return None

        actual = [f for f in os.listdir(self._temp_dir) if f.startswith("frame_")]
        if not actual:
            log.error("No frame files in %s", self._temp_dir)
            return None

        output_path = os.path.join(self._temp_dir, "output.mp4")
        input_pattern = os.path.join(self._temp_dir, "frame_%05d.jpg")

        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(self.fps),
            "-i", input_pattern,
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "32",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            output_path,
        ]

        try:
            creation = subprocess.CREATE_NO_WINDOW if _IS_WIN else 0
            r = subprocess.run(
                cmd, capture_output=True, timeout=30, creationflags=creation,
            )
            if r.returncode != 0:
                log.error("ffmpeg exit %d: %s", r.returncode,
                          r.stderr.decode(errors="replace")[-500:])
                return None
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            log.error("ffmpeg failed: %s", e)
            return None

        if os.path.exists(output_path):
            with open(output_path, "rb") as f:
                return f.read()
        log.error("ffmpeg output not found: %s", output_path)
        return None
