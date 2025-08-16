import os
import re
import html
import logging
from datetime import datetime, timedelta
from pymongo import MongoClient
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
)
from telegram.constants import ParseMode

# --- CONFIGURE THESE! ---
BOT_TOKEN = os.environ.get('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
MONGODB_URI = os.environ.get('MONGODB_URI', 'mongodb+srv://worep38024:eQkzkfjayr6cVtkI@cluster0.mtradfw.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0')
DB_NAME = os.environ.get('MONGO_DB_NAME', 'filterbot')
BOT_OWNER_ID = 6797820880  # <--- PUT YOUR TELEGRAM USER ID HERE!
BOT_DISPLAY_NAME = "FilterBot"
NEWS_CHANNEL_URL = "https://t.me/Zoro_bots"
UPI_ID = "8888888888@upi"
PAID_FILTER_PRICE = 49  # INR
PAID_FILTER_DURATION = 1  # days (set to your desired duration)

client = MongoClient(MONGODB_URI)
db = client[DB_NAME]
filters_col = db['filters']
paid_requests_col = db['paid_requests']

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Helpers ---

def is_admin(user_id: int, chat_admins) -> bool:
    return any(admin.user.id == user_id for admin in chat_admins)

def get_group_filters(chat_id: int):
    return list(filters_col.find({"chat_id": chat_id}))

def get_filter(chat_id: int, keyword: str):
    return filters_col.find_one({"chat_id": chat_id, "keyword": keyword})

def is_paid_filter(flt):
    return flt.get("paid_by") is not None

def filter_expiry_string(flt):
    if is_paid_filter(flt) and flt.get("expires_at"):
        return f" [Paid, expires: {flt['expires_at'].strftime('%Y-%m-%d %H:%M')}]"
    return " [Paid]" if is_paid_filter(flt) else ""

def add_filter(chat_id: int, keyword: str, reply: dict, regex=False, silent=False, paid_by=None, expires_at=None):
    filters_col.update_one(
        {"chat_id": chat_id, "keyword": keyword},
        {"$set": {
            "chat_id": chat_id,
            "keyword": keyword,
            "reply": reply,
            "regex": regex,
            "silent": silent,
            "count": 0,
            "paid_by": paid_by,  # Telegram user_id if paid filter else None
            "created_at": datetime.now(),
            "expires_at": expires_at  # for paid filters
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

# --- Start/Help ---

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        f"Hey there! My name is <b>{BOT_DISPLAY_NAME}</b> - I'm here to help you manage your groups!\n"
        "Use /help to see all commands.\n\n"
        f"Join my <a href=\"{NEWS_CHANNEL_URL}\">news channel</a> for updates.\n\n"
        f"Non-admins: To add your own filter, pay <b>₹{PAID_FILTER_PRICE}</b> via UPI: <code>{UPI_ID}</code> and follow the instructions."
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
        "<b>/addfilter &lt;keyword&gt; &lt;reply&gt;</b> - Admins add filters for free. Members can buy a filter.\n"
        "<b>/removefilter &lt;keyword&gt;</b> - Remove filter (admins or owner for paid)\n"
        "<b>/editfilter &lt;keyword&gt;</b> - Edit filter (admins or owner for paid)\n"
        "<b>/listfilters</b> - List all filters\n"
        "<b>/filterstats &lt;keyword&gt;</b> - Show trigger count\n"
        "<b>/approvefilter &lt;keyword&gt;</b> - (owner only, reply to payment screenshot)\n"
        "<b>/setregex &lt;keyword&gt; on/off</b> - Regex (admins or owner for paid)\n"
        "<b>/setsilent &lt;keyword&gt; on/off</b> - Silent mode (admins or owner for paid)\n"
        "<b>/enablefilters</b> - Enable filtering in group\n"
        "<b>/disablefilters</b> - Disable filtering in group\n"
        "<b>/help</b> - Show this help\n\n"
        f"<i>Paid filters expire in {PAID_FILTER_DURATION} day(s).</i>"
    )
    await update.message.reply_html(help_text, disable_web_page_preview=True)

# --- Filter commands ---

async def addfilter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or update.effective_chat.type == "private":
        return
    user_id = update.effective_user.id
    admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text("Usage: /addfilter <keyword> <reply>")
        return
    keyword = args[0].lower()
    reply_text = " ".join(args[1:]).strip()
    pure_text, buttons = parse_buttons(reply_text)
    reply = {"type": "text", "content": pure_text, "buttons": buttons}

    if is_admin(user_id, admins):
        add_filter(update.effective_chat.id, keyword, reply)
        await update.message.reply_text(f"Filter <b>{html.escape(keyword)}</b> added!", parse_mode=ParseMode.HTML)
        return

    # Paid filter request for non-admins
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
        "The bot owner will approve your filter!"
    )
    await update.message.reply_html(price_text)

async def handle_payment_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    req = paid_requests_col.find_one({"user_id": user_id, "chat_id": chat_id, "status": "pending"})
    if not req:
        return
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

    expires_at = datetime.now() + timedelta(days=PAID_FILTER_DURATION)
    add_filter(chat_id, keyword, req["reply"], paid_by=user_id, expires_at=expires_at)
    paid_requests_col.update_one({"_id": req["_id"]}, {"$set": {"status": "approved", "approved_at": datetime.now(), "expires_at": expires_at}})
    await update.message.reply_text(
        f"Paid filter <b>{html.escape(keyword)}</b> approved! It will expire in {PAID_FILTER_DURATION} day(s).",
        parse_mode=ParseMode.HTML
    )
    try:
        await context.bot.send_message(
            user_id,
            f"Your paid filter '<b>{html.escape(keyword)}</b>' is now active in group <b>{update.effective_chat.title}</b> for {PAID_FILTER_DURATION} day(s)!",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

async def removefilter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or update.effective_chat.type == "private":
        return
    user_id = update.effective_user.id
    admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /removefilter <keyword>")
        return
    keyword = args[0].lower()
    flt = get_filter(update.effective_chat.id, keyword)
    if not flt:
        await update.message.reply_text("No such filter.")
        return
    if is_paid_filter(flt):
        if user_id != BOT_OWNER_ID:
            await update.message.reply_text("Only the bot owner can remove paid filters.")
            return
    else:
        if not is_admin(user_id, admins):
            await update.message.reply_text("Only admins can remove filters.")
            return
    result = remove_filter(update.effective_chat.id, keyword)
    if result.deleted_count:
        await update.message.reply_text(f"Filter <b>{html.escape(keyword)}</b> removed.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("No such filter.")

async def editfilter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or update.effective_chat.type == "private":
        return
    user_id = update.effective_user.id
    admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /editfilter <keyword> (reply to new content)")
        return
    keyword = args[0].lower()
    flt = get_filter(update.effective_chat.id, keyword)
    if not flt:
        await update.message.reply_text("No such filter.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to the new content for the filter.")
        return
    reply_msg = update.message.reply_to_message
    reply = None
    if reply_msg.text:
        pure_text, buttons = parse_buttons(reply_msg.text)
        reply = {"type": "text", "content": pure_text, "buttons": buttons}
    elif reply_msg.photo:
        reply = {"type": "photo", "file_id": reply_msg.photo[-1].file_id, "caption": reply_msg.caption or "", "buttons":[]}
    elif reply_msg.video:
        reply = {"type": "video", "file_id": reply_msg.video.file_id, "caption": reply_msg.caption or "", "buttons":[]}
    elif reply_msg.sticker:
        reply = {"type": "sticker", "file_id": reply_msg.sticker.file_id, "buttons":[]}
    elif reply_msg.document:
        reply = {"type": "document", "file_id": reply_msg.document.file_id, "caption": reply_msg.caption or "", "buttons":[]}
    else:
        await update.message.reply_text("Unsupported media for filter.")
        return
    if is_paid_filter(flt):
        if user_id != BOT_OWNER_ID:
            await update.message.reply_text("Only the bot owner can edit paid filters.")
            return
    else:
        if not is_admin(user_id, admins):
            await update.message.reply_text("Only admins can edit filters.")
            return
    add_filter(update.effective_chat.id, keyword, reply)
    await update.message.reply_text(f"Filter <b>{html.escape(keyword)}</b> edited.", parse_mode=ParseMode.HTML)

async def listfilters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    filters_list = get_group_filters(chat_id)
    if not filters_list:
        await update.message.reply_text("No filters set in this group.")
        return
    text = "<b>Filters in this group:</b>\n" + "\n".join(
        f"• <code>{html.escape(f['keyword'])}</code>{filter_expiry_string(f)}"
        for f in filters_list
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

# --- Regex and Silent features ---

async def setregex_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    args = context.args
    if len(args) != 2 or args[1] not in ['on', 'off']:
        await update.message.reply_text("Usage: /setregex <keyword> on/off")
        return
    keyword, state = args[0].lower(), args[1]
    flt = get_filter(update.effective_chat.id, keyword)
    if not flt:
        await update.message.reply_text("No such filter.")
        return
    if is_paid_filter(flt):
        if user_id != BOT_OWNER_ID:
            await update.message.reply_text("Only the bot owner can set regex for paid filters.")
            return
    else:
        if not is_admin(user_id, admins):
            await update.message.reply_text("Only admins can set regex.")
            return
    filters_col.update_one(
        {"chat_id": update.effective_chat.id, "keyword": keyword},
        {"$set": {"regex": state == 'on'}}
    )
    await update.message.reply_text(f"Regex {'enabled' if state == 'on' else 'disabled'} for <b>{html.escape(keyword)}</b>.", parse_mode=ParseMode.HTML)

async def setsilent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    args = context.args
    if len(args) != 2 or args[1] not in ['on', 'off']:
        await update.message.reply_text("Usage: /setsilent <keyword> on/off")
        return
    keyword, state = args[0].lower(), args[1]
    flt = get_filter(update.effective_chat.id, keyword)
    if not flt:
        await update.message.reply_text("No such filter.")
        return
    if is_paid_filter(flt):
        if user_id != BOT_OWNER_ID:
            await update.message.reply_text("Only the bot owner can set silent mode for paid filters.")
            return
    else:
        if not is_admin(user_id, admins):
            await update.message.reply_text("Only admins can set silent mode.")
            return
    filters_col.update_one(
        {"chat_id": update.effective_chat.id, "keyword": keyword},
        {"$set": {"silent": state == 'on'}}
    )
    await update.message.reply_text(f"Silent mode {'enabled' if state == 'on' else 'disabled'} for <b>{html.escape(keyword)}</b>.", parse_mode=ParseMode.HTML)

# --- Message Handler for Filters ---

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
        regex = f.get('regex', False)
        silent = f.get('silent', False)
        found = False
        if regex:
            try:
                if re.search(keyword, text, re.IGNORECASE):
                    found = True
            except Exception:
                continue
        else:
            if keyword.lower() in text.lower():
                found = True
        if found:
            increment_filter_count(chat_id, keyword)
            if silent:
                try:
                    await update.message.delete()
                except Exception:
                    pass
            try:
                markup = build_markup(reply.get('buttons', []))
                if reply["type"] == "text":
                    await update.message.reply_text(reply["content"], reply_markup=markup, disable_web_page_preview=True)
            except Exception as e:
                logger.error(f"Error sending filter reply: {e}")
            break

# --- Background job to remove expired paid filters ---
async def remove_expired_paid_filters(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    expired = list(filters_col.find({"expires_at": {"$lte": now}}))
    for flt in expired:
        filters_col.delete_one({"_id": flt["_id"]})
        try:
            await context.bot.send_message(
                flt["chat_id"],
                f"Paid filter <b>{html.escape(flt['keyword'])}</b> expired and was removed.",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

# --- MAIN APP ---

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("addfilter", addfilter_cmd))
    app.add_handler(CommandHandler("approvefilter", approvefilter_cmd))
    app.add_handler(CommandHandler("removefilter", removefilter_cmd))
    app.add_handler(CommandHandler("editfilter", editfilter_cmd))
    app.add_handler(CommandHandler("listfilters", listfilters_cmd))
    app.add_handler(CommandHandler("filterstats", filterstats_cmd))
    app.add_handler(CommandHandler("setregex", setregex_cmd))
    app.add_handler(CommandHandler("setsilent", setsilent_cmd))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_payment_screenshot))
    app.add_handler(MessageHandler(filters.ALL, message_filter_handler))
    app.job_queue.run_repeating(remove_expired_paid_filters, interval=3600, first=10)
    print(f"{BOT_DISPLAY_NAME} started.")
    app.run_polling()
if __name__ == "__main__":
    main()
