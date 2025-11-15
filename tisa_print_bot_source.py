import pymysql
import logging
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, InputMediaPhoto, InputMediaDocument, ReplyKeyboardMarkup, KeyboardButton, MenuButtonCommands
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ContextTypes,
)
from datetime import datetime, timedelta
from telegram.constants import ParseMode
import os
import subprocess
import tempfile
import time
import json
import uuid
from io import BytesIO
from telegram.helpers import escape_markdown
import pytz
import signal
import asyncio
from dotenv import load_dotenv

load_dotenv()
required_env_vars = [
    'MAIN_BOT_TOKEN', 
    'MAIN_NAZI_CHAT_ID', 
    'MAIN_DESIGNER_CHAT_ID',
    'MAIN_GROUP_PRODUCTS',
    'MAIN_GROUP_PRINT',
    'MAIN_LOG_GROUP_ID'
]
missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    logging.error(f"Missing environment variables: {missing_vars}")
    exit(1)


BOT_TOKEN = os.getenv("MAIN_BOT_TOKEN")
NAZI_CHAT_ID = int(os.getenv("MAIN_NAZI_CHAT_ID"))
GROUP_PRODUCTS = int(os.getenv("MAIN_GROUP_PRODUCTS"))
GROUP_PRINT = int(os.getenv("MAIN_GROUP_PRINT"))
DESIGNER_CHAT_ID = int(os.getenv("MAIN_DESIGNER_CHAT_ID"))
LOG_GROUP_ID = int(os.getenv("MAIN_LOG_GROUP_ID"))

DB_CONFIG = {
    'host': os.getenv('MAIN_DB_HOST'),
    'user': os.getenv('MAIN_DB_USER'),
    'password': os.getenv('MAIN_DB_PASSWORD'),
    'database': os.getenv('MAIN_DB_NAME'),
    'charset': 'utf8mb4',
    'autocommit': True
}

restore_pending = {}
restore_files = {}
TEHRAN_TZ = pytz.timezone('Asia/Tehran')
application_ref = None
admin_cache = {}
admin_cache_time = {}
user_sessions = {}


def get_db_connection():
    return pymysql.connect(**DB_CONFIG)
            
