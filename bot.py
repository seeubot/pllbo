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
from urllib.parse import quote

from aiohttp import web
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)
from telegram.request import HTTPXRequest

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

# Public HTTPS URL this service is reachable at (e.g. your Koyeb app URL,
# https://yelling-merci-seeutech-f6258452.koyeb.app). Required to build
# in-app player links for the "play_" buttons. Without it, play buttons fall
# back to sending the raw stream URL as text.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

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

if not PUBLIC_BASE_URL:
    logger.warning(
        "PUBLIC_BASE_URL is not set — 'play' buttons will fall back to sending "
        "the raw stream URL instead of an in-app HLS player link."
    )


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


def build_indices(streams: list) -> tuple[dict, dict, list]:
    """Precompute lookup structures once, instead of scanning/sorting STREAMS
    on every button click (this is the main per-click latency win)."""
    by_id = {s["id"]: s for s in streams}
    by_category: dict[str, list] = {}
    for s in streams:
        by_category.setdefault(s.get("category", "General"), []).append(s)
    categories = sorted(by_category.keys())
    return by_id, by_category, categories


STREAMS = load_streams(STREAMS_FILE)
STREAMS_BY_ID, CHANNELS_BY_CATEGORY, CATEGORIES = build_indices(STREAMS)

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
    buttons = [InlineKeyboardButton(f"📺 {c}", callback_data=f"cat_{c}_0") for c in CATEGORIES]
    rows = chunk(buttons, CATEGORY_COLUMNS)
    if not rows:
        rows = [[InlineKeyboardButton("No streams available yet", callback_data="noop")]]
    return InlineKeyboardMarkup(rows)


def build_channel_keyboard(category: str, page: int) -> tuple[InlineKeyboardMarkup, int]:
    channels = CHANNELS_BY_CATEGORY.get(category, [])
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
        stream = STREAMS_BY_ID.get(stream_id)

        if not stream:
            await query.answer("Stream not found — it may have been removed.", show_alert=True)
            return

        if PUBLIC_BASE_URL:
            player_url = f"{PUBLIC_BASE_URL}/public/index.html?id={quote(stream_id)}"
            buttons = [[InlineKeyboardButton("▶️ Watch now", web_app=WebAppInfo(url=player_url))]]
            await query.message.reply_text(
                f"🎬 *{stream['name']}*\n\nTap below to watch.",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="Markdown",
            )
            return

        # No PUBLIC_BASE_URL configured — fall back to sharing the raw link.
        # Note: sendVideo does NOT work for .m3u8 playlists (Telegram needs an
        # actual video file, not a stream manifest), so we send it as text.
        await query.message.reply_text(
            f"🎬 {stream['name']}\n\n{stream['url']}\n\n"
            "Open this link in a player that supports HLS (e.g. VLC).",
            disable_web_page_preview=True,
        )


async def reload_streams_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: reload streams.json without restarting the bot."""
    global STREAMS, STREAMS_BY_ID, CHANNELS_BY_CATEGORY, CATEGORIES
    admin_ids = {i.strip() for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip()}
    if str(update.effective_user.id) not in admin_ids:
        return
    STREAMS = load_streams(STREAMS_FILE)
    STREAMS_BY_ID, CHANNELS_BY_CATEGORY, CATEGORIES = build_indices(STREAMS)
    await update.message.reply_text(f"✅ Reloaded {len(STREAMS)} stream(s).")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception while processing update: %s", context.error, exc_info=context.error)


# --------------------------------------------------------------------------
# Web server: health check + in-app HLS player (required for Koyeb web
# services; the player route is what makes m3u8 streams watchable, since
# Telegram's sendVideo can't handle stream playlists directly)
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Web server: health check + static player (required for Koyeb web services;
# the player is what makes m3u8 streams watchable, since Telegram's
# sendVideo can't handle stream playlists directly).
#
# The player itself lives in public/index.html as a plain static file (Plyr
# UI + hls.js for playback) and is served as-is — no per-request templating.
# It fetches stream details from /api/stream?id=<id>, keeping the server
# side to a small, fast JSON lookup.
# --------------------------------------------------------------------------

PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "streams_loaded": len(STREAMS)})


async def api_stream(request: web.Request) -> web.Response:
    stream_id = request.query.get("id", "")
    stream = STREAMS_BY_ID.get(stream_id)
    logger.info("PLAYER_TRACE /api/stream requested id=%r found=%s", stream_id, stream is not None)

    if not stream:
        return web.json_response({"error": "not_found"}, status=404)

    return web.json_response({"id": stream["id"], "name": stream["name"], "url": stream["url"]})


async def run_health_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    app.router.add_get("/api/stream", api_stream)
    if os.path.isdir(PUBLIC_DIR):
        app.router.add_static("/public/", path=PUBLIC_DIR, name="public", show_index=False)
    else:
        logger.warning("public/ directory not found at %s — player page will not be served.", PUBLIC_DIR)
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
    # concurrent_updates lets independent button clicks/commands be handled in
    # parallel instead of one-at-a-time, which is the main source of perceived
    # lag when multiple users (or rapid taps) hit the bot at once. The larger
    # connection pool avoids requests queuing up behind a small default pool.
    request = HTTPXRequest(connection_pool_size=16, pool_timeout=10.0)
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .concurrent_updates(64)
        .build()
    )

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
