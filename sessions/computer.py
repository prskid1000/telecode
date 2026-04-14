"""Computer control via vision LLM — captures a window, sends to LLM, executes actions.

The LLM sees screenshots of a target window and responds with JSON actions
(click, type, key, scroll, move, wait, screenshot). Actions are executed via
pyautogui, with coordinates translated from window-relative to absolute screen
positions.

Supports both OpenAI-compatible and Anthropic API formats — toggle via
``api.format`` in settings (``"openai"`` or ``"anthropic"``).  Works with
LM Studio, Ollama, vLLM, or any provider that supports either format.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
import sys
import tempfile
import time
from typing import Any, Callable

import aiohttp
from PIL import Image

import config
from sessions.screen import capture_window, get_window_title

# hwnd=0 sentinel means "full screen" mode
FULL_SCREEN_HWND = 0

log = logging.getLogger("telecode.computer")

_IS_WIN = sys.platform == "win32"




# ═══════════════════════════════════════════════════════════════════════════════
# Cursor position (logical coords, same space as pyautogui)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_cursor_pos() -> tuple[int | None, int | None]:
    """Return current cursor (x, y) in logical screen coords, or (None, None)."""
    if _IS_WIN:
        try:
            import ctypes
            from ctypes import wintypes
            pt = wintypes.POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                return pt.x, pt.y
        except Exception:
            pass
    else:
        try:
            import pyautogui
            return pyautogui.position()
        except Exception:
            pass
    return None, None


# ═══════════════════════════════════════════════════════════════════════════════
# Full-screen capture
# ═══════════════════════════════════════════════════════════════════════════════

def _capture_full_screen() -> bytes | None:
    """Capture the entire primary screen as JPEG bytes."""
    try:
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # primary monitor
            img = sct.grab(monitor)
            pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=80)
            return buf.getvalue()
    except Exception as e:
        log.warning("Full screen capture failed: %s", e)
        return None


def _get_screen_size() -> tuple[int, int]:
    """Return (width, height) of the primary monitor in logical coords (for pyautogui)."""
    if _IS_WIN:
        try:
            import ctypes
            w = ctypes.windll.user32.GetSystemMetrics(0)  # SM_CXSCREEN
            h = ctypes.windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN
            if w > 0 and h > 0:
                return w, h
        except Exception:
            pass
    try:
        import pyautogui
        return pyautogui.size()
    except Exception:
        return 1920, 1080


# ═══════════════════════════════════════════════════════════════════════════════
# Window geometry — needed to translate window-relative coords to screen coords
# ═══════════════════════════════════════════════════════════════════════════════

def _get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Return (left, top, width, height) of a window in logical coords.

    Uses GetWindowRect (not DWM) so the coordinates match pyautogui's
    coordinate space regardless of DPI scaling.
    """
    if _IS_WIN:
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        # Use GetWindowRect — returns logical (scaled) coordinates that
        # match what pyautogui expects. DwmGetWindowAttribute returns
        # physical pixels which would mismatch on high-DPI displays.
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return (rect.left, rect.top,
                    rect.right - rect.left, rect.bottom - rect.top)
        return None

    if sys.platform == "darwin":
        # macOS — use Quartz
        try:
            from Quartz import CGWindowListCopyWindowInfo, kCGWindowListOptionIncludingWindow, kCGNullWindowID
            info_list = CGWindowListCopyWindowInfo(kCGWindowListOptionIncludingWindow, hwnd)
            if info_list and len(info_list) > 0:
                bounds = info_list[0].get("kCGWindowBounds", {})
                return (int(bounds["X"]), int(bounds["Y"]),
                        int(bounds["Width"]), int(bounds["Height"]))
        except Exception:
            pass
        return None

    if sys.platform.startswith("linux"):
        import subprocess
        try:
            r = subprocess.run(
                ["xdotool", "getwindowgeometry", "--shell", str(hwnd)],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0:
                vals = {}
                for line in r.stdout.strip().split("\n"):
                    if "=" in line:
                        k, v = line.split("=", 1)
                        vals[k.strip()] = int(v.strip())
                # xdotool gives X, Y, WIDTH, HEIGHT
                r2 = subprocess.run(
                    ["xdotool", "getwindowgeometry", "--shell", str(hwnd)],
                    capture_output=True, text=True, timeout=2,
                )
                return (vals.get("X", 0), vals.get("Y", 0),
                        vals.get("WIDTH", 0), vals.get("HEIGHT", 0))
        except Exception:
            pass
        return None

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Action execution
# ═══════════════════════════════════════════════════════════════════════════════

def _execute_action(action: dict, win_left: int, win_top: int,
                    win_w: int, win_h: int, img_w: int, img_h: int) -> str:
    """Execute a single action dict. Returns a description of what was done."""
    import pyautogui
    pyautogui.FAILSAFE = False  # don't abort on corner moves

    act = action.get("action", "").lower()

    def _coord(val) -> int:
        """Coerce a coordinate value to int — handles list, float, str, etc."""
        if isinstance(val, (list, tuple)):
            val = val[0] if val else 0
        return int(float(val))

    def _abs(x, y) -> tuple[int, int]:
        """Convert image-relative coords to absolute screen coords.

        Image is in physical pixels, window rect is in logical pixels.
        x * win_w / img_w naturally converts physical → logical offset.
        Adding win_left/win_top (logical) gives the final pyautogui coord.
        """
        x, y = _coord(x), _coord(y)
        # img coords (physical px) → logical offset via ratio
        sx = win_left + int(x * win_w / img_w) if img_w else win_left + x
        sy = win_top + int(y * win_h / img_h) if img_h else win_top + y
        return sx, sy

    if act == "click":
        ax, ay = _abs(action.get("x", 0), action.get("y", 0))
        button = action.get("button", "left")
        if button == "double":
            pyautogui.doubleClick(ax, ay)
            return f"Double-clicked ({ax}, {ay})"
        elif button == "right":
            pyautogui.rightClick(ax, ay)
            return f"Right-clicked ({ax}, {ay})"
        else:
            pyautogui.click(ax, ay)
            return f"Clicked ({ax}, {ay})"

    elif act == "type":
        text = action.get("text", "")
        interval = action.get("interval", 0.02)
        pyautogui.typewrite(text, interval=interval) if text.isascii() else pyautogui.write(text)
        return f"Typed: {text[:50]}"

    elif act == "key":
        keys = action.get("keys", [])
        if isinstance(keys, str):
            keys = [keys]
        if len(keys) == 1:
            pyautogui.press(keys[0])
            return f"Pressed {keys[0]}"
        else:
            pyautogui.hotkey(*keys)
            return f"Hotkey {'+'.join(keys)}"

    elif act == "scroll":
        ax, ay = _abs(action.get("x", 0), action.get("y", 0))
        direction = action.get("direction", "down")
        amount = action.get("amount", 3)
        clicks = -amount if direction == "down" else amount
        pyautogui.moveTo(ax, ay)
        pyautogui.scroll(clicks)
        return f"Scrolled {direction} {amount} at ({ax}, {ay})"

    elif act == "move":
        ax, ay = _abs(action.get("x", 0), action.get("y", 0))
        pyautogui.moveTo(ax, ay)
        return f"Moved to ({ax}, {ay})"

    elif act == "wait":
        secs = min(action.get("seconds", 1), 10)  # cap at 10s
        time.sleep(secs)
        return f"Waited {secs}s"

    elif act == "screenshot":
        return "screenshot_requested"

    else:
        return f"Unknown action: {act}"


# ═══════════════════════════════════════════════════════════════════════════════
# LLM API call
# ═══════════════════════════════════════════════════════════════════════════════

_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string", "description": "Brief reasoning about what you see and plan to do"},
        "done": {"type": "boolean", "description": "True when the task is fully complete"},
        "action": {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["click", "type", "key", "scroll", "move", "wait"]},
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "button": {"type": "string"},
                        "text": {"type": "string"},
                        "keys": {"type": "array", "items": {"type": "string"}},
                        "direction": {"type": "string"},
                        "amount": {"type": "integer"},
                        "seconds": {"type": "number"},
                    },
                    "required": ["action"],
                },
                {"type": "null"},
            ],
            "description": "Single action to perform, or null if done",
        },
    },
    "required": ["thought", "done", "action"],
}


