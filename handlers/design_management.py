import logging
import asyncio
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ContextTypes
from config.settings import SUDO_USER_ID, NAZI_CHAT_ID
from models.design import Design
from models.product_line import ProductLine
from models.design_group_message import DesignGroupMessage
from utils.helpers import safe_edit_message, delete_messages, format_datetime_persian


def is_privileged(user_id):
    """Sudo and Nazi only"""
    return user_id in (SUDO_USER_ID, NAZI_CHAT_ID)


# ---------------------------------------------------------------------------
# Design info — "اطلاعات طرح"
# ---------------------------------------------------------------------------

async def design_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point: ask for a code"""
    user_id = update.effective_user.id
    if not is_privileged(user_id):
        await update.message.reply_text("🚫 دسترسی غیرمجاز.")
        return

    context.user_data['awaiting_design_code'] = 'info'
    await update.message.reply_text(
        "🔍 کد طرح مورد نظر را وارد کنید:\n"
        "مثال: TS001\n\n"
        "برای لغو /cancel بزنید."
    )


async def delete_design_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point: ask for a code to delete"""
    user_id = update.effective_user.id
    if not is_privileged(user_id):
        await update.message.reply_text("🚫 دسترسی غیرمجاز.")
        return

    context.user_data['awaiting_design_code'] = 'delete'
    await update.message.reply_text(
        "🗑 کد طرح تایید شده‌ای که می‌خواهید حذف کنید را وارد کنید:\n"
        "مثال: TS001\n\n"
        "⚠️ این عملیات فایل‌ها را از گروه‌ها حذف کرده و کد را آزاد می‌کند.\n\n"
        "برای لغو /cancel بزنید."
    )


