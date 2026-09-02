import os
import json
import asyncio
import glob
import uuid
import requests
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)


# =========================================================
# SETTINGS & CONFIGURATION
# =========================================================

BOT_TOKEN = "8981768773:AAFMe_AL_y8mBFfmFiHFKa8lu1H3WtM8Xvc"
ADMIN_ID = 8466040187  

MAX_DOWNLOADS = 20
MAX_SENDS = 20
CONCURRENT_FRAGMENTS = 16
MAX_CHANNELS = 5

DOWNLOAD_DIR = "downloads"
CHANNELS_FILE = "channels.json"
USERS_FILE = "users.json"
STATS_FILE = "stats.json"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# =========================================================
# DATA MANAGEMENT (JSON)
# =========================================================

def load_json(filename, default=None):
    if default is None:
        default = []
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_channels():
    return load_json(CHANNELS_FILE, [])

def save_channels(channels):
    save_json(CHANNELS_FILE, channels)


# =========================================================
# DAILY STATS & SETTINGS TRACKER
# =========================================================

def get_today_date():
    return datetime.now().strftime("%Y-%m-%d")

def update_daily_stat(key: str, amount: int = 1):
    stats = load_json(STATS_FILE, {})
    today = get_today_date()
    
    if "daily" not in stats:
        stats["daily"] = {}
    if today not in stats["daily"]:
        stats["daily"][today] = {"joins": 0, "blocks": 0, "links": 0, "success": 0, "fails": 0}
        
    stats["daily"][today][key] = stats["daily"][today].get(key, 0) + amount
    save_json(STATS_FILE, stats)

def get_today_stats():
    stats = load_json(STATS_FILE, {})
    today = get_today_date()
    return stats.get("daily", {}).get(today, {"joins": 0, "blocks": 0, "links": 0, "success": 0, "fails": 0})

def get_forward_status():
    stats = load_json(STATS_FILE, {})
    return stats.get("forward_enabled", True)

def set_forward_status(status: bool):
    stats = load_json(STATS_FILE, {})
    stats["forward_enabled"] = status
    save_json(STATS_FILE, stats)


# =========================================================
# FORCED SUBSCRIPTION CHECKER
# =========================================================

async def check_subscriptions(bot, user_id: int):
    if user_id == ADMIN_ID:
        return True, []

    channels = load_channels()
    if not channels:
        return True, []

    missing_channels = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch["chat_id"], user_id=user_id)
            if member.status in ["left", "kicked"]:
                missing_channels.append(ch)
        except Exception as e:
            print(f"خطأ في فحص القناة {ch['chat_id']}: {e}")
            missing_channels.append(ch)

    return len(missing_channels) == 0, missing_channels

def get_sub_keyboard(missing_channels):
    keyboard = []
    for ch in missing_channels:
        keyboard.append([InlineKeyboardButton(f"📢 {ch['title']}", url=ch['link'])])
    keyboard.append([InlineKeyboardButton("🔄 تحقق من الاشتراك", callback_data="check_sub")])
    return InlineKeyboardMarkup(keyboard)


# =========================================================
# SEMAPHORES
# =========================================================

download_semaphore = asyncio.Semaphore(MAX_DOWNLOADS)
send_semaphore = asyncio.Semaphore(MAX_SENDS)


# =========================================================
# TRACK NEW USERS & BOT BLOCK NOTIFICATIONS
# =========================================================

async def track_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    users = load_json(USERS_FILE, [])
    if user.id not in users:
        users.append(user.id)
        save_json(USERS_FILE, users)
        update_daily_stat("joins", 1)

        username_str = f"@{user.username}" if user.username else "لا يوجد"
        msg = (
            "👤 **عضو جديد انضم للبوت!**\n\n"
            f"• الاسم: {user.full_name}\n"
            f"• المعرف: {username_str}\n"
            f"• الآيدي: `{user.id}`\n"
            f"• الرابط: tg://user?id={user.id}"
        )
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode="Markdown")
        except Exception:
            pass


