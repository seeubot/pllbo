import os
import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
CHANNEL_ID = os.getenv('CHANNEL_ID', '-1004458683062')
CHANNEL_USERNAME = os.getenv('CHANNEL_USERNAME', '@livetvappiptv')
CHANNEL_URL = os.getenv('CHANNEL_URL', 'https://t.me/livetvappiptv')

# WebSocket configuration
PORT = int(os.getenv('PORT', '8000'))
HOST = os.getenv('HOST', '0.0.0.0')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')

# Timeout configuration
CONNECT_TIMEOUT = int(os.getenv('CONNECT_TIMEOUT', '30'))
READ_TIMEOUT = int(os.getenv('READ_TIMEOUT', '30'))
WRITE_TIMEOUT = int(os.getenv('WRITE_TIMEOUT', '30'))

# Proxy configuration (if needed)
PROXY_URL = os.getenv('PROXY_URL', '')

# Load streams
def load_streams():
    try:
        with open('streams.json', 'r') as f:
            return json.load(f)
    except:
        return []

STREAMS = load_streams()

async def is_member(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is a member of the channel"""
    try:
        member = await context.bot.get_chat_member(
            chat_id=CHANNEL_ID,
            user_id=user_id
        )
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Error checking membership: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    user = update.effective_user
    user_id = user.id
    
    if not await is_member(user_id, context):
        await show_join_prompt(update, context)
        return
    
    await show_main_menu(update, context)

async def show_join_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show join channel prompt"""
    keyboard = [
        [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_URL)],
        [InlineKeyboardButton("✅ I've Joined", callback_data="check_membership")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🔒 *Access Restricted*\n\n"
        f"To use this bot and watch live streams, you must join our channel first.\n\n"
        f"📢 Channel: {CHANNEL_USERNAME}\n\n"
        f"1️⃣ Click 'Join Channel'\n"
        f"2️⃣ Join the channel\n"
        f"3️⃣ Click 'I've Joined'",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show main menu"""
    categories = sorted(list(set(s['category'] for s in STREAMS if s.get('category'))))
    
    keyboard = []
    for cat in categories:
        count = len([s for s in STREAMS if s.get('category') == cat])
        keyboard.append([InlineKeyboardButton(f"📺 {cat} ({count})", callback_data=f"cat_{cat}")])
    
    keyboard.append([InlineKeyboardButton("🔍 Search", callback_data="search")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            f"🎬 *Chill Box Live TV*\n\nSelect a category:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            f"🎬 *Chill Box Live TV*\n\nSelect a category:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )

async def show_channels(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str):
    """Show channels in category"""
    channels = [s for s in STREAMS if s.get('category') == category]
    
    keyboard = []
    for ch in channels:
        keyboard.append([InlineKeyboardButton(f"📺 {ch['name']}", callback_data=f"play_{ch['id']}")])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        f"*{category}* ({len(channels)} channels)\n\nSelect a channel:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def play_stream(update: Update, context: ContextTypes.DEFAULT_TYPE, stream_id: str):
    """Play stream"""
    query = update.callback_query
    stream = next((s for s in STREAMS if s['id'] == stream_id), None)
    
    if not stream:
        await query.answer("Stream not found!", show_alert=True)
        return
    
    await query.answer(f"Loading {stream['name']}...")
    
    try:
        if stream.get('url'):
            await query.message.reply_video(
                video=stream['url'],
                caption=f"🎬 *{stream['name']}*\n\n📢 {CHANNEL_USERNAME}",
                parse_mode=ParseMode.MARKDOWN,
                supports_streaming=True,
                timeout=120
            )
        else:
            await query.message.reply_text("Stream URL not available!")
    except Exception as e:
        logger.error(f"Error playing stream: {e}")
        await query.message.reply_text("❌ Error playing stream. Try again later.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callbacks"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data == "check_membership":
        if await is_member(user_id, context):
            await query.edit_message_text("✅ Verified! Welcome!")
            await show_main_menu(update, context)
        else:
            await query.answer("Not joined yet!", show_alert=True)
    
    elif data == "back_to_menu":
        await show_main_menu(update, context)
    
    elif data == "search":
        await query.edit_message_text(
            "🔍 Send channel name to search:",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['searching'] = True
    
    elif data.startswith("cat_"):
        category = data.replace("cat_", "")
        await show_channels(update, context, category)
    
    elif data.startswith("play_"):
        stream_id = data.replace("play_", "")
        await play_stream(update, context, stream_id)

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle search"""
    if context.user_data.get('searching'):
        query_text = update.message.text.lower().strip()
        results = [s for s in STREAMS if query_text in s.get('name', '').lower()]
        
        if results:
            keyboard = []
            for s in results[:15]:
                keyboard.append([InlineKeyboardButton(f"📺 {s['name']}", callback_data=f"play_{s['id']}")])
            
            keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"Found {len(results)} channels:",
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text("No channels found!")
        
        context.user_data['searching'] = False

def main():
    """Main function"""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is required!")
        return
    
    # Create request with timeout
    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=CONNECT_TIMEOUT,
        read_timeout=READ_TIMEOUT,
        write_timeout=WRITE_TIMEOUT,
    )
    
    # Create application
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .connect_timeout(CONNECT_TIMEOUT)
        .read_timeout(READ_TIMEOUT)
        .write_timeout(WRITE_TIMEOUT)
        .build()
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", show_main_menu))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search))
    
    logger.info("Bot starting...")
    logger.info(f"Channel: {CHANNEL_USERNAME}")
    logger.info(f"Streams: {len(STREAMS)}")
    
    # Run in polling mode (simpler for Koyeb)
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )

if __name__ == '__main__':
    main()