async def handle_design_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles text input when awaiting a design code.
    Returns True if it consumed the message, False otherwise.
    """
    mode = context.user_data.get('awaiting_design_code')
    if mode not in ('info', 'delete'):
        return False

    code = update.message.text.strip().upper()
    context.user_data.pop('awaiting_design_code', None)

    if mode == 'info':
        await _send_design_info(update, context, code)
    elif mode == 'delete':
        await _send_delete_confirmation(update, context, code)

    return True


async def _send_design_info(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    """Fetch and display full design info + files"""
    design = Design.get_by_code(code)
    if not design:
        await update.message.reply_text(f"❌ طرح با کد '{code}' یافت نشد.")
        return

    product_line = ProductLine.get_by_id(design.product_line_id)
    pl_name = f"{product_line.icon} {product_line.name_fa}" if product_line else "نامشخص"

    status_map = {
        'pending': '⏳ در انتظار بررسی',
        'approved': '✅ تایید شده',
        'rejected': '❌ رد شده'
    }
    status_text = status_map.get(design.status, design.status)

    # Build info text
    lines = [
        f"📋 اطلاعات طرح: {code}",
        f"━━━━━━━━━━━━━━━━━━",
        f"📦 خط تولید:  {pl_name}",
        f"👤 طراح:       {design.editor_name or 'نامشخص'}",
        f"📅 تاریخ ثبت: {format_datetime_persian(design.created_at)}",
        f"🔖 وضعیت:    {status_text}",
    ]

    if design.status in ('approved', 'rejected') and design.reviewer_name:
        lines.append(f"✅ ناظر:      {design.reviewer_name}")
        lines.append(f"📅 تاریخ بررسی: {format_datetime_persian(design.reviewed_at)}")

    lines.append(f"\n🖼 موکاپ: {len(design.mockup_file_ids)} فایل")
    lines.append(f"🖨 فایل چاپی: {len(design.print_file_ids)} فایل")

    info_text = '\n'.join(lines)

    # Send info message
    await update.message.reply_text(info_text)

    # Send mockup files
    if design.mockup_file_ids:
        await update.message.reply_text("🖼 موکاپ‌ها:")
        for i, fid in enumerate(design.mockup_file_ids):
            try:
                cap = f"موکاپ {i+1}/{len(design.mockup_file_ids)} — {code}"
                if fid.startswith(('AgAC', 'AQA')):
                    await context.bot.send_photo(
                        update.effective_chat.id, photo=fid, caption=cap
                    )
                else:
                    await context.bot.send_document(
                        update.effective_chat.id, document=fid, caption=cap
                    )
                await asyncio.sleep(0.3)
            except Exception as e:
                logging.error(f"Failed to send mockup {i} for {code}: {e}")
                await update.message.reply_text(f"⚠️ موکاپ {i+1} قابل ارسال نیست.")
    else:
        await update.message.reply_text("⚠️ موکاپی برای این طرح ذخیره نشده.")

    # Send print files
    if design.print_file_ids:
        unique_prints = list(dict.fromkeys(design.print_file_ids))
        await update.message.reply_text("🖨 فایل‌های چاپی:")
        for i, fid in enumerate(unique_prints):
            try:
                cap = f"فایل چاپی {i+1}/{len(unique_prints)} — {code}"
                await context.bot.send_document(
                    update.effective_chat.id, document=fid, caption=cap
                )
                await asyncio.sleep(0.3)
            except Exception as e:
                logging.error(f"Failed to send print {i} for {code}: {e}")
                await update.message.reply_text(f"⚠️ فایل چاپی {i+1} قابل ارسال نیست.")
    else:
        await update.message.reply_text("⚠️ فایل چاپی برای این طرح ذخیره نشده.")


async def _send_delete_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str):
    """Show mockup + confirm/cancel buttons before deleting"""
    design = Design.get_by_code(code)
    if not design:
        await update.message.reply_text(f"❌ طرح با کد '{code}' یافت نشد.")
        return

    if design.status != 'approved':
        status_map = {'pending': 'در انتظار', 'rejected': 'رد شده'}
        st = status_map.get(design.status, design.status)
        await update.message.reply_text(
            f"❌ فقط طرح‌های تایید شده قابل حذف هستند.\n"
            f"وضعیت {code}: {st}"
        )
        return

    product_line = ProductLine.get_by_id(design.product_line_id)
    pl_name = f"{product_line.icon} {product_line.name_fa}" if product_line else "نامشخص"

    # Check if group messages are tracked
    group_msgs = DesignGroupMessage.get_by_code(code)
    group_msg_count = len(group_msgs)

    confirm_text = (
        f"⚠️ تایید حذف طرح\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔖 کد: {code}\n"
        f"📦 خط تولید: {pl_name}\n"
        f"👤 طراح: {design.editor_name or 'نامشخص'}\n"
        f"✅ ناظر: {design.reviewer_name or 'نامشخص'}\n"
        f"📅 تایید: {format_datetime_persian(design.reviewed_at)}\n\n"
        f"📨 پیام‌های قابل حذف از گروه‌ها: {group_msg_count}\n\n"
        f"🚨 آیا مطمئن هستید؟ این عملیات برگشت‌پذیر نیست."
    )

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"confirm_delete_{code}"),
        InlineKeyboardButton("❌ انصراف", callback_data="cancel_delete")
    ]])

    # Send first mockup as preview if available
    if design.mockup_file_ids:
        fid = design.mockup_file_ids[0]
        try:
            if fid.startswith(('AgAC', 'AQA')):
                await context.bot.send_photo(
                    update.effective_chat.id,
                    photo=fid,
                    caption=confirm_text,
                    reply_markup=markup
                )
            else:
                await context.bot.send_document(
                    update.effective_chat.id,
                    document=fid,
                    caption=confirm_text,
                    reply_markup=markup
                )
            return
        except Exception as e:
            logging.error(f"Failed to send mockup preview for delete: {e}")

    # Fallback: no mockup, just text
    await update.message.reply_text(confirm_text, reply_markup=markup)


async def confirm_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle confirm/cancel delete buttons"""
    query = update.callback_query
    await query.answer()

    if not is_privileged(query.from_user.id):
        await query.answer("🚫 دسترسی غیرمجاز", show_alert=True)
        return

    if query.data == "cancel_delete":
        await safe_edit_message(query, "👌 عملیات حذف لغو شد.")
        return

    # confirm_delete_{code}
    code = query.data.split('_', 2)[2]
    design = Design.get_by_code(code)

    if not design:
        await safe_edit_message(query, "❌ طرح یافت نشد.")
        return

    if design.status != 'approved':
        await safe_edit_message(query, "❌ فقط طرح‌های تایید شده قابل حذف هستند.")
        return

    await safe_edit_message(query, f"⏳ در حال حذف طرح {code} از گروه‌ها...")

    # Delete messages from groups
    group_msgs = DesignGroupMessage.get_by_code(code)
    deleted_from_groups = 0
    failed_deletions = 0

    for record in group_msgs:
        try:
            await context.bot.delete_message(
                chat_id=record['chat_id'],
                message_id=record['message_id']
            )
            deleted_from_groups += 1
            await asyncio.sleep(0.1)
        except Exception as e:
            logging.warning(f"Could not delete msg {record['message_id']} from group: {e}")
            failed_deletions += 1

    # Remove group message records
    if group_msgs:
        DesignGroupMessage.delete_by_code(code)

    # Remove from locked_codes to free the code
    _free_locked_code(code)

    # Rename the design record to free the original code
    # (same _REJ_ pattern used for rejected — append _DEL_ + timestamp)
    import time as time_module
    _rename_design_as_deleted(design, code)

    result_lines = [
        f"✅ طرح {code} با موفقیت حذف شد.",
        f"━━━━━━━━━━━━━━━━━━",
        f"🗑 پیام‌های حذف شده از گروه: {deleted_from_groups}",
    ]
    if failed_deletions:
        result_lines.append(f"⚠️ پیام‌هایی که قابل حذف نبودند: {failed_deletions}")
    result_lines.append(f"🔓 کد {code} آزاد شد و قابل استفاده مجدد است.")

    await context.bot.send_message(
        chat_id=query.from_user.id,
        text='\n'.join(result_lines)
    )