async def _call_vision_llm(
    messages: list[dict],
    base_url: str,
    api_key: str,
    model: str,
    api_format: str = "openai",
    session_id: str | None = None,
) -> tuple[str, str | None]:
    """Call a vision LLM. Supports OpenAI, Anthropic, and Claude Code formats.

    Returns (response_text, session_id).  session_id is only set by the
    ``claude-code`` format; other formats pass through the input value.
    """
    if api_format == "claude-code":
        return await _call_vision_llm_claude_code(messages, base_url, api_key, model, session_id)
    if api_format == "anthropic":
        return await _call_vision_llm_anthropic(messages, base_url, api_key, model), session_id
    return await _call_vision_llm_openai(messages, base_url, api_key, model), session_id


async def _call_vision_llm_openai(
    messages: list[dict],
    base_url: str,
    api_key: str,
    model: str,
) -> str:
    """Call OpenAI-compatible chat/completions with vision."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.1,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "computer_action",
                "strict": True,
                "schema": _JSON_SCHEMA,
            },
        },
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"LLM API error {resp.status}: {body[:500]}")
            data = await resp.json()
            return data["choices"][0]["message"]["content"]


async def _call_vision_llm_anthropic(
    messages: list[dict],
    base_url: str,
    api_key: str,
    model: str,
) -> str:
    """Call Anthropic-format /messages endpoint with vision."""
    url = f"{base_url.rstrip('/')}/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key or "",
        "anthropic-version": "2023-06-01",
    }

    # Separate system prompt from conversation messages
    system_text = ""
    conv_messages: list[dict] = []
    for msg in messages:
        if msg["role"] == "system":
            system_text = msg["content"] if isinstance(msg["content"], str) else ""
        else:
            conv_messages.append(_to_anthropic_message(msg))

    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 4096,
        "temperature": 0.1,
        "messages": conv_messages,
    }
    if system_text:
        payload["system"] = system_text

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"LLM API error {resp.status}: {body[:500]}")
            data = await resp.json()
            # Anthropic returns content as a list of blocks
            blocks = data.get("content", [])
            texts = [b["text"] for b in blocks if b.get("type") == "text"]
            return "\n".join(texts)


def _to_anthropic_message(msg: dict) -> dict:
    """Convert an OpenAI-format message to Anthropic format."""
    role = msg["role"]
    content = msg.get("content", "")

    # Simple text message
    if isinstance(content, str):
        return {"role": role, "content": content}

    # Multi-part content (text + images)
    anthropic_parts: list[dict] = []
    for part in content:
        if part.get("type") == "text":
            anthropic_parts.append({"type": "text", "text": part["text"]})
        elif part.get("type") == "image_url":
            # Extract base64 data from data URI
            data_uri = part["image_url"]["url"]
            # Format: data:image/jpeg;base64,<data>
            if data_uri.startswith("data:"):
                header, b64data = data_uri.split(",", 1)
                # Extract media type: "data:image/jpeg;base64" -> "image/jpeg"
                media_type = header.split(":")[1].split(";")[0]
            else:
                b64data = data_uri
                media_type = "image/jpeg"
            anthropic_parts.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64data,
                },
            })
    return {"role": role, "content": anthropic_parts}


async def _call_vision_llm_claude_code(
    messages: list[dict],
    base_url: str,
    api_key: str,
    model: str,
    session_id: str | None = None,
) -> tuple[str, str | None]:
    """Call Claude Code CLI (``claude -p``) with a screenshot and return structured JSON.

    Uses ``--resume`` to maintain conversation continuity across turns.
    When ``base_url`` / ``api_key`` / ``model`` are provided they are passed
    as environment variables so the same settings block works for both the
    cloud Claude Code and a local LM Studio backend.
    Returns (response_text, session_id).
    """
    # Extract latest user text and image from the message list
    latest_text = ""
    latest_image_b64: str | None = None
    system_text = ""

    for msg in messages:
        if msg["role"] == "system":
            system_text = msg["content"] if isinstance(msg["content"], str) else ""
        elif msg["role"] == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                latest_text = content
            elif isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        latest_text = part["text"]
                    elif part.get("type") == "image_url":
                        data_uri = part["image_url"]["url"]
                        if data_uri.startswith("data:"):
                            _, b64data = data_uri.split(",", 1)
                            latest_image_b64 = b64data
                        else:
                            latest_image_b64 = data_uri

    # Save screenshot to a temp file so claude can see it
    tmp_path: str | None = None
    try:
        if latest_image_b64:
            fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
            os.write(fd, base64.b64decode(latest_image_b64))
            os.close(fd)

        # Build the prompt — include system prompt on the first call only
        if session_id:
            prompt = latest_text
        else:
            prompt = f"{system_text}\n\n{latest_text}" if system_text else latest_text

        # Build command
        cmd: list[str] = ["claude", "-p", prompt]
        if tmp_path:
            cmd.append(tmp_path)
        cmd += ["--output-format", "json",
                "--json-schema", json.dumps(_JSON_SCHEMA),
                "--max-turns", "1"]
        if session_id:
            cmd += ["--resume", session_id]

        # Build env — inherit current env, overlay API settings when provided
        env = os.environ.copy()
        if base_url:
            env["ANTHROPIC_BASE_URL"] = base_url
        if api_key:
            env["ANTHROPIC_AUTH_TOKEN"] = api_key
        if model:
            env["ANTHROPIC_MODEL"] = model
        # Useful defaults for local backends (no-ops for cloud)
        env.setdefault("DISABLE_PROMPT_CACHING", "1")

        log.debug("claude-code cmd: %s", " ".join(cmd[:6]) + " ...")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=180,
        )

        if proc.returncode != 0:
            err = stderr.decode(errors="replace")[:500]
            raise RuntimeError(f"Claude Code exited {proc.returncode}: {err}")

        data = json.loads(stdout.decode(errors="replace"))
        new_session_id = data.get("session_id", session_id)

        # Structured output takes priority, fall back to result text
        if data.get("structured_output"):
            result_text = json.dumps(data["structured_output"])
        else:
            result_text = data.get("result", "")

        return result_text, new_session_id

    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _parse_llm_response(raw: str) -> tuple[str, bool, dict | None]:
    """Parse LLM response into (thought, done, action).

    Returns:
        thought: reasoning text
        done: True if LLM says task is complete
        action: single action dict, or None if done/no action
    """
    text = raw.strip()

    # Try to extract JSON from code blocks
    code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if code_block:
        text = code_block.group(1)

    def _extract(obj: dict) -> tuple[str, bool, dict | None]:
        thought = obj.get("thought", "")
        done = bool(obj.get("done", False))
        # Support both "action" (singular, new) and "actions" (list, old)
        action = obj.get("action")
        if action and isinstance(action, dict):
            return thought, done, action
        actions = obj.get("actions", [])
        if isinstance(actions, list) and actions:
            return thought, done, actions[0]
        return thought, done or (not action and not actions), None

    # Try to parse as JSON
    try:
        obj = json.loads(text)
        return _extract(obj)
    except (json.JSONDecodeError, AttributeError):
        pass

    # Fallback: try to find any JSON object in the text
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if json_match:
        try:
            return _extract(json.loads(json_match.group()))
        except json.JSONDecodeError:
            pass

    # Plain text response — treat as done
    return text, True, None


# ═══════════════════════════════════════════════════════════════════════════════
# System prompt
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_SYSTEM_PROMPT = """\
You are a computer control agent. You see screenshots and perform actions on a computer, \
just like a human user sitting in front of it.

