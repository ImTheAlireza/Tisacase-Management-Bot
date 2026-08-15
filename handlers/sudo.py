import os
import shutil
import asyncio
import logging
import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.decorators import require_sudo
from models.user import User
from models.product_line import ProductLine
from services.backup_service import BackupService
from utils.helpers import get_tehran_time, send_with_retry, safe_answer_callback
from config.settings import SUDO_USER_ID, SUPERVISORD_CONF, SUPERVISOR_PROCESS
from utils.enums import DesignStatus
from utils.callback_lock import deduplicate_callback


def _verify_sudo_for_group_input(user_id):
    """Helper to verify sudo access for group input flow"""
    from config.settings import SUDO_USER_ID
    from models.user import User

    if user_id != SUDO_USER_ID:
        return False

    user = User.get_by_id(user_id)
    return user and user.is_sudo


@require_sudo
async def switch_role_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = context.user_data['db_user']
    keyboard = [
        [InlineKeyboardButton("👑 Sudo (کامل)", callback_data="role_sudo")],
        [InlineKeyboardButton("🎨 Editor (طراح)", callback_data="role_editor")],
        [InlineKeyboardButton("✅ Reviewer (ناظر)", callback_data="role_reviewer")]
    ]
    await update.message.reply_text(
        f"👑 تنظیمات نقش (Sudo)\n\n"
        f"نقش فعلی: {user.active_role.upper()}\n"
        f"شما همیشه Sudo هستید، این فقط رابط کاربری را تغییر می‌دهد.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


@require_sudo
async def handle_role_switch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query

    ALLOWED_ROLES = {'sudo', 'editor', 'reviewer'}
    new_role = query.data.split('_')[1]

    if new_role not in ALLOWED_ROLES:
        await safe_answer_callback(query, "❌ نقش نامعتبر", show_alert=True)
        return

    await safe_answer_callback(query)

    user = User.get_by_id(query.from_user.id)
    user.update_active_role(new_role)

    from ui.keyboards import Keyboards

    # FIX: Try to delete message, but don't fail if it's already gone
    try:
        await query.message.delete()
    except Exception as e:
        logging.warning(f"Could not delete role switch message: {e}")

    # Send new message with updated keyboard
    try:
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=f"✅ نقش شما به {new_role.upper()} تغییر یافت.",
            reply_markup=Keyboards.get_main_menu(user)
        )
    except Exception as e:
        logging.error(f"Failed to send role switch confirmation: {e}")
        # Fallback: edit the original message if delete failed
        try:
            await query.edit_message_text(
                f"✅ نقش شما به {new_role.upper()} تغییر یافت.\n\n"
                f"برای دیدن منوی جدید /start بزنید."
            )
        except Exception as e2:
            logging.error(f"Role switch message update fallback also failed: {e2}")

