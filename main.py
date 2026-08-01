import logging
import asyncio
import html
import os
from datetime import time
from utils.enums import DesignStatus
import traceback


from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import RetryAfter
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    CallbackQueryHandler, ContextTypes
)

# Configuration & Infrastructure
from config.settings import BOT_TOKEN, BACKUP_TIME_HOUR, BACKUP_TIME_MINUTE, LOG_LEVEL, LOG_FORMAT, LOG_GROUP_ID, SERVER_BILL_REMINDER_HOUR, SERVER_BILL_REMINDER_MINUTE
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
from migrations.migration_008_add_stats_reset import Migration008
from migrations.migration_009_add_file_types import Migration009

# Handlers
from handlers.common import start_command, cancel_command
from handlers.sudo import (
    switch_role_command, handle_role_switch,
    manual_backup_command, restore_command, restart_command, execute_restart,
    group_management_command, group_management_callback,
    handle_group_id_input, status_command, broadcast_update_callback,
    delete_design_command, confirm_delete_design_callback,
    cleanup_orphans_command, confirm_restore_callback,
    backup_type_callback, csv_range_callback
)
from handlers.reset_stats import reset_stats_command, reset_stats_callback
from handlers.stats import stats_command, stats_callback
from handlers.editor import start_new_design, handle_files, editor_callbacks
from handlers.reviewer import review_callback, handle_reject_reason_reply
from handlers.help import help_command, help_callback
from handlers.search import (
    search_command, search_filter_callback, search_back_callback,
    handle_search_code_input
)
from handlers.my_designs import (
    my_designs_command, my_designs_callback
)
from handlers.management import (
    add_user_command, remove_user_command, list_users_command, set_role_command,
    list_lines_command, add_line_command, disable_line_command, enable_line_command,
    delete_line_command,
    lock_code_command, unlock_code_command, locked_codes_command
)
from handlers.design_management import (
    design_info_command,
    handle_design_code_input,
    confirm_delete_callback,
    pending_designs_command,
    pending_view_callback
)
from handlers.server_bill import (
    server_bill_callback,
    send_monthly_reminder,
    send_daily_reminder
)

