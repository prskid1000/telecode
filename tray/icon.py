"""Tray icon — monochrome white lightning bolt, transparent background.

Single image, never replaced at runtime. State is conveyed via the
tooltip text (set on Icon.title), not the icon image — avoids a known
pystray-on-Windows bug where reassigning Icon.icon registers a NEW tray
entry instead of modifying the existing one (visible as accumulating
ghost icons in the system tray overflow).

Public API:
    make_icon(size: int = 64) -> PIL.Image.Image
"""
from __future__ import annotations

from PIL import Image, ImageDraw

# Lightning-bolt polygon (canvas 64×64).
# Classic Z-bolt: top-right peak → left taper → mid-left jut → bottom point.
_BOLT = [
    (40, 4),
    (14, 36),
    (26, 36),
    (22, 60),
    (50, 28),
    (36, 28),
    (44, 4),
]


def _scale_points(pts, scale):
    return [(x * scale, y * scale) for (x, y) in pts]


def make_icon(size: int = 64) -> Image.Image:
    """Render the tray icon — monochrome white bolt, transparent background.

    4× supersampled then downscaled with LANCZOS for clean anti-aliasing
    at the OS's native tray size (16/24/32px on Windows).
    """
    SCALE = 4
    big = 64 * SCALE
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Mint bolt — pops on dark taskbars (Windows 11 dark theme) and stays
    # visible on light ones; matches voxtype's accent palette.
    pts = _scale_points(_BOLT, SCALE)
    d.polygon(pts, fill=(86, 224, 194, 255))

    if size != big:
        img = img.resize((size, size), Image.LANCZOS)
    return img


# Kept for backwards compat with any callers that pass a state arg —
# state no longer changes the image; tooltip is updated instead.
def make_icon_for_state(_state: str = "off", size: int = 64) -> Image.Image:
    return make_icon(size=size)
