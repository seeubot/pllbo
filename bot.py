import os
import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration from environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
CHANNEL_ID = os.getenv('CHANNEL_ID', '-1004458683062')
CHANNEL_USERNAME = os.getenv('CHANNEL_USERNAME', '@livetvappiptv')
CHANNEL_URL = os.getenv('CHANNEL_URL', 'https://t.me/livetvappiptv')

# WebSocket configuration
PORT = int(os.getenv('PORT', '8000'))
HOST = os.getenv('HOST', '0.0.0.0')
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')  # Set this on Koyeb

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
        logger.error(f"Error checking membership for {user_id}: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name
    
    logger.info(f"User {username} ({user_id}) started the bot")
    
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
        f"Steps:\n"
        f"1️⃣ Click 'Join Channel' button below\n"
        f"2️⃣ Join the channel\n"
        f"3️⃣ Come back and click 'I've Joined'",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show main menu with categories"""
    categories = sorted(list(set(s['category'] for s in STREAMS if s.get('category'))))
    
    keyboard = []
    for cat in categories:
        count = len([s for s in STREAMS if s.get('category') == cat])
        keyboard.append([InlineKeyboardButton(f"📺 {cat} ({count})", callback_data=f"cat_{cat}")])
    
    keyboard.append([InlineKeyboardButton("🔍 Search Channel", callback_data="search")])
    keyboard.append([InlineKeyboardButton("📢 Join Channel", url=CHANNEL_URL)])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            f"🎬 *Chill Box Live TV*\n\n"
            f"Welcome! Select a category to browse channels:\n\n"
            f"📢 Join: {CHANNEL_USERNAME}",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            f"🎬 *Chill Box Live TV*\n\n"
            f"Welcome! Select a category to browse channels:\n\n"
            f"📢 Join: {CHANNEL_USERNAME}",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )

async def show_channels(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str):
    """Show channels in a category"""
    channels = [s for s in STREAMS if s.get('category') == category]
    
    keyboard = []
    for ch in channels:
        keyboard.append([InlineKeyboardButton(f"📺 {ch['name']}", callback_data=f"play_{ch['id']}")])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        f"*{category} Channels* ({len(channels)})\n\n"
        f"Select a channel to play:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def play_stream(update: Update, context: ContextTypes.DEFAULT_TYPE, stream_id: str):
    """Play stream as video"""
    query = update.callback_query
    stream = next((s for s in STREAMS if s['id'] == stream_id), None)
    
    if not stream:
        await query.answer("❌ Stream not found!", show_alert=True)
        return
    
    await query.answer(f"🎬 Loading {stream['name']}...")
    
    try:
        if stream.get('url'):
            await query.message.reply_video(
                video=stream['url'],
                caption=f"🎬 *{stream['name']}*\n"
                        f"📂 Category: {stream.get('category', 'General')}\n\n"
                        f"📢 Join: {CHANNEL_USERNAME}\n"
                        f"🔗 {CHANNEL_URL}",
                parse_mode=ParseMode.MARKDOWN,
                supports_streaming=True,
                timeout=120
            )
        else:
            await query.message.reply_text("❌ Stream URL not available!")
    except Exception as e:
        logger.error(f"Error playing stream: {e}")
        await query.message.reply_text(
            f"❌ Error playing stream. Please try again later.\n\n"
            f"📢 Join {CHANNEL_USERNAME} for updates."
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    if data == "check_membership":
        if await is_member(user_id, context):
            await query.edit_message_text(
                "✅ *Membership Verified!*\n\n"
                "Welcome to Chill Box Live TV! 🎬",
                parse_mode=ParseMode.MARKDOWN
            )
            await show_main_menu(update, context)
        else:
            await query.answer("❌ You haven't joined yet!", show_alert=True)
    
    elif data == "back_to_menu":
        await show_main_menu(update, context)
    
    elif data == "search":
        await query.edit_message_text(
            "🔍 *Search Channel*\n\n"
            "Send the channel name you want to search.\n"
            "Example: `zee` or `sun` or `sports`",
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
    """Handle search messages"""
    if context.user_data.get('searching'):
        query_text = update.message.text.lower().strip()
        
        results = [s for s in STREAMS if query_text in s.get('name', '').lower()]
        
        if results:
            keyboard = []
            for s in results[:15]:
                keyboard.append([InlineKeyboardButton(f"📺 {s['name']}", callback_data=f"play_{s['id']}")])
            
            keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"🔍 *Search Results* for '{query_text}':\n"
                f"Found {len(results)} channels",
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(
                f"❌ No channels found for '{query_text}'",
                parse_mode=ParseMode.MARKDOWN
            )
        
        context.user_data['searching'] = False

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    await update.message.reply_text(
        "🎬 *Chill Box Live TV Bot*\n\n"
        "Commands:\n"
        "/start - Start bot\n"
        "/menu - Show channel menu\n"
        "/help - Show this help\n\n"
        f"📢 Must join: {CHANNEL_USERNAME}\n"
        f"🔗 {CHANNEL_URL}",
        parse_mode=ParseMode.MARKDOWN
    )

def main():
    """Main function with WebSocket support"""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable is required!")
        return
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", show_main_menu))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search))
    
    logger.info(f"Bot started!")
    logger.info(f"Channel: {CHANNEL_USERNAME}")
    logger.info(f"Channel ID: {CHANNEL_ID}")
    logger.info(f"Total streams: {len(STREAMS)}")
    
    # Start with WebSocket
    if WEBHOOK_URL:
        # Webhook mode (for production)
        logger.info(f"Starting webhook on {WEBHOOK_URL}")
        application.run_webhook(
            listen=HOST,
            port=PORT,
            webhook_url=WEBHOOK_URL,
            allowed_updates=Update.ALL_TYPES
        )
    else:
        # Polling mode (for local testing)
        logger.info("Starting polling mode")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
