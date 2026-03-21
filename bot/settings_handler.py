"""
/settings command handler — view and edit settings.json from Telegram.

Commands:
  /settings                    — show full settings summary
  /settings reload             — hot-reload settings.json from disk
  /settings get <dotpath>      — get a specific value
  /settings set <dotpath> <v>  — set a value and save immediately

  /settings tool <key>                    — show one tool's config
  /settings tool <key> flag add <flag>    — add a flag
  /settings tool <key> flag remove <flag> — remove a flag
  /settings tool <key> env <VAR> <value>  — set an env var
  /settings tool <key> env <VAR> --delete — remove an env var
  /settings tool <key> cmd <cmd...>       — set startup command

  /settings voice stt on|off|url <url>|model <m>

  /settings validate           — check for missing/placeholder values
"""
from __future__ import annotations
import json
from html import escape as _esc

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import config
from backends.params import render_all_tools


async def handle_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    user_id = update.effective_user.id

    if not args:
        await _show_summary(update)
        return

    cmd = args[0].lower()

    if cmd == "reload":
        config.reload()
        await update.message.reply_text("✅ settings.json reloaded from disk.")
        return

    if cmd == "validate":
        warnings = config.validate()
        if warnings:
            await update.message.reply_text(
                "⚠️ <b>Validation warnings:</b>\n" + "\n".join(_esc(w) for w in warnings),
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text("✅ All settings look good.")
        return

    if cmd == "get" and len(args) >= 2:
        val = config.get_nested(args[1])
        if val is None:
            await update.message.reply_text(
                f"❌ Path <code>{_esc(args[1])}</code> not found.", parse_mode=ParseMode.HTML,
            )
        else:
            display = json.dumps(val, indent=2) if isinstance(val, (dict, list)) else str(val)
            await update.message.reply_text(
                f"<code>{_esc(args[1])}</code>:\n<pre>{_esc(display)}</pre>",
                parse_mode=ParseMode.HTML,
            )
        return

    if cmd == "set" and len(args) >= 3:
        dotpath = args[1]
        raw_val = " ".join(args[2:])
        try:
            value = json.loads(raw_val)
        except json.JSONDecodeError:
            value = raw_val
        config.set_nested(dotpath, value)
        await update.message.reply_text(
            f"✅ <code>{_esc(dotpath)}</code> = <code>{_esc(str(value))}</code> — saved",
            parse_mode=ParseMode.HTML,
        )
        return

    if cmd == "tool":
        await _handle_tool(update, args[1:])
        return

    if cmd == "voice":
        await _handle_voice(update, args[1:])
        return

    await update.message.reply_text(
        "<b>Settings commands:</b>\n"
        "<code>/settings</code> — summary\n"
        "<code>/settings get &lt;path&gt;</code> — read value\n"
        "<code>/settings set &lt;path&gt; &lt;value&gt;</code> — write value\n"
        "<code>/settings tool &lt;key&gt;</code> — tool config\n"
        "<code>/settings voice stt</code> — voice config\n"
        "<code>/settings validate</code> — check issues\n"
        "<code>/settings reload</code> — hot-reload from disk",
        parse_mode=ParseMode.HTML,
    )


async def _show_summary(update: Update) -> None:
    lines = ["<b>⚙️ Telecode Settings</b>\n"]

    # Telegram
    lines.append("<b>Telegram</b>")
    lines.append(f"  Group ID: <code>{config.telegram_group_id()}</code>")
    allowed = config.allowed_user_ids()
    if allowed:
        lines.append(f"  Allowed users: <code>{', '.join(map(str, allowed))}</code>")
    else:
        lines.append("  Allowed users: <i>ALL (⚠️ open!)</i>")

    # Voice
    lines.append("\n<b>Voice</b>")
    stt_status = "🟢 enabled" if config.stt_enabled() else "⚫ disabled"
    lines.append(f"  STT: {stt_status}  ·  <code>{_esc(config.stt_base_url())}</code>")

    # Streaming
    lines.append("\n<b>Streaming</b>")
    lines.append(f"  Interval: <code>{config.stream_interval()}s</code>")
    lines.append(f"  Max msg length: <code>{config.max_msg_length()}</code>")
    lines.append(f"  Idle timeout: <code>{config.idle_timeout()}s</code>")

    lines.append("\n<i>Use <code>/settings tool &lt;key&gt;</code> to see tool configs.</i>")
    lines.append("<i>Use <code>/settings validate</code> to check for issues.</i>")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def _handle_tool(update: Update, args: list[str]) -> None:
    if not args:
        await update.message.reply_text(render_all_tools(), parse_mode=ParseMode.HTML)
        return

    key = args[0].lower()
    if key not in config.all_tool_keys():
        keys = ", ".join(f"<code>{_esc(k)}</code>" for k in config.all_tool_keys())
        await update.message.reply_text(
            f"❌ Unknown tool <code>{_esc(key)}</code>.\nAvailable: {keys}",
            parse_mode=ParseMode.HTML,
        )
        return

    if len(args) == 1:
        cmd_str = " ".join(config.tool_startup_cmd(key))
        flags_str = " ".join(config.tool_flags(key)) or "(none)"
        text = (
            f"🔧 <b>Tool: {_esc(key)}</b>\n\n"
            f"<b>Startup cmd:</b> <code>{_esc(cmd_str)}</code>\n"
            f"<b>Flags:</b> <code>{_esc(flags_str)}</code>\n"
            f"<b>Env vars:</b>"
        )
        env = config.tool_env(key)
        if env:
            for k, v in env.items():
                masked = (v[:4] + "…") if len(v) > 8 else "***"
                text += f"\n  <code>{_esc(k)}</code> = <code>{_esc(masked)}</code>"
        else:
            text += " (none)"
        sess = config.tool_session_args(key)
        if sess:
            text += "\n<b>Session args:</b>"
            for k, v in sess.items():
                text += f"\n  <code>{_esc(k)}</code> = <code>{_esc(v or '(unset)')}</code>"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    sub = args[1].lower()

    if sub == "cmd" and len(args) >= 3:
        new_cmd = args[2:]
        config.set_nested(f"tools.{key}.startup_cmd", new_cmd)
        await update.message.reply_text(
            f"✅ <code>tools.{_esc(key)}.startup_cmd</code> = <code>{_esc(str(new_cmd))}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if sub == "flag" and len(args) >= 4:
        action = args[2].lower()
        flag   = args[3]
        flags  = list(config.tool_flags(key))
        if action == "add":
            if flag not in flags:
                flags.append(flag)
            config.set_nested(f"tools.{key}.flags", flags)
            flags_str = " ".join(flags)
            await update.message.reply_text(
                f"✅ Added flag <code>{_esc(flag)}</code> to <code>{_esc(key)}</code>.\n"
                f"Flags: <code>{_esc(flags_str)}</code>",
                parse_mode=ParseMode.HTML,
            )
        elif action == "remove":
            flags = [f for f in flags if f != flag]
            config.set_nested(f"tools.{key}.flags", flags)
            flags_str = " ".join(flags) or "(none)"
            await update.message.reply_text(
                f"✅ Removed flag <code>{_esc(flag)}</code> from <code>{_esc(key)}</code>.\n"
                f"Flags: <code>{_esc(flags_str)}</code>",
                parse_mode=ParseMode.HTML,
            )
        return

    if sub == "env" and len(args) >= 4:
        var = args[2]
        val = args[3]
        if val == "--delete":
            env = dict(config.tool_cfg(key).get("env", {}))
            env.pop(var, None)
            config.set_nested(f"tools.{key}.env", env)
            await update.message.reply_text(
                f"✅ Removed <code>{_esc(var)}</code> from <code>{_esc(key)}</code> env.",
                parse_mode=ParseMode.HTML,
            )
        else:
            config.set_nested(f"tools.{key}.env.{var}", val)
            masked = (val[:4] + "…") if len(val) > 8 else "***"
            await update.message.reply_text(
                f"✅ <code>{_esc(key)}.env.{_esc(var)}</code> = <code>{_esc(masked)}</code>",
                parse_mode=ParseMode.HTML,
            )
        return

    await update.message.reply_text(
        f"<b>Tool sub-commands:</b>\n"
        f"<code>/settings tool {_esc(key)} cmd &lt;cmd...&gt;</code>\n"
        f"<code>/settings tool {_esc(key)} flag add|remove &lt;flag&gt;</code>\n"
        f"<code>/settings tool {_esc(key)} env &lt;VAR&gt; &lt;value&gt;</code>\n"
        f"<code>/settings tool {_esc(key)} env &lt;VAR&gt; --delete</code>",
        parse_mode=ParseMode.HTML,
    )


async def _handle_voice(update: Update, args: list[str]) -> None:
    if not args:
        await update.message.reply_text(
            "<b>Voice commands:</b>\n"
            "<code>/settings voice stt on|off|url &lt;url&gt;|model &lt;m&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    side = args[0].lower()
    if side != "stt":
        await update.message.reply_text(
            "Use <code>stt</code>.", parse_mode=ParseMode.HTML,
        )
        return

    if len(args) < 2:
        val = config.get_nested("voice.stt")
        await update.message.reply_text(
            f"<b>STT config:</b>\n<pre>{_esc(json.dumps(val, indent=2))}</pre>",
            parse_mode=ParseMode.HTML,
        )
        return

    sub = args[1].lower()

    if sub in ("on", "off"):
        config.set_nested("voice.stt.enabled", sub == "on")
        await update.message.reply_text(
            f"✅ <code>voice.stt.enabled</code> = <code>{sub == 'on'}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if sub == "url" and len(args) >= 3:
        config.set_nested("voice.stt.base_url", args[2])
        await update.message.reply_text(
            f"✅ <code>voice.stt.base_url</code> = <code>{_esc(args[2])}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if sub == "model" and len(args) >= 3:
        config.set_nested("voice.stt.model", args[2])
        await update.message.reply_text(
            f"✅ <code>voice.stt.model</code> = <code>{_esc(args[2])}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    await update.message.reply_text(
        "Unknown sub-command. Try <code>/settings voice stt on|off|url &lt;url&gt;</code>",
        parse_mode=ParseMode.HTML,
    )