def init_db():
    """ایجاد جداول با ساختار جدید"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # ============= جداول طرح‌های موبایل =============
        # جدول طرح‌های در انتظار
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_designs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(20) UNIQUE NOT NULL,
                designer_chat_id BIGINT NOT NULL,
                mockup_file_ids JSON NOT NULL,
                print_file_ids JSON NOT NULL,
                mockup_message_ids_nazi JSON,
                created_at DATETIME NOT NULL
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)

        # جدول تاریخچه
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS design_log (
                id INT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(20) NOT NULL,
                final_name TEXT,
                status ENUM('approved', 'rejected') NOT NULL,
                designer_chat_id BIGINT NOT NULL,
                created_at DATETIME NOT NULL
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)

        # جدول کدهای قفل شده
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS locked_codes (
                code VARCHAR(20) PRIMARY KEY,
                locked_at DATETIME NOT NULL
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)

        # جدول ثبت فایل‌های اپلود شده
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS uploaded_files (
                id INT AUTO_INCREMENT PRIMARY KEY,
                file_unique_id VARCHAR(255) NOT NULL,
                code VARCHAR(20) NOT NULL,
                uploaded_by_chat_id BIGINT NULL,
                uploaded_by_name VARCHAR(100) NULL,
                uploaded_at DATETIME NULL,
                created_at DATETIME NOT NULL,
                message_id_in_group INT NOT NULL,
                status ENUM('pending', 'uploaded') DEFAULT 'pending',
                INDEX idx_code (code),
                INDEX idx_status (status),
                INDEX idx_uploaded_at (uploaded_at),
                INDEX idx_created_at (created_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)

        # ============= جداول استیکرها (جدید) =============
        # جدول استیکرهای در انتظار
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_stickers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(20) UNIQUE NOT NULL,
                designer_chat_id BIGINT NOT NULL,
                mockup_file_ids JSON NOT NULL,
                print_file_ids JSON NOT NULL,
                mockup_message_ids_nazi JSON,
                created_at DATETIME NOT NULL
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)

        # جدول تاریخچه استیکرها
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sticker_log (
                id INT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(20) NOT NULL,
                final_name TEXT,
                status ENUM('approved', 'rejected') NOT NULL,
                designer_chat_id BIGINT NOT NULL,
                created_at DATETIME NOT NULL
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)

        # جدول کدهای قفل شده استیکرها
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS locked_sticker_codes (
                code VARCHAR(20) PRIMARY KEY,
                locked_at DATETIME NOT NULL
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)

        # جدول ثبت فایل‌های اپلود شده استیکرها
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS uploaded_sticker_files (
                id INT AUTO_INCREMENT PRIMARY KEY,
                file_unique_id VARCHAR(255) NOT NULL,
                code VARCHAR(20) NOT NULL,
                uploaded_by_chat_id BIGINT NULL,
                uploaded_by_name VARCHAR(100) NULL,
                uploaded_at DATETIME NULL,
                created_at DATETIME NOT NULL,
                message_id_in_group INT NOT NULL,
                status ENUM('pending', 'uploaded') DEFAULT 'pending',
                INDEX idx_code (code),
                INDEX idx_status (status),
                INDEX idx_uploaded_at (uploaded_at),
                INDEX idx_created_at (created_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)

        # ============= جداول مشترک =============
        # جدول ادمین‌های اپلود
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS upload_admins (
                chat_id BIGINT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                promoted_by BIGINT NOT NULL,
                promoted_at DATETIME NOT NULL
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)

        conn.commit()
        logging.info("✅ Database tables initialized successfully (Mobile + Sticker)")
        
    except Exception as e:
        logging.error(f"Database initialization error: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
        
        
class TelegramLogHandler(logging.Handler):
    
    def __init__(self, bot_token: str, chat_id: int):
        super().__init__()
        self.chat_id = chat_id
        self.bot_token = bot_token
        self.bot = None
        self.message_queue = []
        self.is_sending = False
        
    async def init_bot(self):
        """راه‌اندازی bot"""
        if self.bot is None:
            from telegram import Bot
            self.bot = Bot(token=self.bot_token)
    
    def emit(self, record):
        """ارسال لاگ به تلگرام"""
        try:
            # فرمت کاملاً خام (همون چیزی که تو کنسول چاپ میشه)
            log_entry = self.format(record)
            
            # محدودیت طول پیام تلگرام
            if len(log_entry) > 4000:
                log_entry = log_entry[:3950] + "\n... (بریده شد)"
            
            # اضافه به صف
            self._queue_message(log_entry)
                
        except Exception as e:
            print(f"Failed to send log to Telegram: {e}")
    
    def _queue_message(self, message: str):
        """اضافه کردن پیام به صف"""
        self.message_queue.append(message)
        
        if not self.is_sending:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._process_queue())
            except RuntimeError:
                pass  # اگه event loop نبود، صبر می‌کنیم
    
    async def _process_queue(self):
        """پردازش صف پیام‌ها"""
        if self.is_sending:
            return
            
        self.is_sending = True
        
        try:
            await self.init_bot()
            
            while self.message_queue:
                message = self.message_queue.pop(0)
                
                try:
                    # ارسال بدون هیچ parse mode (متن ساده)
                    await self.bot.send_message(
                        chat_id=self.chat_id,
                        text=message
                    )
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    print(f"Telegram send error: {e}")
                    
        finally:
            self.is_sending = False 
            
def generate_code():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # چک همهٔ کدهایی که تا به حال استفاده شده‌اند
        cursor.execute("""
            SELECT code FROM locked_codes 
            UNION 
            SELECT code FROM pending_designs
            UNION
            SELECT code FROM design_log
        """)
        used_codes = {row[0] for row in cursor.fetchall()}

        # زمان فعلی تهران به UTC برای ذخیره
        now_tehran = get_tehran_time()
        now_utc = to_utc_naive(now_tehran)

        for counter in range(1, 1000):
            code = f"TS{counter:03d}"
            if code not in used_codes:
                # ذخیره در pending_designs با زمان تهران
                cursor.execute("""
                    INSERT INTO pending_designs 
                    (code, designer_chat_id, mockup_file_ids, print_file_ids, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                """, (code, DESIGNER_CHAT_ID, '[]', '[]', now_utc))
                conn.commit()
                return code
        
        raise Exception("No available codes (TS001-TS999)")
    except Exception as e:
        logging.error(f"Code generation error: {e}")
        conn.rollback()
        return f"ERR{int(time.time())}"
    finally:
        cursor.close()
        conn.close()

def generate_sticker_code():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # چک همهٔ کدهایی که تا به حال استفاده شده‌اند
        cursor.execute("""
            SELECT code FROM locked_sticker_codes 
            UNION 
            SELECT code FROM pending_stickers
            UNION
            SELECT code FROM sticker_log
        """)
        used_codes = {row[0] for row in cursor.fetchall()}

        # زمان فعلی تهران به UTC برای ذخیره
        now_tehran = get_tehran_time()
        now_utc = to_utc_naive(now_tehran)

        for counter in range(1, 1000):
            code = f"STI{counter:03d}"
            if code not in used_codes:
                # ذخیره در pending_stickers با زمان تهران
                cursor.execute("""
                    INSERT INTO pending_stickers 
                    (code, designer_chat_id, mockup_file_ids, print_file_ids, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                """, (code, DESIGNER_CHAT_ID, '[]', '[]', now_utc))
                conn.commit()
                return code
        
        raise Exception("No available sticker codes (STI001-STI999)")
    except Exception as e:
        logging.error(f"Sticker code generation error: {e}")
        conn.rollback()
        return f"ERR{int(time.time())}"
    finally:
        cursor.close()
        conn.close()

async def cleanup_stale_pending_codes(context: ContextTypes.DEFAULT_TYPE):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # محاسبه 1 ساعت گذشته به وقت تهران
        now_tehran = get_tehran_time()
        one_hour_ago_tehran = now_tehran - timedelta(hours=1)
        one_hour_ago_utc = to_utc_naive(one_hour_ago_tehran)
        
        # پاک کردن pending_designs هایی که بیش از 1 ساعت فایل نداشتن
        cursor.execute("""
            DELETE FROM pending_designs 
            WHERE JSON_LENGTH(mockup_file_ids) = 0 
              AND JSON_LENGTH(print_file_ids) = 0
              AND created_at < %s
        """, (one_hour_ago_utc,))
        deleted_designs = cursor.rowcount
        
        # پاک کردن pending_stickers هایی که بیش از 1 ساعت فایل نداشتن
        cursor.execute("""
            DELETE FROM pending_stickers 
            WHERE JSON_LENGTH(mockup_file_ids) = 0 
              AND JSON_LENGTH(print_file_ids) = 0
              AND created_at < %s
        """, (one_hour_ago_utc,))
        deleted_stickers = cursor.rowcount
        
        if deleted_designs > 0 or deleted_stickers > 0:
            logging.info(f"🧹 Cleaned up {deleted_designs} stale design codes and {deleted_stickers} stale sticker codes.")
        conn.commit()
    except Exception as e:
        logging.error(f"Cleanup stale codes error: {e}")
    finally:
        cursor.close()
        conn.close()
        
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id == DESIGNER_CHAT_ID:
        # تنظیم Menu Button
        await context.bot.set_chat_menu_button(
            chat_id=chat_id,
            menu_button=MenuButtonCommands()
        )
        
        # 🆕 دکمه‌های ثابت برای علیرضا (با ریستارت و وضعیت)
        keyboard = [
            [KeyboardButton("➕ ثبت طرح موبایل"), KeyboardButton("🎨 ثبت استیکر")],
            [KeyboardButton("📊 آمار موبایل"), KeyboardButton("📊 آمار استیکر")],
            [KeyboardButton("📤 گزارش موبایل"), KeyboardButton("📤 گزارش استیکر")],
            [KeyboardButton("💾 بکاپ"), KeyboardButton("👥 ادمین‌ها")],
            [KeyboardButton("🔄 ریستارت"), KeyboardButton("📊 وضعیت")] 
        ]
        reply_markup = ReplyKeyboardMarkup(
            keyboard, 
            resize_keyboard=True
        )
        
        await update.message.reply_text(
            "سلام علیرضا 👋\n\n"
            "📱 برای ثبت طرح موبایل: دکمه یا /new\n"
            "🎨 برای ثبت استیکر: دکمه یا /new_sticker\n\n"
            "💡 دکمه Menu (کنار 📎) هم فعاله!",
            reply_markup=reply_markup
        )
        
    elif chat_id == NAZI_CHAT_ID:
        # تنظیم Menu Button
        await context.bot.set_chat_menu_button(
            chat_id=chat_id,
            menu_button=MenuButtonCommands()
        )
        
        # دکمه‌های ثابت برای نازی (بدون ریستارت)
        keyboard = [
            [KeyboardButton("📊 آمار موبایل"), KeyboardButton("📊 آمار استیکر")],
            [KeyboardButton("📤 گزارش موبایل"), KeyboardButton("📤 گزارش استیکر")],
            [KeyboardButton("👥 ادمین‌ها")]
        ]
        reply_markup = ReplyKeyboardMarkup(
            keyboard, 
            resize_keyboard=True
        )
        
        await update.message.reply_text(
            "سلام نازی عزیز 💼\n"
            "طرح‌های موبایل و استیکرهایی که علیرضا آماده می‌کنه، اینجا برات نمایش داده می‌شن.\n\n"
            "📌 راهنمای استفاده:\n"
            "✅ برای تایید: روی دکمه «تایید» کلیک کن\n"
            "❌ برای رد: روی دکمه «رد کردن» کلیک کن\n"
            "🔧 برای ارسال اصلاحیه: روی *آخرین موکاپ* ریپلای کن و متن اصلاحیه رو بنویس\n\n"
            "💡 از دکمه‌های زیر یا Menu هم می‌تونی استفاده کنی!",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text("🚫 شما مجاز به استفاده از این ربات نیستید.")

async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id != DESIGNER_CHAT_ID:
        await update.message.reply_text("🚫 فقط علیرضا می‌تونه طرح ثبت کنه!")
        return

    # پاک کردن داده‌های قبلی
    keys_to_clear = ['mockup_files', 'print_files', 'awaiting_input', 'code', 'current_menu_message_id']
    for key in keys_to_clear:
        context.user_data.pop(key, None)

    # تولید کد جدید
    code = generate_code()
    context.user_data['code'] = code
    context.user_data['mockup_files'] = []
    context.user_data['print_files'] = []
    context.user_data['awaiting_input'] = None

    # نمایش منوی اولیه
    await show_interactive_menu(update, context, edit=False)
    
async def new_sticker_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id != DESIGNER_CHAT_ID:
        await update.message.reply_text("🚫 فقط علیرضا می‌تونه استیکر ثبت کنه!")
        return

    # پاک کردن داده‌های قبلی
    keys_to_clear = ['mockup_files', 'print_files', 'awaiting_input', 'code', 'current_menu_message_id', 'design_type']
    for key in keys_to_clear:
        context.user_data.pop(key, None)

    # تولید کد جدید
    code = generate_sticker_code()
    context.user_data['code'] = code
    context.user_data['design_type'] = 'sticker'  # نوع طرح
    context.user_data['mockup_files'] = []
    context.user_data['print_files'] = []
    context.user_data['awaiting_input'] = None

    # نمایش منوی اولیه
    await show_interactive_menu(update, context, edit=False)
    
async def handle_designer_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # فقط اگر در حالت دریافت فایل باشیم
    if context.user_data.get('awaiting_input') not in ['mockup', 'print']:
        return

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    else:
        await update.message.reply_text("❌ لطفاً فایل معتبر بفرستید.")
        return

    if context.user_data['awaiting_input'] == 'mockup':
        if 'mockup_files' not in context.user_data:
            context.user_data['mockup_files'] = []
        context.user_data['mockup_files'].append(file_id)
        await update.message.reply_text(f"✅ موکاپ اضافه شد. تعداد کل: {len(context.user_data['mockup_files'])}")
    elif context.user_data['awaiting_input'] == 'print':
        if 'print_files' not in context.user_data:
            context.user_data['print_files'] = []
        context.user_data['print_files'].append(file_id)
        await update.message.reply_text(f"✅ فایل چاپی اضافه شد. تعداد کل: {len(context.user_data['print_files'])}")

    # آپدیت منو
    await show_interactive_menu(update, context, edit=True)
    
async def show_interactive_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    mockup_count = len(context.user_data.get('mockup_files', []))
    print_count = len(context.user_data.get('print_files', []))
    code = context.user_data.get('code', 'N/A')
    design_type = context.user_data.get('design_type', 'mobile')
    
    # عنوان متناسب با نوع
    type_label = "📱 طرح موبایل" if design_type == 'mobile' else "🎨 استیکر"

    # تشخیص حالت فعلی
    if context.user_data.get('awaiting_input') == 'mockup':
        text = f"{type_label} — کد: {code}\n\n📥 لطفاً موکاپ‌ها رو بفرست (عکس یا فایل)\n\n💡 برای بازگشت به منو، دکمه زیر رو بزن:"
        reply_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("↩️ بازگشت به منو", callback_data="back_to_menu")
        ]])
    elif context.user_data.get('awaiting_input') == 'print':
        text = f"{type_label} — کد: {code}\n\n📥 لطفاً فایل‌های چاپی رو بفرست (PNG/PSD/AI)\n\n💡 برای بازگشت به منو، دکمه زیر رو بزن:"
        reply_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("↩️ بازگشت به منو", callback_data="back_to_menu")
        ]])
    else:
        # منوی اصلی
        text = (
            f"{type_label} — کد: *{code}*\n\n"
            f"📊 وضعیت فعلی:\n"
            f"🖼 موکاپ: {mockup_count} {'✅' if mockup_count > 0 else '❌'}\n"
            f"🖨 فایل چاپی: {print_count} {'✅' if print_count > 0 else '❌'}"
        )
        
        buttons = [
            [
                InlineKeyboardButton(
                    f"🖼 {'ویرایش' if mockup_count > 0 else 'افزودن'} موکاپ ({mockup_count})" if mockup_count > 0 else "🖼 افزودن موکاپ",
                    callback_data="add_mockup"
                ),
                InlineKeyboardButton(
                    f"🖨 {'ویرایش' if print_count > 0 else 'افزودن'} فایل چاپی ({print_count})" if print_count > 0 else "🖨 افزودن فایل چاپی",
                    callback_data="add_print"
                )
            ]
        ]
        
        # دکمه ثبت نهایی فقط وقتی هر دو فایل موجود باشه
        if mockup_count > 0 and print_count > 0:
            buttons.append([
                InlineKeyboardButton("✅ ثبت نهایی و ارسال", callback_data="confirm_submit")
            ])
        
        buttons.append([
            InlineKeyboardButton("🗑 لغو و حذف", callback_data="cancel_submission")
        ])
        
        reply_markup = InlineKeyboardMarkup(buttons)

    # ذخیره وضعیت قبلی برای جلوگیری از ویرایش تکراری
    last_menu_state = context.user_data.get('last_menu_state')
    current_state = f"{text}||{str(reply_markup)}"
    
    if edit and 'current_menu_message_id' in context.user_data:
        # چک کردن تغییر واقعی
        if last_menu_state == current_state:
            # هیچ تغییری نکرده، نیازی به ویرایش نیست
            return
        
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['current_menu_message_id'],
                text=text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            # ذخیره وضعیت جدید
            context.user_data['last_menu_state'] = current_state
        except Exception as e:
            # اگر پیام حذف شده یا خطایی رخ داد، پیام جدید بفرست
            logging.warning(f"Edit menu error: {e}")
            sent_msg = await update.effective_chat.send_message(
                text=text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            context.user_data['current_menu_message_id'] = sent_msg.message_id
            context.user_data['last_menu_state'] = current_state
    else:
        # ارسال پیام جدید
        if hasattr(update, 'message') and update.message:
            sent_msg = await update.message.reply_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        else:
            sent_msg = await update.effective_chat.send_message(
                text=text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        context.user_data['current_menu_message_id'] = sent_msg.message_id
        context.user_data['last_menu_state'] = current_state
        
async def handle_add_mockup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()


    context.user_data['mockup_files'] = []
    context.user_data['awaiting_input'] = 'mockup'
    
    await show_interactive_menu(update, context, edit=True)
    
async def handle_add_print(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()


    context.user_data['print_files'] = []
    context.user_data['awaiting_input'] = 'print'
    
    await show_interactive_menu(update, context, edit=True)
    
async def handle_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data['awaiting_input'] = None
    await show_interactive_menu(update, context, edit=True)    
        
async def handle_cancel_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    code = context.user_data.get('code')
    
    # پاک کردن کد از دیتابیس
    if code:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # حذف از pending_designs برای آزاد کردن کد
            cursor.execute("DELETE FROM pending_designs WHERE code = %s", (code,))
            conn.commit()
            logging.info(f"🗑️ Code {code} released (submission cancelled)")
        except Exception as e:
            logging.error(f"Error releasing code {code}: {e}")
            conn.rollback()
        finally:
            cursor.close()
            conn.close()

    await query.edit_message_text(
        "🗑️ ثبت طرح لغو شد.\n"
        f"کد {code} آزاد شد.\n\n"
        "[➕ ثبت طرح جدید](/new)", 
        parse_mode="Markdown"
    )
    
    # پاک کردن داده‌های موقت
    keys_to_clear = ['mockup_files', 'print_files', 'awaiting_input', 'code', 'current_menu_message_id', 'last_menu_state']
    for key in keys_to_clear:
        context.user_data.pop(key, None)
              
async def handle_confirm_submit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    mockup_files = context.user_data.get('mockup_files', [])
    print_files = context.user_data.get('print_files', [])
    code = context.user_data['code']
    design_type = context.user_data.get('design_type', 'mobile')

    if len(mockup_files) == 0 or len(print_files) == 0:
        await query.answer("⚠️ لطفاً حداقل یک موکاپ و یک فایل چاپی ارسال کنید.", show_alert=True)
        return

    await query.edit_message_text("✅ در حال پردازش... لطفاً صبر کنید.")

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        mockup_json = json.dumps(mockup_files, ensure_ascii=False)
        print_json = json.dumps(print_files, ensure_ascii=False)

        # آپدیت در جدول مناسب
        if design_type == 'mobile':
            cursor.execute("""
                UPDATE pending_designs 
                SET mockup_file_ids = %s, print_file_ids = %s
                WHERE code = %s
            """, (mockup_json, print_json, code))
        else:  # sticker
            cursor.execute("""
                UPDATE pending_stickers 
                SET mockup_file_ids = %s, print_file_ids = %s
                WHERE code = %s
            """, (mockup_json, print_json, code))

        conn.commit()

        # ارسال به نازی
        total_mockups = len(mockup_files)
        message_ids_to_save = []

        # شناسه callback برای approve/reject
        approve_callback = f"approve_{design_type}_{code}"
        reject_callback = f"reject_{design_type}_{code}"

        if total_mockups == 1:
            fid = mockup_files[0]
            caption = f"کد {'طرح' if design_type == 'mobile' else 'استیکر'}: {code}"

            if isinstance(fid, str) and (fid.startswith(('AgAC', 'AQA'))):
                sent_msg = await context.bot.send_photo(
                    chat_id=NAZI_CHAT_ID,
                    photo=fid,
                    caption=caption,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ تایید", callback_data=approve_callback),
                        InlineKeyboardButton("❌ رد کردن", callback_data=reject_callback)
                    ]])
                )
            else:
                sent_msg = await context.bot.send_document(
                    chat_id=NAZI_CHAT_ID,
                    document=fid,
                    caption=caption,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ تایید", callback_data=approve_callback),
                        InlineKeyboardButton("❌ رد کردن", callback_data=reject_callback)
                    ]])
                )
            message_ids_to_save = [sent_msg.message_id]
        else:
            media_group = []
            for i, fid in enumerate(mockup_files):
                caption = f"کد {'طرح' if design_type == 'mobile' else 'استیکر'}: {code}\n(موکاپ {i+1} از {total_mockups})"
                if isinstance(fid, str) and (fid.startswith(('AgAC', 'AQA'))):
                    media_group.append(InputMediaPhoto(media=fid, caption=caption))
                else:
                    media_group.append(InputMediaDocument(media=fid, caption=caption))

            sent_messages = await context.bot.send_media_group(
                chat_id=NAZI_CHAT_ID,
                media=media_group
            )
            media_message_ids = [msg.message_id for msg in sent_messages]

            control_msg = await context.bot.send_message(
                chat_id=NAZI_CHAT_ID,
                text=f"👇 *{'طرح' if design_type == 'mobile' else 'استیکر'} {code}* — لطفاً وضعیت رو مشخص کنید:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ تایید", callback_data=approve_callback),
                    InlineKeyboardButton("❌ رد کردن", callback_data=reject_callback)
                ]]),
                reply_to_message_id=media_message_ids[-1]
            )
            message_ids_to_save = media_message_ids + [control_msg.message_id]

        message_ids_json = json.dumps(message_ids_to_save, ensure_ascii=False)
        
        if design_type == 'mobile':
            cursor.execute("UPDATE pending_designs SET mockup_message_ids_nazi = %s WHERE code = %s", 
                          (message_ids_json, code))
        else:  # sticker
            cursor.execute("UPDATE pending_stickers SET mockup_message_ids_nazi = %s WHERE code = %s", 
                          (message_ids_json, code))
        conn.commit()

        type_label = "طرح موبایل" if design_type == 'mobile' else "استیکر"
        new_command_text = "/new" if design_type == 'mobile' else "/new_sticker"
        undo_callback = f"undo_{design_type}_{code}"

        await query.edit_message_text(
            f"✅ {type_label} {code} با موفقیت ثبت و به نازی ارسال شد!\n\n"
            f"📦 شامل:\n- {len(mockup_files)} موکاپ\n- {len(print_files)} فایل چاپی\n\n"
            f"[➕ ثبت {type_label} جدید]({new_command_text})",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("↩️ Undo", callback_data=undo_callback)
            ]])
        )

    except Exception as e:
        logging.error(f"Submit Error: {e}")
        await query.edit_message_text("❌ خطایی در ثبت رخ داد.")
    finally:
        cursor.close()
        conn.close()

    # پاک کردن داده‌های موقت
    for key in ['mockup_files', 'print_files', 'awaiting_input', 'code', 'current_menu_message_id', 'design_type']:
        context.user_data.pop(key, None)
        
async def handle_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # استخراج نوع و کد
    parts = query.data.split("_")
    if len(parts) < 3:
        await query.edit_message_text("❌ داده نامعتبر.")
        return
    
    design_type = parts[1]  # mobile یا sticker
    code = parts[2]

    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # انتخاب جدول مناسب
        if design_type == 'mobile':
            table_pending = 'pending_designs'
            table_log = 'design_log'
        else:  # sticker
            table_pending = 'pending_stickers'
            table_log = 'sticker_log'
        
        cursor.execute(f"SELECT designer_chat_id, mockup_message_ids_nazi FROM {table_pending} WHERE code = %s", (code,))
        row = cursor.fetchone()
        
        if not row:
            await query.edit_message_text("❌ این طرح قبلاً پردازش شده.")
            return

        designer_chat_id, mockup_message_ids_str = row
        mockup_message_ids = json.loads(mockup_message_ids_str)

        # ویرایش پیام فعلی
        type_label = "طرح" if design_type == 'mobile' else "استیکر"
        await query.edit_message_text(f"🗑️ {type_label} {code} رد شد.")

        # پاک کردن همه پیام‌های مرتبط
        await delete_messages(context.bot, NAZI_CHAT_ID, mockup_message_ids)

        # زمان فعلی تهران به UTC
        now_utc = to_utc_naive(get_tehran_time())

        # ذخیره در لاگ با زمان تهران
        cursor.execute(f"""
            INSERT INTO {table_log} (code, final_name, status, designer_chat_id, created_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (code, '', 'rejected', designer_chat_id, now_utc))

        cursor.execute(f"DELETE FROM {table_pending} WHERE code = %s", (code,))
        conn.commit()

        new_command = "/new" if design_type == 'mobile' else "/new_sticker"
        await context.bot.send_message(
            designer_chat_id,
            f"🔴 {type_label} {code} رد شد.\nبرای ارسال {type_label} جدید، دوباره {new_command} را بفرستید."
        )

    except json.JSONDecodeError as e:
        logging.error(f"JSON Decode Error in reject: {e}")
        await query.edit_message_text("❌ خطایی در خواندن داده‌ها رخ داد.")
    except Exception as e:
        logging.error(f"Reject Error: {e}")
        conn.rollback()
        await query.edit_message_text("❌ خطایی در رد کردن رخ داد.")
    finally:
        cursor.close()
        conn.close()
        
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logging.info(f"User {chat_id} requested /stats (mobile)")

    if chat_id != DESIGNER_CHAT_ID and chat_id != NAZI_CHAT_ID:
        await update.message.reply_text(f"🚫 دسترسی غیرمجاز.")
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT COUNT(*) FROM pending_designs")
        pending_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM design_log WHERE status = 'approved'")
        approved_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM design_log WHERE status = 'rejected'")
        rejected_count = cursor.fetchone()[0]

        # زمان فعلی تهران
        now_tehran = get_tehran_time()
        time_str = now_tehran.strftime('%Y-%m-%d %H:%M')

        await update.message.reply_text(
            f"📊 *آمار طرح‌های موبایل*\n\n"
            f"🟡 در انتظار تایید: *{pending_count}*\n"
            f"✅ تایید شده: *{approved_count}*\n"
            f"❌ رد شده: *{rejected_count}*\n\n"
            f"_آخرین بروزرسانی: {time_str}_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ ریست کامل آمار", callback_data="confirm_reset_stats_mobile")
            ]])
        )

    except Exception as e:
        logging.error(f"Stats Error: {e}")
        await update.message.reply_text("❌ خطایی در دریافت آمار رخ داد.")
    finally:
        cursor.close()
        conn.close()

