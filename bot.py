import os
import re
import html
import logging
import asyncio
from datetime import datetime, timedelta, time as dt_time
from pymongo import MongoClient
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
)
from telegram.constants import ParseMode

# === CONFIGURATION ===
BOT_TOKEN = os.environ.get('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
MONGODB_URI = os.environ.get('MONGODB_URI', 'mongodb+srv://worep38024:eQkzkfjayr6cVtkI@cluster0.mtradfw.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0')
DB_NAME = os.environ.get('MONGO_DB_NAME', 'filterbot')

BOT_OWNER_ID = 6797820880  # <--- PUT YOUR TELEGRAM USER ID HERE!
BOT_DISPLAY_NAME = "FilterBot"
NEWS_CHANNEL_URL = "https://t.me/Zoro_bots"
UPI_ID = "8888888888@upi"
PAID_FILTER_PRICE = 49  # INR
PAID_FILTER_DURATION = 1  # days, after which filter expires

client = MongoClient(MONGODB_URI)
db = client[DB_NAME]
filters_col = db['filters']
paid_requests_col = db['paid_requests']

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Helper Functions ===

def get_group_filters(chat_id: int):
    return list(filters_col.find({"chat_id": chat_id}))

def get_filter(chat_id: int, keyword: str):
    return filters_col.find_one({"chat_id": chat_id, "keyword": keyword})

def add_filter(chat_id: int, keyword: str, reply: dict, paid_by=None, expires_at=None):
    filters_col.update_one(
        {"chat_id": chat_id, "keyword": keyword},
        {"$set": {
            "chat_id": chat_id,
            "keyword": keyword,
            "reply": reply,
            "count": 0,
            "paid_by": paid_by,
            "expires_at": expires_at
        }},
        upsert=True
    )

def remove_filter(chat_id: int, keyword: str):
    return filters_col.delete_one({"chat_id": chat_id, "keyword": keyword})

def increment_filter_count(chat_id: int, keyword: str):
    filters_col.update_one(
        {"chat_id": chat_id, "keyword": keyword},
        {"$inc": {"count": 1}}
    )

def parse_buttons(reply_text):
    button_pattern = r'\[([^\[\]]+)\]\((https?://[^\(\)]+)\)'
    buttons = re.findall(button_pattern, reply_text)
    pure_text = re.sub(button_pattern, '', reply_text).strip()
    return pure_text, [{'text': t, 'url': u} for t, u in buttons] if buttons else (pure_text, [])

def build_markup(buttons):
    if not buttons:
        return None
    keyboard, row = [], []
    for btn in buttons:
        row.append(InlineKeyboardButton(btn['text'], url=btn['url']))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

async def remove_expired_paid_filters(app):
    """Background task: remove paid filters that have expired."""
    while True:
        now = datetime.now()
        expired = list(filters_col.find({"expires_at": {"$lte": now}}))
        for flt in expired:
            filters_col.delete_one({"_id": flt["_id"]})
            try:
                await app.bot.send_message(
                    flt["chat_id"],
                    f"Paid filter <b>{html.escape(flt['keyword'])}</b> expired and was removed.",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
        await asyncio.sleep(3600)

# === COMMAND HANDLERS ===

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        f"Hey there! My name is <b>{BOT_DISPLAY_NAME}</b> - I'm here to help you manage your groups!\n"
        "Use /help to find out how to use me to my full potential.\n\n"
        f"Join my <a href=\"{NEWS_CHANNEL_URL}\">news channel</a> to get information on all the latest updates.\n\n"
        f"To add your own filter, pay <b>₹{PAID_FILTER_PRICE}</b> via UPI: <code>{UPI_ID}</code> and follow instructions."
    )
    keyboard = [
        [
            InlineKeyboardButton("Add me to your chat!", url=f"https://t.me/{context.bot.username}?startgroup=new"),
            InlineKeyboardButton("📢 Join our Channel", url=NEWS_CHANNEL_URL),
        ]
    ]
    await update.message.reply_html(
        text, 
        reply_markup=InlineKeyboardMarkup(keyboard), 
        disable_web_page_preview=True
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        f"<b>{BOT_DISPLAY_NAME} Commands:</b>\n"
        "<b>/addfilter &lt;keyword&gt; &lt;reply&gt;</b> - Request a paid filter (for 1 day)\n"
        "<b>/approvefilter &lt;keyword&gt;</b> - (Owner only, reply to payment screenshot)\n"
        "<b>/listfilters</b> - List all active filters\n"
        "<b>/filterstats &lt;keyword&gt;</b> - Show trigger count\n"
        "<b>/help</b> - Show this help\n\n"
        f"<i>Only the bot owner can approve filters. Paid filters are auto-removed after 1 day</i>."
    )
    await update.message.reply_html(help_text, disable_web_page_preview=True)

async def addfilter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or update.effective_chat.type == "private":
        return
    user_id = update.effective_user.id
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text("Usage: /addfilter <keyword> <reply>")
        return
    keyword = args[0].lower()
    reply_text = " ".join(args[1:]).strip()
    pure_text, buttons = parse_buttons(reply_text)
    reply = {"type": "text", "content": pure_text, "buttons": buttons}

    # Store the request in DB, wait for screenshot, then owner approval
    paid_requests_col.update_one(
        {"chat_id": update.effective_chat.id, "user_id": user_id, "keyword": keyword},
        {"$set": {
            "chat_id": update.effective_chat.id,
            "user_id": user_id,
            "username": update.effective_user.username,
            "keyword": keyword,
            "reply": reply,
            "status": "pending",
            "requested_at": datetime.now()
        }},
        upsert=True
    )
    price_text = (
        f"To add your own filter, please pay ₹{PAID_FILTER_PRICE} via UPI to <code>{UPI_ID}</code> "
        "and send your payment screenshot here (in group or private chat). "
        "The bot owner will approve your filter and it will be active for 1 day from approval!"
    )
    await update.message.reply_html(price_text)

async def handle_payment_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only act if it's a media message (photo/document) and the sender has a pending paid filter
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    req = paid_requests_col.find_one({"user_id": user_id, "chat_id": chat_id, "status": "pending"})
    if not req:
        return  # No pending filter request from this user in this group
    # Attach the screenshot file info to their request for owner review
    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    if file_id:
        paid_requests_col.update_one(
            {"_id": req["_id"]},
            {"$set": {"payment_screenshot": file_id, "screenshot_msg_id": update.message.message_id}}
        )
        await update.message.reply_text(
            "Payment screenshot received. Please wait for the bot owner to approve your filter."
        )

async def approvefilter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only bot owner can approve paid filters
    if update.effective_user.id != BOT_OWNER_ID:
        await update.message.reply_text("Only the bot owner can approve paid filters.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to the user's payment screenshot with /approvefilter <keyword>")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /approvefilter <keyword>")
        return
    keyword = args[0].lower()
    replied_msg = update.message.reply_to_message
    user_id = replied_msg.from_user.id
    chat_id = update.effective_chat.id
    req = paid_requests_col.find_one({
        "chat_id": chat_id,
        "user_id": user_id,
        "keyword": keyword,
        "status": "pending"
    })
    if not req:
        await update.message.reply_text("No matching pending paid filter request found for this user and keyword.")
        return
    # Approve: add filter, set expiration, notify user
    expires_at = datetime.combine((datetime.now() + timedelta(days=PAID_FILTER_DURATION)).date(), dt_time.min)
    add_filter(chat_id, keyword, req["reply"], paid_by=user_id, expires_at=expires_at)
    paid_requests_col.update_one({"_id": req["_id"]}, {"$set": {"status": "approved", "approved_at": datetime.now(), "expires_at": expires_at}})
    await update.message.reply_text(
        f"Paid filter <b>{html.escape(keyword)}</b> approved! It will be active for 1 day and auto-remove at midnight.",
        parse_mode=ParseMode.HTML
    )
    try:
        # Notify the user in private
        await context.bot.send_message(
            user_id,
            f"Your paid filter '<b>{html.escape(keyword)}</b>' is now active in group <b>{update.effective_chat.title}</b> for 1 day!",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

async def listfilters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    filters_list = get_group_filters(chat_id)
    if not filters_list:
        await update.message.reply_text("No filters set in this group.")
        return

    text = "<b>Filters in this group:</b>\n" + "\n".join(
        f"• <code>{html.escape(f['keyword'])}</code>" for f in filters_list
    )
    await update.message.reply_html(text)

async def filterstats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /filterstats <keyword>")
        return
    keyword = args[0].lower()
    flt = get_filter(update.effective_chat.id, keyword)
    if not flt:
        await update.message.reply_text("No such filter.")
        return
    await update.message.reply_text(f"Filter <b>{html.escape(keyword)}</b> triggered <b>{flt.get('count',0)}</b> times.", parse_mode=ParseMode.HTML)

# === FILTER TRIGGER HANDLER ===

async def message_filter_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or update.effective_chat.type == "private":
        return
    chat_id = update.effective_chat.id
    filters_list = get_group_filters(chat_id)
    text = update.message.text or update.message.caption
    if not text:
        return
    for f in filters_list:
        keyword = f['keyword']
        reply = f['reply']
        found = False
        if keyword.lower() in text.lower():
            found = True
        if found:
            increment_filter_count(chat_id, keyword)
            try:
                markup = build_markup(reply.get('buttons', []))
                if reply["type"] == "text":
                    await update.message.reply_text(reply["content"], reply_markup=markup, disable_web_page_preview=True)
            except Exception as e:
                logger.error(f"Error sending filter reply: {e}")
            break

# === MAIN APP ===

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("addfilter", addfilter_cmd))
    app.add_handler(CommandHandler("approvefilter", approvefilter_cmd))
    app.add_handler(CommandHandler("listfilters", listfilters_cmd))
    app.add_handler(CommandHandler("filterstats", filterstats_cmd))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_payment_screenshot))
    app.add_handler(MessageHandler(filters.ALL, message_filter_handler))

    # Start background task for filter expiry
    app.job_queue.run_repeating(lambda ctx: asyncio.create_task(remove_expired_paid_filters(app)), interval=3600, first=0)

    print(f"{BOT_DISPLAY_NAME} started.")
    app.run_polling()

if __name__ == "__main__":
    main()
