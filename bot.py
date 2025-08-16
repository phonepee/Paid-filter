import os
import re
import html
import logging
from pymongo import MongoClient
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
)
from telegram.constants import ParseMode

# === CONFIGURATION ===
BOT_TOKEN = os.environ.get('BOT_TOKEN', 'Y')
MONGODB_URI = os.environ.get('MONGODB_URI', 'mongodb+srv://worep38024:eQkzkfjayr6cVtkI@cluster0.mtradfw.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0')
DB_NAME = os.environ.get('MONGO_DB_NAME', 'filterbot')

BOT_DISPLAY_NAME = "FilterBot"  # Change to your bot's display name
NEWS_CHANNEL_URL = "https://t.me/Zoro_bots"  # Change to your Telegram channel
PRIVACY_URL = "https://t.me/yourbot?start=privacy"  # Change if you have a privacy page

client = MongoClient(MONGODB_URI)
db = client[DB_NAME]
filters_col = db['filters']
settings_col = db['settings']

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def is_admin(user_id: int, chat_admins) -> bool:
    return any(admin.user.id == user_id for admin in chat_admins)

def get_group_filters(chat_id: int):
    return list(filters_col.find({"chat_id": chat_id}))

def get_filter(chat_id: int, keyword: str):
    return filters_col.find_one({"chat_id": chat_id, "keyword": keyword})

