import logging
import asyncio
import html
from datetime import time
from utils.enums import DesignStatus
import traceback


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
    handle_group_id_input, status_command, broadcast_update_callback,
    delete_design_command, confirm_delete_design_callback,
    cleanup_orphans_command
)
from handlers.stats import stats_command, stats_callback
from handlers.editor import start_new_design, handle_files, editor_callbacks
from handlers.reviewer import review_callback
from handlers.help import help_command, help_callback
from handlers.management import (
    add_user_command, remove_user_command, list_users_command, set_role_command,
    list_lines_command, add_line_command, disable_line_command, enable_line_command,
    lock_code_command, unlock_code_command, locked_codes_command
)
from handlers.design_management import (
    design_info_command,
    handle_design_code_input,
    confirm_delete_callback,
    pending_designs_command,
    pending_view_callback
)

# Models
from models.product_line import ProductLine
from models.user import User


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Global handler for uncaught exceptions in any handler.
    Logs to console, sends to LOG_GROUP_ID, and notifies user if possible.
    """
    logging.error("Uncaught exception:", exc_info=context.error)
    tb_str = ''.join(traceback.format_exception(
        type(context.error), context.error, context.error.__traceback__
    ))

    safe_tb = html.escape(tb_str[-3000:])
    error_msg = (
        f"⚠️ <b>Uncaught Exception</b>\n\n"
        f"<pre>{safe_tb}</pre>"
    )

    try:
        await context.bot.send_message(
            chat_id=LOG_GROUP_ID,
            text=error_msg,
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Failed to send error to log group: {e}")

    if isinstance(update, Update):
        user_msg = "❌ خطای داخلی رخ داد. تیم فنی در جریان قرار گرفت."
        try:
            if update.callback_query:
                await update.callback_query.answer(user_msg, show_alert=True)
            elif update.message:
                await update.message.reply_text(user_msg)
        except Exception:
            pass


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
    text = update.message.text.strip()

    # -----------------------------------------------------------------------
    # Priority 1 — awaiting group input
    # -----------------------------------------------------------------------
    if context.user_data.get('awaiting_group_input'):
        handled = await handle_group_id_input(update, context)
        if handled:
            return

    # -----------------------------------------------------------------------
    # Priority 2 — awaiting design code (info / delete)
    # -----------------------------------------------------------------------
    if context.user_data.get('awaiting_design_code'):
        handled = await handle_design_code_input(update, context)
        if handled:
            return

    # -----------------------------------------------------------------------
    # Priority 3 — privileged keyboard buttons (حذف طرح / اطلاعات طرح)
    # ✅ Must be before product line buttons
    # ✅ Sets awaiting_design_code then waits for next message
    # -----------------------------------------------------------------------
    if text == "🗑 حذف طرح":
        if User.is_privileged_user(update.effective_user.id):
            context.user_data['awaiting_design_code'] = 'delete'
            await update.message.reply_text(
                "🗑 کد طرح تایید شده‌ای که می‌خواهید حذف کنید را وارد کنید:\n"
                "مثال: TS001\n\n"
                "⚠️ این عملیات فایل‌ها را از گروه‌ها حذف کرده و "
                "کد را آزاد می‌کند.\n\n"
                "برای لغو /cancel بزنید."
            )
        return

    if text == "🔍 اطلاعات طرح":
        if User.is_privileged_user(update.effective_user.id):
            context.user_data['awaiting_design_code'] = 'info'
            await update.message.reply_text(
                "🔍 کد طرح مورد نظر را وارد کنید:\n"
                "مثال: TS001\n\n"
                "برای لغو /cancel بزنید."
            )
        return

    # -----------------------------------------------------------------------
    # Priority 4 — product line submission buttons
    # -----------------------------------------------------------------------
    if text.startswith("➕") and "ثبت" in text:
        products = ProductLine.get_all_active()
        for pl in products:
            expected_text = f"➕ {pl.icon} ثبت {pl.name_fa}"
            if text == expected_text:
                return await start_new_design(update, context, pl.code_prefix)
        return

    # -----------------------------------------------------------------------
    # Priority 5 — product line stats buttons
    # -----------------------------------------------------------------------
    if text.startswith("📊 آمار") and text != "📊 آمار کلی":
        products = ProductLine.get_all_active()
        for pl in products:
            expected_text = f"📊 آمار {pl.name_fa}"
            if text == expected_text:
                stats = pl.get_stats()
                msg = (
                    f"{pl.icon} آمار {pl.name_fa}\n\n"
                    f"⏳ در انتظار: {stats[DesignStatus.PENDING]}\n"
                    f"✅ تایید شده: {stats[DesignStatus.APPROVED]}\n"
                    f"❌ رد شده: {stats[DesignStatus.REJECTED]}\n"
                    f"🔒 قفل شده: {stats['locked']}"
                )
                await update.message.reply_text(msg)
                return
        return

    # -----------------------------------------------------------------------
    # Priority 6 — system buttons (exact match, all independent)
    # -----------------------------------------------------------------------
    if text == "📋 طرح‌های در انتظار":
        return await pending_designs_command(update, context)

    if text == "📊 آمار کلی":
        return await stats_command(update, context)

    if text == "👑 تغییر نقش":
        return await switch_role_command(update, context)

    if text == "💾 بکاپ":
        return await manual_backup_command(update, context)

    if text == "🔄 ریستارت":
        return await restart_command(update, context)

    if text == "⚙️ تنظیم گروه‌ها":
        return await group_management_command(update, context)

    if text == "📊 وضعیت":
        return await status_command(update, context)

    if text == "📖 راهنما":
        return await help_command(update, context)

    # -----------------------------------------------------------------------
    # Fallback
    # -----------------------------------------------------------------------
    if context.user_data.get('stage'):
        await update.message.reply_text(
            "📎 لطفا فایل ارسال کنید یا از دکمه‌های زیر استفاده کنید."
        )
    elif any(k.startswith('awaiting_') for k in context.user_data):
        await update.message.reply_text(
            "❌ ورودی نامعتبر. لطفا دوباره امتحان کنید یا /cancel بزنید."
        )

    # -----------------------------------------------------------------------
    # Fallback
    # -----------------------------------------------------------------------
    else:
        if context.user_data.get('stage'):
            await update.message.reply_text(
                "📎 لطفا فایل ارسال کنید یا از دکمه‌های زیر استفاده کنید."
            )
        elif any(k.startswith('awaiting_') for k in context.user_data):
            await update.message.reply_text(
                "❌ ورودی نامعتبر. لطفا دوباره امتحان کنید یا /cancel بزنید."
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
            text=(
                f"✅ ربات تیسا چاپ با موفقیت راه‌اندازی شد!\n\n"
                f"🕐 زمان: {startup_time}\n"
                f"🤖 وضعیت: آماده دریافت دستورات\n\n"
                f"💡 می‌خوای به بقیه اطلاع بدی که ربات آپدیت شد؟"
            ),
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
        self._recursion_guard = False

    def emit(self, record):
        if self._recursion_guard:
            return

        if record.levelno >= logging.INFO:
            try:
                self._recursion_guard = True
                log_entry = self.format(record)
                self.message_queue.append(log_entry)
                if not self.is_sending:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(self._process_queue())
            except Exception:
                pass
            finally:
                self._recursion_guard = False

    async def _process_queue(self):
        if self.is_sending:
            return
        self.is_sending = True
        self._recursion_guard = True

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
            self._recursion_guard = False


if __name__ == "__main__":
    logging.info("🚀 Starting Tisa Print Bot...")

    test_connection()
    run_db_migrations()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_error_handler(global_error_handler)

    telegram_handler = TelegramLogHandler(application.bot, LOG_GROUP_ID)
    telegram_handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(telegram_handler)

    # -----------------------------------------------------------------------
    # Commands
    # -----------------------------------------------------------------------
    application.add_handler(CommandHandler("start",         start_command))
    application.add_handler(CommandHandler("cancel",        cancel_command))
    application.add_handler(CommandHandler("help",          help_command))

    # User management
    application.add_handler(CommandHandler("adduser",       add_user_command))
    application.add_handler(CommandHandler("removeuser",    remove_user_command))
    application.add_handler(CommandHandler("listusers",     list_users_command))
    application.add_handler(CommandHandler("setrole",       set_role_command))

    # Product line management
    application.add_handler(CommandHandler("listlines",     list_lines_command))
    application.add_handler(CommandHandler("addline",       add_line_command))
    application.add_handler(CommandHandler("disableline",   disable_line_command))
    application.add_handler(CommandHandler("enableline",    enable_line_command))

    # Code management
    application.add_handler(CommandHandler("lockcode",      lock_code_command))
    application.add_handler(CommandHandler("unlockcode",    unlock_code_command))
    application.add_handler(CommandHandler("lockedcodes",   locked_codes_command))

    # Design management
    application.add_handler(CommandHandler("designinfo",    design_info_command))
    application.add_handler(CommandHandler("deletedesign",  delete_design_command))
    application.add_handler(CommandHandler("cleanup",       cleanup_orphans_command))

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

    # Role switching
    application.add_handler(CallbackQueryHandler(
        handle_role_switch,
        pattern=r"^role_"
    ))

    # Editor stage flow
    application.add_handler(CallbackQueryHandler(
        editor_callbacks,
        pattern=r"^(stage_mockup_done|stage_print_done|stage_goto_mockup|stage_goto_print|back_to_workspace|stage_mockup_clear|stage_print_clear|workspace_clear_mockup|workspace_clear_print|clear_confirmed_mockup|clear_confirmed_print|clear_cancelled_mockup|clear_cancelled_print|confirm_submit|submit_to_reviewer|preview_files|cancel_submission)$"
    ))

    # Reviewer
    application.add_handler(CallbackQueryHandler(
        review_callback,
        pattern=r"^(approve|reject)_"
    ))

    # Restart
    application.add_handler(CallbackQueryHandler(
        execute_restart,
        pattern=r"^(confirm_restart|cancel_restart)$"
    ))

    # Group management
    application.add_handler(CallbackQueryHandler(
        group_management_callback,
        pattern=r"^setgroup_"
    ))

    # Log forwarding
    application.add_handler(CallbackQueryHandler(
        sendlog_callback,
        pattern=r"^sendlog_"
    ))

    # Help
    application.add_handler(CallbackQueryHandler(
        help_callback,
        pattern=r"^help_"
    ))

    # Stats
    application.add_handler(CallbackQueryHandler(
        stats_callback,
        pattern=r"^stats_"
    ))

    # Broadcast
    application.add_handler(CallbackQueryHandler(
        broadcast_update_callback,
        pattern=r"^broadcast_update$"
    ))

    # Delete design confirmation (from sudo.py)
    application.add_handler(CallbackQueryHandler(
        confirm_delete_design_callback,
        pattern=r"^(confirm_kill_.+|cancel_kill)$"
    ))

    # Design management callbacks (from design_management.py)
    application.add_handler(CallbackQueryHandler(
        confirm_delete_callback,
        pattern=r"^(confirm_delete_.+|cancel_delete)$"
    ))

    application.add_handler(CallbackQueryHandler(
        pending_view_callback,
        pattern=r"^pending_view_"
    ))

    # -----------------------------------------------------------------------
    # Jobs
    # -----------------------------------------------------------------------
    application.job_queue.run_once(send_startup_notification, 2)

    t = time(
        hour=BACKUP_TIME_HOUR,
        minute=BACKUP_TIME_MINUTE,
        tzinfo=TEHRAN_TZ
    )
    application.job_queue.run_daily(
        send_daily_backup,
        time=t,
        name='daily_backup'
    )

    logging.info("✅ Bot is ready and polling...")
    application.run_polling()