async def track_bot_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result:
        return

    user = result.from_user
    old_status = getattr(result.old_chat_member, "status", None)
    new_status = getattr(result.new_chat_member, "status", None)

    if result.chat.type == "private":
        if old_status in ["member", "restricted"] and new_status == "kicked":
            update_daily_stat("blocks", 1)
            username_str = f"@{user.username}" if user.username else "لا يوجد"
            msg = (
                "🚫 **عضو قام بحظر البوت!**\n\n"
                f"• الاسم: {user.full_name}\n"
                f"• المعرف: {username_str}\n"
                f"• الآيدي: `{user.id}`"
            )
            try:
                await context.bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode="Markdown")
            except Exception:
                pass


# =========================================================
# START COMMAND
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = update.effective_user.id
    await track_user_start(update, context)

    is_subbed, missing = await check_subscriptions(context.bot, user_id)

    if not is_subbed:
        await update.message.reply_text(
            "⚠️ عذراً عزيزي، يجب عليك الاشتراك في قنوات البوت أولاً لاستخدامه:",
            reply_markup=get_sub_keyboard(missing)
        )
        return

    await update.message.reply_text("أهلاً بك! أرسل رابط (يوتيوب، تيك توك، إنستغرام، بنترست) أو اكتب اسم أي أغنية/فيديو للبحث عنه.")


# =========================================================
# ADMIN PANEL COMMAND & HANDLERS
# =========================================================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return

    forward_status = get_forward_status()
    forward_text = "توجيه الرسائل: ✅ (مفعل)" if forward_status else "توجيه الرسائل: ❌ (معطل)"

    keyboard = [
        [InlineKeyboardButton("➕ إضافة قناة", callback_data="admin_add_chan"),
         InlineKeyboardButton("➖ حذف قناة", callback_data="admin_del_menu")],
        [InlineKeyboardButton(forward_text, callback_data="admin_toggle_forward")],
        [InlineKeyboardButton("📊 إحصائيات البوت", callback_data="admin_stats")],
        [InlineKeyboardButton("❌ إغلاق اللوحة", callback_data="admin_close")]
    ]

    text = "⚙️ **لوحة التحكم الإدارية**\n\nاختر من الأزرار أدناه:"
    
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


# =========================================================
# UNIQUE JOB DIRECTORY
# =========================================================

def create_job_directory(user_id: int):
    job_id = str(user_id) + "_" + uuid.uuid4().hex
    job_dir = os.path.join(DOWNLOAD_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    return job_dir


# =========================================================
# EXPAND SHORT URLS (REQUESTS FIX)
# =========================================================

def expand_short_url(url: str) -> str:
    if "pin.it" in url or "pinterest.com" in url:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            }
            response = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
            if response.url:
                return response.url
        except Exception as e:
            print(f"فشل فك رابط بينترست: {e}")
    return url


# =========================================================
# YOUTUBE SEARCH FUNCTION
# =========================================================