async def stats_sticker_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logging.info(f"User {chat_id} requested /stats_sticker")

    if chat_id != DESIGNER_CHAT_ID and chat_id != NAZI_CHAT_ID:
        await update.message.reply_text(f"🚫 دسترسی غیرمجاز.")
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT COUNT(*) FROM pending_stickers")
        pending_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sticker_log WHERE status = 'approved'")
        approved_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM sticker_log WHERE status = 'rejected'")
        rejected_count = cursor.fetchone()[0]

        # زمان فعلی تهران
        now_tehran = get_tehran_time()
        time_str = now_tehran.strftime('%Y-%m-%d %H:%M')

        await update.message.reply_text(
            f"📊 *آمار استیکرها*\n\n"
            f"🟡 در انتظار تایید: *{pending_count}*\n"
            f"✅ تایید شده: *{approved_count}*\n"
            f"❌ رد شده: *{rejected_count}*\n\n"
            f"_آخرین بروزرسانی: {time_str}_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ ریست کامل آمار", callback_data="confirm_reset_stats_sticker")
            ]])
        )

    except Exception as e:
        logging.error(f"Stats Sticker Error: {e}")
        await update.message.reply_text("❌ خطایی در دریافت آمار رخ داد.")
    finally:
        cursor.close()
        conn.close()
        
async def confirm_reset_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.from_user.id

    # چک دسترسی
    if chat_id != DESIGNER_CHAT_ID and chat_id != NAZI_CHAT_ID:
        await query.edit_message_text("🚫 دسترسی غیرمجاز.")
        return

    # تشخیص نوع
    design_type = 'mobile' if 'mobile' in query.data else 'sticker'
    type_label = "طرح‌های موبایل" if design_type == 'mobile' else "استیکرها"

    # نمایش پیام تأیید نهایی
    await query.edit_message_text(
        text=f"⚠️ مطمئنی می‌خوای *همه آمار {type_label}* رو پاک کنی؟\nاین عمل *غیرقابل بازگشت* هست!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ بله، پاک شه!", callback_data=f"do_reset_stats_{design_type}"),
                InlineKeyboardButton("❌ انصراف", callback_data="cancel_reset")
            ]
        ])
    )
   
async def do_reset_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.from_user.id

    if chat_id != DESIGNER_CHAT_ID and chat_id != NAZI_CHAT_ID:
        await query.edit_message_text("🚫 دسترسی غیرمجاز.")
        return

    # تشخیص نوع
    design_type = 'mobile' if 'mobile' in query.data else 'sticker'
    table_name = 'design_log' if design_type == 'mobile' else 'sticker_log'
    type_label = "طرح‌های موبایل" if design_type == 'mobile' else "استیکرها"

    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute(f"DELETE FROM {table_name}")
        conn.commit()

        logging.info(f"📊 آمار {type_label} توسط کاربر {chat_id} ریست شد.")

        await query.edit_message_text(
            f"✅ آمار {type_label} با موفقیت ریست شد!\nهمه داده‌ها پاک شدند.",
            reply_markup=None
        )

    except Exception as e:
        logging.error(f"Reset Stats Error: {e}")
        conn.rollback()
        await query.edit_message_text("❌ خطایی در ریست آمار رخ داد.")
    finally:
        cursor.close()
        conn.close()
        
async def cancel_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("👌 عملیات ریست لغو شد.", reply_markup=None)
    