@require_sudo
async def manual_backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show backup type selection: Data Export or Full Backup"""
    keyboard = [
        [InlineKeyboardButton("📊 خروجی اطلاعات (CSV)", callback_data="backup_csv")],
        [InlineKeyboardButton("💾 بکاپ کامل (ZIP)", callback_data="backup_zip")],
    ]
    await update.message.reply_text(
        "💾 تهیه بکاپ\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "نوع خروجی مورد نظر را انتخاب کنید:\n\n"
        "📊 خروجی اطلاعات: فایل CSV با اطلاعات طرح‌ها\n"
        "💾 بکاپ کامل: فایل ZIP شامل دیتابیس و فایل‌ها",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


@require_sudo
async def backup_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle backup type selection callbacks"""
    query = update.callback_query
    await safe_answer_callback(query)

    if query.data == "backup_csv":
        keyboard = [
            [InlineKeyboardButton("📅 هفته اخیر", callback_data="csv_week")],
            [InlineKeyboardButton("📅 ماه اخیر", callback_data="csv_month")],
            [InlineKeyboardButton("📅 کل اطلاعات", callback_data="csv_all")],
            [InlineKeyboardButton("❌ انصراف", callback_data="csv_cancel")],
        ]
        await query.edit_message_text(
            "📊 خروجی اطلاعات (CSV)\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "بازه زمانی مورد نظر را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "backup_zip":
        await query.edit_message_text("⏳ در حال تهیه بکاپ کامل... لطفاً صبر کنید.")

        zip_path = await BackupService.create_daily_backup_zip()

        if not zip_path:
            await query.edit_message_text("❌ خطایی در گرفتن بکاپ رخ داد.")
            return

        try:
            file_size = os.path.getsize(zip_path) / 1024
            with open(zip_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=SUDO_USER_ID,
                    document=f,
                    filename=os.path.basename(zip_path),
                    caption=f"💾 بکاپ کامل\nحجم: {file_size:.1f} KB"
                )
            await query.delete_message()
        finally:
            shutil.rmtree(os.path.dirname(zip_path), ignore_errors=True)


@require_sudo
async def csv_range_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle CSV time range selection"""
    query = update.callback_query
    await safe_answer_callback(query)

    if query.data == "csv_cancel":
        await query.edit_message_text("❌ عملیات لغو شد.")
        return

    time_range = query.data.replace("csv_", "")

    range_labels = {
        'week': 'هفته اخیر',
        'month': 'ماه اخیر',
        'all': 'کل اطلاعات'
    }

    await query.edit_message_text(
        f"⏳ در حال تهیه خروجی {range_labels[time_range]}..."
    )

    loop = asyncio.get_event_loop()
    csv_path = await loop.run_in_executor(
        None, BackupService.create_csv_export, time_range
    )

    if not csv_path:
        await query.edit_message_text("❌ خطا در تهیه خروجی. ممکن است داده‌ای موجود نباشد.")
        return

    try:
        file_size = os.path.getsize(csv_path) / 1024

        with open(csv_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=SUDO_USER_ID,
                document=f,
                filename=os.path.basename(csv_path),
                caption=(
                    f"📊 خروجی اطلاعات — {range_labels[time_range]}\n"
                    f"حجم: {file_size:.1f} KB"
                )
            )
        await query.delete_message()
    except Exception as e:
        logging.error(f"Failed to send CSV: {e}")
        await query.edit_message_text(f"❌ خطا در ارسال فایل: {e}")
    finally:
        shutil.rmtree(os.path.dirname(csv_path), ignore_errors=True)


@require_sudo
async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask user to send the backup ZIP file for restore"""
    context.user_data['awaiting_restore_file'] = True
    await update.message.reply_text(
        "🔧 ریستور از بکاپ\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "فایل ZIP بکاپ را ارسال کنید.\n\n"
        "⚠️ این عملیات:\n"
        "• دیتابیس فعلی را با بکاپ جایگزین می‌کند\n"
        "• فایل‌های پوشه public را بازنویسی می‌کند\n"
        "• ربات ریستارت می‌شود\n\n"
        "برای لغو /cancel بزنید."
    )


async def confirm_restore_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle restore confirmation buttons"""
    query = update.callback_query
    await safe_answer_callback(query)

    # Only sudo can confirm
    if query.from_user.id != SUDO_USER_ID:
        await safe_answer_callback(query, "🚫 فقط Sudo", show_alert=True)
        return

    if query.data == "cancel_restore":
        context.user_data.pop('restore_pending', None)
        await query.edit_message_text("❌ ریستور لغو شد.")
        return

    if query.data == "confirm_restore":
        pending = context.user_data.pop('restore_pending', None)
        if not pending:
            await query.edit_message_text("❌ اطلاعات ریستور یافت نشد. دوباره تلاش کنید.")
            return

        await query.edit_message_text("⏳ در حال ریستور دیتابیس...")

        from services.restore_service import RestoreService

        # Step 1: Restore database
        try:
            db_result = RestoreService.restore_database(pending['sql_path'])
            if not db_result['success']:
                await query.edit_message_text(
                    f"❌ خطا در ریستور دیتابیس:\n{db_result['error'][:300]}"
                )
                shutil.rmtree(pending['temp_dir'], ignore_errors=True)
                return
        except Exception as e:
            await query.edit_message_text(f"❌ خطا در ریستور دیتابیس:\n{str(e)[:300]}")
            shutil.rmtree(pending['temp_dir'], ignore_errors=True)
            return

        # Step 2: Restore public files
        await query.edit_message_text("⏳ در حال بازنویسی فایل‌های public...")
        try:
            files_result = RestoreService.restore_public_files(pending['zip_path'])
        except Exception as e:
            files_result = {'success': False, 'error': str(e)}

        # Step 3: Cleanup temp files
        shutil.rmtree(pending['temp_dir'], ignore_errors=True)

        # Step 4: Report result
        msg_lines = [
            "✅ ریستور با موفقیت انجام شد",
            "━━━━━━━━━━━━━━━━━━",
            f"📦 دیتابیس: بازیابی شد",
        ]
        if files_result['success']:
            msg_lines.append(
                f"📁 فایل‌ها: {files_result.get('files_restored', 0)} فایل بازنویسی شد"
            )
        else:
            msg_lines.append(f"📁 فایل‌ها: خطا — {files_result.get('error', 'نامشخص')[:100]}")

        await query.edit_message_text('\n'.join(msg_lines))

        # Step 5: Restart bot
        await context.bot.send_message(
            chat_id=SUDO_USER_ID,
            text="🔄 ربات در حال ریستارت برای اعمال تغییرات..."
        )
        RestoreService.restart_bot()


@require_sudo
async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [[
        InlineKeyboardButton("✅ ریستارت کن", callback_data="confirm_restart"),
        InlineKeyboardButton("❌ انصراف", callback_data="cancel_restart")
    ]]
    await update.message.reply_text(
        "⚠️ مطمئنی می‌خوای ربات رو ریستارت کنی؟",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

@require_sudo
async def execute_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await safe_answer_callback(query)

    if query.data == "cancel_restart":
        await query.edit_message_text("لغو شد.")
        return

    await query.edit_message_text("🔄 ربات در حال ریستارت...\nلطفاً 10 ثانیه صبر کنید.")

    try:
        proc = await asyncio.create_subprocess_exec(
            'supervisorctl', '-c', SUPERVISORD_CONF, 'restart', SUPERVISOR_PROCESS,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode != 0:
            error_detail = (stderr or stdout or b"unknown error").decode(errors="replace").strip()
            logging.error(f"Restart failed (rc={proc.returncode}): {error_detail}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"❌ ریستارت ناموفق بود:\n<pre>{html.escape(error_detail[:500])}</pre>",
                parse_mode="HTML"
            )
    except FileNotFoundError:
        logging.error("supervisorctl not found — check SUPERVISORD_CONF path")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ دستور supervisorctl یافت نشد. مسیر SUPERVISORD_CONF را بررسی کنید."
        )
    except asyncio.TimeoutError:
        logging.error("Restart command timed out after 30s")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ ریستارت بیش از 30 ثانیه طول کشید. ممکن است ربات هنگ کرده باشد."
        )
    except Exception as e:
        logging.error(f"Restart failed: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ خطای غیرمنتظره: {html.escape(str(e)[:300])}"
        )


@require_sudo
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            'supervisorctl', '-c', SUPERVISORD_CONF, 'status', SUPERVISOR_PROCESS,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        await update.message.reply_text(
            f"📊 وضعیت ربات:\n\n{stdout.decode().strip()}\n\n"
            f"🕐 زمان: {get_tehran_time().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در دریافت وضعیت: {e}")



@require_sudo
async def broadcast_update_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await safe_answer_callback(query)

    users = User.get_all_active()
    sent_count = 0
    failure_count = 0

    for u in users:
        if u.user_id == SUDO_USER_ID:
            continue
        try:
            await send_with_retry(
                lambda user_id=u.user_id: context.bot.send_message(
                    chat_id=user_id,
                    text="⏏️ ربات آپدیت شد!\n\n"
                         "💢 برای دریافت تغییرات جدید منوی اصلی را چک کنید یا دستور /start بزنید."
                ),
                f"Broadcast update to user {u.user_id}"
            )
            sent_count += 1
        except Exception as e:
            failure_count += 1
            logging.error(f"Broadcast update failed for user {u.user_id}: {e}")

    await query.edit_message_text(
        f"✅ پیام آپدیت به {sent_count} کاربر فعال ارسال شد.\n"
        f"❌ ارسال به {failure_count} کاربر ناموفق بود."
    )


@require_sudo
async def group_management_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ProductLine.get_all()
    if not lines:
        await update.message.reply_text("هیچ خط تولیدی وجود ندارد.")
        return

    text_lines = ["⚙️ تنظیم گروه‌ها\n━━━━━━━━━━━━━━━━━━"]
    keyboard = []

    for pl in lines:
        gp = pl.group_products or "❌ تنظیم نشده"
        gpr = pl.group_print or "❌ تنظیم نشده"
        status = "✅" if pl.is_fully_configured() else "⚠️"

        text_lines.append(
            f"\n{status} {pl.icon} {pl.name_fa} ({pl.code_prefix})\n"
            f"  📦 محصولات: {gp}\n"
            f"  🖨 چاپ: {gpr}"
        )
        keyboard.append([
            InlineKeyboardButton(
                f"✏️ {pl.icon} {pl.name_fa}",
                callback_data=f"setgroup_select_{pl.id}"
            )
        ])

    await update.message.reply_text(
        '\n'.join(text_lines),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


@require_sudo
async def group_management_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await safe_answer_callback(query)
    data = query.data

    if data.startswith("setgroup_select_"):
        pl_id = int(data.split("_")[2])
        pl = ProductLine.get_by_id(pl_id)
        if not pl:
            await query.edit_message_text("❌ خط تولید یافت نشد.")
            return

        keyboard = [
            [InlineKeyboardButton(
                "📦 گروه محصولات", callback_data=f"setgroup_type_{pl_id}_products"
            )],
            [InlineKeyboardButton(
                "🖨 گروه چاپ", callback_data=f"setgroup_type_{pl_id}_print"
            )],
            [InlineKeyboardButton("↩️ بازگشت", callback_data="setgroup_back")]
        ]

        gp = pl.group_products or "تنظیم نشده"
        gpr = pl.group_print or "تنظیم نشده"

        await query.edit_message_text(
            f"{pl.icon} {pl.name_fa}\n\n"
            f"📦 گروه محصولات: {gp}\n"
            f"🖨 گروه چاپ: {gpr}\n\n"
            f"کدام گروه را می‌خواهید تنظیم کنید؟",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("setgroup_type_"):
        parts = data.split("_")
        pl_id = int(parts[2])
        group_type = parts[3]

        pl = ProductLine.get_by_id(pl_id)
        if not pl:
            await query.edit_message_text("❌ خط تولید یافت نشد.")
            return

        context.user_data['awaiting_group_input'] = {
            'pl_id': pl_id,
            'group_type': group_type,
            'pl_name': pl.name_fa,
            'menu_message_id': query.message.message_id
        }

        type_label = "محصولات" if group_type == "products" else "چاپ"
        await query.edit_message_text(
            f"📝 لطفاً Chat ID گروه {type_label} را برای {pl.icon} {pl.name_fa} ارسال کنید.\n\n"
            f"مثال: -1001234567890\n\n"
            f"برای لغو /cancel بزنید."
        )

    elif data == "setgroup_back":
        lines = ProductLine.get_all()
        text_lines = ["⚙️ تنظیم گروه‌ها\n━━━━━━━━━━━━━━━━━━"]
        keyboard = []

        for pl in lines:
            gp = pl.group_products or "❌ تنظیم نشده"
            gpr = pl.group_print or "❌ تنظیم نشده"
            status = "✅" if pl.is_fully_configured() else "⚠️"
            text_lines.append(
                f"\n{status} {pl.icon} {pl.name_fa} ({pl.code_prefix})\n"
                f"  📦 محصولات: {gp}\n"
                f"  🖨 چاپ: {gpr}"
            )
            keyboard.append([
                InlineKeyboardButton(
                    f"✏️ {pl.icon} {pl.name_fa}",
                    callback_data=f"setgroup_select_{pl.id}"
                )
            ])

        await query.edit_message_text(
            '\n'.join(text_lines),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_group_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    pending = context.user_data.get('awaiting_group_input')
    if not pending:
        return False

    if not _verify_sudo_for_group_input(update.effective_user.id):
        await update.message.reply_text("🚫 دسترسی غیرمجاز.")
        context.user_data.pop('awaiting_group_input', None)
        return True

    from utils.validators import Validators, ValidationError
    try:
        chat_id = Validators.validate_chat_id(update.message.text.strip())
    except ValidationError as e:
        await update.message.reply_text(
            f"{e}\nبرای لغو /cancel بزنید."
        )
        return True

    pl = ProductLine.get_by_id(pending['pl_id'])
    if not pl:
        await update.message.reply_text("❌ خط تولید یافت نشد.")
        context.user_data.pop('awaiting_group_input', None)
        return True

    group_type = pending['group_type']
    pl.set_group(group_type, chat_id)

    type_label = "محصولات" if group_type == "products" else "چاپ"
    context.user_data.pop('awaiting_group_input', None)

    await update.message.reply_text(
        f"✅ گروه {type_label} برای {pl.icon} {pl.name_fa} تنظیم شد.\n"
        f"Chat ID: {chat_id}"
    )
    return True


@require_sudo
async def delete_design_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /deletedesign <code>
    Permanently delete a design and all its data.
    """
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ فرمت نادرست\n"
            "استفاده: /deletedesign <code>\n\n"
            "مثال: /deletedesign TS001\n\n"
            "⚠️ این عملیات غیرقابل بازگشت است!"
        )
        return

    code = args[0].strip().upper()

    # Confirm
    keyboard = [
        [
            InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"confirm_kill_{code}"),
            InlineKeyboardButton("❌ انصراف", callback_data="cancel_kill")
        ]
    ]

    from models.design import Design
    design = Design.get_by_code(code)

    if not design:
        await update.message.reply_text(f"❌ طرح {code} یافت نشد.")
        return

    status_fa = {
        DesignStatus.PENDING: "در انتظار",
        DesignStatus.APPROVED: "تایید شده",
        DesignStatus.REJECTED: "رد شده",
        DesignStatus.DELETED: "حذف شده"
    }.get(design.status, str(design.status))

    await update.message.reply_text(
        f"⚠️ حذف کامل طرح\n\n"
        f"کد: {code}\n"
        f"وضعیت: {status_fa}\n"
        f"طراح: {design.editor_name}\n\n"
        f"این عملیات:\n"
        f"• فایل‌ها را از گروه‌ها حذف می‌کند\n"
        f"• پیام‌ها را از PV ناظران حذف می‌کند\n"
        f"• کد را از دیتابیس حذف می‌کند\n\n"
        f"⚠️ غیرقابل بازگشت است!\n\n"
        f"ادامه می‌دهید؟",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def _kill_key(update, context) -> str:
    """Lock key for delete confirmation — prevents double-tap"""
    data: str = update.callback_query.data
    if data == "cancel_kill":
        return "cancel_kill"
    code: str = data.replace("confirm_kill_", "")
    return f"kill_{code}"

@require_sudo
@deduplicate_callback(_kill_key)
async def confirm_delete_design_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle design deletion confirmation"""
    query = update.callback_query
    await safe_answer_callback(query)

    data = query.data

    if data == "cancel_kill":
        await query.edit_message_text("❌ عملیات لغو شد.")
        return

    if data.startswith("confirm_kill_"):
        code = data.replace("confirm_kill_", "")

        await query.edit_message_text(f"🔄 در حال حذف {code}...")

        from models.design import Design
        result = await Design.delete_completely(code, context.bot)

        if result['database_deleted']:
            msg = (
                f"✅ طرح {code} به طور کامل حذف شد\n\n"
                f"📊 گزارش:\n"
                f"• وضعیت: {result['status']}\n"
                f"• پیام‌های گروه حذف شده: {result['group_messages_deleted']}\n"
                f"• پیام‌های ناظر حذف شده: {result['reviewer_messages_deleted']}\n"
            )
            if result['errors']:
                msg += f"\n⚠️ خطاها:\n" + "\n".join(f"• {e}" for e in result['errors'][:5])
        else:
            msg = (
                f"❌ خطا در حذف {code}\n\n"
                f"خطاها:\n" + "\n".join(f"• {e}" for e in result['errors'][:5])
            )

        await query.edit_message_text(msg)

@require_sudo
async def cleanup_orphans_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /cleanup
    Delete old pending designs with no files (orphaned codes)
    """
    await update.message.reply_text("🔄 در حال اسکن طرح‌های ناقص...")

    from services.cleanup_service import CleanupService
    result = await CleanupService.cleanup_orphaned_pending_designs(
        context.bot,
        max_age_hours=24
    )

    msg = (
        f"✅ پاکسازی کامل شد\n\n"
        f"📊 گزارش:\n"
        f"• اسکن شده: {result['scanned']}\n"
        f"• حذف شده: {result['deleted']}\n"
    )

    if result['codes']:
        msg += f"\n🗑 کدهای حذف شده:\n"
        msg += ", ".join(result['codes'][:20])
        if len(result['codes']) > 20:
            msg += f"\n... و {len(result['codes']) - 20} مورد دیگر"

    if result['errors']:
        msg += f"\n\n⚠️ خطاها:\n" + "\n".join(f"• {e}" for e in result['errors'][:5])

    await update.message.reply_text(msg)



@require_sudo
async def userbot_queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """\u200f/userbotqueue — status of the userbot deletion queue."""
    from models.userbot_deletion_queue import UserbotDeletionQueue

    counts = UserbotDeletionQueue.counts()
    failed = UserbotDeletionQueue.recent_failed(limit=5)

    lines = [
        "🤖 وضعیت صف حذف یوزربات",
        "━━━━━━━━━━━━━━━━━━",
        f"⏳ در انتظار: {counts['pending']}",
        f"⚙️ در حال پردازش: {counts['processing']}",
        f"✅ انجام شده: {counts['done']}",
        f"❌ ناموفق: {counts['failed']}",
    ]

    if failed:
        lines.append("\n❌ آخرین خطاها:")
        for row in failed:
            code = row.get('code') or '-'
            lines.append(
                f"• {code} | chat={row['chat_id']} msg={row['message_id']} "
                f"(attempt {row['attempts']}): {row.get('last_error') or '?'}"
            )

    if counts['pending'] or counts['processing']:
        lines.append("\n🔄 یوزربات به‌صورت خودکار این پیام‌ها را حذف می‌کند.")

    await update.message.reply_text("\n".join(lines))
