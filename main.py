import logging
import asyncio
import html
from datetime import time
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    CallbackQueryHandler, ContextTypes
)

# Configuration & Infrastructure
from config.settings import BOT_TOKEN, BACKUP_TIME_HOUR, BACKUP_TIME_MINUTE, LOG_LEVEL, LOG_FORMAT, LOG_GROUP_ID
from utils.helpers import get_tehran_time, TEHRAN_TZ
from config.database import test_connection, init_legacy_tables
from services.backup_service import BackupService, send_daily_backup
from services.code_service import CodeService

# Migrations
from migrations.migration_manager import MigrationManager
from migrations.migration_001_add_users_table import Migration001
from migrations.migration_002_add_product_lines import Migration002
from migrations.migration_003_create_unified_tables import Migration003
from migrations.migration_004_migrate_existing_data import Migration004
from migrations.migration_005_add_groups_and_reviewer_dict import Migration005
from migrations.migration_006_create_design_group_messages import Migration006
from migrations.migration_007_add_deleted_status import Migration007
 
 
# Handlers
from handlers.common import start_command, cancel_command
from handlers.sudo import (
    switch_role_command, handle_role_switch,
    manual_backup_command, restart_command, execute_restart,
    group_management_command, group_management_callback,
    handle_group_id_input, status_command, broadcast_update_callback
)
from handlers.stats import stats_command, stats_callback
from handlers.editor import start_new_design, handle_files, editor_callbacks, handle_undo_submission
from handlers.reviewer import review_callback
from handlers.help import help_command, help_callback
from handlers.management import (
    add_user_command, remove_user_command, list_users_command, set_role_command,
    list_lines_command, add_line_command, disable_line_command, enable_line_command,
    lock_code_command, unlock_code_command, locked_codes_command
)
from handlers.design_management import (
    design_info_command, delete_design_command,
    handle_design_code_input,
    confirm_delete_callback,
    pending_designs_command,
    pending_view_callback
)




# Models
from models.product_line import ProductLine
from models.user import User

# Setup Logging
logging.basicConfig(
    format=LOG_FORMAT, level=LOG_LEVEL,
    handlers=[logging.StreamHandler()]
)
logging.getLogger("httpx").setLevel(logging.WARNING)


def run_db_migrations():
    logging.info("Checking database migrations...")
    init_legacy_tables()

    migrations = [
        Migration001(), Migration002(), Migration003(),
        Migration004(), Migration005(), Migration006(),
        Migration007()
    ]
    manager = MigrationManager()
    manager.run_migrations(migrations)
    CodeService.cleanup_orphaned_designs()


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    # These two take absolute priority — must be first
    if context.user_data.get('awaiting_group_input'):
        handled = await handle_group_id_input(update, context)
        if handled:
            return

    if context.user_data.get('awaiting_design_code'):
        handled = await handle_design_code_input(update, context)
        if handled:
            return


    if text.startswith("➕"):
        products = ProductLine.get_all_active()
        for pl in products:
            if pl.name_fa in text:
                return await start_new_design(update, context, pl.code_prefix)

    elif text == "📊 وضعیت":
        return await status_command(update, context)

    elif text == "📊 آمار کلی":
        return await stats_command(update, context)
        
    elif text.startswith("📊"):
        products = ProductLine.get_all_active()
        for pl in products:
            if pl.name_fa in text:
                stats = pl.get_stats()
                msg = (
                    f"{pl.icon} آمار {pl.name_fa}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"⏳ در انتظار: {stats['pending']}\n"
                    f"✅ تایید شده: {stats['approved']}\n"
                    f"❌ رد شده: {stats['rejected']}\n"
                    f"🔒 قفل شده: {stats['locked']}"
                )
                await update.message.reply_text(msg)
                return

    elif text == "👑 تغییر نقش":
        return await switch_role_command(update, context)
    elif text == "💾 بکاپ":
        return await manual_backup_command(update, context)
    elif text == "🔄 ریستارت":
        return await restart_command(update, context)
    elif text == "⚙️ تنظیم گروه‌ها":
        return await group_management_command(update, context)
    elif text == "📖 راهنما":
        return await help_command(update, context)
    elif text == "❌ لغو":
        return await cancel_command(update, context)
    elif text == "🔍 اطلاعات طرح":
        return await design_info_command(update, context)
    elif text == "🗑 حذف طرح":
        return await delete_design_command(update, context)
    elif text == "📋 طرح‌های در انتظار":
        return await pending_designs_command(update, context)
    else:
        if any(k.startswith('awaiting_') for k in context.user_data):
            await update.message.reply_text(
                "⚠️ ورودی نامعتبر. لطفاً دوباره امتحان کنید یا /cancel بزنید."
            )