async def search_youtube(query: str):
    command = [
        "python3", "-m", "yt_dlp",
        "ytsearch5:" + query,
        "--print", "%(id)s||%(title)s||%(uploader)s||%(duration_string)s",
        "--flat-playlist",
        "--skip-download",
        "--quiet", "--no-warnings"
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        output = stdout.decode("utf-8").strip()
        if not output:
            return []
        
        results = []
        for line in output.split("\n"):
            parts = line.split("||")
            if len(parts) >= 4:
                results.append({
                    "id": parts[0],
                    "title": parts[1],
                    "uploader": parts[2],
                    "duration": parts[3]
                })
        return results
    except Exception as e:
        print("SEARCH EXCEPTION:", e)
        return []


# =========================================================
# DOWNLOAD WITH YT-DLP
# =========================================================

async def download_media(url: str, job_dir: str, media_type: str):
    url = expand_short_url(url)
    output = os.path.join(job_dir, "%(id)s.%(ext)s")

    while True:
        command = [
            "python3", "-m", "yt_dlp",
            "--no-playlist",
            "-o", output,
            "--concurrent-fragments", str(CONCURRENT_FRAGMENTS),
            "--buffer-size", "1M",
            "--socket-timeout", "30",
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "--retries", "5",
            "--fragment-retries", "5",
            "--quiet", "--no-warnings",
        ]

        if "pinterest.com" in url or "pin.it" in url:
            command.extend([
                "--extractor-args", "pinterest:max_quality=true",
                "--geo-bypass",
            ])
        else:
            if media_type == "audio":
                command.extend(["-x", "--audio-format", "mp3", "--audio-quality", "0"])
            else:
                command.extend(["-f", "bv*+ba/b/best"])

        if "youtube.com" in url or "youtu.be" in url:
            cookies_path = "cookies.txt"

            if os.path.exists(cookies_path):
                command.extend(["--cookies", cookies_path])

        command.append(url)

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            files = glob.glob(os.path.join(job_dir, "*"))
            valid_files = [f for f in files if os.path.isfile(f) and os.path.getsize(f) > 0]

            if process.returncode == 0 and valid_files:
                valid_files.sort(key=os.path.getsize, reverse=True)
                return valid_files[0]

        except Exception as e:
            print("YT-DLP EXCEPTION:", repr(e))

        try:
            for file in glob.glob(os.path.join(job_dir, "*")):
                if os.path.isfile(file):
                    os.remove(file)
        except Exception:
            pass

        await asyncio.sleep(2)


# =========================================================
# SEND MEDIA
# =========================================================

async def send_media(update: Update, filename: str, media_type: str):
    async with send_semaphore:
        target_message = None
        if update.callback_query and update.callback_query.message:
            target_message = update.callback_query.message
        elif update.message:
            target_message = update.message

        if not target_message:
            return

        file_extension = os.path.splitext(filename)[1].lower()
        is_image = file_extension in [".jpg", ".jpeg", ".png", ".webp"]

        with open(filename, "rb") as media_file:
            if is_image:
                await target_message.reply_photo(
                    photo=media_file, 
                    read_timeout=180, 
                    write_timeout=180, 
                    connect_timeout=30
                )
            elif media_type == "audio":
                await target_message.reply_audio(
                    audio=media_file, 
                    read_timeout=180, 
                    write_timeout=180, 
                    connect_timeout=30
                )
            else:
                try:
                    await target_message.reply_video(
                        video=media_file, 
                        supports_streaming=True, 
                        read_timeout=180, 
                        write_timeout=180, 
                        connect_timeout=30
                    )
                except Exception:
                    media_file.seek(0)
                    await target_message.reply_document(
                        document=media_file, 
                        read_timeout=180, 
                        write_timeout=180, 
                        connect_timeout=30
                    )


# =========================================================
# PROCESS ONE JOB
# =========================================================

async def process_job(update: Update, status, url: str, media_type: str):
    user_id = update.effective_user.id if update.effective_user else 0
    job_dir = create_job_directory(user_id)
    filename = None

    try:
        async with download_semaphore:
            filename = await download_media(url, job_dir, media_type)

        if not filename or not os.path.exists(filename):
            update_daily_stat("fails", 1)
            raise Exception("لم يتم العثور على الملف أو أن الرابط غير مدعوم أو تالف.")

        update_daily_stat("success", 1)

        if status:
            try:
                await status.edit_text("تم التحميل، جاري الإرسال...")
            except Exception:
                pass

        await send_media(update, filename, media_type)

        if status:
            try:
                await status.delete()
            except Exception:
                pass

    except Exception as e:
        update_daily_stat("fails", 1)
        if status:
            try:
                await status.edit_text("فشل التحميل:\n" + str(e)[:1500])
            except Exception:
                pass

    finally:
        try:
            if os.path.exists(job_dir):
                for file in glob.glob(os.path.join(job_dir, "*")):
                    try:
                        os.remove(file)
                    except Exception:
                        pass
                os.rmdir(job_dir)
        except Exception:
            pass


# =========================================================
# RECEIVE URL / SEARCH QUERY / ADMIN INPUT
# =========================================================

def is_supported_url(text: str) -> bool:
    supported_domains = ["youtube.com", "youtu.be", "tiktok.com", "instagram.com", "pinterest.com", "pin.it"]
    return any(domain in text.lower() for domain in supported_domains)


async def download_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_id == ADMIN_ID and context.user_data.get("awaiting_channel"):
        context.user_data["awaiting_channel"] = False
        channels = load_channels()

        chat_identifier = text
        forward_origin = getattr(update.message, "forward_origin", None)
        if forward_origin:
            chat_obj = getattr(forward_origin, "chat", None)
            if chat_obj:
                chat_identifier = chat_obj.id

        try:
            chat = await context.bot.get_chat(chat_identifier)
            chat_id = chat.id
            title = chat.title or "قناة جديدة"
            link = chat.invite_link or (f"https://t.me/{chat.username}" if chat.username else text)

            bot_member = await context.bot.get_chat_member(chat_id=chat_id, user_id=context.bot.id)
            if bot_member.status not in ["administrator", "creator"]:
                await update.message.reply_text(
                    "❌ **عذراً، لا يمكن إضافة القناة!**\n\n"
                    "البوت ليس مشرفاً (Admin) في هذه القناة. يرجى رفع البوت مشرفاً أولاً.",
                    parse_mode="Markdown"
                )
                return

            if any(c["chat_id"] == chat_id for c in channels):
                await update.message.reply_text("⚠️ هذه القناة مضافة بالفعل!")
                return

            channels.append({
                "chat_id": chat_id,
                "title": title,
                "link": link
            })
            save_channels(channels)
            await update.message.reply_text(f"✅ تم إضافة القناة بنجاح:\n<b>{title}</b>", parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ فشل إضافة القناة. تأكد من أن البوت مشرف فيها.\nالخطأ: {e}")
        return

    is_subbed, missing = await check_subscriptions(context.bot, user_id)
    if not is_subbed:
        await update.message.reply_text(
            "⚠️ عذراً عزيزي، يجب عليك الاشتراك في قنوات البوت أولاً لاستخدامه:",
            reply_markup=get_sub_keyboard(missing)
        )
        return

    if get_forward_status() and user_id != ADMIN_ID:
        try:
            await update.message.forward(chat_id=ADMIN_ID)
        except Exception as e:
            print(f"فشل توجيه الرسالة للأدمن: {e}")

    if text.startswith(("http://", "https://")):
        if not is_supported_url(text):
            return

        update_daily_stat("links", 1)

        if "youtube.com" in text or "youtu.be" in text:
            keyboard = [
                [
                    InlineKeyboardButton("🎬 فيديو", callback_data=f"vid|{text}"),
                    InlineKeyboardButton("🎵 صوت (MP3)", callback_data=f"aud|{text}")
                ]
            ]
            await update.message.reply_text("اختر صيغة التحميل المناسبة:", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            try:
                status = await update.message.reply_text("جاري معالجة الرابط...")
            except Exception:
                status = None

            asyncio.create_task(process_job(update, status, text, "video"))
    else:
        update_daily_stat("links", 1)
        wait_msg = await update.message.reply_text(f"🔍 **نتائج البحث عن:** ( {text} )\n\nجاري البحث...")
        
        results = await search_youtube(text)
        if not results:
            await wait_msg.edit_text("❌ لم يتم العثور على نتائج مطابقة لبحثك.")
            return

        keyboard = []
        for r in results:
            video_url = f"https://youtu.be/{r['id']}"
            btn_text = f"🎬 {r['title'][:35]} | ⏱ {r['duration']}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"sbox|{video_url}")])

        await wait_msg.edit_text(
            f"🔍 **نتائج البحث عن:** ( {text} )\nاختر الفيديو للتحميل:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


# =========================================================
# BUTTON CALLBACK HANDLER
# =========================================================

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
        
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if data == "check_sub":
        is_subbed, missing = await check_subscriptions(context.bot, user_id)
        if is_subbed and query.message:
            try:
                await query.edit_message_text("✅ تم التحقق بنجاح! يمكنك الآن إرسال الرابط أو البحث وسيقوم البوت بتحميله مباشرة.")
            except Exception:
                await query.message.reply_text("✅ تم التحقق بنجاح! يمكنك الآن إرسال الرابط أو البحث وسيقوم البوت بتحميله مباشرة.")
        else:
            await query.answer("❌ لم تشترك في جميع القنوات بعد!", show_alert=True)
        return

    if user_id == ADMIN_ID and data.startswith("admin_"):
        channels = load_channels()

        if data == "admin_add_chan":
            if len(channels) >= MAX_CHANNELS:
                await query.answer("⚠️ وصلت للحد الأقصى (5 قنوات)!", show_alert=True)
                return
            context.user_data["awaiting_channel"] = True
            if query.message:
                await query.edit_message_text(
                    "📥 **إضافة قناة جديدة**\n\nقم بتوجيه رسالة من القناة هنا أو أرسل معرفها (مثال: `@channel`).",
                    parse_mode="Markdown"
                )
            return

        elif data == "admin_del_menu":
            if not channels:
                await query.answer("لا توجد قنوات مضافة حالياً!", show_alert=True)
                return
            keyboard = []
            for idx, ch in enumerate(channels):
                keyboard.append([InlineKeyboardButton(f"❌ {ch['title']}", callback_data=f"admin_del_id_{idx}")])
            keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_main")])
            if query.message:
                await query.edit_message_text("اختر القناة للحذف:", reply_markup=InlineKeyboardMarkup(keyboard))
            return

        elif data.startswith("admin_del_id_"):
            idx = int(data.split("_")[-1])
            if 0 <= idx < len(channels):
                removed = channels.pop(idx)
                save_channels(channels)
                await query.answer(f"تم حذف: {removed['title']}", show_alert=True)
            await admin_panel(update, context)
            return

        elif data == "admin_toggle_forward":
            current_status = get_forward_status()
            set_forward_status(not current_status)
            await query.answer("✅ تم تغيير حالة توجيه الرسائل بنجاح", show_alert=True)
            await admin_panel(update, context)
            return

        elif data == "admin_stats":
            total_users = len(load_json(USERS_FILE, []))
            today_s = get_today_stats()
            channels = load_channels()

            text = (
                "📊 **إحصائيات البوت الشاملة**\n\n"
                f"👥 **إجمالي المستخدمين:** `{total_users}`\n"
                f"📥 **عدد دخول اليوم:** `{today_s.get('joins', 0)}`\n"
                f"🚫 **من غادروا / حظروا البوت اليوم:** `{today_s.get('blocks', 0)}`\n"
                f"🔗 **عدد روابط اليوم:** `{today_s.get('links', 0)}`\n"
                f"✅ **التحميلات الناجحة اليوم:** `{today_s.get('success', 0)}`\n"
                f"❌ **التحميلات الخاطئة اليوم:** `{today_s.get('fails', 0)}`\n"
                f"📢 **عدد القنوات المفعلة:** `{len(channels)} / {MAX_CHANNELS}`\n"
            )

            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_main")]]
            if query.message:
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            return

        elif data == "admin_main":
            await admin_panel(update, context)
            return

        elif data == "admin_close":
            if query.message:
                await query.message.delete()
            return

    is_subbed, missing = await check_subscriptions(context.bot, user_id)
    if not is_subbed:
        if query.message:
            await query.message.reply_text(
                "⚠️ عذراً عزيزي، يجب عليك الاشتراك في قنوات البوت أولاً لاستخدامه:",
                reply_markup=get_sub_keyboard(missing)
            )
        return

    try:
        parts = data.split("|", 1)
        action = parts[0]
        url = parts[1]
    except Exception:
        return

    if action == "sbox":
        keyboard = [
            [
                InlineKeyboardButton("🎬 فيديو", callback_data=f"vid|{url}"),
                InlineKeyboardButton("🎵 صوت (MP3)", callback_data=f"aud|{url}")
            ]
        ]
        if query.message:
            try:
                await query.edit_message_text("اختر صيغة التحميل المناسبة:", reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception:
                await query.message.reply_text("اختر صيغة التحميل المناسبة:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    media_type = "video" if action == "vid" else "audio"

    status = None
    if query.message:
        try:
            status = await query.edit_message_text("جاري معالجة الطلب...")
        except Exception:
            status = await query.message.reply_text("جاري معالجة الطلب...")

    asyncio.create_task(process_job(update, status, url, media_type))


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("TELEGRAM ERROR:", repr(context.error))


# =========================================================
# MAIN
# =========================================================

def main():
    request = HTTPXRequest(
        connect_timeout=20,
        read_timeout=180,
        write_timeout=180,
        pool_timeout=180
    )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .get_updates_request(request)
        .concurrent_updates(100)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_handler))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    app.add_handler(ChatMemberHandler(track_bot_block, ChatMemberHandler.MY_CHAT_MEMBER))

    app.add_error_handler(error_handler)

    print("==========================================")
    print("BOT STARTED SUCCESSFULLY (ALL FEATURES READY)")
    print("==========================================")

    app.run_polling(drop_pending_updates=True, bootstrap_retries=5)


if __name__ == "__main__":
    main()

