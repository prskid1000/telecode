"""Animation → MP4: deterministic frame capture + ffmpeg.

The page is never played in real time. For every frame ``t = i / fps`` the exporter
calls ``window.tdTimeline.seek(t)`` (the animations starter's timeline: ``{duration,
seek(t), play(), pause()}``, seconds), waits two animation frames, and screenshots —
so a slow machine produces the same video as a fast one.

Pages without ``tdTimeline`` fall back to the Web Animations API: every
``document.getAnimations()`` entry (CSS animations and transitions included) is paused
and its ``currentTime`` set to ``t``; ``options.duration`` (seconds, default 5) bounds
the clip. Flag ``no_timeline`` records the fallback.

Options: ``fps`` (1–60, default 30), ``duration`` (seconds, cap 120), ``width`` /
``height`` (default: the board size, else 1920×1080), ``scale`` (1–2), ``crf`` (default 18).

ffmpeg runs as ``subprocess.Popen(..., creationflags=CREATE_NO_WINDOW)`` bound to
telecode's Job Object, in a worker thread so the proxy loop never blocks.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

from services.design import render

log = logging.getLogger("telecode.services.design.video_export")

MAX_DURATION_SEC = 120.0
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_TIMELINE_INFO_JS = r"""
(() => {
  const tl = window.tdTimeline;
  if (tl && typeof tl.seek === 'function') {
    try { if (typeof tl.pause === 'function') tl.pause(); } catch (e) {}
    let d = typeof tl.duration === 'function' ? tl.duration() : tl.duration;
    return {timeline: true, duration: +d || 0};
  }
  const anims = document.getAnimations ? document.getAnimations() : [];
  let end = 0;
  for (const a of anims) {
    try { a.pause(); const t = a.effect && a.effect.getComputedTiming();
          if (t && isFinite(t.endTime)) end = Math.max(end, t.endTime); } catch (e) {}
  }
  return {timeline: false, duration: end / 1000, animations: anims.length};
})()
"""

_SEEK_JS = r"""
async (t, useTimeline) => {
  if (useTimeline) { await window.tdTimeline.seek(t); }
  else {
    for (const a of document.getAnimations()) { try { a.pause(); a.currentTime = t * 1000; } catch (e) {} }
  }
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  return true;
}
"""


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH")
    return exe


def _encode(frames_dir: Path, fps: int, out: Path, crf: int) -> None:
    args = [
        _ffmpeg(), "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(frames_dir / "%06d.jpg"),
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2:in_range=pc:out_range=tv,format=yuv420p", "-color_range", "tv",
        "-movflags", "+faststart", str(out),
    ]
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=_CREATE_NO_WINDOW)
    try:
        from process import bind_to_lifetime_job
        bind_to_lifetime_job(proc.pid, proc)
    except Exception:
        pass
    try:
        _, err = proc.communicate(timeout=600)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise RuntimeError("ffmpeg timed out")
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {err.decode('utf-8', 'replace')[-500:]}")


async def export(ctx) -> Path:
    from services.design.export import _board_size, _opt_float, _opt_int

    opts = ctx.options
    fps = _opt_int(opts, "fps", 30, 1, 60)
    bw, bh = _board_size(ctx.pid, ctx.file)
    if (bw, bh) == (1280, 800):
        bw, bh = 1920, 1080
    width = _opt_int(opts, "width", bw, 64, 3840)
    height = _opt_int(opts, "height", bh, 64, 3840)
    scale = _opt_float(opts, "scale", 1, 1, 2)
    crf = _opt_int(opts, "crf", 18, 0, 40)
    frames_dir = ctx.out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    try:
        async with render.open_page(width, height, scale) as page:
            await page.goto(ctx.url())
            await page.hide([".deck-controls", "[data-td-scrubber]", ".td-scrubber"]
                            + [s for s in (opts.get("hideSelectors") or []) if isinstance(s, str)])
            info = await page.eval(_TIMELINE_INFO_JS, await_promise=False) or {}
            use_tl = bool(info.get("timeline"))
            duration = float(info.get("duration") or 0)
            if use_tl and duration > 600:  # a timeline reporting milliseconds
                duration /= 1000.0
            if "duration" in opts:
                duration = _opt_float(opts, "duration", duration or 5, 0.1, MAX_DURATION_SEC)
            if duration <= 0:
                duration = 5.0
            duration = min(duration, MAX_DURATION_SEC)
            if not use_tl:
                ctx.flag("no_timeline", "The page has no window.tdTimeline; captured CSS/Web Animations by seeking "
                         f"document.getAnimations() ({info.get('animations', 0)} found)")
            total = max(1, int(round(duration * fps)))
            for i in range(total):
                await page.call(_SEEK_JS, i / fps, use_tl, timeout=30)
                data = await page.capture("jpeg", 92)
                (frames_dir / f"{i:06d}.jpg").write_bytes(data)
                if i % 5 == 0:
                    ctx.progress(0.05 + 0.8 * (i + 1) / total, f"Frame {i + 1}/{total}")
            if page.errors:
                ctx.flag("console_errors", f"{len(page.errors)} console error(s) while rendering",
                         errors=page.errors[:5])
        ctx.progress(0.88, "Encoding MP4")
        out = ctx.out_dir / ctx.name("mp4")
        await asyncio.to_thread(_encode, frames_dir, fps, out, crf)
        ctx.job["stats"] = {"frames": total, "fps": fps, "duration": round(total / fps, 3),
                            "width": width, "height": height, "timeline": use_tl}
        return out
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)