async def handle_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # استخراج نوع و کد
    parts = query.data.split("_")
    if len(parts) < 3:
        await query.edit_message_text("❌ داده نامعتبر.")
        return
    
    design_type = parts[1]  # mobile یا sticker
    code = parts[2]

    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # انتخاب جدول‌های مناسب
        if design_type == 'mobile':
            table_pending = 'pending_designs'
            table_log = 'design_log'
            table_locked = 'locked_codes'
            table_uploaded = 'uploaded_files'
        else:  # sticker
            table_pending = 'pending_stickers'
            table_log = 'sticker_log'
            table_locked = 'locked_sticker_codes'
            table_uploaded = 'uploaded_sticker_files'
        
        cursor.execute(f"SELECT * FROM {table_pending} WHERE code = %s", (code,))
        row = cursor.fetchone()
        
        if not row:
            await query.edit_message_text("❌ این طرح قبلاً پردازش شده.")
            return

        # ساختار ردیف: id, code, designer_chat_id, mockup_file_ids, print_file_ids, mockup_message_ids_nazi, created_at
        _, code, designer_chat_id, mockup_file_ids_str, print_file_ids_str, mockup_message_ids_str, _ = row[:7]

        mockup_file_ids = json.loads(mockup_file_ids_str)
        print_file_ids = json.loads(print_file_ids_str)
        mockup_message_ids = json.loads(mockup_message_ids_str)

        type_label = "طرح" if design_type == 'mobile' else "استیکر"
        logging.info(f"[APPROVE] {type_label} Code: {code} | Mockups: {len(mockup_file_ids)} | Prints: {len(print_file_ids)}")

        # زمان فعلی تهران
        now_tehran = get_tehran_time()
        now_utc = to_utc_naive(now_tehran)

        total_mockups = len(mockup_file_ids)

        # ارسال موکاپ‌ها به گروه محصولات
        for i, fid in enumerate(mockup_file_ids, 1):
            if total_mockups == 1:
                caption = f"کد {type_label}: {code}"
            else:
                caption = f"کد {type_label}: {code} (موکاپ {i} از {total_mockups})"

            # دکمه "اپلود شد"
            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("📤 اپلود شد", callback_data=f"mark_uploaded_{design_type}_{code}_{i}")
            ]])

            try:
                if isinstance(fid, str) and (fid.startswith('AgAC') or fid.startswith('AQA')):
                    # برای عکس
                    sent_msg = await context.bot.send_photo(
                        chat_id=GROUP_PRODUCTS,
                        photo=fid,
                        caption=caption,
                        reply_markup=reply_markup
                    )
                    photo_file = await context.bot.get_file(sent_msg.photo[-1].file_id)
                    file_unique_id = photo_file.file_unique_id
                else:
                    # برای document
                    sent_msg = await context.bot.send_document(
                        chat_id=GROUP_PRODUCTS,
                        document=fid,
                        caption=caption,
                        reply_markup=reply_markup
                    )
                    doc_file = await context.bot.get_file(fid)
                    file_unique_id = doc_file.file_unique_id

                # ذخیره در جدول مناسب
                cursor.execute(f"""
                    INSERT INTO {table_uploaded} 
                    (file_unique_id, code, message_id_in_group, status, created_at)
                    VALUES (%s, %s, %s, 'pending', %s)
                """, (file_unique_id, code, sent_msg.message_id, now_utc))
                
            except Exception as e:
                logging.error(f"Error sending mockup {i} for code {code}: {e}")
                continue
                
        # ارسال فایل‌های چاپی با نام صحیح
        unique_print_file_ids = list(dict.fromkeys(print_file_ids))
        total_prints = len(unique_print_file_ids)
        
        for i, fid in enumerate(unique_print_file_ids, 1):
            try:
                # دانلود فایل
                file = await context.bot.get_file(fid)
                file_bytes = await file.download_as_bytearray()
                
                # تشخیص پسوند اصلی
                if '.' in file.file_path:
                    original_ext = file.file_path.split('.')[-1].lower()
                else:
                    original_ext = 'png'
                
                # تعیین نام فایل جدید
                if total_prints == 1:
                    new_filename = f"{code}.{original_ext}"
                else:
                    new_filename = f"{code}_{i}.{original_ext}"
                
                # ارسال با نام جدید
                await context.bot.send_document(
                    chat_id=GROUP_PRINT,
                    document=InputFile(BytesIO(file_bytes), filename=new_filename)
                )
                
                logging.info(f"✅ Sent print file: {new_filename}")
                
            except Exception as e:
                logging.error(f"❌ Error sending print file {i}: {e}")
                await context.bot.send_document(GROUP_PRINT, document=fid)

        # پاک کردن پیام‌ها از PV نازی
        await delete_messages(context.bot, NAZI_CHAT_ID, mockup_message_ids)

        # نوتیفیکیشن به علیرضا
        await context.bot.send_message(
            chat_id=designer_chat_id,
            text=f"🟢 {type_label} {code} تایید و ارسال شد!\n"
                 f"📦 {len(mockup_file_ids)} موکاپ + {total_prints} فایل چاپی"
        )

        # ذخیره در لاگ و قفل کد با زمان تهران
        cursor.execute(f"""
            INSERT INTO {table_log} (code, final_name, status, designer_chat_id, created_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (code, '', 'approved', designer_chat_id, now_utc))

        cursor.execute(
            f"INSERT IGNORE INTO {table_locked} (code, locked_at) VALUES (%s, %s)", 
            (code, now_utc)
        )
        cursor.execute(f"DELETE FROM {table_pending} WHERE code = %s", (code,))
        conn.commit()

        # ویرایش پیام تایید
        try:
            if hasattr(query.message, 'text'):
                await query.edit_message_text(f"✅ {type_label} {code} تایید و ارسال شد.")
            elif hasattr(query.message, 'caption'):
                await query.edit_message_caption(caption=f"✅ {type_label} {code} تایید و ارسال شد.")
            else:
                await context.bot.send_message(
                    chat_id=NAZI_CHAT_ID,
                    text=f"✅ {type_label} {code} تایید و ارسال شد."
                )
        except Exception as e:
            logging.debug(f"Could not edit approval message: {e}")

    except json.JSONDecodeError as e:
        logging.error(f"JSON Decode Error: {e}")
        await query.edit_message_text("❌ خطایی در خواندن فایل‌ها رخ داد.")
    except Exception as e:
        logging.error(f"Approve Error: {e}")
        conn.rollback()
        await query.edit_message_text("❌ خطایی در تایید رخ داد.")
    finally:
        cursor.close()
        conn.close()
        
async def handle_revision_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        return

    replied_msg = update.message.reply_to_message
    revision_text = update.message.text.strip()
    if not revision_text:
        return

    code = None
    design_type = None

    # چک کردن caption
    if hasattr(replied_msg, 'caption') and replied_msg.caption:
        import re
        # شناسایی طرح موبایل
        match = re.search(r'کد\s*طرح[:\s]*(TS\d+)', replied_msg.caption)
        if match:
            code = match.group(1)
            design_type = 'mobile'
        else:
            # شناسایی استیکر
            match = re.search(r'کد\s*استیکر[:\s]*(STI\d+)', replied_msg.caption)
            if match:
                code = match.group(1)
                design_type = 'sticker'

    if not code or not design_type:
        await update.message.reply_text(
            "❌ نمی‌تونم کد رو تشخیص بدم.\n"
            "لطفاً روی هر کدوم از موکاپ‌ها ریپلای کنید.",
            parse_mode="Markdown"
        )
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        table_name = 'pending_designs' if design_type == 'mobile' else 'pending_stickers'
        
        cursor.execute(f"SELECT designer_chat_id, mockup_message_ids_nazi FROM {table_name} WHERE code = %s", (code,))
        row = cursor.fetchone()
        
        if not row:
            await update.message.reply_text("❌ طرحی با این کد یافت نشد یا قبلاً پردازش شده.")
            return

        designer_chat_id, mockup_message_ids_str = row
        mockup_message_ids = json.loads(mockup_message_ids_str)

        # پاک کردن همه پیام‌ها
        await delete_messages(context.bot, NAZI_CHAT_ID, mockup_message_ids)

        # پاک کردن پیام اصلاحیه و ریپلای
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=replied_msg.message_id)
        except:
            pass

        # پاک کردن از دیتابیس
        cursor.execute(f"DELETE FROM {table_name} WHERE code = %s", (code,))
        conn.commit()

        type_label = "طرح" if design_type == 'mobile' else "استیکر"
        # نوتیفیکیشن به علیرضا
        await context.bot.send_message(
            designer_chat_id,
            f"🔧 {type_label} {code} نیاز به اصلاحیه دارد:\n«{revision_text}»\n\n"
            "لطفاً اصلاحیه رو انجام بده و دوباره ثبت کن."
        )

    except json.JSONDecodeError as e:
        logging.error(f"JSON Decode Error in revision: {e}")
        await update.message.reply_text("❌ خطایی در خواندن داده‌ها رخ داد.")
    except Exception as e:
        logging.error(f"Revision Error: {e}")
        conn.rollback()
        await update.message.reply_text("❌ مشکلی در ثبت اصلاحیه پیش آمد.")
    finally:
        cursor.close()
        conn.close()
        
async def handle_undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # استخراج نوع و کد
    parts = query.data.split("_")
    if len(parts) < 3:
        await query.edit_message_text("❌ داده نامعتبر.")
        return
    
    design_type = parts[1]  # mobile یا sticker
    code = parts[2]
    user_id = query.from_user.id

    if user_id != DESIGNER_CHAT_ID:
        await query.edit_message_text("🚫 فقط علیرضا می‌تونه لغو کنه!")
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        table_name = 'pending_designs' if design_type == 'mobile' else 'pending_stickers'
        
        cursor.execute(f"SELECT mockup_message_ids_nazi FROM {table_name} WHERE code = %s", (code,))
        row = cursor.fetchone()
        
        if not row:
            await query.edit_message_text("❌ این طرح قبلاً پردازش شده یا وجود نداره.")
            return

        mockup_message_ids_str = row[0]
        mockup_message_ids = json.loads(mockup_message_ids_str)
        
        # پاک کردن همه پیام‌ها
        await delete_messages(context.bot, NAZI_CHAT_ID, mockup_message_ids)

        cursor.execute(f"DELETE FROM {table_name} WHERE code = %s", (code,))
        conn.commit()

        type_label = "طرح" if design_type == 'mobile' else "استیکر"
        await query.edit_message_text(f"↩️ {type_label} {code} با موفقیت لغو و حذف شد.\nکد آزاد شد.")

    except json.JSONDecodeError as e:
        logging.error(f"JSON Decode Error in undo: {e}")
        await query.edit_message_text("❌ خطایی در خواندن داده‌ها رخ داد.")
    except Exception as e:
        logging.error(f"Undo Error: {e}")
        conn.rollback()
        await query.edit_message_text("❌ مشکلی در لغو پیش آمد.")
    finally:
        cursor.close()
        conn.close()
        
def backup_database():
    try:
        # تنظیمات mysqldump — مسیرش رو باید مطابق سرورت تنظیم کنی
        dump_cmd = [
            'mysqldump',
            '-h', DB_CONFIG['host'],
            '-u', DB_CONFIG['user'],
            f"-p{DB_CONFIG['password']}",
            DB_CONFIG['database'],
            '--single-transaction',
            '--routines',
            '--triggers'
        ]

        result = subprocess.run(dump_cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except Exception as e:
        logging.error(f"Backup failed: {e}")
        return None
        
async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id != DESIGNER_CHAT_ID:
        await update.message.reply_text("🚫 فقط علیرضا دسترسی به بکاپ داره!")
        return

    try:
        # ۱. چک کردن وجود و اندازه فایل سورس
        source_file = __file__
        
        if not os.path.exists(source_file):
            await update.message.reply_text("❌ فایل سورس کد یافت نشد!")
            logging.error(f"Source file not found: {source_file}")
            return
        
        file_size = os.path.getsize(source_file)
        
        if file_size == 0:
            await update.message.reply_text("❌ فایل سورس کد خالی است!")
            logging.error(f"Source file is empty: {source_file}")
            return
        
        logging.info(f"📄 Source file: {source_file} ({file_size} bytes)")
        
        # ارسال سورس کد
        with open(source_file, 'rb') as f:
            await context.bot.send_document(
                chat_id=chat_id,
                document=f,
                filename="tisa_print_bot_source.py",
                caption=f"📄 سورس کد فعلی ربات ({file_size} bytes)"
            )

        # ۲. گرفتن بکاپ دیتابیس
        await update.message.reply_text("⏳ در حال گرفتن بکاپ دیتابیس...")
        
        backup_sql = backup_database()
        if not backup_sql:
            await update.message.reply_text("❌ خطایی در گرفتن بکاپ دیتابیس رخ داد.")
            return
        
        if not backup_sql.strip():
            await update.message.reply_text("❌ بکاپ دیتابیس خالی است!")
            logging.error("Database backup is empty")
            return

        # تولید نام فایل با زمان تهران
        now_tehran = get_tehran_time()
        timestamp = now_tehran.strftime('%Y%m%d_%H%M%S')
        backup_filename = f"tisa_backup_{timestamp}.sql"

        # ذخیره در فایل موقت
        backup_path = f"tisa_backup_{timestamp}.sql"
        
        with open(backup_path, 'w', encoding='utf8') as f:
            f.write(backup_sql)
        
        # چک اندازه فایل بکاپ
        backup_size = os.path.getsize(backup_path)
        logging.info(f"💾 Backup file: {backup_path} ({backup_size} bytes)")
        
        if backup_size == 0:
            await update.message.reply_text("❌ فایل بکاپ خالی است!")
            os.remove(backup_path)
            return

        # ارسال فایل
        with open(backup_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=backup_filename,
                caption=f"💾 بکاپ کامل دیتابیس ({backup_size} bytes)"
            )

        # پاک کردن فایل موقت
        os.remove(backup_path)

        logging.info(f"✅ Backup sent to user {chat_id}")
        await update.message.reply_text("✅ بکاپ با موفقیت ارسال شد!")

    except Exception as e:
        logging.error(f"Backup Command Error: {e}", exc_info=True)
        await update.message.reply_text(f"❌ مشکلی در ارسال بکاپ پیش آمد:\n{str(e)[:200]}")
        
async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id != DESIGNER_CHAT_ID:
        await update.message.reply_text("🚫 فقط علیرضا می‌تونه بازیابی کنه!")
        return

    restore_pending[chat_id] = True
    await update.message.reply_text(
        "⚠️ هشدار: بازیابی دیتابیس *همه داده‌های فعلی* رو پاک می‌کنه!\n"
        "لطفاً یک فایل `.sql` معتبر بفرستید.\n"
        "برای لغو، /cancel رو بفرستید.",
        parse_mode="Markdown"
    )

async def handle_restore_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id not in restore_pending:
        return

    if not update.message.document:
        await update.message.reply_text("❌ لطفاً یک فایل .sql بفرستید.")
        return

    if not update.message.document.file_name.endswith('.sql'):
        await update.message.reply_text("❌ فقط فایل‌های .sql قابل قبول هستند.")
        return

    try:
        # دانلود فایل
        file = await context.bot.get_file(update.message.document.file_id)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sql") as tmp_file:
            await file.download_to_drive(tmp_file.name)
            sql_path = tmp_file.name

        # 🆕 تولید کلید کوتاه (8 کاراکتر)
        import uuid
        file_key = str(uuid.uuid4())[:8]
        
        # 🆕 ذخیره مسیر فایل با کلید
        restore_files[file_key] = sql_path

        # تأیید نهایی
        await update.message.reply_text(
            "🚨 مطمئنی می‌خوای *همه داده‌های فعلی* رو با این بکاپ جایگزین کنی؟\n"
            "این عمل *غیرقابل بازگشت* هست!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ بله، بازیابی کن!", callback_data=f"restore_{file_key}"),  # 🆕 کلید کوتاه
                InlineKeyboardButton("❌ انصراف", callback_data="cancel_restore")
            ]])
        )

    except Exception as e:
        logging.error(f"Restore File Download Error: {e}")
        await update.message.reply_text("❌ خطایی در دانلود فایل رخ داد.")

async def confirm_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.from_user.id

    if chat_id != DESIGNER_CHAT_ID or chat_id not in restore_pending:
        await query.edit_message_text("🚫 دسترسی غیرمجاز.")
        return

    # 🆕 استخراج کلید کوتاه
    try:
        file_key = query.data.split("restore_", 1)[1]
    except IndexError:
        await query.edit_message_text("❌ خطا در شناسایی فایل.")
        return

    # 🆕 بازیابی مسیر فایل از دیکشنری
    if file_key not in restore_files:
        await query.edit_message_text("❌ فایل بکاپ یافت نشد یا منقضی شده.")
        return

    sql_path = restore_files[file_key]

    # بررسی وجود فایل
    if not os.path.exists(sql_path):
        await query.edit_message_text("❌ فایل بکاپ حذف شده است.")
        del restore_files[file_key]
        return

    try:
        await query.edit_message_text("⏳ در حال بازیابی دیتابیس... لطفاً صبر کنید.")

        # اجرای دستور mysql
        restore_cmd = [
            'mysql',
            '-h', DB_CONFIG['host'],
            '-u', DB_CONFIG['user'],
            f"-p{DB_CONFIG['password']}",
            DB_CONFIG['database']
        ]

        with open(sql_path, 'r', encoding='utf8') as f:
            result = subprocess.run(
                restore_cmd, 
                stdin=f, 
                capture_output=True, 
                text=True, 
                check=True,
                timeout=300  # 🆕 تایم‌اوت 5 دقیقه
            )

        # پاک کردن فایل موقت
        try:
            os.remove(sql_path)
        except:
            pass

        # پاک کردن از دیکشنری
        del restore_files[file_key]
        del restore_pending[chat_id]

        logging.info(f"✅ Database restored by user {chat_id}")
        
        await query.edit_message_text(
            "✅ دیتابیس با موفقیت بازیابی شد!\n\n"
            "⚠️ توصیه می‌شه ربات رو ریستارت کنی تا تغییرات اعمال بشن.",
            reply_markup=None
        )

    except subprocess.TimeoutExpired:
        logging.error(f"Restore timeout for user {chat_id}")
        await query.edit_message_text("❌ بازیابی به دلیل طولانی بودن فایل متوقف شد.")
    except subprocess.CalledProcessError as e:
        logging.error(f"Restore MySQL Error: {e.stderr}")
        await query.edit_message_text(
            f"❌ خطا در بازیابی دیتابیس:\n```\n{e.stderr[:500]}\n```",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Restore Error: {e}")
        await query.edit_message_text(f"❌ خطا در بازیابی:\n{str(e)[:200]}")
    finally:
        # پاکسازی در صورت خطا
        if file_key in restore_files:
            try:
                os.remove(restore_files[file_key])
            except:
                pass
            del restore_files[file_key]
       
async def cancel_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.from_user.id

    # 🆕 پاک کردن فایل موقت در صورت لغو
    try:
        callback_data = query.data
        if callback_data != "cancel_restore":
            file_key = callback_data.split("cancel_restore_", 1)[1]
            if file_key in restore_files:
                try:
                    os.remove(restore_files[file_key])
                except:
                    pass
                del restore_files[file_key]
    except:
        pass

    if chat_id in restore_pending:
        del restore_pending[chat_id]

    await query.edit_message_text("👌 بازیابی لغو شد.", reply_markup=None)
    
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id in restore_pending:
        del restore_pending[chat_id]
        await update.message.reply_text("👌 فرآیند بازیابی لغو شد.")
    else:
        await update.message.reply_text("ℹ️ هیچ فرآیند بازیابی فعالی وجود ندارد.")
              
async def delete_messages(bot, chat_id, message_ids):
    for msg_id in message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            logging.warning(f"Failed to delete message {msg_id}: {e}")
        
async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id != NAZI_CHAT_ID and chat_id != DESIGNER_CHAT_ID:
        await update.message.reply_text("🚫 فقط نازی و علیرضا می‌تونن ادمین اضافه کنن.")
        return

    if not context.args or len(context.args) != 1:
        await update.message.reply_text("Usage: /promote <chat_id>")
        return

    try:
        target_chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ chat_id باید عدد باشه.")
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        name = f"{target_chat_id}"

        try:
            user = await context.bot.get_chat(target_chat_id)
            if user.full_name and user.full_name.strip():
                name = user.full_name.strip()
            elif user.first_name and user.first_name.strip():
                name = user.first_name.strip()
        except Exception as e:
            logging.warning(f"Could not fetch user info for {target_chat_id}: {e}")

        # 🆕 دریافت زمان تهران
        now_utc = to_utc_naive(get_tehran_time())

        # 🆕 اضافه کردن promoted_at
        cursor.execute(
            "INSERT INTO upload_admins (chat_id, name, promoted_by, promoted_at) VALUES (%s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE name = %s",
            (target_chat_id, name, chat_id, now_utc, name)
        )
        conn.commit()

        safe_name = escape_markdown(name, version=2)

        message_text = (
            f"✅ کاربر [{safe_name}](tg://user?id={target_chat_id}) "
            f"به عنوان ادمین اپلود اضافه شد\\!"
        )

        await update.message.reply_text(
            message_text,
            parse_mode="MarkdownV2"
        )

    except Exception as e:
        logging.error(f"Promote Error: {e}")
        await update.message.reply_text("❌ خطایی در ارتقا کاربر رخ داد.")
    finally:
        cursor.close()
        conn.close()
        
async def demote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id != NAZI_CHAT_ID and chat_id != DESIGNER_CHAT_ID:
        await update.message.reply_text("🚫 فقط نازی و علیرضا می‌تونن ادمین رو حذف کنن.")
        return

    if not context.args or len(context.args) != 1:
        await update.message.reply_text("UsageId: /demote <chat_id>")
        return

    try:
        target_chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ chat_id باید عدد باشه.")
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("DELETE FROM upload_admins WHERE chat_id = %s", (target_chat_id,))
        if cursor.rowcount == 0:
            await update.message.reply_text(f"❌ کاربر {target_chat_id} در لیست ادمین‌ها وجود ندارد.")
        else:
            # 🛠️ رفع خطا: از parse_mode استفاده نکن برای عدد خام
            await update.message.reply_text(
                f"✅ کاربر {target_chat_id} از لیست ادمین‌های اپلود حذف شد."
                # parse_mode حذف شد تا از خطا جلوگیری بشه
            )
        conn.commit()
    except Exception as e:
        logging.error(f"Demote Error: {e}")
        await update.message.reply_text("❌ خطایی در حذف کاربر رخ داد.")
    finally:
        cursor.close()
        conn.close()
        
async def admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id != NAZI_CHAT_ID and chat_id != DESIGNER_CHAT_ID:
        await update.message.reply_text("🚫 فقط نازی و علیرضا دسترسی دارند.")
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT chat_id, name, promoted_by, promoted_at FROM upload_admins ORDER BY promoted_at DESC")
        rows = cursor.fetchall()

        if not rows:
            await update.message.reply_text("📭 هیچ ادمین اپلودی وجود نداره.")
            return

        text = "📋 *لیست ادمین‌های اپلود:*\n\n"
        for row in rows:
            admin_chat_id, name, promoted_by, promoted_at = row
            
            # تبدیل UTC به تهران
            promoted_at_tehran = get_tehran_time(promoted_at)
            time_str = promoted_at_tehran.strftime('%Y-%m-%d %H:%M')
            
            text += f"👤 [{name}](tg://user?id={admin_chat_id})\n" \
                    f"🆔 `{admin_chat_id}`\n" \
                    f"📅 {time_str}\n\n"

        await update.message.reply_text(text, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Admins List Error: {e}")
        await update.message.reply_text("❌ خطایی در دریافت لیست ادمین‌ها رخ داد.")
    finally:
        cursor.close()
        conn.close()
        
async def check_admin_permission(user_chat_id):
    """چک کردن دسترسی با cache"""
    if user_chat_id == NAZI_CHAT_ID or user_chat_id == DESIGNER_CHAT_ID:
        return True
        
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1 FROM upload_admins WHERE chat_id = %s", (user_chat_id,))
        return cursor.fetchone() is not None
    finally:
        cursor.close()
        conn.close()

async def cleanup_admin_cache(context: ContextTypes.DEFAULT_TYPE):
    current_time = time.time()
    expired_keys = [k for k, v in admin_cache_time.items() if current_time - v > 3600]  # 1 hour
    for key in expired_keys:
        admin_cache.pop(key, None)
        admin_cache_time.pop(key, None)
        
async def mark_uploaded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # استخراج نوع، کد و ایندکس
    parts = query.data.split("_", 4)
    if len(parts) < 5:
        await query.answer("❌ داده نامعتبر.", show_alert=True)
        return

    design_type = parts[2]  # mobile یا sticker
    code = parts[3]
    part_index = parts[4]

    user_chat_id = query.from_user.id
    user_name = query.from_user.full_name or f"User {user_chat_id}"

    # چک کردن دسترسی
    allowed = False
    if user_chat_id in admin_cache:
        allowed = admin_cache[user_chat_id]
    else:
        allowed = await check_admin_permission(user_chat_id)
        admin_cache[user_chat_id] = allowed
        admin_cache_time[user_chat_id] = time.time()

    if not allowed:
        await query.answer("🚫 شما مجاز به ثبت اپلود نیستید.", show_alert=True)
        return
    
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # کاراکتر کنترلی برای جلوگیری از جابجایی اعداد در فارسی
        LRM = '\u200E'
        
        # انتخاب جدول مناسب
        table_name = 'uploaded_files' if design_type == 'mobile' else 'uploaded_sticker_files'
        
        # پیدا کردن فایل بر اساس message_id
        message_id = query.message.message_id
        
        cursor.execute(f"""
            SELECT file_unique_id, status 
            FROM {table_name} 
            WHERE message_id_in_group = %s AND code = %s
        """, (message_id, code))
        
        row = cursor.fetchone()
        if not row:
            await query.answer("❌ اطلاعات فایل یافت نشد.", show_alert=True)
            return
            
        file_unique_id, status = row
        
        if status == 'uploaded':
            await query.answer("✅ این فایل قبلاً اپلود شده.", show_alert=True)
            return

        # دریافت زمان تهران
        now_tehran = get_tehran_time()
        now_utc = to_utc_naive(now_tehran)
        
        # آپدیت وضعیت فایل با زمان UTC
        cursor.execute(f"""
            UPDATE {table_name} 
            SET status = 'uploaded', 
                uploaded_by_chat_id = %s, 
                uploaded_by_name = %s,
                uploaded_at = %s
            WHERE message_id_in_group = %s AND code = %s
        """, (user_chat_id, user_name, now_utc, message_id, code))

        cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE code = %s", (code,))
        total_count = cursor.fetchone()[0]
        
        # نمایش زمان تهران
        time_str = now_tehran.strftime('%H:%M')
        
        type_label = "طرح" if design_type == 'mobile' else "استیکر"
        
        if total_count == 1:
            new_caption = f"کد {type_label}: {LRM}{code}{LRM}\n✅ اپلود شد | توسط: {user_name}\nزمان: {LRM}{time_str}{LRM}"
        else:
            new_caption = f"کد {type_label}: {LRM}{code}{LRM} (موکاپ {LRM}{part_index}{LRM} از {LRM}{total_count}{LRM})\n✅ اپلود شد | توسط: {user_name}\nزمان: {LRM}{time_str}{LRM}"

        try:
            await query.edit_message_caption(caption=new_caption)
        except Exception as e:
            logging.warning(f"Failed to edit caption: {e}")

        # آپدیت دکمه (غیرفعال کردن)
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ اپلود شده", callback_data="noop")
                ]])
            )
        except Exception as e:
            logging.warning(f"Failed to edit inline button: {e}")

        conn.commit()
        
        # چک کردن آیا همه فایل‌های این کد اپلود شده‌اند
        cursor.execute(f"""
            SELECT COUNT(*) 
            FROM {table_name} 
            WHERE code = %s AND status = 'pending'
        """, (code,))
        pending_count = cursor.fetchone()[0]
        
        if pending_count == 0:
            # همه فایل‌ها اپلود شده، نوتیف به نازی و علیرضا
            completion_text = f"✅ همه فایل‌های کد *{LRM}{code}{LRM}* ({type_label}) اپلود شد!\n⏰ زمان: {LRM}{time_str}{LRM}"
            
            await context.bot.send_message(
                chat_id=NAZI_CHAT_ID,
                text=completion_text,
                parse_mode="Markdown"
            )
            await context.bot.send_message(
                chat_id=DESIGNER_CHAT_ID,
                text=completion_text,
                parse_mode="Markdown"
            )
            
            logging.info(f"All files for code {code} ({design_type}) have been uploaded")
        
        await query.answer("✅ فایل با موفقیت ثبت شد.", show_alert=False)

    except Exception as e:
        logging.error(f"Mark Uploaded Error: {e}")
        conn.rollback()
        await query.answer("❌ خطایی در ثبت اپلود رخ داد.", show_alert=True)
    finally:
        cursor.close()
        conn.close()
        
def get_tehran_time(dt=None):
    """
    تبدیل زمان به تایم‌زون تهران
    
    Args:
        dt: datetime object (می‌تواند None، naive UTC، یا aware باشد)
    
    Returns:
        datetime object با timezone تهران
    """
    if dt is None:
        # زمان فعلی تهران
        return datetime.now(TEHRAN_TZ)
    
    # اگر naive datetime باشد (از MySQL)، فرض کن UTC است
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)
    
    # تبدیل به تهران
    return dt.astimezone(TEHRAN_TZ)

def to_utc_naive(dt_tehran):
    """
    تبدیل زمان تهران به UTC naive برای ذخیره در MySQL
    
    Args:
        dt_tehran: datetime object (می‌تواند naive تهران یا aware باشد)
    
    Returns:
        naive datetime در UTC (بدون timezone info)
    """
    # اگر naive باشد، فرض کن تهران است
    if dt_tehran.tzinfo is None:
        dt_tehran = TEHRAN_TZ.localize(dt_tehran)
    
    # تبدیل به UTC و حذف timezone info
    return dt_tehran.astimezone(pytz.UTC).replace(tzinfo=None)
    
async def uploads_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id != NAZI_CHAT_ID and chat_id != DESIGNER_CHAT_ID:
        await update.message.reply_text("🚫 فقط نازی و علیرضا دسترسی دارند.")
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # کاراکتر کنترلی برای جلوگیری از جابجایی اعداد در فارسی
        LRM = '\u200E'
        
        # محاسبه 24 ساعت گذشته به وقت تهران (فقط برای لیست)
        now_tehran = get_tehran_time()
        yesterday_tehran = now_tehran - timedelta(hours=24)
        yesterday_utc = to_utc_naive(yesterday_tehran)

        cursor.execute("""
            SELECT code, COUNT(*) as total_count
            FROM uploaded_files
            GROUP BY code
        """)
        code_counts = {row[0]: row[1] for row in cursor.fetchall()}

        # 📋 لیست فایل‌های اپلود شده در 24 ساعت اخیر (فقط برای نمایش)
        cursor.execute("""
            SELECT code, uploaded_by_name, uploaded_at, file_unique_id, id
            FROM uploaded_files 
            WHERE uploaded_at >= %s AND status = 'uploaded'
            ORDER BY uploaded_at DESC, code, id
        """, (yesterday_utc,))
        uploaded_rows_24h = cursor.fetchall()

        # گروه‌بندی لیست 24 ساعت
        uploaded_grouped = {}
        for row in uploaded_rows_24h:
            code, uploader, uploaded_at, file_unique_id, file_id = row
            if code not in uploaded_grouped:
                uploaded_grouped[code] = []
            
            cursor.execute("""
                SELECT COUNT(*) + 1 
                FROM uploaded_files 
                WHERE code = %s AND id < %s
            """, (code, file_id))
            part_number = cursor.fetchone()[0]
            
            uploaded_grouped[code].append({
                'uploader': uploader,
                'uploaded_at': uploaded_at,
                'part_number': part_number,
                'total_parts': code_counts.get(code, 1)
            })

        # 📊 جمع کل فایل‌های اپلود شده (همه، بدون محدودیت زمانی)
        cursor.execute("""
            SELECT COUNT(*) 
            FROM uploaded_files 
            WHERE status = 'uploaded'
        """)
        total_uploaded_all_time = cursor.fetchone()[0]

        # فایل‌های منتظر اپلود (همه)
        cursor.execute("""
            SELECT code, id, message_id_in_group
            FROM uploaded_files 
            WHERE status = 'pending'
            ORDER BY code, id
        """)
        pending_rows = cursor.fetchall()
        
        # گروه‌بندی pending
        pending_grouped = {}
        for row in pending_rows:
            code, file_id, message_id = row
            if code not in pending_grouped:
                pending_grouped[code] = []
            
            cursor.execute("""
                SELECT COUNT(*) + 1 
                FROM uploaded_files 
                WHERE code = %s AND id < %s
            """, (code, file_id))
            part_number = cursor.fetchone()[0]
            
            pending_grouped[code].append({
                'part_number': part_number,
                'total_parts': code_counts.get(code, 1)
            })

        # 👑 آمار ادمین‌ها (همه، بدون محدودیت زمانی)
        cursor.execute("""
            SELECT uploaded_by_name, COUNT(*) as upload_count
            FROM uploaded_files 
            WHERE status = 'uploaded'
            GROUP BY uploaded_by_name
            ORDER BY upload_count DESC
        """)
        admin_stats = cursor.fetchall()

        # ساخت متن گزارش
        text = "📊 *گزارش اپلودهای موبایل*\n\n"

        # 📋 فایل‌های اپلود شده (فقط 24 ساعت اخیر)
        text += "✅ *اپلود شده (24 ساعت اخیر):*\n"
        if uploaded_grouped:
            for code, uploads in uploaded_grouped.items():
                for upload in uploads:
                    if upload['uploaded_at']:
                        tehran_time = get_tehran_time(upload['uploaded_at'])
                        time_str = tehran_time.strftime('%H:%M')
                    else:
                        time_str = ''
                    
                    uploader = upload['uploader'] or 'Unknown'
                    
                    if upload['total_parts'] > 1:
                        text += f"▫️ {LRM}{code}{LRM} ({LRM}{upload['part_number']}{LRM}) — {uploader} | ({LRM}{time_str}{LRM})\n"
                    else:
                        text += f"▫️ {LRM}{code}{LRM} — {uploader} ({LRM}{time_str}{LRM})\n"
        else:
            text += "▫️ هیچ فایلی در 24 ساعت اخیر اپلود نشده.\n"

        # ⏳ فایل‌های در انتظار
        text += "\n⏳ *در انتظار اپلود:*\n"
        if pending_grouped:
            for code, pendings in pending_grouped.items():
                parts_str = ""
                if pendings[0]['total_parts'] > 1:
                    part_numbers = [str(p['part_number']) for p in pendings]
                    parts_list = ', '.join(part_numbers)
                    parts_str = f" ({LRM}{parts_list}{LRM})"
                
                text += f"▫️ {LRM}{code}{LRM}{parts_str}\n"
        else:
            text += "▫️ همه فایل‌ها اپلود شده‌اند.\n"

        # 📌 جمع کل (همه، بدون محدودیت زمانی)
        total_waiting = len(pending_rows)
        
        text += f"\n📌 *جمع کل:*\n" \
                f"▫️ اپلود شده: {LRM}{total_uploaded_all_time}{LRM} فایل\n" \
                f"▫️ منتظر اپلود: {LRM}{total_waiting}{LRM} فایل\n"

        # 👑 آمار ادمین‌ها (همه، بدون محدودیت ز��انی)
        if admin_stats:
            text += "\n👑 *آمار ادمین‌ها:*\n"
            for name, count in admin_stats:
                if name:
                    text += f"▫️ {name}: {LRM}{count}{LRM} فایل\n"

        # دکمه ریست آمار
        await update.message.reply_text(
            text, 
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ ریست آمار اپلودها", callback_data="confirm_reset_uploads_mobile")
            ]])
        )

    except Exception as e:
        logging.error(f"Uploads Report Error: {e}")
        await update.message.reply_text("❌ خطایی در تهیه گزارش رخ داد.")
    finally:
        cursor.close()
        conn.close()

async def uploads_sticker_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id != NAZI_CHAT_ID and chat_id != DESIGNER_CHAT_ID:
        await update.message.reply_text("🚫 فقط نازی و علیرضا دسترسی دارند.")
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # کاراکتر کنترلی برای جلوگیری از جابجایی اعداد در فارسی
        LRM = '\u200E'
        
        # محاسبه 24 ساعت گذشته به وقت تهران (فقط برای لیست)
        now_tehran = get_tehran_time()
        yesterday_tehran = now_tehran - timedelta(hours=24)
        yesterday_utc = to_utc_naive(yesterday_tehran)

        cursor.execute("""
            SELECT code, COUNT(*) as total_count
            FROM uploaded_sticker_files
            GROUP BY code
        """)
        code_counts = {row[0]: row[1] for row in cursor.fetchall()}

        # 📋 لیست فایل‌های اپلود شده در 24 ساعت اخیر (فقط برای نمایش)
        cursor.execute("""
            SELECT code, uploaded_by_name, uploaded_at, file_unique_id, id
            FROM uploaded_sticker_files 
            WHERE uploaded_at >= %s AND status = 'uploaded'
            ORDER BY uploaded_at DESC, code, id
        """, (yesterday_utc,))
        uploaded_rows_24h = cursor.fetchall()

        # گروه‌بندی لیست 24 ساعت
        uploaded_grouped = {}
        for row in uploaded_rows_24h:
            code, uploader, uploaded_at, file_unique_id, file_id = row
            if code not in uploaded_grouped:
                uploaded_grouped[code] = []
            
            cursor.execute("""
                SELECT COUNT(*) + 1 
                FROM uploaded_sticker_files 
                WHERE code = %s AND id < %s
            """, (code, file_id))
            part_number = cursor.fetchone()[0]
            
            uploaded_grouped[code].append({
                'uploader': uploader,
                'uploaded_at': uploaded_at,
                'part_number': part_number,
                'total_parts': code_counts.get(code, 1)
            })

        # 📊 جمع کل فایل‌های اپلود شده (همه، بدون محدودیت زمانی)
        cursor.execute("""
            SELECT COUNT(*) 
            FROM uploaded_sticker_files 
            WHERE status = 'uploaded'
        """)
        total_uploaded_all_time = cursor.fetchone()[0]

        # فایل‌های منتظر اپلود (همه)
        cursor.execute("""
            SELECT code, id, message_id_in_group
            FROM uploaded_sticker_files 
            WHERE status = 'pending'
            ORDER BY code, id
        """)
        pending_rows = cursor.fetchall()
        
        # گروه‌بندی pending
        pending_grouped = {}
        for row in pending_rows:
            code, file_id, message_id = row
            if code not in pending_grouped:
                pending_grouped[code] = []
            
            cursor.execute("""
                SELECT COUNT(*) + 1 
                FROM uploaded_sticker_files 
                WHERE code = %s AND id < %s
            """, (code, file_id))
            part_number = cursor.fetchone()[0]
            
            pending_grouped[code].append({
                'part_number': part_number,
                'total_parts': code_counts.get(code, 1)
            })

        # 👑 آمار ادمین‌ها (همه، بدون محدودیت زمانی)
        cursor.execute("""
            SELECT uploaded_by_name, COUNT(*) as upload_count
            FROM uploaded_sticker_files 
            WHERE status = 'uploaded'
            GROUP BY uploaded_by_name
            ORDER BY upload_count DESC
        """)
        admin_stats = cursor.fetchall()

        # ساخت متن گزارش
        text = "📊 *گزارش اپلودهای استیکر*\n\n"

        # 📋 فایل‌های اپلود شده (فقط 24 ساعت اخیر)
        text += "✅ *اپلود شده (24 ساعت اخیر):*\n"
        if uploaded_grouped:
            for code, uploads in uploaded_grouped.items():
                for upload in uploads:
                    if upload['uploaded_at']:
                        tehran_time = get_tehran_time(upload['uploaded_at'])
                        time_str = tehran_time.strftime('%H:%M')
                    else:
                        time_str = ''
                    
                    uploader = upload['uploader'] or 'Unknown'
                    
                    if upload['total_parts'] > 1:
                        text += f"▫️ {LRM}{code}{LRM} ({LRM}{upload['part_number']}{LRM}) — {uploader} | ({LRM}{time_str}{LRM})\n"
                    else:
                        text += f"▫️ {LRM}{code}{LRM} — {uploader} ({LRM}{time_str}{LRM})\n"
        else:
            text += "▫️ هیچ فایلی در 24 ساعت اخیر اپلود نشده.\n"

        # ⏳ فایل‌های در انتظار
        text += "\n⏳ *در انتظار اپلود:*\n"
        if pending_grouped:
            for code, pendings in pending_grouped.items():
                parts_str = ""
                if pendings[0]['total_parts'] > 1:
                    part_numbers = [str(p['part_number']) for p in pendings]
                    parts_list = ', '.join(part_numbers)
                    parts_str = f" ({LRM}{parts_list}{LRM})"
                
                text += f"▫️ {LRM}{code}{LRM}{parts_str}\n"
        else:
            text += "▫️ همه فایل‌ها اپلود شده‌اند.\n"

        # 📌 جمع کل (همه، بدون محدودیت زمانی)
        total_waiting = len(pending_rows)
        
        text += f"\n📌 *جمع کل:*\n" \
                f"▫️ اپلود شده: {LRM}{total_uploaded_all_time}{LRM} فایل\n" \
                f"▫️ منتظر اپلود: {LRM}{total_waiting}{LRM} فایل\n"

        # 👑 آمار ادمین‌ها (همه، بدون محدودیت زمانی)
        if admin_stats:
            text += "\n👑 *آمار ادمین‌ها:*\n"
            for name, count in admin_stats:
                if name:
                    text += f"▫️ {name}: {LRM}{count}{LRM} فایل\n"

        # دکمه ریست آمار
        await update.message.reply_text(
            text, 
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ ریست آمار اپلودها", callback_data="confirm_reset_uploads_sticker")
            ]])
        )

    except Exception as e:
        logging.error(f"Uploads Sticker Report Error: {e}")
        await update.message.reply_text("❌ خطایی در تهیه گزارش رخ داد.")
    finally:
        cursor.close()
        conn.close()
         
async def confirm_reset_uploads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.from_user.id

    if chat_id != DESIGNER_CHAT_ID and chat_id != NAZI_CHAT_ID:
        await query.edit_message_text("🚫 دسترسی غیرمجاز.")
        return

    # تشخیص نوع
    design_type = 'mobile' if 'mobile' in query.data else 'sticker'
    type_label = "موبایل" if design_type == 'mobile' else "استیکر"

    await query.edit_message_text(
        text=f"⚠️ مطمئنی می‌خوای *فایل‌های اپلود شده {type_label}* رو پاک کنی؟\n\n"
             "این عمل:\n"
             "✅ فایل‌های اپلود شده رو حذف می‌کنه\n"
             "✅ آمار ادمین‌ها پاک می‌شه\n"
             "❌ فایل‌های در انتظار اپلود حفظ می‌شن\n\n"
             "*این عمل غیرقابل بازگشت است!*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ بله، پاک شه!", callback_data=f"do_reset_uploads_{design_type}"),
                InlineKeyboardButton("❌ انصراف", callback_data="cancel_reset_uploads")
            ]
        ])
    )

async def do_reset_uploads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.from_user.id

    if chat_id != DESIGNER_CHAT_ID and chat_id != NAZI_CHAT_ID:
        await query.edit_message_text("🚫 دسترسی غیرمجاز.")
        return

    # تشخیص نوع
    design_type = 'mobile' if 'mobile' in query.data else 'sticker'
    table_name = 'uploaded_files' if design_type == 'mobile' else 'uploaded_sticker_files'
    type_label = "موبایل" if design_type == 'mobile' else "استیکر"

    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # حذف فقط فایل‌های اپلود شده (status = 'uploaded')
        cursor.execute(f"DELETE FROM {table_name} WHERE status = 'uploaded'")
        deleted_count = cursor.rowcount
        conn.commit()

        logging.info(f"📊 آمار اپلودهای {type_label} توسط کاربر {chat_id} ریست شد. ({deleted_count} فایل حذف شد)")

        await query.edit_message_text(
            f"✅ آمار اپلودهای {type_label} با موفقیت ریست شد!\n\n"
            f"🗑️ {deleted_count} فایل اپلود شده پاک شد.\n"
            f"✅ فایل‌های در انتظار اپلود حفظ شدند.",
            reply_markup=None
        )

    except Exception as e:
        logging.error(f"Reset Uploads Error: {e}")
        conn.rollback()
        await query.edit_message_text("❌ خطایی در ریست آمار رخ داد.")
    finally:
        cursor.close()
        conn.close()

async def cancel_reset_uploads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("👌 عملیات ریست لغو شد.", reply_markup=None)
    
async def check_overdue_files(context: ContextTypes.DEFAULT_TYPE):
    """بررسی فایل‌های معوقه و ارسال نوتیفیکیشن"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # کاراکتر کنترلی برای جلوگیری از جابجایی اعداد در فارسی
        LRM = '\u200E'
        
        # محاسبه 24 ساعت گذشته به وقت تهران
        now_tehran = get_tehran_time()
        yesterday_tehran = now_tehran - timedelta(hours=24)
        yesterday_utc = to_utc_naive(yesterday_tehran)
        
        # فایل‌های موبایل که بیش از 24 ساعت منتظر هستند
        cursor.execute("""
            SELECT code, COUNT(*) as count, MIN(created_at) as first_created,
                   GROUP_CONCAT(id ORDER BY id) as part_ids
            FROM uploaded_files
            WHERE status = 'pending' 
            AND created_at <= %s
            GROUP BY code
            ORDER BY first_created
        """, (yesterday_utc,))
        overdue_mobile_files = cursor.fetchall()
        
        # فایل‌های استیکر که بیش از 24 ساعت منتظر هستند
        cursor.execute("""
            SELECT code, COUNT(*) as count, MIN(created_at) as first_created,
                   GROUP_CONCAT(id ORDER BY id) as part_ids
            FROM uploaded_sticker_files
            WHERE status = 'pending' 
            AND created_at <= %s
            GROUP BY code
            ORDER BY first_created
        """, (yesterday_utc,))
        overdue_sticker_files = cursor.fetchall()
        
        if overdue_mobile_files or overdue_sticker_files:
            # ساخت پیام نوتیفیکیشن
            text = "⚠️ *هشدار: فایل‌های معوقه*\n\n"
            
            if overdue_mobile_files:
                text += "📱 *طرح‌های موبایل:*\n"
                for row in overdue_mobile_files:
                    code, count, created_at, part_ids = row
                    
                    # تبدیل UTC به تهران
                    created_tehran = get_tehran_time(created_at)
                    hours_waiting = (now_tehran - created_tehran).total_seconds() / 3600
                    
                    if hours_waiting > 48:
                        days = int(hours_waiting / 24)
                        time_str = f"{LRM}{days}{LRM} روز"
                    else:
                        time_str = f"{LRM}{int(hours_waiting)}{LRM} ساعت"
                    
                    # دریافت شماره قسمت‌ها
                    cursor.execute("""
                        SELECT id FROM uploaded_files 
                        WHERE code = %s AND status = 'pending'
                        ORDER BY id
                    """, (code,))
                    parts = []
                    all_parts = cursor.fetchall()
                    for i, (file_id,) in enumerate(all_parts, 1):
                        parts.append(str(i))
                    
                    if len(parts) > 1:
                        parts_list = ', '.join(parts)
                        parts_str = f" (قسمت‌های {LRM}{parts_list}{LRM})"
                    else:
                        parts_str = ""
                    
                    text += f"🔴 کد {LRM}{code}{LRM}{parts_str} ـ {time_str} انتظار\n"
                text += "\n"
            
            if overdue_sticker_files:
                text += "🎨 *استیکرها:*\n"
                for row in overdue_sticker_files:
                    code, count, created_at, part_ids = row
                    
                    # تبدیل UTC به تهران
                    created_tehran = get_tehran_time(created_at)
                    hours_waiting = (now_tehran - created_tehran).total_seconds() / 3600
                    
                    if hours_waiting > 48:
                        days = int(hours_waiting / 24)
                        time_str = f"{LRM}{days}{LRM} روز"
                    else:
                        time_str = f"{LRM}{int(hours_waiting)}{LRM} ساعت"
                    
                    # دریافت شماره قسمت‌ها
                    cursor.execute("""
                        SELECT id FROM uploaded_sticker_files 
                        WHERE code = %s AND status = 'pending'
                        ORDER BY id
                    """, (code,))
                    parts = []
                    all_parts = cursor.fetchall()
                    for i, (file_id,) in enumerate(all_parts, 1):
                        parts.append(str(i))
                    
                    if len(parts) > 1:
                        parts_list = ', '.join(parts)
                        parts_str = f" (قسمت‌های {LRM}{parts_list}{LRM})"
                    else:
                        parts_str = ""
                    
                    text += f"🔴 کد {LRM}{code}{LRM}{parts_str} ـ {time_str} انتظار\n"
            
            # نمایش زمان بررسی به وقت تهران
            time_check = now_tehran.strftime('%H:%M')
            text += f"\n⏰ زمان بررسی: {LRM}{time_check}{LRM}"
            
            # ارسال به گروه محصولات
            await context.bot.send_message(
                chat_id=GROUP_PRODUCTS,
                text=text,
                parse_mode="Markdown"
            )
            
            # همچنین به نازی هم اطلاع بده
            await context.bot.send_message(
                chat_id=NAZI_CHAT_ID,
                text=text,
                parse_mode="Markdown"
            )
            
            logging.info(f"Overdue notification sent for {len(overdue_mobile_files)} mobile + {len(overdue_sticker_files)} sticker codes")
    
    except Exception as e:
        logging.error(f"Check overdue files error: {e}")
    finally:
        cursor.close()
        conn.close()
        
async def handle_keyboard_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت کلیک روی دکمه‌های کیبورد"""
    text = update.message.text
    chat_id = update.effective_chat.id
    
    # دکمه‌های علیرضا
    if chat_id == DESIGNER_CHAT_ID:
        if text == "➕ ثبت طرح موبایل":
            await new_command(update, context)
        elif text == "🎨 ثبت استیکر":
            await new_sticker_command(update, context)
        elif text == "📊 آمار موبایل":
            await stats_command(update, context)
        elif text == "📊 آمار استیکر":
            await stats_sticker_command(update, context)
        elif text == "📤 گزارش موبایل":
            await uploads_command(update, context)
        elif text == "📤 گزارش استیکر":
            await uploads_sticker_command(update, context)
        elif text == "💾 بکاپ":
            await backup_command(update, context)
        elif text == "👥 ادمین‌ها":
            await admins_command(update, context)
        elif text == "🔄 ریستارت":  # 🆕 اضافه شد
            await restart_command(update, context)
        elif text == "📊 وضعیت":  # 🆕 اضافه شد
            await status_command(update, context)
    
    # دکمه‌های نازی
    elif chat_id == NAZI_CHAT_ID:
        if text == "📊 آمار موبایل":
            await stats_command(update, context)
        elif text == "📊 آمار استیکر":
            await stats_sticker_command(update, context)
        elif text == "📤 گزارش موبایل":
            await uploads_command(update, context)
        elif text == "📤 گزارش استیکر":
            await uploads_sticker_command(update, context)
        elif text == "👥 ادمین‌ها":
            await admins_command(update, context)
            
async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ریستارت ربات از طریق supervisor"""
    chat_id = update.effective_chat.id

    if chat_id != DESIGNER_CHAT_ID:
        await update.message.reply_text("🚫 فقط علیرضا می‌تونه ربات رو ریستارت کنه!")
        return

    keyboard = [
        [
            InlineKeyboardButton("✅ بله، ریستارت کن!", callback_data="confirm_restart"),
            InlineKeyboardButton("❌ انصراف", callback_data="cancel_restart")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "⚠️ مطمئنی می‌خوای ربات رو ریستارت کنی?\n\n"
        "ربات برای 5-10 ثانیه آفلاین می‌شه.",
        reply_markup=reply_markup
    )

async def confirm_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تایید و اجرای ریستارت"""
    query = update.callback_query
    await query.answer()

    chat_id = query.from_user.id

    if chat_id != DESIGNER_CHAT_ID and chat_id != NAZI_CHAT_ID:
        await query.edit_message_text("🚫 دسترسی غیرمجاز.")
        return

    await query.edit_message_text(
        "🔄 ربات در حال ریستارت...\n"
        "⏳ لطفاً 10 ثانیه صبر کنید.\n\n"
        "بعد از آماده شدن، /start بفرستید."
    )

    logging.info(f"🔄 Restart requested by user {chat_id}")

    # ارسال نوتیف به گروه لاگ
    try:
        await context.bot.send_message(
            chat_id=LOG_GROUP_ID,
            text=f"🔄 ربات توسط کاربر {chat_id} ریستارت شد - {get_tehran_time().strftime('%H:%M:%S')}"
        )
    except:
        pass

    # مسیر فایل تنظیمات supervisor
    supervisor_conf = "/home/selfnit4/supervisor/supervisord.conf"

    try:
        # 🆕 ریستارت به صورت غیرهمزمان (بدون منتظر ماندن)
        subprocess.Popen(
            ['supervisorctl', '-c', supervisor_conf, 'restart', 'tisabot'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        logging.info("✅ Restart command sent to supervisor")
            
    except Exception as e:
        logging.error(f"❌ Restart error: {e}")

async def cancel_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لغو ریستارت"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("👌 عملیات ریستارت لغو شد.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش وضعیت ربات"""
    chat_id = update.effective_chat.id

    if chat_id != DESIGNER_CHAT_ID:
        await update.message.reply_text("🚫 فقط علیرضا دسترسی دارد!")
        return

    supervisor_conf = "/home/selfnit4/supervisor/supervisord.conf"

    try:
        # دریافت وضعیت
        result = subprocess.run(
            ['supervisorctl', '-c', supervisor_conf, 'status', 'tisabot'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        status_text = result.stdout.strip()
        
        await update.message.reply_text(
            f"📊 *وضعیت ربات:*\n\n"
            f"`{status_text}`\n\n"
            f"🕐 زمان: {get_tehran_time().strftime('%Y-%m-%d %H:%M:%S')}",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logging.error(f"Status check error: {e}")
        await update.message.reply_text("❌ خطا در دریافت وضعیت")
        
def shutdown_handler(signum, frame):
    """مدیریت خروج ایمن از ربات"""
    logging.info("🛑 Received shutdown signal, cleaning up...")
    
    try:
        # 1. توقف job queue
        if application_ref and application_ref.job_queue:
            application_ref.job_queue.stop()
        
        # 2. بستن connection pool دیتابیس
        if 'db_pool' in globals():
            db_pool.close()
            logging.info("✅ Database connection pool closed")
        
        # 3. پاک کردن فایل‌های موقت
        cleanup_temp_files()
        
        # 4. لاگ نهایی
        logging.info("✅ Bot shutdown completed gracefully")
        
    except Exception as e:
        logging.error(f"❌ Error during shutdown: {e}")
    finally:
        exit(0)

def cleanup_temp_files():
    """پاک کردن فایل‌های موقت"""
    try:
        # پاک کردن فایل‌های restore موقت
        for file_key, file_path in list(restore_files.items()):
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logging.info(f"🗑️ Removed temp file: {file_path}")
            except Exception as e:
                logging.warning(f"Could not remove temp file {file_path}: {e}")
        
        # پاک کردن backup موقت
        if os.path.exists("tisa_backup.sql"):
            os.remove("tisa_backup.sql")
            
    except Exception as e:
        logging.warning(f"Temp file cleanup warning: {e}")

async def notify_nazi_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارسال نوتیفیکیشن آپدیت به نازی"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # فقط علیرضا می‌تونه کلیک کنه
    if user_id != DESIGNER_CHAT_ID:
        await query.answer("🚫 فقط علیرضا می‌تونه این دکمه رو بزنه!", show_alert=True)
        return
    
    try:
        # ارسال پیام به نازی
        await context.bot.send_message(
            chat_id=NAZI_CHAT_ID,
            text="⏏️ ربات اپدیت شد!\n\n"
                 "💢 برای دریافت تغییرات جدید روی دکمه زیر بزنید!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "اعمال تغییرات ✅", 
                    url="https://t.me/tisachapbot?start=1"
                )
            ]])
        )
        
        # ویرایش پیام علیرضا
        await query.edit_message_text(
            text=f"{query.message.text}\n\n✅ پیام آپدیت به نازی ارسال شد!",
            reply_markup=None  # حذف دکمه بعد از کلیک
        )
        
        logging.info(f"✅ Restart notification sent to Nazi by designer")
        
    except Exception as e:
        logging.error(f"Error sending restart notification to Nazi: {e}")
        await query.answer("❌ خطایی در ارسال پیام رخ داد.", show_alert=True)

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

async def send_startup_notification():
    """ارسال پیام استارت به علیرضا با دکمه اطلاع به نازی"""
    try:
        startup_time = get_tehran_time().strftime('%Y-%m-%d %H:%M:%S')
        
        # ارسال به علیرضا با دکمه اختیاری برای اطلاع به نازی
        await application_ref.bot.send_message(
            chat_id=DESIGNER_CHAT_ID,
            text=f"✅ ربات تیسا چاپ با موفقیت راه‌اندازی شد!\n\n"
                 f"🕐 زمان: {startup_time}\n"
                 f"🤖 وضعیت: آماده دریافت دستورات\n\n"
                 f"💡 می‌خوای به نازی اطلاع بدی که ربات آپدیت شد؟",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📢 اطلاع به نازی", callback_data="notify_nazi_restart")
            ]])
        )
        logging.info(f"✅ Startup notification sent to designer")
    except Exception as e:
        logging.error(f"Failed to send startup notification: {e}")
        
