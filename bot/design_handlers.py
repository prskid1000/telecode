"""Telegram `/design` — drive TeleDesign from a forum topic.

    /design <prompt>     in a design topic: next turn in that topic's chat
                         in any other topic: new project, this topic becomes its chat
                         in General: new project + a new "🎨 <title>" topic
    /design comments     open comments on this topic's project
    /design stop         cancel the running turn in this topic's chat
    /design status       project, chat and last turn of this topic

When the turn finishes the bot posts the reply excerpt, the changed files and the
project thumbnail (or a fresh screenshot) as a photo.

Notify-on-done: with `design.telegram_notify: true`, turns started from the web UI are
announced too — in the project's topic when it has one, else in General. The notifier
polls the proxy's REST API (it never imports W1 internals), and only chats whose
`updated_at` moved are re-read.

Topic ↔ project mapping lives in `data/design/telegram.json`
(`{"topics": {"<thread_id>": {project_id, chat_id, user_id}}}`).
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import threading
from html import escape as _esc
from pathlib import Path
from typing import Any, Dict, Optional, Set

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError
from telegram.ext import ContextTypes

import config
from bot.live import flood_active, set_flood_backoff
from services.design.parallel import DesignAPI, DesignAPIError, TERMINAL

log = logging.getLogger("telecode.handlers.design")

_EXCERPT = 1200
_lock = threading.Lock()

# Turn ids started from Telegram: their completion is posted by the watcher, so the
# notifier must not announce them a second time.
_telegram_turns: Set[str] = set()


# ── topic map ────────────────────────────────────────────────────────────

def _map_path() -> Path:
    return Path(config._settings_dir()) / "data" / "design" / "telegram.json"


def _load_map() -> Dict[str, Any]:
    try:
        data = json.loads(_map_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"topics": {}}
    except (OSError, ValueError):
        return {"topics": {}}


def _save_map(data: Dict[str, Any]) -> None:
    p = _map_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def get_topic(thread_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if not thread_id:
        return None
    with _lock:
        return _load_map().get("topics", {}).get(str(thread_id))


def set_topic(thread_id: int, rec: Dict[str, Any]) -> None:
    with _lock:
        data = _load_map()
        data.setdefault("topics", {})[str(thread_id)] = rec
        _save_map(data)


def topic_for_project(pid: str) -> Optional[int]:
    with _lock:
        for tid, rec in _load_map().get("topics", {}).items():
            if rec.get("project_id") == pid:
                return int(tid)
    return None


def _ui_link(pid: str, cid: str = "") -> str:
    port = int(config.get_nested("proxy.port", 1235))
    return f"http://127.0.0.1:{port}/design?project={pid}" + (f"&chat={cid}" if cid else "")


# ── sending ──────────────────────────────────────────────────────────────

def _group_id() -> int:
    return int(config.get_nested("telegram.group_id", 0) or 0)


async def _send_text(bot, chat_id: int, thread_id: Optional[int], html_text: str) -> None:
    if flood_active(chat_id):
        await asyncio.sleep(2)
    kwargs: Dict[str, Any] = {"chat_id": chat_id, "text": html_text, "parse_mode": ParseMode.HTML,
                              "disable_web_page_preview": True}
    if thread_id:
        kwargs["message_thread_id"] = thread_id
    try:
        await bot.send_message(**kwargs)
    except RetryAfter as e:
        set_flood_backoff(chat_id, e.retry_after)
        await asyncio.sleep(e.retry_after + 1)
        await bot.send_message(**kwargs)


async def _send_photo(bot, chat_id: int, thread_id: Optional[int], jpeg: bytes, caption: str) -> None:
    kwargs: Dict[str, Any] = {"chat_id": chat_id, "photo": jpeg, "caption": caption,
                              "parse_mode": ParseMode.HTML}
    if thread_id:
        kwargs["message_thread_id"] = thread_id
    try:
        await bot.send_photo(**kwargs)
    except RetryAfter as e:
        set_flood_backoff(chat_id, e.retry_after)
        await asyncio.sleep(e.retry_after + 1)
        await bot.send_photo(**kwargs)


def _to_jpeg(data: bytes) -> Optional[bytes]:
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.thumbnail((1600, 1600))
        out = io.BytesIO()
        im.convert("RGB").save(out, "JPEG", quality=85)
        return out.getvalue()
    except Exception:
        return None


async def project_image(api: DesignAPI, pid: str, changed: Optional[list] = None) -> Optional[bytes]:
    """The project's thumbnail (W4 writes thumbnail.webp), else a screenshot of the first
    changed HTML file, as JPEG. None when neither is available."""
    try:
        jpeg = _to_jpeg(await api.get_file(pid, "thumbnail.webp"))
        if jpeg:
            return jpeg
    except Exception:
        pass
    html = [f for f in (changed or []) if f.endswith(".html")] or ["index.html"]
    try:
        shot = await api.request("POST", f"/api/design/projects/{pid}/screenshot",
                                 json_body={"file": html[0], "width": 1280, "height": 800,
                                            "format": "jpeg"}, raw=True, timeout=120)
        return _to_jpeg(shot)
    except Exception:
        return None


def format_result(title: str, turn: Dict[str, Any], pid: str, cid: str) -> str:
    status = turn.get("status")
    icon = {"done": "✅", "failed": "❌", "cancelled": "⏹", "error": "❌"}.get(status, "ℹ️")
    lines = [f"{icon} <b>{_esc(title or 'Design')}</b> — {_esc(str(status))}"]
    text = (turn.get("text") or "").strip()
    if text:
        if len(text) > _EXCERPT:
            text = text[:_EXCERPT].rstrip() + "…"
        lines.append(_esc(text))
    if turn.get("error"):
        lines.append(f"<i>{_esc(str(turn['error'])[:400])}</i>")
    files = turn.get("changed_files") or []
    if files:
        shown = ", ".join(_esc(f) for f in files[:8]) + (f" +{len(files) - 8}" if len(files) > 8 else "")
        lines.append(f"<b>Files:</b> {shown}")
    usage = turn.get("usage") or {}
    if usage.get("cost_usd"):
        lines.append(f"<i>${float(usage['cost_usd']):.3f}</i>")
    lines.append(f"<code>{_esc(_ui_link(pid, cid))}</code>")
    return "\n\n".join(lines)


async def post_result(bot, api: DesignAPI, chat_id: int, thread_id: Optional[int], title: str,
                      pid: str, cid: str, turn: Dict[str, Any]) -> None:
    await _send_text(bot, chat_id, thread_id, format_result(title, turn, pid, cid))
    if turn.get("status") == "done":
        img = await project_image(api, pid, turn.get("changed_files"))
        if img:
            await _send_photo(bot, chat_id, thread_id, img, f"🎨 {_esc(title or 'Design')}")


# ── /design ──────────────────────────────────────────────────────────────

def _allowed(user_id: int) -> bool:
    allowed = config.allowed_user_ids()
    return not allowed or user_id in allowed


_USAGE = ("<b>TeleDesign</b>\n"
          "/design &lt;prompt&gt; - design (or iterate on) the project for this topic\n"
          "/design comments - open comments\n"
          "/design stop - cancel the running turn\n"
          "/design status - project and last turn")


async def cmd_design(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not _allowed(update.effective_user.id):
        await msg.reply_text("Not authorised.")
        return
    args = list(ctx.args or [])
    thread_id = msg.message_thread_id if getattr(msg, "is_topic_message", True) else None
    if not args:
        await msg.reply_text(_USAGE, parse_mode=ParseMode.HTML)
        return
    api = DesignAPI()
    sub = args[0].lower()
    mapping = get_topic(thread_id)
    try:
        if sub in ("comments", "stop", "status") and len(args) == 1:
            if not mapping:
                await msg.reply_text("This topic has no design project yet. Start one with "
                                     "<code>/design &lt;prompt&gt;</code>.", parse_mode=ParseMode.HTML)
                return
            await {"comments": _sub_comments, "stop": _sub_stop, "status": _sub_status}[sub](
                msg, api, mapping)
            return
        await _sub_prompt(update, ctx, api, " ".join(args), thread_id, mapping)
    except DesignAPIError as e:
        hint = " — is the proxy running with TeleDesign?" if e.status in (404, 502, 503) else ""
        await msg.reply_text(f"TeleDesign error: {_esc(e.message)}{_esc(hint)}", parse_mode=ParseMode.HTML)
    except Exception as e:
        log.exception("/design failed")
        await msg.reply_text(f"TeleDesign error: {_esc(str(e) or type(e).__name__)}",
                             parse_mode=ParseMode.HTML)


async def _sub_comments(msg, api: DesignAPI, mapping: Dict[str, Any]) -> None:
    comments = [c for c in await api.list_comments(mapping["project_id"]) if c.get("status") == "open"]
    if not comments:
        await msg.reply_text("No open comments.")
        return
    lines = [f"<b>{len(comments)} open comment(s)</b>"]
    for c in comments[:20]:
        where = c.get("file") or c.get("board_id") or ""
        lines.append(f"• <b>{_esc(str(c.get('author') or 'You'))}</b> "
                     f"<i>{_esc(str(where))}</i>: {_esc(str(c.get('note') or ''))[:300]}")
    if len(comments) > 20:
        lines.append(f"…and {len(comments) - 20} more")
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def _sub_stop(msg, api: DesignAPI, mapping: Dict[str, Any]) -> None:
    await api.stop_chat(mapping["project_id"], mapping["chat_id"])
    await msg.reply_text("⏹ Stop requested.")


async def _sub_status(msg, api: DesignAPI, mapping: Dict[str, Any]) -> None:
    pid, cid = mapping["project_id"], mapping["chat_id"]
    proj = await api.get_project(pid)
    turns = await api.list_turns(pid, cid)
    last = next((t for t in reversed(turns) if t.get("role") == "assistant"), None)
    lines = [f"<b>{_esc(proj.get('title') or 'Design')}</b> ({_esc(proj.get('kind') or '')})",
             f"Turns: {len(turns)}"]
    if last:
        lines.append(f"Last: {_esc(str(last.get('status')))}")
    lines.append(f"<code>{_esc(_ui_link(pid, cid))}</code>")
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def _sub_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE, api: DesignAPI, prompt: str,
                      thread_id: Optional[int], mapping: Optional[Dict[str, Any]]) -> None:
    msg = update.effective_message
    chat_id = msg.chat_id
    if mapping:
        pid, cid = mapping["project_id"], mapping["chat_id"]
        title = mapping.get("title") or "Design"
    else:
        title = " ".join(prompt.split())[:60] or "Telegram design"
        proj = await api.create_project({"title": title})
        pid = proj["id"]
        chat = await api.create_chat(pid, {"title": "Telegram"})
        cid = chat["id"]
        if not thread_id:
            topic = await ctx.bot.create_forum_topic(chat_id=chat_id, name=f"🎨 {title}"[:128])
            thread_id = topic.message_thread_id
        set_topic(thread_id, {"project_id": pid, "chat_id": cid, "title": title,
                              "user_id": update.effective_user.id})
    try:
        turn = await api.post_turn(pid, cid, {"text": prompt}, busy_wait=0)
    except DesignAPIError as e:
        if e.status == 409:
            await _send_text(ctx.bot, chat_id, thread_id,
                             "⏳ A turn is already running here — wait for it, or <code>/design stop</code>.")
            return
        raise
    if turn.get("id"):
        _telegram_turns.add(turn["id"])
    await _send_text(ctx.bot, chat_id, thread_id,
                     f"🎨 Designing <b>{_esc(title)}</b>…\n<code>{_esc(_ui_link(pid, cid))}</code>")
    asyncio.ensure_future(_watch(ctx.bot, api, chat_id, thread_id, title, pid, cid, turn))


async def _watch(bot, api: DesignAPI, chat_id: int, thread_id: Optional[int], title: str,
                 pid: str, cid: str, turn: Dict[str, Any]) -> None:
    timeout = float(config.get_nested("design.agent_turn_timeout_sec", 3600))
    try:
        final = await api.wait_turn(pid, cid, turn, poll=2.0, timeout=timeout)
        if final.get("id"):
            _telegram_turns.add(final["id"])
        await post_result(bot, api, chat_id, thread_id, title, pid, cid, final)
    except asyncio.TimeoutError:
        await _send_text(bot, chat_id, thread_id, f"⌛ <b>{_esc(title)}</b> is still running — "
                                                  f"check the TeleDesign tab.")
    except (TelegramError, DesignAPIError) as e:
        log.warning("/design watcher %s/%s: %s", pid, cid, e)
    except Exception:
        log.exception("/design watcher failed")


# ── notify-on-done for web-UI turns ──────────────────────────────────────

class Notifier:
    """Polls projects → chats → turns and announces newly finished assistant turns."""

    def __init__(self, bot, api: Optional[DesignAPI] = None):
        self.bot = bot
        self.api = api or DesignAPI(timeout=15)
        self._chat_stamp: Dict[str, str] = {}      # chat_id → updated_at seen
        self._seen: Set[str] = set()               # terminal turn ids already handled
        self._primed: Set[str] = set()             # chats scanned once (history not announced)

    async def scan(self) -> int:
        """One pass. Returns how many notifications were sent."""
        sent = 0
        projects = await self.api.list_projects()
        for proj in projects[:50]:
            pid = proj["id"]
            try:
                chats = await self.api.list_chats(pid)
            except DesignAPIError:
                continue
            for chat in chats:
                cid = chat.get("id")
                stamp = str(chat.get("updated_at") or "")
                if not cid or (cid in self._primed and self._chat_stamp.get(cid) == stamp):
                    continue
                try:
                    turns = await self.api.list_turns(pid, cid)
                except DesignAPIError:
                    continue
                first = cid not in self._primed
                pending = False
                for t in turns:
                    if t.get("role") != "assistant":
                        continue
                    tid = t.get("id")
                    if t.get("status") not in TERMINAL:
                        pending = True
                        continue
                    if tid in self._seen:
                        continue
                    self._seen.add(tid)
                    if first or tid in _telegram_turns:
                        continue
                    await self._announce(proj, chat, t)
                    sent += 1
                self._primed.add(cid)
                # Keep re-reading a chat while a turn is still running in it.
                self._chat_stamp[cid] = "" if pending else stamp
        return sent

    async def _announce(self, proj: Dict[str, Any], chat: Dict[str, Any], turn: Dict[str, Any]) -> None:
        group = _group_id()
        if not group:
            return
        thread_id = topic_for_project(proj["id"])
        await post_result(self.bot, self.api, group, thread_id, proj.get("title") or "Design",
                          proj["id"], chat["id"], turn)

    async def run(self) -> None:
        while True:
            interval = float(config.get_nested("design.telegram_notify_interval_sec", 15) or 15)
            try:
                if config.get_nested("design.telegram_notify", False):
                    await self.scan()
            except asyncio.CancelledError:
                raise
            except DesignAPIError as e:
                log.debug("design notifier: %s", e)
            except Exception as e:
                log.debug("design notifier scan failed: %s", e)
            await asyncio.sleep(max(3.0, interval))


def start_design_notifier(app) -> asyncio.Task:
    """Start the notify-on-done loop (idle unless `design.telegram_notify` is true)."""
    task = asyncio.ensure_future(Notifier(app.bot).run())
    app.bot_data["_design_notifier"] = task
    return task