async def sendlog_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forward the daily log message to a reviewer when sudo taps the button"""
    query = update.callback_query
    await query.answer()

    from config.settings import SUDO_USER_ID
    if query.from_user.id != SUDO_USER_ID:
        await query.answer("🚫 فقط Sudo", show_alert=True)
        return

    target_user_id = int(query.data.split('_')[1])
    target_user = User.get_by_id(target_user_id)
    if not target_user:
        await query.answer("❌ کاربر یافت نشد", show_alert=True)
        return

    try:
        await context.bot.forward_message(
            chat_id=target_user_id,
            from_chat_id=query.message.chat_id,
            message_id=query.message.message_id
        )
        await query.answer(f"✅ ارسال شد به {target_user.first_name}")
    except Exception as e:
        logging.error(f"Failed to forward log to {target_user_id}: {e}")
        await query.answer("❌ ارسال ناموفق", show_alert=True)

async def send_startup_notification(context: ContextTypes.DEFAULT_TYPE):
    from config.settings import SUDO_USER_ID
    startup_time = get_tehran_time().strftime('%Y-%m-%d %H:%M:%S')

    try:
        await context.bot.send_message(
            chat_id=SUDO_USER_ID,
            text=f"✅ ربات تیسا چاپ با موفقیت راه‌اندازی شد!\n\n"
                 f"🕐 زمان: {startup_time}\n"
                 f"🤖 وضعیت: آماده دریافت دستورات\n\n"
                 f"💡 می‌خوای به بقیه اطلاع بدی که ربات آپدیت شد؟",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📢 اطلاع به همه کاربران", callback_data="broadcast_update")
            ]])
        )
    except Exception as e:
        logging.error(f"Startup notif failed: {e}")

class TelegramLogHandler(logging.Handler):
    """Async Queue handler for Telegram to prevent rate limits"""
    def __init__(self, bot, chat_id):
        super().__init__()
        self.bot = bot
        self.chat_id = chat_id
        self.message_queue = []
        self.is_sending = False

    def emit(self, record):
        if record.levelno >= logging.INFO:
            try:
                log_entry = self.format(record)
                self.message_queue.append(log_entry)
                if not self.is_sending:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(self._process_queue())
            except Exception:
                pass

    async def _process_queue(self):
        if self.is_sending:
            return
        self.is_sending = True
        try:
            while self.message_queue:
                msg = self.message_queue.pop(0)
                safe_msg = html.escape(msg[:3000])
                try:
                    await self.bot.send_message(
                        self.chat_id,
                        f"ℹ️ Bot Log\n<pre>{safe_msg}</pre>",
                        parse_mode="HTML"
                    )
                    await asyncio.sleep(0.3)
                except Exception:
                    pass
        finally:
            self.is_sending = False

if __name__ == "__main__":
    logging.info("🚀 Starting Tisa Print Bot...")

    test_connection()
    run_db_migrations()

    application = Application.builder().token(BOT_TOKEN).build()

    telegram_handler = TelegramLogHandler(application.bot, LOG_GROUP_ID)
    telegram_handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(telegram_handler)

    # -----------------------------------------------------------------------
    # Commands
    # -----------------------------------------------------------------------
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("help", help_command))

    # User management
    application.add_handler(CommandHandler("adduser", add_user_command))
    application.add_handler(CommandHandler("removeuser", remove_user_command))
    application.add_handler(CommandHandler("listusers", list_users_command))
    application.add_handler(CommandHandler("setrole", set_role_command))

    # Product line management
    application.add_handler(CommandHandler("listlines", list_lines_command))
    application.add_handler(CommandHandler("addline", add_line_command))
    application.add_handler(CommandHandler("disableline", disable_line_command))
    application.add_handler(CommandHandler("enableline", enable_line_command))

    # Code management
    application.add_handler(CommandHandler("lockcode", lock_code_command))
    application.add_handler(CommandHandler("unlockcode", unlock_code_command))
    application.add_handler(CommandHandler("lockedcodes", locked_codes_command))

    # -----------------------------------------------------------------------
    # Text routing & Files
    # -----------------------------------------------------------------------
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_router)
    )

    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (filters.PHOTO | filters.Document.ALL),
            handle_files
        )
    )

    # -----------------------------------------------------------------------
    # Callbacks
    # -----------------------------------------------------------------------
    application.add_handler(CallbackQueryHandler(handle_role_switch, pattern=r"^role_"))
    application.add_handler(CallbackQueryHandler(
        editor_callbacks,
        pattern=r"^(add_mockup|add_print|back_to_menu|cancel_submission|confirm_submit)$"
    ))
    application.add_handler(CallbackQueryHandler(handle_undo_submission, pattern=r"^undo_"))
    application.add_handler(CallbackQueryHandler(review_callback, pattern=r"^(approve|reject)_"))
    application.add_handler(CallbackQueryHandler(
        execute_restart, pattern=r"^(confirm_restart|cancel_restart)$"
    ))
    application.add_handler(CallbackQueryHandler(
        group_management_callback, pattern=r"^setgroup_"
    ))
    application.add_handler(CallbackQueryHandler(
        sendlog_callback, pattern=r"^sendlog_"
    ))
    application.add_handler(CallbackQueryHandler(
        help_callback, pattern=r"^help_"
    ))
    application.add_handler(CallbackQueryHandler(
        stats_callback, pattern=r"^stats_"
    ))
    application.add_handler(CallbackQueryHandler(
        broadcast_update_callback, pattern=r"^broadcast_update$"
    ))
    application.add_handler(CallbackQueryHandler(
        confirm_delete_callback,
        pattern=r"^(confirm_delete_|cancel_delete)"
    ))
    application.add_handler(CallbackQueryHandler(
        pending_view_callback,
        pattern=r"^pending_view_"
    ))
    # -----------------------------------------------------------------------
    # Jobs
    # -----------------------------------------------------------------------
    application.job_queue.run_once(send_startup_notification, 2)
    
    t = time(hour=BACKUP_TIME_HOUR, minute=BACKUP_TIME_MINUTE, tzinfo=TEHRAN_TZ)
    application.job_queue.run_daily(send_daily_backup, time=t, name='daily_backup')

    logging.info("✅ Bot is ready and polling...")
    application.run_polling()