"""
Chill Box Telegram Bot
Gated IPTV/stream bot that requires channel membership before use.

Designed to run on Koyeb:
- Binds an HTTP health check server on $PORT (Koyeb web services require this;
  if you deploy as a Koyeb "Worker" service instead, the health server is
  simply harmless and can be left running).
- Handles SIGTERM/SIGINT gracefully so Koyeb rolling deploys don't kill the
  bot mid-request.
- Fails fast with a clear message if required config is missing, instead of
  silently doing nothing.
"""

import asyncio
import json
import logging
import os
import signal
import time
from typing import Optional

from aiohttp import web
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("chillbox")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@livetvappiptv")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/livetvappiptv")
STREAMS_FILE = os.getenv("STREAMS_FILE", "streams.json")
PORT = int(os.getenv("PORT", "8000"))

# How many channel buttons to show per page in a category.
PAGE_SIZE = 8
# How many category buttons per row.
CATEGORY_COLUMNS = 2
# How long (seconds) a membership check result is cached before re-checking.
MEMBERSHIP_CACHE_TTL = 300

REQUIRED_MEMBER_STATUSES = {"member", "administrator", "creator"}


def _fail(msg: str) -> "None":
    logger.error(msg)
    raise SystemExit(1)


if not BOT_TOKEN:
    _fail("BOT_TOKEN environment variable is required but not set.")

if not CHANNEL_ID:
    _fail("CHANNEL_ID environment variable is required but not set.")


# --------------------------------------------------------------------------
# Stream data
# --------------------------------------------------------------------------

def load_streams(path: str) -> list:
    """Load and validate the stream catalog. Bad entries are skipped, not fatal."""
    if not os.path.exists(path):
        logger.warning("Streams file '%s' not found; starting with an empty catalog.", path)
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load '%s': %s. Starting with an empty catalog.", path, e)
        return []

    if not isinstance(raw, list):
        logger.error("'%s' must contain a JSON array. Starting with an empty catalog.", path)
        return []

    valid = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            logger.warning("Skipping stream #%d: not an object.", i)
            continue
        if not entry.get("id") or not entry.get("name") or not entry.get("url"):
            logger.warning("Skipping stream #%d: missing required 'id', 'name', or 'url'.", i)
            continue
        entry.setdefault("category", "General")
        valid.append(entry)

    logger.info("Loaded %d valid stream(s) from '%s'.", len(valid), path)
    return valid


STREAMS = load_streams(STREAMS_FILE)

# Simple in-memory membership cache: user_id -> (is_member: bool, checked_at: float)
_membership_cache: dict[int, tuple[bool, float]] = {}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

async def is_member(user_id: int, context: ContextTypes.DEFAULT_TYPE, force: bool = False) -> bool:
    """Check whether a user belongs to the gate channel.

    Only positive results are cached. A "not a member" result is never cached,
    so a user who just joined and immediately taps "I've Joined" always gets a
    fresh check instead of a stale negative from before they joined.
    `force=True` skips the cache entirely (used by the "I've Joined" button).
    """
    if not force:
        cached = _membership_cache.get(user_id)
        if cached and (time.time() - cached[1]) < MEMBERSHIP_CACHE_TTL:
            return cached[0]

    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        result = member.status in REQUIRED_MEMBER_STATUSES
    except TelegramError as e:
        logger.warning(
            "Membership check failed for user %s against CHANNEL_ID=%r: %s",
            user_id, CHANNEL_ID, e,
        )
        result = False

    if result:
        _membership_cache[user_id] = (result, time.time())
    else:
        _membership_cache.pop(user_id, None)

    return result


def chunk(items: list, size: int) -> list:
    return [items[i : i + size] for i in range(0, len(items), size)]


def build_category_keyboard() -> InlineKeyboardMarkup:
    categories = sorted({s.get("category", "General") for s in STREAMS})
    buttons = [InlineKeyboardButton(f"📺 {c}", callback_data=f"cat_{c}_0") for c in categories]
    rows = chunk(buttons, CATEGORY_COLUMNS)
    if not rows:
        rows = [[InlineKeyboardButton("No streams available yet", callback_data="noop")]]
    return InlineKeyboardMarkup(rows)


def build_channel_keyboard(category: str, page: int) -> tuple[InlineKeyboardMarkup, int]:
    channels = [s for s in STREAMS if s.get("category", "General") == category]
    pages = chunk(channels, PAGE_SIZE)
    total_pages = max(len(pages), 1)
    page = max(0, min(page, total_pages - 1))
    current = pages[page] if pages else []

    rows = [[InlineKeyboardButton(f"📺 {ch['name']}", callback_data=f"play_{ch['id']}")] for ch in current]

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"cat_{category}_{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"cat_{category}_{page + 1}"))
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton("⬅️ Back to categories", callback_data="menu")])
    return InlineKeyboardMarkup(rows), total_pages


