import os
import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN', '')
CHANNEL_ID = os.getenv('CHANNEL_ID', '-1004458683062')
CHANNEL_USERNAME = os.getenv('CHANNEL_USERNAME', '@livetvappiptv')
CHANNEL_URL = os.getenv('CHANNEL_URL', 'https://t.me/livetvappiptv')

def load_streams():
    try:
        with open('streams.json', 'r') as f:
            return json.load(f)
    except:
        return []

STREAMS = load_streams()

async def is_member(user_id, context):
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not await is_member(user_id, context):
        keyboard = [
            [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_URL)],
            [InlineKeyboardButton("✅ I've Joined", callback_data="check")]
        ]
        await update.message.reply_text(
            f"🔒 Join {CHANNEL_USERNAME} to use this bot!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    await show_menu(update, context)

async def show_menu(update, context):
    categories = sorted(list(set(s.get('category', 'General') for s in STREAMS)))
    
    keyboard = []
    for cat in categories:
        keyboard.append([InlineKeyboardButton(f"📺 {cat}", callback_data=f"cat_{cat}")])
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            "🎬 *Chill Box*\n\nSelect category:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "🎬 *Chill Box*\n\nSelect category:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "check":
        if await is_member(query.from_user.id, context):
            await query.edit_message_text("✅ Verified!")
            await show_menu(update, context)
        else:
            await query.answer("❌ Not joined yet!", show_alert=True)
    
    elif data.startswith("cat_"):
        cat = data.replace("cat_", "")
        channels = [s for s in STREAMS if s.get('category', 'General') == cat]
        
        keyboard = []
        for ch in channels:
            keyboard.append([InlineKeyboardButton(f"📺 {ch['name']}", callback_data=f"play_{ch['id']}")])
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="menu")])
        
        await query.edit_message_text(
            f"*{cat}*\n\nSelect channel:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == "menu":
        await show_menu(update, context)
    
    elif data.startswith("play_"):
        stream_id = data.replace("play_", "")
        stream = next((s for s in STREAMS if s['id'] == stream_id), None)
        
        if stream:
            await query.message.reply_video(
                video=stream['url'],
                caption=f"🎬 {stream['name']}\n\n📢 {CHANNEL_USERNAME}",
                supports_streaming=True
            )

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN required!")
        return
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("Bot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