## How this works
- You receive a screenshot of the screen as an image.
- A RED CROSSHAIR (+) on the screenshot marks the current mouse pointer position.
- You respond with ONE action. It is executed, then you get a new screenshot.
- Repeat until the user's task is complete.

## Coordinates
- Top-left of the screenshot is x=0, y=0.
- x increases going right, y increases going down.
- To interact with an element, estimate the x,y pixel position of its center in the image.

## How a computer works — mouse, focus, and text cursor

The mouse pointer and the text cursor are two different things:
- The MOUSE POINTER is what you move around the screen (shown as the red crosshair).
- The TEXT CURSOR (blinking line |) appears inside text fields and shows where text \
will be inserted or deleted.

Clicking the mouse does two things: it gives that element FOCUS, and if it is a text field, \
it places the text cursor at that position. Only the focused element receives keyboard input.

To interact with anything:
1. First click on the element. This gives it focus.
2. Now keyboard actions (type, key) will affect that focused element.

## Mouse actions

Single click: selects an element, gives it focus, places text cursor in text fields.
Double click: selects a whole word in text, or opens files/items.
Right click: opens a context menu with options. Then click the option you want.
Hover (move): moving the mouse over an element can reveal tooltips or hidden menus.

## Working with text

All text operations depend on where the text cursor is and what text is selected:
- Clicking inside text places the text cursor at that exact character position.
- The text cursor determines where new text is inserted and where deletions happen.
- backspace deletes the character BEFORE the cursor. delete removes the character AFTER it.
- Arrow keys (left, right, up, down) move the text cursor within text.
- home moves cursor to start of line. end moves to end of line.

