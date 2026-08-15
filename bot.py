import os
import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN')
CHANNEL_USERNAME = os.getenv('CHANNEL_USERNAME', '@your_channel')
CHANNEL_ID = os.getenv('CHANNEL_ID', '@your_channel_id')

# Load streams
def load_streams():
    with open('streams.json', 'r') as f:
        return json.load(f)

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
    
    # Check membership
    if not await is_member(user_id, context):
        await show_join_prompt(update, context)
        return
    
    await show_main_menu(update, context)

async def show_join_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show join channel prompt"""
    keyboard = [
        [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}")],
        [InlineKeyboardButton("✅ I've Joined", callback_data="check_membership")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🔒 *Access Restricted*\n\n"
        f"To use this bot and watch streams, you must join our channel first.\n\n"
        f"1. Click 'Join Channel'\n"
        f"2. Join the channel\n"
        f"3. Come back and click 'I've Joined'",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show main menu with channel categories"""
    categories = list(set(s['category'] for s in STREAMS))
    
    keyboard = []
    for cat in categories:
        keyboard.append([InlineKeyboardButton(f"📺 {cat}", callback_data=f"cat_{cat}")])
    
    keyboard.append([InlineKeyboardButton("🔍 Search", callback_data="search")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎬 *Chill Box Streams*\n\n"
        "Select a category to browse channels:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_channels(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str):
    """Show channels in a category"""
    channels = [s for s in STREAMS if s['category'] == category]
    
    keyboard = []
    for ch in channels[:10]:
        keyboard.append([InlineKeyboardButton(f"📺 {ch['name']}", callback_data=f"play_{ch['id']}")])
    
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        f"*{category} Channels*\n\nSelect a channel to play:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def play_stream(update: Update, context: ContextTypes.DEFAULT_TYPE, stream_id: str):
    """Play stream inline"""
    stream = next((s for s in STREAMS if s['id'] == stream_id), None)
    
    if not stream:
        await update.callback_query.answer("Stream not found!")
        return
    
    # Send the stream as video
    await update.callback_query.message.reply_video(
        video=stream['url'],
        caption=f"🎬 *{stream['name']}*\n\n"
                f"📢 Join: {CHANNEL_USERNAME}",
        parse_mode='Markdown',
        supports_streaming=True,
        timeout=120
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "check_membership":
        user_id = query.from_user.id
        if await is_member(user_id, context):
            await query.edit_message_text(
                "✅ *Membership Verified!*\n\nWelcome to Chill Box!",
                parse_mode='Markdown'
            )
            await show_main_menu(update, context)
        else:
            await query.answer("❌ You haven't joined yet!", show_alert=True)
            await show_join_prompt(update, context)
    
    elif data == "back_to_menu":
        await show_main_menu(update, context)
    
    elif data == "search":
        await query.edit_message_text(
            "🔍 *Search*\n\nSend the channel name you want to search:",
            parse_mode='Markdown'
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
        query_text = update.message.text.lower()
        
        # Search streams
        results = [s for s in STREAMS if query_text in s['name'].lower()]
        
        if results:
            keyboard = []
            for s in results[:10]:
                keyboard.append([InlineKeyboardButton(f"📺 {s['name']}", callback_data=f"play_{s['id']}")])
            
            keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"🔍 *Search Results* for '{query_text}':",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ No channels found!")
        
        context.user_data['searching'] = False

def main():
    """Main function"""
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", show_main_menu))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search))
    
    # Start bot
    logger.info("Bot started!")
    application.run_polling()

if __name__ == '__main__':
    main()