def _free_locked_code(code: str):
    """Remove code from designs_locked_codes"""
    from config.database import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM designs_locked_codes WHERE code = %s", (code,)
        )
        conn.commit()
        logging.info(f"🔓 Code {code} removed from locked_codes")
    except Exception as e:
        conn.rollback()
        logging.error(f"Failed to free locked code {code}: {e}")
    finally:
        cursor.close()
        conn.close()


def _rename_design_as_deleted(design, original_code: str):
    """Rename the design code to _DEL_{timestamp} to free the original code slot"""
    import time as time_module
    from config.database import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        del_code = f"{original_code}_DEL_{int(time_module.time())}"
        cursor.execute(
            "UPDATE designs SET code = %s, status = 'deleted' WHERE id = %s",
            (del_code, design.id)
        )
        conn.commit()
        logging.info(f"🗑 Design {original_code} renamed to {del_code}")
    except Exception as e:
        conn.rollback()
        logging.error(f"Failed to rename design {original_code}: {e}")
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------------------------
# Pending designs — with inline code buttons
# ---------------------------------------------------------------------------

async def pending_designs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Show pending designs as inline code buttons.
    Accessible to privileged users AND all reviewers.
    """
    from models.user import User
    user_id = update.effective_user.id
    user = User.get_by_id(user_id)

    if not user or not user.is_active:
        await update.message.reply_text("🚫 دسترسی غیرمجاز.")
        return

    # Sudo, Nazi, or any reviewer
    is_reviewer = user.role == 'reviewer' or user.is_sudo or user_id == NAZI_CHAT_ID
    if not is_reviewer:
        await update.message.reply_text("🚫 دسترسی غیرمجاز.")
        return

    pending = Design.get_all_pending()
    if not pending:
        await update.message.reply_text("✨ هیچ طرحی در انتظار بررسی نیست.")
        return

    # Group by product line
    by_line = {}
    for d in pending:
        key = f"{d.product_icon} {d.product_name}"
        by_line.setdefault(key, []).append(d)

    lines = [f"📋 طرح‌های در انتظار ({len(pending)} طرح)\n━━━━━━━━━━━━━━━━━━"]
    keyboard = []

    for pl_label, designs in by_line.items():
        lines.append(f"\n{pl_label}:")
        row = []
        for d in designs:
            row.append(
                InlineKeyboardButton(
                    d.code,
                    callback_data=f"pending_view_{d.code}"
                )
            )
            if len(row) == 3:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    await update.message.reply_text(
        '\n'.join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def pending_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Send mockup + print files when a code button is tapped in pending list.
    """
    from models.user import User
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    user = User.get_by_id(user_id)

    if not user or not user.is_active:
        await query.answer("🚫 دسترسی غیرمجاز", show_alert=True)
        return

    is_reviewer = user.role == 'reviewer' or user.is_sudo or user_id == NAZI_CHAT_ID
    if not is_reviewer:
        await query.answer("🚫 دسترسی غیرمجاز", show_alert=True)
        return

    code = query.data.split('_', 2)[2]
    design = Design.get_by_code(code)

    if not design:
        await query.answer("❌ این طرح یافت نشد یا حذف شده.", show_alert=True)
        return

    if design.status != 'pending':
        await query.answer("⚠️ این طرح دیگر در انتظار نیست.", show_alert=True)
        return

    product_line = ProductLine.get_by_id(design.product_line_id)
    pl_name = f"{product_line.icon} {product_line.name_fa}" if product_line else "نامشخص"

    # Send info header
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"📋 {pl_name} | کد: {code}\n"
            f"👤 طراح: {design.editor_name or 'نامشخص'}\n"
            f"📅 ثبت: {format_datetime_persian(design.created_at)}\n"
            f"🖼 موکاپ: {len(design.mockup_file_ids)} | 🖨 چاپی: {len(design.print_file_ids)}"
        )
    )

    # Send mockups
    for i, fid in enumerate(design.mockup_file_ids):
        try:
            cap = f"🖼 موکاپ {i+1} — {code}"
            if fid.startswith(('AgAC', 'AQA')):
                await context.bot.send_photo(user_id, photo=fid, caption=cap)
            else:
                await context.bot.send_document(user_id, document=fid, caption=cap)
            await asyncio.sleep(0.2)
        except Exception as e:
            logging.error(f"Failed to send mockup {i} to {user_id}: {e}")

    # Send print files
    unique_prints = list(dict.fromkeys(design.print_file_ids))
    for i, fid in enumerate(unique_prints):
        try:
            cap = f"🖨 فایل چاپی {i+1} — {code}"
            await context.bot.send_document(user_id, document=fid, caption=cap)
            await asyncio.sleep(0.2)
        except Exception as e:
            logging.error(f"Failed to send print {i} to {user_id}: {e}")