Selecting text:
- Double-click selects a single word.
- shift + arrow keys extends the selection character by character.
- ctrl+shift + left/right extends the selection word by word.
- ctrl+a selects ALL text in the focused element.
- Selected text appears highlighted. Any typing REPLACES the selected text.

Editing text:
- To insert text: click where you want to insert, then type.
- To replace all text: click the field, ctrl+a to select all, then type the new text.
- To delete all text: click the field, ctrl+a to select all, then backspace.
- To delete one word: ctrl+backspace removes the word before the cursor.
- To undo a mistake: ctrl+z. To redo: ctrl+y.

Clipboard:
- ctrl+c copies selected text. ctrl+x cuts it. ctrl+v pastes at the cursor position.

## Reading the screen

Before acting, understand what you see:
- The focused element usually has a blue outline, border highlight, or different background.
- Greyed-out or faded elements are disabled — you cannot interact with them.
- A blinking text cursor (|) shows where typing will appear.
- Loading spinners, progress bars, or "Loading..." text means the app is busy — use wait.
- Notifications or toast messages appear briefly at the edges and disappear on their own.
- A darkened/dimmed background with a box on top is a modal dialog — you must close it \
(click its buttons or press escape) before you can interact with anything behind it.

## Interacting with UI elements

Buttons, links, tabs: click on them.
Text fields, search boxes, address bars: click to focus, then type.
Dropdowns / select boxes: click to open, wait for the list to appear, then click an option.
Checkboxes: click to check or uncheck. Radio buttons: click one to select it.
Sliders: click and drag, or click on the track where you want the value.
Scrollable areas: use scroll action at that area's position.
Tooltips: move the mouse over an element and wait — a small label may appear explaining it.
Dialogs and popups: click OK/Cancel/Close or press enter to confirm, escape to cancel.
Window controls: close (X), maximize, minimize buttons are at the top-right corner of a window.
Tabs (in browsers or apps): click a tab to switch to it. ctrl+t opens a new tab. ctrl+w closes current tab.
Multi-select: ctrl+click to select/deselect individual items. shift+click to select a range.