# Models
from models.product_line import ProductLine
from models.user import User


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Global handler for uncaught exceptions in any handler.
    Logs to console, sends sanitized version to LOG_GROUP_ID, and notifies user if possible.
    """
    logging.error("Uncaught exception:", exc_info=context.error)
    tb_str = ''.join(traceback.format_exception(
        type(context.error), context.error, context.error.__traceback__
    ))

    # Sanitize: Remove potential secrets (file paths, env vars, tokens)
    safe_tb = tb_str
    # Remove absolute paths
    safe_tb = safe_tb.replace(os.getcwd(), '<PROJECT_ROOT>')
    # Truncate for Telegram
    safe_tb = html.escape(safe_tb[-3000:])

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
        Migration007(), Migration008(), Migration009()
    ]
    manager = MigrationManager()
    manager.run_migrations(migrations)
    CodeService.cleanup_orphaned_designs()


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # -----------------------------------------------------------------------
    # Priority 0 — awaiting reject reason reply
    # -----------------------------------------------------------------------
    if await handle_reject_reason_reply(update, context):
        return

    # -----------------------------------------------------------------------
    # Priority 1 — awaiting group input
    # -----------------------------------------------------------------------
    if context.user_data.get('awaiting_group_input'):
        handled = await handle_group_id_input(update, context)
        if handled:
            return

    # -----------------------------------------------------------------------
    # Priority 1.5 — awaiting delete line confirmation
    # -----------------------------------------------------------------------
    if context.user_data.get('awaiting_delete_line_confirm'):
        prefix = context.user_data.pop('awaiting_delete_line_confirm')
        from handlers.management import _execute_delete_line
        await _execute_delete_line(update, context, prefix)
        return

    # -----------------------------------------------------------------------
    # Priority 1.6 — awaiting search code input
    # -----------------------------------------------------------------------
    if context.user_data.get('awaiting_search_code'):
        handled = await handle_search_code_input(update, context)
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
        user = User.get_by_id(update.effective_user.id)
        user_role = user.get_effective_role() if user else None
        for pl in products:
            expected_text = f"📊 آمار {pl.name_fa}"
            if text == expected_text:
                # Editors see only their own stats
                editor_id = update.effective_user.id if user_role == 'editor' else None
                stats = pl.get_stats(editor_user_id=editor_id)
                if editor_id:
                    header = f"{pl.icon} آمار شخصی — {pl.name_fa}"
                else:
                    header = f"{pl.icon} آمار {pl.name_fa}"
                msg = (
                    f"{header}\n\n"
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

    if text == "🔧 ریستور":
        return await restore_command(update, context)

    if text == "🔄 ریستارت":
        return await restart_command(update, context)

    if text == "⚙️ تنظیم گروه‌ها":
        return await group_management_command(update, context)

    if text == "📊 وضعیت":
        return await status_command(update, context)

    if text == "🔄 بازنشانی آمار":
        return await reset_stats_command(update, context)

    if text == "📖 راهنما":
        return await help_command(update, context)

    if text == "🔍 جستجوی پیشرفته":
        return await search_command(update, context)

    if text == "📋 طرح‌های من":
        return await my_designs_command(update, context)

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
    """
    Async Queue handler for Telegram to prevent rate limits.

    Every record at INFO level or above is queued and forwarded to
    LOG_GROUP_ID. A single background task drains the queue, so a burst of
    log records (e.g. all the per-file logs produced while approving a design)
    is never silently dropped while a previous log message is still being
    sent — every record, including the "Mockup N FAILED" exceptions, reaches
    the log group.
    """
    MAX_QUEUE_SIZE = 200

    def __init__(self, bot, chat_id):
        super().__init__()
        self.bot = bot
        self.chat_id = chat_id
        self.message_queue = []
        self._task = None

    def emit(self, record):
        if record.levelno < logging.INFO:
            return

        try:
            log_entry = self.format(record)
        except Exception:
            return

        # Bound memory: if a burst outpaces the log sender, keep the newest
        # records (which usually describe the failure being diagnosed) and
        # drop the oldest instead of dropping the latest log lines.
        if len(self.message_queue) >= self.MAX_QUEUE_SIZE:
            self.message_queue.pop(0)

        self.message_queue.append(log_entry)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (e.g. emitted from a non-async thread); the
            # message stays queued and will be sent once a loop exists.
            return

        if self._task is None or self._task.done():
            self._task = loop.create_task(self._process_queue())

    async def _process_queue(self):
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
            except RetryAfter as e:
                # Respect Telegram flood control on the log chat itself.
                raw = getattr(e, 'retry_after', 1)
                if hasattr(raw, 'total_seconds'):  # datetime.timedelta (PTB >= 22)
                    try:
                        wait = max(int(raw.total_seconds()), 1)
                    except (TypeError, ValueError):
                        wait = 1
                else:
                    try:
                        wait = max(int(raw or 1), 1)
                    except (TypeError, ValueError):
                        wait = 1
                await asyncio.sleep(wait)
            except Exception:
                # Never let log delivery crash the bot; drop this single
                # message and keep draining the rest of the queue.
                pass


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
    application.add_handler(CommandHandler("search",        search_command))

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
    application.add_handler(CommandHandler("deleteline",    delete_line_command))

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
        pattern=r"^(stage_mockup_done|stage_print_done|stage_goto_mockup|stage_goto_print|back_to_workspace|stage_mockup_clear|stage_print_clear|clear_confirmed_mockup|clear_confirmed_print|clear_cancelled_mockup|clear_cancelled_print|confirm_submit|submit_to_reviewer|preview_files|cancel_submission|cancel_editing|manage_mockups|manage_prints|manage_clear_mockup|manage_clear_print|manage_back|remove_mockup_\d+|remove_print_\d+)$"
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

    # Restore confirmation
    application.add_handler(CallbackQueryHandler(
        confirm_restore_callback,
        pattern=r"^(confirm_restore|cancel_restore)$"
    ))

    # Backup type selection
    application.add_handler(CallbackQueryHandler(
        backup_type_callback,
        pattern=r"^(backup_csv|backup_zip)$"
    ))

    # CSV time range selection
    application.add_handler(CallbackQueryHandler(
        csv_range_callback,
        pattern=r"^(csv_week|csv_month|csv_all|csv_cancel)$"
    ))

    # Reset stats
    application.add_handler(CallbackQueryHandler(
        reset_stats_callback,
        pattern=r"^(reset_stats_|reset_editor_|reset_reviewer_|reset_line_|confirm_reset_|reset_stats_cancel)"
    ))

    # Search
    application.add_handler(CallbackQueryHandler(
        search_filter_callback,
        pattern=r"^search_(filter_|set_|execute|clear|cancel|page_|view_)"
    ))

    application.add_handler(CallbackQueryHandler(
        search_back_callback,
        pattern=r"^search_back$"
    ))

    # My Designs
    application.add_handler(CallbackQueryHandler(
        my_designs_callback,
        pattern=r"^mydesigns_"
    ))

    # Server bill reminder
    application.add_handler(CallbackQueryHandler(
        server_bill_callback,
        pattern=r"^server_bill_"
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

    # Server bill reminder - monthly on 12th at 9:00 AM Tehran time
    server_bill_time = time(hour=SERVER_BILL_REMINDER_HOUR, minute=SERVER_BILL_REMINDER_MINUTE, tzinfo=TEHRAN_TZ)
    application.job_queue.run_monthly(
        send_monthly_reminder,
        when=server_bill_time,
        day=12,
        name='server_bill_monthly'
    )

    # Server bill daily follow-up reminder
    application.job_queue.run_daily(
        send_daily_reminder,
        time=server_bill_time,
        name='server_bill_daily'
    )

    logging.info("✅ Bot is ready and polling...")
    application.run_polling()