def join_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_URL)],
            [InlineKeyboardButton("✅ I've Joined", callback_data="check")],
        ]
    )


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info("BUTTON_TRACE /start received from user_id=%s username=%s", user_id, update.effective_user.username)

    if not await is_member(user_id, context):
        await update.message.reply_text(
            f"🔒 Join {CHANNEL_USERNAME} to use this bot!",
            reply_markup=join_prompt_keyboard(),
        )
        return

    await show_menu(update, context)


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🎬 *Chill Box*\n\nSelect category:"
    keyboard = build_category_keyboard()

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user_id = query.from_user.id
    logger.info(
        "BUTTON_TRACE callback received: data=%r user_id=%s username=%s",
        data, user_id, query.from_user.username,
    )

    # Every gated action re-checks membership except the join-check button itself,
    # so someone who left the channel loses access again.
    if data != "check" and not await is_member(user_id, context):
        await query.answer("🔒 You must join the channel first!", show_alert=True)
        await query.edit_message_text(
            f"🔒 Join {CHANNEL_USERNAME} to use this bot!",
            reply_markup=join_prompt_keyboard(),
        )
        return

    if data == "noop":
        return

    if data == "check":
        result = await is_member(user_id, context, force=True)
        logger.info(
            "BUTTON_TRACE 'I've Joined' clicked by user_id=%s | CHANNEL_ID=%r | is_member=%s",
            user_id, CHANNEL_ID, result,
        )
        if result:
            await query.edit_message_text("✅ Verified!")
            await show_menu(update, context)
        else:
            await query.answer("❌ Not joined yet!", show_alert=True)

    elif data.startswith("cat_"):
        # format: cat_<category>_<page>
        rest = data[len("cat_"):]
        category, _, page_str = rest.rpartition("_")
        try:
            page = int(page_str)
        except ValueError:
            category, page = rest, 0

        keyboard, total_pages = build_channel_keyboard(category, page)
        title = f"*{category}*" + (f" (page {page + 1}/{total_pages})" if total_pages > 1 else "")
        await query.edit_message_text(f"{title}\n\nSelect channel:", reply_markup=keyboard, parse_mode="Markdown")

    elif data == "menu":
        await show_menu(update, context)

    elif data.startswith("play_"):
        stream_id = data[len("play_"):]
        stream = next((s for s in STREAMS if s["id"] == stream_id), None)

        if not stream:
            await query.answer("Stream not found — it may have been removed.", show_alert=True)
            return

        try:
            await query.message.reply_video(
                video=stream["url"],
                caption=f"🎬 {stream['name']}\n\n📢 {CHANNEL_USERNAME}",
                supports_streaming=True,
            )
        except TelegramError as e:
            logger.error("Failed to send stream '%s': %s", stream_id, e)
            await query.answer("Couldn't send that stream right now. Please try again.", show_alert=True)


async def reload_streams_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: reload streams.json without restarting the bot."""
    global STREAMS
    admin_ids = {i.strip() for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip()}
    if str(update.effective_user.id) not in admin_ids:
        return
    STREAMS = load_streams(STREAMS_FILE)
    await update.message.reply_text(f"✅ Reloaded {len(STREAMS)} stream(s).")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception while processing update: %s", context.error, exc_info=context.error)


# --------------------------------------------------------------------------
# Health check server (required for Koyeb web services; harmless otherwise)
# --------------------------------------------------------------------------

async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "streams_loaded": len(STREAMS)})


async def run_health_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Health check server listening on 0.0.0.0:%d", PORT)
    return runner


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------

async def run():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reload", reload_streams_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_error_handler(on_error)

    health_runner = await run_health_server()

    stop_event = asyncio.Event()

    def _request_stop(*_args):
        logger.info("Shutdown signal received, stopping...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Signal handlers aren't available on some platforms (e.g. Windows).
            pass

    async with application:
        await application.initialize()
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        logger.info("Bot started and polling for updates.")

        await stop_event.wait()

        await application.updater.stop()
        await application.stop()

    await health_runner.cleanup()
    logger.info("Shutdown complete.")


def main():
    try:
        asyncio.run(run())
    except SystemExit:
        raise
    except Exception:
        logger.exception("Fatal error, exiting.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