## Navigation and keyboard shortcuts

tab: moves focus to the next interactive element in a form or page.
shift+tab: moves focus to the previous element.
enter: submits a form, confirms a dialog, or activates the focused button.
escape: closes menus, popups, dialogs, or cancels the current operation.
alt+tab: switches between open windows.
ctrl+f: opens find/search within the current page or document.
ctrl+z: undo. ctrl+y: redo.
ctrl+s: save in most applications.

## Timing and patience

Some actions take time. After clicking a link, submitting a form, or opening an application, \
the screen may need time to update. If the screenshot looks the same, shows a loading spinner, \
or shows a blank page, use a wait action before continuing. Do not repeat the same action \
if the page is still loading.

## Available actions

click: {"action": "click", "x": <int>, "y": <int>}
click: {"action": "click", "x": <int>, "y": <int>, "button": "right|double"}
type:  {"action": "type", "text": "<string>"}
key:   {"action": "key", "keys": ["<key>"]}
key:   {"action": "key", "keys": ["<modifier>", "<key>"]}
scroll: {"action": "scroll", "x": <int>, "y": <int>, "direction": "up|down", "amount": <int>}
wait:  {"action": "wait", "seconds": <number>}

## Available keys
enter, backspace, tab, escape, space, delete, up, down, left, right, \
home, end, pageup, pagedown, f1-f12
Modifiers: ctrl, alt, shift