def add_filter(chat_id: int, keyword: str, reply: dict, regex=False, silent=False):
    # Always save as dict
    filters_col.update_one(
        {"chat_id": chat_id, "keyword": keyword},
        {"$set": {
            "chat_id": chat_id,
            "keyword": keyword,
            "reply": reply,
            "regex": regex,
            "silent": silent,
            "count": 0
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

def set_group_setting(chat_id: int, key: str, value):
    settings_col.update_one(
        {"chat_id": chat_id},
        {"$set": {key: value, "chat_id": chat_id}},
        upsert=True
    )

def get_group_setting(chat_id: int, key: str, default=None):
    g = settings_col.find_one({"chat_id": chat_id})
    if g:
        return g.get(key, default)
    return default

def parse_buttons(reply_text):
    """
    Extracts inline buttons from reply text using [text](url) format.
    Returns (pure_reply_text, [{'text':..., 'url':...},...])
    """
    button_pattern = r'\[([^\[\]]+)\]\((https?://[^\(\)]+)\)'
    buttons = re.findall(button_pattern, reply_text)
    pure_text = re.sub(button_pattern, '', reply_text).strip()
    return pure_text, [{'text': t, 'url': u} for t, u in buttons]

def build_markup(buttons):
    if not buttons:
        return None
    # 2 buttons per row
    keyboard, row = [], []
    for btn in buttons:
        row.append(InlineKeyboardButton(btn['text'], url=btn['url']))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

# === COMMAND HANDLERS ===

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        f"Hey there! My name is <b>{BOT_DISPLAY_NAME}</b> - I'm here to help you manage your groups! "
        "Use /help to find out how to use me to my full potential.\n\n"
        f"Join my <a href=\"{NEWS_CHANNEL_URL}\">news channel</a> to get information on all the latest updates.\n\n"
        f"Check <a href=\"{PRIVACY_URL}\">/privacy</a> to view the privacy policy, and interact with your data."
    )
    keyboard = [
        [
            InlineKeyboardButton("Add me to your chat!", url=f"https://t.me/{context.bot.username}?startgroup=new"),
            InlineKeyboardButton(f"⭐ Get your own {BOT_DISPLAY_NAME}", url=f"https://t.me/{context.bot.username}"),
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
        "<b>/addfilter &lt;keyword&gt;</b> - Add filter with reply (as reply to message or text after keyword, [Button](url) supported)\n"
        "<b>/removefilter &lt;keyword&gt;</b> - Remove filter\n"
        "<b>/editfilter &lt;keyword&gt;</b> - Edit filter (reply to new content)\n"
        "<b>/listfilters</b> - List all filters\n"
        "<b>/filterstats &lt;keyword&gt;</b> - Show trigger count\n"
        "<b>/setregex &lt;keyword&gt; on/off</b> - Regex mode for filter\n"
        "<b>/setsilent &lt;keyword&gt; on/off</b> - Silent mode (delete trigger)\n"
        "<b>/enablefilters</b> - Enable filtering in group\n"
        "<b>/disablefilters</b> - Disable filtering in group\n"
        "<b>/help</b> - Show this help"
    )
    await update.message.reply_html(help_text, disable_web_page_preview=True)

async def addfilter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or update.effective_chat.type == "private":
        return
    user_id = update.effective_user.id
    admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    if not is_admin(user_id, admins):
        await update.message.reply_text("Only admins can add filters.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /addfilter <keyword> [reply as text or media]")
        return

    keyword = args[0].lower()
    reply = None

    # If replying to a message, use that as filter reply
    if update.message.reply_to_message:
        reply_msg = update.message.reply_to_message
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
            await update.message.reply_text("Unsupported media type for filter reply.")
            return
    else:
        # Use message text after keyword as reply
        reply_text = " ".join(args[1:]).strip()
        if not reply_text:
            await update.message.reply_text("Please provide a reply (as text or reply to a message).")
            return
        pure_text, buttons = parse_buttons(reply_text)
        reply = {"type": "text", "content": pure_text, "buttons": buttons}

    add_filter(update.effective_chat.id, keyword, reply)
    await update.message.reply_text(f"Filter <b>{html.escape(keyword)}</b> added!", parse_mode=ParseMode.HTML)

async def removefilter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or update.effective_chat.type == "private":
        return
    user_id = update.effective_user.id
    admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    if not is_admin(user_id, admins):
        await update.message.reply_text("Only admins can remove filters.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /removefilter <keyword>")
        return
    keyword = args[0].lower()
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
    if not is_admin(user_id, admins):
        await update.message.reply_text("Only admins can edit filters.")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /editfilter <keyword> (reply to new content)")
        return
    keyword = args[0].lower()
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
    add_filter(update.effective_chat.id, keyword, reply)
    await update.message.reply_text(f"Filter <b>{html.escape(keyword)}</b> edited.", parse_mode=ParseMode.HTML)

async def listfilters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    filters_list = get_group_filters(chat_id)
    if not filters_list:
        await update.message.reply_text("No filters set in this group.")
        return

    # Pagination support (10 filters per page)
    page = int(context.args[0]) if context.args and context.args[0].isdigit() else 1
    per_page = 10
    start = (page - 1) * per_page
    end = start + per_page
    total_pages = (len(filters_list) - 1) // per_page + 1

    text = "<b>Filters in this group:</b>\n" + "\n".join(
        f"• <code>{html.escape(f['keyword'])}</code>" for f in filters_list[start:end]
    )
    text += f"\n\nPage {page}/{total_pages}"
    if total_pages > 1:
        buttons = []
        if page > 1:
            buttons.append(InlineKeyboardButton("Prev", callback_data=f"filters_page:{page-1}"))
        if page < total_pages:
            buttons.append(InlineKeyboardButton("Next", callback_data=f"filters_page:{page+1}"))
        await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup([buttons]))
    else:
        await update.message.reply_html(text)

async def filters_pagination_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    chat_id = query.message.chat.id
    filters_list = get_group_filters(chat_id)
    per_page = 10
    start = (page - 1) * per_page
    end = start + per_page
    total_pages = (len(filters_list) - 1) // per_page + 1
    text = "<b>Filters in this group:</b>\n" + "\n".join(
        f"• <code>{html.escape(f['keyword'])}</code>" for f in filters_list[start:end]
    )
    text += f"\n\nPage {page}/{total_pages}"
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("Prev", callback_data=f"filters_page:{page-1}"))
    if page < total_pages:
        buttons.append(InlineKeyboardButton("Next", callback_data=f"filters_page:{page+1}"))
    await query.edit_message_text(
        text=text, reply_markup=InlineKeyboardMarkup([buttons]), parse_mode=ParseMode.HTML
    )

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

async def setregex_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    if not is_admin(user_id, admins):
        await update.message.reply_text("Only admins can set regex.")
        return
    args = context.args
    if len(args) != 2 or args[1] not in ['on', 'off']:
        await update.message.reply_text("Usage: /setregex <keyword> on/off")
        return
    keyword, state = args[0].lower(), args[1]
    flt = get_filter(update.effective_chat.id, keyword)
    if not flt:
        await update.message.reply_text("No such filter.")
        return
    filters_col.update_one(
        {"chat_id": update.effective_chat.id, "keyword": keyword},
        {"$set": {"regex": state == 'on'}}
    )
    await update.message.reply_text(f"Regex {'enabled' if state == 'on' else 'disabled'} for <b>{html.escape(keyword)}</b>.", parse_mode=ParseMode.HTML)

async def setsilent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    if not is_admin(user_id, admins):
        await update.message.reply_text("Only admins can set silent mode.")
        return
    args = context.args
    if len(args) != 2 or args[1] not in ['on', 'off']:
        await update.message.reply_text("Usage: /setsilent <keyword> on/off")
        return
    keyword, state = args[0].lower(), args[1]
    flt = get_filter(update.effective_chat.id, keyword)
    if not flt:
        await update.message.reply_text("No such filter.")
        return
    filters_col.update_one(
        {"chat_id": update.effective_chat.id, "keyword": keyword},
        {"$set": {"silent": state == 'on'}}
    )
    await update.message.reply_text(f"Silent mode {'enabled' if state == 'on' else 'disabled'} for <b>{html.escape(keyword)}</b>.", parse_mode=ParseMode.HTML)

async def enablefilters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    if not is_admin(user_id, admins):
        await update.message.reply_text("Only admins can enable filters.")
        return
    set_group_setting(update.effective_chat.id, 'enabled', True)
    await update.message.reply_text("Filters enabled in this group.")

async def disablefilters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    if not is_admin(user_id, admins):
        await update.message.reply_text("Only admins can disable filters.")
        return
    set_group_setting(update.effective_chat.id, 'enabled', False)
    await update.message.reply_text("Filters disabled in this group.")

# === FILTER TRIGGER HANDLER ===

async def message_filter_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat or update.effective_chat.type == "private":
        return

    chat_id = update.effective_chat.id
    if not get_group_setting(chat_id, 'enabled', True):
        return

    filters_list = get_group_filters(chat_id)
    text = update.message.text or update.message.caption
    if not text:
        return

    for f in filters_list:
        keyword = f['keyword']
        reply = f['reply']
        # ---- Fix: check reply is dict ----
        if not isinstance(reply, dict):
            logger.error(f"Filter reply is not a dict: {reply}")
            continue  # skip this filter

        regex = f.get('regex', False)
        silent = f.get('silent', False)
        found = False

        # Regex trigger
        if regex:
            try:
                if re.search(keyword, text, re.IGNORECASE):
                    found = True
            except Exception:
                continue
        else:
            # Whole word/phrase/case-insensitive like Rose
            if re.search(rf'\b{re.escape(keyword)}\b', text, re.IGNORECASE):
                found = True

        if found:
            increment_filter_count(chat_id, keyword)
            # Delete user message if silent
            if silent:
                try:
                    await update.message.delete()
                except Exception:
                    pass
            # Reply with filter content
            try:
                markup = build_markup(reply.get('buttons', []))
                if reply["type"] == "text":
                    await update.message.reply_text(reply["content"], reply_markup=markup, disable_web_page_preview=True)
                elif reply["type"] == "photo":
                    await update.message.reply_photo(reply["file_id"], caption=reply.get("caption", ""), reply_markup=markup)
                elif reply["type"] == "video":
                    await update.message.reply_video(reply["file_id"], caption=reply.get("caption", ""), reply_markup=markup)
                elif reply["type"] == "sticker":
                    await update.message.reply_sticker(reply["file_id"])
                elif reply["type"] == "document":
                    await update.message.reply_document(reply["file_id"], caption=reply.get("caption", ""), reply_markup=markup)
            except Exception as e:
                logger.error(f"Error sending filter reply: {e}")
            break  # Only trigger one filter per message

# === MAIN APP ===

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("addfilter", addfilter_cmd))
    app.add_handler(CommandHandler("removefilter", removefilter_cmd))
    app.add_handler(CommandHandler("editfilter", editfilter_cmd))
    app.add_handler(CommandHandler("listfilters", listfilters_cmd))
    app.add_handler(CommandHandler("filterstats", filterstats_cmd))
    app.add_handler(CommandHandler("setregex", setregex_cmd))
    app.add_handler(CommandHandler("setsilent", setsilent_cmd))
    app.add_handler(CommandHandler("enablefilters", enablefilters_cmd))
    app.add_handler(CommandHandler("disablefilters", disablefilters_cmd))
    app.add_handler(CallbackQueryHandler(filters_pagination_callback, pattern=r"^filters_page:"))
    app.add_handler(MessageHandler(filters.ALL, message_filter_handler))

    print(f"{BOT_DISPLAY_NAME} started.")
    app.run_polling()

if __name__ == "__main__":
    main()