if __name__ == "__main__":
    # تنظیم logging
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO,
        handlers=[
            logging.StreamHandler(),
        ]
    )
    
    # اضافه کردن Telegram Handler
    telegram_handler = TelegramLogHandler(BOT_TOKEN, LOG_GROUP_ID)
    telegram_handler.setLevel(logging.INFO)
    telegram_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    )
    logging.getLogger().addHandler(telegram_handler)
    
    # فیلتر کردن لاگ‌های اضافی
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    
    logging.info("🚀 ربات تیسا چاپ شروع به کار کرد")
    
    application = Application.builder().token(BOT_TOKEN).build()
    application_ref = application
    
    init_db()
    
    # Job Queue Setup
    job_queue = application.job_queue
    
    job_queue.run_repeating(check_overdue_files, interval=43200, first=60, name='check_overdue')
    job_queue.run_repeating(cleanup_admin_cache, interval=3600, first=300, name='cleanup_cache')
    job_queue.run_repeating(cleanup_stale_pending_codes, interval=3600, first=600, name='cleanup_stale_codes')

    # Command Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("new", new_command))
    application.add_handler(CommandHandler("backup", backup_command))
    application.add_handler(CommandHandler("restore", restore_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("promote", promote_command))
    application.add_handler(CommandHandler("demote", demote_command))
    application.add_handler(CommandHandler("admins", admins_command))
    application.add_handler(CommandHandler("uploads", uploads_command))
    application.add_handler(CommandHandler("restart", restart_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("new_sticker", new_sticker_command))
    application.add_handler(CommandHandler("stats_sticker", stats_sticker_command))
    application.add_handler(CommandHandler("uploads_sticker", uploads_sticker_command))

    # Keyboard Button Handler
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_keyboard_buttons
    ))


    application.add_handler(CallbackQueryHandler(handle_add_mockup, pattern="^add_mockup$"))
    application.add_handler(CallbackQueryHandler(handle_add_print, pattern="^add_print$"))
    application.add_handler(CallbackQueryHandler(handle_back_to_menu, pattern="^back_to_menu$"))
    application.add_handler(CallbackQueryHandler(handle_cancel_submission, pattern="^cancel_submission$"))
    application.add_handler(CallbackQueryHandler(handle_confirm_submit, pattern="^confirm_submit$"))
    
    application.add_handler(CallbackQueryHandler(notify_nazi_restart, pattern="^notify_nazi_restart$"))

    application.add_handler(CallbackQueryHandler(handle_approve, pattern=r"^approve_(mobile|sticker)_"))
    application.add_handler(CallbackQueryHandler(handle_reject, pattern=r"^reject_(mobile|sticker)_"))
    application.add_handler(CallbackQueryHandler(handle_undo, pattern=r"^undo_(mobile|sticker)_"))
    application.add_handler(CallbackQueryHandler(mark_uploaded, pattern=r"^mark_uploaded_(mobile|sticker)_"))

    application.add_handler(CallbackQueryHandler(confirm_reset_stats, pattern="^confirm_reset_stats_(mobile|sticker)$"))  
    application.add_handler(CallbackQueryHandler(do_reset_stats, pattern="^do_reset_stats_(mobile|sticker)$"))  
    application.add_handler(CallbackQueryHandler(cancel_reset, pattern="^cancel_reset$"))
    

    application.add_handler(CallbackQueryHandler(confirm_reset_uploads, pattern="^confirm_reset_uploads_(mobile|sticker)$"))  
    application.add_handler(CallbackQueryHandler(do_reset_uploads, pattern="^do_reset_uploads_(mobile|sticker)$"))  
    application.add_handler(CallbackQueryHandler(cancel_reset_uploads, pattern="^cancel_reset_uploads$"))
    

    application.add_handler(CallbackQueryHandler(confirm_restart, pattern="^confirm_restart$"))
    application.add_handler(CallbackQueryHandler(cancel_restart, pattern="^cancel_restart$"))
    

    application.add_handler(CallbackQueryHandler(confirm_restore, pattern=r"^restore_"))
    application.add_handler(CallbackQueryHandler(cancel_restore, pattern="^cancel_restore$"))
    

    application.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & (filters.PHOTO | filters.Document.ALL),
        handle_designer_files
    ))
    
    application.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.Document.ALL,
        handle_restore_file
    ))
    
    application.add_handler(MessageHandler(
        filters.Chat(NAZI_CHAT_ID) & filters.REPLY & filters.TEXT,
        handle_revision_request
    ))

    logging.info("🚀 ربات با اتصال به MySQL در حال اجراست...")
    
    # 🆕 ارسال پیام استارت قبل از شروع polling
    application.job_queue.run_once(
        lambda context: send_startup_notification(), 
        when=2  # 2 ثانیه بعد از شروع
    )
    
    application.run_polling()