## Rules
- ONE action per response. Verify the result in the next screenshot before continuing.
- Always click to focus an element before typing or using keyboard shortcuts.
- Set done=true only when the user's task is fully complete.

## Response format (JSON only, no other text)
Not done: {"thought": "<reasoning>", "done": false, "action": {<action>}}
Done:     {"thought": "<reasoning>", "done": true, "action": null}
"""


# ═══════════════════════════════════════════════════════════════════════════════
# ComputerControl — the "process" duck type
# ═══════════════════════════════════════════════════════════════════════════════

class ComputerControl:
    """Vision-LLM-driven computer control session.

    Duck-type compatible with PTYProcess / ScreenCapture:
      .alive, .start(), .stop(), .subscribe(cb), .send(text)

    Text subscriber receives status/thought text (like PTY output).
    Frame subscriber receives JPEG bytes (screenshots after actions).
    """

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self.alive = False
        self.paused = False

        # Subscribers
        self._text_subscribers: list[Callable[[str], None]] = []
        self._frame_subscribers: list[Callable[[bytes], None]] = []

        # Conversation history for the LLM
        self._history: list[dict] = []
        self._max_history: int = 20  # max user+assistant turns to keep

        # Pending user message queue
        self._msg_queue: asyncio.Queue[str] = asyncio.Queue()

        # Claude Code session id (for --resume across turns)
        self._llm_session_id: str | None = None

        # Main loop task
        self._task: asyncio.Task | None = None

    def subscribe(self, callback: Callable[[str], None]) -> None:
        """Subscribe to text output (thoughts, action summaries)."""
        self._text_subscribers.append(callback)

    def subscribe_frame(self, callback: Callable[[bytes], None]) -> None:
        """Subscribe to frame output (screenshots after actions)."""
        self._frame_subscribers.append(callback)

    def _emit_text(self, text: str) -> None:
        for cb in self._text_subscribers:
            try:
                cb(text)
            except Exception as e:
                log.warning("Text subscriber error: %s", e)

    def _emit_frame(self, jpeg_bytes: bytes) -> None:
        for cb in self._frame_subscribers:
            try:
                cb(jpeg_bytes)
            except Exception as e:
                log.warning("Frame subscriber error: %s", e)

    async def start(self) -> None:
        self.alive = True
        self._task = asyncio.ensure_future(self._run_loop())

    async def stop(self) -> None:
        self.alive = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    async def send(self, text: str) -> None:
        """Queue a user message for the LLM."""
        await self._msg_queue.put(text)

    @property
    def is_full_screen(self) -> bool:
        return self.hwnd == FULL_SCREEN_HWND

    def _capture(self) -> tuple[bytes | None, int, int]:
        """Capture window (or full screen) with cursor drawn on it."""
        if self.is_full_screen:
            jpeg = _capture_full_screen()
        else:
            jpeg = capture_window(self.hwnd)
        if not jpeg:
            return None, 0, 0
        img = Image.open(io.BytesIO(jpeg))

        # Draw cursor position onto the image
        img = self._draw_cursor(img)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return buf.getvalue(), img.width, img.height

    def _draw_cursor(self, img: Image.Image) -> Image.Image:
        """Draw a cursor marker on the screenshot at the current mouse position."""
        try:
            cursor_x, cursor_y = _get_cursor_pos()
            if cursor_x is None:
                return img

            # Convert screen coords to image-relative coords
            if self.is_full_screen:
                scr_w, scr_h = _get_screen_size()
                # Screen logical → image physical
                ix = int(cursor_x * img.width / scr_w) if scr_w else cursor_x
                iy = int(cursor_y * img.height / scr_h) if scr_h else cursor_y
            else:
                rect = _get_window_rect(self.hwnd)
                if not rect:
                    return img
                wl, wt, ww, wh = rect
                # Logical screen coord → offset within window → scale to image
                ix = int((cursor_x - wl) * img.width / ww) if ww else 0
                iy = int((cursor_y - wt) * img.height / wh) if wh else 0

            # Skip if cursor is outside the image
            if ix < 0 or iy < 0 or ix >= img.width or iy >= img.height:
                return img

            # Draw a small crosshair cursor
            from PIL import ImageDraw
            draw = ImageDraw.Draw(img)
            size = max(12, min(img.width, img.height) // 100)
            # Red crosshair with white outline for visibility
            for color, width in [((255, 255, 255), 3), ((255, 0, 0), 1)]:
                draw.line([(ix - size, iy), (ix + size, iy)], fill=color, width=width)
                draw.line([(ix, iy - size), (ix, iy + size)], fill=color, width=width)
                draw.ellipse(
                    [(ix - size // 2, iy - size // 2), (ix + size // 2, iy + size // 2)],
                    outline=color, width=width,
                )
        except Exception as e:
            log.warning("Failed to draw cursor: %s", e)
        return img

    def _build_vision_message(self, role: str, text: str, jpeg_b64: str | None = None) -> dict:
        """Build a chat message, optionally with an image."""
        if jpeg_b64:
            return {
                "role": role,
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{jpeg_b64}",
                    }},
                ],
            }
        return {"role": role, "content": text}

    def _trim_history(self) -> None:
        """Keep only the last N turns of history (excluding system prompt)."""
        if len(self._history) > self._max_history:
            # Keep system prompt (index 0) + last N messages
            self._history = self._history[:1] + self._history[-(self._max_history - 1):]

    async def _run_loop(self) -> None:
        """Main loop — wait for user messages, capture, call LLM, execute."""
        try:
            # Initialize system prompt
            sys_prompt = config.computer_system_prompt() or DEFAULT_SYSTEM_PROMPT
            self._history = [{"role": "system", "content": sys_prompt}]
            self._max_history = config.computer_max_history()

            # Verify we can capture before accepting messages
            jpeg, img_w, img_h = self._capture()
            if not jpeg:
                self._emit_text("Could not capture window. It may have been closed.")
                self.alive = False
                return

            label = "your screen" if self.is_full_screen else "this window"
            self._emit_text(f"Ready. Send a message to control {label}.")

            while self.alive:
                # Wait for user message
                try:
                    user_text = await asyncio.wait_for(self._msg_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                if self.paused:
                    self._emit_text("Session is paused. Use /resume first.")
                    continue

                # Action loop — one action at a time until done or interrupted
                is_first_turn = True
                while self.alive and not self.paused:
                    # Check if user sent a new message (interrupts the loop)
                    if not is_first_turn:
                        try:
                            new_msg = self._msg_queue.get_nowait()
                            user_text = new_msg
                            is_first_turn = True
                            continue
                        except asyncio.QueueEmpty:
                            pass

                    # Capture screenshot
                    jpeg, img_w, img_h = self._capture()
                    if not jpeg:
                        self._emit_text("Capture failed. Session ending.")
                        self.alive = False
                        return

                    jpeg_b64 = base64.b64encode(jpeg).decode("ascii")

                    # Build message for LLM
                    if is_first_turn:
                        msg = self._build_vision_message("user", user_text, jpeg_b64)
                    else:
                        msg = self._build_vision_message("user", "Continue.", jpeg_b64)
                    self._history.append(msg)
                    self._trim_history()
                    is_first_turn = False

                    # Call the vision LLM — raced against the user message queue
                    # so a new instruction sent mid-call interrupts immediately
                    # instead of waiting for the LLM response.
                    self._emit_text("Thinking...")
                    llm_task = asyncio.ensure_future(_call_vision_llm(
                        messages=self._history,
                        base_url=config.computer_api_base_url(),
                        api_key=config.computer_api_key(),
                        model=config.computer_model(),
                        api_format=config.computer_api_format(),
                        session_id=self._llm_session_id,
                    ))
                    queue_task = asyncio.ensure_future(self._msg_queue.get())
                    done, pending = await asyncio.wait(
                        {llm_task, queue_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if queue_task in done and llm_task not in done:
                        # User interrupted — cancel LLM, restart with new instruction.
                        llm_task.cancel()
                        try:
                            await llm_task
                        except BaseException:
                            pass
                        user_text = queue_task.result()
                        self._history.pop()  # drop the user msg for the cancelled turn
                        is_first_turn = True
                        continue
                    # LLM finished first — drain queue_task.
                    queue_task.cancel()
                    try:
                        await queue_task
                    except BaseException:
                        pass
                    try:
                        raw_response, new_sid = llm_task.result()
                        if new_sid:
                            self._llm_session_id = new_sid
                    except Exception as e:
                        self._emit_text(f"LLM error: {e}")
                        self._history.pop()
                        break

                    # Parse response
                    thought, done, action = _parse_llm_response(raw_response)
                    self._history.append({"role": "assistant", "content": raw_response})
                    self._trim_history()

                    if thought:
                        self._emit_text(thought)

                    if done or not action:
                        # Task complete — send final screenshot
                        await asyncio.sleep(0.3)
                        jpeg, _, _ = self._capture()
                        if jpeg:
                            self._emit_frame(jpeg)
                        break

                    # Get window/screen rect for coordinate translation
                    if self.is_full_screen:
                        scr_w, scr_h = _get_screen_size()
                        win_left, win_top, win_w, win_h = 0, 0, scr_w, scr_h
                    else:
                        rect = _get_window_rect(self.hwnd)
                        if not rect:
                            self._emit_text("Cannot get window position.")
                            break
                        win_left, win_top, win_w, win_h = rect

                    # Execute the single action
                    is_wait = action.get("action", "").lower() == "wait"
                    if is_wait:
                        # Handle wait asynchronously instead of blocking a thread
                        secs = min(float(action.get("seconds", 1)), 30)
                        self._emit_text(f"Waiting {secs}s...")
                        await asyncio.sleep(secs)
                        result = f"Waited {secs}s"
                    else:
                        try:
                            result = await asyncio.get_event_loop().run_in_executor(
                                None, _execute_action,
                                action, win_left, win_top, win_w, win_h, img_w, img_h,
                            )
                        except Exception as e:
                            self._emit_text(f"Error: {e}")
                            log.warning("Action execution error: %s", e, exc_info=True)
                            break

                    if result != "screenshot_requested":
                        self._emit_text(result)

                    # Wait for UI to settle, then send post-action screenshot
                    if not is_wait:
                        await asyncio.sleep(0.5)
                    jpeg, _, _ = self._capture()
                    if jpeg:
                        self._emit_frame(jpeg)

        except asyncio.CancelledError:
            return
        except Exception as e:
            log.error("ComputerControl loop error: %s", e, exc_info=True)
            self._emit_text(f"Session error: {e}")
        finally:
            self.alive = False
